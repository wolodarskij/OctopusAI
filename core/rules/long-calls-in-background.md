# Long tool calls in the background

*Always applies, in every project, to every tool call of every session.*

**The owner's messages reach the session only between its tool calls.** A tool call that runs for minutes holds every message of the owner until it ends. This includes the messages from the phone through Remote Control.

1. **Run every tool call that can take longer than about one minute in a subagent or in the background.** Never run it as one blocking call in your own turn. Examples: a build, a cook, a compile, an encode, a render, a 3D generation, a test run, a download, a wait for a session or a ticket.
   - A shell command: use `run_in_background`, then read its output when Claude Code reports that it ended. Claude Code moves a foreground shell command to the background after 60 seconds (`BASH_DEFAULT_TIMEOUT_MS` and `BASH_MAX_TIMEOUT_MS` in `.claude/settings.json`).
   - A wait for a condition: use `Monitor`, or a `CronCreate` job. Never write a wait loop or a long `sleep`.
   - A long MCP call or a long series of steps: give it to a subagent that runs in the background.
2. **Keep each tool call of your own turn short.** Divide long work into short steps.
3. **A message of the owner comes first.** When the owner tells you to stop, stop the background work and stop. When the owner asks a question, answer it before you continue.
