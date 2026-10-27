# Reading a Blueprint: take the cheapest route that answers

*Always applies. Governs text exports and live editor reads. Where this rule conflicts with another
rule about reading Blueprints, this rule wins.*

In this rule, `<tools>` is `{{OCTOPUS}}/packs/blueprint/tools`, and each tool runs as
`python "<tools>/<tool>.py" --project "{{PROJECT}}" ...`. `<t3dDir>` is the directory that `t3dDir` in
`{{PROJECT}}/.octopus/blueprint.json` names (default `T3D`).

## The ladder

Each route below costs more than the route above it. Do not go down the list until the route above
cannot answer your question. "It is quicker to look at the graph" is the failure that this rule
prevents. It is never quicker when several agents share one editor.

| # | Route | Cost | Use it for |
|---|---|---|---|
| 1 | the pages of the project that describe the native layer as it is now, if it keeps them | free | what the native layer is **now** |
| 2 | the legacy record of the project, if it keeps one (for example the tree that the `unreal-project-docs` skill writes) | free | what the Blueprint **did**: body, pins, callers, intent |
| 3 | the ported source, the change log, the ledger of deliberately removed behaviour, the ticket list | free | what the port already says, what was removed on purpose, what is known to be wrong |
| 4 | the offline tools in `<tools>` | seconds of CPU | islands, entry points, name references, guids |
| 5 | a text export that ALREADY exists in `<t3dDir>` | context only | a pin default or an island that the page does not state |
| 6 | a live read-only editor read (`get_graph` -> `find_nodes` -> `get_node_infos`) | shared editor time | control flow that a page cannot express: macros, exec joins, orphaned pins |
| 7 | a NEW text export (`<tools>/export_bp_t3d.py`) | shared editor time and hundreds of KB of context | only while you mutate that asset |

**Name the route that answered** where the answer is used (the ticket, the doc page, the source
comment): "answered from `Docs/Classes/BP_Door/functions.md:164`", or "nothing on disk had the pin
default; read it live". Never in a chat report to the owner.

## A live `.uasset` stops holding the logic as a migration continues

A port empties the asset as it goes. An asset that answered a question last month answers with an
absence today, and an absence looks like a fact. For **what a Blueprint DID**, the order is:

1. the page of the legacy record;
2. the **oldest** export of that class in `<t3dDir>`, oldest by mtime, because it holds the most;
3. when neither answers: write "I do not know what it does", and stop.

After a migration starts, the live asset is not on that list.

**Widget bindings are the exception.** To read the authored child names of a widget from its asset
is implementation while you write `BindWidget` declarations. It never decides what a function does.

**Before you open an asset, make sure that its parent class still exists in the native layer.** An
asset whose script parent was deleted cannot load and answers nothing.

## The free routes are the default, and they are usually sufficient

| Question | Route |
|---|---|
| "What does this function do?" / "How was this implemented?" | 2 |
| "Reimplement this natively" | 2: the pseudocode IS the source. No pins, no export |
| "Why did this change?" | 3 |
| "Does anything still refer to X?" | 4 |
| "Port a body" | 2 |
| "Rewire a graph node by node" | 6 or 7, and a port is better |

A port needs no pin. You write native code from the pseudocode, and then you delete the graph. Pin
names, node ids and link state answer a question that a port never asks. Many pin reads are a sign
that you chose to rewire.

Trust a legacy page for what a function is FOR and WHY. Verify every formula, sign, axis and argument
list against a second source before you port arithmetic. The silence of a page is not evidence: it
cannot see a disconnected island, and it cannot tell you whether a graph still exists.

## When the user says "look in the documentation"

Read the docs and nothing else: not an export, not a live read, and not the ported source. The ported
source is what somebody concluded, and it is usually the thing that the question checks. If the
documentation does not answer, say so and stop.

## A live editor read is not free because it is read-only

It runs on the one shared editor, and every other agent waits behind it. It is the correct route for
one type of question only: control flow that the pages cannot express.

- `read_graph_dsl` **drops the exec content of a macro** and **shows an exec JOIN as a branch**. So a
  graph with macros reads as empty, and a node with two exec inputs reads as two calls.
- An audit of orphaned pins must read the live graph (below).
- `list_graphs` starts a compile on a Blueprint that is out of date (see `shared-editor.md`).

Before a live read, ask what the world looks like if your premise is true. A premise whose result
contradicts the running game is false before any editor call.

## A new text export: the rules

**Only while you change a Blueprint.** Capture the state of an asset that you are about to mutate, or
verify a mutation that you just made.

**Never export the same state again.** Before you run `export_bp_t3d.py`, look in `<t3dDir>` for an
existing export of that asset, and use it. Exports accumulate as `Text<Asset>-<n>.txt`. The only
export that is not a repeat is one taken AFTER a mutation that you just made.

### Reading what is there

- The dumps of the engine use **mixed encoding**: UTF-16-LE when the text holds a character that is
  not ANSI, UTF-8 in the other cases. A byte-oriented `grep` returns a confident zero. Look at the BOM
  (`head -c2 <dump> | xxd -p`: `fffe` = UTF-16LE, `efbbbf` = UTF-8 BOM).
- **"Oldest" means EARLIEST MTIME, not lowest `<n>`.** Select with `ls -l --time-style=long-iso`.
- **Node names repeat within a file.** Ten different nodes can have the name
  `K2Node_CallArrayFunction_2` in one asset. Follow chains with `t3d_node_chain.py`, which resolves
  by PinId.
- **A sweep of "all exported assets" is not a project scan.** Exports exist only for assets that
  somebody exported. Scan `Content/` with `search_bp_refs.py --exact --maps`.

The old exports are a restore point and a last source for research. Read the oldest one when the
legacy page does not answer. Do not mine the others.

## What a text export gives that a page does not, and what it drops

| | behaviour? |
|---|---|
| exact pin defaults on pins that the doc pass did not quote | **yes**: an unwired bool pin with `true` changes what the function does |
| disconnected islands that the doc pass did not measure | **yes**: a node that nothing reaches does not run |
| node identity and `GraphGuid` | no; it serves guid sweeps and caller counts |

**An export DROPS orphaned pins.** A node whose live copy has an orphaned `int = 2` exports with no
`int` pin; the only trace is an old `ErrorMsg` string. An audit of orphans needs a live read, and the
silence of an export looks like a clean asset. Also, sweep what you measure: a defect found at one
site must be looked for in the sibling classes too.

## Why this rule exists

One export is hundreds of kilobytes to megabytes of node dump, and it pushes out the information that
the task needs. An agent that has read three exports has spent its working memory on node ids and pin
literals. The doc page is a few hundred lines and answers the same question. The same argument
applies to a live read, in the time of the other agents.
