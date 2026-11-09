import sys, json; sys.path.insert(0,'.')
from wf import *
w=WF()
note(w,(-640,-40),"""## Video — pick a model (480p, with sound)

1. **Model**
   - **MiniMax H3 · References**: up to 6 reference images (characters, location, props, storyboard frame) + up to 3 voice/dialogue audio clips. Best for consistency. Tag refs in the prompt as `<Picture 1>`, `<Audio 1>` (numbered by active slots).
   - **MiniMax H3 · First frame / text**: Image 1 becomes the exact first frame (bypass it for pure text-to-video). Audio refs are ignored.
   - **Wan 2.2 · Image to video**: Image 1 becomes the first frame (keep it on). 16 fps, no sound. Turbo = 4 steps with the lightx2v speed LoRA; Full = 20 steps. Best at 5 s; longer clips can drift.
   - **LTX-2.5 · Image to video** (default): Image 1 is the first frame (keep it on). Renders at 720p (16:9 1280×720, 9:16 720×1280, 1:1 960×960, 2.39:1 1536×640) with generated sound, 24 fps. Quality and Color settings don't apply. About 6 min for 3 s at 960 px on this PC.
   - **MiniMax H3 · First + last frame**: Image 1 is the first frame and Image 2 the last frame (turn both on); the shot moves from one to the other.
   - **Uncensored LoRA**: applies to Wan 2.2 only.
2. **Aspect**, **Duration**, **Quality** (Turbo = drafts, Full = finals ~5× slower), **Color** (SDR / HDR).
3. Turn each reference on/off with the **Use …** switch above it (Image 1 is on by default; in 'First frame' mode turn it off for pure text-to-video).
4. Run. Output: `Pictures\\Comfyui\\Videos`, named after the model used (MP4; H3 has sound). For 1080p, run the result through an upscale step (ask Claude).

Only the chosen model loads. LTX-2.5 can be added once its text encoder is downloaded (needs your Hugging Face login).
Time on this PC: ~15–30 min per 5 s clip (Wan 2.2: expect similar or longer; first run loads two 14 GB models).

Everything else (loaders, samplers, switches) is packed inside the small **ENGINE** box tucked behind the result. No need to touch it.""","HOW TO USE",(600,600))
def combo(pos,title,opts,default=0):
    return w.add("CustomCombo",pos,[],[("STRING","STRING"),("INDEX","INT")],[opts[default],default]+opts+[""],title=title,size=(340,60+30*len(opts)),color=True)
mdl=combo((0,0),"Model",["MiniMax H3 References (images + voice)","MiniMax H3 First frame (or text only)","Wan 2.2 Image to video (Image 1 = first frame, no sound)","LTX-2.5 Image to video (Image 1 = first frame, sound, 720p)","MiniMax H3 First + last frame (Image 1 = start, Image 2 = end)"],3)
unc=w.add("PrimitiveBoolean",(0,0),[],[("BOOLEAN","BOOLEAN")],[False],title="Uncensored LoRA (Wan 2.2 only)",size=(340,60),color=True)
asp=combo((0,130),"Aspect (480p)",["16:9 (864x480)","9:16 (480x864)","1:1 (480x480)","2.39:1 (1152x480)"])
dur=combo((0,320),"Duration",["5 s","8 s","10 s","15 s"])
q=combo((0,510),"Quality",["Turbo (draft)","Full 20 steps (final)"])
col=combo((0,640),"Color",["SDR (standard)","HDR (HLG, 10-bit)"])
seed=w.add("PrimitiveInt",(0,770),[],[("INT","INT")],[0,"randomize"],title="Seed",size=(340,80),color=True)
pr=text(w,(360,0),"<Picture 1> is the main character. <Picture 2> is the location.\nDescribe the shot: camera, action, mood, lighting.\nDialogue: <Picture 1> says \"...\" in the voice of <Audio 1>.","PROMPT",(1050,330))
EY=1640
W_=math(w,(0,0),"864 if a == 0 else (480 if a == 1 else (480 if a == 2 else 1152))","W"); H_=math(w,(0,0),"480 if a == 0 else (864 if a == 1 else 480)","H")
for m in (W_,H_): w.c(asp,1,m,"values.a")
L=math(w,(0,0),"124 if a == 0 else (192 if a == 1 else (243 if a == 2 else 362))","Frames"); w.c(dur,1,L,"values.a")
tb=math(w,(0,0),"a == 0","Turbo?"); w.c(q,1,tb,"values.a")
s0=math(w,(0,0),"4 if a == 0 else 20","Steps refs"); s1=math(w,(0,0),"8 if a == 0 else 20","Steps first-frame")
for m in (s0,s1): w.c(q,1,m,"values.a")
hc=clip(w,(0,0),"qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors","minimax"); hv=vae(w,(0,0),"minimax_h3_video_vae_int8_convrot.safetensors"); ha=vae(w,(0,0),"minimax_h3_audio_vae_fp32.safetensors")
# refs
names=["Character 1","Character 2","Location","Prop","Prop 2","Storyboard frame"]
imgs=[]; itg=[]
for k in range(6):
    x=360+(k%3)*350; y=340+(k//3)*390
    li=w.add("LoadImage",(x,y),[],[("IMAGE","IMAGE"),("MASK","MASK")],["ref_couture_model.png","image"],title=f"Image {k+1}: {names[k]}",size=(330,360))
    imgs.append(li)
    tg=w.add("PrimitiveBoolean",(x,y),[],[("BOOLEAN","BOOLEAN")],[k==0],title=f"Use image {k+1}",size=(330,60),color=True); itg.append(tg)
auds=[]; atg=[]
for k in range(3):
    la=w.add("LoadAudio",(0,900+k*160),[],[("AUDIO","AUDIO")],["voice_placeholder.wav",None,None],title=f"Audio {k+1}: dialogue / voice",size=(330,140))
    auds.append(la)
    tg=w.add("PrimitiveBoolean",(0,0),[],[("BOOLEAN","BOOLEAN")],[False],title=f"Use audio {k+1}",size=(330,60),color=True); atg.append(tg)
def branch(model_file,lora_file,steps_node,cond_node,lat_slot):
    u=unet(w,(0,0),model_file); lo=lora(w,(0,0),lora_file); w.c(u,0,lo,"model")
    ms=w.add("ComfySwitchNode",(0,0),[("on_false","MODEL"),("on_true","MODEL"),("switch","BOOLEAN","w")],[("output","MODEL")],[True],collapsed=True,title="Turbo LoRA?")
    w.c(u,0,ms,"on_false"); w.c(lo,0,ms,"on_true"); w.c(tb,2,ms,"switch")
    nz=w.add("RandomNoise",(0,0),[("noise_seed","INT","w")],[("NOISE","NOISE")],[0,"randomize"],collapsed=True); w.c(seed,0,nz,"noise_seed")
    ks=w.add("KSamplerSelect",(0,0),[],[("SAMPLER","SAMPLER")],["res_multistep"],collapsed=True)
    sc=w.add("BasicScheduler",(0,0),[("model","MODEL"),("steps","INT","w")],[("SIGMAS","SIGMAS")],["simple",4,1],collapsed=True)
    gd=w.add("BasicGuider",(0,0),[("model","MODEL"),("conditioning","CONDITIONING")],[("GUIDER","GUIDER")],collapsed=True)
    sa=w.add("SamplerCustomAdvanced",(0,0),[("noise","NOISE"),("guider","GUIDER"),("sampler","SAMPLER"),("sigmas","SIGMAS"),("latent_image","LATENT")],[("output","LATENT"),("denoised_output","LATENT")],collapsed=True)
    w.c(ms,0,sc,"model"); w.c(steps_node,1,sc,"steps"); w.c(ms,0,gd,"model"); w.c(cond_node,0,gd,"conditioning")
    w.c(nz,0,sa,"noise"); w.c(gd,0,sa,"guider"); w.c(ks,0,sa,"sampler"); w.c(sc,0,sa,"sigmas"); w.c(cond_node,1,sa,"latent_image")
    return sa
# branch 0: references
ins=[("clip","CLIP"),("vae","VAE"),("audio_vae","VAE")]+[(f"ref_images.ref_image_{i}","IMAGE","o") for i in range(6)]+[(f"ref_audios.ref_audio_{i}","AUDIO","o") for i in range(3)]+[("prompt","STRING","w"),("width","INT","w"),("height","INT","w"),("length","INT","w")]
r2v=w.add("MiniMaxH3ReferenceToVideo",(0,0),ins,[("positive","CONDITIONING"),("LATENT","LATENT")],["",864,480,124,"match"],collapsed=True,title="H3 references")
w.c(hc,0,r2v,"clip"); w.c(hv,0,r2v,"vae"); w.c(ha,0,r2v,"audio_vae"); w.c(pr,0,r2v,"prompt"); w.c(W_,1,r2v,"width"); w.c(H_,1,r2v,"height"); w.c(L,1,r2v,"length")
def gate(src,tog,typ):
    s_=w.add("ComfySwitchNode",(0,0),[("on_false",typ),("on_true",typ),("switch","BOOLEAN","w")],[("output",typ)],[False],collapsed=True,title="use?")
    w.c(src,0,s_,"on_true"); w.c(tog,0,s_,"switch"); return s_
for i,li in enumerate(imgs): w.c(gate(li,itg[i],"IMAGE"),0,r2v,f"ref_images.ref_image_{i}")
for i,la in enumerate(auds): w.c(gate(la,atg[i],"AUDIO"),0,r2v,f"ref_audios.ref_audio_{i}")
b0=branch("minimax_h3_ref2va_pruned_int8_convrot.safetensors","minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",s0,r2v,1)
# branch 1: first frame
i2v=w.add("MiniMaxH3ImageToVideo",(0,0),[("clip","CLIP"),("vae","VAE"),("first_frame","IMAGE","o"),("last_frame","IMAGE","o"),("prompt","STRING","w"),("width","INT","w"),("height","INT","w"),("length","INT","w")],[("positive","CONDITIONING"),("LATENT","LATENT")],["",864,480,124],collapsed=True,title="H3 first frame")
w.c(hc,0,i2v,"clip"); w.c(hv,0,i2v,"vae"); w.c(pr,0,i2v,"prompt"); w.c(W_,1,i2v,"width"); w.c(H_,1,i2v,"height"); w.c(L,1,i2v,"length")
rs=w.add("ImageScaleToTotalPixels",(0,0),[("image","IMAGE")],[("IMAGE","IMAGE")],["lanczos",0.41,32],collapsed=True); w.c(imgs[0],0,rs,"image"); w.c(gate(rs,itg[0],"IMAGE"),0,i2v,"first_frame")
rs2=w.add("ImageScaleToTotalPixels",(0,0),[("image","IMAGE")],[("IMAGE","IMAGE")],["lanczos",0.41,32],collapsed=True); w.c(imgs[1],0,rs2,"image")
lastf=w.add("ComfyMathExpression",(0,0),[("values.a","FLOAT,INT,BOOLEAN"),("values.b","FLOAT,INT,BOOLEAN","o")],[("FLOAT","FLOAT"),("INT","INT"),("BOOL","BOOLEAN")],["b and a == 4"],collapsed=True,title="last frame used?")
w.c(mdl,1,lastf,"values.a"); w.c(itg[1],0,lastf,"values.b")
lsw2=w.add("ComfySwitchNode",(0,0),[("on_false","IMAGE"),("on_true","IMAGE"),("switch","BOOLEAN","w")],[("output","IMAGE")],[False],collapsed=True,title="last frame?")
w.c(rs2,0,lsw2,"on_true"); w.c(lastf,2,lsw2,"switch"); w.c(lsw2,0,i2v,"last_frame")
b1=branch("minimax_h3_fl2va_pruned_int8_convrot.safetensors","minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",s1,i2v,1)
mf=math(w,(0,0),"a == 0","Refs model?"); w.c(mdl,1,mf,"values.a")
lsw=w.add("ComfySwitchNode",(0,0),[("on_false","LATENT"),("on_true","LATENT"),("switch","BOOLEAN","w")],[("output","LATENT")],[True],collapsed=True,title="Which model")
w.c(b1,0,lsw,"on_false"); w.c(b0,0,lsw,"on_true"); w.c(mf,2,lsw,"switch")
vd=w.add("VAEDecode",(0,0),[("samples","LATENT"),("vae","VAE")],[("IMAGE","IMAGE")],collapsed=True); w.c(lsw,0,vd,"samples"); w.c(hv,0,vd,"vae")
ad=w.add("VAEDecodeAudio",(0,0),[("samples","LATENT"),("vae","VAE")],[("AUDIO","AUDIO")],collapsed=True); w.c(lsw,0,ad,"samples"); w.c(ha,0,ad,"vae")
c1=w.add("CreateVideo",(0,0),[("images","IMAGE"),("audio","AUDIO","o")],[("VIDEO","VIDEO")],[24,8,"sRGB","auto","none"],collapsed=True,title="SDR video")
w.c(vd,0,c1,"images"); w.c(ad,0,c1,"audio")
hd=w.add("ImageSDRToHDR",(0,0),[("image","IMAGE")],[("IMAGE","IMAGE")],["lottes",650],collapsed=True); w.c(vd,0,hd,"image")
c2=w.add("CreateVideo",(0,0),[("images","IMAGE"),("audio","AUDIO","o")],[("VIDEO","VIDEO")],[24,10,"HDR","auto","auto"],collapsed=True,title="HDR video")
w.c(hd,0,c2,"images"); w.c(ad,0,c2,"audio")
hf=math(w,(0,0),"a == 1","HDR?"); w.c(col,1,hf,"values.a")
vs=w.add("ComfySwitchNode",(0,0),[("on_false","VIDEO"),("on_true","VIDEO"),("switch","BOOLEAN","w")],[("output","VIDEO")],[False],collapsed=True,title="SDR/HDR")
w.c(c1,0,vs,"on_false"); w.c(c2,0,vs,"on_true"); w.c(hf,2,vs,"switch")

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
sv=w.add("SaveVideo",(1420,0),[("video","VIDEO"),("filename_prefix","STRING","w")],[("video","VIDEO")],["Videos/shot","auto","auto"],title="RESULT",size=(720,700))
w.c(file_prefix("Videos",mdl,("b and a == 2",unc)),0,sv,"filename_prefix")
# ---- Wan 2.2 14B image-to-video (16 fps, no audio) ----
def mswitch(cond,cslot,on_false,on_true,typ,title):
    s=w.add("ComfySwitchNode",(0,0),[("on_false",typ),("on_true",typ),("switch","BOOLEAN","w")],[("output",typ)],[False],collapsed=True,title=title)
    w.c(on_false,0,s,"on_false"); w.c(on_true,0,s,"on_true"); w.c(cond,cslot,s,"switch"); return s
def wan_model(unet_file,nsfw_file,light_file,tag):
    u=unet(w,(0,0),unet_file)
    n_=lora(w,(0,0),nsfw_file,1.0); w.c(u,0,n_,"model")
    m1=mswitch(unc,0,u,n_,"MODEL",f"Wan {tag} uncensored?")
    l_=lora(w,(0,0),light_file,1.0); w.c(m1,0,l_,"model")
    m2=mswitch(tb,2,m1,l_,"MODEL",f"Wan {tag} turbo?")
    sh=w.add("ModelSamplingSD3",(0,0),[("model","MODEL")],[("MODEL","MODEL")],[5.0],collapsed=True); w.c(m2,0,sh,"model"); return sh
wh=wan_model("wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors","wan2.2_i2v_general_nsfw_high_noise.safetensors","wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors","high")
wl=wan_model("wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors","wan2.2_i2v_general_nsfw_low_noise.safetensors","wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors","low")
wc=clip(w,(0,0),"umt5_xxl_fp8_e4m3fn_scaled.safetensors","wan"); wv=vae(w,(0,0),"wan_2.1_vae.safetensors")
wp=w.add("CLIPTextEncode",(0,0),[("clip","CLIP"),("text","STRING","w")],[("CONDITIONING","CONDITIONING")],[""],collapsed=True,title="Wan +"); w.c(wc,0,wp,"clip"); w.c(pr,0,wp,"text")
wn=w.add("CLIPTextEncode",(0,0),[("clip","CLIP")],[("CONDITIONING","CONDITIONING")],["色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"],collapsed=True,title="Wan -"); w.c(wc,0,wn,"clip")
wf_=math(w,(0,0),"81 if a == 0 else (129 if a == 1 else (161 if a == 2 else 241))","Wan frames"); w.c(dur,1,wf_,"values.a")
wi=w.add("WanImageToVideo",(0,0),[("positive","CONDITIONING"),("negative","CONDITIONING"),("vae","VAE"),("clip_vision_output","CLIP_VISION_OUTPUT","o"),("start_image","IMAGE","o"),("width","INT","w"),("height","INT","w"),("length","INT","w")],[("positive","CONDITIONING"),("negative","CONDITIONING"),("latent","LATENT")],[640,640,81,1],collapsed=True)
w.c(wp,0,wi,"positive"); w.c(wn,0,wi,"negative"); w.c(wv,0,wi,"vae"); w.c(gate(imgs[0],itg[0],"IMAGE"),0,wi,"start_image"); w.c(W_,1,wi,"width"); w.c(H_,1,wi,"height"); w.c(wf_,1,wi,"length")
wst=math(w,(0,0),"4 if a == 0 else 20","Wan steps"); wsp=math(w,(0,0),"2 if a == 0 else 10","Wan split"); wcf=math(w,(0,0),"1.0 if a == 0 else 3.5","Wan cfg")
for m in (wst,wsp,wcf): w.c(q,1,m,"values.a")
k1=w.add("KSamplerAdvanced",(0,0),[("model","MODEL"),("positive","CONDITIONING"),("negative","CONDITIONING"),("latent_image","LATENT"),("noise_seed","INT","w"),("steps","INT","w"),("cfg","FLOAT","w"),("end_at_step","INT","w")],[("LATENT","LATENT")],["enable",0,"randomize",4,1,"euler","simple",0,2,"enable"],collapsed=True,title="Wan high-noise")
w.c(wh,0,k1,"model"); w.c(wi,0,k1,"positive"); w.c(wi,1,k1,"negative"); w.c(wi,2,k1,"latent_image"); w.c(seed,0,k1,"noise_seed"); w.c(wst,1,k1,"steps"); w.c(wcf,0,k1,"cfg"); w.c(wsp,1,k1,"end_at_step")
k2=w.add("KSamplerAdvanced",(0,0),[("model","MODEL"),("positive","CONDITIONING"),("negative","CONDITIONING"),("latent_image","LATENT"),("steps","INT","w"),("cfg","FLOAT","w"),("start_at_step","INT","w")],[("LATENT","LATENT")],["disable",0,"fixed",4,1,"euler","simple",2,10000,"disable"],collapsed=True,title="Wan low-noise")
w.c(wl,0,k2,"model"); w.c(wi,0,k2,"positive"); w.c(wi,1,k2,"negative"); w.c(k1,0,k2,"latent_image"); w.c(wst,1,k2,"steps"); w.c(wcf,0,k2,"cfg"); w.c(wsp,1,k2,"start_at_step")
wd=w.add("VAEDecode",(0,0),[("samples","LATENT"),("vae","VAE")],[("IMAGE","IMAGE")],collapsed=True); w.c(k2,0,wd,"samples"); w.c(wv,0,wd,"vae")
wv1=w.add("CreateVideo",(0,0),[("images","IMAGE"),("audio","AUDIO","o")],[("VIDEO","VIDEO")],[16,8,"sRGB","auto","none"],collapsed=True,title="Wan SDR video"); w.c(wd,0,wv1,"images")
whd=w.add("ImageSDRToHDR",(0,0),[("image","IMAGE")],[("IMAGE","IMAGE")],["lottes",650],collapsed=True); w.c(wd,0,whd,"image")
wv2=w.add("CreateVideo",(0,0),[("images","IMAGE"),("audio","AUDIO","o")],[("VIDEO","VIDEO")],[16,10,"HDR","auto","auto"],collapsed=True,title="Wan HDR video"); w.c(whd,0,wv2,"images")
wvs=mswitch(hf,2,wv1,wv2,"VIDEO","Wan SDR/HDR")
isw=math(w,(0,0),"a == 2","Wan model?"); w.c(mdl,1,isw,"values.a")
fin=mswitch(isw,2,vs,wvs,"VIDEO","H3 or Wan")
# ---- LTX-2.5 (official image-to-video subgraph, distilled, 2-stage, with sound) ----
import copy as _cp
_lt=json.load(open('ltx25_template.json')); LSG=_lt['definitions']['subgraphs'][0]
_tn=[x for x in _lt['nodes'] if x['id']==398][0]
w.nid+=1; L=_cp.deepcopy(_tn); L['id']=w.nid; L['pos']=[0,0]; L['flags']={'collapsed':True}; L['title']='LTX-2.5 image to video'
for _i in L['inputs']: _i['link']=None
L['inputs'].append({"name":"noise_seed","type":"INT","widget":{"name":"noise_seed"},"link":None})
L['outputs'][0]['links']=[]; L['widgets_values'][1]=False
w.nodes.append(L); w.by[L['id']]=L
w.c(gate(imgs[0],itg[0],"IMAGE"),0,L['id'],'input'); w.c(pr,0,L['id'],'value')
_ld=math(w,(0,0),"5 if a == 0 else (8 if a == 1 else (10 if a == 2 else 15))","LTX seconds"); w.c(dur,1,_ld,"values.a"); w.c(_ld,1,L['id'],'value_2')
_lw=math(w,(0,0),"1280 if a == 0 else (720 if a == 1 else (960 if a == 2 else 1536))","LTX width"); _lh=math(w,(0,0),"720 if a == 0 else (1280 if a == 1 else (960 if a == 2 else 640))","LTX height")
for _m in (_lw,_lh): w.c(asp,1,_m,"values.a")
w.c(_lw,1,L['id'],'value_3'); w.c(_lh,1,L['id'],'value_4'); w.c(seed,0,L['id'],'noise_seed')
_isL=math(w,(0,0),"a == 3","LTX model?"); w.c(mdl,1,_isL,"values.a")
fin2=mswitch(_isL,2,fin,L['id'],"VIDEO","LTX?")
w.c(fin2,0,sv,"video")
ya=stack(w,[mdl,asp,dur,q,col,seed,unc],0)
yau=stack(w,[x for p in zip(atg,auds) for x in p],0,ya+40)
for k,li in enumerate(imgs):
    x=360+(k%3)*350; y=380+(k//3)*508
    w.by[itg[k]]["pos"]=[x,y]; w.by[li]["pos"]=[x,y+104]
yau=max(yau,380+2*508)
for k in (3,4,5): w.by[imgs[k]]["size"][1]=yau-44-(380+508+104)
w.group("OPTIONS",[-10,-40,370,ya+10],"#8A8")
w.group("VOICE AUDIO (References mode only)",[-10,ya,370,yau-ya],"#b58b2a")
w.group("PROMPT & REFERENCE IMAGES (Image 1 = first frame in 'First frame' mode)",[350,-40,1060,yau],"#a1309b")
w.by[sv]["size"][1]=yau-44
w.group("RESULT",[1410,-40,740,yau],"#3f789e")
keep=[n["id"] for n in w.nodes if not n.get("flags",{}).get("collapsed")]
d=pack(w,keep,pos=(1700,300),extra_defs=[LSG])
d["extra"]["ds"]={"scale":0.5,"offset":[660,60]}
json.dump(d,open(sys.argv[1],"w"))
print(len(d["nodes"]),len(d["links"]),len(d["definitions"]["subgraphs"][0]["nodes"]))
