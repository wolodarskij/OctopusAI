## Your job

**Re-evaluate this ticket. You are not fixing the code.**

Judge it against the tree as it stands today and answer three questions:

1. **Is it still true?** Read the code the ticket names. The defect may be
   fixed, or the subject may be gone.
2. **Does it describe what is wrong now?** A ticket whose subject moved
   is live but mis-sourced. A ticket whose defect partly closed overstates.
3. **Are `severity` and `difficulty` right?** Severity is what the user loses.
   Difficulty is how much is unknown and how many sites it touches.

Then do exactly one of these:

| Verdict | What you do |
|---|---|
| still true, well written | change nothing, say so |
| still true, badly written | rewrite `title`, the body, and `source` |
| partly closed | rewrite it to name ONLY what survives, and lower the severity |
| no longer true | write why in the body, then `{cli} move {id} {lane:verify}`. **You may not close it** - only a verification session moves a ticket to `{lane:closed}` |
| another ticket must land first | `{cli} link {id} --blocked-by <blocker>`, then `{cli} move {id} {lane:blocked}` |
| the owner must decide | keep the board, run `{cli} report {id} input --why "<the question>" --mode {mode}`, then ask the owner with the `AskUserQuestion` tool. Only the owner's order sets `blocked by user` |

**The only file you may change is this ticket.** If the re-evaluation turns up
a defect the ticket does not cover, make a NEW ticket with `{cli} new` and leave
the code alone.

