---
name: wait-for
description: Carry out an order that waits for a condition - "when X is done, do Y", "wait for X, then do Y", "as soon as X lands, run Y". Use when the owner gives such an order.
---

# Wait for

An order "when X is done, do Y" stands until Y is done. It does not end because X is not done yet.

1. Check X now. When it holds, do Y at once.
2. When it does not hold, say its state in one line, and create a `CronCreate` job that checks X every 10 minutes.
3. When the check finds that X holds, do Y in the same session with no new order, then delete the job.
4. Only two things end the order: Y is done, or the owner ends it.
