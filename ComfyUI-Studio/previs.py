"""Previz: turn a 3D still (a frame of a set, a blocking render) into a real film frame with the Edit app.

Used by the music video's start frames (musicvideo.py) and open to any 3D still: the still is <Picture 1>, an optional
reference (an outfit sheet, a set plate) is <Picture 2>, and the prompt says what to keep and what to paint.
"""
import asyncio
import os
import time

from aiohttp import web

H = {}          # helpers from __init__ (set in setup)


def setup(ns):
    H.update(ns)
    routes = ns["routes"]
    _new_job, _ai_job, _need_private = ns["_new_job"], ns["_ai_job"], ns["_need_private"]

    # ---- turn a 3D still into a film frame with the Edit app
    @routes.post("/studio/api/previs/style")
    async def pv_style(request):
        b = await request.json()
        private = bool(b.get("private"))
        if private:
            _need_private(request)
        H["_input_path"](b["still"])          # both must be in the input folder (raises otherwise)
        ref = H["_input_path"](b["ref"]) if b.get("ref") else None
        job = _new_job(private)

        async def run():
            app = H["_app_by_name"]("edit")
            vals = {}
            pic = next(i for i in app["inputs"] if i["kind"] == "image")
            vals[pic["key"]] = b["still"]
            imgs = [i for i in app["inputs"] if i["kind"] == "image"]
            tog = next((i for i in app["inputs"] if i["kind"] == "toggle" and "second" in i["label"].lower()), None)
            if ref and len(imgs) > 1:
                vals[imgs[1]["key"]] = b["ref"]
                if tog:
                    vals[tog["key"]] = True
            elif tog:
                vals[tog["key"]] = False
            txt = next(i for i in app["inputs"] if i["kind"] == "text")
            vals[txt["key"]] = b.get("prompt") or ""
            mdl = next((i for i in app["inputs"] if i["kind"] == "choice"), None)
            if mdl and b.get("model"):
                want = next((o for o in mdl["options"] if b["model"].lower() in o.lower()), None)
                if want:
                    vals[mdl["key"]] = want
            unc = next((i for i in app["inputs"] if i["kind"] == "toggle" and "uncensored" in i["label"].lower()), None)
            if unc:
                vals[unc["key"]] = bool(b.get("uncensored"))
            seed = next((i for i in app["inputs"] if i["kind"] == "seed"), None)
            if seed:
                vals[seed["key"]] = int(time.time()) % 2_000_000_000
            job["stage"] = "Painting the frame"
            pid = await H["_queue_app"](app, vals, private, "3D frame", b.get("target"))
            job["pids"].append(pid)
            fn, sub = await H["_wait_output"](pid, job)
            full = await asyncio.to_thread(H["_find_out"], fn, sub, private)
            return {"value": H["_to_input"](full, private), "output": {"filename": os.path.basename(full), "subfolder": sub.replace(H["INCOMING"], "").strip("/")}}
        asyncio.ensure_future(_ai_job(job, run))
        return web.json_response({"id": job["id"]})
