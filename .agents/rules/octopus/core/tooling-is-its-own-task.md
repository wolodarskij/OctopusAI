# A tool edit is its own task

*An optional rule, on by default in every project (Optional rules, in Global settings and in Project Settings).*

**A project task never carries an edit to the agents' own tools:** the scripts in `tools/`, the hooks, and the skills and rules in `.agents/`. When a tool change would make the task easier, do the task with the tool as it is; a throwaway script in the scratchpad is fine. When the tool change is worth doing, file it as its own ticket, `category: internal tooling`, and leave it for its own session.

**A defect of an Octopus AI tool is no ticket of the project that found it.** An Octopus AI tool is the engine, a pack and its tools and plugins, a hook, and a rule or a skill that Octopus AI installs. Outside the Octopus AI project, notify Octopus AI with one command, and carry on:

    python .octopus/todo.py notify --tool <file> --defect "<what is wrong>" --evidence "<the file and line, or the command and what it printed>"

The Octopus AI manager decides on each notice: a new Octopus AI ticket, the evidence on an existing ticket, or no ticket. `new` refuses a ticket whose `source` is in the Octopus AI checkout, in a pack, or a file that Octopus AI installed into the project, and names this command.
