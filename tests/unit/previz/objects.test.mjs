// Previz objects (ComfyUI-Studio/web/previz/objects.js): the stand-ins you add, the names that find a file's objects again
// after a new export, and the outliner. Run by tests/unit/test_previz_js.py (node --test).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as T from "../../../ComfyUI-Studio/web/vendor/three/three.bundle.min.js";
import * as OB from "../../../ComfyUI-Studio/web/previz/objects.js";

test("every stand-in stands on the floor at its origin", () => {
  for (const a of OB.ADDABLE) {
    const g = OB.build(a.id), box = new T.Box3().setFromObject(g);
    assert.ok(Math.abs(box.min.y) < 0.02, `${a.id} floor ${box.min.y}`);
    assert.ok(Math.abs((box.min.x + box.max.x) / 2) < 0.6, `${a.id} centred`);
    assert.equal(g.userData.kind, a.id);
  }
  const person = new T.Box3().setFromObject(OB.build("person")), child = new T.Box3().setFromObject(OB.build("child"));
  assert.ok(Math.abs(person.max.y - 1.75) < 0.03 && Math.abs(child.max.y - 1.2) < 0.03, "1.75 m and 1.2 m");
  assert.ok(OB.build("person").userData.person && !OB.build("box").userData.person);
});

test("a person's nose shows where it faces: +Z, like a glTF character", () => {
  const g = OB.build("person"), cone = [];
  g.traverse(o => { if (o.isMesh && o.geometry.type === "ConeGeometry") cone.push(o.getWorldPosition(new T.Vector3())); });
  assert.equal(cone.length, 1); assert.ok(cone[0].z > 0.1);
});

test("names that survive a new export, even when names repeat", () => {
  const root = new T.Group(), a = new T.Group(), b = new T.Group(), c1 = new T.Mesh(), c2 = new T.Mesh();
  a.name = "S01_Root"; b.name = "S02_Root"; c1.name = c2.name = "Wall";
  root.add(a, b); a.add(c1); b.add(c2);
  assert.equal(OB.keyOf(c1, root), "S01_Root/Wall"); assert.equal(OB.keyOf(c2, root), "S02_Root/Wall");
  const d = new T.Mesh(); d.name = "Wall"; a.add(d);
  assert.equal(OB.keyOf(c1, root), "S01_Root/Wall#0"); assert.equal(OB.keyOf(d, root), "S01_Root/Wall#1");
});

test("the outliner: what can be seen, characters marked once", () => {
  const root = new T.Group();
  const ctrl = new T.Group(); ctrl.name = "VIX_Ctrl";
  const rig = new T.Group(); rig.name = "VIX_Rig";
  const bone = new T.Bone(); bone.name = "Hips";
  const body = new T.SkinnedMesh(new T.BoxGeometry(), new T.MeshBasicMaterial()); body.name = "VIX_Body";
  const empty = new T.Group(); empty.name = "TGT_CAM_01";
  const wall = new T.Mesh(new T.BoxGeometry()); wall.name = "Wall";
  root.add(ctrl, empty, wall); ctrl.add(rig); rig.add(bone, body);
  assert.deepEqual(OB.characters(root), [ctrl]);
  const tree = OB.outline(root, OB.characters(root));
  assert.deepEqual(tree.map(n => n.name), ["VIX_Ctrl", "Wall"], "no empties");
  assert.equal(tree[0].character, true);
  assert.equal(tree[0].kids[0].name, "VIX_Rig"); assert.equal(tree[0].kids[0].character, false, "marked once, at the character");
  assert.deepEqual(tree[0].kids[0].kids.map(n => n.name), ["VIX_Body"], "no bones");
  assert.equal(OB.niceName("DRIFT_1_Ctrl"), "DRIFT 1");
});
