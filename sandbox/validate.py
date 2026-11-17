"""ComfyUI's prompt validation, as the stand-in applies it: the same checks, error types and messages for the cases Studio
can hit (unknown node, no outputs, missing required input, bad link, type mismatch, value not in list, out of range).
Pure functions over an object_info dict, so tests can call them directly.

ComfyUI's V3 dynamic inputs are handled as ComfyUI expands them: an autogrow input ("values" with "values.a",
"values.b"...), a dynamic combo ("sampling_mode" plus "sampling_mode.temperature"...) and match-type inputs/outputs,
which accept any of their allowed types. Nodes with their own validate_inputs (custom_validation.json, read from
ComfyUI's source) skip the list and range checks for those inputs, like ComfyUI; the file loaders check the file exists."""
import json
import os

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_validation.json"), encoding="utf-8") as _f:
    CUSTOM_VALIDATION = json.load(_f)
FILE_CHECKS = {"LoadImage": ("image", "Invalid image file: {}"), "LoadImageMask": ("image", "Invalid image file: {}"),
               "LoadVideo": ("file", "Invalid video file: {}"), "LoadAudio": ("audio", "Invalid audio file: {}")}
WILDCARD_TYPES = ("COMFY_MATCHTYPE_V3", "*")


def _input_spec(info, name):
    for sect in ("required", "optional"):
        d = (info.get("input") or {}).get(sect) or {}
        if name in d:
            return d[name], sect == "required"
    return None, False


def _combo_options(spec):
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options", [])
    return None


def _types_match(received, expected):
    if received in WILDCARD_TYPES or expected in WILDCARD_TYPES:
        return True
    if isinstance(expected, list):          # a combo fed from another node's output
        return True
    r = set(str(received).split(","))
    e = set(str(expected).split(","))
    return bool(r & e)


def _err(kind, message, details, extra=None):
    return {"type": kind, "message": message, "details": details, "extra_info": extra or {}}


def _expand(info, inputs):
    """The inputs to check, with ComfyUI's dynamic inputs expanded: {name: (spec, required)}."""
    out = {}
    for sect in ("required", "optional"):
        for name, spec in ((info.get("input") or {}).get(sect) or {}).items():
            kind = spec[0] if isinstance(spec, list) and spec else None
            extra = spec[1] if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) else {}
            if kind == "COMFY_AUTOGROW_V3":
                grown = [k for k in inputs if k.startswith(name + ".")]
                tmpl = (extra.get("template") or {}).get("input") or {}
                tspec = next(iter(((tmpl.get("required") or {}) | (tmpl.get("optional") or {})).values()), ["*", {}])
                if len(grown) < int((extra.get("template") or {}).get("min", 0)) and sect == "required":
                    out[name] = (["*", {}], True)            # reported as missing below
                for k in grown:
                    out[k] = (tspec, False)
            elif kind == "COMFY_DYNAMICCOMBO_V3":
                keys = [o.get("key") for o in extra.get("options", [])]
                out[name] = ([keys, {}], sect == "required")
                chosen = next((o for o in extra.get("options", []) if o.get("key") == inputs.get(name)), None)
                for sub_sect in ("required", "optional"):
                    for sub, sub_spec in (((chosen or {}).get("inputs") or {}).get(sub_sect) or {}).items():
                        out[f"{name}.{sub}"] = (sub_spec, sub_sect == "required" and sect == "required")
            else:
                out[name] = (spec, sect == "required")
    return out


def validate_node(prompt, node_id, object_info, cache, file_exists=None):
    """Errors of one node and everything it depends on (each node checked once). Returns a list of (node_id, error).
    file_exists(kind, name) answers for the file loaders (kind "image", "video" or "audio")."""
    if node_id in cache:
        return []
    cache[node_id] = True
    node = prompt[node_id]
    ct = node["class_type"]
    info = object_info[ct]
    inputs = node.get("inputs") or {}
    own = set(CUSTOM_VALIDATION.get(ct, []))
    found = []
    for name, (spec, required) in _expand(info, inputs).items():
        if name not in inputs:
            if required:
                found.append((node_id, _err("required_input_missing", "Required input is missing", name,
                                            {"input_name": name})))
            continue
        val = inputs[name]
        multiselect = len(spec) > 1 and isinstance(spec[1], dict) and spec[1].get("multiselect")
        if isinstance(val, list) and not multiselect:
            if len(val) != 2 or not isinstance(val[1], int):
                found.append((node_id, _err("bad_linked_input", "Bad linked input, must be a length-2 list of [node_id, slot_index]",
                                            name, {"input_name": name, "received_value": val})))
                continue
            src_id = str(val[0])
            if src_id not in prompt:
                found.append((node_id, _err("bad_linked_input", "Bad linked input, must be a length-2 list of [node_id, slot_index]",
                                            name, {"input_name": name, "received_value": val})))
                continue
            src_info = object_info.get(prompt[src_id].get("class_type"))
            if src_info is not None:
                outs = src_info.get("output") or []
                if val[1] >= len(outs):
                    found.append((node_id, _err("bad_linked_input", "Bad linked input, must be a length-2 list of [node_id, slot_index]",
                                                name, {"input_name": name, "received_value": val})))
                    continue
                expected = spec[0]
                if expected == "COMBO":
                    expected = "*"
                if not _types_match(outs[val[1]], expected):
                    found.append((node_id, _err("return_type_mismatch", "Return type mismatch between linked nodes",
                                                f"{name}, received_type({outs[val[1]]}) mismatch input_type({expected})",
                                                {"input_name": name, "received_type": outs[val[1]], "linked_node": val})))
                    continue
                found += validate_node(prompt, src_id, object_info, cache, file_exists)
            continue
        if name.split(".")[0] in own or "**" in own:
            if ct in FILE_CHECKS and name == FILE_CHECKS[ct][0] and file_exists is not None:
                ok = isinstance(val, str) and file_exists(FILE_CHECKS[ct][0], val)
                if not ok:
                    found.append((node_id, _err("custom_validation_failed", "Custom validation failed for node",
                                                f"{name} - {FILE_CHECKS[ct][1].format(val)}", {"input_name": name})))
            continue
        kind = spec[0]
        extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        if kind in ("INT", "FLOAT"):
            try:
                num = int(val) if kind == "INT" else float(val)
            except (TypeError, ValueError):
                found.append((node_id, _err("invalid_input_type", f"Failed to convert an input value to a {kind} value",
                                            f"{name}, {val}", {"input_name": name, "received_value": val})))
                continue
            if "min" in extra and num < extra["min"]:
                found.append((node_id, _err("value_smaller_than_min", "Value {} smaller than min of {}".format(num, extra["min"]),
                                            f"{name}", {"input_name": name, "received_value": val})))
            if "max" in extra and num > extra["max"]:
                found.append((node_id, _err("value_bigger_than_max", "Value {} bigger than max of {}".format(num, extra["max"]),
                                            f"{name}", {"input_name": name, "received_value": val})))
            continue
        options = _combo_options(spec)
        if options is not None:
            bad = [v for v in (val if extra.get("multiselect") and isinstance(val, list) else [val]) if v not in options]
            if bad:
                list_info = f"(list of length {len(options)})" if len(options) > 20 else str(options)
                found.append((node_id, _err("value_not_in_list", "Value not in list",
                                            f"{name}: {', '.join(repr(v) for v in bad)} not in {list_info}",
                                            {"input_name": name, "input_config": None if len(options) > 20 else spec,
                                             "received_value": val})))
    return found


def validate_prompt(prompt, object_info, file_exists=None):
    """(ok, error, good_outputs, node_errors) like ComfyUI's validate_prompt."""
    outputs = []
    for x, node in prompt.items():
        if not isinstance(node, dict) or "class_type" not in node:
            title = (node or {}).get("_meta", {}).get("title") if isinstance(node, dict) else None
            return False, _err("missing_node_type", f"Node '{title or f'ID #{x}'}' has no class_type. The workflow may be corrupted or a custom node is missing.",
                               f"Node ID '#{x}'", {"node_id": x, "class_type": None, "node_title": title}), [], {}
        info = object_info.get(node["class_type"])
        if info is None:
            title = node.get("_meta", {}).get("title", node["class_type"])
            return False, _err("missing_node_type", f"Node '{title}' not found. The custom node may not be installed.",
                               f"Node ID '#{x}'", {"node_id": x, "class_type": node["class_type"], "node_title": title}), [], {}
        if info.get("output_node"):
            outputs.append(x)
    if not outputs:
        return False, _err("prompt_no_outputs", "Prompt has no outputs", ""), [], {}
    good, errors, node_errors = [], [], {}
    for o in outputs:
        found = validate_node(prompt, o, object_info, {}, file_exists)
        if not found:
            good.append(o)
            continue
        errors.append((o, [e for _n, e in found]))
        for nid, e in found:
            ne = node_errors.setdefault(nid, {"errors": [], "dependent_outputs": [], "class_type": prompt[nid]["class_type"]})
            if e not in ne["errors"]:
                ne["errors"].append(e)
            if o not in ne["dependent_outputs"]:
                ne["dependent_outputs"].append(o)
    if not good:
        details = "\n".join(f"{e['message']}: {e['details']}" for _o, es in errors for e in es)
        return False, _err("prompt_outputs_failed_validation", "Prompt outputs failed validation", details), [], node_errors
    return True, None, good, node_errors
