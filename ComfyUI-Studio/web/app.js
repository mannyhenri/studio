/* Studio — a studio front end for this ComfyUI. */
(() => {
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const store = {
  get(k, d) { try { const v = localStorage.getItem("studio." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("studio." + k, JSON.stringify(v)); } catch {} },
};

let clientId = store.get("clientId", null);
if (!clientId) { clientId = "studio-" + Math.random().toString(36).slice(2, 12); store.set("clientId", clientId); }

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
  if (r.status === 401) { location.href = "/studio/login?next=" + encodeURIComponent(location.pathname + location.hash); throw new Error("sign in"); }
  if (!r.ok) throw Object.assign(new Error(typeof data === "string" ? data : JSON.stringify(data)), { data });
  return data;
}
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body) });
const viewUrl = (f, sub, type = "output") => `/view?filename=${encodeURIComponent(f)}&subfolder=${encodeURIComponent(sub || "")}&type=${type}`;
const fileUrl = (f, priv) => priv ? `/studio/api/pfile?path=${encodeURIComponent(((f.subfolder ? f.subfolder + "/" : "") + f.filename))}` : viewUrl(f.filename, f.subfolder);
const itemUrl = it => fileUrl(it, it.private);
/* fast previews: the server makes small cached copies, so big (Gigapixel) files open quickly on a phone */
const cq = it => `filename=${encodeURIComponent(it.filename)}&subfolder=${encodeURIComponent(it.subfolder || "")}&m=${Math.round(it.mtime || 0)}${it.private ? "&private=1" : ""}`;
const thumbUrl = (it, w = 640) => `/studio/api/thumb?${cq(it)}&w=${w}`;
const proxyUrl = it => `/studio/api/proxy?${cq(it)}`;
const isTiff = it => /\.tiff?$/i.test(it.filename);
const bigPic = it => isTiff(it) || (it.size || 0) > 3e6;
const bigVid = it => (it.size || 0) > 12e6;
const PQ = () => S.incog ? "?private=1" : "";
const mkey = app => (S.incog ? "P|" : "") + app.id;
const inputUrl = (value) => { const i = value.lastIndexOf("/"); return i < 0 ? viewUrl(value, "", "input") : viewUrl(value.slice(i + 1), value.slice(0, i), "input"); };

async function upload(file) {
  const fd = new FormData();
  fd.append("image", file); fd.append("subfolder", S.incog ? "studio_private" : "studio"); fd.append("type", "input"); fd.append("overwrite", "false");
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
const appShort = a => (a?.name || "").replace(/^Studio\s*-\s*/i, "");
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
const appIcon = a => ({ edit: "✎", inpaint: "◐", upscale: "⤢", "upscale video": "⤢", "remove background": "✂", "smooth motion": "≋", "character replace": "⇄" })[appShort(a).toLowerCase()] || (a.kind === "video" ? "▶" : "▣");
const findApp = name => S.apps.findIndex(a => appShort(a).toLowerCase() === name);
const CAMERA = [["Static", "static locked-off camera"], ["Push in", "slow dolly push-in toward the subject"], ["Pull back", "slow dolly pull-back revealing the scene"],
  ["Pan left", "smooth pan left"], ["Pan right", "smooth pan right"], ["Tilt up", "slow tilt up"], ["Orbit", "camera orbits around the subject"],
  ["Crane up", "crane shot rising up"], ["Crane down", "crane shot descending"], ["Tracking", "tracking shot following the subject"],
  ["Handheld", "handheld camera with natural shake"], ["Drone", "aerial drone flyover"], ["Crash zoom", "fast crash zoom in"],
  ["Dolly zoom", "dolly zoom (vertigo effect)"], ["Low angle", "low-angle hero shot"], ["Close-up", "tight close-up"], ["Slow motion", "slow motion"],
  ["Wide", "wide establishing shot"], ["POV", "point-of-view shot"], ["Over the shoulder", "over-the-shoulder shot"], ["High angle", "high-angle shot looking down"]];

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
const pages = { home: pageHome, create: pageCreate, gallery: pageGallery, characters: pageCharacters, storyboard: pageStoryboard, loras: pageLoras, reel: pageReel, sound: pageSound, projects: pageProjects, previz: pagePreviz, "3d": (m, a) => location.replace("#/previz" + (a && a.startsWith("glb:") ? "/" + a : "")), settings: pageSettings, aitools: pageAiTools };
async function route() {
  // a screenplay being typed is saved before its page goes (it reads the page itself): the next page sees the breakdown it makes
  if (S.spFlush) { const f = S.spFlush; S.spFlush = null; try { await f(); } catch { } }
  const [, page = "home", arg, sub] = location.hash.split("/");
  closeMenu();
  const main = $("#main"); main.innerHTML = "";
  const md = $("#modal"); if (md && !md.classList.contains("hidden") && !md.querySelector(".lbox,.lb")) { md.classList.add("hidden"); md.innerHTML = ""; }
  navRefresh();
  (pages[page] || pageHome)(main, arg, sub);
  main.scrollTop = 0; window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
document.addEventListener("click", e => { if (e.target.closest && e.target.closest("#bmore")) moreMenu(); });

let SR = null;                                   // screenplay/rules.js, loaded at boot
/* ------------------------------------------------------------ NAVIGATION: the film's three phases in the top bar (Film Engine's layout)
   Each phase is a menu of its tools. A project's own steps (screenplay … film) are pages of the project that is open, which
   the top bar names and switches; the other tools (Create's apps, Characters, Previz, Reel…) work with or without one. */
const PHASES = [["pre", "Pre-production"], ["prod", "Production"], ["post", "Post-production"]];
const P_STEP = {   // a project's pages: phase, menu label, icon, what it is
  screenplay: ["pre", "Screenplay", "✎", "Write or import it, on the standard page"],
  scenes: ["pre", "Scenes & shots", "▤", "The breakdown: scenes and camera setups"],
  cast: ["pre", "Cast", "☺", "Who plays each part: new parts join as you write"],
  locations: ["pre", "Locations", "◎", "Every place in the screenplay, and how it looks"],
  storyboard: ["pre", "Storyboard", "▣", "A frame for every shot, scene by scene"],
  takes: ["prod", "Takes", "▶", "Draft, pick and finish each shot's video"],
  sound: ["post", "Music & dialogue", "♪", "The score, and the lines in the cast's voices"],
  film: ["post", "Film edit", "🎬", "Cut the takes into the film: titles, music"],
};
const P_ALIAS = { frames: "storyboard", board: "takes" };     // the pages' names before: old links and saved places still open
const APP_DESC = { image: "Pictures from any model on this PC", video: "Shots from any model, with sound", edit: "Change a picture in words", inpaint: "Repaint one area of a picture",
  upscale: "Pictures to 2K or 4K", "upscale video": "Clips to a higher resolution", "remove background": "Cut the subject out", "smooth motion": "More frames: smoother clips", "character replace": "Put someone else in a clip" };
S.projects = []; S.projId = store.get("proj", null);
const curProject = () => S.projects.find(x => x.id === S.projId) || null;
const projTitle = pr => (pr && (pr.title || pr.name)) || "Untitled";
async function loadProjects() { try { S.projects = await api("/studio/api/projects" + PQ()); } catch { } navRefresh(); return S.projects; }
function setProject(pr) { if (pr && pr.id) { S.projId = pr.id; store.set("proj", pr.id); const k = S.projects.findIndex(x => x.id === pr.id); if (k < 0) S.projects.unshift(pr); else S.projects[k] = pr; } navRefresh(); }
const hashParts = () => { const [, page = "home", arg, sub] = location.hash.split("/"); return { page, arg, sub }; };
function phaseOf(r = hashParts()) {
  if (r.page === "projects") return (P_STEP[P_ALIAS[r.sub] || r.sub] || [])[0] || "";
  if (r.page === "create") { const a = S.apps[r.arg != null && S.apps[+r.arg] ? +r.arg : S.appIdx]; return a && isFixer(a) ? "post" : "prod"; }
  return { characters: "pre", previz: "pre", storyboard: "prod", aitools: "prod", reel: "post", sound: "post" }[r.page] || "";
}
function curKey(r = hashParts()) {
  if (r.page === "projects") return r.arg ? `projects/${r.arg}${r.sub ? "/" + r.sub : ""}` : "projects";
  if (r.page === "create") return "create/" + (r.arg != null && S.apps[+r.arg] ? +r.arg : S.appIdx);
  if (r.page === "aitools") return "aitools/" + (r.arg || store.get("aiTool", ""));
  return r.page;
}
// how far a project is, per page and per phase (the phase buttons' bars, the menus, the project's overview)
function projStats(pr) {
  const shots = pr.shots || [], roles = pr.roles || [], board = (S.boards || []).find(b => b.id === pr.board), bs = board ? board.shots || [] : [];
  const lines = shots.reduce((a, s) => a + (s.lines || []).length, 0), voiced = shots.reduce((a, s) => a + (s.voice_files || []).filter(Boolean).length, 0);
  return { shots: shots.length, scenes: (pr.scenes || []).length, pages: pr.pages || 0, roles: roles.length, cast: roles.filter(r => (pr.castMap || {})[r.name]).length,
    kf: shots.filter(s => s.keyframe).length, taken: bs.filter(s => s.output).length, finished: bs.filter(s => s.final).length, lines, voiced,
    changed: bs.filter(x => changesOf(x).length).length, staleKf: shots.filter(s => frameDrift(pr, s).length).length,
    locs: (pr.locations || []).filter(l => !l.gone && (l.scenes || []).length).length, looked: (pr.locations || []).filter(l => !l.gone && (l.scenes || []).length && (l.look || l.picture)).length,
    music: !!pr.music_file, film: !!(board && board.film), written: !!(pr.screenplay?.text || "").trim() };
}
function stepStat(pr, k, st = projStats(pr)) {
  return {
    screenplay: st.pages ? plural(st.pages, "page") : st.written ? "Started" : "Empty",
    scenes: st.shots ? `${plural(st.scenes, "scene")} · ${st.shots} shots` : "None yet",
    cast: st.roles ? `${st.cast}/${st.roles} cast` : "No parts yet",
    locations: st.locs ? plural(st.locs, "place") : "None yet",
    storyboard: st.shots ? `${st.kf}/${st.shots} frames${st.staleKf ? ` · ${st.staleKf} from an earlier draft` : ""}` : "–",
    takes: st.shots ? `${st.taken}/${st.shots} shots${st.changed ? ` · ${st.changed} changed` : ""}` : "–",
    sound: [st.music ? "Music ✓" : "No music", st.lines ? `${st.voiced}/${st.lines} lines` : ""].filter(Boolean).join(" · "),
    film: st.film ? "Made ✓" : "Not cut yet",
  }[k];
}
function phaseFrac(pr, ph, st = projStats(pr)) {
  if (ph === "pre") return [[st.written && st.scenes > 0, st.shots > 0, st.roles > 0 && st.cast === st.roles, st.locs > 0 && st.looked === st.locs, st.shots > 0 && st.kf === st.shots].filter(Boolean).length, 5];
  if (ph === "prod") return [st.taken, st.shots];
  const done = [st.music, st.film, ...(st.lines ? [st.voiced >= st.lines] : [])];
  return [done.filter(Boolean).length, done.length];
}
// a phase's tools, in groups: the open project's pages first
function phaseGroups(ph) {
  const pr = curProject();
  const step = k => ({ label: P_STEP[k][1], icon: P_STEP[k][2], desc: P_STEP[k][3], href: pr ? `#/projects/${pr.id}/${k}` : "#/projects", key: pr ? `projects/${pr.id}/${k}` : "", off: !pr, stat: pr ? stepStat(pr, k) : "" });
  const app = a => { const k = S.apps.indexOf(a); return { label: appShort(a), icon: appIcon(a), desc: APP_DESC[appShort(a).toLowerCase()] || `${a.kind === "video" ? "Clips" : "Pictures"} · ${plural(a.inputs.length, "option")}`, href: "#/create/" + k, key: "create/" + k }; };
  const link = (label, icon, desc, page) => ({ label, icon, desc, href: "#/" + page, key: page });
  const proj = keys => ({ head: "Project", proj: pr, items: keys.map(step) });
  if (ph === "pre") return [proj(["screenplay", "scenes", "cast", "locations", "storyboard"]), { head: "Design", items: [link("Characters", "☺", "Faces, looks and voices you reuse", "characters"), link("Previz", "◰", "3D sets: cameras, lenses, light, blocking", "previz")] }];
  // Film Engine's Generators (AI Depth, Slow-Mo, Stems, Subtitles, Lossless Cut, One-Click Master): one that can't run here says so
  const ai = t => ({ label: t.name, icon: t.icon || "✦", desc: t.unavailable ? "Not ready on this PC: " + t.unavailable : t.short || "", href: "#/aitools/" + t.id, key: "aitools/" + t.id, stat: t.unavailable ? "Not ready" : "" });
  if (ph === "prod") return [proj(["takes"]), { head: "Generate", items: S.apps.filter(a => !isFixer(a)).map(app) }, { head: "AI tools", col: 2, items: S.aiTools.map(ai) }];
  return [proj(["sound", "film"]), { head: "Sound & cut", items: [link("Reel", "♫", "Pictures and clips cut to a song's beat", "reel"), link("Music & voices", "♪", "Songs, scores and voices", "sound")] },
    { head: "Fix & enhance", col: 2, items: S.apps.filter(isFixer).map(app) }];
}
// a long menu (Post-production's) in two columns, so every tool shows without scrolling
function phaseMenuHtml(ph) {
  const gs = phaseGroups(ph).filter(g => g.items.length), two = gs.some(g => g.col === 2);
  const html = list => groupsHtml(list);
  return two ? `<div class="mcols"><div class="mcol">${html(gs.filter(g => g.col !== 2))}</div><div class="mcol">${html(gs.filter(g => g.col === 2))}</div></div>` : html(gs);
}
function groupsHtml(gs) {
  const key = curKey();
  return gs.map(g => `<div class="mg"><div class="mgh">${g.head === "Project"
      ? (g.proj ? `<span>Project · ${esc(projTitle(g.proj))}</span><a href="#/projects/${g.proj.id}/overview">Overview</a>` : `<span>Project · none open</span><a href="#/projects">Open one →</a>`)
      : esc(g.head)}</div>${g.items.map(it => `<a class="mi ${it.key && it.key === key ? "on" : ""} ${it.off ? "off" : ""}" href="${it.href}" role="menuitem"><i>${it.icon}</i><span><b>${esc(it.label)}</b><small>${esc(it.off ? "Open a project to use it" : it.desc)}</small></span>${it.stat ? `<em>${esc(it.stat)}</em>` : ""}</a>`).join("")}</div>`).join("");
}
function projMenuHtml() {
  const key = curKey(), list = S.projects.slice(0, 8);
  return `<div class="mg"><div class="mgh"><span>Projects</span><a href="#/projects">All projects →</a></div>${list.length ? list.map(x => `<a class="mi ${x.id === S.projId ? "on" : ""}" href="#/projects/${x.id}" role="menuitem"><i>${x.private ? "🕶" : "◧"}</i><span><b>${esc(projTitle(x))}</b><small>${[x.scenes ? plural(x.scenes.length, "scene") : "", plural((x.shots || []).length, "shot"), x.stage || "Screenplay"].filter(Boolean).join(" · ")}</small></span></a>`).join("")
    : `<p class="hint mempty">No projects yet.</p>`}</div><div class="mg"><a class="mi ${key === "projects" ? "on" : ""}" href="#/projects" role="menuitem"><i>＋</i><span><b>New project</b><small>Import a screenplay (PDF, Fountain) or write one</small></span></a></div>`;
}
let menuOpen = null, menuByKey = false;   // opened from the keyboard: Escape gives the focus back to its button
function openMenu(id, btn) {
  const pop = $("#menupop");
  pop.innerHTML = id === "proj" ? projMenuHtml() : phaseMenuHtml(id);
  pop.classList.toggle("wide", !!$(".mcols", pop));
  pop.classList.remove("hidden"); menuOpen = id; pop.dataset.menu = id;
  const r = btn.getBoundingClientRect();
  pop.style.top = Math.round(r.bottom + 8) + "px";
  pop.style.left = Math.round(Math.max(10, Math.min(r.left, innerWidth - pop.offsetWidth - 10))) + "px";
  $$("[data-menu]", $("#mtop")).forEach(b => { const on = b === btn; b.classList.toggle("open", on); b.setAttribute("aria-expanded", on ? "true" : "false"); });
}
function closeMenu() {
  if (!menuOpen) return;
  menuOpen = null; const pop = $("#menupop"); pop.classList.add("hidden"); pop.innerHTML = "";
  $$("[data-menu]", $("#mtop")).forEach(b => { b.classList.remove("open"); b.setAttribute("aria-expanded", "false"); });
}
function buildNav() {
  const track = $("#menus"); track.innerHTML = "";
  PHASES.forEach(([id, label]) => {
    const b = h(`<button class="fe-ph" data-menu="${id}" data-phase="${id}" aria-haspopup="menu" aria-expanded="false" aria-label="${label}"><span class="fe-ph-row"><span class="fe-ph-name">${label}</span><span class="fe-ph-short">${{ pre: "Pre-prod", prod: "Production", post: "Post" }[id]}</span><span class="fe-ph-frac"></span><svg class="fe-caret" viewBox="0 0 10 6"><path d="M1 1l4 4 4-4"/></svg></span><span class="fe-ph-bar"><span class="fe-ph-fill"></span></span></button>`);
    track.append(b);
  });
  $$("[data-menu]", $("#mtop")).forEach(b => {
    b.onclick = e => { e.stopPropagation(); if (menuOpen === b.dataset.menu) return closeMenu(); openMenu(b.dataset.menu, b); menuByKey = e.detail === 0; if (menuByKey) { const f = $("#menupop .mi"); if (f) f.focus(); } };
    b.onmouseenter = () => { if (menuOpen && menuOpen !== b.dataset.menu) openMenu(b.dataset.menu, b); };   // a menu bar: the open menu follows the pointer
  });
  $("#menupop").onclick = e => { if (e.target.closest("a")) closeMenu(); };
  $("#menupop").onkeydown = e => {
    if (!["ArrowDown", "ArrowUp"].includes(e.key)) return;
    const all = $$("#menupop a"), k = all.indexOf(document.activeElement); e.preventDefault();
    const n = all[(k + (e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length]; if (n) n.focus();
  };
  document.addEventListener("click", e => { if (menuOpen && !e.target.closest("#menupop,[data-menu]")) closeMenu(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && menuOpen) { const b = $(`#mtop [data-menu="${menuOpen}"]`); closeMenu(); if (b) { if (menuByKey) b.focus(); else b.blur(); } } });
  window.addEventListener("resize", closeMenu);
  $$("#bnav [data-phase]").forEach(a => a.onclick = () => phaseSheet(a.dataset.phase));
}
function navRefresh() {
  const r = hashParts(), ph = phaseOf(r), pr = curProject(), st = pr ? projStats(pr) : null;
  $("#projname").textContent = pr ? projTitle(pr) : "No project open";
  $("#projsub").textContent = pr ? "Switch project" : "Open a project";
  $("#projbtn").title = pr ? projTitle(pr) + " · switch project" : "Open a project";
  $("#projbtn").classList.toggle("on", r.page === "projects");
  $$("#menus .fe-ph").forEach(b => {
    const id = b.dataset.phase, fr = pr ? phaseFrac(pr, id, st) : null;
    b.classList.toggle("on", id === ph);
    const alert = st ? (id === "prod" ? st.changed : id === "pre" ? st.staleKf : 0) : 0;
    b.classList.toggle("alert", !!alert);
    b.title = !alert ? "" : id === "prod" ? `The screenplay changed ${plural(alert, "shot")} on the takes since you last looked` : `${plural(alert, "storyboard frame")} made from an earlier draft of the screenplay`;
    $(".fe-ph-frac", b).textContent = fr && fr[1] ? `${fr[0]}/${fr[1]}` : "";
    $(".fe-ph-fill", b).style.width = fr && fr[1] ? Math.round(fr[0] / fr[1] * 100) + "%" : "0";
  });
  $$("#bnav a, #mtop a[data-page]").forEach(a => a.classList.toggle("active", a.dataset.phase ? a.dataset.phase === ph : a.dataset.page === r.page));
  if (menuOpen) { const b = $(`#mtop [data-menu="${menuOpen}"]`); if (b) openMenu(menuOpen, b); }
}
// phones: a phase's tools as a sheet from the bottom bar
function phaseSheet(ph) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog sheet"><div class="row" style="justify-content:space-between"><h2>${esc(PHASES.find(p => p[0] === ph)[1])}</h2><button class="icon" data-x>✕</button></div><div class="menupop insheet">${phaseMenuHtml(ph)}</div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $("[data-x]", d).onclick = close; $$("a", d).forEach(a => a.addEventListener("click", close));
}

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
  // the AI tools (Production): a shortcut to each one's page
  S.aiTools.forEach(t => ag.append(h(`<a class="qcard ai ${t.unavailable ? "off" : ""}" href="#/aitools/${t.id}" title="${esc(t.unavailable ? "Not ready on this PC: " + t.unavailable : t.description || "")}">
    <div class="ic">${t.icon || "✦"}</div><h3>${esc(t.name)}</h3><p>${esc(t.unavailable ? "Not ready on this PC" : t.short || "AI tool")}</p></a>`)));
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
  // the apps of this phase as tabs: the generators are Production, the fix & enhance tools Post-production (the top bar has both)
  const post = isFixer(app);
  const p = h(`<div class="page">
    <div class="eyebrow">${post ? "Post-production · Fix &amp; enhance" : "Production · Generate"}</div><h1>${esc(appShort(app))}</h1>
    <div class="apptabs"></div>
    <div class="create"><div class="panel form"></div><div class="stage"></div></div>
  </div>`);
  main.append(p);
  const tabs = $(".apptabs", p);
  S.apps.forEach((a, k) => { if (isFixer(a) !== post) return; const t = h(`<div class="apptab ${post ? "small" : ""} ${k === S.appIdx ? "active" : ""}"><span>${appIcon(a)}</span>${esc(appShort(a))}</div>`); t.onclick = () => openApp(k); tabs.append(t); });
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
    // P-H3 decides on its own how much of the reference it keeps: no slider (it would do nothing)
    const noStrength = L.model && /^P-H3/i.test(v[L.model.key] || "");
    if (L.refs.strength && !noStrength) rs.append(control(app, L.refs.strength, v, set));
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
  const has = v[m.key] && String(v[m.key]).startsWith("studio/mask_");
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
      const res = await post("/studio/api/run", { app: app.id, values: vals, count: 1, client_id: clientId, private: S.incog, target: runTarget() });
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
    const desc = charDesc(c);
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
      try { const r = await fetch("/studio/api/poses/upload", { method: "POST", body: fd }); if (!r.ok) throw new Error(await r.text()); const j = await r.json(); toast(`${j.poses} poses added to ${j.pack}`); await loadPoses(true); sec.replaceWith(poseSection(app)); }
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
      await post("/studio/api/schedule", { run: body, when, at: at ? at / 1000 : null, kind: app.kind, label: priv ? `${appShort(app)} (Incognito)` : `${appShort(app)}${txt ? ": " + txt : ""}` });
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
  try { S.sched = await api("/studio/api/schedule" + (S.incog ? "?private=1" : "")); } catch { S.sched = []; }
  if (!$("#drawer").classList.contains("hidden")) drawDrawer();
  drawQueueCard();
}
async function generate(app) {
  const btn = $("#gen"); if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }
  try {
    const { body, priv } = runBody(app);
    const res = await post("/studio/api/run", body);
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
  try { S.gallery = await api("/studio/api/gallery" + PQ()); S.galleryAt = Date.now(); } catch { S.gallery = []; }
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
      try { await post("/studio/api/trash", it); ok++; const k = items.indexOf(it); if (k >= 0) items.splice(k, 1); S.gallery = S.gallery.filter(x => x !== it); }
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
  const r = await post("/studio/api/favorite", { filename: it.filename, subfolder: it.subfolder, on: !it.fav, private: !!it.private });
  it.fav = r.fav; const g = S.gallery.find(x => x.filename === it.filename && x.subfolder === it.subfolder); if (g) g.fav = r.fav;
}
async function sendToApp(name, cur, kind) {
  const k = findApp(name); if (k < 0) return toast("That app isn't installed", true);
  const app = S.apps[k];
  const { value } = await post("/studio/api/use_output", cur);
  const v = valuesFor(app), L = layout(app), r = L.refs.find(x => x.media.kind === kind);
  if (r) { v[r.media.key] = value; if (r.toggle) v[r.toggle.key] = true; }
  saveState(); return k;
}
let lbKeys = null;
function lightbox(it, list) {
  list = list && list.length ? list : [it];
  let i = Math.max(0, list.indexOf(it));
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
  const close = () => { document.removeEventListener("fullscreenchange", onFsChange); if (document.fullscreenElement) document.exitFullscreen().catch(() => { }); m.classList.add("hidden"); m.innerHTML = ""; m.onclick = null; if (lbKeys) { document.removeEventListener("keydown", lbKeys); lbKeys = null; } };   // (the next dialogs don't close on a click beside them)
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
       ${cur.private ? "" : `<button class="btn" data-ait title="AI Depth, AI Slow-Mo, AI Stems, AI Subtitles, AI Lossless Cut, One-Click Master">✦ AI tools${cur.kind === "image" ? " (depth)" : ": slow-mo, depth, stems…"}</button>`}
     </div>
     <button class="ghost" data-board>＋ Add to storyboard${cur.kind === "image" ? " (as first frame)" : ""}</button>
     ${cur.kind === "image" && S.chars.length ? `<select class="ghost" data-face title="Make this picture a character's face (and keep it with their pictures)"><option value="">☺ Use as a character's face…</option>${S.chars.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select>` : ""}
     <div class="row"><a class="ghost" href="${url}" download="${esc(cur.filename)}">Download</a><button class="ghost danger" data-trash>Move to trash</button></div>
     <p class="hint">← → to browse · Esc to close. Trash is a folder (_trash) inside Pictures › Comfyui, so nothing is lost.</p>`;
    $("[data-fav]", side).onclick = async () => { await toggleFav(cur); const s = $("[data-fav]", side); s.textContent = cur.fav ? "★" : "☆"; s.classList.toggle("on", cur.fav); m.dataset.dirty = "1"; };
    let meta = null;
    api(`/studio/api/meta?filename=${encodeURIComponent(cur.filename)}&subfolder=${encodeURIComponent(cur.subfolder || "")}${cur.private ? "&private=1" : ""}`).then(md => {
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
    const fc = $("[data-face]", side); if (fc) fc.onchange = async () => {
      const c = S.chars.find(x => x.id === fc.value); fc.value = ""; if (!c) return;
      try {
        const val = (await post("/studio/api/use_output", cur)).value;
        c.face = val; c.pictures = [val, ...(c.pictures || []).filter(x => x !== val)];
        await post("/studio/api/characters", c); await loadChars();
        toast(`Now ${c.name}'s face: it's used in Create and in every project they're cast in`);
      } catch (e) { toast(e.message.slice(0, 200), true); }
    };
    $("[data-board]", side).onclick = async () => {
      let img = "";
      if (cur.kind === "image") img = (await post("/studio/api/use_output", cur)).value;
      await addToBoard(img, meta && meta.prompt && cur.kind === "image" ? "" : "");
      toast("Added a shot to “" + (curBoard()?.name || "My film") + "”");
    };
    $$("[data-to]", side).forEach(b => b.onclick = async () => {
      const [name, kind] = b.dataset.to.split(":");
      const k = await sendToApp(name, cur, kind); if (k == null || k < 0) return;
      close(); openApp(k); toast("Added to " + appShort(S.apps[k]));
    });
    $("[data-x]", side).onclick = close;
    const aib = $("[data-ait]", side);
    if (aib) aib.onclick = () => {
      S.aiPick = { filename: cur.filename, subfolder: cur.subfolder || "" }; close();
      const to = "#/aitools/" + (cur.kind === "image" ? "depth" : store.get("aiTool", "slowmo"));
      if (location.hash === to) route(); else location.hash = to;      // from the AI tools' own page: the same address
    };
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
      await post("/studio/api/trash", cur);
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

/* ------------------------------------------------------------ AI TOOLS (Production): Film Engine's Generators, on this PC
   AI Depth, AI Slow-Mo, AI Stems, AI Subtitles, AI Lossless Cut and One-Click Master: each an engine in
   ComfyUI-Studio/ai_tools/ with its generator.json, run by generators.py. Give a tool a file (an upload of any size, or
   one from the Gallery), set it, Generate. A section tool (Slow-Mo) does only what's marked between In and Out, a pieces
   tool (Lossless Cut) cuts each piece marked: I and O mark, ← → step a frame, as in Resolve. One job runs at a time, the
   others wait their turn; the History keeps every result, and the pictures and videos are in the Gallery (Generators). */
const GEN = "/studio/api/generators";
S.aiTools = []; S.ai = { input: null, inF: null, outF: null, pieces: [], pIn: null, pOut: null };
async function loadAiTools() { try { S.aiTools = (await api(GEN + "/tools")).tools || []; } catch { S.aiTools = []; } navRefresh(); return S.aiTools; }
const aiActive = j => j.status === "running" || j.status === "queued";
const aiErr = e => (e && e.data && e.data.error) || (e && e.message) || String(e);
/* 0:12.500, or 1:02:03.000 past an hour */
function aiClock(t) {
  if (t == null || !isFinite(t)) return "–";
  const ms = Math.round(t * 1000), hh = Math.floor(ms / 3600000), mm = Math.floor(ms / 60000) % 60, ss = (ms % 60000) / 1000;
  return `${hh ? `${hh}:${String(mm).padStart(2, "0")}` : mm}:${ss.toFixed(3).padStart(6, "0")}`;
}
function aiTimecode(frame, fps) {
  if (frame == null || !fps) return "–";
  const f = Math.round(fps), s = Math.floor(frame / f), pad = n => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}:${pad(frame % f)}`;
}
/* what a cut piece really is, when it differs from what was asked (a keyframe cut starts earlier) or was re-encoded */
function aiPieceInfo(pc) {
  const parts = [`${aiClock(pc.start)} → ${aiClock(pc.end)}`];
  if (pc.asked_start != null && Math.abs(pc.start - pc.asked_start) > 0.0015) parts.push(`starts on the keyframe at ${aiClock(pc.start)} (asked ${aiClock(pc.asked_start)})`);
  if (pc.reencoded && pc.reencoded.length) parts.push(`re-encoded ${pc.reencoded.map(([a, b]) => `${aiClock(a)}–${aiClock(b)}`).join(", ")}, the rest copied`);
  else if (pc.mode) parts.push("nothing re-encoded");
  return parts.join(" · ");
}
/* whether a tool takes this file, in words (the server checks the same) */
function aiFits(t, inp) {
  if (!t || !inp) return "";
  const acc = t.accepts || "video", k = inp.kind;
  if (acc === "audio") return k === "image" ? `${t.name} needs a sound file or a video with sound` : k === "video" && !inp.has_audio ? `${inp.name} has no sound` : "";
  if (acc === "video") {
    if (k === "audio" && !t.audio_ok) return `${t.name} needs a video`;
    if (k === "image" && !t.image_ok) return `${t.name} needs a video, not a picture`;
    return t.needs_sound && k === "video" && !inp.has_audio ? `${inp.name} has no sound` : "";
  }
  return k === "image" ? "" : `${t.name} needs a picture`;
}
const AI_STATS = { integrated_lufs: ["Loudness", " LUFS"], true_peak_dbtp: ["true peak", " dBTP"], loudness_range_lu: ["range", " LU"], input_lufs: ["the mix was", " LUFS"],
  input_true_peak_dbtp: ["its peak", " dBTP"], gain_db: ["gain", " dB"], limiter: ["limiter", ""], language: ["Language", ""], subtitles: ["subtitles", ""], stems: ["Stems", ""], model: ["model", ""] };
const aiStats = st => Object.entries(st || {}).map(([k, v]) => { const [l, u] = AI_STATS[k] || [k.replace(/_/g, " "), ""]; return `${l} ${typeof v === "number" ? +v.toFixed(2) : v}${u}`; }).join(" · ");
const AI_STATUS = { queued: "Waiting its turn", done: "Done", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted" };

async function pageAiTools(main, arg) {
  const pg = h(`<div class="page ait"><div class="eyebrow">Production · AI tools</div></div>`);
  main.append(pg);
  await loadAiTools();                                  // fresh: whether each tool can run here
  if (!document.body.contains(pg)) return;              // another page opened meanwhile
  const tools = S.aiTools;
  const t = tools.find(x => x.id === arg) || tools.find(x => x.id === store.get("aiTool")) || tools[0];
  if (!t) { pg.append(h(`<h1>AI tools</h1>`), h(`<div class="empty">No AI tools found in ComfyUI-Studio/generators.</div>`)); return; }
  if (arg !== t.id) history.replaceState(null, "", "#/aitools/" + t.id);
  store.set("aiTool", t.id); navRefresh();
  pg.append(h(`<h1>${esc(t.name)}</h1>`));
  const tabs = h(`<div class="apptabs ait-tabs" role="tablist" aria-label="AI tools"></div>`);
  tools.forEach(x => tabs.append(h(`<a class="apptab small ${x.id === t.id ? "active" : ""} ${x.unavailable ? "off" : ""}" role="tab" aria-selected="${x.id === t.id}" href="#/aitools/${x.id}" title="${esc(x.unavailable ? "Not ready on this PC: " + x.unavailable : x.short || "")}"><span>${x.icon || "✦"}</span>${esc(x.name)}</a>`)));
  pg.append(tabs);
  if (S.incog) { pg.append(h(`<div class="empty">The AI tools work on your regular files, not on Incognito ones yet: leave Incognito (the mask, top right) to use them.</div>`)); return; }
  const body = h(`<div class="ait-main"><div class="ait-left"><section class="ait-drop" aria-label="The file"></section><div class="ait-now hidden" role="status" aria-live="polite"></div></div>
      <section class="panel ait-panel" aria-label="${esc(t.name)}"></section></div>`);
  pg.append(body, h(`<div class="section-head"><h2>History</h2><span class="hint">One job runs at a time; the others wait their turn. Pictures and videos also land in the Gallery, in Generators › the tool's name.</span></div>`),
    h(`<ul class="ait-jobs"></ul>`), h(`<input type="file" hidden accept="video/*,audio/*,image/*,.mov,.mkv,.mxf,.mts,.m2ts,.wav,.aif,.aiff,.flac,.tif,.tiff,.heic">`));
  const A = S.ai, drop = $(".ait-drop", pg), panel = $(".ait-panel", pg), now = $(".ait-now", pg), jobsEl = $(".ait-jobs", pg), picker = $("input[type=file]", pg);
  const vals = store.get("aiv", {}), v = vals[t.id] || (vals[t.id] = {});
  (t.options || []).forEach(o => { if (!(o.name in v) && o.type !== "points") v[o.name] = o.default; });
  const saveVals = () => store.set("aiv", vals);
  let jobs = [], playing = null, busy = false, frame = 0, player = null;
  const alive = () => document.body.contains(pg);

  /* ---- the file */
  const setInput = inp => { A.input = inp; store.set("aiInput", inp ? inp.id : null); A.inF = A.outF = A.pIn = A.pOut = null; A.pieces = []; frame = 0; drawDrop(); drawPanel(); };
  const register = async body => {
    busy = true; drawPanel();
    try { setInput((await post(GEN + "/inputs", body)).input); } catch (e) { toast(aiErr(e), true); } finally { busy = false; drawPanel(); }
  };
  const uploadFile = file => new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", GEN + "/inputs/import");
    x.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    x.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    x.upload.onprogress = e => { if (e.lengthComputable) { const el = $(".ait-up", drop); if (el) el.textContent = `Uploading ${file.name}: ${Math.round(e.loaded / e.total * 100)}%`; } };
    x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch { } x.status < 300 ? resolve(d.input) : reject(new Error(d.error || `HTTP ${x.status}`)); };
    x.onerror = () => reject(new Error("The upload didn't go through"));
    x.send(file);
  });
  const upload = async file => {
    if (!file) return;
    busy = true; drop.innerHTML = `<div class="ait-empty"><div class="ait-up">Uploading ${esc(file.name)}…</div></div>`; drawPanel();
    try { setInput(await uploadFile(file)); } catch (e) { toast(e.message, true); drawDrop(); } finally { busy = false; drawPanel(); }
  };
  picker.onchange = () => { upload(picker.files[0]); picker.value = ""; };
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); const f = e.dataTransfer && e.dataTransfer.files[0]; if (f) upload(f); };

  const fps = () => A.input && A.input.kind === "video" && A.input.fps ? A.input.fps : null;
  const lastFrame = () => A.input && A.input.frames ? A.input.frames - 1 : Infinity;
  // the frame on screen, never past the clip's real last frame (at the very end the player's time rounds one beyond it)
  const curFrame = () => Math.max(0, Math.min(lastFrame(), player && fps() ? Math.round(player.currentTime * fps()) : frame));
  const nowAt = () => fps() ? curFrame() / fps() : Math.max(0, player ? player.currentTime : 0);
  const nowOut = () => fps() ? (curFrame() + 1) / fps() : Math.max(0, player ? player.currentTime : 0);
  const duration = () => (A.input && A.input.duration) || (player && player.duration) || 0;

  function drawDrop() {
    const inp = A.input;
    drop.classList.toggle("has", !!inp);
    if (!inp) {
      drop.innerHTML = `<div class="ait-empty"><div class="ait-ic">⇪</div><h3>Drop a video, a sound or a picture here</h3>
        <p class="hint">Or choose one: it's sent to this PC (any size, ProRes too). ${esc(t.name)} takes ${t.accepts === "audio" ? "a sound file or a video's sound" : t.image_ok ? "a video or a picture" : t.audio_ok ? "a video or a sound file" : "a video"}.</p>
        <div class="row"><button class="btn" data-file>Choose a file…</button><button class="ghost" data-gal>From the Gallery…</button></div></div>`;
      $("[data-file]", drop).onclick = () => picker.click();
      $("[data-gal]", drop).onclick = () => aiPickDialog(it => register({ filename: it.filename, subfolder: it.subfolder }));
      player = null; return;
    }
    const src = `${GEN}/inputs/${inp.id}${inp.preview ? "/preview" : ""}`;
    drop.innerHTML = `<div class="ait-player">${inp.kind === "image" ? `<img src="${src}" alt="${esc(inp.name)}">` : inp.kind === "video"
        ? `<video src="${src}" controls playsinline preload="metadata"></video>` : `<div class="ait-sound">♪</div><audio src="${src}" controls preload="metadata"></audio>`}
        <div class="ait-loading">${inp.preview ? `Preparing a ${inp.kind === "image" ? "viewable" : "playable"} copy of this ${esc((inp.codec || "").toUpperCase())} file…` : "Loading…"}</div></div>
      ${inp.kind === "image" ? "" : `<div class="vbar ait-bar" title="Click to go there"><div class="ait-sels"></div><div class="vhead"></div></div>`}
      <div class="row ait-info"><b title="${esc(inp.name)}">${esc(inp.name)}</b><span class="mono hint">${[inp.width ? `${inp.width}×${inp.height}` : "", inp.fps ? `${+inp.fps.toFixed(3)} fps` : "", inp.frames ? `${inp.frames} frames` : "", inp.duration ? `${inp.duration.toFixed(2)} s` : "", inp.kind === "audio" || inp.has_audio ? "sound" : inp.kind === "video" ? "no sound" : ""].filter(Boolean).join(" · ")}</span>
        ${inp.kind === "video" ? `<span class="mono ait-frame"></span>` : ""}<span class="sp"></span><button class="ghost sm" data-gal>From the Gallery…</button><button class="ghost sm" data-file>Another file…</button></div>`;
    $("[data-file]", drop).onclick = () => picker.click();
    $("[data-gal]", drop).onclick = () => aiPickDialog(it => register({ filename: it.filename, subfolder: it.subfolder }));
    const media = $("img,video,audio", drop), loading = $(".ait-loading", drop);
    player = inp.kind === "image" ? null : media;
    const ready = () => loading.classList.add("hidden");
    const failed = () => { loading.classList.remove("hidden"); loading.textContent = "This file can't be shown here, but the tools can still read it."; };
    if (inp.kind === "image") { media.onload = ready; media.onerror = failed; return; }
    media.onloadedmetadata = () => { ready(); drawMarks(); };
    media.onerror = failed;
    const tick = () => { frame = fps() ? Math.round(media.currentTime * fps()) : 0; drawMarks(); };
    media.ontimeupdate = tick; media.onseeked = tick;
    if (media.requestVideoFrameCallback) { const cb = () => { if (!alive() || player !== media) return; tick(); media.requestVideoFrameCallback(cb); }; media.requestVideoFrameCallback(cb); }
    $(".ait-bar", drop).onclick = e => { const r = e.currentTarget.getBoundingClientRect(), d = duration(); if (d) media.currentTime = Math.max(0, Math.min(d, (e.clientX - r.left) / r.width * d)); };
    drawMarks();
  }
  /* the bar under the player: the section or the pieces, and the playhead */
  function drawMarks() {
    const bar = $(".ait-bar", drop); if (!bar || !A.input) return;
    const d = duration() || 1, pct = x => `${Math.max(0, Math.min(100, x / d * 100))}%`, f = fps();
    const spans = t.range === "section" ? (A.inF != null && A.outF != null ? [[A.inF / f, A.outF / f]] : A.inF != null ? [[A.inF / f, A.inF / f + 0.04]] : [])
      : t.range === "segments" ? A.pieces.map(p => [p.start, p.end]).concat(A.pIn != null ? [[A.pIn, A.pOut != null ? A.pOut : A.pIn + 0.04]] : []) : [];
    $(".ait-sels", bar).innerHTML = spans.map(([a, b]) => `<div class="vsel" style="left:${pct(a)};width:calc(${pct(b)} - ${pct(a)})"></div>`).join("");
    $(".vhead", bar).style.left = pct(player ? player.currentTime : 0);
    const fr = $(".ait-frame", drop); if (fr) fr.textContent = `frame ${curFrame()} · ${aiTimecode(curFrame(), f)}`;
  }

  /* ---- marks: a section (frames, Out exclusive) or pieces (seconds) */
  const markIn = () => { const f = curFrame(); A.inF = f; if (A.outF != null && A.outF <= f) A.outF = null; drawPanel(); drawMarks(); };
  const markOut = () => { const f = curFrame() + 1; A.outF = f; if (A.inF != null && A.inF >= f) A.inF = null; drawPanel(); drawMarks(); };   // the frame on screen is included
  const sortPieces = ps => ps.sort((a, b) => a.start - b.start || a.end - b.end);
  const pieceIn = () => { const x = nowAt(); A.pIn = x; if (A.pOut != null && A.pOut <= x) A.pOut = null; drawPanel(); drawMarks(); };
  const pieceOut = () => { const x = nowOut(); A.pOut = x; if (A.pIn != null && A.pIn >= x) A.pIn = null; drawPanel(); drawMarks(); };
  const addPiece = () => { if (A.pIn == null || A.pOut == null || A.pOut <= A.pIn) return; A.pieces = sortPieces([...A.pieces, { start: A.pIn, end: A.pOut }]); A.pIn = A.pOut = null; drawPanel(); drawMarks(); };
  // split at the playhead: the first split cuts the whole file in two, later ones cut whichever piece holds it
  const splitHere = () => {
    const x = nowAt(), d = duration();
    if (!(x > 0 && x < d)) return toast("Move the playhead inside the file to split there", true);
    if (!A.pieces.length) A.pieces = [{ start: 0, end: x }, { start: x, end: d }];
    else {
      const k = A.pieces.findIndex(p => p.start < x && x < p.end);
      if (k < 0) return toast("The playhead isn't inside a piece", true);
      A.pieces = sortPieces([...A.pieces.slice(0, k), { start: A.pieces[k].start, end: x }, { start: x, end: A.pieces[k].end }, ...A.pieces.slice(k + 1)]);
    }
    drawPanel(); drawMarks();
  };
  const onKey = e => {
    if (!alive()) return document.removeEventListener("keydown", onKey);
    if (!A.input || !player || e.ctrlKey || e.metaKey || e.altKey || /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName) || !$("#modal").classList.contains("hidden")) return;
    const seg = t.range === "segments", k = e.key.toLowerCase();
    if (k === "i" && t.range !== "whole") { e.preventDefault(); (seg ? pieceIn : markIn)(); }
    else if (k === "o" && t.range !== "whole") { e.preventDefault(); (seg ? pieceOut : markOut)(); }
    else if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && fps() && player.paused) {
      e.preventDefault(); player.currentTime = (Math.max(0, Math.min(lastFrame(), curFrame() + (e.key === "ArrowLeft" ? -1 : 1))) + 0.1) / fps();   // just inside the frame: it reads back as itself
    }
  };
  document.addEventListener("keydown", onKey);

  /* ---- the tool: what it does, its marks, its settings, Generate */
  const applies = o => !o.when || Object.entries(o.when).every(([k, vs]) => (vs || []).map(String).includes(String(v[k])));
  function option(o) {
    const el = optionEl(o);
    if (o.hint) el.append(h(`<p class="hint ait-ohint">${esc(o.hint)}</p>`));
    return el;
  }
  function optionEl(o) {
    const id = `ait-${o.name}`;
    if (o.type === "checkbox") {
      const el = h(`<div class="toggle ${v[o.name] ? "on" : ""}" role="switch" tabindex="0" aria-checked="${!!v[o.name]}"><span>${esc(o.label)}</span><div class="sw"></div></div>`);
      el.onclick = el.onkeydown = e => { if (e.type === "keydown" && e.key !== " " && e.key !== "Enter") return; e.preventDefault(); v[o.name] = !v[o.name]; saveVals(); drawPanel(); };
      return el;
    }
    if (o.type === "select") {
      const ch = o.choices || [], short = ch.length <= 4 && ch.every(c => String(c.label).length <= 30);
      const el = h(`<div><label class="lbl" for="${id}">${esc(o.label)}</label>${short ? `<div class="chips" id="${id}" role="radiogroup">${ch.map(c => `<div class="chip ${String(c.value) === String(v[o.name]) ? "on" : ""}" role="radio" tabindex="0" aria-checked="${String(c.value) === String(v[o.name])}" data-v="${esc(c.value)}">${esc(c.label)}</div>`).join("")}</div>`
        : `<select id="${id}">${ch.map(c => `<option value="${esc(c.value)}" ${String(c.value) === String(v[o.name]) ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select>`}</div>`);
      const pick = raw => { const hit = ch.find(c => String(c.value) === raw); v[o.name] = hit ? hit.value : raw; saveVals(); drawPanel(); };
      $$(".chip", el).forEach(c => { c.onclick = () => pick(c.dataset.v); c.onkeydown = e => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); pick(c.dataset.v); } }; });
      const sel = $("select", el); if (sel) sel.onchange = () => pick(sel.value);
      return el;
    }
    if (o.type === "slider") {
      const show = x => o.max <= 1 && o.min >= 0 ? `${Math.round(+x * 100)}%` : String(+(+x).toFixed(2));
      const el = h(`<div><label class="lbl" for="${id}">${esc(o.label)} <b class="mono">${show(v[o.name] ?? o.default)}</b></label><input id="${id}" type="range" min="${o.min}" max="${o.max}" step="${o.step || "any"}" value="${v[o.name] ?? o.default}"></div>`);
      const r = $("input", el); r.oninput = () => { v[o.name] = +r.value; saveVals(); $("b", el).textContent = show(r.value); };
      return el;
    }
    if (o.type === "points") return h(`<p class="hint">${esc(o.label || o.name)}: picking points on the picture isn't in Studio yet.</p>`);
    const el = h(`<div><label class="lbl" for="${id}">${esc(o.label)}</label><input id="${id}" type="${o.type === "number" ? "number" : "text"}" value="${esc(v[o.name] ?? "")}"></div>`);
    const inp = $("input", el); inp.onchange = () => { v[o.name] = o.type === "number" ? +inp.value : inp.value; saveVals(); };
    return el;
  }
  function drawPanel() {
    const inp = A.input, f = fps(), misfit = aiFits(t, inp), fitting = inp && misfit ? tools.filter(x => !x.unavailable && !aiFits(x, inp)) : [];
    const section = t.range === "section" && A.inF != null && A.outF != null && A.outF > A.inF, segs = t.range === "segments";
    panel.innerHTML = `<p class="ait-desc">${esc(t.description || "")}</p>
      ${t.unavailable ? `<div class="ait-warn"><b>Not ready on this PC.</b> ${esc(t.unavailable)}</div>` : ""}
      ${misfit ? `<div class="ait-warn">${esc(misfit)}.${fitting.length ? ` This file works with ${fitting.map(x => `<a href="#/aitools/${x.id}">${esc(x.name)}</a>`).join(", ")}.` : ""}</div>` : ""}
      <div class="ait-marks"></div><div class="fsec ait-opts"></div>
      <p class="hint">Saved in the output folder, in Generators › ${esc(t.name)}${t.out && t.out.kind === "dir" ? " (a folder for each run)" : ""}.</p>
      <div class="row"><button class="btn primary" data-go></button></div>`;
    const marks = $(".ait-marks", panel);
    if (t.range === "section" && inp && inp.kind === "video") {
      marks.innerHTML = `<label class="lbl">Section</label><div class="row wrap"><button class="ghost sm" data-in title="Mark In (I)">Mark In <kbd>I</kbd></button><button class="ghost sm" data-out title="Mark Out (O)">Mark Out <kbd>O</kbd></button><button class="ghost sm" data-clear ${A.inF == null && A.outF == null ? "disabled" : ""}>Clear</button></div>
        <p class="mono hint" data-testid="ait-section">${section ? `Section ${aiTimecode(A.inF, f)} → ${aiTimecode(A.outF, f)} · ${A.outF - A.inF} frames` : A.inF != null ? `In ${aiTimecode(A.inF, f)}, now mark Out` : "The whole clip (mark In and Out to do only a section)"}</p>`;
      $("[data-in]", marks).onclick = markIn; $("[data-out]", marks).onclick = markOut;
      $("[data-clear]", marks).onclick = () => { A.inF = A.outF = null; drawPanel(); drawMarks(); };
    } else if (segs && inp && inp.kind !== "image") {
      marks.innerHTML = `<label class="lbl">Pieces</label><div class="row wrap"><button class="ghost sm" data-in title="Mark In (I)">Mark In <kbd>I</kbd></button><button class="ghost sm" data-out title="Mark Out (O)">Mark Out <kbd>O</kbd></button>
          <button class="ghost sm" data-add ${A.pIn == null || A.pOut == null ? "disabled" : ""}>Add piece</button><button class="ghost sm" data-split title="Cut the file (or the piece under the playhead) in two here">Split at playhead</button>
          <button class="ghost sm" data-clear ${!A.pieces.length && A.pIn == null && A.pOut == null ? "disabled" : ""}>Clear</button></div>
        <p class="mono hint" data-testid="ait-marks">${A.pIn != null && A.pOut != null ? `In ${aiClock(A.pIn)} → Out ${aiClock(A.pOut)} · Add piece to keep it` : A.pIn != null ? `In ${aiClock(A.pIn)}, now mark Out`
          : A.pOut != null ? `Out ${aiClock(A.pOut)}, now mark In` : A.pieces.length ? `${plural(A.pieces.length, "piece")}: each becomes its own file` : "Mark In and Out and Add piece, or Split at playhead to cut the whole file in two"}</p>
        ${A.pieces.length ? `<ol class="ait-pieces">${A.pieces.map((p, i) => `<li><span class="mono">${aiClock(p.start)} → ${aiClock(p.end)}</span><span class="hint">${(p.end - p.start).toFixed(2)} s</span><button class="ghost sm" data-rm="${i}" aria-label="Remove piece ${i + 1}">Remove</button></li>`).join("")}</ol>` : ""}`;
      $("[data-in]", marks).onclick = pieceIn; $("[data-out]", marks).onclick = pieceOut; $("[data-add]", marks).onclick = addPiece; $("[data-split]", marks).onclick = splitHere;
      $("[data-clear]", marks).onclick = () => { A.pieces = []; A.pIn = A.pOut = null; drawPanel(); drawMarks(); };
      $$("[data-rm]", marks).forEach(b => b.onclick = () => { A.pieces.splice(+b.dataset.rm, 1); drawPanel(); drawMarks(); });
    }
    const opts = $(".ait-opts", panel);
    (t.options || []).filter(applies).forEach(o => opts.append(option(o)));
    if (!opts.children.length) opts.remove();
    const go = $("[data-go]", panel), running = jobs.some(j => j.status === "running");
    go.textContent = busy ? "Working…" : segs ? (A.pieces.length ? `Cut ${plural(A.pieces.length, "piece")}` : "Cut") : section ? "Generate the section" : "Generate";
    if (running && !busy) go.textContent += " (after the one running)";
    go.disabled = busy || !inp || !!t.unavailable || !!misfit || (segs && !A.pieces.length);
    go.title = !inp ? "Give it a file first" : t.unavailable ? "Not ready on this PC" : misfit || (segs && !A.pieces.length ? "Add at least one piece" : "");
    go.onclick = generate;
  }
  async function generate() {
    const inp = A.input; if (!inp) return;
    const options = {}; (t.options || []).filter(o => applies(o) && o.type !== "points" && v[o.name] !== undefined).forEach(o => options[o.name] = v[o.name]);
    const b = { tool: t.id, input_id: inp.id, options };
    if (t.range === "section" && A.inF != null && A.outF != null && A.outF > A.inF) Object.assign(b, { start: A.inF, end: A.outF });
    if (t.range === "segments") b.segments = A.pieces.map(p => [+p.start.toFixed(6), +p.end.toFixed(6)]);
    busy = true; drawPanel();
    try { const r = await post(GEN + "/jobs", b); toast(`${t.name}: ${r.job.status === "queued" ? "waiting its turn" : "started"}`); await loadJobs(); }
    catch (e) { toast(aiErr(e), true); }
    finally { busy = false; drawPanel(); }
  }

  /* ---- jobs: the one running, and the history */
  const pctOf = j => j.total ? Math.min(100, Math.round(100 * j.done / j.total)) : j.status === "done" ? 100 : 0;
  const outUrl = (j, n, preview) => `${GEN}/jobs/${j.id}/output/${n}${preview ? "/preview" : ""}`;
  const where = j => [j.input_name, j.start != null ? `frames ${j.start}–${j.end - 1}` : "", j.segments ? plural(j.segments.length, "piece") : ""].filter(Boolean).join(" · ");
  async function loadJobs() {
    try { jobs = (await api(GEN + "/jobs")).jobs || []; } catch { return; }
    if (!alive()) return;
    drawNow(); drawJobs(); drawPanel();
    clearTimeout(S.aiPoll);
    if (jobs.some(aiActive)) S.aiPoll = setTimeout(loadJobs, 1500);
  }
  const cancel = async id => { try { await post(`${GEN}/jobs/${id}/cancel`, {}); } catch (e) { toast(aiErr(e), true); } loadJobs(); };
  function drawNow() {
    const cur = jobs.find(j => j.status === "running") || jobs.find(j => j.status === "queued");
    now.classList.toggle("hidden", !cur); if (!cur) return;
    now.innerHTML = `<div class="row"><b>${esc(cur.tool_name)}</b><span class="hint">${esc(where(cur))}</span><span class="sp"></span><button class="ghost sm" data-x>Cancel</button></div>
      <div class="ait-prog big" role="progressbar" aria-label="The job running" aria-valuenow="${pctOf(cur)}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pctOf(cur)}%"></i></div>
      <span class="mono hint">${esc(cur.status === "queued" ? "Waiting to start" : cur.stage)}${cur.total ? ` · ${cur.done} / ${cur.total}` : ""} · ${pctOf(cur)}%</span>`;
    $("[data-x]", now).onclick = () => cancel(cur.id);
  }
  function drawJobs() {
    jobsEl.innerHTML = jobs.length ? "" : `<li class="empty">Nothing made yet.</li>`;
    jobs.forEach(j => {
      const li = h(`<li class="ait-job ${j.status}"><div class="row"><b>${esc(j.tool_name)}</b><span class="hint">${esc(where(j))}</span><span class="sp"></span>
          <span class="ait-status ${j.status}">${esc(j.status === "running" ? j.stage : AI_STATUS[j.status] || j.status)}</span>${aiActive(j) ? `<button class="ghost sm" data-x>Cancel</button>` : ""}</div>
        ${aiActive(j) ? `<div class="ait-prog" role="progressbar" aria-valuenow="${pctOf(j)}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pctOf(j)}%"></i></div>` : ""}
        ${j.error ? `<p class="ait-err">${esc(j.error)}</p>` : ""}${j.note ? `<p class="hint">${esc(j.note)}</p>` : ""}
        ${j.stats && Object.keys(j.stats).length ? `<p class="mono ait-stats">${esc(aiStats(j.stats))}</p>` : ""}
        ${(j.outputs || []).length ? `<ul class="ait-outs">${j.outputs.map((o, n) => { const pc = (j.pieces || []).find(x => x.file === o.name); const key = `${j.id}:${n}`;
          return `<li><div class="row wrap"><span class="mono" title="${esc(o.subfolder + "/" + o.name)}">${esc(o.name)}</span>${pc ? `<span class="hint" data-testid="ait-piece-info" title="${esc(pc.note || "")}">${esc(aiPieceInfo(pc))}${pc.note ? " · " + esc(pc.note) : ""}</span>` : ""}<span class="sp"></span>
            ${["video", "audio", "image"].includes(o.kind) ? `<button class="ghost sm" data-play="${n}">${playing === key ? "Hide" : o.kind === "image" ? "View" : "Play"}</button>` : o.kind === "text" ? `<a class="ghost sm" href="${outUrl(j, n)}" target="_blank" rel="noreferrer">Open</a>` : ""}
            ${["video", "image"].includes(o.kind) ? `<button class="ghost sm" data-gallery="${n}" title="Open it in the Gallery's viewer">Gallery</button>` : ""}
            <a class="ghost sm" href="${outUrl(j, n)}" download="${esc(o.name)}">Download</a></div>
            ${playing === key ? (o.kind === "audio" ? `<audio src="${outUrl(j, n, true)}" controls autoplay></audio>` : o.kind === "image" ? `<a href="${outUrl(j, n)}" target="_blank" rel="noreferrer"><img class="ait-outplayer" src="${outUrl(j, n, true)}" alt="${esc(o.name)}"></a>` : `<video class="ait-outplayer" src="${outUrl(j, n, true)}" controls autoplay playsinline></video>`) : ""}</li>`; }).join("")}</ul>` : ""}</li>`);
      const x = $("[data-x]", li); if (x) x.onclick = () => cancel(j.id);
      $$("[data-play]", li).forEach(b => b.onclick = () => { const key = `${j.id}:${b.dataset.play}`; playing = playing === key ? null : key; drawJobs(); });
      $$("[data-gallery]", li).forEach(b => b.onclick = () => { const o = j.outputs[+b.dataset.gallery]; const it = { filename: o.name, subfolder: o.subfolder, kind: o.kind, size: o.size || 0, mtime: Date.now() / 1000 }; lightbox(it, [it]); });
      jobsEl.append(li);
    });
  }

  drawDrop(); drawPanel();
  if (S.aiPick) { const it = S.aiPick; S.aiPick = null; register(it); }
  else if (A.input) api(`${GEN}/inputs/${A.input.id}/info`).catch(() => setInput(null));   // still there (a restart, a file moved)?
  else if (store.get("aiInput")) api(`${GEN}/inputs/${store.get("aiInput")}/info`).then(r => { if (alive() && !A.input) setInput(r.input); }).catch(() => store.set("aiInput", null));
  loadJobs();
}
/* a file from the output folder: the Gallery's videos and pictures, and sounds (Music & voices' songs) */
async function aiPickDialog(done) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog wide ait-pick"><div class="row" style="justify-content:space-between"><h2>Choose a file</h2><button class="icon" data-x>✕</button></div>
    <div class="chips" role="tablist"><div class="chip on" data-k="video">Videos</div><div class="chip" data-k="image">Pictures</div><div class="chip" data-k="audio">Sounds</div></div>
    <div class="ait-pgrid"><div class="hint">Reading the Gallery…</div></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; m.onclick = null; };
  $("[data-x]", d).onclick = close; m.onclick = e => { if (e.target === m) close(); };
  let files = [];
  try { files = (await api(GEN + "/files")).files || []; } catch (e) { $(".ait-pgrid", d).innerHTML = `<div class="hint">${esc(aiErr(e))}</div>`; return; }
  const draw = kind => {
    $$(".chip", d).forEach(c => c.classList.toggle("on", c.dataset.k === kind));
    const list = files.filter(f => f.kind === kind), g = $(".ait-pgrid", d);
    g.innerHTML = list.length ? "" : `<div class="hint">No ${kind === "audio" ? "sounds" : kind === "image" ? "pictures" : "videos"} in the output folder yet.</div>`;
    list.slice(0, 300).forEach(f => {
      const el = h(`<button class="ait-pfile" title="${esc((f.subfolder ? f.subfolder + "/" : "") + f.filename)}">${kind === "audio" ? `<span class="ait-pic">♪</span>` : `<img src="${thumbUrl(f, 640)}" loading="lazy" alt="">`}<span>${esc(f.filename)}</span><small>${esc(f.subfolder || "")}</small></button>`);
      el.onclick = () => { close(); done(f); };
      g.append(el);
    });
  };
  $$(".chip", d).forEach(c => c.onclick = () => draw(c.dataset.k));
  draw("video");
}

/* ------------------------------------------------------------ CHARACTERS */
// the character sheet: what they look like, field by field, composed into one description for every generator
// (Create, project keyframes, storyboard prompts, reference pictures; the same as projects.char_look on the server)
const CHAR_FIELDS = [["age", "Age", "57"], ["gender", "Gender", "man"], ["build", "Build & height", "tall, heavy-set"], ["skin", "Skin", "weathered, tanned"],
  ["features", "Face", "square jaw, deep lines around the eyes"], ["eyes", "Eyes", "grey"], ["hair", "Hair", "short, grey, receding"], ["facial_hair", "Facial hair", "three-day stubble"],
  ["marks", "Distinguishing marks", "scar through the left eyebrow"], ["wardrobe", "Wardrobe", "tan canvas work jacket over a red flannel shirt"], ["accessories", "Accessories & props", "felt hat, wedding ring"]];
const NUMBER_AGE = /^(\d{1,3}|(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(-(one|two|three|four|five|six|seven|eight|nine))?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)$/i;
function charLook(c) {
  const g = k => String(c[k] || "").trim();
  const age = g("age") ? g("age") + (NUMBER_AGE.test(g("age")) ? "-year-old" : "") : "";
  const with_ = (v, word, re) => v && !re.test(v) ? v + " " + word : v;
  return [[age, g("gender")].filter(Boolean).join(" "), g("build"), with_(g("skin"), "skin", /skin/i), g("features"), with_(g("eyes"), "eyes", /eye/i),
    with_(g("hair"), "hair", /hair|bald|shaved/i), g("facial_hair"), g("marks"), g("wardrobe") ? (/^(wearing|in|dressed)\b/i.test(g("wardrobe")) ? "" : "wearing ") + g("wardrobe") : "",
    g("accessories"), g("look").replace(/\.$/, "")].map(x => x.trim().replace(/,$/, "")).filter(Boolean).join(", ");
}
// what a prompt says about a character: the LoRA's trigger word first (it's what calls up the LoRA), then the look
const charDesc = c => [String(c.trigger || "").trim(), charLook(c)].filter(Boolean).join(", ");
async function loadChars() { try { S.chars = await api("/studio/api/characters" + PQ()); } catch { S.chars = []; } return S.chars; }
async function pageCharacters(main) {
  const p = h(`<div class="page"><div class="eyebrow">Pre-production · Design</div><h1>Characters</h1>
    <p class="muted" style="max-width:640px">A character is a face, a look and (for shots) a voice. Pick one in Create and it goes into the prompt, the reference image and the voice slot for you.</p>
    <div class="cgrid" id="cg" style="margin-top:22px"></div></div>`);
  main.append(p);
  await loadChars();
  const g = $("#cg", p);
  const nc = h(`<div class="ccard newcard"><div class="plus">+</div><h3>New character</h3><span class="hint">Face, look and voice</span></div>`);
  nc.onclick = () => editChar(null); g.append(nc);
  S.chars.forEach(c => {
    const el = h(`<div class="ccard"><div class="face" style="${c.face ? `background-image:url('${inputUrl(c.face)}')` : ""}">${c.face ? "" : `<div class="noface">${esc(c.name[0] || "?")}</div>`}</div>
      <div class="body"><h3>${esc(c.name)}</h3><p>${esc(charLook(c) || "No description yet")}</p>
      <div class="acts"><button class="ghost" data-a="image">Picture</button><button class="ghost" data-a="video">Shot</button><button class="ghost" data-a="edit">Edit</button></div></div></div>`);
    $$("[data-a]", el).forEach(b => b.onclick = () => {
      if (b.dataset.a === "edit") return editChar(c);
      const k = S.apps.findIndex(a => a.kind === b.dataset.a); if (k < 0) return;
      selectCharacter(S.apps[k], c); openApp(k);
    });
    g.append(el);
  });
}
function editChar(c, after) {
  c = c ? { ...c } : { name: "", look: "", trigger: "", face: "", voice: "" };
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog chard"><div class="row" style="justify-content:space-between"><h2>${c.id ? "Character sheet" : "New character"}</h2><button class="icon" data-x>✕</button></div>
    <div class="csheet">
      <div class="cside">
        <label class="lbl">Face</label><div class="ref ${c.face ? "on" : ""}" id="cf" style="aspect-ratio:4/5">${c.face ? `<img src="${inputUrl(c.face)}">` : `<span class="plus">+</span>`}<span class="rl">Drop a clear face photo</span><input type="file" hidden accept="image/*"></div>
        <button class="btn" id="cport" title="A picture of the character made from this sheet with the Image app">✦ Make a reference picture</button>
        <div class="hint" id="cports"></div>
        <div class="cpics" id="cpics"></div>
        <label class="lbl">Voice (for shots)</label><div class="ref audio ${c.voice ? "on" : ""}" id="cv" style="aspect-ratio:auto;min-height:74px"><span class="${c.voice ? "wave" : "plus"}">${c.voice ? "♪" : "+"}</span><span class="rl">${c.voice ? esc(c.voice.split("/").pop()) : "Drop a voice sample"}</span><input type="file" hidden accept="audio/*,video/*"></div>
      </div>
      <div class="cmain">
        <div><label class="lbl">Name</label><input type="text" id="cn" placeholder="Ray Mercer"></div>
        <label class="lbl" style="margin-top:6px">How they look</label>
        <div class="cfields">${CHAR_FIELDS.map(([k, l, ph]) => `<div class="${k === "wardrobe" || k === "features" ? "wide" : ""}"><span>${l}</span><input type="text" data-f="${k}" placeholder="${esc(ph)}"></div>`).join("")}</div>
        <div><label class="lbl">Anything else about how they look</label><textarea id="cl" rows="2" placeholder="the way they carry themselves, the era, the style of the picture…"></textarea></div>
        <div class="cgets"><b>What the generator gets</b><div id="cdesc"></div></div>
        <details class="clora" ${c.lora || c.trigger ? "open" : ""}><summary>LoRA for this character (optional)</summary>
          <p class="hint">A LoRA trained on this person keeps their face the same from picture to picture. Pick it here and it's loaded with every render of this character (in Create and the project's keyframes), with its trigger word at the start of the description. Only LoRAs made for the model in use are loaded.</p>
          <div class="row wrap"><select id="clr"><option value="">No LoRA</option></select><label class="hint">Strength <input type="number" id="cls" min="0" max="2" step="0.05" style="width:70px"></label></div>
          <div><label class="lbl">Trigger word</label><input type="text" id="ct" placeholder="the word the LoRA was trained on, e.g. rmrc_man"></div></details>
      </div>
    </div>
    <div class="foot">${c.id ? `<button class="ghost danger" data-del style="margin-right:auto">Delete</button>` : ""}<button class="ghost" data-x>Cancel</button><button class="btn primary" data-save>Save</button></div></div>`);
  m.append(d);
  $("#cn", d).value = c.name; $("#cl", d).value = c.look || ""; $("#ct", d).value = c.trigger || "";
  $$("[data-f]", d).forEach(i => { i.value = c[i.dataset.f] || ""; });
  const read = () => { c.name = $("#cn", d).value.trim(); c.look = $("#cl", d).value.trim(); c.trigger = $("#ct", d).value.trim(); $$("[data-f]", d).forEach(i => { c[i.dataset.f] = i.value.trim(); }); };
  const preview = () => { read(); $("#cdesc", d).textContent = [c.name, charDesc(c)].filter(Boolean).join(": ") || "Fill in the sheet: this is the text every picture and shot of them gets."; };
  $$("input, textarea", d).forEach(i => i.addEventListener("input", preview)); preview();
  // the LoRA: picked from the installed ones; its trained words become the trigger
  const lsel = $("#clr", d);
  $("#cls", d).value = c.lora?.strength ?? 0.8;
  loadLoras().then(list => {
    list.forEach(l => lsel.append(new Option(`${l.name}${l.base ? " · " + l.base : ""}`, l.file, false, c.lora?.name === l.file)));
    if (c.lora?.name && !list.some(l => l.file === c.lora.name)) lsel.append(new Option(c.lora.name + " (not installed)", c.lora.name, false, true));
    lsel.onchange = () => {
      const l = list.find(x => x.file === lsel.value);
      if (l && !$("#ct", d).value.trim() && (l.trained || []).length) { $("#ct", d).value = l.trained[0]; preview(); }
    };
  });
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(b => b.onclick = close);
  const setFace = val => { c.face = val; const el = $("#cf", d); el.classList.add("on"); $$("img", el).forEach(x => x.remove()); el.prepend(h(`<img src="${inputUrl(val)}">`)); pics(); };
  const pics = () => {
    const box = $("#cpics", d); box.innerHTML = "";
    (c.pictures || []).forEach(pv => { const t = h(`<div class="cpic ${pv === c.face ? "on" : ""}" title="${pv === c.face ? "The face" : "Use as the face"}"><img src="${inputUrl(pv)}"></div>`); t.onclick = () => setFace(pv); box.append(t); });
  };
  pics();
  const wire = (id, field, isAudio) => {
    const el = $(id, d), fi = $("input", el);
    el.onclick = () => fi.click();
    const take = async f => {
      const val = await upload(f); c[field] = val; el.classList.add("on");
      if (isAudio) { $(".plus,.wave", el).className = "wave"; $(".wave", el).textContent = "♪"; $(".rl", el).textContent = f.name; }
      else setFace(val);
    };
    fi.onchange = () => fi.files[0] && take(fi.files[0]);
    el.ondragover = e => e.preventDefault();
    el.ondrop = e => { e.preventDefault(); e.dataTransfer.files[0] && take(e.dataTransfer.files[0]); };
  };
  wire("#cf", "face", false); wire("#cv", "voice", true);
  const collect = () => {
    read();
    const l = lsel.value;
    c.lora = l ? { name: l, strength: +$("#cls", d).value || 0.8, family: loraFamily((loraCache || []).find(x => x.file === l)?.base), trained: (loraCache || []).find(x => x.file === l)?.trained || [] } : undefined;
  };
  const persist = async () => { collect(); if (!c.name) throw new Error("Give your character a name"); c.private = c.private ?? S.incog; const saved = await post("/studio/api/characters", c); Object.assign(c, saved); await loadChars(); return c; };
  // a reference picture from the sheet: the Image app with the whole description (and the face, if there is one, to keep it)
  $("#cport", d).onclick = async () => {
    const k = findApp("image"); if (k < 0) return toast("The Image app is missing", true);
    const btn = $("#cport", d); btn.disabled = true;
    try {
      await persist();
      const app = S.apps[k], L = layout(app), v = { ...valuesFor(app) }, ref = L.refs.find(r => r.media.kind === "image");
      const fam = L.model ? modelFamily(v[L.model.key]) : "";
      const loras = c.lora && !(c.lora.family && fam && c.lora.family !== fam) ? [{ name: c.lora.name, strength: c.lora.strength }] : [];
      const prompt = `Character reference portrait of ${c.name}: ${charDesc(c)}. Head and shoulders, facing the camera, plain light grey background, soft even studio light, sharp focus, natural skin texture, film still.`;
      const r = await post(`/studio/api/characters/${c.id}/portrait`, { values: v, prompt, prompt_key: L.prompt?.key, ref: c.face || "", ref_key: ref?.media.key, ref_toggle_key: ref?.toggle?.key, seed_keys: app.inputs.filter(i => i.kind === "seed").map(i => i.key), loras, target: runTarget() });
      const res = await pollJob(r.id, st => { const e = $("#cports", d); if (e) e.textContent = st + "…"; });
      await loadChars(); const fresh = S.chars.find(x => x.id === c.id); if (fresh) { c.pictures = fresh.pictures; if (!c.face) setFace(res.picture); }
      pics(); const e = $("#cports", d); if (e) e.textContent = "Click a picture to make it the face.";
    } catch (e) { toast(e.message.slice(0, 200), true); } finally { btn.disabled = false; }
  };
  $("[data-save]", d).onclick = async () => {
    try { await persist(); close(); toast("Saved " + c.name); if (after) after(c); else route(); } catch (e) { toast(e.message, true); }
  };
  const del = $("[data-del]", d);
  if (del) del.onclick = async () => { if (!confirm("Delete " + c.name + "?")) return; await api("/studio/api/characters/" + c.id, { method: "DELETE" }); await loadChars(); close(); route(); };
}


/* ------------------------------------------------------------ STORYBOARD */
S.boards = []; S.boardId = store.get("board", null);
async function loadBoards() { try { S.boards = await api("/studio/api/boards" + PQ()); } catch { S.boards = []; } return S.boards; }
const boardSaves = {};
function saveBoard(b, now) {
  clearTimeout(boardSaves[b.id || "new"]);
  if (b.private == null) b.private = S.incog;
  const go = async () => {
    try { const r = await post("/studio/api/boards", b); if (!b.id) b.id = r.id; b.updated = r.updated; b.by = r.by; }
    catch (e) { if (e.data && e.data.error === "changed") return takeAiChange("board", e.data.board); throw e; }
  };
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

// a shot's prompt and its camera move as one text: "…late afternoon. Static locked-off camera."
const withCamera = (prompt, camera) => {
  const p = (prompt || "").trim(), c = (camera || "").trim();
  if (!c) return p;
  const cam = c.charAt(0).toUpperCase() + c.slice(1) + (/[.!?]$/.test(c) ? "" : ".");
  return p ? (/[.!?"”…]$/.test(p) ? p + " " : p + ". ") + cam : cam;
};
// the Video app's duration for a shot of this length: the shortest one that holds it (the cut trims it to the shot)
const renderLength = (durIn, secs) => durIn.options.find(x => parseFloat(x) >= secs - 0.2) || durIn.options[durIn.options.length - 1];
async function renderShot(b, s, o = {}) {
  ensureTakes(s);
  const vs = videoSummary(); if (!vs) return toast("The Video app is missing", true);
  const app = vs.app, L = layout(app), vals = { ...valuesFor(app) };
  if (L.prompt) vals[L.prompt.key] = withCamera(s.prompt, s.camera);
  let first = true;
  const durIn = app.inputs.find(i => /^duration/i.test(i.label));
  if (durIn && s.seconds) vals[durIn.key] = renderLength(durIn, s.seconds);
  if (!s.image && L.model && /first frame\)|image 1 = first frame/i.test(vals[L.model.key] || "") && !/text only/i.test(vals[L.model.key] || "")) {
    toast(`${splitOption(vals[L.model.key])[0]} needs a first frame: add one to the shot, or pick “MiniMax H3 First frame (or text only)” in the Video app.`, true);
    return;
  }
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
    const res = await post("/studio/api/run", { app: app.id, values: vals, count: o.count || 1, client_id: clientId, random_seed_keys: seedKeys, private: !!b.private, loras, target: runTarget() });
    res.queued.forEach(q => {
      const seed = o.seed != null ? o.seed : (K.seed ? (q.seed || [])[seedKeys.indexOf(K.seed.key)] : null);
      s.takes.push({ pid: q.prompt_id, seed: seed ?? null, q: o.q || "", status: "queued", output: null, from: o.from ?? null, prompt: s.prompt });
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
      const r = await post("/studio/api/ai/prompt", { idea, kind: "video", private: S.incog });
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
  const key = e => { if (!d.isConnected) { cancelAnimationFrame(raf); document.removeEventListener("keydown", key); return; } if (e.key === "Escape") close(); else if (e.key === " ") { e.preventDefault(); toggle(); } };
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
  if (!tzCache || force) tzCache = await api("/studio/api/topaz/status" + (force ? "?force=1" : "")).catch(() => null);
  return tzCache;
}
const tzReady = (t, kind) => !!(t && t.prefs && t.prefs.use && t[kind] && t[kind].ready);
const tzApp = (t, kind) => !!(t && t.apps && t.apps[kind] && t.apps[kind].exe);
const isTopaz = it => /topaz|gigapixel|[-_ ](prob|rhea|iris|apo|chr|ahq|nyx|proteus|apollo|chronos)-?\d/i.test(it.filename || "");
async function topazHandoff(cur) {
  const kind = cur.kind === "video" ? "video" : "image", app = kind === "video" ? "Topaz Video" : "Gigapixel";
  if (cur.private) return toast("Incognito files can't go to the Topaz app", true);
  try {
    const r = await post("/studio/api/topaz/open", { filename: cur.filename, subfolder: cur.subfolder || "" });
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
      const g = await api("/studio/api/gallery"); const ups = (g.items || g).filter(x => /Upscaled/.test(x.subfolder || "")).map(x => x.filename).join("|");
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
        const r = await api("/studio/api/job/" + j.id);
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
  $$("[data-s]", el).forEach(b => b.onclick = () => post(`/studio/api/finish/${b.dataset.s}/stop`, {}).then(() => toast("Stopping…")));
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
      const r = await post("/studio/api/topaz/" + kind, { filename: cur.filename, subfolder: cur.subfolder || "", private: !!cur.private, size: st.size === "keep" ? "" : st.size, fps: st.fps, model: st.model });
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
    try { await videoJob("/studio/api/video/fix", body, st.mode === "face" ? "Face fix" : "Fix"); } catch (e) { toast(e.message.slice(0, 260), true); }
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
    try { await videoJob("/studio/api/video/extend", { filename: cur.filename, subfolder: cur.subfolder || "", prompt: pr, seconds: st.seconds, ref: st.engine === "h3" ? st.ref : "", engine: st.engine, target: runTarget() }, "Continue shot"); }
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
    try { await videoJob("/studio/api/video/keyframes", body, "Keyframe shot"); } catch (e) { toast(e.message.slice(0, 260), true); }
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
      const r = await post("/studio/api/finish", { ...t.output, private: !!b.private, upscale: st.upscale, size: st.size, fps: st.fps, sound: st.sound, audio, name: `${b.name} shot ${n + 1}`, target: runTarget() });
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
      const j = await api("/studio/api/finish/" + s.finish.id);
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
  /* a storyboard that belongs to a project lives in the project (its Storyboard step); this page is for the others
     (boards made from the Gallery or by Claude) */
  await loadBoards();
  if (!S.boards.length) { const b = { name: "My film", shots: [newShot(), newShot()] }; S.boards.push(b); await saveBoard(b, true); }
  let b = curBoard(); S.boardId = b.id; store.set("board", b.id);
  const owner = (await api("/studio/api/projects" + PQ()).catch(() => [])).find(x => x.id === b.project || x.board === b.id)?.id;
  if (owner) { location.replace(`#/projects/${owner}/takes`); return; }
  const p = h(`<div class="page"><div class="eyebrow">Production · <a href="#/projects">Projects</a> › Storyboard</div>
    <div class="row sbtop"><h1 id="bname" contenteditable="true" spellcheck="false">${esc(b.name)}</h1></div>
    <div class="filters"><select id="bsel"></select><button class="ghost" id="bnew">＋ New film</button><button class="ghost danger" id="bdel">Delete film</button></div>
    <div class="sbwrap"></div></div>`);
  main.append(p);
  S.boards.forEach(x => $("#bsel", p).append(new Option(x.name, x.id, false, x.id === b.id)));
  $("#bsel", p).onchange = e => { S.boardId = e.target.value; store.set("board", S.boardId); route(); };
  $("#bnew", p).onclick = async () => { const nb = { name: "Film " + (S.boards.length + 1), shots: [newShot()] }; S.boards.push(nb); await saveBoard(nb, true); S.boardId = nb.id; store.set("board", nb.id); route(); };
  $("#bdel", p).onclick = async () => { if (!confirm(`Delete “${b.name}”? The rendered shots stay in your gallery.`)) return; await api("/studio/api/boards/" + b.id, { method: "DELETE" }); S.boardId = null; route(); };
  $("#bname", p).oninput = e => { b.name = e.target.textContent.trim() || "Untitled"; saveBoard(b); };
  await boardView($(".sbwrap", p), b);
}
/* the board itself: shots with their first frame, prompt, takes, pick, final render, finish, and the film.
   opts.sceneOf(shot) -> a scene label: the shots are grouped under their scenes (a project's board) */
const clipOf = s => (s.final && s.finish && s.finish.from === s.pick) ? s.final : s.output;
async function boardView(wrap, b, opts = {}) {
  b.shots.forEach(ensureTakes);
  if (b.takes == null) b.takes = 2;
  const p = h(`<div class="sbview">
    <div class="howto"><b>1</b> Draft takes (fast) <i>→</i> <b>2</b> Pick the best ★ <i>→</i> <b>3</b> Render it in final quality <i>→</i> <b>4</b> Finish: 1080p, smoother motion, sound <i>→</i> <b>5</b> Make the film</div>
    <p class="hint" id="vsum"></p>
    <div id="sbchg"></div>
    <div class="shots" id="shots"></div>
    <div class="row sbbar"><button class="btn" id="add">＋ Add shot</button><label class="hint">Takes per shot <select id="ntakes">${[1, 2, 3, 4].map(k => `<option ${k === b.takes ? "selected" : ""}>${k}</option>`).join("")}</select></label><button class="btn" id="all">Draft every shot without takes</button><button class="btn primary" id="film">🎬 Make the film</button></div>
    <div id="filmout"></div></div>`);
  wrap.append(p);
  $("#ntakes", p).onchange = e => { b.takes = +e.target.value; saveBoard(b); };
  const vs = videoSummary();
  $("#vsum", p).innerHTML = !vs ? "" : opts.project
    ? `The shots of the breakdown, each rendered at its length (the cut trims it to the second) with its camera; the model, shape and quality are the Video app's: <b>${esc(vs.text.split(" · ").filter(x => !/\d+ s$/.test(x)).join(" · "))}</b> · <a href="#/create/${vs.k}">change</a>`
    : `Shots use the Video app's settings: <b>${esc(vs.text)}</b> · <a href="#/create/${vs.k}">change</a>`;
  await resolveShots(b);
  const box = $("#shots", p);
  const tstat = t => t.output ? "" : t.pid && S.running === t.pid ? `${Math.round(S.progress * 100)}%` : t.status === "queued" ? "Waiting" : t.status === "failed" ? "Failed" : t.status === "lost" ? "Stopped" : "";
  const draw = () => {
    if (!document.body.contains(box)) { S.sbDraw = null; return; }
    box.innerHTML = "";
    let lastScene = null;
    b.shots.forEach((s, n) => {
      ensureTakes(s);
      if (opts.sceneOf) { const lab = opts.sceneOf(s); if (lab !== lastScene) { lastScene = lab; if (lab) box.append(h(`<div class="sbscene">${esc(lab)}</div>`)); } }
      const pk = s.takes[s.pick], showFinal = s.final && s.finish && s.finish.from === s.pick;
      const main = showFinal ? s.final : pk && pk.output;
      const pending = s.takes.find(t => !t.output && (t.status === "queued"));
      const fin = s.finish && s.finish.status === "running" ? `<div class="fbadge run">Finishing: ${esc(s.finish.stage)}…</div>` : showFinal ? `<div class="fbadge">✓ Finished</div>` : s.finish && s.finish.status === "failed" ? `<div class="fbadge bad" title="${esc(s.finish.error || "")}">Finish failed</div>` : "";
      const el = h(`<div class="shot" data-shot="${s.id}"><div class="shead"><b>SHOT ${esc(opts.code ? opts.code(s) : n + 1)}</b>${opts.project && s.title ? `<span class="stitle">${esc(s.title)}</span>` : ""}${opts.project ? `<span class="ssecs ${s.seconds > longestTake() ? "long" : ""}" title="${s.seconds > longestTake() ? `Longer than one take (${longestTake()} s): draft the start here, then Continue → for the rest` : "Screen time"}">${s.seconds} s${s.seconds > longestTake() ? " · in parts" : ""}</span>` : ""}${opts.project && s.auto && s.prompt !== s.auto ? `<button class="ghost sm sedited" data-reset title="The prompt was changed here: use the one from the breakdown again">✎ edited · ↺ breakdown</button>` : ""}<div class="row">${!opts.project || opts.project.canMove(s) ? `<button class="icon" data-up title="Move up">↑</button><button class="icon" data-dn title="Move down">↓</button>` : ""}${!opts.project || opts.project.canRemove(s) ? `<button class="icon" data-rm title="${opts.project && !opts.project.isAdded(s) ? "Remove this setup: its beats join the shot before" : "Remove"}">✕</button>` : ""}</div></div>
        <div class="sbody"><div class="ref ${s.image ? "on" : ""} sframe">${s.image ? `<img src="${inputUrl(s.image)}">` : `<span class="plus">+</span>`}<span class="rl">${s.image ? (s.cont ? "Last frame of shot " + s.cont : "First frame") : s.cont && opts.project ? `Starts on ${esc(s.cont)}'s last frame:<br>Continue → on ${esc(s.cont)}` : "First frame (optional)"}</span><button class="x" title="Remove">✕</button><input type="file" hidden accept="image/*"></div>
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
      const fr = $(".sframe", el), fi = $("input", fr);
      const take = async f => { s.image = await upload(f); s.cont = null; if (opts.project) opts.project.frame(s, s.image); saveBoard(b); draw(); };
      fr.onclick = e => { if (e.target.closest(".x")) return; fi.click(); };
      fi.onchange = () => fi.files[0] && take(fi.files[0]);
      fr.ondragover = e => e.preventDefault(); fr.ondrop = e => { e.preventDefault(); e.dataTransfer.files[0] && take(e.dataTransfer.files[0]); };
      $(".x", fr).onclick = () => { s.image = ""; s.cont = null; if (opts.project) opts.project.frame(s, ""); saveBoard(b); draw(); };
      const ta = $("textarea", el); ta.oninput = e => { s.prompt = e.target.value; saveBoard(b); };
      ta.onchange = () => { if (opts.project) draw(); };
      const rs = $("[data-reset]", el); if (rs) rs.onclick = () => { s.prompt = s.auto; saveBoard(b, true); draw(); };
      $(".scam", el).onchange = e => { s.camera = e.target.value; saveBoard(b); if (opts.project) opts.project.camera(s, e.target.value); };
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
        try { const r = await post(`/studio/api/boards/${b.id}/shots/${s.id}/final_request`, { engine: on ? "none" : "seedance", notes }); s.final_request = r.final_request || undefined; draw(); toast(on ? "Request cancelled" : "Requested. In Claude, say: “do the Seedance final renders in Studio”."); }
        catch (e) { toast(e.message.slice(0, 200), true); }
      };
      const rb = $("[data-rev]", el); if (rb) rb.onclick = () => reviewModal(b, s, pk, v => { s.prompt = v; saveBoard(b, true); draw(); toast("Prompt updated. Draft new takes when ready."); });
      $("[data-tin]", el).oninput = e => { s.trimIn = +e.target.value || 0; saveBoard(b); };
      $("[data-tout]", el).oninput = e => { s.trimOut = +e.target.value || 0; s.trimSet = true; saveBoard(b); };
      $("[data-cap]", el).oninput = e => { s.caption = e.target.value; saveBoard(b); };
      const cb = $("[data-cont]", el); if (cb) cb.onclick = async () => {
        cb.disabled = true; cb.textContent = "Grabbing last frame…";
        try {
          const { value } = await post("/studio/api/lastframe", { ...(showFinal ? s.final : pk.output), private: !!b.private });
          if (opts.project) {
            const r = await opts.project.insertAfter(s, value); draw();
            toast(r.chained ? `Shot ${r.code}, the next move of the take, now starts on ${opts.code(s)}'s last frame.` : `Shot ${r.code} starts where shot ${opts.code(s)} ends. Say what happens next in its prompt (or in Scenes & shots).`);
            return;
          }
          const ns = newShot(s.prompt, value); ns.camera = s.camera; ns.cont = n + 1;
          b.shots.splice(n + 1, 0, ns); saveBoard(b, true); draw(); toast(`Shot ${n + 2} starts where shot ${n + 1} ends. Change its prompt to say what happens next.`);
        } catch (e) { toast("Couldn't read the last frame: " + e.message.slice(0, 200), true); cb.disabled = false; cb.textContent = "Continue →"; }
      };
      if (opts.project) {                              // the shot list is the breakdown's: these change it there
        const rm = $("[data-rm]", el), up = $("[data-up]", el), dn = $("[data-dn]", el);
        if (rm) rm.onclick = async () => { if (!confirm(opts.project.isAdded(s) ? `Remove shot ${opts.code(s)}?${s.takes.some(t => t.output) ? " (Its takes stay in your gallery.)" : ""}` : `Remove setup ${opts.code(s)}? Its action and lines stay in the screenplay and join the shot before.`)) return; await opts.project.remove(s); draw(); };
        if (up) up.onclick = async () => { await opts.project.move(s, -1); draw(); };
        if (dn) dn.onclick = async () => { await opts.project.move(s, 1); draw(); };
        const ch = changesOf(s);                      // what the screenplay changed on this shot since it was last looked at
        if (ch.length) {
          const stale = staleTakes(s).length;
          const ban = h(`<div class="shchg"><div class="row"><b>↻ Changed in the screenplay</b>${stale ? `<span class="hint">${plural(stale, "take")} made from the earlier text</span>` : ""}<button class="ghost sm" data-seen style="margin-left:auto">Reviewed ✓</button></div><ul>${ch.map(changeHtml).join("")}</ul></div>`);
          $("[data-seen]", ban).onclick = () => { s.seen = s.src; saveBoard(b, true); navRefresh(); (S.sbDraw || draw)(); };
          $(".shead", el).after(ban);
        }
        box.append(el);
        return;
      }
      $("[data-rm]", el).onclick = () => { if (s.output && !confirm("Remove this shot from the storyboard? (The videos stay in your gallery.)")) return; b.shots.splice(n, 1); saveBoard(b); draw(); };
      $("[data-up]", el).onclick = () => { if (n > 0) { b.shots.splice(n - 1, 0, b.shots.splice(n, 1)[0]); saveBoard(b); draw(); } };
      $("[data-dn]", el).onclick = () => { if (n < b.shots.length - 1) { b.shots.splice(n + 1, 0, b.shots.splice(n, 1)[0]); saveBoard(b); draw(); } };
      box.append(el);
    });
  };
  // what the screenplay changed, at the top (a project's board), and a shot opened from the screenplay's margin
  const drawChg = () => {
    const box2 = $("#sbchg", p); if (!box2 || !opts.project) return;
    const n = b.shots.filter(x => changesOf(x).length).length, cut = (b.cut || []).length;
    box2.innerHTML = n || cut ? `<div class="sbchg"><span>${n ? `↻ The screenplay changed <b>${plural(n, "shot")}</b> since you last looked.` : ""} ${cut ? `${plural(cut, "shot")} cut from it keep their takes.` : ""}</span><button class="ghost sm" data-see>What changed</button>${n ? `<button class="ghost sm" data-all>Mark all reviewed</button>` : ""}</div>` : "";
    const see = $("[data-see]", box2); if (see) see.onclick = () => opts.project.changes(() => { draw(); drawChg(); });
    const all = $("[data-all]", box2); if (all) all.onclick = () => { b.shots.forEach(x => { if (x.src) x.seen = x.src; }); saveBoard(b, true); navRefresh(); draw(); drawChg(); };
  };
  const draw0 = draw;
  S.sbDraw = () => { draw0(); drawChg(); };
  drawChg();
  if (S.sbJump) {
    const t = b.shots.find(x => x.projShot === S.sbJump); S.sbJump = null;
    const el = t && $(`[data-shot="${t.id}"]`, box); if (el) { el.scrollIntoView({ block: "center" }); el.classList.add("flash"); }
  }
  draw();
  pollFinish();
  $("#add", p).onclick = async () => { if (opts.project) { await opts.project.add(); draw(); return; } b.shots.push(newShot()); saveBoard(b); draw(); };
  $("#all", p).onclick = async () => { const todo = b.shots.filter(s => !s.takes.length); if (!todo.length) return toast("Every shot has takes. Use “＋ more takes” on a shot for more."); for (const s of todo) await renderShot(b, s, { q: "draft", count: b.takes }); toast(`Queued ${todo.length * b.takes} take${todo.length * b.takes > 1 ? "s" : ""}`); draw(); pollQueue(); };
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
        const j = await api("/studio/api/job/" + id);
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
      const items = (await api("/studio/api/models?folder=diffusion_models" + (S.incog ? "&private=1" : ""))).items || [];
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
async function loadPoses(force) { if (!poseCache || force) { try { poseCache = await api("/studio/api/poses"); } catch { poseCache = { packs: [], controlnets: {} }; } } return poseCache; }
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
  if (!ckptCache || force) { try { ckptCache = (await api("/studio/api/models?folder=checkpoints" + (S.incog ? "&private=1" : ""))).items.filter(m => m.folder === "checkpoints" && !/inpaint/i.test(m.name)); } catch { ckptCache = []; } }
  return ckptCache;
}
async function loadLoras(force) { if (!loraCache || force) { try { loraCache = await api("/studio/api/loras" + PQ()); } catch { loraCache = []; } } return loraCache; }
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
    if (!sugCache.status) sugCache.status = api("/studio/api/civitai/status").catch(() => ({}));
    if (!(await sugCache.status).has_key) return;        // no Civitai key: no suggestions (and no failed call)
    if (!sugCache[key]) sugCache[key] = api(`/studio/api/civitai/search?type=LORA&base=${encodeURIComponent(FAM_BASES[fam])}&sort=${encodeURIComponent("Most Downloaded")}&period=Month${S.incog ? "&private=1" : ""}`);
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
      const c = h(`<div class="scard ${added ? "on" : ""}" title="${esc(it.name)} · ${esc(vv.base || "")} · ${(it.downloads || 0).toLocaleString()} downloads">${img ? `<img src="/studio/api/civitai/img?u=${encodeURIComponent(img.url || img)}" loading="lazy">` : `<div class="lph">LoRA</div>`}<b>${esc(it.name.slice(0, 40))}</b><button class="btn sm">${added ? "✓ Added" : installed ? "＋ Add" : "↓ Get"}</button></div>`);
      const btn = $("button", c);
      btn.onclick = async () => {
        if (added) return;
        const addPick = file => { const ps = S.loras[mkey(app)] || (S.loras[mkey(app)] = []); if (!ps.some(p => p.name === file)) ps.push({ name: file, label: it.name, strength: 0.8, trained: vv.trained || [], family: fam }); saveLoras(); };
        if (installed) { const l = mine.find(l => l.file.split(/[\\/]/).pop().toLowerCase() === f.name.toLowerCase()); addPick(l.file); return renderForm(form, app); }
        btn.disabled = true; btn.textContent = "Downloading…";
        try {
          const r = await post("/studio/api/civitai/download", { version: vv.id, file: f.name, type: "LORA", private: !!S.incog });
          for (let k = 0; k < 400; k++) {
            await new Promise(res => setTimeout(res, 3000));
            const j = await api("/studio/api/job/" + r.id);
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
      const c = h(`<div class="lcard ${ok ? "" : "dim"}">${l.preview ? `<img src="/studio/api/civitai/img?u=${encodeURIComponent(l.preview)}" loading="lazy">` : `<div class="lph">LoRA</div>`}<div class="lb2"><b>${esc(l.name)}</b><span>${esc(l.base || "unknown model")}</span></div></div>`);
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
  const ch = S.chars.find(x => x.id === S.char[app.id]), cl = ch && ch.lora;   // the character's own LoRA comes with the character
  if (cl && cl.name && !(cl.family && fam && cl.family !== fam) && !picks.some(p => p.name === cl.name)) picks.push({ name: cl.name, strength: +cl.strength || 0.8, trained: cl.trained || [] });
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
    api("/studio/api/civitai/status").then(st => st.has_key && api("/studio/api/civitai/updates" + PQ())).then(ups => {
      const u = $("#upd", body); if (!u || !ups || !ups.length) return;
      const box = h(`<div class="panel updbox"><b>${ups.length} update${ups.length > 1 ? "s" : ""} on Civitai</b></div>`);
      ups.forEach(x => {
        const r = h(`<div class="mrow"><div class="mn"><b>${esc(x.name)}</b><span>You have ${esc(x.installed || "an older version")} · latest is ${esc(x.latest.name)}${x.latest.date ? " (" + esc(x.latest.date) + ")" : ""}</span></div><span></span><span class="ms"></span>${x.paid
          ? `<a class="btn sm ghost" target="_blank" rel="noopener" href="https://civitai.com/models/${encodeURIComponent(x.model)}?modelVersionId=${encodeURIComponent(x.latest.id)}" title="This version is paid or early access on Civitai">${x.paid === "paid" ? "Paid on Civitai" : "Free from " + esc(x.paid)}</a>`
          : `<button class="btn sm">Get latest</button>`}</div>`);
        const bt = $("button", r);
        if (bt) bt.onclick = async () => {
          bt.disabled = true; bt.textContent = "Starting…";
          try { const j = await post("/studio/api/civitai/download", { version: x.latest.id, type: x.type, private: S.incog }); await pollJob(j.id, s2 => bt.textContent = s2); bt.textContent = "Installed"; loraCache = null; ckptCache = null; toast(`${x.name} updated. Remove the old version in Settings if you like.`); }
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
    api("/studio/api/models" + PQ()).then(r => {
      const og = $("#og", body); if (!og) return;
      const list = r.items.filter(m => m.folder !== "loras" && m.civitai);
      if (!list.length) og.append(h(`<div class="empty">Checkpoints, VAEs, embeddings and upscalers you download from Civitai show up here.</div>`));
      list.forEach(m => og.append(h(`<div class="lcard">${m.preview ? `<img src="/studio/api/civitai/img?u=${encodeURIComponent(m.preview)}" loading="lazy">` : `<div class="lph">${esc(m.folder.toUpperCase())}</div>`}<div class="lb2"><b>${esc(m.title || m.name)}</b><span>${esc(m.folder)} · ${esc(m.base)} · ${fmtSize(m.size)}</span></div></div>`)));
    }).catch(() => { });
    const all = await loadLoras(true), g = $("#lg", body);
    if (!all.length) g.append(h(`<div class="empty">No LoRAs yet. Open “Find on Civitai”.</div>`));
    all.forEach(l => {
      const c = h(`<div class="lcard">${l.preview ? `<img src="/studio/api/civitai/img?u=${encodeURIComponent(l.preview)}" loading="lazy">` : `<div class="lph">LoRA</div>`}<div class="lb2"><b>${esc(l.name)}</b><span>${esc(l.base || "")}${l.version ? " · " + esc(l.version) : ""}</span>${l.trained.length ? `<span class="tw">${esc(l.trained.slice(0, 4).join(", "))}</span>` : ""}<button class="ghost" data-use>Use in Create</button></div></div>`);
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
    const st = await api("/studio/api/civitai/status").catch(() => ({}));
    if (!st.has_key) {
      body.innerHTML = "";
      const k = h(`<div class="panel keybox"><h3>Connect your Civitai account</h3><p class="muted">Studio searches and downloads with your own key. It stays on this PC and is never shown again.</p>
        <ol class="muted"><li>On civitai.com open <b>Account settings → API Keys</b> and create a key.</li><li>Paste it here.</li></ol>
        <div class="row"><input type="password" id="ck" placeholder="Civitai API key" autocomplete="off"><button class="btn primary" id="cks">Save key</button></div></div>`);
      body.append(k);
      $("#cks", k).onclick = async () => { try { await post("/studio/api/civitai/key", { key: $("#ck", k).value.trim() }); toast("Key saved"); civ(); } catch (e) { toast(e.message, true); } };
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
        const r = await api("/studio/api/civitai/search?" + qs);
        if (!more) g.innerHTML = "";
        next = r.next; $("#cmore", body).classList.toggle("hidden", !next);
        if (!r.items.length && !more) g.append(h(`<div class="empty">Nothing found.</div>`));
        r.items.forEach(it => {
          const v = it.versions[0] || { images: [] }, im = v.images[0];
          const c = h(`<div class="lcard">${im ? (im.type === "video" ? `<video src="/studio/api/civitai/img?u=${encodeURIComponent(im.url)}" muted loop playsinline preload="none"></video>` : `<img src="/studio/api/civitai/img?u=${encodeURIComponent(im.url)}" loading="lazy">`) : `<div class="lph">LoRA</div>`}
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
    $("#ckx", body).onclick = async () => { if (!confirm("Remove the saved Civitai key?")) return; await post("/studio/api/civitai/key", { key: "" }); civ(); };
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
    $(".cimgs", d).innerHTML = v.images.map(i => i.type === "video" ? `<video src="/studio/api/civitai/img?u=${encodeURIComponent(i.url)}" muted loop autoplay playsinline></video>` : `<img src="/studio/api/civitai/img?u=${encodeURIComponent(i.url)}" loading="lazy">`).join("") || `<div class="hint">No previews${S.incog ? "" : " (mature previews show only in private mode)"}.</div>`;
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
      const r = await post("/studio/api/civitai/download", { version: v.id, file: f.name, type: it.type, private: S.incog });
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
      const r = await post("/studio/api/ai/review", { ...t.output, private: !!b.private, prompt: withCamera(s.prompt, s.camera) });
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
      const r = await post("/studio/api/edit", { ...e, shots, name: b.name, private: !!b.private });
      b.film = await pollJob(r.id, s => { const el = $("#fst", d); if (el) el.textContent = s; });
      saveBoard(b, true); close(); toast("Film ready"); S.gallery = []; after();
    } catch (err) { toast("Couldn't make the film: " + err.message.slice(0, 300), true); btn.disabled = false; }
  };
}

/* ------------------------------------------------------------ REEL: pictures and clips cut to a song */
S.reel = { picks: [], song: "", aspect: "9:16", length: 30, cut: 1.2 };
/* ------------------------------------------------------------ PROJECTS: screenplay → scenes & shots → cast → keyframes → storyboard → sound & film */
const P_CAMS = { wide: "wide establishing shot", pov: "point-of-view shot", "over the shoulder": "over-the-shoulder shot", "high angle": "high-angle shot looking down", static: "static locked-off camera", "push in": "slow dolly push-in toward the subject", "pull back": "slow dolly pull-back revealing the scene", "pan left": "smooth pan left", "pan right": "smooth pan right", tracking: "tracking shot following the subject", orbit: "camera orbits around the subject", "crane up": "crane shot rising up", "crane down": "crane shot descending", handheld: "handheld camera with natural shake", "close-up": "tight close-up", "low angle": "low-angle hero shot", drone: "aerial drone flyover", "slow motion": "slow motion" };
const P_TONES = ["Cinematic drama", "Dark thriller", "Epic fantasy", "Sci-fi", "Anime", "Music video", "Comedy", "Horror", "Documentary", "Commercial"];
let projSave = null;
const saveProject = (pr, now) => { clearTimeout(projSave); const go = () => post("/studio/api/projects", pr).then(r => { if (!pr.id) pr.id = r.id; pr.updated = r.updated; pr.by = r.by; const k = S.projects.findIndex(x => x.id === pr.id); if (k >= 0) S.projects[k] = pr; navRefresh(); })
  .catch(e => { if (e.data && e.data.error === "changed") return takeAiChange("project", e.data.project); throw e; }); if (now || !pr.id) return go(); projSave = setTimeout(go, 700); };
/* An AI assistant (Claude and others, through Studio's MCP server) works on the same projects and boards. Its changes are
   saved with by: "ai"; the page takes them in: when it notices them (/studio/api/stamps, every few seconds), and when a save
   of its own is refused for being older than one of them (409 with the version as it is now). Nothing the AI did is undone. */
function takeAiChange(kind, fresh, quiet) {
  if (!fresh) return;
  const list = kind === "project" ? S.projects : (S.boards = S.boards || []), mine = list.find(x => x.id === fresh.id);
  if (mine) { Object.keys(mine).forEach(k => delete mine[k]); Object.assign(mine, fresh); } else list.push(fresh);
  S.spFlush = null; spDirty = false;                  // the editor's pending save would put the old text back
  // the refused save was yours if you'd just typed or picked something; else it was the page keeping itself in step
  const yours = Date.now() - (S.lastEdit || 0) < 15000, what = kind === "project" ? "project" : "storyboard";
  if (!quiet) toast(yours ? `An AI assistant changed this ${what} while you were editing it: here it is as it is now. Your last change wasn't saved: make it again.`
    : `An AI assistant updated this ${what}`, yours);
  route();
}
["input", "change"].forEach(ev => document.addEventListener(ev, () => { S.lastEdit = Date.now(); }, true));   // your own edits
let aiWatchAt = 0;
async function watchAiChanges() {
  if (document.hidden || Date.now() - aiWatchAt < 3500) return;
  aiWatchAt = Date.now();
  let st; try { st = await api("/studio/api/stamps"); } catch { return; }
  const newer = (list, stamps) => list.filter(x => { const t = stamps[x.id]; return t && t[1] === "ai" && t[0] > (x.updated || 0) + 1e-6; });
  const newIds = Object.entries(st.projects || {}).filter(([id, t]) => t[1] === "ai" && !S.projects.some(x => x.id === id)).map(([id]) => id);
  const prs = newer(S.projects, st.projects || {}), bds = newer(S.boards || [], st.boards || {});
  if (!prs.length && !bds.length && !newIds.length) return;
  const r = hashParts(), open = r.page === "projects" && r.arg && prs.some(x => x.id === r.arg);
  if (open && spDirty) return;                         // typing in the screenplay: its save will meet the AI's version (409)
  await Promise.all([loadProjects(), loadBoards(), loadChars()]);
  if (open || (r.page === "projects" && !r.arg) || (r.page === "storyboard" && bds.length)) {
    if (open) toast("An AI assistant updated this project", false);
    route();
  }
}
setInterval(watchAiChanges, 4000);
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
// 'RAY MERCER' -> 'Ray Mercer' (like screenplay.nice_name)
const niceName = s => s && s === s.toUpperCase() ? s.toLowerCase().replace(/(^|[\s'’-])(\p{L})/gu, (m, a, c) => a + c.toUpperCase()) : (s || "");
const sceneOf = (pr, s) => (pr.scenes || []).find(x => x.id === s.scene);
// shot numbers as a shot list and a storyboard write them: the scene's number and a letter, no I or O (screenplay.shot_codes)
const SHOT_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ";
const shotLetters = n => { let out = ""; n += 1; while (n) { const r = (n - 1) % 24; n = Math.floor((n - 1) / 24); out = SHOT_LETTERS[r] + out; } return out; };
function shotCodes(pr) {
  const num = {}; (pr.scenes || []).forEach((sc, k) => { num[sc.id] = sc.number || String(k + 1); });
  const seen = {}, out = {};
  (pr.shots || []).forEach((s, k) => { const n = seen[s.scene] || 0; seen[s.scene] = n + 1; out[s.id] = s.scene in num ? num[s.scene] + (/[A-Za-z]$/.test(num[s.scene]) ? "-" : "") + shotLetters(n) : String(k + 1); });
  return out;
}
// the screenplay's text behind a beat, rewritten: a beat edited in Scenes & shots is an edit of the screenplay
function rewriteBeat(pr, beat, text) {
  const src = pr.screenplay?.text || "", old = beat.action != null ? beat.action : beat.line.text;
  const lines = src.split("\n");
  let off = 0; for (let i = 0; i < Math.min(beat.at || 0, lines.length); i++) off += lines[i].length + 1;
  let at = src.indexOf(old, Math.max(0, off - 2));
  if (at < 0 || at > off + 400) { const first = src.indexOf(old); at = first >= 0 && src.indexOf(old, first + 1) < 0 ? first : -1; }
  if (at < 0) throw new Error("That text has changed in the screenplay: edit it on the Screenplay page.");
  pr.screenplay.text = src.slice(0, at) + text + src.slice(at + old.length);
}
const beatKey = b => b.action != null ? b.action.trim() : `${b.line.who || ""}: ${b.line.text || ""}`.trim();
// the breakdown made again from the screenplay with the project's choices (splits, merges, cameras, lengths, added shots)
async function rebreak(pr, extra = {}) {
  const r = await post("/studio/api/screenplay/breakdown", { text: pr.screenplay?.text || "", scenes: pr.scenes || [], shots: pr.shots || [], ...extra });
  Object.assign(pr, { scenes: r.scenes, shots: r.shots, roles: r.roles, pages: r.pages });
  await saveProject(pr, true);
  await followScreenplay(pr);
  return r;
}
const longestTake = () => { const d = (videoSummary()?.app.inputs || []).find(i => /^duration/i.test(i.label)); return d ? Math.max(...d.options.map(parseFloat)) : 15; };
const DN = { DAY: "Day", NIGHT: "Night", DAWN: "Dawn", DUSK: "Dusk" };
// where and when, as a prompt says it (like screenplay.setting_text)
function settingText(sc, pr) {
  if (!sc) return "";
  const loc = niceName(sc.location || "").trim(), time = (sc.time || "").trim().toLowerCase();
  const where = { INT: "Inside ", EXT: "Outside ", EST: "Establishing view of " }[sc.intext || ""] || "";
  const look = ((pr && locationOf(pr, sc)) || {}).look ? locationOf(pr, sc).look.replace(/\s+/g, " ").trim().replace(/\.$/, "") : "";
  return [loc ? where + loc + (look ? ` (${look})` : "") : "", time].filter(Boolean).join(", ");
}
const pSentence = p => { p = p.trim(); p = p.charAt(0).toUpperCase() + p.slice(1); return !p || /[.!?"”…]$/.test(p) ? p : p + "."; };
// a shot's prompt: the action with each character's look written in, the spoken lines (video), where and when, the camera
// (the same as projects.shot_text on the server)
function shotText(pr, s, withCamera, withLines) {
  let t = s.action || "";
  const cast = (pr.cast || []).map(id => S.chars.find(c => c.id === id)).filter(Boolean);
  cast.forEach(c => { const first = c.name.split(" ")[0]; const look = charDesc(c); if (look && new RegExp("\\b" + first + "\\b", "i").test(t) && !t.includes(look)) t = t.replace(new RegExp("\\b(" + c.name + "|" + first + ")\\b", "i"), `$1 (${look})`); });
  const parts = [t];
  if (withLines) (s.lines || []).slice(0, 2).forEach(l => { if (l.text) parts.push(`${niceName(l.who || "")} says: "${l.text}"`); });   // the lines it opens on
  parts.push(settingText(sceneOf(pr, s), pr));
  if (withCamera && P_CAMS[s.camera]) parts.push(P_CAMS[s.camera]);
  return parts.filter(x => x && x.trim()).map(pSentence).join(" ");
}
const eighths = n => { const p = Math.floor(n / 8), r = n % 8; return (p ? p + (r ? " " : "") : "") + (r ? r + "/8" : "") + (p === 1 && !r ? " page" : " pages"); };
const filmLength = shots => { const t = shots.reduce((a, s) => a + (+s.seconds || 5), 0); return t >= 60 ? `${Math.floor(t / 60)} min ${t % 60 ? (t % 60) + " s" : ""}`.trim() : t + " s"; };

async function pageProjects(main, arg, sub) {
  await loadChars();
  const list = await loadProjects();
  const pr = arg ? list.find(x => x.id === arg) : null;
  if (arg && !pr) { main.append(h(`<div class="page"><div class="empty">This project isn't here${S.incog ? "" : " (an Incognito project shows when Incognito is on)"}. <a href="#/projects">All projects →</a></div></div>`)); return; }
  if (pr) {
    // a project opens where it was left (a new one on its screenplay); its overview is #/projects/<id>/overview
    const step = P_ALIAS[pr.step] || pr.step;
    if (P_ALIAS[sub]) { location.replace(`#/projects/${pr.id}/${P_ALIAS[sub]}`); return; }
    if (!sub || (sub !== "overview" && !P_STEP[sub])) { location.replace(`#/projects/${pr.id}/${P_STEP[step] ? step : "screenplay"}`); return; }
    // its places and its parts, up to date when any of its pages opens (a project made before, or changed by Claude)
    const lc = syncLocations(pr), cc = await syncCast(pr).catch(() => null);
    if (lc.changed || (cc && cc.changed)) await saveProject(pr, true);
    setProject(pr); await loadBoards(); navRefresh();
    return sub === "overview" ? projectOverview(main, pr) : projectStep(main, pr, sub);
  }
  const p = h(`<div class="page"><div class="eyebrow">Film Engine Studio</div><h1>Projects</h1>
    <p class="hint" style="max-width:760px">A project takes a film from its screenplay to the finished cut, through the three menus at the top: <b>Pre-production</b> (the screenplay, its breakdown into scenes and shots, the cast, a keyframe per shot), <b>Production</b> (the storyboard: every shot's takes) and <b>Post-production</b> (music, dialogue and the film edit). Import the screenplay, or write it here, to start one.</p>
    <div class="row wrap" style="margin:14px 0"><button class="btn primary" id="nsp" title="A PDF or Fountain screenplay becomes a new project">⇪ Import a screenplay</button><input type="file" id="nspf" accept="${SP_ACCEPT}" hidden><button class="btn" id="np">＋ Write a new one</button><span class="hint">PDF, Final Draft (.fdx) or Fountain (.fountain). You can also drop the file here.</span></div><div class="pgrid"></div><div id="oboards"></div></div>`);
  main.append(p);
  const nsp = $("#nsp", p), nspf = $("#nspf", p);
  nsp.onclick = () => nspf.click();
  nspf.onchange = () => { const f = nspf.files[0]; nspf.value = ""; importScreenplay(f, nsp); };
  p.ondragover = e => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { e.preventDefault(); p.classList.add("spdrop"); } };
  p.ondragleave = e => { if (e.target === p || !p.contains(e.relatedTarget)) p.classList.remove("spdrop"); };
  p.ondrop = e => { e.preventDefault(); p.classList.remove("spdrop"); const f = e.dataTransfer?.files?.[0]; if (f) importScreenplay(f, nsp); };
  const g = $(".pgrid", p);
  if (!list.length) g.append(h(`<div class="empty">No projects yet. Import a screenplay to start one.</div>`));
  list.forEach(x => {
    const kf = (x.shots || []).find(s => s.keyframe);
    const what = [x.scenes ? plural(x.scenes.length, "scene") : "", plural((x.shots || []).length, "shot")].filter(Boolean).join(" · ");
    g.append(h(`<a class="pcard" href="#/projects/${x.id}"><div class="pthumb" style="${kf ? `background-image:url('${inputUrl(kf.keyframe)}')` : ""}">${kf ? "" : "✎"}</div><div class="pb"><b>${esc(x.title || x.name || "Untitled")}</b><span>${what} · ${esc(x.stage || "Screenplay")}</span></div></a>`));
  });
  $("#np", p).onclick = () => titlePageDialog([], { heading: "A new screenplay", action: "Start writing", onSave: async (tp, title) => {
    const n = { title, name: title, tone: "Cinematic drama", cast: [], castMap: {}, scenes: [], shots: [], roles: [], private: !!S.incog, stage: "Screenplay", step: "screenplay", screenplay: { text: withTitlePage("", tp), source: "new", created: Date.now() / 1000 } };
    await saveProject(n, true); setProject(n); location.hash = `#/projects/${n.id}/screenplay`;
  } });
  // storyboards that belong to no project (made from the Gallery or by Claude)
  const boards = (await loadBoards()).filter(b => !list.some(x => x.id === b.project || x.board === b.id));
  if (boards.length) {
    const o = $("#oboards", p);
    o.append(h(`<div class="section-head"><h2>Other storyboards</h2><span class="hint">Made from the Gallery or by Claude</span></div>`));
    const og = h(`<div class="pgrid"></div>`); o.append(og);
    boards.forEach(b => { const c = h(`<a class="pcard" href="#/storyboard"><div class="pthumb">▤</div><div class="pb"><b>${esc(b.name || "Untitled")}</b><span>${plural((b.shots || []).length, "shot")} · Storyboard</span></div></a>`); c.onclick = () => { S.boardId = b.id; store.set("board", b.id); }; og.append(c); });
  }
}
/* a project's overview: its title, and how far it is in each phase, every page one click away */
function projectOverview(main, pr) {
  const st = projStats(pr);
  const p = h(`<div class="page proj"><div class="eyebrow"><a href="#/projects">Projects</a> ›</div>
    <div class="row ptop"><input class="ptitle" value="${esc(pr.title || pr.name || "")}" placeholder="Film title"><button class="ghost danger sm" id="pdel" title="Delete this project (its storyboard and renders stay)">Delete</button></div>
    <p class="hint">${[plural(st.scenes, "scene"), pr.pages ? `about ${plural(pr.pages, "page")}` : "", plural(st.shots, "shot"), st.shots ? filmLength(pr.shots) + " of film" : "", st.roles ? plural(st.roles, "part") : ""].filter(Boolean).join(" · ")}${pr.private ? " · 🕶 Incognito" : ""}</p>
    <div class="ovgrid"></div></div>`);
  main.append(p);
  $(".ptitle", p).oninput = e => {                 // the film's title is the screenplay's
    pr.title = pr.name = e.target.value;
    if (pr.screenplay) { const tp = [["Title", e.target.value], ...titlePageOf(pr.screenplay.text).filter(([k]) => k !== "Title")]; pr.screenplay.text = withTitlePage(pr.screenplay.text, tp); }
    saveProject(pr); navRefresh();
  };
  $("#pdel", p).onclick = async () => { if (!confirm(`Delete the project “${pr.title || "Untitled"}”? Its storyboard and everything rendered stay (in Projects › Other storyboards and the Gallery).`)) return; await api("/studio/api/projects/" + pr.id, { method: "DELETE" }); await loadChars(); S.projects = S.projects.filter(x => x.id !== pr.id); if (S.projId === pr.id) { S.projId = null; store.set("proj", null); } location.hash = "#/projects"; };
  const g = $(".ovgrid", p);
  PHASES.forEach(([ph, label]) => {
    const [done, of] = phaseFrac(pr, ph, st);
    const col = h(`<div class="panel ovcol"><div class="ovhead"><span class="eyebrow">${label}</span><span class="fe-ph-frac">${of ? `${done}/${of}` : ""}</span></div><div class="fe-ph-bar"><span class="fe-ph-fill" style="width:${of ? Math.round(done / of * 100) : 0}%"></span></div></div>`);
    Object.keys(P_STEP).filter(k => P_STEP[k][0] === ph).forEach(k => col.append(h(`<a class="mi" href="#/projects/${pr.id}/${k}"><i>${P_STEP[k][2]}</i><span><b>${P_STEP[k][1]}</b><small>${P_STEP[k][3]}</small></span><em>${esc(stepStat(pr, k, st))}</em></a>`)));
    g.append(col);
  });
}
/* one of the project's pages (screenplay, scenes & shots, cast, keyframes, storyboard, music & dialogue, film edit) */
function projectStep(main, pr, k) {
  const [ph, label] = P_STEP[k];
  const p = h(`<div class="page proj"><div class="eyebrow">${esc(PHASES.find(x => x[0] === ph)[1])} · <a href="#/projects/${pr.id}/overview" title="The project's overview">${esc(projTitle(pr))}</a></div><h1>${esc(label)}</h1><div class="pbody"></div></div>`);
  main.append(p);
  if (pr.step !== k) { pr.step = k; saveProject(pr); }
  const go = k2 => { location.hash = `#/projects/${pr.id}/${k2}`; };   // route() saves a screenplay being typed first
  const b = $(".pbody", p);
  const draw = () => {
    b.innerHTML = ""; ({ screenplay: stepScreenplay, scenes: stepScenes, cast: stepCast, locations: stepLocations, storyboard: stepStoryboard, takes: stepBoard, sound: stepSound, film: stepFilm })[k](b, pr, go, draw); navRefresh();
    if (k === "scenes" && S.scnJump) { const el = $(`.shotrow[data-shot="${S.scnJump}"]`, b); S.scnJump = null; if (el) { el.scrollIntoView({ block: "center" }); el.classList.add("flash"); } }
  };
  draw();
}

/* ---- 2 · scenes and shots: the screenplay's breakdown, made again from it at every save. What is decided here is kept:
   splits and merges of setups (on the scene), a camera or a length set by hand, keyframes, shots added by hand. The action
   and the lines are the screenplay's own text: editing them here edits the screenplay. */
function stepScenes(b, pr, go, draw) {
  const scenes = pr.scenes || [], shots = pr.shots || [];
  const totalEighths = scenes.reduce((a, sc) => a + (sc.eighths || 0), 0);
  const d = h(`<div><div class="panel scnsum"><div><b>${plural(scenes.length, "scene")} · ${eighths(totalEighths)} · ${plural(shots.length, "shot")} · ${filmLength(shots)} of film</b>
      <p class="hint">The breakdown, made from the screenplay every time it's saved: a scene per scene heading (its length in eighths of a page, Day/Night and cast numbers as on a breakdown sheet), and in each a shot per camera setup. A setup starts where the screenplay calls for one: a shot heading (CLOSE ON, ANGLE ON, INSERT, POV…), a cut, or a camera direction that moves or re-frames the camera (“The camera eases toward Ray”); the action and lines after it play in that shot, and so does a camera that holds. Screen time is a page a minute. Your changes here stay through edits of the screenplay: ✂ splits a setup at a beat, ⤒ merges it into the one before, and a camera or length you set is kept. The action and the lines are the screenplay's text: edit them here and the screenplay changes.</p></div>
      <button class="btn primary" id="pnext">Next: cast →</button></div><div class="scns"></div></div>`);
  b.append(d);
  const box = $(".scns", d);
  if (!scenes.length && !shots.length) box.append(h(`<div class="empty">No scenes yet. Write a scene heading (INT. PLACE - DAY) in the screenplay.</div>`));
  const codes = shotCodes(pr);
  const castNo = Object.fromEntries((pr.roles || []).map(r => [r.name, r.cast]));
  const busy = async (fn, what) => { try { await fn(); draw(); } catch (e) { toast(`${what}: ${e.message.slice(0, 200)}`, true); } };
  const groups = scenes.map(sc => ({ sc, list: shots.filter(s => s.scene === sc.id) }));
  const loose = shots.filter(s => !scenes.some(sc => sc.id === s.scene));
  if (loose.length) groups.push({ sc: null, list: loose });
  groups.forEach(({ sc, list }) => {
    const ie = { INT: "Interior", EXT: "Exterior", "INT/EXT": "Interior / exterior", EST: "Establishing" }[sc?.intext] || "";
    const meta = !sc ? "" : sc.omitted ? "Omitted" : [sc.continuous ? "One continuous take" : "", ie, niceName(sc.location || ""), [DN[sc.dn], sc.time && DN[sc.dn] !== niceName(sc.time) ? `(${niceName(sc.time).toLowerCase()})` : ""].filter(Boolean).join(" "), eighths(sc.eighths || 1),
      (sc.characters || []).length ? "Cast " + [...sc.characters].sort((a, b2) => (castNo[a] || 99) - (castNo[b2] || 99)).map(c => (castNo[c] ? castNo[c] + ". " : "") + niceName(c)).join(", ") : ""].filter(Boolean).join(" · ");
    const custom = sc && ((sc.splits || []).length || (sc.joins || []).length || list.some(s => s.manual || s.camera_set || s.seconds_set));
    const el = h(`<div class="scn panel"><div class="scn-head"><span class="scn-no">${sc ? esc(sc.number) : "–"}</span>
      <div class="scn-t"><b>${sc ? esc(sc.heading || "Opening") : "Shots made before the screenplay"}</b>${meta ? `<div class="scn-meta">${esc(meta)}</div>` : ""}${sc?.summary ? `<p class="hint">${esc(sc.summary)}</p>` : ""}</div>
      <div class="scn-acts">${custom ? `<button class="ghost sm" data-reset title="Your splits, merges, cameras, lengths and added shots in this scene go; keyframes stay">↺ Back to the screenplay's setups</button>` : ""}${sc ? `<button class="ghost sm" data-open>✎ Screenplay</button>` : ""}</div></div>
      <div class="scn-shots"></div>${sc?.omitted ? "" : `<button class="ghost sm" data-add>＋ Add a shot the screenplay doesn't have</button>`}</div>`);
    const rows = $(".scn-shots", el);
    list.forEach((s, k) => {
      const beats = s.beats || [];
      const own = s.manual || !sc;                   // added by hand, or made before the screenplay: not the screenplay's
      const before = list.slice(0, k).reverse().find(y => !y.manual);   // the setup a setup merges into
      const beatHtml = (bt, j) => (j ? `<button class="bt-cut" data-cut="${j}" title="Split here: a new setup starts at this beat">✂ split here</button>` : "") + (bt.action != null
        ? `<textarea class="bt-act" data-j="${j}" rows="1" spellcheck="true">${esc(bt.action)}</textarea>`
        : `<div class="bt-line"><b>${esc(niceName(bt.line.who))}</b>${bt.line.paren ? `<i>${esc(bt.line.paren)}</i>` : ""}<textarea class="bt-say" data-j="${j}" rows="1" spellcheck="true">${esc(bt.line.text)}</textarea></div>`);
      const shown = beats.slice(0, 4).map(beatHtml).join("") + (beats.length > 4 ? `<details class="bt-more"><summary>${beats.length - 4} more beat${beats.length > 5 ? "s" : ""}</summary>${beats.slice(4).map((bt, j) => beatHtml(bt, j + 4)).join("")}</details>` : "");
      const r = h(`<div class="shotrow ${own ? "manual" : ""}" data-shot="${s.id}"><div class="sr-no">${esc(codes[s.id])}</div><div class="sr-main">
          <div class="row wrap sr-opts"><select class="sr-cam" title="${s.camera_set ? "Set by you" : "From the screenplay"}">${Object.keys(P_CAMS).map(c => `<option ${c === s.camera ? "selected" : ""}>${c}</option>`).join("")}</select>
            <label class="sr-secl" title="Screen time (a page a minute)"><input type="number" class="sr-sec" min="1" max="900" value="${+s.seconds || 5}"> s</label>
            ${s.camera_set || s.seconds_set ? `<span class="sr-set" title="Kept through edits of the screenplay">set by you</span>` : ""}
            ${s.continues ? `<span class="sr-cont" title="The scene is one continuous take: this move starts where the one before ends">↳ continues ${esc(codes[list[k - 1]?.id] || "")}</span>` : ""}
            ${s.manual ? `<span class="sr-cont">added by you</span>` : ""}
            ${(+s.seconds || 0) > longestTake() ? `<span class="hint" title="The Video app makes takes of ${longestTake()} s at most">longer than one take: made in parts on the storyboard</span>` : ""}
            ${(s.characters || []).length ? `<span class="hint">${esc(s.characters.map(niceName).join(", "))}</span>` : ""}
            <span class="sr-btns">${own ? `<button class="icon" data-u title="Move up">↑</button><button class="icon" data-dn title="Move down">↓</button>` : ""}${!own && before ? `<button class="icon" data-m title="Merge into shot ${esc(codes[before.id])}">⤒</button>` : ""}${own || before ? `<button class="icon" data-x title="${own ? "Remove" : `Remove this setup: its beats join shot ${esc(codes[before.id])}`}">✕</button>` : ""}</span></div>
          ${own ? `<textarea class="sr-act" rows="2" placeholder="What we see in this shot">${esc(s.action || "")}</textarea>` : `<div class="sr-beats">${shown}</div>`}</div></div>`);
      $(".sr-cam", r).onchange = e => { s.camera = e.target.value; s.camera_set = true; saveProject(pr); draw(); };
      $(".sr-sec", r).onchange = e => { s.seconds = Math.max(1, +e.target.value || 1); s.seconds_set = true; saveProject(pr); draw(); };
      // a beat edited here is the screenplay edited
      $$("[data-j]", r).forEach(t => t.onchange = () => busy(async () => {
        const bt = beats[+t.dataset.j], before = beatKey(bt);
        rewriteBeat(pr, bt, t.value.trim());
        const after = beatKey(bt.action != null ? { action: t.value.trim() } : { line: { ...bt.line, text: t.value.trim() } });
        ["splits", "joins"].forEach(k2 => { if (sc && sc[k2]) sc[k2] = sc[k2].map(x => x === before ? after : x); });
        await rebreak(pr); toast("Screenplay updated");
      }, "Couldn't change the screenplay"));
      $$("[data-cut]", r).forEach(c => c.onclick = () => busy(async () => {
        const key = beatKey(beats[+c.dataset.cut]);
        sc.joins = (sc.joins || []).filter(x => x !== key); sc.splits = [...new Set([...(sc.splits || []), key])];
        await rebreak(pr);
      }, "Couldn't split it"));
      const join = () => busy(async () => {
        if ((sc.splits || []).includes(s.anchor)) sc.splits = sc.splits.filter(x => x !== s.anchor);
        else sc.joins = [...new Set([...(sc.joins || []), s.anchor])];
        await rebreak(pr);
      }, "Couldn't merge it");
      const m = $("[data-m]", r); if (m) m.onclick = join;
      const x = $("[data-x]", r); if (x) x.onclick = () => {
        if (own) { if (!confirm(`Remove shot ${codes[s.id]}?`)) return; pr.shots = shots.filter(y => y !== s); saveProject(pr, true); draw(); return; }
        if (confirm(`Remove setup ${codes[s.id]}? Its action and lines stay in the screenplay and join shot ${codes[before.id]}.`)) join();
      };
      const act = $(".sr-act", r); if (act) act.oninput = e => { s.action = e.target.value; s.title = e.target.value.split(/(?<=[.!?])\s/)[0].slice(0, 70) || "Added shot"; saveProject(pr); };
      const move = dir => {                       // an added shot moves among the scene's shots (the setups keep the story's order)
        const i = list.indexOf(s), to = i + dir; if (to < 0 || to >= list.length) return;
        const order = list.filter(y => y !== s); order.splice(to, 0, s);
        const at = order.indexOf(s); s.after = at === 0 ? "^" : order[at - 1].id;
        const start = pr.shots.indexOf(list[0]);
        pr.shots = pr.shots.filter(y => !list.includes(y)); pr.shots.splice(start, 0, ...order);
        saveProject(pr, true); draw();
      };
      const u = $("[data-u]", r); if (u) u.onclick = () => move(-1);
      const dn = $("[data-dn]", r); if (dn) dn.onclick = () => move(1);
      rows.append(r);
    });
    const add = $("[data-add]", el); if (add) add.onclick = () => {
      const last = list[list.length - 1];
      const ns = { id: Math.random().toString(36).slice(2, 10), scene: sc ? sc.id : undefined, title: "Added shot", action: "", lines: [], beats: [], dialogue: "", characters: [], camera: "static", seconds: 5, manual: true, after: last ? last.id : "^" };
      shots.splice(last ? shots.indexOf(last) + 1 : shots.length, 0, ns); pr.shots = shots; saveProject(pr, true); draw();
    };
    const rs = $("[data-reset]", el); if (rs) rs.onclick = () => { if (confirm("Go back to the screenplay's own setups in this scene? Your splits, merges, cameras, lengths and added shots here go; keyframes stay.")) busy(() => rebreak(pr, { redo: [sc.id] }), "Couldn't reset it"); };
    const op = $("[data-open]", el); if (op) op.onclick = () => { S.spJump = scenes.filter(x => x.heading).indexOf(sc); go("screenplay"); };
    box.append(el);
  });
  $("#pnext", d).onclick = () => go("cast");
}

/* ---- 3 · cast: the screenplay's characters played by Studio characters */
function stepCast(b, pr, go, draw) {
  pr.castMap = pr.castMap || {};                      // the parts were cast when the project opened (syncCast)
  const roles = [...(pr.roles || [])].sort((a, b2) => (a.cast || 99) - (b2.cast || 99) || b2.lines - a.lines);
  const sync = () => { pr.cast = castOf(pr); };
  const d = h(`<div><div class="panel scnsum"><div><b>${plural(roles.length, "character")} in the screenplay</b><p class="hint">Each part is played by a Studio character: their look is written into every shot they're in, and their face guides the keyframes. Create them from the screenplay (Studio takes the description from where they're introduced), then add a face picture.</p></div>
    <button class="btn primary" id="pnext">Next: locations →</button></div><div class="casts"></div></div>`);
  b.append(d);
  const box = $(".casts", d);
  if (!roles.length) box.append(h(`<div class="empty">No speaking characters in the screenplay yet.</div>`));
  roles.forEach(r => {
    const c = S.chars.find(x => x.id === pr.castMap[r.name]);
    const el = h(`<div class="castrow panel"><div class="cr-face ${c ? "" : "off"}" title="${c ? "Add or change the face picture" : ""}">${c && c.face ? `<img src="${inputUrl(c.face)}">` : `<span>${esc((r.full || r.name)[0] || "?")}</span>`}${c ? `<small>${c.face ? "change" : "＋ face"}</small>` : ""}<input type="file" accept="image/*" hidden></div>
      <div class="cr-main"><div>${r.cast ? `<span class="cr-no" title="Cast number">${r.cast}</span>` : ""}<b>${esc(r.full || niceName(r.name))}</b> <span class="hint">${esc(r.name)} · ${plural(r.lines, "line")} · ${plural((r.scenes || []).length, "scene")}</span></div>
        ${c ? `<div class="cr-desc" title="Written into every shot ${esc(c.name.split(" ")[0])} is in, and sent with every picture of them">${esc(charDesc(c) || "No description yet: fill in the character sheet.")}</div><div class="row wrap"><button class="ghost sm" data-sheet>✎ Character sheet</button>${c.lora ? `<span class="hint">LoRA: ${esc(c.lora.name.split(/[\\/]/).pop())}</span>` : ""}</div>` : r.intro ? `<p class="cr-intro">“${esc(r.intro)}”</p>` : ""}</div>
      <div class="cr-link"><select class="cr-sel"><option value="">Not cast yet</option>${S.chars.filter(x => !x.private || pr.private).map(x => `<option value="${x.id}" ${c && c.id === x.id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>
        ${c ? "" : `<button class="btn" data-new>＋ Create ${esc(r.full || niceName(r.name))}</button>`}</div></div>`);
    $(".cr-sel", el).onchange = async e => { pr.castMap[r.name] = e.target.value; sync(); await adopt(S.chars.find(x => x.id === e.target.value), pr).catch(() => { }); saveProject(pr, true); draw(); };   // "": not cast, and it stays so
    const nb = $("[data-new]", el); if (nb) nb.onclick = async () => {
      nb.disabled = true;
      try {
        const nc = await post("/studio/api/characters", { ...charFromRole(r), private: !!pr.private });
        await loadChars(); pr.castMap[r.name] = nc.id; sync(); await saveProject(pr, true); toast(`${nc.name} created. Add a face picture for the storyboard.`); draw();
      } catch (e) { toast(e.message.slice(0, 200), true); nb.disabled = false; }
    };
    if (c) {
      const face = $(".cr-face", el), fi = $("input", face);
      face.onclick = () => fi.click();
      fi.onchange = async () => { if (!fi.files[0]) return; try { c.face = await upload(fi.files[0]); await post("/studio/api/characters", c); await loadChars(); draw(); } catch (e) { toast(e.message, true); } };
      $("[data-sheet]", el).onclick = () => editChar(c, async () => { await loadChars(); draw(); });
    }
    box.append(el);
  });
  $("#pnext", d).onclick = () => go("locations");
}

/* ------------------------------------------------------------ the screenplay's places and people follow it
   After every change of the screenplay (the editor's save, the breakdown page, a new file, a draft restored): its places
   become the project's locations (a new one added, its scenes kept up to date; one gone from the screenplay goes if
   nobody touched it, else stays, marked), and its speaking characters are cast (a Studio character found by name, or
   made from the screenplay's introduction; one made this way that nobody touched goes when the part does: a typo). */
const rid = () => Math.random().toString(36).slice(2, 10);
const locationOf = (pr, sc) => { const n = ((sc && sc.location) || "").trim().toUpperCase(); return n ? (pr.locations || []).find(l => (l.name || "").toUpperCase() === n) || null : null; };
// the breakdown's scenes -> the screenplay's places (screenplay.places on the server)
function placesOf(scenes) {
  const out = new Map();
  (scenes || []).forEach(sc => {
    const name = (sc.location || "").trim().toUpperCase();
    if (!name || sc.omitted || !sc.heading) return;
    if (!out.has(name)) out.set(name, { name, full: niceName(name), intext: [], times: [], scenes: [], description: sc.summary || "" });
    const p = out.get(name);
    if (sc.intext && !p.intext.includes(sc.intext)) p.intext.push(sc.intext);
    const t = (sc.time || "").toUpperCase(); if (t && !p.times.includes(t)) p.times.push(t);
    p.scenes.push(sc.id);
    if (!p.description && sc.summary) p.description = sc.summary;
  });
  return [...out.values()];
}
const newLocation = p => ({ id: rid(), name: p.name, full: p.full, description: p.description || "", look: "", lighting: "",
  time: (p.times[0] || "").toLowerCase(), atmosphere: "", sound: "", picture: "", intext: p.intext, scenes: p.scenes, auto: true });
function syncLocations(pr) {
  const places = placesOf(pr.scenes), by = new Map(places.map(p => [p.name, p]));
  pr.locations = pr.locations || [];
  const have = new Set(pr.locations.map(l => (l.name || "").toUpperCase()));
  const fresh = places.filter(p => !have.has(p.name)), lost = pr.locations.filter(l => !l.gone && (l.scenes || []).length && !by.has((l.name || "").toUpperCase()));
  const out = { added: [], removed: [], renamed: [], changed: false };
  // a place renamed in the headings (one went, one came): the location you worked on follows it
  if (fresh.length === 1 && lost.length === 1 && lost[0].edited) {
    const l = lost[0], p = fresh.shift(); out.renamed.push([l.full, p.full]);
    Object.assign(l, { name: p.name, full: l.full && niceName(l.name) !== l.full ? l.full : p.full }); have.add(p.name); out.changed = true;
  }
  fresh.forEach(p => { pr.locations.push(newLocation(p)); out.added.push(p.full); out.changed = true; });
  pr.locations = pr.locations.filter(l => {
    const p = by.get((l.name || "").toUpperCase());
    if (p) {
      const before = JSON.stringify([l.intext, l.scenes, !!l.gone]);
      Object.assign(l, { intext: p.intext, scenes: p.scenes }); delete l.gone;
      if (!l.edited && !l.description && p.description) l.description = p.description;
      if (JSON.stringify([l.intext, l.scenes, !!l.gone]) !== before) out.changed = true;
      return true;
    }
    if (!(l.scenes || []).length && !l.auto) return true;     // added by hand, in no scene yet
    if (l.auto && !l.edited && !l.picture) { out.removed.push(l.full); out.changed = true; return false; }
    if (!l.gone) { l.gone = true; l.scenes = []; out.changed = true; }
    return true;
  });
  return out;
}
// a character made from a part of the screenplay: its introduction ("fifty-seven, canvas work jacket…") -> age and look
function charFromRole(r) {
  const parts = (r.intro || "").split(/,\s*|\.\s+/), first = (parts[0] || "").trim().replace(/\.$/, "");
  const age = NUMBER_AGE.test(first) || /^\(?\d{2}s\)?$|^(early|mid|late) \w+$/i.test(first) ? first.replace(/[()]/g, "") : "";
  const look = age ? (r.intro || "").slice((r.intro || "").indexOf(parts[0]) + parts[0].length).replace(/^[,.]\s*/, "") : (r.intro || "");
  return { name: r.full || niceName(r.name), age, look, trigger: "", voice: "", face: "" };
}
const SHEET_KEYS = ["name", "age", "gender", "build", "skin", "features", "eyes", "hair", "facial_hair", "marks", "wardrobe", "accessories", "look", "trigger"];
const sheetSig = c => JSON.stringify(SHEET_KEYS.map(k => c[k] || ""));
const untouched = c => sheetSig(c) === c.autoSig && !c.face && !(c.pictures || []).length && !c.lora && !c.voice;
// the characters playing the screenplay's parts now (a part gone keeps its character in castMap, should it come back)
const castOf = pr => { const names = new Set((pr.roles || []).map(r => r.name)); return [...new Set(Object.entries(pr.castMap || {}).filter(([role, id]) => id && names.has(role)).map(([, id]) => id))]; };
// a character made for another project's cast, cast here too: it's nobody's typo now, it stays when that part or project goes
async function adopt(c, pr) {
  if (!c || !c.auto || c.auto === pr.id) return;
  delete c.auto; delete c.autoSig;
  Object.assign(c, await post("/studio/api/characters", c));
}
let castBusy = null;
async function syncCast(pr) {
  while (castBusy) await castBusy;                   // one at a time: two saves must not both make RAY
  let done; castBusy = new Promise(r => { done = r; });
  const out = { added: [], removed: [], changed: false };
  try {
    pr.castMap = pr.castMap || {};
    const roles = pr.roles || [], names = new Set(roles.map(r => r.name));
    for (const r of roles) {
      if (pr.castMap[r.name] === "" || (pr.castMap[r.name] && S.chars.some(c => c.id === pr.castMap[r.name]))) continue;   // "": left uncast on purpose
      let c = S.chars.find(c => c.name.toUpperCase() === (r.full || "").toUpperCase()) || S.chars.find(c => c.name.split(" ")[0].toUpperCase() === r.name.split(" ")[0]);
      if (!c) {
        const sheet = charFromRole(r);
        c = await post("/studio/api/characters", { ...sheet, private: !!pr.private, auto: pr.id, autoSig: sheetSig(sheet) });
        S.chars.push(c); out.added.push(c.name);
      } else await adopt(c, pr);
      pr.castMap[r.name] = c.id; out.changed = true;
    }
    for (const [role, id] of Object.entries(pr.castMap)) {   // a part gone from the screenplay
      if (names.has(role)) continue;
      const c = id && S.chars.find(x => x.id === id);
      if (!c || (c.auto === pr.id && untouched(c))) {
        if (c) { await api("/studio/api/characters/" + c.id, { method: "DELETE" }); S.chars = S.chars.filter(x => x !== c); out.removed.push(c.name); }
        delete pr.castMap[role]; out.changed = true;
      }
    }
    const cast = castOf(pr);
    if (JSON.stringify(cast) !== JSON.stringify(pr.cast || [])) { pr.cast = cast; out.changed = true; }
  } finally { castBusy = null; done(); }
  return out;
}
// the shots changed since last looked at: on the takes (their board's `seen`) and in the storyboard (a frame's `kf_src`)
const frameDrift = (pr, s) => s.keyframe && s.kf_src ? SR.shotChanges(s.kf_src, SR.shotSource(s, sceneOf(pr, s))) : [];
async function followScreenplay(pr) {
  const locs = syncLocations(pr);
  let cast = null; try { cast = await syncCast(pr); } catch (e) { console.warn("cast", e); }
  if (locs.changed || (cast && cast.changed)) await saveProject(pr, true);
  const board = await syncProjectBoard(pr);
  const bs = boardOf(pr), moved = new Set();
  (bs ? bs.shots : []).forEach(x => { if (changesOf(x).length) moved.add(x.projShot); });
  (pr.shots || []).forEach(s => { if (frameDrift(pr, s).length) moved.add(s.id); });
  navRefresh();
  return { pending: moved.size, follows: !!board || (pr.shots || []).some(s => s.keyframe), places: locs.added, placesGone: locs.removed, renamed: locs.renamed,
    chars: cast ? cast.added : [], charsGone: cast ? cast.removed : [] };
}

/* ---- 4 · locations: the screenplay's places (from its scene headings, as it's written), each with what it looks like */
function stepLocations(b, pr, go, draw) {
  const ch = syncLocations(pr); if (ch.changed) saveProject(pr);
  const order = id => (pr.scenes || []).findIndex(s => s.id === id);
  const locs = (pr.locations || []).slice().sort((a, z) => (a.gone ? 1 : 0) - (z.gone ? 1 : 0) || (a.scenes?.length ? order(a.scenes[0]) : 1e6) - (z.scenes?.length ? order(z.scenes[0]) : 1e6));
  const inScript = locs.filter(l => !l.gone && (l.scenes || []).length).length;
  const d = h(`<div><div class="panel scnsum"><div><b>${plural(inScript, "place")} in the screenplay</b><p class="hint">Every place a scene heading names (INT. THE GLASS HARBOUR DINER - DAY) is a location here, with its scenes; a new one appears as you write it. What you write in <i>What the generator gets</i> goes into every shot set there, like a character's look, and its picture is a reference for their frames.</p></div>
      <div class="row"><button class="ghost" id="lnew">＋ Location</button><button class="btn primary" id="pnext">Next: storyboard →</button></div></div>
    <div class="loc-grid"></div></div>`);
  b.append(d);
  const grid = $(".loc-grid", d);
  if (!locs.length) grid.append(h(`<div class="empty">No locations yet: a scene heading in the screenplay makes one.</div>`));
  locs.forEach(l => {
    const scenes = (l.scenes || []).map(id => (pr.scenes || []).find(s => s.id === id)).filter(Boolean);
    const el = h(`<div class="loc-card ${l.gone ? "gone" : ""}" data-loc="${l.id}">
      ${l.picture ? `<img class="loc-pic" src="${inputUrl(l.picture)}" loading="lazy">` : ""}
      <div class="loc-head"><div class="loc-av">◎</div><div><h3>${esc(l.full || niceName(l.name))}</h3><span class="hint">${l.gone ? "Not in the screenplay any more" : plural(scenes.length, "scene") + (scenes.length ? " · " + esc(scenes.map(s => s.number).join(", ")) : "")}${(l.intext || []).length ? " · " + esc(l.intext.join("/")) : ""}</span></div></div>
      <p class="loc-desc">${esc(l.description || "No description yet.")}</p>
      <div class="loc-tags">${[l.time, l.lighting].filter(Boolean).map(t => `<span class="tag">${esc(t)}</span>`).join("")}${l.look ? `<span class="tag look" title="${esc(l.look)}">in the prompts</span>` : ""}${l.auto && !l.edited ? `<span class="tag dim" title="Made from the screenplay: edit it to make it yours">from the screenplay</span>` : ""}</div>
      <div class="loc-acts"><button class="ghost sm" data-edit>Edit</button><button class="ghost sm" data-pic title="A reference picture from its look, with the Image app">✦ Picture</button>${l.gone || !(l.scenes || []).length ? `<button class="ghost sm danger" data-del>Delete</button>` : ""}</div></div>`);
    $("[data-edit]", el).onclick = () => locationDialog(pr, l, draw);
    el.onclick = e => { if (!e.target.closest("button")) locationDialog(pr, l, draw); };
    $("[data-pic]", el).onclick = () => locationPicture(pr, l, draw);
    const del = $("[data-del]", el); if (del) del.onclick = () => { if (!confirm(`Delete the location “${l.full}”?`)) return; pr.locations = pr.locations.filter(x => x !== l); saveProject(pr, true); draw(); };
    grid.append(el);
  });
  $("#lnew", d).onclick = () => locationDialog(pr, null, draw);
  $("#pnext", d).onclick = () => go("storyboard");
}
// a location's details, in a form (Film Engine's): name, description, what the generator gets, lighting, time, atmosphere, sound, picture
function locationDialog(pr, l, after) {
  const isNew = !l;
  l = l || { id: rid(), name: "", full: "", description: "", look: "", lighting: "", time: "", atmosphere: "", sound: "", picture: "", intext: [], scenes: [] };
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const f = (k, label, ph, area, rows = 2) => `<div><span>${label}</span>${area ? `<textarea data-k="${k}" rows="${rows}" placeholder="${esc(ph)}">${esc(l[k] || "")}</textarea>` : `<input type="text" data-k="${k}" value="${esc(l[k] || "")}" placeholder="${esc(ph)}">`}</div>`;
  const d = h(`<div class="dialog wide locd"><div class="row" style="justify-content:space-between"><h2>${isNew ? "A new location" : esc(l.full || niceName(l.name))}</h2><button class="icon" data-x>✕</button></div>
    <div class="locd-grid"><div class="locd-pic"><div class="ref ${l.picture ? "on" : ""}" id="lp">${l.picture ? `<img src="${inputUrl(l.picture)}">` : `<span class="plus">+</span>`}<span class="rl">${l.picture ? "Reference picture" : "Drop a picture"}</span><button class="x" title="Remove">✕</button><input type="file" accept="image/*" hidden></div>
      ${(l.pictures || []).length > 1 ? `<div class="cpics">${l.pictures.map(p => `<div class="cpic ${p === l.picture ? "on" : ""}" data-p="${esc(p)}"><img src="${inputUrl(p)}"></div>`).join("")}</div>` : ""}
      ${isNew ? "" : `<button class="btn" id="lmk">✦ Make a reference picture</button>`}<p class="hint">${(l.scenes || []).length ? `In ${plural(l.scenes.length, "scene")}: ${esc(l.scenes.map(id => ((pr.scenes || []).find(s => s.id === id) || {}).number).filter(Boolean).join(", "))}.` : "In no scene yet: a scene heading with its name puts it there."}</p></div>
    <div class="cfields">
      <div class="wide"><span>Name, as the scene headings write it</span><input type="text" data-k="name" value="${esc(l.name || "")}" placeholder="THE GLASS HARBOUR DINER" style="text-transform:uppercase"></div>
      ${f("description", "Description", "What this place is, for you and the crew", true)}
      ${f("look", "What the generator gets", "How it looks, in a few words: written into every shot set here", true, 3)}
      ${f("lighting", "Lighting", "Low gold sun through the glass, neon…")}${f("time", "Time of day", "day, night, dusk…")}
      ${f("atmosphere", "Atmosphere", "Mood, weather, smell of the place…", true)}
      ${f("sound", "Sound", "Traffic two streets over, gulls, a screen door…", true)}</div></div>
    <p class="hint" style="margin:0">The sound notes are for you and whoever makes the ambience; the look and the picture go to the generator.</p>
    <div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" id="lsave">Save</button></div></div>`);
  d.querySelectorAll(".cfields > div:not(.wide)").forEach(x => { if (x.querySelector("textarea")) x.classList.add("wide"); });
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  let picture = l.picture || "";
  const lp = $("#lp", d), fi = $("input", lp);
  const showPic = () => { lp.classList.toggle("on", !!picture); lp.querySelector("img")?.remove(); if (picture) lp.prepend(h(`<img src="${inputUrl(picture)}">`)); $(".rl", lp).textContent = picture ? "Reference picture" : "Drop a picture"; };
  lp.onclick = e => { if (e.target.closest(".x")) { picture = ""; showPic(); return; } fi.click(); };
  const take = async f => { if (!f) return; try { picture = await upload(f); showPic(); } catch (e) { toast("Couldn't add the picture: " + e.message, true); } };
  fi.onchange = () => take(fi.files[0]);
  lp.ondragover = e => e.preventDefault(); lp.ondrop = e => { e.preventDefault(); take(e.dataTransfer.files[0]); };
  $$(".cpic", d).forEach(x => x.onclick = () => { picture = x.dataset.p; $$(".cpic", d).forEach(y => y.classList.toggle("on", y === x)); showPic(); });
  const mk = $("#lmk", d); if (mk) mk.onclick = () => { close(); locationPicture(pr, l, after); };
  $("#lsave", d).onclick = async () => {
    const v = k => ($(`[data-k="${k}"]`, d).value || "").trim();
    const name = v("name").toUpperCase().replace(/\s+/g, " ");
    if (!name) return toast("Give the location a name", true);
    const was = (l.name || "").toUpperCase();
    if ((pr.locations || []).some(x => x !== l && (x.name || "").toUpperCase() === name)) return toast(`There's already a location called ${name}`, true);
    Object.assign(l, { name, full: was === name && l.full ? l.full : niceName(name), description: v("description"), look: v("look"), lighting: v("lighting"), time: v("time"),
      atmosphere: v("atmosphere"), sound: v("sound"), picture, edited: true, ...(picture !== l.picture ? { pic_at: Date.now() / 1000 } : {}) });
    if (isNew) (pr.locations = pr.locations || []).push(l);
    close();
    if (!isNew && was && was !== name && (l.scenes || []).length && confirm(`Rename ${was} to ${name} in the screenplay's scene headings too?`)) {
      const n = await renamePlace(pr, was, name);
      toast(`Renamed in ${plural(n, "scene heading")}`);
    } else { await saveProject(pr, true); await syncProjectBoard(pr); }
    if (after) after();
  };
}
// a place renamed in the screenplay's scene headings (the breakdown made again, the storyboard following)
async function renamePlace(pr, from, to) {
  let n = 0;
  const text = (pr.screenplay?.text || "").split("\n").map(ln => {
    const m = ln.match(/^(\s*\.?(?:INT\.?\/EXT|EXT\.?\/INT|INT|EXT|EST|I\/E|E\/I)[. ]\s*)(.*?)(\s+[-–—]+\s+[^#]*?)?(\s*#[\w.\-]+#\s*)?$/i);
    if (!m || m[2].trim().toUpperCase() !== from) return ln;
    n++; return m[1] + to + (m[3] || "") + (m[4] || "");
  }).join("\n");
  if (!n) { await saveProject(pr, true); return 0; }
  const r = await post("/studio/api/screenplay/breakdown", { text, scenes: pr.scenes || [], shots: pr.shots || [] });
  pr.screenplay = { ...(pr.screenplay || {}), text };
  Object.assign(pr, { scenes: r.scenes, shots: r.shots, roles: r.roles, pages: r.pages });
  await saveProject(pr, true); await followScreenplay(pr);
  return n;
}
// a reference picture of a location from its look, with the Image app (kept with it; the first one is its picture)
async function locationPicture(pr, l, after) {
  const k = findApp("image"), app = k >= 0 ? S.apps[k] : null;
  if (!app) return toast("The Image app is missing", true);
  await saveProject(pr, true);
  const L = layout(app), v = buildValues(app), ref = L.refs.find(r => r.media.kind === "image");
  const prompt = [`${l.full || niceName(l.name)}${l.look ? ": " + l.look : ""}`, l.lighting && (/\blight/i.test(l.lighting) ? l.lighting : `${l.lighting} light`), l.time, l.atmosphere, "an empty set, no people, film still"].filter(Boolean).join(". ");
  try {
    const j = await post(`/studio/api/projects/${pr.id}/locations/${l.id}/picture`, { private: !!pr.private, values: v, prompt, prompt_key: L.prompt?.key, ref_key: ref?.media.key, ref_toggle_key: ref?.toggle?.key, seed_keys: app.inputs.filter(i => i.kind === "seed").map(i => i.key), target: runTarget() });
    toast(`Picturing ${l.full}…`);
    await pollJob(j.id);
    const fresh = (await api("/studio/api/projects" + PQ())).find(x => x.id === pr.id);
    const f = (fresh?.locations || []).find(x => x.id === l.id);
    if (f) Object.assign(l, { picture: f.picture, pictures: f.pictures, pic_at: f.pic_at, edited: true });
    toast(`${l.full}: a reference picture`); if (after) after();
  } catch (e) { toast("Couldn't make the picture: " + e.message.slice(0, 200), true); }
}

/* ---- 5 · the storyboard (Film Engine's): a frame per shot, its card beside it. The frames are the takes' first frames;
   made with the Image app from each shot's action, its characters' and its location's looks, or dropped in. */
const imageAspect = () => { const k = findApp("image"), app = k >= 0 ? S.apps[k] : null; if (!app) return ""; const i = app.inputs.find(x => /aspect/i.test(x.label)); const v = i ? String(valuesFor(app)[i.key] ?? i.value ?? "") : ""; return (v.match(/\d+(?:\.\d+)?:\d+/) || [""])[0]; };
function setFrame(pr, s, value) {                   // a new frame for a shot: the one it replaces is kept as a version
  if (s.keyframe && s.keyframe !== value) s.kf_versions = [s.keyframe, ...(s.kf_versions || []).filter(x => x !== s.keyframe && x !== value)].slice(0, 8);
  s.keyframe = value || ""; delete s.keyframe_out; s.kf_at = Date.now() / 1000;    // the newest frame wins a save (projects.keep_newer_media)
  if (value) s.kf_src = SR.shotSource(s, sceneOf(pr, s)); else delete s.kf_src;
}
const SBF_LINES = 3;                                // the lines of dialogue a folded card shows
function clipped(t, n) {                            // a storyboard card's text: does the folded card hide some of it?
  const open = t.classList.contains("open");
  let hid = n > SBF_LINES;
  if (!open && !hid) hid = [$(".sbf-desc", t), ...$$(".sbf-dl > div", t)].some(x => x && x.scrollHeight > x.clientHeight + 1);
  t.classList.toggle("clip", !open && hid);
  const more = $(".sbf-more", t), rest = n - SBF_LINES;
  more.hidden = !open && !hid;
  more.textContent = open ? "Show less ▴" : rest > 0 ? `Show all · ${plural(rest, "more line")} ▾` : "Show all ▾";
  t.title = open ? "Click to fold the text" : hid ? "Click to read all of it" : "";
  t.setAttribute("aria-expanded", String(open));
}
async function stepStoryboard(b, pr, go, draw) {
  const k = findApp("image"), app = k >= 0 ? S.apps[k] : null;
  const shots = pr.shots || [], codes = shotCodes(pr), locked = !!pr.boardLocked;
  const F = S.sbf = S.sbf && S.sbf.pid === pr.id ? S.sbf : { pid: pr.id, q: "", scene: "", frame: "", sort: "story", open: new Set() };
  const aspect = imageAspect(), model = app ? String(valuesFor(app)[layout(app).model?.key] || "").split("(")[0].trim() : "";
  const headed = (pr.scenes || []).filter(s => s.heading);
  const d = h(`<div class="sbf">
    <div class="sbf-bar"><label class="hint">Style <select id="sbt"><option value="">none</option>${P_TONES.map(t => `<option ${pr.tone === t ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <label class="tog" title="A shot with one cast character who has a face uses it as a reference picture; otherwise its location's picture"><input type="checkbox" id="sbref" ${pr.useRefs !== false ? "checked" : ""}><span>Use references</span></label>
      <span style="flex:1"></span><button class="ghost" id="sbprint" title="Six frames to a page, with their shots' descriptions and lines">Print storyboard</button>
      <button class="btn primary" id="sbgen" ${locked || !shots.length ? "disabled" : ""} title="${locked ? "The board is locked" : "A frame for every shot that has none"}">Generate all</button>
      <button class="ghost ${locked ? "on" : ""}" id="sblock" title="${locked ? "Unlock: frames can be made and replaced again" : "Lock the board when you're happy with it: nothing replaces a frame until you unlock it"}">${locked ? "🔒 Locked" : "Lock board"}</button></div>
    <p class="hint sbf-note">A frame for every shot of the breakdown, made with the Image app${model ? ` (<b>${esc(model)}</b>, change it in Create)` : ""} from the shot's action, its characters' looks and its location's. They're the first frames of the takes. <span id="sbst"></span></p>
    <div class="filters sbf-filters"><input type="text" class="search" id="sbq" placeholder="Search…" value="${esc(F.q)}">
      <select id="sbsc"><option value="">Scene: all</option>${headed.map(s => `<option value="${s.id}" ${F.scene === s.id ? "selected" : ""}>Scene ${esc(s.number)}</option>`).join("")}</select>
      <select id="sbfr"><option value="">Frame: all</option><option value="has">has a frame</option><option value="none">no frame yet</option><option value="old">from an earlier draft</option></select>
      <select id="sbso"><option value="story">Story order</option><option value="code">Shot code</option><option value="long">Longest first</option></select><span class="hint" id="sbn"></span></div>
    <div class="sbf-grid"></div>
    <div class="row" style="margin-top:14px"><button class="btn primary" id="pnext">Next: takes →</button></div></div>`);
  b.append(d);
  $("#sbfr", d).value = F.frame; $("#sbso", d).value = F.sort;
  const grid = $(".sbf-grid", d);
  const people = s => (pr.cast || []).map(id => S.chars.find(c => c.id === id)).filter(c => c && new RegExp("\\b" + c.name.split(" ")[0] + "\\b", "i").test((s.characters || []).join(" ") + " " + (s.action || "")));
  const visible = () => {
    const q = F.q.trim().toLowerCase();
    let list = shots.filter(s => (!F.scene || s.scene === F.scene) && (!q || [codes[s.id], s.title, s.action, s.camera, (s.lines || []).map(l => l.who + " " + l.text).join(" ")].join(" ").toLowerCase().includes(q))
      && (!F.frame || (F.frame === "has" ? !!s.keyframe : F.frame === "none" ? !s.keyframe : frameDrift(pr, s).length > 0)));
    if (F.sort === "code") list = list.slice().sort((a, z) => codes[a.id].localeCompare(codes[z.id], undefined, { numeric: true }));
    if (F.sort === "long") list = list.slice().sort((a, z) => (+z.seconds || 0) - (+a.seconds || 0));
    return list;
  };
  const card = s => {
    const sc = sceneOf(pr, s), drift = frameDrift(pr, s), lines = (s.lines || []).filter(l => l.text), loc = locationOf(pr, sc);
    const off = lines.length && lines.every(l => /\((V\.?O|O\.?S|O\.?C)\.?\)/i.test(l.who || ""));
    const el = h(`<div class="sbf-card ${drift.length ? "drift" : ""}" data-shot="${s.id}">
      <div class="sbf-img ${s.keyframe ? "" : "none"}" title="${s.keyframe ? "Open full screen" : "Drop a picture here"}">${s.keyframe ? `<img src="${inputUrl(s.keyframe)}" loading="lazy" alt="">` : `<span>No frame yet<small>${locked ? "the board is locked" : "generate it, or drop a picture here"}</small></span>`}${aspect ? `<i class="sbf-ar">${esc(aspect)}</i>` : ""}<input type="file" accept="image/*" hidden></div>
      <div class="sbf-body"><div class="sbf-head"><b>Sc${esc(sc?.number || "–")} / ${esc(codes[s.id])}</b><select class="sbf-cam" title="The camera">${Object.keys(P_CAMS).map(c => `<option ${s.camera === c ? "selected" : ""}>${c}</option>`).join("")}</select></div>
        <div class="sbf-acts"><button class="ghost sm" data-gen ${locked || !app || S.kfBusy === pr.id ? "disabled" : ""} title="${s.keyframe ? "Make this frame again from the shot" : "Make this shot's frame"}">${s.keyframe ? "Regen" : "Generate"}</button><button class="ghost sm" data-imp ${locked ? "disabled" : ""} title="Use a picture of your own">Import</button><button class="ghost sm" data-ver ${(s.kf_versions || []).length ? "" : "disabled"} title="${(s.kf_versions || []).length ? "The frames this shot had before" : "No earlier frames"}">v${(s.kf_versions || []).length + (s.keyframe ? 1 : 0) || 1}</button><button class="ghost sm" data-add title="A new shot right after this one, in the same scene">+ Shot</button><button class="ghost sm" data-dir title="The shot in the breakdown: its beats, camera and length">Direct</button>${s.keyframe && !locked ? `<button class="ghost sm" data-clr title="No frame for this shot">Clear</button>` : ""}</div>
        <div class="sbf-tags">${drift.length ? `<span class="tag warn" data-drift title="The screenplay changed this shot after its frame was made">↻ from an earlier draft</span>` : ""}${lines.length ? `<span class="tag ${off ? "vo" : "speak"}" title="${esc(lines[0].who + ": " + lines[0].text)}">${off ? "V.O." : "speaks"} ×${lines.length}</span>` : ""}${people(s).map(c => `<span class="tag">${esc(c.name.split(" ")[0])}</span>`).join("")}${loc ? `<span class="tag place" title="${esc(loc.look || "")}">◎ ${esc(loc.full)}</span>` : ""}<span class="tag">${+s.seconds || 5} s</span>${s.manual ? `<span class="tag dim">added by hand</span>` : ""}</div>
        <div class="sbf-txt ${F.open.has(s.id) ? "open" : ""}"><p class="sbf-desc">${esc(s.action || s.title || "")}</p>
        ${lines.length ? `<div class="sbf-dl">${lines.map(l => `<div><b>${esc(niceName(l.who))}</b>${l.paren ? ` <i>${esc(l.paren)}</i>` : ""}<span>“${esc(l.text)}”</span></div>`).join("")}</div>` : ""}
        <button class="sbf-more" hidden></button></div></div></div>`);
    const img = $(".sbf-img", el), fi = $("input", img);
    const take = async f => {
      if (locked) return toast("The board is locked", true);
      try { setFrame(pr, s, await upload(f)); } catch (e) { return toast("Couldn't add the picture: " + e.message, true); }
      await saveProject(pr, true); await syncProjectBoard(pr); draw();
    };
    img.onclick = () => { if (s.keyframe) frameViewer(pr, visible().filter(x => x.keyframe), s); else if (!locked) fi.click(); };
    fi.onchange = () => fi.files[0] && take(fi.files[0]);
    img.ondragover = e => e.preventDefault(); img.ondrop = e => { e.preventDefault(); if (e.dataTransfer.files[0]) take(e.dataTransfer.files[0]); };
    $(".sbf-cam", el).onchange = async e => { s.camera = e.target.value; s.camera_set = true; await saveProject(pr, true); await followScreenplay(pr); draw(); };
    $("[data-gen]", el).onclick = () => run([s]);
    $("[data-imp]", el).onclick = () => fi.click();
    $("[data-ver]", el).onclick = () => frameVersions(pr, s, draw);
    $("[data-add]", el).onclick = async () => {
      pr.shots.splice(pr.shots.indexOf(s) + 1, 0, { id: rid(), scene: s.scene, title: "Added shot", action: "", lines: [], beats: [], dialogue: "", characters: [...(s.characters || [])], camera: s.camera, seconds: 5, manual: true, after: s.id });
      await rebreak(pr); S.scnJump = null; toast("A new shot after " + codes[s.id] + ": say what happens in it in Scenes & shots (Direct)."); draw();
    };
    $("[data-dir]", el).onclick = () => { S.scnJump = s.id; go("scenes"); };
    const clr = $("[data-clr]", el); if (clr) clr.onclick = async () => { setFrame(pr, s, ""); await saveProject(pr, true); await syncProjectBoard(pr); draw(); };
    const dr = $("[data-drift]", el); if (dr) dr.onclick = () => driftDialog(pr, s, () => run([s]), draw);
    // text longer than the card shows: a click on it (or on "Show all") reveals the rest, another folds it again
    const txt = $(".sbf-txt", el), flip = () => { if (F.open.has(s.id)) F.open.delete(s.id); else F.open.add(s.id); txt.classList.toggle("open", F.open.has(s.id)); clipped(txt, lines.length); };
    txt.onclick = e => { if (!txt.classList.contains("clip") && !txt.classList.contains("open")) return; if (e.target.closest(".sbf-more") || !String(getSelection() || "")) flip(); };
    return el;
  };
  const paint = () => {
    const list = visible(); grid.innerHTML = "";
    $("#sbn", d).textContent = plural(list.length, "frame") + (list.length !== shots.length ? ` of ${shots.length}` : "");
    if (!shots.length) grid.append(h(`<div class="empty">No shots yet: they come from the screenplay's scenes (Scenes &amp; shots).</div>`));
    else if (!list.length) grid.append(h(`<div class="empty">No frame matches.</div>`));
    list.forEach(s => grid.append(card(s)));
    measure();
  };
  // which cards hide some of their text (it depends on the width, so again when the grid is resized)
  const measure = () => $$(".sbf-txt", grid).forEach(t => clipped(t, $$(".sbf-dl > div", t).length));
  let rq = 0; const ro = new ResizeObserver(() => { if (!grid.isConnected) return ro.disconnect(); cancelAnimationFrame(rq); rq = requestAnimationFrame(measure); });
  ro.observe(grid);
  paint();
  $("#sbq", d).oninput = e => { F.q = e.target.value; paint(); };
  $("#sbsc", d).onchange = e => { F.scene = e.target.value; paint(); };
  $("#sbfr", d).onchange = e => { F.frame = e.target.value; paint(); };
  $("#sbso", d).onchange = e => { F.sort = e.target.value; paint(); };
  $("#sbt", d).onchange = e => { pr.tone = e.target.value; saveProject(pr); };
  $("#sbref", d).onchange = e => { pr.useRefs = e.target.checked; saveProject(pr); };
  $("#sblock", d).onclick = async () => { pr.boardLocked = !locked; await saveProject(pr, true); toast(pr.boardLocked ? "The board is locked: nothing replaces a frame until you unlock it." : "The board is unlocked."); draw(); };
  $("#sbprint", d).onclick = () => printStoryboard(pr);
  $("#pnext", d).onclick = () => go("takes");
  $("#sbgen", d).onclick = () => {
    const todo = shots.filter(s => !s.keyframe);
    if (todo.length) return run(todo);
    if (confirm("Every shot has a frame. Make them all again? (The frames they have now are kept as versions.)")) run(shots);
  };
  const run = async list => {
    if (pr.boardLocked) return toast("The board is locked", true);
    if (!app) return toast("The Image app is missing", true);
    if (S.kfBusy === pr.id) return toast("Frames are being made for this storyboard: the next ones wait for them", true);
    S.kfBusy = pr.id; $$("[data-gen], #sbgen", d).forEach(x => x.disabled = true);
    const L = layout(app), v = buildValues(app);
    const ref = L.refs.find(r => r.media.kind === "image");
    const items = list.map(s => {
      const inShot = people(s), faces = inShot.filter(c => c.face), fam = L.model ? modelFamily(v[L.model.key]) : "";
      const loras = inShot.map(c => c.lora).filter(l => l && l.name && !(l.family && fam && l.family !== fam)).map(l => ({ name: l.name, strength: +l.strength || 0.8 }));
      const place = locationOf(pr, sceneOf(pr, s));
      const pic = pr.useRefs === false || !ref ? "" : faces.length === 1 ? faces[0].face : !inShot.length && place && place.picture ? place.picture : "";
      return { id: s.id, prompt: shotText(pr, s, false) + (pr.tone ? ` ${pr.tone} style, film still.` : ""), ref: pic, loras };
    });
    const stat = t => { const e = $("#sbst", d); if (e) e.textContent = t; };
    try {
      await saveProject(pr, true);
      const r = await post(`/studio/api/projects/${pr.id}/keyframes`, { private: !!pr.private, values: v, shots: items, redo: true, prompt_key: L.prompt?.key, ref_key: ref?.media.key, ref_toggle_key: ref?.toggle?.key, seed_keys: app.inputs.filter(i => i.kind === "seed").map(i => i.key), target: runTarget() });
      toast(`Making ${plural(items.length, "frame")}. They render one after another; you can leave this page.`);
      // each frame is saved on the server as it's made (with the one it replaces as a version): take them in as they come
      const takeIn = async () => {
        const fresh = (await api("/studio/api/projects" + PQ()).catch(() => [])).find(x => x.id === pr.id);
        (fresh?.shots || []).forEach(f => { const s = (pr.shots || []).find(x => x.id === f.id); if (s && f.keyframe && (f.kf_at || 0) > (s.kf_at || 0)) Object.assign(s, { keyframe: f.keyframe, keyframe_out: f.keyframe_out, kf_versions: f.kf_versions, kf_src: f.kf_src, kf_at: f.kf_at }); });
        if (b.isConnected) paint();
      };
      let taken = 0;
      await pollJob(r.id, async st => { stat(st); const n = +(String(st).match(/Keyframe (\d+)/) || [])[1] || 0; if (n > taken + 1) { taken = n - 1; await takeIn(); } });
      await takeIn(); stat("");
      pr.stage = "Storyboard"; await saveProject(pr, true); await syncProjectBoard(pr); toast(`${plural(items.length, "frame")} ready`);
    } catch (e) { stat(""); toast("Frames stopped: " + e.message.slice(0, 200), true); }
    finally { S.kfBusy = null; if (b.isConnected) draw(); }
  };
}
// the frames full screen, one after another (← →), with their shots
function frameViewer(pr, list, s) {
  const codes = shotCodes(pr); let k = Math.max(0, list.indexOf(s));
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="sbf-view"><button class="icon sbf-x" data-x title="Close (Esc)">✕</button><button class="sbf-nav prev">‹</button><img alt=""><button class="sbf-nav next">›</button><div class="sbf-cap"></div></div>`);
  m.append(d);
  const show = () => { const x = list[k], sc = sceneOf(pr, x); $("img", d).src = inputUrl(x.keyframe); $(".sbf-cap", d).innerHTML = `<b>Sc${esc(sc?.number || "–")} / ${esc(codes[x.id])}</b> ${esc(x.title || "")} <span class="hint">${k + 1} of ${list.length} · ${esc(x.camera || "")} · ${+x.seconds || 5} s</span>`; };
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; document.removeEventListener("keydown", key); };
  const key = e => { if (!d.isConnected) return document.removeEventListener("keydown", key); if (e.key === "Escape") close(); else if (e.key === "ArrowRight") { k = (k + 1) % list.length; show(); } else if (e.key === "ArrowLeft") { k = (k - 1 + list.length) % list.length; show(); } };
  document.addEventListener("keydown", key);
  $("[data-x]", d).onclick = close; $(".prev", d).onclick = () => key({ key: "ArrowLeft" }); $(".next", d).onclick = () => key({ key: "ArrowRight" });
  show();
}
// the frames a shot had, to go back to one
function frameVersions(pr, s, after) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const all = [s.keyframe, ...(s.kf_versions || [])].filter(Boolean);
  const d = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>Shot ${esc(shotCodes(pr)[s.id])}: its frames</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">The newest first. Pick one to make it the frame again (the one it replaces is kept here).</p>
    <div class="sbf-vers">${all.map((v, i) => `<div class="sbf-ver ${i === 0 ? "on" : ""}" data-v="${esc(v)}"><img src="${inputUrl(v)}"><span>${i === 0 ? "Now" : "v" + (all.length - i)}</span></div>`).join("")}</div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  $$(".sbf-ver", d).forEach(x => x.onclick = async () => {
    if (x.dataset.v === s.keyframe) return close();
    if (pr.boardLocked) return toast("The board is locked", true);
    const v = x.dataset.v; s.kf_versions = (s.kf_versions || []).filter(y => y !== v); const src = s.kf_src; setFrame(pr, s, v); if (src) s.kf_src = src;
    await saveProject(pr, true); await syncProjectBoard(pr); close(); if (after) after();
  });
}
// what the screenplay changed on a shot since its frame was made: keep the frame, or make it again
function driftDialog(pr, s, regen, after) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>Shot ${esc(shotCodes(pr)[s.id])}: the screenplay changed</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">Since its frame was made:</p><ul class="chlist">${frameDrift(pr, s).map(changeHtml).join("")}</ul>
    <div class="foot"><button class="ghost" data-keep>Keep the frame</button><button class="btn primary" data-regen ${pr.boardLocked ? "disabled" : ""}>Make it again</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  $("[data-keep]", d).onclick = async () => { s.kf_src = SR.shotSource(s, sceneOf(pr, s)); await saveProject(pr, true); close(); navRefresh(); if (after) after(); };
  $("[data-regen]", d).onclick = () => { close(); regen(); };
}
// the storyboard to print: six frames a page, each with its shot, description and lines
function printStoryboard(pr) {
  const codes = shotCodes(pr), shots = (pr.shots || []);
  const abs = u => new URL(u, location.href).href;
  const cells = shots.map(s => { const sc = sceneOf(pr, s); return `<div class="c"><div class="f">${s.keyframe ? `<img src="${abs(inputUrl(s.keyframe))}">` : ""}</div><b>Sc${esc(sc?.number || "–")} / ${esc(codes[s.id])}</b> <small>${esc(s.camera || "")} · ${+s.seconds || 5} s</small><p>${esc((s.action || s.title || "").slice(0, 260))}</p>${(s.lines || []).slice(0, 2).map(l => `<p class="l"><b>${esc(niceName(l.who))}:</b> “${esc(l.text)}”</p>`).join("")}</div>`; });
  const pages = []; for (let i = 0; i < cells.length; i += 6) pages.push(`<section><h1>${esc(pr.title || "Storyboard")}<span>${i / 6 + 1}</span></h1><div class="g">${cells.slice(i, i + 6).join("")}</div></section>`);
  const w = window.open("", "_blank"); if (!w) return toast("Allow pop-ups to print the storyboard", true);
  w.document.write(`<!doctype html><title>${esc(pr.title || "Storyboard")} · storyboard</title><style>
    @page{size:letter landscape;margin:.4in}body{font:10px -apple-system,"Segoe UI",sans-serif;margin:0;color:#111}
    section{page-break-after:always;height:7.6in;display:flex;flex-direction:column}h1{font-size:13px;margin:0 0 6px;display:flex;justify-content:space-between}
    .g{display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:repeat(2,1fr);gap:10px;flex:1}.c{border:1px solid #ccc;padding:6px;overflow:hidden}
    .f{aspect-ratio:16/9;background:#eee;margin-bottom:5px}.f img{width:100%;height:100%;object-fit:cover}p{margin:3px 0;line-height:1.3}.l{font-style:italic}small{color:#666}</style>${pages.join("")}`);
  w.document.close(); w.onload = () => w.print();
}

/* ---- 6 · the takes: exactly the breakdown's shots, in its order, with its numbers, cameras and lengths
   (projects.to_board does the same for Claude). What is done on the board stays: takes, picks, finishes, trims, and a
   prompt rewritten there (marked, and one click from the breakdown's again). */
const camKey = words => Object.keys(P_CAMS).find(k => P_CAMS[k] === words) || "";
function syncBoard(pr, board) {
  const old = board.shots || [], ids = new Set((pr.shots || []).map(s => s.id));
  const fresh = !old.length;                     // a board made now: nothing on it has changed yet
  board.cut = board.cut || [];
  // board shots that belong to no breakdown shot (made on an older board) join the breakdown, so nothing is lost
  old.filter(x => !x.projShot).forEach(x => {
    const last = (pr.scenes || []).slice(-1)[0];
    const ns = { id: Math.random().toString(36).slice(2, 10), scene: x.scene || last?.id, title: (x.prompt || "Shot").split(/[.!?]/)[0].slice(0, 60), action: x.prompt || "", lines: [], dialogue: "", characters: [], camera: camKey(x.camera) || "static", seconds: x.seconds || 5, manual: true, keyframe: x.image || "" };
    pr.shots.push(ns); x.projShot = ns.id; ids.add(ns.id); x.auto = x.prompt;
  });
  const durIn = (videoSummary()?.app.inputs || []).find(i => /^duration/i.test(i.label));
  const codes = shotCodes(pr);
  board.shots = (pr.shots || []).map(s => {
    const text = shotText(pr, s, false, true);
    let bs = old.find(x => x.projShot === s.id);
    if (!bs && s.anchor) {                       // cut before, back in the screenplay (an undo, a paste): with its takes
      const back = board.cut.find(x => x.anchor === s.anchor);
      if (back) { board.cut = board.cut.filter(x => x !== back); bs = { ...back, projShot: s.id }; delete bs.cutAt; }
    }
    if (!bs) { bs = newShot(text, s.keyframe || ""); bs.projShot = s.id; bs.auto = text; if (!fresh) bs.seen = null; }
    if (!bs.prompt || !("auto" in bs) || bs.prompt === bs.auto) bs.prompt = text;
    bs.auto = text;
    // what the shot is made from (SR.shotSource), and as it was when last looked at: its changes are said from that
    bs.src = SR.shotSource(s, sceneOf(pr, s));
    if (!("seen" in bs)) bs.seen = bs.src;
    // first frame <-> keyframe: one picture. Changed on the board since the last sync: the board's wins; else the keyframe's
    if ("kf" in bs && (bs.image || "") !== (bs.kf || "")) setFrame(pr, s, bs.image || "");
    else if (!("kf" in bs) && !s.keyframe && bs.image) s.keyframe = bs.image;
    bs.image = s.keyframe || ""; bs.kf = bs.image;
    const k = pr.shots.indexOf(s), prev = s.continues && k > 0 && pr.shots[k - 1].scene === s.scene ? codes[pr.shots[k - 1].id] : null;
    Object.assign(bs, { scene: s.scene, code: codes[s.id], title: s.title || "", seconds: +s.seconds || 5, camera: P_CAMS[s.camera] || "", cont: prev, anchor: s.anchor || "" });
    if (!bs.trimSet) bs.trimOut = durIn && parseFloat(renderLength(durIn, bs.seconds)) > bs.seconds ? bs.seconds : 0;   // the cut is the shot's length
    return bs;
  });
  // cut from the screenplay: a shot with takes keeps them in the board's "cut" list (and gets them back if it returns)
  const kept = new Set(board.shots.map(x => x.projShot));
  old.forEach(x => { if (x.projShot && !kept.has(x.projShot) && ((x.takes || []).some(t => t.output) || x.final)) board.cut.push({ ...x, cutAt: Date.now() / 1000 }); });
  board.cut = board.cut.slice(-50);
  board.name = pr.title || board.name || "Project film"; board.project = pr.id;
}
/* what the screenplay changed on a storyboard shot since it was last looked at (SR.shotChanges), and which of its takes
   were made from an earlier text */
const changesOf = bs => bs && bs.src && "seen" in bs ? SR.shotChanges(bs.seen, bs.src) : [];
const staleTakes = bs => (bs.takes || []).filter(t => t.output && (t.prompt != null ? t.prompt !== bs.prompt : changesOf(bs).length > 0));
const boardOf = pr => pr && pr.board ? S.boards.find(b => b.id === pr.board) : null;
// the storyboard follows the screenplay: its shots made again from the breakdown, saved -> {pending, cut} (null: no board yet)
async function syncProjectBoard(pr) {
  if (!pr || !pr.board) return null;
  if (!boardOf(pr)) await loadBoards();
  const board = boardOf(pr); if (!board) return null;
  const before = JSON.stringify((pr.shots || []).map(s => [s.id, s.keyframe || ""]));
  syncBoard(pr, board);
  await saveBoard(board, true);
  if (JSON.stringify((pr.shots || []).map(s => [s.id, s.keyframe || ""])) !== before) saveProject(pr);
  navRefresh();
  return { pending: board.shots.filter(x => changesOf(x).length).length, cut: board.cut.length };
}
// projShot id -> what changed on its storyboard shot, in a few words (the screenplay's margin)
function boardChangeMap(pr) {
  const out = new Map(), board = boardOf(pr); if (!board) return out;
  const word = { new: "new shot", setting: "where and when", action: "action", line_changed: "dialogue", line_added: "dialogue", line_removed: "dialogue", characters: "who is in it", camera: "camera", length: "length" };
  board.shots.forEach(bs => { const ch = changesOf(bs); if (ch.length) out.set(bs.projShot, [...new Set(ch.map(c => word[c.kind] || c.kind))].join(", ")); });
  return out;
}
const chq = t => `“${esc(String(t || "").length > 160 ? String(t).slice(0, 157) + "…" : t)}”`;
function changeHtml(c) {
  const who = w => `<b>${esc(niceName(w || ""))}</b>`;
  return `<li class="ch-${c.kind}">${{
    new: () => "New in the screenplay",
    setting: () => `Where and when: ${esc(c.from || "–")} → ${esc(c.to)}`,
    action: () => `The action: <del>${chq(c.from)}</del> → <ins>${chq(c.to)}</ins>`,
    line_changed: () => `${who(c.who)}: <del>${chq(c.from)}</del> → <ins>${chq(c.to)}</ins>`,
    line_added: () => `${who(c.who)}: <ins>${chq(c.line)}</ins> <span class="hint">added</span>`,
    line_removed: () => `${who(c.who)}: <del>${chq(c.line)}</del> <span class="hint">cut</span>`,
    characters: () => `In the shot: ${esc((c.from || []).map(niceName).join(", ") || "nobody")} → ${esc((c.to || []).map(niceName).join(", ") || "nobody")}`,
    camera: () => `Camera: ${esc(c.from || "–")} → ${esc(c.to)}`,
    length: () => `Length: ${c.from || "–"} s → ${c.to} s`,
  }[c.kind]?.() || esc(c.kind)}</li>`;
}
// the shots the screenplay changed, scene by scene, in words: on the takes (since you last looked) and in the storyboard
// (since its frame was made); the takes cut from it
async function changesDialog(pr, after) {
  await syncProjectBoard(pr);
  const board = boardOf(pr);
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; if (after) after(); };
  const rows = (pr.shots || []).map(s => {
    const bs = board && board.shots.find(x => x.projShot === s.id), took = bs ? changesOf(bs) : [], drift = frameDrift(pr, s);
    return { s, bs, ch: took.length ? took : drift, took: took.length > 0, drift: drift.length > 0 };
  }).filter(x => x.ch.length);
  const scenes = []; rows.forEach(x => { const sc = sceneOf(pr, x.s); let g = scenes.find(y => y.id === x.s.scene); if (!g) scenes.push(g = { id: x.s.scene, label: sc ? `${sc.number} · ${sc.heading || "Opening"}` : "Other shots", rows: [] }); g.rows.push(x); });
  const codes = shotCodes(pr);
  const d = h(`<div class="dialog wide chg"><div class="row" style="justify-content:space-between"><h2>What the screenplay changed on the shots</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">The storyboard and the takes follow the screenplay as you write: each shot's action, lines, camera and length are the breakdown's (a prompt you rewrote on the takes is kept). What changed is below: since its storyboard frame was made, and since you last looked at its takes. Frames and takes already made stay, and say when they were made from the earlier text.</p>
    ${rows.length ? scenes.map(g => `<div class="chs"><h3>${esc(g.label)}</h3>${g.rows.map(x => {
      const stale = x.bs ? staleTakes(x.bs).length : 0, own = x.bs && x.bs.prompt !== x.bs.auto;
      return `<div class="chshot" data-id="${x.s.id}"><div class="row"><b>Shot ${esc(codes[x.s.id] || "")}</b><span class="hint">${esc(x.s.title || "")}</span>${x.took ? `<button class="ghost sm" data-ok style="margin-left:auto">Reviewed ✓</button>` : ""}</div>
        <ul>${x.ch.map(changeHtml).join("")}</ul>
        ${x.drift ? `<p class="chnote">Its storyboard frame was made from the earlier text. <button class="ghost sm" data-keep>Keep the frame</button> <a href="#/projects/${pr.id}/storyboard">Make it again on the storyboard</a></p>` : ""}
        ${stale ? `<p class="chnote">${plural(stale, "take")} made from the earlier text: draft new takes.</p>` : ""}
        ${own ? `<p class="chnote">You rewrote this shot's prompt on the takes, so it was kept. <button class="ghost sm" data-auto>Use the screenplay's</button></p>` : ""}</div>`;
    }).join("")}</div>`).join("") : `<p>✓ Nothing changed since the frames were made and you last looked at the takes: they're the screenplay's.</p>`}
    ${board && board.cut.length ? `<details class="chcut"><summary>Cut from the screenplay: ${plural(board.cut.length, "shot")} with takes (kept, and back if the shot comes back)</summary>${board.cut.slice().reverse().map(x => `<div class="row" data-cut="${x.id}"><b>Shot ${esc(x.code || "")}</b><span class="hint">${esc(x.title || (x.prompt || "").slice(0, 80))} · ${plural((x.takes || []).filter(t => t.output).length, "take")}</span>${x.output ? `<button class="ghost sm" data-view style="margin-left:auto">Watch</button>` : ""}<button class="ghost sm" data-forget>Forget</button></div>`).join("")}</details>` : ""}
    <div class="foot">${rows.some(x => x.took) ? `<button class="ghost" data-all>Mark all reviewed</button>` : ""}<button class="ghost" data-x>Close</button><a class="btn" href="#/projects/${pr.id}/storyboard">Open the storyboard</a><a class="btn primary" href="#/projects/${pr.id}/takes">Open the takes</a></div></div>`);
  m.append(d);
  $$("[data-x]", d).forEach(x => x.onclick = close); $$("a", d).forEach(a => a.addEventListener("click", () => { m.classList.add("hidden"); m.innerHTML = ""; }));
  const done = async () => { if (board) await saveBoard(board, true); await saveProject(pr, true); navRefresh(); changesDialog(pr, after); };
  $$(".chshot", d).forEach(el => {
    const x = rows.find(r => r.s.id === el.dataset.id);
    const ok = $("[data-ok]", el); if (ok) ok.onclick = () => { x.bs.seen = x.bs.src; done(); };
    const kp = $("[data-keep]", el); if (kp) kp.onclick = () => { x.s.kf_src = SR.shotSource(x.s, sceneOf(pr, x.s)); done(); };
    const au = $("[data-auto]", el); if (au) au.onclick = () => { x.bs.prompt = x.bs.auto; done(); };
  });
  const all = $("[data-all]", d); if (all) all.onclick = () => { board.shots.forEach(bs => { if (bs.src) bs.seen = bs.src; }); done(); };
  $$("[data-cut]", d).forEach(el => {
    const x = board.cut.find(c => c.id === el.dataset.cut);
    $("[data-forget]", el).onclick = () => { if (!confirm(`Forget shot ${x.code}? Its videos stay in the Gallery.`)) return; board.cut = board.cut.filter(c => c !== x); done(); };
    const v = $("[data-view]", el); if (v) v.onclick = () => lightbox({ ...x.output, private: !!board.private, kind: "video", mtime: Date.now() / 1000, size: 0 });
  });
}
async function stepBoard(b, pr) {
  await loadBoards();
  let board = S.boards.find(x => x.id === pr.board);
  if (!board) { board = { name: pr.title || "Project film", shots: [], private: !!pr.private, takes: 2 }; S.boards.push(board); }
  syncBoard(pr, board);
  await saveBoard(board, true);
  pr.board = board.id; if (!["Storyboard", "Film"].includes(pr.stage)) pr.stage = "Storyboard";
  await saveProject(pr, true);
  S.boardId = board.id; store.set("board", board.id);
  if (!board.shots.length) return b.append(h(`<div class="empty">No shots yet: they come from the screenplay's scenes (Pre-production › Scenes &amp; shots).</div>`));
  const wrap = h(`<div class="pboard"></div>`); b.append(wrap);
  const shotOf = bs => (pr.shots || []).find(x => x.id === bs.projShot);
  const commit = async (again = true) => { if (again) await rebreak(pr); else await saveProject(pr, true); syncBoard(pr, board); await saveBoard(board, true); };
  const newId = () => Math.random().toString(36).slice(2, 10);
  // a shot the user owns: added by hand, or made before the screenplay (it belongs to no scene)
  const isOwn = s => !!s && (s.manual || !(pr.scenes || []).some(x => x.id === s.scene));
  await boardView(wrap, board, {
    sceneOf: s => { const sc = (pr.scenes || []).find(x => x.id === s.scene); return sc ? `${sc.number} · ${sc.heading || "Opening"}` : "Other shots"; },
    code: s => s.code || "",
    project: {
      changes: after => changesDialog(pr, after),
      isAdded: bs => isOwn(shotOf(bs)),
      frame: (bs, image) => { const s = shotOf(bs); if (s) { setFrame(pr, s, image || ""); bs.kf = s.keyframe; saveProject(pr); } },
      canMove: bs => isOwn(shotOf(bs)),                                  // setups keep the story's order
      canRemove: bs => { const s = shotOf(bs); return !!s && (isOwn(s) || pr.shots.filter(x => x.scene === s.scene && !x.manual)[0] !== s); },
      camera: (bs, words) => { const s = shotOf(bs); if (s) { s.camera = camKey(words) || "static"; s.camera_set = true; saveProject(pr); } },
      remove: async bs => {
        const s = shotOf(bs); if (!s) return;
        if (isOwn(s)) { pr.shots = pr.shots.filter(x => x !== s); return commit(false); }
        const sc = (pr.scenes || []).find(x => x.id === s.scene); if (!sc) return;
        if ((sc.splits || []).includes(s.anchor)) sc.splits = sc.splits.filter(x => x !== s.anchor);
        else sc.joins = [...new Set([...(sc.joins || []), s.anchor])];
        await commit();
      },
      move: async (bs, dir) => {
        const s = shotOf(bs); if (!isOwn(s)) return;
        const same = pr.shots.filter(x => x.scene === s.scene), k = same.indexOf(s), to = k + dir; if (to < 0 || to >= same.length) return;
        const order = same.filter(x => x !== s); order.splice(to, 0, s);
        const at = order.indexOf(s); s.after = at === 0 ? "^" : order[at - 1].id;
        const start = pr.shots.indexOf(same[0]); pr.shots = pr.shots.filter(x => !same.includes(x)); pr.shots.splice(start, 0, ...order);
        await commit(false);
      },
      add: async () => {
        const sc = (pr.scenes || []).filter(x => !x.omitted).slice(-1)[0], last = pr.shots.filter(x => x.scene === sc?.id).slice(-1)[0];
        pr.shots.splice(last ? pr.shots.indexOf(last) + 1 : pr.shots.length, 0, { id: newId(), scene: sc?.id, title: "Added shot", action: "", lines: [], beats: [], dialogue: "", characters: [], camera: "static", seconds: 5, manual: true, after: last ? last.id : "^" });
        await commit(false);
      },
      insertAfter: async (bs, image) => {           // "Continue →": the next shot starts on this one's last frame
        const s = shotOf(bs); if (!s) return { code: "", chained: false };
        const next = pr.shots[pr.shots.indexOf(s) + 1];
        if (next && next.continues && next.scene === s.scene) {   // one continuous take: the next move starts right here
          next.keyframe = image; const nb = board.shots.find(x => x.projShot === next.id); if (nb) { nb.image = image; nb.kf = image; }
          await commit(false); return { code: shotCodes(pr)[next.id], chained: true };
        }
        const ns = { id: newId(), scene: s.scene, title: "Continues " + (bs.code || ""), action: "", lines: [], beats: [], dialogue: "", characters: [...(s.characters || [])], camera: s.camera, seconds: s.seconds, manual: true, keyframe: image, after: s.id };
        pr.shots.splice(pr.shots.indexOf(s) + 1, 0, ns); await commit(false);
        return { code: shotCodes(pr)[ns.id], chained: false };
      },
    },
  });
}

/* ---- 6 · sound and the film */
function stepSound(b, pr, go) {
  const shots = pr.shots || [];
  const total = shots.reduce((a, s) => a + (+s.seconds || 5), 0);
  const codes = shotCodes(pr);
  const lines = shots.flatMap(s => (s.lines || []).map((l, k) => ({ s, l, k, n: codes[s.id] })));
  const d = h(`<div><div class="panel pform"><label class="lbl">Music (${filmLength(shots)}, the film's length)</label><textarea id="pm" rows="2" placeholder="genre, mood, instruments"></textarea>
    <div class="row"><button class="btn primary" id="pmk">♫ Make the music</button><span class="hint" id="pms">${pr.music_file ? "Made: " + esc(pr.music_file.filename) : ""}</span></div>${pr.music_file ? `<audio controls src="${fileUrl(pr.music_file, pr.music_file.private)}"></audio>` : ""}</div>
    <div class="panel pform"><label class="lbl">Dialogue (${plural(lines.length, "line")})</label>${lines.length ? `<div class="vlines">${lines.slice(0, 400).map(({ s, l, k, n }) => `<div class="hint"><b>${esc(niceName(l.who))}</b> “${esc(l.text)}” <i>(shot ${n})</i>${(s.voice_files || [])[k] ? " ✓" : ""}</div>`).join("")}</div>` : `<p class="hint">No dialogue in the screenplay.</p>`}
    ${lines.length ? `<div class="row"><button class="btn" id="pvk">☺ Make the voice lines</button><span class="hint" id="pvs"></span></div><p class="hint">Each line in its character's voice when they have a voice sample (Characters page). The lines are in Music &amp; voices.</p>` : ""}</div>
    <div class="panel"><p class="hint" style="margin-top:0">The cut is the Film edit: each shot's finished take (or its picked one), with titles, crossfades and the music bed.</p><div class="row"><button class="btn primary" id="pfo">Next: film edit →</button></div></div></div>`);
  b.append(d);
  $("#pm", d).value = pr.music || ""; $("#pm", d).oninput = e => { pr.music = e.target.value; saveProject(pr); };
  $("#pmk", d).onclick = async () => {
    if (!(pr.music || "").trim()) return toast("Describe the music first", true);
    const btn = $("#pmk", d); btn.disabled = true;
    try {
      const r = await post("/studio/api/music", { tags: pr.music, instrumental: true, seconds: Math.min(240, Math.max(15, total)), name: (pr.title || "Film") + " score", private: !!pr.private, target: runTarget() });
      const res = await pollJob(r.id, st => { const e = $("#pms", d); if (e) e.textContent = st + "…"; });
      pr.music_file = res; saveProject(pr, true); toast("Music ready. It's in Music & voices; pick it as the music in the film editor."); const e = $("#pms", d); if (e) e.textContent = "Made: " + res.filename;
    } catch (e) { toast("Music failed: " + e.message.slice(0, 160), true); } finally { btn.disabled = false; }
  };
  const vk = $("#pvk", d); if (vk) vk.onclick = async () => {
    vk.disabled = true;
    for (const { s, l, k, n } of lines) {
      if ((s.voice_files || [])[k]) continue;
      const who = S.chars.find(c => c.id === (pr.castMap || {})[l.who]);
      try {
        $("#pvs", d).textContent = `Speaking: ${niceName(l.who)} (shot ${n})`;
        const r = await post("/studio/api/voice", { text: l.text, voice: who && who.voice ? who.voice : "", name: `${pr.title || "Film"} - shot ${n} - ${niceName(l.who)}`, private: !!pr.private, target: runTarget() });
        (s.voice_files = s.voice_files || [])[k] = await pollJob(r.id); saveProject(pr);
      } catch (e) { toast(`Voice failed for shot ${n}: ${e.message.slice(0, 120)}`, true); }
    }
    $("#pvs", d).textContent = "Done"; vk.disabled = false; toast("Voice lines ready in Music & voices");
  };
  $("#pfo", d).onclick = () => go("film");
}

/* ---- 7 · the film edit: the storyboard's shots cut into the film (the same cut as 🎬 Make the film on the storyboard) */
async function stepFilm(b, pr) {
  await loadBoards();
  const board = S.boards.find(x => x.id === pr.board);
  if (!board || !(board.shots || []).length) { b.append(h(`<div class="empty">Nothing to cut yet. Draft the shots' takes first (<a href="#/projects/${pr.id}/takes">Takes</a>).</div>`)); return; }
  syncBoard(pr, board); await saveBoard(board, true); await saveProject(pr, true);       // the breakdown as it is now, like the storyboard
  const shots = board.shots, ready = shots.filter(clipOf).length;
  const d = h(`<div><div class="panel scnsum"><div><b>${ready} of ${plural(shots.length, "shot")} ready to cut</b>
      <p class="hint">The film is the storyboard's shots in order: each one's finished version, or its picked take, trimmed to the shot's length, with the titles, crossfades and music bed you set when you make it. Trims and captions are set on each shot on the <a href="#/projects/${pr.id}/takes">storyboard</a> (✂ under its prompt).</p></div>
      <button class="btn primary" id="fmake">🎬 Make the film</button></div>
    <div id="filmout"></div>
    <div class="panel"><label class="lbl">The cut</label><div class="cutlist"></div></div></div>`);
  b.append(d);
  const list = $(".cutlist", d);
  shots.forEach((s, n) => {
    const fin = s.final && s.finish && s.finish.from === s.pick, c = clipOf(s);
    const trim = s.trimIn || (s.trimSet && s.trimOut) ? ` · ✂ ${s.trimIn || 0}–${s.trimOut || "end"} s` : "";   // a trim set by hand (the shot's own length is automatic)
    list.append(h(`<div class="cutrow ${c ? "" : "miss"}"><b>${esc(s.code || String(n + 1))}</b><span>${esc(s.title || (s.prompt || "").split(/[.!?]/)[0])}</span><em>${fin ? "Finished ✓" : c ? "Picked take" : "Not rendered"}${esc(trim)}</em><small>${esc(String(s.seconds || ""))}${s.seconds ? " s" : ""}</small></div>`));
  });
  const show = () => {
    const fo = $("#filmout", d); fo.innerHTML = "";
    if (board.film) fo.append(h(`<div class="filmbox panel"><div class="section-head" style="margin-top:0"><h2>The film</h2><a href="${fileUrl(board.film, board.private)}" download>Download</a></div><video src="${fileUrl(board.film, board.private)}" controls playsinline></video><p class="hint">Saved in Videos › Films as “${esc(board.film.filename)}”.</p></div>`));
    navRefresh();
  };
  show();
  $("#fmake", d).onclick = () => filmDialog(board, clipOf, show);
}

/* ------------------------------------------------------------ SCREENPLAY: import (PDF or Fountain -> a new project) and the editor */
const SP_ACCEPT = ".pdf,.fdx,.fountain,.spmd,.txt";
const SP_FILE = /\.(pdf|fdx|fountain|spmd|txt)$/i;
let spDirty = false;
window.addEventListener("beforeunload", e => { if (spDirty) { e.preventDefault(); e.returnValue = ""; } });
async function sendScreenplay(path, file) {
  const fd = new FormData(); fd.append("private", S.incog ? "1" : "0"); fd.append("file", file, file.name);
  const r = await fetch(path, { method: "POST", body: fd });
  const txt = await r.text();
  if (!r.ok) throw new Error(txt || "HTTP " + r.status);
  return JSON.parse(txt);
}
const spCounts = s => [plural(s.scenes, "scene"), s.shots != null ? plural(s.shots, "shot") : "", plural(s.characters.length, "character"), s.pages ? `about ${plural(s.pages, "page")}` : ""].filter(Boolean).join(", ");
async function importScreenplay(file, btn) {
  if (!file) return;
  if (!SP_FILE.test(file.name)) return toast("Import a screenplay as a .pdf, .fdx (Final Draft) or .fountain file", true);
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "Reading the screenplay…"; }
  try {
    const j = await sendScreenplay("/studio/api/projects/import_screenplay", file);
    toast(`Imported “${j.project.title}”: ${spCounts(j.summary)}`);
    (j.warnings || []).forEach(w => toast(w, true));
    setProject(j.project); location.hash = `#/projects/${j.project.id}/screenplay`;
  } catch (e) { toast(`Couldn't import ${file.name}: ${e.message.slice(0, 300)}`, true); }
  finally { if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = label; } }
}

/* ---- 1 · the screenplay: the editor is screenplay/editor.js (its rules: screenplay/rules.js); the title page is here */
const SP_TP = [["Title", "Title", "The title of the screenplay"], ["Credit", "Credit", "Written by"], ["Author", "Author(s)", "Your name"], ["Source", "Based on", "Based on the novel by…"],
  ["Draft date", "Draft", "First draft, 1 May 2026"], ["Contact", "Contact", "Agent, address, phone, email"], ["Copyright", "Copyright", "© 2026"]];
// the title page of a screenplay, in its Fountain form ([[key, value]]), edited in a dialog: it's a page of its own, not the script
function titlePageDialog(tp, { heading = "Title page", action = "Save", onSave }) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const val = k => ((tp || []).find(([x]) => x.toLowerCase() === k.toLowerCase()) || [])[1] || "";
  const d = h(`<div class="dialog tpd"><div class="row" style="justify-content:space-between"><h2>${esc(heading)}</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">Printed on a page of its own before the script: the title a third of the way down, the credit and the authors under it, the draft and contact at the foot.</p>
    ${SP_TP.map(([k, l, ph]) => `<label class="lbl">${l}</label>${k === "Contact" ? `<textarea data-k="${k}" rows="3" placeholder="${esc(ph)}"></textarea>` : `<input type="text" data-k="${k}" placeholder="${esc(ph)}">`}`).join("")}
    <div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" data-go>${esc(action)}</button></div></div>`);
  m.append(d);
  $$("[data-k]", d).forEach(i => { i.value = val(i.dataset.k) || (i.dataset.k === "Credit" && !tp?.length ? "Written by" : ""); });
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  $("[data-k=Title]", d).focus();
  $("[data-go]", d).onclick = async () => {
    const title = $("[data-k=Title]", d).value.trim();
    if (!title) return toast("Give the screenplay a title", true);
    const out = SP_TP.map(([k]) => [k, $(`[data-k="${k}"]`, d).value.trim()]).filter(([, v]) => v);
    const others = (tp || []).filter(([k]) => !SP_TP.some(([x]) => x.toLowerCase() === k.toLowerCase()));
    close(); await onSave([...out, ...others], title);
  };
}
// the title page at the top of a screenplay's text ([[key, value]]; Fountain: Key: value, longer values indented under)
const TITLE_KEYS = new Set(["title", "credit", "author", "authors", "source", "draft date", "date", "contact", "copyright", "notes", "revision", "format", "font", "watermark", "tl", "tc", "tr", "cc", "bl", "br"]);
const startsWithTitlePage = text => { const m = (text || "").match(/^([A-Za-z][A-Za-z ]*):/); return !!m && TITLE_KEYS.has(m[1].trim().toLowerCase()); };   // not FADE IN:
function titlePageOf(text) {
  const lines = (text || "").split("\n"), out = [];
  if (!startsWithTitlePage(text)) return out;
  for (const ln of lines) {
    if (!ln.trim()) break;
    const m = ln.match(/^([A-Za-z][A-Za-z ]*):\s*(.*)$/);
    if (m && !/^\s/.test(ln)) out.push([m[1].trim(), m[2].trim()]);
    else if (out.length) out[out.length - 1][1] = (out[out.length - 1][1] + "\n" + ln.trim()).replace(/^\n/, "");
  }
  return out;
}
// a screenplay's text with a new title page
function withTitlePage(text, tp) {
  const lines = (text || "").split("\n");
  let i = 0;
  if (startsWithTitlePage(text)) { while (i < lines.length && lines[i].trim()) i++; }
  while (i < lines.length && !lines[i].trim()) i++;
  const head = tp.flatMap(([k, v]) => v.includes("\n") ? [`${k}:`, ...v.split("\n").map(x => "    " + x)] : [`${k}: ${v}`]);
  return [...head, "", ...lines.slice(i)].join("\n");
}
async function stepScreenplay(b, pr, go) {
  const E = await import("/studio/static/screenplay/editor.js");
  await E.mount(b, pr, { $, $$, h, esc, post, toast, plural, pollJob, saveProject, titlePageDialog, sendScreenplay, spCounts, accept: SP_ACCEPT, fileRe: SP_FILE,
    S, go, route, setDirty: v => { spDirty = v; }, dirty: () => spDirty, names: () => S.chars.filter(c => !(pr.cast || []).includes(c.id)).map(c => c.name), codes: () => shotCodes(pr),
    sync: () => followScreenplay(pr), changes: () => boardChangeMap(pr), openChanges: () => changesDialog(pr),
    places: () => (pr.locations || []).map(l => l.name) });
}

/* ------------------------------------------------------------ MUSIC + VOICES */
async function pageSound(main) {
  const st = store.get("sound", { tab: "music", tags: "", lyrics: "", seconds: 60, bpm: 100, key: "C major", timesig: "4", language: "en", instrumental: true, text: "", exag: 0.5, cfgw: 0.5, voice: "", voiceName: "" });
  const opts = await api("/studio/api/sound/options").catch(() => ({ keys: ["C major"], languages: ["en"] }));
  const p = h(`<div class="page"><div class="eyebrow">Post-production · Sound &amp; cut</div><h1>Music &amp; voices</h1>
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
      try { const r = await api("/studio/api/job/" + j.id); j.stage = r.stage; j.status = r.status === "lost" ? "failed" : r.status; if (r.error) j.stage = r.error.slice(0, 220); } catch { }
      drawJobs();
    }
    if (j.status === "done") { toast(j.kind === "music" ? "Song ready" : "Voice ready"); loadList(); }
  };
  const go = async (kind, body, label) => {
    try { const r = await post("/studio/api/" + kind, { ...body, private: !!S.incog, target: runTarget() }); const j = { id: r.id, kind, label, stage: "Queued", status: "running" }; jobs.unshift(j); drawJobs(); poll(j); }
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
    try { list = await api("/studio/api/sounds" + (S.incog ? "?private=1" : "")); } catch { }
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
  const p = h(`<div class="page"><div class="eyebrow">Post-production · Sound &amp; cut</div><h1>Cut to the beat</h1>
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
      const r = await post("/studio/api/reel", { items: R.picks.map(i => ({ filename: i.filename, subfolder: i.subfolder, kind: i.kind })), song: R.song, aspect: R.aspect, length: R.length, cut: R.cut, name: $("#rn", p).value, private: S.incog });
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
    <a href="#/home" class="mitem">⌂ Home</a><a href="#/projects" class="mitem">◧ Projects${curProject() ? ` <small class="hint">· ${esc(projTitle(curProject()))}</small>` : ""}</a><a href="#/loras" class="mitem">◈ Models &amp; LoRAs</a><a href="#/settings" class="mitem">⚙ Settings</a></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $("[data-x]", d).onclick = close; $$("a", d).forEach(a => a.addEventListener("click", close));
}

/* ------------------------------------------------------------ PREVIZ: .glb sets explored and directed in the browser */

async function pagePreviz(main, arg) {
  if (arg && arg.startsWith("glb:")) return glbPage(main, arg.slice(4));
  const p = h(`<div class="page"><div class="eyebrow">Pre-production · Design</div><h1>Previz</h1>
    <p class="muted" style="max-width:720px">Add a 3D set (.glb) and direct it right here: look through its cameras or make your own, frame the shot (wide to extreme close-up), set the camera height, build camera moves that play with the set's animation, light it, and block the scene with stand-ins and props.</p>
    <div class="section-head"><h2>Sets</h2><div class="row"><button class="ghost" id="qscene" title="A blank stage with a stand-in: nothing moves, direct it from scratch">＋ Quick scene</button><button class="ghost" id="aglb">＋ Add a .glb</button></div></div>
    <div class="lgrid" id="gg"></div></div>`);
  main.append(p);
  $("#aglb", p).onclick = () => addGlbDialog();
  $("#qscene", p).onclick = () => quickSceneDialog();
  p.addEventListener("dragover", e => { if ([...(e.dataTransfer?.items || [])].some(i => i.kind === "file")) e.preventDefault(); });
  p.addEventListener("drop", e => { const f = [...(e.dataTransfer?.files || [])].find(f => /\.glb$/i.test(f.name)); if (f) { e.preventDefault(); addGlbDialog(f); } });
  drawGlbs($("#gg", p));
}

/* ---- sets: .glb files explored and directed in the browser (glbsets.py, previz/viewer.js) */
const glbMeta = g => {
  const i = g.info || {}, n = (i.cameras || []).length, d = i.duration || 0;
  return [`${n} camera${n === 1 ? "" : "s"}`, d ? `${Math.floor(d / 60)}:${String(Math.round(d % 60)).padStart(2, "0")} of animation` : "", i.size ? (i.size < 1e6 ? `${Math.max(1, Math.round(i.size / 1e3))} KB` : `${Math.round(i.size / 1e6)} MB`) : ""].filter(Boolean).join(" · ");
};
async function drawGlbs(box) {
  let list = [];
  try { list = await api("/studio/api/glb" + PQ()); } catch (e) { box.append(h(`<div class="empty">${esc(e.message.slice(0, 200))}</div>`)); return; }
  box.innerHTML = "";
  if (!list.length) box.append(h(`<div class="empty" style="grid-column:1/-1">No previz sets yet. Add a .glb (drop it on this page), or start from a quick scene: a stage and a stand-in, nothing moving. Export a .glb from Blender or any 3D app: glTF 2.0 (.glb) with the cameras included.</div>`));
  list.forEach(g => {
    const c = h(`<div class="lcard">${g.thumb ? `<img class="gthumb" src="/studio/api/glb/${g.id}/thumb.jpg?t=${Math.round(g.thumb)}" alt="">` : `<div class="gph">◰</div>`}<div class="lb2"><b>${esc(g.name)}</b><span>${esc(glbMeta(g))}</span>
      ${g.missing_file ? `<span class="warn">The file isn't there any more: ${esc(g.path)}</span>` : g.error ? `<span class="warn">${esc(g.error)}</span>` : ""}
      <div class="row" style="gap:6px;margin-top:6px"><button class="ghost" data-open>Explore</button><button class="icon" data-ref title="Read the file again (after exporting it again)">↻</button><button class="icon" data-rm title="Remove from Studio (the file stays)">✕</button></div></div></div>`);
    c.onclick = e => { if (!e.target.closest("[data-ref],[data-rm]")) location.hash = "#/previz/glb:" + g.id; };
    $("[data-ref]", c).onclick = async () => { try { const r = await post(`/studio/api/glb/${g.id}/refresh`, {}); toast(`Read again: ${glbMeta(r)}`); route(); } catch (e) { toast(e.message.slice(0, 300), true); } };
    $("[data-rm]", c).onclick = async () => { if (!confirm(`Remove “${g.name}” from Studio? The .glb file stays where it is.`)) return; await api("/studio/api/glb/" + g.id, { method: "DELETE" }); route(); };
    box.append(c);
  });
}
function uploadGlb(file, name, onProgress, kind) {   // streamed as the request body, so a set of any size goes through
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest(), q = new URLSearchParams({ filename: file.name, name: name || "", kind: kind || "set" });
    if (S.incog) q.set("private", "1");
    x.open("POST", "/studio/api/glb/upload?" + q);
    x.setRequestHeader("Content-Type", "model/gltf-binary");
    x.upload.onprogress = e => e.lengthComputable && onProgress && onProgress(e.loaded / e.total);
    x.onload = () => { if (x.status >= 200 && x.status < 300) resolve(JSON.parse(x.responseText)); else reject(new Error(x.responseText || "Upload failed (" + x.status + ")")); };
    x.onerror = () => reject(new Error("Upload failed: the connection dropped"));
    x.send(file);
  });
}
function quickSceneDialog() {    // a blank slate: a stage, a subject, one camera, nothing moving (glbsets.py makes it)
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const st = { stage: "cyc", people: 1 };
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>Quick scene</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">A blank slate to direct from scratch: a stage, a stand-in for your subject and one camera. Nothing moves until you add camera moves; light it in the Light tab, add props in Objects.</p>
    <div><label class="lbl">Stage</label><div class="chips" id="qs-stage"><div class="chip" data-v="cyc" title="A seamless white cyclorama: floor curving up into the back wall">Studio cyc</div><div class="chip" data-v="room" title="An 8 × 6 m room with 3 m walls and a doorway">Room</div><div class="chip" data-v="open" title="40 × 40 m of open ground">Open ground</div></div></div>
    <div><label class="lbl">People</label><div class="chips" id="qs-people"><div class="chip" data-v="1">A subject</div><div class="chip" data-v="2" title="Two people facing each other: ready for a dialogue (two-shot, over the shoulder)">Two people</div><div class="chip" data-v="0">Nobody</div></div></div>
    <div><label class="lbl">Name</label><input type="text" id="qs-name" placeholder="Quick scene"></div>
    <div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" id="qs-go">Create</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  const chips = () => { $$("#qs-stage .chip", d).forEach(c => c.classList.toggle("on", c.dataset.v === st.stage)); $$("#qs-people .chip", d).forEach(c => c.classList.toggle("on", +c.dataset.v === st.people)); };
  $$("#qs-stage .chip", d).forEach(c => c.onclick = () => { st.stage = c.dataset.v; chips(); });
  $$("#qs-people .chip", d).forEach(c => c.onclick = () => { st.people = +c.dataset.v; chips(); });
  chips();
  $("#qs-go", d).onclick = async () => {
    $("#qs-go", d).disabled = true;
    try { const g = await post("/studio/api/glb/quick", { ...st, name: $("#qs-name", d).value.trim(), private: S.incog }); close(); location.hash = "#/previz/glb:" + g.id; }
    catch (e) { toast(e.message.slice(0, 300), true); $("#qs-go", d).disabled = false; }
  };
}
function addGlbDialog(file0) {
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  let file = null;
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>Add a .glb set</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">A 3D set saved as glTF Binary (.glb), from Blender or any 3D app. Export it with the cameras included (Blender: File › Export › glTF 2.0, <b>Include › Cameras</b>) so they come along; animated cameras keep their moves.</p>
    <div class="gdrop"><b>Drop a .glb here</b><br><span class="hint">or click to pick one from this device</span><input type="file" hidden accept=".glb,model/gltf-binary"></div>
    <div class="gprog hidden"><div></div></div>
    <div><label class="lbl">Or a .glb already on the PC (used where it is)</label><input type="text" id="gp" placeholder="C:\\Users\\you\\Documents\\Studio Sets\\My set.glb"></div>
    <div><label class="lbl">Name</label><input type="text" id="gn" placeholder="(the file's name)"></div>
    <div class="foot"><span class="hint" id="gst" style="margin-right:auto"></span><button class="ghost" data-x>Cancel</button><button class="btn primary" id="ggo">Add</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  const drop = $(".gdrop", d), fi = $("input[type=file]", drop), stt = $("#gst", d), prog = $(".gprog", d);
  const take = f => {
    if (!f) return;
    if (!/\.glb$/i.test(f.name)) return toast("Pick a .glb file (glTF Binary)", true);
    file = f; drop.classList.add("on"); drop.innerHTML = `<b>${esc(f.name)}</b><br><span class="hint">${(f.size / 1e6).toFixed(1)} MB · click to pick another</span>`; drop.append(fi);
    if (!$("#gn", d).value) $("#gn", d).placeholder = f.name.replace(/\.glb$/i, "");
  };
  drop.onclick = e => { if (e.target !== fi) fi.click(); };
  fi.onchange = () => take(fi.files[0]);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("drag"); };
  drop.ondragleave = () => drop.classList.remove("drag");
  drop.ondrop = e => { e.preventDefault(); e.stopPropagation(); drop.classList.remove("drag"); take(e.dataTransfer.files[0]); };
  take(file0);
  $("#ggo", d).onclick = async () => {
    const path = $("#gp", d).value.trim(), name = $("#gn", d).value.trim();
    if (!file && !path) return toast("Pick a .glb, or give its path on the PC", true);
    const go = $("#ggo", d); go.disabled = true;
    try {
      let g;
      if (file) {
        prog.classList.remove("hidden");
        g = await uploadGlb(file, name, f => { $("div", prog).style.width = (f * 100).toFixed(1) + "%"; stt.textContent = `Uploading ${Math.round(f * 100)}%`; });
      } else { stt.textContent = "Reading the file…"; g = await post("/studio/api/glb/add", { path, name, private: S.incog }); }
      close(); toast(`Added ${g.name}: ${glbMeta(g)}`); location.hash = "#/previz/glb:" + g.id;
    } catch (e) { toast(e.message.slice(0, 300), true); go.disabled = false; stt.textContent = ""; prog.classList.add("hidden"); }
  };
}
async function glbPage(main, id) {
  let g;
  try { g = await api("/studio/api/glb/" + encodeURIComponent(id)); }
  catch (e) { main.append(h(`<div class="page"><div class="eyebrow"><a href="#/previz">Previz</a></div><div class="empty">${esc(/Incognito/.test(e.message) ? "This set is in Incognito: unlock it first." : "That set isn't in Studio.")}</div></div>`)); return; }
  const i = g.info || {};
  const p = h(`<div class="page gvpage"><div class="gvtop"><div><div class="eyebrow"><a href="#/previz">Previz</a> · Set</div><h1 id="gnm" contenteditable="true" spellcheck="false">${esc(g.name)}</h1></div>
    <div class="hint" style="text-align:right">${esc(g.file_name)} · ${esc(glbMeta(g))}${i.generator ? `<br>${esc(i.generator)}` : ""}</div></div><div id="gvw"></div></div>`);
  main.append(p);
  let nt = 0;
  $("#gnm", p).oninput = e => { clearTimeout(nt); nt = setTimeout(() => post("/studio/api/glb/" + g.id, { name: e.target.textContent.trim() }).catch(() => { }), 600); };
  $("#gnm", p).onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } };
  const box = $("#gvw", p);
  if (g.missing_file) { box.append(h(`<div class="empty">The .glb file isn't there any more:<br><b>${esc(g.path)}</b><br>Put it back (or add it again) and reopen this set.</div>`)); return; }
  if (g.error) { box.append(h(`<div class="empty">${esc(g.error)}</div>`)); return; }
  let mod;
  try { mod = await import("/studio/static/previz/viewer.js"); }
  catch (e) { box.append(h(`<div class="empty">The 3D viewer couldn't start: ${esc(String(e.message || e).slice(0, 200))}</div>`)); return; }
  if (!box.isConnected) return;
  if (!mod.webglAvailable()) { box.append(h(`<div class="empty">This browser can't show 3D (WebGL is off). Try Chrome, Edge, Firefox or Safari with hardware acceleration on.</div>`)); return; }
  const sendThumb = (blob, quiet) => fetch(`/studio/api/glb/${g.id}/thumb`, { method: "POST", body: blob, headers: { "Content-Type": "image/jpeg" } })
    .then(r => { if (!r.ok) throw new Error(); if (!quiet) toast("Card picture saved"); }).catch(() => { if (!quiet) toast("Couldn't save the card picture", true); });
  let thumbs = 0;
  const propOf = x => ({ id: x.id, name: x.name, url: `/studio/api/glb/${x.id}/file?v=${Math.round((x.info || {}).mtime || 0)}` });
  const props = await api("/studio/api/glb?kind=prop" + (g.private ? "&private=1" : "")).catch(() => []);
  if (!box.isConnected) return;
  const below = h(`<div class="gvbelow"></div>`); p.append(below);
  const refresh = async () => { try { Object.assign(g, await api("/studio/api/glb/" + g.id)); } catch { } drawTakes(g, below, viewer, refresh); };
  let viewer = null;
  viewer = mod.mountViewer(box, {
    onSnapshot: () => snapshotDialog(g, viewer, refresh),
    onMovie: () => movieDialog(g, viewer, refresh),
    url: `/studio/api/glb/${g.id}/file?v=${Math.round(i.mtime || 0)}`, info: { ...i }, initial: g.view || {}, needThumb: !g.thumb,
    direction: g.direction || null, props: props.map(propOf), toast,
    onState: v => post("/studio/api/glb/" + g.id, { view: v }).catch(() => { }),
    onDirection: (d, leaving) => leaving   // the page is going away: keepalive lets the save finish
      ? fetch("/studio/api/glb/" + g.id, { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ direction: d }) }).catch(() => { })
      : post("/studio/api/glb/" + g.id, { direction: d }).catch(e => toast("Couldn't save: " + e.message.slice(0, 200), true)),
    onThumb: blob => sendThumb(blob, !g.thumb && thumbs++ === 0),
    uploadProp: async f => { const q = new URLSearchParams({ filename: f.name, kind: "prop" }); if (g.private) q.set("private", "1");
      const r = await fetch("/studio/api/glb/upload?" + q, { method: "POST", body: f, headers: { "Content-Type": "model/gltf-binary" } });
      if (!r.ok) throw new Error(await r.text()); return propOf(await r.json()); },
  });
  drawTakes(g, below, viewer, refresh);
}

/* ---- what a set's cameras made: location shots (snapshots kept with the set) and movies (in the Gallery) */
function drawTakes(g, box, viewer, refresh) {
  const stills = (g.stills || []).slice().reverse(), movies = (g.movies || []).slice().reverse();
  box.innerHTML = "";
  box.append(h(`<div class="section-head"><h2>Location shots</h2><span class="hint">📷 Snapshot › Location shot keeps a view with this set</span></div>`));
  const sg = h(`<div class="gvstills"></div>`); box.append(sg);
  if (!stills.length) sg.append(h(`<div class="empty" style="grid-column:1/-1">No location shots yet. Frame a view, then 📷 Snapshot › Location shot.</div>`));
  stills.forEach(x => {
    const c = h(`<div class="gvstill"><img src="${inputUrl(x.input)}" loading="lazy" title="Open the picture"><div class="lb2"><b>${esc(x.name)}</b><span>${esc([x.camera !== x.name ? x.camera : "", x.t ? "at " + x.t.toFixed(1) + " s" : ""].filter(Boolean).join(" · ") || "Location shot")}</span>
      <div class="row"><button class="ghost sm" data-sb title="Make it the first frame of a storyboard shot">→ Storyboard shot</button><button class="icon" data-rm title="Take it off this list (the picture stays in the Gallery)">✕</button></div></div></div>`);
    $("img", c).onclick = () => window.open(fileUrl(x.output, g.private), "_blank");
    $("[data-sb]", c).onclick = () => snapshotDialog(g, viewer, refresh, x);
    $("[data-rm]", c).onclick = async () => { await api(`/studio/api/glb/${g.id}/stills/${x.id}`, { method: "DELETE" }); refresh(); };
    sg.append(c);
  });
  box.append(h(`<div class="section-head"><h2>Movies</h2><span class="hint">🎬 Movie renders a camera and its move: an MP4 in the Gallery, for reference</span></div>`));
  const mg = h(`<div class="gvstills"></div>`); box.append(mg);
  if (!movies.length) mg.append(h(`<div class="empty" style="grid-column:1/-1">No movies yet. Pick a camera (give it a move), then 🎬 Movie.</div>`));
  movies.forEach(x => {
    const c = h(`<div class="gvstill"><video src="${fileUrl(x.output, g.private)}" controls muted playsinline preload="metadata"></video><div class="lb2"><b>${esc(x.name)}</b><span>${esc([x.camera !== x.name ? x.camera : "", (+x.seconds || 0).toFixed(1) + " s", "Gallery › " + x.output.subfolder].filter(Boolean).join(" · "))}</span>
      <div class="row"><button class="icon" data-rm title="Take it off this list (the movie stays in the Gallery)">✕</button></div></div></div>`);
    $("[data-rm]", c).onclick = async () => { await api(`/studio/api/glb/${g.id}/stills/${x.id}`, { method: "DELETE" }); refresh(); };
    mg.append(c);
  });
}

/* A Studio move in the words a storyboard shot's camera uses (the Storyboard's CAMERA list), when there are such words. */
const MOVE_CAMERA = { push: "slow dolly push-in toward the subject", pull: "slow dolly pull-back revealing the scene", pan_l: "smooth pan left", pan_r: "smooth pan right",
  tilt_u: "slow tilt up", arc_l: "camera orbits around the subject", arc_r: "camera orbits around the subject", crane_up: "crane shot rising up",
  crane_down: "crane shot descending", vertigo_in: "dolly zoom (vertigo effect)", vertigo_out: "dolly zoom (vertigo effect)" };
const cameraWords = m => m.follow ? "tracking shot following the subject" : MOVE_CAMERA[m.move] || (m.handheld ? "handheld camera with natural shake" : "");
const shotPrompt = m => `${m.size ? m.size[0].toUpperCase() + m.size.slice(1) + " of " + (m.subject || "the subject") : "A shot"}${m.height ? ", " + m.height.toLowerCase() : ""}, ${m.lens} mm lens.`;

/* A snapshot of the view (or a location shot, `pre`): the first frame of a storyboard shot, or a location shot of the set. */
async function snapshotDialog(g, viewer, refresh, pre) {
  if (!viewer) return;
  let shot = null;
  if (!pre) { try { shot = await viewer.snapshot(1920); } catch (e) { return toast("Couldn't take the snapshot: " + e.message, true); } }
  const meta = pre ? { camera: pre.camera, t: pre.t, lens: 35 } : shot.meta, words = pre ? "" : cameraWords(meta);
  const src = pre ? inputUrl(pre.input) : URL.createObjectURL(shot.blob);
  const boards = await loadBoards();
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  let choice = pre ? "board" : "";
  const d = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>${pre ? "Location shot → storyboard" : "Snapshot"}</h2><button class="icon" data-x>✕</button></div>
    <img class="snapimg" src="${src}">
    <div class="hint">${esc(meta.camera || "")}${meta.describe ? " · " + esc(meta.describe) : ""}${meta.aspect ? " · " + esc(meta.aspect) : ""}${shot ? ` · ${shot.size[0]} × ${shot.size[1]}` : ""}</div>
    ${pre ? "" : `<div><label class="lbl">Name</label><input type="text" id="sn-name" value="${esc(meta.camera)}"></div>
    <div class="snapchoice"><div class="chip" data-c="board"><b>Storyboard shot</b>The first frame of a shot on a storyboard (or a new shot)</div>
      <div class="chip" data-c="location"><b>Location shot</b>Kept with this set, under the viewer, for reference</div></div>`}
    <div id="sn-board" class="${pre ? "" : "hidden"}">
      <div class="two"><div><label class="lbl">Storyboard</label><select id="sn-b">${boards.map(b => `<option value="${esc(b.id)}" ${b.id === S.boardId ? "selected" : ""}>${esc(b.name || "Storyboard")}</option>`).join("")}<option value="_new">＋ New storyboard</option></select></div>
        <div><label class="lbl">Shot</label><select id="sn-s"></select></div></div>
      <div id="sn-newb" class="hidden" style="margin-top:8px"><label class="lbl">New storyboard's name</label><input type="text" id="sn-bname" value="${esc(g.name)}"></div>
      <div id="sn-new" style="margin-top:8px"><label class="lbl">What happens in the new shot</label><textarea id="sn-prompt" rows="2">${esc(shotPrompt(meta))}</textarea></div>
      ${words ? `<label class="hint" style="display:block;margin-top:8px"><input type="checkbox" id="sn-cam" checked> Camera: ${esc(words)} (this camera's move)</label>` : ""}
    </div>
    <div class="foot"><span class="hint" id="sn-st" style="margin-right:auto"></span><button class="ghost" data-x>Cancel</button><button class="btn primary" id="sn-go" ${pre ? "" : "disabled"}>Save</button></div></div>`);
  m.append(d);
  const close = () => { m.classList.add("hidden"); m.innerHTML = ""; if (!pre) URL.revokeObjectURL(src); };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  const bsel = $("#sn-b", d), ssel = $("#sn-s", d);
  const drawShots = () => {
    const b = boards.find(x => x.id === bsel.value);
    ssel.innerHTML = (b ? b.shots.map((s, k) => `<option value="${esc(s.id)}">Shot ${esc(s.code || k + 1)}${s.image ? " ✓" : ""} · ${esc((s.title || s.prompt || "").slice(0, 40))}</option>`).join("") : "") + `<option value="_new">＋ New shot at the end</option>`;
    if (!b || !b.shots.length) ssel.value = "_new";
    $("#sn-newb", d).classList.toggle("hidden", bsel.value !== "_new");
    $("#sn-new", d).classList.toggle("hidden", ssel.value !== "_new");
  };
  bsel.onchange = drawShots; ssel.onchange = drawShots; drawShots();
  $$(".snapchoice .chip", d).forEach(c => c.onclick = () => {
    choice = c.dataset.c; $$(".snapchoice .chip", d).forEach(x => x.classList.toggle("on", x === c));
    $("#sn-board", d).classList.toggle("hidden", choice !== "board"); $("#sn-go", d).disabled = false;
  });
  $("#sn-go", d).onclick = async () => {
    const go = $("#sn-go", d), stt = $("#sn-st", d); go.disabled = true; stt.textContent = "Saving…";
    try {
      let value = pre && pre.input;
      if (!value) {
        const q = new URLSearchParams({ kind: choice === "location" ? "location" : "storyboard", name: $("#sn-name", d).value.trim() || meta.camera, camera: meta.camera, t: String(meta.t || 0) });
        const r = await fetch(`/studio/api/glb/${g.id}/still?` + q, { method: "POST", body: shot.blob, headers: { "Content-Type": "image/png" } });
        if (!r.ok) throw new Error(await r.text());
        value = (await r.json()).still.input;
      }
      if (choice === "board") {
        await loadBoards();
        let b = S.boards.find(x => x.id === bsel.value);
        if (!b) { b = { name: $("#sn-bname", d).value.trim() || g.name, shots: [], private: !!g.private }; S.boards.push(b); }
        let s = b.shots.find(x => x.id === ssel.value), k;
        if (!s) { s = newShot($("#sn-prompt", d).value.trim(), value); b.shots.push(s); }
        else { s.image = value; s.cont = null; }
        k = b.shots.indexOf(s);
        const cam = $("#sn-cam", d); if (cam && cam.checked) s.camera = words;
        await saveBoard(b, true); S.boardId = b.id; store.set("board", b.id);
        toast(`The first frame of shot ${s.code || k + 1} in “${b.name}”`);
      } else toast("Added to the location shots, under the viewer");
      close(); refresh();
    } catch (e) { stt.textContent = ""; go.disabled = false; toast(e.message.slice(0, 300), true); }
  };
}

/* A movie of the view: every frame rendered at its own moment (so it plays at the right speed on any computer), then an
   MP4 in the Gallery, for reference. */
function movieDialog(g, viewer, refresh) {
  if (!viewer) return;
  const [from, to] = viewer.movieSpan(), meta = viewer.meta();
  const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
  const st = { long: 1280, fps: 24 };
  const d = h(`<div class="dialog"><div class="row" style="justify-content:space-between"><h2>Movie of this view</h2><button class="icon" data-x>✕</button></div>
    <p class="hint" style="margin:0">${esc(meta.camera)}${meta.describe ? " · " + esc(meta.describe) : ""}. Every frame is rendered at its own moment, with the set's animation, and saved as an MP4 in the Gallery (Videos › Previz) to use as a reference.</p>
    <div class="two"><div><label class="lbl">From (s)</label><input type="number" id="mv-from" step="0.1" min="0" value="${from.toFixed(2)}"></div><div><label class="lbl">To (s)</label><input type="number" id="mv-to" step="0.1" min="0" value="${to.toFixed(2)}"></div></div>
    <div class="two"><div><label class="lbl">Size</label><div class="chips" id="mv-size"><div class="chip" data-v="1280">720p</div><div class="chip" data-v="1920">1080p</div></div></div>
      <div><label class="lbl">Frames a second</label><div class="chips" id="mv-fps"><div class="chip" data-v="24">24</div><div class="chip" data-v="25">25</div><div class="chip" data-v="30">30</div></div></div></div>
    <div><label class="lbl">Name</label><input type="text" id="mv-name" value="${esc(meta.camera)}"></div>
    <div class="recbar hidden"><div></div></div>
    <div class="foot"><span class="hint" id="mv-st" style="margin-right:auto"></span><button class="ghost" data-x>Cancel</button><button class="btn primary" id="mv-go">🎬 Record</button></div></div>`);
  m.append(d);
  let busy = false, stop = false;
  const close = () => { if (busy) { stop = true; return; } m.classList.add("hidden"); m.innerHTML = ""; };
  $$("[data-x]", d).forEach(x => x.onclick = close);
  const chips = () => { $$("#mv-size .chip", d).forEach(c => c.classList.toggle("on", +c.dataset.v === st.long)); $$("#mv-fps .chip", d).forEach(c => c.classList.toggle("on", +c.dataset.v === st.fps)); frames(); };
  const frames = () => { const n = Math.max(1, Math.round((+$("#mv-to", d).value - +$("#mv-from", d).value) * st.fps) + 1), [w, hh] = viewer.outSize(st.long); $("#mv-st", d).textContent = `${n} frames · ${w} × ${hh}`; };
  $$("#mv-size .chip", d).forEach(c => c.onclick = () => { st.long = +c.dataset.v; chips(); });
  $$("#mv-fps .chip", d).forEach(c => c.onclick = () => { st.fps = +c.dataset.v; chips(); });
  $("#mv-from", d).oninput = frames; $("#mv-to", d).oninput = frames;
  chips();
  $("#mv-go", d).onclick = async () => {
    const a = +$("#mv-from", d).value, b = +$("#mv-to", d).value, stt = $("#mv-st", d), bar = $(".recbar", d);
    if (!(b > a)) return toast("“To” must come after “From”", true);
    busy = true; $("#mv-go", d).disabled = true; bar.classList.remove("hidden"); $("[data-x].ghost", d).textContent = "Stop";
    let id = null;
    try {
      id = (await post(`/studio/api/glb/${g.id}/movie`, {})).id;
      await viewer.record({ from: a, to: b, fps: st.fps, long: st.long }, async (blob, i, n) => {
        const r = await fetch(`/studio/api/glb/${g.id}/movie/${id}/frame?i=${i}`, { method: "POST", body: blob, headers: { "Content-Type": "image/jpeg" } });
        if (!r.ok) throw new Error(await r.text());
        $("div", bar).style.width = ((i + 1) / n * 95).toFixed(1) + "%"; stt.textContent = `Frame ${i + 1} of ${n}`;
      }, () => stop);
      stt.textContent = "Making the MP4…";
      const r = await post(`/studio/api/glb/${g.id}/movie/${id}/finish`, { fps: st.fps, name: $("#mv-name", d).value.trim() || meta.camera, camera: meta.camera });
      $("div", bar).style.width = "100%";
      busy = false; close(); S.gallery = [];
      toast(`Saved to the Gallery: ${r.movie.output.subfolder}/${r.movie.output.filename}`);
      refresh();
    } catch (e) {
      busy = false;
      if (id) api(`/studio/api/glb/${g.id}/movie/${id}`, { method: "DELETE" }).catch(() => { });
      toast(stop ? "Stopped: nothing was saved" : "The movie failed: " + e.message.slice(0, 300), !stop);
      close();
    }
  };
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
    <div class="section-head"><h2>Versions</h2><a class="ghost" id="gho" target="_blank" rel="noopener">GitHub ↗</a></div><div class="panel keybox" id="gitbox"><p class="muted">…</p></div>
    <details class="adv"><summary>Advanced</summary><div class="panel keybox"><p class="hint" style="margin-top:0">Private space</p><a class="ghost" href="/studio/password">Change the Incognito password…</a></div></details>
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
        try { await post("/studio/api/models/remove", { folder: m.folder, name: m.name }); all = all.filter(x => x !== m); draw(); loadTrash(); loraCache = null; ckptCache = null; toast("Removed. Restore it below if needed."); }
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
  api("/studio/api/models" + PQ()).then(r => {
    all = r.items;
    const tot = all.reduce((a, m) => a + m.size, 0);
    $("#disk", p).textContent = `${all.length} files · ${fmtSize(tot)}` + (r.disk ? ` · ${fmtSize(r.disk.free)} free of ${fmtSize(r.disk.total)}` : "");
    draw();
  }).catch(e => { $("#mlist", p).innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
  async function loadTrash() {
    const box = $("#trash", p); box.innerHTML = "";
    const items = await api("/studio/api/models/removed").catch(() => []);
    $("#purgeall", p).classList.toggle("hidden", !items.length);
    if (!items.length) box.append(h(`<div class="empty">Nothing removed.</div>`));
    items.forEach(m => {
      const r = h(`<div class="mrow"><div class="mn"><b>${esc(m.name.split("/").pop())}</b><span>${esc(m.folder)} › ${esc(m.name)}</span></div><span class="ms">${fmtSize(m.size)}</span><button class="ghost sm" data-r>Restore</button><button class="ghost danger sm" data-d>Delete for good</button></div>`);
      $("[data-r]", r).onclick = async () => { try { await post("/studio/api/models/restore", m); toast("Restored"); route(); } catch (e) { toast(e.message, true); } };
      $("[data-d]", r).onclick = async () => { if (!confirm(`Delete ${m.name.split("/").pop()} for good? This frees ${fmtSize(m.size)} and can't be undone.`)) return; try { await post("/studio/api/models/purge", m); toast("Deleted"); loadTrash(); } catch (e) { toast(e.message, true); } };
      box.append(r);
    });
    $("#purgeall", p).onclick = async () => { const tot = items.reduce((a, m) => a + m.size, 0); if (!confirm(`Delete all ${items.length} removed models for good? This frees ${fmtSize(tot)} and can't be undone.`)) return; try { const r = await post("/studio/api/models/purge", { all: true }); toast(`Deleted ${r.deleted} files, ${fmtSize(r.freed)} freed`); loadTrash(); } catch (e) { toast(e.message, true); } };
  }
  loadTrash();
  const ckst = async () => { const s = await api("/studio/api/civitai/status").catch(() => ({})); $("#ckst", p).textContent = s.has_key ? "A Civitai key is saved on this PC (it's never shown)." : "No Civitai key saved."; };
  ckst();
  $("#cks2", p).onclick = async () => { try { await post("/studio/api/civitai/key", { key: $("#ck2", p).value.trim() }); $("#ck2", p).value = ""; toast("Key saved"); ckst(); } catch (e) { toast(e.message, true); } };
  $("#ckr2", p).onclick = async () => { if (!confirm("Remove the saved Civitai key?")) return; await post("/studio/api/civitai/key", { key: "" }); ckst(); };
  const tzDraw = async force => {
    const box = $("#tzbox", p); box.innerHTML = `<p class="muted">Checking Topaz… (a few seconds)</p>`;
    const t = await topaz(force); if (!t) { box.innerHTML = `<p class="muted">Couldn't check Topaz (restart ComfyUI after updating Studio).</p>`; return; }
    const line = (x, name) => !x.found ? `<span class="bad">✕</span> ${name}: Not installed on this computer` : x.ready ? `<span class="ok">✓</span> ${name}: ready${x.version ? " · " + esc(x.version) : ""}${x.fps === false ? " (no frame-rate filter)" : ""}` : `<span class="bad">!</span> ${name}: installed but not working. ${esc(x.error || "")}`;
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
    const save = async () => { tzCache = null; await post("/studio/api/topaz/prefs", { use: $("#tz-use", box).checked, video_model: $("#tz-vm", box).value, fps_model: $("#tz-fm", box).value, image_model: $("#tz-im", box).value, image_format: $("#tz-if", box).value, device: $("#tz-dev", box).value }).then(() => toast("Saved")).catch(e => toast(e.message, true)); };
    $$("input,select", box).forEach(x => x.onchange = save);
    $$("[data-cp]", box).forEach(x => x.onclick = () => copyText(x.dataset.cp, "Folder copied"));
  };
  tzDraw(false); $("#tzt", p).onclick = () => tzDraw(true);
  const gitDraw = async () => {
    const box = $("#gitbox", p); let g;
    try { g = await api("/studio/api/git/status"); } catch { box.innerHTML = `<p class="muted">Restart ComfyUI to turn on versions.</p>`; return; }
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
        const r = await post("/studio/api/git/pull", { prefer }); const res = await pollJob(r.id, st => up.innerHTML = `<span class="muted">${esc(st)}…</span>`);
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
        const r = await post("/studio/api/git/check", {}); const c = await pollJob(r.id);
        if (!c.remote_branch) { up.innerHTML = `<span class="muted">GitHub is empty: the first save pushes everything.</span>`; return; }
        if (!c.behind) { up.innerHTML = `<span class="ok">✓ Up to date with GitHub</span>${c.ahead ? ` <span class="muted">· ${c.ahead} version${c.ahead > 1 ? "s" : ""} to push</span>` : ""}`; return; }
        up.innerHTML = `<p><b>GitHub has ${c.behind} change${c.behind > 1 ? "s" : ""} this PC doesn't have</b>${c.remote_log && c.remote_log[0] ? ` <span class="muted">(latest: “${esc(c.remote_log[0].message.slice(0, 70))}” by ${esc(c.remote_log[0].author)})</span>` : ""}. Get them before saving.</p>
          ${ghFiles(c.files || [])}<button class="btn primary" id="gpull">⬇ Get updates from GitHub</button>`;
        $("#gpull", up).onclick = () => doPull(null);
      } catch (e) { up.innerHTML = `<span class="warn">${esc(e.message.slice(0, 300))}</span>`; }
    })(); else up.innerHTML = "";
    $("#gs", box).onclick = async () => {
      const b = $("#gs", box); b.disabled = true; b.textContent = "Saving…";
      try { const r = await post("/studio/api/git/save", { message: $("#gm", box).value, push: true }); const res = await pollJob(r.id); toast(res.changed ? `Saved ${res.commit}${res.pushed ? " and pushed to GitHub" : ""}` : (res.pushed ? "Nothing new; GitHub is up to date" : "Nothing new to save")); }
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
      <div class="row"><input id="wkl" placeholder="Name (mac-vega-a)"><input id="wkh" placeholder="Host (studio-mac.local)"><input id="wkp" placeholder="Port" style="max-width:80px"><button class="btn" id="wka">Add</button></div>`;
    const save = async list => { try { await post("/studio/api/workers", { workers: list.map(({ label, host, port, device }) => ({ label, host, port, device })) }); } catch (e) { toast(e.message, true); } wkDraw(); };
    $$("[data-rm]", box).forEach(b => b.onclick = () => save(others.filter((_, k) => k !== +b.dataset.rm)));
    $("#wka", box).onclick = () => {
      const label = $("#wkl", box).value.trim(), host = $("#wkh", box).value.trim(), port = +$("#wkp", box).value;
      if (!label || !host || !port || label === "local" || label === "auto" || others.some(w => w.label === label)) return toast("Give it a new name, a host and a port", true);
      save([...others, { label, host, port }]);
    };
  };
  wkDraw(); $("#wkr", p).onclick = wkDraw;
  api("/studio/api/guard").then(g => {
    const box = $("#gdbox", p), pr = g.prefs;
    box.innerHTML = `<label class="tog"><input type="checkbox" data-k="free_between_models" ${pr.free_between_models ? "checked" : ""}><span><b>Free memory between different models</b><small>When the next job uses other models, ComfyUI unloads the old ones first. The graphics chip shares the PC's memory, so piling models up makes Windows page to disk and freeze.</small></span></label>
      <label class="tog"><input type="checkbox" data-k="keep_awake" ${pr.keep_awake ? "checked" : ""}><span><b>Keep the PC awake while rendering</b><small>Sleep in the middle of a render leaves the job stuck.</small></span></label>
      <div class="row"><label class="lbl" style="margin:0">Warn when a render shows no progress for</label><select data-k="stall_minutes">${[5, 10, 15, 30, 60].map(v => `<option ${v === pr.stall_minutes ? "selected" : ""}>${v}</option>`).join("")}</select><span class="hint">minutes</span></div>
      <div class="row"><button class="ghost" id="gfree">Free memory now</button><button class="ghost" id="gpow">Check sleep & crash events</button></div><div id="gpowo" class="hint"></div>`;
    $$("[data-k]", box).forEach(x => x.onchange = () => post("/studio/api/guard/prefs", { [x.dataset.k]: x.type === "checkbox" ? x.checked : +x.value }).then(() => toast("Saved")));
    $("#gfree", box).onclick = () => post("/studio/api/guard/free", {}).then(() => toast("Memory will be freed after the current job"));
    $("#gpow", box).onclick = async () => { const o = $("#gpowo", box); o.textContent = "Reading the Windows log…"; try { const ev = await api("/studio/api/diag/power?hours=24"); o.innerHTML = ev.length ? ev.map(e => `${esc(e.t)} · ${esc(e.msg || e.src)}`).join("<br>") : "No sleep, crash or low-memory events in the last 24 hours."; } catch (e) { o.textContent = e.message; } };
  }).catch(() => { $("#gdbox", p).innerHTML = `<p class="muted">Restart ComfyUI to turn this on.</p>`; });
}
/* ------------------------------------------------------------ QUEUE + WS */
/* progress from the server: works on every device (ComfyUI only sends step events to the browser that queued
   the job) and moves smoothly through slow steps (Wan: ~23 min per step) using the last step's duration */
const fmtEta = s => s == null ? "" : s < 90 ? "under 2 min left" : s < 3600 ? `about ${Math.round(s / 60)} min left` : `about ${(s / 3600).toFixed(1)} h left`;
async function serverProgress() {
  if (document.hidden || !S.running) return;
  try {
    const g = await api("/studio/api/guard"); const j = g.job;
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
function appNameOf(item) { const ex = item[3] || {}; const a = S.apps.find(x => x.id === ex.studio_app); return a ? appShort(a) : "Node graph"; }
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
      cx.onclick = async () => { await fetch("/studio/api/schedule/" + x.id, { method: "DELETE" }); loadSchedule(); toast("Cancelled"); };
      now.onclick = async () => { try { await post(`/studio/api/schedule/${x.id}/now`, {}); toast("Sent to the queue"); } catch (e) { toast(e.message, true); } loadSchedule(); setTimeout(pollQueue, 500); };
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
      if (x) x.onclick = async () => { x.disabled = true; try { await post(`/studio/api/remote_jobs/${j.prompt_id}/cancel`, {}); toast(j.status === "fetching" ? `Stopped. The model keeps downloading on ${j.worker}, so it's there next time` : "Stopped on " + j.worker); } catch (e) { toast(e.message, true); } pollRemote(); };
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
  if (info.vram_usable) return `${gb(info.vram_in_use || 0)}/${gb(info.vram_usable)} GB in use` + (info.vram_cached >= 1e8 ? ` · ${gb(info.vram_cached)} GB kept from the last job` : "");
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
  try { S.workers = await api("/studio/api/workers"); } catch { S.workers = []; }
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
  try { S.remote = await api("/studio/api/remote_jobs"); } catch { return; }
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
      const r = await post("/studio/api/queue/move", { prompt_ids: list, target: t });
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

let ws;
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
      case "progress": if (!(S.srvAt && Date.now() - S.srvAt < 10000)) { S.progress = d.max ? d.value / d.max : 0; S.progressNode = `Step ${d.value}/${d.max}`; } drawQueueCard(); { const w8 = S.compare && S.compare.items.find(x => x.prompt_id === S.running && !x.url); if (w8 && location.hash.startsWith("#/create")) { const c = $(`.cmpcell[data-pid="${w8.prompt_id}"] .cmpwait`); if (c) c.textContent = "Rendering " + Math.round(S.progress * 100) + "%"; } } { const tk = $(`.take[data-pid="${S.running}"] span`); /* a take rendering, on any takes page */ if (tk) tk.textContent = Math.round(S.progress * 100) + "%"; } if (!$("#drawer").classList.contains("hidden")) drawDrawer(); break;
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
  if (S.sbDraw) S.sbDraw();
  if (mine) toast((isVid ? "Shot" : "Picture") + " ready");
}

async function sysMon() {
  if (document.hidden || !$("#mon") || getComputedStyle($("#mon")).display === "none") return;
  try {
    const m = await api("/studio/api/sysmon");
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
    const [ws, rj, q, m, ds] = await Promise.all([api("/studio/api/workers"), api("/studio/api/remote_jobs"), api("/queue"), api("/studio/api/sysmon"),
      api("/studio/api/dispatch").catch(() => null)]);
    let box = $("#m-mach");
    if (ws.length < 2) { if (box) box.remove(); return; }       // only this PC: nothing to add
    if (!box) { box = h(`<div id="m-mach" class="mmach"><div class="mmh">MACHINES</div><div class="mml"></div></div>`); ($("#machines") || mon).append(box); }
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
    if (ds) {
      let sw = $(".mmauto", box);
      if (!sw) {
        sw = h(`<label class="mmauto" title="While this PC renders, a waiting job goes to a free machine that already has its models (Incognito jobs too)."><input type="checkbox"> Share waiting jobs with free machines</label>`);
        box.append(sw);
        $("input", sw).onchange = async e => {
          try { await api("/studio/api/dispatch", { method: "POST", body: JSON.stringify({ enabled: e.target.checked }) }); }
          catch (err) { toast(String(err.message || err)); }
        };
      }
      const cb = $("input", sw);
      if (document.activeElement !== cb) cb.checked = !!ds.enabled;
    }
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
  if (!quiet) { Promise.all([loadChars(), loadProjects(), loadBoards()]).then(() => route()); if (S.incog) setTimeout(checkUnfinished, 800); }
}
function migrateLocalValues() {
  // Studio's earlier settings belonged to what is now the private space.
  if (store.get("valuesMigrated", false)) return;
  for (const k of Object.keys(S.values)) {
    if (k.startsWith("P|")) continue;
    const v = S.values[k];
    for (const f of Object.keys(v)) if (typeof v[f] === "string" && v[f].startsWith("studio/")) v[f] = "studio_private/" + v[f].slice(6);
    S.values["P|" + k] = v; delete S.values[k];
  }
  for (const k of Object.keys(S.stage)) if (!k.startsWith("P|")) delete S.stage[k];
  store.set("valuesMigrated", true); saveState();
}
async function toggleIncog() {
  if (S.incog) { await post("/studio/api/private/lock", {}); setIncog(false); return; }
  const st = await api("/studio/api/private/status");
  if (st.unlocked) { setIncog(true); return; }
  if (!st.has_password) return toast(st.local ? "Set a password first: sidebar › Password" : "Set a password on the PC first (/studio/password)", true);
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
    try { await post("/studio/api/private/unlock", { password: $("#pw", d).value }); close(); setIncog(true); }
    catch (e) { const er = $("#pwerr", d); er.textContent = (e.data && e.data.error) || "Wrong password."; er.classList.remove("hidden"); }
  };
  $("#go", d).onclick = go; $("#pw", d).onkeydown = e => { if (e.key === "Enter") go(); };
  setTimeout(() => $("#pw", d).focus(), 50);
}
$$(".incog-btn").forEach(b => b.onclick = toggleIncog);
let idleT; const IDLE = 20 * 60 * 1000;
const bump = () => { clearTimeout(idleT); idleT = setTimeout(async () => { if (S.incog) { try { await post("/studio/api/private/lock", {}); } catch { } setIncog(false);  } }, IDLE); };
["click", "keydown", "touchstart", "scroll"].forEach(ev => document.addEventListener(ev, bump, { passive: true })); bump();

/* ------------------------------------------------------------ boot */

/* ---- unfinished queue from before a shutdown or restart */
async function checkUnfinished() {
  let r; try { r = await api("/studio/api/queue/unfinished"); } catch { return; }
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
    close(); try { await api("/studio/api/queue/unfinished/discard", { method: "POST", body: JSON.stringify({}) }); toast("Discarded"); } catch (e) { toast(e.message, true); }
  };
  $("[data-go]", d).onclick = async () => {
    const go = [...sel], drop = items.map(i => i.prompt_id).filter(id => !sel.has(id));
    close();
    try {
      if (drop.length) await api("/studio/api/queue/unfinished/discard", { method: "POST", body: JSON.stringify({ ids: drop }) });
      if (!go.length) return toast("Nothing continued");
      const r2 = await api("/studio/api/queue/unfinished/resume", { method: "POST", body: JSON.stringify({ ids: go }) });
      toast(`Queued ${r2.sent} render${r2.sent === 1 ? "" : "s"} again` + (r2.failed?.length ? ` — ${r2.failed.length} couldn't be sent` : ""), !!r2.failed?.length);
      if (r2.failed?.length) console.warn("unfinished resume", r2.failed);
      pollQueue();
    } catch (e) { toast(e.message, true); }
  };
}

(async function boot() {
  SR = await import("/studio/static/screenplay/rules.js");
  try { S.apps = await api("/studio/api/apps"); } catch (e) { toast("Couldn't load apps: " + e.message, true); }
  S.apps = S.apps.filter(a => !a.error);
  const ord = a => { const k = APP_ORDER.indexOf(appShort(a).toLowerCase()); return k < 0 ? 50 : k; };
  S.apps.sort((a, b) => ord(a) - ord(b) || appShort(a).localeCompare(appShort(b)));
  if (S.appIdx >= S.apps.length) S.appIdx = 0;
  try { const a = await api("/studio/api/private/status"); S.pw = a; setIncog(a.unlocked && store.get("incog", true), true); } catch { }
  migrateLocalValues();
  await Promise.all([loadChars(), loadProjects(), loadBoards(), loadAiTools()]);
  buildNav();
  connect(); pollQueue(); sysStats(); setInterval(sysStats, 15000); setInterval(pollQueue, 5000); sysMon(); setInterval(sysMon, 3000); machinesMon(); setInterval(machinesMon, 8000);
  loadWorkers().then(pollRemote); setInterval(pollRemote, 3000); setInterval(loadWorkers, 60000);
  setInterval(serverProgress, 3000);
  loadSchedule(); setInterval(loadSchedule, 30000);
  route();
  setTimeout(checkUnfinished, 1500);
})();
})();
