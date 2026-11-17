"""Writes small .glb files for the tests: a triangle on the floor plus whatever nodes, cameras and animations a test needs.

Helpers for the two kinds of camera Studio's viewer shows (ComfyUI-Studio/glbsets.py):
- gltf_camera(): a real glTF camera node (looks down -Z, +Y up);
- blender_rig(): an empty posed the way a Blender camera arrives when cameras weren't exported: it looks down its -Y axis
  with -Z up, aimed at a point (the rig's TGT_ target)."""
import json
import math
import struct


def _pad(b, fill=b"\x00"):
    return b + fill * (-len(b) % 4)


def quat_from_basis(x, y, z):
    """The rotation (x, y, z, w) whose columns are the given unit axes."""
    m00, m01, m02 = x[0], y[0], z[0]
    m10, m11, m12 = x[1], y[1], z[1]
    m20, m21, m22 = x[2], y[2], z[2]
    tr = m00 + m11 + m22
    if tr > 0:
        s = 0.5 / math.sqrt(tr + 1.0)
        return [(m21 - m12) * s, (m02 - m20) * s, (m10 - m01) * s, 0.25 / s]
    if m00 > m11 and m00 > m22:
        s = 2.0 * math.sqrt(1.0 + m00 - m11 - m22)
        return [0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s]
    if m11 > m22:
        s = 2.0 * math.sqrt(1.0 + m11 - m00 - m22)
        return [(m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s]
    s = 2.0 * math.sqrt(1.0 + m22 - m00 - m11)
    return [(m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s]


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def look_rotation(pos, at, forward="-Z"):
    """A rotation that points the node's `forward` axis ("-Z" or "-Y") at `at`, keeping the image upright."""
    f = _norm([at[i] - pos[i] for i in range(3)])
    right = _norm(_cross(f, [0, 1, 0]) if abs(f[1]) < 0.999 else [1, 0, 0])
    up = _cross(right, f)
    if forward == "-Z":            # x = right, y = up, z = -forward
        return quat_from_basis(right, up, [-c for c in f])
    # Blender camera as an empty: -Y looks forward, -Z is up, so y = -forward and z = -up
    return quat_from_basis(right, [-c for c in f], [-c for c in up])


def gltf_camera(name, pos, at, yfov=0.6, aspect=16 / 9):
    return {"node": {"name": name, "translation": list(pos), "rotation": look_rotation(pos, at, "-Z")},
            "camera": {"type": "perspective", "perspective": {"yfov": yfov, "aspectRatio": aspect, "znear": 0.1, "zfar": 500}}}


def blender_rig(name, pos, at, target_name=None):
    nodes = [{"name": name, "translation": list(pos), "rotation": look_rotation(pos, at, "-Y")}]
    if target_name:
        nodes.append({"name": target_name, "translation": list(at)})
    return nodes


def make_glb(nodes=(), cameras=(), animations=(), generator="Khronos glTF Blender I/O v5.3.43", extra=None):
    """A .glb with a floor triangle (node 0) and the given nodes after it. `cameras` are gltf_camera() results.
    `animations`: (node name, [times], [[x, y, z], ...]) translation tracks."""
    bin_ = bytearray()
    views, accessors = [], []

    def add(data, count, typ, comp=5126, mn=None, mx=None):
        off = len(bin_)
        bin_.extend(_pad(data))
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data)})
        acc = {"bufferView": len(views) - 1, "componentType": comp, "count": count, "type": typ}
        if mn is not None:
            acc.update(min=mn, max=mx)
        accessors.append(acc)
        return len(accessors) - 1

    tri = [-1, 0, -1, 1, 0, -1, 0, 0, 1]
    pos = add(struct.pack("<9f", *tri), 3, "VEC3", mn=[-1, 0, -1], mx=[1, 0, 1])
    gl_nodes = [{"name": "Floor", "mesh": 0}]
    gl_cams = []
    for c in cameras:
        n = dict(c["node"], camera=len(gl_cams))
        gl_cams.append(c["camera"])
        gl_nodes.append(n)
    gl_nodes += [dict(n) for n in nodes]
    names = {n["name"]: i for i, n in enumerate(gl_nodes)}
    anims = []
    for node_name, times, values in animations:
        t = add(struct.pack(f"<{len(times)}f", *times), len(times), "SCALAR", mn=[min(times)], mx=[max(times)])
        v = add(struct.pack(f"<{len(values) * 3}f", *[c for xyz in values for c in xyz]), len(values), "VEC3")
        anims.append({"name": node_name + "Action", "samplers": [{"input": t, "output": v, "interpolation": "LINEAR"}],
                      "channels": [{"sampler": 0, "target": {"node": names[node_name], "path": "translation"}}]})
    children = {c for n in gl_nodes for c in n.get("children", [])}
    gltf = {"asset": {"version": "2.0", "generator": generator}, "scene": 0,
            "scenes": [{"name": "Scene", "nodes": [i for i in range(len(gl_nodes)) if i not in children]}],
            "nodes": gl_nodes, "meshes": [{"name": "Floor", "primitives": [{"attributes": {"POSITION": pos}}]}],
            "buffers": [{"byteLength": len(bin_)}], "bufferViews": views, "accessors": accessors}
    if gl_cams:
        gltf["cameras"] = gl_cams
    if anims:
        gltf["animations"] = anims
    gltf.update(extra or {})
    js = _pad(json.dumps(gltf).encode(), b" ")
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(bin_), 0x004E4942) + bytes(bin_)
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def studio_set():
    """The set the sandbox tests use: one glTF camera, two Blender rigs (one aimed by a target, one not), an aim target
    whose camera wasn't exported, an armature rig that isn't a camera, and a 2-second animation at 24 fps."""
    nodes = (blender_rig("G_Wide_24mm_Rig", [4, 1.6, 4], [0, 1, 0], "TGT_G_Wide_24mm")
             + blender_rig("G_Top_35mm_Rig", [0, 8, 0.001], [0, 0, 0])
             + [{"name": "TGT_CAM_09_Lost_50mm", "translation": [0, 1, 0]}, {"name": "DRIFT_1_Rig", "translation": [1, 0, 1]}])
    return make_glb(nodes=nodes, cameras=[gltf_camera("Cam_Front", [0, 1.5, 6], [0, 1, 0])],
                    animations=[("G_Wide_24mm_Rig", [1 / 24, 2.0], [[4, 1.6, 4], [3, 1.6, 5]])])
