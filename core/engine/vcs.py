"""vcs.py - the version control of a project: one interface, a git backend, and nothing else.

    detect(root)         "git" when the root holds `.git`, "diversion" when it holds
                         `.diversion`, else None
    backend(root, kind)  `Git` for "git", else `NoVcs`. Without a kind, `detect` picks it

EVERY METHOD RETURNS A LIST OF `Step(argv, rc, out)`, one per command it ran, in order. A
method that runs several commands stops at the first that fails, so the last step says why.
`status()` and `branches()` return a `Steps`, a list that also carries what they read in
`info`. A missing git, or a command past its timeout, is a failed step, not an exception.

A DESTRUCTIVE CALL WITHOUT `confirm=True` raises `NeedsConfirm(why, argv)` and runs nothing:
deleting a branch that is not merged, and `init` on a project that already has another VCS.

CREDENTIALS stay in git's own credential manager. Octopus AI stores none, and git never prompts on
a terminal: `GIT_TERMINAL_PROMPT=0`, and stdin is empty.

The project chooses its VCS in `.octopus/project.json`: `vcs` is `git` or `none` (absent means
none), and `vcsPull` is how `sync` pulls (`PULL_ARGS`). A project with no VCS works as before.
"""

from __future__ import annotations

import io
import os
import subprocess
from collections import namedtuple
from pathlib import Path

KINDS = ("git", "none")
MARKERS = (("git", ".git"), ("diversion", ".diversion"))
PULL_ARGS = {
    "rebase": ["--rebase", "--autostash"],
    "merge": ["--no-rebase"],
    "ff-only": ["--ff-only"],
}
DEFAULT_PULL = "rebase"
TIMEOUT = 120
NET_TIMEOUT = 600
GITIGNORE_HEADER = "# Octopus AI: machine-local files"

Step = namedtuple("Step", "argv rc out")


class Steps(list):
    """The steps a reading method ran, and what it read in `info`."""

    def __init__(self, steps=(), info=None):
        super().__init__(steps)
        self.info = info or {}


class NeedsConfirm(Exception):
    """A destructive call that needs `confirm=True`. `argv` is what it would run."""

    def __init__(self, why, argv):
        super().__init__(why)
        self.why, self.argv = why, list(argv)


def detect(root):
    """`"git"`, `"diversion"` or None: the VCS whose marker sits in the root."""
    root = Path(root)
    for kind, marker in MARKERS:
        if (root / marker).exists():
            return kind
    return None


def backend(root, kind=None):
    """The backend of this root: `Git` for "git", else `NoVcs`."""
    kind = detect(root) if kind is None else kind
    return Git(root) if kind == "git" else NoVcs(root)


def _git(root, *args, timeout=TIMEOUT):
    """Run `git <args>` in the root with no console window and no prompt. One `Step`."""
    argv = ["git"] + [str(a) for a in args]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           cwd=str(root), encoding="utf-8", errors="replace", env=env,
                           stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        return Step(argv, 127, "git is not on the PATH")
    except subprocess.TimeoutExpired:
        return Step(argv, 124, "git did not finish in %d seconds" % timeout)
    out = "\n".join(s.strip() for s in (r.stdout, r.stderr) if s and s.strip())
    return Step(argv, r.returncode, out)


def merge_gitignore(path, lines):
    """Append to the `.gitignore` at `path` every line of `lines` it lacks. True when it
    wrote."""
    path = Path(path)
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        text = ""
    have = {ln.strip() for ln in text.splitlines()}
    new = [ln for ln in lines if ln.strip() and ln.strip() not in have]
    if not new:
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    if text:
        text += "\n"
    if GITIGNORE_HEADER not in have:
        text += GITIGNORE_HEADER + "\n"
    text += "\n".join(new) + "\n"
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return True


class NoVcs:
    """A project with no VCS: no repository, and every call does nothing."""

    kind = "none"

    def __init__(self, root):
        self.root = Path(root)

    def status(self):
        return Steps(info={"repo": False, "vcs": detect(self.root)})

    def branches(self):
        return Steps(info={"current": "", "local": []})

    def init(self, gitignore=(), confirm=False):
        return []

    def commit_all(self, msg):
        return []

    def set_remote(self, url, name="origin"):
        return []

    def sync(self, strategy=DEFAULT_PULL, remote="origin"):
        return []

    def branch_create(self, name):
        return []

    def branch_switch(self, name):
        return []

    def branch_delete(self, name, confirm=False):
        return []

    def merge(self, name):
        return []

    def abort(self):
        return []


class Git:
    """A git working tree at `root`."""

    kind = "git"

    def __init__(self, root):
        self.root = Path(root)

    def _run(self, *args, timeout=TIMEOUT):
        return _git(self.root, *args, timeout=timeout)

    def _git_dir(self):
        s = self._run("rev-parse", "--absolute-git-dir")
        return Path(s.out.splitlines()[0]) if s.rc == 0 and s.out else None

    # ── reading ─────────────────────────────────────────────────────────────

    def status(self):
        """`info`: repo, branch, upstream, ahead, behind (None without an upstream), dirty
        (the paths that differ from HEAD, untracked ones included), remote and remote_url,
        rebase and merge (an operation in progress)."""
        info = {"repo": False, "vcs": "git", "branch": "", "upstream": "", "ahead": None,
                "behind": None, "dirty": [], "remote": "", "remote_url": "",
                "rebase": False, "merge": False}
        st = self._run("-c", "core.quotepath=false", "status", "--porcelain=v2", "--branch")
        steps = Steps([st], info)
        if st.rc != 0:
            return steps
        info["repo"] = True
        for line in st.out.splitlines():
            if line.startswith("# branch.head "):
                head = line[len("# branch.head "):]
                info["branch"] = "" if head == "(detached)" else head
            elif line.startswith("# branch.upstream "):
                info["upstream"] = line[len("# branch.upstream "):]
            elif line.startswith("# branch.ab "):
                a, b = line[len("# branch.ab "):].split()
                info["ahead"], info["behind"] = int(a), -int(b)
            elif line.startswith("1 "):
                info["dirty"].append(line.split(" ", 8)[8])
            elif line.startswith("2 "):
                info["dirty"].append(line.split(" ", 9)[9].split("\t")[0])
            elif line.startswith("u "):
                info["dirty"].append(line.split(" ", 10)[10])
            elif line.startswith("? "):
                info["dirty"].append(line[2:])
        rem = self._run("remote")
        steps.append(rem)
        names = rem.out.split() if rem.rc == 0 else []
        if info["upstream"] and "/" in info["upstream"]:
            pref = info["upstream"].split("/", 1)[0]
        else:
            pref = "origin"
        if names:
            info["remote"] = pref if pref in names else names[0]
            url = self._run("remote", "get-url", info["remote"])
            steps.append(url)
            info["remote_url"] = url.out if url.rc == 0 else ""
        gd = self._git_dir()
        if gd:
            info["rebase"] = (gd / "rebase-merge").is_dir() or (gd / "rebase-apply").is_dir()
            info["merge"] = (gd / "MERGE_HEAD").is_file()
        return steps

    def branches(self):
        """`info`: current, the branch checked out, and local, every local branch."""
        s = self._run("branch", "--format=%(HEAD) %(refname:short)")
        info = {"current": "", "local": []}
        if s.rc == 0:
            for line in s.out.splitlines():
                name = line[2:].strip()
                if not name:
                    continue
                info["local"].append(name)
                if line.startswith("*"):
                    info["current"] = name
        return Steps([s], info)

    def _has_head(self):
        return self._run("rev-parse", "--verify", "--quiet", "HEAD").rc == 0

    # ── changing ────────────────────────────────────────────────────────────

    def init(self, gitignore=(), confirm=False):
        """Make the root a repository, add `gitignore` to its `.gitignore`, and make the first
        commit when it has none. Raises `NeedsConfirm` when the root holds another VCS."""
        other = detect(self.root)
        argv = ["git", "init"]
        if other not in (None, "git") and not confirm:
            raise NeedsConfirm("%s already holds a %s repository" % (self.root, other), argv)
        steps = [self._run("init")]
        if steps[-1].rc != 0:
            return steps
        merge_gitignore(self.root / ".gitignore", list(gitignore))
        if not self._has_head():
            steps.append(self._run("add", "-A"))
            if steps[-1].rc != 0:
                return steps
            steps.append(self._run("commit", "-m", "Initial commit"))
        return steps

    def commit_all(self, msg):
        """Stage every change and commit it. Only the staging step when nothing changed."""
        steps = [self._run("add", "-A")]
        if steps[-1].rc != 0:
            return steps
        if self._has_head() and self._run("diff", "--cached", "--quiet").rc == 0:
            return steps
        steps.append(self._run("commit", "-m", msg))
        return steps

    def set_remote(self, url, name="origin"):
        """Point the remote `name` at `url`, adding it when it is new."""
        rem = self._run("remote")
        if rem.rc != 0:
            return [rem]
        verb = "set-url" if name in rem.out.split() else "add"
        return [self._run("remote", verb, name, url)]

    def sync(self, strategy=DEFAULT_PULL, remote="origin"):
        """Fetch, pull with `strategy` (`PULL_ARGS`), push. Stops at the first failure. A
        branch with no upstream tracks `remote` when the remote has it, and is pushed with
        `-u` when it does not."""
        if strategy not in PULL_ARGS:
            raise ValueError("strategy %r; one of %s" % (strategy, ", ".join(PULL_ARGS)))
        steps = [self._run("fetch", remote, timeout=NET_TIMEOUT)]
        if steps[-1].rc != 0:
            return steps
        branch = self._run("symbolic-ref", "--short", "-q", "HEAD")
        if branch.rc != 0:
            return steps + [branch]
        upstream = self._run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if upstream.rc != 0:
            ref = "%s/%s" % (remote, branch.out)
            if self._run("rev-parse", "--verify", "--quiet", "refs/remotes/" + ref).rc != 0:
                steps.append(self._run("push", "-u", remote, "HEAD", timeout=NET_TIMEOUT))
                return steps
            steps.append(self._run("branch", "--set-upstream-to", ref))
            if steps[-1].rc != 0:
                return steps
        steps.append(self._run("pull", *PULL_ARGS[strategy], timeout=NET_TIMEOUT))
        if steps[-1].rc != 0:
            return steps
        steps.append(self._run("push", timeout=NET_TIMEOUT))
        return steps

    def branch_create(self, name):
        return [self._run("branch", name)]

    def branch_switch(self, name):
        return [self._run("switch", name)]

    def branch_delete(self, name, confirm=False):
        """Delete a local branch. Raises `NeedsConfirm` when it is not merged into HEAD; with
        `confirm`, it is deleted all the same."""
        merged = self._run("branch", "--merged", "HEAD", "--format=%(refname:short)")
        if merged.rc != 0:
            return [merged]
        if name in merged.out.split():
            return [merged, self._run("branch", "-d", name)]
        argv = ["git", "branch", "-D", name]
        if not confirm:
            raise NeedsConfirm("branch %s is not merged into the current branch" % name, argv)
        return [merged, self._run("branch", "-D", name)]

    def merge(self, name):
        return [self._run("merge", "--no-edit", name)]

    def abort(self):
        """Abort the rebase or the merge in progress. Nothing when neither is."""
        gd = self._git_dir()
        if gd is None:
            return []
        if (gd / "rebase-merge").is_dir() or (gd / "rebase-apply").is_dir():
            return [self._run("rebase", "--abort")]
        if (gd / "MERGE_HEAD").is_file():
            return [self._run("merge", "--abort")]
        return []
