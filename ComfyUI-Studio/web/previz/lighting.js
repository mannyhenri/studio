/* Previz: lighting setups. A preset places lights around the subject, measured from the camera: az is degrees around the
   subject from the camera's side (+ = camera left), el degrees up. Levels are how bright a light is where the subject
   stands (1 ≈ a white card lit to full), so a setup looks the same at any distance. */
import * as T from "../vendor/three/three.bundle.min.js";

const D2R = Math.PI / 180;

export const LIGHT_PRESETS = [
  { id: "set", label: "Set lights", hint: "The lights built into the file (how the set was lit in Blender)", env: 0.1, file: true, group: "Set" },
  { id: "neutral", label: "Neutral", hint: "Soft even light to see everything", env: 0.9, hemi: 0.8, group: "Set" },
  { id: "three", label: "Three-point", hint: "Key, fill and back light: the classic", env: 0.08,
    lights: [{ role: "key", az: 40, el: 35, lvl: 3 }, { role: "fill", az: -45, el: 15, ratio: 3 }, { role: "back", az: 165, el: 45, lvl: 2.5 }], group: "Portrait" },
  { id: "highkey", label: "High key", hint: "Bright, few shadows: comedy, commercials", env: 0.45, hemi: 0.5, bg: 0xdfe3ea,
    lights: [{ role: "key", az: 25, el: 30, lvl: 3 }, { role: "fill", az: -35, el: 10, ratio: 1.5 }, { role: "back", az: 170, el: 40, lvl: 2 }], group: "Portrait" },
  { id: "lowkey", label: "Low key", hint: "Dark and contrasty: noir, thriller", env: 0.015, bg: 0x050507,
    lights: [{ role: "key", az: 70, el: 25, lvl: 3.5 }, { role: "fill", az: -40, el: 10, ratio: 12 }, { role: "back", az: 170, el: 35, lvl: 1.5 }], group: "Portrait" },
  { id: "rembrandt", label: "Rembrandt", hint: "Key high and to the side: a triangle of light on the far cheek", env: 0.04,
    lights: [{ role: "key", az: 45, el: 45, lvl: 3.2 }, { role: "fill", az: -40, el: 10, ratio: 4 }], group: "Portrait" },
  { id: "loop", label: "Loop", hint: "Key a little to the side and above: a small nose shadow", env: 0.06,
    lights: [{ role: "key", az: 30, el: 30, lvl: 3 }, { role: "fill", az: -35, el: 10, ratio: 3 }], group: "Portrait" },
  { id: "butterfly", label: "Butterfly", hint: "Paramount: key high in front, a butterfly shadow under the nose", env: 0.05,
    lights: [{ role: "key", az: 0, el: 50, lvl: 3.2 }, { role: "fill", az: 0, el: -20, ratio: 3 }], group: "Portrait" },
  { id: "split", label: "Split", hint: "Key at 90°: half the face in shadow", env: 0.02,
    lights: [{ role: "key", az: 90, el: 5, lvl: 3.2 }], group: "Portrait" },
  { id: "broad", label: "Broad", hint: "Key on the side of the face turned to the camera", env: 0.06,
    lights: [{ role: "key", az: 50, el: 30, lvl: 3, face: "broad" }, { role: "fill", az: -40, el: 10, ratio: 3 }], group: "Portrait" },
  { id: "short", label: "Short", hint: "Key on the side of the face turned away: slimming, moody", env: 0.04,
    lights: [{ role: "key", az: 50, el: 30, lvl: 3, face: "short" }, { role: "fill", az: -40, el: 10, ratio: 4 }], group: "Portrait" },
  { id: "rim", label: "Rim light", hint: "Edge lights from behind outline the subject", env: 0.04,
    lights: [{ role: "rim", az: 140, el: 30, lvl: 3.5 }, { role: "rim", az: -140, el: 30, lvl: 3.5 }, { role: "fill", az: 0, el: 10, ratio: 8 }], group: "Portrait" },
  { id: "silhouette", label: "Silhouette", hint: "The background lit bright, the subject dark against it", env: 0, bg: 0xf4f1ea,
    lights: [{ role: "bg", lvl: 6, k: 6000 }, { role: "back", az: 180, el: 20, lvl: 4 }], group: "Portrait" },
  { id: "under", label: "Under-light", hint: "From below: horror, campfire stories", env: 0.02, bg: 0x050507,
    lights: [{ role: "key", az: 0, el: -45, lvl: 3 }], group: "Portrait" },
  { id: "top", label: "Top light", hint: "Straight down: interrogation, dark eyes", env: 0.02,
    lights: [{ role: "key", az: 0, el: 85, lvl: 4 }], group: "Portrait" },
  { id: "golden", label: "Golden hour", hint: "Low warm sun, long shadows", env: 0.2, hemi: 0.35, sky: 7500, ground: 0x6b4a2e, bg: 0xf0a860,
    sun: { az: 60, el: 8, lvl: 3, k: 2800 }, group: "Time of day" },
  { id: "noon", label: "Midday sun", hint: "Hard light from high above", env: 0.3, hemi: 0.5, sky: 9000, bg: 0x9ec4ee,
    sun: { az: 30, el: 75, lvl: 4, k: 5600 }, group: "Time of day" },
  { id: "overcast", label: "Overcast", hint: "Soft, shadowless daylight", env: 0.7, hemi: 1.4, sky: 7000, bg: 0xb9bec6, noShadow: true, group: "Time of day" },
  { id: "bluehour", label: "Blue hour", hint: "Just after sunset: cool, soft", env: 0.12, hemi: 0.45, sky: 14000, ground: 0x1a2030, bg: 0x22345c, group: "Time of day" },
  { id: "moon", label: "Moonlight", hint: "Night: cool, dim, a few hard shadows", env: 0.02, hemi: 0.08, sky: 12000, bg: 0x060a16,
    sun: { az: -50, el: 35, lvl: 0.7, k: 9000 }, group: "Time of day" },
  { id: "candle", label: "Candlelight", hint: "A warm flickering light close by", env: 0.01, bg: 0x030202,
    lights: [{ role: "practical", az: 35, el: -5, lvl: 2.2, k: 1900, point: true, near: 0.45, flicker: true }], group: "Mood" },
  { id: "neon", label: "Neon", hint: "Two coloured lights, magenta and cyan", env: 0.02, bg: 0x07040c,
    lights: [{ role: "key", az: 75, el: 15, lvl: 3, color: 0xff2fb4 }, { role: "key", az: -75, el: 15, lvl: 3, color: 0x22d6ff }], group: "Mood" },
  { id: "police", label: "Emergency lights", hint: "Red and blue from off-screen", env: 0.02, bg: 0x040306,
    lights: [{ role: "key", az: 80, el: 20, lvl: 3, color: 0xff2020, flicker: "strobe" }, { role: "key", az: -80, el: 20, lvl: 3, color: 0x2050ff, flicker: "strobe2" }], group: "Mood" },
];
export const lightInfo = id => LIGHT_PRESETS.find(p => p.id === id) || LIGHT_PRESETS[0];

/* A colour temperature in kelvin as an RGB colour (Tanner Helland's fit). */
export function kelvin(k) {
  const t = Math.max(1000, Math.min(40000, k)) / 100;
  let r, g, b;
  if (t <= 66) { r = 255; g = 99.4708025861 * Math.log(t) - 161.1195681661; b = t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307; }
  else { r = 329.698727446 * Math.pow(t - 60, -0.1332047592); g = 288.1221695283 * Math.pow(t - 60, -0.0755148492); b = 255; }
  const c = v => Math.max(0, Math.min(255, v)) / 255;
  return new T.Color().setRGB(c(r), c(g), c(b), T.SRGBColorSpace);
}

/* The defaults of the sliders for a preset. */
export function presetParams(id) {
  const p = lightInfo(id), fill = (p.lights || []).find(l => l.role === "fill");
  return { preset: id, key: 1, ratio: fill ? fill.ratio : 4, side: 0, height: 0, temp: 5600, ambient: p.env ?? 0.3, shadows: !p.noShadow, file: !!p.file, fileScale: 1, exposure: 1 };
}

/* Where each light of a preset goes, as plain numbers (the viewer turns them into three.js lights; tests check them).
   ctx: { at: [x,y,z] the subject's middle, cam: [x,y,z] the camera, size: subject height, facing: [x,0,z] }. */
export function layout(params, ctx) {
  const p = lightInfo(params.preset), out = [];
  const c = [ctx.cam[0] - ctx.at[0], 0, ctx.cam[2] - ctx.at[2]], cl = Math.hypot(c[0], c[2]) || 1;
  const axis = [c[0] / cl, 0, c[2] / cl];            // from the subject towards the camera
  const H = Math.max(ctx.size || 1.75, 0.3), dist = Math.max(2.2, H * 1.8);
  // broad/short: which side of the face is turned to the camera decides the key's side
  let faceSign = 1;
  if (ctx.facing) { const right = [-axis[2], 0, axis[0]]; faceSign = (ctx.facing[0] * right[0] + ctx.facing[2] * right[2]) >= 0 ? 1 : -1; }
  const keyLvl = ((p.lights || []).find(l => l.role === "key") || { lvl: 3 }).lvl * (params.key || 1);   // the fill is the key over the ratio
  for (const l of p.lights || []) {
    if (l.role === "bg") {      // washes whatever is behind the subject (a wall, a cyc), aimed away from the camera
      const pos = [ctx.at[0] - axis[0] * 1.5, ctx.at[1] + H * 1.2, ctx.at[2] - axis[2] * 1.5];
      const target = [ctx.at[0] - axis[0] * 6, ctx.at[1], ctx.at[2] - axis[2] * 6];
      out.push({ role: "bg", pos, target, lvl: l.lvl * (params.key || 1), dist: 6, color: kelvin(l.k || 6000), point: false, wide: true, flicker: false, shadow: false });
      continue;
    }
    let az = l.az, el = l.el;
    if (l.role === "key") { az += params.side || 0; el += params.height || 0; }
    if (l.face === "short") az = Math.abs(az) * faceSign;
    if (l.face === "broad") az = -Math.abs(az) * faceSign;
    const d = l.near || dist, a = -az * D2R, e = el * D2R;
    const hx = axis[0] * Math.cos(a) + axis[2] * Math.sin(a), hz = -axis[0] * Math.sin(a) + axis[2] * Math.cos(a);
    const pos = [ctx.at[0] + hx * Math.cos(e) * d, ctx.at[1] + Math.sin(e) * d, ctx.at[2] + hz * Math.cos(e) * d];
    const lvl = l.role === "fill" ? keyLvl / Math.max(1, params.ratio || l.ratio || 4) : l.lvl * (l.role === "key" ? (params.key || 1) : 1);
    const color = l.color != null ? new T.Color(l.color) : kelvin(l.k || (l.role === "key" ? params.temp || 5600 : l.role === "fill" ? (params.temp || 5600) + 400 : 6500));
    out.push({ role: l.role, pos, target: ctx.at.slice(), lvl, dist: d, color, point: !!l.point, wide: !!l.wide, flicker: l.flicker || false,
      shadow: params.shadows !== false && (l.role === "key" || l.role === "back" && p.id === "silhouette" || l.role === "practical") });
  }
  let sun = null;
  if (p.sun) {
    const a = -(p.sun.az + (params.side || 0)) * D2R, e = Math.max(1, p.sun.el + (params.height || 0)) * D2R;
    const hx = axis[0] * Math.cos(a) + axis[2] * Math.sin(a), hz = -axis[0] * Math.sin(a) + axis[2] * Math.cos(a);
    sun = { dir: [hx * Math.cos(e), Math.sin(e), hz * Math.cos(e)], lvl: p.sun.lvl * (params.key || 1), color: kelvin(p.sun.k), shadow: params.shadows !== false };
  }
  return { lights: out, sun, hemi: p.hemi || 0, sky: p.sky ? kelvin(p.sky) : new T.Color(0xffffff), ground: new T.Color(p.ground ?? 0x303038),
    env: params.ambient ?? p.env ?? 0.3, bg: p.bg ?? null, file: !!params.file };
}

/* How bright a flickering light is at time t (1 = as set). */
export function flicker(kind, t) {
  if (kind === "strobe") return (Math.sin(t * 9) > 0.3) ? 1 : 0.05;
  if (kind === "strobe2") return (Math.sin(t * 9 + Math.PI) > 0.3) ? 1 : 0.05;
  return 0.82 + Math.sin(t * 13.1) * 0.07 + Math.sin(t * 27.7 + 1.3) * 0.06 + Math.sin(t * 5.3 + 2.1) * 0.05;
}

/* ---------------------------------------------------------------- lighting as plain data (what a keyframe keeps) */
const rgb = c => [c.r, c.g, c.b];

/* A layout (with its sliders' values) as plain numbers: saved with a keyframe, blended between two keyframes. */
export function serial(L, params, ctx) {
  return {
    lights: L.lights.map(l => ({ role: l.role, pos: l.pos.slice(), target: l.target.slice(), lvl: l.lvl, dist: l.dist, color: rgb(l.color),
      point: l.point, wide: l.wide, flicker: l.flicker || false, shadow: l.shadow })),
    sun: L.sun ? { dir: L.sun.dir.slice(), lvl: L.sun.lvl, color: rgb(L.sun.color), shadow: L.sun.shadow } : null,
    at: ctx.at.slice(), size: ctx.size || 2, hemi: L.hemi, sky: rgb(L.sky), ground: rgb(L.ground), env: L.env, bg: L.bg,
    file: !!L.file, fileScale: params.fileScale || 1, exposure: params.exposure || 1, params: { ...params },
  };
}

const mix = (a, b, s) => a + (b - a) * s;
const mixV = (a, b, s) => a.map((v, i) => mix(v, b[i], s));
const same = (A, B) => A.lights.length === B.lights.length && A.lights.every((l, i) => l.role === B.lights[i].role && l.point === B.lights[i].point);

/* The light between two keyframes, s from 0 (A) to 1 (B). The same setup: every light moves and changes level and
   colour. Two different setups: one fades out while the other fades in. */
export function blend(A, B, s) {
  s = Math.max(0, Math.min(1, s));
  let lights;
  if (same(A, B)) {
    lights = A.lights.map((l, i) => {
      const m = B.lights[i];
      return { ...(s < 0.5 ? l : m), pos: mixV(l.pos, m.pos, s), target: mixV(l.target, m.target, s), lvl: mix(l.lvl, m.lvl, s), dist: mix(l.dist, m.dist, s), color: mixV(l.color, m.color, s) };
    });
  } else {
    lights = A.lights.map(l => ({ ...l, lvl: l.lvl * (1 - s), shadow: l.shadow && s < 0.5 }))
      .concat(B.lights.map(l => ({ ...l, lvl: l.lvl * s, shadow: l.shadow && s >= 0.5 })));
  }
  let sun = null;
  if (A.sun && B.sun) {
    const d = mixV(A.sun.dir, B.sun.dir, s), n = Math.hypot(...d) || 1;
    sun = { dir: d.map(v => v / n), lvl: mix(A.sun.lvl, B.sun.lvl, s), color: mixV(A.sun.color, B.sun.color, s), shadow: s < 0.5 ? A.sun.shadow : B.sun.shadow };
  } else if (A.sun || B.sun) {
    const o = A.sun || B.sun;
    sun = { ...o, lvl: o.lvl * (A.sun ? 1 - s : s) };
  }
  const bgOf = L => L.bg == null ? null : L.bg;
  const bgA = bgOf(A), bgB = bgOf(B);
  const bgMix = bgA == null && bgB == null ? null : (() => { const ca = new T.Color(bgA ?? 0x1a1a1f), cb = new T.Color(bgB ?? 0x1a1a1f); return ca.lerp(cb, s).getHex(); })();
  const fileOf = L => L.file ? L.fileScale : 0, file = mix(fileOf(A), fileOf(B), s);
  return { lights, sun, at: mixV(A.at, B.at, s), size: mix(A.size, B.size, s), hemi: mix(A.hemi, B.hemi, s), sky: mixV(A.sky, B.sky, s),
    ground: mixV(A.ground, B.ground, s), env: mix(A.env, B.env, s), bg: bgMix, file: file > 1e-4, fileScale: file || 1,
    exposure: mix(A.exposure, B.exposure, s), params: (s < 0.5 ? A : B).params };
}
