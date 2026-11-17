#!/usr/bin/env python3
"""Studio's offline sandbox: Studio inside a stand-in ComfyUI on Linux, no GPU, no internet.

    npm run sandbox                       everything in one go: set up (first time), fresh demo data, start, open the browser
    python3 scripts/sandbox.py demo       fresh demo data (The Glass Harbour's 3 clips and 5 pictures, nothing else) and start
    python3 scripts/sandbox.py empty      start with no data at all
    python3 scripts/sandbox.py serve      start on the data already there
    python3 scripts/sandbox.py jobs       the jobs Studio sent to the stand-in, newest first (-n 20)
    python3 scripts/sandbox.py last-job   the last job: app, models, size, prompt, result files
    python3 scripts/sandbox.py queue-bad  queue a job with a model the sandbox doesn't have (shows ComfyUI's refusal)
    python3 scripts/sandbox.py reset      delete the sandbox data
    python3 scripts/sandbox.py check      unit tests, sandbox tests and ruff

demo, empty and serve take --open (open the browser) and --port (default 8188). The first run creates .venv with
scripts/setup-linux.sh. Studio is at http://localhost:8188/studio; Ctrl+C stops it."""
import argparse
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX = os.path.join(REPO, "sandbox")
RUN = os.environ.get("SANDBOX_RUN") or os.path.join(SANDBOX, "run")        # the ComfyUI folders (tests use their own)
JOBS = os.environ.get("SANDBOX_JOBS") or os.path.join(SANDBOX, "jobs")
VENV_PY = os.path.join(REPO, ".venv", "bin", "python")


def ensure_env():
    """Re-run this script with the sandbox's own Python (.venv), creating it the first time."""
    if os.path.realpath(sys.prefix) == os.path.realpath(os.path.join(REPO, ".venv")):
        return
    if not os.path.isfile(VENV_PY):
        print("First run: setting up the sandbox (scripts/setup-linux.sh)...", flush=True)
        r = subprocess.run(["bash", os.path.join(REPO, "scripts", "setup-linux.sh")], cwd=REPO, env=dict(os.environ, SANDBOX_FROM_CLI="1"))
        if r.returncode != 0:
            sys.exit(r.returncode)
    os.execv(VENV_PY, [VENV_PY, os.path.abspath(__file__), *sys.argv[1:]])


def env_for_server():
    env = dict(os.environ)
    home = os.path.join(RUN, "home")
    os.makedirs(home, exist_ok=True)
    env.update(HOME=home, STUDIO_URL="", PYTHONUNBUFFERED="1", STUDIO_SANDBOX="1")
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


def start(kind, port, open_browser):
    if not port_free(port):
        raise SystemExit(f"Port {port} is in use (another sandbox?). Stop it, or use --port.")
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
        page = f"http://localhost:{port}/studio"
        print(f"\nStudio (sandbox) is running at {page}\nJobs go to the stand-in ComfyUI and are saved in sandbox/jobs/. Ctrl+C stops it.\n", flush=True)
        if open_browser:
            try:
                if not webbrowser.open(page):
                    print("(No browser found here: open the address above.)")
            except Exception:
                print("(Couldn't open a browser here: open the address above.)")
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


def cmd_check():
    codes = [subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/unit", "tests/sandbox"], cwd=REPO).returncode,
             subprocess.run([sys.executable, "-m", "ruff", "check", "."], cwd=REPO).returncode]
    sys.exit(max(codes))


def main():
    ensure_env()
    ap = argparse.ArgumentParser(description="Studio's offline sandbox", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("command", choices=["demo", "empty", "serve", "jobs", "last-job", "queue-bad", "reset", "check"])
    ap.add_argument("--port", type=int, default=8188)
    ap.add_argument("--open", action="store_true", help="open Studio in the browser")
    ap.add_argument("-n", type=int, default=20)
    a = ap.parse_args()
    if a.command in ("demo", "empty", "serve"):
        start(a.command, a.port, a.open)
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


if __name__ == "__main__":
    main()
