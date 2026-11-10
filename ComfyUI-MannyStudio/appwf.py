"""App workflow -> ComfyUI API prompt, without ComfyUI imports (Studio and the Mac parity test share it)."""
import json

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
    return wf, fix_custom_combos(convert_ui_to_api(wf, object_info), wf)
