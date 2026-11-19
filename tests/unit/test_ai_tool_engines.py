"""Studio's six AI tools (ComfyUI-Studio/ai_tools/*: AI Depth, AI Slow-Mo, AI Stems, AI Subtitles, AI Lossless Cut,
One-Click Master): their manifests, the logic they share (studio_engine.py), and the engines that need no ComfyUI run
for real on media made here:
  - Lossless Cut: a keyframe cut starts on the keyframe before the In and says so; a smart cut starts exactly on the In
    (its first frame is the source's frame there), with its sound in sync; a sound file cuts anywhere
  - One-Click Master: the loudness lands on the target, the true peak under the ceiling, measured again afterwards
  - AI Depth's effects: depth of field blurs what's far from the focus, fog thickens with distance; a depth map drawn
    near-dark is recognised
  - AI Stems and AI Subtitles with the sandbox's stand-ins for Demucs and Whisper: their files and their subtitles
The two tools that render on the GPU (Slow-Mo, Depth) through ComfyUI are run end to end in tests/sandbox/test_ai_tools.py."""
import importlib.util
import json
import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS = os.path.join(REPO, "ComfyUI-Studio", "ai_tools")
STUBS = os.path.join(REPO, "sandbox", "engine_stubs")
sys.path.insert(0, TOOLS)
import studio_engine as se  # noqa: E402
import results  # noqa: E402

pytestmark = pytest.mark.generators

FF = results.ffmpeg()
ORDER = ["AI Depth", "AI Slow-Mo", "AI Stems", "AI Subtitles", "AI Lossless Cut", "One-Click Master"]


def manifests():
    out = {}
    for name in os.listdir(TOOLS):
        p = os.path.join(TOOLS, name, "generator.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                out[name] = json.load(f)
    return out


def engine(folder):
    spec = importlib.util.spec_from_file_location("engine_" + re.sub(r"\W", "_", folder), os.path.join(TOOLS, folder, "engine.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_engine(folder, args, tmp_path, env=None):
    safe = re.sub(r"\W", "_", folder)
    prog = str(tmp_path / f"progress-{safe}.json")
    e = dict(os.environ, STUDIO_FFMPEG=FF, **(env or {}))
    r = subprocess.run([sys.executable, os.path.join(TOOLS, folder, "engine.py")] + [str(a) for a in args] + ["--progress", prog],
                       capture_output=True, text=True, env=e, timeout=300)
    with open(prog, encoding="utf-8") as f:
        p = json.load(f)
    assert r.returncode == 0 and p["stage"] == "done", (p, r.stdout[-2000:], r.stderr[-2000:])
    return p


def ff(*args):
    subprocess.run([FF, "-v", "error", "-y", *map(str, args)], check=True)


def frames(path):
    import av
    with av.open(str(path)) as c:
        return sum(1 for _ in c.decode(video=0))


# ---------------------------------------------------------------- the manifests
def test_the_six_tools_are_there_in_order_and_their_manifests_hold_together(tmp_path):
    import importlib.util as iu
    spec = iu.spec_from_file_location("studio_generators_m", os.path.join(REPO, "ComfyUI-Studio", "generators.py"))
    host = iu.module_from_spec(spec)
    spec.loader.exec_module(host)
    ms = manifests()
    assert sorted(ms, key=lambda n: ms[n]["order"]) == ORDER
    assert [ms[n]["name"] for n in ORDER] == ORDER, "each folder is named after its tool"
    assert len({m["id"] for m in ms.values()}) == 6
    with open(os.path.join(REPO, "sandbox", "object_info.json"), encoding="utf-8") as f:
        nodes = set(json.load(f))
    with open(os.path.join(REPO, "sandbox", "models.json"), encoding="utf-8") as f:
        models = json.load(f)
    for name, m in ms.items():
        assert host.validate_manifest(m, os.path.join(TOOLS, name)) is None, name
        assert m.get("icon") and m.get("short") and len(m["short"]) <= 48 and m.get("description"), name
        names = {o["name"] for o in m.get("options") or []}
        for o in m.get("options") or []:
            if o["type"] == "select":
                assert o["default"] in [c["value"] for c in o["choices"]], (name, o["name"])
            if o["type"] == "slider":
                assert o["min"] <= o["default"] <= o["max"], (name, o["name"])
            for k in (o.get("when") or {}):
                assert k in names, (name, o["name"], k)
        needs = m.get("needs") or {}
        assert needs.get("ffmpeg"), name
        assert set(needs.get("comfy_nodes") or []) <= nodes, (name, "a node ComfyUI 0.45 hasn't got")
        if needs.get("model"):
            assert needs["model"]["default"] in models[needs["model"]["folder"]], (name, "the sandbox has its model")
        # the command line each engine gets from its defaults, and from every choice of every setting, is one its parser
        # accepts: with a file that isn't there it fails on the file (3), never on its arguments (2)
        t = dict(m, dir=os.path.join(TOOLS, name))
        tries = [{}] + [{o["name"]: c["value"]} for o in m.get("options") or [] if o["type"] == "select" for c in o["choices"]]
        tries += [{o["name"]: True} for o in m.get("options") or [] if o["type"] == "checkbox"]
        for opts in tries:
            args = host.build_args(t, "/nowhere/in.mp4", "/nowhere/out", str(tmp_path / "p.json"), options=opts,
                                   segments=[[0, 1]] if m["range"] == "segments" else None)
            r = subprocess.run([sys.executable] + args, capture_output=True, text=True, env=dict(os.environ, STUDIO_FFMPEG=FF))
            assert r.returncode == 3, (name, opts, r.stderr[-400:])


# ---------------------------------------------------------------- what the engines share
def test_frames_split_at_cuts_and_into_parts_that_share_their_edge():
    assert se.runs_between(10, [4, 0, 12, 4]) == [(0, 4), (4, 10)]
    assert se.chunks(0, 10, 4) == [(0, 4), (3, 7), (6, 10)]
    assert se.chunks(5, 7, 120) == [(5, 7)]
    slow = engine("AI Slow-Mo")
    plan = slow.plan(300, [100, 101], size=120)
    assert [j[1] for j in plan] == [(0, 100), (100, 101), (101, 221), (220, 300)]
    assert all(a < b for _, (a, b) in plan)


def test_subtitles_are_two_short_lines_timed_to_the_words():
    words = "So here we are again, at the same table, with the same cold coffee. Tell me why you came back tonight, Ray."
    toks = words.split()
    seg = {"start": 1.0, "end": 9.0, "text": words, "words": [{"word": w, "start": 1.0 + i * 0.35, "end": 1.3 + i * 0.35} for i, w in enumerate(toks)]}
    cues = se.make_cues([seg, {"start": 12.0, "end": 12.3, "text": "Yes.", "words": []}])
    assert len(cues) >= 2
    for c in cues:
        assert 1 <= len(c["lines"]) <= 2 and all(len(line) <= 42 for line in c["lines"]), c
    assert cues[0]["lines"][-1].endswith("coffee."), "a sentence that fills half a subtitle closes it"
    assert all(a["end"] <= b["start"] for a, b in zip(cues, cues[1:])), "subtitles never overlap"
    assert cues[-1]["lines"] == ["Yes."] and cues[-1]["end"] - cues[-1]["start"] >= 0.8, "a short one stays up long enough to read"
    assert " ".join(" ".join(c["lines"]) for c in cues[:-1]) == words, "every word, in order"
    srt = se.to_srt(cues)
    assert srt.startswith("1\n00:00:01,000 --> ") and "\n\n2\n" in srt
    assert se.to_vtt(cues).startswith("WEBVTT\n\n00:00:01.000 --> ")
    assert se.wrap_lines("a b", 42) == ["a b"] and se.srt_time(3723.5) == "01:02:03,500"
    short = se.make_cues([seg], max_chars=32)
    assert all(len(line) <= 32 for c in short for line in c["lines"])


# ---------------------------------------------------------------- AI Lossless Cut
@pytest.fixture(scope="module")
def gop(tmp_path_factory):
    """10 s at 24 fps with a keyframe every second (frame 24k), and a tone."""
    p = tmp_path_factory.mktemp("cut") / "gop.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "10",
       "-c:v", "libx264", "-g", "24", "-keyint_min", "24", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", p)
    return p


def psnr(a, b):
    r = subprocess.run([FF, "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi", "psnr", "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"average:([\d.]+|inf)", r.stderr)
    return float("inf") if m.group(1) == "inf" else float(m.group(1))


def test_a_keyframe_cut_starts_on_the_keyframe_before_the_in_and_says_so(gop, tmp_path):
    p = run_engine("AI Lossless Cut", [gop, "--segments", "1.291667-3.708333,5.000000-6.500000", "--mode", "keyframe", "--all-tracks",
                                       "--out", tmp_path / "k"], tmp_path)
    a, b = p["pieces"]
    assert (a["start"], a["asked_start"], a["mode"], a["reencoded"]) == (1.0, 1.291667, "copy", [])
    assert b["start"] == 5.0, "an In on a keyframe is exact"
    assert a["end"] >= 3.708333 and "1 starts on the keyframe before the In" in p["note"]
    assert [os.path.basename(o) for o in p["outs"]] == ["gop - piece 01.mp4", "gop - piece 02.mp4"]


def test_a_smart_cut_starts_exactly_on_the_in_with_its_sound_in_sync(gop, tmp_path):
    p = run_engine("AI Lossless Cut", [gop, "--segments", "1.291667-3.708333", "--mode", "smart", "--all-tracks", "--out", tmp_path / "s"], tmp_path)
    pc, out = p["pieces"][0], p["outs"][0]
    assert (pc["start"], pc["mode"], pc["reencoded"]) == (1.291667, "smart", [[1.291667, 2.0]])
    ff("-i", out, "-frames:v", "1", tmp_path / "first.png")
    ff("-i", gop, "-vf", r"select=eq(n\,31)", "-frames:v", "1", tmp_path / "f31.png")
    ff("-i", gop, "-vf", r"select=eq(n\,30)", "-frames:v", "1", tmp_path / "f30.png")
    assert psnr(tmp_path / "first.png", tmp_path / "f31.png") > 40 > psnr(tmp_path / "first.png", tmp_path / "f30.png"), "frame 31 (1.2917 s) first"
    import av
    with av.open(out) as c:
        v, a = c.streams.video[0], c.streams.audio[0]
        vd, ad = float(v.duration * v.time_base), float(a.duration * a.time_base)
    assert abs(vd - ad) < 0.15, ("the sound is copied from the In, not from the keyframe before it", vd, ad)
    assert 58 <= frames(out) <= 61


def test_a_sound_file_cuts_anywhere(tmp_path):
    wav = tmp_path / "tone.wav"
    ff("-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=6", wav)
    p = run_engine("AI Lossless Cut", [wav, "--segments", "1.25-2.5,4.0-99", "--mode", "smart", "--out", tmp_path / "a"], tmp_path)
    assert [pc["start"] for pc in p["pieces"]] == [1.25, 4.0] and p["pieces"][1]["end"] == pytest.approx(6.0, abs=0.05)


# ---------------------------------------------------------------- One-Click Master
@pytest.fixture(scope="module")
def mix(tmp_path_factory):
    """20 s of something like music: a chord over pink noise, quiet then loud."""
    p = tmp_path_factory.mktemp("master") / "mix.wav"
    ff("-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.05:duration=20:sample_rate=48000", "-f", "lavfi", "-i",
       "sine=frequency=220:duration=20:sample_rate=48000", "-f", "lavfi", "-i", "sine=frequency=330:duration=20:sample_rate=48000",
       "-filter_complex", "[0][1][2]amix=inputs=3,volume='if(lt(t,8),0.25,0.9)':eval=frame", "-ac", "2", p)
    return p


@pytest.mark.parametrize("target,ceiling,tone", [(-14, -1, "transparent"), (-9, -1, "punchy"), (-23, -2, "warm")])
def test_a_master_lands_on_the_target_with_the_true_peak_under_the_ceiling(mix, tmp_path, target, ceiling, tone):
    p = run_engine("One-Click Master", [mix, "--target", target, "--ceiling", ceiling, "--character", tone, "--out", tmp_path / "m.wav"], tmp_path)
    st = p["stats"]
    assert abs(st["integrated_lufs"] - target) <= 0.5, st
    assert st["true_peak_dbtp"] <= ceiling + 0.05, st
    assert p["outs"][0].endswith(".wav") and os.path.getsize(p["outs"][0]) > 1000
    import av
    with av.open(p["outs"][0]) as c:
        assert c.streams.audio[0].codec_context.name == "pcm_s24le"


def test_a_video_is_mastered_with_its_picture_untouched(gop, tmp_path):
    p = run_engine("One-Click Master", [gop, "--target", -16, "--out", tmp_path / "v.mp4"], tmp_path)
    out = p["outs"][0]
    assert out.endswith(".mp4") and frames(out) == frames(gop) == 240
    assert abs(p["stats"]["integrated_lufs"] + 16) <= 0.5


# ---------------------------------------------------------------- AI Depth's effects
def sharpness(path, rows):
    """How sharp a band of rows of a picture is (the variance of its Laplacian)."""
    import av
    with av.open(str(path)) as c:
        g = next(c.decode(video=0)).to_ndarray(format="gray").astype("float32")
    band = g[rows[0]:rows[1]]
    lap = band[1:-1, 1:-1] * 4 - band[:-2, 1:-1] - band[2:, 1:-1] - band[1:-1, :-2] - band[1:-1, 2:]
    return float(lap.var())


def test_depth_of_field_blurs_what_is_far_from_the_focus_and_fog_thickens_with_distance(tmp_path):
    depth = engine("AI Depth")
    W, H = 320, 240
    pic, ramp = tmp_path / "pic.png", tmp_path / "ramp.png"
    ff("-f", "lavfi", "-i", f"testsrc2=size={W}x{H}", "-frames:v", "1", pic)
    ff("-f", "lavfi", "-i", f"nullsrc=s={W}x{H},geq=lum='255*Y/H':cb=128:cr=128", "-frames:v", "1", ramp)   # the bottom is near (white)
    near, far = (H - 24, H - 4), (4, 24)                 # depth within the sharp band of one end, and the other end

    def apply(kind, name, **kw):
        fc = depth.effect_filter(kind, W, H, 25.0, kw.get("flip", False), kw.get("focus", 0.0), 2.0, kw.get("density", 1.0), 1.0, True)
        out = tmp_path / name
        ff("-i", pic, "-i", ramp, "-filter_complex", fc, "-map", "[v]", "-frames:v", "1", out)
        return out
    orig = (sharpness(pic, near), sharpness(pic, far))
    at_near = apply("dof", "near.png", focus=0.0)
    assert sharpness(at_near, near) > 0.8 * orig[0] and sharpness(at_near, far) < 0.3 * orig[1], "focused near: the far rows blur"
    at_far = apply("dof", "far.png", focus=1.0)
    assert sharpness(at_far, far) > 0.8 * orig[1] and sharpness(at_far, near) < 0.3 * orig[0], "focused far: the near rows blur"
    fog = apply("fog", "fog.png", density=1.0)
    import av
    with av.open(str(fog)) as c:
        g = next(c.decode(video=0)).to_ndarray(format="gray").astype("float32")
    assert g[far[0]:far[1]].std() < 0.2 * g[near[0]:near[1]].std(), "the far rows are fogged over, the near ones clear"
    assert depth.near_is_dark(str(ramp)) is False
    flipped = tmp_path / "flipped.png"
    ff("-i", ramp, "-vf", "negate", flipped)
    assert depth.near_is_dark(str(flipped)) is True


# ---------------------------------------------------------------- AI Stems and AI Subtitles, with the sandbox's stand-ins
def test_stems_and_subtitles_with_the_sandboxs_stand_ins(gop, tmp_path):
    env = {"PYTHONPATH": STUBS}
    p = run_engine("AI Stems", [gop, "--stems", 4, "--out", tmp_path / "stems"], tmp_path, env)
    assert [os.path.basename(o) for o in p["outs"]] == ["gop - vocals.wav", "gop - drums.wav", "gop - bass.wav", "gop - other.wav"]
    two = run_engine("AI Stems", [gop, "--stems", 2, "--format", "flac", "--out", tmp_path / "two"], tmp_path, env)
    assert [os.path.basename(o) for o in two["outs"]] == ["gop - vocals.flac", "gop - music (no vocals).flac"]
    s = run_engine("AI Subtitles", [gop, "--language", "auto", "--out", tmp_path / "subs"], tmp_path, env)
    assert [os.path.basename(o) for o in s["outs"]] == ["gop.srt", "gop.vtt", "gop transcript.txt"]
    srt = open(s["outs"][0], encoding="utf-8").read()
    assert srt.startswith("1\n00:00:00,500 --> ") and "Sandbox transcript" in srt
    assert s["stats"]["language"] == "en" and s["stats"]["subtitles"] >= 2
    mute = tmp_path / "mute.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=64x36:rate=24", "-t", "1", mute)
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "AI Subtitles", "engine.py"), mute, "--out", tmp_path / "x", "--progress", tmp_path / "x.json"],
                       capture_output=True, env=dict(os.environ, STUDIO_FFMPEG=FF, PYTHONPATH=STUBS))
    assert r.returncode == 3 and json.load(open(tmp_path / "x.json"))["error"] == "mute.mp4 has no sound"
