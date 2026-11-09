### Close this terminal

When the work is done, the ticket is in {label:verify} and you have no question for the
owner, the last command of the session is `{cli} close {id} --mode work --self`, after every
item above. Then end your last message with `WORK IS FINISHED`. This terminal closes when the
turn ends. A session whose ticket is anywhere else never runs it and never writes
`WORK IS FINISHED`: it waits in {label:blocked} for an outside cause or holds the check on a
blocker, or it asks the owner a decision.

