"""_export.py - read a file of the Blueprint export.

A tool of this pack reads `Script/` and never a Blueprint. When it needs Blueprint data, the
`blueprint` pack writes that data to a JSON file first:

    python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" <kind> --project <project>

The file is `<exportDir>/<kind>.json`. `exportDir` is a key of `<project>/.octopus/blueprint.json`;
the default is `.octopus/export/blueprint`, relative to the project root.

`load(kind)` returns the file as a dict. When the file is missing, it prints one line that names
the command to run and exits 2.
"""

from __future__ import annotations

import json
import os
import sys

import _project

# Inside the project's config directory: `.octopus/`, or `.octopus/` before the migration.
DEFAULT_EXPORT_DIR = "export/blueprint"
_EXPORTER = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                          "blueprint", "tools", "blueprint_export.py"))


def export_dir():
    """The export directory, as an absolute path."""
    default = _project.in_config(DEFAULT_EXPORT_DIR)
    raw = _project.setting("blueprint", "exportDir", default) or default
    path = raw if os.path.isabs(raw) else os.path.join(str(_project.project_root()), raw)
    return os.path.normpath(path)


def path_of(kind):
    return os.path.join(export_dir(), kind + ".json")


def command(kind):
    """The command line that writes the export file of `kind`."""
    return 'python "%s" %s --project "%s"' % (_EXPORTER.replace("\\", "/"), kind,
                                              str(_project.project_root()).replace("\\", "/"))


def load(kind):
    """The export file of `kind` as a dict. Exits 2 with one line when the file is missing."""
    path = path_of(kind)
    if not os.path.isfile(path):
        sys.stderr.write("error: the Blueprint export %s is missing; write it with: %s\n"
                         % (path.replace("\\", "/"), command(kind)))
        sys.exit(2)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write("error: the Blueprint export %s cannot be read (%s); write it again with: "
                         "%s\n" % (path.replace("\\", "/"), exc, command(kind)))
        sys.exit(2)
    if not isinstance(data, dict) or data.get("kind") != kind:
        sys.stderr.write("error: %s is not a `%s` export; write it again with: %s\n"
                         % (path.replace("\\", "/"), kind, command(kind)))
        sys.exit(2)
    return data
