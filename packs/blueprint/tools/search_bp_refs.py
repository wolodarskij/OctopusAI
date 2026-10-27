"""
search_bp_refs.py — Search Blueprint uassets for references to a class or function name.

Blueprints store class names, function names, and property names as plain strings
inside their binary .uasset format. This script exploits that to find which
Blueprints reference any given identifier.

Non-Blueprint assets (textures, audio, meshes, etc.) are filtered out cheaply
before the content search, so the scan stays fast even on large Content trees.

Usage:
    python search_bp_refs.py <pattern> [--root <path>] [--maps] [--exact] [--verbose]

The default root is the `Content/` directory of the project (`--project`, OCTOPUS_PROJECT, or
the nearest directory above the current one with a `.uproject`).

Examples:
    python search_bp_refs.py MyHelperFunction
    python search_bp_refs.py AMyActor --verbose
    python search_bp_refs.py ComputeCurvePoint --maps
    python search_bp_refs.py ResetScore --root Content --maps --exact
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

# ── Blueprint detection ────────────────────────────────────────────────────────
# Every Blueprint-type uasset contains at least one of these strings.
# Pure binary assets (textures, audio, meshes) never do.
BLUEPRINT_INDICATORS = (
    b"BlueprintGeneratedClass",
    b"WidgetBlueprintGeneratedClass",
    b"AnimBlueprintGeneratedClass",
)

NUL = bytes([0])


def is_blueprint_asset(data: bytes) -> bool:
    return any(ind in data for ind in BLUEPRINT_INDICATORS)


# ── Core search ───────────────────────────────────────────────────────────────

def resolve_pattern(pattern: str) -> tuple[str, str | None]:
    """
    UE binary assets store class/struct names without the C++ type prefix.
    AMyClass → MyClass, UMyComp → MyComp, FMyStruct → MyStruct, etc.

    If the pattern looks like a prefixed UE name (single uppercase letter
    followed by another uppercase letter), return both the original and the
    stripped version so both are tried.
    """
    if len(pattern) >= 3 and pattern[0] in "AUFEITa" and pattern[0].isupper() and pattern[1].isupper():
        return pattern, pattern[1:]
    return pattern, None


def search(pattern: str, root: Path, include_maps: bool, verbose: bool,
           exact: bool = False) -> list[Path]:
    original, stripped = resolve_pattern(pattern)

    if stripped:
        print(f"Note: '{original}' looks like a UE-prefixed name. Searching for both "
              f"'{original}' and '{stripped}' (UE drops prefixes in binary).\n")

    # UTF-8 matches latin-1 byte-for-byte on ASCII, so every pure-ASCII search behaves
    # exactly as latin-1 would. It also stops the tool raising UnicodeEncodeError on a
    # name that is not ASCII -- a name can carry a Cyrillic letter that looks like a
    # Latin one, and latin-1 cannot encode it, so the sweep would die instead of reporting.
    # A non-ASCII FName is written to the package as UTF-16, so add that needle too;
    # for ASCII names it is skipped, leaving existing counts untouched.
    def needles_for(text: str) -> list[bytes]:
        out = [text.encode("utf-8")]
        if any(ord(c) > 127 for c in text):
            out.append(text.encode("utf-16-le"))
        return out

    names = [original] + ([stripped] if stripped else [])

    if exact:
        # Exact-FName matching. The default scan is a plain substring test, so a search
        # for `ResetScore` also reports every package holding `ResetScoreLocal`, and
        # `GetScore` reports packages whose only hit is `GetScoreText`.
        #
        # UE stores an FName in the package name table as a length-prefixed string: an
        # int32 count, then the bytes, then a terminator. The count is negative for
        # UTF-16, where it counts characters rather than bytes. Requiring that framing
        # is what makes a match exact.
        #
        # The count INCLUDES the terminator -- "ResetScoreNow" is 13 characters and is
        # stored with a count of 14 (\x0e\x00\x00\x00). Using the bare length matches
        # nothing at all, which looks exactly like "this symbol has no references".
        #
        # KNOWN LIMIT: an FName is a (string, number) pair, and a trailing "_<digits>" is
        # stored as the NUMBER, not as part of the string. So --exact on `CustomEvent_3`
        # matches zero packages -- including the one that declares it -- because the table
        # holds "CustomEvent" with number 4. For any name ending in _<digits>, search the
        # stem and disambiguate with the guid sweep instead.
        def exact_needles_for(text: str) -> list[bytes]:
            out = [(len(text) + 1).to_bytes(4, "little", signed=True)
                   + text.encode("utf-8") + NUL]
            if any(ord(c) > 127 for c in text):
                out.append((-(len(text) + 1)).to_bytes(4, "little", signed=True)
                           + text.encode("utf-16-le") + NUL + NUL)
            return out

        build_needles = exact_needles_for
    else:
        build_needles = needles_for

    needles = []
    for text in names:
        needles.extend(build_needles(text))

    extensions = ["*.uasset"]
    if include_maps:
        extensions.append("*.umap")

    all_files: list[Path] = []
    for ext in extensions:
        all_files.extend(root.rglob(ext))

    # A root that exists but holds no packages returns "no references" for every name, and that
    # zero is indistinguishable from a real one. Refuse instead of answering.
    if not all_files:
        print(f"Error: no {' or '.join(extensions)} under {root} -- this root holds no packages, "
              f"so every answer would be a false zero. Pass --root pointing at Content/.",
              file=sys.stderr)
        sys.exit(2)
    print(f"corpus: {len(all_files)} package(s) under {root}")

    # A Blueprint class is referenced by its generated-class FName, so a package that uses
    # BP_ExampleActor holds "BP_ExampleActor_C" and the bare asset name matches almost nothing.
    # Count the class form in the same sweep for every term that is not already one. The class
    # form of a name that is not a class matches zero packages and stays quiet.
    #
    # The asset itself is not the test: a deleted Blueprint has no package under Content/ while
    # other packages still hold its `_C` name, which is the zero this note exists to catch.
    class_form = None
    class_needles: list[bytes] = []
    term_is_asset = any(f.stem == pattern for f in all_files)
    if not pattern.endswith("_C"):
        class_form = pattern + "_C"
        class_needles = build_needles(class_form)

    matches: list[Path] = []
    class_matches = 0
    skipped = 0
    searched = 0

    for path in sorted(all_files):
        data = path.read_bytes()

        if not is_blueprint_asset(data):
            skipped += 1
            if verbose:
                print(f"  [skip]  {path.relative_to(root)}")
            continue

        searched += 1
        if class_needles and any(n in data for n in class_needles):
            class_matches += 1
        if any(n in data for n in needles):
            matches.append(path)
            if verbose:
                print(f"  [MATCH] {path.relative_to(root)}")
        else:
            if verbose:
                print(f"  [  -  ] {path.relative_to(root)}")

    print(f"\nSearched {searched} Blueprint asset(s), skipped {skipped} non-Blueprint asset(s).")
    if class_form and (term_is_asset or class_matches > len(matches)):
        print(f"Note: a Blueprint class is referenced as '{class_form}', never as "
              f"'{pattern}'. '{pattern}' matches {len(matches)} package(s), "
              f"'{class_form}' matches {class_matches}. Search the class form for the "
              f"consumers of the class.")
    return matches


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    project = _project.optional_project_root()
    parser = argparse.ArgumentParser(
        description="Search Blueprint uassets for a class/function/property name."
    )
    parser.add_argument("pattern", help="Name to search for (case-sensitive)")
    parser.add_argument(
        "--root",
        default=None,
        help="Path to Content/ directory. Defaults to the project's Content/.",
    )
    parser.add_argument(
        "--maps",
        action="store_true",
        help="Also search .umap files (Level Blueprints).",
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="Match whole FName entries only, so ResetScore no longer reports "
             "packages whose only hit is ResetScoreLocal.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every file as it is processed.",
    )
    args = parser.parse_args()

    if args.root:
        root = Path(args.root)
    elif project is not None:
        root = Path(project) / "Content"
    else:
        print("Error: no project: pass --project <path> or --root <Content dir>", file=sys.stderr)
        sys.exit(1)

    if not root.is_dir():
        print(f"Error: content root not found: {root}", file=sys.stderr)
        sys.exit(1)

    print(f"Searching for '{args.pattern}' in {root}"
          f"{' (exact FName match)' if args.exact else ''}\n")
    matches = search(args.pattern, root, args.maps, args.verbose, args.exact)

    if matches:
        print(f"\nFound {len(matches)} match(es):")
        for m in matches:
            print(f"  {m.relative_to(root)}")
    else:
        print(f"\nNo Blueprint assets reference '{args.pattern}'.")


if __name__ == "__main__":
    main()
