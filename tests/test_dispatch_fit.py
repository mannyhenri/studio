"""Auto-sharing the queue never keeps a job off a Mac card for its SIZE (confer c301a683, revise).

  ~/code/comfy-mac/.venv/bin/python tests/test_dispatch_fit.py      (needs the PC's model share for real file sizes)

The ask: "I don't want to hear a model doesn't fit on the card. They're two 32GB cards." The Vegas run models bigger than
the card: fp8 stays fp8 in RAM and goes to the card a layer at a time, text encoders run on the CPU or the other card,
LoRA budgets are proportional (comfy-mac). Proven live: Wan 2.2 14B fp8 x2 (about 29 GB of model files) on mac-vega-a.
dispatch.py (PC snapshot 2890bc3) refused any job whose model files passed 80% of the card. For EVERY app x choice x
graph-changing toggle, with each model file at its real size on the PC, dispatch.unfit gives no size reason for a 32 GB
Vega. A job that already failed on a card is still kept off it (that rule stays).
"""
import glob, importlib.util, json, os, shutil, sys, tempfile, types, urllib.parse, urllib.request, asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "ComfyUI-MannyStudio")
PC_URL = os.environ.get("PC_URL", "http://192.168.4.90:8188")
VEGA = {"label": "mac-vega-a", "host": "127.0.0.1", "port": 8188, "device": {"name": "AMD Radeon Pro Vega II", "vram_total": 32625813094}}
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-dispatch-fit-"))
for d in ("user/default/workflows", "input", "output", "temp"):
    os.makedirs(os.path.join(tmp, d))
for f in glob.glob(os.path.join(ROOT, "workflows", "*.app.json")):
    shutil.copy(f, os.path.join(tmp, "user/default/workflows"))
fp = types.ModuleType("folder_paths")
fp.get_user_directory = lambda: os.path.join(tmp, "user")
fp.get_input_directory = lambda: os.path.join(tmp, "input")
fp.get_output_directory = lambda: os.path.join(tmp, "output")
fp.get_temp_directory = lambda: os.path.join(tmp, "temp")
fp.get_filename_list = lambda *a: []
fp.get_folder_paths = lambda *a: []
fp.models_dir = os.path.join(tmp, "models")
fp.folder_names_and_paths = {}
SIZES = {}                                     # (kind, name) -> real size on the PC, or None


def full_path(kind, name):
    """A sparse stand-in for the PC's file, at its real size (asked of the PC's model share once)."""
    k = (kind, name.replace("\\", "/"))
    if k not in SIZES:
        SIZES[k] = None
        try:
            r = urllib.request.urlopen(urllib.request.Request(f"{PC_URL}/manny/api/models/file/{kind}/{urllib.parse.quote(k[1])}", method="HEAD"), timeout=30)
            SIZES[k] = int(r.headers["Content-Length"])
        except Exception:
            pass
    if not SIZES[k]:
        return None
    p = os.path.join(tmp, "models", kind, *k[1].split("/"))
    if not os.path.isfile(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.truncate(SIZES[k])
    return p
fp.get_full_path = full_path
sys.modules["folder_paths"] = fp


class _R:
    def __getattr__(self, m):
        return lambda path: (lambda fn: fn)


srv = types.ModuleType("server")
srv.PromptServer = type("PS", (), {"instance": types.SimpleNamespace(routes=_R(), app=types.SimpleNamespace(middlewares=[]), port=1,
                                                                   loop=None, prompt_queue=None, send_sync=lambda *a, **k: None)})
sys.modules["server"] = srv
spec = importlib.util.spec_from_file_location("studio", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
studio = importlib.util.module_from_spec(spec)
sys.modules["studio"] = studio
spec.loader.exec_module(studio)
remote = sys.modules["studio.remote"]
dispatch = sys.modules["studio.dispatch"]


async def main():
    pc_oi = json.load(urllib.request.urlopen(PC_URL + "/object_info", timeout=120))   # the jobs as the PC builds them

    async def oi(force=False):
        return pc_oi
    studio._get_object_info = oi
    api = None
    rows, big, refused = 0, [], []
    for app in studio._list_apps():
        d = studio._describe_app(app)
        choices = [i for i in d["inputs"] if i["kind"] == "choice" and i.get("options")]
        toggles = [i for i in d["inputs"] if i["kind"] == "toggle"]
        for inp in choices or [None]:
            for opt in (inp["options"] if inp else [None]):
                for tg in [None] + toggles:
                    vals = {inp["key"]: opt} if inp else {}
                    if tg:
                        vals[tg["key"]] = True
                    tag = (f"{os.path.basename(app)[:-9]} [{opt}]" if inp else os.path.basename(app)[:-9]) + (f" + {tg['label']}" if tg else "")
                    try:
                        _wf, api = await studio._build_prompt(app, vals)
                    except Exception:
                        continue
                    rows += 1
                    need = sum(next((SIZES.get((k, m)) or 0 for k in dispatch.MODEL_KINDS if full_path(k, m)), 0) for m in dispatch._models(api))
                    if need > 0.8 * VEGA["device"]["vram_total"]:
                        big.append(f"{tag} {need / 1e9:.1f} GB")
                    why = dispatch.unfit(VEGA, api)
                    if why:
                        refused.append(f"{tag}: {why}")
    check(rows > 50 and sum(1 for v in SIZES.values() if v) > 10, f"{rows} app x choice x toggle jobs built, {sum(1 for v in SIZES.values() if v)} model files sized from the PC")
    print(f"NOTE jobs over 80% of a Vega in model files: {len(big)}: {big[:8]}")
    check(not refused, f"no job is kept off a 32 GB Vega for its size: {len(refused)} refused: {refused[:6]}")
    # the put-back rule stays: a job whose models failed on that card isn't sent there again
    dispatch._avoid_load = lambda: [{"worker": VEGA["label"], "sig": dispatch._sig(api), "why": "MPS backend out of memory"}]
    check(bool(dispatch.unfit(VEGA, api)), "a job whose models failed on that card is still kept off it")


asyncio.run(main())
print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
