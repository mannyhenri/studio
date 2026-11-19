"""Sandbox stand-in for faster-whisper (see ../README.md): WhisperModel(...).transcribe(...) "hears" a placeholder line
every few seconds of the sound, with word times, in the language asked (English when it's left to listening)."""
import collections
import wave

__version__ = "1.1.0+sandbox"
Word = collections.namedtuple("Word", "start end word probability")
Segment = collections.namedtuple("Segment", "id seek start end text tokens avg_logprob compression_ratio no_speech_prob words temperature")
Info = collections.namedtuple("TranscriptionInfo", "language language_probability duration duration_after_vad all_language_probs transcription_options vad_options")
LINES = ["Sandbox transcript: nothing was really heard here.", "The stand-in model writes a line every few seconds.",
         "On the PC, Whisper listens to the real dialogue.", "Each subtitle is timed to its words."]


class WhisperModel:
    def __init__(self, model_size_or_path, device="auto", compute_type="default", **kw):
        self.size = model_size_or_path

    def transcribe(self, audio, language=None, task="transcribe", word_timestamps=False, **kw):
        with wave.open(audio) as w:
            seconds = w.getnframes() / float(w.getframerate())
        info = Info(language or "en", 1.0, seconds, seconds, None, None, None)

        def segments():
            t, k = 0.5, 0
            while t + 2.0 <= seconds:
                text = LINES[k % len(LINES)]
                words, step, at = [], 2.4 / len(text.split()), t
                for w in text.split():
                    words.append(Word(round(at, 3), round(at + step * 0.9, 3), " " + w, 0.9))
                    at += step
                yield Segment(k, 0, round(t, 3), round(at, 3), " " + text, [], -0.2, 1.2, 0.01, words if word_timestamps else None, 0.0)
                t, k = at + 1.2, k + 1
        return segments(), info
