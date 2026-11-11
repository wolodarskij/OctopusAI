"""chats.py - the codebase chats of one project: read-only Claude sessions the owner talks to.

    TODO/.sessions/chats.json      one entry per chat, oldest first

Each entry holds the chat's session id, its provider, model and effort, the time it was made
(`created`, a `time.time()` stamp), the time it was last opened (`opened`) and its first
question (`question`, from its transcript, "" until the owner asked one).

A chat keeps its history in Claude's transcript of its session id, so a chat that was closed
opens again with `claude --resume <id>`: the same chat, one entry, one card on the board.

THE TITLE. `<project title> - chat - <first question, cut on a word>`; a chat that has no
question yet is `<project title> - chat - new, <MM-DD HH:MM>`. The title is the chat's name in
its terminal window, in Claude Code's own lists (`--name`), on the board and in the session
scan.

A chat with no transcript has nothing to resume: once its terminal is gone (`prune`), its
entry drops out, so a chat opened by mistake leaves nothing behind.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, HERE.parent / "hooks"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import project  # noqa: E402

FILE = "chats.json"
# The characters of a question that break a shell line or a window title.
_UNSAFE = re.compile(r'["&|^%<>\\\x00-\x1f]')
# The length of the question in the title.
QUESTION_WIDTH = 48
# Seconds a new chat may stand with no transcript and no terminal before `prune` drops it:
# its terminal starts a moment after the entry is written.
START_GRACE = 120


def _path():
    return project.current().SESSIONS_DIR / FILE


def load():
    """Every chat of the current project, oldest first."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [c for c in data if isinstance(c, dict) and c.get("session")] \
        if isinstance(data, list) else []


def save(entries):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("%s.%d.tmp" % (p.name, os.getpid()))
    tmp.write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(p))


def get(sid, entries=None):
    """The chat of the session id `sid`, or None."""
    sid = (sid or "").lower()
    if not sid:
        return None
    return next((c for c in (load() if entries is None else entries)
                 if c["session"].lower() == sid), None)


def add(sid, provider="claude", model=None, effort=None, now=None):
    """Record a new chat and return its entry."""
    now = time.time() if now is None else now
    entries = [c for c in load() if c["session"].lower() != sid.lower()]
    entry = {"session": sid.lower(), "provider": provider, "model": model or "",
             "effort": effort or "", "created": now, "opened": now, "question": ""}
    save(entries + [entry])
    return entry


def opened(sid, now=None):
    """Write the time a chat opened again."""
    entries = load()
    c = get(sid, entries)
    if c is not None:
        c["opened"] = time.time() if now is None else now
        save(entries)
    return c


def forget(sid):
    """Drop the chat `sid` from the record. Its transcript stays where Claude keeps it.
    Returns whether it was there."""
    entries = load()
    keep = [c for c in entries if c["session"].lower() != (sid or "").lower()]
    if len(keep) != len(entries):
        save(keep)
    return len(keep) != len(entries)


def transcript(sid):
    """The file Claude keeps the chat `sid` in: chats run in the project root."""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    root = re.sub(r"[^A-Za-z0-9]", "-", str(project.current().ROOT))
    return home / "projects" / root / (sid + ".jsonl")


def has_history(sid):
    """Whether the chat has a transcript, so `claude --resume` opens it again."""
    return transcript(sid).is_file()


def short(text, limit=QUESTION_WIDTH):
    """`text` on one line, with no character that breaks a shell line or a window title, cut
    on a word boundary to `limit` characters."""
    s = " ".join(_UNSAFE.sub(" ", text or "").split())
    if len(s) > limit:
        cut = s[:limit + 1].rsplit(" ", 1)[0]
        s = (cut if cut and len(cut) < len(s[:limit + 1]) else s[:limit]) + "..."
    return s.strip()


def question(c, entries=None):
    """The first question of the chat `c`, read from its transcript once and kept in the
    record."""
    if c.get("question"):
        return c["question"]
    try:
        import session_state
        q = short(session_state.first_prompt(transcript(c["session"])))
    except Exception:                                   # noqa: BLE001 - a title never raises
        q = ""
    if q:
        entries = load() if entries is None else entries
        mine = get(c["session"], entries)
        if mine is not None:
            mine["question"] = q
            save(entries)
        c["question"] = q
    return q


def title(c, entries=None):
    """The chat's title: `<project title> - chat - <first question>`, or `... - chat - new,
    <MM-DD HH:MM>` before the first question."""
    prefix = "%s - chat - " % project.current().title
    q = question(c, entries)
    if q:
        return prefix + q
    return prefix + "new, " + time.strftime("%m-%d %H:%M",
                                            time.localtime(c.get("created") or time.time()))


def prune(live, now=None):
    """Drop each chat that has no transcript and no terminal in `live` (session ids), made
    more than `START_GRACE` seconds ago: nothing of it can be resumed. Returns the chats
    that stay."""
    now = time.time() if now is None else now
    live = {s.lower() for s in live}
    entries = load()
    keep = [c for c in entries if c["session"] in live or has_history(c["session"])
            or now - (c.get("created") or 0) < START_GRACE]
    if len(keep) != len(entries):
        save(keep)
    return keep


def listing(live):
    """The chats for the board, newest opened first: `session`, `title`, `question`,
    `created`, `opened`, `history` (it can be resumed) and `live` (its terminal is open).
    `live` is the set of session ids that have a terminal."""
    live = {s.lower() for s in live}
    entries = prune(live)
    out = []
    for c in entries:
        out.append({"session": c["session"], "title": title(c, entries),
                    "question": c.get("question") or "", "created": c.get("created") or 0,
                    "opened": c.get("opened") or 0, "provider": c.get("provider") or "claude",
                    "history": has_history(c["session"]), "live": c["session"] in live})
    return sorted(out, key=lambda c: -(c["opened"] or c["created"]))
