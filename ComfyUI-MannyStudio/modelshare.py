"""Share model files with this PC's GPU machines, so a Mac can fetch what isn't on Hugging Face (Civitai LoRAs etc.).

  HEAD/GET /manny/api/models/file/{folder}/{name}   the file (Range for resume), X-Studio-SHA256 when a sidecar has it
  GET      /manny/api/models/index?folder=<key>     {"files": [{"name", "size", "mtime"}]}, names with "/"

Only this PC and the hosts in workers.json (every address they resolve to) may call; everyone else gets 403.
Files are found only through folder_paths, so nothing outside the model folders is reachable, and nothing in
the Incognito space is ever served even if a model path points there."""
import asyncio
import ipaddress
import os
import re
import socket
import time

from aiohttp import web

import folder_paths

H = {}
TTL = 60                                 # seconds a worker's resolved addresses are trusted
NOT_MODELS = {"custom_nodes", "configs"}  # folder keys that aren't model files
_cache = {"key": None, "at": 0, "ips": frozenset()}


def _ip(s):
    """An address as ipaddress, IPv4-mapped IPv6 as IPv4 and any zone id dropped; None if it isn't one."""
    try:
        a = ipaddress.ip_address((s or "").split("%")[0])
    except ValueError:
        return None
    return a.ipv4_mapped if a.version == 6 and a.ipv4_mapped else a


def _resolve(host):
    try:
        return {a for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM) if (a := _ip(info[4][0]))}
    except Exception:
        return set()                     # a host that doesn't resolve contributes nothing


async def _allowed_ips(workers):
    key = tuple(sorted({w["host"].lower() for w in workers}))
    if _cache["key"] != key or time.time() - _cache["at"] > TTL:
        found = await asyncio.gather(*(asyncio.to_thread(_resolve, h) for h in key))
        _cache.update(key=key, at=time.time(), ips=frozenset().union(*found))
    return _cache["ips"]


async def _check(request):
    """403 unless the caller is this PC or one of its GPU machines."""
    ip = _ip(request.remote)
    if ip is not None and ip.is_loopback:
        return
    workers = H["_remote"].load_workers(H["_data_dir"]())
    if ip is not None and ip in await _allowed_ips(workers):
        return
    names = ", ".join(f'{w["label"]} ({w["host"]})' for w in workers) or "none set"
    raise web.HTTPForbidden(text=f"Model files are shared only with this PC and its GPU machines: {names}.")


def _folder(key):
    key = getattr(folder_paths, "map_legacy", lambda k: k)(key or "")
    if key in NOT_MODELS or key not in folder_paths.folder_names_and_paths:
        raise web.HTTPNotFound(text=f"No model folder called {key!r}")
    return key


def _private(path):
    """True inside the Incognito space (private renders or private uploads)."""
    p = os.path.realpath(path)
    for d in (H["_priv_root"](), os.path.join(folder_paths.get_input_directory(), H["PRIV_INPUT"])):
        d = os.path.realpath(d)
        if p == d or p.startswith(d + os.sep):
            return True
    return False


def _bad_name(name):
    parts = re.split(r"[\\/]", name)
    return (not name or "\0" in name or ".." in parts or name[0] in "\\/" or re.match(r"^[A-Za-z]:", name)
            or os.path.isabs(name))


def _find(folder, name):
    for n in dict.fromkeys((name, name.replace("\\", "/"), name.replace("/", "\\"))):
        p = folder_paths.get_full_path(folder, n)
        if p and os.path.isfile(p) and not _private(p):
            return p
    return None


def _sha(path):
    """A sha256 already written next to the file (<file>.sha256), never computed here."""
    try:
        with open(path + ".sha256", encoding="utf-8") as f:
            m = re.match(r"\s*([0-9a-fA-F]{64})\b", f.read(200))
        return m.group(1).lower() if m else None
    except OSError:
        return None


def _index(folder):
    out = []
    for n in folder_paths.get_filename_list(folder):
        p = _find(folder, n)
        if p:
            st = os.stat(p)
            out.append({"name": n.replace("\\", "/"), "size": st.st_size, "mtime": st.st_mtime})
    return out


def setup(ns):
    H.update(ns)
    routes = ns["routes"]

    @routes.get("/manny/api/models/file/{folder}/{name:.*}")     # GET also answers HEAD
    async def model_file(request):
        """One model file, streamed; Range gives 206 so a download can resume."""
        await _check(request)
        name = request.match_info["name"]
        if _bad_name(name):
            raise web.HTTPBadRequest(text="Name must be relative to the model folder, without ..")
        folder = _folder(request.match_info["folder"])
        p = await asyncio.to_thread(_find, folder, name)
        if not p:
            raise web.HTTPNotFound(text=f"This PC has no {folder}/{name}")
        hd = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
        sha = await asyncio.to_thread(_sha, p)
        if sha:
            hd["X-Studio-SHA256"] = sha
        return web.FileResponse(p, headers=hd)

    @routes.get("/manny/api/models/index")
    async def model_index(request):
        """Every file in one model folder: {"files": [{"name", "size", "mtime"}]}."""
        await _check(request)
        if not request.query.get("folder"):
            raise web.HTTPBadRequest(text="folder=<key> is needed")
        folder = _folder(request.query["folder"])
        return web.json_response({"files": await asyncio.to_thread(_index, folder)})
