# The `ue-angelscript` pack

Tools, a rule and a skill for a project with an AngelScript layer (`Script/**/*.as`, the Hazelight
fork of Unreal Engine). The pack requires the `unreal` pack.

A tool of this pack reads `Script/` and project text files. It never reads a Blueprint. A tool that
compares `Script/` with the Blueprints reads a file of the Blueprint export, which the `blueprint`
pack writes (see "The Blueprint export" below). This pack does not require the `blueprint` pack;
those tools need only the export file.

## What the pack holds

| Path | What it is | Install copies it to |
|---|---|---|
| `rules/angelscript.md` | the rule for `.as` files: reserved words, `BlueprintAssignable`, null guards, array access, names, redirects, overridability, purity, unreflected members, DataTables, localised text, widget animations, counting callers | `.agents/rules/octopus/ue-angelscript/` |
| `skills/regenerate-script-graph/` | regenerate the dependency graph of `Script/` and the per-file dependency blocks, then verify | `.agents/skills/` |
| `tools/` | the scripts below. They stay in Octopus AI and run from there | nothing |
| `ruleset.json` | the pack contract (see `../PACKS.md`) | nothing |

## Tools

Every tool reads files only, unless the line says that it writes. None of them calls the editor.

- `_export.py` - library: reads a file of the Blueprint export; when the file is missing, prints one line with the command that writes it and exits 2.
- `check_asset_paths.py` - every literal `/Game/...` object path in `Script/` must name a package in the export `packages.json`; exit 1 on a miss.
- `check_member_specifiers.py` - compares each script-parent `UPROPERTY` with the member variables of the newest text export of its Blueprint, from the export `variables.json`, on three axes: container-ness, `CPF_Net`, `CPF_SaveGame`
- `check_string_table_keys.py` - every `FText::FromStringTable` site in `Script/` must name a key that the export `stringtables.json` holds for that table; exit 1 on a miss.
- `check_struct_specifiers.py` - compares each script struct field with its `UserDefinedStruct` from the export `structs.json` on four axes: name, DisplayName, container-ness, `bEnableSaveGame`
- `check_struct_types.py` - compares the TYPE of each script struct field with the pin type in the export `structs.json`
- `collision_census.py` - finds names that a script parent and a child Blueprint both declare (variables, function graphs, custom events, dispatchers; macros apart), from `Script/` and the export `blueprints.json`, and names the removal route of each
- `comment_census.py` - report-only census of comments in `Script/`: density per file, narration markers, detached doc comments, narration preludes, rule violations, undocumented declarations.
- `comment_deprovenance.py` - strips provenance clauses (verb plus date, tick numbers, agent ids) from comment lines; `--report` shows each change, `--apply` writes each file once.
- `component_dupes.py` - finds actors that carry two components of one project base class, which `GetComponentByClass` cannot tell apart, and the by-class resolve sites that hit them, from `Script/` and the export `components.json`
- `expose_texts.py` - `report` counts the text literals left inside function bodies (exit 1 while any is hoistable); `hoist` moves them onto `EditDefaultsOnly` FText members (dry run without `--apply`).
- `gen_stubs_doc.py` - writes the stubs page from `stub_census.py`; `--check` exits 1 when the page on disk is stale.
- `native_callindex.py` - builds the AngelScript + C++ half of the call index (who calls each native member) and merges it into a call index JSON file
- `native_callsites.py` - classifies each occurrence of a member name in `Script/`, `Source/` and `Plugins/` as a declaration, a call or unclassified
- `port_gaps.py` - lists what a script parent still owes against its per-class docs pages, in three buckets: needs review, not work, elsewhere
- `script_graph.py` - draws the dependency graph of `Script/**/*.as` by layer as one HTML file, finds cycles and callerless functions, and writes a regenerable dependency block at the top of each `.as` file (`--write-headers --apply`, claim-aware).
- `stub_census.py` - counts every empty or near-empty `UFUNCTION` body in `Script/`; `--enrich` adds callers (Blueprint callers from the export `references.json`), documentation hits and a verdict per stub.
- `text_to_table.py` - moves one `NSLOCTEXT` text onto a string table key and carries its translations through the manifest, archives, locres and `.po` files (dry run without `--apply`).

## The Blueprint export

| Tool | Export file | The command that writes it |
|---|---|---|
| `check_asset_paths.py` | `packages.json` | `blueprint_export.py packages` |
| `check_string_table_keys.py` | `stringtables.json` | `blueprint_export.py stringtables` |
| `check_member_specifiers.py` | `variables.json` | `blueprint_export.py variables` |
| `check_struct_specifiers.py` | `structs.json` | `blueprint_export.py structs` |
| `check_struct_types.py` | `structs.json` | `blueprint_export.py structs` |
| `collision_census.py` | `blueprints.json` | `blueprint_export.py blueprints` |
| `component_dupes.py` | `components.json` | `blueprint_export.py components` |
| `stub_census.py --enrich` | `references.json` | `blueprint_export.py references` |

Run the command as `python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" <kind> --project
<project>`, or `all` for every file. The files are in `<exportDir>`: the key `exportDir` of
`<project>/.octopus/blueprint.json`, default `.octopus/export/blueprint`. The schema of each file is
in `../blueprint/README.md`. When a file is missing, the tool prints one line that names the
command and exits 2. A file is the package state on disk when it was written: write it again after
a save in the editor.

## How to run a tool

    python "<Octopus AI>/packs/ue-angelscript/tools/<tool>.py" --project <project> [options]

- The tool finds the project through `packs/unreal/tools/_project.py`: `--project <path>`, then the
  `OCTOPUS_PROJECT` environment variable, then the nearest directory above the current directory
  that holds `.octopus/project.json` or a `*.uproject` file. `--project` is removed from the
  arguments before the tool reads its own options, so every other option is the same as before.
- The tools import only `_project` from `packs/unreal/tools/`, and nothing from the `blueprint`
  pack. Install the `unreal` pack first.
- A relative path that the tool reads by default resolves against the project root. The current
  directory does not change the result.
- `python <tool>.py --help` prints the options of a tool that has them.
- A self-test that needs the data of a project reads its fixtures from the settings below. When
  none is set, it prints that it skips and exits 0.

## Settings: `<project>/.octopus/ue-angelscript.json`

Each tool reads the object under its own module name:

```json
{
  "script_graph": {"layers": [["Data", 0], ["Gameplay", 1], ["UI", 2]], "outputHtml": "Docs/script-graph.html"},
  "expose_texts": {"category": "Text"}
}
```

A key that is absent takes the default in the table. A tool with no keys in the table has no
settings. `exportDir` is in `<project>/.octopus/blueprint.json`, and the `blueprint` pack's README
lists its other keys.

| Key | Meaning | Default |
|---|---|---|
| `check_member_specifiers.pairs` | hand-listed pairs `[[script file, Blueprint asset name]]`; pairs found from the `parents` of `variables.json` are added | `[]` |
| `check_member_specifiers.exemptNoBlueprintChild` | script files with no Blueprint child, reported as exempt | `[]` |
| `check_member_specifiers.scriptOnly` | `[{file, member, reason}]`: members with no Blueprint counterpart (file is the `.as` basename) | `[]` |
| `check_member_specifiers.standingReview` | `[[script file, member, text]]`: notes printed on every run | `[]` |
| `check_member_specifiers.expectedFlagDivergence` | `[{file, member, axes: {CONTAINER, REPLICATION, SAVEGAME: reason}}]`: decided divergences, skipped per axis | `[]` |
| `check_member_specifiers.renameMapFile` | TAB-separated rename map `<asset> <blueprintName> <scriptName> [<evidence>]`, relative to the project root | `""` (no map file) |
| `check_member_specifiers.guessPrefixes` | name prefixes that the `--guess` shapes strip | `[]` |
| `check_member_specifiers.structCheckCommand` | the command the report names for the struct comparison | `python "<pack>/tools/check_struct_specifiers.py"` |
| `check_member_specifiers.structNoteLines` | lines printed after that command | `[]` |
| `check_struct_specifiers.aliases` | asset name -> script struct name, for pairs that do not follow `<Asset>` -> `F<Asset>` | `{}` |
| `check_struct_specifiers.noCounterpart` | asset name -> reason: struct assets with no script counterpart | `{}` |
| `check_struct_specifiers.scriptOnly` | script struct -> reason: script structs with no Blueprint original | `{}` |
| `check_struct_specifiers.expectedDivergence` | `[{struct, field, reason}]`: decided divergences | `[]` |
| `check_struct_specifiers.discriminatorNoteLines` | lines printed under the asset count of the report | two neutral lines |
| `check_struct_specifiers.selfTest` | `{mutations: [{file, find, replace, regex, what}], expect: [[struct, field, axis]]}`; `file` is relative to `Script/`. Absent: `--self-test` skips and exits 0 | absent |
| `check_struct_types.expectedDivergence` | `[{struct, field, reason}]`: decided type divergences; `field` is the asset's field name | `[]` |
| `collision_census.remainNote` | text printed after the count when collisions remain | empty |
| `collision_census.routeCustomEvent` | the command printed as the removal route of a custom event | `remove_bp_event.py` |
| `collision_census.routeMacroGraph` | the command printed as the removal route of a macro graph (the tool adds `--empty`) | `remove_bp_macro.py` |
| `collision_census.selfTest` | the control assets of `--self-test`: `title`, `minScriptParented`, `ancestry`, `functionGraphs`, `graphCounts`, `customEvents`, `namedFunctionGraphs`, `notReportedAssets`, `notReportedNames`, `seams`, `staleSignatureGraphs`, `plants`, `seamPlants`, `variableControl` (shapes in the tool's self-test comment) | absent: `--self-test` skips |
| `comment_census.markers` | the narration marker table, a list of `[name, regex]`; flags inline, e.g. `(?i)` | a built-in table (date, agent-id, tick-number, export-forensics, node-counts, ported-by, shouting, process-meta, todo-marker) |
| `comment_census.violations` | the rule-violation table of `--violations`, a list of `[name, regex]` | a built-in table (authored-data, design-assumption, speaks-for-children, history, defence, reaches-past, introspection, absence-as-prose, provenance, blueprint-word) |
| `comment_census.keptPattern` | regex of the anchor lines the rule exempts | `Migrated from … Blueprint variable`, `DOC:`, `Legacy variables/functions of`, `---`, `TODO(` |
| `comment_deprovenance.agentPattern` | regex of an agent id | `(?:claude\|agent)-[a-z0-9-]{2,}` |
| `component_dupes.selfTest` | the two positive controls of `--self-test`: `duplicate` `{class, base, names}` and `ancestry` `{class, chain}` | absent: `--self-test` skips |
| `expose_texts.category` | the `Category` of the members `hoist` writes | `Text` |
| `gen_stubs_doc.output` | the page, relative to the project root | `Docs/STUBS.md` |
| `gen_stubs_doc.generatorCommand` | the command the page and the stale message name to regenerate it | `python gen_stubs_doc.py` |
| `gen_stubs_doc.censusCommand` | the census the page names as its source | `stub_census.py` |
| `gen_stubs_doc.overrideCensusCommand` | the command the page names for the override census | `python collision_census.py --all` |
| `port_gaps.pairs` | `[[script parent, docs class, matching subsystem or null]]` | `[]` |
| `port_gaps.docsDir` | per-class docs directory; pages are `<docsDir>/<Class>/{functions,events}*.md` | `Docs/Classes` |
| `port_gaps.interfaceNames` | interface names whose members cannot be ported | `[]` |
| `port_gaps.marshallerPattern` | regex of marshaller function names | `^(Serialize\w*\|Deserialize\w*\|\w*FromStruct\w*\|\w*ToStruct\w*)$` |
| `port_gaps.marshallerReason` | text printed beside a marshaller | `marshaller - left as a stub, do not port` |
| `script_graph.layers` | the layer table: a list of `[directory, rank]`; a directory under `Script/` is a layer, and an edge to a higher rank is upward | every directory that holds a `.as` file, at rank 0 |
| `script_graph.rootLayer` | the layer name of the files directly in `Script/` | `Globals` |
| `script_graph.outputHtml` | the default `--out` path, relative to the project; `--json` writes the JSON beside it | `Docs/script-graph.html` |
| `script_graph.projectName` | the project name in the HTML title | the name of the project directory |
| `script_graph.docLink` | the link from a graph node to its prose page; `{page}` is the layer with `/` as `-`, `{anchor}` is the file stem in lower case | `Layers/{page}.md#{anchor}` |
| `script_graph.docRootPage` | the `{page}` of a file in the root layer | `Root` |
| `script_graph.claimsFile` | the claim board that `--write-headers --apply` reads, relative to the project | `.claude/coordination/CLAIMS.md` |
| `script_graph.messagesFile` | the message board that the `--apply` output names | `.claude/coordination/MESSAGES.md` |
| `script_graph.headerNote` | the second line of every generated header block | `// Generated. Regenerate with: script_graph.py --write-headers --apply` |
| `script_graph.selfTest.plantLayer` | the layer directory the `--self-test` plants go into | none: `--self-test` skips and exits 0 |
| `script_graph.selfTest.plantBase` | `{"class", "file"}`: a script class the planted class derives from, and its file | none: `--self-test` skips |
| `script_graph.selfTest.plantService` | `{"class", "file"}`: a script class with a static `Get()` in another layer, that the plant holds and calls | none: `--self-test` skips |
| `script_graph.selfTest.minFiles` | the least number of `.as` files the planted copy must hold | `1` |
| `script_graph.selfTest.facts` | facts about the real tree that `--self-test` checks: objects with `label`, `check` (`edge`, `eventHeldAndBroadcast`, `eventBroadcastFrom`, `callsInto`, `reachable`, `specHas`, `notTypes`, `reachesDirs`, `sameComponent`, `mutualPair`) and that check's parameters | none (only the two checks that hold for every tree) |
| `stub_census.manual` | hand-verified verdicts, a list of `[file basename, function, verdict, reason]`; a hand verdict wins over the keyword pass | `[]` |
| `stub_census.ruled` | verdicts that match before the others, a list of `[verdict, regex over the lower-case comment]` | `[]` |
| `stub_census.ruledReason` | the reason a `ruled` verdict carries in the JSON output | `ruled by project instruction` |
| `stub_census.faithfulPattern` | regex: a comment that records the Blueprint body as empty (verdict FAITHFUL EMPTY) | a built-in phrase list |
| `stub_census.declaredPattern` | regex: a comment that names the debt (verdict DECLARED DEBT) | a built-in phrase list |
| `stub_census.falseDonePattern` | regex: a comment that claims a Blueprint implements the body (the `stale` flag) | a built-in phrase list |
| `stub_census.docRoot` | the documentation directory `--enrich` searches for the function names | `Docs` |
| `stub_census.docClassPrefixes` | path prefixes of the pages that record a Blueprint body; without such a page a FAITHFUL EMPTY verdict becomes UNVERIFIABLE | `[]` |
| `stub_census.unverifiablePrefix` | the text put in front of the reason of an UNVERIFIABLE verdict | `reads as faithful, but no documentation page records the body: ` |
| `text_to_table.localizationTarget` | the localisation target under `Content/Localization/` | `Game` |
| `text_to_table.nativeCulture` | the culture whose entry is the source text | `en` |

The decoder control of `check_struct_types.py` is `blueprint_export.structControl` of
`.octopus/blueprint.json`; `structs.json` records its result.
