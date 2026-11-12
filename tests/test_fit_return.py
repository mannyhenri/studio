"""What happens when a job moved to a Mac card ends there (the PC's return path) and one push per file.

  ~/code/comfy-mac/.venv/bin/python tests/test_fit_return.py        (no workers needed)

Return path (fit-check V17): a job moved with Send to... keeps its original here (_moved_put). For EVERY way it can end
on the card and both spaces (normal, Incognito):
  done / cancelled    -> the kept original is dropped; nothing is queued here again
  failed              -> the original goes back to the FRONT of this PC's queue, unchanged, the error says so, and an
                         Incognito one stays Incognito. Failures include comfy-mac's lora_guard text, an MPS
                         out-of-memory and a gate refusal.
One push per file (V20): jobs that need the same file at once make ONE PUT; every job waits for it.
"""
import asyncio, glob, importlib.util, json, os, shutil, sys, tempfile, types

from aiohttp import web

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.environ.get("STUDIO_PKG") or os.path.join(ROOT, "ComfyUI-MannyStudio")
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-return-"))
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


class _R:
    def __getattr__(self, m):
        return lambda path: (lambda fn: fn)


ps = types.SimpleNamespace(routes=_R(), app=types.SimpleNamespace(middlewares=[]), port=1, loop=None, prompt_queue=None,
                           send_sync=lambda *a, **k: None)
srv = types.ModuleType("server")
srv.PromptServer = type("PS", (), {"instance": ps})
sys.modules["server"] = srv
spec = importlib.util.spec_from_file_location("studio", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
studio = importlib.util.module_from_spec(spec)
sys.modules["studio"] = studio
spec.loader.exec_module(studio)
remote = sys.modules["studio.remote"]

ENDINGS = [   # (status, error) a moved job can end with on a card
    ("done", None),
    ("cancelled", None),
    ("failed", "mac-vega-a: the render failed. 2136 of this LoRA's model keys didn't load on this Mac (ComfyUI 0.44): it would "
               "render only part of the LoRA, unlike the PC. Render it on the PC"),
    ("failed", "mac-vega-a: the render failed. MPS backend out of memory (MPS allocated: 26.46 GiB, other allocations: 5.46 GiB)"),
    ("failed", "mac-vega-a can't run this: node H3FaceTrackCrop is not installed"),
]


async def main():
    posted = []
    app = web.Application()

    async def prompt(q):            # this PC's own ComfyUI
        b = await q.json()
        posted.append(b)
        return web.json_response({"prompt_id": f"back-{len(posted)}", "number": -len(posted)})
    app.router.add_post("/prompt", prompt)
    run = web.AppRunner(app)
    await run.setup()
    site = web.TCPSite(run, "127.0.0.1", 0)
    await site.start()
    ps.port = site._server.sockets[0].getsockname()[1]

    # --- V17: every ending x both spaces
    k = 0
    for private in (False, True):
        for status, error in ENDINGS:
            k += 1
            pid = f"moved-{k}"
            orig = {"1": {"class_type": "LoraLoader", "inputs": {"lora_name": "Civitai\\Qwen\\Qwen_Snofs_1_3.safetensors"}}, "n": k}
            extra = {"client_id": "browser-7", "manny_app": "Manny Studio - Image.app.json"}
            studio._moved_put(pid, orig, extra, private)
            n = len(posted)
            job = {"prompt_id": pid, "worker": "mac-vega-a", "status": status, "error": error}
            await studio._put_back(job)
            back = posted[n:]
            kept = pid in studio._moved_load(private)
            tag = f"{'Incognito' if private else 'normal'} job {status}{' (' + error.split('. ')[-1][:40] + ')' if error else ''}"
            if status == "failed":
                b = back[0] if back else {}
                ok = (len(back) == 1 and b.get("prompt") == orig and b.get("front") is True and b.get("client_id") == "browser-7"
                      and (b.get("extra_data") or {}).get("manny_app") == extra["manny_app"]
                      and "put back in this PC's queue" in (job.get("error") or "") and job.get("moved_back") == f"back-{len(posted)}"
                      and (not private or job["moved_back"] in studio._pending_private) and not kept)
                check(ok, f"{tag}: back at the front of this PC's queue, unchanged ({len(back)} post, private kept private {not private or job.get('moved_back') in studio._pending_private})")
            else:
                check(not back and not kept, f"{tag}: the kept original is dropped, nothing queued again ({len(back)} posts, kept {kept})")
    # a job that wasn't moved (no kept original) never posts
    n = len(posted)
    await studio._put_back({"prompt_id": "never-moved", "worker": "mac-vega-a", "status": "failed", "error": "x"})
    check(len(posted) == n, "a job that wasn't moved here is never queued here")

    # --- V20: three jobs needing one file at once -> one push
    calls = []
    real = remote._push_file

    async def slow_push(w, folder, value, path, job):
        calls.append(value)
        await asyncio.sleep(0.3)
    remote._push_file = slow_push
    remote.LOCAL_MODEL = lambda folder, value: __file__
    w = {"label": "mac-vega-a", "host": "127.0.0.1", "port": 1}
    await asyncio.gather(*(remote._push(w, {}, "loras", v) for v in ("Civitai\\Qwen\\x.safetensors", "Civitai/Qwen/x.safetensors", "Civitai\\Qwen\\x.safetensors")))
    check(calls == ["Civitai\\Qwen\\x.safetensors"], f"three jobs needing one file (both spellings) push it once: {calls}")
    calls.clear()
    await remote._push(w, {}, "loras", "Civitai/Qwen/x.safetensors")
    check(calls == ["Civitai/Qwen/x.safetensors"], "a later job needing it again asks again (the worker answers present at once if it has it)")
    remote._push_file = real
    await run.cleanup()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
