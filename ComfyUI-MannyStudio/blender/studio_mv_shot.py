"""Studio music video: render one shot of a planned .blend through its own camera, in the background.

blender -b <file.blend> --factory-startup -P studio_mv_shot.py -- spec.json
spec: {"camera": "G_LowHero_18mm", "f0": 1, "f1": 105, "w": 1344, "h": 576, "dw": 672, "dh": 288,
       "near": 0.3, "far": 45.0, "out": "<folder>", "frame_at": 1}
Writes <out>/first.png (the shot's first frame, the file's own look, EEVEE), <out>/depth/d_####.png
(near = white, far = black, every frame of the shot) and <out>/result.json. The .blend is never saved."""
import json
import os
import sys
import traceback

import bpy

SPEC = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
OUT = SPEC["out"]
os.makedirs(os.path.join(OUT, "depth"), exist_ok=True)


def log(*a):
    print("[studio]", *a, flush=True)


def eevee(scene):
    for e in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = e
            return e
        except Exception:
            pass
    scene.render.engine = "BLENDER_WORKBENCH"
    return "BLENDER_WORKBENCH"


def depth_material(near, far):
    m = bpy.data.materials.new("STUDIO_DEPTH")
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    cam = nt.nodes.new("ShaderNodeCameraData")
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = near
    mr.inputs["From Max"].default_value = far
    mr.inputs["To Min"].default_value = 1.0
    mr.inputs["To Max"].default_value = 0.0
    mr.clamp = True
    em = nt.nodes.new("ShaderNodeEmission")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(cam.outputs["View Z Depth"], mr.inputs["Value"])
    nt.links.new(mr.outputs["Result"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return m


def main():
    scene = bpy.context.scene
    cam = bpy.data.objects.get(SPEC["camera"])
    if not cam or cam.type != "CAMERA":
        raise RuntimeError(f"No camera called {SPEC['camera']}")
    scene.camera = cam
    for mk in list(scene.timeline_markers):        # markers that switch cameras would fight the one we chose
        if mk.camera:
            mk.camera = cam
    r = scene.render
    r.resolution_percentage = 100
    r.use_motion_blur = False
    r.image_settings.file_format = "PNG"
    r.film_transparent = False
    eng = eevee(scene)
    # 1) the first frame, the file's own look
    r.resolution_x, r.resolution_y = SPEC["w"], SPEC["h"]
    scene.frame_set(int(SPEC.get("frame_at", SPEC["f0"])))
    r.filepath = os.path.join(OUT, "first.png")
    log("render first frame", eng)
    bpy.ops.render.render(write_still=True)
    # 2) depth of every frame: one emission material on everything, black world
    world = bpy.data.worlds.new("STUDIO_BLACK")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0, 0, 0, 1)
        bg.inputs["Strength"].default_value = 0.0
    scene.world = world
    vl = bpy.context.view_layer
    vl.material_override = depth_material(float(SPEC.get("near", 0.3)), float(SPEC.get("far", 45.0)))
    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
    except Exception:
        pass
    try:
        scene.eevee.use_bloom = False
    except Exception:
        pass
    r.resolution_x, r.resolution_y = SPEC["dw"], SPEC["dh"]
    n = 0
    for f in range(int(SPEC["f0"]), int(SPEC["f1"]) + 1):
        scene.frame_set(f)
        r.filepath = os.path.join(OUT, "depth", f"d_{n:04d}.png")
        bpy.ops.render.render(write_still=True)
        n += 1
        log(f"render depth {n}/{int(SPEC['f1']) - int(SPEC['f0']) + 1}")
    lens = cam.data.lens
    json.dump({"ok": True, "engine": eng, "frames": n, "lens": lens, "fps": scene.render.fps}, open(os.path.join(OUT, "result.json"), "w"))


try:
    main()
except Exception as e:
    traceback.print_exc()
    json.dump({"ok": False, "error": str(e)}, open(os.path.join(OUT, "result.json"), "w"))
