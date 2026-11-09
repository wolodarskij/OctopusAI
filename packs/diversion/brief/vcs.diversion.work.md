- **Write your files on the ticket before the move to {lane:verify} above.** This project is a
  Diversion workspace. A commit goes to the branch at once, so a work session never commits.
  Other sessions and the owner change files in the same workspace, and their changes are not
  yours.
  1. Find what this session changed: `dv status --no-limit`.
  2. Write each file that this session changed on the ticket, under `## Files`, one path from
     the project root on each line.
  3. A file that also holds a change of another session or of the owner: Diversion cannot
     commit a part of a file. Write it under `## Files` with the words `shared with` and the
     other ticket or the owner.
  4. Never commit, never update and never reset: the verify session commits the work when it
     holds. Never write a change of another ticket under `## Files`, and never a change of a
     ticket the owner keeps in {label:blocked}.
