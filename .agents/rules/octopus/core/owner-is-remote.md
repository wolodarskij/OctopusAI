# The owner is remote

*Always applies, in every project, to every session and to every message, question and report to the owner.*

**Assume that the owner works from a remote device, and that nobody is at the PC.** This holds until the owner says that the owner is at the PC.

1. **Never propose or ask for an action at the PC.** An action at the PC is a command to type, a click, a drag, a dialog or a UAC approval to accept, a window to close, a key to press, or a physical action. This holds in every message, question, option, report and ticket text.
2. **Choose a route that needs no action at the PC.** Do the step yourself with your tools. When one route needs an action at the PC, search for another route that needs none: another tool, another command, a setting that you can change, a process that you can start or stop.
3. **When no route works without an action at the PC, the work stops.** Send the ticket to Blocked with the cause:

       python .octopus/todo.py report <id> blocked --why "<the step, and the approval or the action at the PC that it needs>" --tried "<every route you tried>" --mode <mode>

   The report names the step and its cause. It offers no action at the PC.
4. **A dialog on the PC is no question to the owner.** Never start a step that opens a dialog, a UAC prompt or a window that waits for a click at the PC.

**When the owner says that the owner is at the PC,** an action at the PC is a valid question for the rest of that session. Ask it with the question dialog.
