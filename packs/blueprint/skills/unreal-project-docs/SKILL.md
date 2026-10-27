---
name: unreal-project-docs
description: Generate, reprocess and update an exhaustive design-document tree under Docs/ for an Unreal Engine project. Computes the full dependency closure from the root gameplay classes and gives every reachable actor, component and class its own directory of cross-linked pages — role, inheritance contribution, variables with writers and readers, functions analysed from their actual graph or source, data flow, lifecycle, call graph, dependencies, config surface and graded findings. Use when asked to "document the project", "make design docs", "document X", "go deeper on X", "reprocess X" or "reprocess everything" (delete and rebuild from scratch), "update the docs" (incremental), "what changed since the docs were written", or "how much is left". Reads the live editor over unreal-mcp. Not for writing gameplay code and not for the per-plugin README/ARCHITECTURE files plugins maintain themselves.
---

# Unreal project documentation

Exhaustive by design. The target is **every class reachable from the root gameplay classes**, each in
its own directory. Nothing is skipped for being small, boring, or third-party.

The first full run computes the closure for the project at hand (Step 1) and records it in
`Docs/_worklist.json`. The categories and their treatment are always the same:

| Category | Treatment |
|---|---|
| Project Blueprint classes | `full` — directory |
| Project C++ classes | `full` — directory |
| Project script classes (e.g. AngelScript, if the project uses a scripting layer) | `full` — directory |
| Third-party classes reachable from project code | `summary` — one page |
| Structs, enums, data tables, data assets | `data` — one page |
| Leaf content (anims, textures, meshes, sounds) | referenced rows only |

On a mid-sized project this lands in the thousands of files. It is a multi-run job. It is driven by
`Docs/_worklist.json`, and it is not finished until that file reports zero pending. (Numbers quoted
in the `references/` files illustrate scale; compute everything again for your project.)

## Three verbs, and they are not interchangeable

Getting these confused is what produced a tree of patched-over half-pages.

| Verb | Means | Deletes? |
|---|---|---|
| **document** | Process `pending` targets from the worklist | no |
| **update** | Re-read only sources whose mtime or size changed; regenerate the affected pages | no |
| **reprocess** | **Delete the target's directory entirely and rebuild from scratch** | **yes** |

**Reprocess never reads the old files.** Not to diff, not to salvage, not to check what changed. Old
output is deleted first, then the target is regenerated from the editor and the source as if it had
never been documented. The point is that a stale page cannot survive to mislead.

Before deleting anything, echo the exact target list back to the user and get confirmation.
`reprocess everything` means every target in the worklist — say how many directories that is before
touching one.

## The failure this skill exists to prevent

A shallow run produces tables of one-line descriptions, repeated boilerplate rows, and a few
classes with a directory, some of them half-finished with no record of what remained. Four rules
follow:

1. **Never describe a member from its name.** Read the logic — `read_graph_dsl` for Blueprints, the
   body for script and C++ classes.
2. **Never put a function in a table row.** One `###` block each.
3. **Every class gets a directory.** Size changes how many files are inside, never whether there is a
   directory.
4. **Never leave a target half-done.** Finish the target or leave it `pending`. A partially written
   directory with no worklist record is the worst possible state.

## Connect first, and don't bluff

Call `list_toolsets`. If it errors, or `unreal-mcp` is absent, and no `UnrealEditor.exe` runs, launch
the editor with the `restart-unreal-editor` skill (and run `ModelContextProtocol.StartServer` if
auto-start is off). Never
guess Blueprint contents from `.uasset` bytes.

Call conventions that differ from each other:

- `describe_toolset` takes **`toolset_name`**, not `toolset`.
- `call_tool` takes the **short** `tool_name` plus `toolset_name`.
- `execute_tool()` *inside* a batch script takes the **fully qualified** name and a **JSON string**.
- A batch helper named `t(name, **kw)` collides with `find_assets`' own `name` argument — call the
  first parameter `_tool`.
- Never `describe_toolset` on `BlueprintTools` — 72k characters.

Sequential only; the game thread runs these. Check every result.

**Check for PIE before doing anything else.** While Play-In-Editor is running, packages are
duplicated under a `UEDPIE_0_` prefix and object resolution goes through the PIE world, so
`get_dependencies`, `exists` and `get_asset_class` all fail with *"Asset does not exist"* for assets
that plainly exist — while `find_assets` keeps working, which makes the failure look like a path bug
rather than a state problem. Detect it:

```
LogsToolset.GetLogEntries(category="", pattern="UEDPIE|EndPlayMap", maxEntries=6)
```

A `UEDPIE_0_` line with no later `EndPlayMap` means PIE is live. **Stop and ask the user to press
Stop in the editor** — do not try to work around it, and do not report the resulting empty closure as
a real result.

## The one thing Unreal cannot tell you

The batch sandbox imports only `math, json, datetime, time, re, copy` — no `os`, so **file timestamps
cannot be read from inside Unreal**. Use the host shell. If the project is not under git (or uses
another VCS without cheap per-file history), mtime + size is the only change signal; if it is under
git, `git status`/`git log` can supplement but mtime + size remains the manifest key.

`AssetTools.write_file` only writes under `/Game/`, a plugin's `Content/`, or `Saved/`. `Docs/` is
none of those — use the normal Write tool.

## Step 1 — Discovery and the worklist

Every full run starts here. The recipe is in `references/extraction.md`.

Breadth-first over `AssetTools.get_dependencies` from the roots — GameMode, GameState,
PlayerController, Pawn/Character, HUD, GameInstance, AnimBP — traversing **only through class assets**
and recording everything else as a leaf. Find the roots from `Config/DefaultEngine.ini` /
`DefaultGame.ini` (GameInstanceClass, default GameMode) and the per-map GameMode overrides. Add the
C++ classes found in `Source/` and `Plugins/*/Source/`, and script classes (e.g. `Script/**/*.as` on
AngelScript projects) if the project has a scripting layer.

Then classify every discovered target and write **`Docs/_worklist.json`** (schema in
`references/templates.md`):

| `treatment` | Applies to |
|---|---|
| `full` | Project Blueprint, C++ and script classes — a directory |
| `summary` | Classes under the project's third-party content roots (marketplace folders, e.g. `/Game/ThirdParty`, `/Game/Marketplace`) or a marketplace plugin — one page. Identify these roots during discovery and record them in the worklist. |
| `data` | UserDefinedStruct, UserDefinedEnum, DataTable, PrimaryDataAsset — one page |

`status` is `pending` | `in-progress` | `done` | `blocked`. **Report the pending count every time you
stop.** If the user asks "how much is left", this file is the answer.

Rediscover on every full run and diff against the existing worklist: new targets appear as `pending`,
targets whose asset is gone become `blocked` with a reason.

## Step 2 — Extract, per target

Details and batch scripts in `references/extraction.md`.

1. `get_parent`, walked up until the chain leaves Blueprint into script or C++.
2. `list_variables` → own variables.
3. `ObjectTools.list_properties` on the CDO → flattened list. **The CDO lower-cases the first
   letter**; normalize before diffing or every variable reads as inherited.
4. `ObjectTools.get_properties` → defaults, batched.
5. `list_functions` / `list_events` → inherited-inclusive with `bIsImplemented`.
6. `list_graphs` (only on an asset no other agent holds: it compiles an out-of-date Blueprint),
   `list_event_dispatchers`, `get_variable_category`, `get_variable_replication`.
7. **`read_graph_dsl` on every graph with real logic.** This is the step that separates documentation
   from an inventory.
8. `get_dependencies` / `get_referencers`.
9. The `K2Node_VariableGet` / `K2Node_VariableSet` sweep for the data-flow map.

For script and C++ classes, read the whole file including commented-out blocks — those are findings.

## Step 3 — Classify honestly

| Status | Evidence required |
|---|---|
| **Deprecated** | `meta=(DeprecatedFunction)`, `deprecated` in name/comment/category, a `Deprecated` folder, or a `+FunctionRedirects` entry |
| **Engine-invoked** | Override, `BlueprintImplementableEvent`/`NativeEvent`, `AnimNotify_*`, lifecycle event |
| **Name-bound** | Reached via a string literal — invisible to a node sweep by construction |
| **Orphaned** | Zero call sites across *all* swept sources, and none of the above |
| **Suspect** | Zero call sites but reachable from somewhere not swept |

Only **Orphaned** is a claim, and only when `_callindex.json` reports `sourcesNotSwept: []`. Every
Deprecated / Orphaned finding also gets a ticket in Pipeline: `python .octopus/todo.py new --title
"..." --source <page>`.

## Step 4 — Write the directory

Templates for all fourteen aspect files are in `references/templates.md`; tagging rules in
`references/taxonomy.md`.

**Every `full` target is a directory.** File count scales with size, existence does not:

| Size | Files |
|---|---|
| Major — >40 members or >30 graphs | 14, with `functions/` split per category |
| Standard — 8–40 members | 10 |
| Minimal — <8 members | `index.md`, `functions.md`, `findings.md` — still a directory |

The twelve analyses happen at every size: role, inheritance contribution, own variables, inherited
variables, functions, events, data flow, lifecycle, call graph, dependencies, config surface,
findings. A Minimal target folds them into three files; it does not skip them.

**Never fake a link.** A target with no page yet is plain `` `ClassName` `` plus *(not yet
documented)*. After writing, run the auto-linker so every backticked mention of a documented class
becomes a link.

## Step 5 — Batch, and never overrun

Process **deepest-first** in dependency order so a parent links to children that already exist.
Default batch: **8 `full` targets**, or 25 `summary`/`data` targets, per run.

After each target completes: mark it `done` in the worklist with its file list. After each batch:
regenerate the architecture pages and `README.md`, run the auto-linker, rebuild `_index.json`,
`_symbols.json` and `_callindex.json`
(`python "{{OCTOPUS}}/packs/blueprint/tools/build_callindex.py" --project "{{PROJECT}}" --manifest ... --source ...`),
run the checks in `references/taxonomy.md`, and report progress plus the pending count.

**`README.md` is a README file.** Write its new text only after the owner approved that exact text
(the rule `readme-needs-owner-approval.md`): show the owner the new text and ask. Until the owner says
yes, regenerate everything else and leave `README.md` as it is. The README guard refuses the write.

**Never start a target you cannot finish in the run.** A `pending` target is recoverable; a
half-written directory is not.

## Step 6 — Verify, then finish per project policy

Run the checks in `references/taxonomy.md`. Then check the tree: distinct navigation labels; no tag
outside the five axes; a search for a function name finds it; a search for a phrase from a function's
*prose* finds it; every cross-link resolves.

Then finish per the project's own conventions — if it keeps a changelist log, append one line; file each
finding as a ticket in Pipeline. Do not `save_assets` — this skill only reads the editor.

## Update run

`Docs/_index.json` exists and the user asked to **update**, not reprocess.

1. Stat every source in the manifest. Changed = mtime or size differs.
2. Add files that appeared; flag manifest entries whose file is gone.
3. **Propagate.** A changed parent invalidates children's pages, because inherited members are
   rendered into them. Walk `_callindex.json` and the dependency edges before regenerating.
4. Regenerate the expanded set only. Rebuild the call index only if a graph-bearing asset changed.
5. Refresh `README.md` and the drift report, after the owner approved the new `README.md` text; re-verify any agent-instruction claims (AGENTS.md /
   CLAUDE.md) touching changed files, if the project keeps such files.
6. Report files changed, pages regenerated, pages invalidated by propagation, new findings, and the
   worklist pending count. If nothing changed, say so in one line.

`AssetTools.is_dirty` catches an asset edited but unsaved — its disk mtime is stale while the editor
holds newer state.

## Correcting agent-instruction files

If the project keeps an AGENTS.md / CLAUDE.md, fix statements that are **factually wrong** — a
renamed class, a deleted function, a wrong path. Do not restructure sections. List every correction
in `Docs/README.md`'s drift report with before/after, and list what you deliberately did **not**
correct, with the reason.

## Scope discipline

Depth is not padding. Every sentence must carry something the tools cannot answer in one call: why the
class exists, what it assumes of others, who writes this variable, what breaks if you change it. If a
paragraph only restates names and types, cut it and spend the words on the dependency story.

Finish whole targets rather than starting many. A complete directory is useful; fifteen half-pages are
not.

## Reference files

- `references/extraction.md` — closure discovery, read-only tool list, batch script patterns, the CDO
  property-diff recipe, `read_graph_dsl` and its traps, T3D dumps for what the DSL cannot show, the
  data-flow sweep, call-index construction.
- `references/taxonomy.md` — the five axes, assignment rules, searchable descriptions, the extension
  protocol, and the validator.
- `references/templates.md` — worklist schema, tiering, templates for all fourteen aspect files plus
  the summary and data pages, the `###` block contract, and the JSON schemas.
