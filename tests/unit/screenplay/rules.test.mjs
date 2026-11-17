// The screenplay editor's rules (ComfyUI-Studio/web/screenplay/rules.js): what Enter and Tab do, what a line is, what
// SmartType offers, the statistics, drafts' differences, locked scene numbers, and a storyboard shot's changes.
// Run by tests/unit/test_screenplay_js.py (node --test).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as R from "../../../ComfyUI-Studio/web/screenplay/rules.js";

test("Tab on an empty line picks the likely element for where it is, and walks on", () => {
  const t = (prev, type, back) => R.tab({ prev, type, empty: true, atEnd: true }, back);
  assert.deepEqual(t("dialogue", "action"), { type: "character" });          // after a speech: the next speaker
  assert.deepEqual(t("dialogue", "character"), { type: "parenthetical" });
  assert.deepEqual(t("action", "action"), { type: "character" });
  assert.deepEqual(t("scene", "action"), { type: "character" });
  assert.deepEqual(t("character", "dialogue"), { type: "parenthetical" });   // after a cue: a parenthetical
  assert.deepEqual(t("transition", "scene"), { type: "action" });
  assert.deepEqual(t("", "scene"), { type: "action" });                       // the first line of a script
  assert.deepEqual(t("action", "character", true), { type: "action" });     // Shift+Tab walks back
  assert.deepEqual(t("dialogue", "action", true), { type: "transition" });
  assert.deepEqual(t("section", "lyrics"), { type: "scene" });               // not in the list: its first
});

test("Tab at the end of a cue or a speech adds a parenthetical; after one, the speech goes on", () => {
  assert.deepEqual(R.tab({ prev: "action", type: "character", empty: false, atEnd: true }), { insert: "parenthetical" });
  assert.deepEqual(R.tab({ prev: "character", type: "dialogue", empty: false, atEnd: true }), { insert: "parenthetical" });
  assert.deepEqual(R.tab({ prev: "character", type: "parenthetical", empty: false, atEnd: true }), { insert: "dialogue" });
  // with text, not at the end (or on action): the element changes
  assert.deepEqual(R.tab({ prev: "dialogue", type: "action", empty: false, atEnd: true }), { type: "character" });
  assert.deepEqual(R.tab({ prev: "action", type: "character", empty: false, atEnd: false }), { type: "transition" });
});

test("Enter reads the line: forcing marks, headings, transitions, parentheticals, known cues", () => {
  const known = new Set(["RAY", "JUNE"]);
  const d = (t, type = "action", prev = "action") => R.detect(t, type, { prev, known });
  assert.deepEqual(d("int. diner - night"), { type: "scene", text: "int. diner - night" });
  assert.deepEqual(d(".FLASHBACK"), { type: "scene", text: "FLASHBACK" });
  assert.deepEqual(d("@McCLANE"), { type: "character", text: "McCLANE", forced: true });
  assert.deepEqual(d("> THE END <"), { type: "centered", text: "THE END" });
  assert.deepEqual(d("> FADE OUT."), { type: "transition", text: "FADE OUT." });
  assert.deepEqual(d("SMASH CUT TO:"), { type: "transition", text: "SMASH CUT TO:" });
  assert.deepEqual(d("~la la la"), { type: "lyrics", text: "la la la" });
  assert.deepEqual(d("## Act two"), { type: "section", text: "Act two", depth: 2 });
  assert.deepEqual(d("= She finds the letter."), { type: "synopsis", text: "She finds the letter." });
  assert.deepEqual(d("==="), { type: "page_break", text: "" });
  assert.deepEqual(d("RAY"), { type: "character", text: "RAY" });            // a known name: a cue
  assert.deepEqual(d("JUNE (V.O.)"), { type: "character", text: "JUNE (V.O.)" });
  assert.deepEqual(d("STRANGER (O.S.)"), { type: "character", text: "STRANGER (O.S.)" });   // an extension makes it a cue
  assert.equal(d("BANG!"), null);                                            // a sound, not a name
  assert.equal(d("HALE"), null);                                             // unknown capitals stay action (Tab makes a cue)
  assert.equal(d("FADE OUT."), null);                                        // Fountain's own rule: transitions end in TO:
  assert.equal(d("The sea."), null);
  assert.deepEqual(d("(beat)", "dialogue", "character"), { type: "parenthetical", text: "(beat)" });
  assert.deepEqual(d("(beat)", "action", "dialogue"), { type: "parenthetical", text: "(beat)" });
  assert.equal(d("(a note)", "action", "scene"), null);
  assert.equal(d("#1 priority", "dialogue", "character"), null);            // marks only force action lines
});

test("cues as the server reads them", () => {
  for (const s of ["RAY", "JUNE (CONT'D)", "DR. HALE (V.O.)", "COP #2", "@McClane"]) assert.ok(R.isCue(s), s);
  for (const s of ["ONE SHOT. NO CUTS, GO.", "Ray", "INT. DINER", "WAIT!"]) assert.ok(!R.isCue(s), s);
  assert.equal(R.cueName("june (cont'd) ^"), "JUNE");
});

test("SmartType: headings step by step, the next speaker first, extensions, transitions", () => {
  const ctx = { places: [["DINER", 4], ["PIER", 1]], times: ["LATE AFTERNOON"], cues: [["RAY", 28], ["JUNE", 35], ["WAITRESS", 2]],
    speakers: ["JUNE", "RAY"], names: ["Theo"], transitions: [] };
  assert.deepEqual(R.suggest("scene", "", ctx).map(x => x.label), ["INT.", "EXT.", "INT./EXT.", "EXT./INT.", "EST.", "I/E"]);
  assert.deepEqual(R.suggest("scene", "e", ctx).map(x => x.label), ["EXT.", "EXT./INT.", "EST."]);
  assert.deepEqual(R.suggest("scene", "INT. ", ctx).map(x => x.text), ["INT. DINER - ", "INT. PIER - "]);
  assert.deepEqual(R.suggest("scene", "INT. pi", ctx).map(x => x.text), ["INT. PIER - "]);
  const times = R.suggest("scene", "EXT. PIER - ", ctx).map(x => x.label);
  assert.equal(times[0], "LATE AFTERNOON"); assert.ok(times.includes("NIGHT"));
  assert.deepEqual(R.suggest("scene", "EXT. PIER - n", ctx).map(x => x.text), ["EXT. PIER - NIGHT"]);
  const next = R.suggest("character", "", ctx);
  assert.equal(next[0].label, "RAY"); assert.equal(next[0].hint, "next in the conversation");   // June just spoke: Ray answers
  assert.deepEqual(next.map(x => x.label), ["RAY", "WAITRESS", "THEO", "JUNE"]);   // who just spoke: last
  assert.deepEqual(R.suggest("character", "w", ctx).map(x => x.label), ["WAITRESS"]);
  // a scene where one person has spoken: anyone but them first (they'd go on with the same speech)
  assert.deepEqual(R.suggest("character", "", { ...ctx, speakers: ["JUNE"] }).map(x => x.label), ["RAY", "WAITRESS", "THEO", "JUNE"]);
  assert.deepEqual(R.suggest("character", "RAY", ctx), []);                 // typed in full: nothing left to offer
  assert.deepEqual(R.suggest("character", "RAY", { ...ctx, names: ["Ray Mercer"] }), []);   // not RAY MERCER over the script's own cue
  assert.deepEqual(R.suggest("character", "june (v", ctx).map(x => x.text), ["JUNE (V.O.)"]);
  assert.deepEqual(R.suggest("transition", "DIS", ctx).map(x => x.text), ["DISSOLVE TO:"]);
});

test("the script's vocabulary and who spoke last", () => {
  const els = [{ type: "scene", text: "INT. DINER - LATE AFTERNOON" }, { type: "character", text: "RAY" }, { type: "dialogue", text: "Hi." },
    { type: "character", text: "JUNE (V.O.)" }, { type: "dialogue", text: "Hi." }, { type: "scene", text: "EXT. PIER - NIGHT" },
    { type: "character", text: "RAY" }, { type: "transition", text: "CUT TO:" }, { type: "scene", text: "INT. DINER - LATER" }];
  const v = R.vocabulary(els);
  assert.deepEqual(v.cues, [["RAY", 2], ["JUNE", 1]]);
  assert.deepEqual(v.places, [["DINER", 2], ["PIER", 1]]);
  assert.deepEqual(v.times, ["LATE AFTERNOON", "NIGHT", "LATER"]);
  assert.deepEqual(v.transitions, ["CUT TO:"]);
  assert.deepEqual(R.speakersBefore(els, 5), ["JUNE", "RAY"]);
  assert.deepEqual(R.speakersBefore(els, 7), ["RAY"]);
});

test("statistics: words, speeches, scenes, characters, places", () => {
  const els = [{ type: "scene", text: "INT. DINER - DAY" }, { type: "action", text: "Ray waits." }, { type: "character", text: "RAY" },
    { type: "dialogue", text: "Coffee is bad here." }, { type: "scene", text: "EXT. PIER - NIGHT" }, { type: "character", text: "JUNE" },
    { type: "dialogue", text: "Mom liked it." }, { type: "character", text: "RAY" }, { type: "dialogue", text: "She did." }];
  const r = R.report(els, [{ number: "1", eighths: 3 }, { number: "2", eighths: 9 }]);
  assert.equal(r.scenes.length, 2); assert.equal(r.speeches, 3);
  assert.equal(r.dialogueWords, 4 + 3 + 2); assert.equal(r.actionWords, 2);
  assert.deepEqual(r.characters.map(c => [c.name, c.speeches, c.words, c.scenes, c.first]), [["RAY", 2, 6, 2, "1"], ["JUNE", 1, 3, 1, "2"]]);
  assert.deepEqual(r.locations.map(l => [l.place, l.scenes, l.intext]), [["DINER", 1, "INT"], ["PIER", 1, "EXT"]]);
  assert.equal(r.longest.number, "2"); assert.equal(r.shortest.number, "1");
  assert.equal(r.int, 1); assert.equal(r.ext, 1); assert.equal(r.day, 1); assert.equal(r.night, 1);
  assert.equal(R.eighthsText(9), "1 1/8"); assert.equal(R.eighthsText(8), "1"); assert.equal(R.eighthsText(3), "3/8");
});

test("differences: drafts line by line, revised elements", () => {
  const ops = R.diff(["a", "b", "c", "d"], ["a", "c", "x", "d"]);
  assert.deepEqual(ops, [[" ", "a"], ["-", "b"], [" ", "c"], ["+", "x"], [" ", "d"]]);
  assert.deepEqual(R.diff([], ["x"]), [["+", "x"]]);
  assert.deepEqual(R.diff(["x"], []), [["-", "x"]]);
  const then = [{ type: "scene", text: "INT. A" }, { type: "action", text: "One." }, { type: "action", text: "Two." }];
  const now = [{ type: "scene", text: "INT. A" }, { type: "action", text: "One, changed." }, { type: "action", text: "Two." }, { type: "action", text: "Three." }];
  assert.deepEqual([...R.revised(then, now)].sort(), [1, 3]);
});

test("scene numbers: counted, or locked with letters for the scenes added since", () => {
  assert.deepEqual(R.sceneNumbers([null, null, null]), ["1", "2", "3"]);
  assert.deepEqual(R.sceneNumbers(["1", "2", null, "3"]), ["1", "2", "2A", "3"]);
  assert.deepEqual(R.sceneNumbers(["1", "2", null, null, "3"]), ["1", "2", "2A", "2B", "3"]);
  assert.deepEqual(R.sceneNumbers([null, "1", "2"]), ["A1", "1", "2"]);
  assert.deepEqual(R.sceneNumbers(["12A", null]), ["12A", "12B"]);
});

test("find and rename", () => {
  assert.deepEqual(R.findIn("Ray and RAYMOND and ray.", "ray"), [[0, 3], [8, 11], [20, 23]]);
  assert.deepEqual(R.findIn("Ray and RAYMOND and ray.", "ray", { word: true }), [[0, 3], [20, 23]]);
  assert.deepEqual(R.findIn("Ray and ray", "Ray", { cs: true }), [[0, 3]]);
  assert.equal(R.renameIn("RAY looks at Ray's cup. Raymond waits.", "Ray", "joe"), "JOE looks at Joe's cup. Raymond waits.");
});

test("a storyboard shot's changes, said in words", () => {
  const sc = { heading: "INT. DINER - DAY" };
  const a = R.shotSource({ action: "Ray waits.", lines: [{ who: "RAY", text: "Coffee's bad." }, { who: "JUNE", text: "It is." }], characters: ["RAY", "JUNE"], camera: "static", seconds: 6 }, sc);
  const b = R.shotSource({ action: "Ray waits.", lines: [{ who: "RAY", text: "Coffee's still bad." }, { who: "JUNE", text: "It is." }, { who: "RAY", text: "Sit." }], characters: ["RAY", "JUNE"], camera: "push in", seconds: 8 },
    { heading: "INT. DINER - NIGHT" });
  assert.deepEqual(R.shotChanges(a, a), []);
  assert.deepEqual(R.shotChanges(null, a), [{ kind: "new" }]);
  assert.deepEqual(R.shotChanges(a, b), [
    { kind: "setting", from: "INT. DINER - DAY", to: "INT. DINER - NIGHT" },
    { kind: "line_changed", who: "RAY", from: "Coffee's bad.", to: "Coffee's still bad." },
    { kind: "line_added", who: "RAY", line: "Sit." },
    { kind: "camera", from: "static", to: "push in" },
    { kind: "length", from: 6, to: 8 }]);
  const c = R.shotSource({ action: "June leaves.", lines: [{ who: "RAY", text: "Coffee's bad." }], characters: ["JUNE"], camera: "static", seconds: 6 }, sc);
  assert.deepEqual(R.shotChanges(a, c).map(x => x.kind), ["action", "line_removed", "characters"]);
});
