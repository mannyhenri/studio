"""The PC must allow its Mac by the address the Mac really connects from (fit of the model share, confer c301a683).

  ~/code/comfy-mac/.venv/bin/python tests/test_share_resolve.py        (no network needed)

Live: Studio on the Windows PC answered 403 to Mannys-Mac-Pro.local, from its IPv4 (192.168.4.40) and from its stable
IPv6. Windows resolves a .local name asked without a family to IPv6 only, so the Mac's IPv4 never got into the
allow-list. For EVERY way a resolver can answer (IPv6 only unless asked for IPv4; IPv4 only unless asked for IPv6;
both; a family that errors), modelshare._resolve must return the union of what either family gives.
"""
import importlib, os, socket, sys, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fp = types.ModuleType("folder_paths")
fp.folder_names_and_paths, fp.get_full_path, fp.get_filename_list = {}, lambda *a: None, lambda *a: []
sys.modules["folder_paths"] = fp
sys.modules["studio"] = types.ModuleType("studio")
sys.modules["studio"].__path__ = [os.path.join(ROOT, "ComfyUI-MannyStudio")]
share = importlib.import_module("studio.modelshare")
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


V4, V6 = "192.168.4.40", "fd67:faf6:9d51:1:c14:9396:85bb:2075"
A4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", (V4, 0))
A6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (V6, 0, 0, 0))
RESOLVERS = {   # name -> answer for family 0 (unspecified), AF_INET, AF_INET6 (None = raises)
    "windows .local (IPv6 unless asked)": {0: [A6], socket.AF_INET: [A4], socket.AF_INET6: [A6]},
    "IPv4 unless asked": {0: [A4], socket.AF_INET: [A4], socket.AF_INET6: [A6]},
    "both": {0: [A4, A6], socket.AF_INET: [A4], socket.AF_INET6: [A6]},
    "IPv6 lookup errors": {0: [A4], socket.AF_INET: [A4], socket.AF_INET6: None},
}
real = socket.getaddrinfo
for name, answers in RESOLVERS.items():
    def gai(host, port=None, family=0, *a, _ans=answers, **k):
        fam = k.get("family", family)
        r = _ans.get(fam)
        if r is None:
            raise socket.gaierror("no address")
        return r
    socket.getaddrinfo = gai
    got = {str(x) for x in share._resolve("Mannys-Mac-Pro.local")}
    want = {str(share._ip(x[4][0])) for fam in (0, socket.AF_INET, socket.AF_INET6) for x in (answers.get(fam) or [])}
    check(got == want, f"{name}: allows {sorted(got)} (want {sorted(want)})")
socket.getaddrinfo = real
print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
