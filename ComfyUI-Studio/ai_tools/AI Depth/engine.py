#!/usr/bin/env python3
"""AI Depth: how far away everything is, frame by frame (Depth Anything 3 on this PC's GPU, through ComfyUI's DA3 nodes),
as a depth pass or used at once for a depth-of-field blur or fog.

    engine.py <video or picture> --output depth|color|dof|fog [--focus 0..1] [--blur 0.2..3] [--density 0..1]
              [--detail 504|756|1008] --out <file> --progress <json>

A video goes to ComfyUI in parts of CHUNK frames; the parts' depth maps are joined at the source's size and frame rate.
The depth pass is written with white near and black far. Which way round the model draws it is checked on the result
(the bottom of a frame is nearly always nearer than its top) and turned round when it's the other way, so the effects
below can rely on it:
- depth of field: each pixel is blurred by how far its depth is from the focus (a sharp band around it);
- fog: a pale haze mixed in by distance, up to the density."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import studio_engine as se  # noqa: E402

CHUNK = 96
FOG = "0xC9D1D9"


def graph(comfy, name, still, fps, model, detail, colored):
    api = {}
    if still:
        api["li"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        images = ["li", 0]
    else:
        images, _fps = se.load_video(api, name)
    api["dm"] = {"class_type": "LoadDA3Model", "inputs": {"model_name": model, "weight_dtype": "default"}}
    api["di"] = {"class_type": "DA3Inference", "inputs": {"da3_model": ["dm", 0], "image": images, "resolution": int(detail),
                                                          "resize_method": "upper_bound_resize", "mode": "mono"}}
    api["dr"] = {"class_type": "DA3Render", "inputs": {"da3_geometry": ["di", 0], "output": "depth_colored" if colored else "depth",
                                                       "output.normalization": "v2_style", "output.apply_sky_clip": False}}
    if still:
        api["si"] = {"class_type": "SaveImage", "inputs": {"images": ["dr", 0], "filename_prefix": comfy.prefix("depth")}}
    else:
        se.save_video(api, ["dr", 0], max(1.0, min(120.0, fps)), comfy.prefix("depth"))
    return api


def effect_filter(kind, W, H, fps, flip, focus, blur, density, seconds, still):
    """The ffmpeg filter graph that uses the depth ([1:v], white near once `flip` is applied) on the picture ([0:v]):
    - dof: blur by how far each pixel's depth is from the focus (0 near .. 1 far), sharp in a band around it (±20 of
      255 levels), full blur 100 levels away;
    - fog: a pale haze mixed in by distance, up to `density` at the farthest.
    Every plane is mixed by the same mask (gbrp), so colours stay true. Ends in [v]."""
    grey = f"[1:v]scale={W}:{H}:flags=bicubic,format=gray{',negate' if flip else ''}"
    if kind == "dof":
        at = 255.0 * (1.0 - max(0.0, min(1.0, focus)))          # white is near: focus 0 is the nearest
        sigma = max(0.5, blur * W / 240.0)
        # sharp within 20 levels (of 255) of the focus, then blurring more and more, fully past 100 levels away
        fc = (f"{grey},lut=c0='clip((abs(val-{at:.1f})-20)*3.2,0,255)',format=gbrp[m];"
              f"[0:v]scale={W}:{H},setsar=1,format=gbrp,split[a][b];[b]gblur=sigma={sigma:.2f}[bl];[a][bl][m]maskedmerge[x]")
    else:
        dens = max(0.0, min(1.0, density))
        fc = (f"{grey},lut=c0='clip((255-val)*{dens:.3f},0,255)',format=gbrp[m];"
              f"color=c={FOG}:s={W}x{H}:r={fps:.6f}:d={seconds:.3f},format=gbrp[f];"
              f"[0:v]scale={W}:{H},setsar=1,format=gbrp[a];[a][f][m]maskedmerge[x]")
    return fc + (";[x]format=rgb24[v]" if still else ";[x]format=yuv420p[v]")


def near_is_dark(path, samples=6):
    """Whether a depth map draws near things dark: the bottom quarter of a frame is nearly always nearer than the top
    quarter. Looked at on a few frames; a flat map (nothing to tell) is left as it is."""
    import av
    import numpy as np
    diffs = []
    with av.open(path) as c:
        v = c.streams.video[0]
        n = max(1, se.count_frames(path))
        every = max(1, n // samples)
        for i, fr in enumerate(c.decode(v)):
            if i % every:
                continue
            g = fr.to_ndarray(format="gray").astype("float32")
            q = max(1, g.shape[0] // 4)
            diffs.append(float(g[-q:].mean() - g[:q].mean()))
            if len(diffs) >= samples:
                break
    if not diffs:
        return False
    d = float(np.median(diffs))
    return d < -4.0


def main():
    ap = se.parser("AI Depth: depth maps, depth of field and fog (Depth Anything 3, through ComfyUI)")
    ap.add_argument("--output", default="depth", choices=["depth", "color", "dof", "fog"])
    ap.add_argument("--focus", type=float, default=0.15)
    ap.add_argument("--blur", type=float, default=1.0)
    ap.add_argument("--density", type=float, default=0.6)
    ap.add_argument("--detail", type=int, default=504)
    a = ap.parse_args()
    prog = se.Progress(a.progress)
    with se.engine(prog):
        run(a, prog)


def run(a, prog):
    model = se.model()
    info = se.probe(a.input)
    if info["kind"] not in ("video", "image"):
        raise se.EngineError("AI Depth needs a video or a picture")
    still = info["kind"] == "image"
    fps = info["fps"] or 24.0
    W, H = max(2, info["width"] // 2 * 2), max(2, info["height"] // 2 * 2)
    detail = max(140, min(2520, int(a.detail) // 14 * 14))
    colored = a.output == "color"
    comfy = se.Comfy(prog)
    with se.workdir("depth") as wd:
        try:
            if still:
                prog(stage="Finding the depth", done=0, total=2)
                src = os.path.join(wd, "still.png")
                se.ff(["-i", a.input, "-frames:v", "1", "-vf", f"scale={W}:{H}", src], what="Reading the picture")
                files = comfy.run(graph(comfy, comfy.put(src), True, fps, model, detail, colored), "Finding the depth")
                pic = next((f for f in files if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))), None)
                if not pic:
                    raise se.EngineError("The depth came back without a picture")
                depth = os.path.join(wd, "depth.png")
                se.ff(["-i", pic, "-frames:v", "1", "-vf", f"scale={W}:{H}:flags=bicubic", depth], what="Sizing the depth map")
                prog(done=1)
            else:
                n = info["frames"] or se.count_frames(a.input)
                parts = _tile(n)
                prog(stage="Finding the depth", done=0, total=len(parts) + 1)
                made = []
                for k, (s, e) in enumerate(parts):
                    src = se.cut_frames(a.input, s, e, fps, os.path.join(wd, f"in{k:05d}.mp4"))
                    files = comfy.run(graph(comfy, comfy.put(src), False, fps, model, detail, colored),
                                      f"Finding the depth (part {k + 1} of {len(parts)})" if len(parts) > 1 else "Finding the depth",
                                      f"part {k + 1}/{len(parts)}" if len(parts) > 1 else "")
                    vid = next((f for f in files if f.lower().endswith((".mp4", ".webm", ".mov", ".mkv"))), None)
                    if not vid:
                        raise se.EngineError("The depth came back without a video")
                    part = os.path.join(wd, f"depth{k:05d}.mp4")
                    # exactly the part's frames at the source's size and rate, whatever ComfyUI's file holds
                    se.ff(["-i", vid, "-vf", f"tpad=stop_mode=clone:stop={e - s},setpts=N/({fps:.6f}*TB),scale={W}:{H}:flags=bicubic,"
                           "setsar=1,format=yuv420p", "-frames:v", str(e - s), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "10",
                           "-r", f"{fps:.6f}", part], what="Sizing the depth map")
                    made.append(part)
                    prog(done=k + 1)
                depth = se.concat_copy(made, os.path.join(wd, "depth.mp4"))
            prog(stage="Applying it" if a.output in ("dof", "fog") else "Writing the depth map")
            flip = not colored and near_is_dark(depth)
            out = a.out
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            still_out = ["-frames:v", "1"] if still else ["-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
                                                          "-r", f"{fps:.6f}", "-movflags", "+faststart"]
            if a.output in ("depth", "color"):
                vf = f"scale={W}:{H}:flags=bicubic" + ("" if colored else ",format=gray" + (",negate" if flip else ""))
                vf += ",format=rgb24" if still else ",format=yuv420p"
                se.ff(["-i", depth, "-vf", vf, "-an"] + still_out + [out], what="Writing the depth map")
                what = "A colour depth map" if colored else "A depth map, white near and black far"
            else:
                audio = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "256k"] if info["has_audio"] and not still else []
                fc = effect_filter(a.output, W, H, fps, flip, a.focus, a.blur, a.density, 1.0 if still else info["duration"] + 1.0, still)
                se.ff(["-i", a.input, "-i", depth, "-filter_complex", fc, "-map", "[v]"] + audio + still_out + [out],
                      what="Applying the depth")
                what = (f"Depth of field, focused at {round(a.focus * 100)}% of the way from near to far" if a.output == "dof"
                        else f"Fog by distance, {round(max(0.0, min(1.0, a.density)) * 100)}% at the farthest")
            note = what + (" (the model drew it the other way round: turned)" if flip else "") + "."
            prog.finish([out], note=note)
        finally:
            comfy.cleanup()


def _tile(n):
    """Frames 0..n-1 in parts of at most CHUNK, end to end (depth needs no shared frames)."""
    return [(s, min(n, s + CHUNK)) for s in range(0, n, CHUNK)]


if __name__ == "__main__":
    main()
