"""The screenplay app's tools on the server (ComfyUI-Studio/screenplay.py, projects.py): Final Draft (.fdx) in and out, the
screenplay as plain text on the standard page, revision marks in the PDF, where each element starts in the Fountain text
(the editor puts the breakdown's shots beside it), and the storyboard keeping the takes of a shot cut from the screenplay."""
import importlib.util
import os
import re
import sys
import types
import zlib

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FOUNTAIN = os.path.join(REPO, "screenplays", "the_glass_harbour.fountain")

_spec = importlib.util.spec_from_file_location("studio_screenplay_tools", os.path.join(REPO, "ComfyUI-Studio", "screenplay.py"))
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)

SCRIPT = """Title: The Test
Credit: Written by
Author: A. Writer
Draft date: 05/01/2026

INT. DINER - NIGHT #12A#

Rain on the **glass**. A _sign_ *flickers*.

RAY
(quietly)
Stay.

JUNE ^
Go.

===

@McCLANE
Yippee.

> THE END <

# Act two

= A synopsis.

CUT TO:
"""


def key(els):
    return [(e["type"], e["text"].strip(), bool(e.get("dual")), e.get("number", "")) for e in els if e["type"] not in ("section", "synopsis")]


def test_where_each_element_starts_is_the_line_the_server_reads_it_on():
    for text in (open(FOUNTAIN, encoding="utf-8").read(), SCRIPT):
        p = sp.parse(text)
        starts = []
        out = sp.to_fountain(p["title_page"], p["elements"], starts)
        assert [e["line"] for e in sp.parse(out)["elements"]] == starts


def test_final_draft_round_trip_keeps_the_screenplay():
    for text in (open(FOUNTAIN, encoding="utf-8").read(), SCRIPT):
        p = sp.parse(text)
        fdx = sp.to_fdx(p["title_page"], p["elements"])
        assert fdx.startswith(b'<?xml version="1.0" encoding="UTF-8"') and b"<FinalDraft" in fdx
        back = sp.parse(sp.fdx_to_fountain(fdx))
        assert key(back["elements"]) == key(p["elements"])
        assert dict(back["title_page"])["Title"] == dict(p["title_page"])["Title"]


def test_final_draft_paragraphs_styles_dual_dialogue_and_title_page():
    p = sp.parse(SCRIPT)
    fdx = sp.to_fdx(p["title_page"], p["elements"]).decode()
    assert '<Paragraph Type="Scene Heading" Number="12A"><Text>INT. DINER - NIGHT</Text></Paragraph>' in fdx
    assert '<Text Style="Bold">glass</Text>' in fdx and '<Text Style="Underline">sign</Text>' in fdx and '<Text Style="Italic">flickers</Text>' in fdx
    assert "<DualDialogue>" in fdx and fdx.index("<DualDialogue>") < fdx.index(">RAY<") < fdx.index(">JUNE<") < fdx.index("</DualDialogue>")
    assert 'StartsNewPage="Yes"' in fdx                                   # === a page break
    assert '<Paragraph Type="Action" Alignment="Center"><Text>THE END</Text>' in fdx
    assert "Act two" not in fdx and "A synopsis" not in fdx              # sections and synopses don't print
    tp = fdx[fdx.index("<TitlePage>"):]
    assert "The Test" in tp and "A. Writer" in tp and "05/01/2026" in tp
    back = sp.fdx_to_fountain(fdx.encode())
    assert "@McCLANE" in back and "**glass**" in back and "JUNE ^" in back and "INT. DINER - NIGHT #12A#" in back


def test_a_final_draft_file_from_elsewhere():
    fdx = b"""<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<FinalDraft DocumentType="Script" Template="No" Version="4">
  <Content>
    <Paragraph Type="Scene Heading"><SceneProperties Length="1/8"/><Text>EXT. PIER - DAWN</Text></Paragraph>
    <Paragraph Type="Action"><Text>Gulls. </Text><Text Style="Bold+Underline">Silence.</Text></Paragraph>
    <Paragraph Type="Shot"><Text>CLOSE ON THE WATER</Text></Paragraph>
    <Paragraph Type="Character"><Text>RAY</Text></Paragraph>
    <Paragraph Type="Dialogue"><Text>Cold.</Text></Paragraph>
    <Paragraph Type="Transition"><Text>CUT TO:</Text></Paragraph>
  </Content>
  <TitlePage><Content>
    <Paragraph Alignment="Center"><Text>Pier</Text></Paragraph>
    <Paragraph Alignment="Center"><Text>written by</Text></Paragraph>
    <Paragraph Alignment="Center"><Text>Sam Lee</Text></Paragraph>
    <Paragraph Alignment="Left"><Text>sam@example.com</Text></Paragraph>
  </Content></TitlePage>
</FinalDraft>"""
    p = sp.parse(sp.fdx_to_fountain(fdx))
    assert [(e["type"], e["text"]) for e in p["elements"]] == [("scene", "EXT. PIER - DAWN"), ("action", "Gulls. **_Silence._**"), ("action", "CLOSE ON THE WATER"),
                                                              ("character", "RAY"), ("dialogue", "Cold."), ("transition", "CUT TO:")]
    vis, sty = sp.styled(p["elements"][1]["text"])
    assert vis == "Gulls. Silence." and set(sty[7:]) == {2 | 4}              # bold and underlined, as Final Draft had it
    assert p["title_page"] == [("Title", "Pier"), ("Credit", "written by"), ("Author", "Sam Lee"), ("Contact", "sam@example.com")]


@pytest.mark.parametrize("data, why", [(b"<html></html>", "isn't a Final Draft"), (b"not xml", "can't be read|isn't a Final Draft"),
                                       (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><FinalDraft/>', "entities")])
def test_what_isnt_a_final_draft_file_is_refused(data, why):
    with pytest.raises(sp.ScreenplayError, match=why):
        sp.fdx_to_fountain(data)


def test_plain_text_is_laid_out_as_the_page():
    p = sp.parse(open(FOUNTAIN, encoding="utf-8").read())
    txt = sp.render_text(p["title_page"], p["elements"])
    pages = txt.split("\f")
    assert len(pages) == 1 + p["pages"]                                   # the title page, then the script's pages
    assert "THE GLASS HARBOUR" in pages[0] and "Alex Rivera" in pages[0]
    assert re.search(r"^\s+2\.$", pages[2], re.M)                        # a page number from page 2
    body = pages[1].split("\n")
    scene = next(ln for ln in body if "INT. THE GLASS HARBOUR DINER" in ln)
    assert scene.startswith("1    INT.") and scene.rstrip().endswith("1")   # scene numbers in both margins
    cue = next(ln for ln in pages[2].split("\n") if ln.strip() == "RAY")
    assert cue.index("RAY") == 27                                         # a cue at 3.7" (1" margin: column 27)
    for page in pages[1:]:
        assert len(page.strip("\n").split("\n")) <= 2 + 55                # the number line, a blank, 55 lines


def _pdf_text(pdf):
    return b"".join(zlib.decompress(m) for m in re.findall(rb"stream\n(.*?)\nendstream", pdf, re.S))


def test_revision_marks_on_the_changed_lines():
    text = open(FOUNTAIN, encoding="utf-8").read()
    p = sp.parse(text)
    now = [dict(e) for e in p["elements"]]
    k = next(i for i, e in enumerate(now) if e["type"] == "dialogue")
    now[k]["text"] = "Something else entirely."
    now.insert(k + 1, {"type": "action", "text": "A new beat."})
    assert sp.revised_elements(text, now) == {k, k + 1}
    plain = _pdf_text(sp.render_pdf(p["title_page"], now, "T"))
    marked = _pdf_text(sp.render_pdf(p["title_page"], now, "T", sp.revised_elements(text, now), "Blue Rev. 05/01/26"))
    assert b"(*)" not in plain and marked.count(b"(*)") == 2                # one line each
    assert marked.count(b"(Blue Rev. 05/01/26)") == 1                       # on the revised page only


# ------------------------------------------------------------------ the storyboard keeps a cut shot's takes
def _projects(boards):
    pkg = types.ModuleType("studio_pkg")
    pkg.__path__ = [os.path.join(REPO, "ComfyUI-Studio")]
    sys.modules["studio_pkg"] = pkg
    sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp")).web = types.SimpleNamespace()
    spec = importlib.util.spec_from_file_location("studio_pkg.projects", os.path.join(REPO, "ComfyUI-Studio", "projects.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["studio_pkg.projects"] = m
    sys.modules["studio_pkg.screenplay"] = sp
    spec.loader.exec_module(m)
    m.H.update(_load_boards=lambda: boards, _save_boards=lambda b: None)
    return m


def test_a_shot_cut_from_the_screenplay_keeps_its_takes_and_gets_them_back():
    pytest.importorskip("aiohttp")
    p = sp.parse(open(FOUNTAIN, encoding="utf-8").read())
    bd = sp.breakdown(p)
    proj = {"id": "p1", "title": "T", "scenes": bd["scenes"], "shots": bd["shots"], "cast": [], "board": "b1"}
    boards = [{"id": "b1", "shots": []}]
    pm = _projects(boards)
    b = pm.to_board(proj, {})
    gone = b["shots"][2]
    gone["takes"] = [{"output": {"filename": "take.mp4"}, "prompt": gone["prompt"]}]
    shots = proj["shots"]
    proj["shots"] = shots[:2] + shots[3:]                                # the screenplay lost that setup
    b = pm.to_board(proj, {})
    assert gone["projShot"] not in [x["projShot"] for x in b["shots"]]
    assert [x["id"] for x in b["cut"]] == [gone["id"]] and b["cut"][0]["takes"][0]["output"]["filename"] == "take.mp4"
    proj["shots"] = shots[:2] + [dict(shots[2], id="new-id")] + shots[3:]   # back (an undo): a new id, the same first beat
    b = pm.to_board(proj, {})
    back = b["shots"][2]
    assert back["id"] == gone["id"] and back["projShot"] == "new-id" and back["takes"][0]["output"]["filename"] == "take.mp4"
    assert b["cut"] == []


# ------------------------------------------------------------------ locations: the screenplay's places, in its prompts
PLACES = """INT. DINER - DAY

A chrome diner. Rain on the glass.

EXT. PIER - NIGHT

Fog over the black water.

INT. DINER - NIGHT

Empty now.

OMITTED

I/E CAR - CONTINUOUS

Wipers.
"""


def test_the_breakdown_lists_the_screenplays_places():
    bd = sp.breakdown(sp.parse(PLACES))
    places = bd["locations"]
    assert [(p["name"], p["full"], p["intext"], p["times"], len(p["scenes"])) for p in places] == [
        ("DINER", "Diner", ["INT"], ["DAY", "NIGHT"], 2), ("PIER", "Pier", ["EXT"], ["NIGHT"], 1), ("CAR", "Car", ["INT/EXT"], ["CONTINUOUS"], 1)]
    assert places[0]["description"] == "A chrome diner. Rain on the glass." and places[0]["scenes"] == [bd["scenes"][0]["id"], bd["scenes"][2]["id"]]
    gh = sp.breakdown(sp.parse(open(FOUNTAIN, encoding="utf-8").read()))["locations"]
    assert [p["name"] for p in gh] == ["THE GLASS HARBOUR DINER"] and gh[0]["description"].startswith("A 1941 diner")


def test_a_locations_look_is_in_every_shot_set_there():
    pytest.importorskip("aiohttp")
    pm = _projects([])
    bd = sp.breakdown(sp.parse(PLACES))
    proj = {"scenes": bd["scenes"], "shots": bd["shots"], "cast": [], "locations": [pm.new_location(p) for p in bd["locations"]]}
    scene = bd["scenes"][0]
    assert pm.setting_text(proj, scene) == "Inside Diner, day"                  # a new location: no look yet, nothing added
    proj["locations"][0]["look"] = "chrome and red vinyl, rain-streaked windows."
    assert pm.setting_text(proj, scene) == "Inside Diner (chrome and red vinyl, rain-streaked windows), day"
    assert pm.setting_text(proj, bd["scenes"][2]) == "Inside Diner (chrome and red vinyl, rain-streaked windows), night"
    assert pm.setting_text(proj, bd["scenes"][1]) == "Outside Pier, night"
    shot = next(s for s in bd["shots"] if s["scene"] == scene["id"])
    assert "Inside Diner (chrome and red vinyl, rain-streaked windows), day." in pm.shot_text(proj, shot, False, {})


def test_a_frame_or_a_picture_made_while_the_page_saves_is_kept():
    pytest.importorskip("aiohttp")
    pm = _projects([])
    stored = {"shots": [{"id": "a", "keyframe": "new.png", "kf_versions": ["old.png"], "kf_at": 200}, {"id": "b", "keyframe": "b.png", "kf_at": 100}],
              "locations": [{"id": "l", "picture": "pier.png", "pictures": ["pier.png"], "pic_at": 300}]}
    page = {"shots": [{"id": "a", "keyframe": "old.png", "kf_at": 150, "action": "edited"}, {"id": "b", "keyframe": "", "kf_at": 120}],
            "locations": [{"id": "l", "picture": "", "look": "fog"}]}
    out = pm.keep_newer_media(stored, page)
    assert out["shots"][0] == {"id": "a", "keyframe": "new.png", "kf_versions": ["old.png"], "kf_at": 200, "action": "edited"}   # the job's frame
    assert out["shots"][1] == {"id": "b", "keyframe": "", "kf_at": 120}                                                      # cleared later: cleared
    assert out["locations"][0] == {"id": "l", "picture": "pier.png", "pictures": ["pier.png"], "pic_at": 300, "look": "fog"}


def _node(js):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    r = subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, cwd=REPO, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_the_page_and_the_server_agree_on_what_a_shot_is_made_from_and_on_an_untouched_character():
    import json
    pytest.importorskip("aiohttp")
    pm = _projects([])
    shot = {"action": " Ray waits. ", "lines": [{"who": "RAY", "text": "Coffee. ", "paren": "(beat)"}, {"who": "JUNE", "text": "No."}],
            "characters": ["RAY", "JUNE"], "camera": "push in", "seconds": 38}
    scene = {"heading": "INT. DINER - DAY"}
    js = _node(f"import * as R from './ComfyUI-Studio/web/screenplay/rules.js'; console.log(JSON.stringify(R.shotSource({json.dumps(shot)}, {json.dumps(scene)})));")
    assert json.loads(js) == pm.shot_source(shot, scene)
    sheet = {"name": "Zoë “Z” O'Neil", "age": "57", "look": 'canvas jacket, "lucky" hat', "trigger": ""}
    app = open(os.path.join(REPO, "ComfyUI-Studio", "web", "app.js"), encoding="utf-8").read()
    keys = re.search(r"const SHEET_KEYS = (\[[^\]]*\]);", app).group(1)
    assert json.loads(keys) == list(pm.SHEET_KEYS)                      # the same sheet, field for field
    sig = _node(f"const K = {keys}; const c = {json.dumps(sheet)}; process.stdout.write(JSON.stringify(K.map(k => c[k] || '')));")
    assert pm.made_for_and_untouched(dict(sheet, auto="p1", autoSig=sig), "p1")
    assert not pm.made_for_and_untouched(dict(sheet, auto="p1", autoSig=sig, face="studio/z.png"), "p1")      # it has a face now
    assert not pm.made_for_and_untouched(dict(sheet, look="changed", auto="p1", autoSig=sig), "p1")         # its sheet was edited
    assert not pm.made_for_and_untouched(dict(sheet, auto="p2", autoSig=sig), "p1")                         # made for another project
