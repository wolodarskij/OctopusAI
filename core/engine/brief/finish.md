- Report the files this ticket changed. Name a problem only if one prevented
  the ticket from being executed.
- **When you need the owner's answer:** run `{cli} report {id} input --why "<the question>" --mode {mode}`,
  then ask with the `AskUserQuestion` tool, never in plain text. The board then keeps this
  terminal open, wherever the ticket is, and shows the question and its options.
  - One dialog holds up to 4 questions. Put a longer list into several dialogs, one after the
    other.
  - Give each question its options, with a short description of each option.
  - The question and its options state only proven facts, or mark a claim "Unverified:".
    Recommend an option only when proven facts support it, and never an option that does only
    a part of the work. A recommended option stands first.
  - Put the context the owner needs into the question and the option descriptions, or into a
    short message before the dialog.
- **When you wait for something outside the project, not for an answer**, such as an
  editor restart: run `{cli} report {id} event --why "<what you wait for>" --mode {mode}`,
  say what you wait for, and end the turn without a question. The board shows the session
  as waiting for that event and keeps this terminal open.
- **When the work of this ticket is finished and you have no question for the owner,** end
  your last message with this exact line, alone, with nothing after it: `WORK IS FINISHED`.
  The board then shows this session as finished, and this terminal closes at once.
- **`WORK IS FINISHED` only when the work is finished.** The engine trusts the line and
  checks nothing else: it closes this terminal for good. Never write it while any part of the
  ticket is undone, while you wait for an event (an editor restart, a build, a background
  job), or while a question to the owner is open. Saying it early is the worst fault a
  session can make: the owner loses the agent in the middle of the work.
- **Never leave out `WORK IS FINISHED` when the work is finished.** This holds in every
  launch mode, also in a turn after the owner's answer, after a message of the owner, after
  a resume of this session, and after the event or the background job you waited for. The
  last message of the turn that finishes the work ends with the line.
- **A session that cannot do its work never writes `WORK IS FINISHED`.** A missing asset or
  file, an ungranted permit, an outside action, a tool or a server that fails, another ticket
  or a decision of the owner stops the work, and stopped work is not finished.
- **First try every way to remove the cause.** Search the project for what is missing, use
  another tool or another route, correct what fails inside this ticket's scope, and run the
  failed step again. Stop only when no way works.
- **When no way works, the ticket goes to {label:blocked}, and the session waits:**
  - **An outside cause** (a missing asset, file or value after the search, an ungranted
    permit, an outside action, a tool, a server or a lock that fails):
    `{cli} report {id} blocked --why "<the cause>" --tried "<every way you tried>" --mode {mode}`.
    It moves the ticket to {label:blocked} `{status:blocked by outside cause}`, writes the cause
    and the ways you tried on the ticket, and reports the session as waiting for that event.
    End the turn without the line, and keep this terminal open. When the owner or the event
    removes the cause, run `{cli} move {id} {lane:active} --status "{status:implementing}"`
    and continue the work.
  - Another ticket: the steps under **When another ticket blocks this one**.
  - A decision that only the owner can make: `report ... input` and the question dialog.
- **Never offer, propose or ask to do only a part of the work.** No part commit, no part
  delivery, no smaller scope, and no question whether a part is enough. Only the owner, on
  the owner's own order, says that a part is enough. When a part of the order cannot be done,
  the ticket goes to {label:blocked} with the cause, as above.
- **A launch note never makes stopped work finished.** A note that tells you to ask the
  owner nothing, or not to use the question dialog, does not permit the line on stopped
  work. An outside cause goes to {label:blocked} as above. A decision of the owner: write the
  question on the ticket, run `report ... input` with it, end your last message with the
  question as its last line, and keep this terminal open.
- **A ticket whose session did not carry out its order to the end is {label:blocked}, never
  finished.** The order is the ticket, its `## Owner's orders` and the note of this launch. An
  action that the order names (a run, a setup, a connection) is the work, not a test: a note
  that forbids tests never forbids it. When you do not carry out an action of the order, for
  any cause, run `report ... blocked` with the cause. Never move the ticket to
  {label:verify}, and never write `WORK IS FINISHED`. `move` refuses a `## Result` that says
  an action was not carried out ("The script was not run"). When a session ends with its
  ticket's work not done, the engine moves the ticket to {label:blocked}.
- **Work is never finished while a question is open.** When you cannot do the work without
  the owner (a value only the owner can give, a decision, a word), that is a question, not a
  finish: report `input`, ask it with the `AskUserQuestion` tool, and keep this terminal open.
- **A plan is never finished while it cannot be executed.** A plan that leaves a decision, a
  name or a value open is not done: ask the owner, and set `ready to implement` only when a
  worker can carry out every step without asking.

