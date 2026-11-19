"""Projects: idea -> script -> cast -> keyframes -> storyboard -> sound -> film.

A project keeps the plan (the AI-written script and shot list) and links to the pieces Studio already has:
Characters for the cast, the Image app for keyframes, a Storyboard board for the takes, Music/Voices for the sound.
Incognito projects live in the private space."""
import asyncio
import json
import os
import re
import time
import urllib.parse
import uuid

from aiohttp import web

from . import screenplay as _sp

H = {}
_RUNNING = set()                                        # the background jobs, referenced until they finish


def _background(coro):
    """Runs a job in the background: asyncio keeps only a weak reference to a task, so one nobody holds can vanish."""
    task = asyncio.ensure_future(coro)
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    return task
MAX_SCREENPLAY = 40 * 1024 * 1024                      # a PDF with pictures in it can be big; the text is what's kept
SCREENPLAY_EXT = (".pdf", ".fountain", ".spmd", ".txt", ".text", ".md")
CAMERAS = ["static", "push in", "pull back", "pan left", "pan right", "tracking", "orbit", "crane up", "crane down",
           "handheld", "close-up", "low angle", "drone", "slow motion"]

SCRIPT_PROMPT = """You are a film director planning a short AI-generated film. Answer with JSON only, no other text.

Idea: "{idea}"
Tone / style: {tone}
Number of shots: exactly {n}
Each shot lasts about {secs} seconds.
{cast}
Write this JSON:
{{"title": "short film title",
 "logline": "one sentence that sums up the story",
 "music": "music style for the soundtrack: genre, mood, instruments, tempo (comma separated)",
 "shots": [
  {{"title": "2-4 words",
    "action": "what we SEE in this shot: subject, place, action, light, mood. 1-2 vivid present-tense sentences. Name characters by name.",
    "camera": "one of: {cams}",
    "characters": ["names of characters visible in this shot"],
    "dialogue": "a short spoken line if someone speaks, else empty"}}
 ]}}
Rules: tell a story with a beginning, middle and end across the shots; every shot is one continuous moment; keep places and
characters consistent from shot to shot; no text or logos on screen."""


def _file(private):
    return os.path.join(H["_priv_root"](), ".projects.json") if private else os.path.join(H["_data_dir"](), "projects.json")


def _load(private):
    try:
        with open(_file(private), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_all(private, items):
    p = _file(private)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f)
    os.replace(tmp, p)


def _get(pid, private):
    return next((x for x in _load(private) if x.get("id") == pid), None)


def _put(proj):
    private = bool(proj.get("private"))
    items = [x for x in _load(private) if x.get("id") != proj["id"]] + [proj]
    proj["updated"] = time.time()
    _save_all(private, items)
    return proj


def _clean_shots(raw, n, secs):
    shots = []
    for s in (raw or [])[: max(1, n)]:
        if not isinstance(s, dict):
            continue
        cam = str(s.get("camera") or "static").lower().strip()
        cam = next((c for c in CAMERAS if c in cam), "static")
        chars = s.get("characters") or []
        if isinstance(chars, str):
            chars = [c.strip() for c in chars.split(",") if c.strip()]
        shots.append({"id": uuid.uuid4().hex[:8], "title": str(s.get("title") or f"Shot {len(shots) + 1}")[:60],
                      "action": str(s.get("action") or s.get("description") or "").strip()[:900], "camera": cam,
                      "characters": [str(c)[:40] for c in chars][:6], "dialogue": str(s.get("dialogue") or "").strip()[:400],
                      "seconds": secs})
    return shots


CAMS_WORDS = {"wide": "wide establishing shot", "pov": "point-of-view shot", "over the shoulder": "over-the-shoulder shot",
              "high angle": "high-angle shot looking down", "static": "static locked-off camera", "push in": "slow dolly push-in toward the subject", "pull back": "slow dolly pull-back revealing the scene",
              "pan left": "smooth pan left", "pan right": "smooth pan right", "tracking": "tracking shot following the subject",
              "orbit": "camera orbits around the subject", "crane up": "crane shot rising up", "crane down": "crane shot descending",
              "handheld": "handheld camera with natural shake", "close-up": "tight close-up", "low angle": "low-angle hero shot",
              "drone": "aerial drone flyover", "slow motion": "slow motion"}


# ---------------------------------------------------------------- the character sheet, as the generators get it
CHAR_FIELDS = ["age", "gender", "build", "skin", "features", "eyes", "hair", "facial_hair", "marks", "wardrobe", "accessories"]   # ("face" is the picture)
_NUMBER_AGE = re.compile(r"^(?:\d{1,3}|(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:-(?:one|two|three|four|five|six|seven|eight|nine))?|"
                         r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)$", re.I)


def char_look(c):
    """A character sheet as one description for a generator (the same as the page's charLook): '57-year-old man, heavy-set,
    weathered skin, grey eyes, short grey hair, wearing a tan canvas jacket, felt hat' and the notes."""
    g = {k: str(c.get(k) or "").strip() for k in CHAR_FIELDS}
    age = g["age"] + ("-year-old" if _NUMBER_AGE.match(g["age"]) else "") if g["age"] else ""
    parts = [" ".join(x for x in (age, g["gender"]) if x), g["build"],
             g["skin"] + ("" if not g["skin"] or "skin" in g["skin"].lower() else " skin"), g["features"],
             g["eyes"] + ("" if not g["eyes"] or "eye" in g["eyes"].lower() else " eyes"),
             g["hair"] + ("" if not g["hair"] or re.search(r"hair|bald|shaved", g["hair"], re.I) else " hair"), g["facial_hair"], g["marks"],
             ("" if not g["wardrobe"] or re.match(r"(wearing|in|dressed)\b", g["wardrobe"], re.I) else "wearing ") + g["wardrobe"] if g["wardrobe"] else "",
             g["accessories"], str(c.get("look") or "").strip().rstrip(".")]
    return ", ".join(x.strip().rstrip(",") for x in parts if x and x.strip())


def char_desc(c):
    """What a prompt says about a character: the LoRA's trigger word first (it's what calls up the LoRA), then the look."""
    return ", ".join(x for x in (str(c.get("trigger") or "").strip(), char_look(c)) if x)


def _family(text):
    """A model's or a LoRA's family, as the page's modelFamily / loraFamily name it ('' when unknown)."""
    t = (text or "").lower()
    for rx, fam in ((r"anime desire|illustrious|noobai|pony|sdxl", "sdxl"), (r"qwen", "qwen"), (r"z-?image", "zimage"), (r"\banima\b", "anima"),
                    (r"wan.*2\.2", "wan22"), (r"ltx", "ltx"), (r"minimax|h3", "h3"), (r"krea", "krea"), (r"flux\.?\s*(3|klein)|flux 2|flux\.2|klein", "flux3"),
                    (r"moss", "moss")):
        if re.search(rx, t):
            return fam
    return ""


def char_loras(chars, model_option):
    """The LoRAs of these characters that fit this model: [{name, strength}]."""
    fam = _family(model_option)
    out = []
    for c in chars:
        lo = c.get("lora") or {}
        if lo.get("name") and (not lo.get("family") or not fam or lo.get("family") == fam) and lo["name"] not in [x["name"] for x in out]:
            out.append({"name": lo["name"], "strength": float(lo.get("strength") or 0.8)})
    return out


def _sentence(p):
    p = p.strip()
    p = p[:1].upper() + p[1:]
    return p if not p or p.endswith((".", "!", "?", '"', "”", "…")) else p + "."


def location_of(proj, scene):
    """The project's location a scene is set in (its heading's place), or None."""
    name = ((scene or {}).get("location") or "").strip().upper()
    return next((x for x in proj.get("locations") or [] if (x.get("name") or "").upper() == name), None) if name else None


def setting_text(proj, scene):
    """Where and when, as a prompt says it, with the location's look: 'Inside The Glass Harbour Diner (a 1941 diner…),
    late afternoon' (the page's settingText)."""
    if not scene:
        return ""
    loc = _sp.nice_name(scene.get("location") or "").strip()
    time_ = (scene.get("time") or "").strip().lower()
    where = {"INT": "Inside ", "EXT": "Outside ", "EST": "Establishing view of "}.get(scene.get("intext") or "", "")
    look = re.sub(r"\s+", " ", (location_of(proj, scene) or {}).get("look") or "").strip().rstrip(".")
    return ", ".join(x for x in [(where + loc + (f" ({look})" if look else "")) if loc else "", time_] if x)


def shot_source(s, scene):
    """What a shot is made from (the page's SR.shotSource): kept with a frame as `kf_src`, so the storyboard can say when
    the screenplay changed it after the frame was made."""
    lines = [dict({"who": ln.get("who") or "", "text": (ln.get("text") or "").strip()}, **({"paren": ln["paren"]} if ln.get("paren") else {}))
             for ln in s.get("lines") or []]
    return {"action": (s.get("action") or "").strip(), "lines": lines, "chars": list(s.get("characters") or []),
            "setting": (scene or {}).get("heading") or "", "camera": s.get("camera") or "", "seconds": float(s.get("seconds") or 0) or 0}


KF_FIELDS = ("keyframe", "keyframe_out", "kf_versions", "kf_src", "kf_at")
PIC_FIELDS = ("picture", "pictures", "pic_at")


def keep_newer_media(old, new):
    """A project saved by the page while a job was making a frame or a picture for it: what the job made after the page
    read the project is kept (a frame: its shot's kf_* fields; a location's picture), the rest is the page's."""
    if not old:
        return new
    for key, fields, stamp in (("shots", KF_FIELDS, "kf_at"), ("locations", PIC_FIELDS, "pic_at")):
        was = {x.get("id"): x for x in old.get(key) or [] if isinstance(x, dict)}
        for x in new.get(key) or []:
            o = was.get(x.get("id")) if isinstance(x, dict) else None
            if o and (o.get(stamp) or 0) > (x.get(stamp) or 0):
                for f in fields:
                    if f in o:
                        x[f] = o[f]
                    else:
                        x.pop(f, None)
    return new


def by_ai(request):
    """A request from an AI client (the MCP connector says so with X-Studio-Client: ai), not from the page."""
    return request.headers.get("X-Studio-Client", "").lower() == "ai"


def stale_over_ai(stored, incoming):
    """A page's save that would undo an AI's change (Claude through MCP): the project was last saved by an AI, after the
    version the page sends ("updated" as the page read it). The page gets the project as it is now, and takes it in."""
    if not stored or stored.get("by") != "ai":
        return False
    seen = incoming.get("updated")
    return isinstance(seen, (int, float)) and (stored.get("updated") or 0) > seen + 1e-6


SHEET_KEYS = ("name", "age", "gender", "build", "skin", "features", "eyes", "hair", "facial_hair", "marks", "wardrobe", "accessories", "look", "trigger")


def made_for_and_untouched(c, pid):
    """A character the page made from this project's screenplay (the cast follows it) that nobody has touched since: its
    sheet as it was made (the page's sheetSig), no face, pictures, LoRA or voice."""
    sig = json.dumps([c.get(k) or "" for k in SHEET_KEYS], ensure_ascii=False, separators=(",", ":"))
    return c.get("auto") == pid and c.get("autoSig") == sig and not (c.get("face") or c.get("pictures") or c.get("lora") or c.get("voice"))


def new_location(p):
    """A project's location made from the screenplay's place (breakdown "locations")."""
    return {"id": uuid.uuid4().hex[:8], "name": p["name"], "full": p["full"], "description": p.get("description") or "",
            "look": "", "lighting": "", "time": (p.get("times") or [""])[0].lower(), "atmosphere": "", "sound": "",
            "picture": "", "intext": p.get("intext") or [], "scenes": p.get("scenes") or [], "auto": True}


def shot_text(proj, s, with_camera, chars, with_lines=False):
    """Same as the page's shotText: the action with the characters' looks written in, where and when the scene is (with
    the location's look), the spoken lines (for video) and the camera move."""
    t = s.get("action") or ""
    for cid in proj.get("cast", []):
        c = chars.get(cid)
        if not c:
            continue
        first = c["name"].split(" ")[0]
        look = char_desc(c)
        if look and re.search(r"\b" + re.escape(first) + r"\b", t, re.I) and look not in t:
            t = re.sub(r"\b(" + re.escape(c["name"]) + "|" + re.escape(first) + r")\b", lambda m: f"{m.group(1)} ({look})", t, count=1, flags=re.I)
    scene = next((x for x in proj.get("scenes") or [] if x.get("id") == s.get("scene")), None)
    parts = [t]
    if with_lines:                                       # the lines the shot opens on (a long setup holds many)
        parts += [f'{_sp.nice_name(ln.get("who") or "")} says: "{ln.get("text")}"' for ln in (s.get("lines") or [])[:2] if ln.get("text")]
    parts.append(setting_text(proj, scene))
    if with_camera and CAMS_WORDS.get(s.get("camera")):
        parts.append(CAMS_WORDS[s["camera"]])
    return " ".join(_sentence(x) for x in parts if x and x.strip())


def to_board(proj, chars, render_lengths=(5, 8, 10, 15)):
    """The project's Storyboard board: exactly the breakdown's shots, in its order, with its shot numbers (1A, 1B…),
    cameras and lengths; each shot's prompt (action, looks, setting, lines) and its keyframe as the first frame. Like the
    page's syncBoard: what was done on the board stays (takes, picks, trims, a prompt rewritten there); a board shot that
    belongs to no breakdown shot joins the breakdown; one whose breakdown shot was cut from the screenplay leaves the board
    for its "cut" list with its takes (and comes back with them when a shot opening on the same beat does).
    render_lengths: the Video app's durations, for the cut's trim."""
    boards = H["_load_boards"]()
    b = next((x for x in boards if x.get("id") == proj.get("board")), None)
    if not b:
        b = {"id": uuid.uuid4().hex[:10], "name": proj.get("title") or "Project film", "shots": [], "private": bool(proj.get("private")),
             "created": time.time(), "takes": 2}
        boards.append(b)
    b["name"] = proj.get("title") or b["name"]
    b["project"] = proj.get("id")
    old = b.get("shots", [])
    shots = proj.setdefault("shots", [])
    scenes = proj.get("scenes") or []
    for x in old:
        if not x.get("projShot"):
            ns = {"id": uuid.uuid4().hex[:8], "scene": x.get("scene") or (scenes[-1]["id"] if scenes else None), "action": x.get("prompt") or "",
                  "title": (re.split(r"[.!?]", x.get("prompt") or "Shot")[0])[:60], "lines": [], "dialogue": "", "characters": [],
                  "camera": next((k for k, v in CAMS_WORDS.items() if v == x.get("camera")), "static"), "seconds": x.get("seconds") or 5,
                  "manual": True, "keyframe": x.get("image") or ""}
            shots.append(ns)
            x["projShot"], x["auto"] = ns["id"], x.get("prompt")
    codes = _sp.shot_codes(scenes, shots)
    cut = b.setdefault("cut", [])
    new = []
    for s in shots:
        text = shot_text(proj, s, False, chars, with_lines=True)
        bs = next((x for x in old if x.get("projShot") == s["id"]), None)
        if not bs and s.get("anchor"):                   # a shot cut before, back in the screenplay: with its takes
            back = next((x for x in cut if x.get("anchor") == s["anchor"]), None)
            if back:
                cut.remove(back)
                bs = {k: v for k, v in back.items() if k != "cutAt"}
                bs["projShot"] = s["id"]
        if not bs:
            bs = {"id": uuid.uuid4().hex[:8], "prompt": text, "auto": text, "image": s.get("keyframe") or "", "projShot": s["id"]}
        if not bs.get("prompt") or "auto" not in bs or bs.get("prompt") == bs.get("auto"):
            bs["prompt"] = text
        bs["auto"] = text
        # first frame <-> keyframe: one picture. Changed on the board since the last sync: the board's wins; else the keyframe's
        if "kf" in bs and (bs.get("image") or "") != (bs.get("kf") or ""):
            s["keyframe"] = bs.get("image") or ""
        elif "kf" not in bs and not s.get("keyframe") and bs.get("image"):
            s["keyframe"] = bs["image"]
        bs["image"] = bs["kf"] = s.get("keyframe") or ""
        secs = int(s.get("seconds") or 5)
        k = shots.index(s)
        prev = codes[shots[k - 1]["id"]] if s.get("continues") and k and shots[k - 1].get("scene") == s.get("scene") else None
        bs.update(scene=s.get("scene"), code=codes[s["id"]], title=s.get("title") or "", seconds=secs, camera=CAMS_WORDS.get(s.get("camera"), ""),
                  cont=prev, anchor=s.get("anchor") or "")
        if not bs.get("trimSet"):
            render = next((x for x in render_lengths if x >= secs - 0.2), render_lengths[-1])
            bs["trimOut"] = secs if render > secs else 0
        new.append(bs)
    kept = {x["projShot"] for x in new}
    for x in old:                                        # cut from the screenplay: what was made for it stays with the board
        if x.get("projShot") and x["projShot"] not in kept and (any(t.get("output") for t in x.get("takes") or []) or x.get("final")):
            cut.append(dict(x, cutAt=time.time()))
    del cut[:-50]
    b["shots"] = new
    b["updated"] = time.time()
    H["_save_boards"](boards)
    return b


async def _read_upload(request):
    """The multipart screenplay upload: (file bytes, file name, private flag)."""
    if not request.content_type.startswith("multipart/"):
        raise web.HTTPBadRequest(text="Send the screenplay as a file upload (multipart/form-data).")
    reader = await request.multipart()
    data, name, private = None, "", False
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "private":
            private = str(await part.text()).lower() in ("1", "true", "yes")
        elif part.name == "file":
            name = os.path.basename((part.filename or "").replace("\\", "/"))
            buf = bytearray()
            while True:
                chunk = await part.read_chunk(1 << 16)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_SCREENPLAY:
                    raise web.HTTPRequestEntityTooLarge(max_size=MAX_SCREENPLAY, actual_size=len(buf),
                                                        text="That file is too big for a screenplay (40 MB at most).")
            data = bytes(buf)
    if not data:
        raise web.HTTPBadRequest(text="Pick a screenplay file (.pdf or .fountain).")
    return data, name, private


def convert_screenplay(data, name):
    """An uploaded screenplay -> {"text" (Fountain), "source", "warnings", "parsed"}. Raises ScreenplayError for the user."""
    ext = os.path.splitext(name.lower())[1]
    if ext == ".fdx" or data.lstrip()[:300].find(b"<FinalDraft") >= 0:
        text = _sp.fdx_to_fountain(data)
        parsed = _sp.parse(text)
        return {"text": text, "source": "fdx", "warnings": [], "parsed": parsed}
    is_pdf = data.lstrip()[:5] == b"%PDF-" or ext == ".pdf"
    if not is_pdf and ext and ext not in SCREENPLAY_EXT:
        raise _sp.ScreenplayError("Import a screenplay as a .pdf, .fdx (Final Draft) or .fountain file.")
    if is_pdf:
        text, warnings = _sp.fountain_from_pdf(data)
    else:
        text, warnings = _sp.fountain_from_text(data), []
    parsed = _sp.parse(text)
    if not parsed["elements"]:
        raise _sp.ScreenplayError("There's no screenplay text in that file.")
    return {"text": text, "source": "pdf" if is_pdf else "fountain", "warnings": warnings, "parsed": parsed}


def _title_from_name(name):
    stem = os.path.splitext(name)[0].replace("_", " ").strip()
    return stem.title() if stem == stem.lower() else stem


def _summary(parsed, bd=None):
    out = {"title": parsed["title"], "scenes": len(parsed["scenes"]), "pages": parsed["pages"],
           "characters": [c["name"] for c in parsed["characters"]]}
    if bd:
        out["shots"] = len(bd["shots"])
    return out


def setup(ns):
    H.update(ns)
    routes = ns["routes"]
    _need_private, _flag, _new_job, _ai_job = ns["_need_private"], ns["_flag"], ns["_new_job"], ns["_ai_job"]

    def _check(request, private):
        if private:
            _need_private(request)

    @routes.get("/studio/api/projects")
    async def lst(request):
        out = list(_load(False))
        if _flag(request.query.get("private")):
            _need_private(request)
            out += _load(True)
        out.sort(key=lambda x: -x.get("updated", 0))
        return web.json_response(out)

    @routes.post("/studio/api/projects")
    async def save(request):
        b = await request.json()
        private = bool(b.get("private"))
        _check(request, private)
        if not b.get("id"):
            b["id"] = uuid.uuid4().hex[:10]
            b["created"] = time.time()
        else:
            other = _get(b["id"], not private)        # moved between normal and Incognito
            if other:
                _check(request, True)
                _save_all(not private, [x for x in _load(not private) if x.get("id") != b["id"]])
            stored = _get(b["id"], private)
            if not by_ai(request) and stale_over_ai(stored, b):
                return web.json_response({"error": "changed", "project": stored}, status=409)
            keep_newer_media(stored, b)
        b["by"] = "ai" if by_ai(request) else "page"
        return web.json_response(_put(b))

    @routes.delete("/studio/api/projects/{pid}")
    async def delete(request):
        pid = request.match_info["pid"]
        for private in (False, True):
            if _get(pid, private):
                _check(request, private)
                _save_all(private, [x for x in _load(private) if x.get("id") != pid])
        chars = ns["_load_chars"]()                 # the characters made for its cast that nobody touched go with it
        used = {cid for p in (False, True) for x in _load(p) for cid in [*(x.get("cast") or []), *(x.get("castMap") or {}).values()] if cid}
        keep = [c for c in chars if not made_for_and_untouched(c, pid) or c.get("id") in used]   # (unless another project casts them)
        if len(keep) != len(chars):
            ns["_save_chars"](keep)
        return web.json_response({"ok": True, "characters_removed": len(chars) - len(keep)})

    @routes.post("/studio/api/projects/import_screenplay")
    async def import_screenplay(request):
        """A screenplay (PDF or Fountain, multipart "file", "private") becomes a new project holding it as Fountain text."""
        data, name, private = await _read_upload(request)
        _check(request, private)
        try:
            conv = await asyncio.to_thread(convert_screenplay, data, name)
        except _sp.ScreenplayError as e:
            raise web.HTTPBadRequest(text=str(e)) from e
        parsed = conv["parsed"]
        title = (parsed["title"] or _title_from_name(name) or "Imported screenplay")[:120]
        now = time.time()
        bd = _sp.breakdown(parsed)
        proj = {"id": uuid.uuid4().hex[:10], "created": now, "name": title, "title": title, "tone": "Cinematic drama", "cast": [],
                "castMap": {}, "private": private, "stage": "Screenplay", "step": "screenplay", "scenes": bd["scenes"], "shots": bd["shots"],
                "roles": bd["characters"], "locations": [new_location(x) for x in bd["locations"]],
                "screenplay": {"text": conv["text"], "source": conv["source"], "filename": name[:200], "imported": now}}
        _put(proj)
        return web.json_response({"project": proj, "warnings": conv["warnings"], "summary": _summary(parsed, bd)})

    @routes.post("/studio/api/screenplay/convert")
    async def convert(request):
        """Reads a screenplay file into Fountain text without saving anything (to replace a project's screenplay)."""
        data, name, _private = await _read_upload(request)
        try:
            conv = await asyncio.to_thread(convert_screenplay, data, name)
        except _sp.ScreenplayError as e:
            raise web.HTTPBadRequest(text=str(e)) from e
        return web.json_response({"text": conv["text"], "source": conv["source"], "filename": name[:200], "warnings": conv["warnings"],
                                  "summary": _summary(conv["parsed"])})

    @routes.post("/studio/api/screenplay/breakdown")
    async def breakdown_route(request):
        """The screenplay editor's save: {elements, title_page} (or {text}) with the project's current {scenes, shots} ->
        the Fountain text and the breakdown (scenes, shots, characters) merged with the current one. Saves nothing."""
        b = await request.json()
        starts = []
        if isinstance(b.get("elements"), list):
            els = [e for e in b["elements"] if isinstance(e, dict) and e.get("type")]
            text = _sp.to_fountain([tuple(x) for x in b.get("title_page") or [] if isinstance(x, (list, tuple)) and len(x) == 2], els, starts)
        else:
            text = b.get("text") or ""
        if not isinstance(text, str) or len(text) > 5_000_000:
            raise web.HTTPBadRequest(text="The screenplay is too long")

        def work():
            parsed = _sp.parse(text)
            bd = _sp.breakdown(parsed, b.get("scenes"), b.get("shots"), tuple(b.get("redo") or ()))
            out = {"text": text, "title": parsed["title"], "pages": parsed["pages"], "scenes": bd["scenes"], "shots": bd["shots"],
                   "roles": bd["characters"], "places": bd["locations"]}
            if isinstance(b.get("elements"), list):      # the page marks, on the editor's own elements (their "i")
                sent = [e for e in b["elements"] if isinstance(e, dict) and e.get("type")]
                mine = [dict(e, text=e.get("text") or "") for e in sent]
                out["page_starts"] = [dict(m, el=sent[m["el"]].get("i", m["el"])) for m in _sp.page_starts(mine)]
                out["el_lines"] = [[e.get("i", k), starts[k]] for k, e in enumerate(sent) if k < len(starts)]
            return out
        return web.json_response(await asyncio.to_thread(work))

    def _export(request, ext, ctype):
        """(the project, a function that sends its file) for the screenplay's downloads."""
        private = _flag(request.query.get("private"))
        _check(request, private)
        proj = _get(request.match_info["pid"], private)
        if not proj:
            raise web.HTTPNotFound(text="No such project")
        name = re.sub(r'[\\/:*?"<>|]+', " ", proj.get("title") or "Screenplay").strip() or "Screenplay"
        return proj, (lambda data: web.Response(body=data, content_type=ctype, charset="utf-8" if ctype.startswith("text/") else None, headers={
            "Content-Disposition": f'attachment; filename="{name}.{ext}"; filename*=UTF-8\'\'{urllib.parse.quote(name)}.{ext}'}))

    @routes.get("/studio/api/projects/{pid}/screenplay.pdf")
    async def export_pdf(request):
        """The project's screenplay as a PDF on the Hollywood standard page. ?since=<draft id>: marked as a revision of that
        draft (asterisks on the changed lines, &label=Blue Rev. 05/01/26 at the top of the revised pages)."""
        proj, send = _export(request, "pdf", "application/pdf")
        since = request.query.get("since") or ""
        label = (request.query.get("label") or "")[:60]

        def work():
            parsed = _sp.parse((proj.get("screenplay") or {}).get("text") or "")
            draft = next((d for d in proj.get("drafts") or [] if d.get("id") == since), None) if since else None
            rev = _sp.revised_elements(draft.get("text") or "", parsed["elements"]) if draft else None
            return _sp.render_pdf(parsed["title_page"], parsed["elements"], proj.get("title") or "", rev, label if draft else "")
        return send(await asyncio.to_thread(work))

    @routes.get("/studio/api/projects/{pid}/screenplay.fdx")
    async def export_fdx(request):
        """The project's screenplay as a Final Draft file."""
        proj, send = _export(request, "fdx", "application/xml")
        parsed = _sp.parse((proj.get("screenplay") or {}).get("text") or "")
        return send(_sp.to_fdx(parsed["title_page"], parsed["elements"]))

    @routes.get("/studio/api/projects/{pid}/screenplay.txt")
    async def export_txt(request):
        """The project's screenplay as plain text laid out as the standard page."""
        proj, send = _export(request, "txt", "text/plain")
        parsed = _sp.parse((proj.get("screenplay") or {}).get("text") or "")
        return send(_sp.render_text(parsed["title_page"], parsed["elements"]).encode("utf-8"))

    @routes.post("/studio/api/screenplay/parse")
    async def parse(request):
        """Fountain text -> its elements (for the formatted view), scenes, characters and an estimate of the pages."""
        b = await request.json()
        text = b.get("text") or ""
        if not isinstance(text, str) or len(text) > 5_000_000:
            raise web.HTTPBadRequest(text="The screenplay text is too long")
        return web.json_response(await asyncio.to_thread(_sp.parse, text))

    @routes.post("/studio/api/projects/script")
    async def script(request):
        """The local AI writes the script and shot list from the idea. Returns a job; result = {title, logline, music, shots}."""
        b = await request.json()
        private = bool(b.get("private"))
        idea = (b.get("idea") or "").strip()
        if len(idea) < 8:
            raise web.HTTPBadRequest(text="Describe the idea in a sentence or two first")
        n = max(2, min(16, int(b.get("shots") or 6)))
        secs = max(3, min(15, int(b.get("seconds") or 5)))
        cast = b.get("cast") or []
        cast_txt = ("Characters (use these names): " + "; ".join(f'{c.get("name")}: {c.get("look") or ""}'.strip(": ") for c in cast) + "\n") if cast else ""
        prompt = SCRIPT_PROMPT.format(idea=idea.replace('"', "'")[:1500], tone=(b.get("tone") or "cinematic")[:200], n=n, secs=secs,
                                      cast=cast_txt, cams=", ".join(CAMERAS))
        job = _new_job(private)

        async def run():
            job["stage"] = "Writing the script"
            last = None
            for attempt in range(2):
                text = await ns["_llm"](job, prompt, [], private, 2200)
                data = ns["_json_from"](text)
                if data and isinstance(data.get("shots"), list) and data["shots"]:
                    shots = _clean_shots(data["shots"], n, secs)
                    if shots:
                        return {"title": str(data.get("title") or "")[:80], "logline": str(data.get("logline") or "")[:300],
                                "music": str(data.get("music") or "")[:300], "shots": shots}
                last = (text or "")[:300]
                job["stage"] = "Writing the script (second try)"
            raise RuntimeError("The AI didn't return a usable shot list. Try a clearer idea. " + (last or ""))
        _background(_ai_job(job, run))
        return web.json_response({"id": job["id"]})

    @routes.post("/studio/api/projects/{pid}/keyframes")
    async def keyframes(request):
        """Makes a still for each shot with the Image app (in order), saved on the project as the shot's first frame."""
        pid = request.match_info["pid"]
        b = await request.json()
        private = bool(b.get("private"))
        _check(request, private)
        proj = _get(pid, private)
        if not proj:
            raise web.HTTPNotFound(text="No such project")
        app = ns["_app_by_name"]("image")
        if not app:
            raise web.HTTPBadRequest(text="The Image app is missing")
        base = dict(b.get("values") or {})
        jobs = b.get("shots") or []                 # [{id, prompt, ref}] prepared by the page
        pkey, rkey, tkey = b.get("prompt_key"), b.get("ref_key"), b.get("ref_toggle_key")
        if not pkey:                                # called by an AI (MCP) or a script: the page's frames, worked out here
            from . import projectops as _ops
            if proj.get("boardLocked"):
                raise web.HTTPConflict(text="The storyboard is locked in the page: unlock it to make frames")
            try:
                _img, base, L, req = _ops.image_request(b.get("model"), b.get("values"))   # values by the options' labels
                pkey, rkey, tkey = req["prompt_key"], req["ref_key"], req["ref_toggle_key"]
                b.setdefault("seed_keys", req["seed_keys"])
                if not jobs:
                    only = [str(x).upper() for x in b.get("only") or []]
                    codes = _sp.shot_codes(proj.get("scenes") or [], proj.get("shots") or [])
                    todo = [s for s in proj.get("shots", []) if (str(codes.get(s["id"], "")).upper() in only or s["id"].upper() in only) if only] if only \
                        else [s for s in proj.get("shots", []) if b.get("redo") or not s.get("keyframe")]
                    jobs = _ops.frame_items(proj, todo, str(base.get((L["model"] or {}).get("key")) or ""), bool(rkey and tkey))
            except _ops.OpError as e:
                raise web.HTTPBadRequest(text=str(e))
        job = _new_job(private, kind="project-keyframes")

        async def run():
            done = 0
            for k, s in enumerate(jobs):
                if job.get("cancel"):
                    raise RuntimeError("Stopped")
                job["stage"] = f"Keyframe {k + 1} of {len(jobs)}"
                vals = dict(base)
                if pkey:
                    vals[pkey] = s.get("prompt") or ""
                if rkey and tkey:
                    vals[tkey] = bool(s.get("ref"))
                    if s.get("ref"):
                        vals[rkey] = s["ref"]
                for key in (b.get("seed_keys") or []):
                    vals[key] = int(time.time() * 1000 + k) % 2147483646 + 1
                pidq = await ns["_queue_app"](app, vals, private, f"Project keyframe {k + 1}", b.get("target"), s.get("loras"))
                job["pids"].append(pidq)
                fn, sub = await ns["_wait_output"](pidq, job)
                full = await asyncio.to_thread(ns["_find_out"], fn, sub, private)
                value = ns["_to_input"](full, private)
                p2 = _get(pid, private)
                if p2:
                    for sh in p2.get("shots", []):
                        if sh.get("id") == s.get("id"):
                            if sh.get("keyframe") and sh["keyframe"] != value:      # the frame it replaces: a version
                                sh["kf_versions"] = ([sh["keyframe"]] + [v for v in sh.get("kf_versions") or [] if v != sh["keyframe"]])[:8]
                            scene = next((x for x in p2.get("scenes") or [] if x.get("id") == sh.get("scene")), None)
                            sh["kf_src"] = shot_source(sh, scene)
                            sh["kf_at"] = time.time()
                            sh["keyframe"] = value
                            sh["keyframe_out"] = {"filename": fn, "subfolder": re.sub(r"^\.private_incoming/?", "", sub.replace("\\", "/")), "private": private}
                    _put(p2)
                done += 1
            return {"done": done}
        _background(_ai_job(job, run))
        return web.json_response({"id": job["id"]})

    @routes.post("/studio/api/projects/{pid}/locations/{lid}/picture")
    async def location_picture(request):
        """A reference picture of a location from its look, with the Image app (the page sends the app's values and the
        prompt). Kept with the location; the first one becomes its picture (a reference for the shots set there)."""
        pid, lid = request.match_info["pid"], request.match_info["lid"]
        b = await request.json()
        private = bool(b.get("private"))
        _check(request, private)
        proj = _get(pid, private)
        loc = next((x for x in (proj or {}).get("locations") or [] if x.get("id") == lid), None)
        if not loc:
            raise web.HTTPNotFound(text="No such location")
        app = ns["_app_by_name"]("image")
        if not app:
            raise web.HTTPBadRequest(text="The Image app is missing")
        if not b.get("prompt_key"):                 # an AI (MCP) or a script: the page's values and prompt, worked out here
            from . import projectops as _ops
            try:
                b = _ops.location_body(loc, b)
            except _ops.OpError as e:
                raise web.HTTPBadRequest(text=str(e))
        vals = dict(b.get("values") or {})
        if b.get("prompt_key"):
            vals[b["prompt_key"]] = b.get("prompt") or f"{loc.get('full') or loc.get('name')}, {loc.get('look') or ''}, empty, no people, film still"
        if b.get("ref_key") and b.get("ref_toggle_key"):
            vals[b["ref_toggle_key"]] = False
        for key in b.get("seed_keys") or []:
            vals[key] = int(time.time() * 1000) % 2147483646 + 1
        job = _new_job(private, kind="location-picture")

        async def run():
            job["stage"] = f"Picturing {loc.get('full') or loc.get('name')}"
            pidq = await ns["_queue_app"](app, vals, private, f"Location: {loc.get('full') or loc.get('name')}", b.get("target"))
            job["pids"] = [pidq]
            fn, sub = await ns["_wait_output"](pidq, job)
            full = await asyncio.to_thread(ns["_find_out"], fn, sub, private)
            value = ns["_to_input"](full, private)
            p2 = _get(pid, private)
            for x in (p2 or {}).get("locations") or []:
                if x.get("id") == lid:
                    x["pictures"] = [value] + [p for p in x.get("pictures") or [] if p != value]
                    if not x.get("picture"):
                        x["picture"] = value
                    x["edited"] = True
                    x["pic_at"] = time.time()
            if p2:
                _put(p2)
            return {"picture": value}
        _background(_ai_job(job, run))
        return web.json_response({"id": job["id"]})

    @routes.post("/studio/api/characters/{cid}/portrait")
    async def portrait(request):
        """A reference picture of a character from its sheet, with the Image app (the page sends the app's values, the
        prompt and where the face goes). The picture is kept with the character; the first one becomes its face."""
        cid = request.match_info["cid"]
        b = await request.json()
        c = next((x for x in ns["_load_chars"]() if x.get("id") == cid), None)
        if not c:
            raise web.HTTPNotFound(text="No such character")
        private = bool(c.get("private"))
        _check(request, private)
        app = ns["_app_by_name"]("image")
        if not app:
            raise web.HTTPBadRequest(text="The Image app is missing")
        if not b.get("prompt_key"):                 # an AI (MCP) or a script: the page's values and prompt, worked out here
            from . import projectops as _ops
            try:
                b = _ops.portrait_body(c, b)
            except _ops.OpError as e:
                raise web.HTTPBadRequest(text=str(e))
        vals = dict(b.get("values") or {})
        if b.get("prompt_key"):
            vals[b["prompt_key"]] = b.get("prompt") or f"{c['name']}, {char_desc(c)}"
        if b.get("ref_key") and b.get("ref_toggle_key"):
            vals[b["ref_toggle_key"]] = bool(b.get("ref"))
            if b.get("ref"):
                vals[b["ref_key"]] = b["ref"]
        for key in b.get("seed_keys") or []:
            vals[key] = int(time.time() * 1000) % 2147483646 + 1
        job = _new_job(private, kind="character-portrait")

        async def run():
            job["stage"] = f"Picturing {c['name']}"
            pidq = await ns["_queue_app"](app, vals, private, f"Reference picture: {c['name']}", b.get("target"), b.get("loras"))
            job["pids"] = [pidq]
            fn, sub = await ns["_wait_output"](pidq, job)
            full = await asyncio.to_thread(ns["_find_out"], fn, sub, private)
            value = ns["_to_input"](full, private)
            chars = ns["_load_chars"]()
            for x in chars:
                if x.get("id") == cid:
                    x["pictures"] = [value] + [p for p in x.get("pictures") or [] if p != value]
                    if not x.get("face"):
                        x["face"] = value
                    x["updated"] = time.time()
            ns["_save_chars"](chars)
            return {"picture": value}
        _background(_ai_job(job, run))
        return web.json_response({"id": job["id"]})

    @routes.post("/studio/api/projects/{pid}/to_board")
    async def to_board_route(request):
        b = await request.json()
        private = bool(b.get("private"))
        _check(request, private)
        proj = _get(request.match_info["pid"], private)
        if not proj:
            raise web.HTTPNotFound(text="No such project")
        chars = {c.get("id"): c for c in ns["_load_chars"]()}
        lengths = (5, 8, 10, 15)
        app = ns["_app_by_name"]("video")
        dur = next((i for i in (app or {}).get("inputs", []) if str(i.get("label", "")).lower().startswith("duration")), None)
        if dur and dur.get("options"):
            lengths = tuple(sorted(float(re.sub(r"[^\d.]", "", o) or 0) for o in dur["options"])) or lengths
        board = to_board(proj, chars, lengths)
        proj["board"] = board["id"]
        proj["stage"] = "Storyboard"
        _put(proj)
        return web.json_response({"board": board["id"], "name": board["name"], "shots": len(board["shots"]),
                                  "codes": [x.get("code") for x in board["shots"]]})
