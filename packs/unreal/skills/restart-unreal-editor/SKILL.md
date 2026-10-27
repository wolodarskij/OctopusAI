---
name: restart-unreal-editor
description: Launch the Unreal editor for {{NAME}} with the MCP server started and the Claude Code client config regenerated, so unreal-mcp tools become reachable again. Use when the editor is down and work that needs it cannot proceed - "the editor is down", "start Unreal", "relaunch the editor", "unreal-mcp is unreachable", "Unable to connect. Is the computer able to access the url?" - and when a scheduled or cron task reports the editor down and stops. Also use to check whether the editor is up before editor-dependent work starts. Not for building the project or compiling plugins - that is a separate toolchain.
---

# Restarting the Unreal editor

When `unreal-mcp` answers `Unable to connect. Is the computer able to access the url?`, the editor
process is gone, or its MCP server did not start. All editor-dependent work stops until the editor is
back. A cron-driven batch that only reports "editor is down" makes no progress.

This skill launches the editor. It is one command, and you can run it without a check first.
It needs the `editor` or `pie` permit: without one, the launch denies `start_editor.ps1` and every
unreal-mcp tool.

## Do this

```bash
powershell -ExecutionPolicy Bypass -File "{{OCTOPUS}}/packs/unreal/tools/start_editor.ps1" -ProjectDir "{{PROJECT}}" -Wait
```

Run it in the **background**. A cold editor start takes 1 to 3 minutes, and the script polls until the
MCP endpoint answers.

The script reads `engineDir`, `uproject`, `mcpUrl` and `editorLog` from
`{{PROJECT}}/.octopus/unreal.json`. Without `engineDir` it takes `-EngineDir`, then `UE_ENGINE_DIR`.

The script does not launch a second editor. It probes the MCP port first, then looks for a running
`UnrealEditor` process. If it finds either, it exits and does nothing.

### Exit codes

| Code | Meaning | What to do |
|---|---|---|
| `0` `ALREADY-UP` | MCP answers | nothing - continue the work |
| `0` `READY` | launched and reachable | continue |
| `2` `EDITOR-RUNNING-NO-MCP` | the editor is up, the server is not | see [below](#the-editor-is-up-but-mcp-is-not) |
| `3` `FAILED` | the editor exited during startup | read the editor log under `Saved/Logs/` |
| `4` `TIMEOUT` | no answer in 300 s | the editor can still be in load - probe again before a relaunch |
| `5` `BUILD-FAILED` | `.octopus/build-pending` asked for a build first, and the build failed | read the build output; the editor is not launched |

## The autosave restore dialog stops an unattended launch

**This is the most frequent reason why a launched editor never becomes reachable.**

If the previous session left autosaves, `FPackageAutoSaver::OfferToRestorePackages` shows a **modal**
*"restore files from autosave?"* dialog during startup. Nobody clicks it, so the game thread blocks on it.
The engine source says so in `Editor/UnrealEd/Private/PackageAutoSaver.cpp`:

> *Don't offer to restore packages during automation testing or when unattended; the dlg is modal and blocks*

**`start_editor.ps1` passes `-AutoDeclinePackageRecovery`.** Startup then behaves as if the user chose
Decline, and the engine deletes the restore file so that the dialog does not come back. **Use it, not
`-unattended`.** `-unattended` stops this dialog only because it auto-answers every modal.

### How to recognise it

The symptoms look like a network fault or a plugin fault:

| Symptom | Cause |
|---|---|
| **`list_toolsets` says "Unable to connect"** | the listener exists, but nothing calls `accept()` |
| **`Get-NetTCPConnection -LocalPort 8000` shows `Listen`** | the engine created the socket during init |
| **A raw TCP connect is *actively refused*** | the accept backlog is full, so the kernel sends RST |
| **The log shows `Starting MCP server on port 8000` and the toolsets register** | all of that occurs during init, before the block |
| **The log shows `Engine is initialized. Leaving FEngineLoop::Init()`** | init is complete; the modal comes after it |
| **`Get-Process` shows `Responding: True`** | this does not mean that the game thread is free |

**The frame counter decides it.** Unreal stamps each log line `[timestamp][frame]`, and the frame
number increases by one on each tick:

```bash
powershell -NoProfile -Command "if (Select-String -Path 'Saved\Logs\<Project>.log' -Pattern '^\[[^\]]*\]\[\s*[1-9][0-9]*\]' -List) {'FRAMES TICKED'} else {'NEVER TICKED - startup modal'}"
```

**If no line shows a non-zero frame, the editor did not complete one frame.** It stopped before any
work started. A wait does not help, because no operation is in progress.

### Recovery

**A process that did not tick a frame has nothing unsaved.** The editor UI did not run, so no asset
was opened or changed. It is safe to kill it, and the script kills it only in that case:

```bash
powershell -ExecutionPolicy Bypass -File "{{OCTOPUS}}/packs/unreal/tools/start_editor.ps1" -ProjectDir "{{PROJECT}}" -KillExisting -Wait
```

`-KillExisting` reads the frame counter first. **It does not kill an editor that ticked frames**,
because that editor can hold real work. If the user is at the machine, ask before you run it - the
user can be in a dialog.

## What the launch does

```
UnrealEditor.exe "<ProjectRoot>\<Project>.uproject"
    -ModelContextProtocolStartServer
    -ModelContextProtocolPort=<port of mcpUrl>
    -AutoDeclinePackageRecovery
    -ExecCmds="ModelContextProtocol.GenerateClientConfig ClaudeCode"
```

**`-ModelContextProtocolStartServer`** is an engine switch. `ShouldAutoStartServer()` reads it and
starts the HTTP server at the correct point of engine init. Use it, not
`-ExecCmds ModelContextProtocol.StartServer`, which can run before the module is ready.

**`-ExecCmds="ModelContextProtocol.GenerateClientConfig ClaudeCode"`** writes `.mcp.json` next to the
`.uproject`. The argument after the space stays inside one comma-delimited entry, so the command
receives it as its parameter.

You need both. The switch makes the server exist. The console command lets Claude Code find it.

## The facts it depends on

Check these again if one of them changes.

| | |
|---|---|
| Engine | **`<EngineRoot>`**, from `.octopus/unreal.json`. When the `.uproject` `EngineAssociation` matches no registered engine, the path must be explicit |
| Editor | `<EngineRoot>\Engine\Binaries\Win64\UnrealEditor.exe` |
| Plugin | `ModelContextProtocol`, engine-side, `Engine/Plugins/Experimental/` |
| Port, URL | `mcpUrl` in `.octopus/unreal.json`, default `http://127.0.0.1:8000/mcp` (port 8000, the default of `UModelContextProtocolSettings::ServerPortNumber`). The script passes its port as `-ModelContextProtocolPort` |
| Autostart | **`bAutoStartServer = false`** by default - so the switch is necessary |
| Config | `<ProjectRoot>\.mcp.json` |

If another process uses the port, set another `mcpUrl`.

## Probe without a launch

To find out whether the editor is up, with no side effects, call `list_toolsets`. A tool list means
yes. `Unable to connect` means no.

From the shell, for a script, or when the MCP client itself does not respond:

```bash
powershell -Command "try { Invoke-WebRequest -Uri http://127.0.0.1:8000/mcp -TimeoutSec 3 -UseBasicParsing | Out-Null; 'UP' } catch { if (\$_.Exception.Response) { 'UP' } else { 'DOWN' } }"
```

**Any HTTP response means up.** The endpoint is a streamable-HTTP MCP endpoint, so a bare `GET`
returns an error status, not `200`. Only a *connection* failure means down.

## After it comes back

**The MCP session can fail to reconnect by itself.** When the script reports `READY`, call
`list_toolsets` one time. Make sure that the tools are reachable from this session before you use
them. If the call still fails, the user must reconnect the client. Tell the user; do not loop.

Then do again the editor check that the blocked work did, and continue that work.

## The editor is up but MCP is not

Exit code `2`. The process is alive, and the server did not start. Usually somebody launched the
editor by hand without the switch.

Do **not** relaunch. Two editors on one project conflict: the second one cannot get the file lock, or
it corrupts state. Ask the user to type this in the editor console:

```
ModelContextProtocol.StartServer
ModelContextProtocol.GenerateClientConfig ClaudeCode
```

`-Force` overrides the guard. Do not use it while an editor runs.

## When the editor died during work

A relaunch costs little. To repeat the operation that killed the editor costs more. If the editor
goes down two times in sequence at the same point of the work, mark that target as blocked in the
work list. Do not restart a third time for the same target.

A tool error such as `read_graph_dsl` raising `UserRaisedError` is not a crash. It does not need a
restart.

## Not this skill's job

- **Build the project or compile plugins.** That is
  `{{OCTOPUS}}/packs/unreal/tools/build_editor.ps1`; see `unreal-build-system.md`.
- **Stop the editor.** Nothing here closes it. The user closes it, and unsaved work belongs to the user.
- **PIE.** A live PIE session breaks asset resolution, but it does not need a relaunch. Ask the user to
  click Stop.
