"""Remote GPU workers: other ComfyUI machines (the Mac Pro's two Vega II workers) that Studio can render on.

A worker is {label, host, port, device?}, kept in manny_studio/workers.json (same entry shape as comfy-mac's
mac-workers.json, so its "workers" list can be pasted in as is). "local" is this PC's own ComfyUI.

What a worker can run is read LIVE from its /object_info, never from a manifest. gate() refuses a prompt unless
every node exists on the worker and every string value sent to a COMBO input is one of that input's options
(models, samplers, schedulers; the folder is checked too, a VAE file in a UNet slot fails; an empty folder
refuses everything), and every required input is there (dynamic "<name>.<child>" keys count; an autogrow input
needs its template's min children). Like ComfyUI's own validation, inputs a node validates itself are skipped: the worker's
/mac/info lists them as "self_validated" {class: [input, ...] or ["*"]}; a worker without /mac/info (and this PC)
uses SELF_VALIDATED. File inputs (image/video/audio upload COMBOs) are always skipped: Studio uploads those.

A worker picked BY NAME that lacks only model files (every refusal is a COMBO value) is asked to download them
(comfy-mac's POST /mac/fetch); the job gets its prompt_id at once, waits as "fetching", then is gated again and
queued under that same id (ComfyUI takes a client prompt_id). "auto" never downloads: it picks a machine that can run
the job now.

A remote job: upload inputs -> POST /prompt on the worker -> poll /history -> download every output through /view
into this PC's output folder (private jobs into the incoming folder the sweeper moves) -> POST /mac/forget so
nothing stays on the worker. No ComfyUI imports here: the paths come in as arguments.
"""
import asyncio
import json
import os
import re
import time
import uuid

from aiohttp import ClientSession, ClientTimeout, FormData

from .appwf import missing_required

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
LOST_AFTER = 600        # a worker unreachable this long while it has our job: the job is failed
LONG_JOB = 600          # "auto": a worker running one of our jobs for longer than this counts one busier
LOST_MISSES = 3         # polls in a row with the job in neither /history nor /queue: the worker restarted
FETCH_POLL = 5          # seconds between /mac/fetch asks while a model downloads
FETCH_TIMEOUT = 60      # seconds one /mac/fetch ask may take
FETCH_TRIES = 3         # a download that fails this many times in a row (each ask resumes it) fails the job
LOCAL_INFO = None       # async () -> this PC's /object_info, set by Studio: says which pack a node the worker lacks comes from
LOCAL_MODEL = None      # (folder, value) -> this PC's file for a model, or None; set by Studio (modelshare's own rules)
LOCAL_SHA = None        # path -> its sha256 (sidecar or computed once and cached); sent with every push
PUSH_CHUNK = 1 << 20
PUSH_STALL = LOST_AFTER  # a push that gains no bytes for this long (dropped line, 202 with nothing taken, a lock) fails
_PUSHES = {}            # (worker, folder, value) -> the task pushing it, so jobs needing one file send it once


# ---------------------------------------------------------------- remote jobs on disk (survive a restart of this PC's ComfyUI)
_STATE = {"dir": None}
ACTIVE = ("fetching", "queued", "running", "downloading", "cancelling")


def configure(data_dir):
    """Where the job list is kept (manny_studio/); None turns persistence off."""
    _STATE["dir"] = data_dir


def state_file():
    return os.path.join(_STATE["dir"], "remote_jobs.json") if _STATE["dir"] else None


def _persist():
    path = state_file()
    if not path:
        return
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([job_view(j) for j in JOBS.values()], f)
        os.replace(tmp, path)
    except Exception as e:
        print("[Studio] could not save remote jobs:", e)


def resume(dirs, incoming=".private_incoming", on_done=None):
    """After a restart: load the saved jobs and follow the unfinished ones again (pull back, forget).
    Returns how many were resumed. Must run on the event loop."""
    path = state_file()
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except Exception:
        return 0
    workers = {w["label"]: w for w in load_workers(_STATE["dir"])}
    n = 0
    for job in saved if isinstance(saved, list) else []:
        if not isinstance(job, dict) or not job.get("prompt_id") or job["prompt_id"] in JOBS:
            continue
        JOBS[job["prompt_id"]] = job
        if job.get("status") not in ACTIVE:
            continue
        w = workers.get(job.get("worker"))
        if not w:
            job.update(status="failed", error=f"{job.get('worker')} is no longer set up", finished_at=time.time())
            _ended(job, on_done)        # a moved job comes back to this PC's queue
            continue
        if job["status"] == "fetching":     # the prompt isn't kept on disk, so it can't be sent once the model lands
            job.update(status="failed", finished_at=time.time(),
                       error=f"Studio restarted while {w['label']} was downloading a model; send it again (the download carries on)")
            _ended(job, on_done)
            continue
        if job["status"] == "cancelling":
            job["task"] = asyncio.ensure_future(_quiet(cancel(w, job["prompt_id"])))
        else:
            job["task"] = asyncio.ensure_future(_follow(w, job, dirs, incoming, on_done))
        n += 1
    _persist()
    return n


def _ended(job, on_done):
    if on_done:
        try:
            on_done(job)
        except Exception as e:
            print("[Studio] remote on_done:", e)


async def _quiet(coro):
    try:
        return await coro
    except Exception as e:
        print("[Studio] remote:", e)


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
        for name in missing_required(node.get("inputs") or {}, oi):
            why.append({"node": nid, "class_type": ct, "input": name, "reason": f"{ct}.{name} is required but missing"})
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
            if val not in c[0] and _spelled(val, c[0]) is None:
                why.append({"node": nid, "class_type": ct, "input": name, "value": val, "reason": f"{ct}.{name} has no “{val}”"})
    return why


def _spelled(val, options):
    """val as options spell it when only the folder separator differs ("Civitai\\x" on the Windows PC is "Civitai/x"
    on a Mac); None if no spelling of it is there."""
    for alt in (val.replace("\\", "/"), val.replace("/", "\\")):
        if alt != val and alt in options:
            return alt
    return None


def respell(api, object_info, self_validated=None):
    """Rewrite every COMBO value to the worker's own spelling of it (its ComfyUI validates exact names)."""
    sv = SELF_VALIDATED if self_validated is None else self_validated
    for node in api.values():
        ct = node.get("class_type")
        skip = sv.get(ct) or []
        for name, val in list((node.get("inputs") or {}).items()):
            if not isinstance(val, str) or "*" in skip or name in skip:
                continue
            spec = _spec(object_info.get(ct), name)
            c = combo(spec)
            if c and not is_file_input(spec) and val not in c[0]:
                alt = _spelled(val, c[0])
                if alt is not None:
                    node["inputs"][name] = alt
    return api


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
            busy += 1 if nfo.get("te_for") else 0     # its GPU is encoding another job's prompt (both Vegas on one job)
            # one of our jobs has held this card for >10 min (an LTX video, an upscale): count it as busier
            busy += sum(1 for j in JOBS.values() if j["worker"] == w["label"] and j["status"] == "running"
                        and time.time() - (j.get("started_at") or time.time()) > LONG_JOB)
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


async def _upload_inputs(s, w, api, object_info, dirs, tag):
    """Send every file the prompt reads to the worker, under studio/<tag>/ so jobs never share (and never
    forget) each other's copies; rewrite the values to the worker's names."""
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
            sub = "/".join(x for x in ("studio", tag, os.path.dirname(rel).replace("\\", "/")) if x)
            with open(src, "rb") as fh:
                data = fh.read()
            fd = FormData()
            fd.add_field("image", data, filename=os.path.basename(rel))
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


async def submit(w, api, payload_extra, dirs, private=False, incoming=".private_incoming", on_done=None, fetch=False):
    """Queue api on worker w. dirs = {"input", "output", "temp"} of this PC. Returns {"prompt_id", "worker"}.
    Raises GateRefused with the gate reasons when the worker can't run it. fetch (a worker picked by name): when
    only model files are missing, the worker downloads them and the job is queued once they're there ("fetching")."""
    oi, sv = await caps(w)
    why = gate(api, oi, sv)
    if why and fetch and fetchable(why):
        oi, sv = await caps(w, force=True)      # it may have landed since the cached list
        why = gate(api, oi, sv)
    if why and fetch and fetchable(why):
        try:
            files = await _fetch(w, why)
        except NoFetch as e:        # a worker without /mac/fetch: refuse as before, saying why
            raise GateRefused(w["label"], why + [{"reason": f"it can't download models ({e})"}])
        except Exception as e:      # slow or dropped (an index rebuild, a PC asleep): wait and ask again
            files = [{"value": x["value"], "status": "asking", "reason": f"/mac/fetch didn't answer yet ({type(e).__name__})"} for x in why]
        bad = [f for f in files if f.get("status") == "unavailable" and not _pushable(f)]
        if bad:
            raise GateRefused(w["label"], _unavailable(bad))
        job = _new_job(w, str(uuid.uuid4()), payload_extra, private, "fetching")
        job["fetch"] = files
        _show_fetch(w, job, files)
        job["task"] = asyncio.ensure_future(_fetch_then_send(w, job, api, payload_extra, dirs, incoming, on_done, files, why))
        _persist()
        return {"prompt_id": job["prompt_id"], "number": None, "worker": w["label"], "fetching": [f.get("value") for f in files]}
    if why:
        raise GateRefused(w["label"], await name_packs(why))
    data, sent, opened = await _send(w, api, payload_extra, dirs, oi, sv)
    job = _new_job(w, data["prompt_id"], payload_extra, private, "queued")
    job["inputs"] = sent
    job["task"] = asyncio.ensure_future(_follow(w, job, dirs, incoming, on_done, opened=opened))
    _persist()
    return {"prompt_id": job["prompt_id"], "number": data.get("number"), "worker": w["label"]}


def _new_job(w, pid, payload_extra, private, status):
    ex = payload_extra.get("extra_data") or {}
    job = {"prompt_id": pid, "worker": w["label"], "status": status, "private": private, "inputs": [], "outputs": [], "error": None,
           "queued_at": time.time(), "app": ex.get("manny_app"), "label": ex.get("manny_label"), "client_id": payload_extra.get("client_id"),
           "progress": 0.0, "step": None}
    JOBS[pid] = job
    return job


async def _send(w, api, payload_extra, dirs, oi, sv, pid=None):
    """Tag, upload the inputs, POST /prompt (under pid when given). Returns (reply, uploaded inputs, open /ws)."""
    respell(api, oi, sv)
    # ComfyUI caches output nodes too: an identical job would "finish" pointing at files /mac/forget already
    # deleted. A per-job key on each output node changes only its cache key (ComfyUI never passes an unknown
    # literal input to the node), so the save runs again while everything upstream may still come from cache.
    tag = os.urandom(6).hex()
    for node in api.values():
        ct = node.get("class_type")
        if (oi.get(ct) or {}).get("output_node") and "*" not in (sv.get(ct) or []):   # "*" nodes would receive it
            node.setdefault("inputs", {})["studio_job"] = tag
    async with ClientSession() as s:
        JOBS_UPLOADING[w["label"]] = JOBS_UPLOADING.get(w["label"], 0) + 1
        try:
            sent = await _upload_inputs(s, w, api, oi, dirs, tag)
        finally:
            JOBS_UPLOADING[w["label"]] -= 1
        body = dict(payload_extra, prompt=api, **({"prompt_id": pid} if pid else {}))
        opened = await _open_ws(w, payload_extra["client_id"]) if payload_extra.get("client_id") else None
        try:
            async with s.post(base(w) + "/prompt", json=body, timeout=ClientTimeout(total=120)) as r:
                data = await r.json(content_type=None)
                if r.status != 200:
                    raise RuntimeError(f"{w['label']} refused the job: {json.dumps(data)[:600]}")
        except BaseException:
            if opened:
                await opened[1].close()
                await opened[0].close()
            raise
    if data.get("node_errors"):
        if opened:
            await opened[1].close()
            await opened[0].close()
        # ComfyUI queues a job when ANY output validates and lists the failed nodes in the same 200 reply: that
        # job would "succeed" without the picture. Take it back out and refuse, like the gate would have.
        await cancel(w, data["prompt_id"], inputs=sent)
        raise GateRefused(w["label"], node_errors_why(data["node_errors"]))
    return data, sent, opened


async def name_packs(why):
    """A node the worker lacks: say which custom node pack it comes from on this PC, so it can be copied over.
    Reasons keep their "node X is not installed" start; core nodes and unknown classes are left as they are."""
    if not LOCAL_INFO or not any(x.get("class_type") and "input" not in x and str(x.get("reason", "")).startswith("node ") for x in why):
        return why
    try:
        here = await LOCAL_INFO()
    except Exception:
        return why
    for x in why:
        if "input" in x or not str(x.get("reason", "")).startswith("node "):
            continue
        mod = str((here.get(x.get("class_type")) or {}).get("python_module") or "")
        if mod.startswith("custom_nodes."):
            pack = mod.split(".", 1)[1].split(".")[0]
            x["pack"] = pack
            x["reason"] += f" (it comes from the {pack} pack: copy ComfyUI/custom_nodes/{pack} from this PC to that machine)"
    return why


# ---------------------------------------------------------------- missing models: the worker downloads them
class NoFetch(RuntimeError):
    """The worker has no /mac/fetch (an older worker): it can't download models."""


def fetchable(why):
    """True when every refusal is a COMBO value (a file the worker's folder lacks), so /mac/fetch may get it."""
    return bool(why) and all(x.get("class_type") and x.get("input") and isinstance(x.get("value"), str) for x in why)


async def _fetch(w, why):
    """POST /mac/fetch with the gate's refusals (one per file); its answer per file, in order."""
    files, seen = [], set()
    for x in why:
        k = (x["class_type"], x["input"], x["value"])
        if k not in seen:
            seen.add(k)
            files.append({"class_type": k[0], "input": k[1], "value": k[2]})
    async with ClientSession() as s:
        async with s.post(base(w) + "/mac/fetch", json={"files": files}, timeout=ClientTimeout(total=FETCH_TIMEOUT)) as r:
            if r.status in (404, 405):
                raise NoFetch(f"/mac/fetch HTTP {r.status}")
            if r.status != 200:
                raise RuntimeError(f"/mac/fetch HTTP {r.status}")
            return (await r.json(content_type=None)).get("files") or []


def _pushable(f):
    """A file the worker can't pull but this PC has, so it can send it (/mac/fetch said "push": true)."""
    return bool(f.get("push") and f.get("folder") and LOCAL_MODEL and LOCAL_MODEL(f["folder"], f["value"]))


async def _push(w, job, folder, value):
    """Send this PC's copy of a model to the worker (PUT /mac/models/...), resuming from what it already has.
    A file several jobs need is sent once: they all wait for the same push."""
    key = (w["label"], folder, value.replace("\\", "/"))
    t = _PUSHES.get(key)
    if not t or t.done():
        t = _PUSHES[key] = asyncio.ensure_future(_push_file(w, folder, value, LOCAL_MODEL(folder, value), job))
    await asyncio.shield(t)


async def _push_file(w, folder, value, path, job):
    import urllib.parse
    url = f"{base(w)}/mac/models/{folder}/{urllib.parse.quote(value.replace(chr(92), '/'), safe='/')}"
    size = os.path.getsize(path)
    sha = await asyncio.to_thread(LOCAL_SHA, path) if LOCAL_SHA else None     # the worker checks every byte against it
    from aiohttp import ClientError
    best, since = -1, time.time()       # progress = what the worker says it has; a network error just asks again
    async with ClientSession() as s:
        while True:
            try:
                async with s.head(url, timeout=ClientTimeout(total=30)) as r:
                    if r.status == 200:
                        return
                    have = int(r.headers.get("X-Mac-Have") or 0)
            except (ClientError, asyncio.TimeoutError) as e:
                if time.time() - since > PUSH_STALL:
                    raise RuntimeError(f"Lost the connection to {w['label']} while sending {value} ({type(e).__name__})")
                await asyncio.sleep(FETCH_POLL)
                continue
            if have > best:
                best, since = have, time.time()
            elif time.time() - since > PUSH_STALL:
                raise RuntimeError(f"{w['label']} isn't taking {value}: still {have} of {size} bytes after {PUSH_STALL:.0f} s")

            async def body(start=have):
                done = start
                with open(path, "rb") as fh:
                    fh.seek(start)
                    while True:
                        chunk = await asyncio.to_thread(fh.read, PUSH_CHUNK)
                        if not chunk:
                            return
                        done += len(chunk)
                        unit, div = ("GB", 1e9) if size >= 1e9 else ("MB", 1e6)
                        job["step"], job["progress"] = f"Sending model {value} to {w['label']}: {done / div:.1f}/{size / div:.1f} {unit}", done / size
                        yield chunk
            hd = {"Content-Length": str(size - have)}
            if have:
                hd["Content-Range"] = f"bytes {have}-{size - 1}/{size}"
            if sha:
                hd["X-Studio-SHA256"] = sha
            try:
                async with s.put(url, data=body(), headers=hd, timeout=ClientTimeout(total=None, sock_read=300)) as r:
                    if r.status == 201:
                        return
                    if r.status == 423:         # it's already pulling or being sent this file
                        await asyncio.sleep(FETCH_POLL)
                    elif r.status not in (202, 416):     # 202 cut short / 416 out of step: ask what it has, go on
                        raise RuntimeError(f"{w['label']} wouldn't take {value}: HTTP {r.status} {(await r.text())[:200]}")
            except (ClientError, asyncio.TimeoutError):
                await asyncio.sleep(FETCH_POLL)     # the line dropped mid-file: the worker keeps the .part


def _unavailable(bad):
    return [{"value": f.get("value"), "reason": f"{f.get('value')}: {f.get('reason') or 'unavailable'}"} for f in bad]


def _show_fetch(w, job, files):
    """The queue line while waiting: 'Downloading model x on mac-vega-a: 1.2/9.6 GB'."""
    left = [f for f in files if f.get("status") != "present"] or files
    f = left[0] if left else {}
    size = sum(x.get("size") or 0 for x in left)
    done = sum(x.get("done") or 0 for x in left)
    unit, div = ("GB", 1e9) if size >= 1e9 else ("MB", 1e6)
    more = f" (+{len(left) - 1} more)" if len(left) > 1 else ""
    job["step"] = f"Downloading model {f.get('value')}{more} on {w['label']}" + (f": {done / div:.1f}/{size / div:.1f} {unit}" if size else "")
    job["progress"] = done / size if size else 0.0
    notes = [x["note"] for x in files if x.get("note")]
    if notes:
        job["note"] = "; ".join(notes)


async def _fetch_then_send(w, job, api, payload_extra, dirs, incoming, on_done, files, why):
    """Wait for the worker's downloads, gate again, then queue under job's prompt_id and follow it like any job."""
    tries, stale, seen = {}, 0, time.time()
    try:
        while True:
            job["fetch"] = files
            _show_fetch(w, job, files)
            bad = [f for f in files if f.get("status") == "unavailable" and not _pushable(f)]
            if bad:
                raise GateRefused(w["label"], _unavailable(bad))
            push = [f for f in files if f.get("status") == "unavailable"]
            if push:            # the worker can't pull these (no route to this PC): send them over the way that works
                for f in push:
                    await _push(w, job, f["folder"], f["value"])
                files = await _fetch(w, why)
                continue
            for f in files:
                v = f.get("value")
                tries[v] = tries.get(v, 0) + 1 if f.get("status") == "failed" else 0
                if tries[v] >= FETCH_TRIES:
                    raise RuntimeError(f"{w['label']} couldn't download {v}: {f.get('reason') or 'failed'}")
            if files and all(f.get("status") == "present" for f in files):
                oi, sv = await caps(w, force=True)
                why = gate(api, oi, sv)
                if not why:
                    break
                if not fetchable(why):
                    raise GateRefused(w["label"], await name_packs(why))
                stale += 1      # "present" but not listed yet, or another file is still missing: ask again
                if stale >= FETCH_TRIES:
                    raise GateRefused(w["label"], why)
            await asyncio.sleep(FETCH_POLL)
            try:
                files = await _fetch(w, why)
                seen = time.time()
            except Exception as e:
                if time.time() - seen > LOST_AFTER:
                    raise RuntimeError(f"Lost contact with {w['label']} for {LOST_AFTER // 60} min while it downloaded ({e})")
        job.update(status="queued", step=None, progress=0.0)
        _persist()
        data, sent, opened = await _send(w, api, payload_extra, dirs, oi, sv, pid=job["prompt_id"])
        job["inputs"] = sent
        _persist()
    except asyncio.CancelledError:
        if job["status"] != "cancelling":
            job.update(status="cancelled", finished_at=time.time())
            _persist()
        raise
    except Exception as e:
        job.update(status="failed", error=str(e), finished_at=time.time())
        _persist()
        if on_done:
            try:
                on_done(job)
            except Exception as oe:
                print("[Studio] remote on_done:", oe)
        return
    await _follow(w, job, dirs, incoming, on_done, opened=opened)


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
    """Every file the job saved, whatever key its node reports it under (images, videos, audio, 3d, ...)."""
    for o in (h.get("outputs") or {}).values():
        for v in o.values():
            for f in v if isinstance(v, list) else []:
                if isinstance(f, dict) and f.get("type") == "output" and f.get("filename"):
                    yield f


def _local_target(dirs, f, private, incoming):
    sub = (f.get("subfolder") or "").replace("\\", "/").strip("/")
    root = os.path.join(dirs["output"], incoming) if private and not sub.startswith(incoming) else dirs["output"]
    dst = os.path.realpath(os.path.join(root, sub, f["filename"]))
    if not dst.startswith(os.path.realpath(dirs["output"]) + os.sep):
        raise RuntimeError(f"Refused an output path outside the output folder: {sub}/{f['filename']}")
    return _claim(dst)


_COUNTER = re.compile(r"^(.*_)(\d{5})(_\.[^.]+)$")


def _claim(dst):
    """dst, or the next free name ComfyUI-style (x_00001_.png -> x_00002_.png), created empty so that a job
    finishing at the same moment can't take it too. Never overwrites a file on this PC."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    d, name = os.path.split(dst)
    m = _COUNTER.match(name)
    stem, ext = os.path.splitext(name)
    for k in range(100000):
        cand = name if k == 0 else (f"{m.group(1)}{int(m.group(2)) + k:05d}{m.group(3)}" if m else f"{stem}_{k}{ext}")
        try:
            os.close(os.open(os.path.join(d, cand), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return os.path.join(d, cand)
        except FileExistsError:
            continue
    raise RuntimeError(f"No free name for {name}")


def _ws_url(w, client_id):
    return base(w).replace("http", "ws", 1) + f"/ws?clientId={client_id}"


async def _open_ws(w, client_id):
    """(session, websocket) to the worker, opened BEFORE the job is queued so no early step is missed; None if it fails."""
    s = ClientSession()
    try:
        return s, await s.ws_connect(_ws_url(w, client_id), heartbeat=30, timeout=10)
    except Exception:
        await s.close()
        return None


async def _progress(w, job, opened=None):
    """Step progress from the worker's /ws. ComfyUI sends it only to the client_id that queued, which is ours."""
    pid = job["prompt_id"]
    while job["status"] in ("queued", "running"):
        try:
            if not opened:
                opened = await _open_ws(w, job["client_id"])
                if not opened:
                    raise ConnectionError("no /ws")
            s, ws = opened
            async with s, ws:
                async for m in ws:
                    if m.type != 1:     # TEXT; previews are binary
                        continue
                    d = json.loads(m.data)
                    x = d.get("data") or {}
                    if x.get("prompt_id") != pid:
                        continue
                    if d.get("type") == "execution_start" or (d.get("type") == "progress" and x.get("max")):
                        job.setdefault("started_at", time.time())
                        job["status"] = "running"
                    if d.get("type") == "progress" and x.get("max"):
                        job["progress"], job["step"] = x["value"] / x["max"], f"Step {x['value']}/{x['max']}"
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(3)
        opened = None


async def _follow(w, job, dirs, incoming, on_done, poll=2, opened=None):
    pid = job["prompt_id"]
    prog = asyncio.ensure_future(_progress(w, job, opened)) if job.get("client_id") else None
    try:
        async with ClientSession() as s:
            seen, misses = time.time(), 0
            while True:
                try:
                    h = (await _get(s, w, f"/history/{pid}")).get(pid)
                    seen = time.time()
                    if not h:   # not finished: still queued, or did the worker restart and lose it?
                        q = await _get(s, w, "/queue")
                        if any(it[1] == pid for it in q.get("queue_running", []) + q.get("queue_pending", [])):
                            misses = 0
                        else:
                            h = (await _get(s, w, f"/history/{pid}")).get(pid)    # it may have just finished
                            misses = 0 if h else misses + 1
                            if misses >= LOST_MISSES:
                                raise RuntimeError(f"{w['label']} restarted and lost the job; send it again")
                except RuntimeError:
                    raise
                except Exception as e:
                    h = None    # worker briefly unreachable: keep waiting, but not forever
                    if time.time() - seen > LOST_AFTER:
                        raise RuntimeError(f"Lost contact with {w['label']} for {LOST_AFTER // 60} min ({e}); the job may still be there")
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
                    tmp = dst + ".part"
                    try:
                        if r.status != 200:
                            raise RuntimeError(f"{w['label']}: could not fetch {f['filename']} (HTTP {r.status})")
                        with open(tmp, "wb") as out:
                            async for chunk in r.content.iter_chunked(1 << 20):
                                out.write(chunk)
                    except BaseException:
                        for x in (tmp, dst):    # the empty name _claim reserved, and any half file
                            try:
                                os.remove(x)
                            except OSError:
                                pass
                        raise
                os.replace(tmp, dst)
                job["outputs"].append({"filename": os.path.basename(dst), "subfolder": f.get("subfolder") or "", "path": dst})
            job["status"], job["progress"] = "done", 1.0
            job["forgot"] = await forget(s, w, pid, job["inputs"])
    except asyncio.CancelledError:
        if job["status"] != "cancelling":
            job["status"] = "cancelled"
        raise
    except Exception as e:
        job["status"], job["error"] = "failed", str(e)
        for o in job["outputs"]:    # files of a half-copied job: keep the PC clean too
            try:
                os.remove(o["path"])
            except OSError:
                pass
        try:            # nothing of a failed job stays on the worker: uploads, outputs, history (the prompt)
            async with ClientSession() as s2:
                job["forgot"] = await forget(s2, w, pid, job["inputs"])
        except Exception as fe:
            job["forgot"] = {"deleted": [], "warning": f"forget failed: {fe}"}
    finally:
        if prog:
            prog.cancel()
        job["finished_at"] = time.time()
        _persist()
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
    if job and job["status"] == "fetching":
        job["note"] = f"The model keeps downloading on {w['label']}, so it's there next time"
    if job:
        job["status"] = "cancelling"    # "cancelled" once the worker has dropped it and forgotten its files
        _persist()
    if job and job.get("task") and not job["task"].done() and job["task"] is not asyncio.current_task():
        job["task"].cancel()
    try:
        return await _cancel(w, pid, settle, inputs, job)
    except Exception as e:
        if job:
            job.update(status="failed", error=f"Couldn't cancel on {w['label']}: {e}", finished_at=time.time())
            _persist()
        raise


async def _cancel(w, pid, settle, inputs, job):
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
        _persist()
    return fg


def job_view(job):
    return {k: v for k, v in job.items() if k != "task"}


def jobs(now=None):
    """Live and recently finished remote jobs, newest first (finished ones older than KEEP_DONE are dropped)."""
    now = now or time.time()
    old = [p for p, j in JOBS.items() if j.get("finished_at") and j["status"] not in ACTIVE and now - j["finished_at"] > KEEP_DONE]
    for pid in old:
        del JOBS[pid]
    if old:
        _persist()
    return sorted((job_view(j) for j in JOBS.values()), key=lambda j: -j["queued_at"])
