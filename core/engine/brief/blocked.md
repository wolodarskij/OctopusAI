## When another ticket blocks this one

1. `{cli} link {id} --blocked-by <blocker>`, then
   `{cli} move {id} {lane:blocked}`. Write on the ticket what the blocker
   has to land.
2. `{cli} show <blocker>`: the board is in brackets on its first line.
   When the board is {label:start} and the ticket carries no `session:`, launch a worker
   on it: `{cli} work <blocker> --note "<what this ticket needs from it>"`, or
   `plan <blocker>` when its `difficulty` is `unknown`. **Never wait passively on a
   blocker that nobody works.** A wait is only for a blocker that has a session.
3. Schedule a check every 10 minutes with the `CronCreate` tool, `cron`
   `3,13,23,33,43,53 * * * *`, `recurring` true, and this prompt, with the ids
   filled in:
   `Run {cli} show <blocker> and read the board in brackets on its first line.
   When it is {label:verify} or {label:closed}: run {cli} move {id} {lane:active}
   --status "{status:implementing}", delete this cron job with CronDelete, and
   continue the ticket from where it stopped. When it is {label:start} and the
   ticket carries no session: line: run {cli} work <blocker>, or {cli} plan <blocker>
   when its difficulty is unknown. Otherwise: do nothing.`
4. End the turn and wait. Do not exit the session: the job fires only while the
   session is open and idle, and it expires after seven days.

**Read only what this ticket needs.** A sweep of the code for other instances
of the same shape is a separate ticket, and it is not this one.

