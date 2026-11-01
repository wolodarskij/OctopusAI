- **Record your work for the commit before the move to {lane:verify} above.** This project is an
  svn working copy. An svn commit goes to the repository at once, so the verify session commits
  the work when it holds. Other sessions and the owner change files in the same working copy, and
  their changes are not yours.
  1. Find what this session changed: `svn status`, then `svn diff <file>` for each file you
     edited. A file inside an external shows in the status of its own working copy.
  2. Schedule each new file with `svn add <file>`, each deleted file with `svn delete <file>`,
     and each moved file with `svn move <old> <new>`. Name every file. Never `svn add .` or
     `svn add --force`.
  3. Put every file this session changed, added or deleted into the changelist of this
     ticket: `svn changelist {id} <file> <file>`. When svn warns that it removes a file from
     another changelist, that file is shared: put it back with `svn changelist <other> <file>`.
  4. Write every file of the changelist on the ticket under `## Changed files`, one path per line.
     Write each file that also holds changes of another session, of another ticket or of the owner
     under `## Shared files`, and keep it out of the changelist: svn commits whole files.
  5. Never commit, never update and never revert. Never change the changelist of another ticket,
     and never touch a change of a ticket the owner keeps in {label:blocked}.
