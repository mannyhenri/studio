"""Previz sets (.glb) end to end in the sandbox: upload (streamed), add by path, the list and one set, the file the viewer
loads, renaming and the viewer's saved choices, the card picture, reading a re-exported file, removing, Incognito, and the
vendored three.js the viewer imports."""
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "tests"))
import glbfile  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200


def call(s, path, data=None, method=None, ctype="application/json", opener=None):
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode()
    req = urllib.request.Request(s.url + path, data=data, method=method or ("POST" if data is not None else "GET"),
                                 headers={"Content-Type": ctype} if data is not None else {})
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=60) as r:
            raw, ct = r.read(), r.headers.get("Content-Type", "")
            return r.status, (json.loads(raw) if "json" in ct else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def upload(s, data, filename="Test set.glb", name="", private=False, opener=None):
    q = urllib.parse.urlencode({"filename": filename, "name": name, **({"private": "1"} if private else {})})
    return call(s, "/studio/api/glb/upload?" + q, data, ctype="model/gltf-binary", opener=opener)


def sets_folder(s):
    return os.path.join(s.run, "home", "Documents", "Studio Sets")


def test_upload_list_open_and_remove(scratch):
    data = glbfile.studio_set()
    code, g = upload(scratch, data, name="Diner set")
    assert code == 200, g
    assert g["name"] == "Diner set" and g["file_name"] == "Test set.glb" and g["source"] == "upload" and not g["missing_file"]
    assert os.path.dirname(g["path"]) == sets_folder(scratch)
    assert not [f for f in os.listdir(sets_folder(scratch)) if f.endswith(".part")]
    info = g["info"]
    cams = {c["name"]: c for c in info["cameras"]}
    assert set(cams) == {"Cam_Front", "G_Wide_24mm", "G_Top_35mm"}
    assert cams["G_Wide_24mm"]["animated"] and cams["G_Wide_24mm"]["aim"] == "target" and cams["G_Top_35mm"]["aim"] == "guess"
    assert info["missing"] == ["CAM_09_Lost_50mm"] and info["duration"] == 2.0 and info["fps"] == 24 and info["size"] == len(data)

    assert [x["id"] for x in scratch.get("/studio/api/glb")] == [g["id"]]
    assert scratch.get("/studio/api/glb/" + g["id"])["name"] == "Diner set"
    code, raw = call(scratch, f"/studio/api/glb/{g['id']}/file")
    assert code == 200 and raw == data

    # the same name again gets a file of its own
    code, g2 = upload(scratch, data, name="Diner set")
    assert code == 200 and g2["file_name"] == "Test set 2.glb" and g2["id"] != g["id"]

    code, _ = call(scratch, "/studio/api/glb/" + g2["id"], method="DELETE")
    assert code == 200 and os.path.isfile(g2["path"])          # removed from Studio, the file stays
    assert [x["id"] for x in scratch.get("/studio/api/glb")] == [g["id"]]
    assert call(scratch, "/studio/api/glb/" + g2["id"])[0] == 404
    call(scratch, "/studio/api/glb/" + g["id"], method="DELETE")


def test_what_isnt_a_glb_is_refused(scratch):
    for body in (b"<html>not a set</html>" * 10, b"glTF", b""):
        code, msg = upload(scratch, body, filename="bad.glb")
        assert code == 400 and "isn't a .glb" in msg
    assert not os.path.isdir(sets_folder(scratch)) or not [f for f in os.listdir(sets_folder(scratch)) if "bad" in f]
    assert scratch.get("/studio/api/glb") == []


def test_add_a_file_already_on_the_pc(scratch, tmp_path):
    p = tmp_path / "Hall.glb"
    p.write_bytes(glbfile.studio_set())
    code, g = call(scratch, "/studio/api/glb/add", {"path": f'"{p}"'})       # pasted with quotes, as Windows copies a path
    assert code == 200 and g["path"] == str(p) and g["name"] == "Hall" and g["source"] == "path"
    code, again = call(scratch, "/studio/api/glb/add", {"path": str(p), "name": "Hall B"})
    assert again["id"] == g["id"] and again["name"] == "Hall B"               # the same file stays one set
    for path, msg in ((str(tmp_path / "nope.glb"), "wasn't found"), ("/etc/hostname", "Only .glb"), ("", "Give the path")):
        code, m = call(scratch, "/studio/api/glb/add", {"path": path})
        assert code == 400 and msg in m, (path, m)
    fake = tmp_path / "fake.glb"
    fake.write_text("hello")
    assert call(scratch, "/studio/api/glb/add", {"path": str(fake)})[0] == 400

    # exported again (now with more cameras): Studio reads it again by itself
    p.write_bytes(glbfile.make_glb(cameras=[glbfile.gltf_camera("CAM_09_Lost_50mm", [0, 1, 5], [0, 1, 0])],
                                   nodes=[{"name": "TGT_CAM_09_Lost_50mm", "translation": [0, 1, 0]}]))
    os.utime(p, (1, 1))
    fresh = scratch.get("/studio/api/glb/" + g["id"])["info"]
    assert [c["name"] for c in fresh["cameras"]] == ["CAM_09_Lost_50mm"] and fresh["missing"] == []
    code, r = call(scratch, f"/studio/api/glb/{g['id']}/refresh", {})
    assert code == 200 and r["info"]["cameras"][0]["kind"] == "camera"

    # the file goes away: the set says so
    os.remove(p)
    assert scratch.get("/studio/api/glb/" + g["id"])["missing_file"] is True
    assert call(scratch, f"/studio/api/glb/{g['id']}/file")[0] == 404
    assert call(scratch, f"/studio/api/glb/{g['id']}/refresh", {})[0] == 400
    call(scratch, "/studio/api/glb/" + g["id"], method="DELETE")


def test_name_view_and_card_picture(scratch):
    g = upload(scratch, glbfile.studio_set())[1]
    code, r = call(scratch, "/studio/api/glb/" + g["id"], {"name": "  Renamed  ", "view": {"camera": "n1", "aspect": "2.39", "look": "clay", "evil": 1}})
    assert code == 200 and r["name"] == "Renamed" and r["view"] == {"camera": "n1", "aspect": "2.39", "look": "clay"}
    assert call(scratch, f"/studio/api/glb/{g['id']}/thumb.jpg")[0] == 404
    assert call(scratch, f"/studio/api/glb/{g['id']}/thumb", b"GIF89a....", ctype="image/gif")[0] == 400
    code, t = call(scratch, f"/studio/api/glb/{g['id']}/thumb", JPEG, ctype="image/jpeg")
    assert code == 200 and t["thumb"]
    assert call(scratch, f"/studio/api/glb/{g['id']}/thumb.jpg") == (200, JPEG)
    assert scratch.get("/studio/api/glb/" + g["id"])["thumb"] == t["thumb"]
    call(scratch, "/studio/api/glb/" + g["id"], method="DELETE")


def test_incognito_sets_stay_private(scratch):
    code, msg = upload(scratch, glbfile.studio_set(), private=True)
    assert code == 403
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    form = urllib.parse.urlencode({"new": "sandbox-pass", "again": "sandbox-pass"}).encode()
    assert call(scratch, "/studio/password", form, ctype="application/x-www-form-urlencoded")[0] == 200
    assert call(scratch, "/studio/api/private/unlock", {"password": "sandbox-pass"}, opener=op)[0] == 200
    code, g = upload(scratch, glbfile.studio_set(), filename="Secret.glb", private=True, opener=op)
    assert code == 200 and g["private"] and "Comfyui-Private" in g["path"]
    assert scratch.get("/studio/api/glb") == []                                   # not in the open list
    assert call(scratch, "/studio/api/glb?private=1")[0] == 403                    # locked
    assert [x["id"] for x in call(scratch, "/studio/api/glb?private=1", opener=op)[1]] == [g["id"]]
    for path in ("", "/file", "/thumb.jpg"):
        assert call(scratch, f"/studio/api/glb/{g['id']}{path}")[0] == 403, path    # nothing of it without the key
    assert call(scratch, f"/studio/api/glb/{g['id']}/file", opener=op)[0] == 200
    assert call(scratch, f"/studio/api/glb/{g['id']}", method="DELETE")[0] == 403
    assert call(scratch, f"/studio/api/glb/{g['id']}", method="DELETE", opener=op)[0] == 200


def test_the_viewer_and_three_js_are_served(scratch):
    for path in ("/studio/static/previz/viewer.js", "/studio/static/previz/shots.js", "/studio/static/previz/lighting.js",
                 "/studio/static/previz/objects.js", "/studio/static/vendor/three/three.bundle.min.js",
                 "/studio/static/vendor/three/draco/draco_decoder.wasm", "/studio/static/vendor/three/draco/draco_wasm_wrapper.js"):
        code, body = call(scratch, path)
        assert code == 200 and len(body) > 1000, path
    code, js = call(scratch, "/studio/static/previz/viewer.js")
    assert b'from "../vendor/three/three.bundle.min.js"' in js
    for bad in ("/studio/static/../__init__.py", "/studio/static/%2e%2e/__init__.py", "/studio/static/vendor/../../glbsets.py",
                "/studio/static/vendor", "/studio/static/nope.js"):
        assert call(scratch, bad)[0] == 404, bad


def test_a_quick_scene_is_a_blank_slate(scratch):
    code, g = call(scratch, "/studio/api/glb/quick", {"stage": "room", "people": 2, "name": "Kitchen talk"})
    assert code == 200 and g["name"] == "Kitchen talk" and g["source"] == "quick" and g["kind"] == "set"
    assert os.path.isfile(g["path"]) and os.path.dirname(g["path"]) == sets_folder(scratch)
    info = g["info"]
    assert [c["name"] for c in info["cameras"]] == ["Camera"] and info["animations"] == 0 and not info["cameras"][0]["animated"]
    d = g["direction"]
    assert [a["name"] for a in d["added"]] == ["Person A", "Person B"] and d["subject"].startswith("added:") and d["with"].startswith("added:")
    assert g["view"]["camera"] == "Camera"
    code, g2 = call(scratch, "/studio/api/glb/quick", {"stage": "moon", "people": 7})       # unknown values: the defaults
    assert code == 200 and g2["name"] == "Quick scene (studio cyc)" and [a["name"] for a in g2["direction"]["added"]] == ["Subject"]
    assert call(scratch, "/studio/api/glb/quick", {"private": True})[0] == 403                # Incognito needs the key
    for x in (g, g2):
        call(scratch, "/studio/api/glb/" + x["id"], method="DELETE")


def _jpeg(color, size=(64, 36)):
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    return b.getvalue()


def test_snapshots_go_to_the_gallery_and_the_set(scratch):
    g = upload(scratch, glbfile.studio_set(), name="Snap set")[1]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    q = urllib.parse.urlencode({"kind": "location", "name": "Wide of the hall", "camera": "Cam_Front", "t": "1.5"})
    code, r = call(scratch, f"/studio/api/glb/{g['id']}/still?{q}", png, ctype="image/png")
    loc = r["still"]
    assert code == 200 and loc["kind"] == "location" and loc["output"]["subfolder"] == "Images/Previz/Snap set" and loc["t"] == 1.5
    assert os.path.isfile(os.path.join(scratch.run, "output", "Images", "Previz", "Snap set", loc["output"]["filename"]))
    assert loc["input"].startswith("studio/") and os.path.isfile(os.path.join(scratch.run, "input", loc["input"]))   # ready to be a first frame
    code, r = call(scratch, f"/studio/api/glb/{g['id']}/still?name=For%20a%20shot", _jpeg("red"), ctype="image/jpeg")
    assert code == 200 and r["still"]["kind"] == "storyboard" and r["still"]["output"]["filename"].endswith(".jpg")
    stills = scratch.get("/studio/api/glb/" + g["id"])["stills"]
    assert [x["name"] for x in stills] == ["Wide of the hall"]                       # only location shots are listed on the set
    gallery = {(x["subfolder"], x["filename"]) for x in scratch.get("/studio/api/gallery")}
    assert ("Images/Previz/Snap set", loc["output"]["filename"]) in gallery
    assert call(scratch, f"/studio/api/glb/{g['id']}/still", b"GIF89a...", ctype="image/gif")[0] == 400
    assert call(scratch, f"/studio/api/glb/{g['id']}/stills/{loc['id']}", method="DELETE")[0] == 200
    assert scratch.get("/studio/api/glb/" + g["id"])["stills"] == []
    assert os.path.isfile(os.path.join(scratch.run, "output", "Images", "Previz", "Snap set", loc["output"]["filename"]))   # stays in the Gallery
    call(scratch, "/studio/api/glb/" + g["id"], method="DELETE")


def test_a_movie_of_the_view(scratch):
    g = upload(scratch, glbfile.studio_set(), name="Movie set")[1]
    base = f"/studio/api/glb/{g['id']}/movie"
    mid = call(scratch, base, {})[1]["id"]
    for i, c in enumerate(["red", "green", "blue", "white"]):
        assert call(scratch, f"{base}/{mid}/frame?i={i}", _jpeg(c), ctype="image/jpeg")[0] == 200
    assert call(scratch, f"{base}/{mid}/frame?i=9", b"\x89PNG\r\n\x1a\nxx", ctype="image/png")[0] == 400   # frames are JPEG
    assert call(scratch, f"{base}/{mid}/frame", _jpeg("red"), ctype="image/jpeg")[0] == 400                    # numbered
    code, r = call(scratch, f"{base}/{mid}/finish", {"fps": 2, "name": "Push in", "camera": "Shot 1"})
    assert code == 200, r
    mv = r["movie"]
    assert mv["seconds"] == 2.0 and mv["output"]["subfolder"] == "Videos/Previz/Movie set" and mv["output"]["filename"].endswith(".mp4")
    path = os.path.join(scratch.run, "output", "Videos", "Previz", "Movie set", mv["output"]["filename"])
    import av
    with av.open(path) as c:
        v = c.streams.video[0]
        assert v.frames == 4 and (v.codec_context.width, v.codec_context.height) == (64, 36) and v.codec_context.name == "h264"
    assert scratch.get("/studio/api/glb/" + g["id"])["movies"][0]["id"] == mv["id"]
    assert any(x["filename"] == mv["output"]["filename"] for x in scratch.get("/studio/api/gallery?kind=video"))
    assert call(scratch, f"{base}/{mid}/frame?i=0", _jpeg("red"), ctype="image/jpeg")[0] == 404              # done: the frames are gone
    # a frame missing: refused; stopping: nothing left behind
    mid = call(scratch, base, {})[1]["id"]
    for i in (0, 2):
        call(scratch, f"{base}/{mid}/frame?i={i}", _jpeg("red"), ctype="image/jpeg")
    code, msg = call(scratch, f"{base}/{mid}/finish", {"fps": 24})
    assert code == 400 and "missing" in msg
    assert call(scratch, f"{base}/{mid}", method="DELETE")[0] == 200
    assert not [d for d in os.listdir(os.path.join(scratch.run, "user", "default", "studio_data", "glb", g["id"])) if d.startswith("rec-")]
    assert call(scratch, f"{base}/../../x/frame?i=0", _jpeg("red"), ctype="image/jpeg")[0] == 404
    assert call(scratch, f"{base}/zzzzzzzzzz/finish", {})[0] == 404
    call(scratch, "/studio/api/glb/" + g["id"], method="DELETE")


def test_saves_at_the_same_time_keep_each_other(scratch):
    """The viewer saves its view (camera, tab…) and what was directed within moments of each other; a thumb or a movie
    can land meanwhile too. None of them may write back an old copy of the set over another's change."""
    import threading
    import time
    g = upload(scratch, glbfile.studio_set())[1]
    errors = []

    def many(kind):
        for i in range(25):
            body = {"view": {"camera": f"cam{i}", "tab": "move", "isolate": False}} if kind == "view" else {"direction": {"v": 1, "n": i, "pad": "x" * 1_200_000}}
            code, _r = call(scratch, "/studio/api/glb/" + g["id"], body)
            if code != 200:
                errors.append(code)
    ts = [threading.Thread(target=many, args=(k,)) for k in ("view", "direction")]
    ts += [threading.Thread(target=lambda: [call(scratch, f"/studio/api/glb/{g['id']}/thumb", JPEG, ctype="image/jpeg") for _ in range(10)])]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    got = scratch.get("/studio/api/glb/" + g["id"])
    assert not errors
    assert got["view"] == {"camera": "cam24", "tab": "move", "isolate": False}     # the last of each, isolate kept too
    assert got["direction"]["n"] == 24 and got.get("thumb")
    # directing while a movie is being encoded: the movie's save (after ffmpeg) keeps what was directed meanwhile
    base = f"/studio/api/glb/{g['id']}/movie"
    mid = call(scratch, base, {})[1]["id"]
    for i in range(48):
        call(scratch, f"{base}/{mid}/frame?i={i}", _jpeg("red", (640, 360)), ctype="image/jpeg")
    fin = threading.Thread(target=lambda: errors.append(call(scratch, f"{base}/{mid}/finish", {"fps": 24})[0] - 200))
    fin.start()
    time.sleep(0.05)
    call(scratch, "/studio/api/glb/" + g["id"], {"direction": {"v": 1, "n": "during"}})
    fin.join()
    got = scratch.get("/studio/api/glb/" + g["id"])
    assert errors == [0] and got["direction"]["n"] == "during" and len(got["movies"]) == 1
    call(scratch, "/studio/api/glb/" + g["id"], method="DELETE")
    assert not os.path.exists(os.path.join(scratch.run, "user", "default", "studio_data", "glb", g["id"])), "its cache goes with it"


def test_props_are_kept_apart_from_sets(scratch):
    code, prop = upload(scratch, glbfile.make_glb(), filename="Chair.glb", name="Chair", **{})
    assert code == 200 and prop["kind"] == "set"
    call(scratch, "/studio/api/glb/" + prop["id"], method="DELETE")
    q = urllib.parse.urlencode({"filename": "Chair.glb", "kind": "prop"})
    code, prop = call(scratch, "/studio/api/glb/upload?" + q, glbfile.make_glb(), ctype="model/gltf-binary")
    assert code == 200 and prop["kind"] == "prop" and os.path.basename(os.path.dirname(prop["path"])) == "Props"
    assert [x["id"] for x in scratch.get("/studio/api/glb?kind=prop")] == [prop["id"]]
    assert prop["id"] not in [x["id"] for x in scratch.get("/studio/api/glb")]           # not a set on the Previz page
    assert call(scratch, f"/studio/api/glb/{prop['id']}/file")[0] == 200
    call(scratch, "/studio/api/glb/" + prop["id"], method="DELETE")


def test_what_was_directed_is_saved_with_the_set(scratch):
    g = upload(scratch, glbfile.studio_set())[1]
    d = {"v": 1, "cams": [{"id": "s1", "name": "Shot 1", "setup": {"size": "MCU"}, "A": {"p": [0, 1.6, 3], "t": [0, 1.5, 0], "roll": 0, "lens": 35},
                           "move": {"type": "push", "at": 2, "dur": 4}}],
         "hidden": ["Floor"], "moved": {}, "added": [{"id": "o1", "kind": "person", "name": "Person 1", "p": [1, 0, 0]}], "light": {"preset": "rembrandt"}}
    code, r = call(scratch, "/studio/api/glb/" + g["id"], {"direction": d, "view": {"camera": "s1", "tab": "move", "junk": 1}})
    assert code == 200 and r["direction"] == d and r["view"] == {"camera": "s1", "tab": "move"}
    assert scratch.get("/studio/api/glb/" + g["id"])["direction"] == d
    assert "direction" not in scratch.get("/studio/api/glb")[0]                          # lists stay small
    assert call(scratch, "/studio/api/glb/" + g["id"], {"direction": [1, 2]})[0] == 400
    assert call(scratch, "/studio/api/glb/" + g["id"], {"direction": {"x": "y" * (3 << 20)}})[0] == 400
    assert scratch.get("/studio/api/glb/" + g["id"])["direction"] == d                    # a refused save changes nothing
    assert call(scratch, "/studio/api/glb/" + g["id"], {"direction": None})[1]["direction"] is None
    call(scratch, "/studio/api/glb/" + g["id"], method="DELETE")


# ------------------------------------------------------------------ the viewer in a real browser (skipped without Chrome)
def _chrome():
    import shutil
    return next((p for p in map(shutil.which, ("google-chrome", "chromium", "chromium-browser", "chrome")) if p), None)


async def _in_viewer(chrome, url, port, profile, steps):
    """Opens url in headless Chrome (software WebGL), waits for the viewer to load the set, then runs
    `steps(js, send, seen)`. Returns what the page showed (seen) and the page's errors."""
    import asyncio
    import aiohttp
    proc = await asyncio.create_subprocess_exec(chrome, "--headless=new", "--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                                                "--ignore-gpu-blocklist", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                                                "--window-size=1400,900", "about:blank", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    errors, seen, n = [], {}, 0
    try:
        async with aiohttp.ClientSession() as s:
            targets = []
            for _ in range(100):
                try:
                    async with s.get(f"http://127.0.0.1:{port}/json/list") as r:
                        targets = [t for t in await r.json() if t["type"] == "page"]
                    if targets:
                        break
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(0.1)
            async with s.ws_connect(targets[0]["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                async def send(method, params=None):
                    nonlocal n
                    n += 1
                    me = n
                    await ws.send_json({"id": me, "method": method, "params": params or {}})
                    while True:
                        msg = await asyncio.wait_for(ws.receive_json(), 60)
                        note(msg)
                        if msg.get("id") == me:
                            return msg.get("result", {})

                def note(msg):
                    meth, p = msg.get("method"), msg.get("params", {})
                    if meth == "Runtime.exceptionThrown":
                        errors.append(p["exceptionDetails"].get("exception", {}).get("description", "exception"))
                    elif meth == "Runtime.consoleAPICalled" and p.get("type") == "error":
                        errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", [])))
                    elif meth == "Network.responseReceived" and p["response"]["status"] >= 400:
                        errors.append(f"HTTP {p['response']['status']} {p['response']['url']}")

                async def js(expr):
                    r = await send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
                    return r.get("result", {}).get("value")

                for m in ("Runtime.enable", "Network.enable", "Page.enable"):
                    await send(m)
                await send("Page.navigate", {"url": url})
                for _ in range(600):
                    seen["state"] = await js("""(() => { const l = document.querySelector('.gv-load'), e = document.querySelector('#gvw .empty');
                        return e ? 'empty: ' + e.textContent : !l ? 'waiting' : l.classList.contains('hidden') ? 'loaded' : /Couldn't/.test(l.textContent) ? 'failed: ' + l.textContent : 'loading'; })()""")
                    if seen["state"] not in ("waiting", "loading"):
                        break
                    await asyncio.sleep(0.1)
                if seen["state"] == "loaded":
                    await asyncio.sleep(0.5)
                    await steps(js, send, seen)
    finally:
        proc.kill()
        await proc.wait()
    return seen, errors


CLICK = "(s => { const e = document.querySelector(s); if (!e) throw new Error('no ' + s); e.click(); })"
PV = "document.querySelector('.gv').previz"


async def _directing(js, send, seen):
    """Every camera, then directing a shot: a stand-in, framing, a move, light, a new camera aimed and operated, a
    location shot and a movie."""
    import asyncio
    await asyncio.sleep(0.5)
    pv = PV
    seen["cams"] = await js("[...document.querySelectorAll('.gv-camsel option')].map(o => o.value)")
    seen["missing"] = await js("(document.querySelector('.gv-missing summary') || {}).textContent")
    seen["hud0"] = await js("document.querySelector('.gv-hud.tl').textContent")
    seen["labels"] = await js("[...document.querySelectorAll('.gv-camsel option')].map(o => o.textContent)")
    await js("(() => { const s = document.querySelector('.gv-camsel'); s.value = 'G_Wide_24mm'; s.dispatchEvent(new Event('change')); })()")
    await asyncio.sleep(0.3)
    seen["range"] = await js("[+document.querySelector('.gv-time').min, +document.querySelector('.gv-time').max, document.querySelector('.gv-iso').classList.contains('hidden')]")
    await js("(() => { const s = document.querySelector('.gv-camsel'); s.value = 'Cam_Front'; s.dispatchEvent(new Event('change')); })()")
    await asyncio.sleep(0.3)
    seen["range_static"] = await js("[+document.querySelector('.gv-time').min, document.querySelector('.gv-iso').classList.contains('hidden')]")
    await js("(() => { const s = document.querySelector('.gv-camsel'); s.value = 'G_Top_35mm'; s.dispatchEvent(new Event('change')); })()")
    await asyncio.sleep(0.5)
    seen["hud1"] = await js("document.querySelector('.gv-hud.tl').textContent")
    await js("document.querySelector('.gv-play').click()")
    for _ in range(40):                      # software WebGL on a busy machine: the first frames can take seconds
        await asyncio.sleep(0.3)
        if not (await js("document.querySelector('.gv-tc').textContent")).startswith("0:00.0"):
            break
    await js("document.querySelector('.gv-play').click()")
    seen["tc"] = await js("document.querySelector('.gv-tc').textContent")
    x, y = await js("(() => { const r = document.querySelector('.gv-frame canvas').getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")
    for kind, bx in (("mousePressed", x), ("mouseMoved", x + 40), ("mouseReleased", x + 40)):
        await send("Input.dispatchMouseEvent", {"type": kind, "x": bx, "y": y, "button": "left", "buttons": 0 if kind == "mouseReleased" else 1, "clickCount": 1})
    await asyncio.sleep(0.3)
    seen["hud2"] = await js("document.querySelector('.gv-hud.tl').textContent")
    # direct a shot: a stand-in, framed in medium close-up, pushed in, lit Rembrandt
    click = CLICK
    await js(click + "('.gv-tabs [data-tab=objects]')")
    await js(click + "('[data-k=add] [data-v=person]')")
    await asyncio.sleep(0.4)
    seen["added"] = await js(pv + ".state.added")
    seen["subject"] = await js("document.querySelector('.gv-subject').selectedOptions[0].textContent")
    await js(click + "('.gv-tabs [data-tab=frame]')")
    await js(click + "('[data-k=size] [data-v=MCU]')")
    await asyncio.sleep(0.4)
    seen["hud3"] = await js("document.querySelector('.gv-hud.tl').textContent")
    seen["studio"] = await js(pv + ".state.studio")
    await js(click + "('.gv-tabs [data-tab=move]')")
    await js(click + "('[data-mv=preset]')")          # Moves opens on a move by hand; the presets are the other view
    await js(click + "('[data-k=move] [data-v=push]')")
    await js(click + "('[data-playm]')")
    for _ in range(30):                   # a frame or two (software WebGL on a busy machine draws few)
        await asyncio.sleep(0.2)
        seen["moving"] = await js(pv + ".state.t")
        if seen["moving"] > 0:
            break
    await js(click + "('.gv-tabs [data-tab=light]')")
    await js(click + "('[data-k=light] [data-v=rembrandt]')")
    await asyncio.sleep(1.0)
    # a new camera starts on the subject: medium shot, eye level; then aimed off it, operated, kept, filmed
    await js(click + "('.gv-new')")
    await asyncio.sleep(0.5)
    seen["new_cam"] = [await js(pv + ".state.studio"), await js("document.querySelector('.gv-hud.tl').textContent")]
    await js(click + "('.gv-tabs [data-tab=height]')")
    await js("(() => { const e = document.querySelector('.gv-pan'); e.value = 20; e.dispatchEvent(new Event('input')); })()")
    await asyncio.sleep(0.3)
    seen["panned"] = await js("document.querySelector('.gv-hud.tl').textContent")
    p0 = await js(pv + ".pose().p")
    await js(click + "('.gv-op')")
    w = {"key": "w", "code": "KeyW", "windowsVirtualKeyCode": 87}
    await send("Input.dispatchKeyEvent", {"type": "keyDown", **w})
    for _ in range(25):                   # held until a frame moves it (a slow machine draws few frames)
        await asyncio.sleep(0.2)
        p1 = await js(pv + ".pose().p")
        if p1 != p0:
            break
    await send("Input.dispatchKeyEvent", {"type": "keyUp", **w})
    seen["operated"] = [round(sum((a - b) ** 2 for a, b in zip(p0, p1)) ** 0.5, 2), await js(pv + ".state.mode")]
    await js(click + "('.gv-op')")
    await js(click + "('.gv-snap')")
    for _ in range(50):
        if await js("!!document.querySelector('.snapimg')"):
            break
        await asyncio.sleep(0.1)
    await js(click + "('.snapchoice [data-c=location]')")
    await js(click + "('#sn-go')")
    for _ in range(50):
        await asyncio.sleep(0.1)
        if await js("document.querySelectorAll('.gvbelow .gvstill img').length"):
            break
    seen["stills"] = await js("document.querySelectorAll('.gvbelow .gvstill img').length")
    await js(click + "('.gv-movie')")
    await asyncio.sleep(0.3)
    await js("(() => { const f = document.querySelector('#mv-from'), t = document.querySelector('#mv-to'); f.value = 0; t.value = 0.25; t.dispatchEvent(new Event('input')); })()")
    await js(click + "('#mv-go')")
    for _ in range(600):
        await asyncio.sleep(0.2)
        if await js("document.querySelector('#modal').classList.contains('hidden')"):
            break
    await asyncio.sleep(0.8)
    seen["movies"] = await js("document.querySelectorAll('.gvbelow video').length")
    await js("window.__gvCanvas = document.querySelector('.gv-frame canvas'); location.hash = '#/previz'")   # leave the viewer
    await asyncio.sleep(1.5)
    seen["left"] = await js("[document.querySelectorAll('.gv').length, window.__gvCanvas.getContext('webgl2').isContextLost()]")


import pytest  # noqa: E402


@pytest.mark.skipif(_chrome() is None, reason="no Chrome or Chromium installed")
def test_the_viewer_shows_the_set_and_its_cameras(scratch, tmp_path):
    import asyncio
    import socket
    g = upload(scratch, glbfile.studio_set(), name="Viewer test")[1]
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    seen, errors = asyncio.run(_in_viewer(_chrome(), f"{scratch.url}/studio/#/previz/glb:{g['id']}", port, str(tmp_path / "chrome"), _directing))
    if seen.get("state", "").startswith("empty: ") and "WebGL" in seen["state"]:
        pytest.skip("this Chrome has no WebGL")
    assert seen["state"] == "loaded", seen
    assert seen["cams"] == ["free", "Cam_Front", "G_Wide_24mm", "G_Top_35mm"]
    assert seen["missing"] == "1 camera isn't in this file"
    assert seen["hud0"].startswith("Cam_Front")                   # opens on the first camera
    assert any(x.startswith("G_Wide_24mm · 24 mm · moves 0:00.0–0:02.0") for x in seen["labels"]), seen["labels"]
    lo, hi, hidden = seen["range"]                                # a moving camera plays only its own move…
    assert abs(lo - 1 / 24) < 0.01 and abs(hi - 2) < 0.01 and not hidden, seen["range"]
    assert seen["range_static"] == [0, True]                      # …a still one, the whole timeline
    assert seen["hud1"] == "G_Top_35mm · 35 mm"
    assert not seen["tc"].startswith("0:00.0"), seen["tc"]       # played
    assert seen["hud2"].startswith("✥ Free camera · 35 mm (from G_Top_35mm")   # a drag leaves the camera where it is
    assert seen["added"] == 1 and seen["subject"] == "Person 1"   # the stand-in is the subject (the set has nobody)
    assert seen["studio"] == 1 and "MCU" in seen["hud3"], seen["hud3"]   # framing made a Studio camera
    assert seen["moving"] > 0, seen                               # Play the move runs the timeline from the move's start
    assert seen["new_cam"][0] == 2 and "MS · Eye level" in seen["new_cam"][1], seen["new_cam"]      # ＋: on the subject, medium shot
    assert "panned 20° right" in seen["panned"], seen["panned"]
    assert seen["operated"][0] > 0.02 and seen["operated"][1] == "cam", seen["operated"]   # W moved the camera itself (how far depends on the frame rate)
    assert seen["stills"] == 1 and seen["movies"] == 1, seen
    assert seen["left"] == [0, True]                              # leaving the page gives the GPU back
    assert errors == [], errors
    got = scratch.get("/studio/api/glb/" + g["id"])
    assert got.get("thumb")                                       # the card picture was sent on first open
    d = got["direction"]                                          # and what was directed was saved
    assert [a["kind"] for a in d["added"]] == ["person"] and d["subject"] == "added:" + d["added"][0]["id"]
    assert d["cams"][0]["setup"]["size"] == "MCU" and d["cams"][0]["move"]["type"] == "push" and d["light"]["preset"] == "rembrandt"
    assert d["cams"][1]["setup"]["size"] == "MS" and d["cams"][1]["setup"]["pan"] == -20 and d["cams"][1]["setup"]["free"]
    assert len(got["stills"]) == 1 and len(got["movies"]) == 1 and got["movies"][0]["seconds"] > 0
    call(scratch, "/studio/api/glb/" + g["id"], method="DELETE")


async def _keyframes(js, send, seen):
    """A start keyframe, a change (tilt, then light), an end keyframe: the move plays the change."""
    import asyncio
    pitch = "(p => Math.asin((p.t[1] - p.p[1]) / Math.hypot(p.t[0] - p.p[0], p.t[1] - p.p[1], p.t[2] - p.p[2])) * 180 / Math.PI)"
    at = lambda t: js(f"(() => {{ const e = document.querySelector('.gv-time'); e.value = {t}; e.dispatchEvent(new Event('input')); e.dispatchEvent(new Event('change')); }})()")
    await js(CLICK + "('.gv-new')")
    await asyncio.sleep(0.3)
    await js(CLICK + "('.gv-tabs [data-tab=move]')")
    await js(CLICK + "('[data-setA]')")
    await js(CLICK + "('.gv-tabs [data-tab=height]')")
    await js("(() => { const e = document.querySelector('.gv-tilt'); e.value = 20; e.dispatchEvent(new Event('input')); })()")
    await asyncio.sleep(0.2)
    seen["shaping"] = [await js(f"{pitch}({PV}.pose())"), await js("document.querySelector('.gv-hud.tl').textContent")]
    await js(CLICK + "('.gv-tabs [data-tab=move]')")
    await js(CLICK + "('[data-setB]')")
    await asyncio.sleep(0.3)
    seen["pitch"] = []
    for t in (0, 2, 4):
        await at(t)
        await asyncio.sleep(0.2)
        seen["pitch"].append(await js(f"{pitch}({PV}.pose())"))
    await at(4)                                   # on the end keyframe: the light changes there
    await js(CLICK + "('.gv-tabs [data-tab=light]')")
    await js("(() => { const e = document.querySelector('.gv-lside'); e.value = 90; e.dispatchEvent(new Event('input')); })()")
    await asyncio.sleep(0.2)
    await at(0)
    seen["start_tilt"] = await js(f"{pitch}({PV}.pose())")
    # the free camera: a start, fly back, an end
    await js(CLICK + "('.gv-new')")
    await asyncio.sleep(0.3)
    await js(CLICK + "('.gv-tabs [data-tab=move]')")
    await js(CLICK + "('[data-setA]')")
    p0 = await js(f"{PV}.pose().p")
    await js(f"{PV}.explore()")
    k = {"key": "s", "code": "KeyS", "windowsVirtualKeyCode": 83}
    await send("Input.dispatchKeyEvent", {"type": "keyDown", **k})
    for _ in range(25):                   # held until the free camera has moved
        await asyncio.sleep(0.2)
        if await js(f"{PV}.pose().p") != p0:
            break
    await asyncio.sleep(0.3)
    await send("Input.dispatchKeyEvent", {"type": "keyUp", **k})
    await js(CLICK + "('[data-setB]')")
    await asyncio.sleep(0.3)
    await at(4)
    await asyncio.sleep(0.2)
    p1 = await js(f"{PV}.pose().p")
    seen["flown"] = round(sum((a - b) ** 2 for a, b in zip(p0, p1)) ** 0.5, 2)
    await asyncio.sleep(0.8)                     # saved


@pytest.mark.skipif(_chrome() is None, reason="no Chrome or Chromium installed")
def test_keyframes_animate_what_changed(scratch, tmp_path):
    import asyncio
    import socket
    g = call(scratch, "/studio/api/glb/quick", {"stage": "cyc", "people": 1})[1]
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    seen, errors = asyncio.run(_in_viewer(_chrome(), f"{scratch.url}/studio/#/previz/glb:{g['id']}", port, str(tmp_path / "chrome"), _keyframes))
    if seen.get("state", "").startswith("empty: ") and "WebGL" in seen["state"]:
        pytest.skip("this Chrome has no WebGL")
    assert seen["state"] == "loaded", seen
    assert "shaping the end" in seen["shaping"][1], seen["shaping"]       # after the start, a change shapes the end…
    p0, p2, p4 = seen["pitch"]
    assert abs((p4 - p0) - 20) < 0.5 and abs((p2 - p0) - 10) < 0.5, seen["pitch"]   # …and the move plays it: 0, 10, 20°
    assert abs(seen["start_tilt"] - p0) < 0.05, "changing the end kept the start"
    assert seen["flown"] > 0.1, seen                                        # the free camera's move, keyframed
    assert errors == [], errors
    cams = scratch.get("/studio/api/glb/" + g["id"])["direction"]["cams"]
    m = cams[0]["move"]
    assert m["type"] == "custom" and not m["pending"] and m["A"]["setup"]["tilt"] == 0 and m["B"]["setup"]["tilt"] == 20
    assert m["A"]["light"]["params"]["side"] == 0 and m["B"]["light"]["params"]["side"] == 90     # the light is keyframed too
    assert m["A"]["light"]["lights"][0]["pos"] != m["B"]["light"]["lights"][0]["pos"]
    call(scratch, "/studio/api/glb/" + g["id"], method="DELETE")


async def _blocking(js, send, seen):
    """Two people in a room: a double-click frames one (from inside the room), the top view moves and turns them and a
    camera, and ◆ Keys moves a camera's move later in time."""
    import asyncio
    pv = PV

    async def mouse(kind, x, y, buttons=1, clicks=1, mods=0):
        await send("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "buttons": buttons, "clickCount": clicks, "modifiers": mods})

    async def drag(x0, y0, x1, y1, mods=0):
        await mouse("mouseMoved", x0, y0, 0)
        await mouse("mousePressed", x0, y0, mods=mods)
        for i in range(1, 9):
            await mouse("mouseMoved", x0 + (x1 - x0) * i / 8, y0 + (y1 - y0) * i / 8, mods=mods)
        await mouse("mouseReleased", x1, y1, 0, mods=mods)
        await asyncio.sleep(0.6)

    # a double-click on Person B: in frame in the free camera, seen from inside the room (not from behind a wall)
    await js(CLICK + "('.gv-tabs [data-tab=objects]')")
    await js("[...document.querySelectorAll('.gv-orow')].find(r => r.textContent.includes('Person B')).querySelector('.gv-on').click()")
    x, y = await js(pv + ".screenPos(true)")
    await send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
    await send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
    for clicks in (1, 2):
        await mouse("mousePressed", x, y, clicks=clicks)
        await mouse("mouseReleased", x, y, 0, clicks=clicks)
    await asyncio.sleep(1.5)
    seen["framed"] = [await js(pv + ".state.mode"), await js(pv + ".state.selected"), await js(pv + ".pose().p")]
    # the top view: Person B dragged to the right, then turned (Shift)
    await js(CLICK + "('.gv-topb')")
    await asyncio.sleep(1.0)
    seen["top_shown"] = await js("(() => { const r = document.querySelector('.gv-top canvas').getBoundingClientRect(); return r.width > 100 && r.height > 100; })()")
    x, y = await js(pv + ".topPos()")
    await drag(x, y, x + 40, y)
    x, y = await js(pv + ".topPos()")
    await drag(x, y, x + 40, y, mods=8)
    # a Studio camera with a push in, dragged to the left on the plan
    await js(CLICK + "('.gv-new')")
    await asyncio.sleep(0.4)
    await js(CLICK + "('.gv-tabs [data-tab=move]')")
    await js(CLICK + "('[data-mv=preset]')")
    await js(CLICK + "('[data-k=move] [data-v=push]')")
    await asyncio.sleep(0.3)
    await send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
    seen["cam_before"] = await js(pv + ".pose().p")
    x, y = await js(pv + ".topPos()")
    await drag(x, y, x - 40, y)
    # ◆ Keys: a push in of 10 s that starts at 3 s (the shot stays 10 s: still for 3 s), then slid 2 s later
    await js("(() => { const e = document.querySelector('.gv-durn'); e.value = 10; e.dispatchEvent(new Event('input')); e.dispatchEvent(new Event('change')); })()")
    await asyncio.sleep(0.3)
    await js(CLICK + "('.gv-keysb')")
    await asyncio.sleep(0.8)
    box = "(s => { const b = document.querySelector('.gv-dlane.cur ' + s).getBoundingClientRect(); return [b.x, b.y, b.width, b.height]; })"
    tr = await js(box + "('.gv-dtrack')")
    ruler = await js("[...document.querySelectorAll('.gv-druler .gv-dtl')].map(e => e.textContent)")
    per_s = tr[2] / (int(ruler[-1].split(":")[0]) * 60 + int(ruler[-1].split(":")[1]))     # the ruler's last mark is its end
    ha = await js(box + "('.gv-dh.a')")
    await drag(ha[0] + ha[2] / 2, ha[1] + ha[3] / 2, ha[0] + ha[2] / 2 + 3 * per_s, ha[1] + ha[3] / 2)
    await asyncio.sleep(0.8)                          # saved
    seen["delayed"] = await js(pv + ".movieSpan()")
    bar = await js(box + "('.gv-dbar')")
    await drag(bar[0] + bar[2] / 2, bar[1] + bar[3] / 2, bar[0] + bar[2] / 2 + 2 * per_s, bar[1] + bar[3] / 2)
    seen["lanes"] = await js("[...document.querySelectorAll('.gv-dlane')].map(l => l.textContent.trim())")
    await asyncio.sleep(0.8)                          # saved


@pytest.mark.skipif(_chrome() is None, reason="no Chrome or Chromium installed")
def test_blocking_from_above_and_moves_in_time(scratch, tmp_path):
    import asyncio
    import socket
    g = call(scratch, "/studio/api/glb/quick", {"stage": "room", "people": 2})[1]
    before = {a["name"]: a for a in scratch.get("/studio/api/glb/" + g["id"])["direction"]["added"]}
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    seen, errors = asyncio.run(_in_viewer(_chrome(), f"{scratch.url}/studio/#/previz/glb:{g['id']}", port, str(tmp_path / "chrome"), _blocking))
    if seen.get("state", "").startswith("empty: ") and "WebGL" in seen["state"]:
        pytest.skip("this Chrome has no WebGL")
    assert seen["state"] == "loaded", seen
    mode, selected, p = seen["framed"]
    assert mode == "free" and selected == "Person B", seen["framed"]
    assert abs(p[0]) < 4 and abs(p[2]) < 3, f"framed from outside the 8 × 6 m room (behind a wall): {p}"
    assert seen["top_shown"] is True
    assert errors == [], errors
    got = scratch.get("/studio/api/glb/" + g["id"])
    d = got["direction"]
    b0, b1 = before["Person B"], next(a for a in d["added"] if a["name"] == "Person B")
    assert b1["p"][0] - b0["p"][0] > 0.1 and abs(b1["p"][2] - b0["p"][2]) < 0.05, (b0["p"], b1["p"])    # dragged right on the plan
    assert b1["q"] != b0["q"], "Shift-drag turned them"
    cam = d["cams"][0]
    assert cam["A"]["p"][0] < seen["cam_before"][0] - 0.1 and abs(cam["A"]["p"][1] - seen["cam_before"][1]) < 1e-6, (seen["cam_before"], cam["A"]["p"])
    assert cam["move"]["type"] == "push" and cam["move"]["A"]["p"] == cam["A"]["p"]          # its push in moved with it
    assert seen["delayed"] == [0, 10], seen["delayed"]          # the start keyframe moved to 3 s: the shot (and a movie) stays 0-10 s
    mv = cam["move"]                                            # then the move slid 2 s later: 5 s to 12 s, the shot grew to hold it
    assert abs(mv["at"] - 5) < 0.11 and abs(mv["dur"] - 7) < 0.11 and mv["shot"][0] == 0 and abs(mv["shot"][1] - 12) < 0.11, mv
    assert any(lane.startswith(cam["name"]) and "Push in" in lane for lane in seen["lanes"]), seen["lanes"]
    assert got["view"].get("top") is True and got["view"].get("keys") is True                  # kept open for next time
    call(scratch, "/studio/api/glb/" + g["id"], method="DELETE")
