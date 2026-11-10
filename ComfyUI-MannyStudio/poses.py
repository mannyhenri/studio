"""Poses: stick-figure (OpenPose) pictures that make the Image app's SD 1.5 / SDXL checkpoints copy a pose.

Pose packs live in ComfyUI's input folder under poses/<pack>/. A pack from Civitai is a zip: only its pictures
are kept (no scripts, no folders from inside the zip). At render time Studio adds a ControlNet in front of the
checkpoint's sampler, picking the SD 1.5 or SDXL pose ControlNet to match the checkpoint."""
import asyncio
import io
import os
import time
import zipfile

from aiohttp import web, ClientSession

H = {}
PIC_EXT = (".png", ".jpg", ".jpeg", ".webp")
CN = {"sd15": "control_v11p_sd15_openpose_fp16.safetensors", "sdxl": "controlnet-openpose-sdxl-xinsir.safetensors",
      "illustrious": "controlnet-openpose-noobai-illustrious.safetensors"}
ANIME_SDXL = ("illustrious", "noob", "pony", "anime", "ilust", "animagine")
MAX_ZIP = 200 * 1024 * 1024


def _root():
    import folder_paths
    d = os.path.join(folder_paths.get_input_directory(), "poses")
    os.makedirs(d, exist_ok=True)
    return d


def _clean(s, n=60):
    return "".join(ch for ch in (s or "") if ch.isalnum() or ch in " -_.()").strip()[:n] or "pose"


def list_poses():
    root = _root()
    packs = []
    for p in sorted(os.listdir(root)):
        d = os.path.join(root, p)
        if not os.path.isdir(d):
            continue
        pics = sorted(f for f in os.listdir(d) if f.lower().endswith(PIC_EXT))
        if pics:
            packs.append({"name": p, "poses": [{"file": f"poses/{p}/{f}", "name": os.path.splitext(f)[0],
                                                "subfolder": f"poses/{p}", "filename": f} for f in pics]})
    return packs


def _is_pose_picture(name):
    """Packs often ship depth / canny / preview pictures next to the skeletons: keep only skeletons when marked."""
    low = name.lower()
    return not any(k in low for k in ("depth", "canny", "normal", "lineart", "preview", "example", "sample", "result"))


def _check_zip(data):
    if data[:2] == b"PK":
        return
    if data[:2] == b"7z":
        raise RuntimeError("This pack is a .7z archive; unpack it and add the pictures, or ask Claude to add 7z support")
    if data[:4] == b"Rar!":
        raise RuntimeError("This pack is a .rar archive; unpack it and add the pictures")
    head = data[:160].decode("utf-8", "replace").replace("\n", " ")
    raise RuntimeError("Civitai didn't send a zip (it sent: " + head + ")")


def extract(data, pack):
    _check_zip(data)
    dest = os.path.join(_root(), _clean(pack))
    os.makedirs(dest, exist_ok=True)
    n = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith(PIC_EXT) and i.file_size < 30 * 1024 * 1024]
        marked = [i for i in names if "openpose" in i.filename.lower() or "pose" in os.path.basename(i.filename).lower()]
        keep = [i for i in (marked or names) if _is_pose_picture(i.filename)] or names
        for i in keep:
            parts = [x for x in i.filename.replace("\\", "/").split("/") if x and x not in (".", "..")]
            base = _clean("_".join(parts[-2:]) if len(parts) > 1 else parts[-1], 100)
            root, ext = os.path.splitext(base)
            if ext.lower() not in PIC_EXT:
                continue
            with z.open(i) as src, open(os.path.join(dest, base), "wb") as out:
                out.write(src.read())
            n += 1
    return n


def _ckpt_family(name):
    """SD 1.5 checkpoints are ~2-4 GB; SDXL ones ~6-7 GB. Illustrious / NoobAI / Pony (anime SDXL) need their own pose ControlNet."""
    import folder_paths
    try:
        p = folder_paths.get_full_path("checkpoints", name)
        if p and os.path.getsize(p) < 4.5e9:
            return "sd15"
    except Exception:
        pass
    key = (name or "").replace("\\", "/")
    base = ""
    try:
        meta = H["_lora_meta"]()
        base = next((m.get("base") or "" for k, m in meta.items() if k.replace("\\", "/").endswith(key)), "")
    except Exception:
        pass
    if any(w in (base + " " + key).lower() for w in ANIME_SDXL):
        return "illustrious"
    return "sdxl"


def _trace_ckpt(api, ref, hops=12):
    """Follow a MODEL link back to a CheckpointLoaderSimple (through LoRAs, switches...)."""
    seen = 0
    while isinstance(ref, list) and seen < hops:
        n = api.get(str(ref[0]))
        if not n:
            return None
        if n.get("class_type") == "CheckpointLoaderSimple":
            return n
        nxt = n["inputs"].get("model") or n["inputs"].get("on_true") or n["inputs"].get("on_false")
        ref = nxt
        seen += 1
    return None


def inject_pose(api, pose_file, strength=0.85):
    """Adds the pose ControlNet in front of every sampler that runs a checkpoint. Other models are left as they are."""
    import folder_paths
    have = set(folder_paths.get_filename_list("controlnet") or [])
    pose_file = (pose_file or "").replace("\\", "/")
    if not pose_file.startswith("poses/") or ".." in pose_file:
        return False
    if not os.path.isfile(os.path.join(folder_paths.get_input_directory(), pose_file)):
        return False
    strength = max(0.0, min(1.5, float(strength or 0.85)))
    applied = {}
    hit = False
    for nid, n in list(api.items()):
        if n.get("class_type") not in ("KSampler", "KSamplerAdvanced"):
            continue
        ck = _trace_ckpt(api, n["inputs"].get("model"))
        if not ck:
            continue
        fam = _ckpt_family(ck["inputs"].get("ckpt_name", ""))
        cn = CN[fam]
        if cn not in have and fam == "illustrious":
            cn = CN["sdxl"]          # until the Illustrious one is downloaded
        if cn not in have:
            continue
        pos, neg = n["inputs"].get("positive"), n["inputs"].get("negative")
        key = (str(pos), str(neg), cn)
        if key not in applied:
            k = f"pose{len(applied)}"
            api[k + "_img"] = {"class_type": "LoadImage", "inputs": {"image": pose_file}, "_meta": {"title": "Pose"}}
            api[k + "_cn"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": cn}, "_meta": {"title": "Pose ControlNet"}}
            api[k] = {"class_type": "ControlNetApplyAdvanced",
                      "inputs": {"positive": pos, "negative": neg, "control_net": [k + "_cn", 0], "image": [k + "_img", 0],
                                 "strength": strength, "start_percent": 0.0, "end_percent": 0.8 if strength < 1 else 1.0},
                      "_meta": {"title": "Apply pose"}}
            applied[key] = k
        k = applied[key]
        n["inputs"]["positive"] = [k, 0]
        n["inputs"]["negative"] = [k, 1]
        hit = True
    return hit


def setup(ns):
    H.update(ns)
    routes = ns["routes"]

    @routes.get("/manny/api/poses")
    async def poses(request):
        import folder_paths
        have = set(folder_paths.get_filename_list("controlnet") or [])
        return web.json_response({"packs": await asyncio.to_thread(list_poses),
                                  "controlnets": {k: v in have for k, v in CN.items()}})

    @routes.post("/manny/api/poses/civitai")
    async def from_civitai(request):
        """Downloads a Civitai pose pack (type Poses, a zip) and keeps only its pictures."""
        b = await request.json()
        vid = int(b["version"])
        job = ns["_new_job"](False, kind="poses")

        async def run():
            info = await ns["_civ_get"](f"/model-versions/{vid}", {})
            files = [f for f in info.get("files", []) if str(f.get("name", "")).lower().endswith(".zip")]
            if not files:
                raise RuntimeError("This pose pack has no zip file")
            f = files[0]
            if (f.get("sizeKB") or 0) * 1024 > MAX_ZIP:
                raise RuntimeError("That pack is too big for a pose pack")
            url = f["downloadUrl"] if str(f.get("downloadUrl", "")).startswith("https://civitai.com/") else f"https://civitai.com/api/download/models/{vid}"
            job["stage"] = "Downloading"
            async with ClientSession(headers={"Authorization": f"Bearer {ns['_civ_key']()}", "User-Agent": "Studio/1.0"}) as s:
                async with s.get(url, timeout=None) as r:
                    if r.status != 200:
                        raise RuntimeError(f"Civitai answered {r.status}")
                    buf = bytearray()
                    async for chunk in r.content.iter_chunked(1 << 20):
                        buf += chunk
                        if len(buf) > MAX_ZIP:
                            break
                    data = bytes(buf)
                    job["ctype"] = r.headers.get("Content-Type")
            if len(data) > MAX_ZIP:
                raise RuntimeError("That pack is too big for a pose pack")
            pack = b.get("name") or (info.get("model") or {}).get("name") or f"pack {vid}"
            job["stage"] = "Unpacking"
            n = await asyncio.to_thread(extract, data, pack)
            if not n:
                raise RuntimeError("No pose pictures inside")
            return {"pack": _clean(pack), "poses": n}
        asyncio.ensure_future(ns["_ai_job"](job, run))
        return web.json_response({"id": job["id"]})

    @routes.post("/manny/api/poses/upload")
    async def upload(request):
        """A pose pack zip (or single pose pictures) picked in the browser."""
        reader = await request.multipart()
        pack, added = "My poses", 0
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "pack":
                pack = (await part.text()) or pack
                continue
            if part.name != "file" or not part.filename:
                continue
            data = await part.read(decode=False)
            if len(data) > MAX_ZIP:
                raise web.HTTPBadRequest(text="Too big for a pose pack")
            fn = part.filename.lower()
            if fn.endswith(".zip"):
                if pack == "My poses":
                    pack = os.path.splitext(os.path.basename(part.filename))[0]
                try:
                    added += await asyncio.to_thread(extract, data, pack)
                except Exception as e:
                    raise web.HTTPBadRequest(text=str(e))
            elif fn.endswith(PIC_EXT):
                dest = os.path.join(_root(), _clean(pack))
                os.makedirs(dest, exist_ok=True)
                with open(os.path.join(dest, _clean(os.path.basename(part.filename), 100)), "wb") as out:
                    out.write(data)
                added += 1
        if not added:
            raise web.HTTPBadRequest(text="No pose pictures found")
        return web.json_response({"pack": _clean(pack), "poses": added})
