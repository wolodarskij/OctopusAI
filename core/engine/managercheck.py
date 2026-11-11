"""managercheck.py - the manager's check, permanently on.

    python octopus.py check [--watch] [--dry-run]

The manager's model runs only when the manager has something to say. Two timers run the check
in Python every `INTERVAL` seconds and wake the manager only on news (`evaluate`). Both type
`wake_text()` into each live manager session through `wake_home`, and `claim_wake` lets one
wake per interval through across both:

- THE BOARD. Every `INTERVAL` seconds the board's watchdog runs `wake_home` for each live
  manager session of every AI provider. Its heartbeat (`mark_board`) says that the board runs.
- THE FALLBACK. `check --watch` is a detached engine process that wakes no model while it
  waits. The engine starts it (`ensure_watcher`) when it launches or claims the manager, and
  at each end of the manager's turn when it does not run; the manager's model starts none.
  While the board's heartbeat is older than `BOARD_STALE`, it runs `wake_home` every
  `WATCH_POLL` seconds. It never exits on news. It exits when a newer watcher replaced it,
  when the engine's code changed, or when the home project has no active manager ticket.

News is every row of the update: an agent that finished since the last update, one that waits
for the owner, one that works and one that waits for anything else. A notice that was not
reported yet is news too. So while any agent runs, the manager gets the full update every
interval, and a finished agent reaches it within one interval. `manager-watch.json` beside
the registry holds the state.

One check, `check` alone, does three things. It launches no ticket and no follow-up:

1. THE UPDATE. `sessions --if-any`: the five sections while any agent finished since the last
   update, waits for the owner, works or waits for anything else; nothing at all only when
   none does. Never a line that says nothing changed.
2. THE NOTICES. Each notice that a session of another project sent about a defect of an
   Octopus AI tool (`todo notify`) and the manager did not decide yet stands on stderr, with
   the commands that decide it: a new Octopus AI ticket, its evidence on an existing ticket,
   or no ticket.
3. THE AI TICKETS. Each open ticket of a registered project that an AI filed without an
   owner's request and that sits outside AI-Pipeline with no `filed-by: ai` moves there
   (`aifiled.sweep`). It writes nothing to the update.

`named_followups` reads the `## Follow-ups` section of a ticket for `aifiled`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, HERE.parent / "win", HERE.parent / "hooks"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import project  # noqa: E402

INTERVAL = 600
BOARD_STALE = 2 * INTERVAL
WATCH_POLL = 60
# Seconds a lone character must stand unchanged in a manager's input box before the wake
# takes it for a stray one, and how long the wake waits for the box to clear after it.
STRAY_AFTER = 60
STRAY_CLEAR = 2.0
STATE_FILE = "manager-watch.json"
FOLLOWUPS = "Follow-ups"
_TICKET = re.compile(r"\bT\d{4,}\b")


def wake_text(s=None):
    """What the board and the watcher type into a manager session `s` on news: the skill
    call `/manager-check`, or the full text when the session cannot call the skill."""
    import todo as T
    return T.skill_call(s, T.MANAGER_CHECK_SKILL) or (
        'Manager check: run python "%s" check. Print its output as it is; when it '
        'prints nothing, write nothing. Decide each notice it lists with the command it '
        'names, and write nothing about the notices. Then end this turn immediately. '
        'Keep the session open for the owner. Do not call sleep, poll, close the '
        'session, or schedule another check; the engine owns the timer.'
        % (project.OCTOPUS / "octopus.py").as_posix())


def claim_wake(key, now, interval=INTERVAL):
    """Reserve at most one wake per interval across board processes and restarts."""
    import sqlite3
    path = project.home() / "manager-wakes.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(str(path), timeout=1)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS wakes "
                   "(root TEXT, session TEXT, at REAL, PRIMARY KEY(root, session))")
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT at FROM wakes WHERE root=? AND session=?", key).fetchone()
            if row is not None and 0 <= now - row[0] < interval:
                return False
            db.execute("INSERT OR REPLACE INTO wakes VALUES (?, ?, ?)", (*key, now))
            return row is not None and now >= row[0]


def release_wake(key, now, interval=INTERVAL):
    """Give back the wake that `claim_wake(key, now)` reserved and did not use: the next
    claim of `key` goes through."""
    import sqlite3
    path = project.home() / "manager-wakes.sqlite3"
    with contextlib.closing(sqlite3.connect(str(path), timeout=1)) as db:
        with db:
            db.execute("UPDATE wakes SET at=? WHERE root=? AND session=? AND at=?",
                       (now - interval, *key, now))


# ── the wake ───────────────────────────────────────────────────────────────

def wake_home(now, note=None, mark=False):
    """Check the manager sessions of the current project, when it is the manager's home, and
    type the check into each one on news. A session whose brief is older than the brief
    sections is told to read the brief again first. `mark` writes the board's heartbeat.
    `note(now, managers, skip)` hears what the tick saw. Returns the keys of the sessions
    woken."""
    import todo as T
    note = note or (lambda *_a: None)
    if not T.at_manager_home():
        return []
    root = project.P.ROOT
    managers = {t.id: t for t in T.load_all()
                if t.repeat == T.MANAGER and t.state == T.lane("active")}
    if not managers:
        note(now, managers, "")
        return []
    if mark:
        mark_board(now)
    woken = []
    live = [s for s in T.live_sessions()
            if s.get("id") in managers and s.get("mode") == "work" and s.get("claude")]
    note(now, managers, "" if live else "no manager session runs")
    for s in live:
        # Keys typed into an open dialog, or into a question before the first turn, would
        # answer it. Any other `input` word comes from the manager's own update, which lists
        # the questions of other agents: the check goes on.
        if s.get("dialog") or s.get("hooked") is False:
            note(now, managers, "the manager waits for your answer")
            continue
        # Text typed into a box that holds the owner's unsent message cuts into it. The
        # session waits, unclaimed, and the next tick reads the box again. A stray
        # character that nobody types is deleted before the wake types.
        try:
            scr = T.screen_session(s)
            busy = T.input_box_busy(s, scr)
            stray = stray_char(s, scr, now) if busy else forget_box(s)
        except OSError as exc:
            busy, stray = "its screen could not be read: %s" % exc, ""
        if busy and not stray:
            note(now, managers, "the check waits: %s" % busy)
            continue
        t = managers[s["id"]]
        if T.manager_brief_stale(t):
            brief = T.rewrite_manager_brief(t)
            try:
                clear_stray(s, stray)
                T.send_when_box_empty(s, T.manager_brief_text(brief, s), ["Enter"])
            except (OSError, ValueError) as exc:
                os.utime(str(brief), ns=(0, 0))         # stale again: the next tick tries again
                print("watchdog: %s %s: the new brief could not be typed: %s"
                      % (root.name, s["id"], exc))
            continue
        key = (os.path.normcase(str(root.resolve())), s["session"])
        if not claim_wake(key, now):
            continue
        news, seen = evaluate(now=now)
        if not news:
            continue
        try:
            clear_stray(s, stray)
            T.send_when_box_empty(s, wake_text(s), ["Enter"])
        except T.InputBoxBusy:
            release_wake(key, now)
            note(now, managers, "the check waits: the input box holds unsent text")
            continue
        except (OSError, ValueError) as exc:
            print("watchdog: %s %s: the check could not be typed: %s" % (root.name, s["id"], exc))
            continue
        remember(seen)
        woken.append(s["key"])
    return woken


def stray_char(s, scr, now):
    """The lone character in the input box of session `s` that nobody types, or "". It is
    one character with the cursor right after it, and the box held the same character at a
    read `STRAY_AFTER` seconds or more before `now`. Each call records what the box holds."""
    import todo as T
    text = T.typed_input(scr) or ""
    boxes = load_state().get("boxes") or {}
    seen = boxes.get(s["session"]) or {}
    if seen.get("text") != text:
        seen = boxes[s["session"]] = {"text": text, "since": now}
        update_state(boxes=boxes)
    box = T.input_box(scr)
    if len(text) != 1 or box is None or box["rows"][0].strip() != text:
        return ""
    end = [box["col"] + len(box["rows"][0].rstrip()), box["row"]]
    if list(scr.get("cursor") or ()) != end or now - seen["since"] < STRAY_AFTER:
        return ""
    return text


def forget_box(s):
    """Drop the record of what the input box of session `s` held. Returns ""."""
    boxes = load_state().get("boxes") or {}
    if boxes.pop(s["session"], None) is not None:
        update_state(boxes=boxes)
    return ""


def clear_stray(s, stray):
    """Delete the stray character `stray` from the input box of session `s`, and wait up to
    `STRAY_CLEAR` seconds for the box to show empty. Raises `InputBoxBusy` when it does not."""
    import todo as T
    if not stray:
        return
    T.send_session(s, "", ["Backspace"] * len(stray))
    end = time.monotonic() + STRAY_CLEAR
    while True:
        busy = T.input_box_busy(s)
        if not busy:
            forget_box(s)
            return
        if time.monotonic() >= end:
            raise T.InputBoxBusy("%s: %s" % (s.get("key") or s.get("id"), busy))
        time.sleep(0.25)


# ── the follow-ups ─────────────────────────────────────────────────────────

def followup_lines(body):
    """The lines of the `## Follow-ups` section of a ticket body."""
    out, inside = [], False
    for ln in (body or "").splitlines():
        if ln.startswith("## "):
            inside = ln[3:].strip().lower() == FOLLOWUPS.lower()
            continue
        if inside and ln.strip():
            out.append(ln)
    return out


def named_followups(body, names, own):
    """`[(project name, ticket id)]` that the `## Follow-ups` section names. A line names its
    project by the longest registered name in it, else it is a ticket of `own`."""
    found = []
    for ln in followup_lines(body):
        hits = [n for n in names if n and n in ln]
        name = max(hits, key=len) if hits else own
        for tid in _TICKET.findall(ln):
            if (name, tid) not in found:
                found.append((name, tid))
    return found


# ── the notices ────────────────────────────────────────────────────────────

def list_notices(err=None):
    """Write each open notice of the Octopus AI project on `err`, with the commands that
    decide it. Returns how many wait."""
    import todo as T
    err = sys.stderr if err is None else err
    try:
        with T.in_manager_home():
            waiting = T.open_notices()
            name = T.manager_home_name()
    except Exception:                                   # noqa: BLE001 - the check goes on
        return 0
    if not waiting:
        return 0
    cmd = 'python "%s" todo --project "%s" notice' % (
        (project.OCTOPUS / "octopus.py").as_posix(), name)
    err.write("check: %d Octopus AI notice(s) wait for your decision. Decide each one with "
              "one of these commands, and write nothing about it:\n" % len(waiting))
    err.write('  %s <id> ticket --title "..." --severity <severity> --difficulty '
              '<difficulty>\n' % cmd)
    err.write("  %s <id> evidence <Octopus AI ticket id>\n" % cmd)
    err.write('  %s <id> none --why "..."\n' % cmd)
    for _f, meta, body in waiting:
        err.write("- %s\n" % T.notice_line(meta, body))
        err.write("  Evidence: %s\n" % " ".join(T.notice_section(body, "Evidence").split()))
    return len(waiting)


# ── the AI tickets ─────────────────────────────────────────────────────────

def sort_ai_tickets(registry=None, dry_run=False, err=None):
    """Move the open tickets that an AI filed without an owner's request to AI-Pipeline.
    What the sweep prints is kept out of the update; a failure says so on `err`."""
    import aifiled
    err = sys.stderr if err is None else err
    try:
        aifiled.sweep(registry, dry_run, out=io.StringIO(), err=err)
    except Exception as exc:                            # noqa: BLE001 - the check goes on
        err.write("check: the AI ticket sweep failed: %s: %s\n" % (type(exc).__name__, exc))


# ── the news ───────────────────────────────────────────────────────────────

def state_file():
    return project.home() / STATE_FILE


def load_state():
    """`{"board", "checked", "notices", "watcher", "watcher_pid", "watcher_started",
    "boxes"}`, each part absent until something wrote it. `boxes` maps a manager session to
    the text of its input box and the time it first held it."""
    try:
        data = json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update_state(**parts):
    """Write `parts` into the state. Returns the new state."""
    data = load_state()
    data.update(parts)
    f = state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name("%s.%d.tmp" % (f.name, os.getpid()))
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(f))
    return data


def _within(now, then, span):
    """Whether `then` lies at most `span` seconds before `now`."""
    try:
        return 0 <= now - float(then or 0) < span
    except (TypeError, ValueError):
        return False


def mark_board(now=None):
    """The board's heartbeat: the board runs and checks the manager."""
    now = time.time() if now is None else now
    if not _within(now, load_state().get("board"), WATCH_POLL):
        update_state(board=now)


def board_runs(now=None):
    """Whether the board's heartbeat is younger than `BOARD_STALE`."""
    now = time.time() if now is None else now
    return _within(now, load_state().get("board"), BOARD_STALE)


def notice_ids():
    """The ids of the notices that wait for the manager's decision."""
    import todo as T
    try:
        with T.in_manager_home():
            return sorted(str(meta.get("id") or f) for f, meta, _b in T.open_notices())
    except Exception:                                   # noqa: BLE001 - the check goes on
        return []


def evaluate(registry=None, dry_run=False, now=None, err=None):
    """One check with no model: the AI ticket sweep and the update's rows,
    with no write of the update's snapshot. Records when it ran. News is any row of the
    update, or a notice that was not reported yet. Returns `(news, seen)`; call
    `remember(seen)` once the news reached the manager."""
    import sessionreport
    err = sys.stderr if err is None else err
    now = time.time() if now is None else now
    sort_ai_tickets(registry, dry_run, err)
    rows, _live = sessionreport.collect(True, registry)
    seen = {"notices": notice_ids()}
    news = bool(rows) or bool(set(seen["notices"]) - set(load_state().get("notices") or ()))
    update_state(checked=now, **({} if news else seen))
    return news, seen


def remember(seen):
    """Record the notices that reached the manager."""
    update_state(**seen)


LATE_MARGIN = 60


def check_view(since, now=None, interval=INTERVAL):
    """`{"checked", "interval", "late"}`: when the last check ran, and whether none ran within
    `interval` plus `LATE_MARGIN` seconds. `since` is when the board began to check, so a
    board that just started is not late."""
    now = time.time() if now is None else now
    try:
        checked = float(load_state().get("checked") or 0)
    except (TypeError, ValueError):
        checked = 0.0
    late = now - max(checked, float(since or 0)) > interval + LATE_MARGIN
    return {"checked": checked, "interval": interval, "late": late}


# ── the run ────────────────────────────────────────────────────────────────

def _report(registry, dry_run, out, err):
    import sessionreport
    sessionreport.report(since_last=True, if_any=True, registry=registry, out=out,
                         save=not dry_run)
    list_notices(err)


def run(dry_run=False, registry=None, out=None, err=None):
    """One check, with the update. Returns 0."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    sort_ai_tickets(registry, dry_run, err)
    _report(registry, dry_run, out, err)
    return 0


def _engine_stamp():
    import todo_server
    return todo_server._code_stamp()


def _home_has_manager(T):
    """Whether the manager's home project has a manager ticket in the active lane."""
    try:
        with T.in_manager_home():
            return any(t.repeat == T.MANAGER and t.state == T.lane("active")
                       for t in T.load_all())
    except LookupError:
        return False


def watch(registry=None, out=None, now_fn=None, sleep=None, token=None, loops=None):
    """The fallback while the board does not run. Every `WATCH_POLL` seconds it runs
    `wake_home` in the manager's home project, which types the check into the manager on
    news, as the board does. It writes nothing on `out` but the errors of a poll, and keeps
    running. Returns 0 when a newer watcher replaced it, when the engine's code changed, or
    when the home project has no manager ticket in the active lane."""
    import uuid
    import todo as T
    import winproc
    out = sys.stdout if out is None else out
    now_fn = time.time if now_fn is None else now_fn
    sleep = time.sleep if sleep is None else sleep
    token = token or uuid.uuid4().hex
    pid = os.getpid()
    update_state(watcher=token, watcher_pid=pid, watcher_started=winproc.created_at(pid))
    stamp = _engine_stamp()
    n = 0
    while loops is None or n < loops:
        n += 1
        sleep(WATCH_POLL)
        try:
            if load_state().get("watcher") != token or _engine_stamp() != stamp:
                return 0
            if not _home_has_manager(T):
                return 0
            now = now_fn()
            if board_runs(now):
                continue
            with T.in_manager_home():
                wake_home(now)
        except Exception as exc:                        # noqa: BLE001 - the watcher must live
            out.write("check --watch: %s: %s\n" % (type(exc).__name__, exc))
            out.flush()
    return 0


def watcher_alive(state=None):
    """Whether the watcher that the state names still runs."""
    import winproc
    st = load_state() if state is None else state
    pid = st.get("watcher_pid")
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid not in winproc.processes():
        return False
    started = winproc.created_at(pid)
    return started is not None and started == st.get("watcher_started")


def ensure_watcher():
    """Start the watcher when none runs. Returns whether it started one. Never raises."""
    try:
        if watcher_alive():
            return False
        import todo as T
        T.spawn_detached([sys.executable, str(project.OCTOPUS / "octopus.py"), "check",
                          "--watch"])
        return True
    except Exception:                                   # noqa: BLE001 - the caller goes on
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="octopus check",
                                 description="the manager's check: print the update while "
                                             "any agent finished, waits or works. It "
                                             "launches nothing")
    ap.add_argument("--watch", action="store_true",
                    help="the engine's watcher while the board does not run: type the "
                         "check into the manager every ten minutes while any agent "
                         "finished, waits or works, or a new notice waits")
    ap.add_argument("--dry-run", action="store_true",
                    help="move no ticket, and leave the snapshot of the last update as it "
                         "was")
    a = ap.parse_args(argv)
    if a.watch:
        return watch()
    return run(dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
