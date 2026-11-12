"""Live: what Studio shows and decides for the two Vegas comes from what they really report (confer f014f890).

  ~/code/comfy-mac/.venv/bin/python tests/test_pair_live.py        (needs mac-vega-a/b on 127.0.0.1:8188/8189, node)

For EVERY worker: its /mac/info carries vram_usable, vram_in_use, vram_cached (int or null) and te_for (present, a
worker label or null); Studio's own cardMem() (run from web/app.js under node) turns that into "x/y GB in use" with no
"too small"/"doesn't fit" wording; and remote.pick, reading the same live info, treats a card as one job busier exactly
when te_for is set.
"""
import asyncio, importlib, json, os, re, subprocess, sys, types, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.modules["studio"] = types.ModuleType("studio")
sys.modules["studio"].__path__ = [os.path.join(ROOT, "ComfyUI-MannyStudio")]
remote = importlib.import_module("studio.remote")
WORKERS = [{"label": "mac-vega-a", "host": "127.0.0.1", "port": 8188}, {"label": "mac-vega-b", "host": "127.0.0.1", "port": 8189}]
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


def card_mem_js(info):
    src = open(os.path.join(ROOT, "ComfyUI-MannyStudio", "web", "app.js"), encoding="utf-8").read()
    fn = re.search(r"function cardMem\(info\) \{.*?\n\}", src, re.S).group(0)
    js = fn + f"\nprocess.stdout.write(cardMem({json.dumps(info)}));"
    return subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30).stdout


async def main():
    infos = {}
    for w in WORKERS:
        info = json.load(urllib.request.urlopen(f"{remote.base(w)}/mac/info", timeout=10))
        infos[w["label"]] = info
        types_ok = all(k in info and (info[k] is None or isinstance(info[k], int)) for k in ("vram_usable", "vram_in_use", "vram_cached"))
        check(types_ok and "te_for" in info and (info["te_for"] is None or isinstance(info["te_for"], str)),
              f"{w['label']} reports the pair fields: { {k: info.get(k) for k in ('vram_usable', 'vram_in_use', 'vram_cached', 'te_for')} }")
        shown = card_mem_js(info)
        check(shown and ("GB in use" in shown if info.get("vram_usable") else "GB card" in shown)
              and not re.search(r"too small|doesn.t fit|not enough", shown, re.I), f"{w['label']} in Settings: {shown!r}")
    # pick on the live info: a lending card (te_for) is one job busier, nothing else changes
    real_info = remote.info

    async def ok(w, api):
        return []
    remote.check = ok
    label, _ = await remote.pick(WORKERS, {}, local_busy=99, local_ok=False)
    busy = {l: (i.get("queue_remaining") or 0) + (1 if i.get("te_for") else 0) for l, i in infos.items()}
    want = min(WORKERS, key=lambda w: (busy[w["label"]], WORKERS.index(w)))["label"]
    check(label == want, f"Any free GPU on the live cards picks {label} (busy {busy})")
    remote.info = real_info
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
