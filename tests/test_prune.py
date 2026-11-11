"""Dead switch branches: for every app x every value of every dropdown/toggle it exposes, the pruned prompt must be
exactly what ComfyUI would execute.

  ~/code/comfy-mac/.venv/bin/python tests/test_prune.py      (needs a worker on 127.0.0.1:8188 for /object_info, and simpleeval)

Reference, independent of appwf: every ComfyMathExpression is evaluated with simpleeval (what ComfyUI's node uses), every
switch takes on_true/on_false from that, and the reference graph is what the outputs reach through the TAKEN branches.
Pruned prompt == reference: same node ids, same inputs once switch links are resolved, no ComfySwitchNode left.
"""
import glob, importlib, json, math, os, sys, types, urllib.request

from simpleeval import simple_eval

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.modules["studio"] = types.ModuleType("studio")
sys.modules["studio"].__path__ = [os.path.join(ROOT, "ComfyUI-MannyStudio")]
appwf = importlib.import_module("studio.appwf")
from studio.mconv.workflow_to_api import convert_ui_to_api  # noqa: E402

OI = json.load(urllib.request.urlopen(os.environ.get("OBJECT_INFO", "http://127.0.0.1:8188/object_info")))
FUNCS = {"sum": lambda *a: sum(a[0]) if len(a) == 1 else sum(a), "min": min, "max": max, "abs": abs, "round": round,
         "pow": pow, "sqrt": math.sqrt, "ceil": math.ceil, "floor": math.floor, "log": math.log, "log2": math.log2,
         "log10": math.log10, "sin": math.sin, "cos": math.cos, "tan": math.tan, "int": int, "float": float}
fails = []


def check(c, m):
    if not c:
        print("FAIL " + m)
        fails.append(m)
    return c


def link(v):
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def ref_value(api, v):
    if not link(v):
        return v
    n = api[v[0]]
    ct, ins = n["class_type"], n["inputs"]
    if ct.startswith("Primitive"):
        return ref_value(api, ins["value"])
    if ct == "CustomCombo":
        return ins["choice"] if v[1] == 0 else ins["index"]
    if ct == "ComfySwitchNode":
        return ref_value(api, ins.get("on_true" if ref_value(api, ins["switch"]) else "on_false"))
    if ct == "ComfyMathExpression":
        vals = {k.split(".", 1)[1]: ref_value(api, x) for k, x in ins.items() if k.startswith("values.")}
        r = simple_eval(ins["expression"], names=dict(vals, values=list(vals.values())), functions=FUNCS)
        return (float(r), int(r), bool(r))[v[1]]
    raise ValueError(ct)


NONE = "<switch gives None>"


def resolve(api, v):
    """Follow switch outputs to the taken branch's source (NONE when that branch isn't connected)."""
    while link(v) and api[v[0]]["class_type"] == "ComfySwitchNode":
        ins = api[v[0]]["inputs"]
        v = ins.get("on_true" if ref_value(api, ins["switch"]) else "on_false", NONE)
    return v


def reference(api):
    outs = [k for k, n in api.items() if OI.get(n["class_type"], {}).get("output_node")]
    keep, todo = {}, list(outs)
    while todo:
        k = todo.pop()
        if k in keep:
            continue
        ins = {name: resolve(api, v) for name, v in api[k]["inputs"].items()}
        ins = {name: v for name, v in ins.items() if v != NONE}    # unconnected optional
        keep[k] = ins
        todo += [v[0] for v in ins.values() if link(v)]
    return keep


def drivers(wf):
    """Every (values_key, value) a user can pick on the app's dropdowns and toggles."""
    nodes = {str(n["id"]): n for n in wf.get("nodes", [])}
    for entry in ((wf.get("extra") or {}).get("linearData") or {}).get("inputs", []):
        nid, wname = entry[0].split(":")[-2:]
        n = nodes.get(nid)
        if not n:
            continue
        if n["type"] == "CustomCombo":
            for o in appwf.combo_options(n):
                yield f"{nid}:{wname}", o
        elif n["type"] == "PrimitiveBoolean":
            for b in (False, True):
                yield f"{nid}:{wname}", b


def main():
    apps = sorted(glob.glob(os.path.join(ROOT, "workflows", "*.app.json")))
    check(len(apps) == 9, f"{len(apps)} apps")
    cases = switches_before = 0
    for path in apps:
        wf0 = json.load(open(path, encoding="utf-8"))
        combos = [None] + list(drivers(wf0))
        name = os.path.basename(path)
        bad = 0
        for d in combos:
            values = dict([d]) if d else {}
            wf = appwf.apply_values(json.loads(json.dumps(wf0)), values)
            raw = appwf.fix_custom_combos(convert_ui_to_api(wf, OI), wf)
            switches_before += sum(n["class_type"] == "ComfySwitchNode" for n in raw.values())
            _wf, pruned = appwf.build_app_prompt(path, values, OI)
            ref = reference(raw)
            cases += 1
            ok = check(not any(n["class_type"] == "ComfySwitchNode" for n in pruned.values()), f"{name} {d}: switch left after pruning")
            ok &= check(set(pruned) == set(ref), f"{name} {d}: nodes differ: extra {sorted(set(pruned) - set(ref))[:5]} missing {sorted(set(ref) - set(pruned))[:5]}")
            ok &= check(all(pruned[k]["inputs"] == ref[k] for k in set(pruned) & set(ref)),
                        f"{name} {d}: inputs differ on {[k for k in set(pruned) & set(ref) if pruned[k]['inputs'] != ref[k]][:5]}")
            bad += not ok
        print(f"{'PASS' if not bad else 'FAIL'} {name}: {len(combos)} cases (default + every dropdown option / toggle), pruned == ComfyUI's taken branches")
    print(f"\n{cases} cases, {switches_before} switch nodes before pruning; " + (f"{len(fails)} failed" if fails else "all passed"))
    sys.exit(1 if fails else 0)


main()
