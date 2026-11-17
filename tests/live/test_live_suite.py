"""Each live script as one pytest test (skipped unless STUDIO_LIVE=1)."""
import glob
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, "test_*.py")) if not p.endswith("test_live_suite.py"))

pytestmark = pytest.mark.skipif(os.environ.get("STUDIO_LIVE") != "1", reason="live test: set STUDIO_LIVE=1 (needs the GPU machines)")


@pytest.mark.parametrize("script", SCRIPTS)
def test_live_script(script):
    r = subprocess.run([sys.executable, script], cwd=HERE, capture_output=True, text=True, timeout=7200)
    assert r.returncode == 0 and "FAIL" not in r.stdout, (r.stdout + r.stderr)[-4000:]
