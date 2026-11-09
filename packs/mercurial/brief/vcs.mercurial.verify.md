- **Push the work when it holds.** This project is a Mercurial repository. Other sessions and
  the owner change files in the same working directory, and their changes are not yours to
  commit.
  1. **It holds:** after your move to {lane:closed}, find this ticket's commit with
     `hg log -k "{id}:"`. When its work is still not committed, commit it now, as a work
     session does: `hg commit <file>... -m "{id}: {title}"`, only the files of this ticket.
  2. When Mercurial tracks `{todo_dir}` (`hg files {todo_dir}` prints a file), commit this
     ticket's file: `hg remove --after <old path>`, `hg add <new path>`, then
     `hg commit <old path> <new path> -m "{id}: verified"`.
  3. Run `hg pull`. When `hg heads .` then shows two heads, run `hg merge --tool :merge`, and
     commit the merge with `hg commit --message "{id}: merge"`. A merge commit takes no file
     names.
  4. When the merge stops on a conflict: run `hg merge --abort`, push nothing, and write the
     conflicting files under `## Verified`. When the merge refuses because an uncommitted change
     of another session is in the way, push nothing and write those files there. The ticket
     stays in {lane:closed}.
  5. Run `hg push`. When the push fails because the remote moved: wait 30 seconds, pull and
     merge again, then push again, at most 5 times.
  6. A commit message holds only its line above: no `Co-Authored-By:` line, no
     `Claude-Session:` line and no session link.
  7. **It does not hold:** commit nothing more and push nothing.
  8. Never commit with no file names except the merge, never `hg revert`, never
     `hg update --clean`, never `hg shelve`, and never force a push.
