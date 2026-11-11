"""stuckcheck.py - the manager's hourly evaluation: is a running ticket or a blocker stuck.

    python octopus.py stuck [--json]

The evaluation reads every registered project with no model, and finds:

1. A STUCK AGENT: a live session of a ticket that
   - waits at a dialog for `QUIET` seconds or more: only the owner answers it;
   - ended its turn and waits for nothing (`idle`), or is `finished` while its Claude runs
     and its ticket's order is not carried out to the end, for `QUIET` seconds or more: the
     manager sends it its next step;
   - runs or works and wrote nothing to its transcript for `NO_PROGRESS` seconds or more:
     the manager sends it its next step.
2. A TICKET WITH NO AGENT: a ticket in the active lane as `implementing`, `planning` or
   `investigating` that no live session runs: the owner decides.
3. A STUCK CHAIN: the `blocked-by:` chain of a ticket that a live session runs, that is in
   the active lane, or that is blocked as `blocked by other task`. A blocker
   - in the start or the AI lane with no session is not worked: the manager launches it,
     `work`, or `plan` when its difficulty is `unknown`;
   - in the active lane with no session, blocked by an outside cause or by the owner,
     paused, cancelled, or missing from the project waits on something that will not come
     by itself: the owner decides;
   - that closes a cycle: the owner decides.
   A ticket in the blocked lane as `blocked by other task` whose blockers all landed in the
   verify or the closed lane still waits: the manager sends its session its next step, or
   launches `work` on it when no session runs.

Each finding is one line for the owner: `<project> <id> "<title>" - <what is stuck>`, with a
sub-line that says who acts. A finding the manager acts on carries the command it runs.
`report` prints the lines on `out` and the commands on `err`, and nothing at all when
nothing is stuck. The engine types the evaluation into the manager once an hour, only when
`evaluate` finds something (`managercheck.wake_stuck`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, HERE.parent / "win", HERE.parent / "hooks", HERE.parent / "posix"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import project  # noqa: E402

# How long a session waits at a dialog, or waits for nothing after its turn, before it is stuck.
QUIET = 30 * 60
# How long a running or working session writes nothing to its transcript before it is stuck.
NO_PROGRESS = 60 * 60
HEADING = "Stuck agents and blockers"
DASH = "—"
OWNER = "only you can decide it"
MANAGER = "the manager acts on it"


def _cmd(name, rest):
    return 'python "%s" todo --project "%s" %s' % (
        (project.OCTOPUS / "octopus.py").as_posix(), name, rest)


def _minutes(span):
    return "%d minutes" % max(1, int(span // 60))


def _send(name, tid, mode, text):
    return _cmd(name, 'send %s --mode %s --text "%s" --keys Enter' % (tid, mode, text))


def _launch(name, T, t, why):
    mode = "plan" if t.difficulty == "unknown" else "work"
    return _cmd(name, '%s %s --note "%s"' % (mode, t.id, why))


def _finding(name, t, text, command=""):
    return {"project": name, "id": t.id if t else "", "title": t.title if t else "",
            "text": text, "command": command}


def _next_step(T, tid):
    return ("The manager's hourly check finds you stuck. Continue %s from where it stopped "
            "and carry out its order to the end. When an outside cause stops the work, run "
            "python .octopus/todo.py report %s blocked. When you need the owner, run "
            "python .octopus/todo.py report %s input and ask with the question dialog."
            % (tid, tid, tid))


def _landed_step(T, tid):
    return ("Every ticket in the blocked-by line of %s is in %s or %s. Run python "
            ".octopus/todo.py move %s %s --status %s and continue the ticket from where it "
            "stopped." % (tid, T.lane("verify"), T.lane("closed"), tid, T.lane("active"),
                          T.S("implementing")))


def agent_findings(name, T, s, t, now):
    """The findings of the live session `s` of ticket `t`."""
    import sessionreport
    mode = s.get("mode") or "work"
    word = T.state_word(s)
    last = max(float(s.get("updated") or 0), float(s.get("idle_since") or 0)) \
        or float(s.get("created") or 0)
    quiet = now - last if last else 0
    if s.get("dialog"):
        if quiet >= QUIET:
            return [_finding(name, t, "a dialog waits for %s: %s" % (
                _minutes(quiet), sessionreport._question(s)))]
        return []
    if word == "idle" and quiet >= QUIET:
        return [_finding(name, t, "its turn ended %s ago, and it waits for nothing"
                         % _minutes(quiet), _send(name, t.id, mode, _next_step(T, t.id)))]
    if word == "finished" and s.get("claude") and not T.session_ended(s) and quiet >= QUIET:
        undone = T.finish_undone(t, mode)
        if undone:
            return [_finding(name, t, "its turn ended %s ago, and its order is not carried "
                             "out: %s" % (_minutes(quiet), undone),
                             _send(name, t.id, mode, _next_step(T, t.id)))]
    if word in ("running", "subagent") and s.get("updated") \
            and now - float(s["updated"]) >= NO_PROGRESS:
        span = now - float(s["updated"])
        doing = s.get("doing") or s.get("why") or "its turn runs"
        return [_finding(name, t, "no progress for %s: %s" % (_minutes(span), doing),
                         _send(name, t.id, mode, _next_step(T, t.id)))]
    return []


def chain_findings(name, T, t, by_id, live):
    """The findings of the `blocked-by:` chain of ticket `t`. `live` maps a ticket id to its
    live sessions."""
    settled = (T.lane("verify"), T.lane("closed"))
    out = []

    def walk(x, path):
        for b in x.blocked_by:
            waits = "the chain %s waits for it" % " -> ".join(path + [b])
            if b in path:
                out.append(("cycle:" + ",".join(sorted(path)), _finding(
                    name, t, "its blocked-by chain is a cycle: %s -> %s"
                    % (" -> ".join(path), b))))
                continue
            bt = by_id.get(b)
            if bt is None:
                out.append((b, _finding(name, t, "it waits for %s, which this project does "
                                                 "not hold" % b)))
                continue
            if bt.state == T.lane("closed"):
                if bt.status == T.S("cancelled"):
                    out.append((b, _finding(name, bt, "cancelled, and %s: it never lands"
                                            % waits)))
                continue
            if bt.state in settled or live.get(b):
                continue
            if bt.state in (T.lane("start"), T.lane("ai")):
                out.append((b, _finding(name, bt, "nobody works it, and %s; it sits in %s "
                                        "with no session" % (waits, bt.state),
                                        _launch(name, T, bt, "%s waits for it" % path[-1]))))
            elif bt.state == T.lane("active"):
                out.append((b, _finding(name, bt, "no agent runs it in %s / %s, and %s"
                                        % (bt.state, bt.status, waits))))
            elif bt.state == T.P.PAUSED:
                out.append((b, _finding(name, bt, "the owner paused it, and %s" % waits)))
            elif bt.state == T.lane("blocked"):
                if bt.status == T.S("blocked by other task") and bt.blocked_by:
                    walk(bt, path + [b])
                    continue
                cause = _first_line(bt.body, "Blocked")
                out.append((b, _finding(name, bt, "%s%s, and %s" % (
                    bt.status, (": " + cause) if cause else "", waits))))
    walk(t, [t.id])
    return out


def _first_line(body, heading):
    import sessionreport
    for ln in sessionreport._section(body, heading).splitlines():
        if ln.strip():
            return " ".join(ln.strip().lstrip("-* ").split())[:160]
    return ""


def project_findings(name, T, now):
    """The findings of the current project."""
    tickets = T.load_all()
    by_id = {t.id: t for t in tickets}
    live = {}
    found = []
    for s in T.live_sessions(tickets):
        t = by_id.get(s.get("id"))
        if t is None or t.repeat == T.MANAGER or T._is_own_session(s):
            continue
        live.setdefault(t.id, []).append(s)
        found += [(t.id, f) for f in agent_findings(name, T, s, t, now)]
    running = (T.S("implementing"), T.S("planning"), T.S("investigating"))
    settled = (T.lane("verify"), T.lane("closed"))
    for t in tickets:
        if not t.id or t.repeat == T.MANAGER or t.state in settled or t.state == T.P.PAUSED:
            continue
        if t.state == T.lane("active") and t.status in running and not live.get(t.id):
            found.append((t.id, _finding(name, t, "no agent runs it in %s / %s"
                                         % (t.state, t.status))))
        waiting = t.state == T.lane("blocked") and t.status == T.S("blocked by other task")
        if not (live.get(t.id) or t.state == T.lane("active") or waiting) or not t.blocked_by:
            continue
        if waiting and all(b in by_id and by_id[b].state in settled
                           and by_id[b].status != T.S("cancelled") for b in t.blocked_by):
            s = next(iter(live.get(t.id) or ()), None)
            if s is None:
                cmd = _cmd(name, 'work %s --note "Every ticket it waits for landed."' % t.id)
            elif s.get("dialog"):
                cmd = ""
            else:
                cmd = _send(name, t.id, s.get("mode") or "work", _landed_step(T, t.id))
            found.append((t.id, _finding(name, t, "every ticket it waits for landed, and it "
                                                  "still waits in %s" % t.state, cmd)))
            continue
        found += chain_findings(name, T, t, by_id, live)
    out, seen = [], set()
    for key, f in found:
        k = (key, f["text"])
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def evaluate(registry=None, now=None):
    """Every finding of every registered project, in the order of the registry."""
    import todo as T
    now = time.time() if now is None else now
    findings = []
    for r in (project.load_registry() if registry is None else registry):
        name = r["name"]
        try:
            tok = project.use(Path(r["path"]))
        except Exception:                               # noqa: BLE001 - an unreachable project
            continue
        try:
            findings += project_findings(name, T, now)
        except Exception as exc:                        # noqa: BLE001 - the evaluation goes on
            findings.append({"project": name, "id": "", "title": "", "command": "",
                             "text": "its tickets cannot be read: %s: %s"
                                     % (type(exc).__name__, exc)})
        finally:
            project.reset(tok)
    return findings


def line(f):
    cell = ('%s %s "%s"' % (f["project"], f["id"], " ".join(f["title"].split()))
            if f["id"] else f["project"])
    return "%s %s %s" % (cell, DASH, f["text"])


def render(findings):
    """The findings as Markdown for the owner, or "" when there is none."""
    if not findings:
        return ""
    rows = ["**%s**" % HEADING]
    for f in findings:
        rows.append("- %s" % line(f))
        rows.append("  - %s" % (MANAGER if f["command"] else OWNER))
    return "\n".join(rows) + "\n"


def report(registry=None, as_json=False, out=None, err=None, now=None):
    """Print the findings on `out`, and the commands the manager runs on `err`. Returns 0."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    findings = evaluate(registry, now)
    if as_json:
        out.write(json.dumps(findings, indent=1) + "\n")
        return 0
    out.write(render(findings))
    cmds = [f["command"] for f in findings if f["command"]]
    if cmds:
        err.write("stuck: run each command below, and write nothing about it:\n")
        for c in cmds:
            err.write("  %s\n" % c)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="octopus stuck",
                                 description="the manager's hourly evaluation: the running "
                                             "tickets and the blockers that are stuck, in "
                                             "every registered project. It launches nothing")
    ap.add_argument("--json", action="store_true", help="the findings as JSON")
    a = ap.parse_args(argv)
    return report(as_json=a.json)


if __name__ == "__main__":
    sys.exit(main())
