## A change for every project reaches every project

This ticket holds `projects`: it may change other registered projects and file
tickets in them. **Carry the change into each project to the end.** The owner
never has to launch a session in another project by hand.

**An Octopus AI change is never a follow-up.** No ticket of this project or of
another project installs, updates or fixes Octopus AI there: the Octopus AI
ticket carries its change into every project itself, and `new` refuses such a
ticket. A defect of an Octopus AI tool goes to `notify`.

- **File each follow-up with its session:** `new --title "..." --requested "<the
  owner's order for every project, in neutral words>" --launch`, with the command in
  the project's row below. It files the ticket and launches `work` on it, or `plan`
  when its `difficulty` is `unknown`. Give it `--note` with what the follow-up needs
  from this ticket. Do the same in every project the change reaches.
- **A follow-up filed without a session** gets one now: `work <id>`, or
  `plan <id>` when its difficulty is `unknown`, with the same command.
- **A ticket that `new` sends to {label:ai} is no follow-up.** `--launch` files it
  and launches nothing. Launch no session on it, and do not name it under
  `## Follow-ups`.
- **Name every follow-up on this ticket**, under `## Follow-ups`: one line each,
  project, id and title.

{projects}

