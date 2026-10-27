"""Recover FText columns that the editor MCP's DataTableTools.get_rows silently drops.

WHY THIS EXISTS
---------------
`DataTableTools.get_rows` omits any row field literally named `name`. It reports no error and
no gap - the row object simply comes back one key short. Every row struct that has a `name`
field is affected, and that silence reads as "the column is unauthored".

It is not. The data is in the .uasset; only the reader is broken. This script reads it directly.

WHAT IT HANDLES
---------------
An FText column is serialised one of two ways, and they look nothing alike in the file:

  LOCTABLE  - a string-table reference: <table id> <key>, both as plain FStrings.
              e.g. "/Game/Text/ST_ExampleNames.ST_ExampleNames" + "exampleName"

  NSLOCTEXT - inline text: <namespace> <32-hex key> <source string>.
              The display string is embedded in the row itself.

Searching for keys that look like names (`*Name`) finds the first and misses the second
entirely - that is exactly the mistake this script exists to prevent. It reports both.

USAGE
-----
    python tools/datatable_text_dump.py <path-to-.uasset> [--start OFFSET]

Rows serialise in table order, so the Nth group of strings belongs to the Nth row name from
`DataTableTools.list_rows`. Fields serialise in struct-declaration order, and a field left at
its default is not written at all - so a row with an empty `killedText` emits one string fewer.
Always reconcile the groups against known-good columns (an image path, a description key)
before trusting a mapping.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402


def extract_strings(data):
    """Every length-prefixed FString in the package, as (offset, text).

    UE writes FStrings as an int32 length followed by that many bytes including a trailing
    NUL. A negative length means UTF-16, and the magnitude is the character count.
    """
    found = []
    i = 0
    while i < len(data) - 8:
        n = struct.unpack_from("<i", data, i)[0]

        if 2 <= n <= 400 and i + 4 + n <= len(data):
            raw = data[i + 4:i + 4 + n]
            if raw.endswith(b"\x00"):
                body = raw[:-1]
                if body and all(32 <= c < 127 for c in body):
                    found.append((i, body.decode("ascii")))
                    i += 4 + n
                    continue

        if -400 <= n <= -2 and i + 4 + (-n) * 2 <= len(data):
            raw = data[i + 4:i + 4 + (-n) * 2]
            try:
                text = raw.decode("utf-16-le").rstrip("\x00")
                if text and all(31 < ord(c) < 0x3000 for c in text):
                    found.append((i, text))
                    i += 4 + (-n) * 2
                    continue
            except UnicodeDecodeError:
                pass

        i += 1
    return found


def is_hex_key(text):
    return len(text) == 32 and all(c in "0123456789ABCDEF" for c in text)


def classify(strings):
    """Yield (offset, kind, value) for every string in the region, tagged.

    Deliberately does not try to be clever about which column a string belongs to - it tags
    what each string IS and leaves grouping to the caller:

      NSLOCTEXT  the source string of inline text; it directly follows a 32-hex key
      NSKEY      that 32-hex key, and the namespace GUID that precedes it
      KEY        a bare identifier - a string-table key for a LOCTABLE-authored column.
                 The table id does NOT appear here: it is serialised as a name-table
                 reference, not an inline FString, which is why only the key is visible.
      TEXT       anything else (paths, literals)
    """
    skip_next = False
    for idx, (offset, text) in enumerate(strings):
        if skip_next:
            skip_next = False
            yield offset, "NSLOCTEXT", text
            continue

        if is_hex_key(text):
            if idx + 1 < len(strings) and not is_hex_key(strings[idx + 1][1]):
                skip_next = True
            yield offset, "NSKEY", text
        elif text.isidentifier() and not text.startswith("/"):
            yield offset, "KEY", text
        else:
            yield offset, "TEXT", text


def main():
    _project.optional_project_root()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 1

    start = 0
    if "--start" in sys.argv:
        start = int(sys.argv[sys.argv.index("--start") + 1])

    with open(args[0], "rb") as handle:
        data = handle.read()

    strings = [(o, s) for o, s in extract_strings(data) if o >= start]
    hits = list(classify(strings))

    print("%s  (%d bytes, %d strings)" % (args[0], len(data), len(strings)))
    print()
    for offset, kind, value in hits:
        print("%8d  %-9s  %r" % (offset, kind, value[:100]))
    print()
    print("%d FText payload(s). Group them in order against list_rows output." % len(hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
