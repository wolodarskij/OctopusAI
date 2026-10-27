# -*- coding: utf-8 -*-
"""Remove (or empty) a Blueprint MACRO graph — the removal `remove_function_graph` lies about.

WHY THIS EXISTS
---------------
The MCP surface has three graph kinds and one removal tool, and it does not tell you when the two do
not match. Companion to `tools/remove_bp_event.py`, which exists for the same reason for custom
events. The full picture:

    a FUNCTION graph   -> remove_function_graph          works
    an EVENT DISPATCHER-> remove_function_graph          works (it special-cases dispatchers)
    a CUSTOM EVENT     -> remove_function_graph          errors "Graph X does not exist"  -> remove_bp_event.py
    a MACRO graph      -> remove_function_graph          **RETURNS SUCCESS AND DOES NOTHING**  -> this script
    an UBERGRAPH page  -> remove_function_graph          same silent no-op

**The macro case is the dangerous one, because it looks like success.** The call returns cleanly,
the asset is never even dirtied, and a re-export from disk shows the macro still there with every
node. A control proves it is not a "not found" case wearing a different hat — a name that genuinely
does not exist errors loudly with `Graph <name> does not exist`.

WHY, EXACTLY — the engine, not the toolset
------------------------------------------
`BlueprintTools.remove_function_graph`
(`<EngineRoot>/Engine/Plugins/Experimental/Toolsets/EditorToolset/Content/Python/editor_toolset/
toolsets/blueprint.py`) guards on `list_graphs`, which returns **every** graph — function, macro,
ubergraph — then hands off to `unreal.BlueprintEditorLibrary.remove_function_graph`. That lands on
`UBlueprintEditorLibrary::RemoveFunctionGraph`
(`<EngineRoot>/Engine/Source/Editor/BlueprintEditorLibrary/Private/BlueprintEditorLibrary.cpp`),
whose whole body is:

    for (UEdGraph* Graph : Blueprint->FunctionGraphs) { ... }        // <- FunctionGraphs ONLY
    if (FunctionGraph && FunctionGraph->bAllowDeletion) { ...RemoveGraph... }
    else { UE_LOGF(..., Warning, "Failed to remove function '%ls' ...") }   // <- returns void

A macro lives in `UBlueprint::MacroGraphs`, a different array. The loop never finds it, the function
logs a Warning and **returns void**, and the toolset has nothing to check — so it reports success.
The guard passing and the removal failing are looking at two different lists.

CAN THIS SCRIPT ACTUALLY REMOVE ONE? — read this before trusting a green result
-------------------------------------------------------------------------------
On a UE 5.8 editor with Epic's toolsets: **no.** Every route was probed against the live server and
every one is closed. The script still ATTEMPTS them all, in order, and then **verifies from a fresh
T3D export**; it exits non-zero and prints the route table when the macro survives. It will start
working, unchanged, the day one of the routes opens.

    remove_function_graph                    "success", macro still present   (the defect above)
    remove_macro_graph / remove_macro        Unknown tool
    remove_graph / delete_graph              Unknown tool
    ObjectTools.set_properties MacroGraphs   unreachable — see below

The `set_properties` route deserves its own line because it looks like it should work and does not.
`MacroGraphs` is a plain `UPROPERTY()` on **UBlueprint** (`Engine/Classes/Engine/Blueprint.h:555`),
but `ObjectTools._get_instance_object` (`object.py:118`) rewrites a Blueprint refPath to its **CDO**
before every get/set. So the property you reach is on `Default__<Asset>_C`, which has no
`MacroGraphs` at all, and the call fails with:

    GetObjectProperties on '...Default__BP_ExampleActor_C': the following properties could not
    be read: MacroGraphs

There is no way to address the UBlueprint object itself through that toolset.

WHAT DOES WORK, AND IS WHY `--empty` EXISTS
-------------------------------------------
`delete_node` works fine on nodes **inside** a macro. So a macro can be reduced to its two-tunnel
floor (entry + exit, zero body nodes) even though the graph itself cannot be deleted. That is the
most this surface can do. `--empty`
does it and verifies the node count from a fresh export. An emptied macro is inert: it has no body,
and a Blueprint macro is only reachable from its own class or a child.

WHAT A FUTURE TOOLSET ADDITION NEEDS (one function)
---------------------------------------------------------------
`FBlueprintEditorUtils::RemoveGraph(Blueprint, Graph, EGraphRemoveFlags::MarkTransient)` already
handles macros — it is graph-kind agnostic and is what `RemoveFunctionGraph` calls once it has found
the graph. The gap is purely that nothing exposes it for a graph that is not in `FunctionGraphs`.
Either fix closes it:
  (a) engine: widen `UBlueprintEditorLibrary::RemoveFunctionGraph` to search `MacroGraphs` and
      `UbergraphPages` too — and make it return `bool` so a caller can tell success from the
      Warning-and-return-void path, which is the actual root cause of the silent lie;
  (b) toolset: add `BlueprintTools.remove_graph(blueprint, graph_name)` in
      `editor_toolset/toolsets/blueprint.py` calling a new
      `BlueprintEditorLibrary.remove_graph`/`BlueprintGraphEditor` entry point.
`UBlueprintGraphEditor` (`BlueprintEditorLibrary/Private/BlueprintEditorLibrary/
BlueprintGraphEditor.h`) exposes ~60 BlueprintCallable graph operations and **not one removes a
graph**, so there is nothing to reach today.

USAGE
-----
    python tools/remove_bp_macro.py /Game/Path/Asset MacroName [MacroName ...]
    python tools/remove_bp_macro.py /Game/Path/Asset MacroName --dry-run
    python tools/remove_bp_macro.py /Game/Path/Asset MacroName --empty     # strip body to the floor
    python tools/remove_bp_macro.py /Game/Path/Asset --list                # every macro + node count
    python tools/remove_bp_macro.py /Game/Path/Asset MacroName --force     # ignore call sites
    python tools/remove_bp_macro.py /Game/Path/Asset MacroName --scan-content   # cross-asset callers

The T3D exports are read from `t3dDir` of `.octopus/blueprint.json` (default `T3D`).
**Under Git Bash prefix with `MSYS_NO_PATHCONV=1`**, or the leading `/Game/`
is rewritten to `C:/Program Files/Git/Game/...` — the same trap `export_bp_t3d.py` documents, and it
fails while printing a success-shaped JSON.

Exit codes: 0 the requested change is verified done (or --dry-run/--list) · 1 the macro survived a
removal attempt, or a resolution/verification step failed.
"""
import argparse
import glob
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# `mcp` and `_project` are in the unreal pack; this pack's tools directory goes FIRST on sys.path.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import _project  # noqa: E402

ROOT = str(_project.project_root())
import mcp  # noqa: E402
import _t3d_dir  # noqa: E402

DIFF = _t3d_dir.T3D_DIR

# Every removal route, in the order tried. Each entry: (label, toolset, tool, args-builder).
# Keeping the closed ones in the list is deliberate: the run prints what each one answered, which is
# the evidence a future reader needs, and the script starts working the day one of them opens.
_ROUTES = [
    ("remove_function_graph", lambda ref, g: (mcp.BP, "remove_function_graph",
                                              {"blueprint": {"refPath": ref}, "graph_name": g})),
    ("remove_macro_graph", lambda ref, g: (mcp.BP, "remove_macro_graph",
                                           {"blueprint": {"refPath": ref}, "graph_name": g})),
    ("remove_graph", lambda ref, g: (mcp.BP, "remove_graph",
                                     {"blueprint": {"refPath": ref}, "graph_name": g})),
    ("delete_graph", lambda ref, g: (mcp.BP, "delete_graph",
                                     {"blueprint": {"refPath": ref}, "graph_name": g})),
]


def read_t3d(path):
    """T3D exports are sometimes UTF-16. Reading one as utf-8 with errors='ignore' yields
    NUL-separated garbage that matches nothing and fails SILENTLY."""
    raw = open(path, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8", "replace")


def latest_t3d(stem):
    """By MTIME, never by the -N suffix. The export index RESETS on editor restart, so -4 can be
    newer than -164."""
    files = glob.glob(os.path.join(DIFF, "Text%s-*.txt" % stem))
    return max(files, key=os.path.getmtime) if files else None


def export_fresh(asset, label):
    """Run the project's own exporter so the T3D reflects DISK, not memory. Returns the new path.

    Exporting before a save compares the file to itself and reports zero loss no matter what
    happened."""
    cmd = [sys.executable, os.path.join(HERE, "export_bp_t3d.py"), "--project", ROOT, asset,
           "--label", label, "--no-parse"]
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, cwd=ROOT)
    if p.returncode != 0:
        print("   !! export_bp_t3d.py failed rc=%d\n%s" % (p.returncode, (p.stderr or "")[:400]))
        return None
    return latest_t3d(asset.rstrip("/").rsplit("/", 1)[-1])


def parse(txt):
    """-> (graph_kind{name: 'macro'|'function'|'ubergraph'|'delegate'},
            nodes{graph: [node names]},
            macro_callers{macro name: [count of K2Node_MacroInstance referencing it]})

    Two T3D traps this respects:
      1. The values pass omits `Class=` - `Begin Object Class=X Name="Y"` is written first as an
         empty declaration, then a second `Begin Object Name="Y"` carries the body. A regex
         requiring `Class=` matches ZERO graphs. Match the bodies.
      2. Node names repeat across graphs (10x `K2Node_CallArrayFunction_2` in one asset), so the
         node map is built PER GRAPH and never by a whole-file scan.
    """
    kind = {}
    for arr, k in (("UbergraphPages", "ubergraph"), ("FunctionGraphs", "function"),
                   ("MacroGraphs", "macro"), ("DelegateSignatureGraphs", "delegate")):
        for m in re.finditer(r"^   %s\(\d+\)=\"[^']*'([^']+)'\"" % arr, txt, re.M):
            kind[m.group(1)] = k

    nodes, callers = {}, {}
    for gm in re.finditer(r'^   Begin Object Name="([^"]+)"(.*?)^   End Object', txt, re.S | re.M):
        gname, body = gm.group(1), gm.group(2)
        found = re.findall(r'^      Begin Object Name="([^"]+)"', body, re.M)
        # A graph can appear twice (declaration pass + values pass); keep the richer one.
        if len(found) >= len(nodes.get(gname, [])):
            nodes[gname] = found
        # Macro CALL SITES: a K2Node_MacroInstance names its target as
        #   MacroGraph="/Script/Engine.EdGraph'<Asset>:<MacroName>'"
        # Cross-asset macros (StandardMacros:ForEachLoop) carry a package path and are skipped by
        # the same regex, which only accepts a bare `Asset:Name`.
        for cm in re.finditer(r"MacroGraph=\"[^']*'([^'/]+):([^'/]+)'\"", body):
            callers.setdefault(cm.group(2), []).append(gname)
    return kind, nodes, callers


def scan_content(macro):
    """Cross-asset callers. A Blueprint macro is only reachable from its own class or a child, so
    this is normally empty - but a child Blueprint IS a real caller, and the in-asset T3D cannot
    see one. Binary substring scan, same idea as tools/search_bp_refs.py."""
    needle = macro.encode("utf-8")
    hits = []
    for dirpath, _, files in os.walk(os.path.join(ROOT, "Content")):
        for fn in files:
            if not fn.endswith((".uasset", ".umap")):
                continue
            p = os.path.join(dirpath, fn)
            try:
                if needle in open(p, "rb").read():
                    hits.append(os.path.relpath(p, ROOT))
            except OSError:
                pass
    return hits


def verify_identity(ref, graph, node_names):
    """Confirm the T3D mapping still points at a real macro in the LIVE editor before mutating.

    Two independent checks, because either alone lies:
      - the graph must be in `list_graphs` but NOT in `list_functions`. That pair is the reliable
        macro discriminator on this surface; `find_nodes` matches the node's DISPLAY title (spaces
        inserted between camel-case words) and misses most nodes outright.
      - a body node's refPath must resolve. Unreal REUSES freed node names, so a refPath built from
        an older T3D can silently resolve to a different node.
    """
    graphs = [g["refPath"].rsplit(":", 1)[-1]
              for g in (mcp.tool(mcp.BP, "list_graphs", {"blueprint": {"refPath": ref}}) or [])]
    if graph not in graphs:
        return False, "not in list_graphs - the T3D is stale, or it is already gone"
    funcs = [f["name"] for f in
             (mcp.tool(mcp.BP, "list_functions", {"blueprint": {"refPath": ref}}) or [])]
    if graph in funcs:
        return False, "list_functions names it -> it is a FUNCTION graph; use remove_function_graph"
    if node_names:
        rp = "%s:%s.%s" % (ref, graph, node_names[0])
        try:
            mcp.tool(mcp.BP, "get_node_infos", {"nodes": [{"refPath": rp}]})
        except Exception as ex:
            # Some nodes raise UserRaisedError on read; that is a read defect, not a wrong node.
            if "UserRaisedError" not in str(ex):
                return False, "first body node will not resolve: %s" % str(ex)[:90]
    return True, "macro (in list_graphs, absent from list_functions)"


def attempt_removal(ref, graph):
    """Try every route. Returns [(label, 'OK'|'ERR', detail)] - NOT a verdict. A route answering OK
    proves nothing here; only the post-export check does."""
    out = []
    for label, build in _ROUTES:
        ts, name, args = build(ref, graph)
        try:
            r = mcp.tool(ts, name, args)
            out.append((label, "OK", str(r)[:70]))
        except Exception as ex:
            out.append((label, "ERR", str(ex).split(": ", 1)[-1][:90]))
    # The reflection route, kept last because it is the one that looks plausible and is not.
    try:
        mcp.tool(mcp.OBJ, "get_properties",
                 {"instance": {"refPath": ref}, "properties": ["MacroGraphs"]})
        out.append(("ObjectTools MacroGraphs", "OK", "readable - the CDO redirect may be fixed"))
    except Exception as ex:
        out.append(("ObjectTools MacroGraphs", "ERR", str(ex).split(": ", 1)[-1][:90]))
    return out


def empty_macro(ref, graph, node_names):
    """Delete every non-tunnel node in the macro. This is the one mutation that DOES work."""
    keep = [n for n in node_names if n.startswith("K2Node_Tunnel")]
    kill = [n for n in node_names if not n.startswith("K2Node_Tunnel")]
    print("   floor: %d tunnel node(s) kept %s" % (len(keep), keep))
    gone, failed = [], []
    for n in kill:
        rp = "%s:%s.%s" % (ref, graph, n)
        try:
            mcp.tool(mcp.BP, "delete_node", {"node": {"refPath": rp}})
            gone.append(n)
        except Exception as ex:
            failed.append((n, str(ex).split(": ", 1)[-1][:80]))
    for n, e in failed:
        print("   FAILED %-40s %s" % (n, e))
    print("   deleted %d/%d body node(s)" % (len(gone), len(kill)))
    return len(gone), failed


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("asset", help="/Game/... path, with or without the .Name suffix")
    ap.add_argument("macros", nargs="*", help="macro graph name(s)")
    ap.add_argument("--list", action="store_true", help="list every macro with its node count")
    ap.add_argument("--dry-run", action="store_true", help="resolve and report, mutate nothing")
    ap.add_argument("--empty", action="store_true",
                    help="strip the body to the 2-tunnel floor instead of removing the graph")
    ap.add_argument("--force", action="store_true", help="proceed even with call sites")
    ap.add_argument("--scan-content", action="store_true",
                    help="also scan Content/ binaries for cross-asset callers (slow)")
    ap.add_argument("--no-export", action="store_true",
                    help="reuse the newest T3D on disk instead of exporting fresh (NOT recommended)")
    args = ap.parse_args()

    asset = args.asset.split(".")[0].rstrip("/")
    stem = asset.rsplit("/", 1)[-1]
    ref = "%s.%s" % (asset, stem)
    mutating = bool(args.macros) and not args.dry_run and not args.list

    mcp.connect()

    # ---- 1. baseline, from a FRESH export (disk), never from a remembered file -----------------
    if args.no_export:
        pre = latest_t3d(stem)
        print("!! --no-export: reusing %s (may predate another agent's save)"
              % (os.path.basename(pre) if pre else "nothing"))
    else:
        print("exporting baseline ...")
        pre = export_fresh(asset, "macro-pre")
    if not pre:
        print("!! no T3D for %s under %s - cannot resolve macro names" % (stem, DIFF))
        return 1
    kind, nodes, callers = parse(read_t3d(pre))
    macros = sorted(n for n, k in kind.items() if k == "macro")
    print("baseline %s -> %d graph(s), %d macro(s)"
          % (os.path.basename(pre), len(kind), len(macros)))

    if args.list or not args.macros:
        print("\n%-34s %6s %6s" % ("macro", "nodes", "calls"))
        for m in macros:
            print("%-34s %6d %6d" % (m, len(nodes.get(m, [])), len(callers.get(m, []))))
        if not args.macros:
            print("\nname one or more macros to act on. Nothing was mutated.")
        return 0

    # ---- 2. resolve + verify identity ----------------------------------------------------------
    targets = []
    for m in args.macros:
        k = kind.get(m)
        if k is None:
            print("\n%-30s NOT IN THE T3D - already gone, or misspelled" % m)
            continue
        if k != "macro":
            print("\n%-30s is a %s graph, not a macro." % (m, k.upper()))
            if k == "function":
                print("   -> use remove_function_graph; it works for that kind.")
            continue
        body = nodes.get(m, [])
        calls = callers.get(m, [])
        extern = scan_content(m) if args.scan_content else None
        print("\n%-30s %d node(s), %d in-asset call site(s)" % (m, len(body), len(calls)))
        if calls:
            print("   called from: %s" % ", ".join(sorted(set(calls))[:8]))
        if extern is not None:
            others = [h for h in extern if stem not in h]
            print("   Content/ binaries naming %r: %d%s"
                  % (m, len(others), (" -> " + ", ".join(others[:4])) if others else ""))
        ok, why = verify_identity(ref, m, body)
        print("   identity: %s%s" % ("OK - " if ok else "REFUSED - ", why))
        if not ok:
            continue
        if calls and not args.force:
            print("   REFUSED: %d call site(s) remain. Re-point them first, or pass --force."
                  % len(calls))
            continue
        targets.append((m, body))

    if not targets:
        print("\nnothing to act on.")
        return 1
    if args.dry_run:
        for m, body in targets:
            print("\nWOULD %s %s (%d nodes)" % ("EMPTY" if args.empty else "REMOVE", m, len(body)))
        return 0

    # ---- 3. mutate ------------------------------------------------------------------------------
    route_log = {}
    for m, body in targets:
        print("\n--- %s %s ---" % ("emptying" if args.empty else "removing", m))
        if args.empty:
            empty_macro(ref, m, body)
        else:
            route_log[m] = attempt_removal(ref, m)
            for label, st, detail in route_log[m]:
                print("   %-26s %-4s %s" % (label, st, detail))

    # ---- 4. SAVE, then export again. In THIS order. --------------------------------------------
    # export_bp_t3d.py reads from DISK. Exporting before the save compares the file to itself and
    # reports zero change no matter what happened.
    print("\nsaving %s ..." % asset)
    try:
        print("   save_assets -> %s" % mcp.tool(mcp.ASSET, "save_assets", {"asset_paths": [asset]}))
    except Exception as ex:
        print("   !! save failed: %s" % str(ex)[:200])

    if args.no_export:
        print("!! --no-export: cannot verify. Treating as FAILED - verification is the point.")
        return 1
    print("verifying from a fresh export ...")
    post = export_fresh(asset, "macro-post")
    if not post or post == pre:
        print("!! no post-export produced - CANNOT VERIFY, treating as failure")
        return 1
    kind2, nodes2, _ = parse(read_t3d(post))
    print("post %s -> %d graph(s)" % (os.path.basename(post), len(kind2)))

    bad = 0
    print("\n%-30s %-10s %-10s %s" % ("macro", "before", "after", "verdict"))
    for m, body in targets:
        after_present = kind2.get(m) is not None
        after_nodes = len(nodes2.get(m, []))
        if args.empty:
            ok = after_present and after_nodes <= 2
            verdict = ("EMPTIED to the tunnel floor" if ok else
                       "STILL HAS A BODY - %d node(s) survived" % after_nodes)
        else:
            ok = not after_present
            verdict = "REMOVED" if ok else "!! STILL PRESENT - the removal did nothing"
        if not ok:
            bad += 1
        print("%-30s %-10s %-10s %s"
              % (m, "%d nodes" % len(body),
                 ("absent" if not after_present else "%d nodes" % after_nodes), verdict))

    if bad:
        print("\n%d macro(s) survived. THE MCP SURFACE CANNOT REMOVE A MACRO GRAPH." % bad)
        print("Every route's answer is printed above; the file header records why each is closed")
        print("and what a toolset addition would need. `--empty` strips the body, which works.")
        return 1
    print("\nall %d target(s) verified." % len(targets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
