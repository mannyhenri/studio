"""Writes sandbox/models.json and sandbox/inputs.json.

models.json: every model file the workflows and Studio's code name, by ComfyUI folder. The sandbox
creates each one as an empty placeholder, so the model pickers and the job checks list the same files as the PC.
inputs.json: the example pictures, videos and sounds the apps open with (their default input files); the sandbox makes
each from its sample media, so every app can be queued with its default values.
Run it again after adding a workflow or a model to the code: python sandbox/collect_models.py"""
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXT = (".safetensors", ".gguf", ".pt", ".pth", ".bin", ".ckpt", ".sft", ".onnx")
KEY_FOLDER = {"unet_name": "diffusion_models", "clip_name": "text_encoders", "clip_name1": "text_encoders",
              "clip_name2": "text_encoders", "clip_name3": "text_encoders", "vae_name": "vae", "lora_name": "loras",
              "ckpt_name": "checkpoints", "bg_removal_name": "background_removal"}


def main():
    with open(os.path.join(HERE, "file_inputs.json"), encoding="utf-8") as f:
        file_inputs = json.load(f)
    found = {}
    inputs = {}
    loaders = {"LoadImage": "image", "LoadImageMask": "image", "LoadVideo": "video", "LoadAudio": "audio"}

    def add(folder, name):
        if isinstance(name, str) and name.lower().endswith(EXT) and folder not in ("image", "video", "audio"):
            found.setdefault(folder, set()).add(name.replace("\\", "/"))

    for wf in glob.glob(os.path.join(REPO, "workflows", "*.json")):
        with open(wf, encoding="utf-8") as f:
            w = json.load(f)
        nodes = (w.get("nodes") or []) + [n for d in (w.get("definitions") or {}).get("subgraphs", []) for n in d.get("nodes", [])]
        for n in nodes:
            spec = file_inputs.get(n.get("type"))
            vals = n.get("widgets_values")
            if spec and isinstance(vals, list) and vals:
                add(list(spec.values())[0][0], vals[0])
            if n.get("type") in loaders and isinstance(vals, list) and vals and isinstance(vals[0], str) and vals[0]:
                inputs[vals[0].replace(" [input]", "")] = loaders[n["type"]]
    for py in glob.glob(os.path.join(REPO, "ComfyUI-Studio", "**", "*.py"), recursive=True) + \
            glob.glob(os.path.join(REPO, "builders", "*.py")):
        with open(py, encoding="utf-8") as f:
            src = f.read()
        for key, name in re.findall(r'"(\w+_name\d?)":\s*"([^"]+)"', src):
            if key in KEY_FOLDER:
                add(KEY_FOLDER[key], name)
            elif key == "model_name":
                add("latent_upscale_models" if "upscal" in name.lower() else "frame_interpolation", name)
        for name in re.findall(r'"([\w./ -]+\.(?:safetensors|gguf))"', src):
            low = name.lower()
            folder = ("loras" if "lora" in low else "vae" if "vae" in low else
                      "text_encoders" if re.search(r"qwen3vl|qwen_3|umt5|t5xxl|gemma|clip_|llava|mistral|text_enc", low) else
                      "diffusion_models")
            if not any(name in v for v in found.values()):
                add(folder, name)
    out = {k: sorted(v) for k, v in sorted(found.items())}
    with open(os.path.join(HERE, "models.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    with open(os.path.join(HERE, "inputs.json"), "w", encoding="utf-8") as f:
        json.dump(dict(sorted(inputs.items())), f, indent=1)
    print({k: len(v) for k, v in out.items()}, sum(len(v) for v in out.values()), "model files;", len(inputs), "default inputs")


if __name__ == "__main__":
    main()
