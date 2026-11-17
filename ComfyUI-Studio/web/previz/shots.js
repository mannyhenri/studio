/* Previz: the maths of directing a camera. Shot sizes, camera heights and angles, and camera moves, as a camera setup
   that frames a subject. No DOM here (tests run it in node: tests/unit/test_previz_js.py).

   A pose is { p: [x,y,z] camera position, t: [x,y,z] the point it looks at, roll: degrees, lens: mm }.
   A body is what a subject measures: { feet: [x,y,z] (floor point under it), h: height, facing: [x,0,z] unit,
   w: width, d: depth, person: bool, center: [x,y,z] (middle of its box),
   head: [x,y,z] (its head bone, if it has one: close shots aim at the face) }. */
import * as T from "../vendor/three/three.bundle.min.js";

export const SENSOR = 36;                       // mm across the wider side of the frame (Blender's auto sensor fit)
const D2R = Math.PI / 180, Y = new T.Vector3(0, 1, 0);
const V = a => new T.Vector3(a[0], a[1], a[2]);
const A = v => [v.x, v.y, v.z];

export function vfov(lens, aspect) { return aspect >= 1 ? 2 * Math.atan(SENSOR / 2 / (lens * aspect)) : 2 * Math.atan(SENSOR / 2 / lens); }
export function hfov(lens, aspect) { return 2 * Math.atan(Math.tan(vfov(lens, aspect) / 2) * aspect); }
export function lensForVfov(v, aspect) { return aspect >= 1 ? SENSOR / 2 / (Math.tan(v / 2) * aspect) : SENSOR / 2 / Math.tan(v / 2); }

/* Where things are on a standing person, as a share of their height from the floor. */
export const BODY = { top: 1, eyes: 0.936, chin: 0.87, shoulders: 0.82, chest: 0.72, waist: 0.61, hips: 0.52, thigh: 0.40, knees: 0.28, shins: 0.14, feet: 0 };

/* Shot sizes, widest to tightest. For a person: what the frame cuts (top and bottom, as BODY shares) with room above the
   head; the wide ones give the frame's height in body heights. `obj`: how many times an object's size the frame is. */
export const SHOT_SIZES = [
  { id: "EWS", words: "extreme wide shot", label: "Extreme wide", hint: "Establishing shot: the place, the figure tiny in it", frame: 12, obj: 20 },
  { id: "VWS", words: "very wide shot", label: "Very wide", hint: "The figure small in the setting", frame: 5, obj: 9 },
  { id: "WS", words: "wide shot", label: "Wide (long shot)", hint: "The whole figure with room around it", frame: 2.2, obj: 5 },
  { id: "FS", words: "full shot", label: "Full shot", hint: "Head to toe", top: 1, bottom: 0, room: 0.07, foot: 0.04, obj: 3, wid: 0.15 },
  { id: "MFS", words: "medium full shot", label: "Medium full", hint: "From the knees up", top: 1, bottom: BODY.knees, room: 0.08, obj: 2.6, wid: 0.15 },
  { id: "COW", words: "cowboy shot", label: "Cowboy", hint: "Mid-thigh up: the American shot", top: 1, bottom: BODY.thigh, room: 0.08, obj: 2.2, wid: 0.15 },
  { id: "MS", words: "medium shot", label: "Medium", hint: "From the waist up", top: 1, bottom: 0.58, room: 0.1, obj: 1.8, wid: 0.14 },
  { id: "MCU", words: "medium close-up", label: "Medium close-up", hint: "From the chest up", top: 1, bottom: 0.70, room: 0.1, obj: 1.4, wid: 0.13, dep: 0.07 },
  { id: "CU", words: "close-up", label: "Close-up", hint: "Head and shoulders", top: 1, bottom: 0.80, room: 0.1, obj: 1.1, wid: 0.09, dep: 0.06 },
  { id: "BCU", words: "big close-up", label: "Big close-up", hint: "Forehead to chin (a choker)", top: 0.975, bottom: 0.86, room: 0, obj: 0.6, wid: 0.055, dep: 0.04 },
  { id: "ECU", words: "extreme close-up", label: "Extreme close-up", hint: "Just the eyes", top: 0.955, bottom: 0.915, room: 0, obj: 0.35, wid: 0.04, dep: 0.03 },
];

/* Kinds of shot: who is in it and where the camera stands. */
export const SHOT_TYPES = [
  { id: "single", label: "Single", hint: "One subject" },
  { id: "two", label: "Two-shot", hint: "The subject and a second person together" },
  { id: "group", label: "Group", hint: "Everyone in the scene" },
  { id: "ots", label: "Over the shoulder", hint: "Past the second person's shoulder onto the subject" },
  { id: "pov", label: "POV", hint: "Through the subject's eyes" },
  { id: "insert", label: "Insert", hint: "A detail: an object or a hand, close" },
];

/* Which side of the subject the camera stands on, in degrees from straight in front (+ is the subject's left). */
export const SIDES = [
  { id: "front", label: "Front", deg: 0 }, { id: "3ql", label: "¾ left", deg: 45 }, { id: "pl", label: "Profile left", deg: 90 },
  { id: "back", label: "Back", deg: 180 }, { id: "pr", label: "Profile right", deg: -90 }, { id: "3qr", label: "¾ right", deg: -45 },
];

/* Camera heights (the lens at a point of the body, looking at the frame) and angles (tilted by so many degrees). */
export const HEIGHTS = [
  { id: "eye", label: "Eye level", hint: "Neutral: the lens at the subject's eyes", at: BODY.eyes },
  { id: "shoulder", label: "Shoulder level", hint: "Just under the eyes: a touch of stature", at: BODY.shoulders },
  { id: "chest", label: "Chest level", at: BODY.chest },
  { id: "hip", label: "Hip level", hint: "The western's gunslinger height", at: BODY.hips },
  { id: "knee", label: "Knee level", hint: "Low, looking up slightly", at: BODY.knees },
  { id: "ground", label: "Ground level", hint: "The lens on the floor", abs: 0.12 },
  { id: "high", label: "High angle", hint: "Above, looking down: the subject seems smaller", el: 30 },
  { id: "low", label: "Low angle", hint: "Below, looking up: the subject seems powerful", el: -25 },
  { id: "overhead", label: "Overhead", hint: "Bird's-eye: straight down", el: 89.5 },
  { id: "worm", label: "Worm's-eye", hint: "From the ground, steeply up (goes wide to fit)", el: -65, minY: 0.05 },
  { id: "aerial", label: "Aerial", hint: "High and far, like a drone", el: 45, far: 3 },
];

export const LENSES = [14, 18, 24, 35, 50, 85, 135];
export const MIN_FACE_DISTANCE = 0.5;      // m: closer than this to a person, a shot goes to a longer lens

/* Camera moves. `unit`: what the amount is (m, ° or ×, or a share of the distance). */
export const MOVES = [
  { id: "static", label: "Static", hint: "Locked off" },
  { id: "push", label: "Push in", hint: "Dolly in towards the subject", amt: 0.35, unit: "share", min: 0.05, max: 0.9 },
  { id: "pull", label: "Pull out", hint: "Dolly back away from the subject", amt: 0.6, unit: "share", min: 0.1, max: 3 },
  { id: "truck_l", label: "Truck left", hint: "Slide sideways, left", amt: 1.5, unit: "m", min: 0.2, max: 10 },
  { id: "truck_r", label: "Truck right", hint: "Slide sideways, right", amt: 1.5, unit: "m", min: 0.2, max: 10 },
  { id: "ped_up", label: "Pedestal up", hint: "Rise straight up, staying level", amt: 0.8, unit: "m", min: 0.1, max: 5 },
  { id: "ped_down", label: "Pedestal down", hint: "Sink straight down, staying level", amt: 0.8, unit: "m", min: 0.1, max: 5 },
  { id: "crane_up", label: "Crane up", hint: "Rise while keeping the subject in frame", amt: 3, unit: "m", min: 0.3, max: 20 },
  { id: "crane_down", label: "Crane down", hint: "Come down onto the subject", amt: 3, unit: "m", min: 0.3, max: 20 },
  { id: "pan_l", label: "Pan left", hint: "Turn on the spot, left", amt: 30, unit: "°", min: 5, max: 180 },
  { id: "pan_r", label: "Pan right", hint: "Turn on the spot, right", amt: 30, unit: "°", min: 5, max: 180 },
  { id: "tilt_u", label: "Tilt up", hint: "Turn on the spot, up", amt: 20, unit: "°", min: 5, max: 80 },
  { id: "tilt_d", label: "Tilt down", hint: "Turn on the spot, down", amt: 20, unit: "°", min: 5, max: 80 },
  { id: "arc_l", label: "Arc left", hint: "Circle around the subject (orbit)", amt: 45, unit: "°", min: 5, max: 360 },
  { id: "arc_r", label: "Arc right", hint: "Circle around the subject (orbit)", amt: 45, unit: "°", min: 5, max: 360 },
  { id: "zoom_in", label: "Zoom in", hint: "The lens gets longer; the camera stays", amt: 2, unit: "×", min: 1.1, max: 6 },
  { id: "zoom_out", label: "Zoom out", hint: "The lens gets wider; the camera stays", amt: 2, unit: "×", min: 1.1, max: 6 },
  { id: "vertigo_in", label: "Dolly zoom in", hint: "Vertigo: push in while zooming out; the background stretches", amt: 0.5, unit: "share", min: 0.1, max: 0.8 },
  { id: "vertigo_out", label: "Dolly zoom out", hint: "Vertigo: pull out while zooming in; the background closes in", amt: 1, unit: "share", min: 0.2, max: 3 },
  { id: "whip_l", label: "Whip pan left", hint: "A fast pan that blurs", amt: 90, unit: "°", min: 30, max: 180, dur: 0.5 },
  { id: "whip_r", label: "Whip pan right", hint: "A fast pan that blurs", amt: 90, unit: "°", min: 30, max: 180, dur: 0.5 },
  { id: "custom", label: "◆ Keyframes", hint: "Your own move: set a start keyframe, change anything (framing, height, lens, light, or move the camera), set an end keyframe" },
];
export const moveInfo = id => MOVES.find(m => m.id === id) || MOVES[0];

/* ---------------------------------------------------------------- framing */
export function rotY(v, deg) { const a = deg * D2R, c = Math.cos(a), s = Math.sin(a); return [v[0] * c + v[2] * s, v[1], -v[0] * s + v[2] * c]; }
const flat = v => { const l = Math.hypot(v[0], v[2]) || 1; return [v[0] / l, 0, v[2] / l]; };

/* The points a shot must hold in frame, and where the camera aims, for one body and a shot size. */
export function region(body, sizeId, typeId = "single") {
  const s = SHOT_SIZES.find(x => x.id === sizeId) || SHOT_SIZES[7];
  const [fx, fy, fz] = body.feet, H = body.h;
  if (!body.person || typeId === "insert") {        // an object: its box, times the size
    const k = s.obj, c = body.center || [fx, fy + H / 2, fz];
    const hw = Math.max(body.w, 0.05) * k / 2, hh = Math.max(H, 0.05) * k / 2, hd = Math.max(body.d, 0.05) * k / 2;
    return { aim: c, pts: box(c, hw, hh, hd) };
  }
  const hw = (s.wid || 0.15) * H, hd = (s.dep || 0.09) * H;      // half the width and depth of what's framed
  const onHead = body.head && s.bottom >= 0.68;                   // close shots are about the face, not the middle of the body
  const cx = onHead ? body.head[0] + (body.facing || [0, 0, 0])[0] * 0.04 * H : fx, cz = onHead ? body.head[2] + (body.facing || [0, 0, 0])[2] * 0.04 * H : fz;
  if (s.frame) {
    const c = [fx, fy + H * 0.5, fz], hh = s.frame * H / 2;
    return { aim: c, pts: box(c, Math.max(hw, body.w / 2) * s.frame / 2.2, hh, hd) };
  }
  const span = (s.top - s.bottom) * H, Vh = span / (1 - (s.room || 0) - (s.foot || 0));
  const ft = fy + s.top * H + (s.room || 0) * Vh, fb = ft - Vh;
  const c = [cx, (ft + fb) / 2, cz];
  const w = s.bottom <= 0.3 ? Math.max(hw, Math.min(body.w / 2, 0.5 * H)) : hw;
  return { aim: c, pts: box(c, w, Vh / 2, hd) };
}
function box(c, hw, hh, hd) {
  const o = [];
  for (const x of [-hw, hw]) for (const y of [-hh, hh]) for (const z of [-hd, hd]) o.push([c[0] + x, c[1] + y, c[2] + z]);
  return o;
}
function union(regions) {
  const pts = regions.flatMap(r => r.pts), mn = [1e9, 1e9, 1e9], mx = [-1e9, -1e9, -1e9];
  pts.forEach(p => p.forEach((v, i) => { mn[i] = Math.min(mn[i], v); mx[i] = Math.max(mx[i], v); }));
  const c = mn.map((v, i) => (v + mx[i]) / 2);
  return { aim: c, pts: box(c, (mx[0] - mn[0]) / 2, (mx[1] - mn[1]) / 2, (mx[2] - mn[2]) / 2) };
}

/* The camera's axes when it looks along `dir` (unit), rolled by `roll` degrees. `ref` is the up it keeps when looking
   straight up or down (then "up" in the picture is the subject's facing). */
export function basis(dir, roll = 0, ref = [0, 0, -1]) {
  const f = V(dir).normalize();
  let up = Math.abs(f.y) > 0.995 ? V(ref).normalize() : Y.clone();
  const right = new T.Vector3().crossVectors(f, up).normalize();
  up = new T.Vector3().crossVectors(right, f).normalize();
  if (roll) { const q = new T.Quaternion().setFromAxisAngle(f, -roll * D2R); right.applyQuaternion(q); up.applyQuaternion(q); }
  return { f, right, up };
}

/* How far back the camera must be, looking along `dir` at `aim`, so every point is inside the frame. */
export function fitDistance(pts, aim, dir, lens, aspect, ref) {
  const { f, right, up } = basis(dir, 0, ref);
  const tv = Math.tan(vfov(lens, aspect) / 2), th = Math.tan(hfov(lens, aspect) / 2), a = V(aim);
  let d = 0.05;
  for (const p of pts) {
    const r = V(p).sub(a), z = r.dot(f);
    d = Math.max(d, Math.abs(r.dot(up)) / tv - z, Math.abs(r.dot(right)) / th - z);
  }
  return d;
}

/* A camera setup -> a pose. setup: { size, type, side (deg from the subject's front; + = its left), height (HEIGHTS id or
   "custom" with camY), roll, lens, ots: "left"|"right" }. bodies: { a: subject, b: second person, all: [every person] }.
   `aspect` is the frame's. Returns the pose and what it did (for the panel's notes). */
export function solve(setup, bodies, aspect) {
  const a = bodies.a;
  if (!a) throw new Error("Pick a subject first");
  const type = setup.type || "single", lens0 = setup.lens || 35, roll = setup.roll || 0;
  const hgt = HEIGHTS.find(x => x.id === setup.height) || (setup.height === "custom" ? { id: "custom" } : HEIGHTS[0]);
  const facing = flat(a.facing || [0, 0, 1]);
  if (type === "pov") {
    const c = a.head || a.feet, eye = [c[0] + facing[0] * 0.12, a.feet[1] + a.h * BODY.eyes, c[2] + facing[2] * 0.12];
    const el = hgt.el != null && Math.abs(hgt.el) < 80 ? -hgt.el : 0;      // looking down from a high angle, up from a low one
    const dir = rotY(facing, 0), t = [eye[0] + dir[0] * 5 * Math.cos(el * D2R), eye[1] + 5 * Math.sin(el * D2R), eye[2] + dir[2] * 5 * Math.cos(el * D2R)];
    return { pose: { p: eye, t, roll, lens: lens0 }, note: "Through the subject's eyes" };
  }
  if (type === "ots") {
    const b = bodies.b;
    if (!b) throw new Error("Over the shoulder needs a second person: pick one under “With”");
    const ab = flat([a.feet[0] - b.feet[0], 0, a.feet[2] - b.feet[2]]);
    const right = [-ab[2], 0, ab[0]], side = setup.ots === "left" ? -1 : 1;
    const p = [b.feet[0] - ab[0] * 0.55 + right[0] * 0.3 * side, b.feet[1] + b.h * 0.9, b.feet[2] - ab[2] * 0.55 + right[2] * 0.3 * side];
    const r = region(a, setup.size || "MCU"), t = [a.feet[0], r.aim[1], a.feet[2]];
    const dist = Math.hypot(t[0] - p[0], t[1] - p[1], t[2] - p[2]);
    const span = Math.max(...r.pts.map(q => q[1])) - Math.min(...r.pts.map(q => q[1]));
    const lens = Math.min(300, Math.max(12, lensForVfov(2 * Math.atan(span / 2 / Math.max(dist - a.d / 2, 0.2)), aspect)));
    const facing2 = flat(b.facing || [0, 0, 1]), faces = facing2[0] * ab[0] + facing2[2] * ab[2] > 0.5;
    return { pose: { p, t, roll, lens: Math.round(lens) }, note: `Past ${side > 0 ? "the right" : "the left"} shoulder; the lens sets the size${faces ? "" : " · works best with the two facing each other"}` };
  }
  let reg;
  if (type === "two" && bodies.b) reg = union([region(a, setup.size || "MS"), region(bodies.b, setup.size || "MS")]);
  else if (type === "group" && (bodies.all || []).length > 1) reg = union(bodies.all.map(x => region(x, setup.size || "FS")));
  else reg = region(a, setup.size || "MCU", type);
  // which way the camera looks from: the setup's side of the subject, or for two people, across the line between them
  // the side: the setup's, or (none set) where the camera already is
  let h = setup.side == null && bodies.cam ? flat([bodies.cam[0] - a.feet[0], 0, bodies.cam[2] - a.feet[2]]) : rotY(facing, setup.side || 0);
  if ((type === "two" || type === "group") && bodies.b) {     // across the line between the two, on the side they face
    const ab = flat([bodies.b.feet[0] - a.feet[0], 0, bodies.b.feet[2] - a.feet[2]]), fb = flat(bodies.b.facing || facing);
    const sum = [facing[0] + fb[0], 0, facing[2] + fb[2]], facingBoth = Math.hypot(sum[0], sum[2]) > 0.2;
    const want = setup.side == null && bodies.cam ? [bodies.cam[0] - a.feet[0], 0, bodies.cam[2] - a.feet[2]] : facingBoth ? rotY(flat(sum), setup.side || 0) : h;
    const perp = [-ab[2], 0, ab[0]];
    h = perp[0] * want[0] + perp[2] * want[2] < 0 ? perp.map(v => -v) : perp;
  }
  const aim = reg.aim, ref = facing;        // looking straight down, the subject's front is the top of the picture
  const dirFor = el => { const c = Math.cos(el * D2R), s = Math.sin(el * D2R); return [-(h[0] * c), -s, -(h[2] * c)]; };
  let el, d, lens = lens0, note = "";
  if (hgt.el != null) {
    el = hgt.el;
    d = fitDistance(reg.pts, aim, dirFor(el), lens, aspect, ref) * (hgt.far || 1);
    if (hgt.minY != null) {               // worm's-eye: the lens can't go under the floor, so it goes wider to fit
      const room = (aim[1] - a.feet[1] - hgt.minY) / Math.sin(-el * D2R);
      for (let i = 0; i < 6 && d > room && lens > 8; i++) {
        lens = Math.max(8, Math.floor(lens * room / d));
        d = fitDistance(reg.pts, aim, dirFor(el), lens, aspect, ref);
      }
      if (lens !== lens0) note = ` · went to ${lens} mm to fit`;
    }
  } else {
    const camY = hgt.abs != null ? a.feet[1] + hgt.abs : hgt.at != null ? a.feet[1] + hgt.at * a.h : (setup.camY ?? aim[1]);
    el = 0;
    for (let i = 0; i < 6; i++) {            // the tilt depends on the distance and the distance on the tilt
      d = fitDistance(reg.pts, aim, dirFor(el), lens, aspect, ref);
      const k = Math.max(-0.98, Math.min(0.98, (camY - aim[1]) / d));
      el = Math.asin(k) / D2R;
    }
    d = fitDistance(reg.pts, aim, dirFor(el), lens, aspect, ref);
  }
  // no closer than arm's length to a face: a tighter shot takes a longer lens instead, as a DP would
  const minD = a.person && !hgt.minY ? MIN_FACE_DISTANCE : 0;
  if (d < minD) { lens = Math.min(300, Math.round(lens * minD / d)); d = fitDistance(reg.pts, aim, dirFor(el), lens, aspect, ref); note += ` · ${lens} mm to stay ${minD} m away`; }
  const dir = dirFor(el), p = [aim[0] - dir[0] * d, aim[1] - dir[1] * d, aim[2] - dir[2] * d];
  if (hgt.minY != null) p[1] = Math.max(p[1], a.feet[1] + hgt.minY);     // never under the floor
  const pose = { p, t: aim.slice(), roll, lens };
  if (Math.abs(dir[1]) > 0.995) pose.up = ref.slice();
  return { pose, note: `${d.toFixed(1)} m from the subject${note}` };
}

/* ---------------------------------------------------------------- poses and cameras */
export function poseDir(pose) { return V(pose.t).sub(V(pose.p)).normalize(); }
export function poseDist(pose) { return V(pose.t).distanceTo(V(pose.p)); }

/* Puts a three.js camera at a pose (lens on a 36 mm sensor, like Blender). */
export function applyPose(cam, pose) {
  const { f, right, up } = basis(A(poseDir(pose)), pose.roll || 0, pose.up);
  cam.position.set(...pose.p);
  cam.quaternion.setFromRotationMatrix(new T.Matrix4().makeBasis(right, up, f.clone().negate()));
  if (cam.isPerspectiveCamera && pose.lens) { cam.filmGauge = SENSOR; cam.setFocalLength(pose.lens); }
}

/* The pose of a camera (three.js: looks down -Z), looking at a point `dist` ahead. Roll is read against world up. */
export function poseOf(cam, dist = 5, lens) {
  cam.updateMatrixWorld();
  const p = new T.Vector3(), q = new T.Quaternion(), s = new T.Vector3();
  cam.matrixWorld.decompose(p, q, s);
  const f = new T.Vector3(0, 0, -1).applyQuaternion(q), up = new T.Vector3(0, 1, 0).applyQuaternion(q);
  const b = basis(A(f), 0), roll = Math.abs(f.y) > 0.995 ? 0 : Math.atan2(up.dot(b.right), up.dot(b.up)) / D2R;
  return { p: A(p), t: A(p.clone().addScaledVector(f, dist)), roll: Math.round(-roll * 100) / 100, lens: lens || (cam.getFocalLength ? Math.round(cam.getFocalLength() * 10) / 10 : 35) };
}

/* Turns a pose on the spot: pan degrees to the left (+) or right (-), tilt degrees up (+) or down (-). The camera stays
   where it is; what it looks at moves (it no longer has to be the subject). */
export function aim(pose, pan = 0, tilt = 0) {
  const d = A(poseDir(pose)), dist = poseDist(pose) || 5;
  const h = rotY(d, pan), el = Math.max(-89, Math.min(89, Math.asin(Math.max(-1, Math.min(1, h[1]))) / D2R + tilt)) * D2R, f = flat(h);
  const t = [pose.p[0] + f[0] * Math.cos(el) * dist, pose.p[1] + Math.sin(el) * dist, pose.p[2] + f[2] * Math.cos(el) * dist];
  const out = { ...pose, t };
  if (Math.abs(Math.sin(el)) < 0.995) delete out.up;
  return out;
}

/* Moves a pose in its own axes (metres): right, up (world up) and forward, keeping where it looks relative to it. */
export function slide(pose, right = 0, up = 0, forward = 0) {
  const { f, right: r } = basis(A(poseDir(pose)));
  const d = [0, 1, 2].map(i => r.getComponent(i) * right + (i === 1 ? up : 0) + f.getComponent(i) * forward);
  return { ...pose, p: pose.p.map((v, i) => v + d[i]), t: pose.t.map((v, i) => v + d[i]) };
}

/* Moves the camera itself, as on a dolly or a crane, without turning it (where it looks moves with it): right (+) or left
   (-) and in (+) or out (-) along the floor, up (+) or down (-) straight up. Tilted or not, in/out never changes the height.
   Looking straight down, "in" is the top of the picture. */
export function track(pose, right = 0, up = 0, forward = 0) {
  const { fl, r } = floorAxes(pose), m = [0, 1, 2].map(i => r[i] * right + fl[i] * forward + (i === 1 ? up : 0));
  return { ...pose, p: pose.p.map((v, i) => v + m[i]), t: pose.t.map((v, i) => v + m[i]) };
}
/* Where `to` is from `from` in `from`'s own floor axes: { right, up, forward } in metres (what a manual move travelled). */
export function travel(from, to) {
  const { fl, r } = floorAxes(from), m = to.p.map((v, i) => v - from.p[i]);
  return { right: m[0] * r[0] + m[2] * r[2], up: m[1], forward: m[0] * fl[0] + m[2] * fl[2] };
}
/* A pose's forward along the floor (unit) and the floor's right of it. */
function floorAxes(pose) {
  const d = A(poseDir(pose));
  let fl = [d[0], 0, d[2]], l = Math.hypot(fl[0], fl[2]);
  if (l < 1e-3) { const u = pose.up || [0, 0, -1]; fl = [u[0], 0, u[2]]; l = Math.hypot(fl[0], fl[2]) || 1; }
  fl = [fl[0] / l, 0, fl[2] / l];
  return { fl, r: [-fl[2], 0, fl[0]] };
}

/* ---------------------------------------------------------------- moves */
/* The end of a preset move, from its start. */
export function presetEnd(A0, type, amount) {
  const m = moveInfo(type), amt = amount ?? m.amt, B = { p: A0.p.slice(), t: A0.t.slice(), roll: A0.roll || 0, lens: A0.lens };
  const f = poseDir(A0), dist = poseDist(A0), { right } = basis(A(f));
  const add = (v, dir, k) => v.map((x, i) => x + dir[i] * k);
  switch (type) {
    case "push": B.p = add(A0.p, A(f), dist * amt); break;
    case "pull": B.p = add(A0.p, A(f), -dist * amt); break;
    case "truck_l": case "truck_r": { const k = type === "truck_l" ? -amt : amt; B.p = add(A0.p, A(right), k); B.t = add(A0.t, A(right), k); break; }
    case "ped_up": case "ped_down": { const k = type === "ped_up" ? amt : -amt; B.p = add(A0.p, [0, 1, 0], k); B.t = add(A0.t, [0, 1, 0], k); break; }
    case "crane_up": B.p = add(A0.p, [0, 1, 0], amt); break;
    case "crane_down": B.p = add(A0.p, [0, 1, 0], -amt); break;
    case "pan_l": case "pan_r": case "whip_l": case "whip_r": { const k = /_l$/.test(type) ? amt : -amt; B.t = add(A0.p, rotY(A(f), k), dist); break; }
    case "tilt_u": case "tilt_d": {
      const el = Math.asin(Math.max(-1, Math.min(1, f.y))) / D2R + (type === "tilt_u" ? amt : -amt), c = Math.min(89, Math.max(-89, el)) * D2R;
      const h = flat(A(f)); B.t = [A0.p[0] + h[0] * Math.cos(c) * dist, A0.p[1] + Math.sin(c) * dist, A0.p[2] + h[2] * Math.cos(c) * dist]; break;
    }
    case "arc_l": case "arc_r": { const r = A0.p.map((x, i) => x - A0.t[i]), k = type === "arc_l" ? -amt : amt; B.p = add(A0.t, rotY(r, k), 1); break; }
    case "zoom_in": B.lens = A0.lens * amt; break;
    case "zoom_out": B.lens = A0.lens / amt; break;
    case "vertigo_in": B.p = add(A0.p, A(f), dist * amt); B.lens = A0.lens * (1 - amt); break;
    case "vertigo_out": B.p = add(A0.p, A(f), -dist * amt); B.lens = A0.lens * (1 + amt); break;
  }
  if (B.lens) B.lens = Math.round(Math.min(400, Math.max(6, B.lens)) * 10) / 10;
  return B;
}

/* ---------------------------------------------------------------- a shot and its move, in time
   A Studio camera's move has keyframes: it starts at `at` and lasts `dur` (s on the set's timeline). Its `shot` is the
   stretch of timeline the camera plays (and a movie records): [from, to], holding the move. Before the move's start
   keyframe the camera holds still at the start, after its end at the end: a 10 s shot can push in from 3 s to 10 s.
   A move saved before shots existed has none: its shot is the move. These keep a move's numbers tidy: the shot always
   holds the move, nothing is shorter than MIN_TIME, nothing starts before 0. */
export const MIN_TIME = 0.1;
const r3 = v => Math.round(v * 1000) / 1000;
export function shotOf(m) {
  const a = m.at || 0, b = a + (m.dur || 0), s = Array.isArray(m.shot) && m.shot.length === 2 && m.shot.every(Number.isFinite) ? m.shot : null;
  return s ? [Math.min(s[0], a), Math.max(s[1], b)] : [a, b];
}
function settle(m, a, b, s0, s1) {        // the move a..b in the shot s0..s1, tidied, written back
  a = Math.max(0, a); b = Math.max(a + MIN_TIME, b);
  s0 = Math.max(0, Math.min(s0, a)); s1 = Math.max(s1, b, s0 + MIN_TIME);
  const ra = r3(a), rb = r3(b);            // the ends to the ms, the length from them (so at + dur is the end)
  m.at = ra; m.dur = r3(rb - ra); m.shot = [r3(s0), Math.max(r3(s1), rb)];
  return m;
}
/* The move's keyframes at a and b (either may be null: it stays); the shot grows to hold them, never shrinks. */
export function setKeyTimes(m, a, b) {
  const [s0, s1] = shotOf(m), a0 = m.at || 0, b0 = a0 + (m.dur || 0);
  return settle(m, a ?? a0, b ?? b0, s0, s1);
}
/* The whole move later or sooner by dt, keeping its length; the shot grows to hold it. */
export function slideKeys(m, dt) {
  const a = Math.max(0, (m.at || 0) + dt);
  return setKeyTimes(m, a, a + (m.dur || 0));
}
/* The shot starts at t: the shot and its move go there together. */
export function moveShot(m, t) {
  const [s0, s1] = shotOf(m), d = Math.max(0, t) - s0;
  return settle(m, (m.at || 0) + d, (m.at || 0) + (m.dur || 0) + d, s0 + d, s1 + d);
}
/* The shot's start and end (either null: it stays). A move that filled the shot still does; otherwise the keyframes stay
   where they are, and an end that comes in past one takes it along. */
export function setShotEdges(m, from, to) {
  const [s0, s1] = shotOf(m), a0 = m.at || 0, b0 = a0 + (m.dur || 0), fills = Math.abs(a0 - s0) < 1e-3 && Math.abs(b0 - s1) < 1e-3;
  const n0 = Math.max(0, from ?? s0), n1 = Math.max(n0 + MIN_TIME, to ?? s1);
  if (fills) return settle(m, n0, n1, n0, n1);
  let a = Math.min(Math.max(a0, n0), n1 - MIN_TIME), b = Math.min(Math.max(b0, a + MIN_TIME), n1);
  if (b - a < MIN_TIME) a = b - MIN_TIME;
  return settle(m, a, b, n0, n1);
}
/* The shot lasts `len` from where it starts (Moves › Shot length). */
export const setShotLength = (m, len) => setShotEdges(m, null, shotOf(m)[0] + Math.max(MIN_TIME, len));

export const ease = (s, kind) => kind === "linear" ? s : kind === "in" ? s * s : kind === "out" ? 1 - (1 - s) * (1 - s) : s * s * (3 - 2 * s);

/* Handheld: a slow wander plus a little shake, the same every time for the same moment. */
export function shake(t, k) {
  const n = (a, b, c) => Math.sin(t * a + b) * 0.6 + Math.sin(t * a * 2.3 + c) * 0.3 + Math.sin(t * a * 5.1 + b + c) * 0.1;
  return { yaw: n(0.9, 1.3, 4.1) * 0.7 * k, pitch: n(1.1, 2.9, 0.7) * 0.5 * k, roll: n(0.7, 5.3, 2.2) * 0.6 * k, p: [n(0.8, 0.1, 3) * 0.012 * k, n(1.3, 2.1, 1) * 0.01 * k, n(0.6, 4.2, 2) * 0.012 * k] };
}

/* Where a camera with a move is at time t. move: { type, A, B, at, dur, ease, shake, follow } (follow is added by the viewer
   as the subject's offset since the move was set). */
export function poseAt(move, t, offset) {
  const A0 = move.A, B0 = move.B || move.A, dur = Math.max(0.01, move.dur || 5);
  const s = ease(Math.max(0, Math.min(1, (t - (move.at || 0)) / dur)), move.ease);
  const type = move.type || "static";
  let p, tt, lens = A0.lens + (B0.lens - A0.lens) * s;
  if (/^arc_/.test(type)) {
    const r0 = A0.p.map((x, i) => x - A0.t[i]), r1 = B0.p.map((x, i) => x - B0.t[i]);
    const a0 = Math.atan2(r0[0], r0[2]);
    const da = (move.amount ?? moveInfo(type).amt) * D2R * (type === "arc_l" ? -1 : 1);   // more than half a turn works too
    const rad = Math.hypot(r0[0], r0[2]) + (Math.hypot(r1[0], r1[2]) - Math.hypot(r0[0], r0[2])) * s, ang = a0 + da * s;
    tt = A0.t.map((x, i) => x + (B0.t[i] - x) * s);
    p = [tt[0] + Math.sin(ang) * rad, tt[1] + r0[1] + (r1[1] - r0[1]) * s, tt[2] + Math.cos(ang) * rad];
  } else if (/^(pan|tilt|whip)_/.test(type)) {
    p = A0.p.slice();
    const d0 = poseDir(A0), d1 = poseDir(B0), q = new T.Quaternion().setFromUnitVectors(d0, d1);
    const d = d0.clone().applyQuaternion(new T.Quaternion().slerp(q, s)), dist = poseDist(A0);
    tt = A(V(p).addScaledVector(d, dist));
  } else if (type === "custom") {       // keyframes: the camera travels, and turns from where it looked to where it looks
    p = A0.p.map((x, i) => x + (B0.p[i] - x) * s);
    const d0 = poseDir(A0), d1 = poseDir(B0), q = new T.Quaternion().setFromUnitVectors(d0, d1);
    const d = d0.clone().applyQuaternion(new T.Quaternion().slerp(q, s)), dist = poseDist(A0) + (poseDist(B0) - poseDist(A0)) * s;
    tt = A(V(p).addScaledVector(d, dist));
  } else {
    p = A0.p.map((x, i) => x + (B0.p[i] - x) * s);
    tt = A0.t.map((x, i) => x + (B0.t[i] - x) * s);
    if (/^vertigo_/.test(type)) {        // the lens follows the distance, so the subject keeps its size
      lens = A0.lens * Math.hypot(...tt.map((x, i) => x - p[i])) / Math.max(0.01, poseDist(A0));
    }
  }
  let roll = (A0.roll || 0) + ((B0.roll || 0) - (A0.roll || 0)) * s;
  if (offset) { p = p.map((x, i) => x + offset[i]); tt = tt.map((x, i) => x + offset[i]); }
  if (move.shake) {
    const k = shake(t, move.shake), f = V(tt).sub(V(p)), dist = f.length() || 1;
    const dir = A(f.normalize()), yawed = rotY(dir, k.yaw), el = Math.asin(Math.max(-1, Math.min(1, yawed[1]))) + k.pitch * D2R, hz = flat(yawed);
    p = p.map((x, i) => x + k.p[i]);
    tt = [p[0] + hz[0] * Math.cos(el) * dist, p[1] + Math.sin(el) * dist, p[2] + hz[2] * Math.cos(el) * dist];
    roll += k.roll;
  }
  const out = { p, t: tt, roll, lens: Math.round(lens * 100) / 100 };
  if (A0.up) out.up = A0.up;
  return out;
}

/* A line for the panel: what a camera setup is. */
export function describe(cam) {
  const s = cam.setup || {}, m = cam.move || {}, out = [];
  if (s.type && s.type !== "single") out.push((SHOT_TYPES.find(x => x.id === s.type) || {}).label);
  if (s.size && !["pov", "ots"].includes(s.type)) out.push(s.size);
  if (s.height && s.height !== "custom") out.push((HEIGHTS.find(x => x.id === s.height) || {}).label);
  if (s.roll) out.push(`Dutch ${Math.abs(s.roll)}°`);
  if (s.pan) out.push(`panned ${Math.abs(s.pan)}° ${s.pan > 0 ? "left" : "right"}`);
  if (s.tilt) out.push(`tilted ${Math.abs(s.tilt)}° ${s.tilt > 0 ? "up" : "down"}`);
  if (s.free) out.push("operated");
  if (m.type && m.type !== "static") out.push((moveInfo(m.type).label || "").toLowerCase());
  if (m.follow) out.push("following");
  if (m.shake) out.push("handheld");
  return out.filter(Boolean).join(" · ");
}
