"""git_guard.py - a session commits only on the work branch of its project.

A project that lists the `git` pack names its work branch in `.octopus/project.json`:
`gitBranch`. This guard runs on `PreToolUse`, after the README guard (`readme_guard.py`):

    Claude Code   `octopus hook`               Bash, PowerShell
    Codex         `octopus status-hook codex`  Bash

It refuses a shell command that runs a git command which makes a commit on the branch that is
checked out (`COMMITTERS`), when the project of the command's directory has `gitBranch` and
another branch, or a detached HEAD, is checked out there. `main` and `master` get no exception.
`--abort` and `--quit` make no commit, so they always run. A commit after a `git switch` or a
`git checkout` of a branch in the same command line is refused too. A project with no
`gitBranch` lets every command run.

The directory of a git command is the tool call's `cwd`, changed by each `cd`, `Set-Location`,
`pushd` and `sl` before it in the same command line, then by its own `-C <dir>`. The branch is
what `git symbolic-ref --short HEAD` prints there. When git cannot run, the guard lets the
command run: the commit then fails on its own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, HERE.parent / "engine"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import readme_guard as _rg  # noqa: E402

SHELL_TOOLS = ("Bash", "PowerShell")
# The git commands that make a commit on the branch that is checked out.
COMMITTERS = {"commit", "revert", "cherry-pick", "merge", "pull", "am", "rebase"}
# The options of those commands that make no commit.
SAFE_OPTIONS = {"--abort", "--quit"}
# The git commands that can change the branch checked out; `checkout -- <file>` does not.
SWITCHERS = {"switch", "checkout"}
# The options of git itself that take the next word as their value.
_GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                      "--config-env"}
_CD = {"cd", "set-location", "sl", "pushd", "chdir"}
TIMEOUT = 10


def _git_call(words):
    """`(directory or None, subcommand, arguments)` of one `git` command line after `git`."""
    k, where = 0, None
    while k < len(words):
        w = words[k]
        if w == "-C" and k + 1 < len(words):
            where = words[k + 1] if where is None else os.path.join(where, words[k + 1])
            k += 2
        elif w in _GIT_VALUE_OPTIONS and k + 1 < len(words):
            k += 2
        elif w.startswith("-"):
            k += 1
        else:
            return where, w, words[k + 1:]
    return where, "", []


def commit_calls(command, cwd, powershell=False):
    """Each `(directory, subcommand, switched)` of the shell `command` that makes a commit.
    `switched` is true when a `git switch` or a `git checkout` of a branch comes before it in
    the same command line, so the branch checked out now is not the one it commits on."""
    here = cwd or os.getcwd()
    out, switched = [], False
    for words, _targets, _bodies in _rg._commands(_rg._lex(command, posix=not powershell)):
        prog, args = _rg._program(words)
        if prog in _CD:
            plain = [a for a in args if not a.startswith("-")]
            if plain:
                here = _rg.absolute(plain[0], here)
            continue
        if prog != "git":
            continue
        where, sub, rest = _git_call(args)
        if sub in SWITCHERS and "--" not in rest:
            switched = True
            continue
        if sub not in COMMITTERS or any(a in SAFE_OPTIONS for a in rest):
            continue
        out.append((_rg.absolute(where, here) if where else here, sub, switched))
    return out


def work_branch(folder):
    """`(project name, gitBranch)` of the project that holds `folder`, or `(None, "")`."""
    try:
        import project
        root = project.find_root(folder)
        if root is None:
            return None, ""
        cfg = json.loads(project.config_path(root, project.PROJECT_FILE)
                         .read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            return None, ""
        branch = cfg.get("gitBranch")
        return cfg.get("name") or root.name, branch if isinstance(branch, str) else ""
    except Exception:                                   # noqa: BLE001 - a hook never raises
        return None, ""


def current_branch(folder):
    """The branch checked out in `folder`; "" for a detached HEAD; None when git cannot say."""
    try:
        r = subprocess.run(["git", "-C", str(folder), "symbolic-ref", "--quiet", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode == 0:
        return r.stdout.strip()
    if r.returncode == 1:
        probe = subprocess.run(["git", "-C", str(folder), "rev-parse", "--git-dir"],
                               capture_output=True, text=True, timeout=TIMEOUT,
                               stdin=subprocess.DEVNULL)
        return "" if probe.returncode == 0 else None
    return None


def reason(sub, folder, name, want, have):
    """The refusal the agent reads. `have` is None when the command line switches branches
    before it commits."""
    where = ("the same command line switches branches first" if have is None
             else "a detached HEAD is checked out" if not have
             else "the branch `%s` is checked out" % have)
    return ("Refused: `git %s` makes a commit, and in %s %s. The project %s commits only on the"
            " branch `%s` (`gitBranch` in .octopus/project.json). Do not switch branches and do"
            " not commit on another branch. Ask the owner."
            % (sub, Path(folder).as_posix(), where, name, want))


def decide(payload):
    """The `PreToolUse` answer for one tool call: a refusal, or None to let it run. Never
    raises."""
    try:
        if str(payload.get("hook_event_name") or "") != "PreToolUse":
            return None
        tool = str(payload.get("tool_name") or "")
        tool_input = payload.get("tool_input")
        if tool not in SHELL_TOOLS or not isinstance(tool_input, dict):
            return None
        command = tool_input.get("command")
        if isinstance(command, list):
            command = " ".join(str(c) for c in command)
        if not isinstance(command, str) or "git" not in command:
            return None
        cwd = str(payload.get("cwd") or "") or None
        for folder, sub, switched in commit_calls(command, cwd, powershell=tool == "PowerShell"):
            name, want = work_branch(folder)
            if not want:
                continue
            have = None if switched else current_branch(folder)
            if not switched and (have is None or have == want):
                continue
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason":
                                               reason(sub, folder, name, want, have)}}
        return None
    except Exception:                                   # noqa: BLE001 - a hook never raises
        return None
