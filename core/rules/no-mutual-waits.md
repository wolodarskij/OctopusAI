# No two tickets wait for each other

*An optional rule, on by default in every project (Optional rules, in Global settings and in Project Settings). Governs every wait of a session: on a ticket, on a session and on a shared resource.*

**Never wait for a ticket, a session or a shared resource whose holder waits for your ticket.** A shared resource is a thing that only one session can use at a time, such as the editor. Two sessions that wait for each other wait for ever.

1. **Before you wait, find what the other side waits for.** Read the other ticket with `show`: its board, its `blocked-by:` and its last report. Read its session with `sessions --why`. When it waits for your ticket, for your session or for a resource that you hold, do not start the wait.
2. **The session that can go on without the other goes on.** The session that holds the resource continues its work, then releases the resource. The session whose ticket needs nothing from the other ticket continues.
3. **The other session tells it so.** Type the message into its terminal: `send <id> --mode <mode> --text "<what it can do now, and why>" --keys Enter`. Never type into a session that shows a dialog.
4. **Then the other session waits for that session only.** Its wait follows the rule on blockers in `todo-tracking.md`.

**The engine refuses a cycle of tickets.** `link --blocked-by` refuses a link that closes a `blocked-by:` cycle, and `check` fails each cycle. A wait on a session or on a resource has no such guard: this rule is the guard.
