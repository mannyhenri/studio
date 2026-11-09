r"""Incognito: a private space inside Studio.

- Regular mode needs no password, on this PC or any other computer.
- Incognito (the private space) asks for the password, everywhere, this PC included.
  Its renders live in a folder outside ComfyUI's output folder (Pictures\Comfyui-Private),
  so ComfyUI itself never serves them; Studio serves them only to an unlocked browser.
- Private uploads (references, masks, faces) live in ComfyUI's input folder under
  manny_private/, and /view refuses them without the unlock cookie.
- Only a salted PBKDF2 hash of the password is stored.
"""
import hashlib
import hmac
import html
import json
import os
import secrets
import time

from aiohttp import web

import folder_paths

COOKIE = "manny_private"
SESSION_DAYS = 0.5  # 12 hours
ITER = 310_000
_fails = {}


def _file():
    d = os.path.join(folder_paths.get_user_directory(), "default", "manny_studio")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "auth.json")


def _load():
    try:
        with open(_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    tmp = _file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, _file())


def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), ITER).hex()


def has_password():
    return bool(_load().get("hash"))


def check_password(pw):
    d = _load()
    if not d.get("hash"):
        return False
    return hmac.compare_digest(_hash(pw, d["salt"]), d["hash"])


def set_password(pw):
    salt = secrets.token_hex(16)
    _save({"salt": salt, "hash": _hash(pw, salt), "sessions": {}})  # changing it signs everyone out


def new_session():
    d = _load()
    tok = secrets.token_urlsafe(32)
    now = time.time()
    s = {k: v for k, v in d.get("sessions", {}).items() if v > now}
    s[hashlib.sha256(tok.encode()).hexdigest()] = now + SESSION_DAYS * 86400
    d["sessions"] = s
    _save(d)
    return tok


def drop_session(tok):
    d = _load()
    d.get("sessions", {}).pop(hashlib.sha256((tok or "").encode()).hexdigest(), None)
    _save(d)


def valid_session(tok):
    if not tok:
        return False
    exp = _load().get("sessions", {}).get(hashlib.sha256(tok.encode()).hexdigest())
    return bool(exp and exp > time.time())


def is_local(request):
    ip = (request.remote or "").split("%")[0]
    return ip in ("127.0.0.1", "::1", "localhost") or ip.startswith("127.") or ip == "::ffff:127.0.0.1"


PRIVATE_INPUT = "manny_private"


def unlocked(request):
    return valid_session(request.cookies.get(COOKIE))


def _is_private_view(request):
    q = request.query
    sub = (q.get("subfolder") or "").replace("\\", "/").lstrip("/")
    fn = (q.get("filename") or "").replace("\\", "/")
    return (q.get("type", "output") == "input" and (sub.startswith(PRIVATE_INPUT) or fn.startswith(PRIVATE_INPUT + "/")))


@web.middleware
async def lock(request, handler):
    """Everything is open except private input files viewed through ComfyUI's /view."""
    if request.path in ("/view", "/api/view") and _is_private_view(request) and not unlocked(request):
        return web.json_response({"error": "incognito is locked"}, status=403)
    return await handler(request)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Studio</title><link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Inter:wght@400;500&display=swap" rel="stylesheet">
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(900px 500px at 80% -10%,#ff6b3d30,transparent 60%),radial-gradient(700px 400px at 0% 110%,#8b6bff30,transparent 60%),#0b0b0e;color:#ecebf2;font:14px Inter,Segoe UI,system-ui,sans-serif}
.card{width:min(380px,90vw);background:#16161c;border:1px solid #2a2a34;border-radius:20px;padding:32px;box-shadow:0 30px 80px #0008}
.brand{display:flex;align-items:center;gap:10px;font:800 24px 'Barlow Condensed',sans-serif;letter-spacing:.04em;margin-bottom:22px}
.brand b{color:#ff6b3d}.mark{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,#ff6b3d,#ffb03d);display:grid;place-items:center}
h1{font:800 34px 'Barlow Condensed',sans-serif;text-transform:uppercase;margin:0 0 6px;letter-spacing:.03em}p{color:#9a98a8;margin:0 0 20px}
label{display:block;font:700 13px 'Barlow Condensed',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:#9a98a8;margin:14px 0 6px}
input{width:100%;box-sizing:border-box;background:#111116;border:1px solid #2a2a34;border-radius:10px;color:#fff;padding:12px;font:inherit;outline:none}input:focus{border-color:#ff6b3d}
button{margin-top:22px;width:100%;border:0;border-radius:11px;padding:14px;font:800 18px 'Barlow Condensed',sans-serif;letter-spacing:.1em;text-transform:uppercase;background:linear-gradient(135deg,#ff6b3d,#ff8a3d);color:#160a05;cursor:pointer}
.err{background:#ff4d6d22;border:1px solid #ff4d6d;color:#ffb3c0;border-radius:10px;padding:10px 12px;margin-bottom:6px}.ok{background:#3ddc9722;border:1px solid #3ddc97;border-radius:10px;padding:10px 12px}
a{color:#ff8a5c}
</style></head><body><div class="card">
<div class="brand"><span class="mark"><svg width="22" height="22" viewBox="0 0 32 32"><path d="M12 8.5v15l12-7.5z" fill="#120c09" stroke="#120c09" stroke-width="1.5" stroke-linejoin="round"/></svg></span><b style="color:#ecebf2;letter-spacing:.12em">STUDIO</b></div>
%BODY%</div></body></html>"""


def page(body, status=200):
    return web.Response(text=PAGE.replace("%BODY%", body), content_type="text/html", status=status,
                        headers={"Cache-Control": "no-store"})


def setup(routes):
    @routes.get("/manny/login")
    async def old_login(request):
        raise web.HTTPFound("/manny/")

    @routes.get("/manny/logout")
    async def old_logout(request):
        raise web.HTTPFound("/manny/")

    @routes.get("/manny/api/private/status")
    async def status(request):
        return web.json_response({"unlocked": unlocked(request), "has_password": has_password(), "local": is_local(request)})

    @routes.post("/manny/api/private/unlock")
    async def unlock(request):
        ip = request.remote or "?"
        f = [t for t in _fails.get(ip, []) if t > time.time() - 300]
        _fails[ip] = f
        if len(f) >= 8:
            return web.json_response({"error": "Too many wrong tries. Wait 5 minutes."}, status=429)
        if not has_password():
            return web.json_response({"error": "No password yet. On the PC, open /manny/password to set one."}, status=400)
        b = await request.json()
        if not check_password(str(b.get("password", ""))):
            f.append(time.time())
            return web.json_response({"error": "Wrong password."}, status=403)
        _fails.pop(ip, None)
        resp = web.json_response({"unlocked": True})
        resp.set_cookie(COOKIE, new_session(), max_age=int(SESSION_DAYS * 86400), httponly=True, samesite="Lax", path="/")
        return resp

    @routes.post("/manny/api/private/lock")
    async def lock_now(request):
        drop_session(request.cookies.get(COOKIE))
        resp = web.json_response({"unlocked": False})
        resp.del_cookie(COOKIE, path="/")
        return resp

    def _pw_form(msg=""):
        cur = '<label>Current password</label><input type="password" name="current" autocomplete="current-password">' if has_password() else ""
        title = "Change password" if has_password() else "Set a password"
        return page(f"""<h1>{title}</h1><p>This password unlocks Incognito (your private space) on every computer, this one included. Regular mode needs no password.</p>{msg}
<form method="post" action="/manny/password">{cur}
<label>New password</label><input type="password" name="new" autocomplete="new-password" minlength="6" required>
<label>Type it again</label><input type="password" name="again" autocomplete="new-password" minlength="6" required>
<button>Save password</button></form><p style="margin-top:18px"><a href="/manny/">Back to the studio</a></p>""")

    @routes.get("/manny/password")
    async def pw_get(request):
        if not is_local(request):
            return page("<h1>On the PC only</h1><p>The password can only be set or changed on the PC that runs ComfyUI.</p>", 403)
        return _pw_form()

    @routes.post("/manny/password")
    async def pw_post(request):
        if not is_local(request):
            return page("<h1>On the PC only</h1>", 403)
        d = await request.post()
        new, again = str(d.get("new", "")), str(d.get("again", ""))
        if has_password() and not check_password(str(d.get("current", ""))):
            return _pw_form('<div class="err">The current password is wrong.</div>')
        if len(new) < 6:
            return _pw_form('<div class="err">Use at least 6 characters.</div>')
        if new != again:
            return _pw_form('<div class="err">The two passwords don\'t match.</div>')
        set_password(new)
        return page('<h1>Saved</h1><div class="ok">Password saved. Use it to unlock Incognito.</div>'
                    '<p style="margin-top:18px"><a href="/manny/">Back to the studio</a></p>')
