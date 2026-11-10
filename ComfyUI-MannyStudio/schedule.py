"""Render later: Studio holds a job and sends it to ComfyUI at a set time, or when the PC is free.

A scheduled item is the same request the Generate button sends to /manny/api/run, kept on disk so it
survives restarts. Incognito items are kept in the private space, never in the normal data folder."""
import asyncio
import json
import os
import time
import uuid

from aiohttp import web, ClientSession

H = {}
IDLE_SECONDS = 120          # "when the PC is free": the queue has been empty this long
KEEP_DONE = 24 * 3600       # sent items stay listed for a day


def _file(private):
    return os.path.join(H["_priv_root"](), ".schedule.json") if private else os.path.join(H["_data_dir"](), "schedule.json")


def _load(private):
    try:
        with open(_file(private), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(private, items):
    p = _file(private)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f)
    os.replace(tmp, p)


def _public(it):
    return {k: it.get(k) for k in ("id", "label", "when", "at", "created", "status", "sent_at", "prompt_ids", "error", "private", "kind")}


_idle_since = {"t": None}


def _queue_empty():
    try:
        q = H["PromptServer"].instance.prompt_queue
        return q.get_tasks_remaining() == 0
    except Exception:
        return False


async def _fire(it):
    body = dict(it["run"])
    body["client_id"] = "studio-schedule"
    async with ClientSession() as s:
        async with s.post(f"http://127.0.0.1:{H['_port']()}/manny/api/run", json=body) as r:
            data = await r.json()
            if r.status != 200:
                raise RuntimeError(json.dumps(data.get("error") or data)[:400])
    return [q.get("prompt_id") for q in data.get("queued", [])]


async def _tick():
    now = time.time()
    empty = _queue_empty()
    if empty:
        _idle_since["t"] = _idle_since["t"] or now
    else:
        _idle_since["t"] = None
    idle_ok = empty and _idle_since["t"] and now - _idle_since["t"] >= IDLE_SECONDS
    for private in (False, True):
        items = _load(private)
        changed = False
        for it in items:
            if it.get("status") != "waiting":
                continue
            due = (it["when"] == "at" and now >= (it.get("at") or 0)) or (it["when"] == "idle" and idle_ok)
            if not due:
                continue
            try:
                it["prompt_ids"] = await _fire(it)
                it.update(status="sent", sent_at=now)
            except Exception as e:
                it.update(status="failed", error=str(e), sent_at=now)
            changed = True
            if it["when"] == "idle":
                idle_ok = False      # one "when free" job at a time; the next waits for the queue to empty again
                _idle_since["t"] = None
        keep = [it for it in items if it.get("status") == "waiting" or now - (it.get("sent_at") or now) < KEEP_DONE]
        if changed or len(keep) != len(items):
            _save(private, keep)


_started = {"task": None}


def _ensure():
    """Start the timer inside the running server loop (safe to call from any request handler)."""
    t = _started["task"]
    if t is None or t.done():
        _started["task"] = asyncio.get_running_loop().create_task(_loop())


async def _loop():
    while True:
        try:
            await _tick()
        except Exception as e:
            print("[Studio] schedule:", e)
        await asyncio.sleep(20)


def setup(ns):
    H.update(ns)
    routes = ns["routes"]
    _need_private, _flag = ns["_need_private"], ns["_flag"]

    @routes.post("/manny/api/schedule")
    async def add(request):
        _ensure()
        b = await request.json()
        run = b.get("run") or {}
        if not run.get("app"):
            raise web.HTTPBadRequest(text="Nothing to schedule")
        private = bool(run.get("private"))
        when = b.get("when") if b.get("when") in ("at", "idle") else "at"
        at = float(b.get("at") or 0)
        if when == "at" and at < time.time() - 60:
            raise web.HTTPBadRequest(text="That time has already passed")
        it = {"id": uuid.uuid4().hex[:10], "run": run, "when": when, "at": at if when == "at" else None,
              "label": (b.get("label") or "Render")[:120], "kind": b.get("kind") or "image",
              "created": time.time(), "status": "waiting", "private": private}
        items = _load(private)
        items.append(it)
        _save(private, items)
        return web.json_response(_public(it))

    @routes.get("/manny/api/schedule")
    async def lst(request):
        _ensure()
        out = [_public(x) for x in _load(False)]
        if _flag(request.query.get("private")):
            _need_private(request)
            out += [_public(x) for x in _load(True)]
        out.sort(key=lambda x: (x["status"] != "waiting", x.get("at") or 9e12, x["created"]))
        return web.json_response(out)

    def _find(request):
        sid = request.match_info["sid"]
        for private in (False, True):
            items = _load(private)
            for it in items:
                if it["id"] == sid:
                    if private:
                        _need_private(request)
                    return private, items, it
        raise web.HTTPNotFound(text="Not found")

    @routes.delete("/manny/api/schedule/{sid}")
    async def remove(request):
        private, items, it = _find(request)
        _save(private, [x for x in items if x is not it])
        return web.json_response({"ok": True})

    @routes.post("/manny/api/schedule/{sid}/now")
    async def now(request):
        private, items, it = _find(request)
        try:
            it["prompt_ids"] = await _fire(it)
            it.update(status="sent", sent_at=time.time())
        except Exception as e:
            it.update(status="failed", error=str(e), sent_at=time.time())
        _save(private, items)
        return web.json_response(_public(it))

    loop = getattr(ns["PromptServer"].instance, "loop", None)
    if loop is not None:
        loop.call_soon_threadsafe(_ensure)
