"""The AI tools (Production › AI tools: Film Engine's Generators in Studio) end to end in the sandbox, on a sandbox of
their own (their results land in the Gallery, which other tests count):
  - the six tools are listed, ready, in order; a Gallery file, an upload (any name) or a sound from the output folder can
    be given to them, and nothing else
  - each tool runs through Studio's HTTP API: AI Slow-Mo and AI Depth queue their work on the stand-in ComfyUI (checked
    like ComfyUI checks it, named after the tool in the queue), the others run ffmpeg or the stand-ins for Demucs and
    Whisper; every result lands in Generators/<tool>, the pictures and videos in the Gallery, and no work file is left
  - Cancel stops a job waiting in ComfyUI's queue; the page: the Production menu lists the tools, each page opens, and
    a clip from the Gallery is slowed down from its marked section, in headless Chrome with no console errors; Home's
    "Your apps" has a shortcut to each tool after the apps"""
import asyncio
import glob
import json
import os
import time
import urllib.request

import pytest

from conftest import Sandbox
from test_screenplay_import import _port, _with_chrome, chrome
from find_chrome import WHY_NOT  # noqa: E402

pytestmark = pytest.mark.generators

G = "/studio/api/generators"
ORDER = ["depth", "slowmo", "stems", "subtitles", "cut", "master"]


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    s = Sandbox(tmp_path_factory.mktemp("aitools"), "demo")
    yield s
    s.stop()


def wait(box, jid, limit=90):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = box.get(f"{G}/jobs/{jid}")["job"]
        if j["status"] not in ("running", "queued"):
            return j
        time.sleep(0.2)
    raise AssertionError(f"job {jid} still {j['status']}: {j['stage']}")


def job(box, **body):
    code, r = box.post(G + "/jobs", body)
    assert code == 202, r
    return wait(box, r["job"]["id"])


def upload(box, name, data):
    req = urllib.request.Request(box.url + G + "/inputs/import", data=data, method="POST",
                                 headers={"X-File-Name": urllib.request.quote(name), "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def comfy_jobs(box):
    out = []
    for f in glob.glob(os.path.join(box.jobs, "*.json")):
        with open(f, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def test_the_six_tools_are_listed_ready_in_order(box):
    tools = box.get(G + "/tools")["tools"]
    assert [t["id"] for t in tools] == ORDER
    assert [t["name"] for t in tools] == ["AI Depth", "AI Slow-Mo", "AI Stems", "AI Subtitles", "AI Lossless Cut", "One-Click Master"]
    assert not [t for t in tools if t.get("unavailable")], [t.get("unavailable") for t in tools]
    assert all("dir" not in t and "needs" not in t for t in tools)
    for path in (G + "/jobs", G + "/files"):
        with urllib.request.urlopen(box.url + path, timeout=30) as r:
            assert r.status == 200


def test_files_come_from_the_gallery_an_upload_or_the_output_folder_and_nothing_else(box):
    code, r = box.post(G + "/inputs", {"filename": "diner-wide.mp4", "subfolder": "Videos"})
    assert code == 200 and r["input"]["kind"] == "video" and r["input"]["frames"] == 96 and "path" not in r["input"]
    with open(os.path.join(box.run, "input", "song.wav"), "rb") as f:
        code, r = upload(box, "Chanson d'été.wav", f.read())
    assert code == 200 and r["input"]["kind"] == "audio" and r["input"]["name"].startswith("Chanson d_été")
    assert r["input"]["source"]["input"].startswith("studio/ai-tools/")
    code, r = upload(box, "notes.txt", b"hello")
    assert code == 400 and "not a video, audio or image" in r["error"]
    code, r = upload(box, "fake.mp4", b"not a video at all")
    assert code == 422 and not os.path.exists(os.path.join(box.run, "input", "studio", "ai-tools", "fake.mp4")), "a file no tool can read isn't kept"
    for body, want in (({"filename": "passwd", "subfolder": "../../../../etc"}, 400), ({"input": "studio_private/face.png"}, 403),
                       ({"filename": "diner-wide.mp4", "subfolder": "Videos", "private": True}, 403), ({}, 400)):
        code, r = box.post(G + "/inputs", body)
        assert code == want, (body, r)


def test_every_tool_runs_and_its_results_land_in_the_gallery(box):
    vid = box.post(G + "/inputs", {"filename": "diner-ray.mp4", "subfolder": "Videos"})[1]["input"]
    pic = box.post(G + "/inputs", {"filename": "diner.jpg", "subfolder": "Images"})[1]["input"]
    with open(os.path.join(box.run, "input", "song.wav"), "rb") as f:
        song = upload(box, "song.wav", f.read())[1]["input"]
    n0 = len(comfy_jobs(box))
    made = {}
    made["slowmo"] = job(box, tool="slowmo", input_id=vid["id"], start=12, end=60, options={"factor": 4, "compare": True})
    made["depth"] = job(box, tool="depth", input_id=vid["id"], options={"output": "dof", "focus": 0.3})
    made["depth-pic"] = job(box, tool="depth", input_id=pic["id"], options={"output": "fog"})
    made["stems"] = job(box, tool="stems", input_id=vid["id"], options={"stems": 2})
    made["subtitles"] = job(box, tool="subtitles", input_id=vid["id"], options={"language": "fr", "translate": True})
    made["cut"] = job(box, tool="cut", input_id=vid["id"], segments=[[0.5, 1.5], [2.0, 3.5]], options={"mode": "smart", "join": True})
    made["master"] = job(box, tool="master", input_id=song["id"], options={"target": -16})
    for k, j in made.items():
        assert j["status"] == "done", (k, j["error"])
    kinds = {k: [(o["subfolder"].split("/")[1], o["kind"], os.path.splitext(o["name"])[1]) for o in j["outputs"]] for k, j in made.items()}
    assert kinds["slowmo"] == [("AI Slow-Mo", "video", ".mp4")] and made["slowmo"]["outputs"][0]["name"].startswith("diner-ray - AI Slow-Mo f12-60 ")
    assert kinds["depth"] == [("AI Depth", "video", ".mp4")] and kinds["depth-pic"] == [("AI Depth", "image", ".png")]
    assert [o["name"] for o in made["stems"]["outputs"]] == ["diner-ray - vocals.wav", "diner-ray - music (no vocals).wav"]
    assert [o["name"] for o in made["subtitles"]["outputs"]] == ["diner-ray.srt", "diner-ray.vtt", "diner-ray transcript.txt"]
    assert made["subtitles"]["stats"]["language"] == "en (translated from fr)"
    assert [o["name"] for o in made["cut"]["outputs"]] == ["diner-ray - piece 01.mp4", "diner-ray - piece 02.mp4", "diner-ray - joined.mp4"]
    assert [p["start"] for p in made["cut"]["pieces"]] == [0.5, 2.0]
    assert kinds["master"] == [("One-Click Master", "audio", ".wav")] and abs(made["master"]["stats"]["integrated_lufs"] + 16) <= 0.5
    # the outputs are served, and the pictures and videos are in the Gallery
    for j in made.values():
        for n, o in enumerate(j["outputs"]):
            with urllib.request.urlopen(f"{box.url}{G}/jobs/{j['id']}/output/{n}", timeout=30) as r:
                assert r.status == 200 and len(r.read()) == o["size"]
    gallery = {(i["subfolder"], i["filename"]) for i in box.get("/studio/api/gallery")}
    for k in ("slowmo", "depth", "depth-pic", "cut"):
        for o in made[k]["outputs"]:
            assert (o["subfolder"], o["name"]) in gallery, (k, o)
    assert any(f["kind"] == "audio" and f["subfolder"].startswith("Generators/One-Click Master") for f in box.get(G + "/files")["files"])
    # the GPU work went through ComfyUI's queue, checked like ComfyUI checks it, named after the tool
    cj = comfy_jobs(box)[n0:]
    by_label = {}
    for c in cj:
        by_label.setdefault(c.get("label"), []).append(c)
    assert {c["status"] for c in cj} == {"success"}, [c.get("error") for c in cj]
    assert {c["app"] for c in cj} == {"AI tools"} and {c["client_id"] for c in cj} == {"studio-generators"}
    assert set(by_label) == {"AI Slow-Mo · diner-ray.mp4", "AI Depth · diner-ray.mp4", "AI Depth · diner.jpg"}
    interp = by_label["AI Slow-Mo · diner-ray.mp4"][0]["prompt"]
    assert {n["class_type"] for n in interp.values()} >= {"FrameInterpolate", "FrameInterpolationModelLoader", "LoadVideo", "SaveVideo"}
    assert next(n for n in interp.values() if n["class_type"] == "FrameInterpolate")["inputs"]["multiplier"] == 4
    da3 = by_label["AI Depth · diner.jpg"][0]["prompt"]
    assert next(n for n in da3.values() if n["class_type"] == "LoadDA3Model")["inputs"]["model_name"] == "da3_mono_large.safetensors"
    # and nothing is left behind: not ComfyUI's work files, not the engines'
    left = [p for p in glob.glob(os.path.join(box.run, "input", "studio", "ai-tools", ".ai-tools-*")) +
            glob.glob(os.path.join(box.run, "output", ".ai-tools", "*")) +
            glob.glob(os.path.join(box.run, "user", "default", "studio_data", "generators", "work", "*"))]
    assert not left, left


def test_a_job_refused_for_its_file_or_its_settings_says_why(box):
    pic = box.post(G + "/inputs", {"filename": "diner.jpg", "subfolder": "Images"})[1]["input"]
    vid = box.post(G + "/inputs", {"filename": "diner-wide.mp4", "subfolder": "Videos"})[1]["input"]
    for body, want in (({"tool": "slowmo", "input_id": pic["id"]}, "needs a video, not a picture"),
                       ({"tool": "master", "input_id": pic["id"]}, "not a picture"),
                       ({"tool": "slowmo", "input_id": vid["id"], "options": {"factor": 5}}, "not one of its choices"),
                       ({"tool": "cut", "input_id": vid["id"], "segments": []}, "at least one piece"),
                       ({"tool": "nope", "input_id": vid["id"]}, "no AI tool called nope"),
                       ({"tool": "depth", "input_id": "gone"}, "no longer registered")):
        code, r = box.post(G + "/jobs", body)
        assert code in (400, 404) and want in r["error"], (body, r)


def test_cancel_takes_a_waiting_job_off_comfyuis_queue(box):
    vid = box.post(G + "/inputs", {"filename": "diner-counter.mp4", "subfolder": "Videos"})[1]["input"]
    # something else renders first (60 renders of 0.2 s here), so the tool's job waits in ComfyUI's queue
    blocker = {"1": {"class_type": "EmptyImage", "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0}},
               "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "blocker"}}}
    pids = [box.post("/prompt", {"prompt": blocker, "client_id": "test"})[1]["prompt_id"] for _ in range(60)]
    code, r = box.post(G + "/jobs", {"tool": "depth", "input_id": vid["id"], "options": {"output": "color"}})
    jid = r["job"]["id"]
    t0 = time.time()
    while "Waiting in ComfyUI" not in box.get(f"{G}/jobs/{jid}")["job"]["stage"]:
        assert time.time() - t0 < 30
        time.sleep(0.1)
    code, c = box.post(f"{G}/jobs/{jid}/cancel", {})
    assert c["job"]["status"] == "cancelled"
    time.sleep(0.5)
    q = box.get("/queue")
    assert not [x for x in q["queue_running"] + q["queue_pending"] if x[3].get("client_id") == "studio-generators" or x[1] not in pids]
    box.post("/queue", {"clear": True})                     # the blockers have done their part
    assert box.get(f"{G}/jobs/{jid}")["job"]["status"] == "cancelled"


@pytest.mark.skipif(chrome() is None, reason=WHY_NOT)
def test_the_production_menu_lists_the_tools_and_a_clip_is_slowed_from_the_page(box, tmp_path):
    async def run(page):
        await page.js(f"location.href = '{box.url}/studio/#/home'")
        await page.until("!!(document.querySelector('#menus [data-menu=prod]'))")
        # Home › Your apps: a shortcut to each tool, after the apps
        await page.until("!!(document.querySelector('#apps a.qcard.ai'))")
        home = await page.js("[...document.querySelectorAll('#apps .qcard')].map(c => [c.getAttribute('href') || '', c.querySelector('h3').textContent])")
        await page.js("document.querySelector('#apps a.qcard.ai[href=\"#/aitools/stems\"]').click()")
        await page.until("!!(document.querySelector('.ait h1') && document.querySelector('.ait h1').textContent === 'AI Stems')")
        shortcut = await page.js("location.hash + ' ' + document.querySelector('.ait h1').textContent")
        await page.js("location.hash = '#/home'")
        await page.until("!!(document.querySelector('#menus [data-menu=prod]') && document.querySelector('#apps'))")
        await page.js("document.querySelector('#menus [data-menu=prod]').click()")
        await page.until("!!(document.querySelector('#menupop .mi'))")
        menu = await page.js("""[...document.querySelectorAll('#menupop .mg')].filter(g => g.querySelector('.mgh').textContent === 'AI tools')
            .map(g => [...g.querySelectorAll('.mi')].map(a => [a.getAttribute('href'), a.querySelector('b').textContent]))[0]""")
        pages = {}
        for href, _name in menu:
            await page.js(f"location.hash = '{href}'")
            await page.until(f"!!(location.hash === '{href}' && document.querySelector('.ait h1') && document.querySelector('.ait-panel [data-go]'))")
            pages[href] = await page.js("document.querySelector('.ait h1').textContent")
        # a clip from the Gallery, its section marked with the keyboard, slowed down
        await page.js("location.hash = '#/aitools/slowmo'")
        await page.until("!!(document.querySelector('.ait-drop [data-gal]'))")
        await page.js("document.querySelector('.ait-drop [data-gal]').click()")
        await page.until("!!(document.querySelector('.ait-pfile[title=\"Videos/diner-wide.mp4\"]'))")
        await page.js("document.querySelector('.ait-pfile[title=\"Videos/diner-wide.mp4\"]').click()")
        await page.until("!!(document.querySelector('.ait-info b') && document.querySelector('.ait-info b').textContent === 'diner-wide.mp4')")
        for t, key in ((0.5, "i"), (1.5, "o")):
            await page.js(f"(() => {{ const v = document.querySelector('.ait-player video'); v.currentTime = {t}; }})()")
            await asyncio.sleep(0.4)
            await page.key(key, "Key" + key.upper(), ord(key.upper()), key)
        section = await page.js("document.querySelector('[data-testid=ait-section]').textContent")
        await page.js("document.querySelector('.ait-panel [data-go]').click()")
        mine = "[...document.querySelectorAll('.ait-jobs li')].find(li => li.textContent.includes('AI Slow-Mo') && li.textContent.includes('diner-wide.mp4'))"
        await page.until(f"!!((li => li && li.querySelector('.ait-status').textContent === 'Done')({mine}))", tries=400)
        done = await page.js(f"{mine}.textContent")
        return home, shortcut, menu, pages, section, done
    (home, shortcut, menu, pages, section, done), errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    names = ["AI Depth", "AI Slow-Mo", "AI Stems", "AI Subtitles", "AI Lossless Cut", "One-Click Master"]
    assert [n for _, n in home][-6:] == names and [h for h, _ in home][-6:] == [f"#/aitools/{i}" for i in ORDER], home
    assert not [h for h, _ in home[:-6] if h], "the apps come first, as before"
    assert shortcut == "#/aitools/stems AI Stems"
    assert [m[1] for m in menu] == names
    assert list(pages.values()) == [m[1] for m in menu]
    assert section == "Section 00:00:00:12 → 00:00:01:13 · 25 frames", "In on frame 12, Out after frame 36 (the frame on screen is in)"
    assert "frames 12–36" in done and "diner-wide - AI Slow-Mo f12-37" in done
    assert not [e for e in errors if "MEDIA_ERR" not in e and "no supported source" not in e.lower()], errors
