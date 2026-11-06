# Perforce

*Always applies in a project that lists the `perforce` pack and sets `vcs` to `perforce`. The
brief of each launched session says which Perforce permits it holds and what it submits.*

## One workspace, many sessions

Every session of this project works in the same Perforce workspace. Another session's open
files sit next to yours, in their own changelists.

- **Put each change of a ticket into the ticket's own numbered changelist.** Its description
  starts with `<id>: `. Never use the default changelist.
- **Open a file before you change it:** `p4 edit -c <change> <file>`. Open a new file with
  `p4 add -c <change> <file>`, and remove a file with `p4 delete -c <change> <file>`.
- **A file is open in one changelist only.** When another ticket's changelist holds the file,
  do not move it. Write the file and that changelist on your ticket.
- **Never discard work.** `p4 revert`, `p4 clean`, `p4 obliterate`, `p4 sync -f`,
  `p4 reconcile -w`, `p4 shelve -d`, `p4 change -f`, `p4 change -d` and a submit of the
  default changelist are denied to every session, whatever it holds.

## The permits

| Permit | Grants | Denied while absent |
|---|---|---|
| `p4-submit` | send work to the server | `p4 submit`, `shelve`, `unshelve` |
| `p4-sync` | get the changes of others | `p4 sync`, `update`, `flush`, `resolve` |
| `p4-branch` | change streams, branches and workspaces | `p4 switch`, `integrate`, `merge`, `copy`, `populate`, `branch`, `client`, `workspace`, `stream` |

Reading is always allowed: `p4 info`, `opened`, `changes`, `describe`, `diff`, `files`,
`filelog`, `fstat`. Opening files and making a changelist are always allowed: `p4 edit`,
`add`, `delete`, `move`, `reopen`, `change`, `reconcile`.

## Who submits

A `verify` launch always holds `p4-submit` and `p4-sync`. The other launches hold only what
their ticket grants.

| Session | Submits |
|---|---|
| `work` | never: it puts its files into the ticket's changelist, `<id>: <title>` |
| `verify`, when it holds | the ticket's changelist, after a sync and a resolve of its files |
| `verify`, when it does not hold | nothing |
| `plan`, `review` | never |
| the manager | never |
