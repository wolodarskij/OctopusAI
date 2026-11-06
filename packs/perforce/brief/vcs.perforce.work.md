- **Put your work into the ticket's changelist before the move to {lane:verify} above.** This
  project is a Perforce workspace. Other sessions and the owner open files in the same
  workspace, and their changelists are not yours.
  1. Find the ticket's changelist: `p4 changes -s pending -l -c <client>`, with the client name
     from `p4 -F %clientName% info`. Its description starts with `{id}: `. When none exists, make
     it: `p4 --field "Description={id}: {title}" --field "Files=" change -o | p4 change -i`. The
     command prints the change number. Write `Changelist: <change>` on the ticket.
  2. Open each file before you change it: `p4 edit -c <change> <file>`. Open a new file with
     `p4 add -c <change> <file>`, and remove a file with `p4 delete -c <change> <file>`. A file
     you changed with no open: `p4 reconcile -c <change> <file>`.
  3. A file that another ticket's changelist holds: do not move it with `p4 reopen`. Write the
     file and that changelist on the ticket.
  4. When the depot holds `{todo_dir}` (`p4 files {todo_dir}/...` prints a file), open this
     ticket's file too: `p4 reconcile -c <change> <old path> <new path>`.
  5. `p4 opened -c <change>` must show only your files.
  6. Never submit, shelve or sync: the verify session submits the work when it holds. Never
     open a change of another ticket, and never a change of a ticket the owner keeps in
     {label:blocked}.
