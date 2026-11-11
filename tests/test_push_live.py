"""Live: a job needing a model only this PC has lands on a real Mac card through PUSH (the card can't pull it).

  ~/code/comfy-mac/.venv/bin/python tests/test_push_live.py        (needs mac-vega-b on 127.0.0.1:8189)

Studio runs here as the dispatcher, with a model folder of its own ("this PC"). The Smooth motion job asks for
frame_interpolation/zz-push-<id>/rife.safetensors (a copy of the Mac's own RIFE under a new name, so the render is
real and short). The card lacks it; it isn't on Hugging Face (a subfolder name: PC only) and the card can't reach
the PC, so /mac/fetch answers "push": true. Studio sends the file (PUT /mac/models), the job is gated again,
renders on the card under the id it got at once, and comes back here. Asserted: the job passed through
"Sending model", the card holds a byte-identical file, both cards list it, and the result is a playable video.
"""
import asyncio, glob, hashlib, importlib.util, json, os, shutil, sys, tempfile, time, types, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "ComfyUI-MannyStudio")
sys.path.insert(0, os.path.join(ROOT, "tests"))
from probes import make_inputs  # noqa: E402

MAC_MODELS = os.path.expanduser(os.environ.get("MAC_MODELS", "~/code/comfy-mac/ComfyUI/models"))
SRC = os.path.join(MAC_MODELS, "frame_interpolation", "rife_v4.25_lite.safetensors")
NAME = f"zz-push-{os.urandom(3).hex()}/rife.safetensors"
W = {"label": "mac-vega-b", "host": "127.0.0.1", "port": 8189}
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-pushlive-"))
for d in ("user/default/workflows", "input", "output", "temp", "pc_models/frame_interpolation"):
    os.makedirs(os.path.join(tmp, d), exist_ok=True)
for f in glob.glob(os.path.join(ROOT, "workflows", "*.app.json")):
    shutil.copy(f, os.path.join(tmp, "user/default/workflows"))
make_inputs({"input": os.path.join(tmp, "input")})
mine = os.path.join(tmp, "pc_models", "frame_interpolation", *NAME.split("/"))
os.makedirs(os.path.dirname(mine), exist_ok=True)
shutil.copy(SRC, mine)

fp = types.ModuleType("folder_paths")
fp.get_user_directory = lambda: os.path.join(tmp, "user")
fp.get_input_directory = lambda: os.path.join(tmp, "input")
fp.get_output_directory = lambda: os.path.join(tmp, "output")
fp.get_temp_directory = lambda: os.path.join(tmp, "temp")
fp.models_dir = os.path.join(tmp, "pc_models")
fp.folder_names_and_paths = {"frame_interpolation": ([os.path.join(tmp, "pc_models", "frame_interpolation")], {".safetensors"})}
fp.get_folder_paths = lambda k: list(fp.folder_names_and_paths.get(k, ([], None))[0])
fp.map_legacy = lambda k: k
fp.get_full_path = lambda f, n: (lambda p: p if os.path.isfile(p) else None)(os.path.join(fp.folder_names_and_paths[f][0][0], *n.replace("\\", "/").split("/"))) if f in fp.folder_names_and_paths else None
fp.get_filename_list = lambda f: []
sys.modules["folder_paths"] = fp


class _R:
    def __getattr__(self, m):
        return lambda path: (lambda fn: fn)


srv = types.ModuleType("server")
srv.PromptServer = type("PS", (), {"instance": types.SimpleNamespace(routes=_R(), app=types.SimpleNamespace(middlewares=[]), port=1,
                                                                   loop=None, prompt_queue=None, send_sync=lambda *a, **k: None)})
sys.modules["server"] = srv
spec = importlib.util.spec_from_file_location("studio", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
studio = importlib.util.module_from_spec(spec)
sys.modules["studio"] = studio
spec.loader.exec_module(studio)
remote = sys.modules["studio.remote"]


def options(port):
    o = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/object_info/FrameInterpolationModelLoader"))
    spec = o["FrameInterpolationModelLoader"]["input"]["required"]["model_name"]
    return remote.combo(spec)[0]


async def main():
    remote.save_workers(studio._data_dir(), [{"label": "mac-vega-a", "host": "127.0.0.1", "port": 8188}, W])

    async def oi(force=False):
        return (await remote.caps(W, force=True))[0]
    studio._get_object_info = oi
    check(NAME not in options(8189), f"the card lacks {NAME} before the job")
    app = next(a for a in studio._list_apps() if "Smooth motion" in a)
    _wf, api = await studio._build_prompt(app, {})
    for node in api.values():
        if node["class_type"] == "FrameInterpolationModelLoader":
            node["inputs"]["model_name"] = NAME.replace("/", "\\")       # as the Windows PC lists it
        for k, v in node["inputs"].items():
            if isinstance(v, str) and v.endswith((".mp4", ".mov", ".webm")):
                node["inputs"][k] = "studio_test/probe.mp4"
    remote.FETCH_POLL = 1
    pushes, real_push, real_fetch, answers = [], remote._push_file, remote._fetch, []

    async def spy_push(w, folder, value, path, job):     # what Studio sent, and to whom
        pushes.append((w["label"], folder, value, path))
        return await real_push(w, folder, value, path, job)

    async def spy_fetch(w, why):                         # what the card answered
        a = await real_fetch(w, why)
        answers.append(a)
        return a
    remote._push_file, remote._fetch = spy_push, spy_fetch
    t0 = time.time()
    pid = await studio._queue_api(api, {"manny_app": app, "manny_label": "push live"}, False, W["label"], "studio-pushlive")
    job = remote.JOBS[pid]
    seen = []
    while not job["task"].done():
        s = (job["status"], (job.get("step") or "").split(":")[0])
        if not seen or seen[-1] != s:
            seen.append(s)
            print(f"  {time.time() - t0:6.1f}s {s[0]} {s[1]}", flush=True)
        await asyncio.sleep(0.3)
    try:
        await job["task"]
    except Exception:
        pass
    dst = os.path.join(MAC_MODELS, "frame_interpolation", *NAME.split("/"))
    same = os.path.isfile(dst) and hashlib.sha256(open(dst, "rb").read()).hexdigest() == hashlib.sha256(open(mine, "rb").read()).hexdigest()
    first = (answers or [[{}]])[0][0]
    check(first.get("status") == "unavailable" and first.get("push") is True, f"the card answered: can't pull, push it ({first})")
    check(pushes == [("mac-vega-b", "frame_interpolation", NAME.replace("/", "\\"), mine)], f"Studio pushed it, once: {pushes}")
    check(same, f"the card holds a byte-identical copy at models/frame_interpolation/{NAME}")
    check(NAME in options(8188) and NAME in options(8189), "both cards list it without a restart")
    out = job["outputs"][0]["path"] if job.get("outputs") else ""
    vt = sys.modules["studio.videotools"]
    info = vt.probe(out) if out and os.path.isfile(out) else {}
    check(job["status"] == "done" and job["worker"] == "mac-vega-b" and info.get("duration", 0) > 0,
          f"rendered on mac-vega-b and came back ({job['status']} {job.get('error') or ''}, {info}) in {time.time() - t0:.0f} s")
    print(f"\npushed test file left on the Mac: models/frame_interpolation/{NAME}")
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
