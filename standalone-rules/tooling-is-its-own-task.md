# A tool edit is its own task

**A project task never carries an edit to the agents' own tools:** the scripts in `tools/`, the hooks, and the skills and rules of the agents (`.agents/`, `.claude/`, `.codex/`). When a tool change would make the task easier, do the task with the tool as it is; a throwaway script in the scratchpad is fine. When the tool change is worth doing, file it as its own ticket, and leave it for its own session.

**A defect of a third-party tool is no ticket of the project that found it.** Report it to the tool's maintainers, and carry on with the task.
