# Mercurial

*Always applies in a project that lists the `mercurial` pack and sets `vcs` to `mercurial`. The
brief of each launched session says which hg permits it holds and what it commits.*

## One working directory, many sessions

Every session of this project works in the same working directory. Another session's changes
sit next to yours, not committed.

- **Commit only the files this session changed**, by name: `hg commit <file>... -m "<msg>"`.
  Add a new file first with `hg add <file>`, and remove a file with `hg remove <file>`. A commit
  with no file names takes every change of the working directory, so it is denied. Only the
  merge commit of a `verify` session takes no file names: `hg commit --message "<id>: merge"`.
- **A file that also holds a change of another session is not committed.** Mercurial has no
  index, and `hg commit -i` waits for input. Leave the file out, and write it on the ticket.
- **Never discard work.** `hg revert`, `hg update --clean`, `hg purge`, `hg strip`,
  `hg rollback`, `hg shelve`, `hg addremove` and a forced push are denied to every session,
  whatever it holds.

## The permits

| Permit | Grants | Denied while absent |
|---|---|---|
| `hg-commit` | commit to the local repository | `hg commit`, `ci`, `tag`, `backout`, `graft`, `amend` |
| `hg-sync` | pull and push | `hg push`, `pull`, `fetch` |
| `hg-branch` | change branches | `hg update`, `checkout`, `branch`, `bookmark`, `rebase` |

`hg merge` and `hg resolve` are denied unless the session holds `hg-branch` or `hg-sync`: a
session that pulls must be able to merge the new head, and to abort that merge.

Reading is always allowed: `hg status`, `diff`, `log`, `heads`, `annotate`, `phase`.

## Who commits

A `work` launch always holds `hg-commit`. A `verify` launch always holds `hg-commit` and
`hg-sync`. A `review` launch and the manager hold only what their ticket grants, and they never
commit.

| Session | Commits |
|---|---|
| `work` | before the move to Verification: only the files it changed, as `<id>: <title>`. It never pulls or pushes |
| `verify`, when it holds | the ticket's work if it is still not committed, then `hg pull`, a merge of a new head, and `hg push` |
| `verify`, when it does not hold | nothing, and it pushes nothing |
| `plan`, `review` | never |
| the manager | never |
