"""The AI tools of a real, running Studio: `npm run test:generators -- --url http://<pc>:8188` (STUDIO_TEST_URL). Skipped
without a URL.
  - the six tools are there, and each one is ready on that PC: a tool that isn't fails with the reason and what to do
    (its model to download, its Python package to install…)
  - AI Lossless Cut and One-Click Master (ffmpeg only) run on files made here and uploaded, and what they make is right:
    frame-exact smart pieces, a master on its loudness target under its ceiling
  - with --render (STUDIO_TEST_RENDER=1), the four that use its GPU or a local model run too, on small files: AI Depth (a
    picture), AI Slow-Mo (a section), AI Stems and AI Subtitles (a clip's sound). Slower: models load.
What they make lands in that Studio's Gallery under Generators/ (the files are named studio-test-…)."""
import json
import os
import subprocess
import urllib.error
import urllib.request

import pytest

from test_generators_matrix import check_file, fetch, run

URL = (os.environ.get("STUDIO_TEST_URL") or "").rstrip("/")
RENDER = os.environ.get("STUDIO_TEST_RENDER") == "1"
pytestmark = [pytest.mark.generators, pytest.mark.live,
              pytest.mark.skipif(not URL, reason="a real Studio's URL: npm run test:generators -- --url http://<pc>:8188")]
G = "/studio/api/generators"
NAMES = {"depth": "AI Depth", "slowmo": "AI Slow-Mo", "stems": "AI Stems", "subtitles": "AI Subtitles", "cut": "AI Lossless Cut", "master": "One-Click Master"}


class Remote:
    """That Studio, with the two calls the checks make (as the sandbox's own)."""
    url = URL

    def get(self, path):
        with urllib.request.urlopen(URL + path, timeout=120) as r:
            return json.loads(r.read())

    def post(self, path, body):
        req = urllib.request.Request(URL + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


@pytest.fixture(scope="module")
def studio():
    return Remote()


@pytest.fixture(scope="module")
def tools(studio):
    return {t["id"]: t for t in studio.get(G + "/tools")["tools"]}


def ff(*args):
    import results
    subprocess.run([results.ffmpeg(), "-v", "error", "-y", *map(str, args)], check=True)


@pytest.fixture(scope="module")
def uploaded(tmp_path_factory):
    d = tmp_path_factory.mktemp("livegen")
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000", "-t", "3",
       "-c:v", "libx264", "-g", "12", "-keyint_min", "12", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", d / "studio-test-clip.mp4")
    ff("-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.05:duration=6:sample_rate=48000", "-f", "lavfi", "-i",
       "aevalsrc='0.9*sin(2*PI*55*t)*exp(-25*mod(t,0.5))':d=6:s=48000", "-filter_complex", "[0][1]amix=inputs=2", "-ac", "2", d / "studio-test-song.wav")
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360", "-frames:v", "1", d / "studio-test-frame.png")
    out = {}
    for kind, name in (("video", "studio-test-clip.mp4"), ("audio", "studio-test-song.wav"), ("image", "studio-test-frame.png")):
        req = urllib.request.Request(URL + G + "/inputs/import", data=(d / name).read_bytes(), method="POST",
                                     headers={"X-File-Name": name, "Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=300) as r:
            out[kind] = json.loads(r.read())["input"]
    return out


def test_the_six_tools_are_there(tools):
    assert set(tools) == set(NAMES), f"tools found: {sorted(tools)} (ComfyUI-Studio/ai_tools/ copied with the pack?)"


@pytest.mark.parametrize("tid", list(NAMES), ids=list(NAMES.values()))
def test_each_tool_is_ready_on_this_pc(tools, tid):
    t = tools.get(tid)
    assert t, f"{NAMES[tid]} isn't installed"
    assert not t.get("unavailable"), f"{t['name']} is not ready on that PC: {t['unavailable']}"


def ready(tools, tid):
    if not tools.get(tid) or tools[tid].get("unavailable"):
        pytest.skip(f"{NAMES[tid]} isn't ready (see its readiness check)")


def test_lossless_cut_makes_frame_exact_pieces(studio, tools, uploaded, tmp_path):
    ready(tools, "cut")
    job = run(studio, {"tool": "cut", "input_id": uploaded["video"]["id"], "segments": [[0.5, 1.5], [2.2, 2.9]], "options": {"mode": "smart", "join": True}}, 600)
    assert job["status"] == "done", job["error"]
    assert [p["start"] for p in job["pieces"]] == [0.5, 2.2] and len(job["outputs"]) == 3
    for n, o in enumerate(job["outputs"]):
        check_file(fetch(studio, job, n, tmp_path / o["name"]), o["kind"])


def test_one_click_master_lands_on_its_target(studio, tools, uploaded, tmp_path):
    ready(tools, "master")
    job = run(studio, {"tool": "master", "input_id": uploaded["audio"]["id"], "options": {"target": -14, "ceiling": -1}}, 600)
    assert job["status"] == "done", job["error"]
    assert abs(job["stats"]["integrated_lufs"] + 14) <= 0.6 and job["stats"]["true_peak_dbtp"] <= -0.9, job["stats"]
    check_file(fetch(studio, job, 0, tmp_path / job["outputs"][0]["name"]), "audio")


RENDERED = [("depth", "image", {"output": "depth"}, None), ("slowmo", "video", {"factor": 2}, (12, 24)),
            ("stems", "audio", {"stems": 2}, None), ("subtitles", "video", {"accuracy": "fast"}, None)]


@pytest.mark.skipif(not RENDER, reason="renders on that PC's GPU or a local model: add --render")
@pytest.mark.parametrize("tid,kind,options,section", RENDERED, ids=[NAMES[r[0]] for r in RENDERED])
def test_the_gpu_tools_render(studio, tools, uploaded, tmp_path, tid, kind, options, section):
    ready(tools, tid)
    body = {"tool": tid, "input_id": uploaded[kind]["id"], "options": options}
    if section:
        body.update(start=section[0], end=section[1])
    job = run(studio, body, 3600)
    assert job["status"] == "done", (job["stage"], job["error"])
    assert job["outputs"] or job.get("note"), "it made nothing"     # a test tone has no speech: Subtitles may say so
    for n, o in enumerate(job["outputs"]):
        check_file(fetch(studio, job, n, tmp_path / o["name"]), o["kind"])
