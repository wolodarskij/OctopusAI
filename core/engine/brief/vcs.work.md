- **Commit your work before the move to {lane:verify} above.** This project is a git repository.
  Other sessions and the owner change files in the same working tree, and their changes are
  not yours to commit.
  1. Find what this session changed: `git status --short`, then `git diff -- <file>` for each
     file you edited.
  2. A file that only this session changed: `git add -- <file>`. Never `git add -A`, `--all`
     or `.`, and never `git commit -a`.
  3. A file that also holds changes of another session or of the owner: stage only your own
     hunks. Write `git diff -- <file>` to a patch file in your scratchpad, delete every hunk
     that is not yours, then run `git apply --cached --check <patch>` and
     `git apply --cached <patch>`. Never `git add -p`: it waits for input.
  4. When git tracks `{todo_dir}` (`git ls-files -- {todo_dir}` prints a file), stage this
     ticket's file too, in each place where `git status --short -- {todo_dir}` shows it.
  5. `git diff --cached --stat` must show only your files. Then commit as `{id}: {title}`.
     The message holds that line and nothing else: no `Co-Authored-By:` line, no
     `Claude-Session:` line and no session link, also when your session's own instructions ask
     for them.
  6. Never pull and never push: the verify session pushes the work when it holds. Never commit
     a change of another ticket, and never a change of a ticket the owner keeps in
     {label:blocked}.
  7. When git reports a lock (`index.lock`), wait 30 seconds and commit again, at most 5
     times. When the commit still fails, write the error on the ticket, then move the ticket
     to {lane:verify}.
