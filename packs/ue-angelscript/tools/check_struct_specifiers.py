"""Check every AngelScript struct FIELD against the Blueprint UserDefinedStruct it is a port of.

`check_member_specifiers.py` compares class members. It does not compare struct fields, because the
counterpart of a script struct is a UserDefinedStruct asset and not a Blueprint class. This tool does
that comparison.

WHAT IT CATCHES
---------------
A struct field that lost `SaveGame`, a field that is absent from the script struct, a flattened
container, and a renamed field with no DisplayName bridge. Each of these compiles. A field without
`SaveGame` replicates and plays correctly, and then comes back empty from a save.

THE FOUR AXES
-------------
  1. NAME        BP `VarName` vs the script property name. The match is CASE-INSENSITIVE, because
                 FName comparison is. It also matches against `meta=(DisplayName="...")`, because a
                 DisplayName is a legal bridge for a renamed field.
  2. DISPLAYNAME BP `FriendlyName` vs the script `meta=(DisplayName=...)`. A DisplayName with a
                 space that the asset does not have makes a pin that matches nothing.
  3. CONTAINER   BP `ContainerType` (`EPinContainerType::None|Array|Set|Map`) vs `TArray`/`TSet`/
                 `TMap` in the script type. A flattened container still compiles.
  4. SAVEGAME    BP `bEnableSaveGame` vs the `SaveGame` UPROPERTY specifier. A save system that
                 serialises on `CPF_SaveGame` does not persist a field without it.

The tool does not compare the scalar type. The script spelling (`int32`, `float64`, `FName`) is not
the pin category string. `check_struct_types.py` compares the type.

WHERE THE STRUCT ASSETS COME FROM
---------------------------------
The Blueprint export `structs.json` holds the fields of every UserDefinedStruct asset. This tool
reads `Script/` and that file, and nothing else. The `blueprint` pack writes the file:

    python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" structs --project <project>

When the file is missing, the tool prints the command and exits 2.

THE TOOL IS OFFLINE
-------------------
It reads `.as` text and the export file from disk. It does not talk to the editor.

IT FAILS LOUDLY
---------------
An asset the exporter could not read is an ERROR and exit code 2. It is never counted as clean.

USAGE
-----
    python check_struct_specifiers.py             # normal run
    python check_struct_specifiers.py --verbose   # also print every field that matched
    python check_struct_specifiers.py --json      # machine-readable, for other tools
    python check_struct_specifiers.py --self-test # prove it still catches known defects

Exit code: 0 clean, 1 defects found, 2 something could not be read or paired.

--self-test copies `Script/` to a temp directory, applies the mutations in the `selfTest` fixture of
`.octopus/ue-angelscript.json`, and asserts that the checker reports exactly the expected defects. It
never writes to the live tree. When the project sets no fixture, it says so and exits 0.

PROJECT SETTINGS (`.octopus/ue-angelscript.json`, object `check_struct_specifiers`)
-------------------------------------------------------------------------------
  aliases                 asset name -> script struct name, for pairs that do not follow
                          `<AssetName>` -> `F<AssetName>`
  noCounterpart           asset name -> reason: UserDefinedStructs with no script counterpart
  scriptOnly              script struct -> reason: script structs with no Blueprint original
  expectedDivergence      list of {struct, field, reason}: decided divergences
  discriminatorNoteLines  lines printed under the discriminator line of the report
  selfTest                {mutations: [{file, find, replace, regex, what}], expect: [[struct, field, axis]]}

A CLEAN RUN SAYS WHY IT IS CLEAN
--------------------------------
The report always prints what it compared AND what it did not. A clean run states the field count
it checked. "0 defects" with "0 fields compared" is a broken tool.
"""

import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402
import _export  # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("check_struct_specifiers", {})

# Relative paths resolve against the project root.
os.chdir(ROOT)

VERBOSE = "--verbose" in sys.argv
AS_JSON = "--json" in sys.argv

# --script-root runs the checker against a copy of Script/, so the self-test can plant defects
# without a write to the live tree.
SCRIPT_ROOT = "Script"
for _arg in sys.argv[1:]:
    if _arg.startswith("--script-root="):
        SCRIPT_ROOT = _arg.split("=", 1)[1]


# =================================================================================================
# PAIRING. Discovered in BOTH directions. The default rule is `<AssetName>` -> `F<AssetName>`.
# ALIASES holds the pairs that do not follow it. A UserDefinedStruct that matches neither is
# reported UNPAIRED and exits 2.
# =================================================================================================
ALIASES = dict(CFG.get("aliases", {}))

# UserDefinedStructs with no script counterpart, each with a reason.
NO_COUNTERPART_EXPECTED = dict(CFG.get("noCounterpart", {}))

# Script structs with no Blueprint original, each with a reason.
SCRIPT_ONLY_EXPECTED = dict(CFG.get("scriptOnly", {}))

# Decided divergences. Key: (script struct, Blueprint VarName) -> reason.
EXPECTED_DIVERGENCE = {(e["struct"], e["field"]): e["reason"]
                       for e in CFG.get("expectedDivergence", [])}

DISCRIMINATOR_NOTE_LINES = CFG.get("discriminatorNoteLines", [
    "   A scan for `UserDefinedStruct` instead matches every package that USES a struct,",
    "   because each user imports that class name.",
])


# =================================================================================================
# The Blueprint export.
# =================================================================================================
def load_struct_assets():
    """(assets, read errors) from `structs.json`: asset stem -> {path, fields, fieldsError, ...}."""
    data = _export.load("structs")
    return data.get("structs", {}), list(data.get("errors", []))


def asset_fields(entry):
    """The specifier fields of one struct asset, in the shape the comparison reads."""
    return [{"name": f["name"], "raw": f.get("raw"), "SaveGame": bool(f.get("saveGame")),
             "Container": f.get("container"), "Friendly": f.get("friendly"),
             "Category": f.get("category")}
            for f in entry.get("fields") or []]


# =================================================================================================
# .as reading.
# =================================================================================================
# The inner group accepts one level of nesting, so meta=(DisplayName="x") does not end the
# specifier list at its own closing paren.
UPROPERTY_RE = re.compile(
    r'UPROPERTY\(((?:[^()]|\([^()]*\))*)\)\s*(?://[^\n]*)?\s*\n\s*'
    r'([A-Za-z_][\w:<>, \t]*?)\s+(\w+)\s*(?:=[^;]*)?;')


def parse_script_structs():
    structs = {}
    for path in sorted(glob.glob(os.path.join(SCRIPT_ROOT, "**", "*.as"), recursive=True)):
        source = open(path, encoding="utf-8", errors="replace").read()
        for match in re.finditer(r"^struct\s+(F\w+)", source, re.M):
            name = match.group(1)
            brace = source.find("{", match.start())
            if brace < 0:
                continue
            depth, pos = 0, brace
            while pos < len(source):
                if source[pos] == "{":
                    depth += 1
                elif source[pos] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                pos += 1
            body = source[brace:pos]
            fields = []
            for prop in UPROPERTY_RE.finditer(body):
                spec, typ, field = prop.group(1), prop.group(2).strip(), prop.group(3)
                display = re.search(r'DisplayName\s*=\s*"([^"]*)"', spec)
                fields.append({"name": field, "type": typ, "spec": spec,
                               "display": display.group(1) if display else None})
            structs[name] = {"file": path.replace(os.sep, "/"), "fields": fields}
    return structs


# =================================================================================================
# The comparison.
# =================================================================================================
def main():
    errors = []      # tool could not do its job - exit 2
    defects = []     # real mismatches - exit 1
    expected = []    # divergences with a recorded reason
    unpaired = []
    compared_fields = 0
    compared_structs = 0

    exported, read_errors = load_struct_assets()
    errors.extend(read_errors)
    assets = sorted(exported)

    script = parse_script_structs()
    paired_script_names = set()

    for stem in assets:
        entry = exported[stem]
        asset = entry.get("path", stem)
        if stem in NO_COUNTERPART_EXPECTED:
            continue
        target = ALIASES.get(stem, "F" + stem)
        struct_def = script.get(target)
        if struct_def is None:
            unpaired.append((asset, target))
            continue
        paired_script_names.add(target)

        if entry.get("fieldsError") or not entry.get("fields"):
            errors.append("%s: %s" % (asset, entry.get("fieldsError") or "no fields in the export"))
            continue
        bp_fields = asset_fields(entry)

        by_name = {f["name"].lower(): f for f in struct_def["fields"]}
        by_display = {f["display"].lower(): f for f in struct_def["fields"] if f["display"]}
        compared_structs += 1

        for bp in bp_fields:
            var, friendly = bp["name"], bp["Friendly"]
            container = bp["Container"] or ""
            reason = EXPECTED_DIVERGENCE.get((target, var))
            match = by_display.get(var.lower()) or by_name.get(var.lower())

            if match is None:
                if reason:
                    expected.append((target, var, reason))
                else:
                    defects.append((target, struct_def["file"], var, "NO PIN MATCH",
                                    "the Blueprint field has no script property of that name and no "
                                    "meta=(DisplayName) bridging it - the pin cannot rewire, and if "
                                    "the field is absent entirely its data is dropped silently"))
                continue

            compared_fields += 1

            # Axis 4 - SaveGame. It is checked first, because it fails with no symptom.
            if bp["SaveGame"] and "SaveGame" not in match["spec"]:
                defects.append((target, struct_def["file"], var, "SAVEGAME LOST",
                                "the asset sets bEnableSaveGame; script property `%s` has no "
                                "SaveGame specifier. A save system that serialises on "
                                "CPF_SaveGame does not persist this field - it compiles, "
                                "replicates and plays correctly, then comes back empty from a "
                                "save" % match["name"]))

            # Axis 3 - container-ness.
            typ = match["type"]
            wanted = ("TArray" if "Array" in container else
                      "TMap" if "Map" in container else
                      "TSet" if "Set" in container else None)
            if wanted and not typ.startswith(wanted):
                if reason:
                    expected.append((target, var, reason))
                else:
                    defects.append((target, struct_def["file"], var, "CONTAINER FLATTENED",
                                    "the asset says %s; script declares `%s`. A flattened container "
                                    "compiles and then holds one value where the game wrote many"
                                    % (container, typ)))
            elif container.endswith("None") and typ.split("<")[0] in ("TArray", "TMap", "TSet"):
                defects.append((target, struct_def["file"], var, "CONTAINER INVENTED",
                                "the asset says the field is scalar; script declares `%s`" % typ))
            elif not container:
                errors.append("%s :: %s - ContainerType could not be decoded, so container-ness "
                              "was NOT checked on this field" % (target, var))

            # Axis 2 - DisplayName. Only meaningful when the asset's FriendlyName is informative.
            if friendly:
                display = match["display"]
                if friendly.lower() != var.lower() and (display or "").lower() != friendly.lower():
                    if not reason:
                        defects.append((target, struct_def["file"], var, "DISPLAYNAME MISSING",
                                        "the asset's FriendlyName is %r; script DisplayName is %r"
                                        % (friendly, display)))
                elif display and display.lower() not in (var.lower(), friendly.lower()):
                    if reason:
                        expected.append((target, var, reason))
                    else:
                        defects.append((target, struct_def["file"], var, "DISPLAYNAME DRIFT",
                                        "script declares DisplayName=%r, which matches neither the "
                                        "asset's VarName %r nor its FriendlyName %r - the pin "
                                        "matches nothing" % (display, var, friendly)))

            if VERBOSE and not reason:
                print("    ok  %-24s %-34s %s%s"
                      % (var, typ, container.replace("EPinContainerType::", ""),
                         "  SaveGame" if bp["SaveGame"] else ""))

    script_only = [n for n in sorted(script) if n not in paired_script_names
                   and n not in SCRIPT_ONLY_EXPECTED]

    if AS_JSON:
        print(json.dumps({
            "assets_found": len(assets), "structs_compared": compared_structs,
            "fields_compared": compared_fields,
            "defects": [{"struct": d[0], "file": d[1], "field": d[2],
                         "axis": d[3], "detail": d[4]} for d in defects],
            "expected": [{"struct": e[0], "field": e[1], "reason": e[2]} for e in expected],
            "unpaired": unpaired, "script_only_unexplained": script_only, "errors": errors,
        }, indent=1))
        return 2 if (errors or unpaired or script_only) else (1 if defects else 0)

    print("UserDefinedStruct assets in the export: %d" % len(assets))
    print("   the exporter selects the assets that hold `UserDefinedStructEditorData`.")
    for line in DISCRIMINATOR_NOTE_LINES:
        print(line)
    print("   %d excluded with a recorded reason, %d expected to pair."
          % (len(NO_COUNTERPART_EXPECTED), len(assets) - len(NO_COUNTERPART_EXPECTED)))

    if defects:
        print("\nDEFECTS: %d" % len(defects))
        for name, path, field, axis, detail in defects:
            print("\n  %s :: %s   [%s]" % (name, field, axis))
            print("      %s" % path)
            for line in re.findall(r".{1,88}(?:\s|$)", detail):
                if line.strip():
                    print("      %s" % line.strip())
    else:
        print("\nDEFECTS: 0")

    print("\nCOMPARED: %d field(s) across %d struct(s), on four axes -"
          % (compared_fields, compared_structs))
    print("   NAME (VarName vs property name / DisplayName, case-insensitive, as FName is),")
    print("   DISPLAYNAME (FriendlyName vs meta=(DisplayName)), CONTAINER (Array/Set/Map),")
    print("   SAVEGAME (bEnableSaveGame vs the SaveGame specifier).")
    if not defects:
        print("   0 defects across %d compared fields is a CLEAN run, not an idle one." % compared_fields)
        print("   If that field count is 0 or implausibly low, the tool is broken - not the tree.")

    print("\nNOT CHECKED, deliberately:")
    print("   the SCALAR TYPE. The script spelling (int32, float64, FName) does not equal the pin")
    print("   category string, and a mapping table would go stale. Same call the member checker makes.")
    print("   Also unchecked: default values, tooltips, field ORDER, and the SubCategoryObject that")
    print("   names a field's enum or struct - so a field typed as the WRONG enum still passes here.")

    if expected:
        print("\nEXPECTED DIVERGENCES: %d - each has a recorded reason. Read it before 'fixing' one."
              % len(expected))
        for name, field, reason in sorted(set(expected)):
            print("\n   %s :: %s" % (name, field))
            for line in re.findall(r".{1,86}(?:\s|$)", reason):
                if line.strip():
                    print("      %s" % line.strip())

    print("\nCOVERAGE - both directions, so a new struct is UNCOVERED, never invisible:")
    if unpaired:
        print("   !! %d UserDefinedStruct(s) with NO script struct and NO recorded reason:"
              % len(unpaired))
        for asset, target in unpaired:
            print("        %s  (looked for %s)" % (asset, target))
        print("      Declare it, or add it to NO_COUNTERPART_EXPECTED WITH A REASON.")
    else:
        print("   every in-scope UserDefinedStruct pairs to a script struct.")
    if script_only:
        print("   !! %d script struct(s) with no Blueprint original and no entry in "
              "SCRIPT_ONLY_EXPECTED:" % len(script_only))
        for name in script_only:
            print("        %-28s %s" % (name, script[name]["file"]))
    else:
        print("   every script struct is either paired or listed in SCRIPT_ONLY_EXPECTED.")
    print("   excluded by NO_COUNTERPART_EXPECTED:")
    for name, reason in sorted(NO_COUNTERPART_EXPECTED.items()):
        print("        %-38s %s" % (name, reason))

    if errors:
        print("\n!! ERRORS: %d - these were NOT compared. This run does NOT clear them." % len(errors))
        for line in errors:
            print("      %s" % line)
        print("   An unreadable asset is a failure, never a pass. Fix or exclude it explicitly.")

    return 2 if (errors or unpaired or script_only) else (1 if defects else 0)


_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
          8: "eight", 9: "nine"}


def self_test():
    """Plant the defects of the `selfTest` fixture in a throwaway copy and demand they be caught.

    A green run here means the four axes are live. A clean run of the checker without this test
    proves nothing.
    """
    import shutil
    import subprocess
    import tempfile

    fixture = CFG.get("selfTest") or {}
    mutations = fixture.get("mutations") or []
    wanted = {tuple(e) for e in fixture.get("expect") or []}
    if not mutations or not wanted:
        print("SELF-TEST SKIPPED: no selfTest fixture is set in .octopus/ue-angelscript.json "
              "(check_struct_specifiers.selfTest).")
        return 0

    _export.load("structs")
    temp = tempfile.mkdtemp(prefix="structcheck-")
    try:
        copy = os.path.join(temp, "Script")
        shutil.copytree("Script", copy)

        for mut in mutations:
            target = os.path.join(copy, *mut["file"].split("/"))
            text = open(target, encoding="utf-8").read()
            if mut.get("regex"):
                mutated = re.sub(mut["find"], mut["replace"], text)
            else:
                mutated = text.replace(mut["find"], mut["replace"])
            if mutated == text:
                print("SELF-TEST INCONCLUSIVE: could not %s - the" % mut.get("what", mut["file"]))
                print("declaration's spelling changed, so this test is no longer testing anything.")
                return 2
            open(target, "w", encoding="utf-8").write(mutated)

        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--project", ROOT,
             "--json", "--script-root=" + copy],
            capture_output=True, text=True)
        report = json.loads(proc.stdout)
        found = {(d["struct"], d["field"], d["axis"]) for d in report["defects"]}
        missed = wanted - found
        print("SELF-TEST - %s known defects reintroduced in a temp copy of Script/"
              % _WORDS.get(len(wanted), str(len(wanted))))
        print("   fields compared: %d across %d struct(s)"
              % (report["fields_compared"], report["structs_compared"]))
        for item in sorted(wanted):
            print("   %-6s %s :: %s [%s]"
                  % ("CAUGHT" if item in found else "MISSED", item[0], item[1], item[2]))
        if missed:
            print("\n!! THE CHECKER IS ASLEEP. It missed %d of %d planted defects, so any clean run"
                  % (len(missed), len(wanted)))
            print("   it prints against the real tree means NOTHING. Fix the parsing before trusting it.")
            return 2
        extra = len(report["defects"]) - len(wanted)
        print("   all %d caught; %d other defect(s) in the copy (expected 0 - the live tree is clean)"
              % (len(wanted), extra))
        return 0 if extra == 0 else 1
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else main())
