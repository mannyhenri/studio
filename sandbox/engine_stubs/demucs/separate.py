"""Sandbox stand-in for `python -m demucs.separate`: the same options the AI Stems engine passes, a quiet tone of the
input's length for each stem (in <out>/<model>/<stem>.<ext>), and Demucs's progress bar on stderr."""
import argparse
import os
import shutil
import subprocess
import sys
import time
import wave

STEMS = {"htdemucs": ["drums", "bass", "other", "vocals"], "htdemucs_ft": ["drums", "bass", "other", "vocals"],
         "htdemucs_6s": ["drums", "bass", "other", "vocals", "guitar", "piano"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("tracks", nargs="+")
    ap.add_argument("-n", "--name", default="htdemucs")
    ap.add_argument("-o", "--out", default="separated")
    ap.add_argument("--filename", default="{track}/{stem}.{ext}")
    ap.add_argument("--two-stems")
    ap.add_argument("--flac", action="store_true")
    ap.add_argument("--int24", action="store_true")
    ap.add_argument("--mp3", action="store_true")
    ap.add_argument("-d", "--device", default="cpu")
    a = ap.parse_args(argv)
    if a.name not in STEMS:
        sys.exit(f"FATAL: model {a.name} is not in the sandbox")
    ff = os.environ.get("STUDIO_FFMPEG") or shutil.which("ffmpeg")
    stems = STEMS[a.name] if not a.two_stems else [a.two_stems, "no_" + a.two_stems]
    ext = "flac" if a.flac else "mp3" if a.mp3 else "wav"
    print(f"Selected model is a bag of {4 if a.name == 'htdemucs_ft' else 1} models. (sandbox stand-in: nothing is separated)")
    for track in a.tracks:
        with wave.open(track) as w:
            seconds = w.getnframes() / float(w.getframerate())
        print(f"Separating track {track}")
        for model in range(4 if a.name == "htdemucs_ft" else 1):
            for pct in (0, 25, 50, 75, 100):
                sys.stderr.write(f"\r{pct:3d}%|{'█' * (pct // 10):10s}| {seconds * pct / 100:.1f}/{seconds:.1f} [00:00<00:00, 9.9seconds/s]")
                sys.stderr.flush()
                time.sleep(0.02)
            sys.stderr.write("\n")
        name = os.path.splitext(os.path.basename(track))[0]
        for k, stem in enumerate(stems):
            dest = os.path.join(a.out, a.name, a.filename.format(track=name, stem=stem, ext=ext, trackext="wav"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            codec = ["-c:a", "flac"] if a.flac else ["-c:a", "pcm_s24le" if a.int24 else "pcm_s16le"]
            subprocess.run([ff, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"sine=frequency={220 * (k + 1)}:sample_rate=44100:duration={seconds:.3f}", "-af", "volume=0.05",
                            "-ac", "2"] + codec + [dest], check=True)


if __name__ == "__main__":
    main()
