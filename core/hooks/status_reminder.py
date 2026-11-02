"""status_reminder.py - the 5-minute status reminder of a Codex session.

Claude Code asks an agent for its status line itself (`agents.STATUS_ENV`). Codex has no timer,
but it runs a hook after a tool call and can add text to the agent's context there. `octopus
status-hook <provider>` is that hook:

    provider   the clock starts again on                          the reminder after
    codex      SessionStart, UserPromptSubmit, Stop               PostToolUse

On `PreToolUse` the same hook line is the README guard (`readme_guard.py`): it refuses a tool
call that writes a project-wide README file the owner did not approve.

After a tool call it also reads the new lines of the session's transcript: visible assistant
text starts the clock again too, from the `timestamp` of its line when the line has one. Once `INTERVAL` seconds passed, the hook adds
`agents.STATUS_REMINDER` to the agent's context and starts the clock again.

The clock of each session is one JSON file in `project.home()/status-reminder/`, outside every
repository: `{"last": <time>, "offset": <bytes of the transcript read>}`. The hook never fails
and never holds the agent: an error makes it print only what the event needs.

THE REPLY CHECK (`reply_check.py`) of a Codex session: `UserPromptSubmit` keeps the prompt in
the state file (`request`); `Stop` starts the check of `last_assistant_message` against it,
detached, and answers `{}` at once; the next `UserPromptSubmit` gives the session the note of the
sentences that failed, as `additionalContext`, from `<session>.note` beside the state file. The
check never blocks a turn: the owner saw the reply already.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent / "engine") not in sys.path:
    sys.path.insert(0, str(HERE.parent / "engine"))

INTERVAL = 300
STATE_DIR = "status-reminder"
# A state file that nothing touched for this long belongs to a session that is gone.
STALE = 7 * 24 * 3600
# The most transcript one hook call reads; the rest waits for the next call.
READ_LIMIT = 4 * 1024 * 1024

import agents  # noqa: E402

# Each provider's hook events (`agents.STATUS_HOOKS`): the ones that start the clock again, and
# the last, after a tool call, that can add the reminder.
EVENTS = {p: {"reset": events[:-1], "tool": events[-1]}
          for p, (_rel, events) in agents.STATUS_HOOKS.items()}
# What an event must print when it adds nothing. Codex's `Stop` takes only JSON.
_QUIET = {("codex", "Stop"): {}}
# The Codex events of the reply check: the prompt, and the end of the turn.
PROMPT_EVENT, STOP_EVENT = "UserPromptSubmit", "Stop"


def _quiet(provider, event):
    return _QUIET.get((provider, event))


def _reminder(provider):
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "additionalContext": agents.STATUS_REMINDER}}


def state_dir():
    import project
    return project.home() / STATE_DIR


def _session(provider, payload):
    sid = str(payload.get("session_id") or "").strip()
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", sid)[:120]
    return "%s-%s" % (provider, sid) if sid else None


def _load(p):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _save(p, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp%d" % os.getpid())
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(str(tmp), str(p))


def _says_text(obj):
    """Whether one transcript line is assistant text the owner sees. Codex writes an
    `agent_message` event and a `message` item with `output_text`; a Claude-shaped transcript
    writes `{"message": {"role": "assistant", "content": [{"type": "text"}]}}`."""
    if not isinstance(obj, dict):
        return False
    for o in (obj, obj.get("payload"), obj.get("message")):
        if not isinstance(o, dict):
            continue
        if o.get("type") == "agent_message" and str(o.get("message") or "").strip():
            return True
        if o.get("role") != "assistant":
            continue
        c = o.get("content")
        if isinstance(c, str) and c.strip():
            return True
        if isinstance(c, list) and any(
                isinstance(i, dict) and i.get("type") in ("text", "output_text")
                and str(i.get("text") or "").strip() for i in c):
            return True
    return False


def _size(path):
    try:
        return Path(path).stat().st_size
    except (OSError, TypeError, ValueError):
        return 0


def _when(obj, now):
    """The time a transcript line carries in `timestamp`, else `now`."""
    ts = obj.get("timestamp")
    if isinstance(ts, (int, float)):
        return float(ts) / (1000.0 if ts > 1e11 else 1.0)
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return now
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return min(t.timestamp(), now)


def read_new_text(path, offset, now):
    """`(said, offset)`: the time of the last visible assistant text in the complete lines of
    the transcript after `offset`, or None, and the offset after the last complete line read."""
    try:
        size = Path(path).stat().st_size
    except (OSError, TypeError, ValueError):
        return None, offset
    if size < offset:
        offset = 0
    if size == offset:
        return None, offset
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read(READ_LIMIT)
    except OSError:
        return None, offset
    end = data.rfind(b"\n")
    if end < 0:
        return None, offset
    said = None
    for line in data[:end].splitlines():
        try:
            obj = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if _says_text(obj):
            said = max(said or 0.0, _when(obj, now))
    return said, offset + end + 1


def _root(payload):
    """The project root of the session, or None."""
    import project
    for start in (payload.get("cwd"), os.getcwd()):
        if start:
            try:
                r = project.find_root(str(start))
            except Exception:                           # noqa: BLE001 - no project there
                r = None
            if r:
                return r
    return None


def start_check(payload, st, note, ask=None):
    """Start the reply check of a Codex session's last message, detached. With `ask`, the
    check runs here, with `ask(prompt)` in place of the headless CLI. Never raises."""
    import reply_check
    try:
        text = str(payload.get("last_assistant_message") or "")
        if os.environ.get(reply_check.ENV) or not reply_check.worth_checking(text):
            return
        root = _root(payload)
        job = {"message": text, "provider": "codex", "note": str(note),
               "session": _session("codex", payload),
               "request": reply_check.launch_request(str(st.get("request") or ""), root=root)}
        if ask is not None:
            job_path = Path(note).with_suffix(".job")
            job_path.write_text(json.dumps(job), encoding="utf-8")
            reply_check.run_job(job_path, ask=ask)
            return
        import project
        if root is None:
            return
        tok = project.use(root)
        try:
            p = project.current()
            if not p.reply_check:
                return
            job.update(cwd=str(root), model=p.models.get("haiku", reply_check.MODEL),
                       home=str(project.home()))
        finally:
            project.reset(tok)
        reply_check.start(Path(note).with_suffix(".job"), job)
    except Exception:                                   # noqa: BLE001 - a hook never raises
        pass


def note_answer(note):
    """The `UserPromptSubmit` answer that gives a Codex session the note of the reply check,
    or None."""
    import reply_check
    text = reply_check.take_note(note)
    if not text:
        return None
    return {"hookSpecificOutput": {"hookEventName": PROMPT_EVENT, "additionalContext": text}}


def _prune(d, now):
    try:
        for f in d.glob("*.json"):
            if now - f.stat().st_mtime > STALE:
                f.unlink()
    except OSError:
        pass


def handle(provider, payload, now=None, base=None, ask=None):
    """The JSON the hook prints for one event, or None for nothing. `ask` replaces the
    headless call of the reply check, which then runs at once (`start_check`)."""
    now = time.time() if now is None else now
    event = str(payload.get("hook_event_name") or "")
    quiet = _quiet(provider, event)
    key = _session(provider, payload)
    spec = EVENTS[provider]
    if key is None or (event not in spec["reset"] and event != spec["tool"]):
        return quiet
    d = Path(base) if base else state_dir()
    p = d / (key + ".json")
    lock = d / (key + ".lock")
    d.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - lock.stat().st_mtime > 60:
            lock.unlink()
    except OSError:
        pass
    # Two tool calls that end together give one reminder: the call that finds the lock
    # taken adds nothing.
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return quiet
    try:
        st = _load(p)
        transcript = payload.get("transcript_path")
        keep = {}
        if provider == "codex" and event == PROMPT_EVENT:
            keep = {"request": str(payload.get("prompt") or "")}
        if st is None:
            if event == "SessionStart":
                _prune(d, now)
            st = dict(keep, last=now, offset=_size(transcript))
            _save(p, st)
            out = quiet
        else:
            out = _tick(provider, event, spec, st, keep, transcript, now)
            _save(p, st)
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass
    if provider == "codex" and event == STOP_EVENT:
        start_check(payload, st, d / (key + ".note"), ask)
    elif provider == "codex" and event == PROMPT_EVENT:
        try:
            return note_answer(d / (key + ".note")) or out
        except Exception:                               # noqa: BLE001 - a hook never raises
            return out
    return out


def _tick(provider, event, spec, st, keep, transcript, now):
    """Move the clock of the state `st` for one event, in place. Returns the reminder, or
    what the event prints when it adds nothing."""
    last = float(st.get("last", now))
    offset = int(st.get("offset", 0))
    said, offset = read_new_text(transcript, offset, now)
    if said is not None:
        last = max(last, said)
    if event in spec["reset"]:
        last = now
    out = _quiet(provider, event)
    if event == spec["tool"] and now - last >= INTERVAL:
        out, last = _reminder(provider), now
    st.update(keep)
    st.update(last=last, offset=offset)
    return out


def main(argv):
    """`octopus status-hook <provider>`: one hook event on stdin, the answer on stdout."""
    provider = argv[0] if argv else ""
    if provider not in EVENTS:
        return 0
    payload = {}
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            payload = {}
        if str(payload.get("hook_event_name") or "") == "PreToolUse":
            import readme_guard
            out = readme_guard.decide(payload)
        else:
            out = handle(provider, payload)
    except Exception:                                   # noqa: BLE001 - a hook never raises
        out = _quiet(provider, str(payload.get("hook_event_name") or ""))
    if out is not None:
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()
    return 0
