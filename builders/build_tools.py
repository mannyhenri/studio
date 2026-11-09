"""Builds the Studio tool apps (Edit, Inpaint, Upscale, Upscale video, Remove background, Smooth motion)."""
import sys, json, copy, uuid
sys.path.insert(0, '.')
from wf import *

OUT = sys.argv[1]
T = {'PrimitiveBoolean': 'BOOLEAN', 'PrimitiveInt': 'INT', 'PrimitiveFloat': 'FLOAT', 'PrimitiveStringMultiline': 'STRING',
     'CustomCombo': 'COMBO', 'LoadImage': 'COMBO', 'LoadImageMask': 'COMBO', 'LoadVideo': 'COMBO', 'LoadAudio': 'COMBO'}


def as_dict(w, extra_defs=()):
    return {"id": str(uuid.uuid4()), "revision": 0, "last_node_id": w.nid, "last_link_id": w.lid, "nodes": w.nodes, "links": w.links,
            "groups": w.groups, "definitions": {"subgraphs": list(extra_defs)}, "config": {}, "extra": {"ds": {"scale": 0.6, "offset": [100, 60]}}, "version": 0.4}


def make_app(d, name, spec, outputs):
    d = copy.deepcopy(d)
    d['id'] = str(uuid.uuid4())
    by = {n['id']: n for n in d['nodes']}
    li = []
    for nid, wn, label in spec:
        n = by[nid]
        ins = n.setdefault('inputs', [])
        e = next((i for i in ins if (i.get('widget') or {}).get('name') == wn), None)
        if e is None:
            e = {'name': wn, 'type': T[n['type']], 'widget': {'name': wn}, 'link': None}
            ins.append(e)
        e['label'] = label
        li.append([f"{d['id']}:{nid}:{wn}", wn])
    d['extra']['linearData'] = {'inputs': li, 'outputs': [str(o) for o in outputs]}
    d['extra']['linearMode'] = True
    json.dump(d, open(f"{OUT}/Studio - {name}.app.json", 'w'), indent=1, ensure_ascii=False)
    print('built', name, len(li), 'inputs')


def combo(w, title, opts, default=0):
    return w.add("CustomCombo", (0, 0), [], [("STRING", "STRING"), ("INDEX", "INT")], [opts[default], default] + opts + [""], title=title, size=(360, 60 + 30 * len(opts)), color=True)


def boolean(w, title, v=False):
    return w.add("PrimitiveBoolean", (0, 0), [], [("BOOLEAN", "BOOLEAN")], [v], title=title, size=(360, 60), color=True)


def seed_node(w):
    return w.add("PrimitiveInt", (0, 0), [], [("INT", "INT")], [0, "randomize"], title="Seed", size=(360, 80), color=True)


def load_image(w, title):
    return w.add("LoadImage", (0, 0), [], [("IMAGE", "IMAGE"), ("MASK", "MASK")], ["ref_couture_model.png", "image"], title=title, size=(400, 420))


def swtch(w, typ, on_false, on_true, cond, cslot, title):
    s = w.add("ComfySwitchNode", (0, 0), [("on_false", typ), ("on_true", typ), ("switch", "BOOLEAN", "w")], [("output", typ)], [False], collapsed=True, title=title)
    w.c(on_false[0], on_false[1], s, "on_false"); w.c(on_true[0], on_true[1], s, "on_true"); w.c(cond, cslot, s, "switch")
    return s


def gate(w, src, tog, typ):
    s = w.add("ComfySwitchNode", (0, 0), [("on_false", typ), ("on_true", typ), ("switch", "BOOLEAN", "w")], [("output", typ)], [False], collapsed=True, title="use?")
    w.c(src, 0, s, "on_true"); w.c(tog, 0, s, "switch")
    return s


def concat(w, a, b_node, delim, title):
    c = w.add("StringConcatenate", (0, 0), [("string_a", "STRING", "w"), ("string_b", "STRING", "w")], [("STRING", "STRING")], [a, "", delim], collapsed=True, title=title)
    w.c(b_node, 0, c, "string_b")
    return c


def model_name(w, mdl):
    rx = w.add("RegexExtract", (0, 0), [("string", "STRING", "w")], [("STRING", "STRING")], ["", r"^[^(]*[^ (]", "First Match", True, False, False, 1], collapsed=True, title="model name")
    w.c(mdl, 0, rx, "string")
    return rx


def save_image(w, img, prefix_node=None, prefix="Images/img"):
    sv = w.add("SaveImage", (900, 0), [("images", "IMAGE"), ("filename_prefix", "STRING", "w")], [], [prefix], title="RESULT", size=(560, 640))
    w.c(img, 0, sv, "images")
    if prefix_node: w.c(prefix_node, 0, sv, "filename_prefix")
    return sv


QSIG = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"


def qwen_sampler(w, model, pos, neg, latent, seed):
    g = w.add("CFGGuider", (0, 0), [("model", "MODEL"), ("positive", "CONDITIONING"), ("negative", "CONDITIONING")], [("GUIDER", "GUIDER")], [1], collapsed=True)
    w.c(model[0], model[1], g, "model"); w.c(pos[0], pos[1], g, "positive"); w.c(neg[0], neg[1], g, "negative")
    sig = w.add("ManualSigmas", (0, 0), [], [("SIGMAS", "SIGMAS")], [QSIG], collapsed=True, title="Qwen 2.1 Turbo 8-step schedule")
    nz = w.add("RandomNoise", (0, 0), [("noise_seed", "INT", "w")], [("NOISE", "NOISE")], [0, "randomize"], collapsed=True); w.c(seed, 0, nz, "noise_seed")
    ks = w.add("KSamplerSelect", (0, 0), [], [("SAMPLER", "SAMPLER")], ["euler"], collapsed=True)
    sa = w.add("SamplerCustomAdvanced", (0, 0), [("noise", "NOISE"), ("guider", "GUIDER"), ("sampler", "SAMPLER"), ("sigmas", "SIGMAS"), ("latent_image", "LATENT")], [("output", "LATENT"), ("denoised_output", "LATENT")], collapsed=True)
    w.c(nz, 0, sa, "noise"); w.c(g, 0, sa, "guider"); w.c(ks, 0, sa, "sampler"); w.c(sig, 0, sa, "sigmas"); w.c(latent[0], latent[1], sa, "latent_image")
    return sa


def qwen_model(w, unc):
    qu = unet(w, (0, 0), "qwen_image_2.1_turbo_int8_convrot.safetensors")
    lo = w.add("LoraLoaderModelOnly", (0, 0), [("model", "MODEL")], [("MODEL", "MODEL")], ["qwen_image_2.1_nsfw_lora.safetensors", 1.0], collapsed=True, title="Qwen uncensored LoRA")
    w.c(qu, 0, lo, "model")
    m = swtch(w, "MODEL", (qu, 0), (lo, 0), unc, 0, "uncensored?")
    k = w.add("QwenImage21Cache", (0, 0), [("model", "MODEL")], [("MODEL", "MODEL")], ["auto", "default"], collapsed=True); w.c(m, 0, k, "model")
    qc = clip(w, (0, 0), "qwen3vl_8b_int8_convrot.safetensors", "qwen_image"); qv = vae(w, (0, 0), "qwen_image_2.1_vae_bf16.safetensors")
    return k, qc, qv


def decode(w, lat, v):
    d = w.add("VAEDecode", (0, 0), [("samples", "LATENT"), ("vae", "VAE")], [("IMAGE", "IMAGE")], collapsed=True)
    w.c(lat, 0, d, "samples"); w.c(v, 0, d, "vae")
    return d


def rgb(w, img):
    s = w.add("SplitImageWithAlpha", (0, 0), [("image", "IMAGE")], [("IMAGE", "IMAGE"), ("MASK", "MASK")], [], collapsed=True, title="drop alpha")
    w.c(img, 0, s, "image")
    return s


# =============================================================== EDIT
w = WF()
pr = text(w, (0, 0), "Change the jacket to red leather. Keep her face, pose, hair and the background exactly the same.", "WHAT TO CHANGE", (560, 200))
mdl = combo(w, "Model", ["Qwen Image 2.1 Turbo edit (fast, follows instructions)", "FLUX 3 Klein edit (top quality, keeps identity)"])
pic = load_image(w, "PICTURE")
use2 = boolean(w, "Use a second picture")
pic2 = load_image(w, "SECOND PICTURE (optional)")
unc = boolean(w, "Uncensored (Qwen only)")
seed = seed_node(w)
# Qwen
qm, qc, qv = qwen_model(w, unc)
ins = [("clip", "CLIP"), ("images.image_1", "IMAGE", "o"), ("images.image_2", "IMAGE", "o"), ("vae", "VAE", "o"), ("prompt", "STRING", "w"), ("negative_prompt", "STRING", "w")]
te = w.add("TextEncodeQwenImage21", (0, 0), ins, [("positive", "CONDITIONING"), ("negative", "CONDITIONING"), ("latent", "LATENT")], ["", "", 1024], collapsed=True, title="Qwen edit encode")
w.c(qc, 0, te, "clip"); w.c(pic, 0, te, "images.image_1"); w.c(gate(w, pic2, use2, "IMAGE"), 0, te, "images.image_2"); w.c(qv, 0, te, "vae"); w.c(pr, 0, te, "prompt")
q_img = decode(w, qwen_sampler(w, (qm, 0), (te, 0), (te, 1), (te, 2), seed), qv)
# FLUX 3 Klein
gu = unet(w, (0, 0), "flux3-image-klein-int8-convrot.safetensors"); gc = clip(w, (0, 0), "qwen3vl_4b_int8_convrot.safetensors", "flux3"); gv = vae(w, (0, 0), "flux2-vae.safetensors")
gp = w.add("CLIPTextEncode", (0, 0), [("clip", "CLIP"), ("text", "STRING", "w")], [("CONDITIONING", "CONDITIONING")], [""], collapsed=True); w.c(gc, 0, gp, "clip"); w.c(pr, 0, gp, "text")
gn = w.add("ConditioningZeroOut", (0, 0), [("conditioning", "CONDITIONING")], [("CONDITIONING", "CONDITIONING")], collapsed=True); w.c(gp, 0, gn, "conditioning")
rml = w.add("ReferenceMultiLatent", (0, 0), [("positive", "CONDITIONING"), ("negative", "CONDITIONING", "o"), ("vae", "VAE", "o"), ("images.image0", "IMAGE", "o"), ("images.image1", "IMAGE", "o")],
            [("positive", "CONDITIONING"), ("negative", "CONDITIONING"), ("empty_latent", "LATENT")], ["resize", 1], collapsed=True, title="FLUX 3 references")
w.c(gp, 0, rml, "positive"); w.c(gn, 0, rml, "negative"); w.c(gv, 0, rml, "vae"); w.c(pic, 0, rml, "images.image0"); w.c(gate(w, pic2, use2, "IMAGE"), 0, rml, "images.image1")
gk = w.add("KSampler", (0, 0), [("model", "MODEL"), ("positive", "CONDITIONING"), ("negative", "CONDITIONING"), ("latent_image", "LATENT"), ("seed", "INT", "w")], [("LATENT", "LATENT")], [0, "randomize", 16, 1, "euler", "simple", 1], collapsed=True, title="FLUX 3 sampler")
w.c(gu, 0, gk, "model"); w.c(rml, 0, gk, "positive"); w.c(rml, 1, gk, "negative"); w.c(rml, 2, gk, "latent_image"); w.c(seed, 0, gk, "seed")
g_img = decode(w, gk, gv)
isq = math(w, (0, 0), "a == 0", "Qwen?"); w.c(mdl, 1, isq, "values.a")
out = swtch(w, "IMAGE", (g_img, 0), (q_img, 0), isq, 2, "which model")
pre = concat(w, "Images", model_name(w, mdl), "/", "file name")
sv = save_image(w, out, pre)
fix_seed(w, seed)
make_app(as_dict(w), "Edit", [(pr, "value", "What to change"), (mdl, "choice", "Model"), (pic, "image", "Picture"), (use2, "value", "Use a second picture"),
                              (pic2, "image", "Second picture (e.g. an outfit or a person to add)"), (unc, "value", "Uncensored (Qwen only)"), (seed, "value", "Seed")], [sv])

# =============================================================== INPAINT
w = WF()
pr = text(w, (0, 0), "a red silk scarf", "WHAT TO PUT THERE", (560, 160))
pic = load_image(w, "PICTURE")
msk = w.add("LoadImageMask", (0, 0), [], [("MASK", "MASK")], ["ref_couture_model.png", "red", "image"], title="MASK (paint the area to change)", size=(400, 420))
unc = boolean(w, "Uncensored")
seed = seed_node(w)
sc = w.add("ImageScaleToTotalPixels", (0, 0), [("image", "IMAGE")], [("IMAGE", "IMAGE")], ["lanczos", 2.0, 32], collapsed=True, title="max 2 MP"); w.c(pic, 0, sc, "image")
mi = w.add("MaskToImage", (0, 0), [("mask", "MASK")], [("IMAGE", "IMAGE")], [], collapsed=True); w.c(msk, 0, mi, "mask")
ms = w.add("ImageScaleToTotalPixels", (0, 0), [("image", "IMAGE")], [("IMAGE", "IMAGE")], ["lanczos", 2.0, 32], collapsed=True, title="mask to 2 MP"); w.c(mi, 0, ms, "image")
mk = w.add("ImageToMask", (0, 0), [("image", "IMAGE")], [("MASK", "MASK")], ["red"], collapsed=True); w.c(ms, 0, mk, "image")
gm = w.add("GrowMask", (0, 0), [("mask", "MASK")], [("MASK", "MASK")], [8, True], collapsed=True, title="grow mask"); w.c(mk, 0, gm, "mask")
qm, qc, qv = qwen_model(w, unc)
ins = [("clip", "CLIP"), ("images.image_1", "IMAGE", "o"), ("vae", "VAE", "o"), ("prompt", "STRING", "w"), ("negative_prompt", "STRING", "w")]
te = w.add("TextEncodeQwenImage21", (0, 0), ins, [("positive", "CONDITIONING"), ("negative", "CONDITIONING"), ("latent", "LATENT")], ["", "", 1024], collapsed=True, title="Qwen encode")
w.c(qc, 0, te, "clip"); w.c(sc, 0, te, "images.image_1"); w.c(qv, 0, te, "vae"); w.c(pr, 0, te, "prompt")
ve = w.add("VAEEncode", (0, 0), [("pixels", "IMAGE"), ("vae", "VAE")], [("LATENT", "LATENT")], collapsed=True); w.c(sc, 0, ve, "pixels"); w.c(qv, 0, ve, "vae")
nm = w.add("SetLatentNoiseMask", (0, 0), [("samples", "LATENT"), ("mask", "MASK")], [("LATENT", "LATENT")], collapsed=True); w.c(ve, 0, nm, "samples"); w.c(gm, 0, nm, "mask")
dec = decode(w, qwen_sampler(w, (qm, 0), (te, 0), (te, 1), (nm, 0), seed), qv)
r3 = rgb(w, dec)
cm = w.add("ImageCompositeMasked", (0, 0), [("destination", "IMAGE"), ("source", "IMAGE"), ("mask", "MASK", "o")], [("IMAGE", "IMAGE")], [0, 0, False], collapsed=True, title="paste back")
w.c(sc, 0, cm, "destination"); w.c(r3, 0, cm, "source"); w.c(gm, 0, cm, "mask")
sv = save_image(w, cm, prefix="Images/Inpaint")
fix_seed(w, seed)
make_app(as_dict(w), "Inpaint", [(pic, "image", "Picture"), (msk, "image", "Mask"), (pr, "value", "What to put there"), (unc, "value", "Uncensored"), (seed, "value", "Seed")], [sv])

# =============================================================== UPSCALE (picture)
moss = json.load(open('/mnt/user-data/outputs/MOSS-Image Text to Image (storyboard).json'))
SG4 = copy.deepcopy([s for s in moss['definitions']['subgraphs'] if s['name'].startswith('Upscale')][0]); SG4['name'] = "Upscale to 4K (SeedVR2 7B)"
SG2 = copy.deepcopy(SG4); SG2['id'] = "2b2b2b2b-0000-4000-8000-00000000c002"; SG2['name'] = "Upscale to 2K (SeedVR2 7B)"
for n in SG2['nodes']:
    if n['type'] == 'ResizeImageMaskNode': n['widgets_values'] = ["scale longer dimension", 2560, "lanczos"]
w = WF()
pic = load_image(w, "PICTURE")
size = combo(w, "Size", ["2K (2560 px long side)", "4K (3840 px long side)"])
r3 = rgb(w, pic)
u2 = w.add(SG2["id"], (0, 0), [("image", "IMAGE")], [("images", "IMAGE")], [], collapsed=True, title="Upscale 2K"); w.c(r3, 0, u2, "image")
u4 = w.add(SG4["id"], (0, 0), [("image", "IMAGE")], [("images", "IMAGE")], [], collapsed=True, title="Upscale 4K"); w.c(r3, 0, u4, "image")
for u in (u2, u4): w.by[u]["properties"] = {"proxyWidgets": [], "cnr_id": "comfy-core", "ver": "0.27.0"}
is2 = math(w, (0, 0), "a == 0", "2K?"); w.c(size, 1, is2, "values.a")
out = swtch(w, "IMAGE", (u4, 0), (u2, 0), is2, 2, "2K or 4K")
sv = save_image(w, out, prefix="Images/Upscaled")
make_app(as_dict(w, [SG4, SG2]), "Upscale", [(pic, "image", "Picture"), (size, "choice", "Size")], [sv])

# =============================================================== UPSCALE VIDEO (existing SeedVR2 3B workflow)
uv = json.load(open('/mnt/user-data/outputs/Upscale Video to 1080p (SeedVR2).json'))
uv['nodes'] = [n for n in uv['nodes'] if n['type'] != 'MarkdownNote']
for n in uv['nodes']:
    if n['type'] == 'SaveVideo': n['widgets_values'][0] = 'Videos/Upscaled 1080p'; sv_id = n['id']
    if n['type'] == 'LoadVideo': n['widgets_values'][0] = 'test_shot_00001_.mp4'; lv_id = n['id']
uv.setdefault('extra', {})
make_app(uv, "Upscale video", [(lv_id, "file", "Video (a 480p shot)")], [sv_id])

# =============================================================== REMOVE BACKGROUND
w = WF()
pic = load_image(w, "PICTURE")
bm = w.add("LoadBackgroundRemovalModel", (0, 0), [], [("bg_model", "BACKGROUND_REMOVAL")], ["birefnet.safetensors"], collapsed=True)
rb = w.add("RemoveBackground", (0, 0), [("image", "IMAGE"), ("bg_removal_model", "BACKGROUND_REMOVAL")], [("mask", "MASK")], [], collapsed=True)
w.c(pic, 0, rb, "image"); w.c(bm, 0, rb, "bg_removal_model")
inv = w.add("InvertMask", (0, 0), [("mask", "MASK")], [("MASK", "MASK")], [], collapsed=True); w.c(rb, 0, inv, "mask")
jn = w.add("JoinImageWithAlpha", (0, 0), [("image", "IMAGE"), ("alpha", "MASK")], [("IMAGE", "IMAGE")], [], collapsed=True); w.c(pic, 0, jn, "image"); w.c(inv, 0, jn, "alpha")
sv = save_image(w, jn, prefix="Images/Cutout")
make_app(as_dict(w), "Remove background", [(pic, "image", "Picture")], [sv])

# =============================================================== SMOOTH MOTION
w = WF()
lv = w.add("LoadVideo", (0, 0), [], [("VIDEO", "VIDEO")], ["test_shot_00001_.mp4", "image"], title="VIDEO", size=(400, 420))
mult = combo(w, "Smoothness", ["2x frames (24 → 48 fps)", "3x frames", "4x frames (slow-motion ready)"])
gvc = w.add("GetVideoComponents", (0, 0), [("video", "VIDEO")], [("images", "IMAGE"), ("audio", "AUDIO"), ("fps", "FLOAT"), ("bit_depth", "COMBO"), ("color_space", "COMBO")], [], collapsed=True)
w.c(lv, 0, gvc, "video")
im = w.add("FrameInterpolationModelLoader", (0, 0), [], [("INTERP_MODEL", "INTERP_MODEL")], ["film_net_fp16.safetensors"], collapsed=True)
mm = math(w, (0, 0), "a + 2", "multiplier"); w.c(mult, 1, mm, "values.a")
fi = w.add("FrameInterpolate", (0, 0), [("interp_model", "INTERP_MODEL"), ("images", "IMAGE"), ("multiplier", "INT", "w")], [("IMAGE", "IMAGE")], [2], collapsed=True)
w.c(im, 0, fi, "interp_model"); w.c(gvc, 0, fi, "images"); w.c(mm, 1, fi, "multiplier")
fps = w.add("ComfyMathExpression", (0, 0), [("values.a", "FLOAT,INT,BOOLEAN"), ("values.b", "FLOAT,INT,BOOLEAN", "o")], [("FLOAT", "FLOAT"), ("INT", "INT"), ("BOOL", "BOOLEAN")], ["a * (b + 2)"], collapsed=True, title="new fps")
w.c(gvc, 2, fps, "values.a"); w.c(mult, 1, fps, "values.b")
cv = w.add("CreateVideo", (0, 0), [("images", "IMAGE"), ("audio", "AUDIO", "o"), ("fps", "FLOAT", "w")], [("VIDEO", "VIDEO")], [30, 8, "sRGB", "auto", "none"], collapsed=True)
w.c(fi, 0, cv, "images"); w.c(gvc, 1, cv, "audio"); w.c(fps, 0, cv, "fps")
svv = w.add("SaveVideo", (900, 0), [("video", "VIDEO"), ("filename_prefix", "STRING", "w")], [("video", "VIDEO")], ["Videos/Smooth", "auto", "auto"], title="RESULT", size=(560, 640))
w.c(cv, 0, svv, "video")
make_app(as_dict(w), "Smooth motion", [(lv, "file", "Video"), (mult, "choice", "Smoothness")], [svv])
