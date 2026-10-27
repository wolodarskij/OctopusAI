# A tool edit is its own task

*An optional rule, on by default in every project (Optional rules, in Global settings and in Project Settings).*

**A project task never carries an edit to the agents' own tools:** the scripts in `tools/`, the hooks, and the skills and rules in `.agents/`. When a tool change would make the task easier, do the task with the tool as it is; a throwaway script in the scratchpad is fine. When the tool change is worth doing, file it as its own ticket, `category: internal tooling`, and leave it for its own session.
