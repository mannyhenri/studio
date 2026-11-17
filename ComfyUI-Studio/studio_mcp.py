"""Studio connector for Claude (MCP over stdio, no extra packages).

Lets Claude generate with any Studio app, run projects and the storyboard, read characters and Previz sets, and bring
final renders (e.g. Seedance through the Higgsfield Bridge) back into Studio.

Claude Desktop / Cowork config ("mcpServers"):
  "studio": {"command": "<ComfyUI>\\.venv\\Scripts\\python.exe",
             "args": ["<ComfyUI>\\custom_nodes\\ComfyUI-Studio\\studio_mcp.py"]}
Studio must be running (ComfyUI on this PC). Only regular (non-Incognito) items are visible here.
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


def http(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Studio answered {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Studio isn't reachable at {BASE} ({e.reason}). Is ComfyUI running?")


def wait_job(jid, limit=1800):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = http("GET", f"/studio/api/job/{jid}")
        if j.get("status") == "done":
            return j.get("result")
        if j.get("status") in ("failed", "lost"):
            raise RuntimeError(j.get("error") or j.get("stage"))
        time.sleep(2)
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
    {"name": "studio_project_create", "description": "Start a project from an idea, without a screenplay: the local AI writes a shot list (write_script=true, waits). cast = character names from studio_list_characters. A project from a screenplay is made by importing it on the Projects page (PDF or Fountain); its scenes and shots come from the screenplay.",
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

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        mid, method = m.get("id"), m.get("method")
        if mid is None:
            continue   # notifications
        try:
            if method == "initialize":
                res = {"protocolVersion": (m.get("params") or {}).get("protocolVersion") or PROTOCOL,
                       "capabilities": {"tools": {}}, "serverInfo": {"name": "studio", "version": "2.0"},
                       "instructions": "Studio is the user's ComfyUI studio app on this PC. Use these tools to generate pictures, videos, music and voices with any Studio app, run projects and the storyboard, read Previz sets, and bring final renders (e.g. Seedance via Higgsfield) back into Studio. Call studio_overview first."}
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
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}})
                continue
            send({"jsonrpc": "2.0", "id": mid, "result": res})
        except Exception as e:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
