#!/usr/bin/env bash
# Sets up Studio's offline sandbox on Linux: a Python environment in .venv (aiohttp, numpy, pillow, av, pytest, ruff;
# no torch, no GPU), ffmpeg, and the sample media. Safe to run again. npm run sandbox runs it by itself the first time (and
# again when sandbox/requirements.txt changes, or when a setup stopped half-way): there's no need to run it by hand.
# Only pip (PyPI) is contacted, and only when something is missing.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
say() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nSetup failed: %s\n' "$1" >&2; exit 1; }

say "Checking Python"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || fail "python3 is not installed."
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || fail "Python 3.10 or newer is needed ($("$PY" --version))."
"$PY" -c 'import venv, ensurepip' 2>/dev/null || fail "Python's venv module is missing (Debian/Ubuntu: apt-get install python3-venv)."
"$PY" --version

say "Python packages (.venv)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r sandbox/requirements.txt
.venv/bin/python -c "import aiohttp, av, numpy, PIL; print('aiohttp', aiohttp.__version__, '· av', av.__version__, '· numpy', numpy.__version__)"

say "ffmpeg"
if command -v ffmpeg >/dev/null 2>&1; then
  ffmpeg -version | head -1
else
  echo "No ffmpeg on PATH: installing the imageio-ffmpeg build into .venv"
  .venv/bin/python -m pip install -q imageio-ffmpeg
  FF="$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
  ln -sf "$FF" .venv/ffmpeg && ln -sf "$FF" .venv/bin/ffmpeg
  .venv/ffmpeg -version | head -1
fi

say "Sample media (sandbox/media)"
PATH="$ROOT/.venv/bin:$PATH" .venv/bin/python sandbox/make_media.py

# done: npm run sandbox checks this stamp (the requirements it was made for) before starting
.venv/bin/python -c 'import hashlib; print(hashlib.sha256(open("sandbox/requirements.txt", "rb").read()).hexdigest()[:16])' > .venv/.sandbox-ready

[ -n "${SANDBOX_FROM_CLI:-}" ] && exit 0
cat <<'TXT'

Ready. Start Studio (demo data) and open it in the browser with:
  npm run sandbox
TXT
