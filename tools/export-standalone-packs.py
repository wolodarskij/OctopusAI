"""Build the standalone Unreal kit: the `unreal`, `ue-angelscript` and `blueprint` packs as plain files.

    python tools/export-standalone-packs.py --out <dir>

Writes `<dir>/unreal-kit/` and `<dir>/unreal-kit.zip`. The rules, the skills, the READMEs and the
index come from `standalone-packs/`. The tools and the AssetTextExport plugin come from `packs/`,
with the exact-line patches of PATCHES and the name replacements of RENAMES. Exit 1 when a patch
does not match exactly once, or when an exported file still names the engine.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KIT = "unreal-kit"
HAND_WRITTEN = REPO / "standalone-packs"
COPIED = [
    "unreal/tools",
    "ue-angelscript/tools",
    "blueprint/tools",
    "blueprint/Plugins/AssetTextExport",
]
TEXT_SUFFIXES = {".py", ".ps1", ".cpp", ".h", ".cs", ".uplugin", ".ini", ".md"}
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc"}

PATCHES = {
    "unreal/tools/_project.py": [
        ("3. the nearest directory above the current directory that holds `.octopus/project.json` or a\n"
         "   `*.uproject` file.",
         "3. the nearest directory above the current directory that holds a `*.uproject` file."),
        ("`ue-angelscript` pack puts `packs/unreal/tools/` on `sys.path` to import it.",
         "`ue-angelscript` pack puts `unreal/tools/` on `sys.path` to import it."),
        ("def _is_root(d):\n"
         "    if (d / CONFIG_DIR / \"project.json\").is_file():\n"
         "        return True\n",
         "def _is_root(d):\n"),
        ("            raise SystemExit(\"error: no project: pass --project <path>, set OCTOPUS_PROJECT, \"\n"
         "                             \"or run inside a directory with .octopus/project.json or a \"\n"
         "                             \"*.uproject file\")",
         "            raise SystemExit(\"error: no project: pass --project <path>, set UNREAL_KIT_PROJECT, \"\n"
         "                             \"or run inside a directory with a *.uproject file\")"),
    ],
    "blueprint/tools/blueprint_export.py": [
        ("# Inside the project's config directory: `.octopus/`, or `.octopus/` before the migration.",
         "# Inside the project's config directory, `.unreal-kit/`."),
        ("each kind is in `packs/blueprint/README.md`.",
         "each kind is in `blueprint/README.md`."),
        ("(see packs/PACKS.md)",
         "(see unreal/README.md)"),
    ],
    "ue-angelscript/tools/_export.py": [
        ("# Inside the project's config directory: `.octopus/`, or `.octopus/` before the migration.",
         "# Inside the project's config directory, `.unreal-kit/`."),
    ],
    "unreal/tools/build_editor.ps1": [
        ("    Install writes the marker when a pack replaced editor sources in the project (the\n",
         "    Create the marker after a change of editor sources in the project (the\n"),
    ],
    "unreal/tools/start_editor.ps1": [
        ("# Install leaves .octopus\\build-pending when a pack replaced editor sources while the editor ran\n",
         "# Create .octopus\\build-pending after a change of editor sources while the editor ran\n"),
    ],
    "blueprint/Plugins/AssetTextExport/AssetTextExport.uplugin": [
        ('"CreatedBy": "Octopus AI"', '"CreatedBy": ""'),
    ],
}

RENAMES = [
    ("<Octopus AI>/packs/", "<kit>/"),
    (".octopus\\", ".unreal-kit\\"),
    (".octopus", ".unreal-kit"),
    ("OCTOPUS_", "UNREAL_KIT_"),
]

FORBIDDEN = re.compile(r"octopus|todo\.py|\{\{OCTOPUS\}\}|\{\{PROJECT\}\}", re.IGNORECASE)


def copy_tree(src: Path, dst: Path) -> None:
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts) or path.suffix in SKIP_SUFFIXES:
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def rewrite(path: Path, rel: str, errors: list[str]) -> None:
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    for old, new in PATCHES.get(rel, []):
        count = text.count(old)
        if count != 1:
            errors.append("%s: patch matches %d times: %r" % (rel, count, old.splitlines()[0]))
            continue
        text = text.replace(old, new)
    for old, new in RENAMES:
        text = text.replace(old, new)
    if crlf:
        text = text.replace("\n", "\r\n")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="the directory that receives unreal-kit/ and unreal-kit.zip")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    kit = out / KIT
    if kit.exists():
        shutil.rmtree(kit)
    kit.mkdir(parents=True)

    copy_tree(HAND_WRITTEN, kit)
    for rel in COPIED:
        copy_tree(REPO / "packs" / rel, kit / rel)

    errors: list[str] = []
    for path in sorted(p for p in kit.rglob("*") if p.is_file()):
        if path.suffix.lower() in TEXT_SUFFIXES:
            rewrite(path, path.relative_to(kit).as_posix(), errors)
    for rel in PATCHES:
        if not (kit / rel).is_file():
            errors.append("%s: the file of a patch is missing" % rel)

    files = sorted(p for p in kit.rglob("*") if p.is_file())
    for path in files:
        text = path.read_bytes().decode("utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line):
                errors.append("%s:%d: %s" % (path.relative_to(kit).as_posix(), n, line.strip()))

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1

    archive = out / (KIT + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in files:
            z.write(path, path.relative_to(out).as_posix())
    print("%s (%d files)" % (archive, len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
