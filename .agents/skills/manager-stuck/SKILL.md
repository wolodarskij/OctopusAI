---
name: manager-stuck
description: The manager's hourly evaluation. The engine types `/manager-stuck` into the Octopus AI manager session once an hour when a running ticket or a blocker is stuck. Run the evaluation, print its findings, run the commands it names, and end the turn.
disable-model-invocation: true
---

# The manager's hourly evaluation

1. Run `python "./octopus.py" stuck`.
2. Print what it prints on stdout as it is, and nothing else: the section Stuck agents and
   blockers. A line marked `only you can decide it` is for the owner. When it prints nothing,
   write nothing.
3. Run each command it prints on stderr: the launch of a blocker that nobody works, the next
   step to a stuck session, the launch of a ticket whose blockers landed. Write nothing about
   the commands. When `send` refuses a session because a dialog waits, leave it: only the
   owner answers a dialog.
4. End this turn immediately. Keep the session open for the owner.

Do not call sleep, poll, close the session, or schedule another evaluation. The engine owns the
timer.
