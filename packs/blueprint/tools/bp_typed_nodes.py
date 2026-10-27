"""Find, and optionally delete, every Blueprint node that references a named type.

WHY THIS EXISTS
---------------
Retiring a class leaves nodes behind that still name it, and removing them needs the node's TRUE
graph. Two traps make that harder than it looks, and together they produce deletions that report
success and remove nothing:

1. **`delete_node` returns success for a refPath that resolves to nothing.** Address a node at the
   wrong graph and the call reports success, the node stays, and the next export shows it again. Five
   export-resolve-delete rounds can report thirty successes and change nothing.
2. **A T3D's values pass omits `Class=`** on the graph's own `Begin Object`. So attributing a node by
   the last `Begin Object Class=/Script/Engine.EdGraph` seen reports the enclosing graph as unknown
   for every node inside it, and the obvious fallback - "it must be the EventGraph" - is wrong
   whenever the node is in a function graph.

This attributes by Begin/End **nesting**, which is right under both.

    python tools/bp_typed_nodes.py /Game/Path/Asset MyRetiredType
    python tools/bp_typed_nodes.py /Game/Path/Asset MyRetiredType --delete
    python tools/bp_typed_nodes.py /Game/Path/Asset --errors          # only nodes carrying an ErrorMsg

`--delete` verifies each node resolves with `get_node_infos` before deleting it, re-exports, and
reports what survived. It saves the asset only when something was deleted.
"""
import argparse
import glob
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, HERE)
import _project  # noqa: E402

ROOT = str(_project.project_root())
import _t3d_dir  # noqa: E402

BEGIN = re.compile(r'Begin Object(?: Class=(\S+))? Name="([^"]+)"')


def read_t3d(path):
    raw = open(path, "rb").read()
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le", "replace").splitlines()
    return raw.decode("utf-8", "replace").splitlines()


def export(game_path):
    """Fresh export. Call it only while mutating the asset: a new export is the costliest read."""
    name = game_path.rsplit("/", 1)[-1]
    subprocess.run([sys.executable, os.path.join(HERE, "export_bp_t3d.py"), "--project", ROOT,
                    game_path], capture_output=True, cwd=ROOT, timeout=900)
    files = glob.glob(os.path.join(_t3d_dir.T3D_DIR, "Text%s-*.txt" % name))
    if not files:
        raise SystemExit("no export produced for %s" % name)
    return max(files, key=os.path.getmtime)


def scan(lines, markers, errors_only=False):
    """-> {(graph, node): [matching lines]} with the graph taken from Begin/End nesting."""
    stack = []
    out = {}
    for raw in lines:
        s = raw.strip()
        m = BEGIN.match(s)
        if m:
            stack.append((m.group(1) or "", m.group(2)))
            continue
        if s.startswith("End Object"):
            if stack:
                stack.pop()
            continue
        if not stack:
            continue
        if errors_only and "ErrorMsg=" not in s:
            continue
        if markers and not any(k in s for k in markers):
            continue
        if not markers and not errors_only:
            continue
        # The node is the innermost frame; its graph is the nearest ancestor that is not itself a
        # node. The outermost frame is NOT reliably the graph - some exports wrap everything in the
        # asset, others start at the graph.
        node = stack[-1][1]
        if not node.startswith("K2Node"):
            continue
        graph = next((n for _, n in reversed(stack[:-1]) if not n.startswith("K2Node")), None)
        if graph is None:
            continue
        out.setdefault((graph, node), []).append(s[:160])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("asset", help="/Game/... path")
    ap.add_argument("markers", nargs="*", help="type names to match, e.g. MyRetiredType")
    ap.add_argument("--errors", action="store_true", help="only nodes carrying an ErrorMsg")
    ap.add_argument("--delete", action="store_true", help="delete what is found, then re-export")
    a = ap.parse_args()

    name = a.asset.rsplit("/", 1)[-1]
    found = scan(read_t3d(export(a.asset)), a.markers, a.errors)
    print("%s: %d node(s)" % (name, len(found)))
    for (g, n) in sorted(found):
        print("   graph=%-24s node=%s" % (g, n))
    if not a.delete or not found:
        return 0

    import mcp
    mcp.connect()
    bp = {"refPath": "%s.%s" % (a.asset, name)}
    deleted = 0
    for (g, n) in sorted(found):
        ref = {"refPath": "%s.%s:%s.%s" % (a.asset, name, g, n)}
        try:
            info = mcp.tool(mcp.BP, "get_node_infos", {"nodes": [ref]})
            if not info:
                print("   %s.%s did not resolve - NOT deleted" % (g, n))
                continue
            mcp.tool(mcp.BP, "delete_node", {"node": ref})
            deleted += 1
        except Exception as ex:
            print("   %s.%s -> %s" % (g, n, str(ex)[:110]))
    print("deleted %d of %d" % (deleted, len(found)))
    if deleted:
        try:
            mcp.tool(mcp.BP, "compile_blueprint", {"blueprint": bp})
            print("compiles CLEAN")
        except Exception as ex:
            print("compile errors:", str(ex)[:220])
        print("save ->", mcp.tool(mcp.ASSET, "save_assets", {"asset_paths": [a.asset]}))
        left = scan(read_t3d(export(a.asset)), a.markers, a.errors)
        print("survived: %d" % len(left))
        for (g, n) in sorted(left):
            print("   graph=%-24s node=%s" % (g, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
