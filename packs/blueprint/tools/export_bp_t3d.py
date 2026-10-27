# -*- coding: utf-8 -*-
"""Export a Blueprint to T3D text, plus a backup copy outside the project.

Run this BEFORE mutating any Blueprint. It captures what the asset looks like in a form that can
still be read after the live asset has been broken - which is the point: the editor's read path
(`read_graph_dsl`, `get_node_infos`) resolves node references and raises on anything it cannot
resolve, taking the whole graph down with it. A T3D is a flat text dump, so nothing is resolved and
nothing can raise.

    python tools/export_bp_t3d.py /Game/Blueprints/BP_ExampleActor
    python tools/export_bp_t3d.py <path> --label pre-varmove --no-parse

Chain:
    <assetTextToolset>.export_asset_text(asset)  -> <t3dDir>/Text<Asset>-N.txt
    tools/t3d_graphs.py <text> --outdir Saved/<Asset>Graphs

`assetTextToolset` of `.octopus/blueprint.json` names the MCP toolset that exports the text; the
default is the AssetTextExport plugin's `asset_text_export.toolsets.text.AssetTextExportTools`.

It also copies the asset's last saved bytes to `backupDir` of `.octopus/blueprint.json` (default
../<Project>Backups/MigrationBackups/), a sibling of the project that is never mounted. Those copies
are a RECORD, not a restore plan - do not restore an asset from one without being asked.

IT WRITES NO PACKAGE INSIDE THE PROJECT, AND MUST NOT
-----------------------------------------------------
The engine mounts the whole of Saved/ as the package root /Temp/ (PackageName.cpp:808 and :904), so
a `.uasset` anywhere under Saved/ is a real, loadable package: `Saved/<Dir>/Foo.uasset` is the package
`/Temp/<Dir>/Foo`. A package saved while one of those is loaded records a dependency on it, and
many packages can end up importing a `/Temp/...` copy that way.

`export_asset_text` dumps the LIVE object out of memory with `UExporter` (the flags of the engine's
own asset dump, never `IAssetTools::DumpAssetToTempFile`, which writes under `Saved/`). Its only
output is one `Text<Asset>-N.txt`, written straight into the T3D directory: this tool passes that
directory as `out_dir`. An older toolset build that ignores `out_dir` is caught by
`move_into_t3d_dir`. A `.txt` is not a package and nothing can import it.

Never add a route that stages a package to read an older version. Such a route can crash the
editor and leave importable copies inside Saved/.
"""
import argparse, json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, HERE)
import _project  # noqa: E402

ROOT = str(_project.project_root())
import mcp  # noqa: E402
import _t3d_dir  # noqa: E402


def backup_root():
    """Where .uasset backups go: a sibling of the project, never inside it.

    OCTOPUS_BACKUP_DIR overrides, then `backupDir` of `.octopus/blueprint.json`. Unreal must never be
    able to mount this path."""
    override = _project.env("BACKUP_DIR")
    default = os.path.join("..", os.path.basename(ROOT) + "Backups", "MigrationBackups")
    path = override or os.path.normpath(str(_project.project_path("backupDir", default, "blueprint")))
    os.makedirs(path, exist_ok=True)
    return path


TOOLSET = _project.setting("blueprint", "assetTextToolset",
                           "asset_text_export.toolsets.text.AssetTextExportTools")
TOOLSET_CLASS = TOOLSET.rsplit(".", 1)[-1]
TOOLSET_SOURCE = _project.setting("blueprint", "assetTextToolsetSource",
                                  "Plugins/AssetTextExport/Content/Python")


def move_into_t3d_dir(text_path):
    """Moves an export that still lies under Saved/ into T3D/ and returns its new path. A path already
    in T3D/ comes back as it is."""
    if not text_path or not os.path.isfile(text_path):
        return text_path
    saved = os.path.join(ROOT, "Saved") + os.sep
    if not os.path.abspath(text_path).startswith(saved):
        return text_path
    os.makedirs(_t3d_dir.T3D_DIR, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(text_path))
    base = stem.rsplit("-", 1)[0] if stem.rsplit("-", 1)[-1].isdigit() else stem
    target, n = os.path.join(_t3d_dir.T3D_DIR, stem + ext), 0
    while os.path.exists(target):
        target = os.path.join(_t3d_dir.T3D_DIR, "%s-%d%s" % (base, n, ext))
        n += 1
    shutil.move(text_path, target)
    source_dir = os.path.dirname(text_path)
    if not os.listdir(source_dir):
        os.rmdir(source_dir)
    return target


def resolve_toolset():
    """The registered name of the project's text toolset. Confirmed against the live list, because a
    toolset that failed to register answers nothing and the error should say so."""
    d = mcp._post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "list_toolsets", "arguments": {}}})
    txt = d["result"]["content"][0]["text"]
    for line in txt.splitlines():
        if not line.strip().startswith("- "):
            continue
        name = line.strip("- ").split(":")[0].strip()
        if name == TOOLSET or name.endswith(TOOLSET_CLASS):
            return name
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asset", help="/Game/... path, with or without the .Name suffix")
    ap.add_argument("--label", default=None, help="backup label (default: a timestamp)")
    ap.add_argument("--no-parse", action="store_true", help="skip tools/t3d_graphs.py")
    ap.add_argument("--backup-only", action="store_true", help="copy the bytes out and stop")
    args = ap.parse_args()

    pkg = args.asset.split(".")[0]
    short = pkg.rstrip("/").split("/")[-1]
    label = args.label or time.strftime("%Y%m%d-%H%M%S")
    out = {"asset": pkg, "label": label}

    # 1. plain file copy, outside the project - no editor needed
    src = os.path.join(ROOT, "Content", *pkg.replace("/Game/", "").split("/")) + ".uasset"
    if os.path.isfile(src):
        dst = os.path.join(backup_root(), "%s_%s.uasset" % (short, label))
        with open(src, "rb") as f:
            data = f.read()
        with open(dst, "wb") as f:
            f.write(data)
        out["backup"] = dst
        out["bytes"] = len(data)
        print("backup   : %s (%d bytes)" % (dst, len(data)))
    else:
        print("!! source .uasset not found at %s" % src)

    if args.backup_only:
        print(json.dumps(out, indent=1))
        return 0

    # 2. T3D of the LIVE asset - one text file, no package
    mcp.connect()
    ts = resolve_toolset()
    if not ts:
        print("!! %s is not registered in this editor." % TOOLSET_CLASS)
        print("   It comes from %s. Check the LogPython lines at startup." % TOOLSET_SOURCE)
        print(json.dumps(out, indent=1))
        return 2

    try:
        txt = move_into_t3d_dir(mcp.tool(ts, "export_asset_text",
                                         {"asset_path": pkg, "out_dir": _t3d_dir.T3D_DIR}))
        out["t3d"] = txt
        print("t3d      : %s" % txt)
    except Exception as ex:
        print("!! export_asset_text failed: %s" % str(ex)[:300])
        print(json.dumps(out, indent=1))
        return 3

    # 3. parse to per-graph JSON
    if txt and not args.no_parse:
        parser = os.path.join(HERE, "t3d_graphs.py")
        outdir = os.path.join(ROOT, "Saved", "%sGraphs" % short)
        if os.path.isfile(parser):
            r = subprocess.run([sys.executable, parser, str(txt), "--outdir", outdir],
                               capture_output=True, text=True)
            print("parse    : rc=%d %s" % (r.returncode, (r.stdout or r.stderr)[:300]))
            out["graphs_dir"] = outdir
        else:
            print("!! tools/t3d_graphs.py not found; skipping parse")

    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
