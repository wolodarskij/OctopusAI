"""Where every Blueprint stands against the migration's actual target.

THE TARGET
----------
Blueprints are not eliminated. Every Blueprint gets an AngelScript ancestor, its CODE moves up to
that parent, and its DATA and VISUALS stay in the engine - the widget tree, the components, the class
defaults, the brushes, fonts and animations.

So a Blueprint is finished when it has an AngelScript ancestor and its own graphs are empty.

TWO FACTS, BOTH READ FROM THE PACKAGE, NEITHER A NAME HEURISTIC
---------------------------------------------------------------
parent  the BlueprintGeneratedClass export's SuperStruct, followed through Blueprint parents.
        A Blueprint whose parent is another Blueprint that reaches script COUNTS AS PARENTED:
        BP_ExampleScreen -> BP_ExampleScreenBase_C -> MyScreenBase is parented.
        An AngelScript parent is the import with class_name 'ASClass', package
        '/Script/AngelscriptCode'.

code    the graph nodes the package holds, comments excluded, split into the ones execution can
        reach and the ones it cannot. Data and visuals are not graph nodes, so they never appear
        here and never count as work.

THE FOUR STATES
---------------
  needs-parent  no AngelScript ancestor yet
  move-code     parented, and its graphs still run code that belongs in the parent
  sweep-dead    parented, and every node left is severed - deletion, not porting
  done          parented, no graph logic left

    python migration_state.py                 # summary and the whole list
    python migration_state.py --state move-code
    python migration_state.py --json out.json

`--root` and a relative path in the rows are relative to the project root.
"""
import argparse
import glob
import io
import json
import os
import sys

import struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _project  # noqa: E402
from _uasset import Package  # noqa: E402
import exec_islands as ei  # noqa: E402
from parent_census import build_index, chain_of  # noqa: E402
import raised_entrypoints as re_  # noqa: E402

ROOT = str(_project.project_root())

SEP = chr(92)


def blueprint_kind(pkg):
    for e in pkg.exports:
        cn = pkg.class_of(e) or ""
        if cn.endswith("BlueprintGeneratedClass"):
            return cn.replace("GeneratedClass", "")
    return None


def surface(pkg, path):
    """What the Blueprint still declares: function graphs, macros, events and variables.

    These are what a port actually moves. A node count is a size; this is the shape."""
    try:
        gl = re_.graph_lists(pkg)
        fns, macros = len(gl["function"]), len(gl["macro"])
    except Exception:
        fns = macros = 0
    nvars = 0
    try:
        pkg.load_payload()
        bp = None
        for e in pkg.exports:
            cls = pkg.class_of(e) or ""
            if cls == "Blueprint" or (cls.endswith("Blueprint")
                                      and not cls.endswith("GeneratedClass")):
                bp = e
                break
        if bp is not None:
            for tag in pkg.script_tags(bp):
                if tag.name == "NewVariables":
                    nvars = struct.unpack_from("<i", pkg.data, tag.value_off)[0]
                    break
    except Exception:
        pass
    return fns, macros, nvars


EVENT_CLASSES = ("K2Node_Event", "K2Node_CustomEvent",
                 "K2Node_ComponentBoundEvent", "K2Node_ActorBoundEvent")


def scan_paths(paths, root="Content"):
    """Scan exactly these .uasset paths. Same row shape as scan()."""
    by_class = build_index(root)
    return [r for r in (_row(f, by_class) for f in paths) if r is not None]


def scan(root="Content"):
    by_class = build_index(root)
    rows = []
    for f in sorted(glob.glob(os.path.join(root, "**", "*.uasset"), recursive=True)):
        r = _row(f, by_class)
        if r is not None:
            rows.append(r)
    return rows


def _row(f, by_class):
    """One row for one .uasset, or None when the file is not a Blueprint."""
    try:
        pkg = Package(f)
    except Exception:
        return None
    kind = blueprint_kind(pkg)
    if kind is None:
        return None
    try:
        chain, landing, root_class, rootpkg = chain_of(f, by_class)
    except Exception:
        chain, landing, root_class, rootpkg = [], "broken", None, None
    live = dead = events = 0
    fns, macros, nvars = surface(pkg, f)
    try:
        _, nodes, _ = ei.load_graphs(f)
        events = sum(1 for x in nodes.values() if x.cls in EVENT_CLASSES)
        real = [n for n in nodes.values() if n.cls != "EdGraphNode_Comment"]
        if real:
            reach = set(ei.reachable_set(nodes))
            live = len([n for n in real if n.index in reach])
            dead = len(real) - live
    except Exception:
        pass
    if landing != "script":
        state = "needs-parent"
    elif live > 0:
        state = "move-code"
    elif dead > 0:
        state = "sweep-dead"
    else:
        state = "done"
    return {"path": f.replace(SEP, "/"), "name": os.path.basename(f)[:-7],
            "kind": kind, "landing": landing, "root": root_class, "chain": chain,
            "live": live, "dead": dead, "state": state,
            "functions": fns, "macros": macros, "events": events, "variables": nvars}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="Content")
    ap.add_argument("--state")
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.json:
        a.json = os.path.abspath(a.json)
    os.chdir(ROOT)
    rows = scan(a.root)
    if a.json:
        json.dump(rows, io.open(a.json, "w", encoding="utf-8"))
    tally = {}
    for r in rows:
        tally[r["state"]] = tally.get(r["state"], 0) + 1
    print("%d Blueprint assets" % len(rows))
    for k in ("needs-parent", "move-code", "sweep-dead", "done"):
        if k in tally:
            print("   %-13s %3d files" % (k, tally[k]))
    print()
    for r in rows:
        if a.state and r["state"] != a.state:
            continue
        print("%-13s %-13s %-40s %s" % (
            r["state"], r["kind"], r["name"],
            " -> ".join(r["chain"]) if r["chain"] else "?"))


if __name__ == "__main__":
    main()
