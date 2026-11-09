---
name: move-bp-function-to-parent
description: Move Blueprint functions up into an AngelScript or C++ parent class and reparent the Blueprint onto it, without breaking a single call site. Use when asked to "move these functions to the parent", "reimplement this Blueprint in AngelScript", "make the BP a shim over a script class", "push this class down into .as", or when replacing an empty/stub Blueprint with a real native implementation. Covers Blueprint member FUNCTIONS and reparenting; for member variables use move-bp-variable-to-parent, for enum-typed things use migrate-bp-enum-to-parent.
---

# Moving Blueprint functions to a script parent

The procedure is automatic. Never ask the user to rewire anything by hand.

## Rule 0: migrate only the functions the user named

The rule is the same as in [`move-bp-variable-to-parent`](../move-bp-variable-to-parent/SKILL.md).
The set of functions is exactly the set that the user named. A compile error that existed before is a
**finding to report**. Record it as a baseline and compare against it; do not fix it.

The removal of Blueprint functions is a deletion in a binary asset. **Get explicit confirmation
first**, if the project requires it.

## The two facts that make this non-obvious

**1. A parent function with the same name is a hard compile error.** When you reparent a Blueprint
whose functions match functions of the new parent, you get these errors for each function:

```
The function name in node  X  is already used
Access specifier is not compatible the parent function  X
Cannot override '::X' at  X  which was declared in a parent with a different signature
```

So you cannot reparent and keep the functions. They must come out.

**2. Pin names carry the connections, and this is where work is lost silently.**
`K2Node_CallFunction` stores an `FMemberReference` that resolves by **name** up the class hierarchy.
So a call site resolves to the parent function automatically when the child function is gone. But when
the node is rebuilt, **the new pins match the old pins by name**. A pin whose name changed becomes
orphaned, and its connection is dropped. The Blueprint still compiles.

The trap is the return value. A Blueprint function declares the name of its output pin on its
`K2Node_FunctionResult`, for example `vol`. A script function that *returns* a value makes a pin with
the name **`ReturnValue`**. The two names do not match.

**Fix: declare an out-parameter**, because an out-parameter lets you name the pin:

```angelscript
// Blueprint had:  GetVolume() -> vol : Float
UFUNCTION(BlueprintPure, Category = "Example|Settings")
void GetVolume(float&out vol)    // pin is `vol`, not `ReturnValue`
{
    vol = Volume;
}
```

A `BlueprintPure` function with only out-parameters makes exactly the node shape of the original.

**Also:** if an out-parameter name is the same as a member variable that you add, rename the
**member**, not the parameter. The parameter name is a contract with every call site. The member name
is private.

## A Blueprint name with a space cannot move this way

This fails, and at first it looks like success. `Get Health` and `Can be Damaged` cannot become
AngelScript identifiers. A `+FunctionRedirects` entry that maps the name with spaces onto
`GetHealth` / `CanBeDamaged` **does bind**: the editor renames the call nodes and does not leave
them unresolved. But the rebuilt node **loses its self pin**:

```
In use pin  Target  no longer exists on node  GetHealth
```

When the names match exactly, you need **no redirect**. The `FMemberReference` resolves up the
hierarchy by itself, and every pin survives, `Target` also. The redirect causes a rebuild, and the
rebuild loses the pin.

**So: keep functions with spaces in their names out of every batch.** The route that should work is:
rename the Blueprint function *first*, so that the editor fixes its own call sites, then move it under
the matching name with the normal procedure. MCP `BlueprintTools` has no `rename_function`, so the
rename needs the editor UI. Look for names with spaces before you start, and also names with two
spaces in a row.

## Procedure

### 0. Back up, and check PIE

Run `AssetTools.save_assets` on the target. Then copy the `.uasset` to
`../<Project>Backups/MigrationBackups/` with a timestamp. Make sure that PIE is stopped
(`LogsToolset.GetLogEntries(pattern="UEDPIE|EndPlayMap")`). During PIE, asset resolution goes through
the PIE world, and every step below reports wrong results.

With a VCS, also make sure that the asset is committed and clean.

### 1. Read the exact signatures from the Blueprint - do not trust docs

Make the signatures **exactly** the same, parameter names and pin names included. Read them from the
entry and result nodes, not from a DSL dump or a docs page:

```python
for g in ['FuncA', 'FuncB', ...]:
    nodes = find_nodes(graph={'refPath': BP + ':' + g}, title='')
    for i in get_node_infos(nodes=nodes):
        # K2Node_FunctionEntry.output_pins  -> the parameters
        # K2Node_FunctionResult.input_pins  -> the return values, WITH THEIR NAMES
```

Also record purity. A call node with no exec pin calls a pure function. **A pure function must stay
pure**. A change of purity changes the node shape and breaks every call site.

### 2. Write the script class, then verify the hot reload

Match every signature. Then look for these lines in the log:

```
Angelscript: Compiling (structural): <Path>.<ClassName>
Angelscript: Full Reload: <ClassName>
```

Make sure that the CDO reads the new properties at **`/Script/Angelscript.Default__<ClassName>`**. The
path without `Default__` always fails to read properties. Never use it to decide that the reload
failed.

### 3. Record the call-site baseline before anything changes

This baseline is the verification. Sweep every graph of every asset that refers to the class. For each
matching node, record the graph, the node name, the `type_id`, and **every pin name with its
connection count**.

```python
hits.append({'graph': g, 'node': n, 'type_id': tid,
             'in':  [[p['name'], len(p['connected_pins'])] for p in i['input_pins']],
             'out': [[p['name'], len(p['connected_pins'])] for p in i['output_pins']]})
```

**Be careful with a substring match on `type_id`.** `SetVolume` matches `SetVolumeInMenu`, which is
a *different function on a different class*. Remove such false hits explicitly, and count what stays.

### 4. Prove the end state on a throwaway duplicate first

Run `AssetTools.duplicate` on the Blueprint to `ZZ_TEST_<something>`. Reparent the duplicate, remove its
functions, and compile. This takes approximately one minute. It tells you whether the end state is
possible **before** you change an asset that other assets refer to. Delete the duplicate after.

`get_referencers` returns `None` (not `[]`) on a new duplicate that was never saved. Do not iterate
over it.

### 5. Reparent, then remove immediately

**The order is the opposite of the variable case.** For a variable, you add the parent property under a
*different* name and delete the child last. You cannot rename a function, so:

1. `set_parent` -> the new class. The call **succeeds** alone and returns `null`. For a short time the
   Blueprint cannot compile.
2. `remove_function_graph` for each function, in the **next call**.
3. Compile.

Reparent first, not remove first: then the child functions stay resolvable during the whole window,
and a call site never points at nothing. The window is two tool calls with nothing else between them.

**Expect many compile errors between step 1 and step 2**: the name errors above, and type errors on
each pin whose class changed. They are temporary. Judge only a compile *after* step 2.

- **The temporary errors can come from assets that refer to the class, not only from the child.** A
  referencer can report `Can't connect pins ... is not compatible with ...` until the function graphs
  are gone. Take the compile after the removals against a new log marker. If not, you read the
  temporary errors again and think that the change failed.
- **Run `list_variables` on the child before the reparent.** A child variable whose name the script
  parent declares is the same hard error as a function with the same name.

### 6. Verify

- **Compile every referencing asset again with `warnings_as_errors: true`.** A plain compile is not
  sufficient: orphaned pins and broken bindings are *warnings*.
- **Do the step-3 sweep again and compare.** Every node must have the same pin names and the **same
  connection counts**. This check finds a wire that was silently dropped.
- **`type_id` must be different now.** It gets the `Category` of the script function, for example
  `|GetVolume` becomes `Example|Settings|GetVolume`. That change proves that the call now binds to the
  parent, and not to a leftover.
- `list_variables` and `list_functions` of the Blueprint both return empty.
- `get_referencers` still lists every original referencer.
- `save_assets`. If the project keeps a change log, add one line to it.
- Behaviour check in PIE, when the user asks for a PIE run. A static check cannot see a wrong value at
  runtime.

An asset that refers to the class only through pin types, with zero call sites, needs no change.
Look for this before you decide that a referencer needs work.

## Recovery

Do **not** `save_assets` until the step-6 comparison passes. Until then, the `.uasset` on disk is the
last good state, and a close of the editor without a save restores it. After the save, the step-0
backup and the VCS are the nets, in that order: the backup does not depend on the state of the VCS.

**Do not record a run as proven until the assets are saved and you checked the state on disk again.**
An editor session that was not saved goes back to the disk state on close, and all observations from
memory go with it.
