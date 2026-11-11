"""Keep the queue: ComfyUI forgets its queue when it stops. Studio writes a copy of the queue to disk
whenever it changes, so after a shutdown, crash or restart Studio can ask "continue the unfinished
renders?" and send them again.

Normal items go to manny_studio/queue_saved.json; Incognito items (and their prompts, which name private
files) go only to the private space. Login keys that API nodes carry are never written."""
import asyncio
import copy
import hashlib
import json
import os
import threading
import time

import urllib.error
import urllib.request
from aiohttp import web

H = {}
EVERY = 3                       # seconds between looks at the queue
_lock = threading.Lock()
_unfinished = {False: [], True: []}
_last = {"sig": None}
SECRET_KEYS = ("api_key", "auth_token", "token", "password", "secret")
MULTI_STEP = {"studio-videotools": "only the render step is redone: run the video tool again for the joined result"}


def _files(private):
    if private:
        d = H["_priv_root"]()
        return os.path.join(d, ".queue_saved.json"), os.path.join(d, ".queue_unfinished.json")
    d = H["_data_dir"]()
    return os.path.join(d, "queue_saved.json"), os.path.join(d, "queue_unfinished.json")


def _read(p):
    try:
        with open(p, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _write(p, items):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f)
    os.replace(tmp, p)


def _clean_extra(extra):
    out = {}
    for k, v in (extra or {}).items():
        if any(s in k.lower() for s in SECRET_KEYS):
            continue
        out[k] = v
    return out


def _is_private(pid, prompt):
    if pid in H["_pending_private"]:
        return True
    return any((n.get("_meta") or {}).get("title") == "private prefix" for n in prompt.values() if isinstance(n, dict))


def _label(prompt, extra):
    lab = extra.get("manny_label")
    if lab:
        return str(lab)
    app = extra.get("manny_app")
    if app:
        a = os.path.basename(str(app)).replace(".app.json", "")
        for pre in ("Manny Studio - ", "Studio - "):
            a = a.replace(pre, "")
        return a
    for n in prompt.values():
        if isinstance(n, dict) and str(n.get("class_type", "")).startswith("Save"):
            pfx = (n.get("inputs") or {}).get("filename_prefix")
            if isinstance(pfx, str):
                return pfx.split("/")[-1]
    return "ComfyUI render"


def _snapshot():
    q = getattr(H["PromptServer"].instance, "prompt_queue", None)
    if q is None:
        raise LookupError("not ready")
    get = getattr(q, "get_current_queue_volatile", None) or q.get_current_queue
    running, queued = get()
    items = {False: [], True: []}
    for is_run, lst in ((True, running), (False, sorted(queued, key=lambda t: t[0]))):
        for t in lst:
            try:
                number, pid, prompt, extra, outputs = t[0], t[1], t[2], t[3] or {}, t[4]
            except Exception:
                continue
            priv = _is_private(pid, prompt)
            items[priv].append({"prompt_id": pid, "number": number, "running": is_run, "saved_at": time.time(),
                                "client_id": extra.get("client_id"), "label": _label(prompt, extra),
                                "prompt": copy.deepcopy(prompt), "extra_data": _clean_extra(extra),
                                "outputs": list(outputs or [])})
    return items


def _tick():
    try:
        items = _snapshot()
    except LookupError:
        return
    except Exception as e:
        print("[Studio] queue keep:", e)
        return
    sig = hashlib.md5(json.dumps([[i["prompt_id"] for i in items[p]] for p in (False, True)]).encode()).hexdigest()
    if sig == _last["sig"]:
        return
    _last["sig"] = sig
    for priv in (False, True):
        saved, _u = _files(priv)
        if items[priv] or os.path.exists(saved):
            _write(saved, items[priv])


def _adopt_leftovers():
    """At start: whatever was in the queue when ComfyUI stopped becomes 'unfinished' until the person decides."""
    for priv in (False, True):
        saved, unf = _files(priv)
        old = _read(unf)
        new = _read(saved)
        seen = {i.get("prompt_id") for i in old}
        merged = old + [i for i in new if i.get("prompt_id") not in seen]
        _unfinished[priv] = merged
        if merged:
            _write(unf, merged)
        if os.path.exists(saved):
            _write(saved, [])
        if merged:
            print(f"[Studio] {len(merged)} unfinished {'incognito ' if priv else ''}render(s) from before the restart are waiting")


def _loop():
    _adopt_leftovers()
    while True:
        time.sleep(EVERY)
        with _lock:
            _tick()


def _public(i):
    return {"prompt_id": i.get("prompt_id"), "label": i.get("label"), "running": bool(i.get("running")),
            "saved_at": i.get("saved_at"), "note": MULTI_STEP.get(i.get("client_id"))}


def _post_prompt(item):
    extra = dict(item.get("extra_data") or {})
    cid = extra.pop("client_id", None) or item.get("client_id") or "studio-resume"
    body = {"prompt": item["prompt"], "client_id": cid, "extra_data": extra}
    if item.get("outputs"):
        body["partial_execution_targets"] = item["outputs"]
    req = urllib.request.Request(f"http://127.0.0.1:{H['_port']()}/prompt", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            body.pop("partial_execution_targets", None)       # older ComfyUI: no partial targets
            req = urllib.request.Request(req.full_url, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except Exception:
            raise RuntimeError(e.read().decode("utf-8", "replace")[:300] if hasattr(e, "read") else str(e))


def setup(ns):
    H.update(ns)
    routes = ns["routes"]

    def _privs(request):
        return (False, True) if H["auth"].unlocked(request) else (False,)

    @routes.get("/manny/api/queue/unfinished")
    async def unfinished(request):
        out = []
        for p in _privs(request):
            out += [dict(_public(i), private=p) for i in _unfinished[p]]
        return web.json_response({"items": out})

    @routes.post("/manny/api/queue/unfinished/{what}")
    async def decide(request):
        what = request.match_info["what"]
        if what not in ("resume", "discard"):
            raise web.HTTPNotFound()
        b = await request.json() if request.can_read_body else {}
        only = set(b.get("ids") or [])
        sent, failed = [], []
        for p in _privs(request):
            keep = []
            for i in _unfinished[p]:
                if only and i.get("prompt_id") not in only:
                    keep.append(i)
                    continue
                if what == "resume":
                    try:
                        r = await asyncio.to_thread(_post_prompt, i)
                        if r.get("prompt_id"):
                            sent.append(r["prompt_id"])
                            if p:
                                H["_pending_private"].add(r["prompt_id"])
                        else:
                            failed.append({"label": i.get("label"), "error": str(r)[:200]})
                            keep.append(i)
                    except Exception as e:
                        failed.append({"label": i.get("label"), "error": str(e)[:300]})
                        keep.append(i)
            _unfinished[p] = keep
            _w = _files(p)[1]
            if keep:
                _write(_w, keep)
            elif os.path.exists(_w):
                os.remove(_w)
        return web.json_response({"sent": len(sent), "failed": failed})

    threading.Thread(target=_loop, name="studio-queue-keep", daemon=True).start()
