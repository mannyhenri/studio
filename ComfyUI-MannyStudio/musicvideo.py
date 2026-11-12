"""Music video: a whole planned music video (song + segment plan + prompts + set plates + outfit sheets) as one storyboard,
rendered segment by segment with LTX-2.5 in time with the song.

Import reads a production folder laid out like "Squared Down Beat":
  Music/<song>.wav                                   the full mix (and Music/*Stems*/*Lead Vocal*.wav, if there)
  3D/<...>/SDB_Generation_Plan_v04.csv               Segment, Name, In, Out, Dur (s), Outfit, Set, Lip-sync, ...
  3D/<...>/SDB_Seedance25_Prompts_v04.md             "## SEG01 | ..." blocks with a ``` prompt ```
  3D/<...>/Segments/SEGxx_<name>/SEGxx_start_frame.png, SEGxx_blocking_ref.mp4
  3D/<...>/Refs/SET_0n_<Set>_plate.png, CHAR_Vixen_<Outfit>_sheet.png
Everything a render needs is copied into ComfyUI's input folder (musicvideo/<slug>/); the production folder is only read.

Each segment then goes through two steps, both on the board's shot:
  frame   the blocking start frame becomes a real film frame (FLUX 3 Klein edit, with the outfit sheet and set plate as
          the reference), so LTX starts from the right Vixen, outfit and set
  render  LTX-2.5 two passes from that frame, hearing the song for exactly that stretch (frozen audio + A/V coupling
          guidance, so lips and moves land on the music). draft = smaller and faster, final = 1344x576.
The take is attached to the shot like any other take; Studio's film editor cuts them on the song."""
import asyncio
import csv
import io
import json
import os
import re
import shutil
import subprocess
import time
import uuid

from aiohttp import web, ClientSession

H = {}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
SIZES = {"draft": (1280, 512), "final": (1536, 640)}      # ~2.4:1; /128, so the half-size depth guide of each LTX pass stays /32
MAX_SECONDS = 15.0


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "musicvideo"


def _tc(s):
    """'1:06.4' or '66.4' -> seconds."""
    s = str(s or "").strip()
    if ":" in s:
        m, sec = s.split(":", 1)
        return int(m) * 60 + float(sec)
    return float(s or 0)


def _find(folder, pattern, dirs=False):
    rx = re.compile(pattern, re.I)
    for root, ds, fs in os.walk(folder):
        for n in (ds if dirs else fs):
            if rx.search(n):
                return os.path.join(root, n)
    return None


def parse_prompts(md):
    """{"01": prompt text, ...} from '## SEG01 | ...' blocks (first ``` block after the heading)."""
    out = {}
    for m in re.finditer(r"^##\s*SEG([0-9]{2}(?:-[0-9]{2})?)\b.*?$(.*?)(?=^##\s*SEG|\Z)", md, re.M | re.S):
        code = re.search(r"```(?:\w+)?\n(.*?)```", m.group(2), re.S)
        if code:
            out[m.group(1)] = code.group(1).strip()
    return out


def ltx_prompt(raw, extra=""):
    """A Seedance-style prompt with @references turned into a plain LTX description (LTX reads prose, not references)."""
    keep = []
    sec = ""
    for line in raw.splitlines():
        t = line.strip()
        h = re.match(r"^\[(.+?)\]", t)
        if h:
            sec = h.group(1).upper()
            continue
        if not t or sec in ("REFERENCES", "AUDIO", "MAINTAIN CONSISTENCY"):
            continue
        t = re.sub(r"\(?@(Video|Image|Audio)\s*\d+\)?:?", "", t)
        t = re.sub(r"\b(Follow|following)\s+for every cut.*?;", "", t)
        t = re.sub(r"\s{2,}", " ", t).strip(" :")
        if t:
            keep.append(t)
    txt = " ".join(keep)
    txt = re.sub(r"\b\d+ fast cuts?( following)?", "one continuous shot", txt)
    txt = re.sub(r"\b(following|along to|after|from|with)\s*([.,;])", r"\2", txt)
    txt = re.sub(r"\bsings along to with\b", "sings along with the song with", txt)
    txt = re.sub(r"\bcuts land on the beat\.?", "Moves land on the beat.", txt)
    txt = re.sub(r"\s+([.,;])", r"\1", txt)
    return (txt + (" " + extra if extra else "")).strip()[:2400]


def _ff():
    return H["_ffmpeg"]()


def _cut_audio(src, start, dur, dest):
    r = subprocess.run([_ff(), "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src, "-ac", "2", "-ar", "48000", dest],
                       capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + (r.stderr or b"").decode("utf-8", "ignore")[-300:])


def _montage(paths, dest, height=1024):
    """Pictures side by side (the outfit sheet, then the set plate): one reference picture for the frame edit."""
    from PIL import Image
    ims = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        w = max(1, round(im.width * height / im.height))
        ims.append(im.resize((w, height), Image.LANCZOS))
    out = Image.new("RGB", (sum(i.width for i in ims) + 16 * (len(ims) - 1), height), (20, 20, 20))
    x = 0
    for i in ims:
        out.paste(i, (x, 0))
        x += i.width + 16
    out.save(dest, quality=92)


def import_folder(folder, name=None, outfit_refs=None):
    import folder_paths
    if not os.path.isdir(folder):
        raise web.HTTPBadRequest(text="No such folder on this PC")
    plan_p = _find(folder, r"Generation_Plan.*\.csv$")
    prom_p = _find(folder, r"Prompts.*\.md$")
    song = _find(os.path.join(folder, "Music"), r"\.wav$") if os.path.isdir(os.path.join(folder, "Music")) else None
    vocal = _find(folder, r"lead vocal.*\.wav$")
    seg_root = _find(folder, r"^Segments$", dirs=True)
    refs = _find(folder, r"^Refs$", dirs=True)
    if not (plan_p and song):
        raise web.HTTPBadRequest(text="Need a *Generation_Plan*.csv and the song (Music/*.wav)")
    name = name or os.path.basename(folder.rstrip("\\/"))
    slug = _slug(name)
    dest = os.path.join(folder_paths.get_input_directory(), "musicvideo", slug)
    os.makedirs(dest, exist_ok=True)

    def inp(full):
        return os.path.relpath(full, folder_paths.get_input_directory()).replace("\\", "/")

    def copy(src, newname=None):
        if not src or not os.path.isfile(src):
            return ""
        d = os.path.join(dest, newname or os.path.basename(src))
        if not os.path.exists(d) or os.path.getsize(d) != os.path.getsize(src):
            shutil.copy2(src, d)
        return inp(d)

    prompts = parse_prompts(open(prom_p, encoding="utf-8").read()) if prom_p else {}
    with open(plan_p, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    # set plates and outfit sheets
    plates = {}
    if refs:
        for fn in os.listdir(refs):
            m = re.match(r"SET_\d+_(.+?)_plate\.", fn, re.I)
            if m:
                plates[re.sub(r"[^a-z]", "", m.group(1).lower())] = copy(os.path.join(refs, fn))
    outfits = {}
    if refs:
        for fn in os.listdir(refs):
            m = re.match(r"CHAR_\w+?_(.+?)_sheet\.", fn, re.I)
            if m:
                outfits[re.sub(r"[^a-z]", "", m.group(1).lower())] = copy(os.path.join(refs, fn))
    for k, p in (outfit_refs or {}).items():
        if p and os.path.isfile(p):
            outfits[re.sub(r"[^a-z]", "", k.lower())] = copy(p)
    song_in = copy(song, "song.wav")
    vocal_in = copy(vocal, "lead_vocal.wav") if vocal else ""

    boards = H["_load_boards"]()
    b = next((x for x in boards if (x.get("musicvideo") or {}).get("slug") == slug), None)
    if not b:
        b = {"id": uuid.uuid4().hex[:10], "name": f"{name} · music video", "shots": [], "private": False, "created": time.time()}
        boards.append(b)
    b["musicvideo"] = {"slug": slug, "folder": folder, "song": song_in, "vocal": vocal_in, "imported": time.time()}
    missing = set()
    for r in rows:
        seg = (r.get("Segment") or "").strip()
        m = re.match(r"(\d{2})(?:-(\d{2}))?\s*(FC\d)?", seg)
        if not m:
            continue
        sid_key = seg.replace(" ", "_")
        t_in, t_out = _tc(r.get("In")), _tc(r.get("Out"))
        dur = float(r.get("Dur (s)") or (t_out - t_in))
        if m.group(3):           # a final-chorus face: the chorus window shared equally between the faces, one after another
            k = int(m.group(3)[2:]) - 1
            nfc = sum(1 for x in rows if re.search(r"FC\d", x.get("Segment") or "")) or 1
            win = _tc(r.get("Out")) - _tc(r.get("In"))
            dur = win / nfc
            t_in = t_in + dur * k
        t_out = t_in + dur
        segdir = _find(seg_root, (rf"^SEG{m.group(1)}_" if not m.group(3) else r"FinalChorus_PerFace"), dirs=True) if seg_root else None
        start = None
        if segdir:
            start = _find(segdir, (rf"^SEG{m.group(1)}_start_frame" if not m.group(3) else rf"^FC_Face{m.group(3)[2:]}_.*start_frame"))
        outfit = (r.get("Outfit") or "").strip()
        setname = (r.get("Set") or "").split("/")[0].strip()
        ok = re.sub(r"[^a-z]", "", outfit.lower())
        sk = re.sub(r"[^a-z]", "", setname.lower())
        oref = outfits.get(ok, "")
        pref = plates.get(sk) or next((v for k2, v in plates.items() if k2 in sk or sk in k2), "")
        if not oref:
            missing.add(outfit)
        # the song for exactly this stretch (and the lead vocal), cut once
        mix = os.path.join(dest, f"{sid_key}_mix_{t_in:.2f}.wav")
        if not os.path.exists(mix):
            _cut_audio(song, t_in, dur, mix)
        voc = ""
        if vocal:
            vp = os.path.join(dest, f"{sid_key}_vocal_{t_in:.2f}.wav")
            if not os.path.exists(vp):
                _cut_audio(vocal, t_in, dur, vp)
            voc = inp(vp)
        raw = (prompts.get(f"{m.group(1)}-{m.group(2)}") if m.group(2) else None) or prompts.get(m.group(1)) or ""
        sh = next((x for x in b["shots"] if x.get("mv_key") == sid_key), None)
        if not sh:
            sh = {"id": uuid.uuid4().hex[:8], "mv_key": sid_key, "takes": [], "pick": None}
            b["shots"].append(sh)
        sh.update({
            "name": f"{sid_key} · {(r.get('Name') or '').replace('_', ' ')}",
            "seconds": round(dur, 2), "song_in": round(t_in, 3), "song_out": round(t_out, 3),
            "outfit": outfit, "set": setname, "lipsync": (r.get("Lip-sync") or "").lower().startswith("y"),
            "outfit_ref": oref, "set_ref": pref,
            "blocking_frame": copy(start, f"{sid_key}_blocking_frame.png") if start else "",
            "audio": inp(mix), "vocal": voc, "engine": "perform",
            "raw_prompt": raw,
        })
        if not sh.get("prompt_edited"):
            sh["prompt"] = ltx_prompt(raw) if raw else f"Photoreal music video, {setname}, the singer in the {outfit}, performing to the song."
        sh["camera"] = ""
    b["shots"].sort(key=lambda s: s.get("song_in", 0))
    b["updated"] = time.time()
    H["_save_boards"](boards)
    return b, sorted(x for x in missing if x)


UNION_LORA = "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors"


def perform_api(image, audio, prompt, W, Hh, seconds, seed, prefix, depth=None, depth_strength=0.9, audio_start=0.0,
                depth_pass2=False):
    """LTX-2.5 two passes from a first frame, hearing `audio` (frozen) for lip-sync and timing.
    depth: a depth video of the shot rendered from the Blender camera (input file). With it the Union Control IC-LoRA
    makes LTX follow the exact camera move, the set's geometry and her blocking; the first frame sets the look."""
    vt = H["_videotools"]
    api = vt.ltx_keyframes_api([(image, 0.0)], prompt, None, W, Hh, seconds, seed)
    n = int(round(seconds * 24 / 8.0)) * 8 + 1
    api["la"] = {"class_type": "LoadAudio", "inputs": {"audio": audio}}
    api["at"] = {"class_type": "TrimAudioDuration", "inputs": {"audio": ["la", 0], "start_index": float(audio_start), "duration": round(n / 24.0, 3)}}
    api["ae"] = {"class_type": "LTXVAudioVAEEncode", "inputs": {"audio": ["at", 0], "audio_vae": ["va", 0]}}
    api["ea"] = {"class_type": "LTXVFreezeLatent", "inputs": {"latent": ["ae", 0]}}
    api["mg"] = {"class_type": "LTXVModalityGuidance", "inputs": {"model": ["um", 0], "modality_scale": 3.0, "start_percent": 0.0, "end_percent": 1.0}}
    for g in ("gd1", "gd2"):
        api[g]["inputs"]["model"] = ["mg", 0]
    if depth:
        w1, h1 = api["el"]["inputs"]["width"], api["el"]["inputs"]["height"]
        api["icl"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["um", 0], "lora_name": UNION_LORA, "strength_model": 1.0}}
        api["mg"]["inputs"]["model"] = ["icl", 0]
        api["icp"] = {"class_type": "GetICLoRAParameters", "inputs": {"iclora_model": ["icl", 0]}}
        api["dv"] = {"class_type": "LoadVideo", "inputs": {"file": depth}}
        api["dc"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["dv", 0]}}
        api["dn"] = {"class_type": "ImageFromBatch", "inputs": {"image": ["dc", 0], "batch_index": 0, "length": n}}
        for k, (ww, hh) in (("1", (w1 // 2, h1 // 2)), ("2", (W // 2, Hh // 2))):     # the reference is half the size of its pass
            api["ds" + k] = {"class_type": "ImageScale", "inputs": {"image": ["dn", 0], "upscale_method": "bilinear",
                                                                    "width": max(32, ww // 32 * 32), "height": max(32, hh // 32 * 32), "crop": "center"}}
        # pass 1: first frame, then the depth guide
        api["ic1"] = {"class_type": "LTXVAddGuide", "inputs": {"positive": ["cond", 0], "negative": ["cond", 1], "vae": ["vv", 0],
                                                               "latent": ["i1", 0], "image": ["ds1", 0], "frame_idx": 0,
                                                               "strength": float(depth_strength), "iclora_parameters": ["icp", 0]}}
        api["av1"]["inputs"]["video_latent"] = ["ic1", 2]
        for nid in ("gd1", "crop1"):
            api[nid]["inputs"]["positive"], api[nid]["inputs"]["negative"] = ["ic1", 0], ["ic1", 1]
        # pass 2 (full size) only refines 3 steps; the depth guide there costs ~20 GB more RAM at full size, so it is off
        # unless asked for: pass 1 already fixed the camera move and the set's geometry.
    if depth and depth_pass2:
        api["ic2"] = {"class_type": "LTXVAddGuide", "inputs": {"positive": ["crop1", 0], "negative": ["crop1", 1], "vae": ["vv", 0],
                                                               "latent": ["i2", 0], "image": ["ds2", 0], "frame_idx": 0,
                                                               "strength": float(depth_strength), "iclora_parameters": ["icp", 0]}}
        api["av2"]["inputs"]["video_latent"] = ["ic2", 2]
        for nid in ("gd2", "crop2"):
            api[nid]["inputs"]["positive"], api[nid]["inputs"]["negative"] = ["ic2", 0], ["ic2", 1]
    api["sv"]["inputs"]["filename_prefix"] = prefix
    return api


PH3 = {"unet": "p-h3-image_w4a8.safetensors", "te": "p-h3-image_text_encoder_w4a8.safetensors", "vae": "p-h3-image_vae_int8_convrot.safetensors",
       "sigmas": "1.0, 0.984753, 0.967905, 0.951688, 0.925372, 0.881923, 0.810083, 0.528718, 0.0"}


def ph3_frame_api(pictures, prompt, W, Hh, seed, prefix):
    """P-H3 Image: edit <Picture 1> (the Blender camera frame) using the other pictures as references (sheets, plates)."""
    api = {
        "m": {"class_type": "UNETLoader", "inputs": {"unet_name": PH3["unet"], "weight_dtype": "default"}},
        "ms": {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["m", 0], "shift_video": 1.0, "shift_audio": 1.0}},
        "te": {"class_type": "CLIPLoader", "inputs": {"clip_name": PH3["te"], "type": "minimax", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": PH3["vae"]}},
    }
    cond = {"clip": ["te", 0], "prompt": prompt, "width": int(W), "height": int(Hh), "vae": ["vae", 0]}
    for k, pic in enumerate(pictures[:9], start=1):
        api[f"p{k}"] = {"class_type": "LoadImage", "inputs": {"image": pic}}
        cond[f"images.image_{k}"] = [f"p{k}", 0]
    api["c"] = {"class_type": "PH3ImageConditioning", "inputs": cond}
    api["g"] = {"class_type": "BasicGuider", "inputs": {"model": ["ms", 0], "conditioning": ["c", 0]}}
    api["nz"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
    api["ks"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    api["sg"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": PH3["sigmas"]}}
    api["sa"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["nz", 0], "guider": ["g", 0], "sampler": ["ks", 0],
                                                                    "sigmas": ["sg", 0], "latent_image": ["c", 1]}}
    api["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sa", 0], "vae": ["vae", 0]}}
    api["sv"] = {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": prefix}}
    return api


BL_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender", "studio_mv_shot.py")


async def blender_shot(blend, camera, f0, f1, W, Hh, dest_dir, job, far=45.0):
    """Renders the shot's first frame and its depth video from the .blend's own camera; returns (first.png, depth.mp4) paths."""
    import tempfile
    exe = H["_previs"].blender_exe()
    if not exe:
        raise RuntimeError("Blender isn't installed")
    tmp = tempfile.mkdtemp(prefix="studio_mv_")
    spec = {"camera": camera, "f0": int(f0), "f1": int(f1), "w": int(W), "h": int(Hh), "dw": int(W) // 2, "dh": int(Hh) // 2,
            "near": 0.3, "far": float(far), "out": tmp, "frame_at": int(f0)}
    sp = os.path.join(tmp, "spec.json")
    json.dump(spec, open(sp, "w", encoding="utf-8"))
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = await asyncio.create_subprocess_exec(exe, "-b", blend, "--factory-startup", "-P", BL_SCRIPT, "--", sp,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, creationflags=flags)
    tail = []
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        t = line.decode("utf-8", "ignore").rstrip()
        tail = (tail + [t])[-30:]
        if t.startswith("[studio]"):
            job["stage"] = "Blender: " + t[9:]
    await proc.wait()
    rp = os.path.join(tmp, "result.json")
    res = json.load(open(rp)) if os.path.isfile(rp) else {"ok": False, "error": " | ".join(tail[-6:])}
    if not res.get("ok"):
        raise RuntimeError("Blender: " + str(res.get("error"))[-500:])
    os.makedirs(dest_dir, exist_ok=True)
    first = os.path.join(dest_dir, "blender_first.png")
    shutil.copy2(os.path.join(tmp, "first.png"), first)
    depth = os.path.join(dest_dir, "depth.mp4")
    r = subprocess.run([_ff(), "-y", "-framerate", "24", "-i", os.path.join(tmp, "depth", "d_%04d.png"), "-c:v", "libx264", "-crf", "12",
                        "-pix_fmt", "yuv420p", depth], capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + (r.stderr or b"").decode("utf-8", "ignore")[-300:])
    shutil.rmtree(tmp, ignore_errors=True)
    return first, depth, res


def setup(ns):
    H.update(ns)
    routes = ns["routes"]

    def _local(request):
        if not ns["auth"].is_local(request):
            raise web.HTTPForbidden(text="Only from this PC")

    def _shot(bid, sid):
        boards = H["_load_boards"]()
        b = next((x for x in boards if x.get("id") == bid), None)
        s = next((x for x in (b or {}).get("shots", []) if x.get("id") == sid), None) if b else None
        if not s:
            raise web.HTTPNotFound(text="No such shot")
        return boards, b, s

    def _update_shot(bid, sid, **kv):
        boards, b, s = _shot(bid, sid)
        s.update(kv)
        b["updated"] = time.time()
        H["_save_boards"](boards)
        return s

    @routes.post("/manny/api/musicvideo/import")
    async def mv_import(request):
        """{folder, name?, outfits?: {"Architect": "C:\\...\\sheet.png", ...}} -> the board (one shot per segment)."""
        _local(request)
        body = await request.json()
        b, missing = await asyncio.to_thread(import_folder, body["folder"], body.get("name"), body.get("outfits"))
        return web.json_response({"id": b["id"], "name": b["name"], "shots": len(b["shots"]), "missing_outfits": missing,
                                  "segments": [{"id": s["id"], "name": s["name"], "seconds": s["seconds"], "outfit_ref": bool(s.get("outfit_ref")),
                                                "set_ref": bool(s.get("set_ref")), "frame": bool(s.get("blocking_frame"))} for s in b["shots"]]})

    @routes.post("/manny/api/musicvideo/{bid}/{sid}/frame")
    async def mv_frame(request):
        """The blocking start frame -> a real film frame of this segment (outfit sheet + set plate as the reference)."""
        bid, sid = request.match_info["bid"], request.match_info["sid"]
        body = await request.json() if request.can_read_body else {}
        _boards, b, s = _shot(bid, sid)
        if not s.get("blocking_frame"):
            raise web.HTTPBadRequest(text="This segment has no start frame from the 3D blocking")
        job = ns["_new_job"](False, kind="mvframe")

        async def run():
            import folder_paths
            root = folder_paths.get_input_directory()
            refs = [os.path.join(root, p) for p in (s.get("outfit_ref"), s.get("set_ref")) if p]
            ref = ""
            if refs:
                mpath = os.path.join(root, "musicvideo", b["musicvideo"]["slug"], f"{s['mv_key']}_ref.jpg")
                await asyncio.to_thread(_montage, refs, mpath)
                ref = os.path.relpath(mpath, root).replace("\\", "/")
            who = f"The woman is the singer from the left of the second picture, wearing exactly that outfit ({s.get('outfit')})"
            prompt = (f"Turn this 3D blocking render into a real cinematic film frame from a high-end music video. Keep the exact camera angle, "
                      f"lens, framing, perspective and where she stands and how she poses. {who}: same face, long voluminous wavy black hair, "
                      f"same clothes. The place is the {s.get('set')} on the right of the second picture: its architecture, materials and light. "
                      f"Photoreal, practical light only, deep blacks, natural skin. {body.get('extra') or ''}").strip()
            job["stage"] = "Painting the start frame"
            async with ClientSession() as cs:
                async with cs.post(f"http://127.0.0.1:{ns['_port']()}/manny/api/previs/style",
                                   json={"still": s["blocking_frame"], "ref": ref, "prompt": prompt, "model": "flux 3",
                                         "target": body.get("target")}) as r:
                    jid = (await r.json())["id"]
                while True:
                    await asyncio.sleep(3)
                    async with cs.get(f"http://127.0.0.1:{ns['_port']()}/manny/api/job/{jid}") as r:
                        j = await r.json()
                    job["stage"] = "Painting the start frame · " + (j.get("stage") or "")
                    if j.get("status") != "running":
                        break
            if j.get("status") != "done":
                raise RuntimeError(j.get("error") or "the frame didn't render")
            frames = (s.get("frames") or []) + [j["result"]["value"]]
            _update_shot(bid, sid, image=j["result"]["value"], frames=frames)
            return {"image": j["result"]["value"], "output": j["result"].get("output")}
        asyncio.ensure_future(ns["_ai_job"](job, run))
        return web.json_response({"id": job["id"]})

    @routes.post("/manny/api/musicvideo/shot")
    async def mv_shot(request):
        """One cut of the video, the full process. Body: {board, name, camera, f0, f1 (frames of the .blend), song_in (s),
        blend, pictures: [outfit sheet, set plate, crew sheet...] (input files), frame_prompt, prompt, q, depth_strength,
        steps: ["blender","frame","render"] (default all), seed}. Makes a shot on the board and fills it step by step."""
        _local(request)
        body = await request.json()
        import folder_paths
        root = folder_paths.get_input_directory()
        boards = H["_load_boards"]()
        b = next((x for x in boards if x.get("id") == body["board"]), None)
        if not b:
            raise web.HTTPNotFound(text="No such board")
        slug = (b.get("musicvideo") or {}).get("slug") or "musicvideo"
        key = _slug(body["name"])
        sh = next((x for x in b["shots"] if x.get("mv_key") == key), None)
        if not sh:
            sh = {"id": uuid.uuid4().hex[:8], "mv_key": key, "takes": [], "pick": None}
            b["shots"].append(sh)
        q = "final" if body.get("q") == "final" else "draft"
        W, Hh = SIZES[q]
        f0, f1 = int(body["f0"]), int(body["f1"])
        n = max(9, ((f1 - f0 + 1) - 1) // 8 * 8 + 1)
        f1 = f0 + n - 1
        sh.update(name=body["name"], camera=body.get("camera_words", ""), blender_camera=body["camera"], f0=f0, f1=f1,
                  seconds=round(n / 24.0, 3), song_in=float(body.get("song_in", (f0 - 1) / 24.0)), engine="perform",
                  prompt=body.get("prompt") or sh.get("prompt") or "", frame_prompt=body.get("frame_prompt") or sh.get("frame_prompt") or "",
                  pictures=body.get("pictures") or sh.get("pictures") or [])
        b["updated"] = time.time()
        H["_save_boards"](boards)
        steps = body.get("steps") or ["blender", "frame", "render"]
        seed = int(body.get("seed") or int(time.time()) % 2 ** 31)
        shot_dir = os.path.join(root, "musicvideo", slug, "shots", key)
        job = ns["_new_job"](False, kind="mvshot")

        def rel(p):
            return os.path.relpath(p, root).replace("\\", "/")

        async def run():
            res = {}
            if "blender" in steps:
                job["stage"] = "Blender: opening the set"
                first, depth, info = await blender_shot(body["blend"], body["camera"], f0, f1, *SIZES["final"], shot_dir, job,
                                                        float(body.get("far") or 45.0))
                _update_shot(b["id"], sh["id"], blender_first=rel(first), depth=rel(depth))
                res["blender"] = {"first": rel(first), "depth": rel(depth), "info": info}
            _bb, _b, s = _shot(b["id"], sh["id"])
            if "frame" in steps:
                pics = [s["blender_first"]] + list(s.get("pictures") or [])
                api = ph3_frame_api(pics, s["frame_prompt"], 1536, 640, seed, f"Images/Music video/{slug}/{key} frame")
                job["stage"] = "P-H3: painting the start frame"
                out = await H["_videotools"]._render(job, api, f"{s['name']} start frame", "P-H3: painting the start frame", body.get("target"))
                dst = os.path.join(shot_dir, f"frame_{seed}.png")
                os.makedirs(shot_dir, exist_ok=True)
                shutil.copy2(out, dst)
                _update_shot(b["id"], sh["id"], image=rel(dst), frames=(s.get("frames") or []) + [rel(dst)])
                res["frame"] = rel(dst)
            _bb, _b, s = _shot(b["id"], sh["id"])
            if "render" in steps:
                audio = os.path.join(shot_dir, f"song_{s['song_in']:.3f}.wav")
                if not os.path.exists(audio):
                    src = os.path.join(root, b["musicvideo"]["song"]) if b.get("musicvideo") else None
                    await asyncio.to_thread(_cut_audio, src, s["song_in"], s["seconds"] + 0.5, audio)
                api = perform_api(s["image"], rel(audio), s["prompt"], W, Hh, s["seconds"], seed,
                                  f"Videos/Music video/{slug}/{key} {q}", depth=s.get("depth") if body.get("depth", True) else None,
                                  depth_strength=float(body.get("depth_strength") or 0.9), depth_pass2=bool(body.get("depth_pass2")))
                out = await H["_videotools"]._render(job, api, f"{s['name']} ({q})", f"LTX: rendering the {q}", body.get("target"))
                vroot = ns["_root"](False)
                r2 = os.path.relpath(out, vroot).replace("\\", "/")
                H["_pipeline"].attach(b["id"], sh["id"], os.path.basename(out), os.path.dirname(r2), "take", f"{q} · seed {seed}")
                res["video"] = {"filename": os.path.basename(out), "subfolder": os.path.dirname(r2)}
            return res
        asyncio.ensure_future(ns["_ai_job"](job, run))
        return web.json_response({"id": job["id"], "shot": sh["id"], "frames": [f0, f1], "seconds": round(n / 24.0, 3), "seed": seed})

    @routes.post("/manny/api/musicvideo/{bid}/{sid}/render")
    async def mv_render(request):
        """{q: draft|final, seed?, audio: mix|vocal, target?} -> an LTX-2.5 take of this segment, in time with the song."""
        bid, sid = request.match_info["bid"], request.match_info["sid"]
        body = await request.json() if request.can_read_body else {}
        _boards, b, s = _shot(bid, sid)
        if not s.get("image"):
            raise web.HTTPBadRequest(text="Make the start frame first (or give the shot a first frame)")
        q = "final" if body.get("q") == "final" else "draft"
        W, Hh = SIZES[q]
        secs = min(MAX_SECONDS, float(s.get("seconds") or 5))
        seed = int(body.get("seed") or int(time.time()) % 2 ** 31)
        audio = s.get("vocal") if body.get("audio") == "vocal" and s.get("vocal") else s.get("audio")
        prefix = f"Videos/Music video/{b['musicvideo']['slug'] if b.get('musicvideo') else 'board'}/{s.get('mv_key', s['id'])} {q}"
        api = perform_api(s["image"], audio, s.get("prompt") or "", W, Hh, secs, seed, prefix)
        job = ns["_new_job"](False, kind="mvrender")

        async def run():
            out = await H["_videotools"]._render(job, api, f"{s.get('name', 'Segment')} ({q})", f"Rendering {q}", body.get("target"))
            root = ns["_root"](False)
            rel = os.path.relpath(out, root).replace("\\", "/")
            H["_pipeline"].attach(bid, sid, os.path.basename(out), os.path.dirname(rel), "take", f"{q} · seed {seed}")
            return {"filename": os.path.basename(out), "subfolder": os.path.dirname(rel), "seed": seed}
        asyncio.ensure_future(ns["_ai_job"](job, run))
        return web.json_response({"id": job["id"], "seed": seed, "seconds": secs, "size": [W, Hh]})
