### Close this terminal

When the measurement is done, the ticket is in {label:closed} or back in {label:start} and you
have no question for the owner, the last command of the session is `{cli} close {id} --mode
verify --self`, after every item above. Then end your last message with `WORK IS FINISHED`.
This terminal closes when the turn ends. A session that cannot measure the work never runs it
and never writes `WORK IS FINISHED`: it tries every way first, then waits in {label:blocked} for an outside
cause (`{cli} report {id} blocked`), or asks the owner a decision.

