---
name: todo-run-group
description: Launch one Claude session per Pipeline ticket of a Todo group, plan for unknown difficulty and work for the rest. Trigger - "run the group", "do the whole group", "run all tasks from <group>", "start every ticket in <group>".
---

# Run a whole Todo group

One call launches every Pipeline ticket of the group, each in its own terminal:

    python .octopus/todo.py groups --run "<group>" [--note "..."]

The name is a group from `python .octopus/todo.py groups --table` (map store) or
`python .octopus/todo.py groups` (files store). An auto group works too.

| Difficulty | Mode |
|---|---|
| `small`, `medium`, `large` | `work` |
| `unknown` | `plan` |

- The model and the effort are the project's `defaultModel` and `defaultEffort`. `--model` and
  `--effort` override them on the owner's order.
- A ticket in any other board is printed with its board, and nothing is launched for it.
- `--note` goes into every brief, above the generic steps.
- `--dry-run` prints the launch lines and starts nothing.

A launched session that meets a blocker follows its brief. It stays open while it waits, so do not
close its terminal.

## Report

A table with one row per ticket: id, mode, and the `claude --resume <uuid>` line that the command
printed. Nothing else.
