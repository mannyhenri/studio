"""The live tests: scripts that need the real GPU machines (the Mac Pro workers) or this PC's ComfyUI with its models.
Run one directly (python tests/live/test_remote.py, see README), or all of them through pytest with STUDIO_LIVE=1:

    STUDIO_LIVE=1 python -m pytest tests/live

Without STUDIO_LIVE=1 they are skipped. The scripts run at import, so pytest never imports them; test_live_suite.py runs each
as a subprocess."""
import glob
import os

collect_ignore = [os.path.basename(p) for p in glob.glob(os.path.join(os.path.dirname(__file__), "test_*.py"))
                  if not p.endswith("test_live_suite.py")] + ["probes.py"]
