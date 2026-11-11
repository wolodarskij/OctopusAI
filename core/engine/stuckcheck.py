"""stuckcheck.py - the manager's hourly evaluation: which running ticket is stuck.

    python octopus.py stuck [--json]
    python octopus.py stuck --switch on|off

The evaluation reads every registered project with no model, and reports only what is stuck.

A RUNNING TICKET is a ticket whose session is open, and that the owner did not set aside: not
paused, not `blocked by user`, not `cancelled`. Only a running ticket, and a ticket in the
`blocked-by:` chain of a running ticket, is ever named. A ticket that does not run and blocks
no running ticket is never named.

STUCK means only: the work waits for something that cannot happen without the owner's
intervention, or cannot happen at all, or the chain is circular. A running ticket is stuck when
- its session ended its turn and waits for nothing for `QUIET` seconds or more, while the
  ticket does not show its work done: nothing starts its next turn;
- it is `blocked by outside cause`: the cause goes only by the owner's intervention;
- it waits in the blocked lane for blockers that all landed, and its session holds no job,
  subagent or turn that resumes it;
- a ticket in its `blocked-by:` chain does not exist, was cancelled, was paused or
  `blocked by user` by the owner, is `blocked by outside cause`, sits in the start or the AI
  lane with no session, or sits in the active lane with no session: nothing works it;
- its `blocked-by:` chain is circular.
A session at a dialog or one that waits for the owner's answer is no finding: the manager's
update shows it already.

Each finding is one short line: `<project> <id> "<title>" - <why it is stuck>`. A finding the
manager can act on also carries a command: the next step to the session, or the launch of the
blocker. `report` prints the lines on `out` and the commands on `err`, and nothing at all when
nothing is stuck.

THE SWITCH. The engine types the evaluation into the manager once an hour, only when
`evaluate` finds something (`managercheck.wake_stuck`), and only while the switch is on. The
switch is off by default, and only the owner's order turns it on: `stuck --switch on`. It
lives outside every repository (`SWITCH_FILE` beside the registry).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, HERE.parent / "win", HERE.parent / "hooks", HERE.parent / "posix"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import project  # noqa: E402

# How long a session waits for nothing after its turn before it is stuck.
QUIET = 30 * 60
HEADING = "Stuck"
DASH = "—"
TITLE_WIDTH = 60
CAUSE_WIDTH = 60
# The time stamp before a cause under `## Blocked`.
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\s*")
SWITCH_FILE = "stuck-check.json"
# The session states in which a job, a subagent or a turn of the session still runs.
BUSY = ("running", "subagent", "blocker")


# ── the switch ─────────────────────────────────────────────────────────────

def switched_on():
    """Whether the engine types the hourly evaluation into the manager. Off by default."""
    try:
        data = json.loads((project.home() / SWITCH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("on") is True


def switch(on):
    """Turn the hourly evaluation on or off."""
    path = project.home() / SWITCH_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    tmp.write_text(json.dumps({"on": bool(on)}) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


# ── the findings ───────────────────────────────────────────────────────────

def _cmd(name, rest):
    return 'python "%s" todo --project "%s" %s' % (
        (project.OCTOPUS / "octopus.py").as_posix(), name, rest)


def _cut(text, width):
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[:width - 3].rstrip() + "..."


def _send(name, tid, mode, text):
    return _cmd(name, 'send %s --mode %s --text "%s" --keys Enter' % (tid, mode, text))


def _launch(name, t, why):
    mode = "plan" if t.difficulty == "unknown" else "work"
    return _cmd(name, '%s %s --note "%s"' % (mode, t.id, why))


def _finding(name, t, text, command=""):
    return {"project": name, "id": t.id, "title": t.title, "text": text, "command": command}


def _cause(T, t):
    import sessionreport
    found = sessionreport._section(t.body, "Blocked")
    text = (found[-1] if found else "") if isinstance(found, list) else found
    for ln in text.splitlines():
        ln = _STAMP.sub("", ln.strip().lstrip("-* "))
        if ln:
            return _cut(ln, CAUSE_WIDTH)
    return ""


def set_aside(T, t):
    """Whether the owner set ticket `t` aside: paused, `blocked by user`, or cancelled."""
    return (t.state == T.P.PAUSED
            or (t.state == T.lane("blocked") and t.status == T.S("blocked by user"))
            or (t.state == T.lane("closed") and t.status == T.S("cancelled")))


def _next_step(tid):
    return ("Your turn ended, and nothing starts your next one. Continue %s from where it "
            "stopped and carry out its order to the end." % tid)


def _landed_step(T, tid):
    return ("Every ticket in the blocked-by line of %s is in %s or %s. Run python "
            ".octopus/todo.py move %s %s --status %s and continue the ticket from where it "
            "stopped." % (tid, T.lane("verify"), T.lane("closed"), tid, T.lane("active"),
                          T.S("implementing")))


def session_finding(name, T, s, t, now):
    """The finding of the open session `s` of running ticket `t`, or None: its turn ended,
    it waits for nothing, and its ticket does not show its work done."""
    if s.get("dialog") or T.state_word(s) not in ("idle", "finished"):
        return None
    if not s.get("claude") or T.session_ended(s):
        return None
    mode = s.get("mode") or "work"
    last = max(float(s.get("updated") or 0), float(s.get("idle_since") or 0))
    if not last or now - last < QUIET or not T.finish_undone(t, mode):
        return None
    return _finding(name, t, "its session waits for nothing",
                    _send(name, t.id, mode, _next_step(t.id)))


def chain_findings(name, T, t, by_id, live):
    """The findings of the `blocked-by:` chain of running ticket `t`. `live` maps a ticket id
    to its open sessions."""
    out = []
    settled = (T.lane("verify"), T.lane("closed"))

    def walk(x, path):
        for b in x.blocked_by:
            if b in path:
                out.append(_finding(name, t, "circular dependency: %s"
                                    % " -> ".join(path + [b])))
                continue
            bt = by_id.get(b)
            if bt is None:
                out.append(_finding(name, t, "waits for %s, which does not exist" % b))
            elif bt.state == T.lane("closed") and bt.status == T.S("cancelled"):
                out.append(_finding(name, t, "waits for %s, which is cancelled" % b))
            elif bt.state in settled or live.get(b):
                continue
            elif bt.state == T.P.PAUSED:
                out.append(_finding(name, t, "waits for %s, which is paused" % b))
            elif bt.state == T.lane("blocked") and bt.status == T.S("blocked by user"):
                out.append(_finding(name, t, "waits for %s, which you blocked" % b))
            elif bt.state == T.lane("blocked") and bt.status == T.S("blocked by outside cause"):
                cause = _cause(T, bt)
                out.append(_finding(name, t, "waits for %s, blocked by an outside cause%s"
                                    % (b, (": " + cause) if cause else "")))
            elif bt.state == T.lane("blocked") and bt.blocked_by:
                walk(bt, path + [b])
            elif bt.state in (T.lane("start"), T.lane("ai")):
                out.append(_finding(name, t, "waits for %s, which nobody works" % b,
                                    _launch(name, bt, "%s waits for it" % t.id)))
            elif bt.state == T.lane("active"):
                out.append(_finding(name, t, "waits for %s, which no agent runs" % b,
                                    _cmd(name, 'work %s --note "%s waits for it"'
                                         % (b, t.id))))
    walk(t, [t.id])
    return out


def project_findings(name, T, now):
    """The findings of the current project."""
    tickets = T.load_all()
    by_id = {t.id: t for t in tickets}
    live = {}
    for s in T.live_sessions(tickets):
        t = by_id.get(s.get("id"))
        if t is None or not t.id or t.repeat == T.MANAGER or T._is_own_session(s):
            continue
        live.setdefault(t.id, []).append(s)
    found = []
    for tid, sessions in live.items():
        t = by_id[tid]
        if set_aside(T, t) or t.state in (T.lane("verify"), T.lane("closed")):
            continue
        for s in sessions:
            f = session_finding(name, T, s, t, now)
            if f:
                found.append(f)
                break
        if t.state == T.lane("blocked") and t.status == T.S("blocked by outside cause"):
            cause = _cause(T, t)
            found.append(_finding(name, t, "blocked by an outside cause%s"
                                  % ((": " + cause) if cause else "")))
            continue
        waiting = t.state == T.lane("blocked") and t.status == T.S("blocked by other task")
        if waiting and t.blocked_by and all(
                b in by_id and by_id[b].state in (T.lane("verify"), T.lane("closed"))
                and by_id[b].status != T.S("cancelled") for b in t.blocked_by):
            s = next((x for x in sessions if not x.get("dialog")), None)
            if s is not None and not any(T.state_word(x) in BUSY for x in sessions):
                found.append(_finding(name, t, "its blockers landed, and nothing resumes it",
                                      _send(name, tid, s.get("mode") or "work",
                                            _landed_step(T, tid))))
            continue
        found += chain_findings(name, T, t, by_id, live)
    out, seen = [], set()
    for f in found:
        key = (f["id"], f["text"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def evaluate(registry=None, now=None):
    """Every finding of every registered project, in the order of the registry. A project
    whose tickets or sessions cannot be read gives none."""
    import todo as T
    now = time.time() if now is None else now
    findings = []
    for r in (project.load_registry() if registry is None else registry):
        try:
            tok = project.use(Path(r["path"]))
        except Exception:                               # noqa: BLE001 - an unreachable project
            continue
        try:
            findings += project_findings(r["name"], T, now)
        except Exception as exc:                        # noqa: BLE001 - the evaluation goes on
            sys.stderr.write("stuck: %s cannot be read: %s: %s\n"
                             % (r["name"], type(exc).__name__, exc))
        finally:
            project.reset(tok)
    return findings


# ── the report ─────────────────────────────────────────────────────────────

def line(f):
    return '%s %s "%s" %s %s' % (f["project"], f["id"], _cut(f["title"], TITLE_WIDTH), DASH,
                                 f["text"])


def render(findings):
    """The findings as Markdown, one line each, or "" when nothing is stuck."""
    if not findings:
        return ""
    return "\n".join(["**%s**" % HEADING] + ["- %s" % line(f) for f in findings]) + "\n"


def report(registry=None, as_json=False, out=None, err=None, now=None):
    """Print the findings on `out`, and the commands the manager runs on `err`. Prints
    nothing when nothing is stuck. Returns 0."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    findings = evaluate(registry, now)
    if as_json:
        out.write(json.dumps(findings, indent=1) + "\n")
        return 0
    out.write(render(findings))
    cmds = []
    for f in findings:
        if f["command"] and f["command"] not in cmds:
            cmds.append(f["command"])
    if cmds:
        err.write("stuck: run each command below, and write nothing about it:\n")
        for c in cmds:
            err.write("  %s\n" % c)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="octopus stuck",
                                 description="the manager's hourly evaluation: the running "
                                             "tickets that are stuck, in every registered "
                                             "project. It launches nothing")
    ap.add_argument("--json", action="store_true", help="the findings as JSON")
    ap.add_argument("--switch", choices=("on", "off"),
                    help="turn the hourly evaluation on or off; only on the owner's order. "
                         "It is off by default")
    a = ap.parse_args(argv)
    if a.switch:
        switch(a.switch == "on")
        print("stuck: the hourly evaluation is %s" % a.switch)
        return 0
    return report(as_json=a.json)


if __name__ == "__main__":
    sys.exit(main())
