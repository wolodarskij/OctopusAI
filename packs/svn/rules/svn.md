# SVN

*Always applies in a project that lists the `svn` pack. The brief of each launched session says
which svn permits it holds and what it commits.*

## One working copy, many sessions

Every session of this project works in the same working copy. Another session's changes sit
next to yours. An svn commit goes to the repository at once: no local commit exists.

- **Keep the changes of each ticket in its own changelist,** named after the ticket id:
  `svn changelist <id> <file> <file>`. A file is in one changelist only.
- **Name each file in every command that changes the working copy.** Never `svn add .`,
  `svn add --force`, and never `svn commit` with no file and no `--changelist`.
- **Schedule a new, a deleted or a moved file by its local path:** `svn add <file>`,
  `svn delete <file>`, `svn move <old> <new>`. These change only the working copy.
- **Never discard work.** `svn revert`, `svn cleanup --remove-unversioned`,
  `svn cleanup --remove-ignored`, a forced `svn lock` or `svn unlock`, and a conflict resolution
  that throws away one side (`theirs-full`, `mine-full`) are denied to every session, whatever it
  holds.
- **Remove only your own ticket's files from a changelist:** `svn changelist --remove <file>`.

## The permits

| Permit | Grants | Denied while absent |
|---|---|---|
| `svn-commit` | write to the repository | `svn commit`, `import`, and `mkdir`, `delete`, `move` on a URL |
| `svn-update` | bring the working copy up to date | `svn update`, `relocate` |
| `svn-branch` | change branches | `svn switch`, `copy`, `merge` |

Reading is always allowed: `svn status`, `diff`, `log`, `info`, `blame`, `list`, `cat`.

`svn lock` is allowed: a file with `svn:needs-lock` stays read-only until its session locks it.
`svn commit` releases the locks of the committed files.

## Who commits

A `verify` launch always holds `svn-commit` and `svn-update`. A `work`, a `plan` and a `review`
launch and the manager hold only what their ticket grants, and they never commit.

| Session | Commits |
|---|---|
| `work` | nothing. It puts every file it changed, added or deleted into the changelist `<id>`, and lists the files on the ticket under `## Changed files` |
| `verify`, when it holds | updates the ticket's files, commits them as `<id>: <title>`, then the ticket file when svn tracks `TODO/`, as `<id>: verified` |
| `verify`, when it does not hold | nothing |
| `plan`, `review` | never |
| the manager | never |

**A file that another session also changed is not committed by part.** svn commits whole files.
The work session lists it under `## Shared files` on the ticket. The verify session commits it
only when `svn diff <file>` shows no change of another ticket or of the owner.

**An external is a working copy of its own.** A commit of files in several working copies runs
once for each working copy root (`svn info --show-item wc-root <file>`), with the same message.

On a working copy lock (`E155004`) or a commit that is out of date, a `verify` session waits 30
seconds, updates the ticket's files and commits again, at most 5 times. On a conflict it commits
nothing, leaves the conflict in place, and writes the conflicting files on the ticket.

There is no branch per ticket: every session commits to the URL the working copy is on.
