"""Studio's MCP server over HTTP (MCP's Streamable HTTP transport), served by Studio itself at /studio/mcp: an AI assistant
connects with a URL instead of starting studio_mcp.py (e.g. `claude mcp add --transport http studio http://<pc>:8188/studio/mcp`).
The tools are studio_mcp's, the same as over stdio; each call runs in a thread and talks to this Studio over HTTP.

One JSON-RPC message (or a batch) per POST, answered as JSON; a notification is accepted with 202. There are no sessions and
no server-sent events (nothing here needs to reach the client first). A request a web page makes (it carries an Origin that
isn't this server) is refused: MCP's guard against DNS rebinding."""
import asyncio
import json
from urllib.parse import urlparse

from aiohttp import web

from . import studio_mcp


def _foreign_origin(request):
    origin = request.headers.get("Origin")
    if not origin:
        return False                        # MCP clients send none; browsers always do
    host = (urlparse(origin).netloc or "").lower()
    return host != (request.host or "").lower()


def setup(ns):
    routes = ns["routes"]

    @routes.post("/studio/mcp")
    async def mcp(request):
        if _foreign_origin(request):
            return web.json_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Cross-origin requests are refused"}}, status=403)
        studio_mcp.BASE = f"http://127.0.0.1:{ns['_port']()}"
        try:
            msg = await request.json()
        except Exception:
            return web.json_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status=400)
        if isinstance(msg, list):
            out = [r for r in await asyncio.gather(*(asyncio.to_thread(studio_mcp.handle, m) for m in msg)) if r is not None]
            return web.json_response(out) if out else web.Response(status=202)
        res = await asyncio.to_thread(studio_mcp.handle, msg)
        return web.json_response(res) if res is not None else web.Response(status=202)

    @routes.get("/studio/mcp")
    async def mcp_get(request):
        # no server-initiated messages: the spec's answer for a GET stream is 405
        return web.Response(status=405, headers={"Allow": "POST"}, text=json.dumps({"error": "POST JSON-RPC messages here"}))
