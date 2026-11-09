# Unity Version Control (Plastic SCM)

*Always applies in a project that lists the `plastic` pack and sets `vcs` to `plastic`. The
brief of each launched session says which cm permits it holds and what it checks in.*

## One workspace, many sessions

Every session of this project works in the same workspace. Another session's changes sit next
to yours, not checked in.

- **A checkin goes to the repository of the workspace at once.** So only a `verify` session
  checks in, after the measurement holds.
- **Check out a file before you change it:** `cm checkout <file>`. Add a new file with
  `cm add <file>`, and remove a file with `cm remove <file>`.
- **Check in only the files of a ticket**, by name, file names first:
  `cm checkin <file>... -c="<id>: <title>"`. A checkin with no file names takes every pending
  change of the workspace, so it is denied.
- **A file that also holds a change of another session is not checked in.** A checkin cannot
  take a part of a file. Leave the file out, and write it on the ticket.
- **Never discard work.** `cm undo`, `cm undocheckout`, `cm update --override`,
  `cm update --forced` and `cm branch delete` are denied to every session, whatever it holds.

## The permits

| Permit | Grants | Denied while absent |
|---|---|---|
| `cm-checkin` | send work to the repository | `cm checkin`, `ci`, `partial checkin`, `label` |
| `cm-sync` | get the changes of others, and exchange branches | `cm update`, `partial update`, `push`, `pull`, `replicate` |
| `cm-branch` | change branches | `cm switch`, `partial switch`, `branch`, `merge` |

Reading is always allowed: `cm status`, `diff`, `log`, `find`, `annotate`. Changing the
workspace is always allowed: `cm checkout`, `add`, `remove`, `move`.

## Who checks in

A `verify` launch always holds `cm-checkin`. The other launches hold only what their ticket
grants.

| Session | Checks in |
|---|---|
| `work` | never: it writes the files it changed on the ticket, under `## Files` |
| `verify`, when it holds | the files of the ticket, as `<id>: <title>` |
| `verify`, when it does not hold | nothing |
| `plan`, `review` | never |
| the manager | never |
