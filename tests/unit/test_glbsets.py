"""Previz sets (ComfyUI-Studio/glbsets.py): reading a .glb's JSON chunk and finding what the 3D viewer needs: its cameras
(glTF cameras and the camera rigs a Blender export leaves when cameras weren't ticked), how to aim each one, the lens,
the animation's length, and the cameras that didn't make it into the file."""
import importlib.util
import math
import os
import struct
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "tests"))
import glbfile  # noqa: E402

_spec = importlib.util.spec_from_file_location("studio_glbsets", os.path.join(REPO, "ComfyUI-Studio", "glbsets.py"))
gs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gs)
SQUARED = os.path.join(REPO, "3D sets", "Squared Sets.glb")        # exported without cameras: only their rigs
SQUARED_V2 = os.path.join(REPO, "3D sets", "Squared V2.glb")        # exported again with cameras and lights


def info_of(data, tmp_path, name="set.glb"):
    p = tmp_path / name
    p.write_bytes(data)
    return gs.inspect_file(str(p))


def by_name(info):
    return {c["name"]: c for c in info["cameras"]}


def aim_error_deg(node, cam, at):
    """How far (degrees) the camera's forward axis, as Studio reports it, points from `at`."""
    m = gs._local_matrix(node)
    f = gs._axis_world(m, cam["forward"])
    d = gs._norm([at[i] - m[i][3] for i in range(3)])
    return math.degrees(math.acos(max(-1, min(1, sum(a * b for a, b in zip(f, d))))))


# ------------------------------------------------------------------ reading the file
def test_reads_only_the_json_chunk(tmp_path):
    info = info_of(glbfile.make_glb(), tmp_path)
    assert info["meshes"] == 1 and info["nodes"] == 1 and info["cameras"] == [] and info["missing"] == []
    assert info["size"] == (tmp_path / "set.glb").stat().st_size


@pytest.mark.parametrize("data,msg", [
    (b"PK\x03\x04" + b"\x00" * 40, "isn't a .glb"),
    (b"glTF" + struct.pack("<II", 1, 40) + b"\x00" * 28, "glTF 1"),
    (b"glTF" + struct.pack("<IIII", 2, 100, 500, 0x4E4F534A) + b"{}", "cut short"),
    (b"glTF" + struct.pack("<IIII", 2, 40, 8, 0x004E4942) + b"\x00" * 8, "no readable JSON"),
    (b"glTF" + struct.pack("<IIII", 2, 40, 4, 0x4E4F534A) + b"{{{{", "can't be read"),
    (b"glTF" + struct.pack("<IIII", 2, 22, 4, 0x4E4F534A) + b"{}  ", "damaged"),
    (b"glTF", "isn't a .glb"),
])
def test_bad_files_are_refused_with_a_reason(tmp_path, data, msg):
    p = tmp_path / "bad.glb"
    p.write_bytes(data)
    with pytest.raises(gs.NotGlb, match=msg):
        gs.read_gltf_json(str(p))


def test_looks_like_glb():
    assert gs.looks_like_glb(glbfile.make_glb()[:12])
    assert not gs.looks_like_glb(b"glTF" + struct.pack("<II", 1, 0))
    assert not gs.looks_like_glb(b"{}")


# ------------------------------------------------------------------ cameras
def test_gltf_camera_lens_and_axes(tmp_path):
    cam = glbfile.gltf_camera("Cam_Front", [0, 1.5, 6], [0, 1, 0], yfov=2 * math.atan(18 / 35 / (16 / 9)), aspect=16 / 9)
    c = by_name(info_of(glbfile.make_glb(cameras=[cam]), tmp_path))["Cam_Front"]
    assert c["kind"] == "camera" and c["forward"] == "-Z" and c["up"] == "+Y" and c["lens_known"]
    assert c["lens"] == pytest.approx(35, abs=0.1)         # 36 mm sensor across the wider side, like Blender
    assert c["aspect"] == pytest.approx(16 / 9) and c["animated"] is False


def test_a_portrait_camera_puts_the_sensor_on_its_height(tmp_path):
    cam = glbfile.gltf_camera("Tall", [0, 1, 5], [0, 1, 0], yfov=2 * math.atan(18 / 50), aspect=9 / 16)
    assert by_name(info_of(glbfile.make_glb(cameras=[cam]), tmp_path))["Tall"]["lens"] == pytest.approx(50, abs=0.1)


def test_orthographic_cameras(tmp_path):
    cam = {"node": {"name": "Plan"}, "camera": {"type": "orthographic", "orthographic": {"xmag": 46, "ymag": 19.25, "znear": 0.1, "zfar": 100}}}
    c = by_name(info_of(glbfile.make_glb(cameras=[cam]), tmp_path))["Plan"]
    assert c["kind"] == "ortho" and c["lens"] is None and c["xmag"] == 46 and c["ymag"] == 19.25 and c["forward"] == "-Z"


def test_camera_ids_are_their_names(tmp_path):
    """So the last camera you used (and directing saved per camera) survives exporting the file again."""
    cams = [glbfile.gltf_camera("Wide", [0, 1, 5], [0, 1, 0]), glbfile.gltf_camera("Wide", [3, 1, 5], [0, 1, 0]), glbfile.gltf_camera("CU", [0, 1, 2], [0, 1, 0])]
    info = info_of(glbfile.make_glb(cameras=cams, nodes=glbfile.blender_rig("G_Top_28mm_Rig", [0, 9, 0], [0, 0, 0])), tmp_path)
    assert [c["id"] for c in info["cameras"]] == ["Wide", "Wide#2", "CU", "G_Top_28mm"]


def test_blender_rig_aimed_by_its_target(tmp_path):
    pos, at = [4, 1.6, 4], [0, 1, 0]
    info = info_of(glbfile.make_glb(nodes=glbfile.blender_rig("G_Wide_24mm_Rig", pos, at, "TGT_G_Wide_24mm")), tmp_path)
    c = by_name(info)["G_Wide_24mm"]          # shown without the _Rig
    assert c["kind"] == "rig" and c["forward"] == "-Y" and c["up"] == "-Z" and c["aim"] == "target"
    assert c["lens"] == 24 and c["lens_known"] and info["missing"] == []
    assert aim_error_deg(glbfile.blender_rig("x", pos, at)[0], c, at) < 0.01


def test_rig_looking_down_minus_z_is_found_from_its_target(tmp_path):
    """A rig set up like a glTF camera (not Blender's axes) is aimed by its target too."""
    nodes = [{"name": "ShotCam", "translation": [0, 2, 8], "rotation": glbfile.look_rotation([0, 2, 8], [0, 0, 0], "-Z")},
             {"name": "ShotCam_Target", "translation": [0, 0, 0]}]
    c = by_name(info_of(glbfile.make_glb(nodes=nodes, generator="Maya"), tmp_path))["ShotCam"]
    assert c["forward"] == "-Z" and c["up"] == "+Y" and c["aim"] == "target" and c["lens"] == 50 and not c["lens_known"]


def test_rig_without_target_follows_the_files_other_rigs(tmp_path):
    nodes = (glbfile.blender_rig("A_28mm_Rig", [3, 2, 3], [0, 1, 0], "TGT_A_28mm")
             + [{"name": "TopDown_28mm_Rig", "translation": [0, 9, 0]}])      # no rotation: Blender's -Y looks straight down
    c = by_name(info_of(glbfile.make_glb(nodes=nodes), tmp_path))["TopDown_28mm"]
    assert c["aim"] == "guess" and c["forward"] == "-Y" and c["up"] == "-Z"


def test_rig_without_any_target_uses_the_exporters_convention(tmp_path):
    nodes = [{"name": "CAM_01", "translation": [0, 2, 5]}]
    assert by_name(info_of(glbfile.make_glb(nodes=nodes), tmp_path))["CAM_01"]["forward"] == "-Y"
    assert by_name(info_of(glbfile.make_glb(nodes=nodes, generator="three.js"), tmp_path, "b.glb"))["CAM_01"]["forward"] == "-Z"


def test_targets_without_their_camera_are_listed_as_missing(tmp_path):
    nodes = (glbfile.blender_rig("G_CU_50mm_Rig", [1, 1.5, 1], [0, 1.5, 0], "TGT_G_CU_50mm")
             + [{"name": "TGT_CAM_01_Corridor_24mm", "translation": [0, 1, 0]}, {"name": "TGT_S_Black", "translation": [0, -700, 0]}])
    info = info_of(glbfile.make_glb(nodes=nodes), tmp_path)
    assert [c["name"] for c in info["cameras"]] == ["G_CU_50mm"]
    assert info["missing"] == ["CAM_01_Corridor_24mm"]        # TGT_S_Black isn't named like a camera


def test_a_target_whose_camera_is_a_real_gltf_camera_isnt_missing(tmp_path):
    cam = glbfile.gltf_camera("CAM_02_Hall_35mm", [0, 1.5, 6], [0, 1, 0])
    info = info_of(glbfile.make_glb(cameras=[cam], nodes=[{"name": "TGT_CAM_02_Hall_35mm", "translation": [0, 1, 0]}]), tmp_path)
    assert info["missing"] == [] and [c["kind"] for c in info["cameras"]] == ["camera"]


def test_what_isnt_a_camera(tmp_path):
    nodes = [
        {"name": "DRIFT_1_Rig", "children": [3]}, {"name": "Hips"},          # an armature rig, not a camera
        {"name": "Body_35mm", "mesh": 0},                                    # a mesh, whatever its name
        {"name": "TGT_Lonely"},                                              # an aim target
        {"name": "Cam_Group", "children": [6, 7]}, {"name": "CamA_Rig"}, {"name": "CamB_Rig"},   # a group of rigs isn't one
        {"name": "CameraDolly_Rig", "children": [9]}, {"name": "ShotCamera", "camera": 0},   # a real camera's parent isn't one
        {"name": "Camel_Statue"}, {"name": "Scam_Sign"},                      # "cam" inside a word
    ]
    nodes[0]["children"] = [2]
    extra = {"cameras": [{"type": "perspective", "perspective": {"yfov": 0.7}}], "skins": [{"joints": [2]}]}
    info = info_of(glbfile.make_glb(nodes=nodes, extra=extra), tmp_path)
    assert sorted(c["name"] for c in info["cameras"]) == ["CamA", "CamB", "ShotCamera"]


def test_animated_cameras_and_timeline(tmp_path):
    nodes = [{"name": "Crane", "children": [2]}, {"name": "CAM_Crane_21mm"}]
    data = glbfile.make_glb(nodes=nodes, animations=[("Crane", [1 / 24, 5.0], [[0, 0, 0], [0, 3, 0]])])
    info = info_of(data, tmp_path)
    c = by_name(info)["CAM_Crane_21mm"]
    assert c["animated"] is True                      # moved by its parent
    assert info["duration"] == pytest.approx(5.0) and info["fps"] == 24 and info["animations"] == 1


@pytest.mark.parametrize("times,fps", [([1 / 24, 3], 24), ([1 / 30, 2], 30), ([1 / 25, 2], 25), ([0, 2], 24), ([0.0123, 1], 24)])
def test_frame_rate_guess(times, fps):
    assert gs._guess_fps(times) == fps


@pytest.mark.parametrize("name,lens", [("G_CU_50mm_Rig", 50), ("BCAM_05A_Forest_Telephoto_100mm", 100), ("Cam 35 mm", 35),
                                       ("lens_18.5mm", 18.5), ("Room_2mmm", None), ("CAM_01", None), ("Box_9000mm", None)])
def test_lens_from_name(name, lens):
    assert gs.lens_from_name(name) == lens


def test_file_names():
    assert gs.safe_stem("C:\\Users\\me\\My Set v2.glb") == "My Set v2"
    assert gs.safe_stem("../../etc/passwd") == "passwd"
    assert gs.safe_stem("") == "Set" and gs.safe_stem("???.glb") == "Set"


def test_free_path(tmp_path):
    (tmp_path / "Set.glb").write_bytes(b"x")
    (tmp_path / "Set 2.glb").write_bytes(b"x")
    assert os.path.basename(gs.free_path(str(tmp_path), "Set")) == "Set 3.glb"


# ------------------------------------------------------------------ the real set in the repo
@pytest.mark.skipif(not os.path.isfile(SQUARED), reason="3D sets/Squared Sets.glb isn't here")
def test_squared_sets():
    """Exported from Blender without cameras: 24 camera rigs (aimed by their TGT_ targets, the lens in the name) and the
    aim targets of 23 cameras that weren't exported."""
    info = gs.inspect_file(SQUARED)
    cams = by_name(info)
    assert len(cams) == 24 and len(info["missing"]) == 23
    assert all(c["kind"] == "rig" and c["forward"] == "-Y" and c["up"] == "-Z" and c["animated"] for c in cams.values())
    assert sum(c["aim"] == "target" for c in cams.values()) == 23 and cams["G_TopDown_28mm"]["aim"] == "guess"
    assert cams["G_Profile_85mm"]["lens"] == 85 and cams["S_Ext_Orbit_24mm"]["lens"] == 24
    assert "CAM_01_Corridor_24mm" in info["missing"] and "BCAM_05A_Forest_Telephoto_100mm" in info["missing"]
    assert info["duration"] == pytest.approx(221.9583, abs=1e-3) and info["fps"] == 24 and info["animations"] == 117


@pytest.mark.skipif(not os.path.isfile(SQUARED_V2), reason="3D sets/Squared V2.glb isn't here")
def test_squared_v2():
    """Exported with Include › Cameras: 53 real cameras (one orthographic), none missing, and the rigs that carry some of
    them aren't listed twice. Some names say one lens and the camera has another: the camera's own field of view wins."""
    info = gs.inspect_file(SQUARED_V2)
    cams = by_name(info)
    assert len(cams) == 53 and info["missing"] == [] and info["lights"] == 19
    assert [c["name"] for c in cams.values() if c["kind"] == "ortho"] == ["CAM_TopDown_Ortho"]
    assert all(c["kind"] in ("camera", "ortho") for c in cams.values()), "no rigs: their cameras are in the file"
    assert cams["CAM_03_Chamber_35mm"]["lens"] == 35 and cams["G_Profile_85mm"]["lens"] == 85
    assert cams["G_CU_50mm"]["lens"] == 28                       # the name says 50 mm; the camera is 28 mm
    assert all(abs(c["aspect"] - 2.391) < 0.001 for c in cams.values() if c["kind"] == "camera")
    assert sorted(n for n, c in cams.items() if not c["animated"]) == ["CAM_Corridor_Plate", "CAM_CubeOverview", "CAM_TopDown_Ortho"]



# ------------------------------------------------------------------ quick scenes: a blank stage, nothing moving
@pytest.mark.parametrize("stage", ["cyc", "room", "open"])
def test_quick_scene_stage(tmp_path, stage):
    """A valid .glb: the stage, one camera at eye level looking at the middle of the floor, no animation, no lights."""
    info = info_of(gs.quick_scene_glb(stage), tmp_path)
    assert info["animations"] == 0 and info["duration"] == 0 and info["lights"] == 0 and info["meshes"] == 1
    [cam] = info["cameras"]
    assert cam["name"] == "Camera" and cam["kind"] == "camera" and cam["lens"] == 35 and not cam["animated"]
    assert abs(cam["aspect"] - 16 / 9) < 1e-6
    gltf = gs.read_gltf_json(str(tmp_path / "set.glb"))
    node = gltf["nodes"][cam["node"]]
    assert abs(node["translation"][1] - 1.6) < 1e-9, "eye level"
    assert aim_error_deg(node, cam, [0, 1.25 if stage != "open" else 1.2, 0]) < 0.01, "looks at the subject's spot"
    prim = gltf["meshes"][0]["primitives"]
    assert all("NORMAL" in p["attributes"] and "indices" in p for p in prim)
    assert {gltf["materials"][p["material"]]["name"] for p in prim} >= {"floor", "floor2"}, "a floor in squares, for scale"


def test_quick_scene_people():
    one, two, none = gs.quick_direction(1), gs.quick_direction(2), gs.quick_direction(0)
    assert [a["name"] for a in one["added"]] == ["Subject"] and one["subject"] == "added:" + one["added"][0]["id"] and one["with"] is None
    a, b = two["added"]
    assert two["subject"] == "added:" + a["id"] and two["with"] == "added:" + b["id"]
    face = lambda q: [2 * (q[0] * q[2] + q[1] * q[3]), 0, 1 - 2 * (q[0] ** 2 + q[1] ** 2)]    # +Z turned by q
    towards = lambda x, y: [y["p"][0] - x["p"][0], 0, y["p"][2] - x["p"][2]]
    for x, y in ((a, b), (b, a)):
        f, d = face(x["q"]), towards(x, y)
        assert sum(u * v for u, v in zip(f, d)) / math.hypot(*d) > 0.999, "the two face each other"
    assert none["added"] == [] and none["subject"] is None
    assert gs.quick_direction(1, "open")["light"] == {"preset": "overcast"} and one["light"] is None
