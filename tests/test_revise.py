"""Critique fixes (confer 6fa54bc5 Revise), one check per finding, so none can be half done.

  LIVE=127.0.0.1:8189 ~/code/comfy-mac/.venv/bin/python tests/test_revise.py

C1 pulled-back files never overwrite files on this PC        (live)
C2 jobs sharing a reference file on one worker all succeed   (live)
K2 a failed job is forgotten on the worker too               (live)
C7 uploads don't leak file descriptors                        (live)
K3 a worker that restarted (job in neither history nor queue) fails the job instead of waiting forever  (fake worker)
C5 a cancel that can't reach the worker doesn't leave the job "cancelling"                               (fake worker)
C6 remote jobs survive a Studio restart: persisted, resumed, pulled back                                  (fake worker)
C8 no studio_job tag on output classes that take any input (self_validated "*")                           (fake worker)
K4 auto counts a worker running one of our jobs for >10 min as busier                                     (unit)
K5 every output file comes back, whatever key the node reports it under                                   (unit)
C4 and C9 are route-level checks in test_run_route.py.
"""
import asyncio, importlib, json, os, sys, tempfile, time, types

import aiohttp
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probes import make_inputs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.modules["studio"] = types.ModuleType("studio")
sys.modules["studio"].__path__ = [os.path.join(ROOT, "ComfyUI-MannyStudio")]
remote = importlib.import_module("studio.remote")
_live = os.environ.get("LIVE", "127.0.0.1:8189")
LIVE = None if _live == "none" else {"label": "live", "host": _live.rsplit(":", 1)[0], "port": int(_live.rsplit(":", 1)[1])}
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


def img2img(seed, image="studio_test/in.png [input]", prefix="studio_test/rv"):
    return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1-5-pruned-emaonly-fp16.safetensors"}},
            "2": {"class_type": "LoadImage", "inputs": {"image": image}},
            "3": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["1", 2]}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "a red apple", "clip": ["1", 1]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
            "6": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["3", 0],
                                                       "seed": seed, "steps": 2, "cfg": 5.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 0.5}},
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
            "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": prefix}}}


async def wait(job, t=900):
    await asyncio.wait_for(job["task"], t)


# ---------------------------------------------------------------- live
async def live(dirs):
    w = LIVE
    # C1: two different jobs; the worker forgets each, so both come back under the same name
    a = remote.JOBS[(await remote.submit(w, img2img(101), {"client_id": "rv-c1"}, dirs))["prompt_id"]]
    await wait(a)
    b = remote.JOBS[(await remote.submit(w, img2img(202), {"client_id": "rv-c1"}, dirs))["prompt_id"]]
    await wait(b)
    pa, pb = a["outputs"][0]["path"], b["outputs"][0]["path"]
    check(pa != pb and os.path.isfile(pa) and os.path.isfile(pb) and open(pa, "rb").read() != open(pb, "rb").read()
          and b["outputs"][0]["filename"] == os.path.basename(pb),
          f"C1 two pulled-back files both kept: {os.path.basename(pa)}, {os.path.basename(pb)} (outputs name the file as saved here)")

    # C2: three jobs at once, same reference, same worker
    jobs = [remote.JOBS[(await remote.submit(w, img2img(300 + k), {"client_id": "rv-c2"}, dirs))["prompt_id"]] for k in range(3)]
    for j in jobs:
        await wait(j)
    check([j["status"] for j in jobs] == ["done"] * 3 and len({j["inputs"][0] for j in jobs}) == 3
          and all(j["inputs"][0].startswith("studio/") for j in jobs),
          f"C2 three jobs sharing a reference: {[(j['status'], j['inputs'], (j['error'] or '')[:80]) for j in jobs]}")

    # K2: a job that fails while running (1/0 in a math node) after its upload: forgotten anyway
    api = img2img(400)
    api["9"] = {"class_type": "ComfyMathExpression", "inputs": {"expression": "a / 0", "values.a": 1}}
    api["10"] = {"class_type": "PreviewAny", "inputs": {"source": ["9", 0]}}
    j = remote.JOBS[(await remote.submit(w, api, {"client_id": "rv-k2"}, dirs))["prompt_id"]]
    await wait(j)
    fg = j.get("forgot") or {}
    async with aiohttp.ClientSession() as s:
        async with s.get(remote.base(w) + f"/history/{j['prompt_id']}") as r:
            hist = await r.json()
    check(j["status"] == "failed" and "warning" not in fg and any(x.startswith("input/studio/") for x in fg.get("deleted", []))
          and j["prompt_id"] not in hist, f"K2 failed job forgotten: {j['status']} ({(j['error'] or '')[:70]}), forget {json.dumps(fg)[:160]}")

    # C7: uploads close their files
    before = len(os.listdir("/dev/fd"))
    async with aiohttp.ClientSession() as s:
        oi, _sv = await remote.caps(w)
        for _ in range(5):
            await remote._upload_inputs(s, w, img2img(1), oi, dirs, "rvfd" + os.urandom(3).hex())
    after = len(os.listdir("/dev/fd"))
    check(after <= before, f"C7 open files before/after 5 uploads: {before} -> {after}")
    async with aiohttp.ClientSession() as s:      # those uploads belong to no job: drop them
        await remote.forget(s, w, "rv-fd-cleanup", [])


# ---------------------------------------------------------------- fake worker
class Fake:
    def __init__(self):
        self.history, self.queue, self.posted, self.forgot, self.sv = {}, {"queue_running": [], "queue_pending": []}, [], [], {}
        self.app = web.Application()
        r = self.app.router
        r.add_get("/object_info", lambda q: web.json_response({
            "LoadImage": {"input": {"required": {"image": [[], {"image_upload": True}]}}, "output_node": False},
            "Keep": {"input": {"required": {"images": ["IMAGE", {}]}}, "output_node": True},
            "AnyOut": {"input": {"required": {}}, "output_node": True}}))
        r.add_get("/mac/info", lambda q: web.json_response({"label": "fake", "queue_remaining": 0, "self_validated": self.sv}))
        r.add_get("/history/{pid}", lambda q: web.json_response({k: v for k, v in self.history.items() if k == q.match_info["pid"]}))
        r.add_get("/queue", lambda q: web.json_response(self.queue))
        r.add_get("/view", lambda q: web.Response(body=b"PNG-" + q.query["filename"].encode()))
        r.add_post("/prompt", self.prompt)
        r.add_post("/mac/forget", self.forget)
        r.add_post("/history", lambda q: web.json_response({}))
        r.add_post("/interrupt", lambda q: web.json_response({}))
        r.add_post("/upload/image", lambda q: web.json_response({"name": "x.png", "subfolder": "", "type": "input"}))

    async def prompt(self, q):
        self.posted.append(await q.json())
        return web.json_response({"prompt_id": f"p{len(self.posted)}", "number": len(self.posted)})

    async def forget(self, q):
        b = await q.json()
        self.forgot.append(b)
        return web.json_response({"prompt_id": b["prompt_id"], "deleted": [], "refused": []})

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return {"label": "fake", "host": "127.0.0.1", "port": self.port}


def jobrec(pid, worker, status="running"):
    return {"prompt_id": pid, "worker": worker, "status": status, "private": False, "inputs": ["studio/abc/x.png"], "outputs": [],
            "error": None, "queued_at": time.time(), "app": "t", "label": None, "client_id": None, "progress": 0.0, "step": None}


async def fake(dirs, data_dir):
    f = Fake()
    w = await f.start()
    keep = remote.LOST_AFTER

    # K3: the worker restarted: the job is in neither /history nor /queue
    j = jobrec("gone1", "fake")
    await asyncio.wait_for(remote._follow(w, j, dirs, ".private_incoming", None, poll=0.05), 30)
    check(j["status"] == "failed" and "restarted" in (j["error"] or "") and any(x["prompt_id"] == "gone1" for x in f.forgot),
          f"K3 job lost to a worker restart -> {j['status']}: {j['error']}; forgot {[x['prompt_id'] for x in f.forgot]}")

    # ...but a job still in the queue keeps waiting (no false "lost")
    f.queue = {"queue_running": [[1, "busy1", {}, {}, []]], "queue_pending": []}
    j2 = jobrec("busy1", "fake")
    t = asyncio.ensure_future(remote._follow(w, j2, dirs, ".private_incoming", None, poll=0.05))
    await asyncio.sleep(1.0)
    check(not t.done() and j2["status"] == "running", f"K3 a queued job is not declared lost: {j2['status']}")
    f.queue = {"queue_running": [], "queue_pending": []}
    f.history["busy1"] = {"status": {"status_str": "success", "completed": True},
                          "outputs": {"9": {"images": [{"filename": "ok.png", "subfolder": "", "type": "output"}]}}}
    await asyncio.wait_for(t, 30)
    check(j2["status"] == "done", f"K3 ...and finishes normally once it's in history: {j2['status']}")

    # C5: cancel with the worker unreachable: the job ends "failed", not stuck in "cancelling"
    dead = {"label": "dead", "host": "127.0.0.1", "port": 9}
    remote.JOBS["c5"] = jobrec("c5", "dead")
    try:
        await remote.cancel(dead, "c5", settle=1)
        check(False, "C5 cancel against an unreachable worker should raise")
    except Exception:
        pass
    check(remote.JOBS["c5"]["status"] == "failed" and "cancel" in (remote.JOBS["c5"]["error"] or "").lower(),
          f"C5 unreachable cancel -> {remote.JOBS['c5']['status']}: {remote.JOBS['c5']['error']}")
    del remote.JOBS["c5"]

    # C6: a job persisted by a previous Studio run is resumed, pulled back and forgotten
    remote.configure(data_dir)
    f.history["old1"] = {"status": {"status_str": "success", "completed": True},
                         "outputs": {"9": {"images": [{"filename": "old.png", "subfolder": "Images", "type": "output"}]}}}
    with open(os.path.join(data_dir, "workers.json"), "w") as fh:
        json.dump({"workers": [w]}, fh)
    with open(remote.state_file(), "w") as fh:
        json.dump([jobrec("old1", "fake"), jobrec("done1", "fake", "done")], fh)
    remote.JOBS.clear()
    n = remote.resume(dirs, ".private_incoming")
    check(n == 1 and "old1" in remote.JOBS, f"C6 resume picks up the 1 unfinished job: {n}")
    await asyncio.wait_for(remote.JOBS["old1"]["task"], 30)
    got = os.path.join(dirs["output"], "Images", "old.png")
    saved = json.load(open(remote.state_file()))
    check(remote.JOBS["old1"]["status"] == "done" and os.path.isfile(got) and any(x["prompt_id"] == "old1" for x in f.forgot)
          and next(x for x in saved if x["prompt_id"] == "old1")["status"] == "done",
          f"C6 resumed job pulled back to {os.path.relpath(got, dirs['output'])} and saved as done")

    # C8: no tag on an output class that takes any input
    f.sv = {"AnyOut": ["*"]}
    await remote.caps(w, force=True)
    api = {"1": {"class_type": "AnyOut", "inputs": {}}, "2": {"class_type": "Keep", "inputs": {"images": ["1", 0]}}}
    r = await remote.submit(w, api, {"client_id": None}, dirs)
    await remote.cancel(w, r["prompt_id"], settle=1)
    sent = f.posted[-1]["prompt"]
    check("studio_job" not in sent["1"]["inputs"] and "studio_job" in sent["2"]["inputs"],
          f"C8 tag skipped on self_validated '*' outputs: AnyOut {sorted(sent['1']['inputs'])}, Keep {sorted(sent['2']['inputs'])}")
    remote.LOST_AFTER = keep
    remote.configure(None)
    await f.runner.cleanup()


# ---------------------------------------------------------------- unit
async def units():
    # K4
    real_check, real_info = remote.check, remote.info

    async def ok(w, api):
        return []

    async def idle(w):
        return {"queue_remaining": 1}
    remote.check, remote.info = ok, idle
    remote.JOBS["k4"] = dict(jobrec("k4", "a"), started_at=time.time() - 900)
    try:
        lab, _ = await remote.pick([{"label": "a"}, {"label": "b"}], {}, 5)
        check(lab == "b", f"K4 a worker 15 min into our job counts busier (a=1+1, b=1) -> {lab}")
    finally:
        remote.check, remote.info = real_check, real_info
        del remote.JOBS["k4"]
    # K5
    h = {"outputs": {"9": {"3d": [{"filename": "m.glb", "subfolder": "3D", "type": "output"}], "text": ["hello"],
                           "images": [{"filename": "p.png", "subfolder": "", "type": "temp"}, {"filename": "q.png", "subfolder": "", "type": "output"}],
                           "animated": [True]}}}
    names = sorted(f["filename"] for f in remote._outputs(h))
    check(names == ["m.glb", "q.png"], f"K5 every output file, any key: {names}")


def main():
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-revise-"))
    dirs = {k: os.path.join(tmp, k) for k in ("input", "output", "temp")}
    for d in dirs.values():
        os.makedirs(d)
    data_dir = os.path.join(tmp, "manny_studio")
    os.makedirs(data_dir)
    make_inputs(dirs)

    async def all_():
        await units()
        await fake(dirs, data_dir)
        if LIVE:
            await live(dirs)
    asyncio.run(all_())
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)


main()
