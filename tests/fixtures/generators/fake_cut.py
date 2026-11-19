#!/usr/bin/env python3
"""A stand-in for Lossless Cut in the AI tools' tests: the same command line and progress JSON (outs + pieces). Writes one
small JSON file per piece into --out (a folder) and says each piece starts half a second before it was asked, as a
keyframe cut does; one piece reports a bad shape so the server's cleaning is exercised."""
import json
import os
import sys

a = sys.argv[1:]


def opt(flag, default=None):
    return a[a.index(flag) + 1] if flag in a else default


out, prog = opt("--out"), opt("--progress")
segs = [tuple(map(float, s.split("-"))) for s in opt("--segments").split(",")]


def report(**k):
    tmp = prog + ".tmp"
    with open(tmp, "w") as f:
        json.dump(k, f)
    os.replace(tmp, prog)


outs, pieces = [], []
for i, (s, e) in enumerate(segs):
    f = os.path.join(out, f"piece {i + 1:02d}.json")
    with open(f, "w") as fh:
        json.dump({"args": a, "segment": [s, e]}, fh)
    outs.append(f)
    pieces.append({"n": i + 1, "file": f, "asked_start": s, "asked_end": e, "start": max(0.0, s - 0.5), "end": e,
                   "mode": "copy", "reencoded": [[s, s + 0.25], "junk"], "note": "x" * 400, "secret": "/etc/passwd"})
    report(stage=f"Cutting part {i + 1} of {len(segs)}", done=i + 1, total=len(segs), outs=outs, pieces=pieces)
report(stage="done", done=len(segs), total=len(segs), outs=outs, pieces=pieces, note=f"{len(segs)} pieces")
