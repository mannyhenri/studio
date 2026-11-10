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
    return os.path.join(input_dir_guess(), v.replace("/", os.sep))


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
                       "capabilities": {"tools": {}}, "serverInfo": {"name": "studio", "version": "1.0"},
                       "instructions": "Studio is the user's ComfyUI studio app. Use these tools to read locations/characters and to register 3D sets built in Blender."}
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
