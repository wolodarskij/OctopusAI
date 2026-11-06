- **Commit the work when it holds.** This project is a Diversion workspace. A commit goes to
  the branch and to every workspace at once. Other sessions and the owner change files in the
  same workspace, and their changes are not yours to commit.
  1. **It holds:** after your move to {lane:closed}, read the files under `## Files` on the
     ticket. Leave out each file marked `shared with`, and write it under `## Verified`.
  2. When Diversion tracks `{todo_dir}` (`dv status {todo_dir}` names it), add this ticket's
     file to the commit: its old path and its new path.
  3. Commit the files by name, file names first: `dv commit <file>... -m "{id}: {title}"`.
     The message holds that line and nothing else: no `Co-Authored-By:` line, no
     `Claude-Session:` line and no session link, also when your session's own instructions ask
     for them.
  4. When the commit fails: wait 30 seconds, then commit again, at most 5 times. When it still
     fails, write the error under `## Verified`. Resolve no conflict. The ticket stays in
     {lane:closed}.
  5. **It does not hold:** commit nothing.
  6. Never `dv commit -a`, never `dv reset`, never `dv restore`, and never
     `dv checkout --discard-changes`.
