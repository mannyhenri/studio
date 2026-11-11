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
    check(p["status"] == "failed" and "isn't reachable" in p.get("reason", ""), f"C5 PC unreachable -> {p['status']} (asked again later)")

    # C3: what the Mac answers is what Studio handles. answer() turns plan "fetch" into the Fetcher's state and
    # "missing" into "downloading"; Studio handles present / downloading / failed / unavailable.
    sent = {"fetch": "downloading", "missing": "downloading"}
    wire = {sent.get(s, s) for s in statuses} | {"downloading"}
    src = open(os.path.join(PKG, "remote.py"), encoding="utf-8").read()
    handled = set(re.findall(r'status"\) (?:==|!=) "(\w+)"', src)) | set(re.findall(r'f\.get\("status"\) == "(\w+)"', src))
    check(wire <= {"present", "downloading", "failed", "unavailable"} and {"present", "failed", "unavailable"} <= handled,
          f"C3 statuses on the wire {sorted(wire)} are all handled by Studio ({sorted(handled)})")
    reads = set(re.findall(r'(?:x|f|e)\.get\("(\w+)"\)', src[src.index("def _fetch("):src.index("async def _fetch_then_send")]))
    mac_src = open(os.path.join(MAC, "comfy_nodes", "mac_fetch.py"), encoding="utf-8").read()
    mac_keys = set(re.findall(r'"(\w+)":', mac_src)) | {"value", "status"}
    check(reads <= mac_keys, f"C3 the keys Studio reads from a reply {sorted(reads)} are keys the Mac sends (missing {sorted(reads - mac_keys)})")

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
