"""What each Blueprint's porting state REALLY is.

Three facts per file, each from an authoritative source, and none of them a name heuristic:

  parent   the BlueprintGeneratedClass export's SuperStruct, followed through Blueprint parents
           until it lands on an AngelScript class or an engine one  (parent_census.py)
  graphs   the graph nodes the package actually holds, comments excluded  (exec_islands.py)
  live     of those, the ones execution can reach; the rest are severed and get deleted, not ported

The state a file is in follows from the three:

  needs-parent   no AngelScript ancestor, and it still holds live logic
  clearing       an AngelScript ancestor already, with graphs left to move or delete
  dead-only      every remaining node is severed; nothing to port, only to delete
  done           no graph logic left

    python port_state.py            # summary plus every file
    python port_state.py --json X   # write the rows for another tool to read

`--root` and a relative path in the rows are relative to the project root.
"""
import argparse
import glob
import json
import io
import os
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _project  # noqa: E402
import exec_islands as ei  # noqa: E402
from parent_census import build_index, chain_of  # noqa: E402

ROOT = str(_project.project_root())

SEP = chr(92)


def scan(root="Content"):
    by_class = build_index(root)
    rows = []
    for f in sorted(glob.glob(os.path.join(root, "**", "*.uasset"), recursive=True)):
        try:
            pkg, nodes, gn = ei.load_graphs(f)
        except Exception:
            continue
        real = [n for n in nodes.values() if n.cls != "EdGraphNode_Comment"]
        try:
            chain, landing = chain_of(f, by_class)
        except Exception:
            chain, landing = [], "broken"
        if not real and landing not in ("script",):
            # No logic and no script parent: a data-only or engine-only asset, not migration work.
            if not real:
                continue
        reach = set()
        if real:
            try:
                reach = set(ei.reachable_set(nodes))
            except Exception:
                reach = set()
        live = len([n for n in real if n.index in reach])
        dead = len(real) - live
        if not real:
            state = "done"
        elif landing != "script":
            state = "needs-parent"
        elif live == 0:
            state = "dead-only"
        else:
            state = "clearing"
        rows.append({
            "path": f.replace(SEP, "/"),
            "name": os.path.basename(f)[:-7],
            "landing": landing,
            "chain": chain,
            "nodes": len(real),
            "live": live,
            "dead": dead,
            "graphs": len(set(n.graph for n in nodes.values())),
            "state": state,
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="Content")
    ap.add_argument("--json")
    ap.add_argument("--state", help="print only this state")
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
    print("%d files carry graph logic" % len(rows))
    for k in ("needs-parent", "clearing", "dead-only"):
        if k in tally:
            print("   %-13s %3d files" % (k, tally[k]))
    print()
    for r in rows:
        if a.state and r["state"] != a.state:
            continue
        print("%-13s %-40s live %4d  dead %4d  <- %s" % (
            r["state"], r["name"], r["live"], r["dead"],
            " -> ".join(r["chain"]) if r["chain"] else "?"))


if __name__ == "__main__":
    main()
