"""sessionreport.py - the manager's update: every agent of every registered project.

    python octopus.py sessions [--since-last] [--if-any] [--json]

THE FORMAT. Every update of the manager lists agents (sessions), not tickets, in four
sections, always all four and in this order, an empty one as `none`. Each agent is one line,
and the manager copies the lines and never summarizes a ticket:

    **Finished since the last update**
    - <project> <id> "<title>" — <outcome>; <lane> / <status>; <commit> pushed|not pushed
    **Waiting for your input**
    - <project> <id> "<title>" — <the question, word for word>
    **Working**
    - <project> <id> "<title>" — <what it does now, at most 10 words>
    **Other**
    - <project> <id> "<title>" — <what it waits for: a blocker, an event, idle, stuck>

The outcome is the ticket's `outcome:` line, which its `work` or `verify` session wrote
(`todo.outcome_problem`); a ticket without one leaves it out. The commit is the newest commit
whose message starts with `<id>:`, and `pushed` when a remote branch holds it; `no commit`
when none does, nothing in a project without git. A session with no ticket is
`<project> <session> (<mode>)`. Waiting for your input holds only an open session whose agent
runs and whose state is `input`; a finished or closed one is under Finished, whatever its
ticket's result names for the owner. A session the engine calls `input` whose ticket waits for
other tickets, or that still holds a background task or a scheduled job, is under Other. The
manager sessions and the session that runs the command are left out.

SINCE THE LAST CALL. `--since-last` reads the snapshot of the last call
(`sessions-snapshot.json` beside the registry, outside every repository) and writes it again.
Finished then holds each session that finished since that call: one finished now that was
not finished then, and one that was in the snapshot unfinished and whose terminal is gone,
since a finished session closes its terminal at once. A session gone with its work undone
(`todo.finish_undone`) is under Other. Without `--since-last`, Finished holds the sessions
that are finished and still open, and no snapshot is read or written.

ONLY WHEN AN AGENT IS THERE. `--if-any` is `--since-last` that prints nothing only when the
update has no row: no agent finished since that call, none waits for the owner, none works and
none waits for anything else. Otherwise it prints all four sections, every run, and a waiting
question again on every run until it is answered. The manager's 10-minute check runs it
(`managercheck.py`). No output ever says that nothing changed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, HERE.parent / "win", HERE.parent / "hooks", HERE.parent / "posix"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import project  # noqa: E402

SNAPSHOT = "sessions-snapshot.json"
# (key, heading) of each section, in the order of the report.
SECTIONS = (("finished", "Finished since the last update"),
            ("input", "Waiting for your input"),
            ("working", "Working"),
            ("other", "Other"))
TEXT_WIDTH = 200
# The most words of what a working agent does now.
DOING_WORDS = 10
DASH = "—"


def snapshot_file():
    return project.home() / SNAPSHOT


def load_snapshot():
    """`{key: row}` of the last `--since-last` call, {} when there is none."""
    try:
        data = json.loads(snapshot_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_snapshot(rows):
    keep = ("project", "session", "id", "mode", "title", "section", "state")
    data = {r["key"]: {k: r.get(k) for k in keep} for r in rows if r.get("session")}
    f = snapshot_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _cut(text, width):
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[:width - 3].rstrip() + "..."


def result_line(text):
    """The first line of a last message that says something: no finished line, no heading
    marks, no table rule."""
    import session_state
    for ln in (text or "").splitlines():
        ln = re.sub(r"^[#>*\-\s|]+|[*`|]+$", "", ln).replace("**", "").strip()
        if not ln or ln in session_state.FINISHED_LINES or set(ln) <= set("-:| "):
            continue
        return _cut(ln, TEXT_WIDTH)
    return ""


def _last_result(T, sid):
    import session_state
    try:
        text = session_state.transcript_tail(T.session_transcript(sid))[0]
    except Exception:                                   # noqa: BLE001 - no transcript, no line
        return ""
    return result_line(text)


def commit_of(root, tid, kind):
    """`<hash> pushed`, `<hash> not pushed` or `no commit` for the newest commit of ticket
    `tid` in a git project; "" in a project without git."""
    if kind != "git" or not tid:
        return ""
    import vcs
    st = vcs._git(root, "log", "-1", "--format=%h", "--grep", "^%s:" % tid, timeout=30)
    h = st.out.strip().splitlines()[0].strip() if st.rc == 0 and st.out.strip() else ""
    if not h:
        return "no commit"
    st = vcs._git(root, "branch", "-r", "--contains", h, timeout=30)
    return "%s %s" % (h, "pushed" if st.rc == 0 and st.out.strip() else "not pushed")


def _words(text, n):
    words = str(text or "").split()
    return " ".join(words[:n]) + ("..." if len(words) > n else "")


def _where(T, t):
    return "%s / %s" % (T.P.STATE_LABEL.get(t.state, t.state), t.status) if t else ""


def _question(s):
    q = (s.get("reported_why") or s.get("question")
         or (s.get("judge") == "input" and s.get("judge_why")) or s.get("why") or "")
    return q or "a question, not named"


def finished_text(T, t, fallback=""):
    """`<outcome>; <lane> / <status>; <commit>` of a finished agent's ticket, the parts it
    has; `fallback` when it has no ticket."""
    if t is None:
        return fallback
    return "; ".join(x for x in (t.meta.get(T.OUTCOME_FIELD, ""), _where(T, t),
                                 commit_of(T.P.ROOT, t.id, T.P.vcs)) if x)


def classify(T, s, t):
    """`(section, text)` of one live session: `t` is its ticket or None."""
    word = T.state_word(s)
    why = s.get("why") or ""
    if word == "finished":
        res = _last_result(T, s["session"]) if s.get("session") and t is None else ""
        return "finished", finished_text(T, t, res or why)
    if word == "running":
        return "working", _words(s.get("doing") or "its turn runs", DOING_WORDS)
    if word == "subagent":
        return "working", _words(why or "its own work", DOING_WORDS)
    if word == "input":
        # Only an open session whose agent still runs waits for the owner.
        if not s.get("claude"):
            return "finished", finished_text(T, t, why)
        if T._waits_on_tickets(s, t) or s.get("tasks") or s.get("jobs"):
            return "other", why
        return "input", _question(s)
    return "other", ("%s: %s" % (word, why) if why else word)


def agent_cell(r):
    """`<project> <id> "<title>"`, or `<project> <session> (<mode>)` for a session with no
    ticket."""
    if r.get("id"):
        return '%s %s "%s"' % (r["project"], r["id"], " ".join(str(r.get("title") or "").split()))
    return "%s %s (%s)" % (r["project"], (r.get("session") or "")[:8] or "-",
                           r.get("mode") or "-")


def _row(name, T, s, t):
    title = t.title if t else (s.get("goal") or s.get("title") or "")
    section, text = classify(T, s, t)
    return {"key": "%s|%s" % (name, s["session"]), "project": name, "session": s["session"],
            "id": s.get("id"), "title": title, "mode": s.get("mode") or "",
            "state": T.state_word(s), "lane": _where(T, t), "section": section,
            # A question is repeated word for word, never cut.
            "text": " ".join(str(text or "").split()) if section == "input"
            else _cut(text, TEXT_WIDTH)}


def _gone_row(name, T, e, t):
    """The row of a session in the snapshot whose terminal is gone."""
    row = dict(e, key="%s|%s" % (name, e["session"]), state="gone", lane=_where(T, t))
    undone = T.finish_undone(t, e.get("mode") or "") if t is not None else ""
    if undone:
        row.update(section="other", text="its terminal closed with the work undone: %s"
                   % _where(T, t))
        return row
    res = _last_result(T, e["session"]) if t is None else ""
    row.update(section="finished",
               text=_cut(finished_text(T, t, res) or "its terminal closed", TEXT_WIDTH))
    return row


def collect(since_last=False, registry=None):
    """The rows of the report, and the rows of the live sessions for the next snapshot."""
    import todo as T
    prev = load_snapshot() if since_last else {}
    rows, live = [], []
    for r in (project.load_registry() if registry is None else registry):
        name = r["name"]
        try:
            tok = project.use(Path(r["path"]))
        except Exception as exc:                        # noqa: BLE001 - an unreachable project
            rows.append({"key": name, "project": name, "id": None, "title": "", "mode": "-",
                         "section": "other", "text": _cut("its sessions cannot be read: %s"
                                                          % exc, TEXT_WIDTH)})
            continue
        try:
            tickets = T.load_all()
            by_id = {t.id: t for t in tickets}
            here = []
            for s in T.live_sessions(tickets):
                t = by_id.get(s.get("id"))
                if (t is not None and t.repeat == T.MANAGER) or T._is_own_session(s):
                    continue
                here.append(_row(name, T, s, t))
            seen = {x["key"] for x in here}
            for key, e in prev.items():
                if e.get("project") == name and key not in seen \
                        and e.get("section") != "finished" and e.get("session"):
                    rows.append(_gone_row(name, T, e, by_id.get(e.get("id"))))
            live += here
        except Exception as exc:                        # noqa: BLE001 - the report goes on
            rows.append({"key": name, "project": name, "id": None, "title": "", "mode": "-",
                         "section": "other", "text": _cut("its sessions cannot be read: %s"
                                                          % exc, TEXT_WIDTH)})
        finally:
            project.reset(tok)
    for x in live:
        # A finished session was reported in the update where it finished, once.
        if x["section"] == "finished" and (prev.get(x["key"]) or {}).get("section") == "finished":
            continue
        rows.append(x)
    return rows, live


def line(r):
    """The one line of an agent: `<agent> - <text>`, with an em dash."""
    return "%s %s %s" % (agent_cell(r), DASH, r.get("text") or "-")


def render(rows):
    """The four sections as Markdown, every section present, an empty one as `none`, one
    line per agent."""
    out = []
    for key, heading in SECTIONS:
        mine = sorted((r for r in rows if r["section"] == key),
                      key=lambda r: (r["project"], r.get("id") or "", r.get("mode") or ""))
        out.append("**%s**" % heading)
        if not mine:
            out.append("none")
        for r in mine:
            out.append("- " + line(r))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="octopus sessions",
                                 description="the manager's update: every agent of every "
                                             "registered project, in four sections")
    ap.add_argument("--since-last", action="store_true",
                    help="Finished holds what finished since the last --since-last call, "
                         "and the snapshot is written again")
    ap.add_argument("--if-any", action="store_true",
                    help="--since-last that prints nothing only when no agent finished "
                         "since the last call, none waits and none works")
    ap.add_argument("--json", action="store_true", help="the rows, as JSON")
    a = ap.parse_args(argv)
    return report(since_last=a.since_last or a.if_any, if_any=a.if_any, as_json=a.json)


def report(since_last=False, if_any=False, as_json=False, registry=None, out=None,
           save=True):
    """Print the update. With `if_any`, print nothing when it has no row. `save` False
    leaves the snapshot as it was. Returns 0."""
    out = sys.stdout if out is None else out
    rows, live = collect(since_last, registry)
    if since_last and save:
        save_snapshot(live)
    if if_any and not rows:
        return 0
    if as_json:
        out.write(json.dumps(rows, indent=1) + "\n")
    else:
        out.write(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
