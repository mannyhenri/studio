"""The AI tools' host (ComfyUI-Studio/generators.py): Film Engine's Generators, moved into Studio.

Run against the fixture engines in tests/fixtures/generators (two stand-in engines that speak the real engines' command
line and progress file, and manifests that are broken or need something this machine hasn't got). The same checks as
Film Engine's backend/tests/generators.test.js, plus Studio's own:
  - tools come from the manifests; a broken one, or one missing its Python package, ComfyUI node or model, is listed as
    not ready with the reason and what to do
  - the command line is built only from declared options, their values checked against the manifest; a section goes only
    to a section tool, pieces only to a pieces tool, a setting only when its choice is made
  - a file is probed (frame rate, frames, size, sound); non-media, missing and out-of-bounds paths are refused
  - jobs run one at a time, report progress, stats, notes and outputs, fail with the engine's own message, and Cancel
    stops a running engine (and the ComfyUI job it waited for) and starts the next
  - the page never sees the command line or a path on this PC; a job still running when ComfyUI stopped says so"""
import asyncio
import importlib.util
import json
import os
import subprocess
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIX = os.path.join(REPO, "tests", "fixtures", "generators")
_spec = importlib.util.spec_from_file_location("studio_generators", os.path.join(REPO, "ComfyUI-Studio", "generators.py"))
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

import folder_paths  # noqa: E402  (the sandbox's stand-in: tests/conftest.py puts it on the path)
import results  # noqa: E402

pytestmark = pytest.mark.generators

FF = results.ffmpeg()


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "BUILTIN", FIX)
    monkeypatch.setattr(gen, "POLL", 0.05)
    monkeypatch.delenv("STUDIO_GENERATORS_DIR", raising=False)
    monkeypatch.delenv("FAKE_MODE", raising=False)
    dirs = {k: tmp_path / k for k in ("data", "input", "output", "temp", "cache")}
    for d in dirs.values():
        d.mkdir()
    gen.reset()
    gen.configure(**{k: str(v) for k, v in dirs.items()}, ffmpeg=FF)
    gen.CFG["comfy_url"] = None
    yield gen
    gen.reset()


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "output" / "Videos" / "clip.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000", "-t", "2", "-pix_fmt", "yuv420p", "-shortest", str(p)], check=True)
    return str(p)


def run(coro):
    return asyncio.run(coro)


async def until(pred, limit=20):
    t0 = time.time()
    while not pred():
        if time.time() - t0 > limit:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------- tools
def test_tools_come_from_their_manifests_and_a_broken_one_says_why(g):
    by = {t["id"]: t for t in g.list_tools()}
    assert {"broken", "Not JSON", "fake-slowmo", "fake-upscale", "fake-depth", "fake-cut"} <= set(by)
    assert [t["id"] for t in g.list_tools()][:4] == ["fake-slowmo", "fake-upscale", "fake-depth", "fake-cut"], "in their order"
    assert by["fake-cut"]["range"] == "segments" and by["fake-slowmo"]["range"] == "section" and by["fake-upscale"]["range"] == "whole"
    assert "missing.py is missing" in by["broken"]["unavailable"]
    assert "not valid JSON" in by["Not JSON"]["unavailable"], "a manifest that can't be read is listed under its folder's name"
    with pytest.raises(g.GenError, match="not ready"):
        g.get_tool("broken")
    with pytest.raises(g.GenError, match="no AI tool called nope"):
        g.get_tool("nope")
    assert set(g.public_tool(by["fake-slowmo"])) & {"dir", "script", "needs", "python"} == set(), "the page never sees where a tool lives"


def test_a_tool_missing_a_package_a_node_or_a_model_says_what_to_do(g, tmp_path, monkeypatch):
    folder_paths.configure(str(tmp_path / "comfy"))
    by = {t["id"]: t for t in g.list_tools()}
    assert by["needs-package"]["unavailable"] == ("needs no_such_package_here or nor_this_one in ComfyUI's Python: "
                                                 "pip install no-such-package")
    assert by["needs-node"]["unavailable"] == "this ComfyUI has no NoSuchStudioNode node: update ComfyUI"
    assert by["needs-model"]["unavailable"] == "needs a test depth model in models/geometry_estimation (for example da3_mono_large.safetensors)"
    # the model appears: the tool is ready and the model it will use is the one the manifest prefers
    models = tmp_path / "comfy" / "models" / "geometry_estimation"
    for name in ("da3_base.safetensors", "da3_mono_large.safetensors", "moge2.safetensors"):
        (models / name).write_bytes(b"")
    folder_paths.filename_list_cache.clear()
    t = g.get_tool("needs-model")
    assert g.find_model(t["needs"]["model"]) == "da3_mono_large.safetensors"
    # a package found on the engines' extra path (the sandbox's stand-ins) makes its tool ready
    pkg = tmp_path / "extra" / "nor_this_one"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.setenv("STUDIO_GENERATORS_PYTHONPATH", str(tmp_path / "extra"))
    assert not g.get_tool("needs-package").get("unavailable")


def test_more_tools_come_from_STUDIO_GENERATORS_DIR_and_studios_own_come_first(g, tmp_path, monkeypatch):
    extra = tmp_path / "film-engine-engines"
    (extra / "Other").mkdir(parents=True)
    (extra / "Other" / "go.py").write_text("")
    (extra / "Other" / "generator.json").write_text(json.dumps({"id": "other", "name": "Other", "script": "go.py", "accepts": "audio",
                                                                "range": "whole", "out": {"flag": "--out", "kind": "dir"}}))
    (extra / "Same").mkdir()
    (extra / "Same" / "generator.json").write_text(json.dumps({"id": "fake-upscale", "name": "Shadow", "script": "x.py"}))
    monkeypatch.setenv("STUDIO_GENERATORS_DIR", str(extra))
    ts = g.list_tools()
    assert ts[-1]["id"] == "other" and not ts[-1].get("unavailable")
    assert [t["name"] for t in ts if t["id"] == "fake-upscale"] == ["Fake Upscale"], "the same id twice: the first folder's is used"


# ---------------------------------------------------------------- the command line
def test_the_command_line_holds_only_declared_options_checked_against_the_manifest(g):
    slow, up, depth = g.get_tool("fake-slowmo"), g.get_tool("fake-upscale"), g.get_tool("fake-depth")
    a = g.build_args(slow, "/in.mov", "/o.mov", "/p.json", 10, 40, {"factor": 2, "scene": 9, "compare": True, "evil": "--rm"})
    assert a[1:] == ["/in.mov", "--start", "10", "--end", "40", "--factor", "2", "--scene", "0.6", "--compare", "--out", "/o.mov", "--progress", "/p.json"]
    b = g.build_args(slow, "/in.mov", "/o", "/p")
    assert b[2:6] == ["--factor", "4", "--scene", "0.3"], "the defaults when nothing is chosen"
    assert "--compare" not in b, "an unticked checkbox adds nothing"
    assert "--start" not in g.build_args(up, "/in.mov", "/o", "/p", 1, 9), "a whole-file tool never receives a section"
    with pytest.raises(g.GenError, match="not one of its choices"):
        g.build_args(slow, "/in.mov", "/o", "/p", options={"factor": 3})
    with pytest.raises(g.GenError, match="end after it starts"):
        g.build_args(slow, "/in.mov", "/o", "/p", 9, 9)
    with pytest.raises(g.GenError, match="must be a number"):
        g.build_args(slow, "/in.mov", "/o", "/p", options={"scene": "lots"})
    # a setting that applies to one choice only goes with that choice
    fog = g.build_args(depth, "/in", "/o", "/p", options={"effect": "fog", "blur": 2, "density": 1.5})
    assert "--density" in fog and "--blur" not in fog
    dof = g.build_args(depth, "/in", "/o", "/p", options={"blur": 2.5})
    assert dof[dof.index("--blur") + 1] == "2.5" and "--density" not in dof


def test_pieces_are_passed_in_seconds_and_checked(g):
    cut = g.get_tool("fake-cut")
    a = g.build_args(cut, "/in.mov", "/o", "/p", segments=[[12, 30.5], {"start": 41, "end": 55}], duration=60)
    assert a[2:4] == ["--segments", "12.000000-30.500000,41.000000-55.000000"]
    for segs, why in (([], "at least one piece"), ([[0, 1]] * 6, "at most 5 pieces"), ([[5, 5]], "piece 1 must end after it starts"),
                      ([[1, 2], [70, 80]], "piece 2 starts after the end"), ([["a", 2]], "needs a start and an end"),
                      ([[float("nan"), 2]], "needs a start and an end")):
        with pytest.raises(g.GenError, match=why):
            g.build_args(cut, "/in", "/o", "/p", segments=segs, duration=60)


# ---------------------------------------------------------------- files
def test_a_file_is_probed_and_non_media_missing_or_outside_files_are_refused(g, clip, tmp_path):
    rec = g.register(clip, {"filename": "clip.mp4", "subfolder": "Videos"})
    assert (rec["kind"], rec["fps"], rec["frames"], rec["width"], rec["height"], rec["has_audio"]) == ("video", 24.0, 48, 320, 180, True)
    assert rec["preview"] is False, "H.264 plays in the browser as it is"
    assert g.get_input(rec["id"])["path"] == clip
    assert "path" not in g.public_input(rec)
    assert g.gallery_file("clip.mp4", "Videos") == os.path.realpath(clip)
    txt = tmp_path / "notes.txt"
    txt.write_text("hi")
    with pytest.raises(g.GenError, match="not a video, audio or image file"):
        g.register(str(txt))
    with pytest.raises(g.GenError, match="there is no file"):
        g.register(str(tmp_path / "gone.mov"))
    with pytest.raises(g.GenError, match="full file path"):
        g.register("relative.mov")
    with pytest.raises(g.GenError, match="outside Studio's folders"):
        g.gallery_file("passwd", "../../../../etc")
    with pytest.raises(g.GenError, match="Incognito"):
        g.input_file("studio_private/face.png")
    assert g.clean_name("../../évasion: \"x\".mov") == "évasion_ _x_.mov"


def test_a_prores_file_gets_a_playable_copy_for_the_player_only(g, tmp_path):
    src = tmp_path / "output" / "master.mov"
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-t", "1", "-c:v", "prores_ks", str(src)], check=True)
    rec = g.register(str(src))
    assert rec["codec"] == "prores" and rec["preview"] is True
    copy = run(g.preview_file(str(src)))
    assert copy.endswith(".mp4") and os.path.dirname(copy) == os.path.join(g.CFG["cache"], "ai-tools")
    import av
    with av.open(copy) as c:
        assert c.streams.video[0].codec_context.name == "h264" and c.streams.video[0].frames == 24, "every frame kept"


def test_a_file_a_tool_cannot_take_is_refused_in_words(g):
    vid = {"name": "take.mp4", "kind": "video", "has_audio": True}
    mute = dict(vid, name="mute.mp4", has_audio=False)
    song = {"name": "song.wav", "kind": "audio", "has_audio": True}
    pic = {"name": "frame.png", "kind": "image", "has_audio": False}
    stems = {"name": "Stems", "accepts": "video", "audio_ok": True, "needs_sound": True}
    master = {"name": "Master", "accepts": "audio"}
    slow = {"name": "Slow-Mo", "accepts": "video"}
    depth = {"name": "Depth", "accepts": "video", "image_ok": True}
    ok = [(stems, vid), (stems, song), (master, song), (master, vid), (slow, vid), (slow, mute), (depth, pic), (depth, vid)]
    for tool, rec in ok:
        g._fits(tool, rec)
    for tool, rec, why in ((stems, mute, "mute.mp4 has no sound"), (master, mute, "has no sound"), (master, pic, "not a picture"),
                           (slow, song, "Slow-Mo needs a video"), (slow, pic, "not a picture"), (stems, pic, "not a picture")):
        with pytest.raises(g.GenError, match=why):
            g._fits(tool, rec)


# ---------------------------------------------------------------- jobs
def test_jobs_run_one_at_a_time_and_report_progress_stats_and_outputs(g, clip):
    async def go():
        rec = g.register(clip)
        j1 = g.create_job({"tool": "fake-slowmo", "input_id": rec["id"], "start": 12, "end": 36, "options": {"factor": 2}})
        j2 = g.create_job({"tool": "fake-upscale", "input_id": rec["id"]})
        assert g.get_job(j1["id"])["status"] == "running"
        assert g.get_job(j2["id"])["status"] == "queued", "a second job waits for the GPU"
        assert not any(k.startswith("_") for k in j1), "the page never sees the command line"
        await until(lambda: g.get_job(j2["id"])["status"] == "done")
        a, b = g.public_job(g.get_job(j1["id"])), g.public_job(g.get_job(j2["id"]))
        assert (a["status"], a["done"], a["total"]) == ("done", 4, 4)
        assert a["stats"] == {"integrated_lufs": -14.02, "true_peak_dbtp": -2.1}, "flat stats reach the job; nested ones don't"
        assert a["note"] == "a note from the engine"
        assert len(a["outputs"]) == 1 and a["outputs"][0]["subfolder"] == "Generators/Fake SlowMo"
        assert a["outputs"][0]["name"].startswith("clip - Fake SlowMo f12-36 ") and a["outputs"][0]["kind"] == "text"
        ran = json.load(open(g.output_path(j1["id"], 0)))
        assert ran["input"] == clip and ran["args"][1:7] == ["--start", "12", "--end", "36", "--factor", "2"]
        assert ran["env"]["STUDIO_JOB_LABEL"] == "Fake SlowMo · clip.mp4" and "STUDIO_FFMPEG" in ran["env"]
        assert json.loads(ran["env"]["STUDIO_COMFY_DIRS"])["output"] == g.CFG["output"]
        assert b["started_at"] >= a["finished_at"], "the queued job started only after the first finished"
        with pytest.raises(g.GenError, match="not there"):
            g.output_path(j1["id"], 5)
        assert not os.path.exists(g.work_dir(g.get_job(j1["id"]))), "the engine's work folder goes when it ends"
    run(go())


def test_two_runs_in_the_same_second_never_share_a_file(g, clip, monkeypatch):
    monkeypatch.setattr(g, "_stamp", lambda: "20260101-120000")

    async def go():
        rec = g.register(clip)
        ids = [g.create_job({"tool": "fake-upscale", "input_id": rec["id"]})["id"] for _ in range(3)]
        await until(lambda: all(g.get_job(i)["status"] == "done" for i in ids))
        names = [g.public_job(g.get_job(i))["outputs"][0]["name"] for i in ids]
        assert names == ["clip - Fake Upscale 20260101-120000.json", "clip - Fake Upscale 20260101-120000 (2).json",
                         "clip - Fake Upscale 20260101-120000 (3).json"]
    run(go())


def test_a_picture_in_gives_a_picture_out_when_the_tool_says_so(g, tmp_path):
    async def go():
        pic = tmp_path / "input" / "frame.png"
        subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x36", "-frames:v", "1", str(pic)], check=True)
        rec = g.register(str(pic))
        j = g.create_job({"tool": "fake-depth", "input_id": rec["id"]})
        await until(lambda: g.get_job(j["id"])["status"] == "done")
        assert g.public_job(g.get_job(j["id"]))["outputs"][0]["name"].endswith(".png.json")
        with pytest.raises(g.GenError, match="needs a video, not a picture"):
            g.create_job({"tool": "fake-slowmo", "input_id": rec["id"]})
    run(go())


def test_a_failing_engine_reports_its_own_message(g, clip, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")

    async def go():
        j = g.create_job({"tool": "fake-upscale", "input_id": g.register(clip)["id"]})
        await until(lambda: g.get_job(j["id"])["status"] != "running")
        assert g.get_job(j["id"])["status"] == "failed"
        assert "told to fail" in g.get_job(j["id"])["error"]
        assert os.path.isfile(g.get_job(j["id"])["_log"]), "a failed engine's log stays"
    run(go())


def test_cancel_stops_the_engine_and_its_comfyui_job_and_starts_the_next(g, clip, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "slow")
    stopped = []

    async def fake_stop(pid):
        stopped.append(pid)
    monkeypatch.setattr(g, "_stop_comfy", fake_stop)

    async def go():
        rec = g.register(clip)
        j1 = g.create_job({"tool": "fake-upscale", "input_id": rec["id"]})
        j2 = g.create_job({"tool": "fake-slowmo", "input_id": rec["id"]})
        await until(lambda: g.get_job(j1["id"])["done"] >= 1)
        proc = g._procs[j1["id"]]
        c = await g.cancel_job(j1["id"])
        assert c["status"] == "cancelled"
        assert stopped == ["fake-prompt"], "the ComfyUI job the engine waited for is stopped too"
        await until(lambda: proc.poll() is not None, 10)
        await until(lambda: g.get_job(j2["id"])["status"] == "running", 10)
        monkeypatch.setenv("FAKE_MODE", "")
        q = await g.cancel_job(j2["id"])
        assert q["status"] == "cancelled"
        j3 = g.create_job({"tool": "fake-upscale", "input_id": rec["id"]})
        assert (await g.cancel_job(j3["id"]))["status"] in ("cancelled", "done")
        await asyncio.sleep(0.3)
    run(go())


def test_pieces_are_reported_cleaned(g, clip):
    async def go():
        j = g.create_job({"tool": "fake-cut", "input_id": g.register(clip)["id"], "segments": [[0.6, 1.2], [1.5, 1.9]]})
        await until(lambda: g.get_job(j["id"])["status"] == "done")
        pub = g.public_job(g.get_job(j["id"]))
        assert [o["name"] for o in pub["outputs"]] == ["piece 01.json", "piece 02.json"]
        assert pub["segments"] == [[0.6, 1.2], [1.5, 1.9]] and pub["note"] == "2 pieces"
        pc = pub["pieces"][0]
        assert pc["n"] == 1 and pc["file"] == "piece 01.json", "a piece names its file, not where it is"
        assert pc["start"] == pytest.approx(0.1) and pc["asked_start"] == 0.6
        assert pc["reencoded"] == [[0.6, 0.85]] and len(pc["note"]) == 300 and "secret" not in pc
    run(go())


def test_a_job_still_running_when_comfyui_stopped_says_so_after_a_restart(g, clip, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "slow")

    async def go():
        j = g.create_job({"tool": "fake-upscale", "input_id": g.register(clip)["id"]})
        await until(lambda: g.get_job(j["id"])["status"] == "running")
        proc = g._procs[j["id"]]
        g.reset()                       # ComfyUI stops; its state file stays
        g._kill(proc)
        g.load()
        back = g.get_job(j["id"])
        assert back["status"] == "interrupted" and "run it again" in back["error"]
    run(go())
