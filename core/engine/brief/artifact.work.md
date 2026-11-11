
**This ticket is a documentation ticket: its result is an artifact**
(`documentation-delivers-artifact.md`).

- Write the result as one HTML page, as the `artifacts` skill says: `Docs/Artifacts/{id}-<slug>.html`,
  or the directory where the project keeps such pages.
- Publish the page on claude.ai with the `Artifact` tool. The link opens on the owner's phone.
- Register both on the board:
  `{cli} artifact add <path> --ticket {id}`, then
  `{cli} artifact add <https://claude.ai/... link> --ticket {id}`.
- Name the link under `## Result`.
- `move` to {label:verify} refuses this ticket while the board does not hold the local page and
  the link that `## Result` names.
- When the session cannot publish the page on claude.ai, run `{cli} report {id} blocked` with
  the cause.
- Only the owner's order frees the ticket from its artifact:
  `{cli} doc-artifact {id} no --owner-order "<the order, in neutral words>"`.
