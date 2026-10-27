"""_project.py - how a pack tool finds the project it works on, and that project's settings.

The order:

1. `--project <path>` on the command line. `project_root()` removes it from `sys.argv`, so a
   tool's own argument parser never sees it.
2. the `OCTOPUS_PROJECT` environment variable;
3. the nearest directory above the current directory that holds `.octopus/project.json` or a
   `*.uproject` file.

A value that belongs to one project is never in a tool. It is in
`<project>/.octopus/<name>.json`, and `config("<name>")` reads it.

    import _project
    ROOT = _project.project_root()
    CFG = _project.config("unreal")

The `unreal`, `blueprint` and `ue-angelscript` packs all use this file. A tool of the `blueprint` or
`ue-angelscript` pack puts `packs/unreal/tools/` on `sys.path` to import it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = None
CONFIG_DIR = ".octopus"
# A renamed pack -> its old name, whose settings file a project not yet migrated still holds.
OLD_NAMES = {"ue-angelscript": "angelscript"}


def env(name):
    """`OCTOPUS_<name>`, else an empty string."""
    return os.environ.get("OCTOPUS_" + name, "").strip()


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


def _is_root(d):
    if (d / CONFIG_DIR / "project.json").is_file():
        return True
    try:
        return any(p.suffix == ".uproject" for p in d.iterdir() if p.is_file())
    except OSError:
        return False


def find_root(start=None):
    """The nearest project directory at or above `start`, or None."""
    d = Path(start or os.getcwd()).resolve()
    for c in [d] + list(d.parents):
        if _is_root(c):
            return c
    return None


def project_root():
    """The project this tool runs on. Exits with a message when there is none."""
    global _ROOT
    if _ROOT is not None:
        return _ROOT
    arg, rest = _take_project_arg(sys.argv[1:])
    sys.argv[1:] = rest
    raw = arg or env("PROJECT")
    if raw:
        root = Path(raw).resolve()
        if not root.is_dir():
            raise SystemExit("error: --project %s is not a directory" % raw)
    else:
        root = find_root()
        if root is None:
            raise SystemExit("error: no project: pass --project <path>, set OCTOPUS_PROJECT, "
                             "or run inside a directory with .octopus/project.json or a "
                             "*.uproject file")
    _ROOT = root
    os.environ["OCTOPUS_PROJECT"] = str(root)
    return root


def config_dir():
    """The name of the project's config directory."""
    return CONFIG_DIR


def in_config(rel):
    """`rel` inside the project's config directory, relative to the project root."""
    return "%s/%s" % (config_dir(), rel)


def uproject():
    """The project's `.uproject` file, or None."""
    name = config("unreal").get("uproject", "")
    root = project_root()
    if name:
        return root / name
    hits = sorted(root.glob("*.uproject"))
    return hits[0] if hits else None


def config(name):
    """`<project>/.octopus/<name>.json` as a dict; an empty dict when the file is absent. A
    renamed pack's settings are read under its old name while the new file is absent."""
    p = project_root() / config_dir() / ("%s.json" % name)
    old = project_root() / config_dir() / ("%s.json" % OLD_NAMES.get(name, name))
    if not p.is_file() and old.is_file():
        p = old
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SystemExit("error: %s is not valid JSON: %s" % (p, exc))
    return data if isinstance(data, dict) else {}


def setting(name, key, default=None):
    """One key of `<project>/.octopus/<name>.json`, or `default`."""
    return config(name).get(key, default)


def optional_project_root():
    """The project this tool runs on, or None when there is none.

    It removes `--project` from `sys.argv` like `project_root()`. A tool that can run without a
    project, such as a parser of one T3D file, calls this one."""
    if _ROOT is not None:
        return _ROOT
    arg, rest = _take_project_arg(sys.argv[1:])
    if arg or env("PROJECT") or find_root() is not None:
        return project_root()
    sys.argv[1:] = rest
    return None


def project_path(key, default, pack="unreal"):
    """The path that `key` of `.octopus/<pack>.json` names, or `default`, joined to the project
    root when it is relative."""
    value = setting(pack, key, default)
    if value is None or value == "":
        return None
    p = Path(value)
    return p if p.is_absolute() else project_root() / p
