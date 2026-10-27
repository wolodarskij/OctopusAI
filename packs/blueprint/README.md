# The `blueprint` pack

The `blueprint` pack reads Blueprints: the package bytes (`.uasset`) under `Content/`, the text
exports (T3D) and the graphs. It gives pseudocode and data that AngelScript work and C++ work can
both use. It requires the `unreal` pack.

A tool of a language pack never reads a Blueprint. When it compares its source with the
Blueprints, `blueprint_export.py` of this pack writes the Blueprint data to a JSON file first, and
the language tool reads that file (see "The Blueprint export" below).

| Path | What it is | Install copies it to |
|---|---|---|
| `rules/` | `blueprint-to-native-migration.md`, `reading-blueprints-cheapest-route.md` | `.agents/rules/octopus/blueprint/` |
| `skills/` | `move-bp-variable-to-parent`, `move-bp-function-to-parent`, `migrate-bp-enum-to-parent`, `unreal-project-docs`, `docs-progress`, `read-blueprint-without-editor`, `document-blueprint-exhaustively` | `.agents/skills/` |
| `tools/` | the scripts below. They stay in Octopus AI and run from there | nothing |
| `Plugins/AssetTextExport/` | the editor plugin that exports any asset to T3D text. The T3D tools read its output | the directory where the project already keeps `AssetTextExport.uplugin` under `Plugins/`, else `Plugins/Tools/AssetTextExport/` (see "The AssetTextExport plugin" below) |
| `ruleset.json` | the pack contract (see `../PACKS.md`) | nothing |

## The AssetTextExport plugin

The pack owns the project's copy of the plugin. Nobody copies it by hand.

- **Install** copies `Plugins/AssetTextExport/` over the copy the project already has, wherever it
  is under `Plugins/`, or to `Plugins/Tools/AssetTextExport/` when the project has none.
- **Update** writes every file the pack changed and removes every file the pack no longer ships.
  `octopus update <project>` and `octopus update --all` run it by hand; the `SessionStart` hook runs
  it for the plugin in the project each session starts in. `Binaries/` and `Intermediate/` of the
  copy stay.
- **The build.** A copy that changed leaves `.octopus/build-pending`. The `unreal` pack's
  `build_editor.ps1` builds the editor target and removes the marker; while an editor has the project
  open it builds nothing, and `start_editor.ps1` builds before its next launch. An editor launched by
  hand loads the old build until then.
- **Enable it** in the `.uproject` once: `{"Name": "AssetTextExport", "Enabled": true,
  "TargetAllowList": ["Editor"]}` under `Plugins`.

## How to run a tool

```
python "<Octopus AI>/packs/blueprint/tools/<tool>.py" --project <path> [arguments]
```

- The tools import `_project` (and `mcp` where they need the editor) from `packs/unreal/tools/`.
  Install the `unreal` pack first.
- A tool finds its project in this order: `--project <path>`, the `OCTOPUS_PROJECT` environment
  variable, then the nearest directory above the current directory that holds
  `.octopus/project.json` or a `*.uproject` file.
- A parser of one file (`t3d_dump`, `t3d_graphs`, `enum_dump` and the like) also runs with no
  project. A tool that scans the project stops with an error when it finds no project.

## Tools

### The Blueprint export

| Tool | What it does |
|---|---|
| `blueprint_export.py` | Writes the Blueprint data that the language tools read, one JSON file per kind: `packages`, `stringtables`, `variables`, `structs`, `blueprints`, `components`, `references`, or `all`. Writes only into the export directory. |

### Offline `.uasset` readers

| Tool | What it does |
|---|---|
| `_uasset.py` | Library: reads a package header, the name table, the imports, the exports and the tagged properties. |
| `exec_islands.py` | Finds the detached exec islands of every Blueprint graph under `Content/`: nodes that no execution can reach. `--self-test` plants an island first. |
| `raised_entrypoints.py` | Finds the Blueprint entry points (functions, macros, custom events) that nothing raises. `--controls` calibrates, `--crosscheck` compares with `search_bp_refs.py`. |
| `search_bp_refs.py` | Finds the Blueprint packages whose bytes name a class, a function or a property. `--exact` matches whole FNames only. |
| `guid_sweep.py` | Counts the packages that hold a GUID (for example a graph GUID from `graph_guids.py`). |
| `widget_designer_values.py` | Prints the authored designer values of a WidgetBlueprint: the widget tree and the CDO defaults. |
| `enum_dump.py` | Prints the values of a `UserDefinedEnum` asset, with internal and display names. |
| `stringtable_keys.py` | Prints the keys and the source strings of a `UStringTable` asset. |
| `datatable_text_dump.py` | Prints the FText payloads of a DataTable asset, tagged NSLOCTEXT, key or text. |
| `loc_key_audit.py` | Prints the localisation identities (namespace, key, source) of the inline texts in an asset. `--diff` compares two assets. |
| `loc_orphans.py` | Keeps the translations whose source text left the manifest, and writes them back when the source returns. |
| `parent_census.py` | Reads the real parent of each Blueprint from the export table and follows the chain to a script, C++ or engine root. |
| `migration_state.py` | Puts every Blueprint in one of four states (needs-parent, move-code, sweep-dead, done) from its parent chain and its reachable graph nodes. |
| `port_state.py` | Puts every Blueprint that holds graph logic in one of four states (needs-parent, clearing, dead-only, done) from its parent chain and its reachable graph nodes. |
| `find_unbindable_names.py` | Lists every Blueprint symbol name that AngelScript cannot declare (spaces, non-ASCII, leading digits, illegal characters, reserved words), from T3D exports and package name tables. |

### T3D text tools

| Tool | What it does |
|---|---|
| `_t3d_dir.py` | Library: the T3D export directory of the project. |
| `t3d_dump.py` | Prints the nodes, the pins and the links of a T3D export. |
| `t3d_graphs.py` | Parses a whole-Blueprint T3D export per graph: `--summary`, `--graph <name>`, or `--outdir` for one JSON file per graph. |
| `t3d_node_chain.py` | Follows an exec or data chain from a start node, resolved per graph by node name and pin id. |
| `graph_guids.py` | Prints the GraphGuid of every graph in a T3D export. |
| `graph_islands.py` | Measures the entry points of one parsed graph JSON: what each reaches, the overlaps, the nodes no entry reaches. |
| `animgraph_dump.py` | Prints the pose connectivity of an anim graph T3D export. |
| `rigvm_dump.py` | Prints the RigVM nodes, pin defaults and links of a Control Rig T3D export. |
| `link_diff.py` | Finds the data links that a Blueprint lost while every node survived, by diffing the exports of each asset. `--self-test` gates every run. |
| `build_callindex.py` | Builds the documentation call index (`_callindex.json`) from T3D dumps. |
| `doc_entries.py` | Keeps the worklist of a per-symbol documentation pass on one Blueprint. |

### Editor tools (need a running editor with the MCP server)

| Tool | What it does |
|---|---|
| `export_bp_t3d.py` | Copies an asset's bytes to the backup directory, exports the live asset to T3D, and parses it per graph. |
| `bp_typed_nodes.py` | Finds the Blueprint nodes that name a type, with their true graph; `--delete` removes them over MCP. |
| `remove_bp_event.py` | Removes a Blueprint custom event by name. |
| `remove_bp_macro.py` | Tries every route to remove a Blueprint macro graph and verifies from a fresh export; `--empty` strips the body. |
| `collision_survey.py` | Compiles each Blueprint that references a script class through the live editor and counts the name collisions the log reports. |

## The Blueprint export

```
python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" <kind> --project <path> [--out-dir <dir>]
python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" all --project <path>
```

The tool writes `<exportDir>/<kind>.json`. `exportDir` is a key of `.octopus/blueprint.json`; the
default is `.octopus/export/blueprint`. Keep the directory outside `Saved/`: the engine mounts
`Saved/` as a package root. Exit 0 when every file was written, 2 when a kind failed.

Every file is one JSON object with these keys, and then the keys of its kind:

| Key | Value |
|---|---|
| `kind` | the kind name |
| `schema` | `1` |
| `generatedBy` | `blueprint_export.py <kind>` |
| `generated` | the UTC time of the write, `YYYY-MM-DDTHH:MM:SSZ` |

A file is the package state on disk when the tool wrote it. Write it again after a save in the
editor.

### `packages.json`

| Key | Value |
|---|---|
| `packages` | sorted list of every package path under `Content/`, `.uasset` and `.umap`, as `/Game/<dir>/<name>` with no extension |

Read by `ue-angelscript/tools/check_asset_paths.py`.

### `stringtables.json`

| Key | Value |
|---|---|
| `tables` | table id (`/Game/<dir>/<name>.<name>`) -> `{key: source string}` |
| `unreadable` | list of `{path, why}`: `.uasset` files the string table reader could not read |

Read by `ue-angelscript/tools/check_string_table_keys.py`.

### `variables.json`

| Key | Value |
|---|---|
| `blueprints` | asset name -> `{exports: [...]}`. `exports` holds every T3D export `Text<asset>-<n>.txt` of the asset, NEWEST FIRST by modification time. Each entry: `file` (the file name), `decoded` (false when the file did not decode), `error` (only when the file could not be read), `variables` (member variable name -> `{container, category, flags}`: the `ContainerType`, the `PinCategory` and the `PropertyFlags` of its `NewVariables` line) |
| `parents` | asset name -> `{path, chain, landing, root}` from `parent_census.py`: the parent chain, where it lands (`script`, `cpp`, `engine`, `broken`), and the root class |

Read by `ue-angelscript/tools/check_member_specifiers.py`.

### `structs.json`

| Key | Value |
|---|---|
| `structs` | asset name -> one UserDefinedStruct asset: `path`; `fields` (list of `{name, raw, saveGame, container, friendly, category}`: the variable name, the serialised `VarName`, `bEnableSaveGame`, the `EPinContainerType::` value, the `FriendlyName`, the `Category`); `types` (list of `{name, container, category, terminal}`: the pin type of each field); `fieldsError` and `typesError` (the reason a list could not be decoded, else null) |
| `errors` | list of strings: `.uasset` files that could not be read |
| `control` | `{status, struct, problems}`: the decoder control of `blueprint_export.structControl`. `status` is `passed`, `failed` or `skipped` |

The tool selects the struct assets by the packed FName `UserDefinedStructEditorData`. Read by
`ue-angelscript/tools/check_struct_specifiers.py` and `ue-angelscript/tools/check_struct_types.py`.

### `blueprints.json`

| Key | Value |
|---|---|
| `assetsScanned` | the number of `.uasset` files read |
| `unparsable` | list of `{path, why}`: packages whose header did not parse |
| `blueprints` | asset name -> `{path, parent, parentIsScript, scriptParent, hops, bodyError}`. `scriptParent` is the registered name of the first script class up the `_C` chain, or null; `hops` is the number of Blueprint parents between. A Blueprint with a `scriptParent` also carries `variables` (list of `{name, category, subName, subClass, container}`), `functionGraphs`, `macroGraphs`, `ubergraphPages`, `delegateGraphs`, `customEvents` and `overrides` (lists of names). `bodyError` is the reason these could not be read, else null |

Read by `ue-angelscript/tools/collision_census.py`.

### `components.json`

| Key | Value |
|---|---|
| `packages` | package file (relative to the project) -> `{classes, scs, addComponent, handlers, instanced}`. `classes`: `[class, parent]` of each generated class. `scs`: class -> list of `[variable, component class, origin]` from its construction script. `addComponent`: `[node, template class]`. `handlers`: the classes that carry an `InheritableComponentHandler`. `instanced`: `[outer, object, class]` of each component on a placed actor in a `.umap` |
| `dropped` | list of `{path, why}`: packages that could not be read |

Read by `ue-angelscript/tools/component_dupes.py`.

### `references.json`

| Key | Value |
|---|---|
| `assets` | the assets read: the export with the highest number of each asset in the T3D directory |
| `memberNames` | name -> `{count, assets}`: the `MemberName` references of call nodes |
| `functionGraphs` | name -> the assets that declare a graph of that name |
| `customEvents` | name -> the assets that declare a custom event of that name |

Read by `ue-angelscript/tools/stub_census.py --enrich`.

## `<project>/.octopus/blueprint.json`

Every key is optional. A relative path is relative to the project root. `uproject`, `editorLog` and
`mcpUrl` are in `.octopus/unreal.json` of the `unreal` pack.

| Key | Meaning | Default |
|---|---|---|
| `exportDir` | the directory of the Blueprint export; the language tools read it from here too | `.octopus/export/blueprint` |
| `t3dDir` | the directory of the T3D text exports | `T3D` |
| `backupDir` | where `export_bp_t3d.py` copies `.uasset` bytes. Keep it outside the project. `OCTOPUS_BACKUP_DIR` overrides it. | `../<project dir name>Backups/MigrationBackups` |
| `assetTextToolset` | the registered name of the MCP toolset that has `export_asset_text` | `asset_text_export.toolsets.text.AssetTextExportTools` |
| `assetTextToolsetSource` | where that toolset comes from, named in the error when it is not registered | `Plugins/AssetTextExport/Content/Python` |
| `callIndexPath` | the default `--out` of `build_callindex.py`, and the call index `find_unbindable_names.py` reads | `Docs/_callindex.json` |
| `docEntriesDir` | the default worklist directory of `doc_entries.py`; `{target}` becomes the target | `Docs/Classes/{target}` |
| `locTarget` | the localization target; it names the `.archive` and `.manifest` files | `Game` |
| `locDir` | the live localization target directory. `OCTOPUS_LOC_DIR` overrides it. | `Content/Localization/<locTarget>` |
| `locStore` | the store of held translations of `loc_orphans.py` | `.octopus/orphaned-translations.json` |
| `locCultures` | the cultures `loc_orphans.py` walks, in order | every culture directory under `locDir` that holds an archive, sorted |
| `debugFunctions` | the project's own debug-print functions; `exec_islands.py` treats an island of these as debug-only | none (the engine print functions are built in) |
| `selfTestAssetDir` | where the self-tests of `exec_islands.py` and `raised_entrypoints.py` look for a Blueprint to plant on | `Content` |
| `raisedControls` | the calibration controls of `raised_entrypoints.py`: `{"<function>": [gap, fingerprint]}` | none; calibration is skipped |
| `linkDiff` | the known answers of `link_diff.py`: `calibration`, `survivors`, `knownSignature`, `knownAdjudicated` (the format is in the tool's docstring) | none; calibration is skipped |
| `blueprint_export.structControl` | `{asset, struct, expect: [[field, container, category, terminal]]}`: a struct asset whose pin types are known. `blueprint_export.py structs` decodes it and records the result in `structs.json` | absent: the control is `skipped` |
| `collision_survey.logFile` | the editor log, relative to the project root | `Saved/Logs/<uproject name>.log` |
| `find_unbindable_names.thirdPartyOwners` | classes of third-party assets; a name declared only there is left out unless `--third-party` | empty list |
| `find_unbindable_names.packagesFailedNote` | text printed after the failed-package count | empty |
| `find_unbindable_names.mdCommand` | the command line quoted at the top of the `--md` report | `python find_unbindable_names.py --md <file>` |
| `parent_census.cppModules` | the project's C++ module names; a native root whose package ends with one is `cpp` | the `Modules` of the `.uproject` file |

Example:

```json
{
  "exportDir": ".octopus/export/blueprint",
  "t3dDir": "T3D",
  "backupDir": "../MyGameBackups",
  "debugFunctions": ["MyDebugPrint"],
  "raisedControls": {"GetMyGameState": [2, 140]}
}
```
