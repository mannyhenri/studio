"""Studio - a studio app on top of this ComfyUI.

Open http://<this-pc>:8188/manny . Every app built with ComfyUI's App builder
(a workflow saved as *.app.json) shows up in Studio automatically.
"""
import asyncio
import threading
import urllib.request
import json
import os
import random
import shutil
import time
import uuid

from aiohttp import web, ClientSession

import folder_paths
from server import PromptServer

from .mconv.workflow_to_api import convert_ui_to_api
from .appwf import combo_options as _combo_options, apply_values as _apply_values, fix_custom_combos as _fix_custom_combos, prune_switches as _prune_switches
from . import auth
from . import remote as _remote

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
VERSION = "1.18.0"

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

routes = PromptServer.instance.routes
auth.setup(routes)
try:
    PromptServer.instance.app.middlewares.append(auth.lock)
except Exception as e:  # never fail open silently
    print("[Studio] could not install the password lock:", e)
_object_info = {"data": None, "at": 0}


# ---------------------------------------------------------------- helpers
def _user_dir():
    d = os.path.join(folder_paths.get_user_directory(), "default")
    return d


def _workflows_dir():
    return os.path.join(_user_dir(), "workflows")


def _data_dir():
    d = os.path.join(_user_dir(), "manny_studio")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- incognito storage
INCOMING = ".private_incoming"          # inside the output folder, while a private render is being saved
PRIV_INPUT = auth.PRIVATE_INPUT         # inside the input folder: private references, masks, faces


def _priv_root():
    d = os.path.join(os.path.dirname(os.path.abspath(folder_paths.get_output_directory())), "Comfyui-Private")
    os.makedirs(d, exist_ok=True)
    return d


def _root(private):
    return _priv_root() if private else folder_paths.get_output_directory()


def _need_private(request):
    if not auth.unlocked(request):
        raise web.HTTPForbidden(text="Incognito is locked")


def _flag(v):
    return str(v).lower() in ("1", "true", "yes")


def _chars_file():
    return os.path.join(_data_dir(), "characters.json")


def _load_chars():
    try:
        with open(_chars_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_chars(chars):
    tmp = _chars_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(chars, f, indent=1, ensure_ascii=False)
    os.replace(tmp, _chars_file())


def _port():
    try:
        return PromptServer.instance.port
    except Exception:
        return 8188


async def _get_object_info(force=False):
    if _object_info["data"] is None or force or time.time() - _object_info["at"] > 600:
        async with ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{_port()}/object_info") as r:
                _object_info["data"] = await r.json()
                _object_info["at"] = time.time()
    return _object_info["data"]


def _list_apps():
    apps = []
    root = _workflows_dir()
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(".app.json"):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                apps.append(rel)
    return sorted(apps)


def _safe_app_path(rel):
    root = os.path.realpath(_workflows_dir())
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(root) or not full.endswith(".app.json"):
        raise web.HTTPBadRequest(text="bad app")
    return full




def _describe_app(rel):
    with open(_safe_app_path(rel), encoding="utf-8") as f:
        wf = json.load(f)
    nodes = {str(n["id"]): n for n in wf.get("nodes", [])}
    ld = (wf.get("extra") or {}).get("linearData") or {}
    inputs = []
    for entry in ld.get("inputs", []):
        key = entry[0]
        parts = key.split(":")
        nid, wname = parts[-2], parts[-1]
        node = nodes.get(nid)
        if not node:
            continue
        label = None
        for i in node.get("inputs", []) or []:
            if (i.get("widget") or {}).get("name") == wname and i.get("label"):
                label = i["label"]
        label = label or node.get("title") or wname
        t = node.get("type")
        wv = node.get("widgets_values") or []
        item = {"key": f"{nid}:{wname}", "node": nid, "widget": wname, "label": label, "nodeType": t, "title": node.get("title") or t}
        if t == "CustomCombo":
            item.update(kind="choice", options=_combo_options(node), value=wv[0] if wv else None)
        elif t == "PrimitiveBoolean":
            item.update(kind="toggle", value=bool(wv[0]) if wv else False)
        elif t == "PrimitiveInt":
            seed = "seed" in label.lower()
            item.update(kind="seed" if seed else "int", value=wv[0] if wv else 0)
        elif t == "PrimitiveFloat":
            item.update(kind="float", value=wv[0] if wv else 0.0)
        elif t in ("PrimitiveStringMultiline", "PrimitiveString"):
            item.update(kind="text", multiline=t.endswith("Multiline"), value=wv[0] if wv else "")
        elif t == "LoadImage":
            item.update(kind="image", value=wv[0] if wv else "")
        elif t == "LoadImageMask":
            item.update(kind="mask", value=wv[0] if wv else "")
        elif t == "LoadVideo":
            item.update(kind="video", value=wv[0] if wv else "")
        elif t == "LoadAudio":
            item.update(kind="audio", value=wv[0] if wv else "")
        else:
            idx = 0
            item.update(kind="raw", value=wv[idx] if wv else None)
        inputs.append(item)
    name = os.path.basename(rel)[: -len(".app.json")]
    outputs = [str(o) for o in ld.get("outputs", [])]
    out_types = [nodes[o]["type"] for o in outputs if o in nodes]
    kind = "video" if any("Video" in t for t in out_types) else "image"
    return {"id": rel, "name": name, "kind": kind, "inputs": inputs, "outputs": outputs}






async def _build_prompt(app_id, values):
    with open(_safe_app_path(app_id), encoding="utf-8") as f:
        wf = json.load(f)
    wf = _apply_values(wf, values)
    oi = await _get_object_info()
    try:
        api = convert_ui_to_api(wf, oi)
    except Exception:
        oi = await _get_object_info(force=True)
        api = convert_ui_to_api(wf, oi)
    return wf, _prune_switches(_fix_custom_combos(api, wf), oi)


# ---------------------------------------------------------------- pages
@routes.get("/manny")
async def manny_root(request):
    raise web.HTTPFound("/manny/")


@routes.get("/studio")
async def studio_root(request):
    raise web.HTTPFound("/manny/")


@routes.get("/manny/")
async def manny_index(request):
    return web.FileResponse(os.path.join(WEB, "index.html"), headers={"Cache-Control": "no-cache"})


@routes.get("/manny/static/{name}")
async def manny_static(request):
    name = request.match_info["name"]
    full = os.path.realpath(os.path.join(WEB, name))
    if not full.startswith(os.path.realpath(WEB)) or not os.path.isfile(full):
        raise web.HTTPNotFound()
    return web.FileResponse(full, headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- api
@routes.get("/manny/api/info")
async def manny_info(request):
    return web.json_response({"version": VERSION, "apps": _list_apps()})


@routes.get("/manny/api/apps")
async def manny_apps(request):
    out = []
    for rel in _list_apps():
        try:
            out.append(_describe_app(rel))
        except Exception as e:  # a broken app file shouldn't hide the others
            out.append({"id": rel, "name": rel, "error": str(e), "inputs": []})
    return web.json_response(out)


def _privatize(api):
    """Send every saved file to the hidden incoming folder; the sweeper moves it to Comfyui-Private."""
    n = 0
    for nid in list(api.keys()):
        node = api[nid]
        if node.get("class_type") not in ("SaveImage", "SaveVideo", "SaveAnimatedWEBP", "SaveAnimatedPNG", "VHS_VideoCombine",
                                          "SaveAudio", "SaveAudioMP3", "SaveAudioOpus", "SaveAudioAdvanced"):
            continue
        key = "filename_prefix"
        orig = node["inputs"].get(key, "Studio")
        n += 1
        cid = f"privpfx{n}"
        api[cid] = {"class_type": "StringConcatenate", "inputs": {"string_a": INCOMING, "string_b": orig, "delimiter": "/"},
                    "_meta": {"title": "private prefix"}}
        node["inputs"][key] = [cid, 0]
    return api


@routes.post("/manny/api/run")
async def manny_run(request):
    body = await request.json()
    app_id = body["app"]
    values = dict(body.get("values") or {})
    count = max(1, min(8, int(body.get("count", 1))))
    client_id = body.get("client_id") or uuid.uuid4().hex
    seed_keys = body.get("random_seed_keys") or []
    private = bool(body.get("private"))  # writing privately needs no unlock; viewing does
    target = body.get("target") or _remote.LOCAL    # "local", "auto" or a worker label
    worker = None
    if target not in (_remote.LOCAL, _remote.AUTO):
        try:
            worker = _remote.find(_data_dir(), target)
        except KeyError as e:
            return web.json_response({"error": str(e)}, status=400)
    results = []
    async with ClientSession() as s:
        for i in range(count):
            vals = dict(values)
            for k in seed_keys:
                vals[k] = random.randint(1, 2**31 - 1)
            wf, api = await _build_prompt(app_id, vals)
            if body.get("loras"):
                _inject_loras(api, body["loras"])
            if body.get("checkpoint"):
                _inject_checkpoint(api, body["checkpoint"])
            if isinstance(body.get("unet"), dict) and body["unet"].get("file"):
                _inject_unet(api, body["unet"].get("family"), body["unet"]["file"])
            if body.get("motion_video"):
                _previs.inject_motion(api, body["motion_video"])
            if body.get("pose"):
                _poses.inject_pose(api, body["pose"], body.get("pose_strength"))
            if private:
                _privatize(api)
            payload = {
                "prompt": api,
                "client_id": client_id,
                "extra_data": {"extra_pnginfo": {"workflow": wf}, "manny_app": app_id},
            }
            if target == _remote.AUTO:
                why_local = _remote.gate(api, await _get_object_info())
                label, notes = await _remote.pick(_remote.load_workers(_data_dir()), api, _local_busy(), not why_local)
                if label is None:
                    notes[_remote.LOCAL] = why_local
                    return web.json_response({"error": "No machine can run this: " + "; ".join(
                        f"{k}: {(v[0] or {}).get('reason', '')}" for k, v in notes.items() if v), "refused_by": notes, "queued": results}, status=409)
                worker = None if label == _remote.LOCAL else _remote.find(_data_dir(), label)
            if worker:
                try:
                    results.append(dict(await _remote.submit(worker, api, {k: v for k, v in payload.items() if k != "prompt"}, _dirs(), private, INCOMING,
                                                             on_done=_remote_done), seed=[vals.get(k) for k in seed_keys]))
                except _remote.GateRefused as e:
                    return web.json_response({"error": str(e), "refused": e.why, "worker": e.label, "queued": results}, status=409)
                except Exception as e:
                    return web.json_response({"error": str(e), "worker": worker["label"], "queued": results}, status=502)
                continue
            async with s.post(f"http://127.0.0.1:{_port()}/prompt", json=payload) as r:
                data = await r.json()
                if r.status != 200:
                    return web.json_response({"error": data, "queued": results}, status=400)
            if data.get("node_errors"):     # queued for the outputs that validated: the picture would be missing
                async with s.post(f"http://127.0.0.1:{_port()}/queue", json={"delete": [data.get("prompt_id")]}):
                    pass
                async with s.post(f"http://127.0.0.1:{_port()}/interrupt", json={"prompt_id": data.get("prompt_id")}):
                    pass
                return web.json_response({"error": {"error": {"message": "Part of this can't run on this PC"}, "node_errors": data["node_errors"]},
                                          "queued": results}, status=400)
            results.append({"prompt_id": data.get("prompt_id"), "number": data.get("number"), "seed": [vals.get(k) for k in seed_keys],
                            "worker": _remote.LOCAL})
            if private:
                _pending_private.add(data.get("prompt_id"))
    return web.json_response({"queued": results})


# ---------------------------------------------------------------- remote GPU workers (remote.py)
def _dirs():
    return {"input": folder_paths.get_input_directory(), "output": folder_paths.get_output_directory(),
            "temp": folder_paths.get_temp_directory()}


@routes.get("/manny/api/workers")
async def manny_workers(request):
    """This PC plus every configured worker, with live health (and its error when it's down)."""
    async def one(w):
        try:
            return dict(w, online=True, info=await _remote.info(w))
        except Exception as e:
            return dict(w, online=False, error=str(e))
    ws = await asyncio.gather(*(one(w) for w in _remote.load_workers(_data_dir())))
    return web.json_response([{"label": _remote.LOCAL, "host": "127.0.0.1", "port": _port(), "online": True}] + list(ws))


@routes.post("/manny/api/workers")
async def manny_workers_save(request):
    """Replace the worker list. Body: {"workers": [{label, host, port, device?}]} (comfy-mac's mac-workers.json works as is)."""
    body = await request.json()
    _remote.save_workers(_data_dir(), body.get("workers") or [])
    return web.json_response({"workers": _remote.load_workers(_data_dir())})


@routes.post("/manny/api/workers/check")
async def manny_workers_check(request):
    """Which machines can run this app with these values: {label: {"ok", "refused": [...]}}."""
    body = await request.json()
    _wf, api = await _build_prompt(body["app"], body.get("values") or {})
    out = {_remote.LOCAL: {"ok": True, "refused": _remote.gate(api, await _get_object_info())}}
    out[_remote.LOCAL]["ok"] = not out[_remote.LOCAL]["refused"]
    for w in _remote.load_workers(_data_dir()):
        try:
            why = await _remote.check(w, api)
            out[w["label"]] = {"ok": not why, "refused": why}
        except Exception as e:
            out[w["label"]] = {"ok": False, "error": str(e)}
    return web.json_response(out)


def _local_busy():
    try:
        return PromptServer.instance.prompt_queue.get_tasks_remaining()
    except Exception:
        return 0


def _remote_done(job):
    """Tell the browser that queued it, the same way ComfyUI does for a local job, so Create/Gallery/Storyboard
    pick the pulled-back file up unchanged."""
    sid = job.get("client_id")
    try:
        if job["status"] == "done":
            for k, o in enumerate(job["outputs"]):
                f = {"filename": o["filename"], "subfolder": o["subfolder"], "type": "output"}
                PromptServer.instance.send_sync("executed", {"prompt_id": job["prompt_id"], "node": f"remote{k}", "display_node": f"remote{k}",
                                                            "output": {"images": [f]}, "worker": job["worker"]}, sid)
            PromptServer.instance.send_sync("execution_success", {"prompt_id": job["prompt_id"], "worker": job["worker"]}, sid)
        elif job["status"] == "failed":
            PromptServer.instance.send_sync("execution_error", {"prompt_id": job["prompt_id"], "exception_message": job["error"] or "failed",
                                                               "worker": job["worker"]}, sid)
    except Exception as e:
        print("[Studio] remote job event:", e)


@routes.get("/manny/api/remote_jobs")
async def manny_remote_jobs(request):
    return web.json_response(_remote.jobs())


@routes.post("/manny/api/remote_jobs/{pid}/cancel")
async def manny_remote_cancel(request):
    job = _remote.JOBS.get(request.match_info["pid"])
    if not job:
        return web.json_response({"error": "unknown job"}, status=404)
    try:
        fg = await _remote.cancel(_remote.find(_data_dir(), job["worker"]), job["prompt_id"])
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)
    return web.json_response({"cancelled": job["prompt_id"], "forgot": fg})


@routes.post("/manny/api/preview_prompt")
async def manny_preview(request):
    """Returns the API prompt without queueing (for checking)."""
    body = await request.json()
    _wf, api = await _build_prompt(body["app"], body.get("values") or {})
    if body.get("save"):
        p = os.path.join(_data_dir(), "last_prompt_api.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(api, f, indent=1)
    return web.json_response(api)


MEDIA_EXT = {".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
             ".mp4": "video", ".webm": "video", ".mov": "video", ".gif": "image",
             ".tif": "image", ".tiff": "image"}  # Gigapixel can save TIFF; Studio shows it through cached previews


_meta_cache = {}


def _read_meta(full):
    """Returns (prompt_api, workflow_ui, (w, h)) embedded by ComfyUI in a PNG or video file."""
    try:
        st = os.stat(full)
    except OSError:
        return None, None, None
    key = (full, st.st_mtime, st.st_size)
    if key in _meta_cache:
        return _meta_cache[key]
    prompt = wf = size = None
    ext = os.path.splitext(full)[1].lower()
    try:
        if ext in (".png", ".webp", ".jpg", ".jpeg", ".tif", ".tiff"):
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = None
            from PIL import Image
            with Image.open(full) as im:
                size = im.size
                info = dict(im.info)
            prompt, wf = info.get("prompt"), info.get("workflow")
        elif ext in (".mp4", ".webm", ".mov"):
            import av
            with av.open(full) as c:
                md = dict(c.metadata or {})
                if c.streams.video:
                    v = c.streams.video[0]
                    size = (v.codec_context.width, v.codec_context.height)
            prompt = md.get("prompt") or md.get("PROMPT")
            wf = md.get("workflow") or md.get("WORKFLOW")
            if not prompt and md.get("comment"):
                try:
                    cm = json.loads(md["comment"])
                    prompt, wf = cm.get("prompt"), cm.get("workflow")
                except Exception:
                    pass
        prompt = json.loads(prompt) if isinstance(prompt, str) else prompt
        wf = json.loads(wf) if isinstance(wf, str) else wf
    except Exception:
        pass
    _meta_cache[key] = (prompt, wf, size)
    return prompt, wf, size


PROMPT_TITLES = ("PROMPT", "WHAT TO CHANGE", "WHAT TO PUT THERE")


def _prompt_text(prompt_api):
    if not isinstance(prompt_api, dict):
        return ""
    best = ""
    for nid, n in prompt_api.items():
        if not isinstance(n, dict):
            continue
        title = ((n.get("_meta") or {}).get("title") or "").upper()
        if n.get("class_type", "").startswith("PrimitiveString") and any(title.startswith(t) for t in PROMPT_TITLES):
            v = (n.get("inputs") or {}).get("value")
            if isinstance(v, str):
                return v
        if n.get("class_type") == "CLIPTextEncode" and not best:
            v = (n.get("inputs") or {}).get("text")
            if isinstance(v, str) and len(v) > len(best):
                best = v
    return best


def _favs_file():
    return os.path.join(_data_dir(), "favorites.json")


def _load_favs():
    try:
        with open(_favs_file(), encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _scan_gallery(kind, limit, private=False):
    root = _root(private)
    favs = _load_favs()
    pfx = "P:" if private else ""
    items = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith((".", "_trash"))]
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            k = MEDIA_EXT.get(ext)
            if not k or (kind and kind != k):
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            sub = os.path.relpath(dirpath, root).replace("\\", "/")
            sub = "" if sub == "." else sub
            items.append({"filename": fn, "subfolder": sub, "kind": k, "mtime": st.st_mtime, "size": st.st_size,
                          "fav": f"{pfx}{sub}/{fn}" in favs, "_full": full, "private": private})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    items = items[:limit]
    for it in items:
        pa, _wf, _sz = _read_meta(it.pop("_full"))
        it["prompt"] = _prompt_text(pa)[:600]
    return items


@routes.get("/manny/api/gallery")
async def manny_gallery(request):
    kind = request.query.get("kind")
    limit = int(request.query.get("limit", "600"))
    private = _flag(request.query.get("private"))
    if private:
        _need_private(request)
    items = await asyncio.to_thread(_scan_gallery, kind, limit, private)
    return web.json_response(items)


@routes.post("/manny/api/favorite")
async def manny_favorite(request):
    b = await request.json()
    if b.get("private"):
        _need_private(request)
    key = ("P:" if b.get("private") else "") + f"{b.get('subfolder', '')}/{b['filename']}"
    favs = _load_favs()
    if b.get("on"):
        favs.add(key)
    else:
        favs.discard(key)
    with open(_favs_file(), "w", encoding="utf-8") as f:
        json.dump(sorted(favs), f)
    return web.json_response({"fav": key in favs})


def _out_path(filename, subfolder, private=False):
    roots = [_priv_root(), os.path.join(folder_paths.get_output_directory(), INCOMING)] if private else [folder_paths.get_output_directory()]
    sub = (subfolder or "").replace("\\", "/")
    if private and sub.startswith(INCOMING):
        sub = sub[len(INCOMING):].lstrip("/")
    for r in roots:
        root = os.path.realpath(r)
        full = os.path.realpath(os.path.join(root, sub, filename))
        if full.startswith(root) and os.path.isfile(full):
            return full
    raise web.HTTPBadRequest(text="bad file")


def _value_from(prompt_api, wf_nodes, node, widget):
    """Value a saved node had: the API prompt first (exact), else the UI workflow."""
    nid = str(node.get("id"))
    if isinstance(prompt_api, dict) and nid in prompt_api:
        ins = prompt_api[nid].get("inputs") or {}
        if widget in ins and not isinstance(ins[widget], list):
            return ins[widget]
    wv = node.get("widgets_values") or []
    return wv[0] if wv else None


@routes.get("/manny/api/meta")
async def manny_meta(request):
    private = _flag(request.query.get("private"))
    if private:
        _need_private(request)
    full = _out_path(request.query["filename"], request.query.get("subfolder", ""), private)
    prompt_api, wf, size = await asyncio.to_thread(_read_meta, full)
    res = {"prompt": _prompt_text(prompt_api), "size": size, "settings": [], "remix": None}
    nodes = (wf or {}).get("nodes") or []
    if not nodes and isinstance(prompt_api, dict):
        # no UI workflow saved: fall back to the API prompt's titled nodes
        nodes = [{"id": k, "type": v.get("class_type"), "title": (v.get("_meta") or {}).get("title")} for k, v in prompt_api.items() if isinstance(v, dict)]
    by_title = {}
    for n in nodes:
        t = (n.get("title") or "").strip()
        if t and t not in by_title:
            by_title[t] = n
    best = None
    for rel in _list_apps():
        try:
            app = _describe_app(rel)
        except Exception:
            continue
        vals, hits = {}, 0
        for i in app["inputs"]:
            n = by_title.get(i["title"])
            if not n or n.get("type") != i["nodeType"]:
                continue
            v = _value_from(prompt_api, nodes, n, i["widget"])
            if v is None:
                continue
            vals[i["key"]] = v
            hits += 1
        if hits and (best is None or hits > best[0]):
            best = (hits, app, vals)
    if best:
        _h, app, vals = best
        res["remix"] = {"app": app["id"], "values": vals}
        for i in app["inputs"]:
            v = vals.get(i["key"])
            if v is None or i["kind"] in ("text", "image", "mask", "video", "audio"):
                continue
            if i["kind"] == "toggle" and not v:
                continue
            if isinstance(v, str) and "(" in v:
                v = v.split("(")[0].strip()
            res["settings"].append([i["label"], v])
    return web.json_response(res)


@routes.post("/manny/api/trash")
async def manny_trash(request):
    """Moves an output file to output/_trash (recoverable), never deletes."""
    body = await request.json()
    private = bool(body.get("private"))
    if private:
        _need_private(request)
    full = _out_path(body["filename"], body.get("subfolder", ""), private)
    trash = os.path.join(_root(private), "_trash")
    os.makedirs(trash, exist_ok=True)
    dest = os.path.join(trash, os.path.basename(full))
    if os.path.exists(dest):
        base, ext = os.path.splitext(dest)
        dest = f"{base}_{int(time.time())}{ext}"
    shutil.move(full, dest)
    return web.json_response({"ok": True})


@routes.post("/manny/api/use_output")
async def manny_use_output(request):
    """Copies an output file into ComfyUI's input folder so an app can use it."""
    body = await request.json()
    private = bool(body.get("private"))
    if private:
        _need_private(request)
    full = _out_path(body["filename"], body.get("subfolder", ""), private)
    sub = PRIV_INPUT if private else "manny"
    dest_dir = os.path.join(folder_paths.get_input_directory(), sub)
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(full, os.path.join(dest_dir, os.path.basename(full)))
    return web.json_response({"value": f"{sub}/" + os.path.basename(full)})


@routes.get("/manny/api/pfile")
async def manny_pfile(request):
    """Serves a private render to an unlocked browser only."""
    _need_private(request)
    rel = request.query.get("path", "").replace("\\", "/")
    sub, _, fn = rel.rpartition("/")
    full = _out_path(fn, sub, True)
    return web.FileResponse(full, headers={"Cache-Control": "private, no-store"})


@routes.get("/manny/api/characters")
async def manny_chars(request):
    private = _flag(request.query.get("private"))
    if private:
        _need_private(request)
    return web.json_response([c for c in _load_chars() if bool(c.get("private")) == private])


@routes.post("/manny/api/characters")
async def manny_chars_save(request):
    c = await request.json()
    if c.get("private"):
        _need_private(request)
    chars = _load_chars()
    old = next((x for x in chars if x.get("id") == c.get("id")), None)
    if old and old.get("private") and not auth.unlocked(request):
        raise web.HTTPForbidden(text="Incognito is locked")
    if not c.get("id"):
        c["id"] = uuid.uuid4().hex[:10]
        c["created"] = time.time()
    c["updated"] = time.time()
    chars = [x for x in chars if x.get("id") != c["id"]] + [c]
    _save_chars(chars)
    return web.json_response(c)


@routes.delete("/manny/api/characters/{cid}")
async def manny_chars_delete(request):
    cid = request.match_info["cid"]
    old = next((x for x in _load_chars() if x.get("id") == cid), None)
    if old and old.get("private"):
        _need_private(request)
    _save_chars([x for x in _load_chars() if x.get("id") != cid])
    return web.json_response({"ok": True})


# ---------------------------------------------------------------- storyboards
def _boards_file():
    return os.path.join(_data_dir(), "storyboards.json")


def _load_boards():
    try:
        with open(_boards_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_boards(b):
    tmp = _boards_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(b, f, indent=1, ensure_ascii=False)
    os.replace(tmp, _boards_file())


@routes.get("/manny/api/boards")
async def manny_boards(request):
    private = _flag(request.query.get("private"))
    if private:
        _need_private(request)
    return web.json_response([b for b in _load_boards() if bool(b.get("private")) == private])


@routes.post("/manny/api/boards")
async def manny_boards_save(request):
    b = await request.json()
    if b.get("private"):
        _need_private(request)
    boards = _load_boards()
    old = next((x for x in boards if x.get("id") == b.get("id")), None)
    if old and old.get("private") and not auth.unlocked(request):
        raise web.HTTPForbidden(text="Incognito is locked")
    if not b.get("id"):
        b["id"] = uuid.uuid4().hex[:10]
        b["created"] = time.time()
    b["updated"] = time.time()
    boards = [x for x in boards if x.get("id") != b["id"]] + [b]
    _save_boards(boards)
    return web.json_response(b)


@routes.delete("/manny/api/boards/{bid}")
async def manny_boards_delete(request):
    bid = request.match_info["bid"]
    old = next((x for x in _load_boards() if x.get("id") == bid), None)
    if old and old.get("private"):
        _need_private(request)
    _save_boards([x for x in _load_boards() if x.get("id") != bid])
    return web.json_response({"ok": True})


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def _probe(full):
    import av
    with av.open(full) as c:
        v = c.streams.video[0]
        has_audio = bool(c.streams.audio)
        dur = float(c.duration / 1_000_000) if c.duration else float(v.frames / (v.average_rate or 24))
        return v.codec_context.width, v.codec_context.height, has_audio, dur


@routes.post("/manny/api/join")
async def manny_join(request):
    """Joins clips (in order) into one film: same size as the first clip, 24 fps, silent clips get silence."""
    b = await request.json()
    private = bool(b.get("private"))
    if private:
        _need_private(request)
    clips = [_out_path(c["filename"], c.get("subfolder", ""), private) for c in b.get("clips", [])]
    if len(clips) < 1:
        raise web.HTTPBadRequest(text="no clips")
    ff = _ffmpeg()
    if not ff:
        raise web.HTTPBadRequest(text="ffmpeg not found")
    info = [await asyncio.to_thread(_probe, c) for c in clips]
    W, H = info[0][0] // 2 * 2, info[0][1] // 2 * 2
    cmd = [ff, "-y"]
    for c in clips:
        cmd += ["-i", c]
    n = len(clips)
    extra = 0
    parts, chains = [], []
    for k, (w_, h_, has_a, dur) in enumerate(info):
        chains.append(f"[{k}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v{k}]")
        if has_a:
            chains.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo[a{k}]")
        else:
            idx = n + extra
            extra += 1
            cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            chains.append(f"[{idx}:a]aformat=channel_layouts=stereo[a{k}]")
        parts.append(f"[v{k}][a{k}]")
    fc = ";".join(chains) + ";" + "".join(parts) + f"concat=n={n}:v=1:a=1[v][a]"
    name = "".join(ch for ch in (b.get("name") or "Film") if ch.isalnum() or ch in " -_").strip() or "Film"
    out_dir = os.path.join(_root(private), "Videos", "Films")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{name} {time.strftime('%Y-%m-%d %H%M%S')}.mp4")
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "17", "-preset", "medium",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _o, err = await proc.communicate()
    if proc.returncode != 0:
        return web.json_response({"error": err.decode("utf-8", "ignore")[-1500:]}, status=500)
    return web.json_response({"filename": os.path.basename(out), "subfolder": "Videos/Films", "private": private})


# ---------------------------------------------------------------- fast previews: cached thumbnails and video proxies
import hashlib as _hashlib
import subprocess as _subprocess
_thumb_sem = asyncio.Semaphore(2)      # huge (Gigapixel) pictures use lots of memory while shrinking
_cache_locks = {}
_proxy_busy = set()
_CACHE_HDR = "max-age=31536000, immutable"


def _cache_dir(private):
    d = os.path.join(_priv_root(), ".cache") if private else os.path.join(_data_dir(), "cache")
    os.makedirs(d, exist_ok=True)
    return d


def _ckey(full, tag):
    st = os.stat(full)
    return _hashlib.sha1(f"{full}|{st.st_mtime}|{st.st_size}|{tag}".encode("utf-8", "ignore")).hexdigest()[:24]


def _make_thumb(full, dest, w):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = None
    if MEDIA_EXT.get(os.path.splitext(full)[1].lower()) == "video":
        import av
        img = None
        with av.open(full) as c:
            for i, fr in enumerate(c.decode(video=0)):
                img = fr.to_image()
                if (fr.time or 0) >= 0.4 or i >= 12:
                    break
        if img is None:
            raise RuntimeError("no frames")
    else:
        img = Image.open(full)
        if img.format == "JPEG":
            img.draft("RGB", (w, w))
        img = ImageOps.exif_transpose(img)
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        img = img.point(lambda x: x * (1 / 256)).convert("L")
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.mode or img.mode == "P" else "RGB")
    img.thumbnail((w, w), Image.LANCZOS)
    tmp = dest + ".tmp"
    img.save(tmp, "WEBP", quality=82, method=4)
    os.replace(tmp, dest)


@routes.get("/manny/api/thumb")
async def manny_thumb(request):
    """A small WebP copy of a picture (or a video's first frame), made once and cached."""
    q = request.query
    private = _flag(q.get("private"))
    if private:
        _need_private(request)
    full = _out_path(q["filename"], q.get("subfolder", ""), private)
    w = int(q.get("w", "640") or 640)
    w = 640 if w <= 640 else 1600 if w <= 1600 else 2560
    dest = os.path.join(_cache_dir(private), _ckey(full, f"t{w}") + ".webp")
    if not os.path.isfile(dest):
        lock = _cache_locks.setdefault(dest, asyncio.Lock())
        async with lock:
            if not os.path.isfile(dest):
                async with _thumb_sem:
                    try:
                        await asyncio.to_thread(_make_thumb, full, dest, w)
                    except Exception as e:
                        return web.Response(status=415, text=f"no preview: {e}")
        _cache_locks.pop(dest, None)
    return web.FileResponse(dest, headers={"Cache-Control": ("private, " if private else "public, ") + _CACHE_HDR})


def _make_proxy(full, dest):
    ff = _ffmpeg()
    tmp = dest + ".tmp.mp4"
    vf = "scale='if(gte(iw,ih),-2,min(720,iw))':'if(gte(iw,ih),min(720,ih),-2)',format=yuv420p"
    cmd = [ff, "-y", "-i", full, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", tmp]
    flags = getattr(_subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)   # Windows: don't slow down renders
    r = _subprocess.run(cmd, stdout=_subprocess.DEVNULL, stderr=_subprocess.PIPE, creationflags=flags)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "ignore")[-300:])
    os.replace(tmp, dest)


def _proxy_job(full, dest):
    try:
        _make_proxy(full, dest)
    except Exception as e:
        print("[Studio] proxy failed:", e)
    finally:
        _proxy_busy.discard(dest)


def _start_proxy(full, private):
    dest = os.path.join(_cache_dir(private), _ckey(full, "p720") + ".mp4")
    if os.path.isfile(dest) or dest in _proxy_busy:
        return dest
    _proxy_busy.add(dest)
    threading.Thread(target=_proxy_job, args=(full, dest), daemon=True).start()
    return dest


@routes.get("/manny/api/proxy")
async def manny_proxy(request):
    """A light 720p copy of a big video for phones and other computers. The first time, it
    sends the original while the light copy is made in the background."""
    q = request.query
    private = _flag(q.get("private"))
    if private:
        _need_private(request)
    fn, sub = q["filename"], q.get("subfolder", "")
    full = _out_path(fn, sub, private)
    dest = _start_proxy(full, private)
    if os.path.isfile(dest):
        return web.FileResponse(dest, headers={"Cache-Control": ("private, " if private else "public, ") + _CACHE_HDR})
    from urllib.parse import quote
    path = (sub + "/" if sub else "") + fn
    orig = f"/manny/api/pfile?path={quote(path)}" if private else f"/view?filename={quote(fn)}&subfolder={quote(sub)}&type=output"
    raise web.HTTPFound(orig, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- takes: finish and continue
import re as _re
_jobs = {}


def _app_by_name(name):
    for rel in _list_apps():
        n = os.path.basename(rel)[: -len(".app.json")]
        if _re.sub(r"^(Manny\s+)?Studio\s*-\s*", "", n, flags=_re.I).strip().lower() == name:
            return _describe_app(rel)
    raise RuntimeError(f"The “{name}” app is missing")


async def _queue_app(app, values, private, label):
    wf, api = await _build_prompt(app["id"], values)
    if private:
        _privatize(api)
    payload = {"prompt": api, "client_id": "studio-finish",
               "extra_data": {"extra_pnginfo": {"workflow": wf}, "manny_app": app["id"], "manny_label": label}}
    async with ClientSession() as s:
        async with s.post(f"http://127.0.0.1:{_port()}/prompt", json=payload) as r:
            data = await r.json()
            if r.status != 200:
                raise RuntimeError(json.dumps(data)[:600])
    pid = data.get("prompt_id")
    if private:
        _pending_private.add(pid)
    return pid


async def _wait_output(pid, job):
    async with ClientSession() as s:
        while True:
            if job.get("cancel"):
                raise RuntimeError("Stopped")
            async with s.get(f"http://127.0.0.1:{_port()}/history/{pid}") as r:
                h = (await r.json()).get(pid)
            if h:
                for o in (h.get("outputs") or {}).values():
                    for f in (o.get("images") or []) + (o.get("gifs") or []) + (o.get("videos") or []) + (o.get("audio") or []):
                        if f.get("type") == "output":
                            return f["filename"], f.get("subfolder", "")
                st = (h.get("status") or {}).get("status_str")
                if st == "error":
                    msgs = [m[1].get("exception_message", "") for m in (h.get("status") or {}).get("messages", []) if m and m[0] == "execution_error"]
                    raise RuntimeError("The render failed. " + " ".join(msgs)[:400])
                if (h.get("status") or {}).get("completed"):
                    raise RuntimeError("The render finished without a file")
            await asyncio.sleep(3)


def _to_input(full, private):
    sub = PRIV_INPUT if private else "manny"
    d = os.path.join(folder_paths.get_input_directory(), sub)
    os.makedirs(d, exist_ok=True)
    shutil.copy2(full, os.path.join(d, os.path.basename(full)))
    return f"{sub}/" + os.path.basename(full)


def _input_path(value):
    root = os.path.realpath(folder_paths.get_input_directory())
    full = os.path.realpath(os.path.join(root, value))
    if not full.startswith(root) or not os.path.isfile(full):
        raise RuntimeError("That sound file is missing")
    return full


def _find_out(fn, sub, private, tries=20):
    for _ in range(tries):
        try:
            return _out_path(fn, sub, private)
        except web.HTTPException:
            time.sleep(0.5)  # the private sweeper may be moving it
    raise RuntimeError("Couldn't find the rendered file " + fn)


def _set(app, values, label_start, val):
    i = next((x for x in app["inputs"] if x["label"].lower().startswith(label_start)), None)
    if i:
        values[i["key"]] = val
    return i


async def _finish(job):
    b = job["req"]
    private = job["private"]
    orig = _out_path(b["filename"], b.get("subfolder", ""), private)
    cur = orig
    temps = []
    try:
        fps = str(b.get("fps") or "keep")
        used_topaz = False
        if (b.get("upscale") or fps in ("48", "60")) and b.get("engine") != "comfy":
            try:
                if await asyncio.to_thread(_topaz.video_ready):
                    tdir = os.path.join(_root(private), ".topaz_work")
                    cur = await _topaz.run_video(job, cur, (b.get("size") or "1080") if b.get("upscale") else None, fps, tdir, "finish " + job["id"])
                    temps.append(cur)
                    used_topaz = True
            except Exception as e:
                if job.get("cancel"):
                    raise
                job["topaz_error"] = str(e)
                job["stage"] = "Topaz failed, using the ComfyUI upscaler"
                cur = orig
        if b.get("upscale") and not used_topaz:
            job["stage"] = "Upscaling to 1080p"
            app = _app_by_name("upscale video")
            vals = {}
            if not _set(app, vals, "video", _to_input(cur, private)):
                vals[next(x["key"] for x in app["inputs"] if x["kind"] == "video")] = _to_input(cur, private)
            pid = await _queue_app(app, vals, private, "Finish: upscale")
            job["pids"].append(pid)
            fn, sub = await _wait_output(pid, job)
            cur = await asyncio.to_thread(_find_out, fn, sub, private)
            temps.append(cur)
        if fps in ("48", "60") and not used_topaz:
            job["stage"] = f"Smoothing to {fps} fps"
            app = _app_by_name("smooth motion")
            vals = {}
            vk = next(x["key"] for x in app["inputs"] if x["kind"] == "video")
            vals[vk] = _to_input(cur, private)
            ch = next(x for x in app["inputs"] if x["kind"] == "choice")
            vals[ch["key"]] = ch["options"][0 if fps == "48" else 1]
            pid = await _queue_app(app, vals, private, "Finish: smooth")
            job["pids"].append(pid)
            fn, sub = await _wait_output(pid, job)
            cur = await asyncio.to_thread(_find_out, fn, sub, private)
            temps.append(cur)
        job["stage"] = "Final file"
        ff = _ffmpeg()
        if not ff:
            raise RuntimeError("ffmpeg not found")
        sound = b.get("sound") or "keep"
        cmd = [ff, "-y", "-i", cur, "-i", orig]
        if sound == "replace":
            cmd += ["-i", _input_path(b.get("audio") or "")]
        vf = "fps=60," if fps == "60" else ""
        cmd += ["-map", "0:v:0", "-vf", vf + "format=yuv420p", "-c:v", "libx264", "-crf", "16", "-preset", "medium"]
        if sound == "keep":
            cmd += ["-map", "1:a:0?", "-c:a", "aac", "-b:a", "192k"]
        elif sound == "replace":
            _w, _h, _a, dur = await asyncio.to_thread(_probe, cur)
            fade = max(0.0, dur - 0.6)
            cmd += ["-map", "2:a:0", "-af", f"afade=t=out:st={fade:.2f}:d=0.6", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        else:
            cmd += ["-an"]
        name = "".join(ch for ch in (b.get("name") or os.path.splitext(os.path.basename(orig))[0]) if ch.isalnum() or ch in " -_").strip() or "Shot"
        out_dir = os.path.join(_root(private), "Videos", "Finished")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"{name} final {time.strftime('%Y-%m-%d %H%M%S')}.mp4")
        cmd += ["-movflags", "+faststart", out]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _o, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg: " + err.decode("utf-8", "ignore")[-600:])
        # the in-between files go to the trash (recoverable), the gallery keeps only the final one
        for t in temps:
            try:
                trash = os.path.join(_root(private), "_trash")
                os.makedirs(trash, exist_ok=True)
                _move_into(t, os.path.join(trash, os.path.basename(t)))
            except Exception:
                pass
        try:
            _start_proxy(out, private)
        except Exception:
            pass
        job.update(status="done", stage="Done", result={"filename": os.path.basename(out), "subfolder": "Videos/Finished", "private": private})
    except Exception as e:
        job.update(status="failed", stage="Failed", error=str(e))


@routes.post("/manny/api/finish")
async def manny_finish(request):
    b = await request.json()
    private = bool(b.get("private"))
    if private:
        _need_private(request)
    _out_path(b["filename"], b.get("subfolder", ""), private)  # checks the clip exists
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "req": b, "private": private, "status": "running", "stage": "Starting", "pids": [], "at": time.time()}
    _jobs[jid] = job
    asyncio.ensure_future(_finish(job))
    return web.json_response({"id": jid})


@routes.get("/manny/api/finish/{jid}")
async def manny_finish_status(request):
    job = _jobs.get(request.match_info["jid"])
    if not job:
        return web.json_response({"status": "lost", "stage": "Lost (ComfyUI restarted)"})
    if job["private"]:
        _need_private(request)
    return web.json_response({k: job.get(k) for k in ("id", "status", "stage", "error", "result", "pids", "topaz_error")})


@routes.post("/manny/api/finish/{jid}/stop")
async def manny_finish_stop(request):
    job = _jobs.get(request.match_info["jid"])
    if job:
        job["cancel"] = True
    return web.json_response({"ok": True})


def _last_frame(full, dest):
    import av
    last = None
    with av.open(full) as c:
        for fr in c.decode(video=0):
            last = fr
    if last is None:
        raise RuntimeError("no frames")
    last.to_image().save(dest)


@routes.post("/manny/api/lastframe")
async def manny_lastframe(request):
    """Saves a clip's last frame into the input folder, to start the next shot from it."""
    b = await request.json()
    private = bool(b.get("private"))
    if private:
        _need_private(request)
    full = _out_path(b["filename"], b.get("subfolder", ""), private)
    sub = PRIV_INPUT if private else "manny"
    d = os.path.join(folder_paths.get_input_directory(), sub)
    os.makedirs(d, exist_ok=True)
    name = os.path.splitext(os.path.basename(full))[0] + "_last.png"
    await asyncio.to_thread(_last_frame, full, os.path.join(d, name))
    return web.json_response({"value": f"{sub}/{name}"})


# ---------------------------------------------------------------- AI helper (local vision language model, nothing leaves the PC)
LLM_MODELS = ["qwen3VL4BAbliteratedComfyui_FP8.safetensors", "qwen3vl_4b_fp8_scaled.safetensors", "qwen3vl_8b_int8_convrot.safetensors"]


def _llm_model():
    have = set(folder_paths.get_filename_list("text_encoders") or [])
    for m in LLM_MODELS:
        if m in have:
            return m
    raise RuntimeError("No Qwen3-VL model in models/text_encoders")


def _frames(full, n, dest_dir, stem, width=512):
    """n evenly spaced frames of a video (or the picture itself), saved as PNG; returns file names."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    os.makedirs(dest_dir, exist_ok=True)
    out = []
    if MEDIA_EXT.get(os.path.splitext(full)[1].lower()) != "video":
        im = Image.open(full).convert("RGB")
        im.thumbnail((width, width))
        name = f"{stem}_0.png"
        im.save(os.path.join(dest_dir, name))
        return [name]
    import av
    with av.open(full) as c:
        s = c.streams.video[0]
        total = s.frames or 0
        if not total:
            total = sum(1 for _ in c.decode(video=0))
            c.seek(0)
        want = sorted(set(int(round(k * (total - 1) / max(1, n - 1))) for k in range(n)))
        i = 0
        for fr in c.decode(video=0):
            if i in want:
                im = fr.to_image()
                im.thumbnail((width, width))
                name = f"{stem}_{len(out)}.png"
                im.save(os.path.join(dest_dir, name))
                out.append(name)
                if len(out) == len(want):
                    break
            i += 1
    return out


async def _llm(job, prompt, images, private, max_length=700):
    sub = PRIV_INPUT if private else "manny"
    api = {"1": {"class_type": "CLIPLoader", "inputs": {"clip_name": _llm_model(), "type": "ideogram4", "device": "default"}}}
    tg = {"clip": ["1", 0], "prompt": prompt, "max_length": max_length, "sampling_mode": "on",
          "sampling_mode.temperature": 0.5, "sampling_mode.top_k": 64, "sampling_mode.top_p": 0.95, "sampling_mode.min_p": 0.05,
          "sampling_mode.repetition_penalty": 1.05, "sampling_mode.presence_penalty": 0.0, "sampling_mode.seed": random.randint(1, 2**31),
          "thinking": False, "use_default_template": True, "mtp": "auto"}
    for k, name in enumerate(images, 1):
        api[f"i{k}"] = {"class_type": "LoadImage", "inputs": {"image": f"{sub}/{name}"}}
        tg[f"images.image{k}"] = [f"i{k}", 0]
    api["2"] = {"class_type": "TextGenerateV2", "inputs": tg}
    api["3"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}
    async with ClientSession() as s:
        async with s.post(f"http://127.0.0.1:{_port()}/prompt", json={"prompt": api, "client_id": "studio-ai", "extra_data": {"manny_label": "AI"}}) as r:
            data = await r.json()
            if r.status != 200:
                raise RuntimeError("The AI couldn't start: " + json.dumps(data)[:400])
        pid = data["prompt_id"]
        job["pids"].append(pid)
        if private:
            _pending_private.add(pid)
        while True:
            if job.get("cancel"):
                raise RuntimeError("Stopped")
            async with s.get(f"http://127.0.0.1:{_port()}/history/{pid}") as r:
                h = (await r.json()).get(pid)
            if h:
                st = h.get("status") or {}
                if st.get("status_str") == "error":
                    msgs = [m[1].get("exception_message", "") for m in st.get("messages", []) if m and m[0] == "execution_error"]
                    raise RuntimeError("The AI failed. " + " ".join(msgs)[:300])
                for o in (h.get("outputs") or {}).values():
                    t = o.get("text")
                    if t:
                        return t[0] if isinstance(t, list) else str(t)
                if st.get("completed"):
                    raise RuntimeError("The AI returned nothing")
            await asyncio.sleep(2)


def _json_from(text):
    import re as _r
    m = _r.search(r"\{.*\}", text or "", _r.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


REVIEW_PROMPT = """You are a strict film editor reviewing one AI-generated video shot. The images are {n} frames taken in order, evenly spaced from start to end.
The shot was supposed to show: "{want}"
Judge it and answer ONLY with JSON in this shape:
{{"score": 1-10, "prompt_match": 1-10, "motion": 1-10, "anatomy": 1-10, "artifacts": 1-10,
 "verdict": "keep" or "redo",
 "good": ["short points"],
 "problems": ["short, concrete problems, with the frame number when you can"],
 "better_prompt": "a rewritten prompt that would fix the problems"}}
Scores: 10 is perfect. anatomy covers faces, hands and bodies. artifacts covers flicker, warping, morphing and blur."""

PROMPT_HELP = """You write prompts for an AI {kind} model. Rewrite the idea below into one strong prompt.
Rules: plain present-tense description; {rules} Keep every detail the person gave; add only what helps (light, framing, mood). No lists, no quotes around it, 60 to 120 words.
{img}Idea: "{idea}"
Answer with the prompt only."""


async def _ai_job(job, fn):
    try:
        job["result"] = await fn()
        job.update(status="done", stage="Done")
    except Exception as e:
        job.update(status="failed", stage="Failed", error=str(e))
    finally:
        for p in job.get("temps", []):
            try:
                os.remove(p)
            except Exception:
                pass


def _new_job(private, **kw):
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "private": private, "status": "running", "stage": "Starting", "pids": [], "at": time.time(), "temps": []}
    job.update(kw)
    _jobs[jid] = job
    return job


@routes.post("/manny/api/ai/review")
async def manny_ai_review(request):
    b = await request.json()
    private = bool(b.get("private"))
    if private:
        _need_private(request)
    full = _out_path(b["filename"], b.get("subfolder", ""), private)
    job = _new_job(private)
    sub = PRIV_INPUT if private else "manny"
    d = os.path.join(folder_paths.get_input_directory(), sub)

    async def run():
        job["stage"] = "Looking at the shot"
        names = await asyncio.to_thread(_frames, full, 8, d, "review_" + job["id"])
        job["temps"] += [os.path.join(d, n) for n in names]
        want = (b.get("prompt") or "").strip() or "(no prompt given: judge it on quality alone)"
        text = await _llm(job, REVIEW_PROMPT.format(n=len(names), want=want.replace('"', "'")[:1500]), names, private, 800)
        return {"text": text, "review": _json_from(text)}
    asyncio.ensure_future(_ai_job(job, run))
    return web.json_response({"id": job["id"]})


@routes.post("/manny/api/ai/prompt")
async def manny_ai_prompt(request):
    b = await request.json()
    private = bool(b.get("private"))
    kind = "video" if b.get("kind") == "video" else "image"
    idea = (b.get("idea") or "").strip()
    if not idea:
        raise web.HTTPBadRequest(text="no idea")
    img = (b.get("image") or "").replace("\\", "/")
    if img.startswith(PRIV_INPUT + "/"):
        _need_private(request)
    job = _new_job(private)

    async def run():
        job["stage"] = "Writing"
        names = []
        if img:
            names = [img.split("/", 1)[1]] if img.startswith((PRIV_INPUT + "/", "manny/")) else []
        rules = ("one shot, one main action, say how the camera moves and how fast things move;" if kind == "video"
                 else "describe subject, setting, light, lens and style;")
        imgline = "The first image is the starting frame: keep its people and setting.\n" if names else ""
        text = await _llm(job, PROMPT_HELP.format(kind=kind, rules=rules, img=imgline, idea=idea.replace('"', "'")[:1500]), names, private, 300)
        return {"prompt": text.strip().strip('"')}
    asyncio.ensure_future(_ai_job(job, run))
    return web.json_response({"id": job["id"]})


@routes.get("/manny/api/job/{jid}")
async def manny_job(request):
    return await manny_finish_status(request)


# ---------------------------------------------------------------- film editing: trim, crossfades, titles, captions, music
def _font(size, bold=True):
    from PIL import ImageFont
    names = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
    for n in names:
        for p in (n, os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", n)):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wrap(draw, text, font, maxw):
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for w in para.split():
            t = (cur + " " + w).strip()
            if draw.textlength(t, font=font) <= maxw or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _card_png(path, W, H, title, sub=""):
    """A black title card."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(im)
    f1, f2 = _font(max(24, H // 11)), _font(max(16, H // 30), False)
    l1 = _wrap(d, title.upper(), f1, W * 0.84)
    l2 = _wrap(d, sub, f2, W * 0.8) if sub else []
    h1, h2 = f1.size * 1.15, f2.size * 1.4
    y = (H - (len(l1) * h1 + (len(l2) * h2 + h2 * 0.6 if l2 else 0))) / 2
    for ln in l1:
        d.text(((W - d.textlength(ln, font=f1)) / 2, y), ln, font=f1, fill=(240, 238, 245))
        y += h1
    y += h2 * 0.6
    for ln in l2:
        d.text(((W - d.textlength(ln, font=f2)) / 2, y), ln, font=f2, fill=(170, 168, 180))
        y += h2
    im.save(path)


def _caption_png(path, W, H, text):
    """A transparent caption overlay at the bottom of the frame."""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = _font(max(18, H // 22))
    lines = _wrap(d, text, f, W * 0.86)
    lh = f.size * 1.25
    y = H - H * 0.08 - len(lines) * lh
    for ln in lines:
        x = (W - d.textlength(ln, font=f)) / 2
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (2, 2)):
            d.text((x + dx, y + dy), ln, font=f, fill=(0, 0, 0, 200))
        d.text((x, y), ln, font=f, fill=(255, 255, 255, 255))
        y += lh
    im.save(path)


async def _ff(cmd):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _o, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg: " + err.decode("utf-8", "ignore")[-700:])


async def _segment(src, dst, W, H, fps, start=0.0, dur=None, still=False, caption=None, fade_in=0.0, fade_out=0.0, zoom=False):
    """One normalised piece of a film: size, fps, stereo 48k audio (silence if none), optional caption."""
    ff = _ffmpeg()
    cmd = [ff, "-y"]
    has_a = False
    if still:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", src]
    else:
        cmd += ["-ss", f"{start:.3f}"] + (["-t", f"{dur:.3f}"] if dur else []) + ["-i", src]
        try:
            has_a = (await asyncio.to_thread(_probe, src))[2]
        except Exception:
            has_a = False
    n = 1
    if caption:
        cmd += ["-loop", "1", "-i", caption]
        n += 1
    length = dur
    if length is None:
        length = max(0.1, (await asyncio.to_thread(_probe, src))[3] - start)
    if not has_a:
        cmd += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    v = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={fps}"
    if zoom and still:
        frames = int(length * fps) + 1
        v = (f"[0:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
             f"zoompan=z='min(1.0+0.12*on/{frames},1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps},setsar=1")
    if fade_in:
        v += f",fade=t=in:st=0:d={fade_in:.2f}"
    if fade_out:
        v += f",fade=t=out:st={max(0, length - fade_out):.2f}:d={fade_out:.2f}"
    v += ",format=yuv420p[vb]"
    chains = [v]
    vout = "[vb]"
    if caption:
        chains.append(f"[1:v]scale={W}:{H}[cap];[vb][cap]overlay=0:0:shortest=1,format=yuv420p[vc]")
        vout = "[vc]"
    a_idx = 0 if has_a else n
    a = f"[{a_idx}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{length:.3f}"
    if fade_in:
        a += f",afade=t=in:st=0:d={fade_in:.2f}"
    if fade_out:
        a += f",afade=t=out:st={max(0, length - fade_out):.2f}:d={fade_out:.2f}"
    chains.append(a + "[ab]")
    cmd += ["-filter_complex", ";".join(chains), "-map", vout, "-map", "[ab]", "-t", f"{length:.3f}",
            "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-c:a", "aac", "-b:a", "192k", dst]
    await _ff(cmd)
    return length


async def _xfade_join(parts, out, fade, music=None, music_vol=0.35, keep_audio=True):
    """Joins normalised parts. fade > 0 crossfades picture and sound between parts."""
    ff = _ffmpeg()
    lens = [p[1] for p in parts]
    cmd = [ff, "-y"]
    for p, _l in parts:
        cmd += ["-i", p]
    chains = []
    if fade > 0 and len(parts) > 1:
        f = min(fade, min(lens) / 2.5)
        prev_v, prev_a, t = "[0:v]", "[0:a]", lens[0]
        for k in range(1, len(parts)):
            off = t - f
            chains.append(f"{prev_v}[{k}:v]xfade=transition=fade:duration={f:.3f}:offset={off:.3f}[v{k}]")
            chains.append(f"{prev_a}[{k}:a]acrossfade=d={f:.3f}[a{k}]")
            prev_v, prev_a = f"[v{k}]", f"[a{k}]"
            t = off + lens[k]
        total = t
    else:
        chains.append("".join(f"[{k}:v][{k}:a]" for k in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=1[vcat][acat]")
        prev_v, prev_a, total = "[vcat]", "[acat]", sum(lens)
    if music:
        mi = len(parts)
        cmd += ["-i", music]
        fo = max(0, total - 2.0)
        if keep_audio:
            chains.append(f"[{mi}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},volume={music_vol},afade=t=out:st={fo:.2f}:d=2[mus]")
            chains.append(f"{prev_a}[mus]amix=inputs=2:duration=first:normalize=0[am]")
        else:
            chains.append(f"[{mi}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},afade=t=out:st={fo:.2f}:d=2[am]")
        prev_a = "[am]"
    cmd += ["-filter_complex", ";".join(chains), "-map", prev_v, "-map", prev_a, "-c:v", "libx264", "-crf", "17",
            "-preset", "medium", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    await _ff(cmd)
    return total


def _input_or_none(value):
    return _input_path(value) if value else None


@routes.post("/manny/api/edit")
async def manny_edit(request):
    """Makes a film from shots with trims, crossfades, an opening title, an end card, captions and a music bed."""
    b = await request.json()
    private = bool(b.get("private"))
    if private:
        _need_private(request)
    shots = b.get("shots") or []
    if not shots:
        raise web.HTTPBadRequest(text="no shots")
    clips = [_out_path(s["filename"], s.get("subfolder", ""), private) for s in shots]
    if b.get("music"):
        _input_path(b["music"])
    job = _new_job(private)

    async def run():
        import tempfile
        tmp = tempfile.mkdtemp(prefix="studio_edit_")
        try:
            W, H = (await asyncio.to_thread(_probe, clips[0]))[:2]
            W, H = W // 2 * 2, H // 2 * 2
            fps = 24
            parts = []
            fade = float(b.get("crossfade") or 0)
            t = b.get("title") or {}
            if (t.get("text") or "").strip():
                job["stage"] = "Title card"
                png = os.path.join(tmp, "title.png")
                await asyncio.to_thread(_card_png, png, W, H, t["text"].strip(), (t.get("sub") or "").strip())
                p = os.path.join(tmp, "p_title.mp4")
                L = await _segment(png, p, W, H, fps, dur=float(t.get("secs") or 3), still=True, fade_in=0.6, fade_out=0 if fade else 0.6)
                parts.append((p, L))
            for k, (s, full) in enumerate(zip(shots, clips)):
                job["stage"] = f"Shot {k + 1} of {len(shots)}"
                dur_all = (await asyncio.to_thread(_probe, full))[3]
                a = max(0.0, float(s.get("in") or 0))
                z = float(s.get("out") or 0) or dur_all
                z = min(z, dur_all)
                if z - a < 0.3:
                    a, z = 0.0, dur_all
                cap = None
                if (s.get("caption") or "").strip():
                    cap = os.path.join(tmp, f"cap{k}.png")
                    await asyncio.to_thread(_caption_png, cap, W, H, s["caption"].strip())
                p = os.path.join(tmp, f"p{k:03d}.mp4")
                L = await _segment(full, p, W, H, fps, start=a, dur=z - a, caption=cap)
                parts.append((p, L))
            e = b.get("end") or {}
            if (e.get("text") or "").strip():
                job["stage"] = "End card"
                png = os.path.join(tmp, "end.png")
                await asyncio.to_thread(_card_png, png, W, H, e["text"].strip(), (e.get("sub") or "").strip())
                p = os.path.join(tmp, "p_end.mp4")
                L = await _segment(png, p, W, H, fps, dur=float(e.get("secs") or 3), still=True, fade_in=0 if fade else 0.6, fade_out=1.0)
                parts.append((p, L))
            job["stage"] = "Joining"
            name = "".join(ch for ch in (b.get("name") or "Film") if ch.isalnum() or ch in " -_").strip() or "Film"
            out_dir = os.path.join(_root(private), "Videos", "Films")
            os.makedirs(out_dir, exist_ok=True)
            out = os.path.join(out_dir, f"{name} {time.strftime('%Y-%m-%d %H%M%S')}.mp4")
            await _xfade_join(parts, out, fade, _input_or_none(b.get("music")), float(b.get("music_vol") or 0.35), b.get("keep_audio", True))
            try:
                _start_proxy(out, private)
            except Exception:
                pass
            return {"filename": os.path.basename(out), "subfolder": "Videos/Films", "private": private}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    asyncio.ensure_future(_ai_job(job, run))
    return web.json_response({"id": job["id"]})


# ---------------------------------------------------------------- reel: pictures and clips cut to the beat of a song
def _beats(path, max_secs=180):
    """Beat times (seconds) of a song: onset strength + tempo by autocorrelation + best phase."""
    import av
    import numpy as np
    sr = 22050
    chunks = []
    with av.open(path) as c:
        rs = av.AudioResampler(format="flt", layout="mono", rate=sr)
        for fr in c.decode(audio=0):
            for r in rs.resample(fr):
                chunks.append(r.to_ndarray().reshape(-1))
            if sum(len(x) for x in chunks) > sr * max_secs:
                break
    y = np.concatenate(chunks) if chunks else np.zeros(sr)
    dur = len(y) / sr
    hop, n = 512, 2048
    if len(y) < n * 2:
        return [], dur, 120.0
    frames = np.lib.stride_tricks.sliding_window_view(y, n)[::hop] * np.hanning(n)
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 10)
    flux = np.maximum(0, np.diff(mag, axis=0)).sum(axis=1)
    flux = (flux - flux.mean()) / (flux.std() + 1e-9)
    fps = sr / hop
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    lag = lo + int(np.argmax(ac[lo:hi] * np.exp(-0.5 * (np.log2((np.arange(lo, hi) / fps) / 0.5)) ** 2)))
    # refine to a fractional period: the comb that lines up best with the onsets over the whole song
    env = np.convolve(np.maximum(flux, 0), np.hanning(5), "same")
    best = (-1e9, float(lag), 0.0)
    for P in np.linspace(lag - 1.5, lag + 1.5, 61):
        if P <= 2:
            continue
        for ph in np.arange(0, P, 0.5):
            idx = np.round(np.arange(ph, len(env) - 1, P)).astype(int)
            sc = env[idx].mean()
            if sc > best[0]:
                best = (sc, P, ph)
    _sc, P, ph = best
    period = P / fps
    beats = list(np.arange(ph / fps + (n + hop / 2) / sr, dur, period))  # onset shows when it enters the window
    return [float(t) for t in beats], dur, 60.0 / period


@routes.post("/manny/api/reel")
async def manny_reel(request):
    b = await request.json()
    private = bool(b.get("private"))
    if private:
        _need_private(request)
    items = [(_out_path(i["filename"], i.get("subfolder", ""), private), i.get("kind") or MEDIA_EXT.get(os.path.splitext(i["filename"])[1].lower())) for i in b.get("items", [])]
    if not items:
        raise web.HTTPBadRequest(text="no pictures or clips")
    song = _input_path(b.get("song") or "")
    job = _new_job(private)

    async def run():
        import tempfile
        tmp = tempfile.mkdtemp(prefix="studio_reel_")
        try:
            job["stage"] = "Finding the beat"
            beats, sdur, bpm = await asyncio.to_thread(_beats, song)
            length = min(float(b.get("length") or 30), sdur)
            W, H = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}.get(b.get("aspect") or "9:16", (1080, 1920))
            fps = 30
            per = max(1, int(round((float(b.get("cut") or 0) or 1.2) / (60.0 / bpm))))
            cuts = [t for t in beats[::per] if t < length] or [0.0]
            if cuts[0] > 0.05:
                cuts = [0.0] + cuts
            cuts.append(length)
            parts, used = [], {}
            for k in range(len(cuts) - 1):
                d = cuts[k + 1] - cuts[k]
                if d < 0.2:
                    continue
                job["stage"] = f"Cut {k + 1} of {len(cuts) - 1}"
                full, kind = items[k % len(items)]
                p = os.path.join(tmp, f"r{k:03d}.mp4")
                if kind == "video":
                    vdur = (await asyncio.to_thread(_probe, full))[3]
                    st = used.get(full, 0.0)
                    if st + d > vdur:
                        st = 0.0
                    used[full] = st + d
                    await _segment(full, p, W, H, fps, start=st, dur=min(d, vdur))
                else:
                    await _segment(full, p, W, H, fps, dur=d, still=True, zoom=True)
                parts.append((p, d))
            job["stage"] = "Adding the song"
            lst = os.path.join(tmp, "list.txt")
            with open(lst, "w", encoding="utf-8") as f:
                for p, _d in parts:
                    f.write("file '" + p.replace("\\", "/").replace("'", "'\\''") + "'\n")
            name = "".join(ch for ch in (b.get("name") or "Reel") if ch.isalnum() or ch in " -_").strip() or "Reel"
            out_dir = os.path.join(_root(private), "Videos", "Reels")
            os.makedirs(out_dir, exist_ok=True)
            out = os.path.join(out_dir, f"{name} {time.strftime('%Y-%m-%d %H%M%S')}.mp4")
            fo = max(0, length - 1.5)
            await _ff([_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", song, "-map", "0:v", "-map", "1:a",
                       "-af", f"atrim=0:{length:.3f},afade=t=out:st={fo:.2f}:d=1.5", "-t", f"{length:.3f}",
                       "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
            try:
                _start_proxy(out, private)
            except Exception:
                pass
            return {"filename": os.path.basename(out), "subfolder": "Videos/Reels", "private": private, "bpm": round(bpm), "cuts": len(parts)}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    asyncio.ensure_future(_ai_job(job, run))
    return web.json_response({"id": job["id"]})


# ---------------------------------------------------------------- LoRAs: Civitai browser (your own key) and applying them to Image / Video
CIVITAI = "https://civitai.com/api/v1"
_lora_lock = threading.Lock()


def _civ_file():
    return os.path.join(_data_dir(), "civitai.json")


def _civ_key():
    try:
        with open(_civ_file(), encoding="utf-8") as f:
            return (json.load(f) or {}).get("key") or ""
    except Exception:
        return ""


def _lora_meta_file():
    return os.path.join(_data_dir(), "loras.json")


def _lora_meta():
    try:
        with open(_lora_meta_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_lora_meta(m):
    with _lora_lock:
        tmp = _lora_meta_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=1, ensure_ascii=False)
        os.replace(tmp, _lora_meta_file())


def _strip_html(s, n=1200):
    import re as _r
    import html as _h
    t = _r.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s or "", flags=_r.I)
    t = _h.unescape(_r.sub(r"<[^>]+>", "", t))
    t = _r.sub(r"\n{3,}", "\n\n", t).strip()
    return t[:n] + ("…" if len(t) > n else "")


def _civ_img(u, w=450):
    """Civitai image URL at a given width."""
    import re as _r
    if not u:
        return ""
    if "/width=" in u or "/original=" in u:
        u = _r.sub(r"/(width=\d+|original=true)[^/]*/", f"/width={w}/", u)
    return u


SAFE_LEVEL = 2   # Civitai nsfwLevel: 1 PG, 2 PG-13, 4 R, 8 X, 16 XXX


def _civ_item(m, mature):
    vers = []
    for v in (m.get("modelVersions") or [])[:6]:
        imgs = [i for i in (v.get("images") or []) if mature or (i.get("nsfwLevel") or 1) <= SAFE_LEVEL]
        files = [f for f in (v.get("files") or []) if str(f.get("name", "")).lower().endswith(".safetensors")]
        vers.append({"id": v.get("id"), "name": v.get("name"), "base": v.get("baseModel"), "trained": v.get("trainedWords") or [],
                     "date": (v.get("publishedAt") or v.get("createdAt") or "")[:10],
                     "files": [{"name": f.get("name"), "sizeKB": f.get("sizeKB"), "primary": bool(f.get("primary")),
                                "sha256": ((f.get("hashes") or {}).get("SHA256") or "").lower()} for f in files],
                     "images": [{"url": _civ_img(i.get("url")), "type": i.get("type") or "image", "w": i.get("width"), "h": i.get("height")} for i in imgs[:8]]})
    return {"id": m.get("id"), "name": m.get("name"), "creator": (m.get("creator") or {}).get("username"),
            "nsfw": bool(m.get("nsfw")), "desc": _strip_html(m.get("description")),
            "downloads": (m.get("stats") or {}).get("downloadCount"), "likes": (m.get("stats") or {}).get("thumbsUpCount"),
            "versions": vers}


async def _civ_get(path, params):
    key = _civ_key()
    if not key:
        raise web.HTTPBadRequest(text="Add your Civitai key first")
    try:
        async with ClientSession(headers={"Authorization": f"Bearer {key}", "User-Agent": "Studio/1.0"}) as s:
            async with s.get(CIVITAI + path, params=params, timeout=30) as r:
                if r.status == 401:
                    raise web.HTTPBadRequest(text="Civitai didn't accept the key")
                if r.status != 200:
                    raise web.HTTPBadRequest(text=f"Civitai answered {r.status}. Try again in a minute.")
                return await r.json()
    except web.HTTPException:
        raise
    except Exception as e:
        raise web.HTTPBadRequest(text=f"Couldn't reach Civitai ({type(e).__name__}). Check the PC's internet connection.")


@routes.get("/manny/api/civitai/status")
async def civ_status(request):
    return web.json_response({"has_key": bool(_civ_key())})


@routes.post("/manny/api/civitai/key")
async def civ_key(request):
    """Stores your own Civitai key on this PC. It is never sent back to any browser."""
    b = await request.json()
    key = str(b.get("key") or "").strip()
    if key and (len(key) < 20 or len(key) > 200 or not all(c.isalnum() or c in "-_" for c in key)):
        raise web.HTTPBadRequest(text="That doesn't look like a Civitai key")
    with open(_civ_file(), "w", encoding="utf-8") as f:
        json.dump({"key": key} if key else {}, f)
    return web.json_response({"has_key": bool(key)})


@routes.get("/manny/api/civitai/search")
async def civ_search(request):
    q = request.query
    mature = auth.unlocked(request) and _flag(q.get("private"))
    ctype = q.get("type") or "LORA"
    if ctype not in CIV_TYPES:
        ctype = "LORA"
    params = {"types": ctype, "limit": "24", "sort": q.get("sort") or "Most Downloaded", "nsfw": "true" if mature else "false"}
    if q.get("q"):
        params["query"] = q["q"][:100]
    plist = list(params.items())
    for bm in [x for x in (q.get("base") or "").split("|") if x][:12]:
        plist.append(("baseModels", bm))
    params = plist
    if q.get("cursor"):
        params.append(("cursor", q["cursor"]))
    if q.get("period") in ("Day", "Week", "Month", "Year"):
        params.append(("period", q["period"]))
    data = await _civ_get("/models", params)
    items = [dict(_civ_item(m, mature), type=m.get("type")) for m in data.get("items", []) if mature or not m.get("nsfw")]
    return web.json_response({"items": items, "next": (data.get("metadata") or {}).get("nextCursor")})


@routes.get("/manny/api/civitai/img")
async def civ_img(request):
    """Civitai preview pictures, fetched by this PC and cached (only image.civitai.com)."""
    from urllib.parse import urlparse
    u = request.query.get("u", "")
    p = urlparse(u)
    if p.scheme != "https" or p.hostname != "image.civitai.com":
        raise web.HTTPBadRequest(text="bad image")
    d = os.path.join(_data_dir(), "cache", "civitai")
    os.makedirs(d, exist_ok=True)
    import hashlib as _h
    dest = os.path.join(d, _h.sha1(u.encode()).hexdigest()[:24])
    if not os.path.isfile(dest):
        async with ClientSession(headers={"User-Agent": "Studio/1.0"}) as s:
            async with s.get(u, timeout=30) as r:
                if r.status != 200:
                    raise web.HTTPNotFound()
                ct = r.headers.get("Content-Type", "")
                if not ct.startswith(("image/", "video/")):
                    raise web.HTTPBadRequest(text="not media")
                data = await r.read()
        with open(dest + ".tmp", "wb") as f:
            f.write(data)
        with open(dest + ".type", "w") as f:
            f.write(ct)
        os.replace(dest + ".tmp", dest)
    try:
        ct = open(dest + ".type").read().strip()
    except Exception:
        ct = "image/jpeg"
    return web.FileResponse(dest, headers={"Content-Type": ct, "Cache-Control": "private, max-age=2592000"})


def _base_folder(base):
    b = "".join(ch for ch in (base or "Other") if ch.isalnum() or ch in " .-_").strip() or "Other"
    return os.path.join("Civitai", b)


_CIV_LOCK = asyncio.Lock()   # Civitai downloads run one at a time


async def _civ_fetch(job, url, part, total, key):
    """Download into part, resuming after dropped connections (Range requests)."""
    tries = 0
    while True:
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        if total and have >= total:
            break
        hdrs = {"Authorization": f"Bearer {key}", "User-Agent": "Studio/1.0"}
        if have:
            hdrs["Range"] = f"bytes={have}-"
        try:
            async with ClientSession(headers=hdrs) as s:
                async with s.get(url, timeout=None) as r:
                    if r.status not in (200, 206):
                        if r.status == 403:
                            raise RuntimeError("Civitai won't let you download this one yet (it's paid or early access)")
                        raise RuntimeError(f"Civitai answered {r.status}" + (" (check your key)" if r.status == 401 else ""))
                    if r.status == 200:      # no resume offered: start over
                        have = 0
                    total = (have + int(r.headers.get("Content-Length") or 0)) or total
                    got = have
                    with open(part, "ab" if have else "wb") as out:
                        async for chunk in r.content.iter_chunked(1 << 20):
                            if job.get("cancel"):
                                raise RuntimeError("Stopped")
                            out.write(chunk)
                            got += len(chunk)
                            job["stage"] = f"Downloading {got / 1e6:.0f} of {total / 1e6:.0f} MB" if total else f"Downloading {got / 1e6:.0f} MB"
                            job["progress"] = got / total if total else None
            break
        except RuntimeError:
            raise
        except Exception as e:      # Civitai dropped the connection: pick up where it stopped
            tries += 1
            if tries > 30:
                raise RuntimeError(f"Download keeps breaking ({e}); try again later")
            job["stage"] = f"Connection dropped, resuming ({tries})"
            await asyncio.sleep(min(60, 5 * tries))


async def _civ_download(job, b, private):
    key = _civ_key()
    vid = int(b["version"])
    info = await _civ_get(f"/model-versions/{vid}", {})
    files = [f for f in info.get("files", []) if str(f.get("name", "")).lower().endswith(".safetensors")]
    want = b.get("file")
    f = next((x for x in files if x.get("name") == want), None) or next((x for x in files if x.get("primary")), None) or (files[0] if files else None)
    if not f:
        raise RuntimeError("This LoRA has no .safetensors file (other formats are refused for safety)")
    name = "".join(ch for ch in f["name"] if ch.isalnum() or ch in " .-_()").strip()
    ctype = (info.get("model") or {}).get("type") or b.get("type") or "LORA"
    folder = _civ_folder(ctype, info.get("baseModel"))
    rel = os.path.join(_base_folder(info.get("baseModel")), name)
    roots = _folder_roots(folder)
    if not roots:
        raise RuntimeError(f"ComfyUI has no {folder} folder")
    root = roots[0]
    dest = os.path.join(root, rel)
    if os.path.isfile(dest):
        job["stage"] = "Already installed"
    else:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        import hashlib as _h
        url = f["downloadUrl"] if f.get("downloadUrl", "").startswith("https://civitai.com/") else f"https://civitai.com/api/download/models/{vid}"
        part = dest + ".part"
        total = int((f.get("sizeKB") or 0) * 1024)
        if _CIV_LOCK.locked():
            job["stage"] = "Waiting for the other download to finish"
        await _CIV_LOCK.acquire()
        try:
            await _civ_fetch(job, url, part, total, key)
        finally:
            _CIV_LOCK.release()
        job["stage"] = "Checking the file"

        def _sha():
            h = _h.sha256()
            with open(part, "rb") as fh:
                for blk in iter(lambda: fh.read(1 << 22), b""):
                    h.update(blk)
            return h.hexdigest()
        digest = await asyncio.to_thread(_sha)
        want_sha = ((f.get("hashes") or {}).get("SHA256") or "").lower()
        if want_sha and digest != want_sha:
            os.remove(part)
            raise RuntimeError("The download was damaged (checksum mismatch). Try again.")
        os.replace(part, dest)
    imgs = info.get("images") or []
    safe = [i for i in imgs if (i.get("nsfwLevel") or 1) <= SAFE_LEVEL]
    meta = _lora_meta()
    meta[_meta_key(folder, rel)] = {"folder": folder, "type": ctype, **{"model": info.get("modelId"), "version": vid, "name": (info.get("model") or {}).get("name") or name,
                                    "versionName": info.get("name"), "base": info.get("baseModel"), "trained": info.get("trainedWords") or [],
                                    "preview": _civ_img((imgs[0] if private and imgs else (safe[0] if safe else {})).get("url", "")),
                                    "private": private, "at": time.time()}}
    _save_lora_meta(meta)
    _forget_list(folder)   # let ComfyUI see the new file without a restart
    return {"file": rel.replace("\\", "/"), "folder": folder, "type": ctype}


@routes.get("/manny/api/civitai/updates")
async def civ_updates(request):
    """For every model downloaded through Studio: is there a newer version on Civitai?"""
    private = auth.unlocked(request) and _flag(request.query.get("private"))
    meta = _lora_meta()
    by_model = {}
    for key, m in meta.items():
        if m.get("model") and (private or not m.get("private")):
            by_model.setdefault(m["model"], []).append((key, m))
    out = []
    for mid, rows in list(by_model.items())[:60]:
        try:
            info = await _civ_get(f"/models/{mid}", {})
        except Exception:
            continue
        vers = info.get("modelVersions") or []
        if not vers:
            continue
        latest = vers[0]
        have = {r[1].get("version") for r in rows}
        if latest.get("id") not in have:
            k, m = rows[0]
            paid = None
            try:
                vi = await _civ_get(f"/model-versions/{latest.get('id')}", {})
                pa = vi.get("paidAccess") or {}
                if pa.get("permanent"):
                    paid = "paid"
                elif pa.get("endsAt") or vi.get("earlyAccessEndsAt"):
                    paid = (pa.get("endsAt") or vi.get("earlyAccessEndsAt") or "")[:10]
            except Exception:
                pass
            out.append({"key": k, "model": mid, "name": m.get("name"), "installed": m.get("versionName"), "type": info.get("type"), "paid": paid,
                        "latest": {"id": latest.get("id"), "name": latest.get("name"), "base": latest.get("baseModel"),
                                   "date": (latest.get("publishedAt") or latest.get("createdAt") or "")[:10]}})
    return web.json_response(out)


def _civ_pending(update=None, private=False):
    """Downloads not finished yet, kept on disk so they carry on after a restart. Incognito ones are kept in the private space."""
    p = os.path.join(_priv_root(), ".civitai_pending.json") if private else os.path.join(_data_dir(), "civitai_pending.json")
    try:
        with open(p, encoding="utf-8") as fh:
            items = json.load(fh)
    except Exception:
        items = []
    if update is not None:
        items = update(items)
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(items, fh)
        os.replace(p + ".tmp", p)
    return items


def _civ_start(b, private):
    job = _new_job(private, kind="civitai", version=b.get("version"))
    key = str(b.get("version"))
    drop = lambda xs: [x for x in xs if str(x.get("version")) != key]
    _civ_pending(lambda xs: drop(xs) + [{k: b.get(k) for k in ("version", "file", "type")}], private)

    async def run():
        try:
            res = await _civ_download(job, b, private)
        except Exception as e:
            if "keeps breaking" not in str(e):   # only connection trouble is worth retrying after a restart
                _civ_pending(drop, private)
            raise
        _civ_pending(drop, private)
        return res
    asyncio.ensure_future(_ai_job(job, run))
    return job


_civ_resumed = {"done": False}


def _civ_resume_once():
    if not _civ_resumed["done"]:
        _civ_resumed["done"] = True
        try:
            _civ_resume_all()
        except Exception as e:
            print("[Studio] civitai resume:", e)


def _civ_resume_all():
    for private in (False, True):
        for b in _civ_pending(private=private):
            if not any(j.get("kind") == "civitai" and str(j.get("version")) == str(b.get("version")) and j.get("status") == "running" for j in _jobs.values()):
                _civ_start(b, private)


@routes.post("/manny/api/civitai/download")
async def civ_download(request):
    b = await request.json()
    private = bool(b.get("private"))
    if private:
        _need_private(request)
    return web.json_response({"id": _civ_start(b, private)["id"]})


@routes.get("/manny/api/civitai/downloads")
async def civ_downloads(request):
    out = [{k: j.get(k) for k in ("id", "version", "status", "stage", "progress", "error")} for j in _jobs.values()
           if j.get("kind") == "civitai" and not j.get("private")]
    return web.json_response({"jobs": out, "pending": _civ_pending()})


@routes.post("/manny/api/civitai/resume")
async def civ_resume(request):
    _civ_resume_all()
    return web.json_response({"ok": True, "pending": _civ_pending()})


@routes.get("/manny/api/loras")
async def manny_loras(request):
    private = _flag(request.query.get("private"))
    if private:
        _need_private(request)
    meta = _lora_meta()
    try:
        folder_paths.filename_list_cache.pop("loras", None)
    except Exception:
        pass
    out = []
    for name in folder_paths.get_filename_list("loras") or []:
        key = name.replace("\\", "/")
        m = meta.get(key) or {}
        if m.get("private") and not private:
            continue
        out.append({"file": name, "name": m.get("name") or os.path.splitext(os.path.basename(key))[0], "base": m.get("base") or "",
                    "trained": m.get("trained") or [], "preview": m.get("preview") or "", "civitai": m.get("model"),
                    "version": m.get("versionName") or "", "private": bool(m.get("private"))})
    return web.json_response(out)


def _inject_loras(api, loras):
    """Adds LoRAs after each model loader (SeedVR2 upscalers excluded). Only the branch of the chosen model runs."""
    have = {x.replace("\\", "/"): x for x in (folder_paths.get_filename_list("loras") or [])}
    loras = [dict(l, name=have[str(l.get("name", "")).replace("\\", "/")]) for l in (loras or []) if str(l.get("name", "")).replace("\\", "/") in have][:4]
    if not loras:
        return api
    loaders = [nid for nid, n in api.items() if n.get("class_type") in ("UNETLoader", "CheckpointLoaderSimple", "UnetLoaderGGUF")
               and "seedvr" not in json.dumps(n.get("inputs", {})).lower()]
    for nid in loaders:
        prev = [nid, 0]
        for k, l in enumerate(loras):
            lid = f"lora_{nid.replace(':', '_')}_{k}"
            api[lid] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": prev, "lora_name": l["name"],
                        "strength_model": max(-2.0, min(2.0, float(l.get("strength", 0.8))))}, "_meta": {"title": "Studio LoRA"}}
            prev = [lid, 0]
        for oid, n in api.items():
            if oid.startswith(f"lora_{nid.replace(':', '_')}_"):
                continue
            for kk, v in (n.get("inputs") or {}).items():
                if isinstance(v, list) and len(v) == 2 and str(v[0]) == nid and v[1] == 0:
                    n["inputs"][kk] = prev
    return api


# ---------------------------------------------------------------- every Civitai model type, and managing model files
CIV_TYPES = {   # Civitai type -> ComfyUI models folder
    "LORA": "loras", "LoCon": "loras", "DoRA": "loras", "Checkpoint": "checkpoints", "TextualInversion": "embeddings",
    "VAE": "vae", "Controlnet": "controlnet", "Upscaler": "upscale_models", "Hypernetwork": "hypernetworks", "MotionModule": "animatediff_models",
}
SD_FAMILY = ("sd 1", "sd1", "sdxl", "pony", "illustrious", "noobai")
MODEL_FOLDERS = ["checkpoints", "diffusion_models", "loras", "text_encoders", "vae", "upscale_models", "latent_upscale_models",
                 "embeddings", "controlnet", "clip_vision", "audio_encoders", "model_patches", "hypernetworks", "animatediff_models"]


def _civ_folder(ctype, base):
    f = CIV_TYPES.get(ctype, "loras")
    if f == "checkpoints" and base and not any(k in base.lower() for k in SD_FAMILY):
        f = "diffusion_models"   # FLUX, Qwen, Wan, Z-Image... checkpoints are usually the model alone
    return f


def _meta_key(folder, rel):
    rel = rel.replace("\\", "/")
    return rel if folder == "loras" else f"{folder}:{rel}"


def _folder_roots(folder):
    try:
        return [r for r in folder_paths.get_folder_paths(folder) if r]
    except Exception:
        return []


def _forget_list(folder):
    try:
        folder_paths.filename_list_cache.pop(folder, None)
    except Exception:
        pass


_apps_text = {"at": 0, "text": ""}


def _apps_blob():
    if time.time() - _apps_text["at"] > 60:
        parts = []
        for rel in _list_apps():
            try:
                with open(_safe_app_path(rel), encoding="utf-8") as f:
                    parts.append((os.path.basename(rel)[:-9], f.read()))
            except Exception:
                pass
        _apps_text.update(at=time.time(), text=parts)
    return _apps_text["text"]


def _used_by(name):
    base = os.path.basename(name.replace("\\", "/"))
    return [n.replace("Manny Studio - ", "").replace("Studio - ", "") for n, t in _apps_blob() if base in t]


def _model_file(folder, name):
    if folder not in MODEL_FOLDERS:
        raise web.HTTPBadRequest(text="bad folder")
    for root in _folder_roots(folder):
        r = os.path.realpath(root)
        full = os.path.realpath(os.path.join(r, name))
        if full.startswith(r + os.sep) and os.path.isfile(full):
            return r, full
    raise web.HTTPNotFound(text="model not found")


def _trash_dir(root, folder):
    d = os.path.join(os.path.dirname(os.path.realpath(root)), "_studio_removed", folder)
    os.makedirs(d, exist_ok=True)
    return d


@routes.get("/manny/api/models")
async def models_list(request):
    """Every model file Studio can see, with size, Civitai info and the apps that use it."""
    want = request.query.get("folder")
    private = auth.unlocked(request) and _flag(request.query.get("private"))
    meta = _lora_meta()

    def scan():
        out = []
        for folder in ([want] if want else MODEL_FOLDERS):
            if folder not in MODEL_FOLDERS:
                continue
            seen = set()
            for root in _folder_roots(folder):
                if not os.path.isdir(root):
                    continue
                for dp, dirs, files in os.walk(root):
                    dirs[:] = [d for d in dirs if not d.startswith((".", "_studio_removed"))]
                    for fn in files:
                        if not fn.lower().endswith((".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft")):
                            continue
                        full = os.path.join(dp, fn)
                        rel = os.path.relpath(full, root).replace("\\", "/")
                        if rel in seen:
                            continue
                        seen.add(rel)
                        m = meta.get(_meta_key(folder, rel)) or {}
                        if m.get("private") and not private:
                            continue
                        try:
                            st = os.stat(full)
                        except OSError:
                            continue
                        out.append({"folder": folder, "name": rel, "size": st.st_size, "mtime": st.st_mtime, "used_by": _used_by(rel),
                                    "title": m.get("name") or "", "base": m.get("base") or "", "trained": m.get("trained") or [],
                                    "preview": m.get("preview") or "", "civitai": m.get("model"), "private": bool(m.get("private"))})
        return out
    items = await asyncio.to_thread(scan)
    try:
        du = shutil.disk_usage(_folder_roots("checkpoints")[0])
        disk = {"free": du.free, "total": du.total}
    except Exception:
        disk = None
    return web.json_response({"items": items, "disk": disk})


@routes.post("/manny/api/models/remove")
async def models_remove(request):
    """Moves a model to the Studio trash (models/_studio_removed). Nothing is deleted."""
    b = await request.json()
    root, full = _model_file(b.get("folder"), b.get("name", ""))
    dest = os.path.join(_trash_dir(root, b["folder"]), b["name"].replace("/", os.sep))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        base, ext = os.path.splitext(dest)
        dest = f"{base}_{int(time.time())}{ext}"
    await asyncio.to_thread(shutil.move, full, dest)
    _forget_list(b["folder"])
    return web.json_response({"ok": True})


def _trash_items():
    out = []
    for folder in MODEL_FOLDERS:
        for root in _folder_roots(folder):
            d = os.path.join(os.path.dirname(os.path.realpath(root)), "_studio_removed", folder)
            if not os.path.isdir(d):
                continue
            for dp, _dirs, files in os.walk(d):
                for fn in files:
                    full = os.path.join(dp, fn)
                    out.append({"folder": folder, "name": os.path.relpath(full, d).replace("\\", "/"), "size": os.path.getsize(full),
                                "_full": full, "_root": root})
    return out


@routes.get("/manny/api/models/removed")
async def models_removed(request):
    items = await asyncio.to_thread(_trash_items)
    return web.json_response([{k: v for k, v in i.items() if not k.startswith("_")} for i in items])


@routes.post("/manny/api/models/restore")
async def models_restore(request):
    b = await request.json()
    it = next((i for i in _trash_items() if i["folder"] == b.get("folder") and i["name"] == b.get("name")), None)
    if not it:
        raise web.HTTPNotFound()
    dest = os.path.join(it["_root"], it["name"].replace("/", os.sep))
    if os.path.exists(dest):
        raise web.HTTPConflict(text="A file with that name is back already")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    await asyncio.to_thread(shutil.move, it["_full"], dest)
    _forget_list(it["folder"])
    return web.json_response({"ok": True})


@routes.post("/manny/api/models/purge")
async def models_purge(request):
    """Deletes trashed models for good. Only from this PC or an unlocked browser."""
    if not (auth.is_local(request) or auth.unlocked(request)):
        raise web.HTTPForbidden(text="Deleting for good works on the PC itself, or with Incognito unlocked")
    b = await request.json()
    items = _trash_items()
    if not b.get("all"):
        items = [i for i in items if i["folder"] == b.get("folder") and i["name"] == b.get("name")]
    freed = 0
    for i in items:
        freed += i["size"]
        await asyncio.to_thread(os.remove, i["_full"])
    return web.json_response({"deleted": len(items), "freed": freed})


# which loader file each Studio model starts from, so a downloaded finetune ("style") of the same family
# can take its place. Only the branch of the chosen model runs, so replacing all matches is safe.
UNET_FAMILY = {"moss": r"^moss_image_sft", "qwen": r"^qwen_image_2\.1", "krea": r"krea2", "flux3": r"^flux3-image-klein",
               "zimage": r"^z_image_turbo", "anima": r"^anima-base", "h3": r"^minimax_h3_ref2va"}


def _inject_unet(api, family, name):
    """Uses a finetune (a Civitai checkpoint, MOSS-heal, NitroFusion2…) in place of the family's stock model."""
    import re as _re
    pat = UNET_FAMILY.get(family)
    have = {x.replace("\\", "/"): x for x in (folder_paths.get_filename_list("diffusion_models") or [])}
    name = have.get((name or "").replace("\\", "/"))   # Windows lists sub-folders with backslashes
    if not pat or not name:
        return api
    hit = False
    for n in api.values():
        if n.get("class_type") in ("UNETLoader", "UnetLoaderGGUF"):
            cur = str(n["inputs"].get("unet_name", ""))
            if _re.search(pat, os.path.basename(cur.replace("\\", "/")), _re.I):
                n["inputs"]["unet_name"] = name
                hit = True
    if hit:
        title = "".join(ch for ch in os.path.splitext(os.path.basename(name.replace("\\", "/")))[0] if ch.isalnum() or ch in " -_.")[:60]
        for n in api.values():
            if n.get("class_type") == "SaveImage" and isinstance(n["inputs"].get("filename_prefix"), str):
                n["inputs"]["filename_prefix"] = "Images/" + title
    return api


def _inject_checkpoint(api, name):
    """Uses one of your own SD/SDXL checkpoints in place of the Image app's SDXL model."""
    have = {x.replace("\\", "/"): x for x in (folder_paths.get_filename_list("checkpoints") or [])}
    real = have.get((name or "").replace("\\", "/"))   # Windows lists sub-folders with backslashes
    if not real:
        return api
    hit = False
    for n in api.values():
        if n.get("class_type") == "CheckpointLoaderSimple":
            n["inputs"]["ckpt_name"] = real
            hit = True
    if hit:
        title = "".join(ch for ch in os.path.splitext(os.path.basename(name.replace("\\", "/")))[0] if ch.isalnum() or ch in " -_.")[:60]
        for n in api.values():
            if n.get("class_type") == "SaveImage":
                n["inputs"]["filename_prefix"] = "Images/" + title
    return api


# ---------------------------------------------------------------- resource monitor: CPU, RAM, GPU load and VRAM
_gpu = {"util": None, "engine": None, "last_ask": 0, "running": False}


def _gpu_sampler():
    """Windows performance counters for GPU load (works for Intel Arc, NVIDIA and AMD). Runs only while someone looks."""
    ps = ("$s=(Get-Counter '\\GPU Engine(*)\\Utilization Percentage' -ErrorAction SilentlyContinue).CounterSamples;"
          "$g=@{};foreach($x in $s){if($x.InstanceName -match 'engtype_(\\w+)'){$k=$matches[1];$g[$k]=$g[$k]+$x.CookedValue}};"
          "($g.GetEnumerator()|Sort-Object Value -Descending|Select-Object -First 1|%{\"$($_.Key) $([math]::Round($_.Value,1))\"})")
    flags = getattr(subprocess_mod, "CREATE_NO_WINDOW", 0)
    while time.time() - _gpu["last_ask"] < 30:
        try:
            r = subprocess_mod.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=10, creationflags=flags)
            parts = (r.stdout or "").strip().split()
            if len(parts) == 2:
                _gpu["engine"], _gpu["util"] = parts[0], min(100.0, float(parts[1]))
        except Exception:
            _gpu["util"] = None
        time.sleep(1.5)
    _gpu["running"] = False


import subprocess as subprocess_mod  # noqa: E402


@routes.get("/manny/api/sysmon")
async def manny_sysmon(request):
    _civ_resume_once()
    out = {}
    try:
        import psutil
        out["cpu"] = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        out["ram"] = {"used": vm.total - vm.available, "total": vm.total}
    except Exception:
        pass
    try:
        import comfy.model_management as mm
        dev = mm.get_torch_device()
        total = mm.get_total_memory(dev)
        free = mm.get_free_memory(dev)
        out["vram"] = {"used": max(0, total - free), "total": total, "device": str(dev)}
    except Exception:
        pass
    if os.name == "nt":
        _gpu["last_ask"] = time.time()
        if not _gpu["running"]:
            _gpu["running"] = True
            threading.Thread(target=_gpu_sampler, name="studio-gpu", daemon=True).start()
        out["gpu"] = _gpu["util"]
        out["gpu_engine"] = _gpu["engine"]
    try:
        out["job"] = _guard.job_status()
        out["awake"] = _guard.STATE["awake"]
    except Exception:
        pass
    return web.json_response(out)


# ---------------------------------------------------------------- incognito housekeeping
_pending_private = set()


def _move_into(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        base, ext = os.path.splitext(dst)
        dst = f"{base}_{int(time.time())}{ext}"
    shutil.move(src, dst)


def _sweep_incoming(min_age=15):
    inc = os.path.join(folder_paths.get_output_directory(), INCOMING)
    if not os.path.isdir(inc):
        return
    now = time.time()
    for dirpath, _dirs, files in os.walk(inc):
        for fn in files:
            full = os.path.join(dirpath, fn)
            try:
                if now - os.path.getmtime(full) < min_age:
                    continue
                rel = os.path.relpath(full, inc)
                _move_into(full, os.path.join(_priv_root(), rel))
            except Exception as e:
                print("[Studio] could not move private file", fn, e)


def _forget_private_history():
    port = _port()
    for pid in list(_pending_private):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/history/{pid}", timeout=10) as r:
                h = json.loads(r.read() or b"{}")
            if pid not in h:
                continue
            st = (h[pid].get("status") or {})
            if not st.get("completed") and st.get("status_str") not in ("success", "error"):
                continue
            _sweep_incoming(min_age=0)
            req = urllib.request.Request(f"http://127.0.0.1:{port}/history", data=json.dumps({"delete": [pid]}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=10).read()
            _pending_private.discard(pid)
        except Exception:
            pass


def _migrate_once():
    """First start with incognito: back up, then move everything Studio made into the private space."""
    marker = os.path.join(_data_dir(), "incognito_migrated.json")
    if os.path.exists(marker):
        return
    out = folder_paths.get_output_directory()
    inp = folder_paths.get_input_directory()
    pics = os.path.dirname(os.path.abspath(out))
    backup = os.path.join(pics, "Comfyui-Backup " + time.strftime("%Y-%m-%d %H%M"))
    moved = []
    try:
        plan = [(os.path.join(out, d), os.path.join(_priv_root(), d), os.path.join(backup, d)) for d in ("Images", "Videos", "_trash")]
        plan.append((os.path.join(inp, "manny"), os.path.join(inp, PRIV_INPUT), os.path.join(backup, "input-manny")))
        # 1) full copy first (nothing is moved until every copy succeeded)
        for src, _dst, bak in plan:
            if os.path.isdir(src):
                shutil.copytree(src, bak, dirs_exist_ok=True)
        # 2) move into the private space
        for src, dst, _bak in plan:
            if not os.path.isdir(src):
                continue
            for dirpath, _dirs, files in os.walk(src):
                for fn in files:
                    full = os.path.join(dirpath, fn)
                    _move_into(full, os.path.join(dst, os.path.relpath(full, src)))
                    moved.append(fn)
            shutil.rmtree(src, ignore_errors=True)
        # 3) characters, storyboards and favorites follow their files
        chars = _load_chars()
        for c in chars:
            c["private"] = True
            for k in ("face", "voice"):
                if isinstance(c.get(k), str) and c[k].startswith("manny/"):
                    c[k] = PRIV_INPUT + c[k][len("manny"):]
        _save_chars(chars)
        boards = _load_boards()
        for b in boards:
            b["private"] = True
            for sh in b.get("shots", []):
                if isinstance(sh.get("image"), str) and sh["image"].startswith("manny/"):
                    sh["image"] = PRIV_INPUT + sh["image"][len("manny"):]
        _save_boards(boards)
        favs = _load_favs()
        with open(_favs_file(), "w", encoding="utf-8") as f:
            json.dump(sorted("P:" + k if not k.startswith("P:") else k for k in favs), f)
        with open(marker, "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "backup": backup, "files": len(moved)}, f)
        print(f"[Studio] moved {len(moved)} files into the private space; backup copy in {backup}")
    except Exception as e:
        print("[Studio] incognito move stopped, nothing deleted:", e)


def _housekeeping():
    _migrate_once()
    while True:
        try:
            _sweep_incoming()
            _forget_private_history()
        except Exception as e:
            print("[Studio] housekeeping:", e)
        time.sleep(10)


threading.Thread(target=_housekeeping, name="studio-incognito", daemon=True).start()


print(f"[Studio] {VERSION} ready at /manny")


# ---------------------------------------------------------------- 3D: sets, scenes and Blender renders
from . import previs as _previs  # noqa: E402
_previs.setup(globals())

# ---------------------------------------------------------------- Topaz upscalers (Gigapixel, Topaz Video)
from . import topaz as _topaz  # noqa: E402
_topaz.setup(globals())

# ---------------------------------------------------------------- render guard (keep awake, free memory, stall watch)
from . import guard as _guard  # noqa: E402
_guard.setup(globals())

# ---------------------------------------------------------------- Music (ACE-Step) and Voices (Chatterbox)
from . import sound as _sound  # noqa: E402
_sound.setup(globals())

# ---------------------------------------------------------------- render later (scheduled queue items)
from . import schedule as _schedule  # noqa: E402
_schedule.setup(globals())

# ---------------------------------------------------------------- Projects: idea -> script -> film
from . import projects as _projects  # noqa: E402
_projects.setup(globals())

# ---------------------------------------------------------------- Versions (git + GitHub)
from . import gitver as _gitver  # noqa: E402
_gitver.setup_routes(globals())

# ---------------------------------------------------------------- 3D -> storyboard -> final render pipeline
from . import pipeline as _pipeline  # noqa: E402
_pipeline.setup(globals())

# ---------------------------------------------------------------- poses (OpenPose ControlNet for checkpoints)
from . import poses as _poses  # noqa: E402
_poses.setup(globals())


try:   # carry on with Civitai downloads that a restart interrupted
    _lp = getattr(PromptServer.instance, "loop", None)
    if _lp is not None:
        _lp.call_soon_threadsafe(lambda: _lp.call_later(20, _civ_resume_once))
except Exception as _e:
    print("[Studio] civitai resume:", _e)
