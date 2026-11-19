"""AI tools (Production › AI tools): Film Engine's Generators, moved into Studio.

AI Depth, AI Slow-Mo, AI Stems, AI Subtitles, AI Lossless Cut and One-Click Master, and any other tool built the same way.
Each tool is a command-line engine in its own folder, described by a generator.json manifest in Film Engine's format
(its name, what it accepts, whether it takes a section, the whole file or several pieces, its options). Studio's own
tools are in ComfyUI-Studio/ai_tools/; STUDIO_GENERATORS_DIR adds more folders (os.pathsep-separated), e.g. Film
Engine's engines folder on a Mac.

This module reads the manifests, checks that each tool can run here (the ComfyUI nodes and model file it uses, the
Python packages it imports: a tool that can't says why, and how to fix it), registers the file the user gives it (an
upload, or a file from the Gallery), runs one job at a time and reports progress from the JSON file every engine writes:
    { "stage", "done", "total", "out" | "outs", "error", "note", "stats", "pieces", "prompt_id" }
("done" and "error" are terminal stages; prompt_id is the ComfyUI job the engine is waiting for, stopped with it).

The engines that need the GPU queue their work on this ComfyUI, so it waits its turn and shows in the queue like any
render; the others run ffmpeg or a local model. Results land in ComfyUI's output folder under Generators/<tool>/, so
the videos and pictures show in the Gallery too. Nothing calls a paid service or leaves this PC.

Film Engine ran the engines on files where they lay (a desktop app); Studio is a web app used from other computers and
phones too, so a file is uploaded (streamed to the input folder, no size ceiling) or picked from the Gallery."""
import asyncio
import hashlib
import importlib.machinery
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

from aiohttp import web

H = {}
HERE = os.path.dirname(os.path.abspath(__file__))
BUILTIN = os.path.join(HERE, "ai_tools")             # Studio's own engines
POLL = 0.7
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".mxf", ".avi", ".webm", ".mts", ".m2ts"}
AUDIO_EXT = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".heic"}
SHOWABLE_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}        # the rest (TIFF, HEIC) get a PNG copy to show
PLAYABLE_VIDEO = {"h264", "hevc", "vp8", "vp9", "av1"}             # what a browser's player decodes; ProRes and the rest get a copy
PLAYABLE_AUDIO = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
TEXT_EXT = {".srt": "text/plain; charset=utf-8", ".vtt": "text/vtt; charset=utf-8", ".txt": "text/plain; charset=utf-8",
            ".json": "application/json"}

# where things live; setup() fills it from ComfyUI, tests call configure()
CFG = {"data": None, "input": None, "output": None, "temp": None, "cache": None, "comfy_url": None, "ffmpeg": None}


class GenError(Exception):
    """An answer for the page: status and a sentence that says what to do."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def configure(**kw):
    CFG.update({k: v for k, v in kw.items() if v is not None})


def _state_dir():
    d = os.path.join(CFG["data"], "generators")
    os.makedirs(d, exist_ok=True)
    return d


def ffmpeg():
    return CFG.get("ffmpeg") or os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")


# ---------------------------------------------------------------- tools
def tool_dirs():
    extra = [d for d in (os.environ.get("STUDIO_GENERATORS_DIR") or "").split(os.pathsep) if d.strip()]
    return [BUILTIN] + [os.path.abspath(os.path.expanduser(d)) for d in extra]


def list_tools():
    """Every tool folder with a generator.json, Studio's own first, in their order. Read fresh, so a new engine appears.
    A tool that can't run here is listed with `unavailable`: the reason, worded for the page."""
    tools, seen = [], set()
    for base in tool_dirs():
        try:
            dirs = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)) and not d.startswith((".", "_")))
        except OSError:
            continue
        found = []
        for d in dirs:
            folder = os.path.join(base, d)
            f = os.path.join(folder, "generator.json")
            if not os.path.isfile(f):
                continue
            try:
                with open(f, encoding="utf-8") as fh:
                    m = json.load(fh)
            except Exception as e:
                found.append({"id": d, "name": d, "dir": folder, "unavailable": f"generator.json is not valid JSON: {e}"})
                continue
            problem = validate_manifest(m, folder)
            if problem:
                found.append({"id": m.get("id") or d, "name": m.get("name") or d, "dir": folder, "unavailable": problem})
                continue
            t = dict(m, dir=folder, options=m.get("options") or [], builtin=base == BUILTIN)
            why = readiness(t)
            if why:
                t["unavailable"] = why
            found.append(t)
        found.sort(key=lambda t: (t.get("order", 999), str(t.get("name", "")).lower()))
        for t in found:
            if t["id"] in seen:          # the same tool in two folders: the first one (Studio's own) is used
                continue
            seen.add(t["id"])
            tools.append(t)
    return tools


def validate_manifest(m, folder):
    if not isinstance(m, dict):
        return "empty manifest"
    if not re.fullmatch(r"[a-z0-9-]+", str(m.get("id") or "")):
        return "manifest id must be lowercase letters, digits or dashes"
    if not m.get("script") or not os.path.isfile(os.path.join(folder, m["script"])):
        return f"engine script {m.get('script') or '(none)'} is missing"
    if m.get("python") and not os.path.isfile(os.path.join(folder, m["python"])):
        return f"its Python ({m['python']}) is not installed yet"
    if m.get("accepts", "video") not in ("video", "audio", "image"):
        return 'accepts must be "video", "audio" or "image"'
    if m.get("range") not in ("section", "whole", "segments"):
        return 'range must be "section", "whole" or "segments"'
    seg = m.get("segments")
    if m["range"] == "segments" and not (isinstance(seg, dict) and str(seg.get("flag", "")).startswith("--")):
        return 'a "segments" tool needs "segments": { "flag": "--..." }'
    out = m.get("out")
    if not isinstance(out, dict) or not out.get("flag") or out.get("kind") not in ("file", "dir"):
        return 'out needs a flag and kind "file" or "dir"'
    return None


def _extra_pythonpath():
    return [p for p in (os.environ.get("STUDIO_GENERATORS_PYTHONPATH") or "").split(os.pathsep) if p]


def _importable(mod):
    """Whether the engine's Python can import mod: ComfyUI's own packages, plus STUDIO_GENERATORS_PYTHONPATH (the sandbox's
    stand-ins for the local models). Nothing is imported to find out."""
    try:
        if importlib.machinery.PathFinder.find_spec(mod, _extra_pythonpath() or None):
            return True
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def find_model(spec):
    """The model file a tool uses: the first file in its ComfyUI models folder whose name matches, its default if it's there."""
    try:
        import folder_paths
        names = [n.replace("\\", "/") for n in folder_paths.get_filename_list(spec["folder"])]
    except Exception:
        return None
    hits = sorted(n for n in names if re.search(spec.get("match") or ".", n.rsplit("/", 1)[-1], re.I))
    if not hits:
        return None
    want = spec.get("default")
    prefer = [n for n in hits if n == want] or [n for n in hits if spec.get("prefer") and spec["prefer"].lower() in n.lower()]
    return (prefer or hits)[0]


def readiness(t):
    """Why this tool can't run on this PC (None when it can): its ComfyUI nodes, its model, its Python packages, ffmpeg."""
    needs = t.get("needs") or {}
    if needs.get("ffmpeg") and not ffmpeg():
        return "needs ffmpeg (put it on the PATH, or pip install imageio-ffmpeg into ComfyUI's Python)"
    nodes_needed = needs.get("comfy_nodes") or []
    if nodes_needed:
        try:
            import nodes
            have = nodes.NODE_CLASS_MAPPINGS
        except Exception:
            have = {}
        missing = [n for n in nodes_needed if n not in have]
        if missing:
            return f"this ComfyUI has no {', '.join(missing)} node{'s' if len(missing) > 1 else ''}: update ComfyUI"
    if needs.get("model"):
        spec = needs["model"]
        if not find_model(spec):
            eg = f" (for example {spec['default']})" if spec.get("default") else ""
            return f"needs {spec.get('label') or 'its model'} in models/{spec['folder']}{eg}"
    alts = needs.get("python_any") or []
    if alts and not any(_importable(m) for m in alts):
        how = needs.get("install") or f"pip install {alts[0]}"
        return f"needs {' or '.join(alts)} in ComfyUI's Python: {how}"
    return None


def get_tool(tid):
    t = next((x for x in list_tools() if x["id"] == tid), None)
    if not t:
        raise GenError(404, f"no AI tool called {tid}")
    if t.get("unavailable"):
        raise GenError(409, f"{t['name']} is not ready: {t['unavailable']}")
    return t


def public_tool(t):
    return {k: v for k, v in t.items() if k not in ("dir", "needs", "script", "python")}


def python_for(tool):
    """The Python a tool runs under: its own venv when the manifest names one, else ComfyUI's (which has av and numpy)."""
    if tool.get("python"):
        return os.path.join(tool["dir"], tool["python"])
    return sys.executable


# ---------------------------------------------------------------- the command line
def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool) or isinstance(v, float) and v.is_integer()


def segments_arg(tool, segments, duration):
    """A "segments" tool (Lossless Cut) takes several pieces of the file at once: [[start, end], ...] in seconds from
    the start, as the player counts. Checked here so the engine only ever sees sane numbers: at least one piece,
    at most the manifest's max (99), each ending after it starts, none starting past the end (an end past it
    means "to the end"). Passed as "--segments 12.000000-30.500000,41.000000-55.000000"."""
    mx = min(int((tool.get("segments") or {}).get("max") or 99), 999)
    if not isinstance(segments, list) or not segments:
        raise GenError(400, "add at least one piece (mark In and Out, then Add piece)")
    if len(segments) > mx:
        raise GenError(400, f"{tool['name']} takes at most {mx} pieces")
    out = []
    for i, seg in enumerate(segments):
        try:
            s, e = (float(seg[0]), float(seg[1])) if isinstance(seg, (list, tuple)) else (float(seg.get("start")), float(seg.get("end")))
        except (TypeError, ValueError, IndexError, AttributeError):
            raise GenError(400, f"piece {i + 1} needs a start and an end in seconds")
        if s != s or e != e or s in (float("inf"), float("-inf")) or e in (float("inf"), float("-inf")):
            raise GenError(400, f"piece {i + 1} needs a start and an end in seconds")
        if s < 0 or e <= s:
            raise GenError(400, f"piece {i + 1} must end after it starts")
        if duration and duration > 0 and s >= duration:
            raise GenError(400, f"piece {i + 1} starts after the end of the file")
        out.append(f"{s:.6f}-{e:.6f}")
    return ",".join(out)


def build_args(tool, input_path, out_path, progress_path, start=None, end=None, options=None, segments=None, duration=None):
    """The engine's arguments from the manifest and the user's choices. Unknown options are dropped and values are
    checked against the manifest, so the page can only ever pass what the tool declared."""
    args = [os.path.join(tool["dir"], tool["script"]), input_path]
    if tool["range"] == "section" and _is_int(start) and _is_int(end):
        start, end = int(start), int(end)
        if start < 0 or end <= start:
            raise GenError(400, "the section must end after it starts")
        args += ["--start", str(start), "--end", str(end)]
    if tool["range"] == "segments":
        args += [tool["segments"]["flag"], segments_arg(tool, segments, duration)]
    chosen = options if isinstance(options, dict) else {}
    by_name = {o["name"]: o for o in tool.get("options") or []}

    def value(o):
        return chosen[o["name"]] if o["name"] in chosen else o.get("default")

    # A setting that applies only to some choices ("when": {"output": ["dof"]}) is left out when they are not made,
    # judged on what will be sent (the choice made, else its default).
    def applies(o):
        return all(k in by_name and str(value(by_name[k])) in [str(v) for v in (vs or [])] for k, vs in (o.get("when") or {}).items())

    for opt in tool.get("options") or []:
        if not applies(opt):
            continue
        raw = value(opt)
        if raw is None or raw == "":
            continue
        label = opt.get("label") or opt["name"]
        kind = opt.get("type")
        if kind == "checkbox":
            if raw is True or raw == "true" or raw == 1:
                args.append(opt["flag"])
            continue
        if kind == "select":
            hit = next((c for c in opt.get("choices") or [] if str(c["value"]) == str(raw)), None)
            if not hit:
                raise GenError(400, f"{label}: {raw} is not one of its choices")
            args += [opt["flag"], str(hit["value"])]
            continue
        if kind in ("slider", "number"):
            try:
                n = float(raw)
            except (TypeError, ValueError):
                raise GenError(400, f"{label} must be a number")
            if n != n or n in (float("inf"), float("-inf")):
                raise GenError(400, f"{label} must be a number")
            lo = opt.get("min", float("-inf"))
            hi = opt.get("max", float("inf"))
            n = min(hi, max(lo, n))
            args += [opt["flag"], _num(n)]
            continue
        if kind == "points":
            if not re.fullmatch(r"(\s*[01]?\.?\d*,\s*[01]?\.?\d*,\s*[01]\s*;?)+", str(raw)):
                raise GenError(400, 'points must be "x,y,label;..." with x,y between 0 and 1')
            args += [opt["flag"], str(raw)]
            continue
        if kind == "text":
            args += [opt["flag"], str(raw)[:200]]
    if "frame" in chosen and _is_int(chosen["frame"]) and any(o.get("type") == "points" for o in tool.get("options") or []):
        args += ["--frame", str(int(chosen["frame"]))]
    args += [tool["out"]["flag"], out_path, "--progress", progress_path]
    return args


def _num(n):
    """A number as Film Engine's engines read it: 2 not 2.0, 0.3 not 0.30000000000000004."""
    return str(int(n)) if float(n).is_integer() else repr(round(float(n), 6))


# ---------------------------------------------------------------- inputs
_inputs = {}     # id -> {id, path, name, kind, size, fps, width, height, duration, frames, has_audio, codec, preview, source}


def probe_media(path, kind):
    """What a file is: size, frame rate, length, sound. Read with PyAV (ComfyUI's own), pictures with Pillow."""
    name = os.path.basename(path)
    if kind == "image":
        try:
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = None
            with Image.open(path) as im:
                w, h = im.size
                fmt = (im.format or "").lower()
            return {"width": w, "height": h, "fps": None, "duration": 0.0, "has_audio": False, "codec": fmt or None}
        except Exception:
            pass        # HEIC and the like: PyAV reads them as a one-frame video
    try:
        import av
        with av.open(path) as c:
            v = c.streams.video[0] if c.streams.video and kind != "audio" else None
            a = c.streams.audio[0] if c.streams.audio else None
            dur = float(c.duration / 1e6) if c.duration else 0.0
            if not dur:
                s = v or a
                if s is not None and s.duration and s.time_base:
                    dur = float(s.duration * s.time_base)
            fps = float(v.average_rate or v.guessed_rate or 0) if v is not None else None
            info = {"width": v.codec_context.width if v is not None else None, "height": v.codec_context.height if v is not None else None,
                    "fps": round(fps, 6) if fps else None, "duration": round(dur, 6), "has_audio": a is not None,
                    "codec": (v.codec_context.name if v is not None else a.codec_context.name if a is not None else None)}
    except Exception as e:
        raise GenError(422, f"{name} could not be read as a {kind} file ({str(e)[:120]})")
    if kind == "video" and not info["width"]:
        raise GenError(422, f"{name} has no picture")
    if kind == "audio" and not info["has_audio"]:
        raise GenError(422, f"{name} has no sound")
    if kind != "image" and not info["duration"]:
        raise GenError(422, f"{name} has no length ffmpeg can read")
    return info


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    return "video" if ext in VIDEO_EXT else "audio" if ext in AUDIO_EXT else "image" if ext in IMAGE_EXT else None


def needs_preview(path, info):
    ext = os.path.splitext(path)[1].lower()
    if ext in AUDIO_EXT:
        return ext not in PLAYABLE_AUDIO
    if ext in IMAGE_EXT:
        return ext not in SHOWABLE_IMAGE
    return str((info or {}).get("codec") or "").lower() not in PLAYABLE_VIDEO


def register(path, source=None):
    """Check a file and register it. Only registered inputs can be run or served."""
    load()
    if not isinstance(path, str) or not os.path.isabs(path):
        raise GenError(400, "a full file path is needed")
    try:
        st = os.stat(path)
    except OSError:
        raise GenError(404, f"there is no file at {os.path.basename(path) or path}")
    if not os.path.isfile(path):
        raise GenError(400, f"{os.path.basename(path)} is not a file")
    kind = kind_of(path)
    if not kind:
        raise GenError(400, f"{os.path.basename(path)} is not a video, audio or image file these tools know")
    info = probe_media(path, kind)
    rid = hashlib.sha1(f"{path}:{st.st_size}:{st.st_mtime}".encode("utf-8", "ignore")).hexdigest()[:16]
    fps = info["fps"]
    rec = {"id": rid, "path": path, "name": os.path.basename(path), "kind": kind, "size": st.st_size, "fps": fps,
           "width": info["width"], "height": info["height"], "duration": info["duration"],
           "frames": int(round(info["duration"] * fps)) if fps and kind == "video" else None, "has_audio": info["has_audio"],
           "codec": info["codec"], "preview": needs_preview(path, info), "source": source or {}}
    _inputs.pop(rid, None)
    _inputs[rid] = rec
    persist()
    return rec


def public_input(rec):
    return {k: v for k, v in rec.items() if k != "path"}


def get_input(rid):
    load()
    rec = _inputs.get(str(rid))
    if not rec:
        raise GenError(404, "that file is no longer registered; choose it again")
    if not os.path.isfile(rec["path"]):
        raise GenError(404, f"{rec['name']} has moved or been deleted")
    return rec


def _inside(root, rel):
    root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([full, root]) != root:
        raise GenError(400, "that file is outside Studio's folders")
    return full


def gallery_file(filename, subfolder=""):
    """A file of the Gallery (ComfyUI's output folder; not the private space)."""
    full = _inside(CFG["output"], os.path.join(subfolder or "", filename or ""))
    if not os.path.isfile(full):
        raise GenError(404, "that file is not in the Gallery any more")
    return full


def input_file(value):
    """A file of ComfyUI's input folder, as Studio's uploads name them ("studio/take.mp4"); never the private ones."""
    v = str(value or "").replace("\\", "/")
    if v.startswith("studio_private/"):
        raise GenError(403, "AI tools don't work on Incognito files yet")
    full = _inside(CFG["input"], v)
    if not os.path.isfile(full):
        raise GenError(404, "that file is not in the input folder any more")
    return full


def library(limit=400):
    """The files the picker offers: the output folder's videos, pictures and sounds (Music & voices' songs too), newest
    first. Not the private space, the trash, or hidden work folders."""
    root = CFG["output"]
    items = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith((".", "_trash"))]
        for fn in files:
            kind = kind_of(fn)
            if not kind or fn.startswith("."):
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            sub = os.path.relpath(dirpath, root).replace("\\", "/")
            items.append({"filename": fn, "subfolder": "" if sub == "." else sub, "kind": kind, "mtime": st.st_mtime, "size": st.st_size})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def upload_dir():
    d = os.path.join(CFG["input"], "studio", "ai-tools")
    os.makedirs(d, exist_ok=True)
    return d


def clean_name(name, fallback="upload"):
    base = os.path.basename(str(name or "").replace("\\", "/"))
    base = re.sub(r"[^\w .()+-]", "_", base).strip(" .")[:120]
    return base or fallback


# ---------------------------------------------------------------- previews
# The page's player is a browser's, and browsers can't decode ProRes (or DNxHD, CineForm...). Those get an H.264 copy
# for the PLAYER ONLY; the engines always read the original. The copy keeps every frame and the frame rate
# (passthrough), so the frame counter and the In / Out marks stay true to the original.
_previews = {}    # cache key -> asyncio.Future of the copy's path, so two requests share one conversion


def _preview_key(path):
    st = os.stat(path)
    return hashlib.sha1(f"{path}:{st.st_size}:{st.st_mtime}".encode("utf-8", "ignore")).hexdigest()[:16]


def make_preview(src, out):
    """An H.264 / AAC copy (a PNG for a TIFF or HEIC still) the browser can show. Blocking: run it in a thread."""
    ff = ffmpeg()
    if not ff:
        raise GenError(500, "ffmpeg is needed to preview this file")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    _prune_previews(os.path.dirname(out))
    tmp = re.sub(r"(\.\w+)$", r".part\1", out)
    kind = kind_of(src)
    head = [ff, "-nostdin", "-y", "-v", "error", "-i", src]
    if kind == "image":
        tries = [head + ["-vf", "scale='min(3840,iw)':-2", "-frames:v", "1", tmp]]
    elif kind == "audio":
        tries = [head + ["-vn", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp]]
    else:
        # full size up to 4K; a high frame rate (a 192 fps slow-mo) at 1920 wide, which every decoder plays smoothly
        info = probe_media(src, "video")
        width = 1920 if (info.get("fps") or 0) > 60 else 3840
        tries = [head + ["-map", "0:v:0", "-map", "0:a:0?", "-fps_mode", "passthrough", "-vf", f"scale='min({width},iw)':-2,format=yuv420p",
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp]]
    err = ""
    for cmd in tries:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        if r.returncode == 0:
            os.replace(tmp, out)
            return out
        err = (r.stderr or b"").decode("utf-8", "ignore").strip().splitlines()[-1:] or [f"ffmpeg exited {r.returncode}"]
        err = err[0]
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise GenError(500, f"could not make a playable preview: {err}")


def _prune_previews(d, keep=30):
    """Keep the newest previews; they are only copies and are made again on demand."""
    try:
        files = sorted((os.path.join(d, f) for f in os.listdir(d) if ".part." not in f), key=os.path.getmtime, reverse=True)
    except OSError:
        return
    for f in files[keep:]:
        try:
            os.remove(f)
        except OSError:
            pass


async def preview_file(path):
    """A path the page can play or show: the file itself when it can, else a cached copy (made once)."""
    kind = kind_of(path)
    info = await asyncio.to_thread(probe_media, path, kind) if kind == "video" else None
    if not needs_preview(path, info):
        return path
    key = _preview_key(path)
    ext = ".png" if kind == "image" else ".m4a" if kind == "audio" else ".mp4"
    out = os.path.join(CFG["cache"], "ai-tools", key + ext)
    if os.path.isfile(out):
        return out
    fut = _previews.get(key)
    if fut is None:
        fut = asyncio.ensure_future(asyncio.to_thread(make_preview, path, out))
        _previews[key] = fut
        fut.add_done_callback(lambda _f, k=key: _previews.pop(k, None))
    return await asyncio.shield(fut)


# ---------------------------------------------------------------- jobs
_jobs = {}       # id -> job record (persisted)
_procs = {}      # id -> Popen (this process only)
_loaded = {"v": False}


def _state_file():
    return os.path.join(_state_dir(), "state.json")


def load():
    if _loaded["v"]:
        return
    _loaded["v"] = True
    try:
        with open(_state_file(), encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        return          # the first run
    for i in s.get("inputs") or []:
        _inputs[i["id"]] = i
    for j in s.get("jobs") or []:
        # a job that was running when ComfyUI stopped has no process any more: say so rather than spin forever
        if j.get("status") in ("running", "queued"):
            j.update(status="interrupted", error="ComfyUI was stopped while this ran: run it again")
        _jobs[j["id"]] = j


def persist():
    try:
        tmp = _state_file() + ".tmp"
        recent = sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)[:200]
        keep = {"inputs": list(_inputs.values())[-200:], "jobs": [{k: v for k, v in j.items() if k not in ("_args", "_python")} for j in recent]}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(keep, f, indent=1)
        os.replace(tmp, _state_file())
    except Exception as e:          # state is a convenience; a failed write must not fail the job
        print("[Studio] AI tools: couldn't save their state:", e)


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def out_root():
    return os.path.join(CFG["output"], "Generators")


def _out_kind(p):
    ext = os.path.splitext(p)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    return "text" if ext in TEXT_EXT else "file"


def public_job(j):
    """The job as the page sees it: no command line or internal paths; outputs as Gallery-relative files."""
    out = {k: v for k, v in j.items() if not k.startswith("_")}
    root = os.path.realpath(CFG["output"])
    files = []
    for p in j.get("_outs") or []:
        rp = os.path.realpath(p)
        rel = os.path.relpath(rp, root).replace("\\", "/") if os.path.commonpath([rp, root]) == root else os.path.basename(p)
        sub, name = (rel.rsplit("/", 1) if "/" in rel else ("", rel))
        try:
            size = os.path.getsize(p)
        except OSError:
            size = None
        files.append({"name": name, "subfolder": sub, "kind": _out_kind(p), "size": size})
    out["outputs"] = files
    if j.get("pieces"):
        out["pieces"] = [dict(pc, file=os.path.basename(pc["file"]) if pc.get("file") else None) for pc in j["pieces"]]
    return out


def list_jobs():
    load()
    return [public_job(j) for j in sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)]


def get_job(jid):
    load()
    j = _jobs.get(str(jid))
    if not j:
        raise GenError(404, "no such AI tool job")
    return j


def _fits(tool, rec):
    """Refuse a file the tool can't take, in words."""
    acc, kind = tool.get("accepts", "video"), rec["kind"]
    if acc == "audio":
        if kind == "image":
            raise GenError(400, f"{tool['name']} needs a sound file or a video with sound, not a picture")
        if kind == "video" and not rec.get("has_audio"):
            raise GenError(400, f"{rec['name']} has no sound")
        return
    if acc == "video":
        # a tool that works on sound (stems, subtitles) says so with "audio_ok": it then takes a bare audio file too;
        # one that works on pictures (depth) says "image_ok"
        if kind == "audio" and not tool.get("audio_ok"):
            raise GenError(400, f"{tool['name']} needs a video")
        if kind == "image" and not tool.get("image_ok"):
            raise GenError(400, f"{tool['name']} needs a video, not a picture")
        if tool.get("audio_ok") and kind == "video" and not rec.get("has_audio") and tool.get("needs_sound"):
            raise GenError(400, f"{rec['name']} has no sound")
        return
    if kind != "image":
        raise GenError(400, f"{tool['name']} needs a picture")


def _unique(stem, ext):
    """An output path no file and no other job has: two runs in the same second (a second Generate, queued) get " (2)"."""
    taken = {j.get("_out_path") for j in _jobs.values()}
    path, k = stem + ext, 1
    while path in taken or os.path.exists(path):
        k += 1
        path = f"{stem} ({k}){ext}"
    return path


def create_job(body):
    """Queue a job: {tool, input_id, start?, end?, segments?, options?}. Starts at once if nothing else is running."""
    load()
    body = body if isinstance(body, dict) else {}
    tool = get_tool(str(body.get("tool") or ""))
    rec = get_input(body.get("input_id"))
    _fits(tool, rec)
    start, end = body.get("start"), body.get("end")
    section = tool["range"] == "section" and _is_int(start) and _is_int(end)
    jid = uuid.uuid4().hex[:12]
    base = re.sub(r"[/\\:*?\"<>|]", "-", os.path.splitext(rec["name"])[0])[:80] or "file"
    tname = re.sub(r"[/\\:*?\"<>|]", "-", tool["name"])
    suffix = f" f{int(start)}-{int(end)}" if section else ""
    ext = ""
    if tool["out"]["kind"] == "file":    # "ext_for": {"image": ".png"}: a picture in, a picture out
        ext = (tool["out"].get("ext_for") or {}).get(rec["kind"]) or tool["out"].get("ext", "")
    out_path = _unique(os.path.join(out_root(), tname, f"{base} - {tname}{suffix} {_stamp()}"), ext)
    progress = os.path.join(_state_dir(), "progress", f"{jid}.json")
    args = build_args(tool, rec["path"], out_path, progress, start if section else None, end if section else None,
                      body.get("options"), body.get("segments"), rec.get("duration"))     # checks everything before it's queued
    segments = None
    if tool["range"] == "segments":
        segments = [[float(x) for x in (s if isinstance(s, (list, tuple)) else (s["start"], s["end"]))] for s in body["segments"]]
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    job = {"id": jid, "tool": tool["id"], "tool_name": tool["name"], "input_id": rec["id"], "input_name": rec["name"],
           "input_kind": rec["kind"], "start": int(start) if section else None, "end": int(end) if section else None,
           "segments": segments, "options": options, "status": "queued", "stage": "Waiting for the GPU", "done": 0, "total": 0,
           "error": None, "note": None, "stats": None, "pieces": None,
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}", "started_at": None, "finished_at": None,
           "_outs": [], "_out_path": out_path, "_out_kind": tool["out"]["kind"], "_progress": progress,
           "_log": progress[:-5] + ".log", "_python": python_for(tool), "_args": args, "_model": None, "_prompt": None}
    spec = (tool.get("needs") or {}).get("model")
    if spec:
        job["_model"] = find_model(spec)
    _jobs[jid] = job
    persist()
    pump()
    return public_job(job)


def pump():
    """Start the oldest queued job when nothing is running: one at a time, they share the GPU."""
    if any(j["status"] == "running" for j in _jobs.values()):
        return
    waiting = sorted((j for j in _jobs.values() if j["status"] == "queued"), key=lambda j: j["created_at"])
    if waiting:
        _start(waiting[0])


def engine_env(job):
    """What an engine is told about this Studio: where ComfyUI is and its folders, ffmpeg, the model it uses."""
    env = dict(os.environ)
    extra = _extra_pythonpath()
    if extra:
        env["PYTHONPATH"] = os.pathsep.join(extra + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.update(PYTHONUNBUFFERED="1", STUDIO_COMFY_URL=CFG.get("comfy_url") or "", STUDIO_WORK_DIR=work_dir(job),
               STUDIO_COMFY_DIRS=json.dumps({"input": CFG["input"], "output": CFG["output"], "temp": CFG.get("temp") or CFG["output"]}),
               STUDIO_JOB_LABEL=f"{job['tool_name']} · {job['input_name']}", STUDIO_JOB_ID=job["id"])
    if ffmpeg():
        env["STUDIO_FFMPEG"] = ffmpeg()
    if job.get("_model"):
        env["STUDIO_MODEL"] = job["_model"]
    if os.name != "nt":
        env["PATH"] = os.pathsep.join(p for p in ("/usr/local/bin", "/opt/homebrew/bin", env.get("PATH") or "/usr/bin:/bin") if p)
    return env


def work_dir(job):
    """Where an engine keeps its intermediate files (its own, under Studio's data): removed when it ends, however it ends."""
    return os.path.join(_state_dir(), "work", job["id"])


def _clean_work(job):
    """An engine's leftovers once its process is gone: its work folder, and the work files it gave ComfyUI and ComfyUI
    saved for it (studio_engine names those folders after the job). A cancelled engine can't always tidy up itself:
    Windows stops it outright."""
    for d in (work_dir(job), os.path.join(CFG["input"], "studio", "ai-tools", f".ai-tools-{job['id']}"),
              os.path.join(CFG["output"], ".ai-tools", job["id"])):
        shutil.rmtree(d, ignore_errors=True)


def _start(job):
    os.makedirs(os.path.dirname(job["_progress"]), exist_ok=True)
    os.makedirs(job["_out_path"] if job["_out_kind"] == "dir" else os.path.dirname(job["_out_path"]), exist_ok=True)
    try:
        os.remove(job["_progress"])
    except OSError:
        pass
    job.update(status="running", stage="Starting", started_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    try:
        with open(job["_log"], "wb") as log:
            # its own process group, so Cancel stops the engine AND what it started (ffmpeg, a model)
            kw = {"creationflags": NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            proc = subprocess.Popen([job["_python"]] + job["_args"], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    env=engine_env(job), cwd=os.path.dirname(job["_args"][0]), **kw)
    except Exception as e:
        _finish(job, "failed", f"could not start the engine: {e}")
        return
    _procs[job["id"]] = proc
    persist()
    asyncio.ensure_future(_follow(job, proc))


async def _follow(job, proc):
    try:
        while proc.poll() is None:
            await asyncio.sleep(POLL)
            read_progress(job)
    finally:
        _procs.pop(job["id"], None)
        await asyncio.to_thread(_clean_work, job)
    read_progress(job)
    if job["status"] != "running":        # cancelled, or already failed
        if job["status"] == "cancelled":
            _drop_files(job, keep_log=False)
        return
    if job["stage"] == "done":
        return _finish(job, "done")
    tail = _tail(job["_log"])
    code = proc.returncode
    why = job.get("error") or (f"the engine stopped (signal {-code})" if code and code < 0 else f"the engine exited with code {code}")
    _finish(job, "failed", why + (f": {tail}" if tail and not job.get("error") else ""))


def read_progress(job):
    try:
        with open(job["_progress"], encoding="utf-8") as f:
            p = json.load(f)
    except Exception:
        return
    if not isinstance(p, dict):
        return
    job["stage"] = str(p.get("stage") or job["stage"])[:200]
    job["done"] = _nz(p.get("done"))
    job["total"] = _nz(p.get("total"))
    if p.get("error"):
        job["error"] = str(p["error"])[:1000]
    if p.get("note"):
        job["note"] = str(p["note"])[:300]         # e.g. Subtitles: "no speech found"
    if isinstance(p.get("stats"), dict):           # an engine's measured results (mastering: LUFS, true peak); flat values only
        job["stats"] = dict(list((k, v) for k, v in p["stats"].items() if isinstance(v, (str, int, float, bool)))[:12])
    outs = p.get("outs") if isinstance(p.get("outs"), list) else [p["out"]] if p.get("out") else []
    if outs:
        job["_outs"] = [str(x) for x in outs]
    if isinstance(p.get("pieces"), list):
        job["pieces"] = [c for c in (clean_piece(x) for x in p["pieces"][:999]) if c]
    job["_prompt"] = str(p["prompt_id"]) if p.get("prompt_id") else None


def _nz(v):
    try:
        n = float(v)
        return int(n) if n.is_integer() else n
    except (TypeError, ValueError):
        return 0


def clean_piece(pc):
    """What a multi-piece engine (Lossless Cut) says about each piece it wrote: where it really starts and ends (a keyframe
    cut can start earlier than asked), how it was made, and which stretches, if any, were re-encoded. Only these fields,
    as numbers and short text, reach the page."""
    if not isinstance(pc, dict):
        return None

    def num(v):
        try:
            n = float(v)
            return n if n == n and abs(n) != float("inf") else None
        except (TypeError, ValueError):
            return None

    def span(r):
        return [float(r[0]), float(r[1])] if isinstance(r, (list, tuple)) and len(r) == 2 and all(num(x) is not None for x in r) else None

    n = num(pc.get("n"))
    return {"n": int(n) if n is not None and n.is_integer() else n, "file": pc["file"] if isinstance(pc.get("file"), str) else None,
            "asked_start": num(pc.get("asked_start")), "asked_end": num(pc.get("asked_end")), "start": num(pc.get("start")),
            "end": num(pc.get("end")), "mode": pc["mode"] if pc.get("mode") in ("copy", "smart") else None,
            "reencoded": [s for s in (span(r) for r in (pc.get("reencoded") or [])[:4] if isinstance(pc.get("reencoded"), list)) if s],
            "note": pc["note"][:300] if isinstance(pc.get("note"), str) else ""}


def _tail(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return " ".join(f.read().strip().splitlines()[-3:])[-400:]
    except OSError:
        return ""


def _list_dir(d):
    try:
        return sorted(os.path.join(d, f) for f in os.listdir(d) if not f.startswith("."))
    except OSError:
        return []


def _finish(job, status, error=None):
    if status == "done" and not job["_outs"]:
        job["_outs"] = _list_dir(job["_out_path"]) if job["_out_kind"] == "dir" else [job["_out_path"]] if os.path.isfile(job["_out_path"]) else []
    if status == "done":
        job["_outs"] = [p for p in job["_outs"] if os.path.isfile(p)]
    job.update(status=status, error=error or None, finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
               stage="done" if status == "done" else job["stage"], _prompt=None)
    _drop_files(job, keep_log=status == "failed")      # a failed engine's log stays, for whoever looks into it
    persist()
    pump()


def _drop_files(job, keep_log):
    for p in (job["_progress"],) + (() if keep_log else (job["_log"],)):
        try:
            os.remove(p)
        except OSError:
            pass


def _kill(proc):
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def _kill_hard(proc):
    if proc.poll() is None and os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass


async def _stop_comfy(prompt_id):
    """The ComfyUI job an engine was waiting for: taken off the queue, or stopped if it's rendering."""
    if not prompt_id or not CFG.get("comfy_url"):
        return
    try:
        from aiohttp import ClientSession
        async with ClientSession() as s:
            async with s.post(CFG["comfy_url"] + "/queue", json={"delete": [prompt_id]}) as r:
                await r.read()
            async with s.get(CFG["comfy_url"] + "/queue") as r:
                q = await r.json()
            if any(len(x) > 1 and x[1] == prompt_id for x in q.get("queue_running") or []):
                async with s.post(CFG["comfy_url"] + "/interrupt", json={"prompt_id": prompt_id}) as r:
                    await r.read()
    except Exception as e:
        print("[Studio] AI tools: couldn't stop the ComfyUI job:", e)


async def cancel_job(jid):
    job = get_job(jid)
    if job["status"] == "queued":
        _finish(job, "cancelled")
        return public_job(job)
    if job["status"] != "running":
        return public_job(job)
    read_progress(job)
    prompt = job.get("_prompt")
    proc = _procs.get(job["id"])
    job.update(status="cancelled", finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"), _prompt=None)
    persist()
    if proc:
        _kill(proc)
        asyncio.get_running_loop().call_later(3, _kill_hard, proc)
    await _stop_comfy(prompt)
    pump()
    return public_job(job)


def output_path(jid, n):
    """One of a finished job's outputs, by index: the only output paths ever served."""
    job = get_job(jid)
    try:
        p = job["_outs"][int(n)]
    except (ValueError, IndexError):
        p = None
    if not p or not os.path.isfile(p):
        raise GenError(404, "that output is not there any more")
    return p


def reset():
    """For tests: forget everything this process knows (the state file stays)."""
    _jobs.clear()
    _inputs.clear()
    _procs.clear()
    _loaded["v"] = False


# ---------------------------------------------------------------- routes
def _mime(p):
    ext = os.path.splitext(p)[1].lower()
    return TEXT_EXT.get(ext) or mimetypes.guess_type(p)[0] or "application/octet-stream"


def _file(p):
    return web.FileResponse(p, headers={"Content-Type": _mime(p), "Cache-Control": "no-store"})


def _answer(fn):
    async def wrapped(request):
        try:
            return await fn(request)
        except GenError as e:
            return web.json_response({"error": str(e)}, status=e.status)
    return wrapped


async def _json(request):
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def setup(ns):
    H.update(ns)
    import folder_paths
    configure(data=ns["_data_dir"](), input=folder_paths.get_input_directory(), output=folder_paths.get_output_directory(),
              temp=folder_paths.get_temp_directory(), cache=ns["_cache_dir"](False), ffmpeg=os.environ.get("FFMPEG_PATH") or ns["_ffmpeg"]())
    CFG["comfy_url"] = f"http://127.0.0.1:{ns['_port']()}"
    routes = ns["routes"]
    P = "/studio/api/generators"

    @routes.get(P + "/tools")
    @_answer
    async def tools(request):
        CFG["comfy_url"] = f"http://127.0.0.1:{ns['_port']()}"
        ts = await asyncio.to_thread(list_tools)
        return web.json_response({"tools": [public_tool(t) for t in ts], "out_dir": "Generators"})

    @routes.get(P + "/files")
    @_answer
    async def files(request):
        return web.json_response({"files": await asyncio.to_thread(library)})

    @routes.post(P + "/inputs")
    @_answer
    async def inputs_add(request):
        """{filename, subfolder}: a Gallery file, or {input}: a file of ComfyUI's input folder (Studio's uploads)."""
        b = await _json(request)
        if b.get("private"):
            raise GenError(403, "AI tools don't work on Incognito files yet")
        if b.get("filename"):
            full = gallery_file(b["filename"], b.get("subfolder") or "")
            src = {"filename": b["filename"], "subfolder": b.get("subfolder") or ""}
        elif b.get("input"):
            full, src = input_file(b["input"]), {"input": b["input"]}
        else:
            raise GenError(400, "choose a file: a Gallery item or an upload")
        rec = await asyncio.to_thread(register, full, src)
        return web.json_response({"input": public_input(rec)})

    @routes.post(P + "/inputs/import")
    @_answer
    async def inputs_import(request):
        """The file itself as the body (any size: streamed to the input folder), its name in X-File-Name."""
        from urllib.parse import unquote
        name = clean_name(unquote(request.headers.get("X-File-Name") or ""))     # sent URI-encoded: a header can't hold any name
        if not kind_of(name):
            raise GenError(400, f"{name} is not a video, audio or image file these tools know")
        stem, ext = os.path.splitext(name)
        d = upload_dir()
        dest = os.path.join(d, name)
        k = 1
        while os.path.exists(dest):
            dest = os.path.join(d, f"{stem} ({k}){ext}")
            k += 1
        tmp = dest + ".part"
        size = 0
        try:
            with open(tmp, "wb") as f:
                async for chunk in request.content.iter_chunked(1 << 20):
                    f.write(chunk)
                    size += len(chunk)
            if not size:
                raise GenError(400, "the file was empty")
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        rel = os.path.relpath(dest, CFG["input"]).replace("\\", "/")
        try:
            rec = await asyncio.to_thread(register, dest, {"input": rel})
        except GenError:
            os.remove(dest)          # not a file the tools can read: don't keep it
            raise
        return web.json_response({"input": public_input(rec)})

    @routes.get(P + "/inputs/{id}")
    @_answer
    async def inputs_get(request):
        return _file(get_input(request.match_info["id"])["path"])

    @routes.get(P + "/inputs/{id}/info")
    @_answer
    async def inputs_info(request):
        return web.json_response({"input": public_input(get_input(request.match_info["id"]))})

    @routes.get(P + "/inputs/{id}/preview")
    @_answer
    async def inputs_preview(request):
        return _file(await preview_file(get_input(request.match_info["id"])["path"]))

    @routes.get(P + "/jobs")
    @_answer
    async def jobs_list(request):
        return web.json_response({"jobs": list_jobs()})

    @routes.post(P + "/jobs")
    @_answer
    async def jobs_add(request):
        return web.json_response({"job": create_job(await _json(request))}, status=202)

    @routes.get(P + "/jobs/{id}")
    @_answer
    async def jobs_one(request):
        return web.json_response({"job": public_job(get_job(request.match_info["id"]))})

    @routes.post(P + "/jobs/{id}/cancel")
    @_answer
    async def jobs_cancel(request):
        return web.json_response({"job": await cancel_job(request.match_info["id"])})

    @routes.get(P + "/jobs/{id}/output/{n}")
    @_answer
    async def jobs_output(request):
        return _file(output_path(request.match_info["id"], request.match_info["n"]))

    @routes.get(P + "/jobs/{id}/output/{n}/preview")
    @_answer
    async def jobs_output_preview(request):
        return _file(await preview_file(output_path(request.match_info["id"], request.match_info["n"])))

    try:
        load()
    except Exception as e:
        print("[Studio] AI tools: couldn't read their state:", e)

