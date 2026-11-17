// Previz camera maths (ComfyUI-Studio/web/previz/shots.js): framing, heights, sides, kinds of shot and moves.
// Run by tests/unit/test_previz_js.py (node --test). Checks what a camera operator would check in the viewfinder:
// everything the shot must hold is inside the frame, the frame is tight, the camera is where the setup says.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as T from "../../../ComfyUI-Studio/web/vendor/three/three.bundle.min.js";
import * as S from "../../../ComfyUI-Studio/web/previz/shots.js";

const person = (over = {}) => ({ feet: [0, 0, 0], h: 1.75, facing: [0, 0, 1], w: 0.5, d: 0.3, person: true, center: [0, 0.875, 0], ...over });
const near = (a, b, eps = 1e-6, msg) => assert.ok(Math.abs(a - b) <= eps, `${msg || ""} ${a} ≉ ${b} (±${eps})`);
const sub = (a, b) => a.map((v, i) => v - b[i]);
const dot = (a, b) => a.reduce((s, v, i) => s + v * b[i], 0);
const len = a => Math.hypot(...a);
const unit = a => { const l = len(a); return a.map(v => v / l); };

function cameraFor(pose, aspect) {
  const c = new T.PerspectiveCamera(40, aspect, 0.01, 1000);
  S.applyPose(c, pose); c.updateMatrixWorld(); c.updateProjectionMatrix();
  return c;
}
/* Where the region's points land in the frame (NDC, -1..1 is inside). */
function ndc(points, pose, aspect) {
  const c = cameraFor(pose, aspect);
  return points.map(p => new T.Vector3(...p).project(c));
}

test("lens and field of view follow Blender's 36 mm sensor fit", () => {
  for (const lens of [18, 35, 85]) {
    near(S.hfov(lens, 16 / 9), 2 * Math.atan(18 / lens), 1e-12, "landscape: the width is the sensor");
    near(S.hfov(lens, 2.39), S.hfov(lens, 1.3333), 1e-12);
    near(S.vfov(lens, 9 / 16), 2 * Math.atan(18 / lens), 1e-12, "portrait: the height is the sensor");
    near(S.lensForVfov(S.vfov(lens, 2.39), 2.39), lens, 1e-9);
  }
});

test("every shot size holds its part of the body, tightly, at every frame shape and side", () => {
  const body = person();
  for (const aspect of [2.39, 16 / 9, 1, 9 / 16]) {
    for (const side of [0, 45, 90, -135]) {
      for (const s of S.SHOT_SIZES) {
        const { pose } = S.solve({ size: s.id, side, height: "eye", lens: 35 }, { a: body }, aspect);
        const r = S.region({ ...body, head: undefined }, s.id);
        const pts = ndc(r.pts, pose, aspect);
        for (const v of pts) assert.ok(Math.abs(v.x) <= 1.0001 && Math.abs(v.y) <= 1.0001 && v.z < 1, `${s.id} @${aspect} side ${side}: out of frame ${v.x.toFixed(3)},${v.y.toFixed(3)}`);
        const edge = Math.max(...pts.map(v => Math.max(Math.abs(v.x), Math.abs(v.y))));
        assert.ok(edge > 0.9, `${s.id} @${aspect}: the frame isn't tight (${edge.toFixed(2)})`);
      }
    }
  }
});

test("wider shot sizes put the camera further away", () => {
  const d = S.SHOT_SIZES.map(s => { const { pose } = S.solve({ size: s.id, height: "eye", lens: 24 }, { a: person() }, 2.39); return { id: s.id, d: len(sub(pose.p, pose.t)), lens: pose.lens }; });
  for (let i = 1; i < d.length; i++) {
    const closer = d[i].d < d[i - 1].d || d[i].lens > d[i - 1].lens;     // tighter: nearer, or (at arm's length) a longer lens
    assert.ok(closer, `${d[i].id} should be tighter than ${d[i - 1].id}: ${JSON.stringify(d.slice(i - 1, i + 1))}`);
  }
});

test("close shots stay at arm's length from a face and take a longer lens", () => {
  const { pose, note } = S.solve({ size: "ECU", height: "eye", lens: 35 }, { a: person({ head: [0, 1.5, 0] }) }, 2.39);
  assert.ok(len(sub(pose.p, [0, 1.64, 0.06])) >= S.MIN_FACE_DISTANCE - 0.12, "too close to the face");
  assert.ok(pose.lens > 35, "went to a longer lens");
  assert.match(note, /mm to stay/);
});

test("close shots aim at the head, wide ones at the body", () => {
  const body = person({ feet: [0, 0, 0], head: [0.3, 1.5, 0.1] });
  const cu = S.solve({ size: "CU", height: "eye", lens: 50 }, { a: body }, 2.39).pose, ws = S.solve({ size: "WS", height: "eye", lens: 50 }, { a: body }, 2.39).pose;
  assert.ok(Math.abs(cu.t[0] - 0.3) < 0.01, "CU aims at the head");
  assert.ok(Math.abs(ws.t[0]) < 0.01, "WS aims at the body");
});

test("camera heights: the lens at that point of the body; angles: tilted by that much", () => {
  const body = person();
  for (const h of S.HEIGHTS.filter(x => x.at != null || x.abs != null)) {
    const { pose } = S.solve({ size: "MS", height: h.id, lens: 35 }, { a: body }, 2.39);
    const want = h.abs != null ? h.abs : h.at * 1.75;
    near(pose.p[1], want, 0.02, h.id);
  }
  for (const h of S.HEIGHTS.filter(x => x.el != null && x.id !== "worm")) {
    const { pose } = S.solve({ size: "MS", height: h.id, lens: 35 }, { a: body }, 2.39);
    const d = sub(pose.t, pose.p), el = Math.asin(-d[1] / len(d)) * 180 / Math.PI;
    near(el, h.el, 0.01, h.id);
  }
  const o = S.solve({ size: "MS", height: "overhead", lens: 35 }, { a: body }, 2.39).pose;
  assert.ok(o.up, "straight down: the picture's up is the subject's front");
  assert.ok(len([o.p[0] - o.t[0], 0, o.p[2] - o.t[2]]) < 0.05, "overhead is above");
});

test("worm's-eye never goes under the floor: it goes wider instead", () => {
  const { pose } = S.solve({ size: "MS", height: "worm", lens: 35 }, { a: person({ feet: [2, 0.5, -1] }) }, 2.39);
  assert.ok(pose.p[1] >= 0.5 + 0.05 - 1e-6, `under the floor: ${pose.p[1]}`);
  assert.ok(pose.lens < 35);
});

test("sides: front, the subject's left, back, and where the camera already is", () => {
  const body = person({ facing: [1, 0, 0] }), left = [0, 0, -1];     // facing +X, its left is -Z
  const at = side => unit(sub(S.solve({ size: "MS", side, height: "eye", lens: 35 }, { a: body }, 2.39).pose.p, [0, 0, 0]).map((v, i) => i === 1 ? 0 : v));
  assert.ok(dot(at(0), [1, 0, 0]) > 0.99, "front");
  assert.ok(dot(at(90), left) > 0.99, "profile left");
  assert.ok(dot(at(180), [-1, 0, 0]) > 0.99, "back");
  assert.ok(dot(at(45), unit([1, 0, -1])) > 0.99, "three-quarter left");
  const keep = S.solve({ size: "MS", side: null, height: "eye", lens: 35 }, { a: body, cam: [-3, 1.6, 4] }, 2.39).pose;
  assert.ok(dot(unit([keep.p[0], 0, keep.p[2]]), unit([-3, 0, 4])) > 0.99, "no side set: stays on the camera's side");
});

test("dutch angle rolls the picture (a positive roll lifts the camera's right side)", () => {
  const { pose } = S.solve({ size: "MS", height: "eye", lens: 35, roll: 20 }, { a: person() }, 2.39);
  const c = cameraFor(pose, 2.39), right = new T.Vector3(1, 0, 0).applyQuaternion(c.quaternion);
  near(Math.asin(right.y) * 180 / Math.PI, 20, 0.5);
  const back = S.poseOf(c, len(sub(pose.t, pose.p)));
  near(back.roll, 20, 0.05, "poseOf reads the roll back");
});

test("over the shoulder: behind the second person, past their shoulder, onto the subject's face", () => {
  const a = person({ feet: [0, 0, 0], facing: [0, 0, 1] }), b = person({ feet: [0, 0, 2], facing: [0, 0, -1] });
  const { pose, note } = S.solve({ type: "ots", size: "MCU" }, { a, b }, 2.39);
  assert.ok(pose.p[2] > 2.3, "behind B (further from A than B is)");
  assert.ok(Math.abs(pose.p[0]) > 0.2, "off to one side: past the shoulder");
  near(pose.p[1], 1.75 * 0.9, 0.01, "at shoulder height");
  assert.ok(pose.t[1] > 1.4, "aims at A's face");
  assert.ok(pose.lens >= 12 && pose.lens <= 300);
  assert.doesNotMatch(note, /facing each other/);
  const other = S.solve({ type: "ots", size: "MCU", ots: "left" }, { a, b }, 2.39).pose;
  assert.ok(Math.sign(other.p[0]) === -Math.sign(pose.p[0]), "the other shoulder");
  assert.throws(() => S.solve({ type: "ots" }, { a }, 2.39), /second person/);
  const sideBySide = S.solve({ type: "ots", size: "MCU" }, { a, b: person({ feet: [1, 0, 0] }) }, 2.39);
  assert.match(sideBySide.note, /facing each other/);
});

test("two-shot and group hold everyone", () => {
  const a = person({ feet: [-1, 0, 0], facing: [0, 0, 1] }), b = person({ feet: [1.2, 0, 0.3], facing: [0, 0, 1] }), c = person({ feet: [0, 0, -2], facing: [0, 0, 1] });
  for (const [type, bodies] of [["two", { a, b }], ["group", { a, b, all: [a, b, c] }]]) {
    const { pose } = S.solve({ type, size: "FS", lens: 35, height: "eye" }, bodies, 2.39);
    for (const x of bodies.all || [a, b]) for (const v of ndc(S.region(x, "FS").pts, pose, 2.39)) assert.ok(Math.abs(v.x) <= 1.0001 && Math.abs(v.y) <= 1.0001, type);
    assert.ok(pose.p[2] > 0, `${type}: in front of them`);
  }
});

test("POV looks out from the subject's eyes, where it faces", () => {
  const { pose } = S.solve({ type: "pov", lens: 24 }, { a: person({ facing: [1, 0, 0] }) }, 2.39);
  near(pose.p[1], 1.75 * S.BODY.eyes, 1e-9);
  assert.ok(dot(unit(sub(pose.t, pose.p)), [1, 0, 0]) > 0.999);
});

test("an object (an insert) is framed by its box", () => {
  const cup = { feet: [0, 0.75, 0], h: 0.12, facing: [0, 0, 1], w: 0.09, d: 0.09, person: false, center: [0, 0.81, 0] };
  const { pose } = S.solve({ size: "CU", type: "insert", lens: 50, height: "high" }, { a: cup }, 2.39);
  for (const v of ndc(S.region(cup, "CU", "insert").pts, pose, 2.39)) assert.ok(Math.abs(v.x) <= 1.0001 && Math.abs(v.y) <= 1.0001);
  assert.ok(len(sub(pose.p, pose.t)) < 1.5);
});

test("pose -> camera -> pose keeps the position, the aim and the lens", () => {
  const pose = { p: [1, 2, 3], t: [-2, 1, 0], roll: -7, lens: 50 };
  const back = S.poseOf(cameraFor(pose, 2.39), len(sub(pose.t, pose.p)));
  back.p.forEach((v, i) => near(v, pose.p[i], 1e-6)); back.t.forEach((v, i) => near(v, pose.t[i], 1e-5));
  near(back.roll, -7, 0.02); near(back.lens, 50, 0.05);
});

/* ---------------------------------------------------------------- moves */
const A0 = { p: [0, 1.6, 4], t: [0, 1.5, 0], roll: 0, lens: 35 };
test("preset moves end where they say", () => {
  const d0 = len(sub(A0.t, A0.p));
  const push = S.presetEnd(A0, "push", 0.25); near(len(sub(push.t, push.p)), d0 * 0.75, 1e-9, "push in by a quarter");
  const pull = S.presetEnd(A0, "pull", 0.5); near(len(sub(pull.t, pull.p)), d0 * 1.5, 1e-9);
  const tl = S.presetEnd(A0, "truck_l", 2); assert.ok(tl.p[0] < -1.99 && tl.t[0] < -1.99, "truck left: camera and aim slide left");
  const pu = S.presetEnd(A0, "ped_up", 1); near(pu.p[1], 2.6, 1e-9); near(pu.t[1], 2.5, 1e-9);
  const cu = S.presetEnd(A0, "crane_up", 3); near(cu.p[1], 4.6, 1e-9); assert.deepEqual(cu.t, A0.t, "crane: keeps the subject");
  const pl = S.presetEnd(A0, "pan_l", 90); assert.deepEqual(pl.p, A0.p); assert.ok(pl.t[0] < -3.9, "pan left turns to the left");
  const tu = S.presetEnd(A0, "tilt_u", 30); assert.ok(tu.t[1] > A0.t[1] + 1.5);
  const al = S.presetEnd(A0, "arc_l", 90); near(len(sub(al.p, al.t)), d0, 1e-9, "arc keeps the distance"); assert.ok(al.p[0] < -3.9, "arc left goes to the camera's left");
  near(S.presetEnd(A0, "zoom_in", 2).lens, 70, 1e-9); near(S.presetEnd(A0, "zoom_out", 2).lens, 17.5, 1e-9);
});

test("moves play over time: before, during, after", () => {
  const m = { type: "push", A: A0, B: S.presetEnd(A0, "push", 0.5), at: 10, dur: 4, ease: "linear" };
  assert.deepEqual(S.poseAt(m, 0).p, A0.p); assert.deepEqual(S.poseAt(m, 10).p, A0.p);
  near(S.poseAt(m, 12).p[2], (A0.p[2] + m.B.p[2]) / 2, 1e-9, "halfway at half time");
  assert.deepEqual(S.poseAt(m, 99).p.map(v => +v.toFixed(9)), m.B.p.map(v => +v.toFixed(9)));
  const smooth = S.poseAt({ ...m, ease: "smooth" }, 10.4).p[2], lin = S.poseAt(m, 10.4).p[2];
  assert.ok(Math.abs(smooth - A0.p[2]) < Math.abs(lin - A0.p[2]), "eases in");
});

test("a dolly zoom keeps the subject the same size the whole way", () => {
  const B = S.presetEnd(A0, "vertigo_in", 0.5), m = { type: "vertigo_in", A: A0, B, at: 0, dur: 1, ease: "linear" };
  const k0 = A0.lens / len(sub(A0.t, A0.p));
  for (const t of [0, 0.25, 0.5, 0.9, 1]) { const p = S.poseAt(m, t); near(p.lens / len(sub(p.t, p.p)), k0, 0.01, `t=${t}`); }
});

test("an arc goes round (a full orbit too), a pan turns on the spot", () => {
  const m = { type: "arc_r", A: A0, B: S.presetEnd(A0, "arc_r", 360), amount: 360, at: 0, dur: 1, ease: "linear" };
  const half = S.poseAt(m, 0.5); assert.ok(half.p[2] < -3.9, "halfway round a full orbit: on the far side");
  near(len(sub(half.p, half.t)), len(sub(A0.p, A0.t)), 1e-6, "same distance");
  const pan = { type: "pan_r", A: A0, B: S.presetEnd(A0, "pan_r", 60), at: 0, dur: 1, ease: "linear" };
  const mid = S.poseAt(pan, 0.5); assert.deepEqual(mid.p, A0.p);
  const ang = Math.acos(dot(unit(sub(mid.t, mid.p)), unit(sub(A0.t, A0.p)))) * 180 / Math.PI; near(ang, 30, 0.05, "half the pan at half time");
});

test("follow moves with the subject; handheld wanders a little, the same way every time", () => {
  const m = { type: "static", A: A0, B: A0, at: 0, dur: 1 };
  const f = S.poseAt(m, 0.5, [2, 0, -1]); assert.deepEqual(f.p, [2, 1.6, 3]); assert.deepEqual(f.t, [2, 1.5, -1]);
  const h1 = S.poseAt({ ...m, shake: 1 }, 3.3), h2 = S.poseAt({ ...m, shake: 1 }, 3.3);
  assert.deepEqual(h1, h2);
  assert.ok(len(sub(h1.p, A0.p)) < 0.05 && len(sub(h1.p, A0.p)) > 0, "a small wobble");
  assert.ok(Math.abs(h1.roll) < 2);
});

test("the panel's summary of a camera", () => {
  assert.equal(S.describe({ setup: { size: "MCU", height: "low", roll: -15 }, move: { type: "push", follow: true } }), "MCU · Low angle · Dutch 15° · push in · following");
  assert.equal(S.describe({ setup: { type: "ots", size: "MCU" } }), "Over the shoulder");
});

/* ---------------------------------------------------------------- aiming off the subject, operating by hand */
test("pan and tilt turn the camera on the spot, off the subject", () => {
  const P = { p: [0, 1.6, 4], t: [0, 1.6, 0], roll: 0, lens: 35 };
  const l = S.aim(P, 30, 0); assert.deepEqual(l.p, P.p);
  assert.ok(l.t[0] < -1.9, "pan left looks to the camera's left");
  near(Math.acos(dot(unit(sub(l.t, l.p)), unit(sub(P.t, P.p)))) * 180 / Math.PI, 30, 1e-6);
  near(len(sub(l.t, l.p)), 4, 1e-9, "keeps its distance");
  const u = S.aim(P, 0, 20), d = unit(sub(u.t, u.p)); near(Math.asin(d[1]) * 180 / Math.PI, 20, 1e-6, "tilt up");
  const back = S.aim(S.aim(P, -25, 10), 25, -10); back.t.forEach((v, i) => near(v, P.t[i], 1e-9));
  near(Math.asin(unit(sub(S.aim(P, 0, 120).t, P.p))[1]) * 180 / Math.PI, 89, 1e-6, "never past straight up");
});

test("sliding moves the camera and what it looks at together, in its own axes", () => {
  const P = { p: [0, 1.6, 4], t: [0, 1.6, 0], roll: 0, lens: 35 };
  const s = S.slide(P, 1, 0.5, 2);       // right, up, forward
  assert.deepEqual(s.p.map(v => +v.toFixed(9)), [1, 2.1, 2]);
  assert.deepEqual(sub(s.t, s.p).map(v => +v.toFixed(9)), sub(P.t, P.p).map(v => +v.toFixed(9)));
});

test("a shot size has words for a prompt", () => {
  for (const s of S.SHOT_SIZES) assert.ok(/shot$|close-up$/.test(s.words), s.id);
  assert.equal(S.describe({ setup: { size: "MS", pan: -12, tilt: 5, free: true } }), "MS · panned 12° right · tilted 5° up · operated");
});

test("keyframes: a tilt is halfway at half time, a wide pan turns instead of cutting through", () => {
  const P = { p: [0, 1.6, 4], t: [0, 1.6, 0], roll: 0, lens: 35 };
  const m = { type: "custom", A: P, B: S.aim(P, 0, 20), at: 0, dur: 2, ease: "linear" };
  const pitch = x => Math.asin(unit(sub(x.t, x.p))[1]) * 180 / Math.PI;
  near(pitch(S.poseAt(m, 1)), 10, 1e-6); near(pitch(S.poseAt(m, 2)), 20, 1e-6);
  const wide = { type: "custom", A: P, B: S.aim(P, 120, 0), at: 0, dur: 1, ease: "linear" }, half = S.poseAt(wide, 0.5);
  near(Math.acos(dot(unit(sub(half.t, half.p)), unit(sub(P.t, P.p)))) * 180 / Math.PI, 60, 1e-6);
  near(len(sub(half.t, half.p)), 4, 1e-9, "keeps looking as far");
  const lens = { type: "custom", A: P, B: { ...S.slide(P, 2, 0, 0), lens: 85, roll: 10 }, at: 0, dur: 1, ease: "linear" };
  const q = S.poseAt(lens, 0.5); near(q.lens, 60, 1e-9); near(q.roll, 5, 1e-9); near(q.p[0], 1, 1e-9);
});

test("moving the camera by hand (dolly, crane) slides it without turning it", () => {
  const A = { p: [0, 1.6, 5], t: [0, 1.2, 0], roll: 4, lens: 35 };           // looking at -Z, tilted down a little
  const B = S.track(A, 1, 0.5, 2);                                            // 1 m right, 0.5 m up, 2 m in
  assert.deepEqual(B.p.map(v => +v.toFixed(6)), [1, 2.1, 3], "right is +X, up is up, in is along the floor (not down the tilt)");
  const dirA = sub(A.t, A.p), dirB = sub(B.t, B.p);
  dirA.forEach((v, i) => near(dirB[i], v, 1e-9, "it looks the same way"));
  assert.equal(B.roll, 4); assert.equal(B.lens, 35);
  const v = S.travel(A, B);
  near(v.right, 1, 1e-9); near(v.up, 0.5, 1e-9); near(v.forward, 2, 1e-9);
  // any heading: travel reads back what track did, in the camera's own axes
  for (const yaw of [0, 37, 90, 200, -120]) {
    const P = { p: [2, 1.5, -1], t: S.rotY([0, -0.3, -4], yaw).map((x, i) => x + [2, 1.5, -1][i]), roll: 0, lens: 50 };
    const Q = S.track(P, -0.7, 1.2, -0.4), w = S.travel(P, Q);
    near(w.right, -0.7, 1e-9, `right @${yaw}`); near(w.up, 1.2, 1e-9); near(w.forward, -0.4, 1e-9, `in @${yaw}`);
    near(Q.p[1] - P.p[1], 1.2, 1e-9, "in/out never changes the height");
  }
});

test("looking straight down, in is the top of the picture", () => {
  const P = { p: [0, 10, 0], t: [0, 0, 0], roll: 0, lens: 24, up: [0, 0, -1] };
  const Q = S.track(P, 0, 0, 1);
  assert.deepEqual(Q.p.map(v => +v.toFixed(6)), [0, 10, -1]);
  assert.ok(Q.p.every(Number.isFinite));
});

test("a shot holds its move: moving the keyframes never shortens it", () => {
  const m = { type: "push", at: 0, dur: 10 };                   // a push in of 10 s
  assert.deepEqual(S.shotOf(m), [0, 10], "a move saved before shots: its shot is the move");
  S.setKeyTimes(m, 3, null);                                    // the push starts at 3 s…
  assert.deepEqual([m.at, m.dur, m.shot], [3, 7, [0, 10]], "…the shot is still 10 s, still for the first 3");
  S.setKeyTimes(m, null, 8);
  assert.deepEqual([m.at, m.dur, m.shot], [3, 5, [0, 10]], "the end comes in: the shot stays");
  S.setKeyTimes(m, null, 12);
  assert.deepEqual(S.shotOf(m), [0, 12], "pushed past the shot's end: the shot grows");
  S.slideKeys(m, -5);
  assert.deepEqual([m.at, m.dur, m.shot], [0, 9, [0, 12]], "slid back: never before 0, keeps its length, the shot stays");
});

test("the shot's length and start", () => {
  const filled = { at: 2, dur: 4 };
  S.setShotLength(filled, 10);
  assert.deepEqual([filled.at, filled.dur, filled.shot], [2, 10, [2, 12]], "a move that filled its shot stretches with it");
  const inside = { at: 3, dur: 4, shot: [0, 10] };
  S.setShotLength(inside, 12);
  assert.deepEqual([inside.at, inside.dur, inside.shot], [3, 4, [0, 12]], "keyframes placed in the shot stay where they are");
  S.setShotLength(inside, 5);
  assert.deepEqual([inside.at, inside.dur, inside.shot], [3, 2, [0, 5]], "a shorter shot takes the end keyframe along");
  S.setShotLength(inside, 1);
  assert.ok(inside.at >= 0 && inside.dur >= S.MIN_TIME && inside.at + inside.dur <= inside.shot[1] + 1e-9 && inside.shot[1] === 1, JSON.stringify(inside));
  const moved = { at: 3, dur: 4, shot: [0, 10] };
  S.moveShot(moved, 5);
  assert.deepEqual([moved.at, moved.dur, moved.shot], [8, 4, [5, 15]], "the shot starts later: its move goes along");
  S.setShotEdges(moved, 9, null);
  assert.deepEqual([moved.at, moved.dur, moved.shot], [9, 3, [9, 15]], "its start comes in past the start keyframe: takes it along");
  assert.deepEqual(S.shotOf({ at: 1, dur: 2, shot: "junk" }), [1, 3], "a broken shot is the move");
  assert.deepEqual(S.shotOf({ at: 1, dur: 2, shot: [2, 2.5] }), [1, 3], "a shot always holds its move");
});

test("before its start keyframe the camera holds still, after its end too", () => {
  const A = { p: [0, 1.6, 5], t: [0, 1.6, 0], roll: 0, lens: 35 }, B = S.presetEnd(A, "push", 0.5);
  const m = { type: "push", A, B, at: 3, dur: 7, shot: [0, 10] };
  assert.deepEqual(S.poseAt(m, 0).p, A.p); assert.deepEqual(S.poseAt(m, 2.9).p, A.p);
  assert.ok(S.poseAt(m, 6).p[2] < A.p[2] && S.poseAt(m, 6).p[2] > B.p[2], "moving in between");
  assert.deepEqual(S.poseAt(m, 10).p.map(v => +v.toFixed(9)), B.p.map(v => +v.toFixed(9)));
});

test("odd numbers stay tidy: a move that fills its shot keeps filling it", () => {
  for (const [a, b] of [[0.1234, 0.5678], [1.0005, 7.3335], [2.9999, 3.0004], [0, 0.05]]) {
    const m = { at: 0, dur: 1 };
    S.setShotEdges(m, a, b);                                   // a shot the move fills, at odd times
    const [s0, s1] = S.shotOf(m);
    assert.ok(Math.abs(m.at - s0) < 1e-9 && Math.abs(m.at + m.dur - s1) < 1e-9, `fills ${JSON.stringify(m)}`);
    S.setShotLength(m, 5.5555);
    assert.ok(Math.abs(m.at - m.shot[0]) < 1e-9 && Math.abs(m.at + m.dur - m.shot[1]) < 1e-9, `still fills ${JSON.stringify(m)}`);
    assert.ok(m.dur >= S.MIN_TIME && m.at >= 0);
  }
});
