"""gitsrc.py - a git repository as a source of files: a pack (`packreg.py`) or a skill (`skills.py`).

    fetch(url, dest, ref)   clone `url` into `dest`, or fetch it again when `dest` holds it;
                            then check out `ref`, or the remote's default branch. Returns the commit
    commit(dest)            the commit `dest` has checked out
    is_url(text)            whether `text` names a remote repository and not a local folder

A clone always sits detached at the commit it was asked for: a branch name follows the remote
branch on every fetch, a tag or a commit stays where it is. `GIT_TERMINAL_PROMPT=0` keeps git from
asking for a password; a repository that needs one fails with git's own message.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

_URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|[\w.-]+@[\w.-]+:)", re.I)


def remove_tree(p):
    """Remove a directory, a clone's read-only object files included."""
    def again(fn, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    if Path(p).exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(p, onexc=again)
        else:
            shutil.rmtree(p, onerror=again)


def is_url(text):
    return bool(_URL.match(str(text or "").strip()))


def _git(args, cwd=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, env=env,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip().splitlines()
        raise ValueError("git %s failed: %s" % (args[0], msg[-1] if msg else r.returncode))
    return r.stdout.strip()


def commit(dest):
    return _git(["rev-parse", "HEAD"], cwd=dest)


def _target(dest, ref):
    """The commit-ish to check out: the remote branch `ref`, else `ref` itself (a tag, a
    commit), else the remote's default branch."""
    if not ref:
        try:
            return _git(["rev-parse", "--verify", "--quiet", "origin/HEAD"], cwd=dest)
        except ValueError:
            return _git(["rev-parse", "--verify", "--quiet", "FETCH_HEAD"], cwd=dest)
    try:
        return _git(["rev-parse", "--verify", "--quiet", "origin/%s^{commit}" % ref], cwd=dest)
    except ValueError:
        return _git(["rev-parse", "--verify", "--quiet", "%s^{commit}" % ref], cwd=dest)


def fetch(url, dest, ref=None, shallow=False):
    """Clone or fetch `url` into `dest` and check out `ref`. Returns the commit. A clone that
    fails leaves no directory behind. `shallow` takes only the last commit of the default
    branch, for a large library that is read and never checked out at another ref."""
    dest = Path(dest)
    fresh = not (dest / ".git").exists()
    depth = ["--depth", "1"] if shallow and not ref else []
    if fresh:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            _git(["clone", "--quiet", "--no-checkout"] + depth + [str(url), str(dest)])
        except ValueError:
            remove_tree(dest)
            raise
    else:
        _git(["remote", "set-url", "origin", str(url)], cwd=dest)
        _git(["fetch", "--quiet", "--force", "origin"] + (depth or ["--tags"]), cwd=dest)
        try:
            _git(["remote", "set-head", "origin", "--auto"], cwd=dest)
        except ValueError:
            pass
    try:
        _git(["checkout", "--quiet", "--force", "--detach", _target(dest, ref)], cwd=dest)
    except ValueError:
        if fresh:
            remove_tree(dest)
        raise ValueError("%s has no ref %r" % (url, ref) if ref else "%s has no commit" % url)
    return commit(dest)


def checkout(dest, sha):
    """Put `dest` back on the commit `sha`."""
    _git(["checkout", "--quiet", "--force", "--detach", sha], cwd=dest)
