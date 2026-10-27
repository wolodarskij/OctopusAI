# AngelScript for Unreal Engine

*Applies to `.as` files under `Script/`. Takes precedence over the rules of the `unreal` pack
wherever they differ. The facts below come from the Hazelight AngelScript fork of the engine.
Verify a line number against the fork version in use before you rely on it.*

## Official documentation

| Resource | URL |
|---|---|
| Main docs | https://angelscript.hazelight.se/ |
| API reference | https://angelscript.hazelight.se/api |
| Functions and events | https://angelscript.hazelight.se/scripting/functions-and-events/ |
| Properties and accessors | https://angelscript.hazelight.se/scripting/properties-and-accessors/ |
| Actors and components | https://angelscript.hazelight.se/scripting/actors-components/ |
| Function libraries | https://angelscript.hazelight.se/scripting/function-libraries/ |
| Differences with C++ | https://angelscript.hazelight.se/scripting/cpp-differences/ |
| Examples | https://github.com/Hazelight/UnrealEngine-Angelscript/tree/angelscript-master/Script-Examples/Examples |

## The language

Prefix conventions and the `UPROPERTY` / `UFUNCTION` specifiers are the same as in C++. These
keywords are AngelScript's own:

| Keyword | Meaning |
|---|---|
| `UFUNCTION(BlueprintEvent)` | Overridable in Blueprints. An empty body is a pure event; a body is a default implementation. |
| `UFUNCTION(BlueprintOverride)` | Overrides a C++ `BlueprintImplementableEvent` or `BlueprintNativeEvent`. |
| `UPROPERTY(DefaultComponent)` | Creates and attaches a component subobject. `Attach = ParentName` sets the hierarchy. |

| Reference syntax | C++ equivalent |
|---|---|
| `Type &out param` | `Type& param` |
| `Type &in param` | `const Type&` |

A free function at global scope needs no `UCLASS()` wrapper. The generator puts it in
`Module_<File>Statics`. The engine discovers `.as` files anywhere under `Script/` at startup and on
hot reload.

**A global function still needs `UFUNCTION` to reach Blueprint.** Without it the function compiles,
hot reloads and has no node.

A script class registers as `/Script/Angelscript.<ClassName>`, whatever the file location, and with
no type prefix. The class default object is `/Script/Angelscript.Default__<ClassName>`.

```angelscript
if (Actor.HasMatchingGameplayTag(GameplayTags::Characters_IsJumping))   // generated from the INI

TSoftObjectPtr<UStaticMesh> MeshRef;
UStaticMesh Mesh = Cast<UStaticMesh>(System::LoadAsset_Blocking(MeshRef));
```

AngelScript hot reloads on save. C++ needs a full build. **Hot reload is global**: a save of one
`.as` file recompiles the whole script layer for every user of the editor.

Reach a subsystem through its generated `::Get()` accessor, or through
`SubsystemLibrary::GetGameInstanceSubsystem(instance, UMySubsystem)`.

## Two tokens that fail at preprocess time and stop the whole layer

A preprocess failure reports *"Hot reload failed… Keeping all old script code"* for the whole
layer. The layer stays on the last good build until the file on disk is valid.

**1. A reserved word as an identifier.** Never name a variable, parameter, member or function
`local`, `class`, `auto` or `char`. Rename it: `localPlayer`, `bLocal`, `charTint`, `bAuto`.

**2. `UPROPERTY(BlueprintAssignable)`.** It is a hard error in this fork. Use a bare `UPROPERTY()`
on an `event` that you declare at global scope. That form is Blueprint-assignable:

```angelscript
event void FStatsChangedSignature();      // GLOBAL scope, outside any class

class AMyActor : AActor
{
    UPROPERTY()                           // bare. NOT BlueprintAssignable.
    FStatsChangedSignature OnStatsChanged;
}
```

## Do not guard a structural object against null

**A null check on an object that always exists hides the defect that it must show.** Write the
dereference. When the object is null, the game stops on the incorrect line and the log names it. A
guard changes that into a silent early return, and the caller reads the type default as an answer.

| The object | Guard it? |
|---|---|
| the owner, a component of this actor, the game mode, the game state, a subsystem, a data asset that the actor resolved once | **no**: dereference it |
| a thing that the game spawns and destroys while it runs: a projectile, a pickup, a widget that opens and closes | yes |
| a lookup whose contract includes "not found": a table row, a map find, a `Cast` to a narrower type | yes: the null is the answer of the lookup |

**A guard that reproduces an authored Blueprint branch is behaviour, and the port keeps it.**

**A state test is not a null guard.** `if (!GetMyGameInstance().bGameRunning) return;` dereferences
a structural object and tests a flag on it. That is correct, and it keeps the poll of a game widget
quiet before a game starts.

## Array access does not mean what it meant in the Blueprint

Blueprint array nodes accept an index out of range. The AngelScript `[]` operator does not.

| Operation | Blueprint | AngelScript |
|---|---|---|
| indexed **write**, `Array_Set` with `bSizeToFit=true` | **grows** the array with defaults | asserts |
| indexed **write**, `bSizeToFit=false` | does nothing | asserts |
| indexed **read** past the end | returns the **type default** and logs a warning | asserts |

The authored node decides which port is correct. `python "{{OCTOPUS}}/packs/blueprint/tools/t3d_node_chain.py"`
prints `bSizeToFit` on every `Array_Set` of a function in an export.

- If `bSizeToFit` was `true`, pad the array up to the index before the write.
- If it was `false`, guard and skip.
- For a read, substitute the type default. Do not return early: an early return is a different
  answer, because the comparison in the Blueprint still ran on that default.

## Name it well

**Pick the most accurate name.** A symbol whose callers all migrate does not need the authored
name.

| The symbol | Name it |
|---|---|
| script-only, or every caller migrates | the most **accurate** name |
| a live Blueprint call node must keep its binding now | the authored FName |

**"Better" means more accurate, never more general.** A general name removes the domain fact that
the precise name carried. `IsLeapYear` tells the reader the rule; `CheckDateRule` does not. When the
authored name is already precise, keep it.

**`NewParam`, `NewParam1`, `b`, `A` and `A2` are engine defaults for unnamed pins.** Keep one only to
keep a live serialised call node bound. Replace it when that caller is ported.

**A character that AngelScript cannot spell never blocks a port.** For a space, a leading digit, a
dot or a dash, rename the symbol and continue. Two mechanisms keep the old spelling for a live
caller:

- `meta=(DisplayName="Fast Forward")` gives the node its old title.
- A `+FunctionRedirects=` entry makes existing call nodes resolve, because a call node stores the
  real FName.

**Do not resolve a name collision that the authored name caused.** Rename your symbol. A compile
error in a child Blueprint is cheap: declare the parent symbol, let the child report the error, and
remove the copy of the child.

## Redirects

The `[CoreRedirects]` INI form is in the rules of the `unreal` pack. Three facts are specific to
AngelScript:

1. **An AngelScript class path has NO type prefix.** The class registers as
   `/Script/Angelscript.MyComponent`, never `.UMyComponent`. A redirect with the prefix fails with a
   log line and no error.
2. **When the old name was on the Blueprint class, redirect from that owner**:
   `OldName="/Game/Blueprints/BP_ExampleActor.BP_ExampleActor_C.Play Sound"`. When more than one
   owner used a name, add a redirect for each.
3. **Land the real body and the redirect first. Remove the declaration from the Blueprint last.**
   `FMemberReference::FindRemappedFieldImpl` returns before it reads `FCoreRedirects` while the
   Blueprint still declares the old name, so the redirect has no effect until then. It becomes
   active at once when the declaration goes. **Never point a redirect at an empty stub**: the
   redirect then removes the feature without a message.

**You cannot verify a redirect without an editor restart**, because
`FCoreRedirects::ReadRedirectsFromIni` runs once, at process start. Report such work as "expected,
not verified".

## Overridability

Only `BlueprintEvent` and `BlueprintOverride` set `FUNC_BlueprintEvent`.

| The parent declares | Can a child Blueprint override it? |
|---|---|
| `UFUNCTION(BlueprintCallable)` | **No**: a child override is a hard compile error |
| `UFUNCTION(BlueprintPure)` | **No**: the same |
| `UFUNCTION(BlueprintEvent)` | yes |

**So a function that the parent declares `BlueprintCallable` can never be overridden.** Make one of
two decisions:

- The child override goes away: keep the parent `BlueprintCallable`, give it a real body, then
  remove the override of the child. The removal clears the error.
- The child override must stay: use `BlueprintEvent`. It **clears callable**, so write
  `BlueprintEvent, BlueprintCallable` when you need both. Give a function that returns a value a
  default that fails closed.

**A custom EVENT in a child Blueprint with the name of a parent function is always a hard error**:
*"@@ name conflicts with a native '<Class>' function"*. It can never become an override, and
`BlueprintEvent` does not help. Remove the event of the child first, with
`python "{{OCTOPUS}}/packs/blueprint/tools/remove_bp_event.py"`.

**A function GRAPH in a child Blueprint with a matching signature is a legal override of a parent
`BlueprintEvent`**, and it may call `Parent: <Name>`. A child graph that duplicates the parent body
reports *"The function name in node X is already used"*. Remove that graph to clear the error.

## Purity

**When the authored function was pure, the port is `BlueprintPure`.** Measure it. Do not assume
it. `BlueprintEvent` clears callable, so a pure function that a child can override is
`UFUNCTION(BlueprintEvent, BlueprintPure)`.

**Find purity from the CALL SITES: a `K2Node_CallFunction` with no `execute` and no `then` pin is
pure.** Read `ExtraFlags & 0x10000000` on the `K2Node_FunctionEntry` only when no caller is left.
The engine keeps a `then` pin on the entry node of a pure function too, so the pin test is
incorrect there. An interface implementation has no `ExtraFlags`; its purity comes from the
interface.

**`bIsPureFunc` is never in a T3D export.**

**A pure function declared impure is the destructive error.** It gets exec pins that nothing
drives, it returns the type default, and it compiles with no error.

**The name of an output pin binds callers.** A script function that *returns* a value makes a pin
named `ReturnValue`. To keep the authored pin name, declare an out parameter:
`void GetLanguage(FString&out lang)`.

## When an unreflected member is NOT the answer

An unreflected member avoids a name collision. When that choice is incorrect, a loud collision
becomes a silent deletion. The test is: **does anything in Blueprint use the member by name?**

| Case | Unreflected? |
|---|---|
| nothing in Blueprint reads it | **yes** |
| the property IS a spawn pin (`meta=(ExposeOnSpawn=true)`) | **no**: the pin *is* the `UProperty` |
| Blueprint call nodes bind to it by name | **no**: they lose their target without a message |

## DataTables

**`FindRow` compares row structs BY POINTER.** A native struct and a Blueprint
`UserDefinedStruct` are different objects. The comparison never succeeds, and **`FindRow` returns
`false`** with no error, no warning and no log line.

**Script cannot read a table whose rows are a `UserDefinedStruct`** until an asset with native rows
replaces it. Choose one option, and state the choice in the port:

| Option | Cost |
|---|---|
| put the rules in script | **the table stops being data**: a designer who edits it no longer changes the game |
| make a DataTable asset with native rows | a new asset and a data migration in `Content/` |

**Never name a field of a row struct `name`.** `FDataTableExporterJSON` and the importer skip each
column whose authored name matches the key field `"Name"`, in **both** directions and with no
regard to case. You can then neither write nor read the column, and no error or warning shows. On a
native struct the compare uses the real property name, so `meta=(DisplayName=...)` does not change
the result. Rename the property: `displayedName`, `itemName`.

**"The column reads empty" is not "the column is empty".**

## Localised text

**An FText ported from a Blueprint must drop the `[<package id>]` namespace.** A Blueprint text
literal stores its namespace as the localisation id of the package, in square brackets. The form
`NSLOCTEXT("[...]", key, source)` compiles and resolves in the editor, and **loses every translation
in a cooked build**. `GetDisplayString` removes the bracketed namespace before the lookup only under
`USE_STABLE_LOCALIZATION_KEYS`, which is `WITH_EDITORONLY_DATA`: 1 in the editor, 0 in a cooked
build.

**Write `NSLOCTEXT("", "<KEY>", "<source>")`.** Keep the key exactly. Never keep the brackets.

No check finds this defect: there is no error, no warning, and the text is correct in the editor.

### A text that a player reads is a member of the class, never a literal in a function

```angelscript
UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Text", meta = (MultiLine = true))
FText txtNotEnoughGold = NSLOCTEXT("", "0A1B2C3D4E5F60718293A4B5C6D7E8F9", "Not enough gold");
```

The member form gives three things:

- a designer edits the line in the Details panel;
- the localisation gather reads a Blueprint override of the member from the asset;
- the text stays when the function that used it is removed.

**The exception is a text whose key the code selects at run time.** `FromStringTable(table,
usable ? "a" : "b")` reads a local and cannot be a member initialiser.

    python "{{OCTOPUS}}/packs/ue-angelscript/tools/expose_texts.py" report   # must be zero for each file that declares a class

**A `FromStringTable` key must exist in the string table ASSET**, not only in the localisation
manifest. The two can differ, and a key that is only in the manifest shows the placeholder for a
missing entry on screen.

    python "{{OCTOPUS}}/packs/blueprint/tools/blueprint_export.py" stringtables --project "{{PROJECT}}"
    python "{{OCTOPUS}}/packs/ue-angelscript/tools/check_string_table_keys.py"   # reads stringtables.json

## A widget animation is not a duplicate

An animation and a `UPROPERTY(Transient, BindWidgetAnim)` of the same name make the binding: the
property finds the authored animation by name. **Never remove an animation, and never port one.** A
`UWidgetAnimation` is authored timeline data. Script binds an animation and plays it, and does
nothing else with it. `list_variables` does not report animations, so a name that looks like a
duplicate and is not in that list is an animation.

Play it with `PlayAnimation(anim, start, loops, mode, speed, restore)`. Stop it with
`StopAnimation(anim)`.

## Counting callers

**The reliable test is `FunctionReference(MemberName="X")` on a `K2Node_CallFunction`, minus the
entry and result nodes of the graph itself.** Those nodes carry the same `MemberName` and add two to
a simple count. A grep of the whole file is incorrect in both directions.

**"Zero callers" is not "unused".** Before you delete a symbol because the count is zero, name what
the count cannot see:

- a `GetComponentByClass` at run time;
- the extra matches of a substring search;
- a delegate binding through `K2Node_AddDelegate`, which makes no call node.

**A zero is a statement about the instrument, never about the code.**

## Hot reload and deletion

- A `.as` save reinstances every script-derived class, along reference edges. Grep the log for
  `Assertion failed`, `appError` and `Fatal`. A count of zero `MoveCDOToNewClass` lines is not a
  crash check.
- *"Keeping all old script code"* is a stop of the layer, not data loss. Fix the file that the
  **first** error names, then read the log again.
- **Never delete a `.as` file while a Blueprint still names the class that it declares.** The
  reinstancer resolves the parent of each Blueprint by name and asserts on a child that it cannot
  reparent (`BlueprintCompilationManager.cpp`). Reparent every child Blueprint first.
