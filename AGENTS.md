<!-- octopus:begin - generated from .agents/ by Octopus AI. Edit the files in .agents/, not this block. -->
## Octopus AI

The rules and the skills of this project live once, in `.agents/`. Every agent reads the same files.

**Never narrate your own work.** In every message to the owner, mid-task and final, write only what was asked for: the answer, the files changed, a problem that stopped the work, a question. Never write a plan or a next step, what you read, check or run, what a tool printed, your reasoning, or a summary of your steps: no "I will use an edit tool to edit ...", no "Let me ...", no "Checking ...", no "Reading ...". **One status line is allowed:** at the start of your run, and again when about 5 minutes passed since your last message, write one short sentence: what you work on now. Never at every step, and nothing more than that sentence.

**The rules.** Read each file below before your first change. Claude Code has them loaded already, through `CLAUDE.md`.

- `.agents/rules/octopus/core/investigate-first.md`
- `.agents/rules/octopus/core/local-information.md`
- `.agents/rules/octopus/core/no-narration-in-comments.md`
- `.agents/rules/octopus/core/no-owner-quotes.md`
- `.agents/rules/octopus/core/owner-edits.md`
- `.agents/rules/octopus/core/owner-wording.md`
- `.agents/rules/octopus/core/readme-needs-owner-approval.md`
- `.agents/rules/octopus/core/reporting-to-the-owner.md`
- `.agents/rules/octopus/core/simplified-technical-english.md`
- `.agents/rules/octopus/core/todo-tracking.md`
- `.agents/rules/octopus/core/tooling-is-its-own-task.md`
- `.agents/rules/octopus/core/verify-before-asserting.md`

**The skills** are in `.agents/skills/<name>/SKILL.md`. Codex finds them there; Claude Code finds them through `.claude/skills/`.

Skills: `artifacts`, `co-writing`, `do-pause`, `octopusmanager`, `todo`, `todo-groups`, `todo-run-group`, `wait-for`.

**The tickets:** `python .octopus/todo.py --help`.
<!-- octopus:end -->
