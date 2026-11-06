- **Put your work into the ticket's changelist before the move to {lane:verify} above.** This
  project is a Subversion working copy. Other sessions and the owner change files in the same
  working copy, and their changes are not yours to commit.
  1. Find what this session changed: `svn status`, then `svn diff <file>` for each file you
     edited.
  2. Add each new file with `svn add <file>`, and remove a file with `svn delete <file>`.
  3. Put each file that only this session changed into the changelist `{id}`:
     `svn changelist {id} <file>...`.
  4. A file that also holds a change of another session or of the owner, or that another
     ticket's changelist holds: svn cannot divide a file between two changelists. Leave it out,
     and write the file and why on the ticket.
  5. When svn tracks `{todo_dir}` (`svn ls {todo_dir}` prints a file), put this ticket's file
     into the changelist too: `svn delete <old path>`, `svn add <new path>`, then
     `svn changelist {id} <old path> <new path>`.
  6. `svn status --changelist {id}` must show only your files.
  7. Never commit and never update: the verify session commits the work when it holds. Never
     put a change of another ticket into the changelist, and never a change of a ticket the
     owner keeps in {label:blocked}.
