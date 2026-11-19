#!/usr/bin/env python3
"""AI Slow-Mo: new frames between the real ones, made by a frame interpolation model (FILM) on this PC's GPU through
ComfyUI's FrameInterpolate node.

    engine.py <video> [--start F --end F] --factor 2|4|8 [--hfr] [--scene 0.3] [--compare] --out <file.mp4> --progress <json>

The section (frames F..end-1, the whole clip without one) is cut out, its scene cuts are found (ffmpeg's scene score),
and each shot is interpolated on its own, so no frame is ever blended across a cut. A long shot goes to ComfyUI in
parts that share their edge frame, so nothing is lost between them. Every source frame then lasts `factor` frames:
played at the source's frame rate that is slow motion (silent), with --hfr it plays at factor × the frame rate, at the
same speed, with the section's own sound."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import studio_engine as se  # noqa: E402

CHUNK = 120          # source frames per ComfyUI job: memory stays low on a long section


def graph(comfy, name, factor, fps, model):
    api = {}
    images, _fps = se.load_video(api, name)
    api["im"] = {"class_type": "FrameInterpolationModelLoader", "inputs": {"model_name": model}}
    api["fi"] = {"class_type": "FrameInterpolate", "inputs": {"interp_model": ["im", 0], "images": images, "multiplier": int(factor)}}
    se.save_video(api, ["fi", 0], max(1.0, min(120.0, fps)), comfy.prefix("slowmo"))   # the timing is set afterwards
    return api


def plan(n, cuts, size=CHUNK):
    """The ComfyUI jobs for n frames with scene cuts: [((run_first, run_end), (first, end)), ...]."""
    jobs = []
    for ra, rb in se.runs_between(n, cuts):
        for ca, cb in (se.chunks(ra, rb, size) if rb - ra > 1 else [(ra, rb)]):
            jobs.append(((ra, rb), (ca, cb)))
    return jobs


def main():
    ap = se.parser("AI Slow-Mo: slow motion or smoother motion by frame interpolation (FILM, through ComfyUI)")
    ap.add_argument("--start", type=int)
    ap.add_argument("--end", type=int)
    ap.add_argument("--factor", type=int, default=4, choices=[2, 3, 4, 6, 8, 16])
    ap.add_argument("--hfr", action="store_true", help="the same speed at factor × the frame rate, with the sound")
    ap.add_argument("--scene", type=float, default=0.3, help="scene-cut threshold, 0..1 (lower finds more cuts)")
    ap.add_argument("--compare", action="store_true", help="the original beside the result")
    a = ap.parse_args()
    prog = se.Progress(a.progress)
    with se.engine(prog):
        run(a, prog)


def run(a, prog):
    model = se.model()
    info = se.probe(a.input)
    if info["kind"] != "video":
        raise se.EngineError("AI Slow-Mo needs a video")
    fps, total_frames = info["fps"] or 24.0, info["frames"]
    first = max(0, a.start or 0)
    end = min(total_frames, a.end) if a.end is not None else total_frames
    if end - first < 2:
        raise se.EngineError("Mark at least two frames: slow motion is made between frames")
    factor = int(a.factor)
    out_fps = fps * factor if a.hfr else fps
    W, H = info["width"] // 2 * 2, info["height"] // 2 * 2
    comfy = se.Comfy(prog)
    with se.workdir("slowmo") as wd:
        try:
            prog(stage="Reading the section", done=0, total=3)
            section = se.cut_frames(a.input, first, end, fps, os.path.join(wd, "section.mp4"))
            n = se.count_frames(section)
            prog(stage="Finding the scene cuts")
            cuts = se.scene_cuts(section, a.scene, fps)
            jobs = plan(n, cuts)
            total = len(jobs) + 2
            prog(stage="Making the new frames", done=1, total=total)
            parts = []
            for k, ((_ra, rb), (ca, cb)) in enumerate(jobs):
                part = os.path.join(wd, f"part{k:05d}.mp4")
                enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-r", f"{out_fps:.6f}", part]
                tail = f"setpts=N/({out_fps:.6f}*TB),scale={W}:{H}:flags=lanczos,setsar=1,format=yuv420p"
                if cb - ca < 2:
                    # a one-frame shot: nothing to put between, it's held for its share of the time
                    se.ff(["-i", section, "-vf", f"trim=start_frame={ca}:end_frame={cb},tpad=stop_mode=clone:stop={factor - 1},{tail}"] + enc,
                          what="Holding a one-frame shot")
                else:
                    src = se.cut_frames(section, ca, cb, fps, os.path.join(wd, f"in{k:05d}.mp4"))
                    files = comfy.run(graph(comfy, comfy.put(src), factor, fps, model),
                                      f"Making the new frames (part {k + 1} of {len(jobs)})" if len(jobs) > 1 else "Making the new frames",
                                      f"part {k + 1}/{len(jobs)}" if len(jobs) > 1 else "")
                    vid = next((f for f in files if f.lower().endswith((".mp4", ".webm", ".mov", ".mkv"))), None)
                    if not vid:
                        raise se.EngineError("The interpolation came back without a video")
                    got = se.count_frames(vid)
                    # parts of a shot share their edge frame: each but the shot's last drops it; the shot's last part
                    # holds its last frame, so every source frame lasts `factor` frames
                    edge = f"tpad=stop_mode=clone:stop={factor - 1}" if cb == rb else f"trim=end_frame={max(1, got - 1)}"
                    se.ff(["-i", vid, "-vf", f"{edge},{tail}"] + enc, what="Timing the new frames")
                parts.append(part)
                prog(done=k + 2)
            prog(stage="Putting it together", done=total - 1)
            joined = se.concat_copy(parts, os.path.join(wd, "joined.mp4"))
            out = a.out if a.out.lower().endswith(".mp4") else a.out + ".mp4"
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            t0, dur = first / fps, (end - first) / fps
            cmd = ["-i", joined]
            if a.compare or (a.hfr and info["has_audio"]):
                cmd += ["-ss", f"{t0:.6f}", "-t", f"{dur:.6f}", "-i", a.input]
            if a.compare:
                # the original beside it, each of its frames held as long as the result's frames take
                orig = f"[1:v]setpts={factor}*PTS," if not a.hfr else "[1:v]"
                fc = (f"{orig}fps={out_fps:.6f},scale={W}:{H}:flags=lanczos,setsar=1,format=yuv420p[o];"
                      f"[0:v]setsar=1,format=yuv420p[r];[o][r]hstack=inputs=2:shortest=1[v]")
                cmd += ["-filter_complex", fc, "-map", "[v]"]
            else:
                cmd += ["-map", "0:v:0"]
            if a.hfr and info["has_audio"]:
                cmd += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "256k", "-shortest"]
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", "-r", f"{out_fps:.6f}",
                    "-movflags", "+faststart", out]
            se.ff(cmd, what="Writing the result")
            made = se.probe(out)
            what = (f"{factor}× the frame rate ({out_fps:g} fps, the same speed)" if a.hfr else f"{factor}× slow motion")
            where = "the whole clip" if first == 0 and end == total_frames else f"frames {first}–{end - 1}"
            shots = f"; {len(cuts)} scene cut{'s' if len(cuts) != 1 else ''} kept sharp" if cuts else ""
            note = f"{what} of {where}: {made['frames'] or '?'} frames, {made['duration']:.2f} s{shots}."
            if not a.hfr:
                note += " Slow motion is silent: the sound stays with the original."
            prog.finish([out], note=note)
        finally:
            comfy.cleanup()


if __name__ == "__main__":
    main()
