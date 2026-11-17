"""The sandbox end to end: Studio's pack loaded unchanged in the stand-in ComfyUI, its routes, ComfyUI's API, job checks,
the render loop, the demo and empty data, the MCP call tool, and the pages in a real browser (when one is installed)."""
import asyncio
import glob
import json
import os
import shutil
import subprocess
import sys

import aiohttp
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STUDIO_GETS = ["/studio/", "/studio/api/info", "/studio/api/apps", "/studio/api/gallery", "/studio/api/characters", "/studio/api/boards",
               "/studio/api/projects", "/studio/api/models", "/studio/api/topaz/status", "/studio/api/guard", "/studio/api/sysmon",
               "/studio/api/workers", "/studio/api/sound/options", "/studio/api/poses", "/studio/api/schedule", "/studio/api/loras",
               "/studio/api/paths", "/studio/api/private/status",
               "/studio/api/remote_jobs", "/studio/api/queue/unfinished", "/studio/api/diag/power",
               "/studio/api/final_requests", "/studio/api/civitai/status", "/studio/api/models/removed", "/studio/api/topaz/jobs",
               "/studio/api/glb"]


def test_studio_loads_and_every_route_answers(demo):
    assert demo.health["pack_error"] is None
    import urllib.request
    for path in STUDIO_GETS:
        with urllib.request.urlopen(demo.url + path, timeout=30) as r:
            assert r.status == 200, path
    assert demo.get("/studio/api/topaz/status")["video"]["found"] is False
    assert demo.get("/studio/api/diag/power") == []


def test_blender_is_gone(demo):
    """Studio doesn't run Blender any more: Previz directs .glb sets in the browser instead."""
    import urllib.error
    import urllib.request
    for path in ("/studio/api/blender/status", "/studio/api/sets", "/studio/api/scenes", "/studio/api/locations"):
        try:
            urllib.request.urlopen(demo.url + path, timeout=30)
            raise AssertionError(path + " still answers")
        except urllib.error.HTTPError as e:
            assert e.code in (404, 405), (path, e.code)
    assert not os.path.exists(os.path.join(REPO, "ComfyUI-Studio", "blender"))


def test_model_lists_show_only_downloaded_files(scratch):
    """The sandbox's model files are empty stand-ins (so jobs pass ComfyUI's checks): Models and Settings › Models list
    none of them, nor a download still in progress; a real file shows up."""
    assert scratch.get("/studio/api/loras") == []
    assert scratch.get("/studio/api/models")["items"] == []
    loras = os.path.join(scratch.run, "models", "loras")
    with open(os.path.join(loras, "my_style.safetensors"), "wb") as f:
        f.write(b"\x08" * 2048)
    with open(os.path.join(loras, "half_way.safetensors.part"), "wb") as f:
        f.write(b"\x08" * 2048)
    try:
        assert [x["file"] for x in scratch.get("/studio/api/loras")] == ["my_style.safetensors"]
        assert [x["name"] for x in scratch.get("/studio/api/models")["items"]] == ["my_style.safetensors"]
    finally:
        os.remove(os.path.join(loras, "my_style.safetensors"))
        os.remove(os.path.join(loras, "half_way.safetensors.part"))


def test_no_windows_only_program_is_started(demo):
    demo.get("/studio/api/sysmon")
    demo.get("/studio/api/diag/power")
    demo.get("/studio/api/guard")
    assert not os.path.exists(demo.winlog) or open(demo.winlog).read() == ""


def test_comfyui_api(demo):
    oi = demo.get("/object_info")
    assert "z_image_turbo_int8_convrot.safetensors" in oi["UNETLoader"]["input"]["required"]["unet_name"][0]
    assert "StudioKeep" in oi and oi["StudioKeep"]["output_node"] is True and oi["StudioKeep"]["python_module"].startswith("custom_nodes.")
    assert set(demo.get("/object_info/LoadVideo")) == {"LoadVideo"}
    assert "street.mp4" in oi["LoadVideo"]["input"]["required"]["file"][1]["options"]          # a V3 combo: ["COMBO", {"options"}]
    assert demo.get("/queue") == {"queue_running": [], "queue_pending": []} or "queue_running" in demo.get("/queue")
    assert demo.get("/system_stats")["system"]["os"] == "linux"
    assert demo.get("/api/object_info/VAELoader")                     # every route also under /api, like ComfyUI


def test_upload_and_view(demo):
    import urllib.request
    body = (b"--x\r\nContent-Disposition: form-data; name=\"image\"; filename=\"up.png\"\r\nContent-Type: image/png\r\n\r\n"
            + open(os.path.join(demo.run, "input", "portrait.png"), "rb").read()
            + b"\r\n--x\r\nContent-Disposition: form-data; name=\"subfolder\"\r\n\r\ntests\r\n--x--\r\n")
    req = urllib.request.Request(demo.url + "/upload/image", data=body, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=x"})
    with urllib.request.urlopen(req) as r:
        assert json.loads(r.read()) == {"name": "up.png", "subfolder": "tests", "type": "input"}
    with urllib.request.urlopen(demo.url + "/view?filename=up.png&subfolder=tests&type=input") as r:
        assert r.headers["Content-Type"] == "image/png" and r.read()[:4] == b"\x89PNG"


def test_a_job_with_a_missing_model_is_refused_like_comfyui(demo):
    bad = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "not_here.safetensors", "weight_dtype": "default"}},
           "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3}},
           "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}}
    code, r = demo.post("/prompt", {"prompt": bad})
    assert code == 400 and r["error"]["type"] == "prompt_outputs_failed_validation"
    e = r["node_errors"]["1"]["errors"][0]
    assert e["type"] == "value_not_in_list" and e["details"].startswith("unet_name: 'not_here.safetensors' not in")
    code, r = demo.post("/prompt", {"prompt": {"1": {"class_type": "Nope", "inputs": {}}}})
    assert code == 400 and r["error"]["type"] == "missing_node_type"
    assert glob.glob(os.path.join(demo.jobs, "refused-*.json"))
    out = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "sandbox.py"), "queue-bad", "--port", str(demo.port)],
                         capture_output=True, text=True, env=dict(os.environ, SANDBOX_RUN=demo.run, SANDBOX_JOBS=demo.jobs)).stdout
    assert "Refused (400)" in out and "Value not in list" in out


def test_websocket_messages_and_history(demo):
    graph = {"1": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 512, "height": 768, "batch_size": 1}},
             "2": {"class_type": "VAELoader", "inputs": {"vae_name": "pixel_space"}},
             "3": {"class_type": "VAEDecode", "inputs": {"samples": ["1", 0], "vae": ["2", 0]}},
             "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "Images/ws test"}}}

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(demo.url + "/ws?clientId=pytest") as ws:
                first = json.loads((await ws.receive()).data)
                assert first["type"] == "status"
                async with s.post(demo.url + "/prompt", json={"prompt": graph, "client_id": "pytest"}) as r:
                    pid = (await r.json())["prompt_id"]
                seen = []
                while True:
                    m = json.loads((await asyncio.wait_for(ws.receive(), 30)).data)
                    if m["data"].get("prompt_id") == pid:
                        seen.append(m["type"])
                        if m["type"] == "executed":
                            out = m["data"]["output"]
                        if m["type"] == "executing" and m["data"]["node"] is None:
                            return pid, seen, out

    pid, seen, out = asyncio.run(go())
    assert seen[0] == "execution_start" and "progress" in seen and "executed" in seen and "execution_success" in seen
    f = out["images"][0]
    assert f["subfolder"] == "Images" and f["filename"].startswith("ws test_")
    from PIL import Image
    with Image.open(os.path.join(demo.run, "output", "Images", f["filename"])) as im:
        assert im.size == (512, 768)
    h = demo.get(f"/history/{pid}")[pid]
    assert h["status"]["status_str"] == "success" and h["outputs"]["4"] == out
    job = json.load(open(glob.glob(os.path.join(demo.jobs, f"*-{pid}.json"))[0]))
    assert job["status"] == "success" and job["prompt"] == graph and job["outputs"]["4"] == out


def test_every_app_runs_with_its_default_values(demo):
    apps = demo.get("/studio/api/apps")
    assert len(apps) == len(glob.glob(os.path.join(REPO, "workflows", "*.app.json")))
    pids = {}
    for a in apps:
        code, r = demo.post("/studio/api/run", {"app": a["id"], "values": {}, "client_id": "pytest"})
        assert code == 200, (a["id"], r)
        pids[a["id"]] = r["queued"][0]["prompt_id"]
    done = demo.wait_done(list(pids.values()), limit=180)
    for app, pid in pids.items():
        h = done[pid]
        assert h["status"]["status_str"] == "success", app
        files = [x for o in h["outputs"].values() for x in o.get("images", []) + o.get("audio", [])]
        assert files, app
        f = files[0]
        ext = os.path.splitext(f["filename"])[1]
        kind = next(x for x in apps if x["id"] == app).get("kind")
        assert ext == (".mp4" if kind == "video" else ".png"), (app, kind, f)
        assert os.path.getsize(os.path.join(demo.run, f["type"], f["subfolder"], f["filename"])) > 0


def test_demo_data(demo):
    """The demo is The Glass Harbour's pictures and clips only: no characters, Previz sets, storyboards or projects, so a
    screenplay import starts from a blank slate."""
    for k in ("characters", "glb", "boards", "projects"):
        assert demo.get(f"/studio/api/{k}") == [], k
    assert demo.seeded == {"clips": 3, "pictures": 5}
    names = {g["filename"] for g in demo.get("/studio/api/gallery")}
    assert {"diner-wide.mp4", "diner-ray.mp4", "diner-counter.mp4", "harbour-1A.jpg", "harbour-1D.jpg", "harbour-1H.jpg", "diner.jpg",
            "salt-shaker.jpg"} <= names                               # (other tests of the session render into it too)
    assert all(os.path.isfile(os.path.join(demo.run, "input", "studio", f)) for f in ("june.jpg", "ray.jpg", "diner.jpg"))


def test_samples_are_small_and_clean():
    import subprocess as sp
    samples = os.path.join(REPO, "sandbox", "samples")
    assert sum(os.path.getsize(os.path.join(samples, f)) for f in os.listdir(samples)) < 1_500_000
    for f in os.listdir(samples):
        p = os.path.join(samples, f)
        if f.endswith(".mp4"):
            tags = sp.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", p], capture_output=True, text=True).stdout
            assert set(json.loads(tags).get("format", {}).get("tags", {})) <= {"major_brand", "minor_version", "compatible_brands", "encoder"}, f
        else:
            from PIL import Image
            with Image.open(p) as im:
                assert not im.info.get("exif") and not im.info.get("xmp") and not im.getexif(), f


def test_empty_data(empty):
    for k in ("characters", "boards", "projects", "gallery", "glb"):
        assert empty.get(f"/studio/api/{k}") == [], k


def test_studio_call_runs_mcp_tools(demo):
    out = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "studio-call.py"), "studio_overview", "{}",
                          "--port", str(demo.port)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout.split("\n\n")[0])
    assert data["counts"]["characters"] == 0 and data["counts"]["boards"] == 0 and data["counts"]["projects"] == 0
    assert "Image" in data["apps"] and data["counts"]["apps"] == len(data["apps"])
    out = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "studio-call.py"), "--list", "--port", str(demo.port)],
                         capture_output=True, text=True, timeout=120)
    assert "studio_generate" in out.stdout


def test_studios_own_prune_test_passes_on_the_stand_in(demo):
    """tests/live/test_prune.py checks every app x every choice against a ComfyUI's /object_info: here, the stand-in's."""
    r = subprocess.run([sys.executable, "test_prune.py"], cwd=os.path.join(REPO, "tests", "live"), capture_output=True, text=True,
                       timeout=600, env=dict(os.environ, OBJECT_INFO=demo.url + "/object_info"))
    assert r.returncode == 0 and "all passed" in r.stdout, (r.stdout + r.stderr)[-3000:]


# ------------------------------------------------------------------ the pages in a real browser
def chrome():
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


async def page_errors(chrome_path, url, routes, port, profile):
    proc = await asyncio.create_subprocess_exec(chrome_path, "--headless=new", "--no-sandbox", "--disable-gpu",
                                                f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                                                "about:blank", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    errors = {}
    try:
        async with aiohttp.ClientSession() as s:
            for _ in range(100):
                try:
                    async with s.get(f"http://127.0.0.1:{port}/json/list") as r:
                        targets = [t for t in await r.json() if t["type"] == "page"]
                    if targets:
                        break
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(0.1)
            async with s.ws_connect(targets[0]["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                n = 0

                async def send(method, params=None):
                    nonlocal n
                    n += 1
                    await ws.send_json({"id": n, "method": method, "params": params or {}})

                for m in ("Runtime.enable", "Log.enable", "Network.enable", "Page.enable"):
                    await send(m)
                for route in routes:
                    errs = errors.setdefault(route, [])
                    await send("Page.navigate", {"url": f"{url}/studio/#/{route}"})
                    end = asyncio.get_running_loop().time() + 4
                    while asyncio.get_running_loop().time() < end:
                        try:
                            msg = await asyncio.wait_for(ws.receive_json(), 0.5)
                        except asyncio.TimeoutError:
                            continue
                        meth, p = msg.get("method"), msg.get("params", {})
                        if meth == "Runtime.exceptionThrown":
                            errs.append(p["exceptionDetails"].get("exception", {}).get("description", "exception"))
                        elif meth == "Runtime.consoleAPICalled" and p.get("type") == "error":
                            errs.append(" ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", [])))
                        elif meth == "Network.responseReceived" and p["response"]["status"] >= 400:
                            errs.append(f"HTTP {p['response']['status']} {p['response']['url']}")
    finally:
        proc.kill()
        await proc.wait()
    return errors


@pytest.mark.skipif(chrome() is None, reason="no Chrome or Chromium installed")
@pytest.mark.parametrize("which", ["demo", "empty"])
def test_pages_open_without_console_errors(which, request, tmp_path):
    s = request.getfixturevalue(which)
    import socket
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    errors = asyncio.run(page_errors(chrome(), s.url, ["home", "gallery", "create", "storyboard", "projects", "characters", "previz", "3d", "settings"],
                                     port, str(tmp_path / "chrome")))
    assert all(not v for v in errors.values()), errors
