"""Both Vegas on one job (confer f014f890): a card encoding another job's prompt counts as busier for "Any free GPU".

  ~/code/comfy-mac/.venv/bin/python tests/test_pair.py        (no workers needed)

comfy-mac runs a job's text encoder on the OTHER card when it's idle (/mac/te) and reports it in /mac/info as "te_for"
(the worker whose prompt it encodes). For EVERY combination of states of two cards (idle, lending its GPU to the other's
text encoder, running one of our jobs, both) "auto" must pick the least busy card, a lending card counting one job
busier, ties to the card listed first; and an older worker without the field is treated as not lending.
"""
import asyncio, importlib, itertools, os, sys, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.modules["studio"] = types.ModuleType("studio")
sys.modules["studio"].__path__ = [os.path.join(ROOT, "ComfyUI-MannyStudio")]
remote = importlib.import_module("studio.remote")
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


STATES = {   # name -> (/mac/info extras, queue_remaining)
    "idle": ({}, 0),
    "lending": ({"te_for": "other"}, 0),
    "busy": ({}, 1),
    "busy+lending": ({"te_for": "other"}, 1),
    "old-worker": (None, 0),          # no te_for field at all
}
WEIGHT = {"idle": 0, "lending": 1, "busy": 1, "busy+lending": 2, "old-worker": 0}


async def main():
    W = [{"label": "mac-vega-a", "host": "h", "port": 1}, {"label": "mac-vega-b", "host": "h", "port": 2}]
    state = {}

    async def fake_check(w, api):
        return []

    async def fake_info(w):
        extra, q = STATES[state[w["label"]]]
        out = {"label": w["label"], "queue_remaining": q}
        if extra is not None:
            out.update(extra)
        return out
    remote.check, remote.info = fake_check, fake_info
    for sa, sb in itertools.product(STATES, STATES):
        state.update({"mac-vega-a": sa, "mac-vega-b": sb})
        label, _notes = await remote.pick(W, {}, local_busy=99, local_ok=False)
        want = "mac-vega-a" if WEIGHT[sa] <= WEIGHT[sb] else "mac-vega-b"
        check(label == want, f"a {sa}, b {sb}: auto picks {label} (want {want})")
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
