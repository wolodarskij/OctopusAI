"""todo.py - the ticket tree of one project: one file per ticket, the state is the directory.

    TODO/
      INDEX.md              generated - never hand-edit
      Pipeline/             a ticket nobody has started
        T0001-short-slug.md
        some-epic/          an EPIC: a directory that moves as one unit
          _epic.md          what the group is for
          T0002-....md
      InProgress/           somebody is working on it now
      Blocked/              something outside the ticket stops it
      Verification/         the work landed and is not yet proven
      Done/                 proven

The lanes above are the default. A project names its own in `.octopus/project.json`
(`lanes`): their directories, their labels, their statuses, and lanes of its own. The code
needs five ROLES - start, active, blocked, verify, closed - and each names one lane.

THE DIRECTORY IS THE LANE AND `status` IS WHERE INSIDE IT. The path carries the coarse state,
so a VCS move, an editor drag and `todo.py move` all mean the same thing. The `status` field
carries the finer one, and its legal values are a SUBSET of the directory it sits in. `check`
enforces the subset, so the field cannot drift away from the path, and `move` resets the
status to the target lane's default when the old value has no meaning there.

IDS ARE PERMANENT AND NEVER REUSED. A ticket keeps its id through every move and rename.
The highest id ever issued is remembered in `TODO/.ids`, so deleting the newest ticket does
not hand its number to the next one.

THE INDEX IS DERIVED. Every mutating command rewrites it. `check` exits 1 when the file on
disk does not match what the tree says.

ONE ENGINE, MANY PROJECTS. Every path and every rule is read from the current project
(`project.py`). The CLI takes `--project <name|path>`; without it, the nearest
`.octopus/project.json` above the working directory is the project.

    octopus todo index                        rebuild TODO/INDEX.md
    octopus todo check                        exit 1 when the index is stale
    octopus todo board                        the lanes, side by side
    octopus todo list --state Blocked
    octopus todo show T0001
    octopus todo move T0001 T0002 InProgress
    octopus todo move --epic some-epic Verification
    octopus todo new --title "..." --severity High --source src/x.ts:12
    octopus todo epic some-epic --title "..."
    octopus todo permits                      the capability vocabulary
    octopus todo rulesets                     the rulesets in order and what each adds
    octopus todo plan T0001                   a session that only writes the plan
    octopus todo work T0001                   a session that plans, then executes
    octopus todo review T0001 --note "..."    a session that judges the TICKET
    octopus todo verify T0001                 a session that proves the work
    octopus todo new --title "..." --repeat test   a repeatable ticket
    octopus todo restart T0001                start the next run of a repeatable ticket
    octopus todo repeatable                   every repeatable ticket and its last run
    octopus todo artifact add Docs/x.html --title "..." --description "..." --ticket T0001
                                              register a page for the board's Artifacts view
    octopus todo watchdog --once              start every dead manager session of the Octopus AI project again
    octopus todo manager --start              the Octopus AI project's manager, from the template when there is none, started
    octopus todo manager --claim              this Claude session, in any project, becomes the manager
    octopus todo remote-control T0001 off     Remote Control of a ticket's launches: on, off, default
    octopus todo groups                       the groups; `--table`, `--set`, `--run`
    octopus todo group <slug> --title "..."   create, retitle, rename or delete a group
    octopus todo regroup T0001 <slug>         put tickets in a group
    octopus todo children T0001               a parent's children and where they stand
    octopus todo wait T0001                   hold until every child is in Verification or Done
    octopus todo sessions                     every session that still has a terminal
    octopus todo screen T0001 --mode work     the screen of that session's terminal
    octopus todo send T0001 --text "yes" --keys Enter
    octopus todo report T0001 input --why "Which lane?" --mode work   a session asks the owner
    octopus todo report T0001 event --why "editor restart"   ... or for an outside event
    octopus todo approve-readme README.md --session <id> --owner-order "<the order, in neutral words>"
                                              the owner approved the exact new text of a
                                              project-wide README file: the README guard lets
                                              this session write it

WORKING A TICKET IN ITS OWN SESSION. `work`, `plan`, `review` and `verify` write
`TODO/.briefs/<id>.md` and launch `claude` in a new terminal on it. The ticket's `permits`
become that session's `--disallowedTools`, so a ticket that was never granted a permit
cannot reach what the permit guards. A launch carries `--remote-control`, named
`<title> - <ticket> - <short ticket title> - <mode>`, while remote control is on for the ticket
(`remote-control: on|off` on the ticket, else `remoteControl` in project.json), and `--name`
with the same name when it is off. The model and the effort are the defaults of the provider the
launch starts; `--model` and `--effort` override them with one of that provider's own, and a
launch naming a model or an effort its provider cannot run is refused. Every launch runs `--permission-mode auto`, except a
`plan` asked for by name, which runs `--permission-mode plan`. Each mode gets its own session
id, derived from the project's session namespace and the ticket id, so `claude --resume
<uuid>` reattaches to that mode's agent, and two projects never share one.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import shutil
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
WIN = HERE.parent / "win"
HOOKS = HERE.parent / "hooks"
POSIX = HERE.parent / "posix"
for _d in (HERE, WIN, HOOKS, POSIX):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

# Windows runs a session in `cmd`, in Windows Terminal or a pseudoconsole of its own; macOS
# and Linux run it in `/bin/sh` inside tmux (`core/posix/posixterm.py`).
WINDOWS = os.name == "nt"

import project as _project  # noqa: E402
import providers as _providers  # noqa: E402
from project import P  # noqa: E402
from project import CATEGORY_CODE, CATEGORY_TOOLING, LEGACY_KINDS  # noqa: E402
import oldnames  # noqa: E402
import agents as _agents  # noqa: E402

# A ticket carries whatever the code it describes carries - an arrow, a non-Latin identifier,
# a box-drawing character. The Windows console is cp1252, so printing one raises
# UnicodeEncodeError. Re-wrap stdout and stderr as UTF-8 with replacement, so a character the
# console cannot draw costs one glyph instead of the whole listing.
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def lane(role):
    """The directory of the lane with this role: start, active, blocked, verify, closed."""
    return P.lane(role)


def S(role):
    """The status word the project uses for this status role."""
    return P.S[role]


# A REPEATABLE ticket is a job that is done again and again: a test run, a verification
# sweep, a pass over the board. It carries `repeat: <role>` and `run: <n>`. A verification
# that holds does not close it: the run is recorded under `## Runs`, `run` rises by one, and
# the ticket returns to the start lane for the next run. `restart` starts the next run in a
# fresh session. Only `cancelled` or `closed by other task` retires one.
#
# A MANAGER ticket (`repeat: manager`) has no runs to pass or fail. Its session stays open and
# waits for the owner's orders. It lives in the start lane (not started) or the active lane
# (running, guarded by the board's watchdog), and never enters the verify or the blocked lane,
# nor the closed lane except as `cancelled` or `closed by other task`.
REPEAT_ROLES = ("test", "verify", "board", "manager")
REPEAT_LABEL = {"test": "Tester", "verify": "Verificator", "board": "Board management",
                "manager": "Manager"}
REPEAT_MEANING = {
    "test": "runs a test or a measurement and reports what it found",
    "verify": "proves the tickets waiting in Verification",
    "board": "keeps the ticket tree in order: groups, sizes, stale tickets",
    "manager": "stays open, waits for the owner's orders, and runs tickets in every project",
}
MANAGER = "manager"
# The optional rule whose brief section `readme.<mode>` a launch gets only while it is on.
README_RULE = "readme-needs-owner-approval"
# The manager ticket Octopus AI ships. `ensure_manager` files it in the home project when that
# project has none.
MANAGER_TEMPLATE = HERE.parent / "templates" / "manager.md"


# THE MANAGER'S HOME. The manager belongs to the Octopus AI project: the registered project
# whose root is this checkout, the one `admin.ensure_self` installs and registers on the first
# run, else the one registered under its name (`admin.SELF_NAME`). `manager`, `watchdog`, the
# board's start and its watchdog file, start, claim and restart the manager only there,
# whichever project calls them.

def _manager_home_row():
    """The registry row of the manager's home project, or None."""
    import admin
    rows = [r for r in _project.load_registry() if _project.has_config(r["path"])]
    return (next((r for r in rows if admin.is_checkout(r["path"])), None)
            or next((r for r in rows if r["name"] == admin.SELF_NAME), None))


def manager_home():
    """The root of the manager's home project, or None when no registered project is it."""
    row = _manager_home_row()
    return Path(row["path"]) if row else None


def manager_home_name():
    """The name the manager's home project is registered under, or ""."""
    row = _manager_home_row()
    return row["name"] if row else ""


def at_manager_home():
    """Whether the current project is the manager's home project."""
    root = manager_home()
    try:
        return root is not None and P.ROOT.resolve() == root.resolve()
    except OSError:
        return False


@contextlib.contextmanager
def in_manager_home():
    """Make the manager's home project the current one for the block. Raises `LookupError`
    when no registered project is it."""
    root = manager_home()
    if root is None:
        raise LookupError("no registered project is the Octopus AI project, the manager's "
                          "home: register the checkout with `python \"%s\" projects add "
                          "\"%s\"`" % (_project.OCTOPUS / "octopus.py", _project.OCTOPUS))
    tok = _project.use(root)
    try:
        yield root
    finally:
        _project.reset(tok)


# Claude Code's Remote Control, per ticket. `remote-control: on|off` on the ticket wins over
# `remoteControl` in project.json; a provider without Remote Control (Codex) has none.
RC_FIELD = "remote-control"
RC_VALUES = ("on", "off")
# The AI provider of a ticket's last launch, when it is not the project's own; `agent:` is the
# line's old name.
PROVIDER_FIELD = "provider"
OLD_PROVIDER_FIELD = "agent"


def manager_lane_problem(t, state, status=None):
    """Why a manager ticket may not sit in `state` with `status`, or empty when it may.

    A manager runs in the active lane or waits in the start lane. It never finishes, so it
    never enters the verify lane, never waits on a blocker, and leaves the closed lane to the
    owner's `cancelled` or `closed by other task`."""
    if t.repeat != MANAGER:
        return ""
    if state in (lane("verify"), lane("blocked")):
        return ("%s is a manager ticket: it never enters %s. It runs in %s or waits in %s"
                % (t.id, state, lane("active"), lane("start")))
    if state == lane("closed") and (status or default_status(state)) not in P.OWNER_CLOSES:
        return ("%s is a manager ticket: it enters %s only as `%s` or `%s`"
                % ((t.id, state) + tuple(P.OWNER_CLOSES)))
    return ""


# THE OUTCOME. Every ticket that leaves a `work` or a `verify` session carries `outcome:`, one
# line its session writes: what now works or what was found, in at most `OUTCOME_WORDS` words,
# with no measurement, no caveat and no other ticket. `octopus sessions` prints it for a
# finished agent, and the manager copies that line. It is a field of the ticket, never a line
# of the session's last message, so that message still ends with `WORK IS FINISHED`.
OUTCOME_FIELD = "outcome"
OUTCOME_WORDS = 20
# A launch before this time read no brief that asked for the outcome: its ticket is history.
OUTCOME_SINCE = "2026-10-27 14:30"
_CAVEAT = re.compile(r"\b(?:not|never|un)[ -]?(?:tested|verified|checked|proven|measured)\b"
                     r"|\bcould not (?:test|verify|check)\b|\bbefore this ticket\b", re.I)
_MEASURE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:%|ms|s|sec|secs|seconds?|min|minutes?|hours?|"
                      r"tests?|checks?|runs?|passed|failed|lines?|files?|tickets?|"
                      r"problems?)\b|\b\d+\s*/\s*\d+\b|\b\d+(?:[.,]\d+)?\s*[-–]\s*\d+", re.I)


def outcome_problem(text, own_id=""):
    """Why `text` is no outcome line, or "" when it is one."""
    text = str(text or "")
    if "\n" in text.strip() or "\r" in text.strip():
        return "the outcome is one line"
    words = text.split()
    if not words:
        return "the outcome is empty"
    if len(words) > OUTCOME_WORDS:
        return "the outcome has %d words; at most %d" % (len(words), OUTCOME_WORDS)
    others = sorted(set(_ID_IN_TEXT.findall(text)) - {own_id})
    if others:
        return "the outcome names another ticket (%s); it names only this one" % ", ".join(others)
    m = _CAVEAT.search(text)
    if m:
        return "the outcome carries a caveat (%r); it says what works or what was found" \
            % m.group(0)
    m = _MEASURE.search(text)
    if m:
        return "the outcome carries a measurement (%r); the measurement goes on the ticket" \
            % m.group(0)
    return ""


_ID_IN_TEXT = re.compile(r"\b[A-Z]+\d{3,}\b")


def outcome_due(t):
    """Whether `t` must carry `outcome:`: it is in the verify or the closed lane, it is no
    manager and not `cancelled`, and its last `work` or `verify` launch read a brief that asks
    for it."""
    if t.state not in (lane("verify"), lane("closed")) or t.repeat == MANAGER:
        return False
    if t.state == lane("closed") and t.status == S("cancelled"):
        return False
    stamps = [e["launched"] for e in t.sessions if e["mode"] in ("work", "verify")]
    return bool(stamps) and max(stamps) >= OUTCOME_SINCE


def set_outcome(t, text):
    """Write `outcome:` on the ticket. Returns the problem of the text, or ""."""
    why = outcome_problem(text, t.id)
    if why:
        return why
    meta, body = parse(_read(t.path))
    meta[OUTCOME_FIELD] = " ".join(str(text).split())
    _write(t.path, render(meta, body))
    t.meta[OUTCOME_FIELD] = meta[OUTCOME_FIELD]
    return ""


def default_status(state):
    return P.STATUSES[state][0]


def denied_tools(permits):
    """What the launched session cannot reach. Absence denies."""
    return P.denied_tools(permits)


def derive_category(source):
    """Where a ticket's subject lives decides its category: `internal tooling` under a
    `toolingPaths` prefix, `project code` when nothing says otherwise."""
    s = (source or "").strip().strip("`")
    return (CATEGORY_TOOLING if P.TOOLING_PREFIX and s.startswith(P.TOOLING_PREFIX)
            else CATEGORY_CODE)


# SEVERITY is what the user loses. It says nothing about how hard the fix is.
SEVERITIES = ("High", "Medium", "Low")
SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2, "": 3}

# DIFFICULTY is what the fix costs, and `unknown` is a separate answer from `large`.
# `large` means the change IS known and it is big. `unknown` means nobody can say how big
# it is, because the question the ticket asks has not been answered.
DIFFICULTIES = ("small", "medium", "large", "unknown")
DIFF_ORDER = {"small": 0, "medium": 1, "large": 2, "unknown": 3}

# A ticket is a bug or a feature, and its title says which. A bug title starts with `Bug: `
# and says what is wrong. A feature title starts with `Feature: ` and says what the program
# should do. The first section of the body follows the title.
BUG_PREFIX, FEATURE_PREFIX = "Bug:", "Feature:"
SECTION_BUG, SECTION_FEATURE = "What is wrong", "What it should do"


def body_section(title):
    """The heading of the first body section for a ticket with this `title` (str): `What it
    should do` for a feature, `What is wrong` for every other ticket. Returns a str."""
    is_feature = (title or "").strip().lower().startswith(FEATURE_PREFIX.lower())
    return SECTION_FEATURE if is_feature else SECTION_BUG


def priority(t):
    """The order the board and every listing use. PRIORITY IS DERIVED, never stored.

    Severity first, because it is what the user loses. Difficulty second and ascending,
    so among equally bad defects the cheap one goes first.
    """
    return (SEV_ORDER.get(t.severity, 9), DIFF_ORDER.get(t.difficulty, 9), t.id)


EPIC_FILE = "_epic.md"

_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.S)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"^T(\d{4,})$")


# ── the record ──────────────────────────────────────────────────────────────

class Ticket:
    __slots__ = ("path", "state", "epic", "meta", "body")

    def __init__(self, path, state, epic, meta, body):
        self.path, self.state, self.epic = path, state, epic
        self.meta, self.body = meta, body

    @property
    def id(self):
        return self.meta.get("id", "")

    @property
    def title(self):
        return self.meta.get("title", "")

    @property
    def status(self):
        """The sub-state inside the directory. Absent reads as the state's default."""
        return self.meta.get("status", "") or default_status(self.state)

    @property
    def severity(self):
        return self.meta.get("severity", "")

    @property
    def source(self):
        return self.meta.get("source", "")

    @property
    def claim(self):
        return self.meta.get("claim", "")

    @property
    def session(self):
        """The Claude session id of the last launch on this ticket; `claude --resume` takes it."""
        return self.meta.get("session", "")

    @property
    def launched(self):
        """`<mode> <YYYY-MM-DD HH:MM>` of the last launch, or empty."""
        return self.meta.get("launched", "")

    @property
    def sessions(self):
        """One entry per launched mode, in launch order: `{"mode", "session", "launched"}`.
        Read from `sessions:`; a ticket that only carries `session:` and `launched:` gives
        that one launch."""
        out = []
        for part in self.meta.get("sessions", "").split(";"):
            bits = part.split()
            if len(bits) >= 2 and bits[0] in MODES:
                out.append({"mode": bits[0], "session": bits[1],
                            "launched": " ".join(bits[2:])})
        if not out and self.session:
            mode = (self.launched.split() or ["work"])[0]
            stamp = " ".join(self.launched.split()[1:])
            out.append({"mode": mode if mode in MODES else "work",
                        "session": self.session, "launched": stamp})
        return out

    @property
    def blocked_by(self):
        return [x for x in re.split(r"[,\s]+", self.meta.get("blocked-by", "")) if x]

    @property
    def tags(self):
        return [x for x in re.split(r"[,\s]+", self.meta.get("tags", "")) if x]

    @property
    def group(self):
        return self.meta.get("group", "")

    @property
    def related(self):
        return [x for x in re.split(r"[,\s]+", self.meta.get("related", "")) if x]

    @property
    def parent(self):
        """The ticket this one is a part of, or empty. Children are derived from this."""
        return self.meta.get("parent", "").strip()

    @property
    def category(self):
        """One of `P.CATEGORIES`. Absent is derived from the source, so it is never empty.
        A ticket that still carries the old field `kind` reads through `LEGACY_KINDS` until
        `migrate` rewrites it."""
        if self.meta.get("category", ""):
            return self.meta["category"]
        old = self.meta.get("kind", "")
        return LEGACY_KINDS.get(old, old) if old else derive_category(self.source)

    @property
    def difficulty(self):
        """How much is unknown and how many sites. Empty means nobody has sized it."""
        return self.meta.get("difficulty", "") or "unknown"

    @property
    def permits(self):
        """What this ticket may use. Empty means read and report, and nothing else. A Tester
        ticket (`repeat: test`) also holds the project's `testerPermits` on every run, whatever
        its `permits:` line says."""
        out = [x for x in re.split(r"[,\s]+", self.meta.get("permits", "")) if x]
        if self.repeat == "test":
            out += [p for p in P.TESTER_PERMITS if p not in out]
        return out

    @property
    def repeat(self):
        """The role of a repeatable ticket, or empty for a ticket that closes once."""
        return self.meta.get("repeat", "")

    @property
    def run(self):
        """The number of the run a repeatable ticket is on. The first run is 1."""
        try:
            return max(1, int(self.meta.get("run", "") or 1))
        except ValueError:
            return 1

    @property
    def has_plan(self):
        """A plan-only run writes `## Plan` into the body; the next run executes it."""
        return re.search(r"^##\s+Plan\s*$", self.body, re.M) is not None

    def searchable(self):
        return " ".join([self.id, self.title, self.status, self.source, self.body,
                         self.meta.get("tags", ""),
                         self.meta.get("permits", "")]).lower()


def _read(p):
    with io.open(p, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(p, text):
    """Write atomically, and survive Windows holding the destination for a moment.

    `os.replace` onto a file that was created or moved seconds ago fails with
    `WinError 5` while a scanner or an indexer still has it open. The failure is
    transient and a short retry clears it; without one, a `move` that rewrites the
    status leaves the ticket written and a `.tmp` beside it.

    Each call writes its own temp file, so two processes that write one file at
    the same moment never replace from the same name.
    """
    import tempfile
    p = Path(p)
    fd, name = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".", suffix=".tmp")
    tmp = Path(name)
    try:
        with io.open(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    delay, last = 0.05, None
    for _ in range(6):
        try:
            os.replace(tmp, p)
            return
        except FileNotFoundError:
            tmp.unlink(missing_ok=True)
            raise
        except PermissionError as exc:
            last = exc
            try:                                  # a read-only attribute is the other cause
                if p.exists():
                    os.chmod(p, 0o666)
            except OSError:
                pass
            import time
            time.sleep(delay)
            delay *= 2
    try:
        tmp.unlink()
    except OSError:
        pass
    raise last


def parse(text):
    """`--- key: value --- body` -> (dict, body). A file with no frontmatter has none."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).replace("\r\n", "\n").split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        k, sep, v = line.partition(":")
        if sep:
            v = v.strip()
            # A YAML-style quoted value (`title: "Bug: ..."`) means the text inside.
            if len(v) >= 2 and v[0] == v[-1] == '"' and '"' not in v[1:-1]:
                v = v[1:-1]
            meta[k.strip().lower()] = v
    return meta, m.group(2)


def render(meta, body):
    keys = ["id", "title", "category", "severity", "difficulty", "status", "outcome", "source",
            "created", "claim", "session", "launched", "sessions", "blocked-by", "parent",
            "group", "related",
            "permits", "remote-control", "tags", "repeat", "run", "last-run", "last-result"]
    keys += [k for k in meta if k not in keys]
    out = ["---"]
    for k in keys:
        if k in meta:
            out.append("%s: %s" % (k, meta[k]))
    out.append("---")
    out.append("")
    return "\n".join(out) + body.lstrip("\n")


# ── the tree ────────────────────────────────────────────────────────────────

def ensure_tree():
    for s in P.STATES:
        (P.TODO / s).mkdir(parents=True, exist_ok=True)
    if P.groups_store == "files":
        P.GROUPS_DIR.mkdir(parents=True, exist_ok=True)


def load_all():
    """Every ticket in the tree, in (state, epic, id) order. Epic files are not tickets."""
    out = []
    for state in P.STATES:
        base = P.TODO / state
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.md")):
            if p.name == EPIC_FILE:
                continue
            rel = p.relative_to(base)
            epic = rel.parts[0] if len(rel.parts) > 1 else ""
            meta, body = parse(_read(p))
            out.append(Ticket(p, state, epic, meta, body))
    return out


def load_epics():
    """slug -> (state, title, path-of-_epic.md or None)."""
    found = {}
    for state in P.STATES:
        base = P.TODO / state
        if not base.is_dir():
            continue
        for d in sorted(x for x in base.iterdir() if x.is_dir()):
            ep = d / EPIC_FILE
            title = ""
            if ep.exists():
                meta, _ = parse(_read(ep))
                title = meta.get("title", "")
            found[d.name] = (state, title, ep if ep.exists() else None)
    return found


def next_id():
    """One past the highest id ever issued, so a deleted ticket's number is not reused."""
    highest = 0
    if P.IDFILE.exists():
        try:
            highest = int(_read(P.IDFILE).strip() or 0)
        except ValueError:
            highest = 0
    for t in load_all():
        m = _ID_RE.match(t.id)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def commit_id(n):
    _write(P.IDFILE, "%d\n" % n)


def slug(text, words=8):
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return "-".join(s.split("-")[:words])[:60] or "ticket"


def find(tickets, ident):
    ident = ident.strip()
    up = ident.upper()
    hits = [t for t in tickets if t.id.upper() == up]
    if hits:
        return hits[0]
    hits = [t for t in tickets if t.path.stem.lower().startswith(ident.lower())]
    if len(hits) == 1:
        return hits[0]
    return None


# ── the index ───────────────────────────────────────────────────────────────

def build_index(tickets, epics):
    per = {s: [t for t in tickets if t.state == s] for s in P.STATES}
    lines = []
    lines.append("# TODO index")
    lines.append("")
    lines.append("Generated by `%s index`. Every mutating command rewrites" % P.cli)
    lines.append("it, and `%s check` exits 1 when it no longer matches the" % P.cli)
    lines.append("tree. Do not edit this file: the tree is the truth and this is the view.")
    lines.append("")
    lines.append("A ticket's state is the directory it sits in. An epic is a directory inside")
    if P.groups_store == "files":
        lines.append("a state, and it moves as one unit. A group is a topic (`%s/groups/`);"
                     % P.todo_dir)
        lines.append("every open ticket is in one, and Done leaves it."
                     if P.groups_required else "an open ticket may sit in one.")
    else:
        lines.append("a state, and it moves as one unit.")
    lines.append("")
    total = len(tickets)
    head = "  ·  ".join("**%s** %d" % (P.STATE_LABEL[s], len(per[s])) for s in P.STATES)
    lines.append("%d tickets  —  %s" % (total, head))
    lines.append("")

    sev = {}
    for t in tickets:
        sev[t.severity] = sev.get(t.severity, 0) + 1
    if sev:
        lines.append("Severity: " + "  ·  ".join(
            "%s %d" % (k or "none", sev[k])
            for k in sorted(sev, key=lambda x: SEV_ORDER.get(x, 9))))
        lines.append("")

    if P.groups_store == "files":
        groups = load_group_files()
        if groups:
            closed = lane("closed")
            lines.append("## Groups")
            lines.append("")
            lines.append("| Group | Open | What it is for |")
            lines.append("|---|---|---|")
            for slug in sorted(groups):
                n = sum(1 for t in tickets if t.group == slug and t.state != closed)
                lines.append("| `%s` | %d | %s |" % (slug, n, _esc(groups[slug][0])))
            lines.append("")

    for s in P.STATES:
        rows = per[s]
        lines.append("## %s (%d)" % (P.STATE_LABEL[s], len(rows)))
        lines.append("")
        if not rows:
            lines.append("_empty_")
            lines.append("")
            continue

        loose = [t for t in rows if not t.epic]
        grouped = {}
        for t in rows:
            if t.epic:
                grouped.setdefault(t.epic, []).append(t)

        for name in sorted(grouped):
            title = epics.get(name, ("", "", None))[1]
            lines.append("### %s — epic, %d ticket(s)%s"
                         % (name, len(grouped[name]), (" — " + title) if title else ""))
            lines.append("")
            lines.extend(_table(grouped[name], s, name))
            lines.append("")
        if loose:
            if grouped:
                lines.append("### Ungrouped (%d)" % len(loose))
                lines.append("")
            lines.extend(_table(loose, s, ""))
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _table(rows, state, epic):
    want_claim = any(t.claim for t in rows)
    want_block = any(t.blocked_by for t in rows)
    want_parent = any(t.parent for t in rows)
    want_group = P.groups_store == "files" and any(t.group for t in rows)
    head = "| Id | Sev | Difficulty | Status | Ticket | Source |"
    sep = "|---|---|---|---|---|---|"
    if want_group:
        head += " Group |"
        sep += "---|"
    if want_claim:
        head += " Claim |"
        sep += "---|"
    if want_block:
        head += " Blocked by |"
        sep += "---|"
    if want_parent:
        head += " Parent |"
        sep += "---|"
    out = [head, sep]
    for t in sorted(rows, key=priority):
        rel = "%s/%s" % (state, epic) if epic else state
        link = "[%s](%s/%s)" % (_esc(t.title) or t.id, rel, t.path.name)
        # The Source often carries its own backticks. Nesting them inside a code span
        # closes it early and the rest of the cell renders as prose.
        src = _esc(t.source).replace("`", "")
        row = "| `%s` | %s | %s | %s | %s | %s |" % (
            t.id, t.severity or "-", t.difficulty or "-", t.status, link,
            ("`%s`" % src) if src else "-")
        if want_group:
            row += " %s |" % (("`%s`" % t.group) if t.group else "-")
        if want_claim:
            row += " %s |" % (t.claim or "-")
        if want_block:
            row += " %s |" % (", ".join("`%s`" % b for b in t.blocked_by) or "-")
        if want_parent:
            row += " %s |" % (("`%s`" % t.parent) if t.parent else "-")
        out.append(row)
    return out


def _esc(s):
    return s.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def write_index(quiet=False):
    ensure_tree()
    tickets = load_all()
    text = build_index(tickets, load_epics())
    old = _read(P.INDEX) if P.INDEX.exists() else None
    if old != text:
        _write(P.INDEX, text)
        if not quiet:
            print("INDEX.md rewritten - %d tickets" % len(tickets))
    elif not quiet:
        print("INDEX.md already current - %d tickets" % len(tickets))
    if P.groups_store == "map" and P.GROUPS_MAP.exists():
        write_groups(tickets)
    return tickets


# ── groups ──────────────────────────────────────────────────────────────────
# A project stores its groups one of two ways, `groups.store` in its project.json:
#
#   map    `TODO/.groups.json` maps a group name to the fine `group:` slugs and the ticket
#          ids it holds. `TODO/GROUPS.md` is derived from it and the tree: open tickets
#          only, so a ticket that reaches Verification or Done leaves its group on the next
#          rewrite. An `autoGroups` entry is one more group, by tag, that cuts across the
#          others.
#   files  one topic file per group, `TODO/groups/<slug>.md`, and a ticket names its group
#          in `group:`. With `groups.required`, `check` fails an open ticket in no group.

# What the board remembers between visits. It lives in the project, beside the tickets it
# describes, so the setting follows the tree and not one browser on one machine.
BOARD_VIEWS = ("lanes", "groups", "sessions", "repeatable", "artifacts", "project")
# A view the board once had, and the view that took its place, so an old `.board.json`
# opens where its owner left it.
BOARD_VIEW_RENAMED = {"manage": "groups"}
# The card order inside a lane. `priority` is severity first, then difficulty ascending;
# `newest` and `oldest` follow the `created` date, then the id; `updated` is the last write
# to the ticket file.
BOARD_SORTS = ("priority", "newest", "oldest", "updated")
# Where a launched session's terminal lives. `external` is a Windows Terminal tab, or a
# console window of its own; `own` is a pseudoconsole `session_pty.py` holds with no window,
# read and driven from the page.
BOARD_TERMINALS = ("external", "own")
# What a click on a session does with an external terminal: raise its window, or show its
# screen in a popup on the page. An own terminal has no window, so it always shows.
BOARD_FOCUS = ("window", "popup")
# Whether the filter row of the header is shown.
BOARD_FILTERS = ("hidden", "shown")
# Whether the Sessions view also lists the sessions of the other projects the server serves.
BOARD_OTHERS = ("hidden", "shown")
# The width of a lane column the owner dragged, in pixels. A lane with no width takes the
# page's default.
BOARD_LANE_WIDTH = (160, 1200)


def load_board():
    """The board's remembered view, terminal kind, focus behaviour, filter row, whether
    the Sessions view lists the other projects, and the lane columns: their order, their
    widths and the ones collapsed."""
    import json
    try:
        d = json.loads(_read(P.BOARD))
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    view, shut, order = d.get("view"), d.get("shut"), d.get("sort")
    term, focus, filt = d.get("terminal"), d.get("focus"), d.get("filters")
    others = d.get("others")
    order_l, width_l, shut_l = d.get("laneOrder"), d.get("laneWidth"), d.get("laneShut")
    if isinstance(view, str):
        view = BOARD_VIEW_RENAMED.get(view, view)
    return {"view": view if view in BOARD_VIEWS else "lanes",
            "sort": order if order in BOARD_SORTS else "priority",
            "shut": sorted(str(x) for x in shut) if isinstance(shut, list) else [],
            "terminal": term if term in BOARD_TERMINALS else "external",
            "focus": focus if focus in BOARD_FOCUS else "popup",
            "filters": filt if filt in BOARD_FILTERS else "hidden",
            "others": others if others in BOARD_OTHERS else "hidden",
            "laneOrder": _lane_list(order_l),
            "laneWidth": _lane_widths(width_l),
            "laneShut": _lane_list(shut_l)}


def _lane_list(x):
    """Lane directories, in order and once each; anything else is dropped."""
    out = []
    for v in x if isinstance(x, list) else []:
        if isinstance(v, str) and v and v not in out:
            out.append(v)
    return out


def _lane_widths(x):
    """Lane directory -> width in pixels, clamped to BOARD_LANE_WIDTH."""
    lo, hi = BOARD_LANE_WIDTH
    out = {}
    for k, v in (x.items() if isinstance(x, dict) else ()):
        try:
            out[str(k)] = max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            pass
    return out


def save_board(view=None, shut=None, sort=None, terminal=None, focus=None,
               filters=None, others=None, lane_order=None, lane_width=None, lane_shut=None):
    """Write the board's view, the groups it holds collapsed, the terminal kind, the focus
    behaviour, the filter row, whether the Sessions view lists the other projects, and the
    lane columns: their order, their widths and the ones collapsed. Returns what is now on
    disk."""
    import json
    d = load_board()
    if view is not None:
        if view not in BOARD_VIEWS:
            raise ValueError("unknown board view %r" % view)
        d["view"] = view
    if sort is not None:
        if sort not in BOARD_SORTS:
            raise ValueError("unknown card order %r" % sort)
        d["sort"] = sort
    for key, value, allowed in (("terminal", terminal, BOARD_TERMINALS),
                                ("focus", focus, BOARD_FOCUS),
                                ("filters", filters, BOARD_FILTERS),
                                ("others", others, BOARD_OTHERS)):
        if value is not None:
            if value not in allowed:
                raise ValueError("unknown %s %r; one of %s" % (key, value, ", ".join(allowed)))
            d[key] = value
    if shut is not None:
        if not isinstance(shut, list):
            raise ValueError("the collapsed groups are a list of names")
        d["shut"] = sorted({str(x) for x in shut})
    for key, value in (("laneOrder", lane_order), ("laneShut", lane_shut)):
        if value is not None:
            if not isinstance(value, list):
                raise ValueError("%s is a list of lane directories" % key)
            d[key] = _lane_list(value)
    if lane_width is not None:
        if not isinstance(lane_width, dict):
            raise ValueError("laneWidth maps a lane directory to a width in pixels")
        d["laneWidth"] = _lane_widths(lane_width)
    P.BOARD.parent.mkdir(parents=True, exist_ok=True)
    _write(P.BOARD, json.dumps(d, indent=1) + "\n")
    return d


# ── groups stored as a map ──────────────────────────────────────────────────

def load_groups():
    """The map store: group name -> fine slugs and ticket ids, in the declared order. The
    files store gives an empty map."""
    import json
    if P.groups_store != "map":
        return {}
    return json.loads(_read(P.GROUPS_MAP)) if P.GROUPS_MAP.exists() else {}


def save_groups(mapping):
    import json
    _write(P.GROUPS_MAP, json.dumps(mapping, indent=1, ensure_ascii=False) + "\n")


def todo_groups(tickets, mapping):
    """(members by group name, open tickets in no group). Only open tickets count.

    A ticket belongs to the group that names its id, and otherwise to the group that names
    its fine `group:` slug. The id wins, so a ticket put in a group by hand leaves the
    group its slug would have placed it in, and no ticket is counted twice."""
    open_t = [t for t in tickets if t.state in P.OPEN_STATES and not t.repeat]
    known = {t.id for t in open_t}
    by_id, by_slug = {}, {}
    for name, items in mapping.items():
        for it in items:
            (by_id if it in known else by_slug).setdefault(it, name)
    members = {name: [] for name in mapping}
    ungrouped = []
    for t in open_t:
        name = by_id.get(t.id) or (by_slug.get(t.group) if t.group else None)
        if name in members:
            members[name].append(t)
        else:
            ungrouped.append(t)
    return members, ungrouped


def group_names():
    """Every group, in the order the store declares them."""
    if P.groups_store == "files":
        return sorted(load_group_files())
    return list(load_groups().keys())


def group_assign(ids, name):
    """Put these ticket ids in this group and in no other. An empty `name` takes them out of
    every group (map store)."""
    if P.groups_store == "files":
        return group_file_assign(ids, name)
    mapping = load_groups()
    want = {i.upper() for i in ids}
    for items in mapping.values():
        for it in list(items):
            if it.upper() in want:
                items.remove(it)
    if name:
        if name not in mapping:
            raise ValueError("no Todo group %r" % name)
        mapping[name] += [i for i in ids if i not in mapping[name]]
    _save_and_render(mapping)
    return mapping


def group_create(name, title=None, description=None):
    """Add an empty group. A group with no ticket is legal; it is a place to drop one."""
    if P.groups_store == "files":
        return group_file_create(name, title, description)
    name = (name or "").strip()
    if not name:
        raise ValueError("a group needs a name")
    mapping = load_groups()
    if name in mapping:
        raise ValueError("%r is already a Todo group" % name)
    mapping[name] = []
    _save_and_render(mapping)
    return mapping


def group_rename(old, new):
    """Rename a group and keep its place in the order."""
    if P.groups_store == "files":
        return group_file_rename(old, new)
    new = (new or "").strip()
    mapping = load_groups()
    if old not in mapping:
        raise ValueError("no Todo group %r" % old)
    if not new:
        raise ValueError("a group needs a name")
    if new != old and new in mapping:
        raise ValueError("%r is already a Todo group" % new)
    out = {}
    for k, v in mapping.items():
        out[new if k == old else k] = v
    _save_and_render(out)
    return out


def group_delete(name, into=None):
    """Drop a group. In the map store its tickets fall into no group."""
    if P.groups_store == "files":
        return group_file_delete(name, into)
    mapping = load_groups()
    if name not in mapping:
        raise ValueError("no Todo group %r" % name)
    del mapping[name]
    _save_and_render(mapping)
    return mapping


def _save_and_render(mapping):
    save_groups(mapping)
    write_groups(load_all())


def auto_group_tickets(tickets, ag):
    """The open tickets carrying the tag of this auto group, whatever group they sit in."""
    return [t for t in tickets if t.state in P.OPEN_STATES and ag["tag"] in t.tags]


def auto_group(name):
    """The auto group of this name, or None."""
    for ag in P.auto_groups:
        if ag["name"] == name:
            return ag
    return None


def build_groups(members, ungrouped, autos):
    n = sum(len(v) for v in members.values())
    lines = ["# Todo groups", "",
             "%d open tickets in %d groups. A group holds tickets that share a `group`, a "
             "`related` or a `blocked-by` field, so one session can work them together. Name "
             "a group to work it. Tickets in Verification and Done are left out. "
             "`%s groups` rebuilds this file and lists the open tickets in "
             "no group." % (n, len(members), P.cli), ""]
    for name, ts in members.items():
        lines += ["## %s (%d)" % (name, len(ts)), ""]
        for t in sorted(ts, key=lambda t: t.id):
            state = "" if t.state == lane("start") else " (%s)" % t.state
            lines.append("- %s — %s%s" % (t.id, t.title, state))
        lines.append("")
    if ungrouped:
        lines += ["## In no group (%d)" % len(ungrouped), ""]
        lines += ["- %s — %s" % (t.id, t.title) for t in sorted(ungrouped, key=lambda t: t.id)]
        lines.append("")
    for ag, ts in autos:
        lines += ["## %s (%d)" % (ag["name"], len(ts)), "",
                  "Tagged `%s`. Each also sits in its Todo group above." % ag["tag"], ""]
        lines += ["- %s — %s" % (t.id, t.title) for t in sorted(ts, key=lambda t: t.id)]
        lines.append("")
    return "\n".join(lines)


def write_groups(tickets):
    members, ungrouped = todo_groups(tickets, load_groups())
    autos = [(ag, auto_group_tickets(tickets, ag)) for ag in P.auto_groups]
    text = build_groups(members, ungrouped, autos)
    if not P.GROUPS.exists() or _read(P.GROUPS) != text:
        _write(P.GROUPS, text)
    return members, ungrouped


# ── groups stored as topic files ────────────────────────────────────────────

_GROUP_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_GROUP_BODY = "\n## What this group is for\n\n%s\n"


def load_group_files():
    """slug -> (title, path). A group is a topic file under TODO/groups/, never a ticket."""
    found = {}
    if P.GROUPS_DIR.is_dir():
        for f in sorted(P.GROUPS_DIR.glob("*.md")):
            meta, _ = parse(_read(f))
            found[f.stem] = (meta.get("title", "") or f.stem, f)
    return found


def _group_slug(slug):
    slug = (slug or "").strip()
    if not _GROUP_SLUG_RE.match(slug):
        raise ValueError("%r is not a group slug: lowercase letters, digits and dashes" % slug)
    return slug


def _no_group(slug, groups):
    return ValueError("no group %r. One of: %s" % (slug, ", ".join(sorted(groups)) or "none"))


def group_file_create(slug, title=None, description=None):
    """Create TODO/groups/<slug>.md. Returns its path."""
    slug = _group_slug(slug)
    f = P.GROUPS_DIR / ("%s.md" % slug)
    if f.exists():
        raise ValueError("group %r already exists" % slug)
    ensure_tree()
    meta = {"id": slug, "title": title or slug, "created": _today()}
    _write(f, render(meta, _GROUP_BODY % (description or "TODO: describe it.")))
    return f


def group_file_rename(old, new):
    """Rename a group's file and rewrite `group:` on every ticket that names it. Returns the
    ids of those tickets."""
    new = _group_slug(new)
    groups = load_group_files()
    if old not in groups:
        raise _no_group(old, groups)
    if new == old:
        return []
    if new in groups:
        raise ValueError("group %r already exists" % new)
    src = groups[old][1]
    meta, body = parse(_read(src))
    meta["id"] = new
    _write(P.GROUPS_DIR / ("%s.md" % new), render(meta, body))
    src.unlink()
    moved = []
    for t in load_all():
        if t.group == old:
            t.meta["group"] = new
            _write(t.path, render(t.meta, t.body))
            moved.append(t.id)
    write_index(quiet=True)
    return moved


def group_file_assign(ids, slug):
    """Put these tickets in this group. A closed ticket stays out of every group. Returns
    (id, previous group) for each ticket that changed."""
    groups = load_group_files()
    if slug not in groups:
        raise _no_group(slug, groups)
    tickets = load_all()
    found = []
    for ident in ids:
        t = find(tickets, ident)
        if t is None:
            raise ValueError("no ticket %r" % ident)
        found.append(t)
    changed = []
    for t in found:
        if t.state == lane("closed") or t.group == slug:
            continue
        changed.append((t.id, t.group))
        t.meta["group"] = slug
        _write(t.path, render(t.meta, t.body))
    write_index(quiet=True)
    return changed


def group_file_delete(slug, into=None):
    """Delete a group's file. Refused while an open ticket names the group, unless `into`
    names the group those tickets move to first. Returns the ids moved."""
    groups = load_group_files()
    if slug not in groups:
        raise _no_group(slug, groups)
    if into is not None and (into == slug or into not in groups):
        raise ValueError("--into must name another existing group, not %r" % into)
    members = [t for t in load_all() if t.group == slug and t.state != lane("closed")]
    if members and into is None:
        raise ValueError("%d open ticket(s) name group %r: %s. Regroup them, or pass --into "
                         "<other>" % (len(members), slug, " ".join(t.id for t in members)))
    moved = [i for i, _ in group_file_assign([t.id for t in members], into)] if members else []
    groups[slug][1].unlink()
    write_index(quiet=True)
    return moved


def group_table(tickets=None):
    """Every topic group as data: slug, title, description, open count, count per lane and
    the open ticket ids; and the open tickets in no group."""
    tickets = load_all() if tickets is None else tickets
    closed = lane("closed")
    rows = []
    for slug, (title, path) in sorted(load_group_files().items()):
        body = parse(_read(path))[1]
        m = re.search(r"^## What this group is for\s*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
        members = [t for t in tickets if t.group == slug]
        rows.append({
            "slug": slug,
            "title": title,
            "description": " ".join(m.group(1).split()) if m else "",
            "open": sum(1 for t in members if t.state != closed),
            "states": {s: n for s in P.STATES
                       for n in [sum(1 for t in members if t.state == s)] if n},
            "tickets": [t.id for t in members if t.state != closed],
        })
    loose = [t.id for t in tickets if t.state != closed and not t.group]
    return {"groups": rows, "ungrouped": loose}


def group_members(name, tickets=None):
    """The open tickets of one group, whatever the store, or None when there is no such
    group. An auto group is a group too."""
    tickets = load_all() if tickets is None else tickets
    ag = auto_group(name)
    if ag is not None:
        return auto_group_tickets(tickets, ag)
    if P.groups_store == "files":
        if name not in load_group_files():
            return None
        return [t for t in tickets if t.group == name and t.state != lane("closed")]
    mapping = load_groups()
    if name not in mapping:
        return None
    return todo_groups(tickets, mapping)[0][name]


def cmd_groups(args):
    """The map store rebuilds TODO/GROUPS.md. `--set NAME ITEM...` puts fine group slugs or
    ticket ids into a group, new or existing, and takes them out of any other. `--table`
    prints every group as a markdown table. Without either, list the open tickets in no
    group with the groups their relations sit in. The files store prints each topic group
    with its count per lane, or the table as JSON with `--json`."""
    if getattr(args, "run", None):
        return run_group(args.run, args)
    if P.groups_store == "files":
        return _cmd_groups_files(args)
    mapping = load_groups()
    if getattr(args, "table", False):
        tickets = load_all()
        members, ungrouped = todo_groups(tickets, mapping)
        print("| Group | Open | Fine groups |")
        print("|---|---|---|")
        for name, items in mapping.items():
            print("| %s | %d | %s |" % (name, len(members[name]),
                                        ", ".join("`%s`" % i for i in items)))
        for ag in P.auto_groups:
            print("| %s | %d | tag `%s`; each ticket also sits in its group above |"
                  % (ag["name"], len(auto_group_tickets(tickets, ag)), ag["tag"]))
        if ungrouped:
            print("| In no group | %d | %s |"
                  % (len(ungrouped), ", ".join(t.id for t in sorted(ungrouped, key=lambda t: t.id))))
        return 0
    if getattr(args, "json", False):
        import json
        tickets = load_all()
        members, ungrouped = todo_groups(tickets, mapping)
        print(json.dumps({"groups": [{"name": n, "items": list(mapping[n]),
                                      "tickets": [t.id for t in members[n]]} for n in mapping],
                          "ungrouped": [t.id for t in ungrouped]}, indent=2))
        return 0
    if getattr(args, "set", None):
        name, items = args.set[0], args.set[1:]
        if not items:
            print("Error: --set needs a group name and at least one slug or id", file=sys.stderr)
            return 2
        for other in mapping.values():
            for it in items:
                if it in other:
                    other.remove(it)
        mapping.setdefault(name, [])
        mapping[name] += [it for it in items if it not in mapping[name]]
        mapping = {k: v for k, v in mapping.items() if v}
        save_groups(mapping)
    tickets = load_all()
    members, ungrouped = write_groups(tickets)
    where = {t.id: name for name, ts in members.items() for t in ts}
    for t in sorted(ungrouped, key=lambda t: t.id):
        near = sorted({where[i] for i in t.related + t.blocked_by if i in where})
        print("%s  %-8s  group: %-28s  %s" % (t.id, t.difficulty, t.group or "-", t.title[:60]))
        if near:
            print("        relations sit in: %s" % "; ".join(near))
    for name, ts in members.items():
        if not 5 <= len(ts) <= 10:
            print("%s has %d tickets" % (name, len(ts)))
    print("%d groups, %d open tickets placed, %d in no group"
          % (len(members), sum(len(v) for v in members.values()), len(ungrouped)))
    return 0


def _cmd_groups_files(args):
    table = group_table()
    if getattr(args, "json", False):
        import json
        print(json.dumps(table, indent=2))
        return 0
    if not table["groups"]:
        print("no groups yet: %s group <slug> --title \"...\"" % P.cli)
        return 0
    for g in table["groups"]:
        detail = "  ".join("%s %d" % (P.STATE_LABEL[s], n) for s, n in g["states"].items())
        print("%-24s %3d  %s%s" % (g["slug"], sum(g["states"].values()), g["title"],
                                   ("   (%s)" % detail) if detail else ""))
    if table["ungrouped"]:
        print("")
        print("in no group: " + " ".join(table["ungrouped"]))
    return 0


def run_group(name, args):
    """Launch one session per start-lane ticket of a group: `plan` when the difficulty is
    `unknown`, `work` otherwise. Tickets in any other lane are printed with their lane and
    left alone."""
    members = group_members(name)
    if members is None:
        print("Error: no group %r; `groups` lists them" % name, file=sys.stderr)
        return 2
    if not members:
        print("%s has no open ticket" % name)
        return 0
    rc = 0
    start = lane("start")
    for t in sorted(members, key=lambda t: t.id):
        if t.state != start:
            print("%s  %-8s  %s  not launched" % (t.id, t.difficulty, t.state))
            continue
        mode = "plan" if t.difficulty == "unknown" else "work"
        a = argparse.Namespace(id=t.id, mode=mode, note=getattr(args, "note", None),
                               model=getattr(args, "model", None),
                               effort=getattr(args, "effort", None),
                               no_fable=getattr(args, "no_fable", False),
                               terminal=getattr(args, "terminal", None),
                               dry_run=getattr(args, "dry_run", False), auto=True)
        rc = cmd_work(a) or rc
    return rc


def cmd_group(args):
    """Create a group, retitle it, rename it or delete it."""
    try:
        if args.into and not args.delete:
            raise ValueError("--into goes with --delete")
        if args.rename:
            moved = group_rename(args.slug, args.rename)
            if P.groups_store == "files":
                print("group %s -> %s, %d ticket(s) rewritten%s"
                      % (args.slug, args.rename, len(moved),
                         (": " + " ".join(moved)) if moved else ""))
            else:
                print("group %s -> %s" % (args.slug, args.rename))
            return 0
        if args.delete:
            moved = group_delete(args.slug, args.into)
            if P.groups_store == "files":
                print("group %s deleted%s" % (args.slug, ("; %s -> %s" % (" ".join(moved), args.into))
                                              if moved else ""))
            else:
                print("group %s deleted" % args.slug)
            return 0
        if P.groups_store == "map":
            if args.slug in load_groups():
                print("group %s exists" % args.slug)
                return 0
            group_create(args.slug)
            print("group %s created" % args.slug)
            return 0
        f = P.GROUPS_DIR / ("%s.md" % _group_slug(args.slug))
        if not f.exists():
            print(group_file_create(args.slug, args.title, args.description)
                  .relative_to(P.ROOT).as_posix())
            return 0
    except ValueError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2
    meta, body = parse(_read(f))
    if args.title:
        meta["title"] = args.title
    if args.description:
        body = _GROUP_BODY % args.description
    _write(f, render(meta, body))
    print("%s updated" % f.relative_to(P.ROOT).as_posix())
    return 0


def cmd_regroup(args):
    """Put tickets in a group."""
    tickets = load_all()
    try:
        if P.groups_store == "map":
            ids = []
            for ident in args.ids:
                t = find(tickets, ident)
                if t is None:
                    raise ValueError("no ticket %r" % ident)
                ids.append(t.id)
            group_assign(ids, args.slug)
            for i in ids:
                print("%s  -> %s" % (i, args.slug))
            return 0
        changed = dict(group_file_assign(args.ids, args.slug))
    except ValueError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2
    tickets = load_all()
    for ident in args.ids:
        t = find(tickets, ident)
        if t.id in changed:
            print("%s  %s -> %s" % (t.id, changed[t.id] or "(none)", args.slug))
        elif t.state == lane("closed"):
            print("%s is Done and stays out of every group" % t.id)
        else:
            print("%s  already in %s" % (t.id, args.slug))
    return 0


# ── commands ────────────────────────────────────────────────────────────────

def cmd_index(args):
    write_index(quiet=args.quiet)
    return 0


def cmd_check(args):
    ensure_tree()
    tickets = load_all()
    problems = list(P.problems)
    for d in sorted(x for x in P.TODO.iterdir() if x.is_dir()):
        if d.name.startswith(".") or d.name in P.STATES:
            continue
        if d.name == "groups" and P.groups_store == "files":
            continue
        if any(d.rglob("*.md")):
            problems.append("%s/%s holds tickets and is not a lane" % (P.todo_dir, d.name))
    seen = {}
    for t in tickets:
        rel = t.path.relative_to(P.TODO)
        if not t.id:
            problems.append("%s has no id" % rel)
        elif not _ID_RE.match(t.id):
            problems.append("%s has a malformed id %r" % (rel, t.id))
        elif t.id in seen:
            problems.append("%s reuses the id of %s" % (rel, seen[t.id]))
        else:
            seen[t.id] = rel
        if not t.title:
            problems.append("%s has no title" % rel)
        if t.severity and t.severity not in SEVERITIES:
            problems.append("%s has severity %r, not one of %s"
                            % (rel, t.severity, ", ".join(SEVERITIES)))
        if "kind" in t.meta:
            problems.append("%s carries the old field `kind`; run: %s migrate" % (rel, P.cli))
        elif t.meta.get("category", "") and t.category not in P.CATEGORIES:
            problems.append("%s has category %r, not one of %s"
                            % (rel, t.category, ", ".join(P.CATEGORIES)))
        if t.difficulty and t.difficulty not in DIFFICULTIES:
            problems.append("%s has difficulty %r, not one of %s"
                            % (rel, t.difficulty, ", ".join(DIFFICULTIES)))
        if t.repeat and t.repeat not in REPEAT_ROLES:
            problems.append("%s has repeat %r, not one of %s"
                            % (rel, t.repeat, ", ".join(REPEAT_ROLES)))
        rc = t.meta.get(RC_FIELD, "")
        if rc and rc not in RC_VALUES:
            problems.append("%s has %s %r, not one of %s"
                            % (rel, RC_FIELD, rc, ", ".join(RC_VALUES)))
        why = manager_lane_problem(t, t.state, t.status)
        if why:
            problems.append("%s: %s" % (rel, why))
        elif t.repeat and t.state == lane("closed") and t.status == S("done"):
            problems.append("%s is repeatable and sits in %s as %s; a passed run returns "
                            "it to %s - run: %s restart %s --no-launch"
                            % (rel, lane("closed"), S("done"), lane("start"), P.cli, t.id))
        for perm in t.permits:
            if perm not in P.PERMITS:
                problems.append("%s grants %r, which is not a permit. One of: %s"
                                % (rel, perm, ", ".join(sorted(P.PERMITS))))
        declared = t.meta.get("status", "")
        if declared and declared not in P.STATUSES[t.state]:
            problems.append("%s is in %s and its status is %r; %s allows only %s"
                            % (rel, t.state, declared, t.state,
                               ", ".join(P.STATUSES[t.state])))
        if not t.path.stem.startswith(t.id + "-") and t.id:
            problems.append("%s is not named after its id %s" % (rel, t.id))
        said = t.meta.get(OUTCOME_FIELD, "")
        if said:
            why = outcome_problem(said, t.id)
            if why:
                problems.append("%s: %s - run: %s outcome %s \"<what now works or what was "
                                "found>\"" % (rel, why, P.cli, t.id))
        elif outcome_due(t):
            problems.append("%s is in %s and has no outcome line - run: %s outcome %s \"<what "
                            "now works or what was found>\"" % (rel, t.state, P.cli, t.id))
    known = set(seen)
    for t in tickets:
        for b in t.blocked_by:
            if b not in known:
                problems.append("%s is blocked by %s, which does not exist"
                                % (t.path.relative_to(P.TODO), b))
        if t.state == lane("blocked") and t.status == S("blocked by other task") \
                and not t.blocked_by:
            problems.append("%s is %s and names no ticket in blocked-by"
                            % (t.path.relative_to(P.TODO), S("blocked by other task")))
        if t.parent:
            rel = t.path.relative_to(P.TODO)
            if t.parent not in known:
                problems.append("%s names parent %s, which does not exist" % (rel, t.parent))
            elif t.parent == t.id:
                problems.append("%s is its own parent" % rel)
            else:
                # Walk up; a loop would never end, so stop at the first repeat.
                by_id = {x.id: x for x in tickets}
                seen_up, cur = set(), t.parent
                while cur and cur in by_id and cur not in seen_up:
                    seen_up.add(cur)
                    cur = by_id[cur].parent
                if cur and cur in seen_up:
                    problems.append("%s sits in a parent loop through %s" % (rel, cur))
    by_id = {t.id: t for t in tickets}
    for t in tickets:
        for r in t.related:
            if r not in known:
                problems.append("%s names %s in related, which does not exist"
                                % (t.path.relative_to(P.TODO), r))
            elif t.id not in by_id[r].related:
                problems.append("%s relates to %s, which does not relate back"
                                % (t.path.relative_to(P.TODO), r))
    for cycle in _blocked_cycles(tickets):
        problems.append("blocked-by cycle: %s" % " -> ".join(cycle))
    if P.groups_store == "files":
        groups = load_group_files()
        closed = lane("closed")
        for t in tickets:
            rel = t.path.relative_to(P.TODO)
            if t.state == closed:
                if t.group:
                    problems.append("%s is %s and still names group %s; %s leaves every group"
                                    % (rel, closed, t.group, closed))
            elif not t.group:
                if P.groups_required:
                    problems.append("%s is in no group. `%s regroup %s <slug>`; `groups` "
                                    "lists them" % (rel, P.cli, t.id))
            elif t.group not in groups:
                problems.append("%s names group %r, which has no %s/groups/%s.md"
                                % (rel, t.group, P.todo_dir, t.group))
    closed = lane("closed")
    for t in tickets:
        kids = children_of(tickets, t.id)
        if t.state == closed and any(k.state != closed for k in kids):
            k = next(k for k in kids if k.state != closed)
            problems.append("%s is %s while its child %s is in %s"
                            % (t.path.relative_to(P.TODO), closed, k.id, k.state))

    # An open ticket that git tracks and that misnames the toolkit misleads whoever reads it; a
    # closed one is history.
    open_ = [t for t in tickets if t.state != closed]
    oldnames.report(oldnames.scan_tickets(open_, P.ROOT, P.RULES["oldNameAllowed"],
                                          P.RULES["oldNameAliases"]), out=problems.append)

    # An agent that does not load the no-narration rule narrates every step of its work.
    problems += _agents.reporting_problems(P.ROOT)

    want = build_index(tickets, load_epics())
    have = _read(P.INDEX) if P.INDEX.exists() else None
    stale = want != have
    if stale:
        problems.append("%s/INDEX.md is stale - run: %s index" % (P.todo_dir, P.cli))

    if not problems:
        print("%d tickets, index current, no problem" % len(tickets))
        return 0
    for p in problems:
        print("  %s" % p)
    print("\n%d problem(s)" % len(problems))
    return 1


def _dest_dir(state, epic):
    d = P.TODO / state / epic if epic else P.TODO / state
    d.mkdir(parents=True, exist_ok=True)
    return d


def _by_verifier():
    """Whether this process runs inside a verification session of THIS project. The launch
    sets the project's `verifyEnv` variable; a value that names another project's session
    namespace does not count."""
    return P.verify_ok(P.verify_env_value())


def _owner_words(args):
    """The owner's order that `--owner-order` states in neutral words, or empty for a bare flag
    or none."""
    said = getattr(args, "owner_order", None)
    return said.strip() if isinstance(said, str) else ""


def _may_close(args, status=None):
    """Whether this caller may land a ticket in the closed lane with this status.

    A verification session carries the project's `verifyEnv` variable in its environment,
    set when it was launched, so it needs no flag. `--verified` is the same permission
    written by hand, for a verification the owner ran themselves. The owner closes a ticket
    as `closed by other task` or `cancelled` from a shell of their own, which is a caller
    outside every Claude session: Claude Code sets `CLAUDECODE` in the shells it runs. An
    order the owner gives in a session's chat weighs the same: the session passes
    `--owner-order` with the order, stated in neutral words.
    """
    if bool(getattr(args, "verified", False)) or _by_verifier():
        return True
    if status not in P.OWNER_CLOSES:
        return False
    return bool(_owner_words(args)) or not os.environ.get("CLAUDECODE")


def _may_block_by_user(args):
    """Whether this caller may set `blocked by user`.

    Only the owner's direct order sets it: the owner's own shell, which Claude Code does not
    mark with `CLAUDECODE`, or a session the owner ordered in that message, which passes
    `--owner-order`. Waiting for the owner's answer is never a block.
    """
    return bool(getattr(args, "owner_order", False)) or not os.environ.get("CLAUDECODE")


def _refuse_block_by_user():
    for line in (
        "Error: only the owner's direct order sets `blocked by user`.",
        "  A ticket that waits for an answer, a decision or an input from the owner is not",
        "  blocked: it stays in its lane, and the session asks the owner. A ticket blocked by",
        "  another ticket is `blocked by other task`, with `--blocked-by <id>` on the ticket.",
        "  A session the owner ordered, in that message, to block this ticket passes",
        "  --owner-order.",
    ):
        print(line, file=sys.stderr)
    return 2


def cmd_move(args):
    ensure_tree()
    state = args.state
    if state not in P.STATES:
        print("Error: %r is not a state. One of: %s" % (state, ", ".join(P.STATES)),
              file=sys.stderr)
        return 2
    if args.status and args.status not in P.STATUSES[state]:
        print("Error: status %r does not belong to %s. One of: %s"
              % (args.status, state, ", ".join(P.STATUSES[state])), file=sys.stderr)
        return 2
    if state == lane("closed") and not _may_close(args, args.status or default_status(state)):
        for line in (
            "Error: only a verification session moves a ticket to %s." % lane("closed"),
            "  Finished work lands in %s. A verification session proves it there,"
            % lane("verify"),
            "  and that session is the one that closes it. The owner closes a ticket as",
            "  `%s` or `%s`. When the owner ordered it in this session's chat," % P.OWNER_CLOSES,
            "  close it now with the order in neutral words; never send the owner to the board:",
            "    %s move <id> %s --status cancelled --owner-order \"<the order, in neutral words>\""
            % (P.cli, lane("closed")),
            "    %s verify <id>                 launch one" % P.cli,
            "    %s move <id> %s --verified   from inside one" % (P.cli, lane("closed")),
        ):
            print(line, file=sys.stderr)
        return 2
    if args.status == P.OWNER_BLOCKS and not _may_block_by_user(args):
        return _refuse_block_by_user()
    ordered = _owner_words(args)
    tickets = load_all()
    said = getattr(args, "outcome", None)
    if said is not None:
        if args.epic:
            print("Error: --outcome names the outcome of one ticket, not of an epic",
                  file=sys.stderr)
            return 2
        for ident in args.ids:
            t = find(tickets, ident)
            why = outcome_problem(said, t.id) if t is not None else ""
            if why:
                print("Error: %s: %s." % (t.id, why), file=sys.stderr)
                return 2
        if not args.dry_run:
            for ident in args.ids:
                t = find(tickets, ident)
                if t is not None:
                    set_outcome(t, said)
    moved = []
    targets = ([t for t in tickets if t.epic == args.epic] if args.epic else
               [t for t in (find(tickets, i) for i in args.ids) if t is not None])
    for t in targets:
        why = manager_lane_problem(t, state, args.status)
        if why:
            print("Error: %s." % why, file=sys.stderr)
            return 1

    if args.epic:
        epics = load_epics()
        if args.epic not in epics:
            print("Error: no epic %r" % args.epic, file=sys.stderr)
            return 2
        src = P.TODO / epics[args.epic][0] / args.epic
        dst = P.TODO / state / args.epic
        if src == dst:
            print("%s is already in %s" % (args.epic, P.STATE_LABEL[state]))
            return 0
        if args.dry_run:
            print("would move epic %s -> %s/" % (args.epic, state))
            return 0
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        for f in sorted(dst.glob("*.md")):
            if f.name != EPIC_FILE:
                _restatus(f, state, args.status)
        moved.append("epic %s (%d ticket(s))"
                     % (args.epic, len(list(dst.glob("*.md"))) - 1))
    else:
        for ident in args.ids:
            t = find(tickets, ident)
            if t is None:
                print("Error: no ticket %r" % ident, file=sys.stderr)
                return 2
            result = repeat_result(t, state, args.status, _by_verifier())
            if result and not args.dry_run:
                finish_run(t, result)
                moved.append("%s  %s -> %s  run %d %s, run %d is next"
                             % (t.id, P.STATE_LABEL[t.state], lane("start"), t.run, result,
                                t.run + 1))
                continue
            dst = _dest_dir(state, t.epic) / t.path.name
            if dst == t.path:
                # Already in the lane. A `--status` is still a request to set it, which is
                # how a ticket moves from `planning` to `ready to implement` without leaving
                # In Progress.
                if args.status and not args.dry_run:
                    note = _restatus(t.path, state, args.status)
                    moved.append("%s  stays in %s%s" % (t.id, P.STATE_LABEL[state], note))
                    if ordered:
                        record_owner_order(t.path, state, args.status, ordered)
                else:
                    print("%s is already in %s" % (t.id, P.STATE_LABEL[state]))
                continue
            if args.dry_run:
                print("would move %s  %s -> %s" % (t.id, t.state, state))
                continue
            shutil.move(str(t.path), str(dst))
            note = _restatus(dst, state, args.status)
            moved.append("%s  %s -> %s%s" % (t.id, P.STATE_LABEL[t.state],
                                             P.STATE_LABEL[state], note))
            if ordered:
                record_owner_order(dst, state, args.status or default_status(state), ordered)
            if state == lane("closed") and (args.status or "") in P.OWNER_CLOSES:
                moved.extend(end_ticket_sessions(t.id))

    if args.dry_run:
        return 0
    _prune_empty_dirs()
    for m in moved:
        print(m)
    write_index(quiet=True)
    print("INDEX.md rewritten")
    return 0


def record_owner_order(path, state, status, words):
    """Write the owner's order behind a move on the ticket, under `## Owner's orders`, so
    the record shows who decided. `words` is the order in neutral words, written as a
    statement and never as a quote."""
    import datetime
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    meta, body = parse(_read(path))
    line = '- %s: %s / %s on the owner\'s order: %s' % (
        stamp, P.STATE_LABEL[state], status, " ".join(words.split()))
    m = re.search(r"^##\s+Owner's orders[ \t]*\n(?:[ \t]*\n)?(?:- .*\n?)*", body, re.M)
    if m:
        block = m.group(0)
        if not block.endswith("\n"):
            block += "\n"
        body = body[:m.start()] + block + line + "\n" + body[m.end():]
    else:
        body = body.rstrip("\n") + "\n\n## Owner's orders\n\n" + line + "\n"
    _write(path, render(meta, body))


def end_ticket_sessions(tid):
    """Close every open session of a ticket the owner cancelled or closed, so no agent
    keeps waiting on it. The session this command runs in cannot end its own shell: it
    gets the `--self` command instead. Returns one line per session."""
    out = []
    try:
        sessions = [s for s in live_sessions() if s["id"] == tid]
    except (OSError, ValueError) as exc:
        return ["%s  its sessions were not read: %s" % (tid, exc)]
    for s in sessions:
        if _is_own_session(s):
            out.append("%s  runs this command: %s close %s --mode %s --self closes it"
                       % (s["key"], P.cli, tid, s["mode"]))
            continue
        try:
            rc = close_session(s, force=True)
        except (OSError, ValueError) as exc:
            out.append("%s  not closed: %s" % (s["key"], exc))
            continue
        out.append("%s  %s" % (s["key"], "closed" if rc == 0 else "rc %d" % rc))
    return out


def _restatus(path, state, wanted):
    """Keep the status when the new lane allows it; otherwise take that lane's default.

    A status is only meaningful under its own state, so carrying `implementing` into
    `Verification` would leave the field saying something the lane cannot mean.
    """
    meta, body = parse(_read(path))
    was = meta.get("status", "")
    if wanted:
        new = wanted
    elif was in P.STATUSES[state]:
        return ""
    else:
        new = default_status(state)
    meta["status"] = new
    _write(path, render(meta, body))
    return "  status %s -> %s" % (was or "(unset)", new)


def _prune_empty_dirs():
    for state in P.STATES:
        base = P.TODO / state
        if not base.is_dir():
            continue
        for d in [x for x in base.iterdir() if x.is_dir()]:
            if not any(d.iterdir()):
                d.rmdir()


def repeat_result(t, state, status, by_verifier):
    """What a move of a repeatable ticket means for its run, or empty for an ordinary move.

    A move to Done as `done` is a run that passed. A verification session that sends the
    ticket back to Pipeline from Verification is a run that failed. Every other move is an
    ordinary move, and `cancelled` or `closed by other task` retires the ticket to Done.
    A manager ticket has no pass and no fail."""
    if not t.repeat or t.repeat == MANAGER:
        return ""
    if state == lane("closed") and (status or default_status(state)) == S("done"):
        return "pass"
    if state == lane("start") and t.state == lane("verify") and by_verifier:
        return "fail"
    return ""


def finish_run(t, result, note=""):
    """Record the end of one run of a repeatable ticket, and return it to Pipeline for the
    next run. The run is one line under `## Runs`; `run` rises by one; the session and the
    claim of the finished run are cleared, so the next run starts in a fresh session."""
    import datetime
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    meta, body = parse(_read(t.path))
    n = t.run
    line = "- run %d: %s, %s%s" % (n, result, stamp, (" - " + note) if note else "")
    m = re.search(r"^##\s+Runs[ \t]*\n(?:[ \t]*\n)?(?:- .*\n?)*", body, re.M)
    if m:
        block = m.group(0)
        if not block.endswith("\n"):
            block += "\n"
        body = body[:m.start()] + block + line + "\n" + body[m.end():]
    else:
        body = body.rstrip("\n") + "\n\n## Runs\n\n" + line + "\n"
    meta["run"] = str(n + 1)
    meta["last-run"] = stamp
    meta["last-result"] = result
    meta["status"] = default_status(lane("start"))
    for k in ("claim", "session", "launched", "sessions", OUTCOME_FIELD):
        meta.pop(k, None)
    dst = _dest_dir(lane("start"), t.epic) / t.path.name
    _write(t.path, render(meta, body))
    if dst != t.path:
        shutil.move(str(t.path), str(dst))
    return dst


def cmd_restart(args):
    """Start the next run of a repeatable ticket: a ticket in the middle of a run records
    that run as `restarted`, the ticket returns to Pipeline, and a work session starts on
    the new run unless `--no-launch`."""
    ensure_tree()
    t = find(load_all(), args.id)
    if t is None:
        print("Error: no ticket %r" % args.id, file=sys.stderr)
        return 2
    if not t.repeat:
        print("Error: %s is not repeatable. Make it one with: %s "
              "repeat %s %s" % (t.id, P.cli, t.id, "|".join(REPEAT_ROLES)), file=sys.stderr)
        return 2
    start = lane("start")
    if getattr(args, "dry_run", False):
        print("would restart %s from %s: run %d %s, then work on run %d"
              % (t.id, P.STATE_LABEL[t.state], t.run,
                 "restarted" if t.state != start else "is next",
                 t.run + (1 if t.state != start else 0)))
        return 0
    if t.state != start:
        finish_run(t, "restarted", "from %s / %s" % (P.STATE_LABEL[t.state], t.status))
        print("%s  %s -> %s  run %d restarted, run %d is next"
              % (t.id, P.STATE_LABEL[t.state], start, t.run, t.run + 1))
    else:
        print("%s  in %s, run %d is next" % (t.id, start, t.run))
    _prune_empty_dirs()
    write_index(quiet=True)
    if getattr(args, "no_launch", False):
        return 0
    args.mode = "work"
    return cmd_work(args)


def cmd_repeat(args):
    """Make tickets repeatable with a role, or ordinary again with `none`."""
    role = args.role
    if role != "none" and role not in REPEAT_ROLES:
        print("Error: %r is not a role. One of: %s, none" % (role, ", ".join(REPEAT_ROLES)),
              file=sys.stderr)
        return 2
    tickets = load_all()
    for ident in args.ids:
        t = find(tickets, ident)
        if t is None:
            print("Error: no ticket %r" % ident, file=sys.stderr)
            return 2
        set_repeat(t, role)
        print("%s  repeat %s" % (t.id, role))
    write_index(quiet=True)
    return 0


def set_repeat(t, role):
    """Write the repeat role onto one ticket; `none` makes it an ordinary ticket again."""
    if role == "none" or not role:
        for k in ("repeat", "run", "last-run", "last-result"):
            t.meta.pop(k, None)
    else:
        t.meta["repeat"] = role
        t.meta.setdefault("run", "1")
    _write(t.path, render(t.meta, t.body))


def cmd_repeatable(args):
    """Every repeatable ticket, by role: its lane, its status, its run and its last result."""
    rows = [t for t in load_all() if t.repeat]
    if not rows:
        print("no repeatable ticket")
        return 0
    for role in REPEAT_ROLES:
        mine = sorted((t for t in rows if t.repeat == role), key=priority)
        if not mine:
            continue
        print("%s (%d)" % (REPEAT_LABEL[role], len(mine)))
        for t in mine:
            print("  %s  %-12s %-20s run %-3d last %-9s %-16s %s"
                  % (t.id, P.STATE_LABEL[t.state], t.status, t.run,
                     t.meta.get("last-result", "") or "-", t.meta.get("last-run", "") or "-",
                     t.title))
    return 0


def ticket_hash(t):
    """The sha256 of the ticket file as it is on disk. An edit names the hash it started
    from, so an edit made on top of a change it never saw is refused."""
    import hashlib
    return hashlib.sha256(_read(t.path).encode("utf-8")).hexdigest()


# The front-matter keys an edit may set. Everything else - id, status, sessions, runs - is
# the engine's, and an edit keeps it as it is.
EDIT_FIELDS = ("title", "severity", "difficulty", "category", "source", "permits", "tags",
               "group", "repeat")


def edit_ticket(tid, fields, body, base):
    """Rewrite one ticket from the edit form: the fields it is given and the whole body.

    `fields` holds only the keys the owner may edit (`EDIT_FIELDS`); an absent key stays as
    it is, and an empty value removes the key. `base` is the `ticket_hash` the form was
    opened on. The file keeps its name, even when the title changes, so the path that briefs
    and live sessions hold stays valid. Returns the ticket as it is after the edit."""
    t = find(load_all(), tid)
    if t is None:
        raise KeyError(tid)
    if base != ticket_hash(t):
        raise ValueError("%s changed since you opened it; open it again" % t.id)
    body = (body or "").strip()
    if not body:
        raise ValueError("the ticket needs a body")
    bad = [k for k in fields if k not in EDIT_FIELDS]
    if bad:
        raise ValueError("cannot edit %s" % ", ".join(bad))
    f = {k: (v if isinstance(v, str) else " ".join(v) if isinstance(v, list) else str(v))
         .strip() for k, v in fields.items()}
    if "title" in f and not f["title"]:
        raise ValueError("a ticket needs a title")
    for key, legal in (("severity", SEVERITIES), ("difficulty", DIFFICULTIES),
                       ("category", P.CATEGORIES), ("repeat", REPEAT_ROLES)):
        if f.get(key) and f[key] not in legal:
            raise ValueError("%r is not a %s. One of: %s" % (f[key], key, ", ".join(legal)))
    if "permits" in f:
        permits = [x for x in re.split(r"[,\s]+", f["permits"]) if x]
        bad = [x for x in permits if x not in P.PERMITS]
        if bad:
            raise ValueError("not a permit: %s" % ", ".join(bad))
        f["permits"] = " ".join(permits)
    group = f.pop("group", None)
    if group and group not in group_names():
        raise ValueError("no group %r" % group)
    repeat = f.pop("repeat", None)
    meta = dict(t.meta)
    for k, v in f.items():
        if v:
            meta[k] = v
        else:
            meta.pop(k, None)
    _write(t.path, render(meta, "\n" + body + "\n"))
    if repeat is not None and repeat != t.repeat:
        set_repeat(find(load_all(), t.id), repeat or "none")
    if group is not None:
        group_assign([t.id], group)
    write_index(quiet=True)
    return find(load_all(), t.id)


def cmd_new(args):
    ensure_tree()
    if getattr(args, "launch", False) and args.state != lane("start"):
        print("Error: --launch files the ticket into %s; it does not go with --state %s"
              % (lane("start"), args.state), file=sys.stderr)
        return 2
    if args.status and args.status not in P.STATUSES[args.state]:
        print("Error: status %r does not belong to %s. One of: %s"
              % (args.status, args.state, ", ".join(P.STATUSES[args.state])), file=sys.stderr)
        return 2
    if args.status == P.OWNER_BLOCKS and not _may_block_by_user(args):
        return _refuse_block_by_user()
    n = next_id()
    tid = "T%04d" % n
    meta = {
        "id": tid,
        "title": args.title,
        "severity": args.severity or "",
        "source": args.source or "",
        "difficulty": args.difficulty or "unknown",
        "status": args.status or default_status(args.state),
        "created": args.created or _today(),
    }
    category = getattr(args, "category", None)
    if category:
        if category not in P.CATEGORIES:
            print("Error: %r is not a category. One of: %s"
                  % (category, ", ".join(P.CATEGORIES)), file=sys.stderr)
            return 2
        meta["category"] = category
    if args.claim:
        meta["claim"] = args.claim
    if args.blocked_by:
        meta["blocked-by"] = args.blocked_by
    if args.permits:
        bad = [x for x in re.split(r"[,\s]+", args.permits) if x and x not in P.PERMITS]
        if bad:
            print("Error: not a permit: %s. One of: %s"
                  % (", ".join(bad), ", ".join(sorted(P.PERMITS))), file=sys.stderr)
            return 2
        meta["permits"] = args.permits
    if args.tags:
        meta["tags"] = args.tags
    parent = getattr(args, "parent", None)
    if parent:
        pt = find(load_all(), parent)
        if pt is None:
            print("Error: no ticket %r for --parent" % parent, file=sys.stderr)
            return 2
        meta["parent"] = pt.id
    group = getattr(args, "group", None)
    if P.groups_store == "files":
        if group and group not in load_group_files():
            print("Error: %s" % _no_group(group, load_group_files()), file=sys.stderr)
            return 2
        if group:
            meta["group"] = group
        elif P.groups_required and args.state != lane("closed"):
            print("Error: a ticket needs --group; `%s groups` lists them" % P.cli,
                  file=sys.stderr)
            return 2
    repeat = getattr(args, "repeat", None)
    if repeat:
        if repeat not in REPEAT_ROLES:
            print("Error: %r is not a role. One of: %s" % (repeat, ", ".join(REPEAT_ROLES)),
                  file=sys.stderr)
            return 2
        meta["repeat"] = repeat
        meta["run"] = "1"
        why = manager_lane_problem(Ticket(None, args.state, "", meta, ""), args.state,
                                   meta["status"])
        if why:
            print("Error: %s." % why.replace("%s is" % tid, "a ticket is", 1), file=sys.stderr)
            return 1
    body = "\n## %s\n\n%s\n" % (body_section(args.title),
                                args.description or "TODO: describe it.")
    if args.evidence:
        body += "\n## Evidence\n\n%s\n" % args.evidence
    path = _dest_dir(args.state, args.epic or "") / ("%s-%s.md" % (tid, slug(args.title)))
    _write(path, render(meta, body))
    commit_id(n)
    print("%s  %s" % (tid, path.relative_to(P.ROOT)))
    write_index(quiet=True)
    if getattr(args, "launch", False):
        return launch_new(tid, args)
    return 0


def launch_new(tid, args):
    """Launch the session of a ticket `new` just filed, as a group run does: `plan` when its
    difficulty is `unknown`, `work` otherwise. A manager runs only as `work`."""
    t = find(load_all(), tid)
    mode = "plan" if t.difficulty == "unknown" and t.repeat != MANAGER else "work"
    a = argparse.Namespace(id=tid, mode=mode, note=getattr(args, "note", None),
                           model=None, effort=None, no_fable=False,
                           terminal=getattr(args, "terminal", None),
                           dry_run=getattr(args, "dry_run", False), auto=True)
    return cmd_work(a)


def file_template(text):
    """File a ticket from a template: a ticket file with no id. Its frontmatter gives the
    fields, its body stays as written; the ticket gets the next id, today's date and the
    start lane. Returns the id, or None when the template has no title."""
    ensure_tree()
    meta, body = parse(text.replace("\r\n", "\n"))
    if not meta.get("title"):
        return None
    n = next_id()
    tid = "T%04d" % n
    meta = dict({"id": tid}, **{k: v for k, v in meta.items() if k != "id"})
    meta.setdefault("difficulty", "unknown")
    meta["status"] = default_status(lane("start"))
    meta["created"] = _today()
    if meta.get("repeat"):
        meta.setdefault("run", "1")
    path = _dest_dir(lane("start"), "") / ("%s-%s.md" % (tid, slug(meta["title"])))
    _write(path, render(meta, body))
    commit_id(n)
    return tid


def cmd_epic(args):
    ensure_tree()
    d = _dest_dir(args.state, args.name)
    ep = d / EPIC_FILE
    if ep.exists() and not args.force:
        print("Error: %s already exists" % ep.relative_to(P.ROOT), file=sys.stderr)
        return 2
    meta = {"id": args.name, "title": args.title or args.name, "created": _today()}
    _write(ep, render(meta, "\n## What this group is for\n\n%s\n"
                      % (args.description or "TODO: describe it.")))
    print("%s" % ep.relative_to(P.ROOT))
    write_index(quiet=True)
    return 0


def cmd_migrate(args):
    """Rewrite what an older engine left behind: the field `kind` becomes `category`, in every
    ticket and in `.octopus/project.json`. Running it twice changes nothing."""
    import json
    changed = []
    for t in load_all():
        if "kind" not in t.meta:
            continue
        meta = dict(t.meta)
        old = meta.pop("kind")
        if old and not meta.get("category"):
            meta["category"] = LEGACY_KINDS.get(old, old)
        _write(t.path, render(meta, t.body))
        changed.append("%s: kind %r -> category %r"
                       % (t.id, old, meta.get("category", "")))
    cfg_path = P.CONFIG / _project.PROJECT_FILE
    try:
        cfg = json.loads(_read(cfg_path))
    except (OSError, ValueError):
        cfg = None
    if isinstance(cfg, dict):
        dirty = False
        if "kinds" in cfg:
            kinds = cfg.pop("kinds")
            cats = dict(cfg.get("categories") or {})
            for k, v in (kinds.items() if isinstance(kinds, dict) else []):
                cats.setdefault(LEGACY_KINDS.get(k, k), v)
            cfg["categories"] = cats
            dirty = True
            changed.append("%s: `kinds` -> `categories`" % _project.PROJECT_FILE)
        qf = cfg.get("queryFields")
        if isinstance(qf, list) and "kind" in qf:
            cfg["queryFields"] = [("category" if f == "kind" else f) for f in qf]
            dirty = True
            changed.append("%s: queryFields `kind` -> `category`" % _project.PROJECT_FILE)
        if dirty:
            _write(cfg_path, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    for line in changed:
        print(line)
    print("%d change(s)" % len(changed))
    write_index(quiet=True)
    return 0


def cmd_list(args):
    tickets = load_all()
    terms = [w.lower() for w in (args.query or [])]
    rows = []
    for t in tickets:
        if args.state and t.state != args.state:
            continue
        if args.status and t.status != args.status:
            continue
        if args.permit and args.permit not in t.permits:
            continue
        if args.category and t.category != args.category:
            continue
        if args.difficulty and t.difficulty != args.difficulty:
            continue
        if args.unknown and t.difficulty != "unknown":
            continue
        if args.severity and t.severity.lower() != args.severity.lower():
            continue
        if args.epic and t.epic != args.epic:
            continue
        if terms and not all(w in t.searchable() for w in terms):
            continue
        rows.append(t)
    rows.sort(key=lambda t: (P.STATES.index(t.state),) + priority(t))
    for t in rows:
        print("%-6s %-16s %-12s %-22s %-7s %-7s %s"
              % (t.id, t.category, P.STATE_LABEL[t.state], t.status, t.severity or "-",
                 t.difficulty, t.title[:58]))
    print("\n%d of %d ticket(s)" % (len(rows), len(tickets)))
    return 0


def cmd_query(args):
    """One call for a question asked in chat: filter, order, and print a compact table.

    `list` prints one fixed shape. A question in chat asks for a slice with a shape of its
    own - the high ones that can be started, everything blocked, what a tag covers - so this
    takes every filter at once and prints only the columns the filters leave interesting.
    """
    rows = load_all()
    every = rows
    related_ids = set()
    if args.related_to:
        anchor = find(rows, args.related_to)
        if anchor is None:
            print("Error: no ticket %r" % args.related_to, file=sys.stderr)
            return 2
        related_ids = set(anchor.related) | set(anchor.blocked_by)
        related_ids |= {t.id for t in rows if anchor.id in t.blocked_by}
    def keep(t):
        if args.state and t.state not in args.state:
            return False
        if args.status and t.status not in args.status:
            return False
        if args.severity and t.severity not in [s.title() for s in args.severity]:
            return False
        if args.difficulty and t.difficulty not in args.difficulty:
            return False
        if args.category and t.category not in args.category:
            return False
        if args.epic and t.epic != args.epic:
            return False
        if args.permit and not (set(args.permit) & set(t.permits)):
            return False
        if args.tag and not (set(args.tag) & set(t.tags)):
            return False
        if args.not_tag and (set(args.not_tag) & set(t.tags)):
            return False
        if args.claimed and not t.claim:
            return False
        if args.unclaimed and t.claim:
            return False
        if args.blocked and not t.blocked_by:
            return False
        if getattr(args, "parent", None) and t.parent != _resolve_id(args.parent):
            return False
        if getattr(args, "parents", False) and not any(x.parent == t.id for x in every):
            return False
        if getattr(args, "roots", False) and t.parent:
            return False
        if args.startable and (t.difficulty == "unknown" or t.state != lane("start")):
            return False
        if args.source and args.source.lower() not in t.source.lower():
            return False
        if args.group and t.group != args.group:
            return False
        if args.related_to and t.id not in related_ids:
            return False
        terms = [w.lower() for w in (args.query or [])]
        return all(w in t.searchable() for w in terms)

    rows = [t for t in rows if keep(t)]
    rows.sort(key=lambda t: (P.STATES.index(t.state),) + priority(t))
    if args.limit:
        shown, rest = rows[:args.limit], len(rows) - args.limit
    else:
        shown, rest = rows, 0

    if args.count:
        print(len(rows))
        return 0
    if args.ids:
        print(" ".join(t.id for t in shown))
        return 0
    if args.json:
        import json
        print(json.dumps([query_record(t, every) for t in shown], indent=1))
        return 0

    # Only print a column the result actually varies on.
    many_states = len({t.state for t in shown}) > 1
    many_categories = len({t.category for t in shown}) > 1
    width = args.width
    for t in shown:
        bits = [t.id]
        if many_states:
            bits.append("%-12s" % P.STATE_LABEL[t.state])
        bits.append("%-6s" % t.severity)
        bits.append("%-7s" % t.difficulty)
        if many_categories:
            bits.append("%-16s" % t.category)
        if t.claim:
            bits.append("@%s" % t.claim)
        if t.blocked_by:
            bits.append("<-%s" % ",".join(t.blocked_by))
        if t.parent:
            bits.append("^%s" % t.parent)
        kids = children_of(every, t.id)
        if kids:
            bits.append("+%d/%d" % (sum(1 for k in kids if k.state in settled_states()),
                                    len(kids)))
        bits.append(t.title[:width])
        print(" ".join(bits))
    print("")
    print("%d ticket(s)%s" % (len(rows), (", %d not shown" % rest) if rest else ""))
    return 0


def cmd_show(args):
    t = find(load_all(), args.id)
    if t is None:
        print("Error: no ticket %r" % args.id, file=sys.stderr)
        return 2
    print("# %s   [%s]   %s" % (t.id, P.STATE_LABEL[t.state], t.path.relative_to(P.ROOT)))
    print(_read(t.path))
    kids = children_of(load_all(), t.id)
    if kids:
        print("")
        print("## Children")
        for k in kids:
            print("- %s  %-12s %s" % (k.id, P.STATE_LABEL[k.state], k.title))
    return 0


def query_record(t, every):
    """One ticket as `query --json` prints it: the project's `queryFields`, in their order."""
    get = {
        "id": lambda: t.id, "title": lambda: t.title, "state": lambda: t.state,
        "status": lambda: t.status, "severity": lambda: t.severity,
        "difficulty": lambda: t.difficulty, "category": lambda: t.category,
        "source": lambda: t.source, "epic": lambda: t.epic, "permits": lambda: t.permits,
        "claim": lambda: t.claim, "session": lambda: t.session,
        "launched": lambda: t.launched, "sessions": lambda: t.sessions,
        "blockedBy": lambda: t.blocked_by, "parent": lambda: t.parent,
        "group": lambda: t.group, "related": lambda: t.related,
        "children": lambda: [k.id for k in children_of(every, t.id)],
        "tags": lambda: t.tags, "repeat": lambda: t.repeat, "run": lambda: t.run,
        "path": lambda: t.path.relative_to(P.ROOT).as_posix(),
    }
    return {f: get[f]() for f in P.query_fields}


def children_of(tickets, tid):
    """Every ticket whose `parent` is `tid`, in priority order."""
    return sorted([t for t in tickets if t.parent == tid], key=priority)


def settled_states():
    """A child counts as settled for its parent once a work session has handed it over: the
    verify lane and the closed lane."""
    return (lane("verify"), lane("closed"))


def waits_for(t, tickets):
    """The tickets `t` waits for: its blockers and its children that are not yet in
    Verification or Done. A blocker this project does not hold counts as open."""
    by_id = {x.id: x for x in tickets}
    settled = settled_states()
    return ([b for b in t.blocked_by if b not in by_id or by_id[b].state not in settled]
            + [k.id for k in children_of(tickets, t.id) if k.state not in settled])


def _resolve_id(ident):
    t = find(load_all(), ident)
    return t.id if t else ident


def cmd_children(args):
    tickets = load_all()
    t = find(tickets, args.id)
    if t is None:
        print("Error: no ticket %r" % args.id, file=sys.stderr)
        return 2
    kids = children_of(tickets, t.id)
    if not kids:
        print("%s has no children" % t.id)
        return 0
    for k in kids:
        print("%s  %-12s %-13s %s%s" % (k.id, P.STATE_LABEL[k.state], k.status, k.title,
                                        ("  @" + k.claim) if k.claim else ""))
    open_kids = [k for k in kids if k.state not in settled_states()]
    print("")
    print("%d child(ren), %d still open" % (len(kids), len(open_kids)))
    return 1 if open_kids else 0


def cmd_wait(args):
    """The parent session's hold: look every `interval` seconds until no child is open."""
    import time
    started = time.time()
    last = None
    while True:
        tickets = load_all()
        t = find(tickets, args.id)
        if t is None:
            print("Error: no ticket %r" % args.id, file=sys.stderr)
            return 2
        kids = children_of(tickets, t.id)
        open_kids = [k for k in kids if k.state not in settled_states()]
        line = ", ".join("%s %s" % (k.id, k.state) for k in kids) or "no children"
        if line != last:
            print(time.strftime("%H:%M:%S") + "  " + line, flush=True)
            last = line
        if not open_kids:
            print("every child of %s is in %s or %s" % ((t.id,) + settled_states()))
            return 0
        if args.timeout and time.time() - started > args.timeout:
            print("timed out with %d child(ren) still open: %s"
                  % (len(open_kids), ", ".join(k.id for k in open_kids)), file=sys.stderr)
            return 1
        time.sleep(max(1, args.interval))


def cmd_board(args):
    tickets = load_all()
    per = {s: [t for t in tickets if t.state == s] for s in P.STATES}
    for s in P.STATES:
        per[s].sort(key=priority)
    w, limit = args.width, args.limit
    heads = ["%s (%d)" % (P.STATE_LABEL[s], len(per[s])) for s in P.STATES]
    print("  ".join(h[:w].ljust(w) for h in heads))
    print("  ".join("-" * w for _ in P.STATES))
    depth = min(limit, max((len(per[s]) for s in P.STATES), default=0))
    for row in range(depth):
        cells = []
        for s in P.STATES:
            col = per[s]
            if row < len(col):
                t = col[row]
                cells.append(("%s %s" % ((t.severity or "-")[:1], t.title))[:w].ljust(w))
            else:
                cells.append(" " * w)
        print("  ".join(cells).rstrip())
    if any(len(per[s]) > depth for s in P.STATES):
        print("  ".join((("+%d more" % (len(per[s]) - depth)) if len(per[s]) > depth
                         else "").ljust(w) for s in P.STATES).rstrip())
    print("\n%d ticket(s)" % len(tickets))
    return 0


def cmd_statuses(args):
    counts = {}
    for t in load_all():
        counts[(t.state, t.status)] = counts.get((t.state, t.status), 0) + 1
    for s in P.STATES:
        print("%s" % P.STATE_LABEL[s])
        for st in P.STATUSES[s]:
            print("    %-22s %4d" % (st, counts.get((s, st), 0)))
    stray = [(k, v) for k, v in counts.items() if k[1] not in P.STATUSES[k[0]]]
    for (s, st), v in stray:
        print("  ILLEGAL  %s/%s  %d ticket(s)" % (s, st, v))
    return 1 if stray else 0


# ── working a ticket in its own Claude session ──────────────────────────────

def session_uuid(ticket_id):
    """The same ticket always gets the same session id, so `claude --resume` reattaches.
    The project's session namespace is part of the seed, so two projects never share one."""
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, P.session_namespace + "/" + ticket_id))


# The three things a session can be launched to do. `review` is not a smaller `work`: it
# judges the ticket rather than the code, so it is granted nothing whatever the ticket says.
MODES = ("work", "plan", "review", "verify")
# The pack that holds the git permits and the `modePermits` that grant them to a launch.
GIT_PACK = "git"


def launch_permits(t, mode):
    """What a launch in `mode` holds: the ticket's permits, then the `modePermits` of the mode
    from the rulesets. A review is granted nothing, and the manager only what its ticket says."""
    if mode == "review":
        return []
    out = list(t.permits)
    if t.repeat != MANAGER:
        out += [p for p in P.MODE_PERMITS.get(mode, ()) if p not in out]
    return out


def build_brief(t, mode="work", note="", provider="claude"):
    """The whole instruction set the launched session gets, as one file.

    It is written rather than passed on the command line because it carries the ticket
    verbatim, and a ticket body is longer than a command line should be.

    The text comes from SECTION FILES: `core/engine/brief/<section>.md`, replaced by a
    ruleset's `brief/<section>.md`, replaced by the project's `.octopus/brief/<section>.md`.
    An empty file removes the section. `brief/README.md` lists the sections and the
    placeholders.
    """
    granted = launch_permits(t, mode)
    denied = denied_tools(granted)
    ungranted = [p for p in sorted(P.PERMITS) if p not in granted]
    vals = brief_values(t, mode, note, granted, denied, ungranted)
    vals.update(provider_values(provider, mode, denied))
    files = P.brief_layers()

    def sec(*names):
        for n in names:
            if n in files:
                return _fill(_read(files[n]).replace("\r\n", "\n"), vals)
        return ""

    # The commit policy of each mode, only where the project uses git: the core supplies
    # `vcs.work` and `vcs.verify`, the git pack the others.
    git = P.vcs == "git"
    permits = [sec("permits.granted") if granted else sec("permits.none")]
    if ungranted:
        permits.append(sec("permits.ungranted"))
    if denied:
        permits.append(sec("permits.denied"))
    if "projects" in granted and t.repeat != MANAGER:
        # The manager's own section already carries the projects and its orders on them.
        permits.append(sec("projects"))
    # A session on another provider has no hook: what the launch cannot enforce, it is told.
    other = sec("provider") if provider != "claude" else ""
    if t.repeat == MANAGER:
        # A manager never finishes and never moves its own ticket, so it gets none of the
        # sections that tell a session to finish, to hand over, or to park on a blocker.
        out = [sec("header"), sec("messages"), other,
               sec("where") if vals["project_line"] else "",
               sec("repeat.%s" % MANAGER)] + permits + [sec("before")]
        if git:
            out.append(sec("vcs.%s" % MANAGER))
        if note:
            out.append(sec("note"))
        out.append(sec("ticket"))
        return "".join(out)
    out = [sec("header"), sec("messages"), other, sec("where") if vals["project_line"] else "",
           sec("boundary"), sec("blocked")]
    if t.repeat:
        out.append(sec("repeat"))
        out.append(sec("repeat.%s" % t.repeat))
    if mode == "work" and t.has_plan:
        out.append(sec("job.work-plan"))
    else:
        out.append(sec("job.%s" % mode))
    if _project.rule_enabled(P.ROOT, README_RULE):
        out.append(sec("readme.%s" % mode))
    out += permits
    out.append(sec("before.%s" % mode, "before"))
    out.append(sec("finish.%s" % mode))
    if git:
        out.append(sec("vcs.%s" % mode))
    out.append(sec("finish"))
    out.append(sec("close.%s" % mode))
    if note:
        out.append(sec("note"))
    out.append(sec("ticket"))
    return "".join(out)


def provider_values(provider, mode, denied):
    """The placeholders of the `provider` section: the provider's name, and what its launch cannot
    enforce, as rules the session keeps."""
    feats = _project.PROVIDER_FEATURES.get(provider, {})
    name = _providers.label(provider)
    limits = []
    if denied and not feats.get("deny"):
        limits.append("- **The launch cannot deny a tool.** Never use these all the same: `%s`."
                      % "`, `".join(denied))
    if mode == "plan" and not feats.get("planMode"):
        limits.append("- **%s has no plan mode.** Change no file except the ticket." % name)
    if mode == "review":
        limits.append("- **A review changes no file except the ticket.**")
    limits = "\n".join(limits)
    # `{agent}`, `{agent_label}` and `{agent_limits}` are the names an older project's own
    # `.octopus/brief/agent.md` uses.
    return {"provider": provider, "provider_label": name, "provider_limits": limits,
            "agent": provider, "agent_label": name, "agent_limits": limits}


def _bullets(items):
    return "\n".join("- %s" % x for x in items)


def brief_values(t, mode, note, granted, denied, ungranted):
    """Every placeholder a brief section may name, as text."""
    rows = ["| Ticket | `%s` |" % t.path.relative_to(P.ROOT).as_posix(),
            "| State | %s / %s |" % (P.STATE_LABEL[t.state], t.status),
            "| Severity | %s |" % (t.severity or "none"),
            "| Source | %s |" % (t.source or "-")]
    if t.epic:
        rows.append("| Epic | `%s` |" % t.epic)
    if t.parent:
        rows.append("| Parent | `%s` |" % t.parent)
    if t.blocked_by:
        rows.append("| Blocked by | %s |" % ", ".join(t.blocked_by))
    launch_lane, launch_status = P.LAUNCH_LANE[mode]
    r = P.RULES
    return {
        "id": t.id, "id_lower": t.id.lower(), "title": t.title, "mode": mode,
        "cli": P.cli, "todo_dir": P.todo_dir, "name": P.name, "slug": P.slug,
        "description": P.description,
        "project_line": ("This is %s: %s." % (P.name, P.description.rstrip(".")))
        if P.description else "",
        "header_rows": "\n".join(rows),
        "run": str(t.run),
        "repeat_label": REPEAT_LABEL.get(t.repeat, ""),
        "repeat_meaning": REPEAT_MEANING.get(t.repeat, ""),
        "permit_rows": "\n".join("| `%s` | %s |" % (p, P.PERMITS.get(p, "unknown permit"))
                                 for p in granted),
        "ungranted": ", ".join("`%s`" % p for p in ungranted),
        "denied": "`, `".join(denied),
        "launch_lane": launch_lane, "launch_label": P.STATE_LABEL[launch_lane],
        "launch_status": launch_status,
        "state": t.state, "state_label": P.STATE_LABEL[t.state], "status": t.status,
        "note": note,
        "note_quoted": "> " + note.replace("\n", "\n> ") if note else "",
        "ticket_text": _read(t.path).replace("\r\n", "\n").rstrip("\n"),
        "hard_rules": ("   The hard rules of this project:\n" + "\n".join(
            "   - %s" % x for x in r["hardRules"])) if r["hardRules"] else "",
        "checks": "\n".join("   - `%s`" % c for c in r["checks"]),
        "checks_line": ("- Run %s. Hand the work over only when each one passes."
                        % ", then ".join("`%s`" % c for c in r["checks"]))
        if r["checks"] else "",
        "granted": _granted_paragraph(r),
        "write_paths": ", ".join("`%s`" % x for x in r["writePaths"]),
        "read_only": ", ".join("`%s`" % x for x in r["readOnly"]),
        "look_on_screen": ("4. **Look at it on screen.** %s" % r["lookOnScreen"])
        if r["lookOnScreen"] else "",
        "projects": _project_rows(),
        "octopus": 'python "%s"' % (_project.OCTOPUS / "octopus.py").as_posix(),
    }


def _project_rows():
    """One table row per registered project: its name, its root, and the command that runs
    the ticket engine on it from any directory."""
    exe = (_project.OCTOPUS / "octopus.py").as_posix()
    rows = []
    for r in _project.load_registry():
        here = " (this project)" if Path(r["path"]).resolve() == P.ROOT else ""
        rows.append('| %s%s | `%s` | `python "%s" todo --project "%s" <command>` |'
                    % (r["name"], here, r["path"], exe, r["name"]))
    if not rows:
        return ""
    return "| Project | Root | Command |\n|---|---|---|\n" + "\n".join(rows)


def _granted_paragraph(r):
    """What every session may do without a permit, where it may write, and what it may not
    touch, from the rulesets. Empty when the rulesets say none of it."""
    parts = []
    if r["granted"]:
        parts.append("Every session may also %s." % ", and ".join(r["granted"]))
    if r["writePaths"]:
        parts.append("Write inside %s." % ", ".join("`%s`" % x for x in r["writePaths"]))
    if r["readOnly"]:
        parts.append("Never write %s." % ", ".join("`%s`" % x for x in r["readOnly"]))
    return " ".join(parts)


_PLACEHOLDER = re.compile(r"\{(lane|label|status):([a-z][a-z ]*)\}|\{([a-z_]+)\}")


def _fill(text, vals):
    """Replace the placeholders of one section. A line that holds nothing but a placeholder
    whose value is empty is removed, so an optional block leaves no blank line behind."""
    def one(m):
        if m.group(1):
            kind, role = m.group(1), m.group(2)
            if kind == "status":
                return P.S.get(role, role)
            if role not in P.ROLE:
                return m.group(0)
            return P.lane(role) if kind == "lane" else P.label(role)
        key = m.group(3)
        return vals[key] if key in vals else m.group(0)
    out = []
    for line in text.split("\n"):
        m = _PLACEHOLDER.fullmatch(line.strip())
        if m and m.group(3) in vals and not vals[m.group(3)]:
            continue
        out.append(_PLACEHOLDER.sub(one, line))
    return "\n".join(out)


def cmd_brief(args):
    t = find(load_all(), args.id)
    if t is None:
        print("Error: no ticket %r" % args.id, file=sys.stderr)
        return 2
    P.BRIEFS.mkdir(parents=True, exist_ok=True)
    p = P.BRIEFS / ("%s.md" % t.id)
    _write(p, build_brief(t, getattr(args, "mode", "work"), getattr(args, "note", "") or ""))
    print(p.relative_to(P.ROOT).as_posix())
    return 0


def session_model(t, provider=None, model=None, effort=None):
    """The model id and the effort a session on this ticket runs, as (model, effort): the
    `--model` and `--effort` named, else the defaults of the provider the launch starts. Raises
    ValueError for a model or an effort that provider cannot run."""
    return P.choose(launch_provider(t, provider), model, effort)


def launch_session_id(ticket_id, mode="work", run=1):
    """The session id a launch in this mode gets. Each mode gets its own, so a `work` after
    a `plan` starts fresh and reads the plan off the ticket, and each of the four can be
    resumed on its own. Each run of a repeatable ticket after the first gets ids of its own,
    so a new run starts in a fresh session."""
    base = ticket_id + ("/run%d" % run if run > 1 else "")
    return session_uuid(base + {"plan": "/plan", "review": "/review",
                                "verify": "/verify"}.get(mode, ""))


def own_transcript(sid):
    """The file Claude saves the session `sid` in when it runs in this project."""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    return home / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(P.ROOT)) / (sid + ".jsonl")


def session_transcript(sid):
    """The file Claude saves the session `sid` of this project in. A session that began in
    another project and became this project's (`manager --claim`) keeps its transcript in
    that project's folder: the newest file of that id in any folder is it."""
    own = own_transcript(sid)
    if own.exists() or not sid:
        return own
    found = []
    for p in own.parent.parent.glob("*/%s.jsonl" % sid):
        try:
            found.append((p.stat().st_mtime, p))
        except OSError:
            continue
    return max(found)[1] if found else own


def adopt_transcript(sid):
    """Whether a launch in this project resumes the session `sid`: its transcript is in this
    project's folder, copied there first when it is only in another project's folder. Claude
    resumes a session only from the folder of the directory it starts in."""
    own = own_transcript(sid)
    if own.exists():
        return True
    other = session_transcript(sid)
    if other == own or not other.exists():
        return False
    own.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(other), str(own))
    return True


def ticket_session_id(t, mode="work"):
    """The session id a launch on this ticket gets now: the id this mode already recorded,
    so the launch resumes that mode's own session; otherwise a derived id that no
    other mode of the ticket holds, so a `work` after a `plan` starts a fresh session."""
    mine = [e for e in t.sessions if e["mode"] == mode]
    if mine:
        return mine[-1]["session"]
    taken = {e["session"] for e in t.sessions if e["mode"] != mode}
    run = t.run if t.repeat else 1
    sid = launch_session_id(t.id, mode, run)
    base = t.id + ("/run%d" % run if run > 1 else "")
    n = 1
    while sid in taken:
        n += 1
        sid = session_uuid("%s/%s%d" % (base, mode, n))
    return sid


def dedupe_sessions(entries, keep_mode=None, live_modes=()):
    """Drop the entries that share a session id with another mode. The mode that keeps the
    id is the one being launched, else one with a terminal open, else the last recorded."""
    by_id = {}
    for e in entries:
        by_id.setdefault(e["session"], []).append(e)
    out = []
    for e in entries:
        group = by_id[e["session"]]
        if len(group) == 1:
            out.append(e)
            continue
        rank = lambda x: (x["mode"] == keep_mode, x["mode"] in live_modes, group.index(x))
        if e is max(group, key=rank):
            out.append(e)
    return out


def start_lane(t, mode):
    """Move the ticket into the lane its launch means, with the mode's status, and rewrite
    the index. Returns the one-line record of the move."""
    state, status = P.LAUNCH_LANE[mode]
    dst = _dest_dir(state, t.epic) / t.path.name
    was = t.state
    if dst != t.path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(t.path), str(dst))
        t.path = dst
    note = _restatus(dst, state, status)
    _prune_empty_dirs()
    write_index(quiet=True)
    return "%s  %s -> %s%s" % (t.id, P.STATE_LABEL[was], P.STATE_LABEL[state], note)


def record_launch(t, mode, provider=None):
    """Write the session id and the launch into the ticket, so `claude --resume` can be
    read off the ticket itself: this mode's entry in `sessions:`, and the last launch in
    `session:` and `launched:`."""
    import datetime
    sid = ticket_session_id(t, mode)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    entries = [e for e in t.sessions if e["mode"] != mode]
    entries.append({"mode": mode, "session": sid, "launched": stamp})
    try:
        live_modes = {s["mode"] for s in live_sessions([t]) if s["id"] == t.id}
    except Exception:                                   # noqa: BLE001 - a launch never fails on this
        live_modes = set()
    entries = dedupe_sessions(entries, mode, live_modes)
    t.meta["session"] = sid
    t.meta["launched"] = "%s %s" % (mode, stamp)
    # The provider of the last launch, when it is not the project's own: the watchdog starts a
    # manager again on it. The line's old name goes.
    t.meta.pop(OLD_PROVIDER_FIELD, None)
    if provider and provider != P.provider:
        t.meta[PROVIDER_FIELD] = provider
    else:
        t.meta.pop(PROVIDER_FIELD, None)
    t.meta["sessions"] = "; ".join("%s %s %s" % (e["mode"], e["session"], e["launched"])
                                   for e in entries)
    _write(t.path, render(t.meta, t.body))


def permission_mode(mode, auto=False):
    """The permission mode a launched session runs in. `plan` only for a plan the owner
    asked for by name; `auto` for every other launch, a plan that a group run chose
    included. No launch runs in the default mode, which stops on every permission."""
    return "plan" if (mode == "plan" and not auto) else "auto"


def ticket_provider(t):
    """The provider of the ticket's last launch when it is not the project's own, or "". The
    old name of the line, `agent:`, is read too."""
    return t.meta.get(PROVIDER_FIELD) or t.meta.get(OLD_PROVIDER_FIELD) or ""


def launch_provider(t, provider=None):
    """The provider a launch on this ticket starts: the one named, else the project's `provider`."""
    a = provider or P.provider
    if a not in _project.PROVIDERS:
        raise ValueError("%r is not an AI provider. One of: %s" % (a, ", ".join(_project.PROVIDERS)))
    return a


def remote_control_available(t, provider=None):
    """Whether the provider a launch on this ticket starts has Remote Control."""
    a = provider or ticket_provider(t) or P.provider
    return bool(_project.PROVIDER_FEATURES.get(a, {}).get("remoteControl"))


def remote_control(t, provider=None):
    """Whether a launch on this ticket runs with Remote Control: the ticket's
    `remote-control:`, else the project's `remoteControl`, and never on a provider without it."""
    if not remote_control_available(t, provider):
        return False
    own = t.meta.get(RC_FIELD, "")
    return own == "on" if own in RC_VALUES else P.remote_control


def set_remote_control(t, value):
    """Write `on`, `off` or `default` (the project's setting) on the ticket."""
    if value not in RC_VALUES + ("default",):
        raise ValueError("%r is not on, off or default" % value)
    if value == "default":
        t.meta.pop(RC_FIELD, None)
    else:
        t.meta[RC_FIELD] = value
    _write(t.path, render(t.meta, t.body))


# What a running Claude session is typed to turn Remote Control on. It has no command that
# turns it off, so `off` reaches a running session only at its next launch.
RC_COMMAND = "/remote-control"


def apply_remote_control(t, sessions=None):
    """Turn Remote Control on in the live Claude sessions of this ticket that wait at their
    prompt, when the ticket has it on. Returns the keys of the sessions typed into."""
    if not remote_control(t):
        return []
    done = []
    for s in (sessions if sessions is not None else live_sessions([t])):
        if s.get("id") != t.id or not s.get("claude") or s.get("running"):
            continue
        send_session(s, RC_COMMAND, ["Enter"])
        done.append(s["key"])
    return done


def _launch_argv(t, mode="work", model=None, no_fable=False, auto=False, effort=None,
                 provider=None):
    provider = launch_provider(t, provider)
    tail = {
        "plan": "produce the plan and stop.",
        "review": "re-evaluate the ticket. Change no file except the ticket itself.",
        "verify": "prove the work landed, or send the ticket back with what is still wrong.",
    }.get(mode, "get on with it.")
    if t.repeat == MANAGER:
        tail = "wait for the owner's orders. Never exit."
    verb = {"review": "Re-evaluate", "verify": "Verify"}.get(mode, "Work")
    prompt = ("%s ticket %s. Read %s/.briefs/%s.md first and in full - it carries the "
              "ticket, what you may and may not use, and what to do when you finish. "
              "Then %s" % (verb, t.id, P.todo_dir, t.id, tail))
    if provider != "claude":
        return _provider_argv(t, mode, provider, prompt, model, effort, auto)
    # The prompt is the first argument, before every option. `--disallowedTools` and
    # `--add-dir` take a list, and a list option eats every following word until the next
    # option; a prompt placed after one arrives as a set of deny rules, and the session
    # starts with no instruction and no ticket.
    # Remote Control, named after the ticket, so the owner can reach the session from
    # another machine or from a phone without going back to the terminal it was started in.
    # With it off the session carries the same name, which the session scan reads back.
    # An id whose transcript already exists is resumed: `--session-id` refuses an id Claude
    # has saved a session under, with "Session ID <id> is already in use".
    sid = ticket_session_id(t, mode)
    session_flag = "--resume" if adopt_transcript(sid) else "--session-id"
    # The command and the arguments of the `claude` entry of the provider list come next.
    argv = [_providers.launch_command(P.providers[provider]["command"]), prompt] + _providers.fill(
        _providers.get(provider)["args"], root=str(P.ROOT))
    argv += [session_flag, sid,
            "--remote-control" if remote_control(t, provider) else "--name", session_title(t, mode)]
    argv += ["--permission-mode", permission_mode(mode, auto)]
    # The no-narration rule stands in the system prompt too.
    argv += ["--append-system-prompt", NO_NARRATION_PROMPT]
    # A review judges the ticket, not the code, so it is granted nothing whatever the
    # ticket says. Its one write is the ticket, and `Edit` stays available for that.
    granted = launch_permits(t, mode)
    denied = denied_tools(granted)
    if denied:
        argv += ["--disallowedTools", " ".join(denied)]
    dirs = []
    for p in P.PERMITS:
        if p in granted:
            dirs += [d for d in P.add_dirs(p) if d not in dirs]
    if dirs:
        argv += ["--add-dir"] + dirs
    # An explicit --model and --effort win; otherwise Claude's defaults in this project.
    model, effort = P.choose(provider, model, effort)
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    return argv


def _provider_argv(t, mode, provider, prompt, model=None, effort=None, auto=False):
    """The command line of a session on any provider but Claude: Codex, or a provider of the
    provider list, built the way its `dialect` says. Codex takes no session id, no tool deny
    list and no Remote Control, and has no plan mode. The brief carries what the
    launch cannot enforce. The prompt is the last argument."""
    spec = P.providers[provider]
    prov = _providers.get(provider)
    granted = launch_permits(t, mode)
    dirs = []
    for p in P.PERMITS:
        if p in granted:
            dirs += [d for d in P.add_dirs(p) if d not in dirs]
    # Only a model and an effort of this provider; none of Claude's defaults.
    model, effort = P.choose(provider, model, effort)
    if prov["dialect"] == "template":
        plan = permission_mode(mode, auto) == "plan" and prov["features"]["planMode"]
        p = dict(prov, command=_providers.launch_command(spec["command"]))
        return _providers.template_argv(
            p, prompt, prov["plan"] if plan else prov["auto"], model=model, effort=effort,
            dirs=dirs, session=ticket_session_id(t, mode), root=str(P.ROOT))
    argv = [_providers.launch_command(spec["command"])] + _providers.fill(prov["args"], root=str(P.ROOT))
    if prov["dialect"] == "codex":
        # The desktop-bundled CLI cannot bootstrap the standalone shared daemon.
        # Keep the worker attached to this terminal for process tracking as well.
        argv += ["--no-daemon", "--sandbox", "workspace-write"] + _providers.approval_argv(prov)
        for d in dirs:
            argv += ["--add-dir", d]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["-c", "model_reasoning_effort=%s" % effort]
    return argv + [prompt]


# A character that ends or redirects the `cmd` line, or the quoted `--remote-control` or
# `--name` name the session scan reads back. A backslash before the closing quote escapes it.
_TITLE_UNSAFE = re.compile(r'["&|^%<>\\\x00-\x1f]')


def short_title(title, limit=40):
    """The ticket title cut to `limit` characters on a word boundary, with no character
    that breaks the shell line or the window title and no trailing punctuation."""
    s = " ".join(_TITLE_UNSAFE.sub(" ", title or "").split())
    if len(s) > limit:
        cut = s[:limit + 1].rsplit(" ", 1)[0]
        s = cut if cut and len(cut) < len(s[:limit + 1]) else s[:limit]
    return s.rstrip(" .,;:!?-–—(['/").strip()


def session_title(t, mode):
    """`<title> - <ticket> - <short ticket title> - <mode>`: the window title and the Remote
    Control name. The ticket id and the mode stay fields of their own."""
    short = short_title(t.title)
    if not short:
        return "%s - %s - %s" % (P.title, t.id, mode)
    return "%s - %s - %s - %s" % (P.title, t.id, short, mode)


def current_title(name):
    """`name`, a session name read off a shell line, rebuilt from its ticket as it stands
    now. A shell started before the ticket title entered the name carries the old form
    `<title> - <ticket> - <mode>`. Any other name, or one whose ticket is gone, comes back
    unchanged. The hook runs this before every tool call, so it reads one file only."""
    prefix = P.title + " - "
    if not name.startswith(prefix):
        return name
    parts = name[len(prefix):].split(" - ")
    if len(parts) < 2 or not _ID_RE.match(parts[0]) or parts[-1] not in MODES:
        return name
    for p in P.TODO.rglob("%s-*.md" % parts[0]):
        meta, body = parse(_read(p))
        if meta.get("id") != parts[0]:
            continue
        return session_title(Ticket(p, "", "", meta, body), parts[-1])
    return name


# The session name on a launch's shell line: `--remote-control "<name>"`, or `--name "<name>"`
# for a launch with Remote Control off.
_NAME_ARG = r'--(?:remote-control|name)\s+"([^"]+)"'

# The session id on a launch line: a Claude's `--session-id` or `--resume`, the
# `OCTOPUS_SESSION` of a session on another AI-provider.
_LINE_SESSION = (r"--(?:session-id|resume)[\s\"']+([0-9a-fA-F-]{36})"
                 r"|OCTOPUS_SESSION=([0-9a-fA-F-]{36})")


# The variables the shell of every session sets before Claude starts, on every system;
# `_shell_line` explains each.
LAUNCH_ENV = {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1", "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE": "1",
              "CLAUDE_CODE_DISABLE_FAST_MODE": "1"}


def _shell_line(argv, title, marks=None):
    """The `cmd` line that runs `argv` as a session of this project. `marks` are variables the
    shell sets first; they stand on its command line, where the session scan reads them."""
    line = " ".join(('"%s"' % a.replace('"', '\\"')) if (" " in a or '"' in a) else a
                    for a in argv)
    # A session runs in the project directory and nowhere else. The shell moves there
    # itself, so no route can start one anywhere else: `cwd` on the launch call reaches
    # nothing when Windows Terminal starts the tab, and a session started in the home
    # directory stops on the trust question and never reaches Remote Control.
    # The window is named before Claude starts, and the variable stops Claude replacing the
    # name with its own topic. The SessionStart hook writes the same name once Claude runs.
    # After Claude exits, however it exits, the shell turns off the terminal modes Claude
    # turned on. A crash or a `taskkill /F` leaves mouse tracking and win32-input-mode on,
    # and the prompt that gets the tab back reads every click and key as an escape
    # sequence. `&` runs the reset whatever Claude's exit code was.
    # The shell is `cmd /c`, so it exits after the reset with the reset's code, 0, and its
    # window or tab closes: Windows Terminal's default `closeOnExit` closes a tab whose
    # process exits with 0, and keeps one that exits with anything else.
    # A session launched from inside a Claude session (the manager, the board under one) inherits
    # CLAUDE_CODE_CHILD_SESSION and would keep no transcript. Every session keeps its transcript.
    # `STATUS_ENV` sets Claude Code's reminder to ask for one status line once every 5 minutes.
    env = dict(LAUNCH_ENV, **_agents.STATUS_ENV)
    if not WINDOWS:
        import posixterm
        return posixterm.shell_line(argv, title, P.ROOT, env, marks,
                                    [sys.executable, str(HOOKS / "session_state.py"),
                                     "--reset-terminal"])
    reset = '"%s" "%s" --reset-terminal' % (sys.executable, HOOKS / "session_state.py")
    if os.environ.get("TERM") == "dumb":
        env["TERM"] = "xterm-256color"
    if marks and marks.get("OCTOPUS_PROVIDER", "claude") != "claude":
        # Keep a failed startup visible in console windows as well as Windows Terminal.
        line += ' || (%s & echo. & echo Provider exited with an error. & pause)' % reset
    if marks:
        line = "".join(('set "%s=%s"&& ' if k.upper() == "PATH" else "set %s=%s&& ") % (k, v)
                       for k, v in marks.items()) + line
    return ('cd /d "%s"&& %s&& title %s&& %s & %s'
            % (P.ROOT, "&& ".join("set %s=%s" % kv for kv in env.items()), title, line, reset))


def _open_terminal(line, title, terminal, tab=None, env=None):
    """Run `cmd /c <line>` in a terminal of its own: a Windows Terminal window of its own
    (`wt`), else a console window. A wt window runs `tab` when one is given.
    Returns 0, or 2 when nothing could start.

    On macOS and Linux the line runs in a tmux session named after its session id, with a
    Terminal window on it unless `terminal` is `own` (`posixterm.launch`)."""
    import subprocess
    tab = tab or line
    if not WINDOWS:
        import posixterm
        m = re.search(_LINE_SESSION, tab)
        name = posixterm.session_name((m.group(1) or m.group(2)).lower() if m
                                      else "%s-%d" % (P.slug, os.getpid()))
        why = posixterm.launch(tab, title, P.ROOT, name, P.SESSIONS_DIR,
                               window=terminal != "own")
        if why:
            print("Error: could not launch (%s). The command is:\n  %s" % (why, line),
                  file=sys.stderr)
            return 2
        return 0
    try:
        if terminal == "wt":
            # Every session gets a window of its own, so the window that shows a session
            # is exact: `focus` raises the window of the session's console, and never
            # guesses which tab of a shared window holds it.
            #
            # Windows Terminal is one process for the whole machine. `wt` hands the request
            # to the running one, and THAT process starts the shell - so `cwd` and `env` on
            # this call reach nothing. The directory has to be `-d`, and a variable has to
            # be set by the shell itself.
            #
            # wt reads every bare `;` on its command line as its own separator and starts
            # the rest as a new tab. A ticket title can hold one, so each is escaped.
            subprocess.Popen(["wt", "-w", "new", "nt",
                              "-d", str(P.ROOT),
                              "--title", title.replace(";", "\\;"),
                              "cmd", "/c", tab.replace(";", "\\;")], cwd=str(P.ROOT), env=env)
        else:
            CREATE_NEW_CONSOLE = 0x00000010
            subprocess.Popen("cmd /c " + line, cwd=str(P.ROOT), shell=False,
                             creationflags=CREATE_NEW_CONSOLE, env=env)
    except FileNotFoundError as exc:
        if terminal == "wt":
            # No Windows Terminal on this machine. The session still starts, in a console
            # of its own, and the board reads its state and raises its window the same
            # way.
            CREATE_NEW_CONSOLE = 0x00000010
            subprocess.Popen("cmd /c " + line, cwd=str(P.ROOT), shell=False,
                             creationflags=CREATE_NEW_CONSOLE, env=env)
        else:
            print("Error: could not launch (%s). The command is:\n  %s" % (exc, line),
                  file=sys.stderr)
            return 2
    return 0


# The no-narration rule as a command-line argument: `cmd` reads every double quote, so it
# carries none, and no markdown.
NO_NARRATION_PROMPT = _agents.NO_NARRATION.replace("**", "").replace('"', "'")


# What a chat session may not use. It answers questions and changes nothing: plan mode
# blocks the edit tools, and this list takes away every tool that could write all the same -
# a shell writes files, and ExitPlanMode would leave plan mode.
CHAT_DENIED = ("Write", "Edit", "NotebookEdit", "Bash", "PowerShell", "ExitPlanMode")


def chat_argv(model=None, effort=None, sid=None, provider=None):
    """The command line of a read-only session that answers questions about this codebase."""
    import uuid
    note = ("You answer the owner's questions about the %s codebase. Read and search as "
            "much as the answer needs. You cannot change any file, and you do not offer "
            "to. %s" % (P.name, NO_NARRATION_PROMPT))
    provider = provider or P.provider
    model, effort = P.choose(provider, model, effort)
    prov = _providers.get(provider)
    argv = [_providers.launch_command(P.providers[provider]["command"])] + _providers.fill(
        prov["args"], root=str(P.ROOT))
    if prov["dialect"] == "codex":
        return argv + ["--no-daemon", "--sandbox", "read-only"] + _providers.approval_argv(prov) + (
            ["--model", model] if model else []) + (
            ["-c", "model_reasoning_effort=%s" % effort] if effort else []) + [note]
    if prov["dialect"] != "claude":
        raise ValueError("Codebase chat supports Claude Code and Codex")
    return argv + ["--session-id", sid or str(uuid.uuid4()),
            "--permission-mode", "plan",
            "--disallowedTools", " ".join(CHAT_DENIED),
            "--append-system-prompt", note] + (["--model", model] if model else []) + (
            ["--effort", effort] if effort else [])


def cmd_chat(args):
    """Open a terminal with a read-only session on the selected provider."""
    import uuid
    sid = str(uuid.uuid4())
    provider = getattr(args, "provider", None) or P.provider
    try:
        argv = chat_argv(args.model, args.effort, sid, provider)
    except ValueError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2
    if args.dry_run or args.terminal == "none":
        print(" ".join(('"%s"' % a) if " " in a else a for a in argv))
        return 0
    title = "%s - chat" % P.title
    if _open_terminal(_shell_line(argv, title, _providers.launch_env(provider)), title,
                      args.terminal or P.terminal) != 0:
        return 2
    print("CHAT  launched in a new terminal")
    print("  session %s" % sid)
    if provider == "claude":
        print("  resume  claude --resume %s" % sid)
    return 0


def cmd_work(args):
    mode = getattr(args, "mode", "work")
    terminal = getattr(args, "terminal", None) or P.terminal
    note = (getattr(args, "note", "") or "").strip()
    t = find(load_all(), args.id)
    if t is None:
        print("Error: no ticket %r" % args.id, file=sys.stderr)
        return 2
    if t.repeat == MANAGER and mode != "work":
        print("Error: %s is a manager ticket: it runs only as `work`. It has nothing to plan, "
              "review or verify." % t.id, file=sys.stderr)
        return 2
    try:
        provider = launch_provider(t, getattr(args, "provider", None))
        # A model or an effort the provider cannot run never reaches its command line.
        session_model(t, provider, getattr(args, "model", None), getattr(args, "effort", None))
    except ValueError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2
    P.BRIEFS.mkdir(parents=True, exist_ok=True)
    _write(P.BRIEFS / ("%s.md" % t.id), build_brief(t, mode, note, provider))

    argv = _launch_argv(t, mode, getattr(args, "model", None), getattr(args, "no_fable", False),
                        getattr(args, "auto", False), getattr(args, "effort", None), provider)
    if provider == "claude":
        pm = argv[argv.index("--permission-mode") + 1]
        if pm not in ("auto", "plan") or (pm == "plan" and mode != "plan"):
            raise SystemExit("Error: a %s launch must run in auto mode, and it carries %r"
                             % (mode, pm))
    # The shell sets the variables the provider list gives the provider: an endpoint, a model
    # host. Another provider's command line carries no session id: the shell names it, and the
    # provider.
    marks = _providers.launch_env(provider)
    if provider != "claude":
        marks.update(OCTOPUS_PROVIDER=provider, OCTOPUS_SESSION=ticket_session_id(t, mode))
    marks = marks or None
    key = _providers.key_missing(provider)
    if key:
        print("  warning %s is unset in this environment; %s reads its key from it"
              % (key, _providers.label(provider)), file=sys.stderr)
    if getattr(args, "dry_run", False) or terminal == "none":
        print(" ".join(('"%s"' % a) if " " in a else a for a in argv))
        if getattr(args, "dry_run", False):
            print("  in      %s" % P.ROOT)
            print("  title   %s" % session_title(t, mode))
            for k, v in (marks or {}).items():
                print("  set     %s=%s" % (k, v))
        return 0
    request = _providers.setup_request()
    if request:
        print("Error: no launch until the default AI provider and model work. %s" % request,
              file=sys.stderr)
        return 2

    # One Claude per session id. A terminal of this ticket and mode that is still busy stops
    # the launch; one that waits at its prompt, or whose Claude has exited, is closed first,
    # and the launch resumes its session in a new one.
    open_here = [s for s in live_sessions() if s["id"] == t.id and s["mode"] == mode]
    busy = [s for s in open_here if s["running"]]
    if busy:
        print("Error: %s is still running in an open terminal; it keeps its session. "
              "Close it first: %s close %s --mode %s --force"
              % (busy[-1]["key"], P.cli, t.id, mode), file=sys.stderr)
        return 2
    for s in open_here:
        close_session(s)
        print("  closed  %s, waiting at its prompt" % s["key"])
    if open_here:
        argv = _launch_argv(t, mode, getattr(args, "model", None),
                            getattr(args, "no_fable", False),
                            getattr(args, "auto", False), getattr(args, "effort", None), provider)

    import subprocess
    env = None
    if mode == "verify":
        # This is what `move ... Done` checks. It reaches the session's own shell, so a
        # verification session needs no flag and no other session can pass the guard by
        # accident.
        env = dict(os.environ, **{P.verify_env: P.verify_token(t.id)})
    title = session_title(t, mode)
    line = _shell_line(argv, title, marks)
    # The shell sets the verify flag itself, because Windows Terminal starts the shell and
    # `env` on this call does not reach it.
    tab = line
    if mode == "verify" and not WINDOWS:
        # The tmux server starts the shell, as Windows Terminal does: `env` does not reach it.
        import posixterm
        tab = posixterm.export({P.verify_env: P.verify_token(t.id)}) + line
    elif mode == "verify":
        tab = "set %s=%s&& %s" % (P.verify_env, P.verify_token(t.id), line)
    if terminal == "own" and WINDOWS:
        # No window. `session_pty.py` holds a pseudoconsole with the same `cmd /c`
        # line inside, so the hooks and the session detection see the same shell, and
        # the page reads its screen and types into it through the host. The host is
        # detached: it survives the board server, and it has no console of its own,
        # which `AttachConsole` needs.
        DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP = 0x00000008, 0x00000200
        host = [sys.executable, str(WIN / "session_pty.py"),
                "--ticket", t.id, "--mode", mode, "--session", ticket_session_id(t, mode),
                "--title", title, "--sessions-dir", str(P.SESSIONS_DIR),
                "--cwd", str(P.ROOT), "--cmd-line", "cmd /c " + tab]
        P.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        host_log = P.SESSIONS_DIR / ("%s.%s.host.log" % (ticket_session_id(t, mode), mode))
        try:
            with open(host_log, "ab") as err:
                subprocess.Popen(host, cwd=str(P.ROOT), env=env,
                                 creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                                 stdin=subprocess.DEVNULL, stdout=err, stderr=err)
        except FileNotFoundError as exc:
            print("Error: could not launch (%s). The command is:\n  %s" % (exc, line),
                  file=sys.stderr)
            return 2
    elif _open_terminal(line, title, terminal, tab=tab, env=env) != 0:
        return 2
    record_launch(t, mode, provider)
    moved = start_lane(t, mode)
    print("%s  %s  launched in a new terminal%s"
          % (t.id, mode.upper(), "" if provider == "claude" else " on %s" % provider))
    print("  lane    %s" % moved)
    print("  brief   %s/.briefs/%s.md" % (P.todo_dir, t.id))
    if provider == "claude":
        print("  resume  claude --resume %s   (written to the ticket under `sessions:`)"
              % t.session)
    else:
        print("  resume  %s takes no session id; a launch again starts a fresh session that "
              "reads the brief" % provider)
    return 0


# ── the terminals those sessions run in ─────────────────────────────────────
#
# Two sources. A launched session is a `cmd.exe` carrying `--session-id <uuid>`, or
# `--resume <uuid>` for a session saved before, on its
# command line, with `claude.exe` as a child; `launch_session_id` derives that uuid from the
# ticket id, so the ticket tree and the process table identify it and nothing is recorded at
# launch. Every other session - `claude` typed into a terminal, a `--resume` - is found by the
# live record `hooks/session_state.py` writes from the hooks into `TODO/.sessions/`. Such a
# session has no ticket: its `id` is None and its `mode` is `cli`.
#
# The session ids come from this project's namespace, so the process table of a machine
# that runs sessions of several projects gives each project only its own.

CLAUDE_IMAGES = ("claude.exe", "node.exe", "claude", "node")
# The process of a session on Codex or another listed AI-provider under its shell: the native
# binary, or the Node that runs the provider's script. Each provider names its `images`.
PROVIDER_IMAGES = ("codex.exe", "node.exe", "codex", "node")
# The shell a launch runs in: `cmd /c` on Windows, `/bin/sh -c` elsewhere.
LAUNCH_SHELLS = ("cmd.exe", "sh", "bash", "dash", "zsh")


def provider_images():
    names = PROVIDER_IMAGES + tuple(_providers.images())
    # macOS and Linux name a process without the `.exe` its Windows image carries.
    names += tuple(n[:-4] for n in names if n.lower().endswith(".exe"))
    return tuple(dict.fromkeys(names))
TERMINAL_IMAGES = ("openconsole.exe", "windowsterminal.exe")


def session_goals():
    """`session id -> (agent-id, goal)` out of the project's sessions board (`sessionsBoard`,
    a table the SessionStart hook appends to), the two columns an agent fills in by hand."""
    out = {}
    try:
        text = P.SESSIONS_BOARD.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"^\|\s*`?([0-9a-fA-F-]{36})`?\s*\|", line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        agent = cells[3] if len(cells) > 3 else ""
        goal = cells[4] if len(cells) > 4 else ""
        out[m.group(1).lower()] = (agent, goal)
    return out


def _mode_from_line(line):
    """Which mode a shell was launched in, read off its command line: the plan prompt, which
    every plan carries whatever its permission mode, the re-evaluate prompt, or the verify
    prompt."""
    if "--permission-mode plan" in line or "produce the plan and stop." in line:
        return "plan"
    if "Re-evaluate ticket" in line:
        return "review"
    if "Verify ticket" in line:
        return "verify"
    return "work"


def live_sessions(tickets=None, recheck=False):
    """Every launched session that still has a terminal, newest last.

    Each entry carries the ticket, the mode, the session id, the shell pid, the Claude pid
    or `None`, the shell's start time, and `tabbed` when the shell runs in Windows
    Terminal.

    A session is at its prompt when its Claude has exited, and also when its Claude runs
    and waits for the next instruction. An interactive `claude` keeps `claude.exe` alive
    between turns, so the second case comes from the `Stop` hook marker that
    `session_state.py` writes, and `idle_since` carries the time it was written.

    A session the board did not launch comes from the live record the hooks write. It
    carries `id` None, `mode` `cli`, the window `title` and the `goal` from `SESSIONS.md`.

    Each entry also carries `wait` and `why` from `wait_state`: `None` while its turn runs,
    else `input`, `event`, `subagent`, `blocker`, `idle` or `finished`; `running` is
    `wait is None`. `read_turn` gives `ended`, `stale`, `updated`, `doing` (what it does
    now, its last tool call), `judge_from` and `recap`. `asks`, `unsure`,
    `dialog`, `says_finished`, `judge`, `judge_why`, `judging`, `reported`, `reported_why`,
    `tasks`, `jobs` and `waits_for` are the signals that `wait_state` reads: the `Stop`
    marker says the last line asks a question, a `?` stands only higher up, or the message
    ends with `WORK IS FINISHED` or says its ticket is in the verify or closed lane
    (`says_finished` carries what it said); the judge's verdict on the last message, and why,
    and whether a judge was started on this turn; a dialog stops the turn; what `report`
    wrote, and why; the subagents and background shells that still ran when its turn ended;
    the recurring jobs the session scheduled; the blockers and children of its ticket that
    are not yet in Verification or Done.

    Each entry also carries `signals` from `wait_signals`: every signal above with its
    value, and the rule that decided, so the owner can tell a wrong decision from a signal
    that never came.

    Two things happen on the way. A launched Claude that no hook file names, long after its
    start, has lost its files: when its transcript holds a message, its live record and its
    marker are written back from the transcript. And `read_turn` reads every session's
    turn: the judge on what no rule reads, once per turn, and the recap when the judge is
    unsure.

    `recheck` is the owner's re-evaluation: a judge that was started on the current turn
    and died without a verdict, and a recap that never came, are tried again
    (`session_state.rejudge`), and the entry carries `rejudged`."""
    import session_state
    import winproc

    idle = session_state.idle_sessions()
    ids, modes = {}, {}
    tickets = load_all() if tickets is None else list(tickets)
    by_id = {t.id: t for t in tickets}
    for t in tickets:
        for mode in MODES:
            sid = launch_session_id(t.id, mode, t.run if t.repeat else 1)
            ids[sid], modes[sid] = t.id, mode
        for e in t.sessions:
            ids[e["session"]], modes[e["session"]] = t.id, e["mode"]

    procs = winproc.processes()
    shells = [p for p, (_pp, name) in procs.items() if name in LAUNCH_SHELLS]
    lines = winproc.command_lines(shells)
    # A POSIX shell counts only when it runs a launch line: the agent's own Bash tool runs
    # shells too, and their commands may name a session id.
    import posixterm
    lines = {pid: line for pid, line in lines.items()
             if procs.get(pid, (0, ""))[1] not in LAUNCH_SHELLS[1:]
             or posixterm.LAUNCH_LINE.match(line or "")}

    kids = {}
    for pid, (ppid, name) in procs.items():
        kids.setdefault(ppid, []).append((pid, name))

    records = session_state.live_records(procs)
    found = []
    for pid, line in lines.items():
        m = re.search(r"(?:--(?:session-id|resume)[\s\"']+|OCTOPUS_SESSION=)([0-9a-fA-F-]{36})",
                      line or "")
        sid = m.group(1).lower() if m else ""
        tid = ids.get(sid)
        if not tid:
            continue
        # `OCTOPUS_AGENT` is the variable's old name, on a session launched before the rename.
        a = re.search(r"OCTOPUS_(?:PROVIDER|AGENT)=([\w-]+)", line or "")
        provider = a.group(1) if a else "claude"
        if provider != "claude":
            # No hook runs under another provider: the session lives while its process does,
            # and says what it waits for with `report`.
            images = provider_images()
            proc = next((c for c, n in kids.get(pid, []) if n.lower() in images), None)
            found.append({
                "id": tid, "mode": _mode_from_line(line), "session": sid, "shell": pid,
                "claude": proc, "provider": provider, "idle_since": None, "running": bool(proc),
                "hooked": True, "recovered": False, "created": winproc.created_at(pid) or 0,
                "tabbed": procs.get(procs[pid][0], (0, ""))[1] in TERMINAL_IMAGES,
                "terminal": None, "title": "%s - %s - %s" % (P.title, tid, provider),
                "goal": "", "mark_shell": pid, "starting": False})
            continue
        claude = None
        for child, name in kids.get(pid, []):
            if name in CLAUDE_IMAGES:
                claude = child
                break
            for grand, gname in kids.get(child, []):
                if gname in CLAUDE_IMAGES:
                    claude = grand
                    break
        parent = procs.get(procs[pid][0], (0, ""))[1]
        started = winproc.created_at(pid) or 0
        # A hook under this Claude wrote the live record. With none, Claude has not begun
        # its first turn: a question before it, the folder trust above all, fires no hook
        # and waits for ever. It is not working, and it is not finished.
        rec = records.get(sid) or {}
        hooked = not claude or claude == rec.get("claude") or pid == rec.get("shell")
        recovered = hooked and rec.get("last_event") == RECOVERED
        if (not hooked and _age(winproc.created_at(claude)) >= START_GRACE) or recovered:
            # No hook file names this Claude, long after its start. A transcript with a
            # message says it began: its hook files are gone, so its last turn is read back
            # from the transcript, and read again on every scan until a hook fires. A turn
            # that stopped at a tool call with no result stopped at a dialog.
            text, ended, when, error = session_state.transcript_tail(
                session_transcript(sid), error=True)
            if text is not None:
                hooked = True
                title = re.search(_NAME_ARG, line or "")
                session_state.record_live(sid, RECOVERED,
                                          current_title(title.group(1)) if title else "",
                                          claude, pid, procs)
                if ended:
                    session_state.recover(sid, pid, text, when, error)
                else:
                    session_state.mark_idle(sid, pid, dialog=True, when=when)
                idle[session_state.key(sid, pid)] = when
        if hooked:
            waiting, since = session_state.at_prompt(claude, idle, sid, pid)
        else:
            waiting, since = _age(winproc.created_at(claude)) >= START_GRACE, None
        name = re.search(_NAME_ARG, line or "")
        found.append({
            "id": tid,
            "mode": _mode_from_line(line),
            "session": sid,
            "shell": pid,
            "claude": claude,
            "idle_since": since,
            "running": not waiting,
            "hooked": hooked,
            "recovered": recovered,
            "created": started,
            "tabbed": parent in TERMINAL_IMAGES,
            "terminal": None,
            "title": current_title(name.group(1)) if name else "%s - %s" % (P.title, tid),
            "goal": "",
            "mark_shell": pid,
            "starting": not hooked and claude is not None and not waiting,
        })

    # A shell inside a pseudoconsole `session_pty.py` holds has no window: the page reads
    # its screen through the host, and nothing can be raised. `pty` keeps the host's record,
    # so `pty_call` needs no second scan; it carries the host's token, and the board server
    # strips it before a session goes to the page.
    ptys = pty_records(procs)
    for s in found:
        s["own"] = s["shell"] in ptys
        s["pty"] = ptys.get(s["shell"])
        if s["own"]:
            s["tabbed"] = False
    if not WINDOWS and found:
        # A shell in a tmux session with no window attached is an own terminal here: the
        # page shows its screen. One that has a Terminal window raises that window.
        import posixterm
        panes = posixterm.panes()
        for s in found:
            pane = panes.get(s["shell"])
            s["own"] = bool(pane) and not pane["attached"]

    known = {s["session"] for s in found}
    goals = session_goals()
    hand = []
    for sid, rec in records.items():
        if sid in known:
            continue
        claude = rec["claude"] if rec.get("claude_alive") else None
        shell = rec["shell"] if rec.get("shell_alive") else 0
        if not claude and not shell:
            continue
        waiting, since = session_state.at_prompt(claude, idle, sid, rec.get("shell") or 0)
        agent, goal = goals.get(sid, ("", ""))
        # A session the owner opened that a ticket records as its own (`manager --claim`)
        # is that ticket's session: the watchdog and the board see it like a launched one.
        tid = ids.get(sid)
        (found if tid else hand).append({
            "id": tid,
            "mode": modes.get(sid, "work") if tid else "cli",
            "session": sid,
            "shell": shell,
            "claude": claude,
            "idle_since": since,
            "running": not waiting,
            "hooked": True,
            "created": rec.get("shell_started") or rec.get("claude_started") or 0,
            "tabbed": False,
            "own": False,
            "pty": None,
            "terminal": rec.get("terminal") or None,
            "title": rec.get("title") or "%s - %s" % (P.title, sid[:8]),
            "goal": " - ".join(x for x in (agent, goal) if x),
            "mark_shell": rec.get("shell") or 0,
            "starting": False,
        })

    found.sort(key=lambda s: s["created"])
    for s in found:
        s["key"] = "%s/%s" % (s["id"], s["mode"])
    hand.sort(key=lambda s: s["created"])
    for s in hand:
        s["key"] = "%s/%s" % (s["session"][:8], s["mode"])
    for s in found + hand:
        t = by_id.get(s["id"])
        # The tickets this one waits for: its blockers and its children that are not yet
        # in Verification or Done.
        s["waits_for"] = [] if t is None else waits_for(t, tickets)
        read_turn(s, recheck)
        s["wait"], s["why"] = wait_state(s, t)
        s["running"] = s["wait"] is None
        s["signals"] = wait_signals(s, t)
    return found + hand


# Seconds a turn that has not ended may write nothing before the judge reads it: a turn
# that wrote within them runs.
STALE_SECONDS = 300
# Seconds after a turn ended by its transcript before a missing `Stop` marker counts as
# missed: the hook writes it a moment after the turn's last row.
MISSED_STOP = 15
# Seconds a row of the transcript may stand after the `Stop` marker and still belong to
# the turn the marker ended.
TURN_SLACK = 2


def _read_marks(s):
    """Fill the signals the hook files give into the session entry `s`: the `Stop` marker
    and what it read in the last message, the report, the judge's verdict, the jobs and the
    background work."""
    import session_state
    sid, shell = s["session"], s.get("mark_shell") or 0
    if s.get("claude"):
        s["idle_since"] = session_state.idle_since(session_state.idle_sessions(), sid, shell)
    s["asks"] = session_state.idle_asks(sid, shell)
    s["dialog"] = session_state.idle_dialog(sid, shell)
    s["error"] = session_state.idle_error(sid, shell)
    s["says_finished"] = session_state.idle_finished(sid, shell)
    s["unsure"] = session_state.idle_unsure(sid, shell)
    s["question"] = session_state.idle_question(sid, shell)
    s["judge"], s["judge_why"] = session_state.judged(sid, shell)
    s["judge_from"] = session_state.judge_source(sid, shell)
    s["reported"] = session_state.reported(sid, shell)
    s["reported_why"] = session_state.reported_why(sid, shell)
    s["jobs"] = session_state.all_jobs(sid, shell, session_transcript(sid))
    s["tasks"] = session_state.background_tasks(sid, shell)


def _judge_needed(s):
    """Whether no rule before the judge decides the session (`wait_state` steps 0 to 3), so
    the judge has to read it."""
    return not (s.get("dialog") or s.get("error") or s.get("asks") or s.get("says_finished")
                or s.get("reported") in ("input", "event", "finished")
                or s.get("jobs") or s.get("tasks"))


def read_turn(s, recheck=False, now=None):
    """Read the session's turn into the entry `s`: whether it `ended`, its last update
    (`updated`), what it does now (`doing`, `doing_since`), whether it is `stale`, the hook
    files' signals, the judge (`judging`, `judge_dead`, `rejudged`) and the recap (`recap`).

    The transcript corrects the hook files. A row after the `Stop` marker that starts a turn
    (a scheduled prompt, Claude Code calling the session back after its background work)
    ends what the marker said: no prompt of the owner removed it. A turn that ended by the
    transcript and has no marker `MISSED_STOP` seconds later gets the marker the `Stop`
    hook would have written. A turn that has not ended and wrote nothing for
    `STALE_SECONDS` is stale and goes to the judge, unless a tool call of it has no result
    yet (`calling`, `calling_since`): that call runs. An ended turn that no rule reads goes
    to the judge too. A verdict `unsure` on an ended turn types `/recap` into the session
    once, and the judge reads the recap.

    `recheck` is the owner's re-evaluation: a judge that died without a verdict, and a recap
    that never came, are tried again (`session_state.rejudge`)."""
    import session_state
    import time
    now = time.time() if now is None else now
    sid, shell = s["session"], s.get("mark_shell") or 0
    if s.get("provider", "claude") != "claude":
        # Another provider has no transcript of Claude's and no hook: only `report` signals.
        _read_marks(s)
        s.update(ended=not s.get("claude"), updated=None, doing="", doing_since=0,
                 stale=False, calling="", calling_since=0, judging=False, rejudged=False,
                 judge_dead=False, recap="")
        return s
    path = session_transcript(sid) if sid else None
    act = session_state.transcript_activity(path) if path is not None else None
    live = bool(s.get("claude") and s.get("hooked"))
    _read_marks(s)
    marker = s.get("idle_since")
    if live and marker and not s["dialog"] and act and not act["ended"] \
            and act["since"] > marker + TURN_SLACK:
        session_state.mark_busy(sid, shell)
        _read_marks(s)
        marker = s.get("idle_since")
    # A turn whose transcript ends in Claude Code's API-error message ended in that error,
    # whatever marker a missed `StopFailure` left: the error text is no message to judge.
    if live and act and act["ended"] and act["error"] and not s["dialog"] \
            and s["error"] != act["error"] and now - act["when"] >= MISSED_STOP:
        session_state.mark_stale(sid, shell, act["text"], act["since"], act["error"])
        _read_marks(s)
        marker = s.get("idle_since")
    if live and not marker and act and act["ended"] and now - act["when"] >= MISSED_STOP:
        session_state.mark_stale(sid, shell, act["text"], act["since"], act["error"])
        _read_marks(s)
        marker = s.get("idle_since")
    s["ended"] = not s.get("claude") or bool(marker and not s["dialog"])
    s["updated"] = act["when"] if act else marker
    s["doing"] = act["doing"] if act else ""
    s["doing_since"] = act["since"] if act else 0
    quiet = bool(live and not s["ended"] and not s["dialog"] and act
                 and now - act["when"] > STALE_SECONDS)
    # A tool call that has no result yet runs: the turn is busy with it, not stale.
    s["calling"] = (act.get("call") or "") if quiet else ""
    s["calling_since"] = (act.get("call_since") or 0) if quiet else 0
    s["stale"] = quiet and not s["calling"]
    s["judging"] = s["rejudged"] = s["judge_dead"] = False
    s["recap"] = ""
    if not (live and (s["ended"] or s["stale"]) and _judge_needed(s)):
        return s
    try:
        if recheck:
            s["rejudged"] = session_state.rejudge(sid, shell, now)
        if s["ended"]:
            started = session_state.ensure_judged(sid, shell, P.ROOT, path)
        else:
            started = session_state.ensure_judged_stale(sid, shell, P.ROOT, act)
        s["judging"] = started or session_state.judge_started(sid, shell)
        s["judge"], s["judge_why"] = session_state.judged(sid, shell)
        s["judge_from"] = session_state.judge_source(sid, shell)
        at = session_state.judge_started_at(sid, shell)
        s["judge_dead"] = bool(at) and not s["judge"] \
            and now - at >= session_state.JUDGE_STALE
        if s["ended"] and s["judge"] == "unsure" and s["judge_from"] != "recap":
            s["recap"] = _recap(s, act, path, now)
    except Exception:                                   # noqa: BLE001 - the scan goes on
        pass
    return s


def _recap(s, act, path, now):
    """Ask the session for its recap when the judge is unsure of its last message: type
    `/recap` into its terminal once per turn, then start the judge on the recap once it
    stands in the transcript. Returns the recap's state for the signals: `sent`, `missing`
    (none came within `RECAP_WAIT`), `reading`, `dead` (the judge on it left no verdict) or
    `unsent` (no transcript, or the terminal took no keys)."""
    import session_state
    sid, shell = s["session"], s.get("mark_shell") or 0
    if not act or not act.get("since"):
        return "unsent"
    turn = "%.3f" % act["since"]
    rec = session_state.recap_record(sid, shell)
    if rec.get("turn") != turn:
        try:
            send_session(s, "/recap", ["Enter"])
        except Exception:                               # noqa: BLE001 - no terminal, no recap
            return "unsent"
        session_state.mark_recap_sent(sid, shell, turn, now)
        return "sent"
    try:
        sent, judging = float(rec.get("sent") or 0), float(rec.get("judging") or 0)
    except (TypeError, ValueError):
        sent = judging = 0
    if judging:
        return "dead" if now - judging >= session_state.JUDGE_STALE else "reading"
    text = session_state.recap_after(sid, path, sent)
    if text:
        session_state.start_judge(sid, shell, text, P.ROOT, recap=True)
        return "reading"
    return "missing" if now - sent >= session_state.RECAP_WAIT else "sent"


# Seconds a new Claude may take to reach its first hook before the board shows it as
# waiting: a start takes a few seconds, a question before the first turn waits for ever.
START_GRACE = 20

# The `last_event` of a live record that `live_sessions` wrote back from the transcript.
RECOVERED = "Recovered"


def _age(filetime):
    """Seconds since a process start that `winproc.created_at` gave, 0 when unknown."""
    if not filetime:
        return 0
    import time
    return time.time() - (filetime / 1e7 - 11644473600)


def _waits_on_tickets(s, t):
    """Whether the session's ticket waits for other tickets: blocked by another ticket, or a
    blocker or a child not yet in Verification or Done."""
    return t is not None and bool(
        s.get("waits_for")
        or (t.state == lane("blocked") and t.status == S("blocked by other task")))


def plan_done(t):
    """Whether a plan session has done its job on this ticket: the ticket is in the active
    lane with status `ready to implement`, or a work session already took it further."""
    return (t.state, t.status) == (lane("active"), S("ready to implement")) or (
        t.state in (lane("verify"), lane("closed"))
        or (t.state, t.status) == P.LAUNCH_LANE["work"])


# What in a plan marks a decision, a name or a value the plan leaves to someone else.
_PLAN_OPEN = re.compile(
    r"\bTBD\b|\?\?\?|\bto be (?:decided|confirmed|named|chosen)\b|\bopen questions?\b"
    r"|\bquestions? for the owner\b|\b(?:ask|asks) the owner\b"
    r"|\bowner (?:must|has to|to|should|will) (?:decide|choose|pick|name|give|confirm|say)\b",
    re.I)
# An open-questions line that holds none: `Open questions: none`.
_PLAN_NONE = re.compile(r":\s*(?:none|n/?a|-|nothing)\.?\s*$", re.I)


def plan_open(t):
    """What the ticket's plan leaves open, so that a worker cannot carry out every step
    without asking: no `## Plan`, a difficulty still `unknown`, a line of the plan that asks
    (`?`) or names an open decision (`TBD`, `open question`, `the owner must decide`). ""
    when nothing is open. A plan that a worker cannot carry out is not finished."""
    m = re.search(r"^##\s+Plan\s*$", t.body, re.M)
    if not m:
        return "the ticket has no ## Plan"
    if t.difficulty == "unknown":
        return "its difficulty is still unknown"
    rest = t.body[m.end():]
    nxt = re.search(r"^##\s+\S", rest, re.M)
    for ln in (rest[:nxt.start()] if nxt else rest).splitlines():
        ln = " ".join(re.sub(r"[*_`]", "", ln).split()).strip(" \t\"')>-#")
        if not ln or (_PLAN_OPEN.search(ln) and _PLAN_NONE.search(ln)):
            continue
        if ln.endswith("?") or _PLAN_OPEN.search(ln):
            return "the plan leaves open: %s" % ln[:200]
    return ""


def finish_undone(t, mode):
    """Why a session's claim that it is finished is not believable, "" when it is. The claim
    holds for a `work` session whose ticket is in the verify or the closed lane or blocked by
    another task, a `plan` session whose ticket is `ready to implement` (or taken further)
    and whose plan leaves nothing open, and a `review` or `verify` session whose ticket has
    left `investigating` / `testing`. A session with no ticket or no launch mode, and a
    manager, are not judged here."""
    if t is None or mode not in MODES or t.repeat == MANAGER:
        return ""
    where = "%s %s" % (t.state, t.status)
    if mode == "work":
        if t.state in (lane("verify"), lane("closed")) or (
                t.state == lane("blocked") and t.status == S("blocked by other task")):
            return ""
        return "%s is in %s" % (t.id, where)
    if mode == "plan":
        if not plan_done(t):
            return "%s is in %s" % (t.id, where)
        if (t.state, t.status) == (lane("active"), S("ready to implement")):
            return plan_open(t)
        return ""
    if (t.state, t.status) == P.LAUNCH_LANE[mode]:
        return "%s is still in %s" % (t.id, where)
    return ""


# The reason a session that claims it is finished with its work undone waits for the owner.
UNDONE = "said %s with the work undone"


def _asked(verb, s):
    """`asked: <question>` for the board's hover, with the question the `Stop` hook kept from
    the last message; `asked a question` when it kept none."""
    q = s.get("question") or ""
    return "%s: %s" % (verb, q) if q else "%s a question" % verb


def _blocked_text(s, t):
    """`blocked by T0002` or `waits for T0003, T0004`: the tickets the session's ticket
    waits for."""
    if t.state == lane("blocked") and t.status == S("blocked by other task"):
        return "blocked by %s" % (", ".join(t.blocked_by or s.get("waits_for") or [])
                                  or "another ticket")
    return "waits for %s" % ", ".join(s.get("waits_for") or [])


def _working(s, t, why):
    """`blocker` when the session's ticket waits for other tickets, else `subagent`: it still
    does work that is not the owner's. `why` says what that work is."""
    if _waits_on_tickets(s, t):
        return "blocker", "%s; %s" % (_blocked_text(s, t), why)
    return "subagent", why


def _verdict(s, t, judge, why, source):
    """The state a judge's verdict `input`, `finished` or `working` gives, `source` naming
    what it read."""
    if judge == "input":
        return "input", "%s: %s" % (source, why or "waits for the owner")
    if judge == "working":
        return _working(s, t, "%s: still works: %s" % (source, why or "its own work"))
    if t is not None and t.repeat == MANAGER:
        return "idle", "manager: waits for the owner's orders"
    if t is None:
        return "idle", "%s: finished: %s; no ticket, so it stays open" % (source, why or "-")
    return "finished", "%s: %s" % (source, why or "finished")


def _quiet(s, now):
    """`no update for 6 min` since the session's last update."""
    import time
    now = time.time() if now is None else now
    return "no update for %d min" % (max(0, now - (s.get("updated") or now)) // 60)


def _call_text(s):
    """`Bash: run the tests, since 21:02` of the tool call a session's turn runs."""
    import time
    since = s.get("calling_since")
    return "%s, since %s" % (s["calling"], time.strftime("%H:%M", time.localtime(since))) \
        if since else s["calling"]


def wait_state(s, t, now=None):
    """What a session waits for, as `(wait, why)`. `t` is its ticket or None.

    `wait` is `None` while its turn runs, `input` when it explicitly waits for the owner's
    answer, `event` when it waits for something outside the project to happen, such as an
    editor restart, `subagent` when it still does work that is not the owner's (the board
    says `working`), `blocker` when it waits for other tickets, `idle` when nothing says yet
    what it waits for, and `finished` when it waits for nothing. The first row that matches
    decides:

        0  Claude has exited                               finished
           no hook has run under this Claude and it has no
             transcript: a question before its first turn,
             such as folder trust (while it starts: None)  input
           a dialog stops the turn: a permission, an
             AskUserQuestion, a plan to approve            input
           the turn ended in an API error (`StopFailure`)  input
           the session reported `event` (a finished line
             ends the wait)                                 event
           the session reported `input` (a finished line
             does not take the question back)              input
           a plan session's turn ended, its ticket is
             `ready to implement`, and the plan leaves a
             decision, a name or a value open (`plan_open`) input
        1  the turn ended, and its last line is `WORK IS
             FINISHED`, or its last lines say its ticket
             is in the verify or the closed lane, or it
             reported `finished`                           finished (a manager: idle)
             ... while its ticket says the work is undone
             (`finish_undone`)                             input
        2  the turn ended, and its last line asks          input
        3  it holds a recurring job, or its own subagent
             or background shell runs                      blocker when its ticket waits for
                                                           other tickets, else subagent
        4  the turn runs and wrote within `STALE_SECONDS`,
             or a tool call of the turn has no result yet  None
        5  the judge's verdict on the last message of the
             ended turn, or of the stale one               input, finished, subagent/blocker
           no verdict yet                                  idle (a stale turn: None)
        6  the judge is unsure: its verdict on the recap
             the board asked for                           input, finished, subagent/blocker
           no recap, or unsure again                       idle

    Where the ticket stands decides nothing: the judge reads it with the last message. The
    finished line outranks a job: the session said it is done. The judge's `finished` never
    finishes a session with no ticket, which the owner opened and talks to, nor a manager,
    which waits for the owner's orders."""
    provider = s.get("provider", "claude")
    if not s.get("claude"):
        return "finished", "Claude exited" if provider == "claude" else "%s exited" % provider
    if provider != "claude":
        # No hook reads another provider's turn: what it reported decides, else it runs.
        if s.get("reported") == "input":
            return "input", "reported: %s" % (s.get("reported_why") or "a question, not named")
        if s.get("reported") == "event":
            return "event", "waits for %s" % (s.get("reported_why") or "an outside event")
        if s.get("reported") == "finished":
            return ("idle", "reported finished; a manager waits for the owner's orders") \
                if t is not None and t.repeat == MANAGER else ("finished", "reported finished")
        return None, "%s runs; it has no hook, so only `report` says what it waits for" % provider
    if s.get("hooked") is False:
        if s.get("starting"):
            return None, "starts"
        return "input", "has not begun: a question before its first turn, such as folder trust"
    if s.get("dialog"):
        return "input", ("dialog: %s" % s["question"] if s.get("question") else
                         "a dialog waits for an answer: a permission, a question or a plan")
    if s.get("error"):
        import time
        since = s.get("idle_since")
        return "input", "its turn ended in an API error%s (%s): try again" % (
            time.strftime(" at %Y-%m-%d %H:%M", time.localtime(since)) if since else "",
            s["error"])
    if s.get("reported") == "event" and not s.get("says_finished"):
        return "event", "waits for %s" % (s.get("reported_why") or "an outside event")
    import session_state
    said = s.get("says_finished")
    said = said if isinstance(said, str) and said else session_state.FINISHED_LINE
    claim = UNDONE % said if s.get("says_finished") else "reported finished with the work undone"
    question = (s.get("reported_why") or s.get("question")
                or (s.get("judge") == "input" and s.get("judge_why")) or "a question, not named")
    # Work is never finished while a question is open: the finished line does not take back
    # a question reported in the same turn.
    if s.get("reported") == "input":
        if s.get("says_finished"):
            return "input", "%s: reported: %s" % (UNDONE % said, question)
        return "input", "reported: %s" % question
    manager = t is not None and t.repeat == MANAGER
    ended = bool(s.get("ended"))
    mode = s.get("mode") or ""
    # A plan that leaves a decision, a name or a value open cannot be executed, so its
    # planner is not finished, whatever it said and whatever the judge reads.
    if ended and mode == "plan" and t is not None and not manager and (
            t.state, t.status) == (lane("active"), S("ready to implement")):
        undone = plan_open(t)
        if undone:
            if s.get("says_finished") or s.get("reported") == "finished":
                return "input", "%s: %s" % (claim, undone)
            return "input", undone
    if ended and (s.get("says_finished") or s.get("reported") == "finished"):
        if manager:
            return "idle", "manager: waits for the owner's orders"
        # The claim must agree with the ticket: a finished line over undone work closes
        # nothing, and the owner sees it among the sessions that wait for input.
        undone = finish_undone(t, mode)
        if undone:
            return "input", "%s: %s" % (claim, undone)
        if s.get("says_finished"):
            return "finished", "said %s" % said
        return "finished", "reported finished"
    if ended and s.get("asks"):
        return "input", _asked("asked", s)
    if s.get("tasks"):
        return _working(s, t, "its own " + _tasks_text(s["tasks"]))
    if s.get("jobs"):
        return _working(s, t, "its scheduled job %s" % ", ".join(s["jobs"]))
    if not ended and s.get("calling"):
        return None, "%s; a tool call runs: %s" % (_quiet(s, now), _call_text(s))
    if not ended and not s.get("stale"):
        return None, ""
    judge, judge_why = s.get("judge") or "", s.get("judge_why") or ""
    quiet = "" if ended else _quiet(s, now) + "; "
    if judge == "unsure":
        if s.get("judge_from") == "recap":
            return "idle", "%sthe judge is unsure, of its recap too: %s" % (quiet, judge_why or "-")
        recap = s.get("recap") or ""
        if recap in ("sent", "reading"):
            return "idle", "the judge is unsure of its last message; it reads the recap"
        if recap == "missing":
            return "idle", "the judge is unsure; no recap came: %s" % (judge_why or "-")
        if recap == "dead":
            return "idle", "the judge is unsure; the judge on the recap left no verdict"
        return "idle", "%sthe judge is unsure: %s" % (quiet, judge_why or "-")
    if judge:
        return _verdict(s, t, judge, judge_why,
                        "recap judge" if s.get("judge_from") == "recap" else "judge")
    if s.get("judge_dead"):
        return "idle", "%sthe judge left no verdict" % quiet
    if ended:
        if manager:
            return "idle", "manager: waits for the owner's orders"
        if s.get("judging"):
            return "idle", "asked nothing; the judge reads its last message"
        return "idle", "asked nothing; no rule reads its last message"
    if s.get("judging"):
        return None, quiet + "the judge reads its last message"
    return None, quiet.rstrip("; ")


def _ago(when):
    """`12 s ago`, `4 min ago`, `3 h ago` since a `time.time()` stamp; "" for none."""
    import time
    if not when:
        return ""
    sec = max(0, time.time() - when)
    if sec < 120:
        return "%d s ago" % sec
    if sec < 7200:
        return "%d min ago" % (sec // 60)
    return "%d h ago" % (sec // 3600)


def wait_signals(s, t):
    """Every signal `wait_state` reads for this session, as `[name, value]` pairs, and last
    the state it decided with its reason. `t` is its ticket or None. A signal that is absent
    says so: a missed trigger shows as the signal that never came, a wrong decision as a
    signal that is present and a rule that read it otherwise."""
    import session_state
    sid, shell = s.get("session") or "", s.get("mark_shell") or 0
    out = [["Claude", "pid %s" % s["claude"] if s.get("claude") else "exited"]]
    if s.get("hooked") is False:
        out.append(["hooks", "none has fired under this Claude"])
    elif s.get("recovered"):
        out.append(["hooks", "files gone; read back from the transcript"])
    else:
        out.append(["hooks", "fired"])
    since = s.get("idle_since")
    out.append(["Stop marker", "written %s" % _ago(since) if since
                else "none since the last prompt"])
    out.append(["turn", "ended" if s.get("ended") else
                "runs, %s: stale" % _quiet(s, None) if s.get("stale") else
                "runs, %s: a tool call runs: %s" % (_quiet(s, None), _call_text(s))
                if s.get("calling") else "runs"])
    out.append(["last update", _ago(s.get("updated")) or "unknown"])
    out.append(["doing", "%s (%s)" % (s["doing"], _ago(s.get("doing_since")))
                if s.get("doing") and s.get("doing_since") else s.get("doing") or "-"])
    said = s.get("says_finished")
    last = ("a dialog waits" if s.get("dialog")
            else "API error %s" % s["error"] if s.get("error")
            else "asks on its last line" if s.get("asks")
            else "says %s" % (said if isinstance(said, str)
                              else session_state.FINISHED_LINE) if said
            else "a ? stands above its last line" if s.get("unsure")
            else "no rule reads it" if since else "-")
    out.append(["last message", last])
    rep = s.get("reported") or ""
    out.append(["report", "%s%s" % (rep, ": " + s["reported_why"] if s.get("reported_why")
                                    else "") if rep else "none"])
    judge, judge_why = s.get("judge") or "", s.get("judge_why") or ""
    if judge:
        jv = "%s%s: %s" % (judge, " (of the recap)" if s.get("judge_from") == "recap" else "",
                           judge_why) if judge_why else judge
    elif s.get("rejudged"):
        jv = "the last one left no verdict: started again"
    elif s.get("judge_dead"):
        jv = "left no verdict"
    elif s.get("judging"):
        at = session_state.judge_started_at(sid, shell) if sid else 0
        jv = "started %s, no verdict yet" % _ago(at) if at else "started, no verdict yet"
    elif not (s.get("ended") or s.get("stale")):
        jv = "not needed: the turn runs"
    elif not _judge_needed(s):
        jv = "not needed: a rule decides"
    else:
        jv = "not run"
    out.append(["judge", jv])
    recap = s.get("recap") or ""
    if recap:
        rec = session_state.recap_record(sid, shell) if sid else {}
        sent = " " + _ago(rec.get("sent")) if rec.get("sent") else ""
        out.append(["recap", {"sent": "sent%s, not yet in the transcript" % sent,
                              "missing": "sent%s, none came" % sent,
                              "reading": "read; the judge reads it",
                              "dead": "read; its judge left no verdict",
                              "unsent": "could not be sent"}.get(recap, recap)])
    elif s.get("judge_from") == "recap":
        out.append(["recap", "read"])
    else:
        out.append(["recap", "not needed"])
    out.append(["background work", _tasks_text(s["tasks"]) if s.get("tasks") else "none"])
    out.append(["jobs", ", ".join(s["jobs"]) if s.get("jobs") else "none"])
    if t is None:
        out.append(["ticket", "none"])
    else:
        out.append(["ticket", "%s / %s" % (P.STATE_LABEL.get(t.state, t.state), t.status)])
        out.append(["waits for", ", ".join(s.get("waits_for") or []) or "none"])
    if sid and s.get("id"):
        try:
            out.append(["transcript", "written %s" % _ago(session_transcript(sid).stat().st_mtime)])
        except OSError:
            out.append(["transcript", "not found"])
    word = "running" if s.get("wait") is None else s["wait"]
    out.append(["decided", "%s: %s" % (word, s["why"]) if s.get("why") else word])
    return out


def _tasks_text(tasks):
    """`1 subagent: <description>`, `2 background shells`, or a mix: what a session waits
    for, from `session_state.background_tasks`."""
    kinds = {}
    for t in tasks:
        kind = "subagent" if t.get("type") in ("subagent", "agent", "") else "background " + (
            "shell" if t.get("type") in ("shell", "bash") else str(t.get("type")))
        kinds.setdefault(kind, []).append(t)
    parts = ["%d %s%s" % (len(xs), k, "" if len(xs) == 1 else "s") for k, xs in kinds.items()]
    text = ", ".join(parts)
    if len(tasks) == 1 and tasks[0].get("description"):
        text += ": " + tasks[0]["description"]
    return text


def manager_dead(t, sessions):
    """Whether a manager ticket that must run has lost its session: it sits in the active
    lane, and no session of `sessions` (as `live_sessions` gives them) is its `work` session
    with a Claude process. A shell that is gone and a shell whose Claude exited are both
    dead. Read off the process table only; no text on the screen counts."""
    if t.repeat != MANAGER or t.state != lane("active"):
        return False
    return not any(s.get("id") == t.id and s.get("mode") == "work" and s.get("claude")
                   for s in sessions)


def manager_running(t, sessions):
    """Whether a manager ticket has its `work` session with a Claude process, in any lane."""
    return any(s.get("id") == t.id and s.get("mode") == "work" and s.get("claude")
               for s in sessions)


def ensure_manager():
    """The manager ticket of this project, filed from `MANAGER_TEMPLATE` when the project has
    none in any lane. A retired manager counts: the owner cancelled it, so none is filed.
    Returns `(ticket, created)`. The caller makes the home project current (`in_manager_home`)."""
    ensure_tree()
    have = sorted((t for t in load_all() if t.repeat == MANAGER), key=lambda t: t.id)
    if have:
        live = [t for t in have if t.state != lane("closed")]
        return (live or have)[0], False
    tid = file_template(_read(MANAGER_TEMPLATE))
    write_index(quiet=True)
    return find(load_all(), tid), True


def cmd_manager(args):
    """The manager ticket of the home project, whichever project calls it: filed from the
    template when there is none, and with `--start` launched when its session is not running.
    `--claim` reads the calling session in the calling project, then claims the home ticket."""
    claim = getattr(args, "claim", False)
    origin = P.ROOT
    sid = own_session_id() if claim else ""
    try:
        with in_manager_home():
            return _manager_at_home(args, claim, sid, origin)
    except LookupError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2


def _manager_at_home(args, claim, sid, origin):
    t, created = ensure_manager()
    print("%s  %s  %s%s" % (t.id, manager_home_name(), t.path.relative_to(P.ROOT),
                            "  (filed from the template)" if created else ""))
    if claim:
        return claim_manager(t, take_over=getattr(args, "take_over", False), sid=sid or None,
                             origin=origin)
    if not args.start:
        return 0
    if t.state == lane("closed"):
        print("  retired as %s: not started" % t.status)
        return 0
    if manager_running(t, live_sessions([t])):
        print("  running")
        return 0
    return cmd_work(relaunch_args(t, args.terminal, args.dry_run))


def own_session_id():
    """The session id of the Claude this process runs under, from the live record its hooks
    wrote in this project, or "" when no hook of this project names it."""
    import session_state
    import winproc
    procs = winproc.processes()
    claude = session_state.owning_claude(procs)
    if not claude:
        return ""
    for sid, rec in session_state.live_records(procs).items():
        if rec.get("claude") == claude and rec.get("claude_alive"):
            return sid
    return ""


def adopt_session(sid, origin):
    """Make the session `sid`, whose hooks write in the project at `origin`, a session of the
    current project: its live record moves here, and a redirect marker in `origin` sends its
    hooks here from now on. Nothing happens when `origin` is the current project."""
    import session_state
    if Path(origin).resolve() == P.ROOT.resolve():
        return False
    here = P.ROOT
    tok = _project.use(origin)
    try:
        rec = session_state.read_live(sid)
        session_state.redirect(sid, here)
        session_state.drop_live(sid)
    finally:
        _project.reset(tok)
    if rec is not None:
        session_state.write_live(sid, rec)
    return True


def running_managers(skip=""):
    """Every manager session that runs in any registered project, but the session `skip`:
    `[(project name, root, ticket id, session)]`."""
    out = []
    for r in _project.load_registry():
        try:
            tok = _project.use(Path(r["path"]))
        except Exception:                               # noqa: BLE001 - an unreachable project
            continue
        try:
            tickets = load_all()
            managers = {t.id for t in tickets if t.repeat == MANAGER}
            if managers:
                for s in live_sessions(tickets):
                    if s["id"] in managers and s["mode"] == "work" and s.get("claude") \
                            and s["session"] != skip:
                        out.append((r["name"], Path(r["path"]), s["id"], s))
        except Exception:                               # noqa: BLE001
            pass
        finally:
            _project.reset(tok)
    return out


def claim_manager(t, take_over=False, sid=None, origin=None):
    """Make the session this process runs under the `work` session of manager `t`: the
    ticket records it under `sessions:` and `session:`, moves to the active lane, and its
    brief is written. The watchdog then guards this session and resumes it when it dies, and
    the board starts no second manager.

    A manager that runs in any registered project refuses the claim (exit 3) unless
    `take_over`: then its terminal is closed, and a manager ticket other than `t` returns to
    the start lane, as `restart --no-launch` leaves it.

    `origin` is the project the session runs in. When it is not this one, `adopt_session`
    makes the session this project's, so the watchdog here sees it."""
    import datetime
    if t.state == lane("closed"):
        print("Error: %s is retired as %s" % (t.id, t.status), file=sys.stderr)
        return 2
    sid = sid or own_session_id()
    if not sid:
        print("Error: no hook names the session this runs in: run it from a Claude session "
              "opened in a registered project", file=sys.stderr)
        return 2
    others = running_managers(skip=sid)
    if others and not take_over:
        for name, _root, tid, s in others:
            print("manager runs: %s %s, session %s, %s"
                  % (name, tid, s["session"][:8], state_text(s, doing=False)))
        print("Error: a manager runs already; --take-over closes it and makes this session "
              "the manager", file=sys.stderr)
        return 3
    here = P.ROOT.resolve()
    for name, root, tid, s in others:
        tok = _project.use(root)
        try:
            try:
                close_session(s, force=True)
                print("closed the manager session of %s %s" % (name, tid))
            except (OSError, ValueError) as exc:
                print("could not close the manager session of %s %s: %s" % (name, tid, exc))
            if (root.resolve(), tid) != (here, t.id):
                cmd_restart(argparse.Namespace(id=tid, no_launch=True, dry_run=False))
        finally:
            _project.reset(tok)
    if origin is not None:
        adopt_session(sid, origin)
    t = find(load_all(), t.id)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    entries = [e for e in t.sessions if e["mode"] != "work" and e["session"] != sid]
    entries.append({"mode": "work", "session": sid, "launched": stamp})
    t.meta["session"] = sid
    t.meta["launched"] = "work %s" % stamp
    t.meta.pop(PROVIDER_FIELD, None)
    t.meta.pop(OLD_PROVIDER_FIELD, None)
    t.meta["sessions"] = "; ".join("%s %s %s" % (e["mode"], e["session"], e["launched"])
                                   for e in entries)
    _write(t.path, render(t.meta, t.body))
    print(start_lane(t, "work"))
    P.BRIEFS.mkdir(parents=True, exist_ok=True)
    brief = P.BRIEFS / ("%s.md" % t.id)
    _write(brief, build_brief(t, "work"))
    print("%s  session %s is the manager now" % (t.id, sid))
    print("instructions: %s" % brief.as_posix())
    return 0


def relaunch_args(t, terminal=None, dry_run=False):
    """The arguments of `cmd_work` that start a manager's session again. Its `work` session
    id is on the ticket, so the launch resumes it and the manager keeps its context."""
    return argparse.Namespace(id=t.id, mode="work", terminal=terminal, note="", model=None,
                              effort=None, no_fable=False, auto=False, dry_run=dry_run,
                              provider=ticket_provider(t) or None)


def cmd_remote_control(args):
    """Set Remote Control on tickets. `on` reaches a live session that waits at its prompt at
    once; everything else reaches the next launch."""
    tickets = load_all()
    found = []
    for tid in args.ids:
        t = find(tickets, tid)
        if t is None:
            print("Error: no ticket %r" % tid, file=sys.stderr)
            return 2
        if not remote_control_available(t):
            print("Error: %s launches %s, which has no Remote Control"
                  % (t.id, ticket_provider(t) or P.provider), file=sys.stderr)
            return 2
        found.append(t)
    for t in found:
        set_remote_control(t, args.value)
        typed = apply_remote_control(t)
        print("%s  remote control %s; %s" % (t.id, "on" if remote_control(t) else "off",
                                             "typed into %s" % ", ".join(typed) if typed
                                             else "from its next launch"))
    write_index(quiet=True)
    return 0


def cmd_watchdog(args):
    """Every manager ticket of the home project, alive or dead, whichever project calls it.
    `--once` starts the dead ones again. The board server runs the same check every 30
    seconds, with a grace after each launch and a limit on restarts; this is the owner's
    manual check."""
    try:
        with in_manager_home():
            return _watchdog_at_home(args)
    except LookupError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2


def _watchdog_at_home(args):
    tickets = load_all()
    managers = sorted((t for t in tickets if t.repeat == MANAGER), key=lambda t: t.id)
    if not managers:
        print("no manager ticket")
        return 0
    sessions = live_sessions(tickets)
    rc = 0
    for t in managers:
        if t.state != lane("active"):
            print("%s  %-8s %s / %s" % (t.id, "idle", P.STATE_LABEL[t.state], t.status))
            continue
        dead = manager_dead(t, sessions)
        print("%s  %-8s %s" % (t.id, "dead" if dead else "alive", t.title))
        if dead and args.once:
            if args.dry_run:
                print("  would relaunch %s" % t.id)
                continue
            rc = cmd_work(relaunch_args(t, getattr(args, "terminal", None))) or rc
    return rc


def finished_sessions(sessions):
    """The sessions that wait for nothing: what `close --finished` and the board's
    `Close finished` close."""
    return [s for s in sessions if s.get("wait") == "finished"]


def autoclose_sessions(sessions, tickets):
    """The `finished` sessions the board closes by itself: launched on a ticket, and the
    ticket is no manager. A session with no ticket is one the owner opened and talks to, and
    a manager waits for the owner's orders, so neither closes without the owner."""
    import session_state
    by_id = {t.id: t for t in tickets}
    # A session the owner writes to is a conversation: only its own `WORK IS FINISHED`
    # closes it (the `Stop` hook), never the board.
    return [s for s in finished_sessions(sessions)
            if s.get("id") in by_id and by_id[s["id"]].repeat != MANAGER
            and not session_state.owner_talks(s.get("session") or "", s.get("shell") or 0)]


def _matches(s, ident):
    """Whether `ident` names this session: its ticket id, its session id, or the first
    eight or more characters of its session id."""
    want = ident.strip().lower()
    if s["id"] and s["id"].lower() == want:
        return True
    sid = s["session"]
    return sid == want or (len(want) >= 8 and sid.startswith(want))


def find_session(ident, mode=None, sessions=None):
    """The live session for this ticket, or `None`. With no mode, the newest one."""
    want = (sessions if sessions is not None else live_sessions())
    hits = [s for s in want if _matches(s, ident) and (mode is None or s["mode"] == mode)]
    return hits[-1] if hits else None


def pty_records(procs=None):
    """`shell pid -> record` for every pseudoconsole host that still runs. The record is
    what `session_pty.py` wrote: `host_pid`, `shell_pid`, `port`, `token`, `cols`, `rows`,
    `log`. A record whose host is gone is removed."""
    import json
    import winproc
    procs = winproc.processes() if procs is None else procs
    out = {}
    for p in sorted(P.SESSIONS_DIR.glob("*.pty.json")) if P.SESSIONS_DIR.is_dir() else []:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("host_pid") not in procs:
            try:
                p.unlink()
            except OSError:
                pass
            continue
        rec["path"] = str(p)
        out[rec.get("shell_pid")] = rec
    return out


def pty_call(s, request, timeout=5.0):
    """One request to the pseudoconsole host of an own session, answered as a dict. The
    request is a dict with `cmd`: `screen`, `send`, `log`, `resize`, `stop`. The host comes
    from the session's `pty` record, which `live_sessions` keeps, else from a new scan."""
    import json
    import socket
    rec = s.get("pty") or pty_records().get(s["shell"])
    if rec is None:
        raise OSError("%s has no pseudoconsole host" % s["key"])
    request = dict(request, token=rec["token"])
    with socket.create_connection(("127.0.0.1", rec["port"]), timeout=timeout) as sock:
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    reply = json.loads(buf.decode("utf-8") or "{}")
    if reply.get("error"):
        raise OSError(reply["error"])
    return reply


# The one `console_io.py serve` worker of this process, and the lock that keeps one request
# and its reply together. A new Python per call cost ~200 ms of every terminal call.
_CONSOLE_WORKER = [None]
_CONSOLE_LOCK = threading.Lock()
CONSOLE_TIMEOUT = 20


def _console_worker(restart=False):
    """The running `console_io.py serve` worker, started when there is none or `restart`."""
    import subprocess
    proc = _CONSOLE_WORKER[0]
    if proc is not None and (restart or proc.poll() is not None):
        _stop_console_worker()
        proc = None
    if proc is None:
        CREATE_NO_WINDOW = 0x08000000
        proc = subprocess.Popen([sys.executable, str(WIN / "console_io.py"), "serve"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                creationflags=CREATE_NO_WINDOW)
        _CONSOLE_WORKER[0] = proc
    return proc


def _stop_console_worker():
    proc, _CONSOLE_WORKER[0] = _CONSOLE_WORKER[0], None
    if proc is None:
        return
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:                                   # noqa: BLE001 - it is gone either way
        pass


def _console_io(cmd, s, **fields):
    """Ask the `win/console_io.py serve` worker to `screen` or `send` on the session's
    console, and return its JSON reply. The attach is process-wide, so the board server never
    attaches itself: one windowless worker serves every session, one request at a time, and
    detaches after each.

    A worker that is gone, or a pipe that breaks, starts a new worker once. A `send` whose
    reply is lost is not repeated, since its keys may have been typed. A reply that takes
    longer than `CONSOLE_TIMEOUT` seconds kills the worker."""
    import json
    pid = s["shell"] or s["claude"]
    if not pid:
        raise OSError("%s has no process to attach to" % s["key"])
    request = json.dumps(dict(fields, cmd=cmd, pid=int(pid))) + "\n"
    with _CONSOLE_LOCK:
        for attempt in (0, 1):
            proc = _console_worker(restart=attempt > 0)
            try:
                proc.stdin.write(request)
                proc.stdin.flush()
            except (OSError, ValueError):
                if attempt:
                    raise OSError("console_io %s: the worker does not take requests" % cmd)
                continue
            timed_out = []
            timer = threading.Timer(CONSOLE_TIMEOUT, lambda: (timed_out.append(1), proc.kill()))
            timer.start()
            try:
                line = proc.stdout.readline()
            except (OSError, ValueError):
                line = ""
            finally:
                timer.cancel()
            if line:
                break
            _stop_console_worker()
            if timed_out:
                raise OSError("console_io %s: no reply within %d s" % (cmd, CONSOLE_TIMEOUT))
            if attempt or cmd != "screen":
                raise OSError("console_io %s: the worker ended without a reply" % cmd)
    try:
        reply = json.loads(line)
    except ValueError:
        raise OSError("console_io %s: %s" % (cmd, line.strip()[-300:]))
    if reply.get("error"):
        raise OSError(reply["error"])
    return reply


def screen_session(s):
    """The screen of the session's terminal: `cols`, `rows`, `cursor`, `lines`."""
    if not WINDOWS:
        import posixterm
        return posixterm.screen(s["shell"])
    if s.get("own"):
        return pty_call(s, {"cmd": "screen"})["screen"]
    return _console_io("screen", s)


def send_session(s, text="", keys=()):
    """Type `text`, then the named `keys`, into the session's terminal."""
    keys = [x for x in keys if x]
    if not WINDOWS:
        import posixterm
        return posixterm.send(s["shell"], text, keys)
    if s.get("own"):
        return pty_call(s, {"cmd": "send", "text": text, "keys": keys}).get("written", 0)
    return _console_io("send", s, text=text, keys=keys).get("written", 0)


def log_session(s, offset=0):
    """The scrolled output of an own session, VT sequences removed, from `offset`. An
    external terminal keeps no log. Under tmux every session keeps one."""
    if not WINDOWS:
        import posixterm
        return posixterm.log(s["shell"], offset)
    if not s.get("own"):
        raise OSError("%s runs in an external terminal, which keeps no log" % s["key"])
    return pty_call(s, {"cmd": "log", "offset": int(offset)})


def focus_session(s):
    """Bring the session's terminal to the front. Returns True when it landed there."""
    if not WINDOWS:
        # The Terminal window whose tab runs the session's tmux client, or its shell; a tmux
        # session whose window was closed gets a new one.
        import posixterm
        return posixterm.focus(s["shell"] or s["claude"], s.get("title") or "",
                               P.SESSIONS_DIR)
    import winproc
    if s.get("own"):
        return False                                    # a pseudoconsole has no window
    # The window that shows the session's console is exact: the console's own window, or
    # the Windows Terminal window that owns its hidden pseudo window. It counts only when
    # it is a visible terminal window. A session in a tab of a window that holds others
    # raises that window, and the tab the owner last chose stays the active one.
    #
    # Without it, the window is matched by its full title,
    # `<title> - <ticket> - <short title> - <mode>`: a bare ticket id would match the window
    # of another project that holds the same id. The title of a Windows Terminal window is
    # the title of its active tab. Last, the window of the process that owns a session the
    # board did not launch comes forward.
    hwnd = 0
    try:
        hwnd = int(_console_io("window", s).get("hwnd") or 0)
    except OSError:
        pass
    if hwnd and not any(h == hwnd and c in (winproc.WT_WINDOW_CLASS, winproc.CONSOLE_WINDOW_CLASS)
                        for h, c, _t, _p in winproc.windows()):
        hwnd = 0
    if hwnd:
        return winproc.raise_window(hwnd)
    full = ""
    if s["id"]:
        t = find(load_all(), s["id"])
        full = session_title(t, s["mode"]) if t else "%s - %s - %s" % (P.title, s["id"], s["mode"])
    hwnd = winproc.find_terminal_window(s.get("title") or full)
    if not hwnd and full:
        hwnd = winproc.find_terminal_window(full)
    if not hwnd and s.get("terminal"):
        for h, _c, _t, pid in winproc.windows():
            if pid == s["terminal"]:
                hwnd = h
                break
    return bool(hwnd) and winproc.raise_window(hwnd)


def close_session(s, force=False):
    """End the session's shell, which closes its tab or its window (`_end_shell`).

    `taskkill` without `/F` answers 255 on a process waiting at a console prompt - a
    console process ends forcefully or not at all - so the kill is always forceful. `force`
    is the caller's statement that it may also end a session whose Claude still runs; the
    caller is what asks the owner first."""
    import winproc
    if s["running"] and not force:
        raise ValueError("%s is still running" % s["key"])
    target = s["shell"] or s["claude"]
    procs, pid, steps = winproc.processes(), os.getpid(), 0
    while pid in procs and steps < 32:
        if pid == target:
            raise ValueError("%s runs this process (pid %d); it is not a session terminal"
                             % (s["key"], target))
        pid, steps = procs[pid][0], steps + 1
    if s.get("own") and WINDOWS:
        # The host ends its shell and closes the pseudoconsole; the kill below is the
        # fallback for a host that does not answer.
        try:
            pty_call(s, {"cmd": "stop"})
            return 0
        except (OSError, ValueError):
            pass
    if not s["shell"]:
        return winproc.taskkill(s["claude"], force=True, tree=True)
    return _end_shell(s["shell"], procs)


# Seconds `close_session` gives a `cmd /c` shell to run the reset and exit by itself once
# its Claude is gone.
SHELL_EXIT_WAIT = 5


def _end_shell(shell, procs):
    """End a session shell so that its tab or window closes. Returns 0, or 1 when the
    shell is still there.

    `taskkill /F` on the shell itself ends it with exit code 1, and Windows Terminal keeps
    a tab whose process exits with anything but 0. So the kill takes what runs under the
    shell, Claude and its tree, and leaves the console host alone. A launched shell is
    `cmd /c`: it runs the reset and exits with 0. A shell that does not exit by itself - a
    `cmd /k` shell launched before, or one the owner opened - is ended with exit code 0."""
    import winproc
    for child in winproc.children(procs, shell):
        if procs[child][1] not in ("conhost.exe",) + TERMINAL_IMAGES:
            winproc.taskkill(child, force=True, tree=True)
    if winproc.wait_exit(shell, SHELL_EXIT_WAIT):
        return 0
    winproc.terminate(shell, 0)
    return 0 if winproc.wait_exit(shell, SHELL_EXIT_WAIT) else 1


# Seconds the closer that `close --self` starts waits for its session to finish its turn.
SELF_CLOSE_WAIT = 600


def spawn_detached(argv):
    """Start `argv` with no console and out of the caller's job, and return at once. The
    child outlives the session that started it."""
    import subprocess
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    if not WINDOWS:
        # A session of its own: the end of the terminal it was started from does not end it.
        return subprocess.Popen(argv, start_new_session=True, **kw)
    # A job that forbids the breakaway refuses the start: start it inside the job then.
    try:
        return subprocess.Popen(argv, creationflags=flags
                                | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), **kw)
    except OSError:
        return subprocess.Popen(argv, creationflags=flags, **kw)


def _is_own_session(s, procs=None):
    """Whether this process runs inside the session: its shell or its Claude is an
    ancestor of this process."""
    import winproc
    procs = winproc.processes() if procs is None else procs
    own = {p for p in (s.get("shell"), s.get("claude")) if p}
    pid, steps = os.getpid(), 0
    while pid in procs and steps < 32:
        if pid in own:
            return True
        pid, steps = procs[pid][0], steps + 1
    return False


def close_when_finished(ident, mode=None, wait=SELF_CLOSE_WAIT, poll=2, now=False):
    """Wait until the session's turn has ended, then close its terminal when it is
    `finished`. A session that waits for the owner, an event, a subagent or a blocker stays
    open, and so does one that is still busy after `wait` seconds. Returns the rc of the
    close, 1 when the close refused, else 0.

    `now` is the `Stop` hook's word that the turn ended with a believable `WORK IS
    FINISHED`: the terminal closes at once, with no wait for the state word, while that turn
    is still the last one. A session found in a new turn, or whose last turn no longer says
    it, gets the wait for `finished` instead."""
    import time
    end = time.time() + wait
    while True:
        s = find_session(ident, mode, live_sessions())
        if s is None:
            return 0
        if now:
            now = False
            if not s["running"] and s.get("says_finished"):
                try:
                    return close_session(s, force=True)
                except ValueError:
                    return 1
        if not s["running"]:
            word = state_word(s)
            if word == "finished":
                try:
                    return close_session(s, force=True)
                except ValueError:
                    return 1
            if word != "idle":
                return 0
        # Still in its turn, or `idle`: the judge may not have read the last message yet.
        if time.time() >= end:
            return 0
        time.sleep(poll)


def cmd_sessions(args):
    import json
    why = getattr(args, "why", False)
    sessions = live_sessions(recheck=why)
    if getattr(args, "input", False):
        # Only the sessions that wait for the owner's answer.
        sessions = [s for s in sessions if state_word(s) == "input"]
    if getattr(args, "json", False):
        print(json.dumps(sessions, indent=2))
        return 0
    if not sessions:
        print("No launched session waits for input." if getattr(args, "input", False)
              else "No launched session has a terminal open.")
        return 0
    if why:
        # The owner's re-evaluation: each session, the state it got, and every signal read.
        for s in sessions:
            print("%s  %s%s" % (s["key"], state_text(s), "  - " + s["why"] if s.get("why")
                                 else ""))
            for name, value in s["signals"]:
                print("    %-16s %s" % (name, value))
        return 0
    print("%-8s %-7s %-9s %6s %6s %-10s %s" % (
        "TICKET", "MODE", "STATE", "SHELL", "CLAUDE", "WINDOW", "TITLE"))
    for s in sessions:
        print("%-8s %-7s %-9s %6s %6s %-10s %s" % (
            s["id"] or s["session"][:8], s["mode"], state_text(s, doing=False),
            s["shell"] or "-", s["claude"] or "-",
            ("pty" if s.get("own") else ("wt" if s["tabbed"] else "console")) if WINDOWS
            else ("tmux" if s.get("own") else "terminal"),
            (s.get("goal") or s.get("title") or "")
            + ("  | %s" % state_text(s) if s["running"] and s.get("doing") else "")))
    count = {w: sum(1 for s in sessions if state_word(s) == w) for w in SESSION_STATES}
    print("\n%d open, %d running, %d waiting for input, %d waiting for an event, "
          "%d working, %d waiting for a blocker, %d idle, %d finished"
          % (len(sessions), count["running"], count["input"], count["event"],
             count["subagent"], count["blocker"], count["idle"], count["finished"]))
    return 0


# The seven states of a session, as `sessions` and `screen` print them.
SESSION_STATES = ("running", "input", "event", "subagent", "blocker", "idle", "finished")


def state_word(s):
    """`running`, `input`, `event`, `subagent`, `blocker`, `idle` or `finished`."""
    return "running" if s["running"] else (s.get("wait") or "input")


def state_text(s, doing=True):
    """`state_word` as the owner reads it, `working` for `subagent`, and with `doing` what a
    running session does now: `running: Bash: tests`."""
    word = state_word(s)
    if word == "running" and doing and s.get("doing"):
        return "%s: %s" % (word, s["doing"])
    return "working" if word == "subagent" else word


def cmd_focus(args):
    s = find_session(args.id, getattr(args, "mode", None))
    if s is None:
        print("Error: %s has no open terminal" % args.id, file=sys.stderr)
        return 2
    if focus_session(s):
        print("%s  focused" % s["key"])
        return 0
    print("%s  the window did not come forward" % s["key"], file=sys.stderr)
    return 1


def cmd_screen(args):
    import json
    s = find_session(args.id, getattr(args, "mode", None))
    if s is None:
        print("Error: %s has no open terminal" % args.id, file=sys.stderr)
        return 2
    try:
        scr = screen_session(s)
    except OSError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 1
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if getattr(args, "json", False):
        print(json.dumps(scr, ensure_ascii=False))
        return 0
    lines = scr["lines"]
    while lines and not lines[-1]:
        lines.pop()
    print("%s  %dx%d  cursor %d,%d  %s" % (s["key"], scr["cols"], scr["rows"],
                                            scr["cursor"][0], scr["cursor"][1],
                                            state_word(s)))
    print("-" * min(scr["cols"], 120))
    print("\n".join(lines))
    return 0


def cmd_send(args):
    s = find_session(args.id, getattr(args, "mode", None))
    if s is None:
        print("Error: %s has no open terminal" % args.id, file=sys.stderr)
        return 2
    keys = [x.strip() for x in (args.keys or "").split(",") if x.strip()]
    if not args.text and not keys:
        print("Error: give --text, --keys, or both", file=sys.stderr)
        return 2
    try:
        n = send_session(s, args.text or "", keys)
    except (OSError, ValueError) as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 1
    print("%s  %d key records sent" % (s["key"], n))
    return 0


def cmd_close(args):
    mode = getattr(args, "mode", None)
    if getattr(args, "when_finished", False):
        # The detached closer that `--self` starts: its parent has exited, so the session
        # is no longer an ancestor of this process. `--now`: the `Stop` hook read a
        # believable `WORK IS FINISHED`.
        return close_when_finished(args.id, mode, now=getattr(args, "now", False))
    sessions = live_sessions()
    if getattr(args, "self", False):
        if not args.id:
            print("Error: --self needs the ticket of this session", file=sys.stderr)
            return 2
        s = find_session(args.id, mode, sessions)
        if s is None or not _is_own_session(s):
            print("Error: %s is not the session this command runs in" % args.id,
                  file=sys.stderr)
            return 2
        argv = [sys.executable, str(Path(__file__).resolve()), "--project", str(P.ROOT),
                "close", args.id, "--when-finished"]
        if s["mode"]:
            argv += ["--mode", s["mode"]]
        spawn_detached(argv)
        print("%s  closes when this turn ends" % s["key"])
        return 0
    if getattr(args, "finished", False):
        targets = finished_sessions(sessions)
    else:
        if not args.id:
            print("Error: name a ticket, or pass --finished", file=sys.stderr)
            return 2
        s = find_session(args.id, getattr(args, "mode", None), sessions)
        if s is None:
            print("Error: %s has no open terminal" % args.id, file=sys.stderr)
            return 2
        if s["running"] and not args.force:
            print("Error: %s is still running. Pass --force to end it." % s["key"],
                  file=sys.stderr)
            return 2
        targets = [s]
    failed = 0
    for s in targets:
        try:
            rc = close_session(s, force=args.force)
        except ValueError as exc:
            # A session cannot end the shell it runs in: `--self` closes it after the turn.
            hint = (". Run %s close %s --mode %s --self to close it when this turn ends"
                    % (P.cli, s["id"], s["mode"])) if s.get("id") and _is_own_session(s) else ""
            print("Error: %s%s" % (exc, hint), file=sys.stderr)
            failed += 1
            continue
        print("%s  %s" % (s["key"], "closed" if rc == 0 else "rc %d" % rc))
    if not targets:
        print("Nothing to close.")
    return 1 if failed else 0


# ── old tickets: the Board management job's review sweep ───────────────────
#
# A ticket that has waited in the start or the blocked lane for days may no longer be true.
# `review-old` selects them and launches `review` on each, a batch at a time: the review
# judges the ticket against the tree and leaves it, rewrites it, narrows it, or sends it on.

# Seconds a launched review has to appear in the process table before the sweep stops
# waiting for it.
REVIEW_APPEAR_WAIT = 90


def _date(text):
    """The date at the start of `YYYY-MM-DD[ HH:MM]`, or None."""
    import datetime
    try:
        return datetime.date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None


def old_tickets(tickets, days, sessions=()):
    """The tickets a review sweep takes, oldest first: in the start or the blocked lane,
    created more than `days` days ago, not repeatable, with no open session, and with no
    `review` launched within `days` days."""
    import datetime
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    live = {s["id"] for s in sessions if s.get("id")}
    out = []
    for t in tickets:
        created = _date(t.meta.get("created", ""))
        if t.state not in (lane("start"), lane("blocked")) or created is None:
            continue
        if created > cutoff or t.repeat or t.id in live:
            continue
        reviewed = [_date(e["launched"]) for e in t.sessions if e["mode"] == "review"]
        if any(d is not None and d > cutoff for d in reviewed):
            continue
        out.append(t)
    return sorted(out, key=lambda t: (t.meta.get("created", ""), t.id))


def _review_line(tid, why):
    t = find(load_all(), tid)
    if t is None:
        return "%s  gone  %s" % (tid, why)
    return "%s  %s / %s  %s  %s" % (t.id, P.STATE_LABEL.get(t.state, t.state), t.status,
                                    why, t.title)


def _await_reviews(ids, args):
    """Hold until every review of `ids` is finished, close each finished one, and return
    `{id: why}` for every ticket: `reviewed`, `no terminal`, or `still open` at the timeout."""
    import time
    started, pending, done = time.time(), set(ids), {}
    while pending:
        sessions = [s for s in live_sessions() if s["id"] in pending and s["mode"] == "review"]
        seen = {s["id"] for s in sessions}
        for s in sessions:
            if s.get("wait") == "finished":
                close_session(s)
                done[s["id"]] = "reviewed"
        elapsed = time.time() - started
        if elapsed > REVIEW_APPEAR_WAIT:
            for tid in pending - seen:
                done[tid] = "no terminal"
        pending -= set(done)
        if pending and args.timeout and elapsed > args.timeout:
            for tid in pending:
                done[tid] = "still open"
            break
        if pending:
            time.sleep(max(1, args.poll))
    return done


def cmd_review_old(args):
    """`list`: the tickets a review sweep takes. `run`: launch `review` on them a batch at a
    time, wait until each batch is finished, close its sessions, and print one line per
    ticket: where the review left it."""
    import contextlib
    picked = old_tickets(load_all(), args.days, live_sessions())
    if args.action == "list":
        for t in picked:
            print("%s  %s  %-10s  %s" % (t.id, t.meta.get("created", ""),
                                         P.STATE_LABEL.get(t.state, t.state), t.title))
        print("%d ticket(s) older than %d day(s)" % (len(picked), args.days))
        return 0
    if not picked:
        print("No ticket older than %d day(s) waits for a review" % args.days)
        return 0
    terminal = args.terminal or P.terminal
    rc, size = 0, max(1, args.batch)
    for i in range(0, len(picked), size):
        launched = []
        for t in picked[i:i + size]:
            a = argparse.Namespace(id=t.id, mode="review", note=args.note, model=args.model,
                                   effort=args.effort, no_fable=args.no_fable,
                                   terminal=terminal, dry_run=args.dry_run, auto=True)
            with contextlib.redirect_stdout(io.StringIO()):
                failed = cmd_work(a)
            if failed:
                rc = 1
                print("%s  not launched  %s" % (t.id, t.title))
            elif args.dry_run or terminal == "none":
                print("%s  would review  %s" % (t.id, t.title))
            else:
                launched.append(t.id)
        for tid, why in sorted(_await_reviews(launched, args).items()):
            if why != "reviewed":
                rc = 1
            print(_review_line(tid, why))
    return rc


def cmd_approve_readme(args):
    """Record the owner's approval of a project-wide README file for one session, so the README guard
    (`core/hooks/readme_guard.py`) lets that session write it. The session is `--session`,
    else the Claude session this command runs under."""
    import readme_guard
    words = _owner_words(args)
    if not words:
        print("Error: pass --owner-order \"<the order, in neutral words>\": only the owner approves a "
              "README change", file=sys.stderr)
        return 2
    sid = (args.session or os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if not sid:
        try:
            sid = own_session_id()
        except Exception:                               # noqa: BLE001 - no live record
            sid = ""
    try:
        norm = readme_guard.approve(sid, args.file, words)
    except ValueError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2
    print('approved %s for session %s: "%s"' % (norm, sid, " ".join(words.split())))
    return 0


def cmd_report(args):
    """A session says what it waits for: `input` keeps its terminal open wherever the
    ticket is and names the question in `--why`, `event` does too and names the outside event in `--why`, such as an editor
    restart, and `finished` lets `Close finished` close it."""
    import session_state
    t = find(load_all(), args.id)
    if t is None:
        print("Error: no ticket %r" % args.id, file=sys.stderr)
        return 2
    sid = ticket_session_id(t, args.mode)
    why = (getattr(args, "why", None) or "").strip()
    if args.word == "event" and not why:
        print("Error: `event` needs --why: the outside event the session waits for",
              file=sys.stderr)
        return 2
    if args.word == "input" and not why:
        print("Error: `input` needs --why: the question the session asks the owner, which "
              "the board shows", file=sys.stderr)
        return 2
    session_state.report(sid, session_state.owning_shell(), args.word, why)
    print("%s/%s  reported %s%s" % (t.id, args.mode, args.word, ": " + why if why else ""))
    return 0


def _blocked_cycles(tickets):
    """Every cycle in the blocked-by graph, each as a list of ids that closes on its first."""
    edges = {t.id: [b for b in t.blocked_by] for t in tickets}
    done, cycles = set(), []

    def visit(n, path):
        if n in path:
            cycles.append(path[path.index(n):] + [n])
            return
        if n in done or n not in edges:
            return
        for m in edges[n]:
            visit(m, path + [n])
        done.add(n)

    for n in sorted(edges):
        visit(n, [])
    return cycles


def _print_rows(tickets, ids, args):
    by = {t.id: t for t in tickets}
    for i in ids:
        t = by.get(i)
        if t is None:
            print("  %s  (missing)" % i)
        else:
            print("  %s %-12s %s" % (t.id, P.STATE_LABEL[t.state], t.title[:args.width]))


def cmd_artifact(args):
    """The artifacts of the project (`artifacts.py`): add, list, remove, found."""
    import artifacts
    if args.action == "list":
        for e in artifacts.load(P.ROOT):
            target = e.get("path") or e.get("url")
            gone = e.get("path") and artifacts.safe_path(P.ROOT, e["path"]) is None
            print("%-10s %-6s %s  %s%s" % (e.get("date") or "-", e.get("ticket") or "-", target,
                                           e.get("title") or "", "  [missing]" if gone else ""))
        return 0
    if args.action == "found":
        for f in artifacts.found(P.ROOT, P.cfg):
            print("%s  %s" % (f["path"], f["title"]))
        return 0
    if not args.target:
        print("Error: name a file of the project or an https:// URL", file=sys.stderr)
        return 2
    try:
        if args.action == "remove":
            if not artifacts.remove(P.ROOT, args.target):
                print("Error: no artifact %s" % args.target, file=sys.stderr)
                return 2
            print("removed %s" % args.target)
            return 0
        e = artifacts.add(P.ROOT, args.target, title=args.title,
                          description=args.description or "", ticket=args.ticket or "",
                          date=args.date)
    except ValueError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2
    print("%s %s  %s" % (e.get("date"), e.get("path") or e.get("url"), e.get("title")))
    return 0


def cmd_graph(args):
    """The connections of one ticket, or every group when no id is given."""
    tickets = load_all()
    if not args.id:
        groups = {}
        for t in tickets:
            groups.setdefault(t.group or "(none)", []).append(t.id)
        if args.json:
            import json
            print(json.dumps({g: sorted(v) for g, v in groups.items()}, indent=1))
            return 0
        for g in sorted(groups):
            if args.ids:
                print("%s %s" % (g, " ".join(sorted(groups[g]))))
            else:
                print("%-40s %d" % (g, len(groups[g])))
        return 0
    t = find(tickets, args.id)
    if t is None:
        print("Error: no ticket %r" % args.id, file=sys.stderr)
        return 2
    by = {x.id: x for x in tickets}
    blocks = sorted(x.id for x in tickets if t.id in x.blocked_by)
    members = sorted(x.id for x in tickets if t.group and x.group == t.group and x.id != t.id)
    # --hops N: every ticket reachable over blocked-by, blocks and related edges in N steps.
    reach, frontier = {t.id}, {t.id}
    for _ in range(max(args.hops, 1)):
        nxt = set()
        for i in frontier:
            x = by[i]
            nxt |= set(x.blocked_by) | set(x.related)
            nxt |= {y.id for y in tickets if i in y.blocked_by}
        frontier = nxt - reach
        reach |= frontier
    hops = sorted(reach - {t.id} - set(t.blocked_by) - set(blocks) - set(t.related))
    if args.json:
        import json
        print(json.dumps({"id": t.id, "group": t.group, "blockedBy": t.blocked_by,
                          "blocks": blocks, "related": t.related, "groupMembers": members,
                          "reach": hops}, indent=1))
        return 0
    if args.ids:
        print(" ".join(sorted(set(t.blocked_by) | set(blocks) | set(t.related) | set(members)
                              | set(hops))))
        return 0
    print("%s %-12s %s" % (t.id, P.STATE_LABEL[t.state], t.title[:args.width]))
    print("group: %s" % (t.group or "-"))
    for label, ids in (("blocked-by", t.blocked_by), ("blocks", blocks),
                       ("related", t.related), ("group members", members),
                       ("within %d hops" % args.hops, hops)):
        if not ids:
            continue
        print("%s:" % label)
        _print_rows(tickets, ids, args)
    return 0


def _set_list(meta, key, values):
    if values:
        meta[key] = ", ".join(sorted(values))
    else:
        meta.pop(key, None)


def _edit_edges(args, add):
    """Shared body of link and unlink. add=True writes edges, add=False removes them."""
    tickets = load_all()
    t = find(tickets, args.id)
    if t is None:
        print("Error: no ticket %r" % args.id, file=sys.stderr)
        return 2
    by = {x.id: x for x in tickets}

    def resolve(ids):
        out = []
        for i in ids or []:
            o = find(tickets, i)
            if o is None:
                print("Error: no ticket %r" % i, file=sys.stderr)
                raise SystemExit(2)
            if o.id == t.id:
                print("Error: %s cannot relate to itself" % t.id, file=sys.stderr)
                raise SystemExit(2)
            out.append(o)
        return out

    related, blockers = resolve(args.related), resolve(args.blocked_by)
    touched = {t.id}
    for o in related:
        a, b = set(t.related), set(o.related)
        (a.add if add else a.discard)(o.id)
        (b.add if add else b.discard)(t.id)
        _set_list(t.meta, "related", a)
        _set_list(o.meta, "related", b)
        touched.add(o.id)
    if blockers:
        a = set(t.blocked_by)
        for o in blockers:
            (a.add if add else a.discard)(o.id)
        _set_list(t.meta, "blocked-by", a)
    if add and args.group is not None:
        t.meta["group"] = args.group
    if not add and args.group:
        t.meta.pop("group", None)
    if add and t.blocked_by:
        for cycle in _blocked_cycles(tickets):
            print("Error: blocked-by cycle: %s" % " -> ".join(cycle), file=sys.stderr)
            return 2
    for i in sorted(touched):
        x = by[i]
        _write(x.path, render(x.meta, x.body))
    print("%s  group=%s  blocked-by=%s  related=%s"
          % (t.id, t.group or "-", ",".join(t.blocked_by) or "-", ",".join(t.related) or "-"))
    write_index(quiet=True)
    return 0


def cmd_link(args):
    return _edit_edges(args, add=True)


def cmd_unlink(args):
    return _edit_edges(args, add=False)


def cmd_size(args):
    """Set the difficulty on one or more tickets. Sizing needs a read, so it is a command."""
    if args.difficulty not in DIFFICULTIES:
        print("Error: %r is not a difficulty. One of: %s"
              % (args.difficulty, ", ".join(DIFFICULTIES)), file=sys.stderr)
        return 2
    tickets = load_all()
    for ident in args.ids:
        t = find(tickets, ident)
        if t is None:
            print("Error: no ticket %r" % ident, file=sys.stderr)
            return 2
        was = t.difficulty
        t.meta["difficulty"] = args.difficulty
        _write(t.path, render(t.meta, t.body))
        print("%s  %s -> %s" % (t.id, was, args.difficulty))
    write_index(quiet=True)
    return 0


def cmd_outcome(args):
    """Write the outcome line of a ticket: what now works or what was found."""
    t = find(load_all(), args.id)
    if t is None:
        print("Error: no ticket %r" % args.id, file=sys.stderr)
        return 2
    why = set_outcome(t, args.text)
    if why:
        print("Error: %s: %s." % (t.id, why), file=sys.stderr)
        return 2
    print("%s  outcome: %s" % (t.id, t.meta[OUTCOME_FIELD]))
    return 0


def cmd_difficulties(args):
    counts = {}
    for t in load_all():
        counts[t.difficulty] = counts.get(t.difficulty, 0) + 1
    for d in DIFFICULTIES:
        print("  %-8s %4d   %s" % (d, counts.get(d, 0), P.DIFF_MEANING[d]))
    print("")
    print("Priority is derived: severity first, then difficulty ascending.")
    return 0


def permit_counts(tickets=None):
    """permit -> how many tickets grant it, for every permit of the project, and any other
    name a ticket's `permits:` line carries."""
    counts = {name: 0 for name in P.PERMITS}
    for t in load_all() if tickets is None else tickets:
        for p in t.permits:
            counts[p] = counts.get(p, 0) + 1
    return counts


def cmd_permits(args):
    counts = permit_counts()
    print("%-9s %5s   %s" % ("permit", "used", "grants"))
    for name in sorted(P.PERMITS):
        print("%-9s %5d   %s" % (name, counts.get(name, 0), P.PERMITS[name]))
    print("")
    print("Everything else inside the project is granted to every session and is not a")
    print("permit. `work` turns an absent permit into a --disallowedTools entry.")
    for name in sorted(P.PERMITS):
        spec = P.RULES["permits"][name]
        if spec.get("addDirEnv") and not P.add_dir(name):
            print("`%s` adds no directory until %s names a path." % (name, spec["addDirEnv"]))
        if spec.get("addDirRegistry"):
            roots = [d for d in P.add_dirs(name) if d != P.add_dir(name)]
            print("`%s` adds the root of every other registered project:%s"
                  % (name, "".join("\n  %s" % d for d in roots) or " none registered"))
    return 0


def cmd_rulesets(args):
    """The active rulesets in order, and what each one adds."""
    rel = "%s/%s" % (P.config_dir, _project.PROJECT_FILE)
    print("%s (%s)   %s" % (P.name, P.slug, rel))
    r = P.RULES
    print("  models   %s   default %s" % (", ".join("%s=%s" % kv for kv in r["models"].items()),
                                         r["defaultModel"] or "-"))
    print("  efforts  %s   default %s" % (", ".join(r["efforts"]), r["defaultEffort"] or "-"))
    for name in r["rulesets"]:
        added = r["contributions"][name]
        print("")
        print("%s   %s" % (name, added.get("title", "")))
        for k in _project.RULESET_KEYS:
            if k == "title" or k not in added:
                continue
            v = added[k]
            print("  %-12s %s" % (k, ", ".join(v) if isinstance(v, list) else
                                  str(v).lower() if isinstance(v, bool) else v))
    if P.problems:
        print("")
        for p in P.problems:
            print("  %s" % p)
        return 1
    return 0


def _today():
    import datetime
    return datetime.date.today().isoformat()


def _take_project(argv):
    """The value of `--project` anywhere in argv, and argv without it."""
    out, found, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if a == "--project" and i + 1 < len(argv):
            found, i = argv[i + 1], i + 2
            continue
        if a.startswith("--project="):
            found, i = a.split("=", 1)[1], i + 1
            continue
        out.append(a)
        i += 1
    return found, out


def main(argv=None):
    """The CLI. `--project <name|path>` names the project; without it, the nearest
    `.octopus/project.json` above the working directory."""
    argv = list(sys.argv[1:] if argv is None else argv)
    ident, argv = _take_project(argv)
    token = _project.use(_project.resolve(ident))
    try:
        return _main(argv)
    finally:
        _project.reset(token)


def _main(argv):
    ap = argparse.ArgumentParser(prog="todo", description="The ticket tree of %s." % P.name)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="rebuild TODO/INDEX.md")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("check", help="exit 1 when the index is stale or a ticket is malformed")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("move", help="move tickets, or a whole epic, to another state")
    p.add_argument("ids", nargs="*", help="ticket ids; omit when using --epic")
    p.add_argument("state", help="one of: " + ", ".join(P.STATES))
    p.add_argument("--epic", help="move this epic directory whole")
    p.add_argument("--status", help="the sub-state to land on; must belong to the target "
                                    "state. Default: that state's first status")
    p.add_argument("--verified", action="store_true",
                   help="close it: the measurement exists and the ticket names it. Only a "
                        "verification session passes this")
    p.add_argument("--owner-order", dest="owner_order", nargs="?", const=True,
                   metavar="WORDS",
                   help="the owner ordered this move in this session's chat. With the "
                        "order, stated in neutral words, it may also close the ticket as "
                        "`cancelled` or `closed by other task`, and the statement goes on the "
                        "ticket")
    p.add_argument("--outcome", help="the outcome line, written on the ticket before the "
                   "move: what now works or what was found, at most %d words" % OUTCOME_WORDS)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_move)

    p = sub.add_parser("outcome", help="write the outcome line of a ticket: what now works or "
                                       "what was found, at most %d words" % OUTCOME_WORDS)
    p.add_argument("id")
    p.add_argument("text")
    p.set_defaults(fn=cmd_outcome)

    p = sub.add_parser("new", help="create a ticket")
    p.add_argument("--title", required=True,
                   help='"Bug: <what is wrong>" or "Feature: <what the program should do>"')
    p.add_argument("--severity", choices=list(SEVERITIES))
    p.add_argument("--difficulty", choices=list(DIFFICULTIES))
    p.add_argument("--category", help="one of: " + ", ".join(P.CATEGORIES) +
                   "; absent derives it from --source")
    p.add_argument("--source")
    p.add_argument("--description", help="the text of the first section: What is wrong, "
                   "or What it should do for a Feature: title")
    p.add_argument("--evidence")
    p.add_argument("--state", default=lane("start"), choices=list(P.STATES))
    p.add_argument("--status")
    p.add_argument("--epic", default="")
    p.add_argument("--claim")
    p.add_argument("--blocked-by", dest="blocked_by")
    p.add_argument("--permits", help="comma-separated; one of: " + ", ".join(sorted(P.PERMITS)))
    p.add_argument("--tags")
    p.add_argument("--created")
    p.add_argument("--repeat", choices=list(REPEAT_ROLES),
                   help="a repeatable ticket: returns to the start lane after each verified run")
    p.add_argument("--parent", help="the ticket this one is a part of")
    p.add_argument("--group", help="the topic group (files store)")
    p.add_argument("--owner-order", dest="owner_order", nargs="?", const=True,
                   metavar="WORDS",
                   help="the owner ordered, in this session's chat, to file this ticket as "
                        "`blocked by user`; the order in neutral words is optional")
    p.add_argument("--launch", action="store_true",
                   help="launch its session at once: `plan` when its difficulty is unknown, "
                        "`work` otherwise")
    p.add_argument("--note", help="with --launch: what the launched session should know")
    p.add_argument("--terminal", default=None, choices=["cmd", "wt", "own", "none"],
                   help="with --launch: none prints the command instead of launching")
    p.add_argument("--dry-run", action="store_true",
                   help="with --launch: print the launch instead of starting it")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("chat", help="open a terminal with a read-only session that answers "
                                    "questions about the codebase and edits nothing")
    p.add_argument("--provider", choices=list(_project.PROVIDERS), help="the project's default when omitted")
    p.add_argument("--model", help="a key of `models`; absent is the project's default")
    p.add_argument("--effort", help="one of `efforts`; absent is the project's default")
    p.add_argument("--terminal", default=None, choices=["wt", "cmd", "none"])
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_chat)

    p = sub.add_parser("migrate", help="rewrite what an older engine left: `kind` becomes "
                                       "`category`")
    p.set_defaults(fn=cmd_migrate)

    p = sub.add_parser("restart", help="start the next run of a repeatable ticket")
    p.add_argument("id")
    p.add_argument("--no-launch", dest="no_launch", action="store_true",
                   help="return it to Pipeline and start no session")
    p.add_argument("--note", help="what you want this run's session to know")
    p.add_argument("--model", help="a model of the provider the launch starts; absent is its default")
    p.add_argument("--effort", help="override the effort; one of the provider's efforts")
    p.add_argument("--no-fable", action="store_true",
                   help="Fable is not available: run Opus at max effort instead")
    p.add_argument("--terminal", default=None, choices=["cmd", "wt", "own", "none"])
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_restart)

    p = sub.add_parser("repeat", help="make tickets repeatable, or ordinary with `none`")
    p.add_argument("ids", nargs="+")
    p.add_argument("role", help="one of: " + ", ".join(REPEAT_ROLES) + ", none")
    p.set_defaults(fn=cmd_repeat)

    p = sub.add_parser("repeatable", help="every repeatable ticket, its run and last result")
    p.set_defaults(fn=cmd_repeatable)

    p = sub.add_parser("watchdog", help="every manager ticket of the Octopus AI project, alive "
                                        "or dead; --once starts the dead ones again")
    p.add_argument("--once", action="store_true", help="relaunch every dead manager session")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--terminal", default=None, choices=["cmd", "wt", "own"])
    p.set_defaults(fn=cmd_watchdog)

    p = sub.add_parser("manager", help="the Octopus AI project's manager ticket, from any "
                                       "project, filed from the template when that project has "
                                       "none; --start launches it unless it runs")
    p.add_argument("--start", action="store_true", help="launch its work session unless it runs")
    p.add_argument("--claim", action="store_true",
                   help="make the Claude session this runs in the manager: refused while a "
                        "manager runs in any registered project")
    p.add_argument("--take-over", action="store_true",
                   help="with --claim: close the manager that runs, and claim")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--terminal", default=None, choices=["cmd", "wt", "own"])
    p.set_defaults(fn=cmd_manager)

    p = sub.add_parser("remote-control", help="Remote Control of tickets' sessions: on, off, "
                                              "or the project's default")
    p.add_argument("ids", nargs="+", metavar="id")
    p.add_argument("value", choices=list(RC_VALUES) + ["default"])
    p.set_defaults(fn=cmd_remote_control)

    p = sub.add_parser("epic", help="create an epic directory")
    p.add_argument("name")
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--state", default=lane("start"), choices=list(P.STATES))
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_epic)

    p = sub.add_parser("list", help="filter and search")
    p.add_argument("query", nargs="*")
    p.add_argument("--state", choices=list(P.STATES))
    p.add_argument("--status")
    p.add_argument("--permit")
    p.add_argument("--category", choices=list(P.CATEGORIES))
    p.add_argument("--severity")
    p.add_argument("--difficulty")
    p.add_argument("--unknown", action="store_true",
                   help="only the tickets nobody can size yet")
    p.add_argument("--epic")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("statuses", help="the status subset of every state, and what each means")
    p.set_defaults(fn=cmd_statuses)

    p = sub.add_parser("permits", help="the permit vocabulary, and how many tickets grant each")
    p.set_defaults(fn=cmd_permits)

    p = sub.add_parser("size", help="set difficulty on tickets")
    p.add_argument("ids", nargs="+")
    p.add_argument("difficulty", choices=list(DIFFICULTIES))
    p.set_defaults(fn=cmd_size)

    p = sub.add_parser("difficulties", help="the difficulty vocabulary and how many carry each")
    p.set_defaults(fn=cmd_difficulties)

    p = sub.add_parser("brief", help="write TODO/.briefs/<id>.md without launching anything")
    p.add_argument("id")
    p.add_argument("--mode", default="work", choices=list(MODES))
    p.add_argument("--note")
    p.set_defaults(fn=cmd_brief)

    p = sub.add_parser("work", help="launch a Claude session on this ticket: plan, then execute")
    p.add_argument("id")
    p.add_argument("--model", help="a model of the provider the launch starts; absent is its default")
    p.add_argument("--effort", help="override the effort; one of the provider's efforts")
    p.add_argument("--no-fable", action="store_true",
                   help="Fable is not available: run Opus at max effort instead")
    p.add_argument("--terminal", default=None, choices=["cmd", "wt", "own", "none"],
                   help="none prints the command instead of launching")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--note", help="the owner's order to this session, in neutral words")
    p.add_argument("--provider", "--agent", dest="provider", choices=list(_project.PROVIDERS),
                   help="the AI provider to start; absent is the project's default. "
                        "--agent is its old name")
    p.set_defaults(fn=cmd_work, mode="work")

    p = sub.add_parser("plan", help="launch a Claude session that only produces the plan")
    p.add_argument("id")
    p.add_argument("--model", help="a model of the provider the launch starts; absent is its default")
    p.add_argument("--effort", help="override the effort; one of the provider's efforts")
    p.add_argument("--no-fable", action="store_true",
                   help="Fable is not available: run Opus at max effort instead")
    p.add_argument("--terminal", default=None, choices=["cmd", "wt", "own", "none"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--note")
    p.add_argument("--provider", "--agent", dest="provider", choices=list(_project.PROVIDERS),
                   help="the AI provider to start; absent is the project's default. "
                        "--agent is its old name")
    p.set_defaults(fn=cmd_work, mode="plan")

    p = sub.add_parser("review", help="a session that re-evaluates the ticket, not the code")
    p.add_argument("id")
    p.add_argument("--note", help="state explicitly what to reconsider")
    p.add_argument("--model", help="a model of the provider the launch starts; absent is its default")
    p.add_argument("--effort", help="override the effort; one of the provider's efforts")
    p.add_argument("--no-fable", action="store_true",
                   help="Fable is not available: run Opus at max effort instead")
    p.add_argument("--terminal", default=None, choices=["cmd", "wt", "own", "none"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--provider", "--agent", dest="provider", choices=list(_project.PROVIDERS),
                   help="the AI provider to start; absent is the project's default. "
                        "--agent is its old name")
    p.set_defaults(fn=cmd_work, mode="review")

    p = sub.add_parser("verify", help="a session that proves a Verification ticket, and the "
                                      "only one that may close it")
    p.add_argument("id")
    p.add_argument("--note", help="what the owner wants proved, in neutral words")
    p.add_argument("--model", help="a model of the provider the launch starts; absent is its default")
    p.add_argument("--effort", help="override the effort; one of the provider's efforts")
    p.add_argument("--no-fable", action="store_true",
                   help="Fable is not available: run Opus at max effort instead")
    p.add_argument("--terminal", default=None, choices=["cmd", "wt", "own", "none"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--provider", "--agent", dest="provider", choices=list(_project.PROVIDERS),
                   help="the AI provider to start; absent is the project's default. "
                        "--agent is its old name")
    p.set_defaults(fn=cmd_work, mode="verify")

    p = sub.add_parser("query", help="one call for a question asked in chat")
    p.add_argument("query", nargs="*", help="words that must all appear")
    p.add_argument("--state", action="append", choices=list(P.STATES))
    p.add_argument("--status", action="append")
    p.add_argument("--severity", action="append")
    p.add_argument("--difficulty", action="append", choices=list(DIFFICULTIES))
    p.add_argument("--category", action="append", choices=list(P.CATEGORIES))
    p.add_argument("--permit", action="append", choices=sorted(P.PERMITS))
    p.add_argument("--tag", action="append")
    p.add_argument("--not-tag", dest="not_tag", action="append",
                   help="drop tickets carrying this tag")
    p.add_argument("--epic")
    p.add_argument("--source", help="substring of the Source field")
    p.add_argument("--claimed", action="store_true")
    p.add_argument("--unclaimed", action="store_true")
    p.add_argument("--blocked", action="store_true", help="only those naming a blocker")
    p.add_argument("--startable", action="store_true",
                   help="in Pipeline and sized, so the work can begin")
    p.add_argument("--group", help="tickets in this group")
    p.add_argument("--parent", help="only the children of this ticket")
    p.add_argument("--parents", action="store_true", help="only tickets that have children")
    p.add_argument("--roots", action="store_true", help="only tickets with no parent")
    p.add_argument("--related-to", dest="related_to", metavar="ID",
                   help="tickets one edge away from ID: related, blocked-by, or blocking it")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--width", type=int, default=86)
    p.add_argument("--count", action="store_true", help="print the number only")
    p.add_argument("--ids", action="store_true", help="print the ids only, space separated")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_query)

    p = sub.add_parser("show", help="print one ticket verbatim")
    p.add_argument("id")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("graph", help="the connections of one ticket; no id lists the groups")
    p.add_argument("id", nargs="?")
    p.add_argument("--hops", type=int, default=1, help="follow edges this many steps")
    p.add_argument("--width", type=int, default=70)
    p.add_argument("--ids", action="store_true", help="print the ids only, space separated")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_graph)

    p = sub.add_parser("artifact", help="the pages the board's Artifacts view lists: add, list, "
                                        "remove, found")
    p.add_argument("action", choices=("add", "list", "remove", "found"))
    p.add_argument("target", nargs="?", help="add, remove: a file of the project, or an "
                                             "https:// URL")
    p.add_argument("--title", help="default: the page's <title>, else its file name")
    p.add_argument("--description", help="one line: what the page shows")
    p.add_argument("--ticket", help="the ticket that made the page")
    p.add_argument("--date", help="YYYY-MM-DD; default: today")
    p.set_defaults(fn=cmd_artifact)

    p = sub.add_parser("link", help="add related, blocked-by or group to a ticket")
    p.add_argument("id")
    p.add_argument("--related", nargs="*", metavar="ID", help="written on both tickets")
    p.add_argument("--blocked-by", dest="blocked_by", nargs="*", metavar="ID")
    p.add_argument("--group", help="the group slug")
    p.set_defaults(fn=cmd_link)

    p = sub.add_parser("unlink", help="remove related, blocked-by or group from a ticket")
    p.add_argument("id")
    p.add_argument("--related", nargs="*", metavar="ID", help="removed from both tickets")
    p.add_argument("--blocked-by", dest="blocked_by", nargs="*", metavar="ID")
    p.add_argument("--group", action="store_true", help="clear the group")
    p.set_defaults(fn=cmd_unlink)

    p = sub.add_parser("groups", help="rebuild TODO/GROUPS.md; list open tickets in no group")
    p.add_argument("--set", nargs="+", metavar=("NAME", "ITEM"),
                   help="put fine group slugs or ticket ids into a Todo group")
    p.add_argument("--table", action="store_true", help="print every Todo group as a table")
    p.add_argument("--json", action="store_true", help="the groups as data")
    p.add_argument("--model", help="a model of the provider the launch starts; absent is its default")
    p.add_argument("--effort", help="one of the provider's efforts; absent is its default")
    p.add_argument("--run", metavar="NAME",
                   help="launch a session on every Pipeline ticket of a Todo group")
    p.add_argument("--note", help="the owner's order to every launched session, in neutral "
                   "words")
    p.add_argument("--no-fable", action="store_true",
                   help="Fable is not available: large and unknown run Opus at max effort")
    p.add_argument("--terminal", default=None, choices=["cmd", "wt", "own", "none"])
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_groups)

    p = sub.add_parser("group", help="create a group, retitle, rename or delete one")
    p.add_argument("slug")
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--rename", metavar="NEW")
    p.add_argument("--delete", action="store_true")
    p.add_argument("--into", help="with --delete: the group the open tickets move to")
    p.set_defaults(fn=cmd_group)

    p = sub.add_parser("regroup", help="put tickets in a group")
    p.add_argument("ids", nargs="+")
    p.add_argument("slug")
    p.set_defaults(fn=cmd_regroup)

    p = sub.add_parser("children", help="a parent's children and where they stand; exit 1 "
                                        "while one is open")
    p.add_argument("id")
    p.set_defaults(fn=cmd_children)

    p = sub.add_parser("wait", help="block until every child of the ticket is in the verify "
                                    "or the closed lane")
    p.add_argument("id")
    p.add_argument("--interval", type=int, default=30, help="seconds between looks")
    p.add_argument("--timeout", type=int, default=0, help="seconds; 0 waits for ever")
    p.set_defaults(fn=cmd_wait)

    p = sub.add_parser("rulesets", help="the project's active rulesets in order, and what each "
                                        "adds")
    p.set_defaults(fn=cmd_rulesets)

    p = sub.add_parser("sessions", help="every session that still has a terminal")
    p.add_argument("--json", action="store_true")
    p.add_argument("--why", action="store_true",
                   help="re-evaluate every session and print each signal its state comes from")
    p.add_argument("--input", action="store_true",
                   help="only the sessions that wait for the owner's input")
    p.set_defaults(fn=cmd_sessions)

    p = sub.add_parser("focus", help="bring a session's terminal to the front")
    p.add_argument("id", help="a ticket id, or the first 8+ characters of a session id")
    p.add_argument("--mode", choices=list(MODES) + ["cli"])
    p.set_defaults(fn=cmd_focus)

    p = sub.add_parser("screen", help="print the screen of a session's terminal")
    p.add_argument("id", help="a ticket id, or the first 8+ characters of a session id")
    p.add_argument("--mode", choices=list(MODES) + ["cli"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_screen)

    p = sub.add_parser("send", help="type text and keys into a session's terminal")
    p.add_argument("id", help="a ticket id, or the first 8+ characters of a session id")
    p.add_argument("--mode", choices=list(MODES) + ["cli"])
    p.add_argument("--text", default="", help="characters to type")
    p.add_argument("--keys", default="",
                   help="comma-separated key names after the text: Enter, Esc, Tab, "
                        "Shift+Tab, Backspace, Delete, Up, Down, Left, Right, Home, End, "
                        "PageUp, PageDown, Ctrl+<letter> (Ctrl+C, Ctrl+R, Ctrl+U), "
                        "Alt+<letter>, and Ctrl+/Alt+/Shift+ a named key (Ctrl+Left, "
                        "Alt+Enter)")
    p.set_defaults(fn=cmd_send)

    p = sub.add_parser("close", help="close a session's terminal")
    p.add_argument("id", nargs="?", help="a ticket id, or the first 8+ characters of a session id")
    p.add_argument("--mode", choices=list(MODES) + ["cli"])
    p.add_argument("--finished", action="store_true",
                   help="close every terminal whose session is finished: its Claude has "
                        "exited, or its ticket left the lane it was launched on and it "
                        "asked no question")
    p.add_argument("--force", action="store_true",
                   help="end a session that is still running")
    p.add_argument("--self", action="store_true",
                   help="a session's last command: close its own terminal once its turn "
                        "ends, and only when the session is finished")
    p.add_argument("--when-finished", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--now", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_close)

    p = sub.add_parser("review-old", help="the tickets older than --days in the start and "
                                          "the blocked lane; `run` reviews them in batches")
    p.add_argument("action", choices=["list", "run"])
    p.add_argument("--days", type=int, default=5,
                   help="a ticket created more than this many days ago, and not reviewed "
                        "within them")
    p.add_argument("--batch", type=int, default=10, help="reviews open at one time")
    p.add_argument("--poll", type=int, default=20, help="seconds between looks at a batch")
    p.add_argument("--timeout", type=int, default=3600,
                   help="seconds a batch may take; 0 waits for ever")
    p.add_argument("--note", help="what every review should reconsider")
    p.add_argument("--model", help="a model of the provider the launch starts; absent is its default")
    p.add_argument("--effort", help="override the effort; one of the provider's efforts")
    p.add_argument("--no-fable", action="store_true",
                   help="Fable is not available: run Opus at max effort instead")
    p.add_argument("--terminal", default=None, choices=["cmd", "wt", "own", "none"])
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_review_old)

    p = sub.add_parser("report", help="a session says what it waits for: the owner's "
                                      "answer, an outside event, or nothing")
    p.add_argument("id", help="the ticket the session works")
    p.add_argument("word", choices=["input", "event", "finished"])
    p.add_argument("--why", default="",
                   help="what the session waits for, required: the question it asks with "
                        "`input`, the outside event with `event`")
    p.add_argument("--mode", choices=list(MODES), default="work")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("approve-readme", help="the owner approved the exact new text of a "
                                              "project-wide README file: let this session "
                                              "write it")
    p.add_argument("file", help="the project-wide README file, absolute or from the working directory")
    p.add_argument("--session", help="the session the approval is for: the id the README "
                                     "guard's refusal names. Default: this Claude session")
    p.add_argument("--owner-order", dest="owner_order", nargs="?", const=True, metavar="WORDS",
                   help="the owner's approval of the change, stated in neutral words; "
                        "required")
    p.set_defaults(fn=cmd_approve_readme)

    p = sub.add_parser("board", help="the five states as lanes")
    p.add_argument("--width", type=int, default=30)
    p.add_argument("--limit", type=int, default=12)
    p.set_defaults(fn=cmd_board)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
