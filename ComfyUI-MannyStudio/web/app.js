/* Studio — a studio front end for this ComfyUI. */
(() => {
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const store = {
  get(k, d) { try { const v = localStorage.getItem("manny." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("manny." + k, JSON.stringify(v)); } catch {} },
};

let clientId = store.get("clientId", null);
if (!clientId) { clientId = "manny-" + Math.random().toString(36).slice(2, 12); store.set("clientId", clientId); }

const S = {
  apps: [], appIdx: store.get("appIdx", 0),
  values: store.get("values", {}),          // appId -> {key: value}
  randomSeed: store.get("randomSeed", {}),  // appId -> {key: bool}
  count: store.get("count", 1),
  char: store.get("char", {}),              // appId -> character id
  chars: [], gallery: [],
  mine: {},                                  // prompt_id -> {app}
  stage: store.get("stage", {}),             // appId -> last result {url, kind}
  running: null, progress: 0, queueRemaining: 0,
  target: store.get("target", "local"),      // where Create renders: "local", "auto" or a worker label
  workers: [], remote: [],                   // GPU machines (this PC first) and their jobs
};
const saveState = () => { store.set("values", S.values); store.set("randomSeed", S.randomSeed); store.set("count", S.count); store.set("char", S.char); store.set("appIdx", S.appIdx); store.set("stage", S.stage); store.set("target", S.target); };

/* ------------------------------------------------------------ api */
async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  const txt = await r.text();
  let data; try { data = JSON.parse(txt); } catch { data = txt; }
  if (r.status === 401) { location.href = "/manny/login?next=" + encodeURIComponent(location.pathname + location.hash); throw new Error("sign in"); }
  if (!r.ok) throw Object.assign(new Error(typeof data === "string" ? data : JSON.stringify(data)), { data });
  return data;
}
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body) });
const viewUrl = (f, sub, type = "output") => `/view?filename=${encodeURIComponent(f)}&subfolder=${encodeURIComponent(sub || "")}&type=${type}`;
const fileUrl = (f, priv) => priv ? `/manny/api/pfile?path=${encodeURIComponent(((f.subfolder ? f.subfolder + "/" : "") + f.filename))}` : viewUrl(f.filename, f.subfolder);
const itemUrl = it => fileUrl(it, it.private);
/* fast previews: the server makes small cached copies, so big (Gigapixel) files open quickly on a phone */
const cq = it => `filename=${encodeURIComponent(it.filename)}&subfolder=${encodeURIComponent(it.subfolder || "")}&m=${Math.round(it.mtime || 0)}${it.private ? "&private=1" : ""}`;
const thumbUrl = (it, w = 640) => `/manny/api/thumb?${cq(it)}&w=${w}`;
const proxyUrl = it => `/manny/api/proxy?${cq(it)}`;
const isTiff = it => /\.tiff?$/i.test(it.filename);
const bigPic = it => isTiff(it) || (it.size || 0) > 3e6;
const bigVid = it => (it.size || 0) > 12e6;
const PQ = () => S.incog ? "?private=1" : "";
const mkey = app => (S.incog ? "P|" : "") + app.id;
const inputUrl = (value) => { const i = value.lastIndexOf("/"); return i < 0 ? viewUrl(value, "", "input") : viewUrl(value.slice(i + 1), value.slice(0, i), "input"); };

async function upload(file) {
  const fd = new FormData();
  fd.append("image", file); fd.append("subfolder", S.incog ? "manny_private" : "manny"); fd.append("type", "input"); fd.append("overwrite", "false");
  const r = await fetch("/upload/image", { method: "POST", body: fd });
  if (!r.ok) throw new Error("Upload failed");
  const d = await r.json();
  return d.subfolder ? `${d.subfolder}/${d.name}` : d.name;
}

/* copy text: the Clipboard API only works on https or this PC (127.0.0.1); over the LAN (http://192.168…) it
   doesn't exist, so fall back to the old select-and-copy trick, and as a last resort show the text selected */
async function copyText(text, what = "Copied") {
  let ok = false;
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); ok = true; } } catch { }
  if (!ok) {
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0";
    document.body.append(ta); ta.focus(); ta.select(); ta.setSelectionRange(0, text.length);
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
  }
  if (ok) { toast(what); return true; }
  // its own layer, so an open viewer underneath stays as it is
  const m = h(`<div class="modal copylayer"></div>`); document.body.append(m);
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>Copy</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">Your browser blocked copying. The text is selected: press Ctrl+C (or long-press › Copy on a phone).</p><textarea rows="8" readonly></textarea></div>`);
  m.append(d); const t = $("textarea", d); t.value = text; t.focus(); t.select();
  const close = () => m.remove();
  $("[data-x]", d).onclick = close; m.onclick = e => { if (e.target === m) close(); };
  return false;
}
function toast(msg, err = false) {
  const t = h(`<div class="t ${err ? "err" : ""}">${esc(msg)}</div>`);
  $("#toast").append(t); setTimeout(() => t.remove(), err ? 7000 : 3200);
}

/* ------------------------------------------------------------ helpers */
const appShort = a => (a?.name || "").replace(/^(Manny\s+)?Studio\s*-\s*/i, "");
const modelOf = fn => fn.replace(/\.[^.]+$/, "").replace(/_\d{4,6}_?$/, "").replace(/_+$/, "");
const fmtDate = t => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const fmtSize = b => b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.round(b / 1e3) + " KB";
function splitOption(o) { const m = String(o).match(/^([^(]+?)\s*\((.+)\)\s*$/); return m ? [m[1], m[2]] : [o, ""]; }

function curApp() { return S.apps[S.appIdx] || S.apps[0]; }
function valuesFor(app) {
  const v = S.values[mkey(app)] || (S.values[mkey(app)] = {});
  for (const i of app.inputs) if (!(i.key in v)) v[i.key] = i.value;
  return v;
}
function seedRand(app, key) { const r = S.randomSeed[app.id] || (S.randomSeed[app.id] = {}); if (!(key in r)) r[key] = true; return r; }

/* classify inputs into sections */
const MEDIA_KINDS = ["image", "audio", "mask", "video"];
const APP_ORDER = ["image", "video", "edit", "inpaint", "upscale", "upscale video", "remove background", "smooth motion"];
const isFixer = a => isTool(a) || /character replace/i.test(appShort(a));
const isTool = a => APP_ORDER.indexOf(appShort(a).toLowerCase()) > 1 || (APP_ORDER.indexOf(appShort(a).toLowerCase()) < 0 && !a.inputs.some(i => i.kind === "text"));
const appIcon = a => ({ edit: "✎", inpaint: "◐", upscale: "⤢", "upscale video": "⤢", "remove background": "✂", "smooth motion": "≋" })[appShort(a).toLowerCase()] || (a.kind === "video" ? "▶" : "▣");
const findApp = name => S.apps.findIndex(a => appShort(a).toLowerCase() === name);
const CAMERA = [["Static", "static locked-off camera"], ["Push in", "slow dolly push-in toward the subject"], ["Pull back", "slow dolly pull-back revealing the scene"],
  ["Pan left", "smooth pan left"], ["Pan right", "smooth pan right"], ["Tilt up", "slow tilt up"], ["Orbit", "camera orbits around the subject"],
  ["Crane up", "crane shot rising up"], ["Crane down", "crane shot descending"], ["Tracking", "tracking shot following the subject"],
  ["Handheld", "handheld camera with natural shake"], ["Drone", "aerial drone flyover"], ["Crash zoom", "fast crash zoom in"],
  ["Dolly zoom", "dolly zoom (vertigo effect)"], ["Low angle", "low-angle hero shot"], ["Close-up", "tight close-up"], ["Slow motion", "slow motion"]];

function layout(app) {
  const L = { prompt: null, negative: null, model: null, settings: [], refs: [], adv: [] };
  const ins = app.inputs;
  for (let k = 0; k < ins.length; k++) {
    const i = ins[k], lab = i.label.toLowerCase();
    if (i.kind === "text") {
      if (lab.includes("negative")) L.negative = i;
      else if (!L.prompt) L.prompt = i; else L.adv.push(i);
    } else if (i.kind === "choice") {
      if (!L.model && lab.includes("model")) L.model = i; else L.settings.push(i);
    } else if (i.kind === "toggle") {
      const nx = ins[k + 1];
      if (/^use\b/i.test(i.label) && nx && MEDIA_KINDS.includes(nx.kind)) { L.refs.push({ media: nx, toggle: i }); k++; }
      else if (lab.includes("enhance")) L.adv.push(i);
      else L.settings.push(i);
    } else if (MEDIA_KINDS.includes(i.kind)) L.refs.push({ media: i, toggle: null });
    else if (i.kind === "float" && lab.includes("reference")) L.refs.strength = i;
    else L.adv.push(i);
  }
  return L;
}

/* ------------------------------------------------------------ router */
const pages = { home: pageHome, create: pageCreate, gallery: pageGallery, characters: pageCharacters, storyboard: pageStoryboard, loras: pageLoras, reel: pageReel, sound: pageSound, projects: pageProjects, "3d": page3D, settings: pageSettings };
function route() {
  const [, page = "home", arg] = location.hash.split("/");
  $$("#nav a, #bnav a").forEach(a => a.classList.toggle("active", a.dataset.page === page));
  const main = $("#main"); main.innerHTML = "";
  const md = $("#modal"); if (md && !md.classList.contains("hidden") && !md.querySelector(".lbox,.lb")) { md.classList.add("hidden"); md.innerHTML = ""; }
  (pages[page] || pageHome)(main, arg);
  main.scrollTop = 0; window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
document.addEventListener("click", e => { if (e.target.closest && e.target.closest("#bmore")) moreMenu(); });

/* ------------------------------------------------------------ HOME */
async function pageHome(main) {
  const p = h(`<div class="page">
    <section class="hero"><div class="bg"></div>
      <div class="eyebrow">Your studio · your models · your GPU</div>
      <h1>What are we<br><span>making</span> today?</h1>
      <p>Pictures and shots from every model on this PC. Pick an app, write what you see, and press Generate. Everything lands in Pictures › Comfyui.</p>
      <div class="credit"></div>
    </section>
    <div class="quick">
      <div class="qcard" data-go="image"><div class="ic">▣</div><h3>New picture</h3><p>MOSS, Qwen, Krea 2, FLUX 3, Anime Desire…</p></div>
      <div class="qcard" data-go="video"><div class="ic">▶</div><h3>New shot</h3><p>LTX-2.5, Wan 2.2, MiniMax H3 with sound</p></div>
      <div class="qcard" data-href="#/characters"><div class="ic">☺</div><h3>Characters</h3><p>Faces and voices you reuse in any shot</p></div>
      <div class="qcard" data-href="#/gallery"><div class="ic">▦</div><h3>Gallery</h3><p>Everything you've made, by model</p></div>
    </div>
    <div class="section-head"><h2>Recent</h2><a href="#/gallery">See all →</a></div>
    <div class="grid small" id="recent"></div>
    <div class="section-head"><h2>Your apps</h2><span class="hint">Build a new one in the node graph with App builder; it appears here automatically.</span></div>
    <div class="grid small" id="apps"></div>
  </div>`);
  main.append(p);
  $$(".qcard", p).forEach(c => c.onclick = () => {
    if (c.dataset.href) location.hash = c.dataset.href;
    else openApp(S.apps.findIndex(a => a.kind === c.dataset.go));
  });
  const ag = $("#apps", p);
  S.apps.forEach((a, k) => {
    const t = h(`<div class="qcard"><div class="ic">${a.kind === "video" ? "▶" : "▣"}</div><h3>${esc(appShort(a))}</h3><p>${a.inputs.length} options · ${a.kind}</p></div>`);
    t.onclick = () => openApp(k); ag.append(t);
  });
  const items = await loadGallery();
  const recent = $("#recent", p);
  if (!items.length) recent.append(h(`<div class="empty">Nothing yet. Your first render will show up here.</div>`));
  { const rl = items.slice(0, 12); rl.forEach(it => recent.append(tile(it, rl))); }
  const hero = items.find(i => i.kind === "image");
  if (hero) { $(".hero .bg", p).style.backgroundImage = `url("${itemUrl(hero)}")`; $(".credit", p).textContent = modelOf(hero.filename); }
}
function openApp(k) { if (k < 0) k = 0; S.appIdx = k; saveState(); location.hash = "#/create/" + k; }

/* ------------------------------------------------------------ CREATE */
function pageCreate(main, arg) {
  if (arg != null && S.apps[+arg]) S.appIdx = +arg;
  const app = curApp();
  if (!app) { main.append(h(`<div class="page"><div class="empty">No apps found. Save a workflow as an app (App builder in the node graph) and reload.</div></div>`)); return; }
  const p = h(`<div class="page">
    <div class="eyebrow">Create</div><h1>${esc(appShort(app))}</h1>
    <div class="apptabs"></div>
    <div class="create"><div class="panel form"></div><div class="stage"></div></div>
  </div>`);
  main.append(p);
  const tabs = $(".apptabs", p);
  const mkTab = (a, k, small) => { const t = h(`<div class="apptab ${small ? "small" : ""} ${k === S.appIdx ? "active" : ""}"><span>${appIcon(a)}</span>${esc(appShort(a))}</div>`); t.onclick = () => openApp(k); return t; };
  S.apps.forEach((a, k) => { if (!isFixer(a)) tabs.append(mkTab(a, k)); });
  const tools = S.apps.map((a, k) => [a, k]).filter(([a]) => isFixer(a));
  if (tools.length) {
    const inTool = isFixer(app);
    const ft = h(`<div class="apptab ${inTool ? "active" : ""}"><span>✦</span>Fix &amp; enhance ${inTool ? "" : "▾"}</div>`);
    const row = h(`<div class="toolrow ${inTool ? "" : "hidden"}"></div>`);
    tools.forEach(([a, k]) => row.append(mkTab(a, k, true)));
    ft.onclick = () => { if (!inTool) row.classList.toggle("hidden"); };
    tabs.append(ft); tabs.after(row);
  }
  renderForm($(".form", p), app);
  renderStage($(".stage", p), app);
}

/* a section that folds away; remembers open/closed per section */
function fold(key, title, summary, content, defOpen) {
  const st = store.get("folds", {}), open = key in st ? st[key] : !!defOpen;
  const d = h(`<details class="fold" ${open ? "open" : ""}><summary><span class="ft">${esc(title)}</span><em class="fs">${esc(summary || "")}</em></summary></details>`);
  d.append(content);
  d.ontoggle = () => { const f = store.get("folds", {}); f[key] = d.open; store.set("folds", f); };
  return d;
}
/* "moss_image_heal_int8_convrot" -> "Moss image heal" */
function prettyFile(n) {
  let t = String(n || "").split(/[\\/]/).pop().replace(/\.[^.]+$/, "");
  t = t.replace(/[_-](int8|fp8|fp16|bf16|fp32|w4a8|w6a8|nf4|gguf|q\d\w*|convrot|row-wise|runtime|mixed|pruned|scaled|e4m3fn|comfyui)(?=$|[_-])/gi, "").replace(/[_-](int8|fp8|fp16|bf16|convrot|pruned|mixed)$/gi, "");
  t = t.replace(/[_]+/g, " ").replace(/\s+/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : n;
}

function renderForm(form, app) {
  const v = valuesFor(app), L = layout(app);
  form.innerHTML = "";
  const set = (key, val) => { v[key] = val; saveState(); };

  if (app.kind === "video" && !isFixer(app)) {
    const kb = h(`<button class="ghost kfbtn">✦ Keyframe shot · several pictures → one shot</button>`);
    kb.onclick = () => keyframeDialog();
    form.append(kb);
  }
  // character + prompt
  const sec = h(`<div class="fsec prompt-box"></div>`);
  if (S.chars.length) {
    const cp = h(`<div><label class="lbl">Character</label><div class="charpick"></div></div>`);
    const box = $(".charpick", cp);
    const mk = (c) => {
      const on = (S.char[app.id] || null) === (c ? c.id : null);
      const el = h(`<div class="cpick ${on ? "on" : ""}">${c ? (c.face ? `<img src="${inputUrl(c.face)}">` : `<span class="av">${esc(c.name[0] || "?")}</span>`) : `<span class="av">∅</span>`}${c ? esc(c.name) : "None"}</div>`);
      el.onclick = () => { selectCharacter(app, c); renderForm(form, app); };
      box.append(el);
    };
    mk(null); S.chars.forEach(mk);
    sec.append(cp);
  }
  if (L.prompt) {
    const ta = h(`<div><div class="lblrow"><label class="lbl">${esc(L.prompt.label)}</label>${app.kind === "video" && !isTool(app) ? `<a href="javascript:void 0" class="pwlink">✎ Prompt writer</a>` : ""}</div><textarea placeholder="Describe the picture: subject, place, light, style…"></textarea></div>`);
    const t = $("textarea", ta); t.value = v[L.prompt.key] ?? "";
    const pwl = $(".pwlink", ta); if (pwl) pwl.onclick = () => promptWriter(t.value, val => { t.value = val; set(L.prompt.key, val); renderForm(form, app); });
    t.oninput = () => set(L.prompt.key, t.value);
    t.onkeydown = e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) generate(app); };
    sec.append(ta);
    if (app.kind === "video" && !isTool(app)) {
      const cam = h(`<div><label class="lbl">Camera move</label><div class="chips cam"></div></div>`);
      CAMERA.forEach(([name, words]) => {
        const on = t.value.includes(words);
        const c = h(`<div class="chip ${on ? "on" : ""}" title="${esc(words)}">${esc(name)}</div>`);
        c.onclick = () => {
          let val = t.value;
          CAMERA.forEach(([, w2]) => { val = val.split(", " + w2).join("").split(w2 + ". ").join("").split(w2).join(""); });
          val = val.trim();
          if (!c.classList.contains("on")) val = (val ? val.replace(/[.\s]*$/, "") + ". " : "") + words + ".";
          t.value = val; set(L.prompt.key, val);
          $$(".cam .chip", cam).forEach(x => x.classList.remove("on")); if (val.includes(words)) c.classList.add("on");
        };
        $(".cam", cam).append(c);
      });
      sec.append(cam);
    }
    const ch = S.chars.find(c => c.id === S.char[app.id]);
    if (ch) sec.append(h(`<div class="hint">${esc(ch.name)} is added to the prompt when you generate${ch.look ? " (“" + esc(ch.look.slice(0, 70)) + (ch.look.length > 70 ? "…" : "") + "”)" : ""}. Write @${esc(ch.name.split(" ")[0])} to place them yourself.</div>`));
  }
  form.append(sec);

  // model
  if (L.model) {
    const uin = uncInput(app), cov = uncCovers(uin, v[L.model.key]);
    const uncOn = uin && v[uin.key];
    const ms = h(`<div class="fsec modelsec ${S.modelOpen ? "" : "collapsed"}"><div class="lblrow"><label class="lbl">${esc(L.model.label)}</label><button class="ghost sm mtoggle">${S.modelOpen ? "Done ▴" : "Change ▾"}</button>${uin ? `<label class="uncsw ${uncOn ? "on" : ""} ${cov ? "" : "na"}" title="${esc(uin.label)}"><input type="checkbox" ${uncOn ? "checked" : ""}><span>🔓 Uncensored</span></label>` : ""}</div>
      ${uin && uncOn && !cov ? `<p class="hint unchint">Uncensored works with ${esc((/\(([^)]*)\)/.exec(uin.label) || [, "some models"])[1])}${app.kind === "image" ? " (Anima always is)" : ""}. Pick one of those.</p>` : ""}
      ${uin && uncOn && cov ? `<p class="hint unchint">On: no content filter, and results are saved privately (Incognito).</p>` : ""}<div class="models"></div></div>`);
    const box = $(".models", ms);
    if (uin) { const sw = $(".uncsw input", ms); sw.onchange = () => { set(uin.key, sw.checked); renderForm(form, app); }; }
    L.model.options.forEach(o => {
      const [title, sub] = splitOption(o);
      const slow = SLOW_HERE.find(([re]) => re.test(o));
      const uc = uin ? uncCovers(uin, o) : false;
      const el = h(`<div class="model ${v[L.model.key] === o ? "on" : ""}"><div class="radio"></div><div><b>${esc(title)}${uc ? ` <i class="unctag" title="${uc === "always" ? "No content filter" : "Can be uncensored with the 🔓 switch"}">🔓</i>` : ""}</b>${sub ? `<span>${esc(sub)}</span>` : ""}${slow ? `<span class="slowtag" title="${esc(slow[2])}">${esc(slow[1])}</span>` : ""}</div></div>`);
      el.onclick = () => { if (!S.modelOpen) { S.modelOpen = true; renderForm(form, app); return; } set(L.model.key, o); if (S.ckpt && S.ckpt[mkey(app)]) { delete S.ckpt[mkey(app)]; store.set("ckpt", S.ckpt); } S.modelOpen = false; renderForm(form, app); };
      box.append(el);
      // styles: finetunes of this model you downloaded (Civitai checkpoints, MOSS-heal, NitroFusion2…)
      if (v[L.model.key] === o && !isTool(app)) {
        const fam = modelFamily(o);
        if (fam && fam !== "sdxl" && !(fam === "h3" && !/reference/i.test(o))) loadVariants().then(list => {
          const mine = list.filter(x => x.family === fam); if (!mine.length) return;
          const cur = ((S.variant[mkey(app)] || {})[fam]) || "";
          const row = h(`<div class="styles"><label class="lbl">Style</label><div class="chips"></div></div>`);
          [{ file: "", title: "Original" }, ...mine].forEach(x => {
            const c = h(`<div class="chip ${cur === x.file ? "on" : ""}" title="${esc(x.file || "The model as Studio ships it")}">${esc(x.title.length > 34 ? x.title.slice(0, 33) + "…" : x.title)}</div>`);
            c.onclick = e => { e.stopPropagation(); const m = S.variant[mkey(app)] || (S.variant[mkey(app)] = {}); if (x.file) m[fam] = x.file; else delete m[fam]; store.set("variant", S.variant); $$(".chip", row).forEach(y => y.classList.toggle("on", y === c)); };
            $(".chips", row).append(c);
          });
          el.after(row);
        });
      }
    });
    if (app.kind === "image" && !isTool(app)) {
      const sdxlOpt = L.model.options.find(o => /anime desire|illustrious/i.test(o));
      if (sdxlOpt) loadCkpts().then(list => {
        if (!list.length) return;
        const cur = (S.ckpt || {})[mkey(app)];
        const g = h(`<div class="ckg"><label class="lbl">Your checkpoints</label></div>`);
        list.forEach(c => {
          const on = cur === c.name && v[L.model.key] === sdxlOpt;
          const el = h(`<div class="model ${on ? "on" : ""}"><div class="radio"></div><div><b>${esc(c.title || prettyFile(c.name))}</b><span>${esc(c.base || "SD")} · runs like Anime Desire (SDXL pipeline)</span></div></div>`);
          el.onclick = () => { if (!S.modelOpen) { S.modelOpen = true; renderForm(form, app); return; } S.ckpt = S.ckpt || {}; S.ckpt[mkey(app)] = c.name; store.set("ckpt", S.ckpt); set(L.model.key, sdxlOpt); S.modelOpen = false; renderForm(form, app); };
          g.append(el);
        });
        box.append(g);
        if (cur && v[L.model.key] === sdxlOpt) $$(".model", box).forEach(x => { if (!x.closest(".ckg")) x.classList.remove("on"); });
      });
      const cmp = h(`<button class="ghost compare-btn">⊞ Compare all ${L.model.options.length} models with this prompt</button>`);
      cmp.onclick = () => compareModels(app);
      ms.append(cmp);
    }
    $(".mtoggle", ms).onclick = () => { S.modelOpen = !S.modelOpen; renderForm(form, app); };
    form.append(ms);
  }
  if (!isTool(app) && L.model) {
    const n = (S.loras[mkey(app)] || []).length;
    form.append(fold("loras", "LoRAs", n ? n + " on" : "none", loraSection(app, form), n > 0));
  }
  if (!isTool(app) && L.model && app.kind === "image" && /anime desire|illustrious/i.test(v[L.model.key] || "")) {
    const pz = (S.pose || {})[mkey(app)];
    form.append(fold("pose", "Pose", pz && pz.file ? pz.file.split("/").pop().replace(/\.[^.]+$/, "") : "off", poseSection(app), !!(pz && pz.file)));
  }

  // settings
  const later = i => /upscale|steps/i.test(i.label) && !isTool(app);
  const quick = L.settings.filter(i => !(i.kind === "toggle" && /uncensored/i.test(i.label) && L.model) && !later(i));
  if (quick.length) {
    const ss = h(`<div class="fsec set ${quick.every(i => i.kind === "choice") ? "quickset" : ""}"></div>`);
    quick.forEach(i => ss.append(control(app, i, v, set)));
    const anchor = $(".modelsec", form);
    if (anchor) anchor.after(ss); else form.append(ss);
  }

  // references
  if (L.refs.length) {
    const rs = h(`<div class="fsec"><label class="lbl">${app.kind === "video" ? "References · images & voices" : "Reference"}</label><div class="refs"></div></div>`);
    const box = $(".refs", rs);
    L.refs.forEach(r => box.append(refTile(app, r, v, set)));
    if (L.refs.strength) rs.append(control(app, L.refs.strength, v, set));
    const optional = L.refs.every(r => r.toggle);
    if (optional && !isTool(app)) {
      const on = L.refs.filter(r => v[r.toggle.key]).length;
      $(".lbl", rs).remove();
      form.append(fold("refs-" + app.kind, app.kind === "video" ? "References · images & voices" : "Reference picture", on ? on + " on" : "off", rs, on > 0));
    } else form.append(rs);
  }

  // advanced
  const adv = [...L.settings.filter(later), ...(L.negative ? [L.negative] : []), ...L.adv];
  if (adv.length) {
    const d = h(`<details class="adv"><summary>More options</summary><div class="fsec"></div></details>`);
    adv.forEach(i => $(".fsec", d).append(control(app, i, v, set)));
    form.append(d);
  }

  // generate bar
  const go = h(`<div class="go">
      <div class="stepper" title="How many"><button data-d="-1">−</button><span>${S.count}</span><button data-d="1">+</button></div>
      <button class="btn primary" id="gen">Generate ${S.count > 1 ? S.count + " " + (app.kind === "video" ? "shots" : "pictures") : ""}</button>
      <button class="btn" id="later" title="Render later: at a set time or when the PC is free">⏱ Later</button>
    </div>`);
  placeRunOn(go);
  $$(".stepper button", go).forEach(b => b.onclick = () => { S.count = Math.max(1, Math.min(8, S.count + +b.dataset.d)); saveState(); renderForm(form, app); });
  $("#gen", go).onclick = () => generate(app);
  $("#later", go).onclick = e => scheduleMenu(app, e.currentTarget);
  form.append(go);
}

function control(app, i, v, set) {
  const lab = esc(i.label);
  if (i.kind === "choice") {
    const opts = i.options;
    if (opts.length <= 8) {
      const el = h(`<div><label class="lbl">${lab}</label><div class="chips"></div></div>`);
      opts.forEach(o => {
        const c = h(`<div class="chip ${v[i.key] === o ? "on" : ""}">${esc(o)}</div>`);
        c.onclick = () => { set(i.key, o); $$(".chip", el).forEach(x => x.classList.remove("on")); c.classList.add("on"); };
        $(".chips", el).append(c);
      });
      return el;
    }
    const el = h(`<div><label class="lbl">${lab}</label><select></select></div>`);
    opts.forEach(o => $("select", el).append(new Option(o, o, false, v[i.key] === o)));
    $("select", el).onchange = e => set(i.key, e.target.value);
    return el;
  }
  if (i.kind === "toggle") {
    const el = h(`<div class="toggle ${v[i.key] ? "on" : ""}"><span>${lab}</span><div class="sw"></div></div>`);
    el.onclick = () => { set(i.key, !v[i.key]); el.classList.toggle("on", !!v[i.key]); };
    return el;
  }
  if (i.kind === "seed") {
    const r = seedRand(app, i.key);
    const el = h(`<div><label class="lbl">${lab}</label><div class="row"><input type="number" value="${esc(v[i.key])}" ${r[i.key] ? "disabled" : ""}><div class="toggle ${r[i.key] ? "on" : ""}" style="flex:none"><span>New each time</span><div class="sw"></div></div></div></div>`);
    $("input", el).oninput = e => set(i.key, +e.target.value || 0);
    $(".toggle", el).onclick = () => { r[i.key] = !r[i.key]; saveState(); $(".toggle", el).classList.toggle("on", r[i.key]); $("input", el).disabled = r[i.key]; };
    return el;
  }
  if (i.kind === "int" || i.kind === "float") {
    const el = h(`<div><label class="lbl">${lab}</label><input type="number" step="${i.kind === "float" ? "0.05" : "1"}" value="${esc(v[i.key])}"></div>`);
    $("input", el).oninput = e => set(i.key, i.kind === "float" ? parseFloat(e.target.value) : parseInt(e.target.value || "0"));
    return el;
  }
  if (i.kind === "text") {
    const el = h(`<div><label class="lbl">${lab}</label><textarea rows="3"></textarea></div>`);
    $("textarea", el).value = v[i.key] ?? ""; $("textarea", el).oninput = e => set(i.key, e.target.value);
    return el;
  }
  return h(`<div class="hint">${lab}: ${esc(v[i.key])}</div>`);
}

function refTile(app, r, v, set) {
  const m = r.media, t = r.toggle;
  const on = t ? !!v[t.key] : true;
  const name = m.label.replace(/^(image|audio)\s*\d*\s*:\s*/i, "");
  const isAudio = m.kind === "audio", isVideo = m.kind === "video", isMask = m.kind === "mask";
  if (isMask) return maskTile(app, r, v, set);
  const el = h(`<div class="ref ${isAudio ? "audio" : ""} ${on ? "on" : ""}" title="${esc(m.label)}">
     <button class="x" title="Remove">✕</button>
     ${on && isVideo ? `<video src="${inputUrl(v[m.key] || "")}" muted loop autoplay playsinline></video>` : ""}
     ${on && !isAudio && !isVideo ? `<img src="${inputUrl(v[m.key] || "")}" onerror="this.remove()">` : ""}
     ${on && isAudio ? `<span class="wave">♪</span>` : `<span class="plus">${on ? "" : "+"}</span>`}
     <span class="rl">${esc(name)}${on && isAudio ? "<br><small>" + esc(String(v[m.key]).split("/").pop()) + "</small>" : ""}</span>
     <input type="file" hidden accept="${isAudio ? "audio/*" : isVideo ? "video/*" : "image/*"}">
   </div>`);
  const fi = $("input", el);
  const take = async (file) => {
    try { const val = await upload(file); set(m.key, val); if (t) set(t.key, true); toast("Added " + file.name); refreshForm(app); }
    catch (e) { toast(e.message, true); }
  };
  el.onclick = e => { if (e.target.closest(".x")) return; fi.click(); };
  fi.onchange = () => fi.files[0] && take(fi.files[0]);
  $(".x", el).onclick = () => { if (t) set(t.key, false); refreshForm(app); };
  el.ondragover = e => { e.preventDefault(); el.classList.add("drag"); };
  el.ondragleave = () => el.classList.remove("drag");
  el.ondrop = e => { e.preventDefault(); el.classList.remove("drag"); const f = e.dataTransfer.files[0]; if (f) take(f); };
  return el;
}
function refreshForm(app) { const f = $(".form"); if (f) renderForm(f, app); }

/* mask painter (Inpaint) */
function maskTile(app, r, v, set) {
  const m = r.media, L = layout(app);
  const pic = L.refs.find(x => x.media.kind === "image");
  const has = v[m.key] && String(v[m.key]).startsWith("manny/mask_");
  const el = h(`<div class="ref ${has ? "on" : ""}" title="Paint the area to change">
      ${has && pic ? `<img src="${inputUrl(v[pic.media.key])}"><img class="maskov" src="${inputUrl(v[m.key])}">` : `<span class="plus">🖌</span>`}
      <span class="rl">${has ? "Mask · click to repaint" : "Paint the area to change"}</span></div>`);
  el.onclick = () => {
    if (!pic || !v[pic.media.key]) return toast("Add the picture first", true);
    painter(inputUrl(v[pic.media.key]), async blob => {
      const file = new File([blob], `mask_${Date.now()}.png`, { type: "image/png" });
      const val = await upload(file); set(m.key, val); toast("Mask saved"); refreshForm(app);
    });
  };
  return el;
}
function painter(src, onSave) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="painter"><div class="ptools">
      <h2>Paint the area to change</h2>
      <label class="lbl">Brush size</label><input type="range" id="bs" min="5" max="200" value="60">
      <div class="row"><button class="ghost" id="pe">Eraser</button><button class="ghost" id="pc">Clear</button></div>
      <p class="hint">Paint over everything that should change. A little extra around the edges blends better.</p>
      <div class="row" style="margin-top:auto"><button class="ghost" data-x>Cancel</button><button class="btn primary" id="ps">Use this mask</button></div>
    </div><div class="pcanvas"><canvas id="cv"></canvas></div></div>`);
  m.append(d);
  const img = new Image(); img.crossOrigin = "anonymous"; img.src = src;
  const cv = $("#cv", d), ctx = cv.getContext("2d");
  const mask = document.createElement("canvas"), mctx = mask.getContext("2d");
  let erase = false, drawing = false, last = null;
  img.onload = () => {
    mask.width = cv.width = img.naturalWidth; mask.height = cv.height = img.naturalHeight; redraw();
  };
  function redraw() {
    ctx.clearRect(0, 0, cv.width, cv.height); ctx.drawImage(img, 0, 0);
    ctx.globalAlpha = .55; ctx.drawImage(tint(), 0, 0); ctx.globalAlpha = 1;
  }
  let tc; function tint() {
    tc = tc || document.createElement("canvas"); tc.width = mask.width; tc.height = mask.height;
    const t = tc.getContext("2d"); t.clearRect(0, 0, tc.width, tc.height); t.drawImage(mask, 0, 0);
    t.globalCompositeOperation = "source-in"; t.fillStyle = "#ff3d6b"; t.fillRect(0, 0, tc.width, tc.height); t.globalCompositeOperation = "source-over"; return tc;
  }
  const pos = e => { const r = cv.getBoundingClientRect(); const pt = e.touches ? e.touches[0] : e; return [(pt.clientX - r.left) * cv.width / r.width, (pt.clientY - r.top) * cv.height / r.height]; };
  const stroke = p => {
    const size = +$("#bs", d).value * cv.width / cv.getBoundingClientRect().width;
    mctx.globalCompositeOperation = erase ? "destination-out" : "source-over";
    mctx.strokeStyle = "#fff"; mctx.lineWidth = size; mctx.lineCap = mctx.lineJoin = "round";
    mctx.beginPath(); mctx.moveTo(...(last || p)); mctx.lineTo(...p); mctx.stroke(); last = p; redraw();
  };
  const down = e => { e.preventDefault(); drawing = true; last = null; stroke(pos(e)); };
  const move = e => { if (drawing) { e.preventDefault(); stroke(pos(e)); } };
  const up = () => { drawing = false; last = null; };
  cv.addEventListener("mousedown", down); cv.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  cv.addEventListener("touchstart", down, { passive: false }); cv.addEventListener("touchmove", move, { passive: false }); cv.addEventListener("touchend", up);
  $("#pe", d).onclick = () => { erase = !erase; $("#pe", d).classList.toggle("on", erase); $("#pe", d).textContent = erase ? "Brush" : "Eraser"; };
  $("#pc", d).onclick = () => { mctx.clearRect(0, 0, mask.width, mask.height); redraw(); };
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; window.removeEventListener("mouseup", up); };
  $$("[data-x]", d).forEach(b => b.onclick = close);
  $("#ps", d).onclick = () => {
    const out = document.createElement("canvas"); out.width = mask.width; out.height = mask.height;
    const o = out.getContext("2d"); o.fillStyle = "#000"; o.fillRect(0, 0, out.width, out.height); o.drawImage(mask, 0, 0);
    out.toBlob(b => { close(); onSave(b); }, "image/png");
  };
}

/* compare all models */
async function compareModels(app) {
  const L = layout(app), seed = Math.floor(Math.random() * 2 ** 31);
  const seedKeys = app.inputs.filter(i => i.kind === "seed").map(i => i.key);
  S.compare = { app: app.id, items: [] };
  for (const o of L.model.options) {
    const vals = { ...buildValues(app), [L.model.key]: o };
    seedKeys.forEach(k => vals[k] = seed);
    try {
      const res = await post("/manny/api/run", { app: app.id, values: vals, count: 1, client_id: clientId, private: S.incog, target: runTarget() });
      const q = res.queued[0]; S.mine[q.prompt_id] = { app: app.id, compare: true };
      S.compare.items.push({ model: splitOption(o)[0], prompt_id: q.prompt_id, url: null });
    } catch (e) { toast("Couldn't queue " + splitOption(o)[0], true); }
  }
  toast(`Comparing ${S.compare.items.length} models (same prompt and seed). They render one after another.`);
  renderStage($(".stage"), app); pollQueue();
}

function selectCharacter(app, c) {
  S.char[app.id] = c ? c.id : null;
  if (c) {
    const v = valuesFor(app), L = layout(app);
    const img = L.refs.find(r => r.media.kind === "image");
    if (img && c.face) { v[img.media.key] = c.face; if (img.toggle) v[img.toggle.key] = true; }
    const aud = L.refs.find(r => r.media.kind === "audio");
    if (aud && c.voice) { v[aud.media.key] = c.voice; if (aud.toggle) v[aud.toggle.key] = true; }
  }
  saveState();
}

function buildValues(app) {
  const v = { ...valuesFor(app) }, L = layout(app);
  const c = S.chars.find(x => x.id === S.char[app.id]);
  if (c && L.prompt) {
    let p = v[L.prompt.key] || "";
    const desc = [c.trigger, c.look].filter(Boolean).join(", ");
    const tag = "@" + c.name.split(" ")[0];
    if (p.includes(tag)) p = p.split(tag).join(desc ? `${c.name} (${desc})` : c.name);
    else if (desc) p = `${c.name}: ${desc}. ${p}`;
    v[L.prompt.key] = p;
  }
  return v;
}

/* Pose: a stick-figure picture the checkpoint copies (OpenPose ControlNet). Works with Anime Desire and your SD / SDXL checkpoints. */
function poseSection(app) {
  const k = mkey(app), cur = S.pose[k] || {};
  const sec = h(`<div class="fsec posesec"><label class="lbl">Pose <span class="muted">· optional, for checkpoints</span></label><div class="posebody muted">Loading poses…</div></div>`);
  const body = $(".posebody", sec);
  const save = () => { store.set("pose", S.pose); };
  loadPoses().then(d => {
    body.innerHTML = ""; body.classList.remove("muted");
    const addBtn = h(`<label class="ghost addpose" title="A pose pack .zip from Civitai, or pose pictures">+ Add pack (.zip or pictures)<input type="file" accept=".zip,image/*" multiple hidden></label>`);
    $("input", addBtn).onchange = async e => {
      const fd = new FormData(); [...e.target.files].forEach(f => fd.append("file", f));
      try { const r = await fetch("/manny/api/poses/upload", { method: "POST", body: fd }); if (!r.ok) throw new Error(await r.text()); const j = await r.json(); toast(`${j.poses} poses added to ${j.pack}`); await loadPoses(true); sec.replaceWith(poseSection(app)); }
      catch (err) { toast(err.message, true); }
    };
    if (!d.packs.length) { body.append(h(`<div class="muted">No pose packs yet. Download a pack from Civitai (type “Poses”), then add the .zip here.</div>`), addBtn); return; }
    if (!d.controlnets.sd15 && !d.controlnets.sdxl) body.append(h(`<div class="muted">The pose ControlNets are still downloading; poses will work once they're in.</div>`));
    const pick = h(`<div class="chips posepacks"></div>`), grid = h(`<div class="posegrid"></div>`);
    const none = h(`<div class="chip ${cur.file ? "" : "on"}">No pose</div>`);
    none.onclick = () => { delete S.pose[k]; save(); $$(".pose", grid).forEach(x => x.classList.remove("on")); none.classList.add("on"); };
    pick.append(none);
    let pack = (cur.file && d.packs.find(p => cur.file.startsWith("poses/" + p.name + "/"))) || d.packs[0];
    const show = p => {
      pack = p; $$(".chip[data-p]", pick).forEach(c => c.classList.toggle("on", c.dataset.p === p.name));
      grid.innerHTML = "";
      p.poses.forEach(x => {
        const t = h(`<div class="pose ${(S.pose[k] || {}).file === x.file ? "on" : ""}" title="${esc(x.name)}"><img loading="lazy" src="/view?type=input&subfolder=${encodeURIComponent(x.subfolder)}&filename=${encodeURIComponent(x.filename)}"></div>`);
        t.onclick = () => { S.pose[k] = { file: x.file, strength: (S.pose[k] || {}).strength || 0.85 }; save(); $$(".pose", grid).forEach(y => y.classList.toggle("on", y === t)); none.classList.remove("on"); };
        grid.append(t);
      });
    };
    d.packs.forEach(p => { const c = h(`<div class="chip" data-p="${esc(p.name)}">${esc(p.name.length > 28 ? p.name.slice(0, 27) + "…" : p.name)} <span class="muted">${p.poses.length}</span></div>`); c.onclick = () => show(p); pick.append(c); });
    const st = h(`<div class="row posestr"><span class="muted">How closely</span><input type="range" min="0.3" max="1.2" step="0.05" value="${cur.strength || 0.85}"><b>${(cur.strength || 0.85).toFixed(2)}</b></div>`);
    $("input", st).oninput = e => { const val = +e.target.value; $("b", st).textContent = val.toFixed(2); if (S.pose[k]) { S.pose[k].strength = val; save(); } };
    body.append(pick, grid, st, addBtn);
    show(pack);
  });
  return sec;
}

function runBody(app) {
  const seedKeys = app.inputs.filter(i => i.kind === "seed" && (S.randomSeed[app.id] || {})[i.key] !== false).map(i => i.key);
  const vals = buildValues(app);
  const loras = loraPayload(app, vals);
  const ck = (S.ckpt || {})[mkey(app)];
  const checkpoint = ck && L0(app).model && /anime desire|illustrious/i.test(vals[L0(app).model.key] || "") ? ck : null;
  const unc = app.inputs.some(i => i.kind === "toggle" && /uncensored/i.test(i.label) && vals[i.key]);
  const priv = S.incog || unc;
  const fam = L0(app).model ? modelFamily(vals[L0(app).model.key]) : "";
  const vfile = fam && (S.variant[mkey(app)] || {})[fam];
  const unet = vfile ? { family: fam, file: vfile } : null;
  const pz = L0(app).model && /anime desire|illustrious/i.test(vals[L0(app).model.key] || "") ? (S.pose || {})[mkey(app)] : null;
  return { body: { app: app.id, values: vals, count: S.count, client_id: clientId, random_seed_keys: seedKeys, private: priv, loras, checkpoint, unet, target: runTarget(),
    pose: pz && pz.file ? pz.file : undefined, pose_strength: pz && pz.file ? pz.strength : undefined }, priv, vals };
}
/* render later: Studio keeps the job and sends it at a set time, or once the PC has been idle for 2 minutes */
function scheduleMenu(app, anchor) {
  document.querySelectorAll(".schedmenu").forEach(x => x.remove());
  const tonight = new Date(); tonight.setHours(1, 0, 0, 0); if (tonight < new Date()) tonight.setDate(tonight.getDate() + 1); else if (new Date().getHours() >= 1) tonight.setDate(tonight.getDate() + 1);
  const local = d => new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 16);
  const m = h(`<div class="schedmenu"><b>Render later</b>
    <button data-w="idle">When the PC is free<small>after the queue has been empty for 2 min</small></button>
    <button data-h="1">In 1 hour</button><button data-h="3">In 3 hours</button>
    <button data-t="${tonight.getTime()}">Tonight at 1:00 AM</button>
    <div class="row"><input type="datetime-local" value="${local(new Date(Date.now() + 2 * 3600e3))}"><button class="btn sm" data-c>Set</button></div></div>`);
  document.body.append(m);
  const r = anchor.getBoundingClientRect(); m.style.left = Math.max(8, Math.min(window.innerWidth - 300, r.right - 290)) + "px"; m.style.top = Math.max(8, r.top - m.offsetHeight - 8) + "px";
  const close = () => { m.remove(); document.removeEventListener("mousedown", out, true); };
  const out = e => { if (!m.contains(e.target) && e.target !== anchor) close(); };
  setTimeout(() => document.addEventListener("mousedown", out, true), 0);
  const send = async (when, at) => {
    close();
    try {
      const { body, priv, vals } = runBody(app); const L = layout(app);
      const txt = L.prompt ? String(vals[L.prompt.key] || "").slice(0, 70) : "";
      await post("/manny/api/schedule", { run: body, when, at: at ? at / 1000 : null, kind: app.kind, label: priv ? `${appShort(app)} (Incognito)` : `${appShort(app)}${txt ? ": " + txt : ""}` });
      toast(when === "idle" ? "Scheduled: it starts when the PC is free" : "Scheduled for " + new Date(at).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }));
      loadSchedule();
    } catch (e) { toast("Couldn't schedule: " + e.message.slice(0, 200), true); }
  };
  $("[data-w]", m).onclick = () => send("idle");
  $$("[data-h]", m).forEach(b => b.onclick = () => send("at", Date.now() + +b.dataset.h * 3600e3));
  $("[data-t]", m).onclick = () => send("at", +$("[data-t]", m).dataset.t);
  $("[data-c]", m).onclick = () => { const v = $("input", m).value; if (!v) return; const t = new Date(v).getTime(); if (t < Date.now()) return toast("Pick a time in the future", true); send("at", t); };
}
S.sched = [];
async function loadSchedule() {
  try { S.sched = await api("/manny/api/schedule" + (S.incog ? "?private=1" : "")); } catch { S.sched = []; }
  if (!$("#drawer").classList.contains("hidden")) drawDrawer();
  drawQueueCard();
}
async function generate(app) {
  const btn = $("#gen"); if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }
  try {
    const { body, priv } = runBody(app);
    const res = await post("/manny/api/run", body);
    res.queued.forEach(q => S.mine[q.prompt_id] = { app: app.id, at: Date.now(), private: priv, hidden: priv && !S.incog, worker: q.worker });
    const away = res.queued.filter(q => q.worker && q.worker !== "local");
    const fetching = away.filter(q => q.fetching && q.fetching.length);
    if (fetching.length) { toast(`${fetching[0].worker} is downloading ${fetching[0].fetching.join(", ")} first; it renders once that's there`); pollRemote(); }
    else if (away.length) { toast(`Sent to ${[...new Set(away.map(q => q.worker))].join(", ")}`); pollRemote(); }
    if (priv && !S.incog) toast("Saved privately.");
    toast(`Queued ${res.queued.length} ${app.kind === "video" ? "shot" : "picture"}${res.queued.length > 1 ? "s" : ""}`);
    if (window.matchMedia("(max-width: 760px)").matches) { const st = $(".stage"); if (st) st.scrollIntoView({ behavior: "smooth", block: "start" }); }
    pollQueue();
  } catch (e) {
    const d = e.data && e.data.error;
    const msg = typeof d === "string" ? d : d && d.error ? (d.error.message + (d.node_errors ? " — " + Object.values(d.node_errors).map(n => n.errors.map(x => x.message + (x.details ? ": " + x.details : "")).join("; ")).join(" | ") : "")) : e.message;
    toast("Couldn't start: " + msg, true);
  } finally { if (btn) { btn.disabled = false; btn.textContent = "Generate" + (S.count > 1 ? ` ${S.count} ${app.kind === "video" ? "shots" : "pictures"}` : ""); } }
}

function renderStage(stage, app) {
  stage.innerHTML = "";
  if (S.compare && S.compare.app === app.id && S.compare.items.length) {
    const c = h(`<div><div class="section-head" style="margin-top:0"><h3>Model comparison</h3><a href="javascript:void 0" id="cmpx">Close comparison ✕</a></div><div class="cmpgrid"></div></div>`);
    S.compare.items.forEach(it => {
      const cell = h(`<div class="cmpcell" data-pid="${it.prompt_id}">${it.url ? `<img src="${it.url}">` : `<div class="cmpwait">${S.running === it.prompt_id ? "Rendering " + Math.round(S.progress * 100) + "%" : "Waiting…"}</div>`}<b>${esc(it.model)}</b></div>`);
      if (it.url) cell.onclick = () => { const g = S.gallery.find(x => it.url.includes(encodeURIComponent(x.filename))); if (g) lightbox(g, S.compare.items.map(i => S.gallery.find(x => i.url && i.url.includes(encodeURIComponent(x.filename)))).filter(Boolean)); };
      $(".cmpgrid", c).append(cell);
    });
    $("#cmpx", c).onclick = () => { S.compare = null; renderStage(stage, app); };
    stage.append(c);
    return;
  }
  const last = S.stage[mkey(app)];
  const main = h(`<div class="stage-main">${last ? media(last.url, last.kind, true) : `<div class="stage-ph"><h2>${app.kind === "video" ? "Your shot" : "Your picture"}</h2><p>appears here while it renders</p></div>`}<div class="job hidden"><div class="row"><b id="job-t">Rendering</b><span id="job-p"></span></div><div class="bar"><div id="job-bar"></div></div></div></div>`);
  stage.append(main);
  stage.append(h(`<div class="section-head"><h3>Recent ${app.kind === "video" ? "shots" : "pictures"}</h3><a href="#/gallery">Gallery →</a></div>`));
  const g = h(`<div class="grid small"></div>`); stage.append(g);
  loadGallery().then(items => { const rl = items.filter(i => i.kind === app.kind).slice(0, 12); rl.forEach(it => g.append(tile(it, rl))); });
  updateJob();
}
/* measured on this PC (Intel Arc 140T): models whose weights the Arc has to emulate are very slow */
const SLOW_HERE = [
  [/wan 2\.2/i, "Very slow on this PC", "Wan 2.2 14B: about 20–25 minutes per step here (a 5 s clip can take an hour or more). LTX or MiniMax H3 are much faster."],
  [/moss/i, "Slow here (~12 min)", "MOSS-Image: about 26 s per step on this PC, roughly 12 minutes a picture."],
];
function media(url, kind, controls) {
  return kind === "video" ? `<video src="${url}" ${controls ? "controls autoplay loop" : "muted loop preload='metadata'"} playsinline></video>` : `<img src="${url}">`;
}
function updateJob() {
  const j = $(".job"); if (!j) return;
  const app = curApp();
  const run = S.running && S.mine[S.running] && S.mine[S.running].app === app.id;
  j.classList.toggle("hidden", !run);
  if (run) { $("#job-p").textContent = Math.round(S.progress * 100) + "%"; $("#job-bar").style.width = (S.progress * 100) + "%"; $("#job-t").textContent = S.progressNode || "Rendering"; }
}

/* ------------------------------------------------------------ GALLERY */
async function loadGallery(force) {
  if (!force && S.gallery.length && Date.now() - S.galleryAt < 4000) return S.gallery;
  try { S.gallery = await api("/manny/api/gallery" + PQ()); S.galleryAt = Date.now(); } catch { S.gallery = []; }
  return S.gallery;
}
function tile(it, list) {
  const url = itemUrl(it);
  const pv = it.kind === "video" ? `<video src="${bigVid(it) ? proxyUrl(it) : url}" poster="${thumbUrl(it)}" muted loop playsinline preload="none"></video>` : `<img src="${thumbUrl(it)}" loading="lazy" decoding="async">`;
  const el = h(`<div class="tile">${it.kind === "video" ? `<span class="badge v">Video</span>` : ""}${isTopaz(it) ? `<span class="badge tz">Topaz</span>` : ""}${pv}<button class="star ${it.fav ? "on" : ""}" title="Favorite">${it.fav ? "★" : "☆"}</button><div class="cap"><b>${esc(modelOf(it.filename))}</b></div></div>`);
  $(".star", el).onclick = async e => { e.stopPropagation(); await toggleFav(it); const s = $(".star", el); s.classList.toggle("on", it.fav); s.textContent = it.fav ? "★" : "☆"; };
  if (it.kind === "video") { const vd = $("video", el); el.onmouseenter = () => vd.play().catch(() => {}); el.onmouseleave = () => vd.pause(); }
  el.onclick = () => lightbox(it, list);
  return el;
}
async function pageGallery(main) {
  const p = h(`<div class="page"><div class="eyebrow">Library</div><h1>Gallery</h1>
    <div class="filters"><input type="text" id="q" placeholder="Search prompts…" class="search"><div class="chips" id="kind"></div><select id="model"><option value="">Every model</option></select><span class="hint" id="cnt"></span><button class="btn sm" id="selbtn">☐ Select</button></div>
    <div class="selbar hidden" id="selbar"><b id="selcnt">0 selected</b><button class="ghost" id="selall">Select all shown</button><button class="ghost" id="selnone">Clear</button><span class="sp"></span><button class="btn danger" id="seltrash">🗑 Move to trash</button><button class="ghost" id="seldone">Done</button></div>
    <div class="grid" id="g"></div></div>`);
  main.append(p);
  const items = await loadGallery(true);
  let kind = store.get("gkind", ""), model = "";
  const kinds = [["", "All"], ["image", "Pictures"], ["video", "Videos"], ["fav", "★ Favorites"]];
  let q = "";
  $("#q", p).oninput = e => { q = e.target.value.trim().toLowerCase(); draw(); };
  kinds.forEach(([k, l]) => { const c = h(`<div class="chip ${k === kind ? "on" : ""}">${l}</div>`); c.onclick = () => { kind = k; store.set("gkind", k); $$("#kind .chip", p).forEach(x => x.classList.remove("on")); c.classList.add("on"); draw(); }; $("#kind", p).append(c); });
  [...new Set(items.map(i => modelOf(i.filename)))].sort().forEach(m => $("#model", p).append(new Option(m, m)));
  $("#model", p).onchange = e => { model = e.target.value; draw(); };
  let selMode = false, shownNow = [];
  const sel = new Set();
  const selUpd = () => { $("#selcnt", p).textContent = sel.size + " selected"; $("#seltrash", p).disabled = !sel.size; };
  const setMode = on => { selMode = on; sel.clear(); $("#selbar", p).classList.toggle("hidden", !on); $("#selbtn", p).classList.toggle("hidden", on); p.classList.toggle("selecting", on); selUpd(); draw(); };
  $("#selbtn", p).onclick = () => setMode(true);
  $("#seldone", p).onclick = () => setMode(false);
  $("#selnone", p).onclick = () => { sel.clear(); $$(".tile.sel", p).forEach(t => t.classList.remove("sel")); selUpd(); };
  $("#selall", p).onclick = () => { shownNow.forEach(it => sel.add(it)); $$("#g .tile", p).forEach(t => t.classList.add("sel")); selUpd(); };
  $("#seltrash", p).onclick = async () => {
    const list = [...sel]; if (!list.length) return;
    if (!confirm(`Move ${list.length} item${list.length > 1 ? "s" : ""} to the trash? You can get them back from the _trash folder.`)) return;
    const bt = $("#seltrash", p); bt.disabled = true; let ok = 0, bad = 0;
    for (const it of list) {
      bt.textContent = `Moving ${ok + bad + 1} of ${list.length}…`;
      try { await post("/manny/api/trash", it); ok++; const k = items.indexOf(it); if (k >= 0) items.splice(k, 1); S.gallery = S.gallery.filter(x => x !== it); }
      catch { bad++; }
    }
    bt.textContent = "🗑 Move to trash";
    toast(`${ok} moved to trash` + (bad ? `, ${bad} couldn't be moved` : ""), !!bad);
    setMode(false);
  };
  function draw() {
    const g = $("#g", p); g.innerHTML = "";
    const words = q.split(/\s+/).filter(Boolean);
    const list = items.filter(i => (!kind || (kind === "fav" ? i.fav : i.kind === kind)) && (!model || modelOf(i.filename) === model)
      && words.every(w => ((i.prompt || "") + " " + i.filename).toLowerCase().includes(w)));
    $("#cnt", p).textContent = list.length + " items";
    if (!list.length) g.append(h(`<div class="empty">Nothing here yet.</div>`));
    const shown = list.slice(0, 300); shownNow = shown;
    shown.forEach(it => {
      const t = tile(it, shown);
      if (selMode) {
        t.classList.add("selectable"); if (sel.has(it)) t.classList.add("sel");
        t.onclick = e => { e.stopPropagation(); if (sel.has(it)) sel.delete(it); else sel.add(it); t.classList.toggle("sel", sel.has(it)); selUpd(); };
      }
      g.append(t);
    });
  }
  draw();
}
async function toggleFav(it) {
  const r = await post("/manny/api/favorite", { filename: it.filename, subfolder: it.subfolder, on: !it.fav, private: !!it.private });
  it.fav = r.fav; const g = S.gallery.find(x => x.filename === it.filename && x.subfolder === it.subfolder); if (g) g.fav = r.fav;
}
async function sendToApp(name, cur, kind) {
  const k = findApp(name); if (k < 0) return toast("That app isn't installed", true);
  const app = S.apps[k];
  const { value } = await post("/manny/api/use_output", cur);
  const v = valuesFor(app), L = layout(app), r = L.refs.find(x => x.media.kind === kind);
  if (r) { v[r.media.key] = value; if (r.toggle) v[r.toggle.key] = true; }
  saveState(); return k;
}
let lbKeys = null;
function lightbox(it, list) {
  list = list && list.length ? list : [it];
  let i = Math.max(0, list.indexOf(it));
  const imgApp = S.apps.findIndex(a => a.kind === "image"), vidApp = S.apps.findIndex(a => a.kind === "video");
  const m = $("#modal");
  m.innerHTML = ""; m.classList.remove("hidden");
  const box = h(`<div class="lb"><div class="media"><div class="stagebox"></div>
      <button class="nav prev" title="Previous (←)">‹</button><button class="nav next" title="Next (→)">›</button>
      <div class="pos"></div><button class="lbfull" title="Full screen (F)"></button><button class="lbclose" title="Close">✕</button></div><div class="side"></div></div>`);
  m.append(box);
  /* full screen: the picture or video fills the screen; the same button brings the details back */
  const FS_IN = `<svg viewBox="0 0 24 24" width="18" height="18"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`, FS_OUT = `<svg viewBox="0 0 24 24" width="18" height="18"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const fsBtn = $(".lbfull", box);
  const setFull = on => {
    S.lbFull = on; box.classList.toggle("full", on);
    fsBtn.innerHTML = on ? FS_OUT : FS_IN; fsBtn.title = on ? "Exit full screen (F)" : "Full screen (F)";
    try {
      if (on && !document.fullscreenElement && m.requestFullscreen) m.requestFullscreen({ navigationUI: "hide" }).catch(() => { });
      else if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => { });
    } catch { }
  };
  const onFsChange = () => { if (!document.fullscreenElement && box.classList.contains("full")) { S.lbFull = false; box.classList.remove("full"); fsBtn.innerHTML = FS_IN; } };
  document.addEventListener("fullscreenchange", onFsChange);
  fsBtn.onclick = e => { e.stopPropagation(); setFull(!box.classList.contains("full")); };
  fsBtn.innerHTML = FS_IN;
  if (S.lbFull) setFull(true);
  const close = () => { document.removeEventListener("fullscreenchange", onFsChange); if (document.fullscreenElement) document.exitFullscreen().catch(() => { }); m.classList.add("hidden"); m.innerHTML = ""; if (lbKeys) { document.removeEventListener("keydown", lbKeys); lbKeys = null; } };
  const go = d => { if (list.length < 2) return; i = (i + d + list.length) % list.length; show(); };
  function show() {
    const cur = list[i], url = itemUrl(cur);
    const sb = $(".stagebox", box);
    if (cur.kind === "video") sb.innerHTML = `<video src="${bigVid(cur) ? proxyUrl(cur) : url}" poster="${thumbUrl(cur, 1600)}" controls autoplay loop playsinline></video>${bigVid(cur) ? `<button class="fullres" title="This big video plays as a light 720p copy. Click for the original.">Original</button>` : ""}`;
    else if (bigPic(cur)) sb.innerHTML = `<img src="${thumbUrl(cur, 1600)}">${isTiff(cur) ? "" : `<button class="fullres" title="Shown as a light copy for speed">Full resolution</button>`}`;
    else sb.innerHTML = media(url, cur.kind, true);
    const fr = $(".fullres", sb);
    if (fr) fr.onclick = e => { e.stopPropagation(); fr.textContent = "Loading…"; const el2 = $("img,video", sb); if (cur.kind === "video") { const t0 = el2.currentTime; el2.src = url; el2.currentTime = t0; el2.play().catch(() => { }); fr.remove(); } else { const im = new Image(); im.onload = () => { el2.src = url; fr.remove(); }; im.onerror = () => { fr.textContent = "Couldn't load"; }; im.src = url; } };
    // warm the next and previous previews so browsing feels instant
    [list[(i + 1) % list.length], list[(i - 1 + list.length) % list.length]].forEach(n2 => { if (n2 && n2 !== cur && n2.kind === "image") { const im = new Image(); im.src = bigPic(n2) ? thumbUrl(n2, 1600) : itemUrl(n2); } });
    $(".pos", box).textContent = list.length > 1 ? `${i + 1} / ${list.length}` : "";
    $$(".nav", box).forEach(b => b.classList.toggle("hidden", list.length < 2));
    const side = $(".side", box);
    side.innerHTML = `<div class="row" style="justify-content:space-between"><h2>${esc(modelOf(cur.filename))}</h2><div class="row"><button class="icon star2 ${cur.fav ? "on" : ""}" data-fav title="Favorite">${cur.fav ? "★" : "☆"}</button><button class="icon" data-x title="Close (Esc)">✕</button></div></div>
     <div class="promptbox"><label class="lbl">Prompt</label><div class="ptext muted">Reading…</div></div>
     <div class="kv" id="kv"><span>File</span><span>${esc(cur.filename)}</span><span>Made</span><span>${fmtDate(cur.mtime)}</span><span>Size</span><span>${fmtSize(cur.size)}</span></div>
     <button class="btn primary" data-remix>↻ Remix (load these settings)</button>
     <div class="acts2">${cur.kind === "image" ? `
       <button class="btn" data-to="video:image">▶ Animate (first frame)</button>
       <button class="btn" data-to="edit:image">✎ Edit in words</button>
       <button class="btn" data-to="inpaint:image">◐ Inpaint an area</button>
       <button class="btn" data-to="upscale:image">⤢ Upscale 2K / 4K</button>
       <button class="btn hidden" data-tz>✦ Topaz Gigapixel</button>
       <button class="btn" data-to="remove background:image">✂ Remove background</button>
       <button class="btn" data-to="image:image">▣ Use as reference</button>` : `
       <button class="btn" data-to="smooth motion:video">≋ Smooth motion</button>
       <button class="btn" data-to="upscale video:video">⤢ Upscale to 1080p</button>
       <button class="btn hidden" data-tz>✦ Topaz Video</button>
       <button class="btn" data-vfix>🩹 Fix a part / a face</button>
       <button class="btn" data-vext>⏭ Continue this shot</button>`}
     </div>
     <button class="ghost" data-board>＋ Add to storyboard${cur.kind === "image" ? " (as first frame)" : ""}</button>
     <div class="row"><a class="ghost" href="${url}" download="${esc(cur.filename)}">Download</a><button class="ghost danger" data-trash>Move to trash</button></div>
     <p class="hint">← → to browse · Esc to close. Trash is a folder (_trash) inside Pictures › Comfyui, so nothing is lost.</p>`;
    $("[data-fav]", side).onclick = async () => { await toggleFav(cur); const s = $("[data-fav]", side); s.textContent = cur.fav ? "★" : "☆"; s.classList.toggle("on", cur.fav); m.dataset.dirty = "1"; };
    let meta = null;
    api(`/manny/api/meta?filename=${encodeURIComponent(cur.filename)}&subfolder=${encodeURIComponent(cur.subfolder || "")}${cur.private ? "&private=1" : ""}`).then(md => {
      if (list[i] !== cur) return; meta = md;
      const pt = $(".ptext", side);
      if (md.prompt) { pt.classList.remove("muted"); pt.textContent = md.prompt; const cp = h(`<button class="ghost copy">Copy</button>`); cp.onclick = e => { e.stopPropagation(); copyText(md.prompt, "Prompt copied"); }; $(".promptbox", side).append(cp); }
      else pt.textContent = "No prompt saved in this file.";
      const kv = $("#kv", side);
      if (md.size) kv.insertAdjacentHTML("beforeend", `<span>Pixels</span><span>${md.size[0]} × ${md.size[1]}</span>`);
      (md.settings || []).forEach(([k, v2]) => kv.insertAdjacentHTML("beforeend", `<span>${esc(k)}</span><span>${esc(v2)}</span>`));
      if (!md.remix) $("[data-remix]", side).classList.add("hidden");
    }).catch(() => { $(".ptext", side).textContent = "Couldn't read this file's settings."; });
    $("[data-remix]", side).onclick = () => {
      if (!meta || !meta.remix) return;
      const k = S.apps.findIndex(a => a.id === meta.remix.app); if (k < 0) return;
      const app = S.apps[k], v = valuesFor(app);
      Object.assign(v, meta.remix.values);
      const r = S.randomSeed[app.id] || (S.randomSeed[app.id] = {}); app.inputs.filter(x => x.kind === "seed").forEach(x => r[x.key] = false);
      saveState(); close(); openApp(k); toast("Settings loaded, with the same seed. Turn on “New each time” for variations.");
    };
    $("[data-board]", side).onclick = async () => {
      let img = "";
      if (cur.kind === "image") img = (await post("/manny/api/use_output", cur)).value;
      await addToBoard(img, meta && meta.prompt && cur.kind === "image" ? "" : "");
      toast("Added a shot to “" + (curBoard()?.name || "My film") + "”");
    };
    $$("[data-to]", side).forEach(b => b.onclick = async () => {
      const [name, kind] = b.dataset.to.split(":");
      const k = await sendToApp(name, cur, kind); if (k == null || k < 0) return;
      close(); openApp(k); toast("Added to " + appShort(S.apps[k]));
    });
    $("[data-x]", side).onclick = close;
    const vfb = $("[data-vfix]", side), veb = $("[data-vext]", side);
    if (vfb) vfb.onclick = () => { const v = $("video", box); videoFixDialog(cur, meta && meta.prompt, v ? v.currentTime : 0); };
    if (veb) veb.onclick = () => videoExtendDialog(cur, meta && meta.prompt);
    const tzb = $("[data-tz]", side);
    if (tzb) topaz().then(t => {
      const kind = cur.kind === "video" ? "video" : "image";
      if (tzReady(t, kind)) { tzb.classList.remove("hidden"); tzb.onclick = () => topazDialog(cur); }
      else if (tzApp(t, kind) && !cur.private) { tzb.textContent = kind === "video" ? "✦ Topaz Video" : "✦ Gigapixel"; tzb.classList.remove("hidden"); tzb.onclick = () => topazHandoff(cur); }
    });

    $("[data-trash]", side).onclick = async () => {
      await post("/manny/api/trash", cur);
      S.gallery = S.gallery.filter(x => x !== cur); list.splice(i, 1); toast("Moved to trash");
      if (!list.length) { close(); route(); return; }
      if (i >= list.length) i = list.length - 1;
      show(); m.dataset.dirty = "1";
    };
  }
  $(".lbclose", box).onclick = e => { e.stopPropagation(); S.lbFull = false; close(); if (m.dataset.dirty) { delete m.dataset.dirty; route(); } };
  $(".prev", box).onclick = e => { e.stopPropagation(); go(-1); };
  $(".next", box).onclick = e => { e.stopPropagation(); go(1); };
  m.onclick = e => { if (e.target === m) { close(); if (m.dataset.dirty) { delete m.dataset.dirty; route(); } } };
  lbKeys = e => {
    if (e.target.closest && e.target.closest("input,textarea,select")) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
    else if (e.key === "f" || e.key === "F") { e.preventDefault(); setFull(!box.classList.contains("full")); }
    else if (e.key === "Escape") { if (box.classList.contains("full")) { setFull(false); return; } close(); if (m.dataset.dirty) { delete m.dataset.dirty; route(); } }
  };
  document.addEventListener("keydown", lbKeys);
  // swipe on touch screens
  let sx = null;
  box.addEventListener("touchstart", e => { sx = e.touches[0].clientX; }, { passive: true });
  box.addEventListener("touchend", e => { if (sx == null) return; const dx = e.changedTouches[0].clientX - sx; if (Math.abs(dx) > 50) go(dx > 0 ? -1 : 1); sx = null; });
  show();
}

/* ------------------------------------------------------------ CHARACTERS */
async function loadChars() { try { S.chars = await api("/manny/api/characters" + PQ()); } catch { S.chars = []; } return S.chars; }
async function pageCharacters(main) {
  const p = h(`<div class="page"><div class="eyebrow">Cast</div><h1>Characters</h1>
    <p class="muted" style="max-width:640px">A character is a face, a look and (for shots) a voice. Pick one in Create and it goes into the prompt, the reference image and the voice slot for you.</p>
    <div class="cgrid" id="cg" style="margin-top:22px"></div></div>`);
  main.append(p);
  await loadChars();
  const g = $("#cg", p);
  const nc = h(`<div class="ccard newcard"><div class="plus">+</div><h3>New character</h3><span class="hint">Face, look and voice</span></div>`);
  nc.onclick = () => editChar(null); g.append(nc);
  S.chars.forEach(c => {
    const el = h(`<div class="ccard"><div class="face" style="${c.face ? `background-image:url('${inputUrl(c.face)}')` : ""}">${c.face ? "" : `<div class="noface">${esc(c.name[0] || "?")}</div>`}</div>
      <div class="body"><h3>${esc(c.name)}</h3><p>${esc(c.look || "No description yet")}</p>
      <div class="acts"><button class="ghost" data-a="image">Picture</button><button class="ghost" data-a="video">Shot</button><button class="ghost" data-a="edit">Edit</button></div></div></div>`);
    $$("[data-a]", el).forEach(b => b.onclick = () => {
      if (b.dataset.a === "edit") return editChar(c);
      const k = S.apps.findIndex(a => a.kind === b.dataset.a); if (k < 0) return;
      selectCharacter(S.apps[k], c); openApp(k);
    });
    g.append(el);
  });
}
function editChar(c) {
  c = c ? { ...c } : { name: "", look: "", trigger: "", face: "", voice: "" };
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>${c.id ? "Edit character" : "New character"}</h2><button class="icon" data-x>✕</button></div>
    <div class="two">
      <div><label class="lbl">Face</label><div class="ref ${c.face ? "on" : ""}" id="cf" style="aspect-ratio:4/5">${c.face ? `<img src="${inputUrl(c.face)}">` : `<span class="plus">+</span>`}<span class="rl">Drop a clear face photo</span><input type="file" hidden accept="image/*"></div></div>
      <div style="display:flex;flex-direction:column;gap:12px">
        <div><label class="lbl">Name</label><input type="text" id="cn" placeholder="Tess Marlow"></div>
        <div><label class="lbl">Voice (for shots)</label><div class="ref audio ${c.voice ? "on" : ""}" id="cv" style="aspect-ratio:auto;min-height:90px"><span class="${c.voice ? "wave" : "plus"}">${c.voice ? "♪" : "+"}</span><span class="rl">${c.voice ? esc(c.voice.split("/").pop()) : "Drop a voice clip (wav, mp3)"}</span><input type="file" hidden accept="audio/*"></div></div>
      </div>
    </div>
    <div><label class="lbl">Look &amp; who they are</label><textarea id="cl" rows="3" placeholder="late 20s, freckles, copper bob haircut, green field jacket"></textarea></div>
    <div><label class="lbl">LoRA trigger word (optional)</label><input type="text" id="ct" placeholder="Only if you trained a LoRA for this character"></div>
    <div class="foot">${c.id ? `<button class="ghost danger" data-del style="margin-right:auto">Delete</button>` : ""}<button class="ghost" data-x>Cancel</button><button class="btn primary" data-save>Save</button></div></div>`);
  m.append(d);
  $("#cn", d).value = c.name; $("#cl", d).value = c.look || ""; $("#ct", d).value = c.trigger || "";
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(b => b.onclick = close);
  const wire = (id, field, isAudio) => {
    const el = $(id, d), fi = $("input", el);
    el.onclick = () => fi.click();
    const take = async f => {
      const val = await upload(f); c[field] = val; el.classList.add("on");
      if (isAudio) { $(".plus,.wave", el).className = "wave"; $(".wave", el).textContent = "♪"; $(".rl", el).textContent = f.name; }
      else { $$("img", el).forEach(x => x.remove()); el.prepend(h(`<img src="${inputUrl(val)}">`)); }
    };
    fi.onchange = () => fi.files[0] && take(fi.files[0]);
    el.ondragover = e => e.preventDefault();
    el.ondrop = e => { e.preventDefault(); e.dataTransfer.files[0] && take(e.dataTransfer.files[0]); };
  };
  wire("#cf", "face", false); wire("#cv", "voice", true);
  $("[data-save]", d).onclick = async () => {
    c.name = $("#cn", d).value.trim(); c.look = $("#cl", d).value.trim(); c.trigger = $("#ct", d).value.trim();
    if (!c.name) return toast("Give your character a name", true);
    c.private = S.incog; await post("/manny/api/characters", c); await loadChars(); close(); toast("Saved " + c.name); route();
  };
  const del = $("[data-del]", d);
  if (del) del.onclick = async () => { if (!confirm("Delete " + c.name + "?")) return; await api("/manny/api/characters/" + c.id, { method: "DELETE" }); await loadChars(); close(); route(); };
}


/* ------------------------------------------------------------ STORYBOARD */
S.boards = []; S.boardId = store.get("board", null);
async function loadBoards() { try { S.boards = await api("/manny/api/boards" + PQ()); } catch { S.boards = []; } return S.boards; }
const boardSaves = {};
function saveBoard(b, now) {
  clearTimeout(boardSaves[b.id || "new"]);
  if (b.private == null) b.private = S.incog;
  const go = async () => { const r = await post("/manny/api/boards", b); if (!b.id) b.id = r.id; };
  if (now || !b.id) return go();
  boardSaves[b.id] = setTimeout(go, 600);
}
function curBoard() { return S.boards.find(b => b.id === S.boardId) || S.boards[0]; }
const newShot = (prompt = "", image = "") => ({ id: Math.random().toString(36).slice(2, 10), prompt, image, camera: "" });

async function addToBoard(image, prompt) {
  await loadBoards();
  let b = curBoard();
  if (!b) { b = { name: "My film", shots: [] }; S.boards.push(b); }
  b.shots.push(newShot(prompt || "", image || "")); await saveBoard(b, true); S.boardId = b.id; store.set("board", b.id);
}

function videoSummary() {
  const k = findApp("video"); if (k < 0) return null;
  const app = S.apps[k], v = valuesFor(app);
  const pick = lab => { const i = app.inputs.find(x => x.label.toLowerCase().startsWith(lab)); return i ? String(v[i.key] ?? "").split("(")[0].trim() : ""; };
  return { k, app, text: [pick("model"), pick("aspect"), pick("duration"), pick("quality")].filter(Boolean).join(" · ") };
}

/* each shot keeps its takes: draft a few, pick one, render it in final quality, then finish it */
function ensureTakes(s) {
  if (s.takes) return s;
  s.takes = (s.output || s.prompt_id) ? [{ pid: s.prompt_id || null, output: s.output || null, status: s.status || (s.output ? "done" : ""), seed: null, q: "" }] : [];
  s.pick = s.output ? 0 : null;
  return s;
}
function syncPick(s) { const t = s.takes[s.pick]; s.output = t && t.output ? t.output : null; }
function videoKeys(app) { return { seed: app.inputs.find(i => i.kind === "seed"), quality: app.inputs.find(i => /^quality/i.test(i.label)) }; }

async function renderShot(b, s, o = {}) {
  ensureTakes(s);
  const vs = videoSummary(); if (!vs) return toast("The Video app is missing", true);
  const app = vs.app, L = layout(app), vals = { ...valuesFor(app) };
  if (L.prompt) vals[L.prompt.key] = [s.prompt, s.camera].filter(Boolean).join(". ");
  let first = true, motion = null;
  const pv = s.previs && (s.previs.lit || s.previs.grey);
  if (pv && s.engine === "render") {   // the Blender render itself becomes the take
    s.takes.push({ pid: null, seed: null, q: "render", status: "done", output: pv.video });
    if (s.pick == null) s.pick = s.takes.length - 1;
    syncPick(s); saveBoard(b, true); return;
  }
  if (pv) {
    await Promise.all([S.scenes ? 0 : loadScenes(), S.locs ? 0 : loadLocs()]);
    const pick = (re) => { const opt = L.model && L.model.options.find(x => re.test(x)); if (opt) vals[L.model.key] = opt; };
    const dur = app.inputs.find(i => /^duration/i.test(i.label));
    if (dur) { const want = dur.options.find(x => parseFloat(x) >= (s.seconds || pv.secs) - 0.2) || dur.options[dur.options.length - 1]; vals[dur.key] = want; }
    const imgs = L.refs.filter(r => r.media.kind === "image");
    L.refs.forEach(r => { if (r.toggle) vals[r.toggle.key] = false; });
    const setImg = (k, v) => { const r = imgs[k]; if (!r) return; vals[r.media.key] = v; if (r.toggle) vals[r.toggle.key] = true; };
    if (s.engine === "motion") {
      pick(/references/i);
      const { loc, cast } = sceneRefs(b, s);
      const pics = [];
      if (loc && loc.pictures[0]) pics.push(["location", loc.pictures[0]]);
      cast.forEach(c => { if (c.ch && c.ch.face) pics.push([c.name, c.ch.face, c.colorName]); });
      pics.slice(0, imgs.length).forEach(([, v], k) => setImg(k, v));
      const lines = pics.slice(0, imgs.length).map(([n, , col], k) => n === "location" ? `<Picture ${k + 1}> is the location and its lighting.` : `<Picture ${k + 1}> is ${n} (the ${col} figure in <Video 1>).`);
      if (L.prompt) vals[L.prompt.key] = `Follow <Video 1> exactly: the same camera move, framing, timing and the way every figure moves, as a real filmed shot. ${lines.join(" ")} ${s.prompt || ""}`.trim();
      motion = pv.video_in;
    } else {
      if (!s.styled || s.styled.from !== pv.first) {
        s.status3d = "Styling frames…"; if (S.sbDraw) S.sbDraw();
        try { await styleFrames(b, s, st => { s.status3d = st; const e = document.querySelector(`[data-shot="${s.id}"] .s3d`); if (e) e.textContent = st; }); }
        catch (e) { s.status3d = ""; toast("Couldn't style the frames: " + e.message.slice(0, 200), true); return; }
        s.status3d = ""; saveBoard(b, true);
      }
      pick(/first \+ last/i);
      setImg(0, s.styled.first); setImg(1, s.styled.last);
    }
    first = false;
  }
  if (!pv && !s.image && L.model && /first frame\)|image 1 = first frame/i.test(vals[L.model.key] || "") && !/text only/i.test(vals[L.model.key] || "")) {
    toast(`${splitOption(vals[L.model.key])[0]} needs a first frame: add one to the shot, or pick “MiniMax H3 First frame (or text only)” in the Video app.`, true);
    return;
  }
  L.refs.forEach(r => {
    if (pv) return;
    if (r.media.kind === "image" && first) { first = false; if (s.image) { vals[r.media.key] = s.image; if (r.toggle) vals[r.toggle.key] = true; } else if (r.toggle) vals[r.toggle.key] = false; }
    else if (r.toggle) vals[r.toggle.key] = false;
  });
  const K = videoKeys(app);
  if (K.quality && o.q) { const want = K.quality.options.find(x => o.q === "final" ? /final|full/i.test(x) : /draft|turbo/i.test(x)); if (want) vals[K.quality.key] = want; }
  let seedKeys = app.inputs.filter(i => i.kind === "seed").map(i => i.key);
  if (o.seed != null && K.seed) { vals[K.seed.key] = o.seed; seedKeys = seedKeys.filter(k => k !== K.seed.key); }
  try {
    const loras = loraPayload(app, vals);
    const res = await post("/manny/api/run", { app: app.id, values: vals, count: o.count || 1, client_id: clientId, random_seed_keys: seedKeys, private: !!b.private, loras, motion_video: motion, target: runTarget() });
    res.queued.forEach(q => {
      const seed = o.seed != null ? o.seed : (K.seed ? (q.seed || [])[seedKeys.indexOf(K.seed.key)] : null);
      s.takes.push({ pid: q.prompt_id, seed: seed ?? null, q: o.q || "", status: "queued", output: null, from: o.from ?? null });
      S.mine[q.prompt_id] = { app: app.id, board: b.id, shot: s.id, private: !!b.private };
    });
    saveBoard(b, true);
  } catch (e) { toast("Couldn't start shot: " + e.message.slice(0, 200), true); }
}
function shotDone(bid, sid, f, pid) {
  const b = S.boards.find(x => x.id === bid); if (!b) return;
  const s = b.shots.find(x => x.id === sid); if (!s) return;
  ensureTakes(s);
  const k = s.takes.findIndex(t => t.pid === pid); if (k < 0) return;
  s.takes[k].output = { filename: f.filename, subfolder: String(f.subfolder || "").replace(/^\.private_incoming\/?/, "") }; s.takes[k].status = "done";
  if (s.pick == null || s.takes[k].q === "final") s.pick = k;   // a final-quality render replaces the draft it came from
  syncPick(s); saveBoard(b, true);
}
async function resolveShots(b) {
  let changed = false;
  for (const s of b.shots) {
    ensureTakes(s);
    for (const [k, t] of s.takes.entries()) {
      if (!t.pid || t.output || t.status === "failed" || t.status === "lost") continue;
      try {
        const hh = (await api("/history/" + t.pid))[t.pid];
        if (hh && hh.outputs) {
          for (const o of Object.values(hh.outputs)) {
            const f = [...(o.images || []), ...(o.gifs || []), ...(o.videos || [])].find(x => x.type === "output");
            if (f) { t.output = { filename: f.filename, subfolder: String(f.subfolder || "").replace(/^\.private_incoming\/?/, "") }; t.status = "done"; if (s.pick == null || t.q === "final") s.pick = k; changed = true; break; }
          }
          if (!t.output && hh.status && hh.status.status_str === "error") { t.status = "failed"; changed = true; }
        } else if (S.queue && ![...S.queue.queue_running, ...S.queue.queue_pending].some(q => q[1] === t.pid)) { t.status = "lost"; changed = true; }
      } catch { }
    }
    syncPick(s);
  }
  if (changed) saveBoard(b, true);
  return changed;
}

/* ---- prompt writer: builds a clear video prompt from a few plain answers */
const PW = {
  shot: ["", "Wide shot", "Medium shot", "Close-up", "Extreme close-up", "Over-the-shoulder shot", "Low-angle shot", "High-angle shot", "Aerial shot"],
  light: ["", "golden hour sunlight", "soft overcast daylight", "harsh midday sun", "blue hour twilight", "night with neon signs", "moonlight", "candlelight", "soft studio lighting", "moody low-key lighting with deep shadows", "backlit silhouette"],
  mood: ["", "calm and dreamy", "tense and suspenseful", "romantic and intimate", "joyful and energetic", "melancholic", "mysterious", "epic and grand", "playful"],
  style: ["", "cinematic film look, shallow depth of field, 35mm grain", "handheld documentary feel", "glossy commercial look", "music video look", "vintage 1970s film", "anime style", "photorealistic"],
  pace: ["", "slow, graceful motion", "natural real-time motion", "fast, dynamic motion", "slow motion"],
};
function promptWriter(start, onUse) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const sel = (id, list, lab) => `<div><label class="lbl">${lab}</label><select id="${id}">${list.map(x => `<option value="${esc(x)}">${esc(x || "—")}</option>`).join("")}</select></div>`;
  const d = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>Prompt writer</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">Answer what you like, skip the rest. Video models follow one clear action per shot best.</p>
    <div class="two">
      <div><label class="lbl">Who or what</label><input type="text" id="pw-who" placeholder="a woman in a red coat"></div>
      <div><label class="lbl">Does what</label><input type="text" id="pw-act" placeholder="turns and walks toward the camera"></div>
      <div><label class="lbl">Where</label><input type="text" id="pw-where" placeholder="a rainy Tokyo street"></div>
      ${sel("pw-shot", PW.shot, "Framing")}
      ${sel("pw-cam", [""].concat(CAMERA.map(c => c[1])), "Camera move")}
      ${sel("pw-light", PW.light, "Light")}
      ${sel("pw-mood", PW.mood, "Mood")}
      ${sel("pw-pace", PW.pace, "Motion")}
      ${sel("pw-style", PW.style, "Look")}
      <div><label class="lbl">Sound (models with sound)</label><input type="text" id="pw-snd" placeholder="rain, distant traffic"></div>
    </div>
    <div><label class="lbl">Spoken line (optional)</label><div class="two"><input type="text" id="pw-spk" placeholder="who speaks: she"><input type="text" id="pw-line" placeholder="the words"></div></div>
    <div><div class="lblrow"><label class="lbl">Prompt</label><a href="javascript:void 0" class="pwlink" id="pw-ai">✦ Improve with AI</a></div><textarea id="pw-out" rows="5"></textarea><span class="hint" id="pw-hint">You can edit this before using it.</span></div>
    <div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" data-use>Use this prompt</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  const g = id => $("#pw-" + id, d).value.trim();
  const cap = t => t ? t[0].toUpperCase() + t.slice(1) : t;
  let touched = false;
  const build = () => {
    const parts = [];
    const lead = [g("shot"), g("who") ? (g("shot") ? "of " : "") + g("who") : ""].filter(Boolean).join(" ");
    let s1 = [lead, g("act"), g("where") ? "in " + g("where").replace(/^(in|at|on)\s+/i, "") : ""].filter(Boolean).join(" ");
    if (s1) parts.push(cap(s1) + ".");
    if (g("spk") || g("line")) parts.push(`${cap(g("spk") || "They")} says: "${g("line")}".`);
    if (g("cam")) parts.push(cap(g("cam")) + ".");
    if (g("light")) parts.push(cap(g("light")) + ".");
    if (g("mood")) parts.push("The mood is " + g("mood") + ".");
    if (g("pace")) parts.push(cap(g("pace")) + ".");
    if (g("style")) parts.push(cap(g("style")) + ".");
    if (g("snd")) parts.push("Sound: " + g("snd") + ".");
    return parts.join(" ");
  };
  const out = $("#pw-out", d);
  out.value = start || "";
  out.oninput = () => { touched = true; };
  $$("input:not(#pw-out),select", d).forEach(x => x.oninput = x.onchange = () => { if (!touched || confirm("Replace your edits with the new answers?")) { touched = false; out.value = build(); } });
  $("[data-use]", d).onclick = () => { const v = out.value.trim(); if (!v) return toast("Write something first", true); close(); onUse(v); };
  $("#pw-ai", d).onclick = async () => {
    const idea = out.value.trim() || build(); if (!idea) return toast("Write an idea or fill a few answers first", true);
    const a = $("#pw-ai", d), hint = $("#pw-hint", d); a.textContent = "✦ Writing…"; a.style.pointerEvents = "none";
    try {
      const r = await post("/manny/api/ai/prompt", { idea, kind: "video", private: S.incog });
      const res = await pollJob(r.id, st => { hint.textContent = st + " (local Qwen3-VL, about a minute the first time)"; });
      out.value = res.prompt; touched = true; hint.textContent = "Rewritten by the local AI. Edit it if you like.";
    } catch (e) { toast("The AI couldn't help: " + e.message.slice(0, 200), true); hint.textContent = ""; }
    finally { a.textContent = "✦ Improve with AI"; a.style.pointerEvents = ""; }
  };
}

/* ---- compare two takes: side by side or a wipe, always in sync */
function compareTakes(b, s, onPick) {
  const opts = s.takes.map((t, k) => ({ k, t })).filter(x => x.t.output);
  if (s.final) opts.push({ k: "final", t: { output: s.final } });
  if (opts.length < 2) return toast("Render at least two takes first", true);
  const name = x => x.k === "final" ? "Finished" : `Take ${x.k + 1}${x.t.q === "final" ? " (final)" : ""}`;
  const url = x => fileUrl(x.t.output, b.private);
  let A = opts.find(x => x.k === s.pick) || opts[0], B = opts.find(x => x !== A), mode = "side";
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="cmpx"><div class="row cmpbar"><select id="ca"></select><span class="vs">vs</span><select id="cb"></select>
      <div class="chips"><div class="chip on" data-m="side">Side by side</div><div class="chip" data-m="wipe">Wipe</div></div>
      <button class="icon" data-x style="margin-left:auto">✕</button></div>
    <div class="cmpview"></div>
    <div class="row cmpctl"><button class="btn" id="cplay">❚❚</button><input type="range" id="cseek" min="0" max="1000" value="0"><span class="hint" id="ctime">0.0 s</span></div>
    <div class="row cmppick"><button class="btn" data-p="a">★ Pick left</button><button class="btn" data-p="b">★ Pick right</button></div></div>`);
  m.append(d);
  opts.forEach(x => { $("#ca", d).append(new Option(name(x), x.k)); $("#cb", d).append(new Option(name(x), x.k)); });
  let va, vb, raf;
  const close = () => { cancelAnimationFrame(raf); m.classList.add("hidden"); m.innerHTML = ""; document.removeEventListener("keydown", key); };
  const key = e => { if (e.key === "Escape") close(); else if (e.key === " ") { e.preventDefault(); toggle(); } };
  document.addEventListener("keydown", key);
  $("[data-x]", d).onclick = close;
  const toggle = () => { if (va.paused) { va.play(); vb.play(); } else { va.pause(); vb.pause(); } };
  const draw = () => {
    $("#ca", d).value = A.k; $("#cb", d).value = B.k;
    $$("[data-m]", d).forEach(c => c.classList.toggle("on", c.dataset.m === mode));
    const v = $(".cmpview", d);
    v.className = "cmpview " + mode;
    v.innerHTML = mode === "side"
      ? `<div><video src="${url(A)}" playsinline loop></video><b>${esc(name(A))}</b></div><div><video src="${url(B)}" playsinline loop muted></video><b>${esc(name(B))}</b></div>`
      : `<div class="wipe"><video src="${url(B)}" playsinline loop muted></video><video class="top" src="${url(A)}" playsinline loop></video><div class="wline"></div><b class="wl">${esc(name(A))}</b><b class="wr">${esc(name(B))}</b><input type="range" class="wr-range" min="0" max="100" value="50"></div>`;
    [va, vb] = mode === "side" ? $$("video", v) : [$(".top", v), $$("video", v)[0]];
    if (mode === "wipe") {
      const r = $(".wr-range", v), set = () => { $(".top", v).style.clipPath = `inset(0 ${100 - r.value}% 0 0)`; $(".wline", v).style.left = r.value + "%"; };
      r.oninput = set; set();
    }
    va.onplay = () => { $("#cplay", d).textContent = "❚❚"; }; va.onpause = () => { $("#cplay", d).textContent = "▶"; };
    va.play().catch(() => { }); vb.play().catch(() => { });
  };
  const tick = () => {
    if (va && vb && va.duration) {
      if (Math.abs(vb.currentTime - va.currentTime) > 0.06) vb.currentTime = va.currentTime;
      if (va.paused !== vb.paused) va.paused ? vb.pause() : vb.play().catch(() => { });
      $("#cseek", d).value = Math.round(va.currentTime / va.duration * 1000);
      $("#ctime", d).textContent = va.currentTime.toFixed(1) + " s";
    }
    raf = requestAnimationFrame(tick);
  };
  $("#cplay", d).onclick = toggle;
  $("#cseek", d).oninput = e => { if (va.duration) { va.currentTime = e.target.value / 1000 * va.duration; vb.currentTime = va.currentTime; } };
  $("#ca", d).onchange = e => { A = opts.find(x => String(x.k) === e.target.value); draw(); };
  $("#cb", d).onchange = e => { B = opts.find(x => String(x.k) === e.target.value); draw(); };
  $$("[data-m]", d).forEach(c => c.onclick = () => { mode = c.dataset.m; draw(); });
  $$("[data-p]", d).forEach(bt => bt.onclick = () => { const x = bt.dataset.p === "a" ? A : B; if (x.k === "final") return toast("That's the finished version"); onPick(x.k); close(); });
  draw(); tick();
}

/* ---- Topaz (Gigapixel for pictures, Topaz Video for clips) */
let tzCache = null;
async function topaz(force) {
  if (!tzCache || force) tzCache = await api("/manny/api/topaz/status" + (force ? "?force=1" : "")).catch(() => null);
  return tzCache;
}
const tzReady = (t, kind) => !!(t && t.prefs && t.prefs.use && t[kind] && t[kind].ready);
const tzApp = (t, kind) => !!(t && t.apps && t.apps[kind] && t.apps[kind].exe);
const isTopaz = it => /topaz|gigapixel|[-_ ](prob|rhea|iris|apo|chr|ahq|nyx|proteus|apollo|chronos)-?\d/i.test(it.filename || "");
async function topazHandoff(cur) {
  const kind = cur.kind === "video" ? "video" : "image", app = kind === "video" ? "Topaz Video" : "Gigapixel";
  if (cur.private) return toast("Incognito files can't go to the Topaz app", true);
  try {
    const r = await post("/manny/api/topaz/open", { filename: cur.filename, subfolder: cur.subfolder || "" });
    // one click: the picture opens in the app on the PC. The first time, the export folder is put on the
    // clipboard so it can be pasted once as the app's output folder; after that it's just open + export.
    if (!store.get("tzhint-" + kind, false)) { store.set("tzhint-" + kind, true); await copyText(r.export_to, `Opened in ${app} · its output folder is copied: paste it once in ${app}'s export settings`); }
    else toast(`Opened in ${app} on the PC · exports appear here`);
    tzWatch();
  } catch (e) { toast(e.message.slice(0, 200), true); }
}
/* after a hand-off, refresh the gallery when a new upscaled file shows up (checks for 30 min) */
let tzW = null;
function tzWatch() {
  clearInterval(tzW); const t0 = Date.now(); let seen = null;
  tzW = setInterval(async () => {
    if (Date.now() - t0 > 30 * 60e3) return clearInterval(tzW);
    try {
      const g = await api("/manny/api/gallery"); const ups = (g.items || g).filter(x => /Upscaled/.test(x.subfolder || "")).map(x => x.filename).join("|");
      if (seen === null) seen = ups; else if (ups !== seen) { seen = ups; toast("New upscaled file in the gallery"); S.gallery = []; if (location.hash.startsWith("#/gallery") && $("#modal").classList.contains("hidden")) route(); }
    } catch (e) { }
  }, 8000);
}
const tzJobs = [];
let tzT = null;
function tzPoll() {
  clearTimeout(tzT);
  if (!tzJobs.length) { updateTz(); return; }
  tzT = setTimeout(async () => {
    for (const j of tzJobs.slice()) {
      try {
        const r = await api("/manny/api/job/" + j.id);
        j.stage = r.stage;
        if (r.status !== "running") {
          tzJobs.splice(tzJobs.indexOf(j), 1);
          if (r.status === "done") { toast(`Upscaled: ${r.result.filename}`); S.gallery = []; if (location.hash.startsWith("#/gallery")) route(); }
          else toast("Topaz: " + (r.error || r.stage).slice(0, 240), true);
        }
      } catch (e) { }
    }
    updateTz(); tzPoll();
  }, 3000);
  updateTz();
}
function updateTz() {
  let el = $("#tzcard");
  if (!tzJobs.length) { if (el) el.remove(); return; }
  if (!el) { el = h(`<div id="tzcard" class="tzcard"></div>`); document.body.append(el); }
  el.innerHTML = tzJobs.map(j => `<div><b>✦ ${esc(j.label)}</b><small>${esc(j.stage || "Starting")}</small><button class="icon" data-s="${j.id}" title="Stop">✕</button></div>`).join("");
  $$("[data-s]", el).forEach(b => b.onclick = () => post(`/manny/api/finish/${b.dataset.s}/stop`, {}).then(() => toast("Stopping…")));
}
async function topazDialog(cur) {
  const t = await topaz(); const kind = cur.kind === "video" ? "video" : "image";
  if (!tzReady(t, kind)) return toast(kind === "video" ? "Topaz Video isn't ready (see Settings › Topaz)" : "Gigapixel isn't ready (see Settings › Topaz)", true);
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const st = store.get("tz-" + kind, kind === "video" ? { size: "1080", fps: "keep" } : { size: "4k", model: t.prefs.image_model });
  const sizes = kind === "video" ? [["keep", "Keep size"], ["1080", "1080p"], ["1440", "1440p"], ["4k", "4K"]] : [["2x", "2×"], ["4x", "4×"], ["2k", "2K"], ["4k", "4K"], ["8k", "8K"]];
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>✦ Upscale with ${kind === "video" ? "Topaz Video" : "Gigapixel"}</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">${esc(cur.filename)}. Runs on this PC beside the ComfyUI queue; the result lands in ${kind === "video" ? "Videos" : "Images"} › Upscaled.</p>
    <div><label class="lbl">${kind === "video" ? "Size" : "Size (2K / 4K / 8K = long edge)"}</label><div class="chips" id="tz-size">${sizes.map(([v, l]) => `<div class="chip" data-v="${v}">${l}</div>`).join("")}</div></div>
    ${kind === "video" ? `<div><label class="lbl">Frame rate</label><div class="chips" id="tz-fps"><div class="chip" data-v="keep">Keep</div><div class="chip" data-v="48">48 fps</div><div class="chip" data-v="60">60 fps</div></div></div>
      <p class="hint" style="margin:0">Model: ${esc((t.video_models.find(x => x[0] === t.prefs.video_model) || [, t.prefs.video_model])[1])} · change it in Settings.</p>` :
      `<div><label class="lbl">Model</label><select id="tz-model">${t.image_models.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select></div>`}
    <div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" data-go>Upscale</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  const chips = (id, key) => $$(`#${id} .chip`, d).forEach(c => { c.classList.toggle("on", c.dataset.v === st[key]); c.onclick = () => { st[key] = c.dataset.v; chips(id, key); }; });
  chips("tz-size", "size"); if (kind === "video") chips("tz-fps", "fps"); else $("#tz-model", d).value = st.model || "std";
  $("[data-go]", d).onclick = async () => {
    if (kind === "image") st.model = $("#tz-model", d).value;
    if (kind === "video" && st.size === "keep" && st.fps === "keep") return toast("Pick a size or a frame rate", true);
    store.set("tz-" + kind, { ...st });
    try {
      const r = await post("/manny/api/topaz/" + kind, { filename: cur.filename, subfolder: cur.subfolder || "", private: !!cur.private, size: st.size === "keep" ? "" : st.size, fps: st.fps, model: st.model });
      tzJobs.push({ id: r.id, label: cur.filename, stage: "Starting" }); close(); toast("Upscaling with Topaz…"); tzPoll();
    } catch (e) { toast("Couldn't start: " + e.message.slice(0, 200), true); }
  };
}

/* ---- video tools: fix a part (H3 retake), fix a face (H3 face refine), continue a shot (H3 motion context) */
function fmtT(t) { t = Math.max(0, +t || 0); return Math.floor(t / 60) + ":" + (t % 60).toFixed(1).padStart(4, "0"); }
async function videoJob(path, body, label, after) {
  const r = await post(path, body);
  toast(label + " started. It shows in the queue; the result opens when it's done.");
  const j = await pollJob(r.id);
  toast(label + " done: " + j.filename);
  if (after) after(j);
  const items = await loadGallery(true); const it = items.find(x => x.filename === j.filename);
  if (it) lightbox(it, [it]);
}
function videoFixDialog(cur, prompt, at) {
  if (cur.private) return toast("Video fixes don't work on Incognito videos yet", true);
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const url = itemUrl(cur);
  const st = { start: Math.max(0, (at || 0) - 1), end: (at || 0) + 1, mode: "picture", den: { picture: 0.7, face: 0.45 } };
  const d = h(`<div class="dialog vfix"><div class="row" style="justify-content:space-between"><h2>🩹 Fix a part of this video</h2><button class="icon" data-x>✕</button></div>
    <video src="${url}" controls playsinline preload="auto"></video>
    <div class="vrange"><div class="vbar"><div class="vsel"></div><div class="vhead"></div></div>
      <div class="row vmarks"><button class="btn sm" data-a>⇤ Start here</button><span class="vtimes"></span><button class="btn sm" data-b>End here ⇥</button><button class="ghost sm" data-play>▶ Play the marked part</button></div></div>
    <div><label class="lbl">What to fix</label><div class="chips" id="vmode">
      <div class="chip" data-v="picture" title="Re-draw the picture in the marked seconds; the sound stays as it is">Re-draw the picture</div>
      <div class="chip" data-v="face" title="Find the face and re-draw only it, frame by frame, with a light touch">Fix the face</div></div></div>
    <div><label class="lbl" id="vplbl">What should happen in the marked part</label><textarea id="vprompt" rows="3"></textarea></div>
    <div class="vden"><label class="lbl"><span id="vdenl"></span> <b id="vdenv"></b></label><input type="range" step="0.05" id="vden"></div>
    <p class="hint" id="vhint" style="margin:0"></p>
    <div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" data-go>Fix it</button></div></div>`);
  m.append(d);
  $(".foot", d).prepend(runOnSelect("On a Mac GPU a fix takes several minutes; Fix the face needs the FaceRefine nodes there"));
  const v = $("video", d), sel = $(".vsel", d), head = $(".vhead", d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  $("#vprompt", d).value = prompt || "";
  const draw = () => {
    const D = v.duration || 1; st.end = Math.min(st.end, D); if (st.end - st.start < 0.3) st.end = Math.min(D, st.start + 0.3);
    sel.style.left = (st.start / D * 100) + "%"; sel.style.width = ((st.end - st.start) / D * 100) + "%";
    $(".vtimes", d).textContent = `${fmtT(st.start)} → ${fmtT(st.end)}  (${(st.end - st.start).toFixed(1)} s)`;
    const face = st.mode === "face";
    $$("#vmode .chip", d).forEach(c => c.classList.toggle("on", c.dataset.v === st.mode));
    const r = $("#vden", d); r.min = face ? 0.25 : 0.4; r.max = face ? 0.7 : 0.9; r.value = st.den[st.mode];
    $("#vdenl", d).textContent = face ? "How much to change the face" : "How much to change the picture";
    $("#vdenv", d).textContent = st.den[st.mode].toFixed(2);
    $("#vplbl", d).textContent = face ? "Describe the person (helps the face come out right)" : "What should happen in the marked part";
    $("#vhint", d).textContent = face
      ? "LTX-2.5 tracks the biggest face in the marked part, re-draws only the face and blends it back. The sound stays. Lower = closer to the original, higher = more re-drawn."
      : "LTX-2.5 re-draws the marked seconds (plus 1 s each side so it blends in) from the video itself. The sound and everything else stay as they were. Lower = closer to the original.";
  };
  v.onloadedmetadata = () => { if (st.end > v.duration) { st.end = v.duration; st.start = Math.max(0, st.end - 2); } v.currentTime = st.start; draw(); };
  v.ontimeupdate = () => { head.style.left = (v.currentTime / (v.duration || 1) * 100) + "%"; if (v.dataset.loop && v.currentTime >= st.end) { v.pause(); delete v.dataset.loop; } };
  $(".vbar", d).onclick = e => { const r = e.currentTarget.getBoundingClientRect(); v.currentTime = (e.clientX - r.left) / r.width * (v.duration || 0); };
  $("[data-a]", d).onclick = () => { st.start = v.currentTime; if (st.end <= st.start + 0.3) st.end = Math.min(v.duration, st.start + 2); draw(); };
  $("[data-b]", d).onclick = () => { st.end = v.currentTime; if (st.start >= st.end - 0.3) st.start = Math.max(0, st.end - 2); draw(); };
  $("[data-play]", d).onclick = () => { v.currentTime = st.start; v.dataset.loop = "1"; v.play(); };
  $$("#vmode .chip", d).forEach(c => c.onclick = () => { st.mode = c.dataset.v; draw(); });
  $("#vden", d).oninput = e => { st.den[st.mode] = +e.target.value; $("#vdenv", d).textContent = st.den[st.mode].toFixed(2); };
  $("[data-go]", d).onclick = async () => {
    const body = { filename: cur.filename, subfolder: cur.subfolder || "", start: +st.start.toFixed(2), end: +st.end.toFixed(2), mode: st.mode,
      prompt: $("#vprompt", d).value.trim(), denoise: st.den[st.mode], target: runTarget() };
    if (st.mode !== "face" && !body.prompt) return toast("Say what should happen in the marked part", true);
    close();
    try { await videoJob("/manny/api/video/fix", body, st.mode === "face" ? "Face fix" : "Fix"); } catch (e) { toast(e.message.slice(0, 260), true); }
  };
}
function videoExtendDialog(cur, prompt) {
  if (cur.private) return toast("Continuing Incognito videos isn't supported yet", true);
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const st = { seconds: 5, ref: "", engine: store.get("xeng", "ltx") };
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>⏭ Continue this shot</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">The last second of the clip carries on into the new part, which is joined on. The result is a new, longer video; the original stays.</p>
    <div><label class="lbl">Engine</label><div class="chips" id="xeng"><div class="chip" data-v="ltx" title="Runs safely on this PC">LTX-2.5 (sharp, up to +7 s)</div><div class="chip" data-v="h3" title="MiniMax H3 References: the H3 model that rendered fine here. Not yet tested since the Nov 10 ComfyUI update">MiniMax H3 (up to +11 s)</div></div></div>
    <div><label class="lbl">What happens next</label><textarea id="xprompt" rows="4" placeholder="She keeps walking toward the camera, then stops and looks over her shoulder…"></textarea></div>
    <div><label class="lbl">How much longer</label><div class="chips" id="xsec">${[5, 7, 9, 11].map(x => `<div class="chip" data-v="${x}">+${x} s</div>`).join("")}</div></div>
    <div class="row"><div class="ref" id="xref" title="Optional: a picture of the character, to keep them the same"><span class="plus">+</span><span class="rl">Character picture<br><small>optional</small></span><input type="file" accept="image/*" hidden></div>
      <p class="hint" style="flex:1;margin:0" id="xhint"></p></div>
    <div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" data-go>Continue</button></div></div>`);
  m.append(d);
  $(".foot", d).prepend(runOnSelect("On a Mac GPU this is slow: H3 takes about an hour for 5 s; LTX isn't measured there yet"));
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  $("#xprompt", d).value = prompt || "";
  const chips = () => {
    const h3 = st.engine === "h3";
    if (!h3 && st.seconds > 7) st.seconds = 7;
    $$("#xeng .chip", d).forEach(c => { c.classList.toggle("on", c.dataset.v === st.engine); c.onclick = () => { st.engine = c.dataset.v; store.set("xeng", st.engine); chips(); }; });
    $$("#xsec .chip", d).forEach(c => { const off = !h3 && +c.dataset.v > 7; c.classList.toggle("hidden", off); c.classList.toggle("on", +c.dataset.v === st.seconds); c.onclick = () => { st.seconds = +c.dataset.v; chips(); }; });
    $("#xref", d).classList.toggle("hidden", !h3);
    $("#xhint", d).textContent = h3 ? "H3: without a character picture, the clip's last frame keeps the look. ~15–30 min on this PC." : "LTX keeps going from the last second of motion and makes new sound. ~15–20 min on this PC.";
  };
  chips();
  const rb = $("#xref", d), rf = $("input", rb);
  rb.onclick = () => rf.click();
  rf.onchange = async () => { if (!rf.files[0]) return; try { st.ref = await upload(rf.files[0]); rb.classList.add("on"); rb.innerHTML = `<img src="${inputUrl(st.ref)}"><span class="rl">Character</span>`; } catch (e) { toast(e.message, true); } };
  $("[data-go]", d).onclick = async () => {
    const pr = $("#xprompt", d).value.trim(); if (!pr) return toast("Say what happens next", true);
    close();
    try { await videoJob("/manny/api/video/extend", { filename: cur.filename, subfolder: cur.subfolder || "", prompt: pr, seconds: st.seconds, ref: st.engine === "h3" ? st.ref : "", engine: st.engine, target: runTarget() }, "Continue shot"); }
    catch (e) { toast(e.message.slice(0, 260), true); }
  };
}

/* ---- keyframe shot: pictures pinned at chosen seconds, LTX-2.5 (two passes) */
function keyframeDialog() {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const st = store.get("kf", { engine: "ltx", seconds: 8, aspect: "16:9", keys: [], prompt: "" });
  const AS = { "16:9": [1280, 704], "9:16": [704, 1280], "1:1": [960, 960], "21:9": [1344, 576] };
  const d = h(`<div class="dialog kfd"><div class="row" style="justify-content:space-between"><h2>✦ Keyframe shot</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">Give 2 or more pictures and when each should appear. The model moves between them in one continuous shot. Pictures that could plausibly be the same scene give a smooth move; very different ones give a cut.</p>
    <div><label class="lbl">Pictures and when they appear</label><div class="kfkeys"></div><button class="ghost" data-add>＋ Add a picture</button><input type="file" accept="image/*" multiple hidden></div>
    <div><label class="lbl">The shot</label><textarea id="kfp" rows="4" placeholder="Photoreal music video, a woman walks down an endless mirrored corridor, slow dolly forward…"></textarea></div>
    <div class="row" style="gap:18px;flex-wrap:wrap"><div><label class="lbl">Length</label><div class="chips" id="kfs">${[5, 8, 10, 12, 15].map(x => `<div class="chip" data-v="${x}">${x} s</div>`).join("")}</div></div>
      <div><label class="lbl">Shape</label><div class="chips" id="kfa">${Object.keys(AS).map(x => `<div class="chip" data-v="${x}">${x}</div>`).join("")}</div></div></div>
    <div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" data-go>Render the shot</button></div></div>`);
  m.append(d);
  $(".foot", d).prepend(runOnSelect("On a Mac GPU a 5 s shot takes about 40 min"));
  const close = () => { store.set("kf", st); m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  $("#kfp", d).value = st.prompt || ""; $("#kfp", d).oninput = e => st.prompt = e.target.value;
  const chips = (id, key, num) => $$(`#${id} .chip`, d).forEach(c => { c.classList.toggle("on", String(st[key]) === c.dataset.v); c.onclick = () => { st[key] = num ? +c.dataset.v : c.dataset.v; chips(id, key, num); draw(); }; });
  const draw = () => {
    const box = $(".kfkeys", d); box.innerHTML = "";
    st.keys.sort((a, b) => a.at - b.at);
    st.keys.forEach((k, n) => {
      const el = h(`<div class="kfk"><img src="${inputUrl(k.image)}"><div class="kft"><label>${n === 0 ? "Start" : "at"}</label><input type="number" min="0" max="${st.seconds}" step="0.5" value="${n === 0 ? 0 : k.at}" ${n === 0 ? "disabled" : ""}><span>s</span></div><button class="icon" title="Remove">✕</button></div>`);
      $("input", el).onchange = e => { k.at = Math.max(0.5, Math.min(st.seconds, +e.target.value || 0)); draw(); };
      $("button", el).onclick = () => { st.keys.splice(n, 1); draw(); };
      box.append(el);
    });
    const max = 8;
    $("[data-add]", d).classList.toggle("hidden", st.keys.length >= max);
  };
  const fi = $("input[type=file]", d);
  $("[data-add]", d).onclick = () => fi.click();
  fi.onchange = async () => {
    for (const f of [...fi.files]) {
      try { const val = await upload(f); const last = st.keys.length ? Math.max(...st.keys.map(k => k.at)) : -1; st.keys.push({ image: val, at: last < 0 ? 0 : Math.min(st.seconds, last + Math.max(1, st.seconds / 4)) }); }
      catch (e) { toast(e.message, true); }
    }
    fi.value = ""; draw();
  };
  chips("kfs", "seconds", true); chips("kfa", "aspect"); draw();
  $("[data-go]", d).onclick = async () => {
    if (st.keys.length < 2) return toast("Add at least two pictures", true);
    if (!(st.prompt || "").trim()) return toast("Describe the shot", true);
    const [w, hh] = AS[st.aspect] || AS["16:9"];
    const body = { keys: st.keys.map((k, n) => ({ image: k.image, at: n === 0 ? 0 : k.at })), prompt: st.prompt.trim(), seconds: st.seconds, width: w, height: hh, target: runTarget() };
    close();
    try { await videoJob("/manny/api/video/keyframes", body, "Keyframe shot"); } catch (e) { toast(e.message.slice(0, 260), true); }
  };
}

/* ---- 3D: try camera moves on one shot (quick grey Blender previews side by side) */
function tryMovesDialog(sc, shot, after) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const pick = new Set(store.get("trymoves", ["push_in", "orbit_left", "crane_up", "truck_right"]));
  const d = h(`<div class="dialog trym"><div class="row" style="justify-content:space-between"><h2>🎥 Try camera moves · ${esc(shot.name || "Shot")}</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">Blender renders this shot once per move (quick grey previews, ~4 s each) so you can compare them. Keep the camera and people where they are on the plan; only the move changes.</p>
    <div class="chips" id="tmv">${MOVES.filter(x => x[0] !== "static").map(([v, l]) => `<div class="chip" data-v="${v}">${l}</div>`).join("")}</div>
    <div class="row" style="gap:12px;align-items:center"><label class="hint">Strength <input type="range" id="tma" min="0.5" max="2" step="0.25" value="${store.get("trymovesamt", 1)}"></label><span class="hint" id="tmav"></span></div>
    <div class="trygrid"></div>
    <div class="foot"><span class="hint" id="tmst" style="margin-right:auto"></span><button class="ghost" data-x>Close</button><button class="btn primary" data-go>Render previews</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  const drawChips = () => $$("#tmv .chip", d).forEach(c => { c.classList.toggle("on", pick.has(c.dataset.v)); c.onclick = () => { if (pick.has(c.dataset.v)) pick.delete(c.dataset.v); else if (pick.size < 8) pick.add(c.dataset.v); drawChips(); }; });
  drawChips();
  const amt = $("#tma", d), av = () => $("#tmav", d).textContent = `×${(+amt.value).toFixed(2)} of each move's usual amount`;
  amt.oninput = av; av();
  $("[data-go]", d).onclick = async () => {
    if (!pick.size) return toast("Pick some moves", true);
    store.set("trymoves", [...pick]); store.set("trymovesamt", +amt.value);
    const moves = [...pick].map(v => ({ move: v, amount: +(((MOVES.find(x => x[0] === v) || [0, 0, 1])[2]) * +amt.value).toFixed(2) }));
    const bt = $("[data-go]", d); bt.disabled = true;
    try {
      const r = await post(`/manny/api/scenes/${sc.id}/explore`, { shot: shot.id, moves, seconds: Math.min(+shot.seconds || 4, 4) });
      const res = await pollJob(r.id, s2 => $("#tmst", d).textContent = s2);
      $("#tmst", d).textContent = "";
      const g = $(".trygrid", d); g.innerHTML = "";
      res.tries.forEach(t => {
        const lab = (MOVES.find(x => x[0] === t.move) || [0, t.move])[1];
        const c = h(`<div class="tryc"><video src="${fileUrl(t.video, sc.private)}" muted loop autoplay playsinline></video><div class="row" style="justify-content:space-between"><b>${esc(lab)}</b><button class="btn sm">Use this one</button></div></div>`);
        $("button", c).onclick = () => { shot.move = t.move; shot.amount = t.amount; saveScene(sc); toast(`${shot.name || "Shot"}: ${lab}`); close(); after && after(); };
        g.append(c);
      });
    } catch (e) { toast(e.message.slice(0, 240), true); }
    bt.disabled = false;
  };
}

/* ---- finish a picked take: upscale, smoother motion, sound */
function finishDialog(b, s, n, after) {
  const t = s.takes[s.pick]; if (!t || !t.output) return;
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const st = store.get("finish", { upscale: true, fps: "keep", sound: "keep", size: "1080" });
  st.size = st.size || "1080";
  let audio = "";
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>Finish shot ${n + 1}</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">Uses take ${s.pick + 1}${t.q === "final" ? " (final quality)" : t.q === "draft" ? ' (draft quality: render it in final quality first for the best result)' : ""}. Runs in the queue; you can leave this page.</p>
    <label class="tog"><input type="checkbox" id="f-up" ${st.upscale ? "checked" : ""}><span><b id="f-upt">Upscale to 1080p</b><small id="f-ups">Upscale video app. The slow step: several minutes per shot.</small></span></label>
    <div class="chips hidden" id="f-size"><div class="chip" data-v="1080">1080p</div><div class="chip" data-v="1440">1440p</div><div class="chip" data-v="4k">4K</div></div>
    <div><label class="lbl">Frame rate</label><div class="chips" id="f-fps"><div class="chip" data-v="keep">Keep</div><div class="chip" data-v="48">48 fps</div><div class="chip" data-v="60">60 fps</div></div></div>
    <div><label class="lbl">Sound</label><div class="chips" id="f-snd"><div class="chip" data-v="keep">Keep</div><div class="chip" data-v="mute">Mute</div><div class="chip" data-v="replace">Replace…</div></div></div>
    <div class="ref audio hidden" id="f-aud" style="aspect-ratio:auto;min-height:70px"><span class="plus">+</span><span class="rl">Drop a sound or song (wav, mp3). It's cut to the shot's length with a short fade.</span><input type="file" hidden accept="audio/*"></div>
    <div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" data-go>Finish shot</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  const chips = (id, key) => { $$(`#${id} .chip`, d).forEach(c => { c.classList.toggle("on", c.dataset.v === st[key]); c.onclick = () => { st[key] = c.dataset.v; chips(id, key); $("#f-aud", d).classList.toggle("hidden", st.sound !== "replace"); }; }); };
  chips("f-fps", "fps"); chips("f-snd", "sound"); chips("f-size", "size"); $("#f-aud", d).classList.toggle("hidden", st.sound !== "replace");
  topaz().then(tz => { if (!tzReady(tz, "video")) return; $("#f-upt", d).textContent = "Upscale with Topaz Video"; $("#f-ups", d).textContent = "Topaz Video also makes the 48 / 60 fps version in the same pass. Falls back to the ComfyUI apps if it fails."; $("#f-size", d).classList.remove("hidden"); });
  const au = $("#f-aud", d), fi = $("input", au);
  const take = async f => { audio = await upload(f); au.classList.add("on"); $(".plus", au).className = "wave"; $(".wave", au).textContent = "♪"; $(".rl", au).textContent = f.name; };
  au.onclick = () => fi.click(); fi.onchange = () => fi.files[0] && take(fi.files[0]);
  au.ondragover = e => e.preventDefault(); au.ondrop = e => { e.preventDefault(); e.dataTransfer.files[0] && take(e.dataTransfer.files[0]); };
  $("[data-go]", d).onclick = async () => {
    st.upscale = $("#f-up", d).checked; store.set("finish", { ...st });
    if (st.sound === "replace" && !audio) return toast("Add the sound first", true);
    if (!st.upscale && st.fps === "keep" && st.sound === "keep") return toast("Pick at least one thing to change", true);
    try {
      const r = await post("/manny/api/finish", { ...t.output, private: !!b.private, upscale: st.upscale, size: st.size, fps: st.fps, sound: st.sound, audio, name: `${b.name} shot ${n + 1}`, target: runTarget() });
      s.finish = { id: r.id, stage: "Starting", status: "running", from: s.pick }; saveBoard(b, true); close(); toast("Finishing shot " + (n + 1)); after(); pollFinish();
    } catch (e) { toast("Couldn't start: " + e.message.slice(0, 200), true); }
  };
}
let finishT = null;
async function pollFinish() {
  clearTimeout(finishT);
  let busy = false;
  for (const b of S.boards) for (const s of b.shots) {
    if (!s.finish || s.finish.status !== "running") continue;
    busy = true;
    try {
      const j = await api("/manny/api/finish/" + s.finish.id);
      const was = s.finish.stage;
      s.finish.stage = j.stage; s.finish.status = j.status === "lost" ? "failed" : j.status;
      if (j.status === "done") { s.final = { filename: j.result.filename, subfolder: j.result.subfolder }; toast("Shot finished"); S.gallery = []; }
      if (j.status === "failed" || j.status === "lost") { s.finish.error = j.error || j.stage; toast("Finishing failed: " + (j.error || j.stage).slice(0, 200), true); }
      if (j.status !== "running") saveBoard(b, true);
      if ((was !== j.stage || j.status !== "running") && S.sbDraw) S.sbDraw();
    } catch { }
  }
  if (busy) finishT = setTimeout(pollFinish, 4000);
}

async function pageStoryboard(main) {
  await loadBoards();
  if (!S.boards.length) { const b = { name: "My film", shots: [newShot(), newShot()] }; S.boards.push(b); await saveBoard(b, true); }
  let b = curBoard(); S.boardId = b.id; store.set("board", b.id);
  b.shots.forEach(ensureTakes);
  if (b.takes == null) b.takes = 2;
  const p = h(`<div class="page"><div class="eyebrow">Storyboard</div>
    <div class="row sbtop"><h1 id="bname" contenteditable="true" spellcheck="false">${esc(b.name)}</h1></div>
    <div class="filters"><select id="bsel"></select><button class="ghost" id="bnew">＋ New film</button><button class="ghost danger" id="bdel">Delete film</button>
      <span class="hint" id="vsum"></span></div>
    <div class="howto"><b>1</b> Draft takes (fast) <i>→</i> <b>2</b> Pick the best ★ <i>→</i> <b>3</b> Render it in final quality <i>→</i> <b>4</b> Finish: 1080p, smoother motion, sound <i>→</i> <b>5</b> Make the film</div>
    <div class="shots" id="shots"></div>
    <div class="row sbbar"><button class="btn" id="add">＋ Add shot</button><label class="hint">Takes per shot <select id="ntakes">${[1, 2, 3, 4].map(k => `<option ${k === b.takes ? "selected" : ""}>${k}</option>`).join("")}</select></label><button class="btn" id="all">Draft every shot without takes</button><button class="btn primary" id="film">🎬 Make the film</button></div>
    <div id="filmout"></div></div>`);
  main.append(p);
  S.boards.forEach(x => $("#bsel", p).append(new Option(x.name, x.id, false, x.id === b.id)));
  $("#bsel", p).onchange = e => { S.boardId = e.target.value; store.set("board", S.boardId); route(); };
  $("#bnew", p).onclick = async () => { const nb = { name: "Film " + (S.boards.length + 1), shots: [newShot()] }; S.boards.push(nb); await saveBoard(nb, true); S.boardId = nb.id; store.set("board", nb.id); route(); };
  $("#bdel", p).onclick = async () => { if (!confirm(`Delete “${b.name}”? The rendered shots stay in your gallery.`)) return; await api("/manny/api/boards/" + b.id, { method: "DELETE" }); S.boardId = null; route(); };
  $("#bname", p).oninput = e => { b.name = e.target.textContent.trim() || "Untitled"; saveBoard(b); };
  $("#ntakes", p).onchange = e => { b.takes = +e.target.value; saveBoard(b); };
  const vs = videoSummary();
  $("#vsum", p).innerHTML = vs ? `Shots use the Video app's settings: <b>${esc(vs.text)}</b> · <a href="#/create/${vs.k}">change</a>` : "";
  await resolveShots(b);
  const box = $("#shots", p);
  const tstat = t => t.output ? "" : t.pid && S.running === t.pid ? `${Math.round(S.progress * 100)}%` : t.status === "queued" ? "Waiting" : t.status === "failed" ? "Failed" : t.status === "lost" ? "Stopped" : "";
  const draw = () => {
    if (!document.body.contains(box)) { S.sbDraw = null; return; }
    box.innerHTML = "";
    b.shots.forEach((s, n) => {
      ensureTakes(s);
      const pk = s.takes[s.pick], showFinal = s.final && s.finish && s.finish.from === s.pick;
      const main = showFinal ? s.final : pk && pk.output;
      const pending = s.takes.find(t => !t.output && (t.status === "queued"));
      const fin = s.finish && s.finish.status === "running" ? `<div class="fbadge run">Finishing: ${esc(s.finish.stage)}…</div>` : showFinal ? `<div class="fbadge">✓ Finished</div>` : s.finish && s.finish.status === "failed" ? `<div class="fbadge bad" title="${esc(s.finish.error || "")}">Finish failed</div>` : "";
      const pv3 = s.previs && (s.previs.lit || s.previs.grey);
      const el = h(`<div class="shot" data-shot="${s.id}"><div class="shead"><b>SHOT ${n + 1}${pv3 ? ` <span class="b3d">3D</span>` : ""}</b><div class="row"><button class="icon" data-up title="Move up">↑</button><button class="icon" data-dn title="Move down">↓</button><button class="icon" data-rm title="Remove">✕</button></div></div>
        <div class="sbody">${pv3 ? `<div class="sframe s3dbox"><video src="${fileUrl(pv3.video, b.private)}" muted loop playsinline></video>${s.styled ? `<img src="${inputUrl(s.styled.first)}" title="Styled first frame">` : ""}
          <select class="seng"><option value="keyframes">Styled keyframes</option><option value="motion">Follow 3D motion</option><option value="render">Blender render as is</option></select><span class="hint s3d">${esc(s.status3d || (s.styled ? "Frames styled" : ""))}</span>${s.styled ? `<button class="ghost sm" data-restyle>↻ Restyle frames</button>` : `<button class="ghost sm" data-style>🎨 Style frames</button>`}</div>` : ""}<div class="ref ${s.image ? "on" : ""} sframe ${pv3 ? "hidden" : ""}">${s.image ? `<img src="${inputUrl(s.image)}">` : `<span class="plus">+</span>`}<span class="rl">${s.image ? (s.cont ? "Last frame of shot " + s.cont : "First frame") : "First frame (optional)"}</span><button class="x" title="Remove">✕</button><input type="file" hidden accept="image/*"></div>
        <div class="scol"><textarea placeholder="What happens in this shot: who, action, place, mood, dialogue…">${esc(s.prompt)}</textarea>
          <div class="row"><select class="scam"><option value="">Camera: model decides</option>${CAMERA.map(([nm, w2]) => `<option value="${esc(w2)}" ${s.camera === w2 ? "selected" : ""}>Camera: ${esc(nm)}</option>`).join("")}</select><button class="ghost" data-pw>✎ Prompt writer</button></div>
          <details class="trim" ${s.trimIn || s.trimOut || s.caption ? "open" : ""}><summary>✂ Trim &amp; caption</summary><div class="row"><label>From <input type="number" min="0" step="0.1" data-tin value="${s.trimIn || ""}" placeholder="0"> s</label><label>to <input type="number" min="0" step="0.1" data-tout value="${s.trimOut || ""}" placeholder="end"> s</label></div><input type="text" data-cap placeholder="Caption on screen (optional)" value="${esc(s.caption || "")}"></details></div>
        <div class="sout"><div class="smain">${main ? `<video src="${fileUrl(main, b.private)}" poster="${thumbUrl({ ...main, private: b.private }, 640)}" muted loop playsinline preload="none"></video>` : `<div class="swait">${pending ? (tstat(pending) === "Waiting" ? "Waiting in queue" : "Rendering " + tstat(pending)) : "Not rendered yet"}</div>`}${fin}</div>
          <div class="takes">${s.takes.map((t, k) => `<div class="take ${k === s.pick ? "on" : ""} ${t.output ? "" : "wait"}" data-k="${k}" data-pid="${t.pid || ""}" title="${t.q === "final" ? "Final quality" : t.q === "draft" ? "Draft" : "Take"} ${k + 1}${t.seed != null ? " · seed " + t.seed : ""}">${t.output ? `<img src="${thumbUrl({ ...t.output, private: b.private }, 640)}" loading="lazy"><video muted loop playsinline preload="none" data-src="${fileUrl(t.output, b.private)}"></video>` : `<span>${tstat(t) || "…"}</span>`}<i>${k + 1}${t.q === "final" ? "F" : ""}</i>${t.output ? `<button class="tx" data-tx="${k}" title="Remove take">✕</button>` : ""}</div>`).join("")}</div>
          <div class="sacts">
            <button class="ghost" data-go>${s.takes.length ? "＋ " + b.takes + " more take" + (b.takes > 1 ? "s" : "") : "Draft " + b.takes + " take" + (b.takes > 1 ? "s" : "")}</button>
            ${pk && pk.output && pk.q !== "final" && pk.seed != null ? `<button class="ghost" data-hq title="Same seed, full steps. Usually close to the draft, with more detail.">★ Final quality</button>` : ""}
            ${s.takes.filter(t => t.output).length + (s.final ? 1 : 0) > 1 ? `<button class="ghost" data-cmp>⇆ Compare</button>` : ""}
            ${pk && pk.output ? `<button class="ghost" data-rev title="The local AI watches the take and scores it">✦ Review${pk.review && pk.review.review ? " · " + esc(String(pk.review.review.score)) + "/10" : ""}</button><button class="ghost" data-fin>✓ Finish…</button><button class="ghost" data-cloud title="Ask Claude to make the final render on Seedance 2.5 (Higgsfield) from this approved take">${s.final_request && s.final_request.status === "waiting" ? "☁ Seedance requested" : "☁ Seedance…"}</button><button class="ghost" data-cont>Continue →</button>` : ""}
          </div></div></div></div>`);
      const fr = $(".sframe:not(.s3dbox)", el), fi = $("input", fr);
      const s3 = $(".s3dbox", el);
      if (s3) {
        const v3 = $("video", s3); s3.onmouseenter = () => v3.play().catch(() => { }); s3.onmouseleave = () => v3.pause();
        $(".seng", s3).value = s.engine || "keyframes"; $(".seng", s3).onchange = e => { s.engine = e.target.value; saveBoard(b); };
        const sty = $("[data-style],[data-restyle]", s3);
        if (sty) sty.onclick = async () => { sty.disabled = true; try { await loadScenes(); await loadLocs(); await styleFrames(b, s, st => { const e = $(".s3d", s3); if (e) e.textContent = st; }); saveBoard(b, true); draw(); } catch (e) { toast(e.message.slice(0, 200), true); sty.disabled = false; } };
      }
      const take = async f => { s.image = await upload(f); s.cont = null; saveBoard(b); draw(); };
      fr.onclick = e => { if (e.target.closest(".x")) return; fi.click(); };
      fi.onchange = () => fi.files[0] && take(fi.files[0]);
      fr.ondragover = e => e.preventDefault(); fr.ondrop = e => { e.preventDefault(); e.dataTransfer.files[0] && take(e.dataTransfer.files[0]); };
      $(".x", fr).onclick = () => { s.image = ""; s.cont = null; saveBoard(b); draw(); };
      const ta = $("textarea", el); ta.oninput = e => { s.prompt = e.target.value; saveBoard(b); };
      $(".scam", el).onchange = e => { s.camera = e.target.value; saveBoard(b); };
      $("[data-pw]", el).onclick = () => promptWriter(s.prompt, v => { s.prompt = v; if (CAMERA.some(c => v.includes(c[1]))) s.camera = ""; saveBoard(b, true); draw(); });
      const vid = $(".smain video", el);
      if (vid) { vid.onmouseenter = () => vid.play().catch(() => { }); vid.onmouseleave = () => vid.pause(); vid.onclick = () => { const it = S.gallery.find(x => x.filename === main.filename); lightbox(it || { ...main, private: !!b.private, kind: "video", mtime: Date.now() / 1000, size: 0 }); }; }
      $$(".take", el).forEach(tk => {
        const k = +tk.dataset.k, tv = $("video", tk);
        if (tv) { tk.onmouseenter = () => { if (!tv.src) tv.src = tv.dataset.src; tv.classList.add("on"); tv.play().catch(() => { }); }; tk.onmouseleave = () => { tv.pause(); tv.classList.remove("on"); }; }
        tk.onclick = e => { if (e.target.closest(".tx")) return; if (!s.takes[k].output) return; s.pick = k; syncPick(s); saveBoard(b, true); draw(); };
      });
      $$("[data-tx]", el).forEach(x => x.onclick = () => { const k = +x.dataset.tx; if (!confirm(`Remove take ${k + 1} from this shot? (The video stays in your gallery.)`)) return; s.takes.splice(k, 1); if (s.pick === k) s.pick = s.takes.findIndex(t => t.output); else if (s.pick > k) s.pick--; if (s.pick < 0) s.pick = null; if (s.finish && s.finish.from === k) { s.finish = null; s.final = null; } syncPick(s); saveBoard(b, true); draw(); });
      $("[data-go]", el).onclick = async () => { await renderShot(b, s, { q: "draft", count: b.takes }); draw(); pollQueue(); };
      const hq = $("[data-hq]", el); if (hq) hq.onclick = async () => { await renderShot(b, s, { q: "final", seed: pk.seed, from: s.pick }); toast("Final-quality render queued (same seed)"); draw(); pollQueue(); };
      const cmp = $("[data-cmp]", el); if (cmp) cmp.onclick = () => compareTakes(b, s, k => { s.pick = k; syncPick(s); saveBoard(b, true); draw(); });
      const fb = $("[data-fin]", el); if (fb) fb.onclick = () => finishDialog(b, s, n, draw);
      const cld = $("[data-cloud]", el); if (cld) cld.onclick = async () => {
        if (!b.id) return;
        const on = s.final_request && s.final_request.status === "waiting";
        if (on) { if (!confirm("Cancel the Seedance request for this shot?")) return; }
        const notes = on ? "" : prompt("Final render on Seedance 2.5, done by Claude through Higgsfield (uses your Higgsfield credits).\nNotes for the render (optional):", "") ;
        if (notes === null) return;
        try { const r = await post(`/manny/api/boards/${b.id}/shots/${s.id}/final_request`, { engine: on ? "none" : "seedance", notes }); s.final_request = r.final_request || undefined; draw(); toast(on ? "Request cancelled" : "Requested. In Claude, say: “do the Seedance final renders in Studio”."); }
        catch (e) { toast(e.message.slice(0, 200), true); }
      };
      const rb = $("[data-rev]", el); if (rb) rb.onclick = () => reviewModal(b, s, pk, v => { s.prompt = v; saveBoard(b, true); draw(); toast("Prompt updated. Draft new takes when ready."); });
      $("[data-tin]", el).oninput = e => { s.trimIn = +e.target.value || 0; saveBoard(b); };
      $("[data-tout]", el).oninput = e => { s.trimOut = +e.target.value || 0; saveBoard(b); };
      $("[data-cap]", el).oninput = e => { s.caption = e.target.value; saveBoard(b); };
      const cb = $("[data-cont]", el); if (cb) cb.onclick = async () => {
        cb.disabled = true; cb.textContent = "Grabbing last frame…";
        try {
          const { value } = await post("/manny/api/lastframe", { ...(showFinal ? s.final : pk.output), private: !!b.private });
          const ns = newShot(s.prompt, value); ns.camera = s.camera; ns.cont = n + 1;
          b.shots.splice(n + 1, 0, ns); saveBoard(b, true); draw(); toast(`Shot ${n + 2} starts where shot ${n + 1} ends. Change its prompt to say what happens next.`);
        } catch (e) { toast("Couldn't read the last frame: " + e.message.slice(0, 200), true); cb.disabled = false; cb.textContent = "Continue →"; }
      };
      $("[data-rm]", el).onclick = () => { if (s.output && !confirm("Remove this shot from the storyboard? (The videos stay in your gallery.)")) return; b.shots.splice(n, 1); saveBoard(b); draw(); };
      $("[data-up]", el).onclick = () => { if (n > 0) { b.shots.splice(n - 1, 0, b.shots.splice(n, 1)[0]); saveBoard(b); draw(); } };
      $("[data-dn]", el).onclick = () => { if (n < b.shots.length - 1) { b.shots.splice(n + 1, 0, b.shots.splice(n, 1)[0]); saveBoard(b); draw(); } };
      box.append(el);
    });
  };
  S.sbDraw = draw;
  draw();
  pollFinish();
  $("#add", p).onclick = () => { b.shots.push(newShot()); saveBoard(b); draw(); };
  $("#all", p).onclick = async () => { const todo = b.shots.filter(s => !s.takes.length); if (!todo.length) return toast("Every shot has takes. Use “＋ more takes” on a shot for more."); for (const s of todo) await renderShot(b, s, { q: "draft", count: b.takes }); toast(`Queued ${todo.length * b.takes} take${todo.length * b.takes > 1 ? "s" : ""}`); draw(); pollQueue(); };
  const clipOf = s => (s.final && s.finish && s.finish.from === s.pick) ? s.final : s.output;
  const showFilm = () => {
    const fo = $("#filmout", p); fo.innerHTML = "";
    if (b.film) fo.append(h(`<div class="filmbox"><div class="section-head"><h2>The film</h2><a href="${fileUrl(b.film, b.private)}" download>Download</a></div><video src="${fileUrl(b.film, b.private)}" controls playsinline></video><p class="hint">Saved in Videos › Films as “${esc(b.film.filename)}”. Uses each shot's finished version when it has one, else its picked take.</p></div>`));
  };
  showFilm();
  $("#film", p).onclick = () => filmDialog(b, clipOf, showFilm);
}

/* ------------------------------------------------------------ background jobs (AI, film edit, reel, downloads) */
function pollJob(id, onStage) {
  return new Promise((res, rej) => {
    const tick = async () => {
      try {
        const j = await api("/manny/api/job/" + id);
        if (onStage) onStage(j.stage, j);
        if (j.status === "done") return res(j.result);
        if (j.status === "failed" || j.status === "lost") return rej(new Error(j.error || j.stage));
      } catch (e) { }
      setTimeout(tick, 1500);
    };
    tick();
  });
}

/* ------------------------------------------------------------ LoRAs */
S.loras = store.get("loras", {});
const saveLoras = () => store.set("loras", S.loras);
// which Studio models a Civitai base model works with
function loraFamily(base) {
  const b = (base || "").toLowerCase();
  if (/illustrious|noobai|pony|sdxl/.test(b)) return "sdxl";
  if (/qwen/.test(b)) return "qwen";
  if (/z-?image/.test(b)) return "zimage";
  if (/anima/.test(b)) return "anima";
  if (/wan.*2\.2|wan video 2\.2/.test(b)) return "wan22";
  if (/ltx/.test(b)) return "ltx";
  if (/minimax|h3/.test(b)) return "h3";
  if (/krea/.test(b)) return "krea";
  if (/flux\.?\s*(3|klein)|flux 2|flux\.2/.test(b)) return "flux3";
  if (/moss/.test(b)) return "moss";
  return "";
}
function modelFamily(opt) {
  const o = (opt || "").toLowerCase();
  if (/anime desire|illustrious/.test(o)) return "sdxl";
  if (/qwen/.test(o)) return "qwen";
  if (/z-image/.test(o)) return "zimage";
  if (/anima\b|anima \(/.test(o)) return "anima";
  if (/wan 2\.2/.test(o)) return "wan22";
  if (/ltx/.test(o)) return "ltx";
  if (/minimax|h3/.test(o)) return "h3";
  if (/krea/.test(o)) return "krea";
  if (/flux 3|klein/.test(o)) return "flux3";
  if (/moss/.test(o)) return "moss";
  return "";
}
/* Civitai base names per Studio family (for "popular for this model") */
const FAM_BASES = { moss: "MOSS Image", qwen: "Qwen", krea: "Krea 2|Krea 2 Turbo", flux3: "Flux.2 Klein 9B|Flux.2 Klein 4B|Flux.2 D", zimage: "ZImageTurbo",
  anima: "Anima", sdxl: "Illustrious|NoobAI|Pony|SDXL 1.0", h3: "MiniMax H3", wan22: "Wan Video 2.2 I2V-A14B|Wan Video 2.2 T2V-A14B", ltx: "LTXV" };
const FAM_NAME = { moss: "MOSS", qwen: "Qwen Image", krea: "Krea 2", flux3: "FLUX 3 Klein", zimage: "Z-Image", anima: "Anima", sdxl: "Anime Desire / SDXL", h3: "MiniMax H3", wan22: "Wan 2.2", ltx: "LTX" };
// the files Studio's apps already use: everything else of the same family is a "style" (finetune) you added
const STOCK_UNETS = /^(moss_image_sft_int8|qwen_image_2\.1|krea2_turbo|moodykrea2mix|flux3-image-klein|z_image_turbo_int8|anima-base|minimax_h3_|wan2\.2_|ltx-|seedvr2|p-h3-image|acestep|wan_animate|flux2_dev)/i;
function fileFamily(name) {
  const n = (name || "").toLowerCase().split(/[\\/]/).pop();
  if (/moss/.test(n)) return "moss"; if (/nitrofusion|z[-_]?image/.test(n)) return "zimage"; if (/anima/.test(n)) return "anima";
  if (/krea/.test(n)) return "krea"; if (/qwen[-_]?image/.test(n)) return "qwen"; if (/minimax[-_]?h3|_h3_/.test(n)) return "h3";
  if (/klein|flux3/.test(n)) return "flux3"; return "";
}
let variantCache = null;
async function loadVariants(force) {
  if (!variantCache || force) {
    try {
      const items = (await api("/manny/api/models?folder=diffusion_models" + (S.incog ? "&private=1" : ""))).items || [];
      const seen = new Map();
      items.filter(m => !STOCK_UNETS.test(m.name.split(/[\\/]/).pop())).forEach(m => {
        const k = m.name.split(/[\\/]/).pop().toLowerCase(), prev = seen.get(k);
        const x = { file: m.name, title: (m.title || prettyFile(m.name)).split(/[|｜丨]/)[0].trim(), base: m.base || "", family: loraFamily(m.base) || fileFamily(m.name) };
        if (!prev || (!prev.base && x.base)) seen.set(k, x);
      });
      variantCache = [...seen.values()].filter(m => m.family);
    } catch { variantCache = []; }
  }
  return variantCache;
}
S.variant = store.get("variant", {});
S.pose = store.get("pose", {});
let poseCache = null;
async function loadPoses(force) { if (!poseCache || force) { try { poseCache = await api("/manny/api/poses"); } catch { poseCache = { packs: [], controlnets: {} }; } } return poseCache; }
// which models the app's "Uncensored" switch covers (from its label), plus models that are uncensored anyway
const UNCENSORED_ALWAYS = /anima/i;
function uncInput(app) { return app.inputs.find(i => i.kind === "toggle" && /uncensored/i.test(i.label)); }
function uncCovers(input, opt) {
  if (UNCENSORED_ALWAYS.test(opt || "")) return "always";
  const m = /\(([^)]*)\)/.exec(input ? input.label : ""); if (!m) return false;
  return m[1].split(/[·,]/).map(x => x.replace(/only/i, "").trim().toLowerCase()).filter(Boolean).some(w => (opt || "").toLowerCase().includes(w)) ? "toggle" : false;
}
let loraCache = null, ckptCache = null;
const L0 = app => layout(app);
S.ckpt = store.get("ckpt", {});
async function loadCkpts(force) {
  if (!ckptCache || force) { try { ckptCache = (await api("/manny/api/models?folder=checkpoints" + (S.incog ? "&private=1" : ""))).items.filter(m => m.folder === "checkpoints" && !/inpaint/i.test(m.name)); } catch { ckptCache = []; } }
  return ckptCache;
}
async function loadLoras(force) { if (!loraCache || force) { try { loraCache = await api("/manny/api/loras" + PQ()); } catch { loraCache = []; } } return loraCache; }
function loraSection(app, form) {
  const L = layout(app), v = valuesFor(app);
  const fam = L.model ? modelFamily(v[L.model.key]) : "";
  const picks = S.loras[mkey(app)] || (S.loras[mkey(app)] = []);
  const sec = h(`<div class="fsec lorasec"><div class="lblrow"><label class="lbl">LoRAs</label><a href="#/loras" class="pwlink">Find more →</a></div><div class="lpicks"></div><button class="ghost" data-add>＋ Add a LoRA</button></div>`);
  const box = $(".lpicks", sec);
  picks.forEach((p, k) => {
    const bad = p.family && fam && p.family !== fam;
    const el = h(`<div class="lpick ${bad ? "bad" : ""}"><div class="lpn"><b>${esc(p.label || p.name.split(/[\\/]/).pop())}</b>${bad ? `<span class="warn">made for another model, skipped</span>` : p.trained && p.trained.length ? `<span>adds “${esc(p.trained.slice(0, 3).join(", "))}”</span>` : ""}</div>
      <input type="range" min="0" max="1.5" step="0.05" value="${p.strength}"><span class="lv">${(+p.strength).toFixed(2)}</span><button class="icon" title="Remove">✕</button></div>`);
    const r = $("input", el);
    r.oninput = () => { p.strength = +r.value; $(".lv", el).textContent = p.strength.toFixed(2); saveLoras(); };
    $("button", el).onclick = () => { picks.splice(k, 1); saveLoras(); renderForm(form, app); };
    box.append(el);
  });
  $("[data-add]", sec).onclick = () => loraPicker(app, fam, () => renderForm(form, app));
  if (fam && FAM_BASES[fam]) suggestLoras(app, fam, sec, form);
  return sec;
}
/* "Popular for <model>": installed ones are one click to add, the rest one click to download (then added) */
const sugCache = {};
async function suggestLoras(app, fam, sec, form) {
  const key = fam + (S.incog ? "|p" : "");
  try {
    if (!sugCache[key]) sugCache[key] = api(`/manny/api/civitai/search?type=LORA&base=${encodeURIComponent(FAM_BASES[fam])}&sort=${encodeURIComponent("Most Downloaded")}&period=Month${S.incog ? "&private=1" : ""}`);
    const [d, mine] = await Promise.all([sugCache[key], loadLoras()]);
    const items = (d.items || []).slice(0, 8); if (!items.length || !document.body.contains(sec)) return;
    const have = new Set(mine.map(l => l.file.split(/[\\/]/).pop().toLowerCase()));
    const picks = S.loras[mkey(app)] || [];
    const row = h(`<div class="sugg"><label class="lbl">Popular for ${esc(FAM_NAME[fam] || fam)} this month</label><div class="sgrid"></div></div>`);
    items.forEach(it => {
      const vv = it.versions.find(x => loraFamily(x.base) === fam) || it.versions[0]; if (!vv) return;
      const f = (vv.files || []).find(x => /\.safetensors$/i.test(x.name)); if (!f) return;
      const installed = have.has(f.name.toLowerCase()), added = picks.some(p => p.name.split(/[\\/]/).pop().toLowerCase() === f.name.toLowerCase());
      const img = (vv.images || [])[0] || it.image;
      const c = h(`<div class="scard ${added ? "on" : ""}" title="${esc(it.name)} · ${esc(vv.base || "")} · ${(it.downloads || 0).toLocaleString()} downloads">${img ? `<img src="/manny/api/civitai/img?u=${encodeURIComponent(img.url || img)}" loading="lazy">` : `<div class="lph">LoRA</div>`}<b>${esc(it.name.slice(0, 40))}</b><button class="btn sm">${added ? "✓ Added" : installed ? "＋ Add" : "↓ Get"}</button></div>`);
      const btn = $("button", c);
      btn.onclick = async () => {
        if (added) return;
        const addPick = file => { const ps = S.loras[mkey(app)] || (S.loras[mkey(app)] = []); if (!ps.some(p => p.name === file)) ps.push({ name: file, label: it.name, strength: 0.8, trained: vv.trained || [], family: fam }); saveLoras(); };
        if (installed) { const l = mine.find(l => l.file.split(/[\\/]/).pop().toLowerCase() === f.name.toLowerCase()); addPick(l.file); return renderForm(form, app); }
        btn.disabled = true; btn.textContent = "Downloading…";
        try {
          const r = await post("/manny/api/civitai/download", { version: vv.id, file: f.name, type: "LORA", private: !!S.incog });
          for (let k = 0; k < 400; k++) {
            await new Promise(res => setTimeout(res, 3000));
            const j = await api("/manny/api/job/" + r.id);
            if (j.status === "running") { btn.textContent = (j.stage || "Downloading").replace(/^Downloading\s*/, "↓ ").slice(0, 16); continue; }
            if (j.status !== "done") throw new Error(j.error || j.stage);
            break;
          }
          loraCache = null; const all = await loadLoras(true);
          const l = all.find(l => l.file.split(/[\\/]/).pop().toLowerCase() === f.name.toLowerCase());
          if (l) addPick(l.file);
          toast(`${it.name} downloaded and added`); if (document.body.contains(sec)) renderForm(form, app);
        } catch (e) { btn.disabled = false; btn.textContent = "↓ Get"; toast("Download failed: " + e.message.slice(0, 160), true); }
      };
      $(".sgrid", row).append(c);
    });
    sec.append(row);
  } catch { }
}
async function loraPicker(app, fam, done) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>Add a LoRA</h2><button class="icon" data-x>✕</button></div>
    <input type="text" class="search" placeholder="Search your LoRAs…"><div class="lgrid small"></div>
    <p class="hint">Only LoRAs made for the selected model work with it. Others are dimmed. <a href="#/loras">Find more on Civitai →</a></p></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $("[data-x]", d).onclick = close; $("a", d).onclick = close;
  const all = await loadLoras(true);
  const draw = () => {
    const q = $(".search", d).value.trim().toLowerCase(), g = $(".lgrid", d); g.innerHTML = "";
    const list = all.filter(l => !q || (l.name + " " + l.file + " " + l.trained.join(" ")).toLowerCase().includes(q))
      .sort((a, b) => (loraFamily(b.base) === fam) - (loraFamily(a.base) === fam));
    if (!list.length) g.append(h(`<div class="empty">No LoRAs yet. Find some on the LoRAs page.</div>`));
    list.forEach(l => {
      const f = loraFamily(l.base) || modelFamily(l.file), ok = !fam || !f || f === fam;
      const c = h(`<div class="lcard ${ok ? "" : "dim"}">${l.preview ? `<img src="/manny/api/civitai/img?u=${encodeURIComponent(l.preview)}" loading="lazy">` : `<div class="lph">LoRA</div>`}<div class="lb2"><b>${esc(l.name)}</b><span>${esc(l.base || "unknown model")}</span></div></div>`);
      c.onclick = () => {
        const picks = S.loras[mkey(app)] || (S.loras[mkey(app)] = []);
        if (!picks.some(p => p.name === l.file)) picks.push({ name: l.file, label: l.name, strength: 0.8, trained: l.trained, family: f });
        saveLoras(); close(); done();
      };
      g.append(c);
    });
  };
  $(".search", d).oninput = draw; draw();
}
function loraPayload(app, vals) {
  const L = layout(app), fam = L.model ? modelFamily(vals[L.model.key]) : "";
  const picks = (S.loras[mkey(app)] || []).filter(p => !(p.family && fam && p.family !== fam) && p.strength > 0);
  if (picks.length && L.prompt) {
    const words = [...new Set(picks.flatMap(p => p.trained || []))].filter(w => w && !(vals[L.prompt.key] || "").includes(w));
    if (words.length) vals[L.prompt.key] = words.join(", ") + ", " + (vals[L.prompt.key] || "");
  }
  return picks.map(p => ({ name: p.name, strength: p.strength }));
}

async function pageLoras(main) {
  const p = h(`<div class="page"><div class="eyebrow">Library</div><h1>Models &amp; LoRAs</h1>
    <div class="chips" id="ltab"><div class="chip on" data-t="mine">Installed</div><div class="chip" data-t="civ">Find on Civitai</div></div>
    <div id="lbody" style="margin-top:18px"></div></div>`);
  main.append(p);
  const body = $("#lbody", p);
  const tab = t => { $$("#ltab .chip", p).forEach(c => c.classList.toggle("on", c.dataset.t === t)); store.set("ltab", t); (t === "mine" ? mine : civ)(); };
  $$("#ltab .chip", p).forEach(c => c.onclick = () => tab(c.dataset.t));
  async function mine() {
    body.innerHTML = `<div id="upd"></div><h3 class="subh">LoRAs</h3><div class="lgrid" id="lg"></div><h3 class="subh">Other downloads</h3><div class="lgrid" id="og"></div><p class="hint">Remove models in <a href="#/settings">Settings</a>.</p>`;
    api("/manny/api/civitai/status").then(st => st.has_key && api("/manny/api/civitai/updates" + PQ())).then(ups => {
      const u = $("#upd", body); if (!u || !ups || !ups.length) return;
      const box = h(`<div class="panel updbox"><b>${ups.length} update${ups.length > 1 ? "s" : ""} on Civitai</b></div>`);
      ups.forEach(x => {
        const r = h(`<div class="mrow"><div class="mn"><b>${esc(x.name)}</b><span>You have ${esc(x.installed || "an older version")} · latest is ${esc(x.latest.name)}${x.latest.date ? " (" + esc(x.latest.date) + ")" : ""}</span></div><span></span><span class="ms"></span>${x.paid
          ? `<a class="btn sm ghost" target="_blank" rel="noopener" href="https://civitai.com/models/${encodeURIComponent(x.model)}?modelVersionId=${encodeURIComponent(x.latest.id)}" title="This version is paid or early access on Civitai">${x.paid === "paid" ? "Paid on Civitai" : "Free from " + esc(x.paid)}</a>`
          : `<button class="btn sm">Get latest</button>`}</div>`);
        const bt = $("button", r);
        if (bt) bt.onclick = async () => {
          bt.disabled = true; bt.textContent = "Starting…";
          try { const j = await post("/manny/api/civitai/download", { version: x.latest.id, type: x.type, private: S.incog }); await pollJob(j.id, s2 => bt.textContent = s2); bt.textContent = "Installed"; loraCache = null; ckptCache = null; toast(`${x.name} updated. Remove the old version in Settings if you like.`); }
          catch (e) {
            const msg = /403|paid|early access/i.test(e.message) ? "Not free yet on Civitai" : e.message;
            toast(msg, true); bt.disabled = false; bt.textContent = "Try again";
            const ms = $(".ms", r); if (ms) ms.textContent = msg;
          }
        };
        box.append(r);
      });
      u.append(box);
    }).catch(() => { });
    api("/manny/api/models" + PQ()).then(r => {
      const og = $("#og", body); if (!og) return;
      const list = r.items.filter(m => m.folder !== "loras" && m.civitai);
      if (!list.length) og.append(h(`<div class="empty">Checkpoints, VAEs, embeddings and upscalers you download from Civitai show up here.</div>`));
      list.forEach(m => og.append(h(`<div class="lcard">${m.preview ? `<img src="/manny/api/civitai/img?u=${encodeURIComponent(m.preview)}" loading="lazy">` : `<div class="lph">${esc(m.folder.toUpperCase())}</div>`}<div class="lb2"><b>${esc(m.title || m.name)}</b><span>${esc(m.folder)} · ${esc(m.base)} · ${fmtSize(m.size)}</span></div></div>`)));
    }).catch(() => { });
    const all = await loadLoras(true), g = $("#lg", body);
    if (!all.length) g.append(h(`<div class="empty">No LoRAs yet. Open “Find on Civitai”.</div>`));
    all.forEach(l => {
      const c = h(`<div class="lcard">${l.preview ? `<img src="/manny/api/civitai/img?u=${encodeURIComponent(l.preview)}" loading="lazy">` : `<div class="lph">LoRA</div>`}<div class="lb2"><b>${esc(l.name)}</b><span>${esc(l.base || "")}${l.version ? " · " + esc(l.version) : ""}</span>${l.trained.length ? `<span class="tw">${esc(l.trained.slice(0, 4).join(", "))}</span>` : ""}<button class="ghost" data-use>Use in Create</button></div></div>`);
      $("[data-use]", c).onclick = () => {
        const f = loraFamily(l.base) || modelFamily(l.file);
        const k = S.apps.findIndex(a => !isTool(a) && (f === "wan22" || f === "ltx" || f === "h3" ? a.kind === "video" : a.kind === "image"));
        if (k < 0) return;
        const app = S.apps[k], picks = S.loras[mkey(app)] || (S.loras[mkey(app)] = []);
        if (!picks.some(x => x.name === l.file)) picks.push({ name: l.file, label: l.name, strength: 0.8, trained: l.trained, family: f });
        saveLoras(); openApp(k); toast(`${l.name} added. Pick a ${l.base || "matching"} model.`);
      };
      g.append(c);
    });
  }
  async function civ() {
    const st = await api("/manny/api/civitai/status").catch(() => ({}));
    if (!st.has_key) {
      body.innerHTML = "";
      const k = h(`<div class="panel keybox"><h3>Connect your Civitai account</h3><p class="muted">Studio searches and downloads with your own key. It stays on this PC and is never shown again.</p>
        <ol class="muted"><li>On civitai.com open <b>Account settings → API Keys</b> and create a key.</li><li>Paste it here.</li></ol>
        <div class="row"><input type="password" id="ck" placeholder="Civitai API key" autocomplete="off"><button class="btn primary" id="cks">Save key</button></div></div>`);
      body.append(k);
      $("#cks", k).onclick = async () => { try { await post("/manny/api/civitai/key", { key: $("#ck", k).value.trim() }); toast("Key saved"); civ(); } catch (e) { toast(e.message, true); } };
      return;
    }
    const MINE = "Illustrious|Pony|SDXL 1.0|NoobAI|SD 1.5|Qwen|ZImageTurbo|Wan Video 2.2 I2V-A14B|Wan Video 2.2 T2V-A14B|LTXV|Anima";
    const bases = [[MINE, "Works with my models"], ["", "Every base model"], ["Illustrious", "Illustrious"], ["Pony", "Pony"], ["SDXL 1.0", "SDXL"], ["Qwen", "Qwen"], ["ZImageTurbo", "Z-Image"], ["Wan Video 2.2 I2V-A14B", "Wan 2.2 I2V"], ["Wan Video 2.2 T2V-A14B", "Wan 2.2 T2V"], ["LTXV", "LTX"], ["Flux.2 Klein 9B", "FLUX Klein"]];
    const ctypes = [["LORA", "LoRAs"], ["Checkpoint", "Checkpoints"], ["TextualInversion", "Embeddings"], ["VAE", "VAEs"], ["Upscaler", "Upscalers"], ["Controlnet", "ControlNets"], ["LoCon", "LyCORIS"]];
    body.innerHTML = `<div class="filters"><select id="ct">${ctypes.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select><input type="text" class="search" id="cq" placeholder="Search Civitai…"><select id="cb">${bases.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select>
      <select id="cs"><option>Newest</option><option>Most Downloaded</option><option>Highest Rated</option></select><select id="cp"><option value="">Any time</option><option value="Week">This week</option><option value="Month">This month</option><option value="Year">This year</option></select><button class="ghost" id="cgo">Search</button><a href="javascript:void 0" id="ckx" class="hint">Change key</a></div>
      <div class="lgrid" id="cg"></div><div class="row" style="justify-content:center;margin-top:16px"><button class="ghost hidden" id="cmore">More</button></div>`;
    $("#cb", body).value = store.get("cbase2", MINE); $("#ct", body).value = store.get("ctype", "LORA"); $("#cs", body).value = store.get("csort", "Newest"); $("#cp", body).value = store.get("cper", "");
    let next = null;
    const run = async more => {
      const g = $("#cg", body); if (!more) { g.innerHTML = `<div class="empty">Searching…</div>`; next = null; }
      store.set("cbase2", $("#cb", body).value); store.set("ctype", $("#ct", body).value); store.set("csort", $("#cs", body).value); store.set("cper", $("#cp", body).value);
      const qs = new URLSearchParams({ q: $("#cq", body).value.trim(), base: $("#cb", body).value, sort: $("#cs", body).value, type: $("#ct", body).value, period: $("#cp", body).value });
      if (S.incog) qs.set("private", "1");
      if (more && next) qs.set("cursor", next);
      try {
        const r = await api("/manny/api/civitai/search?" + qs);
        if (!more) g.innerHTML = "";
        next = r.next; $("#cmore", body).classList.toggle("hidden", !next);
        if (!r.items.length && !more) g.append(h(`<div class="empty">Nothing found.</div>`));
        r.items.forEach(it => {
          const v = it.versions[0] || { images: [] }, im = v.images[0];
          const c = h(`<div class="lcard">${im ? (im.type === "video" ? `<video src="/manny/api/civitai/img?u=${encodeURIComponent(im.url)}" muted loop playsinline preload="none"></video>` : `<img src="/manny/api/civitai/img?u=${encodeURIComponent(im.url)}" loading="lazy">`) : `<div class="lph">LoRA</div>`}
            <div class="lb2"><b>${esc(it.name)}</b><span>${esc(v.name || "")} <span class="latest">latest</span>${v.date ? " · " + esc(v.date) : ""}</span><span>${esc(v.base || "")} · ⤓ ${(it.downloads || 0).toLocaleString()}</span></div></div>`);
          const vd = $("video", c); if (vd) { c.onmouseenter = () => vd.play().catch(() => { }); c.onmouseleave = () => vd.pause(); }
          c.onclick = () => civDetail(it);
          g.append(c);
        });
      } catch (e) { g.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
    };
    $("#cgo", body).onclick = () => run(false);
    $("#cq", body).onkeydown = e => { if (e.key === "Enter") run(false); };
    $("#cb", body).onchange = $("#cs", body).onchange = $("#ct", body).onchange = $("#cp", body).onchange = () => run(false);
    $("#cmore", body).onclick = () => run(true);
    $("#ckx", body).onclick = async () => { if (!confirm("Remove the saved Civitai key?")) return; await post("/manny/api/civitai/key", { key: "" }); civ(); };
    run(false);
  }
  tab(store.get("ltab", "mine"));
}
function civDetail(it) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  let vi = 0;
  const d = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>${esc(it.name)}</h2><button class="icon" data-x>✕</button></div>
    <div class="hint">by ${esc(it.creator || "?")} · ⤓ ${(it.downloads || 0).toLocaleString()} · 👍 ${(it.likes || 0).toLocaleString()}</div>
    <select id="cv">${it.versions.map((v, k) => `<option value="${k}">${k === 0 ? "★ Latest: " : ""}${esc(v.name)} · ${esc(v.base || "")}${v.date ? " · " + esc(v.date) : ""}</option>`).join("")}</select>
    <div class="cimgs"></div><div class="cinfo"></div><select id="cf"></select><div class="cdesc muted"></div>
    <div class="foot"><span class="hint" id="cst" style="margin-right:auto"></span><button class="ghost" data-x>Close</button><button class="btn primary" id="cdl">Download</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  $(".cdesc", d).textContent = it.desc || "";
  const draw = () => {
    const v = it.versions[vi];
    $(".cimgs", d).innerHTML = v.images.map(i => i.type === "video" ? `<video src="/manny/api/civitai/img?u=${encodeURIComponent(i.url)}" muted loop autoplay playsinline></video>` : `<img src="/manny/api/civitai/img?u=${encodeURIComponent(i.url)}" loading="lazy">`).join("") || `<div class="hint">No previews${S.incog ? "" : " (mature previews show only in private mode)"}.</div>`;
    const fsel = $("#cf", d); fsel.innerHTML = v.files.map((x, k) => `<option value="${k}" ${x.primary ? "selected" : ""}>${esc(x.name)} · ${fmtSize((x.sizeKB || 0) * 1024)}</option>`).join(""); fsel.classList.toggle("hidden", v.files.length < 2);
    const f = v.files[+fsel.value] || v.files.find(x => x.primary) || v.files[0], fam = loraFamily(v.base), typ = it.type || "LORA";
    const isSD = /sd 1|sd1|sdxl|pony|illustrious|noobai/i.test(v.base || "");
    const works = typ === "Checkpoint" ? (isSD ? "Create › Image (under Your checkpoints)" : "the node graph (downloaded to diffusion_models)")
      : /LORA|LoCon|DoRA/.test(typ) ? { sdxl: "Anime Desire and your SDXL checkpoints", qwen: "Qwen Image 2.1", zimage: "Z-Image Turbo", anima: "Anima", wan22: "Video › Wan 2.2", ltx: "Video › LTX-2.5", h3: "Video › MiniMax H3", krea: "Krea 2", flux3: "FLUX 3 Klein", moss: "MOSS" }[fam]
      : "the node graph";
    $(".cinfo", d).innerHTML = `<div class="kv"><span>Made for</span><span>${esc(v.base || "?")}</span><span>Works with</span><span>${works ? esc(works) : "<span class='warn'>none of your models</span>"}</span>
      <span>Trigger words</span><span>${v.trained.length ? esc(v.trained.join(", ")) : "none"}</span><span>File</span><span>${f ? esc(f.name) + " · " + fmtSize((f.sizeKB || 0) * 1024) : "no .safetensors file"}</span></div>`;
    $("#cdl", d).disabled = !f;
  };
  $("#cv", d).onchange = e => { vi = +e.target.value; draw(); };
  $("#cf", d).onchange = () => draw();
  draw();
  $("#cdl", d).onclick = async () => {
    const v = it.versions[vi], f = v.files[+$("#cf", d).value] || v.files.find(x => x.primary) || v.files[0], btn = $("#cdl", d);
    btn.disabled = true;
    try {
      const r = await post("/manny/api/civitai/download", { version: v.id, file: f.name, type: it.type, private: S.incog });
      await pollJob(r.id, s => { const e = $("#cst", d); if (e) e.textContent = s; });
      loraCache = null; ckptCache = null; toast(`${it.name} installed`); const e = $("#cst", d); if (e) e.textContent = /LORA|LoCon|DoRA/.test(it.type || "LORA") ? "Installed. Add it from Create › LoRAs." : "Installed. It's in your model lists now.";
    } catch (e) { toast("Download failed: " + e.message, true); btn.disabled = false; }
  };
}

/* ------------------------------------------------------------ AI review of a take */
function reviewModal(b, s, t, onPrompt) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>AI review</h2><button class="icon" data-x>✕</button></div><div class="rbody"><div class="hint">Looking at 8 frames with the local Qwen3-VL (nothing leaves the PC)… <span id="rst"></span></div><div class="bar"><div class="ind"></div></div></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $("[data-x]", d).onclick = close;
  const show = rv => {
    const r = rv.review;
    const body = $(".rbody", d); body.innerHTML = "";
    if (!r) { body.append(h(`<pre class="rtext"></pre>`)); $(".rtext", body).textContent = rv.text; return; }
    const sc = (k, l) => `<div class="rsc"><b>${esc(String(r[k] ?? "–"))}</b><span>${l}</span></div>`;
    body.append(h(`<div><div class="rscores">${sc("score", "Overall")}${sc("prompt_match", "Matches prompt")}${sc("motion", "Motion")}${sc("anatomy", "Faces & hands")}${sc("artifacts", "Clean picture")}</div>
      <div class="rverdict ${r.verdict === "keep" ? "ok" : "bad"}">${r.verdict === "keep" ? "✓ Keep this take" : "↻ Worth another take"}</div></div>`));
    const list = (title, arr) => { if (!arr || !arr.length) return; const el = h(`<div><label class="lbl">${title}</label><ul class="rl2"></ul></div>`); arr.forEach(x => { const li = document.createElement("li"); li.textContent = x; $("ul", el).append(li); }); body.append(el); };
    list("Good", r.good); list("Problems", r.problems);
    if (r.better_prompt) {
      const bp = h(`<div><label class="lbl">Suggested prompt</label><div class="promptbox"><div class="ptext"></div></div><button class="btn primary" style="margin-top:10px">Use this prompt for the next take</button></div>`);
      $(".ptext", bp).textContent = r.better_prompt;
      $("button", bp).onclick = () => { onPrompt(r.better_prompt); close(); };
      body.append(bp);
    }
  };
  if (t.review) return show(t.review);
  (async () => {
    try {
      const r = await post("/manny/api/ai/review", { ...t.output, private: !!b.private, prompt: [s.prompt, s.camera].filter(Boolean).join(". ") });
      const rv = await pollJob(r.id, st => { const e = $("#rst", d); if (e) e.textContent = st; });
      t.review = rv; saveBoard(b, true); if (document.body.contains(d)) show(rv);
    } catch (e) { const bd = $(".rbody", d); if (bd) bd.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  })();
}

/* ------------------------------------------------------------ film settings: titles, crossfades, music */
function filmDialog(b, clipOf, after) {
  const e = b.edit || (b.edit = { crossfade: 0.5, title: { text: b.name, sub: "", secs: 3 }, end: { text: "", sub: "", secs: 3 }, music: "", music_vol: 0.35, keep_audio: true });
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>Make the film</h2><button class="icon" data-x>✕</button></div>
    <div><label class="lbl">Between shots</label><div class="chips" id="fx"><div class="chip" data-v="0">Hard cut</div><div class="chip" data-v="0.5">Crossfade 0.5 s</div><div class="chip" data-v="1">Crossfade 1 s</div></div></div>
    <div class="two"><div><label class="lbl">Opening title</label><input type="text" id="tt" placeholder="Leave empty for none"></div><div><label class="lbl">Under the title</label><input type="text" id="ts" placeholder="A film by…"></div></div>
    <div class="two"><div><label class="lbl">End card</label><input type="text" id="et" placeholder="The End (empty for none)"></div><div><label class="lbl">Under it</label><input type="text" id="es" placeholder="Credits, music by…"></div></div>
    <div><label class="lbl">Music bed</label><div class="ref audio ${e.music ? "on" : ""}" id="fm" style="aspect-ratio:auto;min-height:64px"><span class="${e.music ? "wave" : "plus"}">${e.music ? "♪" : "+"}</span><span class="rl">${e.music ? esc(e.music.split("/").pop()) : "Drop a song (optional)"}</span><button class="x" title="Remove">✕</button><input type="file" hidden accept="audio/*"></div>
      <div class="row" style="margin-top:8px;gap:14px"><label class="hint">Music volume <input type="range" id="mv" min="0.05" max="1" step="0.05"></label><label class="tog"><input type="checkbox" id="ka"><span>Keep the shots' own sound</span></label></div></div>
    <p class="hint">Trims and captions are set on each shot (✂ under its prompt). Shots use their finished version when they have one.</p>
    <div class="foot"><span class="hint" id="fst" style="margin-right:auto"></span><button class="ghost" data-x>Cancel</button><button class="btn primary" id="fgo">🎬 Make the film</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  const chip = () => $$("#fx .chip", d).forEach(c => { c.classList.toggle("on", +c.dataset.v === +e.crossfade); c.onclick = () => { e.crossfade = +c.dataset.v; chip(); }; });
  chip();
  $("#tt", d).value = e.title.text || ""; $("#ts", d).value = e.title.sub || ""; $("#et", d).value = e.end.text || ""; $("#es", d).value = e.end.sub || "";
  $("#mv", d).value = e.music_vol; $("#ka", d).checked = e.keep_audio !== false;
  const fm = $("#fm", d), fi = $("input", fm);
  fm.onclick = ev => { if (ev.target.closest(".x")) { e.music = ""; fm.classList.remove("on"); $(".rl", fm).textContent = "Drop a song (optional)"; return; } fi.click(); };
  const take = async f => { e.music = await upload(f); fm.classList.add("on"); $(".rl", fm).textContent = f.name; };
  fi.onchange = () => fi.files[0] && take(fi.files[0]);
  fm.ondragover = ev => ev.preventDefault(); fm.ondrop = ev => { ev.preventDefault(); ev.dataTransfer.files[0] && take(ev.dataTransfer.files[0]); };
  $("#fgo", d).onclick = async () => {
    e.title = { text: $("#tt", d).value.trim(), sub: $("#ts", d).value.trim(), secs: 3 }; e.end = { text: $("#et", d).value.trim(), sub: $("#es", d).value.trim(), secs: 3 };
    e.music_vol = +$("#mv", d).value; e.keep_audio = $("#ka", d).checked; saveBoard(b, true);
    const shots = b.shots.map(s => { const c = clipOf(s); return c ? { ...c, in: s.trimIn || 0, out: s.trimOut || 0, caption: s.caption || "" } : null; }).filter(Boolean);
    if (!shots.length) return toast("Render some shots first", true);
    if (shots.length < b.shots.length && !confirm(`${b.shots.length - shots.length} shot(s) aren't rendered yet. Make the film from the ${shots.length} that are?`)) return;
    const btn = $("#fgo", d); btn.disabled = true;
    try {
      const r = await post("/manny/api/edit", { ...e, shots, name: b.name, private: !!b.private });
      b.film = await pollJob(r.id, s => { const el = $("#fst", d); if (el) el.textContent = s; });
      saveBoard(b, true); close(); toast("Film ready"); S.gallery = []; after();
    } catch (err) { toast("Couldn't make the film: " + err.message.slice(0, 300), true); btn.disabled = false; }
  };
}

/* ------------------------------------------------------------ REEL: pictures and clips cut to a song */
S.reel = { picks: [], song: "", aspect: "9:16", length: 30, cut: 1.2 };
/* ------------------------------------------------------------ PROJECTS: idea → script → cast → keyframes → storyboard → sound → film */
const P_CAMS = { static: "static locked-off camera", "push in": "slow dolly push-in toward the subject", "pull back": "slow dolly pull-back revealing the scene", "pan left": "smooth pan left", "pan right": "smooth pan right", tracking: "tracking shot following the subject", orbit: "camera orbits around the subject", "crane up": "crane shot rising up", "crane down": "crane shot descending", handheld: "handheld camera with natural shake", "close-up": "tight close-up", "low angle": "low-angle hero shot", drone: "aerial drone flyover", "slow motion": "slow motion" };
const P_TONES = ["Cinematic drama", "Dark thriller", "Epic fantasy", "Sci-fi", "Anime", "Music video", "Comedy", "Horror", "Documentary", "Commercial"];
let projSave = null;
const saveProject = (pr, now) => { clearTimeout(projSave); const go = () => post("/manny/api/projects", pr).then(r => { if (!pr.id) pr.id = r.id; }); if (now || !pr.id) return go(); projSave = setTimeout(go, 700); };
async function pollJob(id, onStage) {
  for (;;) {
    await new Promise(r => setTimeout(r, 3000));
    const j = await api("/manny/api/job/" + id);
    if (onStage) onStage(j.stage || "");
    if (j.status === "done") return j.result;
    if (j.status !== "running") throw new Error(j.error || j.stage || "failed");
  }
}
// the shot text with each character's look written in, so every shot is self-contained
function shotText(pr, s, withCamera) {
  let t = s.action || "";
  const cast = (pr.cast || []).map(id => S.chars.find(c => c.id === id)).filter(Boolean);
  cast.forEach(c => { const first = c.name.split(" ")[0]; const look = [c.trigger, c.look].filter(Boolean).join(", "); if (look && new RegExp("\\b" + first + "\\b", "i").test(t) && !t.includes(look)) t = t.replace(new RegExp("\\b(" + c.name + "|" + first + ")\\b", "i"), `$1 (${look})`); });
  if (withCamera && s.camera && P_CAMS[s.camera]) t = t.replace(/[.\s]*$/, "") + ". " + P_CAMS[s.camera] + ".";
  return t;
}
async function pageProjects(main, arg) {
  await loadChars();
  let list = []; try { list = await api("/manny/api/projects" + PQ()); } catch { }
  const pr = arg ? list.find(x => x.id === arg) : null;
  if (pr) return projectView(main, pr);
  const p = h(`<div class="page"><div class="eyebrow">Projects</div><h1>Projects</h1>
    <p class="hint" style="max-width:720px">A project takes a film from an idea to the finished cut: the AI writes the script and shot list, you cast your characters, Studio makes a keyframe per shot, sends them to the Storyboard for takes, adds music and voices, and cuts the film.</p>
    <div class="row" style="margin:14px 0"><button class="btn primary" id="np">＋ New project</button></div><div class="pgrid"></div></div>`);
  main.append(p);
  const g = $(".pgrid", p);
  if (!list.length) g.append(h(`<div class="empty">No projects yet.</div>`));
  list.forEach(x => {
    const kf = (x.shots || []).find(s => s.keyframe);
    const c = h(`<a class="pcard" href="#/projects/${x.id}"><div class="pthumb" style="${kf ? `background-image:url('${inputUrl(kf.keyframe)}')` : ""}">${kf ? "" : "▤"}</div><div class="pb"><b>${esc(x.title || x.name || "Untitled")}</b><span>${(x.shots || []).length} shots · ${esc(x.stage || "Idea")}</span></div></a>`);
    g.append(c);
  });
  $("#np", p).onclick = async () => { const n = { name: "New film", idea: "", tone: "Cinematic drama", nshots: 6, seconds: 5, cast: [], shots: [], private: !!S.incog, stage: "Idea" }; await saveProject(n, true); location.hash = "#/projects/" + n.id; };
}
function projectView(main, pr) {
  const p = h(`<div class="page proj"><div class="eyebrow"><a href="#/projects">Projects</a> ›</div>
    <input class="ptitle" value="${esc(pr.title || pr.name || "")}" placeholder="Film title">
    <div class="psteps"></div><div class="pbody"></div></div>`);
  main.append(p);
  $(".ptitle", p).oninput = e => { pr.title = e.target.value; saveProject(pr); };
  const steps = [["idea", "1 · Idea"], ["script", "2 · Script"], ["cast", "3 · Cast"], ["frames", "4 · Keyframes"], ["board", "5 · Storyboard"], ["sound", "6 · Sound & film"]];
  let cur = pr.step || (pr.shots && pr.shots.length ? "script" : "idea");
  const go = k => { cur = k; pr.step = k; saveProject(pr); draw(); };
  const draw = () => {
    $(".psteps", p).innerHTML = ""; steps.forEach(([k, l]) => { const c = h(`<div class="chip ${k === cur ? "on" : ""}">${l}</div>`); c.onclick = () => go(k); $(".psteps", p).append(c); });
    const b = $(".pbody", p); b.innerHTML = "";
    ({ idea: stepIdea, script: stepScript, cast: stepCast, frames: stepFrames, board: stepBoard, sound: stepSound })[cur](b, pr, go, draw);
  };
  draw();
}
function stepIdea(b, pr, go) {
  const d = h(`<div class="panel pform">
    <label class="lbl">What's the film about?</label><textarea id="pi" rows="5" placeholder="e.g. A lighthouse keeper finds a girl washed ashore after a storm; she can't speak, and the sea keeps calling her back."></textarea>
    <label class="lbl">Tone</label><div class="chips" id="pt"></div>
    <div class="grid4"><div><label class="lbl">Shots</label><select id="pn">${[3, 4, 5, 6, 8, 10, 12].map(n => `<option>${n}</option>`).join("")}</select></div>
      <div><label class="lbl">Seconds per shot</label><select id="ps">${[3, 5, 8, 10].map(n => `<option>${n}</option>`).join("")}</select></div></div>
    <div class="row"><button class="btn primary" id="pw">✨ Write the script</button><span class="hint" id="pst"></span></div>
    <p class="hint">The local AI (on this PC) writes a title, a logline, a music style and the shot list. You can edit everything after. Pick your characters in Cast first if you want them written in.</p></div>`);
  b.append(d);
  $("#pi", d).value = pr.idea || ""; $("#pn", d).value = pr.nshots || 6; $("#ps", d).value = pr.seconds || 5;
  P_TONES.forEach(t => { const c = h(`<div class="chip ${pr.tone === t ? "on" : ""}">${t}</div>`); c.onclick = () => { pr.tone = t; saveProject(pr); $$("#pt .chip", d).forEach(x => x.classList.toggle("on", x === c)); }; $("#pt", d).append(c); });
  $("#pi", d).oninput = e => { pr.idea = e.target.value; saveProject(pr); };
  $("#pn", d).onchange = e => { pr.nshots = +e.target.value; saveProject(pr); };
  $("#ps", d).onchange = e => { pr.seconds = +e.target.value; saveProject(pr); };
  $("#pw", d).onclick = async () => {
    if ((pr.idea || "").trim().length < 8) return toast("Describe the idea first", true);
    if ((pr.shots || []).length && !confirm("Replace the current shot list with a new one?")) return;
    const btn = $("#pw", d); btn.disabled = true;
    try {
      const cast = (pr.cast || []).map(id => S.chars.find(c => c.id === id)).filter(Boolean).map(c => ({ name: c.name, look: [c.trigger, c.look].filter(Boolean).join(", ") }));
      const r = await post("/manny/api/projects/script", { idea: pr.idea, tone: pr.tone, shots: pr.nshots || 6, seconds: pr.seconds || 5, cast, private: !!pr.private });
      const res = await pollJob(r.id, st => { const e = $("#pst", d); if (e) e.textContent = st + "…"; });
      Object.assign(pr, { title: pr.title && pr.title !== "New film" ? pr.title : res.title, logline: res.logline, music: res.music, shots: res.shots, stage: "Script" });
      await saveProject(pr, true); toast("Script written"); go("script");
    } catch (e) { toast("The AI couldn't write it: " + e.message.slice(0, 200), true); btn.disabled = false; const s = $("#pst", d); if (s) s.textContent = ""; }
  };
}
function stepScript(b, pr, go, draw) {
  const d = h(`<div><div class="panel"><label class="lbl">Logline</label><textarea rows="2" id="pl"></textarea></div><div class="pshots"></div>
    <div class="row" style="margin-top:10px"><button class="ghost" id="padd">＋ Add a shot</button><button class="btn primary" id="pnext">Next: cast →</button></div></div>`);
  b.append(d);
  $("#pl", d).value = pr.logline || ""; $("#pl", d).oninput = e => { pr.logline = e.target.value; saveProject(pr); };
  const box = $(".pshots", d);
  if (!(pr.shots || []).length) box.append(h(`<div class="empty">No shots yet. Write the script from the idea, or add shots by hand.</div>`));
  (pr.shots || []).forEach((s, k) => {
    const el = h(`<div class="pshot panel"><div class="row" style="justify-content:space-between"><input type="text" class="pst" value="${esc(s.title || "")}"><div class="row"><button class="icon" data-u title="Move up">↑</button><button class="icon" data-dn title="Move down">↓</button><button class="icon" data-x title="Remove">✕</button></div></div>
      <textarea rows="3" class="pa" placeholder="What we see">${esc(s.action || "")}</textarea>
      <div class="row wrap"><select class="pc">${Object.keys(P_CAMS).map(c => `<option ${c === s.camera ? "selected" : ""}>${c}</option>`).join("")}</select>
      <select class="psec">${[3, 5, 8, 10].map(n => `<option ${n === +s.seconds ? "selected" : ""}>${n}</option>`).join("")}</select><span class="hint">s</span>
      <input type="text" class="pd" placeholder="Dialogue (optional)" value="${esc(s.dialogue || "")}"></div><div class="hint">Shot ${k + 1}${(s.characters || []).length ? " · " + esc(s.characters.join(", ")) : ""}</div></div>`);
    $(".pst", el).oninput = e => { s.title = e.target.value; saveProject(pr); };
    $(".pa", el).oninput = e => { s.action = e.target.value; saveProject(pr); };
    $(".pc", el).onchange = e => { s.camera = e.target.value; saveProject(pr); };
    $(".psec", el).onchange = e => { s.seconds = +e.target.value; saveProject(pr); };
    $(".pd", el).oninput = e => { s.dialogue = e.target.value; saveProject(pr); };
    $("[data-x]", el).onclick = () => { pr.shots.splice(k, 1); saveProject(pr); draw(); };
    $("[data-u]", el).onclick = () => { if (k) { pr.shots.splice(k - 1, 0, pr.shots.splice(k, 1)[0]); saveProject(pr); draw(); } };
    $("[data-dn]", el).onclick = () => { if (k < pr.shots.length - 1) { pr.shots.splice(k + 1, 0, pr.shots.splice(k, 1)[0]); saveProject(pr); draw(); } };
    box.append(el);
  });
  $("#padd", d).onclick = () => { (pr.shots = pr.shots || []).push({ id: Math.random().toString(36).slice(2, 10), title: "New shot", action: "", camera: "static", seconds: pr.seconds || 5, characters: [], dialogue: "" }); saveProject(pr); draw(); };
  $("#pnext", d).onclick = () => go("cast");
}
function stepCast(b, pr, go) {
  const named = [...new Set((pr.shots || []).flatMap(s => s.characters || []))];
  const d = h(`<div class="panel"><p class="hint" style="margin-top:0">Pick the characters in this film. Their face is used for the keyframes and their look is written into every shot they're in.${named.length ? ` The script names: <b>${esc(named.join(", "))}</b>.` : ""}</p><div class="pcast"></div>
    <div class="row" style="margin-top:12px"><a class="ghost" href="#/characters">＋ New character</a><button class="btn primary" id="pnext">Next: keyframes →</button></div></div>`);
  b.append(d);
  pr.cast = pr.cast || [];
  if (!S.chars.length) $(".pcast", d).append(h(`<div class="empty">No characters yet. Make some on the Characters page.</div>`));
  S.chars.forEach(c => {
    const on = pr.cast.includes(c.id);
    const el = h(`<div class="cpick ${on ? "on" : ""}">${c.face ? `<img src="${inputUrl(c.face)}">` : `<span class="av">${esc(c.name[0] || "?")}</span>`}${esc(c.name)}</div>`);
    el.onclick = () => { const i = pr.cast.indexOf(c.id); if (i < 0) pr.cast.push(c.id); else pr.cast.splice(i, 1); saveProject(pr); el.classList.toggle("on"); };
    $(".pcast", d).append(el);
  });
  $("#pnext", d).onclick = () => go("frames");
}
function stepFrames(b, pr, go, draw) {
  const k = findApp("image"), app = k >= 0 ? S.apps[k] : null;
  const d = h(`<div><div class="panel"><p class="hint" style="margin-top:0">One still per shot, made with the Image app's current model and settings${app ? ` (<b>${esc(String(valuesFor(app)[layout(app).model?.key] || "").split("(")[0])}</b> — change it in Create)` : ""}. Shots with one character use that character's face as the reference. They become each shot's first frame.</p>
    <div class="row"><button class="btn primary" id="pk">▣ Make keyframes for shots without one</button><button class="ghost" id="pka">Redo all</button><span class="hint" id="pks"></span></div></div><div class="pframes"></div>
    <div class="row" style="margin-top:10px"><button class="btn primary" id="pnext">Next: storyboard →</button></div></div>`);
  b.append(d);
  (pr.shots || []).forEach((s, i) => {
    const el = h(`<div class="pframe"><div class="pfi">${s.keyframe ? `<img src="${inputUrl(s.keyframe)}">` : `<span>Shot ${i + 1}</span>`}</div><b>${esc(s.title || "")}</b><button class="ghost sm">↻ Redo</button></div>`);
    $("button", el).onclick = () => run([s]);
    $(".pframes", d).append(el);
  });
  const run = async shots => {
    if (!app) return toast("The Image app is missing", true);
    if (!shots.length) return toast("Every shot has a keyframe. Use Redo to make new ones.");
    const L = layout(app), v = buildValues(app);
    const ref = L.refs.find(r => r.media.kind === "image");
    const items = shots.map(s => {
      const inShot = (pr.cast || []).map(id => S.chars.find(c => c.id === id)).filter(c => c && c.face && new RegExp("\\b" + c.name.split(" ")[0] + "\\b", "i").test((s.characters || []).join(" ") + " " + s.action));
      return { id: s.id, prompt: shotText(pr, s, false) + (pr.tone ? `. ${pr.tone} style, film still` : ""), ref: inShot.length === 1 && ref ? inShot[0].face : "" };
    });
    try {
      const r = await post(`/manny/api/projects/${pr.id}/keyframes`, { private: !!pr.private, values: v, shots: items, prompt_key: L.prompt?.key, ref_key: ref?.media.key, ref_toggle_key: ref?.toggle?.key, seed_keys: app.inputs.filter(i => i.kind === "seed").map(i => i.key), target: runTarget() });
      toast(`Making ${items.length} keyframe${items.length > 1 ? "s" : ""}. They render one after another; you can leave this page.`);
      await pollJob(r.id, st => { const e = $("#pks", d); if (e) e.textContent = st; });
      const fresh = (await api("/manny/api/projects" + PQ())).find(x => x.id === pr.id); if (fresh) Object.assign(pr, { shots: fresh.shots });
      pr.stage = "Keyframes"; saveProject(pr); toast("Keyframes ready"); if (document.body.contains(d)) draw();
    } catch (e) { toast("Keyframes stopped: " + e.message.slice(0, 200), true); }
  };
  $("#pk", d).onclick = () => run((pr.shots || []).filter(s => !s.keyframe));
  $("#pka", d).onclick = () => { if (confirm("Make new keyframes for every shot?")) run(pr.shots || []); };
  $("#pnext", d).onclick = () => go("board");
}
async function stepBoard(b, pr, go) {
  await loadBoards();
  const bd = S.boards.find(x => x.id === pr.board);
  const d = h(`<div class="panel"><p class="hint" style="margin-top:0">Sends the shots to a Storyboard board: each shot's prompt (with the camera move and character looks) and its keyframe as the first frame. There you draft takes, pick the best, and finish them.</p>
    <p>${bd ? `Board: <b>${esc(bd.name)}</b> · ${bd.shots.length} shots` : "No board yet."}</p>
    <div class="row"><button class="btn primary" id="pbs">${bd ? "↻ Update the board" : "▤ Create the storyboard"}</button>${bd ? `<button class="btn" id="pbo">Open the board →</button>` : ""}</div>
    <p class="hint">Updating keeps the takes you already made; it adds new shots and refreshes prompts and first frames of shots that have no takes yet.</p></div>`);
  b.append(d);
  $("#pbs", d).onclick = async () => {
    let board = S.boards.find(x => x.id === pr.board);
    if (!board) { board = { name: pr.title || "Project film", shots: [], private: !!pr.private }; S.boards.push(board); }
    board.name = pr.title || board.name;
    const old = board.shots;
    board.shots = (pr.shots || []).map(s => {
      const prev = old.find(x => x.projShot === s.id);
      const text = shotText(pr, s, true);
      if (prev) { if (!(prev.takes || []).length) { prev.prompt = text; prev.image = s.keyframe || prev.image; } return prev; }
      const n = newShot(text, s.keyframe || ""); n.projShot = s.id; return n;
    });
    await saveBoard(board, true); pr.board = board.id; pr.stage = "Storyboard"; await saveProject(pr, true);
    S.boardId = board.id; store.set("board", board.id); toast("Storyboard ready"); go("board");
  };
  const o = $("#pbo", d); if (o) o.onclick = () => { S.boardId = pr.board; store.set("board", pr.board); location.hash = "#/storyboard"; };
}
function stepSound(b, pr) {
  const total = (pr.shots || []).reduce((a, s) => a + (+s.seconds || 5), 0);
  const lines = (pr.shots || []).filter(s => s.dialogue);
  const d = h(`<div><div class="panel pform"><label class="lbl">Music (${total} s, the film's length)</label><textarea id="pm" rows="2" placeholder="genre, mood, instruments"></textarea>
    <div class="row"><button class="btn primary" id="pmk">♫ Make the music</button><span class="hint" id="pms">${pr.music_file ? "Made: " + esc(pr.music_file.filename) : ""}</span></div>${pr.music_file ? `<audio controls src="${fileUrl(pr.music_file, pr.music_file.private)}"></audio>` : ""}</div>
    <div class="panel pform"><label class="lbl">Dialogue (${lines.length} line${lines.length === 1 ? "" : "s"})</label>${lines.length ? lines.map(s => `<div class="hint">“${esc(s.dialogue)}” <i>(${esc(s.title)})</i>${s.voice_file ? " ✓" : ""}</div>`).join("") : `<p class="hint">No dialogue in the script.</p>`}
    ${lines.length ? `<div class="row"><button class="btn" id="pvk">☺ Make the voice lines</button><span class="hint" id="pvs"></span></div><p class="hint">Uses each character's voice sample when they have one (Characters page). Lines are in Music &amp; voices.</p>` : ""}</div>
    <div class="panel"><p class="hint" style="margin-top:0">The cut: open the board and press 🎬 Make the film. It uses each shot's finished take, adds titles and crossfades, and the music bed.</p><div class="row"><button class="btn primary" id="pfo">🎬 Open the board to cut the film →</button></div></div></div>`);
  b.append(d);
  $("#pm", d).value = pr.music || ""; $("#pm", d).oninput = e => { pr.music = e.target.value; saveProject(pr); };
  $("#pmk", d).onclick = async () => {
    if (!(pr.music || "").trim()) return toast("Describe the music first", true);
    const btn = $("#pmk", d); btn.disabled = true;
    try {
      const r = await post("/manny/api/music", { tags: pr.music, instrumental: true, seconds: Math.min(240, Math.max(15, total)), name: (pr.title || "Film") + " score", private: !!pr.private, target: runTarget() });
      const res = await pollJob(r.id, st => { const e = $("#pms", d); if (e) e.textContent = st + "…"; });
      pr.music_file = res; saveProject(pr, true); toast("Music ready. It's in Music & voices; pick it as the music in the film editor."); const e = $("#pms", d); if (e) e.textContent = "Made: " + res.filename;
    } catch (e) { toast("Music failed: " + e.message.slice(0, 160), true); } finally { btn.disabled = false; }
  };
  const vk = $("#pvk", d); if (vk) vk.onclick = async () => {
    vk.disabled = true;
    for (const s of lines) {
      const who = (pr.cast || []).map(id => S.chars.find(c => c.id === id)).find(c => c && (s.characters || []).some(n => c.name.toLowerCase().startsWith(n.toLowerCase().split(" ")[0])));
      try {
        $("#pvs", d).textContent = "Speaking: " + s.title;
        const r = await post("/manny/api/voice", { text: s.dialogue, voice: who && who.voice ? who.voice : "", name: (pr.title || "Film") + " - " + s.title, private: !!pr.private, target: runTarget() });
        s.voice_file = await pollJob(r.id); saveProject(pr);
      } catch (e) { toast("Voice failed for " + s.title + ": " + e.message.slice(0, 120), true); }
    }
    $("#pvs", d).textContent = "Done"; vk.disabled = false; toast("Voice lines ready in Music & voices");
  };
  $("#pfo", d).onclick = () => { if (!pr.board) return toast("Create the storyboard first (step 5)", true); S.boardId = pr.board; store.set("board", pr.board); location.hash = "#/storyboard"; };
}

/* ------------------------------------------------------------ MUSIC + VOICES */
async function pageSound(main) {
  const st = store.get("sound", { tab: "music", tags: "", lyrics: "", seconds: 60, bpm: 100, key: "C major", timesig: "4", language: "en", instrumental: true, text: "", exag: 0.5, cfgw: 0.5, voice: "", voiceName: "" });
  const opts = await api("/manny/api/sound/options").catch(() => ({ keys: ["C major"], languages: ["en"] }));
  const p = h(`<div class="page"><div class="eyebrow">Sound</div><h1>Music &amp; voices</h1>
    <div class="chips" id="stab"><div class="chip" data-v="music">♫ Music</div><div class="chip" data-v="voice">☺ Voice</div></div>
    <div class="panel sform" id="f-music">
      <label class="lbl">Engine</label><div class="chips" id="m-eng"><div class="chip" data-v="ace" title="Fast: a 1-minute song in ~1 min">ACE-Step 1.5 XL · fast</div><div class="chip" data-v="music3" title="Best quality: full songs up to 5 min, slower">MiniMax Music 3 · best songs, up to 5 min</div></div>
      <label class="lbl">Style</label><textarea id="m-tags" rows="2" placeholder="Genre, mood, instruments, tempo feel. e.g. dark cinematic synthwave, pulsing bass, female vocals"></textarea>
      <label class="tog"><input type="checkbox" id="m-inst"><span><b>Instrumental</b><small>No singing. Turn off to write lyrics.</small></span></label>
      <div id="m-lyrbox"><label class="lbl">Lyrics</label><textarea id="m-lyr" rows="7" placeholder="[Verse 1]\n…\n[Chorus]\n…"></textarea></div>
      <div class="grid4"><div><label class="lbl">Length</label><select id="m-sec">${[15, 30, 45, 60, 90, 120, 180, 240].map(v => `<option value="${v}">${v < 60 ? v + " s" : (v / 60) + " min"}</option>`).join("")}<option value="300">5 min (Music 3)</option></select></div>
        <div><label class="lbl">BPM</label><input type="number" id="m-bpm" min="40" max="220"></div>
        <div><label class="lbl">Key</label><select id="m-key">${opts.keys.map(k => `<option>${k}</option>`).join("")}</select></div>
        <div><label class="lbl">Time</label><select id="m-ts"><option value="4">4/4</option><option value="3">3/4</option><option value="6">6/8</option><option value="2">2/4</option></select></div></div>
      <div class="row"><input type="text" id="m-name" placeholder="Name (optional)"><button class="btn primary" id="m-go">♫ Make the song</button></div>
      <p class="hint" id="m-hint">ACE-Step 1.5 XL Turbo, on this PC. Saved in Audio › Music.</p></div>
    <div class="panel sform hidden" id="f-voice">
      <label class="lbl">What to say</label><textarea id="v-text" rows="5" placeholder="Type the line or the whole speech."></textarea>
      <label class="lbl">Voice to copy (optional)</label><div class="ref audio" id="v-ref" style="aspect-ratio:auto;min-height:70px"><span class="plus">+</span><span class="rl">Drop a clean 5–15 s recording of the voice (wav, mp3). Empty: a default voice.</span><input type="file" hidden accept="audio/*"></div>
      <div class="grid4"><div><label class="lbl">Emotion</label><input type="range" id="v-exag" min="0.25" max="1.5" step="0.05"></div><div><label class="lbl">Pace / stability</label><input type="range" id="v-cfg" min="0.2" max="1" step="0.05"></div></div>
      <div class="row"><input type="text" id="v-name" placeholder="Name (optional)"><button class="btn primary" id="v-go">☺ Speak</button></div>
      <p class="hint">Chatterbox, on this PC. Only copy voices you have permission to use. Saved in Audio › Voices.</p></div>
    <div id="sjobs"></div>
    <div class="section-head"><h2>Your sounds</h2></div><div id="slist" class="slist"></div></div>`);
  main.append(p);
  const save = () => store.set("sound", { ...st });
  const tab = v => { st.tab = v; save(); $$("#stab .chip", p).forEach(c => c.classList.toggle("on", c.dataset.v === v)); $("#f-music", p).classList.toggle("hidden", v !== "music"); $("#f-voice", p).classList.toggle("hidden", v !== "voice"); };
  $$("#stab .chip", p).forEach(c => c.onclick = () => tab(c.dataset.v)); tab(st.tab);
  $("#m-tags", p).value = st.tags; $("#m-lyr", p).value = st.lyrics; $("#m-sec", p).value = st.seconds; $("#m-bpm", p).value = st.bpm; $("#m-key", p).value = st.key; $("#m-ts", p).value = st.timesig; $("#m-inst", p).checked = st.instrumental;
  const lyr = () => $("#m-lyrbox", p).classList.toggle("hidden", $("#m-inst", p).checked); lyr(); $("#m-inst", p).onchange = lyr;
  st.engine = st.engine || "ace";
  const eng = () => {
    if (st.engine === "music3" && !(opts.engines || {}).music3) st.engine = "ace";
    $$("#m-eng .chip", p).forEach(c => { c.classList.toggle("on", c.dataset.v === st.engine); c.classList.toggle("dis", c.dataset.v === "music3" && !(opts.engines || {}).music3); });
    $("#m-hint", p).textContent = st.engine === "music3"
      ? "MiniMax Music 3, on this PC: complete songs with verses and choruses, up to 5 min. Use [intro] [verse] [chorus] [bridge] [outro] tags in the lyrics. Slower than ACE-Step. Saved in Audio › Music."
      : "ACE-Step 1.5 XL Turbo, on this PC: fast, up to 4 min. Saved in Audio › Music.";
  };
  $$("#m-eng .chip", p).forEach(c => c.onclick = () => { if (c.dataset.v === "music3" && !(opts.engines || {}).music3) return toast("MiniMax Music 3 is still downloading (or ComfyUI needs a restart)", true); st.engine = c.dataset.v; save(); eng(); });
  eng();
  $("#v-text", p).value = st.text; $("#v-exag", p).value = st.exag; $("#v-cfg", p).value = st.cfgw;
  const ref = $("#v-ref", p), fi = $("input", ref);
  const showRef = () => { if (st.voice) { ref.classList.add("on"); $(".plus", ref).textContent = "♪"; $(".rl", ref).textContent = st.voiceName || st.voice; } };
  showRef();
  const take = async f => { try { st.voice = await upload(f); st.voiceName = f.name; save(); showRef(); } catch (e) { toast(e.message, true); } };
  ref.onclick = e => { if (e.shiftKey && st.voice) { st.voice = ""; st.voiceName = ""; save(); route(); return; } fi.click(); }; fi.onchange = () => fi.files[0] && take(fi.files[0]);
  ref.ondragover = e => e.preventDefault(); ref.ondrop = e => { e.preventDefault(); e.dataTransfer.files[0] && take(e.dataTransfer.files[0]); };
  const jobs = [];
  const drawJobs = () => { $("#sjobs", p).innerHTML = jobs.map(j => `<div class="sjob ${j.status}"><b>${j.kind === "music" ? "♫" : "☺"} ${esc(j.label)}</b><span>${esc(j.stage)}</span></div>`).join(""); };
  const poll = async j => {
    while (j.status === "running") {
      await new Promise(r => setTimeout(r, 3000));
      if (!document.body.contains(p)) return;
      try { const r = await api("/manny/api/job/" + j.id); j.stage = r.stage; j.status = r.status === "lost" ? "failed" : r.status; if (r.error) j.stage = r.error.slice(0, 220); } catch { }
      drawJobs();
    }
    if (j.status === "done") { toast(j.kind === "music" ? "Song ready" : "Voice ready"); loadList(); }
  };
  const go = async (kind, body, label) => {
    try { const r = await post("/manny/api/" + kind, { ...body, private: !!S.incog, target: runTarget() }); const j = { id: r.id, kind, label, stage: "Queued", status: "running" }; jobs.unshift(j); drawJobs(); poll(j); }
    catch (e) { toast("Couldn't start: " + e.message.slice(0, 200), true); }
  };
  $("#m-go", p).onclick = () => {
    Object.assign(st, { tags: $("#m-tags", p).value, lyrics: $("#m-lyr", p).value, seconds: +$("#m-sec", p).value, bpm: +$("#m-bpm", p).value || 100, key: $("#m-key", p).value, timesig: $("#m-ts", p).value, instrumental: $("#m-inst", p).checked }); save();
    if (!st.tags.trim()) return toast("Describe the style first", true);
    if (!st.instrumental && !st.lyrics.trim()) return toast("Write lyrics, or tick Instrumental", true);
    go("music", { engine: st.engine, tags: st.tags, lyrics: st.instrumental ? "" : st.lyrics, instrumental: st.instrumental, seconds: st.seconds, bpm: st.bpm, key: st.key, timesig: st.timesig, name: $("#m-name", p).value }, $("#m-name", p).value || st.tags.slice(0, 40));
  };
  $("#v-go", p).onclick = () => {
    Object.assign(st, { text: $("#v-text", p).value, exag: +$("#v-exag", p).value, cfgw: +$("#v-cfg", p).value }); save();
    if (!st.text.trim()) return toast("Write what to say", true);
    go("voice", { text: st.text, voice: st.voice, exaggeration: st.exag, cfg_weight: st.cfgw, name: $("#v-name", p).value }, $("#v-name", p).value || st.text.slice(0, 40));
  };
  const loadList = async () => {
    const box = $("#slist", p); let list = [];
    try { list = await api("/manny/api/sounds" + (S.incog ? "?private=1" : "")); } catch { }
    box.innerHTML = list.length ? "" : `<div class="empty">Songs and voices you make show up here.</div>`;
    list.forEach(it => {
      const url = fileUrl(it, it.private);
      box.append(h(`<div class="sitem"><div class="sic">${it.group === "music" ? "♫" : "☺"}</div><div class="sn"><b>${esc(it.filename.replace(/_\d+_?\.\w+$/, ""))}</b><small>${fmtDate(it.mtime)} · ${fmtSize(it.size)}</small></div><audio src="${url}" controls preload="none"></audio><a class="ghost sm" href="${url}" download="${esc(it.filename)}">Download</a></div>`));
    });
  };
  loadList();
}

async function pageReel(main) {
  const R = S.reel;
  const p = h(`<div class="page"><div class="eyebrow">Reel</div><h1>Cut to the beat</h1>
    <p class="muted" style="max-width:640px">Pick pictures and clips, add a song. Studio finds the beat and cuts on it: pictures get a slow push-in, clips play from where they left off.</p>
    <div class="reel"><div class="panel">
      <label class="lbl">Song</label><div class="ref audio ${R.song ? "on" : ""}" id="rs" style="aspect-ratio:auto;min-height:70px"><span class="${R.song ? "wave" : "plus"}">${R.song ? "♪" : "+"}</span><span class="rl">${R.song ? esc(R.song.split("/").pop()) : "Drop a song (mp3, wav)"}</span><input type="file" hidden accept="audio/*"></div>
      <label class="lbl">Shape</label><div class="chips" id="ra"><div class="chip" data-v="9:16">9:16 phone</div><div class="chip" data-v="1:1">1:1 square</div><div class="chip" data-v="16:9">16:9 wide</div></div>
      <label class="lbl">Cut speed</label><div class="chips" id="rc"><div class="chip" data-v="0.6">Fast</div><div class="chip" data-v="1.2">Normal</div><div class="chip" data-v="2.4">Slow</div></div>
      <label class="lbl">Length <span id="rlv"></span></label><input type="range" id="rl" min="10" max="90" step="5">
      <label class="lbl">Name</label><input type="text" id="rn" value="Reel">
      <div class="hint" id="rcount"></div>
      <button class="btn primary" id="rgo" style="width:100%;margin-top:12px">Make the reel</button>
      <div id="rout"></div>
    </div>
    <div><div class="filters"><div class="chips" id="rk"><div class="chip on" data-k="">All</div><div class="chip" data-k="image">Pictures</div><div class="chip" data-k="video">Clips</div><div class="chip" data-k="fav">★ Favorites</div></div><button class="ghost" id="rclr">Clear picks</button></div>
      <div class="grid small" id="rg"></div></div></div></div>`);
  main.append(p);
  const chips = (id, key, cast) => $$(`#${id} .chip`, p).forEach(c => { c.classList.toggle("on", String(R[key]) === c.dataset.v); c.onclick = () => { R[key] = cast(c.dataset.v); chips(id, key, cast); }; });
  chips("ra", "aspect", String); chips("rc", "cut", Number);
  const rl = $("#rl", p); rl.value = R.length; const lv = () => $("#rlv", p).textContent = `· ${R.length} s`; lv(); rl.oninput = () => { R.length = +rl.value; lv(); };
  const rs = $("#rs", p), fi = $("input", rs);
  rs.onclick = () => fi.click();
  const take = async f => { R.song = await upload(f); rs.classList.add("on"); $(".plus,.wave", rs).className = "wave"; $(".wave", rs).textContent = "♪"; $(".rl", rs).textContent = f.name; };
  fi.onchange = () => fi.files[0] && take(fi.files[0]);
  rs.ondragover = e => e.preventDefault(); rs.ondrop = e => { e.preventDefault(); e.dataTransfer.files[0] && take(e.dataTransfer.files[0]); };
  const items = await loadGallery(true);
  const key = it => (it.subfolder || "") + "/" + it.filename;
  let kind = "";
  const count = () => $("#rcount", p).textContent = R.picks.length ? `${R.picks.length} picked, used in the order you picked them` : "Pick pictures and clips on the right";
  const draw = () => {
    const g = $("#rg", p); g.innerHTML = "";
    items.filter(i => !kind || (kind === "fav" ? i.fav : i.kind === kind)).slice(0, 300).forEach(it => {
      const n = R.picks.findIndex(x => key(x) === key(it));
      const el = h(`<div class="tile pick ${n >= 0 ? "on" : ""}">${it.kind === "video" ? `<span class="badge v">Video</span>` : ""}<img src="${thumbUrl(it)}" loading="lazy"><span class="pnum">${n >= 0 ? n + 1 : ""}</span></div>`);
      el.onclick = () => { const k = R.picks.findIndex(x => key(x) === key(it)); if (k >= 0) R.picks.splice(k, 1); else R.picks.push(it); draw(); count(); };
      g.append(el);
    });
  };
  $$("#rk .chip", p).forEach(c => c.onclick = () => { kind = c.dataset.k; $$("#rk .chip", p).forEach(x => x.classList.toggle("on", x === c)); draw(); });
  $("#rclr", p).onclick = () => { R.picks = []; draw(); count(); };
  draw(); count();
  $("#rgo", p).onclick = async () => {
    if (!R.song) return toast("Add a song first", true);
    if (!R.picks.length) return toast("Pick some pictures or clips", true);
    const btn = $("#rgo", p); btn.disabled = true;
    try {
      const r = await post("/manny/api/reel", { items: R.picks.map(i => ({ filename: i.filename, subfolder: i.subfolder, kind: i.kind })), song: R.song, aspect: R.aspect, length: R.length, cut: R.cut, name: $("#rn", p).value, private: S.incog });
      const res = await pollJob(r.id, s => btn.textContent = s);
      const out = $("#rout", p); out.innerHTML = "";
      out.append(h(`<div class="filmbox"><video src="${fileUrl(res, res.private)}" controls playsinline autoplay></video><p class="hint">${res.cuts} cuts at ${res.bpm} BPM · saved in Videos › Reels · <a href="${fileUrl(res, res.private)}" download>Download</a></p></div>`));
      S.gallery = []; toast("Reel ready");
    } catch (e) { toast("Couldn't make the reel: " + e.message.slice(0, 300), true); }
    finally { btn.disabled = false; btn.textContent = "Make the reel"; }
  };
}

/* ------------------------------------------------------------ mobile: more menu */
function moreMenu() {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog sheet"><div class="row" style="justify-content:space-between"><h2>More</h2><button class="icon" data-x>✕</button></div>
    <a href="#/home" class="mitem">⌂ Home</a><a href="#/projects" class="mitem">◧ Projects</a><a href="#/characters" class="mitem">☺ Characters</a><a href="#/3d" class="mitem">◰ 3D sets</a><a href="#/settings" class="mitem">⚙ Settings</a><a href="#/loras" class="mitem">◈ Models &amp; LoRAs</a><a href="#/reel" class="mitem">♫ Reel</a><a href="#/sound" class="mitem">♪ Music &amp; voices</a></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $("[data-x]", d).onclick = close; $$("a", d).forEach(a => a.addEventListener("click", close));
}

/* ------------------------------------------------------------ LOCATIONS (places, used for sets and as references) */
async function loadLocs() { try { S.locs = await api("/manny/api/locations" + PQ()); } catch { S.locs = []; } return S.locs; }
function editLoc(l, done) {
  l = l ? JSON.parse(JSON.stringify(l)) : { name: "", description: "", pictures: [] };
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>${l.id ? "Edit location" : "New location"}</h2><button class="icon" data-x>✕</button></div>
    <div><label class="lbl">Name</label><input type="text" id="ln" placeholder="Neon alley, Tokyo"></div>
    <div><label class="lbl">What it looks like</label><textarea id="ld" rows="3" placeholder="narrow wet alley, vending machines, red lanterns, steam, night"></textarea></div>
    <div><label class="lbl">Pictures (the first one is the main view)</label><div class="locpics"></div></div>
    <div class="foot">${l.id ? `<button class="ghost danger" data-del style="margin-right:auto">Delete</button>` : ""}<button class="ghost" data-x>Cancel</button><button class="btn primary" data-save>Save</button></div></div>`);
  m.append(d);
  $("#ln", d).value = l.name || ""; $("#ld", d).value = l.description || "";
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  const draw = () => {
    const box = $(".locpics", d); box.innerHTML = "";
    l.pictures.forEach((p, k) => { const t = h(`<div class="ref on"><img src="${inputUrl(p)}"><button class="x">✕</button></div>`); $(".x", t).onclick = () => { l.pictures.splice(k, 1); draw(); }; box.append(t); });
    const add = h(`<div class="ref"><span class="plus">+</span><span class="rl">Add pictures</span><input type="file" hidden accept="image/*" multiple></div>`);
    const fi = $("input", add); add.onclick = () => fi.click();
    const take = async fs => { for (const f of fs) l.pictures.push(await upload(f)); draw(); };
    fi.onchange = () => take([...fi.files]); add.ondragover = e => e.preventDefault(); add.ondrop = e => { e.preventDefault(); take([...e.dataTransfer.files]); };
    box.append(add);
  };
  draw();
  $("[data-save]", d).onclick = async () => {
    l.name = $("#ln", d).value.trim(); l.description = $("#ld", d).value.trim(); l.private = S.incog;
    if (!l.name) return toast("Give the location a name", true);
    await post("/manny/api/locations", l); close(); toast("Saved " + l.name); done && done();
  };
  const del = $("[data-del]", d); if (del) del.onclick = async () => { if (!confirm("Delete " + l.name + "?")) return; await api("/manny/api/locations/" + l.id, { method: "DELETE" }); close(); done && done(); };
}

/* ------------------------------------------------------------ 3D: sets and scenes */
const MOVES = [["static", "Static", 0], ["push_in", "Push in", 1.5], ["pull_back", "Pull back", 1.5], ["truck_left", "Truck left", 1.5], ["truck_right", "Truck right", 1.5],
  ["orbit_left", "Orbit left", 30], ["orbit_right", "Orbit right", 30], ["crane_up", "Crane up", 1.5], ["crane_down", "Crane down", 1], ["pan_left", "Pan left", 25], ["pan_right", "Pan right", 25],
  ["tilt_up", "Tilt up", 1], ["tilt_down", "Tilt down", 1], ["zoom_in", "Zoom in", 0.6], ["zoom_out", "Zoom out", 0.6], ["handheld", "Handheld", 0]];
const MOVE_WORDS = { static: "static locked-off camera", push_in: "slow dolly push-in", pull_back: "slow dolly pull-back", truck_left: "camera trucks left", truck_right: "camera trucks right",
  orbit_left: "camera orbits left around the subject", orbit_right: "camera orbits right around the subject", crane_up: "crane shot rising up", crane_down: "crane shot coming down",
  pan_left: "camera pans left", pan_right: "camera pans right", tilt_up: "camera tilts up", tilt_down: "camera tilts down", zoom_in: "slow zoom in", zoom_out: "slow zoom out", handheld: "handheld camera" };
const CAST_COLORS = [[0.92, 0.55, 0.3], [0.3, 0.5, 0.92], [0.35, 0.8, 0.45], [0.7, 0.4, 0.85], [0.95, 0.85, 0.3], [0.9, 0.35, 0.45]];
const CAST_NAMES = ["orange", "blue", "green", "purple", "yellow", "red"];
const rgbCss = c => `rgb(${c.map(v => Math.round(v * 255)).join(",")})`;
function moveEnd(cam, move, amt) {   // same maths as blender/studio_scene.py
  let P = [cam.x, cam.y, cam.z ?? 1.6], T = [cam.tx, cam.ty, cam.tz ?? 1.4], L = cam.lens || 35;
  const d = [T[0] - P[0], T[1] - P[1], T[2] - P[2]], dl = Math.hypot(...d) || 1, fl = Math.hypot(d[0], d[1]) || 1;
  const f = [d[0] / fl, d[1] / fl], side = [f[1], -f[0]];
  const rot = (v, c, deg) => { const a = deg * Math.PI / 180, x = v[0] - c[0], y = v[1] - c[1]; return [c[0] + x * Math.cos(a) - y * Math.sin(a), c[1] + x * Math.sin(a) + y * Math.cos(a), v[2]]; };
  let P2 = [...P], T2 = [...T], L2 = L;
  switch (move) {
    case "push_in": P2 = P.map((v, i) => v + d[i] / dl * amt); break;
    case "pull_back": P2 = P.map((v, i) => v - d[i] / dl * amt); break;
    case "truck_left": P2 = [P[0] - side[0] * amt, P[1] - side[1] * amt, P[2]]; T2 = [T[0] - side[0] * amt, T[1] - side[1] * amt, T[2]]; break;
    case "truck_right": P2 = [P[0] + side[0] * amt, P[1] + side[1] * amt, P[2]]; T2 = [T[0] + side[0] * amt, T[1] + side[1] * amt, T[2]]; break;
    case "orbit_left": P2 = rot(P, T, amt); break;
    case "orbit_right": P2 = rot(P, T, -amt); break;
    case "crane_up": P2 = [P[0], P[1], P[2] + amt]; break;
    case "crane_down": P2 = [P[0], P[1], P[2] - amt]; break;
    case "pan_left": T2 = rot(T, P, amt); break;
    case "pan_right": T2 = rot(T, P, -amt); break;
    case "tilt_up": T2 = [T[0], T[1], T[2] + amt]; break;
    case "tilt_down": T2 = [T[0], T[1], T[2] - amt]; break;
    case "zoom_in": L2 = L * (1 + amt); break;
    case "zoom_out": L2 = Math.max(8, L / (1 + amt)); break;
  }
  return { P2, T2, L2 };
}
async function loadSets() { try { S.sets = await api("/manny/api/sets" + PQ()); } catch { S.sets = []; } return S.sets; }
async function loadScenes() { try { S.scenes = await api("/manny/api/scenes" + PQ()); } catch { S.scenes = []; } return S.scenes; }
const sceneSaves = {};
function saveScene(sc, now) {
  clearTimeout(sceneSaves[sc.id || "new"]);
  if (sc.private == null) sc.private = S.incog;
  const go = async () => { const r = await post("/manny/api/scenes", sc); if (!sc.id) sc.id = r.id; };
  if (now || !sc.id) return go();
  sceneSaves[sc.id] = setTimeout(go, 700);
}
const planUrl = st => `/manny/api/sets/${st.id}/plan.png?t=${Math.round(st.updated || 0)}`;

async function page3D(main, arg) {
  if (arg) return sceneEditor(main, arg);
  const p = h(`<div class="page"><div class="eyebrow">3D</div><h1>Sets &amp; scenes</h1>
    <p class="muted" style="max-width:720px">A set is a Blender file. Claude can build one from your location pictures, Studio can make a quick one, or you can add any .blend. Plan cameras on the set, render previews, then turn shots into footage in the Storyboard.</p>
    <div class="hint" id="blst"></div>
    <div class="section-head"><h2>Sets</h2><div class="row"><button class="ghost" id="qset">＋ Quick set</button><button class="ghost" id="aset">＋ Add a .blend</button><button class="ghost" id="cl">Build with Claude…</button></div></div>
    <div class="lgrid" id="sg"></div>
    <div class="section-head"><h2>Scenes</h2></div><div class="sclist" id="scl"></div>
    <div class="section-head"><h2>Locations</h2><button class="ghost" id="nloc">＋ New location</button></div><div class="lgrid" id="lcg"></div></div>`);
  main.append(p);
  api("/manny/api/blender/status").then(s => { $("#blst", p).innerHTML = s.found ? `Blender: <b>${esc(s.path)}</b> · sets folder: <b>${esc(s.sets_folder)}</b>` : `<span class="warn">Blender wasn't found on this PC.</span>`; S.setsFolder = s.sets_folder; }).catch(() => { });
  const [sets, scenes, locs] = await Promise.all([loadSets(), loadScenes(), loadLocs()]);
  const sg = $("#sg", p);
  if (!sets.length) sg.append(h(`<div class="empty">No sets yet.</div>`));
  sets.forEach(st => {
    const c = h(`<div class="lcard"><img src="${planUrl(st)}" class="plan"><div class="lb2"><b>${esc(st.name)}</b><span>${{ claude: "Built by Claude", studio: "Quick set", blend: ".blend" }[st.source] || ""} · ${(st.cameras || []).length} camera${(st.cameras || []).length === 1 ? "" : "s"}</span>
      <div class="row" style="gap:6px;margin-top:6px"><button class="ghost" data-new>New scene</button><button class="icon" data-ref title="Read the .blend again">↻</button><button class="icon" data-rm title="Remove from Studio (the file stays)">✕</button></div></div></div>`);
    $("[data-new]", c).onclick = async () => { const sc = { name: st.name + " scene", set: st.id, cast: [], shots: [] }; await saveScene(sc, true); location.hash = "#/3d/" + sc.id; };
    $("[data-ref]", c).onclick = async () => { const r = await post(`/manny/api/sets/${st.id}/refresh`, {}); toast("Reading the set…"); await pollJob(r.id); route(); };
    $("[data-rm]", c).onclick = async () => { if (!confirm(`Remove “${st.name}” from Studio? The .blend file stays where it is.`)) return; await api("/manny/api/sets/" + st.id, { method: "DELETE" }); route(); };
    sg.append(c);
  });
  const scl = $("#scl", p);
  if (!scenes.length) scl.append(h(`<div class="empty">No scenes yet. Pick a set and press “New scene”.</div>`));
  scenes.forEach(sc => {
    const st = sets.find(s => s.id === sc.set);
    const r = h(`<a class="scrow" href="#/3d/${sc.id}"><b>${esc(sc.name)}</b><span>${esc(st ? st.name : "missing set")} · ${(sc.shots || []).length} shots · ${(sc.cast || []).length} cast</span></a>`);
    scl.append(r);
  });
  const lg = $("#lcg", p);
  locs.forEach(l => { const c = h(`<div class="lcard">${l.pictures[0] ? `<img src="${inputUrl(l.pictures[0])}">` : `<div class="lph">PLACE</div>`}<div class="lb2"><b>${esc(l.name)}</b><span>${esc((l.description || "").slice(0, 70))}</span></div></div>`); c.onclick = () => editLoc(l, route); lg.append(c); });
  $("#nloc", p).onclick = () => editLoc(null, route);
  $("#qset", p).onclick = () => quickSetDialog(locs);
  $("#aset", p).onclick = () => addBlendDialog();
  $("#cl", p).onclick = () => claudeHelp();
}
function jobDialog(title, body, start) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>${title}</h2><button class="icon" data-x>✕</button></div>${body}<div class="foot"><span class="hint" id="jst" style="margin-right:auto"></span><button class="ghost" data-x>Cancel</button><button class="btn primary" id="jgo">Go</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  $("#jgo", d).onclick = async () => {
    $("#jgo", d).disabled = true;
    try { const r = await start(d); await pollJob(r.id, s => { const e = $("#jst", d); if (e) e.textContent = s; }); close(); route(); }
    catch (e) { toast(e.message.slice(0, 300), true); $("#jgo", d).disabled = false; }
  };
  return d;
}
function quickSetDialog(locs) {
  let back = "", kind = "room";
  const d = jobDialog("Quick set", `<p class="hint" style="margin:0">A blocking set: floor, walls, a backdrop picture and a few boxes. Good for planning; ask Claude for a detailed one.</p>
    <div><label class="lbl">Name</label><input type="text" id="qn" value="Set ${(S.sets || []).length + 1}"></div>
    <div><label class="lbl">Kind</label><div class="chips" id="qk"><div class="chip" data-v="room">Room</div><div class="chip" data-v="street">Street</div><div class="chip" data-v="stage">Stage + backdrop</div><div class="chip" data-v="exterior">Open ground</div></div></div>
    <div class="two"><div><label class="lbl">Width (m)</label><input type="number" id="qw" value="8"></div><div><label class="lbl">Depth (m)</label><input type="number" id="qd" value="8"></div></div>
    <div><label class="lbl">Backdrop picture</label><select id="ql"><option value="">None</option>${locs.filter(l => l.pictures[0]).map(l => `<option value="${esc(l.pictures[0])}">${esc(l.name)}</option>`).join("")}</select></div>`,
    async dd => post("/manny/api/sets/build", { name: $("#qn", dd).value, private: S.incog, location: null, set: { kind, w: +$("#qw", dd).value || 8, d: +$("#qd", dd).value || 8, h: 3.2, backdrop: $("#ql", dd).value || "" } }));
  const chip = () => $$("#qk .chip", d).forEach(c => { c.classList.toggle("on", c.dataset.v === kind); c.onclick = () => { kind = c.dataset.v; chip(); }; }); chip();
}
function addBlendDialog() {
  let up = "";
  const d = jobDialog("Add a .blend", `<div><label class="lbl">Path on the PC</label><input type="text" id="bp" placeholder="C:\\Users\\Manny\\Documents\\Studio Sets\\Alley.blend"></div>
    <div class="hint">or</div><div class="ref" id="bu" style="aspect-ratio:auto;min-height:64px"><span class="plus">+</span><span class="rl">Upload a .blend from this device</span><input type="file" hidden accept=".blend"></div>
    <div><label class="lbl">Name</label><input type="text" id="bn" placeholder="(file name)"></div>`,
    async dd => post("/manny/api/sets/add", { blend: up ? "input:" + up : $("#bp", dd).value, name: $("#bn", dd).value, private: S.incog }));
  const bu = $("#bu", d), fi = $("input", bu); bu.onclick = () => fi.click();
  fi.onchange = async () => { if (!fi.files[0]) return; $(".rl", bu).textContent = "Uploading…"; up = await upload(fi.files[0]); bu.classList.add("on"); $(".rl", bu).textContent = fi.files[0].name; };
}
function claudeHelp() {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const root = "C:\\Users\\Manny\\AppData\\Local\\Comfy-Desktop\\ComfyUI-Installs\\ComfyUI\\ComfyUI";
  const cfg = JSON.stringify({ mcpServers: { studio: { command: root + "\\.venv\\Scripts\\python.exe", args: [root + "\\custom_nodes\\ComfyUI-MannyStudio\\studio_mcp.py"] } } }, null, 2);
  const d = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>Build sets with Claude</h2><button class="icon" data-x>✕</button></div>
    <ol class="muted" style="line-height:1.7;margin:0;padding-left:18px"><li>Add a <b>Location</b> here with a few pictures of the place.</li>
    <li>In Claude (desktop app), with the Studio connector and the Higgsfield Bridge on, ask: <i>“Build a 3D set in Blender from my Studio location ‘Neon alley’ and add it to Studio.”</i></li>
    <li>Claude reads the pictures from Studio, builds the set in Blender, saves it in the Studio Sets folder and registers it. It shows up here with its cameras.</li></ol>
    <label class="lbl">One-time setup: add this to Claude config (run Add Studio to Claude.cmd in Downloads › Studio MCP setup)</label><pre class="cfg"></pre><button class="ghost" id="cp">Copy</button></div>`);
  m.append(d); $(".cfg", d).textContent = cfg;
  $("#cp", d).onclick = () => copyText(cfg);
  $("[data-x]", d).onclick = () => { m.classList.add("hidden"); m.innerHTML = ""; };
}

async function sceneEditor(main, id) {
  const [scenes, sets] = await Promise.all([loadScenes(), loadSets()]); await loadChars(); await loadLocs();
  const sc = scenes.find(x => x.id === id); if (!sc) { main.append(h(`<div class="page"><div class="empty">Scene not found.</div></div>`)); return; }
  const st = sets.find(x => x.id === sc.set);
  sc.cast = sc.cast || []; sc.shots = sc.shots || [];
  let sel = sc.shots[0] ? sc.shots[0].id : null;
  const p = h(`<div class="page"><div class="eyebrow"><a href="#/3d">3D</a> · ${esc(st ? st.name : "missing set")}</div>
    <div class="row sbtop"><h1 id="scn" contenteditable="true" spellcheck="false">${esc(sc.name)}</h1></div>
    <div class="sced"><div class="planbox"><div class="planwrap"><img class="planimg"><svg class="plansvg"></svg></div>
      <p class="hint">Drag the camera ● and what it looks at ◆. Drag people. With a shot selected, ⇢ sets where a person walks to.</p></div>
      <div class="scside">
        <div class="panel"><div class="lblrow"><label class="lbl">Location</label></div><select id="sloc"><option value="">None</option>${S.locs.map(l => `<option value="${l.id}" ${sc.location === l.id ? "selected" : ""}>${esc(l.name)}</option>`).join("")}</select>
          <div class="lblrow" style="margin-top:12px"><label class="lbl">Cast</label><select id="addc"><option value="">＋ Add…</option>${S.chars.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}<option value="_">Someone else</option></select></div><div id="castl"></div></div>
        <div class="panel"><div class="lblrow"><label class="lbl">Shots</label><select id="adds"><option value="">＋ Add shot…</option><option value="_studio">Studio camera</option>${(st?.cameras || []).map(c => `<option value="${esc(c.name)}">Set camera: ${esc(c.name)}</option>`).join("")}</select></div><div id="shotl"></div></div>
        <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn" id="rgrey">Render previews</button><button class="btn" id="rlit">Render lit</button><button class="btn primary" id="tosb">Send to Storyboard →</button></div>
        <div class="hint" id="rst"></div>
      </div></div></div>`);
  main.append(p);
  $("#scn", p).oninput = e => { sc.name = e.target.textContent.trim() || "Scene"; saveScene(sc); };
  $("#sloc", p).onchange = e => { sc.location = e.target.value; saveScene(sc); };
  const plan = st && st.plan ? st.plan : { cx: 0, cy: 0, span: 10 };
  const img = $(".planimg", p), svg = $(".plansvg", p);
  if (st) img.src = planUrl(st);
  const SZ = 1000;
  svg.setAttribute("viewBox", `0 0 ${SZ} ${SZ}`);
  const toPx = (x, y) => [(x - (plan.cx - plan.span / 2)) / plan.span * SZ, ((plan.cy + plan.span / 2) - y) / plan.span * SZ];
  const toM = (px, py) => [plan.cx - plan.span / 2 + px / SZ * plan.span, plan.cy + plan.span / 2 - py / SZ * plan.span];
  const shot = () => sc.shots.find(s => s.id === sel);
  const ns = "http://www.w3.org/2000/svg";
  const el = (tag, attrs) => { const e = document.createElementNS(ns, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  function drag(node, onMove) {
    node.style.cursor = "grab";
    node.addEventListener("pointerdown", e => {
      e.preventDefault(); node.setPointerCapture(e.pointerId);
      const mv = ev => { const r = svg.getBoundingClientRect(); const [x, y] = toM((ev.clientX - r.left) / r.width * SZ, (ev.clientY - r.top) / r.height * SZ); onMove(Math.round(x * 100) / 100, Math.round(y * 100) / 100); drawPlan(); };
      const up = () => { node.removeEventListener("pointermove", mv); node.removeEventListener("pointerup", up); saveScene(sc); drawShots(); };
      node.addEventListener("pointermove", mv); node.addEventListener("pointerup", up);
    });
  }
  function drawPlan() {
    svg.innerHTML = "";
    (st?.cameras || []).forEach(c => { const [x, y] = toPx(c.x, c.y), [tx, ty] = toPx(c.tx, c.ty); svg.append(el("line", { x1: x, y1: y, x2: tx, y2: ty, class: "pcl" })); const g = el("circle", { cx: x, cy: y, r: 9, class: "pcam set" }); g.append(Object.assign(el("title", {}), { textContent: c.name })); svg.append(g); });
    const s = shot();
    sc.cast.forEach((c, k) => {
      const [x, y] = toPx(c.x, c.y), col = rgbCss(c.color || CAST_COLORS[k % 6]);
      const to = s && s.walks && s.walks[c.id];
      if (to) { const [wx, wy] = toPx(to[0], to[1]); svg.append(el("line", { x1: x, y1: y, x2: wx, y2: wy, class: "pwalk" })); const w = el("circle", { cx: wx, cy: wy, r: 12, class: "pghost", style: `stroke:${col}` }); drag(w, (mx, my) => { s.walks[c.id] = [mx, my]; }); svg.append(w); }
      const a = (c.rot || 0) * Math.PI / 180;
      svg.append(el("line", { x1: x, y1: y, x2: x - Math.sin(a) * 26, y2: y - Math.cos(a) * 26, class: "pface" }));
      const n = el("circle", { cx: x, cy: y, r: 15, fill: col, class: "pman" }); drag(n, (mx, my) => { c.x = mx; c.y = my; }); svg.append(n);
      const t = el("text", { x: x, y: y + 34, class: "plab" }); t.textContent = c.name; svg.append(t);
    });
    if (s && s.cam && !s.camera) {
      const c = s.cam, [x, y] = toPx(c.x, c.y), [tx, ty] = toPx(c.tx, c.ty), e = moveEnd(c, s.move, s.amount || 0);
      const [ex, ey] = toPx(e.P2[0], e.P2[1]), [etx, ety] = toPx(e.T2[0], e.T2[1]);
      const fov = 2 * Math.atan(18 / (c.lens || 35)), ang = Math.atan2(ty - y, tx - x), R = 160;
      svg.append(el("path", { d: `M${x},${y} L${x + Math.cos(ang - fov / 2) * R},${y + Math.sin(ang - fov / 2) * R} L${x + Math.cos(ang + fov / 2) * R},${y + Math.sin(ang + fov / 2) * R} Z`, class: "pfov" }));
      if (s.move && s.move !== "static" && s.move !== "handheld") { svg.append(el("line", { x1: x, y1: y, x2: ex, y2: ey, class: "pmove" })); svg.append(el("circle", { cx: ex, cy: ey, r: 9, class: "pcam end" })); if (etx !== tx || ety !== ty) svg.append(el("line", { x1: tx, y1: ty, x2: etx, y2: ety, class: "pmove" })); }
      svg.append(el("line", { x1: x, y1: y, x2: tx, y2: ty, class: "paim" }));
      const tg = el("rect", { x: tx - 9, y: ty - 9, width: 18, height: 18, transform: `rotate(45 ${tx} ${ty})`, class: "ptgt" }); drag(tg, (mx, my) => { c.tx = mx; c.ty = my; }); svg.append(tg);
      const cm = el("circle", { cx: x, cy: y, r: 14, class: "pcam" }); drag(cm, (mx, my) => { c.x = mx; c.y = my; }); svg.append(cm);
    }
  }
  function drawCast() {
    const box = $("#castl", p); box.innerHTML = "";
    sc.cast.forEach((c, k) => {
      const s = shot(), walking = s && s.walks && s.walks[c.id];
      const r = h(`<div class="castrow"><span class="dot" style="background:${rgbCss(c.color || CAST_COLORS[k % 6])}"></span><b>${esc(c.name)}</b>
        <label class="hint">facing <input type="range" min="-180" max="180" step="15" value="${c.rot || 0}"></label><label class="hint">${(c.height || 1.75).toFixed(2)} m <input type="range" class="ht" min="1.0" max="2.2" step="0.05" value="${c.height || 1.75}"></label>
        ${s ? `<button class="ghost sm" data-walk>${walking ? "✓ walks" : "⇢ walks"}</button>` : ""}<button class="icon" data-rm>✕</button></div>`);
      $("input", r).oninput = e => { c.rot = +e.target.value; drawPlan(); saveScene(sc); };
      $(".ht", r).oninput = e => { c.height = +e.target.value; saveScene(sc); };
      const w = $("[data-walk]", r); if (w) w.onclick = () => { s.walks = s.walks || {}; if (s.walks[c.id]) delete s.walks[c.id]; else s.walks[c.id] = [c.x + 1.5, c.y + 1]; saveScene(sc); drawCast(); drawPlan(); };
      $("[data-rm]", r).onclick = () => { sc.cast.splice(k, 1); saveScene(sc); drawCast(); drawPlan(); };
      box.append(r);
    });
  }
  $("#addc", p).onchange = e => {
    const v = e.target.value; e.target.value = ""; if (!v) return;
    const ch = S.chars.find(c => c.id === v), k = sc.cast.length;
    const name = ch ? ch.name : (prompt("Name?") || "Person " + (k + 1));
    sc.cast.push({ id: Math.random().toString(36).slice(2, 8), char: ch ? ch.id : null, name, x: plan.cx + (k - 1) * 0.8, y: plan.cy, rot: 180, height: 1.75, color: CAST_COLORS[k % 6] });
    saveScene(sc, true); drawCast(); drawPlan();
  };
  $("#adds", p).onchange = e => {
    const v = e.target.value; e.target.value = ""; if (!v) return;
    const n = sc.shots.length + 1, s = { id: "s" + Math.random().toString(36).slice(2, 7), name: "Shot " + n, seconds: 5, prompt: "", engine: "keyframes" };
    if (v === "_studio") { const b = st?.bounds || [-4, -4, 4, 4]; s.cam = { x: plan.cx, y: b[1] + 0.8, z: 1.6, tx: plan.cx, ty: plan.cy + 1, tz: 1.4, lens: 35 }; s.move = "push_in"; s.amount = 1.5; }
    else { s.camera = v; const c = (st.cameras || []).find(c => c.name === v); s.cam = c ? { ...c } : null; }
    sc.shots.push(s); sel = s.id; saveScene(sc, true); drawShots(); drawCast(); drawPlan();
  };
  function drawShots() {
    const box = $("#shotl", p); box.innerHTML = "";
    if (!sc.shots.length) box.append(h(`<div class="hint">Add a shot: a Studio camera you place on the plan, or a camera from the set.</div>`));
    sc.shots.forEach((s, k) => {
      const pv = s.previs || {}, clip = pv.lit || pv.grey;
      const r = h(`<div class="pshot ${s.id === sel ? "on" : ""}"><div class="row" style="justify-content:space-between"><b contenteditable="true" spellcheck="false" class="psn">${esc(s.name)}</b><div class="row" style="gap:4px"><span class="hint">${s.camera ? "set camera" : "Studio camera"}</span><button class="icon" data-rm>✕</button></div></div>
        ${s.camera ? "" : `<div class="row" style="gap:8px"><select class="pmv">${MOVES.map(([v, l]) => `<option value="${v}" ${s.move === v ? "selected" : ""}>${l}</option>`).join("")}</select>
          <label class="hint">amount <input type="range" class="pam" min="0" max="${/orbit|pan/.test(s.move) ? 90 : 4}" step="${/orbit|pan/.test(s.move) ? 5 : 0.1}" value="${s.amount || 0}"></label>
          <label class="hint">lens ${s.cam?.lens || 35} mm <input type="range" class="pln" min="14" max="135" step="1" value="${s.cam?.lens || 35}"></label>
          <label class="hint">height <input type="range" class="pz" min="0.3" max="6" step="0.1" value="${s.cam?.z ?? 1.6}"></label>
          <button class="ghost sm" data-try title="Render this shot with several camera moves and compare them">🎥 Try moves</button></div>`}
        <div class="row" style="gap:8px"><select class="psec">${[5, 8, 10, 15].map(v => `<option ${+s.seconds === v ? "selected" : ""} value="${v}">${v} s</option>`).join("")}</select>
          <select class="peng"><option value="keyframes">Footage: styled keyframes (H3 first + last)</option><option value="motion">Footage: follow the 3D motion (H3)</option><option value="render">Use the Blender render as is</option></select></div>
        <textarea class="ppr" rows="2" placeholder="What happens: action, mood, light, dialogue…">${esc(s.prompt || "")}</textarea>
        ${clip ? `<video src="${fileUrl(clip.video, sc.private)}" muted loop playsinline class="pvid"></video><span class="hint">${pv.lit ? "lit" : "grey"} preview · ${clip.secs.toFixed(1)} s</span>` : ""}</div>`);
      r.onclick = e => { if (e.target.closest("select,input,textarea,button,[contenteditable]")) return; sel = s.id; drawShots(); drawCast(); drawPlan(); };
      $(".peng", r).value = s.engine || "keyframes";
      $(".peng", r).onchange = e => { s.engine = e.target.value; saveScene(sc); };
      $(".psec", r).onchange = e => { s.seconds = +e.target.value; saveScene(sc); };
      $(".ppr", r).oninput = e => { s.prompt = e.target.value; saveScene(sc); };
      $(".psn", r).oninput = e => { s.name = e.target.textContent.trim() || "Shot"; saveScene(sc); };
      $("[data-rm]", r).onclick = () => { if (!confirm("Remove " + s.name + "?")) return; sc.shots.splice(k, 1); if (sel === s.id) sel = sc.shots[0]?.id; saveScene(sc, true); drawShots(); drawPlan(); };
      const mv = $(".pmv", r); if (mv) mv.onchange = e => { s.move = e.target.value; s.amount = (MOVES.find(m => m[0] === s.move) || [0, 0, 1])[2]; saveScene(sc); drawShots(); drawPlan(); };
      const am = $(".pam", r); if (am) am.oninput = e => { s.amount = +e.target.value; saveScene(sc); drawPlan(); };
      const ln = $(".pln", r); if (ln) ln.oninput = e => { s.cam.lens = +e.target.value; e.target.previousSibling.textContent = `lens ${s.cam.lens} mm `; saveScene(sc); drawPlan(); };
      const pz = $(".pz", r); if (pz) pz.oninput = e => { s.cam.z = +e.target.value; saveScene(sc); };
      const v = $("video", r); if (v) { r.onmouseenter = () => v.play().catch(() => { }); r.onmouseleave = () => v.pause(); }
      const tb = $("[data-try]", r); if (tb) tb.onclick = () => tryMovesDialog(sc, s, () => { drawShots(); drawPlan(); });
      box.append(r);
    });
  }
  const render = async look => {
    if (!sc.shots.length) return toast("Add a shot first", true);
    await saveScene(sc, true);
    const st2 = $("#rst", p);
    try {
      const r = await post(`/manny/api/scenes/${sc.id}/render`, { look });
      await pollJob(r.id, s => st2.textContent = s);
      const fresh = (await loadScenes()).find(x => x.id === sc.id); if (fresh) sc.shots = fresh.shots;
      st2.textContent = "Previews ready."; drawShots(); S.gallery = [];
    } catch (e) { st2.textContent = ""; toast("Render failed: " + e.message.slice(0, 300), true); }
  };
  $("#rgrey", p).onclick = () => render("grey");
  $("#rlit", p).onclick = () => render("lit");
  $("#tosb", p).onclick = async () => {
    const missing = sc.shots.filter(s => !(s.previs && (s.previs.grey || s.previs.lit)));
    if (missing.length) return toast("Render previews first (" + missing.length + " shot" + (missing.length > 1 ? "s" : "") + " without one)", true);
    await loadBoards();
    let b = S.boards.find(x => x.scene === sc.id);
    if (!b) { b = { name: sc.name, scene: sc.id, shots: [], private: sc.private }; S.boards.push(b); }
    sc.shots.forEach(s => {
      let t = b.shots.find(x => x.sceneShot === s.id);
      if (!t) { t = newShot(s.prompt || ""); t.sceneShot = s.id; b.shots.push(t); }
      t.prompt = t.prompt || s.prompt || ""; t.camera = s.camera ? "" : (MOVE_WORDS[s.move] || ""); t.engine = s.engine || "keyframes";
      t.previs = s.previs; t.seconds = s.seconds; t.scene = sc.id;
    });
    await saveBoard(b, true); S.boardId = b.id; store.set("board", b.id); location.hash = "#/storyboard";
  };
  drawShots(); drawCast(); drawPlan();
}

/* ---- footage from a 3D shot (called by the Storyboard) */
function sceneRefs(b, s) {
  const sc = (S.scenes || []).find(x => x.id === s.scene) || {};
  const loc = (S.locs || []).find(l => l.id === sc.location);
  const cast = (sc.cast || []).map((c, k) => ({ ...c, colorName: CAST_NAMES[k % 6], ch: S.chars.find(x => x.id === c.char) }));
  return { sc, loc, cast };
}
async function styleFrames(b, s, onStage) {
  const pv = s.previs.lit || s.previs.grey, { loc, cast } = sceneRefs(b, s);
  const who = cast.map(c => `the ${c.colorName} figure is ${c.name}${c.ch && c.ch.look ? " (" + c.ch.look + ")" : ""}`).join("; ");
  const prompt = `Turn this ${s.previs.lit ? "3D render" : "grey 3D blocking render"} into a real cinematic film frame. Keep the exact camera angle, framing, perspective and where every figure stands. ${who ? who.charAt(0).toUpperCase() + who.slice(1) + ". " : ""}${loc ? "The place looks like the second picture: " + (loc.description || loc.name) + ". " : ""}${s.prompt || ""}`.trim();
  const ref = loc && loc.pictures[0] ? loc.pictures[0] : (cast.find(c => c.ch && c.ch.face) || {}).ch?.face || "";
  const one = async (still, label) => {
    const r = await post("/manny/api/previs/style", { still, ref, prompt, private: !!b.private, model: "flux 3", target: runTarget() });
    return (await pollJob(r.id, st => onStage && onStage(label + ": " + st))).value;
  };
  const first = await one(pv.first, "First frame"), last = await one(pv.last, "Last frame");
  s.styled = { first, last, from: pv.first };
  s.image = first;
  return s.styled;
}

/* ------------------------------------------------------------ SETTINGS: models, trash, keys */
async function pageSettings(main) {
  const p = h(`<div class="page"><div class="eyebrow">Settings</div><h1>Settings</h1>
    <div class="chips stabs" id="settabs"><div class="chip" data-t="general">⚙ General</div><div class="chip" data-t="models">◈ Models on this PC</div></div>
    <div data-tab="general">
    <div class="section-head"><h2>Rendering safety</h2></div><div class="panel keybox" id="gdbox"><p class="muted">…</p></div>
    <div class="section-head"><h2>GPU machines</h2><button class="ghost" id="wkr">Check again</button></div><div class="panel keybox" id="wkbox"><p class="muted">Checking…</p></div>
    <div class="section-head"><h2>Topaz</h2><button class="ghost" id="tzt">Check again</button></div><div class="panel keybox" id="tzbox"><p class="muted">Checking…</p></div>
    <div class="section-head"><h2>Civitai</h2></div><div class="panel keybox"><p class="muted" id="ckst">…</p><div class="row"><input type="password" id="ck2" placeholder="New Civitai API key" autocomplete="off"><button class="btn" id="cks2">Save key</button><button class="ghost danger" id="ckr2">Remove key</button></div></div>
    <div class="section-head"><h2>3D</h2></div><div class="panel keybox"><p class="muted" id="b3d">…</p></div>
    <div class="section-head"><h2>Versions</h2><a class="ghost" id="gho" target="_blank" rel="noopener">GitHub ↗</a></div><div class="panel keybox" id="gitbox"><p class="muted">…</p></div>
    <details class="adv"><summary>Advanced</summary><div class="panel keybox"><p class="hint" style="margin-top:0">Private space</p><a class="ghost" href="/manny/password">Change the Incognito password…</a></div></details>
    </div>
    <div data-tab="models" class="hidden">
    <div class="section-head"><h2>Models</h2><span class="hint" id="disk"></span></div>
    <div class="filters"><input type="text" class="search" id="mq2" placeholder="Search models…"><div class="chips" id="mf"></div><select id="ms"><option value="size">Biggest first</option><option value="name">By name</option><option value="new">Newest first</option></select></div>
    <div id="mlist" class="mtable"></div>
    <div class="section-head"><h2>Removed models</h2><button class="ghost danger hidden" id="purgeall">Delete all for good</button></div>
    <p class="hint">Removed models wait here (models › _studio_removed) until you restore them or delete them for good.</p><div id="trash" class="mtable"></div>
    </div></div>`);
  main.append(p);
  const showTab = t => { store.set("settab", t); $$("#settabs .chip", p).forEach(c => c.classList.toggle("on", c.dataset.t === t)); $$("[data-tab]", p).forEach(x => x.classList.toggle("hidden", x.dataset.tab !== t)); };
  $$("#settabs .chip", p).forEach(c => c.onclick = () => showTab(c.dataset.t));
  showTab(location.hash.includes("models") ? "models" : store.get("settab", "general"));
  let all = [], folder = store.get("mfolder", ""), q = "";
  const draw = () => {
    const box = $("#mlist", p); box.innerHTML = "";
    let list = all.filter(m => (!folder || m.folder === folder) && (!q || (m.name + " " + m.title + " " + m.base).toLowerCase().includes(q)));
    const sort = $("#ms", p).value;
    list.sort(sort === "name" ? (a, b) => a.name.localeCompare(b.name) : sort === "new" ? (a, b) => b.mtime - a.mtime : (a, b) => b.size - a.size);
    if (!list.length) box.append(h(`<div class="empty">No models here.</div>`));
    list.slice(0, 400).forEach(m => {
      const r = h(`<div class="mrow"><div class="mn"><b>${esc(m.title || m.name.split("/").pop())}</b><span>${esc(m.folder)} › ${esc(m.name)}${m.base ? " · " + esc(m.base) : ""}</span>${m.used_by.length ? `<span class="used">Used by: ${esc(m.used_by.join(", "))}</span>` : ""}</div><span class="ms">${fmtSize(m.size)}</span><button class="ghost danger sm">Remove</button></div>`);
      $("button", r).onclick = async () => {
        const warn = m.used_by.length ? `\n\n${m.used_by.join(", ")} use${m.used_by.length === 1 ? "s" : ""} this model and will stop working until you restore it.` : "";
        if (!confirm(`Remove ${m.name.split("/").pop()} (${fmtSize(m.size)})?\nIt moves to Removed models, so you can restore it.${warn}`)) return;
        try { await post("/manny/api/models/remove", { folder: m.folder, name: m.name }); all = all.filter(x => x !== m); draw(); loadTrash(); loraCache = null; ckptCache = null; toast("Removed. Restore it below if needed."); }
        catch (e) { toast(e.message, true); }
      };
      box.append(r);
    });
  };
  const folders = [["", "All"], ["checkpoints", "Checkpoints"], ["diffusion_models", "Diffusion"], ["loras", "LoRAs"], ["text_encoders", "Text encoders"], ["vae", "VAE"], ["upscale_models", "Upscalers"], ["embeddings", "Embeddings"], ["controlnet", "ControlNet"], ["clip_vision", "CLIP vision"]];
  folders.forEach(([v, l]) => { const c = h(`<div class="chip ${v === folder ? "on" : ""}">${l}</div>`); c.onclick = () => { folder = v; store.set("mfolder", v); $$("#mf .chip", p).forEach(x => x.classList.toggle("on", x === c)); draw(); }; $("#mf", p).append(c); });
  $("#mq2", p).oninput = e => { q = e.target.value.trim().toLowerCase(); draw(); };
  $("#ms", p).onchange = draw;
  $("#mlist", p).append(h(`<div class="empty">Reading the model folders…</div>`));
  api("/manny/api/models" + PQ()).then(r => {
    all = r.items;
    const tot = all.reduce((a, m) => a + m.size, 0);
    $("#disk", p).textContent = `${all.length} files · ${fmtSize(tot)}` + (r.disk ? ` · ${fmtSize(r.disk.free)} free of ${fmtSize(r.disk.total)}` : "");
    draw();
  }).catch(e => { $("#mlist", p).innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
  async function loadTrash() {
    const box = $("#trash", p); box.innerHTML = "";
    const items = await api("/manny/api/models/removed").catch(() => []);
    $("#purgeall", p).classList.toggle("hidden", !items.length);
    if (!items.length) box.append(h(`<div class="empty">Nothing removed.</div>`));
    items.forEach(m => {
      const r = h(`<div class="mrow"><div class="mn"><b>${esc(m.name.split("/").pop())}</b><span>${esc(m.folder)} › ${esc(m.name)}</span></div><span class="ms">${fmtSize(m.size)}</span><button class="ghost sm" data-r>Restore</button><button class="ghost danger sm" data-d>Delete for good</button></div>`);
      $("[data-r]", r).onclick = async () => { try { await post("/manny/api/models/restore", m); toast("Restored"); route(); } catch (e) { toast(e.message, true); } };
      $("[data-d]", r).onclick = async () => { if (!confirm(`Delete ${m.name.split("/").pop()} for good? This frees ${fmtSize(m.size)} and can't be undone.`)) return; try { await post("/manny/api/models/purge", m); toast("Deleted"); loadTrash(); } catch (e) { toast(e.message, true); } };
      box.append(r);
    });
    $("#purgeall", p).onclick = async () => { const tot = items.reduce((a, m) => a + m.size, 0); if (!confirm(`Delete all ${items.length} removed models for good? This frees ${fmtSize(tot)} and can't be undone.`)) return; try { const r = await post("/manny/api/models/purge", { all: true }); toast(`Deleted ${r.deleted} files, ${fmtSize(r.freed)} freed`); loadTrash(); } catch (e) { toast(e.message, true); } };
  }
  loadTrash();
  const ckst = async () => { const s = await api("/manny/api/civitai/status").catch(() => ({})); $("#ckst", p).textContent = s.has_key ? "A Civitai key is saved on this PC (it's never shown)." : "No Civitai key saved."; };
  ckst();
  $("#cks2", p).onclick = async () => { try { await post("/manny/api/civitai/key", { key: $("#ck2", p).value.trim() }); $("#ck2", p).value = ""; toast("Key saved"); ckst(); } catch (e) { toast(e.message, true); } };
  $("#ckr2", p).onclick = async () => { if (!confirm("Remove the saved Civitai key?")) return; await post("/manny/api/civitai/key", { key: "" }); ckst(); };
  const tzDraw = async force => {
    const box = $("#tzbox", p); box.innerHTML = `<p class="muted">Checking Topaz… (a few seconds)</p>`;
    const t = await topaz(force); if (!t) { box.innerHTML = `<p class="muted">Couldn't check Topaz (restart ComfyUI after updating Studio).</p>`; return; }
    const line = (x, name) => !x.found ? `<span class="bad">✕</span> ${name}: not installed` : x.ready ? `<span class="ok">✓</span> ${name}: ready${x.version ? " · " + esc(x.version) : ""}${x.fps === false ? " (no frame-rate filter)" : ""}` : `<span class="bad">!</span> ${name}: installed but not working. ${esc(x.error || "")}`;
    const opt = (list, v) => list.map(([k, l]) => `<option value="${k}" ${k === v ? "selected" : ""}>${esc(l)}</option>`).join("");
    const imgLine = !t.image.ready && /enterprise/i.test(t.image.error || "") && tzApp(t, "image") ? `<span class="ok">✓</span> Gigapixel (pictures): ready through the app hand-off (its command line needs Topaz Enterprise)` : line(t.image, "Gigapixel (pictures)");
    box.innerHTML = `<p>${imgLine}<br>${line(t.video, "Topaz Video (clips)")}</p>
      <label class="tog"><input type="checkbox" id="tz-use" ${t.prefs.use ? "checked" : ""}><span><b>Use Topaz for upscaling</b><small>Finish and the ✦ buttons in the viewer use Topaz. Off: the ComfyUI upscale apps (SeedVR2) are used.</small></span></label>
      <div class="grid2"><div><label class="lbl">Video upscale model</label><select id="tz-vm">${opt(t.video_models, t.prefs.video_model)}</select></div>
      <div><label class="lbl">Frame-rate model</label><select id="tz-fm">${opt(t.fps_models, t.prefs.fps_model)}</select></div>
      <div><label class="lbl">Picture model</label><select id="tz-im">${opt(t.image_models, t.prefs.image_model)}</select></div>
      <div><label class="lbl">Picture format</label><select id="tz-if">${opt([["png", "PNG"], ["jpg", "JPEG"], ["tif", "TIFF"]], t.prefs.image_format)}</select></div>
      <div><label class="lbl">Video processor</label><select id="tz-dev">${opt([["-2", "Automatic"], ["0", "GPU 1 (Intel Arc)"], ["-1", "CPU only (slow)"]], t.prefs.device)}</select></div></div>
      ${["image", "video"].filter(k => !t[k].ready && tzApp(t, k)).map(k => `<p><b>${k === "image" ? "Gigapixel" : "Topaz Video"} hand-off:</b> the viewer's ✦ button opens the file in the app. Set the app's export folder to <code>${esc(t.apps[k].export_to)}</code> <button class="ghost sm" data-cp="${esc(t.apps[k].export_to)}">Copy</button> and exports appear in the gallery.</p>`).join("")}
      <p class="hint">Topaz uses your Topaz sign-in on this PC. If a model hasn't been used before, open the Topaz app once and preview with it so it downloads.</p>`;
    const save = async () => { tzCache = null; await post("/manny/api/topaz/prefs", { use: $("#tz-use", box).checked, video_model: $("#tz-vm", box).value, fps_model: $("#tz-fm", box).value, image_model: $("#tz-im", box).value, image_format: $("#tz-if", box).value, device: $("#tz-dev", box).value }).then(() => toast("Saved")).catch(e => toast(e.message, true)); };
    $$("input,select", box).forEach(x => x.onchange = save);
    $$("[data-cp]", box).forEach(x => x.onclick = () => copyText(x.dataset.cp, "Folder copied"));
  };
  tzDraw(false); $("#tzt", p).onclick = () => tzDraw(true);
  const gitDraw = async () => {
    const box = $("#gitbox", p); let g;
    try { g = await api("/manny/api/git/status"); } catch { box.innerHTML = `<p class="muted">Restart ComfyUI to turn on versions.</p>`; return; }
    $("#gho", p).href = (g.remote || "").replace(/\.git$/, "");
    if (!g.git) { box.innerHTML = `<p class="muted">Git isn't installed on this PC.</p>`; return; }
    box.innerHTML = `<p class="hint" style="margin-top:0">Studio's code and apps are kept in <code>${esc(g.repo)}</code> and pushed to <code>${esc(g.remote)}</code>. ${g.exists ? (g.unpushed ? `<b>${g.unpushed} version${g.unpushed > 1 ? "s" : ""} not on GitHub yet.</b>` : "") : "Not set up yet: the first save creates it" + (g.bundle ? " from studio.bundle (with the history so far)." : ".")}</p>
      <div id="gup" class="gup"><span class="muted">Checking GitHub…</span></div>
      <div class="row"><input type="text" id="gm" placeholder="What changed (optional)" style="flex:1"><button class="btn primary" id="gs">Save a version</button></div>
      <p class="hint">The first push opens a GitHub sign-in on this PC. If GitHub says the repo doesn't exist, create an empty private repo named <b>studio</b> at github.com/new and save again.</p>
      ${(g.log || []).length ? `<div class="glog">${g.log.map(x => `<div><code>${esc(x.hash)}</code> <span>${esc(x.date)}</span> ${esc(x.message.slice(0, 90))}</div>`).join("")}</div>` : ""}`;
    const up = $("#gup", box);
    const ghFiles = f => `<div class="glog">${f.slice(0, 12).map(x => `<div><code>${esc(x.split("\t")[0])}</code> ${esc(x.split("\t").pop())}</div>`).join("")}${f.length > 12 ? `<div class="muted">…and ${f.length - 12} more</div>` : ""}</div>`;
    const doPull = async prefer => {
      up.innerHTML = `<span class="muted">Getting updates from GitHub…</span>`;
      try {
        const r = await post("/manny/api/git/pull", { prefer }); const res = await pollJob(r.id, st => up.innerHTML = `<span class="muted">${esc(st)}…</span>`);
        if (res.conflicts) {
          up.innerHTML = `<p><b>These files were changed both here and on GitHub:</b></p>${ghFiles(res.conflicts)}
            <div class="row"><button class="btn" data-p="theirs">Use GitHub's version</button><button class="btn" data-p="ours">Keep this PC's version</button></div>`;
          $$("[data-p]", up).forEach(b => b.onclick = () => doPull(b.dataset.p)); return;
        }
        const n = (res.installed || []).length + (res.removed || []).length;
        toast(res.note || (`Got ${res.merged} change${res.merged > 1 ? "s" : ""} from GitHub` + (n ? `, ${n} file${n > 1 ? "s" : ""} updated on this PC` : "")) + (res.restart ? ". Restart ComfyUI to use the new code." : ""));
        gitDraw();
      } catch (e) { up.innerHTML = `<span class="warn">${esc(e.message.slice(0, 300))}</span>`; }
    };
    if (g.exists || g.bundle) (async () => {
      try {
        const r = await post("/manny/api/git/check", {}); const c = await pollJob(r.id);
        if (!c.remote_branch) { up.innerHTML = `<span class="muted">GitHub is empty: the first save pushes everything.</span>`; return; }
        if (!c.behind) { up.innerHTML = `<span class="ok">✓ Up to date with GitHub</span>${c.ahead ? ` <span class="muted">· ${c.ahead} version${c.ahead > 1 ? "s" : ""} to push</span>` : ""}`; return; }
        up.innerHTML = `<p><b>GitHub has ${c.behind} change${c.behind > 1 ? "s" : ""} this PC doesn't have</b>${c.remote_log && c.remote_log[0] ? ` <span class="muted">(latest: “${esc(c.remote_log[0].message.slice(0, 70))}” by ${esc(c.remote_log[0].author)})</span>` : ""}. Get them before saving.</p>
          ${ghFiles(c.files || [])}<button class="btn primary" id="gpull">⬇ Get updates from GitHub</button>`;
        $("#gpull", up).onclick = () => doPull(null);
      } catch (e) { up.innerHTML = `<span class="warn">${esc(e.message.slice(0, 300))}</span>`; }
    })(); else up.innerHTML = "";
    $("#gs", box).onclick = async () => {
      const b = $("#gs", box); b.disabled = true; b.textContent = "Saving…";
      try { const r = await post("/manny/api/git/save", { message: $("#gm", box).value, push: true }); const res = await pollJob(r.id); toast(res.changed ? `Saved ${res.commit}${res.pushed ? " and pushed to GitHub" : ""}` : (res.pushed ? "Nothing new; GitHub is up to date" : "Nothing new to save")); }
      catch (e) { toast(e.message.slice(0, 260), true); }
      gitDraw();
    };
  };
  gitDraw();
  const wkDraw = async () => {
    const box = $("#wkbox", p); await loadWorkers();
    const others = S.workers.slice(1);
    box.innerHTML = `<p class="muted">Other computers running ComfyUI that Studio can send renders to (like the Mac Pro's two GPUs). Pick one under Generate, or “Any free GPU”. A job only goes where every model it needs is installed.</p>
      ${others.map((w, k) => `<div class="row"><span><b>${esc(w.label)}</b> <small class="muted">${esc(w.host)}:${w.port} · ${w.online ? esc(((w.info || {}).device || {}).name || "online") + ` · ${(w.info || {}).queue_remaining || 0} waiting` + (cardMem(w.info) ? " · " + esc(cardMem(w.info)) : "") : "offline: " + esc((w.error || "").slice(0, 120))}</small></span><button class="ghost danger sm" data-rm="${k}">Remove</button></div>`).join("") || `<p class="muted">None yet.</p>`}
      <div class="row"><input id="wkl" placeholder="Name (mac-vega-a)"><input id="wkh" placeholder="Host (Mannys-Mac-Pro.local)"><input id="wkp" placeholder="Port" style="max-width:80px"><button class="btn" id="wka">Add</button></div>`;
    const save = async list => { try { await post("/manny/api/workers", { workers: list.map(({ label, host, port, device }) => ({ label, host, port, device })) }); } catch (e) { toast(e.message, true); } wkDraw(); };
    $$("[data-rm]", box).forEach(b => b.onclick = () => save(others.filter((_, k) => k !== +b.dataset.rm)));
    $("#wka", box).onclick = () => {
      const label = $("#wkl", box).value.trim(), host = $("#wkh", box).value.trim(), port = +$("#wkp", box).value;
      if (!label || !host || !port || label === "local" || label === "auto" || others.some(w => w.label === label)) return toast("Give it a new name, a host and a port", true);
      save([...others, { label, host, port }]);
    };
  };
  wkDraw(); $("#wkr", p).onclick = wkDraw;
  api("/manny/api/guard").then(g => {
    const box = $("#gdbox", p), pr = g.prefs;
    box.innerHTML = `<label class="tog"><input type="checkbox" data-k="free_between_models" ${pr.free_between_models ? "checked" : ""}><span><b>Free memory between different models</b><small>When the next job uses other models, ComfyUI unloads the old ones first. The graphics chip shares the PC's memory, so piling models up makes Windows page to disk and freeze.</small></span></label>
      <label class="tog"><input type="checkbox" data-k="keep_awake" ${pr.keep_awake ? "checked" : ""}><span><b>Keep the PC awake while rendering</b><small>Sleep in the middle of a render leaves the job stuck.</small></span></label>
      <div class="row"><label class="lbl" style="margin:0">Warn when a render shows no progress for</label><select data-k="stall_minutes">${[5, 10, 15, 30, 60].map(v => `<option ${v === pr.stall_minutes ? "selected" : ""}>${v}</option>`).join("")}</select><span class="hint">minutes</span></div>
      <div class="row"><button class="ghost" id="gfree">Free memory now</button><button class="ghost" id="gpow">Check sleep & crash events</button></div><div id="gpowo" class="hint"></div>`;
    $$("[data-k]", box).forEach(x => x.onchange = () => post("/manny/api/guard/prefs", { [x.dataset.k]: x.type === "checkbox" ? x.checked : +x.value }).then(() => toast("Saved")));
    $("#gfree", box).onclick = () => post("/manny/api/guard/free", {}).then(() => toast("Memory will be freed after the current job"));
    $("#gpow", box).onclick = async () => { const o = $("#gpowo", box); o.textContent = "Reading the Windows log…"; try { const ev = await api("/manny/api/diag/power?hours=24"); o.innerHTML = ev.length ? ev.map(e => `${esc(e.t)} · ${esc(e.msg || e.src)}`).join("<br>") : "No sleep, crash or low-memory events in the last 24 hours."; } catch (e) { o.textContent = e.message; } };
  }).catch(() => { $("#gdbox", p).innerHTML = `<p class="muted">Restart ComfyUI to turn this on.</p>`; });
  api("/manny/api/blender/status").then(s => { $("#b3d", p).innerHTML = s.found ? `Blender: <b>${esc(s.path)}</b><br>Sets folder: <b>${esc(s.sets_folder)}</b>` : "Blender wasn't found on this PC."; }).catch(() => { });
}
/* ------------------------------------------------------------ QUEUE + WS */
/* progress from the server: works on every device (ComfyUI only sends step events to the browser that queued
   the job) and moves smoothly through slow steps (Wan: ~23 min per step) using the last step's duration */
const fmtEta = s => s == null ? "" : s < 90 ? "under 2 min left" : s < 3600 ? `about ${Math.round(s / 60)} min left` : `about ${(s / 3600).toFixed(1)} h left`;
async function serverProgress() {
  if (document.hidden || !S.running) return;
  try {
    const g = await api("/manny/api/guard"); const j = g.job;
    if (!j || j.prompt_id !== S.running || j.overall == null) return;
    S.progress = j.overall; S.srvAt = Date.now();
    S.progressNode = [`Step ${j.step}/${j.max}`, j.phase, fmtEta(j.eta_s)].filter(Boolean).join(" · ");
    drawQueueCard();
  } catch { }
}
async function pollQueue() {
  try {
    const q = await api("/queue");
    S.queue = q;
    const run = q.queue_running.length, pend = q.queue_pending.length;
    $("#q-count").textContent = run + pend ? (run + pend) + "" : "";
    if (!run) { S.running = null; S.progress = 0; }
    else S.running = q.queue_running[0][1];
    drawQueueCard();
    if (!$("#drawer").classList.contains("hidden")) drawDrawer();
  } catch { $("#q-title").textContent = "ComfyUI offline"; $("#q-sub").textContent = "Start ComfyUI and this reconnects"; }
}
function appNameOf(item) { const ex = item[3] || {}; const a = S.apps.find(x => x.id === ex.manny_app); return a ? appShort(a) : "Node graph"; }
function drawQueueCard() {
  const q = S.queue; if (!q) return;
  const run = q.queue_running[0];
  const ra = S.remote.filter(remoteActive);
  if (!run && ra.length) {  // nothing on this PC: show what the other GPUs are doing
    const r0 = ra.find(j => j.status === "running") || ra[0];
    $("#q-dot").className = "dot run"; $("#q-title").textContent = (r0.status === "fetching" ? "Downloading on " : "Rendering on ") + r0.worker;
    $("#q-sub").textContent = `${remoteName(r0)} · ${remoteState(r0)}${ra.length > 1 ? " · " + (ra.length - 1) + " more" : ""}`;
    $("#q-bar").style.width = Math.round(r0.progress * 100) + "%";
    $("#mq-dot").className = "dot run"; $("#mq-t").textContent = `${Math.round(r0.progress * 100)}% · ${r0.worker}`;
    return;
  }
  $("#q-dot").className = "dot" + (run ? " run" : "");
  $("#q-title").textContent = run ? "Rendering" : "Idle";
  const nsch = (S.sched || []).filter(x => x.status === "waiting").length;
  $("#q-sub").textContent = (run ? `${appNameOf(run)}${S.progressNode ? " · " + S.progressNode : ""}${q.queue_pending.length ? " · " + q.queue_pending.length + " waiting" : ""}` : "Nothing rendering") + (nsch ? ` · ⏱ ${nsch} later` : "");
  $("#q-bar").style.width = run ? Math.round(S.progress * 100) + "%" : "0%";
  $("#mq-dot").className = "dot" + (run ? " run" : "");
  $("#mq-t").textContent = run ? `${Math.round(S.progress * 100)}%${q.queue_pending.length ? " · +" + q.queue_pending.length : ""}` : (q.queue_pending.length ? q.queue_pending.length + " waiting" : "Idle");
  updateJob();
}
function drawDrawer() {
  const b = $("#drawer-body"); b.innerHTML = ""; const q = S.queue; if (!q) return;
  const waiting = (S.sched || []).filter(x => x.status === "waiting"), recent = (S.sched || []).filter(x => x.status !== "waiting").slice(0, 5);
  if (waiting.length || recent.length) {
    b.append(h(`<div class="qhead">⏱ Scheduled</div>`));
    waiting.forEach(x => {
      const el = h(`<div class="qitem sched"><div class="row"><b>${esc(x.label)}</b><button class="icon" title="Cancel">✕</button></div><small>${x.when === "idle" ? "When the PC is free" : new Date(x.at * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</small><div class="row"><button class="ghost sm">Start now</button></div></div>`);
      const [cx, now] = $$("button", el);
      cx.onclick = async () => { await fetch("/manny/api/schedule/" + x.id, { method: "DELETE" }); loadSchedule(); toast("Cancelled"); };
      now.onclick = async () => { try { await post(`/manny/api/schedule/${x.id}/now`, {}); toast("Sent to the queue"); } catch (e) { toast(e.message, true); } loadSchedule(); setTimeout(pollQueue, 500); };
      b.append(el);
    });
    recent.forEach(x => b.append(h(`<div class="qitem done"><b>${esc(x.label)}</b><small>${x.status === "sent" ? "Started " + new Date(x.sent_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "Failed: " + esc((x.error || "").slice(0, 160))}</small></div>`)));
    b.append(h(`<div class="qhead">Queue</div>`));
  }
  const rem = S.remote.filter(j => remoteActive(j) || Date.now() / 1000 - (j.finished_at || 0) < 600);
  if (!q.queue_running.length && !q.queue_pending.length && !rem.length) b.append(h(`<div class="empty">The queue is empty.</div>`));
  q.queue_running.forEach(it => b.append(qItem(it, true)));
  const movable = q.queue_pending.filter(it => !["studio-videotools", "studio-sound", "studio-ai"].includes((it[3] || {}).client_id));
  if (movable.length > 1 && macWorkers().length) {
    const bar = h(`<div class="qitem qmoveall"><div class="row"><b>Send all ${movable.length} waiting jobs</b><select class="qmove">${moveOptions(true)}</select></div><small>Each Mac downloads a model it's missing when it can find it online; a job it can't run stays here.</small></div>`);
    const sel = $("select", bar); sel.onchange = () => { if (sel.value) { sel.disabled = true; moveJobs(movable.sort((a, b) => a[0] - b[0]).map(it => it[1]), sel.value); } };
    b.append(bar);
  }
  q.queue_pending.slice().sort((a, b) => a[0] - b[0]).forEach(it => b.append(qItem(it, false)));
  if (rem.length) {
    b.append(h(`<div class="qhead">Other GPUs</div>`));
    rem.forEach(j => {
      const live = remoteActive(j);
      const el = h(`<div class="qitem ${j.status === "running" ? "run" : ""} ${live ? "" : "done"}"><div class="row"><b>${esc(remoteName(j))}</b>${live && j.status !== "cancelling" ? `<button class="icon" title="Stop">✕</button>` : ""}</div>
        <small>${esc(j.worker)} · ${esc(remoteState(j))}</small>${["running", "fetching"].includes(j.status) ? `<div class="bar" style="margin-top:8px"><div style="width:${Math.round(j.progress * 100)}%"></div></div>` : ""}</div>`);
      const x = $("button", el);
      if (x) x.onclick = async () => { x.disabled = true; try { await post(`/manny/api/remote_jobs/${j.prompt_id}/cancel`, {}); toast(j.status === "fetching" ? `Stopped. The model keeps downloading on ${j.worker}, so it's there next time` : "Stopped on " + j.worker); } catch (e) { toast(e.message, true); } pollRemote(); };
      b.append(el);
    });
  }
}
/* ------------------------------------------------------------ other GPU machines (remote workers) */
const remoteActive = j => ["fetching", "queued", "running", "downloading", "cancelling"].includes(j.status);
function remoteName(j) { const a = S.apps.find(x => x.id === j.app); return a ? appShort(a) : (j.label || "Job"); }
function remoteState(j) {
  return { fetching: j.step || "Downloading the model", queued: "Waiting", running: j.step ? `${Math.round(j.progress * 100)}% · ${j.step}` : "Rendering", downloading: "Copying to this PC",
           cancelling: "Stopping…", done: "Done", failed: "Failed: " + (j.error || "").slice(0, 140), cancelled: "Stopped" + (j.note ? " · " + j.note : "") }[j.status] || j.status;
}
/* a GPU machine's memory as it really is: what a job holds now, what's only kept loaded from the last job (freed when
   the next one needs it), out of what the card can use. Older workers report only the card's size. */
function cardMem(info) {
  const gb = n => (n / 1e9).toFixed(1);
  if (!info) return "";
  if (info.vram_usable) return `${gb(info.vram_in_use || 0)}/${gb(info.vram_usable)} GB in use` + (info.vram_cached ? ` · ${gb(info.vram_cached)} GB kept from the last job` : "");
  const t = info.device && info.device.vram_total;
  return t ? `${(t / 1e9).toFixed(0)} GB card` : "";
}
function runTarget() { return S.workers.length > 1 && (S.target === "auto" || S.workers.some(w => w.label === S.target)) ? S.target : "local"; }
function runOptions() {
  const t = runTarget();
  return [["local", "This PC"], ["auto", "Any free GPU"], ...S.workers.slice(1).map(w => [w.label, w.label + (w.online ? "" : " (offline)")])]
    .map(([v, l]) => `<option value="${esc(v)}" ${v === t ? "selected" : ""}>${esc(l)}</option>`).join("");
}
async function loadWorkers() {
  try { S.workers = await api("/manny/api/workers"); } catch { S.workers = []; }
  document.querySelectorAll(".go").forEach(placeRunOn);   // the form may have been drawn before the list arrived
}
/* the same choice inside a dialog (the video tools): changing it changes the remembered Run on, so what you see is
   where it goes; note shows while a GPU machine is picked */
function runOnSelect(note) {
  if (S.workers.length < 2) return h(`<span></span>`);
  const el = h(`<label class="runon-row">Run on <select class="runon" title="Which GPU renders this">${runOptions()}</select><small class="runon-note"></small></label>`);
  const sel = $("select", el), nt = $(".runon-note", el);
  const show = () => { nt.textContent = note && sel.value !== "local" && sel.value !== "auto" ? note : ""; };
  sel.onchange = () => { S.target = sel.value; saveState(); show(); };
  show();
  return el;
}
/* the "Run on" menu next to Generate, only once another GPU machine is set up in Settings */
function placeRunOn(go) {
  const old = $("select.runon", go);
  if (old) old.remove();
  if (S.workers.length < 2) return;
  const sel = h(`<select class="runon" title="Which GPU renders this">${runOptions()}</select>`);
  sel.onchange = () => { S.target = sel.value; saveState(); };
  $(".stepper", go).after(sel);
}
async function pollRemote() {
  if (S.workers.length < 2 && !S.remote.some(remoteActive)) return;
  try { S.remote = await api("/manny/api/remote_jobs"); } catch { return; }
  drawQueueCard();
  if (!$("#drawer").classList.contains("hidden")) drawDrawer();
}
/* move waiting jobs from this PC to a Mac GPU ("split" = share them out between the online Macs) */
const macWorkers = () => S.workers.slice(1).filter(w => w.online);
function moveOptions(withSplit) {
  const ms = macWorkers();
  return `<option value="">Send to…</option>` + (withSplit && ms.length > 1 ? `<option value="__split">All Macs (shared out)</option>` : "") +
    ms.map(w => `<option value="${esc(w.label)}">${esc(prettyWorker(w.label))}</option>`).join("");
}
async function moveJobs(ids, target) {
  const ms = macWorkers().map(w => w.label);
  const groups = {};
  ids.forEach((id, i) => { const t = target === "__split" ? ms[i % ms.length] : target; (groups[t] = groups[t] || []).push(id); });
  let moved = 0, dl = 0; const kept = [];
  toast(`Sending ${ids.length} job${ids.length > 1 ? "s" : ""}…`);
  for (const [t, list] of Object.entries(groups)) {
    try {
      const r = await post("/manny/api/queue/move", { prompt_ids: list, target: t });
      r.moved.forEach(m => { if (S.mine[m.from]) { S.mine[m.to] = S.mine[m.from]; delete S.mine[m.from]; } moved++; if (m.fetching && m.fetching.length) dl++; });
      kept.push(...r.kept);
    } catch (e) { kept.push({ reason: e.message }); }
  }
  if (moved) toast(`Moved ${moved} to the Mac` + (dl ? ` · ${dl} will download a model first` : ""));
  if (kept.length) toast(`${kept.length} stayed on this PC: ` + [...new Set(kept.map(k => k.reason))].join(" · ").slice(0, 300), true);
  setTimeout(() => { pollQueue(); pollRemote(); }, 600);
}
function qItem(it, running) {
  const canMove = !running && macWorkers().length && !["studio-videotools", "studio-sound", "studio-ai"].includes((it[3] || {}).client_id);
  const el = h(`<div class="qitem ${running ? "run" : ""}"><div class="row"><b>${esc(appNameOf(it))}</b>${canMove ? `<select class="qmove" title="Render this on another GPU">${moveOptions(false)}</select>` : ""}<button class="icon" title="${running ? "Stop" : "Remove"}">✕</button></div>
     <small>${running ? "Rendering · " + Math.round(S.progress * 100) + "%" : "Waiting · #" + it[0]}</small>${running ? `<div class="bar" style="margin-top:8px"><div style="width:${Math.round(S.progress * 100)}%"></div></div>` : ""}</div>`);
  const mv = $(".qmove", el);
  if (mv) mv.onchange = () => { if (mv.value) { mv.disabled = true; moveJobs([it[1]], mv.value); } };
  $("button", el).onclick = async () => {
    if (running) await post("/interrupt", { prompt_id: it[1] }); else await post("/queue", { delete: [it[1]] });
    setTimeout(pollQueue, 300);
  };
  return el;
}
$("#queue-card").onclick = () => { $("#drawer").classList.remove("hidden"); loadSchedule(); drawDrawer(); };
$("#mq").onclick = () => { $("#drawer").classList.remove("hidden"); drawDrawer(); };
$("#drawer [data-close]").onclick = () => $("#drawer").classList.add("hidden");
$("#q-stop").onclick = async () => { await post("/interrupt", {}); setTimeout(pollQueue, 300); };
$("#q-clear").onclick = async () => { await post("/queue", { clear: true }); setTimeout(pollQueue, 300); toast("Cleared the waiting jobs"); };
document.addEventListener("keydown", e => { if (e.key === "Escape" && !lbKeys) { $("#modal").classList.add("hidden"); $("#drawer").classList.add("hidden"); } });

let ws, nodeTitles = {};
function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?clientId=${clientId}`);
  ws.binaryType = "arraybuffer";
  ws.onmessage = ev => {
    if (typeof ev.data !== "string") { livePreview(ev.data); return; }
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    const d = m.data || {};
    switch (m.type) {
      case "status": pollQueue(); break;
      case "execution_start": S.running = d.prompt_id; S.progress = 0; S.progressNode = "Starting"; drawQueueCard(); break;
      case "executing": if (d.node == null) { S.progressNode = ""; pollQueue(); } else { S.progressNode = "Working"; drawQueueCard(); } break;
      case "progress": if (!(S.srvAt && Date.now() - S.srvAt < 10000)) { S.progress = d.max ? d.value / d.max : 0; S.progressNode = `Step ${d.value}/${d.max}`; } drawQueueCard(); { const w8 = S.compare && S.compare.items.find(x => x.prompt_id === S.running && !x.url); if (w8 && location.hash.startsWith("#/create")) { const c = $(`.cmpcell[data-pid="${w8.prompt_id}"] .cmpwait`); if (c) c.textContent = "Rendering " + Math.round(S.progress * 100) + "%"; } } { const tk = location.hash.startsWith("#/storyboard") && $(`.take[data-pid="${S.running}"] span`); if (tk) tk.textContent = Math.round(S.progress * 100) + "%"; } if (!$("#drawer").classList.contains("hidden")) drawDrawer(); break;
      case "executed": onOutput(d); break;
      case "execution_success": if (d.worker) { pollRemote(); S.gallery = []; break; } S.progress = 1; drawQueueCard(); S.gallery = []; break;
      case "execution_error": toast((d.worker ? `Render failed on ${d.worker}: ` : "Render failed: ") + (d.exception_message || "error").slice(0, 300), true); if (d.worker) pollRemote(); else pollQueue(); break;
      case "execution_interrupted": toast("Stopped"); pollQueue(); break;
    }
  };
  ws.onclose = () => setTimeout(connect, 2000);
}
function livePreview(buf) {
  try {
    const dv = new DataView(buf); if (dv.getUint32(0) !== 1) return;
    const app = curApp(); if (!S.running || !S.mine[S.running] || S.mine[S.running].app !== app.id || S.mine[S.running].hidden) return;
    const blob = new Blob([buf.slice(8)], { type: dv.getUint32(4) === 2 ? "image/png" : "image/jpeg" });
    const st = $(".stage-main"); if (!st) return;
    let img = $("img.live", st);
    if (!img) { st.querySelectorAll(":scope > img, :scope > video, .stage-ph").forEach(x => x.remove()); img = h(`<img class="live">`); st.prepend(img); }
    const old = img.src; img.src = URL.createObjectURL(blob); if (old.startsWith("blob:")) URL.revokeObjectURL(old);
  } catch { }
}
function onOutput(d) {
  const out = d.output || {};
  const files = [...(out.images || []), ...(out.gifs || []), ...(out.videos || [])].filter(f => f.type === "output");
  if (!files.length) return;
  const mine = S.mine[d.prompt_id];
  const f = files[0];
  const isVid = /\.(mp4|webm|mov)$/i.test(f.filename) || (out.animated && out.animated[0]);
  if (mine && mine.hidden) { toast("Saved privately"); S.gallery = []; return; }
  const res = { url: fileUrl(f, mine && mine.private), kind: isVid ? "video" : "image" };
  const appId = mine ? mine.app : null;
  if (S.compare) { const ci = S.compare.items.find(x => x.prompt_id === d.prompt_id); if (ci) ci.url = res.url; }
  if (mine && mine.board) shotDone(mine.board, mine.shot, f, d.prompt_id);
  if (appId && !(mine && (mine.compare || mine.board))) { S.stage[(mine && mine.private ? "P|" : "") + appId] = res; saveState(); }
  S.gallery = [];
  const app = curApp();
  if (location.hash.startsWith("#/create") && app && app.id === appId) renderStage($(".stage"), app);
  if (location.hash.startsWith("#/storyboard") && S.sbDraw) S.sbDraw();
  if (mine) toast((isVid ? "Shot" : "Picture") + " ready");
}

async function sysMon() {
  if (document.hidden || !$("#mon") || getComputedStyle($("#mon")).display === "none") return;
  try {
    const m = await api("/manny/api/sysmon");
    const set = (k, pct, txt) => { const b = $("#m-" + k), t = $("#t-" + k); if (!b) return; b.style.width = (pct == null ? 0 : Math.max(2, Math.min(100, pct))) + "%"; b.className = pct > 90 ? "hot" : pct > 70 ? "warm" : ""; t.textContent = txt; };
    set("cpu", m.cpu, m.cpu == null ? "–" : Math.round(m.cpu) + "%");
    set("gpu", m.gpu, m.gpu == null ? "–" : Math.round(m.gpu) + "%");
    if (m.vram) set("vram", m.vram.used / m.vram.total * 100, `${(m.vram.used / 1e9).toFixed(1)}/${(m.vram.total / 1e9).toFixed(0)} GB`);
    if (m.ram) set("ram", m.ram.used / m.ram.total * 100, `${(m.ram.used / 1e9).toFixed(0)}/${(m.ram.total / 1e9).toFixed(0)} GB`);
    let st = $("#m-stall");
    const j = m.job;
    if (j && j.stalled && (m.gpu == null || m.gpu < 20)) {
      if (!st) { st = h(`<div id="m-stall" class="mstall"></div>`); $("#mon").append(st); }
      st.innerHTML = `⚠ No progress for ${Math.round(j.idle_s / 60)} min${j.node_type ? " in " + esc(j.node_type) : ""}. <button class="ghost sm">Stop it</button>`;
      $("button", st).onclick = async () => { await post("/interrupt", { prompt_id: j.prompt_id }); toast("Asked ComfyUI to stop. If it doesn't within a minute, restart ComfyUI."); };
    } else if (st) st.remove();
    $("#mon").classList.toggle("awake", !!m.awake);
    let sl = $("#m-slow");
    if (j && j.stalled && m.gpu >= 20) {
      if (!sl) { sl = h(`<div id="m-slow" class="mslow"></div>`); $("#mon").append(sl); }
      sl.textContent = `Slow step: ${Math.round(j.idle_s / 60)} min on step ${j.step || "?"}/${j.max || "?"}, GPU still working`;
    } else if (sl) sl.remove();
    $("#mon").title = (m.awake ? "Keeping the PC awake while renders run · " : "") + (j && j.max ? `Step ${j.step}/${j.max} · ` : "") + "GPU load from Windows" + (m.gpu_engine ? " (" + m.gpu_engine + " engine)" : "") + " · VRAM is the shared graphics memory ComfyUI can use";
  } catch { }
}
/* which machines are rendering: this PC and the GPU workers (Mac Vegas), under the resource bars */
const prettyWorker = l => l === "local" ? "This PC" : String(l).replace(/^mac-vega-(\w+)$/i, (_, x) => "Mac · Vega " + x.toUpperCase());
async function machinesMon() {
  const mon = $("#mon");
  if (document.hidden || !mon || getComputedStyle(mon).display === "none") return;
  try {
    const [ws, rj, q, m] = await Promise.all([api("/manny/api/workers"), api("/manny/api/remote_jobs"), api("/queue"), api("/manny/api/sysmon")]);
    let box = $("#m-mach");
    if (ws.length < 2) { if (box) box.remove(); return; }       // only this PC: nothing to add
    if (!box) { box = h(`<div id="m-mach" class="mmach"><div class="mmh">MACHINES</div><div class="mml"></div></div>`); mon.append(box); }
    const rows = ws.map(w => {
      let state = "idle", txt = "Idle";
      if (w.label === "local") {
        const n = (q.queue_running || []).length + (q.queue_pending || []).length;
        const j = m.job;
        if (n) { state = "busy"; txt = (j && j.max ? `Rendering · step ${j.step}/${j.max}` : "Rendering") + (n > 1 ? ` · ${n - 1} waiting` : ""); }
      } else if (!w.online) { state = "off"; txt = "Offline"; }
      else {
        const mine = rj.filter(x => x.worker === w.label && ["fetching", "queued", "running", "downloading", "cancelling"].includes(x.status));
        const run = mine.find(x => x.status === "running"), fet = mine.find(x => x.status === "fetching"), dl = mine.find(x => x.status === "downloading");
        const n = (w.info && w.info.queue_remaining) || 0;
        if (run) { state = "busy"; txt = "Rendering for Studio" + (run.step ? " · " + run.step.replace(/^Step /, "step ") : ""); }
        else if (fet) { state = "busy"; txt = (fet.step || "Downloading a model").replace(/^Downloading model .*? on [^:]+:?/, "Downloading model") ; }
        else if (dl) { state = "busy"; txt = "Sending the result back"; }
        else if (n) { state = "busy"; txt = `Busy · ${n} job${n > 1 ? "s" : ""} (not from Studio)`; }
        if (n && (run || fet) && n > 1) txt += ` · ${n - 1} waiting`;
        else if (mine.length && !run && !fet && !dl) { state = "busy"; txt = `Waiting · ${n} ahead`; }
        const te = w.info && w.info.te_for;
        if (te && state !== "busy") { state = "busy"; txt = `Encoding a prompt for ${prettyWorker(te)}`; }
        else if (te) txt += ` · also encoding for ${prettyWorker(te)}`;
      }
      const dev = w.label === "local" ? "" : ((w.info && w.info.device && w.info.device.name) || "");
      const mem = cardMem(w.info);
      return `<div class="mmr ${state}" title="${esc(w.label + (dev ? " · " + dev : "") + (mem ? " · " + mem : "") + (w.error ? " · " + w.error : ""))}"><i></i><span>${esc(prettyWorker(w.label))}</span><b>${esc(txt)}</b></div>`;
    }).join("");
    $(".mml", box).innerHTML = rows;
  } catch { }
}
async function sysStats() {
  try {
    const s = await api("/system_stats"); const dv = (s.devices || [])[0];
    $("#sys-mem").textContent = dv ? `${dv.name.replace(/^xpu:\d+\s*/, "").slice(0, 22)} · ${((dv.vram_total - dv.vram_free) / 1e9).toFixed(0)}/${(dv.vram_total / 1e9).toFixed(0)} GB` : "ready";
  } catch { $("#sys-mem").textContent = "offline"; }
}

/* ------------------------------------------------------------ INCOGNITO */
function setIncog(on, quiet) {
  S.incog = !!on; store.set("incog", S.incog);
  document.body.classList.toggle("incog", S.incog);
  $$(".incog-btn").forEach(b => b.classList.toggle("on", S.incog));
  S.gallery = []; S.compare = null;
  if (!quiet) { loadChars().then(() => route()); if (S.incog) setTimeout(checkUnfinished, 800); }
}
function migrateLocalValues() {
  // Studio's earlier settings belonged to what is now the private space.
  if (store.get("valuesMigrated", false)) return;
  for (const k of Object.keys(S.values)) {
    if (k.startsWith("P|")) continue;
    const v = S.values[k];
    for (const f of Object.keys(v)) if (typeof v[f] === "string" && v[f].startsWith("manny/")) v[f] = "manny_private/" + v[f].slice(6);
    S.values["P|" + k] = v; delete S.values[k];
  }
  for (const k of Object.keys(S.stage)) if (!k.startsWith("P|")) delete S.stage[k];
  store.set("valuesMigrated", true); saveState();
}
async function toggleIncog() {
  if (S.incog) { await post("/manny/api/private/lock", {}); setIncog(false); return; }
  const st = await api("/manny/api/private/status");
  if (st.unlocked) { setIncog(true); return; }
  if (!st.has_password) return toast(st.local ? "Set a password first: sidebar › Password" : "Set a password on the PC first (/manny/password)", true);
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog unlock"><div class="row" style="justify-content:space-between"><h2>Private</h2><button class="icon" data-x>✕</button></div>
    <p class="muted">Password</p>
    <input type="password" id="pw" placeholder="Password" autocomplete="current-password">
    <div class="err hidden" id="pwerr"></div>
    <div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" id="go">Unlock</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(b => b.onclick = close);
  const go = async () => {
    try { await post("/manny/api/private/unlock", { password: $("#pw", d).value }); close(); setIncog(true); }
    catch (e) { const er = $("#pwerr", d); er.textContent = (e.data && e.data.error) || "Wrong password."; er.classList.remove("hidden"); }
  };
  $("#go", d).onclick = go; $("#pw", d).onkeydown = e => { if (e.key === "Enter") go(); };
  setTimeout(() => $("#pw", d).focus(), 50);
}
$$(".incog-btn").forEach(b => b.onclick = toggleIncog);
let idleT; const IDLE = 20 * 60 * 1000;
const bump = () => { clearTimeout(idleT); idleT = setTimeout(async () => { if (S.incog) { try { await post("/manny/api/private/lock", {}); } catch { } setIncog(false);  } }, IDLE); };
["click", "keydown", "touchstart", "scroll"].forEach(ev => document.addEventListener(ev, bump, { passive: true })); bump();

/* ------------------------------------------------------------ boot */

/* ---- unfinished queue from before a shutdown or restart */
async function checkUnfinished() {
  let r; try { r = await api("/manny/api/queue/unfinished"); } catch { return; }
  const items = r.items || []; if (!items.length || $("#modal .dialog")) return;
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const sel = new Set(items.map(i => i.prompt_id));
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>⏯ Continue the unfinished queue?</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">ComfyUI stopped with ${items.length} render${items.length > 1 ? "s" : ""} not done. Studio kept a copy. Untick any you don't want, then continue.</p>
    <div class="uq">${items.map(i => `<label class="row" style="gap:8px;align-items:flex-start;margin:6px 0"><input type="checkbox" checked data-id="${esc(i.prompt_id)}">
      <span><b>${esc(i.label || "Render")}</b>${i.private ? ' <small>🕶 Incognito</small>' : ""}${i.running ? ' <small>— was rendering when it stopped</small>' : ""}
      ${i.note ? `<br><small class="hint">${esc(i.note)}</small>` : ""}</span></label>`).join("")}</div>
    <p class="hint" style="margin:0">If the PC froze or crashed during the one marked "was rendering", untick it.</p>
    <div class="foot"><button class="ghost" data-later>Ask me later</button><button class="ghost" data-drop>Discard all</button><button class="btn primary" data-go>Continue</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x],[data-later]", d).forEach(x => x.onclick = close);
  $$("input[data-id]", d).forEach(c => c.onchange = () => c.checked ? sel.add(c.dataset.id) : sel.delete(c.dataset.id));
  $("[data-drop]", d).onclick = async () => {
    if (!confirm("Forget all " + items.length + " unfinished renders?")) return;
    close(); try { await api("/manny/api/queue/unfinished/discard", { method: "POST", body: JSON.stringify({}) }); toast("Discarded"); } catch (e) { toast(e.message, true); }
  };
  $("[data-go]", d).onclick = async () => {
    const go = [...sel], drop = items.map(i => i.prompt_id).filter(id => !sel.has(id));
    close();
    try {
      if (drop.length) await api("/manny/api/queue/unfinished/discard", { method: "POST", body: JSON.stringify({ ids: drop }) });
      if (!go.length) return toast("Nothing continued");
      const r2 = await api("/manny/api/queue/unfinished/resume", { method: "POST", body: JSON.stringify({ ids: go }) });
      toast(`Queued ${r2.sent} render${r2.sent === 1 ? "" : "s"} again` + (r2.failed?.length ? ` — ${r2.failed.length} couldn't be sent` : ""), !!r2.failed?.length);
      if (r2.failed?.length) console.warn("unfinished resume", r2.failed);
      pollQueue();
    } catch (e) { toast(e.message, true); }
  };
}

(async function boot() {
  try { S.apps = await api("/manny/api/apps"); } catch (e) { toast("Couldn't load apps: " + e.message, true); }
  S.apps = S.apps.filter(a => !a.error);
  const ord = a => { const k = APP_ORDER.indexOf(appShort(a).toLowerCase()); return k < 0 ? 50 : k; };
  S.apps.sort((a, b) => ord(a) - ord(b) || appShort(a).localeCompare(appShort(b)));
  if (S.appIdx >= S.apps.length) S.appIdx = 0;
  try { const a = await api("/manny/api/private/status"); S.pw = a; setIncog(a.unlocked && store.get("incog", true), true); } catch { }
  migrateLocalValues();
  await loadChars();
  connect(); pollQueue(); sysStats(); setInterval(sysStats, 15000); setInterval(pollQueue, 5000); sysMon(); setInterval(sysMon, 3000); machinesMon(); setInterval(machinesMon, 8000);
  loadWorkers().then(pollRemote); setInterval(pollRemote, 3000); setInterval(loadWorkers, 60000);
  setInterval(serverProgress, 3000);
  loadSchedule(); setInterval(loadSchedule, 30000);
  route();
  setTimeout(checkUnfinished, 1500);
})();
})();
