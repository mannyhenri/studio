"""Reference picture + Reference strength for every model of an app (applied to the API graph after it's built).

Models that repaint the reference (MOSS, Krea, Z-Image, Anime...) already do: denoise = 1 - strength in the workflow.
This adds the rest:
- guide-picture models (Qwen Image 2.x edit encoder, FLUX Klein's ReferenceMultiLatent): the reference stays the guide,
  and the render also STARTS from the reference picture, denoised by 1 - strength. High strength = stay close to it,
  0 = only inspiration (the guide alone, as before).
- P-H3 Image: its encoder had no picture at all: the reference becomes <Picture 1> (P-H3 edits picture 1).
  Its strength isn't adjustable (P-H3 decides how much to keep); Studio hides the slider for it.
Applies only when the app's "Use reference" toggle is on and a reference picture is set."""
import copy

GUIDE_CLASSES = ("TextEncodeQwenImage", "ReferenceMultiLatent", "ReferenceLatent")
EMPTY_LATENTS = ("EmptyLatentImage", "EmptySD3LatentImage", "EmptyFlux2LatentImage", "EmptyHunyuanImageLatent",
                 "EmptyQwenImageLayeredLatentImage", "EmptyChromaRadianceLatentImage")
NO_STRENGTH = ("PH3ImageConditioning",)       # models whose reference strength can't be set


def _ref_values(wf, inputs):
    """(use, image, strength) from the app's inputs (labels) and the workflow with the person's values applied."""
    nodes = {str(n["id"]): n for n in wf.get("nodes", [])}

    def val(item):
        n = nodes.get(item["node"])
        wv = (n or {}).get("widgets_values") or []
        return wv[0] if wv else None
    use, image, strength = None, None, None
    for it in inputs:
        lab = (it.get("label") or "").lower()
        if it.get("kind") == "toggle" and lab.startswith("use") and "reference" in lab:
            use = bool(val(it))
        elif it.get("kind") == "image" and "reference" in lab and image is None:
            image = val(it)
        elif it.get("kind") == "float" and "reference" in lab:
            try:
                strength = float(val(it))
            except (TypeError, ValueError):
                strength = None
    return use, image, strength


def _users(api, nid):
    return [(k, name) for k, n in api.items() for name, v in (n.get("inputs") or {}).items()
            if isinstance(v, list) and len(v) == 2 and v[0] == nid]


def _find(api, pred):
    return [k for k, n in api.items() if pred(n.get("class_type") or "")]


def patch(api, wf, inputs):
    use, image, strength = _ref_values(wf, inputs)
    if use is False or not image:
        return api
    api = copy.deepcopy(api)
    ref = "studio_ref_image"
    ref_node = {"class_type": "LoadImage", "inputs": {"image": image}, "_meta": {"title": "Reference (Studio)"}}

    # P-H3: the reference is <Picture 1>
    for k in _find(api, lambda c: c in NO_STRENGTH):
        ins = api[k]["inputs"]
        if not any(n.startswith("images.") for n in ins):
            api[ref] = ref_node
            ins["images.image_1"] = [ref, 0]
            vae = next((v for n, v in ins.items() if n == "vae"), None)
            if vae is None:         # the encoder needs the VAE to read pictures: take the decoder's
                dec = next((api[d]["inputs"].get("vae") for d in _find(api, lambda c: c.startswith("VAEDecode"))), None)
                if dec:
                    ins["vae"] = dec
        return api

    guides = _find(api, lambda c: c.startswith(GUIDE_CLASSES))
    if not guides or strength is None or strength <= 0.001:
        return api
    denoise = max(0.05, min(1.0, 1.0 - float(strength)))
    empty = [k for k in _find(api, lambda c: c in EMPTY_LATENTS)]
    if len(empty) != 1:
        return api                  # unknown layout: leave it as it was
    e = empty[0]
    dec = _find(api, lambda c: c.startswith("VAEDecode"))
    vae = api[dec[0]]["inputs"].get("vae") if dec else None
    if not vae:
        return api
    ein = api[e]["inputs"]
    api[ref] = ref_node
    api["studio_ref_scale"] = {"class_type": "ImageScale", "inputs": {"image": [ref, 0], "upscale_method": "lanczos",
                                                                      "width": ein.get("width"), "height": ein.get("height"), "crop": "center"}}
    api["studio_ref_latent"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["studio_ref_scale", 0], "vae": vae}}
    # start from the reference: every sampler that took the empty latent now takes the encoded reference, denoised less
    for k, name in _users(api, e):
        node = api[k]
        node["inputs"][name] = ["studio_ref_latent", 0]
        ct = node.get("class_type") or ""
        if ct in ("KSampler", "KSamplerAdvanced") and "denoise" in node["inputs"]:
            node["inputs"]["denoise"] = denoise
        elif ct == "SamplerCustomAdvanced" and "sigmas" in node["inputs"]:
            src = node["inputs"]["sigmas"]
            sid = f"studio_ref_sigmas_{k}"
            api[sid] = {"class_type": "SplitSigmasDenoise", "inputs": {"sigmas": src, "denoise": denoise}}
            node["inputs"]["sigmas"] = [sid, 1]          # the low (last) part: starts part-way, like denoise
        elif ct == "SamplerCustom" and "sigmas" in node["inputs"]:
            src = node["inputs"]["sigmas"]
            sid = f"studio_ref_sigmas_{k}"
            api[sid] = {"class_type": "SplitSigmasDenoise", "inputs": {"sigmas": src, "denoise": denoise}}
            node["inputs"]["sigmas"] = [sid, 1]
    return api
