- **Submit the work when it holds.** This project is a Perforce workspace. Other sessions and
  the owner open files in the same workspace, and their changelists are not yours.
  1. **It holds:** after your move to {lane:closed}, find the ticket's pending changelist:
     the `Changelist:` line on the ticket, or `p4 changes -s pending -l -c <client>` with the
     client name from `p4 -F %clientName% info`. Its description starts with `{id}: `.
  2. When the depot holds `{todo_dir}` (`p4 files {todo_dir}/...` prints a file), open this
     ticket's file into the changelist: `p4 reconcile -c <change> <old path> <new path>`.
  3. Sync only the files of the changelist: `p4 -F %depotFile% opened -c <change> | p4 -x - sync`.
     Then run `p4 resolve -am -c <change>`.
  4. When `p4 resolve -n -c <change>` still lists a file, submit nothing: write the files
     under `## Verified`. The ticket stays in {lane:closed}.
  5. Run `p4 submit -c <change>`. When the submit fails because a file is out of date: wait 30
     seconds, sync and resolve again as in step 3, then submit again, at most 5 times. When it
     still fails, write the error under `## Verified`.
  6. **It does not hold:** submit nothing. The changelist stays pending.
  7. Never submit the default changelist, never `p4 revert`, never `p4 sync -f`, and never
     `p4 clean`.
