# External tools only when needed

*An optional rule, on by default in every project (Optional rules, in Global settings and in Project Settings).*

**Before you use an external tool, decide if you need it.** An external tool is an encoding, compression or rendering tool, or any other program outside the session.

1. **Think twice: can the result of the tool change your decision or your deliverable?** When it cannot, do not use the tool. Example: a lossless encode of a video whose source was never lossless cannot change anything, so do not run it.
2. **When the tool is expensive for the machine, find a cheap alternative.** Expensive means a large use of CPU, memory, disk or time. When no cheap alternative exists, ask the owner before you use the tool.
3. **Before you add a new test, decide if you need it.** Also decide if it makes the test runs much slower. A slow test is not part of every run. It runs only in its own ticket, in its own session, and only when the owner permits it.

This rule is a simple evaluation before use. Do not run a test, a sample run or a measurement run for it.
