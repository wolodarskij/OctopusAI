# Perforce

*Always applies in a project that lists the `perforce` pack. The brief of each launched session
says which Perforce permits it holds and what it submits.*

## One workspace, many sessions

Every session of this project works in the same Perforce workspace. Another session's open files
sit next to yours. A submit goes to the depot at once: no local commit exists.

- **Open every file of a ticket in its own numbered pending changelist,** never in the default
  changelist: the default changelist holds the files of every session. The work session makes
  the changelist and writes its number on the ticket under `## Perforce changelist`.
- **Open a file before you change it.** Perforce keeps a file read-only until it is open:
  `p4 edit -c <n> <file>`, `p4 add -c <n> <file>`, `p4 delete -c <n> <file>`, and for a move
  `p4 edit -c <n> <old>`, then `p4 move -c <n> <old> <new>`. In a workspace with `allwrite`,
  open a file you changed with `p4 reconcile -c <n> <file>`, and name the file.
- **Name each file in every command that opens, moves or submits files.** Never `p4 reconcile`
  of the whole workspace, and never `p4 submit` of the default changelist.
- **Never discard work.** `p4 revert`, `p4 clean`, `p4 obliterate`, `p4 sync -f`, `p4 sync -k`, a
  resolve that throws away one side (`-at`, `-ay`), a forced `p4 unlock`, and the deletion of a
  changelist or a shelf are denied to every session, whatever it holds.
- **Keep what Octopus AI writes out of the depot, or use an `allwrite` workspace:** `TODO/`,
  `.octopus/`, `.agents/`, `.claude/`, `AGENTS.md` and `CLAUDE.md`. The engine writes these
  files directly, and a read-only file stops it.

## The permits

| Permit | Grants | Denied while absent |
|---|---|---|
| `p4-submit` | submit to the depot | `p4 submit`, `populate` |
| `p4-sync` | bring the workspace up to date | `p4 sync`, `update`, `flush`, `p4 set P4PORT`, `p4 set P4CLIENT` |
| `p4-branch` | change streams and branches | `p4 switch`, `integrate`, `merge`, `copy`, a change of the workspace spec |

Reading is always allowed: `p4 opened`, `diff`, `describe`, `changes`, `files`, `fstat`,
`filelog`, `where`, `info`.

A file of an exclusive type (`+l`) is locked by the `p4 edit` that opens it. The submit releases
the lock.

## Who submits

A `verify` launch always holds `p4-submit` and `p4-sync`. A `work`, a `plan` and a `review`
launch and the manager hold only what their ticket grants, and they never submit.

| Session | Submits |
|---|---|
| `work` | nothing. It opens every file it changes in the changelist of its ticket, whose description is `<id>: <title>`, and writes the number on the ticket |
| `verify`, when it holds | syncs and resolves the files of the changelist, then submits it |
| `verify`, when it does not hold | nothing. The changelist stays for the next work session |
| `plan`, `review` | never |
| the manager | never |

**A file that another changelist holds open is not submitted by part.** Perforce opens a file in
one changelist only. The work session lists it under `## Shared files` on the ticket, and leaves
it in its changelist.

On a submit that fails because a file is out of date or needs a resolve, a `verify` session syncs
and resolves again, then submits again, at most 5 times. On a conflict that `p4 resolve -am`
does not merge, it submits nothing, leaves the files open, and writes them on the ticket.

There is no stream or branch per ticket: every session submits to the stream or the branch of the
workspace.
