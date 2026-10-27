"""todo_server.py - the Octopus AI Agent Board of every registered project, served to a browser on localhost.

    python octopus.py board                        open the Agent Board
    python octopus.py board --project <name|path>  open it on this project first
    python octopus.py board --no-open              serve without opening a browser
    python octopus.py board --port N               pin the port

ONE SERVER, MANY PROJECTS. The server serves every project in the registry
(`%APPDATA%/OctopusAI/projects.json`), and the one named by `--project` when it is not
registered. Every request names its project in the `project` query parameter; the server
makes that project the current one for the length of the request (a ContextVar), so two
requests on two threads never see each other's project.

WHY A SERVER AND NOT A FILE. The board has to read `TODO/`, move files between the lane
directories and launch `claude` on a ticket. A page opened with `file://` can do none of
those. A localhost server is the smallest thing that can.

WHAT IT WILL NOT DO. It binds `127.0.0.1` only, it refuses a request whose `Host` is not
loopback, and every mutating call carries a token minted at startup and handed to the page
in its URL. No verb takes a path from the caller - a ticket is addressed by its id and
resolved through `todo.py`, and a project by its registered name - except `addProject`,
which registers a directory the owner types, `suggestPacks`, which only looks for the
`detect` globs of each pack in it, and `pickFolder`, which only opens the owner's folder
dialog at a directory.

THE ARTIFACTS VIEW. `/view/<VIEW_TOKEN>/<project>/<path>` sends one file of a served project,
and renders a Markdown file, for the board's Artifacts view (`artifacts.py`). It takes a
second key that opens no other route, it reaches only what `artifacts.safe_path` lets
through, and every answer carries a sandbox policy, so a page shown there runs in an opaque
origin. `artifactAdd` takes a path too, under the same guard.
"""

from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
for _d in (HERE, HERE.parent / "win", HERE.parent / "hooks", HERE.parent / "posix"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import project  # noqa: E402
import providers  # noqa: E402
import artifacts as _artifacts  # noqa: E402
import todo as T  # noqa: E402
import vcs as _vcs  # noqa: E402
from project import P  # noqa: E402

PAGE = HERE / "todo_board.html"
TOKEN = secrets.token_urlsafe(24)
# The key of `/view/` only: a project file opened in the board. A page served there can read
# its own URL, so this key opens no `/api/` call.
VIEW_TOKEN = secrets.token_urlsafe(24)
# Every `/view/` answer runs in an opaque origin, in the board's frame and in a tab of its own.
VIEW_CSP = "sandbox allow-scripts allow-popups allow-forms allow-popups-to-escape-sandbox"
# The types the platform's own table can name wrongly; a script sent as text/plain does not run.
VIEW_TYPES = {".html": "text/html", ".htm": "text/html", ".js": "text/javascript",
              ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json",
              ".svg": "image/svg+xml", ".wasm": "application/wasm", ".md": "text/plain",
              ".txt": "text/plain"}


def _code_stamp():
    """The newest change time of the engine's Python files."""
    return max((f.stat().st_mtime_ns for f in HERE.glob("*.py")), default=0)


# The page is read from disk on every request, the Python only at start. A server whose code
# changed after it started serves a new page over old calls; `snapshot` says so.
CODE_STAMP = _code_stamp()
EXTRA = {}          # name -> root of a project served without being registered
DEFAULT = [None]    # the project the page opens on


def projects():
    """name -> root of every project this server serves, in registry order."""
    out = {}
    for r in project.load_registry():
        root = Path(r["path"])
        if project.has_config(root):
            out[r["name"]] = root
    for name, root in EXTRA.items():
        out.setdefault(name, root)
    return out


def project_root(name):
    ps = projects()
    if name and name in ps:
        return ps[name]
    if not name and DEFAULT[0] in ps:
        return ps[DEFAULT[0]]
    if not name and ps:
        return next(iter(ps.values()))
    raise KeyError("project %s" % (name or "(none)"))


def _has_browser(p):
    return "browser-game" in p.RULES["rulesets"]


def project_info(name, root):
    p = project.load(root)
    return {"name": name, "path": str(root), "slug": p.slug, "title": p.title,
            "rulesets": list(p.RULES["rulesets"]), "browser": _has_browser(p),
            "problems": list(p.problems)}


def snapshot():
    """Everything the page draws for the current project, in one payload."""
    objs = T.load_all()
    tickets = []
    for t in objs:
        tickets.append({
            "id": t.id,
            "title": t.title,
            "state": t.state,
            "status": t.status,
            "severity": t.severity,
            "difficulty": t.difficulty,
            "category": t.category,
            "source": t.source,
            "epic": t.epic,
            "permits": t.permits,
            "claim": t.claim,
            "session": t.session,
            "launched": t.launched,
            "sessions": t.sessions,
            "blockedBy": t.blocked_by,
            "parent": t.parent,
            "group": t.group,
            "tags": t.tags,
            "repeat": t.repeat,
            "run": t.run,
            "remoteControl": T.remote_control(t),
            "remoteControlSet": t.meta.get(T.RC_FIELD, ""),
            "remoteControlAvailable": T.remote_control_available(t),
            "provider": T.ticket_provider(t) or P.provider,
            "lastRun": t.meta.get("last-run", ""),
            "lastResult": t.meta.get("last-result", ""),
            "created": str(t.meta.get("created") or ""),
            "updated": int(t.path.stat().st_mtime),
            "body": t.body.strip(),
            "path": t.path.relative_to(P.ROOT).as_posix(),
        })
    epics = {name: {"state": st, "title": title}
             for name, (st, title, _p) in T.load_epics().items()}
    if P.groups_store == "files":
        table = T.group_table(objs)
        groups = {g["slug"]: g["tickets"] for g in table["groups"]}
        items = {g["slug"]: [] for g in table["groups"]}
        ungrouped = table["ungrouped"]
        group_files = {g["slug"]: {"title": g["title"], "description": g["description"]}
                       for g in table["groups"]}
    else:
        mapping = T.load_groups()
        members, loose = T.todo_groups(objs, mapping)
        groups = {name: [t.id for t in ts] for name, ts in members.items()}
        items = {name: list(v) for name, v in mapping.items()}
        ungrouped = [t.id for t in loose]
        group_files = {}
    try:
        sessions = _public(T.live_sessions(objs))
    except Exception:                                   # a board without terminals still draws
        sessions = []
    return {
        "project": {"name": _current_name(), "slug": P.slug, "title": P.title,
                    "path": str(P.ROOT), "todoDir": P.todo_dir,
                    "cli": P.cli, "browser": _has_browser(project.current()),
                    "remoteControl": P.remote_control,
                    "problems": list(P.problems)},
        "tickets": tickets,
        "sessions": sessions,
        "epics": epics,
        "groups": groups,
        "groupItems": items,
        "groupsStore": P.groups_store,
        "groupFiles": group_files,
        "ungrouped": ungrouped,
        "autoGroups": [{"name": ag["name"], "tag": ag["tag"],
                        "ids": [t.id for t in T.auto_group_tickets(objs, ag)]}
                       for ag in P.auto_groups],
        "openStates": list(P.OPEN_STATES),
        "board": T.load_board(),
        "lanes": P.lanes,
        "roles": dict(P.ROLE),
        "statusRoles": dict(P.S),
        "states": list(P.STATES),
        "stateLabels": P.STATE_LABEL,
        "statuses": {k: list(v) for k, v in P.STATUSES.items()},
        "severities": list(T.SEVERITIES),
        "difficulties": list(T.DIFFICULTIES),
        "categories": list(P.CATEGORIES),
        "categoryMeaning": P.CATEGORY_MEANING,
        "difficultyMeaning": P.DIFF_MEANING,
        "modes": list(T.MODES),
        # The default provider's models and efforts; `providerChoices` holds every provider's, so a
        # launch offers only what the provider it starts can run.
        "models": list(P.providers[P.provider]["models"]),
        "efforts": list(P.providers[P.provider]["efforts"]),
        "defaultModel": P.providers[P.provider]["defaultModel"],
        "defaultEffort": P.providers[P.provider]["defaultEffort"],
        "providerChoices": provider_choices(),
        "providers": list(project.PROVIDERS),
        "defaultProvider": P.provider,
        "providerFeatures": dict(project.PROVIDER_FEATURES),
        "providerLabels": {a: providers.label(a) for a in project.PROVIDERS},
        # The machine's default AI provider and model; the page asks for them until confirmed.
        "defaultSetup": providers.default_state(),
        "repeatRoles": list(T.REPEAT_ROLES),
        "repeatLabel": T.REPEAT_LABEL,
        "repeatMeaning": T.REPEAT_MEANING,
        "permits": dict(P.PERMITS),
        "watchdog": watchdog_view(P.ROOT),
        "root": str(P.ROOT),
        "serverStale": _code_stamp() != CODE_STAMP,
    }


_NAME = threading.local()


def _current_name():
    return getattr(_NAME, "value", "")


def _ticket(tid):
    t = T.find(T.load_all(), tid)
    if t is None:
        raise KeyError(tid)
    return t


def act_move(body):
    """Move one ticket, or a whole epic, into another lane."""
    state = body["state"]
    if state not in P.STATES:
        raise ValueError("unknown state %r" % state)
    asked = body.get("status") or None
    # Every move on the board is the owner's, so a ticket the board parks waits for the
    # owner and names no blocker. The same holds for the closed lane: the board is not gated
    # by `_may_close`.
    status = asked or (P.S["blocked by user"] if state == T.lane("blocked") else None)
    if status and status not in P.STATUSES[state]:
        raise ValueError("status %r does not belong to %s" % (status, state))
    # A manager ticket never finishes: not even the owner's drag puts it in the verify or the
    # blocked lane, or closes it as done.
    targets = ([t for t in T.load_all() if t.epic == body["epic"]] if body.get("epic")
               else [_ticket(tid) for tid in body["ids"]])
    for t in targets:
        why = T.manager_lane_problem(t, state, status)
        if why:
            raise ValueError(why)
    import shutil
    if body.get("epic"):
        name = body["epic"]
        epics = T.load_epics()
        if name not in epics:
            raise KeyError(name)
        src = P.TODO / epics[name][0] / name
        dst = P.TODO / state / name
        if src != dst:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            for f in sorted(dst.glob("*.md")):
                if f.name != T.EPIC_FILE:
                    T._restatus(f, state, status)
        moved = [name]
    else:
        moved = []
        for tid in body["ids"]:
            t = _ticket(tid)
            # A repeatable ticket dropped on the closed lane as done is a run that passed: it
            # returns to the start lane with the run recorded. `cancelled` retires it.
            if T.repeat_result(t, state, status, False):
                T.finish_run(t, "pass", "the owner moved it to %s" % state)
                moved.append(tid)
                continue
            dst = T._dest_dir(state, t.epic) / t.path.name
            if dst == t.path:
                if asked:
                    T._restatus(t.path, state, asked)
                moved.append(tid)
                continue
            shutil.move(str(t.path), str(dst))
            T._restatus(dst, state, status)
            moved.append(tid)
    T._prune_empty_dirs()
    T.write_index(quiet=True)
    return {"moved": moved}


def act_field(body):
    """Set severity, difficulty, category or permits on one or more tickets."""
    field, value = body["field"], body.get("value", "")
    allowed = {"severity": T.SEVERITIES, "difficulty": T.DIFFICULTIES,
               "category": P.CATEGORIES}
    if field in allowed and value not in allowed[field]:
        raise ValueError("%r is not a %s" % (value, field))
    if field == "permits":
        bad = [p for p in value.replace(",", " ").split() if p not in P.PERMITS]
        if bad:
            raise ValueError("not a permit: %s" % ", ".join(bad))
    elif field not in allowed:
        raise ValueError("cannot set %r" % field)
    for tid in body["ids"]:
        t = _ticket(tid)
        t.meta[field] = value
        T._write(t.path, T.render(t.meta, t.body))
    T.write_index(quiet=True)
    return {"set": body["ids"]}


def board_terminal(body):
    """The `--terminal` a launch from the page gets: what the request names, else the
    board's setting: `own` for a pseudoconsole the board reads, else the project's terminal."""
    if body.get("terminal"):
        return body["terminal"]
    return "own" if T.load_board()["terminal"] == "own" else P.terminal


def provider_choices():
    """What a launch on each provider may name: `{provider: {models, efforts, defaultModel,
    defaultEffort, effort}}`, the model keys and the efforts of that provider in this project."""
    return {a: {"models": list(spec["models"]), "efforts": list(spec["efforts"]),
                "modelEfforts": {key: spec["modelEfforts"].get(mid, spec["efforts"])
                                 for key, mid in spec["models"].items()},
                "defaultModel": spec["defaultModel"], "defaultEffort": spec["defaultEffort"],
                "headless": providers.dialect(a) in ("claude", "codex"),
                "effort": bool(project.PROVIDER_FEATURES.get(a, {}).get("effort"))}
            for a, spec in P.providers.items()}


def _model_effort(body, provider=None):
    """The `model` and `effort` the page chose, checked against the provider the launch starts:
    a model or an effort that provider cannot run is refused, never passed to its CLI. Empty
    leaves the provider's default."""
    model, effort = body.get("model") or None, body.get("effort") or None
    P.choose(provider or P.provider, model, effort)
    return model, effort


def _run(fn, args):
    """Run a CLI command; returns (rc, the last line it wrote to stderr)."""
    import contextlib
    import io
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = fn(args)
    lines = [x for x in err.getvalue().strip().splitlines() if x.strip()]
    return rc, (lines[-1] if lines else "")


def act_launch(body):
    """Start a Claude session on the ticket, with its permits as that session's limits.

    A session of this ticket and mode that still has a terminal - running, waiting or
    finished - is not launched again: the page focuses it instead. To start over, the owner
    stops it first."""
    t = _ticket(body["id"])
    mode = body.get("mode") or ("plan" if body.get("planOnly") else "work")
    if mode not in T.MODES:
        raise ValueError("unknown mode %r" % mode)
    provider = body.get("provider") or None
    if provider and provider not in project.PROVIDERS:
        raise ValueError("%r is not an AI provider. One of: %s" % (provider, ", ".join(project.PROVIDERS)))
    model, effort = _model_effort(body, T.launch_provider(t, provider))
    if not body.get("dryRun"):
        live = [s for s in T.live_sessions() if s.get("id") == t.id and s.get("mode") == mode]
        if live:
            return {"rc": 3, "running": True, "mode": mode, "key": live[-1]["key"],
                    "session": T.ticket_session_id(t, mode)}
    args = argparse.Namespace(id=t.id, model=model, effort=effort,
                              terminal=board_terminal(body),
                              dry_run=bool(body.get("dryRun")),
                              note=body.get("note", ""), mode=mode, provider=provider)
    rc, error = _run(T.cmd_work, args)
    out = {"rc": rc, "mode": mode, "brief": "%s/.briefs/%s.md" % (P.todo_dir, t.id),
           "session": T.ticket_session_id(t, mode),
           "provider": T.launch_provider(t, provider),
           "command": " ".join(T._launch_argv(t, mode, model, effort=effort, provider=provider))}
    if rc:
        out["error"] = error or "the launch failed"
    return out


def act_restart(body):
    """Start the next run of a repeatable ticket, and a work session on it unless the page
    asks for none."""
    t = _ticket(body["id"])
    if not t.repeat:
        raise ValueError("%s is not repeatable" % t.id)
    provider = body.get("provider") or None
    model, effort = _model_effort(body, T.launch_provider(t, provider))
    args = argparse.Namespace(id=t.id, model=model, effort=effort, provider=provider,
                              terminal=board_terminal(body),
                              dry_run=bool(body.get("dryRun")), no_fable=False,
                              note=body.get("note", ""),
                              no_launch=bool(body.get("noLaunch")))
    rc, error = _run(T.cmd_restart, args)
    t = _ticket(t.id)
    out = {"rc": rc, "id": t.id, "run": t.run, "launched": not args.no_launch}
    if rc:
        out["error"] = error or "the restart failed"
    return out


def act_remoteControl(body):
    """Remote Control on tickets: `on`, `off`, or `default`, the project's setting. `on`
    reaches a live session that waits at its prompt at once; everything else reaches the
    next launch. A provider without Remote Control refuses it."""
    value = body.get("value") or ""
    targets = [_ticket(tid) for tid in body["ids"]]
    for t in targets:
        if not T.remote_control_available(t):
            raise ValueError("%s launches %s, which has no Remote Control"
                             % (t.id, T.ticket_provider(t) or P.provider))
    typed = []
    for t in targets:
        T.set_remote_control(t, value)
        typed += T.apply_remote_control(t)
    T.write_index(quiet=True)
    return {"set": body["ids"], "value": value, "typed": typed}


def act_repeat(body):
    """Make tickets repeatable with a role, or ordinary again with `none`."""
    role = body.get("role") or "none"
    if role != "none" and role not in T.REPEAT_ROLES:
        raise ValueError("%r is not a role" % role)
    for tid in body["ids"]:
        T.set_repeat(_ticket(tid), role)
    T.write_index(quiet=True)
    return {"set": body["ids"], "role": role}


# ── the watchdog of the manager tickets ────────────────────────────────────
#
# A manager ticket in the active lane must have a running session. Every `WATCHDOG_INTERVAL`
# seconds a daemon thread reads the process table of every project once, and starts a dead
# manager session of the home project (`T.manager_home`) again with `work`, which resumes its
# session id. A manager ticket in any other project is never started. Mechanical only: a
# session is dead when its shell is gone or its Claude has exited (`T.manager_dead`).
#
# It waits before it acts. A session counts as dead only on `WATCHDOG_MISSES` checks in a
# row, never within `WATCHDOG_GRACE` seconds after a launch, and after `WATCHDOG_LIMIT`
# restarts within `WATCHDOG_WINDOW` seconds the watchdog stops for that ticket until its
# session runs again.

WATCHDOG_INTERVAL = 30
WATCHDOG_MISSES = 2
WATCHDOG_GRACE = 60
WATCHDOG_LIMIT = 5
WATCHDOG_WINDOW = 600
_WATCH = {}         # (root, ticket id) -> {"misses", "restarts", "last", "launched", "stopped"}
_WATCH_LOCK = threading.Lock()


def _launch_time(t):
    """When the ticket says its last launch was, as epoch seconds, or 0."""
    import datetime
    import time
    parts = (t.launched or "").split()
    if len(parts) < 3:
        return 0
    try:
        when = datetime.datetime.strptime(" ".join(parts[1:3]), "%Y-%m-%d %H:%M")
    except ValueError:
        return 0
    return time.mktime(when.timetuple())


def watchdog_step(entry, dead, now, launched):
    """One check of one manager ticket. Updates `entry` and says whether to relaunch.

    `launched` is the last launch the watchdog knows of, in epoch seconds."""
    if not dead:
        entry.update(misses=0, stopped=False)
        return False
    if entry.get("stopped"):
        return False
    if now - launched < WATCHDOG_GRACE:
        entry["misses"] = 0
        return False
    entry["misses"] = entry.get("misses", 0) + 1
    if entry["misses"] < WATCHDOG_MISSES:
        return False
    recent = [x for x in entry.get("restarts", []) if now - x < WATCHDOG_WINDOW]
    entry["restarts"] = recent
    if len(recent) >= WATCHDOG_LIMIT:
        entry["stopped"] = True
        return False
    return True


def _watch_project(root, now):
    """Check the manager tickets of one project, the current one, when it is the home project."""
    managers = ([t for t in T.load_all() if t.repeat == T.MANAGER]
                if T.at_manager_home() else [])
    keys = {(str(root), t.id) for t in managers}
    with _WATCH_LOCK:
        for k in [k for k in _WATCH if k[0] == str(root) and k not in keys]:
            del _WATCH[k]
    if not managers:
        return
    sessions = T.live_sessions()
    for t in managers:
        key = (str(root), t.id)
        with _WATCH_LOCK:
            entry = _WATCH.setdefault(key, {"misses": 0, "restarts": [], "count": 0,
                                            "last": "", "launched": 0, "stopped": False})
            if t.state != T.lane("active"):
                entry.update(misses=0, stopped=False)
                continue
            launched = max(entry["launched"], _launch_time(t))
            go = watchdog_step(entry, T.manager_dead(t, sessions), now, launched)
        if not go:
            continue
        rc, error = _run(T.cmd_work, T.relaunch_args(t, board_terminal({})))
        import time
        with _WATCH_LOCK:
            entry["misses"] = 0
            entry["launched"] = now
            entry["restarts"].append(now)
            entry["count"] += 1
            entry["last"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        print("watchdog: %s %s relaunched%s" % (root.name, t.id,
                                                (" - rc %d %s" % (rc, error)) if rc else ""))


# ── the 10-minute check of a manager with no scheduler ─────────────────────
#
# A Claude manager schedules its own 10-minute check (`managercheck.py`). A provider with no
# scheduler of its own (`scheduler` in its features) gets it from here: every
# `managercheck.INTERVAL` seconds the watchdog types `managercheck.wake_text()` into its live
# manager session, never into one that waits for the owner's answer. The first wake comes
# one interval after the watchdog first sees the session.

_WAKES = {}         # (root, session id) -> when the watchdog last woke it, or first saw it


def wake_due(key, now, interval=None):
    """Whether the manager session `key` is due a wake now. Records the time when it is."""
    import managercheck
    interval = managercheck.INTERVAL if interval is None else interval
    with _WATCH_LOCK:
        last = _WAKES.setdefault(key, now)
        if now - last < interval:
            return False
        _WAKES[key] = now
        return True


def _wake_project(root, now):
    """Type the check into the manager sessions of one project, the current one, whose provider
    has no scheduler. Returns the keys of the sessions woken."""
    import managercheck
    def scheduler(provider):
        return bool(project.PROVIDER_FEATURES.get(provider or "claude", {}).get("scheduler"))

    # Only a manager launched on a provider with no scheduler costs a scan of the processes.
    if not T.at_manager_home():
        return []
    managers = {t.id for t in T.load_all()
                if t.repeat == T.MANAGER and t.state == T.lane("active")
                and not scheduler(T.ticket_provider(t) or P.provider)}
    if not managers:
        return []
    woken = []
    for s in T.live_sessions():
        if s.get("id") not in managers or s.get("mode") != "work" or not s.get("claude"):
            continue
        if scheduler(s.get("provider")) or s.get("wait") == "input":
            continue
        if not wake_due((str(root), s["session"]), now):
            continue
        try:
            T.send_session(s, managercheck.wake_text(), ["Enter"])
        except (OSError, ValueError) as exc:
            print("watchdog: %s %s: the check could not be typed: %s" % (root.name, s["id"], exc))
            continue
        woken.append(s["key"])
    return woken


# ── the close of finished sessions ─────────────────────────────────────────
#
# A launched session that is `finished` closes its own terminal (`close --self`, the `Stop`
# hook, the judge). The watchdog tick is the fallback that does not depend on the provider:
# it closes every session `T.autoclose_sessions` names, once it was `finished` on two ticks
# in a row, so a state read in the middle of a change closes nothing.

_FINISHED = {}      # root -> the keys of the sessions that were finished on the last tick


def _autoclose_project(root):
    """Close the finished launched sessions of one project, the current one. Returns the
    keys closed."""
    tickets = T.load_all()
    now = {s["key"]: s for s in T.autoclose_sessions(T.live_sessions(tickets), tickets)}
    with _WATCH_LOCK:
        before = _FINISHED.get(str(root), set())
        _FINISHED[str(root)] = set(now) - before
    closed = []
    for key in sorted(set(now) & before):
        try:
            T.close_session(now[key])
        except ValueError as exc:
            print("autoclose: %s: %s" % (root.name, exc))
            continue
        closed.append(key)
        print("autoclose: %s %s closed" % (root.name, key))
    return closed


def watchdog_tick(now=None):
    """One check of every project the server serves. Never raises."""
    import time
    now = time.time() if now is None else now
    for name, root in projects().items():
        try:
            tok = project.use(root)
        except Exception as exc:                        # noqa: BLE001 - the thread must live
            print("watchdog: %s: %s" % (name, exc))
            continue
        try:
            for step in (lambda: _watch_project(root, now), lambda: _wake_project(root, now),
                         lambda: _autoclose_project(root)):
                try:
                    step()
                except Exception as exc:                # noqa: BLE001 - the thread must live
                    print("watchdog: %s: %s: %s" % (name, type(exc).__name__, exc))
        finally:
            project.reset(tok)


# ── the start of the manager when the board opens ─────────────────────────
#
# One manager serves every project, and it belongs to the home project, the Octopus AI
# project (`T.manager_home`), whichever project the board shows. Each manager ticket of the home project in the active lane with no live
# session starts again. With none there, the board starts the home project's manager, filed
# from the template when it has none. A manager the owner retired is never started. Each
# launch counts as one for the watchdog's grace.

def _home_project():
    """`(name, root)` of the manager's home project, or `("", None)` when no registered
    project is it."""
    root = T.manager_home()
    return (T.manager_home_name(), root) if root is not None else ("", None)


def _start_manager(name, root, t, now):
    """Launch the `work` session of manager `t` of project `name`, the current project."""
    rc, error = _run(T.cmd_work, T.relaunch_args(t, board_terminal({})))
    with _WATCH_LOCK:
        entry = _WATCH.setdefault((str(root), t.id), {"misses": 0, "restarts": [], "count": 0,
                                                      "last": "", "launched": 0,
                                                      "stopped": False})
        entry.update(misses=0, launched=now)
    print("manager: %s %s started%s" % (name, t.id, (" - rc %d %s" % (rc, error)) if rc else ""))
    return rc


def autostart_managers(now=None):
    """Start the home project's manager when the board opens, unless it runs. Returns
    `[(project, id)]` of the managers launched. Never raises."""
    import time
    now = time.time() if now is None else now
    started = []
    home, root = _home_project()
    if root is None:
        print("manager: no registered project is the Octopus AI project")
        return started
    tok = project.use(root)
    try:
        managers = [t for t in T.load_all()
                    if t.repeat == T.MANAGER and t.state == T.lane("active")]
        sessions = T.live_sessions() if managers else []
        for t in managers:
            if not T.manager_running(t, sessions) and not _start_manager(home, root, t, now):
                started.append((home, t.id))
    except Exception as exc:                            # noqa: BLE001 - the board must open
        print("manager: %s: %s: %s" % (home, type(exc).__name__, exc))
        managers = [None]
    finally:
        project.reset(tok)
    if managers:
        return started
    tok = project.use(root)
    try:
        t, created = T.ensure_manager()
        if created:
            print("manager: %s %s filed from the template" % (home, t.id))
        if t.state != T.lane("closed") and not T.manager_running(t, T.live_sessions()):
            if not _start_manager(home, root, t, now):
                started.append((home, t.id))
    except Exception as exc:                            # noqa: BLE001 - the board must open
        print("manager: %s: %s: %s" % (home, type(exc).__name__, exc))
    finally:
        project.reset(tok)
    return started


def watchdog_view(root):
    """`{id: {"restarts", "last", "stopped"}}` of one project, for the page."""
    with _WATCH_LOCK:
        return {k[1]: {"restarts": v["count"], "last": v["last"], "stopped": v["stopped"]}
                for k, v in _WATCH.items() if k[0] == str(root)}


def start_watchdog(interval=WATCHDOG_INTERVAL):
    """The daemon thread that runs `watchdog_tick` every `interval` seconds."""
    import time

    def loop():
        while True:
            time.sleep(interval)
            try:
                watchdog_tick()
            except Exception as exc:                    # noqa: BLE001 - the thread must live
                print("watchdog: %s: %s" % (type(exc).__name__, exc))

    th = threading.Thread(target=loop, name="octopus-watchdog", daemon=True)
    th.start()
    return th


def act_open(body):
    """Open the ticket file in whatever the machine uses for markdown."""
    t = _ticket(body["id"])
    try:
        os.startfile(str(t.path))                       # noqa: S606 - Windows shell verb
    except AttributeError:
        import posixterm                                # `open` on macOS, `xdg-open` on Linux
        posixterm.open_file(t.path)
    return {"opened": t.path.name}


def act_focus(body):
    """Bring the terminal of one session to the front."""
    s = T.find_session(body["id"], body.get("mode") or None)
    if s is None:
        raise KeyError(body["id"])
    return {"focused": bool(T.focus_session(s)), "key": s["key"]}


def act_manager(body):
    """The board's Manager button. The manager of the home project, whose `work` session runs,
    comes to the front, whichever project the page shows; an own pseudoconsole has no window, so
    the page shows it. With none running, the manager starts as the board starts it when it
    opens. Returns the project and the ticket id of the manager, and whether it `started`."""
    import time
    name, root = _home_project()
    if root is None:
        raise ValueError("no manager: no registered project is the Octopus AI project")
    tok = project.use(root)
    try:
        tickets = T.load_all()
        ids = {t.id for t in tickets if t.repeat == T.MANAGER}
        hit = next((s for s in (T.live_sessions(tickets) if ids else [])
                    if s["id"] in ids and s["mode"] == "work" and s.get("claude")), None)
        if hit is not None:
            own = bool(hit.get("own"))
            return {"project": name, "id": hit["id"], "started": False, "own": own,
                    "focused": not own and bool(T.focus_session(hit)), "noSnapshot": True}
    finally:
        project.reset(tok)
    started = autostart_managers(time.time())
    if not started:
        raise ValueError("no manager started: every manager is retired, or its launch failed "
                         "(the board server's console names the cause)")
    name, tid = started[0]
    return {"project": name, "id": tid, "started": True, "own": False, "focused": False,
            "noSnapshot": True}


def _public(sessions):
    """The sessions as the page gets them: without `pty`, the host record that carries the
    pseudoconsole host's token."""
    return [{k: v for k, v in s.items() if k != "pty"} for s in sessions]


# The terminal window polls `screen` every 150 ms to 1 s and sends every key. A full session
# scan per call (~60 ms) to find one shell was most of each call, so the session it resolves
# is kept for `TERM_TTL` seconds, as long as its shell still runs.
_TERM_CACHE = {}
_TERM_LOCK = threading.Lock()
TERM_TTL = 2.0


def _term_session(body):
    """The session that `id` and `mode` name, for the calls of the terminal window.

    Resolved by `T.find_session` at most once per `TERM_TTL` seconds and per window. Within
    that time a cached session is used while its shell is the same process (`created_at`,
    one `OpenProcess`). Its `running`, `wait` and `why` may therefore be up to `TERM_TTL`
    seconds old; the page shows them only in the state pill."""
    import time
    import winproc
    ident, mode = body["id"], body.get("mode") or None
    key = (str(P.ROOT), ident, mode)
    now = time.monotonic()
    with _TERM_LOCK:
        hit = _TERM_CACHE.get(key)
    if hit is not None and now - hit[0] < TERM_TTL:
        s = hit[1]
        if s["shell"] and winproc.created_at(s["shell"]) == s["created"]:
            return key, s
    s = T.find_session(ident, mode)
    with _TERM_LOCK:
        if s is None:
            _TERM_CACHE.pop(key, None)
        else:
            _TERM_CACHE[key] = (now, s)
    if s is None:
        raise KeyError(ident)
    return key, s


def _term_call(body, fn, *args):
    """`fn(s, *args)` on the session of the terminal window. An `OSError` drops the cached
    session, so the next call resolves it again."""
    key, s = _term_session(body)
    try:
        return s, fn(s, *args)
    except OSError:
        with _TERM_LOCK:
            _TERM_CACHE.pop(key, None)
        raise


def act_screen(body):
    """The screen of one session's terminal, for the popup on the page. Polled every 150 ms
    after a key and once a second otherwise, so it carries no snapshot, and the session comes
    from `_term_session`: `running`, `wait` and `why` may be up to `TERM_TTL` seconds old."""
    s, screen = _term_call(body, T.screen_session)
    return {"key": s["key"], "own": bool(s.get("own")), "running": s["running"],
            "wait": s["wait"], "why": s["why"], "doing": s.get("doing") or "",
            "title": s.get("title") or "", "screen": screen, "noSnapshot": True}


def act_send(body):
    """Type text and named keys into one session's terminal."""
    keys = body.get("keys") or []
    if not isinstance(keys, list):
        raise ValueError("keys is a list of key names")
    s, written = _term_call(body, T.send_session, body.get("text") or "", keys)
    return {"key": s["key"], "written": written, "noSnapshot": True}


def act_log(body):
    """The scrolled output of an own session, from an offset."""
    s, r = _term_call(body, T.log_session, body.get("offset") or 0)
    r["key"] = s["key"]
    r["noSnapshot"] = True
    return r


def act_close(body):
    """Close one session's terminal. A running session needs `force`, which the page only
    sends after the owner confirms."""
    s = T.find_session(body["id"], body.get("mode") or None)
    if s is None:
        raise KeyError(body["id"])
    force = bool(body.get("force"))
    if s["running"] and not force:
        raise ValueError("%s is still running" % s["key"])
    return {"closed": [s["key"]], "rc": T.close_session(s, force=force)}


def act_closeFinished(body):
    """Close every terminal of the project whose session is finished (`T.wait_state`).
    A session that waits for the owner's answer, for an outside event, for its subagents or
    for a blocker stays open."""
    done = []
    for s in T.finished_sessions(T.live_sessions()):
        T.close_session(s)
        done.append(s["key"])
    return {"closed": done}


def act_allSessions(body):
    """The sessions of every project the server serves, for the Sessions view."""
    out = []
    for name, root in projects().items():
        tok = project.use(root)
        try:
            try:
                sessions = _public(T.live_sessions())
            except Exception:                           # noqa: BLE001 - one project never stops the rest
                sessions = []
            out.append({"name": name, "title": P.title, "sessions": sessions})
        finally:
            project.reset(tok)
    return {"projects": out, "noSnapshot": True}


def act_reevaluate(body):
    """The board's Reload: decide again what every session of every project the server
    serves waits for, with the detection the poll uses (`T.live_sessions`, `T.wait_state`),
    and retry what did not fire: a judge that died without a verdict starts again. Each
    session carries its state, `why` and `signals`. The snapshot that follows is the
    current project's board, drawn from the same detection."""
    out = []
    for name, root in projects().items():
        tok = project.use(root)
        try:
            try:
                sessions = _public(T.live_sessions(recheck=True))
                error = ""
            except Exception as exc:                    # noqa: BLE001 - one project never stops the rest
                sessions, error = [], "%s: %s" % (type(exc).__name__, exc)
            out.append({"name": name, "title": P.title, "sessions": sessions, "error": error})
        finally:
            project.reset(tok)
    return {"projects": out}


def act_group(body):
    """Create, rename, delete a group, or put tickets in one.

    The map store rewrites `TODO/GROUPS.md` on every one of them, so the page and the file
    never disagree."""
    op = body.get("op")
    if op == "create":
        T.group_create(body.get("name"), body.get("title"), body.get("description"))
    elif op == "rename":
        T.group_rename(body.get("name"), body.get("to"))
    elif op == "delete":
        T.group_delete(body.get("name"), body.get("into"))
    elif op == "assign":
        ids = [_ticket(i).id for i in body.get("ids") or []]
        T.group_assign(ids, body.get("name") or "")
        if P.groups_store == "files":
            where = {t.id: t.group for t in T.load_all() if t.id in ids}
        else:
            # A ticket whose own `group:` field names a group returns to that group the
            # moment its explicit membership goes, so the page is told where each one landed.
            members, _ung = T.todo_groups(T.load_all(), T.load_groups())
            where = {t.id: name for name, ts in members.items() for t in ts}
        return {"op": op, "name": body.get("name"),
                "placed": {i: where.get(i, "") for i in ids}}
    else:
        raise ValueError("unknown group operation %r" % op)
    return {"op": op, "name": body.get("name")}


# What a drafting session is asked for. It answers with JSON and nothing else, so the page
# can fill the form and the owner can correct it before the ticket is filed.
DRAFT_PROMPT = """You turn a note into %s for the %s TODO tree.

Answer with %s and no other text. The keys of a ticket are:
  title        one line, no ticket id, at most 90 characters. A bug starts with "Bug: "
               and says what is wrong. A feature starts with "Feature: " and says what
               the program should do, as a positive statement
  severity     %s - what the user, the saved data or another machine gets wrong
  difficulty   %s - what the fix costs; unknown when the note's own question is unanswered
  category     %s - %s
  source       a file and line if the note names one, otherwise an empty string
  tags         a short space-separated list, or an empty string
  description  one or two paragraphs: what is wrong for a bug, what the program should
               do for a feature
  evidence     the file and line read, or the command run, or an empty string
%s
Write every word in Simplified Technical English: short sentences, active voice, one
meaning per word. Name a function with its parameters and its return type.

THE NOTE:
%s
"""

DRAFT_SPLIT = """
Give one ticket unless the note describes problems that need separate work sessions. Then
give one ticket per problem, and make each ticket complete without the others.
"""

EDIT_SPLIT = """
The first object is the rewritten ticket. Give no other object unless the ticket describes
problems that need separate work sessions. Then keep one problem in the first object, give
one new ticket object per other problem, and make each ticket complete without the others.
"""


# What an editing session is asked for: the same keys as a draft, from a ticket that exists.
# Its first answer replaces only the fields and the two sections the draft keys name.
EDIT_PROMPT = """You rewrite one existing ticket of the %s TODO tree so that it is correct,
complete and clear. Keep every fact the ticket states. Correct a field only when the ticket
itself shows it is wrong.

Answer with %s and no other text. The keys of a ticket are:
  title        one line, no ticket id, at most 90 characters. A bug starts with "Bug: "
               and says what is wrong. A feature starts with "Feature: " and says what
               the program should do, as a positive statement
  severity     %s - what the user, the saved data or another machine gets wrong
  difficulty   %s - what the fix costs; unknown when the ticket's own question is unanswered
  category     %s - %s
  source       a file and line if the ticket names one, otherwise an empty string
  tags         a short space-separated list, or an empty string
  description  the new text of the first section, one or two paragraphs: what is wrong
               for a bug, what the program should do for a feature
  evidence     the new text of the "Evidence" section, or an empty string
%s
Write every word in Simplified Technical English: short sentences, active voice, one
meaning per word. Name a function with its parameters and its return type.

THE TICKET:
title: %s
severity: %s
difficulty: %s
category: %s
source: %s
tags: %s

%s
"""

_SECTION = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)


def splice_sections(body, description, evidence, title=""):
    """`body` with its first section replaced by `description`, and the text of its
    `## Evidence` section by `evidence`. The first section is `## What is wrong` or `## What
    it should do`, and its heading becomes the one `title` (str) names:
    `T.body_section(title)`. Without either section, the text before the first heading is
    the description, and the section takes its place. Without an Evidence section, one
    follows the first section. An empty `evidence` changes nothing. Every other section
    keeps its text byte for byte. Returns the new body."""
    body = (body or "").strip("\n")
    heads = list(_SECTION.finditer(body))
    ends = [m.start() for m in heads[1:]] + [len(body)]
    spans = {}
    for m, e in zip(heads, ends):
        spans.setdefault(m.group(1).strip().lower(), (m.end(), e))
    description, evidence = (description or "").strip(), (evidence or "").strip()
    sep = lambda b: "\n\n" if b < len(body) else ""     # noqa: E731 - blank line before the next head
    parts = []                                       # (start, end, new text)
    ev = spans.get("evidence")
    if ev and evidence:
        parts.append((ev[0], ev[1], "\n\n%s%s" % (evidence, sep(ev[1]))))
    add_ev = "## Evidence\n\n%s" % evidence if evidence and not ev else ""
    first = [m for m in heads if m.group(1).strip().lower()
             in (T.SECTION_BUG.lower(), T.SECTION_FEATURE.lower())][:1]
    if first:
        a, b = first[0].start(), spans[first[0].group(1).strip().lower()][1]
    else:
        a, b = 0, heads[0].start() if heads else len(body)
    text = "## %s\n\n%s" % (T.body_section(title), description)
    text += ("\n\n" + add_ev if add_ev else "") + sep(b)
    parts.append((a, b, text))
    for a, b, text in sorted(parts, reverse=True):
        body = body[:a] + text + body[b:]
    return body


def _norm_draft(d):
    """One drafted ticket with every key present and every value legal."""
    d = d if isinstance(d, dict) else {}
    cat = d.get("category") or d.get("kind")
    cat = T.LEGACY_KINDS.get(cat, cat)
    return {
        "title": str(d.get("title") or "")[:200],
        "severity": d.get("severity") if d.get("severity") in T.SEVERITIES else "Medium",
        "difficulty": (d.get("difficulty") if d.get("difficulty") in T.DIFFICULTIES
                       else "unknown"),
        "category": cat if cat in P.CATEGORIES else T.CATEGORY_CODE,
        "source": str(d.get("source") or ""),
        "tags": str(d.get("tags") or ""),
        "description": str(d.get("description") or ""),
        "evidence": str(d.get("evidence") or ""),
    }


def _json_in(text):
    """The JSON value in a session's answer: from the first `[` or `{` to the last matching
    `]` or `}`, with any text around it. Raises ValueError when there is none."""
    text = (text or "").strip()
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    if not starts:
        raise ValueError("the answer held no JSON: %s" % text[:200])
    a = min(starts)
    b = text.rfind("]" if text[a] == "[" else "}")
    if b < a:
        raise ValueError("the answer held no JSON: %s" % text[:200])
    try:
        return json.loads(text[a:b + 1])
    except ValueError as exc:
        raise ValueError("the answer was not JSON: %s" % exc)


def parse_drafts(text):
    """The drafted tickets in a session's answer: a JSON array of objects, or one object,
    with any text around it. Raises ValueError when the answer holds neither."""
    d = _json_in(text)
    rows = d if isinstance(d, list) else [d]
    drafts = [_norm_draft(x) for x in rows if isinstance(x, dict)]
    if not drafts:
        raise ValueError("the answer held no ticket")
    return drafts


def _ask_provider(prompt, body, timeout):
    """Use the selected provider and this project's model, effort and executable settings."""
    pid = body.get("provider") or P.provider
    model, effort = P.choose(pid, body.get("model"), body.get("effort"))
    p = dict(providers.get(pid), command=P.providers[pid]["command"])
    return providers.answer(p, prompt, model, effort, timeout)


def act_draft(body):
    """Turn the form's text into ticket fields with the selected headless CLI. One call serves New task and
    Edit. With `split`, the text may become several tickets. Returns `{"drafts": [...]}`,
    one draft or more.

    The session runs outside the project. It reads the supplied text and answers.

    `text` is the note in New task. With `ticket` (the edit form's fields) it is the whole
    body of that ticket, and the session rewrites the ticket: the first draft's description
    and evidence replace only the first section (What is wrong, or What it should do for a
    Feature: title) and the Evidence section, so the Plan, the Runs
    and the Results stay, and that draft carries the new body in `body`. Any further draft is
    a new ticket split off it. Nothing is written: the owner reads the form and saves it."""
    ticket = body.get("ticket")
    ticket = ticket if isinstance(ticket, dict) else None
    text = (body.get("text") or (ticket or {}).get("body") or "").strip()
    if not text:
        raise ValueError("the ticket needs a body" if ticket else "write the note first")
    split = bool(body.get("split"))
    shape = "a JSON array of ticket objects" if split else "ONE JSON object"
    cats = "; ".join("%s is %s" % (k, v) for k, v in P.CATEGORY_MEANING.items())
    scales = ("/".join(T.SEVERITIES), "/".join(T.DIFFICULTIES), "/".join(P.CATEGORIES), cats)
    if ticket:
        prompt = EDIT_PROMPT % (
            P.name, shape, *scales, EDIT_SPLIT if split else "",
            *(str(ticket.get(k) or "") for k in ("title", "severity", "difficulty",
                                                 "category", "source", "tags")), text)
    else:
        prompt = DRAFT_PROMPT % (
            "one or more tickets" if split else "one ticket", P.name, shape, *scales,
            DRAFT_SPLIT if split else "", text)
    drafts = parse_drafts(_ask_provider(prompt, body, 240))
    drafts = drafts if split else drafts[:1]
    if ticket:
        d = drafts[0]
        if not d["description"].strip():
            # A splice with no description would empty the first section.
            raise ValueError("the AI returned no description · the ticket is unchanged")
        d["body"] = splice_sections(text, d["description"], d["evidence"],
                                    d["title"] or str(ticket.get("title") or ""))
    return {"drafts": drafts}


# What a grouping session is asked for. It sees every group and every open ticket, and
# answers with JSON the page shows as a preview; nothing is written until the owner applies.
GROUP_PROMPT = """You sort the open tickets of the %s TODO tree into groups. A group holds
open tickets that one work session can do together: the same module, the same feature, or
tickets that block each other.

THE GROUPS NOW (name, title, member tickets):
%s

THE OPEN TICKETS (id | title | tags | group slug | related | blocked-by | grouped):
%s

%s
Write names%s. Write every title and description in Simplified Technical English: short
sentences, active voice, one meaning per word.
"""

GROUP_ONE = """THE OWNER'S NOTE - what the new group is for:
%s

Answer with ONE JSON object and no other text:
  {"name": "...", "title": "...", "description": "...", "ids": ["T0001", ...]}
name is the new group, which no group above uses. ids are the open tickets above that belong
in it, grouped or not.
"""

GROUP_ALL = """Place every ticket that is in no group ("grouped: no") into an existing group or
a new one. Keep each group between 5 and 10 tickets. Do not move a ticket that is already
in a group. Answer with ONE JSON object and no other text:
  {"groups": [{"name": "...", "title": "...", "description": "...", "new": true,
               "ids": ["T0001", ...]}]}
For an existing group, name is its name above, new is false, and ids are only the tickets
you add to it. For a new group, new is true and ids are all its tickets.
"""


def _group_state():
    """(open non-repeatable tickets, {group: member ids}, {group: title}) of the current
    project, read the way the snapshot reads them."""
    objs = T.load_all()
    open_t = [t for t in objs if t.state in P.OPEN_STATES and not t.repeat]
    if P.groups_store == "files":
        table = T.group_table(objs)
        members = {g["slug"]: list(g["tickets"]) for g in table["groups"]}
        titles = {g["slug"]: g["title"] for g in table["groups"]}
    else:
        grouped, _loose = T.todo_groups(objs, T.load_groups())
        members = {name: [t.id for t in ts] for name, ts in grouped.items()}
        titles = {}
    return open_t, members, titles


def parse_group_drafts(text, known_ids, known_groups):
    """The proposed groups in a grouping session's answer: one object, `{"groups": [...]}`,
    or a list. Each proposal is `{"name", "title", "description", "new", "ids"}`. Unknown
    ids go, an id proposed twice stays with its first group, and `new` says whether the
    name is not yet a group. Raises ValueError when no usable proposal is left."""
    d = _json_in(text)
    if isinstance(d, dict) and isinstance(d.get("groups"), list):
        rows = d["groups"]
    else:
        rows = d if isinstance(d, list) else [d]
    known = {i.upper(): i for i in known_ids}
    seen, out = set(), []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = " ".join(str(row.get("name") or "").split())
        if name and P.groups_store == "files" and name not in known_groups:
            name = T.slug(name, words=6)
        if not name:
            continue
        ids = []
        for raw in row.get("ids") or []:
            tid = known.get(str(raw).strip().upper())
            if tid and tid not in seen:
                seen.add(tid)
                ids.append(tid)
        if not ids:
            continue
        out.append({"name": name, "title": str(row.get("title") or "")[:200],
                    "description": str(row.get("description") or ""),
                    "new": name not in known_groups, "ids": ids})
    if not out:
        raise ValueError("the answer proposed no group with a known ticket")
    return out


def act_groupDraft(body):
    """Ask the selected headless CLI for groups. `mode: "one"` with `text` proposes one new
    group for the owner's note; `mode: "all"` places every ticket in no group. Returns
    `{"proposals": [...]}` and writes nothing: `groupApply` does."""
    mode = body.get("mode")
    if mode not in ("one", "all"):
        raise ValueError("unknown grouping mode %r" % mode)
    note = (body.get("text") or "").strip()
    if mode == "one" and not note:
        raise ValueError("write what the group is for first")
    open_t, members, titles = _group_state()
    grouped = {i for ids in members.values() for i in ids}
    if mode == "all" and all(t.id in grouped for t in open_t):
        raise ValueError("every open ticket is already in a group")
    title_of = {t.id: t.title for t in open_t}
    glines = ["- %s%s: %s" % (name, " (%s)" % titles[name] if titles.get(name) else "",
                              ", ".join("%s %s" % (i, title_of.get(i, "")) for i in ids)
                              or "no ticket")
              for name, ids in members.items()] or ["(none)"]
    tlines = ["%s | %s | %s | %s | %s | %s | %s" % (
        t.id, t.title, " ".join(t.tags) or "-", t.group or "-",
        " ".join(t.related) or "-", " ".join(t.blocked_by) or "-",
        "yes" if t.id in grouped else "no") for t in open_t] or ["(none)"]
    ask = GROUP_ONE % note if mode == "one" else GROUP_ALL
    names = (" as lowercase slugs: letters, digits and dashes" if P.groups_store == "files"
             else " as short plain words")
    prompt = GROUP_PROMPT % (P.name, "\n".join(glines), "\n".join(tlines), ask, names)
    text = _ask_provider(prompt, body, 240 if mode == "one" else 600)
    proposals = parse_group_drafts(text, [t.id for t in open_t], set(members))
    return {"proposals": proposals[:1] if mode == "one" else proposals, "noSnapshot": True}


def act_groupApply(body):
    """Apply the proposals the owner kept: create each new group, then put its ids in it.
    Returns `{"created": [names], "placed": {id: group}}`."""
    rows = body.get("proposals")
    if not isinstance(rows, list) or not rows:
        raise ValueError("no proposal to apply")
    created, placed = [], {}
    for row in rows:
        row = row if isinstance(row, dict) else {}
        name = " ".join(str(row.get("name") or "").split())
        if not name:
            raise ValueError("a proposed group has no name")
        ids = [_ticket(str(i)).id for i in row.get("ids") or []]
        if name not in T.group_names():
            T.group_create(name, row.get("title") or None, row.get("description") or None)
            created.append(name)
        if ids:
            T.group_assign(ids, name)
            placed.update({i: name for i in ids})
    return {"created": created, "placed": placed}


def act_new(body):
    """File a ticket in the start lane from the fields the page holds."""
    title = (body.get("title") or "").strip()
    if not title:
        raise ValueError("a ticket needs a title")
    sev = body.get("severity") or "Medium"
    if sev not in T.SEVERITIES:
        raise ValueError("%r is not a severity" % sev)
    diff = body.get("difficulty") or "unknown"
    if diff not in T.DIFFICULTIES:
        raise ValueError("%r is not a difficulty" % diff)
    category = (body.get("category") or "").strip()
    if category and category not in P.CATEGORIES:
        raise ValueError("%r is not a category" % category)
    permits = [p for p in (body.get("permits") or "").replace(",", " ").split() if p]
    bad = [p for p in permits if p not in P.PERMITS]
    if bad:
        raise ValueError("not a permit: %s" % ", ".join(bad))
    group = (body.get("group") or "").strip()

    tid = "T%04d" % T.next_id()
    args = argparse.Namespace(
        title=title, severity=sev, source=(body.get("source") or "").strip(),
        difficulty=diff, category=category or None, status=None, created=None,
        claim=None, blocked_by=None,
        permits=" ".join(permits), tags=(body.get("tags") or "").strip(),
        description=(body.get("description") or "").strip(),
        evidence=(body.get("evidence") or "").strip(),
        state=T.lane("start"), epic="", parent=(body.get("parent") or "").strip() or None,
        group=group if P.groups_store == "files" else None)
    repeat = (body.get("repeat") or "").strip()
    if repeat and repeat not in T.REPEAT_ROLES:
        raise ValueError("%r is not a role" % repeat)
    args.repeat = repeat or None
    if T.cmd_new(args) != 0:
        raise ValueError("the ticket was not written")

    t = T.find(T.load_all(), tid)
    if t is None:
        raise ValueError("the ticket was written and cannot be read back")
    if group and P.groups_store == "map":
        T.group_assign([tid], group)
    return {"id": tid, "path": t.path.relative_to(P.ROOT).as_posix(), "group": group}


def act_ticket(body):
    """One ticket for the edit form: the fields it edits, the body, and the hash the edit
    must name so a change made meanwhile is not overwritten."""
    t = _ticket(body["id"])
    if P.groups_store == "files":
        group = t.group
    else:
        members, _ung = T.todo_groups(T.load_all(), T.load_groups())
        group = next((name for name, ts in members.items() if any(x.id == t.id for x in ts)),
                     "")
    return {"id": t.id, "title": t.title, "severity": t.severity,
            "difficulty": t.difficulty, "category": t.category, "source": t.source,
            "permits": " ".join(t.meta.get("permits", "").replace(",", " ").split()),
            "tags": " ".join(t.tags), "group": group, "repeat": t.repeat,
            "body": t.body.strip(), "base": T.ticket_hash(t), "noSnapshot": True}


def act_edit(body):
    """Save the edit form onto one ticket. The file keeps its name."""
    fields = {k: body[k] for k in T.EDIT_FIELDS if k in body}
    t = T.edit_ticket(_ticket(body["id"]).id, fields, body.get("body") or "",
                      body.get("base") or "")
    return {"id": t.id, "path": t.path.relative_to(P.ROOT).as_posix()}


def act_chat(body):
    """Open a read-only codebase chat with the project's selected provider."""
    import uuid
    provider = body.get("provider") or P.provider
    model, effort = _model_effort(body, provider)
    sid = str(uuid.uuid4())
    argv = T.chat_argv(model, effort, sid, provider)
    if not body.get("dryRun"):
        title = "%s - chat" % P.title
        terminal = body.get("terminal") or P.terminal
        if terminal not in project.TERMINALS:
            terminal = P.terminal
        rc, error = _run(lambda _a: T._open_terminal(T._shell_line(
            argv, title, providers.launch_env(provider)), title,
                                                     terminal), None)
        if rc:
            raise ValueError(error or "the chat did not start")
    return {"session": sid, "provider": provider, "argv": argv,
            "command": " ".join(argv), "noSnapshot": True}


def act_board(body):
    """Remember the board's view, terminal kind, focus behaviour, filter row, whether the
    Sessions view lists the other projects, and the lane columns' order, widths and
    collapsed lanes, in the project."""
    return {"board": T.save_board(view=body.get("view"), shut=body.get("shut"),
                                  sort=body.get("sort"),
                                  terminal=body.get("terminal"), focus=body.get("focus"),
                                  filters=body.get("filters"), others=body.get("others"),
                                  lane_order=body.get("laneOrder"),
                                  lane_width=body.get("laneWidth"),
                                  lane_shut=body.get("laneShut"))}


def act_groupRun(body):
    """Start a session on every start-lane ticket of a group: `plan` when the difficulty is
    unknown, `work` otherwise."""
    name = body.get("name") or ""
    rows = T.group_members(name)
    if rows is None:
        raise KeyError(name)
    args = argparse.Namespace(note=body.get("note", ""), no_fable=False,
                              terminal=board_terminal(body),
                              dry_run=bool(body.get("dryRun")))
    start = T.lane("start")
    started = [t.id for t in rows if t.state == start]
    T.run_group(name, args)
    return {"name": name, "launched": started,
            "skipped": [t.id for t in rows if t.state != start]}


def act_lanes(body):
    """Add, rename or remove a lane of the current project."""
    import admin
    op = body.get("op")
    root = P.ROOT
    if op == "add":
        statuses = body.get("statuses") or []
        if isinstance(statuses, str):
            statuses = [x.strip() for x in statuses.split(",") if x.strip()]
        admin.lane_add(root, body.get("dir", "").strip(), body.get("label"),
                       body.get("after") or None, statuses, body.get("color"))
    elif op == "rename":
        admin.lane_rename(root, body.get("dir"), body.get("label") or None,
                          body.get("newDir") or None)
    elif op == "remove":
        admin.lane_remove(root, body.get("dir"))
    else:
        raise ValueError("unknown board operation %r" % op)
    # The config changed on disk: the next read loads it again.
    tok = project.use(root)
    try:
        T.write_index(quiet=True)
    finally:
        project.reset(tok)
    return {"op": op, "lanes": project.load(root).lanes}


# ── the Project view ───────────────────────────────────────────────────────

def _shown_path(path):
    """A ruleset file as the page names it: inside the project, else inside Octopus AI."""
    for base, pre in ((P.ROOT, ""), (project.OCTOPUS, "Octopus AI/")):
        try:
            return pre + Path(path).resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    return str(path)


def project_view():
    """Every setting of the current project, for the Project view: `GET /api/project`."""
    import admin
    import packreg
    import skills
    r, cfg = P.RULES, P.cfg
    names = list(r["rulesets"])
    packs = []
    for p in packreg.available():
        if not p["dir"]:
            continue
        on = p["name"] in names
        packs.append({"name": p["name"], "title": p["title"], "source": p["source"],
                      "requires": list(p["requires"]), "installed": on,
                      "dependents": [n for n in admin.dependents(names, p["name"])
                                     if n != p["name"]] if on else []})
    counts = T.permit_counts()
    board = T.load_board()
    root = P.ROOT.resolve()
    return {
        "name": P.name, "board": _current_name(), "slug": P.slug,
        "description": P.description,
        "path": str(P.ROOT), "config": "%s/%s" % (P.config_dir, project.PROJECT_FILE),
        "registered": any(Path(x["path"]).resolve() == root for x in project.load_registry()),
        "rulesets": [{"name": n, "title": r["contributions"].get(n, {}).get("title", ""),
                      "source": _shown_path(r["sources"][n]),
                      "requires": list(r["requires"].get(n, [])),
                      "pack": project.pack_dir(n) is not None} for n in names],
        "packs": packs,
        "optionalRules": admin.optional_rules(P.ROOT),
        "skills": {"installed": skills.listing(P.ROOT), "libraries": skills.libraries(),
                   "record": "%s/%s" % (P.config_dir, skills.RECORD)},
        "lanes": P.lanes,
        "models": dict(r["models"]), "efforts": list(r["efforts"]),
        "defaultModel": r["defaultModel"], "defaultEffort": r["defaultEffort"],
        "provider": P.provider, "providerModels": provider_models(),
        "terminal": {"ruleset": P.terminal, "board": board["terminal"],
                     "focus": board["focus"],
                     "boardFile": "%s/.board.json" % P.todo_dir},
        "permits": [{"name": n, "grants": P.PERMITS[n], "tickets": counts.get(n, 0),
                     "tester": n in P.TESTER_PERMITS} for n in sorted(P.PERMITS)],
        "groups": {"store": P.groups_store, "required": P.groups_required,
                   "autoGroups": list(P.auto_groups)},
        "categories": {"defaults": dict(project.DEFAULT_CATEGORIES),
                       "own": dict(cfg.get("categories") or {}),
                       "all": dict(P.CATEGORY_MEANING)},
        "problems": list(P.problems),
        "installed": admin.status(P.ROOT, out=lambda *_a: None),
        "vcs": {"kind": P.vcs, "pull": P.vcs_pull, "pulls": list(_vcs.PULL_ARGS)},
    }


def _provider_own(provider):
    """The project's own models, efforts and defaults of `provider`: Claude's top-level keys,
    any other provider's `providers.<id>`."""
    keys = ("models", "efforts", "defaultModel", "defaultEffort")
    if provider == project.DEFAULT_PROVIDER:
        src = P.RULES
    else:
        src = P.provider_settings.get(provider) or {}
        src = src if isinstance(src, dict) else {}
    empty = {"models": {}, "efforts": [], "defaultModel": "", "defaultEffort": ""}
    return {k: copy.deepcopy(src.get(k) or empty[k]) for k in keys}


def provider_models():
    """Each provider's models and efforts for the Project view: the provider's own from global
    settings (`inherited`), the project's (`own`), and what a launch offers (`offered`)."""
    out = {}
    for a, spec in P.providers.items():
        prov = providers.load().get(a) or {}
        out[a] = {"label": providers.label(a),
                  "effort": bool(project.PROVIDER_FEATURES.get(a, {}).get("effort")),
                  "inherited": {"models": dict(prov.get("models") or {}),
                                "efforts": list(prov.get("efforts") or []),
                                "defaultModel": prov.get("defaultModel") or "",
                                "defaultEffort": prov.get("defaultEffort") or ""},
                  "own": _provider_own(a),
                  "offered": {"models": dict(spec["models"]), "efforts": list(spec["efforts"]),
                              "defaultModel": spec["defaultModel"],
                              "defaultEffort": spec["defaultEffort"]}}
    return out


def _set_provider_models(root, provider, body):
    """Write the project's own models, efforts and defaults of `provider`. Claude's are the
    top-level keys; another provider's are `providers.<id>`. An empty value removes the key."""
    import admin
    keys = ("models", "efforts", "defaultModel", "defaultEffort")
    if provider == project.DEFAULT_PROVIDER:
        admin.set_settings(root, **{k: (body[k] or None) for k in keys if k in body})
        return
    every = copy.deepcopy(P.provider_settings)
    own = dict(every.get(provider) or {})
    for k in keys:
        if k in body:
            if body[k]:
                own[k] = body[k]
            else:
                own.pop(k, None)
    if own:
        every[provider] = own
    else:
        every.pop(provider, None)
    admin.set_settings(root, providers=every or None)


def _rename_served(old, new):
    """Serve the project under its new name: in the registry when it is registered, and in
    the server's own list when it is not."""
    root = P.ROOT.resolve()
    if any(Path(x["path"]).resolve() == root for x in project.load_registry()):
        project.register(root, new)
    if old in EXTRA:
        EXTRA[new] = EXTRA.pop(old)
    if DEFAULT[0] == old:
        DEFAULT[0] = new


def act_project(body):
    """Change one setting of the current project through `admin`, so the record of the
    installed files stays true. The ops: `meta` (name, description), `models` (`provider`, and
    its models, efforts, defaultModel, defaultEffort), `groups` (store, required), `categories` (the
    project's own categories), `vcs` (vcs, vcsPull), `provider` (the default provider), `packAdd`
    and `packRemove` (a pack), `packsSet` (`packs`: every pack the project should have), `rules`
    (`rules`: an optional rule -> true, false, or null for no switch of the project's own)."""
    import admin
    op, root, name, log = body.get("op"), P.ROOT, _current_name(), []
    if op == "meta":
        new = " ".join(str(body.get("name") or "").split())
        if not new:
            raise ValueError("a project needs a name")
        here = root.resolve()
        if new != name and any(x["name"] == new and Path(x["path"]).resolve() != here
                               for x in project.load_registry()):
            raise ValueError("another project is registered as %r" % new)
        admin.set_settings(root, name=new, description=str(body.get("description") or ""))
        if new != name:
            _rename_served(name, new)
            name = new
    elif op == "models":
        provider = str(body.get("provider") or project.DEFAULT_PROVIDER)
        if provider not in project.PROVIDERS:
            raise ValueError("%r is not an AI provider. One of: %s"
                             % (provider, ", ".join(project.PROVIDERS)))
        _set_provider_models(root, provider, body)
    elif op == "groups":
        g = dict(P.cfg.get("groups") or {})
        if "store" in body:
            g["store"] = body["store"]
        if "required" in body:
            g["required"] = bool(body["required"])
        admin.set_settings(root, groups=g)
    elif op == "categories":
        own = body.get("categories") or {}
        if not isinstance(own, dict):
            raise ValueError("the categories map a name to its meaning")
        left = set(project.DEFAULT_CATEGORIES) | set(own)
        used = sorted({t.category for t in T.load_all() if t.category not in left})
        if used:
            raise ValueError("tickets still carry the category %s" % ", ".join(used))
        admin.set_settings(root, categories=own or None)
    elif op == "vcs":
        admin.set_settings(root, **{k: body[k] for k in ("vcs", "vcsPull") if k in body})
    elif op == "provider":
        # The provider a launch starts unless it names another; the built-in default is no key.
        provider = str(body.get("provider") or "")
        if provider not in project.PROVIDERS:
            raise ValueError("%r is not an AI provider. One of: %s"
                             % (provider, ", ".join(project.PROVIDERS)))
        old = P.providers.get(P.provider, {})
        old_model = old.get("models", {}).get(old.get("defaultModel"), "")
        admin.set_settings(root, provider=None if provider == project.DEFAULT_PROVIDER else provider)
        # The new provider's own default applies; say so when it cannot run the old one.
        new = project.load(root).providers[provider]
        if old_model and old_model not in new["models"].values():
            log.append("%s cannot run %s; its default model %s applies"
                       % (providers.label(provider), old_model,
                          new["defaultModel"] or "of the CLI"))
    elif op in ("packAdd", "packRemove"):
        pack = str(body.get("pack") or "").strip()
        # Never empty: `uninstall` with no pack takes Octopus AI out of the whole project.
        if not pack or project.pack_dir(pack) is None:
            raise ValueError("no pack %r" % pack)
        if op == "packAdd":
            admin.pack_add(root, pack, out=log.append)
        else:
            admin.uninstall(root, pack, out=log.append)
    elif op == "packsSet":
        packs = body.get("packs")
        if not isinstance(packs, list) or not all(isinstance(x, str) for x in packs):
            raise ValueError("`packs` is the list of pack names")
        added, removed = admin.packs_set(root, packs, out=log.append)
        log.append("added %s; removed %s" % (", ".join(added) or "none",
                                             ", ".join(removed) or "none"))
    elif op == "rules":
        changes = body.get("rules")
        if not isinstance(changes, dict) or not all(v is None or isinstance(v, bool)
                                                    for v in changes.values()):
            raise ValueError("`rules` maps an optional rule to true, false or null")
        changed = admin.set_rules(root, changes, out=log.append)
        if not changed:
            log.append("no rule changed")
    else:
        raise ValueError("unknown project operation %r" % op)
    tok = project.use(root)
    try:
        T.write_index(quiet=True)
    finally:
        project.reset(tok)
    return {"op": op, "project": name, "log": log}


# ── the Project view: git ──────────────────────────────────────────────────

_VCS_LOCKS = {}
_VCS_LOCKS_LOCK = threading.Lock()
# A branch or remote name the page sends: never one that git would read as an option.
_REF_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]*$")


def _vcs_lock(root):
    """The lock that serializes the git operations of one project."""
    key = os.path.normcase(str(Path(root).resolve()))
    with _VCS_LOCKS_LOCK:
        return _VCS_LOCKS.setdefault(key, threading.Lock())


def _cmd(argv):
    """A command line as the page shows it."""
    import subprocess
    return subprocess.list2cmdline(argv)


def _steps_out(steps):
    return [{"cmd": _cmd(s.argv), "rc": s.rc, "out": s.out} for s in steps]


def _vcs_state(root):
    """The VCS of the project as the page shows it, and the steps that read it. A directory
    that holds `.git` is read as git, whatever `vcs` says, so the page can offer to enable
    it."""
    p = project.load(root)
    found = _vcs.detect(root)
    b = _vcs.backend(root, "git" if found == "git" else "none")
    st = b.status()
    info = dict(st.info, vcs=p.vcs, detected=found, pull=p.vcs_pull,
                branches=b.branches().info["local"] if st.info.get("repo") else [])
    return info, list(st)


def _ref_arg(body, key):
    name = str(body.get(key) or "").strip()
    if not _REF_NAME.match(name):
        raise ValueError("give a %s name: letters, digits and . _ / -" % key)
    return name


def _enable_git(root, body, log):
    """`vcs: git`, and the git pack unless the owner unticked it."""
    import admin
    admin.set_settings(root, vcs="git")
    if body.get("pack", True) and T.GIT_PACK not in project.load(root).RULES["rulesets"]:
        admin.pack_add(root, T.GIT_PACK, out=log.append)


def act_vcs(body):
    """Read or change the git repository of the current project. Every op returns the steps
    it ran, `[{cmd, rc, out}]`, and the state after them. A destructive op returns
    `{needsConfirm, cmd}` and runs nothing until the page repeats it with `confirm: true`."""
    op, root, confirm = body.get("op") or "status", P.ROOT, bool(body.get("confirm"))
    log = []
    with _vcs_lock(root):
        try:
            if op == "status":
                info, steps = _vcs_state(root)
                return {"op": op, "steps": _steps_out(steps), "status": info,
                        "noSnapshot": True}
            found = _vcs.detect(root)
            if op == "init":
                if found not in (None, "git") and not confirm:
                    raise _vcs.NeedsConfirm("%s already holds a %s repository"
                                            % (root, found), ["git", "init"])
                _enable_git(root, body, log)
                steps = _vcs.Git(root).init(project.load(root).RULES["gitignore"],
                                            confirm=True)
            elif found != "git":
                raise ValueError("%s is not a git repository: create one first" % root)
            elif op == "enable":
                _enable_git(root, body, log)
                steps = []
            else:
                g = _vcs.Git(root)
                if op == "remote":
                    url = str(body.get("url") or "").strip()
                    if not url or url.startswith("-"):
                        raise ValueError("give the URL of the remote")
                    name = _ref_arg({"remote": body.get("name") or "origin"}, "remote")
                    steps = g.set_remote(url, name)
                elif op == "sync":
                    remote = g.status().info["remote"]
                    if not remote:
                        raise ValueError("the repository has no remote: set one first")
                    steps = g.sync(project.load(root).vcs_pull, remote)
                elif op == "commit":
                    msg = str(body.get("message") or "").strip()
                    if not msg:
                        raise ValueError("a commit needs a message")
                    steps = g.commit_all(msg)
                elif op == "branchCreate":
                    steps = g.branch_create(_ref_arg(body, "branch"))
                elif op == "branchSwitch":
                    steps = g.branch_switch(_ref_arg(body, "branch"))
                elif op == "branchDelete":
                    steps = g.branch_delete(_ref_arg(body, "branch"), confirm=confirm)
                elif op == "merge":
                    steps = g.merge(_ref_arg(body, "branch"))
                elif op == "abort":
                    steps = g.abort()
                else:
                    raise ValueError("unknown git operation %r" % op)
        except _vcs.NeedsConfirm as exc:
            return {"op": op, "needsConfirm": exc.why, "cmd": _cmd(exc.argv),
                    "noSnapshot": True}
        info, _ = _vcs_state(root)
    return {"op": op, "steps": _steps_out(steps), "status": info, "log": log,
            "noSnapshot": True}


def act_providers(body):
    """The provider list of global settings (`providers.py`). The ops: `list`; `save` (`provider`,
    an entry; a new provider, or one whose command changed, is checked at once unless
    `check` is false); `remove` (`id`: a provider of the owner's goes, a built-in returns to
    its defaults); `check` (`id`: a short headless launch that must start and answer);
    `default` (`id`, `model`: the machine's default AI provider and model, made the default
    only when one short headless call on them answers). A save that changes the default
    model of the default provider runs the same check."""
    op = body.get("op") or "list"
    out = {"op": op, "noSnapshot": True}
    if op == "save":
        row = body.get("provider")
        if not isinstance(row, dict):
            raise ValueError("`provider` is the provider's entry")
        before = providers.load().get(row.get("id"))
        p = providers.save(row)
        changed = before is None or any(before.get(k) != p.get(k)
                                        for k in ("command", "args", "check", "env"))
        if (before is not None and before["defaultModel"] != p["defaultModel"]
                and p["id"] == providers.default_id()):
            out["setup"] = out["check"] = providers.setup(p["id"], p["defaultModel"])
        elif body.get("check", changed):
            out["check"] = providers.check(p["id"])
        out["id"] = p["id"]
    elif op == "default":
        out["setup"] = providers.setup(str(body.get("id") or ""), str(body.get("model") or ""))
    elif op == "remove":
        out["result"] = providers.remove(str(body.get("id") or ""))
    elif op == "check":
        out["check"] = providers.check(str(body.get("id") or ""))
    elif op != "list":
        raise ValueError("unknown provider operation %r" % op)
    out.update(providers.view())
    return out


def act_packs(body):
    """The pack list of global settings (`packreg.py`). The ops: `list`; `add` (`source`, a
    folder or a git URL, and `name`, `ref`, `path`); `update` (`name`, and `ref`); `remove`
    (`name`)."""
    import packreg
    op, log = body.get("op") or "list", []

    def s(k):
        return str(body.get(k) or "").strip() or None
    if op == "add":
        packreg.add(s("source"), s("name"), s("ref"), s("path"), out=log.append)
    elif op == "update":
        packreg.update(s("name") or "", s("ref"), out=log.append)
    elif op == "remove":
        packreg.remove(s("name") or "", out=log.append)
    elif op != "list":
        raise ValueError("unknown pack operation %r" % op)
    return {"op": op, "log": log, "noSnapshot": True, "file": str(project.pack_registry_file()),
            "packs": packreg.available()}


def act_rules(body):
    """The optional rules of global settings (`admin.global_rules`). The ops: `list`; `set`
    (`rules`: an optional rule -> true, false, or null for its default), which writes the rules
    and the entry files of every registered project again."""
    import admin
    op, log = body.get("op") or "list", []
    if op == "set":
        changes = body.get("rules")
        if not isinstance(changes, dict) or not all(v is None or isinstance(v, bool)
                                                    for v in changes.values()):
            raise ValueError("`rules` maps an optional rule to true, false or null")
        if not admin.set_global_rules(changes, out=log.append):
            log.append("no rule changed for a project without its own switch")
    elif op != "list":
        raise ValueError("unknown rule operation %r" % op)
    return {"op": op, "log": log, "noSnapshot": True, "file": str(project.global_rules_file()),
            "rules": admin.global_rules()}


def act_skills(body):
    """The skills of the current project (`skills.py`). The ops: `list`; `available`
    (`library`, `git` or `folder`, and `ref`, `path`, `refresh`: the skills the source offers);
    `add` (the same source, and `skills`, the paths to install); `update` (`skills`, every
    one when empty, and `force`); `remove` (`skills`, and `force`)."""
    import skills
    op, root, log = body.get("op") or "list", P.ROOT, []
    out = {"op": op, "log": log, "noSnapshot": True}

    def s(k):
        return str(body.get(k) or "").strip() or None
    names = [str(x) for x in body.get("skills") or []]
    src = {"library": s("library"), "git": s("git"), "folder": s("folder"), "ref": s("ref")}
    if op == "available":
        out["available"] = skills.available(root, path=s("path"),
                                            refresh=bool(body.get("refresh")), **src)
    elif op == "add":
        if not names:
            raise ValueError("tick the skills to install")
        skills.add(root, names, path=s("path"), refresh=bool(body.get("refresh")),
                   out=log.append, **src)
    elif op == "update":
        skills.update(root, names, force=bool(body.get("force")), out=log.append)
    elif op == "remove":
        skills.remove(root, names, force=bool(body.get("force")), out=log.append)
    elif op != "list":
        raise ValueError("unknown skill operation %r" % op)
    out["installed"] = skills.listing(root)
    return out


def act_removeProject(body):
    """Take a project off the board: out of the registry, and out of the server's own list.
    Its files stay as they are."""
    name = body.get("name") or ""
    ps = projects()
    if name not in ps:
        raise KeyError(name)
    n = project.unregister(ps[name])
    EXTRA.pop(name, None)
    if DEFAULT[0] == name:
        DEFAULT[0] = None
    return {"removed": name, "unregistered": n, "noSnapshot": True}


def act_addProject(body):
    """Register a project with the board. A directory that holds `.octopus/project.json` is
    registered as it is; any other is installed with the chosen `packs` first, none when the
    body names none. An old `profile` in place of `packs` takes its packs."""
    import admin
    path = Path((body.get("path") or "").strip())
    if not str(path) or not path.is_absolute():
        raise ValueError("give the full path of the project directory")
    if project.has_config(path):
        row = project.register(path, body.get("name") or None)
        return {"project": row["name"], "noSnapshot": True}
    packs = body.get("packs")
    if packs is None:
        packs = admin.packs_of_profile(body["profile"]) if body.get("profile") else []
    if not isinstance(packs, list) or not all(isinstance(x, str) for x in packs):
        raise ValueError("`packs` is the list of pack names")
    path.mkdir(parents=True, exist_ok=True)
    lines = []
    admin.install(path, packs, body.get("name") or None, None, register=True,
                  out=lines.append)
    return {"project": project.load(path).name, "log": lines, "noSnapshot": True}


# The folder dialog runs in a child Python: Tk wants its own main thread, and the server's
# request threads are not one. The child prints the chosen directory as UTF-8, or nothing.
_ASK_FOLDER = r"""
import sys, tkinter, tkinter.filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = tkinter.filedialog.askdirectory(parent=root, title="Choose the project directory",
                                       initialdir=sys.argv[1] or None)
root.destroy()
sys.stdout.buffer.write((path or "").encode("utf-8"))
"""
_FOLDER_LOCK = threading.Lock()


def _ask_folder(start):
    """Show a native folder dialog on this machine. Returns the chosen directory, or "" when
    the owner cancels."""
    import subprocess
    if sys.platform == "darwin":
        # The Finder's own dialog: the Tk of a macOS Python is often missing or old.
        import posixterm
        path = posixterm.ask_folder(start)
        return os.path.normpath(path) if path else ""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run([sys.executable, "-c", _ASK_FOLDER, start], capture_output=True,
                       creationflags=flags)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise OSError("the folder dialog failed: %s" % (err[-1] if err else r.returncode))
    path = r.stdout.decode("utf-8").strip()
    return os.path.normpath(path) if path else ""


def act_suggestPacks(body):
    """The packs the content of the directory `path` needs, for the Add project window: those
    whose `detect` globs match there, and what they require. [] for a path that is no
    directory."""
    import admin
    path = Path((body.get("path") or "").strip())
    if not str(path) or not path.is_absolute():
        return {"packs": [], "noSnapshot": True}
    return {"packs": admin.suggest_packs(path), "noSnapshot": True}


def act_pickFolder(body):
    """Let the owner choose a directory in a native folder dialog, for the Add project window.
    `start` only opens the dialog there; nothing is read or written in it."""
    start = (body.get("start") or "").strip()
    if not (start and Path(start).is_absolute() and Path(start).is_dir()):
        start = ""
    if not _FOLDER_LOCK.acquire(blocking=False):
        raise ValueError("a folder dialog is already open")
    try:
        return {"path": _ask_folder(start), "noSnapshot": True}
    finally:
        _FOLDER_LOCK.release()


def act_artifactAdd(body):
    """Register a file of the project or an https:// URL in the Artifacts view, or update its
    entry. `artifacts.add` refuses a file outside the project or in a dot directory."""
    _artifacts.add(P.ROOT, str(body.get("target") or ""), title=body.get("title") or None,
                   description=body.get("description") or "", ticket=body.get("ticket") or "")
    return {"listing": _artifacts.listing(P.ROOT, P.cfg), "noSnapshot": True}


def act_artifactRemove(body):
    """Take an entry out of the Artifacts view. The file stays."""
    if not _artifacts.remove(P.ROOT, str(body.get("target") or "")):
        raise ValueError("no artifact %s" % body.get("target"))
    return {"listing": _artifacts.listing(P.ROOT, P.cfg), "noSnapshot": True}


ACTIONS = {"move": act_move, "field": act_field, "launch": act_launch, "open": act_open,
           "focus": act_focus, "manager": act_manager, "close": act_close, "closeFinished": act_closeFinished,
           "group": act_group, "groupRun": act_groupRun, "board": act_board,
           "new": act_new, "draft": act_draft, "restart": act_restart,
           "repeat": act_repeat, "screen": act_screen, "send": act_send, "log": act_log,
           "allSessions": act_allSessions, "reevaluate": act_reevaluate, "lanes": act_lanes,
           "addProject": act_addProject, "suggestPacks": act_suggestPacks, "pickFolder": act_pickFolder, "ticket": act_ticket, "edit": act_edit,
           "chat": act_chat, "groupDraft": act_groupDraft, "groupApply": act_groupApply,
           "project": act_project, "removeProject": act_removeProject, "vcs": act_vcs,
           "remoteControl": act_remoteControl, "providers": act_providers, "packs": act_packs,
           "rules": act_rules, "skills": act_skills, "artifactAdd": act_artifactAdd,
           "artifactRemove": act_artifactRemove}
# Actions that name no project of their own.
GLOBAL_ACTIONS = ("allSessions", "addProject", "suggestPacks", "pickFolder", "removeProject",
                  "providers", "packs", "rules", "manager")
# Actions that change the project's config on disk: the snapshot after them reads the
# project loaded again, under the name the action returns when it renamed the project.
CONFIG_ACTIONS = ("lanes", "project")


def _assets():
    """The browser-game pack's asset module, pointed at the current project, or None."""
    d = project.PACKS / "browser-game" / "tools"
    if not (d / "assets.py").is_file():
        return None
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import assets
    assets.set_root(P.ROOT)
    return assets


class Handler(BaseHTTPRequestHandler):
    server_version = "OctopusAI/1"

    def log_message(self, fmt, *a):           # the board polls; the default log is noise
        pass

    # ── guards ──────────────────────────────────────────────────────────────

    def _loopback(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")

    def _query(self):
        return parse_qs(urlparse(self.path).query)

    def _authed(self):
        given = self.headers.get("X-Todo-Token") or ""
        if not given:
            given = (self._query().get("token") or [""])[0]
        return secrets.compare_digest(given, TOKEN)

    def _send(self, code, payload, ctype="application/json; charset=utf-8", headers=None):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _enter(self, name):
        """Make the named project current for this request. Returns the reset token."""
        root = project_root(name)
        _NAME.value = name or next((n for n, r in projects().items() if r == root), "")
        return project.use(root)

    def _view(self, path):
        """`/view/<VIEW_TOKEN>/<project>/<path>`: one file of a served project, for the
        Artifacts view. The path is a prefix, so a page's relative links resolve under it. A
        Markdown file comes rendered, unless `?raw=1`. Every refusal past the key is a 404."""
        segs = path[len("/view/"):].split("/", 2)
        if len(segs) < 3 or not secrets.compare_digest(segs[0], VIEW_TOKEN):
            return self._send(403, {"error": "bad token"})
        name, rel = unquote(segs[1]), unquote(segs[2])
        headers = {"Content-Security-Policy": VIEW_CSP}
        root = projects().get(name) if name else None
        try:
            f = _artifacts.safe_path(root, rel) if root is not None else None
            if f is None:
                return self._send(404, {"error": "no such file"}, headers=headers)
            ext = f.suffix.lower()
            raw = (self._query().get("raw") or [""])[0] == "1"
            if ext == ".md" and not raw:
                page = _artifacts.render_md(f.read_text(encoding="utf-8", errors="replace"))
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8",
                                  headers=headers)
            ctype = VIEW_TYPES.get(ext) or mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/json",):
                ctype += "; charset=utf-8"
            return self._send(200, f.read_bytes(), ctype, headers=headers)
        except OSError:
            return self._send(404, {"error": "no such file"}, headers=headers)

    # ── routes ──────────────────────────────────────────────────────────────

    def do_GET(self):
        if not self._loopback():
            return self._send(403, {"error": "loopback only"})
        path = urlparse(self.path).path
        name = (self._query().get("project") or [""])[0]
        if path in ("/", "/index.html", "/assets"):
            page = PAGE if path != "/assets" else project.PACKS / "browser-game" / "tools" / "asset_board.html"
            if not page.exists():
                return self._send(404, {"error": "%s is missing" % page.name})
            html = (page.read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
                    .replace("__VIEW_TOKEN__", VIEW_TOKEN))
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if path.startswith("/view/"):
            return self._view(path)
        if not self._authed():
            return self._send(403, {"error": "bad token"})
        if path == "/api/projects":
            ps = projects()
            import admin
            import packreg
            packs = [{"name": p["name"], "title": p["title"], "requires": p["requires"],
                      "source": p["source"]} for p in packreg.available() if p["dir"]]
            return self._send(200, {"projects": [project_info(n, r) for n, r in ps.items()],
                                    "default": DEFAULT[0] if DEFAULT[0] in ps else
                                    next(iter(ps), None),
                                    "packs": packs,
                                    "baseRulesets": admin.base_rulesets(),
                                    "defaultSetup": providers.default_state()})
        if path == "/api/providers":
            return self._send(200, providers.view())
        try:
            tok = self._enter(name)
        except KeyError as exc:
            return self._send(404, {"error": "no %s" % exc})
        try:
            if path == "/api/tickets":
                return self._send(200, snapshot())
            if path == "/api/project":
                return self._send(200, project_view())
            if path == "/api/artifacts":
                return self._send(200, _artifacts.listing(P.ROOT, P.cfg))
            if path == "/api/assets" or path.startswith("/asset/"):
                assets = _assets()
                if assets is None:
                    return self._send(404, {"error": "the browser-game pack is not installed"})
                if path == "/api/assets":
                    return self._send(200, {"assets": assets.inventory(),
                                            "kinds": assets.kinds()})
                found = assets.serve(unquote(path[len("/asset/"):]))
                if found is None:
                    return self._send(404, {"error": "no such asset"})
                data, ctype = found
                return self._send(200, data, ctype)
            return self._send(404, {"error": "no route"})
        except Exception as exc:                        # never take the board down
            return self._send(500, {"error": "%s: %s" % (type(exc).__name__, exc)})
        finally:
            project.reset(tok)

    def do_POST(self):
        if not self._loopback():
            return self._send(403, {"error": "loopback only"})
        if not self._authed():
            return self._send(403, {"error": "bad token"})
        path = urlparse(self.path).path
        name = path[len("/api/"):] if path.startswith("/api/") else ""
        fn = ACTIONS.get(name)
        tok = None
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._send(400, {"error": "bad body: %s" % exc})
        if fn is None:
            try:
                tok = self._enter((self._query().get("project") or [""])[0]
                                  or body.get("project") or "")
                assets = _assets()
                if assets is not None and name in assets.ACTIONS:
                    fn = assets.ACTIONS[name]
            except KeyError:
                pass
            if fn is None:
                if tok is not None:
                    project.reset(tok)
                return self._send(404, {"error": "no action %r" % name})
        try:
            if tok is None and name not in GLOBAL_ACTIONS:
                tok = self._enter((self._query().get("project") or [""])[0]
                                  or body.get("project") or "")
            result = fn(body)
            if name in CONFIG_ACTIONS:
                # The config changed on disk: the snapshot reads the project loaded again.
                project.reset(tok)
                tok = None
                tok = self._enter(result.get("project")
                                  or (self._query().get("project") or [""])[0]
                                  or body.get("project") or "")
            # The terminal popup calls `screen` once a second; a snapshot on each call would
            # cost a process-table scan per second.
            if not result.pop("noSnapshot", False):
                result["snapshot"] = snapshot()
            return self._send(200, result)
        except KeyError as exc:
            return self._send(404, {"error": "no such ticket, epic or project: %s" % exc})
        except (ValueError, OSError) as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:                        # never take the board down
            return self._send(500, {"error": "%s: %s" % (type(exc).__name__, exc)})
        finally:
            if tok is not None:
                project.reset(tok)


def make_server(port=0, ident=None):
    """The server, bound to loopback, with `ident` (a name or a path) as the page's first
    project. A path that is not registered is served too."""
    if ident:
        root = project.resolve(ident)
        name = project.load(root).name
        reg = {r["name"]: Path(r["path"]).resolve() for r in project.load_registry()}
        match = [n for n, r in reg.items() if r == root.resolve()]
        if match:
            name = match[0]
        else:
            EXTRA[name] = root
        DEFAULT[0] = name
    for name, root in projects().items():
        tok = project.use(root)
        try:
            T.ensure_tree()
            T.write_index(quiet=True)
        finally:
            project.reset(tok)
    mimetypes.init()
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _initialize(root):
    """Install Octopus AI with no pack into `root`, a folder in no project, and register it.
    The Project view adds packs later. Returns the root as a string."""
    import admin
    print("%s holds no Octopus AI project: installing Octopus AI into it" % root)
    admin.install(root, [], register=True)
    return str(Path(root).resolve())


def first_project(ident):
    """The project the page opens on: `ident` (a name or a path), else the working directory.
    A named folder in no project, and an empty working directory, get Octopus AI installed. With
    no project registered and none found, the owner chooses the folder in a folder dialog."""
    if ident:
        if any(r["name"].lower() == str(ident).lower() for r in project.load_registry()):
            return ident
        p = Path(ident)
        if p.is_dir() and project.find_root(p) is None:
            return _initialize(p)
        return ident
    cwd = Path.cwd()
    found = project.find_root(cwd)
    if found is not None:
        return None if project.load_registry() else str(found)
    if not any(cwd.iterdir()):
        return _initialize(cwd)
    if project.load_registry():
        return None
    print("No project is registered. Choose the folder of your project; an empty folder is fine.")
    chosen = _ask_folder(str(Path.home()))
    if not chosen:
        return None
    return _initialize(chosen) if project.find_root(chosen) is None else chosen


def main(argv=None):
    ap = argparse.ArgumentParser(prog="octopus board",
                                 description="Serve the Octopus AI Agent Board of every project on localhost.")
    ap.add_argument("--port", type=int, default=0, help="0 picks a free one")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--project", help="the project the page opens on (a name or a path)")
    ap.add_argument("--page", choices=("board", "assets"), default="board")
    ap.add_argument("--no-manager", action="store_true",
                    help="do not start the manager when the board opens")
    args = ap.parse_args(argv)
    try:
        args.project = first_project(args.project)
    except (ValueError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    if not args.project and not project.load_registry():
        print("error: no project registered and none chosen: "
              "`octopus board --project <folder>` installs Octopus AI into that folder",
              file=sys.stderr)
        return 2
    httpd = make_server(args.port, args.project)
    start_watchdog()
    if not args.no_manager:
        threading.Thread(target=autostart_managers, name="octopus-manager", daemon=True).start()
    port = httpd.server_address[1]
    q = "token=%s" % TOKEN
    if DEFAULT[0]:
        from urllib.parse import quote
        q += "&project=%s" % quote(DEFAULT[0])
    url = "http://127.0.0.1:%d/%s?%s" % (port, "assets" if args.page == "assets" else "", q)
    print("Agent Board  %s" % url)
    print("Ctrl+C to stop.")
    if not args.no_open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("")
    httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
