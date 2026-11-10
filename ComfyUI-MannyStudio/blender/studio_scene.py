"""Studio <-> Blender. Runs inside Blender (background):

    blender -b [set.blend] --factory-startup -P studio_scene.py -- spec.json

spec["mode"]:
  "build"   make a simple blocking set (floor, walls, backdrop picture) and save it as a .blend
  "inspect" list a set's cameras, markers and size, and render a top-down plan picture
  "render"  render shots: an existing camera of the set, or a Studio camera with a move,
            plus optional mannequins for characters; "grey" (fast blocking look) or "lit"
            (the set's own materials and lights, EEVEE)

Writes spec["out"] + "/result.json". Coordinates: metres, x right, y into the set, z up.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector, Matrix

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SPEC = json.load(open(ARGS[0], encoding="utf-8"))
OUT = SPEC["out"]
os.makedirs(OUT, exist_ok=True)
RESULT = {"ok": True}


def log(*a):
    print("[studio]", *a, flush=True)


def finish():
    with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as f:
        json.dump(RESULT, f)


# ------------------------------------------------------------------ helpers
def mat(name, rgb, image=None, emission=False):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if getattr(m, "node_tree", None) is None:
        try:
            m.use_nodes = True   # Blender 4.x; 5.x materials always have nodes
        except Exception:
            pass
    m.diffuse_color = (*rgb, 1.0)
    nt = m.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.8
    if image:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(image, check_existing=True)
        if bsdf is not None:
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            if emission:
                try:
                    nt.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
                    bsdf.inputs["Emission Strength"].default_value = 0.6
                except Exception:
                    pass
        nt.nodes.active = tex
    return m


def add(obj, m=None, coll=None):
    if m is not None:
        obj.data.materials.clear()
        obj.data.materials.append(m)
    if coll is not None:
        for c in list(obj.users_collection):
            c.objects.unlink(obj)
        coll.objects.link(obj)
    return obj


def collection(name):
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(c)
    return c


def box(name, size, loc, m, coll):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.active_object
    o.name = name
    o.scale = size
    bpy.ops.object.transform_apply(scale=True)
    return add(o, m, coll)


def plane(name, w, h, loc, rot, m, coll):
    bpy.ops.mesh.primitive_plane_add(size=1, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    o.scale = (w, h, 1)
    bpy.ops.object.transform_apply(scale=True)
    return add(o, m, coll)


def scene_bounds():
    """x/y extent of the set's visible geometry (ignores cameras, lights, Studio mannequins)."""
    lo, hi = Vector((1e9, 1e9, 1e9)), Vector((-1e9, -1e9, -1e9))
    n = 0
    for o in bpy.context.scene.objects:
        if o.type != "MESH" or o.hide_render or o.name.startswith("Studio_"):
            continue
        for c in o.bound_box:
            p = o.matrix_world @ Vector(c)
            lo = Vector((min(lo.x, p.x), min(lo.y, p.y), min(lo.z, p.z)))
            hi = Vector((max(hi.x, p.x), max(hi.y, p.y), max(hi.z, p.z)))
            n += 1
    if not n:
        return Vector((-5, -5, 0)), Vector((5, 5, 3))
    # very large ground planes would make the plan useless: clamp to 60 m around the centre
    c = (lo + hi) / 2
    for a in "xy":
        if getattr(hi, a) - getattr(lo, a) > 60:
            setattr(lo, a, getattr(c, a) - 30)
            setattr(hi, a, getattr(c, a) + 30)
    return lo, hi


def look_at(obj, target):
    d = Vector(target) - obj.location
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def setup_render(w, h, look, fps=24):
    s = bpy.context.scene
    s.render.resolution_x, s.render.resolution_y = int(w), int(h)
    s.render.resolution_percentage = 100
    s.render.fps = int(fps)
    s.render.image_settings.file_format = "PNG"
    try:
        s.render.image_settings.media_type = "IMAGE"
    except Exception:
        pass
    if look == "lit":
        s.render.engine = "BLENDER_EEVEE"
        try:
            s.eevee.taa_render_samples = 16
        except Exception:
            pass
    else:
        s.render.engine = "BLENDER_WORKBENCH"
        sh = s.display.shading
        sh.light = "STUDIO"
        sh.color_type = "TEXTURE"
        try:
            sh.show_cavity = True
            sh.cavity_type = "WORLD"
            sh.show_shadows = True
            sh.shadow_intensity = 0.35
        except Exception:
            pass
        s.display.shading.background_type = "WORLD" if hasattr(s.display.shading, "background_type") else None
        if s.world is None:
            s.world = bpy.data.worlds.new("Studio_World")
        s.world.color = (0.32, 0.33, 0.36)


# ------------------------------------------------------------------ build a simple set
def build():
    st = SPEC.get("set") or {}
    kind = st.get("kind", "room")
    W, D, H = float(st.get("w", 8)), float(st.get("d", 8)), float(st.get("h", 3.2))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    coll = collection("Set")
    floor_m = mat("Floor", (0.42, 0.41, 0.4))
    wall_m = mat("Wall", (0.62, 0.61, 0.6))
    back = st.get("backdrop")
    back_m = mat("Backdrop", (0.6, 0.6, 0.6), back, emission=True) if back and os.path.isfile(back) else wall_m
    plane("Floor", W, D, (0, 0, 0), (0, 0, 0), floor_m, coll)
    if kind in ("room", "stage"):
        plane("Back wall", W, H, (0, D / 2, H / 2), (math.pi / 2, 0, 0), back_m, coll)
    if kind == "room":
        plane("Left wall", D, H, (-W / 2, 0, H / 2), (math.pi / 2, 0, math.pi / 2), wall_m, coll)
        plane("Right wall", D, H, (W / 2, 0, H / 2), (math.pi / 2, 0, -math.pi / 2), wall_m, coll)
    if kind == "street":
        bm = mat("Buildings", (0.55, 0.54, 0.56))
        for side in (-1, 1):
            x = side * (W / 2 + 2)
            for k in range(int(D // 4) + 1):
                y = -D / 2 + 2 + k * 4
                hh = 6 + (k * 37 % 7)
                box(f"Building {side}_{k}", (4, 3.6, hh), (x, y, hh / 2), bm, coll)
        if back and os.path.isfile(back):
            plane("Backdrop", W + 8, H * 3, (0, D / 2 + 2, H * 1.5), (math.pi / 2, 0, 0), back_m, coll)
    if kind == "exterior" and back and os.path.isfile(back):
        plane("Backdrop", W * 2, H * 3, (0, D / 2, H * 1.5), (math.pi / 2, 0, 0), back_m, coll)
    for p in st.get("props") or []:
        pm = mat("Prop", (0.5, 0.47, 0.44))
        box(p.get("name", "Prop"), (p.get("w", 1), p.get("d", 1), p.get("h", 1)), (p["x"], p["y"], p.get("h", 1) / 2), pm, coll)
    # a sun and a sky so the lit look works too
    sun = bpy.data.lights.new("Studio_Key", "SUN")
    sun.energy = 3
    so = bpy.data.objects.new("Studio_Key", sun)
    so.rotation_euler = (math.radians(50), 0, math.radians(30))
    bpy.context.scene.collection.objects.link(so)
    w = bpy.data.worlds.new("World")
    w.color = (0.32, 0.33, 0.36)
    bpy.context.scene.world = w
    cam = bpy.data.cameras.new("Main")
    co = bpy.data.objects.new("Main", cam)
    co.location = (0, -D / 2 + 0.6, 1.6)
    bpy.context.scene.collection.objects.link(co)
    look_at(co, (0, D / 4, 1.3))
    bpy.context.scene.camera = co
    path = SPEC["save"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path)
    RESULT["blend"] = path
    inspect()


# ------------------------------------------------------------------ inspect a set
def inspect():
    s = bpy.context.scene
    cams = []
    for o in s.objects:
        if o.type != "CAMERA" or o.name.startswith("Studio_"):
            continue
        fwd = o.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        tgt = o.matrix_world.translation + fwd * 4
        animated = bool(o.animation_data and o.animation_data.action)
        cams.append({"name": o.name, "lens": round(o.data.lens, 1), "x": round(o.matrix_world.translation.x, 3),
                     "y": round(o.matrix_world.translation.y, 3), "z": round(o.matrix_world.translation.z, 3),
                     "tx": round(tgt.x, 3), "ty": round(tgt.y, 3), "tz": round(tgt.z, 3), "animated": animated})
    markers = [{"name": m.name, "frame": m.frame, "camera": m.camera.name if m.camera else None} for m in s.timeline_markers]
    markers.sort(key=lambda m: m["frame"])
    lo, hi = scene_bounds()
    RESULT.update({"cameras": cams, "markers": markers, "frame_start": s.frame_start, "frame_end": s.frame_end,
                   "fps": s.render.fps, "bounds": [round(lo.x, 2), round(lo.y, 2), round(hi.x, 2), round(hi.y, 2), round(hi.z, 2)],
                   "scene": s.name, "active_camera": s.camera.name if s.camera else None})
    plan(lo, hi)


def plan(lo, hi):
    """Top-down orthographic picture of the set, used as the map for placing cameras in Studio."""
    s = bpy.context.scene
    c = (lo + hi) / 2
    span = max(hi.x - lo.x, hi.y - lo.y) * 1.08 + 0.5
    cam = bpy.data.cameras.new("Studio_Plan")
    cam.type = "ORTHO"
    cam.ortho_scale = span
    co = bpy.data.objects.new("Studio_Plan", cam)
    co.location = (c.x, c.y, hi.z + 20)
    co.rotation_euler = (0, 0, 0)
    cam.clip_end = hi.z + 60
    s.collection.objects.link(co)
    prev = s.camera
    s.camera = co
    # hide ceilings and anything high above head height so the plan shows the floor
    hidden = []
    for o in s.objects:
        if o.type == "MESH" and not o.hide_render:
            zs = [(o.matrix_world @ Vector(b)).z for b in o.bound_box]
            if min(zs) > 2.6 and (max(zs) - min(zs)) < 1.0:
                o.hide_render = True
                hidden.append(o)
    setup_render(768, 768, "grey")
    s.render.filepath = os.path.join(OUT, "plan.png")
    bpy.ops.render.render(write_still=True)
    for o in hidden:
        o.hide_render = False
    s.camera = prev
    bpy.data.objects.remove(co)
    RESULT["plan"] = {"file": "plan.png", "cx": round(c.x, 3), "cy": round(c.y, 3), "span": round(span, 3)}


# ------------------------------------------------------------------ mannequins
def mannequins(chars, frames):
    coll = collection("Studio_Cast")
    for k, ch in enumerate(chars):
        hgt = float(ch.get("height", 1.75))
        rgb = tuple(ch.get("color") or (0.85, 0.55, 0.35))
        m = mat(f"Studio_Cast_{k}", rgb)
        bpy.ops.object.empty_add(location=(ch["x"], ch["y"], 0))
        root = bpy.context.active_object
        root.name = f"Studio_{ch.get('name', 'Person')}"
        add(root, None, coll)
        parts = []
        bpy.ops.mesh.primitive_cylinder_add(radius=0.19 * hgt / 1.75, depth=hgt * 0.62, location=(0, 0, hgt * 0.48))
        parts.append(bpy.context.active_object)
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.12 * hgt / 1.75, location=(0, 0, hgt * 0.89))
        parts.append(bpy.context.active_object)
        bpy.ops.mesh.primitive_cylinder_add(radius=0.08 * hgt / 1.75, depth=hgt * 0.45, location=(0.1, 0, hgt * 0.2))
        parts.append(bpy.context.active_object)
        bpy.ops.mesh.primitive_cylinder_add(radius=0.08 * hgt / 1.75, depth=hgt * 0.45, location=(-0.1, 0, hgt * 0.2))
        parts.append(bpy.context.active_object)
        bpy.ops.mesh.primitive_cone_add(radius1=0.05, depth=0.12, location=(0, 0.14 * hgt / 1.75, hgt * 0.89), rotation=(-math.pi / 2, 0, 0))
        parts.append(bpy.context.active_object)
        for p in parts:
            add(p, m, coll)
            p.parent = root
        face = math.radians(float(ch.get("rot", 0)))
        root.rotation_euler = (0, 0, face)
        to = ch.get("to")
        if to and frames > 1:
            root.keyframe_insert("location", frame=1)
            ang = math.atan2(to[1] - ch["y"], to[0] - ch["x"]) - math.pi / 2
            root.rotation_euler = (0, 0, ang)
            root.keyframe_insert("rotation_euler", frame=1)
            root.location = (to[0], to[1], 0)
            root.keyframe_insert("location", frame=frames)
            root.keyframe_insert("rotation_euler", frame=frames)


# ------------------------------------------------------------------ camera moves (same maths as Studio's plan view)
def move_end(P, T, lens, move, amt):
    P, T = Vector(P), Vector(T)
    d = (T - P)
    flat = Vector((d.x, d.y, 0)).normalized() if d.length > 1e-6 else Vector((0, 1, 0))
    side = Vector((flat.y, -flat.x, 0))
    P2, T2, L2 = P.copy(), T.copy(), lens

    def rot(v, c, deg):
        a = math.radians(deg)
        r = v - c
        return c + Vector((r.x * math.cos(a) - r.y * math.sin(a), r.x * math.sin(a) + r.y * math.cos(a), r.z))
    if move == "push_in":
        P2 = P + d.normalized() * amt
    elif move == "pull_back":
        P2 = P - d.normalized() * amt
    elif move == "truck_left":
        P2, T2 = P - side * amt, T - side * amt
    elif move == "truck_right":
        P2, T2 = P + side * amt, T + side * amt
    elif move == "orbit_left":
        P2 = rot(P, T, amt)
    elif move == "orbit_right":
        P2 = rot(P, T, -amt)
    elif move == "crane_up":
        P2 = P + Vector((0, 0, amt))
    elif move == "crane_down":
        P2 = P - Vector((0, 0, amt))
    elif move == "pan_left":
        T2 = rot(T, P, amt)
    elif move == "pan_right":
        T2 = rot(T, P, -amt)
    elif move == "tilt_up":
        T2 = T + Vector((0, 0, amt))
    elif move == "tilt_down":
        T2 = T - Vector((0, 0, amt))
    elif move == "zoom_in":
        L2 = lens * (1 + amt)
    elif move == "zoom_out":
        L2 = max(8, lens / (1 + amt))
    return P2, T2, L2


def studio_camera(sh, frames):
    c = sh["cam"]
    cam = bpy.data.cameras.new("Studio_Shot")
    cam.lens = float(c.get("lens", 35))
    co = bpy.data.objects.new("Studio_Shot", cam)
    bpy.context.scene.collection.objects.link(co)
    tgt = bpy.data.objects.new("Studio_Target", None)
    bpy.context.scene.collection.objects.link(tgt)
    P = (c["x"], c["y"], c.get("z", 1.6))
    T = (c["tx"], c["ty"], c.get("tz", 1.4))
    move, amt = sh.get("move", "static"), float(sh.get("amount", 1.0))
    P2, T2, L2 = move_end(P, T, cam.lens, move, amt)
    con = co.constraints.new("TRACK_TO")
    con.target = tgt
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    co.location, tgt.location = P, T
    for ob, key in ((co, "location"), (tgt, "location")):
        ob.keyframe_insert(key, frame=1)
    cam.keyframe_insert("lens", frame=1)
    co.location, tgt.location, cam.lens = P2, T2, L2
    for ob, key in ((co, "location"), (tgt, "location")):
        ob.keyframe_insert(key, frame=frames)
    cam.keyframe_insert("lens", frame=frames)
    if move == "handheld":
        for ob in (co, tgt):
            ob.animation_data_create()
            for fc in _fcurves(ob):
                mod = fc.modifiers.new("NOISE")
                mod.scale, mod.strength = 18, 0.035 if ob is co else 0.06
    return co


def _fcurves(ob):
    ad = ob.animation_data
    if not ad or not ad.action:
        return []
    try:
        return list(ad.action.fcurves)
    except AttributeError:   # layered actions (Blender 4.4+)
        out = []
        for layer in ad.action.layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    out += list(cb.fcurves)
        return out


def render_shots():
    s = bpy.context.scene
    look = SPEC.get("look", "grey")
    W, H = SPEC.get("size", [864, 480])
    fps = int(SPEC.get("fps", 24))
    setup_render(W, H, look, fps)
    results = []
    for sh in SPEC.get("shots", []):
        sid = sh["id"]
        d = os.path.join(OUT, sid)
        os.makedirs(d, exist_ok=True)
        if sh.get("camera"):                       # a camera that already exists in the .blend
            co = bpy.data.objects.get(sh["camera"])
            if co is None:
                results.append({"id": sid, "error": "no camera " + sh["camera"]})
                continue
            f0 = int(sh.get("start") or s.frame_start)
            f1 = int(sh.get("end") or (f0 + int(float(sh.get("seconds", 5)) * fps) - 1))
            frames = f1 - f0 + 1
            for o in [o for o in s.objects if o.name.startswith("Studio_Shot") or o.name.startswith("Studio_Target")]:
                bpy.data.objects.remove(o)
        else:                                      # a Studio camera with a move
            frames = int(round(float(sh.get("seconds", 5)) * fps)) + 1
            f0, f1 = 1, frames
            co = studio_camera(sh, frames)
        old = bpy.data.collections.get("Studio_Cast")
        if old:
            for o in list(old.objects):
                bpy.data.objects.remove(o)
        if sh.get("cast"):
            mannequins(sh["cast"], frames)
        s.camera = co
        s.frame_start, s.frame_end = f0, f1
        s.render.filepath = os.path.join(d, "f_")
        log("render", sid, f0, f1)
        bpy.ops.render.render(animation=True)
        results.append({"id": sid, "dir": d, "frames": frames, "fps": fps, "start": f0})
        for o in [o for o in s.objects if o.name in ("Studio_Shot", "Studio_Target")]:
            bpy.data.objects.remove(o)
    RESULT["shots"] = results


try:
    mode = SPEC.get("mode")
    if mode == "build":
        build()
    elif mode == "inspect":
        inspect()
    elif mode == "render":
        render_shots()
    else:
        raise ValueError("unknown mode " + str(mode))
except Exception as e:
    import traceback
    traceback.print_exc()
    RESULT = {"ok": False, "error": f"{type(e).__name__}: {e}"}
finish()
