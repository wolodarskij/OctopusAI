# -*- coding: utf-8 -*-
"""Every `/Game/...` object path written as a literal in `Script/` must name a package that exists.

A path that names nothing fails the same way every time: `LoadAsset_Blocking` returns null, the
guard above it returns early, and the feature is silently absent.

    python check_asset_paths.py            # report, exit 1 on a miss
    python check_asset_paths.py --list     # every path it checked

WHAT IT CHECKS. A literal is checked when it starts with `/Game/`, does not end in `/`, and its last
segment carries a dot - that is, it is a complete `Package.Object` path rather than a prefix joined
at run time. The check is for the PACKAGE: `/Game/A/B.B` passes when the package `/Game/A/B` is in
the Blueprint export `packages.json`. It says nothing about the object inside the package, and
nothing about the row struct of a DataTable.

THE PACKAGE LIST. This tool reads `Script/` and the export file only. The `blueprint` pack writes
the file:

    python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" packages --project <project>

When the file is missing, the tool prints the command and exits 2.

WHAT IT CANNOT SEE. A path built by concatenation, a path held in a data asset, and a soft reference
authored in the editor are all invisible to it.
"""
import argparse
import io
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402
import _export  # noqa: E402

ROOT = str(_project.project_root())
SCRIPT_DIR = os.path.join(ROOT, 'Script')

LITERAL = re.compile(r'"(/Game/[^"]+)"')


def literals():
    """Every complete object path written in `Script/`, with the file and line that holds it."""
    found = []
    for dirpath, _dirnames, filenames in os.walk(SCRIPT_DIR):
        for name in sorted(filenames):
            if not name.endswith('.as'):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, ROOT).replace('\\', '/')
            with io.open(path, encoding='utf-8', errors='replace') as f:
                for number, line in enumerate(f, 1):
                    for hit in LITERAL.findall(line):
                        if hit.endswith('/'):
                            continue
                        if '.' not in hit.rsplit('/', 1)[-1]:
                            continue
                        found.append((hit, rel, number))
    return found


def package_exists(object_path, packages):
    """Whether the package half of an object path is in the exported package list."""
    package = object_path.split('.')[0]
    return package.startswith('/Game/') and package in packages


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--list', action='store_true', help='print every path that was checked')
    args = parser.parse_args()

    packages = set(_export.load('packages')['packages'])
    found = literals()
    missing = [(p, f, n) for p, f, n in found if not package_exists(p, packages)]

    if args.list:
        for p, f, n in sorted(set(found)):
            print('%s %s:%d' % ('MISS' if not package_exists(p, packages) else '  ok', f, n))
            print('     %s' % p)

    print('%d literal object path(s) in %s, %d distinct, %d name no package'
          % (len(found), 'Script/', len(set(p for p, _f, _n in found)), len(missing)))

    for p, f, n in missing:
        print('  MISSING %s' % p)
        print('          %s:%d' % (f, n))

    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
