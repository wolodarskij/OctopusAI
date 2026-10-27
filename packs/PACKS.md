# Packs

A pack is an optional part of Octopus AI. A project takes any number of packs at install time
(`--packs a,b`, or the checkboxes of the board's Add project window), or none, adds or removes one later (`octopus packs <project> add | remove <name>`), or sets
them all at once (`octopus packs <project> set a,b`). The core runs with no pack.

**A renamed pack keeps its old name as an alias.** `angelscript` is `ue-angelscript` and `browser` is
`browser-game` (`PACK_ALIASES` in `core/engine/project.py`). The command line, a `project.json` and a
`requires` may still say the old name. `octopus update` and the `SessionStart` hook move a project to the new
name: its `rulesets`, its rules folder `.agents/rules/octopus/<name>/`, its install record and its settings
file `.octopus/<name>.json`.

## The layout of a pack

```
packs/<name>/
  ruleset.json        the rules the pack adds to a project, and the payload that install copies
  rules/              markdown rules; install copies them to <project>/.agents/rules/octopus/<name>/
  skills/<skill>/     skills; install copies each to <project>/.agents/skills/<skill>/, and every
                      AI provider finds it there (Claude Code through a generated pointer)
  brief/<section>.md  brief sections the pack adds or replaces (see core/engine/brief/README.md)
  tools/              scripts; they stay in Octopus AI and run from there
  templates/          files install copies into the project, with the placeholders filled in
```

## `ruleset.json`

Every key is optional except `title`.

| Key | Type | Meaning |
|---|---|---|
| `title` | string | one line: what the ruleset adds |
| `detect` | list of path globs, relative to the project root | the board's Add project window ticks the pack, as a suggestion the owner can untick, when one glob matches a path in the chosen directory |
| `requires` | list of pack names | install refuses the pack while one of these is absent, and uninstall of one of these removes this pack too |
| `optionalRules` | map of a rule file stem to `{title, default}` | rules of the payload that a project switches on and off; `default` is `true` (on) or `false` (off) |
| `hardRules` | list of strings | the rules a session obeys, listed in the brief |
| `checks` | list of commands | the commands a session runs before it hands work over |
| `toolingPaths` | list of path prefixes | a ticket whose `source` starts with one is `category: internal tooling` |
| `writePaths` | list of path prefixes | where a session may write |
| `readOnly` | list of path prefixes | what no session writes |
| `granted` | list of strings | what every session may do without a permit |
| `gitignore` | list of `.gitignore` lines | what a project's repository never tracks: machine-local files and build output. When Octopus AI makes the repository, it adds every line the `.gitignore` lacks |
| `oldNameAllowed` | list of path globs | files that may name the toolkit by a name that must not appear elsewhere. `check` and `octopus.py status` report every other open ticket or file, tracked by git, that misnames the toolkit. `**` spans directories; a glob with no `/` matches a file name anywhere |
| `oldNameAliases` | list of strings | literal strings that are no hit of that check wherever they stand |
| `lookOnScreen` | string | how a verify session looks at the result on screen |
| `terminal` | `wt` or `cmd` | the default terminal of a launched session |
| `sessions` | bool | whether terminal control (screen, send, focus, close) works on this platform |
| `permits` | name -> `{grants, deny, addDirEnv, addDir, addDirRegistry}` | a capability a ticket must be given. `deny` lists the tools the launch denies while the permit is absent. `addDirEnv` names the variable whose path a granted permit adds with `--add-dir`; `addDir` is the path when the variable is not set. `{SLUG}` in `addDirEnv` becomes the project slug in capitals. `addDirRegistry: true` adds the root of every other registered project |
| `permitOverrides` | name -> `{grants, addDirEnv, addDir, addDirRegistry}` | changes a permit an earlier ruleset defined |
| `deny` | list of `{tools, unless}` | deny `tools` while none of the permits in `unless` is granted |
| `testerPermits` | list of permit names | the permits a `repeat: test` ticket holds on every run |
| `modePermits` | launch mode -> list of permit names | the permits every launch in that mode holds, whatever the ticket says. A `review` launch and the manager never get them |
| `brief` | list of section names | the brief sections this pack supplies from its `brief/` directory |
| `payload` | list of `{from, to, find, replace, pending}` | what install copies. `from` is relative to the pack, `to` is relative to the project. A `from` that ends in `/` copies the directory. The other keys are optional: `find` names a file; when the project already holds it under the top directory of `to`, its directory is the destination. `replace: true` gives the copy to the pack: install overwrites a copy it did not write or that was edited, removes the files the pack no longer ships, and the `SessionStart` hook brings the copy up to date. `pending` is a project-relative file install writes when the entry wrote or removed a file |

Lists join in ruleset order without duplicates. A scalar that a later ruleset sets wins. A permit
defined twice is a problem that `check` reports; `permitOverrides` is the way to change one.

**An optional rule is on or off in each project.** `optionalRules` names a rule file of the payload
by its stem, `"optionalRules": {"<stem>": {"title": "...", "default": true}}`. The global switch,
`{"rules": {"<stem>": false}}` in `rules.json` of the Octopus AI home directory, holds for every
project. A project switches it in `project.json`, `"rules": {"<stem>": false}`, and its switch
wins. A rule that neither names takes its `default`. Install copies the rule only while it is
on, and turning it off removes the copy unless it was edited. `octopus rules` lists the rules
with their state and where the state comes from. `octopus rules <rule> on|off|default` switches
a rule globally, and `--project <name>` switches it in one project. The Optional rules sections
of the board's Global settings and Project Settings views do the same. A switch writes
`AGENTS.md` and `CLAUDE.md` again in each project that it changes. A guard of the rule and a
brief section of the rule follow the switch: the README guard and the brief section
`readme.<mode>` hold only while `readme-needs-owner-approval` is on.

## Placeholders

Install fills these in every text file it copies:

| Placeholder | Becomes |
|---|---|
| `{{OCTOPUS}}` | the Octopus AI directory, with forward slashes |
| `{{NAME}}` | the project `name` |
| `{{SLUG}}` | the project `slug` |
| `{{PROJECT}}` | the project directory, with forward slashes |

A skill or a rule that runs a pack tool names it as
`python "{{OCTOPUS}}/packs/<pack>/tools/<tool>.py"`.

## The Unreal packs

| Pack | Requires | What its tools read |
|---|---|---|
| `unreal` | - | the engine, the editor, the build, `Config/`, the MCP connection |
| `blueprint` | `unreal` | Blueprints: the `.uasset` bytes under `Content/`, the T3D text exports, the graphs, the live editor |
| `ue-angelscript` | `unreal` | `Script/`, project text files, and the Blueprint export |

A tool of a language pack never reads a Blueprint. A tool that compares the source of a language
with the Blueprints is split in two:

- the Blueprint half is a kind of `packs/blueprint/tools/blueprint_export.py`. It writes the data
  to `<exportDir>/<kind>.json`; `exportDir` is a key of `.octopus/blueprint.json`, and the default
  is `.octopus/export/blueprint`.
- the language half reads its source and that file. When the file is missing, it prints one line
  that names the export command, and exits 2.

The kinds and the schema of each file are in `blueprint/README.md`. A language pack does not
require the `blueprint` pack: its tools need only the export file.

## How a pack tool finds its project

Every tool of the `unreal`, `blueprint` and `ue-angelscript` packs finds the project the same way,
through `packs/unreal/tools/_project.py`:

1. `--project <path>` on the command line;
2. the `OCTOPUS_PROJECT` environment variable;
3. the nearest directory above the current directory that holds `.octopus/project.json` or a
   `*.uproject` file.

A value that belongs to one project - a path, a class name, an asset name, a table - is never in
a tool. It is in `<project>/.octopus/<pack>.json` (`unreal.json`, `blueprint.json` or
`ue-angelscript.json`), and the tool reads it with `_project.config("<pack>")`. A tool whose
self-test needs a project's assets reads the fixture names from there, and skips when none is set.

## The git pack

`git` guards what a launched session may do with git. It needs nothing else. Its denies apply in every
project that lists it. A `work` launch always holds `git-commit`, and a `verify` launch always holds
`git-commit` and `git-sync` (`modePermits`), so each can commit and push as the core brief says. The
commit policy reaches a brief only when `.octopus/project.json` sets `vcs` to `git`. Add it with
`octopus packs <project> add git`.

| Permit | Grants | Denied while absent |
|---|---|---|
| `git-commit` | commit to the local repository | `git commit`, `revert`, `cherry-pick`, `tag` |
| `git-sync` | pull and push, add or change a remote | `git push`, `pull`, `remote add`, `remote set-url` |
| `git-branch` | change branches | `git switch`, `checkout`, `branch`, `worktree` |

The commands denied to every session and the commit policy of each mode are in
`git/rules/git.md` and in the brief sections `vcs.<mode>` (see `core/engine/brief/README.md`).
The core supplies `vcs.work` and `vcs.verify`; the pack supplies `vcs.plan`, `vcs.review` and
`vcs.manager`.

## Another engine

A pack for another engine is a new directory under `packs/` with a `ruleset.json`. Give it
`detect` globs, such as the engine's project file, so the Add project window suggests it for a
directory of that engine.

## A pack added on this machine

A pack need not live in `packs/`. `octopus pack-registry add` adds one from a folder, which is used
where it is, or from a git repository, which is cloned into `%APPDATA%/OctopusAI/packs/<name>/repo`
and checked out at `--ref` (a branch, a tag or a commit; the default branch when none). `--path`
names the pack's directory inside the folder or the repository. The board's Global settings does
the same. The row goes into `%APPDATA%/OctopusAI/packs.json`, outside the tree, because it names a
machine path. From then on the pack is a checkbox in every project, and its rules, skills, brief
sections and payload install like those of a shipped pack.

Adding refuses a pack that fails this contract, and lists each problem:

- the name is lower-case letters, digits, `-` and `_`, and no core ruleset or other pack has it;
- `ruleset.json` is a JSON object with a `title`, and it loads with no problem after the base
  rulesets and the packs it `requires`: only the keys above, each list a list of strings, each
  permit complete, no permit another ruleset defines;
- every `requires` names a pack this machine has;
- every payload `from` exists in the pack, and every `to` is project-relative and stays inside it;
- every `brief` section has its `brief/<section>.md`;
- every directory under `skills/` holds a `SKILL.md` whose frontmatter has `name` and `description`.

`pack-registry update <name>` fetches a git pack again (a branch moves, a tag or a commit stays),
checks it, and runs `update` in every registered project that lists it; an update that fails the
contract is undone. `pack-registry remove <name>` refuses while a registered project lists the pack.
