# The shared editor

*Always applies when more than one agent drives one `UnrealEditor.exe` over MCP. Where this rule
conflicts with another rule about editor work, this rule wins.*

You are never the only writer. Another agent can edit assets, source files, config and `CLAUDE.md` in
the same minute as you. The failure is the silent destruction of finished work of somebody else, and
the undo stack of the editor cannot recover it.

## The shared editor is the dangerous part

All agents drive the **same** editor process over MCP.

- Asset state is **in memory and global**. When two agents change one Blueprint, their edits mix into
  one broken result. There is no sandbox per agent.
- `AssetTools.save_assets` writes whatever is dirty for that asset **now**, also the half-finished
  edit of another agent.
- A script hot reload (for example AngelScript) or a Live Coding patch (C++) is **global**. A syntax
  error that you leave on disk breaks the compile for every other agent.
- Play-In-Editor is global. While PIE runs, packages resolve under a `UEDPIE_0_` prefix, and
  `get_dependencies`, `exists` and `get_asset_class` fail for assets that exist.
- Undo is one shared stack. Never use it to revert your own work.

## Claim before you mutate

Keep a claim board, one append-only markdown file (for example `CLAIMS.md`) that every agent reads.

**Before the first mutating call of a task**, read the board, then append your claim. Mutating means:
`create_node`, `delete_node`, `connect_pins`, `set_pin_value`, `add_variable`, `remove_variable`,
`set_parent`, `set_properties`, `compile_blueprint`, `save_assets`, `write_graph_dsl`, and every
`Write` or `Edit` to `Source/`, `Script/`, `Config/`, `Content/` or `CLAUDE.md`.

```markdown
## T0001 — ACTIVE — started <YYYY-MM-DD HH:MM>
**Session:** <session id>
**Doing:** one line, plain language.
**Exclusive paths:**
- Content/Blueprints/BP_Door.uasset
- Source/MyGame/DoorBase.h
**Shared resources:** save_assets
```

The heading is the ticket id, or three or four words that name the paths. The `Session` field is the
identity: another agent uses it to find the holder of a path. Append a `DONE` block when you stop.
Never edit an old block.

**Read the board by POSITION, never by timestamp.** The board is append-only, so the current state of
an agent is its **last block in the file**. Each agent writes its own timestamps, and the clocks of
different sessions can differ by tens of minutes. An old `ACTIVE` block can have a later stamp than
the `DONE` that released it.

**Never reap the claim of another agent.** A dead agent's `ACTIVE` block continues to hold its paths.
If you think a claim is dead, ask on the message board and do other work. Saves, text exports and
change-log lines prove that an agent is alive.

## Exclusive ownership

**Never mutate a path that another agent holds ACTIVE.** Not to fix one node, not because it looks
finished, not because the change looks wrong. Ask, and do other work. **Nobody waits for anybody.**

Ownership is by **path**, and it includes everything that the path brings with it:

- A claim on a Blueprint claims **all of its graphs and nodes**, and its CDO.
- A claim on a source file claims **every class declared in it**, and every Blueprint parented to
  those classes: a property added to a parent changes the compile of the children.
- A claim on `Config/DefaultEngine.ini` claims all of it.

A lock names a resource, not a relationship. Two agents can hold separate paths and still collide
through the class hierarchy, the reinstancer or a shared recompile (see "Reloads" below).

## Rules that also apply to paths you own

- **Save after EVERY modification.** One mutation, one save. This limits the damage when the editor
  dies in the middle of a pass. Do not put many Blueprint mutations behind one save.
- **Never blanket-save.** Do not call `save_assets` with an empty list, or with paths that you did not
  claim.
- **Never save an asset that you did not claim**, also when the editor shows it dirty. Dirty means that
  somebody is in the middle of an edit.
- **Never compile the Blueprint of another agent, and never call `list_graphs` on it.** Both show the
  errors of their work in progress as if they were yours. `list_graphs` starts a compile on a
  Blueprint that is out of date, so it is a mutation. Verify from disk.
- **Never run PIE, and never run a test scenario, unless the user asks for it in that message.**
  Before editor work, look for PIE:
  `EditorToolset.LogsToolset.GetLogEntries(category="", pattern="UEDPIE|EndPlayMap", maxEntries=6)`. A `UEDPIE_0_` line with no later `EndPlayMap` means that PIE runs. When the user
  asks for a run, claim the shared resource `pie` first. The one exception is a Tester ticket
  (`repeat: test`): it holds `pie` and `editor` on every run, and its brief is the request.
- **Never kill or restart a LIVE editor** without a claim on `editor` and a message. That discards the
  unsaved work in memory of every other agent.
- **Restart a DEAD editor at once** when the ticket holds `editor` or `pie`. Nothing is in memory
  to lose. Make sure that no `UnrealEditor.exe` runs, then launch it with the
  `restart-unreal-editor` skill. Without either permit, write `editor` on the ticket's `permits:`
  line, move the ticket to InProgress `waiting`, and stop.
- **Never launch a second editor.** Two editors that write one `Content/` is damage that you cannot
  recover. A claim is not proof; read the process list.
- **Keep every source file compiling at all times.** Write the whole valid declaration in one `Write`
  or `Edit`. Never leave a half-typed class on disk between calls.
- **Do not reformat, reorder or tidy a file that you only visit.** A reflow makes the edit of every
  other agent conflict.

**A signature change is ONE save.** When you change a declaration, every call site that you own goes
into the SAME save. A script layer compiles as a whole and hot reload is global, so a callee saved
without its updated caller stops **every agent**. It fails safe: the last good build continues to run.
Fix the first error and read again: one missing `/*` makes many errors that follow from it. Look at
the file that the error names before you believe that you caused it.

## Talk to each other

Keep a message board, one append-only markdown file (for example `MESSAGES.md`). **Do not send a
message that you can work without.** The default is silence.

| Send | Do not send |
|---|---|
| you need a path that somebody else holds | that you started or finished ordinary work |
| you think that a claim is dead | a summary of what you did; that goes in the change log |
| you found damage that somebody else caused | findings; those go in the ticket list |
| you are about to restart a live editor or start PIE | acknowledgements, thanks, status, plans |

**One message, one fact.** No introduction, no closing line.

```markdown
### <YYYY-MM-DD HH:MM> — <agent-id> → <target agent-id or "all">
<what they must know, in one or two sentences. Name exact paths.>
```

**Read the message board at the start of every task and after every long operation.**

**Append to a shared board with a quoted heredoc**, `cat >> file << 'EOF'` … `EOF`. The quoted
delimiter stops substitution, so backticks, `$` and `!` stay as they are. **Never rewrite any part of a
shared board**, also not "only my own section": a rewrite from your heading to the end of the file
deletes a block that another agent appended in between. To change your status, append a **new**
block. **Never write the text of another agent again from memory.** Leave the hole, and post a damage
notice. If you are not sure whether a release survived, treat the path as held.

## Reloads reach the whole project

A script hot reload reinstances **every script-derived class in the project**, and the wave travels
along references: a class with zero Blueprint children still reloads every class that refers to it.
The Blueprint that kills the editor does not have to be yours, and it does not have to be related to
the file that you edited.

A reload lock does not help: two agents that edit different files do not cause the crash. The save
rule above and batches limit the damage:

1. **Batch.** Put every change to a file in ONE write, and batch across files when you can. Ten files
   in one write is one compile and one reinstance; ten separate edits are ten.
2. **A signature change is ONE save.**
3. **Before the save, ask two questions.** Is the file in the block of another agent? Does another
   agent hold a Blueprint CHILD of the class that will reload, or a child of a class that REFERS to
   it? If a child is held: post a message, name the child and its holder, and do other work.

`ObjectTools.search_subclasses` takes `base_class` as an OBJECT and a `class_name` string:
`{"base_class": {"refPath": "/Script/Angelscript.Foo"}, "class_name": ""}`. A bare path string fails
the schema and returns the permissive answer, "no children". The results include the base class, so
a class with no children returns a list of length 1.

**Never predict the cost of a reload from the type of the edit. Read the log.** A change to a comment
only can cause a full reinstance. The `Compiling (...)` line is not the result; the `Full Reload:` and
`Soft Reload:` lines are. Search the log for `Assertion failed`, `appError` and `Fatal`.

## Read the shared log carefully

`EditorToolset.LogsToolset.GetLogEntries` returns the log of the **whole editor**, with the compiles and errors of
other agents. Before you decide that an error comes from your work:

1. Look at the **asset path** in the message. A path that you did not claim is not yours.
2. Compare the **timestamp** with the time of your call. Automatic compiles run during an edit and
   make temporary errors that a later compile clears.
3. Never "fix" an error in an asset that you do not own.

Read only the window of the live log that your own operation produced. An old log line tells what
occurred at its time, and nothing about now.

## What a zero proves

A packed-FName hit does NOT prove that a property still exists. An FName is stored in the name table
of the package, and the entry stays after the property that used it is removed.

| Question | Tool |
|---|---|
| does this property exist on this Blueprint? | `list_variables` / `list_functions` on the live asset (correct) |
| does anything in `Content/` refer to this string? | a packed-FName scan: `python "{{OCTOPUS}}/packs/blueprint/tools/search_bp_refs.py" <name> --exact` |
| does a substring appear? | the same without `--exact`; it finds too much |

A good tool used on a question that it does not answer gives a confident wrong result. Doubt a
correction as much as the claim that it corrects.

## Files that several agents append to

- the change log: one line per batch, `YYYY-MM-DD | <agent-id> | path1, path2 | short goal`;
- the claim board and the message board: append your own blocks only;
- the ticket list: findings only.

Read the end of the file again before you append. A full rewrite from an old copy drops the line of
another agent. **Keep every shared file below 100 KB.** When a file is larger, move its old text to an
archive file next to it. Never delete it, and always keep an `ACTIVE` claim with nothing after it.

## Backups and scratch

**Never put a `.uasset` copy anywhere inside the project.** Not in `Content/`, and not in `Saved/`.

The engine mounts `Saved/` as the package root `/Temp/`, so a `.uasset` under it is a real package
that other packages can load. A package saved while such a copy is loaded records THAT path as its
dependency, and the reference stays after the file is deleted. No redirect fixes it, because a
`FunctionRedirect` rebinds a name and never rewrites the importing package.

- Put asset restore points in a directory next to the project, for example
  `../<Project>Backups/MigrationBackups/<Asset>_<YYYYMMDD-HHmmss>[-<tag>].uasset`. The `blueprint` pack tools use
  the backup directory that `.octopus/blueprint.json` names.
- Keep T3D text exports outside `Saved/`, in the directory that `t3dDir` of `.octopus/blueprint.json` names
  (default `T3D/`).
- Use your own temporary directory for scripts that you throw away.

Take a restore point before every destructive batch, and give its path in your report. When several
agents work, an unsaved edit is no protection: the `save_assets` of another agent can write your
changes in memory to disk at any moment.

## When the tree changed under you

Assume that it did. If a message came, a claim that you care about changed, a long operation just
finished, or somebody else writes a file that you read before, **read it again before you write**.

Node identity is especially unsafe: Unreal **uses freed node names again**, so a `K2Node_*` refPath
that you recorded before a deletion can resolve to a different node later. Find a node again by
identity (graph, variable, position), never by a refPath that you remember.

## Verbs that damage the editor or the data

- **Never call `set_pin_value` on an orphaned pin.** It asserts on `OwningNode` and kills the editor.
  The K2 schema does not accept a new connection *from* an orphaned pin, and when you break the last
  link of an orphaned pin, the pin is deleted. When the orphan only holds an authored literal, copy
  the value onto the LIVE pin.
- **Never call `ToolsetLibrary.UndoTransaction` on the shared editor.** It takes widgets out of the
  tree in memory. Every widget map entry that stays then looks orphaned, and the next compile removes
  the GUIDs of widgets that exist. A save makes it permanent. Revert from a restore point.
- **`set_pin_value` and `retarget_node_class` do not make the package dirty**, also when they succeed.
  `save_assets` then returns `true` and writes nothing. Verify by mtime. To save one, make one
  graph edit that makes the package dirty (`connect_pins`, or a `set_node_position` of one unit), then
  compile and save.
- **`delete_node` returns success for a refPath that resolves to nothing.** `connect_pins` returns
  `null` for a silent no-op, the same as for a success. `create_node` on a variable Get/Set returns an
  unbound node with no pins unless you pass `declaring_class`. Verify by a new read.
- **`DataTableTools.set_rows` destroys data and reports success.** `get_rows` returns a `TMap` value
  wrapped (`{"key": {"<field>_Value": ...}}`); `set_rows` wants it bare. The wrapped form does
  nothing, an unknown wrapper empties the whole map, and `set_rows` replaces the rows; it does not
  merge. It also does not call `FillParentTags()` on an `FGameplayTagContainer` column, so write
  `ParentTags` explicitly. **Always read the table back after a write.**
- **Never delete a script source file while a Blueprint still names a class that it declares.** The
  reinstancer resolves the parent of every Blueprint by name and asserts on a child that it cannot
  reparent. Reparent every child first, save, then delete the file.

## Reporting

**A premise that does not survive your measurement stops the task.** Say so with the evidence. Do not
complete the work as written, and do not work around it silently.

**When two agents disagree, the NARROWER claim wins.** Compare what each agent measured. Do not decide
by recency.

**A refusal must name what works TODAY that it protects.** A dangling `VariableGet` gives the type
default; a refusal to repoint it keeps an error message, not a behaviour.

**A blocker names one route, not a wall.** A recorded blocker is a statement about one route that
somebody tried on one day. Test the premise: is that route the only route, and is the measurement
still current?

**Say which unit you count.** Error LINES are one per compiler message (a broken pin in a macro is
reported one time per inlining). Error ENTRIES are one per endpoint node. LINKS or SITES are the real
work, and only they predict effort.
