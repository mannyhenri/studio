#!/usr/bin/env python3
"""AI Stems: a song or a mix split into stems by Demucs (Meta's hybrid transformer model), on this PC.

    engine.py <sound or video> --stems 2|4|6 [--quality fast|best] [--format wav|flac] --out <folder> --progress <json>

The sound is read with ffmpeg (any format, or a video's first sound track) and handed to Demucs as a 44.1 kHz stereo
WAV; Demucs runs in its own process (its progress bar is read for the job's progress), on the GPU when PyTorch has one
it knows, else on the CPU. Each stem lands in the output folder as "<name> - <stem>.wav"."""
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import studio_engine as se  # noqa: E402

NAMES = {"vocals": "vocals", "no_vocals": "music (no vocals)", "drums": "drums", "bass": "bass", "other": "other",
         "guitar": "guitar", "piano": "piano"}
ORDER = ["vocals", "no_vocals", "drums", "bass", "guitar", "piano", "other"]


def model_for(stems, quality):
    if int(stems) == 6:
        return "htdemucs_6s"
    return "htdemucs_ft" if quality == "best" else "htdemucs"


def demucs_cmd(wav, out_dir, stems, quality, fmt):
    cmd = [sys.executable, "-m", "demucs.separate", "-n", model_for(stems, quality), "-o", out_dir, "--filename", "{stem}.{ext}"]
    if int(stems) == 2:
        cmd += ["--two-stems", "vocals"]
    cmd += ["--flac"] if fmt == "flac" else ["--int24"]
    return cmd + [wav]


def follow(cmd, prog, passes):
    """Run Demucs and turn its progress bar ("45%|████▌ ...", once per model of a bag) into the job's progress."""
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=se.NO_WINDOW)
    buf, last, model, tail = b"", -1, 0, []
    while True:
        chunk = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for line in parts:
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            m = re.search(r"(\d{1,3})%\|", text)
            if m:
                pct = int(m.group(1))
                if pct < last:          # a bag of models: the next one starts over
                    model += 1
                last = pct
                k = min(model, passes - 1)
                prog(stage=f"Separating (model {k + 1} of {passes}): {pct}%" if passes > 1 else f"Separating: {pct}%",
                     done=1 + k * 100 + pct, total=2 + passes * 100)
            else:
                tail = (tail + [text])[-6:]
    code = p.wait()
    if code != 0:
        raise se.EngineError("Demucs failed: " + " ".join(tail)[-500:])


def main():
    ap = se.parser("AI Stems: vocals, drums, bass… with Demucs")
    ap.add_argument("--stems", type=int, default=4, choices=[2, 4, 6])
    ap.add_argument("--quality", default="fast", choices=["fast", "best"])
    ap.add_argument("--format", default="wav", choices=["wav", "flac"])
    a = ap.parse_args()
    prog = se.Progress(a.progress)
    with se.engine(prog):
        run(a, prog)


def run(a, prog):
    info = se.probe(a.input)
    if not info["has_audio"]:
        raise se.EngineError(f"{os.path.basename(a.input)} has no sound")
    passes = 4 if model_for(a.stems, a.quality) == "htdemucs_ft" else 1
    os.makedirs(a.out, exist_ok=True)
    base = os.path.splitext(os.path.basename(a.input))[0]
    with se.workdir("stems") as wd:
        prog(stage="Reading the sound", done=0, total=2 + passes * 100)
        wav = os.path.join(wd, "mix.wav")
        se.ff(["-i", a.input, "-map", "0:a:0", "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s24le", wav], what="Reading the sound")
        prog(stage="Loading Demucs", done=1)
        sep = os.path.join(wd, "separated")
        follow(demucs_cmd(wav, sep, a.stems, a.quality, a.format), prog, passes)
        made = {}
        for root, _dirs, files in os.walk(sep):
            for f in files:
                stem, ext = os.path.splitext(f)
                if ext.lower() in (".wav", ".flac", ".mp3"):
                    made[stem] = os.path.join(root, f)
        if not made:
            raise se.EngineError("Demucs finished without writing any stem")
        outs = []
        for stem in sorted(made, key=lambda s: ORDER.index(s) if s in ORDER else 99):
            dest = os.path.join(a.out, f"{base} - {NAMES.get(stem, stem)}{os.path.splitext(made[stem])[1].lower()}")
            shutil.move(made[stem], dest)          # a rename on the same drive
            outs.append(dest)
        names = ", ".join(NAMES.get(s, s) for s in sorted(made, key=lambda s: ORDER.index(s) if s in ORDER else 99))
        prog.finish(outs, note=f"{len(outs)} stems: {names}.", stats={"model": model_for(a.stems, a.quality), "stems": len(outs)})


if __name__ == "__main__":
    main()
