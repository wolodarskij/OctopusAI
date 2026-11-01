- **Commit the work when it holds.** This project is an svn working copy, and an svn commit goes
  to the repository at once. Other sessions and the owner change files in the same working copy,
  and their changes are not yours to commit.
  1. **It holds:** after your move to {lane:closed}, take the files of this ticket: the list under
     `## Changed files` on the ticket, and `svn status --changelist {id}`. Add each file under
     `## Shared files` whose `svn diff <file>` now shows only this ticket's change. Commit no
     other file.
  2. Run `svn update --accept postpone <file> <file>` on those files only. When a file ends in
     conflict (`C` in `svn status`): commit nothing, leave the conflict in place, and write the
     conflicting files under `## Verified`. The ticket stays in {lane:closed}.
  3. Commit them: `svn commit -m "{id}: {title}" <file> <file>`. A file inside an external is
     in another working copy: commit the files of each working copy root
     (`svn info --show-item wc-root <file>`) in one command each, with the same message. The
     message holds only that line: no `Co-Authored-By:` line, no `Claude-Session:` line and no
     session link, also when your session's own instructions ask for them.
  4. When svn tracks `{todo_dir}` (`svn info {todo_dir}` succeeds), commit this ticket's file as
     `{id}: verified`: `svn delete` each old path of it that `svn status {todo_dir}` shows as `!`,
     `svn add` its new path, then `svn commit -m "{id}: verified" <old path> <new path>`.
  5. When svn reports a working copy lock (`E155004`), or the commit is out of date because the
     repository moved: wait 30 seconds, update the ticket's files again, then commit again, at
     most 5 times. Write the revision of each commit under `## Verified`.
  6. **It does not hold:** commit nothing and update nothing. The changelist stays for the next
     work session.
  7. Never `svn commit` with no file, never `svn revert`, never `svn cleanup --remove-unversioned`,
     never `svn switch` or `svn merge`, and never resolve a conflict with `theirs-full` or
     `mine-full`.
