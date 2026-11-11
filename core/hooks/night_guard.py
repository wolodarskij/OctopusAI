"""night_guard.py - the question guard of night mode: no ticket session asks the owner from 00:00 to 08:00.

The rule is `core/rules/night-mode.md`. `octopus hook` runs this guard on `PreToolUse`. In night
mode (`nightmode.is_night`) it refuses `AskUserQuestion` in a session launched on a ticket, the
manager's too, and tells the session to decide in place of the owner (`todo.py decide`) or to
collect the question for the morning (`todo.py morning`). A session with no ticket, which the
owner opened, asks as usual. A refused call opens no dialog, so the hook writes no dialog marker.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent / "engine") not in sys.path:
    sys.path.insert(0, str(HERE.parent / "engine"))

import nightmode  # noqa: E402

TOOL = "AskUserQuestion"


def decide(payload, now=None):
    """The `PreToolUse` answer that refuses the question dialog in night mode, or None. Call
    it with the session's project in use (`project.use`)."""
    try:
        if payload.get("tool_name") != TOOL or not nightmode.is_night(now):
            return None
        sid = str(payload.get("session_id") or "")
        if not sid:
            return None
        import project
        import session_state
        import todo
        t, mode = session_state._launch_of(sid)
        if t is None:
            return None
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": nightmode.order(
                project.current().cli, t.id, mode if mode in todo.MODES else "work")}}
    except Exception:                                   # noqa: BLE001 - a hook never raises
        return None
