# Unreal Engine 5 — Agent Reference

*Always applies. Covers: naming, UPROPERTY/UFUNCTION, Gameplay Tags, Blueprints, Data Assets,
collision, Enhanced Input, redirects. When the project uses AngelScript, its AngelScript rules take
precedence for `.as` files wherever they differ.*

## Project Structure

| Directory | Contents |
|---|---|
| `Config/` | INI configuration files |
| `Content/` | Unreal assets (`.uasset`, `.umap`); binary, never text-editable |
| `Source/` | C++ source code |
| `Script/` | script source, when the project uses a script layer (for example AngelScript) |
| `Plugins/` | Engine and in-house plugins |

`Binaries/`, `Intermediate/`, `DerivedDataCache/` and `Saved/` are generated. Do not edit them.
`Saved/` is mounted as the package root `/Temp/`, so a `.uasset` under it is a real package (see
`shared-editor.md`). The `blueprint` pack writes T3D text exports to the directory that `t3dDir` in
`.octopus/blueprint.json` names (default `T3D/`), outside `Saved/`.

## Configuration Files

| File | Purpose |
|---|---|
| `DefaultEngine.ini` | Core engine: renderer, RHI, physics, collision, navigation, plugins, `[CoreRedirects]` |
| `DefaultGame.ini` | Project metadata, maps to cook, packaging settings |
| `DefaultInput.ini` | Input mappings |
| `DefaultGameplayTags.ini` | Gameplay tag definitions |
| `DefaultEditor.ini` | Editor-only settings |

INI syntax: `+Key=Value` appends to an array · `Key=Value` sets (replacing the whole array for that
key) · `-Key=Value` removes an element · `!Key=ClearArray` clears the array.

## Naming Conventions

| Prefix | Type |
|---|---|
| `A` | Actor |
| `U` | UObject / component |
| `F` | Struct |
| `E` | Enum |
| `I` | Interface |
| `T` | Template class |

Booleans are prefixed with `b`.

## UPROPERTY and UFUNCTION

| `UPROPERTY` specifier | Effect |
|---|---|
| `EditAnywhere` | Editable on instances and class defaults |
| `EditDefaultsOnly` | Editable only on class defaults (CDO) |
| `EditInstanceOnly` | Editable only on placed instances |
| `BlueprintReadWrite` / `BlueprintReadOnly` | Blueprint access |
| `Transient` | Not serialized |
| `SaveGame` | Serialized by save systems that walk `CPF_SaveGame` |
| `Replicated` / `ReplicatedUsing=OnRep_Func` | Replicated, optionally with a notification callback |

| `UFUNCTION` specifier | Effect |
|---|---|
| `BlueprintCallable` | Callable from Blueprints |
| `BlueprintPure` | No execution pin, no side effects |
| `BlueprintImplementableEvent` | Declared in C++, implemented in Blueprint only |
| `BlueprintNativeEvent` | C++ provides `_Implementation`; Blueprint may override |
| `NetMulticast` / `Server` / `Client` | RPC target |
| `Reliable` / `Unreliable` | RPC reliability |

## Gameplay Tags

Defined in `Config/DefaultGameplayTags.ini`. Hierarchical (`Parent.Child.Leaf`). Query with
`HasMatchingGameplayTag()`, `HasAllMatchingGameplayTags()`, `HasAnyMatchingGameplayTags()`. C++:
`FGameplayTag::RequestGameplayTag(FName("Parent.Child"))`. AngelScript, when used: the generated
`GameplayTags::` namespace.

**The `GameplayTagsToolset` MCP tools rewrite `DefaultGameplayTags.ini`** and strip hand-written
`DevComment` values; `RenameTag` leaves CDOs on the old name and writes a redirect. Read the file
back after any tag write.

## Enhanced Input

| Class | Role |
|---|---|
| `UInputAction` | A single logical input |
| `UInputMappingContext` | Maps physical keys/axes to Input Actions |
| `UEnhancedInputComponent` | Binds Input Actions to handler functions |
| `UEnhancedInputLocalPlayerSubsystem` | Manages active mapping contexts per player |

A project that still uses legacy Axis/Action mappings runs them through `EnhancedPlayerInput`
passthrough; check `DefaultInput.ini` before you assume either model.

## Blueprints

- Binary `.uasset` files in `Content/`; never edit as text. Read them by the ladder in
  `reading-blueprints-cheapest-route.md`, when the project has the `blueprint` pack.
- Prefer native code (C++, or the script layer when the project has one) for logic; Blueprints for
  data configuration and designer tweaks.

## Data Assets and References

| Type | Use |
|---|---|
| `TObjectPtr<T>` / `T*` | Hard ref; loads with the owning object |
| `TSoftObjectPtr<T>` | Soft ref; stores a path, load explicitly |
| `TSoftClassPtr<T>` | Soft class ref; for deferred spawning |
| `FSoftObjectPath` | Raw string path |

`TSoftObjectPtr` does not load automatically.

## Collision

Object types (`ECollisionChannel`), trace channels (custom `ECC_GameTraceChannel1`–`18`, named in
`DefaultEngine.ini`), and profiles (named presets). Check `DefaultEngine.ini` for the project's
custom channels before you add one.

## Renaming (Core Redirects)

When renaming a class, property, function, struct or enum, add a redirect to `DefaultEngine.ini` in
the same change:

```ini
[CoreRedirects]
+ClassRedirects=(OldName="/Script/MyGame.OldClassName",NewName="/Script/MyGame.NewClassName")
+PropertyRedirects=(OldName="/Script/MyGame.ClassName.OldPropName",NewName="/Script/MyGame.ClassName.NewPropName")
+FunctionRedirects=(OldName="/Script/MyGame.ClassName.OldFuncName",NewName="/Script/MyGame.ClassName.NewFuncName")
+StructRedirects=(OldName="/Script/MyGame.OldStructName",NewName="/Script/MyGame.NewStructName")
+EnumRedirects=(OldName="/Script/MyGame.OldEnumName",NewName="/Script/MyGame.NewEnumName")
```

Never remove old redirects. Three facts that decide whether a redirect works:

1. **A redirect is inert while the Blueprint still declares the old name.**
   `FMemberReference::FindRemappedFieldImpl` returns before `FCoreRedirects` is consulted when the
   field is found on the scope or its superclasses. Land the declaration and the redirect FIRST;
   remove the Blueprint graph SECOND. The reverse order arms the redirect onto whatever the native
   side declares, including an empty stub.
2. **A redirect cannot be verified without an editor restart.** `FCoreRedirects::ReadRedirectsFromIni`
   runs once at process start. Report such work as EXPECTED, NOT VERIFIED.
3. **A `PropertyRedirect` rebinds a node; it cannot carry a PIN.** A node in another asset that
   read the renamed member keeps a pin whose property no longer exists. After the child-side
   removal, sweep every other asset that read the old name.

**A CoreRedirect cannot retarget a Blueprint `UserDefinedStruct`.** Retire a Blueprint struct by
removing its consumers, never by redirecting it. A `UserDefinedEnum` can be redirected
(the `migrate-bp-enum-to-parent` skill of the `blueprint` pack), and its serialised names are `NewEnumeratorN`, never the
display names.

## Common Gotchas

- `BeginPlay()` may fire before all components are initialized; use `PostInitializeComponents()`
  for cross-component setup.
- `GetWorld()` returns `nullptr` in constructors.
- Use `NewObject<T>()` for UObjects, `GetWorld()->SpawnActor<T>()` for actors; never `new`.
- `FindRow` on a DataTable compares row structs BY POINTER. A native struct and a Blueprint
  `UserDefinedStruct` are different objects, so a native reader of a Blueprint-rowed table gets
  `false` silently.
- A DataTable row-struct field called `name` (any case) is skipped by the JSON exporter and
  importer in both directions. Never name a row-struct field `name`.
- An `FText` literal transcribed from a Blueprint carries a `[<package id>]` namespace. Write
  `NSLOCTEXT("", "<KEY>", "<source>")`; the bracketed namespace resolves in the editor and loses
  every translation once cooked.

## `ExposeOnSpawn` is metadata, and specifier checks miss it

A property with `meta=(ExposeOnSpawn=true)` (in a T3D export:
`MetaDataArray=((DataKey="ExposeOnSpawn",DataValue="true"))`) gives each `SpawnActorFromClass` node of
that class a live spawn pin. A check that compares name, container type, `CPF_Net` and `CPF_SaveGame`
does not see metadata, so a property can match on all four and still carry it.

A spawn pin **is** the property. There is nothing to repoint to, and a rename with a redirect removes
the pin with the declaration. **Before you remove or rename a Blueprint member, look in its
`MetaDataArray` for `ExposeOnSpawn`. If it is there, find the spawn nodes first.**

## `create_node` resolves a struct node to the `UserDefinedStruct` when names collide

A native struct and a Blueprint `UserDefinedStruct` with the same name share one `create_node`
`type_id` (for example `Utilities|Struct|BreakMyStruct`):

- `find_node_types` returns that string **two times**, one per struct, with nothing to tell them apart.
- `create_node` always resolves it to the `UserDefinedStruct`. No parameter selects the native one.
- `declaring_class` does not accept a `UScriptStruct`, and `retarget_node_class` handles no struct
  nodes.

So there is no MCP route to build a Break or Make node for a native struct whose name a
`UserDefinedStruct` also uses. A duplicate `type_id` from `find_node_types` is the sign. Expect the
wrong node, pins that do not match, and dropped connections.
