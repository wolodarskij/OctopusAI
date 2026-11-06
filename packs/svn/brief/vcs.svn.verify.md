- **Commit the work when it holds.** This project is a Subversion working copy. Other
  sessions and the owner change files in the same working copy, and their changes are not
  yours to commit.
  1. **It holds:** after your move to {lane:closed}, read `svn status --changelist {id}`.
     When the work of this ticket is not in the changelist, put it there, as a work session
     does: `svn changelist {id} <file>...`, only the files of this ticket.
  2. When svn tracks `{todo_dir}` (`svn ls {todo_dir}` prints a file), put this ticket's file
     into the changelist: `svn delete <old path>`, `svn add <new path>`, then
     `svn changelist {id} <old path> <new path>`.
  3. Update only the files of the changelist: `svn update --changelist {id} --accept postpone`.
  4. When `svn status --changelist {id}` shows a conflict (`C`), commit nothing: write the
     files under `## Verified`. The ticket stays in {lane:closed}.
  5. Run `svn commit --changelist {id} -m "{id}: {title}"`. The message holds only that
     line: no `Co-Authored-By:` line, no `Claude-Session:` line and no session link.
  6. When svn reports a lock, or the commit fails because a file is out of date: wait 30
     seconds, update again as in step 3, then commit again, at most 5 times. When it still
     fails, write the error under `## Verified`.
  7. **It does not hold:** commit nothing. The changelist stays.
  8. Never commit without `--changelist`, never `svn revert`, and never
     `svn cleanup --remove-unversioned`.
