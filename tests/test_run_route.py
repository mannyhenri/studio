"""/manny/api/run with a target and /manny/api/workers, through the pack's real handlers (ComfyUI stubbed).

  WORKERS=127.0.0.1:8188,127.0.0.1:8189 ~/code/comfy-mac/.venv/bin/python tests/test_run_route.py

Studio's own ComfyUI is played by the first worker (object_info for conversion). For every app x every worker the
route must answer like the gate: 409 with the refusal list, or queue on that worker, finish, pull the files back
and tell the browser that queued it ("executed" + "execution_success" to its client_id). Unknown targets get 400.
"auto" over every app with this PC busy: eligible apps go to a worker, the rest get 409 naming every machine.
pick() unit cases: least busy wins, ties go to this PC, offline and ineligible workers are skipped.
A queued job that is cancelled must leave nothing on the worker (forget deletes its uploaded inputs).
"""
import asyncio, glob, importlib.util, json, os, shutil, sys, tempfile, types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "ComfyUI-MannyStudio")
WORKERS = [{"label": f"w{p}", "host": h, "port": int(p)} for h, p in
           (x.rsplit(":", 1) for x in os.environ.get("WORKERS", "127.0.0.1:8188,127.0.0.1:8189").split(","))]
# apps rendered to the end on every worker; other eligible apps are queued and then cancelled (LTX video is ~15 min a card)
RENDER = set(os.environ.get("RENDER_APPS", "Studio - Remove background.app.json,Studio - Smooth motion.app.json").split(","))
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


EVENTS = []


class _PS:
    instance = types.SimpleNamespace(routes=_Routes(), app=types.SimpleNamespace(middlewares=[]), port=WORKERS[0]["port"],
                                     loop=None, prompt_queue=None, send_sync=lambda ev, data, sid=None: EVENTS.append((ev, data, sid)))


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
    eligible = {}
    for w in WORKERS:
        oi, sv = await remote.caps(w)
        for app in apps:
            _wf, api = await studio._build_prompt(app, {})
            why = remote.gate(api, oi, sv)
            EVENTS.clear()
            r = await H[("POST", "/manny/api/run")](Req({"app": app, "target": w["label"], "client_id": "browser-1"}))
            d = json.loads(r.text)
            if why:
                check(r.status == 409 and len(d["refused"]) == len(why) and d["worker"] == w["label"],
                      f"{w['label']} {app}: 409 refused ({len(d.get('refused') or [])})")
                continue
            eligible.setdefault(app, []).append(w["label"])
            pid = (d.get("queued") or [{}])[0].get("prompt_id")
            check(r.status == 200 and d["queued"][0]["worker"] == w["label"], f"{w['label']} {app}: queued {pid}")
            if not pid:
                continue
            job = remote.JOBS[pid]
            if app not in RENDER:
                d = json.loads((await H[("POST", "/manny/api/remote_jobs/{pid}/cancel")](types.SimpleNamespace(match_info={"pid": pid}))).text)
                fg = d.get("forgot") or {}
                check(job["status"] == "cancelled" and "warning" not in fg and any(x.startswith("input/") for x in fg.get("deleted", [])) == bool(job["inputs"]),
                      f"{w['label']} {app}: eligible, not rendered here (RENDER_APPS); cancelled, forget {json.dumps(fg)[:200]}")
                continue
            await asyncio.wait_for(job["task"], 1800)
            files = [o["path"] for o in job["outputs"]]
            ex = [e for e in EVENTS if e[0] == "executed" and e[1]["prompt_id"] == pid]
            ok = (job["status"] == "done" and files and all(os.path.isfile(f) and f.startswith(os.path.join(tmp, "output") + os.sep) for f in files)
                  and len(ex) == len(files) and all(e[2] == "browser-1" and os.path.isfile(os.path.join(tmp, "output", e[1]["output"]["images"][0]["subfolder"], e[1]["output"]["images"][0]["filename"])) for e in ex)
                  and ("execution_success", {"prompt_id": pid, "worker": w["label"]}, "browser-1") in EVENTS
                  and not (job.get("forgot") or {}).get("warning"))
            check(ok, f"{w['label']} {app}: rendered, {len(files)} file(s) in this PC's output, browser told ({[e[0] for e in EVENTS]}), forgot {len((job.get('forgot') or {}).get('deleted') or [])} {job['error'] or ''}")

    # cancel: a job still waiting in the worker's queue (the worker is kept busy by a second job ahead of it)
    if eligible.keys() & RENDER:
        app = sorted(eligible.keys() & RENDER)[0]
        w = remote.find(studio._data_dir(), eligible[app][0])
        a = json.loads((await H[("POST", "/manny/api/run")](Req({"app": app, "target": w["label"], "client_id": "browser-1"}))).text)["queued"][0]["prompt_id"]
        b = json.loads((await H[("POST", "/manny/api/run")](Req({"app": app, "target": w["label"], "client_id": "browser-1"}))).text)["queued"][0]["prompt_id"]
        r = await H[("POST", "/manny/api/remote_jobs/{pid}/cancel")](types.SimpleNamespace(match_info={"pid": b}))
        d = json.loads(r.text)
        fg = d.get("forgot") or {}
        check(r.status == 200 and remote.JOBS[b]["status"] == "cancelled" and any(x.startswith("input/") for x in fg.get("deleted", [])) and "warning" not in fg,
              f"cancel a waiting job on {w['label']}: {remote.JOBS[b]['status']}, forget {json.dumps(fg)[:220]}")
        await asyncio.wait_for(remote.JOBS[a]["task"], 1800)
        check(remote.JOBS[a]["status"] == "done", f"the job ahead of it still finished: {remote.JOBS[a]['status']}")
        lst = json.loads((await H[("GET", "/manny/api/remote_jobs")](Req())).text)
        check({a, b} <= {j["prompt_id"] for j in lst} and all("task" not in j for j in lst), f"/manny/api/remote_jobs lists them ({len(lst)})")

    # the other render paths share _queue_api: Finish (_queue_app + _wait_output) and the Music/Voice routes
    if eligible.keys() & RENDER:
        app = sorted(eligible.keys() & RENDER)[0]
        desc = studio._describe_app(app)
        for w in WORKERS:
            pid = await studio._queue_app(desc, {}, False, "Finish test", w["label"])
            fn, sub = await asyncio.wait_for(studio._wait_output(pid, {}), 1800)
            check(remote.JOBS[pid]["worker"] == w["label"] and os.path.isfile(os.path.join(tmp, "output", sub, fn)),
                  f"Finish path (_queue_app -> _wait_output) on {w['label']}: {sub}/{fn} is on this PC")
        job = {"cancel": True}
        pid = await studio._queue_app(desc, {}, False, "Finish stop", WORKERS[-1]["label"])
        try:
            await studio._wait_output(pid, job)
            check(False, "Stop during a remote Finish step must raise")
        except RuntimeError as e:
            fg = remote.JOBS[pid].get("forgot") or {}
            check(str(e) == "Stopped" and remote.JOBS[pid]["status"] == "cancelled" and "warning" not in fg,
                  f"Stop during a remote Finish step: {e}, job {remote.JOBS[pid]['status']}, forget {json.dumps(fg)[:160]}")
    for kind, body in (("music", {"tags": "calm piano", "instrumental": True, "seconds": 15}), ("voice", {"text": "Hello there."})):
        for w in WORKERS:
            r = await H[("POST", f"/manny/api/{kind}")](Req(dict(body, target=w["label"])))
            job = studio._jobs[json.loads(r.text)["id"]]
            for _ in range(120):
                if job["status"] != "running":
                    break
                await asyncio.sleep(0.5)
            oi, sv = await remote.caps(w)
            sapi = (studio._sound.music_prompt if kind == "music" else studio._sound.voice_prompt)(body)[0] if hasattr(studio._sound, "music_prompt") else None
            expect_ok = sapi is not None and not remote.gate(sapi, oi, sv)
            check((job["status"] == "failed" and "can't run" in (job.get("error") or "")) if not expect_ok else job["status"] != "failed",
                  f"{kind} route with target {w['label']}: {job['status']} {(job.get('error') or '')[:160]}")

    # auto, with this PC busy: every app goes to an eligible worker or is refused naming every machine
    studio._local_busy = lambda: 99
    for app in apps:
        r = await H[("POST", "/manny/api/run")](Req({"app": app, "target": "auto", "client_id": "browser-1"}))
        d = json.loads(r.text)
        if app in eligible:
            q = (d.get("queued") or [{}])[0]
            check(r.status == 200 and q.get("worker") in eligible[app], f"auto {app}: went to {q.get('worker')} (eligible: {eligible[app]})")
            if q.get("prompt_id"):
                await remote.cancel(remote.find(studio._data_dir(), q["worker"]), q["prompt_id"])
        else:
            check(r.status == 409 and set(d.get("refused_by") or {}) == {"local"} | {w["label"] for w in WORKERS},
                  f"auto {app}: 409, refused by {sorted(d.get('refused_by') or {})}")
    studio._local_busy = lambda: 0
    import aiohttp
    staged = []     # "this PC" is worker 1 here, and its input folder doesn't have the apps' default files: upload them
    async with aiohttp.ClientSession() as s:
        for app in sorted(eligible):
            _wf, api = await studio._build_prompt(app, {})
            oi1, _sv = await remote.caps(WORKERS[0])
            api2 = json.loads(json.dumps(api))
            for node in api2.values():
                for k, v in list(node.get("inputs", {}).items()):
                    if isinstance(v, str) and remote.is_file_input(remote._spec(oi1.get(node["class_type"]), k)):
                        rel = remote._split_annot(v)[0]
                        fd = aiohttp.FormData()
                        fd.add_field("image", open(os.path.join(tmp, "input", rel), "rb"), filename=os.path.basename(rel))
                        fd.add_field("subfolder", os.path.dirname(rel)); fd.add_field("type", "input"); fd.add_field("overwrite", "true")
                        async with s.post(remote.base(WORKERS[0]) + "/upload/image", data=fd) as r:
                            staged.append(rel)
    for app in sorted(eligible):
        r = await H[("POST", "/manny/api/run")](Req({"app": app, "target": "auto", "client_id": "browser-1"}))
        q = json.loads(r.text)["queued"][0]
        check(q["worker"] == "local", f"auto {app} with this PC idle and able: stays local ({q['worker']})")
        await post_local_cancel(q["prompt_id"])
    async with aiohttp.ClientSession() as s:
        await remote.forget(s, WORKERS[0], "studio-route-staged", sorted(set(staged)))
    await pick_units()
    mcp_units()
    r = await H[("POST", "/manny/api/workers/check")](Req({"app": apps[0]}))
    d = json.loads(r.text)
    check(set(d) == {"local"} | {w["label"] for w in WORKERS}, f"workers/check answers per machine: { {k: v.get('ok') for k, v in d.items()} }")
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


async def post_local_cancel(pid):
    """'local' here is worker 1's ComfyUI: take the job straight back out."""
    import aiohttp
    async with aiohttp.ClientSession() as s:
        base = remote.base(WORKERS[0])
        async with s.post(base + "/queue", json={"delete": [pid]}):
            pass
        async with s.post(base + "/interrupt", json={"prompt_id": pid}):
            pass


def mcp_units():
    """studio_wait (MCP) follows jobs on other machines too: their state and files come from /manny/api/remote_jobs."""
    sys.path.insert(0, PKG)
    import studio_mcp
    jobs = [{"prompt_id": "r1", "worker": "w", "status": "done", "outputs": [{"filename": "a.png", "subfolder": "Images"}]},
            {"prompt_id": "r2", "worker": "w", "status": "done", "outputs": [{"filename": "b.png", "subfolder": ".private_incoming/Images"}]},
            {"prompt_id": "r3", "worker": "w", "status": "running", "outputs": []},
            {"prompt_id": "r4", "worker": "w", "status": "failed", "error": "OOM", "outputs": []}]
    studio_mcp.http = lambda m, path, body=None, timeout=60: jobs if path == "/manny/api/remote_jobs" else {}
    got = {p: studio_mcp.outputs_of(p) for p in ("r1", "r2", "r3", "r4", "nope")}
    check(got == {"r1": ("done", [{"filename": "a.png", "subfolder": "Images", "private": False}]),
                  "r2": ("done", [{"filename": "b.png", "subfolder": "Images", "private": True}]),
                  "r3": ("running", []), "r4": ("error", "w: OOM"), "nope": (None, None)}, f"MCP outputs_of for remote jobs: {got}")


async def pick_units():
    real_check, real_info = remote.check, remote.info
    ws = [{"label": "a"}, {"label": "b"}, {"label": "off"}, {"label": "cant"}]
    busy = {"a": 3, "b": 1}

    async def fake_check(w, api):
        if w["label"] == "off":
            raise OSError("connection refused")
        return [{"reason": "no model"}] if w["label"] == "cant" else []

    async def fake_info(w):
        return {"queue_remaining": busy[w["label"]]}
    remote.check, remote.info = fake_check, fake_info
    try:
        lab, notes = await remote.pick(ws, {}, 2)
        check(lab == "b" and set(notes) == {"off", "cant"}, f"pick: least busy wins (b=1 < local=2 < a=3) -> {lab}, skipped {sorted(notes)}")
        lab, _ = await remote.pick(ws, {}, 1)
        check(lab == "local", f"pick: a tie goes to this PC -> {lab}")
        lab, _ = await remote.pick(ws, {}, 0, local_ok=False)
        check(lab == "b", f"pick: this PC can't run it -> {lab}")
        lab, notes = await remote.pick(ws[2:], {}, 0, local_ok=False)
        check(lab is None and set(notes) == {"off", "cant"}, f"pick: nobody can -> {lab}")
    finally:
        remote.check, remote.info = real_check, real_info


sys.exit(asyncio.run(main()))
