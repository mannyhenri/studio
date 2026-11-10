"""The 3D -> storyboard -> final-render pipeline, on the server so both the app and Claude (MCP) can drive it.

- a planned 3D scene becomes (or updates) a Storyboard board, one shot per camera, with its Blender preview
- any file (a render from Studio, Higgsfield, Seedance, a download) can be attached to a shot as a take or as its final
- a shot can ask for a final render: "local" (Studio's Finish: Topaz upscale + fps) or "seedance" (done by Claude through
  Higgsfield, which then imports the result back here)"""
import json
import os
import shutil
import time
import uuid

from aiohttp import web, ClientSession

H = {}
MOVE_WORDS = {"static": "static locked-off camera", "push_in": "slow dolly push-in", "pull_back": "slow dolly pull-back",
              "truck_left": "camera trucks left", "truck_right": "camera trucks right", "orbit_left": "camera orbits left around the subject",
              "orbit_right": "camera orbits right around the subject", "crane_up": "crane shot rising up", "crane_down": "crane shot coming down",
              "pan_left": "camera pans left", "pan_right": "camera pans right", "tilt_up": "camera tilts up", "tilt_down": "camera tilts down",
              "zoom_in": "slow zoom in", "zoom_out": "slow zoom out", "handheld": "handheld camera"}
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")


def _new_id():
    return uuid.uuid4().hex[:8]


def _boards():
    return H["_load_boards"]()


def _save(boards):
    H["_save_boards"](boards)


def _find(bid, sid):
    boards = _boards()
    b = next((x for x in boards if x.get("id") == bid), None)
    if not b:
        raise web.HTTPNotFound(text="No such board")
    s = next((x for x in b.get("shots", []) if x.get("id") == sid), None) if sid else None
    if sid and not s:
        raise web.HTTPNotFound(text="No such shot")
    return boards, b, s


def _ensure_takes(s):
    if "takes" not in s:
        s["takes"] = [{"pid": s.get("prompt_id"), "output": s.get("output"), "status": "done" if s.get("output") else "", "seed": None, "q": ""}] if (s.get("output") or s.get("prompt_id")) else []
        s["pick"] = 0 if s.get("output") else None
    return s


def _sync_pick(s):
    t = s["takes"][s["pick"]] if s.get("pick") is not None and s["pick"] < len(s["takes"]) else None
    s["output"] = t.get("output") if t else None


def attach(bid, sid, filename, subfolder, as_="take", label="", pick=True):
    boards, b, s = _find(bid, sid)
    out = {"filename": filename, "subfolder": subfolder or ""}
    if as_ == "final":
        s["final"] = out
        s["finish"] = {"id": None, "stage": "Done", "status": "done", "from": s.get("pick")}
        if s.get("final_request"):
            s["final_request"].update(status="done", done_at=time.time())
    else:
        _ensure_takes(s)
        s["takes"].append({"pid": None, "seed": None, "q": label or "import", "status": "done", "output": out})
        if pick or s.get("pick") is None:
            s["pick"] = len(s["takes"]) - 1
        _sync_pick(s)
    b["updated"] = time.time()
    _save(boards)
    return s


def import_file(src_path=None, data=None, name=None, private=False):
    """Copies a file into Studio's output (Videos/Imported or Images/Imported). Returns {filename, subfolder, kind}."""
    ext = os.path.splitext(name or src_path or "")[1].lower()
    kind = "video" if ext in VIDEO_EXT else "image" if ext in IMAGE_EXT else None
    if not kind:
        raise web.HTTPBadRequest(text="Only videos (mp4, mov, webm) and pictures can be imported")
    sub = ("Videos" if kind == "video" else "Images") + "/Imported"
    d = os.path.join(H["_root"](private), sub)
    os.makedirs(d, exist_ok=True)
    base = "".join(ch for ch in os.path.splitext(os.path.basename(name or src_path))[0] if ch.isalnum() or ch in " -_.")[:80] or "import"
    dest = os.path.join(d, f"{base} {time.strftime('%Y-%m-%d %H%M%S')}{ext}")
    if src_path:
        shutil.copy2(src_path, dest)
    else:
        with open(dest, "wb") as f:
            f.write(data)
    return {"filename": os.path.basename(dest), "subfolder": sub, "kind": kind, "path": dest}


def scene_to_board(scid):
    scene = next((x for x in H["_previs"]._load("scenes.json") if x.get("id") == scid), None)
    if not scene:
        raise web.HTTPNotFound(text="No such scene")
    missing = [s for s in scene.get("shots", []) if not ((s.get("previs") or {}).get("grey") or (s.get("previs") or {}).get("lit"))]
    if missing:
        raise web.HTTPBadRequest(text=f"Render the 3D previews first ({len(missing)} shot(s) without one)")
    boards = _boards()
    b = next((x for x in boards if x.get("scene") == scid), None)
    if not b:
        b = {"id": uuid.uuid4().hex[:10], "name": scene.get("name") or "3D scene", "scene": scid, "shots": [],
             "private": bool(scene.get("private")), "created": time.time()}
        boards.append(b)
    for s in scene["shots"]:
        t = next((x for x in b["shots"] if x.get("sceneShot") == s["id"]), None)
        if not t:
            t = {"id": _new_id(), "prompt": s.get("prompt") or "", "image": "", "camera": "", "sceneShot": s["id"]}
            b["shots"].append(t)
        t["prompt"] = t.get("prompt") or s.get("prompt") or ""
        t["camera"] = "" if s.get("camera") else MOVE_WORDS.get(s.get("move") or "", "")
        t["engine"] = s.get("engine") or "keyframes"
        t["previs"] = s.get("previs")
        t["seconds"] = s.get("seconds")
        t["scene"] = scid
    b["updated"] = time.time()
    _save(boards)
    return b


def setup(ns):
    H.update(ns)
    routes = ns["routes"]
    _need_private = ns["_need_private"]

    def _local(request):
        if not ns["auth"].is_local(request):
            raise web.HTTPForbidden(text="Only from this PC")

    @routes.get("/manny/api/paths")
    async def paths(request):
        import folder_paths
        return web.json_response({"output": folder_paths.get_output_directory(), "input": folder_paths.get_input_directory(),
                                  "private": ns["_priv_root"]() if ns["auth"].unlocked(request) else None})

    @routes.post("/manny/api/scenes/{scid}/to_board")
    async def to_board(request):
        b = scene_to_board(request.match_info["scid"])
        return web.json_response({"id": b["id"], "name": b["name"], "shots": [{"id": s["id"], "prompt": s.get("prompt"), "engine": s.get("engine")} for s in b["shots"]]})

    @routes.post("/manny/api/boards/{bid}/shots/{sid}/attach")
    async def attach_route(request):
        bb = await request.json()
        _boards_, b, _s = _find(request.match_info["bid"], request.match_info["sid"])
        if b.get("private"):
            _need_private(request)
        if bb.get("path") or bb.get("url"):
            _local(request)
            info = await _import(bb, bool(b.get("private")))
            fn, sub = info["filename"], info["subfolder"]
        else:
            ns["_out_path"](bb["filename"], bb.get("subfolder", ""), bool(b.get("private")))   # must exist
            fn, sub = bb["filename"], bb.get("subfolder", "")
        s = attach(request.match_info["bid"], request.match_info["sid"], fn, sub, bb.get("as") or "take", bb.get("label") or "", bb.get("pick", True))
        return web.json_response({"ok": True, "filename": fn, "subfolder": sub, "takes": len(s.get("takes", [])), "pick": s.get("pick")})

    async def _import(bb, private):
        if bb.get("path"):
            p = os.path.abspath(bb["path"])
            if not os.path.isfile(p):
                raise web.HTTPBadRequest(text="No such file: " + p)
            return import_file(src_path=p, private=private)
        url = str(bb["url"])
        if not url.startswith("https://"):
            raise web.HTTPBadRequest(text="Only https links can be imported")
        async with ClientSession() as s:
            async with s.get(url, timeout=None) as r:
                if r.status != 200:
                    raise web.HTTPBadRequest(text=f"Download failed ({r.status})")
                data = await r.content.read(2 * 1024 ** 3 + 1)
                if len(data) > 2 * 1024 ** 3:
                    raise web.HTTPBadRequest(text="Too big (over 2 GB)")
        name = bb.get("name") or os.path.basename(url.split("?")[0]) or "import.mp4"
        return import_file(data=data, name=name, private=private)

    @routes.post("/manny/api/import")
    async def import_route(request):
        _local(request)
        bb = await request.json()
        private = bool(bb.get("private"))
        if private:
            _need_private(request)
        info = await _import(bb, private)
        return web.json_response(info)

    @routes.post("/manny/api/boards/{bid}/shots/{sid}/final_request")
    async def final_request(request):
        bb = await request.json()
        boards, b, s = _find(request.match_info["bid"], request.match_info["sid"])
        if b.get("private"):
            _need_private(request)
        engine = bb.get("engine")
        if engine in (None, "", "none"):
            s.pop("final_request", None)
        else:
            if engine not in ("local", "seedance"):
                raise web.HTTPBadRequest(text="engine: local or seedance")
            s["final_request"] = {"engine": engine, "notes": (bb.get("notes") or "")[:500], "status": "waiting", "at": time.time()}
        _save(boards)
        return web.json_response({"ok": True, "final_request": s.get("final_request")})

    @routes.get("/manny/api/final_requests")
    async def final_requests(request):
        """Shots waiting for a final render, with everything needed to make it (for Claude)."""
        import folder_paths
        show_private = ns["_flag"](request.query.get("private")) and ns["auth"].unlocked(request)
        out = []
        for b in _boards():
            if b.get("private") and not show_private:
                continue
            root = ns["_root"](bool(b.get("private")))
            for k, s in enumerate(b.get("shots", [])):
                fr = s.get("final_request")
                if not fr or fr.get("status") == "done":
                    continue
                o = s.get("output") or {}
                pv = (s.get("previs") or {}).get("lit") or (s.get("previs") or {}).get("grey") or {}
                ip = lambda v: os.path.join(folder_paths.get_input_directory(), v.replace("/", os.sep)) if v else None
                out.append({"board": b["id"], "board_name": b.get("name"), "shot": s["id"], "shot_number": k + 1, "engine": fr["engine"],
                            "notes": fr.get("notes"), "prompt": ". ".join(x for x in [s.get("prompt"), s.get("camera")] if x),
                            "seconds": s.get("seconds"),
                            "approved_take": os.path.join(root, o.get("subfolder", ""), o["filename"]) if o.get("filename") else None,
                            "first_frame": ip(s.get("image")), "previs_video": ip(pv.get("video_in")), "private": bool(b.get("private"))})
        return web.json_response(out)
