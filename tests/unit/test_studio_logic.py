"""Studio's own logic tests that need no GPU machine and no server (they live in tests/live with the others, since
they are run the same way, as scripts)."""
import os
import subprocess
import sys

import pytest

LIVE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "live")
STUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "sandbox", "comfy_stub")
OFFLINE = ["test_fit_pair.py", "test_fit_return.py", "test_model_share.py", "test_pair.py", "test_share_big.py", "test_share_resolve.py"]


@pytest.mark.parametrize("script", OFFLINE)
def test_offline_script(script, tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=STUB, OBJECT_INFO="http://127.0.0.1:9/none")
    r = subprocess.run([sys.executable, script], cwd=LIVE, capture_output=True, text=True, timeout=600, env=env)
    assert r.returncode == 0 and "all passed" in r.stdout and "FAIL" not in r.stdout, (r.stdout + r.stderr)[-3000:]
