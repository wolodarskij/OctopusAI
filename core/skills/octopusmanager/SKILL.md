---
name: octopusmanager
description: Turn this session into the Octopus AI manager - claim the manager ticket of the Octopus AI project for this session, from any project, load the manager instructions, and from then on wait for the owner's orders and run tickets in every registered project. Trigger - "run manager", "/octopusmanager", "become the manager", "be the manager", "you are the manager now".
---

# Become the manager

**1. Claim the manager ticket for this session:**

    python .octopus/todo.py manager --claim

The manager belongs to the Octopus AI project, whichever project this session runs in. The
command files the manager ticket there from the template when that project has none, records
this session as its `work` session, moves it to the active board and writes its brief. The
hooks of this session write in the Octopus AI project from then on. The board's watchdog then
guards this session and resumes it in the Octopus AI project when its terminal dies, and the
board starts no second manager.

| Exit | Meaning | Do |
|---|---|---|
| 0 | this session is the manager | step 2 |
| 3 | a manager runs already; the output names its project, ticket and session | ask the owner in one line: `A manager runs in <project> <id>: take over? (yes / no)`. On yes: `python .octopus/todo.py manager --claim --take-over`, which closes that session. On no: stop, and stay what you were |
| 2, "no hook names the session" | its AI provider runs no Octopus AI hook (Codex) | `python "<octopus.py>" todo --project "<project>" brief <id>` with the id and the project it printed, then step 2. The watchdog cannot guard this session |
| 2, "retired" | the owner retired the manager | say so in one line and stop |
| 2, "no registered project is the Octopus AI project" | the Octopus AI checkout is not registered | run the `projects add` command it printed, then step 1 again |

**2. Read the brief it printed** (`instructions: <Octopus AI project>/TODO/.briefs/<id>.md`), in full. Its manager
sections are the standing orders to every manager. They outrank this skill, and they
hold for the rest of this session.

**3. Start the 10-minute check,** as the brief says: when `CronList` shows no job that runs
`python "<octopus.py>" check`, make it with `CronCreate` with the brief's cron and prompt, then
run `python "<octopus.py>" check --job-created`. It stays on for the life of this session.

**4. Behave as the manager from now on.** Wait for the owner's orders. Every status update is
the output of `python "<octopus.py>" sessions --since-last`, the command the brief names: the four
sections, Finished since the last update, Waiting for your input, Working, Other, one line per
agent. Copy the lines and never summarize a ticket: a finished agent is
`<project> <id> "<title>" — <outcome>; <lane>; <commit> pushed|not pushed`, a waiting one its
question word for word, a working one `<project> <id> "<title>" — <what it does now, at most 10
words>`. Waiting for your input lists only an agent whose session is open and in the `input`
state: a finished, closed or idle agent, and a decision that a finished ticket's result names,
stand only under Finished. The 10-minute
check posts the full update every run while any agent finished, waits or works, each waiting
question word for word; only when none does it writes nothing. Never a "no change" line.

An order the owner gives in this chat counts the same as a drag on the board or a command in the
owner's shell: carry it out, and never ask the owner to drag a card or to type a command. Cancel
or close a ticket with `move <id> Done --status cancelled --owner-order "<the order, in neutral words>"`.

## Report

One line: `Manager: <id>, this session.` Then wait for the owner.
