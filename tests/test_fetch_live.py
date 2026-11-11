"""Live (needs mac-vega-b on 127.0.0.1:8189 WITHOUT the model; it downloads 22 MB):
  ~/code/comfy-mac/.venv/bin/python tests/test_fetch_live.py

a Smooth motion job asking for rife_v4.25_lite (not on the Mac) sent BY NAME to mac-vega-b through
Studio's own _queue_api: the Mac downloads it, the job waits as "fetching", is gated again, lands on mac-vega-b
under the id it got at once, renders, comes back to this PC, and is forgotten on the Mac."""
import asyncio, glob, importlib.util, json, os, shutil, sys, tempfile, time, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "ComfyUI-MannyStudio")
sys.path.insert(0, os.path.join(ROOT, "tests"))
from probes import make_inputs
tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-livefetch-"))
for d in ("user/default/workflows", "input", "output", "temp"):
    os.makedirs(os.path.join(tmp, d))
for f in glob.glob(os.path.join(ROOT, "workflows", "*.app.json")):
    shutil.copy(f, os.path.join(tmp, "user/default/workflows"))
make_inputs({"input": os.path.join(tmp, "input")})
fp = types.ModuleType("folder_paths")
fp.get_user_directory = lambda: os.path.join(tmp, "user")
fp.get_input_directory = lambda: os.path.join(tmp, "input")
fp.get_output_directory = lambda: os.path.join(tmp, "output")
fp.get_temp_directory = lambda: os.path.join(tmp, "temp")
fp.get_filename_list = lambda *a: []
fp.get_folder_paths = lambda *a: []
fp.models_dir = os.path.join(tmp, "models")
fp.folder_names_and_paths = {}
sys.modules["folder_paths"] = fp
class _R:
    def __getattr__(self, m):
        return lambda path: (lambda fn: fn)
srv = types.ModuleType("server")
srv.PromptServer = type("PS", (), {"instance": types.SimpleNamespace(routes=_R(), app=types.SimpleNamespace(middlewares=[]), port=1, loop=None, prompt_queue=None, send_sync=lambda *a, **k: None)})
sys.modules["server"] = srv
spec = importlib.util.spec_from_file_location("studio", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
studio = importlib.util.module_from_spec(spec); sys.modules["studio"] = studio; spec.loader.exec_module(studio)
remote = sys.modules["studio.remote"]
MODEL = "rife_v4.25_lite.safetensors"
W = {"label": "mac-vega-b", "host": "127.0.0.1", "port": 8189}

async def main():
    remote.save_workers(studio._data_dir(), [{"label": "mac-vega-a", "host": "127.0.0.1", "port": 8188}, W])
    async def oi(force=False):
        return (await remote.caps(W, force=True))[0]
    studio._get_object_info = oi
    opts = (await remote.caps(W, force=True))[0]["FrameInterpolationModelLoader"]["input"]["required"]["model_name"][1]["options"]
    print("before: Mac lists", opts)
    app = next(a for a in studio._list_apps() if "Smooth motion" in a)
    d = studio._describe_app(app)
    vid = next(i for i in d["inputs"] if i["kind"] == "video")
    wf, api = await studio._build_prompt(app, {vid["key"]: "studio_test/probe.mp4"} if "key" in vid else {})
    for node in api.values():
        if node["class_type"] == "FrameInterpolationModelLoader":
            node["inputs"]["model_name"] = MODEL
        for k, v in node["inputs"].items():
            if isinstance(v, str) and v.endswith((".mp4", ".mov", ".webm")):
                node["inputs"][k] = "studio_test/probe.mp4"
    print("gate on mac-vega-b now:", [x["reason"] for x in remote.gate(api, *(await remote.caps(W, force=True)))])
    remote.FETCH_POLL = 1
    t0 = time.time()
    pid = await studio._queue_api(api, {"manny_app": app, "manny_label": "live fetch"}, False, "mac-vega-b", "studio-livefetch")
    job = remote.JOBS[pid]
    print(f"queued at once: {pid} status={job['status']} step={job.get('step')!r}")
    states = []
    while not job["task"].done():
        s = (job["status"], job.get("step"))
        if not states or states[-1] != s:
            states.append(s); print(f"  {time.time() - t0:5.1f}s {s[0]} {s[1] or ''}")
        await asyncio.sleep(0.5)
    try:
        await job["task"]
    except Exception:
        pass
    print(f"end {time.time() - t0:.1f}s: {job['status']} error={job.get('error')} outputs={[(o['filename'], os.path.getsize(o['path'])) for o in job['outputs']]} forgot={json.dumps(job.get('forgot'))[:300]}")
    opts = (await remote.caps(W, force=True))[0]["FrameInterpolationModelLoader"]["input"]["required"]["model_name"][1]["options"]
    print("after: Mac lists", opts)
    ok = job["status"] == "done" and job["outputs"] and job["worker"] == "mac-vega-b" and any(s[0] == "fetching" for s in states) and MODEL in opts
    print("LIVE", "PASS" if ok else "FAIL")
    return 0 if ok else 1
sys.exit(asyncio.run(main()))
