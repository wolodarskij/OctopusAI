
**This ticket does visual work: renders of the result are part of it.** Its order names a
texture, a mesh or a material (`renders-for-visual-work.md`).

- Before you move the ticket on, render each changed texture, mesh or material as it shows
  in the engine, in the game or in a viewer. The texture file itself is no render.
- Save each render in `{todo_dir}/.renders/{id}/`, as `.png`, `.jpg`, `.webp`, `.gif`, `.mp4`
  or `.webm`.
- Name each render under `## Result` by its path from the project root, in backticks, one
  line each.
- `move` to {label:verify} refuses this ticket while its `## Result` names no render file
  that exists.
- When the work changes no texture, mesh or material, say so with the reason:
  `{cli} visual {id} no --why "<what the work changed instead>"`.
