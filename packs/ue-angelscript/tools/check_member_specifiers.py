"""Check every script-parent member declaration against its Blueprint's member variables.

WHAT IT CATCHES
---------------
Three axes. The AngelScript compiler and a Blueprint compile see none of them:

  1. CONTAINER-NESS. A Blueprint variable line carries `PinCategory` and `ContainerType` next to each
     other. A reader that reads only the first turns an `Array<Element>` into a scalar.

  2. CPF_Net. `PropertyFlags & 0x20`. A member moved up without `Replicated` compiles and stops
     replicating.

  3. CPF_SaveGame. `PropertyFlags & 0x1000000`. A save system that serialises on this flag does not
     persist a member moved up without it. There is no compile error and no replication symptom.

WHEN TO RUN IT
--------------
`remove_variable` on the child Blueprint DESTROYS the Blueprint's read and write nodes. A rebuild
onto the parent's property is correct only when the parent's property matches, and a mismatched
container, Net or SaveGame flag still compiles. Run this before any `remove_variable` pass.

USAGE
-----
    python check_member_specifiers.py                 # normal run
    python check_member_specifiers.py --map=old=new   # ad-hoc rename, one member
    python check_member_specifiers.py --no-map-file   # ignore the rename map file
    python check_member_specifiers.py --guess         # ALSO try shape-based auto-pairing,
                                                      # every hit reported as a GUESS

Reads only. Compares each `UPROPERTY` declaration in the script parents of PAIRS against the
member variables of the NEWEST text export of that Blueprint.

THE BLUEPRINT DATA comes from the Blueprint export `variables.json`, and this tool reads nothing
else of a Blueprint. The `blueprint` pack writes the file:

    python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" variables --project <project>

The file holds, per Blueprint, every text export newest first with its member variables
(container, pin category, PropertyFlags), and the parent chain of every Blueprint. When the file
is missing, the tool prints the command and exits 2.

PAIRS is the `pairs` list of the project settings plus every pair discovered from the export: a
script file that declares a class a Blueprint parents to directly, when the Blueprint has a text
export. If a Blueprint has no text export the report says so; export it first.

The scalar type is not matched: the script spelling (`int32`, `float64`, `FName`) is not the pin
category string.

HOW A MEMBER GETS PAIRED - FOUR SOURCES, IN ORDER
-------------------------------------------------
  1. NAME         - the script member and the Blueprint member share a name.
  2. MAP          - the rename map file (`renameMapFile`), or `--map=`. A row may name a DIFFERENT
                    stem, because a member can come from a Blueprint other than the child.
  3. PROVENANCE   - a `// Migrated from <Blueprint> Blueprint variable "<name>"` comment immediately
                    above the declaration. The tool reads the source name from it.
  4. GUESS        - `--guess` only. Every hit is LABELLED a guess and is not in the verified count.
                    Shapes: `x` -> `bX`, `x` -> `<prefix>X` for each of `guessPrefixes`, `x` -> `X`.

HISTORICAL EVIDENCE
-------------------
When the Blueprint counterpart is already DELETED, the newest export no longer has it. The tool then
reads the NEWEST EXPORT THAT STILL CONTAINED IT and names that file. A mismatch found that way is
still a real mismatch: those were the member's flags when it was moved. Historical evidence is
counted SEPARATELY from live evidence, because the export may pre-date a deliberate change.

THE BUCKETS FOR EVERYTHING STILL UNPAIRED
-----------------------------------------
  COMPONENT        `DefaultComponent` - lives in the SimpleConstructionScript, never in
                   `NewVariables`. It cannot be compared.
  SCRIPT-ONLY      listed in SCRIPT_ONLY_EXPECTED (`scriptOnly`) with a reason.
  PROVENANCE-ONLY  the .as names the Blueprint variable it came from, but NO export on disk ever
                   contained that name. The provenance comment is the only record.
  UNKNOWN          none of the above. This is the number to drive down.

PROJECT SETTINGS (`.octopus/ue-angelscript.json`, object `check_member_specifiers`)
-------------------------------------------------------------------------------
  pairs                   [[script file, Blueprint name]] - the hand-listed pairs
  exemptNoBlueprintChild  script files with no Blueprint child, so nothing to compare
  scriptOnly              [{file, member, reason}] - members with no Blueprint counterpart
  standingReview          [[script file, member, text]] - notes printed on every run
  expectedFlagDivergence  [{file, member, axes: {CONTAINER|REPLICATION|SAVEGAME: reason}}]
  renameMapFile           the rename map, relative to the project root
  guessPrefixes           name prefixes the --guess shapes strip
  structCheckCommand      the command the report names for the struct comparison
  structNoteLines         lines printed after that command
"""
import glob
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402
import _export  # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("check_member_specifiers", {})
HERE = os.path.dirname(os.path.abspath(__file__))

# Every path in this tool is relative to the project root.
os.chdir(ROOT)

HAND_PAIRS = [tuple(p) for p in CFG.get("pairs", [])]
EXPORT = _export.load("variables")
EXPORTED = EXPORT.get("blueprints", {})


def _discover_pairs(hand):
    """PAIRS from the export: every .as file that declares a class some Blueprint parents to
    directly, paired with that Blueprint, when the Blueprint has a text export. Hand rows whose
    script file is gone are dropped. A new parent is covered the day it lands."""
    pairs = [(pth, stem) for pth, stem in hand if os.path.exists(pth)]
    have = {os.path.normpath(pth) for pth, _ in pairs}
    class_file = {}
    for f in glob.glob("Script/**/*.as", recursive=True):
        src = open(f, encoding="utf-8", errors="ignore").read()
        for m in re.finditer(r"^\s*class\s+([A-Za-z_]\w*)", src, re.M):
            class_file[m.group(1)] = f.replace(os.sep, "/")
    for stem, info in sorted(EXPORT.get("parents", {}).items()):
        chain = info.get("chain") or []
        if info.get("landing") != "script" or len(chain) != 1:
            continue
        cls = chain[0]
        f = class_file.get(cls) or class_file.get("U" + cls) or class_file.get("A" + cls)
        if not f:
            continue
        FILES_WITH_BLUEPRINT_CHILD.add(os.path.normpath(f))
        if os.path.normpath(f) in have:
            continue
        if stem not in EXPORTED:
            NO_EXPORT.add(os.path.normpath(f))
            continue
        pairs.append((f, stem))
        have.add(os.path.normpath(f))
    return pairs


FILES_WITH_BLUEPRINT_CHILD = set()   # script files some Blueprint parents to, per the export
NO_EXPORT = set()                    # ...of which none has a text export to compare against
PAIRS = _discover_pairs(HAND_PAIRS)

# Script files with no Blueprint child. The check has nothing to compare them against, so they are
# listed as exempt and not as a permanent "no export".
EXEMPT_NO_BLUEPRINT_CHILD = list(CFG.get("exemptNoBlueprintChild", []))

# Members that exist ONLY on the script side, with the reason. Keyed by (basename of the .as file,
# member name). These are reported as SCRIPT-ONLY and not as UNKNOWN.
SCRIPT_ONLY_EXPECTED = {(e["file"], e["member"]): e["reason"] for e in CFG.get("scriptOnly", [])}

# Standing REVIEW notes: questions the comparison cannot prove. Printed on every run.
STANDING_REVIEW = [tuple(e) for e in CFG.get("standingReview", [])]

DECL = re.compile(r'UPROPERTY\(([^)]*)\)\s*\n\s*([\w:]+(?:<[^>]*>)?)\s+(\w+)\s*(?:=[^;]*)?;')
# `// Migrated from BP_ExampleActor Blueprint variable "health"` and the backticked variant
# `Migrated from the `BP_ExampleActor` Blueprint variable `health``.
PROV = re.compile(r'Migrated from (?:the )?[`"\']?([A-Za-z0-9_]+)[`"\']?\s+(?:Blueprint )?variable\s+[`"\']([^`"\']+)[`"\']')

MAP_FILE_REL = CFG.get("renameMapFile", "")
MAP_FILE = os.path.join(ROOT, MAP_FILE_REL) if MAP_FILE_REL else ""
MAP_DISPLAY = MAP_FILE_REL.replace(os.sep, "/") if MAP_FILE_REL else "the rename map file"
GUESS_PREFIXES = list(CFG.get("guessPrefixes", []))
STRUCT_CHECK_COMMAND = CFG.get("structCheckCommand",
                               'python "%s"' % os.path.join(HERE, "check_struct_specifiers.py"))
STRUCT_NOTE_LINES = list(CFG.get("structNoteLines", []))


def _vars_of(exported):
    """The member variables of one export entry, keyed by name."""
    out = {}
    for name, v in (exported.get("variables") or {}).items():
        flags = int(v.get("flags", 0))
        out[name] = {
            "ctr": v.get("container", "None"),
            "cat": v.get("category", "?"),
            "flags": flags,
            "net": bool(flags & 0x20),
            # CPF_SaveGame. A save system that serialises on this flag does not persist a member
            # without it.
            "save": bool(flags & 0x1000000),
        }
    return out


_HIST_CACHE = {}


def history(stem):
    """Every text export of `stem`, NEWEST FIRST, as (file name, vars, decoded).

    The export file holds them in that order: by MTIME, because the trailing number of an export
    is not chronological."""
    if stem in _HIST_CACHE:
        return _HIST_CACHE[stem]
    out = []
    for e in (EXPORTED.get(stem) or {}).get("exports", []):
        if e.get("error"):
            print("   !! could not read %s: %s" % (e.get("file"), e["error"]))
            continue
        out.append((e.get("file", "?"), _vars_of(e), bool(e.get("decoded"))))
    _HIST_CACHE[stem] = out
    return out


def find_in_history(stem, varname, skip_newest=False):
    """Newest export of `stem` that declares `varname`, or None. Returns (file, varinfo)."""
    h = history(stem)
    for i, (f, vs, _decoded) in enumerate(h):
        if skip_newest and i == 0:
            continue
        if varname in vs:
            return f, vs[varname]
    return None


# --------------------------------------------------------------------------------------------
# The rename map. The map file is the bulk route; `--map=` is for one member.
# Both produce entries keyed by scriptName -> (sourceStem, blueprintName, evidence, isGuess).
# Map file format: TAB-separated `<stem> <blueprintName> <scriptName> [<evidence>]`, `#` comments.
# A row whose evidence begins `GUESS` is a guess.
# --------------------------------------------------------------------------------------------
def load_map():
    rows = {}
    if "--no-map-file" not in sys.argv and MAP_FILE and os.path.exists(MAP_FILE):
        for ln, line in enumerate(open(MAP_FILE, encoding="utf-8"), 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            parts = [p for p in parts if p != ""]
            if len(parts) < 3:
                print("   !! %s:%d needs at least 3 TAB-separated columns: %r"
                      % (os.path.basename(MAP_FILE), ln, line))
                continue
            src_stem, bpname, scriptname = parts[0].strip(), parts[1].strip(), parts[2].strip()
            evidence = parts[3].strip() if len(parts) > 3 else ""
            rows[scriptname] = (src_stem, bpname, evidence or "(no evidence given)",
                                evidence.upper().startswith("GUESS"))
    for a in sys.argv[1:]:
        if a.startswith("--map=") and "=" in a[6:]:
            o, n = a[6:].split("=", 1)
            rows[n.strip()] = ("*", o.strip(), "--map= on the command line", False)
    return rows


RENAME = load_map()


# Shape heuristics, --guess only. Every hit is labelled a guess wherever it appears.
def guess_names(name):
    out = []
    if len(name) > 1 and name[0] == "b" and name[1].isupper():
        out.append(name[1].lower() + name[2:])                     # bIsOpen    -> isOpen
    for prefix in GUESS_PREFIXES:
        n = len(prefix)
        if name.startswith(prefix) and len(name) > n and name[n].isupper():
            out.append(name[n].lower() + name[n + 1:])             # <p>Target  -> target
            out.append(name[n:])                                   # <p>Target  -> Target
    if name and name[0].isupper():
        out.append(name[0].lower() + name[1:])                     # HomeIndex  -> homeIndex
    return out


# Decided divergences from the authored flags, with the reason. Key: (script file basename,
# member) -> {axis: reason}. Axis is CONTAINER, REPLICATION or SAVEGAME. verdict() skips an axis
# that has a row here.
EXPECTED_FLAG_DIVERGENCE = {(e["file"], e["member"]): dict(e["axes"])
                            for e in CFG.get("expectedFlagDivergence", [])}


def verdict(spec, typ, v, path="", name=""):
    script_is_container = typ.startswith("TArray<") or typ.startswith("TMap<") or typ.startswith("TSet<")
    bp_is_container = v["ctr"] in ("Array", "Map", "Set")
    decided = EXPECTED_FLAG_DIVERGENCE.get((os.path.basename(path), name), {})
    problems = []
    if script_is_container != bp_is_container and "CONTAINER" not in decided:
        problems.append("CONTAINER script=%s bp=%s" % (typ, v["ctr"]))
    if ("Replicated" in spec) != v["net"] and "REPLICATION" not in decided:
        problems.append("REPLICATION script=%s bp=%s" % ("Replicated" in spec, v["net"]))
    if ("SaveGame" in spec) != v["save"] and "SAVEGAME" not in decided:
        problems.append("SAVEGAME script=%s bp=%s" % ("SaveGame" in spec, v["save"]))
    return problems


def provenance(src, decl_start, prev_end):
    """The nearest `Migrated from X variable "y"` comment belonging to this declaration."""
    best = None
    for m in PROV.finditer(src, prev_end, decl_start):
        best = (m.group(1), m.group(2))
    return best


live_issues = 0
hist_issues = 0
n_live_ok = n_hist_ok = 0
n_component = n_scriptonly = n_provonly = n_unknown = n_guess = 0
unresolved_map_rows = []
report = []

for path, stem in PAIRS:
    if not os.path.exists(path):
        continue
    h = history(stem)
    if not h:
        print("%-46s no text export" % os.path.basename(path))
        continue
    curfile, cur, decoded = h[0]

    # Zero variables has TWO causes:
    #   a) the file did not decode - nothing matches, including 'Begin Object'. This is an error.
    #   b) the Blueprint declares no member variables. The file stays in the run, so its script
    #      members are compared against history or counted as not compared.
    if not cur and not decoded:
        print("%-46s !! UNREADABLE %s - class NOT checked"
              % (os.path.basename(path), os.path.basename(curfile)))
        live_issues += 1
        continue

    src = open(path, encoding="utf-8", errors="ignore").read()
    rows_live, rows_hist, notes = [], [], []
    prev_end = 0

    for m in DECL.finditer(src):
        spec, typ, name = m.group(1), m.group(2), m.group(3)
        prov = provenance(src, m.start(), prev_end)
        prev_end = m.end()

        # ---- resolve a counterpart, best evidence first -------------------------------------
        found, how, why, is_guess, from_stem = None, None, "", False, stem
        if name in cur:
            found, how = (curfile, cur[name]), "NAME"
        if found is None and name in RENAME:
            src_stem, bpname, why, is_guess = RENAME[name]
            s = stem if src_stem == "*" else src_stem
            hit = find_in_history(s, bpname)
            if hit:
                found, from_stem = hit, s
                how = "MAP" if s == stem else "MAP@" + s
            else:
                unresolved_map_rows.append((os.path.basename(path), name, s, bpname, why))
        if found is None and prov:
            pstem, pvar = prov
            hit = find_in_history(pstem, pvar)
            if hit:
                found, from_stem = hit, pstem
                how = "PROV" if pstem == stem else "PROV@" + pstem
                why = 'provenance comment: "%s" on %s' % (pvar, pstem)
        if found is None and name not in RENAME:
            hit = find_in_history(stem, name, skip_newest=True)
            if hit:
                found, how = hit, "NAME"
        if found is None and "--guess" in sys.argv:
            for g in guess_names(name):
                hit = find_in_history(stem, g)
                if hit:
                    found, how, why, is_guess = hit, "GUESS", "shape %s <- %s" % (name, g), True
                    break

        # ---- classify ------------------------------------------------------------------------
        if found is None:
            if "DefaultComponent" in spec:
                n_component += 1
                notes.append(("COMPONENT", name, typ,
                              "SimpleConstructionScript subobject - never in NewVariables"))
            elif (os.path.basename(path), name) in SCRIPT_ONLY_EXPECTED:
                n_scriptonly += 1
                notes.append(("SCRIPT-ONLY", name, typ,
                              SCRIPT_ONLY_EXPECTED[(os.path.basename(path), name)]))
            elif prov:
                n_provonly += 1
                notes.append(("PROVENANCE-ONLY", name, typ,
                              'came from %s."%s"; NO export on disk ever declared it - the move '
                              'pre-dates the earliest export' % (prov[0], prov[1])))
            else:
                n_unknown += 1
                notes.append(("UNKNOWN", name, typ, "no counterpart by name, map, or provenance"))
            continue

        f, v = found
        problems = verdict(spec, typ, v, path, name)
        # "Live" means: this is the NEWEST export of the Blueprint the counterpart lives in. That is
        # not always the child Blueprint; a cross-stem hit can be current too.
        live = (f == history(from_stem)[0][0])
        if is_guess:
            n_guess += 1
        if live:
            if problems:
                rows_live.append((name, typ, how, why, v, "; ".join(problems)))
                live_issues += 1
            else:
                n_live_ok += 1
        else:
            if problems:
                rows_hist.append((name, typ, how, why, v, os.path.basename(f), "; ".join(problems)))
                hist_issues += 1
            else:
                n_hist_ok += 1

    if rows_live or rows_hist or notes:
        report.append((os.path.basename(path), os.path.basename(curfile), rows_live, rows_hist, notes))

# ------------------------------------------------------------------------------------------------
for base, exportname, rows_live, rows_hist, notes in report:
    print("\n== %s   (vs %s) ==" % (base, exportname))
    for name, typ, how, why, v, prob in rows_live:
        print("   MISMATCH  %-28s %-26s [%s] %s" % (name, typ, how, prob))
        if why:
            print("             %s" % why)
    for name, typ, how, why, v, hf, prob in rows_hist:
        print("   MISMATCH  %-28s %-26s [%s, historical: %s, flags=%d]"
              % (name, typ, how, hf, v["flags"]))
        print("             %s" % prob)
        if why:
            print("             %s" % why)
    buckets = {}
    for kind, name, typ, reason in notes:
        buckets.setdefault(kind, []).append((name, typ, reason))
    for kind in ("UNKNOWN", "PROVENANCE-ONLY", "COMPONENT", "SCRIPT-ONLY"):
        if kind not in buckets:
            continue
        print("   -- %s (%d)" % (kind, len(buckets[kind])))
        for name, typ, reason in buckets[kind]:
            print("      %-28s %-26s %s" % (name, typ, reason))

if unresolved_map_rows:
    print("\n!! MAP ROWS THAT RESOLVE TO NOTHING - the named Blueprint member is in no export of the")
    print("   named stem, so the row checks nothing. Fix the stem/name, or delete the row.")
    for base, name, s, bpname, why in unresolved_map_rows:
        print("      %-24s %-26s -> %s.%s   (%s)" % (base, name, s, bpname, why))

not_compared = n_component + n_scriptonly + n_provonly + n_unknown
print("\nmismatches:", live_issues, "- against the CURRENT export")
print("mismatches (historical evidence):", hist_issues,
      "- counterpart already deleted; flags read from the newest export that still had it")
print("verified: %d against the current export, %d against a historical one%s"
      % (n_live_ok, n_hist_ok, (", of which %d rest on a GUESS pairing" % n_guess) if n_guess else ""))
if not_compared:
    print("NOT COMPARED:", not_compared,
          "- these were skipped entirely, so 'mismatches' above does NOT cover them.")
    print("   COMPONENT %d - SimpleConstructionScript subobjects, structurally uncomparable"
          % n_component)
    print("   SCRIPT-ONLY %d - listed in SCRIPT_ONLY_EXPECTED with a reason" % n_scriptonly)
    print("   PROVENANCE-ONLY %d - the .as names its source variable, but no export on disk has it"
          % n_provonly)
    print("   UNKNOWN %d - NOT EXPLAINED. This is the number to drive down." % n_unknown)
    print("Supply a row in %s for each UNKNOWN, or a SCRIPT_ONLY_EXPECTED entry" % MAP_DISPLAY)
    print("with a reason - otherwise container / CPF_Net / CPF_SaveGame go unchecked on exactly the")
    print("migrations this project performs most often.")
else:
    print("NOT COMPARED: 0 - every script member paired to a Blueprint member.")

if STANDING_REVIEW:
    print("\nSTANDING REVIEW - not provable by comparison, and not to be forgotten:")
    for p, name, text in STANDING_REVIEW:
        print("   %s :: %s" % (p, name))
        for line in re.findall(r".{1,92}(?:\s|$)", text):
            if line.strip():
                print("      %s" % line.strip())


# ---------------------------------------------------------------------------------------------
# COVERAGE. A script parent absent from PAIRS would otherwise be silently skipped. The tool lists
# every Script/ file that declares a UPROPERTY and says which ones it did not check.
# ---------------------------------------------------------------------------------------------
checked = {os.path.normpath(_p) for _p, _ in PAIRS}
missing_path = [_p for _p, _ in PAIRS if not os.path.exists(_p)]

unchecked = []
for _f in sorted(glob.glob("Script/**/*.as", recursive=True)):
    _src = open(_f, encoding="utf-8", errors="ignore").read()
    if "UPROPERTY(" not in _src:
        continue
    if os.path.normpath(_f) in checked:
        continue
    unchecked.append(_f.replace(os.sep, "/"))

structs = [f for f in unchecked if "/DataTypes/" in f]
others = [f for f in unchecked if "/DataTypes/" not in f]

print("\ncoverage: %d file(s) in PAIRS, %d file(s) with members not checked"
      % (len(checked), len(unchecked)))

if missing_path:
    print("\n  !! PAIRS names path(s) that DO NOT EXIST - these check nothing:")
    for _p in missing_path:
        print("      ", _p)

if others:
    exempt = {os.path.normpath(p) for p in EXEMPT_NO_BLUEPRINT_CHILD}
    real = [f for f in others if os.path.normpath(f) not in exempt]
    print("\n  NOT CHECKED, and not a DataTypes struct:")
    for _f in others:
        _n = os.path.normpath(_f)
        if _n in exempt or _n not in FILES_WITH_BLUEPRINT_CHILD:
            tag = "no Blueprint child - nothing to compare against"
        elif _n in NO_EXPORT:
            tag = "has a Blueprint child and NO text export - export it to check it"
        else:
            tag = "<- ADD TO PAIRS"
        print("       %-52s %s" % (_f, tag))
    if not real:
        print("       ...all of them are on the EXEMPT_NO_BLUEPRINT_CHILD list.")

print("\n  %d DataTypes/ struct file(s) skipped by design: their counterpart is a UserDefinedStruct,"
      % len(structs))
print("  which needs a different comparison than a Blueprint class's NewVariables table.")
print("  THAT COMPARISON NOW EXISTS - run it, this tool does NOT cover these files:")
print("      %s" % STRUCT_CHECK_COMMAND)
for _line in STRUCT_NOTE_LINES:
    print(_line)

# DataTypes/ structs are out of scope for this tool: their counterpart is a UserDefinedStruct.
# `check_struct_specifiers.py` compares them. The two tools together cover the tree.
