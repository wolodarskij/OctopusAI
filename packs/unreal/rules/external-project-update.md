# External project update

*Apply when the project was updated outside the agent: a VCS pull, merge or sync, another IDE,
teammate commits, or the user says the tree changed.*

The chat may still describe **old** file contents. Treat the workspace as **unknown until re-read**.

## 1. Refresh grounding

- Re-read `AGENTS.md` (Claude Code: `CLAUDE.md`) for stack, layout and links to rules.
- Skim the tail of the project change log, if it keeps one; external changes often do not appear there.
- If the task touches engine or VCS policy, re-check the VCS ignore file and `Config/*.ini`
  (especially `DefaultEngine.ini` `[CoreRedirects]` and `DefaultGameplayTags.ini`).

## 2. Re-validate before editing

- Open the specific files you will change; do not rely on earlier messages.
- If something "should exist" from an old turn, confirm with a read or search.
- After a merge or rebase, re-read conflict-resolved files end-to-end.

## 3. Unreal-specific

- `.uproject` / plugins: if changed, module or plugin deps may differ.
- Renames: check `[CoreRedirects]` in `DefaultEngine.ini`.
- Content: use the offline tools in `{{OCTOPUS}}/packs/blueprint/tools/` for `Content/` or Blueprint analysis.

## 4. Rules

`.agents/rules/` may have changed externally. Re-read the relevant files if behaviour conflicts
with assumptions. The pack rules are in `.agents/rules/octopus/`.
