---
name: manager-stuck
description: The manager's hourly evaluation. While its switch is on, the engine types `/manager-stuck` into the Octopus AI manager session once an hour when a running ticket is stuck. Run the evaluation, print its findings, run the commands it names, and end the turn.
disable-model-invocation: true
---

# The manager's hourly evaluation

A running ticket is a ticket whose session is open and that the owner did not pause, block or
cancel. Stuck means only: its work waits for something that cannot happen without the owner's
intervention, or cannot happen at all, or its `blocked-by:` chain is circular.

1. Run `python "./octopus.py" stuck`.
2. Print what it prints on stdout as it is, and nothing else: one short line per stuck ticket.
   When it prints nothing, write nothing. Never add a ticket that it does not name.
3. Run each command it prints on stderr: the next step to a session that waits for nothing,
   the launch of a blocker that nobody works. Write nothing about the commands. When `send`
   refuses a session because a dialog waits, leave it: only the owner answers a dialog.
4. End this turn immediately. Keep the session open for the owner.

Do not call sleep, poll, close the session, or schedule another evaluation. The engine owns the
timer. Turn the evaluation on or off only on the owner's order: `python "./octopus.py" stuck
--switch on|off`.
