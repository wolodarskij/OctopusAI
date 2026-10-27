---
name: read-blueprint-without-editor
description: Read a Blueprint's full graph logic - nodes, pins, links, literals, disconnected islands - from the .uasset on disk, without the live editor resolving anything. Use when a graph refuses to read ("UserRaisedError", read_graph_dsl returns nothing, get_node_infos raises), when a whole asset must be analysed at once, when the editor is unavailable or busy with another agent, or when you need evidence the DSL cannot give (dead code, real FunctionReference owners, node positions). Triggers "this graph won't read", "the editor can't open X", "document every function", "why does read_graph_dsl return empty".
---

# Reading a Blueprint without the editor

The live editor is not the only route to read a Blueprint, and for exhaustive work it is the wrong
route. This route exports the asset to T3D text and parses the text on the host. The editor resolves
nothing, so nothing can raise.

## When the editor route fails, and why

`read_graph_dsl` and `get_node_infos` both resolve node references. A node whose reference does not
resolve raises `UserRaisedError`. That is an error **with an empty message** from
`KismetSystemLibrary::RaiseScriptError`. The log shows it as a bare `LogScript: Warning:` with no text.

Three properties make it worse than it looks:

1. **It kills the whole graph.** One bad node, and the graph gives *nothing*. A 5-node graph fails
   the same as a 500-node graph.
2. **You cannot catch it inside `execute_tool_script`.** `try/except` does not see it. The script
   stops at that line and returns nothing. A loop over eight graphs that raises on the third gives no
   data for any graph, and the loop never tries graphs four to eight.
3. **So it makes a false census.** If you count the graphs with no result as unreadable, the number
   measures your batch size, not the asset. The sign is that *coverage goes down when the batch size
   goes up*.

**`find_nodes` never raises.** So first, divide the asset cheaply:

```python
find_nodes(graph=g, title="<suspect name>")   # substring match, never raises
```

One script, every graph, no risk. It shows which graphs are affected and which node causes it, with
no attempt to resolve the node.

## The route in

```
python "{{OCTOPUS}}/packs/blueprint/tools/export_bp_t3d.py" --project "{{PROJECT}}" /Game/<path to the asset>
    -> <backup directory>/<Asset>_<stamp>.uasset    (a record, outside the project)
    -> <t3dDir>/Text<Asset>-N.txt                    (T3D: objects, nodes, pins, subobjects)
    -> Saved/<Asset>Graphs/*.json                    (one JSON per graph + _index.json)
```

`{{PROJECT}}/.octopus/blueprint.json` names the backup directory and the T3D directory (`t3dDir`, default
`T3D`). The text comes from the asset-text export toolset of the `AssetTextExport` plugin. It dumps
the LIVE object from memory, so nothing resolves and nothing can raise.

**Do not write a package inside the project.** The engine mounts `Saved/` as the package root
`/Temp/`. A `.uasset` put there is a real package, and other packages can import it. A package saved
while such a copy is mounted records that path as a dependency, and the reference stays after the
file is deleted.

## Before you trust the dump

**Run `is_dirty` first.** A backup copy takes the last *saved* file. A dirty asset means that the
dump can show old logic. Do not save the asset yourself: when several agents work, dirty means that
somebody is in the middle of an edit.

If it is dirty, measure the difference; do not guess. Export the last saved file and the latest
autosave, and compare the two exports **file against file**. Zero differences means that the unsaved
change has no effect on the graphs, and you can continue. **Do not** compare against the live asset
for this question: the load of the old copy gives every node a new path, so every pin reports
"connections changed".

**Compare the dump with the live editor** before you use it. `find_nodes` never raises, so compare
the node and pin counts; `list_graphs` adds the graph count, but it compiles a Blueprint that is out
of date, so never call it on an asset that another agent holds. They must match exactly. One difference
is harmless: `find_nodes` returns only `K2Node_*`, and the T3D also has `EdGraphNode_Comment`, so the
T3D node count can be a little higher.

## Two traps that silently corrupt the parse

**Node names are not unique across graphs.** In a large Blueprint, a large part of the node names
repeat: `K2Node_FunctionEntry_0` is in every function graph. `t3d_dump.py` keys nodes by bare name in
one flat dict. That is correct for a dump of one graph and **wrong for a whole asset**: it merges
nodes silently. `t3d_graphs.py` exists for this, and it scopes every node to its graph.

**`PinId` is not globally unique either.** Copied nodes have the same pin ids. A global id-to-pin map
loses pins and resolves links to the wrong nodes. Resolve links **per graph** by
`(node name, pin id)`. This is exact, because a link never crosses a graph boundary and `LinkedTo`
already names its node. `t3d_graphs.py` reports `unresolvedLinks`. A count that is not zero is a parse
failure, not missing wiring.

```
python "{{OCTOPUS}}/packs/blueprint/tools/t3d_graphs.py" --project "{{PROJECT}}" <t3dDir>/Text<Asset>-N.txt --summary
python "{{OCTOPUS}}/packs/blueprint/tools/t3d_node_chain.py" --project "{{PROJECT}}" <t3dDir>/Text<Asset>-N.txt "<start marker>" [max-depth]
```

Follow an exec chain or a data chain with `t3d_node_chain.py`, which resolves by `PinId` inside one
graph. Never follow a chain with a regex on node names.

## The T3D structure

Two passes, both nested, and the indent is significant:

```
Begin Object Class=/Script/Engine.EdGraph Name="MyGraph"          <- declarations, depth 1
   Begin Object Class=/Script/BlueprintGraph.K2Node_VariableGet Name="K2Node_VariableGet_9"
   End Object
...
   Begin Object Name="MyGraph"                                    <- bodies, depth 1
      Begin Object Name="K2Node_VariableGet_9"                    <- node body, depth 2
         VariableReference=(MemberName="Foo",bSelfContext=True)
         NodePosX=-48
         NodeGuid=D441D3EE...
         CustomProperties Pin (PinId=...,PinName="Foo",Direction="EGPD_Output",...,LinkedTo=(...))
      End Object
```

The first pass gives the **class** of each node. The second gives its **properties and pins**. Scope
both by graph. `NodeGuid` is unique and is the safest key.

## What this gives you that the DSL cannot

- **Disconnected islands.** `read_graph_dsl` walks only the exec chains that events and function
  entries reach, and it gives no error for what it skips. Dead code left by a refactor is not visible
  there and is present here. This is what makes a dead-code claim or an orphan claim valid.
  `exec_islands.py` finds them in every package from the `.uasset` bytes.
- **The real owner of a call.** T3D carries `FunctionReference.MemberParent` exactly, so a library call
  reads as the library. The DSL form `Class|<Unrelated>|<Function>` gives the **wrong** owning class.
  Take the owner from `MemberParent` and `bSelfContext` on the node; `t3d_graphs.py` reports them as
  `callOwner` and `varOwner`.
- **Node positions and comments**, which the DSL drops.
- **One artefact:** a pin type can name the temporary load path, for example `/Temp/Diff/<Asset>_<stamp>`,
  because the export loads a *copy* of the asset. Read it as the normal class (`<Asset>_C`). It is not a
  real dependency, and you must not report it as a reference to another package.
- **Unwired pin literals** as `DefaultValue` / `DefaultObject`: the values that a Set node applies with
  no wire. A rebuilt node silently loses exactly these values and gets the type default.

## Finding callers of a symbol

`search_bp_refs.py` searches binary `.uasset` files for a name. It can return only the asset that
*declares* the symbol, and that looks like "nothing calls this". Call sites inside the same asset are
in the dump, not in that result.

```
python "{{OCTOPUS}}/packs/blueprint/tools/search_bp_refs.py" --project "{{PROJECT}}" <symbol> --maps
```

Scan the per-graph JSON too:

```python
# a caller is a K2Node_CallFunction whose FunctionReference names the symbol or carries
# the event node's NodeGuid as MemberGuid
fr = node['props'].get('FunctionReference', '')
if event_guid in fr or ('MemberName="%s"' % symbol) in fr:
    ...
```

Match on **both**. `MemberGuid` is the reliable key (it survives a rename). `MemberName` finds
references that carry no guid. Run it over every `*.json` in the output directory, not only over the
graph of the symbol: a function can have callers in the event graph and in other function graphs.

**A zero result is still not `orphaned`.** Call sites bound by name - timers, `CallFunctionByName`,
delegates bound by string, Chooser and StateTree assets - leave no reference that a dump or a grep can
see. Use `suspect` and say which sweeps you ran, unless the project keeps a complete call index
(`build_callindex.py`).

## What it cannot tell you, and the traps that looked like gaps

**`text` pin defaults look absent, and they are not.** `DefaultValue` holds the literal with its inner
quotes **escaped**. The obvious `DefaultValue="([^"]*)"` stops at the first `\"` and gives a cut
`LOCTABLE(\`, which looks exactly like an empty value. `t3d_graphs.py` has two fixes:

- it reads **`DefaultTextValue`**, which has the same literal with no escapes;
- it matches **balanced parentheses**, not up to the first comma. Every one of these forms contains
  commas.

```
DefaultTextValue=LOCTABLE("/Game/Texts/ExampleTable.ExampleTable", "exampleKey")
DefaultTextValue=NSLOCTEXT("[<pkg guid>]", "<key guid>", "{name1}\r\nvs.\r\n{name2}")
```

`LOCTABLE` gives the string table and the key; resolve it in the string table asset. `NSLOCTEXT`
carries the **source string inline**, so it needs no lookup.

**A field that is empty in hundreds of samples is much more probably a parse failure than a real
absence.** Look at the raw text before you decide that something "is not exported":
`grep -o 'DefaultValue="[^"]*' <dump>` next to `grep -c NSLOCTEXT <dump>` answers it in one call.

**The same truncation hits `string` and `name` pins.** Any quoted T3D value whose content has an
escaped quote is cut, for example rich-text literals such as `<img id="Icon"/> `, which come back as
`<img id=\`. `field()` in `t3d_graphs.py` is escape-aware (`"((?:[^"\\]|\\.)*)"` and then unescape),
so every quoted field is correct. The sign of a survivor of this defect is a default that **ends in a
single backslash**. Scan for it after a parse:

```python
[p for n in nodes for p in n['pins'] if str(p.get('default','')).endswith('\\')]
```

**`MacroGraphReference` is exported.** The export writes

```
MacroGraphReference=(MacroGraph="/Script/Engine.EdGraph'…StandardMacros.StandardMacros:ForEachLoop'",
                     GraphBlueprint=…,GraphGuid=99DBFD5540A796041F72A5A9DA655026)
```

for **every** macro instance. A parser that looks for a bare `MacroGraph` key finds nothing, because
that key is never at top level. `t3d_graphs.py` reads `MacroGraphReference` and gives `macro`,
`macroGuid` and `macroLocal`.

A macro whose path is not under `StandardMacros` is **declared by this Blueprint**, so its instances
are call sites that you can attribute. **A macro local to a class can only have instances inside that
class**, so zero instances in the dump of that class is complete evidence.

**Function locals are on the entry node, as an indexed property.**

```
LocalVariables(0)=(VarName="texts",VarGuid=…,VarType=(PinCategory="text",ContainerType=Array),…)
LocalVariables(1)=(VarName="count",VarGuid=…,VarType=(PinCategory="int"),…)
```

The index makes it a prefix, not a key, so an exact `key=` match misses it. An inference from the
`MemberScope` of Get/Set nodes finds only the locals that something *uses*. The declaration finds all,
also the unused ones. `t3d_graphs.py` gives a `locals` list on the entry node.

All of these have the same shape: **an indexed or nested T3D property does not match a flat key
lookup, and the silent result is an empty field that looks like an absent feature.** Before you write
"X is not exported", grep the raw dump for `X` one time.

## The export does not use one encoding - check the BOM

The export writes plain **UTF-8** for some assets and **UTF-16LE with a BOM** for others, from the
same call.

A decode of a UTF-16 dump as UTF-8 **does not raise**. Every second byte is a NUL, which is a legal
UTF-8 character. The text survives as `B\x00e\x00g\x00i\x00n` and matches no pattern. With
`errors='replace'` you get **0 graphs from a complete file**, which looks like "this asset has no
graphs".

`t3d_graphs.py` examines the BOM (`read_dump`). If you write another reader for these dumps, examine
the BOM too. Treat a parse with zero graphs as a **tool** result until you prove otherwise:

```
head -c2 <dump> | xxd -p        # fffe = UTF-16LE, efbbbf = UTF-8 BOM, anything else = raw UTF-8
```

**`enum_dump.py` orders internal names as text, not by value.** So the internal name of the eleventh
value sorts between those of the second and the third value, and gets index 2. Never read the meaning
of an enumerator from its *position* in that output. Pair each value by its trailing number, or by
the value order of a switch node on the enum. If you do not, every value above 9 gets a wrong label,
and the documentation still looks correct.

**The `MemberGuid` of a function is the `GraphGuid` of its graph.** `K2Node_FunctionEntry` usually
exports `FunctionReference=(MemberName="Foo")` with **no** `MemberGuid`. That does not stop a sweep by
guid for that function: the guid that callers store is on the graph object:

```
Begin Object Class=/Script/Engine.EdGraph Name="Foo"
   GraphGuid=B6EF9D204F1348810E7E56B1B7D24F30      <- this is what call nodes carry as MemberGuid
```

So you can sweep every Blueprint function by guid. Read `GraphGuid` from the graph, then sweep the 16
bytes **little-endian per 32-bit group** across the packages (`guid_sweep.py`, `graph_guids.py`). The
raw big-endian order returns zero for everything. Validate the byte order on a function that you know
has callers before you trust a zero. A control that returns the expected count separates "no callers"
from "wrong byte order".

Two ways to read the wrong guid, and both give a confident wrong answer:

- **The forward declaration has no guid.** `Begin Object Class=/Script/Engine.EdGraph Name="Foo"` is
  the declaration pass. The guid is in the **body** block, `Begin Object Name="Foo"`.
- **The first `GraphGuid=` in the body can belong to something else.** A nested
  `MacroGraphReference=(…,GraphGuid=…)` points at the macro that the graph uses, usually from the
  engine `StandardMacros`. A sweep of that guid measures how popular `ForEachLoop` is, not who calls
  your function. Take the guid at the **indent level of the graph object**.

The same guid identity holds for dispatchers, and the two guids answer different questions. The
`VarGuid` of a delegate appears wherever it is *referenced*. The **`GraphGuid` of the signature graph
appears only on the `Delegate` pin of an Add/RemoveDelegate**. So `GraphGuid` present means a bind;
`VarGuid` alone means a broadcast.

**A caller search can give a false positive.** A name search can match an asset that holds the same
string for a different thing, for example an audio event path `/Game/Audio/Events/<SameName>`. When a
hit matters, confirm it: sweep the candidate packages for the 16-byte `MemberGuid` of the symbol, not
its name. A name can collide; a guid does not.

## Worked example

A 5-node function that the editor cannot read:

```
python "{{OCTOPUS}}/packs/blueprint/tools/t3d_graphs.py" --project "{{PROJECT}}" T3D/TextBP_ExampleActor-4.txt --graph ResetState
```

```
K2Node_FunctionEntry_0   FunctionEntry      then -> K2Node_VariableSet_14.execute
K2Node_CallFunction_12   call ExampleLibrary_C::GetManager [pure]      OUT Manager -> ...
K2Node_CallFunction_16   call self::CurrentItem [pure]                  OUT NewParam -> ...
K2Node_VariableGet_135   get currentSlot
K2Node_VariableSet_14    SET activeSlot   in activeSlot = -1
```

This reads as `GetManager().CurrentItem().currentSlot.activeSlot = -1`.

## Scope discipline

This is a **read** route. It writes only the T3D directory, the backup directory and `Saved/`. It
never changes `Content/`, never saves an asset, and needs no editor mutation. So it is also the safe
way to analyse a Blueprint while another agent works in the same editor. See the `shared-editor` rule.
