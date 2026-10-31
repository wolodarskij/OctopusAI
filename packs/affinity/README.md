# The `affinity` pack

The connection to the Affinity editor (Affinity 3 by Canva) through its built-in MCP server. The pack contract is
`packs/PACKS.md`.

## What the pack holds

| Path | What it is |
|---|---|
| `ruleset.json` | the title and the payload |
| `skills/affinity-mcp/SKILL.md` | the skill: set up, the tools, the work order. Install copies it to `<project>/.agents/skills/affinity-mcp/` |
| `tools/affinity_mcp.py` | `status`, `setup [--codex]`, `tools`. It stays in Octopus AI |
| `tools/vendor/affinity-mcp-proxy.mjs` | the stdio-to-SSE proxy that a session starts as the `affinity` MCP server |
| `tools/vendor/LICENSE` | the MIT license of the proxy |

## Setup

1. In Affinity: **Settings > Model Context Protocol**, turn on **Enable Affinity MCP** and **Scripting**.
2. `python <Octopus AI>/packs/affinity/tools/affinity_mcp.py status` prints `UP` and exits 0 when the server
   answers at `http://localhost:6767/sse`.
3. `python <Octopus AI>/packs/affinity/tools/affinity_mcp.py setup`, from the project root, runs
   `claude mcp add affinity --scope local -e AFFINITY_MCP_BASE_URL=... -- node <proxy>`. `--codex` also runs
   `codex mcp add`. The local scope keeps the machine path of the proxy out of the project's files.

**Dependencies:** Affinity 3 with the MCP setting; Node.js 20 or later (`fetch` and the stream reader of the
proxy); Python 3.

`AFFINITY_MCP_BASE_URL` (default `http://localhost:6767`) and `AFFINITY_MCP_PROTOCOL_VERSION` (default
`2025-11-25`) change the server address and the protocol version that the proxy sends.

## The proxy

`tools/vendor/affinity-mcp-proxy.mjs` comes unchanged from `plugins/affinity-mcp/scripts/` of
https://github.com/deba33/codex-plugins-by-deba33, commit `c08f793377191d1337dd541eb7582f908aec3919`, under the
MIT license in `tools/vendor/LICENSE`. It reads newline-delimited JSON-RPC on stdin, opens the SSE stream of
Affinity, posts each request to the endpoint that the stream names, and writes each answer on stdout. When
Affinity does not answer, `tools/list` returns a fixed list of the Affinity tools, so the session starts.
