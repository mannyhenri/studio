"""Studio 3D: previz sets from .glb files (glTF binary), explored in the browser (web/glbview.js, three.js).

A .glb is added by uploading it (saved in Documents/Studio Sets) or by its path on this PC (used where it is). Studio reads
only the file's JSON chunk to list what the viewer needs: the cameras, how long the animation runs, what's missing.

Cameras come two ways:
- glTF cameras (Blender: File > Export > glTF 2.0 with Include > Cameras ticked);
- camera rigs: empties named like a camera ("CAM_01", "G_CU_50mm_Rig", "ShotCam"), which is how a Blender camera arrives when
  cameras weren't exported but their animated rig was. The rig's aim comes from its target empty (TGT_<name>) when there is
  one, else from the file's other rigs, else from Blender's convention. The lens is read from the name ("..._35mm").
Aim targets whose camera isn't in the file are listed as missing, so the app can say to export again with cameras.
"""
import glob
import json
import math
import os
import re
import shutil
import struct
import threading
import time
import uuid

from aiohttp import web

H = {}          # helpers from __init__ (set in setup)
_lock = threading.RLock()
LIST = "glb_sets.json"
GLB_MAGIC = b"glTF"
JSON_CHUNK = 0x4E4F534A
MAX_JSON = 64 << 20        # a .glb's JSON chunk bigger than this isn't a set we can show
MAX_DIRECTION = 2 << 20    # what's directed on a set (cameras, moves, lights, objects), as JSON
KINDS = ("set", "prop")    # a set you explore, or a prop you add to one
MAX_PICTURE = 40 << 20     # a snapshot or a movie frame
MAX_FRAMES = 60 * 60 * 10  # ten minutes at 60 fps
MAX_STILLS = 300           # location shots (and movies) listed on a set
VIEW_KEYS = ("camera", "aspect", "look", "time", "tab", "isolate", "top", "keys")    # the viewer's choices kept with a set


# ------------------------------------------------------------------ reading a .glb
class NotGlb(ValueError):
    pass


def read_gltf_json(path):
    """The glTF JSON of a .glb, read from its first chunk (the binary chunk is never loaded)."""
    with open(path, "rb") as f:
        head = f.read(20)
        if len(head) < 20 or head[:4] != GLB_MAGIC:
            raise NotGlb("That isn't a .glb file (glTF binary)")
        version, total = struct.unpack("<II", head[4:12])
        if version != 2:
            raise NotGlb(f"This .glb is glTF {version}; Studio reads glTF 2.0")
        clen, ctype = struct.unpack("<II", head[12:20])
        if ctype != JSON_CHUNK or clen > MAX_JSON:
            raise NotGlb("This .glb has no readable JSON chunk")
        raw = f.read(clen)
    if len(raw) < clen:
        raise NotGlb("This .glb is cut short")
    if total < 20 + clen:
        raise NotGlb("This .glb is damaged: its header is shorter than its JSON")
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise NotGlb("This .glb's JSON can't be read: " + str(e)[:120])


def looks_like_glb(head):
    return len(head) >= 12 and head[:4] == GLB_MAGIC and struct.unpack("<I", head[4:8])[0] == 2


# ------------------------------------------------------------------ small matrix helpers (row-major 4x4)
def _mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _local_matrix(node):
    m = node.get("matrix")
    if m and len(m) == 16:      # glTF stores matrices column-major
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]
    tx, ty, tz = (node.get("translation") or [0, 0, 0])[:3]
    x, y, z, w = (node.get("rotation") or [0, 0, 0, 1])[:4]
    sx, sy, sz = (node.get("scale") or [1, 1, 1])[:3]
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[r[0][0] * sx, r[0][1] * sy, r[0][2] * sz, tx],
            [r[1][0] * sx, r[1][1] * sy, r[1][2] * sz, ty],
            [r[2][0] * sx, r[2][1] * sy, r[2][2] * sz, tz],
            [0, 0, 0, 1]]


def _norm(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / n for c in v]


AXES = {"+X": (0, 1), "-X": (0, -1), "+Y": (1, 1), "-Y": (1, -1), "+Z": (2, 1), "-Z": (2, -1)}


def _axis_world(m, axis):
    col, sign = AXES[axis]
    return _norm([m[r][col] * sign for r in range(3)])


# ------------------------------------------------------------------ names
def _tokens(name):
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name or ""))
    s = re.sub(r"([A-Za-z])(\d)", r"\1 \2", s)
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]


CAM_WORDS = {"cam", "camera", "bcam", "shotcam"}
TARGET_WORDS = {"tgt", "target", "aim", "lookat", "interest"}
LENS = re.compile(r"(?<![A-Za-z0-9.])(\d{1,3}(?:\.\d+)?)\s?mm(?![A-Za-z])", re.I)


def lens_from_name(name):
    m = LENS.search(str(name or "").replace("_", " "))
    if not m:
        return None
    v = float(m.group(1))
    return v if 4 <= v <= 1200 else None


def _is_target_name(toks):
    return bool(TARGET_WORDS & set(toks)) or any(a == "look" and b == "at" for a, b in zip(toks, toks[1:]))


def _is_camera_name(name, toks):
    return bool(CAM_WORDS & set(toks)) or lens_from_name(name) is not None


def _key(toks):
    """What a camera and its aim target have in common: CAM_01_24mm_Rig and TGT_CAM_01_24mm give the same key."""
    drop = TARGET_WORDS | {"rig", "look", "at"}
    return "".join(t for t in toks if t not in drop)


def display_name(name):
    return re.sub(r"[_ .-]+rig$", "", str(name or ""), flags=re.I) or str(name or "")


# ------------------------------------------------------------------ what the viewer needs
def _guess_fps(times):
    first = min((t for t in times if t > 1e-6), default=0)
    if first:
        for fps in (24, 30, 25, 60, 50, 48):
            f = first * fps
            if abs(f - round(f)) < 1e-3 and 1 <= round(f) <= 1000:
                return fps
    return 24


def inspect(gltf):
    """Everything Studio shows about a .glb before the viewer loads it: cameras (with how to aim and frame each), the
    animation's length, and the cameras that didn't make it into the file."""
    nodes = gltf.get("nodes") or []
    cams = gltf.get("cameras") or []
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children") or []:
            parent[c] = i
    world = {}

    def wm(i, seen=()):
        if i not in world:
            loc = _local_matrix(nodes[i])
            p = parent.get(i)
            world[i] = _mat_mul(wm(p, seen + (i,)), loc) if p is not None and p not in seen else loc
        return world[i]

    joints = {j for s in gltf.get("skins") or [] for j in s.get("joints") or []}
    animated_direct = {ch.get("target", {}).get("node") for a in gltf.get("animations") or [] for ch in a.get("channels") or []}

    def animated(i):
        while i is not None:
            if i in animated_direct:
                return True
            i = parent.get(i)
        return False

    def has_camera_below(i):
        stack = list(nodes[i].get("children") or [])
        while stack:
            c = stack.pop()
            if "camera" in nodes[c]:
                return True
            stack.extend(nodes[c].get("children") or [])
        return False

    toks = [_tokens(n.get("name")) for n in nodes]
    out, keys = [], set()
    # glTF cameras
    for i, n in enumerate(nodes):
        if "camera" not in n or not (0 <= n["camera"] < len(cams)):
            continue
        c = cams[n["camera"]]
        name = n.get("name") or c.get("name") or f"Camera {len(out) + 1}"
        if c.get("type") == "orthographic":
            o = c.get("orthographic") or {}
            out.append({"id": name, "node": i, "name": name, "kind": "ortho", "lens": None, "lens_known": True,
                        "xmag": float(o.get("xmag") or 1), "ymag": float(o.get("ymag") or 1), "znear": o.get("znear"), "zfar": o.get("zfar"),
                        "forward": "-Z", "up": "+Y", "animated": animated(i)})
            keys.add(_key(toks[i]))
            continue
        if c.get("type") != "perspective":
            continue
        p = c.get("perspective") or {}
        yfov, aspect = float(p.get("yfov") or 0.6911), p.get("aspectRatio")
        lens = 18 / (math.tan(yfov / 2) * max(float(aspect or 1.5), 1.0))
        out.append({"id": name, "node": i, "name": name, "kind": "camera", "lens": round(lens, 1), "lens_known": True,
                    "yfov": yfov, "aspect": aspect, "znear": p.get("znear"), "zfar": p.get("zfar"),
                    "forward": "-Z", "up": "+Y", "animated": animated(i)})
        keys.add(_key(toks[i]))
    # camera rigs (empties named like a camera)
    targets = {}
    for i, n in enumerate(nodes):
        if "mesh" not in n and "camera" not in n and i not in joints and _is_target_name(toks[i]):
            targets.setdefault(_key(toks[i]), i)
    rigs = []
    for i, n in enumerate(nodes):
        if "mesh" in n or "camera" in n or i in joints or "skin" in n:
            continue
        if _is_target_name(toks[i]) or not _is_camera_name(n.get("name"), toks[i]) or has_camera_below(i):
            continue
        rigs.append(i)
    rig_set = set(rigs)
    rigs = [i for i in rigs if not any(c in rig_set for c in nodes[i].get("children") or [])]   # a group of rigs isn't one
    aims = {}
    for i in rigs:
        t = targets.get(_key(toks[i]))
        if t is None:
            continue
        m, mt = wm(i), wm(t)
        d = [mt[r][3] - m[r][3] for r in range(3)]
        if math.sqrt(sum(c * c for c in d)) < 1e-6:
            continue
        d = _norm(d)
        best = max(AXES, key=lambda a: sum(x * y for x, y in zip(_axis_world(m, a), d)))
        if sum(x * y for x, y in zip(_axis_world(m, best), d)) > 0.9:
            aims[i] = (best, t)
    conv = {}
    for fwd, _t in aims.values():
        conv[fwd] = conv.get(fwd, 0) + 1
    blender = "blender" in str((gltf.get("asset") or {}).get("generator", "")).lower()
    default_fwd = max(conv, key=conv.get) if conv else ("-Y" if blender else "-Z")
    for i in rigs:
        fwd, tgt = aims.get(i, (default_fwd, None))
        up = _up_for(fwd, wm(i))
        lens = lens_from_name(nodes[i].get("name"))
        rname = display_name(nodes[i].get("name") or f"Camera {len(out) + 1}")
        out.append({"id": rname, "node": i, "name": rname, "kind": "rig",
                    "lens": lens or 50.0, "lens_known": lens is not None, "forward": fwd, "up": up,
                    "target": tgt, "aim": "target" if tgt is not None else "guess", "animated": animated(i)})
        keys.add(_key(toks[i]))
    seen = {}
    for c in out:                 # ids are the names (they survive a new export); two cameras with one name get #2, #3
        k = seen[c["id"]] = seen.get(c["id"], 0) + 1
        if k > 1:
            c["id"] = f"{c['id']}#{k}"
    missing = sorted(display_name(re.sub(r"^(tgt|target|aim)[_ .-]+|[_ .-]+(tgt|target|aim)$", "", nodes[t].get("name") or "", flags=re.I))
                     for k, t in targets.items() if k not in keys and _is_camera_name(nodes[t].get("name"), toks[t]))
    times = []
    for a in gltf.get("animations") or []:
        for s in a.get("samplers") or []:
            acc = (gltf.get("accessors") or [])[s["input"]] if isinstance(s.get("input"), int) and s["input"] < len(gltf.get("accessors") or []) else {}
            times += [float(v) for v in (acc.get("min") or [])[:1] + (acc.get("max") or [])[:1]]
    return {"generator": (gltf.get("asset") or {}).get("generator", ""), "nodes": len(nodes), "meshes": len(gltf.get("meshes") or []),
            "animations": len(gltf.get("animations") or []), "duration": round(max(times, default=0.0), 4), "fps": _guess_fps(times),
            "lights": len(((gltf.get("extensions") or {}).get("KHR_lights_punctual") or {}).get("lights") or []),
            "required": list(gltf.get("extensionsRequired") or []), "cameras": out, "missing": missing}


def _up_for(fwd, m):
    """A camera's up axis: glTF cameras look down -Z with +Y up; a Blender camera baked into an empty looks down -Y with -Z
    up (Blender's camera axes after the Z-up to Y-up change). Anything else: the side axis that points most to the sky."""
    if fwd in ("-Z", "+Z"):
        return "+Y"
    if fwd in ("-Y", "+Y"):
        return "-Z" if fwd == "-Y" else "+Z"
    col = AXES[fwd][0]
    side = [a for a, (c, _s) in AXES.items() if c != col]
    return max(side, key=lambda a: _axis_world(m, a)[1])


def inspect_file(path):
    info = inspect(read_gltf_json(path))
    st = os.stat(path)
    info.update(size=st.st_size, mtime=st.st_mtime)
    return info


# ------------------------------------------------------------------ a quick scene: a blank stage, nothing moving
STAGES = {"cyc": "Studio cyc", "room": "Room", "open": "Open ground"}


def _look_quat(p, t):
    """The rotation (x, y, z, w) of a glTF camera at p looking at t (it looks down -Z with +Y up)."""
    f = _norm([t[i] - p[i] for i in range(3)])
    r = _norm([-f[2], 0, f[0]])                        # f x up
    u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]]   # r x f
    m = [[r[0], u[0], -f[0]], [r[1], u[1], -f[1]], [r[2], u[2], -f[2]]]
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        k = 0.5 / math.sqrt(tr + 1)
        return [(m[2][1] - m[1][2]) * k, (m[0][2] - m[2][0]) * k, (m[1][0] - m[0][1]) * k, 0.25 / k]
    if m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        k = 2 * math.sqrt(1 + m[0][0] - m[1][1] - m[2][2])
        return [0.25 * k, (m[0][1] + m[1][0]) / k, (m[0][2] + m[2][0]) / k, (m[2][1] - m[1][2]) / k]
    if m[1][1] > m[2][2]:
        k = 2 * math.sqrt(1 + m[1][1] - m[0][0] - m[2][2])
        return [(m[0][1] + m[1][0]) / k, 0.25 * k, (m[1][2] + m[2][1]) / k, (m[0][2] - m[2][0]) / k]
    k = 2 * math.sqrt(1 + m[2][2] - m[0][0] - m[1][1])
    return [(m[0][2] + m[2][0]) / k, (m[1][2] + m[2][1]) / k, 0.25 * k, (m[1][0] - m[0][1]) / k]


class _Mesh:
    """Triangles with normals, one list per material."""
    def __init__(self):
        self.parts = {}

    def quad(self, mat, a, b, c, d, n):
        pos, nor, idx = self.parts.setdefault(mat, ([], [], []))
        k = len(pos) // 3
        for v in (a, b, c, d):
            pos.extend(v)
            nor.extend(n)
        idx.extend([k, k + 1, k + 2, k, k + 2, k + 3])

    def checker(self, x0, x1, z0, z1, tile, mats):
        nx, nz = max(1, round((x1 - x0) / tile)), max(1, round((z1 - z0) / tile))
        for i in range(nx):
            for j in range(nz):
                xa, xb = x0 + (x1 - x0) * i / nx, x0 + (x1 - x0) * (i + 1) / nx
                za, zb = z0 + (z1 - z0) * j / nz, z0 + (z1 - z0) * (j + 1) / nz
                self.quad(mats[(i + j) % 2], [xa, 0, zb], [xb, 0, zb], [xb, 0, za], [xa, 0, za], [0, 1, 0])


def quick_scene_glb(stage="cyc"):
    """A blank stage as a .glb: a floor in 1 m squares (for scale), the stage's walls, and one camera at eye level
    looking at the middle of the floor. No lights (Previz lights it) and no animation."""
    MATS = {"floor": [0.58, 0.58, 0.6], "floor2": [0.5, 0.5, 0.52], "cyc": [0.86, 0.86, 0.86], "wall": [0.74, 0.71, 0.66]}
    m = _Mesh()
    if stage == "room":          # 8 x 6 m, 3 m walls (seen from both sides), a doorway in the left wall
        m.checker(-4, 4, -3, 3, 1, ("floor", "floor2"))
        H = 3.0
        m.quad("wall", [-4, 0, -3], [4, 0, -3], [4, H, -3], [-4, H, -3], [0, 0, 1])
        m.quad("wall", [4, 0, 3], [-4, 0, 3], [-4, H, 3], [4, H, 3], [0, 0, -1])
        m.quad("wall", [4, 0, -3], [4, 0, 3], [4, H, 3], [4, H, -3], [-1, 0, 0])
        for z0, z1 in ((-3, -0.5), (0.5, 3)):
            m.quad("wall", [-4, 0, z1], [-4, 0, z0], [-4, H, z0], [-4, H, z1], [1, 0, 0])
        m.quad("wall", [-4, 2.1, 0.5], [-4, 2.1, -0.5], [-4, H, -0.5], [-4, H, 0.5], [1, 0, 0])
        cam = ([0, 1.6, 2.7], [0, 1.25, 0])
    elif stage == "open":        # 40 x 40 m of ground in 2 m squares
        m.checker(-20, 20, -20, 20, 2, ("floor", "floor2"))
        cam = ([0, 1.6, 5], [0, 1.2, 0])
    else:                        # a cyclorama: floor, a curved cove and a back wall, 16 m wide
        R, Z, W, H = 1.5, -6.0, 8.0, 6.0
        m.checker(-W, W, Z + R, 8, 1, ("floor", "floor2"))
        steps = 12
        for k in range(steps):
            a0, a1 = math.pi / 2 * k / steps, math.pi / 2 * (k + 1) / steps
            y0, z0, y1, z1 = R - R * math.cos(a0), Z + R - R * math.sin(a0), R - R * math.cos(a1), Z + R - R * math.sin(a1)
            am = (a0 + a1) / 2
            m.quad("cyc", [-W, y0, z0], [W, y0, z0], [W, y1, z1], [-W, y1, z1], [0, math.cos(am), math.sin(am)])
        m.quad("cyc", [-W, R, Z], [W, R, Z], [W, H, Z], [-W, H, Z], [0, 0, 1])
        cam = ([0, 1.6, 4.5], [0, 1.25, 0])
    # the binary: per material, positions + normals + indices
    bin_, views, accs, prims, mats = bytearray(), [], [], [], []

    def add(data, fmt, count, typ, target, mn=None, mx=None, comp=5126):
        while len(bin_) % 4:
            bin_.append(0)
        off = len(bin_)
        bin_.extend(struct.pack(f"<{len(data)}{fmt}", *data))
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(bin_) - off, "target": target})
        acc = {"bufferView": len(views) - 1, "componentType": comp, "count": count, "type": typ}
        if mn is not None:
            acc.update(min=mn, max=mx)
        accs.append(acc)
        return len(accs) - 1
    for name, (pos, nor, idx) in m.parts.items():
        n = len(pos) // 3
        mn = [min(pos[i::3]) for i in range(3)]
        mx = [max(pos[i::3]) for i in range(3)]
        pa = add(pos, "f", n, "VEC3", 34962, mn, mx)
        na = add(nor, "f", n, "VEC3", 34962)
        ia = add(idx, "I", len(idx), "SCALAR", 34963, comp=5125)
        if name not in [x["name"] for x in mats]:
            mats.append({"name": name, "doubleSided": name == "wall",
                         "pbrMetallicRoughness": {"baseColorFactor": MATS[name] + [1], "metallicFactor": 0, "roughnessFactor": 0.92}})
        prims.append({"attributes": {"POSITION": pa, "NORMAL": na}, "indices": ia, "material": [x["name"] for x in mats].index(name)})
    aspect, lens = 16 / 9, 35
    yfov = 2 * math.atan(18 / (lens * aspect))
    gltf = {"asset": {"version": "2.0", "generator": "Studio quick scene"}, "scene": 0,
            "scenes": [{"name": STAGES.get(stage, "Stage"), "nodes": [0, 1]}],
            "nodes": [{"name": "Stage", "mesh": 0},
                      {"name": "Camera", "camera": 0, "translation": cam[0], "rotation": _look_quat(*cam)}],
            "meshes": [{"name": "Stage", "primitives": prims}], "materials": mats,
            "cameras": [{"name": "Camera", "type": "perspective", "perspective": {"yfov": yfov, "aspectRatio": aspect, "znear": 0.05, "zfar": 500}}],
            "buffers": [{"byteLength": len(bin_)}], "bufferViews": views, "accessors": accs}
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    while len(bin_) % 4:
        bin_.append(0)
    body = struct.pack("<II", len(js), JSON_CHUNK) + js + struct.pack("<II", len(bin_), 0x004E4942) + bytes(bin_)
    return GLB_MAGIC + struct.pack("<II", 2, 12 + len(body)) + body


def quick_direction(people, stage="cyc"):
    """What a quick scene starts with: the subject (a stand-in), or two people facing each other for a dialogue."""
    a, b = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    s45 = math.sqrt(0.5)
    if people == 2:
        added = [{"id": a, "kind": "person", "name": "Person A", "color": 0x6f9bd6, "p": [-0.75, 0, 0], "q": [0, s45, 0, s45], "s": [1, 1, 1]},
                 {"id": b, "kind": "person", "name": "Person B", "color": 0xe0894a, "p": [0.75, 0, 0], "q": [0, -s45, 0, s45], "s": [1, 1, 1]}]
        d = {"v": 1, "cams": [], "hidden": [], "moved": {}, "added": added, "light": None, "subject": "added:" + a, "with": "added:" + b}
    elif people == 1:
        added = [{"id": a, "kind": "person", "name": "Subject", "color": 0x6f9bd6, "p": [0, 0, 0], "q": [0, 0, 0, 1], "s": [1, 1, 1]}]
        d = {"v": 1, "cams": [], "hidden": [], "moved": {}, "added": added, "light": None, "subject": "added:" + a, "with": None}
    else:
        d = {"v": 1, "cams": [], "hidden": [], "moved": {}, "added": [], "light": None, "subject": None, "with": None}
    if stage == "open":          # outdoors: soft daylight and a sky, not a dark studio
        d["light"] = {"preset": "overcast"}
    return d


# ------------------------------------------------------------------ storage
def _file(name):
    return os.path.join(H["_data_dir"](), name)


def _load():
    try:
        with open(_file(LIST), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(data):
    with _lock:
        tmp = _file(LIST) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, _file(LIST))


def _cache(sid):
    d = os.path.join(H["_data_dir"](), "glb", sid)
    os.makedirs(d, exist_ok=True)
    return d


def upload_folder(private):
    """Uploaded .glb files (and quick scenes): Documents/Studio Sets, or the private folder in Incognito."""
    if private:
        d = os.path.join(H["_priv_root"](), "Studio Sets")
    else:
        d = os.path.join(os.path.expanduser("~"), "Documents", "Studio Sets")
    os.makedirs(d, exist_ok=True)
    return d


def safe_stem(name, default="Set"):
    stem = os.path.splitext(os.path.basename(str(name or "").replace("\\", "/")))[0]
    stem = "".join(ch for ch in stem if ch.isalnum() or ch in " -_.").strip(" .")
    return stem[:80] or default


def free_path(folder, stem, ext=".glb"):
    path, k = os.path.join(folder, stem + ext), 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem} {k}{ext}")
        k += 1
    return path


def resolve_path(v):
    v = str(v or "").strip().strip('"').strip("'")
    if not v:
        raise web.HTTPBadRequest(text="Give the path of a .glb file on this PC")
    full = os.path.realpath(os.path.expanduser(v))
    if not full.lower().endswith(".glb"):
        raise web.HTTPBadRequest(text="Only .glb files (glTF binary) can be added")
    if not os.path.isfile(full):
        raise web.HTTPBadRequest(text="That .glb file wasn't found on this PC")
    return full


def _fresh(entry):
    """The entry with up-to-date info: read again when the file changed on disk (re-exported from Blender, say)."""
    p = entry.get("file", "")
    try:
        st = os.stat(p)
    except OSError:
        return dict(entry, missing_file=True)
    info = entry.get("info") or {}
    if info.get("size") != st.st_size or abs((info.get("mtime") or 0) - st.st_mtime) > 1e-3:
        try:
            entry["info"] = inspect_file(p)
            entry["error"] = ""
        except Exception as e:
            entry["error"] = str(e)[:300]
        entry["updated"] = time.time()
        _store(entry)
    return dict(entry, missing_file=False)


def _store(entry):
    with _lock:
        _save([s for s in _load() if s["id"] != entry["id"]] + [entry])


def _modify(sid, fn):
    """Changes one set in place: read, fn(entry), written, in one go. Call it after the last await of a request, so a
    request that waited (for its body, for ffmpeg) never writes back a stale copy over another's change."""
    with _lock:
        sets = _load()
        entry = next((s for s in sets if s["id"] == sid), None)
        if not entry:
            raise web.HTTPNotFound(text="That set isn't in Studio")
        fn(entry)
        _save(sets)
        return entry


def _get(request):
    sid = request.match_info["sid"]
    entry = next((s for s in _load() if s["id"] == sid), None)
    if not entry:
        raise web.HTTPNotFound(text="That set isn't in Studio")
    if entry.get("private"):
        H["_need_private"](request)
    return entry


def _public(entry, full=True):
    e = {k: v for k, v in entry.items() if k != "file" and (full or k != "direction")}
    e["path"] = entry.get("file", "")
    e["file_name"] = os.path.basename(entry.get("file", ""))
    return e


def register(path, name, private, source, kind="set"):
    """Adds (or updates) a set (or a prop) for a .glb on disk. The same file added twice stays one entry."""
    info = inspect_file(path)
    sets = _load()
    old = next((s for s in sets if os.path.normcase(s.get("file", "")) == os.path.normcase(path)), None)
    entry = old or {"id": uuid.uuid4().hex[:10], "at": time.time()}
    entry.update({"name": (str(name or "").strip() or entry.get("name") or safe_stem(path))[:80], "file": path, "private": bool(private),
                  "source": entry.get("source") or source, "kind": kind if kind in KINDS else "set", "info": info, "error": "", "updated": time.time()})
    _store(entry)
    return entry


# ------------------------------------------------------------------ routes
def setup(ns):
    H.update(ns)
    routes = ns["routes"]
    _flag, _need_private = ns["_flag"], ns["_need_private"]

    @routes.get("/studio/api/glb")
    async def glb_list(request):
        private, kind = _flag(request.query.get("private")), request.query.get("kind") or "set"
        if private and not H["auth"].unlocked(request):
            raise web.HTTPForbidden(text="Incognito is locked")
        return web.json_response([_public(_fresh(s), full=False) for s in _load() if bool(s.get("private")) == private and (s.get("kind") or "set") == kind])

    @routes.get("/studio/api/glb/{sid}")
    async def glb_one(request):
        return web.json_response(_public(_fresh(_get(request))))

    @routes.post("/studio/api/glb/upload")
    async def glb_upload(request):
        """The .glb is the raw request body (streamed to disk, so a set of any size can be sent from another computer).
        Query: name (shown in Studio), filename (the file's own name), private=1 for Incognito, kind=prop for a prop."""
        q = request.query
        private = _flag(q.get("private"))
        if private:
            _need_private(request)
        kind = q.get("kind") if q.get("kind") in KINDS else "set"
        stem = safe_stem(q.get("filename") or q.get("name") or "Set")
        folder = os.path.join(upload_folder(private), "Props") if kind == "prop" else upload_folder(private)
        os.makedirs(folder, exist_ok=True)
        path = free_path(folder, stem)
        part = path + f".{uuid.uuid4().hex[:6]}.part"
        size, head = 0, b""
        try:
            with open(part, "wb") as f:
                async for chunk in request.content.iter_chunked(1 << 20):
                    if len(head) < 12:
                        head += chunk[:12 - len(head)]
                        if len(head) >= 12 and not looks_like_glb(head):
                            raise NotGlb("That isn't a .glb file (glTF binary)")
                    f.write(chunk)
                    size += len(chunk)
            if size < 20 or not looks_like_glb(head):
                raise NotGlb("That isn't a .glb file (glTF binary)")
            if os.path.exists(path):          # another upload took the name meanwhile
                path = free_path(folder, stem)
            os.replace(part, path)
            entry = register(path, q.get("name") or stem, private, "upload", kind)
        except NotGlb as e:
            raise web.HTTPBadRequest(text=str(e))
        finally:
            if os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass
        return web.json_response(_public(_fresh(entry)))

    @routes.post("/studio/api/glb/quick")
    async def glb_quick(request):
        """A quick scene: a blank stage (stage: cyc, room or open) with a subject (people: 1), two people facing each other
        (2) or nobody (0); one camera, no animation. Saved as a .glb in the Studio Sets folder like an upload."""
        b = await request.json()
        private = bool(b.get("private"))
        if private:
            _need_private(request)
        stage = b.get("stage") if b.get("stage") in STAGES else "cyc"
        people = int(b.get("people", 1)) if str(b.get("people", 1)) in ("0", "1", "2") else 1
        name = str(b.get("name") or "").strip()[:80] or f"Quick scene ({STAGES[stage].lower()})"
        path = free_path(upload_folder(private), safe_stem(name, "Quick scene"))
        with open(path, "wb") as f:
            f.write(quick_scene_glb(stage))
        entry = register(path, name, private, "quick")
        entry["direction"] = quick_direction(people, stage)
        entry["view"] = {"camera": "Camera", "aspect": "1.7778", "tab": "frame"}
        _store(entry)
        return web.json_response(_public(_fresh(entry)))

    @routes.post("/studio/api/glb/add")
    async def glb_add(request):
        """Adds a .glb already on this PC, used where it is (nothing is copied). Body: {path, name, private}."""
        b = await request.json()
        private = bool(b.get("private"))
        if private:
            _need_private(request)
        path = resolve_path(b.get("path"))
        try:
            entry = register(path, b.get("name"), private, "path", b.get("kind") or "set")
        except NotGlb as e:
            raise web.HTTPBadRequest(text=str(e))
        return web.json_response(_public(_fresh(entry)))

    @routes.post("/studio/api/glb/{sid}")
    async def glb_edit(request):
        """Renames a set, remembers the viewer's choices for it (last camera, frame shape, tab), or saves what was directed
        on it (Studio cameras and their moves, lighting, objects added, hidden or moved)."""
        _get(request)
        b = await request.json()
        d = b.get("direction")
        if "direction" in b and d is not None and (not isinstance(d, dict) or len(json.dumps(d)) > MAX_DIRECTION):
            raise web.HTTPBadRequest(text="direction must be an object" if not isinstance(d, dict) else "Too much to save on one set")

        def change(entry):
            if "name" in b:
                entry["name"] = str(b.get("name") or "").strip()[:80] or entry.get("name") or "Set"
            if isinstance(b.get("view"), dict):
                entry["view"] = {k: v for k, v in b["view"].items() if k in VIEW_KEYS}
            if "direction" in b:
                entry["direction"] = d
        return web.json_response(_public(_fresh(_modify(request.match_info["sid"], change))))

    @routes.post("/studio/api/glb/{sid}/refresh")
    async def glb_refresh(request):
        entry = _get(request)
        if not os.path.isfile(entry.get("file", "")):
            raise web.HTTPBadRequest(text="The .glb file isn't there any more: " + entry.get("file", ""))
        try:
            info = inspect_file(entry["file"])
        except NotGlb as e:
            raise web.HTTPBadRequest(text=str(e))
        entry = _modify(entry["id"], lambda e: e.update(info=info, error="", updated=time.time()))
        return web.json_response(_public(_fresh(entry)))

    @routes.delete("/studio/api/glb/{sid}")
    async def glb_delete(request):
        """Removes the set from Studio. The .glb file itself stays where it is."""
        entry = _get(request)
        with _lock:
            _save([s for s in _load() if s["id"] != entry["id"]])
        shutil.rmtree(_cache(entry["id"]), ignore_errors=True)       # its card picture and any recording left behind
        return web.json_response({"ok": True})

    @routes.get("/studio/api/glb/{sid}/file")
    async def glb_file(request):
        entry = _get(request)
        p = entry.get("file", "")
        if not os.path.isfile(p) or not p.lower().endswith(".glb"):
            raise web.HTTPNotFound(text="The .glb file isn't there any more")
        return web.FileResponse(p, headers={"Content-Type": "model/gltf-binary", "Cache-Control": "private, no-cache"})

    @routes.post("/studio/api/glb/{sid}/thumb")
    async def glb_thumb_put(request):
        """A picture of the set for its card, sent by the viewer (JPEG or PNG body, up to 4 MB)."""
        entry = _get(request)
        raw = await request.read()
        if len(raw) > 4 << 20 or not (raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n"):
            raise web.HTTPBadRequest(text="Send a JPEG or PNG under 4 MB")
        with open(os.path.join(_cache(entry["id"]), "thumb.jpg"), "wb") as f:
            f.write(raw)
        stamp = time.time()
        _modify(entry["id"], lambda e: e.update(thumb=stamp))
        return web.json_response({"ok": True, "thumb": stamp})

    # ---- snapshots and movies of a view: files in the Gallery (Images/Videos › Previz › the set)
    def out_dir(entry, kind):
        sub = f"{kind}/Previz/{safe_stem(entry.get('name'), 'Set')}"
        d = os.path.join(H["_root"](bool(entry.get("private"))), *sub.split("/"))
        os.makedirs(d, exist_ok=True)
        return d, sub

    def picture_ext(raw):
        return ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg" if raw[:3] == b"\xff\xd8\xff" else None

    @routes.post("/studio/api/glb/{sid}/still")
    async def glb_still(request):
        """A snapshot of the view (PNG or JPEG body). Saved to the Gallery and copied to the input folder (so a storyboard
        shot can use it as its first frame). Query: name, camera, t (the moment), kind=location to list it with the set's
        location shots."""
        entry = _get(request)
        q = request.query
        raw = await request.read()
        ext = picture_ext(raw)
        if not ext or len(raw) > MAX_PICTURE:
            raise web.HTTPBadRequest(text="Send a PNG or JPEG picture")
        d, sub = out_dir(entry, "Images")
        name = str(q.get("name") or "").strip()[:80] or "Snapshot"
        path = free_path(d, f"{safe_stem(name, 'Snapshot')} {time.strftime('%Y%m%d-%H%M%S')}", ext)
        with open(path, "wb") as f:
            f.write(raw)
        value = H["_to_input"](path, bool(entry.get("private")))
        try:
            t = round(float(q.get("t") or 0), 3)
        except ValueError:
            t = 0.0
        rec = {"id": uuid.uuid4().hex[:8], "name": name, "camera": str(q.get("camera") or "")[:120], "input": value,
               "output": {"filename": os.path.basename(path), "subfolder": sub}, "t": t, "at": time.time(),
               "kind": "location" if q.get("kind") == "location" else "storyboard"}
        if rec["kind"] == "location":
            _modify(entry["id"], lambda e: e.update(stills=(e.get("stills") or [])[-(MAX_STILLS - 1):] + [rec]))
        return web.json_response({"still": rec})

    @routes.delete("/studio/api/glb/{sid}/stills/{still}")
    async def glb_still_delete(request):
        """Takes a location shot off the set's list; its picture stays in the Gallery."""
        entry, gone = _get(request), request.match_info["still"]
        _modify(entry["id"], lambda e: e.update(stills=[x for x in e.get("stills") or [] if x.get("id") != gone],
                                                movies=[x for x in e.get("movies") or [] if x.get("id") != gone]))
        return web.json_response({"ok": True})

    def rec_dir(entry, mid):
        if not re.fullmatch(r"[0-9a-f]{10}", mid or ""):
            raise web.HTTPNotFound(text="No such recording")
        return os.path.join(_cache(entry["id"]), "rec-" + mid)

    @routes.post("/studio/api/glb/{sid}/movie")
    async def glb_movie_start(request):
        """Starts a movie of the view: the viewer renders every frame at its own moment and sends them one by one."""
        entry = _get(request)
        for old in glob.glob(os.path.join(_cache(entry["id"]), "rec-*")):     # recordings left behind a day ago
            if time.time() - os.path.getmtime(old) > 86400:
                shutil.rmtree(old, ignore_errors=True)
        mid = uuid.uuid4().hex[:10]
        os.makedirs(rec_dir(entry, mid))
        return web.json_response({"id": mid})

    @routes.post("/studio/api/glb/{sid}/movie/{mid}/frame")
    async def glb_movie_frame(request):
        entry = _get(request)
        d = rec_dir(entry, request.match_info["mid"])
        if not os.path.isdir(d):
            raise web.HTTPNotFound(text="No such recording")
        try:
            i = int(request.query.get("i", ""))
        except ValueError:
            raise web.HTTPBadRequest(text="Which frame? (i)")
        raw = await request.read()
        if not 0 <= i < MAX_FRAMES or picture_ext(raw) != ".jpg" or len(raw) > MAX_PICTURE:
            raise web.HTTPBadRequest(text="Send the frames as JPEG, numbered from 0")
        with open(os.path.join(d, f"f_{i:05d}.jpg"), "wb") as f:
            f.write(raw)
        return web.json_response({"ok": True})

    @routes.post("/studio/api/glb/{sid}/movie/{mid}/finish")
    async def glb_movie_finish(request):
        """Encodes the frames (H.264 MP4) into the Gallery. Body: {fps, name, camera}."""
        entry = _get(request)
        d = rec_dir(entry, request.match_info["mid"])
        b = await request.json()
        frames = sorted(glob.glob(os.path.join(d, "f_*.jpg")))
        if not frames:
            raise web.HTTPBadRequest(text="No frames were recorded")
        if [os.path.basename(f) for f in frames] != [f"f_{i:05d}.jpg" for i in range(len(frames))]:
            raise web.HTTPBadRequest(text="Some frames are missing: record it again")
        fps = max(1, min(60, int(b.get("fps") or 24)))
        out, sub = out_dir(entry, "Videos")
        name = str(b.get("name") or "").strip()[:80] or "Previz"
        path = free_path(out, f"{safe_stem(name, 'Previz')} {time.strftime('%Y%m%d-%H%M%S')}", ".mp4")
        await H["_ff"]([H["_ffmpeg"](), "-y", "-framerate", str(fps), "-i", os.path.join(d, "f_%05d.jpg"),
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", path])
        shutil.rmtree(d, ignore_errors=True)
        rec = {"id": uuid.uuid4().hex[:8], "name": name, "camera": str(b.get("camera") or "")[:120], "seconds": round(len(frames) / fps, 3),
               "output": {"filename": os.path.basename(path), "subfolder": sub}, "at": time.time()}
        _modify(entry["id"], lambda e: e.update(movies=(e.get("movies") or [])[-(MAX_STILLS - 1):] + [rec]))    # after ffmpeg: the set as it is now
        return web.json_response({"movie": rec})

    @routes.delete("/studio/api/glb/{sid}/movie/{mid}")
    async def glb_movie_cancel(request):
        entry = _get(request)
        shutil.rmtree(rec_dir(entry, request.match_info["mid"]), ignore_errors=True)
        return web.json_response({"ok": True})

    @routes.get("/studio/api/glb/{sid}/thumb.jpg")
    async def glb_thumb(request):
        entry = _get(request)
        p = os.path.join(_cache(entry["id"]), "thumb.jpg")
        if not os.path.isfile(p):
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Cache-Control": "no-cache"})
