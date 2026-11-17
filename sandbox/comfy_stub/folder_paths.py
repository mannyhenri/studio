"""Stand-in for ComfyUI's folder_paths: the folders of the sandbox ComfyUI (set by fake_comfy.configure)."""
import os

base_path = ""
models_dir = ""
folder_names_and_paths = {}
filename_list_cache = {}
supported_pt_extensions = {".ckpt", ".pt", ".pt2", ".bin", ".pth", ".safetensors", ".pkl", ".sft", ".gguf"}
_dirs = {"input": "", "output": "", "temp": "", "user": ""}

MODEL_FOLDERS = ["audio_encoders", "background_removal", "checkpoints", "classifiers", "clip_vision", "configs", "controlnet",
                 "detection", "diffusers", "diffusion_models", "embeddings", "frame_interpolation", "geometry_estimation", "gligen",
                 "hypernetworks", "latent_upscale_models", "loras", "model_patches", "optical_flow", "photomaker", "style_models",
                 "text_encoders", "upscale_models", "vae", "vae_approx"]


def configure(root):
    """Lay out a ComfyUI folder tree under root (models/, input/, output/, temp/, user/)."""
    global base_path, models_dir
    base_path = root
    models_dir = os.path.join(root, "models")
    for k in _dirs:
        _dirs[k] = os.path.join(root, k)
        os.makedirs(_dirs[k], exist_ok=True)
    folder_names_and_paths.clear()
    for name in MODEL_FOLDERS:
        p = os.path.join(models_dir, name)
        os.makedirs(p, exist_ok=True)
        folder_names_and_paths[name] = ([p], set() if name == "configs" else supported_pt_extensions)
    folder_names_and_paths["unet"] = folder_names_and_paths["diffusion_models"]
    folder_names_and_paths["clip"] = folder_names_and_paths["text_encoders"]
    folder_names_and_paths["custom_nodes"] = ([os.path.join(root, "custom_nodes")], set())
    filename_list_cache.clear()


def get_input_directory():
    return _dirs["input"]


def get_output_directory():
    return _dirs["output"]


def get_temp_directory():
    return _dirs["temp"]


def get_user_directory():
    return _dirs["user"]


def get_directory_by_type(type_name):
    return _dirs.get(type_name)


def get_folder_paths(folder_name):
    return list(folder_names_and_paths.get(folder_name, ([], set()))[0])


def get_filename_list(folder_name):
    paths, exts = folder_names_and_paths.get(folder_name, ([], set()))
    out = []
    for root in paths:
        for dirpath, _dirs2, files in os.walk(root, followlinks=True):
            for f in files:
                if exts and os.path.splitext(f)[1].lower() not in exts:
                    continue
                out.append(os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/"))
    return sorted(set(out))


def get_full_path(folder_name, filename):
    for root in get_folder_paths(folder_name):
        p = os.path.join(root, filename.replace("\\", "/"))
        if os.path.isfile(p):
            return p
    return None


def get_full_path_or_raise(folder_name, filename):
    p = get_full_path(folder_name, filename)
    if p is None:
        raise FileNotFoundError(f"Model in folder '{folder_name}' with filename '{filename}' not found.")
    return p


def annotated_filepath(name):
    if name.endswith("[output]"):
        return name[:-8].strip(), get_output_directory()
    if name.endswith("[input]"):
        return name[:-7].strip(), get_input_directory()
    if name.endswith("[temp]"):
        return name[:-6].strip(), get_temp_directory()
    return name, None


def get_annotated_filepath(name, default_dir=None):
    name, base = annotated_filepath(name)
    return os.path.join(base or default_dir or get_input_directory(), name)


def exists_annotated_filepath(name):
    return os.path.exists(get_annotated_filepath(name))


def get_save_image_path(filename_prefix, output_dir, image_width=0, image_height=0):
    subfolder = os.path.dirname(os.path.normpath(filename_prefix))
    filename = os.path.basename(os.path.normpath(filename_prefix))
    full_output_folder = os.path.join(output_dir, subfolder)
    os.makedirs(full_output_folder, exist_ok=True)
    counter = 1
    for f in os.listdir(full_output_folder):
        if f.startswith(filename + "_"):
            try:
                counter = max(counter, int(f[len(filename) + 1:].split("_")[0].split(".")[0]) + 1)
            except ValueError:
                pass
    return full_output_folder, filename, counter, subfolder, filename_prefix
