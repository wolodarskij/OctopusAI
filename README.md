# Octopus AI

Octopus AI is a kanban-like board for orchestrating multiple coding agents, working together or
independently across multiple projects. Tasks are tickets, planned, implemented and verified by
agents under your direct control. Work can be done both from the command line and from a web
interface.

Each ticket is a Markdown file with a short header: id, title, severity, difficulty and status.
Tickets live in the `TODO/` folder of the project. The subfolders correspond to the columns on the
board; by default we have Pipeline (not started), InProgress, Blocked, Verification and Done.
Moving a ticket on the kanban board also moves its file. Each project can rename columns and add
its own.

An agent is started on a ticket in one of four modes:

- **Planning** agent (optional, needed rather for bigger tickets) writes a plan into the ticket and
  changes nothing else.
- **Working** agent (the most important one, gets things done) implements the ticket and moves it
  to Verification.
- **Reviewing** agent checks whether the ticket itself is still correct and sized right.
- **Verifying** agent checks the work of another agent; it is the only one that can move a ticket
  to Done.

A repeatable ticket is a job that runs again and again: after each run it returns to Pipeline.

- **Tester** agent runs a repeating test and reports the result. It may execute and play a game or
  use an application like a user would. It is able to create tickets based on the test results.

The **manager** agent runs permanently, one for all projects. It can be used to control other
agents, which is especially useful via a remote session when there is no direct access to the
kanban board or the command line.

## Install and quick start

Octopus AI uses Python 3, git, and at least one AI-provider's command-line tool: Claude Code,
Codex, or one of your own. The starter installs what is missing.
Octopus AI runs on Windows and macOS; on a Mac, read [macOS](#macos) first. Linux version is coming soon.

    git clone https://github.com/wolodarskij/OctopusAI.git
    cd OctopusAI
    AgentBoard.bat or AgentBoard.command

Or download the repository as a ZIP file, unpack it, and run `AgentBoard.bat` on Windows or
`AgentBoard.command` on a Mac. You need nothing else:

- When Python 3.10 or newer or git is missing, the starter runs the setup script first:
  `tools\setup-windows.ps1` on [Windows](#windows), `tools/setup-mac.sh` on [macOS](#macos).
- The first run registers the Octopus AI folder itself as the project "Octopus AI" and installs
  Octopus AI into it. An agent that you open in the folder loads the rules at once. The board opens
  on this project. **Add project** registers the folder of your own project.
- Then the kanban board opens in your browser.

`AgentBoard.bat --project <folder>` opens the board on that folder. When the folder is in no
project, also when it is empty, the board installs Octopus AI into it first. When you start the
board from an empty folder, it does the same with that folder. `AgentBoard.command` does the same
on a Mac.

1. **Add project** registers a project folder and installs Octopus AI into it.
2. **New task** creates a ticket in Pipeline.
3. Open the ticket and press **Worker**: a working agent starts on it in a new terminal.

The same from the command line:

    python octopus.py projects add X:/Projects/MyGame --name "My Game"
    cd X:/Projects/MyGame
    python .octopus/todo.py new --title "Bug: the score resets on load" --severity High
    python .octopus/todo.py work T0001

## Windows

On Windows, run `tools\setup-windows.bat` once, or run it from the `OctopusAI` folder:

    tools\setup-windows.bat                           asks before each install
    tools\setup-windows.bat -Yes                      installs whatever is missing without asking
    tools\setup-windows.bat -Providers claude,codex   these AI-providers' tools: claude, codex

It installs only what is missing, for your user. It uses winget when Windows has it, else the
installer from the maker's site. It installs:

- Python 3.10 or newer, with the plain `python` command that Octopus AI calls.
- git.
- Windows Terminal. Windows 11 has it.
- The command-line tools of the built-in AI-providers that you choose: Claude Code, Codex.

Octopus AI needs no Python packages. At the end, the script shows the version of each tool, how to
fix each failure, and how to log in to each AI-provider. Then open a new terminal. When you run it
again, it changes nothing that is correct.

## macOS

On a Mac, run the setup script once from the `OctopusAI` folder:

    tools/setup-mac.sh             asks before each install
    tools/setup-mac.sh --yes       installs whatever is missing without asking

It installs only what is missing: Homebrew (it asks for your password), Python with the plain
`python` command that Octopus AI calls, tmux, and Claude Code. Then open a new Terminal window.

What is different from Windows:

- **Agents run inside tmux.** Each agent gets a Terminal window of its own, as it gets a Windows
  Terminal window on Windows. Closing the window does not stop the agent: it keeps running in
  tmux, and the board still shows it. Settings → Terminals → Own pseudoconsole runs agents with no
  window at all, and the board shows their screen.
- **The board can show and type into every agent's terminal**, through tmux.
- **Bringing an agent's window to the front** asks macOS once for permission to control Terminal.
- **The list of projects** is kept in `~/.config/OctopusAI/` instead of `%APPDATA%/OctopusAI/`.

To attach to an agent's terminal by hand: `tmux -L octopus attach -t octopus-<session id>`.

## AI-providers

An AI-provider is the coding tool that runs each agent: Claude Code and Codex are built in. You can
add your own: any command-line tool that takes a prompt, or a self-hosted model run through Codex.

- **Default AI-provider of a project:** Settings → Launch defaults.
- **Another AI-provider for one launch:** choose it in the launch dialog.
- **Add your own AI-provider:** Global settings.
- **Models and efforts:** each AI-provider lists the models and reasoning efforts it can run, in
  Global settings, so a new model needs no code change. A launch offers only those of the
  AI-provider it starts and refuses any other; switching the AI-provider resets the model to its
  default and says so. A project adds models per AI-provider in Settings → Launch defaults.

Currently Claude Code has the best support; the rest is work in progress.

## Skill and tool packs

A pack adds skills, rules and tools for one kind of project. Octopus AI ships five:

- **unreal**: Unreal Engine rules, and tools to start and control the editor.
- **blueprint**: reads Blueprint assets and turns them into pseudocode an agent can work with.
  Useful for C++ or AngelScript conversion.
- **ue-angelscript**: rules and checks for Unreal projects scripted in AngelScript.
- **browser-game**: type check, lint, tests and a dev server for TypeScript. Useful for browser game
  prototypes.
- **git**: controls which agents may commit, sync or branch.

Add your own pack from a folder or a git repository in Global settings. A project picks its packs
as checkboxes when it is added, or later in its Settings. Single skills can be installed there too,
from a git repository, a folder, or the Claude or Codex skill library.

## Command line

Tickets, from the project folder:

    python .octopus/todo.py board                          the columns, side by side
    python .octopus/todo.py new --title "..." --severity High
    python .octopus/todo.py show T0001
    python .octopus/todo.py move T0001 Blocked
    python .octopus/todo.py plan T0001                     start a planning agent
    python .octopus/todo.py work T0001                     start a working agent
    python .octopus/todo.py review T0001                   start a reviewing agent
    python .octopus/todo.py verify T0001                   start a verifying agent
    python .octopus/todo.py --help                         every command

From any folder, `python octopus.py todo --project "My Game" <command>` does the same.

Projects:

    python octopus.py board                                the kanban board in the browser
    python octopus.py projects add <path>                  register a project and install Octopus AI into it
    python octopus.py projects remove <path>               take it off the board; its files stay
    python octopus.py install <path> --packs unreal,git    install Octopus AI with these packs
    python octopus.py packs <path> set unreal,git          exactly these packs
    python octopus.py update --all                         update every project after Octopus AI changed
    python octopus.py status <path>                        every installed file: same, edited or missing,
                                                           and whether every agent loads the no-narration rule
    python octopus.py uninstall <path>                     remove everything Octopus AI installed

Packs, skills and AI-providers:

    python octopus.py pack-registry add <folder or git url>
    python octopus.py skills <path> add --library claude pdf
    python octopus.py skills <path> add --git <url>
    python octopus.py providers add <id> --json "{...}"    add your own AI-provider
    python octopus.py providers check <id>                 does it start and answer

## Manager and remote control

The board starts the manager session, and restarts it if it stops. Any Claude Code session becomes
the manager when you say "run manager" or use the skill `/octopusmanager`.

Give the manager orders in plain words, for any project: file a ticket, start an agent, tell you what
waits for you. Every 10 minutes it reports what changed: which agents finished, which wait for your
input, which are working. It can be especially useful when controlling agents remotely from your
phone: the manager is your clever CLI alternative.

Every Claude Code agent, the manager included, starts with Remote Control on, so you can answer it
from the Claude app on your phone or from claude.ai. Turn it off for one ticket in the card menu →
Remote control, or for a whole project with `"remoteControl": false` in `.octopus/project.json`.

## Reports that carry only what you asked

Each agent that finishes a ticket writes one outcome line on it: what now works, in at most 20
words. The manager's update shows that line, the lane and the commit, and nothing more. At the end
of every turn a small model checks each sentence of the agent's last message against your request.
The reply you saw stays as it is and never comes a second time; the sentences you did not ask for,
such as an unasked measurement or caveat, go to the agent with your next prompt, so it writes no
more of them. Turn the check off for a project with `"replyCheck": false` in `.octopus/project.json`.
