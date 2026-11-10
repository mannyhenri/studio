"""Topaz upscalers for Studio: Gigapixel (pictures) and Topaz Video (video upscale and frame rate).

Both run as local command-line tools that ship with the desktop apps (your Topaz login is used).
- Gigapixel: gigapixel.exe -i <file> -o <folder> --scale N -m <model>
- Topaz Video: its own ffmpeg.exe with the tvai_up (upscale) and tvai_fi (frame interpolation) filters
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import time

from aiohttp import web

H = {}   # names from __init__ (set in setup)

PF = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"
PD = os.environ.get("ProgramData") or r"C:\ProgramData"

VIDEO_MODELS = [
    ("prob-4", "Proteus (all-round, recommended)"),
    ("rhea-1", "Rhea (most detail)"),
    ("iris-3", "Iris (faces)"),
    ("ahq-12", "Artemis (clean, smooth)"),
    ("nyx-3", "Nyx (remove noise)"),
]
FPS_MODELS = [("apo-8", "Apollo (smooth, recommended)"), ("chr-2", "Chronos (fast)")]
IMAGE_MODELS = [
    ("std", "Standard (recommended)"), ("hf", "High fidelity"), ("low", "Low resolution source"),
    ("art", "Art & CG"), ("text", "Text & shapes"), ("lines", "Lines"), ("vc", "Very compressed"),
]
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_status = {"at": 0, "data": None}


# ---------------------------------------------------------------- paths and settings
def _first(*paths):
    return next((p for p in paths if p and os.path.isfile(p)), None)


def video_dir():
    for d in (os.environ.get("TOPAZ_VIDEO_DIR"), os.path.join(PF, "Topaz Labs LLC", "Topaz Video"), os.path.join(PF, "Topaz Labs LLC", "Topaz Video AI")):
        if d and os.path.isfile(os.path.join(d, "ffmpeg.exe")):
            return d
    return None


def gigapixel_exe():
    return _first(os.environ.get("TOPAZ_GIGAPIXEL"),
                  os.path.join(PF, "Topaz Labs LLC", "Topaz Gigapixel", "gigapixel.exe"),
                  os.path.join(PF, "Topaz Labs LLC", "Topaz Gigapixel AI", "gigapixel.exe"))


def gui_exe(kind):
    """The desktop apps, for the hand-off (open a file in the app, you export it)."""
    if kind == "image":
        return _first(os.environ.get("TOPAZ_GIGAPIXEL_APP"), os.path.join(PF, "Topaz Labs LLC", "Topaz Gigapixel", "Topaz Gigapixel.exe"),
                      os.path.join(PF, "Topaz Labs LLC", "Topaz Gigapixel AI", "Topaz Gigapixel AI.exe"))
    vd = video_dir()
    return _first(*(os.path.join(vd, n) for n in ("Topaz Video.exe", "Topaz Video AI.exe"))) if vd else None


def export_dir(kind):
    """Where the app should export to: straight into the gallery's Upscaled folder."""
    d = os.path.join(H["_root"](False), "Images" if kind == "image" else "Videos", "Upscaled")
    os.makedirs(d, exist_ok=True)
    return d


def _video_env():
    env = dict(os.environ)
    for name in ("Topaz Video", "Topaz Video AI"):
        md = os.path.join(PD, "Topaz Labs LLC", name, "models")
        if os.path.isdir(md):
            env.setdefault("TVAI_MODEL_DIR", md)
            env.setdefault("TVAI_MODEL_DATA_DIR", md)
            break
    return env


def _prefs_file():
    return os.path.join(H["_data_dir"](), "topaz.json")


DEFAULTS = {"use": True, "video_model": "prob-4", "fps_model": "apo-8", "image_model": "std", "device": "-2", "image_format": "png"}


def prefs():
    p = dict(DEFAULTS)
    try:
        with open(_prefs_file(), encoding="utf-8") as f:
            p.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except Exception:
        pass
    return p


def _run(cmd, env=None, timeout=60):
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout or b"").decode("utf-8", "ignore") + (r.stderr or b"").decode("utf-8", "ignore")


def probe(force=False):
    """What's installed and working. Cached for 10 minutes."""
    if _status["data"] and not force and time.time() - _status["at"] < 600:
        return _status["data"]
    out = {"video": {"found": False}, "image": {"found": False}}
    out["apps"] = {k: {"exe": gui_exe(k), "export_to": export_dir(k)} for k in ("image", "video")}
    vd = video_dir()
    if vd:
        ff = os.path.join(vd, "ffmpeg.exe")
        v = {"found": True, "dir": vd, "ffmpeg": ff}
        try:
            _c, txt = _run([ff, "-hide_banner", "-filters"], env=_video_env(), timeout=40)
            v["upscale"] = "tvai_up" in txt
            v["fps"] = "tvai_fi" in txt
            _c, enc = _run([ff, "-hide_banner", "-encoders"], env=_video_env(), timeout=40)
            v["encoders"] = [e for e in ("prores_ks", "ffv1", "h264_qsv", "hevc_qsv", "libx264") if re.search(r"\s" + e + r"\s", enc)]
            v["ready"] = bool(v["upscale"])
            if not v["ready"]:
                v["error"] = "Topaz Video's ffmpeg doesn't list the tvai filters. " + txt.strip()[-300:]
        except Exception as e:
            v.update(ready=False, error=str(e))
        out["video"] = v
    gx = gigapixel_exe()
    if gx:
        g = {"found": True, "exe": gx}
        try:
            code, txt = _run([gx, "--version"], timeout=40)
            g["version"] = txt.strip().splitlines()[-1][:120] if txt.strip() else ""
            g["ready"] = code == 0 or bool(re.search(r"\d+\.\d+", txt))
            if not g["ready"]:
                g["error"] = txt.strip()[-300:] or f"gigapixel exited with {code}"
        except Exception as e:
            g.update(ready=False, error=str(e))
        out["image"] = g
    _status.update(at=time.time(), data=out)
    return out


def video_ready():
    return bool(probe()["video"].get("ready")) and prefs()["use"]


def image_ready():
    return bool(probe()["image"].get("ready")) and prefs()["use"]


# ---------------------------------------------------------------- video
def target_size(w, h, size):
    """'1080' or '4k': the short side becomes 1080 or 2160, even numbers. None if already that big."""
    short = {"1080": 1080, "1440": 1440, "4k": 2160}.get(str(size).lower())
    if not short or min(w, h) >= short - 8:
        return None
    f = short / min(w, h)
    return int(round(w * f / 2)) * 2, int(round(h * f / 2)) * 2


def video_filter(w, h, size, fps, p, minimal=False):
    parts = []
    dev = str(p.get("device", "-2"))
    ts = target_size(w, h, size) if size else None
    if ts:
        W, H2 = ts
        m = p.get("video_model") or "prob-4"
        if minimal or m.startswith(("rhea", "nyx", "ahq")):
            parts.append(f"tvai_up=model={m}:scale=0:w={W}:h={H2}:device={dev}:vram=1:instances=1")
        else:
            parts.append(f"tvai_up=model={m}:scale=0:w={W}:h={H2}:preblur=0:noise=0:details=0:halo=0:blur=0:compression=0:estimate=8:blend=0.2:device={dev}:vram=1:instances=1")
        parts.append(f"scale=w={W}:h={H2}:flags=lanczos")
    if str(fps) in ("48", "60"):
        fm = p.get("fps_model") or "apo-8"
        parts.append(f"tvai_fi=model={fm}:fps={fps}:device={dev}:vram=1:instances=1" if minimal else
                     f"tvai_fi=model={fm}:slowmo=1:rdt=0.01:fps={fps}:device={dev}:vram=1:instances=1")
    return ",".join(parts), ts


async def run_video(job, src, size, fps, dst_dir, name):
    """Topaz Video pass, then Studio's ffmpeg makes a light H.264 file with the original sound.
    Returns the final path."""
    st = probe()["video"]
    if not st.get("ready"):
        raise RuntimeError(st.get("error") or "Topaz Video isn't installed")
    p = prefs()
    w, h, has_a, dur = await asyncio.to_thread(H["_probe"], src)
    vf, ts = video_filter(w, h, size, fps, p)
    if not vf:
        raise RuntimeError("Nothing to do: the clip is already that size")
    tmp_dir = os.path.join(dst_dir, ".topaz_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp = os.path.join(tmp_dir, f"{job['id']}.mov")
    enc = st.get("encoders") or []
    codec = ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"] if "prores_ks" in enc or not enc else ["-c:v", "ffv1", "-pix_fmt", "yuv420p"]
    src_fps = await asyncio.to_thread(_fps_of, src)
    total = max(1, int(dur * (int(fps) if str(fps) in ("48", "60") else src_fps)))
    what = " and ".join(x for x in [f"upscaling to {ts[0]}×{ts[1]}" if ts else "", f"{fps} fps" if str(fps) in ("48", "60") else ""] if x)
    job["stage"] = "Topaz: " + what
    try:
        for attempt in (0, 1):
            if attempt:
                vf, ts = video_filter(w, h, size, fps, p, minimal=True)
                job["stage"] = "Topaz: retrying with simpler settings"
            cmd = [os.path.join(st["dir"], "ffmpeg.exe"), "-hide_banner", "-nostdin", "-y", "-i", src, "-filter_complex", vf, "-an", *codec, tmp]
            code, log = await _ff_progress(cmd, job, total, "Topaz: " + what)
            if code == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                break
            if job.get("cancel"):
                raise RuntimeError("Stopped")
            if attempt or not re.search(r"Option .* not found|Error applying option|Invalid argument", log):
                raise RuntimeError("Topaz Video: " + _explain(log))
        job["stage"] = "Saving"
        os.makedirs(dst_dir, exist_ok=True)
        out = os.path.join(dst_dir, f"{name} {time.strftime('%Y-%m-%d %H%M%S')}.mp4")
        cmd = [H["_ffmpeg"](), "-y", "-i", tmp, "-i", src, "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "libx264", "-crf", "16", "-preset", "medium",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out]
        await H["_ff"](cmd)
        return out
    finally:
        for x in (tmp, tmp_dir):
            try:
                os.remove(x) if x == tmp else os.rmdir(x)
            except Exception:
                pass


def _fps_of(path):
    import av
    with av.open(path) as c:
        r = c.streams.video[0].average_rate
        return float(r) if r else 24.0


async def _ff_progress(cmd, job, total, label):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                                                env=_video_env(), creationflags=NO_WINDOW)
    job["proc"] = proc
    tail = ""
    t0 = time.time()
    while True:
        if job.get("cancel") and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            chunk = await asyncio.wait_for(proc.stderr.read(4096), 2)
        except asyncio.TimeoutError:
            continue
        if not chunk:
            break
        tail = (tail + chunk.decode("utf-8", "ignore"))[-6000:]
        m = re.findall(r"frame=\s*(\d+)", tail)
        if m:
            n = int(m[-1])
            pct = min(99, int(n * 100 / total))
            el = time.time() - t0
            eta = f" · about {int((el / max(n, 1)) * (total - n) / 60) + 1} min left" if n > 5 else ""
            job["stage"] = f"{label} · {pct}%{eta}"
            job["progress"] = pct / 100
    await proc.wait()
    job.pop("proc", None)
    return proc.returncode, tail


def _explain(log):
    low = log.lower()
    if "login" in low or "authenticat" in low or "unauthori" in low:
        return "you're not signed in. Open Topaz Video once and sign in, then try again."
    if "model" in low and ("download" in low or "not found" in low or "missing" in low):
        return "the AI model isn't downloaded yet. Open Topaz Video, run a short preview with this model once, then try again."
    lines = [ln for ln in log.replace("\r", "\n").splitlines() if ln.strip() and not ln.strip().startswith("frame=")]
    return " ".join(lines[-4:])[-500:]


# ---------------------------------------------------------------- pictures
def image_scale(w, h, size):
    """'2x', '4x', '2k', '4k' (long edge 2048 / 3840) → scale factor for Gigapixel."""
    s = str(size).lower()
    if s.endswith("x"):
        return float(s[:-1])
    edge = {"2k": 2048, "4k": 3840, "6k": 6144, "8k": 7680}.get(s, 3840)
    return round(max(1.0, min(16.0, edge / max(w, h))), 2)


async def run_image(job, src, size, model, dst_dir, name, fmt=None):
    st = probe()["image"]
    if not st.get("ready"):
        raise RuntimeError(st.get("error") or "Topaz Gigapixel isn't installed")
    p = prefs()
    from PIL import Image
    with Image.open(src) as im:
        w, h = im.size
    sc = image_scale(w, h, size)
    if sc <= 1.05:
        raise RuntimeError(f"This picture is already {w}×{h}, bigger than {str(size).upper()}. Pick 2× or a bigger size.")
    fmt = fmt or p.get("image_format") or "png"
    tmp = os.path.join(dst_dir, ".topaz_tmp", job["id"])
    os.makedirs(tmp, exist_ok=True)
    job["stage"] = f"Gigapixel: {w}×{h} → {int(w * sc)}×{int(h * sc)}"
    cmd = [st["exe"], "-i", src, "-o", tmp, "--scale", f"{sc:g}", "-m", model or p["image_model"], "-f", fmt, "--overwrite"]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, creationflags=NO_WINDOW)
        job["proc"] = proc
        log = ""
        while True:
            if job.get("cancel") and proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(2048), 2)
            except asyncio.TimeoutError:
                continue
            if not chunk:
                break
            log = (log + chunk.decode("utf-8", "ignore"))[-4000:]
            m = re.findall(r"(\d{1,3})\s*%", log)
            if m:
                job["stage"] = f"Gigapixel: {min(99, int(m[-1]))}%"
        await proc.wait()
        job.pop("proc", None)
        files = [os.path.join(tmp, f) for f in os.listdir(tmp)]
        files = [f for f in files if os.path.isfile(f)]
        if proc.returncode != 0 or not files:
            if job.get("cancel"):
                raise RuntimeError("Stopped")
            raise RuntimeError("Gigapixel: " + (_explain(log) if log.strip() else f"exited with code {proc.returncode}"))
        got = max(files, key=os.path.getsize)
        os.makedirs(dst_dir, exist_ok=True)
        out = os.path.join(dst_dir, f"{name} {time.strftime('%Y-%m-%d %H%M%S')}{os.path.splitext(got)[1].lower()}")
        shutil.move(got, out)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            os.rmdir(os.path.dirname(tmp))
        except Exception:
            pass


# ---------------------------------------------------------------- routes
def _clean(n):
    return "".join(ch for ch in n if ch.isalnum() or ch in " -_").strip()[:80] or "Upscaled"


def setup(ns):
    H.update(ns)
    routes = ns["routes"]
    _new_job, _need_private, _out_path, _root, _jobs = ns["_new_job"], ns["_need_private"], ns["_out_path"], ns["_root"], ns["_jobs"]

    @routes.get("/manny/api/topaz/status")
    async def tz_status(request):
        data = await asyncio.to_thread(probe, request.query.get("force") == "1")
        return web.json_response({**data, "prefs": prefs(), "video_models": VIDEO_MODELS, "fps_models": FPS_MODELS, "image_models": IMAGE_MODELS})

    @routes.post("/manny/api/topaz/prefs")
    async def tz_prefs(request):
        b = await request.json()
        p = prefs()
        p.update({k: (bool(v) if k == "use" else str(v)) for k, v in b.items() if k in DEFAULTS})
        with open(_prefs_file(), "w", encoding="utf-8") as f:
            json.dump(p, f, indent=1)
        return web.json_response(p)

    async def _start(request, kind):
        b = await request.json()
        private = bool(b.get("private"))
        if private:
            _need_private(request)
        src = _out_path(b["filename"], b.get("subfolder", ""), private)
        job = _new_job(private, kind="topaz-" + kind)
        base = _clean(b.get("name") or os.path.splitext(os.path.basename(src))[0])

        async def run():
            try:
                if kind == "video":
                    out = await run_video(job, src, b.get("size") or "1080", b.get("fps") or "keep",
                                          os.path.join(_root(private), "Videos", "Upscaled"), base + " topaz")
                    sub = "Videos/Upscaled"
                    try:
                        ns["_start_proxy"](out, private)
                    except Exception:
                        pass
                else:
                    out = await run_image(job, src, b.get("size") or "4k", b.get("model"),
                                          os.path.join(_root(private), "Images", "Upscaled"), base + " topaz", b.get("format"))
                    sub = "Images/Upscaled"
                job.update(status="done", stage="Done", result={"filename": os.path.basename(out), "subfolder": sub, "private": private})
            except Exception as e:
                job.update(status="failed", stage="Failed", error=str(e))
        asyncio.ensure_future(run())
        return web.json_response({"id": job["id"]})

    @routes.post("/manny/api/topaz/video")
    async def tz_video(request):
        return await _start(request, "video")

    @routes.post("/manny/api/topaz/image")
    async def tz_image(request):
        return await _start(request, "image")

    @routes.post("/manny/api/topaz/open")
    async def tz_open(request):
        """Opens pictures or clips in the Topaz desktop app. You pick the settings and export;
        exports to the gallery's Upscaled folder show up in Studio by themselves."""
        b = await request.json()
        items = b.get("items") or [b]
        if any(i.get("private") for i in items):
            raise web.HTTPBadRequest(text="Incognito files can't go to the Topaz app (its exports would land in the normal gallery)")
        paths = [_out_path(i["filename"], i.get("subfolder", "")) for i in items]
        vids = [x for x in paths if os.path.splitext(x)[1].lower() in (".mp4", ".mov", ".webm", ".mkv", ".avi")]
        kind = "video" if vids and len(vids) == len(paths) else "image"
        if vids and len(vids) != len(paths):
            raise web.HTTPBadRequest(text="Send pictures and clips separately")
        exe = gui_exe(kind)
        if not exe:
            raise web.HTTPBadRequest(text=("Topaz Gigapixel" if kind == "image" else "Topaz Video") + " isn't installed")
        flags = (0x00000008 | 0x00000200) if os.name == "nt" else 0   # DETACHED_PROCESS | NEW_PROCESS_GROUP
        # start the app *in* the gallery's Upscaled folder: apps that default their export folder to the
        # working directory then save straight into Studio (instead of ComfyUI's python folder)
        subprocess.Popen([exe, *paths], cwd=export_dir(kind), creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return web.json_response({"ok": True, "app": os.path.basename(exe), "count": len(paths), "export_to": export_dir(kind)})

    @routes.get("/manny/api/topaz/jobs")
    async def tz_jobs(request):
        """Running Topaz jobs, for the bottom-left activity card."""
        out = [{k: j.get(k) for k in ("id", "kind", "status", "stage", "progress")} for j in _jobs.values()
               if str(j.get("kind", "")).startswith("topaz") and j.get("status") == "running" and not j.get("private")]
        return web.json_response(out)
