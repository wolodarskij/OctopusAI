"""affinity_mcp.py - set up and check the MCP connection to the Affinity editor.

    python affinity_mcp.py status                 exit 0 when Affinity's MCP server answers, else 1
    python affinity_mcp.py setup [--codex]        add the `affinity` MCP server to Claude Code (local scope of the
                                                  current directory); with --codex, also to Codex
    python affinity_mcp.py tools                  list the tools through the proxy, as a session sees them

Affinity 3 serves MCP over SSE at http://localhost:6767/sse once Settings > Model Context Protocol > Enable Affinity
MCP is on. A session reaches it through `vendor/affinity-mcp-proxy.mjs`, a stdio-to-SSE proxy that answers
`tools/list` with a fixed list while Affinity is closed, so the session starts either way. AFFINITY_MCP_BASE_URL
moves the server (default http://localhost:6767).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

NAME = "affinity"
BASE_URL = os.environ.get("AFFINITY_MCP_BASE_URL", "http://localhost:6767").rstrip("/")
PROXY = Path(__file__).resolve().parent / "vendor" / "affinity-mcp-proxy.mjs"


def node() -> str:
    found = shutil.which("node")
    if found is None:
        sys.exit("node is not on PATH: install Node.js 20 or later")
    return found


def status() -> int:
    """Reads the first SSE event of the server: Affinity sends `endpoint` at once."""
    try:
        request = urllib.request.Request(BASE_URL + "/sse", headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(request, timeout=3) as response:
            line = response.readline().decode("utf-8", "replace").strip()
    except OSError as error:
        print("DOWN: no MCP server at %s/sse (%s). Open Affinity, then Settings > Model Context Protocol > "
              "Enable Affinity MCP." % (BASE_URL, getattr(error, "reason", error)))
        return 1
    print("UP: %s/sse answers (%s)" % (BASE_URL, line or "connected"))
    return 0


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, shell=os.name == "nt")


def setup(codex: bool) -> int:
    proxy = str(PROXY).replace("\\", "/")
    env = "AFFINITY_MCP_BASE_URL=" + BASE_URL
    failed = 0
    if shutil.which("claude") is None:
        print("claude is not on PATH: Claude Code not set up")
        failed = 1
    elif run(["claude", "mcp", "get", NAME]).returncode == 0:
        print("Claude Code: `%s` is there already" % NAME)
    else:
        done = run(["claude", "mcp", "add", NAME, "--scope", "local", "-e", env, "--", node(), proxy])
        print("Claude Code: " + (done.stdout or done.stderr).strip())
        failed |= done.returncode != 0
    if codex:
        if shutil.which("codex") is None:
            print("codex is not on PATH: Codex not set up")
            failed = 1
        elif run(["codex", "mcp", "get", NAME]).returncode == 0:
            print("Codex: `%s` is there already" % NAME)
        else:
            done = run(["codex", "mcp", "add", NAME, "--env", env, "--", node(), proxy])
            print("Codex: " + (done.stdout or done.stderr).strip())
            failed |= done.returncode != 0
    print("A session sees the server after its next start.")
    return int(failed)


def tools() -> int:
    """Talks to the proxy as an MCP client: initialize, then tools/list."""
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "affinity_mcp.py", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    proxy = subprocess.Popen([node(), str(PROXY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, encoding="utf-8")
    try:
        for message in messages:
            proxy.stdin.write(json.dumps(message) + "\n")
            proxy.stdin.flush()
        for line in proxy.stdout:
            answer = json.loads(line)
            if answer.get("id") != 2:
                continue
            if "error" in answer:
                print("error: " + answer["error"].get("message", ""))
                return 1
            for tool in answer["result"]["tools"]:
                print("%-30s %s" % (tool["name"], tool.get("description", "").split("\n")[0]))
            return 0
        return 1
    finally:
        proxy.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    add = commands.add_parser("setup")
    add.add_argument("--codex", action="store_true", help="also add the server to Codex")
    commands.add_parser("tools")
    args = parser.parse_args()
    if args.command == "status":
        return status()
    if args.command == "setup":
        return setup(args.codex)
    return tools()


if __name__ == "__main__":
    sys.exit(main())
