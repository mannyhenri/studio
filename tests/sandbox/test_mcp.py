"""Studio's MCP server end to end, on a sandbox of its own: spoken to through the real connector (studio_mcp.py over stdio,
as Claude Desktop and Claude Code start it) and over HTTP (/studio/mcp, served by Studio):
  - every tool is listed with a schema that holds together; unknown tools and bad calls answer in words
  - a story becomes a film with nothing but MCP calls: the screenplay written as Fountain, its breakdown, the cast made from
    it (a character sheet filled in, a reference picture that becomes the face), the locations (a look, a picture), the
    storyboard's frames (the face and the location's picture as references), the takes (drafted, finished, picked), the
    score, the voices, the film; and each step's job is the one the page would have sent to ComfyUI
  - the screenplay changed: a new place and a new part are a new location and character, the old text is a draft, a part
    and a place gone go if nobody touched them; a screenplay file imported; exported as a PDF
  - the AI tools and the gallery through MCP; the page never saves over an AI's change (409) and takes it in by itself"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

import pytest

from conftest import REPO, Sandbox
from test_screenplay_import import _port, _with_chrome, chrome
from find_chrome import WHY_NOT  # noqa: E402

pytestmark = pytest.mark.mcp

MCP = os.path.join(REPO, "ComfyUI-Studio", "studio_mcp.py")
STORY = """Title: The Last Keeper

EXT. NORTHPOINT LIGHTHOUSE - DUSK

Storm clouds bruise the sky. MARA VOSS (60s, weathered, oilskin coat, silver braid) climbs the iron stairs.

MARA
Not tonight. Not with the lamp out.

INT. LAMP ROOM - NIGHT

The great lens sits dark. TEO (12, soaked, too-big raincoat) pokes his head through the trapdoor.

TEO
Gran? The boats are still out.

MARA
Then hold the light steady.

EXT. NORTHPOINT LIGHTHOUSE - NIGHT

The beam sweeps across black water.
"""


class Connector:
    """studio_mcp.py over stdio, started as an assistant starts it."""

    def __init__(self, box):
        self.box, self.n = box, 0
        self.p = subprocess.Popen([sys.executable, MCP], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=dict(os.environ, STUDIO_URL=box.url, STUDIO_MCP_POLL="0.25", STUDIO_FFMPEG=os.environ.get("FFMPEG_PATH") or "ffmpeg"))
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}})
        assert init["result"]["serverInfo"]["name"] == "studio"
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")

    def rpc(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            assert line, "the connector stopped: " + self.p.stderr.read()[-2000:]
            m = json.loads(line)
            if m.get("id") == self.n:
                return m

    def call(self, _name, ok=True, **args):
        """A tool's answer: its text parsed as JSON when it is, and its pictures."""
        res = self.rpc("tools/call", {"name": _name, "arguments": args})["result"]
        assert bool(res.get("isError")) != ok, (_name, res["content"][0].get("text", "")[:1500])
        texts = [c["text"] for c in res["content"] if c["type"] == "text"]
        images = [c for c in res["content"] if c["type"] == "image"]
        try:
            data = json.loads(texts[0]) if texts else None
        except ValueError:
            data = texts[0]
        return data, images

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    s = Sandbox(tmp_path_factory.mktemp("mcp"), "demo")
    yield s
    s.stop()


@pytest.fixture(scope="module")
def mcp(box):
    c = Connector(box)
    yield c
    c.close()


def comfy_jobs(box):
    """The jobs the stand-in ComfyUI ran, in order (their files are named by their number)."""
    out = []
    for f in sorted(os.listdir(box.jobs)) if os.path.isdir(box.jobs) else []:
        if f.endswith(".json") and not f.startswith("refused"):
            with open(os.path.join(box.jobs, f), encoding="utf-8") as fh:
                out.append(json.load(fh))
    return out


def texts_of(job):
    return [n["inputs"].get("value") for n in job["prompt"].values() if n["class_type"].startswith("PrimitiveString") and isinstance(n["inputs"].get("value"), str)]


def images_of(job):
    return [n["inputs"]["image"] for n in job["prompt"].values() if n["class_type"] == "LoadImage"]


# ---------------------------------------------------------------- the tools
WORKFLOW = ["studio_project_new", "studio_screenplay_get", "studio_screenplay_set", "studio_screenplay_export", "studio_breakdown", "studio_shot_update",
            "studio_cast", "studio_cast_role", "studio_character_save", "studio_character_picture", "studio_character_delete", "studio_locations",
            "studio_location_update", "studio_location_picture", "studio_storyboard_frames", "studio_takes_draft", "studio_takes", "studio_take_pick",
            "studio_project_music", "studio_project_voices", "studio_project_film", "studio_project_delete", "studio_ai_tools", "studio_ai_tool_run",
            "studio_ai_tool_jobs", "studio_view", "studio_file_info", "studio_gallery_action", "studio_ai_review", "studio_ai_prompt", "studio_ai_write",
            "studio_reel", "studio_previz_add", "studio_status", "studio_job",
            # and the connector's earlier tools, unchanged
            "studio_overview", "studio_list_apps", "studio_generate", "studio_wait", "studio_recent", "studio_queue", "studio_music", "studio_voice",
            "studio_list_characters", "studio_list_previz_sets", "studio_list_boards", "studio_create_board", "studio_attach", "studio_projects"]


def test_every_tool_is_listed_with_a_schema_that_holds_together(mcp):
    tools = mcp.rpc("tools/list")["result"]["tools"]
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names)) and set(WORKFLOW) <= set(names), set(WORKFLOW) - set(names)
    for t in tools:
        sch = t["inputSchema"]
        assert t["description"].strip() and sch["type"] == "object", t["name"]
        assert set(sch.get("required") or []) <= set(sch.get("properties") or {}), t["name"]
    m = mcp.rpc("tools/call", {"name": "studio_nope", "arguments": {}})
    assert m["result"]["isError"] and "Unknown tool" in m["result"]["content"][0]["text"]
    assert mcp.rpc("nope/nope")["error"]["code"] == -32601
    data, _ = mcp.call("studio_overview")
    assert "Making a film" in open(MCP, encoding="utf-8").read() and data is not None


# ---------------------------------------------------------------- a story becomes a film
def test_a_story_becomes_a_film_through_mcp(mcp, box, tmp_path):
    n0 = len(comfy_jobs(box))
    made, _ = mcp.call("studio_project_new", screenplay=STORY, tone="Cinematic drama")
    pr = made["project"]
    assert (pr["title"], pr["scenes"], pr["shots"]) == ("The Last Keeper", 3, 3)
    assert made["followed"]["locations_added"] == ["Northpoint Lighthouse", "Lamp Room"]
    assert made["followed"]["characters_added"] == ["Mara Voss", "Teo"]
    bd, _ = mcp.call("studio_breakdown", project="The Last Keeper")
    assert [s["heading"] for s in bd] == ["EXT. NORTHPOINT LIGHTHOUSE - DUSK", "INT. LAMP ROOM - NIGHT", "EXT. NORTHPOINT LIGHTHOUSE - NIGHT"]
    assert bd[1]["shots"][0]["code"] == "2A" and "TEO: Gran? The boats are still out." in bd[1]["shots"][0]["lines"]
    mcp.call("studio_shot_update", project="last keeper", shot="3A", camera="crane up", seconds=6)
    cast, _ = mcp.call("studio_cast", project=pr["id"])
    mara = cast[0]["played_by"]
    assert (cast[0]["part"], mara["name"], mara["sheet"]) == ("MARA", "Mara Voss", {"age": "60s", "look": "weathered, oilskin coat, silver braid"})
    # the character sheet, and a reference picture that becomes her face
    mcp.call("studio_character_save", character="Mara Voss", age="64", gender="woman", hair="long silver braid", wardrobe="yellow oilskin coat", look="")
    pic, images = mcp.call("studio_character_picture", character="Mara")
    assert images and images[0]["mimeType"] == "image/jpeg" and "Mara Voss: " in pic
    cast, _ = mcp.call("studio_cast", project=pr["id"])
    assert cast[0]["played_by"]["face"] and cast[0]["played_by"]["description"].startswith("64-year-old woman")
    # the locations: a look, a picture
    mcp.call("studio_location_update", project=pr["id"], location="lamp room", look="glass walls, brass fittings, a giant Fresnel lens",
             lighting="cold blue storm light", time="night")
    _, images = mcp.call("studio_location_picture", project=pr["id"], location="Lamp Room")
    assert images
    locs, _ = mcp.call("studio_locations", project=pr["id"])
    assert locs[1]["picture"] and locs[1]["look"].startswith("glass walls")
    # the storyboard: a frame per shot, with the page's references
    frames, images = mcp.call("studio_storyboard_frames", project=pr["id"])
    assert frames["made"] == 3 and len(images) == 3
    # the takes: drafted from the frames, finished, picked
    takes, _ = mcp.call("studio_takes_draft", project=pr["id"], count=2, wait=True, timeout=120)
    assert [[t["status"] for t in s["takes"]] for s in takes["shots"]] == [["done", "done"]] * 3
    assert all(t["path"] and os.path.isfile(t["path"]) for s in takes["shots"] for t in s["takes"])
    picked, _ = mcp.call("studio_take_pick", project=pr["id"], shot="2A", take=1)
    assert [s["pick"] for s in picked["shots"]] == [0, 1, 0]
    # the sound and the film
    music, _ = mcp.call("studio_project_music", project=pr["id"], style="brooding strings, solo cello")
    assert music["subfolder"] == "Audio/Music" and os.path.isfile(music["path"])
    voices, _ = mcp.call("studio_project_voices", project=pr["id"])
    assert voices == {"lines": 3}
    film, _ = mcp.call("studio_project_film", project=pr["id"], music="project", end="The End")
    assert (film["shots"], film["of"], film["subfolder"]) == (3, 3, "Videos/Films") and os.path.isfile(film["path"])
    _, images = mcp.call("studio_view", filename=film["filename"], subfolder=film["subfolder"])
    assert images and images[0]["mimeType"] == "image/webp"
    # what reached ComfyUI is what the page would have sent
    jobs = [j for j in comfy_jobs(box)[n0:] if not (j.get("extra_data") or {}).get("studio_prompt_step")]
    portrait = next(j for j in jobs if j["label"] == "Reference picture: Mara Voss")
    assert any(t.startswith("Character reference portrait of Mara Voss: 64-year-old woman") for t in texts_of(portrait))
    place = next(j for j in jobs if j["label"] == "Location: Lamp Room")
    assert any("Lamp Room: glass walls, brass fittings" in t and "cold blue storm light. night." in t for t in texts_of(place)), texts_of(place)
    kf = sorted((j for j in jobs if j["label"].startswith("Project keyframe")), key=lambda j: j["label"])
    refs = [images_of(j) for j in kf]
    assert refs[0] and refs[1] and not refs[2], "Mara's face guides the shots she's in; nobody's in 3A (and its place has no picture)"
    takes_jobs = [j for j in jobs if j["label"].startswith("Take ")]
    assert len(takes_jobs) == 6 and all(images_of(j) for j in takes_jobs), "every take starts from its shot's frame"
    assert [j for j in jobs if j["label"] == "Voice: 2A"] and [j for j in jobs if j["label"].startswith("Music:")]
    assert {j["status"] for j in jobs} == {"success"}
    # the project, as the page will open it
    pj = box.get("/studio/api/projects")[0]
    assert (pj["stage"], pj["by"], bool(pj["music_file"])) == ("Film", "ai", True)
    assert next(s for s in pj["shots"] if s["camera"] == "crane up")["seconds"] == 6
    board = next(b for b in box.get("/studio/api/boards") if b["id"] == pj["board"])
    assert board["film"]["filename"] == film["filename"] and [s["code"] for s in board["shots"]] == ["1A", "2A", "3A"]


def test_the_screenplay_changes_and_everything_follows_it(mcp, box, tmp_path):
    made, _ = mcp.call("studio_project_new", title="Changes", screenplay=STORY.replace("The Last Keeper", "Changes"))
    pid = made["project"]["id"]
    text, _ = mcp.call("studio_screenplay_get", project=pid)
    assert "MARA" in text and text.startswith("Title: Changes")
    more = STORY.replace("The Last Keeper", "Changes") + "\nEXT. HARBOUR - NIGHT\n\nA FISHERMAN (50s, beard, yellow hood) waves a torch.\n\nFISHERMAN\nOver here!\n"
    out, _ = mcp.call("studio_screenplay_set", project=pid, text=more, draft_name="First draft")
    assert out["followed"]["locations_added"] == ["Harbour"] and out["followed"]["characters_added"] == ["Fisherman"]
    pr = next(p for p in box.get("/studio/api/projects") if p["id"] == pid)
    assert pr["drafts"][-1]["name"] == "First draft" and "HARBOUR" not in pr["drafts"][-1]["text"]
    # Teo's part written out and the harbour gone again: the untouched character and location made for this project go; Teo
    # stays (the first project made him: cast here too, he's nobody's typo), and so does Mara (worked on)
    mcp.call("studio_character_save", character="Mara Voss", hair="silver")
    less = STORY.replace("The Last Keeper", "Changes").replace("TEO (12, soaked, too-big raincoat) pokes his head through the trapdoor.\n\nTEO\nGran? The boats are still out.\n", "")
    out, _ = mcp.call("studio_screenplay_set", project=pid, text=less)
    assert out["followed"]["locations_removed"] == ["Harbour"] and out["followed"]["characters_removed"] == ["Fisherman"]
    names = [c["name"] for c in box.get("/studio/api/characters")]
    assert "Fisherman" not in names and "Teo" in names and "Mara Voss" in names
    # renaming a location renames it in the scene headings
    r, _ = mcp.call("studio_location_update", project=pid, location="Lamp Room", name="Lantern Gallery")
    assert r["renamed_in_headings"] == 1
    text, _ = mcp.call("studio_screenplay_get", project=pid)
    assert "INT. LANTERN GALLERY - NIGHT" in text and "LAMP ROOM" not in text
    # exported as a standard PDF; deleted with what was made for it and never touched
    _, _ = mcp.call("studio_screenplay_export", project=pid, format="pdf", path=str(tmp_path))
    assert open(tmp_path / "Changes.pdf", "rb").read(5) == b"%PDF-"
    mcp.call("studio_project_delete", project=pid)
    assert pid not in [p["id"] for p in box.get("/studio/api/projects")]


def test_a_screenplay_file_is_imported(mcp, box):
    made, _ = mcp.call("studio_project_new", screenplay_path=os.path.join(REPO, "screenplays", "The Glass Harbour.pdf"), tone="Film noir")
    assert (made["imported"]["scenes"], made["imported"]["shots"], made["imported"]["characters"]) == (1, 8, ["RAY", "JUNE"])
    assert made["followed"]["characters_added"] and made["project"]["locations"]
    pr = next(p for p in box.get("/studio/api/projects") if p["id"] == made["project"]["id"])
    assert pr["tone"] == "Film noir" and pr["castMap"] and pr["locations"]
    data, _ = mcp.call("studio_project_new", ok=False, screenplay_path="/nowhere/at/all.pdf")
    assert "No such file" in data


def test_the_ai_tools_and_the_gallery_through_mcp(mcp, box):
    tools, _ = mcp.call("studio_ai_tools")
    assert [t["id"] for t in tools] == ["depth", "slowmo", "stems", "subtitles", "cut", "master"]
    job, _ = mcp.call("studio_ai_tool_run", tool="One-Click Master", path=os.path.join(box.run, "input", "song.wav"), options={"target": -16})
    assert job["status"] == "done" and abs(job["stats"]["integrated_lufs"] + 16) <= 0.5 and os.path.isfile(job["files"][0])
    job, _ = mcp.call("studio_ai_tool_run", tool="slowmo", filename="diner-wide.mp4", subfolder="Videos", start_frame=12, end_frame=36)
    assert job["status"] == "done" and "frames 12–35" in job["note"]
    recent, _ = mcp.call("studio_recent", kind="image", limit=3)
    assert recent
    info, _ = mcp.call("studio_file_info", filename="diner.jpg", subfolder="Images")
    assert info["size"] == [1024, 572]
    _, images = mcp.call("studio_view", path=os.path.join(box.run, "output", "Images", "diner.jpg"))
    assert images
    data, _ = mcp.call("studio_view", ok=False, path="/nowhere.png")
    assert "Give a picture" in data
    status, _ = mcp.call("studio_status")
    assert "rendering" in status and "waiting" in status


# ---------------------------------------------------------------- over HTTP, and the page
def http_rpc(box, msg, origin=None):
    req = urllib.request.Request(box.url + "/studio/mcp", data=json.dumps(msg).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_the_same_tools_over_http(box):
    code, r = http_rpc(box, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
    assert code == 200 and r["result"]["serverInfo"]["name"] == "studio" and "capabilities" in r["result"]
    assert http_rpc(box, {"jsonrpc": "2.0", "method": "notifications/initialized"})[0] == 202
    code, r = http_rpc(box, [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                             {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "studio_list_apps", "arguments": {"app": "Image"}}}])
    assert code == 200 and {t["name"] for t in r[0]["result"]["tools"]} >= set(WORKFLOW)
    assert json.loads(r[1]["result"]["content"][0]["text"])[0]["app"] == "Image"
    assert http_rpc(box, {"jsonrpc": "2.0", "id": 4, "method": "ping"}, origin="http://evil.example")[0] == 403
    assert http_rpc(box, {"jsonrpc": "2.0", "id": 5, "method": "ping"}, origin=box.url)[0] == 200
    try:
        urllib.request.urlopen(box.url + "/studio/mcp", timeout=30)
        raise AssertionError("GET must not open a stream")
    except urllib.error.HTTPError as e:
        assert e.code == 405


def test_a_page_save_never_undoes_an_ai_change(mcp, box):
    made, _ = mcp.call("studio_project_new", title="Guarded", screenplay=STORY)
    pid = made["project"]["id"]
    page = next(p for p in box.get("/studio/api/projects") if p["id"] == pid)          # what a page has open
    mcp.call("studio_location_update", project=pid, location="Lamp Room", look="brass")
    code, r = box.post("/studio/api/projects", dict(page, tone="Anime"))
    assert code == 409 and r["error"] == "changed" and r["project"]["locations"][1]["look"] == "brass"
    code, r = box.post("/studio/api/projects", dict(r["project"], tone="Anime"))        # having taken it in
    assert code == 200 and r["by"] == "page"
    stamps = box.get("/studio/api/stamps")
    assert stamps["projects"][pid][1] == "page"


@pytest.mark.skipif(chrome() is None, reason=WHY_NOT)
def test_the_page_shows_an_ai_change_by_itself(mcp, box, tmp_path):
    made, _ = mcp.call("studio_project_new", title="Watched", screenplay=STORY)
    pid = made["project"]["id"]

    async def run(page):
        await page.js(f"location.href = '{box.url}/studio/#/projects/{pid}/locations'")
        await page.until("!!document.querySelector('.loc-grid .loc-card')")
        for step in ("screenplay", "scenes", "cast", "storyboard", "takes", "sound", "film", "locations"):
            await page.js(f"location.hash = '#/projects/{pid}/{step}'")
            await asyncio.sleep(0.8)
        await page.until("!!document.querySelector('.loc-grid .loc-card')")
        await asyncio.to_thread(mcp.call, "studio_location_update", project=pid, location="Northpoint Lighthouse", description="A white tower on black basalt.")
        t0 = time.time()
        seen = await page.until("document.querySelector('.loc-grid').textContent.includes('A white tower on black basalt.')", tries=60)
        return bool(seen), time.time() - t0
    (seen, secs), errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    assert seen and secs < 12, secs
    assert not page_errors(errors), errors


def page_errors(errors):
    """A page's errors, less the save guard's own answer: a save of the page's that met an AI's change is refused (409) and
    the page takes the change in. That's the guard working (a page open on a project re-saves it as its steps open)."""
    return [e for e in errors if not re.match(r"HTTP 409 \S+/studio/api/(projects|boards)$", e)]


@pytest.mark.skipif(chrome() is None, reason=WHY_NOT)
def test_your_edit_right_after_an_ai_change_never_undoes_it(mcp, box, tmp_path):
    """You save a location's form just after an AI changed the same project. However the race goes (the page had already
    taken the AI's change in, or its save is refused), the AI's change is still there; and if your edit was refused, the
    page says so (make it again) instead of dropping it silently."""
    made, _ = mcp.call("studio_project_new", title="Raced", screenplay=STORY)
    pid = made["project"]["id"]

    async def run(page):
        await page.js(f"location.href = '{box.url}/studio/#/projects/{pid}/locations'")
        await page.until("!!document.querySelector('.loc-grid .loc-card')")
        await asyncio.sleep(1.0)
        await asyncio.to_thread(mcp.call, "studio_location_update", project=pid, location="Lamp Room", look="brass and salt-stained glass")
        await page.js("""[...document.querySelectorAll('.loc-card')].find(c => c.textContent.includes('Northpoint')).querySelector('[data-edit]').click()""")
        await page.until("!!document.querySelector('[data-k=description]')")
        await page.js("""(() => { const t = document.querySelector('[data-k=description]'); t.value = 'Edited on the page.';
            t.dispatchEvent(new Event('input', {bubbles: true})); document.querySelector('#lsave').click(); })()""")
        await asyncio.sleep(3)
        return await page.js("[...document.querySelectorAll('#toast .t')].map(t => t.textContent).join(' | ')")
    toasts, errors = asyncio.run(_with_chrome(chrome(), _port(), str(tmp_path / "chrome"), run))
    pr = next(p for p in box.get("/studio/api/projects") if p["id"] == pid)
    looks = {x["name"]: x for x in pr["locations"]}
    assert looks["LAMP ROOM"]["look"] == "brass and salt-stained glass", "the AI's change survived the page's save"
    assert looks["NORTHPOINT LIGHTHOUSE"]["description"] == "Edited on the page." or "Your last change wasn't saved: make it again" in toasts, \
        (looks["NORTHPOINT LIGHTHOUSE"]["description"], toasts)
    assert not page_errors(errors), errors
