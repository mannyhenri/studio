"""What the stand-in ComfyUI answers with: a result file of the right kind and size for each output node of a job, and a
fixed answer from the stand-in LLM. Pictures are plain grey; videos are grey with a slowly moving bar and a quiet tone;
sounds are a quiet tone. Sizes and lengths are read from the job's own graph."""
import json
import os
import re
import shutil
import subprocess

IMAGE_OUT = {"SaveImage": "output", "PreviewImage": "temp", "SaveAnimatedWEBP": "output", "SaveAnimatedPNG": "output"}
VIDEO_OUT = {"SaveVideo": "output", "SaveWEBM": "output", "CreateVideo": None}
AUDIO_OUT = {"SaveAudio": "output", "SaveAudioMP3": "output", "SaveAudioOpus": "output", "PreviewAudio": "temp"}
TEXT_OUT = {"PreviewAny"}
UPSCALERS = ("SeedVR2", "Upscale", "ImageScaleBy", "LatentUpsampler")


def ffmpeg():
    return os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"


# ------------------------------------------------------------------ reading the graph
def _is_link(v):
    return isinstance(v, list) and len(v) == 2 and isinstance(v[1], int)


class Graph:
    """Constant-folds the small nodes Studio's apps compute sizes, lengths and names with (primitives, math expressions,
    string joins, switches, combos, image sizes), so the stand-in answers with the size the job really asks for."""

    def __init__(self, prompt, input_dir):
        self.p, self.input_dir, self.memo = prompt, input_dir, {}

    def value(self, v, depth=0):
        if not _is_link(v):
            return v
        if depth > 40:
            return None
        key = (str(v[0]), v[1])
        if key not in self.memo:
            self.memo[key] = None
            try:
                outs = self._outputs(str(v[0]), depth)
                self.memo[key] = outs[v[1]] if outs is not None and v[1] < len(outs) else None
            except Exception:
                self.memo[key] = None
        return self.memo[key]

    def _in(self, nid, name, depth):
        return self.value((self.p[nid].get("inputs") or {}).get(name), depth + 1)

    def _outputs(self, nid, depth):
        n = self.p.get(nid) or {}
        ct, ins = n.get("class_type", ""), n.get("inputs") or {}
        if ct.startswith("Primitive"):
            return [self._in(nid, "value", depth)]
        if ct == "ComfyMathExpression":
            from simpleeval import simple_eval
            import math
            names = {k.split(".", 1)[1]: self.value(v, depth + 1) for k, v in ins.items() if k.startswith("values.")}
            if any(x is None for x in names.values()):
                return None
            fns = {k: getattr(math, k) for k in ("floor", "ceil", "sqrt", "log", "exp", "sin", "cos")}
            fns.update(min=min, max=max, abs=abs, round=round, int=int, float=float)
            r = simple_eval(str(self._in(nid, "expression", depth)), names=names, functions=fns)
            return [float(r), int(r), bool(r)]
        if ct == "StringConcatenate":
            a, b = self._in(nid, "string_a", depth), self._in(nid, "string_b", depth)
            return None if a is None or b is None else [f"{a}{self._in(nid, 'delimiter', depth) or ''}{b}"]
        if ct == "RegexExtract":
            text, pat = self._in(nid, "string", depth), self._in(nid, "regex_pattern", depth)
            if not isinstance(text, str) or not isinstance(pat, str):
                return None
            flags = (re.I if self._in(nid, "case_insensitive", depth) else 0) | (re.M if self._in(nid, "multiline", depth) else 0) | \
                (re.S if self._in(nid, "dotall", depth) else 0)
            m = re.search(pat, text, flags)
            if not m:
                return [""]
            gi = self._in(nid, "group_index", depth) or 0
            return [m.group(gi) if m.lastindex and gi <= m.lastindex else m.group(0)]
        if ct == "CustomCombo":
            return [self._in(nid, "choice", depth), self._in(nid, "index", depth)]
        if ct == "ComfySwitchNode":
            sw = self._in(nid, "switch", depth)
            src = ins.get("on_true" if sw else "on_false")
            return [self.value(src, depth + 1)] if sw is not None else None
        if ct in ("GetImageSize", "GetVideoComponents"):
            s = self.image_size(ins.get("image") or ins.get("video"), depth)
            return [s[0], s[1], 1] if s else None
        return None

    def image_size(self, link, depth=0):
        """Size of the picture or clip behind a link: a loaded input file, or a node that sets width/height."""
        if not _is_link(link) or depth > 40:
            return None
        nid = str(link[0])
        n = self.p.get(nid) or {}
        ins = n.get("inputs") or {}
        if n.get("class_type", "").startswith("Load"):
            return size_of_input(n, self.input_dir)
        if n.get("class_type") == "ResizeImageMaskNode":
            return self.resized(nid, depth)
        w, h = self.value(ins.get("width"), depth + 1), self.value(ins.get("height"), depth + 1)
        if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w > 0 and h > 0:
            return int(w), int(h)
        for k in ("image", "images", "video", "pixels", "input"):
            if _is_link(ins.get(k)):
                return self.image_size(ins[k], depth + 1)
        return None


    def resized(self, nid, depth=0):
        """The size ResizeImageMaskNode gives its input, for each resize type Studio uses."""
        ins = self.p[nid].get("inputs") or {}
        kind = self.value(ins.get("resize_type"), depth + 1)
        src = self.image_size(ins.get("input"), depth + 1)
        get = lambda k: self.value(ins.get(f"resize_type.{k}"), depth + 1)  # noqa: E731
        if kind == "scale dimensions":
            w, h = get("width"), get("height")
            if w and h:
                return int(w), int(h)
            if src and (w or h):
                return (int(w), int(src[1] * w / src[0])) if w else (int(src[0] * h / src[1]), int(h))
        if not src:
            return None
        w, h = src
        if kind == "scale shorter dimension" and get("shorter_size"):
            k = get("shorter_size") / min(w, h)
        elif kind == "scale longer dimension" and get("longer_size"):
            k = get("longer_size") / max(w, h)
        elif kind == "scale total pixels" and get("megapixels"):
            k = (get("megapixels") * 1024 * 1024 / (w * h)) ** 0.5
        elif kind == "scale by multiplier" and get("multiplier"):
            k = get("multiplier")
        else:
            return src
        return int(round(w * k)), int(round(h * k))


def _literal(prompt, v, depth=0):
    """The constant behind an input (a literal, or what the small helper nodes compute)."""
    return Graph(prompt, "").value(v)


def _upstream(prompt, node_id):
    """Node ids feeding node_id, nearest first."""
    seen, order, todo = set(), [], [str(node_id)]
    while todo:
        nid = todo.pop(0)
        if nid in seen or nid not in prompt:
            continue
        seen.add(nid)
        order.append(nid)
        for v in (prompt[nid].get("inputs") or {}).values():
            if _is_link(v):
                todo.append(str(v[0]))
    return order


def _probe(path):
    try:
        out = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True, timeout=30).stderr
        m = re.search(r"Stream.*Video.* (\d{2,5})x(\d{2,5})", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


GENERATORS = ("EmptyLTXVLatentVideo", "EmptyHunyuanLatentVideo", "EmptyMochiLatentVideo", "ToVideo", "EmptySD3LatentImage",
              "EmptyFlux2LatentImage", "EmptyLatentImage", "EmptyQwenImageLayeredLatentImage", "MiniMaxH3")


def size_of(prompt, node_id, input_dir, video=False):
    """(width, height) the job asks for at this output: the generator's size (an empty latent or an image-to-video node),
    else an upscaler's target, else the nearest node with a width and height, else the input file's size, else
    1024x1024 for pictures and 832x480 for video."""
    g = Graph(prompt, input_dir)
    chain = _upstream(prompt, node_id)

    def wh(nid):
        ins = prompt[nid].get("inputs") or {}
        w, h = g.value(ins.get("width")), g.value(ins.get("height"))
        if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w > 0 and h > 0:
            return _even(w), _even(h)
        return None

    lat_up = 2 ** sum(1 for nid in chain if "LatentUpsampler" in prompt[nid].get("class_type", ""))
    for nid in chain:
        if any(x in prompt[nid].get("class_type", "") for x in GENERATORS) and wh(nid):
            w, h = wh(nid)
            return _even(w * lat_up), _even(h * lat_up)
    for nid in chain:
        if prompt[nid].get("class_type") == "ResizeImageMaskNode":
            r = g.resized(nid)
            if r:
                return _even(r[0]), _even(r[1])
    ups = [nid for nid in chain if any(u in prompt[nid].get("class_type", "") for u in UPSCALERS)]
    for nid in ups:
        ins = prompt[nid].get("inputs") or {}
        res = g.value(ins.get("resolution") or ins.get("new_resolution"))
        if isinstance(res, (int, float)) and res > 64:
            src = next((s for s in (size_of_input(prompt[x], input_dir) for x in chain) if s), None)
            if src:
                k = res / min(src)
                return _even(src[0] * k), _even(src[1] * k)
    for nid in chain:
        if any(x in prompt[nid].get("class_type", "") for x in GENERATORS) and wh(nid):
            return wh(nid)
    for nid in chain:
        if wh(nid):
            return wh(nid)
    for nid in chain:
        s = size_of_input(prompt[nid], input_dir)
        if s:
            return (_even(s[0] * 2), _even(s[1] * 2)) if ups else s
    return (832, 480) if video else (1024, 1024)


def size_of_input(node, input_dir):
    ins = node.get("inputs") or {}
    name = (ins.get("image") or ins.get("file") or ins.get("video")) if node.get("class_type", "").startswith("Load") else None
    if isinstance(name, str):
        p = os.path.join(input_dir, name.replace(" [input]", ""))
        if os.path.isfile(p):
            return _probe(p)
    return None


def _even(x):
    x = int(round(float(x)))
    return max(16, x - x % 2)


def seconds_of(prompt, node_id, default=2.0):
    g = Graph(prompt, "")
    chain = _upstream(prompt, node_id)
    fps = 24.0
    for nid in chain:
        ins = prompt[nid].get("inputs") or {}
        f = g.value(ins.get("fps") or ins.get("frame_rate"))
        if isinstance(f, (int, float)) and f > 0:
            fps = float(f)
            break
    for nid in chain:
        ins = prompt[nid].get("inputs") or {}
        for k in ("seconds", "duration", "duration_seconds"):
            v = g.value(ins.get(k))
            if isinstance(v, (int, float)) and v > 0:
                return min(float(v), 30.0)
        n = g.value(ins.get("length") or ins.get("num_frames") or ins.get("frames"))
        if isinstance(n, (int, float)) and n > 1:
            return min(float(n) / fps, 30.0)
    return default


def prefix_of(prompt, node_id, fallback):
    p = _literal(prompt, (prompt[node_id].get("inputs") or {}).get("filename_prefix"))
    return p if isinstance(p, str) and p.strip() else fallback


# ------------------------------------------------------------------ writing the files
def _next_name(folder, prefix, ext):
    sub = os.path.dirname(prefix.replace("\\", "/"))
    base = os.path.basename(prefix.replace("\\", "/")) or "ComfyUI"
    d = os.path.join(folder, sub)
    os.makedirs(d, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(d, f"{base}_{n:05d}_.{ext}")):
        n += 1
    return os.path.join(d, f"{base}_{n:05d}_.{ext}"), f"{base}_{n:05d}_.{ext}", sub


def write_image(path, w, h):
    from PIL import Image
    Image.new("RGB", (w, h), (128, 128, 128)).save(path)


def write_video(path, w, h, seconds, fps=24):
    bar = max(8, w // 40)
    vf = f"drawbox=x='mod(t*{max(1, w // 4)},{w})':y=0:w={bar}:h=ih:color=0x707070:t=fill"
    subprocess.run([ffmpeg(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s={w}x{h}:r={fps}:d={seconds:.3f}",
                    "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds:.3f}",
                    "-vf", vf, "-af", "volume=0.05", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", "-movflags", "+faststart", path], check=True, timeout=120)


def write_audio(path, seconds):
    subprocess.run([ffmpeg(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=330:sample_rate=48000:duration={seconds:.3f}", "-af", "volume=0.1", path], check=True, timeout=60)


# ------------------------------------------------------------------ the stand-in LLM
def llm_answer(prompt_text):
    """A fixed, well-formed answer for each kind of request Studio sends (script, review, prompt help), else a sentence."""
    t = prompt_text or ""
    if '"shots"' in t and "Number of shots" in t:
        n = int((re.search(r"exactly (\d+)", t) or [None, 3])[1])
        idea = (re.search(r'Idea: "([^"]*)"', t) or [None, "the idea"])[1]
        shots = [{"title": f"Shot {i + 1}", "action": f"A quiet moment {i + 1} of {n} from “{idea[:60]}”, soft evening light.",
                  "camera": ["static", "push in", "pan left", "tracking", "close-up"][i % 5], "characters": [], "dialogue": ""}
                 for i in range(n)]
        return json.dumps({"title": "Sandbox film", "logline": f"A short film about {idea[:80]}.",
                           "music": "warm piano, soft strings, slow tempo", "shots": shots})
    if '"verdict"' in t:
        return json.dumps({"score": 7, "prompt_match": 7, "motion": 7, "anatomy": 8, "artifacts": 7, "verdict": "keep",
                           "good": ["steady framing"], "problems": ["sandbox placeholder: nothing was really rendered"],
                           "better_prompt": "the same shot, slower camera, warmer light"})
    if "professional screenwriter" in t:                 # the screenplay editor's writing help: Fountain (a synopsis: a sentence)
        if "one-sentence synopsis" in t:
            return "Ray and June meet at the diner and neither says what they came to say."
        if "Turn the prose below" in t:
            return "INT. HARBOUR DINER - NIGHT\n\nThe rain eases. JUNE counts coins on the counter.\n\nJUNE\nOne more coffee.\n"
        return "Here is the passage:\n```\nRAY\n(quietly)\nStay for the coffee.\n\nJune looks at the door, then sits.\n```"
    if "prompt" in t.lower():
        return "A cinematic shot in soft evening light, shallow depth of field, gentle camera movement."
    return "Sandbox answer: the stand-in LLM has nothing to say about this."


def text_input_of(prompt, node_id):
    for nid in _upstream(prompt, node_id):
        n = prompt[nid]
        if "TextGenerate" in n.get("class_type", ""):
            v = _literal(prompt, (n.get("inputs") or {}).get("prompt"))
            return v if isinstance(v, str) else ""
    return ""


# ------------------------------------------------------------------ one output node
def make_output(prompt, node_id, dirs):
    """The UI output ComfyUI reports for this node ({"images": [...]}, {"audio": [...]}, {"text": [...]}), after writing the file."""
    ct = prompt[node_id]["class_type"]
    if ct in TEXT_OUT:
        return {"text": [llm_answer(text_input_of(prompt, node_id))]}
    if ct in IMAGE_OUT:
        kind = IMAGE_OUT[ct]
        w, h = size_of(prompt, node_id, dirs["input"])
        path, name, sub = _next_name(dirs[kind], prefix_of(prompt, node_id, "ComfyUI"), "png")
        write_image(path, w, h)
        return {"images": [{"filename": name, "subfolder": sub, "type": kind}]}
    if ct in VIDEO_OUT:
        w, h = size_of(prompt, node_id, dirs["input"], video=True)
        secs = seconds_of(prompt, node_id)
        path, name, sub = _next_name(dirs["output"], prefix_of(prompt, node_id, "video/ComfyUI"), "mp4")
        write_video(path, w, h, secs)
        return {"images": [{"filename": name, "subfolder": sub, "type": "output"}], "animated": [True]}
    if ct in AUDIO_OUT:
        kind = AUDIO_OUT[ct]
        secs = seconds_of(prompt, node_id, default=5.0)
        ext = "mp3" if ct == "SaveAudioMP3" else ("opus" if ct == "SaveAudioOpus" else "flac")
        path, name, sub = _next_name(dirs[kind], prefix_of(prompt, node_id, "audio/ComfyUI"), ext)
        write_audio(path, secs)
        return {"audio": [{"filename": name, "subfolder": sub, "type": kind}]}
    return None
