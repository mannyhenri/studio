"""Stand-in for ComfyUI's nodes module: one class per node in the sandbox object_info (built by fake_comfy), plus the
custom nodes a pack registers. Only the class attributes are real; nothing executes."""
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}


def _spec(v):
    return tuple(v) if isinstance(v, list) else v


def register_from_object_info(object_info):
    for name, spec in object_info.items():
        if name in NODE_CLASS_MAPPINGS:
            continue
        inputs = {sect: {k: _spec(v) for k, v in (spec.get("input", {}).get(sect) or {}).items()}
                  for sect in ("required", "optional") if spec.get("input", {}).get(sect) is not None}
        NODE_CLASS_MAPPINGS[name] = type(name, (), {
            "INPUT_TYPES": classmethod(lambda cls, _i=inputs: _i),
            "RETURN_TYPES": tuple(spec.get("output") or ()),
            "RETURN_NAMES": tuple(spec.get("output_name") or ()),
            "OUTPUT_NODE": bool(spec.get("output_node")),
            "CATEGORY": spec.get("category", ""),
            "FUNCTION": "run",
            "SANDBOX_STAND_IN": True,
        })
        NODE_DISPLAY_NAME_MAPPINGS[name] = spec.get("display_name") or name
