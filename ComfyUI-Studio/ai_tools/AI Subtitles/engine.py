#!/usr/bin/env python3
"""AI Subtitles: the dialogue heard by Whisper, on this PC, written as subtitles (.srt, .vtt) and a transcript (.txt).

    engine.py <video or sound> [--language auto|en|fr|...] [--translate] [--accuracy fast|balanced|best] [--chars 42|32]
              --out <folder> --progress <json>

faster-whisper (CTranslate2) is used when it's installed, else OpenAI's whisper package. The sound is read with ffmpeg
as 16 kHz mono (what Whisper listens to). Whisper's own segments are cut into subtitles by studio_engine.make_cues,
with the words' own times (at most two lines, a sentence's end closes a subtitle when it can)."""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import studio_engine as se  # noqa: E402

MODELS = {"fast": "small", "balanced": "medium", "best": "large-v3"}


def with_faster_whisper(wav, size, language, task, duration, prog):
    from faster_whisper import WhisperModel
    prog(stage=f"Loading Whisper {size}")
    try:
        model = WhisperModel(size, device="auto", compute_type="auto")
    except ValueError:
        model = WhisperModel(size, device="auto", compute_type="default")
    segments, info = model.transcribe(wav, language=language, task=task, word_timestamps=True, vad_filter=True, beam_size=5)
    out = []
    for seg in segments:           # a generator: the model listens as it's read
        out.append({"start": seg.start, "end": seg.end, "text": seg.text,
                    "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (seg.words or [])]})
        if duration:
            pct = min(99, int(100 * seg.end / duration))
            prog(stage=f"Listening: {pct}%", done=1 + pct, total=102)
    return out, getattr(info, "language", None) or language


def with_whisper(wav, size, language, task, prog):
    import whisper
    prog(stage=f"Loading Whisper {size}")
    model = whisper.load_model(size)
    prog(stage="Listening (the whole file at once: no progress until it's done)")
    r = model.transcribe(wav, language=language, task=task, word_timestamps=True, verbose=None)
    return [{"start": s["start"], "end": s["end"], "text": s["text"], "words": s.get("words") or []} for s in r.get("segments", [])], r.get("language")


def main():
    ap = se.parser("AI Subtitles: subtitles and a transcript with Whisper")
    ap.add_argument("--language", default="auto")
    ap.add_argument("--translate", action="store_true")
    ap.add_argument("--accuracy", default="balanced", choices=list(MODELS))
    ap.add_argument("--chars", type=int, default=42)
    a = ap.parse_args()
    prog = se.Progress(a.progress)
    with se.engine(prog):
        run(a, prog)


def run(a, prog):
    info = se.probe(a.input)
    if not info["has_audio"]:
        raise se.EngineError(f"{os.path.basename(a.input)} has no sound")
    language = None if a.language in ("", "auto") else a.language
    task = "translate" if a.translate else "transcribe"
    size = MODELS[a.accuracy]
    os.makedirs(a.out, exist_ok=True)
    base = os.path.splitext(os.path.basename(a.input))[0]
    with se.workdir("subtitles") as wd:
        prog(stage="Reading the sound", done=0, total=102)
        wav = os.path.join(wd, "speech.wav")
        se.ff(["-i", a.input, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav], what="Reading the sound")
        prog(done=1)
        if importlib.util.find_spec("faster_whisper"):
            segs, heard = with_faster_whisper(wav, size, language, task, info["duration"], prog)
        elif importlib.util.find_spec("whisper"):
            segs, heard = with_whisper(wav, size, language, task, prog)
        else:
            raise se.EngineError("Whisper isn't installed in ComfyUI's Python: pip install faster-whisper")
    cues = se.make_cues(segs, max_chars=max(20, min(60, a.chars)))
    stats = {"language": ("en (translated from " + str(heard) + ")") if a.translate and heard and heard != "en" else (heard or "?"),
             "subtitles": len(cues)}
    if not cues:
        prog.finish([], note="No speech found in this file: nothing to subtitle.", stats=stats)
        return
    srt, vtt, txt = (os.path.join(a.out, f"{base}{ext}") for ext in (".srt", ".vtt", " transcript.txt"))
    with open(srt, "w", encoding="utf-8") as f:
        f.write(se.to_srt(cues))
    with open(vtt, "w", encoding="utf-8") as f:
        f.write(se.to_vtt(cues))
    with open(txt, "w", encoding="utf-8") as f:
        f.write("\n".join(" ".join(str(s["text"]).split()) for s in segs if str(s["text"]).strip()) + "\n")
    words = sum(len(" ".join(c["lines"]).split()) for c in cues)
    note = f"{len(cues)} subtitle{'s' if len(cues) != 1 else ''}, {words} word{'s' if words != 1 else ''}, until {se.fmt_clock(cues[-1]['end'])}."
    prog.finish([srt, vtt, txt], note=note, stats=stats)


if __name__ == "__main__":
    main()
