### Close this terminal

When the plan is on the ticket, the ticket is `{status:ready to implement}` and you have no
question for the owner, close this terminal. Always: a finished planner never stays open.

1. When the owner ordered the work too, `{cli} work {id}` first.
2. `{cli} close {id} --mode plan --self`
3. The plan is finished: end your last message with `WORK IS FINISHED`. This terminal
   closes when the turn ends.

A plan that leaves a decision, a name or a value open (a line that asks, `TBD`, an open
question, a difficulty still `unknown`) is not finished: ask the owner, and never write
`WORK IS FINISHED`. The same holds for a plan you cannot write because something stops it.
The `Stop` hook closes a finished planner's terminal even when you do not run the close.
