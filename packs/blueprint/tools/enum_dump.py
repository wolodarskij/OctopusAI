"""Dump the value list of a UserDefinedEnum ``.uasset``.

The editor MCP surface cannot read ``UUserDefinedEnum::DisplayNameMap`` --
``list_properties`` reports only ``enumDescription`` and ``get_properties``
refuses the map -- so the enumerator display names have to come off disk.

They are recoverable exactly, without guessing: every display name is an
``FText`` whose localisation key the editor generates as

    <PackagePath>.<Enum>.DisplayNameMap(<N> - Value).DisplayNameMap

so the entry index ``N`` is written next to the string it belongs to. The
internal identifiers (``<Enum>::NewEnumerator<K>``) come from the package name
table region and are what Blueprint graphs actually serialise -- which is why
renaming a display name is safe and reordering is not.

Usage:
    python tools/enum_dump.py Content/.../MyEnum.uasset
    python tools/enum_dump.py Content/.../MyEnum.uasset --json
"""

import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

KEY_RE = re.compile(rb"\.DisplayNameMap\((\d+) - Value\)\.DisplayNameMap")
STR_RE = re.compile(rb"[ -~]{2,}")
GUID_RE = re.compile(r"^[0-9A-F]{32}$")
MAX_ENUM = "_MAX"


def ascii_runs(blob):
    """(offset, text) for every printable ASCII run, in file order."""
    return [(m.start(), m.group().decode("ascii")) for m in STR_RE.finditer(blob)]


def dump(path):
    with io.open(path, "rb") as fh:
        blob = fh.read()

    stem = os.path.splitext(os.path.basename(path))[0]
    runs = ascii_runs(blob)

    # Internal enumerator identifiers, in name-table order.
    ident_re = re.compile(re.escape(stem.encode("ascii")) + rb"::([A-Za-z0-9_]+)")
    idents, seen = [], set()
    for m in ident_re.finditer(blob):
        name = m.group(1).decode("ascii")
        if name not in seen:
            seen.add(name)
            idents.append(name)
    explicit_max = [n for n in idents if n.endswith(MAX_ENUM)]
    idents = [n for n in idents if not n.endswith(MAX_ENUM)]

    # Display names: the printable run immediately preceding each loc key.
    display = {}
    for m in KEY_RE.finditer(blob):
        index = int(m.group(1))
        if index in display:
            continue  # the second block is the localisation-gather copy
        before = [text for off, text in runs if off < m.start()]
        for text in reversed(before):
            # Skip the FText plumbing that sits between the string and its key:
            # the localisation namespace GUID and the gather-copy markers.
            if text.startswith("/Game") or text.startswith("["):
                continue
            if GUID_RE.match(text):
                continue
            display[index] = text
            break

    values = []
    for i in range(max(len(idents), (max(display) + 1) if display else 0)):
        values.append({
            "index": i,
            "internal": idents[i] if i < len(idents) else None,
            "display": display.get(i),
        })

    return {
        "enum": stem,
        "path": path.replace("\\", "/"),
        "count": len(values),
        "hasExplicitMax": bool(explicit_max),
        "values": values,
    }


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    as_json = "--json" in argv
    paths = [a for a in argv if not a.startswith("--")]
    results = [dump(p) for p in paths]
    if as_json:
        print(json.dumps(results, indent=1))
        return 0
    for r in results:
        print("%s  (%d values%s)" % (
            r["enum"], r["count"], ", explicit _MAX" if r["hasExplicitMax"] else ""))
        for v in r["values"]:
            print("  %-3d %-20s %s" % (v["index"], v["internal"] or "?", v["display"] or "?"))
    return 0


if __name__ == "__main__":
    _project.optional_project_root()
    sys.exit(main(sys.argv[1:]))
