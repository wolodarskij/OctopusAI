# Visual work delivers renders

*Always applies, in every project, to every ticket that does visual work on textures, 3D meshes or the materials that show them.*

**A ticket with visual work on textures or 3D meshes is finished only with renders of the result.** A render is an image or a video that shows the changed texture, mesh or material as it shows in the engine, in the game or in a viewer. The texture file itself is no render.

1. **Make the renders before you finish.** Save them in `TODO/.renders/<id>/`, as `.png`, `.jpg`, `.webp`, `.gif`, `.mp4` or `.webm`. Show each changed asset from the angles that show the change.
2. **List each render under `## Result`,** by its path from the project root, in backticks, one line each.
3. **The engine enforces it.** `move` to Verification, and to Done as `done`, refuses visual work whose `## Result` names no render file that exists. The engine takes a ticket for visual work when:
   - it carries `visual: yes`;
   - it carries no `visual:`, and its title, its first section or its `## Owner's orders` name a texture, a mesh or a material;
   - it carries no `visual:`, and a git commit of the ticket changes a texture, mesh or material file.
4. **A ticket whose work changes no texture, mesh or material is no visual work.** Say so with the reason:

       python .octopus/todo.py visual <id> no --why "<what the work changed instead>"

   The command writes `visual: no`, and the reason under `## Not visual work`. `visual <id> yes` marks visual work.
5. **A verify session opens each render** and checks that it shows the result of the order. A missing render, or one that does not show the change, sends the ticket back. It also checks the reason of `visual: no` against the change.
6. **The renders reach the owner.** The manager's update lists each render of a finished ticket as a `render:` line, and the manager sends each render file to the owner.
