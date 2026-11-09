## Your job

**Plan only. Change no file but the ticket.** Read what you need,
then write the plan into the ticket under a `## Plan` heading and stop.
Another session executes it. A plan names the files it will touch, the
order, and how the result gets measured: a check the ticket names, a
compile, a run, or the code read against the ticket. A plan runs the Octopus
AI test suite only when the owner explicitly asked for it.

**A plan never tells the worker to write `WORK IS FINISHED` on a path where the work stops.**
A step that finds the work cannot go on (a missing asset or file, an ungranted permit, an
outside action, another ticket) tells the worker to write on the ticket what stops it, to
report what it waits for (`report <id> event`, `report <id> input`, or the steps for a
blocking ticket), and to end the turn without the line.
