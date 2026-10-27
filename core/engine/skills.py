"""skills.py - install a skill into one project from a git repository, a folder or a skill library.

    octopus skills <path> list                       the skills installed this way: source, version
    octopus skills <path> libraries                  the libraries a skill can come from
    octopus skills <path> available <library|url|folder> [--ref R] [--path P] [--refresh]
    octopus skills <path> add (--library L | --git URL | --folder DIR) [--ref R] [--path P]
                              [<skill> ...] [--all]
    octopus skills <path> update [<skill> ...] [--force]     every installed skill when none is named
    octopus skills <path> remove <skill> ... [--force]

WHERE A SKILL GOES. Into `.agents/skills/<name>/`, the one copy every agent reads (`agents.py`);
`admin.write_entries` then gives Claude Code its pointer in `.claude/skills/` and lists the skill
in `AGENTS.md`, so every AI provider that reads skills sees it at once. Each file is in the
install record (`.octopus/installed.json`) with the owner `skill:<name>`; the source is in
`.octopus/skills.json`:

    {"skills": {"<name>": {"source": "git" | "folder" | "library", "library", "url", "ref",
                           "path", "folder", "commit", "version", "installed", "updated"}}}

`path` is the skill's directory inside its source; `version` is the `version` of its frontmatter.

THE SOURCES. A skill is any directory of the source that holds a `SKILL.md`, named by that
directory; two of one name are told apart by their path. The libraries:

    claude            the Claude skill library            github.com/anthropics/skills
    claude-plugins    the Claude Code plugin marketplace  github.com/anthropics/claude-plugins-official
    codex             the Codex skill library             github.com/openai/skills
    marketplace:<m>   a marketplace Claude Code has on this machine, read where it is

A git source is cloned once into `<home>/cache/skills/` and fetched again by `--refresh`, `add`
and `update`. A library clone is shallow.

THE RULES. A skill directory the project already has and this record did not write is refused,
whether the project or a pack wrote it. `update` and `remove` refuse a skill whose files were
edited since they were written, unless `--force`. `update` removes a file the source no longer
ships.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import admin as _admin
import agents as _agents
import gitsrc
import project as _project

RECORD = "skills.json"
OWNER = "skill:"
LIBRARIES = {
    "claude": {"title": "the Claude skill library",
               "url": "https://github.com/anthropics/skills.git", "path": ""},
    "claude-plugins": {"title": "the Claude Code plugin marketplace",
                       "url": "https://github.com/anthropics/claude-plugins-official.git",
                       "path": ""},
    "codex": {"title": "the Codex skill library",
              "url": "https://github.com/openai/skills.git", "path": "skills"},
}
_SKIP = {".git", "node_modules", "__pycache__"}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ── the libraries ───────────────────────────────────────────────────────────

def _marketplaces():
    """name -> directory of every plugin marketplace Claude Code has on this machine."""
    f = _admin._claude_home() / "plugins" / "known_marketplaces.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for name, v in (data.items() if isinstance(data, dict) else []):
        loc = isinstance(v, dict) and v.get("installLocation")
        if loc and Path(loc).is_dir():
            out[name] = Path(loc)
    return out


def libraries():
    """`[{name, title, url | folder, path}]`: the libraries a skill can be installed from."""
    out = [dict(v, name=k) for k, v in LIBRARIES.items()]
    for name, d in sorted(_marketplaces().items()):
        out.append({"name": "marketplace:%s" % name, "folder": str(d), "path": "",
                    "title": "the Claude Code marketplace %s on this machine" % name})
    return out


def _library(name):
    for lib in libraries():
        if lib["name"] == name:
            return lib
    raise ValueError("no library %r; one of %s" % (name, ", ".join(l["name"] for l in libraries())))


def _cache(key):
    return _project.home() / "cache" / "skills" / key


def _git_key(url):
    return "git-%s" % hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:12]


def resolve(library=None, git=None, folder=None, ref=None, refresh=False, fetch=True):
    """The tree a source names, fetched when it is git: `(tree, spec)`. `spec` holds what the
    record keeps of the source: source, library, url, ref, folder, commit."""
    if sum(bool(x) for x in (library, git, folder)) != 1:
        raise ValueError("name exactly one source: a library, a git URL or a folder")
    if library:
        lib = _library(library)
        if lib.get("folder"):
            d = Path(lib["folder"])
            return d, {"source": "library", "library": library, "commit": _commit_of(d),
                       "base": lib.get("path", "")}
        tree = _cache(library)
        if fetch and (refresh or not (tree / ".git").exists() or ref):
            sha = gitsrc.fetch(lib["url"], tree, ref, shallow=not ref)
        else:
            sha = _commit_of(tree)
        return tree, {"source": "library", "library": library, "url": lib["url"],
                      "ref": ref or "", "commit": sha, "base": lib.get("path", "")}
    if git:
        tree = _cache(_git_key(git))
        if fetch and (refresh or not (tree / ".git").exists() or ref is not None):
            sha = gitsrc.fetch(git, tree, ref)
        else:
            sha = _commit_of(tree)
        return tree, {"source": "git", "url": git, "ref": ref or "", "commit": sha, "base": ""}
    d = Path(folder).expanduser().resolve()
    if not d.is_dir():
        raise ValueError("%s is not a folder" % folder)
    if ref:
        raise ValueError("a ref needs a git URL; a folder is read as it is")
    return d, {"source": "folder", "folder": str(d), "commit": _commit_of(d), "base": ""}


def _commit_of(d):
    try:
        return gitsrc.commit(d)
    except (ValueError, OSError):
        return ""


# ── the skills of a tree ────────────────────────────────────────────────────

def _fm(text):
    keys = {}
    for line in _agents.frontmatter(text).splitlines():
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if m:
            keys[m.group(1)] = m.group(2).strip().strip("'\"")
    return keys


def find(tree, sub=""):
    """Every skill under `tree/sub`: `[{name, path, description, version}]`, `path` relative
    to `tree`. A skill's own directory is not searched for more."""
    tree = Path(tree)
    top = tree / sub if sub else tree
    if not top.is_dir():
        raise ValueError("the source has no directory %s" % (sub or "."))
    out = []
    for d, dirs, files in os.walk(top):
        dirs[:] = sorted(x for x in dirs if x not in _SKIP)
        if "SKILL.md" in files:
            here = Path(d)
            keys = _fm((here / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
            name = here.name if here != tree else (keys.get("name") or tree.name)
            rel = here.relative_to(tree).as_posix()
            out.append({"name": name, "path": "" if rel == "." else rel,
                        "description": keys.get("description", ""),
                        "version": keys.get("version", "")})
            dirs[:] = []
    return out


def select(found, wanted, every=False):
    """The skills of `found` that `wanted` names, each by its path or its name. With none
    named: the one skill, or every skill with `every`."""
    if not wanted:
        if every or len(found) == 1:
            return list(found)
        if not found:
            raise ValueError("the source holds no skill")
        raise ValueError("the source holds %d skills; name the ones to install, or pass --all"
                         % len(found))
    out = []
    for w in wanted:
        w = w.strip().strip("/")
        hit = [s for s in found if s["path"] == w] or [s for s in found if s["name"] == w]
        if not hit:
            raise ValueError("the source has no skill %r" % w)
        if len(hit) > 1:
            raise ValueError("%d skills are called %s; name one by its path: %s"
                             % (len(hit), w, ", ".join(s["path"] for s in hit)))
        if hit[0] not in out:
            out.append(hit[0])
    return out


def available(root, library=None, git=None, folder=None, ref=None, path=None, refresh=False):
    """The skills a source offers, each with `installed` when the project has it."""
    tree, spec = resolve(library, git, folder, ref, refresh)
    have = installed(root)
    sub = _join(spec.get("base"), path)
    rows = find(tree, sub)
    for s in rows:
        s["installed"] = s["name"] in have
    return {"source": {k: v for k, v in spec.items() if k != "base"}, "skills": rows}


def _join(*parts):
    return "/".join(p.strip("/\\").replace("\\", "/") for p in parts if p and p.strip("/\\"))


# ── the project's record ────────────────────────────────────────────────────

def _record_path(root):
    return _project.config_path(root, RECORD)


def installed(root):
    """name -> the record of every skill installed this way."""
    d = _admin._read_json(_record_path(root), {})
    s = d.get("skills", {}) if isinstance(d, dict) else {}
    return s if isinstance(s, dict) else {}


def _save(root, skills):
    p = _record_path(root)
    if not skills:
        p.unlink(missing_ok=True)
        return
    _admin._write_bytes(p, _admin._json_bytes({"skills": dict(sorted(skills.items()))}))


def _config(root):
    root = Path(root).resolve()
    if not _project.has_config(root):
        raise ValueError("%s has no %s/%s" % (root, _project.CONFIG_DIR, _project.PROJECT_FILE))
    return root


def _dest(name):
    return "%s/%s" % (_agents.SKILLS, name)


def _owned(rec, name):
    return sorted(r for r, v in rec.files.items() if v.get("by") == OWNER + name)


def _edited(root, rec, name):
    return [r for r in _owned(rec, name) if (root / r).exists()
            and _admin._sha((root / r).read_bytes()) != rec.files[r]["hash"]]


def _files(tree, skill):
    src = tree / skill["path"] if skill["path"] else tree
    return [(f, f.relative_to(src).as_posix()) for f in sorted(src.rglob("*"))
            if f.is_file() and not (_SKIP & set(f.relative_to(src).parts))]


def _write(root, rec, tree, skill, name):
    """Copy the skill into `.agents/skills/<name>/`. Returns (written, removed)."""
    shipped, written = set(), 0
    for f, rel in _files(tree, skill):
        dst = "%s/%s" % (_dest(name), rel)
        shipped.add(dst)
        if rec.put(dst, f.read_bytes(), OWNER + name, replace=True) == "written":
            written += 1
    removed = 0
    for rel in _owned(rec, name):
        if rel not in shipped:
            (root / rel).unlink(missing_ok=True)
            del rec.files[rel]
            removed += 1
    _prune(root, name)
    return written, removed


def _prune(root, name):
    top = root / _dest(name)
    if not top.is_dir():
        return
    for d in sorted((p for p in top.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(d.iterdir()):
            d.rmdir()
    if not any(top.iterdir()):
        top.rmdir()


def _finish(root, rec, out):
    _admin.write_entries(root, rec, out=out)
    rec.save()


# ── add, update, remove ─────────────────────────────────────────────────────

def add(root, names=(), library=None, git=None, folder=None, ref=None, path=None, every=False,
        refresh=True, out=print):
    """Install the named skills of a source. Returns the names installed."""
    root = _config(root)
    tree, spec = resolve(library, git, folder, ref, refresh)
    picked = select(find(tree, _join(spec.get("base"), path)), list(names), every)
    have = installed(root)
    rec = _admin.Record(root)
    for s in picked:
        n = s["name"]
        twins = [x["path"] for x in picked if x["name"] == n]
        if len(twins) > 1:
            raise ValueError("%d of the chosen skills are called %s; choose one: %s"
                             % (len(twins), n, ", ".join(twins)))
        if not NAME.match(n):
            raise ValueError("the skill directory %r is no usable name" % n)
        if n in have:
            raise ValueError("%s is installed already, from %s; update it, or remove it first"
                             % (n, _where(have[n])))
        if (root / _dest(n)).exists():
            owner = next((v["by"] for r, v in rec.files.items()
                          if r.startswith(_dest(n) + "/")), "")
            raise ValueError("%s already holds a skill %s%s; it is not replaced"
                             % (_agents.SKILLS, n, (" (from %s)" % owner) if owner else
                                " of the project's own"))
    now = time.strftime("%Y-%m-%d %H:%M")
    for s in picked:
        _write(root, rec, tree, s, s["name"])
        row = {k: v for k, v in spec.items() if k != "base" and v}
        row.update(path=s["path"], version=s["version"], installed=now)
        have[s["name"]] = row
        out("installed skill %s from %s%s" % (s["name"], _where(row), _ver(row)))
    _save(root, have)
    _finish(root, rec, out)
    return [s["name"] for s in picked]


def _where(row):
    if row.get("library"):
        return "%s %s" % (row["library"], row.get("path") or "")
    return ("%s %s" % (row.get("url") or row.get("folder", ""), row.get("path") or "")).strip()


def _ver(row):
    bits = [x for x in (row.get("version"), (row.get("commit") or "")[:10]) if x]
    return (" (%s)" % ", ".join(bits)) if bits else ""


def update(root, names=(), force=False, ref=None, out=print):
    """Fetch the source of each named skill (every installed one when none is named) again,
    and rewrite its files. Returns the names updated."""
    root = _config(root)
    have = installed(root)
    names = list(names) or sorted(have)
    for n in names:
        if n not in have:
            raise ValueError("%s was not installed from a source; `skills list` shows those" % n)
    rec = _admin.Record(root)
    if not force:
        bad = {n: _edited(root, rec, n) for n in names}
        bad = {n: f for n, f in bad.items() if f}
        if bad:
            raise ValueError("these skills were edited since they were installed; pass --force to "
                             "overwrite: %s" % "; ".join("%s (%s)" % (n, ", ".join(f))
                                                        for n, f in bad.items()))
    done, fetched = [], {}
    for n in names:
        row = have[n]
        key = (row.get("library"), row.get("url"), row.get("folder"),
               row.get("ref") if ref is None else ref)
        if key not in fetched:
            git = row.get("url") if row.get("source") == "git" else None
            fetched[key] = resolve(row.get("library"), git, row.get("folder"),
                                   key[3] or None, refresh=True)
        tree, spec = fetched[key]
        found = [s for s in find(tree, _join(spec.get("base"))) if s["path"] == row.get("path", "")]
        if not found:
            raise ValueError("%s: the source has no skill at %s any more" % (n, row.get("path")))
        written, removed = _write(root, rec, tree, found[0], n)
        old = row.get("commit", "")
        row.update(commit=spec.get("commit", ""), version=found[0]["version"],
                   ref=key[3] or "", updated=time.strftime("%Y-%m-%d %H:%M"))
        row = {k: v for k, v in row.items() if v}
        have[n] = row
        out("skill %s: %s%s" % (n, "%d file(s) written, %d removed" % (written, removed)
                                if written or removed else "the same", _ver(row)
                                if row.get("commit") != old else ""))
        done.append(n)
    _save(root, have)
    _finish(root, rec, out)
    return done


def remove(root, names, force=False, out=print):
    """Delete each named skill's files and its record. Returns the names removed."""
    root = _config(root)
    have = installed(root)
    if not names:
        raise ValueError("name the skills to remove")
    rec = _admin.Record(root)
    for n in names:
        if n not in have:
            raise ValueError("%s was not installed from a source; `skills list` shows those" % n)
        bad = _edited(root, rec, n)
        if bad and not force:
            raise ValueError("%s was edited since it was installed (%s); pass --force to remove it"
                             % (n, ", ".join(bad)))
    for n in names:
        for rel in _owned(rec, n):
            (root / rel).unlink(missing_ok=True)
            del rec.files[rel]
        _prune(root, n)
        del have[n]
        out("removed skill %s" % n)
    _save(root, have)
    _finish(root, rec, out)
    return list(names)


def listing(root):
    """`[{name, ..., state}]`: each installed skill and its record; `state` is `same`,
    `edited` or `missing`."""
    root = _config(root)
    rec = _admin.Record(root)
    rows = []
    for n, row in sorted(installed(root).items()):
        files = _owned(rec, n)
        state = "missing" if not files or any(not (root / r).exists() for r in files) else \
            "edited" if _edited(root, rec, n) else "same"
        rows.append(dict(row, name=n, state=state, where=_where(row)))
    return rows


# ── the command line ────────────────────────────────────────────────────────

def main(argv, out=print):
    import argparse
    ap = argparse.ArgumentParser(prog="octopus skills",
                                 description="the skills a project installed from a source")
    ap.add_argument("path", help="the project: a registered name or a directory")
    sub = ap.add_subparsers(dest="op")
    sub.add_parser("list")
    sub.add_parser("libraries")

    def source(p, positional=False):
        if positional:
            p.add_argument("source", help="a library name, a git URL or a folder")
        else:
            g = p.add_mutually_exclusive_group(required=True)
            g.add_argument("--library")
            g.add_argument("--git", metavar="URL")
            g.add_argument("--folder")
        p.add_argument("--ref", help="a branch, a tag or a commit of a git source")
        p.add_argument("--path", dest="subpath", help="the directory inside the source")
    av = sub.add_parser("available")
    source(av, positional=True)
    av.add_argument("--refresh", action="store_true", help="fetch the source again")
    ad = sub.add_parser("add")
    source(ad)
    ad.add_argument("skills", nargs="*", help="the skills, by name or by path")
    ad.add_argument("--all", action="store_true", help="every skill of the source")
    up = sub.add_parser("update")
    up.add_argument("skills", nargs="*")
    up.add_argument("--ref")
    up.add_argument("--force", action="store_true")
    rm = sub.add_parser("remove")
    rm.add_argument("skills", nargs="+")
    rm.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    root = _project.resolve(a.path) if not Path(a.path).is_dir() else Path(a.path)
    if a.op in (None, "list"):
        rows = listing(root)
        for r in rows:
            out("%-28s %-8s %s%s" % (r["name"], r["state"], r["where"], _ver(r)))
        if not rows:
            out("no skill installed from a source")
        return 0
    if a.op == "libraries":
        for lib in libraries():
            out("%-28s %s: %s" % (lib["name"], lib["title"], lib.get("url") or lib.get("folder")))
        return 0
    if a.op == "available":
        s = a.source
        kw = {"library": s} if s in [l["name"] for l in libraries()] else \
            {"git": s} if gitsrc.is_url(s) else {"folder": s}
        r = available(root, ref=a.ref, path=a.subpath, refresh=a.refresh, **kw)
        for x in r["skills"]:
            out("%s %-40s %s" % ("*" if x["installed"] else " ", x["path"] or x["name"],
                                 x["description"][:90]))
        out("%d skill(s); * installed" % len(r["skills"]))
        return 0
    if a.op == "add":
        add(root, a.skills, a.library, a.git, a.folder, a.ref, a.subpath, a.all, out=out)
    elif a.op == "update":
        update(root, a.skills, a.force, a.ref, out=out)
    elif a.op == "remove":
        remove(root, a.skills, a.force, out=out)
    return 0
