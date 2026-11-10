"""Projects: idea -> script -> cast -> keyframes -> storyboard -> sound -> film.

A project keeps the plan (the AI-written script and shot list) and links to the pieces Studio already has:
Characters for the cast, the Image app for keyframes, a Storyboard board for the takes, Music/Voices for the sound.
Incognito projects live in the private space."""
import asyncio
import json
import os
import re
import time
import uuid

from aiohttp import web

H = {}
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


CAMS_WORDS = {"static": "static locked-off camera", "push in": "slow dolly push-in toward the subject", "pull back": "slow dolly pull-back revealing the scene",
              "pan left": "smooth pan left", "pan right": "smooth pan right", "tracking": "tracking shot following the subject",
              "orbit": "camera orbits around the subject", "crane up": "crane shot rising up", "crane down": "crane shot descending",
              "handheld": "handheld camera with natural shake", "close-up": "tight close-up", "low angle": "low-angle hero shot",
              "drone": "aerial drone flyover", "slow motion": "slow motion"}


def shot_text(proj, s, with_camera, chars):
    """Same as the page's shotText: characters' looks written in, plus the camera move."""
    t = s.get("action") or ""
    for cid in proj.get("cast", []):
        c = chars.get(cid)
        if not c:
            continue
        first = c["name"].split(" ")[0]
        look = ", ".join(x for x in [c.get("trigger"), c.get("look")] if x)
        if look and re.search(r"\b" + re.escape(first) + r"\b", t, re.I) and look not in t:
            t = re.sub(r"\b(" + re.escape(c["name"]) + "|" + re.escape(first) + r")\b", lambda m: f"{m.group(1)} ({look})", t, count=1, flags=re.I)
    if with_camera and CAMS_WORDS.get(s.get("camera")):
        t = t.rstrip(". ") + ". " + CAMS_WORDS[s["camera"]] + "."
    return t


def to_board(proj, chars):
    boards = H["_load_boards"]()
    b = next((x for x in boards if x.get("id") == proj.get("board")), None)
    if not b:
        b = {"id": uuid.uuid4().hex[:10], "name": proj.get("title") or "Project film", "shots": [], "private": bool(proj.get("private")), "created": time.time()}
        boards.append(b)
    b["name"] = proj.get("title") or b["name"]
    old = b.get("shots", [])
    new = []
    for s in proj.get("shots", []):
        prev = next((x for x in old if x.get("projShot") == s["id"]), None)
        text = shot_text(proj, s, True, chars)
        if prev:
            if not prev.get("takes"):
                prev["prompt"] = text
                prev["image"] = s.get("keyframe") or prev.get("image", "")
            new.append(prev)
        else:
            new.append({"id": uuid.uuid4().hex[:8], "prompt": text, "image": s.get("keyframe") or "", "camera": "", "projShot": s["id"]})
    b["shots"] = new
    b["updated"] = time.time()
    H["_save_boards"](boards)
    return b


def setup(ns):
    H.update(ns)
    routes = ns["routes"]
    _need_private, _flag, _new_job, _ai_job = ns["_need_private"], ns["_flag"], ns["_new_job"], ns["_ai_job"]

    def _check(request, private):
        if private:
            _need_private(request)

    @routes.get("/manny/api/projects")
    async def lst(request):
        out = list(_load(False))
        if _flag(request.query.get("private")):
            _need_private(request)
            out += _load(True)
        out.sort(key=lambda x: -x.get("updated", 0))
        return web.json_response(out)

    @routes.post("/manny/api/projects")
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
        return web.json_response(_put(b))

    @routes.delete("/manny/api/projects/{pid}")
    async def delete(request):
        pid = request.match_info["pid"]
        for private in (False, True):
            if _get(pid, private):
                _check(request, private)
                _save_all(private, [x for x in _load(private) if x.get("id") != pid])
        return web.json_response({"ok": True})

    @routes.post("/manny/api/projects/script")
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
        asyncio.ensure_future(_ai_job(job, run))
        return web.json_response({"id": job["id"]})

    @routes.post("/manny/api/projects/{pid}/keyframes")
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
        if not pkey:                                # called by Claude: work it out from the Image app and the project
            ins = app["inputs"]
            pkey = next((i["key"] for i in ins if i["kind"] == "text" and "negative" not in i["label"].lower()), None)
            rkey = next((i["key"] for i in ins if i["kind"] == "image"), None)
            tkey = next((i["key"] for i in ins if i["kind"] == "toggle" and "reference" in i["label"].lower()), None)
            b.setdefault("seed_keys", [i["key"] for i in ins if i["kind"] == "seed"])
            if b.get("model"):
                mdl = next((i for i in ins if i["kind"] == "choice" and "model" in i["label"].lower()), None)
                want = mdl and next((o for o in mdl["options"] if b["model"].lower() in o.lower()), None)
                if want:
                    base[mdl["key"]] = want
            if not jobs:
                chars = {c.get("id"): c for c in ns["_load_chars"]()}
                for s in proj.get("shots", []):
                    if s.get("keyframe") and not b.get("redo"):
                        continue
                    cast = [chars[c] for c in proj.get("cast", []) if c in chars]
                    inshot = [c for c in cast if c.get("face") and re.search(r"\b" + re.escape(c["name"].split(" ")[0]) + r"\b", " ".join(s.get("characters") or []) + " " + (s.get("action") or ""), re.I)]
                    jobs.append({"id": s["id"], "prompt": shot_text(proj, s, False, chars) + (f". {proj['tone']} style, film still" if proj.get("tone") else ""),
                                 "ref": inshot[0]["face"] if len(inshot) == 1 else ""})
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
                pidq = await ns["_queue_app"](app, vals, private, f"Project keyframe {k + 1}")
                job["pids"].append(pidq)
                fn, sub = await ns["_wait_output"](pidq, job)
                full = await asyncio.to_thread(ns["_find_out"], fn, sub, private)
                value = ns["_to_input"](full, private)
                p2 = _get(pid, private)
                if p2:
                    for sh in p2.get("shots", []):
                        if sh.get("id") == s.get("id"):
                            sh["keyframe"] = value
                            sh["keyframe_out"] = {"filename": fn, "subfolder": re.sub(r"^\.private_incoming/?", "", sub.replace("\\", "/")), "private": private}
                    _put(p2)
                done += 1
            return {"done": done}
        asyncio.ensure_future(_ai_job(job, run))
        return web.json_response({"id": job["id"]})

    @routes.post("/manny/api/projects/{pid}/to_board")
    async def to_board_route(request):
        b = await request.json()
        private = bool(b.get("private"))
        _check(request, private)
        proj = _get(request.match_info["pid"], private)
        if not proj:
            raise web.HTTPNotFound(text="No such project")
        chars = {c.get("id"): c for c in ns["_load_chars"]()}
        board = to_board(proj, chars)
        proj["board"] = board["id"]
        proj["stage"] = "Storyboard"
        _put(proj)
        return web.json_response({"board": board["id"], "name": board["name"], "shots": len(board["shots"])})
