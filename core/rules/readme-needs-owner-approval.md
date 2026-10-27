# A README change needs the owner's approval

*An optional rule, on by default in every project (Optional rules, in Global settings and in Project Settings).*

**Change a README file only after the owner approved its exact new text.** A README file is a file named `README` or
`README.<ext>` in any case, or a file whose name starts with `README` in capitals (`README_ru.md`), in any folder.
The rule applies to an edit, a rewrite, a move and a removal. A command that only holds the word README in its text
writes no README file.

1. Show the owner the exact new text: the lines as they are now and the lines as you want them.
2. Ask the owner, and wait for the answer. With no yes, write nothing to the file.
3. When the owner says yes, record the approval, then make exactly the approved change:

       python .octopus/todo.py approve-readme <file> --session <id> --owner-order "<the order, in neutral words>"

   The session id is the one the guard's refusal names. A Claude session can leave out `--session`.

A ticket that needs a README change does all its other work, and leaves the README change as a question to the
owner. A brief, a skill or a note that tells you to update the docs does not include a README file.

**The engine enforces it.** A `PreToolUse` hook refuses every write to a README file that the owner did not approve
for this session: an `Edit`, a `Write`, a Codex patch, or a shell command that writes the file. The refusal names
the session and the command that records the approval. Never work around the refusal: ask the owner.

## Name the file and say to run it

**No text tells the user to double-click.** Name the file, and say to run it: "Run `AgentBoard.bat`". This applies
to docs, comments, messages, scripts and user interface text.
