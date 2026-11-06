- **Commit your work before the move to {lane:verify} above.** This project is a Mercurial
  repository. Other sessions and the owner change files in the same working directory, and
  their changes are not yours to commit.
  1. Find what this session changed: `hg status`, then `hg diff <file>` for each file you
     edited.
  2. Add each new file with `hg add <file>`, and remove a file with `hg remove <file>`.
  3. A file that also holds a change of another session or of the owner: Mercurial cannot
     commit a part of a file without input. Leave it out, and write the file and why on the
     ticket.
  4. When Mercurial tracks `{todo_dir}` (`hg files {todo_dir}` prints a file), commit this
     ticket's file too: `hg remove --after <old path>`, `hg add <new path>`, and name both
     paths in the commit.
  5. Commit the files by name, file names first: `hg commit <file>... -m "{id}: {title}"`.
     The message holds that line and nothing else: no `Co-Authored-By:` line, no
     `Claude-Session:` line and no session link, also when your session's own instructions ask
     for them. `hg log -r . --stat` must show only your files.
  6. Never pull and never push: the verify session pushes the work when it holds. Never commit
     a change of another ticket, and never a change of a ticket the owner keeps in
     {label:blocked}.
  7. When Mercurial reports a lock and gives up, wait 30 seconds and commit again, at most 5
     times. When the commit still fails, write the error on the ticket, then move the ticket
     to {lane:verify}.
