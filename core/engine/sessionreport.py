"""sessionreport.py - the manager's update: every agent of every registered project.

    python octopus.py sessions [--since-last] [--if-any] [--json]

THE FORMAT. Every update of the manager lists agents (sessions), not tickets, in five
sections, always all five and in this order, an empty one as `none`. Each agent is one line,
and the manager copies the lines and never summarizes a ticket:

    **Finished since the last update**
    - <project> <id> "<title>" — <outcome>; <lane> / <status>; <commit> pushed|not pushed
      - <a line of its `## Result`, when its order asks a question or an investigation>
      - render: <the absolute path of each render, when its ticket is visual work>
    **Waiting for your input**
    - <project> <id> "<title>" — <the question, word for word>
    **Working**
    - <project> <id> "<title>" — <what it does now, at most 10 words>
    **Blocked**
    - <project> <id> "<title>" — <the other ticket it waits for, or the outside cause>
    **Other**
    - <project> <id> "<title>" — <what it waits for: an event, idle, paused, stuck>

The outcome is the ticket's `outcome:` line, which its `work` or `verify` session wrote
(`todo.outcome_problem`); a ticket without one leaves it out. The commit is the newest commit
whose message starts with `<id>:`, and `pushed` when a remote branch holds it; `no commit`
when none does, nothing in a project without git. THE ANSWER: a finished agent whose ticket's
order (its title, its first section and its `## Owner's orders`) asks a question or an
investigation (`asks_answer`) carries the lines of the ticket's last `## Result` section, one
sub-line each, under its line (`answer_lines`). THE RENDERS: a finished agent whose ticket is
visual work on textures, 3D meshes or materials (`todo.visual_work`) carries one
`render: <path>` sub-line per render file that its `## Result` names (`render_lines`). A session with no
ticket, such as a chat or a session the owner opened, is no agent of the update. Waiting for your input holds only an open session whose agent
runs and whose state is `input`; a finished or closed one is under Finished, whatever its
ticket's result names for the owner, or under Blocked when its ticket's order is not
carried out to the end. Every open session the engine calls `input` is there,
also when its ticket waits for other tickets or it holds a background task or a scheduled
job. The manager sessions and the session that runs the command are left out.

THE BLOCKED SECTION. Blocked holds every agent whose ticket waits on another ticket or is
blocked by an outside cause, and that does not wait for the owner: a session in the state
`blocker`, a session whose ticket is in the blocked lane as `blocked by other task` or
`blocked by outside cause` (`ticket_blocked`), and a session that ended while its ticket's
order is not carried out to the end. Other holds the rest: an event, idle, paused, stuck.

SINCE THE LAST CALL. `--since-last` reads the snapshot of the last call
(`sessions-snapshot.json` beside the registry, outside every repository) and writes it again.
The snapshot holds the time of that call, its live sessions, and the launches it reported
as gone. Finished then holds each session that finished since that call: one finished now
that was not finished then, one that was in the snapshot unfinished and whose terminal is
gone, since a finished session closes its terminal at once, and one whose launch in its
ticket's `sessions:` is at or after that time and whose terminal is gone, so an agent that
started and finished between two calls is there too. A launch is reported once.

NEVER FINISHED WITH ITS ORDER UNMET. A session that ended (it said `WORK IS FINISHED`, its
Claude exited, or its terminal is gone; `todo.session_ended`) while its ticket does not show
its work done (`todo.finish_undone`) did not carry out its order to the end: it stands under
Blocked, never under Finished, and its ticket goes to the blocked lane as
`blocked by outside cause` (`todo.block_undone`). A session that is `finished` while its
Claude runs and that did not say the finished line did not end: with its ticket's work not
done, it stands under Other as `finished`, or under Blocked when its ticket is blocked,
and its ticket stays where it is. Without `--since-last`, Finished holds the sessions that are finished and still open,
and no snapshot is read or written.

QUESTIONS FOR THE MORNING. In night mode, from 00:00 to 08:00 local time, a session collects
a question that only the owner can answer under `## Questions for the morning` of its ticket
(`todo.py morning`). Outside night mode a sixth section follows Other, only when it holds a
row: each open question of an open ticket, word for word, on every update until the manager
records the owner's answer (`morning <id> --answer <n> --text "..."`):

    **Questions for the morning**
    - <project> <id> "<title>" — question <n>, <when>: <the question>

ONLY WHEN AN AGENT IS THERE. `--if-any` is `--since-last` that prints nothing only when the
update has no row: no agent finished since that call, none waits for the owner, none works and
none waits for anything else. Otherwise it prints all five sections, every run, and a waiting
question again on every run until it is answered. The manager's check runs it
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
            ("blocked", "Blocked"),
            ("other", "Other"),
            ("morning", "Questions for the morning"))
# The sections that stand only when they hold a row.
OPTIONAL = ("morning",)
TEXT_WIDTH = 200
# The most words of what a working agent does now.
DOING_WORDS = 10
DASH = "—"
# The most lines of a finished ticket's `## Result` that the update gives as its answer.
ANSWER_LINES = 30
# The text of an agent whose session ended while its ticket's order is not carried out.
BLOCKED_TEXT = "blocked: its session ended, and the ticket's order is not carried out to the end"
PAUSED_TEXT = "paused: the owner stopped its agent, and its work is not finished"
# What in an order asks a question or an investigation.
ASKS = re.compile(r"\?|\b(?:investigat\w*|analy[sz]\w*|finds? out|measur\w*|evaluat\w*|"
                  r"determin\w*|assess\w*|research\w*|compar\w*|root cause|whether|"
                  r"how (?:many|much|long|often|fast|big)|why)\b", re.I)


def snapshot_file():
    return project.home() / SNAPSHOT


def now_stamp():
    """The time now, in the format of a launch stamp in a ticket's `sessions:`."""
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def load_snapshot():
    """The last `--since-last` call: `{"time": <stamp or "">, "sessions": {key: row},
    "reported": {key: <launch stamp>}}`, every part empty when there is none. A snapshot of
    the old format, rows only, has no time."""
    try:
        data = json.loads(snapshot_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("sessions"), dict):
        data = {"sessions": {k: v for k, v in data.items() if isinstance(v, dict)}}
    reported = data.get("reported")
    return {"time": str(data.get("time") or ""), "sessions": data["sessions"],
            "reported": reported if isinstance(reported, dict) else {}}


def save_snapshot(rows, gone=(), since=None):
    """Write the snapshot: `since`, the time the call began (now when None), the live
    `rows`, and the launches of the `gone` rows, kept while a later call can still take
    them."""
    since = since or now_stamp()
    keep = ("project", "session", "id", "mode", "title", "section", "state")
    reported = dict(load_snapshot()["reported"])
    reported.update((r["key"], r["launched"]) for r in gone if r.get("launched"))
    data = {"time": since,
            "sessions": {r["key"]: {k: r.get(k) for k in keep} for r in rows
                         if r.get("session")},
            "reported": {k: v for k, v in reported.items() if str(v) >= since}}
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
    """`<ref> pushed`, `<ref> not pushed` or `no commit` for the newest commit of ticket
    `tid`; "" in a project without a VCS."""
    import vcs
    return vcs.commit_of(root, kind, tid)


def _words(text, n):
    words = str(text or "").split()
    return " ".join(words[:n]) + ("..." if len(words) > n else "")


def _where(T, t):
    return "%s / %s" % (T.P.STATE_LABEL.get(t.state, t.state), t.status) if t else ""


def _question(s):
    """The question a waiting session asks: an open dialog's, with its options, first."""
    q = ((s.get("dialog") and s.get("question")) or s.get("reported_why") or s.get("question")
         or (s.get("judge") == "input" and s.get("judge_why")) or s.get("why") or "")
    return q or "a question, not named"


def finished_text(T, t, fallback=""):
    """`<outcome>; <lane> / <status>; <commit>` of a finished agent's ticket, the parts it
    has; `fallback` when it has no ticket."""
    if t is None:
        return fallback
    return "; ".join(x for x in (t.meta.get(T.OUTCOME_FIELD, ""), _where(T, t),
                                 commit_of(T.P.ROOT, t.id, T.P.vcs)) if x)


def _section(body, heading):
    """The texts of every `## <heading>` section of a ticket body, a heading with a suffix
    such as `## Result (run 3)` included, in the order they stand."""
    return re.findall(r"^##[ \t]+%s\b[^\n]*\n(.*?)(?=^##\s|\Z)" % re.escape(heading),
                      body or "", re.M | re.S)


def asks_answer(T, t):
    """Whether the order of ticket `t` asks a question or an investigation: its title, its
    first section or its `## Owner's orders` hold a question mark or a word of `ASKS`."""
    text = "\n".join([t.title, T.first_section(t.body)] + _section(t.body, "Owner's orders"))
    return bool(ASKS.search(text))


def answer_lines(T, t):
    """The lines of the last `## Result` section of ticket `t`, without list marks and table
    rules, when its order asks a question or an investigation; else []. At most
    `ANSWER_LINES`; a longer section ends with a line that says how many more it holds."""
    if t is None or not asks_answer(T, t):
        return []
    found = _section(t.body, "Result")
    lines = []
    for ln in (found[-1] if found else "").splitlines():
        ln = re.sub(r"^\s*(?:#+|[-*+]|\d+[.)])\s+", "", ln).strip()
        if not ln or ln.startswith("```") or set(ln) <= set("-:| "):
            continue
        lines.append(" ".join(ln.split()))
    if len(lines) > ANSWER_LINES:
        more = len(lines) - ANSWER_LINES + 1
        lines = lines[:ANSWER_LINES - 1] + ["... %d more lines in %s's ## Result" % (more, t.id)]
    return lines


def render_lines(T, t):
    """One `render: <absolute path>` line per render that the result of ticket `t` names,
    when `t` is visual work (`todo.visual_work`); else []."""
    if t is None or not T.visual_work(t):
        return []
    out = []
    for rel in T.renders_of(t):
        p = Path(rel)
        out.append("render: %s" % (p if p.is_absolute() else T.P.ROOT / rel).as_posix())
    return out


def finished_lines(T, t):
    """The sub-lines of a finished agent: its answer, then its renders."""
    return answer_lines(T, t) + render_lines(T, t)


def ticket_blocked(T, t):
    """Whether ticket `t` is in the blocked lane as `blocked by other task` or `blocked by
    outside cause`."""
    return t is not None and t.state == T.lane("blocked") and t.status in (
        T.S("blocked by other task"), T.S("blocked by outside cause"))


def _waits(T, word, t):
    """The section of a live session that neither works nor waits for the owner: `blocked`
    when its state is `blocker` or its ticket is blocked (`ticket_blocked`), else `other`."""
    return "blocked" if word == "blocker" or ticket_blocked(T, t) else "other"


def _ended(T, t, mode, sid, shell, text):
    """`(section, text)` of an agent whose session ended. Its ticket's order not carried out
    to the end (`todo.finish_undone`): `blocked`, and the ticket goes to the blocked lane
    (`todo.block_undone`). A ticket the owner paused: `other`, as paused. Else `finished`
    with `text`."""
    if t is not None and t.state == T.P.PAUSED:
        return "other", "; ".join(x for x in (PAUSED_TEXT, _where(T, t)) if x)
    undone = T.block_undone(t, mode, sid, shell) if t is not None else ""
    if not undone:
        return "finished", text
    t = T.find(T.load_all(), t.id) or t
    return "blocked", "; ".join(x for x in (BLOCKED_TEXT, _where(T, t),
                                          commit_of(T.P.ROOT, t.id, T.P.vcs)) if x)


def classify(T, s, t):
    """`(section, text)` of one live session: `t` is its ticket or None."""
    word = T.state_word(s)
    why = s.get("why") or ""
    if word == "finished":
        res = _last_result(T, s["session"]) if s.get("session") and t is None else ""
        if not T.session_ended(s) and T.finish_undone(t, s.get("mode") or ""):
            # Its Claude runs and it did not say the finished line: it did not end, so its
            # ticket stays where it is.
            return _waits(T, word, t), "; ".join(x for x in ("%s: %s" % (word, why) if why else word,
                                                  _where(T, t)) if x)
        return _ended(T, t, s.get("mode") or "", s.get("session") or "", s.get("shell") or 0,
                      finished_text(T, t, res or why))
    if word == "running":
        return "working", _words(s.get("doing") or "its turn runs", DOING_WORDS)
    if word == "subagent":
        return "working", _words(why or "its own work", DOING_WORDS)
    if word == "input":
        # Only an open session whose agent still runs waits for the owner.
        if not s.get("claude"):
            return _ended(T, t, s.get("mode") or "", s.get("session") or "",
                          s.get("shell") or 0, finished_text(T, t, why))
        return "input", _question(s)
    return _waits(T, word, t), ("%s: %s" % (word, why) if why else word)


def agent_cell(r):
    """`<project> <id> "<title>"`, or `<project> <session> (<mode>)` for a row with no
    ticket: a project whose sessions cannot be read."""
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
            else _cut(text, TEXT_WIDTH),
            "answer": finished_lines(T, t) if section == "finished" else []}


def _gone_row(name, T, e, t):
    """The row of a session in the snapshot whose terminal is gone."""
    row = dict(e, key="%s|%s" % (name, e["session"]), state="gone", lane=_where(T, t))
    res = _last_result(T, e["session"]) if t is None else ""
    section, text = _ended(T, t, e.get("mode") or "", e.get("session") or "", 0,
                           finished_text(T, t, res) or "its terminal closed")
    row.update(section=section, text=_cut(text, TEXT_WIDTH),
               answer=finished_lines(T, t) if section == "finished" else [])
    return row


def _launched_since(name, T, tickets, alive, snap):
    """The gone rows of the launches at or after the snapshot's time whose terminal is gone
    and that no earlier call reported."""
    since = snap["time"]
    if not since:
        return []
    out = []
    for t in tickets:
        if t.repeat == T.MANAGER:
            continue
        for e in t.sessions:
            key = "%s|%s" % (name, e["session"])
            if not e["launched"] or e["launched"] < since or e["session"] in alive \
                    or key in snap["sessions"] or snap["reported"].get(key) == e["launched"]:
                continue
            row = {"project": name, "session": e["session"], "id": t.id, "mode": e["mode"],
                   "title": t.title}
            out.append(dict(_gone_row(name, T, row, t), launched=e["launched"]))
    return out


def morning_rows(name, T, tickets, now=None):
    """Outside night mode, one row per open question for the morning of an open ticket of
    the project `name` (`todo.open_morning`): the question, word for word. In night mode
    none: the owner gives no answer then."""
    import nightmode
    if nightmode.is_night(now):
        return []
    return [{"key": "%s|%s|morning|%d" % (name, t.id, q["n"]), "project": name,
             "id": t.id, "title": t.title, "mode": "", "section": "morning",
             "text": "question %d, %s: %s" % (q["n"], q["when"], q["text"])}
            for t, q in T.open_morning(tickets)]


def collect(since_last=False, registry=None):
    """The rows of the report, and the rows of the live sessions for the next snapshot."""
    import todo as T
    snap = load_snapshot() if since_last else {"time": "", "sessions": {}, "reported": {}}
    prev = snap["sessions"]
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
            sessions = T.live_sessions(tickets)
            for s in sessions:
                t = by_id.get(s.get("id"))
                # The update lists the agents of tickets: a session with no ticket, such as
                # a chat or a session the owner opened, is the owner's own.
                if t is None or t.repeat == T.MANAGER or T._is_own_session(s):
                    continue
                here.append(_row(name, T, s, t))
            seen = {x["key"] for x in here}
            for key, e in prev.items():
                if e.get("project") == name and key not in seen \
                        and e.get("section") != "finished" and e.get("session"):
                    t = by_id.get(e.get("id"))
                    if t is None:
                        # A copy of a launched session that ran under an id of its own: its
                        # launch prompt names its ticket, which records it now.
                        t, mode = T.link_launch(e["session"], tickets)
                        if t is None:
                            continue
                        e = dict(e, id=t.id, mode=mode, title=t.title)
                    rows.append(dict(_gone_row(name, T, e, t), launched=next(
                        (x["launched"] for x in (t.sessions if t else ())
                         if x["session"] == e["session"]), "")))
            rows += _launched_since(name, T, tickets, {s["session"] for s in sessions}, snap)
            rows += morning_rows(name, T, tickets)
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
    """The five sections as Markdown, every section present, an empty one as `none`, one
    line per agent, and under a finished agent the lines of its answer. An `OPTIONAL`
    section stands only when it holds a row."""
    out = []
    for key, heading in SECTIONS:
        mine = sorted((r for r in rows if r["section"] == key),
                      key=lambda r: (r["project"], r.get("id") or "", r.get("mode") or "",
                                     r.get("key") or ""))
        if key in OPTIONAL and not mine:
            continue
        out.append("**%s**" % heading)
        if not mine:
            out.append("none")
        for r in mine:
            out.append("- " + line(r))
            out += ["  - " + a for a in r.get("answer") or ()]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="octopus sessions",
                                 description="the manager's update: every agent of every "
                                             "registered project, in five sections")
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
    since = now_stamp()
    rows, live = collect(since_last, registry)
    if since_last and save:
        save_snapshot(live, [r for r in rows if r.get("state") == "gone"], since)
    if if_any and not rows:
        return 0
    if as_json:
        out.write(json.dumps(rows, indent=1) + "\n")
    else:
        out.write(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
