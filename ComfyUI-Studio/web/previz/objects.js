/* Previz: objects you add to a set (stand-ins to block a scene) and the list of what's in the file (the outliner).
   Every stand-in stands on the floor at its origin and faces +Z, like a glTF character. */
import * as T from "../vendor/three/three.bundle.min.js";

export const ADDABLE = [
  { id: "person", label: "Person", hint: "A 1.75 m stand-in; its nose shows where it faces", color: 0x6f9bd6 },
  { id: "child", label: "Child", hint: "A 1.2 m stand-in", color: 0x8fc48a },
  { id: "box", label: "Box", hint: "1 m cube", color: 0xb9a48a },
  { id: "pillar", label: "Pillar", hint: "3 m column", color: 0xa7a7ad },
  { id: "wall", label: "Wall", hint: "4 × 3 m flat", color: 0xc9c3b8 },
  { id: "floor", label: "Floor", hint: "4 × 4 m platform", color: 0x8d8d92 },
  { id: "steps", label: "Steps", hint: "Five steps, 1.5 m wide", color: 0xa39e95 },
  { id: "table", label: "Table", hint: "1.6 × 0.9 m", color: 0x8a6a4c },
  { id: "chair", label: "Chair", hint: "", color: 0x7d5f45 },
  { id: "car", label: "Car", hint: "4.4 m stand-in", color: 0xb04a3c },
  { id: "tree", label: "Tree", hint: "5 m", color: 0x4f7d45 },
];
export const COLORS = [0x6f9bd6, 0xe0894a, 0x8fc48a, 0xc46fc9, 0xe6cf5a, 0xd65b5b, 0xb9a48a, 0xa7a7ad, 0x3d3d44, 0xf1efe9];

function mat(color) { return new T.MeshStandardMaterial({ color, roughness: 0.75, metalness: 0 }); }
function part(geo, m, x, y, z, rx = 0, ry = 0, rz = 0) {
  const o = new T.Mesh(geo, m); o.position.set(x, y, z); o.rotation.set(rx, ry, rz); o.castShadow = o.receiveShadow = true; return o;
}

function person(m, h) {
  const g = new T.Group(), k = h / 1.75, skin = m;
  const cap = (r, len) => new T.CapsuleGeometry(r * k, len * k, 6, 14);
  g.add(part(new T.SphereGeometry(0.11 * k, 20, 14), skin, 0, 1.635 * k, 0));
  g.add(part(new T.ConeGeometry(0.025 * k, 0.07 * k, 10), skin, 0, 1.62 * k, 0.12 * k, Math.PI / 2));   // the nose: where it faces
  g.add(part(new T.CylinderGeometry(0.05 * k, 0.055 * k, 0.1 * k, 12), skin, 0, 1.5 * k, 0));
  const torso = part(cap(0.16, 0.34), skin, 0, 1.22 * k, 0); torso.scale.set(1.12, 1, 0.62); g.add(torso);
  const hips = part(cap(0.15, 0.08), skin, 0, 0.95 * k, 0); hips.scale.set(1.05, 1, 0.7); g.add(hips);
  for (const s of [-1, 1]) {
    g.add(part(cap(0.075, 0.72), skin, 0.1 * s * k, 0.48 * k, 0));
    g.add(part(cap(0.055, 0.56), skin, 0.245 * s * k, 1.1 * k, 0, 0, 0, 0.07 * s));
    g.add(part(new T.BoxGeometry(0.1 * k, 0.06 * k, 0.24 * k), skin, 0.1 * s * k, 0.03 * k, 0.05 * k));
  }
  return g;
}

/* A new stand-in: a Group with userData.kind, standing on its origin. */
export function build(kind, color) {
  const info = ADDABLE.find(a => a.id === kind) || ADDABLE[2], m = mat(color ?? info.color), g = new T.Group();
  const B = (w, h, d) => new T.BoxGeometry(w, h, d);
  switch (kind) {
    case "person": g.add(person(m, 1.75)); break;
    case "child": g.add(person(m, 1.2)); break;
    case "box": g.add(part(B(1, 1, 1), m, 0, 0.5, 0)); break;
    case "pillar": g.add(part(new T.CylinderGeometry(0.25, 0.25, 3, 24), m, 0, 1.5, 0)); break;
    case "wall": g.add(part(B(4, 3, 0.15), m, 0, 1.5, 0)); break;
    case "floor": g.add(part(B(4, 0.05, 4), m, 0, 0.025, 0)); break;
    case "steps": for (let i = 0; i < 5; i++) g.add(part(B(1.5, 0.18 * (i + 1), 0.3), m, 0, 0.09 * (i + 1), -0.3 * i)); break;
    case "table":
      g.add(part(B(1.6, 0.05, 0.9), m, 0, 0.735, 0));
      for (const x of [-0.72, 0.72]) for (const z of [-0.37, 0.37]) g.add(part(B(0.06, 0.71, 0.06), m, x, 0.355, z));
      break;
    case "chair":
      g.add(part(B(0.45, 0.05, 0.45), m, 0, 0.45, 0)); g.add(part(B(0.45, 0.5, 0.04), m, 0, 0.72, -0.205));
      for (const x of [-0.2, 0.2]) for (const z of [-0.2, 0.2]) g.add(part(B(0.04, 0.45, 0.04), m, x, 0.225, z));
      break;
    case "car": {
      g.add(part(B(1.8, 0.7, 4.4), m, 0, 0.6, 0)); g.add(part(B(1.6, 0.55, 2.3), m, 0, 1.22, -0.2));
      const tyre = mat(0x1c1c1f), wheel = new T.CylinderGeometry(0.34, 0.34, 0.24, 20);
      for (const x of [-0.84, 0.84]) for (const z of [-1.35, 1.35]) g.add(part(wheel, tyre, x, 0.34, z, 0, 0, Math.PI / 2));
      break;
    }
    case "tree": {
      g.add(part(new T.CylinderGeometry(0.12, 0.18, 2.2, 10), mat(0x5b4332), 0, 1.1, 0));
      g.add(part(new T.IcosahedronGeometry(1.4, 1), m, 0, 3.3, 0));
      break;
    }
  }
  g.userData.kind = kind;
  g.userData.person = kind === "person" || kind === "child";
  return g;
}

export function setColor(obj, color) {
  obj.traverse(o => { if (o.isMesh && o.material && o.material.color && !(o.userData.keepColor)) o.material.color.set(color); });
}

/* ---------------------------------------------------------------- the file's objects */
/* A name for an object that stays the same when the file is exported again: its path of names from the set's root. */
export function keyOf(obj, root) {
  const parts = [];
  for (let o = obj; o && o !== root; o = o.parent) {
    const sib = o.parent ? o.parent.children.filter(c => c.name === o.name) : [o];
    parts.push(sib.length > 1 ? `${o.name}#${sib.indexOf(o)}` : o.name);
  }
  return parts.reverse().join("/");
}

export function hasMesh(o) { let y = false; o.traverse(c => { if (c.isMesh) y = true; }); return y; }
export function isCharacter(o) { let y = false; o.traverse(c => { if (c.isSkinnedMesh) y = true; }); return y; }

/* The tree the Objects tab shows: everything with something visible in it (no bones, cameras, lights or empties).
   `chars`: the set's characters (characters(root)), marked in the tree. */
export function outline(root, chars = []) {
  const walk = (o, depth) => {
    if (o.isBone || o.isCamera || o.isLight || !hasMesh(o)) return null;
    const kids = o.children.map(c => walk(c, depth + 1)).filter(Boolean);
    return { obj: o, key: keyOf(o, root), name: o.name || "(no name)", depth, kids, character: chars.includes(o) };
  };
  return root.children.map(c => walk(c, 0)).filter(Boolean);
}

/* The characters in a set: the top objects that hold a skinned mesh (DRIFT_1_Ctrl, VIX_Ctrl…). */
export function characters(root) {
  const out = [];
  for (const c of root.children) {
    if (c.isBone || !isCharacter(c)) continue;
    // a group of several characters: go one level down
    const inner = c.children.filter(isCharacter);
    if (inner.length > 1 && !c.isSkinnedMesh) out.push(...inner); else out.push(c);
  }
  return out;
}
export const niceName = n => String(n || "").replace(/[_ .-]*(ctrl|rig|root|grp|group)$/i, "").replace(/_/g, " ").trim() || n;
