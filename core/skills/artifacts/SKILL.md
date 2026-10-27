---
name: artifacts
description: Make an HTML page for the owner to read - a report, an evaluation, a documentation page - and register it so the board's Artifacts view lists it. Use when asked to "make a report page", "write an HTML page", "publish an artifact", "make an evaluation page", "make a documentation page", or when a ticket's result is a page to read.
---

# Artifacts

An artifact is a page an agent makes for the owner to read: a report, an evaluation, a documentation page. The board's Artifacts view lists each registered artifact and opens it in the board.

## Where the page goes

1. Put the page in `Docs/Artifacts/<ticket id>-<slug>.html`, for example `Docs/Artifacts/T0054-frame-rate.html`.
2. When the project keeps such pages in another directory, use that directory.
3. A page about one area of the docs can stand next to the docs of that area.

## Register it

Run this command after you write the page:

    python .octopus/todo.py artifact add <path> --title "<title>" --description "<one line>" --ticket <id>

- `<path>` is relative to the project root.
- Leave out `--title` to use the page's `<title>`.
- `--description` is one line: what the page shows.
- A page published elsewhere, such as on claude.ai, gets the same command with its `https://` URL in place of the path. The board opens it in a new tab.
- Run the command again to change the title, the description or the ticket.

Other commands:

    python .octopus/todo.py artifact list            every registered artifact; marks a missing file
    python .octopus/todo.py artifact found           the HTML pages of the project that are not registered
    python .octopus/todo.py artifact remove <path>   take the entry off the list; the file stays

The list is `.octopus/artifacts.json`. Do not edit it by hand.

## What a good page is

- **One self-contained HTML file.** Put the CSS and the JavaScript inline. Load nothing from the network. Give the page a `<title>`.
- **Readable on a phone.** Add `<meta name="viewport" content="width=device-width, initial-scale=1">`. Use a 16px side gutter and a maximum width. Make sure no line scrolls the page sideways: let wide tables scroll inside their own box.
- **Light and dark mode.** Set the colors as CSS variables on `:root`, and set them again under `@media (prefers-color-scheme: dark)`. Give `body` a background color.
- **Relative links work.** A link to another file of the project, such as `../Game/index.md` or `chart.png`, opens in the board. A `.md` page opens rendered.
- **Facts only.** Every number and claim on the page comes from a measurement, a file or a command that the page names.

The board shows the page in a sandbox: its scripts run, but the page cannot read the board, its cookies or its storage, and cannot call the board's actions.

## The wiki

A wiki page is a `.md` file under the wiki directory, `Docs/` by default. It needs no registration: the Artifacts view lists every `.md` file there and renders it. `artifacts` in `.octopus/project.json` changes the directories:

    "artifacts": {"wiki": "Docs", "scan": ["Docs"]}

`wiki` is the wiki directory. `scan` lists the directories where `artifact found` looks for HTML pages.
