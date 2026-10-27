## This ticket is repeatable: run {run}

It is a {repeat_label} ticket: it {repeat_meaning}. It is done again and again, and this
session is run {run}.

- **Do the job again, against the tree as it stands today.** Every earlier
  `## Result` and every line under `## Runs` is history. Read it to compare,
  and never copy a finding forward without measuring it again.
- Write this run's outcome under `## Result (run {run})`, and leave the earlier
  results where they are.
- `## Plan` is the standing procedure of every run. Change it only when the
  procedure itself is wrong, and say so under this run's result.
- A verification that holds does not close this ticket. `{cli} move {id} {lane:closed} --verified`
  records the run as passed and returns the ticket to {label:start} for the next
  run. Only the owner retires it.

