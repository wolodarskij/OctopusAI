**Not granted: {ungranted}.**
An ungranted capability is not a thing to work around. If the ticket
cannot be finished without one, write on the ticket which permit it needs,
run `{cli} move {id} {lane:active} --status "{status:waiting}"`, then
`{cli} report {id} event --why "the owner grants the permit <name>" --mode {mode}`. End the
turn without `WORK IS FINISHED`: the work is not done, and this terminal stays open.

