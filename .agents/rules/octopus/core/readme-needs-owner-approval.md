# A project-wide README change needs the owner's approval

*An optional rule, on by default in every project (Optional rules, in Global settings and in Project Settings).*

**Change a project-wide README file only after the owner approved its exact new text.** A
README file is a file named `README` or `README.<ext>` in any case, or a file whose name
starts with `README` in capitals (`README_ru.md`). It is project-wide when it is in the root
folder of a project: the folder that holds `.octopus/project.json`, or, when no folder above
the file holds it, the folder that holds `.git`. The rule applies to an edit, a rewrite, a
move and a removal. A command that only holds the word README in its text writes no README
file.

**A README file in any other folder is internal.** The README file of a tool folder, a pack
or a plugin, such as `core/engine/brief/README.md`, changes as any other file, with no
approval.

1. Show the owner the exact new text: the lines as they are now and the lines as you want them.
2. Ask the owner with the `AskUserQuestion` tool, and wait for the answer. With no yes, write nothing to the file.
3. When the owner says yes, record the approval, then make exactly the approved change:

       python .octopus/todo.py approve-readme <file> --session <id> --owner-order "<the order, in neutral words>"

   The session id is the one the guard's refusal names. A Claude session can leave out `--session`.

A ticket that needs a project-wide README change does all its other work, and leaves the README change as a
question to the owner. A brief, a skill or a note that tells you to update the docs does not include a project-wide
README file.

**The engine enforces it.** A `PreToolUse` hook refuses every write to a project-wide README file that the owner did
not approve for this session: an `Edit`, a `Write`, a Codex patch, or a shell command that writes the file. The refusal names
the session and the command that records the approval. Never work around the refusal: ask the owner.

## Name the file and say to run it

**No text tells the user to double-click.** Name the file, and say to run it: "Run `AgentBoard.bat`". This applies
to docs, comments, messages, scripts and user interface text.
