"""packreg.py - the packs a project can choose: the ones Octopus AI ships, and the ones added here.

    octopus pack-registry list                                   every available pack
    octopus pack-registry add <folder|url> [--name N] [--ref R] [--path P]
    octopus pack-registry update <name>                          fetch again, then update every
                                                                 project that lists it
    octopus pack-registry remove <name>

THE REGISTRY. `packs/` holds the packs Octopus AI ships (`builtin`). A pack added on this machine
is a row of `<home>/packs.json` (`project.pack_registry_file()`), outside the tree, because it
names a machine path:

    {"name", "source": "folder", "path", "added"}                          used in place
    {"name", "source": "git", "url", "ref", "subpath", "commit", "added"}  cloned into
                                                                           <home>/packs/<name>/repo

A git pack is `<clone>/<subpath>`. `ref` is a branch, a tag or a commit; with none, the default
branch. `update` fetches it again and checks `ref` out; a branch moves, a tag or a commit stays.

THE CONTRACT. `validate` holds a pack to `packs/PACKS.md`: a `ruleset.json` that loads with no
problem after the rulesets it requires (a JSON object, a `title`, only known keys, each list a
list of strings, each permit complete, no permit another ruleset defines); every payload entry
with a `from` that exists and a relative `to` that stays in the project; a file for every
`brief` section; a known pack for every `requires`; a `SKILL.md` with `name` and `description`
in every directory of `skills/`. A pack that fails is not added, and an update that fails is
undone.
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import time
from pathlib import Path

import admin as _admin
import agents as _agents
import gitsrc
import project as _project

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# ── the registry file ───────────────────────────────────────────────────────

def _rows():
    try:
        with io.open(_project.pack_registry_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    rows = data.get("packs", []) if isinstance(data, dict) else []
    return [r for r in rows if isinstance(r, dict) and r.get("name")]


def _save(rows):
    p = _project.pack_registry_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"packs": rows}, indent=1) + "\n")
    os.replace(tmp, p)


def _builtin():
    return [d.name for d in sorted(_project.PACKS.iterdir()) if (d / "ruleset.json").is_file()]


def users(name):
    """The names of the registered projects whose rulesets list `name`."""
    out = []
    for r in _project.load_registry():
        cfg = _admin._read_json(Path(r["path"]) / _project.config_dir(r["path"])
                                / _project.PROJECT_FILE, {})
        if name in (cfg.get("rulesets") or []):
            out.append(r["name"])
    return out


def available():
    """Every pack, shipped first: `{name, title, requires, source, dir, users, ...}`, with the
    source's own keys (`path`, or `url`, `ref`, `subpath`, `commit`) and `problems` when its
    directory is gone."""
    out = []
    for n in _builtin():
        d = _project.PACKS / n
        out.append(dict(_describe(d), name=n, source="builtin", dir=str(d)))
    for r in _project.added_packs():
        row = {k: v for k, v in r.items() if k != "dir"}
        if r["dir"] is None:
            row.update(title="", requires=[], dir="",
                       problems=["the pack's directory is gone; update or remove it"])
        else:
            row.update(_describe(r["dir"]), dir=str(r["dir"]))
        out.append(row)
    for row in out:
        row["users"] = users(row["name"])
    return out


def _describe(d):
    data = _admin._read_json(Path(d) / "ruleset.json", {})
    return {"title": data.get("title", ""), "requires": list(data.get("requires", []))}


# ── the contract ────────────────────────────────────────────────────────────

def _fm_keys(text):
    keys = {}
    for line in _agents.frontmatter(text).splitlines():
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if m:
            keys[m.group(1)] = m.group(2).strip()
    return keys


def validate(d, name):
    """The problems of the pack in directory `d` under the name `name`; [] when it meets the
    contract of `packs/PACKS.md`."""
    d = Path(d)
    problems = []
    if not NAME.match(name or ""):
        problems.append("the name %r is not lower-case letters, digits, - and _" % name)
    rs = d / "ruleset.json"
    if not rs.is_file():
        return problems + ["%s holds no ruleset.json" % d]
    try:
        data = json.loads(rs.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return problems + ["ruleset.json is not valid JSON: %s" % exc]
    if not isinstance(data, dict):
        return problems + ["ruleset.json is not a JSON object"]
    if not isinstance(data.get("title"), str) or not data["title"].strip():
        problems.append("ruleset.json has no `title`")
    for e in data.get("payload", []) if isinstance(data.get("payload"), list) else []:
        if not isinstance(e, dict):
            continue
        src, to = e.get("from"), e.get("to")
        if isinstance(src, str) and not (d / src).exists():
            problems.append("payload: %s is not in the pack" % src)
        if isinstance(to, str) and (os.path.isabs(to) or to.startswith(("/", "\\"))
                                    or ".." in re.split(r"[\\/]", to)):
            problems.append("payload: %s leaves the project; `to` is project-relative" % to)
    skills = d / "skills"
    if skills.is_dir():
        for s in sorted(x for x in skills.iterdir() if x.is_dir()):
            md = s / "SKILL.md"
            if not md.is_file():
                problems.append("skills/%s has no SKILL.md" % s.name)
                continue
            keys = _fm_keys(md.read_text(encoding="utf-8", errors="replace"))
            for k in ("name", "description"):
                if not keys.get(k):
                    problems.append("skills/%s/SKILL.md has no `%s` in its frontmatter"
                                    % (s.name, k))
    # The ruleset itself, loaded as a project would load it: after the base rulesets and the
    # packs it requires. Only what this pack adds counts.
    tok = _project.with_candidate({name: d})
    try:
        try:
            chain = _admin.order_packs([name])
        except ValueError as exc:
            return problems + [str(exc)]
        base = _admin.base_rulesets()
        with tempfile.TemporaryDirectory(prefix="octopus-pack-") as tmp:
            def load(names):
                cfg = {"name": "pack-check", "slug": "pack-check", "rulesets": names}
                return _project.load_rules(Path(tmp), cfg)
            before = set(load(base + chain[:-1])[1])
            rules, after = load(base + chain)
        problems += [p.replace("packs/%s/ruleset.json" % name, "ruleset.json")
                     for p in after if p not in before]
        for sec, path in rules["brief"].items():
            if Path(path).parent.parent == d and not Path(path).is_file():
                problems.append("brief: section %s has no brief/%s.md" % (sec, sec))
    finally:
        _project.reset_candidate(tok)
    return problems


# ── add, update, remove ─────────────────────────────────────────────────────

def _taken(name):
    if (_project.CORE / "rulesets" / ("%s.json" % name)).is_file():
        return "a core ruleset is called %s" % name
    if name in _builtin():
        return "Octopus AI ships a pack called %s" % name
    if any(r["name"] == name for r in _rows()):
        return "a pack called %s is added already" % name
    return ""


def _default_name(source, subpath):
    tail = (subpath or "").strip("/\\").replace("\\", "/").split("/")[-1]
    if not tail:
        tail = re.split(r"[\\/:]", str(source).rstrip("/\\"))[-1]
        tail = tail[:-4] if tail.endswith(".git") else tail
    return re.sub(r"[^a-z0-9_-]+", "-", tail.lower()).strip("-")


def add(source, name=None, ref=None, subpath=None, out=print):
    """Add a pack from a folder (used in place) or a git URL (cloned). Returns its row."""
    source = str(source or "").strip()
    if not source:
        raise ValueError("name a folder or a git URL")
    subpath = (subpath or "").strip().strip("/\\").replace("\\", "/")
    is_git = gitsrc.is_url(source)
    if not is_git:
        folder = Path(source).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError("%s is not a folder and not a git URL" % source)
        if ref:
            raise ValueError("a ref needs a git URL; a folder is used as it is")
        if subpath:
            folder = folder / subpath
    name = (name or _default_name(source if is_git else folder, subpath if is_git else "")).strip()
    why = _taken(name)
    if why:
        raise ValueError(why)
    row = {"name": name, "added": time.strftime("%Y-%m-%d %H:%M")}
    if is_git:
        clone = _project.pack_clone_dir(name)
        if clone.exists():
            gitsrc.remove_tree(clone)
        sha = gitsrc.fetch(source, clone, ref)
        d = clone / subpath if subpath else clone
        row.update(source="git", url=source, ref=ref or "", subpath=subpath, commit=sha)
    else:
        d = folder
        row.update(source="folder", path=str(folder))
    problems = validate(d, name)
    if problems:
        if is_git:
            gitsrc.remove_tree(clone.parent)
        raise ValueError("%s does not meet the pack format: %s" % (name, "; ".join(problems)))
    _save(_rows() + [row])
    out("added pack %s from %s%s" % (name, source, (" at %s" % sha[:10]) if is_git else ""))
    return row


def _row(name):
    for r in _rows():
        if r["name"] == name:
            return r
    if name in _builtin():
        raise ValueError("%s ships with Octopus AI; it changes with Octopus AI itself" % name)
    raise ValueError("no added pack %r" % name)


def update(name, ref=None, projects=True, out=print):
    """Fetch a git pack again (at `ref` when given, else its own) or check a folder pack
    again, then run `update` in every registered project that lists it. Returns its row."""
    row = _row(name)
    if row.get("source") == "git":
        clone = _project.pack_clone_dir(name)
        old = gitsrc.commit(clone) if (clone / ".git").exists() else None
        want = row.get("ref", "") if ref is None else ref
        sha = gitsrc.fetch(row["url"], clone, want)
        d = clone / row["subpath"] if row.get("subpath") else clone
        problems = validate(d, name)
        if problems:
            if old:
                gitsrc.checkout(clone, old)
            raise ValueError("%s at %s does not meet the pack format, kept at %s: %s"
                             % (name, sha[:10], (old or "")[:10], "; ".join(problems)))
        row.update(ref=want, commit=sha)
        out("pack %s: %s" % (name, sha[:10] if sha != old else "already at %s" % sha[:10]))
    else:
        problems = validate(row.get("path", ""), name)
        if problems:
            raise ValueError("%s does not meet the pack format: %s" % (name, "; ".join(problems)))
        out("pack %s: %s" % (name, row.get("path")))
    row["updated"] = time.strftime("%Y-%m-%d %H:%M")
    _save([row if r["name"] == name else r for r in _rows()])
    if projects:
        for r in _project.load_registry():
            if r["name"] in users(name):
                _admin.update(Path(r["path"]), out=lambda s, n=r["name"]: out("  %s: %s" % (n, s)))
    return row


def remove(name, out=print):
    """Take an added pack off this machine: its row, and its clone. Refused while a
    registered project lists it."""
    row = _row(name)
    using = users(name)
    if using:
        raise ValueError("%s is in use by %s; remove it from each first" % (name, ", ".join(using)))
    _save([r for r in _rows() if r["name"] != name])
    if row.get("source") == "git":
        gitsrc.remove_tree(_project.pack_clone_dir(name).parent)
    out("removed pack %s" % name)


# ── the command line ────────────────────────────────────────────────────────

def main(argv, out=print):
    import argparse
    ap = argparse.ArgumentParser(prog="octopus pack-registry",
                                 description="the packs a project can choose")
    sub = ap.add_subparsers(dest="op")
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("source", help="a folder, or a git URL")
    a.add_argument("--name")
    a.add_argument("--ref", help="a branch, a tag or a commit of the git URL")
    a.add_argument("--path", dest="subpath", help="the pack's directory inside the source")
    u = sub.add_parser("update")
    u.add_argument("name")
    u.add_argument("--ref")
    u.add_argument("--no-projects", action="store_true",
                   help="do not update the projects that list it")
    r = sub.add_parser("remove")
    r.add_argument("name")
    a_ = ap.parse_args(argv)
    if a_.op in (None, "list"):
        for p in available():
            where = p.get("url") or p.get("path") or "packs/%s" % p["name"]
            ver = (" %s" % p["ref"] if p.get("ref") else "") + \
                  (" %s" % p["commit"][:10] if p.get("commit") else "")
            out("%-12s %-8s %s%s" % (p["name"], p["source"], where, ver))
            out("%12s %s" % ("", p.get("title", "")))
            if p.get("requires"):
                out("%12s requires %s" % ("", ", ".join(p["requires"])))
            if p["users"]:
                out("%12s used by %s" % ("", ", ".join(p["users"])))
            for x in p.get("problems", []):
                out("%12s problem: %s" % ("", x))
        return 0
    if a_.op == "add":
        add(a_.source, a_.name, a_.ref, a_.subpath, out=out)
    elif a_.op == "update":
        update(a_.name, a_.ref, projects=not a_.no_projects, out=out)
    elif a_.op == "remove":
        remove(a_.name, out=out)
    return 0
