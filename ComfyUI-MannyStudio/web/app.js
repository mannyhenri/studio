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
};
const saveState = () => { store.set("values", S.values); store.set("randomSeed", S.randomSeed); store.set("count", S.count); store.set("char", S.char); store.set("appIdx", S.appIdx); store.set("stage", S.stage); };

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
const pages = { home: pageHome, create: pageCreate, gallery: pageGallery, characters: pageCharacters, storyboard: pageStoryboard, loras: pageLoras, reel: pageReel };
function route() {
  const [, page = "home", arg] = location.hash.split("/");
  $$("#nav a, #bnav a").forEach(a => a.classList.toggle("active", a.dataset.page === page));
  const main = $("#main"); main.innerHTML = "";
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
  S.apps.forEach((a, k) => { if (!isTool(a)) tabs.append(mkTab(a, k)); });
  const tools = S.apps.map((a, k) => [a, k]).filter(([a]) => isTool(a));
  if (tools.length) { tabs.append(h(`<div class="tabsep">Tools</div>`)); tools.forEach(([a, k]) => tabs.append(mkTab(a, k, true))); }
  renderForm($(".form", p), app);
  renderStage($(".stage", p), app);
}

function renderForm(form, app) {
  const v = valuesFor(app), L = layout(app);
  form.innerHTML = "";
  const set = (key, val) => { v[key] = val; saveState(); };

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
    const ms = h(`<div class="fsec"><label class="lbl">${esc(L.model.label)}</label><div class="models"></div></div>`);
    const box = $(".models", ms);
    L.model.options.forEach(o => {
      const [title, sub] = splitOption(o);
      const el = h(`<div class="model ${v[L.model.key] === o ? "on" : ""}"><div class="radio"></div><div><b>${esc(title)}</b>${sub ? `<span>${esc(sub)}</span>` : ""}</div></div>`);
      el.onclick = () => { set(L.model.key, o); if ((S.loras[mkey(app)] || []).length) return renderForm(form, app); $$(".model", box).forEach(x => x.classList.remove("on")); el.classList.add("on"); };
      box.append(el);
    });
    if (app.kind === "image" && !isTool(app)) {
      const cmp = h(`<button class="ghost compare-btn">⊞ Compare all ${L.model.options.length} models with this prompt</button>`);
      cmp.onclick = () => compareModels(app);
      ms.append(cmp);
    }
    form.append(ms);
  }
  if (!isTool(app) && L.model) form.append(loraSection(app, form));

  // settings
  if (L.settings.length) {
    const ss = h(`<div class="fsec set"></div>`);
    L.settings.forEach(i => ss.append(control(app, i, v, set)));
    form.append(ss);
  }

  // references
  if (L.refs.length) {
    const rs = h(`<div class="fsec"><label class="lbl">${app.kind === "video" ? "References · images & voices" : "Reference"}</label><div class="refs"></div></div>`);
    const box = $(".refs", rs);
    L.refs.forEach(r => box.append(refTile(app, r, v, set)));
    if (L.refs.strength) rs.append(control(app, L.refs.strength, v, set));
    form.append(rs);
  }

  // advanced
  const adv = [...(L.negative ? [L.negative] : []), ...L.adv];
  if (adv.length) {
    const d = h(`<details class="adv"><summary>More options</summary><div class="fsec"></div></details>`);
    adv.forEach(i => $(".fsec", d).append(control(app, i, v, set)));
    form.append(d);
  }

  // generate bar
  const go = h(`<div class="go">
      <div class="stepper" title="How many"><button data-d="-1">−</button><span>${S.count}</span><button data-d="1">+</button></div>
      <button class="btn primary" id="gen">Generate ${S.count > 1 ? S.count + " " + (app.kind === "video" ? "shots" : "pictures") : ""}</button>
    </div>`);
  $$(".stepper button", go).forEach(b => b.onclick = () => { S.count = Math.max(1, Math.min(8, S.count + +b.dataset.d)); saveState(); renderForm(form, app); });
  $("#gen", go).onclick = () => generate(app);
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
      const res = await post("/manny/api/run", { app: app.id, values: vals, count: 1, client_id: clientId, private: S.incog });
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

async function generate(app) {
  const btn = $("#gen"); if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }
  try {
    const seedKeys = app.inputs.filter(i => i.kind === "seed" && (S.randomSeed[app.id] || {})[i.key] !== false).map(i => i.key);
    const vals = buildValues(app);
    const loras = loraPayload(app, vals);
    const unc = app.inputs.some(i => i.kind === "toggle" && /uncensored/i.test(i.label) && vals[i.key]);
    const priv = S.incog || unc;
    const res = await post("/manny/api/run", { app: app.id, values: vals, count: S.count, client_id: clientId, random_seed_keys: seedKeys, private: priv, loras });
    res.queued.forEach(q => S.mine[q.prompt_id] = { app: app.id, at: Date.now(), private: priv, hidden: priv && !S.incog });
    if (priv && !S.incog) toast("Saved privately.");
    toast(`Queued ${res.queued.length} ${app.kind === "video" ? "shot" : "picture"}${res.queued.length > 1 ? "s" : ""}`);
    if (window.matchMedia("(max-width: 760px)").matches) { const st = $(".stage"); if (st) st.scrollIntoView({ behavior: "smooth", block: "start" }); }
    pollQueue();
  } catch (e) {
    const d = e.data && e.data.error;
    const msg = d && d.error ? (d.error.message + (d.node_errors ? " — " + Object.values(d.node_errors).map(n => n.errors.map(x => x.message + (x.details ? ": " + x.details : "")).join("; ")).join(" | ") : "")) : e.message;
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
  const el = h(`<div class="tile">${it.kind === "video" ? `<span class="badge v">Video</span>` : ""}${pv}<button class="star ${it.fav ? "on" : ""}" title="Favorite">${it.fav ? "★" : "☆"}</button><div class="cap"><b>${esc(modelOf(it.filename))}</b></div></div>`);
  $(".star", el).onclick = async e => { e.stopPropagation(); await toggleFav(it); const s = $(".star", el); s.classList.toggle("on", it.fav); s.textContent = it.fav ? "★" : "☆"; };
  if (it.kind === "video") { const vd = $("video", el); el.onmouseenter = () => vd.play().catch(() => {}); el.onmouseleave = () => vd.pause(); }
  el.onclick = () => lightbox(it, list);
  return el;
}
async function pageGallery(main) {
  const p = h(`<div class="page"><div class="eyebrow">Library</div><h1>Gallery</h1>
    <div class="filters"><input type="text" id="q" placeholder="Search prompts…" class="search"><div class="chips" id="kind"></div><select id="model"><option value="">Every model</option></select><span class="hint" id="cnt"></span></div>
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
  function draw() {
    const g = $("#g", p); g.innerHTML = "";
    const words = q.split(/\s+/).filter(Boolean);
    const list = items.filter(i => (!kind || (kind === "fav" ? i.fav : i.kind === kind)) && (!model || modelOf(i.filename) === model)
      && words.every(w => ((i.prompt || "") + " " + i.filename).toLowerCase().includes(w)));
    $("#cnt", p).textContent = list.length + " items";
    if (!list.length) g.append(h(`<div class="empty">Nothing here yet.</div>`));
    const shown = list.slice(0, 300); shown.forEach(it => g.append(tile(it, shown)));
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
       <button class="btn" data-to="remove background:image">✂ Remove background</button>
       <button class="btn" data-to="image:image">▣ Use as reference</button>` : `
       <button class="btn" data-to="smooth motion:video">≋ Smooth motion</button>
       <button class="btn" data-to="upscale video:video">⤢ Upscale to 1080p</button>`}
     </div>
     <button class="ghost" data-board>＋ Add to storyboard${cur.kind === "image" ? " (as first frame)" : ""}</button>
     <div class="row"><a class="ghost" href="${url}" download="${esc(cur.filename)}">Download</a><button class="ghost danger" data-trash>Move to trash</button></div>
     <p class="hint">← → to browse · Esc to close. Trash is a folder (_trash) inside Pictures › Comfyui, so nothing is lost.</p>`;
    $("[data-fav]", side).onclick = async () => { await toggleFav(cur); const s = $("[data-fav]", side); s.textContent = cur.fav ? "★" : "☆"; s.classList.toggle("on", cur.fav); m.dataset.dirty = "1"; };
    let meta = null;
    api(`/manny/api/meta?filename=${encodeURIComponent(cur.filename)}&subfolder=${encodeURIComponent(cur.subfolder || "")}${cur.private ? "&private=1" : ""}`).then(md => {
      if (list[i] !== cur) return; meta = md;
      const pt = $(".ptext", side);
      if (md.prompt) { pt.classList.remove("muted"); pt.textContent = md.prompt; const cp = h(`<button class="ghost copy">Copy</button>`); cp.onclick = () => { navigator.clipboard?.writeText(md.prompt); toast("Prompt copied"); }; $(".promptbox", side).append(cp); }
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
  let first = true;
  L.refs.forEach(r => {
    if (r.media.kind === "image" && first) { first = false; if (s.image) { vals[r.media.key] = s.image; if (r.toggle) vals[r.toggle.key] = true; } else if (r.toggle) vals[r.toggle.key] = false; }
    else if (r.toggle) vals[r.toggle.key] = false;
  });
  const K = videoKeys(app);
  if (K.quality && o.q) { const want = K.quality.options.find(x => o.q === "final" ? /final|full/i.test(x) : /draft|turbo/i.test(x)); if (want) vals[K.quality.key] = want; }
  let seedKeys = app.inputs.filter(i => i.kind === "seed").map(i => i.key);
  if (o.seed != null && K.seed) { vals[K.seed.key] = o.seed; seedKeys = seedKeys.filter(k => k !== K.seed.key); }
  try {
    const loras = loraPayload(app, vals);
    const res = await post("/manny/api/run", { app: app.id, values: vals, count: o.count || 1, client_id: clientId, random_seed_keys: seedKeys, private: !!b.private, loras });
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

/* ---- finish a picked take: upscale, smoother motion, sound */
function finishDialog(b, s, n, after) {
  const t = s.takes[s.pick]; if (!t || !t.output) return;
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const st = store.get("finish", { upscale: true, fps: "keep", sound: "keep" });
  let audio = "";
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>Finish shot ${n + 1}</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">Uses take ${s.pick + 1}${t.q === "final" ? " (final quality)" : t.q === "draft" ? ' (draft quality: render it in final quality first for the best result)' : ""}. Runs in the queue; you can leave this page.</p>
    <label class="tog"><input type="checkbox" id="f-up" ${st.upscale ? "checked" : ""}><span><b>Upscale to 1080p</b><small>Upscale video app. The slow step: several minutes per shot.</small></span></label>
    <div><label class="lbl">Frame rate</label><div class="chips" id="f-fps"><div class="chip" data-v="keep">Keep</div><div class="chip" data-v="48">48 fps</div><div class="chip" data-v="60">60 fps</div></div></div>
    <div><label class="lbl">Sound</label><div class="chips" id="f-snd"><div class="chip" data-v="keep">Keep</div><div class="chip" data-v="mute">Mute</div><div class="chip" data-v="replace">Replace…</div></div></div>
    <div class="ref audio hidden" id="f-aud" style="aspect-ratio:auto;min-height:70px"><span class="plus">+</span><span class="rl">Drop a sound or song (wav, mp3). It's cut to the shot's length with a short fade.</span><input type="file" hidden accept="audio/*"></div>
    <div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" data-go>Finish shot</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  const chips = (id, key) => { $$(`#${id} .chip`, d).forEach(c => { c.classList.toggle("on", c.dataset.v === st[key]); c.onclick = () => { st[key] = c.dataset.v; chips(id, key); $("#f-aud", d).classList.toggle("hidden", st.sound !== "replace"); }; }); };
  chips("f-fps", "fps"); chips("f-snd", "sound"); $("#f-aud", d).classList.toggle("hidden", st.sound !== "replace");
  const au = $("#f-aud", d), fi = $("input", au);
  const take = async f => { audio = await upload(f); au.classList.add("on"); $(".plus", au).className = "wave"; $(".wave", au).textContent = "♪"; $(".rl", au).textContent = f.name; };
  au.onclick = () => fi.click(); fi.onchange = () => fi.files[0] && take(fi.files[0]);
  au.ondragover = e => e.preventDefault(); au.ondrop = e => { e.preventDefault(); e.dataTransfer.files[0] && take(e.dataTransfer.files[0]); };
  $("[data-go]", d).onclick = async () => {
    st.upscale = $("#f-up", d).checked; store.set("finish", { ...st });
    if (st.sound === "replace" && !audio) return toast("Add the sound first", true);
    if (!st.upscale && st.fps === "keep" && st.sound === "keep") return toast("Pick at least one thing to change", true);
    try {
      const r = await post("/manny/api/finish", { ...t.output, private: !!b.private, upscale: st.upscale, fps: st.fps, sound: st.sound, audio, name: `${b.name} shot ${n + 1}` });
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
      const el = h(`<div class="shot"><div class="shead"><b>SHOT ${n + 1}</b><div class="row"><button class="icon" data-up title="Move up">↑</button><button class="icon" data-dn title="Move down">↓</button><button class="icon" data-rm title="Remove">✕</button></div></div>
        <div class="sbody"><div class="ref ${s.image ? "on" : ""} sframe">${s.image ? `<img src="${inputUrl(s.image)}">` : `<span class="plus">+</span>`}<span class="rl">${s.image ? (s.cont ? "Last frame of shot " + s.cont : "First frame") : "First frame (optional)"}</span><button class="x" title="Remove">✕</button><input type="file" hidden accept="image/*"></div>
        <div class="scol"><textarea placeholder="What happens in this shot: who, action, place, mood, dialogue…">${esc(s.prompt)}</textarea>
          <div class="row"><select class="scam"><option value="">Camera: model decides</option>${CAMERA.map(([nm, w2]) => `<option value="${esc(w2)}" ${s.camera === w2 ? "selected" : ""}>Camera: ${esc(nm)}</option>`).join("")}</select><button class="ghost" data-pw>✎ Prompt writer</button></div>
          <details class="trim" ${s.trimIn || s.trimOut || s.caption ? "open" : ""}><summary>✂ Trim &amp; caption</summary><div class="row"><label>From <input type="number" min="0" step="0.1" data-tin value="${s.trimIn || ""}" placeholder="0"> s</label><label>to <input type="number" min="0" step="0.1" data-tout value="${s.trimOut || ""}" placeholder="end"> s</label></div><input type="text" data-cap placeholder="Caption on screen (optional)" value="${esc(s.caption || "")}"></details></div>
        <div class="sout"><div class="smain">${main ? `<video src="${fileUrl(main, b.private)}" poster="${thumbUrl({ ...main, private: b.private }, 640)}" muted loop playsinline preload="none"></video>` : `<div class="swait">${pending ? (tstat(pending) === "Waiting" ? "Waiting in queue" : "Rendering " + tstat(pending)) : "Not rendered yet"}</div>`}${fin}</div>
          <div class="takes">${s.takes.map((t, k) => `<div class="take ${k === s.pick ? "on" : ""} ${t.output ? "" : "wait"}" data-k="${k}" data-pid="${t.pid || ""}" title="${t.q === "final" ? "Final quality" : t.q === "draft" ? "Draft" : "Take"} ${k + 1}${t.seed != null ? " · seed " + t.seed : ""}">${t.output ? `<img src="${thumbUrl({ ...t.output, private: b.private }, 640)}" loading="lazy"><video muted loop playsinline preload="none" data-src="${fileUrl(t.output, b.private)}"></video>` : `<span>${tstat(t) || "…"}</span>`}<i>${k + 1}${t.q === "final" ? "F" : ""}</i>${t.output ? `<button class="tx" data-tx="${k}" title="Remove take">✕</button>` : ""}</div>`).join("")}</div>
          <div class="sacts">
            <button class="ghost" data-go>${s.takes.length ? "＋ " + b.takes + " more take" + (b.takes > 1 ? "s" : "") : "Draft " + b.takes + " take" + (b.takes > 1 ? "s" : "")}</button>
            ${pk && pk.output && pk.q !== "final" && pk.seed != null ? `<button class="ghost" data-hq title="Same seed, full steps. Usually close to the draft, with more detail.">★ Final quality</button>` : ""}
            ${s.takes.filter(t => t.output).length + (s.final ? 1 : 0) > 1 ? `<button class="ghost" data-cmp>⇆ Compare</button>` : ""}
            ${pk && pk.output ? `<button class="ghost" data-rev title="The local AI watches the take and scores it">✦ Review${pk.review && pk.review.review ? " · " + esc(String(pk.review.review.score)) + "/10" : ""}</button><button class="ghost" data-fin>✓ Finish…</button><button class="ghost" data-cont>Continue →</button>` : ""}
          </div></div></div></div>`);
      const fr = $(".sframe", el), fi = $("input", fr);
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
let loraCache = null;
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
  return sec;
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
  const p = h(`<div class="page"><div class="eyebrow">Library</div><h1>LoRAs</h1>
    <div class="chips" id="ltab"><div class="chip on" data-t="mine">Installed</div><div class="chip" data-t="civ">Find on Civitai</div></div>
    <div id="lbody" style="margin-top:18px"></div></div>`);
  main.append(p);
  const body = $("#lbody", p);
  const tab = t => { $$("#ltab .chip", p).forEach(c => c.classList.toggle("on", c.dataset.t === t)); store.set("ltab", t); (t === "mine" ? mine : civ)(); };
  $$("#ltab .chip", p).forEach(c => c.onclick = () => tab(c.dataset.t));
  async function mine() {
    body.innerHTML = `<div class="lgrid" id="lg"></div>`;
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
    const bases = [["", "All"], ["Illustrious", "Illustrious"], ["Pony", "Pony"], ["SDXL 1.0", "SDXL"], ["Qwen", "Qwen"], ["ZImageTurbo", "Z-Image"], ["Wan Video 2.2 I2V-A14B", "Wan 2.2 I2V"], ["Wan Video 2.2 T2V-A14B", "Wan 2.2 T2V"], ["LTXV", "LTX"], ["Flux.2 Klein 9B", "FLUX Klein"]];
    body.innerHTML = `<div class="filters"><input type="text" class="search" id="cq" placeholder="Search Civitai LoRAs…"><select id="cb">${bases.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select>
      <select id="cs"><option>Most Downloaded</option><option>Highest Rated</option><option>Newest</option></select><button class="ghost" id="cgo">Search</button><a href="javascript:void 0" id="ckx" class="hint">Change key</a></div>
      <div class="lgrid" id="cg"></div><div class="row" style="justify-content:center;margin-top:16px"><button class="ghost hidden" id="cmore">More</button></div>`;
    $("#cb", body).value = store.get("cbase", "");
    let next = null;
    const run = async more => {
      const g = $("#cg", body); if (!more) { g.innerHTML = `<div class="empty">Searching…</div>`; next = null; }
      store.set("cbase", $("#cb", body).value);
      const qs = new URLSearchParams({ q: $("#cq", body).value.trim(), base: $("#cb", body).value, sort: $("#cs", body).value });
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
            <div class="lb2"><b>${esc(it.name)}</b><span>${esc(v.base || "")} · ⤓ ${(it.downloads || 0).toLocaleString()}</span></div></div>`);
          const vd = $("video", c); if (vd) { c.onmouseenter = () => vd.play().catch(() => { }); c.onmouseleave = () => vd.pause(); }
          c.onclick = () => civDetail(it);
          g.append(c);
        });
      } catch (e) { g.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
    };
    $("#cgo", body).onclick = () => run(false);
    $("#cq", body).onkeydown = e => { if (e.key === "Enter") run(false); };
    $("#cb", body).onchange = $("#cs", body).onchange = () => run(false);
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
    <select id="cv">${it.versions.map((v, k) => `<option value="${k}">${esc(v.name)} · ${esc(v.base || "")}</option>`).join("")}</select>
    <div class="cimgs"></div><div class="cinfo"></div><div class="cdesc muted"></div>
    <div class="foot"><span class="hint" id="cst" style="margin-right:auto"></span><button class="ghost" data-x>Close</button><button class="btn primary" id="cdl">Download</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  $(".cdesc", d).textContent = it.desc || "";
  const draw = () => {
    const v = it.versions[vi];
    $(".cimgs", d).innerHTML = v.images.map(i => i.type === "video" ? `<video src="/manny/api/civitai/img?u=${encodeURIComponent(i.url)}" muted loop autoplay playsinline></video>` : `<img src="/manny/api/civitai/img?u=${encodeURIComponent(i.url)}" loading="lazy">`).join("") || `<div class="hint">No previews${S.incog ? "" : " (mature previews show only in private mode)"}.</div>`;
    const f = v.files.find(x => x.primary) || v.files[0], fam = loraFamily(v.base);
    const works = { sdxl: "Anime Desire", qwen: "Qwen Image 2.1", zimage: "Z-Image Turbo", anima: "Anima", wan22: "Video › Wan 2.2", ltx: "Video › LTX-2.5", h3: "Video › MiniMax H3", krea: "Krea 2", flux3: "FLUX 3 Klein", moss: "MOSS" }[fam];
    $(".cinfo", d).innerHTML = `<div class="kv"><span>Made for</span><span>${esc(v.base || "?")}</span><span>Works with</span><span>${works ? esc(works) : "<span class='warn'>none of your models</span>"}</span>
      <span>Trigger words</span><span>${v.trained.length ? esc(v.trained.join(", ")) : "none"}</span><span>File</span><span>${f ? esc(f.name) + " · " + fmtSize((f.sizeKB || 0) * 1024) : "no .safetensors file"}</span></div>`;
    $("#cdl", d).disabled = !f;
  };
  $("#cv", d).onchange = e => { vi = +e.target.value; draw(); };
  draw();
  $("#cdl", d).onclick = async () => {
    const v = it.versions[vi], f = v.files.find(x => x.primary) || v.files[0], btn = $("#cdl", d);
    btn.disabled = true;
    try {
      const r = await post("/manny/api/civitai/download", { version: v.id, file: f.name, private: S.incog });
      await pollJob(r.id, s => { const e = $("#cst", d); if (e) e.textContent = s; });
      loraCache = null; toast(`${it.name} installed`); const e = $("#cst", d); if (e) e.textContent = "Installed. Add it from Create › LoRAs.";
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
    <a href="#/home" class="mitem">⌂ Home</a><a href="#/characters" class="mitem">☺ Characters</a><a href="#/loras" class="mitem">◈ LoRAs</a><a href="#/reel" class="mitem">♫ Reel</a></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $("[data-x]", d).onclick = close; $$("a", d).forEach(a => a.addEventListener("click", close));
}

/* ------------------------------------------------------------ QUEUE + WS */
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
  $("#q-dot").className = "dot" + (run ? " run" : "");
  $("#q-title").textContent = run ? "Rendering" : "Idle";
  $("#q-sub").textContent = run ? `${appNameOf(run)}${S.progressNode ? " · " + S.progressNode : ""}${q.queue_pending.length ? " · " + q.queue_pending.length + " waiting" : ""}` : "Nothing rendering";
  $("#q-bar").style.width = run ? Math.round(S.progress * 100) + "%" : "0%";
  $("#mq-dot").className = "dot" + (run ? " run" : "");
  $("#mq-t").textContent = run ? `${Math.round(S.progress * 100)}%${q.queue_pending.length ? " · +" + q.queue_pending.length : ""}` : (q.queue_pending.length ? q.queue_pending.length + " waiting" : "Idle");
  updateJob();
}
function drawDrawer() {
  const b = $("#drawer-body"); b.innerHTML = ""; const q = S.queue; if (!q) return;
  if (!q.queue_running.length && !q.queue_pending.length) b.append(h(`<div class="empty">The queue is empty.</div>`));
  q.queue_running.forEach(it => b.append(qItem(it, true)));
  q.queue_pending.slice().sort((a, b) => a[0] - b[0]).forEach(it => b.append(qItem(it, false)));
}
function qItem(it, running) {
  const el = h(`<div class="qitem ${running ? "run" : ""}"><div class="row"><b>${esc(appNameOf(it))}</b><button class="icon" title="${running ? "Stop" : "Remove"}">✕</button></div>
     <small>${running ? "Rendering · " + Math.round(S.progress * 100) + "%" : "Waiting · #" + it[0]}</small>${running ? `<div class="bar" style="margin-top:8px"><div style="width:${Math.round(S.progress * 100)}%"></div></div>` : ""}</div>`);
  $("button", el).onclick = async () => {
    if (running) await post("/interrupt", { prompt_id: it[1] }); else await post("/queue", { delete: [it[1]] });
    setTimeout(pollQueue, 300);
  };
  return el;
}
$("#queue-card").onclick = () => { $("#drawer").classList.remove("hidden"); drawDrawer(); };
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
      case "progress": S.progress = d.max ? d.value / d.max : 0; S.progressNode = `Step ${d.value}/${d.max}`; drawQueueCard(); { const w8 = S.compare && S.compare.items.find(x => x.prompt_id === S.running && !x.url); if (w8 && location.hash.startsWith("#/create")) { const c = $(`.cmpcell[data-pid="${w8.prompt_id}"] .cmpwait`); if (c) c.textContent = "Rendering " + Math.round(S.progress * 100) + "%"; } } { const tk = location.hash.startsWith("#/storyboard") && $(`.take[data-pid="${S.running}"] span`); if (tk) tk.textContent = Math.round(S.progress * 100) + "%"; } if (!$("#drawer").classList.contains("hidden")) drawDrawer(); break;
      case "executed": onOutput(d); break;
      case "execution_success": S.progress = 1; drawQueueCard(); S.gallery = []; break;
      case "execution_error": toast("Render failed: " + (d.exception_message || "error").slice(0, 300), true); pollQueue(); break;
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
  if (!quiet) { loadChars().then(() => route()); }
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
(async function boot() {
  try { S.apps = await api("/manny/api/apps"); } catch (e) { toast("Couldn't load apps: " + e.message, true); }
  S.apps = S.apps.filter(a => !a.error);
  const ord = a => { const k = APP_ORDER.indexOf(appShort(a).toLowerCase()); return k < 0 ? 50 : k; };
  S.apps.sort((a, b) => ord(a) - ord(b) || appShort(a).localeCompare(appShort(b)));
  if (S.appIdx >= S.apps.length) S.appIdx = 0;
  try { const a = await api("/manny/api/private/status"); S.pw = a; if (a.local) $("#nav-pw").classList.remove("hidden"); setIncog(a.unlocked && store.get("incog", true), true); } catch { }
  migrateLocalValues();
  await loadChars();
  connect(); pollQueue(); sysStats(); setInterval(sysStats, 15000); setInterval(pollQueue, 5000);
  route();
})();
})();
