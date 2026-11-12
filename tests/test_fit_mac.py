"""The combined result fits: Studio's side of each shared contract run against comfy-mac's REAL code (read-only).

  ~/code/comfy-mac/.venv/bin/python tests/test_fit_mac.py        (COMFY_MAC=~/code/comfy-mac by default)

Model share (C5, C12): Studio's modelshare routes are served on a real port; comfy-mac's mac_fetch.pc_url, pc_head,
plan_file and Fetcher (curl -C -) fetch from it, reaching it the way the Mac does (ipv4_base of this machine's .local
name, so the PC's allow-list sees a LAN address, not loopback). For EVERY pc-only file in comfy-mac's
testruns/studio-models.json, plus subfolder / space / Windows-"\\" names: HEAD 200 with the size, plan "fetch" from
source "pc", the download is byte-identical, and a cut download resumes. A Mac that isn't one of Studio's GPU
machines gets the Mac's "refused" reason; a file the PC lacks gets "not on Hugging Face and not on the Studio PC".
/mac/fetch replies (C3): every status the Mac can send is one Studio handles, and Studio reads only keys the Mac sends.
Missing-node refusals (C11): Studio's name_packs names the same pack as comfy-mac's WAITING map for every class.
Push (C15): when the Mac can't pull, Studio's _push sends every pc-only file into comfy-mac's real /mac/models handlers.
Subfolder spelling (C13): a file the PC lists as "Civitai\\x" and the Mac as "Civitai/x" passes the gate and reaches the
Mac in its spelling, for every downloaded file and every subfolder name in the PC's live model lists.
"""
import ast, asyncio, importlib.util, json, os, re, socket, sys, tempfile, time, types

from aiohttp import web

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.environ.get("STUDIO_PKG") or os.path.join(ROOT, "ComfyUI-MannyStudio")
MAC = os.path.expanduser(os.environ.get("COMFY_MAC", "~/code/comfy-mac"))
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-fit-"))
for d in ("user/default/workflows", "input", "output", "temp", "mac_models", "locks"):
    os.makedirs(os.path.join(tmp, d))

# --- the PC's model folders: every pc-only file comfy-mac's catalog says to get from the PC, plus awkward names
CATALOG = json.load(open(os.path.join(MAC, "testruns", "studio-models.json")))
PC_ONLY = [(e["folder"], e["file"], tuple(e["loaders"][0])) for e in CATALOG if e.get("decision") == "pc-only"]
EXTRA = [("loras", "civitai\\My Lora v2 (é).safetensors", ("LoraLoaderModelOnly", "lora_name")),     # as the Windows PC lists it
         ("checkpoints", "SDXL/juggernaut 9.safetensors", ("CheckpointLoaderSimple", "ckpt_name"))]
FILES = PC_ONLY + EXTRA
DATA = {}
for k, (folder, name, _l) in enumerate(FILES):
    p = os.path.join(tmp, "pc_models", folder, *re.split(r"[\\/]", name))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    DATA[(folder, name)] = os.urandom(200_000 + 9973 * k)
    open(p, "wb").write(DATA[(folder, name)])
FOLDERS = sorted({f for f, _n, _l in FILES})

fp = types.ModuleType("folder_paths")
fp.get_user_directory = lambda: os.path.join(tmp, "user")
fp.get_input_directory = lambda: os.path.join(tmp, "input")
fp.get_output_directory = lambda: os.path.join(tmp, "output")
fp.get_temp_directory = lambda: os.path.join(tmp, "temp")
fp.models_dir = os.path.join(tmp, "pc_models")
fp.folder_names_and_paths = {f: ([os.path.join(tmp, "pc_models", f)], {".safetensors"}) for f in FOLDERS}
fp.get_folder_paths = lambda k: list(fp.folder_names_and_paths.get(k, ([], None))[0])
fp.map_legacy = lambda k: k


def _full(folder, filename):            # ComfyUI's logic
    if folder not in fp.folder_names_and_paths:
        return None
    filename = os.path.relpath(os.path.join("/", filename), "/")
    for x in fp.folder_names_and_paths[folder][0]:
        p = os.path.join(x, filename)
        if os.path.isfile(p):
            return p
    return None


fp.get_full_path = _full
fp.get_filename_list = lambda f: sorted(os.path.relpath(os.path.join(dp, n), fp.folder_names_and_paths[f][0][0]).replace("/", "\\")
                                        for dp, _d, ns in os.walk(fp.folder_names_and_paths[f][0][0]) for n in ns)
sys.modules["folder_paths"] = fp
routes = web.RouteTableDef()
srv = types.ModuleType("server")
srv.PromptServer = type("PS", (), {"instance": types.SimpleNamespace(routes=routes, app=types.SimpleNamespace(middlewares=[]), port=1,
                                                                   loop=None, prompt_queue=None, send_sync=lambda *a, **k: None)})
sys.modules["server"] = srv
spec = importlib.util.spec_from_file_location("studio", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
studio = importlib.util.module_from_spec(spec)
sys.modules["studio"] = studio
spec.loader.exec_module(studio)
remote = sys.modules["studio.remote"]

# --- comfy-mac's code, imported read-only (model_plan and mac_fetch need no ComfyUI for these functions)
sys.path.insert(0, MAC)
sys.path.insert(0, os.path.join(MAC, "comfy_nodes"))
import mac_fetch  # noqa: E402
import model_plan  # noqa: E402


async def main():
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)       # this machine's LAN IPv4 (what the PC sees the Mac as)
    try:
        u.connect(("192.0.2.1", 9))
        lan = u.getsockname()[0]
    finally:
        u.close()
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 0)      # reachable on the LAN address, as the PC is
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = mac_fetch.ipv4_base(f"http://{lan}:{port}")
    check(not re.search(r"//(127\.|localhost)", base), f"the Mac reaches the PC by IPv4 LAN address, not loopback: {base}")

    # C12: Studio's allow-list = addresses of workers.json hosts; this machine plays the Mac
    remote.save_workers(studio._data_dir(), [{"label": "mac-vega-a", "host": lan, "port": 8188}])
    dirs = {f: os.path.join(tmp, "mac_models", f) for f in FOLDERS}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    fetcher = mac_fetch.Fetcher(os.path.join(tmp, "locks"), "mac-vega-a")
    head = mac_fetch.pc_head(base)
    statuses = set()
    for folder, name, (ct, inp) in FILES:
        want = DATA[(folder, name)]
        code, size, url = await asyncio.to_thread(head, folder, name)
        check(code == 200 and size == len(want) and url == mac_fetch.pc_url(base, folder, name),
              f"C5 HEAD {folder}/{name}: {code}, {size} bytes")
        p = await asyncio.to_thread(mac_fetch.plan_file, ct, inp, name, folder, {}, dirs, 10 ** 13, 0, head)
        statuses.add(p["status"])
        check(p.get("status") == "fetch" and p.get("source") == "pc" and p.get("size") == len(want),
              f"C5 plan {folder}/{name}: {p.get('status')} from {p.get('source')} ({p.get('reason', '')})")
        if p.get("status") != "fetch":
            continue
        if name.startswith("SDXL"):         # a cut download resumes (curl -C - needs 206 + Content-Range)
            os.makedirs(os.path.dirname(p["path"]), exist_ok=True)
            open(p["path"] + ".part", "wb").write(want[: len(want) // 3])
        fetcher.start(p["path"], p["url"], p["size"])
        await asyncio.to_thread(fetcher.wait, p["path"], 60)     # off the loop: this loop is the PC serving the file
        st = fetcher.status(p["path"], p["size"])
        statuses.add(st["status"])
        got = open(p["path"], "rb").read() if os.path.isfile(p["path"]) else b""
        check(st["status"] == "present" and got == want,
              f"C5 download {folder}/{name}{' (resumed)' if name.startswith('SDXL') else ''}: {st} {len(got)}/{len(want)} bytes, identical {got == want}")
        again = await asyncio.to_thread(mac_fetch.plan_file, ct, inp, name, folder, {}, dirs, 10 ** 13, 0, head)
        statuses.add(again["status"])
        check(again["status"] == "present", f"C9 once downloaded, the Mac answers present: {again['status']}")

    # C13: after the download, a job naming the file the way the Windows PC lists it ("\\") passes the gate against the
    # Mac's own listing ("/") and reaches the Mac in the Mac's spelling (ComfyUI there validates exact names too)
    mac_lists = {f: sorted(os.path.relpath(os.path.join(dp, n), dirs[f]) for dp, _d, ns in os.walk(dirs[f]) for n in ns if not n.endswith(".part"))
                 for f in FOLDERS}
    pc_lists = {f: fp.get_filename_list(f) for f in FOLDERS}
    posted = []
    wapp = web.Application()

    async def w_oi(q):
        return web.json_response(mac_oi)

    async def w_prompt(q):
        b = await q.json()
        posted.append(b["prompt"])
        return web.json_response({"error": "recorded"}, status=400)
    wapp.router.add_get("/object_info", w_oi)
    wapp.router.add_get("/mac/info", lambda q: web.json_response({"self_validated": {}}))
    wapp.router.add_post("/prompt", w_prompt)
    wr = web.AppRunner(wapp)
    await wr.setup()
    wsite = web.TCPSite(wr, "127.0.0.1", 0)
    await wsite.start()
    fake_mac = {"label": "fit-mac", "host": "127.0.0.1", "port": wsite._server.sockets[0].getsockname()[1]}
    for folder, name, (ct, inp) in FILES:
        pc_name = next(n for n in pc_lists[folder] if n.replace("\\", "/") == name.replace("\\", "/"))
        mac_name = next(n for n in mac_lists[folder] if n == name.replace("\\", "/"))
        mac_oi = {ct: {"input": {"required": {inp: [mac_lists[folder], {}]}}}}
        api = {"1": {"class_type": ct, "inputs": {inp: pc_name}}}
        why = remote.gate(api, mac_oi, {})
        n = len(posted)
        remote._caps.pop("fit-mac", None)
        try:
            await remote.submit(fake_mac, json.loads(json.dumps(api)), {"client_id": "fit"}, studio._dirs())
        except Exception:
            pass        # the fake worker answers 400 once it has recorded the prompt
        sent = posted[n]["1"]["inputs"][inp] if len(posted) > n else None
        check(not why and sent == mac_name, f"C13 {folder}/{pc_name!r}: gate {[x['reason'] for x in why] or 'ok'}, the Mac gets {sent!r} (lists {mac_name!r})")
    # and every subfolder name in the PC's live model lists, against the same names spelled the Mac's way
    live = {}
    try:
        import urllib.request
        for ct, inp in (("LoraLoader", "lora_name"), ("CheckpointLoaderSimple", "ckpt_name"), ("UNETLoader", "unet_name"), ("VAELoader", "vae_name")):
            spec = json.load(urllib.request.urlopen(f"{os.environ.get('PC_URL', 'http://desktop-mggcgsg.local:8188')}/object_info/{ct}", timeout=30))[ct]["input"]["required"][inp]
            live[(ct, inp)] = [o for o in remote.combo(spec)[0] if "\\" in o]
    except Exception as e:
        print(f"NOTE the PC's ComfyUI isn't reachable ({e}): live subfolder names not checked")
    for (ct, inp), names in live.items():
        mac_oi = {ct: {"input": {"required": {inp: [[x.replace("\\", "/") for x in names], {}]}}}}
        bad = [x for x in names if remote.gate({"1": {"class_type": ct, "inputs": {inp: x}}}, mac_oi, {})]
        check(not bad, f"C13 every {ct}.{inp} subfolder name on the PC ({len(names)}) passes against the Mac's spelling: {bad[:3]}")
    await wr.cleanup()

    # a file the PC doesn't have, and a Mac that isn't one of Studio's GPU machines
    p = await asyncio.to_thread(mac_fetch.plan_file, "LoraLoaderModelOnly", "lora_name", "nowhere.safetensors", "loras", {}, dirs, 10 ** 13, 0, head)
    statuses.add(p["status"])
    check(p["status"] == "unavailable" and "not on the Studio PC" in p.get("reason", ""), f"C5 404 -> {p['status']}: {p.get('reason')}")
    remote.save_workers(studio._data_dir(), [{"label": "other", "host": "192.0.2.77", "port": 8188}])
    sys.modules["studio.modelshare"]._cache["key"] = None
    p = await asyncio.to_thread(mac_fetch.plan_file, "LoraLoaderModelOnly", "lora_name", "x.safetensors", "loras", {}, dirs, 10 ** 13, 0, head)
    statuses.add(p["status"])
    check(p["status"] == "unavailable" and "refused this Mac" in p.get("reason", ""), f"C12 403 -> {p['status']}: {p.get('reason')}")
    down = mac_fetch.pc_head("http://127.0.0.1:9")      # nothing listens: unreachable
    p = await asyncio.to_thread(mac_fetch.plan_file, "LoraLoaderModelOnly", "lora_name", "x.safetensors", "loras", {}, dirs, 10 ** 13, 0, down)
    statuses.add(p["status"])
    check(p["status"] == "unavailable" and p.get("push") is True and "isn't reachable" in p.get("reason", ""),
          f"C5 PC unreachable -> {p['status']}, push {p.get('push')} (the PC sends it instead)")

    # C15 push: Studio's real _push into comfy-mac's real PUT/HEAD /mac/models handlers, for every pc-only file and
    # the awkward names; one cut short first, so the resume path (X-Mac-Have, Content-Range) runs too
    import mac_push
    pdirs = {f: os.path.join(tmp, "pushed", f) for f in FOLDERS}
    for d in pdirs.values():
        os.makedirs(d, exist_ok=True)
    puts_seen = []

    @web.middleware
    async def record(q, handler):           # what Studio sends with each PUT
        if q.method == "PUT":
            puts_seen.append((q.path, q.headers.get("X-Studio-SHA256")))
        return await handler(q)
    papp = web.Application(middlewares=[record])
    proutes = web.RouteTableDef()
    mac_push.add_routes(proutes, lambda: pdirs, mac_fetch.Fetcher(os.path.join(tmp, "plocks"), "mac-vega-b"), lambda: 10 ** 13)
    papp.add_routes(proutes)
    pr = web.AppRunner(papp)
    await pr.setup()
    psite = web.TCPSite(pr, "127.0.0.1", 0)
    await psite.start()
    pmac = {"label": "mac-vega-b", "host": "127.0.0.1", "port": psite._server.sockets[0].getsockname()[1]}
    keep = remote.LOCAL_MODEL
    remote.LOCAL_MODEL = sys.modules["studio.modelshare"].local_model
    for folder, name, _l in FILES:
        want = DATA[(folder, name)]
        dst = os.path.join(pdirs[folder], *re.split(r"[\\/]", name))
        if name.startswith("SDXL"):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            open(dst + ".part", "wb").write(want[: len(want) // 4])
        job = {}
        try:
            await remote._push(pmac, job, folder, name)
            err = ""
        except Exception as e:
            err = str(e)
        got = open(dst, "rb").read() if os.path.isfile(dst) else b""
        check(got == want and not err, f"C15 push {folder}/{name}{' (resumed)' if name.startswith('SDXL') else ''}: {len(got)}/{len(want)} bytes, identical {got == want} {err}")
    # R1: every push carries the file's sha256 (computed once, cached), so the Mac verifies every byte
    import hashlib
    want = {hashlib.sha256(d).hexdigest() for d in DATA.values()}
    shas = [h for _p, h in puts_seen]
    check(puts_seen and all(shas) and set(shas) <= want, f"R1 every push carries the file's sha256 ({sum(1 for h in shas if h)}/{len(shas)} PUTs)")
    share = sys.modules["studio.modelshare"]
    calls = []
    real_hash = share._hash_file
    share._hash_file = lambda p: (calls.append(p), real_hash(p))[1]
    f0 = remote.LOCAL_MODEL(FILES[0][0], FILES[0][1])
    a1, a2 = share.sha256_of(f0), share.sha256_of(f0)
    check(a1 == a2 == hashlib.sha256(DATA[(FILES[0][0], FILES[0][1])]).hexdigest() and len(calls) <= 1, f"R1 a file is hashed once, then cached ({len(calls)} hashes)")
    share._hash_file = real_hash
    keep_sha = remote.LOCAL_SHA
    remote.LOCAL_SHA = lambda p: "0" * 64          # what a byte flipped in transit looks like to the Mac
    folder, name, _l = FILES[1]
    os.remove(os.path.join(pdirs[folder], *re.split(r"[\\/]", name)))
    try:
        await remote._push(pmac, {}, folder, name)
        err = ""
    except Exception as e:
        err = str(e)
    check("422" in err, f"R1 a sha mismatch is refused by the Mac, not stored: {err[:120]}")
    remote.LOCAL_SHA = keep_sha
    remote.LOCAL_MODEL = keep
    await pr.cleanup()

    # C3: what the Mac answers is what Studio handles. answer() turns plan "fetch" into the Fetcher's state and
    # "missing" into "downloading"; Studio handles present / downloading / failed / unavailable.
    sent = {"fetch": "downloading", "missing": "downloading"}
    wire = {sent.get(s, s) for s in statuses} | {"downloading"}
    src = open(os.path.join(PKG, "remote.py"), encoding="utf-8").read()
    handled = set(re.findall(r'status"\) (?:==|!=) "(\w+)"', src)) | set(re.findall(r'f\.get\("status"\) == "(\w+)"', src))
    check(wire <= {"present", "downloading", "failed", "unavailable"} and {"present", "failed", "unavailable"} <= handled,
          f"C3 statuses on the wire {sorted(wire)} are all handled by Studio ({sorted(handled)})")
    mac_src = open(os.path.join(MAC, "comfy_nodes", "mac_fetch.py"), encoding="utf-8").read()

    def c3(mac_src, src):
        """What does not fit between what mac_fetch.py can put in a /mac/fetch reply and what remote.py does with it,
        all derived from the two sources (no hand-kept list): [] when it fits."""
        def body(name):
            i = src.index(f"def {name}(")
            j = min((k for k in (src.find("\ndef ", i + 1), src.find("\nasync def ", i + 1)) if k > 0), default=len(src))
            return src[i:j]
        # every status the Mac can write: dict literals, an if/else literal, and assignments (p["status"] = "x")
        in_src = {x for pair in re.findall(r'"status":\s*"(\w+)"(?:\s+if\b[^,]*?\belse\s+"(\w+)")?', mac_src) for x in pair if x}
        in_src |= set(re.findall(r'\["status"\]\s*=\s*"(\w+)"', mac_src))
        on_wire = {sent.get(x, x) for x in in_src}
        loop = body("_fetch_then_send")
        acted = set(re.findall(r'status"\)\s*==\s*"(\w+)"', loop))        # the ones the fetch loop acts on
        out = [f"Studio acts on {x!r}, which the Mac never sends" for x in sorted(acted - on_wire)]
        if "present" not in acted:
            out.append("the fetch loop never acts on 'present': a job could never leave it")
        words = {}
        for x in sorted(on_wire - acted):                                      # the rest are waits: each its own words
            j = {}
            remote._show_fetch({"label": "mac-vega-b"}, j, [{"value": "m.safetensors", "status": x}])
            words.setdefault(j["step"], []).append(x)
        out += [f"waits {xs} read the same on the queue line ({w!r})" for w, xs in words.items() if len(xs) > 1]
        # every key Studio reads from an entry, in the four functions that handle them, is one the Mac writes
        # (_fetch only reads the gate's refusals and returns the reply whole; f / x are reply entries, w the worker)
        reads = set().union(*(re.findall(r'\b[fx]\.get\("(\w+)"', body(n)) + re.findall(r'\b[fx]\["(\w+)"\]', body(n))
                              for n in ("_pushable", "_show_fetch", "_fetch_then_send")))
        writes = set(re.findall(r'"(\w+)":', mac_src)) | set(re.findall(r'\[\s*"(\w+)"\s*\]\s*=', mac_src))
        out += [f"Studio reads {k!r}, which the Mac never writes" for k in sorted(reads - writes)]
        return out, sorted(in_src), sorted(on_wire), sorted(acted), sorted(reads)

    bad, in_src, on_wire, acted, reads = c3(mac_src, src)
    check(not bad and "receiving" in on_wire,
          f"C3 every status mac_fetch.py can write {in_src} -> on the wire {on_wire}: Studio acts on {acted}, waits on the "
          f"rest, each worded apart; keys read {reads} all written: {bad or 'fits'}")
    # and C3 can't pass on a rename: a status added in each form the Mac's code uses, or a key renamed, is caught
    mutants = {"a new status by assignment": mac_src + '\n    p["status"] = "queuedx"\n',
               "a new status in a dict": mac_src + '\n    x = {"status": "parkedx"}\n',
               "a key Studio reads renamed": mac_src.replace('"done"', '"gotx"').replace("done=", "gotx=")}
    caught = {k: bool(c3(m, src)[0]) for k, m in mutants.items()}
    check(all(caught.values()), f"C3 catches each mutant of mac_fetch.py: {caught}")
    # C11: the pack a missing node comes from: Studio's name_packs (from the PC's python_module) == comfy-mac's WAITING
    tree = ast.parse(open(os.path.join(MAC, "test_videotools_mac.py"), encoding="utf-8").read())
    waiting = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "WAITING")
    pc_oi = {}
    try:
        import urllib.request
        pc_oi = json.load(urllib.request.urlopen(os.environ.get("PC_URL", "http://desktop-mggcgsg.local:8188") + "/object_info", timeout=30))
    except Exception as e:
        print(f"NOTE the PC's ComfyUI isn't reachable ({e}): checking against the PC's pack names recorded in the confer")
        pc_oi = {c: {"python_module": f"custom_nodes.{p}"} for c, p in
                 {"H3FaceTrackCrop": "ComfyUI-H3-FaceRefine", "H3FaceStitch": "ComfyUI-H3-FaceRefine",
                  "H3LastFrame": "comfyui-h3-multishot", "H3ConcatAV": "comfyui-h3-multishot"}.items()}

    async def pc_info(force=False):
        return pc_oi
    remote.LOCAL_INFO = pc_info
    for cls, pack in sorted(waiting.items()):
        why = await remote.name_packs([{"node": "1", "class_type": cls, "reason": f"node {cls} is not installed"}])
        check(f"copy ComfyUI/custom_nodes/{pack} " in why[0]["reason"], f"C11 {cls}: Studio says copy {pack} ({why[0].get('pack')})")
    await runner.cleanup()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
