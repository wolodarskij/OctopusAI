# The `browser-game` pack

The pack for a game in TypeScript that runs in the browser: `tsc`, ESLint, Vitest and the Vite
dev server. The pack contract is `packs/PACKS.md`.

## What the pack holds

| Path | What it is |
|---|---|
| `ruleset.json` | the checks, the grant, how to look at the result on screen, the tooling paths, and the payload |
| `rules/browser-game.md` | the hard rules. Install copies it to `<project>/.agents/rules/octopus/browser-game/` |
| `templates/eslint.core-purity.js` | the ESLint block that keeps `src/core` pure. Install copies it to `<project>/eslint.core-purity.js` |
| `templates/launch.json` | the Vite dev server on port 5173, named by the project slug. Install copies it to `<project>/.claude/launch.json` |
| `templates/run.bat` | starts the dev server. Install copies it to `<project>/run.bat` |
| `tools/assets.py` | the asset inventory of a project. It stays in Octopus AI |
| `tools/asset_board.html` | the browser page of the inventory. The Octopus AI board server serves it |

## `ruleset.json`

- **Checks:** `npm run typecheck`, `npm run lint`, `npm test`.
- **Granted:** run the dev server and the browser preview.
- **Look on screen:** start the dev server with `npm run dev`, look at the browser preview, and
  make sure the console shows no errors.
- **Tooling paths:** `package.json`, `eslint.config.js`, `tsconfig.json`, `vite.config.ts`.

## The rules

`rules/browser-game.md` holds seven rules, each with its reason and its check:

1. The core is pure: no engine library, no DOM, no `node:*`, no `Math.random`. ESLint enforces it.
2. The view is an adapter and holds no rule.
3. Content is data, and a validate command checks it.
4. Every player-visible string is a locale key.
5. The simulation is deterministic, with one named RNG stream for each system.
6. A `?seed=` URL replays a level, and a headless play command runs bots.
7. Scratch files and memory stay inside the project.

## The templates

Install fills `{{NAME}}`, `{{SLUG}}`, `{{OCTOPUS}}` and `{{PROJECT}}` in each file.

- **`eslint.core-purity.js`** exports the block for `src/core/**/*.ts` as `corePurityConfig`,
  and a default export that is a list of that one block. Spread the default export into the
  project's own `eslint.config.js`:

  ```js
  import corePurity from './eslint.core-purity.js';

  export default tseslint.config(
    js.configs.recommended,
    ...tseslint.configs.recommended,
    ...corePurity,
  );
  ```

- **`launch.json`** runs `npm run dev` on port 5173, with the configuration named `{{SLUG}}`.
- **`run.bat`** stops with a message when `npm` is not on the path, runs `npm install` when
  `node_modules` is missing, then runs `npm run dev -- --open`.

## `assets.py`

```
python <Octopus AI>/packs/browser-game/tools/assets.py list                one line per kind with its count
python <Octopus AI>/packs/browser-game/tools/assets.py list --kind <kind>  the ids of one kind
python <Octopus AI>/packs/browser-game/tools/assets.py list --json         the whole inventory as data
python <Octopus AI>/packs/browser-game/tools/assets.py check               exit 1 when a file under a check root has no row
```

`--kind` also works with `--json`. An asset's id is its path relative to the project.

**The project.** The tool takes the first of these:

1. `--project <path>` on the command line. The tool removes it before it parses the other
   arguments, so it can stand anywhere on the line;
2. the `OCTOPUS_PROJECT` environment variable;
3. the nearest directory above the current directory that holds `.octopus/project.json`.

**The editor.** The board's `Edit` button runs `OCTOPUS_ASSET_EDITOR` with `{path}` filled, for
example `code -g {path}`. When the variable is not set, `Edit` opens the file like `Open`.

### `<project>/.octopus/assets.json`

The table of kinds and every lookup into the project's content. With no `assets.json` the table
is empty, and the tool writes a note to stderr. Every key is optional.

| Key | Type | Meaning |
|---|---|---|
| `kinds` | list of rows | one row per kind of file, in order. The first row a file matches is its kind |
| `support` | list of globs | files under a check root that are not assets: docs, manifests, generators |
| `checkRoots` | list of directories | the directories `check` scans. A file there that no row and no `support` glob matches fails `check` |
| `generated` | list of kinds | kinds that a generator writes. The page marks them `generated`. A file named `<name>.<w>x<h>.<ext>` shows the name `<name>` and a `<w>x<h> cells` badge |
| `usedKinds` | list of kinds | the kinds the `usedBy` lookups cover. An asset of such a kind with no user shows `unused` |
| `usedBy` | list of lookups | which content entries draw which asset. See below |
| `artboards` | list of globs | manifests of the form `{"artboards": [{"file", "title", "w", "h"}]}`. `file` is relative to the manifest. The page shows `title` as the name and scales the sheet from `w` and `h` |
| `placeholders` | object | the value that fills each `{{name}}` in an SVG on the page. A placeholder with no value becomes empty |
| `gridFill` | kind -> glob | an SVG of that kind is filled with the first one-bit grid the glob finds, as `width`, `height` and `cells` |
| `ink` | colour | the colour of a one-bit grid on the page. Default `#000000` |

**A row of `kinds`:**

| Key | Meaning |
|---|---|
| `kind` | the name. Required |
| `group` | the heading on the page rail |
| `root` | the directory, relative to the project |
| `globs` | the file names under `root` that match, for example `*.svg` or `**/*.png` |
| `preview` | `svg` (placeholders filled), `grid` (a one-bit text grid: `#` is ink), `img`, `audio`, `html` (a sheet in a sandboxed frame), `json` (a key summary and the text), or `none` (open only) |

**A lookup of `usedBy`:**

| Key | Meaning |
|---|---|
| `file` | the JSON file to read, relative to the project |
| `at` | a dot path to the node the lookup starts from. Default: the whole file |
| `each` | a list of steps. Each step is `{"path": <dot path>, "as": <name>}`: it takes every object entry of the list or the object at `path`, under the entry of the step before, and names it `as` (default `item`). An empty `path` is the node itself |
| `where` | dot path -> value. The entry counts only when each path holds that value |
| `match` | dot path -> regular expression. The entry counts only when each value matches. The named groups of the expression become names for the templates |
| `find` | a key. The lookup takes every string stored under that key, anywhere under the last entry, sorted and without duplicates, as `{found}` |
| `who` | the text the page shows for the user, as a template |
| `replace` | value -> text. A value in `who` that equals a key shows as the text |
| `assets` | a list of asset id templates. The lookup records `who` as a user of each |

A template fills each `{<dot path>}` from the names of the entry. In a dot path, a number
indexes a list, `length` is the length of a list, and `$key` is the key or the index of the
entry. An asset template with a path that does not resolve adds nothing. Write a literal dot in
a regular expression as `[.]`.

An example with one kind and one lookup:

```json
{
  "kinds": [
    {"kind": "token", "group": "Art", "root": "art/tokens", "globs": ["*.svg"], "preview": "svg"}
  ],
  "checkRoots": ["art"],
  "support": ["art/README.md"],
  "usedKinds": ["token"],
  "placeholders": {"size": "96"},
  "usedBy": [
    {"file": "content/units.json",
     "each": [{"path": "units", "as": "unit"}],
     "who": "unit {unit.id}",
     "assets": ["art/tokens/{unit.token}.svg"]}
  ]
}
```

### The interface the board server calls

```python
import assets

assets.set_root(path)       # point the module at one project; call it before each request
assets.inventory()          # -> list of records: {id, kind, group, name, path, bytes, mtime,
                            #    placeholders, usedBy, preview, badges, board}
assets.kinds()              # -> list of {kind, group, preview}, in table order
assets.serve(id)            # -> (bytes, content type), or None for an unknown id or an open-only kind
assets.ACTIONS              # {"asset-open": act_open}; act_open(body) takes {id, how}, how is open | edit | reveal
```

`GET /api/assets` answers `{"assets": inventory(), "kinds": kinds()}`, and `GET /asset/<id>`
answers `serve(id)`. The page `asset_board.html` holds the placeholder `__TOKEN__`, which the
server replaces with its token. When the server opens the page as `/assets?project=<name>`, every
request of the page carries the same `project` parameter.
