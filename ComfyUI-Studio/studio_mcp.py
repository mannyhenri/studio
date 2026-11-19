"""Studio's MCP server: Claude and other AI assistants use the whole app through it (no extra packages).

A film from a story: write or import the screenplay, its breakdown into scenes and shots, the cast and the locations (they
follow the screenplay) with their reference pictures, the storyboard's frames, the takes, the music and the voices, the film.
And everything else: any app (pictures, videos, edits, upscales), the gallery (look at any picture or clip), characters,
storyboards, the AI tools, the local AI's help, music and voices, video fixes, Previz sets, the GPU machines. Call
studio_overview first; `python3 scripts/studio-call.py --list` lists every tool.

Two ways to connect (the same tools):
- Over stdio, started by the assistant (Claude Desktop, Claude Code, Cowork…); "mcpServers":
    "studio": {"command": "<ComfyUI>\\.venv\\Scripts\\python.exe",
               "args": ["<ComfyUI>\\custom_nodes\\ComfyUI-Studio\\studio_mcp.py"]}
  (STUDIO_URL if Studio isn't at http://127.0.0.1:8188.)
- Over HTTP, served by Studio itself: http://<this-pc>:8188/studio/mcp (MCP's Streamable HTTP transport), e.g.
    claude mcp add --transport http studio http://127.0.0.1:8188/studio/mcp
Studio must be running. Only regular (non-Incognito) items are visible here. Paths are files on the PC Studio runs on.
What an AI saves is marked as its change (X-Studio-Client: ai): the page shows it rather than saving over it.
"""
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("STUDIO_URL", "http://127.0.0.1:8188").rstrip("/")
PROTOCOL = "2025-06-18"


AI_HEADERS = {"X-Studio-Client": "ai"}     # what an AI saves is marked so: the page takes it in instead of saving over it


def http(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json", **AI_HEADERS})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Studio answered {e.code}: {_said(e.read())}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Studio isn't reachable at {BASE} ({e.reason}). Is ComfyUI running?")


def _said(raw):
    """What Studio said in an error: its "error" sentence when it answered JSON, else the text."""
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
    try:
        d = json.loads(text)
        if isinstance(d, dict) and d.get("error"):
            return str(d["error"])[:600]
    except ValueError:
        pass
    return text[:600]


POLL = float(os.environ.get("STUDIO_MCP_POLL") or 2)      # seconds between looks at a job (the tests look more often)


def wait_job(jid, limit=1800):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = http("GET", f"/studio/api/job/{jid}")
        if j.get("status") == "done":
            return j.get("result")
        if j.get("status") in ("failed", "lost"):
            raise RuntimeError(j.get("error") or j.get("stage"))
        time.sleep(POLL)
    raise RuntimeError("Timed out waiting for Studio")


def input_dir_guess():
    return os.environ.get("STUDIO_INPUT_DIR") or os.path.join(os.path.expanduser("~"), "AppData", "Local", "Comfy-Desktop", "ComfyUI-Shared", "input")


def abs_input(v):
    try:
        root = http("GET", "/studio/api/paths")["input"]
    except Exception:
        root = input_dir_guess()
    return os.path.join(root, v.replace("/", os.sep))


def thumb(path, size=768):
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((size, size))
        b = io.BytesIO()
        im.save(b, "JPEG", quality=82)
        return {"type": "image", "data": base64.b64encode(b.getvalue()).decode(), "mimeType": "image/jpeg"}
    except Exception:
        return None


GUIDE = """How Studio works
- Previz (Studio › Previz) directs 3D sets in the browser: .glb files with their cameras, shot sizes, camera heights,
  camera moves, lighting setups, stand-ins and props. studio_list_previz_sets lists them with their cameras. A set comes
  from Blender as File › Export › glTF 2.0 (.glb) with Include › Cameras ticked, added in Previz › Add a .glb."""

TOOLS = [
    {"name": "studio_overview", "description": "Studio at a glance: its apps and counts of characters, storyboards, projects and Previz sets.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "studio_list_characters", "description": "Characters saved in Studio: name, the description every render of them gets (from their character sheet, LoRA trigger first), the sheet's fields, their LoRA and the absolute path of their face picture.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "studio_list_previz_sets", "description": "Previz sets (.glb) in Studio: name, file path, cameras (name, lens), animation length, and Studio cameras directed on them.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def call(name, a):
    if name == "studio_overview":
        counts = {k: len(http("GET", f"/studio/api/{k}")) for k in ("characters", "boards", "projects")}
        counts["previz_sets"] = len(http("GET", "/studio/api/glb"))
        app_names = [short(x.get("name") or x.get("id", "")) for x in apps()]
        counts["apps"] = len(app_names)
        return [{"type": "text", "text": json.dumps({"apps": app_names, "counts": counts}, indent=1) + "\n\n" + GUIDE}]
    if name == "studio_list_characters":
        cs = http("GET", "/studio/api/characters")
        sheet = ("age", "gender", "build", "skin", "features", "eyes", "hair", "facial_hair", "marks", "wardrobe", "accessories", "look", "trigger")
        return [{"type": "text", "text": json.dumps([{"name": c.get("name"), "description": c.get("description"), "sheet": {k: c[k] for k in sheet if c.get(k)},
                                                      "lora": (c.get("lora") or {}).get("name"),
                                                      "face_path": abs_input(c["face"]) if c.get("face") else None} for c in cs], indent=1)}]
    if name == "studio_list_previz_sets":
        out = []
        for g in http("GET", "/studio/api/glb"):
            i = g.get("info") or {}
            full = http("GET", f"/studio/api/glb/{g['id']}")
            out.append({"id": g["id"], "name": g.get("name"), "path": g.get("path"), "seconds": i.get("duration"),
                        "cameras": [{"name": c["name"], "lens": c.get("lens"), "moves": c.get("animated")} for c in i.get("cameras") or []],
                        "studio_cameras": [{"name": c.get("name"), "shot": c.get("setup"), "move": (c.get("move") or {}).get("type")}
                                           for c in ((full.get("direction") or {}).get("cams") or [])]})
        return [{"type": "text", "text": json.dumps(out, indent=1)}]
    raise RuntimeError("Unknown tool " + name)


# ================================================================ v2: generate everything, storyboard, projects, pipeline
import mimetypes
import re
import uuid as _uuid

_paths = None


def paths():
    global _paths
    if _paths is None:
        _paths = http("GET", "/studio/api/paths")
    return _paths


def short(name):
    return re.sub(r"^(Studio)\s*-\s*", "", re.sub(r"\.app\.json$", "", name.split("/")[-1]))


def apps():
    a = http("GET", "/studio/api/apps")
    return a.get("apps", a) if isinstance(a, dict) else a


def find_app(name):
    al = apps()
    n = (name or "").lower().strip()
    exact = [a for a in al if short(a["id"]).lower() == n]
    if exact:
        return exact[0]
    part = [a for a in al if n in short(a["id"]).lower()]
    if not part:
        raise RuntimeError(f"No app called '{name}'. Apps: " + ", ".join(short(a["id"]) for a in al))
    return part[0]


def upload(path, private=False):
    """Copies a local file into ComfyUI's input folder (as the app does). Returns the input value."""
    if not os.path.isfile(path):
        raise RuntimeError("No such file: " + path)
    boundary = _uuid.uuid4().hex
    fn = os.path.basename(path)
    with open(path, "rb") as f:
        data = f.read()
    ctype = mimetypes.guess_type(fn)[0] or "application/octet-stream"
    parts = []
    for k, v in (("subfolder", "studio_private" if private else "studio"), ("type", "input"), ("overwrite", "false")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{fn}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(BASE + "/upload/image", data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    return f"{d['subfolder']}/{d['name']}" if d.get("subfolder") else d["name"]


def as_input(v, private=False):
    """A local path is uploaded; anything else is taken as an input-folder value already."""
    if v and (os.path.isabs(v) or os.path.isfile(v)):
        return upload(v, private)
    return v


def family(opt):
    o = (opt or "").lower()
    for pat, fam in ((r"anime desire|illustrious", "sdxl"), (r"qwen", "qwen"), (r"z-image", "zimage"), (r"anima\b|anima \(", "anima"),
                     (r"wan 2\.2", "wan22"), (r"ltx", "ltx"), (r"minimax|h3", "h3"), (r"krea", "krea"), (r"flux 3|klein", "flux3"), (r"moss", "moss")):
        if re.search(pat, o):
            return fam
    return ""


def pick_option(inp, want):
    w = str(want).lower()
    return next((o for o in inp.get("options") or [] if o.lower() == w), None) or next((o for o in inp.get("options") or [] if w in o.lower()), None)


def build_run(a):
    app = find_app(a["app"])
    ins = app["inputs"]
    private = bool(a.get("private"))
    vals = {i["key"]: i.get("value") for i in ins}
    notes = []
    text = [i for i in ins if i["kind"] == "text" and "negative" not in i["label"].lower()]
    if a.get("prompt") is not None and text:
        vals[text[0]["key"]] = a["prompt"]
    if a.get("negative"):
        neg = next((i for i in ins if i["kind"] == "text" and "negative" in i["label"].lower()), None)
        if neg:
            vals[neg["key"]] = a["negative"]
    model_in = next((i for i in ins if i["kind"] == "choice" and "model" in i["label"].lower()), None)
    if a.get("model") and model_in:
        o = pick_option(model_in, a["model"])
        if not o:
            raise RuntimeError(f"No model '{a['model']}'. Options: " + " | ".join(model_in["options"]))
        vals[model_in["key"]] = o
    for label, v in (a.get("values") or {}).items():
        inp = next((i for i in ins if i["label"].lower() == label.lower()), None) or next((i for i in ins if label.lower() in i["label"].lower()), None)
        if not inp:
            notes.append(f"no option '{label}'")
            continue
        if inp["kind"] == "choice":
            o = pick_option(inp, v)
            if not o:
                raise RuntimeError(f"'{label}' must be one of: " + " | ".join(inp["options"]))
            vals[inp["key"]] = o
        elif inp["kind"] in ("int", "seed"):
            vals[inp["key"]] = int(v)
        elif inp["kind"] == "float":
            vals[inp["key"]] = float(v)
        elif inp["kind"] == "toggle":
            vals[inp["key"]] = bool(v)
        elif inp["kind"] in ("image", "video", "audio", "mask"):
            vals[inp["key"]] = as_input(v, private)
        else:
            vals[inp["key"]] = v
    toggles = [i for i in ins if i["kind"] == "toggle"]
    for kind, key in (("image", "images"), ("video", "videos"), ("audio", "audio")):
        media = [i for i in ins if i["kind"] == kind]
        for k, v in enumerate(a.get(key) or []):
            if k >= len(media):
                notes.append(f"only {len(media)} {kind} slot(s); extra ignored")
                break
            vals[media[k]["key"]] = as_input(v, private)
            tg = next((t for t in toggles if re.search(rf"\b{kind}\s*{k + 1}\b", t["label"].lower())), None) \
                or (next((t for t in toggles if "reference" in t["label"].lower()), None) if k == 0 and kind == "image" else None)
            if tg:
                vals[tg["key"]] = True
    unc = next((t for t in toggles if "uncensored" in t["label"].lower()), None)
    if a.get("uncensored") is not None and unc:
        vals[unc["key"]] = bool(a["uncensored"])
        private = private or bool(a["uncensored"])
    seed_keys = [i["key"] for i in ins if i["kind"] == "seed"]
    if a.get("seed") is not None and seed_keys:
        vals[seed_keys[0]] = int(a["seed"])
        seed_keys = seed_keys[1:]
    body = {"app": app["id"], "values": vals, "count": max(1, min(8, int(a.get("count") or 1))), "client_id": "claude-mcp",
            "random_seed_keys": seed_keys, "private": private, "target": a.get("run_on") or "local"}
    if a.get("loras"):
        have = [l["file"] for l in http("GET", "/studio/api/loras" + ("?private=1" if private else ""))]
        lo = []
        for l in a["loras"]:
            nm = l["name"] if isinstance(l, dict) else str(l)
            f = next((x for x in have if x.lower() == nm.lower()), None) or next((x for x in have if nm.lower() in x.lower()), None)
            if not f:
                raise RuntimeError(f"No LoRA '{nm}' installed")
            lo.append({"name": f, "strength": float(l.get("strength", 0.8)) if isinstance(l, dict) else 0.8})
        body["loras"] = lo
    if a.get("style") and model_in:
        fam = family(vals.get(model_in["key"]))
        files = [m["name"] for m in http("GET", "/studio/api/models?folder=diffusion_models").get("items", [])]
        f = next((x for x in files if a["style"].lower() in x.lower()), None)
        if not f:
            raise RuntimeError(f"No installed model file matching style '{a['style']}'")
        body["unet"] = {"family": fam, "file": f}
    if a.get("motion_video"):
        body["motion_video"] = as_input(a["motion_video"], private)
    if a.get("pose"):
        pz = str(a["pose"]).replace("\\", "/")
        if not pz.startswith("poses/"):
            packs = (http("GET", "/studio/api/poses") or {}).get("packs", [])
            hit = [x["file"] for p in packs for x in p["poses"] if pz.lower() in (p["name"] + "/" + x["name"]).lower()]
            if not hit:
                raise ValueError(f"No pose matches '{pz}'. Call studio_poses to see them.")
            pz = hit[0]
        body["pose"] = pz
        body["pose_strength"] = float(a.get("pose_strength") or 0.85)
    return app, body, notes


def when_at(s):
    s = str(s).strip().lower()
    if s in ("idle", "free", "when free", "when the pc is free"):
        return "idle", None
    m = re.match(r"in\s+(\d+(?:\.\d+)?)\s*(h|hour|hours|m|min|mins|minutes)$", s)
    if m:
        n = float(m.group(1))
        return "at", time.time() + n * (3600 if m.group(2).startswith("h") else 60)
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        t = time.localtime()
        at = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, int(m.group(1)), int(m.group(2)), 0, 0, 0, -1))
        return "at", at + (86400 if at < time.time() else 0)
    try:
        from datetime import datetime
        return "at", datetime.fromisoformat(s.upper().replace("Z", "+00:00")).timestamp()
    except Exception:
        raise RuntimeError("later: use 'idle', 'in 2h', '23:30' or an ISO time")


def remote_outputs(pid):
    """A job Studio sent to another GPU machine: its state, and its files once they're copied to this PC."""
    j = next((x for x in http("GET", "/studio/api/remote_jobs") if x["prompt_id"] == pid), None)
    if not j:
        return None, None
    if j["status"] in ("failed", "cancelled"):
        return "error", f"{j['worker']}: {j.get('error') or j['status']}"
    if j["status"] != "done":
        return "running", []
    return "done", [{"filename": o["filename"], "subfolder": re.sub(r"^\.private_incoming/?", "", o["subfolder"].replace("\\", "/")),
                     "private": o["subfolder"].replace("\\", "/").startswith(".private_incoming")} for o in j["outputs"]]


def outputs_of(pid):
    h = http("GET", f"/history/{pid}").get(pid)
    if not h:
        return remote_outputs(pid)
    st = h.get("status") or {}
    files = []
    for o in (h.get("outputs") or {}).values():
        for f in (o.get("images") or []) + (o.get("gifs") or []) + (o.get("videos") or []) + (o.get("audio") or []):
            if f.get("type") == "output":
                sub = (f.get("subfolder") or "").replace("\\", "/")
                private = sub.startswith(".private_incoming")
                files.append({"filename": f["filename"], "subfolder": re.sub(r"^\.private_incoming/?", "", sub), "private": private})
    if st.get("status_str") == "error":
        msgs = [m[1].get("exception_message", "") for m in st.get("messages", []) if m and m[0] == "execution_error"]
        return "error", " ".join(msgs)[:400]
    return ("done" if st.get("completed") or files else "running"), files


def abs_out(f):
    root = (paths().get("private") or "") if f.get("private") else paths()["output"]
    return os.path.join(root, f.get("subfolder", "").replace("/", os.sep), f["filename"])


def wait_prompts(pids, limit):
    t0, res = time.time(), {}
    while time.time() - t0 < limit:
        for pid in pids:
            if pid in res:
                continue
            st, files = outputs_of(pid)
            if st == "error":
                res[pid] = {"status": "failed", "error": files}
            elif st == "done":
                res[pid] = {"status": "done", "files": [dict(f, path=abs_out(f)) for f in files]}
        if len(res) == len(pids):
            break
        time.sleep(3)
    for pid in pids:
        res.setdefault(pid, {"status": "still rendering"})
    return res


def result_content(res, title):
    out = [{"type": "text", "text": title + "\n" + json.dumps(res, indent=1)}]
    shown = 0
    for r in res.values():
        for f in r.get("files") or []:
            if shown < 4 and f["filename"].lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                t = thumb(f["path"])
                if t:
                    out.append(t)
                    shown += 1
    return out


def job_wait(jid, limit=1800):
    return wait_job(jid, limit)


S_ = {"type": "string"}
N_ = {"type": "number"}
B_ = {"type": "boolean"}
TOOLS += [
    {"name": "studio_list_apps", "description": "Every Studio app (Image, Video, Edit, Inpaint, Upscale, Upscale video, Remove background, Smooth motion, Character replace…) with its options: labels, kinds, choices and defaults. Read this before studio_generate.",
     "inputSchema": {"type": "object", "properties": {"app": {"type": "string", "description": "Only this app (optional)"}}}},
    {"name": "studio_generate", "description": "Make pictures or videos with any Studio app, like pressing Generate. Options by label (from studio_list_apps). Local files for images/videos/audio are uploaded for you. Set later to render later instead (idle = when the PC is free, 'in 2h', '23:30'). wait=true waits and returns the files (paths, and previews for pictures).",
     "inputSchema": {"type": "object", "properties": {
         "app": {"type": "string", "description": "e.g. Image, Video, Edit, Inpaint, Upscale, Character replace"},
         "prompt": S_, "negative": S_, "model": {"type": "string", "description": "Part of a model option, e.g. 'MOSS', 'Krea 2', 'MiniMax H3 References', 'LTX'"},
         "style": {"type": "string", "description": "A downloaded finetune of that model to use instead (part of its file name), e.g. 'MiaoMiao', 'moss_image_heal', 'DaSiWa'"},
         "values": {"type": "object", "description": "Other options by label, e.g. {\"Aspect\": \"9:16\", \"Size\": \"Large\", \"Duration\": \"5\"}"},
         "images": {"type": "array", "items": S_, "description": "Image slots in order (local file paths or input values). Video app: image 1 = first frame."},
         "videos": {"type": "array", "items": S_}, "audio": {"type": "array", "items": S_},
         "motion_video": {"type": "string", "description": "MiniMax H3 References: a video whose camera move and motion to follow (<Video 1>)"},
         "pose": {"type": "string", "description": "Image app, Anime Desire or your SD/SDXL checkpoints only: a pose picture (poses/<pack>/<file>.png, or part of its name; see studio_poses)"},
         "pose_strength": {"type": "number", "description": "How closely to copy the pose, 0.3-1.2 (default 0.85)"},
         "loras": {"type": "array", "items": {"type": "object"}, "description": "[{name, strength}] installed LoRAs (part of the file name)"},
         "uncensored": B_, "private": {"type": "boolean", "description": "Save in Incognito"}, "count": N_, "seed": N_,
         "run_on": {"type": "string", "description": "Which GPU: \"local\" (this PC, default), \"auto\" (least busy machine that has the models) or a machine name from studio_status"},
         "later": {"type": "string"}, "wait": B_, "timeout": {"type": "number", "description": "Seconds to wait (default 300)"}}, "required": ["app"]}},
    {"name": "studio_wait", "description": "Wait for renders by prompt id and return their files.",
     "inputSchema": {"type": "object", "properties": {"prompt_ids": {"type": "array", "items": S_}, "timeout": N_}, "required": ["prompt_ids"]}},
    {"name": "studio_recent", "description": "The newest files in the gallery (pictures, videos or sounds) with their paths on this PC.",
     "inputSchema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["image", "video", "audio", "any"]}, "limit": N_, "folder": {"type": "string", "description": "Only this subfolder, e.g. Videos/Upscaled"}}}},
    {"name": "studio_queue", "description": "The ComfyUI queue (what's rendering and waiting, plus Studio's scheduled jobs); action stop = stop the current render, clear = remove waiting jobs.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["list", "stop", "clear"]}}}},
    {"name": "studio_music", "description": "Make a song or score on this PC. engine 'ace' (ACE-Step 1.5 XL, fast, up to 4 min) or 'music3' (MiniMax Music 3, best full songs up to 5 min; lyrics with [intro] [verse] [chorus] [bridge] [outro] tags; vocals/arrangement describe the singer and instruments). Waits and returns the file.",
     "inputSchema": {"type": "object", "properties": {"engine": {"type": "string", "enum": ["ace", "music3"]}, "style": {"type": "string", "description": "genre, mood, instruments"},
                                                      "vocals": {"type": "string", "description": "music3: the singer's voice and delivery"}, "arrangement": {"type": "string", "description": "music3: instruments and how the song builds"}, "lyrics": S_, "seconds": N_, "bpm": N_, "key": {"type": "string", "description": "e.g. A minor"}, "name": S_, "private": B_}, "required": ["style"]}},
    {"name": "studio_voice", "description": "Speak a line with Chatterbox; optionally copy a voice from a short recording (local path). Waits and returns the file.",
     "inputSchema": {"type": "object", "properties": {"text": S_, "voice": {"type": "string", "description": "5-15 s recording to copy (path)"}, "emotion": N_, "name": S_, "private": B_}, "required": ["text"]}},
    {"name": "studio_upscale_video", "description": "Upscale a video and/or raise its frame rate with Topaz Video (on this PC). Give a path, or filename+subfolder from the gallery.",
     "inputSchema": {"type": "object", "properties": {"path": S_, "filename": S_, "subfolder": S_, "size": {"type": "string", "enum": ["1080", "1440", "4k", ""]}, "fps": {"type": "string", "enum": ["keep", "48", "60"]}, "private": B_}}},
    {"name": "studio_fix_video", "description": "Fix part of a video with LTX-2.5 (the sound is always kept). mode 'picture' re-draws the marked seconds (plus 1 s each side) from the video itself, 'face' tracks the face and re-draws only it. Only that stretch changes; the result is a new file in Videos/Fixed. Give a path, or filename+subfolder from the gallery. Waits (several minutes).",
     "inputSchema": {"type": "object", "properties": {"run_on": {"type": "string", "description": "Which GPU: \"local\" (this PC, default), \"auto\" or a machine name from studio_status"}, "path": S_, "filename": S_, "subfolder": S_, "start": N_, "end": N_, "mode": {"type": "string", "enum": ["picture", "face"]},
                                                      "prompt": {"type": "string", "description": "what should happen in the marked part (for 'face': describe the person)"},                                                       "denoise": {"type": "number", "description": "how much to change: face 0.25-0.7 (default 0.45), picture 0.4-0.9 (default 0.7)"}, "seed": N_}, "required": ["start", "end", "mode"]}},
    {"name": "studio_extend_video", "description": "Continue a shot: the clip's last second carries into the new part, which is joined on. engine 'ltx' (LTX-2.5, default, +5 or +7 s, safe on this PC) or 'h3' (MiniMax H3 References with motion context, +5..11 s, optional character ref). Result: a new, longer video in Videos/Extended. Waits.",
     "inputSchema": {"type": "object", "properties": {"run_on": {"type": "string", "description": "Which GPU: \"local\" (this PC, default), \"auto\" or a machine name from studio_status"}, "path": S_, "filename": S_, "subfolder": S_, "prompt": {"type": "string", "description": "what happens next"},
                                                      "engine": {"type": "string", "enum": ["ltx", "h3"]}, "seconds": {"type": "integer", "enum": [5, 7, 9, 11]}, "ref": {"type": "string", "description": "character picture (local path), optional"}, "seed": N_}, "required": ["prompt"]}},
    {"name": "studio_keyframe_shot", "description": "One continuous shot through several pictures at chosen times. LTX-2.5, two passes, up to 8 pictures. keys: [{image: local path, at: seconds}], the first at 0. Waits.",
     "inputSchema": {"type": "object", "properties": {"run_on": {"type": "string", "description": "Which GPU: \"local\" (this PC, default), \"auto\" or a machine name from studio_status"}, "keys": {"type": "array", "items": {"type": "object"}}, "prompt": S_,
                                                      "seconds": N_, "aspect": {"type": "string", "enum": ["16:9", "9:16", "1:1", "21:9"]}, "seed": N_}, "required": ["keys", "prompt"]}},
    {"name": "studio_poses", "description": "Pose packs on this PC (stick-figure pictures for the 'pose' option of studio_generate), and whether the pose ControlNets are installed.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "studio_models", "description": "What's installed: LoRAs (with the model they're for) and finetunes ('styles') of each model.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "studio_list_boards", "description": "Storyboard boards with their shots: prompt, first frame, takes (files), the picked take, finished version and any final-render request.",
     "inputSchema": {"type": "object", "properties": {"board": {"type": "string", "description": "Only this board (id or name)"}}}},
    {"name": "studio_create_board", "description": "Make a Storyboard board from shots: [{prompt, image (path or input value, optional first frame), camera, seconds}].",
     "inputSchema": {"type": "object", "properties": {"name": S_, "shots": {"type": "array", "items": {"type": "object"}}, "private": B_}, "required": ["name", "shots"]}},
    {"name": "studio_attach", "description": "Put a file on a storyboard shot: as a take (default; it becomes the picked take) or as the shot's final version. The file can be a Studio render (filename+subfolder), a local path, or an https link (e.g. a Higgsfield/Seedance result).",
     "inputSchema": {"type": "object", "properties": {"board": S_, "shot": {"type": "string", "description": "shot id or number (1-based)"}, "path": S_, "url": S_, "filename": S_, "subfolder": S_,
                                                      "as": {"type": "string", "enum": ["take", "final"]}, "label": S_}, "required": ["board", "shot"]}},
    {"name": "studio_request_final", "description": "Mark a storyboard shot for its final render: 'seedance' (Claude renders it on Seedance 2.5 through Higgsfield) or 'local' (Studio's Finish), or 'none' to cancel.",
     "inputSchema": {"type": "object", "properties": {"board": S_, "shot": S_, "engine": {"type": "string", "enum": ["seedance", "local", "none"]}, "notes": S_}, "required": ["board", "shot", "engine"]}},
    {"name": "studio_final_requests", "description": "Shots waiting for a final render, with the approved take and first frame as files on this PC. For each 'seedance' one: upload the approved take (or first frame) to Higgsfield, generate with Seedance 2.5, then studio_attach the result with as='final'.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "studio_projects", "description": "List projects, or one project with its script and shots.",
     "inputSchema": {"type": "object", "properties": {"id": S_}}},
    {"name": "studio_project_create", "description": "Start a project from an idea, without a screenplay: the local AI writes a shot list (write_script=true, waits). cast = character names from studio_list_characters. For a film from a screenplay (scenes, shots, cast, locations, storyboard), use studio_project_new instead.",
     "inputSchema": {"type": "object", "properties": {"idea": S_, "title": S_, "tone": S_, "shots": N_, "seconds": N_, "cast": {"type": "array", "items": S_}, "write_script": B_, "private": B_}, "required": ["idea"]}},
    {"name": "studio_project_update", "description": "Change a project: title, logline, music, cast (names) or shots [{id?, title, action, camera, seconds, characters, dialogue, keyframe}]. A project without a screenplay: the list replaces its shots. A project with a screenplay: its shots are the screenplay's camera setups, so a shot with an id changes that shot (camera, seconds, keyframe; kept through edits of the screenplay), a shot without one is added (scene: a scene id from studio_projects, else the last scene); shots aren't removed this way.",
     "inputSchema": {"type": "object", "properties": {"id": S_, "title": S_, "logline": S_, "music": S_, "cast": {"type": "array", "items": S_}, "shots": {"type": "array", "items": {"type": "object"}}}, "required": ["id"]}},
    {"name": "studio_project_keyframes", "description": "Make a still for each project shot (missing ones, or all with redo) with the Image app; one-character shots use that character's face. Runs in the background unless wait=true.",
     "inputSchema": {"type": "object", "properties": {"id": S_, "model": S_, "redo": B_, "wait": B_,
                     "run_on": {"type": "string", "description": "Which GPU: \"local\" (this PC, default), \"auto\" or a machine name from studio_status"}}, "required": ["id"]}},
    {"name": "studio_project_to_board", "description": "Send a project's shots (with their storyboard frames as first frames) to its takes board (Production › Takes in the app).",
     "inputSchema": {"type": "object", "properties": {"id": S_}, "required": ["id"]}},
]

GUIDE += """

Working from Claude
- studio_list_apps shows every app and its options; studio_generate runs any of them (pictures, videos, edits, upscales…).
- Local files (references, first frames, voices) are uploaded for you: just give their paths.
- Fast tries: Image app (Z-Image / Qwen turbo) and Video app (LTX, MiniMax H3 turbo). On this PC Wan 2.2 and MOSS are slow.
- Storyboard: studio_create_board or studio_project_to_board, then studio_generate with the shot's prompt
  and first frame, then studio_attach the result to the shot as a take. The user picks and finishes takes in Studio.
- Final renders: studio_final_requests lists shots marked for Seedance 2.5. Render them through Higgsfield and bring the result
  back with studio_attach(as='final')."""


def _board(ref):
    boards = http("GET", "/studio/api/boards")
    b = next((x for x in boards if x.get("id") == ref), None) or next((x for x in boards if (x.get("name") or "").lower() == str(ref).lower()), None)
    if not b:
        raise RuntimeError("No such board. Boards: " + ", ".join(f"{x.get('name')} ({x.get('id')})" for x in boards))
    return b


def _shot(b, ref):
    s = next((x for x in b["shots"] if x.get("id") == ref), None)
    if not s and str(ref).isdigit() and 0 < int(ref) <= len(b["shots"]):
        s = b["shots"][int(ref) - 1]
    if not s:
        raise RuntimeError("No such shot")
    return s


def _char_ids(names):
    chars = http("GET", "/studio/api/characters")
    out = []
    for n in names or []:
        c = next((x for x in chars if x["name"].lower() == n.lower()), None) or next((x for x in chars if n.lower() in x["name"].lower()), None)
        if not c:
            raise RuntimeError(f"No character '{n}'")
        out.append(c["id"])
    return out


def _project(pid):
    pr = next((x for x in http("GET", "/studio/api/projects") if x["id"] == pid), None)
    if not pr:
        raise RuntimeError("No such project")
    return pr


_call_v1 = call


def call(name, a):
    if name == "studio_list_apps":
        out = []
        for ap in apps():
            if a.get("app") and a["app"].lower() not in short(ap["id"]).lower():
                continue
            out.append({"app": short(ap["id"]), "kind": ap["kind"], "inputs": [
                {"label": i["label"], "kind": i["kind"], **({"options": i["options"]} if i.get("options") else {}),
                 **({"default": i.get("value")} if i["kind"] not in ("image", "video", "audio", "mask") else {})} for i in ap["inputs"]]})
        return [{"type": "text", "text": json.dumps(out, indent=1)}]
    if name == "studio_generate":
        app, body, notes = build_run(a)
        if a.get("later"):
            when, at = when_at(a["later"])
            r = http("POST", "/studio/api/schedule", {"run": body, "when": when, "at": at, "kind": app["kind"], "label": f"{short(app['id'])} (from Claude): {(a.get('prompt') or '')[:60]}"})
            return [{"type": "text", "text": f"Scheduled ({'when the PC is free' if when == 'idle' else time.strftime('%a %H:%M', time.localtime(at))}). id {r['id']}" + ("\nNotes: " + "; ".join(notes) if notes else "")}]
        r = http("POST", "/studio/api/run", body)
        pids = [q["prompt_id"] for q in r.get("queued", [])]
        where = sorted({q.get("worker") or "local" for q in r.get("queued", [])})
        head = f"Queued {len(pids)} on {short(app['id'])} ({', '.join(where)}): {pids}" + ("\nNotes: " + "; ".join(notes) if notes else "")
        dl = sorted({f"{v} on {q['worker']}" for q in r.get("queued", []) for v in q.get("fetching") or []})
        if dl:
            head += "\nDownloading first (it renders once the model is there; a big model can take a while): " + ", ".join(dl)
        if not a.get("wait", app["kind"] == "image"):
            return [{"type": "text", "text": head + "\nUse studio_wait with these ids to get the files."}]
        return result_content(wait_prompts(pids, float(a.get("timeout") or 300)), head)
    if name == "studio_wait":
        return result_content(wait_prompts(a["prompt_ids"], float(a.get("timeout") or 300)), "Renders:")
    if name == "studio_recent":
        items = http("GET", "/studio/api/gallery")
        kind = a.get("kind") or "any"
        items = [i for i in items if (kind == "any" or i.get("kind") == kind) and (not a.get("folder") or (i.get("subfolder") or "").startswith(a["folder"]))]
        items.sort(key=lambda i: -(i.get("mtime") or 0))
        if kind in ("audio", "any"):
            snd = http("GET", "/studio/api/sounds")
            items = (items + snd) if kind == "any" else snd
        out = [{"filename": i["filename"], "subfolder": i.get("subfolder", ""), "kind": i.get("kind"), "path": abs_out(i),
                "made": time.strftime("%Y-%m-%d %H:%M", time.localtime(i.get("mtime") or 0))} for i in items[: int(a.get("limit") or 12)]]
        return [{"type": "text", "text": json.dumps(out, indent=1)}]
    if name == "studio_queue":
        act = a.get("action") or "list"
        if act == "stop":
            http("POST", "/interrupt", {})
        elif act == "clear":
            http("POST", "/queue", {"clear": True})
        q = http("GET", "/queue")
        g = http("GET", "/studio/api/guard")
        sch = http("GET", "/studio/api/schedule")
        return [{"type": "text", "text": json.dumps({"running": [short(x[3].get("studio_app", "graph")) for x in q["queue_running"]],
                                                     "progress": g.get("job"), "waiting": [short(x[3].get("studio_app", "graph")) for x in q["queue_pending"]],
                                                     "scheduled": [s for s in sch if s.get("status") == "waiting"]}, indent=1)}]
    if name == "studio_music":
        r = http("POST", "/studio/api/music", {"engine": a.get("engine") or "ace", "vocals": a.get("vocals"), "arrangement": a.get("arrangement"),
                                              "tags": a["style"], "lyrics": a.get("lyrics") or "", "instrumental": not a.get("lyrics"),
                                              "seconds": a.get("seconds") or 60, "bpm": a.get("bpm") or 100, "key": a.get("key"), "name": a.get("name"), "private": a.get("private")})
        res = job_wait(r["id"], 5400)
        return [{"type": "text", "text": json.dumps(dict(res, path=abs_out(res)), indent=1)}]
    if name == "studio_voice":
        r = http("POST", "/studio/api/voice", {"text": a["text"], "voice": as_input(a.get("voice"), a.get("private")) if a.get("voice") else "",
                                              "exaggeration": a.get("emotion") or 0.5, "name": a.get("name"), "private": a.get("private")})
        res = job_wait(r["id"], 1200)
        return [{"type": "text", "text": json.dumps(dict(res, path=abs_out(res)), indent=1)}]
    if name == "studio_upscale_video":
        f = {"filename": a.get("filename"), "subfolder": a.get("subfolder", "")}
        if a.get("path"):
            info = http("POST", "/studio/api/import", {"path": a["path"], "private": a.get("private")})
            f = {"filename": info["filename"], "subfolder": info["subfolder"]}
        r = http("POST", "/studio/api/topaz/video", dict(f, size=a.get("size", "1080"), fps=a.get("fps", "keep"), private=a.get("private")))
        res = job_wait(r["id"], 3600)
        return [{"type": "text", "text": json.dumps(dict(res, path=abs_out(res)), indent=1)}]
    if name in ("studio_fix_video", "studio_extend_video"):
        f = {"filename": a.get("filename"), "subfolder": a.get("subfolder", "")}
        if a.get("path"):
            info = http("POST", "/studio/api/import", {"path": a["path"]})
            f = {"filename": info["filename"], "subfolder": info["subfolder"]}
        if not f["filename"]:
            raise ValueError("Give path, or filename + subfolder")
        body = dict(f, prompt=a.get("prompt", ""), seed=a.get("seed"), target=a.get("run_on") or "local")
        if a.get("ref"):
            body["ref"] = as_input(a["ref"])
        if name == "studio_fix_video":
            body.update(start=a["start"], end=a["end"], mode=a["mode"], denoise=a.get("denoise"))
            r = http("POST", "/studio/api/video/fix", body)
        else:
            body.update(seconds=a.get("seconds", 5), engine=a.get("engine", "ltx"))
            r = http("POST", "/studio/api/video/extend", body)
        res = job_wait(r["id"], 4 * 3600)
        return [{"type": "text", "text": json.dumps(dict(res, path=abs_out(res)), indent=1)}]
    if name == "studio_keyframe_shot":
        sizes = {"16:9": (1280, 704), "9:16": (704, 1280), "1:1": (960, 960), "21:9": (1344, 576)}
        w, h = sizes.get(a.get("aspect") or "16:9")
        keys = [{"image": as_input(k["image"]), "at": float(k.get("at") or 0)} for k in a["keys"]]
        r = http("POST", "/studio/api/video/keyframes", {"engine": a.get("engine", "ltx"), "keys": keys, "prompt": a["prompt"],
                                                       "seconds": a.get("seconds") or max(5, max(k["at"] for k in keys)), "width": w, "height": h, "seed": a.get("seed"), "target": a.get("run_on") or "local"})
        res = job_wait(r["id"], 4 * 3600)
        return [{"type": "text", "text": json.dumps(dict(res, path=abs_out(res)), indent=1)}]
    if name == "studio_poses":
        d = http("GET", "/studio/api/poses")
        return [{"type": "text", "text": json.dumps({"controlnets": d.get("controlnets"), "packs": [{"pack": p["name"], "poses": [x["file"] for x in p["poses"]]} for p in d.get("packs", [])]}, indent=1)}]
    if name == "studio_models":
        lo = http("GET", "/studio/api/loras")
        st = [m for m in http("GET", "/studio/api/models?folder=diffusion_models").get("items", [])]
        return [{"type": "text", "text": json.dumps({"loras": [{"name": l.get("name"), "file": l["file"], "for": l.get("base")} for l in lo],
                                                     "model_files": [{"file": m["name"], "title": m.get("title"), "base": m.get("base"), "gb": round(m.get("size", 0) / 1e9, 1)} for m in st]}, indent=1)}]
    if name == "studio_list_boards":
        boards = http("GET", "/studio/api/boards")
        if a.get("board"):
            boards = [_board(a["board"])]
        out = []
        for b in boards:
            shots = []
            for k, s in enumerate(b.get("shots", [])):
                takes = [t.get("output") for t in s.get("takes") or [] if t.get("output")]
                shots.append({"n": k + 1, "id": s["id"], "prompt": s.get("prompt"), "camera": s.get("camera"), "first_frame": s.get("image"),
                              "engine": s.get("engine"), "takes": len(takes),
                              "picked": s.get("output"), "final": s.get("final"), "final_request": s.get("final_request")})
            out.append({"id": b["id"], "name": b.get("name"), "private": b.get("private"), "shots": shots})
        return [{"type": "text", "text": json.dumps(out, indent=1)}]
    if name == "studio_create_board":
        priv = bool(a.get("private"))
        shots = [{"id": _uuid.uuid4().hex[:8], "prompt": s.get("prompt", ""), "image": as_input(s.get("image"), priv) if s.get("image") else "",
                  "camera": s.get("camera", ""), "seconds": s.get("seconds")} for s in a["shots"]]
        b = http("POST", "/studio/api/boards", {"name": a["name"], "shots": shots, "private": priv})
        return [{"type": "text", "text": f"Board '{b['name']}' ({b['id']}) with {len(shots)} shots. Shot ids: " + ", ".join(s["id"] for s in shots)}]
    if name == "studio_attach":
        b = _board(a["board"])
        s = _shot(b, a["shot"])
        body = {k: a.get(k) for k in ("path", "url", "filename", "subfolder", "label") if a.get(k)}
        body["as"] = a.get("as") or "take"
        r = http("POST", f"/studio/api/boards/{b['id']}/shots/{s['id']}/attach", body, timeout=600)
        return [{"type": "text", "text": json.dumps(r)}]
    if name == "studio_request_final":
        b = _board(a["board"])
        s = _shot(b, a["shot"])
        r = http("POST", f"/studio/api/boards/{b['id']}/shots/{s['id']}/final_request", {"engine": a["engine"], "notes": a.get("notes", "")})
        return [{"type": "text", "text": json.dumps(r)}]
    if name == "studio_final_requests":
        return [{"type": "text", "text": json.dumps(http("GET", "/studio/api/final_requests"), indent=1)}]
    if name == "studio_projects":
        if a.get("id"):
            return [{"type": "text", "text": json.dumps(_project(a["id"]), indent=1)}]
        return [{"type": "text", "text": json.dumps([{"id": p["id"], "title": p.get("title"), "stage": p.get("stage"), "shots": len(p.get("shots") or [])} for p in http("GET", "/studio/api/projects")], indent=1)}]
    if name == "studio_project_create":
        pr = {"name": a.get("title") or "New film", "title": a.get("title") or "", "idea": a["idea"], "tone": a.get("tone") or "Cinematic drama",
              "nshots": int(a.get("shots") or 6), "seconds": int(a.get("seconds") or 5), "cast": _char_ids(a.get("cast")), "shots": [],
              "private": bool(a.get("private")), "stage": "Idea"}
        pr = http("POST", "/studio/api/projects", pr)
        if a.get("write_script", True):
            chars = {c["id"]: c for c in http("GET", "/studio/api/characters")}
            cast = [{"name": chars[c]["name"], "look": chars[c].get("description") or chars[c].get("look") or ""} for c in pr["cast"] if c in chars]
            r = http("POST", "/studio/api/projects/script", {"idea": pr["idea"], "tone": pr["tone"], "shots": pr["nshots"], "seconds": pr["seconds"], "cast": cast, "private": pr["private"]})
            res = job_wait(r["id"], 900)
            pr.update(title=pr["title"] or res.get("title"), logline=res.get("logline"), music=res.get("music"), shots=res.get("shots"), stage="Shots", step="scenes")
            pr = http("POST", "/studio/api/projects", pr)
        return [{"type": "text", "text": json.dumps(pr, indent=1)}]
    if name == "studio_project_update":
        pr = _project(a["id"])
        for k in ("title", "logline", "music"):
            if a.get(k) is not None:
                pr[k] = a[k]
        if a.get("cast") is not None:
            pr["cast"] = _char_ids(a["cast"])
        if a.get("shots") is not None and pr.get("screenplay"):
            have = {s["id"]: s for s in pr.get("shots") or []}
            last = (pr.get("scenes") or [{}])[-1].get("id")
            for s in a["shots"]:
                old = have.get(s.get("id"))
                if old:                                   # the screenplay's shot: what can be set on it
                    if s.get("camera"):
                        old.update(camera=s["camera"], camera_set=True)
                    if s.get("seconds"):
                        old.update(seconds=s["seconds"], seconds_set=True)
                    if s.get("keyframe") is not None:
                        old["keyframe"] = s["keyframe"]
                    continue
                scene = s.get("scene") or last
                after = [x["id"] for x in pr["shots"] if x.get("scene") == scene]
                pr["shots"].append({"id": _uuid.uuid4().hex[:8], "scene": scene, "after": after[-1] if after else "^", "manual": True,
                                    "title": s.get("title", "Added shot"), "action": s.get("action", ""), "camera": s.get("camera", "static"),
                                    "seconds": s.get("seconds", 5), "characters": s.get("characters", []), "lines": [], "beats": [],
                                    "dialogue": s.get("dialogue", ""), **({"keyframe": s["keyframe"]} if s.get("keyframe") else {})})
        elif a.get("shots") is not None:
            pr["shots"] = [{"id": s.get("id") or _uuid.uuid4().hex[:8], "title": s.get("title", f"Shot {k + 1}"), "action": s.get("action", ""),
                            "camera": s.get("camera", "static"), "seconds": s.get("seconds", pr.get("seconds", 5)),
                            "characters": s.get("characters", []), "dialogue": s.get("dialogue", ""), **({"keyframe": s["keyframe"]} if s.get("keyframe") else {})}
                           for k, s in enumerate(a["shots"])]
        return [{"type": "text", "text": json.dumps(http("POST", "/studio/api/projects", pr), indent=1)}]
    if name == "studio_project_keyframes":
        pr = _project(a["id"])
        r = http("POST", f"/studio/api/projects/{pr['id']}/keyframes", {"private": pr.get("private"), "model": a.get("model"), "redo": a.get("redo"), "target": a.get("run_on") or "local"})
        if not a.get("wait"):
            return [{"type": "text", "text": f"Making keyframes in the background (job {r['id']}). Check with studio_projects."}]
        job_wait(r["id"], 3600)
        pr = _project(a["id"])
        content = [{"type": "text", "text": json.dumps([{"title": s.get("title"), "keyframe": s.get("keyframe")} for s in pr.get("shots", [])], indent=1)}]
        for s in pr.get("shots", [])[:4]:
            if s.get("keyframe"):
                t = thumb(abs_input(s["keyframe"]))
                if t:
                    content.append(t)
        return content
    if name == "studio_project_to_board":
        pr = _project(a["id"])
        return [{"type": "text", "text": json.dumps(http("POST", f"/studio/api/projects/{pr['id']}/to_board", {"private": pr.get("private")}), indent=1)}]
    return _call_v1(name, a)

# ================================================================ v3: the whole app, a project from story to film
# Every page of Studio, for an AI: a project's screenplay (write it, or import a file), its breakdown into scenes and shots,
# its cast and locations (which follow the screenplay as it changes) with their reference pictures, the storyboard's frames,
# the takes (drafted, picked), the music and the voices, the film; the AI tools; the gallery (look at any picture or clip);
# the local AI's help; Previz sets; the status of the GPU machines. The server does each step as the page does it
# (projectops.py), so what an AI makes here opens in the page as if it had been made there.
import urllib.parse as _urlparse

V3 = {}


def tool(name, description, properties=None, required=()):
    """Registers a tool: its schema for tools/list and its handler for tools/call."""
    def reg(fn):
        TOOLS.append({"name": name, "description": description,
                      "inputSchema": {"type": "object", "properties": properties or {}, **({"required": list(required)} if required else {})}})
        V3[name] = fn
        return fn
    return reg


def text(x):
    return [{"type": "text", "text": x if isinstance(x, str) else json.dumps(x, indent=1, ensure_ascii=False)}]


P_PROJECT = {"type": "string", "description": "The project: its id or its title"}
P_WAIT = {"type": "boolean", "description": "Wait for it to finish (default true)"}
P_TIMEOUT = {"type": "number", "description": "Seconds to wait at most"}
P_MODEL = {"type": "string", "description": "The Image app's model to use, part of its name (e.g. 'Z-Image', 'Qwen', 'FLUX 3', 'Krea'); default: the app's"}
P_RUN_ON = {"type": "string", "description": "Which GPU: \"local\" (this PC, default), \"auto\" or a machine name from studio_status"}
SHEET = ("age", "gender", "build", "skin", "features", "eyes", "hair", "facial_hair", "marks", "wardrobe", "accessories", "look", "trigger")


def project_ref(ref):
    """A regular project by id, or by title (exact, then partial)."""
    items = http("GET", "/studio/api/projects")
    r = str(ref or "").strip().lower()
    pr = next((x for x in items if x.get("id") == ref), None) or next((x for x in items if (x.get("title") or x.get("name") or "").lower() == r), None) \
        or next((x for x in items if r and r in (x.get("title") or x.get("name") or "").lower()), None)
    if not pr:
        raise RuntimeError("No such project. Projects: " + (", ".join(f"{x.get('title') or x.get('name')} ({x['id']})" for x in items) or "none yet"))
    return pr


def character_ref(ref):
    cs = http("GET", "/studio/api/characters")
    r = str(ref or "").strip().lower()
    c = next((x for x in cs if x.get("id") == ref), None) or next((x for x in cs if x["name"].lower() == r), None) \
        or next((x for x in cs if r and r in x["name"].lower()), None)
    if not c:
        raise RuntimeError("No such character. Characters: " + (", ".join(x["name"] for x in cs) or "none yet"))
    return c


def codes_of(pr):
    """Shot codes as the breakdown numbers them (1A, 1B, 2A…), like the page's shotCodes."""
    num = {sc["id"]: sc.get("number") or str(k + 1) for k, sc in enumerate(pr.get("scenes") or [])}
    seen, out = {}, {}
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"

    def lt(n):
        o, n = "", n + 1
        while n:
            r = (n - 1) % 24
            n = (n - 1) // 24
            o = letters[r] + o
        return o
    for k, s in enumerate(pr.get("shots") or []):
        n = seen.get(s.get("scene"), 0)
        seen[s.get("scene")] = n + 1
        out[s["id"]] = (num[s["scene"]] + ("-" if re.search(r"[A-Za-z]$", num[s["scene"]]) else "") + lt(n)) if s.get("scene") in num else str(k + 1)
    return out


def input_path(v):
    return abs_input(v) if v else None


def image_content(path, size=768):
    """A picture for the AI to look at: made smaller with Pillow when it's there, else sent as it is (up to 5 MB)."""
    t = thumb(path, size)
    if t:
        return t
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(os.path.splitext(path)[1].lower())
    if mime and os.path.getsize(path) < 5e6:
        with open(path, "rb") as f:
            return {"type": "image", "data": base64.b64encode(f.read()).decode(), "mimeType": mime}
    return None


def picture_content(path, caption=None):
    out = []
    t = image_content(path) if path and os.path.isfile(path) else None
    if caption:
        out.append({"type": "text", "text": caption})
    if t:
        out.append(t)
    return out


def job_result(jid, wait, timeout, what):
    if not wait:
        return None, f"{what} in the background (job {jid}); studio_job tells how it's going."
    return job_wait(jid, timeout), None


def gallery_file(a, private=False):
    """{filename, subfolder} of a file given as a gallery item, or a path on this PC (brought into the gallery first)."""
    if a.get("filename"):
        return {"filename": a["filename"], "subfolder": a.get("subfolder") or ""}
    if a.get("path"):
        info = http("POST", "/studio/api/import", {"path": a["path"], "private": private}, timeout=600)
        return {"filename": info["filename"], "subfolder": info["subfolder"]}
    raise RuntimeError("Give the file: path (on this PC), or filename + subfolder (from studio_recent)")


# ---------------------------------------------------------------- status
@tool("studio_status", "This PC and the GPU machines: what's rendering and waiting (ComfyUI's queue), the machines Studio can render on (for run_on), memory and load, and the AI tools' jobs.")
def _status(a):
    q = http("GET", "/queue")
    out = {"rendering": [short((x[3] or {}).get("studio_label") or (x[3] or {}).get("studio_app") or "graph") for x in q.get("queue_running", [])],
           "waiting": [short((x[3] or {}).get("studio_label") or (x[3] or {}).get("studio_app") or "graph") for x in q.get("queue_pending", [])]}
    try:
        ws = http("GET", "/studio/api/workers")                  # "local" is this PC; the others can be named in run_on
        out["machines"] = [{"name": w.get("label"), "host": w.get("host"), "port": w.get("port"), "online": w.get("online")}
                           for w in (ws if isinstance(ws, list) else ws.get("workers", []))]
    except Exception:
        out["machines"] = []
    try:
        out["this_pc"] = http("GET", "/studio/api/sysmon")
    except Exception:
        pass
    try:
        out["ai_tool_jobs"] = [{"tool": j["tool_name"], "file": j["input_name"], "status": j["status"], "stage": j["stage"]}
                               for j in http("GET", "/studio/api/generators/jobs")["jobs"][:5] if j["status"] in ("running", "queued")]
    except Exception:
        pass
    return text(out)


@tool("studio_job", "How a background job is going (music, voices, pictures, frames, the film, video fixes…): its stage, and its result when it's done.",
      {"id": {"type": "string"}, "wait": {"type": "boolean", "description": "Wait for it to finish"}, "timeout": P_TIMEOUT}, ["id"])
def _job(a):
    if a.get("wait"):
        return text({"status": "done", "result": job_wait(a["id"], float(a.get("timeout") or 1800))})
    return text(http("GET", f"/studio/api/job/{a['id']}"))


# ---------------------------------------------------------------- projects and their screenplays
@tool("studio_project_new",
      "Start a film project. Give its screenplay as Fountain text (write it yourself: scene headings INT./EXT. PLACE - DAY, action, "
      "CHARACTER cues in capitals with their dialogue), or screenplay_path: a PDF, Final Draft (.fdx) or Fountain file on this PC. "
      "Studio breaks it down into scenes and shots, makes a location for every place in the scene headings and casts every speaking "
      "part (with a character made from how the screenplay introduces them, or an existing character of the same name). "
      "Without a screenplay the project starts empty: add one with studio_screenplay_set.",
      {"title": {"type": "string"}, "screenplay": {"type": "string", "description": "The screenplay in Fountain"},
       "screenplay_path": {"type": "string", "description": "A screenplay file on this PC (.pdf, .fdx, .fountain, .txt)"},
       "tone": {"type": "string", "description": "The style for the pictures, e.g. 'Cinematic drama', 'Film noir', 'Anime'"},
       "idea": {"type": "string", "description": "The story in a few sentences (kept with the project)"}})
def _project_new(a):
    if a.get("screenplay_path"):
        path = a["screenplay_path"]
        if not os.path.isfile(path):
            raise RuntimeError("No such file: " + path)
        with open(path, "rb") as f:
            data = f.read()
        r = multipart("/studio/api/projects/import_screenplay", {"private": "0"}, "file", os.path.basename(path), data)
        pid = r["project"]["id"]
        out = http("POST", f"/studio/api/projects/{pid}/follow", {})
        pr = project_ref(pid)
        if a.get("title") or a.get("tone") or a.get("idea"):
            for k in ("title", "tone", "idea"):
                if a.get(k):
                    pr[k] = a[k]
            if a.get("title"):
                pr["name"] = a["title"]
            http("POST", "/studio/api/projects", pr)
        return text({"imported": r.get("summary"), "warnings": r.get("warnings"), **out})
    return text(http("POST", "/studio/api/projects/new", {k: a.get(k) for k in ("title", "screenplay", "tone", "idea") if a.get(k)}))


@tool("studio_project_delete", "Delete a project (its characters that were made for it and never touched go with it; pictures and videos stay in the gallery).",
      {"project": P_PROJECT}, ["project"])
def _project_delete(a):
    pr = project_ref(a["project"])
    return text(http("DELETE", f"/studio/api/projects/{pr['id']}"))


@tool("studio_screenplay_get", "A project's screenplay, as Fountain text, with its title, length in pages and what's in it.",
      {"project": P_PROJECT}, ["project"])
def _screenplay_get(a):
    pr = project_ref(a["project"])
    sp = pr.get("screenplay") or {}
    return text(f"Title: {pr.get('title') or '(none)'} · {pr.get('pages') or 0} pages · {len(pr.get('scenes') or [])} scenes · "
                f"{len(pr.get('shots') or [])} shots · parts: {', '.join(r['name'] for r in pr.get('roles') or []) or 'none'}\n\n" + (sp.get("text") or "(no screenplay yet)"))


@tool("studio_screenplay_set",
      "Replace a project's screenplay (Fountain text: write the whole screenplay, e.g. after studio_screenplay_get and your changes). "
      "The text it had is kept as a draft the user can compare or restore. The breakdown is made again, keeping what was decided on "
      "shots that stay (their frames, cameras and lengths); new places become locations and new speaking parts are cast; a part or "
      "place that's gone goes if nothing was done with it.",
      {"project": P_PROJECT, "text": {"type": "string", "description": "The whole screenplay in Fountain"},
       "draft_name": {"type": "string", "description": "A name for the draft keeping the previous text"}}, ["project", "text"])
def _screenplay_set(a):
    pr = project_ref(a["project"])
    return text(http("POST", f"/studio/api/projects/{pr['id']}/screenplay", {"text": a["text"], "draft": a.get("draft_name")}, timeout=300))


@tool("studio_screenplay_export", "Save a project's screenplay as a file on this PC: PDF on the Hollywood standard page, Final Draft (.fdx) or plain text.",
      {"project": P_PROJECT, "format": {"type": "string", "enum": ["pdf", "fdx", "txt"]},
       "path": {"type": "string", "description": "Where to save it (a file or a folder); default: the Downloads folder"}}, ["project"])
def _screenplay_export(a):
    pr = project_ref(a["project"])
    fmt = a.get("format") or "pdf"
    req = urllib.request.Request(f"{BASE}/studio/api/projects/{pr['id']}/screenplay.{fmt}", headers=AI_HEADERS)
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    name = re.sub(r'[\\/:*?"<>|]+', " ", pr.get("title") or "Screenplay").strip() or "Screenplay"
    dest = a.get("path") or os.path.join(os.path.expanduser("~"), "Downloads")
    if os.path.isdir(dest) or not os.path.splitext(dest)[1]:
        os.makedirs(dest, exist_ok=True)
        dest = os.path.join(dest, f"{name}.{fmt}")
    with open(dest, "wb") as f:
        f.write(data)
    return text(f"Saved {dest} ({len(data) // 1024} KB)")


@tool("studio_breakdown",
      "A project's breakdown, as a breakdown sheet: its scenes (number, heading, interior/exterior, place, time of day, length in "
      "eighths of a page, who's in it) and each scene's shots (code like 1A, title, action, camera, length in seconds, who's in it, the "
      "lines spoken, and its storyboard frame if it has one).",
      {"project": P_PROJECT, "scene": {"type": "string", "description": "Only this scene (its number)"}}, ["project"])
def _breakdown(a):
    pr = project_ref(a["project"])
    codes = codes_of(pr)
    out = []
    for sc in pr.get("scenes") or []:
        if a.get("scene") and str(sc.get("number")) != str(a["scene"]):
            continue
        shots = [s for s in pr.get("shots") or [] if s.get("scene") == sc["id"]]
        out.append({"scene": sc.get("number"), "heading": sc.get("heading"), "int_ext": sc.get("intext"), "place": sc.get("location"),
                    "time": sc.get("time"), "eighths": sc.get("eighths"), "omitted": sc.get("omitted") or None, "summary": sc.get("summary"),
                    "shots": [{"code": codes.get(s["id"]), "id": s["id"], "title": s.get("title"), "action": s.get("action"), "camera": s.get("camera"),
                               "seconds": s.get("seconds"), "characters": s.get("characters"),
                               "lines": [f"{ln.get('who')}: {ln.get('text')}" for ln in s.get("lines") or []],
                               "frame": input_path(s.get("keyframe"))} for s in shots]})
    return text(out or "No scenes yet: the breakdown comes from the screenplay's scene headings.")


@tool("studio_shot_update", "Change a shot of a project's breakdown: its camera, its length, or its storyboard frame (a picture file on this PC, or '' for none).",
      {"project": P_PROJECT, "shot": {"type": "string", "description": "The shot's code (1A) or id"},
       "camera": {"type": "string", "enum": ["static", "push in", "pull back", "pan left", "pan right", "tracking", "orbit", "crane up", "crane down",
                                             "handheld", "close-up", "low angle", "drone", "slow motion", "wide", "pov", "over the shoulder", "high angle"]},
       "seconds": {"type": "number"}, "frame": {"type": "string", "description": "A picture file on this PC for its frame, or '' to clear it"}}, ["project", "shot"])
def _shot_update(a):
    pr = project_ref(a["project"])
    body = {k: a[k] for k in ("camera", "seconds") if a.get(k)}
    if a.get("frame") is not None:
        body["frame"] = as_input(a["frame"]) if a["frame"] else ""
    return text(http("POST", f"/studio/api/projects/{pr['id']}/shots/{_urlparse.quote(str(a['shot']))}", body))


# ---------------------------------------------------------------- cast and characters
def char_view(c):
    return {"id": c["id"], "name": c["name"], "description": c.get("description"), "sheet": {k: c[k] for k in SHEET if c.get(k)},
            "face": input_path(c.get("face")), "pictures": len(c.get("pictures") or []), "voice": bool(c.get("voice")),
            "lora": (c.get("lora") or {}).get("name")}


@tool("studio_cast", "A project's cast: every speaking part in its screenplay (how many lines, in which scenes, how the screenplay introduces them) and the Studio character playing it (their look as the generators get it, their face picture).",
      {"project": P_PROJECT}, ["project"])
def _cast(a):
    pr = project_ref(a["project"])
    chars = {c["id"]: c for c in http("GET", "/studio/api/characters")}
    cm = pr.get("castMap") or {}
    return text([{"part": r["name"], "full_name": r.get("full"), "lines": r.get("lines"), "scenes": len(r.get("scenes") or []), "introduced": r.get("intro"),
                  "played_by": char_view(chars[cm[r["name"]]]) if cm.get(r["name"]) in chars else ("left uncast" if cm.get(r["name"]) == "" else None)}
                 for r in pr.get("roles") or []])


@tool("studio_cast_role", "Cast a part of a project's screenplay with a Studio character (by name or id), or '' to leave it uncast.",
      {"project": P_PROJECT, "part": {"type": "string", "description": "The part, as the screenplay's character cue writes it (RAY)"},
       "character": {"type": "string"}}, ["project", "part", "character"])
def _cast_role(a):
    pr = project_ref(a["project"])
    return text(http("POST", f"/studio/api/projects/{pr['id']}/cast", {"role": a["part"], "character": a["character"]}))


@tool("studio_character_save",
      "Create a character, or change one (give character: its name or id). The character sheet is what every picture and shot of them "
      "gets: age, gender, build, skin, features (face), eyes, hair, facial_hair, marks, wardrobe, accessories, look (anything else), and a "
      "LoRA trigger word. face: a picture file on this PC to use as their face (a reference for their frames); voice: a 5-15 s recording "
      "of their voice for the dialogue. Fields not given stay as they are.",
      dict({"character": {"type": "string", "description": "An existing character to change (name or id); omit to create one"},
            "name": {"type": "string"}, "face": {"type": "string", "description": "A picture file on this PC"},
            "voice": {"type": "string", "description": "A sound file on this PC"}},
           **{k: {"type": "string"} for k in SHEET}))
def _character_save(a):
    c = character_ref(a["character"]) if a.get("character") else {"name": "", "private": False}
    c.pop("description", None)
    if a.get("name"):
        c["name"] = a["name"].strip()
    if not c.get("name"):
        raise RuntimeError("Give the character a name")
    for k in SHEET:
        if a.get(k) is not None:
            c[k] = a[k]
    if a.get("face"):
        c["face"] = as_input(a["face"])
        c["pictures"] = [c["face"]] + [p for p in c.get("pictures") or [] if p != c["face"]]
    if a.get("voice"):
        c["voice"] = as_input(a["voice"])
    saved = http("POST", "/studio/api/characters", c)
    return text(char_view(dict(saved, description=next((x.get("description") for x in http("GET", "/studio/api/characters") if x["id"] == saved["id"]), ""))))


@tool("studio_character_delete", "Delete a character (their pictures stay in the gallery).", {"character": {"type": "string"}}, ["character"])
def _character_delete(a):
    c = character_ref(a["character"])
    return text(http("DELETE", f"/studio/api/characters/{c['id']}"))


@tool("studio_character_picture",
      "A reference picture of a character, made with the Image app from their character sheet (head and shoulders, plain background); their "
      "face, if they have one, keeps them the same. The first one becomes their face (a reference for every frame they're in); "
      "make_face=true makes this one their face anyway. Returns the picture so you can look at it.",
      {"character": {"type": "string"}, "model": P_MODEL, "make_face": {"type": "boolean"}, "prompt": {"type": "string", "description": "Instead of the sheet's"},
       "run_on": P_RUN_ON, "wait": P_WAIT, "timeout": P_TIMEOUT}, ["character"])
def _character_picture(a):
    c = character_ref(a["character"])
    body = {k: a[k] for k in ("model", "prompt") if a.get(k)}
    body["target"] = a.get("run_on") or "local"
    r = http("POST", f"/studio/api/characters/{c['id']}/portrait", body)
    res, msg = job_result(r["id"], a.get("wait", True), float(a.get("timeout") or 900), f"Picturing {c['name']}")
    if msg:
        return text(msg)
    if a.get("make_face"):
        c2 = character_ref(c["id"])
        c2.pop("description", None)
        c2["face"] = res["picture"]
        http("POST", "/studio/api/characters", c2)
    return picture_content(input_path(res["picture"]), f"{c['name']}: {input_path(res['picture'])}")


# ---------------------------------------------------------------- locations
def loc_view(pr, x):
    num = {s["id"]: s.get("number") for s in pr.get("scenes") or []}
    return {"id": x["id"], "name": x.get("full") or x.get("name"), "heading_name": x.get("name"), "scenes": [num.get(i) for i in x.get("scenes") or []],
            "int_ext": x.get("intext"), **{k: x.get(k) for k in ("description", "look", "lighting", "time", "atmosphere", "sound") if x.get(k)},
            "picture": input_path(x.get("picture")), "pictures": len(x.get("pictures") or []), "gone_from_screenplay": bool(x.get("gone")) or None}


@tool("studio_locations", "A project's locations: every place its scene headings name, with its scenes, and its form: description, look (what the generator gets: written into every shot set there), lighting, time of day, atmosphere, sound, and its reference picture.",
      {"project": P_PROJECT}, ["project"])
def _locations(a):
    pr = project_ref(a["project"])
    return text([loc_view(pr, x) for x in pr.get("locations") or []] or "No locations yet: the scene headings make them.")


@tool("studio_location_update",
      "Fill in or change a location's form: description, look (how it looks in a few words: written into the prompt of every shot set "
      "there, like a character's look), lighting, time, atmosphere, sound, a picture file on this PC as its reference picture, or a new name "
      "(renamed in the screenplay's scene headings too, unless rename_in_screenplay=false).",
      {"project": P_PROJECT, "location": {"type": "string", "description": "Its name or id"}, "name": {"type": "string"},
       **{k: {"type": "string"} for k in ("description", "look", "lighting", "time", "atmosphere", "sound")},
       "picture": {"type": "string", "description": "A picture file on this PC"}, "rename_in_screenplay": {"type": "boolean"}}, ["project", "location"])
def _location_update(a):
    pr = project_ref(a["project"])
    body = {k: a[k] for k in ("name", "description", "look", "lighting", "time", "atmosphere", "sound", "rename_in_screenplay") if a.get(k) is not None}
    if a.get("picture"):
        body["picture"] = as_input(a["picture"])
    r = http("POST", f"/studio/api/projects/{pr['id']}/locations/{_urlparse.quote(a['location'])}", body)
    return text({"location": loc_view(project_ref(pr["id"]), r["location"]), "renamed_in_headings": r.get("renamed_in_headings")})


@tool("studio_location_picture",
      "A reference picture of a location, made with the Image app from its form (the place, its look, light, time and atmosphere; an empty "
      "set, no people). The first one becomes its picture: the reference for the frames of shots set there with nobody in them.",
      {"project": P_PROJECT, "location": {"type": "string"}, "model": P_MODEL, "prompt": {"type": "string", "description": "Instead of the form's"},
       "run_on": P_RUN_ON, "wait": P_WAIT, "timeout": P_TIMEOUT}, ["project", "location"])
def _location_picture(a):
    pr = project_ref(a["project"])
    r = str(a["location"]).strip().upper()
    loc = next((x for x in pr.get("locations") or [] if x["id"] == a["location"] or (x.get("name") or "").upper() == r or (x.get("full") or "").upper() == r), None) \
        or next((x for x in pr.get("locations") or [] if r in (x.get("name") or "").upper()), None)
    if not loc:
        raise RuntimeError("No such location. Locations: " + ", ".join(x.get("full") or x.get("name") for x in pr.get("locations") or []))
    body = {k: a[k] for k in ("model", "prompt") if a.get(k)}
    body["target"] = a.get("run_on") or "local"
    j = http("POST", f"/studio/api/projects/{pr['id']}/locations/{loc['id']}/picture", body)
    res, msg = job_result(j["id"], a.get("wait", True), float(a.get("timeout") or 900), f"Picturing {loc.get('full')}")
    if msg:
        return text(msg)
    return picture_content(input_path(res["picture"]), f"{loc.get('full')}: {input_path(res['picture'])}")


# ---------------------------------------------------------------- the storyboard's frames
@tool("studio_storyboard_frames",
      "Make a project's storyboard frames with the Image app: one still per shot, from its action with its characters' looks and its "
      "location's look written in, in the project's style. A shot with one cast character who has a face uses it as a reference; a shot "
      "with nobody in it uses its location's picture. By default only the shots without a frame; shots: only these (codes like 1A); "
      "redo: all again (the frames they had are kept as versions). The frames are the takes' first frames.",
      {"project": P_PROJECT, "shots": {"type": "array", "items": {"type": "string"}}, "redo": {"type": "boolean"}, "model": P_MODEL,
       "run_on": P_RUN_ON, "wait": P_WAIT, "timeout": P_TIMEOUT}, ["project"])
def _storyboard_frames(a):
    pr = project_ref(a["project"])
    body = {"private": False, "model": a.get("model"), "redo": bool(a.get("redo")), "only": a.get("shots") or [], "target": a.get("run_on") or "local"}
    j = http("POST", f"/studio/api/projects/{pr['id']}/keyframes", body)
    res, msg = job_result(j["id"], a.get("wait", True), float(a.get("timeout") or 3600), "Making the frames")
    if msg:
        return text(msg)
    pr = project_ref(pr["id"])
    codes = codes_of(pr)
    want = {str(x).upper() for x in a.get("shots") or []}
    frames = [(codes.get(s["id"]), input_path(s.get("keyframe"))) for s in pr.get("shots") or [] if s.get("keyframe") and (not want or str(codes.get(s["id"])).upper() in want)]
    out = text({"made": res.get("done"), "frames": [{"shot": c, "frame": p} for c, p in frames]})
    for c, p in frames[:4]:
        out += picture_content(p, f"Shot {c}")
    return out


# ---------------------------------------------------------------- takes
def board_of(pr):
    if not pr.get("board"):
        raise RuntimeError("No takes yet: draft them with studio_takes_draft")
    return pr["board"]


def takes_view(board):
    for s in board["shots"]:
        for t in s["takes"]:
            if t.get("file"):
                t["path"] = abs_out(dict(t["file"], private=False))
    return board


@tool("studio_takes_draft",
      "Draft a project's takes with the Video app (each shot's prompt, camera, length, and its storyboard frame as the first frame), as "
      "the Takes page does: the storyboard follows the breakdown first. By default every shot without takes; shots: only these (codes); "
      "count: takes per shot (default 2); quality: draft (fast) or final. The first finished take of a shot is its pick. Video takes "
      "time: wait=false returns at once, then studio_takes shows how they're going.",
      {"project": P_PROJECT, "shots": {"type": "array", "items": {"type": "string"}}, "count": {"type": "integer"},
       "quality": {"type": "string", "enum": ["draft", "final"]}, "model": {"type": "string", "description": "The Video app's model (part of its name), e.g. 'LTX', 'MiniMax H3', 'Wan'"},
       "values": {"type": "object", "description": "Other Video app options by label, e.g. {\"Aspect\": \"9:16\"}"}, "run_on": P_RUN_ON,
       "wait": {"type": "boolean", "description": "Wait for the takes (default false)"}, "timeout": P_TIMEOUT}, ["project"])
def _takes_draft(a):
    pr = project_ref(a["project"])
    body = {k: a[k] for k in ("shots", "count", "quality", "model", "values") if a.get(k)}
    body["target"] = a.get("run_on") or "local"
    r = http("POST", f"/studio/api/projects/{pr['id']}/takes", body, timeout=300)
    if not a.get("wait"):
        return text({"queued": r["queued"], "board": r["board"], "next": "studio_takes shows them as they finish"})
    t0, limit = time.time(), float(a.get("timeout") or 3600)
    while True:
        b = http("POST", f"/studio/api/boards/{r['board']}/resolve", {})
        state = {t.get("prompt_id"): t.get("status") for s in b["shots"] for t in s["takes"]}
        if all(state.get(q["prompt_id"]) in ("done", "failed") for q in r["queued"]) or time.time() - t0 > limit:
            break
        time.sleep(POLL * 2.5)
    return text(takes_view(b))


@tool("studio_takes", "A project's takes, shot by shot: each take's state and file, which one is picked (the film uses it), and the finished version if there is one.",
      {"project": P_PROJECT}, ["project"])
def _takes(a):
    pr = project_ref(a["project"])
    return text(takes_view(http("POST", f"/studio/api/boards/{board_of(pr)}/resolve", {})))


@tool("studio_take_pick", "Pick a shot's take (the film uses the pick). Takes are numbered from 0, as studio_takes lists them.",
      {"project": P_PROJECT, "shot": {"type": "string", "description": "The shot's code (1A)"}, "take": {"type": "integer"}}, ["project", "shot", "take"])
def _take_pick(a):
    pr = project_ref(a["project"])
    return text(takes_view(http("POST", f"/studio/api/boards/{board_of(pr)}/shots/{_urlparse.quote(str(a['shot']))}/pick", {"take": a["take"]})))


# ---------------------------------------------------------------- sound and the film
@tool("studio_project_music", "The film's score: an instrumental the film's length, in the style given (genre, mood, instruments, tempo), kept with the project (its music bed). engine 'ace' (fast) or 'music3' (best).",
      {"project": P_PROJECT, "style": {"type": "string"}, "engine": {"type": "string", "enum": ["ace", "music3"]}, "seconds": {"type": "number"},
       "run_on": P_RUN_ON, "wait": P_WAIT, "timeout": P_TIMEOUT}, ["project"])
def _project_music(a):
    pr = project_ref(a["project"])
    j = http("POST", f"/studio/api/projects/{pr['id']}/music", {k: a[k] for k in ("style", "engine", "seconds") if a.get(k)} | {"target": a.get("run_on") or "local"})
    res, msg = job_result(j["id"], a.get("wait", True), float(a.get("timeout") or 5400), "Composing")
    return text(msg or dict(res, path=abs_out(res)))


@tool("studio_project_voices", "Every line of the screenplay's dialogue spoken in its character's voice (their voice recording, when they have one), kept with its shot. Only the lines without a voice yet, unless redo.",
      {"project": P_PROJECT, "redo": {"type": "boolean"}, "run_on": P_RUN_ON, "wait": P_WAIT, "timeout": P_TIMEOUT}, ["project"])
def _project_voices(a):
    pr = project_ref(a["project"])
    j = http("POST", f"/studio/api/projects/{pr['id']}/voices", {"redo": bool(a.get("redo")), "target": a.get("run_on") or "local"})
    res, msg = job_result(j["id"], a.get("wait", True), float(a.get("timeout") or 7200), f"Speaking {j.get('lines')} lines")
    return text(msg or res)


@tool("studio_project_film",
      "Cut the film: the storyboard's shots in order, each one's finished version or picked take trimmed to the shot's length, with an "
      "opening title, an end card, crossfades and a music bed ('project' = the project's score, a sound file on this PC, or 'none').",
      {"project": P_PROJECT, "title": {"type": "string", "description": "The opening title ('' for none; default the film's title)"},
       "subtitle": {"type": "string"}, "end": {"type": "string", "description": "The end card ('' for none)"},
       "crossfade": {"type": "number", "description": "Seconds, 0 for hard cuts (default 0.5)"}, "music": {"type": "string"},
       "music_volume": {"type": "number", "description": "0..1 (default 0.35)"}, "keep_audio": {"type": "boolean", "description": "Keep the takes' own sound"},
       "wait": P_WAIT, "timeout": P_TIMEOUT}, ["project"])
def _project_film(a):
    pr = project_ref(a["project"])
    body = {k: a[k] for k in ("title", "subtitle", "end", "crossfade", "keep_audio") if a.get(k) is not None}
    if a.get("music_volume") is not None:
        body["music_vol"] = a["music_volume"]
    if a.get("music"):
        body["music"] = "project" if a["music"] == "project" else "" if a["music"] == "none" else as_input(a["music"])
    j = http("POST", f"/studio/api/projects/{pr['id']}/film", body)
    res, msg = job_result(j["id"], a.get("wait", True), float(a.get("timeout") or 3600), f"Cutting {j.get('shots')} of {j.get('of')} shots")
    return text(msg or dict(res, path=abs_out(res), shots=j.get("shots"), of=j.get("of")))


# ---------------------------------------------------------------- the AI tools (Production › AI tools)
@tool("studio_ai_tools", "The AI tools (AI Depth, AI Slow-Mo, AI Stems, AI Subtitles, AI Lossless Cut, One-Click Master): what each takes and its options, and whether it's ready on this PC.")
def _ai_tools(a):
    return text([{"id": t["id"], "name": t["name"], "what": t.get("short"), "takes": t.get("accepts") + (" or a sound" if t.get("audio_ok") else "") + (" or a picture" if t.get("image_ok") else ""),
                  "range": {"section": "a section (start_frame, end_frame) or the whole clip", "segments": "pieces [[start_s, end_s], ...]", "whole": "the whole file"}[t["range"]],
                  "options": [{"name": o["name"], "label": o.get("label"), "type": o.get("type"), "default": o.get("default"),
                               **({"choices": [c["value"] for c in o["choices"]]} if o.get("choices") else {}),
                               **({"min": o.get("min"), "max": o.get("max")} if o.get("type") == "slider" else {})} for o in t.get("options") or []],
                  "not_ready": t.get("unavailable")} for t in http("GET", "/studio/api/generators/tools")["tools"]])


@tool("studio_ai_tool_run",
      "Run an AI tool on a file: a path on this PC, or a gallery file (filename + subfolder). Options by name (studio_ai_tools). AI Slow-Mo: "
      "start_frame/end_frame for a section; AI Lossless Cut: pieces [[start, end], ...] in seconds. The results land in the gallery under "
      "Generators/<tool>.",
      {"tool": {"type": "string", "description": "Its id or name: depth, slowmo, stems, subtitles, cut, master"}, "path": {"type": "string"},
       "filename": {"type": "string"}, "subfolder": {"type": "string"}, "options": {"type": "object"},
       "start_frame": {"type": "integer"}, "end_frame": {"type": "integer", "description": "Exclusive"},
       "pieces": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}}, "wait": P_WAIT, "timeout": P_TIMEOUT}, ["tool"])
def _ai_tool_run(a):
    tools = http("GET", "/studio/api/generators/tools")["tools"]
    w = str(a["tool"]).lower()
    t = next((x for x in tools if x["id"] == w or x["name"].lower() == w), None) or next((x for x in tools if w in x["name"].lower()), None)
    if not t:
        raise RuntimeError("No such AI tool: " + ", ".join(x["id"] for x in tools))
    if a.get("path"):
        path = a["path"]
        if not os.path.isfile(path):
            raise RuntimeError("No such file: " + path)
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            req = urllib.request.Request(BASE + "/studio/api/generators/inputs/import", data=f, method="POST",
                                         headers={"X-File-Name": _urlparse.quote(os.path.basename(path)), "Content-Length": str(size),
                                                  "Content-Type": "application/octet-stream", **AI_HEADERS})
            try:
                with urllib.request.urlopen(req, timeout=3600) as r:
                    inp = json.loads(r.read())["input"]
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"Studio answered {e.code}: {_said(e.read())}")
    elif a.get("filename"):
        inp = http("POST", "/studio/api/generators/inputs", {"filename": a["filename"], "subfolder": a.get("subfolder") or ""})["input"]
    else:
        raise RuntimeError("Give the file: path, or filename + subfolder")
    body = {"tool": t["id"], "input_id": inp["id"], "options": a.get("options") or {}}
    if a.get("start_frame") is not None and a.get("end_frame") is not None:
        body.update(start=int(a["start_frame"]), end=int(a["end_frame"]))
    if a.get("pieces"):
        body["segments"] = a["pieces"]
    j = http("POST", "/studio/api/generators/jobs", body)["job"]
    if not a.get("wait", True):
        return text({"job": j["id"], "status": j["status"], "next": "studio_ai_tool_jobs shows how it's going"})
    t0, limit = time.time(), float(a.get("timeout") or 3600)
    while j["status"] in ("running", "queued") and time.time() - t0 < limit:
        time.sleep(POLL)
        j = http("GET", f"/studio/api/generators/jobs/{j['id']}")["job"]
    return text(ai_job_view(j))


def ai_job_view(j):
    out = {k: j.get(k) for k in ("id", "tool_name", "input_name", "status", "stage", "error", "note", "stats", "pieces") if j.get(k) not in (None, [], {})}
    out["files"] = [abs_out({"filename": o["name"], "subfolder": o["subfolder"], "private": False}) for o in j.get("outputs") or []]
    return out


@tool("studio_ai_tool_jobs", "The AI tools' jobs, newest first (or one, by id): state, what each measured, its files.",
      {"id": {"type": "string"}, "cancel": {"type": "boolean", "description": "Stop that job"}})
def _ai_tool_jobs(a):
    if a.get("id") and a.get("cancel"):
        return text(ai_job_view(http("POST", f"/studio/api/generators/jobs/{a['id']}/cancel", {})["job"]))
    if a.get("id"):
        return text(ai_job_view(http("GET", f"/studio/api/generators/jobs/{a['id']}")["job"]))
    return text([ai_job_view(j) for j in http("GET", "/studio/api/generators/jobs")["jobs"][:20]])


# ---------------------------------------------------------------- the gallery: look at things
@tool("studio_view",
      "Look at a picture or a video (a frame of it): a path on this PC, a gallery file (filename + subfolder), or an input value (as "
      "frames and faces are kept, e.g. 'studio/frame_00001_.png'). Returns the picture, so you can judge a frame, a portrait or a take.",
      {"path": {"type": "string"}, "filename": {"type": "string"}, "subfolder": {"type": "string"}, "input": {"type": "string"},
       "size": {"type": "integer", "description": "Longest side in pixels (default 1024)"}})
def _view(a):
    size = int(a.get("size") or 1024)
    if a.get("filename"):
        q = _urlparse.urlencode({"filename": a["filename"], "subfolder": a.get("subfolder") or "", "w": 1600 if size > 640 else 640})
        req = urllib.request.Request(f"{BASE}/studio/api/thumb?{q}", headers=AI_HEADERS)
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        return [{"type": "text", "text": f"{a.get('subfolder') or ''}/{a['filename']}"},
                {"type": "image", "data": base64.b64encode(data).decode(), "mimeType": "image/webp"}]
    path = a.get("path") or (input_path(a["input"]) if a.get("input") else None)
    if not path or not os.path.isfile(path):
        raise RuntimeError("Give a picture: path, filename + subfolder, or input")
    if path.lower().endswith((".mp4", ".mov", ".webm", ".mkv", ".m4v")):
        import subprocess
        import tempfile
        ff = os.environ.get("STUDIO_FFMPEG") or "ffmpeg"
        with tempfile.TemporaryDirectory() as d:
            png = os.path.join(d, "frame.png")
            subprocess.run([ff, "-v", "error", "-y", "-ss", "0.5", "-i", path, "-frames:v", "1", png], capture_output=True)
            out = picture_content(png, path)
        return out or text("Couldn't read a frame of " + path)
    t = image_content(path, size)
    if not t:
        raise RuntimeError("Not a picture this can show: " + path)
    return [{"type": "text", "text": path}, t]


@tool("studio_file_info", "What's known about a gallery file: the prompt and settings it was made with, its size in pixels, when it was made.",
      {"filename": {"type": "string"}, "subfolder": {"type": "string"}}, ["filename"])
def _file_info(a):
    q = _urlparse.urlencode({"filename": a["filename"], "subfolder": a.get("subfolder") or ""})
    return text(http("GET", f"/studio/api/meta?{q}"))


@tool("studio_gallery_action", "Star a gallery file as a favorite (or not), or move it to the trash (a _trash folder: nothing is lost).",
      {"filename": {"type": "string"}, "subfolder": {"type": "string"}, "action": {"type": "string", "enum": ["favorite", "unfavorite", "trash"]}}, ["filename", "action"])
def _gallery_action(a):
    f = {"filename": a["filename"], "subfolder": a.get("subfolder") or "", "private": False}
    if a["action"] == "trash":
        return text(http("POST", "/studio/api/trash", f))
    return text(http("POST", "/studio/api/favorite", dict(f, on=a["action"] == "favorite")))


# ---------------------------------------------------------------- the local AI (Qwen3-VL on this PC)
@tool("studio_ai_review", "The local AI watches a take (a video in the gallery, or a path) and reviews it: scores for prompt match, motion, anatomy and artifacts, a verdict, and a better prompt.",
      {"filename": {"type": "string"}, "subfolder": {"type": "string"}, "path": {"type": "string"}, "prompt": {"type": "string", "description": "What the take was meant to show"}})
def _ai_review(a):
    f = gallery_file(a)
    j = http("POST", "/studio/api/ai/review", dict(f, prompt=a.get("prompt") or ""))
    return text(job_wait(j["id"], 900))


@tool("studio_ai_prompt", "The local AI turns an idea into a strong prompt for a picture or a video (optionally from a starting picture: an input value).",
      {"idea": {"type": "string"}, "kind": {"type": "string", "enum": ["image", "video"]}, "image": {"type": "string"}}, ["idea"])
def _ai_prompt(a):
    j = http("POST", "/studio/api/ai/prompt", {"idea": a["idea"], "kind": a.get("kind") or "image", "image": a.get("image") or ""})
    return text(job_wait(j["id"], 600))


@tool("studio_ai_write", "The screenplay editor's writing help, with the local AI: rewrite a passage (with an instruction), continue a scene, polish dialogue, turn prose into screenplay, or a one-line synopsis. Answers in Fountain.",
      {"mode": {"type": "string", "enum": ["rewrite", "continue", "dialogue", "prose", "synopsis"]}, "text": {"type": "string"},
       "context": {"type": "string", "description": "The scene around it"}, "instruction": {"type": "string"}}, ["mode", "text"])
def _ai_write(a):
    j = http("POST", "/studio/api/ai/write", {k: a.get(k) or "" for k in ("mode", "text", "context", "instruction")})
    return text(job_wait(j["id"], 900))


# ---------------------------------------------------------------- reel, Previz
@tool("studio_reel", "A reel cut to a song's beat: gallery pictures and clips, in order, each cut on the beat (aspect 9:16, 1:1 or 16:9; length in seconds; cut: about how long each holds).",
      {"song": {"type": "string", "description": "A sound file on this PC, or an input value"}, "items": {"type": "array", "items": {"type": "object"}, "description": "[{filename, subfolder}] from the gallery"},
       "aspect": {"type": "string", "enum": ["9:16", "1:1", "16:9"]}, "length": {"type": "number"}, "cut": {"type": "number"}, "wait": P_WAIT}, ["song", "items"])
def _reel(a):
    j = http("POST", "/studio/api/reel", {"song": as_input(a["song"]), "items": a["items"], "aspect": a.get("aspect") or "9:16",
                                          "length": a.get("length") or 30, "cut": a.get("cut") or 1.2})
    res, msg = job_result(j["id"], a.get("wait", True), 1800, "Cutting the reel")
    return text(msg or dict(res, path=abs_out(res)))


@tool("studio_previz_add", "Add a 3D set (.glb on this PC) to Previz, used where it is. Directing it (cameras, moves, light) and its snapshots and movies happen in the page's 3D viewer.",
      {"path": {"type": "string"}, "name": {"type": "string"}, "kind": {"type": "string", "enum": ["set", "prop"]}}, ["path"])
def _previz_add(a):
    g = http("POST", "/studio/api/glb/add", {"path": a["path"], "name": a.get("name"), "kind": a.get("kind") or "set"}, timeout=600)
    i = g.get("info") or {}
    return text({"id": g.get("id"), "name": g.get("name"), "cameras": [c["name"] for c in i.get("cameras") or []], "seconds": i.get("duration")})


def multipart(path, fields, file_field, filename, data):
    boundary = _uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f'Content-Type: application/octet-stream\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(BASE + path, data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **AI_HEADERS})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Studio answered {e.code}: {_said(e.read())}")


GUIDE += """

Making a film (each step is a page of the project in the app, and shows there as you go)
1. studio_project_new with the screenplay (write it in Fountain, or give a PDF/FDX/Fountain file). It's broken down into scenes and
   shots; every place in the scene headings becomes a location, every speaking part is cast (a character made from how the
   screenplay introduces them). Change the screenplay any time with studio_screenplay_get / studio_screenplay_set: all of it follows.
2. studio_breakdown to read the scenes and shots; studio_shot_update for a shot's camera or length.
3. studio_cast, then fill in each character's sheet (studio_character_save: age, build, hair, wardrobe…) and make their reference
   picture (studio_character_picture): it becomes their face, which keeps them the same in every frame. studio_view to look at it.
4. studio_locations, then each location's look (studio_location_update) and its picture (studio_location_picture).
5. studio_storyboard_frames: a frame per shot (the first frames of the takes).
6. studio_takes_draft, studio_takes, studio_take_pick: the shots' videos.
7. studio_project_music and studio_project_voices, then studio_project_film: the film.
Long steps run in the background with wait=false; studio_job / studio_takes / studio_status tell how they're going.
Pictures and videos are files on this PC: studio_view shows you any of them."""


_call_v2 = call


def call(name, a):
    if name in V3:
        return V3[name](a or {})
    return _call_v2(name, a)


# ================================================================ the protocol (stdio here; HTTP in mcp_http.py)
INSTRUCTIONS = ("Studio is the user's film studio app (on ComfyUI, on their PC): screenplays, projects broken down into scenes and shots, "
                "characters and locations with reference pictures, storyboards, takes, music, voices, the film edit, pictures and videos with "
                "any app, and AI tools (depth, slow motion, stems, subtitles, lossless cut, mastering). Everything made here shows in the app. "
                "Call studio_overview first: it explains the steps from a story to a film.")


def handle(m):
    """One JSON-RPC message -> its answer (None for a notification)."""
    if not isinstance(m, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    mid, method = m.get("id"), m.get("method")
    if mid is None:
        return None            # notifications (initialized, cancelled…)
    try:
        if method == "initialize":
            res = {"protocolVersion": (m.get("params") or {}).get("protocolVersion") or PROTOCOL,
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "studio", "version": "3.0"}, "instructions": INSTRUCTIONS}
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            p = m.get("params") or {}
            try:
                res = {"content": call(p.get("name"), p.get("arguments") or {})}
            except Exception as e:
                res = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        elif method == "ping":
            res = {}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}}
        return {"jsonrpc": "2.0", "id": mid, "result": res}
    except Exception as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}



def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    """MCP over stdio: a JSON-RPC message a line, each answered on stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        res = handle(m)
        if res is not None:
            send(res)


if __name__ == "__main__":
    main()
