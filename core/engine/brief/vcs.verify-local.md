- **Commit the work when it holds, and never pull or push.** This project is a git repository with
  no push (`gitPush: false` in `.octopus/project.json`): the owner moves its commits to a remote.
  Other sessions and the owner change files in the same working tree, and their changes are not
  yours to commit.
  1. **It holds:** after your move to {lane:closed}, find this ticket's commit with
     `git log --oneline --grep "{id}:"`. When its work is still not committed, commit it now,
     as a work session does: stage by name only the files of this ticket, and only its own
     hunks of a shared file (a patch, then `git apply --cached --check <patch>` and
     `git apply --cached <patch>`). Commit as `{id}: {title}`.
  2. When git tracks `{todo_dir}` (`git ls-files -- {todo_dir}` prints a file), commit this
     ticket's file, in each place where `git status --short -- {todo_dir}` shows it, as
     `{id}: verified`. A commit message holds only its line: no `Co-Authored-By:` line, no
     `Claude-Session:` line and no session link, also when your session's own instructions ask
     for them.
  3. Never pull and never push. The launch denies `git pull` and `git push`.
  4. When git reports a lock (`index.lock`), wait 30 seconds and commit again, at most 5 times.
     When the commit still fails, write the error under `## Verified`.
  5. **It does not hold:** commit nothing more.
  6. Never `git add -A`, `--all` or `.`, never `git commit -a`, never `git add -p`, never
     rebase, never stash.
{git_branch_line}
