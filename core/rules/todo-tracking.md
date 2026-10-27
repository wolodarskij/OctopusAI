# TODO tracking policy

*Always applies. Governs the `TODO/` ticket tree at the project root. The Octopus AI engine runs it:
`python .octopus/todo.py <command>`, or `python "{{OCTOPUS}}/octopus.py" todo --project {{NAME}}
<command>` from any directory.*

## One ticket, one file. The directory is the state.

```
TODO/
  INDEX.md              generated - never hand-edit
  Pipeline/             nobody has started it
    T0001-short-slug.md
    some-epic/          an EPIC: a directory that moves as one unit
      _epic.md
      T0002-....md
  InProgress/           somebody works on it now
  Blocked/              another ticket, or the owner, stops it
  Verification/         the work landed and is not yet proven
  Done/                 proven
```

**These are the default boards.** A project renames boards and adds boards in `lanes` in
`.octopus/project.json`. The engine needs five roles, and each role names exactly one board:

| Role | Default board |
|---|---|
| `start` | Pipeline |
| `active` | InProgress |
| `blocked` | Blocked |
| `verify` | Verification |
| `closed` | Done |

This rule uses the default names. Read each name as its role. The open boards are every board
before the verify board, except the closed board.

**A move of a ticket is a move of its file.** `move`, a drag on the board and `mv` all do it.
No other place records the state, so nothing can disagree with the tree.

    python .octopus/todo.py board                    the boards, side by side
    python .octopus/todo.py list --state Blocked
    python .octopus/todo.py show T0001
    python .octopus/todo.py move T0001 Verification --status testing
    python .octopus/todo.py new --title "..." --severity High --source src/module.ts:118
    python .octopus/todo.py check                    exit 1 when anything is wrong

## `status` is a subset of the directory

The directory gives the board. `status` gives the place inside the board, and who put the ticket
there. `check` enforces the subset. The first status of each board is its default. A move that
names no status lands on it.

| Board | Status | Set by |
|---|---|---|
| Pipeline | `pipeline` | `new`, the board's `New task`, any move to Pipeline, and a `verify` session that finds the work does not hold |
| InProgress | `waiting` | a move that names no status, such as a drag. No session has the ticket |
| InProgress | `investigating` | a `review` session, at its start |
| InProgress | `planning` | a `plan` session, at its start |
| InProgress | `ready to implement` | a `plan` session, when the plan is on the ticket |
| InProgress | `implementing` | a `work` session, at its start, and the check of a blocked session when the blocker lands |
| Blocked | `blocked by other task` | a session that finds another ticket must land first. `blocked-by:` names that ticket |
| Blocked | `blocked by user` | the owner's direct order only |
| Verification | `waiting` | a `work` session when it finishes, and any move to Verification |
| Verification | `testing` | a `verify` session, at its start |
| Done | `done` | a `verify` session, when the measurement holds |
| Done | `closed by other task` | the owner, or a `verify` session that finds another ticket fixed this one |
| Done | `cancelled` | the owner, when nobody wants the ticket any more |

A project can map a status role to another word (`statusRoles` in `project.json`).

**`blocked by other task` names its blocker in `blocked-by:`.** `check` fails when it does not.

**Only the owner's direct order sets `blocked by user`.** A session never infers it. A ticket that
waits for an answer from the owner stays in its board, and the session asks the owner. `move` and
`new` refuse the status to a Claude session that does not pass `--owner-order`.

**A launched session that meets a blocker:**

1. links the blocker and moves its own ticket to Blocked;
2. launches a worker on the blocker when the blocker is in Pipeline with no session: `work
   <blocker>`, or `plan <blocker>` when its difficulty is `unknown`;
3. schedules a `CronCreate` job every 10 minutes that reads the blocker's board with `show`. When
   the blocker is back in Pipeline with no session, the job launches the worker again;
4. stays open. When the blocker is in Verification or Done, the job moves the ticket back to
   InProgress `implementing` and deletes itself, and the session continues.

**Never wait passively on a blocker that nobody works.** A wait is only for a blocker that has a
session. The brief carries the exact commands.

**A change for every project reaches every project.** A session whose ticket holds `projects` and
changes several registered projects carries the change into each of them to the end. Every
follow-up ticket it files in another project gets its session from the filing session, the same
way in every project: `new --project "<name>" --title "..." --launch` files it and launches
`work`, or `plan` when its difficulty is `unknown`. A follow-up filed without a session gets one
at once. The filing session names every follow-up on its own ticket, under `## Follow-ups`. The
owner never launches them by hand. The brief of a ticket that holds `projects` carries the
registered projects and their commands.

## `permits` says what a ticket may use

**Permits come from the rulesets that `.octopus/project.json` lists.** The core ruleset defines
two permits. `outside`: read or write files outside this project. `projects`: call and create
tickets in every registered project. A pack adds its own permits. Every session may do
everything that no permit guards.

    python .octopus/todo.py permits       the permits of this project, and how many tickets grant each
    python .octopus/todo.py rulesets      the rulesets in order, and what each adds

- An absent permit adds the tools it guards to `--disallowedTools` on the launched session, so
  the attempt fails.
- A granted permit can add a directory with `--add-dir`: the path in its `addDirEnv` variable,
  else its `addDir`. A permit with `addDirRegistry` (`projects`) adds the root of every other
  registered project.
- A `repeat: test` ticket holds the project's `testerPermits` on every run, whatever its
  `permits:` line says.

**Remote control.** A Claude launch runs with Claude Code's Remote Control unless it is off:
`remoteControl` in `project.json` is the default, `remote-control: on|off` on a ticket wins, and
`remote-control <id> on|off|default` or the board's card menu sets it. `on` reaches a live
session at its prompt at once, the rest the next launch. Codex has none. The manager
should keep it on; the board says so when it is off.

**Do not work around an ungranted permit.** Write the permit on the ticket, move the ticket to
InProgress `waiting`, and stop.

## Working a ticket in its own Claude session

    python .octopus/todo.py plan   T0001    writes the plan and changes nothing
    python .octopus/todo.py work   T0001    plans, then executes
    python .octopus/todo.py review T0001    judges the TICKET itself
    python .octopus/todo.py verify T0001    proves the work; the only mode that closes a ticket

- Each writes `TODO/.briefs/<id>.md` and launches `claude` in a new terminal on it. The brief
  carries the ticket, the permits, and the steps to finish.
- A `plan` asked for by name runs `--permission-mode plan`. Every other launch runs
  `--permission-mode auto`. This includes a plan that a group run chose for an `unknown` ticket.
- A plan leaves `## Plan` on the ticket. A later `work` finds that plan and executes it.
- Each mode has its own session id, from the project's session namespace and the ticket id.
  Every launch writes one `<mode> <uuid> <time>` entry under `sessions:`, and the last launch in
  `session:` and `launched:`. `claude --resume <uuid>` restores that agent from the ticket.
- **`review` judges the ticket.** Is it still true against the tree? Does it describe what is
  wrong now? Are `severity` and `difficulty` correct? Its verdict: leave it, rewrite it, narrow
  it, send it to Verification, or move it to Blocked. It gets no permit, and it changes only the
  ticket.
- All four take `--note`: the owner's order to that session, in neutral words. The note goes
  into the brief above the generic steps and outranks them.

**A session is in one of seven states.** `running`: its turn runs. `input`: it waits for the
owner's answer, and only when it explicitly waits for it. `event`: it waits for something
outside the project, such as an editor restart; it reported it with `report <id> event --why
"<the event>" --mode <mode>`, and nobody has to answer it. `working` (the engine's word is
`subagent`): it still works on what is not the owner's, its scheduled job, its own subagent or
background shell, or the judge says it still does its own work; Claude Code calls it back when
that work completes. `blocker`: the same, while its ticket waits for other tickets (`blocked by
other task`, a ticket in its `blocked-by:`, an open child). `idle`: nothing says yet what it
waits for. `finished`: it waits for nothing.

**The engine decides the state in this order, and the first step that matches wins:**

| Step | The session | State |
|---|---|---|
| 0 | its Claude exited | `finished` |
| 0 | no hook ran and it has no transcript: a question before its first turn, such as folder trust | `input` |
| 0 | a dialog waits (a permission, a question, a plan to approve), or its turn ended in an API error (a rate limit, an overload, a network fault) | `input` |
| 0 | it reported `event`, and did not end with `WORK IS FINISHED` | `event` |
| 0 | it reported `report <id> input --why "<the question>" --mode <mode>` in this turn. A `WORK IS FINISHED` after it does not take the question back: work is never finished while a question is open | `input` |
| 0 | a `plan` session's turn ended, its ticket is `ready to implement`, and the plan leaves a decision, a name or a value open: no `## Plan`, difficulty still `unknown`, a line of the plan that asks or names an open decision (a plan that a worker cannot carry out is not finished) | `input` |
| 1 | its turn ended, and the last line of its last message is `WORK IS FINISHED` (every brief asks for it when the session has no question), or its last lines say its ticket is in Verification or Done, or it reported `finished` | `finished`; a manager `idle` |
| 1 | the same, while its ticket says the work is undone: a `work` ticket not in Verification, Done or Blocked `blocked by other task`; a `plan` ticket not `ready to implement`; a `review` or `verify` ticket still `investigating` / `testing` | `input`, "said WORK IS FINISHED with the work undone" |
| 2 | its turn ended, and the last line of its last message is a question | `input` |
| 3 | it holds a recurring job, or its own subagent or background shell runs | `blocker` when its ticket waits for other tickets, else `working` |
| 4 | its turn runs and wrote to its transcript within 5 minutes, or a tool call of its turn has no result yet | `running` |
| 5 | its turn ended, or it wrote nothing for 5 minutes and no tool call runs: the judge reads its last message and its ticket | the verdict: `input`, `finished`, `working` or `blocker` |
| 6 | the judge is unsure: the engine types `/recap` into the session once, and the judge reads the recap | the verdict on the recap; `idle` when it is unsure again or no recap comes within 2 minutes |

Where its ticket stands is never a question to the owner. It decides a state only in the two
steps above that read it, a plan left open and a finished line over undone work; elsewhere the
judge reads it with the last message. A question on the last line is `input` whatever the session waits
for. The finished line outranks a recurring job. A manager never finishes while its Claude
runs: it waits for the owner's orders. The engine finds the jobs in two records and counts every
job either names: the hook's record of each `CronCreate` and `CronDelete`, and the session's
transcript, where each `CronCreate`, its job id, each `CronDelete` and each firing of a job
stand. A resumed Claude keeps its jobs. A job expires 7 days after it was made, unless it fired
within the last 2 hours.
**A Claude session asks the owner with the question dialog.** It runs `report <id> input --why
"<the question>" --mode <mode>`, then asks with the `AskUserQuestion` tool, never in plain text:
the dialog stops the turn, and the session is `input` until the owner answers. The answer, the
dialog's tool result in the transcript, removes that report, so the session goes on and can finish. An AI provider with no question dialog asks on
the last line of its message, and the line ends with `?`.
**The transcript corrects the hook files.** A turn that began without the owner's prompt (a
scheduled prompt, a call back after background work) runs, whatever the last `Stop` said. A turn
that ended by its transcript and has no `Stop` marker gets the one the hook would have written.
`sessions` and the board show the state, and for a running or working session what it does now:
its last tool call, `running · Bash: run the tests`. `sessions --input` and the board's
`Waits for input` filter show only the sessions that wait for `input`. `sessions --why` and the
board's `Reload` decide every session again, start again a judge that left no verdict or a recap
that never came, and show each signal behind the state: a signal that never came is a missed
trigger. `Reload` opens no window: the board redraws in place, and the hover on a session's
marker lists its signals. `close --finished` and the board's `Close finished` close only
`finished`.
`close <id> --mode <mode> --self` is a session's last command: it closes the session's own
terminal once the turn ends, and only when the session is `finished`. Every launch mode
(`plan`, `work`, `review`, `verify`, and the runs of a repeatable ticket) ends this way.
**A finished session closes even when it did not run it.** Its `Stop` hook closes it at once,
with no wait for the state word, when its last message ends with a believable `WORK IS
FINISHED` (step 1, and no question reported in the turn), and once it is `finished` when its
last message says its ticket is in Verification or Done; the
judge's `finished` closes it; and the board closes every other session that is `finished` on
two checks in a row, every 30 seconds. Only a session launched on a ticket closes this way.
A session in `input`, `event`, `working`, `blocker` or `idle`, a running one, a manager's,
and one the owner opened with no ticket stay open.

**The judge** reads what no step before it decides: a last message with no question on its last
line, no `WORK IS FINISHED`, no statement that the ticket is in Verification or Done, and a turn
that has written nothing for 5 minutes and runs no tool call, with its last action. A turn that
wrote no message at all gives it nothing to read: it stays `running`. It uses the project's
default provider once per turn: `claude -p` on Haiku for Claude Code, or `codex exec` with the
project's Codex model and effort. It answers `input`, `finished`, `working` or `unsure`. Only a question the
session itself asks the owner, or an answer it says it cannot go on without, is `input`: a
report, a status table, and other sessions' questions that it lists are not. It also reads
Claude Code's last recap of the session and the jobs and agents it waits for: a session whose
message or recap says it runs a recurring task is `working`, never `finished`. `unsure` says
the text does not show what the session waits for; the engine then asks the session for its
recap. **Every verdict but `unsure` explains itself and quotes its proof**, words copied from
the message, the recap or the list of what it waits for: for `input` the open question, for
`finished` the line that said the work was done, for `working` the line that names what it
waits for. A verdict whose quote stands nowhere, or an `input` that quotes another ticket's
question from a list, is rejected; the judge is asked once more and told why. The hover on
`waits for input` names the question: the dialog's, with the options of each question, the
reported one, the one on the last line, or the judge's quote. It also reads the turns that ended
before it existed. Its `finished` never closes a session that has no ticket. A session whose
hook files are gone is read back from its transcript.

**An AI provider is the tool a launch starts** (Claude Code, Codex, a custom CLI); **an
agent is one running session of it** on a ticket. `provider` in `project.json` names the AI
provider a launch starts unless `--provider` names another.

**Model and effort.** Each AI provider lists the models and efforts it can run, in global
settings. A launch uses the defaults of the AI provider it starts: Claude's are `defaultModel`
and `defaultEffort` in `project.json`, another's are `providers.<id>`. `--model` and `--effort`
override them, only on the owner's order, and only with a model and an effort of that AI
provider; any other is refused and never reaches its command line.

### The outcome line

**Every ticket that leaves a `work` or a `verify` session carries `outcome:`**, one line its
session writes: what now works, or what was found, in at most 20 words. It holds no
measurement, no caveat and no other ticket; those go into the ticket's body. `move <id>
<lane> --outcome "..."` writes it with the move, and `outcome <id> "..."` alone. Both refuse
a line that breaks this. `check` fails a ticket in the verify or the closed lane that has
none, when its last `work` or `verify` launch came after the outcome line began; a
`cancelled` ticket and a manager need none. A passed or failed run of a repeatable ticket
clears it. The outcome is a field of the ticket, never a line of the last message: that
message still ends with `WORK IS FINISHED`.

    python .octopus/todo.py move T0001 Verification --outcome "The board shows the outcome line"
    python .octopus/todo.py outcome T0001 "The board shows the outcome line"

### Only a verification session closes a ticket

**A session that did the work never closes its own ticket.** It lands the work, moves the ticket
to Verification, and stops. A separate `verify` session measures the work, then closes the
ticket or sends it back. A `review` that judges a ticket dead sends it to Verification with its
reasons.

**`move <id> Done` refuses every caller except these:**

- a `verify` session. Its launch sets the project's `verifyEnv` variable (default
  `OCTOPUS_VERIFY`) to `<sessionNamespace>:<id>`, and the guard reads it;
- `--verified`, written by hand for a verification that the owner ran;
- the owner's own shell, outside every Claude session, for `closed by other task` and
  `cancelled`;
- a session the owner ordered in its chat, for `closed by other task` and `cancelled`: it
  passes `--owner-order "<the order, in neutral words>"`. The move writes that statement and
  the time under `## Owner's orders` on the ticket. A session that acts on its own order gets
  the refusal.

A drag on the board is the owner's hand. The engine does not guard it, except for a manager
ticket (below).

**An order the owner gives in chat counts the same as a drag on the board or a command in the
owner's shell.** The session that receives it carries it out at once with `--owner-order`.
Never ask the owner to drag a card or to type a command for an action the session can do on
the owner's order. A move to Done as `cancelled` or `closed by other task` also closes every open
session of the ticket, so no agent waits on a closed ticket.

| The session | Where it leaves the ticket |
|---|---|
| `work` | Verification `waiting`, its work committed first, then it closes its own terminal |
| `plan` | InProgress `ready to implement`, with the plan on the ticket, then it closes its own terminal. A plan session whose ticket is `ready to implement` and that asks nothing is `finished`, and its `Stop` hook closes its terminal even when it did not |
| `review` | its old board, Verification `waiting`, or Blocked, then it closes its own terminal |
| `verify` | Done `done` when the measurement holds, and the work pushed; Done `closed by other task` when another ticket fixed it; Pipeline when it does not hold, and nothing pushed; then it closes its own terminal |

**A close needs a measurement by a session that did not write the fix.** Use a test, a check
command, a compile, or the code read against the ticket.

### A work session commits, a verify session pushes

**In a project with `vcs: git`, the work of a ticket reaches the remote when its verification
holds.** The brief sections `vcs.work` and `vcs.verify` give the exact steps.

- **A `work` session commits its own work** before it moves its ticket to Verification: only the
  files and the hunks it changed, as `<id>: <title>`. A file that another session also changed is
  committed by hunk: a patch of its own hunks, then `git apply --cached`. `git add -p` waits for
  input, so no session uses it. A `work` session never pulls or pushes.
- **A `verify` session pushes what holds.** It commits the ticket's work when it is still not
  committed, then runs `git pull --no-rebase` and `git push`. On a lock or a rejected push it
  waits 30 seconds, pulls and pushes again, at most 5 times. On a conflict it aborts the merge,
  pushes nothing, and writes the conflict on the ticket. When the measurement does not hold, it
  pushes nothing.
- **No session commits a change of another ticket or of the owner,** and never a change of a
  ticket the owner keeps in Blocked.
- **A project with `vcs: none`, or with no `vcs`, commits nothing.** A Diversion project has no
  backend yet, so it commits nothing.
- **With the `git` pack,** a `work` launch holds `git-commit`, and a `verify` launch holds
  `git-commit` and `git-sync`, whatever the ticket says (`modePermits`).

## A repeatable ticket returns to Pipeline

**A repeatable ticket is a job that runs again and again.** A pass does not close it. It carries
`repeat:` with one of four roles, and `run:`, the number of its current run.

| `repeat` | The board calls it | The job |
|---|---|---|
| `test` | Tester | runs a test or a measurement and reports the result |
| `verify` | Verificator | proves the tickets that wait in Verification |
| `board` | Board management | keeps the ticket tree in order: groups, sizes, old tickets |
| `manager` | Manager | stays open, waits for the owner's orders, and runs tickets in every project |

**A Board management job reviews old tickets with `review-old`.** It takes the tickets in the
start and the blocked board created more than `--days` ago, and skips repeatable tickets, tickets
with an open session, and tickets reviewed within `--days`. `run` launches `review` on a batch of
`--batch`, waits until each session of the batch is `finished`, closes it, and prints one line
per ticket: where the review left it.

    python .octopus/todo.py review-old list --days 5             the tickets a sweep takes
    python .octopus/todo.py review-old run --days 5 --batch 10   review them, a batch at a time

One run goes through the boards like any ticket: `work`, then Verification, then `verify`.

| The verification | Result |
|---|---|
| holds: `move <id> Done --verified` | the run is `pass`, and the ticket returns to Pipeline |
| does not hold: `move <id> Pipeline` from a verify session | the run is `fail`, and the ticket is in Pipeline |
| the owner drags it to Done | the same as a pass |

Each end writes one line under `## Runs`, sets `last-run` and `last-result`, raises `run` by one,
and clears `session`, `launched` and `claim`. **Only `cancelled` or `closed by other task` retires
a repeatable ticket.** Only the owner sets them.

    python .octopus/todo.py new --title "..." --repeat test    file one
    python .octopus/todo.py repeat T0001 verify                make a ticket repeatable; `none` undoes it
    python .octopus/todo.py restart T0001                      start the next run in a fresh session
    python .octopus/todo.py restart T0001 --no-launch          return it to Pipeline and start nothing
    python .octopus/todo.py repeatable                         every repeatable ticket and its last result

`restart` records a run in progress as `restarted`. The brief gives the run number. The outcome
goes under `## Result (run <n>)`. `## Plan` is the procedure of every run. The `Boards` view and
the groups leave repeatable tickets out. The `Repeatable` view shows them.

### A manager ticket never finishes

**A `manager` ticket has no runs to pass or fail.** Its `work` session stays open and waits for
the owner. It grants `projects`, so the session reaches every registered project with
`python "<octopus.py>" todo --project "<name>" <command>`; its brief lists them.

- **It has two places.** Pipeline: not started. InProgress `implementing`: it runs. `move`, a
  drag on the board and `new` refuse Verification and Blocked for it, and Done unless the status
  is `cancelled` or `closed by other task`. `check` fails it in any of those.
- **It runs only as `work`.** `plan`, `review` and `verify` refuse it.
- **It ships with Octopus AI.** The ticket is `core/templates/manager.md`; its instructions,
  the standing orders for every manager, are the brief section `repeat.manager`.
- **It belongs to the Octopus AI project:** the registered project whose root is the Octopus
  AI checkout, which the first run registers. `manager`, `manager --start`, `manager --claim`
  and `watchdog` act on the manager of that project, from any project. `manager` files the
  ticket from the template when that project has none; `manager --start` also launches it
  unless it runs. A manager ticket in any other project is never started.
- **The board starts it when it opens,** whichever project is open. One manager serves every
  project. Each manager of the Octopus AI project in InProgress whose session is not running
  starts again. With none in InProgress, the board starts the manager of the Octopus AI
  project, filed from the template when that project has none. A retired manager is never
  filed again nor started.
- **The board's Manager button reaches it.** The button stands center-right in the project band
  whenever a project is open. It brings the terminal of the Octopus AI project's manager to the
  front, whichever project is open, and shows its ticket in the Octopus AI project. When the
  manager does not run, it starts it as the board does when it opens.
- **The board's watchdog restarts it.** Every 30 seconds the board server reads the process
  table. A manager ticket of the Octopus AI project in InProgress whose `work` session has no
  shell, or no Claude under its shell, is dead. On the second dead check in a row, and not
  within 60 seconds after a launch, the watchdog runs `work` on it again, which resumes the
  same session id. After 5
  restarts within 10 minutes it stops for that ticket until the session runs again; the card
  shows `watchdog stopped`. `watchdog` prints the same check, and `watchdog --once` restarts
  the dead ones by hand.
- **The owner stops it** with `restart <id> --no-launch`, which returns it to Pipeline until the
  board opens again, and retires it with `cancelled` or `closed by other task`, also by an
  order in chat (`--owner-order "<the order, in neutral words>"`). `restart <id>` starts it in
  a fresh session.
- **Any session becomes the manager** when the owner says "run manager" or calls the skill
  `/octopusmanager`: `manager --claim` records that session, in any registered project, as the
  `work` session of the Octopus AI project's manager. The hooks of a session from another
  project write in the Octopus AI project from then on, so the watchdog guards it and the
  board starts no second manager. While a manager runs in any registered project, the claim
  names it and refuses; `--take-over` closes it first.
- **Every update of the manager lists agents, not tickets,** in four sections, always all four
  and in this order, an empty one as `none`: Finished since the last update, Waiting for your
  input, Working, Other. `octopus sessions --since-last` builds it from every registered
  project, against a snapshot it keeps outside every repository. Each agent is one line, and
  the manager copies it and never summarizes a ticket: a finished agent is `<project> <id>
  "<title>" — <outcome>; <lane> / <status>; <commit> pushed|not pushed`, a waiting one its
  question word for word, a working one `<project> <id> "<title>" — <what it does now, at most
  10 words>`. **Waiting for your input lists only an agent whose session is open and in the
  `input` state.** A finished, closed or idle agent never stands there, and nor does a decision
  that a finished ticket's result names: that agent stands only under Finished. The reply check
  fails a Waiting line about any other agent.
- **The manager's 10-minute check is always on.** Every manager session keeps a recurring
  job that runs `octopus check` every 10 minutes: it makes the job at its start and after
  every restart, and makes it again before Claude Code expires it after 7 days (the check
  exits 3 then). Each run launches every follow-up that a ticket holding `projects`, in
  Verification or Done, names under `## Follow-ups` and nobody launched, and prints the
  full update whenever any agent finished since the last run, waits for the owner, works or
  waits for something else, each waiting question word for word on every run until it is
  answered. Only when no agent finished, none waits and none works does the manager write
  nothing at all; it never writes "no change" or any line like it. It starts no other
  ticket and answers no agent. A manager on an AI provider
  with no scheduler of its own (Codex) gets the check from the board's watchdog,
  which types it into the manager session every 10 minutes.

    python .octopus/todo.py manager                    the Octopus AI project's manager ticket, from the template when there is none
    python .octopus/todo.py manager --start            start it unless it runs
    python .octopus/todo.py manager --claim            this session becomes the manager, from any project
    python octopus.py sessions --since-last            the manager's update, every project
    python octopus.py check                            the 10-minute check: follow-ups, then the update while any agent finished, waits or works
    python .octopus/todo.py watchdog                   every manager ticket of the Octopus AI project, alive or dead

## Parent and children

A ticket that is too big for one session gets children.

    python .octopus/todo.py new --title "..." --parent T0001    file a child
    python .octopus/todo.py children T0001                      the children and their boards; exit 1 while one is open
    python .octopus/todo.py wait T0001                          hold until every child is in Verification or Done
    python .octopus/todo.py query --parent T0001                also --parents, --roots

`check` fails a parent in Done while a child is still open.

## Groups

A group holds open tickets that one session can work together. `groups.store` in
`project.json` selects the store:

| Store | Where a group lives |
|---|---|
| `map` (default) | `TODO/.groups.json` maps a group name to `group:` slugs and ticket ids. `TODO/GROUPS.md` is generated. `autoGroups` adds a group for each tag it names |
| `files` | one file per group, `TODO/groups/<slug>.md`. Each ticket names its group in `group:`. With `groups.required`, `check` fails an open ticket in no group |

    python .octopus/todo.py groups                   rebuild, and list the open tickets in no group
    python .octopus/todo.py groups --table           every group with its open count (map store)
    python .octopus/todo.py groups --set "<name>" <slug or id>...   place tickets (map store)
    python .octopus/todo.py groups --run "<name>"    a session on every Pipeline ticket of a group
    python .octopus/todo.py group <slug> --title "..."              create or retitle
    python .octopus/todo.py group <slug> --rename <new>             rename
    python .octopus/todo.py group <slug> --delete [--into <other>]  delete
    python .octopus/todo.py regroup T0001 T0002 <slug>              put tickets in a group

## The ticket file

```markdown
---
id: T0001
title: "Bug: <what is wrong>" or "Feature: <what the program should do>"
severity: High | Medium | Low
difficulty: small | medium | large | unknown
status: pipeline
source: src/module.ts:118
created: 2025-03-14
claim:
blocked-by:
permits:
tags: parser
---

## What is wrong

One or two paragraphs. Name a function with its parameters and its return type.

## Evidence

The file and line you read, or the command you ran.
```

**The title says whether the ticket is a bug or a feature.** A bug title starts with `Bug: ` and
says what is wrong: "Bug: The board drops a ticket that has no severity". A feature title starts
with `Feature: ` and says, as a positive statement, what the program should do: "Feature: Illness
should mutate a person". Never write a feature as a negative. A feature ticket has the section
`## What it should do` in place of `## What is wrong`. `new`, the board's `New task` and its
`Process with AI` write the section that the title names.

Other fields: `category`, `parent`, `group`, `related`, `repeat`, `run`.

**`category` says what the ticket is about.** The defaults are `project code` and `internal
tooling`. `categories` in `.octopus/project.json` adds categories, or rewords the default ones.
A ticket with no `category:` is `internal tooling` when `source` starts with a `toolingPaths`
prefix of a ruleset, else `project code`. The old field `kind` fails `check`; `migrate`
rewrites it to `category`.

The id is permanent and never used again. The file name carries it, and `check` fails when the
two disagree. `TODO/.ids` holds the highest id issued.

## Severity, difficulty, and priority

**Severity is what the user loses. Difficulty is what the fix costs.** They are independent. A
ticket that mixes them gets worked in the wrong order. A project can reword both scales in
`severityMeaning` and `difficultyMeaning`.

### Severity

Ask: **what does a user or the saved data get wrong because of this?** Effort never enters it.

| Severity | The user or the data |
|---|---|
| **High** | gets a wrong result, loses data, or the program stops |
| **Medium** | gets a correct result today by accident: two copies of one rule that agree for now, a validation that nothing runs |
| **Low** | gets nothing wrong: a name, a duplicate, a missing comment |

**The test between High and Medium: can a user see it now?** A wrong number on screen is High.
Two functions that both compute it correctly are Medium, because the defect is the next edit.

Never raise severity because the fix is urgent to you. Never lower it because the fix is hard.

### Difficulty

Ask: **how much is unknown, and how many sites does the change touch?** Count sites. Do not count
error lines.

| Difficulty | |
|---|---|
| **small** | one file, one site, and the change is known |
| **medium** | several sites in one layer, and the change is known |
| **large** | many sites, or several layers, and the change is known |
| **unknown** | nobody can size the change: the intent, the measurement or the decision is missing |

**`unknown` and `large` are different answers.** `large` means the change is known and big.
`unknown` means the ticket's question has no answer yet, so nobody can start it. Answer the
question, then size the ticket. `list --unknown` finds them. `size <id> <difficulty>` sets it.

**Difficulty is not permission.** A one-line fix that needs a permit is `small`, with the permit
in `permits:`.

### Priority is derived

    severity first, then difficulty ascending

The board, the index and every listing use this order. `unknown` sorts last at every severity.
There is no priority field. If a ticket must go first, its severity is wrong: correct the
severity.

## The index is derived

Every mutating command rewrites `TODO/INDEX.md`, and the `SessionStart` hook rebuilds it. `check`
exits 1 when it does not match the tree. **Never hand-edit `INDEX.md`.** Edit the ticket, then run
`python .octopus/todo.py index`.

## An epic is a directory

Put tickets that only make sense together in one directory inside a board, with an `_epic.md` that
says what the epic is for. `epic <name> --title "..."` creates it. `move --epic <name> <board>`
moves all of them at once.

## When analyzing code

When the user asks you to **analyze** code, at any scope:

- Find inconsistencies, bugs, missing implementations, code smells and improvements.
- **Create a ticket in Pipeline** for each finding.
- Do **not** fix them. At the end, say "Added N ticket(s)."

**Ask before you log an ambiguous finding.** A finding is ambiguous when the code, the comments
and the project documentation do not show the intent:

- code that looks wrong but can be intentional: an unusual algorithm, a deliberate no-op, a
  placeholder;
- a pattern that can be a bug or a design choice;
- anything where you assume how the code should work.

Log only what you are sure of: a clear bug, a broken documented convention, or a fault that the
code around it makes obvious.

## When NOT to touch `TODO/`

- Normal edits, features and refactors that the user asked for: log nothing.
- Do not move a ticket out of Pipeline unless the user asks, or you do its work.
- **Never delete a ticket to mark it finished.** It moves to Done with the status that says how it
  closed.

## Managing the tree on request

| Request | Action |
|---|---|
| add | `new` |
| start | `move <id> InProgress` |
| park | `move <id> Blocked`, and fill `blocked-by:` |
| finish | `move <id> Verification`, and stop. `verify <id>` closes it |
| reprioritize | edit `severity:`, then run `index` |
| remove | delete the file, only when the user asks. The id is not used again |

**Claim a ticket before any work:** set `claim:` to your agent id.
