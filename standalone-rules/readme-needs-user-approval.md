# A project-wide README change needs the user's approval

**Change a project-wide README file only after the user approved its exact new text.** A README file is a file named `README` or `README.<ext>` in any case, or a file whose name starts with `README` in capitals (`README_ru.md`). It is project-wide when it is in the root folder of a project: the folder that holds `.git`. The rule applies to an edit, a rewrite, a move and a removal. A command that only holds the word README in its text writes no README file.

**A README file in any other folder is internal.** The README file of a tool folder, a package or a plugin changes as any other file, with no approval.

1. Show the user the exact new text: the lines as they are now and the lines as you want them.
2. Ask the user with the question dialog (`AskUserQuestion` in Claude Code), and wait for the answer. An AI tool with no question dialog asks in one line, as the last line of its message. With no yes, write nothing to the file.
3. When the user says yes, make exactly the approved change.

A task that needs a project-wide README change does all its other work, and leaves the README change as a question to the user. A note that tells you to update the docs does not include a project-wide README file.

## Name the file and say to run it

**No text tells the user to double-click.** Name the file, and say to run it: "Run `Setup.bat`". This applies to docs, comments, messages, scripts and user interface text.
