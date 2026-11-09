# No local information in a shared repository

*Applies to every tracked file of a repository that other people or other machines use, and to every memory file of a session.*

**A shared repository holds no fact of one machine or one person.** Write such a fact into a local file that version control ignores, or into the user's own settings.

## Never in a tracked file

| Kind | Examples of what stays out |
|---|---|
| a machine path | a home directory, `%APPDATA%`, the absolute path of this checkout or of another project |
| a person | a login name, a git name, an e-mail address |
| a session | a session id |
| another project | its name, its directory name, its ticket ids, a name only that project has |

Tests, examples and docs use neutral names: `Sample Game`, `X:/Tools/...`, `T0001`. A comment that explains a test keeps its meaning and names no machine, person or other project.

## Project facts live in the project

**A project-specific order or fact goes into that project:** its own rules, its tickets, its docs. Never into a session's memory files. A memory file holds only what is true of the user and of every project.
