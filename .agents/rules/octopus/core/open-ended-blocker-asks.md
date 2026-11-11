# A ticket that another ticket waits for asks the owner at its end

*Always applies, in every project, to every session of a ticket that another ticket waits for.*

**When the owner decides where the work of a ticket ends, and another ticket waits for it, its session asks the owner explicitly when it did all its work.** The owner decides the end of the work in an open discussion with the session, or with a launch note that leaves the end to the owner. Another ticket waits for it when that ticket names it in `blocked-by:`.

1. **Ask with the question dialog.** Run `report <id> input --why "<the question>" --mode <mode>`, then ask with the `AskUserQuestion` tool: does more work come, or is the ticket finished? The session is then `input`, and the manager's update lists it under Waiting for your input, never under Working.
2. **Never end such a turn with a plain message.** A message that asks nothing leaves the session waiting for nobody, and the ticket that waits for it stops.
3. **A background monitor is no work of the session.** A watch of an artifact's live updates, or another monitor, never makes the session `working`.

**The engine enforces it, for every session whose ticket another open ticket waits for:**

- The `Stop` hook holds the stop once in a turn, and tells the session to ask the owner. It holds it when the ticket does not show its work done, is not in Blocked or Paused, and the turn ends with no finished line, no statement that the ticket is done, no report of `input` or `event`, and no work of its own: a subagent, a shell that runs, or a recurring job. A monitor is no such work.
- The scan of the sessions counts no monitor of the session as its own work. The judge's list of what the session waits for leaves the monitor out.
