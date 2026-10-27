"""Extract every inline-NSLOCTEXT text identity (namespace, key, source string) from a .uasset.

WHY THIS EXISTS
---------------
It proves, offline and without the editor, whether a copy of a DataTable (for example a
native-rowed twin of a Blueprint-struct table) kept or lost the localisation identity of its FText
columns. `tools/datatable_text_dump.py` finds the *strings*; this finds the *identities*, which is
what a `.locres` lookup actually matches on.

WHAT AN INLINE TEXT LOOKS LIKE ON DISK
--------------------------------------
`FTextHistory_Base::Serialize` writes three consecutive FStrings:

    <namespace>  <key>  <source string>

`FTextKey::SerializeAsString` writes an ordinary length-prefixed FString for the first two, and an
EMPTY namespace is a bare `int32 0` - four bytes, no payload. `datatable_text_dump.py`'s string
scanner skips those (it requires len >= 2), which is why it cannot pair a namespace to a key.

The key is 32 uppercase hex characters both when authored and when the engine re-keys
(`FGuid::NewGuid().ToString()` defaults to `EGuidFormats::Digits`), so the key's SHAPE proves
nothing. Only comparing key VALUES between two packages does.

A namespace ending in `]` carries a package-localization id (`TextNamespaceUtil`), and that is the
single thing that decides whether the engine re-keys the text when it is written into a new
package.

USAGE
-----
    python tools/loc_key_audit.py <a.uasset> [<b.uasset> ...]        # dump identities
    python tools/loc_key_audit.py --diff <source.uasset> <twin.uasset>

`--diff` pairs the two files' texts by SOURCE STRING and reports, per text, whether the key was
preserved or replaced.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402


def read_fstring(data, i):
    """Read one length-prefixed FString at offset i. Returns (text, next_offset) or None.

    UE writes int32 length then that many bytes including a trailing NUL; a negative length means
    UTF-16 and the magnitude is the character count. Length 0 is a legitimate empty string.
    """
    if i + 4 > len(data):
        return None
    n = struct.unpack_from("<i", data, i)[0]

    if n == 0:
        return "", i + 4

    if 1 <= n <= 4096 and i + 4 + n <= len(data):
        raw = data[i + 4:i + 4 + n]
        if not raw.endswith(b"\x00"):
            return None
        body = raw[:-1]
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if any(ord(c) < 9 for c in text):
            return None
        return text, i + 4 + n

    if -4096 <= n <= -1 and i + 4 + (-n) * 2 <= len(data):
        raw = data[i + 4:i + 4 + (-n) * 2]
        try:
            text = raw.decode("utf-16-le")
        except UnicodeDecodeError:
            return None
        if not text.endswith("\x00"):
            return None
        text = text[:-1]
        if any(ord(c) < 9 for c in text):
            return None
        return text, i + 4 + (-n) * 2

    return None


def is_key(text):
    return len(text) == 32 and all(c in "0123456789ABCDEF" for c in text)


def scan(path):
    """Every (offset, namespace, key, source) inline text identity in the package.

    Anchored on the KEY, because it is the only one of the three with a recognisable shape. The
    namespace is read backwards from it by trying every plausible start offset, so an empty
    namespace (4 bytes) is found as readily as a 34-byte package-id one.
    """
    with open(path, "rb") as handle:
        data = handle.read()

    out = []
    i = 0
    while i < len(data) - 8:
        got = read_fstring(data, i)
        if got and is_key(got[0]):
            key, after_key = got
            src = read_fstring(data, after_key)
            if src is not None and not is_key(src[0]):
                # Walk backwards for the namespace FString that ends exactly at i.
                namespace = None
                for back in range(4, 300):
                    if i - back < 0:
                        break
                    cand = read_fstring(data, i - back)
                    if cand and cand[1] == i:
                        namespace = cand[0]
                        break
                out.append((i, namespace, key, src[0]))
                i = src[1]
                continue
        i += 1
    return out


def pkg_namespace(namespace):
    """The package-localization id inside a namespace, or None. Mirrors ExtractPackageNamespace."""
    if namespace and namespace.endswith("]") and "[" in namespace:
        return namespace[namespace.rfind("[") + 1:-1]
    return None


def dump(path):
    hits = scan(path)
    print("%s  -  %d inline text identities" % (path, len(hits)))
    for offset, namespace, key, src in hits:
        marker = "PKG" if pkg_namespace(namespace) else "   "
        print("  %8d %s ns=%-40r key=%s  %r" % (offset, marker, namespace, key, src[:60]))
    print()


def diff(src_path, twin_path):
    src = scan(src_path)
    twin = scan(twin_path)

    by_string = {}
    for _, namespace, key, s in src:
        by_string.setdefault(s, []).append((namespace, key))

    preserved = replaced = unmatched = 0
    print("SOURCE %s  (%d texts)" % (src_path, len(src)))
    print("TWIN   %s  (%d texts)" % (twin_path, len(twin)))
    print()
    for _, namespace, key, s in twin:
        cands = by_string.get(s)
        if not cands:
            unmatched += 1
            print("  UNMATCHED  key=%s  %r" % (key, s[:60]))
            continue
        src_keys = [k for _, k in cands]
        if key in src_keys:
            preserved += 1
        else:
            replaced += 1
            print("  REKEYED    %s -> %s   ns %r -> %r   %r"
                  % (src_keys[0], key, cands[0][0], namespace, s[:50]))
    print()
    print("preserved %d   rekeyed %d   unmatched %d   (source texts %d, twin texts %d)"
          % (preserved, replaced, unmatched, len(src), len(twin)))
    return preserved, replaced, unmatched


def main():
    _project.optional_project_root()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == "--diff":
        if len(args) != 3:
            print("--diff needs exactly two .uasset paths")
            return 2
        diff(args[1], args[2])
        return 0
    for path in args:
        dump(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
