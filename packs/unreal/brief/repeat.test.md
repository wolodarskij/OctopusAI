### A Tester ticket runs PIE on its own authority

**A Tester ticket holds `pie` and `editor` on every run, without a message from the user, and this
brief is the request that starts run {run} of {id}.** The rule that PIE needs the user's message in
chat, in `shared-editor.md` and `todo-tracking.md`, names this ticket kind as its one exception. Do
not stop at it.

The order of a run:

1. Claim `pie` and `editor` as shared resources in `.claude/coordination/CLAIMS.md`, and post one
   line to `.claude/coordination/MESSAGES.md` to `all`: PIE runs, do not start PIE and do not save
   any hot-reloaded script file until the DONE block of {id}. Append with a quoted heredoc; if the
   shell append is denied, append with the Edit tool. Both files are yours to append to.
2. Check nothing is already in PIE: `LogsToolset.GetLogEntries(category="",
   pattern="UEDPIE|EndPlayMap", maxEntries=6)` - a `UEDPIE_0_` line with no later `EndPlayMap`
   means it is live, and then you wait, you do not start a second.
3. Run the measurement the ticket names, and record what it measures.
4. Post the release line to `MESSAGES.md` and the DONE block to `CLAIMS.md`.

