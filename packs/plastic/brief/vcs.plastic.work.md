- **Write your files on the ticket before the move to {lane:verify} above.** This project is a
  Unity Version Control workspace. A checkin goes to the repository at once, so a work session
  never checks in. Other sessions and the owner change files in the same workspace, and their
  changes are not yours.
  1. Check out each file before you change it: `cm checkout <file>`. Add a new file with
     `cm add <file>`, and remove a file with `cm remove <file>`.
  2. Find what this session changed: `cm status`, then `cm diff <file>` for each file you
     edited.
  3. Write each file that this session changed on the ticket, under `## Files`, one path from
     the project root on each line.
  4. A file that also holds a change of another session or of the owner: a checkin cannot take
     a part of a file. Write it under `## Files` with the words `shared with` and the other
     ticket or the owner.
  5. Never check in, never update and never undo: the verify session checks in the work when
     it holds. Never write a change of another ticket under `## Files`, and never a change of
     a ticket the owner keeps in {label:blocked}.
