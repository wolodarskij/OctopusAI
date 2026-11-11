## Night mode: from 00:00 to 08:00 local time

**In night mode no ticket waits for the owner's input.** `.agents/rules/octopus/core/night-mode.md` is the full rule.

- Ask the owner nothing: no question dialog, no `report {id} input`, no question at the end of a turn.
- Decide in place of the owner, from the rules, the ticket and the evidence, and go on with the work.
  Record each decision: `{cli} decide {id} --question "<the question>" --decision "<what you decided, and why>"`.
- A question that only the owner can answer: `{cli} morning {id} --question "<the question>"`, then do
  all the work that does not need the answer. When no such work is left:
  `{cli} report {id} event --why "the owner's answer in the morning: <the question>" --mode {mode}`,
  and end the turn with no question.
- The engine refuses the question dialog and `report {id} input` in night mode, and types the night
  order into a session that waits for the owner's input.

