"""managercheck.py - the manager's check, permanently on.

    python octopus.py check [--watch] [--dry-run]

The manager's model runs only when the manager has something to say. Two timers run the check
in Python and wake the manager only on news (`evaluate`):

- THE BOARD. Every `INTERVAL` seconds the board's watchdog runs `evaluate` for each live
  manager session of every AI provider, and types `wake_text()` into the session on news. Its
  heartbeat (`mark_board`) says that the board runs.
- THE FALLBACK. `check --watch`, which a Claude manager runs in the background, wakes no model
  while it waits. While the board's heartbeat is older than `BOARD_STALE`, it runs `evaluate`
  `BUSY_INTERVAL` after the last check when that check found an agent, else `IDLE_INTERVAL`
  after it. On news it runs the check, prints it, and exits, which calls the manager back. A
  new watcher replaces the one before it, which exits with no output.

News is an agent that finished since the last update, a question that waits for the owner and
was not reported yet, or a notice that was not reported yet. A working agent and a question
that was reported already are no news. `manager-watch.json` beside the registry holds the
state.

One check, `check` alone, does four things:

1. FOLLOW-UPS. A ticket that holds `projects` and sits in the verify or the closed lane names
   its follow-ups under `## Follow-ups`, one line each: project, id and title. A follow-up
   in its project's start lane that was never launched gets its session now, `plan` when its
   difficulty is `unknown`, else `work`. No other ticket is started.
2. THE UPDATE. `sessions --if-any`: the four sections while any agent finished since the last
   update, waits for the owner, works or waits for anything else; nothing at all only when
   none does. Never a line that says nothing changed.
3. THE NOTICES. Each notice that a session of another project sent about a defect of an
   Octopus AI tool (`todo notify`) and the manager did not decide yet stands on stderr, with
   the commands that decide it: a new Octopus AI ticket, its evidence on an existing ticket,
   or no ticket.
4. THE AI TICKETS. Each open ticket of a registered project that an AI filed without an
   owner's request and that sits outside AI-Pipeline with no `filed-by: ai` moves there
   (`aifiled.sweep`). It writes nothing to the update.
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
BUSY_INTERVAL = 3600
IDLE_INTERVAL = 3 * 3600
BOARD_STALE = 2 * INTERVAL
WATCH_POLL = 60
STATE_FILE = "manager-watch.json"
FOLLOWUPS = "Follow-ups"
_TICKET = re.compile(r"\bT\d{4,}\b")


def wake_text():
    """What the board types into a manager session on news."""
    return ('Manager check: run python "%s" check. Print its output as it is; when it '
            'prints nothing, write nothing. Decide each notice it lists with the command it '
            'names, and write nothing about the notices. Then end this turn immediately. '
            'Keep the session open for the owner. Do not call sleep, poll, close the '
            'session, or schedule another check; the board owns the timer.'
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


def unlaunched(registry=None):
    """`[(project name, root, ticket id, source)]`: each follow-up a finished cross-project
    ticket named that sits in its start lane or its `ai` lane and was never launched.
    `source` is `<project> <id>` of the ticket that named it."""
    import todo as T
    registry = project.load_registry() if registry is None else registry
    roots = {r["name"]: Path(r["path"]) for r in registry}
    named = []
    for name, root in roots.items():
        tok = _use(root)
        if tok is None:
            continue
        try:
            done = (T.lane("verify"), T.lane("closed"))
            for t in T.load_all():
                if t.state in done and "projects" in t.permits:
                    named += [(p, tid, "%s %s" % (name, t.id))
                              for p, tid in named_followups(t.body, roots, name)]
        except Exception:                               # noqa: BLE001 - the check goes on
            pass
        finally:
            project.reset(tok)
    out, seen = [], set()
    for name, tid, source in named:
        if (name, tid) in seen or name not in roots:
            continue
        seen.add((name, tid))
        tok = _use(roots[name])
        if tok is None:
            continue
        try:
            t = T.find(T.load_all(), tid)
            if t is not None and t.state in (T.lane("start"), T.lane("ai")) \
                    and not t.sessions and not t.repeat:
                out.append((name, roots[name], tid, source))
        except Exception:                               # noqa: BLE001
            pass
        finally:
            project.reset(tok)
    return out


def _use(root):
    try:
        return project.use(root)
    except Exception:                                   # noqa: BLE001 - an unreachable project
        return None


def launch_followups(registry=None, dry_run=False, err=None):
    """Launch every `unlaunched` follow-up. What a launch prints is kept out of the update;
    a launch that fails says so on `err`. Returns `[(project name, ticket id)]` launched."""
    import todo as T
    err = sys.stderr if err is None else err
    done = []
    for name, root, tid, source in unlaunched(registry):
        tok = project.use(root)
        try:
            args = argparse.Namespace(note="A follow-up of %s." % source, terminal=None,
                                      dry_run=dry_run)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = T.launch_new(tid, args)
        except Exception as exc:                        # noqa: BLE001 - the check goes on
            rc, buf = 1, io.StringIO("%s: %s" % (type(exc).__name__, exc))
        finally:
            project.reset(tok)
        if dry_run:
            err.write(buf.getvalue())
        if rc:
            err.write("check: %s %s: the launch failed: %s\n"
                      % (name, tid, " ".join(buf.getvalue().split())[:200]))
        else:
            done.append((name, tid))
    return done


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
    """`{"board", "checked", "busy", "questions", "notices", "watcher"}`, each part absent
    until something wrote it."""
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
    """One check with no model: the AI ticket sweep, the follow-ups, and the update's rows,
    with no write of the update's snapshot. Records when it ran and whether any agent was
    there. Returns `(news, seen)`; call `remember(seen)` once the news reached the manager."""
    import sessionreport
    err = sys.stderr if err is None else err
    now = time.time() if now is None else now
    sort_ai_tickets(registry, dry_run, err)
    launch_followups(registry, dry_run, err)
    rows, _live = sessionreport.collect(True, registry)
    questions = sorted({"%s %s" % (r.get("key") or "", r.get("text") or "")
                        for r in rows if r.get("section") == "input"})
    seen = {"questions": questions, "notices": notice_ids()}
    st = load_state()
    news = (any(r.get("section") == "finished" for r in rows)
            or bool(set(questions) - set(st.get("questions") or ()))
            or bool(set(seen["notices"]) - set(st.get("notices") or ())))
    update_state(checked=now, busy=bool(rows), **({} if news else seen))
    return news, seen


def remember(seen):
    """Record the questions and the notices that reached the manager."""
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
    launch_followups(registry, dry_run, err)
    _report(registry, dry_run, out, err)
    return 0


def watch(registry=None, out=None, now_fn=None, sleep=None, token=None, loops=None):
    """The fallback while the board does not run. Waits with no output, and returns after it
    printed the update and the notices on `out` on news. Returns 0 with no output when a newer
    watcher replaced it."""
    import uuid
    out = sys.stdout if out is None else out
    now_fn = time.time if now_fn is None else now_fn
    sleep = time.sleep if sleep is None else sleep
    token = token or uuid.uuid4().hex
    update_state(watcher=token)
    n = 0
    while loops is None or n < loops:
        n += 1
        sleep(WATCH_POLL)
        st = load_state()
        if st.get("watcher") != token:
            return 0
        now = now_fn()
        if board_runs(now):
            continue
        if _within(now, st.get("checked"), BUSY_INTERVAL if st.get("busy") else IDLE_INTERVAL):
            continue
        news, seen = evaluate(registry, now=now, err=out)
        if news:
            _report(registry, False, out, out)
            remember(seen)
            return 0
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="octopus check",
                                 description="the manager's check: launch the unlaunched "
                                             "follow-ups, then print the update while any "
                                             "agent finished, waits or works")
    ap.add_argument("--watch", action="store_true",
                    help="the fallback while the board does not run: check every hour while "
                         "an agent was there, else every three hours, and exit with the "
                         "update on news")
    ap.add_argument("--dry-run", action="store_true",
                    help="launch no follow-up, print what each launch would run, and "
                         "leave the snapshot of the last update as it was")
    a = ap.parse_args(argv)
    if a.watch:
        return watch()
    return run(dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
