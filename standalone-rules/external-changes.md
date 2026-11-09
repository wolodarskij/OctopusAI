# External changes to the project

*Applies when the project changed outside the session: a VCS pull, merge or sync, another IDE, a teammate's commits, or the user says the tree changed.*

The chat may still describe **old** file contents. Treat the workspace as **unknown until re-read**.

## 1. Refresh grounding

- Re-read `AGENTS.md` (Claude Code: `CLAUDE.md`) for the stack, the layout and the links to rules.
- Read the end of the project change log, if it keeps one; external changes often do not appear there.
- If the task touches build or VCS policy, read the VCS ignore file and the project config files again.

## 2. Re-validate before editing

- Open the specific files you will change; do not rely on earlier messages.
- If something "should exist" from an old turn, confirm it with a read or a search.
- After a merge or a rebase, read each conflict-resolved file again from start to end.
- Check the project and package manifests: if they changed, the modules and dependencies may differ.

## 3. Rules

The rules of the agents (`.agents/`, `.claude/`, `.codex/`) may have changed. Read the related files again when their behavior conflicts with your assumptions.
