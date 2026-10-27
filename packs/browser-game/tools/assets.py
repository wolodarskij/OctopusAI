"""assets.py - every asset file of a project, for the asset board and the shell.

    python assets.py list                one line per kind with its count
    python assets.py list --kind <kind>  the ids of one kind
    python assets.py list --json         the whole inventory as data
    python assets.py check               exit 1 when a file under a check root has no row

THE PROJECT. `--project <path>` on the command line (removed before the arguments are parsed),
then the `OCTOPUS_PROJECT` environment variable, then the nearest directory above the current
directory that holds `.octopus/project.json`. A server that serves many projects calls
`set_root(path)` before each call.

THE LIST IS A TABLE OF KINDS, AND THE TABLE IS DATA. `<project>/.octopus/assets.json` holds one
row per kind of file: where it lives, which names match, and how the page previews it. A new
file type is one row. `support` names the files under the check roots that belong there but are
not assets (docs, manifests, generators); `check` fails on anything that matches neither. With
no `assets.json` the table is empty, and the tool says so on stderr. The keys are in the pack's
README.md.

NO PATH FROM THE CALLER. An asset's id is its project-relative path, and `serve` and `act_open`
answer only for an id the table produces. A path the caller makes up is not in that set, so
there is nothing to traverse.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
from pathlib import Path

CONFIG = "assets.json"
EDITOR_ENV = "OCTOPUS_ASSET_EDITOR"
CONFIG_DIR = ".octopus"


def _env(name):
    return os.environ.get(name, "").strip()

# The previews the page knows:
#   svg    the file with its placeholders filled, as <img>
#   grid   a one-bit text grid, drawn to SVG here
#   img    an image file as it is;  audio  a sound file as it is
#   html   a sheet in a sandboxed iframe;  json  a key summary, then the pretty text
#   none   open only
PREVIEWS = ("svg", "grid", "img", "audio", "html", "json", "none")

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
SIZE_SUFFIX = re.compile(r"\.(\d+)x(\d+)\.[^.]+$")
TEMPLATE = re.compile(r"\{([^{}]+)\}")

_ROOT = None            # the project this module works on; set_root() or _resolve_root()
_CFG_CACHE = {}         # config path -> (mtime, size, parsed config)


# ── the project ─────────────────────────────────────────────────────────────

def _take_project_arg(argv):
    """The value of `--project` in argv, and argv without it."""
    out, found, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if a == "--project" and i + 1 < len(argv):
            found = argv[i + 1]
            i += 2
            continue
        if a.startswith("--project="):
            found = a.split("=", 1)[1]
            i += 1
            continue
        out.append(a)
        i += 1
    return found, out


def find_root(start=None):
    """The nearest directory at or above `start` that holds `.octopus/project.json`, or None."""
    d = Path(start or os.getcwd()).resolve()
    for c in [d] + list(d.parents):
        if (c / CONFIG_DIR / "project.json").is_file():
            return c
    return None


def set_root(path):
    """Point the module at one project. Every later call reads that project."""
    global _ROOT
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError("%s is not a directory" % path)
    _ROOT = root
    return root


def _resolve_root(arg=None):
    raw = arg or _env("OCTOPUS_PROJECT")
    if raw:
        root = Path(raw).resolve()
        if not root.is_dir():
            raise SystemExit("error: --project %s is not a directory" % raw)
        return root
    root = find_root()
    if root is None:
        raise SystemExit("error: no project: pass --project <path>, set OCTOPUS_PROJECT, "
                         "or run inside a directory with .octopus/project.json")
    return root


def root():
    """The current project directory."""
    global _ROOT
    if _ROOT is None:
        _ROOT = _resolve_root()
    return _ROOT


def config_path():
    return root() / CONFIG_DIR / CONFIG


def config():
    """`<project>/.octopus/assets.json` as a dict; an empty dict when the file is absent."""
    p = config_path()
    try:
        st = p.stat()
    except OSError:
        return {}
    key = str(p)
    hit = _CFG_CACHE.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError("%s is not valid JSON: %s" % (p, exc))
    if not isinstance(data, dict):
        data = {}
    _CFG_CACHE[key] = (st.st_mtime_ns, st.st_size, data)
    return data


def has_config():
    return config_path().is_file()


def table():
    """The rows of kinds: (kind, group, root, globs, preview). The first row a file matches is
    its kind."""
    rows = []
    for r in config().get("kinds") or []:
        preview = r.get("preview") or "none"
        rows.append((r["kind"], r.get("group") or "", r.get("root") or ".",
                     tuple(r.get("globs") or ()), preview if preview in PREVIEWS else "none"))
    return tuple(rows)


def _list(key):
    return tuple(config().get(key) or ())


def _rel(p):
    return p.relative_to(root()).as_posix()


def _read(path):
    return path.read_text(encoding="utf-8")


def index():
    """Every asset id with its row, in table order. Globs only; reads no file."""
    out = {}
    base_dir = root()
    for row in table():
        kind, _group, sub, globs, _preview = row
        base = base_dir / sub
        found = sorted({p for g in globs for p in base.glob(g) if p.is_file()})
        for p in found:
            out.setdefault(_rel(p), row)
    return out


def _artboards():
    """Each sheet's artboard from the manifests `artboards` names, keyed by file id: the title,
    and the size the page scales the sheet from. A manifest is {"artboards": [{"file", "title",
    "w", "h"}]}, and `file` is relative to the manifest's folder."""
    out = {}
    base = root()
    for g in _list("artboards"):
        for manifest in base.glob(g):
            try:
                boards = json.loads(_read(manifest)).get("artboards") or []
            except (OSError, ValueError, AttributeError):
                continue
            for b in boards:
                if isinstance(b, dict) and b.get("file"):
                    out[_rel(manifest.parent / b["file"])] = b
    return out


def _load(rel):
    try:
        return json.loads(_read(root() / rel))
    except (OSError, ValueError):
        return {}


# ── lookups: which content entries draw which asset ─────────────────────────

_MISSING = object()


def _get(node, path):
    """The value at a dot path. A number indexes a list, `length` is a list's length, and
    `$key` is the key of an object entry. `_MISSING` when the path does not resolve."""
    if path == "":
        return node
    cur = node
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return _MISSING
            cur = cur[part]
        elif isinstance(cur, list):
            if part == "length":
                cur = len(cur)
            elif re.fullmatch(r"-?\d+", part) and -len(cur) <= int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return _MISSING
        else:
            return _MISSING
        if cur is None:
            return _MISSING
    return cur


def _text(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _render(template, scope, replace=None, strict=True):
    """The template with each `{path}` filled from the scope. None when `strict` and a path
    does not resolve to a string or a number."""
    failed = []

    def one(m):
        v = _get(scope, m.group(1).strip())
        if v is _MISSING or isinstance(v, (dict, list)):
            failed.append(m.group(1))
            return ""
        s = _text(v)
        return (replace or {}).get(s, s)

    out = TEMPLATE.sub(one, template)
    return None if failed and strict else out


def _walk(node, key, found):
    """Every string stored under `key`, anywhere under `node`."""
    if isinstance(node, dict):
        if isinstance(node.get(key), str):
            found.add(node[key])
        for v in node.values():
            _walk(v, key, found)
    elif isinstance(node, list):
        for v in node:
            _walk(v, key, found)
    return found


def _entries(node):
    """The object entries of a list or an object, each as a dict with `$key` added."""
    if isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, dict):
                yield dict(v, **{"$key": i})
    elif isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, dict):
                yield dict(v, **{"$key": k})


def _scopes(steps, node, scope):
    """Every (scope, item) the `each` steps produce, in file order. Each step iterates the
    list or the object at its `path` under the item of the step before, and names each entry
    `as` (default `item`). The item of the last step is the one `find` searches."""
    if not steps:
        yield scope, node
        return
    step = steps[0]
    if isinstance(step, str):
        step = {"path": step}
    target = _get(node, step.get("path") or "")
    if target is _MISSING:
        return
    for entry in _entries(target):
        yield from _scopes(steps[1:], entry, dict(scope, **{step.get("as") or "item": entry}))


def _passes(lookup, scope):
    """True when every `where` value and every `match` pattern holds. The named groups of a
    `match` pattern are added to the scope."""
    for path, want in (lookup.get("where") or {}).items():
        if _get(scope, path) != want:
            return False
    for path, pattern in (lookup.get("match") or {}).items():
        v = _get(scope, path)
        if v is _MISSING or isinstance(v, (dict, list)):
            return False
        m = re.search(pattern, _text(v))
        if not m:
            return False
        scope.update({k: g for k, g in m.groupdict().items() if g is not None})
    return True


def used_by():
    """Asset id to the content entries that draw it. Only the kinds in `usedKinds` appear; for
    those, an id with no entry is unused."""
    uses = {}

    def add(asset, who):
        lst = uses.setdefault(asset, [])
        if who not in lst:
            lst.append(who)

    for lookup in config().get("usedBy") or []:
        data = _load(lookup["file"])
        start = _get(data, lookup.get("at") or "")
        if start is _MISSING:
            continue
        replace = lookup.get("replace") or {}
        for scope, item in _scopes(list(lookup.get("each") or []), start, {}):
            if not _passes(lookup, scope):
                continue
            who = _render(lookup.get("who") or "", scope, replace, strict=False)
            values = sorted(_walk(item, lookup["find"], set())) if lookup.get("find") else [None]
            for value in values:
                s = dict(scope, found=value) if value is not None else scope
                for template in lookup.get("assets") or []:
                    asset = _render(template, s)
                    if asset is not None:
                        add(asset, who)
    return uses


def inventory():
    """One record per asset: {id, kind, group, name, path, bytes, mtime, placeholders,
    usedBy, preview, badges, board}. `usedBy` is None where no lookup covers the kind;
    `board` is [w, h] of a sheet's artboard, else None."""
    boards, uses = _artboards(), used_by()
    generated, used_kinds = _list("generated"), _list("usedKinds")
    base = root()
    records = []
    for aid, (kind, group, sub, _globs, preview) in index().items():
        p = base / aid
        st = p.stat()
        board = boards.get(aid) or {}
        name = board.get("title") or p.name
        badges = []
        if kind in generated:
            badges.append("generated")
            m = SIZE_SUFFIX.search(p.name)
            if m:
                badges.append("%sx%s cells" % m.groups())
                name = p.name[:m.start()]
            else:
                name = p.stem
        elif preview == "html":
            badges.append(p.name)
        elif preview == "json":                     # layouts/a, not a second "a"
            name = p.relative_to(base / sub).with_suffix("").as_posix()
        elif preview in ("svg", "grid"):
            name = p.stem
        placeholders = []
        if preview == "svg":
            placeholders = sorted(set(PLACEHOLDER.findall(_read(p))))
        records.append({
            "id": aid, "kind": kind, "group": group, "name": name,
            "path": str(p), "bytes": st.st_size, "mtime": int(st.st_mtime),
            "placeholders": placeholders,
            "usedBy": uses.get(aid, []) if kind in used_kinds else None,
            "preview": preview, "badges": badges,
            "board": [board["w"], board["h"]] if board.get("w") and board.get("h") else None,
        })
    return records


def kinds():
    """The table for the page: kind, group, preview, in table order."""
    return [{"kind": k, "group": g, "preview": pv} for k, g, _r, _gl, pv in table()]


# ── previews ────────────────────────────────────────────────────────────────

def _ink():
    return config().get("ink") or "#000000"


def _grid(text):
    return [r.strip() for r in text.replace("\r\n", "\n").split("\n") if r.strip()]


def _cells(rows):
    return "".join('<rect x="%d" y="%d" width="1" height="1"/>' % (x, y)
                   for y, row in enumerate(rows) for x, c in enumerate(row) if c == "#")


def grid_svg(text, scale=6):
    """A one-bit grid as square pixels: `#` is ink, any other character is nothing."""
    rows = _grid(text)
    w, h = max((len(r) for r in rows), default=0), len(rows)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
            'shape-rendering="crispEdges" fill="%s">%s</svg>'
            % (w, h, w * scale, h * scale, _ink(), _cells(rows)))


def fill(text, kind):
    """The SVG with every placeholder given its default. A kind in `gridFill` is filled with
    the first grid its glob finds, as `width`, `height` and `cells`."""
    values = {k: str(v) for k, v in (config().get("placeholders") or {}).items()}
    pattern = (config().get("gridFill") or {}).get(kind)
    if pattern:
        grids = sorted(p for p in root().glob(pattern) if p.is_file())
        if grids:
            rows = _grid(_read(grids[0]))
            if rows:
                values.update(width=str(max(len(r) for r in rows)), height=str(len(rows)),
                              cells=_cells(rows))
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), text)


def serve(aid):
    """(bytes, content type) for one asset id, or None for an id not in the inventory and
    for a kind that is open only."""
    row = index().get(aid)
    if row is None:
        return None
    kind, preview, p = row[0], row[4], root() / aid
    if preview == "svg":
        return fill(_read(p), kind).encode("utf-8"), "image/svg+xml"
    if preview == "grid":
        return grid_svg(_read(p)).encode("utf-8"), "image/svg+xml"
    if preview == "html":
        return p.read_bytes(), "text/html; charset=utf-8"
    if preview == "json":
        return p.read_bytes(), "application/json; charset=utf-8"
    if preview in ("img", "audio"):
        return p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return None


# ── actions ─────────────────────────────────────────────────────────────────

def act_open(body):
    """Open, edit or reveal one asset. `edit` runs OCTOPUS_ASSET_EDITOR with `{path}` filled
    (e.g. `code -g {path}`), and opens the file when it is unset."""
    aid = body.get("id") or ""
    if aid not in index():
        raise KeyError(aid)
    how = body.get("how") or "open"
    base = root()
    path = str((base / aid).resolve())
    if how == "edit":
        editor = _env(EDITOR_ENV)
        if editor:
            quoted = '"%s"' % path
            cmd = editor.replace("{path}", quoted) if "{path}" in editor else editor + " " + quoted
            subprocess.Popen(cmd, shell=True, cwd=str(base))    # noqa: S602 - the owner's editor
            return {"id": aid, "how": how, "command": cmd}
        how = "open"
    if how == "open":
        try:
            os.startfile(path)                                  # noqa: S606 - Windows shell verb
        except AttributeError:
            subprocess.Popen(["xdg-open", path])
        return {"id": aid, "how": how}
    if how == "reveal":
        if os.name == "nt":
            subprocess.Popen('explorer /select,"%s"' % path)
        else:
            subprocess.Popen(["xdg-open", str(Path(path).parent)])
        return {"id": aid, "how": how}
    raise ValueError("unknown way to open %r" % how)


ACTIONS = {"asset-open": act_open}


# ── check and CLI ───────────────────────────────────────────────────────────

def check_roots():
    return _list("checkRoots")


def unmatched():
    """Files under the check roots that no row and no `support` glob covers."""
    base = root()
    known = set(index())
    for g in _list("support"):
        known.update(_rel(p) for p in base.glob(g) if p.is_file())
    out = []
    for top in check_roots():
        for p in sorted((base / top).rglob("*")):
            if p.is_file() and _rel(p) not in known:
                out.append(_rel(p))
    return out


def _roots_text(word):
    tops = ["%s/" % t.rstrip("/") for t in check_roots()]
    return tops[0] if len(tops) == 1 else ", ".join(tops[:-1]) + " %s %s" % (word, tops[-1])


def main(argv=None):
    global _ROOT
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    arg, rest = _take_project_arg(sys.argv[1:] if argv is None else list(argv))
    if arg or _ROOT is None:
        _ROOT = _resolve_root(arg)
    try:
        rows_table = table()
    except (ValueError, KeyError, TypeError) as exc:
        print("error: %s: %s" % (config_path(), exc), file=sys.stderr)
        return 2
    if not has_config():
        print("note: %s is missing; the table of kinds is empty" % config_path(), file=sys.stderr)

    ap = argparse.ArgumentParser(description="The project's asset files.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="count per kind, or the ids of one kind")
    ls.add_argument("--kind", choices=[k[0] for k in rows_table])
    ls.add_argument("--json", action="store_true")
    sub.add_parser("check", help="exit 1 when a file under a check root has no row")
    args = ap.parse_args(rest)

    if args.cmd == "list":
        recs = inventory() if args.json else None
        if args.json:
            if args.kind:
                recs = [r for r in recs if r["kind"] == args.kind]
            print(json.dumps(recs, indent=2))
            return 0
        rows = index()
        if args.kind:
            for aid, row in rows.items():
                if row[0] == args.kind:
                    print(aid)
            return 0
        for kind, group, sub_root, _g, preview in rows_table:
            n = sum(1 for row in rows.values() if row[0] == kind)
            print("%-8s %4d  %-8s %s" % (kind, n, group, sub_root))
        print("%-8s %4d" % ("total", len(rows)))
        return 0

    bad = unmatched()
    for f in bad:
        print("no row: %s" % f, file=sys.stderr)
    if bad:
        print("%d file(s) under %s match no row in kinds or support" % (len(bad), _roots_text("or")),
              file=sys.stderr)
        return 1
    if not check_roots():
        print("ok  %d assets; assets.json names no check root" % len(index()))
        return 0
    print("ok  %d assets, every file under %s has a row" % (len(index()), _roots_text("and")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
