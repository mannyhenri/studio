import json, uuid
P = {"cnr_id": "comfy-core", "ver": "0.33.0"}
class WF:
    def __init__(s): s.nodes=[]; s.links=[]; s.lid=0; s.nid=0; s.by={}; s.groups=[]
    def add(s, type, pos, ins=(), outs=(), wv=None, title=None, size=(320,120), color=False, mode=0, collapsed=False):
        s.nid+=1; n={"id":s.nid,"type":type,"pos":list(pos),"size":list(size),"flags":{"collapsed":True} if collapsed else {},
            "order":s.nid,"mode":mode,"inputs":[],"outputs":[],"properties":dict(P,**{"Node name for S&R":type}),"widgets_values":wv if wv is not None else []}
        for i in ins:
            name,typ=i[0],i[1]; d={"name":name,"type":typ,"link":None}
            if len(i)>2 and 'w' in i[2]: d["widget"]={"name":name}
            if len(i)>2 and 'o' in i[2]: d["shape"]=7
            n["inputs"].append(d)
        for o in outs: n["outputs"].append({"name":o[0],"type":o[1],"links":[]})
        if title: n["title"]=title
        if color: n["color"],n["bgcolor"]="#322","#533"
        s.nodes.append(n); s.by[s.nid]=n; return s.nid
    def c(s, a, aslot, b, bname):
        A,B=s.by[a],s.by[b]; bi=next(i for i,x in enumerate(B["inputs"]) if x["name"]==bname)
        s.lid+=1; s.links.append([s.lid,a,aslot,b,bi,A["outputs"][aslot]["type"]])
        A["outputs"][aslot]["links"].append(s.lid); B["inputs"][bi]["link"]=s.lid
    def group(s,title,b,color="#3f789e"): s.groups.append({"id":len(s.groups)+1,"title":title,"bounding":b,"color":color,"flags":{}})
    def dump(s,path,ds=(0.5,[100,50])):
        json.dump({"id":str(uuid.uuid4()),"revision":0,"last_node_id":s.nid,"last_link_id":s.lid,"nodes":s.nodes,"links":s.links,
                   "groups":s.groups,"config":{},"extra":{"ds":{"scale":ds[0],"offset":ds[1]}},"version":0.4},open(path,"w"),indent=1)
# common node helpers
def unet(w,p,name): return w.add("UNETLoader",p,[],[("MODEL","MODEL")],[name,"default"],collapsed=True)
def clip(w,p,name,t): return w.add("CLIPLoader",p,[],[("CLIP","CLIP")],[name,t,"default"],collapsed=True)
def vae(w,p,name): return w.add("VAELoader",p,[],[("VAE","VAE")],[name],collapsed=True)
def lora(w,p,name,st=1): return w.add("LoraLoaderModelOnly",p,[("model","MODEL")],[("MODEL","MODEL")],[name,st],collapsed=True)
def text(w,p,val,title,size=(640,220)): return w.add("PrimitiveStringMultiline",p,[],[("STRING","STRING")],[val],title=title,size=size,color=True)
def math(w,p,expr,title): return w.add("ComfyMathExpression",p,[("values.a","FLOAT,INT,BOOLEAN"),("values.b","FLOAT,INT,BOOLEAN","o")],[("FLOAT","FLOAT"),("INT","INT"),("BOOL","BOOLEAN")],[expr],title=title,collapsed=True)
def note(w,p,txt,title,size=(560,420)): return w.add("MarkdownNote",p,[],[],[txt],title=title,size=size)
H3 = dict(unet="minimax_h3_fl2va_pruned_int8_convrot.safetensors", te="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
          vv="minimax_h3_video_vae_int8_convrot.safetensors", av="minimax_h3_audio_vae_fp32.safetensors",
          lora="minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors")
def h3_shot(w, x, y, clipn, vv, av, model, prompt_node, width, height, length, first=None, seed=0, steps=8, title="Shot"):
    """returns (images_node, audio_node)"""
    i2v=w.add("MiniMaxH3ImageToVideo",(x,y),[("clip","CLIP"),("vae","VAE"),("first_frame","IMAGE","o"),("last_frame","IMAGE","o"),
        ("prompt","STRING","w"),("width","INT","w"),("height","INT","w"),("length","INT","w")],[("positive","CONDITIONING"),("LATENT","LATENT")],
        ["",864,480,124],title=f"{title}: H3 image→video",collapsed=True)
    w.c(clipn,0,i2v,"clip"); w.c(vv,0,i2v,"vae"); w.c(prompt_node,0,i2v,"prompt"); w.c(width,1,i2v,"width"); w.c(height,1,i2v,"height"); w.c(length,1,i2v,"length")
    if first: w.c(first,0,i2v,"first_frame")
    nz=w.add("RandomNoise",(x,y+40),[],[("NOISE","NOISE")],[seed,"randomize"],collapsed=True)
    ks=w.add("KSamplerSelect",(x,y+80),[],[("SAMPLER","SAMPLER")],["res_multistep"],collapsed=True)
    sc=w.add("BasicScheduler",(x+260,y),[("model","MODEL"),("steps","INT","w")],[("SIGMAS","SIGMAS")],["simple",steps,1],collapsed=True)
    gd=w.add("BasicGuider",(x+260,y+40),[("model","MODEL"),("conditioning","CONDITIONING")],[("GUIDER","GUIDER")],collapsed=True)
    sa=w.add("SamplerCustomAdvanced",(x+260,y+80),[("noise","NOISE"),("guider","GUIDER"),("sampler","SAMPLER"),("sigmas","SIGMAS"),("latent_image","LATENT")],[("output","LATENT"),("denoised_output","LATENT")],collapsed=True)
    vd=w.add("VAEDecode",(x+520,y),[("samples","LATENT"),("vae","VAE")],[("IMAGE","IMAGE")],collapsed=True)
    ad=w.add("VAEDecodeAudio",(x+520,y+40),[("samples","LATENT"),("vae","VAE")],[("AUDIO","AUDIO")],collapsed=True)
    w.c(model,0,sc,"model"); w.c(model,0,gd,"model"); w.c(i2v,0,gd,"conditioning")
    w.c(nz,0,sa,"noise"); w.c(gd,0,sa,"guider"); w.c(ks,0,sa,"sampler"); w.c(sc,0,sa,"sigmas"); w.c(i2v,1,sa,"latent_image")
    w.c(sa,0,vd,"samples"); w.c(vv,0,vd,"vae"); w.c(sa,0,ad,"samples"); w.c(av,0,ad,"vae")
    return vd, ad

def pack(w, keep, title="ENGINE (double-click to look inside)", sg_id=None, pos=(0,0), extra_defs=()):
    """Move every node not in `keep` into one subgraph node. Returns workflow dict."""
    keep=set(keep); inner=[n for n in w.nodes if n["id"] not in keep]; outer=[n for n in w.nodes if n["id"] in keep]
    inner_ids={n["id"] for n in inner}
    sg_id=sg_id or str(uuid.uuid4()); eid=w.nid+1
    for n in w.nodes:
        for i in n["inputs"]: i["link"]=None
        for o in n["outputs"]: o["links"]=[]
    sin={}; sout={}; ilinks=[]; olinks=[]; il=[0]; ol=[0]
    E={"id":eid,"type":sg_id,"pos":list(pos),"size":[260,60],"flags":{"collapsed":True},"order":0,"mode":0,"inputs":[],"outputs":[],
       "title":title,"properties":{"proxyWidgets":[]},"widgets_values":[]}
    by={n["id"]:n for n in w.nodes}; by[eid]=E
    def link_outer(a,aslot,b,bslot,typ):
        ol[0]+=1; olinks.append([ol[0],a,aslot,b,bslot,typ]); by[a]["outputs"][aslot]["links"].append(ol[0]); by[b]["inputs"][bslot]["link"]=ol[0]
    def link_inner(a,aslot,b,bslot,typ):
        il[0]+=1; ilinks.append({"id":il[0],"origin_id":a,"origin_slot":aslot,"target_id":b,"target_slot":bslot,"type":typ})
        if a>0: by[a]["outputs"][aslot]["links"].append(il[0])
        if b>0: by[b]["inputs"][bslot]["link"]=il[0]
        return il[0]
    SI=[]; SO=[]
    for _,a,aslot,b,bslot,typ in w.links:
        ai,bi=a in inner_ids,b in inner_ids
        if ai and bi: link_inner(a,aslot,b,bslot,typ)
        elif not ai and not bi: link_outer(a,aslot,b,bslot,typ)
        elif bi:  # into engine
            k=(a,aslot)
            if k not in sin:
                nm=(by[a].get("title") or by[a]["type"]).split(" (")[0][:40]
                if any(x["name"]==nm for x in SI): nm=nm+" "+by[a]["outputs"][aslot]["name"].lower()
                sin[k]=len(SI); SI.append({"id":str(uuid.uuid4()),"name":nm,"type":typ,"linkIds":[],"localized_name":nm,"pos":[0,0]})
                E["inputs"].append({"name":nm,"localized_name":nm,"type":typ,"link":None})
                link_outer(a,aslot,eid,sin[k],typ)
            SI[sin[k]]["linkIds"].append(link_inner(-10,sin[k],b,bslot,typ))
        else:     # out of engine
            k=(a,aslot)
            if k not in sout:
                nm={"IMAGE":"image","VIDEO":"video"}.get(typ) or (by[a].get("title") or typ.lower())
                sout[k]=len(SO); SO.append({"id":str(uuid.uuid4()),"name":nm,"type":typ,"linkIds":[],"localized_name":nm,"pos":[0,0]})
                E["outputs"].append({"name":nm,"localized_name":nm,"type":typ,"links":[]})
                SO[sout[k]]["linkIds"].append(link_inner(a,aslot,-20,sout[k],typ))
            link_outer(eid,sout[k],b,bslot,typ)
    # tidy inner layout
    for i,n in enumerate(inner): n["pos"]=[(i%6)*230,(i//6)*40]
    rows=len(inner)//6+1
    for i,s in enumerate(SI): s["pos"]=[-160,20+i*20]
    for i,s in enumerate(SO): s["pos"]=[1420,20+i*20]
    sg={"id":sg_id,"version":1,"state":{"lastGroupId":0,"lastNodeId":max(inner_ids),"lastLinkId":il[0],"lastRerouteId":0},"revision":0,"config":{},
        "name":title,"inputNode":{"id":-10,"bounding":[-340,0,180,40+20*len(SI)]},"outputNode":{"id":-20,"bounding":[1400,0,140,40+20*len(SO)]},
        "inputs":SI,"outputs":SO,"widgets":[],"nodes":inner,"groups":[],"links":ilinks,"extra":{}}
    E["size"]=[260,40+20*max(len(SI),len(SO))]
    return {"id":str(uuid.uuid4()),"revision":0,"last_node_id":eid,"last_link_id":ol[0],"nodes":[E]+outer,"links":olinks,
            "groups":w.groups,"definitions":{"subgraphs":[sg]+list(extra_defs)},"config":{},"extra":{"ds":{"scale":0.6,"offset":[650,40]}},"version":0.4}

def stack(w, ids, x, y0=0, gap=44):
    y=y0
    for i in ids:
        n=w.by[i]; n["pos"]=[x,y]; y+=n["size"][1]+gap
    return y

def fix_seed(w, seed):
    """Route every use of the Seed primitive through abs(), so SDE samplers never get a negative seed."""
    f=w.add("ComfyMathExpression",(0,0),[("values.a","FLOAT,INT,BOOLEAN"),("values.b","FLOAT,INT,BOOLEAN","o")],[("FLOAT","FLOAT"),("INT","INT"),("BOOL","BOOLEAN")],["abs(a)"],collapsed=True,title="seed (always positive)")
    S=w.by[seed]; F=w.by[f]
    for L in w.links:
        if L[1]==seed:
            S["outputs"][L[2]]["links"].remove(L[0]); L[1]=f; L[2]=1; L[5]="INT"; F["outputs"][1]["links"].append(L[0])
    w.c(seed,0,f,"values.a")
