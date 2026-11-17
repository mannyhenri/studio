"""Starts the sandbox (the stand-in ComfyUI with Studio's pack) in temporary folders: one with the demo data, one empty.
Each server runs on a free port with its own HOME, and records any Windows-only program Studio tries to start."""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WINDOWS_ONLY = ("powershell", "powershell.exe", "wevtutil", "powercfg", "cmd.exe", "tasklist", "wmic")

SITECUSTOMIZE = '''
import os, subprocess
_log = os.environ.get("SANDBOX_WINLOG")
_orig = subprocess.Popen.__init__
def _init(self, args, *a, **k):
    try:
        exe = os.path.basename(str(args[0] if isinstance(args, (list, tuple)) else str(args).split()[0])).lower()
        if _log and exe in %r:
            with open(_log, "a") as f:
                f.write(exe + "\\n")
    except Exception:
        pass
    return _orig(self, args, *a, **k)
subprocess.Popen.__init__ = _init
''' % (WINDOWS_ONLY,)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_cli(run, jobs):
    os.environ["SANDBOX_RUN"], os.environ["SANDBOX_JOBS"] = run, jobs
    spec = importlib.util.spec_from_file_location(f"sandbox_cli_{os.path.basename(run)}", os.path.join(REPO, "scripts", "sandbox.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Sandbox:
    def __init__(self, base, kind):
        self.run, self.jobs = str(base / "run"), str(base / "jobs")
        self.winlog = str(base / "windows-calls.log")
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.cli = load_cli(self.run, self.jobs)
        self.cli.prepare(fresh=True)
        site = base / "site"
        site.mkdir()
        (site / "sitecustomize.py").write_text(SITECUSTOMIZE)
        env = self.cli.env_for_server()
        env.update(PYTHONPATH=str(site), SANDBOX_WINLOG=self.winlog, SANDBOX_RENDER_SECONDS="0.2")
        self.log = open(base / "server.log", "w")
        self.proc = subprocess.Popen([sys.executable, os.path.join(REPO, "sandbox", "fake_comfy.py"), "--root", self.run,
                                      "--jobs", self.jobs, "--port", str(self.port)], cwd=REPO, env=env, stdout=self.log,
                                     stderr=subprocess.STDOUT)
        self.health = self.cli.wait_healthy(self.url, self.proc)
        if kind == "demo":
            sys.path.insert(0, os.path.join(REPO, "sandbox"))
            import demo
            self.seeded = demo.seed(self.url, self.run)

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}

    def post(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait_done(self, pids, limit=120):
        t0 = time.time()
        done = {}
        while time.time() - t0 < limit and len(done) < len(pids):
            for pid in pids:
                h = self.get(f"/history/{pid}").get(pid)
                if h and h.get("status"):
                    done[pid] = h
            time.sleep(0.2)
        return done

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    s = Sandbox(tmp_path_factory.mktemp("demo"), "demo")
    yield s
    s.stop()


@pytest.fixture(scope="session")
def empty(tmp_path_factory):
    s = Sandbox(tmp_path_factory.mktemp("empty"), "empty")
    yield s
    s.stop()


@pytest.fixture(scope="module")
def scratch(tmp_path_factory):
    """An empty sandbox of the test module's own, for tests that change settings (a password, Incognito data)."""
    s = Sandbox(tmp_path_factory.mktemp("scratch"), "empty")
    yield s
    s.stop()
