# What enters the Octopus AI repository

*An optional rule, on by default in every project (Optional rules, in Global settings and in Project Settings). Governs every file tracked in the
Octopus AI repository, and every memory file of a session.*

**The Octopus AI repository holds general tooling only:** the engine, the rules, the packs, the
tests and the docs. It never holds what is true of one machine, one person or one project.

## Never in a tracked file

| Kind | Examples of what stays out |
|---|---|
| a registered project | its name, its root's directory name, a word of its name |
| a project's ticket | a project's name followed by one of its ticket ids |
| a project's identifier | a name only one project has: its game, a module, a plugin, a level, an asset, a class |
| a machine path | a registered root or its parent, a home directory, `%APPDATA%`, this checkout's own path |
| a person | a login name, a git name, an e-mail address |
| a session | a session id |

Tests, examples and docs use neutral names: `Sample Game`, `X:/Tools/...`, `T0001`. A comment that
explains a test keeps its meaning and names no project.

## Project facts live in the project

**A project-specific order or fact goes into that project:** its own `.agents/rules/`, its
tickets, its docs. Never into the Octopus AI repository, and never into a session's memory files.
A memory file holds only what is true of the owner and of every project.
