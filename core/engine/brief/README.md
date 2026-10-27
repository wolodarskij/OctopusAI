# Brief sections

A launched session reads one file, `TODO/.briefs/<id>.md`. The engine builds it from the section
files in this directory, in this order:

| Order | Section | When |
|---|---|---|
| 1 | `header` | always |
| 2 | `messages` | always |
| 2a | `provider` | the launch starts an AI provider other than Claude Code, such as Codex |
| 3 | `where` | the project has a description |
| 4 | `boundary` | always |
| 5 | `blocked` | always |
| 6 | `repeat`, then `repeat.<role>` | a repeatable ticket; `<role>` is `test`, `verify` or `board`. A `manager` ticket gets other sections: see below |
| 7 | `job.work-plan` or `job.<mode>` | `job.work-plan` for a `work` launch on a ticket that has `## Plan` |
| 7a | `readme.<mode>` | the rule `readme-needs-owner-approval` is on for the project: `readme.work` for a `work` launch (also on a ticket with `## Plan`), `readme.plan` for a `plan` launch |
| 8 | `permits.granted` or `permits.none` | the ticket has permits, or it has none |
| 9 | `permits.ungranted` | a permit exists that the ticket does not have |
| 10 | `permits.denied` | the launch denies a tool |
| 10a | `projects` | the launch holds the `projects` permit: carry the change into every project, launch each follow-up |
| 11 | `before.<mode>`, else `before` | always |
| 12 | `finish.<mode>` | always |
| 13 | `vcs.<mode>` | the project has `vcs: git`. The core supplies `vcs.work` (commit the work before the move) and `vcs.verify` (push it when it holds); the `git` pack supplies `vcs.plan`, `vcs.review` and `vcs.manager`. It continues the list of `finish.<mode>` |
| 14 | `finish` | always |
| 15 | `close.<mode>` | always: `plan`, `work`, `review` and `verify` each have one |
| 16 | `note` | the launch carries a note |
| 17 | `ticket` | always |

A `manager` ticket never finishes and never moves its own ticket, so its brief is only
`header`, `messages`, `provider`, `where`, `repeat.manager`, the `permits.*` sections (never `projects`: `repeat.manager` carries the projects), `before`, `vcs.manager` (on the
same condition as `vcs.<mode>`), `note` and `ticket`.
It leaves out `boundary`, `blocked`, `repeat`, `job.*`, `readme.*`, `finish.*` and `close.*`.

## Where a section comes from

The last of these that has the file wins:

1. this directory;
2. the `brief/` directory of each ruleset the project lists, in ruleset order, for the sections
   the ruleset names in its `brief` key;
3. the project's own `.octopus/brief/`.

An empty file removes the section. A section no layer supplies is left out.

## Placeholders

| Placeholder | Becomes |
|---|---|
| `{id}`, `{id_lower}`, `{title}`, `{mode}` | the ticket id, the id in lower case, the title, the launch mode |
| `{cli}` | the command that runs the engine for this project (`cli` in project.json) |
| `{todo_dir}`, `{name}`, `{slug}`, `{description}`, `{project_line}` | the project |
| `{header_rows}` | the table rows of the header: ticket, state, severity, source, epic, parent, blocked by |
| `{run}`, `{repeat_label}`, `{repeat_meaning}` | a repeatable ticket's run and role |
| `{projects}` | a table of every registered project: name, root, and the command that runs its ticket engine |
| `{octopus}` | the command that runs `octopus.py` of this checkout: `python "<checkout>/octopus.py"` |
| `{permit_rows}`, `{ungranted}`, `{denied}` | the permits the launch grants, the ones it does not, the denied tools |
| `{launch_lane}`, `{launch_label}`, `{launch_status}` | where the launch put the ticket |
| `{state}`, `{state_label}`, `{status}` | where the ticket was before the launch |
| `{note}` | the note, as plain text |
| `{note_quoted}` | the note, as a quote, for a project's own brief layer |
| `{ticket_text}` | the ticket file |
| `{hard_rules}`, `{checks}`, `{checks_line}`, `{granted}`, `{write_paths}`, `{read_only}`, `{look_on_screen}` | from the rulesets |
| `{lane:<role>}`, `{label:<role>}` | the directory or the label of the board with that role: `start`, `active`, `blocked`, `verify`, `closed` |
| `{provider}`, `{provider_label}`, `{provider_limits}` | the AI provider the launch starts, its name, and what its launch cannot enforce, as rules |
| `{status:<role>}` | the status word the project uses for that status role, e.g. `{status:implementing}` |

A line that holds nothing but a placeholder whose value is empty is removed.
