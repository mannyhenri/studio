"""/manny/api/run with a target and /manny/api/workers, through the pack's real handlers (ComfyUI stubbed).

  WORKERS=127.0.0.1:8188,127.0.0.1:8189 ~/code/comfy-mac/.venv/bin/python tests/test_run_route.py

Studio's own ComfyUI is played by the first worker (object_info for conversion). For every app x every worker the
route must answer like the gate: 409 with the refusal list, or queue on that worker. Unknown targets get 400.
"""
import asyncio, glob, importlib.util, json, os, shutil, sys, tempfile, types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "ComfyUI-MannyStudio")
WORKERS = [{"label": f"w{p}", "host": h, "port": int(p)} for h, p in
           (x.rsplit(":", 1) for x in os.environ.get("WORKERS", "127.0.0.1:8188,127.0.0.1:8189").split(","))]
tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-route-"))
for d in ("user/default/workflows", "input", "output", "temp"):
    os.makedirs(os.path.join(tmp, d))
for f in glob.glob(os.path.join(ROOT, "workflows", "*.app.json")):
    shutil.copy(f, os.path.join(tmp, "user/default/workflows"))

# --- stub the bits of ComfyUI the pack imports
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


class _Routes:
    def __init__(self):
        self.h = {}

    def _reg(self, m):
        def deco(path):
            def wrap(fn):
                self.h[(m, path)] = fn
                return fn
            return wrap
        return deco

    def __getattr__(self, m):
        if m in ("get", "post", "delete", "put"):
            return self._reg(m.upper())
        raise AttributeError(m)


class _PS:
    instance = types.SimpleNamespace(routes=_Routes(), app=types.SimpleNamespace(middlewares=[]), port=WORKERS[0]["port"],
                                     loop=None, prompt_queue=None, send_sync=lambda *a, **k: None)


srv = types.ModuleType("server")
srv.PromptServer = _PS
sys.modules["server"] = srv
spec = importlib.util.spec_from_file_location("studio", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
studio = importlib.util.module_from_spec(spec)
sys.modules["studio"] = studio
spec.loader.exec_module(studio)
H = _PS.instance.routes.h
remote = sys.modules["studio.remote"]
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


class Req:
    def __init__(self, body=None):
        self.body, self.query, self.match_info, self.remote, self.headers, self.cookies = body, {}, {}, "127.0.0.1", {}, {}

    async def json(self):
        return self.body


probes = os.path.realpath(tempfile.mkdtemp(prefix="studio-probes-"))


async def main():
    from probes import make_inputs
    make_inputs({"input": os.path.join(probes, "input")})
    remote.save_workers(studio._data_dir(), WORKERS)
    r = await H[("GET", "/manny/api/workers")](Req())
    ws = json.loads(r.text)
    check([w["label"] for w in ws] == ["local"] + [w["label"] for w in WORKERS] and all(w["online"] for w in ws),
          f"GET /manny/api/workers lists local + {len(WORKERS)} workers, all online: {[(w['label'], w['online'], (w.get('info') or {}).get('queue_remaining')) for w in ws]}")
    r = await H[("POST", "/manny/api/run")](Req({"app": "Studio - Upscale.app.json", "target": "nope"}))
    check(r.status == 400, f"unknown target -> {r.status}")
    apps = studio._list_apps()
    check(len(apps) == 9, f"{len(apps)} apps")
    for app in apps:    # the apps' default file inputs, as real files on "this PC"
        for i in studio._describe_app(app)["inputs"]:
            if i["kind"] in ("image", "mask", "video", "audio") and i["value"]:
                src = {"video": "probe.mp4", "audio": "probe.wav"}.get(i["kind"], "probe.png")
                dst = os.path.join(tmp, "input", i["value"])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(os.path.join(probes, "input", "studio_test", src), dst)
    for w in WORKERS:
        oi, sv = await remote.caps(w)
        for app in apps:
            _wf, api = await studio._build_prompt(app, {})
            why = remote.gate(api, oi, sv)
            r = await H[("POST", "/manny/api/run")](Req({"app": app, "target": w["label"]}))
            d = json.loads(r.text)
            if why:
                check(r.status == 409 and len(d["refused"]) == len(why) and d["worker"] == w["label"],
                      f"{w['label']} {app}: 409 refused ({len(d.get('refused') or [])})")
            else:
                pid = (d.get("queued") or [{}])[0].get("prompt_id")
                check(r.status == 200 and d["queued"][0]["worker"] == w["label"], f"{w['label']} {app}: queued {pid}")
                if pid:
                    await remote.cancel(w, pid)
    r = await H[("POST", "/manny/api/workers/check")](Req({"app": apps[0]}))
    d = json.loads(r.text)
    check(set(d) == {"local"} | {w["label"] for w in WORKERS}, f"workers/check answers per machine: { {k: v.get('ok') for k, v in d.items()} }")
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
