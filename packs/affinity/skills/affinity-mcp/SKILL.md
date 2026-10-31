---
name: affinity-mcp
description: Drive the Affinity editor (Affinity 3 by Canva - vector, pixel and layout) through its built-in MCP server - create or edit a document, place images, change layers, run Affinity JavaScript, render a spread or a selection to look at it, and keep reusable scripts in Affinity's script library. Use when asked to "make it in Affinity", "edit the .af file", "use Affinity", "draw this in Affinity", "export from Affinity", or when the affinity MCP tools fail to connect. Also use to set up the connection in a project.
---

# Affinity over MCP

Affinity 3 has its own MCP server. It serves SSE at `http://localhost:6767/sse` while Affinity runs and the
setting is on. A session reaches it through the stdio proxy of this pack, so the session starts also while Affinity
is closed. The tools then show as `mcp__affinity__<tool>`.

## Set up

1. The owner opens Affinity, then **Settings > Model Context Protocol**, and turns on **Enable Affinity MCP** and
   **Scripting**. Only the owner can do this step: ask with the question dialog when the status check fails.
2. Check the server:

   ```bash
   python "{{OCTOPUS}}/packs/affinity/tools/affinity_mcp.py" status
   ```

   `UP` (exit 0): the server answers. `DOWN` (exit 1): Affinity is closed, or the setting is off.
3. Add the server to the session's AI provider, once per project, from the project root:

   ```bash
   python "{{OCTOPUS}}/packs/affinity/tools/affinity_mcp.py" setup            # Claude Code, local scope
   python "{{OCTOPUS}}/packs/affinity/tools/affinity_mcp.py" setup --codex    # also Codex
   ```

   The tools appear after the next start of the session. `tools` lists them through the proxy.

`AFFINITY_MCP_BASE_URL` moves the server from `http://localhost:6767`.

## The tools

| Tool | What it does |
|---|---|
| `read_sdk_documentation_topic` | reads one topic of the Affinity JavaScript SDK. Read `preamble` first, every session |
| `list_sdk_documentation` | lists the SDK topics |
| `search_sdk_hints` | searches the SDK hints for a task or a problem |
| `execute_script` | runs JavaScript in Affinity and returns its result |
| `render_spread` | renders a spread of a document (`document_session_uuid`, `spread_index`) to a JPEG |
| `render_selection` | renders the selected nodes of a document to a JPEG |
| `list_library_scripts`, `read_library_script`, `save_script_to_library` | the script library of Affinity |
| `add_sdk_hint` | writes a hint into the preamble of every later session |
| `report_sdk_issue` | sends an issue report to Affinity |

## Do this

1. Read `preamble` with `read_sdk_documentation_topic`. Then read each topic that the task needs, and
   `search_sdk_hints` for a behaviour you do not know. Never guess an SDK name: find it in a topic or a hint.
2. Find the document: a script that lists the open documents gives each `document_session_uuid`.
3. Make a change in small scripts, one step each. A script returns the values you need next, such as the ids of
   the nodes it made.
4. After each change that the owner can see, look at it: `render_spread` or `render_selection`, then read the
   image.
5. Work on a copy of a document that the owner made. Save to a new file unless the owner asked to overwrite.
6. Save a script that the task will use again with `save_script_to_library`, with a title and a description.

**Ask the owner first** before `report_sdk_issue` (it sends data to Affinity) and before `add_sdk_hint` (it
changes the preamble of every later session).

## When the tools fail

| You see | Do |
|---|---|
| no `mcp__affinity__*` tools in the session | run `setup`, then the session must start again |
| an error that names `Ensure Affinity is running and MCP is enabled` | run `status`; on `DOWN`, ask the owner to open Affinity and turn on the setting |
| a script error | read the topic of the object in the error, and `search_sdk_hints` with the error text |
