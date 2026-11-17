/* The screenplay's rules, as a screenwriting app applies them while you type: what a line is, what comes after it (Enter),
   what Tab turns it into, what to offer as you type (cues, scene headings, transitions), the script's statistics, the
   differences between two drafts, locked scene numbers, and what a change of the screenplay changed on a storyboard shot.
   No DOM here: the editor (editor.js) and the storyboard (app.js) use it, and tests run it in node
   (tests/unit/screenplay/rules.test.mjs). The element types are the server's (screenplay.py parse): scene, action,
   character, parenthetical, dialogue, transition, centered, lyrics, section, synopsis, page_break. */

export const TYPES = [["scene", "Scene heading"], ["action", "Action"], ["character", "Character"], ["dialogue", "Dialogue"],
  ["parenthetical", "Parenthetical"], ["transition", "Transition"], ["centered", "Centered"], ["lyrics", "Lyrics"],
  ["section", "Section"], ["synopsis", "Synopsis"]];
export const LABEL = Object.fromEntries(TYPES);
export const UPPER = new Set(["scene", "character", "transition"]);
// Alt+digit: the element shortcuts (7 is dual dialogue, on a cue)
export const SHORTCUT = { 1: "scene", 2: "action", 3: "character", 4: "dialogue", 5: "parenthetical", 6: "transition", 7: "dual", 8: "centered", 9: "lyrics", 0: "synopsis" };

/* Enter at the end of a line: what the next line is (Final Draft's defaults: a speech after a cue, a scene after a
   transition, action after dialogue). */
export const NEXT = { scene: "action", action: "action", character: "dialogue", parenthetical: "dialogue", dialogue: "action",
  transition: "scene", centered: "action", lyrics: "lyrics", section: "scene", synopsis: "action" };

/* Tab on an empty line: the elements that can come next here, likeliest first, chosen by the element before it. Tab walks
   the list from the line's element (Shift+Tab walks back): after dialogue, Enter gives action and Tab the next speaker;
   after a cue, Enter gives the speech and Tab a parenthetical; after a scene heading or action, Tab gives a character. */
export const CYCLES = {
  start: ["scene", "action", "transition", "centered"],
  scene: ["action", "character", "transition"],
  action: ["action", "character", "transition", "scene"],
  character: ["dialogue", "parenthetical"],
  parenthetical: ["dialogue", "parenthetical"],
  dialogue: ["action", "character", "parenthetical", "transition"],
  transition: ["scene", "action"],
  centered: ["action", "scene", "character"],
  lyrics: ["lyrics", "action", "character"],
};
const contextOf = prev => CYCLES[prev] ? prev : prev === "section" || prev === "synopsis" || prev === "page_break" || !prev ? "start" : "action";

/* Tab (back: Shift+Tab). ctx: {type, prev (the element before, "" at the start), empty, atEnd}. ->
   {insert: type} a new element after this one (a parenthetical at the end of a cue or a speech, the speech again after
   a parenthetical, as Final Draft does), or {type} this element becomes. */
export function tab({ type, prev, empty, atEnd }, back = false) {
  if (!empty && atEnd && !back) {
    if (type === "character" || type === "dialogue") return { insert: "parenthetical" };
    if (type === "parenthetical") return { insert: "dialogue" };
  }
  const list = CYCLES[contextOf(prev)];
  const i = list.indexOf(type), n = list.length;
  if (i < 0) return { type: back ? list[n - 1] : list[0] };
  let next = list[(i + (back ? n - 1 : 1)) % n];
  if (next === type) next = type === "action" ? "character" : "action";   // a one-element list still changes something
  return { type: next };
}

export const HEADING = /^(?:INT\.?\/EXT|EXT\.?\/INT|INT|EXT|EST|I\/E|E\/I)[. ]/i;
const ABBREV_END = /\b(?:MR|MRS|MS|DR|JR|SR|ST|LT|SGT|CPL|PVT|CAPT|COL|GEN|PROF|REV|NO)\.$/;
const EXTENSION = /\s*\([^)]*\)\s*/g;

/* Could this line be a character cue? (screenplay.py is_cue) All capitals, extensions in parentheses may be lower case,
   a name's length, no sentence punctuation. */
export function isCue(s) {
  s = (s || "").trim();
  if (s.startsWith("@")) return s.length > 1;
  const name = s.replace(/\^$/, "").trim().replace(EXTENSION, " ").trim();
  if (!name || !/\p{L}/u.test(name) || name !== name.toUpperCase() || name.length > 38) return false;
  if (/[,!?:;"“”]/.test(name) || HEADING.test(name)) return false;
  if (name.endsWith(".") && !ABBREV_END.test(name)) return false;
  return true;
}
/* The character a cue names: "JUNE (CONT'D)" -> "JUNE". */
export const cueName = s => (s || "").trim().replace(/^@/, "").replace(/\^$/, "").replace(EXTENSION, " ").replace(/\s+/g, " ").trim().toUpperCase();
const hasExtension = s => /\([^)]*\)\s*\^?\s*$/.test((s || "").trim());
const isTransition = s => s === s.toUpperCase() && /[A-Z]/.test(s) && /TO:$/.test(s) && s.length > 3;
const isParen = s => /^\(.*\)$/s.test((s || "").trim());

/* What a line the writer just finished really is (on Enter, and as the hint while typing): Fountain's forcing marks
   (. @ > >< ! ~ = # ===, the mark is taken off), a scene heading, a transition ("CUT TO:"), a parenthetical inside a
   speech, and a known character's name in capitals on an action line (a cue: the next line is their speech). Unknown
   names in capitals stay action (a shout, a sign): Tab makes them a cue. ctx: {prev, known: Set of cue names}. ->
   {type, text} or null (nothing to change). */
export function detect(text, type, ctx = {}) {
  const t = (text || "").trim();
  if (!t) return null;
  if (type === "action") {
    if (/^={3,}$/.test(t)) return { type: "page_break", text: "" };
    const sec = t.match(/^(#{1,6})\s*(\S.*)$/s);
    if (sec) return { type: "section", text: sec[2], depth: sec[1].length };
    if (/^=(?!=)\s*\S/.test(t)) return { type: "synopsis", text: t.replace(/^=\s*/, "") };
    if (/^>.*<$/s.test(t) && t.length > 2) return { type: "centered", text: t.slice(1, -1).trim() };
    if (/^>\s*\S/.test(t)) return { type: "transition", text: t.slice(1).trim() };
    if (/^~/.test(t)) return { type: "lyrics", text: t.slice(1).trim() };
    if (/^!\S/.test(t)) return { type: "action", text: t.slice(1), forced: true };
    if (/^@\S/.test(t)) return { type: "character", text: t.slice(1).trim(), forced: true };
    if (/^\.[^.\s]/.test(t) && !HEADING.test(t)) return { type: "scene", text: t.slice(1).trim() };
    if (HEADING.test(t)) return { type: "scene", text: t };
    if (isTransition(t)) return { type: "transition", text: t };
    if (isParen(t) && ["character", "parenthetical", "dialogue"].includes(ctx.prev)) return { type: "parenthetical", text: t };
    if (isCue(t) && !t.includes("\n") && (hasExtension(t) || (ctx.known && ctx.known.has(cueName(t))))) return { type: "character", text: t };
    return null;
  }
  if (type === "dialogue" && isParen(t)) return { type: "parenthetical", text: t };
  if (type === "character" && t.startsWith("@")) return { type: "character", text: t.slice(1).trim(), forced: true };
  if (type === "scene" && /^\.[^.]/.test(t)) return { type: "scene", text: t.slice(1).trim() };
  return null;
}

/* ---------------------------------------------------------------- SmartType: what to offer while typing */
const PREFIXES = ["INT. ", "EXT. ", "INT./EXT. ", "EXT./INT. ", "EST. ", "I/E "];
const TIMES = ["DAY", "NIGHT", "MORNING", "AFTERNOON", "EVENING", "DAWN", "DUSK", "SUNRISE", "SUNSET", "LATER", "CONTINUOUS",
  "MOMENTS LATER", "SAME TIME", "THE NEXT DAY"];
const EXTENSIONS = [["V.O.", "voice-over"], ["O.S.", "off screen"], ["O.C.", "off camera"], ["CONT'D", "continues"],
  ["PRE-LAP", "heard before the cut"], ["FILTERED", "on a phone, a radio"], ["INTO PHONE", ""]];
const TRANSITIONS = ["CUT TO:", "DISSOLVE TO:", "SMASH CUT TO:", "MATCH CUT TO:", "JUMP CUT TO:", "FADE TO:", "FADE OUT.",
  "FADE TO BLACK.", "CUT TO BLACK.", "INTERCUT WITH:", "BACK TO:", "TIME CUT:"];

/* The heading's parts: "INT. DINER - NIGHT" -> {prefix: "INT. ", place: "DINER", time: "NIGHT"} (time null before " - ") */
function headingParts(h) {
  const m = (h || "").match(/^((?:INT\.?\/EXT|EXT\.?\/INT|INT|EXT|EST|I\/E|E\/I)[. ]\s*)(.*)$/i);
  if (!m) return { prefix: "", place: h || "", time: null };
  const rest = m[2], k = rest.search(/\s+[-–—]\s+/);
  if (k < 0) return { prefix: m[1], place: rest, time: null };
  return { prefix: m[1], place: rest.slice(0, k).trim(), time: rest.slice(k).replace(/^\s+[-–—]\s+/, "") };
}

const rankBy = (list, q) => {
  const Q = q.toUpperCase();
  return list.filter(x => x.key.toUpperCase() !== Q || x.more)
    .map(x => ({ x, r: x.key.toUpperCase().startsWith(Q) ? 0 : x.key.toUpperCase().includes(Q) ? 1 : 9 }))
    .filter(o => o.r < 9).sort((a, b) => a.r - b.r || (b.x.weight || 0) - (a.x.weight || 0))
    .filter((o, _i, all) => o.r === 0 || all[0].r !== 0)     // what starts with it; only when nothing does, what has it inside
    .map(o => o.x);
};

/* What to offer for the line being typed (the caret at its end). ctx: the script around it: {cues: [[name, speeches]],
   speakers: the scene's speakers before this line, latest first, places: [[place, uses]], times: [used times],
   names: other names (Studio's characters), transitions: used ones}. -> [{text (the whole line once chosen), label,
   hint, keep (choosing it keeps the list open: a prefix, a place)}] (at most 8). */
export function suggest(type, text, ctx = {}) {
  const t = text || "";
  if (type === "scene") {
    const p = headingParts(t);
    if (!p.prefix) {
      const up = t.toUpperCase().replace(/^\./, "");
      if (/\s/.test(up.trim()) && !PREFIXES.some(x => x.trim().startsWith(up.trim()))) return [];
      return PREFIXES.filter(x => x.startsWith(up) && x.trim() !== up.trim()).map(x => ({ text: x, label: x.trim(), hint: "", keep: true }));
    }
    if (p.time === null) {
      const places = (ctx.places || []).map(([k, n]) => ({ key: k, weight: n }));
      return rankBy(places, p.place).slice(0, 8).map(x => ({ text: `${p.prefix}${x.key} - `, label: x.key, hint: plural(x.weight, "scene"), keep: true }));
    }
    const used = ctx.times || [], all = [...used, ...TIMES.filter(x => !used.includes(x))].map((k, i) => ({ key: k, weight: -i, more: false }));
    return rankBy(all, p.time).slice(0, 8).map(x => ({ text: `${p.prefix}${p.place} - ${x.key}`, label: x.key, hint: used.includes(x.key) ? "used" : "" }));
  }
  if (type === "character") {
    const up = t.toUpperCase();
    const open = up.lastIndexOf("(");
    if (open >= 0 && !up.slice(open).includes(")")) {       // an extension: JUNE (V.O.)
      const q = up.slice(open + 1).trim(), name = up.slice(0, open).trim();
      return EXTENSIONS.filter(([e]) => e.startsWith(q)).map(([e, h]) => ({ text: `${name} (${e})`, label: `(${e})`, hint: h }));
    }
    if (up.includes(")")) return [];
    if ((ctx.cues || []).some(([n]) => n === up.trim())) return [];   // a cue of the script, typed in full: nothing to offer
    const scene = ctx.speakers || [];
    // who speaks next: in a conversation, the one who spoke before the last speaker; then the scene's others; then by
    // lines; the one who just spoke last of all (they'd go on with the same speech)
    const order = [], last = scene[0];
    if (scene.length > 1) order.push(scene[1]);
    scene.forEach(n => { if (!order.includes(n) && n !== last) order.push(n); });
    const counts = new Map(ctx.cues || []);
    [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a)).forEach(n => { if (!order.includes(n) && n !== last) order.push(n); });
    (ctx.names || []).map(n => n.toUpperCase()).forEach(n => { if (!order.includes(n) && n !== last) order.push(n); });
    if (last) order.push(last);
    const list = order.map((n, i) => ({ key: n, weight: -i }));
    const hits = up.trim() ? rankBy(list, up.trim()) : list;
    return hits.slice(0, 8).map(x => ({ text: x.key, label: x.key,
      hint: x.key === scene[1] ? "next in the conversation" : scene.includes(x.key) ? "in this scene" : counts.has(x.key) ? plural(counts.get(x.key), "line") : "character" }));
  }
  if (type === "transition") {
    const used = ctx.transitions || [], all = [...used, ...TRANSITIONS.filter(x => !used.includes(x))].map((k, i) => ({ key: k, weight: -i }));
    return rankBy(all, t.trim()).slice(0, 8).map(x => ({ text: x.key, label: x.key, hint: used.includes(x.key) ? "used" : "" }));
  }
  return [];
}
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

/* The script's vocabulary for SmartType, from its elements: cues by speeches, places by scenes, times, transitions. */
export function vocabulary(els) {
  const cues = new Map(), places = new Map(), times = [], trans = [];
  for (const e of els) {
    if (e.type === "character") { const n = cueName(e.text); if (n) cues.set(n, (cues.get(n) || 0) + 1); }
    else if (e.type === "scene") {
      const p = headingParts((e.text || "").toUpperCase());
      if (p.prefix && p.place) places.set(p.place, (places.get(p.place) || 0) + 1);
      if (p.time && !times.includes(p.time)) times.push(p.time);
    } else if (e.type === "transition") { const x = (e.text || "").toUpperCase().trim(); if (x && !trans.includes(x)) trans.push(x); }
  }
  return { cues: [...cues], places: [...places].sort((a, b) => b[1] - a[1]), times, transitions: trans };
}
/* The speakers of the scene before element k, latest first (for "who speaks next"). */
export function speakersBefore(els, k) {
  const out = [];
  for (let i = k - 1; i >= 0; i--) {
    const e = els[i];
    if (e.type === "scene") break;
    if (e.type === "character") { const n = cueName(e.text); if (n && !out.includes(n)) out.push(n); }
  }
  return out;
}

/* ---------------------------------------------------------------- the script's statistics */
const words = s => ((s || "").match(/[\p{L}\p{N}'’-]+/gu) || []).length;
/* els: the elements in order; scenes: the breakdown's scenes (eighths, numbers), matched to the headings in order. */
export function report(els, scenes = []) {
  const out = { words: 0, dialogueWords: 0, actionWords: 0, speeches: 0, scenes: [], characters: [], locations: [] };
  const chars = new Map(), locs = new Map();
  let cur = null, who = "";
  const sceneOf = () => cur || (cur = { number: "", heading: "(before the first scene)", words: 0, speakers: new Set(), intext: "", time: "", eighths: 0 });
  els.forEach(e => {
    const w = words(e.text);
    out.words += w;
    if (e.type === "scene") {
      const sc = scenes[out.scenes.length] || {};
      const p = headingParts((e.text || "").toUpperCase());
      cur = { number: sc.number || String(out.scenes.length + 1), heading: e.text, words: 0, speakers: new Set(), intext: (p.prefix || "").replace(/[. ]+$/, ""),
        time: p.time || "", place: p.place || "", eighths: sc.eighths || 0 };
      out.scenes.push(cur);
      if (cur.place) {
        const l = locs.get(cur.place) || { place: cur.place, scenes: 0, intext: new Set(), times: new Set() };
        l.scenes++; if (cur.intext) l.intext.add(cur.intext); if (cur.time) l.times.add(cur.time); locs.set(cur.place, l);
      }
      return;
    }
    sceneOf().words += w;
    if (e.type === "character") {
      who = cueName(e.text);
      const c = chars.get(who) || { name: who, speeches: 0, words: 0, scenes: new Set(), first: cur ? cur.number : "" };
      c.speeches++; c.scenes.add(cur ? cur.number : ""); chars.set(who, c); cur && cur.speakers.add(who); out.speeches++;
    } else if (e.type === "dialogue") {
      out.dialogueWords += w; const c = chars.get(who); if (c) c.words += w;
    } else if (e.type === "action") out.actionWords += w;
  });
  out.scenes = out.scenes.map(s => ({ ...s, speakers: [...s.speakers] }));
  out.characters = [...chars.values()].map(c => ({ ...c, scenes: c.scenes.size })).sort((a, b) => b.speeches - a.speeches || b.words - a.words);
  out.locations = [...locs.values()].map(l => ({ ...l, intext: [...l.intext].join("/"), times: [...l.times] })).sort((a, b) => b.scenes - a.scenes);
  const sized = out.scenes.filter(s => s.eighths);
  out.longest = sized.slice().sort((a, b) => b.eighths - a.eighths)[0] || null;
  out.shortest = sized.slice().sort((a, b) => a.eighths - b.eighths)[0] || null;
  const count = (k, v) => out.scenes.filter(s => (s[k] || "").toUpperCase().includes(v)).length;
  out.int = count("intext", "INT"); out.ext = count("intext", "EXT");
  out.day = out.scenes.filter(s => /DAY|MORNING|AFTERNOON|DAWN|SUNRISE/.test(s.time)).length;
  out.night = out.scenes.filter(s => /NIGHT|EVENING|DUSK|SUNSET/.test(s.time)).length;
  return out;
}
export const eighthsText = n => { const p = Math.floor(n / 8), r = n % 8; return (p ? p + (r ? " " : "") : "") + (r ? r + "/8" : "") || "0"; };

/* ---------------------------------------------------------------- differences (drafts, revision marks) */
/* Myers' diff of two lists (compared with eq) -> [[op, item]] with op " " kept, "-" only in a, "+" only in b. */
export function diff(a, b, eq = (x, y) => x === y) {
  const n = a.length, m = b.length, max = n + m, v = new Map([[1, 0]]), trace = [];
  let found = false;
  for (let d = 0; d <= max && !found; d++) {
    trace.push(new Map(v));
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && (v.get(k - 1) ?? -1) < (v.get(k + 1) ?? -1)) ? v.get(k + 1) ?? 0 : (v.get(k - 1) ?? 0) + 1;
      let y = x - k;
      while (x < n && y < m && eq(a[x], b[y])) { x++; y++; }
      v.set(k, x);
      if (x >= n && y >= m) { found = true; break; }
    }
  }
  const out = [];
  let x = n, y = m;
  for (let d = trace.length - 1; d >= 0 && (x > 0 || y > 0); d--) {
    const vv = trace[d], k = x - y;
    const prevK = k === -d || (k !== d && (vv.get(k - 1) ?? -1) < (vv.get(k + 1) ?? -1)) ? k + 1 : k - 1;
    const px = vv.get(prevK) ?? 0, py = px - prevK;
    while (x > px && y > py) { out.push([" ", a[x - 1]]); x--; y--; }
    if (d > 0) { if (x === px) out.push(["+", b[y - 1]]); else out.push(["-", a[x - 1]]); }
    x = px; y = py;
  }
  return out.reverse();
}
/* Which elements of `now` are new or changed since `then` (the revision marks: an asterisk in the margin). -> Set of
   indexes into now. */
export function revised(then, now) {
  const key = e => `${e.type}|${(e.text || "").trim()}`;
  const out = new Set();
  let j = 0;
  for (const [op] of diff(then.map(key), now.map(key))) { if (op === "+") out.add(j); if (op !== "-") j++; }
  return out;
}

/* ---------------------------------------------------------------- scene numbers */
/* The numbers the scenes show: a scene with its own number keeps it (a locked script); one without counts on from the
   scene before, and between locked numbers takes a letter (a scene added after 12 is 12A, before the first A1), as a
   shooting script numbers them. */
export function sceneNumbers(own) {
  const anyLocked = own.some(Boolean), out = [];
  let last = null, count = 0;
  own.forEach((n, i) => {
    if (n) { out.push(n); last = n; count = parseInt(n, 10) || count; return; }
    if (!anyLocked) { out.push(String(++count)); return; }
    if (last == null) {                                  // before the first locked number: A1, B1…
      const first = own.slice(i).find(Boolean) || "1";
      const k = own.slice(0, i).filter(x => !x).length;
      out.push(letter(k) + first.replace(/^[A-Z]+(?=\d)/, ""));
      return;
    }
    const base = last.replace(/[A-Z]+$/, ""), suffix = last.slice(base.length);
    const nextLetter = suffix ? letter(letterIndex(suffix) + 1) : "A";
    last = base + nextLetter; out.push(last);
  });
  return out;
}
const LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ";                    // no I or O, as shot and scene letters are written
const letter = k => { let s = ""; k += 1; while (k) { const r = (k - 1) % 24; k = Math.floor((k - 1) / 24); s = LETTERS[r] + s; } return s; };
const letterIndex = s => [...s].reduce((a, c) => a * 24 + LETTERS.indexOf(c) + 1, 0) - 1;

/* ---------------------------------------------------------------- find, replace, rename */
const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
export function finder(q, { cs = false, word = false } = {}) {
  if (!q) return null;
  return new RegExp((word ? "(?<![\\p{L}\\p{N}])" : "") + reEsc(q) + (word ? "(?![\\p{L}\\p{N}])" : ""), "gu" + (cs ? "" : "i"));
}
export function findIn(text, q, opts) {
  const re = finder(q, opts); if (!re) return [];
  return [...(text || "").matchAll(re)].map(m => [m.index, m.index + m[0].length]);
}
/* A name replaced the way it is written where it stands: RAY -> JOE in a cue, Ray -> Joe in action. */
export function renameIn(text, from, to) {
  const re = finder(from, { word: true });
  return (text || "").replace(re, m => m === m.toUpperCase() ? to.toUpperCase() : m[0] === m[0].toUpperCase() ? to[0].toUpperCase() + to.slice(1) : to.toLowerCase());
}

/* ---------------------------------------------------------------- the storyboard follows the screenplay */
/* What a storyboard shot is made from, as the breakdown has it: its action, its lines, who is in it, where and when, the
   camera and the length. Kept on the board shot as it was when the shot was last looked at, so a change of the
   screenplay can be said in words (shotChanges). */
export function shotSource(s, scene) {
  return { action: (s.action || "").trim(), lines: (s.lines || []).map(l => ({ who: l.who || "", text: (l.text || "").trim(), ...(l.paren ? { paren: l.paren } : {}) })),
    chars: [...(s.characters || [])], setting: scene ? (scene.heading || "") : "", camera: s.camera || "", seconds: +s.seconds || 0 };
}
/* The changes from `then` to `now` (shotSource), in the terms a writer and a director use. */
export function shotChanges(then, now) {
  if (!then) return [{ kind: "new" }];
  const out = [];
  if (then.setting !== now.setting) out.push({ kind: "setting", from: then.setting, to: now.setting });
  if (then.action !== now.action) out.push({ kind: "action", from: then.action, to: now.action });
  const key = l => `${l.who}: ${l.text}`;
  const ops = diff((then.lines || []).map(key), (now.lines || []).map(key));
  for (let i = 0; i < ops.length; i++) {
    const [op, v] = ops[i];
    if (op === " ") continue;
    const [who, ...rest] = v.split(": "), line = rest.join(": ");
    const nx = ops[i + 1];
    if (op === "-" && nx && nx[0] === "+" && nx[1].split(": ")[0] === who) {   // the same speaker, the line rewritten
      out.push({ kind: "line_changed", who, from: line, to: nx[1].split(": ").slice(1).join(": ") }); i++; continue;
    }
    out.push(op === "+" ? { kind: "line_added", who, line } : { kind: "line_removed", who, line });
  }
  const a = (then.chars || []).join(", "), b = (now.chars || []).join(", ");
  if (a !== b) out.push({ kind: "characters", from: then.chars || [], to: now.chars || [] });
  if (then.camera !== now.camera) out.push({ kind: "camera", from: then.camera, to: now.camera });
  if (then.seconds !== now.seconds) out.push({ kind: "length", from: then.seconds, to: now.seconds });
  return out;
}
