# The `unreal` pack

The `unreal` pack adds Unreal Engine support to a project: the engine, the editor, the build,
`Config/` and the MCP connection to the editor. The Blueprint tools are in the `blueprint` pack,
which requires this pack.

| Directory | Content |
|---|---|
| `rules/` | engine rules (`unreal-engine.md`, `unreal-macros.md`, `unreal-patterns.md`, `unreal-conventions.md`, `unreal-build-system.md`, `unreal-live-coding.md`), `external-project-update.md`, `shared-editor.md`. Install copies them to `.agents/rules/octopus/unreal/`. |
| `skills/` | `restart-unreal-editor`. Install copies it to `.agents/skills/`. |
| `brief/repeat.test.md` | the brief block of a Tester ticket (`repeat: test`) |
| `tools/` | the scripts below. They stay in Octopus AI and run from there. |
| `ruleset.json` | the `editor` and `pie` permits, the tools a launch denies without them, the Tester permits, the brief section and the payload |

## Tools

| Tool | What it does |
|---|---|
| `_project.py` | Library: finds the project and reads its settings. The tools of the `blueprint` and `ue-angelscript` packs import it too. |
| `mcp.py` | Library: a minimal unreal-mcp client over HTTP, through curl. |
| `start_editor.ps1` | Starts the editor with the MCP server and writes `.mcp.json`. Does nothing when the MCP port already answers. Runs `build_editor.ps1` first when `.octopus/build-pending` exists. |
| `build_editor.ps1` | Builds the editor target with `Build.bat`. When an editor has the project open it builds nothing, keeps `.octopus/build-pending`, and exits 3; a build that succeeds removes the marker. Install writes the marker when a pack replaced editor sources (the `blueprint` pack's AssetTextExport plugin). |
| `check-content-ignores.ps1` | Lists which `Content/` subdirectories the VCS ignore file excludes; `pack` and `unpack` move the excluded trees through a zip. |

## How a tool finds its project

A Python tool finds its project in this order: `--project <path>`, the `OCTOPUS_PROJECT` environment
variable, then the nearest directory above the current directory that holds `.octopus/project.json`
or a `*.uproject` file.

The PowerShell tools take the project as a parameter:

```
powershell -ExecutionPolicy Bypass -File "<Octopus AI>/packs/unreal/tools/start_editor.ps1" -ProjectDir <path> [-EngineDir <path>] [-Wait]
powershell -ExecutionPolicy Bypass -File "<Octopus AI>/packs/unreal/tools/build_editor.ps1" -ProjectDir <path> [-EngineDir <path>] [-IfPending]
powershell -ExecutionPolicy Bypass -File "<Octopus AI>/packs/unreal/tools/check-content-ignores.ps1" -Root <path> [analyze|pack|unpack]
```

## `<project>/.octopus/unreal.json`

Every key is optional. A relative path is relative to the project root. The settings of the
Blueprint tools are in `<project>/.octopus/blueprint.json` (see `../blueprint/README.md`).

| Key | Meaning | Default |
|---|---|---|
| `uproject` | the `.uproject` file name | the first `*.uproject` in the project root |
| `engineDir` | the engine root, the directory that holds `Engine/Binaries/Win64/UnrealEditor.exe` (`start_editor.ps1`) | `UE_ENGINE_DIR` |
| `editorLog` | the editor log that `start_editor.ps1` and the `blueprint` pack's `remove_bp_event.py` read | `Saved/Logs/<uproject name>.log` |
| `mcpUrl` | the MCP endpoint of the editor (`mcp.py`, `start_editor.ps1`) | `http://127.0.0.1:8000/mcp` |
| `ignoreFiles` | the ignore file names `check-content-ignores.ps1` tries, in order | `.p4ignore`, `.gitignore` |

Example:

```json
{
  "uproject": "MyGame.uproject",
  "engineDir": "C:\\Program Files\\Epic Games\\UE_5.8",
  "mcpUrl": "http://127.0.0.1:8000/mcp"
}
```
