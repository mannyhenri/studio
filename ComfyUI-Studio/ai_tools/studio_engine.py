"""What Studio's AI tool engines share: Film Engine's engine command line, the progress file Studio reads, ffmpeg, reading
a file with PyAV, and ComfyUI (for the tools that render on the GPU).

An engine runs as

    python engine.py <input> [--start F --end F | --segments S-E,S-E] [its options] --out <file or folder> --progress <file>

and tells Studio how it's going by rewriting the progress file (JSON, replaced atomically):

    {"stage": "Interpolating frames", "done": 3, "total": 10, "out": "..." | "outs": [...], "note": "...", "stats": {...},
     "pieces": [...], "prompt_id": "<the ComfyUI job it's waiting for>"}

The stages "done" and "error" (with "error": "why") end it. Studio says where things are in the environment:
STUDIO_COMFY_URL and STUDIO_COMFY_DIRS (ComfyUI and its input/output folders), STUDIO_FFMPEG, STUDIO_MODEL (the model file
the tool's manifest asked for) and STUDIO_JOB_LABEL (how its ComfyUI jobs are named in the queue)."""
import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import uuid

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
WORK_PREFIX = ".ai-tools"          # ComfyUI saves the tools' intermediate renders here (a hidden folder: not in the Gallery)


class EngineError(Exception):
    """Something the user should read: it becomes the job's error."""


# ---------------------------------------------------------------- the command line and the progress file
def parser(description):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("input")
    ap.add_argument("--out", required=True)
    ap.add_argument("--progress", required=True)
    return ap


class Progress:
    def __init__(self, path):
        self.path = path
        self.state = {"stage": "Starting", "done": 0, "total": 0}
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def __call__(self, stage=None, done=None, total=None, **extra):
        if stage is not None:
            self.state["stage"] = stage
        if done is not None:
            self.state["done"] = done
        if total is not None:
            self.state["total"] = total
        for k, v in extra.items():
            if v is None:
                self.state.pop(k, None)
            else:
                self.state[k] = v
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.path)

    def finish(self, outs, **extra):
        self(stage="done", done=self.state.get("total") or 1, total=self.state.get("total") or 1, outs=list(outs), prompt_id=None, **extra)


def _stopped(*_):
    raise KeyboardInterrupt()


@contextlib.contextmanager
def engine(progress):
    """Runs an engine's work: any failure ends it with the error on the job (and the traceback in its log). Cancel (a
    SIGTERM) ends it like Ctrl+C, so its clean-ups still run."""
    if os.name != "nt":
        signal.signal(signal.SIGTERM, _stopped)
    try:
        yield
    except EngineError as e:
        progress(stage="error", error=str(e), prompt_id=None)
        sys.exit(3)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        traceback.print_exc()
        progress(stage="error", error=f"{type(e).__name__}: {e}"[:900], prompt_id=None)
        sys.exit(3)


@contextlib.contextmanager
def workdir(tag):
    """A folder for intermediate files: in the one Studio gave this job (STUDIO_WORK_DIR, removed by Studio when the job
    ends, however it ends), else the system's temp folder."""
    base = os.environ.get("STUDIO_WORK_DIR") or None
    if base:
        os.makedirs(base, exist_ok=True)
    d = tempfile.mkdtemp(prefix=f"studio-{tag}-", dir=base)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- ffmpeg
def ffmpeg():
    ff = os.environ.get("STUDIO_FFMPEG") or os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
    if not ff:
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ff = None
    if not ff:
        raise EngineError("ffmpeg was not found on this PC")
    return ff


def ff(args, what="ffmpeg", duration=None, on_progress=None, loglevel="error"):
    """Run ffmpeg; on_progress(fraction) follows it when the length of what it writes is known. Returns its log (stderr)."""
    cmd = [ffmpeg(), "-nostdin", "-hide_banner", "-y", "-v", loglevel]
    if on_progress and duration:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += [str(a) for a in args]
    with tempfile.TemporaryFile() as err:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if on_progress and duration else subprocess.DEVNULL,
                             stderr=err, creationflags=NO_WINDOW)
        if on_progress and duration:
            for line in p.stdout:
                m = re.match(rb"out_time_(?:us|ms)=(\d+)", line)
                if m:
                    try:
                        on_progress(min(1.0, int(m.group(1)) / 1e6 / duration))
                    except Exception:
                        pass
        code = p.wait()
        err.seek(0)
        log = err.read().decode("utf-8", "replace")
    if code != 0:
        tail = [x for x in log.strip().splitlines() if x.strip()][-3:]
        raise EngineError(f"{what} failed: " + (" ".join(tail)[-500:] or f"ffmpeg exited {code}"))
    return log


def probe(path):
    """What a file holds, read with PyAV: kind, length, the picture (size, frame rate, codec) and the sound."""
    import av
    ext = os.path.splitext(path)[1].lower()
    try:
        with av.open(path) as c:
            v = c.streams.video[0] if c.streams.video else None
            a = c.streams.audio[0] if c.streams.audio else None
            dur = float(c.duration / 1e6) if c.duration else 0.0
            if not dur:
                s = v or a
                if s is not None and s.duration and s.time_base:
                    dur = float(s.duration * s.time_base)
            fps = float(v.average_rate or v.guessed_rate or 0) if v is not None else 0.0
            still = v is not None and (ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".heic") or (v.frames or 0) == 1 and not a)
            info = {"path": path, "kind": "image" if still else "video" if v is not None else "audio" if a is not None else None,
                    "duration": dur, "fps": fps, "width": v.codec_context.width if v is not None else 0,
                    "height": v.codec_context.height if v is not None else 0, "vcodec": v.codec_context.name if v is not None else None,
                    "pix_fmt": (v.codec_context.pix_fmt if v is not None else None), "has_audio": a is not None,
                    "acodec": a.codec_context.name if a is not None else None, "sample_rate": a.codec_context.sample_rate if a is not None else 0,
                    "channels": a.codec_context.channels if a is not None else 0, "audio_tracks": len(c.streams.audio)}
    except EngineError:
        raise
    except Exception as e:
        raise EngineError(f"{os.path.basename(path)} could not be read: {e}")
    if info["kind"] is None:
        raise EngineError(f"{os.path.basename(path)} has no picture or sound")
    info["frames"] = int(round(info["duration"] * info["fps"])) if info["kind"] == "video" and info["fps"] else 0
    return info


def count_frames(path):
    """How many frames a video really holds: the container's count, else counted by decoding."""
    import av
    with av.open(path) as c:
        v = c.streams.video[0]
        if v.frames:
            return int(v.frames)
        return sum(1 for _ in c.decode(v))


def keyframes(path):
    """The times (seconds from the start of the file) of the first video stream's keyframes, read from the packets
    without decoding anything."""
    import av
    out = []
    with av.open(path) as c:
        s = c.streams.video[0]
        start = float(s.start_time * s.time_base) if s.start_time is not None else 0.0
        for pkt in c.demux(s):
            if pkt.is_keyframe and pkt.pts is not None:
                out.append(round(float(pkt.pts * s.time_base) - start, 6))
    return sorted(set(out))


def scene_cuts(path, threshold, fps):
    """The frames (from 0) where a new shot starts, found by ffmpeg's scene-change score (0..1; a cut scores high, and a
    lower threshold finds more cuts). Read from the metadata filter's report."""
    cmd = [ffmpeg(), "-nostdin", "-hide_banner", "-v", "error", "-i", path, "-an",
           "-vf", f"select='gt(scene,{float(threshold):.3f})',metadata=print:file=-", "-f", "null", "-"]
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise EngineError("Finding the cuts failed: " + r.stderr.decode("utf-8", "replace").strip()[-300:])
    cuts = set()
    for m in re.finditer(rb"pts_time:([0-9.]+)", r.stdout):
        f = int(round(float(m.group(1)) * fps))
        if f > 0:
            cuts.add(f)
    return sorted(cuts)


def runs_between(n, cuts):
    """Frames 0..n-1 split at each cut: [(first, end_exclusive), ...]."""
    edges = [0] + [c for c in sorted(set(cuts)) if 0 < c < n] + [n]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b > a]


def chunks(a, b, size):
    """A run of frames [a, b) in pieces of at most `size` frames that share their edge frame, so interpolation between
    the pieces is never lost: [(a, a+size), (a+size-1, ...)] as (first, end_exclusive)."""
    out, s = [], a
    while True:
        e = min(b, s + size)
        out.append((s, e))
        if e >= b:
            return out
        s = e - 1


def fmt_clock(t):
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:06.3f}" if h >= 1 else f"{int(m)}:{s:06.3f}"


# ---------------------------------------------------------------- ComfyUI
def model():
    """The model file Studio chose for this tool (its manifest's "needs.model": the first match in that models folder)."""
    m = os.environ.get("STUDIO_MODEL")
    if not m:
        raise EngineError("Studio didn't say which model to use: run the tool from Studio (Production › AI tools)")
    return m


class Comfy:
    """This Studio's ComfyUI, for the tools that render on the GPU: a job waits its turn in ComfyUI's queue with the other
    renders (named after the tool in Studio's queue), and its result file is read from the output folder."""

    CLIENT = "studio-generators"

    def __init__(self, progress):
        self.url = (os.environ.get("STUDIO_COMFY_URL") or "").rstrip("/")
        if not self.url:
            raise EngineError("this tool renders through ComfyUI: run it from Studio (Production › AI tools)")
        self.dirs = json.loads(os.environ.get("STUDIO_COMFY_DIRS") or "{}")
        if not self.dirs.get("input") or not self.dirs.get("output"):
            raise EngineError("Studio didn't say where ComfyUI's folders are")
        self.progress = progress
        self.label = os.environ.get("STUDIO_JOB_LABEL") or "AI tool"
        self.tag = (os.environ.get("STUDIO_JOB_ID") or uuid.uuid4().hex[:12])
        self.trash = []

    def _req(self, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}

    def put(self, path):
        """A work file in ComfyUI's input folder (a hard link when it can be), named as LoadVideo / LoadImage take it."""
        sub = f"studio/ai-tools/{WORK_PREFIX}-{self.tag}"
        d = os.path.join(self.dirs["input"], *sub.split("/"))
        os.makedirs(d, exist_ok=True)
        name = f"{uuid.uuid4().hex[:8]}{os.path.splitext(path)[1].lower()}"
        dest = os.path.join(d, name)
        try:
            os.link(path, dest)
        except OSError:
            shutil.copy2(path, dest)
        self.trash.append(dest)
        return f"{sub}/{name}"

    def prefix(self, what):
        return f"{WORK_PREFIX}/{self.tag}/{what}"

    def run(self, api, stage, label_part=""):
        """Queue api, wait for it, and return the paths of the files it saved. A refusal or a failure raises, in words."""
        extra = {"studio_label": self.label + (f" ({label_part})" if label_part else ""), "studio_app": "AI tools"}
        try:
            r = self._req("/prompt", {"prompt": api, "client_id": self.CLIENT, "extra_data": extra})
        except urllib.error.HTTPError as e:
            raise EngineError("ComfyUI refused the job: " + _refusal(e))
        except urllib.error.URLError as e:
            raise EngineError(f"ComfyUI isn't answering ({e.reason})")
        if r.get("node_errors"):
            raise EngineError("ComfyUI refused part of the job: " + json.dumps(r["node_errors"])[:400])
        pid = r["prompt_id"]
        self.progress(stage=stage, prompt_id=pid)
        seen_running = False
        while True:
            h = self._req(f"/history/{pid}").get(pid)
            if h:
                st = h.get("status") or {}
                if st.get("status_str") == "error":
                    msgs = [m[1].get("exception_message", "") for m in st.get("messages", []) if m and m[0] == "execution_error"]
                    interrupted = any(m and m[0] == "execution_interrupted" for m in st.get("messages", []))
                    raise EngineError("The render was stopped in ComfyUI" if interrupted else "The render failed in ComfyUI: " + " ".join(msgs)[:400])
                files = []
                for o in (h.get("outputs") or {}).values():
                    for f in (o.get("images") or []) + (o.get("gifs") or []) + (o.get("videos") or []) + (o.get("audio") or []):
                        base = self.dirs.get(f.get("type") or "output") or self.dirs["output"]
                        p = os.path.join(base, *(f.get("subfolder") or "").split("/"), f["filename"])
                        if os.path.isfile(p):
                            files.append(p)
                            self.trash.append(p)
                if files:
                    self.progress(prompt_id=None)
                    return files
                if st.get("completed") or st.get("status_str") == "success":
                    raise EngineError("The render finished without a file")
            else:
                try:
                    q = self._req("/queue")
                    running = any(len(x) > 1 and x[1] == pid for x in q.get("queue_running") or [])
                    ahead = sum(1 for x in q.get("queue_pending") or [] if len(x) > 1 and x[1] != pid)
                    if running and not seen_running:
                        seen_running = True
                        self.progress(stage=stage)
                    elif not running and not seen_running:
                        self.progress(stage=f"Waiting in ComfyUI's queue ({ahead} ahead)" if ahead else "Waiting in ComfyUI's queue")
                except Exception:
                    pass
            time.sleep(1.0)

    def cleanup(self):
        for p in self.trash:
            try:
                os.remove(p)
            except OSError:
                pass
        for p in {os.path.dirname(x) for x in self.trash}:
            with contextlib.suppress(OSError):
                os.rmdir(p)


def _refusal(e):
    try:
        body = json.loads(e.read())
        msg = (body.get("error") or {}).get("message") or str(e)
        details = []
        for nid, ne in (body.get("node_errors") or {}).items():
            for err in ne.get("errors") or []:
                details.append(f"{ne.get('class_type')}: {err.get('message')} ({err.get('details')})")
        return msg + (": " + "; ".join(details[:3]) if details else "")
    except Exception:
        return str(e)


def load_video(api, name, key="lv"):
    """LoadVideo + GetVideoComponents: the IMAGE batch link, and the fps link."""
    api[key] = {"class_type": "LoadVideo", "inputs": {"file": name}}
    api[key + "c"] = {"class_type": "GetVideoComponents", "inputs": {"video": [key, 0]}}
    return [key + "c", 0], [key + "c", 2]


def save_video(api, images, fps, prefix, key="sv"):
    api[key + "v"] = {"class_type": "CreateVideo", "inputs": {"images": images, "fps": float(fps)}}
    # the same format Studio's video tools save with on the PC (SaveVideo's nested encoder settings are left at auto)
    api[key] = {"class_type": "SaveVideo", "inputs": {"video": [key + "v", 0], "filename_prefix": prefix, "format": "mp4", "format.codec": "auto"}}


def cut_frames(src, first, end, fps, dest, width=None, height=None):
    """Frames [first, end) of src as a near-lossless H.264 work file at a constant frame rate (no sound)."""
    vf = f"trim=start_frame={int(first)}:end_frame={int(end)},setpts=N/({float(fps):.6f}*TB)"
    if width and height:
        vf += f",scale={int(width)}:{int(height)}:flags=lanczos"
    ff(["-i", src, "-an", "-sn", "-dn", "-vf", vf + ",format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "10", "-r", f"{float(fps):.6f}", dest], what="Cutting the frames")
    return dest


def concat_copy(parts, dest, what="Joining"):
    """Files with the same streams joined end to end without re-encoding (ffmpeg's concat demuxer)."""
    lst = dest + ".txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n")
    try:
        ff(["-f", "concat", "-safe", "0", "-i", lst, "-map", "0", "-c", "copy", dest], what=what)
    finally:
        with contextlib.suppress(OSError):
            os.remove(lst)
    return dest


# ---------------------------------------------------------------- subtitles (AI Subtitles)
def srt_time(t):
    ms = max(0, int(round(float(t) * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def vtt_time(t):
    return srt_time(t).replace(",", ".")


def wrap_lines(text, max_chars=42, max_lines=2):
    """A cue's text on at most max_lines lines of at most max_chars, split where the two lines come out most even (a
    subtitle reads best as a pyramid of two similar lines). A word longer than a line is left whole."""
    words = text.split()
    if not words:
        return []
    one = " ".join(words)
    if len(one) <= max_chars or max_lines < 2:
        return [one]
    best = None
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        over = max(0, len(a) - max_chars) + max(0, len(b) - max_chars)
        score = (over, abs(len(a) - len(b)))
        if best is None or score < best[0]:
            best = (score, [a, b])
    return best[1]


def _timed_words(seg):
    """A segment's words with times: its own word times when the model gave them, else spread over the segment by length."""
    words = [w for w in (seg.get("words") or []) if str(w.get("word", "")).strip() and w.get("start") is not None and w.get("end") is not None]
    if words:
        return [{"word": str(w["word"]).strip(), "start": float(w["start"]), "end": float(w["end"])} for w in words]
    toks = str(seg.get("text") or "").split()
    if not toks:
        return []
    s, e = float(seg["start"]), float(seg["end"])
    total = sum(len(t) + 1 for t in toks)
    out, t = [], s
    for tok in toks:
        d = (e - s) * (len(tok) + 1) / total
        out.append({"word": tok, "start": t, "end": t + d})
        t += d
    return out


def make_cues(segments, max_chars=42, max_lines=2, max_dur=7.0, min_dur=0.8, gap=0.08):
    """Subtitle cues from what the speech model heard: [{"start", "end", "text" (a segment's words), "words"?}, ...] ->
    [{"start", "end", "lines"}]. A cue holds at most max_lines lines of max_chars and lasts at most max_dur; it ends at a
    sentence's end when it can, and each segment (a change of speaker or a pause, usually) starts a new one. Short cues
    stay up at least min_dur when the next one leaves room; cues never overlap."""
    limit = max_chars * max_lines
    cues = []
    for seg in segments:
        words = _timed_words(seg)
        cur = []
        for w in words:
            cand = cur + [w]
            text = " ".join(x["word"] for x in cand)
            fits = (len(text) <= limit and all(len(line) <= max_chars for line in wrap_lines(text, max_chars, max_lines))
                    and w["end"] - cand[0]["start"] <= max_dur)
            if cur and not fits:
                cues.append(cur)
                cand = [w]
            cur = cand
            # a sentence that ends past half a cue's room closes it: the next sentence gets a cue of its own
            if re.search(r"[.!?…]['\")\]]?$", w["word"]) and len(" ".join(x["word"] for x in cur)) >= limit // 2:
                cues.append(cur)
                cur = []
        if cur:
            cues.append(cur)
    out = []
    for ws in cues:
        out.append({"start": ws[0]["start"], "end": max(ws[-1]["end"], ws[0]["start"] + 0.2),
                    "lines": wrap_lines(" ".join(x["word"] for x in ws), max_chars, max_lines)})
    out.sort(key=lambda c: c["start"])
    for i, c in enumerate(out):
        nxt = out[i + 1]["start"] if i + 1 < len(out) else None
        if c["end"] - c["start"] < min_dur:
            c["end"] = c["start"] + min_dur if nxt is None else max(c["end"], min(c["start"] + min_dur, nxt - gap))
        if nxt is not None and c["end"] > nxt - gap:
            c["end"] = max(c["start"] + 0.1, nxt - gap)
        c["start"], c["end"] = round(c["start"], 3), round(c["end"], 3)
    return out


def to_srt(cues):
    return "".join(f"{i}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n" + "\n".join(c["lines"]) + "\n\n" for i, c in enumerate(cues, 1))


def to_vtt(cues):
    return "WEBVTT\n\n" + "".join(f"{vtt_time(c['start'])} --> {vtt_time(c['end'])}\n" + "\n".join(c["lines"]) + "\n\n" for c in cues)
