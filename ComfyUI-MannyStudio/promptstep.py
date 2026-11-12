"""Prompt first, then the picture: text encoders and LLMs run as their own step.

A text encoder (Gemma 12B for LTX, the H3 / P-H3 encoders, Qwen, an LLM that writes a prompt...) is only needed to turn
words into numbers, but inside one graph ComfyUI keeps it in memory for the whole render. On this PC graphics memory IS
system memory, so that is 10-20 GB the video/image/music model can't use.

So every graph Studio queues on this PC is split in two:
  1. "Reading the prompt": the text encoder(s) and whatever reads them (CLIPTextEncode, an LLM's TextGenerate, an
     image-edit conditioning node...), each result handed to Studio Keep, which holds it and then asks ComfyUI to unload
     every model and clear its caches as soon as this step ends;
  2. the render, where Studio Recall gives back what Studio Keep held.
Both are queued together, one after the other. Results are kept in memory and also saved to disk (not for Incognito), so
a render resumed after a restart still finds its prompt. A graph that can't be split this way (a text encoder that also
feeds a LoRA or a model) is queued as it was."""
import copy
import hashlib
import json
import os
import threading
import time

H = {}
_STORE = {}
_lock = threading.Lock()
KEEP_DAYS = 7


class _Any(str):
    def __ne__(self, other):
        return False


ANY = _Any("*")


def _cache_dir():
    d = os.path.join(H["_data_dir"](), "prompt_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _to_cpu(v):
    try:
        import torch
    except Exception:
        return v
    if isinstance(v, torch.Tensor):
        return v.detach().to("cpu")
    if isinstance(v, list):
        return [_to_cpu(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_to_cpu(x) for x in v)
    if isinstance(v, dict):
        return {k: _to_cpu(x) for k, x in v.items()}
    return v


class StudioKeep:
    """Holds one result of the prompt step for the render step, then frees every model when the step ends."""
    CATEGORY = "Manny Studio"
    FUNCTION = "keep"
    RETURN_TYPES = ()
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"value": (ANY, {}), "key": ("STRING", {"default": ""}),
                             "private": ("BOOLEAN", {"default": False})}}

    @classmethod
    def IS_CHANGED(cls, **kw):
        return float("nan")

    def keep(self, value, key, private=False):
        v = _to_cpu(value)
        with _lock:
            _STORE[key] = v
        if not private:
            try:
                import torch
                torch.save(v, os.path.join(_cache_dir(), key + ".pt"))
            except Exception as e:
                print("[Studio] prompt step: kept in memory only:", e)
        try:
            from server import PromptServer
            q = PromptServer.instance.prompt_queue
            q.set_flag("unload_models", True)
            q.set_flag("free_memory", True)
        except Exception as e:
            print("[Studio] prompt step: could not ask for memory to be freed:", e)
        return {}


class StudioRecall:
    """Gives back what Studio Keep held for this render."""
    CATEGORY = "Manny Studio"
    FUNCTION = "recall"
    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("value",)

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"key": ("STRING", {"default": ""})}}

    @classmethod
    def IS_CHANGED(cls, key):
        return key

    def recall(self, key):
        with _lock:
            if key in _STORE:
                return (_STORE[key],)
        p = os.path.join(_cache_dir(), key + ".pt")
        if os.path.isfile(p):
            import torch
            v = torch.load(p, map_location="cpu", weights_only=False)       # Studio's own file, written by Studio Keep
            with _lock:
                _STORE[key] = v
            return (v,)
        raise RuntimeError("The prompt step for this render didn't run (or was Incognito and ComfyUI restarted): queue it again")


NODES = {"StudioKeep": StudioKeep, "StudioRecall": StudioRecall}
NAMES = {"StudioKeep": "Studio Keep (prompt step)", "StudioRecall": "Studio Recall (prompt step)"}


# ------------------------------------------------------------------ splitting a graph
def _returns(ct):
    try:
        import nodes
        cls = nodes.NODE_CLASS_MAPPINGS.get(ct)
        return tuple(str(t) for t in (getattr(cls, "RETURN_TYPES", None) or ())) if cls else None
    except Exception:
        return None


def _links(node):
    for k, v in (node.get("inputs") or {}).items():
        if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
            yield k, v


def _upstream(api, start):
    seen, todo = set(), list(start)
    while todo:
        n = todo.pop()
        if n in seen or n not in api:
            continue
        seen.add(n)
        todo += [v[0] for _k, v in _links(api[n])]
    return seen


def _is_output(ct):
    try:
        import nodes
        cls = nodes.NODE_CLASS_MAPPINGS.get(ct)
        return bool(getattr(cls, "OUTPUT_NODE", False))
    except Exception:
        return False


def split(api, private=False, returns=_returns, is_output=_is_output):
    """(prompt_graph, render_graph), or (None, api) when there's nothing to split or it can't be split safely.
    prompt_graph is None too when every result is already held (the same prompt was read before)."""
    rt = {k: returns(n.get("class_type")) for k, n in api.items()}
    if any(r is None for r in rt.values()):
        return None, api                       # a node this PC doesn't know: leave the graph to ComfyUI's checks
    # the text-encoder group: loaders that give CLIP (and no MODEL), plus nodes that only turn CLIP into CLIP
    te = {k for k, r in rt.items() if "CLIP" in r and "MODEL" not in r and not any(True for _ in _links(api[k]))}
    if not te:
        return None, api
    changed = True
    while changed:
        changed = False
        for k, n in api.items():
            if k in te or not rt[k] or set(rt[k]) != {"CLIP"}:
                continue
            srcs = [v[0] for _i, v in _links(n)]
            if srcs and all(s in te for s in srcs):
                te.add(k)
                changed = True
    consumers = {k for k, n in api.items() if k not in te and any(v[0] in te for _i, v in _links(n))}
    if not consumers:
        return None, api
    enc = _upstream(api, consumers)
    for k in enc - te:
        if "MODEL" in rt[k] or "CLIP" in rt[k]:
            return None, api                   # e.g. a LoRA on model + CLIP: the encoder can't leave the render
    for k in consumers:
        if is_output(api[k].get("class_type")):
            return None, api                   # a node that reads the prompt AND saves something: keep it whole
    outs = [k for k, n in api.items() if is_output(n.get("class_type"))]
    # only results that come from the text encoder are handed over; plain inputs (a VAE, a picture) load again in the render
    from_te = {k for k in enc if k in te or (_upstream(api, [k]) & te)}
    # the render: everything the outputs need, with links into the prompt step replaced by Recall nodes
    render, used, recall = {}, set(), {}
    todo = list(outs)
    while todo:
        k = todo.pop()
        if k in render:
            continue
        node = copy.deepcopy(api[k])
        for name, v in list(_links(node)):
            if v[0] in from_te:
                if v[0] in te:
                    return None, api           # the render itself needs the text encoder
                used.add((v[0], int(v[1])))
                node["inputs"][name] = ["__recall__", v[0], int(v[1])]
            else:
                todo.append(v[0])
        render[k] = node
    if not used:
        return None, api
    sub = {k: api[k] for k in sorted(enc)}
    sig = hashlib.sha1(json.dumps(sub, sort_keys=True).encode()).hexdigest()[:16]
    keys = {}
    for (k, slot) in sorted(used):
        keys[(k, slot)] = f"{sig}_{k}_{slot}".replace("/", "_").replace("\\", "_")
        rid = f"studio_recall_{k}_{slot}"
        recall[rid] = {"class_type": "StudioRecall", "inputs": {"key": keys[(k, slot)]}}
    for node in render.values():
        for name, v in list((node.get("inputs") or {}).items()):
            if isinstance(v, list) and len(v) == 3 and v[0] == "__recall__":
                node["inputs"][name] = [f"studio_recall_{v[1]}_{v[2]}", 0]
    render.update(recall)
    held = all(_held(key, private) for key in keys.values())
    if held:
        return None, render
    prompt = copy.deepcopy(sub)
    for k in te:
        ins = (prompt.get(k) or {}).get("inputs") or {}
        if ins.get("device") == "cpu":         # alone in memory now: the faster side is fine
            ins["device"] = "default"
    for (k, slot), key in keys.items():
        prompt[f"studio_keep_{k}_{slot}"] = {"class_type": "StudioKeep", "inputs": {"value": [k, slot], "key": key, "private": bool(private)}}
    return prompt, render


def _held(key, private):
    with _lock:
        if key in _STORE:
            return True
    return not private and os.path.isfile(os.path.join(_cache_dir(), key + ".pt"))


def _tidy():
    try:
        d = _cache_dir()
        cut = time.time() - KEEP_DAYS * 86400
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if f.endswith(".pt") and os.path.getmtime(p) < cut:
                os.remove(p)
    except Exception:
        pass


def setup(ns):
    H.update(ns)
    ns["NODE_CLASS_MAPPINGS"].update(NODES)
    ns["NODE_DISPLAY_NAME_MAPPINGS"].update(NAMES)
    _tidy()
