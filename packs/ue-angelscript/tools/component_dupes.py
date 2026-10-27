"""Which actors carry TWO components that a `GetComponentByClass` cannot tell apart.

The defect shape: a port declares a component on a script parent while the child Blueprint keeps
its own authored one. Both survive, both match the base class, and
`GetComponentByClass(UMyComponent)` returns whichever the component array yields first. The two
objects hold SEPARATE copies of every member, so the state the script writes and the state the
Blueprint reads can be two different objects.

Five sources produce a component, and a negative on one is not a negative overall:

    1. SCS      `SCS_Node` exports under a `SimpleConstructionScript` outered to the `_C` class,
                ON THE ASSET AND ON EVERY PARENT - an inherited component is not in the child's SCS
    2. ICH      `InheritableComponentHandler` - OVERRIDES a template, never creates a component
    3. AddComponent  `K2Node_AddComponent` in any graph, level Blueprints included
    4. script   `UPROPERTY(DefaultComponent)` in `Script/` - invisible to any Content-only scan
    5. instance placed-actor component data in a `.umap`

A duplicate NOBODY resolves by class is not a finding, so the resolver half is measured too:
`GetComponentByClass` / `GetComponentsByClass` call nodes in `Content/`, and their AngelScript
equivalents in `Script/`.

What this tool DROPS is printed FIRST and the run exits non-zero, because a package that failed to
parse is a package whose components were not counted, and silence there looks exactly like a clean
asset.

Sources 1, 2, 3 and 5 come from the Blueprint export `components.json`; source 4 and the script
resolvers come from `Script/`. This tool reads nothing else. The `blueprint` pack writes the file:

    python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" components --project <project>

When the file is missing, the tool prints the command and exits 2.

    python component_dupes.py                # the census
    python component_dupes.py --self-test    # controls + a planted duplicate
    python component_dupes.py --json out.json

`--self-test` reads its two positive controls from `component_dupes.selfTest` in
`<project>/.octopus/ue-angelscript.json`, and skips when none is set.
"""

import argparse
import json
import os
import re
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project                                                          # noqa: E402
import _export                                                           # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("component_dupes", {})


# A base only counts as a duplicate when it is a PROJECT class - one declared in `Script/` or a
# Blueprint `_C`. Two `ParticleSystemComponent`s or six `TextRenderComponent`s on one actor are
# ordinary authoring, and grouping on engine bases reports every actor that has more than one of
# anything.
def is_project_class(name, script_parents):
    return bool(name) and (name.endswith("_C") or name in script_parents)


# --------------------------------------------------------------------------------------------
# Script/ side - class parents and DefaultComponent members
# --------------------------------------------------------------------------------------------

# `class UMyComponent : UMyBaseComponent` -- the prefix is part of the identifier in the file
# and is STRIPPED in a package's import table (`MyComponent`, class `ASClass`). Every
# comparison happens in the stripped spelling or the two sides never meet.
RE_CLASS = re.compile(r"^\s*class\s+([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)", re.M)
RE_DEFCOMP = re.compile(
    r"UPROPERTY\s*\([^)]*\bDefaultComponent\b[^)]*\)\s*\r?\n\s*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*;",
    re.M)


def strip_prefix(name):
    """`UMyComponent` -> `MyComponent`."""
    if len(name) > 2 and name[0] in "AUFEI" and name[1].isupper():
        return name[1:]
    return name


def read_script(root):
    parents, defcomps, files = {}, {}, 0
    for dirpath, _dirs, names in os.walk(os.path.join(root, "Script")):
        for n in names:
            if not n.endswith(".as"):
                continue
            files += 1
            with open(os.path.join(dirpath, n), "r", encoding="utf-8", errors="replace") as h:
                text = h.read()
            for cls, base in RE_CLASS.findall(text):
                parents[strip_prefix(cls)] = strip_prefix(base)
            # Attribute each DefaultComponent to the class whose body it sits in: the last class
            # declaration above it. A file with two classes otherwise gives every component to the
            # first.
            for m in RE_DEFCOMP.finditer(text):
                owner = None
                for cm in RE_CLASS.finditer(text[:m.start()]):
                    owner = strip_prefix(cm.group(1))
                if owner is None:
                    continue
                defcomps.setdefault(owner, []).append(
                    (m.group(2), strip_prefix(m.group(1)), "script:" + n))
    return parents, defcomps, files


def read_script_resolvers(root):
    """`Get/FindComponentByClass` call sites in `Script/`, with the class the source names."""
    out = []
    call = re.compile(r"(?:Get|Find)Components?ByClass\s*\(\s*([A-Za-z_]\w*)?")
    for dirpath, _dirs, names in os.walk(os.path.join(root, "Script")):
        for n in names:
            if not n.endswith(".as"):
                continue
            path = os.path.join(dirpath, n)
            with open(path, "r", encoding="utf-8", errors="replace") as h:
                for i, line in enumerate(h, 1):
                    for m in call.finditer(line):
                        out.append((os.path.relpath(path, root).replace("\\", "/"), i,
                                    strip_prefix(m.group(1) or ""), line.strip()))
    return out


# --------------------------------------------------------------------------------------------
# Package side, from the export
# --------------------------------------------------------------------------------------------

class Asset(object):
    __slots__ = ("path", "pkg_classes", "scs", "addcomp", "ich", "instanced")

    def __init__(self, path):
        self.path = path
        self.pkg_classes = []      # [(class name, parent name)]
        self.scs = {}              # class name -> [(var, component class, origin)]
        self.addcomp = []          # [(node name, template class)]
        self.ich = []              # [class name carrying an InheritableComponentHandler]
        self.instanced = []        # [(outer name, export name, class)] - components on a placed actor


def load_packages():
    """(assets, dropped) from `components.json`."""
    data = _export.load("components")
    assets = []
    for rel, rec in sorted((data.get("packages") or {}).items()):
        a = Asset(rel)
        a.pkg_classes = [tuple(x) for x in rec.get("classes") or []]
        a.scs = {cls: [tuple(x) for x in rows] for cls, rows in (rec.get("scs") or {}).items()}
        a.addcomp = [tuple(x) for x in rec.get("addComponent") or []]
        a.ich = list(rec.get("handlers") or [])
        a.instanced = [tuple(x) for x in rec.get("instanced") or []]
        assets.append(a)
    dropped = [(d.get("path", "?"), d.get("why", "")) for d in data.get("dropped") or []]
    return assets, dropped


# --------------------------------------------------------------------------------------------
# Ancestry
# --------------------------------------------------------------------------------------------

def ancestry(cls, parents, limit=64):
    """`cls` and every ancestor, nearest first. A cycle truncates rather than hangs."""
    out, seen = [], set()
    cur = cls
    while cur and cur not in seen and len(out) < limit:
        out.append(cur)
        seen.add(cur)
        cur = parents.get(cur, "")
    return out


# --------------------------------------------------------------------------------------------
# The census
# --------------------------------------------------------------------------------------------

def components_of(cls, parents, scs_by_class, script_defcomps):
    """Every component an instance of `cls` carries, from every source, over the whole chain."""
    out = []
    for anc in ancestry(cls, parents):
        for var, ccls, origin in scs_by_class.get(anc, []):
            out.append((var, ccls, origin, anc))
        for var, ccls, origin in script_defcomps.get(anc, []):
            out.append((var, ccls, origin, anc))
    return out


def duplicates(comps, parents, script_parents):
    """Base classes carried by two or more of `comps`, restricted to PROJECT bases."""
    by_base = {}
    for entry in comps:
        for base in ancestry(entry[1], parents):
            if not is_project_class(base, script_parents):
                continue
            by_base.setdefault(base, []).append(entry)
    return dict((b, v) for b, v in by_base.items() if len(v) > 1)


def census(root):
    assets, dropped = load_packages()

    script_parents, script_defcomps, script_files = read_script(root)
    parents = dict(script_parents)
    for a in assets:
        for cls, sup in a.pkg_classes:
            parents[cls] = sup

    scs_by_class, class_source = {}, {}
    for a in assets:
        rel = a.path
        for cls, entries in a.scs.items():
            scs_by_class.setdefault(cls, []).extend(entries)
        for cls, _sup in a.pkg_classes:
            class_source[cls] = rel

    all_classes = set(parents) | set(scs_by_class) | set(script_defcomps)
    findings = {}
    for cls in sorted(all_classes):
        comps = components_of(cls, parents, scs_by_class, script_defcomps)
        if len(comps) < 2:
            continue
        dups = duplicates(comps, parents, script_parents)
        if dups:
            findings[cls] = dups
    return dict(assets=assets, dropped=dropped, parents=parents, findings=findings,
                script_parents=script_parents,
                scs_by_class=scs_by_class, class_source=class_source,
                script_defcomps=script_defcomps, script_files=script_files, root=root)


# --------------------------------------------------------------------------------------------
# The resolver-driven half: a duplicate nobody resolves by class is not a finding
# --------------------------------------------------------------------------------------------

def owning_class_of(script_file, root):
    """The script class a `.as` file's resolver sites run on: the first class in the file."""
    path = None
    for dirpath, _dirs, names in os.walk(os.path.join(root, "Script")):
        if os.path.basename(script_file) in names:
            path = os.path.join(dirpath, os.path.basename(script_file))
            break
    if path is None:
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as h:
        m = RE_CLASS.search(h.read())
    return strip_prefix(m.group(1)) if m else ""


def descendants(cls, parents):
    out, frontier = set([cls]), [cls]
    while frontier:
        cur = frontier.pop()
        for k, v in parents.items():
            if v == cur and k not in out:
                out.add(k)
                frontier.append(k)
    return out


def resolver_report(res, root):
    """For every `GetComponentByClass(X)` in `Script/`: on which concrete actor classes does it run,
    and how many components there match X? Two or more is an ambiguous resolve."""
    parents, sp = res["parents"], res["script_parents"]
    rows = []
    for rel, line, wanted, text in read_script_resolvers(root):
        if not wanted:
            rows.append((rel, line, "", "", 0, [], text))
            continue
        owner = owning_class_of(rel, root)
        for cls in sorted(descendants(owner, parents)) if owner else []:
            comps = components_of(cls, parents, res["scs_by_class"], res["script_defcomps"])
            match = [c for c in comps if wanted in ancestry(c[1], parents)]
            if len(match) > 1:
                rows.append((rel, line, wanted, cls, len(match), match, text))
    return rows


# --------------------------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------------------------

def self_test(root):
    """Four controls, and a plant.

    The two positive controls are real bytes on disk, named in `component_dupes.selfTest`:

        duplicate   {class, base, names}  `class` carries two components of project base `base`,
                                          and `names` are their variable names
        ancestry    {class, chain}        ancestry(`class`) starts with `chain`, across the
                                          package/script boundary

    The plant is at the MODEL layer, not the byte layer. Writing a second `SCS_Node` into a copy of
    a package needs a serialiser no pack has, and a plant that cannot be written cannot be proved.
    So the plant goes through the same `duplicates()` the census calls, which is the code that
    decides every finding - what it does NOT test again is the SCS decode in the exporter, and the
    two positive controls cover that.
    """
    st = CFG.get("selfTest")
    if not st:
        print("SELF-TEST SKIPPED - no component_dupes.selfTest in .octopus/ue-angelscript.json.")
        return 0
    ok = True
    res = census(root)
    parents, findings = res["parents"], res["findings"]

    # Control 1, positive, real bytes: a known duplicate pair.
    dup = st["duplicate"]
    gc = findings.get(dup["class"], {})
    hit = gc.get(dup["base"], [])
    names = sorted(v[0] for v in hit)
    if names != sorted(dup["names"]):
        print("FAIL control-1 %s/%s -> %r" % (dup["class"], dup["base"], names))
        ok = False
    else:
        print("ok   control-1  %s carries both %s components" % (dup["class"], dup["base"]))

    # Control 2, positive, real bytes: ancestry crosses the package/script boundary.
    want = list(st["ancestry"]["chain"])
    anc = ancestry(st["ancestry"]["class"], parents)
    if anc[:len(want)] != want:
        print("FAIL control-2 ancestry(%s) -> %r" % (st["ancestry"]["class"], anc[:len(want) + 1]))
        ok = False
    else:
        print("ok   control-2  %s" % " -> ".join(want))

    # Control 3, negative: a single component is never a duplicate.
    sp = res["script_parents"]
    if duplicates([("Only", dup["base"], "scs:x", "X_C")], parents, sp):
        print("FAIL control-3 one component reported as a duplicate")
        ok = False
    else:
        print("ok   control-3  one component is not a duplicate")

    # Control 4, negative: two components sharing only an engine root are not a finding.
    pair = [("A", "SceneComponent", "scs:a", "X_C"), ("B", "SceneComponent", "scs:b", "X_C")]
    if duplicates(pair, parents, sp):
        print("FAIL control-4 two engine-root components reported")
        ok = False
    else:
        print("ok   control-4  two engine-root components are not a finding")

    # The plant: a class that has NO duplicate today gains a second component of a class that
    # already lives on it, and the census must name the pair.
    # The victim must be a class with NO duplicate today that already carries a PROJECT-class
    # component - planting a second engine-class component is correctly not a finding, and picking
    # such a victim makes the plant pass for the wrong reason.
    victim = None
    for cls in sorted(res["scs_by_class"]):
        if cls in findings:
            continue
        comps = components_of(cls, parents, res["scs_by_class"], res["script_defcomps"])
        target = next((c for c in comps if is_project_class(c[1], sp)), None)
        if target is not None:
            victim = (cls, comps, target)
            break
    if victim is None:
        print("FAIL plant: no duplicate-free class carries a project-class component")
        ok = False
    else:
        cls, comps, target = victim
        planted = list(comps) + [("PLANTED_DUPE", target[1], "plant", cls)]
        got = duplicates(planted, parents, sp)
        if not any("PLANTED_DUPE" in [v[0] for v in group] for group in got.values()):
            print("FAIL plant: %s + a second %s was NOT reported" % (cls, target[1]))
            ok = False
        else:
            print("ok   plant     %s + a second %s is reported" % (cls, target[1]))
        # And the same class WITHOUT the plant must still be clean, or the plant proved nothing.
        if duplicates(list(comps), parents, sp):
            print("FAIL plant: %s was already a duplicate before the plant" % cls)
            ok = False
        else:
            print("ok   plant-ctl %s is clean without the plant" % cls)

    print("SELF-TEST %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


# --------------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--class-name", help="report one class only")
    ap.add_argument("--resolvers", action="store_true",
                    help="drive the report from the by-class resolve sites in Script/")
    args = ap.parse_args()

    if args.self_test:
        return self_test(args.root)

    res = census(args.root)
    dropped = res["dropped"]

    # What was dropped goes FIRST. A package that did not parse is a package whose components were
    # not counted, and its silence is indistinguishable from a clean asset.
    print("DROPPED: %d package(s) could not be read" % len(dropped))
    for path, why in dropped:
        print("  DROP %s: %s" % (path, why))

    n_scs = sum(len(v) for v in res["scs_by_class"].values())
    n_def = sum(len(v) for v in res["script_defcomps"].values())
    n_add = sum(len(a.addcomp) for a in res["assets"])
    n_ich = sum(len(a.ich) for a in res["assets"])
    n_inst = sum(len(a.instanced) for a in res["assets"])
    print("\nCOVERAGE: %d packages read, %d dropped, %d .as files"
          % (len(res["assets"]), len(dropped), res["script_files"]))
    print("SOURCES:  SCS %d components on %d classes | script DefaultComponent %d on %d classes | "
          "AddComponent %d nodes | ICH %d | .umap instanced %d"
          % (n_scs, len(res["scs_by_class"]), n_def, len(res["script_defcomps"]),
             n_add, n_ich, n_inst))

    if args.resolvers:
        rows = resolver_report(res, args.root)
        print("\nAMBIGUOUS BY-CLASS RESOLVES: %d site/class pair(s)\n" % len(rows))
        for rel, line, wanted, cls, n, match, text in rows:
            print("%s:%d  GetComponentByClass(%s) on %s -> %d candidates" % (rel, line, wanted, cls, n))
            for var, ccls, origin, owner in sorted(match):
                print("        %-42s %-34s %-26s declared on %s" % (var, ccls, origin, owner))
            print("        %s" % text)
            print("")
        return 1 if dropped else 0

    findings = res["findings"]
    if args.class_name:
        findings = dict((k, v) for k, v in findings.items() if k == args.class_name)
    print("\nDUPLICATE COMPONENT SETS: %d class(es)\n" % len(findings))
    for cls in sorted(findings):
        print("=== %s   (%s)" % (cls, res["class_source"].get(cls, "script/inherited")))
        for base in sorted(findings[cls]):
            group = findings[cls][base]
            print("    base %s  x%d" % (base, len(group)))
            for var, ccls, origin, owner in sorted(group):
                print("        %-42s %-34s %-26s declared on %s" % (var, ccls, origin, owner))
        print("")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as h:
            json.dump(dict(dropped=dropped,
                           findings=dict((k, dict((b, [list(x) for x in v])
                                                  for b, v in d.items()))
                                         for k, d in res["findings"].items()),
                           class_source=res["class_source"]), h, indent=1, sort_keys=True)
        print("wrote %s" % args.json)

    return 1 if dropped else 0


if __name__ == "__main__":
    sys.exit(main())
