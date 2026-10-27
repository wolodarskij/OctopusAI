"""log_session.py - append this session's id to the project's sessions board.

`octopus hook` runs it on the SessionStart event with the hook payload. Writing the id down is
what makes `claude --resume <id>` possible after the session is gone.

The board is the project's `sessionsBoard` (`.octopus/SESSIONS.md` by default). The project
comes from `CLAUDE_PROJECT_DIR`, where the session was launched, else from the payload's
`cwd`; a directory with no
`.octopus/project.json` above it makes the hook do nothing.

Append-only and deduplicated by id, so a resume or a /clear re-runs it harmlessly.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent / "engine") not in sys.path:
    sys.path.insert(0, str(HERE.parent / "engine"))

HEADER = """# Sessions

Every session's id, so any of them can be resumed from the CLI:

    claude --resume <session-id>

Written by the `SessionStart` hook. Append-only, newest last, one line per session. **Set the
agent-id and the goal by hand** once you know them - the hook cannot know either at startup.

| session-id | first seen | source | agent-id | goal |
|---|---|---|---|---|
"""


def board_for(payload):
    """The sessions board of the payload's project, or None."""
    import project
    root = None
    for start in (os.environ.get("CLAUDE_PROJECT_DIR", ""), str(payload.get("cwd") or "")):
        if start:
            root = project.find_root(start)
            if root:
                break
    if root is None:
        return None
    return project.load(root).SESSIONS_BOARD


def append(payload):
    """Append one row for the payload's session. Returns the board path, or None."""
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return None
    board = board_for(payload)
    if board is None:
        return None
    source = str(payload.get("source") or "unknown").strip()
    board.parent.mkdir(parents=True, exist_ok=True)
    existing = board.read_text(encoding="utf-8") if board.exists() else HEADER
    if session_id in existing:
        return board
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    row = "| `%s` | %s | %s | | |\n" % (session_id, stamp, source)
    if not existing.endswith("\n"):
        existing += "\n"
    with open(board, "w", encoding="utf-8", newline="") as handle:
        handle.write(existing + row)
    return board


def main(payload=None) -> int:
    try:
        if payload is None:
            payload = json.load(sys.stdin)
    except Exception:                                   # noqa: BLE001 - a hook never raises
        payload = {}
    try:
        append(payload if isinstance(payload, dict) else {})
    except Exception:                                   # noqa: BLE001 - a hook never raises
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
