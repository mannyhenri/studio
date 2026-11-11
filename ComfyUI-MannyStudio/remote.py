"""Remote GPU workers: other ComfyUI machines (the Mac Pro's two Vega II workers) that Studio can render on.

A worker is {label, host, port, device?}, kept in manny_studio/workers.json (same entry shape as comfy-mac's
mac-workers.json, so its "workers" list can be pasted in as is). "local" is this PC's own ComfyUI.

What a worker can run is read LIVE from its /object_info, never from a manifest. gate() refuses a prompt unless
every node exists on the worker and every string value sent to a COMBO input is one of that input's options
(models, samplers, schedulers; the folder is checked too, a VAE file in a UNet slot fails; an empty folder
refuses everything). Like ComfyUI's own validation, inputs a node validates itself are skipped: the worker's
/mac/info lists them as "self_validated" {class: [input, ...] or ["*"]}; a worker without /mac/info (and this PC)
uses SELF_VALIDATED. File inputs (image/video/audio upload COMBOs) are always skipped: Studio uploads those.

A remote job: upload inputs -> POST /prompt on the worker -> poll /history -> download every output through /view
into this PC's output folder (private jobs into the incoming folder the sweeper moves) -> POST /mac/forget so
nothing stays on the worker. No ComfyUI imports here: the paths come in as arguments.
"""
import asyncio
import json
import os
import time

from aiohttp import ClientSession, ClientTimeout, FormData

LOCAL = "local"
AUTO = "auto"
CAPS_TTL = 120
FILE_FLAGS = ("image_upload", "video_upload", "audio_upload")
# ComfyUI core classes whose inputs skip the COMBO check (validate_inputs / **kwargs), for workers without /mac/info
SELF_VALIDATED = {"CustomCombo": ["*"], "LoadImage": ["image"], "LoadImageMask": ["image"], "LoadImageOutput": ["image"],
                  "LoadVideo": ["file"], "LoadAudio": ["audio"], "LoadLatent": ["latent"], "Load3D": ["*"], "Load3DAdvanced": ["*"]}
_caps = {}      # label -> {"at": t, "data": object_info}
JOBS = {}       # remote prompt_id -> job dict (see submit)
JOBS_UPLOADING = {}     # label -> jobs still uploading inputs (not in the worker's queue yet)
KEEP_DONE = 3600        # finished jobs stay listed this long


# ---------------------------------------------------------------- registry
def workers_file(data_dir):
    return os.path.join(data_dir, "workers.json")


def load_workers(data_dir):
    """Configured remote workers. Accepts a plain list or comfy-mac's {"workers": [...]} manifest."""
    try:
        with open(workers_file(data_dir), encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    items = d.get("workers", []) if isinstance(d, dict) else d
    out = []
    for w in items if isinstance(items, list) else []:
        if isinstance(w, dict) and w.get("label") and w.get("host") and w.get("port") and w["label"] != LOCAL:
            out.append({"label": str(w["label"]), "host": str(w["host"]), "port": int(w["port"]), "device": w.get("device") or {}})
    return out


def save_workers(data_dir, workers):
    tmp = workers_file(data_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"workers": workers}, f, indent=1)
    os.replace(tmp, workers_file(data_dir))


def find(data_dir, label):
    w = next((w for w in load_workers(data_dir) if w["label"] == label), None)
    if not w:
        raise KeyError(f"No worker called “{label}”")
    return w


def base(w):
    host = w["host"]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{w['port']}"


# ---------------------------------------------------------------- live capabilities
async def _get(s, w, path, timeout=30):
    async with s.get(base(w) + path, timeout=ClientTimeout(total=timeout)) as r:
        if r.status != 200:
            raise RuntimeError(f"{w['label']} {path}: HTTP {r.status}")
        return await r.json(content_type=None)


async def caps(w, force=False):
    """(object_info, self_validated) of worker w, cached CAPS_TTL seconds."""
    c = _caps.get(w["label"])
    if c and not force and time.time() - c["at"] < CAPS_TTL:
        return c["data"], c["sv"]
    async with ClientSession() as s:
        data = await _get(s, w, "/object_info", timeout=60)
        try:
            sv = (await _get(s, w, "/mac/info", timeout=10)).get("self_validated") or SELF_VALIDATED
        except Exception:
            sv = SELF_VALIDATED
    _caps[w["label"]] = {"at": time.time(), "data": data, "sv": sv}
    return data, sv


async def info(w):
    """Cheap health: comfy-mac's /mac/info, else ComfyUI's /system_stats + /queue (an older worker)."""
    async with ClientSession() as s:
        try:
            return await _get(s, w, "/mac/info", timeout=5)
        except Exception:
            st = await _get(s, w, "/system_stats", timeout=5)
            q = await _get(s, w, "/queue", timeout=5)
            dev = (st.get("devices") or [{}])[0]
            return {"label": w["label"], "device": {"name": dev.get("name"), "vram_total": dev.get("vram_total")},
                    "comfy_version": (st.get("system") or {}).get("comfyui_version"),
                    "queue_remaining": len(q.get("queue_running", [])) + len(q.get("queue_pending", []))}


# ---------------------------------------------------------------- the gate
def _spec(oi_node, name):
    ins = (oi_node or {}).get("input") or {}
    for part in ("required", "optional"):
        s = (ins.get(part) or {}).get(name)
        if s is not None:
            return s
    return None


def combo(spec):
    """(options, flags) when spec is a COMBO in either format, else None.
    legacy: [[opt, ...], {flags}]   0.44+: ["COMBO", {"options": [...], ...flags}]"""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    flags = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if isinstance(spec[0], list):
        return list(spec[0]), flags
    if spec[0] == "COMBO":
        return list(flags.get("options") or []), flags
    return None


def is_file_input(spec):
    c = combo(spec)
    return bool(c and any(c[1].get(k) for k in FILE_FLAGS))


def gate(api, object_info, self_validated=None):
    """Reasons this prompt can't run against object_info; [] means it can."""
    sv = SELF_VALIDATED if self_validated is None else self_validated
    why = []
    for nid, node in api.items():
        ct = node.get("class_type")
        oi = object_info.get(ct)
        if oi is None:
            why.append({"node": nid, "class_type": ct, "reason": f"node {ct} is not installed"})
            continue
        for name, val in (node.get("inputs") or {}).items():
            if not isinstance(val, str):
                continue        # links ([id, slot]) and numbers
            skip = sv.get(ct) or []
            if "*" in skip or name in skip:
                continue        # the node checks this input itself
            spec = _spec(oi, name)
            c = combo(spec)
            if not c or is_file_input(spec):
                continue
            if val not in c[0]:
                why.append({"node": nid, "class_type": ct, "input": name, "value": val, "reason": f"{ct}.{name} has no “{val}”"})
    return why


async def check(w, api):
    oi, sv = await caps(w)
    return gate(api, oi, sv)


async def pick(workers, api, local_busy, local_ok=True, timeout=15):
    """"auto": the least busy machine that can run api. local_busy = jobs left on this PC; ties go to this PC,
    then to the worker listed first. Returns (label, {label: refusal reasons or error}) — label None if none can."""
    async def one(w):
        try:
            why = await asyncio.wait_for(check(w, api), timeout)
            if why:
                return w, None, why
            nfo = await asyncio.wait_for(info(w), timeout)
            busy = nfo.get("queue_remaining") or 0
            busy += JOBS_UPLOADING.get(w["label"], 0)
            return w, busy, None
        except Exception as e:
            return w, None, [{"reason": f"{w['label']} is unreachable: {e}"}]
    res = await asyncio.gather(*(one(w) for w in workers))
    cands = ([(local_busy, 0, LOCAL)] if local_ok else []) + [(b, k + 1, w["label"]) for k, (w, b, _) in enumerate(res) if b is not None]
    notes = {w["label"]: why for w, _b, why in res if why}
    return (min(cands)[2] if cands else None), notes


# ---------------------------------------------------------------- remote jobs
def _split_annot(v):
    """'sub/x.png [input]' -> ('sub/x.png', 'input')"""
    for t in ("input", "output", "temp"):
        if v.endswith(f" [{t}]"):
            return v[: -len(t) - 3], t
    return v, "input"


async def _upload_inputs(s, w, api, object_info, dirs):
    """Send every file the prompt reads to the worker; rewrite the values to the worker's names."""
    sent = []
    for node in api.values():
        oi = object_info.get(node.get("class_type"))
        for name, val in list((node.get("inputs") or {}).items()):
            if not isinstance(val, str) or not val or not is_file_input(_spec(oi, name)):
                continue
            rel, kind = _split_annot(val)
            src = os.path.realpath(os.path.join(dirs[kind], rel))
            if not src.startswith(os.path.realpath(dirs[kind]) + os.sep) or not os.path.isfile(src):
                raise RuntimeError(f"The file “{rel}” isn't on this PC")
            sub = os.path.dirname(rel).replace("\\", "/")
            fd = FormData()
            fd.add_field("image", open(src, "rb"), filename=os.path.basename(rel))
            fd.add_field("subfolder", sub)
            fd.add_field("type", "input")
            fd.add_field("overwrite", "true")
            async with s.post(base(w) + "/upload/image", data=fd, timeout=ClientTimeout(total=600)) as r:
                if r.status != 200:
                    raise RuntimeError(f"{w['label']} refused the upload of {rel}: HTTP {r.status}")
                up = await r.json(content_type=None)
            new = (up["subfolder"] + "/" if up.get("subfolder") else "") + up["name"]
            node["inputs"][name] = new
            sent.append(new)
    return sent


async def submit(w, api, payload_extra, dirs, private=False, incoming=".private_incoming", on_done=None):
    """Queue api on worker w. dirs = {"input", "output", "temp"} of this PC. Returns {"prompt_id", "worker"}.
    Raises RuntimeError with the gate reasons when the worker can't run it."""
    oi, sv = await caps(w)
    why = gate(api, oi, sv)
    if why:
        raise GateRefused(w["label"], why)
    async with ClientSession() as s:
        JOBS_UPLOADING[w["label"]] = JOBS_UPLOADING.get(w["label"], 0) + 1
        try:
            sent = await _upload_inputs(s, w, api, oi, dirs)
        finally:
            JOBS_UPLOADING[w["label"]] -= 1
        body = dict(payload_extra, prompt=api)
        async with s.post(base(w) + "/prompt", json=body, timeout=ClientTimeout(total=120)) as r:
            data = await r.json(content_type=None)
            if r.status != 200:
                raise RuntimeError(f"{w['label']} refused the job: {json.dumps(data)[:600]}")
    pid = data["prompt_id"]
    if data.get("node_errors"):
        # ComfyUI queues a job when ANY output validates and lists the failed nodes in the same 200 reply: that
        # job would "succeed" without the picture. Take it back out and refuse, like the gate would have.
        await cancel(w, pid, inputs=sent)
        raise GateRefused(w["label"], node_errors_why(data["node_errors"]))
    ex = payload_extra.get("extra_data") or {}
    job = {"prompt_id": pid, "worker": w["label"], "status": "queued", "private": private, "inputs": sent, "outputs": [], "error": None,
           "queued_at": time.time(), "app": ex.get("manny_app"), "label": ex.get("manny_label"), "client_id": payload_extra.get("client_id"),
           "progress": 0.0, "step": None}
    JOBS[pid] = job
    job["task"] = asyncio.ensure_future(_follow(w, job, dirs, incoming, on_done))
    return {"prompt_id": pid, "number": data.get("number"), "worker": w["label"]}


def node_errors_why(node_errors):
    """ComfyUI's node_errors as gate reasons."""
    why = []
    for nid, ne in (node_errors or {}).items():
        for e in ne.get("errors") or [{}]:
            x = e.get("extra_info") or {}
            why.append({"node": nid, "class_type": ne.get("class_type"), "input": x.get("input_name"), "value": x.get("received_value"),
                        "reason": f"{ne.get('class_type') or nid}: {e.get('message', 'invalid')}" + (f" ({e['details']})" if e.get("details") else "")})
    return why


class GateRefused(RuntimeError):
    def __init__(self, label, why):
        super().__init__(f"{label} can't run this: " + "; ".join(x["reason"] for x in why[:6]) + (" …" if len(why) > 6 else ""))
        self.label, self.why = label, why


def _outputs(h):
    for o in (h.get("outputs") or {}).values():
        for k in ("images", "gifs", "videos", "audio"):
            for f in o.get(k) or []:
                if isinstance(f, dict) and f.get("type") == "output" and f.get("filename"):
                    yield f


def _local_target(dirs, f, private, incoming):
    sub = (f.get("subfolder") or "").replace("\\", "/").strip("/")
    root = os.path.join(dirs["output"], incoming) if private and not sub.startswith(incoming) else dirs["output"]
    dst = os.path.realpath(os.path.join(root, sub, f["filename"]))
    if not dst.startswith(os.path.realpath(dirs["output"]) + os.sep):
        raise RuntimeError(f"Refused an output path outside the output folder: {sub}/{f['filename']}")
    return dst


async def _progress(w, job):
    """Step progress from the worker's /ws. ComfyUI sends it only to the client_id that queued, which is ours."""
    pid, url = job["prompt_id"], base(w).replace("http", "ws", 1) + f"/ws?clientId={job['client_id']}"
    while job["status"] in ("queued", "running"):
        try:
            async with ClientSession() as s:
                async with s.ws_connect(url, heartbeat=30) as ws:
                    async for m in ws:
                        if m.type != 1:     # TEXT; previews are binary
                            continue
                        d = json.loads(m.data)
                        x = d.get("data") or {}
                        if x.get("prompt_id") != pid:
                            continue
                        if d.get("type") == "execution_start":
                            job["status"] = "running"
                        elif d.get("type") == "progress" and x.get("max"):
                            job["status"] = "running"
                            job["progress"], job["step"] = x["value"] / x["max"], f"Step {x['value']}/{x['max']}"
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(3)


async def _follow(w, job, dirs, incoming, on_done, poll=2):
    pid = job["prompt_id"]
    prog = asyncio.ensure_future(_progress(w, job)) if job.get("client_id") else None
    try:
        async with ClientSession() as s:
            while True:
                try:
                    h = (await _get(s, w, f"/history/{pid}")).get(pid)
                except Exception:
                    h = None    # worker briefly unreachable: keep waiting
                if h:
                    st = h.get("status") or {}
                    if st.get("status_str") == "error":
                        msgs = [m[1].get("exception_message", "") for m in st.get("messages", []) if m and m[0] == "execution_error"]
                        raise RuntimeError(f"{w['label']}: the render failed. " + " ".join(msgs)[:400])
                    if st.get("completed") or st.get("status_str") == "success":
                        break
                await asyncio.sleep(poll)
            job["status"] = "downloading"
            for f in _outputs(h):
                dst = _local_target(dirs, f, job["private"], incoming)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                q = {"filename": f["filename"], "subfolder": f.get("subfolder") or "", "type": "output"}
                async with s.get(base(w) + "/view", params=q, timeout=ClientTimeout(total=1800)) as r:
                    if r.status != 200:
                        raise RuntimeError(f"{w['label']}: could not fetch {f['filename']} (HTTP {r.status})")
                    tmp = dst + ".part"
                    with open(tmp, "wb") as out:
                        async for chunk in r.content.iter_chunked(1 << 20):
                            out.write(chunk)
                os.replace(tmp, dst)
                job["outputs"].append({"filename": f["filename"], "subfolder": f.get("subfolder") or "", "path": dst})
            job["status"], job["progress"] = "done", 1.0
            job["forgot"] = await forget(s, w, pid, job["inputs"])
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        raise
    except Exception as e:
        job["status"], job["error"] = "failed", str(e)
    finally:
        if prog:
            prog.cancel()
        job["finished_at"] = time.time()
        if on_done:
            try:
                on_done(job)
            except Exception as e:
                print("[Studio] remote on_done:", e)


async def forget(s, w, pid, inputs=()):
    """Ask the worker to delete this job's outputs, history and the inputs we uploaded. Older workers lack
    /mac/forget: then only the history entry is dropped and the files stay there (reported, not hidden)."""
    try:
        async with s.post(base(w) + "/mac/forget", json={"prompt_id": pid, "inputs": list(inputs)}, timeout=ClientTimeout(total=30)) as r:
            if r.status == 200:
                return await r.json(content_type=None)
            fallback = f"/mac/forget HTTP {r.status}"
    except Exception as e:
        fallback = f"/mac/forget unreachable: {e}"
    try:
        async with s.post(base(w) + "/history", json={"delete": [pid]}, timeout=ClientTimeout(total=10)):
            pass
    except Exception:
        pass
    return {"deleted": [], "warning": fallback + "; files left on the worker"}


async def cancel(w, pid, settle=20, inputs=None):
    """Drop a remote job wherever it is (out of the worker's queue, or interrupted while running), then have the
    worker forget it and the inputs we uploaded. Returns the forget reply."""
    job = JOBS.get(pid)
    if job and job.get("task") and not job["task"].done():
        job["task"].cancel()
    async with ClientSession() as s:
        async with s.post(base(w) + "/queue", json={"delete": [pid]}, timeout=ClientTimeout(total=10)):
            pass
        async with s.post(base(w) + "/interrupt", json={"prompt_id": pid}, timeout=ClientTimeout(total=10)):
            pass
        for _ in range(settle):     # an interrupted job lands in history; a never-started one doesn't
            async with s.get(base(w) + "/queue", timeout=ClientTimeout(total=10)) as r:
                q = await r.json(content_type=None)
            if not any(it[1] == pid for it in q.get("queue_running", []) + q.get("queue_pending", [])):
                break
            # it may have started between the delete and the interrupt: ask again
            async with s.post(base(w) + "/queue", json={"delete": [pid]}, timeout=ClientTimeout(total=10)):
                pass
            async with s.post(base(w) + "/interrupt", json={"prompt_id": pid}, timeout=ClientTimeout(total=10)):
                pass
            await asyncio.sleep(1)
        fg = await forget(s, w, pid, inputs if inputs is not None else (job or {}).get("inputs") or [])
    if job:
        job["status"], job["forgot"], job["finished_at"] = "cancelled", fg, time.time()
    return fg


def job_view(job):
    return {k: v for k, v in job.items() if k != "task"}


def jobs(now=None):
    """Live and recently finished remote jobs, newest first (finished ones older than KEEP_DONE are dropped)."""
    now = now or time.time()
    for pid in [p for p, j in JOBS.items() if j.get("finished_at") and now - j["finished_at"] > KEEP_DONE]:
        del JOBS[pid]
    return sorted((job_view(j) for j in JOBS.values()), key=lambda j: -j["queued_at"])
