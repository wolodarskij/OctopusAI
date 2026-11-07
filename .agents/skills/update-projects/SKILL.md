---
name: update-projects
description: Check every registered project and bring each one up to the current state of Octopus AI - write the changed payload again, move own rules and skills into .agents/, correct the ticket tree and the old toolkit names, and report what is still wrong. Trigger - "update all projects", "update every project", "check all projects", "bring the projects up to date", "upgrade the projects", "sync the projects with Octopus AI", "/update-projects".
---

# Update every project

`octopus` is `python "./octopus.py"`. The session needs write access to every
registered project: the manager, or a session that holds the `projects` permit.

## 1. The projects

    octopus projects list

Each line is a name and a path. A path that does not exist is a question to the owner, with the
`AskUserQuestion` tool: `<name>: <path> does not exist`, options `remove it from the registry`
(`octopus projects remove <path>`) and `keep it`. Skip that project in the steps below.

## 2. Each project, one after the other

Do the steps for each project that exists. `<p>` is its path, `<name>` its name.

1. **The payload.** `octopus update <p>`. It writes again every file that a pack changed, the
   settings, the hook lines, the entry files and the VCS ignore list. It never overwrites a file
   that somebody edited. Keep each line that says `exists with other content and is left as it is`.
2. **Own rules and skills.** `octopus adopt <p> --dry-run`. When it would move, drop or rewrite a
   file, run `octopus adopt <p>`.
3. **The install record.** `octopus status <p>`.
   - `missing`: run `octopus update <p>` again. It writes the file.
   - `edited`, and each file that step 1 left as it is: show the owner the difference between
     the project's copy and the source in `./core/` or in the pack. Ask with the
     `AskUserQuestion` tool, one question for each file: `replace with the current file` or
     `keep the project's copy`. On replace, delete the project's copy, then run
     `octopus update <p>`.
   - `warning`: when the line names `octopus update`, step 1 did it. A warning that stays is a
     line of the report.
   - Each file that misnames the toolkit: change each old spelling to `Octopus AI`, or to the
     current command or path. Keep every other word. A project-wide README file changes only
     after the owner approved its exact new text (`readme-needs-owner-approval.md`).
4. **The ticket tree.**

       octopus todo --project "<name>" migrate
       octopus todo --project "<name>" index
       octopus todo --project "<name>" check

   Correct each problem that `check` names. A problem with `run: <command>` gets that command. A
   field with a value outside its list gets the value the ticket's text supports. A problem that
   needs a decision (a severity, a parent, a lane, an outcome that the ticket does not show) is a
   question to the owner. Run `check` again until it passes or only questions stay.

Never undo a change that the owner made in a project. Commit nothing: the owner commits each
project.

## 3. Report

One table, one row for each project:

| Project | Files written | Moved | Corrected | Still wrong |
|---|---|---|---|---|

`Files written` and `Corrected` name the files by their path from the project root. `Still
wrong` holds each warning and each `check` problem that stays, word for word, and `none` when
nothing stays. Then ask the open questions with the `AskUserQuestion` tool, up to 4 in one dialog.
