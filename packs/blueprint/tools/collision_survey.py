"""Survey Blueprint/AngelScript NAME COLLISIONS through the live editor.

WHY A SECOND SURVEY TOOL
------------------------
`collision_census.py` reads the packages on disk. This tool asks the compiler: it compiles each
Blueprint over unreal-mcp and reads the collisions out of the editor log. Custom EVENTS are the
collisions that most often stop a compile.

Four mistakes make a hand-rolled survey wrong, each time in a direction that looks plausible:

1. **Artifact property errors.** A `Tried to create a property X in scope SKEL_Y_C` line can come
   from a SNAPSHOT copy of an asset outside `Content/` (for example under `Saved/`). The editor
   compiles a snapshot under the SAME `SKEL_<Asset>_C` scope as the live asset. A scope-name match
   is NOT attribution - **filter by the asset path in the message.**
2. **Inherited entries.** `list_functions` reports the script parent's declarations on the child with
   `bIsImplemented: true`, and `list_events` does the same. Counting those inflates a survey.
   `list_graphs` is the honest list of child-owned graphs.
3. **Macro graphs.** A `K2Node_Tunnel` macro emits no UFunction and cannot collide.
4. **Space-in-name symbols.** `Post Update Score` and `PostUpdateScore` are DIFFERENT FNames and
   never collide. A space-stripped match deletes working functions.

The errors cannot be read from the exception: `compile_blueprint`'s text is TRUNCATED at about 320
characters. So this tool reads the log from a byte offset taken before the compile.

WHAT COUNTS AS A REAL COLLISION - only these two shapes, and only from a `Content\\` path:
    "X name conflicts with a native 'Y' function"   -> usually an EVENT, sometimes a function graph
    "The function name in node X is already used"   -> a function graph

The editor log is `collision_survey.logFile` of `<project>/.octopus/blueprint.json`, relative to the
project root. The default is `Saved/Logs/<uproject name>.log`.

Usage:
    python collision_survey.py                # every Blueprint that references a script class
    python collision_survey.py /Game/A /Game/B # just these

Under Git Bash prefix with MSYS_NO_PATHCONV=1, or a leading /Game/ path is rewritten to
C:/Program Files/Git/Game/... and the call fails with a misleading "not valid Blueprint".
"""
import os
import re
import sys
from pathlib import Path

UNREAL_TOOLS = Path(__file__).resolve().parents[2] / "unreal" / "tools"
sys.path.insert(0, str(UNREAL_TOOLS))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import _project  # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("blueprint").get("collision_survey", {})


def _log_path():
    if CFG.get("logFile"):
        return os.path.normpath(os.path.join(ROOT, CFG["logFile"]))
    up = _project.uproject()
    stem = Path(str(up)).stem if up else "Editor"
    return os.path.join(ROOT, "Saved", "Logs", stem + ".log")


LOG = _log_path()
MARK = b"/Script/Angelscript."


def candidates():
    """Only a Blueprint that references a script class can collide with one.

    Found by binary scan rather than a hand-written list: a list silently skips an asset whose name
    is slightly off, and reports it as "not found", which reads like "clean"."""
    out = []
    for root, _, files in os.walk(os.path.join(ROOT, "Content")):
        for fn in files:
            if not fn.endswith(".uasset"):
                continue
            p = os.path.join(root, fn)
            try:
                if MARK not in open(p, "rb").read():
                    continue
            except Exception:
                continue
            rel = os.path.relpath(p, os.path.join(ROOT, "Content")).replace("\\", "/")[:-7]
            out.append("/Game/" + rel)
    return sorted(out)


def survey(asset):
    import mcp  # noqa: E402 - only a survey talks to the editor
    stem = asset.rsplit("/", 1)[-1]
    ref = "%s.%s" % (asset, stem)
    off = os.path.getsize(LOG)
    try:
        mcp.tool(mcp.BP, "compile_blueprint",
                 {"blueprint": {"refPath": ref}, "warnings_as_errors": False})
        return stem, [], [], "CLEAN"
    except Exception as ex:
        if "not valid Blueprint" in str(ex) or "Failed to load" in str(ex):
            return stem, [], [], "UNREADABLE"
    with open(LOG, "rb") as f:
        f.seek(off)
        txt = f.read().decode("utf-8", "replace")

    # ONLY lines naming a file under Content\ - a snapshot copy compiles under the same scope.
    live = [l for l in txt.splitlines() if "Content\\" in l or "Content/" in l]
    blob = "\n".join(live)
    native = sorted({m.strip() for m in
                     re.findall(r"([A-Za-z_][\w ]*?)\s+name conflicts with a native", blob)})
    used = sorted({m.strip() for m in
                   re.findall(r"function name in node\s+(.+?)\s+is already used", blob)})
    return stem, native, used, "FAILS"


def main():
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__)
        return 0
    import mcp  # noqa: E402
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mcp.connect()
    assets = args or candidates()
    if not args:
        print("%d Blueprints reference an AngelScript class\n" % len(assets))

    tot_n = tot_u = 0
    dirty = []
    for a in assets:
        stem, native, used, state = survey(a)
        if state == "CLEAN":
            print("%-40s CLEAN" % stem)
            continue
        if state == "UNREADABLE":
            # DataTables reference a script STRUCT as their row type, never a parent class, so they
            # cannot collide - compile_blueprint rightly turns them away. Not a path problem.
            kind = "DataTable (row struct only - cannot collide)" if stem.startswith("DT_") \
                else "not a Blueprint, or the path is wrong"
            print("%-40s skipped: %s" % (stem, kind))
            continue
        tot_n += len(native)
        tot_u += len(used)
        if native or used:
            dirty.append(a)
            print("%-40s events/native=%-3d graphs/used=%-3d  %s"
                  % (stem, len(native), len(used), ", ".join((native + used)[:5])))
        else:
            print("%-40s fails, but NO collisions (other cause)" % stem)

    print("\nREAL collisions: %d native-conflict + %d already-used" % (tot_n, tot_u))
    print("Property errors are NOT counted - they can come from snapshot copies outside Content/.")
    if dirty:
        print("\nNext:")
        remover = (UNREAL_TOOLS / "remove_bp_event.py").as_posix()
        for a in dirty:
            print('  python "%s" %s --all-conflicts' % (remover, a))
        print("\nThen CONFIRM: remove_function_graph can return SUCCESS and do nothing - re-read")
        print("list_graphs afterwards. And check a same-named graph is not a MACRO before removing it.")


if __name__ == "__main__":
    sys.exit(main() or 0)
