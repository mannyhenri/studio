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

## Render on other GPUs (the Mac Pro)

Studio can send a render to another computer running ComfyUI. Today that's the Mac Pro's two Radeon Pro Vega II cards, one worker per card (set up in the `comfy-mac` repo). The file comes back to this PC's output folder, so Gallery, Storyboard and Finish work as usual.

**Set up**
1. On the Mac: start both workers and the LAN gate (`lan_gate.py --allow <this-pc>.local --ports 8188 8189`). ComfyUI has no password, so the gate only lets this PC in.
2. In Studio, on this PC (or with Incognito unlocked): **Settings → GPU machines**, add `mac-vega-a` · `Mannys-Mac-Pro.local` · `8188` and `mac-vega-b` · `Mannys-Mac-Pro.local` · `8189`. They're kept in `manny_studio/workers.json` (comfy-mac's `mac-workers.json` also works pasted as is).
3. Under **Generate** a "Run on" menu appears: **This PC**, **Any free GPU**, or one machine. The same choice is used by Finish, Music and Voices. Over MCP, `studio_generate` takes `run_on`.

**How a job is sent**
- Studio first checks the machine *live*: every node must be installed, every model, sampler or scheduler name must be one that machine offers for that exact input, and every required input must be present. Missing anything → the job isn't sent, and you see what's missing (node, input, value). It never renders something different by accident.
- Model switches a job doesn't use are dropped before the check. Without that, ComfyUI would ask for every model an app *could* pick. The trimmed job is exactly what ComfyUI would run anyway.
- **Any free GPU** picks the least busy machine that has everything, and this PC wins ties. A Mac card that has been on one of your jobs for more than 10 min counts as busier. Measured on one Vega II: Remove background 14 s, Smooth motion 27 s, Z-Image 4.5 min, Upscale (SeedVR2 7b) 9 min, Inpaint 13 min, LTX video 44–55 min, Upscale video about 2 h (62 frames to 1080p).
- **Incognito** jobs never go to another machine on "Any free GPU"; they run here unless you pick a Mac by name. If you do, the job crosses your network over plain HTTP and is wiped from the Mac when it's done.
- The input files (pictures, videos, sounds) are uploaded with the job, into a folder of their own. The result is copied into this PC's output folder under the next free name (it never replaces a file here). Then the Mac deletes the job's outputs, uploads and history (`/mac/forget`). A failed job is cleaned up the same way.
- Cancel (✕ in the queue drawer) takes the job out of the Mac's queue or stops it, then cleans up the same way. A Mac that restarts mid-job fails the job ("restarted and lost the job"); one that stops answering for 10 min does too.
- Jobs on other machines survive a restart of this PC's ComfyUI: they're kept in `manny_studio/remote_jobs.json` and followed again at startup. As a safety net the Mac clears Studio jobs that finished more than 24 h ago, so a result that waits longer than a day for this PC to come back is lost (the job shows as failed).

**What stays on this PC:** AI review and AI prompt help (their result is text, not a file) and Topaz in Finish.

**Models on the Mac.** A choice runs on the Mac only once its model files are there. Most come from Hugging Face (the comfy-mac repo fetches them). These exist only on this PC, so copy them yourself into `~/code/comfy-mac/ComfyUI/models/<folder>/` on the Mac. Both workers see them at once, no restart needed:

| File | Folder | Unlocks |
|---|---|---|
| `anime_desire_illustrious_v3_fp16` | `checkpoints` | Image · Anime Desire |
| `flux3-image-klein-int8-convrot` | `diffusion_models` | Image and Edit · FLUX 3 Klein (needs `qwen3vl_4b_int8_convrot` from HF too) |
| `anima-base-v1.0` | `diffusion_models` | Image · Anima (with `qwen_3_06b_base`) |
| `qwen_3_06b_base` | `text_encoders` | Image · Anima |
| `p-h3-image_vae_int8_convrot` | `vae` | Image · P-H3 |
| `moss_image_nsfw_uncensored_lora`, `qwen_image_2.1_nsfw_lora` | `loras` | the Uncensored toggles |

**If Settings shows a Mac "offline"** although its workers run: the Mac's LAN gate only lets in the PC it was started for (`lan-gate.sh` allows `desktop-mggcgsg.local`). On the Mac, `ping <this-pc>.local` must answer; if it doesn't, start the gate with this PC's IP (`GATE_ALLOW=192.168.x.y ./lan-gate.sh`). Windows tries the Mac's IPv6 address first, and the gate listens on both.

Check which choices a machine can run with `POST /manny/api/workers/check {"app": "<app id>", "values": {...}}`.

**Tests** (need the workers running; comfy-mac's venv has aiohttp and simpleeval):
```sh
PY=~/code/comfy-mac/.venv/bin/python
$PY tests/test_prune.py       # every app x every choice: trimmed job == what ComfyUI runs
$PY tests/test_remote.py      # check == each worker's own validation, real renders, cleanup
$PY tests/test_run_route.py   # Studio's routes: Run on, Any free GPU, cancel, Finish/Music/Voice paths
$PY tests/test_revise.py      # no overwrites, shared references, failed/lost/restarted jobs, resume after restart
```
`RENDER_APPS` chooses which apps render to the end (default: Remove background and Smooth motion).
