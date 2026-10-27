"""What each Blueprint's parent REALLY is, read from the export table.

WHY IT READS THE EXPORT TABLE
-----------------------------
A name-table heuristic - "does the package mention `Default__SomethingBase`" - answers a different
question and gets this one wrong in both directions. It can call a Blueprint unparented when its
parent is an AngelScript class, and it cannot see a Blueprint that reaches script through another
Blueprint.

The authoritative record is the `BlueprintGeneratedClass` export's `SuperStruct` index:

    import class_name == 'ASClass'                  -> an AngelScript parent, directly
    import class_name endswith BlueprintGeneratedClass -> a Blueprint parent; follow it
    anything else                                   -> an engine or C++ parent

`class_package` is `/Script/AngelscriptCode` for the first case, which is the second signal.

A native root counts as `cpp` when its package ends with one of the project's C++ modules. The
modules come from `parent_census.cppModules` in `<project>/.octopus/blueprint.json`, or from the
`Modules` list of the `.uproject` file when that key is absent.

    python parent_census.py                # every Blueprint, with its chain
    python parent_census.py --unparented   # only those that never reach script
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402
from _uasset import Package, PackageError  # noqa: E402,F401

ROOT = str(_project.project_root())
CFG = _project.config("blueprint").get("parent_census", {})

AS_CLASS = "ASClass"
AS_PACKAGE = "/Script/AngelscriptCode"
SEP = chr(92)


def _cpp_modules():
    """The project's C++ module names: the config list, else the `.uproject` Modules."""
    if "cppModules" in CFG:
        return list(CFG["cppModules"])
    up = _project.uproject()
    try:
        with open(str(up), "r", encoding="utf-8-sig") as fh:
            return [m.get("Name", "") for m in json.load(fh).get("Modules", []) if m.get("Name")]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


CPP_MODULES = _cpp_modules()


def direct_parent(path):
    """-> (own class name, parent name, kind, parent package) where kind is
    'script', 'blueprint' or 'native'."""
    pkg = Package(path)
    for e in pkg.exports:
        cls = pkg.class_of(e) or ""
        if not cls.endswith("BlueprintGeneratedClass"):
            continue
        if e.super_index == 0:
            return e.object_name, None, "native", None
        im = pkg.import_of(e.super_index)
        if im is None:
            name, _, _ = pkg.resolve(e.super_index)
            return e.object_name, name, "blueprint", None
        if im.class_name == AS_CLASS or im.class_package == AS_PACKAGE:
            return e.object_name, im.object_name, "script", im.class_package
        if (im.class_name or "").endswith("BlueprintGeneratedClass"):
            return e.object_name, im.object_name, "blueprint", im.class_package
        return e.object_name, im.object_name, "native", im.class_package
    return None, None, None, None


def build_index(root):
    by_class = {}
    for f in glob.glob(os.path.join(root, "**", "*.uasset"), recursive=True):
        by_class[os.path.basename(f)[:-7] + "_C"] = f
    return by_class


def chain_of(path, by_class, seen=None):
    """The full parent chain and where it lands.

    -> (chain, kind, root class, root package). `kind` is script, cpp, engine or broken:
    an AngelScript root is script; a root in one of the project's C++ modules is cpp; anything
    else engine."""
    seen = seen or set()
    chain = []
    cur = path
    while True:
        if cur in seen:
            return chain, "broken", None, None
        seen.add(cur)
        try:
            _, parent, kind, pkgname = direct_parent(cur)
        except Exception:
            return chain, "broken", None, None
        if parent is None:
            return chain, "broken", None, None
        chain.append(parent)
        if kind == "script":
            return chain, "script", parent, pkgname
        if kind == "native":
            is_cpp = any((pkgname or "").endswith(m) for m in CPP_MODULES)
            landing = "cpp" if is_cpp else "engine"
            return chain, landing, parent, pkgname
        nxt = by_class.get(parent)
        if nxt is None:
            return chain, "broken", parent, pkgname
        cur = nxt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="Content")
    ap.add_argument("--unparented", action="store_true", help="only files that never reach script")
    a = ap.parse_args()
    os.chdir(ROOT)

    by_class = build_index(a.root)
    rows = []
    for f in sorted(glob.glob(os.path.join(a.root, "**", "*.uasset"), recursive=True)):
        try:
            own, parent, kind, _pkg = direct_parent(f)
        except Exception:
            continue
        if own is None:
            continue
        chain, landing, root, rootpkg = chain_of(f, by_class)
        rows.append((f.replace(SEP, "/"), own, chain, landing, root))

    tally = {}
    for _, _, _, landing, _r in rows:
        tally[landing] = tally.get(landing, 0) + 1
    print("%d Blueprint files" % len(rows))
    for k in sorted(tally):
        print("  %-8s %d" % (k, tally[k]))
    print()
    for path, own, chain, landing, root in rows:
        if a.unparented and landing == "script":
            continue
        print("%-8s %-42s %s" % (landing, own, " -> ".join(chain) if chain else "?"))


if __name__ == "__main__":
    main()
