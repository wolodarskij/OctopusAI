---
name: regenerate-script-graph
description: Regenerate the Script/ dependency graph and the per-file dependency header blocks with the ue-angelscript pack's script_graph.py, then verify. Use when asked to "refresh the dependency graph", "update the script graph", "regenerate the dependency lists", "which files reference each other", "find the circular references in Script/", or after any batch of AngelScript work that added, removed or repointed classes.
---

# Regenerate the script dependency graph

One offline tool makes three outputs from `Script/**/*.as`:

    python "{{OCTOPUS}}/packs/ue-angelscript/tools/script_graph.py"

| Output | Path |
|---|---|
| an interactive graph in one self-contained file | `script_graph.outputHtml` in `.octopus/ue-angelscript.json`, default `Docs/script-graph.html` |
| the same model as data | the same path with `.json` (`--json` only) |
| a dependency block at the top of every `.as` file | `--write-headers --apply` |

The tool reads files only. **It never calls the editor**, so it is safe while other agents use the
editor. A run takes a few seconds.

The project settings of the tool are in the `script_graph` object of
`<project>/.octopus/ue-angelscript.json`. The README of the `ue-angelscript` pack lists every key.

## Regenerate the graph

    python "{{OCTOPUS}}/packs/ue-angelscript/tools/script_graph.py" --json

It writes the HTML file and the JSON file. When `Script/` did not change, a second run gives
byte-identical output, so a diff on the output is a real change in the code.

**Read the first lines of the output first.** When the parser left something out, it says so at
the top and exits 2:

```
THE PARSER LEFT SOMETHING OUT -- 2 item(s). Everything below is incomplete:
  UNKNOWN LAYER  NewLayer/MyThing.as  (directory not in LAYERS; add it and re-run)
```

`UNKNOWN LAYER` means that a new directory is under `Script/`. Add it to `script_graph.layers` in
`.octopus/ue-angelscript.json` with a dependency rank, and run again. Until then the file is not in the
graph.

## Read the cycles

The run prints the strongly connected components. For each one it prints the **cut links**: the
file pairs whose removal breaks the component apart.

```
CYCLES over unambiguous edges: 1 SCC of 3+, 0 two-file component, 12 mutual pairs
  scc  14 files  layers Gameplay, Globals, UI
       5 cut link(s), closing kinds call, inherit, member, type
```

In the HTML file, the **cycles** pill in the header and the `cycles view` link in the overview panel
show the same data. Cycle edges are pink. A thick pink edge is a cut link. `cycle edges only` and
`cut links only` are checkboxes in the left panel.

- **The counts do not include ambiguity.** The graph draws a call to a name that more than one file
  declares to every candidate, and that can make a false cycle. The tool computes components over
  unambiguous edges only. A cycle that exists only through an ambiguous edge is listed under
  **ambiguity-dependent** and counted nowhere else. Do not add the two sets together in a report.
- **The kind of the closing edge sets the cost.** An `inherit` edge closes a cycle that no wrapper
  can open. A `type` edge (a `Cast<X>` or a local of type X) is usually one line to remove. `member`
  and `call` edges are between the two.

## Refresh the per-file dependency blocks

Each `.as` file has a generated block at the top. It lists the project files that the file
references, the classes, structs and enums it names in each, the edge kinds, and whether the
reference is part of a cycle.

    python "{{OCTOPUS}}/packs/ue-angelscript/tools/script_graph.py" --write-headers            # DRY RUN: prints, writes nothing
    python "{{OCTOPUS}}/packs/ue-angelscript/tools/script_graph.py" --write-headers --apply    # writes each changed file that nobody holds

```
// --- BEGIN SCRIPT-GRAPH DEPENDS ---
// Generated. Regenerate with: script_graph.py --write-headers --apply
// References 2 file(s) in Script/ - file, then the symbols it names there:
//   Data/MyEnums.as                  member        EMyState, EMyAction
//   Gameplay/MyComponent.as          member cycle  UMyComponent
// In a 14-file reference CYCLE (unambiguous edges): no file in it can be read, ported or tested alone.
// --- END SCRIPT-GRAPH DEPENDS ---
```

`script_graph.headerNote` sets the second line.

The block is at byte 0, and one blank line follows it. It holds `//` lines only, so it cannot make a
`/** */` pair, and it cannot come between a doc comment and the symbol that the comment describes.
The tool removes every `BEGIN..END` region before it writes a new one, so a second run cannot nest
or duplicate the block. A second run with no source change leaves every file byte-identical. The
tool keeps the line endings and the encoding of each file.

### `--apply` skips the files that another agent holds

`--apply` writes the whole tree. It reads the claim board first (`script_graph.claimsFile`, default
`.claude/coordination/CLAIMS.md`) and **skips each file that a live claim covers**. The output names
the skipped files at the top, before the write summary, and the run **exits 3**:

```
HELD BY ANOTHER AGENT -- 2 of 40 file(s) will NOT be written:
  SKIP  Gameplay/MyComponent.as        T0002 (claims `Script/Gameplay/MyComponent.as`)
  SKIP  UI/MyWidget.as                 T0002 (claims `Script/UI/`)
```

Exit 3 means that the tree is **partly current**: the skipped files have an old block or none. When
the holder releases the files, run `--apply` again. It writes only what changed.

How the tool reads a hold:

- **By the POSITION of a block, never by its timestamp.** The board is append-only and its
  timestamps are self-reported. The state of an agent is its **last block in the file**.
- **Each shape that the parser cannot read counts as HELD**, and the output lists it under
  `CLAIM BOARD SHAPES READ CONSERVATIVELY`. Examples: a header with no status word, a live block
  with no `**Exclusive paths:**` section (every path-like token in it is then held).
- **A claim on `Script/` holds every file**, and a claim on a directory holds its subtree.
- **A missing or unreadable board stops the write (exit 2).** Give another path with
  `--claims <path>`.

### `--as-agent <id>`: write a tree that you claimed yourself

When you claim `Script/` before a write of the whole tree, `--apply` skips every file as held by
you and exits 3. Give your own claim id:

    python "{{OCTOPUS}}/packs/ue-angelscript/tools/script_graph.py" --write-headers --apply --as-agent <your-claim-id>

It removes **only the holds of that id**. The holds of every other agent still skip their files,
and the run still exits 3 if one did. The output shows the block that it removed. When the id
matches no live hold, the output says so: that means you did not claim.

### `--ignore-claims`: only after a check of the whole board

    python "{{OCTOPUS}}/packs/ue-angelscript/tools/script_graph.py" --write-headers --apply --ignore-claims

It writes held files too, and prints each hold that it overrides, by agent and by file. Use it only
when you know that each holder is not alive. Write on the message board
(`script_graph.messagesFile`) that you used it, and on which files.

### `--apply` touches every `.as` file

A save of any `Script/**/*.as` file recompiles the **whole** AngelScript layer for every user of the
editor, and a structural reload can crash the editor. One `--apply` over the whole tree is **one**
reload. The same work in parts is one reload per part.

Before you run it:

1. Claim `Script/` on the claim board.
2. Write on the message board that the operation touches every `.as` file.
3. Read the dry-run output for `HELD BY ANOTHER AGENT` and `REFUSED` lines. The tool does not
   change a file with a `BEGIN` and no `END`, or a file that is not valid UTF-8, and names it. Fix
   such a file by hand.
4. Run `--apply` **once**, for the whole tree.

The tool does not run `--apply` when the parser left anything out. A block written from an
incomplete parse gives an incorrect dependency list.

| Exit code | Meaning |
|---|---|
| 0 | wrote each file that needed it; nothing held |
| 1 | wrote, but at least one file was `REFUSED` (no `END`, or not UTF-8) |
| 2 | stopped before the write: the parser left something out, or the claim board could not be read |
| 3 | **at least one file is held.** With `--apply` it wrote the rest; a dry run gives the same code |

## Verify: all four, every time

    python "{{OCTOPUS}}/packs/ue-angelscript/tools/script_graph.py" --self-test
    python "{{OCTOPUS}}/packs/ue-angelscript/tools/comment_census.py" --stacked Script
    python "{{OCTOPUS}}/packs/ue-angelscript/tools/script_graph.py" --write-headers     # must report nothing to change

| Check | What it proves |
|---|---|
| `--self-test` | the facts in `script_graph.selfTest.facts` hold on the live tree, and the plants injected into a **copy** of `Script/` in a temp directory are found, against a **fixture** claim board in that directory. It writes nothing when a check fails. With no `selfTest` settings it prints SKIPPED and exits 0 |
| `comment_census.py --stacked Script` | still `0 detached doc comment(s)`: the block did not make one |
| a second `--write-headers` | reports that every generated block is current. If it wants to change files again, the block is not idempotent: stop and say so |
| the hot-reload log | see below |

### The hot-reload check after `--apply`

**Search the editor log for `Keeping all old script code`, not for `Error`.** A failed AngelScript
hot reload prints that line, and the layer then stays on the last good build.

Search for `Keeping all old script code|Full Reload|Soft Reload|Assertion failed|appError`.

The old code continues to run, so fix the file that the **first** error names, and read the log
again. One missing `/*` can cause many errors after it.

## What the graph says, and what it does not say

- **The identity of a node is its path relative to `Script/`**, with forward slashes and the
  extension: `Gameplay/Components/MyComponent.as`. That is the JSON `id`, the DOM `data-id` and the
  URL fragment, so `script-graph.html#Gameplay/Components/MyComponent.as` selects that node when the
  page opens.
- **"No caller in `Script/`" never means "unused".** A Blueprint can call any reflected function,
  `GetComponentByClass` finds a component at run time, and a `K2Node_AddDelegate` binding makes no
  call node. The tool reads only `Script/`. The strong signal is the pair *no caller in `Script/`*
  **and** *unreflected*.
- **An upward edge breaks the layering of the ranks in `script_graph.layers`.** Check the ranks
  before you call a red edge a defect.
