"""A project's journey on the server for AI clients (ComfyUI-Studio/projectops.py): the page's project flows, in Python,
checked against what the page does (web/app.js):
  - the screenplay's places become locations: new ones added, a renamed heading carries the location worked on, one gone
    goes if untouched and stays (marked) if not, one added by hand stays (syncLocations)
  - the speaking parts are cast: a character of the same name, else one made from the screenplay's introduction; one made
    so that nobody touched goes with its part (a typo), one touched stays; a part left uncast stays so (syncCast, charFromRole)
  - a character's introduction in either of the screenplay's forms ('RAY, fifty-seven, …' and 'MARA (60s, …) climbs …')
  - the storyboard's references (the face of the one character with one, else the location's picture when nobody's in it)
  - a take's Video app values (prompt and camera, the length that holds the shot, the first frame, draft/final quality), and
    a model that needs a first frame refused without one
  - a place renamed in the scene headings; a page's save that would undo an AI's change"""
import importlib
import json
import os
import sys
import types

import pytest

pytestmark = pytest.mark.mcp

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACK = os.path.join(REPO, "ComfyUI-Studio")
if "studiopack" not in sys.modules:                      # the pack's folder as a package, without running its __init__ (ComfyUI's)
    _pkg = types.ModuleType("studiopack")
    _pkg.__path__ = [PACK]
    sys.modules["studiopack"] = _pkg
ops = importlib.import_module("studiopack.projectops")
pj = importlib.import_module("studiopack.projects")
sp = importlib.import_module("studiopack.screenplay")

SCRIPT = """Title: The Keeper

EXT. NORTHPOINT LIGHTHOUSE - DUSK

Waves hammer the rocks. MARA VOSS (60s, weathered, oilskin coat, silver braid) climbs the iron stairs.

MARA
Not tonight.

INT. LAMP ROOM - NIGHT

TEO (12, soaked, too-big raincoat) pokes his head through the trapdoor.

TEO
Gran? The boats are still out.

MARA
Hold the light steady.
"""


@pytest.fixture
def store(monkeypatch):
    """Studio's characters, in memory."""
    chars = []
    monkeypatch.setattr(ops, "H", {"_load_chars": lambda: [dict(c) for c in chars],
                                   "_save_chars": lambda cs: (chars.clear(), chars.extend(dict(c) for c in cs))})
    return chars


def project(text=SCRIPT):
    pr = {"id": "p1", "castMap": {}, "cast": [], "locations": []}
    parsed = sp.parse(text)
    bd = sp.breakdown(parsed)
    pr.update(scenes=bd["scenes"], shots=bd["shots"], roles=bd["characters"])
    return pr


def rebreak(pr, text):
    bd = sp.breakdown(sp.parse(text), pr["scenes"], pr["shots"])
    pr.update(scenes=bd["scenes"], shots=bd["shots"], roles=bd["characters"])


# ---------------------------------------------------------------- locations
def test_the_screenplays_places_become_locations_and_follow_it():
    pr = project()
    out = ops.sync_locations(pr)
    assert out["added"] == ["Northpoint Lighthouse", "Lamp Room"] and [x["auto"] for x in pr["locations"]] == [True, True]
    assert pr["locations"][1]["time"] == "night" and pr["locations"][0]["intext"] == ["EXT"]
    lamp = pr["locations"][1]
    lamp.update(look="brass and glass", edited=True)
    pr["locations"].append({"id": "x", "name": "THE BOAT", "full": "The Boat", "scenes": [], "auto": False})     # added by hand
    # the lamp room is renamed in the headings: the location worked on follows it; the lighthouse goes (nobody touched it)
    rebreak(pr, SCRIPT.replace("LAMP ROOM", "LANTERN GALLERY").replace("EXT. NORTHPOINT LIGHTHOUSE - DUSK", "EXT. THE CLIFF - DUSK"))
    out = ops.sync_locations(pr)
    names = [x["name"] for x in pr["locations"]]
    assert "NORTHPOINT LIGHTHOUSE" not in names and "THE CLIFF" in names and "THE BOAT" in names, names
    assert out["renamed"] == [] or out["renamed"] == [["Lamp Room", "Lantern Gallery"]]
    gallery = next(x for x in pr["locations"] if x["name"] in ("LANTERN GALLERY", "LAMP ROOM"))
    assert gallery is lamp and lamp["look"] == "brass and glass", "the location worked on follows the new name"
    # gone from the screenplay, with a picture: it stays, marked
    lamp["picture"] = "studio/lamp.png"
    rebreak(pr, SCRIPT.replace("INT. LAMP ROOM - NIGHT", "INT. STAIRWELL - NIGHT").replace("EXT. NORTHPOINT LIGHTHOUSE - DUSK", "EXT. THE CLIFF - DUSK"))
    ops.sync_locations(pr)
    assert lamp in pr["locations"] and lamp["gone"] is True and lamp["scenes"] == []


# ---------------------------------------------------------------- cast
def test_the_speaking_parts_are_cast_and_follow_the_screenplay(store):
    store.append({"id": "ray1", "name": "Teo Marsh", "look": "our own Teo"})          # an existing character of that name
    pr = project()
    out = ops.sync_cast(pr)
    assert out["added"] == ["Mara Voss"]
    mara = next(c for c in store if c["name"] == "Mara Voss")
    assert (mara["age"], mara["look"], mara["auto"]) == ("60s", "weathered, oilskin coat, silver braid", "p1")
    assert mara["autoSig"] == ops.sheet_sig(mara) and ops.untouched(mara)
    assert pr["castMap"] == {"MARA": mara["id"], "TEO": "ray1"} and pr["cast"] == [mara["id"], "ray1"]
    # Teo's part is written out: his (existing) character stays; Mara's part renamed (a typo fixed): her untouched one goes
    rebreak(pr, SCRIPT.replace("MARA", "MAREN").replace("TEO (12", "A BOY (12").replace("\nTEO\n", "\nA BOY\n"))
    out = ops.sync_cast(pr)
    assert "Mara Voss" in out["removed"] and not any(c["name"] == "Mara Voss" for c in store)
    assert any(c["id"] == "ray1" for c in store), "a character Studio didn't make for this project is never deleted"
    assert "MARA" not in pr["castMap"]
    assert pr["castMap"]["TEO"] == "ray1" and "ray1" not in pr["cast"], "a gone part keeps its character, should it come back, but isn't cast"
    # a character someone worked on stays when its part goes; a part left uncast on purpose stays so
    maren = next(c for c in store if c["id"] == pr["castMap"]["MAREN"])
    maren["hair"] = "silver braid"
    store[:] = [maren if c["id"] == maren["id"] else c for c in store]
    pr["castMap"]["A BOY"] = ""
    rebreak(pr, SCRIPT.replace("MARA", "ELLEN").replace("TEO (12", "A BOY (12").replace("\nTEO\n", "\nA BOY\n"))
    ops.sync_cast(pr)
    assert any(c["id"] == maren["id"] for c in store) and pr["castMap"]["A BOY"] == ""


def test_a_characters_introduction_in_either_form():
    intro = lambda t: sp._intro(t.split(" ")[0], [t])[1]     # noqa: E731
    assert intro("MARA VOSS (60s, weathered, oilskin coat) climbs the stairs.") == "60s, weathered, oilskin coat"
    assert intro("JOHN (40s), a tired cop, leans on the bar.") == "40s, a tired cop, leans on the bar"
    assert intro("RAY MERCER, fifty-seven, canvas work jacket.").startswith("fifty-seven, canvas work jacket")
    assert ops.char_from_role({"name": "RAY", "full": "Ray Mercer", "intro": "fifty-seven, canvas work jacket"}) == \
        {"name": "Ray Mercer", "age": "fifty-seven", "look": "canvas work jacket", "trigger": "", "voice": "", "face": ""}
    assert ops.char_from_role({"name": "ANNA", "intro": "a nurse in scrubs"})["age"] == ""


# ---------------------------------------------------------------- the storyboard's references
def test_a_frames_reference_is_the_one_face_else_the_place_when_nobodys_in_it(monkeypatch):
    pr = project()
    ops.sync_locations(pr)
    pr["locations"][1]["picture"] = "studio/lamp.png"
    pr["locations"][0]["picture"] = "studio/tower.png"
    chars = {"m": {"id": "m", "name": "Mara Voss", "face": "studio/mara.png", "look": "a lighthouse keeper", "lora": {"name": "mara.safetensors", "family": "zimage"}},
             "t": {"id": "t", "name": "Teo"}}
    pr["cast"] = ["m", "t"]
    monkeypatch.setattr(ops, "chars_by_id", lambda: chars)
    empty = dict(pr["shots"][0], id="e", action="Waves hammer the rocks.", characters=[])
    items = ops.frame_items(pr, pr["shots"] + [empty], "Z-Image Turbo (fast, photoreal)", True)
    assert [i["ref"] for i in items] == ["studio/mara.png", "studio/mara.png", "studio/tower.png"]
    assert items[0]["loras"] == [{"name": "mara.safetensors", "strength": 0.8}]
    assert ops.frame_items(pr, pr["shots"], "Qwen Image", True)[0]["loras"] == [], "a LoRA for another model family stays out"
    assert "MARA VOSS (a lighthouse keeper)" in items[0]["prompt"], "the look written in where the character is named"
    assert "Outside Northpoint Lighthouse, dusk" in items[0]["prompt"]
    pr["useRefs"] = False
    assert [i["ref"] for i in ops.frame_items(pr, pr["shots"], "", True)] == ["", ""]


# ---------------------------------------------------------------- takes
VIDEO = {"name": "Studio - Video", "inputs": [
    {"key": "p", "kind": "text", "label": "Prompt", "value": "x"},
    {"key": "m", "kind": "choice", "label": "Model", "value": "LTX-2.5 Image to video (Image 1 = first frame, sound, 720p)",
     "options": ["MiniMax H3 References (images + voice)", "LTX-2.5 Image to video (Image 1 = first frame, sound, 720p)"]},
    {"key": "d", "kind": "choice", "label": "Duration", "value": "5 s", "options": ["5 s", "8 s", "10 s", "15 s"]},
    {"key": "q", "kind": "choice", "label": "Quality", "value": "Turbo (draft)", "options": ["Turbo (draft)", "Full 20 steps (final)"]},
    {"key": "u1", "kind": "toggle", "label": "Use image 1", "value": True}, {"key": "i1", "kind": "image", "label": "Image 1: first frame", "value": "a.png"},
    {"key": "u2", "kind": "toggle", "label": "Use image 2", "value": True}, {"key": "i2", "kind": "image", "label": "Image 2", "value": "a.png"},
    {"key": "s", "kind": "seed", "label": "Seed", "value": 0}]}


def test_a_takes_values_are_the_pages():
    shot = {"prompt": "Waves hammer the rocks", "camera": "slow dolly push-in toward the subject", "seconds": 7, "image": "studio/f.png"}
    v, seed = ops.take_values(VIDEO, shot, "draft")
    assert v["p"] == "Waves hammer the rocks. Slow dolly push-in toward the subject."
    assert (v["d"], v["i1"], v["u1"], v["u2"], v["q"]) == ("8 s", "studio/f.png", True, False, "Turbo (draft)")
    assert v["s"] == seed and seed > 0
    v, seed = ops.take_values(VIDEO, dict(shot, seconds=30), "final", seed=42)
    assert (v["d"], v["q"], seed) == ("15 s", "Full 20 steps (final)", 42)
    with pytest.raises(ops.OpError, match="needs a first frame"):
        ops.take_values(VIDEO, dict(shot, image=""), "draft")
    v, _ = ops.take_values(VIDEO, dict(shot, image=""), "draft", model="MiniMax")
    assert v["u1"] is False and v["m"].startswith("MiniMax")
    with pytest.raises(ops.OpError, match="No model 'Sora'"):
        ops.take_values(VIDEO, shot, "draft", model="Sora")
    assert ops.with_camera("", "static locked-off camera") == "Static locked-off camera."


# ---------------------------------------------------------------- the screenplay's headings, and saves
def test_a_place_is_renamed_in_the_scene_headings():
    text, n = ops.rename_place(SCRIPT + "\nINT. LAMP ROOM - LATER\n\nQuiet.\n", "LAMP ROOM", "LANTERN GALLERY")
    assert n == 2 and "INT. LANTERN GALLERY - NIGHT" in text and "INT. LANTERN GALLERY - LATER" in text and "LAMP ROOM" not in text


def test_a_page_save_that_would_undo_an_ai_change_is_refused():
    stored = {"id": "p", "updated": 200.0, "by": "ai"}
    assert pj.stale_over_ai(stored, {"updated": 100.0}) is True
    assert pj.stale_over_ai(stored, {"updated": 200.0}) is False, "the page had seen the AI's change"
    assert pj.stale_over_ai(dict(stored, by="page"), {"updated": 100.0}) is False, "page against page: the last save wins, as before"
    assert pj.stale_over_ai(stored, {}) is False and pj.stale_over_ai(None, {"updated": 1}) is False
    assert json.loads(ops.sheet_sig({"name": "A", "age": "3"}))[:2] == ["A", "3"]
