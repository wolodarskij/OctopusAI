## When you finish

- The measurement is written into the ticket under `## Verified`, with what
  you ran and what it said. A sentence with no measurement behind it closes
  nothing.
- It holds: `{cli} move {id} {lane:closed} --verified --outcome "<what now works>"`
- Another ticket already fixed it: `{cli} move {id} {lane:closed} --verified --status "{status:closed by other task}" --outcome "<what was found>"`, and the ticket names that ticket in its body.
- It does not hold: `{cli} move {id} {lane:start} --outcome "<what was found>"`, and the
  ticket says what is still wrong, with the file and the line.
- The outcome is one line of at most 20 words on the ticket: what now works, or what was
  found. It holds no measurement, no caveat and no other ticket; those go under
  `## Verified`. `move` refuses an outcome that breaks this. It is never a line of your last
  message: that message still ends with `WORK IS FINISHED`.
