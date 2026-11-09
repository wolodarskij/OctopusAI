# Git: one working tree, many sessions

*Applies when several agent sessions, or an agent and the user, work in the same git working tree.*

Another session's changes sit next to yours, staged or not.

- **Stage only the files this session changed**, by name: `git add -- <file> <file>`. Never `git add -A`, `--all` or `.`, and never `git commit -a`.
- **Commit a shared file by hunk.** When a file also holds changes of another session or of the user, write `git diff -- <file>` to a patch file, delete every hunk that is not yours, then run `git apply --cached --check <patch>` and `git apply --cached <patch>`. Never `git add -p`: it waits for input.
- **Check what you commit.** Before each commit, `git diff --cached --stat` must show only your files.
- **Never discard work.** Do not use `git reset --hard`, `git clean`, `git stash`, `git restore`, `git checkout -- <file>`, `git branch -D` or a forced push.
- **Never commit a change of another session or of the user.**
- **On a lock** (`index.lock`), wait 30 seconds and try again, at most 5 times.
- **On a rejected push,** run `git pull --no-rebase`, then push again. On a merge conflict, abort the merge, push nothing, and tell the user which files conflict.
