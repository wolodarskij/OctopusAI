- **Push the work when it holds.** This project is a git repository. Other sessions and the
  owner change files in the same working tree, and their changes are not yours to commit.
  1. **It holds:** after your move to {lane:closed}, find this ticket's commit with
     `git log --oneline --grep "{id}:"`. When its work is still not committed, commit it now,
     as a work session does: stage by name only the files of this ticket, and only its own
     hunks of a shared file (a patch, then `git apply --cached --check <patch>` and
     `git apply --cached <patch>`). Commit as `{id}: {title}`.
  2. When git tracks `{todo_dir}` (`git ls-files -- {todo_dir}` prints a file), commit this
     ticket's file, in each place where `git status --short -- {todo_dir}` shows it, as
     `{id}: verified`.
  3. Run `git pull --no-rebase`, then `git push`. The pull merges the remote changes, such as
     another machine's, and never touches the uncommitted changes of other sessions. End every
     commit message with the attribution lines your session gives you.
  4. When git reports a lock (`index.lock`), or the push is rejected because the remote moved:
     wait 30 seconds, pull again, then push again, at most 5 times.
  5. When the pull stops on a conflict: run `git merge --abort`, push nothing, and write the
     conflicting files under `## Verified`. When the pull refuses because an uncommitted change
     of another session is in the way, push nothing and write those files there. The ticket
     stays in {lane:closed}.
  6. **It does not hold:** commit nothing more and push nothing.
  7. Never `git add -A`, `--all` or `.`, never `git commit -a`, never `git add -p`, never
     rebase, never stash and never force a push.
