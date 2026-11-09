# Manny Studio

A studio app on top of this ComfyUI. Open **http://127.0.0.1:8188/manny** (or http://<this-PC>:8188/manny from another computer on your network).

- Every workflow saved as an app (`*.app.json`, made with App builder in the node graph) appears in Create automatically.
- Gallery reads `Pictures\Comfyui` (ComfyUI's output folder). "Move to trash" moves files into `_trash` there.
- Characters are stored in `user\default\manny_studio\characters.json`; their faces and voices in ComfyUI's `input\manny` folder.

`mconv/` is the UI-to-API workflow converter from comfy-cli 1.23.0 (GPL-3.0), vendored unchanged except for import paths.
