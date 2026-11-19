#!/usr/bin/env python3
"""One-Click Master: a mix made ready to publish, on this PC, with ffmpeg.

    engine.py <sound or video> --target -14 [--ceiling -1] [--character transparent|warm|bright|punchy]
              --out <file> --progress <json>

1. Measure: the sound, cleaned of low rumble (a 25 Hz high-pass) and toned as asked, is measured the EBU R128 /
   ITU-R BS.1770 way (ffmpeg's ebur128: integrated loudness, loudness range, true peak).
2. Level: one gain takes it to the target; a look-ahead limiter run at 4× the sample rate (192 kHz, where the peaks
   between samples show) holds the true peak under the ceiling. The result is measured again and the gain and the
   limiter's margin corrected until the loudness is within 0.3 LU of the target and the true peak under the ceiling
   (loudnorm's own limiter let inter-sample peaks through when it resampled: measured, not assumed, here).
   When the gain alone keeps the peaks under the ceiling the limiter never acts: the dynamics are untouched.
3. Write it, and measure the written file: its numbers go on the job.
A sound file becomes a 24-bit WAV; a video keeps its picture as it is (copied) with the mastered sound."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import studio_engine as se  # noqa: E402

TONE = {
    "transparent": "",
    "warm": "bass=g=2:f=110:w=0.7,treble=g=-1.5:f=7500",
    "bright": "bass=g=-1:f=90,treble=g=2:f=9000",
    "punchy": "acompressor=threshold=0.125:ratio=3:attack=15:release=150:makeup=1.5,bass=g=1.5:f=90",
}
MP4_VIDEO = {"h264", "hevc", "av1", "mpeg4", "vp9"}
TOLERANCE = 0.3       # LU from the target
PASSES = 8


def ebur128_summary(log):
    """Integrated loudness, loudness range and true peak from ebur128's summary."""
    summary = log[log.rfind("Summary:"):] if "Summary:" in log else log

    def last(pattern):
        m = re.findall(pattern, summary)
        if not m:
            return None
        v = m[-1]
        return None if "inf" in v else float(v)
    return {"integrated_lufs": last(r"I:\s+(-?[\d.]+|-?inf) LUFS"), "loudness_range_lu": last(r"LRA:\s+(-?[\d.]+) LU\b"),
            "true_peak_dbtp": last(r"Peak:\s+(-?[\d.]+|-?inf) dBFS")}


def pre_chain(character):
    tone = TONE.get(character) or ""
    return "highpass=f=25:p=2" + ("," + tone if tone else "")


def level_chain(pre, gain_db, limit_db, rate):
    """The mastering chain: the clean-up and tone, the gain, then a true-peak limiter (at 4× the rate) at limit_db."""
    lim = 10 ** (limit_db / 20.0)
    return (f"{pre},volume={gain_db:.3f}dB,aresample=192000,alimiter=limit={max(0.0625, min(1.0, lim)):.5f}:attack=1:release=60:"
            f"level=false:latency=true,aresample={rate}")


def measure(src, af, what):
    got = ebur128_summary(se.ff(["-i", src, "-map", "0:a:0", "-vn", "-af", f"{af},ebur128=peak=true:framelog=verbose", "-f", "null", "-"],
                                what=what, loglevel="info"))
    if got["integrated_lufs"] is None:
        raise se.EngineError("The sound is silent: there's nothing to master")
    return got


def plan(src, pre, target, ceiling, rate, prog):
    """The gain and the limiter's level that land on the target with the true peak under the ceiling, found by
    measuring (each pass is one read of the sound, no file written). Returns (gain, limit, measured, first)."""
    first = measure(src, pre, "Measuring the mix")
    gain, limit = target - first["integrated_lufs"], ceiling - 0.2
    got, prev = first, None
    for k in range(PASSES):
        prog(stage=f"Setting the level (pass {k + 1})")
        got = measure(src, level_chain(pre, gain, limit, rate), "Setting the level")
        off_i = target - got["integrated_lufs"]
        over = (got["true_peak_dbtp"] if got["true_peak_dbtp"] is not None else -99) - ceiling
        if abs(off_i) <= TOLERANCE and over <= 0.0:
            break
        if over > 0:
            limit -= over + 0.05            # peaks between samples came back after the rate came down: more margin
            prev = None                     # a new limiter level: the loudness per dB changes too
            continue
        # the limiter eats part of every dB added when it's working hard: step by the loudness a dB bought last time
        slope = 1.0
        if prev and abs(gain - prev[0]) > 0.05:
            slope = max(0.15, min(1.0, (got["integrated_lufs"] - prev[1]) / (gain - prev[0])))
        prev = (gain, got["integrated_lufs"])
        gain += max(-12.0, min(12.0, off_i / slope))
    return gain, limit, got, first


def main():
    ap = se.parser("One-Click Master: loudness to a target, true peak under a ceiling")
    ap.add_argument("--target", type=float, default=-14.0)
    ap.add_argument("--ceiling", type=float, default=-1.0)
    ap.add_argument("--character", default="transparent", choices=list(TONE))
    a = ap.parse_args()
    prog = se.Progress(a.progress)
    with se.engine(prog):
        run(a, prog)


def run(a, prog):
    info = se.probe(a.input)
    if not info["has_audio"]:
        raise se.EngineError(f"{os.path.basename(a.input)} has no sound")
    target = max(-40.0, min(-5.0, a.target))
    ceiling = max(-9.0, min(0.0, a.ceiling))
    pre = pre_chain(a.character)
    rate = info["sample_rate"] if info["sample_rate"] >= 44100 else 48000
    prog(stage="Measuring the mix", done=0, total=4)
    gain, limit, planned, first = plan(a.input, pre, target, ceiling, rate, prog)
    out = a.out
    if info["kind"] == "video":
        if info["vcodec"] in MP4_VIDEO:
            out = os.path.splitext(out)[0] + ".mp4"
            enc = ["-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart"]
        else:                       # ProRes, DNxHD…: a .mov keeps the picture as it is, with 24-bit sound
            out = os.path.splitext(out)[0] + ".mov"
            enc = ["-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "pcm_s24le"]
    else:
        out = os.path.splitext(out)[0] + ".wav"
        enc = ["-map", "0:a:0", "-vn", "-c:a", "pcm_s24le"]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    prog(stage="Writing the master", done=2)
    se.ff(["-i", a.input, "-af", level_chain(pre, gain, limit, rate)] + enc + ["-ar", str(rate), out], what="Writing the master",
          duration=info["duration"], on_progress=lambda f: prog(stage=f"Writing the master: {int(f * 100)}%"))
    prog(stage="Checking the master", done=3)
    got = measure(out, "anull", "Checking the master")
    stats = {k: round(v, 2) for k, v in got.items() if v is not None}
    stats["input_lufs"] = round(first["integrated_lufs"], 2)
    if first["true_peak_dbtp"] is not None:
        stats["input_true_peak_dbtp"] = round(first["true_peak_dbtp"], 2)
    stats["gain_db"] = round(gain, 2)
    # the limiter acted when the gain alone would have put the peaks over its level
    limited = first["true_peak_dbtp"] is not None and first["true_peak_dbtp"] + gain > limit
    stats["limiter"] = "on" if limited else "off"
    i, tp = stats.get("integrated_lufs"), stats.get("true_peak_dbtp")
    note = f"Mastered to {i:g} LUFS (target {target:g}) with the true peak at {tp:g} dBTP (ceiling {ceiling:g})"
    note += f"; the mix was {stats['input_lufs']:g} LUFS, moved by {gain:+.1f} dB"
    note += ", and the limiter held the peaks." if limited else ", and no peak needed limiting: the dynamics are untouched."
    prog.finish([out], note=note, stats=stats)


if __name__ == "__main__":
    main()
