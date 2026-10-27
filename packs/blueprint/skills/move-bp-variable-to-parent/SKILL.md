---
name: move-bp-variable-to-parent
description: Move one or more variables out of a Blueprint and up into its AngelScript or C++ parent class without destroying graph wiring. Use when asked to "move this variable to the parent", "push X up to the base class", "promote a variable to UMyComponent", "this should live in the .as class instead of the BP", or when de-duplicating a variable that several child Blueprints each redefine. Applies to Blueprint member variables only - not local function variables, not components, not functions.
---

# Moving a Blueprint variable to its parent class

The procedure is automatic. Never ask the user to rename or rewire anything by hand in the editor.

## Rule 0: migrate only the variables the user named

**The set of variables to migrate is exactly the set that the user named.** "All booleans" means the
booleans and nothing else. A named list means that list. Do not make the batch larger for any reason:

- **Not** because another variable looks half-migrated, orphaned or misnamed.
- **Not** because a variable of another type is in the same category, graph or name family.
- **Not** because a *different* variable causes a compile error that blocks your verification. A
  compile error that existed before is a finding to **report**. Record the existing errors as a
  baseline, migrate your variables, and compare the new errors with that baseline. If the baseline
  makes verification impossible, stop and ask. Do not repair it without an instruction.
- **Not** because it "is obviously next", or because the previous session intended it.

A change to a variable that nobody asked for is a scope violation, also when the edit succeeds. An
AnimBP can have more than 250 graphs, and a bad edit there costs the user work that the user did not
agree to risk. If you find something out of scope, finish the batch, list the item in the report, and
let the user decide.

## The two rules that make this non-obvious

**1. Never start with `remove_variable`.** It maps to `FBlueprintEditorUtils::RemoveMemberVariable`,
which calls `RemoveVariableNodes` and **deletes every Get/Set node for that variable in every graph**.
The Blueprint still compiles clean after that, because orphaned pins fall back to defaults. There is no
error. Without a backup, you cannot recover the nodes.

**2. A parent property with the same name breaks the child.** If you add one while the child variable
still exists, compilation fails with `Internal Compiler Error: Tried to create a property X in scope
<BP>_C, but another object already exists there.`

So the order is always: **add to the parent under a different name -> repoint the nodes -> delete the
child last.**

## Prerequisite: the toolset patch

`create_node` and `find_node_types` must work inside collapsed graphs and animation graphs. Examine
this before anything else. Pick an anim state graph and try:

```python
create_node(graph=<...AnimStateNode_N.SomeState>, type_id="Variables|<Cat>|GetSomething", pos=...)
```

If it raises `Cannot cast type 'K2Node_Composite' / 'AnimStateNode' / 'AnimGraphNode_X' to
'Blueprint'`, the patch is missing. The file is `<EngineRoot>/Engine/Plugins/Experimental/Toolsets/
EditorToolset/Content/Python/editor_toolset/toolsets/blueprint.py`. The patch writes outside the
project, so the ticket needs the `outside` permit; without it, write the permit on the ticket, move
the ticket to InProgress `waiting`, and stop. The patch:

- **Outer walk.** `unreal.Blueprint.cast()` *raises* on a mismatch and does not return `None`. The
  direct outer of a graph is the Blueprint only for top-level graphs. Add a helper and use it at every
  `unreal.Blueprint.cast(graph.get_outer())` site. It does the same as `FindBlueprintForGraph` on the
  C++ side, which is why `delete_node` and `connect_pins` always worked there.

  ```python
  def _find_blueprint(obj):
      while obj is not None:
          if isinstance(obj, unreal.Blueprint):
              return obj
          obj = obj.get_outer()
      return None
  ```

- **Cache fingerprint.** In `_BlueprintCache._fingerprint`, `list_member_variable_names(blueprint,
  False)` excludes inherited members. A property added to the *parent* then never invalidates the
  cache, and `find_node_types` keeps a stale list. Pass `True`. Do not change the `False` in the
  `list_variables` tool: that tool must return only the Blueprint's own variables.

**Find out which engine runs** before you edit: `Get-Process UnrealEditor | Select Path`. The patch
edits engine files, and other projects on the same machine can use that engine. If the patch is
already present, do not apply it again.

**Reload without a restart.** Type into the status-bar console (`Snapshot` the main window, find the
`Cmd` text box, `Type` with `submit=true`):

```
py import toolset_registry, editor_toolset; toolset_registry.reload_module(editor_toolset)
```

Look for `Re-instancing BlueprintTools after reload` in the log.

## Naming the parent property

The parent name must be different from the child name, or rule 2 applies. Follow the naming rules of
the project: `b` for booleans, PascalCase for the other types. Give a descriptive new name
(`Velocity` -> `CurrentVelocity`, `CharacterMovement` -> `MovementComponent`). Always give an explicit
`Category`, and use the category that the child variable had.

**Add a `[CoreRedirects]` entry** in the same change as the rename. A moved and renamed variable is a
rename:

```ini
+PropertyRedirects=(OldName="/Game/Blueprints/Foo.Foo_C.OldName",NewName="/Script/Angelscript.ParentClass.NewName")
```

With the redirect, an old save or an unmigrated sibling Blueprint resolves the property. Without it,
they silently read a default.

A type id is **not** the raw name. The first letter is capitalised (`fPlayRate` -> `GetFPlayRate`),
spaces in the category are removed (`Speed Targets` -> `SpeedTargets`), and the leading `b` of a
boolean is removed (`bHasVelocity` -> `GetHasVelocity`). Read ids from `find_node_types`. Never
compose them by hand.

The two views do not agree. `find_node_types` reports the **display** name, and `get_node_infos`
reports the **raw** name in `type_id`. A sweep compares against `get_node_infos`. `create_node` takes
the `find_node_types` form.

## Procedure

### 0. Back up the asset first - every batch

Take a restore point *before* each migration batch, not only at the start of the session. There are
two steps, because a file copy captures only what is on disk:

1. `AssetTools.save_assets` on the target Blueprint, so that the disk matches memory.
2. Copy the `.uasset` to a file with a timestamp outside the project (Unreal must not see it as an
   asset):

```
Copy-Item "Content/<path>/<Asset>.uasset" `
          "../<Project>Backups/MigrationBackups/<Asset>_<YYYYMMDD-HHmmss>.uasset"
```

Give the backup path to the user in the batch report.

**To restore:** the editor must not hold the package open. Close it, copy the backup over
`Content/<path>/<Asset>.uasset`, and open it again. A copy under a running editor does nothing,
because the version in memory wins and overwrites the file on the next save.

The backup protects against the frequent failure: damage that compiles clean and shows only as a
warning or at runtime. Without it, the only protection is "not saved yet", and that stops when you
save a good batch and start the next one.

### 1. Survey

Record the type, category and CDO default of each variable. The `title` filter of `find_nodes` does
not help here: the compact title of a setter is `SET`. Filter by node class, and compare the
`type_id` from `get_node_infos` **exactly** after you remove `Get`/`Set`. A substring match confuses
`Velocity` with `Velocity_LastFrame`.

Sweep all graphs in one `ProgrammaticToolset.execute_tool_script` call.

### 2. Add the properties to the parent

Always write a comment that names the **original Blueprint variable**. Later, the parent property is
the only record of it, and after the rename the old name is lost. You need the old name to read old
commits, bug reports, or an unmigrated sibling Blueprint.

```angelscript
// Migrated from ABP_ExampleCharacter Blueprint variable "HasVelocity"
UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Animation|Movement")
bool bHasVelocity;
```

Put the comment **above** the `UPROPERTY` line, not between it and the declaration.

Copy the default of the child. It is lost when you delete the variable.

Make the type of the parent property match the pin type of the child. Take the type name from the
existing source files of the project. Do not invent a spelling.

**Compare the defaults of every type, not only of scalars.** Object references, structs, enums, maps
and arrays can also carry values that are not the default (a data asset reference, a populated `TMap`,
an enum value that is not the first). Get the child CDO for the *whole* batch and compare it with a
new parent value. A lost object-reference default shows only at runtime, as
`Accessed None trying to read property <X>`.

## Invisible references: property bindings

`K2Node_VariableGet`/`Set` are not the only references to a variable. An animation node can bind a
pin to a property **by path**. The anim node stores the binding; there is no graph node. Your sweep
cannot see these bindings, and a rename breaks them:

```
Invalid field 'Trajectory' found in property path for  Pose History
```

Two results:

- A variable with **zero** Get/Set nodes is not necessarily unused. A binding can use it. Do not
  migrate such a variable without a check. Ask the user, or leave it.
- For a bound variable, the parent property **must keep the exact name of the child**, because the
  binding resolves by name. Delete the child first, which frees the name. Then rename the parent
  property to match and repoint its nodes.

Only the compile log shows these. After each batch, search `LogBlueprint` for `Invalid field` and
`could not be found`. A broken binding is a *warning*, not an error, so a "successful" compile hides
it.

Then verify, in this order, before you change a node:

- `Full Reload: <ClassName>` in the `Angelscript` log. A reload can come minutes later when the
  editor is in the background. Change the timestamp of the file to start the watcher again.
- The CDO reads the new properties at `/Script/Angelscript.Default__<Class>`. The class path without
  `Default__` *always* fails to read properties. Never use it to decide that the reload failed.
- Every new property is in `find_node_types(graph, "Variables|<Category>|")`.

For a **C++** parent, run `LiveCodingToolset.CompileLiveCoding` and wait for its result.

### 3. Migrate, one variable at a time

There is no retarget in place. `ObjectTools.list_properties` on a `K2Node_VariableGet` returns only
`errorMsg`, so you cannot reach `VariableReference`. A DSL round trip is also wrong: it drops positions
and comments, and it returns empty for `AnimGraph`. So: **create the replacement at the same
position, move every link, delete the original.**

Three rules:

- **Read the wiring again immediately before you change each node.** Never sweep one time and then
  mutate from that snapshot. The migration of a node deletes it and moves links. That makes the
  recorded wiring of other nodes wrong, also of nodes of a *different* variable. A reconnection from
  stale data points exec pins at deleted nodes and leaves `execute` inputs empty. The result is
  `Dependency cycle detected, preventing node <X> from being scheduled`, or a silently cut exec chain.
- **Skip foreign nodes.** A node with an input pin named `self` reads the property of the same name
  from a *target object* (`UMovementComponent::Velocity`, `ACharacter::CharacterMovement`), not the
  member of the Blueprint. If you repoint it, you make a silent defect that compiles clean.
  `RemoveVariableNodes` uses `bForSelfOnly=true`, so the child delete does not touch these nodes. A
  sweep after the migration still finds the old name exactly that number of times.
- **Compile after every variable.** Then a cycle names the variable that caused it.

The pin mapping is by position. The old node and the new node are the same node class for the same
value type, so `input_pins`/`output_pins` align index by index. Assert that the count and the
`type_id` match. That assertion catches foreign nodes and unexpected types.

- **Copy the literals of unwired input pins, not only the connections.** A Set node whose value input
  has no wire keeps its value as the pin *default* (`value` in `get_node_infos`). If you move only
  `connected_pins`, the new node gets the default of the type: `Set MovementMode = InAir` becomes
  `Set MovementMode = OnGround`. It compiles clean and fails only at runtime. For every input pin
  with no connection whose `value` is different from the new pin, copy it with `set_pin_value` and
  read it back with `get_pin_value`. An enum literal needs a translation when the enum type changed
  (see `migrate-bp-enum-to-parent`).

Unreal allows one link on a data input and on an exec output. So a connection to the new node
**takes** those links from the old node. Before the delete, the pins of the old node must be empty,
with two harmless exceptions: a data output that feeds it still lists it, and the `then` of the old
setter still reaches the next node (an exec input accepts many links). Both clear on delete.

### 4. Delete the child, with a guard

Delete it only when the own-context node count of that variable is **0**, and the count of the new
property equals the original count. Make the script stop, not delete, on any difference.

### 5. Verify and finish

- The node count of each parent property equals the original count of the child, exactly.
- The child names are not in `list_variables`. The Blueprint CDO reads the inherited values.
- `LogBlueprint` shows no new warnings. A `null` return from `compile_blueprint` is not evidence:
  compare with a compile from before the change.
- `AssetTools.save_assets`. If the project keeps a change log, add one line to it.
- Behaviour check: when the user asks for a PIE run, use the feature. A static check cannot see a
  wrong value that goes through the running game.

## The working loop

```python
def locate(name):
    """Fresh sweep -> ([(graph, node_ref)] self-context, foreign_count)."""
    own, foreign = [], 0
    for g in graphs():
        nodes = []
        for cls in (GET_CLS, SET_CLS):
            nodes.extend(bp("find_nodes", graph=g, title="", node_class=cls))
        for i in (bp("get_node_infos", nodes=nodes) if nodes else []):
            nm, _ = vname(i["type_id"])          # strip Get/Set, exact compare
            if nm != name:
                continue
            if any(p["name"] == "self" for p in i["input_pins"]):
                foreign += 1
            else:
                own.append((g, i["node"]))
    return own, foreign

def migrate_one(graph, node_ref, get_tid, set_tid):
    info = bp("get_node_infos", nodes=[node_ref])[0]      # re-read NOW
    _, is_set = vname(info["type_id"])
    new = bp("create_node", graph=graph,
             type_id=(set_tid if is_set else get_tid), pos=info["position"])
    ni = bp("get_node_infos", nodes=[new])[0]
    for key in ("input_pins", "output_pins"):
        old_pins, new_pins = info[key], ni[key]
        if len(old_pins) != len(new_pins):
            raise RuntimeError(f"pin count mismatch {info['type_id']}")
        for op, np in zip(old_pins, new_pins):
            if op["type_id"] != np["type_id"]:
                raise RuntimeError(f"pin type mismatch {op['type_id']}")
            for link in op["connected_pins"]:
                if key == "output_pins":
                    bp("connect_pins", output_pin=np["pin_id"], input_pin=link)
                else:
                    bp("connect_pins", output_pin=link, input_pin=np["pin_id"])
    bp("delete_node", node=info["node"])

# per variable: locate -> migrate each -> re-locate -> counts must match -> remove_variable -> compile
```

Put approximately 4 variables in one `execute_tool_script` call. A full sweep of a large AnimBP is
hundreds of tool calls, and too many variables in one call can time out.

## Resuming after the Blueprint was restored

A restore of the Blueprint from a backup or an autosave rolls back **only the asset**. The `.as` or
`.h` file does not change, so the parent properties from the rolled-back batches still exist. The two
sides are now different: the Blueprint has child variables that the parent already duplicates.

**Do not add the parent properties again.** A second declaration is a compile error or a silent
duplicate. Reconcile:

1. Read the parent file and make the set of the properties that exist. The comments that name the
   original variable make this exact: map `child name -> existing parent property` with no guess.
2. For each child variable that is still in the restored Blueprint, find out whether its parent
   property exists.
   - **It exists** -> skip step 2. Go to the node migration with the existing property, and get its
     type id from `find_node_types`.
   - **It is missing** -> declare it as usual.
3. Then do the migration and the guarded delete.

Before you start, make sure that the parent class did not change: each existing property must still
have the type and default that you expect. The restore can go back further than you think. A restored
asset can also be *older* than the last good batch. Compare the size and timestamp of the backup with
the autosave list; do not assume that it is the most recent state.

## Recovery

**A VCS is a safety net**, but it does not replace these steps:

- Make sure that the target `.uasset` is **committed and clean** before a batch starts. A revert
  gives back only the last commit, and `Content/` is binary: there is no partial recovery, no merge,
  no diff.
- Still take the step-0 file backup. It is instant, it survives a wrong revert, and it does not depend
  on the state of the VCS.

The second safety net costs nothing: **do not `save_assets` until the verification passes.** The
`.uasset` on disk stays at its last saved state, so if you close the editor and discard the changes,
the asset comes back. Read `LastWriteTime` on the `.uasset` to make sure that the disk copy is older
than the damage. Tell the user immediately, and let the user discard. Do not patch a graph whose
original wiring you cannot read any more.

`Saved/Autosaves/` is the weakest net. It is a ring buffer of 10 files that overwrites itself in
approximately 100 minutes. When you find damage, it usually holds only snapshots from after the damage.

## What to exclude

Do not move a variable whose type is a Blueprint class (`BP_ExampleActor_C`). AngelScript cannot name
a Blueprint class. A retype to the script parent narrows the pin and breaks access to members that
only the Blueprint declares.
