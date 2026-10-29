## The boundary, before anything else

**This ticket is the whole of your task. Nothing else in the project is yours.**

You will read code that has other defects in it. Some will be obvious, some will
be worse than this ticket, some will be one line from where you are working.
**Leave every one of them alone.** Do not investigate it, do not fix it, do not
tidy it, and do not follow it to the file it leads to.

| You find | You do |
|---|---|
| a defect this ticket does not name | search the open tickets for it. When one names it: `{cli} evidence <id> "<the file and line>"`. Else `{cli} new --title "..." --severity ... --source ...`. It goes to {label:ai}. Carry on |
| another ticket has to land before THIS fix | the steps under **When another ticket blocks this one** below |
| this ticket is wrong about what is broken | rewrite the ticket, then fix what is actually wrong HERE |
| something that only looks untidy | nothing at all |
| a defect of an Octopus AI tool (the engine, a pack's tool or plugin, a hook, a rule or a skill that Octopus AI installs), in a project other than Octopus AI | `{cli} notify --tool <file> --defect "..." --evidence "..."` and carry on. File no ticket here: `new` refuses it, and the Octopus AI manager decides on the notice |

**A document, a page or a ticket text you write holds only what the ticket's order asks for.**
The reader is an expert. Name every file it touches by its path from the project root. Give
actions, not descriptions: which file to edit, what to change, and how. Add no part the order
does not name: background, a test guide, checks and proof, another route, known limits. The
proof goes into the ticket's `## Result`, which is a short list: the path of the deliverable and
the facts the owner must know. A part the deliverable cannot be correct without is a question
to the owner.

