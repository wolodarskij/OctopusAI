"""leftovers.py - find and remove what the toolkits before Octopus AI left in a project.

    find(root)              [Item]: every leftover, and what `clean` does with it
    clean(root, out)        remove or replace each leftover; returns the items
    clean_hooks(root, out)  only the old hook entries; returns the files it changed
    report(items, out)      one line per item; returns the number of items

THE OLD TOOLKITS. Two came before Octopus AI:

- the ticket engine inside the project: `tools/todo.py`, `tools/todo_server.py`,
  `tools/todo_board.html`, `tools/session_state.py`, `tools/winproc.py`, their tests
  `tools/test_todo_*.py`, its config `TODO/project.json` and `TODO/rulesets/`, and hook lines
  in `.claude/settings.json` that run its scripts;
- the shared toolkit before the rename: `.aitools/` (its shim, its install record, its config,
  `SESSIONS.md`), its payload in `.claude/rules/aitools/`, and its lines in the ignore files.

A FILE IS OLD BY ITS TEXT, NOT BY ITS NAME. A script of the old engine is found by the first
line of its docstring (`SIGNATURES`), so a project's own `tools/todo.py` stays. A hook line is
old when it runs one of those scripts, or a script of `OLD_SCRIPTS` that does not exist.

THE EQUIVALENT. Each leftover has one in Octopus AI, and `clean` uses it:

| Leftover | Octopus AI |
|---|---|
| the old engine, its board and its hooks | the engine, `octopus board`, `octopus hook` |
| `TODO/project.json`, `TODO/rulesets/` | `.octopus/project.json`, `.octopus/rulesets/` |
| `.aitools/` | `.octopus/` |
| `.claude/rules/aitools/`, an old Claude copy of a payload rule or skill | `.agents/` |
| `tools/todo.py` in a command | `.octopus/todo.py` |
| `tools/todo_server.py` in a command | `.octopus/todo.py serve` |

WHAT STAYS AND IS REPORTED. An old script that a staying file imports, or that imports a
staying file next to it, serves something Octopus AI does not know: it stays, with the reason.
An old payload file somebody edited, an own ruleset that `.octopus/rulesets/` holds with other
content, and a project-wide README file (the owner approves its exact text first) stay too.
The ticket tree is history and is never rewritten.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import oldnames
import project as _project

CONFIG_DIR = _project.CONFIG_DIR
ENGINE_DIRS = ("tools",)

# The first line of each script of the old engine.
SIGNATURES = {
    "todo.py": '"""todo.py - the TODO/ ticket tree',
    "todo_server.py": '"""todo_server.py - the TODO board',
    "session_state.py": '"""session_state.py - which launched session waits at its prompt',
    "winproc.py": '"""winproc.py - the Windows process and window calls the TODO board needs',
}
# A file that belongs to the old engine when the script it names is old, in the same directory.
COMPANIONS = (("todo_board.html", "todo_server.py"),)
TEST_GLOB, TEST_OF = "test_todo_*.py", "todo.py"
# The scripts an old hook line ran, by project path.
OLD_SCRIPTS = tuple("tools/%s" % n for n in SIGNATURES) + (".aitools/",)

OLD_CONFIG = ".aitools"
OLD_PAYLOAD_DIR = ".claude/rules/aitools"
HOOK_FILES = (".claude/settings.json", ".claude/settings.local.json", ".codex/hooks.json")
IGNORE_FILES = (".gitignore", ".dvignore", ".hgignore", "ignore.conf", ".p4ignore")
# The text an old Claude copy of a payload rule or skill names.
OLD_TEXT = re.compile(r"tools[/\\]todo(?:_server)?\.py|\.aitools[/\\]|(?<![\w-])aitools(?![\w-])",
                      re.I)
# An ignore line that names the old toolkit's places.
OLD_IGNORE = re.compile(r"(?:^|/)(?:\.aitools|\.claude/rules/aitools)(?:/|$)")
OLD_COMMENT = re.compile(r"^\s*#.*\bAI ?Tools\b", re.I)

# Each command of the old engine -> the command of Octopus AI, applied in this order.
COMMANDS = (
    ("tools", "todo_server.py", CONFIG_DIR, "todo.py serve"),
    ("tools", "todo.py", CONFIG_DIR, "todo.py"),
    (OLD_CONFIG, "todo.py", CONFIG_DIR, "todo.py"),
)
TEXT_SUFFIXES = {".md", ".txt", ".json", ".bat", ".cmd", ".ps1", ".sh", ".command", ".py",
                 ".js", ".cjs", ".mjs", ".ts", ".toml", ".yml", ".yaml", ".html", ""}


class Item(dict):
    """One leftover: `kind`, `path` (project-relative), `action` (remove, rewrite, move,
    keep, readme) and `why`. A rewrite carries `lines`, [(line, old, new)]."""

    def __init__(self, kind, path, action, why="", **kw):
        super().__init__(kind=kind, path=path, action=action, why=why, **kw)


def _read(p):
    try:
        data = Path(p).read_bytes()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", "replace")


def _write(p, text):
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _json_text(d):
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


def _rel(root, p):
    return Path(p).relative_to(root).as_posix()


def _prune(root, d):
    """Remove `d` and each parent up to `root` while it is empty."""
    root, d = Path(root).resolve(), Path(d).resolve()
    while d != root and root in d.parents:
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def _remove(root, rel):
    p = Path(root) / rel
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists() or p.is_symlink():
        p.unlink()
    _prune(root, p.parent)


# ── the old engine ──────────────────────────────────────────────────────────

def _is_old_script(p):
    sig = SIGNATURES.get(p.name)
    text = _read(p) if sig else None
    return bool(text) and text.lstrip("﻿").startswith(sig)


def _imports(text, stem):
    return re.search(r"(?m)^\s*(?:import\s+%s\b|from\s+%s\s+import\b)" % (stem, stem), text)


def _engine(root):
    """{project path: why it stays, or ""} of each file of the old engine, and the
    `__pycache__` files of the scripts that go."""
    found = {}
    for d in ENGINE_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        old = {p.name for p in base.iterdir() if p.is_file() and _is_old_script(p)}
        if not old:
            continue
        files = set(old)
        for comp, of in COMPANIONS:
            if of in old and (base / comp).is_file():
                files.add(comp)
        if TEST_OF in old:
            files |= {p.name for p in base.glob(TEST_GLOB) if p.is_file()}
        stays = {}
        others = [p for p in base.iterdir() if p.is_file() and p.suffix == ".py"
                  and p.name not in files]
        texts = {p.name: _read(base / p.name) or "" for p in others}
        mine = {n: _read(base / n) or "" for n in files if n.endswith(".py")}
        # An old script a staying file imports, or one that imports a staying file, stays,
        # and so does every old script it imports in turn.
        for n, text in mine.items():
            for o in texts:
                if _imports(texts[o], Path(n).stem):
                    stays[n] = "%s/%s imports it" % (d, o)
                elif _imports(text, Path(o).stem):
                    stays[n] = "it imports %s/%s, which is no part of the old toolkit" % (d, o)
        changed = True
        while changed:
            changed = False
            for n, text in mine.items():
                if n in stays:
                    continue
                for k in list(stays):
                    if k in mine and _imports(mine[k], Path(n).stem):
                        stays[n] = "%s/%s stays and imports it" % (d, k)
                        changed = True
                        break
        for n in files:
            found["%s/%s" % (d, n)] = stays.get(n, "")
        cache = base / "__pycache__"
        if cache.is_dir():
            gone = {Path(n).stem for n in files if n.endswith(".py") and n not in stays}
            for p in cache.iterdir():
                if p.name.split(".")[0] in gone:
                    found[_rel(root, p)] = ""
    return found


# ── hooks ───────────────────────────────────────────────────────────────────

def _old_hook(cmd, root, engine):
    """Whether the hook line `cmd` runs a script of the old toolkit."""
    if not isinstance(cmd, str):
        return False
    norm = cmd.replace("\\", "/").lower()
    for script in OLD_SCRIPTS:
        if script.lower() not in norm:
            continue
        if script.endswith("/") or script in engine or not (root / script).exists():
            return True
    return False


def _hook_files(root, engine):
    """{project path: (the settings dict without the old hook entries, [the entries])}."""
    out = {}
    for rel in HOOK_FILES:
        d = _json(root / rel)
        hooks = d.get("hooks") if isinstance(d, dict) else None
        if not isinstance(hooks, dict):
            continue
        dropped = []
        for ev in list(hooks):
            groups = hooks[ev]
            if not isinstance(groups, list):
                continue
            keep_groups = []
            for g in groups:
                inner = g.get("hooks") if isinstance(g, dict) else None
                if not isinstance(inner, list):
                    keep_groups.append(g)
                    continue
                keep = [h for h in inner
                        if not (isinstance(h, dict) and _old_hook(h.get("command"), root, engine))]
                dropped += ["%s: %s" % (ev, h.get("command")) for h in inner if h not in keep]
                if keep:
                    keep_groups.append(dict(g, hooks=keep))
            if keep_groups:
                hooks[ev] = keep_groups
            else:
                hooks.pop(ev)
        if dropped:
            if not hooks:
                d.pop("hooks")
            out[rel] = (d, dropped)
    return out


# ── config ──────────────────────────────────────────────────────────────────

def _shipped():
    """{name: title} of every ruleset and pack Octopus AI ships, and the old names of packs."""
    out = {}
    for f in sorted((_project.CORE / "rulesets").glob("*.json")):
        out[f.stem] = (_json(f) or {}).get("title", "")
    for d in sorted(_project.PACKS.iterdir()) if _project.PACKS.is_dir() else ():
        f = d / "ruleset.json"
        if f.is_file():
            out[d.name] = (_json(f) or {}).get("title", "")
    for old in _project.PACK_ALIASES:
        out.setdefault(old, "")
    return out


def _own_rulesets(root, cfg, rulesets_dir):
    """[(name, its file, its data)] of the project's own rulesets in an old config: each name
    that is no shipped ruleset, and whose title is no shipped title."""
    shipped = _shipped()
    titles = {t for t in shipped.values() if t}
    out = []
    for name in (cfg or {}).get("rulesets", []):
        f = rulesets_dir / ("%s.json" % name)
        data = _json(f) if f.is_file() else None
        if name in shipped or data is None or data.get("title") in titles:
            continue
        out.append((name, f, data))
    return out


def _config(root, pj):
    """Items for the old configs: `TODO/project.json` with `TODO/rulesets/`, and `.aitools/`."""
    items = []
    todo = root / (pj.get("todoDir") or "TODO")
    own_dir = root / CONFIG_DIR / "rulesets"
    for cfg_path, rs_dir, whole in ((todo / "project.json", todo / "rulesets", None),
                                    (root / OLD_CONFIG / "project.json",
                                     root / OLD_CONFIG / "rulesets", root / OLD_CONFIG)):
        if not cfg_path.is_file() and not (whole and whole.is_dir()):
            continue
        blocked = []
        for name, f, data in _own_rulesets(root, _json(cfg_path), rs_dir):
            dst = own_dir / f.name
            bad = [k for k in data if k not in _project.RULESET_KEYS]
            if bad:
                blocked.append(name)
                items.append(Item("config", _rel(root, f), "keep",
                                  "keys Octopus AI does not know: %s" % ", ".join(bad)))
            elif dst.is_file() and _json(dst) != data:
                blocked.append(name)
                items.append(Item("config", _rel(root, f), "keep",
                                  "%s holds other content" % _rel(root, dst)))
            else:
                items.append(Item("config", _rel(root, f), "move",
                                  "own ruleset %r: to %s, listed in %s/project.json"
                                  % (name, _rel(root, dst), CONFIG_DIR),
                                  name=name, data=data, dst=_rel(root, dst)))
        if whole is not None:
            if not blocked:
                items.append(Item("config", OLD_CONFIG + "/", "remove", "replaced by %s/"
                                  % CONFIG_DIR))
            continue
        if not blocked:
            items.append(Item("config", _rel(root, cfg_path), "remove",
                              "replaced by %s/project.json" % CONFIG_DIR))
            if rs_dir.is_dir():
                items.append(Item("config", _rel(root, rs_dir) + "/", "remove",
                                  "replaced by %s/rulesets/" % CONFIG_DIR))
    return items


def _old_payload(root, current):
    """Items for the payload the `.aitools/` record lists, and `.claude/rules/aitools/`."""
    items, seen = [], set()
    rec = _json(root / OLD_CONFIG / "installed.json") or {}
    names = {r.rsplit("/", 1)[-1] for r in current if r.startswith(".agents/")}
    for rel, v in sorted((rec.get("files") or {}).items()):
        # A file outside `.claude/` is a payload destination of a pack now, and stays.
        if not rel.startswith(".claude/") or rel in current or not (root / rel).is_file():
            continue
        seen.add(rel)
        same = hashlib.sha256((root / rel).read_bytes()).hexdigest() == (v or {}).get("hash")
        if same or rel.rsplit("/", 1)[-1] in names:
            items.append(Item("payload", rel, "remove", "old payload; its equivalent is in "
                                                       ".agents/"))
        else:
            items.append(Item("payload", rel, "keep", "old payload, edited since the old "
                                                      "install, and .agents/ has no file of its name"))
    base = root / OLD_PAYLOAD_DIR
    if base.is_dir():
        for p in sorted(base.rglob("*")):
            rel = _rel(root, p)
            if p.is_file() and rel not in seen:
                items.append(Item("payload", rel, "remove", "old payload"))
    return items


def _old_claude_copies(root, current):
    """Items for an old Claude copy of a payload rule or skill: `.claude/skills/<name>/` or
    `.claude/rules/<name>.md` whose name the payload in `.agents/` has, that is no generated
    pointer, and whose text names the old toolkit."""
    items = []
    skills = {r.split("/")[2] for r in current
              if r.startswith(".agents/skills/") and current[r].get("by") != "agents"}
    rules = {r.rsplit("/", 1)[-1] for r in current if r.startswith(".agents/rules/octopus/")}
    sk = root / ".claude" / "skills"
    for name in sorted(skills):
        d = sk / name
        f = d / "SKILL.md"
        if not d.is_dir() or "%s/SKILL.md" % _rel(root, d) in current:
            continue
        texts = [_read(p) or "" for p in d.rglob("*") if p.is_file()]
        if any(OLD_TEXT.search(t) for t in texts) and "octopus:skill-pointer" not in (_read(f) or ""):
            items.append(Item("rule", _rel(root, d) + "/", "remove",
                              "old copy of the skill .agents/skills/%s" % name))
    rd = root / ".claude" / "rules"
    for p in sorted(rd.glob("*.md")) if rd.is_dir() else ():
        if p.name in rules and _rel(root, p) not in current and OLD_TEXT.search(_read(p) or ""):
            items.append(Item("rule", _rel(root, p), "remove",
                              "old copy of the rule %s" % p.name))
    return items


def _ignore_lines(root):
    """Items for each ignore file line that names `.aitools/` or `.claude/rules/aitools/`,
    once both are gone."""
    items = []
    for rel in IGNORE_FILES:
        text = _read(root / rel)
        if not text:
            continue
        lines = text.splitlines(keepends=True)
        drop = [i for i, ln in enumerate(lines) if OLD_IGNORE.search(ln.strip().lstrip("!"))]
        if not drop:
            continue
        for i in list(drop):
            j = i - 1
            while j >= 0 and j not in drop and OLD_COMMENT.match(lines[j]):
                drop.append(j)
                j -= 1
        # A group that goes whole between two blank lines takes the blank line before it.
        drop = set(drop)
        for s in sorted(i for i in drop if i - 1 not in drop):
            e = s
            while e + 1 in drop:
                e += 1
            if s > 0 and not lines[s - 1].strip() and (e + 1 >= len(lines)
                                                      or not lines[e + 1].strip()):
                drop.add(s - 1)
        drop = sorted(drop)
        items.append(Item("ignore", rel, "rewrite", "lines that name the old toolkit's places",
                          lines=[(i + 1, lines[i].rstrip("\r\n"), None) for i in drop]))
    return items


# ── references in the text ──────────────────────────────────────────────────

_BEFORE = r"(?<![\w.\-/\\])"


def _command_rules(root, pj, gone, removed, current):
    """[(regex, replacement)] for each old command whose script goes or is absent, for the old
    config paths once they go, and for each old Claude copy of a payload rule that is gone."""
    out = []
    for d, name, nd, new in COMMANDS:
        rel = "%s/%s" % (d, name)
        if rel in gone or not (root / rel).is_file():
            pat = re.compile(_BEFORE + r"%s([/\\])%s(?![\w.])" % (re.escape(d), re.escape(name)))
            out.append((pat, lambda m, nd=nd, new=new: "%s%s%s" % (nd, m.group(1), new)))
    todo = pj.get("todoDir") or "TODO"
    for old, new in (("%s/project.json" % todo, "%s/project.json" % CONFIG_DIR),
                     ("%s/rulesets/" % todo, "%s/rulesets/" % CONFIG_DIR)):
        if old in removed or not (root / old).exists():
            out.append((re.compile(_BEFORE + re.escape(old) + r"(?![\w.\-])"),
                        lambda m, new=new: new))
    rules = {}
    for r in sorted(current):
        if r.startswith(".agents/rules/octopus/"):
            rules.setdefault(r.rsplit("/", 1)[-1], r)
    if rules:
        names = "|".join(re.escape(n) for n in sorted(rules, key=len, reverse=True))
        pat = re.compile(_BEFORE + r"\.claude/rules/(?:aitools/[\w-]+/)?(%s)(?![\w.\-])" % names)

        def rule(m):
            gone_rule = m.group(0) in removed or not (root / m.group(0)).exists()
            return rules[m.group(1)] if gone_rule else m.group(0)
        out.append((pat, rule))
    return out


def _text_files(root, skip):
    files = oldnames.tracked(root)
    if not files:
        files = {_rel(root, p) for p in root.rglob("*") if p.is_file()
                 and not oldnames.PRUNE.intersection(_rel(root, p).split("/")[:-1])}
    for rel in sorted(files):
        parts = rel.split("/")
        if parts[0] in skip or oldnames.PRUNE.intersection(parts[:-1]):
            continue
        if Path(rel).suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield rel


def _readme(rel):
    name = rel.rsplit("/", 1)[-1]
    return "/" not in rel and (name.lower() == "readme" or name.lower().startswith("readme.")
                               or name.startswith("README"))


def _references(root, pj, gone, removed, current):
    rules = _command_rules(root, pj, gone, removed, current)
    todo = pj.get("todoDir") or "TODO"
    items = []
    for rel in _text_files(root, {todo, ".git", CONFIG_DIR, OLD_CONFIG}):
        if rel in removed or rel in current or rel in HOOK_FILES \
                or any(rel.startswith(r) for r in removed if r.endswith("/")):
            continue
        text = _read(root / rel)
        if not text:
            continue
        lines = []
        for n, line in enumerate(text.splitlines(), 1):
            new = line
            for pat, rep in rules:
                new = pat.sub(rep, new)
            if new != line:
                lines.append((n, line, new))
        if lines:
            items.append(Item("reference", rel, "readme" if _readme(rel) else "rewrite",
                              "names a command or a path of the old toolkit", lines=lines))
    return items


# ── find and clean ──────────────────────────────────────────────────────────

def find(root):
    """[Item] of every leftover of the old toolkits in the project at `root`. Empty for a
    directory with no `.octopus/project.json`."""
    root = Path(root).resolve()
    pj = _json(root / CONFIG_DIR / "project.json")
    if not isinstance(pj, dict) or root == _project.OCTOPUS.resolve():
        return []
    current = (_json(root / CONFIG_DIR / "installed.json") or {}).get("files") or {}
    items = []
    engine = _engine(root)
    for rel, why in sorted(engine.items()):
        items.append(Item("engine", rel, "keep" if why else "remove",
                          why or "replaced by the Octopus AI engine"))
    for rel, (_d, dropped) in _hook_files(root, engine).items():
        items.append(Item("hook", rel, "rewrite",
                          "replaced by the Octopus AI hook line: " + "; ".join(dropped),
                          entries=dropped))
    items += _config(root, pj)
    items += _old_payload(root, current)
    items += _old_claude_copies(root, current)
    removed = {i["path"] for i in items if i["action"] == "remove"}
    stays = [p for p in (OLD_CONFIG + "/", OLD_PAYLOAD_DIR + "/")
             if (root / p).exists() and p not in removed
             and not (p == OLD_PAYLOAD_DIR + "/" and not any(
                 i["path"].startswith(p) and i["action"] == "keep" for i in items))]
    if not stays:
        items += _ignore_lines(root)
    gone = {i["path"] for i in items if i["kind"] == "engine" and i["action"] == "remove"}
    if any(i["path"] == OLD_CONFIG + "/" and i["action"] == "remove" for i in items):
        gone.add("%s/todo.py" % OLD_CONFIG)
    items += _references(root, pj, gone, removed, current)
    return items


def _merge_sessions(root):
    """Append the rows of `.aitools/SESSIONS.md` that `.octopus/SESSIONS.md` lacks."""
    old, new = root / OLD_CONFIG / "SESSIONS.md", root / CONFIG_DIR / "SESSIONS.md"
    text = _read(old)
    if not text:
        return
    if not new.exists():
        _write(new, text)
        return
    have = set((_read(new) or "").splitlines())
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and ln not in have
            and not ln.startswith("| session-id") and not set(ln) <= set("|- ")]
    if rows:
        cur = _read(new) or ""
        _write(new, cur.rstrip("\r\n") + "\n" + "\n".join(rows) + "\n")


def clean(root, out=print, items=None):
    """Remove or replace each leftover `find` names, but each one that stays and each
    project-wide README file. Returns the items."""
    root = Path(root).resolve()
    items = find(root) if items is None else items
    if not items:
        return items
    pj_path = root / CONFIG_DIR / "project.json"
    hooks = _hook_files(root, _engine(root))
    for it in items:
        rel, act = it["path"], it["action"]
        if act in ("keep", "readme"):
            continue
        if it["kind"] == "hook":
            d = hooks.get(rel, (None,))[0]
            if d is not None:
                _write(root / rel, _json_text(d))
        elif it["kind"] == "config" and act == "move":
            dst = root / it["dst"]
            if not dst.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                _write(dst, _json_text(it["data"]))
            pj = _json(pj_path) or {}
            if it["name"] not in pj.setdefault("rulesets", []):
                pj["rulesets"].append(it["name"])
                _write(pj_path, _json_text(pj))
        elif act == "remove":
            if rel == OLD_CONFIG + "/":
                _merge_sessions(root)
            _remove(root, rel.rstrip("/"))
        elif act == "rewrite" and it.get("lines"):
            text = _read(root / rel) or ""
            lines = text.splitlines(keepends=True)
            for n, old, new in sorted(it["lines"], reverse=True):
                end = lines[n - 1][len(lines[n - 1].rstrip("\r\n")):]
                if new is None:
                    del lines[n - 1]
                else:
                    lines[n - 1] = new + end
            _write(root / rel, "".join(lines))
        out("  %-7s %s  (%s)" % (act, rel, it["why"]))
    for it in items:
        if it["action"] in ("keep", "readme"):
            out("  %-7s %s  (%s)" % (it["action"], it["path"],
                                     it["why"] if it["action"] == "keep"
                                     else "a project-wide README file: the owner approves the new "
                                          "text first"))
    return items


def clean_hooks(root, rec=None, out=print):
    """Drop only the old hook entries. The install record `rec` keeps the hash of a settings
    file it wrote in step. Returns the project paths of the files changed."""
    root = Path(root).resolve()
    if not (root / CONFIG_DIR / "project.json").is_file() or root == _project.OCTOPUS.resolve():
        return []
    files = _hook_files(root, _engine(root))
    s = (rec.settings or {}) if rec is not None else {}
    for rel, (d, dropped) in files.items():
        before = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        _write(root / rel, _json_text(d))
        after = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for r, rs in ((".claude/settings.json", s), (".claude/settings.local.json",
                                                     s.get("local") or {})):
            if rel == r and rs and rs.get("written") == before:
                rs["written"] = after
        out("  rewrite %s  (old hook lines: %s)" % (rel, "; ".join(dropped)))
    return sorted(files)


def report(items, out=print):
    """One line per item, and for a rewrite each line it changes. Returns the count."""
    for it in items:
        out("%-7s %-9s %s  (%s)" % (it["action"], it["kind"], it["path"], it["why"]))
        for n, old, new in it.get("lines") or ():
            out("        line %d: %s" % (n, old.strip()))
            if new is not None:
                out("             -> %s" % new.strip())
    out("%d leftover(s) of the old toolkit" % len(items))
    return len(items)
