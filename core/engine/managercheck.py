"""managercheck.py - the manager's 10-minute check, on the owner's order.

    python octopus.py check [--job-created] [--dry-run]

A manager session runs it every 10 minutes: a recurring job it schedules on the owner's
order (Claude Code's `CronCreate`), or the board's watchdog, which types `WAKE_TEXT` into a
manager whose agent has no scheduler of its own. One run does five things:

1. FOLLOW-UPS. A ticket that holds `projects` and sits in the verify or the closed lane names
   its follow-ups under `## Follow-ups`, one line each: project, id and title. A follow-up
   in its project's start lane that was never launched gets its session now, `plan` when its
   difficulty is `unknown`, else `work`. A ticket in the `ai` lane is never a follow-up. The
   first run after the manager starts or starts again, and the `--job-created` run, launch
   nothing: they record each unlaunched follow-up as known (`check-followups.json` beside the
   registry), and no later run launches a known one. The start of the manager is the
   `launched:` line of its ticket. No other ticket is started.
2. THE UPDATE. `sessions --if-any`: the four sections, every run, while any agent finished
   since the last update, waits for the owner, works or waits for anything else; nothing at
   all only when none does. Never a line that says nothing changed.
3. THE NOTICES. Each notice that a session of another project sent about a defect of an
   Octopus AI tool (`todo notify`) and the manager did not decide yet stands on stderr, every
   run, with the commands that decide it: a new Octopus AI ticket, its evidence on an existing
   ticket, or no ticket.
4. THE AI TICKETS. Each open ticket of a registered project that an AI filed without an
   owner's request and that sits outside AI-Pipeline with no `filed-by: ai` moves there
   (`aifiled.sweep`). It writes nothing to the update.
5. THE JOB'S AGE. `--job-created` records, after `CronCreate`, when the calling session made
   its check job (`check-jobs.json` beside the registry). Claude Code deletes a recurring job
   after 7 days; a check whose job is older than `RENEW_SECONDS` exits 3 and says on stderr
   to make the job again.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
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
RENEW_SECONDS = 6 * 24 * 3600
JOBS_FILE = "check-jobs.json"
FOLLOWUPS_FILE = "check-followups.json"
RENEW = 3
FOLLOWUPS = "Follow-ups"
_TICKET = re.compile(r"\bT\d{4,}\b")


def wake_text():
    """What the watchdog types into a manager whose agent has no scheduler."""
    return ('Run the 10-minute check: python "%s" check. Print its output as it is; when it '
            'prints nothing, write nothing. Decide each notice it lists with the command it '
            'names, and write nothing about the notices.'
            % (project.OCTOPUS / "octopus.py").as_posix())


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
    ticket named that sits in its start lane and was never launched. A ticket in the `ai`
    lane is never a follow-up. `source` is `<project> <id>` of the ticket that named it."""
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
            if t is not None and t.state == T.lane("start") \
                    and not t.ai_filed and not t.sessions and not t.repeat:
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


def _key(name, tid):
    return "%s|%s" % (name, tid)


def manager_start():
    """The start of the running manager: the `launched:` line of each manager ticket in the
    active lane of the manager's home project. A launch, a restart by the watchdog and a claim
    each write it anew. "" when there is none."""
    import todo as T
    try:
        with T.in_manager_home():
            return "; ".join(sorted("%s %s" % (t.id, t.meta.get("launched", ""))
                                    for t in T.load_all()
                                    if t.repeat == T.MANAGER and t.state == T.lane("active")))
    except Exception:                                   # noqa: BLE001 - the check goes on
        return ""


def followups_file():
    return project.home() / FOLLOWUPS_FILE


def load_followups():
    """`{"manager": <manager_start of the last run>, "known": [<project|id>]}`."""
    try:
        data = json.loads(followups_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_followups(start, known):
    f = followups_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"manager": start, "known": sorted(known)}, indent=1),
                 encoding="utf-8")


def launch_followups(registry=None, dry_run=False, err=None, baseline=False):
    """Launch every `unlaunched` follow-up that is not known. With `baseline`, and on the
    first run after the manager starts or starts again, launch nothing and record each one as
    known. What a launch prints is kept out of the update; a launch that fails says so on
    `err`. Returns `[(project name, ticket id)]` launched."""
    import todo as T
    err = sys.stderr if err is None else err
    rows = unlaunched(registry)
    found = {_key(name, tid) for name, _root, tid, _source in rows}
    state = load_followups()
    start = manager_start()
    known = {k for k in state.get("known", ()) if k in found}
    if baseline or state.get("manager") != start:
        if not dry_run:
            save_followups(start, known | found)
        return []
    if not dry_run:
        save_followups(start, known)
    done = []
    for name, root, tid, source in rows:
        if _key(name, tid) in known:
            continue
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


# ── the age of the check job ───────────────────────────────────────────────

def jobs_file():
    return project.home() / JOBS_FILE


def load_jobs():
    try:
        data = json.loads(jobs_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_job(sid, now=None):
    """Record that session `sid` made its check job now. Forgets marks older than a job
    lives."""
    now = time.time() if now is None else now
    data = {k: v for k, v in load_jobs().items()
            if isinstance(v, (int, float)) and now - v < 2 * RENEW_SECONDS}
    data[sid] = now
    f = jobs_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1), encoding="utf-8")


def job_too_old(sid, now=None):
    """Whether the check job of session `sid` is older than `RENEW_SECONDS`."""
    made = load_jobs().get(sid)
    now = time.time() if now is None else now
    return isinstance(made, (int, float)) and now - made > RENEW_SECONDS


def own_session(registry=None):
    """The session id of the Claude this process runs under: looked up in the project of
    the working directory first, then in every registered project. "" when none names it."""
    import todo as T
    roots = []
    here = project.find_root()
    if here:
        roots.append(here)
    roots += [Path(r["path"]) for r in (project.load_registry() if registry is None
                                       else registry)]
    for root in roots:
        tok = _use(root)
        if tok is None:
            continue
        try:
            sid = T.own_session_id()
        except Exception:                               # noqa: BLE001
            sid = ""
        finally:
            project.reset(tok)
        if sid:
            return sid
    return ""


# ── the run ────────────────────────────────────────────────────────────────

def run(job_created=False, dry_run=False, registry=None, sid=None, out=None, err=None,
        now=None):
    """One check. Returns 0, or `RENEW` when the caller's check job must be made again."""
    import sessionreport
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    if job_created:
        sid = sid if sid is not None else own_session(registry)
        if not sid:
            err.write("check: no hook names the session this runs in\n")
            return 2
        record_job(sid, now)
    sort_ai_tickets(registry, dry_run, err)
    launch_followups(registry, dry_run, err, baseline=job_created)
    sessionreport.report(since_last=True, if_any=True, registry=registry, out=out,
                         save=not dry_run)
    list_notices(err)
    if job_created:
        return 0
    sid = sid if sid is not None else own_session(registry)
    if sid and job_too_old(sid, now):
        err.write("check: the check job is older than %d days: make it again with "
                  "CronCreate, delete the old one with CronDelete, then run check "
                  "--job-created\n" % (RENEW_SECONDS // 86400))
        return RENEW
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="octopus check",
                                 description="the manager's 10-minute check: launch the "
                                             "unlaunched follow-ups, except on the first run "
                                             "after the manager starts, then print the update "
                                             "while any agent finished, waits or works")
    ap.add_argument("--job-created", action="store_true",
                    help="this session just made its check job: record when")
    ap.add_argument("--dry-run", action="store_true",
                    help="launch no follow-up, print what each launch would run, and "
                         "leave the snapshot of the last update and the known follow-ups "
                         "as they were")
    a = ap.parse_args(argv)
    return run(job_created=a.job_created, dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
