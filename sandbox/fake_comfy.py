"""The stand-in ComfyUI: the HTTP and websocket API ComfyUI serves, its queue, its prompt validation, and a render loop that
answers each job with prepared result files instead of running models. Studio's node pack loads into it unchanged.

    python sandbox/fake_comfy.py --root sandbox/run --port 8188

Node definitions come from sandbox/object_info.json (captured from a real ComfyUI 0.45 on CPU), plus
sandbox/custom_nodes.json (the custom packs Studio's code builds jobs for) and whatever the loaded pack registers.
Inputs listed in sandbox/file_inputs.json (model and file pickers) are filled from the sandbox folders on each request,
as ComfyUI does. Every job is saved to the jobs folder (sandbox/jobs by default) as JSON."""
import argparse
import asyncio
import copy
import importlib.util
import json
import mimetypes
import os
import sys
import time
import traceback
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "comfy_stub"))
sys.path.insert(0, HERE)

from aiohttp import web  # noqa: E402

import folder_paths  # noqa: E402
import nodes  # noqa: E402
import results  # noqa: E402
import validate  # noqa: E402
from server import PromptServer  # noqa: E402

PACK = os.environ.get("STUDIO_PACK") or os.path.join(REPO, "ComfyUI-Studio")
STATE = {"object_info": {}, "file_inputs": {}, "jobs_dir": None, "render_seconds": 1.0, "pack_error": None}
MEDIA = {"image": (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"), "video": (".mp4", ".webm", ".mov", ".mkv", ".avi"),
         "audio": (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".opus")}


# ------------------------------------------------------------------ node definitions
def load_definitions():
    with open(os.path.join(HERE, "object_info.json"), encoding="utf-8") as f:
        oi = json.load(f)
    with open(os.path.join(HERE, "custom_nodes.json"), encoding="utf-8") as f:
        oi.update(json.load(f))
    with open(os.path.join(HERE, "file_inputs.json"), encoding="utf-8") as f:
        STATE["file_inputs"] = json.load(f)
    STATE["object_info"] = oi
    nodes.register_from_object_info(oi)


def node_info(name, cls, module):
    """object_info for a V1 node class a custom pack registers, as ComfyUI builds it."""
    def j(v):
        if isinstance(v, tuple):
            return [j(x) for x in v]
        if isinstance(v, list):
            return [j(x) for x in v]
        if isinstance(v, dict):
            return {k: j(x) for k, x in v.items()}
        if isinstance(v, str):
            return str(v)
        return v
    it = cls.INPUT_TYPES()
    inp = {sect: j(it[sect]) for sect in ("required", "optional") if sect in it}
    outs = [str(x) for x in getattr(cls, "RETURN_TYPES", ())]
    return {"input": inp, "input_order": {k: list(v) for k, v in inp.items()}, "output": outs,
            "output_is_list": list(getattr(cls, "OUTPUT_IS_LIST", [False] * len(outs))),
            "output_name": list(getattr(cls, "RETURN_NAMES", outs)), "name": name,
            "display_name": nodes.NODE_DISPLAY_NAME_MAPPINGS.get(name, name), "description": getattr(cls, "DESCRIPTION", ""),
            "python_module": "custom_nodes." + module, "category": getattr(cls, "CATEGORY", "sd"),
            "output_node": bool(getattr(cls, "OUTPUT_NODE", False))}


def _files(kind):
    root = folder_paths.get_input_directory()
    out = []
    for dirpath, _d, fs in os.walk(root):
        for f in fs:
            if f.lower().endswith(MEDIA[kind]):
                out.append(os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/"))
    return sorted(out)


def current_object_info():
    """The definitions with every model and file picker listing what is in the sandbox folders right now."""
    oi = copy.deepcopy(STATE["object_info"])
    lists = {}
    for cls, inputs in STATE["file_inputs"].items():
        spec = oi.get(cls)
        if not spec:
            continue
        for sect in ("required", "optional"):
            for name, v in (spec.get("input", {}).get(sect) or {}).items():
                if name not in inputs:
                    continue
                files = []
                for folder in inputs[name]:
                    if folder not in lists:
                        lists[folder] = _files(folder) if folder in MEDIA else folder_paths.get_filename_list(folder)
                    files += lists[folder]
                if isinstance(v[0], list):
                    v[0] = sorted(set(v[0]) | set(files), key=lambda x: (x not in files, x))
                elif v[0] == "COMBO" and len(v) > 1:
                    opts = v[1].get("options", [])
                    v[1]["options"] = opts + [f for f in files if f not in opts]
    return oi


# ------------------------------------------------------------------ the custom node pack
def load_pack(path):
    name = os.path.basename(path.rstrip("/"))
    spec = importlib.util.spec_from_file_location(name, os.path.join(path, "__init__.py"),
                                                  submodule_search_locations=[path])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    mappings = getattr(mod, "NODE_CLASS_MAPPINGS", {}) or {}
    nodes.NODE_DISPLAY_NAME_MAPPINGS.update(getattr(mod, "NODE_DISPLAY_NAME_MAPPINGS", {}) or {})
    for k, cls in mappings.items():
        nodes.NODE_CLASS_MAPPINGS[k] = cls
        STATE["object_info"][k] = node_info(k, cls, name)
    web_dir = getattr(mod, "WEB_DIRECTORY", None)
    return name, len(mappings), web_dir


# ------------------------------------------------------------------ the render loop
def _dirs():
    return {"input": folder_paths.get_input_directory(), "output": folder_paths.get_output_directory(),
            "temp": folder_paths.get_temp_directory()}


def _save_job(item, status, outputs, error=None):
    number, pid, prompt, extra = item[0], item[1], item[2], item[3]
    rec = {"number": number, "prompt_id": pid, "app": (extra or {}).get("studio_app"), "label": (extra or {}).get("studio_label"),
           "client_id": (extra or {}).get("client_id"), "status": status, "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "outputs": outputs, "error": error, "prompt": prompt,
           "extra_data": {k: v for k, v in (extra or {}).items() if k != "extra_pnginfo"}}
    os.makedirs(STATE["jobs_dir"], exist_ok=True)
    with open(os.path.join(STATE["jobs_dir"], f"{int(number):06d}-{pid}.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)


async def render_loop(ps):
    q = ps.prompt_queue
    while True:
        item, idx = q.take()
        if item is None:
            await asyncio.sleep(0.1)
            continue
        number, pid, prompt, extra, outs = item[0], item[1], item[2], item[3], item[4]
        sid = (extra or {}).get("client_id")
        msgs = []

        async def say(event, data, keep=False):
            data = dict(data, prompt_id=pid)
            if keep:
                msgs.append([event, dict(data, timestamp=int(time.time() * 1000))])
            await ps.send_json(event, data, sid)

        outputs, status, error = {}, "success", None
        try:
            await say("execution_start", {"timestamp": int(time.time() * 1000)}, keep=True)
            await say("execution_cached", {"nodes": [], "timestamp": int(time.time() * 1000)}, keep=True)
            steps = 4
            for k in range(1, steps + 1):
                if pid in ps.interrupted:
                    raise InterruptedError
                await asyncio.sleep(STATE["render_seconds"] / steps)
                await say("progress", {"value": k, "max": steps, "node": outs[0] if outs else None})
            for nid in outs:
                if pid in ps.interrupted:
                    raise InterruptedError
                await say("executing", {"node": nid, "display_node": nid})
                ui = await asyncio.to_thread(results.make_output, prompt, nid, _dirs())
                if ui:
                    outputs[nid] = ui
                    await say("executed", {"node": nid, "display_node": nid, "output": ui})
            await say("execution_success", {"timestamp": int(time.time() * 1000)}, keep=True)
        except InterruptedError:
            status = "error"
            error = "Interrupted"
            await say("execution_interrupted", {"node_id": None, "node_type": None, "executed": list(outputs),
                                                "timestamp": int(time.time() * 1000)}, keep=True)
        except Exception as e:
            status, error = "error", f"{type(e).__name__}: {e}"
            traceback.print_exc()
            await say("execution_error", {"node_id": None, "node_type": None, "exception_message": error,
                                          "exception_type": type(e).__name__, "traceback": [], "current_inputs": {},
                                          "current_outputs": {}, "timestamp": int(time.time() * 1000)}, keep=True)
        finally:
            ps.interrupted.discard(pid)
        q.task_done(idx, {"outputs": outputs, "meta": {k: {"node_id": k, "display_node": k} for k in outputs}},
                    {"status_str": status, "completed": status == "success", "messages": msgs})
        await ps.send_json("executing", {"node": None, "prompt_id": pid}, sid)
        await asyncio.to_thread(_save_job, item, status, outputs, error)


# ------------------------------------------------------------------ ComfyUI's routes
def add_comfy_routes(ps):
    r = web.RouteTableDef()

    @r.get("/ws")
    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        sid = request.rel_url.query.get("clientId") or uuid.uuid4().hex
        ps.sockets[sid] = ws
        try:
            await ws.send_str(json.dumps({"type": "status", "data": {"status": ps.get_queue_info(), "sid": sid}}))
            async for _msg in ws:
                pass
        finally:
            ps.sockets.pop(sid, None)
        return ws

    @r.get("/object_info")
    async def object_info(request):
        return web.json_response(current_object_info())

    @r.get("/object_info/{node_class}")
    async def object_info_one(request):
        oi = current_object_info()
        n = request.match_info["node_class"]
        return web.json_response({n: oi[n]} if n in oi else {})

    @r.get("/prompt")
    async def get_prompt(request):
        return web.json_response(ps.get_queue_info())

    @r.post("/prompt")
    async def post_prompt(request):
        data = await request.json()
        data = ps.trigger_on_prompt(data)
        number = data.get("number")
        if number is None:
            number = ps.number
            ps.number += 1
            if data.get("front"):
                number = -number
        prompt = data.get("prompt")
        if not isinstance(prompt, dict):
            return web.json_response({"error": {"type": "no_prompt", "message": "No prompt provided", "details": "No prompt provided",
                                                "extra_info": {}}, "node_errors": {}}, status=400)
        pid = data.get("prompt_id") or str(uuid.uuid4())
        ok, err, good, node_errors = validate.validate_prompt(
            prompt, current_object_info(), lambda _kind, name: os.path.isfile(folder_paths.get_annotated_filepath(name)))
        extra = dict(data.get("extra_data") or {})
        if "client_id" in data:
            extra["client_id"] = data["client_id"]
        if not ok:
            os.makedirs(STATE["jobs_dir"], exist_ok=True)
            with open(os.path.join(STATE["jobs_dir"], f"refused-{int(time.time() * 1000)}-{pid}.json"), "w", encoding="utf-8") as f:
                json.dump({"prompt_id": pid, "status": "refused", "error": err, "node_errors": node_errors,
                           "app": extra.get("studio_app"), "label": extra.get("studio_label"), "prompt": prompt}, f, indent=1)
            return web.json_response({"error": err, "node_errors": node_errors}, status=400)
        ps.prompt_queue.put((number, pid, prompt, extra, good, {}))
        return web.json_response({"prompt_id": pid, "number": number, "node_errors": node_errors})

    @r.get("/queue")
    async def get_queue(request):
        running, pending = ps.prompt_queue.get_current_queue_volatile()
        return web.json_response({"queue_running": [list(x[:5]) for x in running],
                                  "queue_pending": [list(x[:5]) for x in sorted(pending)]})

    @r.post("/queue")
    async def post_queue(request):
        data = await request.json()
        if data.get("clear"):
            ps.prompt_queue.wipe_queue()
        for pid in data.get("delete") or []:
            ps.prompt_queue.delete_queue_item(lambda a, p=pid: a[1] == p)
        return web.Response(status=200)

    @r.post("/interrupt")
    async def interrupt(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        pid = data.get("prompt_id")
        if pid:
            ps.prompt_queue.interrupt_if_running(pid)
        else:
            for item in list(ps.prompt_queue.currently_running.values()):
                ps.interrupted.add(item[1])
        return web.Response(status=200)

    @r.post("/free")
    async def free(request):
        return web.Response(status=200)

    @r.get("/history")
    async def history(request):
        mi = request.rel_url.query.get("max_items")
        off = request.rel_url.query.get("offset")
        return web.json_response(ps.prompt_queue.get_history(max_items=int(mi) if mi else None,
                                                             offset=int(off) if off else -1))

    @r.get("/history/{prompt_id}")
    async def history_one(request):
        return web.json_response(ps.prompt_queue.get_history(prompt_id=request.match_info["prompt_id"]))

    @r.post("/history")
    async def post_history(request):
        data = await request.json()
        if data.get("clear"):
            ps.prompt_queue.wipe_history()
        for pid in data.get("delete") or []:
            ps.prompt_queue.delete_history_item(pid)
        return web.Response(status=200)

    @r.get("/view")
    async def view(request):
        q = request.rel_url.query
        name = q.get("filename", "")
        kind = q.get("type", "output")
        base = folder_paths.get_directory_by_type(kind)
        if not base or not name:
            return web.Response(status=400)
        full = os.path.realpath(os.path.join(base, q.get("subfolder", ""), name))
        if os.path.commonpath([full, os.path.realpath(base)]) != os.path.realpath(base) or not os.path.isfile(full):
            return web.Response(status=404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        return web.FileResponse(full, headers={"Content-Type": ctype})

    async def _upload(request, mask=False):
        post = await request.post()
        f = post.get("image")
        if f is None or not getattr(f, "filename", None):
            return web.Response(status=400)
        kind = post.get("type", "input")
        base = folder_paths.get_directory_by_type(kind) or folder_paths.get_input_directory()
        sub = post.get("subfolder", "") or ""
        d = os.path.realpath(os.path.join(base, sub))
        if os.path.commonpath([d, os.path.realpath(base)]) != os.path.realpath(base):
            return web.Response(status=400)
        os.makedirs(d, exist_ok=True)
        name = os.path.basename(f.filename)
        if str(post.get("overwrite", "")).lower() not in ("true", "1"):
            stem, ext = os.path.splitext(name)
            i = 1
            while os.path.exists(os.path.join(d, name)):
                name = f"{stem} ({i}){ext}"
                i += 1
        with open(os.path.join(d, name), "wb") as out:
            out.write(f.file.read())
        return web.json_response({"name": name, "subfolder": sub, "type": kind})

    @r.post("/upload/image")
    async def upload_image(request):
        return await _upload(request)

    @r.post("/upload/mask")
    async def upload_mask(request):
        return await _upload(request, mask=True)

    @r.get("/system_stats")
    async def system_stats(request):
        return web.json_response({"system": {"os": "linux", "ram_total": 16 * 1024 ** 3, "ram_free": 12 * 1024 ** 3,
                                             "comfyui_version": "0.45.0 (sandbox stand-in)", "python_version": sys.version,
                                             "embedded_python": False, "argv": sys.argv},
                                  "devices": [{"name": "cpu (sandbox: nothing renders)", "type": "cpu", "index": None,
                                               "vram_total": 16 * 1024 ** 3, "vram_free": 16 * 1024 ** 3,
                                               "torch_vram_total": 0, "torch_vram_free": 0}]})

    @r.get("/models")
    async def models(request):
        return web.json_response(sorted(folder_paths.folder_names_and_paths))

    @r.get("/models/{folder}")
    async def models_in(request):
        return web.json_response(folder_paths.get_filename_list(request.match_info["folder"]))

    @r.get("/embeddings")
    async def embeddings(request):
        return web.json_response([])

    @r.get("/extensions")
    async def extensions(request):
        return web.json_response([])

    @r.get("/features")
    async def features(request):
        return web.json_response({"supports_preview_metadata": False, "max_upload_size": 1024 ** 3})

    @r.get("/sandbox/health")
    async def health(request):
        return web.json_response({"ok": True, "pack_error": STATE["pack_error"], "jobs_dir": STATE["jobs_dir"]})

    # ComfyUI serves every route both as /x and /api/x
    for route in list(r):
        ps.app.router.add_route(route.method, route.path, route.handler)
        ps.app.router.add_route(route.method, "/api" + route.path, route.handler)

    async def index(request):
        raise web.HTTPFound("/studio")
    ps.app.router.add_get("/", index)


# ------------------------------------------------------------------ main
def configure(root, jobs_dir):
    folder_paths.configure(os.path.abspath(root))
    STATE["jobs_dir"] = os.path.abspath(jobs_dir)


async def main(args):
    configure(args.root, args.jobs)
    STATE["render_seconds"] = args.render_seconds
    load_definitions()
    loop = asyncio.get_running_loop()
    ps = PromptServer(loop)
    ps.port = args.port
    if not args.no_studio:
        try:
            name, n, _web = load_pack(PACK)
            print(f"[sandbox] loaded {name} ({n} node types)")
        except Exception:
            STATE["pack_error"] = traceback.format_exc()
            print("[sandbox] the Studio pack failed to load:\n" + STATE["pack_error"])
    add_comfy_routes(ps)
    ps.app.add_routes(ps.routes)
    runner = web.AppRunner(ps.app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()
    print(f"[sandbox] stand-in ComfyUI on http://{args.host}:{args.port} (data: {os.path.abspath(args.root)})", flush=True)
    await render_loop(ps)


def cli(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=os.path.join(HERE, "run"))
    p.add_argument("--jobs", default=os.path.join(HERE, "jobs"))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8188)
    p.add_argument("--render-seconds", type=float, default=float(os.environ.get("SANDBOX_RENDER_SECONDS", "1.0")))
    p.add_argument("--no-studio", action="store_true", help="only the stand-in ComfyUI, without Studio's pack")
    args = p.parse_args(argv)
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
