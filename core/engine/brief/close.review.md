### Close this terminal

When the verdict is done, the ticket stands where your verdict leaves it and you have no
question for the owner, the last command of the session is `{cli} close {id} --mode review
--self`, after every item above. Then end your last message with `WORK IS FINISHED`. This
terminal closes when the turn ends. A session that cannot give its verdict never runs it and
never writes `WORK IS FINISHED`: it tries every way first, then waits in {label:blocked} for an outside
cause (`{cli} report {id} blocked`), or asks the owner a decision.

