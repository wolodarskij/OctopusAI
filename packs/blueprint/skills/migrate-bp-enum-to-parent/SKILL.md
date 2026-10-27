---
name: migrate-bp-enum-to-parent
description: Replace a Blueprint UserDefinedEnum asset with a native AngelScript (or C++) enum, retyping every variable and pin that used it while keeping the underlying integer values compatible. Use when asked to "recreate this enum in .as", "make E_Foo a native enum", "get rid of the Blueprint enum", "migrate the enum to the parent class", or when a `move-bp-variable-to-parent` batch stalls because a variable's type is a Blueprint-defined enum that AngelScript cannot name. Covers the enum type swap itself; for plain variable moves whose type already exists natively, use move-bp-variable-to-parent instead.
---

# Replacing a Blueprint enum with a native one

This skill goes with `move-bp-variable-to-parent`. That skill moves a *variable* up to the parent
class, and it assumes that the **type** of the variable already exists natively. This skill handles
the case that the other one cannot: a Blueprint `UserDefinedEnum` asset gives the type of the
variable, and AngelScript cannot name that asset.

**Rule 0 of `move-bp-variable-to-parent` applies here: migrate only what the user asked for.** An
enum swap already goes further than one variable, because it changes a shared type. Do not let it
grow into other variables, other enums, or compile errors that are not related.

If the project keeps a data page for the enum (its values and referencers), **read it before step 1**.
It can answer the footprint question of step 1 without a binary scan.

An enum that only copies engine state (for example host / client / single player, which
`HasAuthority()` and `GetNetMode()` already answer) can need removal, not a port. That is a design
decision for the user, not for this skill.

## What a UserDefinedEnum contains

This fact makes the job difficult, and the editor UI does not show it.

A UserDefinedEnum does **not** store the names that you see. It stores:

| Index | Real `FName` in the `Names` array | What the editor shows |
|---|---|---|
| 0 | `E_Foo::NewEnumerator0` | the `DisplayNameMap` entry for index 0 |
| 1 | `E_Foo::NewEnumerator1` | the `DisplayNameMap` entry for index 1 |
| 2 | `E_Foo::E_MAX` | hidden, added automatically |

A rename of an entry in the enum editor changes only `DisplayNameMap`. The serialised internal name
never changes. So:

- Everything that compares the raw `FName` - CoreRedirect `ValueChanges`, pin default strings, saved
  data - sees the internal name, **not** `OrientToMovement`.
- The display names exist only as localisation entries with the key
  `<asset path>.DisplayNameMap(<n> - Value).DisplayNameMap`.

`ObjectTools.get_properties` **cannot read** `Names`, `DisplayNameMap`, `CppForm` or `EnumFlags`. It
gives an error on all four. Read them from the name table of the `.uasset`:

```python
import re
d = open(path, 'rb').read()
for s in re.findall(rb'[ -~]{4,}', d):
    print(s.decode('ascii', 'replace'))
```

Search that output for `DisplayNameMap` and the display names. The localisation keys
`DisplayNameMap(N - Value)` give the correct mapping from index to display name. The name table is
sorted as text, so never pair an internal name with an index by its position in that output.
`python "{{OCTOPUS}}/packs/blueprint/tools/enum_dump.py" --project "{{PROJECT}}" Content/<path>/E_Foo.uasset`
reads the same data and pairs each display name with its index.

## Step 1: find the real footprint before you choose a strategy

Use three independent sources, because each one is wrong in a different way:

1. **`AssetTools.get_referencers`** on the enum asset. It is correct, but **it shows only what is on
   disk**. It does not see edits in memory that are not saved. After you change a Blueprint, you
   must `save_assets` before `get_referencers` and `get_dependencies` report the new state.
2. **A binary scan of all content**, which finds what the registry missed:
   ```bash
   python "{{OCTOPUS}}/packs/blueprint/tools/search_bp_refs.py" --project "{{PROJECT}}" E_Foo
   ```
   Expect exactly the enum asset and its real referencers.
3. **A pin sweep of each referencing Blueprint.** For every node in every graph, examine the
   `type_id` of every input and output pin for the enum name. Only this finds switch nodes, literals
   and typed function parameters.

Do **not** use *Find in Blueprints* for this. Its index goes stale: it reports many "out-of-date"
Blueprints and shows hits in assets that do not refer to the type any more.

**Enum values are usually pin defaults, not literal nodes.** A `Set` node with a constant enum has no
wire. The value is in the `value` field of its value pin, as the internal name. A sweep that follows
only *connections* misses every constant in the graph. Record `value` on every enum pin, connected or
not.

## Step 2: declare the native enum with fixed values

Always give explicit indices, and give the reason in a comment. Serialised data must continue to
resolve.

```angelscript
// Replaces the Blueprint UserDefinedEnum /Game/Data/E_RotationMode.
// Values are pinned to the old asset's indices so serialised data stays compatible:
// OrientToMovement is its value 0 and Strafe is its value 1.
enum ERotationMode {
    OrientToMovement = 0,
    Strafe = 1
}
```

Use the naming convention of the project, not the name of the old asset. The redirect below keeps old
references resolving, so you do not have to keep a bad name.

Do not declare an `E_MAX`. The engine adds `_MAX` to native enums itself.

An AngelScript enum registers at **`/Script/Angelscript.<EnumName>`**, and at runtime it is itself a
`UserDefinedEnum` object. Confirm the path before you write a redirect. The editor console (`Cmd` box)
is the reliable route:

```
py import unreal; e=unreal.load_object(None,'/Script/Angelscript.ERotationMode'); print('ENUMPATH='+e.get_path_name())
```

Then read it back with `LogsToolset.GetLogEntries(pattern="ENUMPATH")`.

Console cautions: keep the command to **one short line**. A new line or a long string is cut in the
middle of a literal and fails with `SyntaxError: unterminated string literal`. Also,
`unreal.<EnumName>` does **not** exist for hot-reloaded AngelScript types, because the Python bindings
are generated at startup. Always use `load_object` with the path.

## Step 3: choose a strategy from the footprint

### Strategy A - direct node swap (no restart)

Use it when the whole footprint of the enum is inside the Blueprints that you edit, and **both ends of
every enum-typed wire are among the nodes that you replace**. This is usual for a small enum that is
used in one place.

This strategy is better: it completes in the session, needs no editor restart, and you set every value
explicitly.

1. Add the properties with the native type to the parent (see `move-bp-variable-to-parent`).
2. Create the replacement nodes at the old positions.
3. Move the **exec** links as usual. Exec pins have the same type.
4. Connect enum links only between two *new* nodes. You cannot connect an old `E_Foo` pin to a new
   `EFoo` pin: the types are different, and the connection fails. This is why both ends must be in
   the batch.
5. **Translate every enum pin default explicitly** with `set_pin_value`, from the old internal name to
   the new enumerator name (the plain name, for example `"Strafe"`).
6. Delete the old nodes, then `remove_variable` the child variables.

### Strategy B - CoreRedirect and restart

Use it when assets that you do not edit refer to the enum, or when the consumers include
`K2Node_SwitchEnum`, enum literal nodes, comparisons or typed function parameters - anything that you
cannot create again cleanly.

```ini
+EnumRedirects=(OldName="/Game/Data/E_Foo.E_Foo",NewName="/Script/Angelscript.EFoo",ValueChanges=(("E_Foo::NewEnumerator0","EFoo::First"),("NewEnumerator0","EFoo::First")))
```

Give both the qualified old name (`E_Foo::` and the internal name) and the bare internal name.
Different call sites store it differently.

A redirect resolves **at load time**, so the change shows only after the package loads again. Restart
the editor. Do **not** use `EditorLoadingAndSavingUtils.reload_packages` on a large Blueprint. It can
leave a `..._DEADPACKAGE` that the engine cannot purge (`failed to purge the old package ... still
externally referenced`), and that can make a later AngelScript hot reload crash the editor.

After the restart, **examine every translated pin default by hand**. If a `ValueChanges` entry does
not match, the value falls back to index 0 with no error. A `Strafe` literal becomes the first
enumerator, the Blueprint compiles clean, and the defect shows only at runtime. Fix each value that did
not translate with `set_pin_value`.

### What does not work

- `BlueprintTools.retarget_node_class` changes only **class** references (casts, function calls,
  events, delegates). It does nothing for enum-typed pins.
- `BlueprintTools.add_variable` cannot create an enum-typed variable. Its `type_name` accepts only
  primitives and some structs. The parent property must come from script or C++.

## Step 4: verify, then delete the old asset

In this order:

1. Every enum pin reports the new type (`EFoo Enum`, not `E_Foo Enum`).
2. Every literal reads the same value as before. Compare with the values that you recorded in step 1.
   **This check finds the silent fallback to index 0.**
3. The Blueprint compiles with no new entries in `LogBlueprint`. Compare with the baseline from
   before the change. Search for `Invalid field`, `could not be found`, `Internal Compiler Error`.
4. `save_assets`. The registry updates only after the save.
5. `get_referencers` on the old enum returns `[]`, and `get_dependencies` on the Blueprint does not
   list it. **Both become true only after the save.**
6. Copy the enum `.uasset` to a location outside the project, then `AssetTools.delete` it.
7. Keep the CoreRedirect also with Strategy A. It costs nothing, and it rescues a stale copy, an
   autosave or a duplicate asset that still has the old type.
8. Compile again and examine the log again after the delete.
9. If the project keeps a change log, add one line to it.

## Where the defaults live

If the user wants the enum defaults on the Blueprint and not on the parent class, declare the parent
property **with no initializer**. Write the value onto the Blueprint CDO with
`ObjectTools.set_properties`. Read it again after a compile to make sure that it stayed.

This has a limit. When the wanted default is the index-0 value of the enum, the value on the Blueprint
is the same as the implicit default of the parent, so nothing shows the difference. The guarantee then
depends on the parent property staying without an initializer. Say so; do not claim a stronger
separation.
