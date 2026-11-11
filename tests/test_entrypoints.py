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
Needs the workers' /object_info (127.0.0.1:8188 and :8189) to convert apps, and ffmpeg for Finish.
"""
import asyncio, glob, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.environ.get("STUDIO_PKG") or os.path.join(ROOT, "ComfyUI-MannyStudio")
LIVE = os.environ.get("LIVE") == "1"
WORKERS = [{"label": "mac-vega-a", "host": "127.0.0.1", "port": 8188}, {"label": "mac-vega-b", "host": "127.0.0.1", "port": 8189}]
APP = "Studio - Remove background.app.json"
MEMBERS = ["manny_run", "finish", "sound:music", "sound:voice", "schedule", "mcp"]

# the mutations used by --mutants: (member, file, original, replacement) — each removes that path's target.
# Scheduled renders and MCP go through /manny/api/run, so breaking manny_run must break them too.
VIA_RUN = {"manny_run": {"manny_run", "schedule", "mcp"}}
MUTANTS = [
    ("manny_run", "__init__.py", 'target = body.get("target") or _remote.LOCAL', "target = _remote.LOCAL"),
    ("finish", "__init__.py", '"Finish: smooth", b.get("target"))', '"Finish: smooth", None)'),
    ("sound:music", "sound.py", "pid = await _queue(api, private, label, b.get(\"target\"))",
     "pid = await _queue(api, private, label, b.get(\"target\") if kind != \"music\" else None)"),
    ("sound:voice", "sound.py", "pid = await _queue(api, private, label, b.get(\"target\"))",
     "pid = await _queue(api, private, label, b.get(\"target\") if kind != \"voice\" else None)"),
    ("schedule", "schedule.py", 'body["client_id"] = "studio-schedule"', 'body["client_id"] = "studio-schedule"; body.pop("target", None)'),
    ("mcp", "studio_mcp.py", '"target": a.get("run_on") or "local"', '"target": "local"'),
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
    sub   - calls that can hand a job to another machine (_remote.submit, _queue_api),
    run   - callers of /manny/api/run (they re-enter manny_run),
    local - posts straight to this PC's ComfyUI /prompt."""
    import ast
    sub, run, local = set(), set(), set()
    for fn in sorted(glob.glob(os.path.join(PKG, "*.py"))):
        f = os.path.basename(fn)
        for call, enc in _calls(fn):
            c = call.func
            if (isinstance(c, ast.Attribute) and c.attr == "submit" and isinstance(c.value, ast.Name) and c.value.id == "_remote") \
                    or (isinstance(c, ast.Name) and c.id == "_queue_api") \
                    or (isinstance(c, ast.Subscript) and _text(c.slice) == "_queue_api"):
                sub.add((f, enc))
            texts = [_text(a) for a in call.args]
            if any("/manny/api/run" in t for t in texts) and (isinstance(c, ast.Attribute) and c.attr == "post" or isinstance(c, ast.Name) and c.id == "http"):
                run.add((f, enc))
            if isinstance(c, ast.Attribute) and c.attr == "post" and any("127.0.0.1" in t and t.endswith("/prompt") for t in texts):
                local.add((f, enc))
    return sub, run, local


# what each derived site is: the members driven below, plus routing internals and the PC-only AI path
SITES = {
    "sub": {("__init__.py", "manny_run"): "manny_run", ("__init__.py", "_queue_app"): "finish",
            ("__init__.py", "_queue_api"): "(routing: Finish, music, voice)", ("sound.py", "_queue"): "sound:music + sound:voice"},
    "run": {("schedule.py", "_fire"): "schedule", ("studio_mcp.py", "call"): "mcp"},
    "local": {("__init__.py", "manny_run"): "(manny_run: this PC)", ("__init__.py", "_queue_api"): "(_queue_api: this PC)",
              ("__init__.py", "_llm"): "AI review / AI prompt help: PC-only",
              ("videotools.py", "_queue"): "Video fixes / Continue / Keyframe shot: PC-only (no Run on yet)"},
}
WEB = os.path.join(os.path.dirname(PKG), "ComfyUI-MannyStudio", "web", "app.js") if os.environ.get("STUDIO_PKG") else os.path.join(PKG, "web", "app.js")
RENDER_ROUTES = r'"/manny/api/(run|finish|music|voice|schedule)"|"/manny/api/" \+ kind'


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

    async def spy(w, api, payload_extra, dirs, private=False, incoming=".private_incoming", on_done=None):
        reached.append({"worker": w["label"], "client_id": payload_extra.get("client_id"),
                        "label": (payload_extra.get("extra_data") or {}).get("manny_label"), "at": time.time()})
        if LIVE:
            return await real_submit(w, api, payload_extra, dirs, private, incoming, on_done)
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

    async def post(s, path, body):
        async with s.post(base + path, json=body) as r:
            txt = await r.text()
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
            elif member == "mcp":
                os.environ["STUDIO_URL"] = base
                sys.path.insert(0, PKG)
                import studio_mcp
                studio_mcp.BASE = base
                await asyncio.to_thread(studio_mcp.call, "studio_generate",
                                        {"app": "Remove background", "images": ["ref_couture_model.png"], "run_on": label, "wait": False})
        return [r["worker"] for r in reached[n:]]

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
        jobs = [j for j in remote.JOBS.values()]
        print("live jobs:", sorted((j["worker"], j["status"], (j.get("error") or "")[:60]) for j in jobs))
        landed = [j for j in jobs if j["status"] == "done"]
        check(len(landed) == 8 and all(j["outputs"] for j in landed),
              f"LIVE: 8 renders (manny_run, finish, schedule, mcp x 2 workers) done and pulled back: {len(landed)}")
        refused = [j for j in jobs if j["status"] not in ("done",)]
        check(not refused, f"LIVE: no other remote job: {[(j['worker'], j['status']) for j in refused]}")
    await runner.cleanup()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


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
    sys.exit(mutants() if "--mutants" in sys.argv else asyncio.run(main()))
