import sys, json, copy; sys.path.insert(0,'.')
from wf import *
moss=json.load(open('/mnt/user-data/outputs/MOSS-Image Text to Image (storyboard).json'))
SG4=[s for s in moss['definitions']['subgraphs'] if s['name'].startswith('Upscale')][0]
SG4=copy.deepcopy(SG4); SG4['name']="Upscale to 4K (SeedVR2 7B)"
SG2=copy.deepcopy(SG4); SG2['id']="2b2b2b2b-0000-4000-8000-00000000c002"; SG2['name']="Upscale to 2K (SeedVR2 7B)"
for n in SG2['nodes']:
    if n['type']=='ResizeImageMaskNode': n['widgets_values']=["scale longer dimension",2560,"lanczos"]
MODELS=["MOSS-Image (5B, sharp detail, text in images)","Qwen Image 2.1 Turbo (fast, great prompt following)","Krea 2 Turbo (fast, stylish)","FLUX 3 Klein (top quality, references)","Z-Image Turbo (fast, photoreal)","Anime Desire (Illustrious, anime)","Anima (anime & illustration, uncensored)"]
w=WF()
note(w,(-640,-40),"""## Image — any model → up to 4K

One workflow for every image model on this PC.

1. **Model**: pick from the dropdown. Only the chosen model loads and runs.
2. **Prompt** (+ optional **Negative**, used by MOSS and Anime Desire). Anime Desire likes Danbooru-style tags; quality tags are added for you.
3. **Aspect** and **Size**: Small ≈0.5 MP, Medium ≈1 MP, Large ≈2 MP (Anime Desire renders at its native size, then a built-in second pass sharpens it).
4. **Steps**: 0 = the model's recommended default.
5. **Reference image** (optional): turn on **Use reference image** and load an image. Most models use it as a starting point (keeps composition, colors, pose); **Reference strength** sets how closely (0.2 loose, 0.9 very close). **FLUX 3 Klein** and **Qwen 2.1** instead treat it as a true reference (keeps the subject's identity/look in a new scene) and ignores strength.
6. **Upscale**: None, 2K (long side 2560 px) or 4K (3840 px) with SeedVR2.
7. **Uncensored**: off by default. On = MOSS-Image and Qwen 2.1 get their uncensored LoRA; Krea 2 Turbo switches to the uncensored Krea 2 merge (Moody Krea2 Mix v3 + abliterated text encoder). Other models ignore it.
8. **Enhance prompt**: off by default. On = a small local LLM (Qwen3-VL 4B) rewrites your prompt first. Adds roughly 5–15 min on this PC; the rewritten prompt shows in the "Prompt used" box.

Output: `Pictures\\Comfyui\\Images`, named after the model used (e.g. `Qwen Image 2.1 Turbo_00001_.png`, with `uncensored` added when that switch was used). One file, upscaled if chosen.

Everything else (loaders, samplers, switches) is packed inside the small **ENGINE** box tucked behind the result. No need to touch it.

Rough times on this PC (1 MP): Anime Desire ~2–3 min · Krea 2 ~2 min · Qwen 2.1 Turbo ~3–5 min · MOSS ~6 min · FLUX 3 Klein ~3–6 min · Z-Image Turbo ~1–2 min · Anima ~2–4 min.""","HOW TO USE",(600,620))
def combo(id_pos,title,opts,default=0,h=None):
    return w.add("CustomCombo",id_pos,[],[("STRING","STRING"),("INDEX","INT")],[opts[default],default]+opts+[""],title=title,size=(360,h or 60+30*len(opts)),color=True)
mdl=combo((0,0),"Model",MODELS,0)
asp=combo((0,250),"Aspect",["16:9 widescreen","9:16 vertical","1:1 square","4:3","3:4","2.39:1 scope"],0)
siz=combo((0,500),"Size",["Small (~0.5 MP)","Medium (~1 MP)","Large (~2 MP)"],1)
up=combo((0,1050),"Upscale",["None","2K (2560 px)","4K (3840 px)"],0)
enh=w.add("PrimitiveBoolean",(0,980),[],[("BOOLEAN","BOOLEAN")],[False],title="Enhance prompt (slow)",size=(360,60),color=True)
seed=w.add("PrimitiveInt",(0,660),[],[("INT","INT")],[0,"randomize"],title="Seed",size=(360,80),color=True)
stp=w.add("PrimitiveInt",(0,750),[],[("INT","INT")],[0,"fixed"],title="Steps (0 = model default)",size=(360,80),color=True)
refon=w.add("PrimitiveBoolean",(0,840),[],[("BOOLEAN","BOOLEAN")],[False],title="Use reference image",size=(360,60),color=True)
refst=w.add("PrimitiveFloat",(0,910),[],[("FLOAT","FLOAT")],[0.6],title="Reference strength (0.2 loose · 0.9 very close)",size=(360,60),color=True)
unc=w.add("PrimitiveBoolean",(0,0),[],[("BOOLEAN","BOOLEAN")],[False],title="Uncensored (MOSS · Qwen 2.1 · Krea 2)",size=(360,60),color=True)
refimg=w.add("LoadImage",(370,500),[],[("IMAGE","IMAGE"),("MASK","MASK")],["ref_couture_model.png","image"],title="REFERENCE IMAGE (used only when 'Use reference image' is on)",size=(640,520),color=True)
pr=text(w,(370,0),"Cinematic film still, a lone lighthouse keeper standing on wet black rocks at dusk, a storm rolling in over the sea, warm lantern light against cold blue clouds, 35mm lens, shallow depth of field, film grain.","PROMPT",(640,300))
ng=text(w,(370,320),"worst quality, low quality, blurry, bad anatomy, extra fingers, watermark, text, logo","NEGATIVE (MOSS · Anime Desire)",(640,160))
EY=1460
R="(16/9 if a == 0 else (9/16 if a == 1 else (1 if a == 2 else (4/3 if a == 3 else (3/4 if a == 4 else 2.39)))))"
def dims(mp,title):
    m=f"({mp})"
    W=w.add("ComfyMathExpression",(20,EY),[("values.a","FLOAT,INT,BOOLEAN"),("values.b","FLOAT,INT,BOOLEAN","o")],[("FLOAT","FLOAT"),("INT","INT"),("BOOL","BOOLEAN")],[f"max(256, round(sqrt({m} * 1000000 * {R}) / 64) * 64)"],title=title+" W",collapsed=True)
    H=w.add("ComfyMathExpression",(20,EY),[("values.a","FLOAT,INT,BOOLEAN"),("values.b","FLOAT,INT,BOOLEAN","o")],[("FLOAT","FLOAT"),("INT","INT"),("BOOL","BOOLEAN")],[f"max(256, round(sqrt({m} * 1000000 / {R}) / 64) * 64)"],title=title+" H",collapsed=True)
    for n in (W,H): w.c(asp,1,n,"values.a"); w.c(siz,1,n,"values.b")
    return W,H
BW,BH=dims("0.5 if b == 0 else (1.0 if b == 1 else 2.0)","Big")
XW,XH=dims("0.6 if b == 0 else (0.9 if b == 1 else 1.1)","SDXL")
def steps(D,t):
    s=math(w,(20,EY),f"a if a > 0 else {D}",t); w.c(stp,0,s,"values.a"); return s
dn=w.add("ComfyMathExpression",(20,EY),[("values.a","FLOAT,INT,BOOLEAN"),("values.b","FLOAT,INT,BOOLEAN","o")],[("FLOAT","FLOAT"),("INT","INT"),("BOOL","BOOLEAN")],["(1 - a) if b else 1.0"],title="denoise",collapsed=True)
w.c(refst,0,dn,"values.a"); w.c(refon,0,dn,"values.b")
def scaled(W,H):
    i=w.add("ImageScale",(20,EY),[("image","IMAGE"),("width","INT","w"),("height","INT","w")],[("IMAGE","IMAGE")],["lanczos",1024,1024,"center"],collapsed=True)
    w.c(refimg,0,i,"image"); w.c(W,1,i,"width"); w.c(H,1,i,"height"); return i
def refsw(on_true,on_false,typ):
    sw_=w.add("ComfySwitchNode",(20,EY),[("on_false",typ),("on_true",typ),("switch","BOOLEAN","w")],[("output",typ)],[False],collapsed=True,title="Ref?")
    w.c(on_false,0,sw_,"on_false"); w.c(on_true,0,sw_,"on_true"); w.c(refon,0,sw_,"switch"); return sw_
def venc(img,vae_):
    e=w.add("VAEEncode",(20,EY),[("pixels","IMAGE"),("vae","VAE")],[("LATENT","LATENT")],collapsed=True)
    w.c(img,0,e,"pixels"); w.c(vae_[0],vae_[1],e,"vae"); return e
# prompt enhancer (lazy)
qe=clip(w,(20,EY+40),"qwen3vl_4b_fp8_scaled.safetensors","krea2")
sysp=w.add("PrimitiveStringMultiline",(20,EY+40),[],[("STRING","STRING")],["You are an expert prompt engineer for text-to-image models. Rewrite the user's request as one rich, concrete image description in English: subject, action, setting, composition and camera, lighting, color palette, materials and style. Keep every specific detail the user gave (names, text to render, counts). Output only the final prompt, no preamble, under 180 words."],title="Enhancer instructions",collapsed=True)
tg=w.add("TextGenerate",(20,EY+40),[("clip","CLIP"),("prompt","STRING","w"),("system_prompt","STRING","o")],[("generated_text","STRING"),("thinking","STRING")],["",400,"on",0.7,64,0.95,0.05,1.05,0,0,False,True,"auto"],collapsed=True,title="Prompt enhancer")
w.c(qe,0,tg,"clip"); w.c(pr,0,tg,"prompt"); w.c(sysp,0,tg,"system_prompt")
psw=w.add("ComfySwitchNode",(20,EY+40),[("on_false","STRING"),("on_true","STRING"),("switch","BOOLEAN","w")],[("output","STRING")],[False],collapsed=True,title="Use enhanced prompt?")
w.c(pr,0,psw,"on_false"); w.c(tg,0,psw,"on_true"); w.c(enh,0,psw,"switch")
pv=w.add("PreviewAny",(1020,700),[("source","*")],[("STRING","STRING")],[],title="Prompt used",size=(560,200))
w.c(psw,0,pv,"source")
P=psw
def enc(clipn,src=None,title="encode"):
    e=w.add("CLIPTextEncode",(20,EY),[("clip","CLIP"),("text","STRING","w")],[("CONDITIONING","CONDITIONING")],[""],collapsed=True,title=title)
    w.c(clipn,0,e,"clip"); w.c(src if src else P,0,e,"text"); return e
def ks(model,pos,neg,lat,seedn,stepsn,cfg,sampler,sched,title):
    k=w.add("KSampler",(20,EY),[("model","MODEL"),("positive","CONDITIONING"),("negative","CONDITIONING"),("latent_image","LATENT"),("seed","INT","w"),("steps","INT","w"),("denoise","FLOAT","w")],[("LATENT","LATENT")],[0,"randomize",20,cfg,sampler,sched,1],collapsed=True,title=title)
    w.c(model[0],model[1],k,"model"); w.c(pos,0,k,"positive"); w.c(neg,0,k,"negative"); w.c(lat,0,k,"latent_image"); w.c(seedn,0,k,"seed"); w.c(stepsn,1,k,"steps"); w.c(dn,0,k,"denoise"); return k
def dec(lat,vae_):
    d=w.add("VAEDecode",(20,EY),[("samples","LATENT"),("vae","VAE")],[("IMAGE","IMAGE")],collapsed=True)
    w.c(lat,0,d,"samples"); w.c(vae_[0],vae_[1],d,"vae"); return d
def latent(t,W,H,vae_=None):
    l=w.add(t,(20,EY),[("width","INT","w"),("height","INT","w")],[("LATENT","LATENT")],[1024,1024,1],collapsed=True)
    w.c(W,1,l,"width"); w.c(H,1,l,"height")
    if vae_ is None: return l
    return refsw(venc(scaled(W,H),vae_),l,"LATENT")
outs=[]
def mswitch(cond_src,on_false,on_true,typ,title):
    s=w.add("ComfySwitchNode",(20,EY),[("on_false",typ),("on_true",typ),("switch","BOOLEAN","w")],[("output",typ)],[False],collapsed=True,title=title)
    w.c(on_false[0],on_false[1],s,"on_false"); w.c(on_true[0],on_true[1],s,"on_true"); w.c(cond_src,0,s,"switch"); return s
def unc_model(base,lora_file,st,title):
    lo=w.add("LoraLoaderModelOnly",(20,EY),[("model","MODEL")],[("MODEL","MODEL")],[lora_file,st],collapsed=True,title=title)
    w.c(base[0],base[1],lo,"model"); return mswitch(unc,base,(lo,0),"MODEL",title+"?")
def concat(a_text,src,title,a_first=True):
    c=w.add("StringConcatenate",(20,EY),[("string_a","STRING","w"),("string_b","STRING","w")],[("STRING","STRING")],[a_text,"",", "],collapsed=True,title=title)
    w.c(src,0,c,"string_b"); return c
# MOSS (+ optional uncensored LoRA, trigger moss_xvstyle)
mu=unet(w,(20,EY),"moss_image_sft_int8_convrot.safetensors"); mc=clip(w,(20,EY),"qwen3.5_9b_w4a8_gptq.safetensors","moss_image"); mv=vae(w,(20,EY),"flux2-vae_small.safetensors")
mm=unc_model((mu,0),"moss_image_nsfw_uncensored_lora.safetensors",0.8,"MOSS uncensored LoRA")
mp=mswitch(unc,(P,0),(concat("moss_xvstyle",P,"MOSS trigger"),0),"STRING","MOSS trigger?")
outs.append(dec(ks((mm,0),enc(mc,mp,"MOSS +"),enc(mc,ng,"MOSS -"),latent("EmptyFlux2LatentImage",BW,BH,(mv,0)),seed,steps(25,"MOSS steps"),2.5,"er_sde","simple","MOSS sampler"),(mv,0)))
# Qwen Image 2.1 Turbo (+ optional uncensored LoRA). Reference image = native edit reference.
qu=unet(w,(20,EY),"qwen_image_2.1_turbo_int8_convrot.safetensors")
qm=unc_model((qu,0),"qwen_image_2.1_nsfw_lora.safetensors",1.0,"Qwen uncensored LoRA")
qk=w.add("QwenImage21Cache",(20,EY),[("model","MODEL")],[("MODEL","MODEL")],["auto","default"],collapsed=True); w.c(qm,0,qk,"model")
qc=clip(w,(20,EY),"qwen3vl_8b_int8_convrot.safetensors","qwen_image"); qv=vae(w,(20,EY),"qwen_image_2.1_vae_bf16.safetensors")
def qte(ref):
    ins=[("clip","CLIP"),("images.image_1","IMAGE","o"),("vae","VAE","o"),("prompt","STRING","w"),("negative_prompt","STRING","w")]
    t=w.add("TextEncodeQwenImage21",(20,EY),ins,[("positive","CONDITIONING"),("negative","CONDITIONING"),("latent","LATENT")],["","",1024],collapsed=True,title="Qwen encode"+(" + ref" if ref else ""))
    w.c(qc,0,t,"clip"); w.c(P,0,t,"prompt"); w.c(ng,0,t,"negative_prompt")
    if ref: w.c(refimg,0,t,"images.image_1"); w.c(qv,0,t,"vae")
    return t
qa,qb=qte(False),qte(True)
qpos=mswitch(refon,(qa,0),(qb,0),"CONDITIONING","Qwen ref +?"); qneg=mswitch(refon,(qa,1),(qb,1),"CONDITIONING","Qwen ref -?")
qg=w.add("CFGGuider",(20,EY),[("model","MODEL"),("positive","CONDITIONING"),("negative","CONDITIONING")],[("GUIDER","GUIDER")],[1],collapsed=True)
w.c(qk,0,qg,"model"); w.c(qpos,0,qg,"positive"); w.c(qneg,0,qg,"negative")
qsig=w.add("ManualSigmas",(20,EY),[],[("SIGMAS","SIGMAS")],["1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"],collapsed=True,title="Qwen 2.1 Turbo 8-step schedule")
qn=w.add("RandomNoise",(20,EY),[("noise_seed","INT","w")],[("NOISE","NOISE")],[0,"randomize"],collapsed=True); w.c(seed,0,qn,"noise_seed")
qs=w.add("KSamplerSelect",(20,EY),[],[("SAMPLER","SAMPLER")],["euler"],collapsed=True)
qsa=w.add("SamplerCustomAdvanced",(20,EY),[("noise","NOISE"),("guider","GUIDER"),("sampler","SAMPLER"),("sigmas","SIGMAS"),("latent_image","LATENT")],[("output","LATENT"),("denoised_output","LATENT")],collapsed=True)
w.c(qn,0,qsa,"noise"); w.c(qg,0,qsa,"guider"); w.c(qs,0,qsa,"sampler"); w.c(qsig,0,qsa,"sigmas"); w.c(latent("EmptyLatentImage",BW,BH),0,qsa,"latent_image")
outs.append(dec(qsa,(qv,0)))
# Krea 2
ku0=unet(w,(20,EY),"krea2_turbo_fp8_scaled.safetensors"); ku1=unet(w,(20,EY),"moodyKrea2Mix_v3-Global-FP8.safetensors")
ku=mswitch(unc,(ku0,0),(ku1,0),"MODEL","Krea 2 uncensored model?")
kc0=clip(w,(20,EY),"qwen3vl_4b_fp8_scaled.safetensors","krea2"); kc1=clip(w,(20,EY),"qwen3VL4BAbliteratedComfyui_FP8.safetensors","krea2")
kc=mswitch(unc,(kc0,0),(kc1,0),"CLIP","Krea 2 uncensored encoder?")
kv=vae(w,(20,EY),"qwen_image_vae.safetensors")
kp=enc(kc,title="Krea +"); kz=w.add("ConditioningZeroOut",(20,EY),[("conditioning","CONDITIONING")],[("CONDITIONING","CONDITIONING")],collapsed=True); w.c(kp,0,kz,"conditioning")
outs.append(dec(ks((ku,0),kp,kz,latent("EmptyLatentImage",BW,BH,(kv,0)),seed,steps(8,"Krea steps"),1,"euler","simple","Krea sampler"),(kv,0)))
# FLUX 3 Image Klein (distilled, 16 steps, cfg 1). Reference image = true multi-reference (ReferenceMultiLatent).
gu=unet(w,(20,EY),"flux3-image-klein-int8-convrot.safetensors"); gc=clip(w,(20,EY),"qwen3vl_4b_int8_convrot.safetensors","flux3"); gv=vae(w,(20,EY),"flux2-vae.safetensors")
gp=enc(gc,title="FLUX 3 +")
gn=w.add("ConditioningZeroOut",(20,EY),[("conditioning","CONDITIONING")],[("CONDITIONING","CONDITIONING")],collapsed=True); w.c(gp,0,gn,"conditioning")
rml=w.add("ReferenceMultiLatent",(20,EY),[("positive","CONDITIONING"),("negative","CONDITIONING","o"),("vae","VAE","o"),("images.image0","IMAGE","o")],[("positive","CONDITIONING"),("negative","CONDITIONING"),("empty_latent","LATENT")],["resize",1],collapsed=True,title="FLUX 3 reference")
w.c(gp,0,rml,"positive"); w.c(gn,0,rml,"negative"); w.c(gv,0,rml,"vae"); w.c(refimg,0,rml,"images.image0")
gpos=mswitch(refon,(gp,0),(rml,0),"CONDITIONING","FLUX 3 ref +?"); gneg=mswitch(refon,(gn,0),(rml,1),"CONDITIONING","FLUX 3 ref -?")
gk=w.add("KSampler",(20,EY),[("model","MODEL"),("positive","CONDITIONING"),("negative","CONDITIONING"),("latent_image","LATENT"),("seed","INT","w"),("steps","INT","w")],[("LATENT","LATENT")],[0,"randomize",16,1,"euler","simple",1],collapsed=True,title="FLUX 3 sampler")
w.c(gu,0,gk,"model"); w.c(gpos,0,gk,"positive"); w.c(gneg,0,gk,"negative"); w.c(latent("EmptyFlux2LatentImage",BW,BH),0,gk,"latent_image"); w.c(seed,0,gk,"seed"); w.c(steps(16,"FLUX 3 steps"),1,gk,"steps")
outs.append(dec(gk,(gv,0)))
# Z-Image Turbo (8 steps, cfg 1, res_multistep, AuraFlow shift 3). Reference image = starting point.
zu=unet(w,(20,EY),"z_image_turbo_int8_convrot.safetensors"); zc=clip(w,(20,EY),"qwen_3_4b_fp8_mixed.safetensors","lumina2"); zv=vae(w,(20,EY),"ae.safetensors")
zms=w.add("ModelSamplingAuraFlow",(20,EY),[("model","MODEL")],[("MODEL","MODEL")],[3],collapsed=True,title="Z-Image shift"); w.c(zu,0,zms,"model")
zp=enc(zc,title="Z-Image +"); zz=w.add("ConditioningZeroOut",(20,EY),[("conditioning","CONDITIONING")],[("CONDITIONING","CONDITIONING")],collapsed=True); w.c(zp,0,zz,"conditioning")
outs.append(dec(ks((zms,0),zp,zz,latent("EmptySD3LatentImage",BW,BH,(zv,0)),seed,steps(8,"Z-Image steps"),1,"res_multistep","simple","Z-Image sampler"),(zv,0)))
# SDXL checkpoints with a light hires pass
def sdxl(name,t,cfg,sampler,sched,stepsD,pos_pre,neg_pre,hdn):
    ck=w.add("CheckpointLoaderSimple",(20,EY),[],[("MODEL","MODEL"),("CLIP","CLIP"),("VAE","VAE")],[name],collapsed=True,title=t)
    ptxt=concat(pos_pre,P,t+" quality tags") if pos_pre else P
    ntxt=concat(neg_pre,ng,t+" negative tags")
    p=w.add("CLIPTextEncode",(20,EY),[("clip","CLIP"),("text","STRING","w")],[("CONDITIONING","CONDITIONING")],[""],collapsed=True); w.c(ck,1,p,"clip"); w.c(ptxt,0,p,"text")
    n_=w.add("CLIPTextEncode",(20,EY),[("clip","CLIP"),("text","STRING","w")],[("CONDITIONING","CONDITIONING")],[""],collapsed=True); w.c(ck,1,n_,"clip"); w.c(ntxt,0,n_,"text")
    first=dec(ks((ck,0),p,n_,latent("EmptyLatentImage",XW,XH,(ck,2)),seed,steps(stepsD,t+" steps"),cfg,sampler,sched,t+" sampler"),(ck,2))
    hs=w.add("ImageScaleBy",(20,EY),[("image","IMAGE")],[("IMAGE","IMAGE")],["lanczos",1.3],collapsed=True,title=t+" hires x1.3"); w.c(first,0,hs,"image")
    he=venc(hs,(ck,2))
    k2=w.add("KSampler",(20,EY),[("model","MODEL"),("positive","CONDITIONING"),("negative","CONDITIONING"),("latent_image","LATENT"),("seed","INT","w")],[("LATENT","LATENT")],[0,"randomize",20,cfg,"dpmpp_2m","karras",hdn],collapsed=True,title=t+" hires pass")
    w.c(ck,0,k2,"model"); w.c(p,0,k2,"positive"); w.c(n_,0,k2,"negative"); w.c(he,0,k2,"latent_image"); w.c(seed,0,k2,"seed")
    outs.append(dec(k2,(ck,2)))
sdxl("anime_desire_illustrious_v3_fp16.safetensors","Anime Desire",4.5,"dpmpp_3m_sde","karras",30,"masterpiece, best quality, ultra-detailed, intricate details, highres","worst quality, low quality, normal quality, lowres, bad anatomy, bad hands, jpeg artifacts, signature, watermark, username, blurry",0.35)
# Anima (2B anime model, Qwen3 0.6B encoder, Qwen-Image VAE; 30 steps, cfg 4, euler/simple)
au=unet(w,(20,EY),"anima-base-v1.0.safetensors"); ac=clip(w,(20,EY),"qwen_3_06b_base.safetensors","stable_diffusion"); av=vae(w,(20,EY),"qwen_image_vae.safetensors")
ap=concat("masterpiece, best quality, score_7",P,"Anima quality tags")
an=concat("worst quality, low quality, score_1, score_2, score_3, blurry, jpeg artifacts",ng,"Anima negative tags")
outs.append(dec(ks((au,0),enc(ac,ap,"Anima +"),enc(ac,an,"Anima -"),latent("EmptyLatentImage",BW,BH,(av,0)),seed,steps(30,"Anima steps"),4,"euler","simple","Anima sampler"),(av,0)))
# select model: lazy switch chain
def sw(cond_expr,src_idx,on_true,on_false,title,typ="IMAGE"):
    f=math(w,(20,EY),cond_expr,title+" flag"); w.c(src_idx[0],src_idx[1],f,"values.a")
    s=w.add("ComfySwitchNode",(20,EY),[("on_false",typ),("on_true",typ),("switch","BOOLEAN","w")],[("output",typ)],[False],collapsed=True,title=title)
    w.c(on_false[0],on_false[1],s,"on_false"); w.c(on_true[0],on_true[1],s,"on_true"); w.c(f,2,s,"switch"); return s
cur=(outs[-1],0)
for k in range(len(outs)-2,-1,-1):
    cur=(sw(f"a == {k}",(mdl,1),(outs[k],0),cur,f"model {k}?"),0)
base=cur
u4=w.add(SG4["id"],(20,EY),[("image","IMAGE")],[("images","IMAGE")],[],collapsed=True,title="Upscale 4K"); w.c(base[0],base[1],u4,"image")
u2=w.add(SG2["id"],(20,EY),[("image","IMAGE")],[("images","IMAGE")],[],collapsed=True,title="Upscale 2K"); w.c(base[0],base[1],u2,"image")
for u in (u4,u2): w.by[u]["properties"]={"proxyWidgets":[],"cnr_id":"comfy-core","ver":"0.27.0"}
s2=sw("a == 1",(up,1),(u2,0),(u4,0),"2K or 4K")
fin=sw("a == 0",(up,1),base,(s2,0),"Upscale?")

def file_prefix(folder,model_combo,model_idx_expr_unc=None):
    rx=w.add("RegexExtract",(0,0),[("string","STRING","w")],[("STRING","STRING")],["",r"^[^(]*[^ (]","First Match",True,False,False,1],collapsed=True,title="model name")
    w.c(model_combo,0,rx,"string"); name=rx
    if model_idx_expr_unc:
        expr,uncnode=model_idx_expr_unc
        f=w.add("ComfyMathExpression",(0,0),[("values.a","FLOAT,INT,BOOLEAN"),("values.b","FLOAT,INT,BOOLEAN","o")],[("FLOAT","FLOAT"),("INT","INT"),("BOOL","BOOLEAN")],[expr],collapsed=True,title="uncensored used?")
        w.c(model_combo,1,f,"values.a"); w.c(uncnode,0,f,"values.b")
        c=w.add("StringConcatenate",(0,0),[("string_a","STRING","w"),("string_b","STRING","w")],[("STRING","STRING")],["","uncensored"," "],collapsed=True,title="+ uncensored")
        w.c(rx,0,c,"string_a")
        s_=w.add("ComfySwitchNode",(0,0),[("on_false","STRING"),("on_true","STRING"),("switch","BOOLEAN","w")],[("output","STRING")],[False],collapsed=True,title="tag uncensored?")
        w.c(rx,0,s_,"on_false"); w.c(c,0,s_,"on_true"); w.c(f,2,s_,"switch"); name=s_
    p=w.add("StringConcatenate",(0,0),[("string_a","STRING","w"),("string_b","STRING","w")],[("STRING","STRING")],[folder,"","/"],collapsed=True,title="file name")
    w.c(name,0,p,"string_b"); return p
sv=w.add("SaveImage",(1020,0),[("images","IMAGE"),("filename_prefix","STRING","w")],[],["Images/img"],title="RESULT",size=(560,640))
w.c(file_prefix("Images",mdl,("b and (a == 0 or a == 1 or a == 2)",unc)),0,sv,"filename_prefix")
w.c(fin,0,sv,"images")
ya=stack(w,[mdl,asp,siz,seed,stp,refon,refst,unc,enh,up],0)
w.by[refimg]["size"][1]=ya-44-548
yb=stack(w,[pr,ng,refimg],370)
w.by[pv]["size"][1]=ya-44-684
stack(w,[sv,pv],1020)
fix_seed(w,seed)
w.group("OPTIONS",[-10,-40,380,ya+10],"#8A8")
w.group("PROMPT & REFERENCE IMAGE",[360,-40,660,yb+10],"#a1309b")
w.group("RESULT",[1010,-40,580,ya+10],"#3f789e")
keep=[n["id"] for n in w.nodes if not n.get("flags",{}).get("collapsed")]
d=pack(w,keep,pos=(1200,300),extra_defs=[SG4,SG2])
d["extra"]["ds"]={"scale":0.55,"offset":[660,60]}
json.dump(d,open(sys.argv[1],"w"))
print(len(d["nodes"]),len(d["links"]),len(d["definitions"]["subgraphs"][0]["nodes"]))
