# No two sessions wait for each other

*Applies when several agent sessions work on one machine or one project. Governs every wait of a session: on a task, on another session and on a shared resource.*

**Never wait for a task, a session or a shared resource whose holder waits for you.** A shared resource is a thing that only one session can use at a time, such as an editor, a device or a lock file. Two sessions that wait for each other wait for ever.

1. **Before you wait, find what the other side waits for.** Read the other task: its state, what it is blocked by, and its last report. Read the other session's last message. When it waits for your task, for your session or for a resource that you hold, do not start the wait.
2. **The session that can go on without the other goes on.** The session that holds the resource continues its work, then releases the resource. The session whose task needs nothing from the other task continues.
3. **Tell the other session so.** Send it a message: what it can do now, and why. Never type into a session that shows a dialog.
4. **Then the other session waits for that session only.**

**Never record a cycle of blockers.** When task A is blocked by task B, task B is never blocked by task A, directly or through other tasks.
