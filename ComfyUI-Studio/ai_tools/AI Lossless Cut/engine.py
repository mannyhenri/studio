#!/usr/bin/env python3
"""AI Lossless Cut: pieces of a video or sound file cut out without re-encoding (ffmpeg stream copy).

    engine.py <file> --segments 12.000000-30.500000,41.0-55.0 [--mode keyframe|smart] [--all-tracks] [--join]
              --out <folder> --progress <json>

A picture can only start on a keyframe when nothing is re-encoded:
- keyframe cut: each piece starts on the keyframe at or before its In (so it may start a little early; the piece says
  where it really starts) and ends at its Out;
- smart cut (H.264, HEVC): the frames from the In up to the next keyframe are re-encoded (near-lossless, the same codec
  and pixel format), the rest is copied, so the piece starts exactly on its In. The sound is copied from the In.
A sound file cuts anywhere. Each piece keeps the original's container; --join also joins them into one file.
Every piece is reported (progress "pieces"): where it was asked to start and end, where it really does, how it was made
and what, if anything, was re-encoded."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import studio_engine as se  # noqa: E402

SMART = {"h264": ("libx264", "h264_mp4toannexb"), "hevc": ("libx265", "hevc_mp4toannexb")}
TS_EXT = {".mts", ".m2ts", ".ts"}
EPS = 0.0005          # a seek lands on the keyframe at or before the time asked: aim just past it


def parse_segments(text):
    out = []
    for part in str(text or "").split(","):
        if not part.strip():
            continue
        try:
            s, e = part.split("-", 1)
            out.append((float(s), float(e)))
        except ValueError:
            raise se.EngineError(f"a piece must be start-end in seconds, not {part!r}")
    if not out:
        raise se.EngineError("no pieces to cut")
    return out


def key_at_or_before(kf, t):
    return max([k for k in kf if k <= t + 1e-3], default=0.0)


def key_at_or_after(kf, t):
    return min([k for k in kf if k >= t - 1e-3], default=None)


def maps(info, all_tracks):
    if info["kind"] == "audio":
        return ["-map", "0:a"] if all_tracks else ["-map", "0:a:0"]
    return ["-map", "0", "-ignore_unknown"] if all_tracks else ["-map", "0:v:0", "-map", "0:a:0?"]


def container_opts(ext):
    return ["-movflags", "+faststart"] if ext in (".mp4", ".mov", ".m4v", ".m4a") else []


def copy_cut(src, start, dur, dest, info, all_tracks):
    """Stream copy from `start` (a keyframe, or anywhere in a sound file) for `dur` seconds."""
    ext = os.path.splitext(dest)[1].lower()
    base = ["-ss", f"{start + (EPS if info['kind'] == 'video' else 0):.6f}", "-i", src, "-t", f"{dur:.6f}"]
    tail = ["-c", "copy", "-avoid_negative_ts", "make_zero", "-map_metadata", "0"] + container_opts(ext) + [dest]
    try:
        se.ff(base + maps(info, all_tracks) + tail, what="Cutting")
    except se.EngineError:
        if not all_tracks:
            raise
        # a track this container can't hold (a data track in an MP4): the picture and every sound track
        se.ff(base + ["-map", "0:v?", "-map", "0:a?"] + tail, what="Cutting")


def smart_cut(src, s, e, kf, dest, info, all_tracks, wd, n):
    """Frames from s to the next keyframe re-encoded, the rest copied; the sound copied from s. Returns the re-encoded span."""
    enc, bsf = SMART[info["vcodec"]]
    k = key_at_or_after(kf, s)
    head_end = min(k, e) if k is not None else e
    parts = []
    head = os.path.join(wd, f"head{n}.ts")
    # near-lossless, and no B-frames: the few re-encoded frames then join the copied ones with simple timestamps
    quality = (["-crf", "12", "-preset", "medium", "-bf", "0"] if enc == "libx264"
               else ["-crf", "14", "-preset", "medium", "-x265-params", "log-level=error:bframes=0"])
    pix = ["-pix_fmt", info["pix_fmt"]] if info.get("pix_fmt") else []
    se.ff(["-ss", f"{s:.6f}", "-i", src, "-t", f"{head_end - s:.6f}", "-map", "0:v:0", "-an", "-sn", "-dn", "-c:v", enc] + quality + pix +
          ["-r", f"{info['fps']:.6f}", "-bsf:v", bsf, "-f", "mpegts", head], what="Re-encoding up to the keyframe")
    parts.append(head)
    if k is not None and k < e - 1e-3:
        tail = os.path.join(wd, f"tail{n}.ts")
        se.ff(["-ss", f"{k + EPS:.6f}", "-i", src, "-t", f"{e - k:.6f}", "-map", "0:v:0", "-an", "-sn", "-dn", "-c", "copy",
               "-bsf:v", bsf, "-f", "mpegts", tail], what="Copying from the keyframe")
        parts.append(tail)
    video = se.concat_copy(parts, os.path.join(wd, f"video{n}.mp4"), what="Joining the re-encoded start and the copy")
    cmd = ["-i", video]
    if info["has_audio"]:
        # seeking the input would land on the picture's keyframe before s (the demuxer seeks by the video) and the copied
        # sound would start early, out of sync: the sound is read from the start and its packets before s dropped
        sound = os.path.join(wd, f"sound{n}.mka")
        se.ff(["-i", src, "-ss", f"{s:.6f}", "-t", f"{e - s:.6f}", "-vn", "-sn", "-dn", "-map", "0:a" if all_tracks else "0:a:0",
               "-c", "copy", sound], what="Copying the sound")
        cmd += ["-i", sound, "-map", "0:v:0", "-map", "1:a"]
    else:
        cmd += ["-map", "0:v:0"]
    se.ff(cmd + ["-c", "copy", "-map_metadata", "-1"] + container_opts(os.path.splitext(dest)[1].lower()) + [dest], what="Putting the piece together")
    return [s, head_end]


def joins_cleanly(path):
    """Whether a joined file's picture timestamps only ever go forward: read packet by packet (no decoding), as a muxer
    would take them."""
    import subprocess
    r = subprocess.run([se.ffmpeg(), "-nostdin", "-hide_banner", "-v", "warning", "-i", path, "-map", "0:v:0", "-c", "copy", "-f", "null", "-"],
                       stdin=subprocess.DEVNULL, capture_output=True, creationflags=se.NO_WINDOW)
    return r.returncode == 0 and b"non monotonically increasing dts" not in r.stderr


def main():
    ap = se.parser("AI Lossless Cut: pieces cut out without re-encoding")
    ap.add_argument("--segments", required=True)
    ap.add_argument("--mode", default="keyframe", choices=["keyframe", "smart"])
    ap.add_argument("--all-tracks", action="store_true")
    ap.add_argument("--join", action="store_true")
    a = ap.parse_args()
    prog = se.Progress(a.progress)
    with se.engine(prog):
        run(a, prog)


def run(a, prog):
    info = se.probe(a.input)
    if info["kind"] not in ("video", "audio"):
        raise se.EngineError("AI Lossless Cut needs a video or a sound file")
    segs = parse_segments(a.segments)
    dur = info["duration"]
    ext = os.path.splitext(a.input)[1].lower() or ".mp4"
    base = os.path.splitext(os.path.basename(a.input))[0]
    os.makedirs(a.out, exist_ok=True)
    video = info["kind"] == "video"
    kf = se.keyframes(a.input) if video else []
    smart = a.mode == "smart" and video
    smart_ok = smart and info["vcodec"] in SMART
    total = len(segs) + (1 if a.join else 0)
    prog(stage="Reading the keyframes" if video else "Cutting", done=0, total=total)
    outs, pieces = [], []
    with se.workdir("cut") as wd:
        for i, (s, e) in enumerate(segs):
            e = min(e, dur) if dur else e
            if e <= s:
                raise se.EngineError(f"piece {i + 1} starts after the end of the file")
            prog(stage=f"Cutting piece {i + 1} of {len(segs)}", done=i)
            dest = os.path.join(a.out, f"{base} - piece {i + 1:02d}{ext}")
            piece = {"n": i + 1, "file": dest, "asked_start": round(s, 6), "asked_end": round(e, 6), "end": round(e, 6), "reencoded": [], "note": ""}
            if not video:
                copy_cut(a.input, s, e - s, dest, info, a.all_tracks)
                piece.update(start=round(s, 6), mode="copy")
            elif smart_ok:
                k = key_at_or_after(kf, s)
                if k is not None and abs(k - s) < 0.5 / (info["fps"] or 24):      # the In is on a keyframe: a plain copy is exact
                    copy_cut(a.input, k, e - k, dest, info, a.all_tracks)
                    piece.update(start=round(k, 6), mode="copy")
                else:
                    try:
                        span = smart_cut(a.input, s, e, kf, dest if ext not in TS_EXT else dest[: -len(ext)] + ".mp4", info, a.all_tracks, wd, i)
                        if ext in TS_EXT:
                            dest = piece["file"] = dest[: -len(ext)] + ".mp4"
                        piece.update(start=round(s, 6), mode="smart", reencoded=[[round(x, 6) for x in span]])
                    except se.EngineError as err:
                        # the encoder couldn't match the source (a 10-bit or 4:2:2 stream, say): a keyframe cut instead
                        ks = key_at_or_before(kf, s)
                        copy_cut(a.input, ks, e - ks, dest, info, a.all_tracks)
                        piece.update(start=round(ks, 6), mode="copy", note=f"Smart cut wasn't possible ({str(err)[:160]}): cut on the keyframe")
            else:
                ks = key_at_or_before(kf, s)
                copy_cut(a.input, ks, e - ks, dest, info, a.all_tracks)
                piece.update(start=round(ks, 6), mode="copy")
                if smart:
                    piece["note"] = f"Smart cut needs H.264 or HEVC (this is {info['vcodec']}): cut on the keyframe"
            # where the piece really ends: a copied picture can't stop mid-group either, so it's measured, not assumed
            try:
                length = se.count_frames(dest) / info["fps"] if video and info["fps"] else se.probe(dest)["duration"]
                piece["end"] = round(piece["start"] + length, 6)
            except Exception:
                pass
            outs.append(dest)
            pieces.append(piece)
            prog(done=i + 1, outs=outs, pieces=pieces)
        note = f"{len(outs)} piece{'s' if len(outs) != 1 else ''}"
        early = [p for p in pieces if p["start"] < p["asked_start"] - 0.0015]
        if early:
            note += f"; {len(early)} start{'s' if len(early) == 1 else ''} on the keyframe before the In"
        if a.join and len(outs) > 0:
            prog(stage="Joining the pieces", done=len(segs))
            joined = os.path.join(a.out, f"{base} - joined{os.path.splitext(outs[0])[1]}")
            try:
                se.concat_copy(outs, joined, what="Joining the pieces")
                outs.append(joined)
                note += ", and the pieces joined into one file"
                if video and not joins_cleanly(joined):
                    note += (" (nothing re-encoded, so a frame or two can stutter at a join: the frames a copied piece holds "
                             "past its Out overlap the next piece)")
            except se.EngineError as err:
                note += f"; they couldn't be joined ({str(err)[:160]})"
    prog.finish(outs, pieces=pieces, note=note + ".")


if __name__ == "__main__":
    main()
