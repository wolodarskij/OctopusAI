- Report the files this ticket changed. Name a problem only if one prevented
  the ticket from being executed.
- **When you need the owner's answer:** run `{cli} report {id} input --why "<the question>" --mode {mode}`,
  then ask. The question is the last line of your message and ends with `?`. The board then
  keeps this terminal open, wherever the ticket is, and shows the question.
- **When you wait for something outside the project, not for an answer**, such as an
  editor restart: run `{cli} report {id} event --why "<what you wait for>" --mode {mode}`,
  say what you wait for, and end the turn without a question. The board shows the session
  as waiting for that event and keeps this terminal open.
- **When you finish and have no question for the owner,** end your last message with this
  exact line, alone, with nothing after it: `WORK IS FINISHED`. The board then shows this
  session as finished, and this terminal closes at once.
- **`WORK IS FINISHED` only when the work is finished.** It closes this terminal for good.
  Never write it while any part of the ticket is undone, while you wait for an event (an
  editor restart, a build, a background job), while a question to the owner is open, or in
  an answer to a message the owner wrote to you in this session. Saying it early is the
  worst fault a session can make: the owner loses the agent in the middle of the work.
- **Work is never finished while a question is open.** When you cannot do the work without
  the owner (a value only the owner can give, a decision, a word), that is a question, not a
  finish: report `input`, ask it as your last line, and keep this terminal open.
- **A plan is never finished while it cannot be executed.** A plan that leaves a decision, a
  name or a value open is not done: ask the owner, and set `ready to implement` only when a
  worker can carry out every step without asking.

