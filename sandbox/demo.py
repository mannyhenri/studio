"""Seeds the demo data into a running sandbox. The demo is the pictures and clips of a small piece of a real Studio film,
"The Glass Harbour" (sandbox/samples, about 1 MB, metadata removed), and nothing else: no characters, storyboards or
projects, so a screenplay import (screenplays/) starts from a blank slate.

  Gallery      the three clips, the three storyboard frames, the diner and the salt shaker
  inputs       all of the above for Create, with the faces of June and Ray, plus the drawn test media
               (street.mp4, portrait.png, chair.png, song.wav)

    python sandbox/demo.py --url http://127.0.0.1:8188 --root sandbox/run"""
import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
sys.path.insert(0, HERE)
import make_media  # noqa: E402

GALLERY = {"Videos": ["diner-wide.mp4", "diner-ray.mp4", "diner-counter.mp4"],
           "Images": ["harbour-1A.jpg", "harbour-1D.jpg", "harbour-1H.jpg", "diner.jpg", "salt-shaker.jpg"]}
DRAWN_INPUTS = ["street.mp4", "portrait.png", "chair.png", "song.wav"]


def place_media(root, media):
    """Gallery shows the output folder; Create picks inputs from the input folder."""
    out, inp = os.path.join(root, "output"), os.path.join(root, "input")
    for sub, names in GALLERY.items():
        os.makedirs(os.path.join(out, sub), exist_ok=True)
        for n in names:
            shutil.copy2(os.path.join(SAMPLES, n), os.path.join(out, sub, n))
    os.makedirs(os.path.join(inp, "studio"), exist_ok=True)
    for n in os.listdir(SAMPLES):
        shutil.copy2(os.path.join(SAMPLES, n), os.path.join(inp, n))
    for n in ("june.jpg", "ray.jpg", "diner.jpg"):
        shutil.copy2(os.path.join(SAMPLES, n), os.path.join(inp, "studio", n))
    for n in DRAWN_INPUTS:
        shutil.copy2(os.path.join(media, n), os.path.join(inp, n))


def seed(url, root, media=None):
    media = media or os.path.join(HERE, "media")
    make_media.make_all(media)
    place_media(root, media)
    return {"clips": len(GALLERY["Videos"]), "pictures": len(GALLERY["Images"])}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188")
    ap.add_argument("--root", default=os.path.join(HERE, "run"))
    a = ap.parse_args()
    print(json.dumps(seed(a.url.rstrip("/"), a.root)))
