- **Record your work for the submit before the move to {lane:verify} above.** This project is a
  Perforce workspace. A submit goes to the depot at once, so the verify session submits the work
  when it holds. Other sessions and the owner open files in the same workspace, and their files
  are not yours.
  1. Use the changelist of this ticket. When the ticket has no `## Perforce changelist`, make one:
     `p4 --field "Description={id}: {title}" --field "Files=" change -o | p4 change -i`. Write
     the number that it prints on the ticket under `## Perforce changelist`.
  2. Open every file in that changelist before you change it: `p4 edit -c <n> <file>`,
     `p4 add -c <n> <file>`, `p4 delete -c <n> <file>`, and for a move `p4 edit -c <n> <old>`,
     then `p4 move -c <n> <old> <new>`. A file this session already opened elsewhere:
     `p4 reopen -c <n> <file>`. Never use the default changelist.
  3. `p4 opened -c <n>` must list every file this session changed, and no other file. A file
     that `p4 opened <file>` shows in another changelist is shared: leave it there, and write it on
     the ticket under `## Shared files`.
  4. Never submit, never sync and never revert. Never reopen a file of another changelist, and
     never touch a change of a ticket the owner keeps in {label:blocked}.
