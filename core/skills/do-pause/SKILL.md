---
name: do-pause
description: Stop the current session and every subagent immediately when the owner says "do pause", "pause", "make pause", "stop for now", or names a time to continue - "pause till 11.51", "pause until 3pm", "pause for two hours". Schedule the wake-up for the time the owner gives, answer with the two words "process paused", and do nothing else. Use it the moment the words arrive, before any work in flight is finished. Not for a question about what a pause would cost, and not for the end of a task.
---

# Do pause

When the owner says **do pause**:

1. **Stop immediately.** Do not finish the tool call you planned or the file you started. Do not wait for a subagent. Do not verify, save, log or tidy.
2. **Stop every subagent** before you answer: `ListAgents` gives the live agents, `TaskStop` stops each one.
3. **Schedule the wake-up** when the owner named a time.
4. **Answer `process paused`.** Nothing before it, nothing after it: no status, no table, no summary of what was in flight.

## The wake-up

Read the current time with `date +%H:%M`.

| The owner said | Schedule for |
|---|---|
| a clock time ("till 11.51", "until 3pm") | that time today, local |
| a duration ("for two hours") | now plus that duration |
| no time | nothing: the owner restarts the session |

Use `CronCreate` for a time more than one hour away, `ScheduleWakeup` for less. Give the job the prompt the session was working on, so the work continues.

## What a pause is not

- **Not a stop.** Nothing is cancelled, and every claim stays as it is.
- **Not a handover.** Write nothing.
- **Not a checkpoint.** Do not save, commit, build or run tests.
