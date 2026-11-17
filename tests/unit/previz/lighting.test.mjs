// Previz lighting setups (ComfyUI-Studio/web/previz/lighting.js): where each light goes around the subject, measured
// from the camera, how bright, and what colour. Run by tests/unit/test_previz_js.py (node --test).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as L from "../../../ComfyUI-Studio/web/previz/lighting.js";

// the subject at the origin (chest height 1.1), the camera 4 m in front of it on +Z: camera left is -X
const ctx = { at: [0, 1.1, 0], cam: [0, 1.6, 4], size: 1.75, facing: [0, 0, 1] };
const role = (lay, r) => lay.lights.filter(l => l.role === r);
const elev = l => Math.atan2(l.pos[1] - ctx.at[1], Math.hypot(l.pos[0] - ctx.at[0], l.pos[2] - ctx.at[2])) * 180 / Math.PI;
const azim = l => Math.atan2(-(l.pos[0] - ctx.at[0]), l.pos[2] - ctx.at[2]) * 180 / Math.PI;    // + = camera left

test("every preset has a label, a hint and a group, and lays out", () => {
  const ids = new Set();
  for (const p of L.LIGHT_PRESETS) {
    assert.ok(p.id && p.label && p.hint && p.group, p.id);
    assert.ok(!ids.has(p.id)); ids.add(p.id);
    const lay = L.layout(L.presetParams(p.id), ctx);
    for (const l of lay.lights) { assert.ok(l.lvl > 0 && l.pos.every(Number.isFinite), p.id); }
    assert.ok(lay.env >= 0);
  }
  for (const want of ["three", "rembrandt", "butterfly", "split", "loop", "broad", "short", "rim", "silhouette", "highkey", "lowkey", "golden", "moon", "neon", "candle"]) assert.ok(ids.has(want), want);
});

test("Rembrandt: key 45° to the side and 45° up; fill on the other side, a quarter as bright", () => {
  const lay = L.layout(L.presetParams("rembrandt"), ctx), [key] = role(lay, "key"), [fill] = role(lay, "fill");
  assert.ok(Math.abs(azim(key) - 45) < 0.01 && Math.abs(elev(key) - 45) < 0.01, `${azim(key)} ${elev(key)}`);
  assert.ok(azim(fill) < 0, "fill on the other side");
  assert.ok(Math.abs(key.lvl / fill.lvl - 4) < 1e-9, "4:1");
  assert.ok(key.shadow && !fill.shadow, "the key casts the shadows");
});

test("the sliders move the key and change the ratio", () => {
  const base = L.presetParams("three");
  const lay = L.layout({ ...base, side: 30, height: -10, ratio: 8, key: 2 }, ctx), [key] = role(lay, "key"), [fill] = role(lay, "fill");
  assert.ok(Math.abs(azim(key) - 70) < 0.01 && Math.abs(elev(key) - 25) < 0.01);
  assert.ok(Math.abs(key.lvl / fill.lvl - 8) < 1e-9);
  assert.ok(Math.abs(key.lvl - 6) < 1e-9, "key ×2");
});

test("butterfly: the key above the lens; split: at 90°; under-light: from below; top light: from above", () => {
  const k = id => role(L.layout(L.presetParams(id), ctx), "key")[0];
  assert.ok(Math.abs(azim(k("butterfly"))) < 0.01 && elev(k("butterfly")) > 40);
  assert.ok(Math.abs(azim(k("split")) - 90) < 0.01);
  assert.ok(elev(k("under")) < -40);
  assert.ok(elev(k("top")) > 80);
});

test("broad and short light opposite sides of a turned face", () => {
  const turned = { ...ctx, facing: [1, 0, 0.3] };      // looking towards camera right
  const b = role(L.layout(L.presetParams("broad"), turned), "key")[0], s = role(L.layout(L.presetParams("short"), turned), "key")[0];
  assert.ok(Math.sign(azim(b)) === -Math.sign(azim(s)));
  assert.ok(azim(s) < 0, "short: from the side the face turns to (camera right)");
});

test("rim and silhouette light from behind; the silhouette lights the background, not the subject", () => {
  for (const l of role(L.layout(L.presetParams("rim"), ctx), "rim")) assert.ok(Math.abs(azim(l)) > 120);
  const sil = L.layout(L.presetParams("silhouette"), ctx), [bg] = role(sil, "bg");
  assert.ok(bg.pos[2] < 0 && bg.target[2] < bg.pos[2], "behind the subject, aimed away from the camera");
  assert.equal(role(sil, "key").length + role(sil, "fill").length, 0, "nothing from the front");
  assert.ok(sil.env === 0);
});

test("time of day: a sun at the right height and colour", () => {
  const g = L.layout(L.presetParams("golden"), ctx), n = L.layout(L.presetParams("noon"), ctx), m = L.layout(L.presetParams("moon"), ctx);
  const el = s => Math.asin(s.dir[1]) * 180 / Math.PI;
  assert.ok(Math.abs(el(g.sun) - 8) < 0.01 && Math.abs(el(n.sun) - 75) < 0.01);
  assert.ok(g.sun.color.r > g.sun.color.b * 1.5, "golden hour is warm");
  assert.ok(m.sun.color.b > m.sun.color.r, "moonlight is cool");
  assert.ok(m.sun.lvl < g.sun.lvl && m.env < n.env);
  assert.equal(L.layout(L.presetParams("overcast"), ctx).lights.length, 0);
  assert.equal(L.presetParams("overcast").shadows, false);
});

test("the set's own lights and the neutral look", () => {
  assert.equal(L.presetParams("set").file, true);
  assert.equal(L.presetParams("neutral").file, false);
  assert.ok(L.layout(L.presetParams("neutral"), ctx).env > 0.5);
});

test("colour temperature", () => {
  const w = L.kelvin(6600), c = L.kelvin(1900), b = L.kelvin(12000);
  assert.ok(Math.abs(w.r - w.b) < 0.08, "about white at 6600 K");
  assert.ok(c.r > c.g && c.g > c.b, "candle: red over blue");
  assert.ok(b.b > b.r, "12000 K is blue");
});

test("flicker stays around the level, strobes switch", () => {
  for (let t = 0; t < 10; t += 0.37) { const f = L.flicker(true, t); assert.ok(f > 0.55 && f < 1.05); }
  const s = Array.from({ length: 60 }, (_, i) => L.flicker("strobe", i / 20));
  assert.ok(s.some(v => v === 1) && s.some(v => v < 0.1));
});

/* ---------------------------------------------------------------- keyframed light */
test("a rig as plain data, and back the same way", () => {
  const P = L.presetParams("three"), lay = L.layout(P, ctx), s = L.serial(lay, P, ctx);
  assert.equal(s.lights.length, 3);
  assert.ok(s.lights.every(l => Array.isArray(l.color) && l.color.length === 3));
  assert.deepEqual(JSON.parse(JSON.stringify(s)), s, "JSON-safe (it's saved with a keyframe)");
  assert.equal(s.params.preset, "three");
});

test("between two keyframes of the same setup, the lights move and change level", () => {
  const P = L.presetParams("three"), A = L.serial(L.layout(P, ctx), P, ctx);
  const P2 = { ...P, side: 90, key: 2, ambient: 0.5 }, B = L.serial(L.layout(P2, ctx), P2, ctx);
  const mid = L.blend(A, B, 0.5);
  assert.equal(mid.lights.length, 3, "the same lights, moving");
  for (let k = 0; k < 3; k++) mid.lights[k].pos.forEach((v, i) => assert.ok(Math.abs(v - (A.lights[k].pos[i] + B.lights[k].pos[i]) / 2) < 1e-9));
  assert.ok(Math.abs(mid.lights[0].lvl - (A.lights[0].lvl + B.lights[0].lvl) / 2) < 1e-9);
  assert.ok(Math.abs(mid.env - (A.env + 0.5) / 2) < 1e-9);
  const close = (x, y) => x.every((l, k) => l.pos.every((v, i) => Math.abs(v - y[k].pos[i]) < 1e-9));
  assert.ok(close(L.blend(A, B, 0).lights, A.lights) && close(L.blend(A, B, 1).lights, B.lights), "the ends are the keyframes");
});

test("between two different setups, one fades out as the other fades in", () => {
  const Pa = L.presetParams("rembrandt"), Pb = L.presetParams("golden");
  const A = L.serial(L.layout(Pa, ctx), Pa, ctx), B = L.serial(L.layout(Pb, ctx), Pb, ctx);
  const q = L.blend(A, B, 0.25);
  assert.equal(q.lights.length, A.lights.length + B.lights.length);
  assert.ok(Math.abs(q.lights[0].lvl - A.lights[0].lvl * 0.75) < 1e-9);
  assert.ok(q.sun && Math.abs(q.sun.lvl - B.sun.lvl * 0.25) < 1e-9, "the sun comes up");
  assert.ok(q.bg != null, "the background turns towards golden hour's");
  assert.equal(L.blend(A, B, 0.25).params.preset, "rembrandt"); assert.equal(L.blend(A, B, 0.75).params.preset, "golden");
});
