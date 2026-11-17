/* Studio Previz: a .glb set in the browser, directed like a shoot.
   Look through the file's cameras, or make Studio cameras and direct them: framing (shot sizes and kinds of shot), camera
   height and angle, camera moves (presets, or keyframes: a start and an end state of everything, light included) that play
   with the set's animation, lighting setups,
   and objects (hide or move what's in the file, add stand-ins and props). What you direct is saved with the set.
   The maths is in shots.js, lighting.js and objects.js; three.js is vendored in ../vendor/three.
   Used by app.js: const { mountViewer } = await import("/studio/static/previz/viewer.js"). */
import * as T from "../vendor/three/three.bundle.min.js";
import * as SH from "./shots.js";
import * as LI from "./lighting.js";
import * as OB from "./objects.js";

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const h = html => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const $ = (s, el) => el.querySelector(s);
const $$ = (s, el) => [...el.querySelectorAll(s)];
const uid = p => p + Math.random().toString(36).slice(2, 8);
const A3 = v => [v.x, v.y, v.z];
const r3 = a => a.map(v => Math.round(v * 1000) / 1000);

export const ASPECTS = [["1.7778", "16:9"], ["2.39", "2.39:1"], ["1.85", "1.85:1"], ["1.3333", "4:3"], ["1", "1:1"], ["0.5625", "9:16"], ["fill", "Fill"]];
const AX = { "+X": [1, 0, 0], "-X": [-1, 0, 0], "+Y": [0, 1, 0], "-Y": [0, -1, 0], "+Z": [0, 0, 1], "-Z": [0, 0, -1] };
const GROUP_RADIUS = 12;             // m: who counts as the subject's group
const PENDING_ROOM = 20;             // s of timeline after a start keyframe, to pick where the end goes
export const MOVE_MIN = SH.MIN_TIME, MOVE_MAX = 3600;   // s: how short and how long a move or a shot can be (typed in Moves)
const PATH_STEPS = 24;               // segments of a Studio camera's move path (free camera, gizmos)
const NUDGE_STEPS = [[0.01, "1 cm"], [0.05, "5 cm"], [0.1, "10 cm"], [0.25, "25 cm"], [0.5, "50 cm"], [1, "1 m"]];   // Moves › Camera position
const LEN_SLIDER = 30;               // s: the (Shot) Length slider's usual range (it grows to fit a longer one typed in)
/* A length or a start time typed in: seconds, kept inside [lo, hi], to the hundredth; null when it isn't a number. */
export function seconds(v, lo = MOVE_MIN, hi = MOVE_MAX) {
  const x = typeof v === "number" ? v : parseFloat(String(v ?? "").trim().replace(",", "."));
  return Number.isFinite(x) ? Math.round(Math.max(lo, Math.min(hi, x)) * 100) / 100 : null;
}
/* The side panel's tabs: id, label, what's in it, and a line icon (24 × 24, drawn with the text colour). */
const TABS = [
  ["frame", "Framing", "Shot size, kind of shot, side, lens", '<rect x="3" y="6" width="18" height="12" rx="1.5"/><path d="M7 9.5v5M17 9.5v5"/>'],
  ["height", "Height", "Camera height and angle, Dutch angle, pan and tilt", '<path d="M12 3v18M8.5 6.5 12 3l3.5 3.5M8.5 17.5 12 21l3.5-3.5"/><path d="M3 12h4M17 12h4"/>'],
  ["move", "Moves", "Camera moves, keyframes and the camera's position", '<path d="M3 18c4 0 5-12 9-12s5 12 9 12"/><circle cx="3" cy="18" r="1.6"/><path d="M18.5 15.5 21 18l-2.5 2.5"/>'],
  ["light", "Light", "Lighting setups", '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/>'],
  ["objects", "Objects", "Stand-ins, props and what's in the set", '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/>'],
];
const FILE_LIGHT_SCALE = 1 / 683;     // Blender writes watts × 683 lm/W as candela; this brings its lights back to its look
const fmtTime = (t, fps) => { const m = Math.floor(t / 60), s = t - m * 60; return `${m}:${s.toFixed(1).padStart(4, "0")} · f ${Math.round(t * fps)}`; };
const fmtSec = t => { const m = Math.floor(t / 60), s = t - m * 60; return `${m}:${s.toFixed(1).padStart(4, "0")}`; };

function correction(forward, up) {         // a camera rig's own axes -> a three.js camera's (looks down -Z, +Y up)
  const z = new T.Vector3(...AX[forward] || AX["-Z"]).negate(), y = new T.Vector3(...AX[up] || AX["+Y"]);
  return new T.Quaternion().setFromRotationMatrix(new T.Matrix4().makeBasis(new T.Vector3().crossVectors(y, z), y, z));
}
function frustumGeometry(lens, d = 0.8) {
  const hw = d * (SH.SENSOR / 2) / lens, hh = hw / 1.7778, up = hh * 1.5;
  const c = [[-hw, -hh, -d], [hw, -hh, -d], [hw, hh, -d], [-hw, hh, -d]], p = [];
  c.forEach((v, i) => { p.push(0, 0, 0, ...v, ...v, ...c[(i + 1) % 4]); });
  p.push(-hw * 0.5, hh * 1.12, -d, hw * 0.5, hh * 1.12, -d, hw * 0.5, hh * 1.12, -d, 0, up, -d, 0, up, -d, -hw * 0.5, hh * 1.12, -d);
  const g = new T.BufferGeometry(); g.setAttribute("position", new T.Float32BufferAttribute(p, 3)); return g;
}

/* Where an object (and what carries it) actually moves: the first and last moment any of its tracks changes value. */
export function moveRange(obj, tracksOf) {
  let lo = Infinity, hi = -Infinity;
  for (let o = obj; o; o = o.parent) {
    for (const tr of tracksOf.get(o.name) || []) {
      const n = tr.times.length, k = tr.getValueSize(), v = tr.values;
      if (n < 2) continue;
      const eps = /quaternion/.test(tr.name) ? 1e-5 : 1e-4;
      const differs = (i, j) => { for (let c = 0; c < k; c++) if (Math.abs(v[i * k + c] - v[j * k + c]) > eps) return true; return false; };
      let a = -1;
      for (let i = 1; i < n; i++) if (differs(i, 0)) { a = i - 1; break; }
      if (a < 0) continue;                // this track holds still
      let b = n - 1;
      for (let j = n - 2; j >= 0; j--) if (differs(j, n - 1)) { b = j + 1; break; }
      lo = Math.min(lo, tr.times[a]); hi = Math.max(hi, tr.times[b]);
    }
  }
  return hi - lo > 0.05 ? [lo, hi] : null;
}

/* A new Studio camera's name, never one already taken: Shot 3 (the next number free), or “CAM_A · shot” (then · shot 2…)
   for a camera made from a set camera. */
export function camName(taken, from) {
  const has = new Set(taken);
  if (from) { let k = 1, n = `${from} · shot`; while (has.has(n)) n = `${from} · shot ${++k}`; return n; }
  let k = taken.length + 1;
  while (has.has(`Shot ${k}`)) k++;
  return `Shot ${k}`;
}

export function webglAvailable() {
  try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); } catch { return false; }
}

function cleanDirection(d) {
  d = d && typeof d === "object" ? d : {};
  return { v: 1, cams: Array.isArray(d.cams) ? d.cams : [], hidden: Array.isArray(d.hidden) ? d.hidden : [], moved: d.moved && typeof d.moved === "object" ? d.moved : {},
    added: Array.isArray(d.added) ? d.added : [], light: d.light && typeof d.light === "object" ? d.light : null, subject: d.subject || null, with: d.with || null };
}

/**
 * Mounts the viewer in `root`.
 * opts: url (the .glb), info (glbsets.inspect), initial {camera, aspect, look, tab, isolate}, direction (what was directed before),
 *   props [{id, name, url}] (.glb props that can be added), uploadProp(file) -> Promise<{id, name, url}>,
 *   onState(view), onDirection(direction), onThumb(blob), needThumb, toast(message, isError).
 */
export function mountViewer(root, opts) {
  const info = opts.info || {}, fileCams = info.cameras || [], fps = info.fps || 24, init = opts.initial || {};
  const toast = opts.toast || (() => { });
  const dir = cleanDirection(opts.direction);
  let props = (opts.props || []).slice();
  const st = {
    mode: "free", cur: null, aspect: ASPECTS.some(a => a[0] === init.aspect) ? init.aspect : "1.7778", aspectAuto: !init.aspect,
    look: init.look === "clay" ? "clay" : "textured", thirds: false, gizmos: true, playing: false, stopAt: null, t: 0, speed: 1, freeLens: 24,
    fileDuration: info.duration || 0, step: 0.1, moveView: null, top: false, dope: false, isolate: init.isolate !== false, tab: ["frame", "height", "move", "light", "objects"].includes(init.tab) ? init.tab : "frame", sel: null, filter: "",
  };
  const el = h(`<div class="gv">
    <div class="gv-main">
      <div class="gv-stage"><div class="gv-frame"><canvas tabindex="0"></canvas><div class="gv-thirds hidden"></div>
        <div class="gv-hud tl"></div><div class="gv-hud tr"></div><div class="gv-hud bl gv-ophint hidden">✥ Operating: drag to pan &amp; tilt · right-drag to slide · wheel in/out · W A S D Q E to move · Esc when done</div>
        <div class="gv-rec hidden"></div><button class="gv-back hidden"></button></div>
        <div class="gv-top hidden"><canvas tabindex="-1"></canvas><div class="gv-hud tl gv-toplabel">Top view</div>
          <div class="gv-topbar"><button class="icon gv-topfit" title="See the whole set">⌂</button><button class="icon gv-topx" title="Close the top view">✕</button></div>
          <div class="gv-tophint">Click to select · drag to move · Shift-drag to turn · drag a camera to move it · double-click to frame · wheel to zoom · right-drag to pan</div></div>
        <div class="gv-load"><b>Loading the set…</b><div class="bar"><div></div></div><span></span></div>
      </div>
      <div class="gv-transport hidden">
        <button class="icon gv-play" title="Play / pause (Space)">▶</button>
        <input type="range" class="gv-time" min="0" max="1" step="${1 / fps}" value="0">
        <span class="gv-tc"></span>
        <button class="ghost sm gv-iso hidden"></button>
        <button class="ghost sm gv-keysb" title="Each camera's shot on the timeline, with its move's keyframes in it: drag the ◆ to decide when the move starts and ends in the shot (the shot stays as long), drag the shot or its ends to move or resize it">◆ Keys</button>
        <select class="gv-speed" title="Playback speed"><option value="0.25">¼×</option><option value="0.5">½×</option><option value="1" selected>1×</option><option value="2">2×</option></select>
      </div>
      <div class="gv-dope hidden"><div class="gv-druler"><span class="gv-dlabel"></span><div class="gv-dtrack"></div></div><div class="gv-dlanes"></div></div>
      <div class="gv-tools">
        <label class="hint">Frame <select class="gv-aspect">${ASPECTS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></label>
        <label class="hint">Look <select class="gv-look"><option value="textured">Textured</option><option value="clay">Clay</option></select></label>
        <button class="ghost sm gv-thirdsb" title="Rule-of-thirds guides">⊞ Thirds</button>
        <button class="ghost sm gv-gizb on" title="Show cameras, moves and lights (free camera)">◰ Gizmos</button>
        <button class="ghost sm gv-topb" title="A top view of the set next to this one: click objects and move them there, drag the cameras">◫ Top view</button>
        <label class="hint gv-lensl">Lens <input type="range" class="gv-lens" min="10" max="200" step="1"> <b class="gv-lensv"></b></label>
        <button class="ghost sm gv-fit" title="See the whole set (free camera)">⌂ Whole set</button>
        <button class="ghost sm gv-snap" title="A picture of this view (1920 px): for a storyboard shot or a location shot">📷 Snapshot</button>
        <button class="ghost sm gv-movie" title="A movie of this camera and its move (with the set's animation), saved to the Gallery">🎬 Movie</button>
        <button class="ghost sm gv-thumbb" title="Use this view as the set's picture on its card">🖼</button>
        <button class="ghost sm gv-fs" title="Full screen"><svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M1 5V1h4M11 1h4v4M15 11v4h-4M5 15H1v-4"/></svg></button>
      </div>
      <p class="hint gv-help">Free camera: drag to orbit · right-drag to pan · wheel to dolly · W A S D to fly, Q E down and up, Shift faster · double-click an object to bring it into frame and move it (a spot on the floor: the pivot).
        Click an object to select it (Objects tab). On a camera: drag to leave it · ↑ ↓ change camera · Space plays · ← → step a frame.</p>
    </div>
    <aside class="gv-side panel">
      <div class="gv-camrow"><select class="gv-camsel" title="The camera you look through and direct"></select>
        <button class="icon gv-new" title="New camera on the main subject: medium shot, eye level, basic light">＋</button><button class="icon gv-op" title="Operate this camera by hand: drag to pan and tilt, right-drag to slide, wheel in and out, W A S D Q E to move it">✥</button><button class="icon gv-ren" title="Rename this camera">✎</button><button class="icon gv-del" title="Delete this Studio camera">✕</button></div>
      <div class="hint gv-camnote"></div>
      <div class="gv-subj"><label class="hint" title="Who the shot is about: shot sizes, heights and sides are measured on it, lights are placed around it, and a move that follows, follows it">Subject <select class="gv-subject"></select></label><label class="hint" title="A second person, for a Two-shot (both in frame) or Over the shoulder (past their shoulder onto the subject). Nothing else uses it; it applies the next time you frame">With <select class="gv-with"></select></label></div>
      <div class="gv-tabs" role="tablist">${TABS.map(([k, l, tip, icon]) => `<button data-tab="${k}" role="tab" title="${tip}"><svg viewBox="0 0 24 24" aria-hidden="true">${icon}</svg><span>${l}</span></button>`).join("")}</div>
      <div class="gv-tabbody"></div>
      ${(info.missing || []).length ? `<details class="gv-missing"><summary>${info.missing.length} camera${info.missing.length > 1 ? "s aren't" : " isn't"} in this file</summary>
        <p class="hint">Only their aim targets were exported. In Blender: File › Export › glTF 2.0 (.glb), tick <b>Include › Cameras</b>, export again over the same file, then press ↻ on the set.</p>
        <ul>${info.missing.map(n => `<li>${esc(n)}</li>`).join("")}</ul></details>` : ""}
    </aside></div>`);
  root.append(el);
  const canvas = $("canvas", el), stage = $(".gv-stage", el), frame = $(".gv-frame", el), load = $(".gv-load", el);
  const hudL = $(".gv-hud.tl", el), hudR = $(".gv-hud.tr", el), hudB = $(".gv-ophint", el), back = $(".gv-back", el), body = $(".gv-tabbody", el);
  const fail = msg => { load.classList.remove("hidden"); load.innerHTML = `<b>Couldn't show this set</b><span>${esc(msg)}</span>`; };

  let renderer;
  try { renderer = new T.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" }); }
  catch { fail("This browser can't draw 3D here (WebGL is off or not supported)."); return { dispose() { el.remove(); } }; }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = T.SRGBColorSpace;
  renderer.toneMapping = T.AgXToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFSoftShadowMap;
  const scene = new T.Scene(), BG = new T.Color(0x1a1a1f);
  scene.background = BG.clone();
  const pmrem = new T.PMREMGenerator(renderer);
  const envTex = pmrem.fromScene(new T.RoomEnvironment(), 0.04).texture;
  scene.environment = envTex;
  const hemi = new T.HemisphereLight(0xffffff, 0x303038, 0.8); scene.add(hemi);
  const rig = new T.Group(); rig.name = "Studio lights"; scene.add(rig);
  const added = new T.Group(); added.name = "Studio objects"; scene.add(added);
  const view = new T.PerspectiveCamera(40, 16 / 9, 0.05, 5000); view.filmGauge = SH.SENSOR;
  const oview = new T.OrthographicCamera(-1, 1, 1, -1, 0.05, 5000);
  const gizmos = new T.Group(); gizmos.visible = false; scene.add(gizmos);
  const tmpCam = new T.PerspectiveCamera(); tmpCam.filmGauge = SH.SENSOR;

  // my listeners first: a drag on a camera leaves it (OrbitControls then takes the same drag); a click selects
  let down = null;
  const lensNow = () => st.mode === "cam" && st.cur ? (st.cur.kind === "studio" ? (studioPose(st.cur.cam, st.t) || {}).lens || 35 : st.cur.info.lens || 35) : st.freeLens;
  const operating = () => st.operate && st.mode === "cam" && st.cur && st.cur.kind === "studio";
  const onDown = e => {
    st.fly = null;                        // you take over from a double-click's fly
    down = { x: e.clientX, y: e.clientY, left: false, lx: e.clientX, ly: e.clientY, button: e.button };
    if (operating()) { canvas.setPointerCapture(e.pointerId); down.op = true; return; }
    if (st.mode === "cam" && e.isPrimary !== false && !(tc && tc.axis)) { explore(); down.left = true; }
  };
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", e => {
    if (!down || !down.op) return;
    const dx = e.clientX - down.lx, dy = e.clientY - down.ly; down.lx = e.clientX; down.ly = e.clientY;
    const r = canvas.getBoundingClientRect(), degPx = T.MathUtils.radToDeg(SH.vfov(lensNow(), aspectNum())) / r.height;
    if (down.button === 2) {            // right-drag: slide the camera (as if dragging the set)
      const cur = studioPose(st.cur.cam, st.t), m = SH.poseDist(cur) * Math.tan(T.MathUtils.degToRad(degPx));
      editPose(st.cur, p => SH.slide(p, -dx * m, dy * m, 0));
    } else editPose(st.cur, p => SH.aim(p, -dx * degPx, -dy * degPx));      // drag: pan and tilt on the spot
  });
  canvas.addEventListener("wheel", e => {
    if (operating()) { e.preventDefault(); const cur = studioPose(st.cur.cam, st.t); editPose(st.cur, p => SH.slide(p, 0, 0, -Math.sign(e.deltaY) * Math.max(0.05, SH.poseDist(cur) * 0.06))); return; }
    if (st.mode === "cam") explore();
  }, { passive: false });
  canvas.addEventListener("contextmenu", e => { if (operating()) e.preventDefault(); });
  const controls = new T.OrbitControls(view, canvas);
  controls.enableDamping = true; controls.dampingFactor = 0.12; controls.zoomToCursor = true; controls.enabled = false;
  let dirty = true;
  controls.addEventListener("change", () => { dirty = true; });
  const tc = new T.TransformControls(view, canvas);
  tc.setSize(0.8);
  const tcHelper = tc.getHelper(); scene.add(tcHelper);
  tc.addEventListener("dragging-changed", e => { controls.enabled = !e.value && st.mode === "free"; if (!e.value) objectMoved(); });
  tc.addEventListener("change", () => { dirty = true; });
  canvas.addEventListener("pointerup", e => {
    if (!down) return;
    if (down.op) { const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y) >= 5; down = null; if (!moved) pickAt(e.clientX, e.clientY); return; }
    const click = Math.hypot(e.clientX - down.x, e.clientY - down.y) < 5 && !tc.dragging;
    if (click && down.left && st.cur) setMode("cam");          // only a click: stay on the camera
    if (click) pickAt(e.clientX, e.clientY);
    down = null;
  });

  // ---- frame (gate) and lens
  const aspectNum = () => st.aspect === "fill" ? (view.aspect || 16 / 9) : +st.aspect;
  function layout() {
    let W = stage.clientWidth, Hh = stage.clientHeight;
    if (!W || !Hh) return;
    if (st.top) {                  // the top view beside the view or under it: whichever leaves the view the bigger picture
      const a = st.aspect === "fill" ? 16 / 9 : +st.aspect, fit = (w, hh) => Math.min(w, hh * a) * Math.min(hh, w / a);
      const mw = Math.max(120, Math.min(Math.round(Hh * a), Math.round(W * 0.62), W - 220));       // side by side: the plan ≥ 220 px
      const mh = Math.max(90, Math.min(Math.round(W / a), Math.round(Hh * 0.62), Hh - 170));       // stacked: the plan ≥ 170 px
      const rows = fit(W, mh) > fit(mw, Hh);
      stage.style.gridTemplateColumns = rows ? "100%" : `${mw}px ${W - mw}px`;
      stage.style.gridTemplateRows = rows ? `${mh}px ${Hh - mh}px` : "100%";
      topEl.classList.toggle("below", rows);
      topSize(rows ? W : W - mw, rows ? Hh - mh : Hh);
      if (rows) Hh = mh; else W = mw;
    } else { stage.style.gridTemplateColumns = stage.style.gridTemplateRows = ""; }
    let w = W, hh = Hh;
    if (st.aspect !== "fill") { const a = +st.aspect; if (W / Hh > a) w = Math.round(Hh * a); else hh = Math.round(W / a); }
    frame.style.width = w + "px"; frame.style.height = hh + "px";
    renderer.setSize(w, hh, false);
    view.aspect = w / hh; view.updateProjectionMatrix(); dirty = true;
  }
  const ro = new ResizeObserver(layout); ro.observe(stage);

  // ---- loading
  let gltf = null, mixer = null, actions = [], meshes = [], bounds = null, fileLights = [], chars = [];
  const sources = [];           // every camera you can look through: the file's and Studio's
  const origMat = new Map(), fileTRS = new Map(), byKey = new Map(), animatedObjs = new Set(), charMeshes = new Set();
  const clay = new T.MeshStandardMaterial({ color: 0xb9b9bf, roughness: 0.85, metalness: 0 });
  const clayGhost = new T.MeshStandardMaterial({ color: 0xb9b9bf, roughness: 1, transparent: true, opacity: 0.12, depthWrite: false });
  const gizMat = new T.LineBasicMaterial({ color: 0xd6d6de }), gizOn = new T.LineBasicMaterial({ color: 0xff6b3d }), gizStudio = new T.LineBasicMaterial({ color: 0x8fd3ff });
  const draco = new T.DRACOLoader().setDecoderPath(new URL("../vendor/three/draco/", import.meta.url).href);
  const loader = new T.GLTFLoader().setDRACOLoader(draco).setMeshoptDecoder(T.MeshoptDecoder);
  const bar = $(".bar div", load), barTxt = $("span", load);
  const mb = n => (n / 1e6).toFixed(n < 1e7 ? 1 : 0);
  loader.load(opts.url, g => {
    if (!el.isConnected) return;
    try { ready(g); } catch (e) { console.warn(e); fail(String(e.message || e)); }
  }, ev => {
    const total = ev.total || (info.size || 0);
    if (total) { bar.style.width = Math.min(100, ev.loaded / total * 100) + "%"; barTxt.textContent = `${mb(ev.loaded)} of ${mb(total)} MB`; }
    if (total && ev.loaded >= total) { $("b", load).textContent = "Building the set…"; barTxt.textContent = ""; }
  }, e => fail(String((e && e.message) || e || "The file couldn't be read")));

  function ready(g) {
    gltf = g;
    scene.add(g.scene);
    g.scene.traverse(o => {
      if (o.isMesh) { meshes.push(o); origMat.set(o, o.material); o.castShadow = o.receiveShadow = true; }
      if (o.isLight) { fileLights.push({ light: o, base: o.intensity }); o.castShadow = false; }
    });
    bounds = new T.Box3().setFromObject(g.scene);
    const size = bounds.getSize(new T.Vector3()).length() || 10;
    view.near = Math.max(0.01, Math.min(0.05, size / 5000)); view.far = Math.max(2000, size * 4); view.updateProjectionMatrix();
    controls.maxDistance = size * 3;
    const byNode = new Map();
    g.parser.associations.forEach((v, k) => { if (k && k.isObject3D && v && v.nodes !== undefined && !byNode.has(v.nodes)) byNode.set(v.nodes, k); });
    for (const a of g.parser.json.animations || []) for (const ch of a.channels || []) { const o = byNode.get(ch.target && ch.target.node); if (o) animatedObjs.add(o); }
    g.scene.traverse(o => { if (o !== g.scene) byKey.set(OB.keyOf(o, g.scene), o); });
    chars = OB.characters(g.scene);
    chars.forEach(c => c.traverse(m => { if (m.isMesh) charMeshes.add(m); }));
    fileCams.forEach(c => {
      const obj = byNode.get(c.node);
      if (!obj) return;
      const giz = new T.LineSegments(frustumGeometry(c.lens || 50), gizMat); gizmos.add(giz);
      sources.push({ id: c.id, name: c.name, kind: c.kind === "ortho" ? "ortho" : "file", info: c, obj, corr: correction(c.forward, c.up), giz });
    });
    const tracksOf = new Map();          // node name -> its animation tracks
    for (const clip of g.animations) for (const tr of clip.tracks) {
      const n = T.PropertyBinding.parseTrackName(tr.name).nodeName;
      if (!tracksOf.has(n)) tracksOf.set(n, []); tracksOf.get(n).push(tr);
    }
    for (const src of sources) src.range = moveRange(src.obj, tracksOf);
    if (g.animations.length) {
      mixer = new T.AnimationMixer(g.scene);
      actions = g.animations.map(clip => { const a = mixer.clipAction(clip); a.setLoop(T.LoopOnce, 1); a.clampWhenFinished = true; a.play(); return a; });
      st.fileDuration = Math.max(st.fileDuration, ...g.animations.map(c => c.duration));
    }
    // where the file has everything (↺ Put back), then what was directed before
    g.scene.traverse(o => { if (o !== g.scene) fileTRS.set(o, { p: o.position.toArray(), q: o.quaternion.toArray(), s: o.scale.toArray() }); });
    dir.hidden.forEach(k => { const o = byKey.get(k); if (o) o.visible = false; });
    Object.entries(dir.moved).forEach(([k, m]) => { const o = byKey.get(k); if (o && m) setTRS(o, m); });
    dir.added.forEach(a => addObject(a, false));
    dir.cams.forEach(c => addStudioSource(c));
    setTime(0);
    applyLook();
    applyLight();
    load.classList.add("hidden");
    timeline();
    const first = sources.find(s => s.id === init.camera) || (init.camera === "free" ? null : sources.find(s => s.kind !== "studio") || sources[0]);
    if (first) pick(first); else { fitAll(); setMode("free"); }
    if (!dir.subject || !subjectObj(dir.subject)) dir.subject = inView();
    if (init.top) setTop(true);               // the top view (its floor is the subject's) and the keys, as they were left
    if (init.keys) setDope(true);
    drawSide();
    if (opts.needThumb) thumbSoon = 3;
  }

  // ---- time
  const duration = () => Math.max(st.fileDuration, ...dir.cams.map(c => {
    const m = c.move;
    if (!m || m.type === "static") return 0;
    return m.type === "custom" && m.pending ? (m.at || 0) + Math.max(m.dur || 0, PENDING_ROOM) : SH.shotOf(m)[1] + 0.5;   // room to pick where it ends
  }), 0);
  const tc_ = $(".gv-tc", el), slider = $(".gv-time", el), playB = $(".gv-play", el);
  /* Each camera plays on its own: the stretch of the timeline where it moves (the file bakes every action over the whole
     timeline, so that's where its keys change), or a Studio camera's move. The toggle shows the whole set's timeline. */
  const rangeOf = src => {
    if (!src) return null;
    if (src.kind === "studio") { const m = src.cam.move; return m && m.type && !(m.type === "custom" && m.pending) ? SH.shotOf(m) : null; }
    return src.range || null;
  };
  const span = () => (st.isolate && rangeOf(st.cur)) || [0, duration()];   // the stretch that plays
  const isoB = $(".gv-iso", el);
  function timeline() {
    const d = duration(), r = rangeOf(st.cur), [lo, hi] = span();
    slider.min = lo; slider.max = hi; slider.value = st.t;
    $(".gv-transport", el).classList.toggle("hidden", !(d > 0));
    isoB.classList.toggle("hidden", !r);
    if (r) {
      isoB.classList.toggle("on", st.isolate);
      isoB.textContent = st.isolate ? `◎ ${st.cur.name.length > 18 ? "This camera" : st.cur.name} only` : "⟷ Whole timeline";
      isoB.title = st.isolate ? `Plays only while this camera moves (${fmtSec(r[0])}–${fmtSec(r[1])} of the set). Click for the whole timeline.`
        : `The whole set's timeline (${fmtSec(d)}). Click to play only this camera's move (${fmtSec(r[0])}–${fmtSec(r[1])}).`;
    }
    lastTc = "";
    if (st.dope) drawDope();
  }
  isoB.onclick = () => { st.isolate = !st.isolate; const [lo, hi] = span(); if (st.t < lo || st.t > hi) setTime(lo); timeline(); state(); };
  function setTime(t) {
    st.t = Math.max(0, Math.min(duration() || 0, t));
    if (mixer) { for (const a of actions) a.time = Math.min(st.t, a.getClip().duration); mixer.update(0); }
    dirty = true;
  }
  function play(on, stopAt = null) {
    st.playing = on ?? !st.playing; st.stopAt = st.playing ? stopAt : null;
    playB.textContent = st.playing ? "❚❚" : "▶";
    const [lo, hi] = span();
    if (st.playing && stopAt == null && (st.t >= hi - 1e-3 || st.t < lo)) setTime(lo);
  }
  playB.onclick = () => play();
  slider.oninput = () => setTime(+slider.value);
  slider.onchange = () => atKey();
  $(".gv-speed", el).onchange = e => { st.speed = +e.target.value; };

  // ---- cameras
  const tmpS = new T.Vector3();
  function anchorOf(key) {     // where a subject is now (its hips if it has bones): a move that follows keeps this offset
    const o = subjectObj(key); if (!o) return null;
    let hips = null; o.traverse(c => { if (!hips && c.isBone && /hips|pelvis/i.test(c.name)) hips = c; });
    return A3((hips || o).getWorldPosition(new T.Vector3()));
  }
  function studioPose(cam, t) {
    if (!cam.A) return null;
    const m = cam.move;
    if (!m || !m.type || (m.type === "custom" && m.pending)) return cam.A;    // no move, or the end keyframe being shaped
    let off = null;
    if (m.follow && cam.anchor && cam.setup && cam.setup.subject) { const now = anchorOf(cam.setup.subject); if (now) off = now.map((v, i) => v - cam.anchor[i]); }
    return SH.poseAt({ ...m, A: m.A || cam.A }, t, off);
  }
  function poseOf(src, pos, quat) {       // -> lens (or ortho height)
    if (src.kind === "studio") {
      const p = studioPose(src.cam, st.t);
      if (!p) return 35;
      SH.applyPose(tmpCam, p); pos.copy(tmpCam.position); quat.copy(tmpCam.quaternion); return p.lens;
    }
    src.obj.updateWorldMatrix(true, false);
    src.obj.matrixWorld.decompose(pos, quat, tmpS);
    quat.multiply(src.corr);
    return src.info.lens || 35;
  }
  function addStudioSource(cam) {
    const giz = new T.LineSegments(frustumGeometry(cam.A ? cam.A.lens : 35), gizStudio); gizmos.add(giz);
    // the path of its move: PATH_STEPS + 1 points from the start (setFromPoints only refills a buffer this size)
    const path = new T.Line(new T.BufferGeometry().setFromPoints(Array.from({ length: PATH_STEPS + 1 }, () => new T.Vector3())), new T.LineDashedMaterial({ color: 0x8fd3ff, dashSize: 0.2, gapSize: 0.12 }));
    const end = new T.LineSegments(frustumGeometry(35), new T.LineBasicMaterial({ color: 0x8fd3ff, transparent: true, opacity: 0.45 }));
    gizmos.add(path, end);
    const s = { id: cam.id, name: cam.name, kind: "studio", cam, giz, path, end };
    sources.push(s);
    return s;
  }
  function setMode(m) {
    st.mode = m;
    if (st.operate && !(m === "cam" && st.cur && st.cur.kind === "studio")) { st.operate = false; $(".gv-op", el).classList.remove("on"); }
    controls.enabled = m === "free" && !tc.dragging;
    gizmos.visible = m === "free" && st.gizmos;
    rigMarks.visible = m === "free" && st.gizmos;
    $(".gv-lensl", el).classList.toggle("hidden", m !== "free");
    $(".gv-fit", el).classList.toggle("hidden", m !== "free");
    back.classList.toggle("hidden", !(m === "free" && st.cur));
    if (st.cur) back.textContent = "↩ Back to " + st.cur.name;
    tc.camera = m === "cam" && st.cur && st.cur.kind === "ortho" ? oview : view;
    if (m === "cam" && st.cur) { const l = poseOf(st.cur, view.position, view.quaternion); if (st.cur.kind !== "ortho") view.setFocalLength(l); }
    else view.setFocalLength(st.freeLens);
    dirty = true; drawHud(); drawCamSel(); state();
  }
  function pick(src) {
    const prev = st.cur;
    st.cur = src;
    if (src !== prev) st.moveView = null;          // Moves shows how this camera moves (by hand or a preset)
    const r = st.isolate && rangeOf(src);
    if (r && src !== prev && (st.t < r[0] || st.t > r[1])) setTime(r[0]);     // a camera starts at the start of its move
    if (src && src.kind !== "studio" && st.aspectAuto) {
      const a0 = src.kind === "ortho" ? (src.info.xmag / src.info.ymag) : src.info.aspect;
      const a = a0 && ASPECTS.find(x => x[0] !== "fill" && Math.abs(+x[0] - a0) < 0.02); if (a) { st.aspect = a[0]; aspectSel.value = a[0]; layout(); }
    }
    setMode(src ? "cam" : "free");
    timeline();
    drawSide();
  }
  function explore() {          // leave the camera, keeping its view
    if (st.mode !== "cam" || !st.cur) return;
    const l = poseOf(st.cur, view.position, view.quaternion);
    st.freeLens = st.cur.kind === "ortho" ? 35 : Math.round(l); lensIn.value = st.freeLens; lensV.textContent = st.freeLens + " mm";
    const fwd = new T.Vector3(0, 0, -1).applyQuaternion(view.quaternion);
    const hit = rayHit(view.position, fwd);
    controls.target.copy(view.position).addScaledVector(fwd, hit ? Math.max(0.5, hit.distance) : 5);
    setMode("free"); controls.update();
  }
  function fitAll() {
    if (!bounds || bounds.isEmpty()) return;
    const c = bounds.getCenter(new T.Vector3()), r = bounds.getSize(new T.Vector3()).length() / 2;
    const d = r / Math.sin(T.MathUtils.degToRad(view.getEffectiveFOV()) / 2);
    view.position.set(c.x + d * 0.6, c.y + d * 0.45, c.z + d * 0.65); controls.target.copy(c); view.lookAt(c); controls.update(); dirty = true;
  }
  back.onclick = () => { if (st.cur) setMode("cam"); };
  function step(d) {
    const list = sources; if (!list.length) return;
    const i = list.indexOf(st.cur);
    pick(list[(i + d + list.length) % list.length]);
  }

  // ---- the camera picker
  const camSel = $(".gv-camsel", el);
  function drawCamSel() {
    const studio = sources.filter(s => s.kind === "studio"), file = sources.filter(s => s.kind !== "studio");
    const lensOf = s => s.kind === "ortho" ? "ortho" : s.kind === "studio" ? (s.cam.A ? Math.round(s.cam.A.lens) + " mm" : "") : s.info.lens + " mm";
    // looking around freely from a camera, the list says so: picking that camera again is a change, and goes back to it
    const away = st.mode === "free" && st.cur;
    camSel.innerHTML = `<option value="free">✥ Free camera${away ? ` · looking around from ${esc(st.cur.name)}` : ""}</option>` +
      (studio.length ? `<optgroup label="Studio cameras">${studio.map(s => `<option value="${esc(s.id)}">${esc(s.name)} · ${esc(lensOf(s))}</option>`).join("")}</optgroup>` : "") +
      (file.length ? `<optgroup label="Set cameras (${file.length})">${file.map(s => `<option value="${esc(s.id)}">${esc(s.name)} · ${esc(lensOf(s))}${s.range ? ` · moves ${fmtSec(s.range[0])}–${fmtSec(s.range[1])}` : ""}</option>`).join("")}</optgroup>` : "");
    camSel.value = st.cur && !away ? st.cur.id : "free";
    if (st.dope) drawDope();
    const studioOn = st.cur && st.cur.kind === "studio";
    $(".gv-ren", el).classList.toggle("hidden", !studioOn); $(".gv-del", el).classList.toggle("hidden", !studioOn);
    const note = $(".gv-camnote", el);
    note.textContent = !st.cur ? (fileCams.length ? `${sources.length} cameras. Framing, Height and Moves make a Studio camera from this view.` : "Framing, Height and Moves make a Studio camera from this view.")
      : st.cur.kind === "studio" ? (SH.describe(st.cur.cam) || "A Studio camera: direct it with the tabs below.") + (st.mode === "free" ? " · you're looking around freely" : "")
      : `From the set${st.cur.info.animated ? ", animated" : ""}. Directing it makes a Studio camera from it; the set's camera stays as it is.`;
  }
  camSel.onchange = () => { const s = sources.find(x => x.id === camSel.value); if (s) pick(s); else { st.cur = null; setMode("free"); drawSide(); } camSel.blur(); };
  $(".gv-new", el).onclick = () => {
    const s = newStudioCam(true), a = subjectObj(dir.subject);
    const side = s.cam.setup && s.cam.setup.side ? (SH.SIDES.find(x => x.deg === s.cam.setup.side) || {}).label : "";
    toast(a ? `${s.name}: medium shot of ${OB.niceName(a.name)}${side ? `, ${side.toLowerCase()} (someone was in the way in front)` : ""}, eye level${dir.light && dir.light.preset === "three" ? ", three-point light" : ""}` : `${s.name}: from this view (pick a subject to frame it)`);
    st.tab = "frame"; drawTabs(); drawTab();
  };
  $(".gv-op", el).onclick = () => setOperate(!st.operate);
  $(".gv-ren", el).onclick = () => {
    const s = st.cur; if (!s || s.kind !== "studio") return;
    const n = prompt("Camera name", s.name); if (!n || !n.trim()) return;
    s.name = s.cam.name = n.trim().slice(0, 60); saveDir(); drawCamSel(); drawHud();
  };
  $(".gv-del", el).onclick = () => {
    const s = st.cur; if (!s || s.kind !== "studio" || !confirm(`Delete ${s.name}?`)) return;
    sources.splice(sources.indexOf(s), 1); [s.giz, s.path, s.end].forEach(o => { gizmos.remove(o); o.geometry.dispose(); });
    dir.cams = dir.cams.filter(c => c.id !== s.id); saveDir(); timeline(); st.cur = null; setMode("free"); drawSide();
  };

  /* The camera the tabs direct: the current Studio camera, or a new one from what you see now. */
  function currentViewPose() {
    const a = subjectBody(dir.subject), dist = a ? view.position.distanceTo(new T.Vector3(a.feet[0], a.feet[1] + a.h * 0.6, a.feet[2])) : (controls.target.distanceTo(view.position) || 5);
    if (st.mode === "cam" && st.cur) { const l = poseOf(st.cur, tmpCam.position, tmpCam.quaternion); tmpCam.setFocalLength(st.cur.kind === "ortho" ? 35 : l); return SH.poseOf(tmpCam, dist, st.cur.kind === "ortho" ? 35 : l); }
    return SH.poseOf(view, dist, st.freeLens);
  }
  /* A Studio camera. fresh: a starting point on the main subject (medium shot, front, eye level, 35 mm, basic light if the
     scene isn't lit yet); otherwise a copy of what you see now, to direct from there. */
  function newStudioCam(fresh) {
    const from = st.cur && st.cur.kind !== "studio" && !fresh ? st.cur.name : null;
    const A = currentViewPose();
    const setup = fresh ? { subject: dir.subject, with: dir.with, size: "MS", type: "single", side: 0, height: "eye", lens: 35, roll: 0, pan: 0, tilt: 0 }
      : { subject: dir.subject, with: dir.with, side: null, height: "custom", camY: A.p[1], lens: Math.round(A.lens), roll: A.roll || 0 };
    const cam = { id: uid("s"), name: camName(dir.cams.map(c => c.name), from), from, A, setup, move: null };
    dir.cams.push(cam);
    const s = addStudioSource(cam); pick(s);
    if (fresh && subjectBody(dir.subject)) {
      reframe(s, true);
      // a clean single: someone standing in front of the subject (the other half of a dialogue) would fill the frame
      // with their back, so the camera goes round (¾, then profile, then further) to where nobody's in the way
      if (inTheWay(cam)) for (const side of [45, -45, 90, -90, 135, -135]) { setup.side = side; reframe(s, true); if (!inTheWay(cam)) break; }
      if (!dir.light) setLight({ preset: "three" }, true);      // somewhere to work from: key, fill and back light
    }
    saveDir();
    return s;
  }
  /* Something (another person, a prop, a wall) between a camera and what it frames, in the middle of the picture. */
  function inTheWay(cam) {
    if (!cam.A || !cam.setup) return false;
    const subj = subjectObj(cam.setup.subject), mine = new Set();
    if (subj) subj.traverse(x => { if (x.isMesh) mine.add(x); });
    const from = new T.Vector3(...cam.A.p), to = new T.Vector3(...cam.A.t), d = from.distanceTo(to);
    const hit = new T.Raycaster(from, to.clone().sub(from).normalize(), 0.05, d).intersectObjects(meshes, false)
      .find(i => shown(i.object) && !mine.has(i.object) && !(i.object.material && i.object.material.transparent));
    return !!hit && hit.distance < d - 0.15;
  }
  /* Changes a Studio camera's pose by hand (operating): fn gets the pose and returns the new one. */
  function editPose(s, fn) {
    const cam = s.cam;
    beginEdit(s);
    cam.A = fn(cam.A || currentViewPose());
    Object.assign(cam.setup = cam.setup || {}, { free: true, height: "custom", camY: cam.A.p[1], side: null });
    commit(s); drawCamSel(); drawHud();
  }

  /* ---- keyframes: a Studio camera's state (pose, lens, roll, its tabs' settings, the light) at the start and the end of
     its move. The tabs always change the camera's working state (cam.A, cam.setup, the scene's light):
     - start set, end not yet ("pending"): what you change is the end being shaped, and the camera shows it;
     - both set: a change goes to the keyframe nearest the timeline (the timeline goes there first, so you see it). */
  const keyed = cam => !!(cam && cam.move && cam.move.type === "custom" && !cam.move.pending && cam.move.A && cam.move.B);
  const keyAt = cam => st.t <= (cam.move.at || 0) + (cam.move.dur || 0) / 2 ? "A" : "B";
  const poseOnly = k => { const o = { p: k.p.slice(), t: k.t.slice(), roll: k.roll || 0, lens: k.lens }; if (k.up) o.up = k.up.slice(); return o; };
  function snap(cam) {
    const o = { ...poseOnly(cam.A), setup: JSON.parse(JSON.stringify(cam.setup || {})) };
    if (st.sceneLight) o.light = JSON.parse(JSON.stringify(st.sceneLight));
    return o;
  }
  function loadKey(cam, k) {
    cam.A = poseOnly(k);
    if (k.setup) cam.setup = JSON.parse(JSON.stringify(k.setup));
    if (k.light && JSON.stringify(k.light) !== JSON.stringify(st.sceneLight)) { dir.light = { ...k.light.params }; applyLight(k.light); }
  }
  function beginEdit(s) {
    const cam = s.cam;
    if (!keyed(cam)) return;
    const m = cam.move, k = keyAt(cam), t = k === "A" ? (m.at || 0) : (m.at || 0) + m.dur;
    if (st.editing && st.editing.id === s.id && st.editing.k === k && st.editing.t === st.t) return;     // already there
    play(false);
    if (Math.abs(st.t - t) > 1e-6) setTime(t);
    loadKey(cam, m[k]);
    st.editing = { id: s.id, k, t: st.t };
  }
  function commit(s) {
    const cam = s.cam, m = cam.move;
    if (keyed(cam)) { m[keyAt(cam)] = snap(cam); st.lightKey = null; }
    else if (m && m.type && m.type !== "custom") syncMove(cam);
    s.giz.geometry.dispose(); s.giz.geometry = frustumGeometry(cam.A.lens);
    dirty = true; saveDir();
  }
  function takeView(s) {       // in the free camera: what you see becomes the camera's working state
    if (st.mode !== "free") return;
    const cam = s.cam;
    cam.A = currentViewPose();
    Object.assign(cam.setup = cam.setup || {}, { free: true, height: "custom", camY: cam.A.p[1], side: null, pan: 0, tilt: 0 });
  }
  function setStartKey(s) {
    const cam = s.cam, old = cam.move || {};
    takeView(s);
    if (keyed(cam)) { cam.move.A = snap(cam); SH.setKeyTimes(cam.move, Math.round(st.t * 100) / 100, null); }
    else cam.move = { type: "custom", amount: 0, dur: old.type === "custom" && old.dur ? old.dur : 4, at: Math.round(st.t * 100) / 100, ease: old.ease || "smooth",
      follow: !!old.follow, shake: old.shake || 0, A: snap(cam), B: null, pending: true, ...(old.pending && old.durSet ? { durSet: true } : {}),
      ...(old.type && !pendingMove(old) ? { shot: SH.shotOf(old) } : old.shot ? { shot: old.shot } : {}) };   // the camera's shot stays (the new move goes in it)
    cam.anchor = cam.anchor || anchorOf(cam.setup && cam.setup.subject);
    st.editing = null; saveDir(); timeline(); setMode("cam"); drawTab();
    toast(keyed(cam) ? "Start keyframe set again" : "◆ Start set. Now change anything: framing, height, lens, light, or move the camera. Then ◆ Set end.");
  }
  function setEndKey(s) {
    const cam = s.cam, m = cam.move;
    if (!m || m.type !== "custom") return toast("Set the start keyframe first", true);
    takeView(s);
    // the end goes where the timeline is, unless a Length was chosen while shaping it
    if (m.pending && !m.durSet && st.t > (m.at || 0) + 0.2) m.dur = seconds(st.t - (m.at || 0));
    m.B = snap(cam); m.pending = false; delete m.durSet;
    SH.setKeyTimes(m, null, null);                                       // its shot: what it had, grown to hold the move
    st.editing = null; saveDir(); timeline(); pick(s); setTime(SH.shotOf(m)[0]); drawTab();
    toast("◆ End set: ▶ Play the move");
  }
  /* ↺ Reset move: the camera goes back to where its move starts (keyframes: the start's position, framing, lens and
     light) and stays there: no move, not following, not handheld. */
  function resetMove(s, ask = true) {
    const cam = s.cam, m = cam.move;
    if (!m) return false;
    if (ask && !confirm(`Reset the move of ${s.name}? It goes back to where the move starts and stays still.`)) return false;
    play(false);
    const from = m.type === "custom" && m.pending ? m.at || 0 : SH.shotOf(m)[0];
    if (m.type === "custom" && m.A) loadKey(cam, m.A);
    else if (m.A) cam.A = poseOnly(m.A);
    cam.move = null; st.editing = null;
    setTime(from); commit(s); timeline(); setMode("cam"); drawTab(); drawHud();
    toast(`${s.name}: move reset`);
    return true;
  }
  function syncMove(cam) {
    const m = cam.move;
    if (!m) return;
    m.A = cam.A;
    if (m.type && m.type !== "custom") m.B = SH.presetEnd(cam.A, m.type, m.amount);
  }
  function setOperate(on) {
    if (on && !(st.cur && st.cur.kind === "studio")) directed();
    st.operate = !!on;
    $(".gv-op", el).classList.toggle("on", st.operate);
    if (st.operate) setMode("cam"); else drawHud();
    controls.enabled = st.mode === "free";
  }
  function directed() { return st.cur && st.cur.kind === "studio" ? st.cur : newStudioCam(); }
  /* directed(), for a control being dragged or held: a new camera doesn't redraw the tab under the pointer (that would end
     the drag); the control redraws it when it's let go. */
  function steady() {
    const made = !(st.cur && st.cur.kind === "studio");
    st.quiet = true;
    try { return directed(); } finally { st.quiet = false; if (made) st.stale = true; }
  }
  function redrawIfStale() { if (st.stale) { st.stale = false; drawTab(); } }    // (a control let go: only if a camera was made under it)

  /* ---- Moves › Camera position: the camera moved by hand as on a dolly or a crane (it doesn't turn). What it changes is
     the camera's working state, so: the camera with no move; where a preset move starts; the end being shaped; or, with
     both keyframes set, the one the timeline is on (Editing ◆ Start / ◆ End). */
  function nudge(right, up, forward) {
    const t = steady(), k = st.step;
    if (st.mode !== "cam") setMode("cam");          // you see what you move
    editPose(t, p => SH.track(p, right * k, up * k, forward * k));
    const cam = t.cam, pk = $(".gv-posk", body), tr = $(".gv-travel", body);
    if (pk) pk.textContent = posText(cam);
    if (tr) tr.textContent = travelText(cam);
  }
  function bindPad() {
    const sel = $(".gv-stepsel", body); if (sel) sel.onchange = () => { st.step = +sel.value; };
    $$("[data-nudge]", body).forEach(b => {
      const [r, u, f] = b.dataset.nudge.split(",").map(Number);
      let wait = 0, rep = 0;
      const stop = () => {
        if (!wait && !rep) return;
        clearTimeout(wait); clearInterval(rep); wait = rep = 0;
        ["pointerup", "pointercancel", "blur"].forEach(ev => window.removeEventListener(ev, stop));
        drawTab();                                   // let go: the tab shows the camera (and its keyframes) as they are now
      };
      b.onpointerdown = e => {                       // a press moves one step; held, it keeps moving
        if (e.button !== 0) return;
        e.preventDefault(); nudge(r, u, f);
        wait = setTimeout(() => { rep = setInterval(() => nudge(r, u, f), 70); }, 350);
        ["pointerup", "pointercancel", "blur"].forEach(ev => window.addEventListener(ev, stop));
      };
      b.onclick = e => { if (e.detail === 0) { nudge(r, u, f); drawTab(); } };    // Enter or Space on the button
    });
  }
  function posText(cam) {
    const m = cam && cam.move, how = "For a move by hand: place the camera, ◆ Set start, move it, ◆ Set end.";
    if (!cam) return "Moves a new camera made from this view. " + how;
    if (keyed(cam)) return `Moves the camera at the ${keyAt(cam) === "A" ? "start" : "end"} keyframe.`;
    if (m && m.type === "custom" && m.pending) return "Shaping the end: move the camera to where the move ends, then ◆ Set end.";
    if (m && m.type !== "static") return `Moves where the ${SH.moveInfo(m.type).label.toLowerCase()} starts (its end moves with it).`;
    return "Moves the camera. " + how;
  }
  /* How far a keyframed move travels, in the start's own axes. */
  function travelText(cam) {
    const m = cam && cam.move;
    if (!m || m.type !== "custom" || !m.A) return "";
    const B = m.pending ? cam.A : m.B;
    if (!B) return "";
    const v = SH.travel(m.A, B), f = (x, pos, neg) => Math.abs(x) >= 0.005 ? `${Math.abs(x).toFixed(2)} m ${x > 0 ? pos : neg}` : "";
    const parts = [f(v.right, "right", "left"), f(v.up, "up", "down"), f(v.forward, "in", "out")].filter(Boolean);
    return (m.pending ? "So far from the start: " : "Start → end: ") + (parts.join(" · ") || "the camera stays where it is");
  }

  // ---- subjects
  function inView() {           // the character nearest the middle of the picture (the one the camera looks at)
    const f = new T.Vector3(0, 0, -1).applyQuaternion(view.quaternion);
    let best = null, score = 1e9;
    for (const c of chars) {
      const v = c.getWorldPosition(new T.Vector3()).add(new T.Vector3(0, 1, 0)).sub(view.position), d = v.length();
      const ang = v.normalize().angleTo(f), sc = ang + d * 0.01 + (ang > 0.9 ? 10 : 0);
      if (sc < score) { score = sc; best = c; }
    }
    return best ? OB.keyOf(best, gltf.scene) : null;
  }
  function subjectObj(key) {
    if (!key) return null;
    if (key.startsWith("added:")) return added.children.find(o => o.userData.addedId === key.slice(6)) || null;
    return byKey.get(key) || null;
  }
  const keyFor = o => o.userData.addedId ? "added:" + o.userData.addedId : OB.keyOf(o, gltf.scene);
  function subjectBody(key) {
    const o = subjectObj(key);
    if (!o || !o.visible) return null;
    o.updateWorldMatrix(true, true);
    const box = new T.Box3().setFromObject(o, true);
    if (box.isEmpty()) return null;
    const size = box.getSize(new T.Vector3()), c = box.getCenter(new T.Vector3());
    // bones: three.js numbers repeated names (Head, Head_1, Head_2…), and Mixamo rigs prefix them (mixamorig:Head)
    const bone = re => { let b = null; o.traverse(x => { if (!b && x.isBone && re.test(x.name.replace(/^mixamorig\d*:?/i, "").replace(/_\d+$/, ""))) b = x; }); return b; };
    const wp = b => b.getWorldPosition(new T.Vector3());
    const hips = bone(/^(hips|pelvis)$/i), head = bone(/^head$/i), front = bone(/^head_?front$/i);
    const feet = [["LeftFoot", "LeftToeBase"], ["RightFoot", "RightToeBase"]].map(([f, t]) => [bone(new RegExp("^" + f + "$", "i")), bone(new RegExp("^" + t + "$", "i"))]).filter(([f, t]) => f && t);
    let facing;
    if (head && front) facing = wp(front).sub(wp(head));                               // the face's marker
    else if (feet.length) { facing = new T.Vector3(); feet.forEach(([f, t]) => facing.add(wp(t).sub(wp(f)))); }   // where the toes point
    else facing = new T.Vector3(0, 0, 1).applyQuaternion(o.getWorldQuaternion(new T.Quaternion()));   // a glTF character faces +Z
    facing.y = 0; if (facing.lengthSq() < 1e-8) facing.set(0, 0, 1); facing.normalize();
    const hp = hips ? wp(hips) : c;
    const person = OB.isCharacter(o) || !!o.userData.person;
    const hd = head ? wp(head) : null;
    return { feet: [hp.x, box.min.y, hp.z], h: size.y, w: Math.max(size.x, size.z), d: Math.min(size.x, size.z), facing: A3(facing), person, center: A3(c),
      head: hd ? [hd.x, hd.y, hd.z] : o.userData.person ? A3(new T.Vector3(0, 0, 0.02).applyMatrix4(o.matrixWorld).setY(box.max.y - 0.1)) : null };
  }
  function subjectOptions() {
    const list = chars.map(o => ({ key: OB.keyOf(o, gltf.scene), name: OB.niceName(o.name), kind: "person" }))
      .concat(added.children.map(o => ({ key: "added:" + o.userData.addedId, name: o.name, kind: o.userData.person ? "person" : "object" })));
    if (st.sel && !list.some(x => x.key === keyFor(st.sel))) list.push({ key: keyFor(st.sel), name: "Selected: " + (st.sel.name || "object"), kind: "object" });
    [dir.subject, dir.with].forEach(k => { if (k && !list.some(x => x.key === k) && subjectObj(k)) list.push({ key: k, name: subjectObj(k).name, kind: "object" }); });
    return list;
  }
  const subjSel = $(".gv-subject", el), withSel = $(".gv-with", el);
  function drawSubjects() {
    const list = subjectOptions(), opt = (x, v) => `<option value="${esc(x.key)}" ${x.key === v ? "selected" : ""}>${esc(x.name)}</option>`;
    subjSel.innerHTML = `<option value="">— none —</option>` + list.map(x => opt(x, dir.subject)).join("");
    withSel.innerHTML = `<option value="">— nobody —</option>` + list.filter(x => x.key !== dir.subject).map(x => opt(x, dir.with)).join("");
  }
  subjSel.onchange = () => { dir.subject = subjSel.value || null; if (dir.with === dir.subject) dir.with = null; saveDir(); drawSubjects(); if (st.tab === "light") setLight({}); };
  withSel.onchange = () => { dir.with = withSel.value || null; saveDir(); };

  /* Frames the directed camera again with its setup (after a tab changed it). */
  function reframe(s, quiet) {
    const cam = s.cam, setup = cam.setup;
    setup.subject = dir.subject; setup.with = dir.with;
    if (!setup.size) setup.size = "MS";
    const a = subjectBody(setup.subject);
    if (!a) { if (!quiet) toast("Pick a subject first (the Subject list above the tabs), or select an object and use it as the subject", true); return false; }
    // a group is the people around the subject, not everyone in the file
    const all = subjectOptions().filter(x => x.kind === "person").map(x => subjectBody(x.key))
      .filter(b => b && Math.hypot(b.feet[0] - a.feet[0], b.feet[2] - a.feet[2]) < GROUP_RADIUS && Math.abs(b.feet[1] - a.feet[1]) < 3);
    if (setup.height === "custom" && setup.camY == null) setup.camY = cam.A ? cam.A.p[1] : a.feet[1] + a.h * 0.9;
    let r;
    try { r = SH.solve(setup, { a, b: subjectBody(setup.with), all, cam: cam.A ? cam.A.p : A3(view.position) }, aspectNum()); }
    catch (e) { toast(e.message, true); return false; }
    if (!["ots", "pov"].includes(setup.type)) clearView(r, [subjectObj(setup.subject), subjectObj(setup.with)]);
    if (setup.pan || setup.tilt) r.pose = SH.aim(r.pose, setup.pan || 0, setup.tilt || 0);     // off the subject, by choice
    setup.free = false;
    cam.A = r.pose; cam.note = r.note; cam.anchor = anchorOf(setup.subject);     // where the subject is now: a move that follows starts here
    commit(s); setMode("cam"); drawTab();
    return true;
  }

  /* A wall between the subject and where the camera should be: the camera stops in front of it and goes wider, so the
     framing stays about the same (as on a real set, where you can't back through the wall). */
  function clearView(r, own) {
    const P = r.pose, aim = new T.Vector3(...P.t), to = new T.Vector3(...P.p).sub(aim), d = to.length();
    if (d < 0.4) return;
    const mine = new Set(); own.filter(Boolean).forEach(o => o.traverse(c => { if (c.isMesh) mine.add(c); }));
    const rc = new T.Raycaster(aim, to.clone().normalize(), 0.05, d);
    const hit = rc.intersectObjects(meshes, false).find(i => shown(i.object) && !mine.has(i.object) && !(i.object.material && i.object.material.transparent));
    if (!hit) return;
    const d2 = Math.max(0.3, hit.distance - 0.3), lens = Math.max(8, Math.round(P.lens * d2 / d));
    P.p = A3(aim.clone().addScaledVector(to.normalize(), d2)); P.lens = lens;
    r.note = `A wall is in the way: ${d2.toFixed(1)} m away at ${lens} mm instead`;
  }

  // ---- moves
  function setMove(s, type) {
    const cam = s.cam, m = SH.moveInfo(type), old = cam.move || {};
    if (!cam.A) cam.A = currentViewPose();
    cam.move = { type, amount: m.amt, dur: m.dur || (old.dur && !SH.moveInfo(old.type).dur ? old.dur : 4), at: old.type ? old.at : Math.round(st.t * 10) / 10, ease: old.ease || "smooth",
      follow: !!old.follow, shake: old.shake || 0, A: cam.A, B: SH.presetEnd(cam.A, type, m.amt) };     // (keyframes: setStartKey)
    if (type === "static" && !old.follow && !old.shake) cam.move = null;
    if (cam.move) {                    // the camera's shot stays: the new move goes in it (held still, it is the shot)
      if (old.type) cam.move.shot = SH.shotOf(old);
      if (type === "static") { const [a, b] = SH.shotOf(cam.move); Object.assign(cam.move, { at: a, dur: b - a }); }
      SH.setKeyTimes(cam.move, null, null);
    }
    if (cam.move && (cam.move.follow || cam.move.shake) && type === "static") cam.move.B = cam.A;
    cam.anchor = cam.anchor || anchorOf(cam.setup && cam.setup.subject);
    afterMove(s);
  }
  function afterMove(s) {
    const m = s.cam.move;
    if (m && m.type !== "custom" && m.type !== "static") m.B = SH.presetEnd(m.A || s.cam.A, m.type, m.amount);
    if (m && m.type === "static" && !m.follow && !m.shake) s.cam.move = null;     // still, not following, not handheld: no move
    saveDir(); timeline(); setMode("cam"); drawTab(); dirty = true;
  }
  const pendingMove = m => !!(m && m.type === "custom" && m.pending);
  /* How long the camera's shot lasts (Moves › Shot length): any length, dragged or typed. A move that filled the shot
     still does; keyframes placed inside it stay where they are. A timeline standing on the shot's end stays on it. While
     the end keyframe is being shaped, it's the move's length, and ◆ Set end keeps it. */
  function setLength(s, v) {
    const m = s.cam.move, d = seconds(v);
    if (!m || d == null) return null;
    if (pendingMove(m)) { m.dur = d; m.durSet = true; }
    else {
      const onEnd = Math.abs(st.t - SH.shotOf(m)[1]) <= 1 / fps + 1e-6;
      SH.setShotLength(m, d);
      if (onEnd) setTime(SH.shotOf(m)[1]);
    }
    const [lo, hi] = span();
    if (st.t > hi || st.t < lo) setTime(Math.min(Math.max(st.t, lo), hi));
    st.editing = null; timeline(); dirty = true; saveDir();
    return d;
  }
  /* Where the shot starts on the timeline (typed, or ⏱ Now): its move goes along; the timeline goes there. */
  function setStart(s, v) {
    const m = s.cam.move, a = seconds(v, 0);
    if (!m || a == null) return null;
    if (pendingMove(m)) m.at = a; else SH.moveShot(m, a);
    st.editing = null;
    play(false); afterMove(s); setTime(a); timeline();
    return a;
  }
  /* Where the move's start and end keyframes are in the shot (Moves › Move, typed, or dragged in ◆ Keys). */
  function setKeys(s, a, b) {
    const m = s.cam.move;
    if (!m || pendingMove(m)) return;
    SH.setKeyTimes(m, a, b);
    st.editing = null; play(false); timeline(); dirty = true; saveDir();
  }
  function playMove(s) {             // the camera's whole shot: the stillness before and after its move too
    const m = s.cam.move; if (!m) return;
    const [a, b] = pendingMove(m) ? [m.at || 0, (m.at || 0) + (m.dur || 0)] : SH.shotOf(m);
    pick(s); setTime(a); play(true, b);
  }

  // ---- lighting
  const rigMarks = new T.Group(); rigMarks.visible = false; scene.add(rigMarks);
  let flickers = [];
  function lightParams() { return dir.light ? { ...LI.presetParams(dir.light.preset || "neutral"), ...dir.light } : LI.presetParams("neutral"); }
  function lightCtx() {
    const a = subjectBody(dir.subject);
    const cam = st.mode === "cam" && st.cur ? (() => { poseOf(st.cur, tmpCam.position, tmpCam.quaternion); return A3(tmpCam.position); })() : A3(view.position);
    if (a) return { at: [a.feet[0], a.feet[1] + a.h * 0.62, a.feet[2]], cam, size: a.h, facing: a.facing };
    const fwd = new T.Vector3(0, 0, -1).applyQuaternion(view.quaternion), hit = rayHit(view.position, fwd);
    return { at: A3(view.position.clone().addScaledVector(fwd, hit ? hit.distance : 5)), cam, size: 3 };
  }
  /* The scene's light: the preset placed around the subject from where the camera is now (or a layout a keyframe kept). */
  function applyLight(layout) {
    const P = lightParams();
    if (!layout) { const ctx = lightCtx(); layout = LI.serial(LI.layout(P, ctx), P, ctx); }
    st.sceneLight = layout; st.lightKey = null;
    buildRig(layout);
  }
  const colorOf = c => new T.Color(c[0], c[1], c[2]);
  const shapeOf = L => L.lights.map(l => (l.point ? "p" : "s") + (l.shadow ? "!" : "")).join("") + (L.sun ? "+sun" + (L.sun.shadow ? "!" : "") : "");
  let rigLights = [], rigSun = null, rigShape = "";
  function buildRig(L) {
    for (const o of [...rig.children]) { rig.remove(o); if (o.shadow && o.shadow.map) o.shadow.map.dispose(); if (o.dispose) o.dispose(); }
    for (const o of [...rigMarks.children]) { rigMarks.remove(o); o.geometry && o.geometry.dispose(); }
    rigLights = []; rigSun = null; flickers = [];
    for (const l of L.lights) {
      const light = l.point ? new T.PointLight(0xffffff, 0, 0, 2) : new T.SpotLight(0xffffff, 0, 0, l.wide ? 0.9 : 0.5, 0.6, 2);
      if (!l.point) rig.add(light.target);
      if (l.shadow) { light.castShadow = true; light.shadow.mapSize.set(2048, 2048); light.shadow.bias = -0.0004; light.shadow.normalBias = 0.02; light.shadow.camera.near = 0.1; }
      rig.add(light);
      const mk = new T.Mesh(new T.SphereGeometry(0.09, 10, 8), new T.MeshBasicMaterial());
      const line = new T.Line(new T.BufferGeometry().setFromPoints([new T.Vector3(), new T.Vector3()]), new T.LineBasicMaterial({ transparent: true, opacity: 0.35 }));
      rigMarks.add(mk, line);
      const r = { light, mk, line, base: 0, flicker: l.flicker };
      if (l.flicker) flickers.push(r);
      rigLights.push(r);
    }
    if (L.sun) {
      rigSun = new T.DirectionalLight(0xffffff, 0);
      if (L.sun.shadow) { rigSun.castShadow = true; rigSun.shadow.mapSize.set(2048, 2048); rigSun.shadow.bias = -0.0005; rigSun.shadow.normalBias = 0.03; }
      rig.add(rigSun, rigSun.target);
    }
    rigShape = shapeOf(L);
    updateRig(L);
  }
  /* Sets the rig to a layout: in place when it has the same lights (a keyframe blend, every frame), else rebuilt. */
  function updateRig(L) {
    if (shapeOf(L) !== rigShape) return buildRig(L);
    L.lights.forEach((l, k) => {
      const r = rigLights[k], cd = l.lvl * l.dist * l.dist;        // candela for this level at the subject
      r.light.color.copy(colorOf(l.color)); r.light.intensity = cd; r.base = cd;
      r.light.position.set(...l.pos);
      if (!l.point) r.light.target.position.set(...l.target);
      r.mk.position.set(...l.pos); r.mk.material.color.copy(colorOf(l.color));
      r.line.geometry.setFromPoints([new T.Vector3(...l.pos), new T.Vector3(...l.target)]); r.line.material.color.copy(colorOf(l.color));
    });
    if (rigSun && L.sun) {
      const at = L.at, rr = Math.max(8, (L.size || 2) * 5);
      rigSun.color.copy(colorOf(L.sun.color)); rigSun.intensity = L.sun.lvl;
      rigSun.position.set(at[0] + L.sun.dir[0] * 60, at[1] + L.sun.dir[1] * 60, at[2] + L.sun.dir[2] * 60); rigSun.target.position.set(...at);
      Object.assign(rigSun.shadow.camera, { left: -rr, right: rr, top: rr, bottom: -rr, near: 1, far: 200 }); rigSun.shadow.camera.updateProjectionMatrix();
    }
    hemi.intensity = L.hemi; hemi.color.copy(colorOf(L.sky)); hemi.groundColor.copy(colorOf(L.ground));
    scene.environmentIntensity = L.env;
    scene.background = L.bg != null ? new T.Color(L.bg) : BG.clone();
    for (const f of fileLights) { f.light.visible = L.file; f.light.intensity = f.base * FILE_LIGHT_SCALE * (L.fileScale || 1); }
    renderer.toneMappingExposure = L.exposure || 1;
    dirty = true;
  }
  /* Through a Studio camera with two keyframes, the light is the blend of theirs at this moment; elsewhere, the scene's. */
  function animateLight() {
    const s = st.mode === "cam" && st.cur && st.cur.kind === "studio" ? st.cur : null, m = s && keyed(s.cam) ? s.cam.move : null;
    if (m && m.A.light && m.B.light) {
      const k = s.id + ":" + st.t;
      if (k === st.lightKey) return;
      st.lightKey = k; st.lightAnim = true;
      updateRig(LI.blend(m.A.light, m.B.light, SH.ease(Math.max(0, Math.min(1, (st.t - (m.at || 0)) / Math.max(0.01, m.dur || 1))), m.ease)));
    } else if (st.lightAnim) { st.lightAnim = false; st.lightKey = null; updateRig(st.sceneLight); }
  }
  function setLight(patch, fresh) {
    const s = st.cur && st.cur.kind === "studio" && st.mode === "cam" ? st.cur : null;
    if (s) beginEdit(s);
    dir.light = fresh ? { ...LI.presetParams(patch.preset), ...(fresh === "keepFile" ? { file: lightParams().file, fileScale: lightParams().fileScale, exposure: lightParams().exposure } : {}) } : { ...lightParams(), ...patch };
    if (fresh) Object.assign(dir.light, patch);
    applyLight();
    if (s && s.cam.move && s.cam.move.type === "custom") commit(s); else saveDir();
  }

  // ---- objects
  function setTRS(o, m) { if (m.p) o.position.fromArray(m.p); if (m.q) o.quaternion.fromArray(m.q); if (m.s) o.scale.fromArray(m.s); o.updateMatrixWorld(true); }
  function addObject(rec, fresh) {
    let o;
    if (rec.kind === "prop") {
      o = new T.Group();
      const pr = props.find(p => p.id === rec.prop);
      if (pr) loader.load(pr.url, g => {
        if (!o.parent) return;          // deleted while it loaded
        g.scene.traverse(c => { if (c.isMesh) { c.castShadow = c.receiveShadow = true; c.userData.isAdded = true; meshes.push(c); origMat.set(c, c.material); } });
        o.add(g.scene); dirty = true; if (st.sel === o) select(o);
      },
        undefined, () => toast(`Couldn't load the prop ${pr.name}`, true));
    } else o = OB.build(rec.kind, rec.color);
    o.name = rec.name; o.userData.addedId = rec.id; o.userData.kind = rec.kind;
    if (rec.hidden) o.visible = false;
    setTRS(o, rec);
    added.add(o);
    o.traverse(c => { if (c.isMesh) { meshes.push(c); origMat.set(c, c.material); c.userData.isAdded = true; } });
    if (fresh) { applyLook(); select(o); }
    dirty = true;
    return o;
  }
  function placeFor() {          // on the floor in the middle of the view, never on someone's head
    view.updateMatrixWorld();
    const f = new T.Vector3(0, 0, -1).applyQuaternion(view.quaternion), ground = o => !o.userData.isAdded && !charMeshes.has(o);
    const hit = rayHit(view.position, f, ground), a = subjectBody(dir.subject);
    const floorAt = ndcY => {     // the floor where this height of the picture meets it
      const rc = new T.Raycaster(); rc.setFromCamera(new T.Vector2(0, ndcY), view);
      const h2 = rc.intersectObjects(meshes, false).find(i => shown(i.object) && ground(i.object));
      return h2 && h2.distance < 15 && h2.face && h2.face.normal.clone().transformDirection(h2.object.matrixWorld).y > 0.7 ? h2.point.clone() : null;
    };
    // the floor in the lower part of the picture (it stands in view, its handles too), else where the view meets the floor
    let p = floorAt(-0.55) || floorAt(-0.3) || floorAt(0);
    if (!p && hit && hit.distance < 8 && hit.face && hit.face.normal.clone().transformDirection(hit.object.matrixWorld).y > 0.7) p = hit.point.clone();
    if (!p) {     // about as far as the subject (or 4 m), before any wall, then down to the floor
      const want = a ? view.position.distanceTo(new T.Vector3(a.feet[0], view.position.y, a.feet[2])) : 4;
      const D = Math.max(1.5, Math.min(want, 8, hit ? hit.distance - 0.6 : 1e9));
      p = view.position.clone().addScaledVector(f, D);
      const downHit = rayHit(p.clone().add(new T.Vector3(0, 0.3, 0)), new T.Vector3(0, -1, 0), ground);
      if (downHit) p = downHit.point.clone(); else p.y = a ? a.feet[1] : bounds ? Math.max(bounds.min.y, 0) : 0;
    }
    // not inside someone or something: step to the side until there's room
    const taken = chars.filter(c => shown(c)).concat(added.children.filter(o => o.visible)).map(o => {
      let hp = null; o.traverse(c => { if (!hp && c.isBone && /hips|pelvis/i.test(c.name)) hp = c; });
      return (hp || o).getWorldPosition(new T.Vector3());
    });
    // …and in sight: not behind someone or something already there (from the camera, a stand-in must be seen)
    const seen = q => {
      const to = q.clone().add(new T.Vector3(0, 0.6, 0)).sub(view.position), d = to.length();
      const h3 = new T.Raycaster(view.position, to.normalize(), 0.05, d).intersectObjects(meshes, false).find(i => shown(i.object));
      return !h3 || ground(h3.object) || h3.distance > d - 0.3;
    };
    const free = q => taken.every(t => Math.hypot(q.x - t.x, q.z - t.z) >= 0.9 || Math.abs(q.y - t.y) > 2.5) && seen(q);
    if (!free(p)) {
      const right = new T.Vector3().crossVectors(f, new T.Vector3(0, 1, 0)).setY(0).normalize();
      const spot = [1.1, -1.1, 2.2, -2.2, 3.3, -3.3].map(k => p.clone().addScaledVector(right, k)).find(free);
      if (spot) { p = spot; const d2 = rayHit(p.clone().add(new T.Vector3(0, 1, 0)), new T.Vector3(0, -1, 0), ground); if (d2) p.y = d2.point.y; }
    }
    // faces the subject when there's one nearby (ready for a dialogue scene), else the camera
    const look = a && Math.hypot(p.x - a.feet[0], p.z - a.feet[2]) < 6 ? new T.Vector3(a.feet[0], p.y, a.feet[2]) : view.position.clone();
    const toward = look.sub(p); toward.y = 0;
    return { p: r3(A3(p)), q: new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 1, 0), Math.atan2(toward.x, toward.z)).toArray().map(v => Math.round(v * 1e5) / 1e5) };
  }
  function addKind(kind, extra = {}) {
    // looking through a camera, you stay in it: the new object goes where that camera sees it (an orthographic one: from
    // the free camera, which is where its handles work)
    if (st.mode === "cam" && st.cur && st.cur.kind === "ortho") explore();
    const pl = placeFor(), n = dir.added.filter(a => a.kind === kind).length + 1;
    const info = OB.ADDABLE.find(a => a.id === kind);
    const rec = { id: uid("o"), kind, name: `${extra.name || (info ? info.label : "Object")} ${n}`, color: info ? info.color : null, p: pl.p, q: pl.q, s: [1, 1, 1], ...extra };
    rec.name = `${extra.name || (info ? info.label : "Object")} ${n}`;
    dir.added.push(rec); const o = addObject(rec, true); saveDir();
    if (o.userData.person && !dir.subject) { dir.subject = "added:" + rec.id; }
    drawSubjects(); drawTab();
  }
  const isAdded = o => !!(o && o.userData.addedId);
  const movable = o => isAdded(o) || (o && !animatedObjs.has(o));
  function select(o) {
    st.sel = o || null;
    if (selBox) { scene.remove(selBox); selBox.geometry.dispose(); selBox = null; }
    if (o) { selBox = new T.BoxHelper(o, 0xffc43d); scene.add(selBox); }
    if (o && movable(o)) tc.attach(o); else tc.detach();
    dirty = true;
    if (st.tab === "objects") drawTab();
    drawSubjects();
  }
  let selBox = null;
  function objectMoved() {
    const o = st.sel; if (!o) return;
    const m = { p: r3(o.position.toArray()), q: o.quaternion.toArray().map(v => Math.round(v * 1e5) / 1e5), s: r3(o.scale.toArray()) };
    if (isAdded(o)) Object.assign(dir.added.find(a => a.id === o.userData.addedId) || {}, m);
    else dir.moved[OB.keyOf(o, gltf.scene)] = m;
    saveDir();
  }
  /* Del: deletes what you added, hides what's in the file. H (hideOnly): hides either. */
  function removeSel(hideOnly) {
    const o = st.sel; if (!o) return;
    if (isAdded(o) && !hideOnly) {
      const id = o.userData.addedId; dir.added = dir.added.filter(a => a.id !== id);
      if (dir.subject === "added:" + id) dir.subject = null; if (dir.with === "added:" + id) dir.with = null;
      select(null); added.remove(o);
      o.traverse(c => { if (c.isMesh) { const i = meshes.indexOf(c); if (i >= 0) meshes.splice(i, 1); origMat.delete(c); c.geometry.dispose(); } });
    } else { setHidden(o, true); select(null); }
    saveDir(); drawSubjects(); drawTab();
  }
  /* Hides or shows an object, and keeps it that way: a file's object in dir.hidden, an added one on its record. */
  function setHidden(o, hide) {
    o.visible = !hide;
    if (isAdded(o)) {
      const rec = dir.added.find(a => a.id === o.userData.addedId);
      if (rec) { if (hide) rec.hidden = true; else delete rec.hidden; }
    } else {
      const k = OB.keyOf(o, gltf.scene);
      dir.hidden = dir.hidden.filter(x => x !== k); if (hide) dir.hidden.push(k);
    }
    dirty = true; saveDir();
  }
  const hiddenCount = () => dir.hidden.length + dir.added.filter(a => a.hidden).length;
  const shown = o => { for (let x = o; x; x = x.parent) if (!x.visible) return false; return true; };
  function rayHit(origin, dirv, only) {
    const rc = new T.Raycaster(origin, dirv.clone().normalize(), 0.05, 1000);
    return rc.intersectObjects(meshes, false).find(i => shown(i.object) && (!only || only(i.object))) || null;
  }
  /* What's under the pointer: { hit, o } where o is what a click selects (a whole stand-in or prop, a whole character). */
  function objectAt(x, y) {
    if (!gltf) return null;
    const r = canvas.getBoundingClientRect(), p = new T.Vector2((x - r.left) / r.width * 2 - 1, -(y - r.top) / r.height * 2 + 1);
    const cam = st.mode === "cam" && st.cur && st.cur.kind === "ortho" ? oview : view, rc = new T.Raycaster();
    cam.updateMatrixWorld();                 // where it is now, even before the next frame is drawn
    rc.setFromCamera(p, cam);
    return ownerOf(rc.intersectObjects(meshes, false).find(i => shown(i.object)));
  }
  function ownerOf(hit) {
    if (!hit) return null;
    let o = hit.object;
    for (let x2 = o; x2; x2 = x2.parent) { if (x2.userData.addedId) { o = x2; break; } }
    if (!isAdded(o)) { const ch = chars.find(c => { let y = false; c.traverse(z => { if (z === hit.object) y = true; }); return y; }); if (ch) o = ch; }
    return { hit, o };
  }
  function pickAt(x, y) {
    const at = objectAt(x, y);
    if (!at) { select(null); return; }
    select(at.o);
    if (st.tab !== "objects") { st.tab = "objects"; drawTabs(); drawTab(); }
  }
  /* Worth framing on a double-click: what you added, a character, or a piece of the set that isn't most of it (the floor
     or the walls of a room set the pivot instead). */
  function frameable(o) {
    if (isAdded(o) || chars.includes(o)) return true;
    const b = new T.Box3().setFromObject(o, true);
    return !b.isEmpty() && !!bounds && b.getSize(new T.Vector3()).length() < 0.35 * bounds.getSize(new T.Vector3()).length();
  }
  /* Puts an object in frame in the free camera (from where you look now, as close as its size allows), selected, its
     handles ready to move it. From a camera, you leave it to look around (↩ Back or the camera list goes back). */
  function focusOn(o) {
    const box = new T.Box3().setFromObject(o, true);
    if (box.isEmpty()) return;
    if (st.mode === "cam") explore();
    select(o);
    if (st.tab !== "objects") { st.tab = "objects"; drawTabs(); drawTab(); }
    const c = box.getCenter(new T.Vector3()), rad = Math.max(0.3, box.getSize(new T.Vector3()).length() / 2);
    const fitAt = lens => { const v = SH.vfov(lens, view.aspect), hz = SH.hfov(lens, view.aspect); return rad / Math.sin(Math.min(v, hz) / 2) * 1.1; };
    let d = fitAt(st.freeLens);
    const back = view.position.clone().sub(controls.target);
    if (back.lengthSq() < 1e-8) back.set(0, 0.35, 1);
    back.normalize();
    if (back.y < 0.12) { back.y = 0.12; back.normalize(); }           // a little from above, never from under the floor
    // somewhere it can be seen from: a wall (or anything) between it and the camera would be all you see. From where you
    // look now, else the nearest way round it that's clear; in a tight spot, as far as there's room, with a wider lens.
    const mine = new Set(); o.traverse(x => { if (x.isMesh) mine.add(x); });
    const room = dirv => {
      const h = new T.Raycaster(c, dirv, 0.01, d + 0.5).intersectObjects(meshes, false)
        .find(i => shown(i.object) && !mine.has(i.object) && !(i.object.material && i.object.material.transparent));
      return h ? h.distance : Infinity;
    };
    let best = null;
    for (const deg of [0, 30, -30, 60, -60, 90, -90, 120, -120, 150, -150, 180]) {
      const dv = back.clone().applyAxisAngle(new T.Vector3(0, 1, 0), T.MathUtils.degToRad(deg)), free = room(dv);
      if (free > d + 0.3) { best = { dv, free }; break; }
      if (!best || free > best.free) best = { dv, free };
    }
    if (best.free <= d + 0.3) {                                         // nowhere far enough: closer, and wider to fit
      const far = Math.max(0.4, best.free - 0.3);
      let lens = st.freeLens;
      while (lens > 10 && fitAt(lens) > far) lens = Math.max(10, Math.floor(lens * 0.85));
      if (lens !== st.freeLens) { st.freeLens = lens; lensIn.value = lens; lensV.textContent = lens + " mm"; view.setFocalLength(lens); drawHud(); }
      d = Math.min(fitAt(lens), far);
    }
    st.fly = { p0: view.position.clone(), t0: controls.target.clone(), p1: c.clone().addScaledVector(best.dv, d), t1: c, start: performance.now(), dur: 450 };
    dirty = true;
  }


  // ---- side panel: tabs
  function drawTabs() { $$(".gv-tabs button", el).forEach(b => { const on = b.dataset.tab === st.tab; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); }); }
  $$(".gv-tabs button", el).forEach(b => b.onclick = () => { st.tab = b.dataset.tab; drawTabs(); drawTab(); state(); });
  function drawSide() { drawCamSel(); drawSubjects(); drawTabs(); drawTab(); }
  const chip = (v, label, on, title, small) => `<button class="gvc ${on ? "on" : ""}" data-v="${esc(v)}" title="${esc(title || "")}">${label}${small ? `<small>${esc(small)}</small>` : ""}</button>`;
  function drawTab() {
    if (st.quiet) return;              // a slider is being dragged: its tab stays as it is until it's let go
    st.stale = false;
    const y = body.scrollTop;
    drawTabBody();
    body.scrollTop = y;                // drawn again, where you were in it
  }
  /* A double-click on a row of the list: the first click selects, which opens the Selected box above the list and moves
     the rows down, so the second lands on something else (a swatch, a button). It's caught here first, and brings what
     the first click picked into frame. */
  body.addEventListener("click", e => {
    const c = st.rowClick;
    if (!c || performance.now() - c.t > 450 || Math.hypot(e.clientX - c.x, e.clientY - c.y) > 8) return;
    e.stopPropagation(); e.preventDefault(); st.rowClick = null;
    if (c.o.visible) focusOn(c.o);
  }, true);
  function drawTabBody() {
    const s = st.cur && st.cur.kind === "studio" ? st.cur : null, cam = s ? s.cam : null, setup = cam ? cam.setup || {} : {};
    const tab = st.tab;
    body.innerHTML = "";
    if (tab === "frame") {
      body.append(h(`<div>
        <div class="gv-sec"><label class="lbl">Shot size</label><div class="gv-grid2" data-k="size">${SH.SHOT_SIZES.map(x => chip(x.id, `<b>${x.id}</b>`, setup.size === x.id, x.hint, x.label)).join("")}</div></div>
        <div class="gv-sec"><label class="lbl">Shot</label><div class="gv-chips" data-k="type">${SH.SHOT_TYPES.map(x => chip(x.id, x.label, (setup.type || "single") === x.id && !!cam, x.hint)).join("")}</div></div>
        <div class="gv-sec"><label class="lbl">Camera side</label><div class="gv-chips" data-k="side">${SH.SIDES.map(x => chip(x.deg, x.label, setup.side === x.deg, "Where the camera stands around the subject")).join("")}${chip("rev", "⇄ Reverse", false, "The reverse angle: the other side of the subject (or the other person's shoulder)")}</div></div>
        <div class="gv-sec"><label class="lbl">Lens</label><div class="gv-chips" data-k="lens">${SH.LENSES.map(l => chip(l, l + " mm", cam && Math.round(cam.A ? cam.A.lens : 0) === l)).join("")}</div></div>
        <div class="row gv-foot"><button class="ghost sm" data-reframe title="The subject moved? Frame it again where it is now">⟳ Frame again now</button><span class="hint">${cam && cam.note ? esc(cam.note) : ""}</span></div>
        ${cam ? "" : `<p class="hint">Pick a size: Studio makes a camera from this view and frames the subject.</p>`}</div>`));
      const go = patch => { const t = directed(); beginEdit(t); Object.assign(t.cam.setup = t.cam.setup || {}, patch); reframe(t); };
      $$("[data-k=size] button", body).forEach(b => b.onclick = () => go({ size: b.dataset.v, type: ["pov"].includes(setup.type) ? "single" : setup.type }));
      $$("[data-k=type] button", body).forEach(b => b.onclick = () => go({ type: b.dataset.v, ...(b.dataset.v === "ots" && !setup.size ? { size: "MCU" } : {}) }));
      $$("[data-k=side] button", body).forEach(b => b.onclick = () => {
        if (b.dataset.v === "rev") {
          if (setup.type === "ots" && dir.with) { const a = dir.subject; dir.subject = dir.with; dir.with = a; drawSubjects(); return go({}); }
          const cur = setup.side ?? (() => { const a = subjectBody(dir.subject), c = cam && cam.A ? cam.A.p : A3(view.position); if (!a) return 0; const v = [c[0] - a.feet[0], c[2] - a.feet[2]]; return Math.round(Math.atan2(v[0] * a.facing[2] - v[1] * a.facing[0], v[0] * a.facing[0] + v[1] * a.facing[2]) / Math.PI * 180); })();
          return go({ side: ((cur + 360) % 360) - 180 });
        }
        go({ side: +b.dataset.v });
      });
      $$("[data-k=lens] button", body).forEach(b => b.onclick = () => go({ lens: +b.dataset.v }));
      $("[data-reframe]", body).onclick = () => { if (s) { beginEdit(s); reframe(s); } else toast("Pick a shot size first", true); };
    } else if (tab === "height") {
      body.append(h(`<div>
        <div class="gv-sec"><label class="lbl">Camera height</label><div class="gv-grid2" data-k="height">${SH.HEIGHTS.slice(0, 6).map(x => chip(x.id, x.label, setup.height === x.id, x.hint)).join("")}</div></div>
        <div class="gv-sec"><label class="lbl">Angle</label><div class="gv-grid2" data-k="height">${SH.HEIGHTS.slice(6).map(x => chip(x.id, x.label, setup.height === x.id, x.hint)).join("")}</div></div>
        <div class="gv-sec"><label class="lbl">Dutch angle</label><div class="gv-chips" data-k="roll">${chip(0, "Level", cam && !setup.roll)}${chip(-15, "Dutch left", setup.roll === -15, "The horizon tilts: unease")}${chip(15, "Dutch right", setup.roll === 15, "The horizon tilts: unease")}</div>
          <label class="hint gv-slide">Roll <input type="range" class="gv-roll" min="-45" max="45" step="1" value="${setup.roll || 0}"> <b>${setup.roll || 0}°</b></label></div>
        <div class="gv-sec"><label class="lbl">Aim</label>
          <label class="hint gv-slide" title="Turn the camera left or right: the subject moves off centre">Pan <input type="range" class="gv-pan" min="-90" max="90" step="1" value="${-(setup.pan || 0)}"> <b>${fmtPan(setup.pan || 0)}</b></label>
          <label class="hint gv-slide" title="Turn the camera up or down">Tilt <input type="range" class="gv-tilt" min="-60" max="60" step="1" value="${setup.tilt || 0}"> <b>${fmtTilt(setup.tilt || 0)}</b></label>
          <div class="row gv-wrap"><button class="ghost sm" data-center title="Aim at the subject again (pan and tilt back to 0)">◎ Back on the subject</button><button class="ghost sm ${st.operate ? "on" : ""}" data-operate title="Move the camera by hand, anywhere: drag to pan and tilt, right-drag to slide, wheel in and out, W A S D Q E to move">✥ Operate by hand</button></div>
          ${setup.free ? `<p class="hint">Operated by hand: the framing buttons aim at the subject again.</p>` : ""}</div>
        ${cam ? "" : `<p class="hint">Pick a height: Studio makes a camera from this view (medium shot) and places it.</p>`}</div>`));
      const go = patch => { const t = directed(); beginEdit(t); Object.assign(t.cam.setup = t.cam.setup || {}, patch); reframe(t); };
      $$("[data-k=height] button", body).forEach(b => b.onclick = () => go({ height: b.dataset.v }));
      $$("[data-k=roll] button", body).forEach(b => b.onclick = () => go({ roll: +b.dataset.v }));
      const aimBy = (key, v, out, fmt) => {
        const t = steady(); beginEdit(t);
        const old = t.cam.setup[key] || 0;
        t.cam.setup[key] = v; out.textContent = fmt(v);
        t.cam.A = SH.aim(t.cam.A || currentViewPose(), key === "pan" ? v - old : 0, key === "tilt" ? v - old : 0);
        commit(t); setMode("cam"); drawCamSel(); drawHud();
      };
      const pn = $(".gv-pan", body), tl = $(".gv-tilt", body);
      pn.oninput = () => aimBy("pan", -(+pn.value), pn.nextElementSibling, fmtPan);          // the slider goes left to right
      tl.oninput = () => aimBy("tilt", +tl.value, tl.nextElementSibling, fmtTilt);
      pn.onchange = tl.onchange = redrawIfStale;           // let go: the tab shows the new camera (arrow keys keep the focus)
      $("[data-center]", body).onclick = () => { const t = directed(); beginEdit(t); Object.assign(t.cam.setup, { pan: 0, tilt: 0 }); if (!reframe(t)) drawTab(); };
      $("[data-operate]", body).onclick = () => { setOperate(!st.operate); drawTab(); };
      const rl = $(".gv-roll", body);
      rl.oninput = () => {
        const t = steady(); beginEdit(t); t.cam.setup.roll = +rl.value; rl.nextElementSibling.textContent = rl.value + "°"; if (t.cam.A) t.cam.A.roll = +rl.value; commit(t); setMode("cam"); drawHud();
        $$("[data-k=roll] button", body).forEach(c => c.classList.toggle("on", +c.dataset.v === +rl.value));     // Level / Dutch left / right
      };
      rl.onchange = redrawIfStale;
    } else if (tab === "move") {
      const m = cam && cam.move, info2 = m ? SH.moveInfo(m.type) : null, custom = !!(m && m.type === "custom");
      // two ways to move: by hand (keyframes, placing the camera yourself) or a preset move
      const mv = st.moveView || (m && !custom && m.type !== "static" ? "preset" : "manual");
      const groups = [["Dolly", ["push", "pull", "vertigo_in", "vertigo_out"]], ["Track & crane", ["truck_l", "truck_r", "ped_up", "ped_down", "crane_up", "crane_down"]],
        ["Turn", ["pan_l", "pan_r", "tilt_u", "tilt_d", "whip_l", "whip_r"]], ["Around & lens", ["arc_l", "arc_r", "zoom_in", "zoom_out"]], ["", ["static"]]];
      const nb = (v, label, tip) => `<button class="gvc" data-nudge="${v}" title="${esc(tip)}">${label}</button>`;
      const pad = `<div class="gv-sec gv-pos"><div class="lblrow"><label class="lbl">Camera position</label>
          <label class="hint gv-num" title="How far one press moves the camera (hold a button to keep moving)">Step <select class="gv-stepsel">${NUDGE_STEPS.map(([v, l]) => `<option value="${v}" ${v === st.step ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
        <div class="gv-pad"><div class="gv-dpad">${nb("0,1,0", "↑ Up", "Crane or pedestal up: the camera rises, still looking the same way")}${nb("-1,0,0", "← Left", "Truck left: the camera slides left on its dolly, still looking the same way")}${nb("1,0,0", "Right →", "Truck right: the camera slides right, still looking the same way")}${nb("0,-1,0", "↓ Down", "Crane or pedestal down")}</div>
          <div class="gv-inout">${nb("0,0,1", "In", "Dolly in: the camera travels forward along the floor")}${nb("0,0,-1", "Out", "Dolly out: the camera travels back along the floor")}</div></div>
        ${keyed(cam) ? `<div class="row gv-wrap gv-editkey"><span class="hint">Editing</span>${["A", "B"].map(k => `<button class="gvc ${keyAt(cam) === k ? "on" : ""}" data-key="${k}" title="Go to the ${k === "A" ? "start" : "end"} keyframe: the buttons above (and every tab) change it">◆ ${k === "A" ? "Start" : "End"}</button>`).join("")}</div>` : ""}
        <p class="hint gv-posk">${esc(posText(cam))}</p></div>`;
      // timing: a finished move's shot (what the camera plays) and its keyframes in it; a move being shaped, its length
      const shaping = pendingMove(m), [sh0, sh1] = m ? (shaping ? [m.at || 0, (m.at || 0) + (m.dur || 0)] : SH.shotOf(m)) : [0, 0], len = sh1 - sh0;
      const num = (cls, v, label, tip) => `<input type="number" class="${cls}" min="0" max="${MOVE_MAX}" step="0.1" value="${+v.toFixed(2)}" aria-label="${label}" title="${tip}">`;
      const timing = m ? `<div class="gv-sec gv-movebox">
          ${mv === "preset" && info2 && info2.unit ? `<label class="hint gv-slide">${info2.unit === "share" ? "Amount" : info2.unit === "°" ? "Angle" : info2.unit === "×" ? "Zoom" : "Distance"} <input type="range" class="gv-amt" min="${info2.min}" max="${info2.max}" step="${info2.unit === "°" ? 1 : 0.05}" value="${m.amount}"> <b>${fmtAmt(m.amount, info2.unit)}</b></label>` : ""}
          <div class="hint gv-slide gv-len" title="${shaping ? "How long the move lasts, in seconds: drag, or type any length" : "How long this camera's shot lasts (what plays, what a movie records), in seconds: drag, or type any length. The move's keyframes stay where they are in it"}"><span>${shaping ? "Length" : "Shot length"}</span><input type="range" class="gv-dur" min="${MOVE_MIN}" max="${Math.max(LEN_SLIDER, Math.ceil(len))}" step="0.1" value="${len}">
            <span class="gv-num"><input type="number" class="gv-durn" min="${MOVE_MIN}" max="${MOVE_MAX}" step="0.1" value="${+len.toFixed(2)}" aria-label="${shaping ? "Length" : "Shot length"} in seconds">s</span></div>
          <div class="row gv-wrap"><label class="hint gv-num">${shaping ? "Starts at" : "Shot starts at"} ${num("gv-atn", sh0, "Start in seconds", shaping ? "Where the move starts on the timeline, in seconds" : "Where the shot starts on the timeline, in seconds (its move goes along)")}s</label><button class="ghost sm" data-at title="Start ${shaping ? "the move" : "the shot"} at the current moment of the timeline">⏱ Now</button></div>
          ${!shaping && m.type !== "static" ? `<div class="row gv-wrap gv-keytimes"><span class="hint" title="When the move happens in the shot: the camera holds still before its start keyframe and after its end">Move</span>
            <label class="hint gv-num">◆ ${num("gv-ka", m.at || 0, "Move start in seconds", "The move's start keyframe, in seconds on the timeline")}s</label><span class="hint">→</span>
            <label class="hint gv-num">◆ ${num("gv-kb", (m.at || 0) + (m.dur || 0), "Move end in seconds", "The move's end keyframe, in seconds on the timeline")}s</label></div>` : ""}
          ${m.type !== "static" ? `<select class="gv-ease" title="How it speeds up and slows down"><option value="smooth">Ease in and out</option><option value="in">Ease in</option><option value="out">Ease out</option><option value="linear">Constant speed</option></select>` : ""}</div>` : "";
      const extras = cam ? `<div class="row gv-wrap"><label class="hint"><input type="checkbox" class="gv-follow" ${m && m.follow ? "checked" : ""}> Follow the subject</label>
            <label class="hint"><input type="checkbox" class="gv-shake" ${m && m.shake ? "checked" : ""}> Handheld</label>
            ${m && m.shake ? `<input type="range" class="gv-shakek" min="0.2" max="3" step="0.1" value="${m.shake}" title="How shaky">` : ""}</div>
          ${m ? `<div class="row gv-wrap">${mv === "preset" && !(custom && m.pending) ? `<button class="ghost sm" data-goA>⏮ Start</button><button class="ghost sm" data-goB>⏭ End</button>` : ""}${custom && m.pending ? "" : `<button class="btn sm primary" data-playm>▶ Play the move</button>`}<button class="ghost sm" data-reset-move title="Take this camera's move away: it goes back to where the move starts (position, framing, lens, light) and stays still">↺ Reset move</button></div>` : ""}` : "";
      body.append(h(`<div>
        <div class="gv-seg" role="tablist"><button class="${mv === "manual" ? "on" : ""}" data-mv="manual" title="Place the camera yourself at the start and at the end (a dolly or crane move by hand), or change anything between two keyframes">✥ Manual move</button><button class="${mv === "preset" ? "on" : ""}" data-mv="preset" title="Push in, truck, crane, pan, arc, zoom…">⇢ Preset moves</button></div>
        ${mv === "manual" ? `
          ${m && !custom && m.type !== "static" ? `<p class="hint gv-note">This camera has a preset move (${esc(info2.label)}). ◆ Set start replaces it with your own.</p>` : ""}
          ${pad}
          <div class="gv-sec gv-keys"><label class="lbl">Keyframes</label>
            <div class="row gv-wrap"><button class="ghost sm ${custom ? "on" : ""}" data-setA title="The start: everything as it is now (where the camera is, the framing, lens, height, light)">◆ Set start</button><button class="ghost sm ${keyed(cam) ? "on" : ""}" data-setB title="The end: everything as it is now. Play animates from the start to here">◆ Set end</button></div>
            <p class="hint gv-keytext">${esc(keyText(cam))}</p>${custom ? `<p class="hint gv-travel">${esc(travelText(cam))}</p>` : ""}</div>
          ${custom || (m && m.type === "static") ? timing : ""}${cam ? extras : ""}`
        : `${custom ? `<p class="hint gv-note">This camera has a keyframed move. Picking a preset replaces it.</p>` : ""}
          ${groups.map(([g, ids]) => `<div class="gv-sec">${g ? `<label class="lbl">${g}</label>` : ""}<div class="gv-chips" data-k="move">${ids.map(id => { const x = SH.moveInfo(id); return chip(id, x.label, (m ? m.type : cam ? "static" : "") === id, x.hint); }).join("")}</div></div>`).join("")}
          ${cam ? (custom ? "" : timing) + extras : `<p class="hint">Pick a move: Studio makes a camera from this view and moves it from there.</p>`}
          ${cam && !custom ? pad : ""}`}
      </div>`));
      $$("[data-mv]", body).forEach(b => b.onclick = () => { st.moveView = b.dataset.mv; drawTab(); });
      $$("[data-k=move] button", body).forEach(b => b.onclick = () => setMove(directed(), b.dataset.v));
      bindPad();
      const sA = $("[data-setA]", body); if (sA) sA.onclick = () => setStartKey(directed());
      const sB = $("[data-setB]", body); if (sB) sB.onclick = () => { if (s) setEndKey(s); else toast("Set the start keyframe first", true); };
      if (cam) {
        const amt = $(".gv-amt", body); if (amt) amt.oninput = () => { m.amount = +amt.value; amt.nextElementSibling.textContent = fmtAmt(m.amount, info2.unit); m.B = SH.presetEnd(m.A || cam.A, m.type, m.amount); saveDir(); setMode("cam"); };
        const du = $(".gv-dur", body), dn = $(".gv-durn", body), an = $(".gv-atn", body), ka = $(".gv-ka", body), kb = $(".gv-kb", body), kt = $(".gv-keytext", body);
        /* the timing fields show the move as it is now, in place (the focus stays where it is); `but` is the one being typed in */
        const sync = but => {
          const mm = cam.move; if (!mm) return;
          const [a, b] = pendingMove(mm) ? [mm.at || 0, (mm.at || 0) + (mm.dur || 0)] : SH.shotOf(mm), len = b - a;
          if (du) { if (len > +du.max) du.max = Math.ceil(len); du.value = len; }
          for (const [f, v] of [[dn, len], [an, a], [ka, mm.at || 0], [kb, (mm.at || 0) + (mm.dur || 0)]]) if (f && f !== but) f.value = +v.toFixed(2);
          if (kt) kt.textContent = keyText(cam);
        };
        if (du) {
          du.oninput = () => { if (setLength(s, +du.value) != null) sync(); };
          dn.oninput = () => { if (setLength(s, dn.value) != null) sync(dn); };      // live while typing
          dn.onchange = () => sync();                                                 // then shows what it became
          an.onchange = () => { setStart(s, an.value); };                             // (redraws the tab: its move went along)
        }
        if (ka) ka.onchange = () => { const v = seconds(ka.value, 0); if (v != null) setKeys(s, v, null); sync(); };
        if (kb) kb.onchange = () => { const v = seconds(kb.value, 0); if (v != null) setKeys(s, null, v); sync(); };
        const ea = $(".gv-ease", body); if (ea) { ea.value = m.ease || "smooth"; ea.onchange = () => { m.ease = ea.value; saveDir(); }; }
        const at = $("[data-at]", body); if (at) at.onclick = () => setStart(s, Math.round(st.t * 10) / 10);
        const need = () => cam.move || (setMove(s, "static"), cam.move = cam.move || { type: "static", amount: 0, dur: 4, at: Math.round(st.t * 10) / 10, ease: "smooth", follow: false, shake: 0, A: cam.A, B: cam.A });
        $(".gv-follow", body).onchange = e => { need(); cam.move.follow = e.target.checked; cam.anchor = anchorOf(cam.setup && cam.setup.subject); if (e.target.checked && !cam.anchor) toast("Pick a subject to follow", true); afterMove(s); };
        $(".gv-shake", body).onchange = e => { need(); cam.move.shake = e.target.checked ? 1 : 0; afterMove(s); };
        const sk = $(".gv-shakek", body); if (sk) sk.oninput = () => { m.shake = +sk.value; saveDir(); dirty = true; };
        const rz = $("[data-reset-move]", body); if (rz) rz.onclick = () => resetMove(s);
        const goKey = k => { play(false); setTime((m.at || 0) + (k === "A" ? 0 : m.dur)); pick(s); atKey(); };
        const gA = $("[data-goA]", body); if (gA) gA.onclick = () => goKey("A");
        const gB = $("[data-goB]", body); if (gB) gB.onclick = () => goKey("B");
        $$("[data-key]", body).forEach(b => b.onclick = () => { goKey(b.dataset.key); drawTab(); });
        const pm = $("[data-playm]", body); if (pm) pm.onclick = () => playMove(s);
      }
    } else if (tab === "light") {
      const P = lightParams(), groups = [...new Set(LI.LIGHT_PRESETS.map(p => p.group))], pr = LI.lightInfo(P.preset);
      const sl = (cls, label, min, max, stp, v, out) => `<label class="hint gv-slide">${label} <input type="range" class="${cls}" min="${min}" max="${max}" step="${stp}" value="${v}"> <b>${out}</b></label>`;
      body.append(h(`<div>
        ${groups.map(g => `<div class="gv-sec"><label class="lbl">${g}</label><div class="gv-chips" data-k="light">${LI.LIGHT_PRESETS.filter(p => p.group === g).map(p => chip(p.id, p.label, P.preset === p.id, p.hint)).join("")}</div></div>`).join("")}
        <p class="hint gv-lnote">${esc(pr.hint)}${(pr.lights || pr.sun) ? ` · placed around ${dir.subject && subjectObj(dir.subject) ? esc(OB.niceName(subjectObj(dir.subject).name)) : "the middle of the view"}, from where the camera is now` : ""}</p>
        <div class="gv-sec">
          ${pr.lights || pr.sun ? sl("gv-lkey", "Key", 0.1, 3, 0.05, P.key, "×" + (+P.key).toFixed(2)) : ""}
          ${(pr.lights || []).some(l => l.role === "fill") ? sl("gv-lratio", "Key : fill", 1, 16, 0.5, P.ratio, P.ratio + ":1") : ""}
          ${pr.lights || pr.sun ? sl("gv-lside", "Key side", -180, 180, 5, P.side, P.side + "°") + sl("gv-lheight", "Key height", -60, 60, 5, P.height, P.height + "°") : ""}
          ${pr.lights ? sl("gv-ltemp", "Colour", 1800, 10000, 100, P.temp, P.temp + " K") : ""}
          ${sl("gv-lamb", "Ambient", 0, 1.5, 0.01, P.ambient, (+P.ambient).toFixed(2))}
          ${sl("gv-lexp", "Exposure", 0.2, 3, 0.05, P.exposure, (+P.exposure).toFixed(2))}
          <div class="row gv-wrap"><label class="hint"><input type="checkbox" class="gv-lshadow" ${P.shadows ? "checked" : ""}> Shadows</label>
            ${fileLights.length ? `<label class="hint"><input type="checkbox" class="gv-lfile" ${P.file ? "checked" : ""}> The set's ${fileLights.length} lights</label>` : ""}</div>
          ${fileLights.length && P.file ? sl("gv-lfscale", "Set lights", 0.05, 5, 0.05, P.fileScale, "×" + (+P.fileScale).toFixed(2)) : ""}
          <div class="row gv-wrap"><button class="ghost sm" data-relight title="Place the lights again around the subject, from the camera you look through now">⟳ Light from this camera</button></div>
        </div></div>`));
      $$("[data-k=light] button", body).forEach(b => b.onclick = () => { setLight({ preset: b.dataset.v }, b.dataset.v === "set" ? true : "keepFile"); drawTab(); });
      const bind = (cls, key, fmt) => { const i = $("." + cls, body); if (i) i.oninput = () => { setLight({ [key]: +i.value }); i.nextElementSibling.textContent = fmt(+i.value); }; };
      bind("gv-lkey", "key", v => "×" + v.toFixed(2)); bind("gv-lratio", "ratio", v => v + ":1"); bind("gv-lside", "side", v => v + "°"); bind("gv-lheight", "height", v => v + "°");
      bind("gv-ltemp", "temp", v => v + " K"); bind("gv-lamb", "ambient", v => v.toFixed(2)); bind("gv-lexp", "exposure", v => v.toFixed(2)); bind("gv-lfscale", "fileScale", v => "×" + v.toFixed(2));
      $(".gv-lshadow", body).onchange = e => setLight({ shadows: e.target.checked });
      const lf = $(".gv-lfile", body); if (lf) lf.onchange = e => { setLight({ file: e.target.checked }); drawTab(); };
      $("[data-relight]", body).onclick = () => setLight({});
    } else if (tab === "objects") {
      const o = st.sel, rec = o && isAdded(o) ? dir.added.find(a => a.id === o.userData.addedId) : null;
      body.append(h(`<div>
        <div class="gv-sec"><label class="lbl">Add</label><div class="gv-chips" data-k="add">${OB.ADDABLE.map(a => chip(a.id, a.label, false, a.hint)).join("")}${chip("_prop", "Prop (.glb)…", false, "Any .glb model: a chair, a car, a character")}</div>
          ${props.length ? `<div class="gv-chips" data-k="prop" style="margin-top:6px">${props.map(p => chip(p.id, "◆ " + esc(p.name), false, "Add this prop again")).join("")}</div>` : ""}
          <input type="file" hidden accept=".glb,model/gltf-binary" class="gv-propfile"></div>
        <div class="gv-sec gv-selbox">${o ? `<label class="lbl">Selected</label><div class="row gv-wrap"><b class="gv-selname">${esc(o.name || "(no name)")}</b><span class="hint">${rec ? "added" : o.userData.addedId ? "" : animatedObjs.has(o) ? "from the set · animated, can't be moved" : chars.includes(o) ? "character from the set" : "from the set"}</span></div>
          <div class="row gv-wrap">${movable(o) ? `<button class="ghost sm ${tc.mode === "translate" ? "on" : ""}" data-tm="translate" title="Move (M)">✥ Move</button><button class="ghost sm ${tc.mode === "rotate" ? "on" : ""}" data-tm="rotate" title="Turn (R)">⟳ Turn</button><button class="ghost sm ${tc.mode === "scale" ? "on" : ""}" data-tm="scale" title="Size">⤡ Size</button>` : ""}
            <button class="ghost sm" data-focus title="Bring it into frame in the free camera, to move it (or double-click it)">⌖ Frame it</button><button class="ghost sm" data-subj title="Frame and light this">◎ Use as subject</button>${rec ? `<button class="ghost sm" data-ren>✎</button>` : ""}<button class="ghost sm danger" data-rm title="${rec ? "Delete (Del)" : "Hide (Del or H)"}">${rec ? "✕ Delete" : "◌ Hide"}</button></div>
          ${rec && rec.kind !== "prop" ? `<div class="gv-swatches">${OB.COLORS.map(c => `<button class="sw" data-c="${c}" style="background:#${c.toString(16).padStart(6, "0")}"></button>`).join("")}</div>` : ""}
          ${!isAdded(o) && movable(o) && dir.moved[OB.keyOf(o, gltf.scene)] ? `<button class="ghost sm" data-reset>↺ Put back where the file has it</button>` : ""}` : `<p class="hint">Click something in the view to select it, or pick it in the list.</p>`}</div>
        <div class="gv-sec"><div class="lblrow"><label class="lbl">In the scene</label>${hiddenCount() ? `<button class="ghost sm" data-showall>Show all (${hiddenCount()} hidden)</button>` : ""}</div>
          <input type="search" class="gv-ofilter" placeholder="Find an object" value="${esc(st.filter)}"><div class="gv-tree"></div></div></div>`));
      $$("[data-k=add] button", body).forEach(b => b.onclick = () => { if (b.dataset.v === "_prop") $(".gv-propfile", body).click(); else addKind(b.dataset.v); });
      $$("[data-k=prop] button", body).forEach(b => b.onclick = () => { const p = props.find(x => x.id === b.dataset.v); if (p) addKind("prop", { prop: p.id, name: p.name }); });
      const pf = $(".gv-propfile", body);
      pf.onchange = async () => {
        const f = pf.files[0]; if (!f || !opts.uploadProp) return;
        try { toast("Adding " + f.name + "…"); const p = await opts.uploadProp(f); props = props.filter(x => x.id !== p.id).concat([p]); addKind("prop", { prop: p.id, name: p.name }); }
        catch (e) { toast("Couldn't add the prop: " + (e.message || e), true); }
      };
      if (o) {
        $$("[data-tm]", body).forEach(b => b.onclick = () => { tc.setMode(b.dataset.tm); drawTab(); });
        $("[data-focus]", body).onclick = () => { if (!o.visible) setHidden(o, false); focusOn(o); };
        $("[data-subj]", body).onclick = () => { dir.subject = keyFor(o); saveDir(); drawSubjects(); toast(`${o.name} is the subject: frame it in Framing, light it in Light`); };
        $("[data-rm]", body).onclick = () => removeSel();
        const rn = $("[data-ren]", body); if (rn) rn.onclick = () => { const n = prompt("Name", o.name); if (n && n.trim()) { o.name = rec.name = n.trim().slice(0, 60); saveDir(); drawTab(); drawSubjects(); } };
        $$(".sw", body).forEach(b => b.onclick = () => { rec.color = +b.dataset.c; OB.setColor(o, rec.color); dirty = true; saveDir(); });
        const rs = $("[data-reset]", body); if (rs) rs.onclick = () => {
          delete dir.moved[OB.keyOf(o, gltf.scene)];
          const m0 = fileTRS.get(o); if (m0) setTRS(o, m0);
          saveDir(); select(o); dirty = true;          // the box and the handles follow it back
        };
      }
      const sa = $("[data-showall]", body); if (sa) sa.onclick = () => {
        dir.hidden.forEach(k => { const x = byKey.get(k); if (x) x.visible = true; }); dir.hidden = [];
        added.children.forEach(x => { if (!x.visible) setHidden(x, false); });
        saveDir(); dirty = true; drawSubjects(); drawTab();
      };
      const of = $(".gv-ofilter", body); of.oninput = () => { st.filter = of.value; drawTree(); };
      drawTree();
    }
  }
  function keyText(cam) {
    const m = cam && cam.move;
    if (!m || m.type !== "custom") return "Set a start, then change anything (framing, height, lens, light, or move the camera: operate it or fly the free camera), then set the end. ▶ animates everything between them.";
    if (m.pending) return `◆ Start at ${fmtSec(m.at || 0)}. Now you're shaping the end: change anything, then ◆ Set end. ` +
      (m.durSet ? `It ends at ${fmtSec((m.at || 0) + m.dur)} (Length: ${+m.dur.toFixed(2)} s).` : `It ends where the timeline is (move it there), or ${+m.dur.toFixed(2)} s after the start: type any Length above.`);
    const [s0, s1] = SH.shotOf(m), inShot = s0 < (m.at || 0) - 1e-6 || s1 > (m.at || 0) + m.dur + 1e-6 ? ` in the ${fmtSec(s0)}–${fmtSec(s1)} shot (still before and after)` : "";
    return `◆ ${fmtSec(m.at || 0)} → ◆ ${fmtSec((m.at || 0) + m.dur)}${inShot}. A change goes into the keyframe nearest the timeline (now the ${keyAt(cam) === "A" ? "start" : "end"}).`;
  }
  /* The timeline stopped on a keyframe (⏮ ⏭, or scrubbed there): the tabs show that keyframe's settings. */
  function atKey() {
    const s = st.cur && st.cur.kind === "studio" ? st.cur : null;
    if (!s || !keyed(s.cam) || st.playing) return;
    const m = s.cam.move, near = x => Math.abs(st.t - x) <= 1 / fps + 1e-6;
    if (near(m.at || 0) || near((m.at || 0) + m.dur)) { st.editing = null; beginEdit(s); drawTab(); }
  }
  function fmtPan(v) { return v ? `${Math.abs(v)}° ${v > 0 ? "left" : "right"}` : "centre"; }
  function fmtTilt(v) { return v ? `${Math.abs(v)}° ${v > 0 ? "up" : "down"}` : "level"; }
  function fmtAmt(v, unit) { return unit === "share" ? Math.round(v * 100) + "%" : unit === "°" ? Math.round(v) + "°" : unit === "×" ? "×" + (+v).toFixed(2) : (+v).toFixed(2) + " m"; }
  function drawTree() {
    const box = $(".gv-tree", body); if (!box || !gltf) return;
    box.innerHTML = "";
    const f = st.filter.trim().toLowerCase();
    const row = (o, depth, kids, label) => {
      const on = o.visible, r = h(`<div class="gv-orow ${st.sel === o ? "on" : ""} ${on ? "" : "off"}" style="padding-left:${6 + depth * 12}px">
        ${kids ? `<button class="gv-tw">${o.userData._open ? "▾" : "▸"}</button>` : `<span class="gv-tw"></span>`}<span class="gv-on">${esc(label || o.name || "(no name)")}</span><button class="gv-eye" title="${on ? "Hide" : "Show"}">${on ? "👁" : "◌"}</button></div>`);
      $(".gv-on", r).onclick = e => { st.rowClick = { o, t: performance.now(), x: e.clientX, y: e.clientY }; select(o); };   // (double: see below)
      $(".gv-eye", r).onclick = () => {
        setHidden(o, o.visible);
        drawSubjects(); drawTab();
      };
      const tw = $("button.gv-tw", r); if (tw) tw.onclick = () => { o.userData._open = !o.userData._open; drawTree(); };
      box.append(r);
    };
    if (added.children.length) {
      box.append(h(`<div class="gv-ohead">Added</div>`));
      added.children.filter(o => !f || o.name.toLowerCase().includes(f)).forEach(o => row(o, 0, false));
    }
    box.append(h(`<div class="gv-ohead">From the file</div>`));
    let n = 0;
    const walk = (node, depth) => {
      if (n > 600) return;
      const match = !f || node.name.toLowerCase().includes(f);
      if (f) { if (match) { row(node.obj, 0, false); n++; } node.kids.forEach(k => walk(k, depth)); return; }
      row(node.obj, depth, node.kids.length > 0, node.character ? "☺ " + node.name : null); n++;
      if (node.obj.userData._open) node.kids.forEach(k => walk(k, depth + 1));
    };
    OB.outline(gltf.scene, chars).forEach(x => walk(x, 0));
  }

  // ---- hud and tools
  function drawHud() {
    if (st.mode === "cam" && st.cur) {
      const lens = st.cur.kind === "ortho" ? "orthographic" : st.cur.kind === "studio" ? (st.cur.cam.A ? Math.round(studioPose(st.cur.cam, st.t).lens) + " mm" : "") : st.cur.info.lens + " mm";
      const what = st.cur.kind === "studio" ? SH.describe(st.cur.cam) : "";
      const m = st.cur.kind === "studio" && st.cur.cam.move, shaping = m && m.type === "custom" && m.pending ? " · ◆ shaping the end" : "";
      hudL.innerHTML = `<i class="rec"></i>${esc(st.cur.name)} · ${esc(lens)}${what || shaping ? ` <span>${esc(what)}${shaping}</span>` : ""}`;
    } else hudL.innerHTML = `✥ Free camera · ${st.freeLens} mm${st.cur ? ` <span>(from ${esc(st.cur.name)})</span>` : ""}`;
    hudB.classList.toggle("hidden", !operating());
    hudR.textContent = (ASPECTS.find(a => a[0] === st.aspect) || [0, ""])[1];
  }
  const aspectSel = $(".gv-aspect", el); aspectSel.value = st.aspect;
  aspectSel.onchange = () => { st.aspect = aspectSel.value; st.aspectAuto = false; layout(); drawHud(); state(); };
  const lookSel = $(".gv-look", el); lookSel.value = st.look;
  lookSel.onchange = () => { st.look = lookSel.value; applyLook(); state(); };
  function applyLook() {
    for (const m of meshes) {
      const o = origMat.get(m), ghost = [].concat(o).some(x => x && (x.transparent || x.alphaTest > 0 || x.transmission > 0));
      m.material = st.look === "clay" && !m.userData.isAdded ? (ghost ? clayGhost : clay) : o;     // stand-ins keep their colours
    }
    dirty = true;
  }
  $(".gv-thirdsb", el).onclick = e => { st.thirds = !st.thirds; e.currentTarget.classList.toggle("on", st.thirds); $(".gv-thirds", el).classList.toggle("hidden", !st.thirds); };
  $(".gv-gizb", el).onclick = e => { st.gizmos = !st.gizmos; e.currentTarget.classList.toggle("on", st.gizmos); gizmos.visible = rigMarks.visible = st.mode === "free" && st.gizmos; dirty = true; };
  const lensIn = $(".gv-lens", el), lensV = $(".gv-lensv", el); lensIn.value = st.freeLens; lensV.textContent = st.freeLens + " mm";
  lensIn.oninput = () => { st.freeLens = +lensIn.value; lensV.textContent = st.freeLens + " mm"; view.setFocalLength(st.freeLens); dirty = true; drawHud(); };
  $(".gv-fit", el).onclick = () => { st.cur = null; fitAll(); setMode("free"); drawSide(); };
  $(".gv-fs", el).onclick = () => { if (document.fullscreenElement) document.exitFullscreen(); else if (el.requestFullscreen) el.requestFullscreen().catch(() => { }); };
  $(".gv-thumbb", el).onclick = () => { thumbSoon = 1; dirty = true; };
  let stateTimer = 0, dirTimer = 0;
  function state() {
    clearTimeout(stateTimer);
    stateTimer = setTimeout(() => opts.onState && opts.onState({ camera: st.cur ? st.cur.id : "free", aspect: st.aspect, look: st.look, tab: st.tab, isolate: st.isolate, top: st.top, keys: st.dope }), 400);
  }
  function saveDir() {
    clearTimeout(dirTimer);
    dirTimer = setTimeout(() => { dirTimer = 0; opts.onDirection && opts.onDirection(JSON.parse(JSON.stringify(dir))); }, 400);
  }

  // ---- a picture of the set for its card
  let thumbSoon = 0;
  function takeThumb() {
    const c = document.createElement("canvas"), w = 640, hh = Math.round(640 / (canvas.width / canvas.height));
    c.width = w; c.height = Math.min(hh, 640);
    c.getContext("2d").drawImage(canvas, 0, 0, c.width, c.height);
    c.toBlob(b => { if (b && opts.onThumb) opts.onThumb(b); }, "image/jpeg", 0.85);
  }

  /* Double-click: an object comes into frame, selected, ready to move; a spot on the floor or a wall (free camera) is the
     new pivot to orbit around. */
  canvas.addEventListener("dblclick", e => {
    if (!meshes.length || operating() || (tc && tc.dragging)) return;
    const at = objectAt(e.clientX, e.clientY);
    if (!at) return;
    if (frameable(at.o)) { focusOn(at.o); return; }
    if (st.mode === "free") { st.fly = null; controls.target.copy(at.hit.point); controls.update(); dirty = true; }
  });

  // ---- ◫ Top view: the set from above, beside the view, to block the scene on: click and drag objects and cameras there.
  /* Drawn by its own renderer as a floor plan: everything higher than TOP_CUT above the floor is cut away (ceilings,
     roofs), the set in clay, people tinted, stand-ins in their colours, every camera big enough to see and grab. */
  const topEl = $(".gv-top", el), topCanvas = $("canvas", topEl), topB = $(".gv-topb", el), topLabel = $(".gv-toplabel", el);
  const topCam = new T.OrthographicCamera(-1, 1, 1, -1, 0.1, 10000); topCam.up.set(0, 0, -1);     // north (-Z) up
  const tv = { x: 0, z: 0, half: 10, w: 1, h: 1, floor: 0, fitted: false, user: false };     // user: you zoomed or panned it
  const cutPlane = new T.Plane(new T.Vector3(0, -1, 0), 0);
  const planMat = (color, extra) => new T.MeshLambertMaterial({ color, side: T.DoubleSide, clippingPlanes: [cutPlane], ...extra });
  const planSet = planMat(0xa4a4ac), planGhost = planMat(0xa4a4ac, { transparent: true, opacity: 0.18, depthWrite: false }), planChar = planMat(0x86a9d6);
  const planCols = new Map(), PLAN_BG = new T.Color(0x232328);
  let topR = null, tdrag = null, lastTopLabel = "";
  function planOf(m) {
    if (m.userData.isAdded) {                     // a stand-in or a prop: its own colour
      const o = [].concat(origMat.get(m) || m.material)[0], hex = o && o.color ? o.color.getHex() : 0xcccccc;
      if (!planCols.has(hex)) planCols.set(hex, planMat(hex));
      return planCols.get(hex);
    }
    if (charMeshes.has(m)) return planChar;
    return [].concat(origMat.get(m) || m.material).some(x => x && (x.transparent || x.transmission > 0)) ? planGhost : planSet;
  }
  function topSize(w, hh) {
    tv.w = Math.max(1, w); tv.h = Math.max(1, hh);
    topEl.style.width = tv.w + "px"; topEl.style.height = tv.h + "px";
    if (topR) topR.setSize(tv.w, tv.h, false);
    if (tv.fitted && !tv.user) fitTop();           // a new shape: the whole set again, unless you've framed it yourself
    dirty = true;
  }
  function fitTop() {              // the whole set, with every camera and what was added
    const b = bounds && !bounds.isEmpty() ? bounds.clone() : new T.Box3(new T.Vector3(-5, 0, -5), new T.Vector3(5, 2, 5));
    const ab = new T.Box3().setFromObject(added); if (!ab.isEmpty()) b.union(ab);
    for (const s of sources) { poseOf(s, tmpV1, tmpQ1); b.expandByPoint(tmpV1); }
    const c = b.getCenter(new T.Vector3()), sz = b.getSize(new T.Vector3());
    tv.x = c.x; tv.z = c.z; tv.half = Math.max(sz.z / 2, sz.x / 2 / (tv.w / tv.h)) * 1.1 + 0.5;
    const a = subjectBody(dir.subject);
    tv.floor = a ? a.feet[1] : b.min.y;
    dirty = true;
  }
  const tmpV1 = new T.Vector3(), tmpQ1 = new T.Quaternion();
  const TOP_CUT = 2.4;             // m above the floor: a floor plan's cut, above the people, under a ceiling
  function placeTopCam() {
    const a = tv.w / tv.h, hi = (bounds && !bounds.isEmpty() ? bounds.max.y : 10) + 50;
    Object.assign(topCam, { left: -tv.half * a, right: tv.half * a, top: tv.half, bottom: -tv.half, near: 0.1, far: hi - tv.floor + 1000 });
    topCam.position.set(tv.x, hi, tv.z); topCam.lookAt(tv.x, tv.floor, tv.z); topCam.updateProjectionMatrix(); topCam.updateMatrixWorld();
    cutPlane.constant = tv.floor + TOP_CUT;
  }
  function drawTop() {
    if (!st.top || !topR || !gltf) return;
    placeTopCam();
    const mats = meshes.map(m => m.material);
    meshes.forEach(m => { m.material = planOf(m); });
    const was = { env: scene.environment, bg: scene.background, hi: hemi.intensity, hc: hemi.color.getHex(), hg: hemi.groundColor.getHex(),
      rig: rig.visible, giz: gizmos.visible, marks: rigMarks.visible, tch: tcHelper.visible, files: fileLights.map(f => f.light.visible) };
    scene.environment = null; scene.background = PLAN_BG; hemi.intensity = 2.6; hemi.color.set(0xffffff); hemi.groundColor.set(0x4a4a52);
    rig.visible = false; rigMarks.visible = false; tcHelper.visible = false; fileLights.forEach(f => { f.light.visible = false; });
    gizmos.visible = true;
    const k = topGizK(), gv = sources.map(s => s.giz.visible);
    sources.forEach(s => { s.giz.visible = true; s.giz.scale.setScalar(k); if (s.end) s.end.scale.setScalar(k); });
    try { topR.render(scene, topCam); }
    finally {
      meshes.forEach((m, i) => { m.material = mats[i]; });
      Object.assign(scene, { environment: was.env, background: was.bg }); hemi.intensity = was.hi; hemi.color.setHex(was.hc); hemi.groundColor.setHex(was.hg);
      rig.visible = was.rig; gizmos.visible = was.giz; rigMarks.visible = was.marks; tcHelper.visible = was.tch;
      fileLights.forEach((f, i) => { f.light.visible = was.files[i]; });
      sources.forEach((s, i) => { s.giz.visible = gv[i]; s.giz.scale.setScalar(1); if (s.end) s.end.scale.setScalar(1); });
    }
    const across = tv.half * 2 * tv.w / tv.h, lbl = `Top view · ${across >= 10 ? Math.round(across) : across.toFixed(1)} m across`;
    if (lbl !== lastTopLabel) { lastTopLabel = lbl; topLabel.textContent = lbl; }
  }
  function setTop(on) {
    st.top = !!on;
    topEl.classList.toggle("hidden", !st.top); topB.classList.toggle("on", st.top);
    if (st.top && !topR) {
      try {
        topR = new T.WebGLRenderer({ canvas: topCanvas, antialias: true });
        topR.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); topR.outputColorSpace = T.SRGBColorSpace; topR.localClippingEnabled = true;
      } catch { st.top = false; topEl.classList.add("hidden"); topB.classList.remove("on"); toast("This browser can't draw a second view", true); return; }
    }
    layout();
    if (st.top && !tv.fitted) { fitTop(); tv.fitted = true; }
    dirty = true; state();
  }
  topB.onclick = () => setTop(!st.top);
  $(".gv-topx", el).onclick = () => setTop(false);
  $(".gv-topfit", el).onclick = () => { tv.user = false; fitTop(); };
  /* page point -> where it is on the floor plan (x, z) */
  const topWorld = (cx, cy) => {
    const r = topCanvas.getBoundingClientRect(), nx = (cx - r.left) / r.width * 2 - 1, ny = -(cy - r.top) / r.height * 2 + 1;
    return { x: tv.x + nx * tv.half * tv.w / tv.h, z: tv.z - ny * tv.half };
  };
  const topPx = (x, z) => { const r = topCanvas.getBoundingClientRect(); return [r.left + ((x - tv.x) / (tv.half * tv.w / tv.h) + 1) / 2 * r.width, r.top + ((z - tv.z) / tv.half + 1) / 2 * r.height]; };
  const topGizK = () => Math.max(1, tv.half * 2 / tv.h * 34 / 0.8);      // how much bigger cameras are drawn (about 34 px)
  function topCamAt(cx, cy) {       // the camera whose drawing is nearest the pointer (from its lens to the end of its frame), within 12 px
    let best = null, bd = 12;
    const fwd = new T.Vector3(), k = topGizK();
    for (const s of sources) {
      poseOf(s, tmpV1, tmpQ1); fwd.set(0, 0, -1).applyQuaternion(tmpQ1).multiplyScalar(0.8 * k);
      const [ax, ay] = topPx(tmpV1.x, tmpV1.z), [bx, by] = topPx(tmpV1.x + fwd.x, tmpV1.z + fwd.z);
      const vx = bx - ax, vy = by - ay, l2 = vx * vx + vy * vy, u = l2 ? Math.max(0, Math.min(1, ((cx - ax) * vx + (cy - ay) * vy) / l2)) : 0;
      const d = Math.hypot(cx - ax - u * vx, cy - ay - u * vy);
      if (d < bd) { bd = d; best = s; }
    }
    return best;
  }
  function topObjectAt(cx, cy) {    // what a click on the plan is on: not what the cut took away, not the floor or the walls
    const r = topCanvas.getBoundingClientRect(), rc = new T.Raycaster();
    placeTopCam(); rc.setFromCamera(new T.Vector2((cx - r.left) / r.width * 2 - 1, -(cy - r.top) / r.height * 2 + 1), topCam);
    const at = ownerOf(rc.intersectObjects(meshes, false).find(i => shown(i.object) && i.point.y <= cutPlane.constant + 1e-3));
    return at && frameable(at.o) ? at : null;
  }
  topCanvas.addEventListener("contextmenu", e => e.preventDefault());
  topCanvas.addEventListener("pointerdown", e => {
    if (!gltf) return;
    topCanvas.setPointerCapture(e.pointerId); st.fly = null;
    const w = topWorld(e.clientX, e.clientY), base = { sx: e.clientX, sy: e.clientY, w0: w, moved: false };
    if (e.button !== 0) { tdrag = { ...base, kind: "pan", tx: tv.x, tz: tv.z }; return; }
    const cam = topCamAt(e.clientX, e.clientY);
    if (cam) {
      if (cam.kind === "studio") {            // drag a Studio camera: you look through it, and it moves as you drag
        if (st.cur !== cam || st.mode !== "cam") pick(cam);
        beginEdit(cam);
        tdrag = { ...base, kind: "cam", src: cam, pose0: JSON.parse(JSON.stringify(cam.cam.A || currentViewPose())) };
      } else tdrag = { ...base, kind: "pickcam", src: cam };
      return;
    }
    const at = topObjectAt(e.clientX, e.clientY);
    if (at) {
      select(at.o);
      if (st.tab !== "objects") { st.tab = "objects"; drawTabs(); drawTab(); }
      if (movable(at.o)) {
        const p0 = at.o.getWorldPosition(new T.Vector3());
        tdrag = { ...base, kind: e.shiftKey ? "turn" : "move", o: at.o, p0, q0: at.o.quaternion.clone() };
        return;
      }
    }
    tdrag = { ...base, kind: "pan", tx: tv.x, tz: tv.z, empty: !at };
  });
  topCanvas.addEventListener("pointermove", e => {
    if (!tdrag) {                   // what a press would do here (looked up at most every 50 ms: it casts rays through the set)
      const now = performance.now(); if (now - (tv.hoverAt || 0) < 50) return; tv.hoverAt = now;
      const c = gltf && (topCamAt(e.clientX, e.clientY) || topObjectAt(e.clientX, e.clientY));
      topCanvas.style.cursor = c ? (c.kind === "studio" || (c.o && movable(c.o)) ? "move" : "pointer") : "grab";
      return;
    }
    if (Math.hypot(e.clientX - tdrag.sx, e.clientY - tdrag.sy) > 3) tdrag.moved = true;
    if (!tdrag.moved) return;
    const w = topWorld(e.clientX, e.clientY), dx = w.x - tdrag.w0.x, dz = w.z - tdrag.w0.z;
    if (tdrag.kind === "pan") {
      const wpp = tv.half * 2 / tv.h;
      tv.x = tdrag.tx - (e.clientX - tdrag.sx) * wpp; tv.z = tdrag.tz - (e.clientY - tdrag.sy) * wpp; tv.user = true;
      topCanvas.style.cursor = "grabbing";
    } else if (tdrag.kind === "move") {
      const o = tdrag.o, to = tdrag.p0.clone().add(new T.Vector3(dx, 0, dz));
      o.position.copy(o.parent ? o.parent.worldToLocal(to) : to); o.updateMatrixWorld(true);
    } else if (tdrag.kind === "turn") {           // around the vertical, in the world (whatever carries it): right = clockwise
      const o = tdrag.o, a = -(e.clientX - tdrag.sx) * 0.5 * Math.PI / 180;
      const P = o.parent ? o.parent.getWorldQuaternion(new T.Quaternion()) : new T.Quaternion();
      const R = new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 1, 0), a);
      o.quaternion.copy(P.clone().invert().multiply(R).multiply(P).multiply(tdrag.q0)); o.updateMatrixWorld(true);
    } else if (tdrag.kind === "cam") {
      const P0 = tdrag.pose0;
      editPose(tdrag.src, () => ({ ...P0, p: [P0.p[0] + dx, P0.p[1], P0.p[2] + dz], t: [P0.t[0] + dx, P0.t[1], P0.t[2] + dz] }));
    }
    dirty = true;
  });
  const topUp = () => {
    const d = tdrag; tdrag = null;
    if (!d) return;
    if ((d.kind === "move" || d.kind === "turn") && d.moved) objectMoved();
    else if (d.kind === "pan" && !d.moved && d.empty) select(null);
    else if (d.kind === "pickcam" && !d.moved) pick(d.src);
    else if (d.kind === "cam" && d.moved) { drawTab(); toast(`${d.src.name} moved`); }
    topCanvas.style.cursor = "";
    dirty = true;
  };
  topCanvas.addEventListener("pointerup", topUp); topCanvas.addEventListener("pointercancel", topUp);
  topCanvas.addEventListener("wheel", e => {           // zoom where the pointer is
    e.preventDefault();
    const w = topWorld(e.clientX, e.clientY), f = Math.exp(Math.max(-1, Math.min(1, e.deltaY * 0.0015)));
    const half = Math.max(0.5, Math.min(5000, tv.half * f)), k = half / tv.half;
    tv.x = w.x - (w.x - tv.x) * k; tv.z = w.z - (w.z - tv.z) * k; tv.half = half; tv.user = true; dirty = true;
  }, { passive: false });
  topCanvas.addEventListener("dblclick", e => {        // an object: into frame in the view; a camera: look through it
    const cam = topCamAt(e.clientX, e.clientY);
    if (cam) { pick(cam); return; }
    const at = topObjectAt(e.clientX, e.clientY);
    if (at) focusOn(at.o);
  });

  // ---- ◆ Keys: where every camera moves on the timeline, and moving those moves in time (under the transport)
  /* One lane per camera: a Studio camera's shot (the stretch of timeline it plays) with its move inside, from the start
     keyframe to the end one; drag either (see startLaneDrag). The set's cameras and its animation are shown where they
     move (they can't be moved: they're in the file). The ruler scrubs. */
  const dopeEl = $(".gv-dope", el), dRuler = $(".gv-druler .gv-dtrack", el), dLanes = $(".gv-dlanes", el), keysB = $(".gv-keysb", el);
  let dopeT = 10, dHeads = [], ddrag = null, dopeSig = "";
  const dopeSpan = () => Math.max(10, Math.ceil((duration() + 1) / 5) * 5);
  const pct = t => `${Math.max(0, t) / dopeT * 100}%`;
  const fmtRuler = t => { const m = Math.floor(t / 60), s = Math.round(t - m * 60); return `${m}:${String(s).padStart(2, "0")}`; };
  function setDope(on) {
    st.dope = !!on; keysB.classList.toggle("on", st.dope); drawDope(); state();
    if (st.dope && dopeEl.scrollIntoView) requestAnimationFrame(() => dopeEl.scrollIntoView({ block: "nearest", behavior: "smooth" }));   // in sight
  }
  keysB.onclick = () => setDope(!st.dope);
  /* The lanes: the set's animation, then every camera. A Studio camera's lane is its shot (the stretch it plays) with its
     move inside (from the start keyframe to the end one); a move whose end is still being shaped is just the move. */
  function lanesNow() {
    const out = [];
    if (st.fileDuration > 0) out.push({ name: "The set's animation", kind: "set", range: [0, st.fileDuration] });
    for (const s of sources) {
      if (s.kind === "studio") {
        const m = s.cam.move, has = !!(m && m.type), shaping = pendingMove(m);
        out.push({ s, name: s.name, kind: "studio", m, shaping, shot: has && !shaping ? SH.shotOf(m) : null,
          range: has ? (shaping ? [m.at || 0, (m.at || 0) + (m.dur || 0)] : SH.shotOf(m)) : null, keys: has ? [m.at || 0, (m.at || 0) + (m.dur || 0)] : null });
      } else if (s.range) out.push({ s, name: s.name, kind: "file", range: s.range });
    }
    return out;
  }
  const moveLabel = m => m.type === "custom" ? (m.pending ? "◆ shaping the end" : "◆ keyframes")
    : m.type === "static" ? [m.follow && "following", m.shake && "handheld"].filter(Boolean).join(", ") : SH.moveInfo(m.type).label;
  /* Puts a Studio camera's lane where its shot and move are now (also while dragging). */
  function placeLane(row, m) {
    const shot = $(".gv-dshot", row), bar = $(".gv-dbar", row), a = m.at || 0, b = a + (m.dur || 0);
    if (shot) {
      const [s0, s1] = SH.shotOf(m), w = Math.max(1e-6, s1 - s0);
      shot.style.left = pct(s0); shot.style.width = pct(w);
      if (bar) { bar.style.left = `${(a - s0) / w * 100}%`; bar.style.width = `${(b - a) / w * 100}%`; }
    } else if (bar) { bar.style.left = pct(a); bar.style.width = pct(b - a); }
  }
  function drawDope() {
    const show = st.dope && !$(".gv-transport", el).classList.contains("hidden");
    dopeEl.classList.toggle("hidden", !show);
    if (!show || ddrag) return;                   // (while dragging, only the dragged lane moves)
    const lanes = lanesNow(), T2 = dopeSpan();
    const sig = JSON.stringify([T2, lanes.map(L => [L.name, L.kind, L.range, L.keys, L.shot, L.s === st.cur, L.m && [L.m.type, L.m.pending, L.m.follow, L.m.shake]])]);
    if (sig === dopeSig && dLanes.childElementCount) { headAt(st.t); return; }      // nothing it shows has changed
    dopeSig = sig; dopeT = T2;
    const [tick, every] = [[20, 1, 5], [60, 5, 10], [180, 10, 30], [600, 30, 60], [Infinity, 60, 300]].find(x => dopeT <= x[0]).slice(1);
    let r = "";
    for (let t = 0; t <= dopeT + 1e-6; t += tick) {
      const big = Math.abs(t / every - Math.round(t / every)) < 1e-6;
      const edge = t > dopeT - 1e-6 ? ";transform:translateX(-100%)" : "";          // the last one stays inside
      r += `<i class="gv-dtick ${big ? "l" : ""}" style="left:${pct(t)}"></i>${big ? `<span class="gv-dtl" style="left:${pct(t)}${edge}">${fmtRuler(t)}</span>` : ""}`;
    }
    dRuler.innerHTML = r + `<i class="gv-dplay"></i>`;
    dLanes.innerHTML = "";
    for (const L of lanes) {
      const cur = L.s && L.s === st.cur, m = L.m;
      let inner;
      if (!L.range) inner = `<span class="gv-dnone">no move</span>`;
      else if (L.kind !== "studio") inner = `<div class="gv-dbar ro" style="left:${pct(L.range[0])};width:${pct(L.range[1] - L.range[0])}" title="${esc(L.name)}: ${fmtSec(L.range[0])} → ${fmtSec(L.range[1])} (in the file)"><span class="gv-dtxt">${L.kind === "file" ? "moves (in the file)" : ""}</span></div>`;
      else {
        const label = moveLabel(m), custom = m.type === "custom";
        const bar = `<div class="gv-dbar ${custom ? "custom" : ""} ${L.shaping ? "pending" : ""}" data-part="body" title="${esc(label)}: ◆ ${fmtSec(L.keys[0])} → ◆ ${fmtSec(L.keys[1])}. Drag to move it in the shot; drag an end to move that keyframe">
              <span class="gv-dh a" data-part="a"></span><span class="gv-dtxt">${esc(label)}</span><span class="gv-dh b" data-part="b"></span></div>`;
        inner = L.shaping ? bar
          : `<div class="gv-dshot" data-part="shot" title="${esc(L.name)}'s shot: ${fmtSec(L.shot[0])} → ${fmtSec(L.shot[1])}. Drag it to move the whole shot in time; drag its ends to change where it starts and ends">
              <span class="gv-dg a" data-part="shotA"></span>${m.type === "static" ? `<span class="gv-dtxt">${esc(label)}</span>` : bar}<span class="gv-dg b" data-part="shotB"></span></div>`;
      }
      const row = h(`<div class="gv-dlane ${cur ? "cur" : ""}"><button class="gv-dlabel" title="${L.s ? `Look through ${esc(L.name)}` : ""}">${esc(L.name)}</button><div class="gv-dtrack">${inner}<i class="gv-dplay"></i></div></div>`);
      if (L.s) $(".gv-dlabel", row).onclick = () => pick(L.s);
      if (L.kind === "studio" && L.range) { placeLane(row, m); const g = $(".gv-dshot", row) || $(".gv-dbar", row); g.addEventListener("pointerdown", e => startLaneDrag(e, L.s, row, g)); }
      scrubber($(".gv-dtrack", row));
      dLanes.append(row);
    }
    dHeads = $$(".gv-dplay", dopeEl);
    headAt(st.t);
  }
  function headAt(t) { const x = pct(t); for (const d of dHeads) d.style.left = x; }
  function scrubber(track) {          // press and drag on a track (not on a shot or a move) or the ruler: the timeline goes there
    track.addEventListener("pointerdown", e => {
      if (e.target.closest(".gv-dshot, .gv-dbar:not(.ro)") || e.button !== 0) return;
      track.setPointerCapture(e.pointerId); play(false);
      const go = ev => { const r = track.getBoundingClientRect(); setTime(Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * dopeT); headAt(st.t); };
      go(e);
      track.onpointermove = go;
      track.onpointerup = track.onpointercancel = () => { track.onpointermove = track.onpointerup = track.onpointercancel = null; atKey(); };
    });
  }
  scrubber(dRuler);
  /* Dragging in a Studio camera's lane. The shot: the whole shot (its move goes along), or one of its ends. The move:
     the whole move inside the shot, or one of its keyframes. Keyframes never shorten the shot (pushed past an end, it
     grows). A move still being shaped: the move itself (its end sets its length). Snaps (within 6 px) to 0, the
     playhead and the other lanes' edges, else to 0.1 s (Shift: a frame). */
  function startLaneDrag(e, s, row, grab) {
    if (e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    const m = s.cam.move, part = (e.target.closest("[data-part]") || grab).dataset.part, shaping = pendingMove(m);
    const w = $(".gv-dtrack", row).getBoundingClientRect().width, before = JSON.stringify([m.at, m.dur, m.shot]);
    const snap0 = { at: m.at || 0, dur: m.dur || 0, shot: m.shot ? m.shot.slice() : undefined };
    const [s0, s1] = SH.shotOf(m), a0 = snap0.at, b0 = a0 + snap0.dur;
    const marks = [0, st.t];
    for (const L of lanesNow()) if (L.s !== s && L.range) { marks.push(...L.range); if (L.keys) marks.push(...L.keys); }
    if (/^(a|b|body)$/.test(part) && !shaping) marks.push(s0, s1);        // a keyframe meets its shot's end…
    if (/^shot[AB]$/.test(part)) marks.push(a0, b0);                       // …and a shot's end meets a keyframe
    grab.setPointerCapture(e.pointerId); play(false);
    ddrag = { s, part };
    const txt = $(".gv-dtxt", row), was = txt ? txt.textContent : "";
    grab.classList.add("drag");
    const mark = t => { const px = dopeT / w * 6, near = marks.find(x => Math.abs(x - t) <= px); return near ?? null; };
    const grid = (t, frames) => frames ? Math.round(t * fps) / fps : Math.round(t * 10) / 10;
    const snap = (t, frames) => mark(t) ?? grid(t, frames);
    const slid = (start, len, frames) => {            // a stretch slid to start: its start or its end meets a mark
      const raw = Math.max(0, start), sa = mark(raw), se = mark(raw + len);
      return sa != null ? sa : se != null ? Math.max(0, se - len) : grid(raw, frames);
    };
    grab.onpointermove = ev => {
      const dt = (ev.clientX - e.clientX) / w * dopeT, fr = ev.shiftKey;
      Object.assign(m, { at: snap0.at, dur: snap0.dur }); if (snap0.shot) m.shot = snap0.shot.slice(); else delete m.shot;
      if (shaping) {                                  // the move being shaped: its start, its end (its length), or both
        let at = a0, dur = snap0.dur;
        if (part === "a") { at = Math.min(b0 - MOVE_MIN, Math.max(0, snap(a0 + dt, fr))); dur = b0 - at; }
        else if (part === "b") dur = Math.max(MOVE_MIN, snap(b0 + dt, fr) - a0);
        else at = slid(a0 + dt, dur, fr);
        m.at = Math.round(at * 1000) / 1000; m.dur = Math.round(dur * 1000) / 1000;
        if (part !== "body") m.durSet = true;
      } else if (part === "shot") SH.moveShot(m, slid(s0 + dt, s1 - s0, fr));
      else if (part === "shotA") SH.setShotEdges(m, Math.min(s1 - MOVE_MIN, snap(s0 + dt, fr)), null);
      else if (part === "shotB") SH.setShotEdges(m, null, Math.max(s0 + MOVE_MIN, snap(s1 + dt, fr)));
      else if (part === "a") SH.setKeyTimes(m, Math.min(b0 - MOVE_MIN, Math.max(0, snap(a0 + dt, fr))), null);
      else if (part === "b") SH.setKeyTimes(m, null, Math.max(a0 + MOVE_MIN, snap(b0 + dt, fr)));
      else SH.slideKeys(m, slid(a0 + dt, snap0.dur, fr) - a0);
      st.editing = null;
      placeLane(row, m);
      if (txt) { const [x0, x1] = SH.shotOf(m); txt.textContent = /^shot/.test(part) && !shaping ? `shot ${fmtSec(x0)} → ${fmtSec(x1)}` : `◆ ${fmtSec(m.at)} → ◆ ${fmtSec(m.at + m.dur)}`; }
      timeline(); dirty = true;
    };
    grab.onpointerup = grab.onpointercancel = () => {
      grab.onpointermove = grab.onpointerup = grab.onpointercancel = null;
      ddrag = null; grab.classList.remove("drag"); if (txt) txt.textContent = was;
      if (JSON.stringify([m.at, m.dur, m.shot]) !== before) {
        saveDir();
        const [x0, x1] = SH.shotOf(m);
        toast(shaping ? `${s.name}: ◆ ${fmtSec(m.at)} → ${fmtSec(m.at + m.dur)}` : `${s.name}: shot ${fmtSec(x0)}–${fmtSec(x1)}${m.type !== "static" ? `, move ◆ ${fmtSec(m.at)} → ◆ ${fmtSec(m.at + m.dur)}` : ""}`);
        if (st.tab === "move" && st.cur === s) drawTab();
      }
      timeline();
    };
  }

  // ---- keys
  const keys = new Set();
  const typing = () => { const a = document.activeElement; return a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) && a.type !== "range" && a.type !== "checkbox" || (a && a.isContentEditable); };
  const onKey = e => {
    if (!el.isConnected) return dispose();
    if (typing() || e.ctrlKey || e.metaKey || e.altKey || document.querySelector("#modal:not(.hidden)")) return;
    const k = e.key.toLowerCase();
    if (e.type === "keyup") { keys.delete(k); if (k === " ") e.preventDefault(); return; }
    if ("wasdqe".includes(k) && k.length === 1) { if (st.mode === "cam" && !operating()) explore(); keys.add(k); dirty = true; e.preventDefault(); return; }
    if (k === "shift") { keys.add(k); return; }
    if (k === " ") { play(); e.preventDefault(); }
    else if (k === "arrowright" || k === "arrowleft") { play(false); setTime(st.t + (k === "arrowright" ? 1 : -1) / fps); e.preventDefault(); }
    else if (k === "arrowdown" || k === "arrowup") { step(k === "arrowdown" ? 1 : -1); e.preventDefault(); }
    else if ((k === "delete" || k === "backspace" || k === "h") && st.sel) { removeSel(k === "h"); e.preventDefault(); }
    else if (k === "escape" && st.operate) setOperate(false);
    else if (k === "escape" && st.sel) select(null);
    else if (k === "m" && st.sel && movable(st.sel)) { tc.setMode("translate"); if (st.tab === "objects") drawTab(); }
    else if (k === "r" && st.sel && movable(st.sel)) { tc.setMode("rotate"); if (st.tab === "objects") drawTab(); }
  };
  window.addEventListener("keydown", onKey); window.addEventListener("keyup", onKey);
  const onBlur = () => keys.clear(); window.addEventListener("blur", onBlur);

  // ---- the loop: renders only when something changed
  let last = performance.now();
  const fwd = new T.Vector3(), side = new T.Vector3(), move = new T.Vector3(), tmpP = new T.Vector3(), tmpQ = new T.Quaternion();
  let raf = 0, lastTc = "", lastHud = "";
  function loop() {
    raf = requestAnimationFrame(loop);
    if (!el.isConnected) return dispose();
    if (st.recording) return;                 // record() draws every frame itself
    const now = performance.now(), raw = (now - last) / 1000, dt = Math.min(raw, 0.1); last = now;
    if (!gltf) return;
    if (st.playing) {
      let t = st.t + Math.min(raw, 0.5) * st.speed;
      const [lo, hi] = span();
      if (st.stopAt != null && t >= st.stopAt) { t = st.stopAt; play(false); }
      else if (t >= hi) t = lo;            // loops: the camera's move, or the whole timeline
      setTime(t);
    }
    if (operating() && keys.size) {           // operating: the keys move the Studio camera itself
      const sp = 2 * (keys.has("shift") ? 5 : 1) * dt, k = c => keys.has(c) ? 1 : 0;
      const r = (k("d") - k("a")) * sp, u = (k("e") - k("q")) * sp, f = (k("w") - k("s")) * sp;
      if (r || u || f) editPose(st.cur, p => SH.slide(p, r, u, f));
    }
    if (st.mode === "free" && keys.size) {
      const sp = 4 * (keys.has("shift") ? 5 : 1) * dt;
      view.getWorldDirection(fwd); side.crossVectors(fwd, view.up).normalize(); move.set(0, 0, 0);
      if (keys.has("w")) move.add(fwd); if (keys.has("s")) move.sub(fwd);
      if (keys.has("d")) move.add(side); if (keys.has("a")) move.sub(side);
      if (keys.has("e")) move.y += 1; if (keys.has("q")) move.y -= 1;
      if (move.lengthSq()) { move.normalize().multiplyScalar(sp); view.position.add(move); controls.target.add(move); dirty = true; }
    }
    if (st.fly) {                              // flying to an object (double-click): eased, then the orbit controls take over
      const f = st.fly, k = Math.min(1, (now - f.start) / f.dur), e2 = SH.ease(k, "smooth");
      view.position.lerpVectors(f.p0, f.p1, e2); controls.target.lerpVectors(f.t0, f.t1, e2);
      if (k >= 1 || st.mode !== "free") st.fly = null;
      dirty = true;
    }
    if (st.mode === "free" && controls.update()) dirty = true;
    let cam = view;
    if (st.mode === "cam" && st.cur) {
      const lens = poseOf(st.cur, tmpP, tmpQ);
      if (st.cur.kind === "ortho") {
        cam = oview;
        const ym = st.cur.info.ymag || 10, xm = ym * aspectNum();
        if (!tmpP.equals(oview.position) || !tmpQ.equals(oview.quaternion) || oview.top !== ym || oview.right !== xm) {
          oview.position.copy(tmpP); oview.quaternion.copy(tmpQ); Object.assign(oview, { left: -xm, right: xm, top: ym, bottom: -ym, near: st.cur.info.znear || 0.1, far: st.cur.info.zfar || 5000 }); oview.updateProjectionMatrix(); dirty = true;
        }
      } else if (!tmpP.equals(view.position) || !tmpQ.equals(view.quaternion) || Math.abs(view.getFocalLength() - lens) > 1e-3) {
        view.position.copy(tmpP); view.quaternion.copy(tmpQ); view.setFocalLength(lens); dirty = true;
      }
    }
    animateLight();
    if (flickers.length) { for (const f of flickers) f.light.intensity = f.base * LI.flicker(f.flicker, performance.now() / 1000); dirty = true; }
    if ((gizmos.visible || st.top) && dirty) {
      for (const s of sources) {
        poseOf(s, s.giz.position, s.giz.quaternion); s.giz.visible = !(st.mode === "cam" && s === st.cur);
        s.giz.material = s === st.cur ? gizOn : s.kind === "studio" ? gizStudio : gizMat;
        if (s.kind === "studio") {
          const m = s.cam.move, B = m && m.B && m.type !== "static" ? m.B : null;
          s.path.visible = s.end.visible = !!B;
          if (B) {
            const pts = []; for (let i = 0; i <= PATH_STEPS; i++) { const p = SH.poseAt({ ...m, A: m.A || s.cam.A, at: 0, dur: 1, shake: 0 }, i / PATH_STEPS); pts.push(new T.Vector3(...p.p)); }
            s.path.geometry.setFromPoints(pts); s.path.geometry.computeBoundingSphere(); s.path.computeLineDistances();
            SH.applyPose(tmpCam, B); s.end.position.copy(tmpCam.position); s.end.quaternion.copy(tmpCam.quaternion);
            if (s.endLens !== B.lens) { s.endLens = B.lens; s.end.geometry.dispose(); s.end.geometry = frustumGeometry(B.lens || 35); }
          }
        }
      }
    }
    if (selBox) selBox.update();
    const [lo, hi] = span(), iso = lo > 0 || hi < duration();
    const tcs = iso ? `${fmtSec(Math.max(0, st.t - lo))} / ${fmtSec(hi - lo)} · set ${fmtTime(st.t, fps)}` : fmtTime(st.t, fps) + " / " + fmtSec(duration());
    if (tcs !== lastTc) { lastTc = tcs; tc_.textContent = tcs; if (document.activeElement !== slider) slider.value = st.t; if (st.dope) headAt(st.t); }
    if (st.cur && st.cur.kind === "studio" && st.mode === "cam") { const hs = Math.round(studioPose(st.cur.cam, st.t)?.lens || 0); if (hs !== lastHud) { lastHud = hs; drawHud(); } }
    if (!dirty && !thumbSoon) return;
    dirty = false;
    tcHelper.visible = !!tc.object && (st.mode === "free" || !!(st.cur && st.cur.kind !== "ortho"));   // move what you selected through the camera too
    draw(cam);
    if (st.top) drawTop();
    if (thumbSoon && --thumbSoon === 0) takeThumb();
  }
  function draw(cam) {
    const pov = st.mode === "cam" && st.cur && st.cur.kind === "studio" && st.cur.cam.setup && st.cur.cam.setup.type === "pov" ? subjectObj(st.cur.cam.setup.subject) : null;
    const wasOn = pov ? pov.visible : true;
    if (pov) pov.visible = false;                  // a point of view doesn't see its own head
    renderer.render(scene, cam);
    if (pov) pov.visible = wasOn;
  }

  /* ---- pictures and movies of the view: drawn at their own size, without gizmos, handles or guides */
  function outSize(long) {
    const a = aspectNum(), even = v => Math.max(2, Math.round(v / 2) * 2);
    return a >= 1 ? [even(long), even(long / a)] : [even(long * a), even(long)];
  }
  function renderAt(w, hh) {
    const size = renderer.getSize(new T.Vector2()), pr = renderer.getPixelRatio();
    const hide = [gizmos, rigMarks, tcHelper, selBox].filter(Boolean), was = hide.map(o => o.visible);
    hide.forEach(o => { o.visible = false; });
    renderer.setPixelRatio(1); renderer.setSize(w, hh, false);
    let cam = view;
    if (st.mode === "cam" && st.cur) {
      const lens = poseOf(st.cur, tmpP, tmpQ);
      if (st.cur.kind === "ortho") {
        cam = oview; const ym = st.cur.info.ymag || 10, xm = ym * w / hh;
        oview.position.copy(tmpP); oview.quaternion.copy(tmpQ); Object.assign(oview, { left: -xm, right: xm, top: ym, bottom: -ym }); oview.updateProjectionMatrix();
      } else { view.position.copy(tmpP); view.quaternion.copy(tmpQ); view.aspect = w / hh; view.setFocalLength(lens); }
    } else { view.aspect = w / hh; view.setFocalLength(st.freeLens); }
    animateLight();
    for (const f of flickers) f.light.intensity = f.base * LI.flicker(f.flicker, st.t);
    draw(cam);
    const out = document.createElement("canvas"); out.width = w; out.height = hh;
    out.getContext("2d").drawImage(canvas, 0, 0, w, hh);      // copied at once: the WebGL picture is gone after this task
    renderer.setPixelRatio(pr); renderer.setSize(size.x, size.y, false);
    view.aspect = size.x / size.y; view.setFocalLength(lensNow());
    hide.forEach((o, k) => { o.visible = was[k]; });
    dirty = true;
    return out;
  }
  const toBlob = (c, type, q) => new Promise((res, rej) => c.toBlob(b => b ? res(b) : rej(new Error("The picture couldn't be made")), type, q));
  /* What the shot is, for the snapshot's name and a storyboard shot's prompt and camera. */
  function shotMeta() {
    const s = st.mode === "cam" ? st.cur : null, cam = s && s.kind === "studio" ? s.cam : null, setup = (cam && cam.setup) || {};
    const subj = subjectObj(setup.subject || dir.subject), size = SH.SHOT_SIZES.find(x => x.id === setup.size);
    const hgt = SH.HEIGHTS.find(x => x.id === setup.height), m = cam && cam.move;
    return { camera: s ? s.name : "Free camera", studio: !!cam, describe: cam ? SH.describe(cam) : "", lens: Math.round(lensNow()), t: st.t,
      aspect: (ASPECTS.find(a => a[0] === st.aspect) || [0, ""])[1], size: size ? size.words : "", height: hgt ? hgt.label : "",
      subject: subj ? OB.niceName(subj.name) : "", move: m && m.type && m.type !== "static" ? m.type : "", follow: !!(m && m.follow), handheld: !!(m && m.shake) };
  }
  /* The stretch a movie of this camera covers: its move, or (a still camera) a few seconds from now. */
  function movieSpan() {
    const r = rangeOf(st.mode === "cam" ? st.cur : null) || (st.cur && rangeOf(st.cur));
    if (r) return r;
    const d = duration();
    return d > 0 ? [Math.min(st.t, Math.max(0, d - 5)), Math.min(d, Math.max(st.t, 0) + 5)] : [0, 4];
  }
  const recB = $(".gv-rec", el);
  async function record(o, onFrame, cancelled) {
    const fps2 = Math.max(1, Math.min(60, o.fps || 24)), from = Math.max(0, o.from || 0), to = Math.max(from, o.to ?? from + 4);
    const n = Math.max(1, Math.round((to - from) * fps2) + 1), [w, hh] = outSize(o.long || 1280);
    play(false); st.recording = true;
    try {
      for (let i = 0; i < n; i++) {
        if (cancelled && cancelled()) throw new Error("Stopped");
        setTime(from + i / fps2);
        recB.classList.remove("hidden"); recB.textContent = `● REC ${i + 1} / ${n}`;
        const blob = await toBlob(renderAt(w, hh), "image/jpeg", 0.92);
        await onFrame(blob, i, n, [w, hh]);
      }
    } finally { st.recording = false; recB.classList.add("hidden"); dirty = true; }
    return { frames: n, size: [w, hh], fps: fps2 };
  }
  $(".gv-snap", el).onclick = () => opts.onSnapshot && opts.onSnapshot();
  $(".gv-movie", el).onclick = () => opts.onMovie && opts.onMovie();
  const flush = () => { if (dirTimer) { clearTimeout(dirTimer); dirTimer = 0; opts.onDirection && opts.onDirection(JSON.parse(JSON.stringify(dir)), true); } };
  window.addEventListener("pagehide", flush);
  layout(); drawHud(); drawTabs();
  raf = requestAnimationFrame(loop);

  let gone = false;
  function dispose() {
    if (gone) return; gone = true;
    cancelAnimationFrame(raf); ro.disconnect(); clearTimeout(stateTimer);
    flush(); window.removeEventListener("pagehide", flush);
    window.removeEventListener("keydown", onKey); window.removeEventListener("keyup", onKey); window.removeEventListener("blur", onBlur);
    tc.detach(); tc.dispose(); controls.dispose(); draco.dispose();
    scene.traverse(o => {
      if (o.geometry) o.geometry.dispose();
      if (o.shadow && o.shadow.map) o.shadow.map.dispose();
      for (const m of [].concat(origMat.get(o) || o.material || [])) { if (!m) continue; for (const v of Object.values(m)) if (v && v.isTexture) v.dispose(); m.dispose(); }
    });
    [clay, clayGhost, gizMat, gizOn, gizStudio].forEach(m => m.dispose()); envTex.dispose(); pmrem.dispose();
    renderer.dispose(); renderer.forceContextLoss();
    if (topR) { topR.dispose(); topR.forceContextLoss(); }
    [planSet, planGhost, planChar, ...planCols.values()].forEach(m => m.dispose());
    el.remove();
  }
  const api = {
    dispose,
    get state() {
      return { mode: st.mode, camera: st.cur ? st.cur.id : null, t: st.t, aspect: st.aspect, look: st.look, loaded: !!gltf, cameras: sources.length,
        studio: dir.cams.length, selected: st.sel ? st.sel.name : null, subject: dir.subject, light: lightParams().preset, added: dir.added.length, hidden: dir.hidden.length };
    },
    pick: id => { const s = sources.find(x => x.id === id); if (s) pick(s); return !!s; },
    /* a picture of the view: { blob (PNG), meta: what the shot is } */
    async snapshot(long = 1920) { const [w, hh] = outSize(long); return { blob: await toBlob(renderAt(w, hh), "image/png"), meta: shotMeta(), size: [w, hh] }; },
    movieSpan, meta: shotMeta, record, outSize, setOperate, newCamera: () => newStudioCam(true).id,
    pose: () => currentViewPose(), explore, setTime, play,
    /* where the selected object is on the page (for tests that drag its gizmo) */
    screenPos(middle = false) {       // (middle: the middle of its box, not its origin, e.g. a person's feet)
      if (!st.sel) return null;
      const cam = st.mode === "cam" && st.cur && st.cur.kind === "ortho" ? oview : view; cam.updateMatrixWorld();
      const v = (middle ? new T.Box3().setFromObject(st.sel, true).getCenter(new T.Vector3()) : st.sel.getWorldPosition(new T.Vector3())).project(cam), r = canvas.getBoundingClientRect();
      return [r.left + (v.x + 1) / 2 * r.width, r.top + (1 - v.y) / 2 * r.height];
    },
    /* where the selected object (or else the camera you look through) is on the top view's page, and the middle of
       its drawing for a camera (for tests that drag them there) */
    topPos() {
      if (!st.top) return null;
      if (st.sel) { const p = st.sel.getWorldPosition(new T.Vector3()); return topPx(p.x, p.z); }
      if (!st.cur) return null;
      const q = new T.Quaternion(), p = new T.Vector3(); poseOf(st.cur, p, q);
      const f = new T.Vector3(0, 0, -1).applyQuaternion(q).multiplyScalar(0.4 * topGizK());
      return topPx(p.x + f.x, p.z + f.z);
    },
  };
  el.previz = api;      // the viewer's controls, for tests and the console
  return api;
}
