# Subversion

*Always applies in a project that lists the `svn` pack and sets `vcs` to `svn`. The brief of
each launched session says which svn permits it holds and what it commits.*

## One working copy, many sessions

Every session of this project works in the same working copy. Another session's changes sit
next to yours, in their own changelists.

- **Put each changed file of a ticket into the changelist named after the ticket id:**
  `svn changelist <id> <file>`. Add a new file first with `svn add <file>`, and remove a file
  with `svn delete <file>`.
- **A file is in one changelist only.** When another ticket's changelist holds the file, do not
  move it. Write the file and that changelist on your ticket.
- **Commit only a changelist:** `svn commit --changelist <id> -m "<id>: <title>"`. A commit with
  no changelist sends every change of the working copy, so it is denied.
- **Never discard work.** `svn revert`, `svn cleanup --remove-unversioned`,
  `svn cleanup --remove-ignored`, `svn delete --force` and `svn update --force` are denied to
  every session, whatever it holds.

## The permits

| Permit | Grants | Denied while absent |
|---|---|---|
| `svn-commit` | send work to the server | `svn commit`, `ci`, `import`, `lock`, `unlock` |
| `svn-sync` | get the changes of others | `svn update`, `up`, `resolve`, `relocate` |
| `svn-branch` | change branches | `svn switch`, `sw`, `merge` |

Reading is always allowed: `svn status`, `diff`, `log`, `info`, `blame`. Changing the working
copy is always allowed: `svn add`, `delete`, `move`, `copy`, `changelist`, `cleanup`.

## Who commits

A `verify` launch always holds `svn-commit` and `svn-sync`. The other launches hold only what
their ticket grants.

| Session | Commits |
|---|---|
| `work` | never: it puts its files into the changelist `<id>` |
| `verify`, when it holds | the changelist `<id>`, after an update of its files, as `<id>: <title>` |
| `verify`, when it does not hold | nothing |
| `plan`, `review` | never |
| the manager | never |
