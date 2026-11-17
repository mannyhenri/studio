// The Previz viewer module (ComfyUI-Studio/web/previz/viewer.js) loads (a syntax error anywhere in it would stop the
// whole viewer), and each camera's own stretch of the timeline is found where its keys (or its rig's) change.
// Run by tests/unit/test_previz_js.py (node --test).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as T from "../../../ComfyUI-Studio/web/vendor/three/three.bundle.min.js";
import * as V from "../../../ComfyUI-Studio/web/previz/viewer.js";

/* A baked track like a Blender export: a key every frame over the whole timeline, moving only between a and b. */
function baked(name, path, a, b, from, to, fps = 24, length = 10) {
  const times = [], values = [];
  for (let f = 1; f <= length * fps; f++) {
    const t = f / fps, s = Math.max(0, Math.min(1, (t - a) / (b - a)));
    times.push(t);
    values.push(...from.map((v, i) => v + (to[i] - v) * s));
  }
  return path === "quaternion" ? new T.QuaternionKeyframeTrack(`${name}.${path}`, times, values) : new T.VectorKeyframeTrack(`${name}.${path}`, times, values);
}
const tracksOf = list => { const m = new Map(); for (const tr of list) { const n = T.PropertyBinding.parseTrackName(tr.name).nodeName; m.set(n, (m.get(n) || []).concat([tr])); } return m; };

test("the viewer module loads and exports what app.js uses", () => {
  assert.equal(typeof V.mountViewer, "function");
  assert.equal(typeof V.webglAvailable, "function");
  assert.ok(V.ASPECTS.some(a => a[1] === "2.39:1"));
});

test("a camera's move is where its keys change, not the whole baked timeline", () => {
  const cam = new T.Object3D(); cam.name = "CAM_03";
  const r = V.moveRange(cam, tracksOf([baked("CAM_03", "position", 3, 5.5, [0, 1, 0], [2, 1, 0])]));
  assert.ok(r, "found");
  assert.ok(Math.abs(r[0] - 3) <= 1 / 24 + 1e-9 && Math.abs(r[1] - 5.5) <= 1 / 24 + 1e-9, JSON.stringify(r));
});

test("the rig that carries a camera counts, and turning counts as moving", () => {
  const rig = new T.Object3D(); rig.name = "G_CU_Rig";
  const cam = new T.Object3D(); cam.name = "G_CU"; rig.add(cam);
  const q0 = new T.Quaternion().toArray(), q1 = new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 1, 0), 0.5).toArray();
  const r = V.moveRange(cam, tracksOf([baked("G_CU_Rig", "position", 1, 2, [0, 0, 0], [0, 0, 1]), baked("G_CU", "quaternion", 6, 7, q0, q1)]));
  assert.ok(r[0] < 1.05 && r[1] > 6.95, JSON.stringify(r));
});

test("a camera that holds still (or barely shivers) has no move", () => {
  const cam = new T.Object3D(); cam.name = "Plate";
  assert.equal(V.moveRange(cam, tracksOf([baked("Plate", "position", 2, 4, [0, 1, 0], [0, 1, 0.00001])])), null);
  assert.equal(V.moveRange(cam, new Map()), null);
  const other = new T.Object3D(); other.name = "Elsewhere";
  assert.equal(V.moveRange(cam, tracksOf([baked("Elsewhere", "position", 2, 4, [0, 0, 0], [5, 0, 0])])), null, "someone else's keys");
});

test("a move's length can be any length, typed in seconds, within sane bounds", () => {
  assert.equal(V.seconds("12.5"), 12.5);
  assert.equal(V.seconds(90), 90, "longer than the slider's 30 s");
  assert.equal(V.seconds("600"), 600);
  assert.equal(V.seconds(" 7,25 "), 7.25, "a comma works as the decimal point");
  assert.equal(V.seconds("2.345"), 2.35, "to the hundredth");
  assert.equal(V.seconds("0"), V.MOVE_MIN, "never zero: a move takes some time");
  assert.equal(V.seconds("-5"), V.MOVE_MIN);
  assert.equal(V.seconds("99999"), V.MOVE_MAX);
  for (const bad of ["", "abc", null, undefined, NaN, Infinity]) assert.equal(V.seconds(bad), null, `not a length: ${bad}`);
  assert.equal(V.seconds("0", 0), 0, "a start time can be 0");
  assert.ok(V.MOVE_MIN > 0 && V.MOVE_MAX >= 3600);
});

test("a new Studio camera never takes a name that's already there", () => {
  assert.equal(V.camName([]), "Shot 1");
  assert.equal(V.camName(["Shot 1"]), "Shot 2");
  assert.equal(V.camName(["Shot 2"]), "Shot 3", "Shot 1 was deleted: not a second Shot 2");
  assert.equal(V.camName(["Shot 2", "Shot 3"]), "Shot 4");
  assert.equal(V.camName(["Wide", "Close"]), "Shot 3");
  assert.equal(V.camName([], "CAM_A"), "CAM_A · shot");
  assert.equal(V.camName(["CAM_A · shot"], "CAM_A"), "CAM_A · shot 2");
  assert.equal(V.camName(["CAM_A · shot", "CAM_A · shot 2"], "CAM_A"), "CAM_A · shot 3");
});
