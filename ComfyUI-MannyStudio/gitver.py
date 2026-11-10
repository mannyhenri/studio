"""Versions: Studio's code and apps kept in a git repo on this PC and pushed to GitHub.

"Save a version" copies the Studio pack (this folder) and the *.app.json apps into the repo, commits and pushes.
Pushing uses your own git login on this PC (Git Credential Manager opens a browser sign-in the first time);
Studio never stores a GitHub password or token."""
import asyncio
import json
import os
import shutil
import subprocess
import time

from aiohttp import web

H = {}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
PACK = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {"repo": os.path.join(os.path.expanduser("~"), "Documents", "code", "studio"),
            "remote": "https://github.com/mannyhenri/studio.git", "name": "mannyhenri", "email": "manny@gridlight.ai", "branch": "main"}
SKIP = {"__pycache__", ".git", ".topaz_tmp"}


def prefs():
    p = dict(DEFAULTS)
    try:
        with open(os.path.join(H["_data_dir"](), "git.json"), encoding="utf-8") as f:
            p.update({k: v for k, v in json.load(f).items() if k in DEFAULTS and v})
    except Exception:
        pass
    return p


def _git(args, cwd=None, timeout=120):
    try:
        r = subprocess.run(["git", *args], cwd=cwd or prefs()["repo"], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except FileNotFoundError:
        return 127, "git isn't installed"
    except subprocess.TimeoutExpired:
        return 124, "git took too long (a sign-in window may be waiting)"
    out = (r.stdout or b"").decode("utf-8", "ignore") + (r.stderr or b"").decode("utf-8", "ignore")
    return r.returncode, out.strip()


def _mirror(src, dst):
    """Copy src into dst; files gone from src are removed from dst (so deletions are versioned too)."""
    os.makedirs(dst, exist_ok=True)
    names = set()
    for n in os.listdir(src):
        if n in SKIP or n.endswith(".pyc"):
            continue
        names.add(n)
        s, d = os.path.join(src, n), os.path.join(dst, n)
        if os.path.isdir(s):
            _mirror(s, d)
        else:
            if not os.path.isfile(d) or os.path.getmtime(d) < os.path.getmtime(s) or os.path.getsize(d) != os.path.getsize(s):
                shutil.copy2(s, d)
    for n in os.listdir(dst):
        if n not in names and n not in SKIP:
            p = os.path.join(dst, n)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)


def status():
    p = prefs()
    repo = p["repo"]
    out = {"repo": repo, "remote": p["remote"], "exists": os.path.isdir(os.path.join(repo, ".git"))}
    code, v = _git(["--version"], cwd=PACK)
    out["git"] = v if code == 0 else None
    if not out["exists"]:
        out["bundle"] = os.path.isfile(os.path.join(os.path.expanduser("~"), "Downloads", "studio.bundle"))
        return out
    _c, log = _git(["log", "--pretty=format:%h|%ad|%s", "--date=format:%Y-%m-%d %H:%M", "-n", "8"])
    out["log"] = [dict(zip(("hash", "date", "message"), ln.split("|", 2))) for ln in log.splitlines() if ln.count("|") >= 2]
    c, ahead = _git(["rev-list", "--count", f"origin/{p['branch']}..HEAD"])
    out["unpushed"] = int(ahead) if c == 0 and ahead.isdigit() else None
    return out


def setup():
    p = prefs()
    repo = p["repo"]
    log = []
    if not os.path.isdir(os.path.join(repo, ".git")):
        os.makedirs(os.path.dirname(repo), exist_ok=True)
        bundle = os.path.join(os.path.expanduser("~"), "Downloads", "studio.bundle")
        if os.path.isfile(bundle):
            c, o = _git(["clone", "-b", p["branch"], bundle, repo], cwd=os.path.dirname(repo))
            log.append(o)
            if c != 0:
                raise RuntimeError("Couldn't start the repo from studio.bundle: " + o[-300:])
        else:
            os.makedirs(repo, exist_ok=True)
            _git(["init", "-b", p["branch"]])
    _git(["config", "user.name", p["name"]])
    _git(["config", "user.email", p["email"]])
    c, o = _git(["remote", "get-url", "origin"])
    if c != 0:
        _git(["remote", "add", "origin", p["remote"]])
    elif o.strip() != p["remote"]:
        _git(["remote", "set-url", "origin", p["remote"]])
    gi = os.path.join(repo, ".gitignore")
    if not os.path.isfile(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write("__pycache__/\n*.pyc\n.topaz_tmp/\n")
    return log


def save(message, push):
    p = prefs()
    setup()
    repo = p["repo"]
    _mirror(PACK, os.path.join(repo, "ComfyUI-MannyStudio"))
    wf_src = os.path.join(H["_user_dir"](), "workflows")
    wf_dst = os.path.join(repo, "workflows")
    os.makedirs(wf_dst, exist_ok=True)
    apps = 0
    for n in os.listdir(wf_src):
        if n.endswith(".app.json"):
            shutil.copy2(os.path.join(wf_src, n), os.path.join(wf_dst, n))
            apps += 1
    _git(["add", "-A"])
    c, changed = _git(["status", "--porcelain"])
    result = {"changed": len([x for x in changed.splitlines() if x.strip()]), "apps": apps}
    if result["changed"]:
        msg = (message or "").strip() or ("Studio " + str(H.get("VERSION", "")) + " · " + time.strftime("%Y-%m-%d %H:%M"))
        c, o = _git(["commit", "-m", msg])
        if c != 0:
            raise RuntimeError("Commit failed: " + o[-300:])
        result["commit"] = _git(["rev-parse", "--short", "HEAD"])[1]
    if push:
        c, o = _git(["push", "-u", "origin", p["branch"]], timeout=300)
        result["pushed"] = c == 0
        if c != 0:
            low = o.lower()
            if "not found" in low or "repository" in low and "does not exist" in low:
                raise RuntimeError("GitHub has no repo there yet. Create an empty private repo named 'studio' on github.com/new, then push again.")
            raise RuntimeError("Push failed: " + o[-400:])
    return result


def setup_routes(ns):
    H.update(ns)
    routes = ns["routes"]

    def _local(request):
        if not ns["auth"].is_local(request):
            raise web.HTTPForbidden(text="Versions can only be saved from this PC")

    @routes.get("/manny/api/git/status")
    async def git_status(request):
        return web.json_response(await asyncio.to_thread(status))

    @routes.post("/manny/api/git/save")
    async def git_save(request):
        _local(request)
        b = await request.json()
        job = ns["_new_job"](False, kind="git")

        async def run():
            job["stage"] = "Saving and pushing" if b.get("push", True) else "Saving"
            return await asyncio.to_thread(save, b.get("message"), b.get("push", True))
        asyncio.ensure_future(ns["_ai_job"](job, run))
        return web.json_response({"id": job["id"]})
