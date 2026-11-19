"""A project's whole journey for a client without the page (Claude and other AIs through MCP, or a script): the page's
project flows, run on the server.

The page does these in the browser (web/app.js): the screenplay saved and broken down again, its places becoming the
project's locations and its speaking parts cast (syncLocations, syncCast), a character's or a location's reference picture,
the storyboard's frames with their references, the takes drafted with the Video app and picked, the music and the voice
lines, and the film cut. Here they are the same, step for step, so a project made this way opens in the page as if it had
been made there:

    POST /studio/api/projects/new                     {title, screenplay?, tone?}                 a project (from a screenplay's text)
    POST /studio/api/projects/{pid}/screenplay        {text, draft?}                              the screenplay replaced; breakdown; follow
    POST /studio/api/projects/{pid}/follow            {}                                          locations and cast follow the screenplay
    POST /studio/api/projects/{pid}/cast              {role, character}                           a part cast (character id or name; "" uncast)
    POST /studio/api/projects/{pid}/locations/{lid}   {name?, description?, look?, ...}           a location's form
    POST /studio/api/projects/{pid}/shots/{sid}       {camera?, seconds?, frame?}                 a shot's camera, length or frame
    POST /studio/api/projects/{pid}/takes             {shots?, count?, quality?, model?}          takes drafted (Video app), on the board
    POST /studio/api/boards/{bid}/resolve             {}                                          the takes' files, from ComfyUI's history
    POST /studio/api/boards/{bid}/shots/{sid}/pick    {take}                                      the take the film uses
    POST /studio/api/projects/{pid}/music             {style?, engine?, seconds?}                 the score (job)
    POST /studio/api/projects/{pid}/voices            {}                                          the dialogue in the cast's voices (job)
    POST /studio/api/projects/{pid}/film              {crossfade?, title?, end?, music?, ...}     the film cut from the board (job)
    GET  /studio/api/stamps                                                                       when each project and board last changed, and by whom

What an AI changes is saved with "by": "ai". The page watches /studio/api/stamps and takes those changes in; a save from a
page that hadn't seen one is refused (409, with the project as it is now) instead of undoing it (projects.py, __init__.py)."""
import asyncio
import json
import re
import time
import uuid

from aiohttp import web

from . import projects as _pj
from . import screenplay as _sp
from . import sound as _sound

H = {}
BY_AI = "ai"
SHEET_KEYS = _pj.SHEET_KEYS
LOCATION_FIELDS = ("description", "look", "lighting", "time", "atmosphere", "sound")


class OpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _rid():
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------- the apps, as a client without the page fills them
def app_inputs(app):
    """An app's inputs as the page lays them out (web/app.js layout): the prompt, the negative prompt, the model, and its
    media slots each with the "Use …" toggle before it."""
    ins = app["inputs"]
    L = {"prompt": None, "negative": None, "model": None, "refs": [], "seeds": [i["key"] for i in ins if i["kind"] == "seed"],
         "duration": next((i for i in ins if i["label"].lower().startswith("duration")), None),
         "quality": next((i for i in ins if i["label"].lower().startswith("quality")), None)}
    k = 0
    while k < len(ins):
        i, lab = ins[k], ins[k]["label"].lower()
        if i["kind"] == "text":
            if "negative" in lab:
                L["negative"] = i
            elif not L["prompt"]:
                L["prompt"] = i
        elif i["kind"] == "choice" and not L["model"] and "model" in lab:
            L["model"] = i
        elif i["kind"] == "toggle" and lab.startswith("use") and k + 1 < len(ins) and ins[k + 1]["kind"] in ("image", "video", "audio", "mask"):
            L["refs"].append({"media": ins[k + 1], "toggle": i})
            k += 1
        elif i["kind"] in ("image", "video", "audio", "mask"):
            L["refs"].append({"media": i, "toggle": None})
        k += 1
    return L


def pick_option(inp, want):
    w = str(want).lower().strip()
    opts = inp.get("options") or []
    return next((o for o in opts if o.lower() == w), None) or next((o for o in opts if w in o.lower()), None)


def app_values(app, model=None, values=None):
    """The app's values: its own defaults (what the workflow was saved with), the model asked for (part of its name), and
    any other option by its label. Raises OpError naming the choices when something doesn't fit."""
    vals = {i["key"]: i.get("value") for i in app["inputs"]}
    L = app_inputs(app)
    if model:
        if not L["model"]:
            raise OpError(400, f"The {app['name']} app has no model choice")
        o = pick_option(L["model"], model)
        if not o:
            raise OpError(400, f"No model '{model}'. Choices: " + " | ".join(L["model"]["options"]))
        vals[L["model"]["key"]] = o
    for label, v in (values or {}).items():
        inp = next((i for i in app["inputs"] if i["label"].lower() == str(label).lower()), None) or \
            next((i for i in app["inputs"] if str(label).lower() in i["label"].lower()), None)
        if not inp:
            raise OpError(400, f"The app has no option '{label}'. Options: " + ", ".join(i["label"] for i in app["inputs"]))
        if inp["kind"] == "choice":
            o = pick_option(inp, v)
            if not o:
                raise OpError(400, f"'{inp['label']}' must be one of: " + " | ".join(inp["options"]))
            vals[inp["key"]] = o
        elif inp["kind"] in ("int", "seed"):
            vals[inp["key"]] = int(v)
        elif inp["kind"] == "float":
            vals[inp["key"]] = float(v)
        elif inp["kind"] == "toggle":
            vals[inp["key"]] = bool(v)
        else:
            vals[inp["key"]] = v
    return vals, L


def _app(name):
    try:
        return H["_app_by_name"](name)
    except RuntimeError as e:
        raise OpError(400, str(e))


def _seed(k=0):
    return int(time.time() * 1000 + k * 7919) % 2147483646 + 1


# ---------------------------------------------------------------- projects as the server keeps them
def get_project(pid):
    pr = _pj._get(pid, False)
    if not pr:
        raise OpError(404, "No such project (only regular projects: Incognito ones stay in the page)")
    return pr


def save_ai(pr):
    """Saved as an AI's change: the page takes it in (it watches /studio/api/stamps) rather than saving over it."""
    pr["by"] = BY_AI
    return _pj._put(pr)


def chars_by_id():
    return {c.get("id"): c for c in H["_load_chars"]()}


def scene_of(pr, s):
    return next((x for x in pr.get("scenes") or [] if x.get("id") == s.get("scene")), None)


def find_shot(pr, ref):
    """A shot by its id or its code (1A, 12B) or its place in the list (1-based)."""
    shots = pr.get("shots") or []
    s = next((x for x in shots if x.get("id") == ref), None)
    if s:
        return s
    codes = _sp.shot_codes(pr.get("scenes") or [], shots)
    s = next((x for x in shots if str(codes.get(x["id"], "")).upper() == str(ref).upper()), None)
    if not s and str(ref).isdigit() and 0 < int(ref) <= len(shots):
        s = shots[int(ref) - 1]
    if not s:
        raise OpError(404, f"No shot '{ref}' (use a shot code like 1A, or an id)")
    return s


def find_location(pr, ref):
    r = str(ref or "").strip().upper()
    loc = next((x for x in pr.get("locations") or [] if x.get("id") == ref), None) or \
        next((x for x in pr.get("locations") or [] if (x.get("name") or "").upper() == r or (x.get("full") or "").upper() == r), None) or \
        next((x for x in pr.get("locations") or [] if r and r in (x.get("name") or "").upper()), None)
    if not loc:
        raise OpError(404, f"No location '{ref}'. Locations: " + ", ".join(x.get("full") or x.get("name") or "" for x in pr.get("locations") or []))
    return loc


def find_character(ref, chars=None):
    chars = chars if chars is not None else H["_load_chars"]()
    r = str(ref or "").strip().lower()
    c = next((x for x in chars if x.get("id") == ref), None) or next((x for x in chars if (x.get("name") or "").lower() == r), None) or \
        next((x for x in chars if r and (x.get("name") or "").split(" ")[0].lower() == r), None)
    if not c:
        raise OpError(404, f"No character '{ref}'")
    if c.get("private"):
        raise OpError(403, "That character is in Incognito")
    return c


# ---------------------------------------------------------------- the screenplay's places become locations (syncLocations)
def sync_locations(pr):
    """The breakdown's places -> the project's locations, as the page's syncLocations: a new place is a new location, a
    place gone goes if nobody touched its location (else it stays, marked gone), a place renamed in the headings carries
    the location that was worked on."""
    places = _sp.places(pr.get("scenes") or [])
    by = {p["name"]: p for p in places}
    locs = pr.setdefault("locations", [])
    have = {(x.get("name") or "").upper() for x in locs}
    fresh = [p for p in places if p["name"] not in have]
    lost = [x for x in locs if not x.get("gone") and x.get("scenes") and (x.get("name") or "").upper() not in by]
    out = {"added": [], "removed": [], "renamed": [], "changed": False}
    if len(fresh) == 1 and len(lost) == 1 and lost[0].get("edited"):
        loc, p = lost[0], fresh.pop(0)
        out["renamed"].append([loc.get("full"), p["full"]])
        loc["full"] = loc["full"] if loc.get("full") and _sp.nice_name(loc.get("name") or "") != loc["full"] else p["full"]
        loc["name"] = p["name"]
        have.add(p["name"])
        out["changed"] = True
    for p in fresh:
        locs.append(_pj.new_location(p))
        out["added"].append(p["full"])
        out["changed"] = True
    keep = []
    for loc in locs:
        p = by.get((loc.get("name") or "").upper())
        if p:
            before = json.dumps([loc.get("intext"), loc.get("scenes"), bool(loc.get("gone"))])
            loc["intext"], loc["scenes"] = p["intext"], p["scenes"]
            loc.pop("gone", None)
            if not loc.get("edited") and not loc.get("description") and p.get("description"):
                loc["description"] = p["description"]
            out["changed"] |= json.dumps([loc.get("intext"), loc.get("scenes"), bool(loc.get("gone"))]) != before
            keep.append(loc)
            continue
        if not loc.get("scenes") and not loc.get("auto"):            # added by hand, in no scene yet
            keep.append(loc)
            continue
        if loc.get("auto") and not loc.get("edited") and not loc.get("picture"):
            out["removed"].append(loc.get("full"))
            out["changed"] = True
            continue
        if not loc.get("gone"):
            loc["gone"], loc["scenes"] = True, []
            out["changed"] = True
        keep.append(loc)
    pr["locations"] = keep
    return out


# ---------------------------------------------------------------- the screenplay's parts are cast (syncCast)
def char_from_role(r):
    """A character from a part of the screenplay: its introduction ("fifty-seven, canvas work jacket…") -> age and look."""
    intro = r.get("intro") or ""
    parts = re.split(r",\s*|\.\s+", intro)
    first = (parts[0] if parts else "").strip().rstrip(".")
    is_age = bool(_pj._NUMBER_AGE.match(first)) or bool(re.match(r"^\(?\d{2}s\)?$|^(early|mid|late) \w+$", first, re.I))
    age = first.replace("(", "").replace(")", "") if is_age else ""
    look = re.sub(r"^[,.]\s*", "", intro[intro.find(parts[0]) + len(parts[0]):]) if is_age else intro
    return {"name": r.get("full") or _sp.nice_name(r.get("name") or ""), "age": age, "look": look, "trigger": "", "voice": "", "face": ""}


def sheet_sig(c):
    return json.dumps([c.get(k) or "" for k in SHEET_KEYS], ensure_ascii=False, separators=(",", ":"))


def untouched(c):
    return sheet_sig(c) == c.get("autoSig") and not c.get("face") and not c.get("pictures") and not c.get("lora") and not c.get("voice")


def cast_of(pr):
    names = {r.get("name") for r in pr.get("roles") or []}
    out = []
    for role, cid in (pr.get("castMap") or {}).items():
        if cid and role in names and cid not in out:
            out.append(cid)
    return out


def _new_char(fields):
    c = dict(fields, id=uuid.uuid4().hex[:10], created=time.time(), updated=time.time())
    c.pop("description", None)
    return c


def sync_cast(pr):
    """The screenplay's speaking parts -> Studio characters, as the page's syncCast: a part is cast with the character of
    its name, else one made from the screenplay's introduction of it; a character made that way that nobody touched goes
    when its part does (a typo). A part left uncast on purpose ("") stays so."""
    chars = H["_load_chars"]()
    by_id = {c.get("id"): c for c in chars}
    cm = pr.setdefault("castMap", {})
    roles = pr.get("roles") or []
    names = {r.get("name") for r in roles}
    out = {"added": [], "removed": [], "changed": False}
    dirty = False
    for r in roles:
        if cm.get(r["name"]) == "" or (cm.get(r["name"]) and cm[r["name"]] in by_id):
            continue
        full = (r.get("full") or "").upper()
        c = next((x for x in chars if not x.get("private") and (x.get("name") or "").upper() == full), None) or \
            next((x for x in chars if not x.get("private") and (x.get("name") or "").split(" ")[0].upper() == (r.get("name") or "").split(" ")[0]), None)
        if not c:
            sheet = char_from_role(r)
            c = _new_char(dict(sheet, private=False, auto=pr["id"], autoSig=sheet_sig(sheet)))
            chars.append(c)
            by_id[c["id"]] = c
            out["added"].append(c["name"])
            dirty = True
        elif c.get("auto") and c["auto"] != pr["id"]:        # made for another project's cast, cast here too: nobody's typo now
            c.pop("auto", None)
            c.pop("autoSig", None)
            dirty = True
        cm[r["name"]] = c["id"]
        out["changed"] = True
    for role, cid in list(cm.items()):                       # a part gone from the screenplay
        if role in names:
            continue
        c = by_id.get(cid) if cid else None
        if not c or (c.get("auto") == pr["id"] and untouched(c)):
            if c:
                chars = [x for x in chars if x.get("id") != cid]
                out["removed"].append(c.get("name"))
                dirty = True
            del cm[role]
            out["changed"] = True
    if dirty:
        H["_save_chars"](chars)
    cast = cast_of(pr)
    if cast != (pr.get("cast") or []):
        pr["cast"] = cast
        out["changed"] = True
    return out


def follow(pr):
    """After the screenplay changed: its places and parts follow it, and the storyboard (once there is one) follows the
    breakdown. Saved. -> what changed."""
    locs = sync_locations(pr)
    cast = sync_cast(pr)
    board = None
    if pr.get("board"):
        board = _pj.to_board(pr, chars_by_id(), _render_lengths())
    save_ai(pr)
    return {"locations_added": locs["added"], "locations_removed": locs["removed"], "locations_renamed": locs["renamed"],
            "characters_added": cast["added"], "characters_removed": cast["removed"], "board": board and board["id"]}


def summary(pr):
    codes = _sp.shot_codes(pr.get("scenes") or [], pr.get("shots") or [])
    chars = chars_by_id()
    cm = pr.get("castMap") or {}
    return {"id": pr["id"], "title": pr.get("title"), "pages": pr.get("pages"), "tone": pr.get("tone"),
            "scenes": len([s for s in pr.get("scenes") or [] if s.get("heading")]), "shots": len(pr.get("shots") or []),
            "frames": len([s for s in pr.get("shots") or [] if s.get("keyframe")]),
            "roles": [{"role": r["name"], "character": (chars.get(cm.get(r["name"])) or {}).get("name"), "lines": r.get("lines")}
                      for r in pr.get("roles") or []],
            "locations": [x.get("full") or x.get("name") for x in pr.get("locations") or [] if not x.get("gone")],
            "board": pr.get("board"), "first_shots": [codes.get(s["id"]) for s in (pr.get("shots") or [])[:3]]}


def set_screenplay(pr, text, draft=None):
    """The screenplay replaced by `text` (Fountain): the text it had is kept as a draft (to compare or restore in the
    page), the breakdown is made again keeping what was decided on the shots (their ids, frames, cameras and lengths set
    by hand), and the locations and cast follow."""
    if not isinstance(text, str) or len(text) > 5_000_000:
        raise OpError(400, "The screenplay must be Fountain text, under 5 MB")
    sp = pr.setdefault("screenplay", {})
    old = sp.get("text") or ""
    if old.strip() and old != text:
        drafts = pr.setdefault("drafts", [])
        drafts.append({"id": _rid(), "name": draft or f"Before an AI edit ({time.strftime('%d %b %H:%M')})", "color": "", "at": time.time(),
                       "text": old, "pages": pr.get("pages") or 0})
        del drafts[:-40]
    parsed = _sp.parse(text)
    bd = _sp.breakdown(parsed, pr.get("scenes"), pr.get("shots"))
    sp.update(text=text, edited=time.time())
    pr.update(scenes=bd["scenes"], shots=bd["shots"], roles=bd["characters"], pages=parsed["pages"])
    if parsed.get("title") and not (pr.get("title") or "").strip():
        pr["title"] = pr["name"] = parsed["title"][:120]
    if pr.get("stage") in (None, "", "Idea"):
        pr["stage"] = "Screenplay"
    return follow(pr)


def new_project(b):
    now = time.time()
    title = (b.get("title") or "").strip()[:120]
    pr = {"id": uuid.uuid4().hex[:10], "created": now, "name": title or "Untitled", "title": title, "tone": b.get("tone") or "Cinematic drama",
          "cast": [], "castMap": {}, "private": False, "stage": "Screenplay", "step": "screenplay", "scenes": [], "shots": [], "roles": [],
          "locations": [], "screenplay": {"text": "", "source": "ai", "created": now}}
    if b.get("idea"):
        pr["idea"] = str(b["idea"])[:4000]
    if b.get("screenplay"):
        out = set_screenplay(pr, b["screenplay"])
        if not title and pr.get("title"):
            pr["name"] = pr["title"]
    else:
        out = {}
        save_ai(pr)
    return pr, out


# ---------------------------------------------------------------- reference pictures (character, location) and frames
def portrait_prompt(c):
    return (f"Character reference portrait of {c['name']}: {_pj.char_desc(c)}. Head and shoulders, facing the camera, plain light grey "
            "background, soft even studio light, sharp focus, natural skin texture, film still.")


def location_prompt(loc):
    return ". ".join(x for x in [f"{loc.get('full') or _sp.nice_name(loc.get('name') or '')}" + (f": {loc['look']}" if loc.get("look") else ""),
                                 (loc["lighting"] if re.search(r"\blight", loc["lighting"], re.I) else f"{loc['lighting']} light") if loc.get("lighting") else "", loc.get("time") or "", loc.get("atmosphere") or "",
                                 "an empty set, no people, film still"] if x)


def image_request(model=None, values=None):
    """The body the picture routes take (projects.py: portrait, location picture, keyframes) for the Image app."""
    app = _app("image")
    vals, L = app_values(app, model, values)
    ref = next((r for r in L["refs"] if r["media"]["kind"] == "image"), None)
    return app, vals, L, {"values": vals, "prompt_key": (L["prompt"] or {}).get("key"), "ref_key": ref and ref["media"]["key"],
                          "ref_toggle_key": ref and ref["toggle"] and ref["toggle"]["key"], "seed_keys": L["seeds"]}


def portrait_body(c, b):
    """A character's reference picture for a client without the page: the Image app's values (the model asked for), the
    page's prompt from the character sheet, the face as a reference if there is one, and the character's LoRA if it fits."""
    app, vals, L, req = image_request(b.get("model"), b.get("values"))
    fam = _pj._family(str(vals.get((L["model"] or {}).get("key")) or ""))
    lo = c.get("lora") or {}
    loras = [{"name": lo["name"], "strength": float(lo.get("strength") or 0.8)}] if lo.get("name") and not (lo.get("family") and fam and lo["family"] != fam) else []
    return dict(b, **req, prompt=b.get("prompt") or portrait_prompt(c), ref=b.get("ref", c.get("face") or ""), loras=loras)


def location_body(loc, b):
    """A location's reference picture for a client without the page: the Image app's values and the page's prompt."""
    app, vals, L, req = image_request(b.get("model"), b.get("values"))
    return dict(b, **req, prompt=b.get("prompt") or location_prompt(loc))


def people_in(pr, s, chars):
    """The cast characters in a shot: named in its characters or its action (the page's people)."""
    text = " ".join(s.get("characters") or []) + " " + (s.get("action") or "")
    out = []
    for cid in pr.get("cast") or []:
        c = chars.get(cid)
        if c and re.search(r"\b" + re.escape(c["name"].split(" ")[0]) + r"\b", text, re.I):
            out.append(c)
    return out


def frame_items(pr, shots, model_option, has_ref):
    """The storyboard's frames as the page asks for them: each shot's prompt (its action with the looks written in, where
    and when with the location's look, the style), its reference (the face of the one cast character in it who has one;
    with nobody in it, its location's picture) and its characters' LoRAs that fit the model."""
    chars = chars_by_id()
    fam = _pj._family(model_option)
    items = []
    for s in shots:
        inshot = people_in(pr, s, chars)
        faces = [c for c in inshot if c.get("face")]
        loras = []
        for c in inshot:
            lo = c.get("lora") or {}
            if lo.get("name") and not (lo.get("family") and fam and lo["family"] != fam) and lo["name"] not in [x["name"] for x in loras]:
                loras.append({"name": lo["name"], "strength": float(lo.get("strength") or 0.8)})
        place = _pj.location_of(pr, scene_of(pr, s)) or {}
        ref = ""
        if pr.get("useRefs") is not False and has_ref:
            if len(faces) == 1:
                ref = faces[0]["face"]                     # the one character in it with a face
            elif not inshot:
                ref = place.get("picture") or ""           # nobody in it: its location's picture
        items.append({"id": s["id"], "prompt": _pj.shot_text(pr, s, False, chars) + (f" {pr['tone']} style, film still." if pr.get("tone") else ""),
                      "ref": ref, "loras": loras})
    return items


# ---------------------------------------------------------------- takes (the board's shots rendered with the Video app)
def _render_lengths():
    app = H["_app_by_name"]("video") if _has_app("video") else None
    dur = next((i for i in (app or {}).get("inputs", []) if str(i.get("label", "")).lower().startswith("duration")), None)
    if dur and dur.get("options"):
        return tuple(sorted(float(re.sub(r"[^\d.]", "", o) or 0) for o in dur["options"])) or (5, 8, 10, 15)
    return (5, 8, 10, 15)


def _has_app(name):
    try:
        H["_app_by_name"](name)
        return True
    except RuntimeError:
        return False


def with_camera(prompt, camera):
    p, c = (prompt or "").strip(), (camera or "").strip()
    if not c:
        return p
    cam = c[0].upper() + c[1:] + ("" if re.search(r"[.!?]$", c) else ".")
    return (p + (" " if re.search(r"[.!?\"”…]$", p) else ". ") + cam) if p else cam


def render_length(dur, secs):
    opts = dur.get("options") or []
    return next((o for o in opts if float(re.sub(r"[^\d.]", "", o) or 0) >= float(secs) - 0.2), opts[-1] if opts else None)


def take_values(app, bs, quality, model=None, values=None, seed=None):
    """The Video app's values for a board shot, as the page's renderShot: its prompt with its camera, its length (the
    shortest take that holds it), its first frame (the first image slot; the other media off), draft or final quality."""
    vals, L = app_values(app, model, values)
    if L["prompt"]:
        vals[L["prompt"]["key"]] = with_camera(bs.get("prompt"), bs.get("camera"))
    if L["duration"] and bs.get("seconds"):
        vals[L["duration"]["key"]] = render_length(L["duration"], bs["seconds"])
    mdl = str(vals.get(L["model"]["key"]) or "") if L["model"] else ""
    if not bs.get("image") and re.search(r"first frame\)|image 1 = first frame", mdl, re.I) and not re.search(r"text only", mdl, re.I):
        raise OpError(409, f"{mdl.split('(')[0].strip()} needs a first frame: make the storyboard frames first (or give the shot a frame), "
                           "or pick a model that starts from text")
    first = True
    for r in L["refs"]:
        if r["media"]["kind"] == "image" and first:
            first = False
            if bs.get("image"):
                vals[r["media"]["key"]] = bs["image"]
                if r["toggle"]:
                    vals[r["toggle"]["key"]] = True
            elif r["toggle"]:
                vals[r["toggle"]["key"]] = False
        elif r["toggle"]:
            vals[r["toggle"]["key"]] = False
    if L["quality"] and quality:
        want = next((o for o in L["quality"]["options"] if re.search(r"final|full" if quality == "final" else r"draft|turbo", o, re.I)), None)
        if want:
            vals[L["quality"]["key"]] = want
    seeds = {}
    for k, key in enumerate(L["seeds"]):
        vals[key] = seeds[key] = int(seed) if seed is not None and k == 0 else _seed(k)
    return vals, (seeds[L["seeds"][0]] if L["seeds"] else None)


def board_of(pr):
    boards = H["_load_boards"]()
    return boards, next((x for x in boards if x.get("id") == pr.get("board")), None)


def _ensure_takes(s):
    if "takes" not in s:
        s["takes"] = [{"pid": s.get("prompt_id"), "output": s.get("output"), "status": "done" if s.get("output") else "", "seed": None, "q": ""}] \
            if (s.get("output") or s.get("prompt_id")) else []
        s["pick"] = 0 if s.get("output") else None
    return s


def _sync_pick(s):
    t = s["takes"][s["pick"]] if s.get("pick") is not None and 0 <= s["pick"] < len(s["takes"]) else None
    s["output"] = t["output"] if t and t.get("output") else None


def _output_of(pid):
    """A render's file: ComfyUI's history (this PC), or a job Studio sent to another GPU machine. (state, file or error)."""
    try:
        from server import PromptServer
        h = (PromptServer.instance.prompt_queue.get_history(prompt_id=pid) or {}).get(pid)
    except Exception:
        h = None
    if h:
        for o in (h.get("outputs") or {}).values():
            for f in (o.get("images") or []) + (o.get("gifs") or []) + (o.get("videos") or []):
                if f.get("type") == "output":
                    return "done", {"filename": f["filename"], "subfolder": re.sub(r"^\.private_incoming/?", "", str(f.get("subfolder") or "").replace("\\", "/"))}
        st = h.get("status") or {}
        if st.get("status_str") == "error":
            return "failed", None
        return ("failed", None) if st.get("completed") else ("running", None)
    rj = getattr(H.get("_remote"), "JOBS", {}).get(pid)
    if rj:
        if rj.get("status") == "done" and rj.get("outputs"):
            o = rj["outputs"][0]
            return "done", {"filename": o["filename"], "subfolder": re.sub(r"^\.private_incoming/?", "", str(o.get("subfolder") or "").replace("\\", "/"))}
        return ("failed", None) if rj.get("status") in ("failed", "cancelled") else ("running", None)
    return "unknown", None


def resolve_board(b):
    """The takes' files, as the page's resolveShots: a finished take gets its file (a final-quality one becomes the pick,
    as does the first one of a shot without a pick). -> whether anything changed."""
    changed = False
    for s in b.get("shots") or []:
        _ensure_takes(s)
        for k, t in enumerate(s["takes"]):
            if not t.get("pid") or t.get("output") or t.get("status") in ("failed", "lost"):
                continue
            state, f = _output_of(t["pid"])
            if state == "done":
                t.update(output=f, status="done")
                if s.get("pick") is None or t.get("q") == "final":
                    s["pick"] = k
                changed = True
            elif state == "failed":
                t["status"] = "failed"
                changed = True
        _sync_pick(s)
    return changed


def save_board(boards, b):
    b["updated"], b["by"] = time.time(), BY_AI
    H["_save_boards"]([x for x in boards if x.get("id") != b["id"]] + [b])


def board_view(b):
    """A board's shots and their takes, for a client to read."""
    out = []
    for s in b.get("shots") or []:
        _ensure_takes(s)
        out.append({"id": s["id"], "code": s.get("code"), "title": s.get("title"), "seconds": s.get("seconds"), "prompt": s.get("prompt"),
                    "first_frame": s.get("image") or "", "pick": s.get("pick"),
                    "takes": [{"n": k, "status": t.get("status") or ("done" if t.get("output") else ""), "quality": t.get("q") or "",
                               "seed": t.get("seed"), "prompt_id": t.get("pid"), "file": t.get("output")} for k, t in enumerate(s["takes"])],
                    "final": s.get("final")})
    return {"id": b["id"], "name": b.get("name"), "film": b.get("film"), "shots": out}


async def draft_takes(pr, b):
    """Takes of the project's shots with the Video app, queued now and put on its board (made from the breakdown first)."""
    app = _app("video")
    chars = chars_by_id()
    board = _pj.to_board(pr, chars, _render_lengths())
    pr["board"] = board["id"]
    if pr.get("stage") not in ("Storyboard", "Film"):
        pr["stage"] = "Storyboard"
    save_ai(pr)
    boards = H["_load_boards"]()
    board = next(x for x in boards if x["id"] == pr["board"])
    want = [str(w).upper() for w in b.get("shots") or []]

    def asked(s):                                          # a shot named by its code (1A), its breakdown id or its board id
        return any(w in (str(s.get("code") or "").upper(), str(s.get("projShot") or "").upper(), str(s.get("id") or "").upper()) for w in want)
    todo = []
    for s in board["shots"]:
        _ensure_takes(s)
        if want and not asked(s):
            continue
        if not want and any(t.get("output") or t.get("status") == "queued" for t in s["takes"]):
            continue                                       # as the page's "Draft every shot without takes"
        todo.append((s, next((x for x in pr.get("shots") or [] if x["id"] == s.get("projShot")), None)))
    if want and len(todo) < len(want):
        raise OpError(404, "Not every shot asked for is on the board. Shots: " + ", ".join(str(x.get("code")) for x in board["shots"]))
    count = max(1, min(4, int(b.get("count") or board.get("takes") or 2)))
    quality = "final" if b.get("quality") == "final" else "draft"
    for s, _ps in todo:                                    # checked before anything is queued: all or nothing
        take_values(app, s, quality, b.get("model"), b.get("values"))
    queued = []
    mkey = (app_inputs(app)["model"] or {}).get("key")
    for s, ps in todo:
        for k in range(count):
            vals, seed = take_values(app, s, quality, b.get("model"), b.get("values"), b.get("seed"))
            loras = _pj.char_loras(people_in(pr, ps or {}, chars), str(vals.get(mkey) or ""))   # the LoRAs of who's in it, that fit the model
            pid = await H["_queue_app"](app, vals, False, f"Take {s.get('code') or ''}: {(s.get('title') or '')[:40]}", b.get("target"), loras)
            s["takes"].append({"pid": pid, "seed": seed, "q": quality, "status": "queued", "output": None, "from": None, "prompt": s.get("prompt")})
            queued.append({"shot": s.get("code"), "board_shot": s["id"], "prompt_id": pid, "seed": seed})
    save_board(boards, board)
    return board, queued


# ---------------------------------------------------------------- the film
def clip_of(s):
    if s.get("final") and s.get("finish") and s["finish"].get("from") == s.get("pick"):
        return s["final"]
    return s.get("output")


# ---------------------------------------------------------------- routes
def _answer(fn):
    async def wrapped(request):
        try:
            return await fn(request)
        except OpError as e:
            return web.json_response({"error": str(e)}, status=e.status)
        except (_sp.ScreenplayError, RuntimeError) as e:    # Studio's own refusals, in words (a file gone, an app missing)
            return web.json_response({"error": str(e)}, status=400)
    return wrapped


async def _body(request):
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def setup(ns):
    H.update(ns)
    routes = ns["routes"]
    new_job, ai_job = ns["_new_job"], ns["_ai_job"]

    def background(coro):
        return _pj._background(coro)

    @routes.post("/studio/api/projects/new")
    @_answer
    async def project_new(request):
        pr, out = await asyncio.to_thread(new_project, await _body(request))
        return web.json_response({"project": summary(pr), "followed": out})

    @routes.post("/studio/api/projects/{pid}/screenplay")
    @_answer
    async def project_screenplay(request):
        b = await _body(request)
        pr = get_project(request.match_info["pid"])
        out = await asyncio.to_thread(set_screenplay, pr, b.get("text"), b.get("draft"))
        return web.json_response({"project": summary(pr), "followed": out})

    @routes.post("/studio/api/projects/{pid}/follow")
    @_answer
    async def project_follow(request):
        pr = get_project(request.match_info["pid"])
        out = await asyncio.to_thread(follow, pr)
        return web.json_response({"project": summary(pr), "followed": out})

    @routes.post("/studio/api/projects/{pid}/cast")
    @_answer
    async def project_cast(request):
        b = await _body(request)
        pr = get_project(request.match_info["pid"])
        role = next((r for r in pr.get("roles") or [] if r["name"].upper() == str(b.get("role") or "").upper()
                     or (r.get("full") or "").upper() == str(b.get("role") or "").upper()), None)
        if not role:
            raise OpError(404, "No such part in the screenplay. Parts: " + ", ".join(r["name"] for r in pr.get("roles") or []))
        c = find_character(b["character"]) if b.get("character") else None
        if c and c.get("auto") and c["auto"] != pr["id"]:
            chars = H["_load_chars"]()
            for x in chars:
                if x.get("id") == c["id"]:
                    x.pop("auto", None)
                    x.pop("autoSig", None)
            H["_save_chars"](chars)
        pr.setdefault("castMap", {})[role["name"]] = c["id"] if c else ""
        pr["cast"] = cast_of(pr)
        if pr.get("board"):
            _pj.to_board(pr, chars_by_id(), _render_lengths())
        save_ai(pr)
        return web.json_response({"project": summary(pr)})

    @routes.post("/studio/api/projects/{pid}/locations/{lid}")
    @_answer
    async def project_location(request):
        b = await _body(request)
        pr = get_project(request.match_info["pid"])
        loc = find_location(pr, request.match_info["lid"])
        renamed = 0
        for k in LOCATION_FIELDS:
            if b.get(k) is not None:
                loc[k] = str(b[k]).strip()
        if b.get("picture") is not None:
            loc["picture"] = b["picture"] or ""
            if b["picture"]:
                loc["pictures"] = [b["picture"]] + [p for p in loc.get("pictures") or [] if p != b["picture"]]
            loc["pic_at"] = time.time()
        if b.get("name"):
            name = re.sub(r"\s+", " ", str(b["name"]).strip().upper())
            if any(x is not loc and (x.get("name") or "").upper() == name for x in pr.get("locations") or []):
                raise OpError(409, f"There's already a location called {name}")
            was = (loc.get("name") or "").upper()
            if name != was:
                loc["full"] = _sp.nice_name(name)
                loc["name"] = name
                if b.get("rename_in_screenplay", True) and loc.get("scenes"):
                    text, renamed = rename_place((pr.get("screenplay") or {}).get("text") or "", was, name)
                    if renamed:
                        loc["edited"] = True
                        set_screenplay(pr, text, f"Before renaming {was}")
        loc["edited"] = True
        if pr.get("board"):
            _pj.to_board(pr, chars_by_id(), _render_lengths())
        save_ai(pr)
        return web.json_response({"location": loc, "renamed_in_headings": renamed})

    @routes.post("/studio/api/projects/{pid}/shots/{sid}")
    @_answer
    async def project_shot(request):
        b = await _body(request)
        pr = get_project(request.match_info["pid"])
        s = find_shot(pr, request.match_info["sid"])
        if b.get("camera"):
            cam = str(b["camera"]).lower().strip()
            if cam not in _pj.CAMS_WORDS:
                raise OpError(400, "camera must be one of: " + ", ".join(_pj.CAMS_WORDS))
            s.update(camera=cam, camera_set=True)
        if b.get("seconds"):
            s.update(seconds=max(1, min(120, int(float(b["seconds"])))), seconds_set=True)
        if b.get("frame") is not None:
            if pr.get("boardLocked"):
                raise OpError(409, "The storyboard is locked in the page: unlock it to change a frame")
            value = b["frame"] or ""
            if s.get("keyframe") and s["keyframe"] != value:
                s["kf_versions"] = ([s["keyframe"]] + [v for v in s.get("kf_versions") or [] if v not in (s["keyframe"], value)])[:8]
            s["keyframe"] = value
            s.pop("keyframe_out", None)
            s["kf_at"] = time.time()
            if value:
                s["kf_src"] = _pj.shot_source(s, scene_of(pr, s))
            else:
                s.pop("kf_src", None)
        if pr.get("board"):
            _pj.to_board(pr, chars_by_id(), _render_lengths())
        save_ai(pr)
        return web.json_response({"shot": s})

    @routes.post("/studio/api/projects/{pid}/takes")
    @_answer
    async def project_takes(request):
        b = await _body(request)
        pr = get_project(request.match_info["pid"])
        if not pr.get("shots"):
            raise OpError(409, "No shots yet: they come from the screenplay's scenes")
        board, queued = await draft_takes(pr, b)
        return web.json_response({"board": board["id"], "queued": queued})

    @routes.post("/studio/api/boards/{bid}/resolve")
    @_answer
    async def board_resolve(request):
        boards = H["_load_boards"]()
        board = next((x for x in boards if x.get("id") == request.match_info["bid"]), None)
        if not board or board.get("private"):
            raise OpError(404, "No such board")
        if resolve_board(board):
            save_board(boards, board)
        return web.json_response(board_view(board))

    @routes.post("/studio/api/boards/{bid}/shots/{sid}/pick")
    @_answer
    async def board_pick(request):
        b = await _body(request)
        boards = H["_load_boards"]()
        board = next((x for x in boards if x.get("id") == request.match_info["bid"]), None)
        if not board or board.get("private"):
            raise OpError(404, "No such board")
        sid = request.match_info["sid"]
        s = next((x for x in board["shots"] if sid in (x.get("id"), x.get("projShot")) or str(x.get("code") or "").upper() == sid.upper()), None)
        if not s:
            raise OpError(404, "No such shot on the board")
        _ensure_takes(s)
        resolve_board(board)
        k = int(b.get("take", -1))
        if not (0 <= k < len(s["takes"])) or not s["takes"][k].get("output"):
            raise OpError(400, f"Take {k} isn't rendered (the shot has {len(s['takes'])} take(s); numbered from 0)")
        s["pick"] = k
        _sync_pick(s)
        save_board(boards, board)
        return web.json_response(board_view(board))

    @routes.post("/studio/api/projects/{pid}/music")
    @_answer
    async def project_music(request):
        b = await _body(request)
        pr = get_project(request.match_info["pid"])
        style = (b.get("style") or pr.get("music") or "").strip()
        if not style:
            raise OpError(400, "Describe the music: genre, mood, instruments (or set the project's music first)")
        pr["music"] = style
        save_ai(pr)
        total = sum(float(s.get("seconds") or 5) for s in pr.get("shots") or []) or 60
        body = {"tags": style, "instrumental": True, "engine": b.get("engine") or "ace", "seconds": b.get("seconds") or min(240, max(15, total)),
                "name": (pr.get("title") or "Film") + " score", "private": False}
        api, seed = _sound.music_prompt(body)
        job = new_job(False, kind="project-music")

        async def run():
            job["stage"] = "Waiting in the queue"
            pid = await H["_queue_api"](api, {"studio_app": "sound:music", "studio_label": f"Music: {pr.get('title') or 'film'}"}, False, b.get("target"), "studio-sound")
            job["pids"].append(pid)
            job["stage"] = "Composing"
            fn, sub = await H["_wait_output"](pid, job)
            res = {"filename": fn, "subfolder": (sub or "").replace("\\", "/"), "private": False, "seed": seed}
            p2 = get_project(pr["id"])
            p2["music_file"] = res
            save_ai(p2)
            return res
        background(ai_job(job, run))
        return web.json_response({"id": job["id"]})

    @routes.post("/studio/api/projects/{pid}/voices")
    @_answer
    async def project_voices(request):
        b = await _body(request)
        pr = get_project(request.match_info["pid"])
        codes = _sp.shot_codes(pr.get("scenes") or [], pr.get("shots") or [])
        chars = chars_by_id()
        def voiced(s, k):
            vf = s.get("voice_files") or []
            return k < len(vf) and bool(vf[k])
        todo = [(s["id"], k, ln) for s in pr.get("shots") or [] for k, ln in enumerate(s.get("lines") or [])
                if ln.get("text") and (b.get("redo") or not voiced(s, k))]
        job = new_job(False, kind="project-voices")

        async def run():
            done = 0
            for n, (sid, k, ln) in enumerate(todo):
                if job.get("cancel"):
                    raise RuntimeError("Stopped")
                who = chars.get((pr.get("castMap") or {}).get(ln.get("who")))
                code = codes.get(sid, "")
                job["stage"] = f"Line {n + 1} of {len(todo)}: {_sp.nice_name(ln.get('who') or '')} (shot {code})"
                api, _seed_ = _sound.voice_prompt({"text": ln["text"], "voice": (who or {}).get("voice") or "",
                                                   "name": f"{pr.get('title') or 'Film'} - shot {code} - {_sp.nice_name(ln.get('who') or '')}"})
                pid = await H["_queue_api"](api, {"studio_app": "sound:voice", "studio_label": f"Voice: {code}"}, False, b.get("target"), "studio-sound")
                job["pids"].append(pid)
                fn, sub = await H["_wait_output"](pid, job)
                p2 = get_project(pr["id"])
                for s in p2.get("shots") or []:
                    if s["id"] == sid:
                        vf = s.setdefault("voice_files", [])
                        while len(vf) <= k:
                            vf.append(None)
                        vf[k] = {"filename": fn, "subfolder": (sub or "").replace("\\", "/"), "private": False}
                save_ai(p2)
                done += 1
            return {"lines": done}
        background(ai_job(job, run))
        return web.json_response({"id": job["id"], "lines": len(todo)})

    @routes.post("/studio/api/projects/{pid}/film")
    @_answer
    async def project_film(request):
        b = await _body(request)
        pr = get_project(request.match_info["pid"])
        boards, board = board_of(pr)
        if not board:
            raise OpError(409, "No takes yet: draft the shots' takes first")
        resolve_board(board)
        shots = [dict(c, **{"in": s.get("trimIn") or 0, "out": s.get("trimOut") or 0, "caption": s.get("caption") or ""})
                 for s in board["shots"] for c in [clip_of(s)] if c]
        if not shots:
            raise OpError(409, "None of the shots has a rendered take yet")
        e = dict(board.get("edit") or {"crossfade": 0.5, "title": {"text": board.get("name"), "sub": "", "secs": 3}, "end": {"text": "", "sub": "", "secs": 3},
                                       "music": "", "music_vol": 0.35, "keep_audio": True})
        for k in ("crossfade", "music_vol", "keep_audio"):
            if b.get(k) is not None:
                e[k] = b[k]
        if b.get("title") is not None:
            e["title"] = {"text": str(b["title"]), "sub": str(b.get("subtitle") or ""), "secs": 3}
        if b.get("end") is not None:
            e["end"] = {"text": str(b["end"]), "sub": str(b.get("end_sub") or ""), "secs": 3}
        if b.get("music") == "project":
            mf = pr.get("music_file")
            if not mf:
                raise OpError(409, "The project has no music yet: make it first")
            e["music"] = H["_to_input"](H["_out_path"](mf["filename"], mf.get("subfolder", ""), False), False)
        elif b.get("music") is not None:
            e["music"] = b["music"] or ""
        board["edit"] = e
        save_board(boards, board)
        bid = board["id"]

        def filed(res):                                    # the film on its board and the project at its last step, as the page does
            bs = H["_load_boards"]()
            bb = next((x for x in bs if x.get("id") == bid), None)
            if bb:
                bb["film"] = res
                save_board(bs, bb)
            p2 = get_project(pr["id"])
            p2["stage"] = "Film"
            save_ai(p2)
        job = H["_edit_job"](dict(e, shots=shots, name=board.get("name") or pr.get("title") or "Film", private=False), False, filed)
        return web.json_response({"id": job["id"], "shots": len(shots), "of": len(board["shots"])})

    @routes.get("/studio/api/stamps")
    async def stamps(request):
        """When each (regular) project and board last changed, and who changed it: the page takes an AI's changes in."""
        return web.json_response({"projects": {p["id"]: [p.get("updated") or 0, p.get("by") or ""] for p in _pj._load(False)},
                                  "boards": {x["id"]: [x.get("updated") or 0, x.get("by") or ""] for x in H["_load_boards"]() if not x.get("private")}})


def rename_place(text, old, new):
    """A place renamed in the screenplay's scene headings (the page's renamePlace): (the new text, how many headings)."""
    n = 0
    out = []
    rx = re.compile(r"^(\s*\.?(?:INT\.?/EXT|EXT\.?/INT|INT|EXT|EST|I/E|E/I)[. ]\s*)(.*?)(\s+[-–—]+\s+[^#]*?)?(\s*#[\w.\-]+#\s*)?$", re.I)
    for ln in text.split("\n"):
        m = rx.match(ln)
        if m and m.group(2).strip().upper() == old:
            n += 1
            ln = m.group(1) + new + (m.group(3) or "") + (m.group(4) or "")
        out.append(ln)
    return "\n".join(out), n
