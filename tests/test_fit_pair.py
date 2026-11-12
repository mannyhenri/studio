"""Fit of Studio with the two-Vega work (confer d77f67a6, rows W7 and W8).

  ~/code/comfy-mac/.venv/bin/python tests/test_fit_pair.py        (no workers needed)

W7: the text encoder on the other card is worker-to-worker only. NO Studio source file (every .py and .js in the pack)
may call /mac/te; Studio only reads te_for from /mac/info.
W8: what a Mac card fails with reaches Studio's job list unchanged. For EVERY failure text the Mac can now produce
(comfy-mac's lora_guard refusal, an MPS out-of-memory, the Metal buffer limit, a missing node), a job that fails with it
on a worker ends "failed" with that exact text in its error, so Studio's queue, the PC agent and the put-back show it.
"""
import asyncio, glob, importlib, os, re, sys, types

from aiohttp import web

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.environ.get("STUDIO_PKG") or os.path.join(ROOT, "ComfyUI-MannyStudio")
sys.modules["studio"] = types.ModuleType("studio")
sys.modules["studio"].__path__ = [PKG]
remote = importlib.import_module("studio.remote")
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


FAILURES = [
    "2136 of this LoRA's model keys didn't load on this Mac (ComfyUI 0.44: lora key not loaded): it would render only part "
    "of the LoRA, unlike the PC. Render it on the PC",
    "MPS backend out of memory (MPS allocated: 26.46 GiB, other allocations: 5.46 GiB, max allowed: 30.39 GiB). Tried to allocate 128.00 KiB",
    "Invalid buffer size: 4.83 GiB",
    "Cannot execute because node H3FaceTrackCrop does not exist.",
]


async def main():
    # --- W7: no Studio code reaches /mac/te
    files = sorted(glob.glob(os.path.join(PKG, "**", "*.py"), recursive=True) + glob.glob(os.path.join(PKG, "web", "*.js")))
    hits = [f"{os.path.relpath(f, ROOT)}:{n}" for f in files for n, line in enumerate(open(f, encoding="utf-8", errors="ignore"), 1)
            if re.search(r"/mac/te\b", line)]
    check(len(files) > 10 and not hits, f"no Studio source calls /mac/te ({len(files)} files scanned): {hits}")

    # --- W8: a worker's failure text reaches the job unchanged
    hist = {}
    app = web.Application()

    async def history(q):
        pid = q.match_info["pid"]
        return web.json_response({pid: hist[pid]} if pid in hist else {})
    app.router.add_get("/history/{pid}", history)
    app.router.add_get("/queue", lambda q: web.json_response({"queue_running": [], "queue_pending": []}))
    app.router.add_post("/mac/forget", lambda q: web.json_response({"deleted": []}))
    app.router.add_post("/history", lambda q: web.json_response({}))
    run = web.AppRunner(app)
    await run.setup()
    site = web.TCPSite(run, "127.0.0.1", 0)
    await site.start()
    w = {"label": "mac-vega-a", "host": "127.0.0.1", "port": site._server.sockets[0].getsockname()[1]}
    remote.configure(None)
    for k, text in enumerate(FAILURES):
        pid = f"fail-{k}"
        hist[pid] = {"status": {"status_str": "error", "completed": False,
                                "messages": [["execution_start", {}], ["execution_error", {"exception_message": text, "node_id": "3"}]]},
                     "outputs": {}}
        job = {"prompt_id": pid, "worker": w["label"], "status": "queued", "private": False, "inputs": [], "outputs": [],
               "error": None, "queued_at": 0, "client_id": None, "progress": 0.0, "step": None}
        ended = []
        await remote._follow(w, job, {"input": "/tmp", "output": "/tmp", "temp": "/tmp"}, ".private_incoming", ended.append, poll=0.01)
        check(job["status"] == "failed" and text in (job.get("error") or "") and ended == [job],
              f"a card failing with “{text[:50]}…” -> job failed, the text unchanged in its error, on_done told")
    await run.cleanup()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
