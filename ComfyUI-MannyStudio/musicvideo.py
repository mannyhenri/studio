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
SIZES = {"draft": (1024, 448), "final": (1344, 576)}       # ~2.39:1, /32
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
        if m.group(3):           # a final-chorus face: 5 s each, played one after another inside the final chorus
            k = int(m.group(3)[2:]) - 1
            t_in, dur = t_in + 5.0 * k, 5.0
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
        mix = os.path.join(dest, f"{sid_key}_mix.wav")
        if not os.path.exists(mix):
            _cut_audio(song, t_in, dur, mix)
        voc = ""
        if vocal:
            vp = os.path.join(dest, f"{sid_key}_vocal.wav")
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


def perform_api(image, audio, prompt, W, Hh, seconds, seed, prefix):
    """LTX-2.5 two passes from a first frame, hearing `audio` (frozen) for lip-sync and timing."""
    vt = H["_videotools"]
    api = vt.ltx_keyframes_api([(image, 0.0)], prompt, None, W, Hh, seconds, seed)
    n = int(round(seconds * 24 / 8.0)) * 8 + 1
    api["la"] = {"class_type": "LoadAudio", "inputs": {"audio": audio}}
    api["at"] = {"class_type": "TrimAudioDuration", "inputs": {"audio": ["la", 0], "start_index": 0.0, "duration": round(n / 24.0, 3)}}
    api["ae"] = {"class_type": "LTXVAudioVAEEncode", "inputs": {"audio": ["at", 0], "audio_vae": ["va", 0]}}
    api["ea"] = {"class_type": "LTXVFreezeLatent", "inputs": {"latent": ["ae", 0]}}
    api["mg"] = {"class_type": "LTXVModalityGuidance", "inputs": {"model": ["um", 0], "modality_scale": 3.0, "start_percent": 0.0, "end_percent": 1.0}}
    for g in ("gd1", "gd2"):
        api[g]["inputs"]["model"] = ["mg", 0]
    api["sv"]["inputs"]["filename_prefix"] = prefix
    return api


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
