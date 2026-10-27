# Blueprint to native migration

*Applies to a project that moves Blueprint logic into a native parent, C++ or AngelScript. Where this
rule conflicts with another rule or with the judgement of the agent, this rule wins.*

In this rule, `<tools>` is `{{OCTOPUS}}/packs/blueprint/tools`, and each tool runs as
`python "<tools>/<tool>.py" --project "{{PROJECT}}" ...`. `<t3dDir>` is the directory that `t3dDir` in
`{{PROJECT}}/.octopus/blueprint.json` names (default `T3D`).

## Only the user stops the migration loop

When the migration runs on a schedule, no agent cancels, pauses or lets that schedule lapse on its
own judgement: not because the context is low, not because the current item is blocked, not because
the tree is broken. If a session ends: compact and continue. If the schedule is missing at the start
of a task, create it again and say so.

## No caller is not a reason to delete

A symbol with no caller is much more often a caller that is not ported yet than a leftover. The
default action is investigation, never removal:

1. Read the legacy record for that symbol **and for the similar functions on sibling classes**.
2. Read the **oldest** export of that class in `<t3dDir>`.
3. Report what the symbol is for, what its neighbours do, and what refers to any of them. When the
   record does not say, write "I do not know what it does".

To retire a symbol, you need the order of the user for that symbol.

## Never restore what was removed, and never repair outside your task

A deletion is a decision. To undo it is a new decision, and it belongs to the user. Never restore a
file, a function, a member or a line because it is missing. A removal that causes no defect is not a
defect. An unexplained change belongs to the user: ask about it. A wish to repair something outside
the current task is a question, never an action. A verification tool reports; it never authorises a
write. Do not overwrite a file that another agent finished. Stop a running agent only when the user
asks, when the editor is dying, or when the agent writes outside its claim.

## Never stop for unimportant things

Debug print colours, log formats, pin names that fail only because of a language keyword, cosmetic
values that a tool cannot read: make the obvious choice, write one line next to it, and continue.
**Test:** if a wrong choice cannot change gameplay, saved data or multiplayer sync, it is not worth a
stop. Stop only for these: something that silently corrupts saved data, desynchronises multiplayer,
or destroys localisation.

## When something is blocked, migrate the blocker

If an asset blocks the migration, migrate that asset too. If you cannot migrate a blocker, put in an
empty stub and continue. A stub that is documented as a stub is progress.

**A stub that loses behaviour carries a `TODO(<tag>)` in the source file**, and the marker names the
cost in plain words: "the door does not glow on hover", never "`SetGlowBrush` is a seam". Write one
marker for each separate thing that is lost. A seam that costs nothing needs no marker.

## When a migration becomes difficult, FIRST ask whether the thing must survive

When a migration becomes difficult, read the architecture documents of the project before you spend
more time on fidelity. Does this function, member or class exist in the target architecture?

| Answer | What to do |
|---|---|
| No, it will be removed | replace it with a stub immediately; do not keep its behaviour |
| Yes | migrate it correctly |
| Unclear | use the stub, write the uncertainty in one line, continue |

The effort must match the future of the thing. A perfect migration of something that will be deleted
is the most frequent way for a loop to lose time. A stub for such a symbol has a one-line comment that
names its replacement.

**Serialization and save-game marshalling functions** are a family that the target architecture
often replaces completely. Decide one time per project whether they are stubs. When they are, keep
the signature and the purity. Purity is part of the shape of the call node: an impure stub of a pure
function makes the compiler prune its call node, and the struct is then serialised empty.

## Porting a Blueprint: variables first

1. **Port every variable that the Blueprint declares**, in one pass, before you touch a function or
   an event. First read each authored default from the CDO (`<tools>/widget_designer_values.py` for
   widgets). A designer value is lost when the copy of the child is removed.
2. **Then remove every colliding variable from the child.** Two properties with one name never
   exchange a value. If you keep the copy of the child, the state is silently wrong.
3. **Then the functions and the events.**

**Never port a widget animation.** Bind an existing animation with `BindWidgetAnim` and play it. An
animation and a `BindWidgetAnim` property with the same name are how the bind works; they are not a
collision.

**Port into the existing native parent of the Blueprint.** Add a class in the middle only when two
Blueprints or more share the parent AND the members that you add belong to one of them.

**One asset at a time.** Finish it, then start the next.

**An event can stay in the Blueprint only when it is BOTH local AND compiles.** Functions and
variables have no exemption: they always move.

**Ignore macros.** `remove_function_graph` reports success and does nothing on a macro
(`<tools>/remove_bp_macro.py` explains why). A macro that stays in an emptied Blueprint costs nothing.

`list_variables` is the first call of any port.

## Every removal destroys nodes. No removal is safe.

`remove_variable` maps to `FBlueprintEditorUtils::RemoveMemberVariable`, which calls
`RemoveVariableNodes` and **deletes every Get/Set node for that variable in every graph**. It does not
look for an inherited property on the parent. The nodes never resolve again. The Blueprint still
compiles, because orphaned pins fall back to defaults, so the damage is silent. The removal also cuts
every data link and exec link of those nodes.

### Collisions

A **collision** is a variable that a native parent AND its child Blueprint both declare. The
Blueprint does not compile until the copy of the child goes. Find collisions by a comparison of
`list_variables` on the child with the properties of the parent, and classify each one:

| | declared type of the child | meaning |
|---|---|---|
| SAFE | the same as the type of the parent | the parent can serve the reads after they are rebuilt |
| DESTRUCTIVE | a `UserDefinedStruct`, or a Blueprint class (`..._C`) | the parent necessarily declares something else |

Remove both kinds. Both get a ledger entry (below); a DESTRUCTIVE one names the lost behaviour in
plain words. A comparison of container type, `CPF_Net` and `CPF_SaveGame` does not answer this
question, because the destructive ones differ in the object sub-category.

### The procedure, and the two mistakes that it prevents

    export (pre) -> record baseline -> DELETE the graphs dir -> remove -> SAVE -> export (post) -> diff

1. **Export AFTER the save.** The post export must show what the saved package holds; an export
   before the save records a state the disk does not hold. The pre export is usually already in `<t3dDir>`.
2. **Delete the per-graph JSON directory before the second export.** Old JSON makes the "after" set
   too large. `link_diff.py` compares the two exports.

### The route that keeps behaviour

Add the property to the parent under a DIFFERENT name, repoint the nodes of the child to it, and
delete the variable of the child LAST. This is impossible when the parent and the child share a
name. The full procedure is the `move-bp-variable-to-parent` skill.

## Breaking is allowed. An uncompilable asset is not. The only fix is a port.

A Blueprint compile problem has one solution: port the failing function or member to the native
layer, retarget its callers, and remove it from the Blueprint. Do not repair a Blueprint graph. You
must close a name conflict that you caused, in the same pass. Report a compile failure in an asset
outside your port in one line, and leave it.

### The ledger of removed behaviour is necessary

Every unsafe removal gets an entry in the ledger file of the project at the repository root. The
important field is **"what behaviour is now gone", in plain words**. "reads of `itemsStruct` were
deleted" is useless. "the player can no longer buy, sell or count items" is what the pass that
reimplements it needs. An unexplained node loss also goes in the ledger.

## There is no Blueprint work queue

The only Blueprint worklist is a short file that an agent writes while it edits a Blueprint now. It
has a ceiling of 100 lines, and the agent deletes an entry when the work is done. A queue that every
agent files into and no agent closes grows without limit.

| You have | It goes in |
|---|---|
| Blueprint work that you are about to do | the Blueprint worklist |
| a finding that nobody works on | the ticket list |
| a debt that you created in the port | a `TODO(<tag>)` in the file itself |
| what you changed | the change log |
| behaviour that you destroyed | the ledger of removed behaviour |
| what a legacy page got wrong | your report |

## Every tick of a scheduled loop, in this order

1. **Fan out.** Give independent work to subagents in parallel, each on **separate assets**.
2. Add the results of the agents from the previous ticks.
3. Find the collisions (above) and remove them.
4. When the state changed since the last tick, report it as ONE TABLE, the current state only.
   Post nothing when nothing changed.
5. Compact when the context passes approximately 700 KB.

Every dispatch carries: the exact symbols to touch and the exclusions by name; the paths that other
live agents hold; the MCP call shapes (`list_variables` returns strings; `list_functions` returns
dicts); the order export -> baseline -> wipe graphs dir -> mutate -> SAVE -> export -> diff; "never
restore a backup"; and "a break on the Blueprint side is acceptable, native code must compile".
