# Studio

A studio app on top of a local ComfyUI: pictures, shots, storyboards and reels from your own models, with no node graphs in sight. Open `http://<this-pc>:8188/manny` (or `/studio`) on the PC, another computer or a phone.

## What's here

| Folder | What it is |
|---|---|
| `ComfyUI-MannyStudio/` | The Studio custom node pack: the web app (`web/`), its server routes (`__init__.py`), the Incognito lock (`auth.py`) and a vendored UI→API workflow converter (`mconv/`, from comfy-cli, GPL-3.0). |
| `workflows/` | The ComfyUI App-mode workflows Studio runs (`*.app.json`) plus the editable graph versions. |
| `builders/` | Python scripts that generate those workflows (`wf.py` is the small builder library). |
| `patches/seedvr2-xpu/` | Patch that adds Intel Arc (XPU) support to the `seedvr2_videoupscaler` node pack 2.5.24. |

## Install on a PC

1. Copy `ComfyUI-MannyStudio` into `ComfyUI/custom_nodes/`.
2. Copy `workflows/*.app.json` into `ComfyUI/user/default/workflows/`.
3. Restart ComfyUI and open `/manny`.

Private data (password hash, characters, storyboards, favorites, LoRA notes, Civitai key) stays in `ComfyUI/user/default/manny_studio/` and is never part of this repo.

## Features

Create (Image, Video and tools: Edit, Inpaint, Upscale, Upscale video, Remove background, Smooth motion) · Gallery with fast cached previews · Compare models · Characters · Storyboard with takes, pick, final-quality re-render, compare/wipe, continue from last frame, finish (1080p, 48/60 fps, sound) · Film editor (trims, crossfades, titles, captions, music bed) · Reel cut to the beat · AI review of a take and AI prompt help (local Qwen3-VL) · LoRA browser for Civitai (your own key) · Incognito private space · Mobile layout.
