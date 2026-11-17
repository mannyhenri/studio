"""Makes the sandbox's sample media with ffmpeg and numpy (the repo carries no large files):

  street.mp4    8 s, 1280x720, 24 fps: a street at dusk with cars passing, and street noise
  portrait.png  1024x1024: a drawn portrait (Maya's face)
  theo.png      1024x1024: a second drawn portrait (Theo's face)
  chair.png     1024x1024: a chair on a plain floor
  song.wav      40 s, 120 BPM: quiet 0-16 s, loud 16-32 s (the drop at 16 s), quiet 32-40 s
  take1.mp4, take2.mp4, take3.mp4   4 s takes for the demo storyboard

    python sandbox/make_media.py [folder]       (default sandbox/media; files already there are kept)"""
import math
import os
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from results import ffmpeg  # noqa: E402


def _run(args):
    subprocess.run([ffmpeg(), "-loglevel", "error", "-y", *args], check=True, timeout=300)


def street(path):
    cars = "".join(f",drawbox=x='mod(t*{v}+{o},1500)-160':y={y}:w={w}:h={h}:color={c}:t=fill"
                   for v, o, y, w, h, c in [(260, 0, 500, 180, 70, "0xb03a2e"), (180, 600, 540, 220, 80, "0x2e5fb0"),
                                            (320, 1100, 470, 150, 60, "0xe0c040")])
    lights = "".join(f",drawbox=x={x}:y=250:w=10:h=200:color=0x202020:t=fill,drawbox=x={x - 14}:y=236:w=38:h=18:color=0xffd27a:t=fill"
                     for x in (120, 520, 920))
    vf = ("drawbox=x=0:y=0:w=iw:h=300:color=0x2c3e63:t=fill,drawbox=x=0:y=300:w=iw:h=150:color=0x3d3d46:t=fill,"
          "drawbox=x=0:y=450:w=iw:h=270:color=0x262628:t=fill,drawbox=x=0:y=578:w=iw:h=8:color=0xd8d8d8:t=fill" + lights + cars)
    _run(["-f", "lavfi", "-i", "color=c=0x1d2433:s=1280x720:r=24:d=8", "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.08:d=8:r=48000",
          "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
          "-movflags", "+faststart", path])


def portrait(path, skin=(224, 178, 150), hair=(52, 34, 26), bg=((40, 70, 90), (120, 160, 170)), short_hair=False):
    s = 1024
    y = np.linspace(0, 1, s)[:, None]
    grad = (np.array(bg[0]) * (1 - y)[..., None] + np.array(bg[1]) * y[..., None]).repeat(s, axis=1).astype(np.uint8)
    im = Image.fromarray(grad, "RGB")
    d = ImageDraw.Draw(im)
    d.ellipse((262, 760, 762, 1260), fill=(70, 80, 110))                       # shoulders
    d.rectangle((452, 600, 572, 800), fill=tuple(int(c * 0.92) for c in skin))  # neck
    if not short_hair:
        d.ellipse((292, 170, 732, 760), fill=hair)                             # hair behind
    d.ellipse((332, 230, 692, 690), fill=skin)                                 # face
    d.chord((322, 190, 702, 520), 180, 360, fill=hair)                         # fringe
    for x in (430, 594):
        d.ellipse((x - 34, 430, x + 34, 470), fill=(250, 250, 250))
        d.ellipse((x - 14, 436, x + 14, 464), fill=(60, 40, 30))
    d.line((512, 470, 500, 560, 524, 565), fill=tuple(int(c * 0.8) for c in skin), width=6)
    d.arc((452, 560, 572, 630), 15, 165, fill=(150, 60, 60), width=8)
    im.filter(ImageFilter.GaussianBlur(1.2)).save(path)


def chair(path):
    s = 1024
    im = Image.new("RGB", (s, s), (214, 206, 194))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 700, s, s), fill=(168, 140, 110))
    wood = (120, 72, 40)
    d.rectangle((362, 300, 392, 760), fill=wood)
    d.rectangle((632, 300, 662, 760), fill=wood)
    d.rectangle((362, 300, 662, 340), fill=wood)
    d.rectangle((362, 400, 662, 430), fill=wood)
    d.polygon([(332, 540), (692, 540), (662, 600), (362, 600)], fill=(140, 86, 48))
    for x in (342, 652):
        d.rectangle((x, 600, x + 28, 860), fill=wood)
    d.ellipse((300, 840, 724, 900), fill=(150, 124, 96))
    im.save(path)


def song(path, seconds=40, sr=48000, bpm=120):
    t = np.arange(int(seconds * sr)) / sr
    loud = np.where((t >= 16) & (t < 32), 1.0, 0.25)
    pad = 0.12 * np.sin(2 * math.pi * 110 * t) * (0.6 + 0.4 * np.sin(2 * math.pi * 0.25 * t))
    beat = 60 / bpm
    phase = np.mod(t, beat)
    kick = np.exp(-phase * 30) * np.sin(2 * math.pi * (60 + 120 * np.exp(-phase * 40)) * phase)
    hat = np.where(np.mod(t - beat / 2, beat) < 0.02, np.random.default_rng(1).uniform(-1, 1, len(t)) * 0.3, 0)
    y = (pad + 0.8 * kick + hat * (t >= 16)) * loud
    y = (np.clip(y, -1, 1) * 32000).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())


def take_from_video(src, path, start):
    _run(["-ss", str(start), "-i", src, "-t", "4", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-movflags", "+faststart", path])


def take_from_picture(src, path):
    _run(["-loop", "1", "-i", src, "-f", "lavfi", "-i", "anoisesrc=color=brown:amplitude=0.03:d=4:r=48000", "-t", "4",
          "-vf", "scale=1280:-2,zoompan=z='1+0.0015*on':d=96:s=1280x720:fps=24", "-c:v", "libx264", "-preset", "veryfast",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart", path])


def make_all(folder):
    os.makedirs(folder, exist_ok=True)
    p = lambda n: os.path.join(folder, n)  # noqa: E731
    jobs = [("street.mp4", lambda: street(p("street.mp4"))),
            ("portrait.png", lambda: portrait(p("portrait.png"))),
            ("theo.png", lambda: portrait(p("theo.png"), skin=(150, 105, 80), hair=(20, 20, 24), bg=((60, 40, 40), (170, 120, 90)), short_hair=True)),
            ("chair.png", lambda: chair(p("chair.png"))),
            ("song.wav", lambda: song(p("song.wav"))),
            ("take1.mp4", lambda: take_from_video(p("street.mp4"), p("take1.mp4"), 0)),
            ("take2.mp4", lambda: take_from_picture(p("portrait.png"), p("take2.mp4"))),
            ("take3.mp4", lambda: take_from_video(p("street.mp4"), p("take3.mp4"), 4))]
    made = []
    for name, fn in jobs:
        if not os.path.isfile(p(name)):
            fn()
            made.append(name)
    return made


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "media")
    print("made:", ", ".join(make_all(out)) or "nothing (all there)")
