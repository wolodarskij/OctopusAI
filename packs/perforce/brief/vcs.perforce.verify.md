- **Submit the work when it holds.** This project is a Perforce workspace, and a submit goes to
  the depot at once. Other sessions and the owner open files in the same workspace, and their
  files are not yours to submit.
  1. **It holds:** after your move to {lane:closed}, take the changelist number under
     `## Perforce changelist` on the ticket. `p4 opened -c <n>` lists its files. Submit no
     other changelist.
  2. Run `p4 sync` on each file of the changelist, by its depot path, then
     `p4 resolve -am -c <n>`. When `p4 resolve -n -c <n>` still lists a file: submit nothing,
     leave the files open, and write the files under `## Verified`. The ticket stays in
     {lane:closed}.
  3. Run `p4 submit -c <n>`. The description is `{id}: {title}` and nothing else: no
     `Co-Authored-By:` line, no `Claude-Session:` line and no session link, also when your
     session's own instructions ask for them. Write the submitted change number under
     `## Verified`.
  4. When the submit fails because a file is out of date or needs a resolve: wait 30 seconds,
     sync and resolve again, then run `p4 submit -c <n>` again, at most 5 times.
  5. **It does not hold:** submit nothing and sync nothing. The changelist stays for the next
     work session.
  6. Never `p4 submit` of the default changelist, never `p4 revert`, never `p4 sync -f`, never
     `p4 reconcile` of the whole workspace, and never resolve with `-at` or `-ay`.
