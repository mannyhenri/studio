"""The PC must allow its Mac by the address the Mac really connects from (fit of the model share, confer c301a683).

  ~/code/comfy-mac/.venv/bin/python tests/test_share_resolve.py        (no network needed)

Live: Studio on the Windows PC answered 403 to Mannys-Mac-Pro.local, from its IPv4 (192.168.4.40) and from its stable
IPv6. Windows resolves a .local name asked without a family to IPv6 only, so the Mac's IPv4 never got into the
allow-list. For EVERY way a resolver can answer (IPv6 only unless asked for IPv4; IPv4 only unless asked for IPv6;
both; a family that errors), modelshare._resolve must return the union of what either family gives.

Live, later: a 16 GB pull from the Mac ran for minutes, then every piece came back 403 (with the Mac's address unchanged).
The PC looks the workers up again every TTL, and a .local (mDNS) lookup on Windows sometimes finds nothing; one empty
answer emptied the allow-list. For EVERY sequence of lookups (good then empty; good then failing; good then a new
address), _allowed_ips must keep the host's last good addresses while a lookup finds none, and take the new ones when found.
"""
import asyncio, importlib, os, socket, sys, types

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

NEW = "192.168.4.41"
SEQUENCES = {   # name -> what each successive lookup of the Mac finds, and what must be allowed after each
    "good, then empty": [({V4}, {V4}), (set(), {V4}), (set(), {V4})],
    "good, then failing": [({V4}, {V4}), (None, {V4})],
    "good, then a new address": [({V4}, {V4}), ({NEW}, {NEW})],
    "never found": [(set(), set()), (None, set())],
}
W = [{"label": "mac-vega-a", "host": "Mannys-Mac-Pro.local"}, {"label": "pc-box", "host": "other.local"}]
real_resolve = share._resolve
for name, seq in SEQUENCES.items():
    share._cache.update(key=None, at=0, ips=frozenset())
    share._known.clear() if hasattr(share, "_known") else None
    got_all, want_all = [], []
    for found, want in seq:
        def res(h, _f=found):
            if h != "mannys-mac-pro.local":
                return {share._ip("10.0.0.9")}
            if _f is None:
                raise OSError("lookup failed")
            return {share._ip(x) for x in _f}
        share._resolve = res
        share._cache["at"] = 0                 # the TTL has passed: look up again
        try:
            ips = asyncio.run(share._allowed_ips(W))
        except Exception as e:
            ips = {f"error {e}"}
        got_all.append(sorted(str(x) for x in ips if str(x) != "10.0.0.9"))
        want_all.append(sorted(want))
    check(got_all == want_all, f"{name}: allowed after each lookup {got_all} (want {want_all})")
# a last good address is kept for KNOWN_MAX at most: past that, a host whose lookups keep failing is no longer allowed
share._cache.update(key=None, at=0, ips=frozenset())
share._known.clear()
real_time, now = share.time.time, [1_000_000.0]
share.time.time = lambda: now[0]
got = []
for dt, found in ((0, {V4}), (3600, None), (25 * 3600, None), (60, {NEW})):
    now[0] += dt
    share._resolve = lambda h, _f=found: (_ for _ in ()).throw(OSError("lookup failed")) if _f is None else {share._ip(x) for x in _f}
    share._cache["at"] = 0
    got.append(sorted(str(x) for x in asyncio.run(share._allowed_ips(W[:1]))))
share.time.time = real_time
check(got == [[V4], [V4], [], [NEW]], f"last good kept 1 h, dropped after 24 h of failed lookups, new one taken: {got}")
share._resolve = real_resolve
print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
