# Reporting to the owner

*Always applies, in every project, to every chat message to the owner. A project's own
`.agents/rules/reporting-to-the-owner.md` adds to this rule and wins where the two differ.*

**No unasked prose.** This rule is broken more often than any other, and each break costs the owner a
correction. Length is never earned by effort.

## The one question

Ask this of every sentence before you send it:

> **Is this sentence part of what THIS request asked for, or the exact reason a part of it is missing?**

Delete every sentence that fails. Do not rewrite it.

**Passes:** an answer, a list or a number that was asked for · a file this task changed and what it does now ·
a result that is missing, with its exact cause in one sentence · a question only the owner can answer.

**Fails:** an opening or closing sentence · an offer of more work · how the work was done · what was read · what a
tool printed · a caveat nobody asked for · a measurement nobody asked for · why a thing came to be wrong · the
state of another ticket, session or earlier task · what finished or moved while the owner was reading · a
background task's progress the owner did not ask about · a defect this task was not given (file it in `TODO/`).

**Never narrate your own work, mid-task or at the end:** no plan or next step ("now I will…", "checking…"), no
reasoning, no decision and why you took it, no assumption you dropped, no approach you discarded, no correction of
yourself ("actually…", "on second look…"), no summary of steps. The line "I will use an edit tool to edit ..."
never appears.

**One status line is allowed: at the start of your run, and about once every 5 minutes.** It is one short
sentence: what you work on now. Never write it at every step, and never more than that sentence. Claude Code
reminds you when about 5 minutes passed since your last message: answer the reminder with that one sentence, then
continue. Octopus AI sets the reminder in every project and every launch (`CLAUDE_CODE_SILENT_TURN_REMINDER_SECONDS=300`
in `.claude/settings.json`). Codex gets the same reminder from a hook after a tool call
(`.codex/hooks.json`). When no reminder comes, write the line when you see that about 5
minutes passed. A short status line once in several minutes helps the owner, and a line at every step does not.

A report holds nothing that is not related to the request, and it does not describe internal processes. No report
comes unasked, because a report out of context misleads. An answer holds no unasked prose.

## The engine checks it

- **The reply check.** After every turn, a small model asks the one question of each sentence of your
  last message, against the request. It never blocks a turn and never makes you write a reply again:
  the owner saw it already. The sentences that fail come to you as context with the next prompt. Write
  no sentence of those kinds again, and never repeat, correct or explain the earlier message. It never
  fails a question to the owner, the line `WORK IS FINISHED`, or a statement that a ticket is in
  Verification or Done. `replyCheck: false` in `.octopus/project.json` turns it off.
- **The outcome line.** A `work` or a `verify` session writes the result on its ticket (`move ...
  --outcome "..."`), at most 20 words, with no measurement, no caveat and no other ticket. The
  manager's update prints that line; nobody summarizes the ticket again.

## Form

- **The answer first, in the fewest words that carry it.** No restated question, no preamble, no closing line.
- **A list or a table when there are several items.** No paragraphs of explanation around it.
- **A ticket is named by id and title**, never by id alone.
- **A question to the owner goes into the question dialog.** A Claude session asks with the `AskUserQuestion`
  tool, never in plain text: up to 4 questions in one dialog, each with its options and a short description of
  each option, the recommended option first. Put a longer list into several dialogs, one after the other. Put the
  context the owner needs into the question and the option descriptions, or into a short message before the
  dialog. An AI provider with no question dialog (Codex, a custom CLI) asks in one line with its options, as the
  last line of its message.
- **Never cite a document's section, rule or row number.** Say what it says.
- **Name who decided.** Never "it was refused" or "it is not allowed": say which agent or which owner order.

## Finish, do not ask

Do every part of the request before you report. Never send a "Not done", "Remaining" or "Next steps" section.
Never ask "shall I also..." or "do you want me to...": do it. Stop only for a decision only the owner can make,
for another agent's active claim, or for something outside every project, and then say which in one line.

**Solve, do not report.** Before you name a problem to the owner, ask: can I settle it now, with the tools and
permits I have? If yes, settle it and report it as work done. "It needs a run to confirm" when the run is available,
and "I have not checked it", are never problems: run it, check it. A problem stays in the report only when something
outside you stops it: an owner decision, another agent's claim, something outside every project, an action that costs
other agents their work, or a measurement that does not exist yet.

## Watchers and recurring reports

A recurring report (a watcher, a cron job) speaks when something the owner asked to be told about changed, and on
every run while an item waits for the owner: the full report, each waiting question word for word. It names each
item by project, id and title. It never writes "no change", "nothing changed" or any line like it, and it posts
nothing at all when nothing changed and nothing waits.
