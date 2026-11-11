"""Every job Studio sends to another machine says it's a Studio job: extra_data.manny_app.

comfy-mac's /mac/forget clears Studio jobs a restarted dispatcher lost track of (stale_jobs), and it recognises
them by extra_data.manny_app. This drives EVERY path that reaches remote.submit (found in the code; a new call
site fails the test until it's covered here) with remote.submit stubbed, and checks what each one would send.

  ~/code/comfy-mac/.venv/bin/python tests/test_marker.py
Needs one running ComfyUI for /object_info (OBJECT_INFO, default http://127.0.0.1:8189/object_info) to convert the
apps; nothing is queued anywhere (remote.submit is stubbed).
"""
import asyncio, glob, importlib.util, json, os, re, shutil, sys, tempfile, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "ComfyUI-MannyStudio")
tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-marker-"))
for d in ("user/default/workflows", "input", "output", "temp"):
    os.makedirs(os.path.join(tmp, d))
for f in glob.glob(os.path.join(ROOT, "workflows", "*.app.json")):
    shutil.copy(f, os.path.join(tmp, "user/default/workflows"))

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
ROUTES = {}


class _R:
    def __getattr__(self, m):
        def deco(path):
            def wrap(fn):
                ROUTES[(m.upper(), path)] = fn
                return fn
            return wrap
        return deco


srv = types.ModuleType("server")
srv.PromptServer = type("PS", (), {"instance": types.SimpleNamespace(routes=_R(), app=types.SimpleNamespace(middlewares=[]), port=1,
                                                                   loop=None, prompt_queue=None, send_sync=lambda *a, **k: None)})
sys.modules["server"] = srv
spec = importlib.util.spec_from_file_location("studio", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
studio = importlib.util.module_from_spec(spec)
sys.modules["studio"] = studio
spec.loader.exec_module(studio)
remote = sys.modules["studio.remote"]
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


class Req:
    def __init__(self, body):
        self.body, self.query, self.match_info, self.remote, self.headers, self.cookies = body, {}, {}, "127.0.0.1", {}, {}

    async def json(self):
        return self.body


async def main():
    # the call sites that can reach another machine, straight from the code
    sites = []
    for fn in sorted(glob.glob(os.path.join(PKG, "*.py"))):
        for k, line in enumerate(open(fn, encoding="utf-8"), 1):
            if re.search(r"(_remote\.submit|_queue_api\"?\]?)\(", line) and not re.match(r"\s*(async )?def ", line):
                sites.append(f"{os.path.basename(fn)}:{k}")
    covered = {"manny_run": "__init__.py", "_queue_api": "__init__.py", "_queue_app": "__init__.py", "sound": "sound.py"}
    check(len(sites) == len(covered), f"remote call sites in the code: {sites} (this test drives {sorted(covered)})")

    remote.save_workers(studio._data_dir(), [{"label": "mac", "host": "127.0.0.1", "port": 1}])
    sent = []

    async def fake_submit(w, api, payload_extra, dirs, private=False, incoming=None, on_done=None, **kw):
        sent.append(payload_extra.get("extra_data") or {})
        pid = f"m{len(sent)}"
        remote.JOBS[pid] = {"prompt_id": pid, "worker": w["label"], "status": "done", "outputs": [], "queued_at": 0,
                            "task": asyncio.ensure_future(asyncio.sleep(0))}
        return {"prompt_id": pid, "worker": w["label"]}
    remote.submit = fake_submit

    async def fake_oi(force=False):     # the pack converts app workflows with ComfyUI's object_info
        import urllib.request
        return json.load(urllib.request.urlopen(os.environ.get("OBJECT_INFO", "http://127.0.0.1:8189/object_info")))
    studio._get_object_info = fake_oi

    paths = {}
    app = next(a for a in studio._list_apps() if "Remove background" in a)
    n = len(sent)
    await ROUTES[("POST", "/manny/api/run")](Req({"app": app, "target": "mac"}))
    paths["manny_run"] = sent[n:]
    n = len(sent)
    await studio._queue_app(studio._describe_app(app), {}, False, "Finish: test", "mac")
    paths["_queue_app"] = sent[n:]
    for kind, body in (("music", {"tags": "calm", "instrumental": True, "seconds": 15}), ("voice", {"text": "Hi."})):
        n = len(sent)
        r = await ROUTES[("POST", f"/manny/api/{kind}")](Req(dict(body, target="mac")))
        job = studio._jobs[json.loads(r.text)["id"]]
        for _ in range(100):
            if len(sent) > n or job["status"] != "running":
                break
            await asyncio.sleep(0.05)
        paths[f"sound:{kind}"] = sent[n:]
    for name, ex in paths.items():
        check(len(ex) == 1 and isinstance(ex[0].get("manny_app"), str) and ex[0]["manny_app"],
              f"{name}: extra_data.manny_app = {ex[0].get('manny_app') if ex else None!r}")
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
