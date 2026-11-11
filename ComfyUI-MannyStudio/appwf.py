"""App workflow -> ComfyUI API prompt, without ComfyUI imports (Studio and the Mac parity test share it)."""
import ast
import json
import math

from .mconv.workflow_to_api import convert_ui_to_api


def combo_options(node):
    wv = node.get("widgets_values") or []
    return [v for v in wv[2:] if isinstance(v, str) and v != ""]


def apply_values(wf, values):
    nodes = {str(n["id"]): n for n in wf.get("nodes", [])}
    for key, val in (values or {}).items():
        nid, wname = key.split(":", 1)
        node = nodes.get(nid)
        if not node:
            continue
        wv = node.setdefault("widgets_values", [])
        t = node.get("type")
        if not wv:
            wv.append(val)
            continue
        if t == "CustomCombo":
            opts = combo_options(node)
            wv[0] = val
            if len(wv) > 1 and val in opts:
                wv[1] = opts.index(val)
        elif t == "PrimitiveInt":
            wv[0] = int(val)
            if len(wv) > 1:
                wv[1] = "fixed"
        elif t == "PrimitiveFloat":
            wv[0] = float(val)
        elif t == "PrimitiveBoolean":
            wv[0] = bool(val)
        else:
            wv[0] = val
    return wf


def fix_custom_combos(api, wf):
    """The UI-to-API converter drops CustomCombo's option list, so the node's
    INDEX output always came out 0 (aspect and size ignored). Send what the
    ComfyUI frontend sends: choice, index and option1..N."""
    nodes = {str(n["id"]): n for n in wf.get("nodes", [])}
    for sg in (wf.get("definitions") or {}).get("subgraphs", []):
        for n in sg.get("nodes", []):
            nodes.setdefault(str(n["id"]), n)
    for nid, node in api.items():
        if node.get("class_type") != "CustomCombo":
            continue
        ui = nodes.get(nid.split(":")[-1])
        if not ui:
            continue
        opts = combo_options(ui)
        choice = node["inputs"].get("choice")
        ins = {"choice": choice, "index": opts.index(choice) if choice in opts else 0}
        for k, o in enumerate(opts, 1):
            ins[f"option{k}"] = o
        ins[f"option{len(opts) + 1}"] = ""
        node["inputs"] = ins
    return api


def build_app_prompt(app_path, values, object_info):
    """(workflow, api prompt) for the *.app.json at app_path with values applied, converted against object_info."""
    with open(app_path, encoding="utf-8") as f:
        wf = json.load(f)
    wf = apply_values(wf, values)
    return wf, prune_switches(fix_custom_combos(convert_ui_to_api(wf, object_info), wf), object_info)


# ---------------------------------------------------------------- dead switch branches
# ComfyUI validates every input of a ComfySwitchNode, the branch not taken too, so a worker would need every
# model the app could ever pick. When the switch is a constant (a toggle, a dropdown's index, or math on those)
# the switch is replaced by its taken branch and whatever no output still reaches is dropped. ComfyUI runs only
# what outputs reach, so the render is the same. A taken branch with nothing connected ("use?" switches on optional
# references) makes the switch output None: the input it fed is removed, which is how an unconnected optional looks.
# the functions ComfyMathExpression offers (comfy_extras/nodes_math.py MATH_FUNCTIONS)
_MATH = {"sum": lambda *a: sum(a[0]) if len(a) == 1 and hasattr(a[0], "__iter__") else sum(a), "min": min, "max": max,
         "abs": abs, "round": round, "pow": pow, "sqrt": math.sqrt, "ceil": math.ceil, "floor": math.floor, "log": math.log,
         "log2": math.log2, "log10": math.log10, "sin": math.sin, "cos": math.cos, "tan": math.tan, "int": int, "float": float}
_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b, ast.Pow: lambda a, b: a ** b}
_CMP = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b}


class _NotConstant(Exception):
    pass


_NONE = object()    # a switch whose taken branch has nothing connected


def _eval(node, names):
    """Arithmetic, comparisons, and/or/not, x if c else y and _MATH calls; anything else is not folded."""
    if isinstance(node, ast.Expression):
        return _eval(node.body, names)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        raise _NotConstant(node.id)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        return _BIN[type(node.op)](_eval(node.left, names), _eval(node.right, names))
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, names)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return +v
        if isinstance(node.op, ast.Not):
            return not v
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, names) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, names)
        for op, right in zip(node.ops, node.comparators):
            if type(op) not in _CMP:
                raise _NotConstant("comparison")
            r = _eval(right, names)
            if not _CMP[type(op)](left, r):
                return False
            left = r
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, names) if _eval(node.test, names) else _eval(node.orelse, names)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and callable(_MATH.get(node.func.id)) and not node.keywords:
        return _MATH[node.func.id](*[_eval(a, names) for a in node.args])
    raise _NotConstant(type(node).__name__)


def _is_link(v):
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int)


def _const(api, v, seen=()):
    """The constant value v (a literal or [node_id, slot]) carries, or _NotConstant."""
    if not _is_link(v):
        return v
    nid, slot = v
    if nid in seen or nid not in api:
        raise _NotConstant(nid)
    seen = seen + (nid,)
    n = api[nid]
    ct, ins = n.get("class_type"), n.get("inputs") or {}
    if ct in ("PrimitiveBoolean", "PrimitiveInt", "PrimitiveFloat", "PrimitiveString", "PrimitiveStringMultiline") and slot == 0:
        return _const(api, ins.get("value"), seen)
    if ct == "CustomCombo":
        if slot == 0:
            return _const(api, ins.get("choice"), seen)
        if slot == 1 and isinstance(ins.get("index"), int):
            return ins["index"]
    if ct == "ComfySwitchNode" and slot == 0:
        return _const(api, ins.get("on_true" if _const(api, ins.get("switch"), seen) else "on_false"), seen)
    if ct == "ComfyMathExpression" and slot in (0, 1, 2):
        names = {k.split(".", 1)[1]: _const(api, x, seen) for k, x in ins.items() if k.startswith("values.")}
        r = _eval(ast.parse(str(ins.get("expression", "")), mode="eval"), names)
        if isinstance(r, bool):
            r = int(r)
        return (float(r), int(r), bool(r))[slot]
    raise _NotConstant(ct)


def prune_switches(api, object_info):
    """Fold constant ComfySwitchNodes into their taken branch and drop nodes no output reaches. In place; returns api."""
    while True:
        folded = {}
        for nid, n in api.items():
            if n.get("class_type") != "ComfySwitchNode":
                continue
            ins = n.get("inputs") or {}
            try:
                taken = "on_true" if _const(api, ins.get("switch")) else "on_false"
            except (_NotConstant, ArithmeticError, TypeError, ValueError, SyntaxError):
                continue
            folded[nid] = ins.get(taken, _NONE)
        if not folded:
            break
        for n in api.values():
            for k, v in list((n.get("inputs") or {}).items()):
                while _is_link(v) and v[0] in folded and v[1] == 0:
                    v = folded[v[0]]
                if v is _NONE:
                    del n["inputs"][k]
                else:
                    n["inputs"][k] = v
        for nid in folded:
            del api[nid]
    keep, todo = set(), [nid for nid, n in api.items() if (object_info.get(n.get("class_type")) or {}).get("output_node")]
    if not todo:
        return api      # no known output node: leave the graph alone rather than empty it
    while todo:
        nid = todo.pop()
        if nid in keep or nid not in api:
            continue
        keep.add(nid)
        todo += [v[0] for v in (api[nid].get("inputs") or {}).values() if _is_link(v)]
    for nid in [k for k in api if k not in keep]:
        del api[nid]
    return api
