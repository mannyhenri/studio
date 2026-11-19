# Stand-ins for the AI tools' local models

The sandbox has no PyTorch and no models, like its stand-in ComfyUI renders nothing. Two of the AI tools (Production ›
AI tools) run a local model in ComfyUI's Python rather than a ComfyUI graph: **AI Stems** runs Demucs and **AI
Subtitles** runs faster-whisper. These packages stand in for them, with the same command line and API the engines use,
and answer like the stand-in ComfyUI does: a quiet tone for every stem, a placeholder transcript.

The sandbox puts this folder on the engines' `PYTHONPATH` (`STUDIO_GENERATORS_PYTHONPATH`, set by `scripts/sandbox.py`),
and only theirs: on a PC, ComfyUI's Python must have the real `demucs` and `faster-whisper` installed, or the two tools
say they're not ready and how to install them.
