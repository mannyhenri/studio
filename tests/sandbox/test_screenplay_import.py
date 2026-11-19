"""Importing a screenplay on the Projects page: a PDF or Fountain file becomes a new project that holds the screenplay as
Fountain text, editable in the project's Screenplay step. Uses the two real files in screenplays/."""
import asyncio
import http.cookiejar
import importlib.util
import json
import os
import urllib.error
import urllib.request
import sys
import uuid

import aiohttp
import pytest

from test_sandbox import chrome
from find_chrome import WHY_NOT  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FOUNTAIN = os.path.join(REPO, "screenplays", "the_glass_harbour.fountain")
PDF = os.path.join(REPO, "screenplays", "The Glass Harbour.pdf")
_spec = importlib.util.spec_from_file_location("studio_screenplay_t", os.path.join(REPO, "ComfyUI-Studio", "screenplay.py"))
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)


def upload(box, path, data=None, filename=None, fields=None, route="/studio/api/projects/import_screenplay", opener=None):
    """A multipart upload like the page's: the file as "file", plus form fields."""
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in (fields or {}).items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    data = open(path, "rb").read() if data is None else data
    name = filename or os.path.basename(path)
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
             f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(box.url + route, data=body, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with (opener.open(req, timeout=60) if opener else urllib.request.urlopen(req, timeout=60)) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def delete(box, pid, board=True):
    """Deletes a test's project, and its storyboard (which would stay, and show in Other storyboards, for the next tests)."""
    pr = next((x for x in box.get("/studio/api/projects") if x["id"] == pid), None)
    urllib.request.urlopen(urllib.request.Request(box.url + f"/studio/api/projects/{pid}", method="DELETE"), timeout=30).read()
    if board and pr and pr.get("board"):
        urllib.request.urlopen(urllib.request.Request(box.url + f"/studio/api/boards/{pr['board']}", method="DELETE"), timeout=30).read()


def test_a_pdf_becomes_a_project(scratch):
    code, j = upload(scratch, PDF, fields={"private": "0"})
    assert code == 200, j
    pr = j["project"]
    assert pr["title"] == "The Glass Harbour" and pr["stage"] == "Screenplay" and pr["step"] == "screenplay" and pr["private"] is False
    # broken down on import: the scene, its shots, the characters with their introductions
    assert [(x["number"], x["heading"]) for x in pr["scenes"]] == [("1", "INT. THE GLASS HARBOUR DINER - LATE AFTERNOON")]
    assert len(pr["shots"]) == 8 and {x["scene"] for x in pr["shots"]} == {pr["scenes"][0]["id"]}    # one per camera setup
    assert [(r["name"], r["full"]) for r in pr["roles"]] == [("RAY", "Ray Mercer"), ("JUNE", "June Mercer")]
    s = pr["screenplay"]
    assert s["source"] == "pdf" and s["filename"] == "The Glass Harbour.pdf"
    assert s["text"] == sp.fountain_from_pdf(open(PDF, "rb").read())[0]
    assert s["text"].startswith("Title: The Glass Harbour\nCredit: Written by\nAuthor: Alex Rivera\nDraft date: 08/28/2026\n\nINT. THE GLASS HARBOUR DINER - LATE AFTERNOON #1#\n")
    assert j["summary"] == {"title": "The Glass Harbour", "scenes": 1, "pages": 7, "characters": ["RAY", "JUNE"], "shots": len(pr["shots"])}
    assert j["warnings"] == []
    stored = next(p for p in scratch.get("/studio/api/projects") if p["id"] == pr["id"])
    assert stored["screenplay"]["text"] == s["text"]
    delete(scratch, pr["id"])


def test_a_fountain_file_becomes_a_project_read_as_its_pdf(scratch):
    code, j = upload(scratch, FOUNTAIN)
    assert code == 200, j
    pr = j["project"]
    assert pr["title"] == "The Glass Harbour" and pr["screenplay"]["source"] == "fountain"
    text = pr["screenplay"]["text"]
    assert text == sp.fountain_from_text(open(FOUNTAIN, "rb").read())       # each action line a paragraph, the rest as written
    assert text.replace("\n\n", "\n") == open(FOUNTAIN, encoding="utf-8").read().replace("\n\n", "\n")
    from_pdf = sp.parse(sp.fountain_from_pdf(open(PDF, "rb").read())[0])
    assert [(e["type"], e["text"]) for e in sp.parse(text)["elements"]] == [(e["type"], e["text"]) for e in from_pdf["elements"]]
    assert len(pr["shots"]) == 8
    delete(scratch, pr["id"])


def test_the_two_files_give_the_same_screenplay(scratch):
    texts = [upload(scratch, f)[1]["project"] for f in (PDF, FOUNTAIN)]
    parsed = [scratch.post("/studio/api/screenplay/parse", {"text": t["screenplay"]["text"]})[1] for t in texts]
    norm = []
    for p in parsed:
        out = []
        for e in p["elements"]:
            t = " ".join(e["text"].split())
            if e["type"] == "action" and out and out[-1][0] == "action":
                out[-1] = ("action", out[-1][1] + " " + t)
            else:
                out.append((e["type"], t))
        norm.append(out)
    assert norm[0] == norm[1] and len(norm[0]) > 100
    assert parsed[0]["title_page"] == parsed[1]["title_page"] and parsed[0]["characters"] == parsed[1]["characters"]
    for t in texts:
        delete(scratch, t["id"])


def test_the_editor_saves_elements_as_fountain_and_keeps_the_breakdown(scratch):
    """The page editor sends its elements; the server writes the Fountain text and merges the breakdown."""
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    code, p = scratch.post("/studio/api/screenplay/parse", {"text": pr["screenplay"]["text"]})
    els = p["elements"] + [{"type": "scene", "text": "EXT. THE HARBOUR - NIGHT"}, {"type": "action", "text": "June walks to the end of the pier."},
                           {"type": "character", "text": "JUNE"}, {"type": "parenthetical", "text": "(quietly)"}, {"type": "dialogue", "text": "Not yet."}]
    code, r = scratch.post("/studio/api/screenplay/breakdown", {"elements": els, "title_page": p["title_page"], "scenes": pr["scenes"], "shots": pr["shots"]})
    assert code == 200
    assert r["text"].startswith("Title: The Glass Harbour\n") and r["text"].endswith("EXT. THE HARBOUR - NIGHT\n\nJune walks to the end of the pier.\n\nJUNE\n(quietly)\nNot yet.\n")
    assert [x["heading"] for x in r["scenes"]] == ["INT. THE GLASS HARBOUR DINER - LATE AFTERNOON", "EXT. THE HARBOUR - NIGHT"]
    assert r["scenes"][0]["id"] == pr["scenes"][0]["id"]
    assert r["shots"][:len(pr["shots"])] == pr["shots"]                                   # scene 1's shots untouched
    new = [x for x in r["shots"] if x["scene"] == r["scenes"][1]["id"]]
    assert len(new) == 1 and new[0]["lines"] == [{"who": "JUNE", "text": "Not yet.", "paren": "(quietly)"}]
    pr.update(scenes=r["scenes"], shots=r["shots"], roles=r["roles"])
    pr["screenplay"]["text"] = r["text"]
    assert scratch.post("/studio/api/projects", pr)[0] == 200
    again = next(x for x in scratch.get("/studio/api/projects") if x["id"] == pr["id"])
    assert again["screenplay"]["text"] == r["text"] and len(again["scenes"]) == 2
    delete(scratch, pr["id"])


def test_the_storyboard_is_the_breakdown(scratch):
    """The project's board has exactly the breakdown's shots, in order, with its shot numbers, cameras and lengths."""
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    pr["shots"][2]["keyframe"] = "studio/june.jpg"
    scratch.post("/studio/api/projects", pr)
    code, r = scratch.post(f"/studio/api/projects/{pr['id']}/to_board", {"private": False})
    assert code == 200 and r["shots"] == len(pr["shots"])
    board = next(b for b in scratch.get("/studio/api/boards") if b["id"] == r["board"])
    codes = sp.shot_codes(pr["scenes"], pr["shots"])
    assert board["project"] == pr["id"] and board["name"] == "The Glass Harbour"
    assert [x["projShot"] for x in board["shots"]] == [x["id"] for x in pr["shots"]]
    assert [x["code"] for x in board["shots"]] == [codes[x["id"]] for x in pr["shots"]] == r["codes"]
    assert r["codes"][:3] == ["1A", "1B", "1C"] and "1I" not in r["codes"] and "1O" not in r["codes"]
    cams = {"static": "static locked-off camera", "push in": "slow dolly push-in toward the subject", "tracking": "tracking shot following the subject",
            "crane down": "crane shot descending", "crane up": "crane shot rising up", "pull back": "slow dolly pull-back revealing the scene",
            "close-up": "tight close-up", "low angle": "low-angle hero shot"}
    assert all(x["camera"] == cams[s["camera"]] and x["seconds"] == s["seconds"] for x, s in zip(board["shots"], pr["shots"]))
    render = lambda secs: next((r for r in (5, 8, 10, 15) if r >= secs - 0.2), 15)   # noqa: E731  (the Video app's lengths)
    assert all(x["trimOut"] == (x["seconds"] if render(x["seconds"]) > x["seconds"] else 0) for x in board["shots"])   # the cut is the shot's length
    assert [x["cont"] for x in board["shots"]] == [None] + r["codes"][:-1]          # one continuous take: each move goes on from the last
    assert board["shots"][2]["image"] == "studio/june.jpg"
    first = board["shots"][0]["prompt"]
    assert first.startswith("ONE SHOT. NO CUTS.") and first.endswith("Inside The Glass Harbour Diner, late afternoon.")   # the camera is its own field
    talk = next(x for x, s in zip(board["shots"], pr["shots"]) if s["lines"] and s["lines"][0]["text"] == "You look like her.")
    assert 'Ray says: "You look like her."' in talk["prompt"] and 'June says: "People say that."' in talk["prompt"]
    # what is done on the board stays; a shot removed from the breakdown leaves the board; a loose board shot joins the breakdown
    board["shots"][0]["prompt"] = "My own prompt"
    board["shots"][1]["takes"] = [{"output": {"filename": "x.mp4", "subfolder": "Videos"}}]
    board["shots"].append({"id": "loose1", "prompt": "A gull on the railing.", "image": "", "camera": "tight close-up", "seconds": 3})
    scratch.post("/studio/api/boards", board)
    gone = pr["shots"].pop(1)
    pr["board"] = r["board"]                                           # as the page keeps it
    scratch.post("/studio/api/projects", pr)
    code, r2 = scratch.post(f"/studio/api/projects/{pr['id']}/to_board", {"private": False})
    board = next(b for b in scratch.get("/studio/api/boards") if b["id"] == r["board"])
    pr = next(x for x in scratch.get("/studio/api/projects") if x["id"] == pr["id"])
    assert board["shots"][0]["prompt"] == "My own prompt" and gone["id"] not in [x["projShot"] for x in board["shots"]]
    assert [x["projShot"] for x in board["shots"]] == [x["id"] for x in pr["shots"]]
    adopted = pr["shots"][-1]
    assert adopted["action"] == "A gull on the railing." and adopted["camera"] == "close-up" and adopted["manual"] and board["shots"][-1]["code"] == r2["codes"][-1]
    delete(scratch, pr["id"])


def test_the_screenplay_exports_as_a_standard_pdf(scratch):
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    with urllib.request.urlopen(scratch.url + f"/studio/api/projects/{pr['id']}/screenplay.pdf", timeout=60) as r:
        assert r.headers["Content-Type"] == "application/pdf" and 'filename="The Glass Harbour.pdf"' in r.headers["Content-Disposition"]
        pdf = r.read()
    pages, _ = sp.pdf_lines(pdf)
    assert len(pages) == 1 + sp.parse(pr["screenplay"]["text"])["pages"]                 # the title page and the script
    back = sp.parse(sp.fountain_from_pdf(pdf)[0])
    src = sp.parse(pr["screenplay"]["text"])
    norm = lambda els: [(e["type"], " ".join(e["text"].split())) for e in els]          # noqa: E731
    merge = lambda xs: [x for k, x in enumerate(xs) if not (x[0] == "action" and k and xs[k - 1][0] == "action")]   # noqa: E731
    assert len(merge(norm(back["elements"]))) == len(merge(norm(src["elements"]))) and back["title_page"] == src["title_page"]
    delete(scratch, pr["id"])


def test_convert_reads_without_saving(scratch):
    before = len(scratch.get("/studio/api/projects"))
    code, j = upload(scratch, PDF, route="/studio/api/screenplay/convert")
    assert code == 200 and j["source"] == "pdf" and j["summary"]["scenes"] == 1 and "RAY" in j["text"]
    assert len(scratch.get("/studio/api/projects")) == before


def test_a_final_draft_file_becomes_a_project(scratch):
    """Final Draft (.fdx): the same screenplay as its Fountain, and it goes back out as a Final Draft file."""
    spec = sp.parse(open(FOUNTAIN, encoding="utf-8").read())
    fdx = sp.to_fdx(spec["title_page"], spec["elements"])
    code, j = upload(scratch, FOUNTAIN, data=fdx, filename="The Glass Harbour.fdx")
    assert code == 200, j
    pr = j["project"]
    assert pr["screenplay"]["source"] == "fdx" and pr["title"] == "The Glass Harbour" and len(pr["shots"]) == 8
    els = lambda p: [(e["type"], e["text"]) for e in p["elements"]]
    assert els(sp.parse(pr["screenplay"]["text"])) == els(spec)
    for ext, head in (("fdx", b"<?xml"), ("txt", b"\n"), ("pdf", b"%PDF-")):
        with urllib.request.urlopen(scratch.url + f"/studio/api/projects/{pr['id']}/screenplay.{ext}", timeout=60) as r:
            body = r.read()
            assert body.startswith(head) and f'filename="The Glass Harbour.{ext}"' in r.headers["Content-Disposition"], ext
    delete(scratch, pr["id"])


def test_files_that_arent_screenplays_are_refused_with_a_reason(scratch):
    before = len(scratch.get("/studio/api/projects"))
    cases = [(dict(path=os.path.join(REPO, "sandbox", "samples", "diner.jpg")), "Import a screenplay as a .pdf, .fdx (Final Draft) or .fountain file."),
             (dict(path=PDF, data=b"not a pdf at all", filename="script.pdf"), "This isn't a PDF file."),
             (dict(path=PDF, data=b"<FinalDraft/>", filename="script.fdx"), "There's no screenplay text in that Final Draft file."),
             (dict(path=PDF, data=b"<html><body>hi</body></html>", filename="script.fdx"), "That isn't a Final Draft (.fdx) file."),
             (dict(path=PDF, data=b"", filename="empty.fountain"), "Pick a screenplay file"),
             (dict(path=PDF, data=b"   \n\n", filename="blank.fountain"), "There's no screenplay text"),
             (dict(path=PDF, data=open(PDF, "rb").read().replace(b"trailer\n<<", b"trailer\n<< /Encrypt 99 0 R")), "password-protected")]
    for kw, msg in cases:
        code, body = upload(scratch, **kw)
        assert code == 400 and msg in body, (kw.get("filename"), code, body)
    req = urllib.request.Request(scratch.url + "/studio/api/projects/import_screenplay", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=30)
    assert e.value.code == 400
    assert len(scratch.get("/studio/api/projects")) == before


def test_incognito_import_needs_the_unlock_and_stays_private(scratch):
    code, body = upload(scratch, FOUNTAIN, fields={"private": "1"})
    assert code == 403 and "Incognito is locked" in body
    req = urllib.request.Request(scratch.url + "/studio/password", data=b"new=sandbox-pw&again=sandbox-pw", method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert b"Password saved" in urllib.request.urlopen(req, timeout=30).read()
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(scratch.url + "/studio/api/private/unlock", data=json.dumps({"password": "sandbox-pw"}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    assert json.loads(opener.open(req, timeout=30).read()) == {"unlocked": True}
    code, j = upload(scratch, FOUNTAIN, fields={"private": "1"}, opener=opener)
    assert code == 200 and j["project"]["private"] is True
    pid = j["project"]["id"]
    assert pid not in [p["id"] for p in scratch.get("/studio/api/projects")]
    mine = json.loads(opener.open(scratch.url + "/studio/api/projects?private=1", timeout=30).read())
    assert pid in [p["id"] for p in mine]


# ------------------------------------------------------------------ the page in a real browser
class Page:
    """A headless Chrome tab driven over the DevTools protocol: navigate, evaluate, press keys, type; page errors collected."""

    def __init__(self, ws):
        self.ws, self.n, self.errors = ws, 0, []

    async def call(self, method, params=None):
        self.n += 1
        me = self.n
        await self.ws.send_json({"id": me, "method": method, "params": params or {}})
        while True:
            msg = await asyncio.wait_for(self.ws.receive_json(), 30)
            meth, p = msg.get("method"), msg.get("params", {})
            if meth == "Runtime.exceptionThrown":
                self.errors.append(p["exceptionDetails"].get("exception", {}).get("description", "exception"))
            elif meth == "Runtime.consoleAPICalled" and p.get("type") == "error":
                self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", [])))
            elif meth == "Network.responseReceived" and p["response"]["status"] >= 400:
                self.errors.append(f"HTTP {p['response']['status']} {p['response']['url']}")
            if msg.get("id") == me:
                return msg.get("result", {})

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("value")

    async def until(self, expr, tries=80):
        for _ in range(tries):
            v = await self.js(expr)
            if v:
                return v
            await asyncio.sleep(0.25)
        return None

    async def key(self, key, code, vk, text="", modifiers=0):
        for kind in ("keyDown", "keyUp"):
            ev = {"type": kind, "key": key, "code": code, "windowsVirtualKeyCode": vk, "modifiers": modifiers}
            if kind == "keyDown" and text:
                ev["text"] = text
            await self.call("Input.dispatchKeyEvent", ev)
        await asyncio.sleep(0.1)

    async def type(self, text):
        await self.call("Input.insertText", {"text": text})
        await asyncio.sleep(0.1)


async def _with_chrome(chrome_path, port, profile, fn):
    proc = await asyncio.create_subprocess_exec(chrome_path, "--headless=new", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={port}",
                                                f"--user-data-dir={profile}", "--window-size=1400,900", "about:blank",
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        async with aiohttp.ClientSession() as s:
            targets = []
            for _ in range(300):                          # Chrome can take a while to start on a busy machine
                try:
                    async with s.get(f"http://127.0.0.1:{port}/json/list") as r:
                        targets = [t for t in await r.json() if t["type"] == "page"]
                    if targets:
                        break
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(0.1)
            if not targets:
                raise RuntimeError("Chrome didn't start within 30 s")
            async with s.ws_connect(targets[0]["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                page = Page(ws)
                for m in ("Runtime.enable", "Network.enable", "Page.enable"):
                    await page.call(m)
                return await fn(page), [e for e in page.errors if "fonts.googleapis" not in e and "ERR_NAME_NOT_RESOLVED" not in e]
    finally:
        proc.kill()
        await proc.wait()


# the top bar: a phase's menu (Pre-production, Production, Post-production) lists the open project's pages first
PROJECT_PAGES = """(ph => { document.querySelector(`[data-menu=${ph}]`).click();
    const g = [...document.querySelectorAll('#menupop .mg')].find(g => g.querySelector('.mgh').textContent.startsWith('Project'));
    const out = g ? [...g.querySelectorAll('.mi b')].map(b => b.textContent) : null; document.querySelector(`[data-menu=${ph}]`).click(); return out; })"""
GO = """((ph, label) => { document.querySelector(`[data-menu=${ph}]`).click();
    [...document.querySelectorAll('#menupop .mi')].find(a => a.querySelector('b').textContent === label).click(); })"""


def _port():
    import socket
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


@pytest.mark.skipif(chrome() is None, reason=WHY_NOT)
def test_the_screenplay_is_one_page_you_type_on(scratch, tmp_path):
    pr = upload(scratch, PDF)[1]["project"]

    async def run(page):
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{pr['id']}"})
        ready = await page.until("document.querySelectorAll('.sp-ed .sp-b').length > 100 && !!document.querySelector('#sp-stats').textContent")
        seen = await page.js("""(() => ({steps: ['pre', 'prod', 'post'].map(ph => (""" + PROJECT_PAGES + """)(ph)),
            panes: document.querySelectorAll('.sp-ed, textarea.sp-src, .sp-page').length,
            titleOnPage: document.querySelectorAll('.sp-sheet input, .sp-sheet textarea, .sp-tp').length,
            first: [...document.querySelectorAll('.sp-ed .sp-b')].slice(0, 3).map(b => b.dataset.t + ':' + b.textContent),
            cues: document.querySelectorAll('.sp-ed .sp-character').length, parens: [...document.querySelectorAll('.sp-ed .sp-parenthetical')].map(b => b.textContent),
            stats: document.querySelector('#sp-stats').textContent, scenes: [...document.querySelectorAll('#sp-jump option')].map(o => o.textContent),
            nav: [...document.querySelectorAll('#mtop a, #bnav a')].map(a => a.getAttribute('href'))}))()""")
        # type at the end: Enter after the last line is action, Tab makes it a character, Enter after a name is dialogue
        await page.js("""(() => { const b = [...document.querySelectorAll('.sp-ed .sp-b')].pop(); const r = document.createRange();
            r.selectNodeContents(b); r.collapse(false); const s = getSelection(); s.removeAllRanges(); s.addRange(r); b.closest('.sp-ed').focus(); })()""")
        await page.key("Enter", "Enter", 13, "\r")
        after_enter = await page.js("document.querySelector('#sp-type').value")
        await page.key("Tab", "Tab", 9)
        after_tab = await page.js("document.querySelector('#sp-type').value")
        await page.type("ray")
        await page.key("Enter", "Enter", 13, "\r")
        after_name = await page.js("document.querySelector('#sp-type').value")
        await page.type("Same time next week?")
        saved = await page.until("document.querySelector('#sp-save').textContent === 'saved' && document.querySelector('#sp-stats').textContent")
        # a camera direction typed at the end is a new setup: the shots follow the screenplay as it's saved
        await page.key("Enter", "Enter", 13, "\r")
        await page.key("Enter", "Enter", 13, "\r")
        await page.type("The camera pushes in on Ray until his face fills the frame.")
        shots = await page.until("document.querySelector('#sp-save').textContent === 'saved' && document.querySelector('#sp-stats').textContent.includes('9 shots') && document.querySelector('#sp-stats').textContent")
        # the title page: a dialog, not the page
        await page.js("document.querySelector('#sp-tp').click()")
        dialog = await page.until("document.querySelector('.tpd') && [...document.querySelectorAll('.tpd [data-k]')].map(i => i.dataset.k + '=' + i.value)")
        await page.js("(() => { const t = document.querySelector('.tpd [data-k=Title]'); t.value = 'The Glass Harbour (revised)'; document.querySelector('.tpd [data-go]').click(); })()")
        renamed = await page.until("document.querySelector('#sp-save').textContent === 'saved' && document.querySelector('#projname').textContent")
        return ready, seen, (after_enter, after_tab, after_name), saved, shots, dialog, renamed

    (ready, seen, typed, saved, shots, dialog, renamed), errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert ready, "the screenplay didn't open"
    assert seen["steps"] == [["Screenplay", "Scenes & shots", "Cast", "Locations", "Storyboard"], ["Takes"], ["Music & dialogue", "Film edit"]]
    assert seen["panes"] == 1                                          # one screen: the page itself
    assert "#/storyboard" not in seen["nav"]                           # the storyboard lives in the project now
    assert seen["titleOnPage"] == 0                                    # the title page isn't on the script's page
    assert dialog[:3] == ["Title=The Glass Harbour", "Credit=Written by", "Author=Alex Rivera"] and "Draft date=08/28/2026" in dialog
    assert seen["first"] == ["scene:INT. THE GLASS HARBOUR DINER - LATE AFTERNOON", "action:ONE SHOT. NO CUTS. FIVE MINUTES, ONE BREATH.",
                             "action:A 1941 diner, re-topped in '52 and kept spotless — gleaming like the day it opened. Checkerboard tile, chrome stools, boomerang formica. On the plate glass, hand-painted and only just beginning to craze, a harbour scene and the name of the place, reading backwards from in here. Late gold light comes through it low and cuts the room in half."]
    assert seen["cues"] == 63 and seen["parens"] == ["(too fast, too loud)", "(beat)"]
    assert seen["stats"].startswith("1 scene · ") and " shots · 2 characters · about 7 pages" in seen["stats"]
    assert seen["scenes"][1] == "1. INT. THE GLASS HARBOUR DINER - LATE AFTERNOON"
    assert typed == ("action", "character", "dialogue") and saved
    assert shots and renamed == "The Glass Harbour (revised)"
    stored = next(x for x in scratch.get("/studio/api/projects") if x["id"] == pr["id"])
    assert stored["screenplay"]["text"].startswith("Title: The Glass Harbour (revised)\nCredit: Written by\nAuthor: Alex Rivera\n")
    assert stored["screenplay"]["text"].endswith("FADE OUT.\n\nRAY\nSame time next week?\n\nThe camera pushes in on Ray until his face fills the frame.\n")
    assert stored["title"] == "The Glass Harbour (revised)"
    # the shots followed the typing; the eight there were kept their ids. FADE OUT. is a cut: Ray's new line opens a shot,
    # and the camera direction after it is that shot's move
    assert [x["id"] for x in stored["shots"][:8]] == [x["id"] for x in pr["shots"]]
    assert len(stored["shots"]) == 9 and stored["shots"][8]["lines"] == [{"who": "RAY", "text": "Same time next week?"}]
    assert stored["shots"][8]["camera"] == "push in"
    delete(scratch, pr["id"])


@pytest.mark.skipif(chrome() is None, reason=WHY_NOT)
def test_every_project_step_opens_and_the_storyboard_route_leads_into_the_project(scratch, tmp_path):
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    code, r = scratch.post(f"/studio/api/projects/{pr['id']}/to_board", {"private": False})

    async def run(page):
        out = {}
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{pr['id']}"})
        await page.until("document.querySelectorAll('#menus [data-menu]').length === 3 && document.querySelectorAll('.sp-ed .sp-b').length")
        pages = [("pre", "Screenplay", ".sp-ed .sp-b"), ("pre", "Scenes & shots", ".scn .shotrow"), ("pre", "Cast", ".castrow .cr-desc"), ("pre", "Locations", ".loc-card"),
                 ("pre", "Storyboard", ".sbf-card"), ("prod", "Takes", ".pboard .shot"), ("post", "Music & dialogue", "#pvk"), ("post", "Film edit", ".cutrow")]
        for k, (ph, label, probe) in enumerate(pages):
            await page.js(f"({GO})('{ph}', '{label}')")
            out[k] = await page.until(f"document.querySelector('.proj h1')?.textContent === '{label}' && document.querySelectorAll('{probe}').length")
        await page.js(f"localStorage.setItem('studio.board', JSON.stringify('{r['board']}'))")
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/storyboard"})
        out["route"] = await page.until("location.hash.startsWith('#/projects/') && document.querySelector('.pboard .shot') && location.hash.split('/').pop() + ' ' + document.querySelector('.proj h1').textContent")
        return out

    out, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert out[0] > 100 and out[1] == len(pr["shots"]) and out[2] == 2 and out[3] == 1 and out[4] == len(pr["shots"]) and out[5] == len(pr["shots"]) and out[6] == 1
    assert out[7] == len(pr["shots"])                                   # the film edit: the cut, shot by shot
    assert out["route"] == "takes Takes"                                # the storyboard route of old: the project's takes
    delete(scratch, pr["id"])


@pytest.mark.skipif(chrome() is None, reason=WHY_NOT)
def test_the_page_marks_fall_where_the_standard_page_breaks(scratch, tmp_path):
    """The editor's sheet has the standard page's measures: every element wraps to the same lines as the layout, so the
    page marks are where the pages of the PDF break."""
    pr = upload(scratch, PDF)[1]["project"]
    parsed = sp.parse(pr["screenplay"]["text"])
    want = [len(sp._lines_of(e, 0)) for e in parsed["elements"]]

    async def run(page):
        await page.call("Emulation.setDeviceMetricsOverride", {"width": 1400, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{pr['id']}"})
        await page.until("document.querySelectorAll('.sp-pm').length > 0")
        return await page.js("""(() => { const ed = document.querySelector('.sp-ed'), lh = parseFloat(getComputedStyle(ed).lineHeight);
            const bs = [...ed.children];
            return {lines: bs.map(b => Math.round(b.getBoundingClientRect().height / lh)),
                    marks: [...document.querySelectorAll('.sp-pm')].map(m => { const y = parseFloat(m.style.top);
                        const b = bs.find(b => b.offsetTop + b.offsetHeight > y + 1); return [m.textContent, bs.indexOf(b), Math.max(0, Math.round((y - b.offsetTop) / lh))]; })}; })()""")

    got, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert got["lines"] == want
    assert got["marks"] == [[f"{m['page']}.", m["el"], m["line"]] for m in parsed["page_starts"]] and len(got["marks"]) == parsed["pages"] - 1
    assert [m[0] for m in got["marks"]] == ["2.", "3.", "4.", "5.", "6.", "7."]
    delete(scratch, pr["id"])


@pytest.mark.skipif(chrome() is None, reason=WHY_NOT)
def test_the_board_changes_the_breakdown(scratch, tmp_path):
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    pr["step"] = "board"
    scratch.post("/studio/api/projects", pr)

    def state():
        p = next(x for x in scratch.get("/studio/api/projects") if x["id"] == pr["id"])
        b = next(x for x in scratch.get("/studio/api/boards") if x["id"] == p["board"])
        return p, b

    async def run(page):
        out = {}
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{pr['id']}"})
        await page.until("document.querySelectorAll('.pboard .shot').length >= 8")
        await page.js("window.confirm = () => true")
        out["labels"] = await page.js("[...document.querySelectorAll('.pboard .shead b')].slice(0, 3).map(b => b.textContent)")
        out["groups"] = await page.js("[...document.querySelectorAll('.pboard .sbscene')].map(b => b.textContent)")
        out["buttons"] = await page.js("[...document.querySelectorAll('.pboard .shot')].slice(0, 2).map(s => [...s.querySelectorAll('.shead button')].map(b => b.dataset.up != null ? 'up' : b.dataset.dn != null ? 'down' : b.dataset.rm != null ? 'remove' : '?'))")
        steps = [("remove", "document.querySelectorAll('.pboard .shot')[2].querySelector('[data-rm]').click()"),
                 ("add", "document.querySelector('#add').click()"),
                 ("up", "[...document.querySelectorAll('.pboard .shot')].pop().querySelector('[data-up]').click()"),
                 ("camera", "(() => { const s = document.querySelectorAll('.pboard .shot')[1].querySelector('.scam'); s.value = 'tight close-up'; s.dispatchEvent(new Event('change')); })()")]
        for name, js in steps:
            await page.js(js)
            await asyncio.sleep(1.5)
            out[name] = state()
        out["after"] = await page.js("[...document.querySelectorAll('.pboard .shead b')].map(b => b.textContent)")
        return out

    out, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert out["labels"] == ["SHOT 1A", "SHOT 1B", "SHOT 1C"] and out["groups"] == ["1 · INT. THE GLASS HARBOUR DINER - LATE AFTERNOON"]
    assert out["buttons"] == [[], ["remove"]]                          # setups keep the story's order; the first setup stays
    for name, size in (("remove", 7), ("add", 8), ("up", 8), ("camera", 8)):
        p, b = out[name]
        assert len(p["shots"]) == len(b["shots"]) == size, name
        assert [x["projShot"] for x in b["shots"]] == [x["id"] for x in p["shots"]], name
    p, b = out["remove"]                                                # removing setup 1C merged its beats into 1B
    assert p["scenes"][0]["joins"] == [pr["shots"][2]["anchor"]] and len(p["shots"][1]["beats"]) == len(pr["shots"][1]["beats"]) + len(pr["shots"][2]["beats"])
    assert all(ln in [x for s2 in p["shots"] for x in s2["lines"]] for s2 in pr["shots"] for ln in s2["lines"])   # nothing of the screenplay lost
    p, b = out["add"]
    assert p["shots"][-1]["manual"] and p["shots"][-1]["after"] == p["shots"][-2]["id"]
    p, b = out["up"]                                                    # an added shot moves
    assert p["shots"][-2]["manual"] and p["shots"][-2]["after"] == p["shots"][-3]["id"]
    p, b = out["camera"]
    assert p["shots"][1]["camera"] == "close-up" and p["shots"][1]["camera_set"] and b["shots"][1]["camera"] == "tight close-up"
    codes = sp.shot_codes(p["scenes"], p["shots"])
    assert out["after"] == ["SHOT " + codes[x["id"]] for x in p["shots"]]
    delete(scratch, pr["id"])


# ------------------------------------------------------------------ characters: the sheet, reference pictures, LoRAs
def _jobs_since(box, n0):
    import glob
    files = sorted(glob.glob(os.path.join(box.jobs, "*.json")), key=os.path.getmtime)
    return [json.load(open(f)) for f in files[n0:] if "refused" not in os.path.basename(f)]


def _job_count(box):
    import glob
    return len(glob.glob(os.path.join(box.jobs, "*.json")))


def _wait_job(box, jid, limit=60):
    import time
    for _ in range(limit * 4):
        j = box.get(f"/studio/api/job/{jid}")
        if j.get("status") != "running":
            return j
        time.sleep(0.25)
    return j


def _texts(job):
    return " ".join(v for n in job["prompt"].values() for v in (n.get("inputs") or {}).values() if isinstance(v, str))


def _loras(job):
    return [n["inputs"]["lora_name"] for n in job["prompt"].values() if n["class_type"] == "LoraLoaderModelOnly"]


SHEET = {"name": "Ray Mercer", "age": "57", "gender": "man", "build": "broad-shouldered", "skin": "weathered", "features": "square jaw",
         "eyes": "grey", "hair": "short grey", "facial_hair": "stubble", "marks": "scar through the left eyebrow",
         "wardrobe": "tan canvas work jacket", "accessories": "felt hat", "look": "carries himself like a tired boxer", "trigger": "rmrc_man",
         "face": "", "voice": ""}
SHEET_TEXT = ("rmrc_man, 57-year-old man, broad-shouldered, weathered skin, square jaw, grey eyes, short grey hair, stubble, "
              "scar through the left eyebrow, wearing tan canvas work jacket, felt hat, carries himself like a tired boxer")


def test_a_reference_picture_from_the_character_sheet(scratch):
    open(os.path.join(scratch.run, "models", "loras", "ray_lora.safetensors"), "wb").close()
    c = scratch.post("/studio/api/characters", dict(SHEET, lora={"name": "ray_lora.safetensors", "strength": 0.9, "family": ""}))[1]
    apps = scratch.get("/studio/api/apps")
    app = next(a for a in apps if a["name"].endswith("Image"))
    prompt_key = next(i["key"] for i in app["inputs"] if i["kind"] == "text")
    n0 = _job_count(scratch)
    code, r = scratch.post(f"/studio/api/characters/{c['id']}/portrait", {"values": {}, "prompt_key": prompt_key,
                                                                        "loras": [{"name": "ray_lora.safetensors", "strength": 0.9}]})
    assert code == 200
    j = _wait_job(scratch, r["id"])
    assert j["status"] == "done", j
    c2 = next(x for x in scratch.get("/studio/api/characters") if x["id"] == c["id"])
    assert c2["face"] == j["result"]["picture"] and c2["pictures"] == [c2["face"]]     # the first picture becomes the face
    job = next(x for x in _jobs_since(scratch, n0) if _loras(x))
    assert "Ray Mercer, " + SHEET_TEXT in _texts(job) and _loras(job) == ["ray_lora.safetensors"]
    # a second picture is kept with the others; the face stays until one is picked
    r = scratch.post(f"/studio/api/characters/{c['id']}/portrait", {"values": {}, "prompt_key": prompt_key})[1]
    assert _wait_job(scratch, r["id"])["status"] == "done"
    c3 = next(x for x in scratch.get("/studio/api/characters") if x["id"] == c["id"])
    assert len(c3["pictures"]) == 2 and c3["face"] == c2["face"]
    urllib.request.urlopen(urllib.request.Request(scratch.url + f"/studio/api/characters/{c['id']}", method="DELETE")).read()


def test_keyframes_carry_the_character_sheet_and_lora(scratch):
    open(os.path.join(scratch.run, "models", "loras", "ray_lora.safetensors"), "wb").close()
    c = scratch.post("/studio/api/characters", dict(SHEET, lora={"name": "ray_lora.safetensors", "strength": 0.9, "family": "qwen"}))[1]
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    pr.update(cast=[c["id"]], castMap={"RAY": c["id"]})
    scratch.post("/studio/api/projects", pr)
    # as Claude asks for them: the server works out each shot's characters, their sheet and LoRAs
    n0 = _job_count(scratch)
    code, r = scratch.post(f"/studio/api/projects/{pr['id']}/keyframes", {"private": False, "model": "qwen"})
    assert code == 200
    assert _wait_job(scratch, r["id"], 120)["status"] == "done"
    jobs = [j for j in _jobs_since(scratch, n0) if str(j.get("label") or "").startswith("Project keyframe")]   # not the prompt step
    with_ray = [j for j in jobs if "Ray (" + SHEET_TEXT + ")" in _texts(j)]
    assert with_ray and all(_loras(j) == ["ray_lora.safetensors"] for j in with_ray)
    # a LoRA made for another model family isn't loaded
    c["lora"]["family"] = "flux3"
    scratch.post("/studio/api/characters", c)
    n1 = _job_count(scratch)
    r = scratch.post(f"/studio/api/projects/{pr['id']}/keyframes", {"private": False, "model": "qwen", "redo": True})[1]
    assert _wait_job(scratch, r["id"], 120)["status"] == "done"
    assert not any(_loras(j) for j in _jobs_since(scratch, n1))
    delete(scratch, pr["id"])
    urllib.request.urlopen(urllib.request.Request(scratch.url + f"/studio/api/characters/{c['id']}", method="DELETE")).read()


def test_keyframes_and_first_frames_are_one_picture(scratch):
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    pr["shots"][0]["keyframe"] = "studio/one.png"
    scratch.post("/studio/api/projects", pr)
    r = scratch.post(f"/studio/api/projects/{pr['id']}/to_board", {"private": False})[1]
    board = next(b for b in scratch.get("/studio/api/boards") if b["id"] == r["board"])
    assert board["shots"][0]["image"] == "studio/one.png"                   # keyframe -> first frame
    board["shots"][0]["takes"] = [{"output": {"filename": "t.mp4", "subfolder": "Videos"}}]
    board["shots"][1]["image"] = "studio/from-board.png"                      # first frame changed on the board
    scratch.post("/studio/api/boards", board)
    pr = next(x for x in scratch.get("/studio/api/projects") if x["id"] == pr["id"])
    pr["shots"][0]["keyframe"] = "studio/two.png"                             # a new keyframe, though the shot has a take
    scratch.post("/studio/api/projects", pr)
    scratch.post(f"/studio/api/projects/{pr['id']}/to_board", {"private": False})
    board = next(b for b in scratch.get("/studio/api/boards") if b["id"] == r["board"])
    pr = next(x for x in scratch.get("/studio/api/projects") if x["id"] == pr["id"])
    assert board["shots"][0]["image"] == "studio/two.png" and board["shots"][0]["takes"]
    assert pr["shots"][1]["keyframe"] == "studio/from-board.png" == board["shots"][1]["image"]   # first frame -> keyframe
    delete(scratch, pr["id"])


@pytest.mark.skipif(chrome() is None, reason=WHY_NOT)
def test_the_character_sheet_keeps_the_face(scratch, tmp_path):
    """Saving the sheet keeps the face picture (the Face field is the face's features, not the picture)."""
    import shutil
    os.makedirs(os.path.join(scratch.run, "input", "studio"), exist_ok=True)
    shutil.copy(os.path.join(REPO, "sandbox", "samples", "ray.jpg"), os.path.join(scratch.run, "input", "studio", "ray.jpg"))
    c = scratch.post("/studio/api/characters", dict(SHEET, face="studio/ray.jpg"))[1]

    async def run(page):
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/characters"})
        await page.until("document.querySelectorAll('.ccard [data-a=edit]').length > 0")
        await page.js("[...document.querySelectorAll('.ccard')].find(x => x.textContent.includes('Ray Mercer')).querySelector('[data-a=edit]').click()")
        await page.until("document.querySelector('.chard [data-f=features]')")
        shown = await page.js("({features: document.querySelector('[data-f=features]').value, gets: document.querySelector('#cdesc').textContent})")
        await page.js("(() => { const i = document.querySelector('[data-f=eyes]'); i.value = 'steel grey'; i.dispatchEvent(new Event('input')); document.querySelector('.chard [data-save]').click(); })()")
        await page.until("!document.querySelector('.chard')")
        return shown

    shown, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert shown == {"features": "square jaw", "gets": "Ray Mercer: " + SHEET_TEXT}
    saved = next(x for x in scratch.get("/studio/api/characters") if x["id"] == c["id"])
    assert saved["face"] == "studio/ray.jpg" and saved["eyes"] == "steel grey" and saved["features"] == "square jaw"
    urllib.request.urlopen(urllib.request.Request(scratch.url + f"/studio/api/characters/{c['id']}", method="DELETE")).read()


# ------------------------------------------------------------------ found in the bug hunt
def test_characters_come_with_their_description(scratch):
    c = scratch.post("/studio/api/characters", dict(SHEET, description="stale, not stored"))[1]
    got = next(x for x in scratch.get("/studio/api/characters") if x["id"] == c["id"])
    assert got["description"] == SHEET_TEXT
    import glob
    stored = json.load(open(glob.glob(os.path.join(scratch.run, "user", "default", "studio_data", "characters.json"))[0]))
    assert "description" not in next(x for x in stored if x["id"] == c["id"])
    urllib.request.urlopen(urllib.request.Request(scratch.url + f"/studio/api/characters/{c['id']}", method="DELETE")).read()


def _mcp(box, tool, args):
    import subprocess
    out = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "studio-call.py"), tool, json.dumps(args), "--port", str(box.port)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_claude_changes_a_screenplay_projects_shots_without_duplicating_them(scratch):
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    first, second = pr["shots"][0], pr["shots"][1]
    _mcp(scratch, "studio_project_update", {"id": pr["id"], "shots": [{"id": first["id"], "camera": "drone", "seconds": 20},
                                                                      {"title": "Insert: the folded letter", "action": "The letter, yellowed at the folds.", "camera": "close-up"}]})
    p = next(x for x in scratch.get("/studio/api/projects") if x["id"] == pr["id"])
    assert len(p["shots"]) == len(pr["shots"]) + 1 and p["shots"][1]["id"] == second["id"]
    # the next save of the screenplay keeps Claude's changes, with no duplicate shots
    r = scratch.post("/studio/api/screenplay/breakdown", {"text": p["screenplay"]["text"], "scenes": p["scenes"], "shots": p["shots"]})[1]
    assert len(r["shots"]) == len(pr["shots"]) + 1
    assert (r["shots"][0]["camera"], r["shots"][0]["seconds"], r["shots"][0]["id"]) == ("drone", 20, first["id"])
    added = r["shots"][-1]
    assert added["manual"] and added["action"] == "The letter, yellowed at the folds." and added["scene"] == p["scenes"][0]["id"]
    delete(scratch, pr["id"])


def test_a_deleted_projects_storyboard_is_still_there(scratch, tmp_path):
    pr = upload(scratch, FOUNTAIN)[1]["project"]
    r = scratch.post(f"/studio/api/projects/{pr['id']}/to_board", {"private": False})[1]
    delete(scratch, pr["id"], board=False)                 # the project goes, its storyboard stays

    async def run(page):
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects"})
        listed = await page.until("document.querySelector('#oboards') && [...document.querySelectorAll('#oboards .pcard b')].map(b => b.textContent)")
        await page.js(f"localStorage.setItem('studio.board', JSON.stringify('{r['board']}'))")
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/storyboard"})
        opened = await page.until("document.querySelectorAll('.shots .shot').length && location.hash + ' ' + document.querySelector('#bname').textContent")
        return listed, opened

    if chrome() is None:
        pytest.skip(WHY_NOT)
    (listed, opened), errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert "The Glass Harbour" in listed and opened == "#/storyboard The Glass Harbour"
    urllib.request.urlopen(urllib.request.Request(scratch.url + f"/studio/api/boards/{r['board']}", method="DELETE"), timeout=30).read()


@pytest.mark.skipif(chrome() is None, reason=WHY_NOT)
def test_shots_from_before_a_screenplay_are_yours_to_change(scratch, tmp_path):
    """A project made from an idea (by Claude, say) has shots of no scene: their text shows and can be edited, and they can
    be moved and removed, here and on the storyboard. Setups only merge into an earlier setup of the screenplay."""
    shots = [{"id": f"s{k}", "title": f"Shot {k}", "action": f"A lighthouse, take {k}.", "camera": "static", "seconds": 5} for k in range(3)]
    pr = scratch.post("/studio/api/projects", {"title": "From an idea", "shots": shots, "step": "scenes", "cast": []})[1]
    withsp = upload(scratch, FOUNTAIN)[1]["project"]
    withsp["shots"].insert(0, {"id": "m0", "scene": withsp["scenes"][0]["id"], "after": "^", "manual": True, "title": "Added", "action": "A gull.",
                               "lines": [], "beats": [], "camera": "static", "seconds": 3})
    withsp["step"] = "scenes"
    scratch.post("/studio/api/projects", withsp)

    async def run(page):
        out = {}
        await page.js("window.confirm = () => true")
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{pr['id']}"})
        await page.until("document.querySelectorAll('.shotrow').length === 3")
        out["texts"] = await page.js("[...document.querySelectorAll('.shotrow .sr-act')].map(t => t.value)")
        out["buttons"] = await page.js("[...document.querySelectorAll('.shotrow')].map(r => [...r.querySelectorAll('.sr-btns button')].map(b => b.title.split(':')[0]))")
        await page.js("window.confirm = () => true; document.querySelectorAll('.shotrow')[1].querySelector('[data-x]').click()")
        await page.until("document.querySelectorAll('.shotrow').length === 2")
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{withsp['id']}"})
        await page.until("document.querySelectorAll('.shotrow').length >= 9")
        out["first_setup"] = await page.js("[...document.querySelectorAll('.shotrow')[1].querySelectorAll('.sr-btns button')].map(b => b.title)")
        return out

    out, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert out["texts"] == ["A lighthouse, take 0.", "A lighthouse, take 1.", "A lighthouse, take 2."]
    assert out["buttons"] == [["Move up", "Move down", "Remove"]] * 3
    assert [s["id"] for s in next(x for x in scratch.get("/studio/api/projects") if x["id"] == pr["id"])["shots"]] == ["s0", "s2"]
    assert out["first_setup"] == []                        # the added shot before it isn't a setup to merge into
    for x in (pr, withsp):
        delete(scratch, x["id"])


@pytest.mark.skipif(chrome() is None, reason=WHY_NOT)
def test_an_edit_is_saved_before_the_page_changes(scratch, tmp_path):
    pr = upload(scratch, FOUNTAIN)[1]["project"]

    async def run(page):
        await page.call("Page.navigate", {"url": f"{scratch.url}/studio/#/projects/{pr['id']}"})
        await page.until("document.querySelectorAll('.sp-ed .sp-b').length > 100 && document.querySelector('#sp-save').textContent === ''")
        await page.js("""(() => { const b = [...document.querySelectorAll('.sp-ed .sp-b')].pop(); const r = document.createRange();
            r.selectNodeContents(b); r.collapse(false); const s = getSelection(); s.removeAllRanges(); s.addRange(r); b.closest('.sp-ed').focus(); })()""")
        await page.type(" Then silence.")
        await page.js(f"({GO})('pre', 'Scenes & shots')")                            # at once, before the save's 0.8 s
        return await page.until("document.querySelectorAll('.shotrow').length && document.querySelector('.shotrow:last-child').textContent.includes('Then silence.')")

    shown, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert not errors, errors
    assert shown
    p = next(x for x in scratch.get("/studio/api/projects") if x["id"] == pr["id"])
    assert p["screenplay"]["text"].rstrip().endswith("FADE OUT. Then silence.")
    delete(scratch, pr["id"])
