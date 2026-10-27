"""admin.py - put Octopus AI into a project, take it out again, and change a project's config.

    octopus new <path> --name <name> --profile <profile>      create the directory, then install
    octopus install <path> --profile <profile>                install into a project that exists
    octopus update <path> | --all                             write every changed payload file again
    octopus uninstall <path> [--pack <name>]                  remove what install wrote
    octopus status <path>                                     each installed file: same, edited, missing
                                                              (octopus.py adds the old-name scan)
    octopus packs <path> [list | add <pack> | remove <pack> | set <pack,pack,...>]
    octopus rules [<rule> on|off|default] [--project <name>]  the optional rules, global or of one
                                                              project
    octopus pack-registry [list | add <folder|url> | update <name> | remove <name>]  (packreg.py)
    octopus skills <path> [list | libraries | available | add | update | remove]   (skills.py)
    settings(root) / set_settings(root, **changes)            read and change project.json
    octopus lanes <path> [list | add | rename | remove] ...
    octopus projects [list | add <path> | remove <path>]
    octopus relocate <new path> [--wait] [--no-link]           move this checkout

WHAT INSTALL WRITES, and records in `<project>/.octopus/installed.json` with each file's hash:

    .octopus/project.json     from core/templates/project.json, with the profile's rulesets
    .octopus/todo.py          the per-project shim: `python .octopus/todo.py <command>`
    .claude/settings.json     the hook lines (`octopus hook`) and one env variable
    .codex/hooks.json         the status hook lines of Codex (`octopus status-hook`), through
                              `agents.write_entries`
    the payload of every ruleset the project lists: skills, rules, templates

The registry (`projects.json`, outside the Octopus AI tree) gets the project.

UPDATE is install with no profile: every payload file a pack changed is written again. A payload
entry with `replace` belongs to its pack: its copy replaces a hand copy, and `refresh`, which the
`SessionStart` hook runs, brings it up to date in every project a session starts in.

AN OPTIONAL RULE (`optionalRules` of a ruleset) is copied only while it is on. Turning it off
removes the copy install wrote, unless it was edited since; `set_rules` does both and writes
the entry files again, so `AGENTS.md` and `CLAUDE.md` list only the rules that are on.
`set_global_rules` switches a rule for every project: it does the same in each registered
project that lists the rule's ruleset.

UNINSTALL removes exactly what install recorded. It refuses a file whose hash changed since
install. Removing a pack removes every pack that requires it too, and says so.

RELOCATE moves the checkout itself. Every registered project names it by its absolute path: the
hook line, the `{{OCTOPUS}}` path filled into each installed file, the config's `cli`. It waits
until nothing runs from the checkout but the board and the managers, stops those, renames the
directory, leaves a junction at the old path, rewrites those paths in every registered project,
moves the registry row and Claude Code's state of the directory, and starts the board again.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

import agents as _agents
import project as _project
from project import OCTOPUS, CORE, PACKS, CONFIG_DIR, PROJECT_FILE

PROFILES_FILE = OCTOPUS / "profiles.json"
INSTALLED = "installed.json"
TEXT_SUFFIXES = {".md", ".json", ".py", ".bat", ".cmd", ".ps1", ".txt", ".js", ".ts", ".html",
                 ".css", ".yml", ".yaml", ".toml", ".ini", ".cfg", ""}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read_json(p, default):
    try:
        with io.open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_bytes(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data)


def _json_bytes(d):
    return (json.dumps(d, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# The env every project's `.claude/settings.json` carries. A session launched from inside another Claude session
# inherits CLAUDE_CODE_CHILD_SESSION and keeps no transcript unless persistence is forced; the owner's rule is that
# every session keeps its transcript. `STATUS_ENV` sets Claude Code's reminder to ask for one status line once every
# 5 minutes.
SESSION_ENV = dict({"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1", "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE": "1",
                    "CLAUDE_CODE_DISABLE_FAST_MODE": "1"}, **_agents.STATUS_ENV)

# The permission rules every project's `.claude/settings.json` carries. A finished session's last command is
# `todo.py close <id> --mode <mode> --self`; the auto-mode check refuses it as a process kill unless a rule allows it.
SESSION_ALLOW = ["Bash(python .octopus/todo.py close:*)", "PowerShell(python .octopus/todo.py close:*)"]

# The rules folder of the payload.
RULES_DIR = ".claude/rules/octopus"
# The second shim a project may hold; relocate rewrites the path it names.
LEGACY_SHIM = ".aitools/todo.py"


def _cd(root):
    """The project's config directory name."""
    return _project.config_dir(root)


# The old name of each renamed profile -> its name now; `--profile` still takes the old one.
PROFILE_ALIASES = {"browser": "browser-game"}


def profiles():
    """profile name -> list of rulesets."""
    d = _read_json(PROFILES_FILE, {})
    return {k: v["rulesets"] for k, v in d.get("profiles", {}).items()}


def hook_command(root=None):
    """The Claude hook line of the project at `root`. The checkout's own line finds
    `octopus.py` at run time, so the tracked `.claude/settings.json` names no machine path."""
    if root is not None and is_checkout(root):
        return 'python "$CLAUDE_PROJECT_DIR/octopus.py" hook'
    return 'python "%s" hook' % (OCTOPUS / "octopus.py").as_posix()


def is_checkout(root):
    """Whether `root` is this Octopus AI checkout itself."""
    try:
        return Path(root).resolve() == OCTOPUS.resolve()
    except OSError:
        return False


def status_hook_command():
    """The Codex hook line of the status reminder, without its provider."""
    argv = [sys.executable, (OCTOPUS / "octopus.py").as_posix(), "status-hook"]
    if os.name == "nt":
        # Codex runs hooks in its PowerShell session shell. A quoted executable
        # needs the call operator; literal strings preserve spaces, $ and apostrophes.
        return "& " + " ".join("'%s'" % a.replace("'", "''") for a in argv)
    return shlex.join(argv)


# The hook line of any checkout, under either script name. A checkout that moved recognises
# the line an install wrote at its old path.
_HOOK_LINE = re.compile(r'^python "[^"]+/(?:octopus|aitools)\.py" hook$')


def is_hook_command(cmd):
    """Whether `cmd` is a hook line some install of Octopus AI wrote."""
    return isinstance(cmd, str) and bool(_HOOK_LINE.match(cmd))


def hook_events():
    """Each event `octopus hook` serves, as `{event: (matcher, async)}`. The matchers are the
    dialogs `session_state.py` watches, and on `PreToolUse` also the tools that
    `readme_guard.py` reads. `PostToolUse` fires after every tool, so it runs in the
    background and never holds a turn."""
    hooks = str(CORE / "hooks")
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    import readme_guard
    import session_state as ss
    return {
        "SessionStart": ("", False),
        "UserPromptSubmit": ("", False),
        "Stop": ("", False),
        "StopFailure": ("", False),
        "Notification": ("|".join(ss.DIALOG_NOTIFICATIONS), False),
        "PreToolUse": ("|".join(ss.DIALOG_TOOLS + readme_guard.CLAUDE_TOOLS), False),
        "PostToolUse": ("", True),
    }


# ── the install record ──────────────────────────────────────────────────────

class Record:
    """`.octopus/installed.json`: every file install wrote, its hash, and who wrote it."""

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / _cd(self.root) / INSTALLED
        d = _read_json(self.path, {})
        self.files = d.get("files", {})
        self.dirs = d.get("dirs", [])
        self.settings = d.get("settings", {})
        self.entries = d.get("entries", {})
        self.tree = d.get("tree", {})
        self.profile = d.get("profile", "")

    def save(self):
        d = {"profile": self.profile, "files": self.files, "dirs": sorted(set(self.dirs)),
             "settings": self.settings, "entries": self.entries, "tree": self.tree}
        _write_bytes(self.path, _json_bytes(d))

    def mkdirs(self, p):
        """Create the missing parents of `p` and remember each one created."""
        missing = []
        d = p.parent
        while not d.exists():
            missing.append(d)
            d = d.parent
        for d in reversed(missing):
            d.mkdir()
            self.dirs.append(d.relative_to(self.root).as_posix())

    def put(self, rel, data, owner, replace=False):
        """Write one file and record it. A file that exists with other content is left
        alone and reported; install never overwrites what it did not write. With `replace`
        the pack owns the file: a copy it did not write and an edited copy are overwritten."""
        p = self.root / rel
        old = p.read_bytes() if p.exists() else None
        if old is not None and rel not in self.files and not replace:
            if old == data:
                return "same"
            return "kept"
        if rel in self.files and old is not None and _sha(old) != self.files[rel]["hash"] \
                and not replace:
            return "edited"
        if old == data:
            self.files[rel] = {"hash": _sha(data), "by": owner}
            return "same"
        self.mkdirs(p)
        _write_bytes(p, data)
        self.files[rel] = {"hash": _sha(data), "by": owner}
        return "written"


def _fill(data, values):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    for k, v in values.items():
        text = text.replace("{{%s}}" % k, v)
    return text.encode("utf-8")


def _payload_files(base, src, dst):
    """(source file, project-relative destination) for one payload entry."""
    s = base / src
    if src.endswith("/"):
        if not s.is_dir():
            return []
        out = []
        for f in sorted(s.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                out.append((f, (dst.rstrip("/") + "/" + f.relative_to(s).as_posix())))
        return out
    return [(s, dst)] if s.is_file() else []


def _values(root, pj):
    """The `{{...}}` values of the payload. In the checkout's own install `{{OCTOPUS}}` and
    `{{PROJECT}}` are `.`: git tracks that payload, and it names no machine path."""
    own = is_checkout(root)
    return {"OCTOPUS": "." if own else OCTOPUS.as_posix(),
            "NAME": pj.get("name", root.name),
            "SLUG": pj.get("slug", root.name.lower()),
            "PROJECT": "." if own else Path(root).as_posix()}


# ── install ─────────────────────────────────────────────────────────────────

def install(root, profile=None, name=None, slug=None, register=True, out=print, manager=True,
            packs=None):
    """Install Octopus AI into the project at `root`. Returns the record. The checkout's own
    project, the manager's home, gets the manager ticket too when it had no config, unless
    `manager` is false. `packs`, a list of pack
    names, chooses the packs in place of a profile: the base rulesets, then those packs and
    what they require."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("%s is not a directory" % root)
    rec = Record(root)
    cfg_path = root / CONFIG_DIR / PROJECT_FILE
    existing = _read_json(cfg_path, None) if cfg_path.exists() else None
    if existing is not None and (migrate_pack_names(root, rec, existing, out)
                                 + migrate_provider_keys(root, rec, existing, out)):
        _write_bytes(cfg_path, _json_bytes(existing))
    profile = PROFILE_ALIASES.get(profile, profile)
    profs = profiles()
    if packs is not None:
        chosen = order_packs(packs)
        profile = profile_of(chosen)
        profs = dict(profs)
        profs.setdefault(profile, base_rulesets() + chosen)
    if profile is None and existing is None:
        raise ValueError("a new project needs --profile; one of %s" % ", ".join(profs))
    if profile is not None and profile not in profs:
        raise ValueError("no profile %r; one of %s" % (profile, ", ".join(profs)))
    if existing is None:
        pj = json.loads((CORE / "templates" / "project.json").read_text(encoding="utf-8"))
        pj["name"] = name or root.name
        pj["slug"] = slug or _slug(pj["name"])
        pj["rulesets"] = list(profs[profile])
        pj["profile"] = profile
        rec.put("%s/%s" % (CONFIG_DIR, PROJECT_FILE), _json_bytes(pj), "core")
    else:
        pj = existing
        if profile is not None:
            known = _known_rulesets()
            own = [r for r in pj.get("rulesets", []) if r not in known]
            pj["rulesets"] = list(profs[profile]) + own
            pj["profile"] = profile
            _write_bytes(cfg_path, _json_bytes(pj))
    rec.profile = pj.get("profile", profile or "")
    _check_requires(pj["rulesets"])
    values = _values(root, pj)
    tpl = CORE / "templates"
    rec.put("%s/todo.py" % CONFIG_DIR, _fill((tpl / "todo.py").read_bytes(),
                                             dict(values, OCTOPUS=OCTOPUS.as_posix())), "core")
    _install_settings(root, rec)
    _move_payload(root, rec, out)
    for rs in pj["rulesets"]:
        _install_payload(root, rs, rec, values, out)
    write_entries(root, rec, pj, out)
    _install_tree(root, rec, out, manager=manager and existing is None)
    rec.save()
    if register:
        _project.register(root, pj["name"])
    out("installed %s into %s: %s" % (rec.profile or "config", root, ", ".join(pj["rulesets"])))
    return rec


def _slug(name):
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


# The name the checkout registers itself under.
SELF_NAME = "Octopus AI"


def ensure_self(out=print):
    """Install Octopus AI into its own checkout and register it, when the checkout has no
    config: the first run of anything in a fresh clone. A checkout with a config is left as it
    is, also when the owner took it out of the registry. Returns the record, or None."""
    if _project.has_config(OCTOPUS):
        return None
    name = SELF_NAME
    if any(r["name"] == name and not is_checkout(r["path"]) for r in _project.load_registry()):
        name = "%s (%s)" % (SELF_NAME, OCTOPUS.name)
    rec = install(OCTOPUS, "none", name, register=False, out=out)
    if (OCTOPUS / ".git").exists():
        set_settings(OCTOPUS, vcs="git")
    _project.register(OCTOPUS, name)
    out("registered %s  %s" % (name, OCTOPUS))
    return rec


def migrate_pack_names(root, rec, pj, out=print):
    """Give the project the name each renamed pack it lists goes by now (`project.PACK_ALIASES`):
    in `pj` (the caller writes it), in the install record (the caller saves it), the files the
    pack installed under `.agents/rules/octopus/<old>/`, and its settings `.octopus/<old>.json`.
    A renamed profile takes its new name too. Returns the old names it renamed."""
    root = Path(root)
    olds = [n for n in pj.get("rulesets", []) if _project.ruleset_name(root, n) != n]
    for old in olds:
        new = _project.pack_name(old)
        src_dir = "%s/%s/" % (_agents.OCTOPUS_RULES, old)
        dst_dir = "%s/%s/" % (_agents.OCTOPUS_RULES, new)
        for rel in sorted(r for r in rec.files if r.startswith(src_dir)):
            nrel = dst_dir + rel[len(src_dir):]
            src, dst = root / rel, root / nrel
            if src.is_file() and not dst.exists():
                rec.mkdirs(dst)
                shutil.move(str(src), str(dst))
            elif src.is_file() and dst.read_bytes() == src.read_bytes():
                src.unlink()
            elif src.is_file():
                out("  %s and %s both exist with other content; %s is left as it is"
                    % (rel, nrel, rel))
                continue
            rec.files[nrel] = rec.files.pop(rel)
        _agents._prune(root, root / src_dir, stop=root / _agents.AGENTS_DIR)
        rec.dirs = [d for d in rec.dirs if (root / d).is_dir() or not (d + "/").startswith(src_dir)]
        for v in rec.files.values():
            if v.get("by") == old:
                v["by"] = new
        cd = root / _cd(root)
        if (cd / ("%s.json" % old)).is_file() and not (cd / ("%s.json" % new)).exists():
            (cd / ("%s.json" % old)).rename(cd / ("%s.json" % new))
        out("  pack %s is called %s now" % (old, new))
    if olds:
        pj["rulesets"] = [_project.ruleset_name(root, n) for n in pj["rulesets"]]
    old_prof = pj.get("profile")
    prof = PROFILE_ALIASES.get(old_prof)
    if prof:
        pj["profile"] = prof
        olds = olds + [old_prof]
    if PROFILE_ALIASES.get(rec.profile):
        rec.profile = PROFILE_ALIASES[rec.profile]
    rel = "%s/%s" % (_cd(root), PROJECT_FILE)
    if olds and rel in rec.files:
        rec.files[rel]["hash"] = _sha(_json_bytes(pj))
    return olds


def migrate_provider_keys(root, rec, pj, out=print):
    """Give `pj` the provider keys by their names now (`project.OLD_PROVIDER_KEYS`): `agent`
    becomes `provider` and `agents` becomes `providers`; a new key already there wins and the
    old one goes. The caller writes `pj`, and saves the record. Returns the old keys it
    renamed."""
    olds = rename_provider_keys(pj)
    for old in olds:
        out("  %s is called %s now" % (old, _project.OLD_PROVIDER_KEYS[old]))
    rel = "%s/%s" % (_cd(Path(root)), PROJECT_FILE)
    if olds and rel in rec.files:
        rec.files[rel]["hash"] = _sha(_json_bytes(pj))
    return olds


def rename_provider_keys(pj):
    """`migrate_provider_keys` on the dict alone. Returns the old keys it renamed."""
    olds = [k for k in _project.OLD_PROVIDER_KEYS if k in pj]
    for old in olds:
        value = pj.pop(old)
        pj.setdefault(_project.OLD_PROVIDER_KEYS[old], value)
    return olds


def _move_payload(root, rec, out=print):
    """Move every payload file recorded under its old Claude path (`.claude/rules/octopus/`,
    `.claude/skills/`) to its one copy in `.agents/`. A copy already there with the same bytes
    replaces it; one with other bytes leaves both, and says so."""
    moved = 0
    for rel in sorted(rec.files):
        new = _agents.new_path(rel)
        if new is None or rec.files[rel].get("by") == _agents.OWNER:
            continue
        src, dst = root / rel, root / new
        if src.is_file() and dst.exists() and dst.read_bytes() != src.read_bytes():
            out("  %s and %s both exist with other content; %s is left as it is"
                % (rel, new, rel))
            continue
        if src.is_file() and not dst.exists():
            rec.mkdirs(dst)
            shutil.move(str(src), str(dst))
        elif src.is_file():
            src.unlink()
        _agents._prune(root, src.parent, stop=root / ".claude")
        rec.files[new] = rec.files.pop(rel)
        moved += 1
    if moved:
        _remove_empty(root, [d for d in rec.dirs if _agents.new_path(d + "/")], keep=True)
        out("  %d payload file(s) moved to %s/" % (moved, _agents.AGENTS_DIR))
    return moved


def write_entries(root, rec, pj=None, out=print):
    """Write the entry file and the hook file of each AI provider from what `.agents/` holds
    (`agents.py`)."""
    pj = pj if pj is not None else _read_json(root / _cd(root) / PROJECT_FILE, {})
    cli = pj.get("cli") or "python %s/todo.py" % _cd(root)
    return _agents.write_entries(root, rec, pj.get("rulesets", []), cli, out=out,
                                 hook=status_hook_command())


def adopt(root, dry_run=False, out=print):
    """Move the project's own rules and skills from Claude's places into `.agents/`, then
    write every provider's entry file (`agents.adopt`). Returns its result."""
    root = Path(root).resolve()
    rec = Record(root)
    if not rec.path.exists():
        raise ValueError("%s has no %s/%s: install Octopus AI first" % (root, _cd(root), INSTALLED))
    res = _agents.adopt(root, recorded=rec.files, dry_run=dry_run, out=out)
    for k in ("moved", "dropped", "kept", "rewritten"):
        for x in res[k]:
            out("  %-9s %s" % (k, x))
    if not dry_run:
        write_entries(root, rec, out=out)
        rec.save()
    out("%s %s: %d moved, %d dropped as duplicates, %d kept, %d rewritten"
        % ("would adopt" if dry_run else "adopted", root, len(res["moved"]), len(res["dropped"]),
           len(res["kept"]), len(res["rewritten"])))
    return res


def adopt_all(dry_run=False, out=print):
    """`adopt` on every registered project that has an install record."""
    for row in _project.load_registry():
        root = Path(row["path"])
        if not (root / _cd(root) / INSTALLED).is_file():
            out("%s: no install record, skipped" % row["name"])
            continue
        out("%s:" % row["name"])
        try:
            adopt(root, dry_run=dry_run, out=out)
        except (OSError, ValueError) as exc:
            out("  error: %s" % exc)


# The manager ticket the checkout's own project gets at install, when it has none.
MANAGER_TEMPLATE = CORE / "templates" / "manager.md"


def _install_tree(root, rec, out=print, manager=False):
    """The ticket tree a project needs to pass `check`: its lanes and its index; with
    `manager`, the manager ticket when the template exists, the project is the checkout's own,
    the manager's home, and it has none.
    `rec.tree` remembers a tree install made and the tickets it filed, so uninstall can take
    them out again while nobody added to them."""
    import todo
    tok = _project.use(Path(root).resolve())
    try:
        P = _project.P
        if not rec.tree:
            rec.tree = {"dir": P.todo_dir, "created": not P.TODO.exists(), "tickets": {}}
        for d in P.STATES:
            (P.TODO / d).mkdir(parents=True, exist_ok=True)
        if manager and is_checkout(root) and MANAGER_TEMPLATE.is_file() and not any(
                t.repeat == todo.MANAGER for t in todo.load_all()):
            tid = todo.file_template(MANAGER_TEMPLATE.read_text(encoding="utf-8"))
            if tid:
                path = next(P.TODO.rglob("%s-*.md" % tid))
                rec.tree["tickets"][path.relative_to(root).as_posix()] = _sha(path.read_bytes())
                out("  manager ticket %s filed" % tid)
        todo.write_index(quiet=True)
    finally:
        _project.reset(tok)


# The files the engine derives in a ticket tree; uninstall removes them with a tree it made.
TREE_DERIVED = ("INDEX.md", ".ids", "GROUPS.md", ".groups.json", ".board.json")


def _uninstall_tree(root, rec, out=print):
    """Take out the ticket tree install made, when it holds nothing but what install put
    there: the tickets it filed, unchanged, and the files the engine derives."""
    tree = rec.tree or {}
    if not tree.get("created"):
        return
    todo_dir = root / tree.get("dir", "TODO")
    if not todo_dir.is_dir():
        return
    mine = tree.get("tickets", {})
    derived = {(todo_dir / n).resolve() for n in TREE_DERIVED}
    for f in todo_dir.rglob("*"):
        if not f.is_file() or f.resolve() in derived:
            continue
        rel = f.relative_to(root).as_posix()
        if rel not in mine or _sha(f.read_bytes()) != mine[rel]:
            out("  %s holds tickets of the project's own and stays" % tree.get("dir", "TODO"))
            return
    shutil.rmtree(todo_dir)


def _known_rulesets():
    names = {p.stem for p in (CORE / "rulesets").glob("*.json")}
    names |= set(_project.pack_names())
    return names


def _ruleset_file(name):
    p = CORE / "rulesets" / ("%s.json" % name)
    if p.is_file():
        return p, CORE
    d = _project.pack_dir(name)
    if d is not None:
        return d / "ruleset.json", d
    return None, None


def base_rulesets():
    """The rulesets every project lists before its packs: those of the profile `none`."""
    return list(profiles().get("none", ["core"]))


def order_packs(names):
    """`names` with every pack each one requires, ordered so a pack comes after what it
    requires; otherwise in the order of `pack_names()`. Raises ValueError on an unknown pack."""
    known = _project.pack_names()
    base = set(base_rulesets())
    want, todo = [], [_project.pack_name(n) for n in names if n not in base]
    while todo:
        n = todo.pop(0)
        if n in want:
            continue
        if n not in known:
            raise ValueError("no pack %r; one of %s" % (n, ", ".join(known)))
        want.append(n)
        todo += [r for r in _requires(n) if r not in base]
    out = []

    def visit(n, path):
        if n in out:
            return
        if n in path:
            raise ValueError("the packs %s require each other" % " and ".join(path))
        for r in _requires(n):
            if r not in base:
                visit(r, path + [n])
        out.append(n)

    for n in sorted(want, key=known.index):
        visit(n, [])
    return out


def profile_of(packs):
    """The profile whose packs are exactly `packs`, else "custom"."""
    for name, rs in profiles().items():
        if set(rs) - set(base_rulesets()) == set(packs):
            return name
    return "custom"


def _requires(name):
    p, _base = _ruleset_file(name)
    return _read_json(p, {}).get("requires", []) if p else []


def _check_requires(names):
    for i, n in enumerate(names):
        for need in _requires(n):
            if need not in names[:i]:
                raise ValueError("%s requires %s, listed before it" % (n, need))


def _install_payload(root, rs, rec, values, out, only_replace=False):
    """Copy one ruleset's payload into the project. Returns the number of files written or
    removed. `only_replace` takes only the entries the pack owns (`replace`)."""
    p, base = _ruleset_file(rs)
    if p is None:
        return 0
    total = 0
    spec = _read_json(p, {})
    pj = _read_json(_project.config_path(root, PROJECT_FILE), {})
    glob = _project.global_rules()
    off = {stem for stem, s in (spec.get("optionalRules") or {}).items()
           if isinstance(s, dict) and not _project.rule_on(stem, s.get("default", True), pj, glob)}
    for e in spec.get("payload", []):
        # The checkout's own install copies its own payload: git tracks the copies, and each
        # one follows its source in core/.
        replace = bool(e.get("replace")) or is_checkout(root)
        if only_replace and not replace:
            continue
        to = _payload_dest(root, rs, rec, e)
        changed, removed, shipped = 0, 0, set()
        for src, dst in _payload_files(base, e["from"], to):
            if src.suffix.lower() == ".md" and src.stem in off:
                removed += _drop_rule(root, rs, rec, dst, out)
                continue
            data = src.read_bytes()
            if src.suffix.lower() in TEXT_SUFFIXES:
                data = _fill(data, values)
            shipped.add(dst)
            r = rec.put(dst, data, rs, replace=replace)
            if r in ("kept", "edited"):
                out("  %s: %s exists with other content and is left as it is" % (rs, dst))
            elif r == "written":
                changed += 1
        if replace and to.endswith("/"):
            # A file the pack no longer ships leaves the copy; build output was never recorded.
            for rel in sorted(r for r, v in rec.files.items()
                              if v["by"] == rs and r.startswith(to) and r not in shipped):
                (root / rel).unlink(missing_ok=True)
                del rec.files[rel]
                removed += 1
        if replace and (changed or removed):
            out("  %s: %s %d file(s) written, %d removed" % (rs, to, changed, removed))
        if (changed or removed) and e.get("pending"):
            _write_bytes(root / e["pending"], ("%s %s %s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), rs, to)).encode("utf-8"))
        total += changed + removed
    return total


def _drop_rule(root, rs, rec, dst, out):
    """Take out the copy of an optional rule that is off. Only a copy this ruleset wrote and
    nobody edited goes. Returns 1 when it went."""
    p = root / dst
    v = rec.files.get(dst)
    if v is None or v["by"] != rs:
        return 0
    if p.is_file() and _sha(p.read_bytes()) != v["hash"]:
        out("  %s: %s is off, and its copy was edited; it is left as it is" % (rs, dst))
        return 0
    p.unlink(missing_ok=True)
    del rec.files[dst]
    out("  %s: %s is off and removed" % (rs, dst))
    return 1


# Directories a `find` search never enters: build output, the engine's working files, VCS.
_FIND_SKIP = {"Binaries", "Intermediate", "Saved", "DerivedDataCache", ".git", "__pycache__"}
_FIND_DEPTH = 3


def _payload_dest(root, rs, rec, e):
    """The project-relative destination of a payload entry. With `find`, the directory where
    the project already keeps that file: the one this ruleset recorded, else the first found
    under the top directory of `to`; `to` when the project holds none."""
    to, name = e["to"], e.get("find")
    if not name:
        return to
    for rel, v in sorted(rec.files.items()):
        if v["by"] == rs and rel.rsplit("/", 1)[-1] == name and (root / rel).is_file():
            return rel.rsplit("/", 1)[0] + "/"
    top = root / to.strip("/").split("/")[0]
    for d, dirs, files in os.walk(top):
        here = Path(d)
        if name in files:
            return here.relative_to(root).as_posix() + "/"
        depth = len(here.relative_to(top).parts)
        dirs[:] = [] if depth >= _FIND_DEPTH else sorted(x for x in dirs if x not in _FIND_SKIP)
    return to


def _pending_files(names):
    """The `pending` markers the payload of these rulesets writes."""
    out = []
    for n in names:
        p, _base = _ruleset_file(n)
        for e in _read_json(p, {}).get("payload", []) if p else []:
            if e.get("pending"):
                out.append(e["pending"])
    return out


def _owns_payload(name):
    """Whether the ruleset has a payload entry it owns (`replace`)."""
    p, _base = _ruleset_file(name)
    return bool(p) and any(e.get("replace") for e in _read_json(p, {}).get("payload", []))


# ── update ──────────────────────────────────────────────────────────────────

def update(root, out=print):
    """Write the payload of every ruleset the project lists again, so what changed in a pack
    reaches the project. The same as `install` with no profile."""
    return install(root, register=False, out=out)


def update_all(out=print):
    """`update` on every registered project that has an install record. Returns their names."""
    done = []
    for row in _project.load_registry():
        root = Path(row["path"])
        if not (root / _cd(root) / INSTALLED).is_file():
            continue
        out("%s:" % row["name"])
        try:
            update(root, out=out)
            done.append(row["name"])
        except (OSError, ValueError) as exc:
            out("  error: %s" % exc)
    return done


# A `refresh` that finds this lock younger than REFRESH_LOCK_STALE seconds leaves the work to its holder.
REFRESH_LOCK = "update.lock"
REFRESH_LOCK_STALE = 600


def refresh(root, out=print):
    """Bring the payload the packs own (`replace`) up to date in the project, and give a
    renamed pack its new name with its whole payload (`migrate_pack_names`). Returns the
    number of files written or removed. The `SessionStart` hook runs it, so a pack change
    reaches every project the next time a session starts in it. In the checkout itself it
    brings its whole payload up to date."""
    root = Path(root).resolve()
    cd = root / _cd(root)
    pj = _read_json(cd / PROJECT_FILE, None)
    if not pj or not (cd / INSTALLED).is_file():
        return 0
    own = is_checkout(root)
    names = [n for n in pj.get("rulesets", []) if own or _owns_payload(n)]
    lock = cd / REFRESH_LOCK
    try:
        if time.time() - lock.stat().st_mtime > REFRESH_LOCK_STALE:
            lock.unlink()
    except OSError:
        pass
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return 0
    try:
        rec = Record(root)
        renamed = migrate_pack_names(root, rec, pj, out)
        keys = migrate_provider_keys(root, rec, pj, out)
        n = len(keys)
        if renamed or keys:
            _write_bytes(cd / PROJECT_FILE, _json_bytes(pj))
            names = [r for r in pj.get("rulesets", []) if own or _owns_payload(r)]
            n += len(renamed)
        values = _values(root, pj)
        for old in renamed:
            new = _project.pack_name(old)
            if new != old and new in pj.get("rulesets", []):
                n += _install_payload(root, new, rec, values, out)
        n += sum(_install_payload(root, rs, rec, values, out, only_replace=True) for rs in names)
        # A rule or a skill added to `.agents/` reaches every provider's entry file. A project
        # that `update` has not moved to `.agents/` yet keeps what it has.
        if (root / _agents.AGENTS_DIR).is_dir():
            n += len(write_entries(root, rec, pj, out))
        if n:
            rec.save()
        return n
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def _install_settings(root, rec):
    """Add the hook lines and the title variable to `.claude/settings.json`, and record
    exactly what was added so uninstall can take it out again."""
    p = root / ".claude" / "settings.json"
    created = not p.exists()
    original = None if created else p.read_bytes().decode("utf-8")
    d = _read_json(p, {}) if not created else {}
    cmd = hook_command(root)
    added = []
    hooks = d.setdefault("hooks", {})
    # A hook line under the other script name, or one written before the checkout moved,
    # becomes this one.
    for groups in hooks.values():
        for g in groups if isinstance(groups, list) else []:
            for h in g.get("hooks", []) if isinstance(g, dict) else []:
                if isinstance(h, dict) and is_hook_command(h.get("command")):
                    h["command"] = cmd
    for ev, (matcher, background) in hook_events().items():
        groups = hooks.setdefault(ev, [])
        mine = [g for g in groups if any(h.get("command") == cmd for h in g.get("hooks", []))]
        if mine:
            # The matcher follows the events this install serves now.
            for g in mine:
                if matcher:
                    g["matcher"] = matcher
                else:
                    g.pop("matcher", None)
            continue
        hook = {"type": "command", "command": cmd}
        if background:
            hook["async"] = True
        groups.append(dict({"matcher": matcher} if matcher else {}, hooks=[hook]))
        added.append(ev)
    env = d.setdefault("env", {})
    env_added = any(k not in env for k in SESSION_ENV)
    for k, v in SESSION_ENV.items():
        env.setdefault(k, v)
    # The status reminder follows the owner's order in every project, whatever value an older
    # install wrote.
    env.update(_agents.STATUS_ENV)
    allow = d.setdefault("permissions", {}).setdefault("allow", [])
    allow_added = [r for r in SESSION_ALLOW if r not in allow]
    allow.extend(allow_added)
    if created:
        rec.mkdirs(p)
    data = _json_bytes(d)
    _write_bytes(p, data)
    old = rec.settings or {}
    rec.settings = {"created": old.get("created", created),
                    "original": old.get("original", original),
                    "written": _sha(data),
                    "hooks": sorted(set(old.get("hooks", [])) | set(added)),
                    "env": bool(old.get("env")) or env_added,
                    "allow": sorted(set(old.get("allow", [])) | set(allow_added)), "command": cmd}


# ── uninstall ───────────────────────────────────────────────────────────────

def dependents(names, pack):
    """Every ruleset in `names` that requires `pack`, directly or through another."""
    out, changed = {pack}, True
    while changed:
        changed = False
        for n in names:
            if n not in out and any(r in out for r in _requires(n)):
                out.add(n)
                changed = True
    return [n for n in names if n in out]


def uninstall(root, pack=None, out=print):
    """Remove what install wrote: everything, or one pack and the packs that require it."""
    root = Path(root).resolve()
    cd = _cd(root)
    rec = Record(root)
    if not rec.path.exists():
        raise ValueError("%s has no %s/%s: nothing was installed" % (root, cd, INSTALLED))
    cfg_path = root / cd / PROJECT_FILE
    if pack:
        _migrate(root, out)
        rec = Record(root)
    pj = _read_json(cfg_path, {})
    if pack:
        pack = _project.ruleset_name(root, pack)
        names = pj.get("rulesets", [])
        if pack not in names:
            raise ValueError("%s is not installed" % pack)
        gone = dependents(names, pack)
        if len(gone) > 1:
            out("removing %s also removes %s, which require it"
                % (pack, ", ".join(g for g in gone if g != pack)))
        targets = [rel for rel, v in rec.files.items() if v["by"] in gone]
    else:
        gone = None
        targets = list(rec.files)
    edited = [rel for rel in targets if (root / rel).exists()
              and _sha((root / rel).read_bytes()) != rec.files[rel]["hash"]
              and rel != "%s/%s" % (cd, PROJECT_FILE)]
    if edited:
        raise ValueError("these files changed since install; restore or remove them first: %s"
                         % ", ".join(sorted(edited)))
    for rel in targets:
        p = root / rel
        if p.exists():
            p.unlink()
        del rec.files[rel]
    for rel in _pending_files(gone if pack else pj.get("rulesets", [])):
        (root / rel).unlink(missing_ok=True)
    if pack:
        pj["rulesets"] = [n for n in pj.get("rulesets", []) if n not in gone]
        if cfg_path.exists():
            data = _json_bytes(pj)
            _write_bytes(cfg_path, data)
            rel = "%s/%s" % (cd, PROJECT_FILE)
            if rel in rec.files:
                rec.files[rel]["hash"] = _sha(data)
        write_entries(root, rec, pj, out)
        rec.save()
        _remove_empty(root, rec.dirs, keep=True)
        out("removed %s" % ", ".join(gone))
        return gone
    _agents.remove_entries(root, rec)
    _uninstall_tree(root, rec, out)
    _uninstall_settings(root, rec)
    rec.path.unlink()
    _remove_empty(root, rec.dirs + [cd], keep=False)
    _project.unregister(root)
    out("uninstalled Octopus AI from %s" % root)
    return None


def _uninstall_settings(root, rec):
    p = root / ".claude" / "settings.json"
    s = rec.settings or {}
    if not p.exists():
        return
    if s.get("created"):
        p.unlink()
        return
    if s.get("original") is not None and _sha(p.read_bytes()) == s.get("written"):
        # Nothing changed the file since install: put back the bytes that were there.
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(s["original"])
        return
    d = _read_json(p, {})
    cmd = s.get("command", hook_command())
    hooks = d.get("hooks", {})
    for ev in s.get("hooks", []):
        groups = [g for g in hooks.get(ev, [])
                  if not any(h.get("command") == cmd or is_hook_command(h.get("command"))
                             for h in g.get("hooks", []))]
        if groups:
            hooks[ev] = groups
        else:
            hooks.pop(ev, None)
    if not hooks:
        d.pop("hooks", None)
    if s.get("env"):
        for k in SESSION_ENV:
            d.get("env", {}).pop(k, None)
        if not d.get("env"):
            d.pop("env", None)
    if s.get("allow"):
        perms = d.get("permissions", {})
        perms["allow"] = [r for r in perms.get("allow", []) if r not in s["allow"]]
        if not perms["allow"]:
            perms.pop("allow")
        if not perms:
            d.pop("permissions", None)
    _write_bytes(p, _json_bytes(d))


def _remove_empty(root, dirs, keep):
    """Remove the recorded directories that are empty now, deepest first."""
    for rel in sorted(set(dirs), key=lambda x: -x.count("/")):
        d = root / rel
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                if keep and rel in dirs:
                    dirs.remove(rel)
        except OSError:
            pass


# ── relocate ────────────────────────────────────────────────────────────────

# The project files that may name the checkout by its absolute path besides the install
# record's own: the project's instructions and its Claude config. History (`TODO/`,
# `.claude/coordination/`) keeps the path it was written with.
OWN_FILES = ("CLAUDE.md", "AGENTS.md", ".claude/settings.json", ".claude/settings.local.json",
             ".codex/hooks.json")
OWN_DIRS = (".claude/rules", ".claude/skills", ".claude/agents", ".claude/commands",
            ".agents/rules", ".agents/skills")


def _path_pattern(old):
    """Every spelling of the directory `old` in a text: `/`, `\\` or the `\\\\` of JSON between
    its parts, in any case, and not the start of a longer name."""
    parts = [re.escape(x) for x in Path(old).as_posix().rstrip("/").split("/")]
    body = parts[0] + r"(?P<sep>/|\\\\|\\)" + r"(?P=sep)".join(parts[1:])
    return re.compile(r"(?<![\w])" + body + r"(?![\w-])", re.IGNORECASE)


def swap_path(text, old, new):
    """`text` with every spelling of the directory `old` written as `new`, in the same
    spelling."""
    parts = Path(new).as_posix().rstrip("/").split("/")
    return _path_pattern(old).sub(lambda m: m.group("sep").join(parts), text)


def _same_dir(a, b):
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def _swap_file(p, old, new):
    """Rewrite the file `p` with `swap_path`. Returns its new bytes, or None when it did not
    change."""
    try:
        text = p.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    moved = swap_path(text, old, new)
    if moved == text:
        return None
    data = moved.encode("utf-8")
    _write_bytes(p, data)
    return data


def relocate_project(root, old, new, out=print):
    """Rewrite every absolute path into the checkout `old` that the project at `root` carries:
    the hook lines, every installed file, the config files, the project's own instructions.
    An installed file that was unedited keeps its recorded hash in step. Returns the files
    changed, project-relative."""
    root = Path(root)
    rec = Record(root)
    record = "%s/%s" % (_cd(root), INSTALLED)
    candidates = [root / rel for rel in sorted(rec.files)]
    candidates += [root / f for f in OWN_FILES]
    candidates += sorted((root / _cd(root)).glob("*.json")) + [root / LEGACY_SHIM]
    for d in OWN_DIRS:
        if (root / d).is_dir():
            candidates += sorted(f for f in (root / d).rglob("*")
                                 if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES)
    changed, seen = [], set()
    for p in candidates:
        rel = p.relative_to(root).as_posix()
        if rel in seen or rel == record or not p.is_file():
            continue
        seen.add(rel)
        before = _sha(p.read_bytes())
        data = _swap_file(p, old, new)
        if data is None:
            continue
        changed.append(rel)
        if rel in rec.files and rec.files[rel]["hash"] == before:
            rec.files[rel]["hash"] = _sha(data)
        if rel == ".claude/settings.json" and rec.settings.get("written") == before:
            rec.settings["written"] = _sha(data)
    for k in ("original", "command"):
        if isinstance(rec.settings.get(k), str):
            moved = swap_path(rec.settings[k], old, new)
            if moved != rec.settings[k]:
                rec.settings[k] = moved
                changed.append("%s (%s)" % (record, k))
    if changed and rec.path.exists():
        rec.save()
    for rel in changed:
        out("  %s" % rel)
    return changed


def claude_key(path):
    """The directory Claude Code keeps a project's transcripts and memory in, under
    `~/.claude/projects/`."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path)))


def _claude_home():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def _claude_config():
    """Claude Code's own config, which keeps the trust of each project directory."""
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        return Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"
    return Path.home() / ".claude.json"


def relocate_claude_state(old, new, out=print):
    """Move what Claude Code keeps per project directory from `old` to `new`: the transcripts
    and memory, so a resumed session finds its transcript, and the project's entry in
    `.claude.json`, so a session at the new path asks no trust question."""
    base = _claude_home() / "projects"
    src, dst = base / claude_key(old), base / claude_key(new)
    if src.is_dir():
        if not dst.exists():
            os.rename(src, dst)
        else:
            for f in sorted(src.rglob("*")):
                t = dst / f.relative_to(src)
                if f.is_file() and not t.exists():
                    t.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(f), str(t))
        out("claude: projects/%s -> projects/%s" % (src.name, dst.name))
    cfg = _claude_config()
    d = _read_json(cfg, None)
    if not isinstance(d, dict) or not isinstance(d.get("projects"), dict):
        return
    pat, entries, added = _path_pattern(old), d["projects"], []
    for k in list(entries):
        if pat.fullmatch(k):
            nk = swap_path(k, old, new)
            if nk not in entries:
                entries[nk] = entries[k]
                added.append(nk)
    if added:
        tmp = cfg.with_name(cfg.name + ".octopus-tmp")
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(d, indent=2, ensure_ascii=False))
        os.replace(tmp, cfg)
        out("claude: %s now trusts %s" % (cfg.name, ", ".join(added)))


def _relocate_sessions(checkout, old, new):
    """The live records of the checkout's sessions name its path and its transcript folder."""
    keys = (claude_key(old), claude_key(new))
    for f in sorted((checkout / "TODO" / ".sessions").glob("*.json")):
        try:
            text = f.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        moved = swap_path(text, old, new).replace(*keys)
        if moved != text:
            _write_bytes(f, moved.encode("utf-8"))


def holders(old):
    """`pid -> what it is` for every process that runs from the directory `old`: its current
    directory lies under it, its program lies under it, or it is a Python that runs a script
    out of it. Windows refuses to rename a directory such a process holds, and a script whose
    checkout moved fails at its next import. A shell whose command line only names a script
    there for later runs nothing from it yet. This process is left out; the shell that
    started it is not, since it holds its own current directory."""
    import winproc
    procs = winproc.processes()
    mine = {os.getpid()}
    base = os.path.normcase(str(Path(old))).rstrip("\\/") + os.sep
    under = _path_pattern(old).pattern + r"[/\\][^\"'\s]*"
    program = re.compile(r'^\s*"?' + under, re.IGNORECASE)
    script = re.compile(under + r"\.py\b", re.IGNORECASE)
    out = {}
    for p in procs:
        if p in mine or p <= 4:
            continue
        cwd = winproc.current_directory(p)
        if cwd and (os.path.normcase(cwd).rstrip("\\/") + os.sep).startswith(base):
            out[p] = "%s in %s" % (procs[p][1], cwd.rstrip("\\/"))
            continue
        line = winproc._peb_command_line(p) or ""
        if program.search(line) or (procs[p][1].startswith("python") and script.search(line)):
            out[p] = "%s: %s" % (procs[p][1], " ".join(line.split())[:140])
    return out


# The board server's command line, and what follows `board` on it.
_BOARD_LINE = re.compile(r'(?:octopus|aitools)\.py"?\s+board\b(?P<args>.*)$', re.IGNORECASE)


def _family(procs, roots):
    """`roots` and every process below them."""
    out, todo = set(), list(roots)
    while todo:
        p = todo.pop()
        if p in out:
            continue
        out.add(p)
        todo += [c for c, (ppid, _n) in procs.items() if ppid == p]
    return out


def _restartable(old, held):
    """What `relocate` stops and starts again among the holders of `old`: the board servers,
    as `[(pid of its window, its arguments)]`, and the sessions of the checkout's manager
    tickets, which the board's watchdog starts again. Returns the boards, the manager
    sessions, and every pid the two account for."""
    import winproc
    procs = winproc.processes()
    boards, owned = [], set()
    for pid in held:
        m = _BOARD_LINE.search(winproc._peb_command_line(pid) or "")
        if not m or procs.get(pid, (0, ""))[1].startswith("cmd"):
            continue
        # The window is the `cmd` that runs `AgentBoard.bat`, when there is one.
        top, parent = pid, procs.get(pid, (0, ""))[0]
        if parent in held and procs.get(parent, (0, ""))[1] == "cmd.exe":
            top = parent
        boards.append((top, m.group("args").strip()))
        owned |= _family(procs, [top])
    managers = []
    if (Path(old) / CONFIG_DIR / PROJECT_FILE).is_file():
        import todo as T
        tok = _project.use(Path(old))
        try:
            tickets = T.load_all()
            ids = {t.id for t in tickets if t.repeat == T.MANAGER}
            managers = [s for s in T.live_sessions(tickets)
                        if s.get("id") in ids and s.get("mode") == "work"]
        finally:
            _project.reset(tok)
        owned |= _family(procs, [s["shell"] or s["claude"] for s in managers
                                 if s["shell"] or s["claude"]])
    return boards, managers, owned


def _start_board(checkout, args, out):
    import subprocess
    argv = args.split() if args else []
    if "--no-open" not in argv:
        argv.append("--no-open")
    if os.name != "nt":
        # A Terminal window that runs `AgentBoard.command` with the same arguments.
        import posixterm
        command = "cd %s && exec ./AgentBoard.command %s" % (
            posixterm.quote(str(checkout)), posixterm.join(argv))
        why = posixterm.open_window(command, "Octopus AI - Agent Board",
                                    _project.home() / "board.command")
        out("board: %s" % ("started from %s" % checkout if why is None else why))
        return
    subprocess.Popen(["cmd", "/c", "start", "Octopus AI - Agent Board",
                      str(Path(checkout) / "AgentBoard.bat")] + argv, cwd=str(checkout))
    out("board: started from %s" % checkout)


def relocate(new, old=None, wait=False, link=True, poll=15, out=print):
    """Move the checkout of Octopus AI to `new`, and every path that names it.

    Waits (`wait`) or refuses while a process other than the board server and the managers'
    sessions runs from the checkout, or while a manager's turn runs. Then it stops the board
    and the manager sessions, renames the directory, leaves a junction at the old path when
    `link` is set, rewrites every registered project, moves the registry row and Claude
    Code's state of the directory, and starts the board again from the new path; its
    watchdog resumes the managers there."""
    import winproc
    old = Path(old or OCTOPUS).resolve()
    new = Path(new).resolve()
    if new.exists():
        raise ValueError("%s exists" % new)
    if new.drive.lower() != old.drive.lower():
        raise ValueError("%s is on another drive than %s; a rename stays on one drive"
                         % (new, old))
    if not new.parent.is_dir():
        raise ValueError("%s is not a directory" % new.parent)
    # A process holds its current directory; this one must not hold the checkout.
    os.chdir(str(old.parent))
    said = None
    while True:
        held = holders(old)
        boards, managers, owned = _restartable(old, held)
        busy = ["%6d  %s" % (p, w) for p, w in sorted(held.items()) if p not in owned]
        busy += ["%6s  manager %s: its turn runs" % ("", s["id"]) for s in managers
                 if s.get("running")]
        if not busy:
            break
        text = "these run from %s:\n%s" % (old, "\n".join(busy))
        if not wait:
            raise ValueError(text)
        if text != said:
            out(text)
            out("waiting; checking every %d seconds" % poll)
            said = text
        time.sleep(poll)
    import todo as T
    for top, _args in boards:
        winproc.taskkill(top, force=True, tree=True)
        out("board: stopped (pid %d)" % top)
    for s in managers:
        T.close_session(s)
        out("manager %s: closed; the board's watchdog resumes it" % s["id"])
    deadline = time.time() + 60
    while holders(old) and time.time() < deadline:
        time.sleep(2)
    try:
        left = holders(old)
        if left:
            raise ValueError("these still run from %s:\n%s" % (
                old, "\n".join("%6d  %s" % kv for kv in sorted(left.items()))))
        for attempt in range(20):
            try:
                os.rename(str(old), str(new))
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(1)
    except (OSError, ValueError):
        for _top, args in boards:
            _start_board(old, args, out)
        raise
    out("moved %s -> %s" % (old, new))
    if link and os.name != "nt":
        os.symlink(str(new), str(old), target_is_directory=True)
        out("symlink: %s -> %s" % (old, new))
    elif link:
        import _winapi
        _winapi.CreateJunction(str(new), str(old))
        out("junction: %s -> %s" % (old, new))
    out("%s:" % new.name)
    relocate_project(new, old, new, out=out)
    rows = _project.load_registry()
    for r in rows:
        if _same_dir(r["path"], old):
            r["path"] = str(new)
            continue
        root = Path(r["path"])
        if not root.is_dir():
            out("skipped %s: %s is not a directory" % (r["name"], root))
            continue
        out("%s:" % r["name"])
        relocate_project(root, old, new, out=out)
    _project.save_registry(rows)
    out("registry: %s" % _project.registry_file())
    relocate_claude_state(old, new, out=out)
    _relocate_sessions(new, old, new)
    for _top, args in boards:
        _start_board(new, args, out)
    return new


def status(root, out=print):
    """Each installed file as same, edited or missing. Returns the counts, and in `rows`
    each file as `{"state", "by", "file"}`."""
    root = Path(root).resolve()
    rec = Record(root)
    counts = {"same": 0, "edited": 0, "missing": 0}
    rows = []
    for rel, v in sorted(rec.files.items()):
        p = root / rel
        if not p.exists():
            st = "missing"
        elif _sha(p.read_bytes()) == v["hash"]:
            st = "same"
        else:
            st = "edited"
        counts[st] += 1
        rows.append({"state": st, "by": v["by"], "file": rel})
        out("%-8s %-12s %s" % (st, v["by"], rel))
    out("%d same, %d edited, %d missing" % (counts["same"], counts["edited"], counts["missing"]))
    counts["rows"] = rows
    counts["reporting"] = _agents.reporting_problems(root)
    for p in counts["reporting"]:
        out("warning  %s" % p)
    return counts


# ── packs ───────────────────────────────────────────────────────────────────

def _migrate(root, out=print):
    """`migrate_pack_names` and `migrate_provider_keys` on the project at `root`, written to
    disk."""
    cfg_path = root / _cd(root) / PROJECT_FILE
    pj = _read_json(cfg_path, None)
    rec = Record(root)
    if pj is not None and rec.path.exists() and (migrate_pack_names(root, rec, pj, out)
                                                  + migrate_provider_keys(root, rec, pj, out)):
        _write_bytes(cfg_path, _json_bytes(pj))
        rec.save()


def pack_add(root, pack, out=print):
    root = Path(root).resolve()
    pack = _project.pack_name(pack)
    _migrate(root, out)
    cfg_path = root / CONFIG_DIR / PROJECT_FILE
    pj = _read_json(cfg_path, None)
    if pj is None:
        raise ValueError("%s has no %s/%s" % (root, CONFIG_DIR, PROJECT_FILE))
    if pack in pj["rulesets"]:
        raise ValueError("%s is already installed" % pack)
    if _ruleset_file(pack)[0] is None:
        raise ValueError("no pack %r" % pack)
    for need in _requires(pack):
        if need not in pj["rulesets"]:
            raise ValueError("%s requires %s; add %s first" % (pack, need, need))
    known = _known_rulesets()
    # A pack goes after the last core or pack ruleset, before the project's own ones.
    pos = max([i + 1 for i, n in enumerate(pj["rulesets"]) if n in known] or [0])
    pj["rulesets"].insert(pos, pack)
    rec = Record(root)
    data = _json_bytes(pj)
    _write_bytes(cfg_path, data)
    rel = "%s/%s" % (CONFIG_DIR, PROJECT_FILE)
    if rel in rec.files:
        rec.files[rel]["hash"] = _sha(data)
    _install_payload(root, pack, rec, _values(root, pj), out)
    write_entries(root, rec, pj, out)
    rec.save()
    out("added %s: %s" % (pack, ", ".join(pj["rulesets"])))


def packs_set(root, packs, out=print):
    """Make the project's packs exactly `packs` and what they require: remove every other
    pack, then add the missing ones in `requires` order. Returns (added, removed)."""
    root = Path(root).resolve()
    want = order_packs(packs)
    _migrate(root, out)
    pj = settings(root)
    known = set(_project.pack_names())
    have = [n for n in pj.get("rulesets", []) if n in known]
    removed = []
    for n in [n for n in reversed(have) if n not in want]:
        if n in removed:
            continue
        removed += uninstall(root, n, out=out)
    have = [n for n in settings(root).get("rulesets", []) if n in known]
    added = []
    for n in want:
        if n not in have:
            pack_add(root, n, out=out)
            added.append(n)
    pj = settings(root)
    prof = profile_of([n for n in pj.get("rulesets", []) if n in known])
    if pj.get("profile") != prof:
        pj["profile"] = prof
        _save_pj(root, pj)
    return added, removed


# ── optional rules ──────────────────────────────────────────────────────────

def optional_rules(root):
    """Every optional rule of the project's rulesets, in ruleset order: name, ruleset, title,
    default (its ruleset's), inherited (the global switch, else the default), whether it is
    on, and source: "project", "global" or "default", where that state comes from."""
    root = Path(root).resolve()
    pj = settings(root)
    glob = _project.global_rules()
    rules, _problems = _project.load_rules(root, pj)
    return [{"name": stem, "ruleset": s["ruleset"], "title": s["title"], "default": s["default"],
             "inherited": glob.get(stem, s["default"]),
             "on": _project.rule_on(stem, s["default"], pj, glob),
             "source": _project.rule_source(stem, pj, glob)}
            for stem, s in rules["optionalRules"].items()]


def set_rules(root, changes, out=print):
    """Switch optional rules in one project: `changes` maps a rule to True (on), False (off) or
    None (no switch of its own: the global switch, else the default). Copies each rule turned
    on, removes each rule turned off, and writes the entry files again. Returns the rules that
    changed."""
    root = Path(root).resolve()
    known = {r["name"]: r for r in optional_rules(root)}
    for stem, v in changes.items():
        if stem not in known:
            raise ValueError("no optional rule %r; one of %s"
                             % (stem, ", ".join(known) or "none"))
        if v is not None and not isinstance(v, bool):
            raise ValueError("an optional rule is on, off or default")
    pj = settings(root)
    switches = dict(pj.get("rules") or {})
    for stem, v in changes.items():
        if v is None:
            switches.pop(stem, None)
        else:
            switches[stem] = v
    if switches:
        pj["rules"] = switches
    else:
        pj.pop("rules", None)
    _save_pj(root, pj)
    rec = Record(root)
    values = _values(root, pj)
    for rs in dict.fromkeys(known[s]["ruleset"] for s in changes):
        _install_payload(root, rs, rec, values, out)
    write_entries(root, rec, pj, out)
    rec.save()
    now = {r["name"]: r["on"] for r in optional_rules(root)}
    changed = [s for s in changes if now[s] != known[s]["on"]]
    for s in changed:
        out("%s: %s" % (s, "on" if now[s] else "off"))
    return changed


def global_rules():
    """Every optional rule of the rulesets on this machine (core and every pack): name,
    ruleset, title, default, the global switch (`switch`: true, false or None), whether it is
    on for a project that has no switch of its own, its source ("global" or "default"), and
    `projects`: each registered project that lists its ruleset, with its own state there."""
    glob = _project.global_rules()
    rows, seen = [], set()
    names = sorted(p.stem for p in (CORE / "rulesets").glob("*.json"))
    names += [n for n in _project.pack_names() if n not in names]
    for rs in names:
        p, _base = _ruleset_file(rs)
        spec = _read_json(p, {}) if p else {}
        for stem, s in (spec.get("optionalRules") or {}).items():
            if stem in seen or not isinstance(s, dict):
                continue
            seen.add(stem)
            default = bool(s.get("default", True))
            rows.append({"name": stem, "ruleset": rs, "title": s.get("title", ""),
                         "default": default, "switch": glob.get(stem),
                         "on": glob.get(stem, default),
                         "source": "global" if stem in glob else "default", "projects": []})
    for row in _project.load_registry():
        pj = _read_json(_project.config_path(row["path"], PROJECT_FILE), None)
        if not isinstance(pj, dict):
            continue
        for r in rows:
            if r["ruleset"] in (pj.get("rulesets") or []):
                r["projects"].append({
                    "name": row["name"],
                    "on": _project.rule_on(r["name"], r["default"], pj, glob),
                    "source": _project.rule_source(r["name"], pj, glob)})
    return rows


def set_global_rules(changes, out=print):
    """Switch optional rules for every project: `changes` maps a rule to True (on), False (off)
    or None (no global switch: its default). Then, in each registered project that lists the
    rule's ruleset, copies each rule now on, removes each rule now off, and writes the entry
    files again; a project's own switch still wins there. Returns the rules whose global state
    changed."""
    known = {r["name"]: r for r in global_rules()}
    for stem, v in changes.items():
        if stem not in known:
            raise ValueError("no optional rule %r; one of %s"
                             % (stem, ", ".join(known) or "none"))
        if v is not None and not isinstance(v, bool):
            raise ValueError("an optional rule is on, off or default")
    switches = _project.global_rules()
    for stem, v in changes.items():
        if v is None:
            switches.pop(stem, None)
        else:
            switches[stem] = v
    _project.save_global_rules(switches)
    sets = list(dict.fromkeys(known[s]["ruleset"] for s in changes))
    for row in _project.load_registry():
        root = Path(row["path"])
        pj = _read_json(_project.config_path(root, PROJECT_FILE), None)
        if not isinstance(pj, dict) or not (root / _cd(root) / INSTALLED).is_file():
            continue
        mine = [rs for rs in sets if rs in (pj.get("rulesets") or [])]
        if not mine:
            continue
        rec = Record(root)
        values = _values(root, pj)
        n = sum(_install_payload(root, rs, rec, values, out) for rs in mine)
        n += len(write_entries(root, rec, pj, out))
        rec.save()
        if n:
            out("  %s: rules and entry files written" % row["name"])
    now = {r["name"]: r["on"] for r in global_rules()}
    changed = [s for s in changes if now[s] != known[s]["on"]]
    for s in changed:
        out("%s: %s for every project without its own switch" % (s, "on" if now[s] else "off"))
    return changed


# ── lanes ───────────────────────────────────────────────────────────────────

def _lanes_of(pj):
    return [dict(x) for x in (pj.get("lanes") or _project.DEFAULT_LANES)]


def _save_pj(root, pj):
    cfg_path = _project.config_path(root, PROJECT_FILE)
    data = _json_bytes(pj)
    _write_bytes(cfg_path, data)
    rec = Record(root)
    rel = "%s/%s" % (_cd(root), PROJECT_FILE)
    if rec.path.exists() and rel in rec.files:
        rec.files[rel]["hash"] = _sha(data)
        rec.save()


# ── settings ────────────────────────────────────────────────────────────────

# The keys `set_settings` refuses. The slug names the session ids and `{SLUG}_OUTSIDE_DIR`;
# the rulesets change only with the files they install, through `pack_add` and `uninstall`.
FIXED_KEYS = {
    "slug": "the slug is fixed: the session ids and {SLUG}_OUTSIDE_DIR derive from it",
    "rulesets": "the rulesets change through pack_add and uninstall",
}


def settings(root):
    """The project's `project.json`, as a dict."""
    root = Path(root).resolve()
    pj = _read_json(_project.config_path(root, PROJECT_FILE), None)
    if not isinstance(pj, dict):
        raise ValueError("%s has no readable %s/%s" % (root, _cd(root), PROJECT_FILE))
    return pj


def set_settings(root, **changes):
    """Change keys of `project.json`; a value of None removes the key. The changed dict is
    loaded as the project before it is written: a change that adds a problem raises
    ValueError and leaves the file as it was. Returns the dict now on disk."""
    root = Path(root).resolve()
    for k in changes:
        if k in FIXED_KEYS:
            raise ValueError(FIXED_KEYS[k])
    pj = settings(root)
    candidate = dict(pj)
    # A write gives the provider keys their names now; a change below names only the new ones.
    rename_provider_keys(candidate)
    for k, v in changes.items():
        if v is None:
            candidate.pop(k, None)
        else:
            candidate[k] = v
    before = set(_project.Project(root, pj).problems)
    new = [x for x in _project.Project(root, candidate).problems if x not in before]
    if new:
        raise ValueError("; ".join(new))
    _save_pj(root, candidate)
    return candidate


def lane_add(root, d, label=None, after=None, statuses=None, color=None):
    root = Path(root).resolve()
    pj = _read_json(_project.config_path(root, PROJECT_FILE), None)
    lanes = _lanes_of(pj)
    if any(x["dir"] == d for x in lanes):
        raise ValueError("board %r exists" % d)
    if not statuses:
        raise ValueError("a board needs at least one status")
    pos = len(lanes)
    if after:
        idx = [i for i, x in enumerate(lanes) if x["dir"] == after]
        if not idx:
            raise ValueError("no board %r" % after)
        pos = idx[0] + 1
    lanes.insert(pos, {"dir": d, "label": label or d, "role": "none",
                       "color": color or "#7a7a7a", "statuses": list(statuses)})
    pj["lanes"] = lanes
    _save_pj(root, pj)
    p = _project.load(root)
    (p.TODO / d).mkdir(parents=True, exist_ok=True)
    return lanes


def lane_rename(root, d, label=None, new_dir=None):
    root = Path(root).resolve()
    pj = _read_json(_project.config_path(root, PROJECT_FILE), None)
    lanes = _lanes_of(pj)
    hit = [x for x in lanes if x["dir"] == d]
    if not hit:
        raise ValueError("no board %r" % d)
    if new_dir and any(x["dir"] == new_dir for x in lanes):
        raise ValueError("board %r exists" % new_dir)
    p = _project.load(root)
    if label:
        hit[0]["label"] = label
    if new_dir and new_dir != d:
        src, dst = p.TODO / d, p.TODO / new_dir
        if src.exists():
            shutil.move(str(src), str(dst))
        hit[0]["dir"] = new_dir
    pj["lanes"] = lanes
    _save_pj(root, pj)
    return lanes


def lane_remove(root, d):
    root = Path(root).resolve()
    pj = _read_json(_project.config_path(root, PROJECT_FILE), None)
    lanes = _lanes_of(pj)
    hit = [x for x in lanes if x["dir"] == d]
    if not hit:
        raise ValueError("no board %r" % d)
    if hit[0].get("role", "none") != "none":
        raise ValueError("board %s has role %s; the code needs it" % (d, hit[0]["role"]))
    p = _project.load(root)
    lane_dir = p.TODO / d
    if lane_dir.exists() and any(lane_dir.rglob("*.md")):
        raise ValueError("board %s holds tickets; move them first" % d)
    if lane_dir.exists():
        shutil.rmtree(lane_dir)
    pj["lanes"] = [x for x in lanes if x["dir"] != d]
    _save_pj(root, pj)
    return pj["lanes"]


def reindex(root):
    """Rewrite the project's index after a lane change."""
    import todo
    tok = _project.use(Path(root).resolve())
    try:
        todo.write_index(quiet=True)
    finally:
        _project.reset(tok)
