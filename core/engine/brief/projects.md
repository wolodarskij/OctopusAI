## A change for every project reaches every project

This ticket holds `projects`: it may change other registered projects and file
tickets in them. **Carry the change into each project to the end.** The owner
never has to launch a session in another project by hand.

- **File each follow-up with its session:** `new --title "..." --launch`, with
  the command in the project's row below. It files the ticket and launches `work`
  on it, or `plan` when its `difficulty` is `unknown`. Give it `--note` with what
  the follow-up needs from this ticket. Do the same in every project the change
  reaches.
- **A follow-up filed without a session** gets one now: `work <id>`, or
  `plan <id>` when its difficulty is `unknown`, with the same command.
- **Name every follow-up on this ticket**, under `## Follow-ups`: one line each,
  project, id and title.

{projects}

