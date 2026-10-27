# Layout, tiering, and per-file templates

The `###` unit is called an **entry** throughout this skill, never a "block". `blocked` is a real
`_worklist.json` status, and "documentation block" reads as *documentation is blocked*.

A viewer or a search tool parses these files directly. The `###` entry metadata line and the member
table headers are a contract — change a column name and those rows vanish from search while still
looking fine in the markdown.

Class names in the examples (`BP_PlayerCharacter` and the others) are stand-ins; substitute your
project's classes.

## `_worklist.json` — the queue

Written by discovery, updated after every target. **A run is finished only when no target is
`pending`.** This file is why a half-done target can never again be lost.

```json
{
  "schemaVersion": 1,
  "generatedUtc": "<UTC timestamp>",
  "roots": ["/Game/YourGame/BP_GameMode", "/Game/YourGame/BP_PlayerController", "..."],
  "counts": { "full": 292, "summary": 63, "data": 110, "done": 0, "pending": 465 },
  "targets": {
    "BP_PlayerCharacter": {
      "assetPath": "/Game/YourGame/BP_PlayerCharacter",
      "sourcePaths": ["Content/YourGame/BP_PlayerCharacter.uasset"],
      "treatment": "full", "shape": "major",
      "kind": "character", "layer": "blueprint",
      "dir": "Classes/BP_PlayerCharacter",
      "depth": 1, "dependsOn": ["ParentCharacter"],
      "status": "pending", "files": [], "processedUtc": null, "blockedReason": null
    }
  }
}
```

- `treatment` — `full` | `summary` | `data`.
- `shape` — `major` | `standard` | `minimal`, only meaningful for `full`.
- `depth` — distance from the roots; **process deepest-first** so parents link to existing children.
- `status` — `pending` | `in-progress` | `done` | `blocked`. `in-progress` surviving a run means a
  crash mid-target: treat it as `pending` and reprocess that directory from scratch.
- `files` — every file written, so `reprocess` knows exactly what to delete.

## Tiering

**Every `full` target is a directory. There is no single-file class.** Size changes only how many
files live inside.

| Shape | Test | Files inside the directory |
|---|---|---|
| **Major** | >40 members **or** >30 graphs | 14, `functions/` split per category |
| **Standard** | 8–40 members | 10 |
| **Minimal** | <8 members | 3 — `index.md`, `functions.md`, `findings.md` |

Apply the test mechanically so re-runs are reproducible. A player character with 290 properties and
240 graphs is Major; a menu HUD with 0 own members and 2 graphs is Minimal — and
still gets `Classes/WBP_ExampleHud/index.md`, not `Classes/WBP_ExampleHud.md`.

A Minimal directory still answers all twelve analyses; `index.md` absorbs role, inheritance,
variables, data flow, lifecycle, dependencies and config surface as short `##` sections. It is a
directory so that deepening it later never means moving a file and breaking every inbound link.

**The twelve analyses happen at every tier.** Tiering changes how many files they occupy, never
whether they are done. A Standard class still needs data flow and lifecycle — they just share a page.

```
Docs/Classes/<ClassName>/
  index.md                 Major + Standard
  role.md                  Major + Standard
  inheritance.md           Major + Standard
  variables-own.md         Major + Standard
  variables-inherited.md   Major + Standard
  functions/index.md       Major only  (Standard uses a flat functions.md)
  functions/<category>.md  Major only
  functions.md             Standard only
  events.md                Major only  (Standard folds events into functions.md)
  graphs.md                Major only
  dataflow.md              Major + Standard
  lifecycle.md             Major + Standard
  callgraph.md             Major only  (Standard folds into dataflow.md)
  dependencies.md          Major + Standard
  config.md                Major only  (Standard folds into variables-own.md)
  findings.md              Major + Standard
```

Omit a file only when it would be empty, and say so in `index.md`'s at-a-glance table with an em
dash rather than dropping the row.

## Frontmatter — every page

```yaml
---
class: BP_PlayerCharacter
aspect: functions          # omit on index.md and on a single-file class
kind: character            # one value from the taxonomy's `kind` axis
layer: blueprint           # one value from the taxonomy's `layer` axis
tier: 1
tags: [movement, animation, gas]     # domain tags only
asset: /Game/YourGame/BP_PlayerCharacter
parent: ParentCharacter
sources:
  - path: Content/YourGame/BP_PlayerCharacter.uasset
    mtimeUtc: <UTC timestamp>
    size: 2451233
documentedUtc: <UTC timestamp>
---
```

`kind` and `layer` are separate keys — the viewer folds them into the tag filter. Putting
`kind:character` inside `tags` sends it to the OTHER bucket.

Every page after `index.md` opens with a breadcrumb line:
`Back to [overview](index.md) · [variables](variables-own.md) · [functions](functions/index.md)`

---

## `index.md` — the front door

Never a dumping ground. A reader who needs the variable list follows the link.

```markdown
# BP_PlayerCharacter

**Blueprint** · `/Game/YourGame/BP_PlayerCharacter` · parent
[`ParentCharacter`](../ParentCharacter.md) (script layer) · Major

Two to three sentences: what this class is for, in the game's terms.

## At a glance

| Aspect | Count | Page |
|---|---|---|
| Role and invariants | — | [role.md](role.md) |
| Inheritance | 3 levels | [inheritance.md](inheritance.md) |
| Own variables | 106 | [variables-own.md](variables-own.md) |
| Inherited variables | 183 | [variables-inherited.md](variables-inherited.md) |
| Functions | 93 | [functions/index.md](functions/index.md) |
| Events and dispatchers | 24 | [events.md](events.md) |
| Graphs | 237 | [graphs.md](graphs.md) |
| Data flow | — | [dataflow.md](dataflow.md) |
| Lifecycle | — | [lifecycle.md](lifecycle.md) |
| Call graph | — | [callgraph.md](callgraph.md) |
| Dependencies | — | [dependencies.md](dependencies.md) |
| Config surface | 31 editable | [config.md](config.md) |
| Findings | 7 | [findings.md](findings.md) |

## Components

| Component | Class | Declared in | Purpose |

## Start here

Three or four bullets pointing a newcomer at the pages that matter most for this class, in order.
```

## `role.md` — what the class is for

The page a shallow run never writes. Sections:

- **Responsibilities** — prose, not a list of function names.
- **Explicitly not responsible for** — what a reader would reasonably assume it does and it does
  not. This prevents the most common class of bug.
- **Invariants** — what must always hold. "`DesiredMovementVector` is written on every path,
  including the zero-vector ones, so it always reflects the last decision."
- **Design intent and history** — why it is shaped this way; cite the project's changelist log
  lines where the shape was changed deliberately, if such a log is kept.
- **How to change it safely** — the two or three things that break if you edit this class.

## `inheritance.md` — per-ancestor contribution

Not just the chain — **what each level adds**.

```markdown
## Chain

    ACharacter                        (engine)
     └─ ABaseCharacter          (C++)          movement, GAS, physics
         └─ AParentCharacter    (script layer) input, caching, speed, state
             └─ BP_PlayerCharacter    (Blueprint)    106 own variables, 237 graphs

## What each level contributes

| Level | Variables | Functions | What it owns |
|---|---|---|---|
| `ABaseCharacter` | 41 | 18 | Movement modes, ability system wiring, physics bodies |

## Overrides

| Member | Declared in | Overridden here | Why |
```

An **empty tier is a finding**, not a footnote — for example an empty parent GameMode that an
architecture doc calls a layer.

## `variables-own.md` / `variables-inherited.md`

Categories become `##` sections. Inherited variables group by **declaring class**, each group linking
to that class's page.

```markdown
## Movement|Climbing

| Name | Type | Declared in | Default | Description | Tags | Repl | Status |
|---|---|---|---|---|---|---|---|
| `bIsClimbing` | `bool` | this | `false` | True while the wall-sensor component reports a climbable wall contact | `#movement` `#accessor` | None | active |
```

Defaults come from `ObjectTools.get_properties` on the CDO — an empty Default column means you did
not read it, not that there isn't one.

**Never use the Description column for a relationship note.** A cell such as
"**Shadows** `AMyPlayerController::InputMode`" tells the reader *that* the variable shadows
something, but never what it holds. Describe the variable; put the relationship in
`findings.md`.

A variable whose semantics need a paragraph gets a `###` entry like a function.

## `functions/` — one `###` entry per function

**Never a table row.** Tables cannot hold a branch table, a side effect or a caveat, and the viewer
truncates cells to a few dozen characters. A table of functions ends as one short phrase per
function.

`functions/index.md` is a catalog: one line per function with a link to its entry, grouped by
category. Then one file per category.

```markdown
### `MovementVector`

**Kind** Function · **Declared in** `this` · **Status** active · **Tags** `#movement` `#query`

The core decision. Six branches are tested in order, and the **first** that matches returns — so the
order is the priority. Every branch writes `DesiredMovementVector` before returning.

| # | Condition | Result |
|---|---|---|
| 1 | `CustomMovementComponent.IsClimbing()` | Input rotated by the **actor's own rotation** |
| 2 | `!CustomMovementComponent.IsMovingOnGround()` | **Zero vector** — no air control at all |

| Parameter | Type | Meaning |
|---|---|---|
| `ControlRotation` | `FRotator` | Frame of reference for branch 6 |

**Returns** `FVector` — a world direction of magnitude ≤ 1, or the zero vector.
**Reads** `CustomMovementComponent`, `SteeringComponent`, the tag container.
**Writes** `DesiredMovementVector` on all six paths.
**Calls** `InputToWorldDirection`, `Init`.
**Called from** `BP_PlayerCharacter` per-frame movement update.
**Caveat** The null guards for two components call `Init()` but do **not** return — their `return`
lines are commented out, so branch 4 can dereference null.
```

The metadata line is parsed for `**Kind**`, `**Declared in**`, `**Status**`, `**Tags**`. Values run
to the next `**field**`, so keep the `·` between fields, never inside a value. Everything after that
line which is not a bold field or a table becomes the searchable description. **An entry without the
metadata line is invisible to the validator and to search** — it is not optional.

`Kind` is Function / Free function / Event / Dispatcher / Override / Interface.

**40–150 words of prose per non-trivial function.** A pure accessor can be one sentence. Include
`**Caveat**` whenever the logic has a trap — usually the most valuable line in the entry.

### Reading the logic is mandatory

- **Blueprint** — `BlueprintTools.read_graph_dsl(graph)` returns the graph as an S-expression. It is
  what revealed that `SetPaused` *also* clears `showHints`:

  ```
  (fn SetPaused (paused)
    (bind _output_get (Variables|Default|SetPaused paused))
    (if _output_get
      (Variables|Default|SetShowHints false)))
  ```

- **C++ / script layer** — read the body, including commented-out blocks. Guards that log-and-continue
  instead of returning, misspelled `UFUNCTION` specifiers, and cached-but-never-read properties are
  only visible in source.

## `events.md`

Events and dispatchers, each as a `###` entry, plus **what binds them**. An event nobody calls is
normal; an event nothing *binds* is a finding. Cover: firing conditions, payload, subscribers, and
ordering relative to tick.

## `graphs.md`

Graph inventory for Blueprints. For a 237-graph class, group by area and describe the group — never
enumerate 200 anim state graphs. Call out collapsed graphs, duplicated names (`OpenMenu` vs
`OpenMainMenu`), and graphs whose name no longer matches their content.

## `dataflow.md` — the page that earns the directory

For every variable of consequence: **who writes it, who reads it, and when**.

```markdown
| Variable | Written by | Read by | Lifetime |
|---|---|---|---|
| `bIsClimbing` | `TraceWalls` (tick) | `MovementVector`, `ABP_PlayerAnimation`, `BP_StateMachine` | one frame |
```

Then: state transition tables, ordering hazards ("written in tick, read by the AnimBP in the same
frame — order depends on tick group"), and any variable **written in more than one place**, which is
where desync bugs live.

Build this from the `K2Node_VariableGet` / `K2Node_VariableSet` sweep, not from guesswork. A node
with a `self` input pin reads the member off a *target object* — attribute it to that class.

## `lifecycle.md`

Construction → `BeginPlay` → tick → teardown. What is initialised where, what the init **order**
depends on, what is lazily initialised and what happens if it fails, what work runs every frame and
roughly what it costs. Lazy `Init()` called from a per-frame function is a lifecycle fact, not a
function detail.

## `callgraph.md`

Inbound (who calls into this class, and which member) and outbound (what this class calls, and on
what). Both from `_callindex.json`. Note name-bound edges separately — they are invisible to a node
sweep and are exactly what makes a reader think a function is dead.

## `dependencies.md`

Assets used and referencing, gameplay tags consumed and produced, data assets, module and plugin
dependencies. State the **contract**: what this class assumes of each dependency, and what breaks if
that assumption fails.

## `config.md`

The designer-facing surface: every `EditAnywhere` / `EditDefaultsOnly` property, its CDO default,
valid range, and **what visibly changes in-game** when it is altered. A designer should be able to
work from this page alone.

## `findings.md`

Graded findings, each with evidence and the sweep that produced it, using the status taxonomy in
`SKILL.md`. Close with a **Sweep boundary** section naming what was and was not swept — that is what
makes an "orphaned" claim trustworthy, or honestly downgrades it to "suspect".

Every Deprecated / Orphaned finding has a matching ticket in `TODO/`; name it by id and title.

---

## Summary page — third-party classes (`treatment: summary`)

One file at `Docs/ThirdParty/<ClassName>.md`. You do not maintain this code, so document **the seam**,
not the internals. `kind: thirdparty`.

```markdown
# BP_ExampleManager

**Third-party** · Example Plugin (`/Game/Marketplace/ExamplePlugin`) · reached from
[`BP_GameInstance`](../Classes/BP_GameInstance/index.md)

## What it is

Two or three sentences. What the marketplace asset does and why this project uses it.

## Who uses it here

| Project class | Uses it for | Via |
|---|---|---|

## The contract we depend on

The specific functions, properties and behaviours this project relies on — the surface that must keep
working across an update. Anything not listed here is a detail we do not care about.

## Gotchas

Version-specific behaviour, silent failure modes, ordering requirements, things that broke before.

## Upgrade risk

What to re-test if this plugin is updated, and which project classes to check first.
```

Do **not** enumerate its variables and functions. If a project class calls into it, that call is
documented on the project class's page, which is where a reader will be.

## Data page — structs, enums, tables, data assets (`treatment: data`)

One file at `Docs/Data/<AssetName>.md`. `kind` is `struct`, `enum`, `datatable` or `dataasset`.

Code reads these **by field name**, so a rename is a breaking change with no compiler error in
Blueprint. That is what this page exists to make visible.

```markdown
# S_ExampleRow

**UserDefinedStruct** · `/Game/YourGame/Data/S_ExampleRow`

## Purpose

What this type represents and where it is authored.

## Fields

| Field | Type | Units / range | Meaning | Read by | Written by |
|---|---|---|---|---|---|
| `Tag` | `FGameplayTag` | — | The gameplay tag that selects this mapping | `BP_PlayerController.ExampleTable` | authored in `DT_ExampleTable` |

For an **enum**, the same table with `Value` / `Meaning` / `Used by`, and state explicitly whether the
numeric values are persisted — if they are, reordering is a save-breaking change.

For a **DataTable**, give the row-struct link, the row-name convention, and who looks rows up by name.

## Consumers

Every class that reads or writes this type, linked.

## Rename and reorder risk

What breaks if a field is renamed, retyped or reordered, and whether anything persists these values
into a save game or a config file.
```

## Schema is a contract

Every member table uses the same column names. `Description` — not "What it does", not "Purpose".
Every class page carries `Declared in`. Three different headers hide rows from every tool keyed on
`Description`.

Structural tables (state-machine states, collapsed-graph lists) deliberately use `State`/`Purpose`
or `Collapsed graph`/`What it does` headers so the viewer skips them — they are not members.

## `_index.json`

```json
{
  "schemaVersion": 2,
  "generatedUtc": "<UTC timestamp>",
  "pages": {
    "Classes/BP_PlayerCharacter/functions/movement.md": {
      "class": "BP_PlayerCharacter", "aspect": "functions", "tierShape": "major",
      "kind": "character", "layer": "blueprint",
      "assetPath": "/Game/YourGame/BP_PlayerCharacter",
      "parent": "Classes/ParentCharacter/index.md",
      "sources": [{ "path": "Content/.../BP_PlayerCharacter.uasset",
                    "mtimeUtc": "<UTC timestamp>", "size": 2451233 }],
      "dependsOn": ["Classes/ParentCharacter/index.md"],
      "wasDirty": false
    }
  }
}
```

Keyed by **page**, so editing one aspect regenerates one file. `dependsOn` drives invalidation.

## `_callindex.json`

```json
{
  "schemaVersion": 1, "generatedUtc": "...",
  "sourcesSwept": ["tier1Blueprints", "levelBlueprints", "scriptLayer", "cpp", "config", "assetRefs"],
  "sourcesNotSwept": [],
  "symbols": {
    "ChangeStateFromTo": {
      "declaredIn": "Classes/BP_PlayerCharacter/functions/behaviour.md",
      "calls": [{ "site": "BP_PlayerCharacter", "graph": "ExampleGraph", "via": "node" }],
      "nameBound": []
    }
  }
}
```

A page may claim **orphaned** only if `sourcesNotSwept` is empty.

## `_symbols.json`

Flat derived search index for other tooling. **Never hand-edited**, regenerated every run. The viewer
ignores it and parses the markdown, so what you read can never disagree with what you search.

## README.md

1. **What this is** — one paragraph, how to regenerate, how to browse.
2. **Entry points** — default map, editor startup map, GameInstance, per-map GameMode/PC/HUD table.
3. **Class catalog** — name, kind, layer, parent, page link, one-line role, and **tier shape**.
   Undocumented classes get a row with an empty link so the gap is visible.
4. **Drift report** — corrections applied to the project's agent-instruction files (AGENTS.md /
   CLAUDE.md, if kept) with before/after, and discrepancies left alone with the reason.
5. **Coverage** — what was swept, what was skipped, tag counts per axis, timestamp.

## Style

Present tense, second person for instructions, no hedging. Where behaviour is unclear from static
reading, say "unverified — needs a PIE check" rather than guessing confidently. A page that guesses is
worse than one that admits the gap, because the next reader cannot tell which sentences were checked.
