"""managercheck.py - the manager's 10-minute check.

    python octopus.py check [--job-created] [--dry-run]

A manager session runs it every 10 minutes: a recurring job it makes at its start and at
each restart (Claude Code's `CronCreate`), or the board's watchdog, which types `WAKE_TEXT`
into a manager whose agent has no scheduler of its own. It launches no ticket and no
follow-up. One run does four things:

1. THE UPDATE. `sessions --if-any`: the four sections, every run, while any agent finished
   since the last update, waits for the owner, works or waits for anything else; nothing at
   all only when none does. Never a line that says nothing changed.
2. THE NOTICES. Each notice that a session of another project sent about a defect of an
   Octopus AI tool (`todo notify`) and the manager did not decide yet stands on stderr, every
   run, with the commands that decide it: a new Octopus AI ticket, its evidence on an existing
   ticket, or no ticket.
3. THE AI TICKETS. Each open ticket of a registered project that an AI filed without an
   owner's request and that sits outside AI-Pipeline with no `filed-by: ai` moves there
   (`aifiled.sweep`). It writes nothing to the update.
4. THE CHECK JOB. `--job-created` records, after `CronCreate`, the newest recurring job of
   the calling session as its check job, with the time (`check-jobs.json` beside the
   registry). Claude Code deletes a recurring job after 7 days. A run in a session that holds
   recurring jobs exits 3 and says on stderr to make the job again when its check job is not
   recorded, is gone, or is older than `RENEW_SECONDS`.

`named_followups` reads the `## Follow-ups` section of a ticket for `aifiled`.
"""

from __future__ import annotations

import argparse
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


# ── the check job ──────────────────────────────────────────────────────────

def _use(root):
    try:
        return project.use(root)
    except Exception:                                   # noqa: BLE001 - an unreachable project
        return None


def jobs_file():
    return project.home() / JOBS_FILE


def load_jobs():
    """`{session id: {"job": <job id>, "made": <seconds>}}`. A mark in another form names
    no job."""
    try:
        data = json.loads(jobs_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _made(mark):
    return mark.get("made") if isinstance(mark, dict) \
        and isinstance(mark.get("made"), (int, float)) else None


def record_job(sid, job, now=None):
    """Record `job` as the check job of session `sid`, made now. Forgets marks older than a
    job lives."""
    now = time.time() if now is None else now
    data = {k: v for k, v in load_jobs().items()
            if _made(v) is not None and now - _made(v) < 2 * RENEW_SECONDS}
    data[sid] = {"job": job, "made": now}
    f = jobs_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1), encoding="utf-8")


def session_jobs(sid, root=None, now=None):
    """`{job id: when it was made}`: every recurring job that session `sid` holds, by the
    hook's record of each of its terminals and by its transcript, in the project at `root`
    (the current project when None). A cancelled job is not held, and nor is a job older
    than a job lives that did not fire within `FIRE_SECONDS`."""
    import session_state as S
    import todo as T
    now = time.time() if now is None else now
    tok = _use(root) if root is not None else None
    if root is not None and tok is None:
        return {}
    try:
        found = {}
        for p in S._dir().glob("%s.*.cron" % sid.lower()):
            for j, t in S._read_jobs(p).items():
                if isinstance(t, (int, float)) and t >= now - S.JOB_SECONDS \
                        and not j.startswith("?"):
                    found[j] = min(found.get(j, t), t)
        transcript = T.session_transcript(sid)
        alive = S.transcript_jobs(sid, transcript, now)
        st = S.scan_transcript(sid, transcript) or {}
        gone = st.get("gone") or {}
        found = {j: t for j, t in found.items() if j not in gone}
        for j in alive:
            found[j] = (st.get("made") or {}).get(j) or found.get(j) \
                or (st.get("fired") or {}).get(j) or now
    except Exception:                                   # noqa: BLE001 - the check goes on
        return {}
    finally:
        if tok is not None:
            project.reset(tok)
    return found


def stale_job(sid, held, now=None):
    """Why the check job of session `sid` must be made again, or "" when it need not: no
    check job of the session is recorded, its recorded job is gone, or it is older than
    `RENEW_SECONDS`. `held` is `session_jobs` of the session. A session that holds no
    recurring job (a check typed by hand, or by the watchdog) needs none."""
    if not held:
        return ""
    now = time.time() if now is None else now
    mark = load_jobs().get(sid)
    if _made(mark) is None or not mark.get("job"):
        return "no check job of this session is recorded"
    if mark["job"] not in held:
        return "the recorded check job %s is gone" % mark["job"]
    if now - _made(mark) > RENEW_SECONDS:
        return "the check job %s is older than %d days" % (mark["job"],
                                                          RENEW_SECONDS // 86400)
    return ""


def own_session(registry=None):
    """`(session id, project root)` of the Claude this process runs under: looked up in the
    project of the working directory first, then in every registered project. `("", None)`
    when none names it."""
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
            return sid, root
    return "", None


# ── the run ────────────────────────────────────────────────────────────────

def run(job_created=False, dry_run=False, registry=None, sid=None, out=None, err=None,
        now=None, held=None):
    """One check. Returns 0, 2 when `--job-created` finds no job, or `RENEW` when the
    caller's check job must be made again. `held` stands for `session_jobs` of the caller."""
    import sessionreport
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    root = None
    if sid is None:
        sid, root = own_session(registry)
    if held is None:
        held = session_jobs(sid, root, now) if sid else {}
    if job_created:
        if not sid:
            err.write("check: no hook names the session this runs in\n")
            return 2
        if not held:
            err.write("check: this session holds no recurring job: make the check job with "
                      "CronCreate first, then run check --job-created\n")
            return 2
        if not dry_run:
            record_job(sid, max(held, key=lambda j: held[j]), now)
    sort_ai_tickets(registry, dry_run, err)
    sessionreport.report(since_last=True, if_any=True, registry=registry, out=out,
                         save=not dry_run)
    list_notices(err)
    if job_created:
        return 0
    why = stale_job(sid, held, now) if sid else ""
    if why:
        err.write("check: %s: make the check job again with CronCreate, with the same cron "
                  "and prompt, delete every other job that runs check with CronDelete, then "
                  "run check --job-created\n" % why)
        return RENEW
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="octopus check",
                                 description="the manager's 10-minute check: print the "
                                             "update while any agent finished, waits or "
                                             "works. It launches nothing")
    ap.add_argument("--job-created", action="store_true",
                    help="this session just made its check job: record its newest "
                         "recurring job as the check job, and when")
    ap.add_argument("--dry-run", action="store_true",
                    help="move no ticket, record no job, and leave the snapshot of the "
                         "last update as it was")
    a = ap.parse_args(argv)
    return run(job_created=a.job_created, dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
