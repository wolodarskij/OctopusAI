# -*- coding: utf-8 -*-
"""Every `FText::FromStringTable` in `Script/` names a key the table asset carries.

Offline: it reads `Script/` and the Blueprint export `stringtables.json`, which holds the rows of
every string table asset. A key the asset does not carry renders as `<MISSING STRING TABLE ENTRY>`
on screen. The `blueprint` pack writes the export:

    python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" stringtables --project <project>

When the file is missing, the tool prints the command and exits 2.

**The asset rows count, never the manifest.** The localisation manifest can hold a key that no
asset carries, and the game then draws the placeholder.

    python check_string_table_keys.py

Exit 1 when a site names a key no table carries. A key built at run time is reported as skipped.
"""
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
os.chdir(ROOT)

SITE = re.compile(r'FromStringTable\(\s*n?"([^"]+)"\s*,\s*"([^"]+)"\s*\)')
RUNTIME = re.compile(r'FromStringTable\(\s*[^,()]+,\s*(?!["\s])[^)]*\)')

# A table named through a constant: `const FName ExampleTable = n"/Game/Text/Foo.Foo";`
CONSTANT = re.compile(r'const\s+FName\s+(\w+)\s*=\s*n"([^"]+)"')
THROUGH_CONSTANT = re.compile(r'FromStringTable\(\s*(\w+)\s*,\s*"([^"]+)"\s*\)')


def table_keys():
    """Every string table, by its table id (`/Game/Text/Foo.Foo`), mapped to the keys it carries."""
    tables = _export.load('stringtables')['tables']
    return {table: set(rows) for table, rows in tables.items()}


def main():
    out = io.open(1, 'w', encoding='utf-8', errors='replace', closefd=False)
    held = table_keys()

    sites = 0
    runtime = 0
    missing = []
    for root, dirs, files in os.walk('Script'):
        for name in sorted(files):
            if not name.endswith('.as'):
                continue
            path = os.path.join(root, name).replace('\\', '/')
            body = io.open(path, encoding='utf-8', errors='replace').read()
            constants = dict(CONSTANT.findall(body))

            for table, key in SITE.findall(body):
                sites += 1
                if key not in held.get(table, ()):
                    missing.append((path, table, key))

            for name, key in THROUGH_CONSTANT.findall(body):
                table = constants.get(name)
                if table is None:
                    continue
                sites += 1
                if key not in held.get(table, ()):
                    missing.append((path, table, key))

            runtime += len(RUNTIME.findall(body))

    for path, table, key in missing:
        out.write('%s names %s / %s, which no table carries\n' % (path, table, key))
    out.write('%d sites, %d keys no table carries, %d keys built at run time\n'
              % (sites, len(missing), runtime))
    out.flush()
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
