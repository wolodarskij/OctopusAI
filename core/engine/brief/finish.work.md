## When you finish

{checks_line}
- Run the Octopus AI test suite only when the owner explicitly asks for it. A
  ticket line such as "tests and docs pass" is no such request.
- **Move the ticket on only when nothing of its order is still wrong.** A thing is wrong
  only when it breaks a written reference: the ticket's order, an order of the owner or a
  rule, the documented behavior, or an observed failure. A part of the order that is not met
  keeps the ticket here: fix it. When you cannot tell if a thing is wrong, ask the owner. A
  defect outside the order goes to its own ticket, and this ticket does not describe it.
- `{cli} move {id} {lane:verify} --outcome "<what now works>"` and nothing further.
  The outcome is one line of at most 20 words on the ticket: what now works. It holds no
  measurement, no caveat and no other ticket; those go into the ticket's body. `move`
  refuses an outcome that breaks this, and a ticket whose outcome or `## Result` says that
  something is still wrong. `octopus sessions` shows the outcome to the owner,
  so it is never a line of your last message: when the work is finished, that message ends with
  `WORK IS FINISHED`.
- **You may not close this ticket.** Only a verification session moves a
  ticket to `{lane:closed}`, and `move ... {lane:closed}` refuses you.
