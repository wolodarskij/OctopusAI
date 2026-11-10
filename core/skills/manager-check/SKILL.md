---
name: manager-check
description: The manager's check. The engine types `/manager-check` into the Octopus AI manager session on news. Run the check, print its output as it is, decide its notices, and end the turn.
disable-model-invocation: true
---

# The manager's check

1. Run `python "{{OCTOPUS}}/octopus.py" check`.
2. Print its output as it is, and nothing else. When it prints nothing, write nothing.
3. Decide each notice it lists with the command it names. Write nothing about the notices.
4. End this turn immediately. Keep the session open for the owner.

Do not call sleep, poll, close the session, or schedule another check. The engine owns the timer.
