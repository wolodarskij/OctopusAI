"""session_state.py - which launched session waits at its prompt.

A session launched from the Agent Board runs an interactive `claude`, so `claude.exe`
stays alive after the work is done. A process test therefore reports a finished session
and a working session the same way, and the board's "Close finished" button sees nothing
to close.

The hooks tell them apart. `Stop` fires when Claude gives the prompt back.
`StopFailure` fires instead of `Stop` when the turn ends in an API error: a rate limit,
an overload, a network fault. `UserPromptSubmit` and `SessionStart` fire when Claude takes
it again, except a `SessionStart` of a compaction: Claude Code also compacts a session idle at
its prompt, and that session still waits for the same thing. Each event writes
or removes one small file, and `todo.py live_sessions` reads those files.

A session at its prompt either waits for the owner's answer or waits for nothing. The
marker records which it looks like from the last message Claude wrote
(`last_assistant_message` of the `Stop` payload): line 1 is the time, line 2 is `finished`
when its last line is `WORK IS FINISHED` (every brief asks for it; `INVESTIGATION FINISHED`
from older briefs still reads) or one of its last 3 lines says its ticket is in the verify or
the closed lane ("T0069 is in Verification", "T0069 is done", "moved T0069 to Verification"),
and line 3 then says which. Else line 2 is `asks` when its last line ends with `?`, and
`unsure` when a `?` stands only higher in its last 3 lines; line 3 then keeps that question,
which the board quotes on its hover.

THE JUDGE. A message that none of those matches, `unsure` among them, goes to the judge:
the `Stop` hook writes it to `<key>.last` and starts `session_state.py --judge` detached,
without waiting for it. The judge runs the project's headless provider (Claude Haiku or Codex) with the session's
ticket, the message, Claude Code's last recap of the session (the `away_summary` row of its
transcript) and the agents and jobs it waits for, and writes `<key>.judge`: line 1 the marker
time it judged, line 2 `input`, `finished`, `working` or `unsure`, line 3 why. Only what the session
itself asks the owner is `input`: a report that lists other sessions' questions is not. A
session whose message or recap says it runs a recurring task is `working`, never `finished`.
Every verdict explains itself and quotes its proof, words copied from the message or the
recap, or the list of what it waits for: for `input` the question, for `finished` the line
that says the work was done, for `working` the line that names what it waits for. A verdict
whose quote stands in none of them, and an `input` whose quote is another ticket's question
that a list only relays, is no verdict (`check_verdict`): the judge is asked once more, told
why. Line 3 is the explanation and the quote, which the board shows
on its hover. A verdict counts only while the marker still
carries that time. The judge's headless CLI runs with `OCTOPUS_JUDGE` set, and every hook
under it returns at once, so it writes no marker and starts no judge. `<key>.judging` holds
the marker time of the turn a judge was started on: one judge per turn. `todo.py
live_sessions` starts the judge too, on a turn that ended before the judge existed, with the
last message from the transcript when the hook kept none.

`unsure` says the message does not show what the session waits for, and needs no quote.
`todo.py live_sessions` then types `/recap` into the session once per turn (`<key>.recap`:
the turn, when it was sent, when its judge started, the recap), and when the output row of
the command stands in the transcript (`recap_after`) it starts the judge on the recap
(`--judge ... --recap`), whose verdict carries `recap` on line 4 (`judge_source`).

A STALE TURN. A turn that has not ended and wrote nothing to its transcript for
`todo.STALE_SECONDS` goes to the judge too (`ensure_judged_stale`): `<key>.stale` holds the
transcript's time, the stamp the verdict carries in place of a marker time (`turn_stamp`),
and its last action, which the prompt names. A transcript written again is a new stamp. A
turn that ended by its transcript and has no marker gets the one the `Stop` hook would have
written (`mark_stale`): the hook missed it, or a slash command took the prompt.
`transcript_activity` reads what the transcript says of the current turn: whether it ended,
its last update, and what it does now, its last tool call.

LOST FILES. A session whose Claude or shell still runs keeps its files, however old. A
session whose files are gone anyway (an older hook pruned them after 7 days) is read back
from its transcript by `todo.py live_sessions` (`transcript_tail`, `recover`): its live
record gets `last_event` `Recovered`, and its marker the words the `Stop` hook would have
written.
A session can also say it outright: `todo.py report <id> input|event|finished` writes
`<key>.report`. `input` names its question on line 2, which the board shows. `event` says it
waits for something outside the project, such as an editor restart, and line 2 of the report
says what. The owner's next prompt removes both, so an answer clears the question. A report
belongs to the turn it was made in: the `Stop` hook drops a report written before the
previous turn ended, because a turn that began without the owner's prompt (a scheduled
prompt, a call back after background work) removed nothing.

THE REPLY CHECK. After the `Stop` hook has written the marker and started the judge and the
closer, it starts `reply_check.py` on the last message, detached (`start_reply_check`). The
check never blocks the turn and never makes the session write its message again: the owner
saw that message already. A sentence that fails goes into `<session>.replynote`, and the
session's next `UserPromptSubmit` gives it to the session as context (`reply_note`).

A launched session that is done closes its own terminal: the `Stop` hook of a `plan` session
whose ticket is `ready to implement`, and of a session of any launch mode whose last message
ends with `WORK IS FINISHED` or says its ticket is in the verify or closed lane, starts
`todo.py close <id> --when-finished`, which closes it once its turn has ended and it is
`finished` (`close_finished`). A believable `WORK IS FINISHED` (the ticket agrees,
`todo.finish_undone`, and no question was reported in the turn) adds `--now`: the terminal
closes at once, with no wait for the state word. An unbelievable one starts no closer, and
the session waits for `input` ("said WORK IS FINISHED with the work undone"); so does a
planner whose plan leaves a decision, a name or a value open (`todo.plan_open`). A judge
that writes `finished` starts the waiting closer. The board closes every other `finished`
launched session (`todo.autoclose_sessions`). A session the owner has written to closes
only by its own `WORK IS FINISHED`. A manager never closes.
`todo.py live_sessions` turns them, with the ticket's lane, into the session's `wait`.

A turn that ends in an API error waits for the owner to try again. `StopFailure` writes
the marker with `error` on line 2 and the error type of its payload on line 3, and starts no
judge: the last message is the error, not Claude's. A turn read back from the transcript
(`mark_stale`, `recover`) whose last row is Claude Code's API-error message (`api_error`)
gets the same marker, and no judge either.

A dialog stops a session in the middle of its turn, and no `Stop` fires while it waits: a
permission prompt, an MCP elicitation (the `Notification` event), an `AskUserQuestion`, and
the approval of a plan (`PreToolUse` of `ExitPlanMode`). Each writes the marker with
`dialog` on line 2. `PostToolUse` fires once the owner answered and the tool ran, and removes
a `dialog` marker written before it started; a marker of any other word is the `Stop` hook's
and stays.

A session can end its turn while its own background work goes on: a subagent, or a shell
command it ran in the background. Claude Code re-invokes it when that work completes, so it
waits for neither the owner nor anything outside. The `Stop` payload lists that work in
`background_tasks`, a field the hook documentation does not name, and `<key>.tasks` keeps
the ones that still run. The next turn's `Stop` rewrites it.

A session that scheduled a recurring prompt with `CronCreate` waits for that job at its
prompt, not for the owner: the blocked brief does it, and so does a manager's watcher. Two
records find the jobs, and a session holds every job either of them names (`all_jobs`):
`PostToolUse` of `CronCreate` writes the job into `<key>.cron`, `PostToolUse` of `CronDelete`
takes it out; and the transcript (`transcript_jobs`): each `CronCreate` and the job id its
result names, each `CronDelete`, and each `scheduled_task_fire` row, the job firing. A resumed
Claude keeps its jobs (the manager T0058's job 22b1d598 fired on for a day after its Claude
was resumed in another shell), so only a new session starts with none. A recurring job
expires 7 days after it was made; one that fired within the last 2 hours still runs. A
one-shot job is not recorded.

A marker is named for the session id AND the terminal it runs in, because `work` and
`plan` share one session id: with two terminals open on one ticket, the session id alone
names both, and a marker written by the finished one would report the working one
finished too. The hook resolves its own terminal by walking up the process table.

    python core/hooks/session_state.py             read the hook payload on stdin
    python core/hooks/session_state.py --list      every session now at its prompt
    python core/hooks/session_state.py --live      every session with a live record
    python core/hooks/session_state.py --prune     drop markers older than 7 days
    python core/hooks/session_state.py --judge <session> <shell> --project <root>
                                              judge the message the Stop hook kept;
                                              with --recap, the recap the board asked for
    python core/hooks/session_state.py --reset-terminal
                                              turn off the terminal modes a crashed Claude
                                              leaves on: mouse tracking, win32-input-mode

`octopus hook` runs it for the `Stop`, `StopFailure`, `UserPromptSubmit`, `SessionStart`, `Notification`,
`PreToolUse` and `PostToolUse` events. The event name comes from the payload, so one command
serves them all.

THE PROJECT. The hook finds its project from `CLAUDE_PROJECT_DIR`, the directory the
session was launched in, else from the payload's `cwd`: the nearest directory above it that
holds `.octopus/project.json`. A directory with none above it makes the hook do nothing.
`--list`, `--live` and `--prune` take `--project <name|path>`, else the working directory.

Every event also writes `<title> - ...` into the session's window title, where `<title>` is
the project's `title`, so a Claude window of the project carries the prefix however it was
started.

Every event also writes the session's live record, `TODO/.sessions/<session_id>.live.json`:
the Claude pid, the shell above it, the terminal above the shell, and the title. That record
is how `todo.py sessions`, `focus` and `close` and the board's Sessions view find a session
the board did not launch. A record whose Claude and shell are both gone is pruned.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE.parent / "engine", HERE.parent / "win", HERE.parent / "posix"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def _dir():
    """The current project's `TODO/.sessions` directory."""
    import project
    return project.current().SESSIONS_DIR


def _title():
    """The current project's window-title prefix."""
    import project
    return project.current().title


# A marker nobody removed belongs to a session whose terminal is long gone.
KEEP_SECONDS = 7 * 24 * 3600

# The event that hands the prompt back to the owner at the end of a turn, and the events
# that take it again: the owner's next prompt, a new or resumed Claude.
IDLE_EVENTS = ("Stop",)
# The event that hands the prompt back when the turn ends in an API error instead.
STOP_FAILURE = "StopFailure"
BUSY_EVENTS = ("UserPromptSubmit", "SessionStart")

# A dialog in the middle of a turn: the notification types of one the owner must answer,
# and the tools that open one.
DIALOG_NOTIFICATIONS = ("permission_prompt", "elicitation_dialog", "elicitation_url_dialog")
ASK_TOOL = "AskUserQuestion"
DIALOG_TOOLS = (ASK_TOOL, "ExitPlanMode")

# The tools that schedule a prompt into the session and cancel it, and how long a recurring
# job lives before Claude deletes it.
CRON_CREATE, CRON_DELETE = "CronCreate", "CronDelete"
JOB_SECONDS = 7 * 24 * 3600

# The `SessionStart` source of a new session: it holds no scheduled job. A resumed Claude
# keeps the jobs of the session it resumes.
NEW_PROCESS = ("startup",)
# The `SessionStart` source that takes no prompt: a compaction. Claude Code compacts a session
# that sits at its prompt too ("Compacted while idle"), and that session still waits for what
# its marker and its report say. A compaction inside a turn finds no marker to keep: the
# turn's prompt removed it.
KEEPS_STATE = ("compact",)
# A job that fired this recently still runs, however old its `CronCreate` is.
FIRE_SECONDS = 2 * 3600

# The images a Claude session runs as, the shells a session runs in, and the terminals
# those shells run in: Windows' images, then the executables of macOS and Linux
# (`posixproc` names a process by its file name, without an extension).
CLAUDE_IMAGES = ("claude.exe", "node.exe", "claude", "node")
SHELL_IMAGES = ("cmd.exe", "powershell.exe", "pwsh.exe", "bash.exe", "sh.exe", "nu.exe",
                "sh", "bash", "zsh", "dash", "fish", "nu", "pwsh")
TERMINAL_IMAGES = ("windowsterminal.exe", "openconsole.exe", "conhost.exe",
                   "tmux", "terminal", "iterm2", "gnome-terminal-server", "konsole", "xterm")

_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")


def key(session_id, shell=0):
    """The name of one terminal's marker. Shell 0 means the terminal is not known."""
    return "%s.%d" % (session_id.lower(), int(shell or 0))


def _marker(session_id, shell=0):
    return _dir() / ("%s.idle" % key(session_id, shell))


def _procs():
    try:
        import winproc
        return winproc.processes()
    except Exception:                                   # noqa: BLE001 - a hook never raises
        return {}


def owning_shell(procs=None):
    """The pid of the shell that this hook's session runs in, or 0.

    The hook is a grandchild of the session's Claude, so the walk finds the nearest
    Claude above it. Its shell is that Claude's own parent. A Claude that a program started,
    such as the board's `claude -p` under `python.exe`, has no shell: a shell further up
    runs the program, and closing it would end the program."""
    procs = _procs() if procs is None else procs
    claude = owning_claude(procs)
    ppid = procs.get(claude, (0, ""))[0] if claude else 0
    return ppid if ppid in procs and procs[ppid][1].lower() in SHELL_IMAGES else 0


def terminal_of(pid, procs):
    """The terminal process above `pid`, as `(pid, image)`, or `(0, "")`."""
    steps = 0
    while pid in procs and steps < 12:
        ppid, name = procs[pid]
        if name.lower() in TERMINAL_IMAGES:
            return pid, name.lower()
        pid, steps = ppid, steps + 1
    return 0, ""


def at_prompt(claude_pid, markers, session_id, shell=0):
    """Whether this terminal waits for the owner, and since when.

    A terminal with no Claude under it is at the shell prompt. A terminal whose Claude
    still runs is at the Claude prompt when the `Stop` hook left a marker."""
    if not claude_pid:
        return True, None
    when = idle_since(markers, session_id, shell)
    return when is not None, when


# The characters a message may close a question with after its `?`: markdown emphasis,
# code, a closing bracket or quote.
_TRAIL = " \t*_`)\"'"


def _lines(text):
    return [ln for ln in (text or "").splitlines() if ln.strip()]


def asks(text):
    """Whether the message may end with a question: one of its last 3 non-empty lines ends
    with `?`, trailing markdown and quotes aside."""
    return any(ln.rstrip(_TRAIL).endswith("?") for ln in _lines(text)[-3:])


def asks_last(text):
    """Whether the message's last non-empty line ends with `?`: the question the briefs ask
    for, which needs no judge."""
    lines = _lines(text)
    return bool(lines) and lines[-1].rstrip(_TRAIL).endswith("?")


# The longest question the marker keeps for the board's hover, and the longest text of a
# dialog: its questions with their options.
QUESTION_CHARS = 200
DIALOG_CHARS = 1000


def question_of(text):
    """The question the message may end with, for the board's hover: the last sentence of the
    lowest of its last 3 non-empty lines that ends with `?`, markdown aside, at most
    `QUESTION_CHARS` long. "" when none ends with `?`."""
    for ln in reversed(_lines(text)[-3:]):
        ln = " ".join(re.sub(r"[*_`]", "", ln).split()).strip(" \t\"')>-")
        if not ln.endswith("?"):
            continue
        q = re.split(r"(?<=[.!:;])\s+", ln)[-1]
        return q if len(q) <= QUESTION_CHARS else "..." + q[-(QUESTION_CHARS - 3):]
    return ""


# The line a session ends its last message with when it has no question for the owner.
# Every brief asks for it: a session may leave its ticket where the launch put it, so the
# lane cannot tell a finished session from one that waits for an answer. Sessions launched
# on an older brief end with the second.
FINISHED_LINE = "WORK IS FINISHED"
FINISHED_LINES = (FINISHED_LINE, "INVESTIGATION FINISHED")


def says_finished(text):
    """The finished line the message's last non-empty line is, trailing markdown and quotes
    aside, or ""."""
    lines = _lines(text)
    last = lines[-1].strip().strip(_TRAIL) if lines else ""
    return last if last in FINISHED_LINES else ""


def _done_re():
    """The statement that a ticket is in the verify or the closed lane: `<id> is in <lane>`,
    `<id> is done`, `moved <id> to <lane>`, `<id>: <lane>`. The lanes are the project's
    directories and labels of the two roles, and the words Verification and Done."""
    import project
    p = project.current()
    names = {"Verification", "Done"}
    for role in ("verify", "closed"):
        names.update((p.lane(role), p.label(role)))
    lane = "(?:%s)" % "|".join(re.escape(n).replace(r"\ ", r"\s+")
                               for n in sorted(names, key=len, reverse=True))
    tid = r"\b[A-Z]+\d{3,}\b"
    return re.compile(
        r"(?:%(id)s\s+(?:is|was|has\s+been)\s+(?:now\s+)?(?:moved\s+)?(?:in\s+|to\s+)?(?:the\s+)?"
        r"%(lane)s|\bmoved\s+(?:ticket\s+)?%(id)s\s+(?:in)?to\s+(?:the\s+)?%(lane)s"
        r"|%(id)s\s*:\s*(?:the\s+)?%(lane)s)(?!\w)" % {"id": tid, "lane": lane}, re.I)


_TICKET_ID = re.compile(r"\b[A-Z]+\d{3,}\b", re.I)


def about_ticket(said, ticket=""):
    """Whether what a message said to mark its session finished is about the session's own
    work: a finished line, or a statement that names `ticket`. A statement about another
    ticket, a child or a blocker, says nothing of the session's own work. With no `ticket`,
    every statement counts."""
    if not said:
        return False
    if said in FINISHED_LINES or not ticket:
        return True
    m = _TICKET_ID.search(said)
    return bool(m) and m.group(0).upper() == ticket.upper()


def says_done(text, ticket=""):
    """The statement in one of the message's last 3 non-empty lines that a ticket is in the
    verify or the closed lane, markdown aside, or "". A line that ends with `?` asks, and
    states nothing. With `ticket`, only a statement about that ticket counts
    (`about_ticket`)."""
    try:
        pat = _done_re()
    except Exception:                                   # noqa: BLE001 - no project, no lanes
        return ""
    for ln in reversed(_lines(text)[-3:]):
        ln = re.sub(r"[*_`]", "", ln).strip()
        if ln.rstrip(_TRAIL).endswith("?"):
            continue
        for m in pat.finditer(ln):
            said = " ".join(m.group(0).split())
            if about_ticket(said, ticket):
                return said
    return ""


def mark_idle(session_id, shell=0, asks=False, finished="", dialog=False, unsure=False,
              when=None, error=""):
    """Record that this terminal waits for the owner. Line 2 of the marker is `dialog` when
    a dialog stops the turn, `error` when the turn ended in an API error (line 3 then carries
    its type), `finished` when its last message says so (line 3 then carries what it said),
    `asks` when its last line asks a question, `unsure` when a `?` stands only higher up
    (line 3 then carries the question, when `asks`, `unsure` or `dialog` is one).
    `when` is the time the turn ended, now when not given."""
    _dir().mkdir(parents=True, exist_ok=True)
    word = ("dialog" if dialog else "error" if error else "finished" if finished
            else "asks" if asks else "unsure" if unsure else "")
    said = {"error": error, "finished": finished, "asks": asks, "unsure": unsure,
            "dialog": dialog}.get(word)
    said = said if isinstance(said, str) else ""
    said = " ".join(said.split())
    _marker(session_id, shell).write_text("%.3f\n%s\n%s" % (
        time.time() if when is None else when, word, said + "\n" if said else ""),
        encoding="utf-8")


def mark_stop(session_id, shell, text, when=None, ticket=None):
    """Record the end of a turn from its last message: the finished line or the statement
    that its own ticket is done, a question on its last line, a `?` higher up, with the
    question. `ticket` is the id of the session's ticket, "" for none; `None` looks it up.
    Returns whether no rule read it, so the judge has to."""
    if ticket is None:
        ticket = own_ticket(session_id)
    drop_stale_report(session_id, shell)
    question = question_of(text)
    mark_idle(session_id, shell, asks=asks_last(text) and (question or True),
              finished=says_finished(text) or says_done(text, ticket),
              unsure=asks(text) and (question or True), when=when)
    return needs_judge(text, ticket)


def drop_stale_report(session_id, shell=0):
    """Remove this terminal's report, and an unknown terminal's, when it was written before
    the previous turn ended: a turn that began without the owner's prompt (a scheduled
    prompt, Claude Code calling the session back after its background work) keeps the report
    of the turn before, which no longer says what the session waits for. Called by the `Stop`
    hook before it writes the new marker. A dialog marker ends no turn."""
    lines = _idle_lines(session_id, shell)
    if len(lines) < 2 or lines[1].strip() == "dialog":
        return
    try:
        ended = float(lines[0].strip())
    except ValueError:
        return
    for p in (_report_path(session_id, shell), _report_path(session_id, 0)):
        try:
            if p.stat().st_mtime < ended:
                p.unlink()
        except OSError:
            pass


def clear_dialog(session_id, shell=0, before=None):
    """Remove this terminal's `dialog` marker, and an unknown terminal's, when it was written
    before `before`: the owner answered and the turn goes on. The `Stop` hook's marker and
    the report stay."""
    for p in (_marker(session_id, shell), _marker(session_id, 0)):
        lines = _first_text((p,)).splitlines()
        if len(lines) < 2 or lines[1].strip() != "dialog":
            continue
        if before is None or _when(p) < before:
            try:
                p.unlink()
            except FileNotFoundError:
                pass


def clear_answered(session_id, shell=0, before=None):
    """Remove this terminal's `input` report, and an unknown terminal's, when it was written
    before `before`: the owner answered the question dialog that the report announced, so the
    session no longer waits for that answer. A report of another word stays."""
    for p in (_report_path(session_id, shell), _report_path(session_id, 0)):
        lines = _first_text((p,)).splitlines()
        if not lines or lines[0].strip() != "input":
            continue
        try:
            if before is None or p.stat().st_mtime < before:
                p.unlink()
        except OSError:
            pass


def _report_path(session_id, shell=0):
    return _dir() / ("%s.report" % key(session_id, shell))


def _talk_path(session_id, shell=0):
    return _dir() / ("%s.talk" % key(session_id, shell))


# The prompt a launch types into a new session: "<Mode> ticket T0001. Read TODO/.briefs/...".
_LAUNCH_PROMPT = re.compile(r"\bticket T\d+\. Read \S*\.briefs/")


def mark_talk(session_id, shell, prompt):
    """At a `UserPromptSubmit`: a prompt that is not the launch's brief prompt means the owner
    talks to this session, so nothing but its own `WORK IS FINISHED` may close it. The launch
    prompt clears the mark."""
    paths = (_talk_path(session_id, shell), _talk_path(session_id, 0))
    if _LAUNCH_PROMPT.search(str(prompt or "")):
        for p in paths:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        return False
    _dir().mkdir(parents=True, exist_ok=True)
    paths[0].write_text("%.3f\n" % time.time(), encoding="utf-8")
    return True


def owner_talks(session_id, shell=0):
    """Whether the owner has written to this session since its launch prompt."""
    return any(p.exists() for p in (_talk_path(session_id, shell), _talk_path(session_id, 0)))


def mark_busy(session_id, shell=0):
    """Record that this terminal works again. Clears the marker, the report and the
    background tasks of an unknown terminal as well, because the same session wrote them."""
    for p in (_marker(session_id, shell), _marker(session_id, 0),
              _report_path(session_id, shell), _report_path(session_id, 0),
              _tasks_path(session_id, shell), _tasks_path(session_id, 0),
              _judge_path(session_id, shell), _judge_path(session_id, 0),
              _last_path(session_id, shell), _last_path(session_id, 0),
              _judging_path(session_id, shell), _judging_path(session_id, 0),
              _stale_path(session_id, shell), _stale_path(session_id, 0)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


# What a session may report about itself.
REPORTS = ("input", "event", "finished")


def report(session_id, shell, word, why=""):
    """Record what the session says it waits for: `input`, `event` or `finished`. `why`
    goes on line 2: for `input`, the question it asks; for `event`, the outside event it
    waits for."""
    if word not in REPORTS:
        raise ValueError("a report is %s, not %r" % (" or ".join(REPORTS), word))
    why = " ".join((why or "").split())
    _dir().mkdir(parents=True, exist_ok=True)
    _report_path(session_id, shell).write_text(word + "\n" + (why + "\n" if why else ""),
                                               encoding="utf-8")


def _first_text(paths):
    """The text of the first of these files that can be read, or ""."""
    for p in paths:
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


def _report_lines(session_id, shell=0):
    """The lines of the shell's own report, else of the report of an unknown terminal."""
    return _first_text((_report_path(session_id, shell),
                        _report_path(session_id, 0))).splitlines()


def reported(session_id, shell=0):
    """What the session reported, `input`, `event` or `finished`, or "" when it reported
    nothing. The shell's own report first, then the report of an unknown terminal."""
    lines = _report_lines(session_id, shell)
    word = lines[0].strip() if lines else ""
    return word if word in REPORTS else ""


def reported_why(session_id, shell=0):
    """Line 2 of the report: what the session said it waits for, or ""."""
    lines = _report_lines(session_id, shell)
    return lines[1].strip() if len(lines) > 1 else ""


def _idle_lines(session_id, shell=0):
    """The lines of this terminal's marker, else of an unknown terminal's."""
    return _first_text((_marker(session_id, shell), _marker(session_id, 0))).splitlines()


def _idle_word(session_id, shell=0):
    """Line 2 of this terminal's marker, else of an unknown terminal's: `asks`, `unsure`,
    `finished`, `dialog`, `error` or ""."""
    lines = _idle_lines(session_id, shell)
    return lines[1].strip() if len(lines) > 1 else ""


def idle_asks(session_id, shell=0):
    """Whether this terminal's marker says its last line asks a question, or a dialog
    waits for the owner's answer."""
    return _idle_word(session_id, shell) in ("asks", "dialog")


def idle_unsure(session_id, shell=0):
    """Whether this terminal's marker says a `?` stands in its last message, but not on its
    last line: the judge decides."""
    return _idle_word(session_id, shell) == "unsure"


def idle_question(session_id, shell=0):
    """The question this terminal's marker kept, for the board's hover: from its last
    message when it says `asks` or `unsure`, the dialog's question when it says `dialog`;
    "" otherwise."""
    lines = _idle_lines(session_id, shell)
    if len(lines) < 3 or lines[1].strip() not in ("asks", "unsure", "dialog"):
        return ""
    return lines[2].strip()


def idle_dialog(session_id, shell=0):
    """Whether this terminal's marker says a dialog stops its turn."""
    return _idle_word(session_id, shell) == "dialog"


def idle_error(session_id, shell=0):
    """The error type of the API error that ended this terminal's turn, `error` when its
    payload named none, or "" when the turn did not end in one."""
    lines = _idle_lines(session_id, shell)
    if len(lines) < 2 or lines[1].strip() != "error":
        return ""
    return (lines[2].strip() if len(lines) > 2 else "") or "error"


# -- the scheduled jobs --------------------------------------------------------

def _jobs_path(session_id, shell=0):
    return _dir() / ("%s.cron" % key(session_id, shell))


def _read_jobs(p):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write_jobs(p, jobs):
    if jobs:
        _dir().mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(jobs, indent=1), encoding="utf-8")
    else:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


_JOB_WORD = re.compile(r"\b(?:task|job)\s+([0-9A-Za-z_-]{4,})")


def job_id(response):
    """The id `CronCreate` returned: a field of a structured response, else the word after
    `task` or `job` in its text ("Scheduled recurring task e4810603 ..."). "" when none."""
    if isinstance(response, dict):
        for k in ("id", "jobId", "job_id", "taskId", "task_id"):
            if response.get(k):
                return str(response[k])
    if not isinstance(response, str):
        response = json.dumps(response)
    m = _JOB_WORD.search(response)
    return m.group(1) if m else ""


def add_job(session_id, shell, job, now=None):
    """Record a recurring job this terminal's session scheduled."""
    p = _jobs_path(session_id, shell)
    jobs = _read_jobs(p)
    jobs[job] = time.time() if now is None else now
    _write_jobs(p, jobs)


def drop_job(session_id, shell, job):
    """Take a cancelled job out of this terminal's record, and out of an unknown terminal's.
    A job whose id the hook could not read (`?` first) goes with any cancel."""
    for p in (_jobs_path(session_id, shell), _jobs_path(session_id, 0)):
        jobs = _read_jobs(p)
        left = {j: t for j, t in jobs.items() if j != job and not j.startswith("?")}
        if left != jobs:
            _write_jobs(p, left)


def clear_jobs(session_id, shell=0):
    """Forget every job of this terminal's session: its Claude process is new."""
    for p in (_jobs_path(session_id, shell), _jobs_path(session_id, 0)):
        _write_jobs(p, {})


def jobs(session_id, shell=0, now=None):
    """The ids of the recurring jobs this terminal's session holds, oldest first: its own
    record, then an unknown terminal's. A job older than `JOB_SECONDS` has expired."""
    cut = (time.time() if now is None else now) - JOB_SECONDS
    found = {}
    for p in (_jobs_path(session_id, shell), _jobs_path(session_id, 0)):
        for j, t in _read_jobs(p).items():
            if isinstance(t, (int, float)) and t >= cut:
                found.setdefault(j, t)
    return [j for j, _t in sorted(found.items(), key=lambda kv: kv[1])]


def _scan_path(session_id):
    """The state of the scan of a session's transcript for its jobs: how far it read, and
    what it found up to there."""
    return _dir() / ("%s.cronscan" % session_id.lower())


def _stamp(text):
    """Seconds since the epoch of a transcript row's ISO `timestamp`, 0 when unreadable."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def _result_text(content):
    """The text of a `tool_result` block's content: a string, or a list of text blocks."""
    if isinstance(content, list):
        return " ".join(str(x.get("text") or "") for x in content if isinstance(x, dict))
    return str(content or "")


def _scan_row(d, st):
    """Fold one transcript row into the scan state `st`."""
    ts = _stamp(d.get("timestamp"))
    if d.get("type") == "system" and d.get("subtype") == "scheduled_task_fire":
        if d.get("taskId"):
            st["fired"][str(d["taskId"])] = ts
        return
    if d.get("type") == "system" and d.get("subtype") == "away_summary":
        text = re.sub(r"\s*\(disable recaps in /config\)\s*$", "",
                      str(d.get("content") or "")).strip()
        if text:
            st["recap"] = {"text": text, "when": str(d.get("timestamp") or "")}
        return
    content = (d.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for b in content:
        if not isinstance(b, dict):
            continue
        tin = b.get("input") if isinstance(b.get("input"), dict) else {}
        if b.get("type") == "tool_use" and b.get("name") == CRON_CREATE:
            st["pending"][str(b.get("id"))] = [ts, tin.get("recurring", True) is not False]
        elif b.get("type") == "tool_use" and b.get("name") == CRON_DELETE:
            st["gone"][str(tin.get("id") or "")] = ts
        elif b.get("type") == "tool_result" and str(b.get("tool_use_id")) in st["pending"]:
            made, recurring = st["pending"].pop(str(b["tool_use_id"]))
            j = "" if b.get("is_error") else job_id(_result_text(b.get("content")))
            if j:
                st["made" if recurring else "once"][j] = made


_SCAN_KEYS = ("pending", "made", "once", "fired", "gone", "recap")


def scan_transcript(session_id, path):
    """What the session's transcript says of its jobs and its last recap, as the scan state:
    `made`, `once`, `fired` and `gone` map a job id to a time, `recap` holds the `text` and
    `when` of the last `away_summary` row. `None` when there is no transcript. The scan goes
    on from where the last one stopped (`<session>.cronscan`), so a long transcript is read
    once."""
    try:
        st = json.loads(_scan_path(session_id).read_text(encoding="utf-8"))
        if not isinstance(st, dict) or not all(isinstance(st.get(k), dict)
                                               for k in _SCAN_KEYS):
            raise ValueError
    except (OSError, ValueError):
        st = {}
    try:
        size = path.stat().st_size
    except (OSError, AttributeError):
        return None
    if not st or not isinstance(st.get("offset"), int) or st["offset"] > size:
        st = {"offset": 0}
        st.update({k: {} for k in _SCAN_KEYS})
    if size > st["offset"]:
        try:
            with open(path, "rb") as f:
                f.seek(st["offset"])
                raw = f.read(size - st["offset"])
        except OSError:
            raw = b""
        end = raw.rfind(b"\n") + 1
        for ln in raw[:end].split(b"\n"):
            # Only a `CronCreate` waiting for its result reads a row that names no job tool.
            if b"Cron" not in ln and b"scheduled_task_fire" not in ln \
                    and b"away_summary" not in ln \
                    and not (st["pending"] and b"tool_result" in ln):
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if isinstance(d, dict):
                _scan_row(d, st)
        if end:
            st["offset"] += end
            try:
                _dir().mkdir(parents=True, exist_ok=True)
                _scan_path(session_id).write_text(json.dumps(st), encoding="utf-8")
            except OSError:
                pass
    return st


def transcript_jobs(session_id, path, now=None):
    """The ids of the recurring jobs the session's transcript shows alive, oldest first:
    made by `CronCreate` and not cancelled by `CronDelete`, and made within `JOB_SECONDS`
    or fired within `FIRE_SECONDS`. A job that fires and was never seen made counts too."""
    st = scan_transcript(session_id, path)
    if st is None:
        return []
    now = time.time() if now is None else now
    alive = {}
    for j in set(st["made"]) | set(st["fired"]):
        if j in st["gone"] or j in st["once"]:
            continue
        made, fired = st["made"].get(j, 0), st["fired"].get(j, 0)
        if made >= now - JOB_SECONDS or fired >= now - FIRE_SECONDS:
            alive[j] = made or fired
    return [j for j, _t in sorted(alive.items(), key=lambda kv: kv[1])]


def all_jobs(session_id, shell=0, transcript=None, now=None):
    """Every recurring job the session holds by either record: the hook's, then the
    transcript's."""
    found = list(jobs(session_id, shell, now))
    if transcript is not None:
        try:
            found += [j for j in transcript_jobs(session_id, transcript, now)
                      if j not in found]
        except Exception:                               # noqa: BLE001 - the hook's record stands
            pass
    return found


# -- the background tasks ------------------------------------------------------

# The statuses of a background task that has stopped. Any other status still runs.
TASK_DONE = ("completed", "failed", "killed", "stopped", "cancelled", "canceled", "error")


def _tasks_path(session_id, shell=0):
    return _dir() / ("%s.tasks" % key(session_id, shell))


def running_tasks(tasks):
    """The tasks of the `background_tasks` field of a `Stop` payload that still run, each as
    `{id, type, description}`. Anything that is not a list of objects gives none."""
    out = []
    for x in tasks if isinstance(tasks, list) else []:
        if not isinstance(x, dict):
            continue
        if str(x.get("status") or "running").strip().lower() in TASK_DONE:
            continue
        out.append({"id": str(x.get("id") or ""), "type": str(x.get("type") or ""),
                    "description": str(x.get("description") or "")})
    return out


def mark_tasks(session_id, shell, tasks):
    """Record the background tasks that run while this terminal's session is at its prompt:
    the subagents and the background shells. No task removes the record."""
    p = _tasks_path(session_id, shell)
    if not tasks:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return
    _dir().mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")


def background_tasks(session_id, shell=0):
    """The background tasks this terminal's session waits for: its own record, else an
    unknown terminal's. Empty when it waits for none."""
    try:
        tasks = json.loads(_first_text((_tasks_path(session_id, shell),
                                        _tasks_path(session_id, 0))) or "[]")
    except ValueError:
        return []
    return [t for t in tasks if isinstance(t, dict)] if isinstance(tasks, list) else []


def idle_finished(session_id, shell=0):
    """What this terminal's last message said to mark it finished, the finished line or the
    statement that its ticket is in the verify or closed lane, or "". A marker of an older
    hook names nothing, and reads as `FINISHED_LINE`."""
    lines = _idle_lines(session_id, shell)
    if len(lines) < 2 or lines[1].strip() != "finished":
        return ""
    return (lines[2].strip() if len(lines) > 2 else "") or FINISHED_LINE


# -- the judge -----------------------------------------------------------------

# Set in the judge's own environment: every hook under its headless call returns at once.
JUDGE_ENV = "OCTOPUS_JUDGE"
JUDGE_STATES = ("input", "finished", "working", "unsure")
JUDGE_SECONDS = 90
# How often the judge is asked when its answer proves nothing, and what it is told then.
JUDGE_TRIES = 2
JUDGE_AGAIN = """
Your previous answer was rejected: %(fault)s. Answer again, with ONE JSON object.
"""
# The tail of the message the judge reads.
JUDGE_CHARS = 4000

JUDGE_PROMPT = """You judge one Claude Code session of the project %(project)s. %(lead)s \
Decide what it waits for.

%(ticket)s
%(what)s, between the markers:
<<<MESSAGE
%(message)s
MESSAGE>>>
%(recap)s
Answer with ONE JSON object and nothing else:
{"state": "input" | "finished" | "working" | "unsure", "why": "<see below>", \
"quote": "<see below>"}

input = the session ITSELF asks the owner a question in this message, or says that IT \
cannot go on until the owner answers, decides, approves or gives an order. Nothing else is \
input.
Never input, whatever words it uses:
- a report: a summary, a status table, a list of tickets, sessions or results;
- questions, plan approvals or decisions that OTHER tickets or OTHER sessions wait for, which \
this message only lists or relays, under headings such as "Waiting for you" or "Blocked by \
you": the owner answers them in those sessions, not here. A line of a list that names \
another ticket and its question ("T0035 ... - asks: ...") is such a relayed question;
- an offer the session does not wait for, such as "I can also do X".
finished = it did the work of this turn, asks the owner nothing itself, and runs no \
recurring task.
working = it still waits for work that is not the owner's: its subagent, a build, a \
background job, a session it launched, another ticket or a child ticket to land, a scheduled \
check. A session whose message or recap says it runs a recurring or scheduled task (a cron \
job, a watcher, a check or report "every 20 minutes") is working, never finished.
unsure = the text does not show what it waits for: none of the three above can be proven \
from it.

why = one sentence of at most 20 words that explains the verdict: for input, what the \
session needs from the owner; for finished, what work it did; for working, what it waits \
for; for unsure, what the text leaves open.
The why names no cause, error or limit that the text does not name.
quote = the proof, at most 30 words copied exactly from the text between the MESSAGE \
markers, the text between the RECAP markers, or the list of what it waits for below; never \
from these instructions. For input, the question the session asks the owner; for finished, \
the line that says the work was done; for working, the line that names what it waits for or \
the recurring task it runs. A verdict without such a quote is wrong; only unsure needs \
none.
%(others)s"""

# The first sentence of the prompt: what the session did.
JUDGE_LEAD = ("The session ended its turn with the message below and now sits at its "
              "prompt.")
# ... for a turn that has not ended and has written nothing for a while.
JUDGE_LEAD_STALE = ("The session has not ended its turn, and it has written nothing for "
                    "%(minutes)d min; its last action: %(doing)s.")
# ... for the recap the board asked for, when the last message did not show it.
JUDGE_LEAD_RECAP = ("The session ended its turn and sits at its prompt. Its last message "
                    "did not show what it waits for, so it was asked for its recap: the "
                    "text below is that recap.")
JUDGE_WHAT = "The last message of the session"
JUDGE_WHAT_RECAP = "The recap of the session"

# The part of the prompt that carries the last message, when the recap is judged.
JUDGE_LAST = """
The last message of the session, between the markers:
<<<RECAP
%(recap)s
RECAP>>>
"""

# The part of the prompt that carries Claude Code's own recap of the session.
JUDGE_RECAP = """
Claude Code's own recap of the session, written %(when)s, between the markers:
<<<RECAP
%(recap)s
RECAP>>>
"""


# The part of the prompt for a session that waits for other agents: what it waits for, and
# how a question of theirs reads.
JUDGE_OTHERS = """
The session waits for other agents:
%(waits)s
A question that one of those tickets or its session asks the owner, and that this message \
only reports or relays, is not input: the owner answers it in that other session, and this \
session waits for that session, so it is working. It is input only when this session itself \
asks the owner, or says it needs the owner's own answer before it can go on.
"""


def _judge_path(session_id, shell=0):
    return _dir() / ("%s.judge" % key(session_id, shell))


def _last_path(session_id, shell=0):
    return _dir() / ("%s.last" % key(session_id, shell))


def _judging_path(session_id, shell=0):
    """Line 1: the turn stamp of the message a judge was started on. One start per turn."""
    return _dir() / ("%s.judging" % key(session_id, shell))


def _marker_time(session_id, shell=0):
    """Line 1 of this terminal's marker, else of an unknown terminal's, as written; "" when
    there is none."""
    lines = _idle_lines(session_id, shell)
    return lines[0].strip() if lines else ""


def needs_judge(text, ticket=""):
    """Whether no rule reads this message: it is not empty, and it carries no finished line,
    no statement that its ticket `ticket` is done, and no question on its last line."""
    return bool((text or "").strip()) and not (
        says_finished(text) or says_done(text, ticket) or asks_last(text))


def start_judge(session_id, shell, text, root, recap=False):
    """Keep the message in `<key>.last` and start the judge on it, detached. Returns at
    once: a `Stop` hook must not hold the prompt back. With `recap`, `text` is the recap the
    board asked for: it goes into the recap record, and the judge reads it there."""
    import subprocess
    _dir().mkdir(parents=True, exist_ok=True)
    if recap:
        rec = recap_record(session_id, shell)
        rec.update(text=text, judging=time.time())
        _recap_path(session_id, shell).write_text(json.dumps(rec), encoding="utf-8")
    else:
        _last_path(session_id, shell).write_text(text, encoding="utf-8")
        _judging_path(session_id, shell).write_text(turn_stamp(session_id, shell) + "\n",
                                                    encoding="utf-8")
    argv = [sys.executable, str(Path(__file__).resolve()), "--judge", session_id,
            str(int(shell or 0)), "--project", str(root)] + (["--recap"] if recap else [])
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    if os.name != "nt":
        # A session of its own, so the end of the hook or of its terminal does not end it.
        return subprocess.Popen(argv, start_new_session=True, **kw)
    # Out of the hook's job, when there is one, so the end of the hook does not end the
    # judge. A job that forbids it refuses the start: start it inside the job then.
    try:
        return subprocess.Popen(argv, creationflags=flags
                                | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), **kw)
    except OSError:
        return subprocess.Popen(argv, creationflags=flags, **kw)


def _ticket_of(session_id):
    """The text that names the session's ticket for the judge, or "" when it has none."""
    import todo
    sid = session_id.lower()
    for t in todo.load_all():
        modes = {todo.launch_session_id(t.id, m, t.run if t.repeat else 1): m
                 for m in todo.MODES}
        modes.update({e["session"]: e.get("mode") or "" for e in t.sessions})
        if sid in modes:
            import project
            p = project.current()
            return ("The session was launched as `%s` on ticket %s: %s\nThe ticket is now in "
                    "%s, status %s. A `work` session ends with its ticket in %s.\n" % (
                        modes[sid] or "?", t.id, t.title, p.STATE_LABEL.get(t.state, t.state),
                        t.status, p.label("verify")))
    return ""


def _others_of(session_id, shell=0):
    """The lines that name the other agents the session waits for, for the judge: the
    tickets its ticket waits for with their lanes, its background work, its jobs. "" when it
    waits for none."""
    import todo
    import project
    p = project.current()
    lines = []
    sid = session_id.lower()
    tickets = todo.load_all()
    by_id = {t.id: t for t in tickets}
    for t in tickets:
        sids = {todo.launch_session_id(t.id, m, t.run if t.repeat else 1) for m in todo.MODES}
        sids.update(e["session"] for e in t.sessions)
        if sid not in sids:
            continue
        for w in todo.waits_for(t, tickets):
            o = by_id.get(w)
            lines.append("- ticket %s%s" % (w, ": %s, now in %s" % (
                o.title, p.STATE_LABEL.get(o.state, o.state)) if o else ""))
        break
    for task in background_tasks(session_id, shell):
        lines.append("- its own background work: %s" % (
            task.get("description") or task.get("type") or "a task"))
    for j in all_jobs(session_id, shell, todo.session_transcript(session_id)):
        lines.append("- its scheduled check, job %s" % j)
    return "\n".join(lines)


def _plain(text):
    """Text compared for a quote: lower case, no markdown marks, no quote marks, one space."""
    text = re.sub(r"[*_`#>\"'\u2018\u2019\u201c\u201d]", "", str(text or "").lower())
    return " ".join(text.split())


def quoted(quote, sources):
    """Whether every part of `quote` between `...` stands in one of the `sources`."""
    parts = [_plain(x).strip(" .,;:") for x in re.split(r"\.\.\.|\u2026", quote or "")]
    parts = [x for x in parts if x]
    texts = [_plain(x) for x in sources if x]
    return bool(parts) and any(all(x in t for x in parts) for t in texts)


# How long the explanation and the quote of a verdict may be on line 3 of `<key>.judge`.
WHY_CHARS = 400


_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_TICKET_ID = re.compile(r"\bT\d{4,}\b")
_RELAYS = re.compile(r"\s[-\u2013\u2014]\s*(?:asks?|question)\s*:", re.I)


def relayed(quote, message, own=()):
    """Whether the quote of an `input` verdict is another ticket's question that the message
    only relays: it names a ticket other than the session's `own`, and it reads `- asks:`
    or stands on a list item of the message."""
    others = set(_TICKET_ID.findall(quote or "")) - set(own)
    if not others:
        return False
    if _RELAYS.search(quote):
        return True
    head = _plain(re.split(r"\.\.\.|\u2026", quote)[0]).strip(" .,;:")
    return any(head and head in _plain(ln) and _LIST_ITEM.match(ln)
               for ln in (message or "").splitlines())


def check_verdict(answer, sources=None, own=()):
    """`((state, why), "")` from the judge's answer: the first `{...}` in it, read as JSON;
    `why` is its explanation and its quote. `(None, fault)` when the answer holds no
    verdict, and, with `sources` (the message first), when its quote stands in none of them
    or, for `input`, relays another ticket's question: a verdict must prove itself. `unsure`
    proves nothing and needs no quote."""
    m = re.search(r"\{.*\}", answer or "", re.S)
    if not m:
        return None, "it held no JSON object"
    try:
        d = json.loads(m.group(0))
    except ValueError:
        m = re.search(r"\{.*?\}", answer or "", re.S)
        try:
            d = json.loads(m.group(0))
        except ValueError:
            return None, "it held no JSON object"
    state = str(d.get("state") or "").strip().lower() if isinstance(d, dict) else ""
    if state not in JUDGE_STATES:
        return None, "its state was none of %s" % ", ".join(JUDGE_STATES)
    why = " ".join(str(d.get("why") or "").split())
    quote = " ".join(str(d.get("quote") or "").split())
    if sources is not None and not (state == "unsure" and not quote):
        if not quoted(quote, sources):
            if state == "unsure":
                quote = ""
            else:
                return None, ("its quote %r stands neither in the message, nor in the recap, "
                              "nor in the list of what it waits for" % quote)
        if state == "input" and relayed(quote, sources[0], own):
            return None, ("its quote %r is another ticket's question that the message only "
                          "relays, which is never input" % quote)
    if quote:
        why = '%s: "%s"' % (why.rstrip(" ."), quote) if why else '"%s"' % quote
    return (state, why[:WHY_CHARS]), ""


def parse_verdict(answer, sources=None, own=()):
    """`(state, why)` of `check_verdict`, `None` when the answer holds no proven verdict."""
    return check_verdict(answer, sources, own)[0]


def last_recap(session_id, path):
    """`(text, when)` of the last recap Claude Code wrote into the session's transcript (an
    `away_summary` row), `("", "")` when there is none."""
    st = scan_transcript(session_id, path) or {}
    recap = st.get("recap") or {}
    return str(recap.get("text") or ""), str(recap.get("when") or "")


def judge(session_id, shell=0, recap=False):
    """Judge the message the `Stop` hook kept for this terminal, or the last message of its
    stale turn, and write `<key>.judge` when the turn still carries the stamp it judged
    (`turn_stamp`). With `recap`, judge the recap the board asked for instead, and mark the
    verdict `recap` on line 4. Returns the state written, or ""."""
    shell = int(shell or 0)
    when = turn_stamp(session_id, shell)
    last = _first_text((_last_path(session_id, shell),))
    text = (recap_record(session_id, shell).get("text") or "") if recap else last
    if not when or not str(text).strip():
        return ""
    os.environ[JUDGE_ENV] = "1"
    import project
    import todo_server
    p = project.current()
    try:
        ticket = _ticket_of(session_id)
    except Exception:                                   # noqa: BLE001 - judge without it
        ticket = ""
    try:
        waits = _others_of(session_id, shell)
    except Exception:                                   # noqa: BLE001 - judge without it
        waits = ""
    stamp, doing = stale_turn(session_id, shell)
    if recap:
        lead, what = JUDGE_LEAD_RECAP, JUDGE_WHAT_RECAP
        recap, when_recap, block = last[-JUDGE_CHARS:], "", JUDGE_LAST
    else:
        try:
            import todo
            recap, when_recap = last_recap(session_id, todo.session_transcript(session_id))
        except Exception:                               # noqa: BLE001 - judge without it
            recap, when_recap = "", ""
        lead, what, block = JUDGE_LEAD, JUDGE_WHAT, JUDGE_RECAP
        if not _marker_time(session_id, shell) and stamp == when:
            try:
                minutes = int(max(0, time.time() - float(stamp)) // 60)
            except ValueError:
                minutes = 0
            lead = JUDGE_LEAD_STALE % {"minutes": minutes, "doing": doing or "unknown"}
    message = str(text)[-JUDGE_CHARS:]
    prompt = JUDGE_PROMPT % {
        "project": p.name, "lead": lead, "what": what, "ticket": ticket, "message": message,
        "recap": block % {"when": when_recap or "earlier", "recap": recap} if recap else "",
        "others": JUDGE_OTHERS % {"waits": waits} if waits else ""}
    own = _TICKET_ID.findall(ticket)
    verdict, fault = None, ""
    for _try in range(JUDGE_TRIES):
        ask = prompt + (JUDGE_AGAIN % {"fault": fault} if fault else "")
        try:
            answer = todo_server._ask_provider(ask, {
                "provider": p.provider,
                "model": p.models.get("haiku") if p.provider == "claude" else None,
            }, JUDGE_SECONDS)
        except ValueError:
            return ""
        verdict, fault = check_verdict(answer, (message, recap, waits), own)
        if verdict is not None:
            break
    if verdict is None or turn_stamp(session_id, shell) != when:
        return ""
    state, why = verdict
    _judge_path(session_id, shell).write_text("%s\n%s\n%s\n%s" % (
        when, state, why, "recap\n" if lead is JUDGE_LEAD_RECAP else ""), encoding="utf-8")
    # A launched session the judge reads as finished closes its terminal without the board,
    # unless the owner talks to it: then only its own `WORK IS FINISHED` closes it.
    if state == "finished" and not owner_talks(session_id, shell):
        try:
            start_closer(*_launch_of(session_id))
        except Exception:                               # noqa: BLE001 - the verdict stands
            pass
    return state


def judge_started(session_id, shell=0):
    """Whether a judge was already started on this terminal's current turn."""
    when = turn_stamp(session_id, shell)
    return bool(when) and _first_text((_judging_path(session_id, shell),)).strip() == when


# Seconds after its start that a judge which wrote no verdict counts as dead: its
# `claude -p` gives up after `JUDGE_SECONDS`.
JUDGE_STALE = JUDGE_TRIES * JUDGE_SECONDS + 30


def judge_started_at(session_id, shell=0):
    """When the judge of this terminal's current turn was started, 0 when none was."""
    if not judge_started(session_id, shell):
        return 0
    try:
        return _judging_path(session_id, shell).stat().st_mtime
    except OSError:
        return 0


def rejudge(session_id, shell=0, now=None):
    """Drop the start record of a judge that was started on this terminal's current turn
    and wrote no verdict `JUDGE_STALE` seconds later, so `ensure_judged` starts it again.
    Returns whether it was dropped. Only a re-evaluation calls it: the poll never retries.

    A `/recap` that brought no recap within `RECAP_WAIT` seconds, or whose judge wrote no
    verdict `JUDGE_STALE` seconds later, is dropped too, so the board asks for it again."""
    now = time.time() if now is None else now
    dropped = False
    rec = recap_record(session_id, shell)
    if rec and judge_source(session_id, shell) != "recap":
        try:
            sent, judging = float(rec.get("sent") or 0), float(rec.get("judging") or 0)
        except (TypeError, ValueError):
            sent = judging = 0
        if (judging and now - judging >= JUDGE_STALE) or (
                not judging and now - sent >= RECAP_WAIT):
            dropped = drop_recap(session_id, shell)
    started = judge_started_at(session_id, shell)
    if not started or judged(session_id, shell)[0]:
        return dropped
    if now - started < JUDGE_STALE:
        return dropped
    try:
        _judging_path(session_id, shell).unlink()
    except OSError:
        return dropped
    return True


# How much of the end of a transcript `transcript_tail` reads.
TAIL_BYTES = 512 * 1024


_BOOKKEEPING = ("<local-command", "<command-name>", "<command-message>",
                "[Request interrupted")


def _bookkeeping(d):
    """Whether a transcript row is no message of the turn: a meta row, the summary a
    compaction writes, the owner's slash command and its output, an interrupt."""
    if d.get("isMeta") or d.get("isCompactSummary"):
        return True
    content = (d.get("message") or {}).get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        content = content[0].get("text") if content[0].get("type") == "text" else ""
    return isinstance(content, str) and content.lstrip().startswith(_BOOKKEEPING)


def api_error(d):
    """The error type of a transcript row that is Claude Code's own API-error message: the
    synthetic assistant row it writes when a turn ends in a rate limit, an overload or a
    request the API refuses. `error` when the row names no type; "" for any other row. A
    limit that names its reset time adds it: `rate_limit, limit until 2026-10-07 17:00`. Its
    text is the error, not Claude's message: no rule and no judge reads it."""
    if d.get("type") != "assistant" or not d.get("isApiErrorMessage"):
        return ""
    word = str(d.get("error") or "").strip() or "error"
    info = ((d.get("apiErrorParams") or {}).get("rate_limit_info")
            or d.get("quotaLimits") or {})
    try:
        resets = float(info.get("resetsAt")) if isinstance(info, dict) else 0
    except (TypeError, ValueError):
        resets = 0
    if resets > 0:
        word += ", limit until %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(resets))
    return word


def transcript_tail(path, error=False):
    """`(text, ended, when)` from the end of a session's transcript: the text of its last
    assistant message, whether its last turn ended, and the file's time. With `error`, a
    fourth item: the `api_error` type of its last row, "" when the turn did not end in an API
    error. A turn has not
    ended only when its last message calls a tool that has no result yet: a permission
    waits. The owner's slash commands and an interrupt after it are bookkeeping.
    `(None, None, 0)` when there is no transcript or it holds no message: the session never
    began (`(None, None, 0, "")` with `error`).

    The `Stop` hook does not read the transcript, whose format is no contract. This is the
    fallback for a session whose hook files are gone."""
    try:
        size, when = path.stat().st_size, path.stat().st_mtime
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return (None, None, 0, "") if error else (None, None, 0)
    rows = []
    for ln in raw.splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") in ("assistant", "user") \
                and not _bookkeeping(d):
            rows.append(d)
    if not rows:
        return (None, None, 0, "") if error else (None, None, 0)
    last = rows[-1].get("message") or {}
    ended = not (rows[-1].get("type") == "assistant" and last.get("stop_reason") == "tool_use")
    texts = []
    for d in reversed(rows):
        if d.get("type") != "assistant":
            break
        content = (d.get("message") or {}).get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        texts[:0] = [str(x.get("text") or "") for x in parts
                     if isinstance(x, dict) and x.get("type") == "text"]
    text = "\n".join(t for t in texts if t.strip())
    return (text, ended, when, api_error(rows[-1])) if error else (text, ended, when)


# The longest "what it does now" line `transcript_activity` gives.
DOING_CHARS = 80
# The input fields of a tool call that say what it does, the first one present wins.
_DOING_FIELDS = ("description", "file_path", "command", "pattern", "url", "query", "prompt",
                 "path")


def _doing_of(block):
    """`<Tool>: <what>` of a `tool_use` block, or the last line of a `text` block, at most
    `DOING_CHARS` long; "" for anything else."""
    if block.get("type") == "tool_use":
        tin = block.get("input") if isinstance(block.get("input"), dict) else {}
        what = next((str(tin[k]) for k in _DOING_FIELDS if tin.get(k)), "")
        text = "%s: %s" % (block.get("name") or "a tool", " ".join(what.split())) if what \
            else str(block.get("name") or "a tool")
    elif block.get("type") == "text":
        lines = _lines(re.sub(r"[*_`#>]", "", str(block.get("text") or "")))
        text = " ".join(lines[-1].split()) if lines else ""
    else:
        return ""
    return text if len(text) <= DOING_CHARS else text[:DOING_CHARS - 3] + "..."


def transcript_activity(path):
    """What the end of a session's transcript says of its current turn, as a dict: `text`,
    the text of its last assistant message; `ended`, whether the turn ended: its last row is
    an assistant message that calls no tool and whose `stop_reason` is not `tool_use`, or the
    owner interrupted it; `when`, the file's time, the session's last update; `doing`, what it
    does now, the last tool call as `<Tool>: <what>` or else the last line it wrote; `since`,
    the time of the row `doing` comes from, else of the last row; `error`, the `api_error`
    type of its last row, "" when the turn did not end in an API error; `call` and
    `call_since`, the tool call of a turn that runs which has no result yet and its start
    (`_running_call`). `None` when there is no transcript or it holds no message.

    A tool result, a prompt and a tool call waiting for its result are a turn that runs. The
    owner's slash commands, their output and the summary of a compaction are bookkeeping."""
    try:
        size, when = path.stat().st_size, path.stat().st_mtime
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read().decode("utf-8", "replace")
    except (OSError, AttributeError):
        return None
    rows, interrupted = [], False
    for ln in raw.splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("type") not in ("assistant", "user"):
            continue
        if _bookkeeping(d):
            content = (d.get("message") or {}).get("content")
            first = content[0].get("text") if isinstance(content, list) and content \
                and isinstance(content[0], dict) else content
            interrupted = interrupted or str(first or "").lstrip().startswith(
                "[Request interrupted")
            continue
        rows.append(d)
        interrupted = False
    if not rows:
        return None
    last = rows[-1]
    msg = last.get("message") or {}
    blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
    calls = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in blocks)
    ended = interrupted or (last.get("type") == "assistant" and not calls
                            and msg.get("stop_reason") != "tool_use")
    texts = []
    for d in reversed(rows):
        if d.get("type") != "assistant":
            break
        content = (d.get("message") or {}).get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        texts[:0] = [str(x.get("text") or "") for x in parts
                     if isinstance(x, dict) and x.get("type") == "text"]
    doing, since = "", _stamp(last.get("timestamp"))
    for d in reversed(rows):
        if d.get("type") != "assistant":
            continue
        content = (d.get("message") or {}).get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for b in reversed([x for x in parts if isinstance(x, dict)]):
            doing = _doing_of(b)
            if doing:
                break
        if doing:
            since = _stamp(d.get("timestamp")) or since
            break
    call, call_since = _running_call(rows) if not ended else ("", 0)
    if not any(t.strip() for t in texts):
        # A turn that runs: the last thing it wrote in it, above the tool calls since.
        for d in reversed(rows):
            content = (d.get("message") or {}).get("content")
            if d.get("type") == "user" and not (isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content)):
                break
            if d.get("type") == "assistant" and isinstance(content, list):
                found = [str(x.get("text") or "") for x in content
                         if isinstance(x, dict) and x.get("type") == "text"]
                if any(t.strip() for t in found):
                    texts = found
                    break
    return {"text": "\n".join(t for t in texts if t.strip()), "ended": ended, "when": when,
            "doing": doing, "since": since, "error": api_error(last),
            "call": call, "call_since": call_since}


def last_answer(path):
    """The time of the newest transcript row that holds the tool result of an
    `AskUserQuestion` call: the owner answered that question dialog. 0 when no row does. It
    reads the whole transcript, since a long turn puts the answer before the tail that
    `transcript_activity` reads, and it parses only the rows that can hold a dialog."""
    asks, when = set(), 0
    try:
        with open(path, "rb") as f:
            for ln in f:
                if b'"tool_use_id"' not in ln and ASK_TOOL.encode() not in ln:
                    continue
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                msg = d.get("message") if isinstance(d, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                blocks = content if isinstance(content, list) else []
                blocks = [b for b in blocks if isinstance(b, dict)]
                if d.get("type") == "assistant":
                    asks.update(b.get("id") for b in blocks
                                if b.get("type") == "tool_use" and b.get("name") == ASK_TOOL)
                elif any(b.get("type") == "tool_result" and b.get("tool_use_id") in asks
                         for b in blocks):
                    when = _stamp(d.get("timestamp")) or when
    except OSError:
        return 0
    return when


def _running_call(rows):
    """`(<Tool>: <what>, start)` of the newest tool call of the current turn that has no
    result yet: a foreground call that still runs. `("", 0)` when every call of the turn has
    its result. A dialog tool waits for the owner, not for itself, so it is no running call."""
    results, calls = set(), []
    for d in reversed(rows):
        content = (d.get("message") or {}).get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if d.get("type") == "user":
            ids = [b.get("tool_use_id") for b in blocks if b.get("type") == "tool_result"]
            if not ids:
                break
            results.update(ids)
            continue
        calls[:0] = [(b, d) for b in blocks if b.get("type") == "tool_use"]
    for b, d in reversed(calls):
        if b.get("id") not in results and b.get("name") not in DIALOG_TOOLS:
            return _doing_of(b), _stamp(d.get("timestamp"))
    return "", 0


def recover(session_id, shell, text, when, error=""):
    """Write the marker that the `Stop` hook would have written for a turn whose hook files
    are gone, from its transcript, or with `error` the one `StopFailure` would have written.
    Returns whether the judge has to read it."""
    if error:
        mark_idle(session_id, shell, error=error, when=when)
        return False
    return mark_stop(session_id, shell, text or "", when)


def mark_stale(session_id, shell, text, when, error="", ticket=None):
    """Write the marker of a turn that ended by its transcript while no `Stop` marker
    exists: the hook missed it, or the owner's slash command (the board's `/recap`) took the
    prompt and removed it. The same words the `Stop` hook writes, at the time of the turn's
    last row, so the judge reads it as it reads any ended turn, and the message the judge
    reads is this one, not the one a stale turn kept. A turn that ended in an API error
    (`error`) gets the marker `StopFailure` would have written, and no judge: a verdict that
    a judge gave on the error text is removed. `ticket` is as `mark_stop` takes it. Returns
    whether the judge has to read it."""
    if error:
        mark_idle(session_id, shell, error=error, when=when)
        for p in (_judge_path(session_id, shell), _judge_path(session_id, 0),
                  _last_path(session_id, shell), _last_path(session_id, 0),
                  _judging_path(session_id, shell), _judging_path(session_id, 0)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        return False
    judge_it = mark_stop(session_id, shell, text or "", when, ticket)
    _last_path(session_id, shell).write_text(text or "", encoding="utf-8")
    return judge_it


# -- a turn that runs and writes nothing ---------------------------------------

def _stale_path(session_id, shell=0):
    """Line 1: the transcript's time when the turn was found stale, the stamp the judge's
    verdict on it carries; line 2: its last action."""
    return _dir() / ("%s.stale" % key(session_id, shell))


def stale_turn(session_id, shell=0):
    """`(stamp, doing)` of this terminal's stale turn, `("", "")` when none was recorded."""
    lines = _first_text((_stale_path(session_id, shell),)).splitlines()
    return (lines[0].strip() if lines else "", lines[1].strip() if len(lines) > 1 else "")


def turn_stamp(session_id, shell=0):
    """The stamp of the turn the judge reads: the `Stop` marker's time, else the stamp of
    the stale turn. "" when there is neither."""
    return _marker_time(session_id, shell) or stale_turn(session_id, shell)[0]


def ensure_judged_stale(session_id, shell, root, activity):
    """Start the judge on a turn that has not ended and has written nothing for
    `STALE_SECONDS`: record the turn's stamp (the transcript's time) and its last action,
    then judge its last message once for that stamp. A transcript written again after it is
    a new stamp, and a new judge. Returns whether a judge was started."""
    if _marker_time(session_id, shell) or not activity:
        return False
    stamp = "%.3f" % activity["when"]
    if stale_turn(session_id, shell)[0] != stamp:
        _dir().mkdir(parents=True, exist_ok=True)
        _stale_path(session_id, shell).write_text(
            "%s\n%s\n" % (stamp, " ".join((activity.get("doing") or "").split())),
            encoding="utf-8")
    if judged(session_id, shell)[0] or judge_started(session_id, shell):
        return False
    text = activity.get("text") or ""
    # The judge reads a message, so a turn that wrote none has nothing for it.
    if not text.strip():
        return False
    start_judge(session_id, shell, text, root)
    return True


# -- the recap -------------------------------------------------------------------

# The `commandRun` of the transcript row the `/recap` command writes its output into.
RECAP_COMMAND = "recap"
# Seconds the board waits for the recap after it typed `/recap`. The command answers in
# a few seconds.
RECAP_WAIT = 120


def _recap_path(session_id, shell=0):
    """JSON: `turn`, the turn the board asked `/recap` for; `sent`, when; `judging`, when the
    judge on the recap was started; `text`, the recap it reads."""
    return _dir() / ("%s.recap" % key(session_id, shell))


def recap_record(session_id, shell=0):
    """The record of the `/recap` the board sent this terminal, `{}` when none."""
    try:
        d = json.loads(_first_text((_recap_path(session_id, shell),)) or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def mark_recap_sent(session_id, shell, turn, now=None):
    """Record that the board typed `/recap` into this terminal for this turn: one per turn."""
    _dir().mkdir(parents=True, exist_ok=True)
    _recap_path(session_id, shell).write_text(json.dumps(
        {"turn": turn, "sent": time.time() if now is None else now}), encoding="utf-8")


def recap_after(session_id, path, t):
    """The recap the session's transcript holds written at `t` or later: the output row of
    the `/recap` command, or Claude Code's own `away_summary` row. "" when none came."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read().decode("utf-8", "replace")
    except (OSError, AttributeError):
        return ""
    found = ""
    for ln in raw.splitlines():
        if "local_command" not in ln and "away_summary" not in ln:
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("type") != "system" \
                or _stamp(d.get("timestamp")) < t - 1:
            continue
        run = d.get("commandRun") if isinstance(d.get("commandRun"), dict) else {}
        if d.get("subtype") == "away_summary" or (
                d.get("subtype") == "local_command" and run.get("command") == RECAP_COMMAND):
            text = re.sub(r"</?local-command-std(?:out|err)>", "", str(d.get("content") or ""))
            text = re.sub(r"\s*\(disable recaps in /config\)\s*$", "", text).strip()
            found = text or found
    return found


def drop_recap(session_id, shell=0):
    """Forget the `/recap` of this terminal, so the board asks for it again."""
    try:
        _recap_path(session_id, shell).unlink()
        return True
    except OSError:
        return False


def ensure_judged(session_id, shell, root, transcript=None, ticket=None):
    """Start the judge on this terminal's last message when no rule read it, no verdict
    exists, and no judge was started on this turn. The message is the one the `Stop` hook
    kept, else the transcript's. `ticket` is the id of the session's ticket, "" for none;
    `None` looks it up. Returns whether a judge was started.

    A question on the last line is the owner's to answer, whatever the session waits for:
    no judge reads it. A marker that an older hook wrote from a statement about another
    ticket read nothing of the session's own work."""
    if ticket is None:
        ticket = own_ticket(session_id)
    word = _idle_word(session_id, shell)
    if word in ("dialog", "error", "asks") or (
            word == "finished" and about_ticket(idle_finished(session_id, shell), ticket)):
        return False
    if judged(session_id, shell)[0] or judge_started(session_id, shell):
        return False
    if not _marker_time(session_id, shell):
        return False
    text = _first_text((_last_path(session_id, shell), _last_path(session_id, 0)))
    if not text.strip() and transcript is not None:
        text = transcript_tail(transcript)[0] or ""
    if not needs_judge(text, ticket):
        return False
    start_judge(session_id, shell, text, root)
    return True


def _verdict_lines(session_id, shell=0):
    """The lines of the judge's verdict on this terminal's current turn, its own record
    first, then an unknown terminal's; `[]` when there is none, or the turn's stamp changed
    after the message the judge read."""
    for sh in (shell, 0):
        lines = _first_text((_judge_path(session_id, sh),)).splitlines()
        if len(lines) >= 2 and lines[0].strip() == turn_stamp(session_id, sh) \
                and lines[1].strip() in JUDGE_STATES:
            return lines
    return []


def judged(session_id, shell=0):
    """`(state, why)` of the judge's verdict on this terminal's current turn: its last
    message, the last message of its stale turn, or its recap; `("", "")` when there is
    none, or the marker was written again after the message the judge read."""
    lines = _verdict_lines(session_id, shell)
    if not lines:
        return "", ""
    return lines[1].strip(), lines[2].strip() if len(lines) > 2 else ""


def judge_source(session_id, shell=0):
    """`recap` when the judge's verdict on the current turn read the recap the board asked
    for, else ""."""
    lines = _verdict_lines(session_id, shell)
    return "recap" if len(lines) > 3 and lines[3].strip() == "recap" else ""


def _when(p):
    """Line 1 of a marker: the time it was written."""
    try:
        return float((p.read_text(encoding="utf-8").splitlines() or [""])[0].strip())
    except (OSError, ValueError):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0


def idle_sessions():
    """Every terminal now at its prompt, keyed by `key(session_id, shell)`."""
    if not _dir().is_dir():
        return {}
    return {p.stem: _when(p) for p in _dir().glob("*.idle")}


def idle_since(markers, session_id, shell=0):
    """The time this terminal reached its prompt, or `None` when it works."""
    return markers.get(key(session_id, shell)) or markers.get(key(session_id, 0))


def prune(keep_seconds=KEEP_SECONDS):
    """Delete the markers of sessions that stopped long ago, and the live records of
    sessions whose Claude and shell are both gone."""
    if not _dir().is_dir():
        return 0
    cut, gone = time.time() - keep_seconds, 0
    procs = _procs()
    # A session whose Claude or shell still runs keeps its files however old they are: a
    # session at its prompt for a week still waits for what its marker says.
    alive = set()
    for p in _dir().glob("*.live.json"):
        rec = _read_record(p)
        if rec is not None and _record_alive(rec, procs):
            alive.add(rec["session"])
    for p in (list(_dir().glob("*.idle")) + list(_dir().glob("*.report"))
              + list(_dir().glob("*.cron")) + list(_dir().glob("*.tasks"))
              + list(_dir().glob("*.judge")) + list(_dir().glob("*.last"))
              + list(_dir().glob("*.judging")) + list(_dir().glob("*.cronscan"))
              + list(_dir().glob("*.stale")) + list(_dir().glob("*.recap"))
              + list(_dir().glob("*.home"))):
        if p.name.split(".")[0] in alive:
            continue
        try:
            if p.stat().st_mtime < cut:
                p.unlink()
                gone += 1
        except OSError:
            pass
    for p in _dir().glob("*.live.json"):
        rec = _read_record(p)
        if rec is None or not _record_alive(rec, procs):
            try:
                p.unlink()
                gone += 1
            except OSError:
                pass
    return gone


# -- the live record ----------------------------------------------------------

def _record_path(session_id):
    return _dir() / ("%s.live.json" % session_id.lower())


def _read_record(p):
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("session") else None


def _alive(pid, started, procs):
    """Whether `pid` is the process that was recorded: alive, and started at the same
    time. A reused pid answers False."""
    if not pid or pid not in procs:
        return False
    if not started:
        return True
    try:
        import winproc
        return winproc.created_at(pid) == started
    except Exception:                                   # noqa: BLE001
        return True


def _own_shell(rec):
    """The record's shell when it is the shell Claude runs in, else 0. A record from an
    older hook, or of a Claude a program started, names no shell of its own."""
    shell = rec.get("shell") or 0
    return shell if shell and shell == rec.get("claude_parent") else 0


def _record_alive(rec, procs):
    return (_alive(rec.get("claude"), rec.get("claude_started"), procs)
            or _alive(_own_shell(rec), rec.get("shell_started"), procs))


def read_live(session_id):
    """The session's live record in the current project, or None."""
    p = _record_path(session_id)
    return _read_record(p) if p.exists() else None


def write_live(session_id, rec):
    """Write `rec` as the session's live record in the current project."""
    _dir().mkdir(parents=True, exist_ok=True)
    _record_path(session_id).write_text(json.dumps(rec, indent=1), encoding="utf-8")


def drop_live(session_id):
    """Delete the session's live record in the current project."""
    try:
        _record_path(session_id).unlink()
    except OSError:
        pass


# A session that became another project's (`todo.py manager --claim` from a project that is not
# the manager's home) keeps `CLAUDE_PROJECT_DIR` of the project it began in. The redirect marker
# in that project names the root its hooks write in from then on.

def _redirect_path(session_id):
    return _dir() / ("%s.home" % session_id.lower())


def redirect(session_id, root):
    """Send the hooks of the session to the project at `root` from now on."""
    _dir().mkdir(parents=True, exist_ok=True)
    _redirect_path(session_id).write_text(str(root), encoding="utf-8")


def redirected_to(session_id):
    """The project root the session's hooks write in, when the current project sent them
    elsewhere, else None."""
    import project
    if not session_id:
        return None
    try:
        root = _redirect_path(session_id).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(root) if root and project.has_config(root) else None


def record_live(session_id, event, title, claude=0, shell=0, procs=None, cwd=""):
    """Write the session's live record: which Claude, shell and terminal it runs in."""
    procs = _procs() if procs is None else procs
    try:
        import winproc
        created = winproc.created_at
    except Exception:                                   # noqa: BLE001
        def created(_pid):
            return None
    _dir().mkdir(parents=True, exist_ok=True)
    path = _record_path(session_id)
    old = _read_record(path) if path.exists() else None
    now = time.time()
    terminal, terminal_image = terminal_of(shell or claude, procs)
    rec = {
        "session": session_id.lower(),
        "claude": int(claude or 0),
        "claude_started": created(claude) if claude else None,
        # The parent of Claude when the hook ran. `shell` counts only when it is this
        # process: a record without the field comes from an older hook that took any shell
        # above Claude.
        "claude_parent": procs.get(claude, (0, ""))[0] if claude else 0,
        "shell": int(shell or 0),
        "shell_image": procs.get(shell, (0, ""))[1] if shell else "",
        "shell_started": created(shell) if shell else None,
        "terminal": int(terminal or 0),
        "terminal_image": terminal_image,
        "title": title,
        "cwd": cwd or (old or {}).get("cwd", ""),
        "first_seen": (old or {}).get("first_seen") or now,
        "last_event": event,
        "last_seen": now,
    }
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def live_records(procs=None):
    """Every recorded session whose Claude or shell still runs, keyed by session id. A
    record whose Claude and shell are both gone is deleted on the way."""
    if not _dir().is_dir():
        return {}
    procs = _procs() if procs is None else procs
    out = {}
    for p in _dir().glob("*.live.json"):
        rec = _read_record(p)
        if rec is None:
            continue
        rec["claude_alive"] = _alive(rec.get("claude"), rec.get("claude_started"), procs)
        rec["shell_alive"] = _alive(_own_shell(rec), rec.get("shell_started"), procs)
        if rec["claude_alive"] or rec["shell_alive"]:
            out[rec["session"]] = rec
        else:
            try:
                p.unlink()
            except OSError:
                pass
    return out


# Every Claude window of a project carries the project's title as its prefix, whoever
# started the session. Claude replaces a terminal title with its own topic unless
# CLAUDE_CODE_DISABLE_TERMINAL_TITLE is set, and install sets it in the project's
# `.claude/settings.json`. A launch with Remote Control off carries the same name as `--name`.
_NAME = re.compile(r'--(?:remote-control|name)\s+"([^"]+)"')


def owning_claude(procs=None):
    """The pid of the Claude process this hook runs under, or 0."""
    procs = _procs() if procs is None else procs
    pid, steps = os.getpid(), 0
    while pid in procs and steps < 12:
        ppid, name = procs[pid]
        if name.lower() in CLAUDE_IMAGES:
            return pid
        pid, steps = ppid, steps + 1
    return 0


def session_title(session_id, shell=0):
    """`<title> - <ticket> - <short ticket title> - <mode>` for a session the Agent Board launched, read
    off the `--remote-control` (or `--name`) name on its shell's command line. Any other session is named
    by the first block of its id, which `claude --resume` completes."""
    if shell:
        try:
            import winproc
            line = winproc.command_lines([shell]).get(shell) or ""
            m = _NAME.search(line)
            if m and m.group(1).startswith(_title()):
                # A shell started before the ticket title entered the name still carries
                # the old one. The name is rebuilt from the ticket as it stands now.
                try:
                    import todo
                    return todo.current_title(m.group(1))
                except Exception:                       # noqa: BLE001 - a hook never raises
                    return m.group(1)
        except Exception:                               # noqa: BLE001 - a hook never raises
            pass
    return "%s - %s" % (_title(), session_id[:8])


def set_title(claude_pid, title):
    """Write the title into the console the session's Claude runs in. The hook has no
    console of its own that the owner sees, so it attaches to Claude's for the one call."""
    if not claude_pid:
        return False
    if os.name != "nt":
        # The hook shares Claude's controlling terminal: the title goes there as an escape
        # sequence, which tmux and Terminal both take as the window title.
        try:
            with open("/dev/tty", "w") as tty:
                tty.write("\033]0;%s\007" % title)
            return True
        except OSError:
            return False
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.FreeConsole()
    if not k32.AttachConsole(int(claude_pid)):
        return False
    try:
        return bool(k32.SetConsoleTitleW(title))
    finally:
        k32.FreeConsole()


# The modes `claude.exe` turns on in Windows Terminal and turns off only when it exits
# normally: mouse tracking (1000, 1002, 1003) with SGR encoding (1006), focus reporting
# (1004), bracketed paste (2004), synchronised update (2026), win32-input-mode (9001),
# the alternate screen (1049) and the hidden cursor. A crash, or a `taskkill /F`, leaves
# every one of them on, and the shell that gets the tab back reads clicks and keys as
# escape sequences.
TERMINAL_RESET = b"".join(b"[?%dl" % n for n in
                          (1000, 1002, 1003, 1006, 1004, 2004, 2026, 9001, 1049)) + b"[?25h"


def reset_terminal():
    """Write the reset sequence to this process's console. Returns True when written."""
    out = getattr(sys.stdout, "buffer", None) or sys.stdout
    if os.name == "nt":
        # The console parses the sequence only with virtual terminal processing on; a
        # plain `cmd` tab of Windows Terminal has it on already, a bare console may not.
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            h = k32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k32.GetConsoleMode(h, ctypes.byref(mode)):
                k32.SetConsoleMode(h, mode.value | 0x0004)
        except Exception:                               # noqa: BLE001 - best effort
            pass
    try:
        out.write(TERMINAL_RESET)
        out.flush()
        return True
    except Exception:                                   # noqa: BLE001 - a hook never raises
        return False


def _project_for(start):
    """The project root at or above `start`, or None."""
    import project
    return project.find_root(start) if start else None


def main(argv=None, payload=None, started=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--reset-terminal" in argv:
        return 0 if reset_terminal() else 1
    import project
    if any(a in argv for a in ("--list", "--live", "--prune", "--judge")):
        ident = None
        if "--project" in argv and argv.index("--project") + 1 < len(argv):
            ident = argv[argv.index("--project") + 1]
        project.use(project.resolve(ident))
    else:
        # A hook that fails must not stop the session it runs in, so every fault is an exit 0.
        try:
            if payload is None:
                payload = json.loads(sys.stdin.read() or "{}")
            # The project the session was launched in, not the directory its shell has
            # moved to: a manager that `cd`s into another project stays in its own.
            root = (_project_for(os.environ.get("CLAUDE_PROJECT_DIR", ""))
                    or _project_for(str(payload.get("cwd") or "")))
            if root is None:
                return 0
            project.use(root)
            # A session that became another project's writes there.
            home = redirected_to(str(payload.get("session_id") or ""))
            if home is not None:
                project.use(home)
        except Exception:                               # noqa: BLE001 - a hook never raises
            return 0
        return _hook(payload, started)
    if "--judge" in argv:
        i = argv.index("--judge")
        if i + 2 >= len(argv):
            return 2
        try:
            judge(argv[i + 1], int(argv[i + 2]), recap="--recap" in argv)
        except Exception:                               # noqa: BLE001 - nobody reads its output
            return 1
        return 0
    if "--list" in argv:
        for k, when in sorted(idle_sessions().items(), key=lambda kv: kv[1]):
            print("%s  %s" % (k, time.strftime("%Y-%m-%d %H:%M", time.localtime(when))))
        return 0
    if "--live" in argv:
        for sid, rec in sorted(live_records().items(), key=lambda kv: kv[1]["first_seen"]):
            print("%s  claude %s%s  shell %s %s%s  %s" % (
                sid[:8], rec["claude"], "" if rec["claude_alive"] else " (gone)",
                rec["shell"], rec["shell_image"], "" if rec["shell_alive"] else " (gone)",
                rec["title"]))
        return 0
    if "--prune" in argv:
        print("%d marker(s) removed" % prune())
        return 0
    return 0


def _launch_of(session_id):
    """`(ticket, mode)` of the launch this session id belongs to, `(None, "")` when none."""
    import todo
    sid = session_id.lower()
    for t in todo.load_all():
        modes = {todo.launch_session_id(t.id, m, t.run if t.repeat else 1): m
                 for m in todo.MODES}
        modes.update({e["session"]: e.get("mode") or "" for e in t.sessions})
        if sid in modes:
            return t, modes[sid]
    return None, ""


def own_ticket(session_id):
    """The id of the ticket this session was launched on, "" when none."""
    try:
        t, _ = _launch_of(session_id)
    except Exception:                                   # noqa: BLE001 - no project, no tree
        return ""
    return t.id if t is not None else ""


def close_finished(session_id, text, shell=0, launch=None):
    """At the end of a turn, start the detached closer for a launched session that is done:
    a `plan` session whose ticket is `ready to implement`, or a session of any launch mode
    whose last message ends with `WORK IS FINISHED` or says its ticket is in the verify or
    the closed lane. Its terminal closes whether or not it ran `close --self` and whether or
    not the board runs. Returns whether it started.

    `WORK IS FINISHED` closes at once (`--now`) when the ticket agrees with it
    (`todo.finish_undone`) and no question was reported in the turn. When it does not, no
    closer starts: work is never finished while a question is open, and the board shows the
    session as `input`. A plan that leaves a decision, a name or a value open closes nothing
    either. `launch` is `_launch_of(session_id)` when the caller has it already."""
    import todo
    t, mode = launch or _launch_of(session_id)
    if t is None or mode not in todo.MODES or t.repeat == "manager":
        return False
    if says_finished(text):
        if reported(session_id, shell) == "input" or todo.finish_undone(t, mode):
            return False
        return start_closer(t, mode, now=True)
    if owner_talks(session_id, shell):
        # The owner talks to it: only its own `WORK IS FINISHED` ends the session.
        return False
    if mode == "plan" and todo.plan_done(t):
        if todo.finish_undone(t, mode):
            return False
        return start_closer(t, mode)
    if says_done(text, t.id):
        return start_closer(t, mode)
    return False


def start_closer(t, mode, now=False):
    """Start `todo.py close <id> --mode <mode> --when-finished`, detached. The closer waits
    until the turn has ended and closes only a `finished` session, so a question keeps the
    terminal open; with `now` it closes the ended turn at once (`todo.close_when_finished`).
    A manager never closes. Returns whether it started."""
    import project
    import todo
    if t is None or mode not in todo.MODES or t.repeat == "manager":
        return False
    todo.spawn_detached([sys.executable, str(Path(todo.__file__).resolve()), "--project",
                         str(project.current().ROOT), "close", t.id, "--mode", mode,
                         "--when-finished"] + (["--now"] if now else []))
    return True


def dialog_text(payload):
    """What a dialog asks the owner, for the board's hover and the manager's update: each
    question of an `AskUserQuestion` with its option labels, `Which lane? (Pipeline |
    Blocked)`, the questions joined by ` / `; the approval of a plan; the message of a
    notification. At most `DIALOG_CHARS` long. `True` when the payload names nothing."""
    tool = str(payload.get("tool_name") or "")
    tin = payload.get("tool_input")
    tin = tin if isinstance(tin, dict) else {}
    if tool == ASK_TOOL:
        qs = []
        for q in tin.get("questions") or []:
            if not isinstance(q, dict) or not str(q.get("question") or "").strip():
                continue
            opts = [str(o.get("label") or "").strip() for o in q.get("options") or []
                    if isinstance(o, dict)]
            opts = " | ".join(o for o in opts if o)
            qs.append(str(q["question"]).strip() + (" (%s)" % opts if opts else ""))
        text = " / ".join(qs)
    elif tool == "ExitPlanMode":
        text = "approve the plan"
    else:
        text = str(payload.get("message") or "").strip()
    text = " ".join(text.split())
    if len(text) > DIALOG_CHARS:
        text = text[:DIALOG_CHARS - 3] + "..."
    return text or True


def notified_dialog(session_id, shell, payload):
    """The text of a dialog that a notification announces. Claude Code also notifies an open
    `AskUserQuestion` or plan approval as a permission prompt: then the marker that the
    dialog's `PreToolUse` wrote keeps its questions and options, unless the notification names
    another tool."""
    tool = re.search(r"\bto use (\w+)", str(payload.get("message") or ""))
    kept = idle_question(session_id, shell) if idle_dialog(session_id, shell) else ""
    if kept and (tool is None or tool.group(1) in DIALOG_TOOLS):
        return kept
    return dialog_text(payload)


def _after_tool(sid, payload, started):
    """`PostToolUse`: the turn goes on, so a dialog marker written before this hook started
    is answered, and an answered `AskUserQuestion` also closes the `input` report written
    before it; `CronCreate` and `CronDelete` change the session's jobs. It fires after
    every tool, so it reads the process table only when there is something to write."""
    tool = str(payload.get("tool_name") or "")
    if tool not in (CRON_CREATE, CRON_DELETE, ASK_TOOL) and not any(
            _dir().glob("%s.*.idle" % sid.lower())):
        return 0
    shell = owning_shell(_procs())
    clear_dialog(sid, shell, started)
    if tool == ASK_TOOL:
        clear_answered(sid, shell, started)
    tin = payload.get("tool_input")
    tin = tin if isinstance(tin, dict) else {}
    if tool == CRON_CREATE and tin.get("recurring", True) is not False:
        add_job(sid, shell, job_id(payload.get("tool_response"))
                or "?" + str(payload.get("tool_use_id") or started))
    elif tool == CRON_DELETE:
        drop_job(sid, shell, str(tin.get("id") or ""))
    return 0


def _note_path(session_id):
    """The note of the reply check (`reply_check.py`) that waits for the session's next
    prompt."""
    return _dir() / ("%s.replynote" % session_id.lower())


def start_reply_check(session_id, shell, payload, text):
    """Start the reply check of the last message, detached: it never holds the turn, never
    blocks it and never makes the session write the message again. A sentence that fails
    reaches the session with its next prompt (`reply_note`). Returns whether it started."""
    try:
        import project
        import reply_check
        p = project.current()
        if not p.reply_check or not reply_check.worth_checking(text):
            return False
        request = reply_check.request_of(payload.get("transcript_path"))
        try:
            mode = _launch_of(session_id)[1]
        except Exception:                               # noqa: BLE001 - the prompt names it
            mode = ""
        reply_check.start(_dir() / ("%s.replyjob" % key(session_id, shell)), {
            "message": text, "request": reply_check.launch_request(request, mode, p.ROOT),
            "cwd": str(p.ROOT), "model": p.models.get("haiku", reply_check.MODEL),
            "provider": "claude", "session": session_id, "home": str(project.home()),
            "note": str(_note_path(session_id))})
        return True
    except Exception:                                   # noqa: BLE001 - a hook never raises
        return False


def reply_note(session_id):
    """The `UserPromptSubmit` answer that gives the session the note of the reply check, or
    None when no note waits."""
    try:
        import reply_check
        text = reply_check.take_note(_note_path(session_id))
    except Exception:                                   # noqa: BLE001 - a hook never raises
        return None
    if not text:
        return None
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                   "additionalContext": text}}


def _hook(payload, started=None):
    """One hook event of the current project: the title, the live record, the idle marker,
    the scheduled jobs. `started` is when the hook process began."""
    started = time.time() if started is None else started
    if os.environ.get(JUDGE_ENV):
        return 0
    try:
        sid = str(payload.get("session_id") or "")
        if not _UUID.match(sid):
            return 0
        event = str(payload.get("hook_event_name") or "")
        if event == "PostToolUse":
            return _after_tool(sid, payload, started)
        procs = _procs()
        shell = owning_shell(procs)
        claude = owning_claude(procs)
        title = session_title(sid, shell)
        try:
            set_title(claude, title)
        except Exception:                               # noqa: BLE001 - a hook never raises
            pass
        try:
            record_live(sid, event, title, claude, shell, procs, str(payload.get("cwd") or ""))
        except Exception:                               # noqa: BLE001 - a hook never raises
            pass
        if event in IDLE_EVENTS:
            # `last_assistant_message` is a documented field of the `Stop` payload. The
            # transcript is not read: its format is no contract.
            text = str(payload.get("last_assistant_message") or "")
            try:
                launch = _launch_of(sid)
            except Exception:                           # noqa: BLE001 - a hook never raises
                launch = (None, "")
            judge_it = mark_stop(sid, shell, text, ticket=launch[0].id if launch[0] else "")
            # `background_tasks` is not in the hook documentation. Claude Code 2.1.287 sends
            # it: the subagents and background shells that still run when the turn ends.
            mark_tasks(sid, shell, running_tasks(payload.get("background_tasks")))
            # Haiku reads only what no rule above could read.
            if judge_it:
                try:
                    import project
                    start_judge(sid, shell, text, project.current().ROOT)
                except Exception:                       # noqa: BLE001 - a hook never raises
                    pass
            try:
                close_finished(sid, text, shell, launch)
            except Exception:                           # noqa: BLE001 - a hook never raises
                pass
            start_reply_check(sid, shell, payload, text)
            prune()
        elif event == STOP_FAILURE:
            # `error` is a documented field of the `StopFailure` payload: the error type.
            mark_idle(sid, shell, error=str(payload.get("error") or "error"))
            prune()
        elif event == "Notification":
            if payload.get("notification_type") in DIALOG_NOTIFICATIONS:
                mark_idle(sid, shell, dialog=notified_dialog(sid, shell, payload))
        elif event == "PreToolUse":
            if payload.get("tool_name") in DIALOG_TOOLS:
                mark_idle(sid, shell, dialog=dialog_text(payload))
        elif event in BUSY_EVENTS:
            if not (event == "SessionStart" and payload.get("source") in KEEPS_STATE):
                mark_busy(sid, shell)
            if event == "UserPromptSubmit":
                # `prompt` is a documented field of the `UserPromptSubmit` payload.
                mark_talk(sid, shell, payload.get("prompt"))
                out = reply_note(sid)
                if out:
                    sys.stdout.write(json.dumps(out) + "\n")
                    sys.stdout.flush()
            if event == "SessionStart" and payload.get("source") in NEW_PROCESS:
                clear_jobs(sid, shell)
    except Exception:                                   # noqa: BLE001 - a hook never raises
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
