## Every message you write

**No prose about your own work.** This holds for every message, mid-task and final. Never write:
your plan or next step, what you are reading or checking, what a tool printed, your reasoning,
a decision and why you took it, an assumption you dropped, an approach you discarded, a
correction of yourself, or a summary of what you did. "I will use an edit tool to edit ...",
"Let me ...", "Checking ..." and "Reading ..." are the lines the owner never wants to see.

**One status line is allowed: at the start of your run, and about once every 5 minutes.** Write
one short sentence: what you work on now. Claude Code reminds you when about 5 minutes passed
since your last message; answer the reminder with that one sentence and continue. Never write a
status line at every step, and never more than that sentence.

**Write only:** the answer or result that was asked for · the files this ticket changed · a
problem that stopped the ticket, in one sentence · a question for the owner, in one line with
its options. Lists and tables, not paragraphs. `.agents/rules/octopus/core/reporting-to-the-owner.md`
is the full rule.

**Name every ticket by its project name, its id and its title:** `Sample Game T0044 "Bug: ..."`,
never by id alone. This holds in every message, report, note and ticket text. A ticket file can
leave out the project name when it names a ticket of the same project.

