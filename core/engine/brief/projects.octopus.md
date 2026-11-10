## An Octopus AI change reaches every project through this ticket

This ticket holds `projects` and `outside`. **Carry the Octopus AI change into every
registered project yourself, to the end.** File no ticket in another project: `new` refuses
it, and no project fixes or updates Octopus AI for itself.

1. For each project in the table below, run `{octopus} update "<Root>"`.
2. Make the edits that each project needs: an own rule, skill or doc of the project that
   contradicts the change. `.octopus/installed.json` of the project lists the files that
   Octopus AI installs; every other file under `.agents/` and `.claude/` is the project's own.
3. In a project that is a git repository, commit only the files that the update and your
   edits changed: `git -C "<Root>" add -- <file>`, then `git -C "<Root>" commit -m "<id>: <title>"`,
   with the id and the title of this ticket. Never commit a change of another session or of
   the owner. Never pull and never push in a `work` session.
4. Name each project and its commit under `## Result`: one line each.

A `verify` session pushes each commit that step 3 names, as it pushes this project's work:
`git -C "<Root>" pull --no-rebase`, then `git -C "<Root>" push`.

{projects}
