#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
find_unbindable_names.py — census of every Blueprint symbol name that AngelScript
can never bind to.

An FName is not an identifier. Unreal accepts `Post Update Score`,
`GetСountValue` (Cyrillic C), `tmpArr ` (trailing space) and a pin literally
named `return`. AngelScript cannot declare any of them, so every one of these
blocks its symbol until a human renames it in the editor.

WHY NOT A BYTE REGEX OVER .uasset
    A naive "non-ASCII near letters" regex over raw `.uasset` bytes matches
    compressed export data and returns ~1000 assets of pure noise. This tool
    reads *real FNames* from two structured sources instead.

SOURCES
  1. T3D exports  <t3dDir>/Text<Asset>-N.txt  (`t3dDir` of .octopus/blueprint.json,
        default T3D)
        Authoritative for KIND and ATTRIBUTION: which class declares the name,
        whether the site is a declaration or a reference, and whether it is a
        function / event / member variable / local variable / parameter pin.
        Some of these files are UTF-16LE with a BOM; reading one as UTF-8 with
        errors="ignore" silently yields garbage that matches nothing, so the
        BOM is sniffed per file.
  2. Package name tables  Content/**/*.uasset
        The FName table of every package, parsed from the package header (not
        a byte regex). Covers assets that were never exported to T3D. Kind is
        unknown here, so these are reported as a completeness backstop only.
        The EXPORT table of the same package answers a second question the name
        table cannot: which graphs the asset declares, which is what decides
        whether a stale symbol was renamed or deleted.
  3. The call index (`callIndexPath` of .octopus/blueprint.json, default
        Docs/_callindex.json) — external caller counts, for ordering by value.

NOT a source: a symbol index that stores SPACE-STRIPPED names ("SetTint", not
"Set Tint Scene"). A census that starts there misses nearly every entry.

PROJECT SETTINGS (`find_unbindable_names` in .octopus/blueprint.json)
    thirdPartyOwners      classes of third-party assets; a name declared only
                          there is left out unless --third-party is given
    packagesFailedNote    text printed after the failed-package count
    mdCommand             the command line quoted at the top of the --md report

USAGE
    python find_unbindable_names.py                 # summary to stdout
    python find_unbindable_names.py --md out.md     # full markdown report
    python find_unbindable_names.py --json out.json # machine readable
    python find_unbindable_names.py --all           # include engine noise
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import struct
import sys
import unicodedata

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project                                                             # noqa: E402
from _uasset import Package, PackageError                                   # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("blueprint").get("find_unbindable_names", {})
T3D_SUBDIR = _project.setting("blueprint", "t3dDir", "T3D")
CALLINDEX = _project.setting("blueprint", "callIndexPath", "Docs/_callindex.json")

# --------------------------------------------------------------------------
# AngelScript reserved words
# Extracted from the fork's own tokenizer:
#   Engine/Plugins/Angelscript/ThirdParty/source/as_tokendef.h
# plus the UE-AngelScript additions (`event`, `delegate`, `default`, `access`,
# `mixin`) that the preprocessor treats as keywords.
# --------------------------------------------------------------------------
ANGELSCRIPT_KEYWORDS = {
    "abstract", "access", "and", "auto", "bool", "break", "case", "cast",
    "class", "const", "continue", "default", "defaults", "delegate",
    "deprecated", "do", "double", "editdefaults", "else", "enum", "event",
    "external", "fallthrough", "false", "final", "float", "float32", "float64",
    "for", "from", "funcdef", "function", "get", "if", "import", "in",
    "inherited", "inout", "int", "int8", "int16", "int32", "int64", "interface",
    "is", "local", "mixin", "namespace", "no_discard", "not", "null", "nullptr",
    "or", "out", "override", "private", "property", "protected", "public",
    "readonly", "return", "set", "shared", "struct", "super", "switch", "text",
    "this", "tidy", "true", "typedef", "uint", "uint8", "uint16", "uint32",
    "uint64", "void", "while", "xor",
}

# Confusable Latin twins for the homoglyphs that actually turn up in Cyrillic /
# Greek text. Used to explain an invisible defect in the report.
CONFUSABLES = {
    0x0410: "A", 0x0412: "B", 0x0415: "E", 0x0417: "3", 0x041A: "K",
    0x041C: "M", 0x041D: "H", 0x041E: "O", 0x0420: "P", 0x0421: "C",
    0x0422: "T", 0x0423: "Y", 0x0425: "X", 0x0430: "a", 0x0435: "e",
    0x043E: "o", 0x0440: "p", 0x0441: "c", 0x0443: "y", 0x0445: "x",
    0x0456: "i", 0x0405: "S", 0x0408: "J", 0x0406: "I", 0x0458: "j",
    0x0391: "A", 0x0392: "B", 0x0395: "E", 0x0396: "Z", 0x0397: "H",
    0x0399: "I", 0x039A: "K", 0x039C: "M", 0x039D: "N", 0x039F: "O",
    0x03A1: "P", 0x03A4: "T", 0x03A5: "Y", 0x03A7: "X", 0x03BF: "o",
    0x00A0: "(space)", 0x2013: "-", 0x2014: "-", 0x2019: "'", 0x2018: "'",
}

# Names that Unreal itself generates and that no human ever renames: engine pin
# labels, auto-derived cast locals, display names of engine nodes. They appear
# in package name tables but are not project symbols.
ENGINE_NOISE = {
    " ", "  ", "Array Element", "Array Index", "Event Graph", "Is Valid",
    "Is Not Valid", "Return Value", "Exec", "Then", "Out Valid", "In Array",
    "New Item", "Target Array", "Item", "Index", "Set By Caller", "Self",
    "Construction Script", "User Construction Script", "Default Scene Root",
    "Multiplayer Compatible", "Delta Seconds", "Dimension 1", "Dimension 2",
    "New Var", "Blueprint Event", "Duration", "Text Color", "Print to Screen",
    "Print to Log", "In String", "In Text", "New Class", "Object Class",
    "Return Node", "Sequence", "Branch", "Cast Failed", "Cast Succeeded",
}
# Prefixes of auto-generated names (cast node output locals: "As BP My Actor")
ENGINE_NOISE_PREFIXES = ("As ", "AsBP", "As BP", "K2Node_", "EdGraphPin_")

ILLEGAL_CHAR_RE = re.compile(r"[^A-Za-z0-9_]")

# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

CATEGORIES = [
    "non-ascii",          # 2 — homoglyphs; invisible on screen
    "whitespace-anomaly", # 5 — trailing / leading / doubled space
    "space",              # 1 — the known bulk
    "leading-digit",      # 3
    "illegal-char",       # 3 — - . / # ' etc
    "keyword",            # 4 — legal identifier, reserved word
]


def classify(name: str):
    """Return (list_of_categories, detail_dict). Empty list = bindable."""
    cats = []
    detail = {}

    non_ascii = [c for c in name if ord(c) > 127]
    if non_ascii:
        cats.append("non-ascii")
        detail["codepoints"] = [
            {
                "char": c,
                "cp": "U+%04X" % ord(c),
                "unicode_name": unicodedata.name(c, "<unnamed>"),
                "confusable_with": CONFUSABLES.get(ord(c)),
            }
            for c in dict.fromkeys(non_ascii)
        ]

    if name != name.strip() or "  " in name:
        cats.append("whitespace-anomaly")
        detail["whitespace"] = {
            "leading": len(name) - len(name.lstrip()),
            "trailing": len(name) - len(name.rstrip()),
            "doubled": "  " in name,
        }
    elif " " in name:
        cats.append("space")

    if name and name[0].isdigit():
        cats.append("leading-digit")

    bad = sorted({c for c in name if ILLEGAL_CHAR_RE.match(c) and c != " " and ord(c) <= 127})
    if bad:
        cats.append("illegal-char")
        detail["illegal_chars"] = bad

    if not cats and name in ANGELSCRIPT_KEYWORDS:
        cats.append("keyword")

    return cats, detail


# --------------------------------------------------------------------------
# source 1 — T3D exports
# --------------------------------------------------------------------------

def read_t3d(path: str) -> str:
    """Read a T3D dump, sniffing the BOM. Some dumps are UTF-16LE."""
    with open(path, "rb") as h:
        b = h.read()
    if b[:2] == b"\xff\xfe":
        return b[2:].decode("utf-16-le", errors="replace")
    if b[:2] == b"\xfe\xff":
        return b[2:].decode("utf-16-be", errors="replace")
    if b[:3] == b"\xef\xbb\xbf":
        return b[3:].decode("utf-8", errors="replace")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", errors="replace")


RE_BEGIN = re.compile(r'^\s*Begin Object(?:\s+Class=(\S+))?\s+Name="([^"]*)"')
RE_END = re.compile(r"^\s*End Object\s*$")
RE_GRAPH_ARRAY = re.compile(
    r'^\s*(FunctionGraphs|MacroGraphs|UbergraphPages|DelegateSignatureGraphs)\(\d+\)='
    r'"/Script/Engine\.EdGraph\'([^\']*)\'"', re.M)
RE_NEWVAR = re.compile(r'\bNewVariables\(\d+\)=\(VarName="([^"]*)"')
RE_LOCALVAR = re.compile(r'\bLocalVariables\(\d+\)=\(VarName="([^"]*)"')
RE_FUNCREF = re.compile(r'\bFunctionReference=\(MemberName="([^"]*)"([^)]*)\)')
RE_EVENTREF = re.compile(r'\bEventReference=\(MemberName="([^"]*)"([^)]*)\)')
RE_VARREF = re.compile(r'\bVariableReference=\(([^)]*)\)')
RE_VARREF_MEMBER = re.compile(r'MemberName="([^"]*)"')
RE_CUSTOMFN = re.compile(r'\bCustomFunctionName="([^"]*)"')
RE_PIN = re.compile(r'\bPinName="([^"]*)"')
RE_MEMBERPARENT = re.compile(r'MemberParent="[^\']*\'([^\']*)\'"')

DECL_NODES = ("K2Node_FunctionEntry", "K2Node_FunctionResult", "K2Node_CustomEvent",
              "K2Node_Tunnel")

Record = collections.namedtuple("Record", "name kind site owner graph source")


def scan_t3d(root: str, records: list, warnings: list):
    d = os.path.join(root, T3D_SUBDIR)
    files = sorted(glob.glob(os.path.join(d, "Text*.txt")))
    seen_assets = set()
    for path in files:
        base = os.path.basename(path)
        m = re.match(r"Text(.+)-\d+\.txt$", base)
        asset = m.group(1) if m else base
        seen_assets.add(asset)
        try:
            text = read_t3d(path)
        except Exception as exc:                       # pragma: no cover
            warnings.append("could not read %s: %s" % (base, exc))
            continue

        graph_kind = {}          # graph name -> function|macro|ubergraph|delegate
        for m in RE_GRAPH_ARRAY.finditer(text):
            graph_kind[m.group(2)] = {
                "FunctionGraphs": "function",
                "MacroGraphs": "macro",
                "UbergraphPages": "ubergraph",
                "DelegateSignatureGraphs": "dispatcher",
            }[m.group(1)]

        # A T3D dump names every object TWICE: first a bare
        #   Begin Object Class=<Class> Name=<N> / End Object
        # skeleton pass that carries the CLASS, then a
        #   Begin Object Name=<N> ... properties ... End Object
        # pass that carries the PROPERTIES but NOT the class. A parser that
        # reads only the second pass sees class "" for every node, so every
        # FunctionEntry looks like an ordinary call site and no declaration is
        # ever recognised. Class is therefore remembered from pass one, keyed by
        # the object's full name path (node names repeat between graphs).
        class_of = {}
        stack = []               # list of (class, name)
        cur_graph = None
        for line in text.splitlines():
            mb = RE_BEGIN.match(line)
            if mb:
                name = mb.group(2)
                path = tuple(n for _c, n in stack) + (name,)
                cls = (mb.group(1) or "").split(".")[-1]
                if cls:
                    class_of[path] = cls
                else:
                    cls = class_of.get(path, "")
                stack.append((cls, name))
                if cls == "EdGraph":
                    cur_graph = name
                continue
            if RE_END.match(line):
                if stack:
                    cls, nm = stack.pop()
                    if cls == "EdGraph":
                        cur_graph = None
                        for c, n in reversed(stack):
                            if c == "EdGraph":
                                cur_graph = n
                                break
                continue

            node_cls = stack[-1][0] if stack else ""
            in_decl_node = node_cls in DECL_NODES

            for m in RE_NEWVAR.finditer(line):
                records.append(Record(m.group(1), "member variable", "declaration",
                                      asset, None, base))
            for m in RE_LOCALVAR.finditer(line):
                records.append(Record(m.group(1), "local variable", "declaration",
                                      asset, cur_graph, base))
            for m in RE_CUSTOMFN.finditer(line):
                kind = "event" if node_cls == "K2Node_CustomEvent" else "function"
                records.append(Record(m.group(1), kind, "declaration", asset,
                                      cur_graph, base))
            for m in RE_FUNCREF.finditer(line):
                nm, rest = m.group(1), m.group(2)
                if node_cls in ("K2Node_FunctionEntry", "K2Node_FunctionResult"):
                    gk = graph_kind.get(cur_graph or "", "function")
                    records.append(Record(nm, gk, "declaration", asset, cur_graph, base))
                else:
                    mp = RE_MEMBERPARENT.search(rest)
                    owner = mp.group(1).split(".")[-1] if mp else "?"
                    records.append(Record(nm, "function", "reference", owner,
                                          cur_graph, base))
            for m in RE_EVENTREF.finditer(line):
                mp = RE_MEMBERPARENT.search(m.group(2))
                owner = mp.group(1).split(".")[-1] if mp else "?"
                records.append(Record(m.group(1), "event", "reference", owner,
                                      cur_graph, base))
            for m in RE_VARREF.finditer(line):
                mm = RE_VARREF_MEMBER.search(m.group(1))
                if mm:
                    records.append(Record(mm.group(1), "variable", "reference",
                                          asset, cur_graph, base))
            if "CustomProperties Pin" in line:
                for m in RE_PIN.finditer(line):
                    records.append(Record(
                        m.group(1),
                        "parameter" if in_decl_node else "pin",
                        "declaration" if in_decl_node else "reference",
                        asset, cur_graph, base))

        # graph names themselves are declarations
        for gname, gk in graph_kind.items():
            records.append(Record(gname, gk, "declaration", asset, gname, base))

    return seen_assets


# --------------------------------------------------------------------------
# source 2 — package name tables
# --------------------------------------------------------------------------

PKG_TAG = 0x9E2A83C1


def _rd_fstr(b, off):
    (n,) = struct.unpack_from("<i", b, off)
    off += 4
    if n == 0:
        return "", off
    if n < 0:
        cnt = -n
        raw = b[off:off + cnt * 2]
        off += cnt * 2
        return raw.decode("utf-16-le", errors="replace").rstrip("\x00"), off
    raw = b[off:off + n]
    off += n
    return raw.decode("utf-8", errors="replace").rstrip("\x00"), off


def _read_name_table(b, name_count, name_offset):
    o = name_offset
    out = []
    for _ in range(name_count):
        if o + 4 > len(b):
            return None
        s, o = _rd_fstr(b, o)
        o += 4                                        # two uint16 FName hashes
        if o > len(b) or "\x00" in s:
            return None
        out.append(s)
    return out


def package_names(path: str, root: str = None):
    """FName table of a .uasset / .umap, read from the package header.

    One Content tree can hold several `LegacyFileVersion` values (such as
    -5, -7, -8, -9) and the field layout between the licensee version and the
    custom-version array is NOT the same in all of them: -9 packages carry an
    extra 24-byte block (a 20-byte FIoHash plus an int32) that -7 packages do
    not. Rather than hard-code an engine version table, the parser tries each
    plausible skip and accepts the first one whose whole chain validates —
    custom-version count, total header size, folder/package name, then a name
    table that reads to its exact end. A wrong guess fails one of those checks.
    """
    with open(path, "rb") as h:
        b = h.read()
    if len(b) < 64 or struct.unpack_from("<I", b, 0)[0] != PKG_TAG:
        return None, "not-a-package"
    (legacy,) = struct.unpack_from("<i", b, 4)
    if legacy >= 0:
        return None, "legacy-format"
    base = 8
    if legacy != -4:
        base += 4                                     # LegacyUE3Version
    base += 4                                         # FileVersionUE4
    if legacy <= -8:
        base += 4                                     # FileVersionUE5
    base += 4                                         # FileVersionLicenseeUE

    # A package's name table almost always contains its own asset name, which
    # is a strong check that a candidate layout is the real one and not a lucky
    # misparse. It is not universal (a renamed asset keeps the old package
    # name), so a candidate that merely reads cleanly is kept as a fallback.
    own = os.path.splitext(os.path.basename(path))[0]
    fallback = None

    for skip in range(0, 65, 4):
        for has_total_header in (True, False):
            off = base + skip
            try:
                if legacy <= -2:
                    (n_custom,) = struct.unpack_from("<i", b, off); off += 4
                    if not (0 <= n_custom <= 128):
                        continue
                    off += n_custom * 20              # FGuid + int32 each
                if has_total_header:
                    (total_header,) = struct.unpack_from("<i", b, off); off += 4
                    if not (0 < total_header <= len(b)):
                        continue
                folder, off = _rd_fstr(b, off)
                if len(folder) > 1024:
                    continue
                off += 4                              # PackageFlags
                (name_count,) = struct.unpack_from("<i", b, off); off += 4
                (name_offset,) = struct.unpack_from("<i", b, off); off += 4
            except struct.error:
                continue
            if not (0 < name_count < 500000 and off <= name_offset < len(b)):
                continue
            names = _read_name_table(b, name_count, name_offset)
            if names is None:
                continue
            if own in names or "None" in names:
                return names, "ok"
            if fallback is None and len(names) > 4:
                fallback = names
    if fallback is not None:
        return fallback, "ok-unanchored"
    return None, "header-unparsed"


BP_CLASS_MARKERS = {"BlueprintGeneratedClass", "WidgetBlueprintGeneratedClass",
                    "AnimBlueprintGeneratedClass"}


def scan_packages(root: str, warnings: list):
    """name -> {asset}. Only names that fail classification.

    Also returns the set of assets that are compiled Blueprint classes, so a
    name-table appearance can be read as "this Blueprint still mentions the
    symbol" — an independent liveness and caller check against the T3D dumps,
    which are snapshots and can be stale.
    """
    hits = collections.defaultdict(set)
    widgets = set()
    # asset -> the package files that carry that basename, so a later question
    # about one asset's export table is answered without a second walk. Several
    # assets can share a basename, so every one of them is kept — keeping only
    # the last silently reports live symbols as deleted.
    asset_paths = collections.defaultdict(set)
    blueprints = set()
    content = os.path.join(root, "Content")
    n_ok = n_fail = 0
    for dirpath, _dirs, files in os.walk(content):
        for f in files:
            if not f.endswith((".uasset", ".umap")):
                continue
            p = os.path.join(dirpath, f)
            asset_paths[os.path.splitext(f)[0]].add(p)
            try:
                names, status = package_names(p, root)
            except Exception as exc:
                names, status = None, "exception: %s" % exc
            if names is None:
                n_fail += 1
                warnings.append("%s: %s" % (os.path.relpath(p, root), status))
                continue
            n_ok += 1
            asset = os.path.splitext(f)[0]
            if BP_CLASS_MARKERS.intersection(names):
                blueprints.add(asset)
            # "WidgetBlueprintGeneratedClass" appears in any package that merely
            # REFERENCES a widget, so it is not a test of what the asset is.
            # "WidgetTree" is a subobject only a UMG widget owns.
            if "WidgetTree" in names:
                widgets.add(asset)
            for nm in names:
                cats, _ = classify(nm)
                if cats:
                    hits[nm].add(asset)
    return hits, asset_paths, blueprints, widgets, n_ok, n_fail


_GRAPH_CACHE = {}


def declared_graphs(paths, warnings: list):
    """The graph names one asset DECLARES, from the export table.

    A Blueprint's function, macro and dispatcher graphs are exports of class
    `EdGraph`, and the export's object name IS the graph name. The FName table
    cannot answer the same question: it also holds every name the asset merely
    REFERENCES — a call node's target, a property on the AngelScript parent —
    so a symbol whose graph was deleted and whose body now lives in script is
    still in the table.

    A package that does not parse contributes nothing and adds a warning.
    """
    key = tuple(sorted(paths))
    if key in _GRAPH_CACHE:
        return _GRAPH_CACHE[key]
    out = set()
    for p in key:
        try:
            pkg = Package(p)
            for e in pkg.exports:
                if (pkg.class_of(e) or "") == "EdGraph":
                    out.add(e.object_name)
        except (PackageError, struct.error, OSError, IndexError) as exc:
            warnings.append("%s: export table unread, no rename verdict: %s"
                            % (os.path.basename(p), exc))
    _GRAPH_CACHE[key] = out
    return out


def is_engine_noise(name: str) -> bool:
    if name in ENGINE_NOISE:
        return True
    if name.startswith(ENGINE_NOISE_PREFIXES):
        return True
    if name.startswith("/") or name.startswith("\\"):   # object paths
        return True
    if "." in name and "/" in name:                     # asset references
        return True
    return False


# --------------------------------------------------------------------------
# caller counts
# --------------------------------------------------------------------------

def load_callindex(root: str):
    """(name -> external caller count, name -> set(owner classes))"""
    p = os.path.join(root, CALLINDEX)
    counts = collections.Counter()
    owners = collections.defaultdict(set)
    if not os.path.exists(p):
        return counts, owners
    with open(p, encoding="utf-8") as h:
        d = json.load(h)
    for owner, members in d.get("symbols", {}).items():
        for member, callers in members.items():
            counts[member] += len(callers)
            owners[member].add(owner)
    return counts, owners


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------

THIRD_PARTY_OWNERS = set(CFG.get("thirdPartyOwners", []))

EXECUTABLE_KINDS = ("function", "macro", "event", "dispatcher")

# The declaration kinds that own a graph of their own. A custom event is a node
# inside the ubergraph, so it is not one of them.
GRAPH_KINDS = ("function", "macro", "dispatcher", "ubergraph")


def build(root: str, include_noise: bool = False):
    warnings = []
    records = []
    t3d_assets = scan_t3d(root, records, warnings)
    (pkg_hits, asset_paths, blueprints, widgets,
     pkg_ok, pkg_fail) = scan_packages(root, warnings)
    callers, call_owners = load_callindex(root)

    by_name = {}
    for r in records:
        cats, detail = classify(r.name)
        if not cats:
            continue
        e = by_name.setdefault(r.name, {
            "name": r.name, "categories": cats, "detail": detail,
            "decl_sites": collections.defaultdict(set),   # kind -> {owner}
            "decl_where": set(),                          # (kind, owner, graph)
            "ref_count": 0, "kinds": set(), "assets": set(),
            "t3d": True, "pkg_assets": set(),
        })
        e["assets"].add(r.owner)
        e["kinds"].add(r.kind)
        if r.site == "declaration":
            e["decl_sites"][r.kind].add(r.owner)
            e["decl_where"].add((r.kind, r.owner, r.graph or ""))
        else:
            e["ref_count"] += 1

    for nm, assets in pkg_hits.items():
        e = by_name.get(nm)
        if e is None:
            cats, detail = classify(nm)
            e = by_name[nm] = {
                "name": nm, "categories": cats, "detail": detail,
                "decl_sites": collections.defaultdict(set),
                "decl_where": set(), "ref_count": 0,
                "kinds": set(), "assets": set(), "t3d": False,
                "pkg_assets": set(),
            }
        e["pkg_assets"] |= assets
        e["assets"] |= assets

    out = []
    for nm, e in by_name.items():
        noise = is_engine_noise(nm) and not e["decl_sites"]
        if noise and not include_noise:
            continue
        decl = {k: sorted(v) for k, v in e["decl_sites"].items()}
        sites = sorted({o for s in e["decl_sites"].values() for o in s})
        ext = callers.get(nm, 0)
        bp_mentions = sorted(e["pkg_assets"] & blueprints)
        # The T3D dumps are SNAPSHOTS and go stale. A declaration site whose own
        # package no longer contains the FName has already been renamed or
        # deleted in the editor, and is not work any more. Checked per SITE, not
        # per name: a name can be gone from one class and still declared on
        # another.
        live_sites = [o for o in sites if o in e["pkg_assets"]]
        stale_sites = [o for o in sites if o not in e["pkg_assets"]]
        # A rename is a graph the asset still DECLARES under the new spelling.
        # Only a site that declared the name as a GRAPH can be answered this
        # way; a member variable, a local variable, a pin and a custom event
        # are not exports of class `EdGraph`, so they get no verdict at all.
        graph_stale = [o for o in stale_sites
                       if any(k in GRAPH_KINDS and o in v
                              for k, v in e["decl_sites"].items())]
        alts = {alt for alt in (nm.replace(" ", ""), nm.replace(" ", "_")) if alt != nm}
        renamed_to = sorted({
            alt for alt in alts
            if any(alt in declared_graphs(asset_paths.get(o, ()), warnings)
                   for o in graph_stale)})
        out.append({
            "name": nm,
            "categories": e["categories"],
            "detail": e["detail"],
            "kinds": sorted(e["kinds"]) or ["unknown (name table only)"],
            "declarations": decl,
            "declaring_classes": sites,
            "declaration_sites": len(sites),
            "live_sites": live_sites,
            # A parameter is renamed per FUNCTION, not per class: one class can
            # own eight different functions each with a pin named `bool`.
            "where": sorted(("%s::%s (%s)" % (o, g, k)) if g else
                            ("%s (%s)" % (o, k))
                            for k, o, g in e["decl_where"] if o in live_sites),
            "rename_units": len({(o, g) for k, o, g in e["decl_where"]
                                 if o in live_sites}),
            "stale_sites": stale_sites,
            "renamed_to": renamed_to,
            "declaration_kinds": sorted(decl),
            "executable": any(k in EXECUTABLE_KINDS for k in decl),
            "third_party": bool(sites) and set(sites) <= THIRD_PARTY_OWNERS,
            "widget_owned": bool(live_sites) and set(live_sites) <= widgets,
            "internal_refs": e["ref_count"],
            "external_callers": ext,
            "callindex_owners": sorted(call_owners.get(nm, ())),
            "blueprints_mentioning": bp_mentions,
            "mention_count": len(bp_mentions),
            "seen_in_t3d": e["t3d"],
            "live": bool(live_sites) or not sites,
            "value": max(ext, 0) * max(len(live_sites), 1) + max(len(live_sites), 1),
        })
    out.sort(key=lambda x: (-x["value"], -x["external_callers"], x["name"].lower()))
    return {
        "root": root,
        "t3d_assets": sorted(t3d_assets),
        "blueprint_classes": len(blueprints),
        "widget_classes": len(widgets),
        "packages_parsed": pkg_ok,
        "packages_failed": pkg_fail,
        "warnings": warnings,
        "names": out,
    }


def summarize(names, site_key="live_sites"):
    """(distinct names, declaration sites) per category."""
    cat_counts = collections.Counter()
    site_counts = collections.Counter()
    for e in names:
        n_sites = len(e[site_key]) if site_key in e else e["declaration_sites"]
        for c in e["categories"]:
            cat_counts[c] += 1
            site_counts[c] += max(n_sites, 1)
    return cat_counts, site_counts


def select(names, declared=True, third_party=False, executable=None,
           include_stale=False):
    out = []
    for e in names:
        if declared and not e["declaration_sites"]:
            continue
        if not include_stale and e["declaration_sites"] and not e["live_sites"]:
            continue
        if not third_party and e["third_party"]:
            continue
        if executable is not None and e["executable"] != executable:
            continue
        out.append(e)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--json", help="write full result as JSON")
    ap.add_argument("--md", help="write a markdown report")
    ap.add_argument("--all", action="store_true", help="include engine-generated noise")
    ap.add_argument("--category", help="only this category")
    ap.add_argument("--mentions", action="store_true",
                    help="also list names that are only referenced, never declared")
    ap.add_argument("--third-party", action="store_true",
                    help="include the assets named in thirdPartyOwners")
    ap.add_argument("--include-stale", action="store_true",
                    help="keep names whose every declaration site is gone from "
                         "the current .uasset (already renamed or deleted)")
    args = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass

    res = build(args.root, include_noise=args.all)
    names = select(res["names"], declared=not args.mentions,
                   third_party=args.third_party, include_stale=args.include_stale)
    if args.category:
        names = [n for n in names if args.category in n["categories"]]

    cat_counts, site_counts = summarize(names)
    print("packages parsed : %d  (failed %d%s)"
          % (res["packages_parsed"], res["packages_failed"],
             CFG.get("packagesFailedNote", "")))
    print("Blueprint classes: %d   T3D-covered assets: %d"
          % (res["blueprint_classes"], len(res["t3d_assets"])))
    print("distinct unbindable names: %d" % len(names))
    print()
    print("%-20s %8s %8s" % ("category", "names", "sites"))
    for c in CATEGORIES:
        if cat_counts[c]:
            print("%-20s %8d %8d" % (c, cat_counts[c], site_counts[c]))
    print()
    print("top 25 by (external callers x live declaration sites):")
    for e in names[:25]:
        print("  %-44s %-24s sites=%-3d callers=%-3d %s"
              % (e["name"][:44], ",".join(e["categories"]),
                 len(e["live_sites"]), e["external_callers"],
                 ",".join(e["declaration_kinds"])[:30]))
    stale = [e for e in res["names"]
             if e["declaration_sites"] and not e["live_sites"]]
    if stale and not args.include_stale:
        print("\nsuppressed as already renamed / deleted (--include-stale to show): %d"
              % len(stale))
        for e in stale:
            print("  %-40s was on %s%s" % (
                e["name"], ", ".join(e["stale_sites"]),
                "  -> now %s" % ", ".join(e["renamed_to"]) if e["renamed_to"] else ""))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as h:
            json.dump(res, h, indent=1, ensure_ascii=False)
        print("\nwrote %s" % args.json)
    if args.md:
        write_md(names, args.md)
        print("wrote %s" % args.md)


def write_md(names, path):
    cat_counts, site_counts = summarize(names)
    L = ["# Unbindable Blueprint names — generated census", "",
         "`%s`" % CFG.get("mdCommand", "python find_unbindable_names.py --md <file>"), "",
         "| category | distinct names | declaration sites |", "|---|---:|---:|"]
    for c in CATEGORIES:
        if cat_counts[c]:
            L.append("| %s | %d | %d |" % (c, cat_counts[c], site_counts[c]))
    L += ["", "| name | categories | declared as | sites | ext. callers | declaring classes |",
          "|---|---|---|---:|---:|---|"]
    for e in names:
        L.append("| `%s` | %s | %s | %d | %d | %s |" % (
            e["name"].replace("|", "\\|"), ", ".join(e["categories"]),
            ", ".join(e["declaration_kinds"]), e["declaration_sites"],
            e["external_callers"], ", ".join(e["declaring_classes"]) or "—"))
    with open(path, "w", encoding="utf-8") as h:
        h.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
