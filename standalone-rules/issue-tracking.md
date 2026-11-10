# Issue tracking

*Applies to every ticket, issue or task item that a session files or works, in any tracker.*

## The title says what kind of ticket it is

- **A bug title** starts with `Bug: ` and says what is wrong in code or content: "Bug: The list drops an item that has no severity".
- **A feature title** starts with `Feature: ` and says, as a positive statement, what the program should do: "Feature: Illness should mutate a person". Never write a feature as a negative. A feature ticket has the section `## What it should do` in place of `## What is wrong`.
- **A documentation bug title** starts with `Documentation Bug: ` and says what in the documentation is wrong: "Documentation Bug: The setup page names a build command that does not exist". It has the section `## What is wrong`. A defect in documentation is always a documentation bug, never a bug.
- **A documentation title** starts with `Documentation: ` and says what the documentation should state: "Documentation: The setup page names the build command". It has the section `## What the documentation should say`.
- **Documentation** is docs, doc pages, generated doc HTML, a README file and the comments of a doc tree. The work of a documentation bug and of a documentation ticket changes only documentation. A ticket that changes code or content and also its docs is a bug or a feature.
- **The source of a ticket names the file that is wrong.** A bug in code that a doc page shows names the code as its source, and the doc page as its evidence.

## The body states the order and nothing more

Keep the scope that the user gave. Add no part, step, test, check, proof or background that the user did not ask for. Name a function with its parameters and its return type. Give the evidence: the file and line you read, or the command you ran. A result is a short list: the path of the deliverable and the facts the user must know.

## One defect, one ticket

**Before you file a ticket, search the open tickets for the same defect or the same request.** When one exists, add your evidence to it, and file nothing. When a close ticket differs, file the new one and write why it differs from each of them.

**A ticket that nobody asked for is marked as such.** A ticket that a session files from its own finding, with no request of the user that names its subject, carries a mark that says an AI filed it, and waits for the user to accept it. Never start such a ticket on your own.

## What may be called wrong

**A thing is wrong only when it breaks a written reference.** The references are:

1. the ticket's order;
2. an order of the user, or a rule of the project;
3. the documented behavior: a doc, a comment, a test, a type or a schema;
4. an observed failure: a crash, an error, lost data, or a result that differs from what the program states.

Nothing else is wrong. When no reference shows the intent of a thing, it is a question to the user, never a defect. A defect outside the ticket's order is not wrong with this ticket: it goes to its own ticket, and this ticket does not describe it.

**A ticket is done, or wrong, or it asks the user. It is never two of them.** A finished ticket that says something is still wrong ("still fails", "does not work", "known limit", "works partly") contradicts itself, and is prohibited.

**A session that did the work never closes its own ticket.** It lands the work and marks the ticket for verification. A separate session, or the user, measures the work with a test, a check command, a compile, or the code read against the ticket, then closes the ticket or sends it back. **Never delete a ticket to mark it finished.** Close it with a status that says how it closed: done, closed by another ticket, or cancelled.

## Severity, difficulty, and priority

**Severity is what the user loses. Difficulty is what the fix costs.** They are independent. A ticket that mixes them gets worked in the wrong order.

### Severity

Ask: **what does a user or the saved data get wrong because of this?** Effort never enters it.

| Severity | The user or the data |
|---|---|
| **High** | gets a wrong result, loses data, or the program stops |
| **Medium** | gets a correct result today by accident: two copies of one rule that agree for now, a validation that nothing runs |
| **Low** | gets nothing wrong: a name, a duplicate, a missing comment |

**The test between High and Medium: can a user see it now?** A wrong number on screen is High. Two functions that both compute it correctly are Medium, because the defect is the next edit.

Never raise severity because the fix is urgent to you. Never lower it because the fix is hard.

### Difficulty

Ask: **how much is unknown, and how many sites does the change touch?** Count sites. Do not count error lines.

| Difficulty | |
|---|---|
| **small** | one file, one site, and the change is known |
| **medium** | several sites in one layer, and the change is known |
| **large** | many sites, or several layers, and the change is known |
| **unknown** | nobody can size the change: the intent, the measurement or the decision is missing |

**`unknown` and `large` are different answers.** `large` means the change is known and big. `unknown` means the ticket's question has no answer yet, so nobody can start it. Answer the question, then size the ticket.

### Priority is derived

    severity first, then difficulty ascending

`unknown` sorts last at every severity. There is no priority field. If a ticket must go first, its severity is wrong: correct the severity.

## When analyzing code

When the user asks you to **analyze** code, at any scope:

- Find inconsistencies, bugs, missing implementations, code smells and improvements.
- **File a ticket** for each finding.
- Do **not** fix them. At the end, say "Added N ticket(s)."

**Ask before you log an ambiguous finding.** A finding is ambiguous when the code, the comments and the project documentation do not show the intent:

- code that looks wrong but can be intentional: an unusual algorithm, a deliberate no-op, a placeholder;
- a pattern that can be a bug or a design choice;
- anything where you assume how the code should work.

Log only what you are sure of: a clear bug, a broken documented convention, or a fault that the code around it makes obvious.

## When not to file a ticket

Normal edits, features and refactors that the user asked for: log nothing.
