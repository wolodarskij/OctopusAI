"""reply_check.py - the check of an agent's last message against the reporting rule.

The reporting rule (`core/rules/reporting-to-the-owner.md`) asks one question of every sentence
of a message to the owner: "Is this sentence part of what THIS request asked for, or the exact
reason a part of it is missing?" Text alone does not hold when an agent summarizes a long
ticket, so a small model asks it again after each turn.

NEVER A SECOND REPLY. No hook can judge a reply before the owner sees it: Claude Code shows the
reply while it streams, and `Stop` runs after that. `MessageDisplay` runs while it streams,
but it only changes what the screen shows, never what the agent and the transcript keep, and
it holds every line for its hook (10 seconds by default). A `Stop` hook that blocks makes the
agent write the reply a second time below the first. So the check never blocks and never
rewrites a reply. It runs detached after the turn, and the failing sentences reach the agent as
context with the next prompt (`NOTE`):

    provider     the end of the turn: start the check     the next prompt: give the note
    Claude Code  `Stop` (`session_state.py`)               `UserPromptSubmit` `additionalContext`
    Codex        `Stop` (`status_reminder.py`)             `UserPromptSubmit` `additionalContext`

THE REQUEST is the owner's last prompt. A scheduled prompt (the manager's routine) is the
request it holds. A launch prompt ("Work ticket T0001. Read TODO/.briefs/...") is the request
of its brief: what the brief asks the last message for (`LAUNCH_ASKS`), and the owner's note
of the launch.

THE VERDICT. The project's headless provider (Claude Haiku or Codex) reads the request, lists its parts,
and names each failing sentence with a quote (`PROMPT`). A sentence that says a part of the
request was done, is being done or will be done passes. A quote that stands nowhere in the
message is dropped. A sentence is never failed when it is a question (it ends with `?`), the
finished line `WORK IS FINISHED`, or a statement that a ticket is in the verify or the closed
lane. Every fault (no CLI, no answer within `SECONDS`, no JSON) gives no note. The
check's own CLI runs with `ENV` set, and every Octopus AI hook under it returns at
once.

THE WAITING SECTION. No model decides it. Each list item under the heading `Waiting for your
input` (the manager's update) that names a registered project and an agent fails when that
agent is not an open session in the `input` state of `sessionreport.collect`, even when the
line ends with `?` (`waiting_fails`).

THE JOB. The hook writes the message, the request and where the note goes into a job file and
starts `python reply_check.py --job <file>` detached (`start`), so the turn ends at once and the
closer of a finished session waits for nothing. The job writes the note file only when a
sentence failed; the next prompt takes it (`take_note`). A note older than `NOTE_SECONDS` is
dropped.

`replyCheck: false` in `.octopus/project.json` turns it off for a project. Each check adds one
line to `reply-check.jsonl` in `project.home()`, outside every repository: the time, the
provider, the session, the request and its parts, how many sentences failed and which.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

# Set in the environment of the check's own CLI (the judge's variable): every hook
# under it returns at once.
ENV = "OCTOPUS_JUDGE"
SECONDS = 60
# How long a note waits for the next prompt.
NOTE_SECONDS = 24 * 3600
MODEL = "haiku"
# The tail of the message, and of the request, that the check reads.
MESSAGE_CHARS = 6000
REQUEST_CHARS = 3000
# The most transcript read backwards for the request.
TRANSCRIPT_TAIL = 4 * 1024 * 1024
LOG = "reply-check.jsonl"
LOG_LIMIT = 1024 * 1024

FINISHED_LINES = ("WORK IS FINISHED", "INVESTIGATION FINISHED")
_TRAIL = " \t*_`)\"'"
# The prompt a launch types into a new session: "<Mode> ticket T0001. Read TODO/.briefs/...".
_LAUNCH_PROMPT = re.compile(r"\b(\w+) ticket (T\d+)\. Read \S*\.briefs/")
_BRIEF_PATH = re.compile(r"[\w./-]*\.briefs/T\d+\.md")
# The section of a brief that holds the owner's note (`core/engine/brief/note.md`).
_NOTE = re.compile(r"^## (?:The owner's order for this launch|What the owner said)[^\n]*\n"
                   r"(.*?)(?=^## |\Z)", re.S | re.M)
# A statement that a ticket is in the verify or the closed lane, when no project names its
# lanes (`session_state.says_done` reads the project's own).
_DONE = re.compile(r"\b[A-Z]+\d{3,}\b[^.?!]{0,40}\b(?:Verification|Done)\b", re.I)

# The manager's section of agents that wait for the owner (`sessionreport.SECTIONS`), and why
# a line in it fails when its agent is not an open session in the `input` state.
WAITING = "Waiting for your input"
WAITING_WHY = "Waiting for your input lists only an open session in the input state"
_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
# The agent after the project name in a line of the update: a ticket id, or a session prefix.
_AGENT = re.compile(r"\s*([A-Za-z]*\d{3,}|[0-9a-fA-F]{8})\b")

# What each launch brief asks the last message for.
LAUNCH_ASKS = {
    "work": "the files this ticket changed; a problem only if one stopped the ticket, in one "
            "sentence; then a question for the owner as the last line, or the last line "
            "WORK IS FINISHED. The outcome of the ticket stands on the ticket, not in the "
            "message.",
    "plan": "that the plan is on the ticket; a decision only the owner can make, as a "
            "question on the last line, or the last line WORK IS FINISHED.",
    "review": "the verdict on the ticket and where it left it; a question for the owner as "
              "the last line, or the last line WORK IS FINISHED.",
    "verify": "whether the work holds and where the ticket is now; a problem that stopped "
              "the verification, in one sentence; a question for the owner as the last line, "
              "or the last line WORK IS FINISHED.",
}

PROMPT = """You check one message that an AI agent wrote to its owner, against one rule.

THE REQUEST the message answers, between the markers:
<<<REQUEST
%(request)s
REQUEST>>>

THE MESSAGE, between the markers:
<<<MESSAGE
%(message)s
MESSAGE>>>

First read the request whole and list every part of it: each thing the owner asked to be \
done, answered or told. "Create a ticket and tell its agent to make the changes, commit and \
push" has four parts: create the ticket, run its agent, commit, push.

The rule asks one question of every sentence, list item and table row of the message:
"Is this sentence part of what THIS request asked for, or the exact reason a part of it is \
missing?" A sentence FAILS when the answer is no.

A sentence that says a part of the request was done, is being done or will be done PASSES, \
whatever it names to say so: a ticket, a session, an agent and what it does now. "Its agent \
is running and will commit and push when it is done" answers the parts run, commit and push.

These FAIL, unless they answer a part of the request:
- a measurement nobody asked for: seconds, counts, timings, how often something was seen;
- a caveat nobody asked for: what was not tested, not checked, not installed, not proven;
- a defect or a ticket this request was not given, a side ticket the agent filed, a check \
that failed "before this ticket";
- the state of a ticket, a session or an earlier task that no part of the request is about;
- how the work was done: what was read, run or checked, what a tool printed, the steps, the \
reasoning, a decision and why, an approach dropped;
- an opening or a closing sentence, an offer of more work.

These always PASS:
- the answer, the result, a list, a table or a number the request asked for;
- a file the work changed, and what it does now;
- a part of the request that is missing, with its exact cause in one sentence;
- a question to the owner: every sentence that ends with "?";
- the line WORK IS FINISHED, and a statement that a ticket is in Verification or Done;
- a heading, a table header, a line that only formats.

Answer with ONE JSON object and nothing else:
{"parts": ["<each part of the request, in a few words>"], "fails": [{"sentence": "<the \
failing sentence, copied exactly from the message>", "why": "<at most 12 words: which kind \
above>"}]}
"fails" is an empty list when every sentence passes."""

NOTE = """The reply check of the reporting rule read your last message to the owner, after \
the owner saw it. These sentences failed its one test, "Is this sentence part of what THIS \
request asked for, or the exact reason a part of it is missing?":
%(fails)s
Write no sentence of these kinds in your next messages. Do not repeat, correct or explain that \
message, and do not mention this check to the owner."""


def _lines(text):
    return [ln for ln in (text or "").splitlines() if ln.strip()]


def _plain(text):
    text = re.sub(r"[*_`#>\"'‘’“”|]", "", str(text or "").lower())
    return " ".join(text.split())


def _done(text):
    try:
        import project
        project.current()
        import session_state
        return bool(session_state.says_done(text))
    except Exception:                                   # noqa: BLE001 - no project
        return bool(_DONE.search(re.sub(r"[*_`]", "", text or "")))


def protected(sentence):
    """Whether the check never fails this sentence: a question, the finished line, or a
    statement that a ticket is in the verify or the closed lane."""
    s = " ".join(str(sentence or "").split()).strip()
    bare = s.strip(_TRAIL + "-:")
    if bare.endswith("?") or bare in FINISHED_LINES or any(f in s for f in FINISHED_LINES):
        return True
    return _done(s)


def worth_checking(text):
    """Whether the message holds a line the check may fail: not empty, and not only
    questions, the finished line and done statements."""
    return any(re.search(r"[A-Za-z]", ln) and not protected(ln) for ln in _lines(text))


def _text_of(content):
    """The text the owner typed in one `user` row, "" for a tool result or a meta line."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
        return ""
    return "\n".join(str(c.get("text") or "") for c in content
                     if isinstance(c, dict) and c.get("type") == "text")


def request_of(path):
    """The last prompt in a Claude Code transcript: the owner's, or the one a scheduled job
    typed. "" when none is found."""
    try:
        size = Path(path).stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - TRANSCRIPT_TAIL))
            data = fh.read()
    except (OSError, TypeError, ValueError):
        return ""
    for raw in reversed(data.splitlines()):
        if b'"user"' not in raw:
            continue
        try:
            row = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("type") != "user" or row.get("isSidechain"):
            continue
        msg = row.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        text = _text_of(msg.get("content")).strip()
        # Claude Code's own lines: a command's caveat or output, a system reminder.
        if text and not text.startswith("<"):
            return text
    return ""


def note_of(brief):
    """The owner's note in a brief file: the section under `## The owner's order for this
    launch`, or under the older `## What the owner said`, or ""."""
    try:
        text = Path(brief).read_text(encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return ""
    m = _NOTE.search(text)
    return m.group(1).strip() if m else ""


def launch_request(request, mode="", root=None):
    """The request with what its brief asks the last message for, and the owner's note of the
    launch, when it is a launch prompt. `root` is the project the brief path is relative to."""
    m = _LAUNCH_PROMPT.search(request or "")
    if not m:
        return request
    mode = mode or m.group(1).lower()
    asks = LAUNCH_ASKS.get(mode)
    if not asks:
        return request
    out = ("%s\n\nThe brief of this launch is the request. It asks the last message for: %s"
           % (request.strip(), asks))
    brief = _BRIEF_PATH.search(request)
    note = note_of(Path(root or ".") / brief.group(0)) if brief else ""
    if note:
        out += "\n\nThe owner's note to this launch, which the request includes:\n" + note
    return out


def _ask(prompt, cwd, model, timeout):
    """Use the project's headless provider and executable; a failed check gives no note."""
    import project
    import providers
    try:
        p = project.load(Path(cwd)) if cwd else None
        spec = providers.get(p.provider if p else providers.DEFAULT)
        selected = model if spec["dialect"] == "claude" else None
        if p:
            selected, effort = p.choose(p.provider, selected)
            spec["command"] = p.providers[p.provider]["command"]
        else:
            selected = spec["models"].get(selected, selected) or ""
            effort = spec["defaultEffort"]
        spec["env"] = dict(spec["env"], **{ENV: "1"})
        return providers.answer(spec, prompt, selected, effort, timeout)
    except (OSError, ValueError):
        return ""


def parse(answer, message, info=None):
    """`[(sentence, why)]`: the failing sentences of the answer whose quote stands in the
    message and that are not protected. `info` gets the parts of the request it read."""
    m = re.search(r"\{.*\}", answer or "", re.S)
    if not m:
        return []
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return []
    fails = d.get("fails") if isinstance(d, dict) else None
    if info is not None and isinstance(d, dict) and isinstance(d.get("parts"), list):
        info["parts"] = [" ".join(str(x).split())[:80] for x in d["parts"]][:12]
    out, seen, body = [], set(), _plain(message)
    for f in fails if isinstance(fails, list) else []:
        if not isinstance(f, dict):
            continue
        s = " ".join(str(f.get("sentence") or "").split())
        key = _plain(s).strip(" .,;:-")
        if not key or key in seen or key not in body or protected(s):
            continue
        seen.add(key)
        out.append((s, " ".join(str(f.get("why") or "").split())[:120]))
    return out


def check(message, request, cwd=None, model=MODEL, ask=None, info=None):
    """The failing sentences of `message` against `request`, `[]` when it passes or the
    check fails. `ask(prompt)` replaces the headless call. `info` gets the request and
    the parts of it the model read, for the log."""
    if not worth_checking(message):
        return []
    request = (request or "(no request found)")[-REQUEST_CHARS:]
    if info is not None:
        info["request"] = " ".join(request.split())[:300]
    prompt = PROMPT % {"request": request, "message": message[-MESSAGE_CHARS:]}
    try:
        answer = ask(prompt) if ask else _ask(prompt, cwd, model, SECONDS)
    except Exception:                                   # noqa: BLE001 - a fault passes
        return []
    return parse(answer, message, info)


def waiting_lines(message):
    """The list items under the heading `WAITING` of a message. Any line that is not a list
    item ends the section: the next heading, `none`, a sentence."""
    out, inside = [], False
    for ln in (message or "").splitlines():
        bare = re.sub(r"[*_#`:]", "", ln).strip()
        if not bare:
            continue
        if not _ITEM.match(ln):
            inside = bare.lower() == WAITING.lower()
            continue
        if inside:
            out.append(_ITEM.sub("", ln, count=1).strip())
    return out


def waiting_fails(message, rows=None, names=None):
    """`[(line, why)]`: each line under `WAITING` whose agent, a registered project and a
    ticket id or a session prefix, is not an open session in the `input` state. `rows` are
    the rows of `sessionreport.collect`, `names` the registered project names; both are read
    when they are None. A line that names no registered project and no agent passes. Any
    fault gives no fail."""
    lines = waiting_lines(message)
    if not lines:
        return []
    try:
        if names is None:
            import project
            names = [r["name"] for r in project.load_registry()]
        if rows is None:
            import sessionreport
            rows = sessionreport.collect()[0]
    except Exception:                                   # noqa: BLE001 - a fault passes
        return []
    waiting = set()
    for r in rows:
        if r.get("section") != "input":
            continue
        if r.get("id"):
            waiting.add((r.get("project"), str(r["id"]).lower()))
        if r.get("session"):
            waiting.add((r.get("project"), str(r["session"])[:8].lower()))
    out = []
    for ln in lines:
        hits = [n for n in names if n and n in ln]
        if not hits:
            continue
        name = max(hits, key=len)
        m = _AGENT.match(ln[ln.find(name) + len(name):])
        if not m:
            continue
        agent = m.group(1).lower()
        if (name, agent) not in waiting and (name, agent[:8]) not in waiting:
            out.append((" ".join(ln.split()), WAITING_WHY))
    return out


def note(fails):
    """The context that tells the agent which sentences of its last message failed."""
    return NOTE % {"fails": "\n".join('- "%s"%s' % (s, " (%s)" % w if w else "")
                                      for s, w in fails)}


def write_note(path, fails, now=None):
    """Keep the note of `fails` for the next prompt."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"time": time.time() if now is None else now,
                             "text": note(fails)}), encoding="utf-8")


def take_note(path, now=None):
    """The note kept for this prompt, removed as it is read; "" when there is none or it is
    older than `NOTE_SECONDS`."""
    p = Path(path)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        p.unlink()
    except (OSError, ValueError):
        return ""
    now = time.time() if now is None else now
    if not isinstance(d, dict) or now - float(d.get("time") or 0) > NOTE_SECONDS:
        return ""
    return str(d.get("text") or "")


def start(job_path, job):
    """Write the job and start `reply_check.py --job` on it, detached. Returns at once: the
    hook that calls it never holds the turn."""
    p = Path(job_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(job), encoding="utf-8")
    import sys
    argv = [sys.executable, str(Path(__file__).resolve()), "--job", str(p)]
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    if os.name != "nt":
        return subprocess.Popen(argv, start_new_session=True, **kw)
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # Out of the hook's job object, when it allows it, so the end of the hook does not end
    # the check.
    try:
        return subprocess.Popen(argv, creationflags=flags
                                | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), **kw)
    except OSError:
        return subprocess.Popen(argv, creationflags=flags, **kw)


def run_job(job_path, ask=None, rows=None):
    """Check the message of one job, log it, and write the note when a sentence failed.
    The job file goes. `rows` replaces the session rows that `waiting_fails` reads. Returns
    the failing sentences."""
    p = Path(job_path)
    try:
        job = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    finally:
        try:
            p.unlink()
        except OSError:
            pass
    try:
        # The project's own lane names for `protected`.
        import sys
        engine = str(Path(__file__).resolve().parent.parent / "engine")
        if engine not in sys.path:
            sys.path.insert(0, engine)
        import project
        project.use(project.find_root(job.get("cwd")))
    except Exception:                                   # noqa: BLE001 - the plain names do
        pass
    info = {}
    fails = check(job.get("message") or "", job.get("request") or "", cwd=job.get("cwd"),
                  model=job.get("model") or MODEL, ask=ask, info=info)
    seen = {_plain(s) for s, _w in fails}
    fails += [f for f in waiting_fails(job.get("message") or "", rows=rows)
              if _plain(f[0]) not in seen]
    if job.get("home"):
        log(job["home"], job.get("provider") or "", job.get("session") or "", fails, info)
    if fails and job.get("note"):
        write_note(job["note"], fails)
    return fails


def log(home, provider, session, fails, info=None):
    """One line of `reply-check.jsonl`, with the request and its parts from `info`. Never
    raises."""
    try:
        p = Path(home) / LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > LOG_LIMIT:
            keep = p.read_bytes()[-LOG_LIMIT // 2:]
            p.write_bytes(keep[keep.find(b"\n") + 1:])
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "provider": provider,
               "session": session, "failed": len(fails), "sentences": [s for s, _w in fails]}
        rec.update(info or {})
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:                                   # noqa: BLE001 - the log is no duty
        pass


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--job":
        os.environ[ENV] = "1"
        try:
            run_job(sys.argv[2])
        except Exception:                               # noqa: BLE001 - nobody reads it
            sys.exit(1)
