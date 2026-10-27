"""oldnames.py - find the names of the toolkit that must not appear, where a person or an agent
reads them.

    hits(text, aliases)               [(line number, spelling)] in one text
    allowed(rel, globs)               whether a project path matches one of the globs
    tracked(root)                     the project paths git tracks
    scan_tickets(tickets, root, ...)  [(path, line, spelling)] in the open tickets
    scan_files(root, globs, aliases)  [(path, line, spelling)] in the files outside TODO/

The toolkit is called Octopus AI. A spelling followed by a letter, a digit or `-` is part of
another name and is no hit.

ONLY WHAT GIT TRACKS is read. A ticket, a config or any other file that git does not track,
or that it ignores, is local and is never a hit; a project outside git has none.

TWO RULESET LISTS say where a spelling stays:

    oldNameAllowed   path globs, relative to the project root, of files that keep it on purpose
    oldNameAliases   literal strings that are no hit wherever they stand

`todo.py check` reads the open tickets: every lane but the closed one, because a closed ticket
is history. `octopus.py status <path>` reads the open tickets and every text file outside
`TODO/`.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import vcs as _vcs

# Names that must not appear.
PATTERN = re.compile(r"(?:AI ?Tools|aitools|AITOOLS|Aitools)(?![A-Za-z0-9-])")
# A ticket's file name carries a slug of its title, where words are joined by `-`.
SLUG = re.compile(r"(?:^|-)(aitools|ai-tools)(?=-|\.md$)", re.I)
NEW_NAME = "Octopus AI"

# Directories that hold build output, caches or binary assets, never text a person writes.
PRUNE = {".git", ".svn", ".vs", ".idea", ".diversion", "node_modules", "__pycache__",
         "Binaries", "Intermediate", "Saved", "DerivedDataCache", "Content"}
MAX_BYTES = 2_000_000


def hits(text, aliases=()):
    """[(line number, spelling)] of every old spelling in `text`, outside the aliases."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if not PATTERN.search(line):
            continue
        for a in aliases:
            line = line.replace(a, "")
        out += [(n, m.group(0)) for m in PATTERN.finditer(line)]
    return out


def allowed(rel, globs):
    """Whether the project path `rel` matches one of `globs`. `**` spans directories; a glob
    with no `/` matches the file name in any directory."""
    rel = rel.replace("\\", "/")
    for g in globs:
        if "/" not in g:
            if fnmatch.fnmatchcase(rel.rsplit("/", 1)[-1], g):
                return True
        elif fnmatch.fnmatchcase(rel, g) or (g.endswith("/**") and rel.startswith(g[:-2])):
            return True
    return False


def tracked(root):
    """The paths under `root` that git tracks, relative to `root`, with `/`. An empty set when
    `root` is in no git repository."""
    st = _vcs._git(root, "-c", "core.quotepath=off", "ls-files", "-z")
    if st.rc != 0:
        return set()
    return {p for p in st.out.split("\0") if p}


def _read_text(p):
    try:
        if p.stat().st_size > MAX_BYTES:
            return None
        data = p.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", "replace")


def scan_tickets(tickets, root, globs=(), aliases=(), files=None):
    """[(project path, line, spelling)] in each ticket of `tickets` that git tracks and `globs`
    do not allow, line 0 for the slug in its file name. The caller passes the open tickets;
    `files` is `tracked(root)` when the caller has it."""
    root = Path(root)
    files = tracked(root) if files is None else files
    out = []
    for t in tickets:
        rel = t.path.relative_to(root).as_posix()
        if rel not in files or allowed(rel, globs):
            continue
        m = SLUG.search(t.path.name)
        if m:
            out.append((rel, 0, m.group(1)))
        text = _read_text(t.path) or ""
        out += [(rel, n, s) for n, s in hits(text, aliases)]
    return out


def scan_files(root, globs=(), aliases=(), skip=("TODO",), files=None):
    """[(project path, line, spelling)] in every text file under `root` that git tracks, but
    the top-level directories in `skip`, the build output in PRUNE and the paths `globs`
    allow."""
    root = Path(root)
    files = tracked(root) if files is None else files
    out = []
    for rel in sorted(files):
        parts = rel.split("/")
        if parts[0] in skip or PRUNE.intersection(parts[:-1]) or allowed(rel, globs):
            continue
        text = _read_text(root / rel)
        if text and PATTERN.search(text):
            out += [(rel, n, s) for n, s in hits(text, aliases)]
    return out


def scan_project(root):
    """[(project path, line, spelling)] in the open tickets and the files outside the ticket
    tree of the project at `root`, by the project's own rulesets."""
    import project as _project
    p = _project.load(Path(root))
    globs, aliases = p.RULES["oldNameAllowed"], p.RULES["oldNameAliases"]
    closed = p.lane("closed")
    tickets = [_Doc(f) for lane in p.STATES if lane != closed and (p.TODO / lane).is_dir()
               for f in sorted((p.TODO / lane).rglob("*.md")) if f.name != "_epic.md"]
    files = tracked(p.ROOT)
    return (scan_tickets(tickets, p.ROOT, globs, aliases, files)
            + scan_files(p.ROOT, globs, aliases, skip=(p.TODO.name,), files=files))


class _Doc:
    def __init__(self, path):
        self.path = path


def report(found, out=print):
    """One line per file: its path, the lines and the spellings. Line 0 is a ticket's file
    name. Returns the number of files."""
    by_file = {}
    for rel, n, s in found:
        by_file.setdefault(rel, []).append((n, s))
    for rel, hs in by_file.items():
        lines = sorted({n for n, _ in hs})
        where = [("file name" if n == 0 else "line %d" % n) for n in lines[:5]]
        out("%s names the toolkit %s (%s%s); it is %s"
            % (rel, ", ".join(sorted({s for _, s in hs})), ", ".join(where),
               ", ..." if len(lines) > 5 else "", NEW_NAME))
    return len(by_file)
