## This session runs on {provider_label}

No hook reads what this session does, so the board knows only what you report.

- **When you finish,** run `{cli} report {id} finished --mode {mode}` before the last command of the
  session. Without it the board shows this session as running until its process ends.
- **When you need the owner's answer,** the `report {id} input` command below is the only way the board
  learns of the question.
- **{provider_label} has no question dialog.** Where this brief says to ask with the `AskUserQuestion`
  tool, ask in plain text: the question is the last line of your message and ends with `?`.
- **The rules of this project** are the files `AGENTS.md` lists. Read each before your first change.
{provider_limits}

