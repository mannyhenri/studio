"""The screenplay editor's rules in JavaScript (ComfyUI-Studio/web/screenplay/rules.js), run with node's own test
runner on the tests in tests/unit/screenplay/. Skipped where node isn't installed (Studio itself doesn't need node)."""
import glob
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node isn't installed")
@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(HERE, "screenplay", "*.test.mjs"))), ids=os.path.basename)
def test_screenplay_js(path):
    r = subprocess.run([NODE, "--no-warnings", "--test", path], capture_output=True, text=True, timeout=300)
    failed = [line for line in r.stdout.splitlines() if line.startswith("not ok") or "error:" in line]
    assert r.returncode == 0, "\n".join(failed) or (r.stdout + r.stderr)[-3000:]
    assert "# fail 0" in r.stdout
