"""Remove a Blueprint CUSTOM EVENT by name — the one removal the MCP surface has no tool for.

WHY THIS EXISTS
---------------
Name collisions between a Blueprint and its native (C++ or AngelScript) parent come in three shapes,
and each needs a different removal:

    "Tried to create a property X in scope SKEL_Y_C"   -> a VARIABLE      -> remove_variable
    "The function name in node X is already used"      -> a FUNCTION graph -> remove_function_graph
    "X name conflicts with a native 'Y' function"      -> often an EVENT   -> **this script**

**MANY collisions are events, and neither existing tool touches them:**

  - `remove_function_graph` answers `Graph X does not exist`. A custom event is a NODE inside the
    ubergraph; only a real function graph has its own UEdGraph.
  - There is NO direct tool. `remove_event`, `delete_event`, `remove_custom_event`,
    `remove_event_graph` and `remove_node` all answer "Unknown tool".
  - `find_nodes(graph, title=...)` matches the node's DISPLAY title, which inserts spaces between
    camel-case words (`NextTurn` is titled "Next Turn"), and even the spaced form misses most of
    them.

So the only working route is: resolve the node's NAME from the T3D, build the refPath by hand, verify
the node really is that event, then delete it.

    refPath = <asset>.<asset>:<GraphName>.<NodeName>

TWO PARSING TRAPS THE T3D SETS
------------------------------
1. **Node names repeat across graphs** — `K2Node_CustomEvent_10` exists in two different graphs of one
   asset. The map MUST be built per graph, never by a whole-file name scan.
2. **The values pass omits `Class=`.** T3D writes an empty `Begin Object Class=X Name="Y"` declaration
   first, then a second `Begin Object Name="Y"` block carrying the values. A regex requiring `Class=`
   matches ZERO graphs while a flat scan finds dozens.

Usage:
    python tools/remove_bp_event.py /Game/Path/Asset EventName [EventName ...]
    python tools/remove_bp_event.py /Game/Path/Asset --all-conflicts   # read names from the compile log
    python tools/remove_bp_event.py /Game/Path/Asset EventName --dry-run

The compile log is `editorLog` of `.octopus/unreal.json` (default `Saved/Logs/<uproject name>.log`).
The T3D exports are read from `t3dDir` (default `T3D`).

Under Git Bash prefix with MSYS_NO_PATHCONV=1, or the leading /Game/ path is rewritten to
C:/Program Files/Git/Game/... and the call fails with a misleading "not valid Blueprint".
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import _project  # noqa: E402

ROOT = str(_project.project_root())
import mcp  # noqa: E402
import _t3d_dir  # noqa: E402


def _default_log():
    up = _project.uproject()
    return os.path.join("Saved", "Logs", (up.stem if up else "UnrealEditor") + ".log")


LOG = str(_project.project_path("editorLog", _default_log()))
DIFF = _t3d_dir.T3D_DIR


def read_t3d(path):
    """T3D exports are sometimes UTF-16. Reading one as utf-8 with errors='ignore' yields
    NUL-separated garbage that matches nothing and fails SILENTLY."""
    raw = open(path, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8", "replace")


def latest_t3d(stem):
    files = glob.glob(os.path.join(DIFF, "Text%s-*.txt" % stem))
    return max(files, key=os.path.getmtime) if files else None


def event_map(txt):
    """event function name -> (graph name, node name), scoped per graph."""
    out = {}
    # NB: no `Class=` in this pattern - the values pass does not carry it.
    for gm in re.finditer(r'^   Begin Object Name="([^"]+)"(.*?)^   End Object',
                          txt, re.S | re.M):
        gname, gbody = gm.group(1), gm.group(2)
        for nm in re.finditer(
                r'^      Begin Object Name="(K2Node_(?:Custom)?Event_\d+)"(.*?)^      End Object',
                gbody, re.S | re.M):
            node, body = nm.group(1), nm.group(2)
            m = (re.search(r'CustomFunctionName="([^"]+)"', body)
                 or re.search(r'MemberName="([^"]+)"', body))
            if m:
                out.setdefault(m.group(1), (gname, node))
    return out


def conflicts_from_log(ref, before):
    """Compile, then read the FULL error list out of the log.

    compile_blueprint's exception text is TRUNCATED at ~320 chars, so it can report 2 conflicts where
    the log holds 31. Never size this work from the exception string."""
    try:
        mcp.tool(mcp.BP, "compile_blueprint",
                 {"blueprint": {"refPath": ref}, "warnings_as_errors": False})
        return []
    except Exception:
        pass
    with open(LOG, "rb") as f:
        f.seek(before)
        txt = f.read().decode("utf-8", "replace")
    return sorted({m.strip() for m in
                   re.findall(r"([A-Za-z_][\w ]*?)\s+name conflicts with a native", txt)})


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        print(__doc__)
        return 2

    asset = args[0].rstrip("/")
    stem = asset.rsplit("/", 1)[-1]
    ref = "%s.%s" % (asset, stem)
    mcp.connect()

    names = args[1:]
    if "--all-conflicts" in sys.argv or not names:
        names = conflicts_from_log(ref, os.path.getsize(LOG))
        print("from compile log: %d conflict name(s)" % len(names))
    if not names:
        print("nothing to do - no native-conflict errors")
        return 0

    t3d = latest_t3d(stem)
    if not t3d:
        print("!! no T3D export for %s under %s - cannot resolve node names" % (stem, DIFF))
        return 1
    emap = event_map(read_t3d(t3d))
    print("t3d %s -> %d event node(s) mapped" % (os.path.basename(t3d), len(emap)))

    done, missing, failed = [], [], []
    for n in names:
        gn = emap.get(n)
        if not gn:
            missing.append(n)
            continue
        rp = "%s:%s.%s" % (ref, gn[0], gn[1])

        # Identity check before deleting: get_node_infos reports `AddEvent|Custom|<name>` as the
        # node's type_id. Node names are REUSED by the engine after a deletion, so a refPath built
        # from an older T3D can resolve to a different node - this is what catches that.
        try:
            info = mcp.tool(mcp.BP, "get_node_infos", {"nodes": [{"refPath": rp}]})
            blob = str(info)
            # An ENGINE-EVENT OVERRIDE reports a type_id WITHOUT the `Receive` prefix:
            # `ReceiveBeginPlay` in the T3D comes back as `AddEvent|EventBeginPlay`. A bare substring
            # test then fails on a perfectly correct node and reports "resolves elsewhere", which reads
            # as a wrong-node hit rather than a naming quirk. Accept the stripped form too.
            alt = n[7:] if n.startswith("Receive") else n
            if n not in blob and alt not in blob and ("Event" + alt) not in blob:
                failed.append((n, "identity check failed - refPath resolves elsewhere"))
                continue
        except Exception as ex:
            # Some nodes raise UserRaisedError on read; fall through and trust the T3D mapping.
            if "UserRaisedError" not in str(ex):
                failed.append((n, "get_node_infos: %s" % str(ex)[:70]))
                continue

        if dry:
            done.append((n, rp))
            continue
        try:
            mcp.tool(mcp.BP, "delete_node", {"node": {"refPath": rp}})
            done.append((n, rp))
        except Exception as ex:
            failed.append((n, str(ex)[:80]))

    for n, rp in done:
        print("  %-34s %s %s" % (n, "WOULD DELETE" if dry else "deleted", rp.split(":")[-1]))
    for n in missing:
        print("  %-34s NOT IN T3D (already gone, or it is a function graph)" % n)
    for n, e in failed:
        print("  %-34s FAILED %s" % (n, e))

    if done and not dry:
        left = conflicts_from_log(ref, os.path.getsize(LOG))
        print("\nconflicts left after removal: %d %s" % (len(left), left[:8]))
        print("saved: %s" % mcp.tool(mcp.ASSET, "save_assets", {"asset_paths": [asset]}))
    print("\n%d deleted | %d not found | %d failed" % (len(done), len(missing), len(failed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
