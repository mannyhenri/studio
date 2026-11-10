"""Studio 3D: sets (.blend files), scenes (cameras, moves, cast), Blender renders and footage helpers.

Blender runs in the background (blender -b ... -P blender/studio_scene.py), never with its window.
Sets live as .blend files in Documents/Studio Sets (Claude can build them there through the
Higgsfield Bridge and register them with the Studio connector), plus any .blend you point at.
"""
import asyncio
import glob
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from aiohttp import web

import folder_paths

H = {}          # helpers from __init__ (set in setup)
_lock = threading.Lock()
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender", "studio_scene.py")


# ------------------------------------------------------------------ storage
def _file(name):
    return os.path.join(H["_data_dir"](), name)


def _load(name):
    try:
        with open(_file(name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(name, data):
    with _lock:
        tmp = _file(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, _file(name))


def sets_folder():
    d = os.path.join(os.path.expanduser("~"), "Documents", "Studio Sets")
    os.makedirs(d, exist_ok=True)
    return d


def _set_cache(sid):
    d = os.path.join(H["_data_dir"](), "sets", sid)
    os.makedirs(d, exist_ok=True)
    return d


def blender_exe():
    env = os.environ.get("STUDIO_BLENDER")
    if env and os.path.isfile(env):
        return env
    cands = sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe"), reverse=True)
    if cands:
        return cands[0]
    return shutil.which("blender")


def _visible(items, request, private_q):
    unlocked = H["auth"].unlocked(request)
    if private_q and not unlocked:
        raise web.HTTPForbidden(text="Incognito is locked")
    return [x for x in items if bool(x.get("private")) == bool(private_q)]


# ------------------------------------------------------------------ running Blender
async def run_blender(spec, job, blend=None, expect_frames=0):
    exe = blender_exe()
    if not exe:
        raise RuntimeError("Blender isn't installed (looked in Program Files\\Blender Foundation)")
    tmp = tempfile.mkdtemp(prefix="studio_bl_")
    spec = dict(spec)
    spec.setdefault("out", os.path.join(tmp, "out"))
    sp = os.path.join(tmp, "spec.json")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(spec, f)
    cmd = [exe, "-b"] + ([blend] if blend else []) + ["--factory-startup", "-P", SCRIPT, "--", sp]
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                creationflags=flags)
    tail, done = [], 0
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        t = line.decode("utf-8", "ignore").rstrip()
        tail = (tail + [t])[-40:]
        if "Saved:" in t and "f_" in t:
            done += 1
            if expect_frames:
                job["stage"] = f"Blender: frame {done} of {expect_frames}"
                job["progress"] = done / expect_frames
        elif t.startswith("[studio] render"):
            job["stage"] = "Blender: " + t[9:]
        if job.get("cancel"):
            proc.kill()
            raise RuntimeError("Stopped")
    await proc.wait()
    rp = os.path.join(spec["out"], "result.json")
    if not os.path.isfile(rp):
        raise RuntimeError("Blender stopped: " + " | ".join(tail[-6:])[-600:])
    res = json.load(open(rp, encoding="utf-8"))
    if not res.get("ok"):
        raise RuntimeError("Blender: " + str(res.get("error")))
    res["_tmp"] = tmp
    res["_out"] = spec["out"]
    return res


def _register_set(entry, res):
    cache = _set_cache(entry["id"])
    if res.get("plan"):
        shutil.copy2(os.path.join(res["_out"], res["plan"]["file"]), os.path.join(cache, "plan.png"))
    entry.update({k: res.get(k) for k in ("cameras", "markers", "frame_start", "frame_end", "fps", "bounds", "scene", "active_camera")})
    entry["plan"] = res.get("plan")
    entry["updated"] = time.time()
    sets = [s for s in _load("sets.json") if s["id"] != entry["id"]] + [entry]
    _save("sets.json", sets)
    shutil.rmtree(res["_tmp"], ignore_errors=True)
    return entry


def _resolve_blend(v):
    v = str(v or "").strip().strip('"')
    if v.startswith("input:"):
        v = H["_input_path"](v[6:])
    full = os.path.realpath(os.path.expanduser(v))
    if not full.lower().endswith(".blend") or not os.path.isfile(full):
        raise web.HTTPBadRequest(text="That .blend file wasn't found")
    return full


# ------------------------------------------------------------------ routes
def setup(ns):
    H.update(ns)
    routes = ns["routes"]
    _flag, _new_job, _ai_job, _need_private = ns["_flag"], ns["_new_job"], ns["_ai_job"], ns["_need_private"]

    @routes.get("/manny/api/blender/status")
    async def bl_status(request):
        exe = blender_exe()
        return web.json_response({"found": bool(exe), "path": exe, "sets_folder": sets_folder()})

    # ---- sets
    @routes.get("/manny/api/sets")
    async def sets_list(request):
        return web.json_response(_visible(_load("sets.json"), request, _flag(request.query.get("private"))))

    @routes.get("/manny/api/sets/{sid}/plan.png")
    async def set_plan(request):
        p = os.path.join(_set_cache(request.match_info["sid"].replace("/", "").replace("\\", "")), "plan.png")
        if not os.path.isfile(p):
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Cache-Control": "no-cache"})

    @routes.post("/manny/api/sets/add")
    async def sets_add(request):
        """Register a .blend (path on this PC, or an uploaded file) as a set. Used by the app and by Claude."""
        b = await request.json()
        private = bool(b.get("private"))
        if private:
            _need_private(request)
        blend = _resolve_blend(b.get("blend"))
        old = next((s for s in _load("sets.json") if os.path.normcase(s.get("blend", "")) == os.path.normcase(blend)), None)
        entry = old or {"id": uuid.uuid4().hex[:10], "at": time.time()}
        entry.update({"name": (b.get("name") or entry.get("name") or os.path.splitext(os.path.basename(blend))[0])[:80],
                      "blend": blend, "notes": (b.get("notes") or entry.get("notes") or "")[:2000],
                      "location": b.get("location") or entry.get("location"), "private": private,
                      "source": b.get("source") or entry.get("source") or "blend"})
        job = _new_job(private)

        async def run():
            job["stage"] = "Reading the set in Blender"
            res = await run_blender({"mode": "inspect"}, job, blend=blend)
            return _register_set(entry, res)
        asyncio.ensure_future(_ai_job(job, run))
        return web.json_response({"id": job["id"], "set": entry["id"]})

    @routes.post("/manny/api/sets/build")
    async def sets_build(request):
        """A quick blocking set made by Studio (floor, walls, backdrop picture, boxes), saved as a .blend."""
        b = await request.json()
        private = bool(b.get("private"))
        if private:
            _need_private(request)
        st = dict(b.get("set") or {})
        if st.get("backdrop"):
            st["backdrop"] = H["_input_path"](st["backdrop"])
        name = "".join(ch for ch in (b.get("name") or "Set") if ch.isalnum() or ch in " -_").strip() or "Set"
        path = os.path.join(sets_folder(), name + ".blend")
        k = 2
        while os.path.exists(path):
            path = os.path.join(sets_folder(), f"{name} {k}.blend")
            k += 1
        entry = {"id": uuid.uuid4().hex[:10], "at": time.time(), "name": name, "blend": path, "notes": "", "private": private,
                 "source": "studio", "location": b.get("location"), "recipe": b.get("set")}
        job = _new_job(private)

        async def run():
            job["stage"] = "Building the set in Blender"
            res = await run_blender({"mode": "build", "set": st, "save": path}, job)
            return _register_set(entry, res)
        asyncio.ensure_future(_ai_job(job, run))
        return web.json_response({"id": job["id"], "set": entry["id"]})

    @routes.post("/manny/api/sets/{sid}/refresh")
    async def sets_refresh(request):
        sid = request.match_info["sid"]
        entry = next((s for s in _load("sets.json") if s["id"] == sid), None)
        if not entry:
            raise web.HTTPNotFound()
        if entry.get("private"):
            _need_private(request)
        job = _new_job(bool(entry.get("private")))

        async def run():
            res = await run_blender({"mode": "inspect"}, job, blend=entry["blend"])
            return _register_set(entry, res)
        asyncio.ensure_future(_ai_job(job, run))
        return web.json_response({"id": job["id"]})

    @routes.delete("/manny/api/sets/{sid}")
    async def sets_delete(request):
        """Removes the set from Studio's list. The .blend file itself is never deleted."""
        sid = request.match_info["sid"]
        _save("sets.json", [s for s in _load("sets.json") if s["id"] != sid])
        return web.json_response({"ok": True})

    # ---- locations (pictures of places, used for sets and as references)
    @routes.get("/manny/api/locations")
    async def loc_list(request):
        return web.json_response(_visible(_load("locations.json"), request, _flag(request.query.get("private"))))

    @routes.post("/manny/api/locations")
    async def loc_save(request):
        b = await request.json()
        if b.get("private"):
            _need_private(request)
        locs = _load("locations.json")
        b["id"] = b.get("id") or uuid.uuid4().hex[:10]
        b["pictures"] = [p for p in (b.get("pictures") or []) if isinstance(p, str)][:12]
        locs = [x for x in locs if x["id"] != b["id"]] + [{k: b.get(k) for k in ("id", "name", "description", "pictures", "private")}]
        _save("locations.json", locs)
        return web.json_response({"id": b["id"]})

    @routes.delete("/manny/api/locations/{lid}")
    async def loc_delete(request):
        _save("locations.json", [x for x in _load("locations.json") if x["id"] != request.match_info["lid"]])
        return web.json_response({"ok": True})

    # ---- scenes
    @routes.get("/manny/api/scenes")
    async def sc_list(request):
        return web.json_response(_visible(_load("scenes.json"), request, _flag(request.query.get("private"))))

    @routes.post("/manny/api/scenes")
    async def sc_save(request):
        b = await request.json()
        if b.get("private"):
            _need_private(request)
        b["id"] = b.get("id") or uuid.uuid4().hex[:10]
        b["updated"] = time.time()
        _save("scenes.json", [x for x in _load("scenes.json") if x["id"] != b["id"]] + [b])
        return web.json_response({"id": b["id"]})

    @routes.delete("/manny/api/scenes/{scid}")
    async def sc_delete(request):
        _save("scenes.json", [x for x in _load("scenes.json") if x["id"] != request.match_info["scid"]])
        return web.json_response({"ok": True})

    @routes.post("/manny/api/scenes/{scid}/render")
    async def sc_render(request):
        """Renders the scene's shots in Blender: "grey" blocking (fast) or "lit" (the set's own look)."""
        scid = request.match_info["scid"]
        b = await request.json()
        sc = next((x for x in _load("scenes.json") if x["id"] == scid), None)
        if not sc:
            raise web.HTTPNotFound()
        private = bool(sc.get("private"))
        if private:
            _need_private(request)
        st = next((s for s in _load("sets.json") if s["id"] == sc.get("set")), None)
        if not st:
            raise web.HTTPBadRequest(text="This scene has no set")
        look = "lit" if b.get("look") == "lit" else "grey"
        only = set(b.get("shots") or [])
        size = b.get("size") or [864, 480]
        fps = 24
        shots = []
        cast = sc.get("cast") or []
        for sh in sc.get("shots") or []:
            if only and sh["id"] not in only:
                continue
            spec = {"id": sh["id"], "seconds": float(sh.get("seconds") or 5)}
            if sh.get("camera"):
                spec.update(camera=sh["camera"], start=sh.get("start"), end=sh.get("end"))
            else:
                spec.update(cam=sh["cam"], move=sh.get("move") or "static", amount=sh.get("amount") or 1)
            walks = sh.get("walks") or {}
            spec["cast"] = [dict(c, to=walks.get(c["id"])) for c in cast if c.get("id") not in (sh.get("hide") or [])]
            shots.append(spec)
        if not shots:
            raise web.HTTPBadRequest(text="No shots to render")
        job = _new_job(private)
        total = sum(int(s["seconds"] * fps) + 1 for s in shots)

        async def run():
            job["stage"] = "Starting Blender"
            res = await run_blender({"mode": "render", "look": look, "size": size, "fps": fps, "shots": shots}, job,
                                    blend=st["blend"], expect_frames=total)
            ff = H["_ffmpeg"]()
            name = "".join(ch for ch in sc.get("name", "Scene") if ch.isalnum() or ch in " -_").strip() or "Scene"
            out_dir = os.path.join(H["_root"](private), "Videos", "Previs", name)
            os.makedirs(out_dir, exist_ok=True)
            in_sub = (H["PRIV_INPUT"] if private else "manny") + "/previs"
            in_dir = os.path.join(folder_paths.get_input_directory(), in_sub)
            os.makedirs(in_dir, exist_ok=True)
            stamp = time.strftime("%H%M%S")
            done = {}
            for r in res.get("shots", []):
                if r.get("error"):
                    continue
                job["stage"] = "Making clip " + r["id"]
                frames = sorted(f for f in os.listdir(r["dir"]) if f.startswith("f_") and f.endswith(".png"))
                if not frames:
                    continue
                base = f"{name} {r['id']} {look} {stamp}"
                mp4 = os.path.join(out_dir, base + ".mp4")
                await H["_ff"]([ff, "-y", "-framerate", str(r["fps"]), "-start_number", str(r.get("start", 1)), "-i", os.path.join(r["dir"], "f_%04d.png"),
                                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4])
                shutil.copy2(mp4, os.path.join(in_dir, base + ".mp4"))
                shutil.copy2(os.path.join(r["dir"], frames[0]), os.path.join(in_dir, base + " first.png"))
                shutil.copy2(os.path.join(r["dir"], frames[-1]), os.path.join(in_dir, base + " last.png"))
                done[r["id"]] = {"video": {"filename": os.path.basename(mp4), "subfolder": f"Videos/Previs/{name}"},
                                 "video_in": f"{in_sub}/{base}.mp4", "first": f"{in_sub}/{base} first.png",
                                 "last": f"{in_sub}/{base} last.png", "secs": (len(frames) - 1) / r["fps"], "at": time.time()}
            shutil.rmtree(res["_tmp"], ignore_errors=True)
            scenes = _load("scenes.json")
            cur = next((x for x in scenes if x["id"] == scid), None)
            if cur:
                for sh in cur.get("shots") or []:
                    if sh["id"] in done:
                        sh.setdefault("previs", {})[look] = done[sh["id"]]
                _save("scenes.json", scenes)
            return {"shots": done, "look": look}
        asyncio.ensure_future(_ai_job(job, run))
        return web.json_response({"id": job["id"]})

    # ---- turn a 3D still into a film frame with the Edit app
    @routes.post("/manny/api/previs/style")
    async def pv_style(request):
        b = await request.json()
        private = bool(b.get("private"))
        if private:
            _need_private(request)
        still = H["_input_path"](b["still"])
        ref = H["_input_path"](b["ref"]) if b.get("ref") else None
        job = _new_job(private)

        async def run():
            app = H["_app_by_name"]("edit")
            vals = {}
            pic = next(i for i in app["inputs"] if i["kind"] == "image")
            vals[pic["key"]] = b["still"]
            imgs = [i for i in app["inputs"] if i["kind"] == "image"]
            tog = next((i for i in app["inputs"] if i["kind"] == "toggle" and "second" in i["label"].lower()), None)
            if ref and len(imgs) > 1:
                vals[imgs[1]["key"]] = b["ref"]
                if tog:
                    vals[tog["key"]] = True
            elif tog:
                vals[tog["key"]] = False
            txt = next(i for i in app["inputs"] if i["kind"] == "text")
            vals[txt["key"]] = b.get("prompt") or ""
            mdl = next((i for i in app["inputs"] if i["kind"] == "choice"), None)
            if mdl and b.get("model"):
                want = next((o for o in mdl["options"] if b["model"].lower() in o.lower()), None)
                if want:
                    vals[mdl["key"]] = want
            unc = next((i for i in app["inputs"] if i["kind"] == "toggle" and "uncensored" in i["label"].lower()), None)
            if unc:
                vals[unc["key"]] = bool(b.get("uncensored"))
            seed = next((i for i in app["inputs"] if i["kind"] == "seed"), None)
            if seed:
                vals[seed["key"]] = int(time.time()) % 2_000_000_000
            job["stage"] = "Painting the frame"
            pid = await H["_queue_app"](app, vals, private, "3D frame")
            job["pids"].append(pid)
            fn, sub = await H["_wait_output"](pid, job)
            full = await asyncio.to_thread(H["_find_out"], fn, sub, private)
            return {"value": H["_to_input"](full, private), "output": {"filename": os.path.basename(full), "subfolder": sub.replace(H["INCOMING"], "").strip("/")}}
        asyncio.ensure_future(_ai_job(job, run))
        return web.json_response({"id": job["id"]})


def inject_motion(api, value, class_types=("MiniMaxH3ReferenceToVideo",)):
    """Adds a reference video (the 3D render) as <Video 1> of MiniMax H3 reference-to-video."""
    if not value:
        return api
    targets = [nid for nid, n in api.items() if n.get("class_type") in class_types]
    if not targets:
        return api
    api["motion_load"] = {"class_type": "LoadVideo", "inputs": {"file": value}, "_meta": {"title": "3D motion"}}
    api["motion_parts"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["motion_load", 0]}, "_meta": {"title": "3D motion frames"}}
    for nid in targets:
        ins = api[nid]["inputs"]
        k = 0
        while f"ref_videos.ref_video_{k}" in ins:
            k += 1
        ins[f"ref_videos.ref_video_{k}"] = ["motion_parts", 0]
    return api
