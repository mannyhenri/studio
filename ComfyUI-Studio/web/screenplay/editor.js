/* The screenplay editor: the screenplay typed on the Hollywood standard page itself, as a screenwriting app does it.

   Writing: Enter ends a line and opens the next one (a cue's speech, a scene after a transition, action after dialogue),
   reading the line first (a known name in capitals is a cue, "CUT TO:" a transition, Fountain's forcing marks . @ > ! ~ =
   #); Tab picks the element the place calls for and walks on (rules.js tab); SmartType offers the script's own
   characters (the next speaker of a conversation first), scene heading prefixes, places and times, transitions and cue
   extensions; Alt+1…0 set an element; Ctrl+B/I/U emphasis (Fountain's ** * _); dual dialogue; notes [[…]].
   Around the page: the scene navigator (drag to reorder), index cards (a synopsis per scene, drag to reorder), find and
   replace, rename a character, statistics, locked scene numbers, drafts (compare, restore, revision marks), exports
   (PDF, PDF with revision marks, Final Draft, Fountain, plain text), the local AI's writing help, focus mode, zoom, the
   typewriter's sound, the shortcuts. In the margin: where each shot of the breakdown starts, and which storyboard shots
   the last changes of the screenplay changed.

   Saving: 0.8 s after typing stops, the elements go to the server, which writes the Fountain text and makes the
   breakdown again (scenes, shots); the storyboard then follows (ctx.sync: the shots' prompts, new shots, cut ones, and
   what changed on each, said in words). */
import * as R from "./rules.js";

const PH = { scene: "INT. PLACE - DAY", action: "What we see…", character: "NAME", parenthetical: "(how it's said)", dialogue: "What they say…",
  transition: "CUT TO:", centered: "Centered text", lyrics: "Lyrics", section: "Section (not printed)", synopsis: "Synopsis (not printed)" };
const REV_COLORS = ["White", "Blue", "Pink", "Yellow", "Green", "Goldenrod", "Buff", "Salmon", "Cherry"];
const pref = { get: (k, d) => { try { const v = localStorage.getItem("studio.sp." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem("studio.sp." + k, JSON.stringify(v)); } catch { } } };

/* the page's blocks: one element each (a div of plain text, its line breaks as "\n") */
export function block(e) {
  const d = document.createElement("div");
  d.className = "sp-b sp-" + e.type; d.dataset.t = e.type;
  if (e.type === "page_break") { d.contentEditable = "false"; return d; }
  if (e.number) d.dataset.n = e.number;
  if (e.dual) d.dataset.dual = "1";
  if (e.depth) d.dataset.depth = e.depth;
  if (e.type === "character" && (e.forced || /\p{Ll}/u.test(e.text || ""))) d.dataset.f = "1";   // @McCLANE: written as typed
  d.dataset.ph = PH[e.type] || "";
  setText(d, e.text || "");
  return d;
}
export function setText(b, t) {
  b.textContent = t;
  if (!t || t.endsWith("\n")) b.append(document.createElement("br"));  // an empty block (or a last empty line) needs a <br> to show
  b.classList.toggle("sp-empty", !t);
}
export const textOf = b => { let t = ""; b.childNodes.forEach(n => { t += n.nodeType === 3 ? n.data : n.nodeName === "BR" ? (n === b.lastChild ? "" : "\n") : n.textContent; }); return t.replace(/\u00a0/g, " "); };

/* the typewriter (Film Engine's): a key's clack, the carriage's thump on Enter, the bell near the end of a line */
let audio = null;
function tw(kind) {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    const a = audio, t = a.currentTime;
    if (kind === "bell") {
      const o = a.createOscillator(), g = a.createGain();
      o.frequency.value = 2400; o.type = "sine"; g.gain.setValueAtTime(0.12, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.9);
      o.connect(g).connect(a.destination); o.start(t); o.stop(t + 0.9); return;
    }
    const len = kind === "thump" ? 0.09 : 0.035, buf = a.createBuffer(1, Math.ceil(a.sampleRate * len), a.sampleRate), ch = buf.getChannelData(0);
    for (let i = 0; i < ch.length; i++) ch[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / ch.length, kind === "thump" ? 2 : 4);
    const src = a.createBufferSource(), f = a.createBiquadFilter(), g = a.createGain();
    f.type = "bandpass"; f.frequency.value = kind === "thump" ? 180 : 1800 + Math.random() * 900; g.gain.value = kind === "thump" ? 0.5 : 0.22;
    src.buffer = buf; src.connect(f).connect(g).connect(a.destination); src.start(t);
  } catch { }
}

export async function mount(b, pr, ctx) {
  const { $, $$, h, esc, post, toast, plural } = ctx;
  pr.screenplay = pr.screenplay || { text: "", source: "new" };
  const sp = pr.screenplay;
  const typeOpts = R.TYPES.map(([v, l]) => `<option value="${v}">${l}${Object.entries(R.SHORTCUT).find(([, t]) => t === v) ? "  ⌥" + Object.entries(R.SHORTCUT).find(([, t]) => t === v)[0] : ""}</option>`).join("");
  const d = h(`<div class="spw">
    <div class="sp-bar">
      <div class="sp-row">
        <select id="sp-type" title="The element the cursor is in. Tab picks the next one for you; Alt+1…0 sets one">${typeOpts}</select>
        <span class="sp-grp"><button class="ghost sm" data-em="**" title="Bold (Ctrl+B)"><b>B</b></button><button class="ghost sm" data-em="*" title="Italic (Ctrl+I)"><i>I</i></button><button class="ghost sm" data-em="_" title="Underline (Ctrl+U)"><u>U</u></button>
          <button class="ghost sm" id="sp-dual" title="Dual dialogue: this speech beside the one before it (Alt+7)">⇆ Dual</button><button class="ghost sm" id="sp-note" title="A note [[…]]: kept in the screenplay, not printed">✎ Note</button></span>
        <span class="sp-grp"><button class="ghost sm" id="sp-undo" title="Undo (Ctrl+Z)">↶</button><button class="ghost sm" id="sp-redo" title="Redo (Ctrl+Shift+Z)">↷</button></span>
        <span class="sp-grp"><button class="ghost sm" id="sp-find" title="Find and replace (Ctrl+F)">⌕ Find</button><button class="ghost sm" id="sp-navb" title="The scene navigator (Ctrl+Shift+O)">☰ Scenes</button>
          <span class="sp-seg"><button id="sp-vpage" class="on" title="The screenplay's page">Page</button><button id="sp-vcards" title="Index cards: a card per scene, its synopsis; drag to reorder">Cards</button><button id="sp-vtreat" title="The treatment: the story in prose, what the screenplay is written from">Treatment</button></span></span>
        <select id="sp-jump" title="Go to a scene"><option value="">Scenes…</option></select>
        <button class="sp-sb" id="sp-sb" hidden></button>

      </div>
      <div class="sp-row sp-row2"><span class="sp-meta" id="sp-stats"></span><span class="sp-save" id="sp-save"></span><span class="sp-hintline" id="sp-flow"></span>
        <span class="sp-tools">
          <button class="ghost sm" id="sp-tp" title="The title page: title, credit, authors, draft, contact">▤ Title page</button>
          <button class="ghost sm" id="sp-drafts" title="Drafts: save this version, compare, restore; revision marks">◫ Drafts</button>
          <button class="ghost sm" id="sp-stat" title="Statistics: pages, runtime, characters, scenes, places">▦ Reports</button>
          <button class="ghost sm" id="sp-ai" title="Writing help from the local AI">✦ AI ▾</button>
          <button class="ghost sm" id="sp-exp" title="PDF, Final Draft, Fountain, plain text">⤓ Export ▾</button>
          <button class="ghost sm" id="sp-more" title="More">⋯</button>
          <input type="file" id="sp-ref" accept="${ctx.accept}" hidden></span></div>
    </div>
    <div class="sp-findbar hidden">
      <input type="text" id="sp-fq" placeholder="Find" spellcheck="false"><span class="sp-fcount" id="sp-fn"></span>
      <button class="ghost sm" id="sp-fprev" title="Previous (Shift+Enter)">↑</button><button class="ghost sm" id="sp-fnext" title="Next (Enter)">↓</button>
      <button class="ghost sm sp-tg" id="sp-fcs" title="Match case">Aa</button><button class="ghost sm sp-tg" id="sp-fw" title="Whole words">W</button>
      <input type="text" id="sp-fr" placeholder="Replace with" spellcheck="false"><button class="ghost sm" id="sp-fr1">Replace</button><button class="ghost sm" id="sp-fra">Replace all</button>
      <button class="icon" id="sp-fx" title="Close (Esc)">✕</button></div>
    <div class="sp-main">
      <aside class="sp-nav hidden"><div class="sp-navh"><b>Scenes</b><span class="hint">drag to reorder</span></div><div class="sp-navl"></div></aside>
      <div class="sp-desk"><div class="sp-sheet">
        <div class="sp-ed" spellcheck="true"></div><div class="sp-marks"></div><div class="sp-shots"></div><div class="sp-revs"></div></div>
        <div class="sp-focusbar"><span id="sp-fw2"></span><button class="ghost sm" id="sp-fexit">Leave focus (Esc)</button></div></div>
      <div class="sp-cards hidden"></div>
      <div class="sp-treat hidden"><p class="hint">The story in prose, in order: what happens, no dialogue, no format. The screenplay is written from it; the local AI can draft scenes from it too. Kept with the project, not printed.</p>
        <textarea id="sp-tt" placeholder="Act one opens on…" spellcheck="true"></textarea>
        <div class="row"><span class="hint" id="sp-tm"></span><button class="ghost" id="sp-tai" style="margin-left:auto">✦ Draft scenes from it</button></div></div>
    </div>
    <p class="hint sp-keys"><b>Enter</b> the next element (a cue's speech, a scene after a transition) · <b>Tab</b> the element this place calls for (after a speech: the next speaker; at the end of a cue or a speech: a parenthetical) · <b>Shift+Enter</b> a new line in the same element · <b>Ctrl+/</b> all shortcuts · ${sp.filename ? `imported from ${esc(sp.filename)}` : "saved as Fountain"}</p>
  </div>`);
  b.append(d);
  const ed = $(".sp-ed", d), typeSel = $("#sp-type", d), status = $("#sp-save", d), sheet = $(".sp-sheet", d);
  ed.contentEditable = "true";
  let tp = [], seq = 0, tSave = null, tCheck = null, lastBlock = null, marks = [], lineOf = new Map();
  const hist = [], histAt = { i: -1 };
  const live = () => d.isConnected;
  const cleanups = [];
  const onDoc = (ev, fn, opt) => { document.addEventListener(ev, fn, opt); cleanups.push(() => document.removeEventListener(ev, fn, opt)); };
  const gone = () => { if (live()) return false; cleanups.splice(0).forEach(f => f()); closePop(); hideChip(); document.body.classList.remove("sp-focus"); return true; };

  /* --- the model: the blocks on the page <-> elements */
  const blocks = () => [...ed.children];
  const readEls = () => blocks().map((bl, i) => {
    const t = bl.dataset.t;
    if (t === "page_break") return { type: t, text: "", i };
    let x = textOf(bl).replace(/[ \t]+$/gm, "").replace(/^\n+|\n+$/g, "");
    if (R.UPPER.has(t) && !bl.dataset.f) x = x.toUpperCase();
    if (!x.trim()) return null;
    const e = { type: t, text: x, i };
    if (t === "scene" && bl.dataset.n) e.number = bl.dataset.n;
    if (bl.dataset.dual) e.dual = true;
    if (bl.dataset.depth) e.depth = +bl.dataset.depth;
    return e;
  }).filter(Boolean);
  const titlePage = () => tp.filter(([, v]) => v);
  const render = els => {
    ed.innerHTML = "";
    const list = els.length ? els : [{ type: "scene", text: "" }];
    ed.append(...list.map(block));
    numberScenes(); transitions();
  };
  const transitions = () => blocks().forEach(bl => { if (bl.dataset.t === "transition") bl.classList.toggle("long", textOf(bl).length > 14); });
  // scene numbers: counted, or the locked ones with letters for scenes added since (rules.sceneNumbers)
  const numberScenes = () => {
    const heads = blocks().filter(bl => bl.dataset.t === "scene");
    const nums = R.sceneNumbers(heads.map(bl => bl.dataset.n || null));
    const jump = $("#sp-jump", d), opts = [`<option value="">Scenes…</option>`];
    heads.forEach((bl, k) => { bl.dataset.o = nums[k]; opts.push(`<option value="${blocks().indexOf(bl)}">${esc(nums[k])}. ${esc(textOf(bl).slice(0, 60) || "(new scene)")}</option>`); });
    jump.innerHTML = opts.join(""); if (!heads.length) jump.options[0].textContent = "No scenes yet";
    soon("nav", drawNav, 250);
  };
  const timers = {};
  const soon = (k, fn, ms) => { clearTimeout(timers[k]); timers[k] = setTimeout(() => { if (live()) fn(); }, ms); };
  // the elements before a block, skipping what doesn't print (for Tab and Enter: "the element before")
  const prevType = bl => { let p = bl.previousElementSibling; while (p && (["section", "synopsis", "page_break"].includes(p.dataset.t) || !textOf(p).trim())) p = p.previousElementSibling; return p ? p.dataset.t : ""; };
  const headOf = bl => { let p = bl; while (p && p.dataset.t !== "scene") p = p.previousElementSibling; return p; };

  /* --- caret */
  const caret = () => {
    const sel = getSelection(); if (!sel.rangeCount) return null;
    const r = sel.getRangeAt(0);
    const node = r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement;
    const bl = node && node.closest(".sp-b");
    if (!bl || bl.parentElement !== ed) return null;
    const pre = document.createRange(); pre.selectNodeContents(bl); pre.setEnd(r.startContainer, r.startOffset);
    let end = null;
    if (!r.collapsed) { const n2 = r.endContainer.nodeType === 1 ? r.endContainer : r.endContainer.parentElement; const b2 = n2 && n2.closest(".sp-b"); if (b2 === bl) { const p2 = document.createRange(); p2.selectNodeContents(bl); p2.setEnd(r.endContainer, r.endOffset); end = p2.toString().length; } }
    return { bl, off: pre.toString().length, end, r, collapsed: r.collapsed };
  };
  const pointAt = (bl, off) => {
    let left = off;
    const walk = document.createTreeWalker(bl, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) { if (left <= n.data.length) return [n, left]; left -= n.data.length; }
    return null;
  };
  const setCaret = (bl, off, end) => {
    const r = document.createRange(), a = pointAt(bl, off);
    if (a) r.setStart(a[0], a[1]);
    else { r.selectNodeContents(bl); r.collapse(false); const br = bl.lastChild; if (br && br.nodeName === "BR") r.setStartBefore(br); }
    if (end != null && end !== off && a) { const z = pointAt(bl, end); if (z) r.setEnd(z[0], z[1]); } else r.collapse(true);
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
    lastBlock = bl; typeSel.value = bl.dataset.t;
  };
  const caretPos = () => { const c = caret(); return c ? [blocks().indexOf(c.bl), c.off] : null; };
  const restorePos = pos => { if (!pos) return; const bl = blocks()[Math.min(pos[0], blocks().length - 1)]; if (bl) setCaret(bl, Math.min(pos[1], textOf(bl).length)); };

  /* --- undo: snapshots of the elements and the caret */
  const readRaw = () => blocks().map(bl => ({ type: bl.dataset.t, text: textOf(bl), number: bl.dataset.n, dual: bl.dataset.dual, depth: bl.dataset.depth, forced: !!bl.dataset.f }));
  const snap = () => JSON.stringify(readRaw());
  const checkpoint = () => {
    const s = snap(); if (hist[histAt.i] && hist[histAt.i].s === s) { hist[histAt.i].pos = caretPos(); return; }
    hist.splice(histAt.i + 1); hist.push({ s, pos: caretPos() }); if (hist.length > 300) hist.shift(); histAt.i = hist.length - 1;
  };
  const restore = k => {
    const hs = hist[k]; if (!hs) return;
    histAt.i = k; ed.innerHTML = ""; ed.append(...JSON.parse(hs.s).map(e => block(e)));
    numberScenes(); transitions(); restorePos(hs.pos); changed(false);
  };
  const undo = () => { checkpoint(); if (histAt.i > 0) restore(histAt.i - 1); };
  const redo = () => { if (histAt.i < hist.length - 1) restore(histAt.i + 1); };

  /* --- saving: the elements go to the server, which writes the Fountain text and makes the breakdown again; the
     storyboard follows */
  const setStatus = t => { status.textContent = t; };
  const stats = () => {
    const sc = (pr.scenes || []).length, sh = (pr.shots || []).length, ch = (pr.roles || []).length;
    $("#sp-stats", d).textContent = [plural(sc, "scene"), plural(sh, "shot"), plural(ch, "character"), pr.pages ? `about ${plural(pr.pages, "page")}` : ""].filter(Boolean).join(" · ");
  };
  const save = async () => {
    clearTimeout(tSave);
    const my = ++seq;
    setStatus("saving…");
    try {
      const els = readEls();
      const r = await post("/studio/api/screenplay/breakdown", { elements: els, title_page: titlePage(), scenes: pr.scenes || [], shots: pr.shots || [] });
      if (my !== seq) return;
      sp.text = r.text; Object.assign(pr, { scenes: r.scenes, shots: r.shots, roles: r.roles, pages: r.pages });
      marks = r.page_starts || []; lineOf = new Map((r.el_lines || []).map(([i, ln]) => [i, ln]));
      await ctx.saveProject(pr, true);
      if (my === seq) { ctx.setDirty(false); setStatus("saved"); }
      if (live()) { stats(); drawMarks(); drawRevs(); }
      await followBoard();
    } catch (e) { if (my === seq) { setStatus("not saved"); toast("Couldn't save the screenplay: " + e.message.slice(0, 200), true); } }
  };
  const changed = (typing = true) => {
    ctx.setDirty(true); setStatus("editing…");
    clearTimeout(tSave); tSave = setTimeout(save, 800);
    if (typing) { clearTimeout(tCheck); tCheck = setTimeout(checkpoint, 700); } else checkpoint();
    soon("marks", () => { drawShots(); drawRevMarks(); footer(); }, 120);
  };
  const setTitle = v => { tp = [["Title", v], ...tp.filter(([k]) => k !== "Title")]; changed(false); };
  ctx.S.spFlush = () => (ctx.dirty() && live() ? save() : null);
  ctx.S.spTitle = { set: setTitle, isConnected: live };
  $("#sp-tp", d).onclick = () => ctx.titlePageDialog(tp, { onSave: async (next, title) => {
    tp = next; pr.title = pr.name = title; const t = $(".ptitle"); if (t) t.value = title;
    changed(false); await save(); toast("Title page saved");
  } });

  /* --- the storyboard follows the screenplay: after each save, the board's shots are the breakdown's, and what the save
     changed on them is kept on each (the Storyboard shows it; the button here says how many) */
  const followBoard = async () => {
    const rep = await ctx.sync();
    if (!live() || !rep) return;
    // new in the screenplay: a place (a location now), a speaking part (cast: a Studio character found or made)
    const news = [rep.places.length ? `${rep.places.length > 1 ? "locations" : "a location"}: ${rep.places.join(", ")}` : "",
      rep.chars.length ? `${rep.chars.length > 1 ? "characters" : "a character"} in the cast: ${rep.chars.join(", ")}` : "",
      rep.renamed.length ? rep.renamed.map(([a, z]) => `${a} is now ${z}`).join(", ") : ""].filter(Boolean);
    if (news.length) toast("New in the screenplay: " + news.join(" · "));
    const sb = $("#sp-sb", d);
    sb.hidden = !rep.follows;
    sb.className = "sp-sb" + (rep.pending ? " chg" : "");
    sb.textContent = rep.pending ? `↻ ${plural(rep.pending, "shot")} changed` : "Storyboard & takes ✓ follow the screenplay";
    sb.title = rep.pending ? "The screenplay changed these shots after their frames or takes were made: see what changed" : "The storyboard's frames and the takes are the breakdown's shots, up to date";
    drawShots();
  };
  $("#sp-sb", d).onclick = () => ctx.openChanges();

  /* --- editing: Enter, Tab, Backspace and Delete at the edges, paste */
  const setType = (bl, t, keepDual) => {
    bl.className = "sp-b sp-" + t; bl.dataset.t = t; bl.dataset.ph = PH[t] || "";
    bl.classList.toggle("sp-empty", !textOf(bl));
    if (t !== "scene") delete bl.dataset.n;
    if (t !== "character" && !keepDual) delete bl.dataset.dual;
    if (t !== "section") delete bl.dataset.depth;
    if (t !== "character") delete bl.dataset.f;
    if (t === "transition") bl.classList.toggle("long", textOf(bl).length > 14);
    typeSel.value = t; numberScenes();
  };
  const split = (c, type) => {
    const t = textOf(c.bl), before = t.slice(0, c.off), after = t.slice(c.off);
    setText(c.bl, before);
    const nb = block({ type, text: after.replace(/^[ \t]+/, "") });
    c.bl.after(nb); setCaret(nb, 0); return nb;
  };
  const insertAfter = (bl, type, text = "", off) => { const nb = block({ type, text }); bl.after(nb); setCaret(nb, off == null ? text.length : off); return nb; };
  const clearSelection = () => { const c = caret(); if (c && !c.collapsed) { c.r.deleteContents(); normalize(); } return caret(); };
  let knownAt = 0, knownSet = null;
  const known = () => {
    if (knownSet && Date.now() - knownAt < 1500) return knownSet;
    knownAt = Date.now();
    return (knownSet = new Set([...R.vocabulary(readEls()).cues.map(([n]) => n), ...(pr.roles || []).map(r => (r.name || "").toUpperCase()), ...ctx.names().map(n => n.toUpperCase())]));
  };
  // a line read again: what it is (rules.detect), applied to the block
  const apply = (bl, det) => {
    if (det.type === "page_break") { const nb = block({ type: "page_break" }); bl.replaceWith(nb); const a = block({ type: "action", text: "" }); nb.after(a); setCaret(a, 0); return a; }
    setText(bl, det.text); setType(bl, det.type);
    if (det.depth) bl.dataset.depth = det.depth;
    if (det.forced && det.type === "character") bl.dataset.f = "1";
    return bl;
  };
  const enter = () => {
    checkpoint();
    const c = clearSelection(); if (!c) return;
    let t = textOf(c.bl), type = c.bl.dataset.t;
    if (!t.trim() && type !== "action") { setType(c.bl, "action"); changed(false); suggestFor(c.bl); return; }   // Enter on an empty element: back to action
    let atEnd = c.off >= t.length || (type === "parenthetical" && /^\)\s*$/.test(t.slice(c.off)));
    let bl = c.bl;
    if (atEnd) {
      const det = R.detect(t, type, { prev: prevType(bl), known: known() });
      if (det) { bl = apply(bl, det); if (det.type === "page_break") { changed(false); return; } type = bl.dataset.t; t = textOf(bl); }
    }
    if (typewriter) tw("thump");
    if (atEnd) insertAfter(bl, R.NEXT[type] || "action");
    else split({ ...c, bl }, type);
    numberScenes(); changed(false);
    suggestFor(caret() && caret().bl);
  };
  const doTab = back => {
    const c = caret(); if (!c) return;
    checkpoint();
    const t = textOf(c.bl), type = c.bl.dataset.t;
    const r = R.tab({ type, prev: prevType(c.bl), empty: !t.trim(), atEnd: c.off >= t.length }, back);
    if (r.insert) {
      const nb = insertAfter(c.bl, r.insert, r.insert === "parenthetical" ? "()" : "", r.insert === "parenthetical" ? 1 : 0);
      numberScenes(); changed(false); flow(nb); return;
    }
    setType(c.bl, r.type); setCaret(c.bl, c.off); changed(false); flow(c.bl);
    if (!t.trim() || ["scene", "character", "transition"].includes(r.type)) suggestFor(c.bl);
  };
  ed.addEventListener("beforeinput", e => {
    const it = e.inputType;
    const c0 = caret(); if (c0 && !c0.collapsed) checkpoint();   // typing or deleting over a selection: undo brings it back
    if (it === "insertParagraph") {
      e.preventDefault();
      if (pop && pop.items.length && accept()) return;    // a heading's prefix or place: the rest of it comes next
      closePop(); hideChip(); enter();
    } else if (it === "insertLineBreak") {
      e.preventDefault();
      const c = clearSelection(); if (!c) return;
      const t = textOf(c.bl); setText(c.bl, t.slice(0, c.off) + "\n" + t.slice(c.off)); setCaret(c.bl, c.off + 1); changed();
    } else if (it === "deleteContentBackward" || it === "deleteContentForward") {
      const c = caret(); if (!c || !c.collapsed) return;
      const t = textOf(c.bl);
      if (it === "deleteContentBackward" && c.off === 0) {
        e.preventDefault(); checkpoint();
        const prev = c.bl.previousElementSibling;
        if (!prev) { if (c.bl.dataset.t !== "action" && !t) setType(c.bl, "action"); return; }
        if (prev.dataset.t === "page_break") { prev.remove(); changed(false); return; }
        const pt = textOf(prev);
        if (!t) { c.bl.remove(); setCaret(prev, pt.length); }
        else if (!pt.trim()) { prev.remove(); setCaret(c.bl, 0); }
        else { setText(prev, pt + t); c.bl.remove(); setCaret(prev, pt.length); }
        numberScenes(); changed(false);
      } else if (it === "deleteContentForward" && c.off >= t.length) {
        e.preventDefault(); checkpoint();
        const next = c.bl.nextElementSibling; if (!next) return;
        if (next.dataset.t !== "page_break") setText(c.bl, t + textOf(next));
        next.remove(); setCaret(c.bl, t.length); numberScenes(); changed(false);
      }
    } else if (it === "insertFromPaste" || it === "insertFromDrop") {
      e.preventDefault();
      const text = (e.dataTransfer && e.dataTransfer.getData("text/plain") || "").replace(/\r\n?/g, "\n");
      if (text) paste(text);
    } else if (it === "historyUndo") { e.preventDefault(); undo(); }
    else if (it === "historyRedo") { e.preventDefault(); redo(); }
  });
  // a piece of screenplay in elements of its own (a paste, the AI's answer): after the caret's element, or over a selection
  const insertFountain = async (text, { replace } = {}) => {
    const p = await post("/studio/api/screenplay/parse", { text });
    if (!p.elements.length) return null;
    let at;
    if (replace) { const [i0, i1] = replace; const bs = blocks().slice(i0, i1 + 1); at = bs[0].previousElementSibling; bs.forEach(x => x.remove()); }
    else at = (caret() || {}).bl || lastBlock || blocks().slice(-1)[0];
    let first = null;
    p.elements.forEach(x => { const nb = block(x); if (at) at.after(nb); else ed.prepend(nb); at = nb; first = first || nb; });
    if (!ed.children.length) ed.append(block({ type: "action", text: "" }));
    setCaret(at, textOf(at).length); numberScenes(); transitions(); changed(false);
    return first;
  };
  const paste = async text => {
    checkpoint();
    const c = clearSelection(); if (!c) return;
    if (!/\n\s*\n/.test(text.trim())) {                 // a line or two: into this element
      const t = textOf(c.bl); setText(c.bl, t.slice(0, c.off) + text + t.slice(c.off)); setCaret(c.bl, c.off + text.length); changed(false); return;
    }
    try {                                             // a piece of screenplay: read as Fountain, in elements of its own
      const p = await post("/studio/api/screenplay/parse", { text });
      const t = textOf(c.bl), type = c.bl.dataset.t;
      setText(c.bl, t.slice(0, c.off));
      let at = c.bl;
      p.elements.forEach(x => { const nb = block(x); at.after(nb); at = nb; });
      const rest = t.slice(c.off); if (rest.trim()) { const nb = block({ type, text: rest }); at.after(nb); at = nb; }
      if (!textOf(c.bl).trim()) c.bl.remove();
      setCaret(at, textOf(at).length); numberScenes(); changed(false);
    } catch (er) { toast("Couldn't paste: " + er.message.slice(0, 120), true); }
  };
  // emphasis as Fountain writes it: **bold**, *italic*, _underline_ around the selection (again: taken off)
  const emphasis = mark => {
    const c = caret(); if (!c) return;
    checkpoint();
    const t = textOf(c.bl), a = c.off, z = c.end == null ? c.off : c.end;
    const L = mark.length, wrapped = t.slice(a - L, a) === mark && t.slice(z, z + L) === mark;
    if (wrapped) { setText(c.bl, t.slice(0, a - L) + t.slice(a, z) + t.slice(z + L)); setCaret(c.bl, a - L, z - L); }
    else { setText(c.bl, t.slice(0, a) + mark + t.slice(a, z) + mark + t.slice(z)); setCaret(c.bl, a + L, z + L); }
    changed(false);
  };
  const note = () => {
    const c = caret() || (lastBlock && { bl: lastBlock, off: textOf(lastBlock).length }); if (!c) return;
    checkpoint(); const t = textOf(c.bl);
    setText(c.bl, t.slice(0, c.off) + "[[ ]]" + t.slice(c.off)); setCaret(c.bl, c.off + 3, c.off + 3); changed(false);
  };
  // dual dialogue: this speech beside the one before (its cue is marked ^ in Fountain)
  const dual = () => {
    const c = caret(); let bl = c ? c.bl : lastBlock;
    while (bl && ["dialogue", "parenthetical"].includes(bl.dataset.t)) bl = bl.previousElementSibling;
    if (!bl || bl.dataset.t !== "character") return toast("Put the cursor in the second of two speeches", true);
    let p = bl.previousElementSibling; while (p && ["dialogue", "parenthetical"].includes(p.dataset.t)) p = p.previousElementSibling;
    if (!bl.dataset.dual && (!p || p.dataset.t !== "character")) return toast("Dual dialogue needs a speech right before this one", true);
    checkpoint();
    if (bl.dataset.dual) delete bl.dataset.dual; else bl.dataset.dual = "1";
    changed(false); toast(bl.dataset.dual ? "Side by side with the speech before" : "Dual dialogue off");
  };
  const setElement = t => {
    const c = caret(); const bl = c ? c.bl : lastBlock; if (!bl) return;
    if (t === "dual") return dual();
    checkpoint(); setType(bl, t); setCaret(bl, c ? c.off : textOf(bl).length); changed(false); flow(bl);
  };

  /* --- SmartType: what to offer as you type (rules.suggest), in a list under the caret */
  let pop = null;
  const vocab = bl => {
    const els = readRaw(), k = blocks().indexOf(bl), v = R.vocabulary(els);
    const places = [...v.places, ...ctx.places().filter(n => !v.places.some(([p]) => p === n)).map(n => [n, 0])];   // the project's locations too
    return { ...v, places, speakers: R.speakersBefore(els, k), names: ctx.names() };
  };
  const closePop = () => { if (pop) { pop.el.remove(); pop = null; } };
  const caretRect = () => { const s = getSelection(); if (!s.rangeCount) return null; const r = s.getRangeAt(0).cloneRange(); let rc = r.getClientRects()[0]; if (!rc) { const bl = (caret() || {}).bl; rc = bl && bl.getBoundingClientRect(); } return rc; };
  const suggestFor = bl => {
    if (!bl || !live()) return closePop();
    const c = caret(); const type = bl.dataset.t, t = textOf(bl);
    if (!c || c.bl !== bl || c.off < t.length || !["scene", "character", "transition"].includes(type)) return closePop();
    const items = R.suggest(type, t, vocab(bl));
    if (!items.length) return closePop();
    const sel = pop && pop.bl === bl ? Math.min(pop.sel, items.length - 1) : 0;
    closePop();
    const el = h(`<div class="sp-pop" role="listbox">${items.map((x, i) => `<div class="sp-pi ${i === sel ? "on" : ""}" data-i="${i}"><b>${esc(x.label)}</b>${x.hint ? `<small>${esc(x.hint)}</small>` : ""}</div>`).join("")}<div class="sp-pk">↑↓ · Tab or Enter · Esc</div></div>`);
    document.body.append(el);
    const rc = caretRect(); if (rc) { el.style.left = Math.min(rc.left, innerWidth - el.offsetWidth - 10) + "px"; el.style.top = (rc.bottom + 6 + el.offsetHeight > innerHeight ? rc.top - el.offsetHeight - 6 : rc.bottom + 6) + "px"; }
    pop = { el, items, sel, bl, type };
    el.onmousedown = ev => { ev.preventDefault(); const it = ev.target.closest(".sp-pi"); if (it) { pop.sel = +it.dataset.i; const keep = accept(); if (!keep && pop) closePop(); } };
  };
  const movePop = k => { if (!pop) return; pop.sel = (pop.sel + k + pop.items.length) % pop.items.length; $$(".sp-pi", pop.el).forEach((x, i) => x.classList.toggle("on", i === pop.sel)); };
  // take the chosen suggestion: -> true when the list stays open (a heading's prefix or place: the rest comes next)
  const accept = () => {
    if (!pop) return false;
    const it = pop.items[pop.sel], bl = pop.bl; if (!it || !bl.isConnected) { closePop(); return false; }
    checkpoint(); setText(bl, it.text); setCaret(bl, it.text.length); changed(false);
    if (it.keep) { suggestFor(bl); return true; }
    closePop(); return false;
  };

  /* --- what the line is turning into (a chip beside it: Enter will make it a cue, a transition…), what Tab will do */
  let chip = null;
  const hideChip = () => { if (chip) { chip.remove(); chip = null; } };
  const hint = bl => {
    hideChip();
    if (!bl || bl.dataset.t !== "action") return;
    const det = R.detect(textOf(bl), "action", { prev: prevType(bl), known: known() });
    if (!det || det.type === "action") return;
    chip = h(`<div class="sp-chip" title="Press Enter at the end of the line">↵ ${esc(R.LABEL[det.type] || det.type.replace("_", " "))}</div>`);
    document.body.append(chip);
    const r = bl.getBoundingClientRect(); chip.style.left = (r.right + 8) + "px"; chip.style.top = r.top + "px";
  };
  const flow = bl => {
    const f = $("#sp-flow", d); if (!bl || !f) return;
    const t = bl.dataset.t, c = caret();
    const tb = R.tab({ type: t, prev: prevType(bl), empty: !textOf(bl).trim(), atEnd: !c || c.off >= textOf(bl).length });
    f.textContent = `${R.LABEL[t] || t} · Enter: ${R.LABEL[R.NEXT[t]] || "Action"} · Tab: ${tb.insert ? "add a " + R.LABEL[tb.insert].toLowerCase() : R.LABEL[tb.type]}`;
  };

  /* --- keys */
  ed.addEventListener("keydown", e => {
    const mod = e.ctrlKey || e.metaKey;
    if (pop && ["ArrowDown", "ArrowUp"].includes(e.key)) { e.preventDefault(); movePop(e.key === "ArrowDown" ? 1 : -1); return; }
    if (e.key === "Escape" && (pop || chip)) { e.preventDefault(); closePop(); hideChip(); return; }
    if (e.key === "Tab" && !mod && !e.altKey) {
      e.preventDefault();
      if (pop && !e.shiftKey) { accept(); return; }
      closePop(); hideChip(); doTab(e.shiftKey);
    } else if (mod && !e.altKey && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
    else if (mod && !e.altKey && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
    else if (mod && !e.altKey && !e.shiftKey && ["b", "i", "u"].includes(e.key.toLowerCase())) { e.preventDefault(); emphasis({ b: "**", i: "*", u: "_" }[e.key.toLowerCase()]); }
    else if (e.altKey && !mod && /^Digit\d$/.test(e.code)) { e.preventDefault(); setElement(R.SHORTCUT[e.code.slice(5)]); }
    else if (typewriter && !mod && e.key.length === 1) { tw("key"); const c = caret(); if (c && c.off === 50) tw("bell"); }
  });
  // the page's own shortcuts, wherever the focus is while the screenplay is open
  onDoc("keydown", e => {
    if (gone()) return;
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    const typing = e.target.closest && e.target.closest("input, textarea, select, [contenteditable=true]") && e.target !== ed;
    if (mod && !e.altKey && (k === "z" || k === "y") && !typing && e.target !== ed && !$("#modal:not(.hidden)")) { e.preventDefault(); (k === "y" || e.shiftKey) ? redo() : undo(); }   // after a drag in the navigator, a rename…
    else if (mod && !e.altKey && k === "s") { e.preventDefault(); save(); }
    else if (mod && !e.altKey && (k === "f" || k === "h") && !e.shiftKey) { e.preventDefault(); openFind(k === "h"); }
    else if (mod && e.shiftKey && k === "f") { e.preventDefault(); focusMode(); }
    else if (mod && e.shiftKey && k === "o") { e.preventDefault(); toggleNav(); }
    else if (mod && (k === "/" || k === "?")) { e.preventDefault(); shortcuts(); }
    else if (e.key === "Escape" && document.body.classList.contains("sp-focus") && !$("#modal:not(.hidden)")) { focusMode(false); }
  });
  // whatever the browser did (a selection typed over several elements, a drag): back to one level of plain blocks
  const normalize = () => {
    const messy = [...ed.childNodes].some(n => n.nodeType !== 1 || !n.classList.contains("sp-b")) || ed.querySelector(".sp-b *:not(br), .sp-b br:not(:last-child)");
    if (!messy) return false;
    const pos = caretPos();
    [...ed.childNodes].forEach(n => {
      if (n.nodeType === 3) { if (n.data.trim()) n.replaceWith(block({ type: "action", text: n.data })); else n.remove(); return; }
      if (n.nodeType !== 1) { n.remove(); return; }
      if (!n.classList.contains("sp-b")) { const t = n.innerText || n.textContent || ""; if (t.trim()) n.replaceWith(block({ type: "action", text: t })); else n.remove(); return; }
      const parts = [""]; n.childNodes.forEach(k => {
        if (k.nodeType === 3) parts[parts.length - 1] += k.data;
        else if (k.nodeName === "BR") { if (k !== n.lastChild) parts[parts.length - 1] += "\n"; }
        else if (/^(DIV|P)$/.test(k.nodeName)) parts.push(k.innerText || k.textContent || "");
        else parts[parts.length - 1] += k.textContent;
      });
      setText(n, parts[0]);
      let at = n; parts.slice(1).forEach(t => { const nb = block({ type: n.dataset.t, text: t }); at.after(nb); at = nb; });
    });
    if (!ed.children.length) ed.append(block({ type: "action", text: "" }));
    restorePos(pos); return true;
  };
  ed.addEventListener("input", () => {
    normalize();
    const c = caret();
    if (c) {
      const t = textOf(c.bl), type = c.bl.dataset.t;
      c.bl.classList.toggle("sp-empty", !t);
      if (type === "action" && R.HEADING.test(t)) { setType(c.bl, "scene"); setCaret(c.bl, c.off); }
      else if (type === "dialogue" && t === "(") { setType(c.bl, "parenthetical"); setText(c.bl, "()"); setCaret(c.bl, 1); }
      if (c.bl.dataset.t === "scene") numberScenes();
      if (c.bl.dataset.t === "transition") c.bl.classList.toggle("long", textOf(c.bl).length > 14);
      suggestFor(c.bl); hint(c.bl); flow(c.bl);
    }
    changed();
  });
  ed.addEventListener("dragover", e => e.preventDefault());
  ed.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== ed) { closePop(); hideChip(); } }, 150));
  onDoc("selectionchange", () => {
    if (gone()) return;
    const c = caret(); if (!c) return;
    if (c.bl !== lastBlock) { closePop(); hideChip(); }
    lastBlock = c.bl; typeSel.value = c.bl.dataset.t; flow(c.bl); soon("where", () => { footer(); navHere(); }, 120);
  });
  typeSel.onchange = () => setElement(typeSel.value);
  $("#sp-jump", d).onchange = e => { const bl = blocks()[+e.target.value]; e.target.value = ""; if (!bl) return; goTo(bl); };
  const goTo = (bl, off = 0) => { if (cardsOn) view(false); bl.scrollIntoView({ block: "center" }); ed.focus(); setCaret(bl, off); };
  $("#sp-undo", d).onclick = undo; $("#sp-redo", d).onclick = redo;
  $$("[data-em]", d).forEach(x => { x.onmousedown = e => e.preventDefault(); x.onclick = () => emphasis(x.dataset.em); });
  $("#sp-dual", d).onmousedown = e => e.preventDefault(); $("#sp-dual", d).onclick = dual;
  $("#sp-note", d).onmousedown = e => e.preventDefault(); $("#sp-note", d).onclick = note;

  /* --- where the caret is: page, scene, words (the foot of the focus view, the navigator's highlight) */
  const pageAt = i => { let p = 1; for (const m of marks) { if (m.el < i || (m.el === i && !m.line)) p = m.page; } return p; };
  const footer = () => {
    const c = caret(), i = c ? blocks().indexOf(c.bl) : 0;
    const w = readEls().reduce((a, e) => a + ((e.text || "").match(/[\p{L}\p{N}'’-]+/gu) || []).length, 0);
    const head = c && headOf(c.bl);
    $("#sp-fw2", d).textContent = `Page ${pageAt(i)} of ${pr.pages || 1}${head ? " · scene " + head.dataset.o : ""} · ${w.toLocaleString()} words`;
  };

  /* --- the scene navigator: the scenes in order (drag one to move it, with everything in it), the one you're in */
  let navOn = pref.get("nav", false);
  const toggleNav = on => { navOn = on == null ? !navOn : on; pref.set("nav", navOn); $(".sp-nav", d).classList.toggle("hidden", !navOn); $("#sp-navb", d).classList.toggle("on", navOn); drawNav(); requestAnimationFrame(() => redrawAll()); };
  $("#sp-navb", d).onclick = () => toggleNav();
  const scenesOf = () => {                               // [{head, blocks, synopsis, number, k}]
    const out = []; let cur = null;
    blocks().forEach(bl => { if (bl.dataset.t === "scene") { cur = { head: bl, blocks: [bl] }; out.push(cur); } else if (cur) cur.blocks.push(bl); });
    const infos = (pr.scenes || []).filter(x => x.heading), used = new Set();
    return out.map((s, k) => {
      const head = textOf(s.head).trim().toUpperCase();
      let info = infos.find(x => !used.has(x) && (x.heading || "").toUpperCase() === head) || (!used.has(infos[k]) ? infos[k] : null);
      if (info) used.add(info);
      return { ...s, k, synopsis: s.blocks.find(x => x.dataset.t === "synopsis"), number: s.head.dataset.o, info };
    });
  };
  const shotsIn = sceneInfo => sceneInfo ? (pr.shots || []).filter(s => s.scene === sceneInfo.id).length : 0;
  const moveScene = (from, to) => {                     // scene `from` before scene `to` (to = count: at the end)
    const sc = scenesOf(); if (from === to || from + 1 === to || !sc[from]) return;
    checkpoint();
    const group = sc[from].blocks, anchor = sc[to] ? sc[to].head : null;
    group.forEach(x => anchor ? ed.insertBefore(x, anchor) : ed.append(x));
    numberScenes(); changed(false); drawCards();
    toast(`Scene moved. The breakdown and the storyboard follow when it's saved.`);
  };
  const drawNav = () => {
    if (!navOn || !live()) return;
    const list = $(".sp-navl", d), sc = scenesOf(); list.innerHTML = "";
    if (!sc.length) { list.append(h(`<p class="hint">No scenes yet: a scene heading (INT. PLACE - DAY) starts one.</p>`)); return; }
    sc.forEach(s => {
      const sec = s.head.previousElementSibling && s.head.previousElementSibling.dataset.t === "section" ? textOf(s.head.previousElementSibling) : "";
      if (sec) list.append(h(`<div class="sp-navsec">${esc(sec)}</div>`));
      const el = h(`<div class="sp-ni" draggable="true" data-k="${s.k}"><b>${esc(s.number || "")}</b><span>${esc(textOf(s.head) || "(new scene)")}<small>${[s.info && s.info.eighths ? R.eighthsText(s.info.eighths) + " pg" : "", s.info ? plural(shotsIn(s.info), "shot") : ""].filter(Boolean).join(" · ")}${s.synopsis ? " · " + esc(textOf(s.synopsis).slice(0, 70)) : ""}</small></span></div>`);
      el.onclick = () => goTo(s.head);
      el.ondragstart = e => { e.dataTransfer.setData("text/x-scene", String(s.k)); e.dataTransfer.effectAllowed = "move"; };
      el.ondragover = e => { if (e.dataTransfer.types.includes("text/x-scene")) { e.preventDefault(); el.classList.add("drop"); } };
      el.ondragleave = () => el.classList.remove("drop");
      el.ondrop = e => { e.preventDefault(); el.classList.remove("drop"); moveScene(+e.dataTransfer.getData("text/x-scene"), s.k); };
      list.append(el);
    });
    const end = h(`<div class="sp-nend">drop here to move a scene to the end</div>`);
    end.ondragover = e => { e.preventDefault(); end.classList.add("drop"); }; end.ondragleave = () => end.classList.remove("drop");
    end.ondrop = e => { e.preventDefault(); end.classList.remove("drop"); moveScene(+e.dataTransfer.getData("text/x-scene"), sc.length); };
    list.append(end); navHere();
  };
  const navHere = () => {
    if (!navOn) return;
    const c = caret(), head = c && headOf(c.bl), k = head ? scenesOf().findIndex(s => s.head === head) : -1;
    $$(".sp-ni", d).forEach(x => x.classList.toggle("on", +x.dataset.k === k));
  };

  /* --- index cards: a card per scene with its synopsis (a Fountain "= synopsis", not printed), drag to reorder */
  let cardsOn = false, mode = "page";
  const view = m => {                                    // "page", "cards" (true) or "treat"; false: the page
    mode = m === true ? "cards" : m || "page"; cardsOn = mode !== "page";
    [["page", "#sp-vpage"], ["cards", "#sp-vcards"], ["treat", "#sp-vtreat"]].forEach(([k, id]) => $(id, d).classList.toggle("on", mode === k));
    $(".sp-desk", d).classList.toggle("hidden", mode !== "page"); $(".sp-cards", d).classList.toggle("hidden", mode !== "cards"); $(".sp-treat", d).classList.toggle("hidden", mode !== "treat");
    if (mode === "cards") drawCards(); else if (mode === "page") requestAnimationFrame(() => redrawAll()); else treatMeta();
  };
  $("#sp-vpage", d).onclick = () => view("page"); $("#sp-vcards", d).onclick = () => view("cards"); $("#sp-vtreat", d).onclick = () => view("treat");
  /* the treatment: the story in prose (Film Engine's), kept on the project; the AI drafts scenes from it */
  const tt = $("#sp-tt", d);
  tt.value = pr.treatment || "";
  const treatMeta = () => { const w = (tt.value.match(/[\p{L}\p{N}'’-]+/gu) || []).length; $("#sp-tm", d).textContent = w ? `${w.toLocaleString()} words` : ""; };
  tt.oninput = () => { pr.treatment = tt.value; treatMeta(); soon("treat", () => ctx.saveProject(pr), 700); };
  $("#sp-tai", d).onclick = async () => {
    const text = tt.value.trim(); if (!text) return toast("Write the treatment first", true);
    const out = await ai("prose", { text: text.slice(0, 12000) }, "Drafting scenes from the treatment");
    if (out) proposal("Scenes from the treatment", out, [{ label: "Put them at the end of the screenplay", go: v => { view("page"); const last = blocks().slice(-1)[0]; setCaret(last, textOf(last).length); return insertFountain(v); } }]);
  };
  const setSynopsis = (s, v) => {
    v = v.replace(/\s+/g, " ").trim();
    if (s.synopsis) { if (v) setText(s.synopsis, v); else s.synopsis.remove(); }
    else if (v) s.head.after(block({ type: "synopsis", text: v }));
    changed(false);
  };
  const drawCards = () => {
    if (!cardsOn || !live()) return;
    const box = $(".sp-cards", d), sc = scenesOf(); box.innerHTML = "";
    sc.forEach(s => {
      const first = s.blocks.find(x => x.dataset.t === "action");
      const info = s.info;
      const el = h(`<div class="sp-card" draggable="true" data-k="${s.k}"><div class="sp-ch"><b>${esc(s.number || "")}</b><span>${esc(textOf(s.head) || "(new scene)")}</span></div>
        <textarea rows="3" placeholder="${esc(first ? textOf(first).split(/(?<=[.!?])\s/)[0].slice(0, 160) : "What happens in this scene…")}">${esc(s.synopsis ? textOf(s.synopsis) : "")}</textarea>
        <div class="sp-cf"><span>${[info && info.eighths ? R.eighthsText(info.eighths) + " pg" : "", info ? plural(shotsIn(info), "shot") : "", info && info.characters && info.characters.length ? info.characters.slice(0, 4).map(x => x[0] + x.slice(1).toLowerCase()).join(", ") : ""].filter(Boolean).join(" · ")}</span>
        <button class="icon" data-ai title="A synopsis from the local AI">✦</button><button class="icon" data-go title="Open the scene on the page">↗</button></div></div>`);
      const ta = $("textarea", el);
      ta.onchange = () => setSynopsis(scenesOf()[s.k], ta.value);
      $("[data-go]", el).onclick = () => goTo(s.head);
      el.ondblclick = e => { if (e.target !== ta) goTo(s.head); };
      $("[data-ai]", el).onclick = async () => {
        const txt = await ai("synopsis", { text: sceneText(s) }, "Writing a synopsis of scene " + s.number); if (txt) { ta.value = txt.replace(/^=\s*/, "").trim(); setSynopsis(scenesOf()[s.k], ta.value); }
      };
      el.ondragstart = e => { if (e.target === ta) return; e.dataTransfer.setData("text/x-scene", String(s.k)); };
      el.ondragover = e => { if (e.dataTransfer.types.includes("text/x-scene")) { e.preventDefault(); el.classList.add("drop"); } };
      el.ondragleave = () => el.classList.remove("drop");
      el.ondrop = e => { e.preventDefault(); el.classList.remove("drop"); moveScene(+e.dataTransfer.getData("text/x-scene"), s.k); };
      box.append(el);
    });
    const add = h(`<div class="sp-card sp-cnew"><b>＋ New scene</b><span class="hint">at the end of the screenplay</span></div>`);
    add.onclick = () => { checkpoint(); const hd = block({ type: "scene", text: "INT. NEW SCENE - DAY" }); ed.append(hd, block({ type: "synopsis", text: "What happens." }), block({ type: "action", text: "" })); numberScenes(); changed(false); drawCards(); const tas = $$(".sp-card textarea", d); const last = tas[tas.length - 1]; if (last) { last.focus(); last.select(); } };
    box.append(add);
  };
  const sceneText = s => s.blocks.map(bl => ({ type: bl.dataset.t, text: textOf(bl) })).filter(e => e.text.trim() && e.type !== "synopsis").map(e => e.type === "character" ? "\n" + e.text.toUpperCase() : e.type === "dialogue" || e.type === "parenthetical" ? e.text : "\n" + e.text).join("\n").trim();

  /* --- find and replace (and rename a character: replaced the way it's written where it stands) */
  let found = [], fi = -1;
  const fopts = () => ({ cs: $("#sp-fcs", d).classList.contains("on"), word: $("#sp-fw", d).classList.contains("on") });
  const openFind = replace => {
    if (cardsOn) view(false);
    $(".sp-findbar", d).classList.remove("hidden");
    const q = $("#sp-fq", d), sel = getSelection().toString();
    if (sel && !sel.includes("\n")) q.value = sel;
    (replace ? $("#sp-fr", d) : q).focus(); q.select(); search();
  };
  const closeFind = () => { $(".sp-findbar", d).classList.add("hidden"); found = []; if (window.CSS && CSS.highlights) { CSS.highlights.delete("sp-find"); CSS.highlights.delete("sp-find-cur"); } ed.focus(); };
  const search = () => {
    const q = $("#sp-fq", d).value; found = [];
    blocks().forEach(bl => { if (bl.dataset.t !== "page_break") R.findIn(textOf(bl), q, fopts()).forEach(([a, z]) => found.push({ bl, a, z })); });
    fi = found.length ? Math.max(0, Math.min(fi, found.length - 1)) : -1;
    paintFind();
  };
  const rangeOf = m => { const r = document.createRange(), a = pointAt(m.bl, m.a), z = pointAt(m.bl, m.z); if (!a || !z) return null; r.setStart(a[0], a[1]); r.setEnd(z[0], z[1]); return r; };
  const paintFind = () => {
    $("#sp-fn", d).textContent = $("#sp-fq", d).value ? (found.length ? `${fi + 1} of ${found.length}` : "not found") : "";
    if (!(window.CSS && CSS.highlights && window.Highlight)) return;
    CSS.highlights.set("sp-find", new Highlight(...found.map(rangeOf).filter(Boolean)));
    const cur = found[fi] && rangeOf(found[fi]); if (cur) CSS.highlights.set("sp-find-cur", new Highlight(cur)); else CSS.highlights.delete("sp-find-cur");
  };
  const showMatch = k => {
    if (!found.length) return;
    fi = (k + found.length) % found.length; const m = found[fi];
    m.bl.scrollIntoView({ block: "center" }); paintFind();
  };
  $("#sp-find", d).onclick = () => openFind(false);
  $("#sp-fx", d).onclick = closeFind;
  $("#sp-fq", d).oninput = () => { fi = 0; search(); if (found.length) showMatch(0); };
  $("#sp-fq", d).onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); showMatch(fi + (e.shiftKey ? -1 : 1)); } else if (e.key === "Escape") closeFind(); };
  $("#sp-fr", d).onkeydown = e => { if (e.key === "Escape") closeFind(); else if (e.key === "Enter") { e.preventDefault(); replaceOne(); } };
  $("#sp-fnext", d).onclick = () => showMatch(fi + 1); $("#sp-fprev", d).onclick = () => showMatch(fi - 1);
  $$(".sp-tg", d).forEach(x => x.onclick = () => { x.classList.toggle("on"); search(); });
  const replaceOne = () => {
    const m = found[fi]; if (!m) return;
    checkpoint(); const t = textOf(m.bl), rep = $("#sp-fr", d).value;
    setText(m.bl, t.slice(0, m.a) + rep + t.slice(m.z)); changed(false); search(); if (found.length) showMatch(fi);
  };
  const replaceAll = (q, rep, opts, how) => {
    const re = R.finder(q, opts); if (!re) return 0;
    checkpoint(); let n = 0;
    blocks().forEach(bl => {
      if (bl.dataset.t === "page_break") return;
      const t = textOf(bl), hits = (t.match(re) || []).length; if (!hits) return;
      n += hits; setText(bl, how ? how(t) : t.replace(re, rep));
    });
    if (n) { numberScenes(); changed(false); }
    return n;
  };
  $("#sp-fr1", d).onclick = replaceOne;
  $("#sp-fra", d).onclick = () => { const n = replaceAll($("#sp-fq", d).value, $("#sp-fr", d).value, fopts()); toast(n ? `Replaced ${plural(n, "time")}` : "Nothing to replace"); search(); };
  const renameCharacter = (from, to) => {
    to = (to || "").trim(); if (!to || to.toUpperCase() === from.toUpperCase()) return;
    const n = replaceAll(from, "", { word: true }, t => R.renameIn(t, from, to));
    toast(`${from} is now ${to.toUpperCase()}: ${plural(n, "place")} in the screenplay.` + (pr.castMap && pr.castMap[from] ? " The cast keeps their character." : ""));
    if (pr.castMap && pr.castMap[from]) { pr.castMap[to.toUpperCase()] = pr.castMap[from]; delete pr.castMap[from]; }
  };

  /* --- reports: the script's statistics */
  const reports = () => {
    const els = readEls(), r = R.report(els, (pr.scenes || []).filter(s => s.heading)), pages = pr.pages || 1;
    const pct = (a, z) => z ? Math.round(a / z * 100) + "%" : "–";
    const talk = r.dialogueWords + r.actionWords;
    const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
    const dlg = h(`<div class="dialog wide sp-rep"><div class="row" style="justify-content:space-between"><h2>Reports</h2><button class="icon" data-x>✕</button></div>
      <div class="gv-seg sp-tabs"><button class="on" data-t="o">Overview</button><button data-t="c">Characters</button><button data-t="s">Scenes</button><button data-t="l">Places</button></div>
      <div data-p="o"><div class="sp-kpis">
        <div><b>${pages}</b><span>pages</span></div><div><b>~${pages} min</b><span>runtime (a page a minute)</span></div><div><b>${r.scenes.length}</b><span>scenes</span></div>
        <div><b>${r.words.toLocaleString()}</b><span>words</span></div><div><b>${r.speeches}</b><span>speeches</span></div><div><b>${pct(r.dialogueWords, talk)}</b><span>dialogue (${pct(r.actionWords, talk)} action)</span></div></div>
        <div class="sp-bars"><div><span>INT ${r.int} · EXT ${r.ext}</span><i style="--a:${pct(r.int, r.int + r.ext || 1)}"></i></div><div><span>Day ${r.day} · Night ${r.night}</span><i style="--a:${pct(r.day, r.day + r.night || 1)}"></i></div></div>
        <p class="hint">${r.longest ? `Longest scene: ${esc(r.longest.number)}. ${esc(r.longest.heading)} (${R.eighthsText(r.longest.eighths)} pages). Shortest: ${esc(r.shortest.number)}. ${esc(r.shortest.heading)} (${R.eighthsText(r.shortest.eighths)}).` : "Scene lengths come with the breakdown (save the screenplay)."}</p></div>
      <div data-p="c" class="hidden"><table class="sp-tbl"><tr><th>Character</th><th>Speeches</th><th>Words</th><th>Scenes</th><th>First in</th><th></th></tr>
        ${r.characters.map(c => `<tr><td><b>${esc(c.name)}</b></td><td>${c.speeches}</td><td>${c.words}</td><td>${c.scenes}</td><td>${esc(c.first)}</td><td><button class="ghost sm" data-find="${esc(c.name)}">Find</button> <button class="ghost sm" data-ren="${esc(c.name)}">Rename…</button></td></tr>`).join("") || `<tr><td colspan="6" class="hint">No speaking characters yet.</td></tr>`}</table></div>
      <div data-p="s" class="hidden"><table class="sp-tbl"><tr><th>#</th><th>Scene</th><th>Length</th><th>Speaking</th><th>Words</th></tr>
        ${r.scenes.map((s, k) => `<tr class="sp-go" data-k="${k}"><td>${esc(s.number)}</td><td>${esc(s.heading)}</td><td>${s.eighths ? R.eighthsText(s.eighths) : "–"}</td><td>${esc(s.speakers.map(x => x[0] + x.slice(1).toLowerCase()).join(", "))}</td><td>${s.words}</td></tr>`).join("")}</table></div>
      <div data-p="l" class="hidden"><table class="sp-tbl"><tr><th>Place</th><th>Scenes</th><th>INT/EXT</th><th>Times</th></tr>
        ${r.locations.map(l => `<tr><td><b>${esc(l.place)}</b></td><td>${l.scenes}</td><td>${esc(l.intext)}</td><td>${esc(l.times.join(", "))}</td></tr>`).join("")}</table></div></div>`);
    m.append(dlg);
    const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
    $$("[data-x]", dlg).forEach(x => x.onclick = close);
    $$(".sp-tabs button", dlg).forEach(bt => bt.onclick = () => { $$(".sp-tabs button", dlg).forEach(x => x.classList.toggle("on", x === bt)); $$("[data-p]", dlg).forEach(p => p.classList.toggle("hidden", p.dataset.p !== bt.dataset.t)); });
    $$("[data-find]", dlg).forEach(x => x.onclick = () => { close(); openFind(false); $("#sp-fq", d).value = x.dataset.find; $("#sp-fw", d).classList.add("on"); search(); showMatch(0); });
    $$("[data-ren]", dlg).forEach(x => x.onclick = () => { const to = prompt(`Rename ${x.dataset.ren} to:`, x.dataset.ren[0] + x.dataset.ren.slice(1).toLowerCase()); if (to) { close(); renameCharacter(x.dataset.ren, to); } });
    $$(".sp-go", dlg).forEach(x => x.onclick = () => { const s = scenesOf()[+x.dataset.k]; if (s) { close(); goTo(s.head); } });
  };
  $("#sp-stat", d).onclick = reports;

  /* --- drafts: this version saved by name (a revision color), compared, restored; the revision's changes marked */
  pr.drafts = pr.drafts || [];
  const draftEls = {};                                   // draft id -> its elements (read once)
  const elsOfDraft = async dr => draftEls[dr.id] || (draftEls[dr.id] = (await post("/studio/api/screenplay/parse", { text: dr.text || "" })).elements);
  let revSet = new Set();
  const drawRevs = async () => {
    revSet = new Set();
    const dr = pr.drafts.find(x => x.id === pr.revSince);
    if (dr) {
      const then = await elsOfDraft(dr), now = readEls();
      R.revised(then, now).forEach(j => { if (now[j]) revSet.add(now[j].i); });
    }
    drawRevMarks();
  };
  const drawRevMarks = () => {
    const box = $(".sp-revs", d); if (!box) return; box.innerHTML = "";
    const bs = blocks();
    revSet.forEach(i => { const bl = bs[i]; if (bl) box.append(h(`<div class="sp-rv" style="top:${bl.offsetTop}px;height:${bl.offsetHeight}px" title="Changed since ${esc((pr.drafts.find(x => x.id === pr.revSince) || {}).name || "the draft")}">*</div>`)); });
  };
  const saveDraft = async (name, color) => {
    if (ctx.dirty()) await save();
    const dr = { id: Math.random().toString(36).slice(2, 10), name: name || `Draft ${pr.drafts.length + 1}`, color: color || "", at: Date.now() / 1000, text: sp.text || "", pages: pr.pages || 0 };
    pr.drafts.push(dr); if (pr.drafts.length > 40) pr.drafts.shift();
    await ctx.saveProject(pr, true); return dr;
  };
  const drafts = () => {
    const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
    const nextColor = REV_COLORS[Math.min(REV_COLORS.length - 1, pr.drafts.filter(x => x.color).length)];
    const dlg = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>Drafts</h2><button class="icon" data-x>✕</button></div>
      <p class="hint" style="margin:0">A draft keeps this version of the screenplay by name. Compare it with today's, go back to it, or mark what changed since it: an asterisk beside each changed line, on the page and in the PDF, as revised pages are marked.</p>
      <div class="row wrap"><input type="text" id="dn" placeholder="Draft ${pr.drafts.length + 1}" style="flex:1;min-width:180px"><select id="dc" title="A revision color (the pages of a revision are printed on colored paper)" style="width:auto"><option value="">No color</option>${REV_COLORS.map(c => `<option ${c === nextColor ? "selected" : ""}>${c}</option>`).join("")}</select><button class="btn primary" id="ds">Save this version</button></div>
      <div class="sp-drs">${pr.drafts.slice().reverse().map(x => `<div class="sp-dr" data-id="${x.id}"><div><b>${esc(x.name)}</b>${x.color ? ` <span class="sp-rc" style="--c:${x.color.toLowerCase()}">${esc(x.color)}</span>` : ""}<small>${new Date(x.at * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}${x.pages ? " · " + plural(x.pages, "page") : ""}</small></div>
        <span class="row"><button class="ghost sm" data-cmp>Compare</button><button class="ghost sm ${pr.revSince === x.id ? "on" : ""}" data-rev>${pr.revSince === x.id ? "✓ Marking changes since" : "Mark changes since"}</button><button class="ghost sm" data-back>Restore</button><button class="icon" data-del title="Delete this draft">✕</button></span></div>`).join("") || `<p class="hint">No drafts yet.</p>`}</div></div>`);
    m.append(dlg);
    const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
    $$("[data-x]", dlg).forEach(x => x.onclick = close);
    $("#ds", dlg).onclick = async () => { const dr = await saveDraft($("#dn", dlg).value.trim(), $("#dc", dlg).value); toast(`Saved as “${dr.name}”`); drafts(); };
    $$(".sp-dr", dlg).forEach(row => {
      const dr = pr.drafts.find(x => x.id === row.dataset.id);
      $("[data-cmp]", row).onclick = () => compare(dr);
      $("[data-rev]", row).onclick = async () => { pr.revSince = pr.revSince === dr.id ? "" : dr.id; await ctx.saveProject(pr, true); await drawRevs(); drafts(); toast(pr.revSince ? `Marking the changes since “${dr.name}”` : "Revision marks off"); };
      $("[data-back]", row).onclick = async () => {
        if (!confirm(`Go back to “${dr.name}”? Today's version is kept as a draft first.`)) return;
        await saveDraft(`Before going back to ${dr.name}`, ""); close(); await replaceWith(dr.text, `Back to “${dr.name}”`);
      };
      $("[data-del]", row).onclick = async () => { if (!confirm(`Delete the draft “${dr.name}”?`)) return; pr.drafts = pr.drafts.filter(x => x !== dr); if (pr.revSince === dr.id) pr.revSince = ""; await ctx.saveProject(pr, true); drawRevs(); drafts(); };
    });
  };
  const compare = async dr => {
    if (ctx.dirty()) await save();
    const a = (dr.text || "").split("\n"), z = (sp.text || "").split("\n");
    const ops = R.diff(a, z);
    const add = ops.filter(o => o[0] === "+" && o[1].trim()).length, del = ops.filter(o => o[0] === "-" && o[1].trim()).length;
    const rows = []; let run = [];
    const flush = () => { if (run.length > 6) rows.push(...run.slice(0, 2), { gap: run.length - 4 }, ...run.slice(-2)); else rows.push(...run); run = []; };
    ops.forEach(o => { if (o[0] === " ") run.push(o); else { flush(); rows.push(o); } }); flush();
    const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
    const dlg = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>“${esc(dr.name)}” → today</h2><button class="icon" data-x>✕</button></div>
      <p class="hint" style="margin:0">${add || del ? `${plural(add, "line")} added or changed, ${plural(del, "line")} removed or changed (the Fountain text, line by line).` : "The same screenplay."}</p>
      <div class="sp-diff">${rows.map(o => o.gap ? `<div class="g">⋯ ${o.gap} unchanged lines</div>` : `<div class="${o[0] === "+" ? "a" : o[0] === "-" ? "r" : ""}"><i>${o[0]}</i>${esc(o[1]) || " "}</div>`).join("")}</div>
      <div class="foot"><button class="ghost" data-back>Restore “${esc(dr.name)}”</button><button class="btn primary" data-x>Close</button></div></div>`);
    m.append(dlg);
    const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
    $$("[data-x]", dlg).forEach(x => x.onclick = close);
    $("[data-back]", dlg).onclick = async () => { if (!confirm(`Go back to “${dr.name}”? Today's version is kept as a draft first.`)) return; await saveDraft(`Before going back to ${dr.name}`, ""); close(); await replaceWith(dr.text, `Back to “${dr.name}”`); };
  };
  $("#sp-drafts", d).onclick = drafts;
  // the screenplay replaced by another text (a file, a draft): the breakdown made again from it, keeping what it can
  const replaceWith = async (text, message, extra = {}) => {
    clearTimeout(tSave); clearTimeout(tCheck); seq++;     // a save still waiting would write the old text back
    const r = await post("/studio/api/screenplay/breakdown", { text, scenes: pr.scenes || [], shots: pr.shots || [] });
    pr.screenplay = { ...sp, text, ...extra };
    Object.assign(pr, { scenes: r.scenes, shots: r.shots, roles: r.roles, pages: r.pages });
    ctx.setDirty(false); await ctx.saveProject(pr, true); await ctx.sync(); toast(message); ctx.route();
  };

  /* --- exports, and replacing the screenplay with a file */
  const download = async (href, ext) => {
    if (ctx.dirty()) await save();
    const a = document.createElement("a");
    a.href = href; a.download = (pr.title || "screenplay").replace(/[\\/:*?"<>|]+/g, " ").trim() + "." + ext;
    document.body.append(a); a.click(); a.remove();
  };
  const q = extra => { const p = new URLSearchParams(extra || {}); if (pr.private) p.set("private", "1"); const s = p.toString(); return s ? "?" + s : ""; };
  const menu = (btn, items) => {                         // a small menu under a toolbar button
    closeMenu();
    const el = h(`<div class="sp-menu">${items.map((it, i) => it === "-" ? `<hr>` : `<button data-i="${i}" ${it.off ? "disabled" : ""}><b>${esc(it.label)}</b>${it.hint ? `<small>${esc(it.hint)}</small>` : ""}</button>`).join("")}</div>`);
    document.body.append(el);
    const r = btn.getBoundingClientRect(); el.style.top = (r.bottom + 6) + "px"; el.style.left = Math.min(r.left, innerWidth - el.offsetWidth - 10) + "px";
    el.onclick = e => { const x = e.target.closest("button[data-i]"); if (x) { closeMenu(); items[+x.dataset.i].go(); } };
    openMenuEl = el;
  };
  let openMenuEl = null;
  const closeMenu = () => { if (openMenuEl) { openMenuEl.remove(); openMenuEl = null; } };
  onDoc("mousedown", e => { if (gone()) return; if (openMenuEl && !e.target.closest(".sp-menu, #sp-exp, #sp-ai, #sp-more")) closeMenu(); });
  $("#sp-exp", d).onclick = e => {
    const dr = pr.drafts.find(x => x.id === pr.revSince);
    const label = dr ? `${dr.color || "Revised"} Rev. ${new Date().toLocaleDateString([], { month: "2-digit", day: "2-digit", year: "2-digit" })}` : "";
    menu(e.currentTarget, [
      { label: "PDF", hint: "The Hollywood standard page", go: () => download(`/studio/api/projects/${pr.id}/screenplay.pdf${q()}`, "pdf") },
      { label: "PDF with revision marks", hint: dr ? `Asterisks on what changed since “${dr.name}”, “${label}” on those pages` : "Mark the changes since a draft first (Drafts)", off: !dr,
        go: () => download(`/studio/api/projects/${pr.id}/screenplay.pdf${q({ since: dr.id, label })}`, "pdf") },
      { label: "Final Draft (.fdx)", hint: "Opens in Final Draft, WriterDuet, Fade In", go: () => download(`/studio/api/projects/${pr.id}/screenplay.fdx${q()}`, "fdx") },
      { label: "Fountain (.fountain)", hint: "Plain text any screenwriting app reads", go: async () => { if (ctx.dirty()) await save(); const u = URL.createObjectURL(new Blob([sp.text || ""], { type: "text/plain;charset=utf-8" })); await download(u, "fountain"); setTimeout(() => URL.revokeObjectURL(u), 2000); } },
      { label: "Plain text (.txt)", hint: "Laid out as the page, for any reader", go: () => download(`/studio/api/projects/${pr.id}/screenplay.txt${q()}`, "txt") },
      "-",
      { label: "Replace with a file…", hint: "PDF, Final Draft or Fountain: scenes that stay keep their shots", go: () => $("#sp-ref", d).click() },
    ]);
  };
  const ref = $("#sp-ref", d);
  ref.onchange = async () => {
    const f = ref.files[0]; ref.value = "";
    if (!f) return;
    if (!ctx.fileRe.test(f.name)) return toast("Pick a .pdf, .fdx or .fountain file", true);
    try {
      const j = await ctx.sendScreenplay("/studio/api/screenplay/convert", f);
      if (!confirm(`Replace this screenplay with ${f.name} (${ctx.spCounts(j.summary)})? Scenes that stay the same keep their shots.`)) return;
      if (j.summary.title) pr.title = pr.name = j.summary.title;
      (j.warnings || []).forEach(w => toast(w, true));
      await replaceWith(j.text, "Screenplay replaced", { source: j.source, filename: j.filename, imported: Date.now() / 1000 });
    } catch (e) { toast(`Couldn't read ${f.name}: ${e.message.slice(0, 300)}`, true); }
  };

  /* --- the local AI's writing help: rewrite, continue, polish the dialogue, prose into screenplay, a scene's synopsis */
  const selectedRange = () => {                          // the blocks the selection covers: [first, last] indexes
    const s = getSelection(); if (!s.rangeCount || s.isCollapsed) return null;
    const r = s.getRangeAt(0), bs = blocks();
    const blk = n => { const e = n.nodeType === 1 ? n : n.parentElement; const bl = e && e.closest(".sp-b"); return bl && bl.parentElement === ed ? bs.indexOf(bl) : -1; };
    const a = blk(r.startContainer), z = blk(r.endContainer);
    return a < 0 || z < 0 ? null : [Math.min(a, z), Math.max(a, z)];
  };
  const elsText = els => els.filter(e => e.text.trim()).map((e, k, all) => {
    const tight = ["dialogue", "parenthetical"].includes(e.type) && k && ["character", "dialogue", "parenthetical"].includes(all[k - 1].type);
    const t = R.UPPER.has(e.type) ? e.text.toUpperCase() : e.type === "synopsis" ? "= " + e.text : e.text;
    return (tight ? "" : "\n") + t;
  }).join("\n").trim();
  const rawEls = (i0, i1) => blocks().slice(i0, i1 + 1).map(bl => ({ type: bl.dataset.t, text: textOf(bl) }));
  const sceneAround = bl => { const s = scenesOf().find(x => x.blocks.includes(bl)); return s ? elsText(s.blocks.map(x => ({ type: x.dataset.t, text: textOf(x) }))) : elsText(rawEls(0, blocks().length - 1)).slice(-4000); };
  const ai = async (mode, body, what) => {
    const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
    const dlg = h(`<div class="dialog"><h2>✦ ${esc(what)}</h2><p class="hint" id="aist">The local AI is writing…</p><div class="bar"><div class="ind"></div></div></div>`);
    m.append(dlg);
    try {
      const j = await post("/studio/api/ai/write", { mode, private: !!pr.private, ...body });
      const res = await ctx.pollJob(j.id, st => { const e = $("#aist", dlg); if (e) e.textContent = st + "…"; });
      m.classList.add("hidden"); m.innerHTML = "";
      return (res && res.text) || "";
    } catch (e) { m.classList.add("hidden"); m.innerHTML = ""; toast("The AI couldn't write: " + e.message.slice(0, 200), true); return null; }
  };
  const proposal = (title, text, actions) => {           // the AI's answer, to take or leave
    const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
    const dlg = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>✦ ${esc(title)}</h2><button class="icon" data-x>✕</button></div>
      <p class="hint" style="margin:0">Edit it here if you like, then put it in the screenplay. Nothing changes until you do.</p>
      <textarea class="sp-prop" spellcheck="true">${esc(text)}</textarea>
      <div class="foot"><button class="ghost" data-x>Discard</button>${actions.map((a, i) => `<button class="${i === actions.length - 1 ? "btn primary" : "ghost"}" data-a="${i}">${esc(a.label)}</button>`).join("")}</div></div>`);
    m.append(dlg);
    const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
    $$("[data-x]", dlg).forEach(x => x.onclick = close);
    $$("[data-a]", dlg).forEach(x => x.onclick = async () => { const v = $(".sp-prop", dlg).value; close(); checkpoint(); await actions[+x.dataset.a].go(v); });
  };
  const aiMenu = btn => {
    const range = selectedRange(), c = caret(), here = (c && c.bl) || lastBlock;
    menu(btn, [
      { label: "Rewrite the selection…", hint: range ? "With what to change: tighter, funnier, more tension…" : "Select a passage first", off: !range, go: async () => {
        const instruction = prompt("What should change? (tighter, more subtext, funnier…)", "Tighter, more vivid"); if (instruction == null) return;
        const text = elsText(rawEls(...range)); const out = await ai("rewrite", { text, context: sceneAround(blocks()[range[0]]), instruction }, "Rewriting");
        if (out) proposal("The rewrite", out, [{ label: "Insert after", go: v => { setCaret(blocks()[range[1]], textOf(blocks()[range[1]]).length); return insertFountain(v); } }, { label: "Replace the selection", go: v => insertFountain(v, { replace: range }) }]);
      } },
      { label: "Polish the dialogue", hint: range ? "Sharper, more natural, same beats" : "Select the speeches first", off: !range, go: async () => {
        const out = await ai("dialogue", { text: elsText(rawEls(...range)), context: sceneAround(blocks()[range[0]]) }, "Polishing the dialogue");
        if (out) proposal("The dialogue, polished", out, [{ label: "Replace the selection", go: v => insertFountain(v, { replace: range }) }]);
      } },
      { label: "Continue the scene", hint: here ? "The next few beats, after the cursor" : "Put the cursor in a scene", off: !here, go: async () => {
        const upto = blocks().slice(0, blocks().indexOf(here) + 1), s = scenesOf().find(x => x.blocks.includes(here));
        const ctxEls = (s ? s.blocks.filter(x => upto.includes(x)) : upto.slice(-40)).map(x => ({ type: x.dataset.t, text: textOf(x) }));
        const out = await ai("continue", { context: elsText(ctxEls) }, "Continuing the scene");
        if (out) proposal("What comes next", out, [{ label: "Insert after the cursor", go: v => { setCaret(here, textOf(here).length); return insertFountain(v); } }]);
      } },
      { label: "Prose into screenplay…", hint: "Paste a story or a treatment: scenes, action, dialogue", go: () => {
        const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
        const dlg = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>✦ Prose into screenplay</h2><button class="icon" data-x>✕</button></div><textarea class="sp-prop" placeholder="Paste prose: a chapter, a treatment, an outline…"></textarea><div class="foot"><button class="ghost" data-x>Cancel</button><button class="btn primary" data-go>Write it as a screenplay</button></div></div>`);
        m.append(dlg); const close = () => { m.classList.add("hidden"); m.innerHTML = ""; };
        $$("[data-x]", dlg).forEach(x => x.onclick = close); $(".sp-prop", dlg).focus();
        $("[data-go]", dlg).onclick = async () => {
          const text = $(".sp-prop", dlg).value.trim(); if (!text) return;
          const out = await ai("prose", { text }, "Writing it as a screenplay");
          if (out) proposal("The screenplay", out, [{ label: "Insert after the cursor", go: v => { if (here) setCaret(here, textOf(here).length); return insertFountain(v); } }]);
        };
      } },
      { label: "A synopsis of this scene", hint: here ? "One sentence, kept on its index card" : "Put the cursor in a scene", off: !here || !headOf(here), go: async () => {
        const s = scenesOf().find(x => x.blocks.includes(here)); if (!s) return;
        const out = await ai("synopsis", { text: sceneText(s) }, "Writing the synopsis"); if (out) { checkpoint(); setSynopsis(s, out.replace(/^=\s*/, "")); toast("The synopsis is on the scene's card"); }
      } },
    ]);
  };
  $("#sp-ai", d).onclick = e => aiMenu(e.currentTarget);

  /* --- focus mode, zoom, the typewriter, locked scene numbers, the shortcuts */
  const focusMode = on => {
    on = on == null ? !document.body.classList.contains("sp-focus") : on;
    document.body.classList.toggle("sp-focus", on);
    if (on) { footer(); ed.focus(); } else if (document.fullscreenElement) document.exitFullscreen().catch(() => { });
    requestAnimationFrame(() => redrawAll());
  };
  $("#sp-fexit", d).onclick = () => focusMode(false);
  let zoom = pref.get("zoom", 100), typewriter = pref.get("tw", false);
  const setZoom = z => { zoom = Math.max(70, Math.min(160, z)); pref.set("zoom", zoom); sheet.style.setProperty("--zoom", zoom / 100); requestAnimationFrame(() => redrawAll()); };
  setZoom(zoom);
  const lock = on => {
    checkpoint();
    const heads = blocks().filter(bl => bl.dataset.t === "scene");
    heads.forEach(bl => { if (on) bl.dataset.n = bl.dataset.o; else delete bl.dataset.n; });
    numberScenes(); changed(false);
    toast(on ? "Scene numbers locked: a scene added now is numbered after the one before it (12A)." : "Scene numbers unlocked: counted again.");
  };
  $("#sp-more", d).onclick = e => {
    const locked = blocks().some(bl => bl.dataset.t === "scene" && bl.dataset.n);
    menu(e.currentTarget, [
      { label: "Focus mode", hint: "The page alone, full screen (Ctrl+Shift+F, Esc leaves)", go: () => { focusMode(true); document.documentElement.requestFullscreen?.().catch(() => { }); } },
      { label: `Zoom in (${zoom}%)`, go: () => setZoom(zoom + 10) }, { label: "Zoom out", go: () => setZoom(zoom - 10) }, { label: "Actual size", go: () => setZoom(100) },
      "-",
      { label: locked ? "Unlock scene numbers" : "Lock scene numbers", hint: locked ? "Count the scenes again" : "For a shooting script: added scenes become 12A, 12B", go: () => lock(!locked) },
      { label: typewriter ? "Typewriter sound: on" : "Typewriter sound: off", hint: "The keys, the carriage, the bell", go: () => { typewriter = !typewriter; pref.set("tw", typewriter); if (typewriter) tw("bell"); } },
      { label: "Keyboard shortcuts", hint: "Ctrl+/", go: shortcuts },
    ]);
  };
  const shortcuts = () => {
    const m = $("#modal"); m.innerHTML = ""; m.classList.remove("hidden");
    const rows = [["Enter", "The next element: a cue's speech, a scene after a transition, action after dialogue (a known name in capitals becomes a cue, “CUT TO:” a transition)"],
      ["Tab", "The element this place calls for: after a speech the next speaker, after a cue a parenthetical, after action a character; again for the next choice"],
      ["Tab at the end of a cue or a speech", "Adds a parenthetical; at the end of a parenthetical, the speech goes on"], ["Shift+Tab", "Back through the choices"],
      ["Shift+Enter", "A new line in the same element"], ["↑ ↓ then Tab or Enter", "Take a suggestion (characters, the next speaker, INT./EXT., places, times, transitions)"],
      ["Alt+1 … Alt+0", "Scene heading, Action, Character, Dialogue, Parenthetical, Transition, Dual dialogue, Centered, Lyrics, Synopsis"],
      ["Ctrl+B / I / U", "Bold, italic, underline (Fountain's ** * _)"], ["Ctrl+F · Ctrl+H", "Find · find and replace"], ["Ctrl+Z · Ctrl+Shift+Z", "Undo · redo"],
      ["Ctrl+S", "Save now (it saves as you type)"], ["Ctrl+Shift+O", "The scene navigator"], ["Ctrl+Shift+F", "Focus mode (Esc leaves)"],
      ["Typing . @ > ~ = # ! at the start", "Fountain's marks: a scene heading, a character, a transition (> text < centered), lyrics, a synopsis, a section, action"]];
    const tabRows = Object.entries(R.CYCLES).map(([k, v]) => `<tr><td>${k === "start" ? "The first line" : "After " + R.LABEL[k].toLowerCase()}</td><td>${v.map(x => R.LABEL[x]).join(" → ")}</td></tr>`).join("");
    const dlg = h(`<div class="dialog wide"><div class="row" style="justify-content:space-between"><h2>Keyboard</h2><button class="icon" data-x>✕</button></div>
      <table class="sp-tbl">${rows.map(([k, v]) => `<tr><td><b>${esc(k)}</b></td><td>${esc(v)}</td></tr>`).join("")}</table>
      <h3 style="margin-top:6px">What Tab offers on an empty line</h3><table class="sp-tbl">${tabRows}</table></div>`);
    m.append(dlg); $$("[data-x]", dlg).forEach(x => x.onclick = () => { m.classList.add("hidden"); m.innerHTML = ""; });
  };

  /* --- the margins: where the pages break, where each shot of the breakdown starts, the revision's asterisks */
  const drawMarks = () => {
    const box = $(".sp-marks", d); if (!box) return;
    box.innerHTML = "";
    const lh = parseFloat(getComputedStyle(ed).lineHeight) || 16;
    marks.forEach(m => {
      const bl = blocks()[m.el]; if (!bl) return;
      const y = m.line ? bl.offsetTop + m.line * lh : bl.offsetTop - (bl.previousElementSibling ? lh / 2 : 0);
      box.append(h(`<div class="sp-pm" style="top:${Math.round(y)}px"><span>${m.page}.</span></div>`));
    });
  };
  // each shot of the breakdown where its first beat is (its line in the Fountain text, the line each block starts on)
  const shotMarks = () => {
    const codes = ctx.codes(), changes = ctx.changes(), bs = blocks();
    const starts = [...lineOf].filter(([i]) => bs[i]).sort((a, b) => a[1] - b[1]);
    return (pr.shots || []).filter(s => s.beats && s.beats.length).map(s => {
      const at = s.beats[0].at; let hit = null;
      for (const st of starts) { if (st[1] <= at) hit = st; else break; }
      return hit ? { s, code: codes[s.id], bl: bs[hit[0]], k: at - hit[1], ch: changes.get(s.id) } : null;
    }).filter(Boolean);
  };
  const drawShots = () => {
    const box = $(".sp-shots", d); if (!box || !live()) return;
    box.innerHTML = "";
    shotMarks().forEach(m => {
      let y = m.bl.offsetTop;
      if (m.k > 0) {                                     // a paragraph further down the action block
        const t = textOf(m.bl); let off = 0; for (let n = 0; n < m.k; n++) { const j = t.indexOf("\n", off); if (j < 0) break; off = j + 1; }
        const p = pointAt(m.bl, off); if (p) { const r = document.createRange(); r.setStart(p[0], p[1]); const rc = r.getClientRects()[0]; if (rc) y = rc.top - sheet.getBoundingClientRect().top; }
      }
      const el = h(`<button class="sp-shot ${m.ch ? "chg" : ""}" style="top:${Math.round(y)}px" title="Shot ${esc(m.code)}: ${esc(m.s.title || "")} · ${esc(m.s.camera || "")} · ${m.s.seconds || ""} s${m.ch ? " · changed on the storyboard: " + esc(m.ch) : ""}">${esc(m.code)}</button>`);
      el.onclick = () => menu(el, [
        { label: `Shot ${m.code}`, hint: [m.s.title, m.s.camera, m.s.seconds ? m.s.seconds + " s" : ""].filter(Boolean).join(" · "), off: true, go: () => { } },
        ...(m.ch ? [{ label: "What changed on the storyboard", hint: m.ch, go: () => ctx.openChanges() }] : []),
        { label: "Open it in Scenes & shots", go: () => { ctx.S.scnJump = m.s.id; ctx.go("scenes"); } },
        { label: "Open it on the storyboard", go: () => { ctx.S.sbJump = m.s.id; ctx.go("board"); } },
      ]);
      box.append(el);
    });
  };
  const redrawAll = () => { if (!live()) return; drawMarks(); drawShots(); drawRevMarks(); };
  const onResize = () => { if (gone()) { window.removeEventListener("resize", onResize); return; } redrawAll(); };
  window.addEventListener("resize", onResize);

  /* --- open: the saved Fountain text, read by the server */
  setStatus("opening…");
  try {
    const p = await post("/studio/api/screenplay/parse", { text: sp.text || "" });
    if (!live()) return;
    tp = p.title_page.length ? p.title_page.map(([k, v]) => [k, v]) : [["Title", pr.title || "Untitled"]];
    render(p.elements); checkpoint(); setStatus(""); pr.pages = p.pages; stats();
    marks = p.page_starts || []; lineOf = new Map(p.elements.map((e, k) => [k, e.line])); transitions();
    toggleNav(navOn);
    requestAnimationFrame(() => { redrawAll(); footer(); });
    drawRevs(); followBoard();
    const jump = ctx.S.spJump; ctx.S.spJump = null;
    const heads = blocks().filter(bl => bl.dataset.t === "scene");
    if (jump != null && heads[jump]) { heads[jump].scrollIntoView({ block: "center" }); setCaret(heads[jump], 0); }
    else if (!p.elements.length) setCaret(blocks()[0], 0);
  } catch (e) { setStatus(""); ed.innerHTML = ""; d.append(h(`<div class="empty">Couldn't open the screenplay: ${esc(e.message.slice(0, 200))}</div>`)); }
}
