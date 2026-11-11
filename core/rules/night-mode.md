# Night mode

*Always applies, in every project, to every session on a ticket, the manager's too.*

**Every night from 00:00 to 08:00 local time is night mode. In night mode no ticket waits for the owner's input.** The owner gives no answer in these hours. The work goes on without the owner.

1. **Ask the owner nothing.** Use no question dialog, run no `report <id> input`, and end no turn with a question to the owner.
2. **Decide in place of the owner.** Decide from the rules, the ticket's order, the code and the evidence. Choose the route that the rules and the evidence support, and that the owner can change in the morning with the least work. Record each decision on the ticket, then go on with the work:

       python .octopus/todo.py decide <id> --question "<the question>" --decision "<what you decided, and why>"

   The command writes the decision under `## Decisions in night mode`.
3. **Collect a question that only the owner can answer for the morning,** and do all the work that does not need the answer:

       python .octopus/todo.py morning <id> --question "<the question>"

   The command writes the question under `## Questions for the morning`. When no work is left that does not need the answer, run `report <id> event --why "the owner's answer in the morning: <the question>" --mode <mode>`, and end the turn with no question.
4. **Night mode never permits an action that the rules forbid without the owner's approval.** A project-wide README change, a permit and an order only the owner gives stay questions for the morning.

**The engine enforces it in night mode.**

- The question guard refuses `AskUserQuestion` in a session on a ticket, and names `decide` and `morning`.
- `report <id> input` refuses, and names `decide` and `morning`.
- Each scan of the sessions (the board, `sessions`, the manager's check) types the night order into a session on a ticket whose state is `input`: a question on the last line of its message, the judge's verdict `input`, or an `input` report from before 00:00. It types once per turn and at most 3 times a night into one session. It never types into a dialog, or into an input box that holds unsent text.
- The `Stop` hold that tells a session to ask the owner gives the night order instead.

**In the morning the questions reach the owner.** Outside night mode, the manager's update adds the section `Questions for the morning`: each open question, with its project, ticket id and title, on every update until it is answered. The manager records the owner's answer, then gives it to the ticket's agent:

    python .octopus/todo.py morning <id> --answer <n> --text "<the owner's answer, in neutral words>"

`morning` with no ticket lists the open questions of the project.
