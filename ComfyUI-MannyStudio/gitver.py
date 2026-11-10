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
            "remote": "https://mannyhenri@github.com/mannyhenri/studio.git", "name": "mannyhenri", "email": "manny@gridlight.ai", "branch": "main"}
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


def _fetch():
    c, o = _git(["fetch", "origin"], timeout=300)
    if c != 0:
        low = o.lower()
        if "not found" in low or "does not exist" in low:
            raise RuntimeError("GitHub has no repo there yet. Create an empty private repo named 'studio' on github.com/new.")
        raise RuntimeError("Couldn't reach GitHub: " + o[-300:])


def _remote_state(p):
    br = f"origin/{p['branch']}"
    if _git(["rev-parse", "--verify", "--quiet", br])[0] != 0:
        return {"remote_branch": False, "behind": 0, "ahead": 0, "unrelated": False, "files": [], "remote_log": []}
    unrelated = _git(["merge-base", "HEAD", br])[0] != 0 if _git(["rev-parse", "--verify", "--quiet", "HEAD"])[0] == 0 else True
    has_head = _git(["rev-parse", "--verify", "--quiet", "HEAD"])[0] == 0
    c, b = _git(["rev-list", "--count", f"HEAD..{br}"]) if has_head else _git(["rev-list", "--count", br])
    c2, a = _git(["rev-list", "--count", f"{br}..HEAD"]) if has_head else (0, "0")
    rng = f"HEAD...{br}" if has_head and not unrelated else (f"HEAD {br}" if has_head else br)
    if not has_head:
        _c, names = _git(["ls-tree", "-r", "--name-only", br])
    elif unrelated:   # separate histories: list only what GitHub adds or changes
        _c, names = _git(["diff", "--name-status", "--diff-filter=AM", "HEAD", br])
    else:
        _c, names = _git(["diff", "--name-status", rng])
    _c, log = _git(["log", "--pretty=format:%h|%ad|%an|%s", "--date=format:%Y-%m-%d %H:%M", "-n", "8", br])
    return {"remote_branch": True, "behind": int(b) if b.isdigit() else None, "ahead": int(a) if str(a).isdigit() else None, "unrelated": unrelated,
            "files": [ln for ln in names.splitlines() if ln.strip()][:80],
            "remote_log": [dict(zip(("hash", "date", "author", "message"), ln.split("|", 3))) for ln in log.splitlines() if ln.count("|") >= 3]}


def check():
    """Fetches GitHub and says what's there that this PC doesn't have yet."""
    p = prefs()
    setup()
    _fetch()
    return _remote_state(p)


def _snapshot(p, message):
    """Commits what Studio looks like on this PC right now (code + apps)."""
    repo = p["repo"]
    _mirror(PACK, os.path.join(repo, "ComfyUI-MannyStudio"))
    wf_src = os.path.join(H["_user_dir"](), "workflows")
    wf_dst = os.path.join(repo, "workflows")
    os.makedirs(wf_dst, exist_ok=True)
    for n in os.listdir(wf_src):
        if n.endswith(".app.json"):
            shutil.copy2(os.path.join(wf_src, n), os.path.join(wf_dst, n))
    _git(["add", "-A"])
    c, changed = _git(["status", "--porcelain"])
    if [x for x in changed.splitlines() if x.strip()]:
        c, o = _git(["commit", "-m", message])
        if c != 0:
            raise RuntimeError("Commit failed: " + o[-300:])
        return True
    return False


def _backup():
    d = os.path.join(H["_data_dir"](), "backups", "studio-before-update " + time.strftime("%Y-%m-%d %H%M%S"))
    shutil.copytree(PACK, os.path.join(d, "ComfyUI-MannyStudio"), ignore=shutil.ignore_patterns(*SKIP, "*.pyc"))
    wf = os.path.join(H["_user_dir"](), "workflows")
    os.makedirs(os.path.join(d, "workflows"), exist_ok=True)
    for n in os.listdir(wf):
        if n.endswith(".app.json"):
            shutil.copy2(os.path.join(wf, n), os.path.join(d, "workflows", n))
    return d


def pull(prefer=None):
    """Get updates from GitHub: saves this PC's state, merges GitHub's changes in, then installs the changed files here.
    prefer: None (stop on conflicts), "theirs" (GitHub wins conflicts) or "ours" (this PC wins conflicts)."""
    p = prefs()
    setup()
    repo = p["repo"]
    _fetch()
    st = _remote_state(p)
    if not st["remote_branch"] or not st["behind"]:
        return {"merged": 0, "installed": [], "note": "Already up to date with GitHub"}
    _snapshot(p, "Studio on this PC before getting updates · " + time.strftime("%Y-%m-%d %H:%M"))
    before = _git(["rev-parse", "HEAD"])[1].strip() if _git(["rev-parse", "--verify", "--quiet", "HEAD"])[0] == 0 else None
    args = ["merge", "--no-edit"]
    if st["unrelated"]:
        args.append("--allow-unrelated-histories")
    if prefer in ("theirs", "ours"):
        args += ["-X", prefer]
    args.append(f"origin/{p['branch']}")
    c, o = _git(args, timeout=300)
    if c != 0:
        _c, conf = _git(["diff", "--name-only", "--diff-filter=U"])
        _git(["merge", "--abort"])
        files = [x for x in conf.splitlines() if x.strip()]
        if files:
            return {"conflicts": files, "merged": 0, "installed": []}
        raise RuntimeError("Getting updates failed: " + o[-300:])
    backup = _backup()
    _c, changed = _git(["diff", "--name-only", before, "HEAD"]) if before else _git(["ls-files"])
    installed, removed, restart = [], [], False
    wf_dst = os.path.join(H["_user_dir"](), "workflows")
    for rel in [x for x in changed.splitlines() if x.strip()]:
        src = os.path.join(repo, rel.replace("/", os.sep))
        if rel.startswith("ComfyUI-MannyStudio/"):
            dst = os.path.join(PACK, rel[len("ComfyUI-MannyStudio/"):].replace("/", os.sep))
        elif rel.startswith("workflows/") and rel.endswith(".app.json") and "/" not in rel[len("workflows/"):]:
            dst = os.path.join(wf_dst, rel[len("workflows/"):])
        else:
            continue
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            installed.append(rel)
        elif os.path.isfile(dst):
            os.remove(dst)          # deleted on GitHub (a copy is in the backup)
            removed.append(rel)
        if rel.endswith(".py"):
            restart = True
    return {"merged": st["behind"], "installed": installed, "removed": removed, "restart": restart, "backup": backup}


def save(message, push):
    p = prefs()
    setup()
    repo = p["repo"]
    if push and _git(["remote", "get-url", "origin"])[0] == 0:
        try:
            _fetch()
            st = _remote_state(p)
        except RuntimeError as e:
            if "no repo there yet" not in str(e):
                raise
            st = {"behind": 0}
        if st.get("behind"):
            raise RuntimeError(f"GitHub has {st['behind']} newer change(s) this PC doesn't have. Press 'Get updates from GitHub' first, then save.")
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

    @routes.post("/manny/api/git/check")
    async def git_check(request):
        _local(request)
        job = ns["_new_job"](False, kind="git")

        async def run():
            job["stage"] = "Checking GitHub"
            return await asyncio.to_thread(check)
        asyncio.ensure_future(ns["_ai_job"](job, run))
        return web.json_response({"id": job["id"]})

    @routes.post("/manny/api/git/pull")
    async def git_pull(request):
        _local(request)
        b = await request.json()
        job = ns["_new_job"](False, kind="git")

        async def run():
            job["stage"] = "Getting updates from GitHub"
            return await asyncio.to_thread(pull, b.get("prefer"))
        asyncio.ensure_future(ns["_ai_job"](job, run))
        return web.json_response({"id": job["id"]})

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
