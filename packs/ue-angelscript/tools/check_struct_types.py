#!/usr/bin/env python
"""The FIFTH struct axis: does a script struct field's TYPE match the authored Blueprint field's type?

`check_struct_specifiers.py` compares four axes - name, `DisplayName`, container-ness and
`bEnableSaveGame`. It does not compare the type. A field declared `TMap<FString, FString>` where
the asset says `TMap<FName, FText>` passes all four axes: both are maps. The FText values then load
as strings, and a player sees export text in place of a localised line. This tool compares the type.

WHERE THE AUTHORED TYPE COMES FROM
==================================
The Blueprint export `structs.json` holds, per UserDefinedStruct asset, the pin type of each field.
This tool reads `Script/` and that file, and nothing else. The `blueprint` pack writes the file:

    python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" structs --project <project>

When the file is missing, the tool prints the command and exits 2. The fields of the pin type:

    ContainerType       EPinContainerType::{None,Array,Set,Map}
    Category            the pin category FName - `bool`, `int`, `real`, `string`, `name`, `text`,
                        `byte` (an enum serialises as this), `struct`, `object`, `class`
    SubCategory         refines Category (e.g. `float` / `double` under `real`)
    TerminalCategory    for a MAP, the VALUE's category
    TerminalSubCategory refines TerminalCategory

For an ARRAY or SET, `Category` is the ELEMENT's category and `TerminalCategory` is `None`.
For a MAP, `Category` is the KEY's category and `TerminalCategory` is the VALUE's.

A CONTROL VERIFIES THE READING
==============================
The exporter decodes a control struct whose types are known (`blueprint_export.structControl` of
`.octopus/blueprint.json`) and records the result in the file. When the control failed, this tool
prints THE DECODER IS WRONG and exits 2. `--self-test` prints the control result. When the project
sets no control, the tool says so; the self-test then exits 0.

WHAT IT DOES NOT DO
===================
It does not compare `SubCategoryObject` - the concrete struct or class behind a `struct`/`object`
category. That needs the export map, and a mismatch there fails to compile. `float` vs `double`
under `real` is not a defect: the engine widened these.

Decided divergences come from `expectedDivergence` in `.octopus/ue-angelscript.json`: a list of
`{struct, field, reason}`, where `field` is the ASSET's field name.

    python check_struct_types.py [--self-test] [--verbose] [--script=Script]

Exit 0 clean, 1 real mismatches, 2 the tool could not do its job.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402
import _export  # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("check_struct_types", {})

import check_struct_specifiers as base  # noqa: E402  (it changes the working directory to ROOT)


# =================================================================================================
# Script type -> expected (container, key/element category, map-value category).
# =================================================================================================
# `None` in a slot means "do not compare this slot": the pin category is ambiguous from the script
# text alone.
SCALAR = {
    "bool": "bool",
    "int32": "int", "int": "int", "uint8": "byte", "int64": "int64",
    "float": "real", "float32": "real", "double": "real", "float64": "real",
    "FString": "string",
    "FName": "name",
    "FText": "text",
}

CONTAINER_RE = re.compile(r"^(TArray|TSet|TMap)\s*<(.+)>$")


# =================================================================================================
# EXPECTED DIVERGENCE - script and asset disagree about a field's TYPE on purpose.
# Keyed by (script struct name, the ASSET's field name). Each entry carries its reason.
# =================================================================================================
EXPECTED_DIVERGENCE = {(e["struct"], e["field"]): e["reason"]
                       for e in CFG.get("expectedDivergence", [])}


def split_top_level(text):
    """Split `A, B` on commas that are not inside nested angle brackets."""
    parts, depth, current = [], 0, ""
    for ch in text:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def category_of(script_type):
    """Pin category a scalar script type serialises as, or None if we decline to judge it."""
    t = script_type.strip().replace("const ", "").rstrip("&").strip()
    if t in SCALAR:
        return SCALAR[t]
    if re.match(r"^E[A-Z]\w*$", t):
        return "byte"          # every enum, native or UserDefinedEnum, serialises as byte
    if re.match(r"^F[A-Z]\w*$", t):
        return "struct"
    if re.match(r"^(U|A)[A-Z]\w*$", t):
        return "object"
    if t.startswith("TSubclassOf"):
        return "class"
    if t.startswith("TSoftObjectPtr") or t.startswith("TSoftClassPtr"):
        return None            # softobject/softclass - declined
    return None


def expected_shape(script_type):
    """Return (container, category, terminal) expected on the asset side, or None to decline."""
    t = script_type.strip()
    m = CONTAINER_RE.match(t)
    if not m:
        return ("EPinContainerType::None", category_of(t), "None")
    kind, inner = m.group(1), m.group(2)
    args = split_top_level(inner)
    if kind == "TArray":
        return ("EPinContainerType::Array", category_of(args[0]), "None")
    if kind == "TSet":
        return ("EPinContainerType::Set", category_of(args[0]), "None")
    if kind == "TMap" and len(args) == 2:
        return ("EPinContainerType::Map", category_of(args[0]), category_of(args[1]))
    return None


# =================================================================================================
def main():
    verbose = "--verbose" in sys.argv
    self_test = "--self-test" in sys.argv

    data = _export.load("structs")
    print("Axis 5 - FIELD TYPE. The axis check_struct_specifiers.py does not have.")
    print("=" * 96)

    control = data.get("control") or {}
    status = control.get("status", "skipped")
    if status == "skipped":
        print("control SKIPPED - no control fixture is set in .octopus/blueprint.json "
              "(blueprint_export.structControl)")
        if self_test:
            print("\nself-test: SKIPPED - the control is the self-test, and no control is set.")
            return 0
    elif status != "passed":
        print("\nTHE DECODER IS WRONG - refusing to report an audit nobody should trust:")
        for p in control.get("problems") or ["the export records the control as %s" % status]:
            print("   " + p)
        return 2
    else:
        print("control PASSED (%s decodes exactly as its known types)" % control.get("struct"))

    if self_test:
        print("\nself-test: the control is the self-test. Decoder verified against known types.")
        return 0

    exported = data.get("structs", {})
    assets = sorted(exported)

    script = base.parse_script_structs()

    defects, declined, unpaired, errors, expected = [], [], [], [], []
    compared = 0

    for stem in assets:
        entry = exported[stem]
        asset = entry.get("path", stem)
        if stem in getattr(base, "NO_COUNTERPART_EXPECTED", ()):
            continue
        target = getattr(base, "ALIASES", {}).get(stem, "F" + stem)
        struct_def = script.get(target)
        if struct_def is None:
            unpaired.append((stem, target))
            continue

        if entry.get("typesError") or not entry.get("types"):
            errors.append("%s: %s" % (asset, entry.get("typesError") or "no fields in the export"))
            continue
        bp_fields = entry["types"]

        by_name = {f["name"].lower(): f for f in struct_def["fields"]}
        by_display = {f["display"].lower(): f for f in struct_def["fields"] if f["display"]}

        for bp in bp_fields:
            match = by_display.get(bp["name"].lower()) or by_name.get(bp["name"].lower())
            if match is None:
                continue                      # axis 1's job, not ours
            want = expected_shape(match["type"])
            if want is None:
                declined.append((target, bp["name"], match["type"]))
                continue
            wc, wcat, wterm = want
            compared += 1

            got = (bp["container"], bp["category"], bp["terminal"])
            if wc != bp["container"]:
                continue                      # axis 3's job - do not double-report
            if wcat is None:
                declined.append((target, bp["name"], match["type"]))
                continue

            bad = []
            if wcat != bp["category"]:
                # real/float widening is not a behavioural difference - report as info only
                if not (wcat == "real" and bp["category"] in ("float", "double", "real")):
                    bad.append("category: script %s implies `%s`, asset says `%s`"
                               % (match["type"], wcat, bp["category"]))
            # `PinValueType`/`TerminalCategory` is only meaningful for a MAP. Some assets carry a
            # stale non-`None` terminal on an Array, so the terminal is compared only for a map.
            if wc == "EPinContainerType::Map" and wterm is not None and wterm != bp["terminal"]:
                if not (wterm == "real" and bp["terminal"] in ("float", "double", "real")):
                    bad.append("MAP VALUE: script %s implies `%s`, asset says `%s`"
                               % (match["type"], wterm, bp["terminal"]))
            if bad:
                reason = EXPECTED_DIVERGENCE.get((target, bp["name"]))
                if reason:
                    expected.append((target, bp["name"], match["type"], bad, got, reason))
                else:
                    defects.append((target, struct_def["file"], bp["name"],
                                    match["type"], bad, got))

    print("\ncompared %d fields across %d paired structs" % (compared, len(assets) - len(unpaired)))

    if defects:
        print("\nTYPE MISMATCHES - %d" % len(defects))
        print("-" * 96)
        for name, path, field, script_type, bad, got in defects:
            print("\n%s.%s   (%s)" % (name, field, path))
            print("   script declares : %s" % script_type)
            print("   asset decodes to: container=%s category=%s terminal=%s" % got)
            for b in bad:
                print("   ! " + b)

    if expected:
        print("\nEXPECTED DIVERGENCE - %d (script and asset disagree ON PURPOSE)" % len(expected))
        print("-" * 96)
        for name, field, script_type, bad, got, reason in expected:
            print("\n%s.%s" % (name, field))
            print("   script declares : %s" % script_type)
            print("   asset decodes to: container=%s category=%s terminal=%s" % got)
            for b in bad:
                print("   - " + b)
            print("   WHY: " + " ".join(reason.split()))

    if verbose and declined:
        print("\ndeclined to judge (%d) - type not decidable from the script text alone:" % len(declined))
        for name, field, typ in declined:
            print("   %s.%s : %s" % (name, field, typ))

    if unpaired:
        print("\nunpaired assets (%d) - no script struct, axis 1's problem not ours" % len(unpaired))

    if errors:
        print("\nTOOL ERRORS - %d (the tool could not do its job here)" % len(errors))
        for e in errors:
            print("   " + e)
        return 2

    if compared == 0:
        print("\nZERO FIELDS COMPARED. A clean report with no inputs is not a clean report.")
        return 2

    print("\n%s" % ("MISMATCHES FOUND" if defects else "no type mismatches"))
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
