"""Studio's MCP server speaks MCP (ComfyUI-Studio/studio_mcp.py, mcp_http.py), with no Studio needed:
  - initialize answers with the protocol version asked for, the tools capability, the server's name and its instructions
  - tools/list: every tool has a unique name, a description an assistant can act on and a valid JSON Schema for its arguments
  - ping, notifications (no answer), unknown methods (-32601), malformed requests (-32600), a tool's failure as a tool
    result with isError (so the assistant reads why), an unknown tool
  - every request to Studio says it's an AI's (X-Studio-Client: ai); Studio's refusals reach the assistant as its sentence
  - projects and characters are found by id, title or part of it; shot codes as the server numbers them
  - over stdio with Studio down: the tools still list, and a call says Studio isn't reachable
  - over HTTP: a request from a web page of another site is refused (DNS rebinding)"""
import importlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import types
import urllib.error

import pytest

pytestmark = pytest.mark.mcp

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACK = os.path.join(REPO, "ComfyUI-Studio")
MCP = os.path.join(PACK, "studio_mcp.py")
_spec = importlib.util.spec_from_file_location("studio_mcp_under_test", MCP)
mcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp)
if "studiopack" not in sys.modules:
    _pkg = types.ModuleType("studiopack")
    _pkg.__path__ = [PACK]
    sys.modules["studiopack"] = _pkg
sp = importlib.import_module("studiopack.screenplay")
JSON_TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}


def rpc(method, params=None, mid=1):
    return mcp.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def valid_schema(s, where):
    """A JSON Schema an MCP client can use: types it knows, arrays with their items, required names that exist."""
    assert isinstance(s, dict), where
    if "enum" in s:
        assert isinstance(s["enum"], list) and s["enum"], where
    if "type" in s:
        types_ = s["type"] if isinstance(s["type"], list) else [s["type"]]
        assert set(types_) <= JSON_TYPES, (where, s["type"])
        if "array" in types_ and "items" in s:
            valid_schema(s["items"], where + "[]")
    for name, sub in (s.get("properties") or {}).items():
        valid_schema(sub, f"{where}.{name}")
    assert set(s.get("required") or []) <= set(s.get("properties") or {}), where


def test_initialize_answers_as_mcp_asks():
    for version in ("2025-06-18", "2025-03-26", "2024-11-05"):
        r = rpc("initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})["result"]
        assert r["protocolVersion"] == version
    assert "tools" in r["capabilities"] and r["serverInfo"]["name"] == "studio" and r["serverInfo"]["version"]
    assert "studio_overview" in r["instructions"], "the instructions point the assistant at where to start"
    assert rpc("initialize")["result"]["protocolVersion"] == mcp.PROTOCOL


def test_every_tool_has_a_name_a_description_and_a_valid_schema():
    tools = rpc("tools/list")["result"]["tools"]
    names = [t["name"] for t in tools]
    assert len(tools) >= 60 and len(names) == len(set(names))
    for t in tools:
        assert set(t) == {"name", "description", "inputSchema"}, t["name"]
        assert t["name"].startswith("studio_") and t["name"].replace("_", "").isalnum()
        assert len(t["description"]) >= 40, (t["name"], "too short for an assistant to know when to use it")
        assert t["inputSchema"]["type"] == "object"
        valid_schema(t["inputSchema"], t["name"])
    assert set(mcp.V3) <= set(names), "every registered tool is listed"


def test_ping_notifications_and_errors():
    assert rpc("ping") == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None, "a notification gets no answer"
    assert rpc("resources/list")["error"]["code"] == -32601
    assert mcp.handle(["not", "a", "request"])["error"]["code"] == -32600
    r = rpc("tools/call", {"name": "studio_no_such_tool", "arguments": {}})["result"]
    assert r["isError"] and "Unknown tool studio_no_such_tool" in r["content"][0]["text"]
    assert rpc("ping", mid="a-string-id")["id"] == "a-string-id"


def test_a_tools_failure_is_a_result_the_assistant_reads(monkeypatch):
    def down(*a, **k):
        raise RuntimeError("Studio isn't reachable at http://x (refused). Is ComfyUI running?")
    monkeypatch.setattr(mcp, "http", down)
    r = rpc("tools/call", {"name": "studio_cast", "arguments": {"project": "Film"}})["result"]
    assert r["isError"] and r["content"] == [{"type": "text", "text": "Studio isn't reachable at http://x (refused). Is ComfyUI running?"}]


def test_requests_say_they_are_an_ais_and_studios_refusals_come_back_in_words(monkeypatch):
    seen = []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=None):
        seen.append(req)
        if req.full_url.endswith("/bad"):
            raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, io.BytesIO(b'{"error": "The storyboard is locked in the page"}'))
        return Reply(b'{"ok": true}')
    monkeypatch.setattr(mcp.urllib.request, "urlopen", fake_open)
    assert mcp.http("POST", "/studio/api/x", {"a": 1}) == {"ok": True}
    assert seen[0].get_header("X-studio-client") == "ai" and json.loads(seen[0].data) == {"a": 1}
    with pytest.raises(RuntimeError, match=r"^Studio answered 409: The storyboard is locked in the page$"):
        mcp.http("GET", "/bad")
    assert mcp._said(b"plain text") == "plain text"


def test_projects_and_characters_are_found_by_id_title_or_part_of_it(monkeypatch):
    projects = [{"id": "a1", "title": "The Last Keeper"}, {"id": "b2", "title": "Harbour Lights"}]
    chars = [{"id": "c1", "name": "Mara Voss"}, {"id": "c2", "name": "Teo"}]
    monkeypatch.setattr(mcp, "http", lambda m, path, *a, **k: projects if "projects" in path else chars)
    assert mcp.project_ref("b2")["id"] == "b2" and mcp.project_ref("the last keeper")["id"] == "a1" and mcp.project_ref("lights")["id"] == "b2"
    with pytest.raises(RuntimeError, match=r"No such project\. Projects: The Last Keeper \(a1\), Harbour Lights \(b2\)"):
        mcp.project_ref("Nope")
    assert mcp.character_ref("mara")["id"] == "c1" and mcp.character_ref("c2")["name"] == "Teo"
    with pytest.raises(RuntimeError, match="Characters: Mara Voss, Teo"):
        mcp.character_ref("Ray")


def test_shot_codes_are_the_servers():
    text = "INT. A - DAY\n\nOne.\n\nCLOSE ON THE CUP\n\nSteam rises.\n\nEXT. B - NIGHT\n\nTwo.\n\n" + "".join(f"EXT. C{k} - DAY\n\nMore.\n\n" for k in range(3))
    bd = sp.breakdown(sp.parse(text))
    pr = {"scenes": bd["scenes"], "shots": bd["shots"]}
    assert mcp.codes_of(pr) == sp.shot_codes(bd["scenes"], bd["shots"])
    assert list(mcp.codes_of(pr).values())[:3] == ["1A", "1B", "2A"]


def test_over_stdio_with_studio_down_the_tools_still_list_and_a_call_says_why(tmp_path):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             "this line is not JSON", "",
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
             json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "studio_overview", "arguments": {}}})]
    p = subprocess.run([sys.executable, MCP], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, STUDIO_URL="http://127.0.0.1:9"))
    out = [json.loads(x) for x in p.stdout.splitlines()]
    assert [m["id"] for m in out] == [1, None, 2, 3], "an answer a request, a parse error for the line that isn't JSON, none for a notification"
    assert out[1]["error"]["code"] == -32700
    assert len(out[2]["result"]["tools"]) >= 60
    assert out[3]["result"]["isError"] and "isn't reachable at http://127.0.0.1:9" in out[3]["result"]["content"][0]["text"]
    assert p.returncode == 0 and not p.stderr.strip()


def test_over_http_a_web_page_of_another_site_is_refused():
    if "studiopack.mcp_http" not in sys.modules:
        pytest.importorskip("aiohttp")
    h = importlib.import_module("studiopack.mcp_http")
    req = lambda origin, host="192.168.1.20:8188": types.SimpleNamespace(headers={"Origin": origin} if origin else {}, host=host)   # noqa: E731
    assert h._foreign_origin(req("http://evil.example")) is True
    assert h._foreign_origin(req("http://192.168.1.20:8188")) is False, "Studio's own page"
    assert h._foreign_origin(req(None)) is False, "MCP clients send no Origin"
