## When you finish

{checks_line}
- Run the Octopus AI test suite only when the owner explicitly asks for it. A
  ticket line such as "tests and docs pass" is no such request.
- `{cli} move {id} {lane:verify} --outcome "<what now works>"` and nothing further.
  The outcome is one line of at most 20 words on the ticket: what now works. It holds no
  measurement, no caveat and no other ticket; those go into the ticket's body. `move`
  refuses an outcome that breaks this. `octopus sessions` shows the outcome to the owner,
  so it is never a line of your last message: that message still ends with
  `WORK IS FINISHED`.
- **You may not close this ticket.** Only a verification session moves a
  ticket to `{lane:closed}`, and `move ... {lane:closed}` refuses you.
