---
name: todo
description: Ask about tickets and act on them from chat - list, filter, count, read, move, set severity/difficulty/category/permits, create, group into an epic or a parent with children, and launch a Claude CLI session to work, plan, re-evaluate or verify a ticket. Use whenever the owner asks what is open, what is blocked, what to start next, what a ticket says, what is still running, or tells you to start, park, close, re-size or re-evaluate one.
---

# The Agent Board, from chat

`python .octopus/todo.py` does everything the board does. The Agent Board itself opens with
`./AgentBoard.bat` or `python "./octopus.py" board`. Policy is in `.agents/rules/octopus/core/todo-tracking.md`.

- **Answer from one call.** `query` takes every filter at once. Do not read `TODO/INDEX.md` to
  answer a question.
- **Act, then say what happened.** "Start T0001" is the order to move it and launch it.
- **Give the answer.** "How many are left?" is a number. "What next?" is three or four lines: id,
  severity, and what breaks.

## Questions

    python .octopus/todo.py query [words...] [filters]

| Filter | Takes |
|---|---|
| `--state` | a board directory; repeat for several |
| `--status` | a status word; `statuses` lists them per board |
| `--severity` | `High` `Medium` `Low` |
| `--difficulty` | `small` `medium` `large` `unknown` |
| `--category` | `project code` `internal tooling`, and the project's own `categories` |
| `--permit` | a permit of this project; `permits` lists them |
| `--tag`, `--not-tag` | keep, or drop, the tickets with this tag |
| `--epic`, `--group`, `--source` | an epic, a group, a substring of `source` |
| `--parent <id>`, `--parents`, `--roots` | the children of a ticket, the tickets with children, the tickets with no parent |
| `--related-to <id>` | the tickets one link away |
| `--claimed` `--unclaimed` `--blocked` | who holds it, and whether it names a blocker |
| `--startable` | in Pipeline and sized |
| `--count` `--ids` `--json` | the number, the ids, the full records |
| `--limit N` `--width N` | the row count, the title width |

Bare words are an AND search over id, title, source, body, tags, permits and status.

| The owner says | The call |
|---|---|
| "what next" | `query --startable --severity High` |
| "what is open" | `query --state Pipeline --count`, then the top rows |
| "anything blocked" | `query --blocked` |
| "what is in flight" | `query --state InProgress` |
| "what is unsized" | `query --difficulty unknown --count` |
| "anything about the parser" | `query parser --state Pipeline` |
| "what touches module.ts" | `query --source module.ts` |
| "the cheap high ones" | `query --severity High --difficulty small` |
| "what is under T0001" | `children T0001` |
| "read me T0001" | `show T0001` |

## Actions

| The owner says | The call |
|---|---|
| "start T0001" | `move T0001 InProgress --status implementing` |
| "park it, T0002 lands first" | `link T0001 --blocked-by T0002`, then `move T0001 Blocked` |
| "block T0001, it waits for me" | `move T0001 Blocked --status "blocked by user" --owner-order "<the order, in neutral words>"`, only on the owner's order in that message |
| "cancel T0001" | `move T0001 Done --status cancelled --owner-order "<the order, in neutral words>"`. It also closes the ticket's open sessions |
| "close T0001, T0002 fixed it" | `move T0001 Done --status "closed by other task" --owner-order "<the order, in neutral words>"` |
| "it is written and not yet proven" | `move T0001 Verification` |
| "it is done" | `verify T0001` |
| "that one is not real" | `move T0001 Verification`, and write the reason on the ticket |
| "this one is small" | `size T0001 small` |
| "it is worse than that" | edit `severity:`, then `index` |
| "that is a tooling thing" | edit `category: internal tooling`, then `index` |
| "it needs <permit>" | edit `permits:`, then `index` |
| "file this" | `new --title "..." --severity High --source src/module.ts:118 --description "..."` |
| "split T0001" | `new --title "..." --parent T0001` per part |
| "file it and start it" | `new --title "..." --launch`: `work`, or `plan` when the difficulty is `unknown` |
| "these belong together" | `epic <name> --title "..."`, then `move <ids> Pipeline --epic <name>` |
| "relate T0001 and T0002" | `link T0001 --related T0002` |

**An order the owner gives in this chat counts the same as a drag on the board or a command in
the owner's shell.** Carry it out. Never ask the owner to drag a card or to type a command for it.

**After a hand edit, run `index`, then `check`.** `check` must pass before you report.

## Launching a session

    python .octopus/todo.py work    T0001 --note "..."   plan, then execute
    python .octopus/todo.py plan    T0001 --note "..."   write the plan, change nothing
    python .octopus/todo.py review  T0001 --note "..."   judge the ticket itself
    python .octopus/todo.py verify  T0001 --note "..."   prove it, then close it or send it back
    python .octopus/todo.py restart T0001 --note "..."   the next run of a repeatable ticket
    python .octopus/todo.py manager --start              the manager, from any project

- **The manager belongs to the Octopus AI project.** `manager`, `manager --start`, `manager
  --claim` and `watchdog` act on that project's manager ticket from any project, and the board
  starts only that one. Never file or start a manager ticket in another project.
- Each writes `TODO/.briefs/<id>.md` and opens `claude` in a new terminal on it.
- **`--note` carries the owner's order, in neutral words.** Pass it when the owner says how to do
  the work. Never copy the owner's message into it.
- The model and the effort come from `defaultModel` and `defaultEffort` in `.octopus/project.json`.
  `--model` and `--effort` (`low` `medium` `high` `xhigh` `max`) override them on the owner's order.
- The ticket's `permits` limit the session. If the work needs a permit, grant it on the ticket
  first and say so.
- In a project with `vcs: git`, a `work` session commits its own files and hunks before it moves
  the ticket to Verification, and a `verify` session pulls and pushes the work when it holds.
- `--terminal cmd|wt|own|none` picks the terminal. `own` is a hidden console that the board holds.
  `none` and `--dry-run` print the launch line.
- Every launch writes its session id into the ticket under `sessions:`. `claude --resume <id>`
  restores it.

**Say which mode you used.** For an `unknown` ticket, use `plan` or `review`: nobody can execute a
ticket that nobody can size.

## Terminals

    python .octopus/todo.py sessions                      every session that still has a terminal
    python .octopus/todo.py sessions --input              only the sessions that wait for the owner's input
    python .octopus/todo.py focus  T0001                  bring it to the front
    python .octopus/todo.py screen T0001 --mode work      print its screen
    python .octopus/todo.py send   T0001 --text "yes" --keys Enter
    python .octopus/todo.py close  T0001                  close it
    python .octopus/todo.py close --finished              close every terminal whose session is finished
    python .octopus/todo.py report T0001 input --why "Which board?" --mode work   the session asks the owner this question
    python .octopus/todo.py report T0001 event --why "editor restart" --mode work   ... or for an outside event

- `focus`, `screen`, `send` and `close` take a ticket id, or the first 8 or more characters of a
  session id for a session that has no ticket.
- `send` refuses a session where a dialog waits (a question, a permission, a plan to approve):
  only the owner answers a dialog, in that terminal.
- A session has one of seven states: `running` (its turn runs; `sessions` names what it does
  now, its last tool call), `input` (waits for the owner's answer), `event` (waits for something
  outside the project, such as an editor restart), `working` (`subagent` in the JSON: its
  scheduled job, its own subagents or background shells, or the judge says it still works),
  `blocker` (the same, while its ticket waits for other tickets), `idle` (nothing says yet what
  it waits for) and `finished` (waits for nothing). The first step that matches decides: Claude
  has exited, `finished`; no hook has run and no transcript exists (a question before the first
  turn, such as folder trust), a dialog, an API error, or a reported `input`, `input`; a
  reported `event`, `event`; a `plan` turn that leaves a decision open, `input`; the turn ended
  with `WORK IS FINISHED`, `finished` (a manager `idle`), or `input` while its ticket says the
  work is undone; the turn ended with a question on its last line, `input`; a recurring job or its own
  background work, `working` or `blocker`; the turn runs and wrote within 5 minutes, or a tool
  call of it has no result yet, `running`;
  else the judge reads the last message and the ticket, and when it is unsure the engine types
  `/recap` into the session and the judge reads the recap. The ticket's board decides nothing
  else.
- **Only an open session in the `input` state waits for the owner.** `sessions --input` and
  the manager's "Waiting for your input" list nothing else. A finished or closed session never
  waits for input, even when its ticket's result names a decision for the owner.
- `--finished` and the board's `Close finished` close only `finished`. A question, a
  reported event or a running subagent keeps the terminal open wherever the ticket is.
- **`close <id> --force` ends a running session** and loses its unsaved work. Ask the owner first.
- These commands need terminal control from a ruleset, such as `windows`.

## The board

The board opens every registered project. `W`, `P` and `R` launch work, plan and review on the
selected card, each with a box for the note. `N` files a ticket. `G` changes the view: `Boards`,
`Groups`, `Sessions`, `Repeatable`, `Project` (every setting of the project). `/` opens the search. The `Groups` view creates, renames,
deletes and fills groups, by hand or with AI.

## Do not

- Do not edit `TODO/INDEX.md`.
- Do not move a ticket to Done. `move <id> Done` refuses every caller except a verification
  session. Send finished work to Verification.
- Do not close a defect because a rule tells an agent to spend no time on it. Close it only when
  the code no longer has it, and name the file and the line.
