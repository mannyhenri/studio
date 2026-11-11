"""Studio connector for Claude (MCP over stdio, no extra packages).

Lets Claude read Studio's locations and characters, register 3D sets it builds in Blender
(for example through the Higgsfield Bridge), plan scenes and render them.

Claude Desktop / Cowork config ("mcpServers"):
  "studio": {"command": "<ComfyUI>\\.venv\\Scripts\\python.exe",
             "args": ["<ComfyUI>\\custom_nodes\\ComfyUI-MannyStudio\\studio_mcp.py"]}
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
        j = http("GET", f"/manny/api/job/{jid}")
        if j.get("status") == "done":
            return j.get("result")
        if j.get("status") in ("failed", "lost"):
            raise RuntimeError(j.get("error") or j.get("stage"))
        time.sleep(2)
    raise RuntimeError("Timed out waiting for Studio")


_info = None


def info():
    global _info
    if _info is None:
        _info = http("GET", "/manny/api/blender/status")
    return _info


def input_dir_guess():
    return os.environ.get("STUDIO_INPUT_DIR") or os.path.join(os.path.expanduser("~"), "AppData", "Local", "Comfy-Desktop", "ComfyUI-Shared", "input")


def abs_input(v):
    try:
        root = http("GET", "/manny/api/paths")["input"]
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


GUIDE = """How Studio uses 3D sets
- A set is a .blend file. Save new sets in the Studio Sets folder ({folder}), then call studio_register_set.
- Scale: metres, Z up, the playable floor centred near the origin, the main view looking toward +Y.
- Name cameras clearly (e.g. "Shot 01 wide", "Shot 02 close"). Animated cameras become shots with their move.
  Timeline markers bound to cameras (Ctrl+B in Blender) also become shots, each running to the next marker.
- Keep heavy lights and materials: the "lit" render uses the file's own look (EEVEE). The "grey" render is a fast blocking view.
- Studio adds its own mannequins for the cast and its own cameras for planned moves; objects named Studio_* are Studio's.
- After registering, Studio shows a top-down plan of the set. People then plan shots in Studio, render previews,
  and generate footage (styled keyframes, or MiniMax H3 following the 3D motion)."""

TOOLS = [
    {"name": "studio_overview", "description": "Studio's 3D setup: where to save .blend sets, whether Blender is found, how sets are used, and counts of locations, characters, sets and scenes.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "studio_list_locations", "description": "Locations saved in Studio (place pictures with descriptions), used to build 3D sets.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "studio_get_location", "description": "One location: its description, the absolute file paths of its pictures on this PC (for Blender or 3D tools), and the pictures themselves.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "studio_list_characters", "description": "Characters saved in Studio: name, look, and the absolute path of their face picture.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "studio_list_sets", "description": "3D sets registered in Studio with their cameras, markers and size.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "studio_register_set", "description": "Register a .blend file as a Studio set (or refresh it if already registered). Studio opens it in Blender in the background, reads cameras and markers, and renders a top-down plan. Returns the set.",
     "inputSchema": {"type": "object", "properties": {
         "blend_path": {"type": "string", "description": "Absolute path of the .blend on this PC (ideally in the Studio Sets folder)."},
         "name": {"type": "string"}, "notes": {"type": "string", "description": "What the set is, how it was built, which location it matches."},
         "location_id": {"type": "string", "description": "The Studio location this set recreates, if any."}}, "required": ["blend_path"]}},
    {"name": "studio_list_scenes", "description": "Scenes planned in Studio: the set, the cast positions and each shot's camera and move.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "studio_create_scene", "description": "Create a scene on a set with shots. Each shot either uses a camera of the .blend (camera, start, end frames) or a Studio camera: cam {x,y,z,tx,ty,tz,lens} in metres plus move (static, push_in, pull_back, truck_left, truck_right, orbit_left, orbit_right, crane_up, crane_down, pan_left, pan_right, tilt_up, tilt_down, zoom_in, zoom_out, handheld) and amount (metres, or degrees for orbit/pan).",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "set_id": {"type": "string"},
         "cast": {"type": "array", "items": {"type": "object"}, "description": "[{name, x, y, rot (degrees, 0 faces +Y), height}]"},
         "shots": {"type": "array", "items": {"type": "object"}, "description": "[{name, seconds, prompt, camera?, start?, end?, cam?, move?, amount?}]"}},
         "required": ["name", "set_id", "shots"]}},
    {"name": "studio_render_scene", "description": "Render a scene's shots in Blender (look 'grey' fast blocking, or 'lit' with the set's own materials). Waits and returns the clips.",
     "inputSchema": {"type": "object", "properties": {"scene_id": {"type": "string"}, "look": {"type": "string", "enum": ["grey", "lit"]}}, "required": ["scene_id"]}},
]


def call(name, a):
    if name == "studio_overview":
        i = info()
        counts = {k: len(http("GET", f"/manny/api/{k}")) for k in ("locations", "characters", "sets", "scenes")}
        return [{"type": "text", "text": json.dumps({"blender": i.get("path"), "sets_folder": i.get("sets_folder"), "counts": counts}, indent=1)
                 + "\n\n" + GUIDE.format(folder=i.get("sets_folder"))}]
    if name == "studio_list_locations":
        locs = http("GET", "/manny/api/locations")
        return [{"type": "text", "text": json.dumps([{"id": l["id"], "name": l.get("name"), "description": l.get("description"), "pictures": len(l.get("pictures") or [])} for l in locs], indent=1)}]
    if name == "studio_get_location":
        l = next((x for x in http("GET", "/manny/api/locations") if x["id"] == a["id"]), None)
        if not l:
            raise RuntimeError("No such location")
        paths = [abs_input(p) for p in l.get("pictures") or []]
        out = [{"type": "text", "text": json.dumps({"id": l["id"], "name": l.get("name"), "description": l.get("description"), "picture_paths": paths}, indent=1)}]
        for p in paths[:4]:
            t = thumb(p)
            if t:
                out.append(t)
        return out
    if name == "studio_list_characters":
        cs = http("GET", "/manny/api/characters")
        return [{"type": "text", "text": json.dumps([{"name": c.get("name"), "look": c.get("look"), "face_path": abs_input(c["face"]) if c.get("face") else None} for c in cs], indent=1)}]
    if name == "studio_list_sets":
        return [{"type": "text", "text": json.dumps(http("GET", "/manny/api/sets"), indent=1)}]
    if name == "studio_register_set":
        r = http("POST", "/manny/api/sets/add", {"blend": a["blend_path"], "name": a.get("name"), "notes": a.get("notes"), "location": a.get("location_id"), "source": "claude"})
        res = wait_job(r["id"])
        return [{"type": "text", "text": "Registered in Studio.\n" + json.dumps(res, indent=1)}]
    if name == "studio_list_scenes":
        return [{"type": "text", "text": json.dumps(http("GET", "/manny/api/scenes"), indent=1)}]
    if name == "studio_create_scene":
        import uuid
        cast = [dict(c, id=c.get("id") or uuid.uuid4().hex[:6]) for c in a.get("cast") or []]
        shots = []
        for k, s in enumerate(a.get("shots") or []):
            s = dict(s)
            s["id"] = s.get("id") or f"s{k + 1}"
            s.setdefault("seconds", 5)
            shots.append(s)
        r = http("POST", "/manny/api/scenes", {"name": a["name"], "set": a["set_id"], "cast": cast, "shots": shots})
        return [{"type": "text", "text": f"Scene created: {r['id']}. Open Studio › 3D to see it."}]
    if name == "studio_render_scene":
        r = http("POST", f"/manny/api/scenes/{a['scene_id']}/render", {"look": a.get("look") or "grey"})
        res = wait_job(r["id"])
        return [{"type": "text", "text": json.dumps(res, indent=1)}]
    raise RuntimeError("Unknown tool " + name)



# ================================================================ v2: generate everything, storyboard, projects, pipeline
import mimetypes
import random
import re
import uuid as _uuid

_paths = None


def paths():
    global _paths
    if _paths is None:
        _paths = http("GET", "/manny/api/paths")
    return _paths


def short(name):
    return re.sub(r"^(Manny Studio|Studio)\s*-\s*", "", re.sub(r"\.app\.json$", "", name.split("/")[-1]))


def apps():
    a = http("GET", "/manny/api/apps")
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
    for k, v in (("subfolder", "manny_private" if private else "manny"), ("type", "input"), ("overwrite", "false")):
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
        have = [l["file"] for l in http("GET", "/manny/api/loras" + ("?private=1" if private else ""))]
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
        files = [m["name"] for m in http("GET", "/manny/api/models?folder=diffusion_models").get("items", [])]
        f = next((x for x in files if a["style"].lower() in x.lower()), None)
        if not f:
            raise RuntimeError(f"No installed model file matching style '{a['style']}'")
        body["unet"] = {"family": fam, "file": f}
    if a.get("motion_video"):
        body["motion_video"] = as_input(a["motion_video"], private)
    if a.get("pose"):
        pz = str(a["pose"]).replace("\\", "/")
        if not pz.startswith("poses/"):
            packs = (http("GET", "/manny/api/poses") or {}).get("packs", [])
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
    j = next((x for x in http("GET", "/manny/api/remote_jobs") if x["prompt_id"] == pid), None)
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
         "prompt": S_, "negative": S_, "model": {"type": "string", "description": "Part of a model option, e.g. 'MOSS', 'Krea 2', 'MiniMax H3 First frame', 'LTX'"},
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
    {"name": "studio_music", "description": "Make a song or score with ACE-Step (on this PC). Waits and returns the file.",
     "inputSchema": {"type": "object", "properties": {"style": {"type": "string", "description": "genre, mood, instruments"}, "lyrics": S_, "seconds": N_, "bpm": N_, "key": {"type": "string", "description": "e.g. A minor"}, "name": S_, "private": B_}, "required": ["style"]}},
    {"name": "studio_voice", "description": "Speak a line with Chatterbox; optionally copy a voice from a short recording (local path). Waits and returns the file.",
     "inputSchema": {"type": "object", "properties": {"text": S_, "voice": {"type": "string", "description": "5-15 s recording to copy (path)"}, "emotion": N_, "name": S_, "private": B_}, "required": ["text"]}},
    {"name": "studio_upscale_video", "description": "Upscale a video and/or raise its frame rate with Topaz Video (on this PC). Give a path, or filename+subfolder from the gallery.",
     "inputSchema": {"type": "object", "properties": {"path": S_, "filename": S_, "subfolder": S_, "size": {"type": "string", "enum": ["1080", "1440", "4k", ""]}, "fps": {"type": "string", "enum": ["keep", "48", "60"]}, "private": B_}}},
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
    {"name": "studio_final_requests", "description": "Shots waiting for a final render, with the approved take, first frame and 3D preview as files on this PC. For each 'seedance' one: upload the approved take (or first frame) to Higgsfield, generate with Seedance 2.5, then studio_attach the result with as='final'.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "studio_scene_to_board", "description": "Send a planned 3D scene to the Storyboard: one shot per camera, with its Blender preview, the camera move and the footage engine (keyframes = styled stills to video, motion = MiniMax H3 follows the 3D motion, render = use the Blender render).",
     "inputSchema": {"type": "object", "properties": {"scene_id": S_}, "required": ["scene_id"]}},
    {"name": "studio_projects", "description": "List projects, or one project with its script and shots.",
     "inputSchema": {"type": "object", "properties": {"id": S_}}},
    {"name": "studio_project_create", "description": "Start a project (idea -> script -> cast -> keyframes -> storyboard -> sound -> film). cast = character names from studio_list_characters. write_script=true has the local AI write the script and shot list (waits).",
     "inputSchema": {"type": "object", "properties": {"idea": S_, "title": S_, "tone": S_, "shots": N_, "seconds": N_, "cast": {"type": "array", "items": S_}, "write_script": B_, "private": B_}, "required": ["idea"]}},
    {"name": "studio_project_update", "description": "Change a project: title, logline, music, cast (names) or the shots list [{title, action, camera, seconds, characters, dialogue}] (replaces it).",
     "inputSchema": {"type": "object", "properties": {"id": S_, "title": S_, "logline": S_, "music": S_, "cast": {"type": "array", "items": S_}, "shots": {"type": "array", "items": {"type": "object"}}}, "required": ["id"]}},
    {"name": "studio_project_keyframes", "description": "Make a still for each project shot (missing ones, or all with redo) with the Image app; one-character shots use that character's face. Runs in the background unless wait=true.",
     "inputSchema": {"type": "object", "properties": {"id": S_, "model": S_, "redo": B_, "wait": B_}, "required": ["id"]}},
    {"name": "studio_project_to_board", "description": "Send a project's shots (with keyframes as first frames) to its Storyboard board.",
     "inputSchema": {"type": "object", "properties": {"id": S_}, "required": ["id"]}},
]

GUIDE += """

Working from Claude
- studio_list_apps shows every app and its options; studio_generate runs any of them (pictures, videos, edits, upscales…).
- Local files (references, first frames, voices) are uploaded for you: just give their paths.
- Fast tries: Image app (Z-Image / Qwen turbo) and Video app (LTX, MiniMax H3 turbo). On this PC Wan 2.2 and MOSS are slow.
- Storyboard: studio_create_board or studio_scene_to_board / studio_project_to_board, then studio_generate with the shot's prompt
  and first frame, then studio_attach the result to the shot as a take. The user picks and finishes takes in Studio.
- Final renders: studio_final_requests lists shots marked for Seedance 2.5. Render them through Higgsfield and bring the result
  back with studio_attach(as='final')."""


def _board(ref):
    boards = http("GET", "/manny/api/boards")
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
    chars = http("GET", "/manny/api/characters")
    out = []
    for n in names or []:
        c = next((x for x in chars if x["name"].lower() == n.lower()), None) or next((x for x in chars if n.lower() in x["name"].lower()), None)
        if not c:
            raise RuntimeError(f"No character '{n}'")
        out.append(c["id"])
    return out


def _project(pid):
    pr = next((x for x in http("GET", "/manny/api/projects") if x["id"] == pid), None)
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
            r = http("POST", "/manny/api/schedule", {"run": body, "when": when, "at": at, "kind": app["kind"], "label": f"{short(app['id'])} (from Claude): {(a.get('prompt') or '')[:60]}"})
            return [{"type": "text", "text": f"Scheduled ({'when the PC is free' if when == 'idle' else time.strftime('%a %H:%M', time.localtime(at))}). id {r['id']}" + ("\nNotes: " + "; ".join(notes) if notes else "")}]
        r = http("POST", "/manny/api/run", body)
        pids = [q["prompt_id"] for q in r.get("queued", [])]
        where = sorted({q.get("worker") or "local" for q in r.get("queued", [])})
        head = f"Queued {len(pids)} on {short(app['id'])} ({', '.join(where)}): {pids}" + ("\nNotes: " + "; ".join(notes) if notes else "")
        if not a.get("wait", app["kind"] == "image"):
            return [{"type": "text", "text": head + "\nUse studio_wait with these ids to get the files."}]
        return result_content(wait_prompts(pids, float(a.get("timeout") or 300)), head)
    if name == "studio_wait":
        return result_content(wait_prompts(a["prompt_ids"], float(a.get("timeout") or 300)), "Renders:")
    if name == "studio_recent":
        items = http("GET", "/manny/api/gallery")
        kind = a.get("kind") or "any"
        items = [i for i in items if (kind == "any" or i.get("kind") == kind) and (not a.get("folder") or (i.get("subfolder") or "").startswith(a["folder"]))]
        items.sort(key=lambda i: -(i.get("mtime") or 0))
        if kind in ("audio", "any"):
            snd = http("GET", "/manny/api/sounds")
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
        g = http("GET", "/manny/api/guard")
        sch = http("GET", "/manny/api/schedule")
        return [{"type": "text", "text": json.dumps({"running": [short(x[3].get("manny_app", "graph")) for x in q["queue_running"]],
                                                     "progress": g.get("job"), "waiting": [short(x[3].get("manny_app", "graph")) for x in q["queue_pending"]],
                                                     "scheduled": [s for s in sch if s.get("status") == "waiting"]}, indent=1)}]
    if name == "studio_music":
        r = http("POST", "/manny/api/music", {"tags": a["style"], "lyrics": a.get("lyrics") or "", "instrumental": not a.get("lyrics"),
                                              "seconds": a.get("seconds") or 60, "bpm": a.get("bpm") or 100, "key": a.get("key"), "name": a.get("name"), "private": a.get("private")})
        res = job_wait(r["id"], 1200)
        return [{"type": "text", "text": json.dumps(dict(res, path=abs_out(res)), indent=1)}]
    if name == "studio_voice":
        r = http("POST", "/manny/api/voice", {"text": a["text"], "voice": as_input(a.get("voice"), a.get("private")) if a.get("voice") else "",
                                              "exaggeration": a.get("emotion") or 0.5, "name": a.get("name"), "private": a.get("private")})
        res = job_wait(r["id"], 1200)
        return [{"type": "text", "text": json.dumps(dict(res, path=abs_out(res)), indent=1)}]
    if name == "studio_upscale_video":
        f = {"filename": a.get("filename"), "subfolder": a.get("subfolder", "")}
        if a.get("path"):
            info = http("POST", "/manny/api/import", {"path": a["path"], "private": a.get("private")})
            f = {"filename": info["filename"], "subfolder": info["subfolder"]}
        r = http("POST", "/manny/api/topaz/video", dict(f, size=a.get("size", "1080"), fps=a.get("fps", "keep"), private=a.get("private")))
        res = job_wait(r["id"], 3600)
        return [{"type": "text", "text": json.dumps(dict(res, path=abs_out(res)), indent=1)}]
    if name == "studio_poses":
        d = http("GET", "/manny/api/poses")
        return [{"type": "text", "text": json.dumps({"controlnets": d.get("controlnets"), "packs": [{"pack": p["name"], "poses": [x["file"] for x in p["poses"]]} for p in d.get("packs", [])]}, indent=1)}]
    if name == "studio_models":
        lo = http("GET", "/manny/api/loras")
        st = [m for m in http("GET", "/manny/api/models?folder=diffusion_models").get("items", [])]
        return [{"type": "text", "text": json.dumps({"loras": [{"name": l.get("name"), "file": l["file"], "for": l.get("base")} for l in lo],
                                                     "model_files": [{"file": m["name"], "title": m.get("title"), "base": m.get("base"), "gb": round(m.get("size", 0) / 1e9, 1)} for m in st]}, indent=1)}]
    if name == "studio_list_boards":
        boards = http("GET", "/manny/api/boards")
        if a.get("board"):
            boards = [_board(a["board"])]
        out = []
        for b in boards:
            shots = []
            for k, s in enumerate(b.get("shots", [])):
                takes = [t.get("output") for t in s.get("takes") or [] if t.get("output")]
                shots.append({"n": k + 1, "id": s["id"], "prompt": s.get("prompt"), "camera": s.get("camera"), "first_frame": s.get("image"),
                              "engine": s.get("engine"), "has_3d_preview": bool(s.get("previs")), "takes": len(takes),
                              "picked": s.get("output"), "final": s.get("final"), "final_request": s.get("final_request")})
            out.append({"id": b["id"], "name": b.get("name"), "private": b.get("private"), "shots": shots})
        return [{"type": "text", "text": json.dumps(out, indent=1)}]
    if name == "studio_create_board":
        priv = bool(a.get("private"))
        shots = [{"id": _uuid.uuid4().hex[:8], "prompt": s.get("prompt", ""), "image": as_input(s.get("image"), priv) if s.get("image") else "",
                  "camera": s.get("camera", ""), "seconds": s.get("seconds")} for s in a["shots"]]
        b = http("POST", "/manny/api/boards", {"name": a["name"], "shots": shots, "private": priv})
        return [{"type": "text", "text": f"Board '{b['name']}' ({b['id']}) with {len(shots)} shots. Shot ids: " + ", ".join(s["id"] for s in shots)}]
    if name == "studio_attach":
        b = _board(a["board"])
        s = _shot(b, a["shot"])
        body = {k: a.get(k) for k in ("path", "url", "filename", "subfolder", "label") if a.get(k)}
        body["as"] = a.get("as") or "take"
        r = http("POST", f"/manny/api/boards/{b['id']}/shots/{s['id']}/attach", body, timeout=600)
        return [{"type": "text", "text": json.dumps(r)}]
    if name == "studio_request_final":
        b = _board(a["board"])
        s = _shot(b, a["shot"])
        r = http("POST", f"/manny/api/boards/{b['id']}/shots/{s['id']}/final_request", {"engine": a["engine"], "notes": a.get("notes", "")})
        return [{"type": "text", "text": json.dumps(r)}]
    if name == "studio_final_requests":
        return [{"type": "text", "text": json.dumps(http("GET", "/manny/api/final_requests"), indent=1)}]
    if name == "studio_scene_to_board":
        return [{"type": "text", "text": json.dumps(http("POST", f"/manny/api/scenes/{a['scene_id']}/to_board", {}), indent=1)}]
    if name == "studio_projects":
        if a.get("id"):
            return [{"type": "text", "text": json.dumps(_project(a["id"]), indent=1)}]
        return [{"type": "text", "text": json.dumps([{"id": p["id"], "title": p.get("title"), "stage": p.get("stage"), "shots": len(p.get("shots") or [])} for p in http("GET", "/manny/api/projects")], indent=1)}]
    if name == "studio_project_create":
        pr = {"name": a.get("title") or "New film", "title": a.get("title") or "", "idea": a["idea"], "tone": a.get("tone") or "Cinematic drama",
              "nshots": int(a.get("shots") or 6), "seconds": int(a.get("seconds") or 5), "cast": _char_ids(a.get("cast")), "shots": [],
              "private": bool(a.get("private")), "stage": "Idea"}
        pr = http("POST", "/manny/api/projects", pr)
        if a.get("write_script", True):
            chars = {c["id"]: c for c in http("GET", "/manny/api/characters")}
            cast = [{"name": chars[c]["name"], "look": ", ".join(x for x in [chars[c].get("trigger"), chars[c].get("look")] if x)} for c in pr["cast"] if c in chars]
            r = http("POST", "/manny/api/projects/script", {"idea": pr["idea"], "tone": pr["tone"], "shots": pr["nshots"], "seconds": pr["seconds"], "cast": cast, "private": pr["private"]})
            res = job_wait(r["id"], 900)
            pr.update(title=pr["title"] or res.get("title"), logline=res.get("logline"), music=res.get("music"), shots=res.get("shots"), stage="Script", step="script")
            pr = http("POST", "/manny/api/projects", pr)
        return [{"type": "text", "text": json.dumps(pr, indent=1)}]
    if name == "studio_project_update":
        pr = _project(a["id"])
        for k in ("title", "logline", "music"):
            if a.get(k) is not None:
                pr[k] = a[k]
        if a.get("cast") is not None:
            pr["cast"] = _char_ids(a["cast"])
        if a.get("shots") is not None:
            pr["shots"] = [{"id": s.get("id") or _uuid.uuid4().hex[:8], "title": s.get("title", f"Shot {k + 1}"), "action": s.get("action", ""),
                            "camera": s.get("camera", "static"), "seconds": s.get("seconds", pr.get("seconds", 5)),
                            "characters": s.get("characters", []), "dialogue": s.get("dialogue", ""), **({"keyframe": s["keyframe"]} if s.get("keyframe") else {})}
                           for k, s in enumerate(a["shots"])]
        return [{"type": "text", "text": json.dumps(http("POST", "/manny/api/projects", pr), indent=1)}]
    if name == "studio_project_keyframes":
        pr = _project(a["id"])
        r = http("POST", f"/manny/api/projects/{pr['id']}/keyframes", {"private": pr.get("private"), "model": a.get("model"), "redo": a.get("redo")})
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
        return [{"type": "text", "text": json.dumps(http("POST", f"/manny/api/projects/{pr['id']}/to_board", {"private": pr.get("private")}), indent=1)}]
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
                       "instructions": "Studio is the user's ComfyUI studio app on this PC. Use these tools to generate pictures, videos, music and voices with any Studio app, run projects and the storyboard, register 3D sets built in Blender and plan/render scenes, and bring final renders (e.g. Seedance via Higgsfield) back into Studio. Call studio_overview first."}
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
