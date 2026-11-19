"""The MCP server of a real, running Studio: `npm run test:mcp -- --url http://<pc>:8188` (STUDIO_TEST_URL). Skipped
without a URL. Nothing renders unless --render (STUDIO_TEST_RENDER=1): then one location picture is made on its GPU.
What the checks make (a project "MCP check …" with a part and a place of its own) is deleted at the end, with the
character made for it; a picture rendered with --render stays in the gallery.
  - Studio answers at the URL, and so does its MCP endpoint (initialize, tools, ping; a foreign web page refused)
  - the connector (studio_mcp.py over stdio, as Claude Desktop starts it) lists every tool and reads Studio through them
  - a project made from a screenplay through MCP: broken down, cast, located, a location's look filled in, seen by the page
    as an AI's change, and deleted with what was made for it"""
import json
import os
import time
import types
import urllib.error
import urllib.request

import pytest

from test_mcp import Connector

URL = (os.environ.get("STUDIO_TEST_URL") or "").rstrip("/")
RENDER = os.environ.get("STUDIO_TEST_RENDER") == "1"
pytestmark = [pytest.mark.mcp, pytest.mark.live, pytest.mark.skipif(not URL, reason="a real Studio's URL: npm run test:mcp -- --url http://<pc>:8188")]
STAMP = time.strftime("%H%M%S")
TITLE = f"MCP check {STAMP}"
SCRIPT = f"""Title: {TITLE}

INT. MCP TEST ROOM {STAMP} - DAY

ZELDA QUILLFEATHER (40s, tweed coat, round glasses) checks a pocket watch.

ZELDA
Right on time.
"""


def get(path):
    with urllib.request.urlopen(URL + path, timeout=60) as r:
        return json.loads(r.read())


def mcp_post(msg, origin=None):
    req = urllib.request.Request(URL + "/studio/mcp", data=json.dumps(msg).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, None


@pytest.fixture(scope="module")
def mcp():
    c = Connector(types.SimpleNamespace(url=URL))
    yield c
    c.close()


def test_studio_answers_at_the_url():
    info = get("/studio/api/info")
    assert info["version"], info


def test_its_mcp_endpoint_answers_over_http():
    code, r = mcp_post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
    assert code == 200 and r["result"]["serverInfo"]["name"] == "studio", (code, r)
    code, r = mcp_post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert code == 200 and len(r["result"]["tools"]) >= 60
    assert mcp_post({"jsonrpc": "2.0", "id": 3, "method": "ping"})[0] == 200
    assert mcp_post({"jsonrpc": "2.0", "id": 4, "method": "ping"}, origin="http://evil.example")[0] == 403


def test_the_connector_lists_every_tool_and_reads_studio(mcp):
    tools = {t["name"] for t in mcp.rpc("tools/list")["result"]["tools"]}
    assert {"studio_project_new", "studio_storyboard_frames", "studio_takes_draft", "studio_ai_tool_run", "studio_view"} <= tools
    overview, _ = mcp.call("studio_overview")
    assert "How Studio works" in overview
    apps, _ = mcp.call("studio_list_apps")
    assert {"Image", "Video"} <= {a["app"] for a in apps}, "the Image and Video apps are installed (workflows/*.app.json)"
    status, _ = mcp.call("studio_status")
    assert status["machines"] and status["machines"][0]["online"]
    for tool in ("studio_projects", "studio_list_characters", "studio_list_boards", "studio_recent", "studio_ai_tools", "studio_models"):
        mcp.call(tool)


def test_a_project_through_mcp_then_deleted(mcp):
    made, _ = mcp.call("studio_project_new", screenplay=SCRIPT)
    pid = made["project"]["id"]
    try:
        assert made["project"]["title"] == TITLE and made["project"]["shots"] == 1
        assert made["followed"]["characters_added"] == ["Zelda Quillfeather"], made["followed"]
        bd, _ = mcp.call("studio_breakdown", project=pid)
        assert bd[0]["heading"] == f"INT. MCP TEST ROOM {STAMP} - DAY" and bd[0]["shots"][0]["lines"] == ["ZELDA: Right on time."]
        cast, _ = mcp.call("studio_cast", project=pid)
        assert cast[0]["played_by"]["sheet"] == {"age": "40s", "look": "tweed coat, round glasses"}
        mcp.call("studio_location_update", project=pid, location=f"MCP TEST ROOM {STAMP}", look="a plain grey room, one window")
        locs, _ = mcp.call("studio_locations", project=pid)
        assert locs[0]["look"] == "a plain grey room, one window"
        assert get("/studio/api/stamps")["projects"][pid][1] == "ai", "the page sees it as an AI's change"
        if RENDER:
            _, images = mcp.call("studio_location_picture", project=pid, location=f"MCP TEST ROOM {STAMP}", timeout=1800)
            assert images, "the picture came back for the assistant to see"
    finally:
        mcp.call("studio_project_delete", project=pid)
    assert pid not in [p["id"] for p in get("/studio/api/projects")]
    assert "Zelda Quillfeather" not in [c["name"] for c in get("/studio/api/characters")], "the character made for it went with it"
