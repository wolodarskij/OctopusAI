---
title: Manager: waits for the owner and runs tickets in every project
category: internal tooling
severity: Low
difficulty: small
permits: projects, outside
repeat: manager
remote-control: on
---
## What it should do

The board's persistent manager. It belongs to the Octopus AI project, the registered project whose root is the
Octopus AI checkout. Its `work` session stays open, waits for the owner's orders, and calls and creates
tickets in every registered project through the `projects` permit. The board starts it when it opens, and its
watchdog restarts the session when the terminal dies. It never enters Verification, Blocked or Done. Retire it only
as `cancelled`.

Its instructions are the manager sections of the brief, `core/engine/brief/repeat.manager.md` in the Octopus AI
repository. A project's own `.octopus/brief/repeat.manager.md` replaces them.
