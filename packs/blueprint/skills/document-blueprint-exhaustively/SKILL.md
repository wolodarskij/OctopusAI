---
name: document-blueprint-exhaustively
description: Write a complete per-symbol documentation set for one Blueprint - every function, event, macro and dispatcher, each with pseudocode and evidence-backed callers - by fanning subagents out over a worklist. Use when the ask is "document this actor and this actor only, fully", "no function should be abandoned", "document every function and event", or when an existing class directory is known to be partial or written from read_graph_dsl. Pairs with read-blueprint-without-editor, which supplies the graph data this consumes.
---

# Documenting one Blueprint exhaustively

The skill `read-blueprint-without-editor` gets the graphs. This skill turns 100 or more graphs into
100 or more written entries. The quality must not drop halfway, and you do not read every node
yourself.

## The shape of the job

**One entry per function graph. One entry per event.** Not one per graph: a large event graph that
holds 20 events is 20 entries, because one description of it is useless. This division also makes
shared nodes manageable (see the pin rule).

Make a worklist before you write anything. `doc_entries.py` takes any target. It derives entries from
the graph dumps and tracks `pending | in-progress | done`. It also records, for each entry, **the
markdown file that holds its heading**.

```
python "{{OCTOPUS}}/packs/blueprint/tools/doc_entries.py" --project "{{PROJECT}}" <Class> build --graphs Saved/<X>Graphs --docs Docs/Classes/<Class>
python "{{OCTOPUS}}/packs/blueprint/tools/doc_entries.py" --project "{{PROJECT}}" <Class> status           progress, per page
python "{{OCTOPUS}}/packs/blueprint/tools/doc_entries.py" --project "{{PROJECT}}" <Class> next-per-page    one claimable entry per target page
python "{{OCTOPUS}}/packs/blueprint/tools/doc_entries.py" --project "{{PROJECT}}" <Class> claim NAME
python "{{OCTOPUS}}/packs/blueprint/tools/doc_entries.py" --project "{{PROJECT}}" <Class> done NAME
python "{{OCTOPUS}}/packs/blueprint/tools/doc_entries.py" --project "{{PROJECT}}" <Class> coverage         graphs with no done entry - the gate
```

`build` keeps status, claim, note and page when you run it again, so it is safe after a new export.

Pick and close entries only through the worklist. Then "did I already do this?" is a mechanical check,
not a judgement.

## Fan out by page, never by entry

Entries on the same page append to the same file, so they must stay **serial**. Different pages are
different files, so they run **at the same time**. That is the whole parallel rule, and
`next-per-page` enforces it: it skips each page that has work in progress.

Eight pages give up to eight agents at the same time. When only one page is left, give its last
entries to one agent. Do the same when several entries share one graph, because the island measurement
(below) is one piece of work, not three.

### When the whole class is one page, write fragments

A small class has one `functions.md`, so "one agent per page" becomes fully serial. Send each agent to
a **fragment** under `Saved/<X>_entries/NN-topic.md` (headings and `---` separators only, no front
matter). Then assemble the page yourself in one write. This gives no conflict, full parallel work, and
you keep control of the order, the section introductions and the header.

When you assemble, **examine the metadata lines mechanically**: every entry has a
`**Kind** … · **Status** … · **Tags** …` line, and every status is in the taxonomy. A bad line from
one agent is not visible until the validator runs much later.

**The page field must be true.** If the heading of an entry is in `setup.md` but the worklist says
`players.md`, `next-per-page` gives the same file to two agents. Audit it one time: for every existing
`### \`symbol\`` heading, compare the file that holds it with the page in the worklist.

## Look for the `done` that did not run

A claim whose agent reported but whose `done` did not run stops its page for ever. Look for it in
every wave: list the in-progress entries with their claim time and **compare with `date -u`**. Do not
guess the current time. Make sure that `done` printed `-> done`.

## The standing subagent prompt

Put everything below into every agent prompt. It is long, and that is correct: the difference between
a good entry and a useless one is mostly in what the prompt forbids.

### Tone - this is first

**Describe what the graph does. Do not argue that something is wrong.** No severity, no defect
language, no proposed fixes. Empty pins, unwired branches, return values that nothing reads, returns
with no value, old pin defaults under live links, a body with no implementation, a call through an
accessor while the reference says `bSelfContext=True`: all of these are normal, and you document them
plainly.

If the code will be rewritten, a list of its faults is effort on something that goes away, and it
hides the description that a reader needs. Record dead code as a **neutral count** ("N of M nodes
cannot be reached from the entry"), because a reader must know which part of the graph is live.
`**Caveat**` is for what a reader needs to understand behaviour, never for a complaint.

Write a name that contains a space without the space in prose. Add no comment about it.

### Source of truth and forbidden tools

The only source is the per-graph JSON. **Forbid `read_graph_dsl`, the live editor and every editor
MCP tool** explicitly. If you do not, agents go back to them and bring back the errors that the dump
route avoids. If the JSON cannot answer a question, say so in the text; that is not a reason to open
the editor.

### The pin rule, for events

Document **only what the entry node of that event reaches**. Document a shared node one time per
event, for that chain. **Never merge** it into one description that belongs to no event.

Better: **measure the islands first.** Walk every link from each event node. Report each reach and
every intersection of two reaches, and find out whether the reaches add up to the node count of the
graph. When the reaches are disjoint, no scoping is necessary, and that statement is worth more than a
hedge. `python "{{OCTOPUS}}/packs/blueprint/tools/graph_islands.py" --project "{{PROJECT}}" Saved/<X>Graphs/EventGraph.json`
measures this on one per-graph JSON.

### Entry shape

```
### `<symbol>`

**Kind** … · **Status** … · **Tags** …

<pseudocode - readable indented control flow, ← for hoisted binds and reads-before-reset>

<40-150 words of prose: what it is for, what it assumes, what breaks if you change it>

**Parameter** / **Returns** / **Reads** / **Writes** / **Calls** / **Called from** / **Caveat**
```

A program parses the metadata line: ` · ` separates the fields and must **never** appear inside a
value. Tags come only from the project taxonomy: exactly one status, one role, and one domain or more.
Never a table row in place of an entry. "unverified - needs a PIE check" is better than a guess.

### Three caller sweeps, and how to read a zero

1. `python "{{OCTOPUS}}/packs/blueprint/tools/search_bp_refs.py" --project "{{PROJECT}}" <sym> --maps [--exact]`.
   A single hit on the declaring asset is the declaration, not a call.

   **`--exact` matches whole FName entries.** Use it when a shorter name is inside a longer one:
   `ResetScore` stops counting `ResetScoreLocal`. Without it, the scan is a plain substring test. (UE
   stores the int32 length prefix **with the NUL terminator**, so a 14-character name has a count of
   15. A frame with the bare length matches nothing, and that looks exactly like "this symbol has no
   references anywhere".)

   **`--exact` cannot see a trailing `_<digits>`.** An FName is a *(string, number)* pair, and
   `CustomEvent_3` is stored as the string `CustomEvent` with the number 4. So an exact sweep for it
   returns **zero packages, also not the one that declares it**, which looks like a dead symbol. For a
   name that ends in `_<digits>` (editor defaults such as `CustomEvent_0`, and members such as
   `value_`), sweep the stem and separate the hits with the guid.

   `--exact` and the substring form usually return the same package set. The prefix traps change
   occurrence counts much more often than the list of referencers. Only the guid sweep separates a
   caller from a different symbol with the same name.
2. `grep -rn <sym> Script/ Source/ Config/`
3. The scan of the graph dumps of the class, **limited to `props.FunctionReference`**. A plain substring
   scan gives false hits on the `graph` field that every node has.

**Always look for substring traps**: run again on a shorter stem. Typical cases: `ItemsByType` in
`SelectItemsByType`, `ActiveItem` in `ActivateItem` and `SwitchActiveItem`. Also look for a
*longer* name that contains yours (`GetScore` in `GetScoreText`), and for a path that only looks like
a symbol, for example an audio event path `/Game/Audio/Events/GetScore`.

**A symbol name can contain a character that is not ASCII, and a typed sweep misses it.** A Cyrillic
`С` (U+0421) looks the same as a Latin `C`, and every sweep that you type by hand returns zero. **Copy
names from the graph index; do not type them. For a name that is not ASCII, the guid sweep decides.**
Two more hazards: the keys of `_index.json` and of the worklist carry the character too, so a tool that
prints them needs a UTF-8 stdout (a cp1252 console raises *after* the write, so the command looks as
if it failed when it succeeded). And never "fix" the spelling in prose: document the real name,
because the next sweep must match it.

### The guid sweep - it turns a name hit into a proven reference

**The `MemberGuid` of a function is the `GraphGuid` of its graph.** Entry nodes usually export no
`MemberGuid`. That does not prevent a guid sweep: read the guid from the graph. Two ways to read the
wrong guid, and both give a confident wrong answer:

- the **forward declaration** (`Begin Object Class=…EdGraph Name="X"`) has no guid. The guid is in the
  **body** block (`Begin Object Name="X"`);
- the first `GraphGuid=` in a body can belong to a nested `MacroGraphReference` that points at the
  engine `StandardMacros`. A sweep of that guid measures how popular `ForEachLoop` is. **"First match
  in the body" is not a safe rule.** Parse with depth, and take the guid **at the graph level**, which
  comes *after* the `Nodes(...)` list, not inside a child object. `graph_guids.py` does this.

A free cross-check: the `MemberGuid` on any call node that points at the function must equal the
`GraphGuid` that you read. When the two agree, the guid is correct. When they do not agree, you read
the guid of a child.

Sweep the 16 bytes as **four little-endian uint32 values** (`guid_sweep.py`). Big-endian returns zero
for everything. **Validate on a control with a known count before you trust any result.** A control
that gives its known count separates "no callers" from "wrong byte order".

**Calibrate the baseline of the declaring package before you read a count as a number of calls.**
Measure each baseline against a control; do not assume it:

| Guid kind | Count in its own package | Each use elsewhere |
|---|---|---|
| function `GraphGuid` | 1 (the declaration) | +1 per call node |
| custom event | 2 | +1 |
| **variable `VarGuid`** | **2** (declaration) | **+1 per node that reads or writes it** |
| interface implementation | often 2 - measure it | callers carry the *interface* guid |

A count per package shows *presence*. It is a number of calls only when the arithmetic closes.

**The own `GraphGuid` of an interface implementation never leaves its package.** Sweep the
*interface* guid: the guid on the `FunctionReference` of the entry or result node. The local guid
gives one occurrence and looks dead; the interface guid shows the real callers.

**An override is not a caller, and its guid count shows it.** A child that overrides `F` carries the
guid of the *parent* three times: on its `FunctionEntry`, on its `FunctionResult`, and on a
`CallParentFunction` node. It calls nothing. If that `CallParentFunction` has no connected pins, the
parent body never runs. Resolve a strange count by a list of the *node classes* that carry the guid,
not by the number alone.

For a dispatcher, the two guids answer different questions. **`VarGuid` present means referenced. The
`GraphGuid` of the signature graph appears only on the `Delegate` pin of an Add/RemoveDelegate, so
`GraphGuid` present means a bind.** This arithmetic can prove that a dispatcher has a broadcaster and
no subscriber.

### Which layer a member lives on

On a Blueprint with a script parent, the question "did this member migrate?" has its answer on the
**`PinSubCategoryObject` of the `self` pin** of the property node. `/Script/Angelscript.<Class>` means
that the member is on the parent. The `BlueprintGeneratedClass` means that it is still on the
Blueprint.

**Do not read `VariableReference.MemberParent` for this.** It goes stale: it can still name the old
Blueprint class while the `self` pin already names the script class. The pin is correct, and the
reference is left from before the member moved.

**The staleness is per member, not per class**, so you cannot decide it one time per class. A second,
independent test: a `VariableReference` carries a `MemberGuid` **if and only if** the member is a
`NewVariables` entry of the Blueprint itself.

Parents can differ very much in how much they hold. One parent can hold most members of its
Blueprint, and a sibling parent can hold none and declare no functions, so its Blueprint still owns
every accessor. Measure each parent; never assume the answer of a sibling.

### Naming conventions are not evidence - read `FunctionFlags`

The RPC names of a class can be the opposite of its flags: a plain `SetValue` can be `NetServer`
while its `…Server` twin is `NetMulticast`. Another class can have names that agree with its flags.
Only the flags decide:
`FUNC_Net`=0x40 · `NetReliable`=0x80 · `NetMulticast`=0x4000 · `NetServer`=0x200000 · `NetClient`=0x1000000.

### Status

Zero callers gives **`suspect`, never `orphaned`**. `orphaned` claims a negative that needs a complete
call index. A function can have callers proven by guid whose call nodes store no name at all. The
exception is an engine override: there an empty sweep is the *expected* result, and the status is the
engine-invoked value of the taxonomy. Say so; do not grade it `suspect`.

### A zero is true for one moment

If somebody edits `Content/` while you work, a caller count measures one tree. A new asset can add
references to a symbol that you recorded with zero callers. **Give your denominator. When a zero makes
something `suspect`, name the tree that you measured.**

## The most expensive trap

**A field that is empty in hundreds of samples is much more probably a parse failure than a real
absence.** These "X is not exported" claims were all parser defects:

| Claimed missing | The facts |
|---|---|
| FText pin literals | `DefaultValue` escapes its inner quotes, so `"([^"]*)"` cuts the value. Read `DefaultTextValue`. |
| `MacroGraphReference` | The key is `MacroGraphReference`, not `MacroGraph`. Every instance names its macro. |
| `CreateDelegate` handler names | `SelectedFunctionName` was not in the list of properties that the parser kept. |
| Function locals | `LocalVariables(N)` is **indexed**, so an exact `key=` match never fires. |

The shared shape: **an indexed or nested T3D property does not match a flat key lookup, and the
silent result is an empty field that looks like an absent feature.** Before you write "X is not
exported", run `grep -c X <dump>` one time.

## Links, and the check that the validator does not do

Links depend on depth. From a `functions/*.md` page, the class overview is `../index.md`, a sibling
class is `../../<Class>/index.md`, and a data page is `../../../Data/<Name>.md`. From a page at the
class root, go up one level less. Never link into source directories: those are source paths, not
docs paths.

**A same-page anchor `(#foo)` must name a heading on the same page.** A tree validator often checks
links between files but **not** anchors inside a page, so these fail silently. Make anchors from the
real list of headings; do not type them. At the end, check every anchor with a small script: for each
`(#slug)` and each `(file.md#slug)`, compute the slugs of the headings of the target file and report
each slug that is missing. Exit 1 when one is missing.

**Validate the anchor check on a directory that you already finished before you trust its failure
list.** Three slug defects in a checker each accuse correct documentation:

| Slug defect | What it condemns by mistake |
|---|---|
| the target regex allows `#`, so `(#anchor)` parses as a *file name* | every same-page link |
| it removes `_` as emphasis | every identifier heading with an underscore, for example `Give_Item`, `Finish__` |
| it collapses `\s+` to one dash | every heading with several symbols: `` `A` · `B` `` gives a **double** dash, because the `·` goes and both spaces stay |

Run it on the finished class first. If it reports anything there, fix the checker, not the docs.

Two more validator blind spots: a program parses a `###` heading as a **symbol entry** and requires a
metadata line, so a section heading inside an aspect page must be `####` or lower. And if you renumber
the findings page, every `findings N` citation in the directory breaks.

## Finishing

Refresh the aspect pages **last**, from the entries: `dataflow`, `lifecycle`, `inheritance`, the
indexes, the findings. These pages most probably carry the errors from before the pass, and you can
write them correctly only after the work per symbol is done. Fan them out too: they are separate
files.

Then examine every finished entry for claims that the pass itself found to be wrong, and correct them
where they are. An entry from the first hour that nobody corrected is worse than no entry, because it
looks current.

Gates: the tree validator is clean · the worklist shows no problems · **a coverage gate reports 0
graphs without an entry** (`doc_entries.py <Class> coverage`; this is the objective test for "no
function abandoned") · every entry symbol has a heading on disk · every anchor resolves.

## What makes this work

Treat the subagent prompt as **evidence to check, not as facts to repeat**. The important corrections
come from agents that do not accept a premise they receive.

So state your premises in the prompt clearly enough that an agent can prove them false. Ask for the
measurement, not the conclusion. **When the number of an agent does not agree with what you told it,
the number wins.**
