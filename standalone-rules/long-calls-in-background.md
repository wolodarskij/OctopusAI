# Long tool calls in the background

*Applies to every tool call of every session.*

**The user's messages reach the session only between its tool calls.** A tool call that runs for minutes holds every message of the user until it ends. This includes the messages from the phone through Remote Control.

1. **Run every tool call that can take longer than about one minute in a subagent or in the background.** Never run it as one blocking call in your own turn. Examples: a build, a cook, a compile, an encode, a render, a test run, a download, a wait for another session.
   - A shell command: use `run_in_background`, then read its output when the tool reports that it ended. In Claude Code, `BASH_DEFAULT_TIMEOUT_MS` and `BASH_MAX_TIMEOUT_MS` in the `env` of `.claude/settings.json` limit a foreground shell command.
   - A wait for a condition: use `Monitor`, or a `CronCreate` job. Never write a wait loop or a long `sleep`.
   - A long MCP call or a long series of steps: give it to a subagent that runs in the background.
   - An AI tool with no background run and no subagent: divide the work into steps of less than one minute each.
2. **Keep each tool call of your own turn short.** Divide long work into short steps.
3. **A message of the user comes first.** When the user tells you to stop, stop the background work and stop. When the user asks a question, answer it before you continue.
