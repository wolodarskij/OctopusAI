
**Renders: this ticket is visual work, or it says `visual: no`** (`renders-for-visual-work.md`).

- Open each render that `## Result` names. Each render shows the changed texture, mesh or
  material as the order asks. A missing render, or one that does not show the change, sends
  the ticket back to {label:start}.
- A ticket with `visual: no`: check the reason under `## Not visual work` against the
  change. When the work changed a texture, mesh or material, the ticket goes back to
  {label:start}.
- `move` to {label:closed} as `done` refuses visual work whose `## Result` names no render
  file that exists.
