#!/usr/bin/env python3
"""Calls one of Studio's MCP tools against the running sandbox and prints its result, through the real connector
(ComfyUI-Studio/studio_mcp.py, spoken to over stdio exactly as Claude does).

    python3 scripts/studio-call.py studio_overview '{}'
    python3 scripts/studio-call.py --list
    python3 scripts/studio-call.py studio_generate '{"app": "image", "prompt": "a red bicycle"}' --port 8188"""
import argparse
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP = os.path.join(REPO, "ComfyUI-Studio", "studio_mcp.py")


def rpc(proc, mid, method, params=None):
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}) + "\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            raise SystemExit("The connector stopped: " + proc.stderr.read()[-800:])
        m = json.loads(line)
        if m.get("id") == mid:
            return m


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tool", nargs="?")
    ap.add_argument("arguments", nargs="?", default="{}")
    ap.add_argument("--list", action="store_true", help="list the tools")
    ap.add_argument("--port", type=int, default=8188)
    a = ap.parse_args()
    env = dict(os.environ, STUDIO_URL=f"http://127.0.0.1:{a.port}",
               STUDIO_INPUT_DIR=os.path.join(REPO, "sandbox", "run", "input"))
    proc = subprocess.Popen([sys.executable, MCP], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    try:
        rpc(proc, 1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "studio-call"}})
        if a.list or not a.tool:
            for t in rpc(proc, 2, "tools/list")["result"]["tools"]:
                print(f"{t['name']:28} {t.get('description', '').splitlines()[0][:90]}")
            return 0
        try:
            args = json.loads(a.arguments)
        except json.JSONDecodeError as e:
            raise SystemExit(f"The arguments must be JSON: {e}")
        m = rpc(proc, 2, "tools/call", {"name": a.tool, "arguments": args})
        if "error" in m:
            print(json.dumps(m["error"], indent=1))
            return 1
        res = m["result"]
        for c in res.get("content", []):
            if c.get("type") == "text":
                print(c["text"])
            else:
                print(f"[{c.get('type')} {c.get('mimeType', '')}, {len(c.get('data', ''))} bytes base64]")
        return 1 if res.get("isError") else 0
    finally:
        proc.stdin.close()
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
