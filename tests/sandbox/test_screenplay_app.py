"""The screenplay editor as a screenwriting app (ComfyUI-Studio/web/screenplay), in a real browser: Enter and Tab choose
the element a place calls for, SmartType offers the next speaker, headings and transitions; the storyboard follows every
change of the screenplay (what changed on each shot, in words; takes made from an earlier text; a shot cut from the
screenplay keeps its takes, and gets them back with an undo); and the tools around the page (the navigator, index cards,
find and replace, renaming a character, drafts and revision marks, the AI's help, exports)."""
import asyncio
import os
import shutil
import urllib.request

import pytest

from test_screenplay_import import FOUNTAIN, REPO, _port, _with_chrome, chrome, delete, upload

pytestmark = pytest.mark.skipif(chrome() is None, reason="no Chrome or Chromium installed")

CARET_END = """(sel => { const b = typeof sel === 'string' ? [...document.querySelectorAll('.sp-ed ' + sel)].pop() : sel; const r = document.createRange();
    r.selectNodeContents(b); r.collapse(false); const s = getSelection(); s.removeAllRanges(); s.addRange(r); b.closest('.sp-ed').focus(); return b.textContent; })"""
LAST = "[...document.querySelectorAll('.sp-ed .sp-b')].slice(-%d).map(b => b.dataset.t + ':' + b.textContent)"
POP = "[...document.querySelectorAll('.sp-pop .sp-pi b')].map(x => x.textContent)"


async def keys(page, *seq):
    """Presses keys by name: Enter, Tab, Shift+Tab, Escape, Alt+1, ArrowDown; any other string is typed."""
    codes = {"Enter": ("Enter", 13, "\r"), "Tab": ("Tab", 9, ""), "Escape": ("Escape", 27, ""), "ArrowDown": ("ArrowDown", 40, "")}
    for k in seq:
        if k == "Shift+Tab":
            await page.key("Tab", "Tab", 9, modifiers=8)
        elif k.startswith("Alt+"):
            d = k[4:]
            await page.key(d, "Digit" + d, 48 + int(d), modifiers=1)
        elif k in codes:
            code, vk, text = codes[k]
            await page.key(k, code, vk, text)
        else:
            await page.type(k)
        await asyncio.sleep(0.12)


async def open_screenplay(page, box, pid):
    await page.call("Page.navigate", {"url": f"{box.url}/studio/#/projects/{pid}/screenplay"})
    return await page.until("document.querySelectorAll('.sp-ed .sp-b').length > 100 && document.querySelector('#sp-save').textContent === ''")


def stored(box, pid):
    return next(x for x in box.get("/studio/api/projects") if x["id"] == pid)


def test_enter_and_tab_choose_the_element_and_smarttype_offers_what_comes_next(scratch, tmp_path):
    pr = upload(scratch, FOUNTAIN)[1]["project"]

    async def run(page):
        out = {}
        await open_screenplay(page, scratch, pr["id"])
        await page.js(f"({CARET_END})('.sp-b')")
        await keys(page, "Enter", "Tab")                   # after the last line: action; Tab: a character
        out["cue"] = await page.js(LAST % 1)
        out["next_speaker"] = await page.js(POP)           # the next speaker of the conversation first
        await keys(page, "Enter")                          # Enter takes it and opens the speech
        out["took"] = await page.js(LAST % 2)
        await keys(page, "Wait.", "Tab")                   # Tab at the end of a speech: a parenthetical
        out["paren"] = await page.js(LAST % 1)
        await keys(page, "softly", "Enter", "Don't go.", "Enter")
        out["speech"] = await page.js(LAST % 4)
        await keys(page, "CUT TO:")
        out["chip"] = await page.js("(document.querySelector('.sp-chip') || {}).textContent")
        await keys(page, "Enter")                          # a transition; then a scene heading, its prefixes offered
        out["transition"] = await page.js(LAST % 2)
        out["prefixes"] = await page.js(POP)
        await keys(page, "e", "Tab")
        out["prefix"] = await page.js(LAST % 1)
        out["places"] = await page.js(POP)
        await keys(page, "Tab")
        out["times"] = await page.js(POP)
        await keys(page, "n", "Enter")
        out["heading"] = await page.js(LAST % 2)
        await keys(page, "Rain.", "Enter", "RAY", "Enter")  # a known name on an action line: a cue
        out["known"] = await page.js(LAST % 2)
        await keys(page, "Hi.", "Enter", "@McCLANE", "Enter", "Yippee.", "Enter", "Alt+6", "fade out.")
        out["alt6"] = await page.js(LAST % 1)
        await keys(page, "Enter", "Escape", "Alt+2", "The end.")
        out["saved"] = await page.until("document.querySelector('#sp-save').textContent === 'saved'")
        await asyncio.sleep(1.2)
        return out

    out, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert out["cue"] == ["character:"]
    assert out["next_speaker"][:2] in (["RAY", "JUNE"], ["JUNE", "RAY"])   # the one who didn't speak last comes first
    assert out["took"] == ["character:" + out["next_speaker"][0], "dialogue:"]
    assert out["paren"] == ["parenthetical:()"]
    assert out["speech"] == ["dialogue:Wait.", "parenthetical:(softly)", "dialogue:Don't go.", "action:"]
    assert out["chip"] == "↵ Transition"
    assert out["transition"] == ["transition:CUT TO:", "scene:"]
    assert out["prefixes"] == ["INT.", "EXT.", "INT./EXT.", "EXT./INT.", "EST.", "I/E"]
    assert out["prefix"] == ["scene:EXT. "] and out["places"] == ["THE GLASS HARBOUR DINER"]
    assert out["times"][0] == "LATE AFTERNOON" and "NIGHT" in out["times"]
    assert out["heading"] == ["scene:EXT. THE GLASS HARBOUR DINER - NIGHT", "action:"]
    assert out["known"] == ["character:RAY", "dialogue:"]
    assert out["alt6"] == ["transition:fade out."]
    text = stored(scratch, pr["id"])["screenplay"]["text"]
    who = out["next_speaker"][0]
    assert text.endswith(f"{who}\nWait.\n(softly)\nDon't go.\n\nCUT TO:\n\nEXT. THE GLASS HARBOUR DINER - NIGHT\n\nRain.\n\nRAY\nHi.\n\n"
                         "@McCLANE\nYippee.\n\n> FADE OUT.\n\nThe end.\n"), text[-300:]
    delete(scratch, pr["id"])


def test_the_storyboard_follows_the_screenplay(scratch, tmp_path):
    """A line rewritten in shot 1B: the storyboard's 1B says so, in words, and that its take was made from the earlier text;
    a setup cut from the screenplay keeps its take in the board's "cut" list, and an undo brings it back."""
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    board_id = scratch.post(f"/studio/api/projects/{pr['id']}/to_board", {"private": False})[1]["board"]

    def board():
        return next(b for b in scratch.get("/studio/api/boards") if b["id"] == board_id)
    b = board()
    out_dir = os.path.join(scratch.run, "output", "Videos")
    os.makedirs(out_dir, exist_ok=True)
    for k in (1, 3):                                       # a take each on 1B and 1D, made from their text as it is
        shutil.copy(os.path.join(REPO, "sandbox", "samples", "diner-ray.mp4"), os.path.join(out_dir, f"take{k}.mp4"))
        b["shots"][k]["takes"] = [{"pid": f"t{k}", "output": {"filename": f"take{k}.mp4", "subfolder": "Videos"}, "status": "done", "prompt": b["shots"][k]["prompt"]}]
        b["shots"][k]["pick"] = 0
    scratch.post("/studio/api/boards", b)
    code_1d = b["shots"][3]["code"]

    async def run(page):
        out = {}
        await open_screenplay(page, scratch, pr["id"])
        out["before"] = await page.until("!document.querySelector('#sp-sb').hidden && document.querySelector('#sp-sb').textContent")
        line = await page.js(f"""({CARET_END})([...document.querySelectorAll('.sp-ed .sp-dialogue')].find(x => x.textContent.includes("still bad here")))""")
        out["line"] = line
        await page.js("(() => { const b = [...document.querySelectorAll('.sp-ed .sp-dialogue')].find(x => x.textContent.includes('still bad here')); const r = document.createRange(); r.selectNodeContents(b); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()")
        await page.type("This coffee gets worse every year.")
        out["after"] = await page.until("document.querySelector('#sp-sb').textContent.includes('changed') && document.querySelector('#sp-sb').textContent")
        out["marks"] = await page.until("[...document.querySelectorAll('.sp-shot.chg')].map(x => x.textContent).join(',')")
        out["dot"] = await page.js("document.querySelector('[data-menu=prod]').classList.contains('alert')")
        await page.js("document.querySelector('#sp-sb').click()")
        out["dialog"] = await page.until("document.querySelector('.dialog.chg .chshot') && [...document.querySelectorAll('.dialog.chg .chshot')].map(x => x.innerText.replace(/\\s+/g, ' '))")
        await page.js("document.querySelector('.dialog.chg [data-x]').click()")
        # the setup that opens on the camera crossing the table (1D): its beats go
        await page.js("""(() => { const b = [...document.querySelectorAll('.sp-ed .sp-action')].find(x => x.textContent.startsWith('The camera crosses the table low'));
            const r = document.createRange(); r.selectNodeContents(b); const s = getSelection(); s.removeAllRanges(); s.addRange(r); b.closest('.sp-ed').focus(); })()""")
        await keys(page, "She looks at him.")
        await page.until("document.querySelector('#sp-save').textContent === 'saved'")
        await asyncio.sleep(1.5)
        out["cut"] = [x["code"] for x in board().get("cut", [])]
        await page.js("document.querySelector('#sp-undo').click()")
        await page.until("document.querySelector('#sp-save').textContent === 'saved'")
        await asyncio.sleep(1.5)
        # the storyboard page: the banner on 1B, then reviewed
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{pr['id']}/takes"})
        out["banner"] = await page.until("document.querySelector('.shchg') && [...document.querySelectorAll('.shot')].filter(s => s.querySelector('.shchg')).map(s => s.querySelector('.shead b').textContent + ' ' + s.querySelector('.shchg').innerText.replace(/\\s+/g, ' '))")
        await page.js("[...document.querySelectorAll('.shot')].find(s => s.querySelector('.shchg') && s.querySelector('.shead b').textContent === 'SHOT 1B').querySelector('[data-seen]').click()")
        await asyncio.sleep(1.2)
        out["left"] = await page.js("[...document.querySelectorAll('.shot')].filter(s => s.querySelector('.shchg')).map(s => s.querySelector('.shead b').textContent)")
        return out

    out, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert out["before"] == "Storyboard & takes ✓ follow the screenplay" and out["line"] == "Coffee's still bad here."
    assert out["after"] == "↻ 1 shot changed" and out["marks"] == "1B" and out["dot"] is True
    assert out["dialog"] == ["Shot 1B The camera eases toward Ray. Reviewed ✓ Ray: “Coffee's still bad here.” → “This coffee gets worse every year.” "
                             "1 take made from the earlier text: draft new takes."]
    assert out["cut"] == [code_1d]                         # cut from the screenplay: kept with its take
    b = board()
    assert b.get("cut") == [] and b["shots"][3]["takes"][0]["output"]["filename"] == "take3.mp4"   # the undo brought it back
    assert out["banner"][0].startswith("SHOT 1B ↻ Changed in the screenplay 1 take made from the earlier text")
    assert "SHOT 1B" not in out["left"]
    shot = next(x for x in board()["shots"] if x["code"] == "1B")
    assert shot["seen"] == shot["src"] and "worse every year" in shot["prompt"]   # reviewed; the prompt is the new text
    delete(scratch, pr["id"])


def test_the_tools_around_the_page(scratch, tmp_path):
    pr = upload(scratch, FOUNTAIN)[1]["project"]

    async def run(page):
        out = {}
        await page.js("window.prompt = () => 'Raymond'; window.confirm = () => true")
        await open_screenplay(page, scratch, pr["id"])
        await page.js("window.prompt = () => 'Raymond'; window.confirm = () => true")
        # the scene navigator
        await page.js("document.querySelector('#sp-navb').click()")
        out["nav"] = await page.until("[...document.querySelectorAll('.sp-ni')].map(x => x.querySelector('b').textContent + ' ' + x.querySelector('small').textContent)")
        # index cards: a synopsis
        await page.js("document.querySelector('#sp-vcards').click()")
        await page.js("(() => { const t = document.querySelector('.sp-card textarea'); t.value = 'Ray and June meet again.'; t.dispatchEvent(new Event('change')); })()")
        # the treatment: the story in prose, kept with the project
        await page.js("document.querySelector('#sp-vtreat').click()")
        await page.js("(() => { const t = document.querySelector('#sp-tt'); t.value = 'Ray waits. June comes.'; t.dispatchEvent(new Event('input')); })()")
        out["treat_words"] = await page.until("document.querySelector('#sp-tm').textContent")
        await asyncio.sleep(1.2)
        await page.js("document.querySelector('#sp-vpage').click()")
        out["synopsis"] = await page.js("[...document.querySelectorAll('.sp-ed .sp-synopsis')].map(b => b.textContent)")
        # find, and rename a character
        await page.js(f"({CARET_END})('.sp-b')")
        await page.key("f", "KeyF", 70, modifiers=2)
        await page.type("coffee")
        out["found"] = await page.until("document.querySelector('#sp-fn').textContent")
        await page.js("document.querySelector('#sp-fx').click(); document.querySelector('#sp-stat').click()")
        out["kpis"] = await page.until("[...document.querySelectorAll('.sp-kpis div span')].map(x => x.textContent)")
        await page.js("document.querySelector('[data-ren=RAY]').click()")
        await page.until("document.querySelector('#sp-save').textContent === 'saved'")
        # a draft, a change, revision marks and the comparison
        await page.js("document.querySelector('#sp-drafts').click()")
        await page.js("document.querySelector('#dn').value = 'First draft'; document.querySelector('#ds').click()")
        await page.until("document.querySelector('.sp-dr')")
        await page.js("document.querySelector('#modal [data-x]').click()")
        await page.js(f"({CARET_END})('.sp-action')")
        await page.type(" The jukebox clicks.")
        await page.until("document.querySelector('#sp-save').textContent === 'saved'")
        await page.js("document.querySelector('#sp-drafts').click()")
        await page.until("document.querySelector('.sp-dr [data-rev]')")
        await page.js("document.querySelector('.sp-dr [data-rev]').click()")
        out["revs"] = await page.until("document.querySelectorAll('.sp-rv').length")
        await page.js("document.querySelector('#modal [data-x]') && document.querySelector('#modal [data-x]').click()")
        await page.js("document.querySelector('#sp-drafts').click()")
        await page.until("document.querySelector('.sp-dr [data-cmp]')")
        await page.js("document.querySelector('.sp-dr [data-cmp]').click()")
        out["diff"] = await page.until("document.querySelector('.sp-diff') && [...document.querySelectorAll('.sp-diff .a, .sp-diff .r')].map(x => x.textContent.slice(-22))")
        await page.js("document.querySelector('#modal [data-x]').click()")
        # the AI continues the scene (the sandbox's stand-in answers with Fountain in a code fence)
        await page.js(f"({CARET_END})('.sp-dialogue')")
        await page.js("document.querySelector('#sp-ai').click()")
        await page.js("[...document.querySelectorAll('.sp-menu button')].find(b => b.textContent.includes('Continue the scene')).click()")
        out["ai"] = await page.until("document.querySelector('.sp-prop') && document.querySelector('.sp-prop').value")
        n = await page.js("document.querySelectorAll('.sp-ed .sp-b').length")
        await page.js("[...document.querySelectorAll('#modal button')].find(b => b.textContent.includes('Insert after the cursor')).click()")
        out["inserted"] = await page.until(f"document.querySelectorAll('.sp-ed .sp-b').length - {n}")
        await page.until("document.querySelector('#sp-save').textContent === 'saved'")
        await asyncio.sleep(1.2)
        return out

    out, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert out["nav"] == ["1 6 5/8 pg · 8 shots"]
    assert out["synopsis"] == ["Ray and June meet again."]
    assert out["found"] == f"1 of {open(FOUNTAIN, encoding='utf-8').read().lower().count('coffee')}"
    assert out["kpis"][:3] == ["pages", "runtime (a page a minute)", "scenes"]
    assert out["revs"] == 1                                 # one element changed since the draft: one asterisk
    assert len(out["diff"]) == 2 and out["diff"][1].endswith("The jukebox clicks.") and not out["diff"][0].endswith("The jukebox clicks.")
    assert out["ai"].startswith("RAY\n(quietly)\nStay for the coffee.") and "```" not in out["ai"] and out["inserted"] == 4
    p = stored(scratch, pr["id"])
    text = p["screenplay"]["text"]
    assert "RAYMOND\n" in text and "Raymond smiles." in text and "\nRAY\nYou look like her." not in text   # renamed in cues and action
    assert "\n= Raymond and June meet again.\n" in text       # the card's synopsis, in the screenplay (renamed too)
    assert out["treat_words"] == "4 words" and p["treatment"] == "Ray waits. June comes."
    assert [d["name"] for d in p["drafts"]] == ["First draft"] and p["revSince"] == p["drafts"][0]["id"]
    assert "RAY\n(quietly)\nStay for the coffee.\n\nJune looks at the door, then sits." in text
    with urllib.request.urlopen(scratch.url + f"/studio/api/projects/{pr['id']}/screenplay.pdf?since={p['drafts'][0]['id']}&label=White%20Rev.", timeout=60) as r:
        assert r.read().startswith(b"%PDF-")
    delete(scratch, pr["id"])
