"""The sample media are what docs/sandbox.md says they are."""
import subprocess
import wave

import make_media
import numpy as np
from PIL import Image


def test_sample_media(tmp_path):
    made = make_media.make_all(str(tmp_path))
    assert set(made) == {"street.mp4", "portrait.png", "theo.png", "chair.png", "song.wav", "take1.mp4", "take2.mp4", "take3.mp4"}
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration",
                          "-of", "default=nw=1", str(tmp_path / "street.mp4")], capture_output=True, text=True).stdout
    assert "width=1280" in out and "height=720" in out and "r_frame_rate=24/1" in out and "codec_type=audio" in out
    assert abs(float(out.split("duration=")[1].split()[0]) - 8.0) < 0.1
    for name in ("portrait.png", "chair.png", "theo.png"):
        with Image.open(tmp_path / name) as im:
            assert im.size == (1024, 1024)
    with wave.open(str(tmp_path / "song.wav")) as w:
        assert w.getnframes() / w.getframerate() == 40.0
        y = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float)
    sr = 48000
    rms = lambda a, b: float(np.sqrt(np.mean(y[a * sr:b * sr] ** 2)))  # noqa: E731
    assert rms(16, 32) > 3 * rms(0, 16) and rms(16, 32) > 3 * rms(32, 40)      # the drop at 16 s
    assert make_media.make_all(str(tmp_path)) == []                           # kept, not made again
