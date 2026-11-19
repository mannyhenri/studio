"""Every one of the MCP server's tools, called at least once through the real connector (studio_mcp.py over stdio), on a
sandbox of its own, each answer checked; the last check fails if a tool the server lists was never called here, so a tool
added later can't go untested. In the order an assistant would use them: reading Studio, making pictures and videos with
its apps, sound, storyboards, a project from an idea, a project from a screenplay (story to film), the gallery and the
local AI, the AI tools, Previz."""
import os
import re

import pytest

from conftest import REPO, Sandbox
from test_mcp import STORY, Connector

pytestmark = pytest.mark.mcp
CALLED = set()


class Recording(Connector):
    def call(self, _name, ok=True, **args):
        CALLED.add(_name)
        return super().call(_name, ok, **args)


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    s = Sandbox(tmp_path_factory.mktemp("mcptools"), "demo")
    yield s
    s.stop()


@pytest.fixture(scope="module")
def mcp(box):
    c = Recording(box)
    yield c
    c.close()


@pytest.fixture(scope="module")
def state():
    return {}


def inp(box, name):
    return os.path.join(box.run, "input", name)


def test_reading_studio(mcp, box):
    ov, _ = mcp.call("studio_overview")
    assert "How Studio works" in ov and '"apps"' in ov
    apps, _ = mcp.call("studio_list_apps")
    assert {"Image", "Video", "Edit", "Upscale"} <= {a["app"] for a in apps}
    image, _ = mcp.call("studio_list_apps", app="Image")
    assert [a["app"] for a in image] == ["Image"] and any(i["label"] == "Model" for i in image[0]["inputs"])
    st, _ = mcp.call("studio_status")
    assert st["machines"][0]["name"] == "local" and st["rendering"] == []
    q, _ = mcp.call("studio_queue", action="list")
    assert q["running"] == [] and q["waiting"] == []
    models, _ = mcp.call("studio_models")
    assert set(models) == {"loras", "model_files"}
    poses, _ = mcp.call("studio_poses")
    assert "packs" in poses
    recent, _ = mcp.call("studio_recent", kind="video", limit=5)
    assert {r["filename"] for r in recent} == {"diner-wide.mp4", "diner-ray.mp4", "diner-counter.mp4"} and all(os.path.isfile(r["path"]) for r in recent)
    for tool in ("studio_list_characters", "studio_list_boards", "studio_projects", "studio_list_previz_sets"):
        data, _ = mcp.call(tool)
        assert data == [], tool


def test_making_pictures_and_videos_with_the_apps(mcp, box, state):
    made, images = mcp.call("studio_generate", app="Image", prompt="a red bicycle against a blue wall", model="Z-Image", values={"Aspect": "1:1"}, wait=True)
    assert "Queued 1 on Image" in made and images and "done" in made
    queued, _ = mcp.call("studio_generate", app="Image", prompt="a green door", count=2, wait=False)
    pids = re.findall(r"'([0-9a-f-]{36})'", queued)
    assert len(pids) == 2
    done, _ = mcp.call("studio_wait", prompt_ids=pids, timeout=120)
    assert done.count('"status": "done"') == 2
    later, _ = mcp.call("studio_generate", app="Image", prompt="a yellow kite", later="in 5h")
    assert later.startswith("Scheduled")
    shot, _ = mcp.call("studio_keyframe_shot", keys=[{"image": inp(box, "portrait.png"), "at": 0}, {"image": inp(box, "chair.png"), "at": 3}],
                       prompt="the woman walks to the chair and sits", seconds=5)
    assert shot["subfolder"].startswith("Videos") and os.path.isfile(shot["path"])
    fixed, _ = mcp.call("studio_fix_video", filename="diner-ray.mp4", subfolder="Videos", start=1.0, end=2.0, mode="picture", prompt="Ray lifts his cup")
    assert fixed["subfolder"] == "Videos/Fixed" and os.path.isfile(fixed["path"])
    longer, _ = mcp.call("studio_extend_video", filename="diner-wide.mp4", subfolder="Videos", prompt="June stands and walks out", engine="ltx", seconds=5)
    assert longer["subfolder"] == "Videos/Extended" and os.path.isfile(longer["path"])
    topaz, _ = mcp.call("studio_upscale_video", ok=False, filename="diner-wide.mp4", subfolder="Videos", size="1080")
    assert "Topaz" in topaz or "not installed" in topaz.lower(), topaz
    state["picture"] = next(p for p in re.findall(r'"path": "([^"]+\.png)"', made))


def test_sound(mcp):
    song, _ = mcp.call("studio_music", style="calm piano, soft strings", seconds=20, name="Test song")
    assert song["subfolder"] == "Audio/Music" and os.path.isfile(song["path"])
    voice, _ = mcp.call("studio_voice", text="The boats are still out.", name="Test line")
    assert voice["subfolder"] == "Audio/Voices" and os.path.isfile(voice["path"])


def test_storyboards(mcp, state):
    made, _ = mcp.call("studio_create_board", name="Test board", shots=[{"prompt": "a harbour at dawn", "camera": "static"}, {"prompt": "gulls"}])
    assert "Board 'Test board'" in made and "2 shots" in made
    r, _ = mcp.call("studio_attach", board="Test board", shot="1", filename="diner-wide.mp4", subfolder="Videos")
    assert r == {"ok": True, "filename": "diner-wide.mp4", "subfolder": "Videos", "takes": 1, "pick": 0}
    r, _ = mcp.call("studio_request_final", board="Test board", shot="1", engine="seedance", notes="golden light")
    assert r["final_request"]["engine"] == "seedance"
    waiting, _ = mcp.call("studio_final_requests")
    assert [(w["board_name"], w["shot_number"], w["engine"], w["notes"], os.path.basename(w["approved_take"])) for w in waiting] == \
        [("Test board", 1, "seedance", "golden light", "diner-wide.mp4")]
    boards, _ = mcp.call("studio_list_boards", board="Test board")
    assert boards[0]["shots"][0]["picked"] and boards[0]["shots"][0]["final_request"]["status"] == "waiting"


def test_a_project_from_an_idea(mcp):
    pr, _ = mcp.call("studio_project_create", idea="A lighthouse keeper and her grandson save the fishing boats in a storm", shots=3, title="Storm")
    assert pr["title"] == "Storm" and len(pr["shots"]) == 3
    up, _ = mcp.call("studio_project_update", id=pr["id"], logline="They keep the light burning.")
    assert up["logline"] == "They keep the light burning."
    kf, images = mcp.call("studio_project_keyframes", id=pr["id"], wait=True)
    assert all(s["keyframe"] for s in kf) and images
    board, _ = mcp.call("studio_project_to_board", id=pr["id"])
    assert board["shots"] == 3
    one, _ = mcp.call("studio_projects", id=pr["id"])
    assert one["board"] == board["board"]


def test_a_project_from_a_screenplay_to_a_film(mcp, state, tmp_path):
    made, _ = mcp.call("studio_project_new", title="Keeper", screenplay=STORY.replace("The Last Keeper", "Keeper"), tone="Cinematic drama")
    pid = made["project"]["id"]
    text, _ = mcp.call("studio_screenplay_get", project="Keeper")
    assert text.startswith("Title: Keeper")
    out, _ = mcp.call("studio_screenplay_set", project=pid, text=STORY.replace("The Last Keeper", "Keeper").replace("Not tonight.", "Not tonight, Teo."))
    assert out["project"]["shots"] == 3
    mcp.call("studio_screenplay_export", project=pid, format="fdx", path=str(tmp_path))
    assert (tmp_path / "Keeper.fdx").read_text(encoding="utf-8").lstrip().startswith("<?xml")
    bd, _ = mcp.call("studio_breakdown", project=pid)
    assert [s["scene"] for s in bd] == ["1", "2", "3"]
    mcp.call("studio_shot_update", project=pid, shot="1A", camera="push in", seconds=6)
    cast, _ = mcp.call("studio_cast", project=pid)
    assert [c["part"] for c in cast] == ["MARA", "TEO"]
    mcp.call("studio_character_save", name="Old Jonah", age="70", look="a retired fisherman")
    r, _ = mcp.call("studio_cast_role", project=pid, part="TEO", character="Old Jonah")
    assert {x["role"]: x["character"] for x in r["project"]["roles"]}["TEO"] == "Old Jonah"
    mcp.call("studio_character_save", character="Mara Voss", hair="silver braid", face=state["picture"])
    _, images = mcp.call("studio_character_picture", character="Old Jonah", make_face=True)
    assert images
    locs, _ = mcp.call("studio_locations", project=pid)
    assert [x["name"] for x in locs] == ["Northpoint Lighthouse", "Lamp Room"]
    mcp.call("studio_location_update", project=pid, location="Northpoint Lighthouse", look="a white Victorian tower on black rocks")
    _, images = mcp.call("studio_location_picture", project=pid, location="Northpoint Lighthouse")
    assert images
    frames, _ = mcp.call("studio_storyboard_frames", project=pid)
    assert frames["made"] == 3
    queued, _ = mcp.call("studio_takes_draft", project=pid, count=1, wait=False)
    assert len(queued["queued"]) == 3
    takes, _ = mcp.call("studio_takes_draft", project=pid, shots=["2A"], count=1, wait=True, timeout=120)
    assert len(next(s for s in takes["shots"] if s["code"] == "2A")["takes"]) == 2
    takes, _ = mcp.call("studio_takes", project=pid)
    assert all(t["status"] == "done" for s in takes["shots"] for t in s["takes"])
    picked, _ = mcp.call("studio_take_pick", project=pid, shot="2A", take=1)
    assert next(s for s in picked["shots"] if s["code"] == "2A")["pick"] == 1
    msg, _ = mcp.call("studio_project_music", project=pid, style="solo cello", wait=False)
    jid = re.search(r"job (\w+)", msg).group(1)
    job, _ = mcp.call("studio_job", id=jid, wait=True, timeout=120)
    assert job["status"] == "done" and job["result"]["subfolder"] == "Audio/Music"
    voices, _ = mcp.call("studio_project_voices", project=pid)
    assert voices["lines"] == 3
    film, _ = mcp.call("studio_project_film", project=pid, music="project", title="Keeper", crossfade=0)
    assert os.path.isfile(film["path"]) and film["shots"] == 3
    state["film"] = film
    gone, _ = mcp.call("studio_character_delete", character="Old Jonah")
    assert gone == {"ok": True}
    mcp.call("studio_project_delete", project=pid)


def test_the_gallery_and_the_local_ai(mcp, box, state):
    _, images = mcp.call("studio_view", filename=state["film"]["filename"], subfolder=state["film"]["subfolder"])
    assert images[0]["mimeType"] == "image/webp"
    info, _ = mcp.call("studio_file_info", filename="diner.jpg", subfolder="Images")
    assert info["size"] == [1024, 572]
    fav, _ = mcp.call("studio_gallery_action", filename="diner.jpg", subfolder="Images", action="favorite")
    assert fav == {"fav": True}
    trash, _ = mcp.call("studio_gallery_action", filename=state["film"]["filename"], subfolder=state["film"]["subfolder"], action="trash")
    assert trash == {"ok": True} and not os.path.exists(state["film"]["path"])
    review, _ = mcp.call("studio_ai_review", filename="diner-ray.mp4", subfolder="Videos", prompt="Ray talks to June")
    assert review["review"]["verdict"] == "keep"
    pr, _ = mcp.call("studio_ai_prompt", idea="a harbour at dawn", kind="image")
    assert pr["prompt"]
    w, _ = mcp.call("studio_ai_write", mode="prose", text="June waits at the counter.")
    assert w["text"].startswith("INT.")
    reel, _ = mcp.call("studio_reel", song="song.wav", items=[{"filename": "diner.jpg", "subfolder": "Images"}, {"filename": "diner-ray.mp4", "subfolder": "Videos"}], length=6)
    assert reel["subfolder"] == "Videos/Reels" and os.path.isfile(reel["path"])


def test_the_ai_tools_and_previz(mcp, box, tmp_path):
    tools, _ = mcp.call("studio_ai_tools")
    assert len(tools) == 6 and not [t for t in tools if t["not_ready"]]
    started, _ = mcp.call("studio_ai_tool_run", tool="cut", filename="diner-ray.mp4", subfolder="Videos", pieces=[[0.5, 1.5]], wait=False)
    job, _ = mcp.call("studio_ai_tool_jobs", id=started["job"])
    assert job["tool_name"] == "AI Lossless Cut"
    jobs, _ = mcp.call("studio_ai_tool_jobs")
    assert jobs and jobs[0]["id"] == started["job"]
    import sys
    sys.path.insert(0, os.path.join(REPO, "tests"))
    import glbfile
    glb = tmp_path / "set.glb"
    glb.write_bytes(glbfile.studio_set())
    s, _ = mcp.call("studio_previz_add", path=str(glb), name="Test set")
    assert s["name"] == "Test set" and "Cam_Front" in s["cameras"]
    sets, _ = mcp.call("studio_list_previz_sets")
    assert [x["name"] for x in sets] == ["Test set"]


def test_no_tool_is_left_untested(mcp):
    listed = {t["name"] for t in mcp.rpc("tools/list")["result"]["tools"]}
    assert not listed - CALLED, f"tools the MCP server lists that no check here calls: {sorted(listed - CALLED)}"
