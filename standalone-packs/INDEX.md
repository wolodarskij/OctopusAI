# Unreal Engine rules, skills and tools for Claude Code and Codex sessions

Copy the rules you want into a project's rules folder (`.claude/rules/`, `.agents/rules/`), and the skill directories into its skills folder (`.claude/skills/`, `.agents/skills/`). In the copied files, replace `<kit>` with the absolute path of this directory. The tools run from this directory. Keep the settings of each tool directory in `<project>/.unreal-kit/<directory>.json`, for example `<project>/.unreal-kit/unreal.json`.

## `unreal/`: the engine, the editor, the build and the MCP connection

| File | What it is |
|---|---|
| [unreal/README.md](unreal/README.md) | The tools, how a tool finds its project, and the keys of `<project>/.unreal-kit/unreal.json`. |

### Rules

| File | The rule |
|---|---|
| [unreal/rules/unreal-engine.md](unreal/rules/unreal-engine.md) | Unreal Engine 5 reference: naming, `UPROPERTY`/`UFUNCTION`, Gameplay Tags, Blueprints, Data Assets and the text exports. |
| [unreal/rules/unreal-macros.md](unreal/rules/unreal-macros.md) | The reflection macros and their specifiers in C++ headers. |
| [unreal/rules/unreal-patterns.md](unreal/rules/unreal-patterns.md) | Common Unreal Engine patterns for the C++ modules under `Source/`. |
| [unreal/rules/unreal-conventions.md](unreal/rules/unreal-conventions.md) | The Unreal Engine C++ coding style for `*.h`, `*.cpp` and `*.inl` files. |
| [unreal/rules/unreal-build-system.md](unreal/rules/unreal-build-system.md) | `*.Build.cs`, `*.Target.cs`, `*.uproject` and `*.uplugin` files, and how to build the editor and a plugin. |
| [unreal/rules/unreal-live-coding.md](unreal/rules/unreal-live-coding.md) | What Live Coding cannot patch, and when a full editor build is necessary. |
| [unreal/rules/shared-editor.md](unreal/rules/shared-editor.md) | How several agents share one `UnrealEditor.exe` over MCP: claims, messages, PIE, restarts and files that agents append to. |
| [unreal/rules/external-project-update.md](unreal/rules/external-project-update.md) | What to read again after a VCS pull, a merge, another IDE or another outside change. |

### Skills

| File | The skill |
|---|---|
| [unreal/skills/restart-unreal-editor/SKILL.md](unreal/skills/restart-unreal-editor/SKILL.md) | Start the editor with the MCP server and write the client config, so that the unreal-mcp tools are reachable again. |

### Tools

| File | What it does |
|---|---|
| `unreal/tools/_project.py` | Library: finds the project and reads its settings. The tools of `blueprint/` and `ue-angelscript/` import it too. |
| `unreal/tools/mcp.py` | Library: a minimal unreal-mcp client over HTTP, through curl. |
| `unreal/tools/start_editor.ps1` | Starts the editor with the MCP server and writes `.mcp.json`. Runs `build_editor.ps1` first when `.unreal-kit/build-pending` exists. |
| `unreal/tools/build_editor.ps1` | Builds the editor target with `Build.bat`. When an editor has the project open, it builds nothing and exits 3. |
| `unreal/tools/check-content-ignores.ps1` | Lists which `Content/` subdirectories the VCS ignore file excludes; `pack` and `unpack` move the excluded trees through a zip. |

## `ue-angelscript/`: a project with an AngelScript layer

| File | What it is |
|---|---|
| [ue-angelscript/README.md](ue-angelscript/README.md) | The tools, the Blueprint export files they read, and the keys of `<project>/.unreal-kit/ue-angelscript.json`. |

### Rules

| File | The rule |
|---|---|
| [ue-angelscript/rules/angelscript.md](ue-angelscript/rules/angelscript.md) | `.as` files: reserved words, `BlueprintAssignable`, null guards, array access, names, redirects, overridability, purity, DataTables, localised text and caller counts. |

### Skills

| File | The skill |
|---|---|
| [ue-angelscript/skills/regenerate-script-graph/SKILL.md](ue-angelscript/skills/regenerate-script-graph/SKILL.md) | Regenerate the dependency graph of `Script/` and the per-file dependency blocks, then verify. |

### Tools

| File | What it does |
|---|---|
| `ue-angelscript/tools/_export.py` | Library: reads a file of the Blueprint export; when the file is missing, prints the command that writes it and exits 2. |
| `ue-angelscript/tools/check_asset_paths.py` | Every literal `/Game/...` object path in `Script/` must name a package in `packages.json`. |
| `ue-angelscript/tools/check_member_specifiers.py` | Compares each script-parent `UPROPERTY` with the Blueprint member variables in `variables.json`: container-ness, `CPF_Net`, `CPF_SaveGame`. |
| `ue-angelscript/tools/check_string_table_keys.py` | Every `FText::FromStringTable` site in `Script/` must name a key that `stringtables.json` holds for that table. |
| `ue-angelscript/tools/check_struct_specifiers.py` | Compares each script struct field with its `UserDefinedStruct` in `structs.json`: name, DisplayName, container-ness, `bEnableSaveGame`. |
| `ue-angelscript/tools/check_struct_types.py` | Compares the type of each script struct field with the pin type in `structs.json`. |
| `ue-angelscript/tools/collision_census.py` | Finds the names that a script parent and a child Blueprint both declare, and names the removal route of each. |
| `ue-angelscript/tools/comment_census.py` | Report-only census of the comments in `Script/`: density, narration markers, detached doc comments, undocumented declarations. |
| `ue-angelscript/tools/comment_deprovenance.py` | Strips provenance clauses (a verb and a date, tick numbers, agent ids) from comment lines. |
| `ue-angelscript/tools/component_dupes.py` | Finds the actors that carry two components of one project base class, and the by-class resolve sites that hit them. |
| `ue-angelscript/tools/expose_texts.py` | Counts the text literals inside function bodies, and moves them onto `EditDefaultsOnly` FText members. |
| `ue-angelscript/tools/gen_stubs_doc.py` | Writes the stubs page from `stub_census.py`; `--check` exits 1 when the page is stale. |
| `ue-angelscript/tools/native_callindex.py` | Builds the AngelScript and C++ half of the call index and merges it into a call index JSON file. |
| `ue-angelscript/tools/native_callsites.py` | Classifies each occurrence of a member name in `Script/`, `Source/` and `Plugins/` as a declaration, a call or unclassified. |
| `ue-angelscript/tools/port_gaps.py` | Lists what a script parent still owes against its per-class docs pages. |
| `ue-angelscript/tools/script_graph.py` | Draws the dependency graph of `Script/**/*.as` as one HTML file, finds cycles and callerless functions, and writes the per-file dependency blocks. |
| `ue-angelscript/tools/stub_census.py` | Counts every empty or near-empty `UFUNCTION` body in `Script/`; `--enrich` adds callers, documentation hits and a verdict. |
| `ue-angelscript/tools/text_to_table.py` | Moves one `NSLOCTEXT` text onto a string table key and carries its translations through the localization files. |

## `blueprint/`: reading Blueprints, the text exports and the graphs

| File | What it is |
|---|---|
| [blueprint/README.md](blueprint/README.md) | The plugin install, the tools, the Blueprint export and its schema, and the keys of `<project>/.unreal-kit/blueprint.json`. |

### Rules

| File | The rule |
|---|---|
| [blueprint/rules/blueprint-to-native-migration.md](blueprint/rules/blueprint-to-native-migration.md) | How to move Blueprint logic into a native parent, C++ or AngelScript, without a broken call site. |
| [blueprint/rules/reading-blueprints-cheapest-route.md](blueprint/rules/reading-blueprints-cheapest-route.md) | Read a Blueprint by the cheapest route that answers: the docs on disk, the offline tools, an existing text export, then the live editor. |

### Skills

| File | The skill |
|---|---|
| [blueprint/skills/read-blueprint-without-editor/SKILL.md](blueprint/skills/read-blueprint-without-editor/SKILL.md) | Read the full graph logic of a Blueprint from the `.uasset` on disk, with no live editor. |
| [blueprint/skills/move-bp-variable-to-parent/SKILL.md](blueprint/skills/move-bp-variable-to-parent/SKILL.md) | Move variables from a Blueprint into its AngelScript or C++ parent class and keep the graph wiring. |
| [blueprint/skills/move-bp-function-to-parent/SKILL.md](blueprint/skills/move-bp-function-to-parent/SKILL.md) | Move Blueprint functions into an AngelScript or C++ parent class and reparent the Blueprint onto it. |
| [blueprint/skills/migrate-bp-enum-to-parent/SKILL.md](blueprint/skills/migrate-bp-enum-to-parent/SKILL.md) | Replace a `UserDefinedEnum` asset with a native enum and keep the integer values compatible. |
| [blueprint/skills/unreal-project-docs/SKILL.md](blueprint/skills/unreal-project-docs/SKILL.md) | Generate, reprocess and update an exhaustive design-document tree under `Docs/` for the project. |
| [blueprint/skills/unreal-project-docs/references/extraction.md](blueprint/skills/unreal-project-docs/references/extraction.md) | Extraction recipes of the documentation skill: closure discovery, reprocess, batch scripts, variables, functions and events. |
| [blueprint/skills/unreal-project-docs/references/taxonomy.md](blueprint/skills/unreal-project-docs/references/taxonomy.md) | Tags on five axes (domain, role, status, layer, kind) and the descriptions of the documentation pages. |
| [blueprint/skills/unreal-project-docs/references/templates.md](blueprint/skills/unreal-project-docs/references/templates.md) | The layout, the tiering, the worklist and the per-file page templates of the documentation tree. |
| [blueprint/skills/docs-progress/SKILL.md](blueprint/skills/docs-progress/SKILL.md) | Show the documentation progress: what is done, the percent, what is next and what is stuck. |
| [blueprint/skills/document-blueprint-exhaustively/SKILL.md](blueprint/skills/document-blueprint-exhaustively/SKILL.md) | Write a per-symbol documentation set for one Blueprint, with pseudocode and evidence-backed callers. |

### Tools

| File | What it does |
|---|---|
| `blueprint/tools/blueprint_export.py` | Writes the Blueprint data that the language tools read, one JSON file per kind, into the export directory. |
| `blueprint/tools/_uasset.py` | Library: reads a package header, the name table, the imports, the exports and the tagged properties. |
| `blueprint/tools/exec_islands.py` | Finds the detached exec islands of every Blueprint graph under `Content/`. |
| `blueprint/tools/raised_entrypoints.py` | Finds the Blueprint entry points (functions, macros, custom events) that nothing raises. |
| `blueprint/tools/search_bp_refs.py` | Finds the Blueprint packages whose bytes name a class, a function or a property. |
| `blueprint/tools/guid_sweep.py` | Counts the packages that hold a GUID. |
| `blueprint/tools/widget_designer_values.py` | Prints the authored designer values of a WidgetBlueprint: the widget tree and the CDO defaults. |
| `blueprint/tools/enum_dump.py` | Prints the values of a `UserDefinedEnum` asset, with internal and display names. |
| `blueprint/tools/stringtable_keys.py` | Prints the keys and the source strings of a `UStringTable` asset. |
| `blueprint/tools/datatable_text_dump.py` | Prints the FText payloads of a DataTable asset, tagged NSLOCTEXT, key or text. |
| `blueprint/tools/loc_key_audit.py` | Prints the localisation identities (namespace, key, source) of the inline texts in an asset. |
| `blueprint/tools/loc_orphans.py` | Keeps the translations whose source text left the manifest, and writes them back when the source returns. |
| `blueprint/tools/parent_census.py` | Reads the real parent of each Blueprint and follows the chain to a script, C++ or engine root. |
| `blueprint/tools/migration_state.py` | Puts every Blueprint in one of four migration states from its parent chain and its reachable graph nodes. |
| `blueprint/tools/port_state.py` | Puts every Blueprint that holds graph logic in one of four port states. |
| `blueprint/tools/find_unbindable_names.py` | Lists every Blueprint symbol name that AngelScript cannot declare. |
| `blueprint/tools/_t3d_dir.py` | Library: the T3D export directory of the project. |
| `blueprint/tools/t3d_dump.py` | Prints the nodes, the pins and the links of a T3D export. |
| `blueprint/tools/t3d_graphs.py` | Parses a whole-Blueprint T3D export per graph. |
| `blueprint/tools/t3d_node_chain.py` | Follows an exec or data chain from a start node, resolved per graph. |
| `blueprint/tools/graph_guids.py` | Prints the GraphGuid of every graph in a T3D export. |
| `blueprint/tools/graph_islands.py` | Measures the entry points of one parsed graph JSON: what each reaches, the overlaps, the unreached nodes. |
| `blueprint/tools/animgraph_dump.py` | Prints the pose connectivity of an anim graph T3D export. |
| `blueprint/tools/rigvm_dump.py` | Prints the RigVM nodes, pin defaults and links of a Control Rig T3D export. |
| `blueprint/tools/link_diff.py` | Finds the data links that a Blueprint lost while every node survived, from the exports of each asset. |
| `blueprint/tools/build_callindex.py` | Builds the documentation call index (`_callindex.json`) from T3D dumps. |
| `blueprint/tools/doc_entries.py` | Keeps the worklist of a per-symbol documentation pass on one Blueprint. |
| `blueprint/tools/export_bp_t3d.py` | Editor tool: copies the bytes of an asset to the backup directory, exports the live asset to T3D, and parses it per graph. |
| `blueprint/tools/bp_typed_nodes.py` | Editor tool: finds the Blueprint nodes that name a type; `--delete` removes them over MCP. |
| `blueprint/tools/remove_bp_event.py` | Editor tool: removes a Blueprint custom event by name. |
| `blueprint/tools/remove_bp_macro.py` | Editor tool: removes a Blueprint macro graph and verifies from a fresh export. |
| `blueprint/tools/collision_survey.py` | Editor tool: compiles each Blueprint that references a script class and counts the name collisions in the log. |

### Plugin

| File | What it is |
|---|---|
| `blueprint/Plugins/AssetTextExport/` | The editor plugin that exports any asset to T3D text, from UE 4.25 to 5.8. Install it as `blueprint/README.md` says. |
