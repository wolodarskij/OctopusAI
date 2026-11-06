# Diversion

*Always applies in a project that lists the `diversion` pack and sets `vcs` to `diversion`. The
brief of each launched session says which Diversion permits it holds and what it commits.*

## One workspace, many sessions

Every session of this project works in the same Diversion workspace. Another session's changes
sit next to yours, not committed.

- **A commit goes to the branch at once.** Diversion has no push: every commit reaches the
  server and every workspace on the branch. So only a `verify` session commits, after the
  measurement holds.
- **Commit only the files of a ticket**, by name, file names first:
  `dv commit <file>... -m "<id>: <title>"`. Never `dv commit -a`.
- **A file that also holds a change of another session is not committed.** Diversion cannot
  commit a part of a file. Leave the file out, and write it on the ticket.
- **Never discard work.** `dv reset`, `dv restore`, `dv checkout --discard-changes` and
  `dv branch -d` are denied to every session, whatever it holds.
- **Never run `dv` with no command, and never `dv login`:** both wait for input.

## The permits

| Permit | Grants | Denied while absent |
|---|---|---|
| `dv-commit` | commit to the branch | `dv commit`, `revert` |
| `dv-sync` | merge the new commits of the branch into the workspace | `dv update` |
| `dv-branch` | change branches | `dv checkout`, `branch`, `merge` |

Reading is always allowed: `dv status`, `log`, `show`.

## Who commits

A `verify` launch always holds `dv-commit`. The other launches hold only what their ticket
grants.

| Session | Commits |
|---|---|
| `work` | never: it writes the files it changed on the ticket, under `## Files` |
| `verify`, when it holds | the files of the ticket, as `<id>: <title>` |
| `verify`, when it does not hold | nothing |
| `plan`, `review` | never |
| the manager | never |
