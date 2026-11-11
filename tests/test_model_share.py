"""Model sharing: this PC serves its model files to its GPU machines (modelshare.py), so a Mac can fetch what isn't on HF.

  ~/code/comfy-mac/.venv/bin/python tests/test_model_share.py
No ComfyUI or workers needed: the whole pack is loaded with ComfyUI stubbed, its routes go on a real aiohttp test
server (with the pack's own auth middleware), and folder_paths points at temp model folders. A test middleware sets
request.remote from an X-Fake-Remote header so every caller address goes through the real handlers.

For EVERY folder key x EVERY file: allowed callers get 200 with the exact bytes and Content-Length (name sent with "/"
and, nested, with "\\"), HEAD works, Range resume gives 206 with the right slice, the index lists exactly the files.
Disallowed addresses get 403 on every route. "..", absolute and drive paths get 400; unknown folders and
non-model keys (custom_nodes) 404; nothing outside the model folders (Incognito included) ever leaks.
"""
import asyncio, importlib.util, os, random, socket, sys, tempfile, types
from urllib.parse import quote

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "ComfyUI-MannyStudio")
tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-share-"))
for d in ("user/default/workflows", "input/manny_private", "output", "temp", "models", "custom_nodes/somepack"):
    os.makedirs(os.path.join(tmp, d))
SECRET = b"SECRET-NEVER-SERVED-" + os.urandom(8).hex().encode()
rnd = random.Random(7)


def put(path, n=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = SECRET if n is None else bytes(rnd.getrandbits(8) for _ in range(n))
    with open(path, "wb") as f:
        f.write(data)
    return data


# --- model folders: several keys, nested subfolders, odd names, a second path per key like extra_model_paths
FILES = {   # key -> {name with "/": bytes}
    "checkpoints": {"anime_desire_illustrious_v3_fp16.safetensors": 3000, "SDXL/juggernaut v9 (é).safetensors": 1200},
    "loras": {"moss_image_nsfw_uncensored_lora.safetensors": 777, "style/sub/deep 100%.safetensors": 513},
    "diffusion_models": {"flux3-image-klein-int8-convrot.safetensors": 2049},
    "text_encoders": {"qwen_3_06b_base.safetensors": 900, "qwen/qwen3vl_4b_int8_convrot.safetensors": 64},
    "vae": {"p-h3-image_vae_int8_convrot.safetensors": 400},
    "upscale_models": {"4x-UltraSharp.pth": 1},
    "ultralytics_bbox": {"face_yolov8m.pt": 333},
}
PATHS = {k: [os.path.join(tmp, "models", k)] for k in FILES}
PATHS["loras"].append(os.path.join(tmp, "extra_loras"))           # second folder for one key
FILES["loras"]["from_extra.safetensors"] = 222
DATA = {}
for k, names in FILES.items():
    for n, size in names.items():
        base = PATHS[k][1] if n == "from_extra.safetensors" else PATHS[k][0]
        DATA[(k, n)] = put(os.path.join(base, *n.split("/")), size)
# a compressed sibling next to every model: a client that accepts gzip/br must still get the model's own bytes
for (k, n), _d in list(DATA.items()):
    base = PATHS[k][1] if n == "from_extra.safetensors" else PATHS[k][0]
    for ext in (".gz", ".br"):
        put(os.path.join(base, *n.split("/")) + ext, 17)
os.makedirs(os.path.join(tmp, "models", "frame_interpolation"))   # a key with no files
PATHS["frame_interpolation"] = [os.path.join(tmp, "models", "frame_interpolation")]
FILES["frame_interpolation"] = {}

# things that must never be served
PRIV = os.path.join(tmp, "Comfyui-Private")                       # Incognito renders (next to the output folder)
put(os.path.join(PRIV, "loras_leak", "private.safetensors"))      # a model path someone pointed into the private space
put(os.path.join(tmp, "input", "manny_private", "face.safetensors"))
put(os.path.join(tmp, "secret.txt"))
put(os.path.join(tmp, "models", "secret.txt"))
put(os.path.join(tmp, "custom_nodes", "somepack", "config.json"))
put(os.path.join(tmp, "user", "default", "manny_studio", "auth.json"))
PATHS["loras"].append(os.path.join(PRIV, "loras_leak"))
PATHS["vae"].append(os.path.join(tmp, "input", "manny_private"))
PATHS["custom_nodes"] = [os.path.join(tmp, "custom_nodes")]

# --- stub the bits of ComfyUI the pack imports
fp = types.ModuleType("folder_paths")
fp.get_user_directory = lambda: os.path.join(tmp, "user")
fp.get_input_directory = lambda: os.path.join(tmp, "input")
fp.get_output_directory = lambda: os.path.join(tmp, "output")
fp.get_temp_directory = lambda: os.path.join(tmp, "temp")
fp.get_folder_paths = lambda k: list(PATHS.get(k, []))
fp.models_dir = os.path.join(tmp, "models")
fp.folder_names_and_paths = {k: (v, {".safetensors", ".pth", ".pt", ".json"}) for k, v in PATHS.items()}
fp.map_legacy = lambda k: {"unet": "diffusion_models", "clip": "text_encoders"}.get(k, k)


def get_full_path(folder, filename):   # ComfyUI's own, verbatim logic
    folder = fp.map_legacy(folder)
    if folder not in fp.folder_names_and_paths:
        return None
    filename = os.path.relpath(os.path.join("/", filename), "/")
    for x in fp.folder_names_and_paths[folder][0]:
        p = os.path.join(x, filename)
        if os.path.isfile(p):
            return p
    return None


def get_filename_list(folder):         # as the Windows PC lists them: "\" separators
    out = []
    for base in fp.folder_names_and_paths[fp.map_legacy(folder)][0]:
        for dp, _ds, fs in os.walk(base):
            out += [os.path.relpath(os.path.join(dp, f), base).replace("/", "\\") for f in fs if not f.endswith((".gz", ".br"))]   # ComfyUI lists model extensions only
    return sorted(set(out))


fp.get_full_path = get_full_path
fp.get_filename_list = get_filename_list
sys.modules["folder_paths"] = fp
ROUTES = web.RouteTableDef()
APP_MW = []
srv = types.ModuleType("server")
srv.PromptServer = type("PS", (), {"instance": types.SimpleNamespace(routes=ROUTES, app=types.SimpleNamespace(middlewares=APP_MW),
                                                                   port=1, loop=None, prompt_queue=None, send_sync=lambda *a, **k: None)})
sys.modules["server"] = srv
spec = importlib.util.spec_from_file_location("studio", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
studio = importlib.util.module_from_spec(spec)
sys.modules["studio"] = studio
spec.loader.exec_module(studio)
remote = sys.modules["studio.remote"]
share = sys.modules.get("studio.modelshare")

# --- name resolution: the Mac's .local name gives a link-local IPv6 (with zone) and an IPv4; another an IPv4-mapped one
_real_gai = socket.getaddrinfo
LOOKUPS = []


def fake_gai(host, *a, **k):
    LOOKUPS.append(host)
    fake = {"mannys-mac-pro.local": ["fe80::1c2:3%en0", "192.168.1.20", "2001:db8::20"],
            "mapped.local": ["::ffff:192.168.1.30"]}
    if host in fake:
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in fake[host]]
    if host.endswith(".invalid"):
        raise socket.gaierror(8, "nodename nor servname provided")
    return _real_gai(host, *a, **k)


socket.getaddrinfo = fake_gai
WORKERS = [{"label": "mac-vega-a", "host": "Mannys-Mac-Pro.local", "port": 8188},
           {"label": "mac-vega-b", "host": "Mannys-Mac-Pro.local", "port": 8189},
           {"label": "mapped", "host": "mapped.local", "port": 8188},
           {"label": "by-ip", "host": "10.0.0.7", "port": 8188},
           {"label": "gone", "host": "nowhere.invalid", "port": 8188}]
remote.save_workers(studio._data_dir(), WORKERS)
ALLOWED = ["127.0.0.1", "::1", "::ffff:127.0.0.1", "192.168.1.20", "::ffff:192.168.1.20", "fe80::1c2:3%en0", "fe80::1c2:3",
           "2001:db8::20", "192.168.1.30", "::ffff:192.168.1.30", "10.0.0.7", "::ffff:10.0.0.7"]
DENIED = ["192.168.1.99", "::ffff:192.168.1.99", "fe80::1c2:4%en0", "2001:db8::21", "10.0.0.8", "8.8.8.8", "fe80::1c2:3:1"]
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


@web.middleware
async def fake_remote(request, handler):
    r = request.headers.get("X-Fake-Remote")
    return await handler(request.clone(remote=r) if r else request)


def furl(folder, name):
    return URL("/manny/api/models/file/" + quote(folder, safe="") + "/" + quote(name, safe="/"), encoded=True)


async def main():
    check(share is not None, "the pack loads studio.modelshare")
    if share is None:
        return 1
    app = web.Application(middlewares=[fake_remote] + APP_MW)
    app.add_routes(ROUTES)
    check(any(getattr(m, "__name__", "") == "lock" for m in APP_MW), "the pack's own auth middleware is on the test server")
    cl = TestClient(TestServer(app, host="127.0.0.1"))
    await cl.start_server()

    async def get(url, ip="127.0.0.1", method="GET", **hd):
        async with cl.request(method, url, headers=dict({"Accept-Encoding": "gzip, br"}, **hd, **{"X-Fake-Remote": ip}), auto_decompress=False) as r:
            return r.status, dict(r.headers), await r.read()

    keys = [k for k in FILES]
    total = sum(len(v) for v in FILES.values())
    print(f"{len(keys)} folder keys, {total} files, {len(ALLOWED)} allowed and {len(DENIED)} denied addresses")

    # 1. every key x every file, from every allowed address
    for ip in ALLOWED:
        bad = []
        for k in keys:
            for n in FILES[k]:
                data = DATA[(k, n)]
                names = [n] + ([n.replace("/", "\\")] if "/" in n else [])
                for nm in names:
                    s, h, b = await get(furl(k, nm), ip)
                    if s != 200 or b != data or h.get("Content-Length") != str(len(data)) or h.get("Accept-Ranges") != "bytes" or h.get("Content-Encoding"):
                        bad.append(f"GET {k}/{nm}: {s} len={len(b)} CL={h.get('Content-Length')} AR={h.get('Accept-Ranges')}")
                s, h, b = await get(furl(k, n), ip, "HEAD")
                if s != 200 or b or h.get("Content-Length") != str(len(data)):
                    bad.append(f"HEAD {k}/{n}: {s} CL={h.get('Content-Length')} body={len(b)}")
                if len(data) > 1:
                    a = len(data) // 3
                    for rng, want in ((f"bytes={a}-", data[a:]), (f"bytes={a}-{len(data) - 2}", data[a:len(data) - 1])):
                        s, h, b = await get(furl(k, n), ip, Range=rng)
                        if s != 206 or b != want or h.get("Content-Length") != str(len(want)) or \
                                not (h.get("Content-Range") or "").startswith(f"bytes {a}-"):
                            bad.append(f"Range {rng} {k}/{n}: {s} len={len(b)} CR={h.get('Content-Range')}")
            s, h, b = await get(f"/manny/api/models/index?folder={k}", ip)
            import json
            idx = json.loads(b) if s == 200 else {}
            got = {f["name"]: f for f in idx.get("files", [])}
            if set(got) != set(FILES[k]):
                bad.append(f"index {k}: {s} {sorted(got)} != {sorted(FILES[k])}")
            for n, f in got.items():
                p = fp.get_full_path(k, n)
                if p and (f["size"] != os.path.getsize(p) or abs(f["mtime"] - os.path.getmtime(p)) > 1e-3):
                    bad.append(f"index {k}/{n}: size/mtime {f}")
        check(not bad, f"allowed {ip}: every file GET (/ and \\), HEAD, Range 206 and the index of every key" + (f": {bad[:4]}" if bad else ""))

    # 2. legacy folder names ComfyUI still maps
    s, _h, b = await get(furl("clip", "qwen_3_06b_base.safetensors"))
    check(s == 200 and b == DATA[("text_encoders", "qwen_3_06b_base.safetensors")], f"legacy folder 'clip' maps to text_encoders: {s}")

    # 3. denied addresses: 403 on every route, never a byte, and the text says who may
    for ip in DENIED:
        bad, why = [], ""
        for k in keys:
            for n in FILES[k]:
                for method, hd in (("GET", {}), ("HEAD", {}), ("GET", {"Range": "bytes=1-"})):
                    s, _h, b = await get(furl(k, n), ip, method, **hd)
                    if s != 403 or DATA[(k, n)][:32] in b and len(DATA[(k, n)]) > 8:
                        bad.append(f"{method} {k}/{n} {hd}: {s}")
                    why = why or b.decode("utf-8", "replace")
            s, _h, b = await get(f"/manny/api/models/index?folder={k}", ip)
            if s != 403 or b"safetensors" in b:
                bad.append(f"index {k}: {s}")
        for path in (furl("nope", "x"), URL("/manny/api/models/file/loras/%2E%2E/secret.txt", encoded=True),
                     "/manny/api/models/index?folder=nope", "/manny/api/models/index"):
            s, _h, _b = await get(path, ip)
            if s != 403:
                bad.append(f"{path}: {s}")
        check(not bad, f"denied {ip}: 403 on every route" + (f": {bad[:4]}" if bad else ""))
    check("mac-vega-a" in why and "this PC" in why, f"the 403 says which machines may: {why!r}")

    # 4. escapes: 400 for .. and absolute names, 404 for unknown/non-model keys and private files; never the secret.
    # A literal ".." segment in the URL is resolved by aiohttp before routing (404, no handler runs); encoded ones reach it.
    escapes = ["../secret.txt", "../../secret.txt", "..", "a/../../secret.txt", "..\\secret.txt", "a\\..\\..\\secret.txt",
               "/etc/hosts", tmp + "/secret.txt", "\\secret.txt", "C:\\Windows\\win.ini", "C:/Windows/win.ini", "c:secret.txt",
               "\\\\server\\share\\x.safetensors", "//server/share/x.safetensors"]
    bad = []
    for k in keys + ["custom_nodes"]:
        for nm in escapes:
            enc = quote(nm, safe="")
            for raw in (quote(nm, safe="/"), enc, enc.replace(".", "%2E")):
                url = URL("/manny/api/models/file/" + k + "/" + raw, encoded=True)
                s, _h, b = await get(url)
                want = 404 if ".." in raw.split("/") else 400
                if s != want or SECRET in b:
                    bad.append(f"{url}: {s} (want {want})")
    check(not bad, f"{len(escapes)} escape names x {len(keys) + 1} keys x 3 encodings: 400 (404 when aiohttp drops a raw ..), never a byte" + (f": {bad[:4]}" if bad else ""))
    bad = []
    for k, n in (("nope", "x.safetensors"), ("custom_nodes", "somepack/config.json"), ("", "x"), ("..", "secret.txt"),
                 ("loras", "private.safetensors"), ("vae", "face.safetensors"), ("loras", "missing.safetensors"),
                 ("loras", "style"), ("loras", "style/sub")):
        s, _h, b = await get(furl(k, n) if k else URL("/manny/api/models/file//x", encoded=True))
        if s not in (404, 400) or SECRET in b:
            bad.append(f"{k}/{n}: {s}")
    for q in ("folder=nope", "folder=custom_nodes", "folder=", ""):
        s, _h, b = await get("/manny/api/models/index?" + q)
        if s not in (400, 404) or b"config.json" in b:
            bad.append(f"index?{q}: {s}")
    check(not bad, "unknown keys, custom_nodes, Incognito files, folders and missing files: 404, never a byte" + (f": {bad}" if bad else ""))
    s, _h, b = await get("/manny/api/models/index?folder=loras")
    check(b"private" not in b, "the index never lists a file from the private space")

    # 5. allow-list: one lookup per host per minute, a workers.json change counts at once, no workers = only this PC
    n = len(LOOKUPS)
    for _ in range(5):
        await get(furl("vae", "p-h3-image_vae_int8_convrot.safetensors"), "192.168.1.20", "HEAD")
    check(len(LOOKUPS) == n, f"cached: no new lookups over 5 requests ({len(LOOKUPS) - n})")
    remote.save_workers(studio._data_dir(), [w for w in WORKERS if w["label"] != "by-ip"])
    s, *_ = await get(furl("vae", "p-h3-image_vae_int8_convrot.safetensors"), "10.0.0.7", "HEAD")
    check(s == 403, f"a machine removed from GPU machines is refused at once: {s}")
    remote.save_workers(studio._data_dir(), [])
    s1, *_ = await get(furl("vae", "p-h3-image_vae_int8_convrot.safetensors"), "192.168.1.20", "HEAD")
    s2, *_ = await get(furl("vae", "p-h3-image_vae_int8_convrot.safetensors"), "127.0.0.1", "HEAD")
    check(s1 == 403 and s2 == 200, f"no GPU machines: only this PC ({s1}, {s2})")

    # 6. a sha256 sidecar is passed on; none is computed otherwise
    p = os.path.join(PATHS["vae"][0], "p-h3-image_vae_int8_convrot.safetensors")
    with open(p + ".sha256", "w") as f:
        f.write("ab" * 32 + "  p-h3-image_vae_int8_convrot.safetensors\n")
    _s, h, _b = await get(furl("vae", "p-h3-image_vae_int8_convrot.safetensors"), method="HEAD")
    _s, h2, _b = await get(furl("checkpoints", "anime_desire_illustrious_v3_fp16.safetensors"), method="HEAD")
    check(h.get("X-Studio-SHA256") == "ab" * 32 and "X-Studio-SHA256" not in h2, f"X-Studio-SHA256 from a sidecar only: {h.get('X-Studio-SHA256')}")

    await cl.close()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
