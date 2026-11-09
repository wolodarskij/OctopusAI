# Rules for Claude Code and Codex sessions

Each file is one rule. Copy the rules you want into a project's rules folder (`.claude/rules/`, `.agents/rules/`), or list them in `AGENTS.md` or `CLAUDE.md`.

## Working

| File | The rule |
|---|---|
| [investigate-first.md](investigate-first.md) | Read the source, the docs and the history of a thing before you say what it does; never guess. |
| [verify-before-asserting.md](verify-before-asserting.md) | State a fact only with proof: a file and line, a doc, a log or a command; mark the rest "Unverified:", and replace a claim that proved wrong. |
| [heavy-tools-only-when-needed.md](heavy-tools-only-when-needed.md) | Use an external or expensive tool only when its result can change the decision; ask before a heavy one. |
| [long-calls-in-background.md](long-calls-in-background.md) | Run each call longer than about one minute in the background or in a subagent, so the user's messages get through. |
| [no-mutual-waits.md](no-mutual-waits.md) | Never wait for a session, a task or a shared resource whose holder waits for you. |
| [tooling-is-its-own-task.md](tooling-is-its-own-task.md) | A project task never edits the agents' own tools, hooks, skills or rules; such an edit is its own ticket. |
| [external-changes.md](external-changes.md) | After a pull, a merge or another outside change, read the files again before you edit them. |
| [git-shared-working-tree.md](git-shared-working-tree.md) | In a working tree that several sessions share, stage and commit only your own files and hunks, and never discard work. |

## The user

| File | The rule |
|---|---|
| [user-edits.md](user-edits.md) | Never undo or overwrite a change that the user made while you work; ask first. |
| [user-wording.md](user-wording.md) | Keep every word of the user's text; correct only grammar and spelling. |
| [no-user-quotes.md](no-user-quotes.md) | No file holds a word-for-word quote or the swearing of the user; record orders in neutral words. |
| [readme-needs-user-approval.md](readme-needs-user-approval.md) | Change a README file in the project root only after the user approved its exact new text. |

## Writing

| File | The rule |
|---|---|
| [reporting-to-the-user.md](reporting-to-the-user.md) | Messages and deliverables hold only what the request asked for: no narration, no unasked prose, questions in the question dialog. |
| [simplified-technical-english.md](simplified-technical-english.md) | Write all text in Simplified Technical English (ASD-STE100). |
| [no-narration-in-comments.md](no-narration-in-comments.md) | A comment says what the code is or does, never the history of the work. |
| [no-local-information.md](no-local-information.md) | A shared repository holds no machine path, person, session id or other project's name. |
| [issue-tracking.md](issue-tracking.md) | Ticket titles, one ticket per defect, what may be called wrong, severity, difficulty and priority. |
