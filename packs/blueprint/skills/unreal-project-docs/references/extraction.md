# Extraction recipes

The numbers in the examples show the scale of a large character Blueprint (`BP_PlayerCharacter`
below): approximately 240 graphs, 100 own variables, 290 CDO properties and 100 visible functions.
Compute everything again for your project.

## Closure discovery — the first thing a full run does

Breadth-first over `get_dependencies`, **traversing only through class assets** and recording
everything else as a leaf. Traversing through leaves would drag in every animation asset via the
AnimBP and never terminate usefully.

**Classify by type set, not per asset.** Build the type→paths map once with `find_assets`, then walk
`get_dependencies` and look each path up in the map. This is one call per type instead of one per
asset — and it does not depend on `exists` / `get_asset_class`, which are unreliable (see below).

```python
TYPES = {
    "Blueprint":         "/Script/Engine.Blueprint",
    "AnimBlueprint":     "/Script/Engine.AnimBlueprint",
    "WidgetBlueprint":   "/Script/UMG.WidgetBlueprint",
    "UserDefinedStruct": "/Script/Engine.UserDefinedStruct",
    "UserDefinedEnum":   "/Script/Engine.UserDefinedEnum",
    "DataTable":         "/Script/Engine.DataTable",
}
kind = {}
for label, ref in TYPES.items():
    for p in t(AS + "find_assets", folder_path="/Game", name="",
               asset_type={"refPath": ref}, recursive=True):
        kind.setdefault(p, label)

CLASSY = {"Blueprint", "AnimBlueprint", "WidgetBlueprint"}
seen, data, depth, queue = {}, {}, {r: 0 for r in ROOTS}, list(ROOTS)
while queue:
    p = queue.pop(0)
    if p in seen or p in data:
        continue
    k = kind.get(p)
    if k is None:              # content leaf — not traversed, not documented
        continue
    if k not in CLASSY:
        data[p] = k            # struct/enum/table -> `data` treatment
        continue
    seen[p] = k
    for x in t(AS + "get_dependencies", asset_path=p):
        if x.startswith("/Game") and x not in seen and x not in data:
            depth.setdefault(x, depth.get(p, 0) + 1)
            queue.append(x)
```

On a mid-sized project this returns a few hundred class assets (project classes at depths 0–4 and
third-party classes) and several hundred leaves, of which a part are `data`.

Traps:

- **Exit PIE before discovering.** While Play-In-Editor is running, packages are duplicated under a
  `UEDPIE_0_` prefix and object resolution goes through the PIE world. `find_assets` still works
  (it queries the asset registry), but `exists`, `get_asset_class` and **`get_dependencies` all fail
  with "Asset does not exist"** for paths that plainly do exist. Check
  `LogsToolset.GetLogEntries(pattern="UEDPIE|EndPlayMap")` before a run: a `UEDPIE_0_` line with no
  later `EndPlayMap` means PIE is live — stop and ask the user to press Stop.
- **`exists` and `get_asset_class` disagree with the registry.** `exists` has returned `false`, and
  `get_asset_class` has raised, for assets `find_assets` lists and `load_asset` loads successfully.
  Never build a traversal on them; the type-set map above avoids both.
- **A raise inside the sandbox can abort the whole script**, not just the iteration — wrap every
  per-asset call in `try/except` even when a guard "should" have prevented it.
- **`get_dependencies` returns hard *and* soft references**, so `TSoftObjectPtr` targets are in the
  closure. Correct — they are still reachable — but it means data assets pull in their content.

Leaves are classified for `treatment`:

| Leaf class | Treatment |
|---|---|
| `UserDefinedStruct`, `UserDefinedEnum`, `DataTable`, `CurveTable`, anything ending `DataAsset` | `data` — gets a page |
| `AnimSequence`, `AnimMontage`, `BlendSpace`, `Texture2D`, `Material*`, `StaticMesh`, `SkeletalMesh`, `SoundWave`, `ParticleSystem`, `Font`, `World` | referenced rows only |

Anything under the project's third-party content roots (marketplace folders such as `/Game/ThirdParty`
or `/Game/Marketplace` — identify them during discovery) or a marketplace plugin path gets
`treatment: summary` regardless of class.

Add the code classes separately — they are not in the asset graph. Grep `Source/**` plus
`Plugins/*/Source/**` for `class\s+\w+_API\s+(\w+)\s*:\s*public\s+(\w+)`. If the project has a
scripting layer (e.g. AngelScript), grep its sources too — `Script/**/*.as` for
`^\s*class\s+(\w+)\s*:\s*(\w+)` on AngelScript projects.

## Reprocess — delete before you read

`reprocess` is destructive by contract. The procedure:

**Delete one target immediately before writing it, never a batch up front.** Deleting four targets and
then generating two leaves two classes with no documentation at all and inbound links pointing at
nothing — strictly worse than having left the stale pages in place. The delete and the first write of
a target belong in the same step.

1. Resolve the target list and **echo it to the user with a count** before touching anything.
2. For each target, read its `files` array from `_worklist.json` — that is the authoritative record of
   what was written.
3. Delete the whole directory (`Docs/Classes/<Name>/`), not the individual files. A file written by an
   older version of the skill will not be in `files` and must still go.
4. Set the worklist entry to `status: pending`, `files: []`, `processedUtc: null`.
5. Regenerate from the editor and the source. **Do not read the deleted content, and do not consult
   `_symbols.json` for what used to be there.** The point is that stale text cannot leak forward.

Reprocessing is also the right response to finding an `in-progress` entry left over from an
interrupted run — that directory is in an unknown state and must be rebuilt, not patched.

## Read-only tools worth calling

Never `describe_toolset` on `BlueprintTools` — 72k characters. This is the whole read surface:

| Tool | Returns |
|---|---|
| `AssetTools.find_assets(folder_path, name, asset_type?, recursive?, tags?)` | asset paths |
| `AssetTools.load_asset(asset_path)` | object ref, the handle everything else takes |
| `AssetTools.get_asset_class(asset_path)` | e.g. `Blueprint`, `AnimBlueprint` |
| `AssetTools.get_dependencies` / `get_referencers(asset_path)` | asset-level edges |
| `AssetTools.get_asset_tags` / `get_metadata_tags(asset_path)` | registry tags, incl. `ParentClass` |
| `AssetTools.is_dirty(asset_path)` | unsaved editor state — see the staleness trap below |
| `AssetTools.list_folders(root_path, recursive?)` | content folder tree |
| `BlueprintTools.get_parent(blueprint)` | parent class ref |
| `BlueprintTools.list_variables(blueprint, graph?)` | **own** member variable names, no types |
| `BlueprintTools.list_functions` / `list_events(blueprint)` | `{name, description, bIsImplemented}`, inherited included |
| `BlueprintTools.list_graphs(blueprint)` | every graph, including anim state graphs |
| `BlueprintTools.list_event_dispatchers(blueprint)` | dispatcher graphs |
| `BlueprintTools.get_variable_category` / `get_variable_replication(blueprint, name)` | per-variable metadata |
| `BlueprintTools.get_default_object(blueprint)` | the CDO |
| `BlueprintTools.find_nodes(graph, title, node_class?, entry_points_only?)` | node refs |
| `BlueprintTools.get_node_infos(nodes)` | `{node, type_id, position, input_pins, output_pins}` |
| `ObjectTools.list_properties(instance)` | **flattened** property schema, JSON string |
| `ObjectTools.get_properties(instance, properties)` | default values |
| `ObjectTools.search_subclasses(base_class, class_name)` | children of a class |

`BehaviorTreeTools`, `StateTreeTools`, `ConversationTools`, `GameplayTagsToolset`,
`AttributeSetToolset` and `DataTableTools` cover AI, dialogue, tags, GAS attributes and data tables
when a documented class touches them.

## Batch script shape

```python
import json

def t(name, **kw):
    return execute_tool(name, json.dumps(kw))["returnValue"]

BP = "editor_toolset.toolsets.blueprint.BlueprintTools."
AS = "editor_toolset.toolsets.asset.AssetTools."
OB = "editor_toolset.toolsets.object.ObjectTools."

def run():
    bp = t(AS + "load_asset", asset_path="/Game/YourGame/BP_PlayerCharacter")
    return {"parent": t(BP + "get_parent", blueprint=bp)}
```

`execute_tool` wants the **fully qualified** tool name and a **JSON string** — unlike `call_tool`,
which takes a short name and an object. Sandbox imports are limited to
`math, json, datetime, time, re, copy`; there is no `os`, no filesystem, no network.

Keep each script to a few hundred tool calls. A 237-graph sweep in one script risks a timeout —
page it by graph range and accumulate across calls.

## Variables: own vs inherited

`list_variables` gives the class's own names in PascalCase. `list_properties` on the CDO gives the
full flattened set **with the first letter lower-cased** — `AIController` appears as `aIController`,
`ActionTriggered` as `actionTriggered`.

**A raw set-difference therefore reports every variable as inherited and every own variable as
missing.** Normalize before diffing — lower the first character only, nothing else:

```python
def lc(n):
    return n[:1].lower() + n[1:] if n else n

props = json.loads(t(OB + "list_properties", instance=cdo))   # returns a JSON *string*
own   = t(BP + "list_variables", blueprint=bp)
own_norm  = set(lc(n) for n in own)
inherited = sorted(set(props) - own_norm)
assert not (own_norm - set(props)), "normalization drifted"
```

Keep that assertion. If a future engine version changes the casing rule it fails loudly instead of
silently mislabelling every variable's origin.

Types come from the same schema fragment: `props[lc(name)]["title"]` for object types
(`/Script/AIModule.AIController`), `["type"]` for primitives (`boolean`, `number`, `string`). Some
properties — delegates and multicast delegates — carry neither; render those as `delegate` rather
than emitting `null`.

### `list_properties` cannot tell `FString`, `FName` and `FText` apart

**All three return `"string"`.** A page that renders all of them as `FString` is wrong, and the cost
is not only the quality of the docs. A porter who reads the page copies the wrong type into native
code. `FStrProperty::ConvertFromType` returns `CannotConvert` for a `NameProperty` tag, and containers
convert element by element, so **every affected field loads EMPTY** from existing data, and an
`FText` declared as `FString` loses its localisation identity.

**So never take the type of a struct field from `list_properties`.** The correct source is the
authored `FEdGraphPinType` inside each `FStructVariableDescription` of the `UserDefinedStruct`:
`ContainerType`, `Category` (key or element), `TerminalCategory` (the value of a map). Read it from a
T3D export of the struct, or decode it from the `.uasset`. Use it for every field on a struct page or
a DataTable page.

Two related traps:

- **A DataTable page documents the fields of its ROW STRUCT under a different name**, under a
  `## Columns` heading. If you pair pages to structs by page name, you miss exactly those pages, and a
  porter most probably reads them.
- **`attackTable_49`, `values_52` and similar names are the generated VALUE-PROPERTY names of a map.**
  They are not types. If a name looks like `<field>_<digits>`, it is a generated property name. Do not
  document a "wrapper struct" for it.

**A finding that rests on a wrong type is worse than no finding, because it looks like work.** When
you correct a type, examine again every finding on the page that depends on it.

Defaults come from `ObjectTools.get_properties(cdo, [names…])`. Batch them; one call per variable
across hundreds of properties is wasteful.

To attribute an inherited variable to the class that actually declares it, walk `get_parent` upward
and repeat the own-list at each level. Once the chain leaves Blueprint — `get_parent` returns a
`/Script/...` class — there is no toolset. Read the `.h`/`.cpp` (or script) file as text and parse
`UPROPERTY` blocks.

## Functions and events

`list_functions` returns inherited entries too, so it is the right source for "including the parent
ones". `bIsImplemented: false` means the function is *available to override but is not overridden*
here — an empty slot, not a member and not an orphan. Unimplemented overridables must not appear in
the member table.

`description` is the editor tooltip and is empty far more often than not. When it is empty, derive
purpose by reading the graph, not by restating the name.

## Reading the logic — `read_graph_dsl`

The single most valuable tool here, and the one the shallow runs never used. It returns a whole graph
as a readable S-expression:

```python
dsl = t(BP + "read_graph_dsl", graph=graph_ref)
```

```
(fn SetPaused (paused)
  (bind _output_get (Variables|Default|SetPaused paused))
  (if _output_get
    (Variables|Default|SetShowHints false)))
```

Four lines, and they contain a side effect no amount of staring at the name would give you: setting
`paused` **also clears `showHints`**. Every function block must be written from this, not
from the function's name.

Notes:

- Call it on every graph with real logic. Pure getters are not worth the round trip.
- It returns empty for `AnimGraph`, and it drops node positions and comments — fine for reading,
  wrong for round-tripping edits.
- **It only walks exec chains reachable from events and function entries.** Disconnected islands —
  dead code left after refactors — are invisible, and no error is raised. When completeness matters
  (orphan claims, dead-code findings), cross-check with a T3D dump (below).
- Long graphs return a lot of text. Page them: read one category of functions per call rather than
  all graphs at once.
- **Two exporter artefacts that look like real calls and are not.**
  - **`ToolMenus|Get`** is an unnameable validity-check node — read it as `IsValid(x)`, with `:then` =
    valid and `:else` = invalid. Not an editor-module call.
  - **`Class|<ClassName>|<Name>`, `URigHierarchy|GetIndex` and every prefix like them can name a
    class that the node never referenced.** The decompiler prints `|Name` for a node whose own
    category is empty, which is what a Blueprint's own function and every variable access produce.
    It then replaces that with the first entry in the graph's action list that ends in the same name,
    and that list holds one entry per class in the project that declares the name. `Get<Word>` and
    `Set<Word>` are titles the editor builds for a variable read and a variable write, so the printed
    symbol can be a member with no function anywhere.

    **Take the owner from the node. The printed prefix is a menu path.** `MemberParent` and
    `MemberName` on the node's own reference are the answer, and `bSelfContext=True` means the
    graph's own class. `t3d_graphs.py` reports both, as `callOwner` and `varOwner`. A bare `|Name`
    with no prefix means the owner is unconfirmed until the node answers. **Never write a
    cross-domain call, a dependency or a missing dependency from a prefix.**

- **Check `get_parent` before reading any Blueprint's graphs.** If it returns `"None"`, the asset has
  no authoritative class (`skel: [NULL]`) and **`read_graph_dsl` will crash the editor** — the
  Blueprint action filter dereferences the null in `IsEventUnimplementable`. `list_graphs` and
  `list_variables` still succeed on such an asset, so a successful size probe is not evidence it is
  safe to read. **Block the target instead.** This can occur on widgets derived from a marketplace
  plugin class; the fix is a forced recompile of the asset, not a documentation change.

- **Never batch `read_graph_dsl` through a sandbox script.** One call per graph, as its own
  `call_tool`. A script looping over graphs runs every one on the game thread inside a single MCP
  request with no yield between them, so a graph that hangs takes the whole batch, the MCP request
  and the editor with it — and the failure names no graph, so you cannot tell which one did it
  without re-probing individually. Individual calls survive a hang as one lost call and identify the
  culprit for free.
- The `Variables|Category|SetName` form in the body is the same `type_id` grammar the node sweep
  uses, so a DSL read doubles as a data-flow sample for that graph.

## T3D dumps — what the DSL cannot show

The editor can export any asset as T3D text (`export_bp_t3d.py`, through the `AssetTextExport`
plugin; the exports go to the directory that `t3dDir` in `{{PROJECT}}/.octopus/blueprint.json` names).
The host-side parsers of the pack read those dumps. Run each as
`python "{{OCTOPUS}}/packs/blueprint/tools/<tool>.py" --project "{{PROJECT}}" ...`:

| Tool | Use for |
|---|---|
| `t3d_graphs.py <file> [--graph NAME]` | Every graph of an asset, with each node scoped to its graph, and links resolved per graph. Reports `callOwner` / `varOwner`. |
| `t3d_dump.py <file>` | Full node/pin/link model of a one-graph dump — including disconnected islands the DSL hides. |
| `exec_islands.py [--asset PATH]` | Detached exec islands in every package, read from the `.uasset` bytes. The check for dead-code findings. |
| `animgraph_dump.py <file>` | AnimGraph pose connectivity. The DSL exports AnimGraphs empty; the T3D has everything. Pins are keyed by `(node, pinId)` because pin ids repeat across copied nodes. |
| `rigvm_dump.py <file> [graph]` | Control Rig RigVM model — nodes, pins with defaults, links. |
| `build_callindex.py --manifest ... --source ...` | Builds `Docs/_callindex.json` from a manifest of T3D dumps; records `blindSpots` (self-context inherited calls, interface dispatch) so no consumer can forget them. |

Do not search a T3D from inside the editor (`AssetTools.read_file` caps at 80 KB) — export in the
editor, parse on the host.

## Data-flow sweep

`dataflow.md` needs, per variable, the set of graphs that read it and the set that write it. Build it
from the same node sweep as the call index:

```python
GET = {"refPath": "/Script/BlueprintGraph.K2Node_VariableGet"}
SET = {"refPath": "/Script/BlueprintGraph.K2Node_VariableSet"}

for g in graphs:
    for cls, bucket in ((GET, readers), (SET, writers)):
        nodes = t(BP + "find_nodes", graph=g, title="", node_class=cls)
        for info in (t(BP + "get_node_infos", nodes=nodes) if nodes else []):
            name = info["type_id"].rsplit("|", 1)[-1]        # strip category path
            name = name[3:] if name[:3] in ("Get", "Set") else name
            if any(p["name"] == "self" for p in info["input_pins"]):
                continue                                      # reads off a target object
            bucket.setdefault(name, set()).add(graph_name(g))
```

Compare **exactly** after stripping `Get`/`Set` — substring matching confuses `Velocity` with
`Velocity_LastFrame`.

A variable that appears in `writers` for more than one graph is worth a line in `dataflow.md`
regardless of anything else; multiple writers is where desync bugs live.

## Call index

Sweep node classes per graph:

| Class ref | Meaning |
|---|---|
| `/Script/BlueprintGraph.K2Node_CallFunction` | a call |
| `/Script/BlueprintGraph.K2Node_VariableGet` | a read |
| `/Script/BlueprintGraph.K2Node_VariableSet` | a write |

`get_node_infos` returns `type_id`, and **there is no `title` key** — don't index on one.

`type_id` for a call is `Category|FunctionName`, with the category empty for uncategorised project
functions. Real samples:

```
Behaviour|ChangeStateFromTo          Utilities|LoadAsset_Blocking
|PreloadAssets                       Components|SkeletalMesh|GetAnimInstance
|Parent:ConstructionScript           Math|Boolean|NOTBoolean
```

So the symbol is the **last `|` segment**, and a `Parent:` prefix marks a parent-call node — count
those as calls to the parent's implementation, not the child's. Categories nest (`Utilities|Map|Find`),
which is why splitting on the last separator is the only rule that holds.

For variable nodes the `type_id` is the variable name prefixed `Get`/`Set` under a category path;
strip the prefix and compare **exactly**, not by substring — `Velocity` and `Velocity_LastFrame`
collide otherwise.

A node whose input pins include one named `self` reads that member off a *target object*, not off
this class. Attribute it to the target's class or it inflates the owner's usage count.

### Sources that must be in the index

Missing any of these turns "orphaned" into a false claim:

1. Every tier-1 Blueprint's graphs.
2. **Level Blueprints** in every `.umap` under `Content/`.
3. Script-layer sources as text (if the project has one).
4. `Source/**` and `Plugins/*/Source/**` as text.
5. `Config/*.ini` — class paths (`GameInstanceClass`), input mappings, and any
   `+FunctionRedirects` blocks.
6. `AssetTools.get_referencers` for asset-level use that no graph node shows.

Name-bound call sites — `BindAction`, `SetTimerByFunctionName`, `AddUFunction`, montage
`AnimNotify_*`, Chooser and StateTree assets — appear only as **string literals**. Regex the text
sources for each documented function name before calling it orphaned.

## Staleness trap

A Blueprint edited but not saved is newer in the editor than on disk. The manifest compares disk
mtimes, so such a page looks current while describing stale content. Check
`AssetTools.is_dirty(asset_path)` for every tier-1 asset at the start of a run; if any are dirty,
say so explicitly and either ask the user to save or note the affected pages as provisional. Do not
call `save_assets` yourself — this skill only reads.
