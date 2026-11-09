# Tagging and descriptions

The vocabulary lives in `Docs/_taxonomy.json` and is **closed**. A tag that is not listed there is
an error — not an invitation to invent one. This is the whole reason tag search works: free-form
tagging drifts into `#anim` / `#animation` / `#animations` within a single run, and the filter panel
becomes a list of near-synonyms nobody can use.

On the first run, write a starter `Docs/_taxonomy.json` with the values below, one array per axis.
Four of the five axes do not depend on the project; use them as they are. The `domain` axis is the one place a project's own vocabulary belongs — seed it
during the first discovery run via the extension protocol below.

## The five axes

Faceted, not hierarchical. The viewer ORs tags within an axis and ANDs across axes, so
`#orphaned #deprecated #vfx` means *"(orphaned or deprecated) and vfx"*.

| Axis | Applies to | Cardinality |
|---|---|---|
| `domain` | pages, functions, variables | one or more |
| `role` | functions | exactly one |
| `status` | functions and variables | exactly one |
| `layer` | pages | exactly one |
| `kind` | pages | exactly one |

Cardinality is not decoration. Two `role` tags on one function means the function does two things,
which is a finding about the code, not a tagging decision — note it in Findings and pick the
dominant role.

Variables take a `role` only where one genuinely fits — `accessor`, `dispatcher` and `mutator` are
common; a pure tuning knob carries `#config` (a domain) and no role. That is why the role tag count
is legitimately lower than the symbol count.

**The axes must stay disjoint.** A value listed on two axes matches both facets, so selecting it
under one silently filters by the other as well. For example, `config` on both `domain` and `layer`,
or `hud` on both `domain` and `kind`, breaks the filter. Before adding any
tag, check it appears on no other axis — the checks below catch this.

### `domain` — what area of the game

The starter set, generic to most UE projects:

`movement` `animation` `camera` `input` `gas` `abilities` `ai` `ik` `physics` `collision` `audio`
`ui` `interaction` `items` `inventory` `data` `config` `save` `level` `vfx` `materials` `debug`
`net` `lifecycle-flow` `tags` `traces`

Extend it with the game's own areas (climbing, stealth, crafting, dialogue, …) via the extension
protocol below — during discovery, not silently mid-run. Keep every added value off the other four
axes.

Tag what the function *affects*, not what it touches in passing. A movement function that reads a
gameplay tag is `#movement`, not `#movement #tags`. Two or three domains is normal; five means the
function is doing too much — worth a Findings note.

### `role` — what kind of thing it is

| Tag | Use for |
|---|---|
| `entry-point` | Reached from outside the class — input binding, ability activation, level BP |
| `lifecycle` | `BeginPlay`, `EndPlay`, `PostInitializeComponents`, `Initialize` |
| `construction` | `UserConstructionScript` and anything it calls |
| `tick` | Runs every frame, or is only ever called from something that does |
| `event-handler` | Bound to a delegate, overlap, damage, or input event |
| `dispatcher` | An event dispatcher / multicast delegate the class owns |
| `accessor` | Returns state without changing it |
| `mutator` | Changes state |
| `query` | Computes an answer from the world — traces, overlaps, searches |
| `helper` | Pure-ish internal utility with no external callers by design |
| `factory` | Spawns or constructs actors, components, or objects |
| `override` | Overrides a parent implementation |
| `interface-impl` | Implements a Blueprint interface function |
| `latent` | Has an exec-output delay: timelines, delays, async loads |
| `anim-notify` | `AnimNotify_*`, called from a montage or sequence |

`override` and `interface-impl` outrank the others: if a function is both an override and a mutator,
tag it `override`. That is the fact a reader needs, because it explains why nothing calls it.

### `status` — health

Must equal the Status column on the same row. `active` `deprecated` `orphaned` `suspect`
`engine-invoked` `name-bound` `experimental`. The evidence rules for each are in `SKILL.md` step 3 —
do not assign these from intuition. This axis is closed for good: do not add to it, and never invent
axis values like `#dead-code` — dead code is `Status: orphaned` plus a domain tag.

### `layer` — which language declares it

`cpp` `blueprint` `mixed`, plus the project's scripting layer if it has one (the starter file ships
`angelscript`; remove it if unused). Use `mixed` only for an architecture page that genuinely spans
layers, never for a class — a class is declared in exactly one place.

### `kind` — what sort of thing the page documents

`character` `controller` `gamemode` `gamestate` `gameinstance` `hud` `component` `animblueprint`
`dataasset` `widget` `interface` `library` `plugin` `subsystem` `architecture` `actor` `ability`
`aitask` `struct` `enum` `datatable` `thirdparty` `statemachine` `cameramodifier` `notify`

The last group covers the exhaustive closure: `thirdparty` for every `treatment: summary` page,
`struct` / `enum` / `datatable` for `treatment: data` pages, and `actor` for placed world actors that
are none of the more specific kinds.

## Descriptions

Every listed member needs one clause, present tense, third person, no trailing period in the table
cell. It must survive the **name-deletion test**: cover the function's name with your thumb and read
the description. If it still tells you what the function does, it is good. If it becomes
meaningless, it was just restating the name.

| Bad | Why | Good |
|---|---|---|
| `Preloads visuals` | Restates the name | `Blocking-loads every soft visual reference on the visuals data asset` |
| `Handles movement` | Says nothing | `Applies a movement-speed transition and clears conflicting stances` |
| `This function is used to change the behaviour of the character from one state to another state` | Padding | `Swaps the character's behaviour state, cancelling the outgoing state's montage first` |
| `Returns bIsClimbing` | Restates the return | `Reports whether the wall-sensor component currently has a climbable wall contact` |

`list_functions` supplies the editor tooltip in `description`, and it is usually empty. When it is,
read the graph or the source body and write the clause. Do not leave the cell blank and do not write
`—` — an unsearchable row is the one failure this whole design exists to prevent.

Aim for 6–14 words. Long enough to be searchable, short enough to fit the table.

## Extending the vocabulary

When a class genuinely has no fitting tag — a new subsystem, a domain nobody has documented yet —
**stop and ask**, with the proposed tag, its axis, and the members that need it. Do not silently add
it to `_taxonomy.json` mid-run. Once approved, add it to the right axis array, and say so in the run
summary (and the changelist log, if the project keeps one).

Before proposing, check the existing list twice. Most "missing" tags are a synonym of something
already there: `#anim` → `animation`, `#fx` → `vfx`, `#getter` → `accessor` (a `role`, not a
`domain`), `#unused` → `orphaned` or `suspect` depending on evidence.

## Checking your work

Run these checks before you declare a run finished. Write them as one small script over the `Docs/`
tree, so that each run does the same checks:

- every tag on a page or a member is in `_taxonomy.json`;
- every symbol has a description;
- every symbol has exactly one status tag;
- every relative link resolves to a file that exists;
- no tag value is on two axes.

The same script rebuilds `_index.json` and `_symbols.json`. **Every member entry must carry the
`**Kind** · **Declared in** · **Status** · **Tags**` metadata line** (see `templates.md`). An entry
without it is invisible to the checks and to search, and the tree can look "valid" while most of it
was never checked.

Then count the tags per axis. Every tag must be under DOMAIN, ROLE, STATUS, LAYER or KIND. A tag in
no axis escaped the vocabulary: fix the page, not the taxonomy.

`layer` and `kind` must show **zero** uses among symbol tags. They are front-matter keys of a page
only. A count that is not zero means that an axis value collided with a domain tag.
