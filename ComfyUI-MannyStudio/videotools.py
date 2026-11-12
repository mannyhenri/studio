"""Video tools: fix a part of a clip, fix a face, continue a shot, and keyframe shots (several pictures -> one shot).

Everything runs on the models already on this PC:
- Fix a part        LTX-2.5 re-draws the marked seconds from the clip itself (video to video), first and last frames pinned
- Fix a face        H3 Face Refine tracks and crops the face; LTX-2.5 re-draws the crop lightly; the face is stitched back
- Continue a shot   LTX-2.5 (default): the clip's last second is pinned at the start of the new part; or H3 Motion Context
                    (Ref2VA + 4-step turbo, the H3 model that rendered fine here)
- Keyframe shot     LTX-2.5 two-pass with a guide picture at each time

The H3 first/last-frame model (FL2VA) was the one loading both times this PC froze (Nov 10 and 11); Studio no longer uses it.
H3 work uses only the Ref2VA model, the one that rendered fine here.

Fixes are spliced back with ffmpeg so the untouched parts keep the original's quality.

Each tool renders where Run on says (body "target"): this PC, "auto" or a GPU machine, through the same path as the
apps (_queue_api: live check, model download, upload, pull back, forget). For another machine, remote_api() makes the
graph readable there; ffmpeg probe and splice always run on this PC, on the pulled-back file."""
import asyncio
import json
import os
import subprocess
import time

from aiohttp import web, ClientSession

H = {}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# model files on this PC (same ones the Video app uses)
H3_REF2VA = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
H3_REF2VA_TURBO = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
H3_TE = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
H3_VVAE = "minimax_h3_video_vae_int8_convrot.safetensors"
H3_AVAE = "minimax_h3_audio_vae_fp32.safetensors"
LTX_UNET = "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"
LTX_TE = "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
LTX_VVAE = "ltx-2.5-video-vae-bf16.safetensors"
LTX_AVAE = "ltx-2.5-audio-vae-bf16.safetensors"
LTX_UPS = "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"
LTX_NEG = "pc game, console game, video game, cartoon, CGI, plastic skin, blurry, low quality, deformed face, distorted"
FACE_DET = "face_yolov8m.pt"
EXT_LENGTHS = {5: 124, 7: 175, 9: 226, 11: 277}       # motion-context friendly lengths (video and audio frames align)


# ---------------------------------------------------------------- helpers
def probe(path):
    """duration (s), fps, width, height, has_audio."""
    import av
    with av.open(path) as c:
        v = c.streams.video[0]
        fps = float(v.average_rate or v.guessed_rate or 24)
        dur = float(c.duration / 1e6) if c.duration else (float(v.frames) / fps if v.frames else 0.0)
        return {"duration": dur, "fps": fps, "width": v.codec_context.width, "height": v.codec_context.height,
                "audio": bool(c.streams.audio)}


def h3_size(w, h, mp=0.45):
    """A /32 canvas with the source's shape, about mp megapixels (H3 likes ~0.5-0.75)."""
    ar = w / float(h)
    W = (mp * 1e6 * ar) ** 0.5
    Hh = W / ar
    return max(256, int(round(W / 32)) * 32), max(256, int(round(Hh / 32)) * 32)


def _ff():
    return H["_ffmpeg"]()


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + (r.stderr or b"").decode("utf-8", "ignore")[-400:])


def splice(orig, new, at, take_from, take_to, dest):
    """orig[0:at] + new[take_from:take_to] + orig[at+(take_to-take_from):]. Size and frame rate follow the original;
    the original's sound is kept unless the whole new clip carries the new sound (handled by the caller)."""
    p = probe(orig)
    seg = max(0.05, take_to - take_from)
    end = min(p["duration"], at + seg)
    fps = round(p["fps"], 3)
    fc = (f"[0:v]trim=0:{at:.3f},setpts=PTS-STARTPTS,fps={fps}[a];"
          f"[1:v]trim={take_from:.3f}:{take_to:.3f},setpts=PTS-STARTPTS,scale={p['width']}:{p['height']}:flags=lanczos,fps={fps},setsar=1[b];"
          f"[0:v]trim=start={end:.3f},setpts=PTS-STARTPTS,fps={fps}[c];"
          f"[a][b][c]concat=n=3:v=1:a=0[v]")
    if at <= 0.01:
        fc = fc.replace(f"[0:v]trim=0:{at:.3f},setpts=PTS-STARTPTS,fps={fps}[a];", "").replace("[a][b][c]concat=n=3", "[b][c]concat=n=2")
    cmd = [_ff(), "-y", "-i", orig, "-i", new, "-filter_complex", fc, "-map", "[v]"]
    if p["audio"]:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "256k"]
    cmd += ["-c:v", "libx264", "-crf", "15", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart", dest]
    _run(cmd)


def splice_with_new_sound(orig, new, at, take_from, take_to, dest):
    """Like splice, but the sound in the window comes from the new clip too."""
    p = probe(orig)
    seg = max(0.05, take_to - take_from)
    end = min(p["duration"], at + seg)
    fps = round(p["fps"], 3)
    parts_v, parts_a, n = [], [], 0
    fc = ""
    if at > 0.01:
        fc += f"[0:v]trim=0:{at:.3f},setpts=PTS-STARTPTS,fps={fps}[v0];[0:a]atrim=0:{at:.3f},asetpts=PTS-STARTPTS[a0];"
        parts_v.append("[v0]"); parts_a.append("[a0]"); n += 1
    fc += (f"[1:v]trim={take_from:.3f}:{take_to:.3f},setpts=PTS-STARTPTS,scale={p['width']}:{p['height']}:flags=lanczos,fps={fps},setsar=1[v1];"
           f"[1:a]atrim={take_from:.3f}:{take_to:.3f},asetpts=PTS-STARTPTS,aresample=48000[a1];")
    parts_v.append("[v1]"); parts_a.append("[a1]"); n += 1
    if end < p["duration"] - 0.02:
        fc += f"[0:v]trim=start={end:.3f},setpts=PTS-STARTPTS,fps={fps}[v2];[0:a]atrim=start={end:.3f},asetpts=PTS-STARTPTS,aresample=48000[a2];"
        parts_v.append("[v2]"); parts_a.append("[a2]"); n += 1
    fc += "".join(v + a for v, a in zip(parts_v, parts_a)) + f"concat=n={n}:v=1:a=1[v][a]"
    _run([_ff(), "-y", "-i", orig, "-i", new, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
          "-c:v", "libx264", "-crf", "15", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", dest])


H3_TE_GGUF = "MiniMax-H3-encoder-Q4_K_M.gguf"


def _gguf_te():
    """The GGUF build of H3's text encoder, when the GGUF node pack is loaded and the file is in place."""
    try:
        import nodes
        import folder_paths
        if "CLIPLoaderGGUF" not in nodes.NODE_CLASS_MAPPINGS:
            return None
        for kind in ("clip_gguf", "text_encoders"):   # the GGUF pack lists .gguf files under its own key
            try:
                names = folder_paths.get_filename_list(kind) or []
            except Exception:
                continue
            for n in names:
                if n.replace("\\", "/").split("/")[-1] == H3_TE_GGUF:
                    full = folder_paths.get_full_path(kind, n)
                    if full and os.path.getsize(full) > 19e9:
                        return n
    except Exception:
        pass
    return None


def _h3_loaders(api, unet, lora):
    """H3's 32B text encoder runs on the CPU (slower, a few minutes): the graphics memory then only holds the video model.
    When this PC froze, the encoder (15 GB) sat in graphics memory while the video model was loading next to it."""
    api["m"] = {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}}
    api["lo"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["m", 0], "lora_name": lora, "strength_model": 1.0}}
    api["te"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": H3_TE, "type": "minimax", "device": "cpu"}}
    api["vv"] = {"class_type": "VAELoader", "inputs": {"vae_name": H3_VVAE}}
    api["va"] = {"class_type": "VAELoader", "inputs": {"vae_name": H3_AVAE}}


def _save(api, images, audio, prefix, fps=24.0):
    api["cv"] = {"class_type": "CreateVideo", "inputs": {"images": images, "fps": fps, **({"audio": audio} if audio else {})}}
    api["sv"] = {"class_type": "SaveVideo", "inputs": {"video": ["cv", 0], "filename_prefix": prefix, "format": "mp4", "format.codec": "auto"}}


# ---------------------------------------------------------------- graphs
def _ltx_sigmas(denoise):
    """The distilled LTX-2.5 schedule, started part-way down for video-to-video."""
    d = max(0.15, min(0.95, float(denoise)))
    tail = [s for s in (0.909375, 0.725, 0.5625, 0.421875, 0.28, 0.14) if s < d - 0.02]
    return ", ".join(f"{x:.4f}" for x in [d] + tail + [0.0])


def _ltx_frames(dur):
    """LTX wants 8k+1 frames."""
    return max(9, int((dur * 24 - 1) // 8) * 8 + 1)


def _ltx_v2v(api, images, n, prompt, denoise, seed, guides=(), audio=None):
    """Re-draws `images` (n = 8k+1 frames) with LTX-2.5 at partial denoise. guides: [(image ref, frame index)] pinned exactly.
    The text encoder runs on the CPU so the graphics memory only ever holds the video model.
    audio: the clip's own sound for these frames. It is encoded and frozen, so LTX hears the singing while it re-draws
    and keeps the mouth in sync (plus A/V coupling guidance, which strengthens lip-sync)."""
    api.update({
        "um": {"class_type": "UNETLoader", "inputs": {"unet_name": LTX_UNET, "weight_dtype": "default"}},
        "te": {"class_type": "CLIPLoader", "inputs": {"clip_name": LTX_TE, "type": "ltxv", "device": "cpu"}},
        "vv": {"class_type": "VAELoader", "inputs": {"vae_name": LTX_VVAE}},
        "va": {"class_type": "VAELoader", "inputs": {"vae_name": LTX_AVAE}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["te", 0], "text": prompt}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["te", 0], "text": LTX_NEG}},
        "cond": {"class_type": "LTXVConditioning", "inputs": {"positive": ["pos", 0], "negative": ["neg", 0], "frame_rate": 24.0}},
        "enc": {"class_type": "VAEEncode", "inputs": {"pixels": images, "vae": ["vv", 0]}},
    })
    if audio:
        api["at"] = {"class_type": "TrimAudioDuration", "inputs": {"audio": audio, "start_index": 0.0, "duration": round(n / 24.0, 3)}}
        api["ae"] = {"class_type": "LTXVAudioVAEEncode", "inputs": {"audio": ["at", 0], "audio_vae": ["va", 0]}}
        api["ea"] = {"class_type": "LTXVFreezeLatent", "inputs": {"latent": ["ae", 0]}}
        api["mg"] = {"class_type": "LTXVModalityGuidance", "inputs": {"model": ["um", 0], "modality_scale": 3.0, "start_percent": 0.0, "end_percent": 1.0}}
        model = ["mg", 0]
    else:
        api["ea"] = {"class_type": "LTXVEmptyLatentAudio", "inputs": {"frames_number": n, "frame_rate": 24, "batch_size": 1, "audio_vae": ["va", 0]}}
        model = ["um", 0]
    pos, neg, lat = ["cond", 0], ["cond", 1], ["enc", 0]
    for k, (img, idx) in enumerate(guides):
        api[f"gd{k}"] = {"class_type": "LTXVAddGuide", "inputs": {"positive": pos, "negative": neg, "vae": ["vv", 0], "latent": lat,
                                                                   "image": img, "frame_idx": int(idx), "strength": 1.0}}
        pos, neg, lat = [f"gd{k}", 0], [f"gd{k}", 1], [f"gd{k}", 2]
    api["av"] = {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": lat, "audio_latent": ["ea", 0]}}
    api["gu"] = {"class_type": "LTXVDualCFGGuider", "inputs": {"model": model, "positive": pos, "negative": neg, "video_cfg": 1.0, "audio_cfg": 1.0}}
    api["sg"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": _ltx_sigmas(denoise)}}
    api["ks"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    api["nz"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
    api["sa"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["nz", 0], "guider": ["gu", 0], "sampler": ["ks", 0],
                                                                    "sigmas": ["sg", 0], "latent_image": ["av", 0]}}
    api["sep"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["sa", 0]}}
    api["crop"] = {"class_type": "LTXVCropGuides", "inputs": {"positive": pos, "negative": neg, "latent": ["sep", 0]}}
    api["dec"] = {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["crop", 2], "vae": ["vv", 0], "tile_size": 512, "overlap": 64,
                                                             "temporal_size": 32, "temporal_overlap": 8}}
    return ["dec", 0]


def _frames(api, path, start, n, size):
    """n frames from `start` seconds, on a /32 canvas (big sources come down to ~0.8 MP)."""
    w, h = size
    api["lv"] = {"class_type": "H3LoadVideoPath", "inputs": {"path": path}}
    rz, rv = ("megapixels", 0.8) if w * h > 900_000 else ("none", 1.0)
    api["fr"] = {"class_type": "H3VideoFrames", "inputs": {"video": ["lv", 0], "start_seconds": float(start), "duration_seconds": n / 24.0 + 0.2,
                                                            "frame_rate": "24 (H3 native)", "resize": rz, "resize_value": rv, "snap_to_h3": True}}
    api["cut"] = {"class_type": "ImageFromBatch", "inputs": {"image": ["fr", 0], "batch_index": 0, "length": int(n)}}
    return ["cut", 0]


def retake_api(path, clip_from, clip_len, prompt, denoise, seed, size):
    """Re-draws clip_from .. clip_from+clip_len with LTX-2.5; the first and last frames are pinned to the original."""
    api = {}
    n = _ltx_frames(clip_len)
    imgs = _frames(api, path, clip_from, n, size)
    api["f0"] = {"class_type": "ImageFromBatch", "inputs": {"image": imgs, "batch_index": 0, "length": 1}}
    api["f1"] = {"class_type": "ImageFromBatch", "inputs": {"image": imgs, "batch_index": n - 1, "length": 1}}
    out = _ltx_v2v(api, imgs, n, prompt, denoise, seed, guides=[(["f0", 0], 0), (["f1", 0], n - 1)], audio=["fr", 1])
    _save(api, out, ["fr", 1], "Videos/_work/retake")
    return api, n


def face_api(path, start, dur, prompt, denoise, seed, size):
    """Tracks the face, re-draws the face crop with LTX-2.5 at low denoise, stitches it back."""
    api = {}
    n = _ltx_frames(dur)
    imgs = _frames(api, path, start, n, size)
    api["tr"] = {"class_type": "H3FaceTrackCrop", "inputs": {
        "images": imgs, "detector": FACE_DET, "confidence": 0.35, "crop_factor": 3.0, "canvas_width": 768, "canvas_height": 768,
        "canvas_mode": "auto_capped_768", "smooth_window": 21, "size_smooth_window": 51, "smooth_method": "gaussian", "size_mode": "per_frame",
        "select": "largest_face", "fallback_detector": "none", "cut_detection": "none", "absent_shots": "off"}}
    out = _ltx_v2v(api, ["tr", 0], n, prompt, denoise, seed, audio=["fr", 1])
    api["st"] = {"class_type": "H3FaceStitch", "inputs": {"base_images": imgs, "refined_crops": out, "transform": ["tr", 1],
                                                           "paste_region": "face_only", "mask_dilation": 24, "feather": 24, "colour_match": 1.0,
                                                           "blend": 1.0, "undetected_frames": "fade_out", "feather_scales_with_crop": False}}
    _save(api, ["st", 0], ["fr", 1], "Videos/_work/face")
    return api, n


def extend_api(path, prompt, seconds, ref, seed, w, h, src_dur, steps=4):
    api = {}
    _h3_loaders(api, H3_REF2VA, H3_REF2VA_TURBO)
    length = EXT_LENGTHS.get(int(seconds), 175)
    api["lv"] = {"class_type": "H3LoadVideoPath", "inputs": {"path": path}}
    api["all"] = {"class_type": "H3VideoFrames", "inputs": {"video": ["lv", 0], "start_seconds": 0.0, "duration_seconds": 0.0,
                                                             "frame_rate": "24 (H3 native)", "resize": "none", "resize_value": 1.0, "snap_to_h3": False}}
    api["tail"] = {"class_type": "H3VideoFrames", "inputs": {"video": ["lv", 0], "start_seconds": max(0.0, src_dur - 1.5), "duration_seconds": 0.0,
                                                              "frame_rate": "24 (H3 native)", "resize": "none", "resize_value": 1.0, "snap_to_h3": False}}
    api["last"] = {"class_type": "H3LastFrame", "inputs": {"images": ["tail", 0]}}
    if ref:
        api["ref"] = {"class_type": "LoadImage", "inputs": {"image": ref}}
    api["r2v"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"clip": ["te", 0], "prompt": prompt, "width": w, "height": h, "length": length,
                                                                         "ref_image_size": "match", "vae": ["vv", 0], "audio_vae": ["va", 0],
                                                                         "ref_images.ref_image_0": ["ref", 0] if ref else ["last", 0]}}
    api["mc"] = {"class_type": "MiniMaxH3MotionContext", "inputs": {"conditioning": ["r2v", 0], "vae": ["vv", 0], "latent": ["r2v", 1],
                                                                     "context_length": "22", "audio_context_length": 18, "context_frames": ["tail", 0],
                                                                     "audio_vae": ["va", 0], "context_audio": ["tail", 1]}}
    api["g"] = {"class_type": "BasicGuider", "inputs": {"model": ["lo", 0], "conditioning": ["mc", 0]}}
    api["ks"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}}
    api["sc"] = {"class_type": "BasicScheduler", "inputs": {"model": ["lo", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}}
    api["nz"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
    api["sa"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["nz", 0], "guider": ["g", 0], "sampler": ["ks", 0],
                                                                    "sigmas": ["sc", 0], "latent_image": ["r2v", 1]}}
    api["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sa", 0], "vae": ["vv", 0]}}
    api["dau"] = {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["sa", 0], "vae": ["va", 0]}}
    api["trim"] = {"class_type": "MiniMaxH3MotionContextTrim", "inputs": {"images": ["dec", 0], "trim_frames": ["mc", 1], "audio": ["dau", 0],
                                                                           "fps": 24.0, "match_tail": True}}
    api["cat"] = {"class_type": "H3ConcatAV", "inputs": {"images_a": ["all", 0], "audio_a": ["all", 1], "images_b": ["trim", 0],
                                                          "audio_b": ["trim", 1], "match_b": "match_to_a"}}
    _save(api, ["cat", 0], ["cat", 1], "Videos/_work/extend")
    return api


LTX_CTX = 25          # frames (~1 s) of the clip's end that the new part starts from


def ltx_extend_api(path, prompt, seconds, seed, W, H, src_dur):
    """Continue a shot with LTX-2.5: the last second of the clip is pinned at the start of a new clip (two passes, like the
    keyframe shot), so the motion carries on. The caller drops that second and joins the rest onto the original."""
    C = LTX_CTX
    N = int(round((C - 1 + seconds * 24) / 8.0)) * 8 + 1
    w1, h1 = max(64, (W // 2) // 32 * 32), max(64, (H // 2) // 32 * 32)
    api = {
        "um": {"class_type": "UNETLoader", "inputs": {"unet_name": LTX_UNET, "weight_dtype": "default"}},
        "te": {"class_type": "CLIPLoader", "inputs": {"clip_name": LTX_TE, "type": "ltxv", "device": "cpu"}},
        "vv": {"class_type": "VAELoader", "inputs": {"vae_name": LTX_VVAE}},
        "va": {"class_type": "VAELoader", "inputs": {"vae_name": LTX_AVAE}},
        "ups": {"class_type": "LatentUpscaleModelLoader", "inputs": {"model_name": LTX_UPS}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["te", 0], "text": prompt}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["te", 0], "text": LTX_NEG}},
        "cond": {"class_type": "LTXVConditioning", "inputs": {"positive": ["pos", 0], "negative": ["neg", 0], "frame_rate": 24.0}},
        "lv": {"class_type": "H3LoadVideoPath", "inputs": {"path": path}},
        "fr": {"class_type": "H3VideoFrames", "inputs": {"video": ["lv", 0], "start_seconds": max(0.0, src_dur - (C + 3) / 24.0),
                                                          "duration_seconds": 0.0, "frame_rate": "24 (H3 native)", "resize": "none",
                                                          "resize_value": 1.0, "snap_to_h3": False}},
        "ctx": {"class_type": "ImageFromBatch", "inputs": {"image": ["fr", 0], "batch_index": 2, "length": C}},   # the last C frames
        "full": {"class_type": "ImageScale", "inputs": {"image": ["ctx", 0], "upscale_method": "lanczos", "width": W, "height": H, "crop": "center"}},
        "half": {"class_type": "ImageScale", "inputs": {"image": ["ctx", 0], "upscale_method": "lanczos", "width": w1, "height": h1, "crop": "center"}},
        "el": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": w1, "height": h1, "length": N, "batch_size": 1}},
        "ea": {"class_type": "LTXVEmptyLatentAudio", "inputs": {"frames_number": N, "frame_rate": 24, "batch_size": 1, "audio_vae": ["va", 0]}},
        "g1": {"class_type": "LTXVAddGuide", "inputs": {"positive": ["cond", 0], "negative": ["cond", 1], "vae": ["vv", 0], "latent": ["el", 0],
                                                         "image": ["half", 0], "frame_idx": 0, "strength": 1.0}},
        "av1": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["g1", 2], "audio_latent": ["ea", 0]}},
        "gd1": {"class_type": "LTXVDualCFGGuider", "inputs": {"model": ["um", 0], "positive": ["g1", 0], "negative": ["g1", 1], "video_cfg": 1.0, "audio_cfg": 1.0}},
        "s1": {"class_type": "ManualSigmas", "inputs": {"sigmas": "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"}},
        "k1": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}},
        "n1": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}},
        "sa1": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["n1", 0], "guider": ["gd1", 0], "sampler": ["k1", 0], "sigmas": ["s1", 0], "latent_image": ["av1", 0]}},
        "sep1": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["sa1", 0]}},
        "crop1": {"class_type": "LTXVCropGuides", "inputs": {"positive": ["g1", 0], "negative": ["g1", 1], "latent": ["sep1", 0]}},
        "up": {"class_type": "LTXVLatentUpsampler", "inputs": {"samples": ["crop1", 2], "upscale_model": ["ups", 0], "vae": ["vv", 0]}},
        "g2": {"class_type": "LTXVAddGuide", "inputs": {"positive": ["crop1", 0], "negative": ["crop1", 1], "vae": ["vv", 0], "latent": ["up", 0],
                                                         "image": ["full", 0], "frame_idx": 0, "strength": 1.0}},
        "av2": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["g2", 2], "audio_latent": ["sep1", 1]}},
        "gd2": {"class_type": "LTXVDualCFGGuider", "inputs": {"model": ["um", 0], "positive": ["g2", 0], "negative": ["g2", 1], "video_cfg": 1.0, "audio_cfg": 1.0}},
        "s2": {"class_type": "ManualSigmas", "inputs": {"sigmas": "0.85, 0.7250, 0.4219, 0.0"}},
        "k2": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}},
        "n2": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed) + 1}},
        "sa2": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["n2", 0], "guider": ["gd2", 0], "sampler": ["k2", 0], "sigmas": ["s2", 0], "latent_image": ["av2", 0]}},
        "sep2": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["sa2", 0]}},
        "crop2": {"class_type": "LTXVCropGuides", "inputs": {"positive": ["g2", 0], "negative": ["g2", 1], "latent": ["sep2", 0]}},
        "dec": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["crop2", 2], "vae": ["vv", 0], "tile_size": 512, "overlap": 64,
                                                           "temporal_size": 32, "temporal_overlap": 8}},
        "dau": {"class_type": "LTXVAudioVAEDecode", "inputs": {"samples": ["sep2", 1], "audio_vae": ["va", 0]}},
    }
    _save(api, ["dec", 0], ["dau", 0], "Videos/_work/extend_ltx")
    return api, C


def _mean_db(path, start=None, end=None):
    cmd = [_ff(), "-v", "info"] + (["-ss", f"{start:.3f}"] if start is not None else []) + (["-to", f"{end:.3f}"] if end is not None else [])
    r = subprocess.run(cmd + ["-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, creationflags=NO_WINDOW)
    for line in (r.stderr or b"").decode("utf-8", "ignore").splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0])
            except Exception:
                return None
    return None


def join_after(orig, new, skip, dest):
    """orig + new[skip:]: the new clip's first `skip` seconds repeat the end of the original, so they are dropped.
    Picture and sound of the new part come from the new clip; size and frame rate follow the original."""
    p = probe(orig)
    q = probe(new)
    fps = round(p["fps"], 3)
    fc = (f"[0:v]fps={fps},setsar=1[v0];"
          f"[1:v]trim=start={skip:.3f},setpts=PTS-STARTPTS,scale={p['width']}:{p['height']}:flags=lanczos,fps={fps},setsar=1[v1];")
    if p["audio"] and q["audio"]:
        # the new part's sound is levelled to the last 3 s of the original, so the join doesn't jump in loudness
        a_db, b_db = _mean_db(orig, max(0.0, p["duration"] - 3.0)), _mean_db(new, skip)
        gain = max(-20.0, min(20.0, a_db - b_db)) if a_db is not None and b_db is not None else 0.0
        fc += (f"[0:a]aresample=48000[a0];[1:a]atrim=start={skip:.3f},asetpts=PTS-STARTPTS,aresample=48000,volume={gain:.2f}dB[a1];"
               f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
        maps = ["-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "256k"]
    else:
        fc += "[v0][v1]concat=n=2:v=1:a=0[v]"
        maps = ["-map", "[v]"]
    _run([_ff(), "-y", "-i", orig, "-i", new, "-filter_complex", fc, *maps,
          "-c:v", "libx264", "-crf", "15", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart", dest])


def ltx_keyframes_api(keys, prompt, negative, W, H, seconds, seed):
    """keys: [(input image value, seconds)], first at 0. Two passes (half size, then x2 latent upscale), like the corridor shot."""
    length = int(round(seconds * 24 / 8.0)) * 8 + 1
    w1, h1 = max(64, (W // 2) // 32 * 32), max(64, (H // 2) // 32 * 32)
    api = {
        "um": {"class_type": "UNETLoader", "inputs": {"unet_name": LTX_UNET, "weight_dtype": "default"}},
        "te": {"class_type": "CLIPLoader", "inputs": {"clip_name": LTX_TE, "type": "ltxv", "device": "cpu"}},   # keeps graphics memory for the video model
        "vv": {"class_type": "VAELoader", "inputs": {"vae_name": LTX_VVAE}},
        "va": {"class_type": "VAELoader", "inputs": {"vae_name": LTX_AVAE}},
        "ups": {"class_type": "LatentUpscaleModelLoader", "inputs": {"model_name": LTX_UPS}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["te", 0], "text": prompt}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["te", 0], "text": negative or LTX_NEG}},
        "cond": {"class_type": "LTXVConditioning", "inputs": {"positive": ["pos", 0], "negative": ["neg", 0], "frame_rate": 24.0}},
        "el": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": w1, "height": h1, "length": length, "batch_size": 1}},
        "ea": {"class_type": "LTXVEmptyLatentAudio", "inputs": {"frames_number": length, "frame_rate": 24, "batch_size": 1, "audio_vae": ["va", 0]}},
    }
    for k, (img, _t) in enumerate(keys):
        api[f"im{k}"] = {"class_type": "LoadImage", "inputs": {"image": img}}
        api[f"pp{k}"] = {"class_type": "LTXVPreprocess", "inputs": {"image": [f"im{k}", 0], "img_compression": 18}}
    api["i1"] = {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["vv", 0], "image": ["pp0", 0], "latent": ["el", 0], "strength": 0.7, "bypass": False}}
    pos, neg, lat = ["cond", 0], ["cond", 1], ["i1", 0]
    for k, (_img, t) in enumerate(keys[1:], start=1):
        idx = min(length - 1, max(8, int(round(t * 24 / 8.0)) * 8))
        api[f"g1_{k}"] = {"class_type": "LTXVAddGuide", "inputs": {"positive": pos, "negative": neg, "vae": ["vv", 0], "latent": lat,
                                                                    "image": [f"pp{k}", 0], "frame_idx": idx, "strength": 0.85}}
        pos, neg, lat = [f"g1_{k}", 0], [f"g1_{k}", 1], [f"g1_{k}", 2]
    api["av1"] = {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": lat, "audio_latent": ["ea", 0]}}
    api["gd1"] = {"class_type": "LTXVDualCFGGuider", "inputs": {"model": ["um", 0], "positive": pos, "negative": neg, "video_cfg": 1.0, "audio_cfg": 1.0}}
    api["s1"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"}}
    api["k1"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}}
    api["n1"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
    api["sa1"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["n1", 0], "guider": ["gd1", 0], "sampler": ["k1", 0], "sigmas": ["s1", 0], "latent_image": ["av1", 0]}}
    api["sep1"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["sa1", 0]}}
    api["crop1"] = {"class_type": "LTXVCropGuides", "inputs": {"positive": pos, "negative": neg, "latent": ["sep1", 0]}}
    api["up"] = {"class_type": "LTXVLatentUpsampler", "inputs": {"samples": ["crop1", 2], "upscale_model": ["ups", 0], "vae": ["vv", 0]}}
    api["i2"] = {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["vv", 0], "image": ["pp0", 0], "latent": ["up", 0], "strength": 1.0, "bypass": False}}
    pos, neg, lat = ["crop1", 0], ["crop1", 1], ["i2", 0]
    for k, (_img, t) in enumerate(keys[1:], start=1):
        idx = min(length - 1, max(8, int(round(t * 24 / 8.0)) * 8))
        api[f"g2_{k}"] = {"class_type": "LTXVAddGuide", "inputs": {"positive": pos, "negative": neg, "vae": ["vv", 0], "latent": lat,
                                                                    "image": [f"pp{k}", 0], "frame_idx": idx, "strength": 1.0}}
        pos, neg, lat = [f"g2_{k}", 0], [f"g2_{k}", 1], [f"g2_{k}", 2]
    api["av2"] = {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": lat, "audio_latent": ["sep1", 1]}}
    api["gd2"] = {"class_type": "LTXVDualCFGGuider", "inputs": {"model": ["um", 0], "positive": pos, "negative": neg, "video_cfg": 1.0, "audio_cfg": 1.0}}
    api["s2"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": "0.85, 0.7250, 0.4219, 0.0"}}
    api["k2"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}}
    api["n2"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed) + 1}}
    api["sa2"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["n2", 0], "guider": ["gd2", 0], "sampler": ["k2", 0], "sigmas": ["s2", 0], "latent_image": ["av2", 0]}}
    api["sep2"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["sa2", 0]}}
    api["crop2"] = {"class_type": "LTXVCropGuides", "inputs": {"positive": pos, "negative": neg, "latent": ["sep2", 0]}}
    api["dec"] = {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["crop2", 2], "vae": ["vv", 0], "tile_size": 512, "overlap": 64,
                                                             "temporal_size": 32, "temporal_overlap": 8}}
    api["dau"] = {"class_type": "LTXVAudioVAEDecode", "inputs": {"samples": ["sep2", 1], "audio_vae": ["va", 0]}}
    _save(api, ["dec", 0], ["dau", 0], "Videos/Keyframes/LTX keyframes")
    return api


# ---------------------------------------------------------------- running
def remote_api(api, dirs=None):
    """The graph as another machine can run it. H3LoadVideoPath reads a path on THIS PC: it becomes core LoadVideo
    with the clip as an annotated name ("Videos/x.mp4 [output]"), which the remote path uploads and renames, and whose
    VIDEO output H3VideoFrames takes as is. The GGUF text encoder stays a PC choice: the Mac runs the nvfp4 CLIPLoader
    on its CPU text-encoder server. Returns a new dict; api is untouched."""
    dirs = dirs or (H["_dirs"]() if "_dirs" in H else {})
    out = json.loads(json.dumps(api))
    for node in out.values():
        ct, ins = node.get("class_type"), node.get("inputs") or {}
        if ct == "H3LoadVideoPath":
            path = ins.get("path") or ""
            value = None
            for kind in ("output", "input", "temp"):
                root = dirs.get(kind)
                if root and os.path.isabs(path) and os.path.realpath(path).startswith(os.path.realpath(root) + os.sep):
                    value = os.path.relpath(os.path.realpath(path), os.path.realpath(root)).replace("\\", "/") + f" [{kind}]"
                    break
            if value is None:
                if os.path.isabs(path):
                    raise RuntimeError("That video isn't in Studio's folders, so another machine can't be sent it")
                value = path.replace("\\", "/") + " [input]"
            node["class_type"], node["inputs"] = "LoadVideo", {"file": value}
        elif ct == "CLIPLoaderGGUF":
            node["class_type"], node["inputs"] = "CLIPLoader", {"clip_name": H3_TE, "type": ins.get("type", "minimax"), "device": "default"}
    return out


async def _place(api, target):
    """Which machine renders: None for this PC, else the worker. "auto" is decided BEFORE the graph is shaped: every
    machine is checked against remote_api's form (this PC can run it too, so the check is fair to all), and this PC,
    if it wins, still gets its own graph (H3LoadVideoPath, its text encoder) and frees memory first."""
    if not target or target == H["_remote"].LOCAL:
        return None
    if target != H["_remote"].AUTO:
        return H["_remote"].find(H["_data_dir"](), target)
    try:
        shaped = remote_api(api)
    except RuntimeError:        # a clip outside Studio's folders can only be read here
        return None
    return await H["_choose"](shaped, target)


async def _queue(api, label, worker=None):
    """Queue api on this PC (worker None) or on that worker, by name; returns the prompt_id _wait_output follows."""
    if worker:
        api = remote_api(api)
    return await H["_queue_api"](api, {"manny_app": "videotools:" + label.lower().replace(" ", "-"), "manny_label": label},
                                 False, worker["label"] if worker else H["_remote"].LOCAL, "studio-videotools")


def _src(b):
    if b.get("private"):
        raise web.HTTPBadRequest(text="Video fixes don't work on Incognito videos yet")
    full = H["_out_path"](b["filename"], b.get("subfolder", ""), False)
    if not os.path.isfile(full):
        raise web.HTTPNotFound(text="That video is gone")
    return full


def _dest(kind, src, tag):
    d = os.path.join(H["_root"](False), "Videos", kind)
    os.makedirs(d, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0][:70]
    return os.path.join(d, f"{base} {tag} {time.strftime('%Y-%m-%d %H%M%S')}.mp4")


def _result(dest):
    root = H["_root"](False)
    rel = os.path.relpath(dest, root).replace("\\", "/")
    return {"filename": os.path.basename(dest), "subfolder": os.path.dirname(rel)}


async def _free_memory():
    """Big video models need most of this PC's memory: unload everything else first."""
    try:
        async with ClientSession() as s:
            async with s.post(f"http://127.0.0.1:{H['_port']()}/free", json={"unload_models": True, "free_memory": True}) as r:
                await r.read()
        await asyncio.sleep(3)
    except Exception:
        pass


async def _render(job, api, label, stage, target=None):
    worker = await _place(api, target)
    if not worker:          # another machine manages its own memory
        job["stage"] = "Freeing memory"
        await _free_memory()
    job["stage"] = "Waiting in the queue" + (f" on {worker['label']}" if worker else "")
    pid = await _queue(api, label, worker)
    job["pids"].append(pid)
    job["stage"] = stage
    fn, sub = await H["_wait_output"](pid, job)
    return H["_out_path"](fn, sub, False)


def setup(ns):
    H.update(ns)
    routes = ns["routes"]

    def _local_or_unlocked(request):
        pass

    @routes.get("/manny/api/video/probe")
    async def vprobe(request):
        full = _src(dict(request.query))
        return web.json_response(await asyncio.to_thread(probe, full))

    @routes.post("/manny/api/video/fix")
    async def vfix(request):
        """{filename, subfolder, start, end, mode: picture|face, prompt, denoise, seed  (LTX-2.5; the sound is always kept)}"""
        b = await request.json()
        src = _src(b)
        p = await asyncio.to_thread(probe, src)
        start = max(0.0, float(b.get("start", 0)))
        end = min(p["duration"], float(b.get("end", start + 2)))
        if end - start < 0.2:
            raise web.HTTPBadRequest(text="Mark at least a fraction of a second")
        mode = "face" if b.get("mode") == "face" else "picture"
        target = b.get("target")  # fix
        seed = int(b.get("seed") or int(time.time()) % 2 ** 31)
        prompt = (b.get("prompt") or "").strip()
        job = ns["_new_job"](False, kind="videofix")

        async def run():
            size = (p["width"], p["height"])
            if mode == "face":
                pad = 0.25
                a, z = max(0.0, start - pad), min(p["duration"], end + pad)
                api, n = face_api(src, a, z - a, prompt or "a person, natural detailed face, consistent identity",
                                  float(b.get("denoise") or 0.45), seed, size)
                out = await _render(job, api, "Fix a face", "Fixing the face", target)
                job["stage"] = "Putting it back in the clip"
                dest = _dest("Fixed", src, "face fix")
                await asyncio.to_thread(splice, src, out, a, 0.0, min(n / 24.0, z - a), dest)
            else:
                ctx = 1.0                       # a second either side is re-drawn too, so the fix blends into what's around it
                c0 = max(0.0, start - ctx)
                c1 = min(p["duration"], end + ctx)
                api, n = retake_api(src, c0, c1 - c0, prompt, float(b.get("denoise") or 0.7), seed, size)
                out = await _render(job, api, "Fix a part", "Re-drawing the marked part", target)
                job["stage"] = "Putting it back in the clip"
                dest = _dest("Fixed", src, "fix")
                # the whole re-drawn stretch goes in: its first and last frames are the original's, so the joins don't jump
                await asyncio.to_thread(splice, src, out, c0, 0.0, min(n / 24.0, p["duration"] - c0), dest)
            try:
                os.remove(out)
            except Exception:
                pass
            return _result(dest)
        asyncio.ensure_future(ns["_ai_job"](job, run))
        return web.json_response({"id": job["id"], "seed": seed})

    @routes.post("/manny/api/video/extend")
    async def vextend(request):
        """{filename, subfolder, engine: ltx|h3, seconds: 5|7 (ltx) or 5|7|9|11 (h3), prompt, ref (h3 only), seed}"""
        b = await request.json()
        src = _src(b)
        p = await asyncio.to_thread(probe, src)
        seed = int(b.get("seed") or int(time.time()) % 2 ** 31)
        prompt = (b.get("prompt") or "").strip()
        if not prompt:
            raise web.HTTPBadRequest(text="Say what happens next")
        engine = "h3" if b.get("engine") == "h3" else "ltx"
        secs = int(b.get("seconds") or 5)
        target = b.get("target")  # extend
        job = ns["_new_job"](False, kind="videoextend")

        async def run():
            if engine == "h3":
                w, h = h3_size(p["width"], p["height"])
                api = extend_api(src, prompt, secs, b.get("ref") or None, seed, w, h, p["duration"])
                out = await _render(job, api, "Continue a shot (H3)", "Continuing the shot with H3", target)
                dest = _dest("Extended", src, "continued H3")
                os.replace(out, dest)
            else:
                W, Hh = p["width"] // 32 * 32, p["height"] // 32 * 32
                if W * Hh > 900_000:                      # big sources: work at ~0.8 MP, joined back at full size
                    f = (0.8e6 / (W * Hh)) ** 0.5
                    W, Hh = int(W * f) // 32 * 32, int(Hh * f) // 32 * 32
                api, C = ltx_extend_api(src, prompt, min(7, max(3, secs)), seed, W, Hh, p["duration"])
                out = await _render(job, api, "Continue a shot (LTX)", "Continuing the shot with LTX", target)
                job["stage"] = "Joining it on"
                dest = _dest("Extended", src, "continued")
                await asyncio.to_thread(join_after, src, out, C / 24.0, dest)
                try:
                    os.remove(out)
                except Exception:
                    pass
            return _result(dest)
        asyncio.ensure_future(ns["_ai_job"](job, run))
        return web.json_response({"id": job["id"], "seed": seed})

    @routes.post("/manny/api/video/keyframes")
    async def vkeys(request):
        """{keys: [{image: input value, at: seconds}], prompt, negative, seconds, width, height, seed}"""
        b = await request.json()
        keys = sorted([(k["image"], float(k.get("at") or 0)) for k in (b.get("keys") or []) if k.get("image")], key=lambda x: x[1])
        if len(keys) < 2:
            raise web.HTTPBadRequest(text="Add at least two pictures")
        seconds = max(2.0, min(20.0, float(b.get("seconds") or max(5.0, keys[-1][1]))))
        keys = [(img, min(seconds, t)) for img, t in keys]
        keys[0] = (keys[0][0], 0.0)
        prompt = (b.get("prompt") or "").strip()
        if not prompt:
            raise web.HTTPBadRequest(text="Describe the shot")
        seed = int(b.get("seed") or int(time.time()) % 2 ** 31)
        W, Hh = int(b.get("width") or 1280), int(b.get("height") or 704)
        target = b.get("target")  # keyframes
        job = ns["_new_job"](False, kind="keyframes")

        async def run():
            api = ltx_keyframes_api(keys[:8], prompt, b.get("negative"), W // 32 * 32, Hh // 32 * 32, seconds, seed)
            out = await _render(job, api, "Keyframe shot", "Rendering the shot", target)
            return _result(out)
        asyncio.ensure_future(ns["_ai_job"](job, run))
        return web.json_response({"id": job["id"], "seed": seed})
