"""Share the queue on its own: while this PC is rendering, a job waiting in its queue goes to any GPU machine that is
free and can run it as it is (nodes and models already there). Not its size: the Vegas run models bigger than the card
  (fp8 kept in RAM and sent a layer at a time, text encoders on the CPU or the other card; confer c301a683).

- Only jobs that are waiting move; this PC keeps what it's rendering. When this PC is idle it takes the next job itself.
- Incognito jobs move too (their results come back into the private space). Never moved automatically: Studio tools that wait for their result on this PC
  (video fixes, music, the AI helper), and the prompt step of a render (the render moves whole; the Mac reads the prompt).
- No downloads: a machine missing a model is skipped (Send to… still downloads when you pick a machine by hand).
- A job that fails on a machine comes back to the front of this PC's queue (put-back), and jobs using the same models
  aren't sent to that machine again for a week (e.g. a model that ran out of memory on a 32 GB card).
Settings: manny_studio/dispatch.json {"enabled": true, "incognito": true}; GET/POST /manny/api/dispatch."""
import asyncio
import hashlib
import json
import os
import time

from aiohttp import web

H = {}
EVERY = 10                  # seconds between looks
RETRY_AFTER = 600           # a job a machine turned down is asked about again after this long
AVOID_DAYS = 7
MODEL_EXT = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin", ".sft")
MODEL_KINDS = ("diffusion_models", "unet", "checkpoints", "vae", "loras", "upscale_models", "latent_upscale_models", "model_patches")
_turned_down = {}           # (worker, prompt_id) -> time
_last = {"moved": [], "note": ""}


def _f(name):
    return os.path.join(H["_data_dir"](), name)


def settings():
    try:
        with open(_f("dispatch.json"), encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        d = {}
    return {"enabled": bool(d.get("enabled", True)), "incognito": bool(d.get("incognito", True))}


def _save_settings(d):
    with open(_f("dispatch.json"), "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def _models(prompt):
    out = set()
    for n in prompt.values():
        if not isinstance(n, dict):
            continue
        for v in (n.get("inputs") or {}).values():
            if isinstance(v, str) and v.lower().endswith(MODEL_EXT):
                out.add(v.replace("\\", "/"))
    return out


def _sig(prompt):
    return hashlib.sha1(json.dumps(sorted(_models(prompt))).encode()).hexdigest()[:16]


def _avoid_load():
    try:
        with open(_f("dispatch_avoid.json"), encoding="utf-8") as fh:
            d = json.load(fh)
        cut = time.time() - AVOID_DAYS * 86400
        return [x for x in d if x.get("at", 0) > cut]
    except Exception:
        return []


def failed(worker, prompt, error=None):
    """A moved job failed on worker: don't send jobs with the same models there again for a while."""
    if not worker or not isinstance(prompt, dict):
        return
    d = _avoid_load()
    d.append({"worker": worker, "sig": _sig(prompt), "models": sorted(_models(prompt))[:12], "at": time.time(),
              "why": str(error or "")[:200]})
    with open(_f("dispatch_avoid.json"), "w", encoding="utf-8") as fh:
        json.dump(d[-200:], fh, indent=1)




def unfit(worker, prompt):
    """Why worker shouldn't get prompt automatically, or None."""
    s = _sig(prompt)
    for x in _avoid_load():
        if x.get("worker") == worker["label"] and x.get("sig") == s:
            return f"a job with these models failed on {worker['label']} before" + (f" ({x['why'][:80]})" if x.get("why") else "")
    return None                 # never its size: what doesn't run comes back (put-back) and lands in the list above


async def _tick():
    st = settings()
    if not st["enabled"]:
        return
    from server import PromptServer
    q = PromptServer.instance.prompt_queue
    get = getattr(q, "get_current_queue_volatile", None) or q.get_current_queue
    running, pending = get()
    if not running or not pending:
        return                                  # this PC is free (it takes the next one itself), or nothing waits
    rm = H["_remote"]
    now = time.time()
    for k in [k for k, t in _turned_down.items() if now - t > RETRY_AFTER]:
        _turned_down.pop(k, None)
    for w in rm.load_workers(H["_data_dir"]()):
        if any(j.get("worker") == w["label"] and j.get("status") in rm.ACTIVE for j in rm.JOBS.values()):
            continue
        if rm.JOBS_UPLOADING.get(w["label"]):
            continue
        try:
            nfo = await asyncio.wait_for(rm.info(w), 6)
        except Exception:
            continue
        if nfo.get("queue_remaining"):
            continue
        _r, pending = get()
        for t in sorted(pending, key=lambda x: x[0]):
            pid = t[1]
            if (w["label"], pid) in _turned_down:
                continue
            res, v = await H["_move_one"](pid, w, st["incognito"], auto=True)
            if res == "moved":
                v.pop("prompt", None)
                _last["moved"] = ([dict(v, at=time.time())] + _last["moved"])[:20]
                print(f"[Studio] auto-share: a waiting job went to {w['label']} (free)")
                break
            _turned_down[(w["label"], pid)] = time.time()


async def _loop():
    await asyncio.sleep(30)                     # let ComfyUI and the workers settle after a start
    while True:
        try:
            await _tick()
        except Exception as e:
            print("[Studio] auto-share:", e)
        await asyncio.sleep(EVERY)


def setup(ns):
    H.update(ns)
    routes = ns["routes"]

    @routes.get("/manny/api/dispatch")
    async def get_dispatch(request):
        return web.json_response(dict(settings(), moved=_last["moved"][:5], avoid=_avoid_load()[-10:]))

    @routes.post("/manny/api/dispatch")
    async def set_dispatch(request):
        b = await request.json()
        d = settings()
        for k in ("enabled", "incognito"):
            if k in b:
                d[k] = bool(b[k])
        if d["incognito"] and not (H["auth"].is_local(request) or H["auth"].unlocked(request)):
            raise web.HTTPForbidden(text="Unlock Incognito first")
        _save_settings(d)
        return web.json_response(d)

    try:
        loop = getattr(ns["PromptServer"].instance, "loop", None)
        if loop is not None:
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_loop()))
    except Exception as e:
        print("[Studio] auto-share could not start:", e)
