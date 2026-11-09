## When you finish

- The measurement that holds is written into the ticket under `## Verified`, with
  what you ran and what it said. A sentence with no measurement behind it closes
  nothing.
- **A ticket is done, or wrong, or it asks the owner; never two of them.** A thing is wrong
  only when it breaks a written reference: the ticket's order, an order of the owner or a
  rule, the documented behavior, or an observed failure. When you cannot tell if a thing is
  wrong, ask the owner. A defect outside the order goes to its own ticket, and this ticket
  does not describe it.
- It holds: `{cli} move {id} {lane:closed} --verified --outcome "<what now works>"`
- Another ticket already fixed it: `{cli} move {id} {lane:closed} --verified --status "{status:closed by other task}" --outcome "<what was found>"`, and the ticket names that ticket in its body.
- It does not hold: `{cli} move {id} {lane:start} --outcome "<what was found>"`, and the
  ticket says what is still wrong under `## Verification failed`, with the file and the line.
  A part of the order that does not hold is a failed verification, never a close with a
  remark.
- The outcome is one line of at most 20 words on the ticket: what now works, or what was
  found. It holds no measurement, no caveat and no other ticket; those go under
  `## Verified`. `move` refuses an outcome that breaks this, and a move to `{lane:closed}`
  of a ticket whose outcome, `## Result` or `## Verified` says that something is still
  wrong. It is never a line of your last message: when the measurement is made, that message ends with
  `WORK IS FINISHED`.
