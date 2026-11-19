"""Pre-production in a real browser: the cast and the locations follow the screenplay as it's written (a new place is a
location, a new speaking part is cast; a typo's character and an untouched place go again; a place renamed in its form
is renamed in the scene headings), and the storyboard, organized as Film Engine's: a frame per shot with its card
(Sc / shot code, camera, facets, description, lines; a click shows what doesn't fit), search and filters, frames made with the Image app (the location's
look in their prompts), versions, frames made from an earlier draft, the lock. A part left uncast stays so, and a character
two projects cast stays when one of them goes."""
import asyncio
import glob
import json
import os
import urllib.request

import pytest

from test_screenplay_app import CARET_END, keys, open_screenplay, stored
from test_screenplay_import import FOUNTAIN, _port, _with_chrome, chrome, delete, upload
from find_chrome import WHY_NOT  # noqa: E402

pytestmark = pytest.mark.skipif(chrome() is None, reason=WHY_NOT)


def chars(box):
    return {c["name"]: c for c in box.get("/studio/api/characters")}


async def saved(page):
    await asyncio.sleep(0.3)
    await page.until("document.querySelector('#sp-save').textContent === 'saved'")
    await asyncio.sleep(1.5)                               # the cast and the locations follow the save


def test_the_cast_and_the_locations_follow_the_screenplay(scratch, tmp_path):
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    assert [(x["name"], x["auto"], x["look"]) for x in pr["locations"]] == [("THE GLASS HARBOUR DINER", True, "")]   # made at the import

    async def run(page):
        out = {}
        await open_screenplay(page, scratch, pr["id"])
        await asyncio.sleep(1.5)
        out["cast_on_open"] = stored(scratch, pr["id"]).get("castMap")
        await page.js(f"({CARET_END})('.sp-b')")
        await keys(page, "Enter", "Enter", "EXT. THE PIER - NIGHT", "Escape", "Enter", "Fog over the black water.", "Enter", "Tab", "Escape", "STRANGR", "Enter", "You're late.")
        await saved(page)
        out["toast"] = await page.js("[...document.querySelectorAll('#toast .t')].map(t => t.textContent).join(' | ')")
        out["typo"] = stored(scratch, pr["id"])
        await page.js("(() => { const b = [...document.querySelectorAll('.sp-ed .sp-character')].pop(); const r = document.createRange(); r.selectNodeContents(b); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()")
        await page.type("STRANGER")
        await saved(page)
        out["fixed"] = stored(scratch, pr["id"])
        # the pier's scene goes: an untouched location made from the screenplay goes with it
        await page.js("(() => { const b = [...document.querySelectorAll('.sp-ed .sp-scene')].pop(); const r = document.createRange(); r.selectNodeContents(b); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()")
        await page.type("EXT. THE OLD PIER - NIGHT")
        await saved(page)
        out["renamed_heading"] = [x["name"] for x in stored(scratch, pr["id"])["locations"]]
        # in its form: a look, and a new name, which the scene headings take
        await page.js("window.confirm = () => true")
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{pr['id']}/locations"})
        await page.until("document.querySelectorAll('.loc-card').length === 2")
        await page.js("[...document.querySelectorAll('.loc-card')].find(c => c.textContent.includes('Old Pier')).querySelector('[data-edit]').click()")
        await page.until("document.querySelector('.locd')")
        await page.js("""(() => { const set = (k, v) => { document.querySelector(`[data-k=${k}]`).value = v; };
            set('name', 'the long pier'); set('look', 'a rotting wooden pier under sodium lamps, fog'); set('lighting', 'sodium lamps');
            document.querySelector('#lsave').click(); })()""")
        await page.until("!document.querySelector('.locd') && [...document.querySelectorAll('.loc-card h3')].some(h => h.textContent === 'The Long Pier')")
        await asyncio.sleep(1.5)
        out["cards"] = await page.js("[...document.querySelectorAll('.loc-card')].map(c => c.querySelector('h3').textContent + ' · ' + c.querySelector('.hint').textContent)")
        # the heading goes from the screenplay: an edited location stays, marked
        await open_screenplay(page, scratch, pr["id"])
        await page.js("(() => { const b = [...document.querySelectorAll('.sp-ed .sp-scene')].pop(); const r = document.createRange(); r.selectNodeContents(b); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()")
        await page.type("INT. THE GLASS HARBOUR DINER - NIGHT")
        await saved(page)
        return out

    out, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    c = chars(scratch)
    assert sorted(out["cast_on_open"]) == ["JUNE", "RAY"]                   # the parts, cast when the screenplay opened
    assert c["Ray Mercer"]["auto"] == pr["id"] and c["Ray Mercer"]["age"] == "fifty-seven" and c["Ray Mercer"]["look"].startswith("canvas work jacket")
    p = out["typo"]
    assert "STRANGR" in p["castMap"] and [x["full"] for x in p["locations"]] == ["The Glass Harbour Diner", "The Pier"]
    assert "a location: The Pier" in out["toast"] and "a character in the cast: Strangr" in out["toast"]
    p = out["fixed"]
    assert "STRANGER" in p["castMap"] and "STRANGR" not in p["castMap"]
    assert out["renamed_heading"] == ["THE GLASS HARBOUR DINER", "THE OLD PIER"]   # untouched: it went, the new one came
    assert out["cards"] == ["The Glass Harbour Diner · 1 scene · 1 · INT", "The Long Pier · 1 scene · 2 · EXT"]
    p = stored(scratch, pr["id"])
    locs = {x["name"]: x for x in p["locations"]}
    assert locs["THE LONG PIER"]["gone"] is True and locs["THE LONG PIER"]["look"].startswith("a rotting wooden pier")   # edited: kept, marked
    assert "THE LONG PIER" not in p["screenplay"]["text"] and "INT. THE GLASS HARBOUR DINER - NIGHT" in p["screenplay"]["text"]
    assert "Stranger" in c and "Strangr" not in c
    delete(scratch, pr["id"])
    assert not {"Ray Mercer", "June Mercer", "Stranger"} & set(chars(scratch))        # made for it and untouched: gone with it


def test_the_storyboard_is_organized_like_film_engines(scratch, tmp_path):
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    pr["locations"][0]["look"] = "chrome stools, checkerboard tile, low gold sun"
    scratch.post("/studio/api/projects", pr)
    os.makedirs(os.path.join(scratch.run, "input", "studio"), exist_ok=True)
    jobs0 = len(glob.glob(os.path.join(scratch.jobs, "*.json")))

    async def run(page):
        out = {}
        await page.js("window.confirm = () => true")
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{pr['id']}/storyboard"})
        out["cards"] = await page.until("document.querySelectorAll('.sbf-card').length === 8 && [...document.querySelectorAll('.sbf-card')].map(c => c.querySelector('.sbf-head b').textContent)")
        out["first"] = await page.js("(() => { const c = document.querySelector('.sbf-card'); return { tags: [...c.querySelectorAll('.sbf-tags .tag')].map(t => t.textContent), lines: c.querySelectorAll('.sbf-dl > div').length, cam: c.querySelector('.sbf-cam').value, none: !!c.querySelector('.sbf-img.none') }; })()")
        # 1B's lines don't all fit its card: a click on its text shows them all, another folds it again
        card = "[...document.querySelectorAll('.sbf-card')].find(c => c.querySelector('.sbf-head b').textContent.endsWith('/ 1B'))"
        shown = f"(() => {{ const t = {card}.querySelector('.sbf-txt'); return [t.className, [...t.querySelectorAll('.sbf-dl > div')].filter(x => getComputedStyle(x).display !== 'none').length, t.querySelector('.sbf-more').textContent]; }})()"
        out["folded"] = await page.js(shown)
        await page.js(card + ".querySelector('.sbf-desc').click()")
        out["unfolded"] = await page.js(shown)
        await page.js(card + ".querySelector('.sbf-more').click()")
        out["refolded"] = await page.js(shown)
        await page.js("(() => { const s = document.querySelector('#sbq'); s.value = 'chowder'; s.dispatchEvent(new Event('input')); })()")
        out["search"] = await page.js("document.querySelector('#sbn').textContent")
        await page.js("(() => { const s = document.querySelector('#sbq'); s.value = ''; s.dispatchEvent(new Event('input')); const f = document.querySelector('#sbfr'); f.value = 'has'; f.dispatchEvent(new Event('change')); })()")
        out["has"] = await page.js("document.querySelector('#sbn').textContent")
        await page.js("(() => { const f = document.querySelector('#sbfr'); f.value = ''; f.dispatchEvent(new Event('change')); })()")
        # 1B: its frame, then again (the first is kept as a version)
        gen = "[...document.querySelectorAll('.sbf-card')].find(c => c.querySelector('.sbf-head b').textContent.endsWith('/ 1B')).querySelector('[data-gen]')"
        for n in (1, 2):
            await page.js(gen + ".click()")
            await page.until(f"!{gen}.disabled && {gen}.textContent === 'Regen' && [...document.querySelectorAll('.sbf-card')].find(c => c.querySelector('.sbf-head b').textContent.endsWith('/ 1B')).querySelector('[data-ver]').textContent === 'v{n}'", tries=160)
        out["versions"] = await page.js("[...document.querySelectorAll('.sbf-card')].find(c => c.querySelector('.sbf-head b').textContent.endsWith('/ 1B')).querySelector('[data-ver]').textContent")
        # the screenplay changes 1B: its frame says so; the page's alert; keep it
        await open_screenplay(page, scratch, pr["id"])
        await page.js("(() => { const b = [...document.querySelectorAll('.sp-ed .sp-dialogue')].find(x => x.textContent.includes('still bad here')); const r = document.createRange(); r.selectNodeContents(b); const s = getSelection(); s.removeAllRanges(); s.addRange(r); b.closest('.sp-ed').focus(); })()")
        await page.type("This coffee gets worse every year.")
        await saved(page)
        out["indicator"] = await page.js("document.querySelector('#sp-sb').textContent")
        out["alert"] = await page.js("document.querySelector('[data-menu=pre]').classList.contains('alert')")
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{pr['id']}/storyboard"})
        out["drift"] = await page.until("document.querySelectorAll('.sbf-card').length === 8 && [...document.querySelectorAll('.sbf-card.drift .sbf-head b')].map(b => b.textContent)")
        await page.js("document.querySelector('.sbf-card.drift [data-drift]').click()")
        out["drift_says"] = await page.until("document.querySelector('#modal .chlist') && document.querySelector('#modal .chlist').innerText")
        await page.js("document.querySelector('#modal [data-keep]').click()")
        await page.until("!document.querySelector('#modal .chlist')")
        await asyncio.sleep(0.8)
        out["kept"] = await page.js("document.querySelectorAll('.sbf-card.drift').length")
        # the frame full screen, one after another
        await page.js("[...document.querySelectorAll('.sbf-card')].find(c => !c.querySelector('.sbf-img.none')).querySelector('.sbf-img').click()")
        out["viewer"] = await page.until("document.querySelector('.sbf-cap') && document.querySelector('.sbf-cap').textContent")
        await page.js("document.querySelector('.sbf-view [data-x]').click()")
        # the lock: nothing makes or replaces a frame
        await page.js("document.querySelector('#sblock').click()")
        out["locked"] = await page.until("document.querySelector('#sblock').textContent.includes('Locked') && [document.querySelector('#sbgen').disabled, [...document.querySelectorAll('[data-gen]')].every(b => b.disabled)]")
        return out

    out, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert out["cards"] == [f"Sc1 / 1{x}" for x in "ABCDEFGH"]
    f = out["first"]
    assert f["none"] and f["cam"] == "push in" and f["lines"] == 2
    assert f["tags"] == ["speaks ×2", "Ray", "June", "◎ The Glass Harbour Diner", "38 s"]
    assert out["folded"] == ["sbf-txt clip", 3, "Show all · 4 more lines ▾"]
    assert out["unfolded"] == ["sbf-txt open", 7, "Show less ▴"] and out["refolded"] == out["folded"]
    assert out["search"] == "1 frame of 8" and out["has"] == "0 frames of 8"
    assert out["versions"] == "v2"
    p = stored(scratch, pr["id"])
    s1b = p["shots"][1]
    assert s1b["keyframe"] and len(s1b["kf_versions"]) == 1 and s1b["kf_at"] and s1b["kf_src"]["setting"].startswith("INT. THE GLASS HARBOUR DINER")
    made = [json.load(open(x)) for x in sorted(glob.glob(os.path.join(scratch.jobs, "*.json")), key=os.path.getmtime)[jobs0:]]
    prompts = [v for j in made for n in j["prompt"].values() for v in (n.get("inputs") or {}).values() if isinstance(v, str) and "The Glass Harbour Diner" in v]
    assert prompts and all("Inside The Glass Harbour Diner (chrome stools, checkerboard tile, low gold sun), late afternoon" in t for t in prompts)
    assert out["indicator"] == "↻ 1 shot changed" and out["alert"] is True
    assert out["drift"] == ["Sc1 / 1B"]
    assert out["drift_says"] == "Ray: “Coffee's still bad here.” → “This coffee gets worse every year.”"
    assert out["kept"] == 0
    assert out["viewer"].startswith("Sc1 / 1B") and "1 of 1" in out["viewer"]
    assert out["locked"] == [True, True] and stored(scratch, pr["id"])["boardLocked"] is True
    delete(scratch, pr["id"])


def test_a_part_left_uncast_stays_so_and_a_shared_character_stays(scratch, tmp_path):
    p1, p2 = (upload(scratch, FOUNTAIN)[1]["project"] for _ in range(2))

    async def run(page):
        out = {}
        for p in (p1, p2):                               # each opens: its parts are cast (the second finds the first's)
            await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{p['id']}/cast"})
            await page.until(f"location.hash.includes('{p['id']}') && document.querySelector('.eyebrow a[href*=\"{p['id']}\"]') && document.querySelectorAll('.castrow .cr-desc').length === 2")
        out["made"] = {n: c.get("auto") for n, c in chars(scratch).items()}
        await page.js("(() => { const r = [...document.querySelectorAll('.castrow')].find(x => x.querySelector('b').textContent === 'June Mercer'); const s = r.querySelector('.cr-sel'); s.value = ''; s.dispatchEvent(new Event('change')); })()")
        await asyncio.sleep(1)
        await page.js("window.before = true")
        await page.call("Page.reload", {})                 # (the page from the server again, not the one before)
        out["uncast"] = await page.until("!window.before && document.querySelectorAll('.castrow').length === 2 && document.querySelectorAll('.castrow .cr-desc').length === 1 && [...document.querySelectorAll('.castrow')].map(r => r.querySelector('b').textContent + ': ' + r.querySelector('.cr-sel').selectedOptions[0].textContent)")
        return out

    out, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert out["made"] == {"Ray Mercer": None, "June Mercer": None}     # cast in two projects: nobody's typo
    assert sorted(out["uncast"]) == ["June Mercer: Not cast yet", "Ray Mercer: Ray Mercer"]
    assert stored(scratch, p2["id"])["castMap"]["JUNE"] == "" and len(stored(scratch, p2["id"])["cast"]) == 1
    delete(scratch, p1["id"])
    assert {"Ray Mercer", "June Mercer"} <= set(chars(scratch))            # the other project still casts them
    delete(scratch, p2["id"])
    for c in chars(scratch).values():                   # (not the next tests')
        urllib.request.urlopen(urllib.request.Request(f"{scratch.url}/studio/api/characters/{c['id']}", method="DELETE"), timeout=30).read()
