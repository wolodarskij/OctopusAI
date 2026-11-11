# A documentation ticket delivers an artifact

*Always applies, in every project, to every documentation ticket: a ticket whose title starts with `Documentation: ` or `Documentation Bug: `.*

**A documentation ticket is finished only with its result as an artifact.** The artifact is one HTML page, published on claude.ai, and registered on the board as the local page and as the link.

1. **Write the result as one HTML page,** as the `artifacts` skill says: `Docs/Artifacts/<id>-<slug>.html`, or the directory where the project keeps such pages.
2. **Publish the page on claude.ai** with the `Artifact` tool. The link opens on the owner's phone.
3. **Register both on the board:**

       python .octopus/todo.py artifact add <path> --ticket <id>
       python .octopus/todo.py artifact add <https://claude.ai/... link> --ticket <id>

4. **Name the link under `## Result`.**
5. **When the session cannot publish the page on claude.ai,** the work stops: `report <id> blocked` with the cause.
6. **Only the owner's order frees a documentation ticket from its artifact:**

       python .octopus/todo.py doc-artifact <id> no --owner-order "<the order, in neutral words>"

   The command writes `artifact: no`, and the order under `## No artifact`.

**The engine enforces it.**

- The brief of a documentation ticket carries the steps: `artifact.work`, `artifact.plan` and `artifact.verify`.
- `move` to Verification, and to Done as `done`, refuses a documentation ticket while its `## Result` names no claude.ai link that the board's artifact list holds, or while the list holds no local HTML page of the ticket that exists.
- A verify session opens the link and the local page, and checks that each one shows the result of the order. A missing page or link sends the ticket back.
