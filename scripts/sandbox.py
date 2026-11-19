#!/usr/bin/env python3
"""Studio's offline sandbox: Studio inside a stand-in ComfyUI on Linux, no GPU, no internet.

Run the app with one command, from the repository's root:

    npm run sandbox                       set up (first time), fresh demo data, start, and open Studio in the browser

    npm run sandbox -- --keep             the same, on the data left from last time (nothing is reset)
    npm run sandbox -- --empty            the same, with no data at all
    npm run sandbox -- --port 8190        on another port (if 8188 is taken by something else, the next free one is used)
    npm run sandbox -- --no-open          don't open the browser (or set SANDBOX_NO_BROWSER=1)

Looking at what Studio did (the sandbox running or not):

    npm run sandbox:jobs                  the jobs Studio sent to the stand-in, newest first (-n 20)
    npm run sandbox:last-job              the last job: app, models, size, prompt, result files
    npm run sandbox:check                 unit tests, sandbox tests and ruff
    npm run test:mcp                      the MCP server's checks, by name (-- --url http://<pc>:8188 for a real Studio)
    npm run test:generators               the AI tools' checks, by name (-- --url … as well; --render to render there)
    python3 scripts/sandbox.py queue-bad  queue a job with a model the sandbox doesn't have (shows ComfyUI's refusal)
    python3 scripts/sandbox.py reset      delete the sandbox data

The first run creates .venv with scripts/setup-linux.sh (again whenever sandbox/requirements.txt changes). Studio is at
http://localhost:8188/studio and opens in the browser by itself; Ctrl+C stops it."""
import argparse
import glob
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX = os.path.join(REPO, "sandbox")
RUN = os.environ.get("SANDBOX_RUN") or os.path.join(SANDBOX, "run")        # the ComfyUI folders (tests use their own)
JOBS = os.environ.get("SANDBOX_JOBS") or os.path.join(SANDBOX, "jobs")
VENV_PY = os.path.join(REPO, ".venv", "bin", "python")


def _setup_stamp():
    """What the environment was set up for: the requirements, so a change to them sets it up again."""
    import hashlib
    with open(os.path.join(SANDBOX, "requirements.txt"), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


READY = os.path.join(REPO, ".venv", ".sandbox-ready")     # written by setup-linux.sh when everything is in place


def _is_set_up():
    try:
        with open(READY, encoding="utf-8") as f:
            return os.path.isfile(VENV_PY) and f.read().strip() == _setup_stamp()
    except OSError:
        return False


def ensure_env():
    """Re-run this script with the sandbox's own Python (.venv), setting it up first when it isn't (the first run, a setup
    that stopped half-way, or new requirements)."""
    if not _is_set_up():
        print("Setting up the sandbox (scripts/setup-linux.sh)...", flush=True)
        r = subprocess.run(["bash", os.path.join(REPO, "scripts", "setup-linux.sh")], cwd=REPO, env=dict(os.environ, SANDBOX_FROM_CLI="1"))
        if r.returncode != 0:
            sys.exit(r.returncode)
    if os.path.realpath(sys.prefix) == os.path.realpath(os.path.join(REPO, ".venv")):
        return
    os.execv(VENV_PY, [VENV_PY, os.path.abspath(__file__), *sys.argv[1:]])


def env_for_server():
    env = dict(os.environ)
    home = os.path.join(RUN, "home")
    os.makedirs(home, exist_ok=True)
    env.update(HOME=home, STUDIO_URL="", PYTHONUNBUFFERED="1", STUDIO_SANDBOX="1",
               # the AI tools' local models (Demucs, Whisper) are stand-ins here, on their engines' path only
               STUDIO_GENERATORS_PYTHONPATH=os.path.join(SANDBOX, "engine_stubs"))
    env.pop("STUDIO_URL")
    ff = os.path.join(REPO, ".venv", "ffmpeg")
    if os.path.isfile(ff):
        env["FFMPEG_PATH"] = ff
    return env


def prepare(fresh):
    """The ComfyUI folder tree: Studio's apps in user/default/workflows and an empty file for every model they use."""
    if fresh:
        shutil.rmtree(RUN, ignore_errors=True)
        shutil.rmtree(JOBS, ignore_errors=True)
    wf = os.path.join(RUN, "user", "default", "workflows")
    os.makedirs(wf, exist_ok=True)
    for f in glob.glob(os.path.join(REPO, "workflows", "*.app.json")):
        shutil.copy2(f, wf)
    with open(os.path.join(SANDBOX, "models.json"), encoding="utf-8") as f:
        models = json.load(f)
    for folder, names in models.items():
        for n in names:
            p = os.path.join(RUN, "models", folder, n)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if not os.path.exists(p):
                open(p, "wb").close()
    for d in ("input", "output", "temp"):
        os.makedirs(os.path.join(RUN, d), exist_ok=True)
    # the example files the apps open with, made from the sample media
    sys.path.insert(0, SANDBOX)
    import make_media
    media = os.path.join(SANDBOX, "media")
    make_media.make_all(media)
    sample = {"image": "portrait.png", "video": "street.mp4", "audio": "song.wav"}
    with open(os.path.join(SANDBOX, "inputs.json"), encoding="utf-8") as f:
        for name, kind in json.load(f).items():
            p = os.path.join(RUN, "input", name)
            if not os.path.exists(p):
                os.makedirs(os.path.dirname(p), exist_ok=True)
                shutil.copy2(os.path.join(media, sample[kind]), p)


def wait_healthy(url, proc, limit=60):
    t0 = time.time()
    while time.time() - t0 < limit:
        if proc.poll() is not None:
            raise SystemExit(f"The stand-in ComfyUI stopped (exit {proc.returncode}); see the lines above.")
        try:
            with urllib.request.urlopen(url + "/sandbox/health", timeout=2) as r:
                h = json.loads(r.read())
            if h.get("pack_error"):
                print("Studio's pack failed to load in the sandbox:\n" + h["pack_error"])
            return h
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.3)
    raise SystemExit("The stand-in ComfyUI didn't answer within a minute.")


def port_free(port):
    import socket
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def sandbox_on(port):
    """The health answer of a sandbox already running on this port, else None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/sandbox/health", timeout=2) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _wsl():
    return sys.platform.startswith("linux") and "microsoft" in platform.uname().release.lower()


def open_browser(url):
    """Open url in this computer's browser, without waiting for it (a terminal browser such as lynx would take over the
    terminal, so on Linux only a desktop session's browser is used). Returns False when there is none to open."""
    if os.environ.get("SANDBOX_NO_BROWSER"):
        return False
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if os.environ.get("BROWSER"):                      # the user's own choice, as Python's webbrowser reads it
            return bool(webbrowser.open(url))
        if sys.platform == "darwin":
            subprocess.Popen(["open", url], **quiet)
            return True
        if os.name == "nt":
            os.startfile(url)                               # the Windows way to open a link
            return True
        if _wsl():                                          # Linux inside Windows: the Windows browser
            opener = shutil.which("wslview")
            subprocess.Popen([opener, url] if opener else ["cmd.exe", "/c", "start", "", url], **quiet)
            return True
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return False                                    # no desktop here (ssh, a container): nothing to open it in
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", url], start_new_session=True, **quiet)
            return True
        for name in ("google-chrome", "chromium", "chromium-browser", "firefox"):
            try:
                if webbrowser.get(name).open(url):
                    return True
            except webbrowser.Error:
                continue
    except Exception:
        pass
    return False


def _say_open(page, open_it):
    mcp = page.rsplit("/studio", 1)[0] + "/studio/mcp"
    print(f"\nStudio (sandbox) is running at {page}\nAI assistants (MCP): {mcp}  (Claude Code in this folder picks it up from .mcp.json)\n"
          "Jobs go to the stand-in ComfyUI and are saved in sandbox/jobs/. Ctrl+C stops it.\n", flush=True)
    if open_it and not os.environ.get("SANDBOX_NO_BROWSER"):
        if open_browser(page):
            print("Opened in your browser.", flush=True)
        else:
            print(f"(No desktop browser to open here: open {page} yourself.)", flush=True)


def start(kind, port, open_it):
    """The app: set up, lay out the ComfyUI folders (fresh unless kept), start Studio in the stand-in, open the browser."""
    if not port_free(port):
        if sandbox_on(port):
            page = f"http://localhost:{port}/studio"
            print(f"A sandbox is already running on port {port} (its data is left as it is; stop it with Ctrl+C in its terminal "
                  "to start afresh).")
            if open_it and open_browser(page):
                print(f"Opened {page} in your browser.")
            else:
                print(f"Open {page}")
            return
        asked = port
        port = next((p for p in range(port + 1, port + 50) if port_free(p)), None)
        if port is None:
            raise SystemExit(f"Port {asked} and the 49 after it are in use. Free one, or use --port.")
        print(f"Port {asked} is in use by another program: using {port}.")
    prepare(fresh=kind in ("demo", "empty"))
    url = f"http://127.0.0.1:{port}"
    cmd = [sys.executable, os.path.join(SANDBOX, "fake_comfy.py"), "--root", RUN, "--jobs", JOBS, "--port", str(port)]
    proc = subprocess.Popen(cmd, cwd=REPO, env=env_for_server())
    try:
        wait_healthy(url, proc)
        if kind == "demo":
            sys.path.insert(0, SANDBOX)
            import demo
            s = demo.seed(url, RUN)
            print(f"Demo data (The Glass Harbour): {s['clips']} clips and {s['pictures']} pictures in Gallery; no projects or characters yet.\n"
                  f"Start one: Projects › Import a screenplay › screenplays/The Glass Harbour.pdf (or the .fountain)")
        elif kind == "empty":
            print("No data: Gallery, Characters, Storyboard and Projects start empty.")
        else:
            print("Kept the data from last time.")
        # the browser opens from its own thread: a slow opener never holds up the server's output or Ctrl+C
        threading.Thread(target=_say_open, args=(f"http://localhost:{port}/studio", open_it), daemon=True).start()
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("Stopped.")


# ------------------------------------------------------------------ inspecting jobs
def _jobs():
    files = sorted(glob.glob(os.path.join(JOBS, "*.json")), key=os.path.getmtime, reverse=True)
    out = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except Exception:
            pass
    return out


def _files_of(job):
    for o in (job.get("outputs") or {}).values():
        for k in ("images", "audio"):
            for x in o.get(k, []):
                yield os.path.join(RUN, x.get("type", "output"), x.get("subfolder", ""), x["filename"])


def _size(path):
    try:
        if path.lower().endswith((".png", ".jpg", ".webp")):
            from PIL import Image
            with Image.open(path) as im:
                return "%dx%d" % im.size
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                              "-of", "csv=p=0:s=x", path], capture_output=True, text=True).stdout.strip()
        return out or "sound"
    except Exception:
        return "?"


MODEL_KEYS = ("unet_name", "clip_name", "clip_name1", "clip_name2", "clip_name3", "vae_name", "lora_name", "ckpt_name",
              "model_name", "bg_removal_name")


def _models(prompt):
    return sorted({v for n in prompt.values() for k, v in (n.get("inputs") or {}).items()
                   if k in MODEL_KEYS and isinstance(v, str) and v not in ("pixel_space",)})


def cmd_jobs(n):
    jobs = _jobs()[:n]
    if not jobs:
        print("No jobs yet.")
    for j in jobs:
        files = ", ".join(f"{os.path.basename(p)} ({_size(p)})" for p in _files_of(j))
        if not files:
            err = j.get("error")
            files = err.get("message", "") if isinstance(err, dict) else (err or "")
        what = (j.get("app") or j.get("label") or "-").removesuffix(".app.json")
        if (j.get("extra_data") or {}).get("studio_prompt_step"):
            what += " (prompt step)"
        print(f"#{j.get('number', '-')}  {j.get('status'):8}  {what}  ·  {files or '-'}")


def cmd_last_job():
    jobs = [j for j in _jobs() if j.get("status") != "refused"]
    if not jobs:
        print("No jobs yet.")
        return
    j = jobs[0]
    texts = [v for n in j["prompt"].values() for k, v in (n.get("inputs") or {}).items()
             if k in ("text", "prompt", "string", "value") and isinstance(v, str) and len(v) > 3]
    print(f"Job #{j['number']} ({j['status']}, {j['finished_at']})")
    print(f"  app:     {(j.get('app') or '-').removesuffix('.app.json')}")
    print(f"  label:   {j.get('label') or '-'}")
    print(f"  models:  {', '.join(_models(j['prompt'])) or '-'}")
    if (j.get("extra_data") or {}).get("studio_prompt_step"):
        print("  step:    the prompt step (text encoders and LLMs); its render is the next job")
    for p in _files_of(j):
        print(f"  result:  {os.path.relpath(p, RUN)}  {_size(p)}")
    if texts:
        print(f"  prompt:  {texts[0][:200]}")
    print(f"  nodes:   {len(j['prompt'])} ({', '.join(sorted({n['class_type'] for n in j['prompt'].values()})[:12])}...)")


def cmd_queue_bad(port):
    bad = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "not_in_the_sandbox.safetensors", "weight_dtype": "default"}},
           "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3}},
           "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}}
    print("Queueing a job whose only output needs not_in_the_sandbox.safetensors (not in sandbox/models.json)...")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/prompt", data=json.dumps({"prompt": bad, "client_id": "queue-bad"}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print("Accepted (unexpected):", r.read().decode())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        print(f"Refused ({e.code}): {body['error']['message']}")
        for nid, ne in body.get("node_errors", {}).items():
            for err in ne["errors"]:
                print(f"  node {nid} ({ne['class_type']}): {err['message']}: {err['details']}")
    except urllib.error.URLError:
        raise SystemExit(f"Nothing on port {port}: start the sandbox first (npm run sandbox).")


SUITES = {"mcp": "Studio's MCP server (AI assistants)", "generators": "the AI tools (Film Engine's Generators)"}


def cmd_test(suite, url=None, render=False):
    """One suite, each check by name: in sandboxes of its own (no GPU, nothing to set up), or with --url against a real,
    running Studio (its live checks; --render lets them render on its GPU too)."""
    env = dict(os.environ)
    env.pop("STUDIO_TEST_URL", None)
    if url:
        url = url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        try:
            with urllib.request.urlopen(url + "/studio/api/info", timeout=10) as r:
                version = json.loads(r.read()).get("version")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SystemExit(f"Nothing answers as Studio at {url} ({getattr(e, 'reason', e)}). Is ComfyUI running there, with Studio "
                             "installed? (The address is the one you open Studio at, without /studio.)")
        env["STUDIO_TEST_URL"] = url
        where = f"the Studio at {url} (Studio {version})"
    else:
        where = "the sandbox (no GPU: the renders are placeholders)"
    if render:
        env["STUDIO_TEST_RENDER"] = "1"
    print(f"Testing {SUITES[suite]} in {where}", flush=True)
    browser = None
    if not url:                                          # the checks that open Studio in a headless browser
        sys.path.insert(0, SANDBOX)
        from find_chrome import WHY_NOT, find_chrome
        browser = find_chrome()
        print(f"Browser checks: {browser}" if browser else f"Browser checks will be SKIPPED: {WHY_NOT}", flush=True)
    print(flush=True)
    marks = f"{suite} and live" if url else f"{suite} and not live"
    code = subprocess.run([sys.executable, "-m", "pytest", "-v", "-rs", "-m", marks, "tests/unit", "tests/sandbox"], cwd=REPO, env=env).returncode
    done = ("every check passed" + (", but the browser checks were skipped (no browser found, see above)." if not url and not browser else ".")
            if code == 0 else "no checks ran." if code == 5 else "SOME CHECKS FAILED (see above).")
    print(f"\n{SUITES[suite][0].upper() + SUITES[suite][1:]}: {done}", flush=True)
    sys.exit(code)


def cmd_check():
    codes = [subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/unit", "tests/sandbox"], cwd=REPO).returncode,
             subprocess.run([sys.executable, "-m", "ruff", "check", "."], cwd=REPO).returncode]
    sys.exit(max(codes))


def main():
    ensure_env()
    ap = argparse.ArgumentParser(description="Studio's offline sandbox", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("command", nargs="?", default="start", choices=["start", "jobs", "last-job", "queue-bad", "reset", "check", "test"],
                    help="start (the default: what npm run sandbox runs) runs the app; the others look at what it did or test it")
    ap.add_argument("suite", nargs="?", choices=sorted(SUITES), help="test: which suite (npm run test:mcp, npm run test:generators)")
    ap.add_argument("--url", help="test: check a real, running Studio instead of the sandbox, e.g. http://192.168.1.20:8188")
    ap.add_argument("--render", action="store_true", help="test --url: let the live checks render on that Studio's GPU too (slower)")
    data = ap.add_mutually_exclusive_group()
    data.add_argument("--keep", action="store_true", help="start on the data left from last time instead of fresh demo data")
    data.add_argument("--empty", action="store_true", help="start with no data at all")
    ap.add_argument("--port", type=int, default=8188)
    ap.add_argument("--no-open", action="store_true", help="don't open Studio in the browser")
    ap.add_argument("-n", type=int, default=20)
    a = ap.parse_args()
    if a.command == "start":
        start("serve" if a.keep else "empty" if a.empty else "demo", a.port, not a.no_open)
    elif a.command == "jobs":
        cmd_jobs(a.n)
    elif a.command == "last-job":
        cmd_last_job()
    elif a.command == "queue-bad":
        cmd_queue_bad(a.port)
    elif a.command == "reset":
        shutil.rmtree(RUN, ignore_errors=True)
        shutil.rmtree(JOBS, ignore_errors=True)
        print("Sandbox data deleted.")
    elif a.command == "check":
        cmd_check()
    elif a.command == "test":
        if not a.suite:
            ap.error("test needs a suite: " + ", ".join(sorted(SUITES)))
        cmd_test(a.suite, a.url, a.render)


if __name__ == "__main__":
    main()
