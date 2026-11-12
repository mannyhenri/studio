"""Every model choice in every app can reach a Mac card: none is refused for its format, its size or a missing node.

  ~/code/comfy-mac/.venv/bin/python tests/test_mac_choices.py     (needs a Mac worker on 127.0.0.1:8189, ~/code/comfy-mac;
                                                                    the PC on desktop-mggcgsg.local:8188 for its own lists)

For EVERY app x EVERY option of each of its "choice" inputs, the job Studio would send is built and gated against the
Mac's real /object_info. A choice passes when the Mac can run it now, or each file it lacks can get there: it is in
comfy-mac's Hugging Face index (the Mac downloads it) or in the PC's own lists (the PC pushes it), and comfy-mac's
model_plan doesn't block it. It fails when a node is missing, a loader has no model folder, or a file exists nowhere.
"untested" formats still go (they download with a note) and are listed. Nothing is downloaded or rendered.
"""
import glob, importlib.util, json, os, shutil, sys, tempfile, types, urllib.request, asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "ComfyUI-MannyStudio")
MAC = os.path.expanduser(os.environ.get("COMFY_MAC", "~/code/comfy-mac"))
MAC_URL = os.environ.get("MAC_URL", "http://127.0.0.1:8189")
PC_URL = os.environ.get("PC_URL", "http://desktop-mggcgsg.local:8188")
fails, notes = [], []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


tmp = os.path.realpath(tempfile.mkdtemp(prefix="studio-choices-"))
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
sys.path.insert(0, MAC)
import model_plan  # noqa: E402


def get(url, timeout=120):
    return json.load(urllib.request.urlopen(url, timeout=timeout))


async def main():
    mac_oi = get(MAC_URL + "/object_info")
    mac_sv = get(MAC_URL + "/mac/info").get("self_validated")
    try:
        pc_oi = get(PC_URL + "/object_info")
    except Exception as e:
        pc_oi = {}
        print(f"NOTE the PC isn't reachable ({e}): files only the PC has count as unreachable")
    idx = json.load(open(model_plan.INDEX))
    base = lambda v: v.replace("\\", "/").split("/")[-1]

    async def oi(force=False):
        return mac_oi
    studio._get_object_info = oi
    rows = 0
    for app in studio._list_apps():
        d = studio._describe_app(app)
        choices = [i for i in d["inputs"] if i["kind"] == "choice" and i.get("options")]
        for inp in choices or [None]:
            for opt in (inp["options"] if inp else [None]):
                rows += 1
                vals = {inp["key"]: opt} if inp else {}
                tag = f"{os.path.basename(app)[:-9]} [{opt}]" if inp else os.path.basename(app)[:-9]
                try:
                    _wf, api = await studio._build_prompt(app, vals)
                except Exception as e:
                    check(False, f"{tag}: Studio can't build it ({str(e)[:80]})")
                    continue
                why = remote.gate(api, mac_oi, mac_sv)
                bad, how = [], []
                for x in why:
                    ct, name, v = x.get("class_type"), x.get("input"), x.get("value")
                    if not (name and isinstance(v, str)):
                        bad.append(x["reason"])
                        continue
                    folder = model_plan.FOLDERS.get((ct, name))
                    if not folder:
                        bad.append(f"{ct}.{name} has no model folder, so “{v}” can't be fetched or pushed")
                        continue
                    blk = model_plan.blocked((ct, name), v)
                    if blk and blk[0] != "untested":
                        bad.append(f"{v}: {blk[1]}")
                        continue
                    if blk:
                        notes.append(f"{tag}: {v} ({blk[0]}: runs on {'the CPU encoder' if (ct, name) in model_plan.CPU_LOADERS else 'a card, not yet rendered'})")
                    on_pc = v in (remote.combo(remote._spec(pc_oi.get(ct), name) or []) or [[]])[0]
                    if "/" not in v.replace("\\", "/") and idx.get(base(v)):
                        how.append("HF")
                    elif on_pc:
                        how.append("PC push")
                    else:
                        bad.append(f"{v} is on neither Hugging Face nor the PC")
                check(not bad, f"{tag}: {'runs as is' if not why else 'reaches the Mac (' + ', '.join(sorted(set(how))) + ')'}"
                               + (f" -- {'; '.join(bad)[:200]}" if bad else ""))
    check(rows > 0, f"{rows} app choices checked")
    for n in sorted(set(notes)):
        print("NOTE", n)
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
