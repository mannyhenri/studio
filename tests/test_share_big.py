"""The model share sends files of 2 GiB and more (confer c301a683).

  ~/code/comfy-mac/.venv/bin/python tests/test_share_big.py        (no network; writes a sparse 2.2 GiB file)

Live: Studio on the Windows PC sent 0 bytes for any response of 2 GiB or more (Range 0-2147483647, a whole 3 GB VAE,
"1000-"), while 100 MB pieces came through: aiohttp's FileResponse hands the file to the loop's sendfile, and Windows'
TransmitFile takes at most 2**31-1 bytes per call. Most models are bigger than that. The loop's sendfile is made to
refuse such counts here, as on Windows, and EVERY response shape must still carry the exact bytes: the whole file,
a Range from the start, one ending at the last byte, an open "from X" range, and a HEAD.
"""
import asyncio, hashlib, importlib.util, os, sys, tempfile, types

from aiohttp import web, ClientSession

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.environ.get("STUDIO_PKG") or os.path.join(ROOT, "ComfyUI-MannyStudio")
LIMIT = 2 ** 31 - 1
SIZE = LIMIT + 200 * 2 ** 20               # 2.2 GiB, sparse
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-big-"))
for d in ("user/default/workflows", "input", "output", "temp", "models/vae"):
    os.makedirs(os.path.join(tmp, d))
big = os.path.join(tmp, "models/vae/big_vae.safetensors")
with open(big, "wb") as f:                 # sparse, with recognisable bytes at both ends and around the 2 GiB mark
    f.truncate(SIZE)
    for at, data in ((0, b"HEAD" * 256), (LIMIT - 512, b"MID!" * 256), (SIZE - 1024, b"TAIL" * 256)):
        f.seek(at)
        f.write(data)

fp = types.ModuleType("folder_paths")
fp.get_user_directory = lambda: os.path.join(tmp, "user")
fp.get_input_directory = lambda: os.path.join(tmp, "input")
fp.get_output_directory = lambda: os.path.join(tmp, "output")
fp.get_temp_directory = lambda: os.path.join(tmp, "temp")
fp.get_folder_paths = lambda k: [os.path.join(tmp, "models", k)]
fp.models_dir = os.path.join(tmp, "models")
fp.folder_names_and_paths = {"vae": ([os.path.join(tmp, "models/vae")], {".safetensors"})}
fp.map_legacy = lambda k: k
fp.get_full_path = lambda f, n: (lambda p: p if os.path.isfile(p) else None)(os.path.join(tmp, "models", f, n))
fp.get_filename_list = lambda f: ["big_vae.safetensors"]
sys.modules["folder_paths"] = fp
ROUTES = web.RouteTableDef()
srv = types.ModuleType("server")
srv.PromptServer = type("PS", (), {"instance": types.SimpleNamespace(routes=ROUTES, app=types.SimpleNamespace(middlewares=[]), port=1,
                                                                   loop=None, prompt_queue=None, send_sync=lambda *a, **k: None)})
sys.modules["server"] = srv
spec = importlib.util.spec_from_file_location("studio", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
studio = importlib.util.module_from_spec(spec)
sys.modules["studio"] = studio
spec.loader.exec_module(studio)


def want(a, b):
    with open(big, "rb") as f:
        f.seek(a)
        h, left = hashlib.sha256(), b - a + 1
        while left:
            chunk = f.read(min(left, 1 << 24))
            h.update(chunk)
            left -= len(chunk)
    return h.hexdigest()


async def main():
    loop = asyncio.get_running_loop()
    real = loop.sendfile

    async def windows_sendfile(transport, file, offset=0, count=None, **k):     # TransmitFile: at most 2**31-1 per call
        if count is None or count > LIMIT:
            raise OSError(87, "The parameter is incorrect (TransmitFile count over 2 GiB)")
        return await real(transport, file, offset, count, **k)
    loop.sendfile = windows_sendfile
    app = web.Application()
    app.add_routes(ROUTES)
    run = web.AppRunner(app)
    await run.setup()
    site = web.TCPSite(run, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/manny/api/models/file/vae/big_vae.safetensors"
    cases = [("whole file", None, 0, SIZE - 1), ("Range from the start", f"bytes=0-{LIMIT}", 0, LIMIT),
             ("Range to the end", f"bytes=1000-{SIZE - 1}", 1000, SIZE - 1), ("open range", "bytes=1000-", 1000, SIZE - 1),
             ("small range", "bytes=0-1048575", 0, 1048575)]
    async with ClientSession() as s:
        for name, rng, a, b in cases:
            h, n = hashlib.sha256(), 0
            try:
                async with s.get(url, headers={"Range": rng} if rng else {}) as r:
                    code, cl, cr = r.status, r.headers.get("Content-Length"), r.headers.get("Content-Range")
                    async for chunk in r.content.iter_chunked(1 << 22):
                        h.update(chunk)
                        n += len(chunk)
            except Exception as e:
                code, cl, cr = f"error {type(e).__name__}", None, None
            ok = code == (206 if rng else 200) and n == b - a + 1 and h.hexdigest() == want(a, b) and cl == str(b - a + 1) \
                and (not rng or (cr or "").startswith(f"bytes {a}-{b}/{SIZE}"))
            check(ok, f"{name}: {code}, {n} of {b - a + 1} bytes, Content-Range {cr}")
        async with s.head(url) as r:
            check(r.status == 200 and r.headers.get("Content-Length") == str(SIZE), f"HEAD: {r.status}, Content-Length {r.headers.get('Content-Length')}")
    await run.cleanup()
    os.remove(big)
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
