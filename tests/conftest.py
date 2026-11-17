import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(REPO, "sandbox"), os.path.join(REPO, "sandbox", "comfy_stub")):
    if p not in sys.path:
        sys.path.insert(0, p)
