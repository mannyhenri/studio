"""Every AI tool with every setting, end to end through Studio's HTTP API, on a sandbox of its own: each tool with its
defaults on each kind of file it takes, then each choice of each of its options (a slider at both ends, a checkbox the other
way; a setting that applies only with another choice is tried with that choice). The cases come from the tools' own
generator.json, so a new tool or option is tested the day it's added. Every run must finish, and every file it makes is
fetched back and checked for what it is: a video that decodes, a sound with a length, a picture that opens, subtitles that
parse. And where a setting has a visible effect, that effect: the loudness and true peak of a master, the frame rate of
smoother motion, the width of a side by side, the frames a cut starts on, how many stems, the line length of subtitles,
the interpolation factor and the depth model's resolution in the job ComfyUI got."""
import glob
import json
import os
import re
import subprocess
import time
import urllib.request

import pytest

from conftest import REPO, Sandbox

pytestmark = pytest.mark.generators
TOOLS = os.path.join(REPO, "ComfyUI-Studio", "ai_tools")
G = "/studio/api/generators"


def manifests():
    out = []
    for p in sorted(glob.glob(os.path.join(TOOLS, "*", "generator.json"))):
        with open(p, encoding="utf-8") as f:
            out.append(json.load(f))
    return sorted(out, key=lambda m: m["order"])


def kinds(m):
    """The kinds of file a tool takes: 'video', 'audio', 'image'."""
    if m["accepts"] == "audio":
        return ["audio", "video"]                         # a sound, or a video's sound
    return [m["accepts"]] + (["audio"] if m.get("audio_ok") else []) + (["image"] if m.get("image_ok") else [])


def cases():
    """(id, tool, file kind, options) for the defaults and for each setting."""
    out = []
    for m in manifests():
        for k in kinds(m):
            out.append((f"{m['id']}-defaults-on-{k}", m, k, {}))
        opts = m.get("options") or []
        for o in opts:
            needs = {k: vs[0] for k, vs in (o.get("when") or {}).items()}     # the choice that makes this setting apply
            if o["type"] == "select":
                values = [c["value"] for c in o["choices"] if c["value"] != o["default"]]
            elif o["type"] == "checkbox":
                values = [not o["default"]]
            elif o["type"] == "slider":
                values = [o["min"], o["max"]]
            else:
                continue
            for v in values:
                out.append((f"{m['id']}-{o['name']}={v}", m, kinds(m)[0], dict(needs, **{o["name"]: v})))
        if m["range"] == "section":
            out.append((f"{m['id']}-a-section", m, "video", {"__section": True}))
    return out


CASES = cases()


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    s = Sandbox(tmp_path_factory.mktemp("genmatrix"), "empty")
    yield s
    s.stop()


def ff(*args):
    import results
    subprocess.run([results.ffmpeg(), "-v", "error", "-y", *map(str, args)], check=True)


@pytest.fixture(scope="module")
def inputs(box, tmp_path_factory):
    """Small files of each kind, uploaded as a user would: a clip with sound (3 s, a keyframe every half second), a song
    (6 s, quiet then loud, with a kick drum), a picture."""
    d = tmp_path_factory.mktemp("genmedia")
    ff("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000", "-t", "3",
       "-c:v", "libx264", "-g", "12", "-keyint_min", "12", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", d / "clip.mp4")
    # a kick on every half second over pink noise and a tone: peaks well above the body of the mix, so a master's limiter
    # has work to do and its ceiling shows
    ff("-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.05:duration=6:sample_rate=48000", "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
       "-f", "lavfi", "-i", "aevalsrc='0.9*sin(2*PI*55*t)*exp(-25*mod(t,0.5))':d=6:s=48000",
       "-filter_complex", "[0][1][2]amix=inputs=3,volume='if(lt(t,3),0.3,0.9)':eval=frame", "-ac", "2", d / "song.wav")
    ff("-f", "lavfi", "-i", "testsrc2=size=320x180", "-frames:v", "1", d / "frame.png")
    out = {}
    for kind, name in (("video", "clip.mp4"), ("audio", "song.wav"), ("image", "frame.png")):
        req = urllib.request.Request(box.url + G + "/inputs/import", data=(d / name).read_bytes(), method="POST",
                                     headers={"X-File-Name": name, "Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=60) as r:
            out[kind] = json.loads(r.read())["input"]
    assert out["video"]["frames"] == 72 and out["video"]["has_audio"] and out["audio"]["duration"] == 6.0
    return out


def run(box, body, limit=120):
    code, r = box.post(G + "/jobs", body)
    assert code == 202, r
    t0 = time.time()
    while True:
        j = box.get(f"{G}/jobs/{r['job']['id']}")["job"]
        if j["status"] not in ("running", "queued"):
            return j
        assert time.time() - t0 < limit, f"still {j['status']}: {j['stage']}"
        time.sleep(0.2)


def fetch(box, job, n, dest):
    with urllib.request.urlopen(f"{box.url}{G}/jobs/{job['id']}/output/{n}", timeout=60) as r:
        dest.write_bytes(r.read())
    return dest


def check_file(path, kind):
    """That a file is what it says: (what was measured)."""
    if kind == "video":
        import av
        with av.open(str(path)) as c:
            v = c.streams.video[0]
            frames = sum(1 for _ in c.decode(v))
            assert frames > 0, path
            return {"frames": frames, "fps": float(v.average_rate or 0), "width": v.codec_context.width}
    if kind == "audio":
        import av
        with av.open(str(path)) as c:
            dur = float(c.duration or 0) / 1e6
        assert dur > 0.1, path
        return {"seconds": dur}
    if kind == "image":
        from PIL import Image
        with Image.open(path) as im:
            im.load()
            return {"size": im.size}
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".srt":
        assert re.match(r"1\n\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}\n", text), text[:200]
        return {"lines": [ln for ln in text.splitlines() if ln and not ln[0].isdigit()]}
    if path.suffix == ".vtt":
        assert text.startswith("WEBVTT\n"), text[:50]
    assert text.strip(), path
    return {}


def last_comfy_job(box, node):
    for f in sorted(glob.glob(os.path.join(box.jobs, "*.json")), key=os.path.getmtime, reverse=True):
        with open(f, encoding="utf-8") as fh:
            j = json.load(fh)
        hit = [n for n in j.get("prompt", {}).values() if n.get("class_type") == node]
        if hit:
            return hit[0]["inputs"]
    return None


@pytest.mark.parametrize("tool,kind,options", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_every_tool_with_every_setting(box, inputs, tmp_path, tool, kind, options):
    opts = dict(options)
    body = {"tool": tool["id"], "input_id": inputs[kind]["id"], "options": opts}
    if opts.pop("__section", False):
        body.update(start=12, end=36)                      # frames 12-35
    if tool["range"] == "segments":                        # the clip has a keyframe every half second
        body["segments"] = [[0.5, 1.5], [2.2, 2.9]] if kind == "video" else [[0.5, 1.5], [3.2, 5.0]]
    job = run(box, body)
    assert job["status"] == "done" and not job["error"], (job["stage"], job["error"])
    assert job["outputs"], "it made nothing"
    measured = []
    for n, o in enumerate(job["outputs"]):
        assert o["subfolder"].startswith(f"Generators/{tool['name']}")
        measured.append(check_file(fetch(box, job, n, tmp_path / o["name"]), o["kind"]))
    want = {o["name"]: o["default"] for o in tool.get("options") or []}
    want.update(opts)
    # what each setting should have done
    if tool["id"] == "master":
        assert abs(job["stats"]["integrated_lufs"] - float(want["target"])) <= 0.6, job["stats"]
        assert job["stats"]["true_peak_dbtp"] <= float(want["ceiling"]) + 0.1, job["stats"]
        if kind == "audio" and float(want["target"]) >= -16:
            assert job["stats"]["limiter"] == "on" and job["stats"]["true_peak_dbtp"] >= float(want["ceiling"]) - 1.5, \
                ("made this loud, the song's kicks need the limiter: the peaks sit just under the ceiling", job["stats"])
        elif kind == "audio":
            assert job["stats"]["limiter"] == "off", ("at a broadcast level nothing needs limiting: the dynamics stay", job["stats"])
        assert job["outputs"][0]["kind"] == ("audio" if kind == "audio" else "video")
    if tool["id"] == "slowmo":
        assert last_comfy_job(box, "FrameInterpolate")["multiplier"] == int(want["factor"])
        assert job["note"].count("of frames 12–35" if body.get("start") == 12 else "of the whole clip") == 1, job["note"]
        if want["hfr"]:
            assert abs(measured[0]["fps"] - 24 * int(want["factor"])) < 0.5, measured[0]
        if want["compare"]:
            assert measured[0]["width"] == 640, "the original beside the result"
    if tool["id"] == "depth":
        assert last_comfy_job(box, "DA3Inference")["resolution"] == int(want["detail"])
        assert job["outputs"][0]["kind"] == ("image" if kind == "image" else "video")
    if tool["id"] == "stems":
        assert len(job["outputs"]) == int(want["stems"]) and all(o["name"].endswith("." + want["format"]) for o in job["outputs"])
    if tool["id"] == "subtitles":
        assert [os.path.splitext(o["name"])[1] for o in job["outputs"]] == [".srt", ".vtt", ".txt"]
        assert all(len(line) <= int(want["chars"]) for line in measured[0]["lines"]), measured[0]["lines"]
        if want["language"] != "auto":
            assert job["stats"]["language"].startswith("en (translated from") if want["translate"] else job["stats"]["language"] == want["language"]
    if tool["id"] == "cut":
        pieces = job["pieces"]
        assert len(pieces) == 2 and len(job["outputs"]) == 2 + (1 if want["join"] else 0)
        assert pieces[0]["start"] == 0.5, "an In on a keyframe (or in a sound) is exact either way"
        if kind == "video":
            assert pieces[1]["start"] == (2.2 if want["mode"] == "smart" else 2.0), "a keyframe cut starts on the keyframe before the In"
        else:
            assert pieces[1]["start"] == 3.2


def test_every_case_comes_from_the_tools_manifests():
    ids = [c[0] for c in CASES]
    assert len(ids) == len(set(ids))
    for m in manifests():
        assert f"{m['id']}-defaults-on-{kinds(m)[0]}" in ids
        for o in m.get("options") or []:
            assert any(i.startswith(f"{m['id']}-{o['name']}=") for i in ids), (m["id"], o["name"])
