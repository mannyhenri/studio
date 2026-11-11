"""Remote workers: gate, and the gate vs each worker's own validation over EVERY Studio app, then a real round trip.

Needs live ComfyUI workers and aiohttp (comfy-mac's venv has it):
  WORKERS=127.0.0.1:8188,127.0.0.1:8189 ~/code/comfy-mac/.venv/bin/python tests/test_remote.py

1. gate() unit cases: both COMBO formats, missing node, wrong folder, file inputs exempt, links skipped.
2. For every app in workflows/*.app.json x every worker: the prompt Studio builds (appwf.build_app_prompt) is
   gated, and the verdict must equal the worker's own /prompt validation (accepted jobs are removed at once).
   Every app the gate passes is then rendered for real through remote.submit and pulled back.
3. A small img2img job through remote.submit on every worker, public and private: the input is uploaded, the
   output lands in a local output folder (private under the incoming folder), and the worker forgets the job.
"""
import asyncio, glob, importlib, json, os, sys, tempfile, types

import aiohttp

from probes import make_inputs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "ComfyUI-MannyStudio")
# import the pack's pure modules without running its __init__ (that one needs a running ComfyUI)
sys.modules["studio"] = types.ModuleType("studio")
sys.modules["studio"].__path__ = [PKG]
remote = importlib.import_module("studio.remote")
appwf = importlib.import_module("studio.appwf")

WORKERS = [{"label": f"w{p}", "host": h, "port": int(p)} for h, p in
           (x.rsplit(":", 1) for x in os.environ.get("WORKERS", "127.0.0.1:8188,127.0.0.1:8189").split(","))]
APPS = sorted(glob.glob(os.path.join(ROOT, "workflows", "*.app.json")))
fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def unit_required():
    """ComfyUI's required_input_missing, mirrored: dynamic "<name>.<child>" keys count, autogrow needs template.min."""
    oi = {"Rs": {"input": {"required": {"input": ["COMFY_MATCHTYPE_V3", {}], "resize_type": ["COMFY_DYNAMICCOMBO_V3", {}]}}},
          "Bi": {"input": {"required": {"images": ["COMFY_AUTOGROW_V3", {"template": {"min": 1}}]}}},
          "Te": {"input": {"required": {"images": ["COMFY_AUTOGROW_V3", {"template": {"min": 0}}]}}},
          "Mx": {"input": {"required": {"values": ["COMFY_AUTOGROW_V3", {"template": {}}], "expression": ["STRING", {}]}}}}
    ok = {"1": {"class_type": "Rs", "inputs": {"input": ["9", 0], "resize_type": "scale", "resize_type.longer_size": 1536}},
          "2": {"class_type": "Bi", "inputs": {"images.image0": ["9", 0]}},
          "3": {"class_type": "Te", "inputs": {}},
          "4": {"class_type": "Mx", "inputs": {"expression": "a", "values.a": 1}}}
    check(remote.gate(ok, oi) == [], "gate: required inputs present (dynamic keys count, autogrow min 0 may be empty)")
    bad = {"1": {"class_type": "Rs", "inputs": {"resize_type": "scale", "resize_type.longer_size": 1536}},
           "2": {"class_type": "Bi", "inputs": {}},
           "4": {"class_type": "Mx", "inputs": {"values.a": 1}}}
    why = remote.gate(bad, oi)
    check(sorted((w["node"], w.get("input")) for w in why) == [("1", "input"), ("2", "images"), ("4", "expression")],
          f"gate: required_input_missing + autogrow below min refused: {[w['reason'] for w in why]}")


def unit():
    oi = {"Ck": {"input": {"required": {"ckpt": [["a.safetensors", "b.safetensors"], {}]}}},
          "Un": {"input": {"required": {"unet": ["COMBO", {"options": ["u.safetensors"]}], "w": ["INT", {}]}}},
          "Img": {"input": {"required": {"image": ["COMBO", {"options": [], "image_upload": True}]}}},
          "Vid": {"input": {"required": {"file": [["x.mp4"], {"video_upload": True}]}}},
          "Opt": {"input": {"optional": {"s": [["euler"]]}}},
          "CustomCombo": {"input": {"required": {"choice": ["COMBO", {"options": []}]}}},
          "Cv": {"input": {"required": {"clip_name": [[], {}]}}}}
    ok = {"1": {"class_type": "Ck", "inputs": {"ckpt": "a.safetensors"}},
          "2": {"class_type": "Un", "inputs": {"unet": "u.safetensors", "w": 3}},
          "3": {"class_type": "Img", "inputs": {"image": "manny/anything.png [input]"}},
          "4": {"class_type": "Vid", "inputs": {"file": "nope.mp4"}},
          "5": {"class_type": "Opt", "inputs": {"s": ["1", 0]}},
          "6": {"class_type": "CustomCombo", "inputs": {"choice": "2x frames (24 → 48 fps)"}}}
    check(remote.gate(ok, oi) == [], "gate: legacy + 0.44 COMBO values, file inputs, links, self-validated CustomCombo pass")
    check(remote.gate(ok, oi, {}) != [], "gate: without the self_validated entry the CustomCombo choice is refused (worker's list is used)")
    bad = {"1": {"class_type": "Ck", "inputs": {"ckpt": "u.safetensors"}},
           "2": {"class_type": "Un", "inputs": {"unet": "a.safetensors", "w": 3}},
           "3": {"class_type": "Gone", "inputs": {}},
           "4": {"class_type": "Opt", "inputs": {"s": "dpm"}},
           "5": {"class_type": "Cv", "inputs": {"clip_name": "clip_vision_h.safetensors"}}}
    why = remote.gate(bad, oi)
    check(sorted((w["node"], w.get("input")) for w in why) == [("1", "ckpt"), ("2", "unet"), ("3", None), ("4", "s"), ("5", "clip_name")],
          f"gate: refuses wrong-folder files (legacy + 0.44), missing node, bad optional combo, empty folder: {[w['reason'] for w in why]}")


async def oi_of(s, w):
    async with s.get(remote.base(w) + "/object_info") as r:
        return await r.json()


def probe_files(api, oi):
    """Point every file input at a generated local file of the right kind (Studio uploads them with the job)."""
    for node in api.values():
        o = oi.get(node.get("class_type"))
        for k, v in list(node.get("inputs", {}).items()):
            spec = remote._spec(o, k)
            if isinstance(v, str) and remote.is_file_input(spec):
                flags = remote.combo(spec)[1]
                node["inputs"][k] = ("studio_test/probe.mp4" if flags.get("video_upload") else
                                     "studio_test/probe.wav" if flags.get("audio_upload") else "studio_test/probe.png")
    return api


async def worker_accepts(s, w, api, oi, dirs):
    api = json.loads(json.dumps(api))
    sent = await remote._upload_inputs(s, w, api, oi, dirs)
    async with s.post(remote.base(w) + "/prompt", json={"prompt": api, "client_id": "studio-gate-test"}) as r:
        d = await r.json(content_type=None)
    if r.status == 200:     # queued: take it straight back out (it may already have started), then forget it
        await remote.cancel(w, d["prompt_id"], inputs=sent)
        return not d.get("node_errors"), d   # a 200 with node_errors runs only the outputs that validated: a refusal
    if sent:                # never queued: forget an unknown id just to drop the uploads
        await remote.forget(s, w, "studio-probe-" + os.urandom(6).hex(), sent)
    return False, d


async def app_set(dirs):
    check(len(APPS) == 9, f"app set: {len(APPS)} apps in workflows/*.app.json")
    eligible = []
    async with aiohttp.ClientSession() as s:
        for w in WORKERS:
            oi, sv = await remote.caps(w, force=True)
            for path in APPS:
                name = os.path.basename(path)
                wf, api = appwf.build_app_prompt(path, {}, oi)
                api = probe_files(api, oi)
                why = remote.gate(api, oi, sv)
                acc, d = await worker_accepts(s, w, api, oi, dirs)
                if not why:
                    eligible.append((w, name, wf, api))
                detail = "eligible" if not why else f"refused ({len(why)}: {'; '.join(x['reason'] for x in why[:3])}{' …' if len(why) > 3 else ''})"
                worker_says = "accepts" if acc else "rejects: " + json.dumps(d.get("node_errors") or d.get("error"))[:200]
                check((not why) == acc, f"{w['label']} {name}: gate {detail} / worker {worker_says}")
                if why and not acc:   # same (node, input) set as the worker's own refusal
                    mine = {(x["node"], x.get("input")) for x in why}
                    theirs = {(n, e.get("extra_info", {}).get("input_name")) for n, ne in (d.get("node_errors") or {}).items() for e in ne.get("errors", [])}
                    check(mine == theirs, f"{w['label']} {name}: refused (node,input) sets equal ({len(mine)} vs {len(theirs)}){'' if mine == theirs else ' gate-only ' + str(mine - theirs) + ' worker-only ' + str(theirs - mine)}")
    return eligible


RENDER = set(os.environ.get("RENDER_APPS", "Studio - Remove background.app.json,Studio - Smooth motion.app.json").split(","))


async def render_eligible(eligible, dirs):
    """The eligible apps in RENDER_APPS, rendered for real on that worker and pulled back here (the gate==worker
    check above already covers every eligible app; LTX video takes ~15 min a card, so it renders only on request)."""
    for w, name, wf, api in eligible:
        if name not in RENDER:
            continue
        r = await remote.submit(w, api, {"client_id": "studio-test", "extra_data": {"manny_app": name}}, dirs)
        job = remote.JOBS[r["prompt_id"]]
        await asyncio.wait_for(job["task"], 1800)
        outs = [o["path"] for o in job["outputs"]]
        ok = job["status"] == "done" and outs and all(os.path.getsize(p) > 0 for p in outs) and "warning" not in (job.get("forgot") or {})
        check(ok, f"{w['label']} {name}: rendered on the worker, {len(outs)} file(s) pulled back {[os.path.basename(p) for p in outs]}, forgot {len((job.get('forgot') or {}).get('deleted') or [])} {job['error'] or ''}")
    check(bool(eligible), f"at least one app eligible to render: {[(w['label'], n) for w, n, *_ in eligible]}")


def img2img(ckpt):
    return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "2": {"class_type": "LoadImage", "inputs": {"image": "studio_test/in.png [input]"}},
            "3": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["1", 2]}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "a red apple", "clip": ["1", 1]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
            "6": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["3", 0],
                                                       "seed": 1, "steps": 4, "cfg": 5.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 0.5}},
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
            "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "studio_test/rt"}}}


async def round_trip(dirs):
    for w in WORKERS:
        oi, _sv = await remote.caps(w, force=True)
        ckpt = "v1-5-pruned-emaonly-fp16.safetensors"
        check(ckpt in remote.combo(remote._spec(oi["CheckpointLoaderSimple"], "ckpt_name"))[0], f"{w['label']}: has {ckpt}")
        for private in (False, True):
            api = img2img(ckpt)
            api["6"]["inputs"]["seed"] = int.from_bytes(os.urandom(4), "big")   # a fresh seed: ComfyUI's cache would skip sampling
            if private:   # what Studio's _privatize does: save under the incoming folder
                api["8"]["inputs"]["filename_prefix"] = ".private_incoming/studio_test/rt"
            r = await remote.submit(w, api, {"client_id": f"studio-test-{w['label']}-{private}", "extra_data": {"manny_app": "test"}}, dirs, private)
            job = remote.JOBS[r["prompt_id"]]
            await asyncio.wait_for(job["task"], 600)
            tag = f"{w['label']} {'private' if private else 'public'}"
            check(job["status"] == "done", f"{tag}: job {job['status']} {job['error'] or ''}")
            check(job["step"] == "Step 4/4" and job["progress"] == 1.0, f"{tag}: progress from the worker's /ws: {job['step']}")
            outs = [o["path"] for o in job["outputs"]]
            inside = os.path.join(dirs["output"], ".private_incoming") if private else dirs["output"]
            check(len(outs) == 1 and os.path.getsize(outs[0]) > 0 and outs[0].startswith(inside + os.sep),
                  f"{tag}: output pulled back to {outs}")
            check(job["inputs"] == ["studio_test/in.png"], f"{tag}: uploaded {job['inputs']}")
            fg = job.get("forgot") or {}
            check(bool(fg.get("deleted")) and "warning" not in fg, f"{tag}: worker forgot the job: {json.dumps(fg)[:200]}")
            async with aiohttp.ClientSession() as s:
                async with s.get(remote.base(w) + f"/history/{r['prompt_id']}") as hr:
                    check(r["prompt_id"] not in (await hr.json()), f"{tag}: history entry gone on the worker")


async def refused_submit():
    w = WORKERS[0]
    api = img2img("not-a-model.safetensors")
    try:
        await remote.submit(w, api, {"client_id": "t"}, {"input": "/nonexistent", "output": "/nonexistent", "temp": "/x"})
        check(False, "submit refuses a model the worker lacks")
    except remote.GateRefused as e:
        check(e.why and e.why[0]["input"] == "ckpt_name", f"submit refuses before upload/queue: {e}")


async def node_errors_refusal(dirs):
    """A gate miss must not become a silent half-render: ComfyUI queues the job when ANY output validates and lists
    the failed nodes in the 200 reply. Gate switched off, one valid output (a copy of the input) + one broken one."""
    w = WORKERS[0]
    api = img2img("not-a-model.safetensors")
    api["9"] = {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "studio_test/copy"}}
    real, real_cancel, cancelled = remote.gate, remote.cancel, []

    async def spy_cancel(w, pid, **k):
        cancelled.append(pid)
        return await real_cancel(w, pid, **k)
    remote.gate, remote.cancel = (lambda *a, **k: []), spy_cancel
    try:
        await remote.submit(w, api, {"client_id": "studio-test-ne"}, dirs)
        check(False, "submit with node_errors in a 200 reply must refuse")
    except remote.GateRefused as e:
        check(any(x["node"] == "1" and x["input"] == "ckpt_name" for x in e.why), f"200 + node_errors -> refused: {e}")
    finally:
        remote.gate, remote.cancel = real, real_cancel
    async with aiohttp.ClientSession() as s:
        async with s.get(remote.base(w) + "/queue") as r:
            q = await r.json()
    check(len(cancelled) == 1 and not any(it[1] in cancelled for it in q["queue_running"] + q["queue_pending"]),
          f"the half-valid job {cancelled} was taken back out of the worker's queue")


async def lost_contact():
    """A worker that stops answering while it has our job: the job fails after LOST_AFTER instead of waiting forever."""
    keep = remote.LOST_AFTER
    remote.LOST_AFTER = 2
    w = {"label": "gone", "host": "127.0.0.1", "port": 9}
    job = {"prompt_id": "x", "worker": "gone", "status": "queued", "private": False, "inputs": [], "outputs": [], "error": None,
           "client_id": None, "progress": 0.0, "step": None}
    try:
        await asyncio.wait_for(remote._follow(w, job, {}, ".private_incoming", None, poll=0.2), 30)
    finally:
        remote.LOST_AFTER = keep
    check(job["status"] == "failed" and "Lost contact" in (job["error"] or ""), f"lost worker -> {job['status']}: {job['error']}")


async def repeat_job(dirs):
    """The same job twice: ComfyUI's cache must not hand back the first job's files, which forget has deleted."""
    for w in WORKERS:
        got = []
        for k in range(2):
            api = img2img("v1-5-pruned-emaonly-fp16.safetensors")
            api["6"]["inputs"]["seed"] = 1234
            r = await remote.submit(w, api, {"client_id": f"studio-test-repeat-{w['label']}"}, dirs)
            job = remote.JOBS[r["prompt_id"]]
            await asyncio.wait_for(job["task"], 600)
            got.append((job["status"], [os.path.isfile(o["path"]) for o in job["outputs"]], job["error"]))
        check(all(st == "done" and files == [True] for st, files, _e in got), f"{w['label']}: identical job twice, both pulled back: {got}")


def main():
    tmp = tempfile.mkdtemp(prefix="studio-remote-")
    dirs = {k: os.path.join(os.path.realpath(tmp), k) for k in ("input", "output", "temp")}
    for d in dirs.values():
        os.makedirs(d)
    make_inputs(dirs)
    unit()
    unit_required()

    async def live():
        eligible = await app_set(dirs)
        await lost_contact()
        await refused_submit()
        await node_errors_refusal(dirs)
        await round_trip(dirs)
        await repeat_job(dirs)
        await render_eligible(eligible, dirs)
    asyncio.run(live())
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
