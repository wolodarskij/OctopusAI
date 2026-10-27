# Git

*Always applies in a project that lists the `git` pack. The brief of each launched session says
which git permits it holds and what it commits.*

## One working tree, many sessions

Every session of this project works in the same working tree. Another session's changes sit
next to yours, staged or not.

- **Stage only the files this session changed**, by name: `git add <file> <file>`.
  Never `git add -A`, `--all` or `.`, and never `git commit -a`.
- **Never discard work.** `git reset --hard`, `git clean`, `git stash`, `git restore`,
  `git branch -D` and a forced push are denied to every session, whatever it holds.

## The permits

| Permit | Grants | Denied while absent |
|---|---|---|
| `git-commit` | commit to the local repository | `git commit`, `revert`, `cherry-pick`, `tag` |
| `git-sync` | pull and push | `git push`, `pull`, `remote add`, `remote set-url` |
| `git-branch` | change branches | `git switch`, `checkout`, `branch`, `worktree` |

`git merge` and `git rebase` are denied unless the session holds `git-branch` or `git-sync`: a
session that pulls must be able to abort a pull that stopped on a conflict.

Reading is always allowed: `git status`, `diff`, `log`, `show`, `blame`.

## Who commits

A `work` launch always holds `git-commit`. A `verify` launch always holds `git-commit` and
`git-sync`. A `review` launch and the manager hold only what their ticket grants, and they never
commit.

| Session | Commits |
|---|---|
| `work` | before the move to Verification: only the files and the hunks it changed, and the ticket file when git tracks `TODO/`, as `<id>: <title>`. It never pulls or pushes |
| `verify`, when it holds | the ticket's work if it is still not committed, the ticket file when git tracks `TODO/` as `<id>: verified`, then `git pull --no-rebase` and `git push` |
| `verify`, when it does not hold | nothing, and it pushes nothing |
| `plan`, `review` | never |
| the manager | never |

A file that another session also changed is committed by hunk: a patch of the session's own
hunks, then `git apply --cached`. `git add -p` waits for input, so no session uses it. On a lock
or a rejected push, a `verify` session waits 30 seconds, pulls and pushes again, at most 5 times.
On a conflict it aborts the merge, pushes nothing, and writes the conflict on the ticket.

There is no branch per ticket: every session commits to the branch that is checked out.
