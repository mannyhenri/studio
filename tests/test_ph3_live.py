"""Live: P-H3 (w4a8 image model) renders on a Mac card, its files pushed by Studio (confer c301a683, item 5).

  PH3_DIR=<folder with diffusion_models/ text_encoders/ vae/> ~/code/comfy-mac/.venv/bin/python tests/test_ph3_live.py
  (needs mac-vega-b on 127.0.0.1:8189, idle; PH3_DIR holds the PC's p-h3-image_w4a8, p-h3-image_text_encoder_w4a8
  and p-h3-image_vae_int8_convrot, e.g. pulled from the PC's /manny/api/models/file in ranges)

First run (2026-11-12): push PASS (13.4 GB in 78 s, 3.6 in 22, 3.1 in 18); render FAIL on the card, "Trying to convert
Float8_e4m3fn to the MPS backend": the w4a8 files keep their weight scales (*.weight_s_rel) as F8_E4M3, which comfy-mac
has to upcast before they reach MPS.

Studio is the dispatcher here, with PH3_DIR as "this PC's" model folders. The Image app's P-H3 choice is built and
sent by name to mac-vega-b through Studio's own path: the card lacks the files, /mac/fetch answers push, Studio PUTs
them, the job is gated again and renders. Reported either way: pass with the picture pulled back (size, not blank), or
the card's exact error; plus seconds per step and the card's peak vram_in_use while it ran. The pushed files stay on
the Mac (real models it can use again).
"""
import asyncio, glob, importlib.util, json, os, shutil, sys, tempfile, time, types, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "ComfyUI-MannyStudio")
PH3 = os.environ["PH3_DIR"]
W = {"label": "mac-vega-b", "host": "127.0.0.1", "port": 8189}
FOLDERS = ("diffusion_models", "text_encoders", "vae")
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-ph3-"))
for d in ("user/default/workflows", "input", "output", "temp"):
    os.makedirs(os.path.join(tmp, d))
for f in glob.glob(os.path.join(ROOT, "workflows", "*.app.json")):
    shutil.copy(f, os.path.join(tmp, "user/default/workflows"))
fp = types.ModuleType("folder_paths")
fp.get_user_directory = lambda: os.path.join(tmp, "user")
fp.get_input_directory = lambda: os.path.join(tmp, "input")
fp.get_output_directory = lambda: os.path.join(tmp, "output")
fp.get_temp_directory = lambda: os.path.join(tmp, "temp")
fp.models_dir = PH3
fp.folder_names_and_paths = {f: ([os.path.join(PH3, f)], {".safetensors"}) for f in FOLDERS}
fp.get_folder_paths = lambda k: list(fp.folder_names_and_paths.get(k, ([], None))[0])
fp.map_legacy = lambda k: k
fp.get_full_path = lambda f, n: (lambda p: p if os.path.isfile(p) else None)(os.path.join(PH3, f, n)) if f in fp.folder_names_and_paths else None
fp.get_filename_list = lambda f: sorted(os.listdir(os.path.join(PH3, f))) if f in fp.folder_names_and_paths else []
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


def vram_in_use():
    try:
        return json.load(urllib.request.urlopen(f"{remote.base(W)}/mac/info", timeout=5)).get("vram_in_use") or 0
    except Exception:
        return 0


async def main():
    remote.save_workers(studio._data_dir(), [{"label": "mac-vega-a", "host": "127.0.0.1", "port": 8188}, W])

    async def oi(force=False):
        return (await remote.caps(W, force=True))[0]
    studio._get_object_info = oi
    app = next(a for a in studio._list_apps() if os.path.basename(a).startswith("Manny Studio - Image"))
    d = studio._describe_app(app)
    model = next(i for i in d["inputs"] if i["kind"] == "choice" and any("P-H3" in o for o in i["options"]))
    vals = {model["key"]: next(o for o in model["options"] if "P-H3" in o)}
    _wf, api = await studio._build_prompt(app, vals)
    used = sorted({v for n in api.values() for v in (n.get("inputs") or {}).values() if isinstance(v, str) and "p-h3" in v.lower()})
    print("P-H3 job uses:", used)
    pushes, real_push = [], remote._push_file

    async def spy(w, folder, value, path, job):
        t = time.time()
        r = await real_push(w, folder, value, path, job)
        pushes.append((folder, value, os.path.getsize(path), round(time.time() - t)))
        return r
    remote._push_file = spy
    remote.FETCH_POLL = 2
    t0 = time.time()
    pid = await studio._queue_api(api, {"manny_app": app, "manny_label": "P-H3 live"}, False, W["label"], "studio-ph3")
    job = remote.JOBS[pid]
    peak, last, steps = 0, None, []
    while not job["task"].done():
        if job["status"] == "running":    # not while pushing: the card may still hold the last job's cache
            peak = max(peak, vram_in_use())
        s = (job["status"], job.get("step"))
        if s != last:
            print(f"  {time.time() - t0:7.0f}s {s[0]} {s[1] or ''}", flush=True)
            if s[1] and s[1].startswith("Step "):
                steps.append((time.time(), s[1]))
            last = s
        await asyncio.sleep(2)
    try:
        await job["task"]
    except Exception:
        pass
    per_step = None
    if len(steps) > 1:
        per_step = (steps[-1][0] - steps[0][0]) / max(1, len(steps) - 1)
    out = job["outputs"][0]["path"] if job.get("outputs") else ""
    size, lo, hi = 0, 0, 0
    if out and os.path.isfile(out):
        from PIL import Image
        im = Image.open(out)
        size = im.size
        lo, hi = im.convert("L").getextrema()
    check(bool(pushes) and all(f in FOLDERS for f, *_ in pushes), f"Studio pushed the P-H3 files the card lacked: {pushes}")
    ok = job["status"] == "done" and size and hi - lo > 10
    check(ok, f"P-H3 on {W['label']}: {job['status']} {job.get('error') or ''} | picture {size} | "
              f"{per_step and round(per_step, 1)} s/step | peak vram_in_use {peak / 1e9:.1f} GB | {time.time() - t0:.0f} s total")
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
