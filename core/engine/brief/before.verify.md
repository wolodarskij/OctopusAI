## Before you touch anything

1. Read `AGENTS.md` and every rule it lists (Claude Code: `CLAUDE.md`, which imports them), when
   the project has them. They outrank this brief.
{hard_rules}
2. The launch already moved this ticket to `{launch_label}` with status
   `{launch_status}`. Confirm with `{cli} show {id}`; if it sits anywhere
   else, run `{cli} move {id} {launch_lane} --status "{launch_status}"`.
   The ticket stays in `{lane:verify}` while you measure. It moves once more,
   at the end.

