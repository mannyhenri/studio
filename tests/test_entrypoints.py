"""Every way Studio starts a render honours the GPU it was asked to use.

The entry points are DERIVED FROM THE CODE: every call that can hand a job to another machine (_remote.submit,
_queue_api) and every caller of /manny/api/run (scheduled renders, Claude over MCP). The test drives each one for
real through Studio's own handlers, served by a real aiohttp server (scheduled renders and MCP talk HTTP), with
remote.submit wrapped to record the worker each job reaches. Each must reach exactly the worker it was asked for,
for both workers. AI review / AI prompt help post straight to this PC's ComfyUI: the one PC-only path, asserted.

  ~/code/comfy-mac/.venv/bin/python tests/test_entrypoints.py              stubbed: nothing is queued
  LIVE=1 ~/code/comfy-mac/.venv/bin/python tests/test_entrypoints.py       real jobs on both Mac workers
  ~/code/comfy-mac/.venv/bin/python tests/test_entrypoints.py --mutants    drop the target on each path in
                                                                           turn: exactly that path must fail
  FETCH=1 ~/code/comfy-mac/.venv/bin/python tests/test_entrypoints.py      a Mac missing the model: every entry point
                                                                           x {present, downloading, failed, unavailable}
  ~/code/comfy-mac/.venv/bin/python tests/test_entrypoints.py --fetch-mutants   drop the download on each path in turn
Needs the workers' /object_info (127.0.0.1:8188 and :8189) to convert apps, and ffmpeg for Finish.
"""
import asyncio, glob, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.environ.get("STUDIO_PKG") or os.path.join(ROOT, "ComfyUI-MannyStudio")
LIVE = os.environ.get("LIVE") == "1"
FETCH = os.environ.get("FETCH") == "1"
WORKERS = [{"label": "mac-vega-a", "host": "127.0.0.1", "port": 8188}, {"label": "mac-vega-b", "host": "127.0.0.1", "port": 8189}]
APP = "Studio - Remove background.app.json"
MEMBERS = ["manny_run", "finish", "sound:music", "sound:voice", "schedule", "mcp", "keyframes", "paint3d", "mcp:keyframes"]
if os.environ.get("MEMBERS"):      # LIVE=1 MEMBERS=keyframes,paint3d: only those paths
    MEMBERS = [m for m in MEMBERS if m in os.environ["MEMBERS"].split(",")]

# the mutations used by --mutants: (member, file, original, replacement) — each removes that path's target.
# Scheduled renders and MCP go through /manny/api/run, so breaking manny_run must break them too.
VIA_RUN = {"manny_run": {"manny_run", "schedule", "mcp"}, "keyframes": {"keyframes", "mcp:keyframes"}}   # MCP keyframes calls the keyframes route
MUTANTS = [
    ("manny_run", "__init__.py", 'target = body.get("target") or _remote.LOCAL', "target = _remote.LOCAL"),
    ("finish", "__init__.py", '"Finish: smooth", b.get("target"))', '"Finish: smooth", None)'),
    ("sound:music", "sound.py", "pid = await _queue(api, private, label, b.get(\"target\"))",
     "pid = await _queue(api, private, label, b.get(\"target\") if kind != \"music\" else None)"),
    ("sound:voice", "sound.py", "pid = await _queue(api, private, label, b.get(\"target\"))",
     "pid = await _queue(api, private, label, b.get(\"target\") if kind != \"voice\" else None)"),
    ("schedule", "schedule.py", 'body["client_id"] = "studio-schedule"', 'body["client_id"] = "studio-schedule"; body.pop("target", None)'),
    ("mcp", "studio_mcp.py", '"private": private, "target": a.get("run_on") or "local"}', '"private": private, "target": "local"}'),
    ("keyframes", "projects.py", 'f"Project keyframe {k + 1}", b.get("target"))', 'f"Project keyframe {k + 1}", None)'),
    ("paint3d", "previs.py", '"3D frame", b.get("target"))', '"3D frame", None)'),
    ("mcp:keyframes", "studio_mcp.py", '"redo": a.get("redo"), "target": a.get("run_on") or "local"}', '"redo": a.get("redo"), "target": "local"}'),
]
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


def _calls(path):
    """(call node, enclosing function name) for every call in a Python file."""
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []

    decorators = {id(d) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for d in n.decorator_list}

    def walk(node, fn):
        for ch in ast.iter_child_nodes(node):
            name = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(ch, ast.Call) and id(ch) not in decorators:   # @routes.post("/manny/api/run") registers, it doesn't call
                out.append((ch, fn))
            walk(ch, name)
    walk(tree, "<module>")
    return out


def _text(node):
    """The literal text of a str / f-string argument ('' otherwise)."""
    import ast
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant))
    return ""


def derive_entry_points():
    """From the code, by ENCLOSING FUNCTION (not line numbers): {(file, function)} of
    sub   - calls that can hand a job to another machine (_remote.submit, _queue_api, _queue_app; by name or ns[...]),
    run   - callers of /manny/api/run (they re-enter manny_run),
    local - posts straight to this PC's ComfyUI /prompt."""
    import ast
    sub, run, local = set(), set(), set()
    for fn in sorted(glob.glob(os.path.join(PKG, "*.py"))):
        f = os.path.basename(fn)
        for call, enc in _calls(fn):
            c = call.func
            if (isinstance(c, ast.Attribute) and c.attr == "submit" and isinstance(c.value, ast.Name) and c.value.id == "_remote") \
                    or (isinstance(c, ast.Name) and c.id in ("_queue_api", "_queue_app")) \
                    or (isinstance(c, ast.Subscript) and _text(c.slice) in ("_queue_api", "_queue_app")):
                sub.add((f, enc))
            texts = [_text(a) for a in call.args]
            if any("/manny/api/run" in t for t in texts) and (isinstance(c, ast.Attribute) and c.attr == "post" or isinstance(c, ast.Name) and c.id == "http"):
                run.add((f, enc))
            if isinstance(c, ast.Attribute) and c.attr == "post" and any("127.0.0.1" in t and t.endswith("/prompt") for t in texts):
                local.add((f, enc))
    return sub, run, local


# what each derived site is: the members driven below, plus routing internals and the PC-only AI path
SITES = {
    "sub": {("__init__.py", "manny_run"): "manny_run", ("__init__.py", "_queue_app"): "finish", ("__init__.py", "_finish"): "finish",
            ("projects.py", "run"): "keyframes + mcp:keyframes", ("previs.py", "run"): "paint3d",
            ("__init__.py", "_queue_api"): "(routing: Finish, music, voice)", ("sound.py", "_queue"): "sound:music + sound:voice"},
    "run": {("schedule.py", "_fire"): "schedule", ("studio_mcp.py", "call"): "mcp"},
    "local": {("__init__.py", "manny_run"): "(manny_run: this PC)", ("__init__.py", "_queue_api"): "(_queue_api: this PC)",
              ("__init__.py", "_llm"): "AI review / AI prompt help: PC-only",
              ("videotools.py", "_queue"): "Video fixes / Continue / Keyframe shot: PC-only (no Run on yet)"},
}
WEB = os.path.join(os.path.dirname(PKG), "ComfyUI-MannyStudio", "web", "app.js") if os.environ.get("STUDIO_PKG") else os.path.join(PKG, "web", "app.js")
RENDER_ROUTES = r'"/manny/api/(run|finish|music|voice|schedule|previs/style)"|"/manny/api/" \+ kind|`/manny/api/projects/\$\{pr\.id\}/keyframes`'


def web_sites(src):
    """Every browser call that starts a render: (line, route, sends a target?). A call passes when its body has
    target: or is runBody()'s body (runBody itself must set target)."""
    out, lines = [], src.split("\n")
    for k, line in enumerate(lines, 1):
        for m in re.finditer(r"post\((" + RENDER_ROUTES + r")", line):
            depth, j, arg = 0, m.start() + 4, ""
            rest = "\n".join(lines[k - 1:k + 3])[m.start() + 4:]
            for ch in rest:
                depth += ch == "("
                depth -= ch == ")"
                arg += ch
                if depth == 0:
                    break
            prior = "\n".join(lines[max(0, k - 4):k])
            via_runbody = bool(re.search(r"\bbody\b", arg)) and "runBody(app)" in prior
            out.append((k, m.group(1), "target:" in arg or via_runbody))
    rb = re.search(r"function runBody\(app\) \{.*?\n\}", src, re.S)
    out.append(("runBody", "(Generate, Later)", bool(rb) and "target: runTarget()" in rb.group(0)))
    return out


async def main():
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-entry-"))
    for d in ("user/default/workflows", "input", "output/et", "temp"):
        os.makedirs(os.path.join(tmp, d))
    for f in glob.glob(os.path.join(ROOT, "workflows", "*.app.json")):
        shutil.copy(f, os.path.join(tmp, "user/default/workflows"))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from probes import make_inputs
    make_inputs({"input": os.path.join(tmp, "input")})
    shutil.copy(os.path.join(tmp, "input/studio_test/probe.png"), os.path.join(tmp, "input/ref_couture_model.png"))
    # not under output/Videos: Studio's first start moves Images/Videos into the Incognito space
    shutil.copy(os.path.join(tmp, "input/studio_test/probe.mp4"), os.path.join(tmp, "output/et/clip.mp4"))

    # ComfyUI stand-ins; the pack's routes are collected and served by a real aiohttp server below
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
    routes = []

    class _R:
        def __getattr__(self, m):
            def deco(path):
                def wrap(fn):
                    routes.append((m.upper(), path, fn))
                    return fn
                return wrap
            return deco
    ps = types.SimpleNamespace(routes=_R(), app=types.SimpleNamespace(middlewares=[]), port=0, loop=None,
                               prompt_queue=None, send_sync=lambda *a, **k: None)
    srv = types.ModuleType("server")
    srv.PromptServer = type("PS", (), {"instance": ps})
    sys.modules["server"] = srv
    spec = importlib.util.spec_from_file_location("studio", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
    studio = importlib.util.module_from_spec(spec)
    sys.modules["studio"] = studio
    spec.loader.exec_module(studio)
    remote = sys.modules["studio.remote"]

    from aiohttp import web
    app = web.Application()
    for m, path, fn in routes:
        app.router.add_route(m, path, fn)
    local_posts = []

    async def pc_prompt(request):       # this PC's own ComfyUI, as far as the test needs it
        local_posts.append(await request.json())
        return web.json_response({"error": "stand-in"}, status=400)
    app.router.add_route("POST", "/prompt", pc_prompt)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    ps.port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{ps.port}"

    async def oi(force=False):      # this "PC" converts apps with a worker's node list (both workers have the same)
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(remote.base(WORKERS[1]) + "/object_info") as r:
                return await r.json()
    studio._get_object_info = oi
    studio._llm_model = lambda: "stand-in-llm.safetensors"   # the AI paths need a model name to build their prompt
    remote.save_workers(studio._data_dir(), WORKERS)

    # routing instrumented: record the worker every job reaches; LIVE also really sends it
    reached, real_submit = [], remote.submit

    async def spy(w, api, payload_extra, dirs, private=False, incoming=".private_incoming", on_done=None, **kw):
        reached.append({"worker": w["label"], "client_id": payload_extra.get("client_id"), "fetch": kw.get("fetch"),
                        "label": (payload_extra.get("extra_data") or {}).get("manny_label"), "at": time.time()})
        if LIVE or FETCH:
            return await real_submit(w, api, payload_extra, dirs, private, incoming, on_done, **kw)
        pid = f"stub{len(reached)}"
        remote.JOBS[pid] = {"prompt_id": pid, "worker": w["label"], "status": "failed", "error": "stub", "outputs": [],
                            "queued_at": time.time(), "task": asyncio.ensure_future(asyncio.sleep(0))}
        return {"prompt_id": pid, "worker": w["label"]}
    remote.submit = spy

    # ---- the set, from the code (by enclosing function)
    sub, run, local = derive_entry_points()
    for kind, found in (("sub", sub), ("run", run), ("local", local)):
        for site in sorted(found):
            print(f"  {kind}: {site[0]}:{site[1]} -> {SITES[kind].get(site, 'NOT MAPPED')}")
        check(found == set(SITES[kind]), f"{kind} sites in the code == the mapped set ({len(found)}): unmapped {sorted(found - set(SITES[kind]))}, gone {sorted(set(SITES[kind]) - found)}")
    kinds = re.findall(r'_start\("(\w+)"', open(os.path.join(PKG, "sound.py"), encoding="utf-8").read())
    check(sorted(f"sound:{k}" for k in kinds) == ["sound:music", "sound:voice"], f"sound routes in the code: {kinds}")
    # the browser: every call that starts a render must send the Run-on target
    ws = web_sites(open(WEB, encoding="utf-8").read())
    for line, route, ok in ws:
        check(ok, f"web app.js:{line} {route} sends the Run-on target")
    check(len(ws) >= 9, f"browser render calls found: {len(ws) - 1} + runBody")

    import aiohttp

    said = []      # every reply an entry point gave (FETCH: where a refusal's reason must show)

    async def post(s, path, body):
        async with s.post(base + path, json=body) as r:
            txt = await r.text()
            said.append(txt)
            try:
                return r.status, json.loads(txt)
            except ValueError:
                print(f"  {path} -> HTTP {r.status}: {txt[:200]}")
                return r.status, {}

    async def drive(member, label):
        """Start a render through that entry point with target=label; returns the workers it reached."""
        n = len(reached)
        async with aiohttp.ClientSession() as s:
            if member == "manny_run":
                await post(s, "/manny/api/run", {"app": APP, "values": {}, "target": label, "client_id": f"et-run-{label}"})
            elif member == "finish":
                st, j = await post(s, "/manny/api/finish", {"filename": "clip.mp4", "subfolder": "et", "fps": "48", "engine": "comfy",
                                                            "upscale": False, "sound": "none", "name": "et", "target": label})
                for _ in range(1200 if LIVE else 100):
                    if len(reached) > n:
                        break
                    await asyncio.sleep(0.1)
            elif member.startswith("sound:"):
                kind = member.split(":")[1]
                body = {"tags": "calm", "instrumental": True, "seconds": 15} if kind == "music" else {"text": "Hello."}
                await post(s, f"/manny/api/{kind}", dict(body, target=label))
                for _ in range(100):
                    if len(reached) > n:
                        break
                    await asyncio.sleep(0.05)
            elif member == "schedule":
                st, it = await post(s, "/manny/api/schedule", {"run": {"app": APP, "values": {}, "target": label}, "when": "at",
                                                               "at": time.time() + 3600, "kind": "image", "label": "et"})
                await post(s, f"/manny/api/schedule/{it['id']}/now", {})
            elif member in ("keyframes", "mcp:keyframes"):
                st, pr = await post(s, "/manny/api/projects", {"title": f"et {member} {label}", "idea": "test", "shots": [{"id": "s1", "title": "Shot 1", "action": "a red apple"}]})
                if member == "keyframes":
                    await post(s, f"/manny/api/projects/{pr['id']}/keyframes", {"private": False, "shots": [{"id": "s1", "prompt": "a red apple", "ref": ""}], "target": label})
                else:
                    os.environ["STUDIO_URL"] = base
                    sys.path.insert(0, PKG)
                    import studio_mcp
                    studio_mcp.BASE = base
                    try:
                        said.append(str(await asyncio.to_thread(studio_mcp.call, "studio_project_keyframes", {"id": pr["id"], "run_on": label})))
                    except Exception as e:
                        said.append(str(e))
                for _ in range(1200 if LIVE else 100):
                    if len(reached) > n:
                        break
                    await asyncio.sleep(0.1)
            elif member == "paint3d":
                await post(s, "/manny/api/previs/style", {"still": "ref_couture_model.png", "prompt": "a film frame", "model": "flux 3", "target": label})
                for _ in range(1200 if LIVE else 100):
                    if len(reached) > n:
                        break
                    await asyncio.sleep(0.1)
            elif member == "mcp":
                os.environ["STUDIO_URL"] = base
                sys.path.insert(0, PKG)
                import studio_mcp
                studio_mcp.BASE = base
                try:
                    said.append(str(await asyncio.to_thread(studio_mcp.call, "studio_generate",
                                                            {"app": "Remove background", "images": ["ref_couture_model.png"], "run_on": label, "wait": False})))
                except Exception as e:
                    said.append(str(e))
        return [r["worker"] for r in reached[n:]]

    if FETCH:
        await fetch_matrix(studio, remote, drive, reached, said, local_posts, base)
        await runner.cleanup()
        print(f"\n{len(fails)} failed" if fails else "\nall passed")
        return 1 if fails else 0
    for member in MEMBERS:
        for w in WORKERS:
            try:
                got = await drive(member, w["label"])
            except Exception as e:      # a path that blows up hasn't honoured the target either
                got = [f"error: {str(e)[:120]}"]
            check(got == [w["label"]], f"{member} with target {w['label']}: reached {got}")
    # PC-only: drive AI review and AI prompt help with a Mac target; they must post to this PC and reach no Mac
    n, lp = len(reached), len(local_posts)
    async with aiohttp.ClientSession() as s:
        for path, body in (("/manny/api/ai/review", {"filename": "clip.mp4", "subfolder": "et"}),
                           ("/manny/api/ai/prompt", {"idea": "a red apple", "kind": "image"})):
            st, j = await post(s, path, dict(body, target="mac-vega-a"))
            for _ in range(200):
                if len(local_posts) > lp:
                    break
                await asyncio.sleep(0.1)
            if len(local_posts) <= lp:
                print("  AI jobs:", [(x.get("kind"), x.get("status"), (x.get("error") or "")[:160]) for x in studio._jobs.values() if "ai" in str(x.get("kind", "")) or "review" in str(x) or "Writing" in str(x.get("stage"))][-2:])
            check(len(local_posts) > lp and all((p.get("extra_data") or {}).get("manny_label") == "AI" for p in local_posts[lp:]),
                  f"{path}: posted to this PC's ComfyUI ({len(local_posts) - lp} prompt)")
            lp = len(local_posts)
    check(len(reached) == n, f"AI review / AI prompt help reached no Mac ({len(reached) - n})")

    if LIVE:    # wait for the real jobs, so each lands, comes back and is forgotten
        for pid, j in list(remote.JOBS.items()):
            if j.get("task"):
                try:
                    await asyncio.wait_for(j["task"], 1800)
                except Exception:
                    pass
        for _ in range(1800):      # Studio's own jobs (keyframes, 3D paint) may still be asking a card
            if not any(j.get("status") == "running" for j in studio._jobs.values()):
                break
            await asyncio.sleep(1)
        for pid, j in list(remote.JOBS.items()):
            if j.get("task"):
                try:
                    await asyncio.wait_for(j["task"], 1800)
                except Exception:
                    pass
        jobs = [j for j in remote.JOBS.values()]
        print("live jobs:", sorted((j["worker"], j["status"], j.get("label"), (j.get("error") or "")[:60]) for j in jobs))
        print("studio jobs:", [(k, j.get("kind"), j.get("status"), j.get("stage"), str(j.get("error"))[:80]) for k, j in studio._jobs.items()])
        print("refused before reaching a card:", [str(j.get("error"))[:240] for j in studio._jobs.values() if j.get("error")])
        landed = [j for j in jobs if j["status"] == "done"]
        want = 2 * len([m for m in MEMBERS if not m.startswith("sound:")])
        said_no = [e for e in (str(j.get("error")) for j in studio._jobs.values() if j.get("error")) if "can't run this" in e and ": " in e]
        check(len(landed) + len(said_no) == want and all(j["outputs"] for j in landed),
              f"LIVE: {want} jobs (every non-sound entry point x 2 workers): {len(landed)} rendered and pulled back, "
              f"{len(said_no)} refused by the card with its reason (e.g. a PC-only model)")
        refused = [j for j in jobs if j["status"] not in ("done",)]
        check(not refused, f"LIVE: no other remote job: {[(j['worker'], j['status']) for j in refused]}")
    await runner.cleanup()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


# ---------------------------------------------------------------- FETCH: a Mac card that lacks the model
MODEL = re.compile(r"\.(safetensors|sft|ckpt|pt|pth|bin|gguf|onnx)$", re.I)
UNAVAILABLE = "not on Hugging Face: copy it from the PC into models/loras/"
# what the Mac's /mac/fetch answers for a file on each call, per scenario (the last answer repeats)
SCENARIOS = {
    "present": ["present"],
    "downloading": ["downloading", "downloading", "present"],
    "failed": ["failed", "downloading", "present"],
    "unavailable": ["unavailable"],
}


async def fake_macs(remote, labels, real_oi, sv):
    """Two fake comfy-mac workers sharing one models folder (as the real ones do). Every model file is missing at
    first; /mac/fetch plays the current scenario, and a file becomes listed once it answers "present". /prompt
    records whether the gate still refused at that moment (it must never)."""
    from aiohttp import web
    st = {"hidden": set(), "landed": {}, "scenario": "present", "calls": {}, "posts": [], "fetches": [], "history": {}}

    def oi_now():
        out = json.loads(json.dumps(real_oi))
        for ct, node in out.items():
            for part in ("required", "optional"):
                for name, spec in ((node.get("input") or {}).get(part) or {}).items():
                    c = remote.combo(spec)
                    if c and not remote.is_file_input(spec):
                        keep = [o for o in c[0] if o not in st["hidden"]]
                        keep += [v for v in st["landed"].get((ct, name), ()) if v not in keep]    # downloaded into this folder
                        if isinstance(spec[0], list):
                            spec[0] = keep
                        else:
                            spec[1]["options"] = keep
        return out
    for node in real_oi.values():       # hide every model file
        for part in ("required", "optional"):
            for name, spec in ((node.get("input") or {}).get(part) or {}).items():
                c = remote.combo(spec)
                if c and not remote.is_file_input(spec):
                    st["hidden"].update(o for o in c[0] if isinstance(o, str) and MODEL.search(o))
    st["all_hidden"] = set(st["hidden"])
    png = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "probes.py"), "rb").read()[:64]

    def app_for(label):
        app = web.Application()
        r = app.router

        async def object_info(q):
            return web.json_response(oi_now())

        async def info(q):
            return web.json_response({"label": label, "self_validated": sv, "queue_remaining": 0})

        async def fetch(q):
            body = await q.json()
            st["fetches"].append((label, [f.get("value") for f in body.get("files") or []]))
            out = []
            for f in body.get("files") or []:
                v = f.get("value")
                n = st["calls"][v] = st["calls"].get(v, 0) + 1
                script = SCENARIOS[st["scenario"]]
                status = script[min(n, len(script)) - 1]
                if not MODEL.search(v or ""):
                    status = "unavailable"      # not a model file
                e = {"value": v, "status": status}
                if status == "present":
                    st["hidden"].discard(v)
                    st["landed"].setdefault((f.get("class_type"), f.get("input")), set()).add(v)
                elif status == "downloading":
                    e.update(by=label, done=n * 100_000_000, size=400_000_000)
                elif status == "failed":
                    e["reason"] = "connection reset"
                else:
                    e["reason"] = UNAVAILABLE
                out.append(e)
            return web.json_response({"files": out})

        async def upload(q):
            d = await q.post()
            return web.json_response({"name": d["image"].filename, "subfolder": d.get("subfolder", ""), "type": "input"})

        async def prompt(q):
            body = await q.json()
            pid = body.get("prompt_id")
            why = remote.gate(body["prompt"], oi_now(), sv)
            st["posts"].append({"worker": label, "prompt_id": pid, "refused_then": why})
            if why:
                return web.json_response({"error": "invalid prompt", "node_errors": {}}, status=400)
            st["history"][pid] = {"status": {"completed": True, "status_str": "success"},
                                  "outputs": {"9": {"images": [{"filename": "fetched_00001_.png", "subfolder": "", "type": "output"}]}}}
            return web.json_response({"prompt_id": pid, "number": 1, "node_errors": {}})

        async def history(q):
            pid = q.match_info["pid"]
            return web.json_response({pid: st["history"][pid]} if pid in st["history"] else {})

        async def ok(q):
            return web.json_response({})
        r.add_get("/object_info", object_info)
        r.add_get("/mac/info", info)
        r.add_post("/mac/fetch", fetch)
        r.add_post("/upload/image", upload)
        r.add_post("/prompt", prompt)
        r.add_get("/history/{pid}", history)
        r.add_get("/queue", lambda q: web.json_response({"queue_running": [], "queue_pending": []}))
        r.add_get("/view", lambda q: web.Response(body=png))
        r.add_post("/mac/forget", lambda q: web.json_response({"deleted": []}))
        for path in ("/queue", "/interrupt", "/history"):
            r.add_post(path, ok)
        return app
    workers, runners = [], []
    for label in labels:
        run = web.AppRunner(app_for(label))
        await run.setup()
        site = web.TCPSite(run, "127.0.0.1", 0)
        await site.start()
        runners.append(run)
        workers.append({"label": label, "host": "127.0.0.1", "port": site._server.sockets[0].getsockname()[1]})
    return st, workers, runners


async def fetch_matrix(studio, remote, drive, reached, said, local_posts, base):
    """Every entry point x every /mac/fetch answer, on a Mac card picked by name: the job either lands on that
    card after the download, under the id it was given at once, or fails with the Mac's reason. Never a /prompt
    while the gate still refuses. "Any free GPU" never downloads. Cancel while downloading drops the job."""
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.get(remote.base(WORKERS[1]) + "/object_info") as r:
            real_oi = await r.json()
        async with s.get(remote.base(WORKERS[1]) + "/mac/info") as r:
            sv = (await r.json()).get("self_validated")
    st, fakes, runners = await fake_macs(remote, [w["label"] for w in WORKERS], real_oi, sv)
    remote.save_workers(studio._data_dir(), fakes)
    remote._caps.clear()
    remote.FETCH_POLL = 0.05

    async def settle(n_jobs, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            live = [j for j in list(remote.JOBS.values())[n_jobs:] if j["status"] in remote.ACTIVE]
            busy = [j for j in studio._jobs.values() if j.get("status") == "running"]
            if not live and not busy:
                return
            await asyncio.sleep(0.1)

    k = 0
    for member in MEMBERS:
        for scen in SCENARIOS:
            w = fakes[k % 2]
            k += 1
            st["scenario"], st["calls"], st["hidden"], st["landed"] = scen, {}, set(st["all_hidden"]), {}
            remote._caps.clear()
            n_reach, n_post, n_fetch, n_said, n_jobs = len(reached), len(st["posts"]), len(st["fetches"]), len(said), len(remote.JOBS)
            old_sj = set(studio._jobs)
            try:
                got = await drive(member, w["label"])
            except Exception as e:
                got = [f"error: {str(e)[:120]}"]
            await settle(n_jobs)
            jobs = list(remote.JOBS.values())[n_jobs:]
            posts, fetches = st["posts"][n_post:], st["fetches"][n_fetch:]
            tag = f"{member} on {w['label']}, Mac answers {scen}:"
            if os.environ.get("DEBUG"):
                print("  DEBUG", tag, [str(x)[:300] for x in said[n_said:]], fetches, [(j["status"], j.get("error")) for j in jobs],
                      [(j.get("kind"), j.get("status"), str(j.get("error"))[:300]) for j in list(studio._jobs.values())[-1:]])
            check(got == [w["label"]] and all(r.get("fetch") is True for r in reached[n_reach:]),
                  f"{tag} reached {got} asking to download {[r.get('fetch') for r in reached[n_reach:]]}")
            # a node the Mac doesn't have at all: no download can help, so it's refused at once with that reason
            missing = sorted({ct for ct in re.findall(r"node (\w+) is not installed", " ".join(said[n_said:]) + json.dumps(
                [j.get("error") for k2, j in studio._jobs.items() if k2 not in old_sj], default=str)) if ct not in real_oi})
            if missing:
                print(f"NOTE {member} can't run on the Mac at all: {missing} not installed there")
                check(not fetches and not posts, f"{tag} refused at once, no download, nothing queued ({len(fetches)} fetch, {len(posts)} posts)")
                continue
            check(bool(fetches) and all(lab == w["label"] for lab, _ in fetches), f"{tag} asked {w['label']}'s /mac/fetch ({len(fetches)} calls)")
            check(all(not p["refused_then"] for p in posts), f"{tag} no /prompt while the gate still refused: {[p['refused_then'][:1] for p in posts if p['refused_then']]}")
            if scen == "unavailable":
                text = " ".join(said[n_said:]) + json.dumps([{k2: v for k2, v in j.items() if k2 in ("error", "stage")} for j in studio._jobs.values()], default=str) \
                    + json.dumps([j.get("error") for j in remote.JOBS.values()], default=str)
                async with aiohttp.ClientSession() as s:
                    async with s.get(base + "/manny/api/schedule") as r:
                        text += await r.text()
                check(not posts, f"{tag} nothing queued on the Mac ({len(posts)})")
                check(UNAVAILABLE in text, f"{tag} the Mac's reason reaches the caller")
            else:
                done = [j for j in jobs if j["status"] == "done" and j["outputs"]]
                check(len(posts) == 1 and len(jobs) == 1 and posts[0]["prompt_id"] == jobs[0]["prompt_id"] and posts[0]["worker"] == w["label"],
                      f"{tag} queued once on {w['label']} under the id it got at once: posts {[(p['worker'], p['prompt_id']) for p in posts]} jobs {[j['prompt_id'] for j in jobs]}")
                check(len(done) == 1, f"{tag} rendered and came back: {[(j['status'], j.get('error')) for j in jobs]}")
                want = {"present": 1, "downloading": 3, "failed": 3}[scen]
                check(len(fetches) >= want, f"{tag} kept asking until present ({len(fetches)} /mac/fetch calls, >= {want})")

    # "Any free GPU" never downloads: it takes a machine that can run it now (here: this PC)
    st["scenario"], st["calls"], st["hidden"], st["landed"] = "present", {}, set(st["all_hidden"]), {}
    remote._caps.clear()
    n_reach, n_fetch, lp = len(reached), len(st["fetches"]), len(local_posts)
    await drive("manny_run", "auto")
    check(len(reached) == n_reach and len(st["fetches"]) == n_fetch and len(local_posts) > lp,
          f"auto: ran on this PC, no download asked ({len(st['fetches']) - n_fetch} /mac/fetch, {len(reached) - n_reach} remote)")

    # cancel while downloading: the job is dropped, never queued; the UI is told the download carries on
    SCENARIOS["forever"] = ["downloading"]
    st["scenario"], st["calls"], st["hidden"], st["landed"] = "forever", {}, set(st["all_hidden"]), {}
    remote._caps.clear()
    n_post, n_jobs = len(st["posts"]), len(remote.JOBS)
    await drive("manny_run", fakes[0]["label"])
    j = (list(remote.JOBS.values())[n_jobs:] or [{}])[0]
    for _ in range(50):
        if j.get("status") == "fetching" and j.get("step"):
            break
        await asyncio.sleep(0.05)
    check(j.get("status") == "fetching" and "Downloading" in (j.get("step") or ""), f"while downloading: {j.get('status')} · {j.get('step')}")
    async with aiohttp.ClientSession() as s:
        async with s.post(base + f"/manny/api/remote_jobs/{j.get('prompt_id')}/cancel", json={}) as r:
            await r.read()
    await asyncio.sleep(0.5)
    check(j.get("status") == "cancelled" and "keeps downloading" in (j.get("note") or "") and len(st["posts"]) == n_post,
          f"cancel while downloading: {j.get('status')}, note {j.get('note')!r}, posts {len(st['posts']) - n_post}")

    # a Studio restart while the Mac downloads: the job can't be rebuilt, so it fails and says why
    remote.JOBS.clear()
    open(remote.state_file(), "w").write(json.dumps([{"prompt_id": "f1", "worker": fakes[0]["label"], "status": "fetching", "queued_at": time.time(),
                                                       "inputs": [], "outputs": [], "private": False}]))
    remote.resume(studio._dirs(), on_done=None)
    r = remote.JOBS.get("f1") or {}
    check(r.get("status") == "failed" and "download" in (r.get("error") or ""), f"restart while downloading: {r.get('status')} · {r.get('error')}")
    for run in runners:
        await run.cleanup()


def fetch_mutants():
    """Drop the download on each call site in turn: exactly the entry points through it must fail."""
    sites = [("manny_run", {"manny_run", "schedule", "mcp"}), ("_queue_api", {"finish", "sound:music", "sound:voice", "keyframes", "paint3d", "mcp:keyframes"})]
    bad = 0
    for fn_name, members in sites:
        d = tempfile.mkdtemp(prefix="studio-mutant-")
        pkg = os.path.join(d, "ComfyUI-MannyStudio")
        shutil.copytree(os.path.join(ROOT, "ComfyUI-MannyStudio"), pkg)
        p = os.path.join(pkg, "__init__.py")
        src = open(p, encoding="utf-8").read()
        m = re.search(rf"\n(async )?def {fn_name}\(.*?(?=\n(async )?def )", src, re.S)
        body = m.group(0) if m else ""
        if body.count("fetch=") != 1:
            print(f"FAIL fetch mutant {fn_name}: no single fetch= in it; update fetch_mutants")
            bad += 1
            continue
        open(p, "w", encoding="utf-8").write(src.replace(body, re.sub(r"fetch=[^,)]*", "fetch=False", body)))
        out = subprocess.run([sys.executable, os.path.abspath(__file__)], env=dict(os.environ, STUDIO_PKG=pkg, FETCH="1", LIVE="0"),
                             capture_output=True, text=True, timeout=900).stdout
        failed = {mm for mm in MEMBERS if re.search(rf"^FAIL {re.escape(mm)} on ", out, re.M)}
        ok = failed == members
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'} fetch mutant {fn_name} (download dropped): failing paths {sorted(failed)}")
    print(f"\n{bad} mutants not caught" if bad else "\nall mutants caught")
    return 1 if bad else 0


def web_mutants():
    """Drop the target from each browser render call in turn: exactly that call must be flagged."""
    src = open(WEB, encoding="utf-8").read()
    base = [x for x in web_sites(src)]
    bad = 0
    for line, route, ok in base:
        if line == "runBody":
            rb = re.search(r"function runBody\(app\) \{.*?\n\}", src, re.S).group(0)
            mut = src.replace(rb, rb.replace(" target: runTarget(),", "", 1), 1)
            if mut == src:
                print("FAIL web mutant runBody: nothing to mutate; update web_mutants")
                bad += 1
                continue
        else:
            ls = src.split("\n")
            seg = "\n".join(ls[line - 1:line + 3])
            if "target: runTarget()" not in seg:      # a runBody() caller: covered by the runBody mutant
                continue
            seg2 = seg.replace(", target: runTarget()", "", 1)
            mut = "\n".join(ls[:line - 1]) + "\n" + seg2 + "\n" + "\n".join(ls[line + 3:])
        flagged = {l for l, r, o in web_sites(mut) if not o}
        want = {line} if line != "runBody" else {"runBody"}
        good = flagged == want
        bad += not good
        print(f"{'PASS' if good else 'FAIL'} web mutant app.js:{line} {route}: flagged {sorted(map(str, flagged))}")
    return bad


def mutants():
    """Each mutation drops the target on ONE path: the test must fail on exactly that path's checks."""
    bad = web_mutants()
    for member, fn, a, b in MUTANTS:
        d = tempfile.mkdtemp(prefix="studio-mutant-")
        pkg = os.path.join(d, "ComfyUI-MannyStudio")
        shutil.copytree(os.path.join(ROOT, "ComfyUI-MannyStudio"), pkg)
        p = os.path.join(pkg, fn)
        src = open(p, encoding="utf-8").read()
        if src.count(a) != 1:
            print(f"FAIL mutant {member}: the line to mutate is gone from {fn}; update MUTANTS")
            bad += 1
            continue
        open(p, "w", encoding="utf-8").write(src.replace(a, b))
        out = subprocess.run([sys.executable, os.path.abspath(__file__)], env=dict(os.environ, STUDIO_PKG=pkg, LIVE="0"),
                             capture_output=True, text=True, timeout=600).stdout
        failed = {m for m in MEMBERS if re.search(rf"^FAIL {re.escape(m)} with target", out, re.M)}
        ok = failed == VIA_RUN.get(member, {member})
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'} mutant '{member}' (target dropped in {fn}): failing paths {sorted(failed)}")
    print(f"\n{bad} mutants not caught" if bad else "\nall mutants caught")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else fetch_mutants() if "--fetch-mutants" in sys.argv else asyncio.run(main()))
