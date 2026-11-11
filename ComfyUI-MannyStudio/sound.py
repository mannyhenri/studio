"""Music (ACE-Step 1.5 XL Turbo) and Voices (Chatterbox) for Studio.

Both build their ComfyUI prompt directly (no app file), queue it, wait, and save to
Audio/Music or Audio/Voices (Incognito: the private space)."""
import os
import random
import time

from aiohttp import web

H = {}
AUDIO_EXT = (".mp3", ".flac", ".wav", ".ogg", ".m4a")
ACE = {"unet": "acestep_v1.5_xl_turbo_bf16.safetensors", "vae": "ace_1.5_vae.safetensors",
       "te1": "qwen_0.6b_ace15.safetensors", "te2": "qwen_4b_ace15.safetensors"}
KEYS = [f"{n} {m}" for m in ("major", "minor") for n in ("C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B")]
LANGS = ["en", "fr", "es", "de", "it", "pt", "ja", "ko", "zh", "ru"]


def _clean(s, fallback):
    s = "".join(ch for ch in (s or "") if ch.isalnum() or ch in " -_").strip()[:60]
    return s or fallback


def music_prompt(b):
    seconds = max(5.0, min(240.0, float(b.get("seconds") or 60)))
    seed = int(b.get("seed")) if str(b.get("seed", "")).strip().lstrip("-").isdigit() else random.randint(0, 2**31)
    lyrics = (b.get("lyrics") or "").strip()
    if b.get("instrumental") or not lyrics:
        lyrics = "[Instrumental]"
    key = b.get("key") if b.get("key") in KEYS else "C major"
    ts = str(b.get("timesig") or "4")
    ts = ts if ts in ("2", "3", "4", "6") else "4"
    name = _clean(b.get("name"), "Song")
    api = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": ACE["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3}},
        "3": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": ACE["te1"], "clip_name2": ACE["te2"], "type": "ace", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": ACE["vae"]}},
        "5": {"class_type": "TextEncodeAceStepAudio1.5", "inputs": {
            "clip": ["3", 0], "tags": (b.get("tags") or "cinematic, emotional, strings").strip(), "lyrics": lyrics,
            "seed": seed, "bpm": max(40, min(220, int(b.get("bpm") or 100))), "duration": seconds,
            "timesignature": ts, "language": b.get("language") if b.get("language") in LANGS else "en", "keyscale": key,
            "generate_audio_codes": True, "cfg_scale": 2, "temperature": 0.85, "top_p": 0.9, "top_k": 0, "min_p": 0}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": seconds, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
                                                   "seed": seed, "steps": 8, "cfg": 1, "sampler_name": "euler", "scheduler": "simple", "denoise": 1}},
        "9": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["8", 0], "vae": ["4", 0]}},
        "10": {"class_type": "SaveAudioMP3", "inputs": {"audio": ["9", 0], "filename_prefix": f"Audio/Music/{name}", "quality": "V0"}},
    }
    return api, seed


def voice_prompt(b):
    text = (b.get("text") or "").strip()
    if not text:
        raise web.HTTPBadRequest(text="Write what the voice should say")
    seed = int(b.get("seed")) if str(b.get("seed", "")).strip().isdigit() else random.randint(0, 2**31)
    name = _clean(b.get("name"), "Voice")
    tts = {"text": text[:3000], "exaggeration": max(0.25, min(2.0, float(b.get("exaggeration") or 0.5))),
           "cfg_weight": max(0.2, min(1.0, float(b.get("cfg_weight") or 0.5))),
           "temperature": max(0.05, min(5.0, float(b.get("temperature") or 0.8))), "seed": seed}
    api = {"2": {"class_type": "FL_ChatterboxTTS", "inputs": tts},
           "3": {"class_type": "SaveAudioMP3", "inputs": {"audio": ["2", 0], "filename_prefix": f"Audio/Voices/{name}", "quality": "V0"}}}
    if b.get("voice"):
        api["1"] = {"class_type": "LoadAudio", "inputs": {"audio": b["voice"]}}
        tts["audio_prompt"] = ["1", 0]
    return api, seed


def setup(ns):
    H.update(ns)
    routes = ns["routes"]
    _new_job, _need_private, _root, _port = ns["_new_job"], ns["_need_private"], ns["_root"], ns["_port"]

    async def _queue(api, private, label, target=None):
        if private:
            ns["_privatize"](api)
        return await ns["_queue_api"](api, {"manny_label": label}, private, target, "studio-sound")

    def _start(kind, build):
        async def handler(request):
            b = await request.json()
            private = bool(b.get("private"))
            api, seed = build(b)
            job = _new_job(private, kind=kind)
            label = "Music" if kind == "music" else "Voice"

            async def run():
                try:
                    job["stage"] = "Waiting in the queue"
                    pid = await _queue(api, private, label, b.get("target"))
                    job["pids"].append(pid)
                    job["stage"] = "Composing" if kind == "music" else "Speaking"
                    fn, sub = await ns["_wait_output"](pid, job)
                    sub = (sub or "").replace("\\", "/")
                    if private and sub.startswith(ns["INCOMING"]):
                        sub = sub[len(ns["INCOMING"]):].lstrip("/")
                    job.update(status="done", stage="Done", result={"filename": fn, "subfolder": sub, "private": private, "seed": seed})
                except Exception as e:
                    job.update(status="failed", stage="Failed", error=str(e))
            import asyncio
            asyncio.ensure_future(run())
            return web.json_response({"id": job["id"], "seed": seed})
        return handler

    routes.post("/manny/api/music")(_start("music", music_prompt))
    routes.post("/manny/api/voice")(_start("voice", voice_prompt))

    @routes.get("/manny/api/sounds")
    async def sounds(request):
        private = ns["_flag"](request.query.get("private"))
        if private:
            _need_private(request)
        base = os.path.join(_root(private), "Audio")
        out = []
        for dirpath, _d, files in os.walk(base):
            for fn in files:
                if fn.lower().endswith(AUDIO_EXT):
                    full = os.path.join(dirpath, fn)
                    st = os.stat(full)
                    sub = os.path.relpath(dirpath, _root(private)).replace("\\", "/")
                    out.append({"filename": fn, "subfolder": sub, "kind": "audio", "mtime": st.st_mtime, "size": st.st_size,
                                "group": "music" if "/Music" in "/" + sub else "voice", "private": private})
        out.sort(key=lambda x: -x["mtime"])
        return web.json_response(out[:300])

    @routes.get("/manny/api/sound/options")
    async def sound_options(request):
        return web.json_response({"keys": KEYS, "languages": LANGS})
