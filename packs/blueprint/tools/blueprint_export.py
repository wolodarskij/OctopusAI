"""Export Blueprint data to JSON files, for the tools that must not read a Blueprint.

A tool of the `ue-angelscript` pack compares `Script/` with the Blueprints, but it never reads a
Blueprint. This tool reads the Blueprints (the package bytes under `Content/` and the text exports
in the `t3dDir` directory) and writes what those tools need into one JSON file per kind of data.
The AngelScript tool then reads `Script/` and that file.

    python blueprint_export.py <kind> [--project <path>] [--out-dir <dir>]
    python blueprint_export.py all

The directory is `exportDir` of `<project>/.octopus/blueprint.json`; the default is
`.octopus/export/blueprint`. Keep it outside `Saved/`: the engine mounts `Saved/` as a package root.

KINDS, AND THE FILE EACH ONE WRITES
-----------------------------------
  packages      packages.json     every package path under `Content/` (`/Game/...`, no extension)
  stringtables  stringtables.json the keys and source strings of every string table asset
  variables     variables.json    the member variables of every text export, newest export first,
                                  and the parent chain of every Blueprint
  structs       structs.json      the fields of every UserDefinedStruct asset: name, FriendlyName,
                                  container, SaveGame, and the pin type
  blueprints    blueprints.json   every Blueprint with its direct parent, and for each one that
                                  reaches a script class: variables, graphs, custom events, overrides
  components    components.json   the components of every package: construction script nodes,
                                  AddComponent nodes, component handlers, placed-actor components
  references    references.json   from the newest text export of each asset: member names that
                                  call nodes name, function graph names, custom event names

Every file carries `kind`, `schema` (1), `generatedBy` and `generated` (UTC time). The schema of
each kind is in `packs/blueprint/README.md`.

PROJECT SETTINGS (`<project>/.octopus/blueprint.json`)
------------------------------------------------------
  exportDir                     the export directory, relative to the project root
  t3dDir                        the text export directory (default `T3D`)
  blueprint_export.structControl  `{asset, struct, expect: [[field, container, category, terminal]]}`:
                                a struct whose pin types are known. `structs` decodes it first and
                                records in the file whether the decoder read it correctly.

Exit 0 when every file was written, 2 when a kind could not be exported.
"""

from __future__ import annotations

import argparse
import collections
import glob
import io
import json
import os
import re
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

SCHEMA = 1
# Inside the project's config directory: `.octopus/`, or `.octopus/` before the migration.
DEFAULT_EXPORT_DIR = "export/blueprint"
KINDS = ("packages", "stringtables", "variables", "structs", "blueprints", "components",
         "references")


def export_dir():
    """The export directory, as an absolute path."""
    default = _project.in_config(DEFAULT_EXPORT_DIR)
    raw = _project.setting("blueprint", "exportDir", default) or default
    path = raw if os.path.isabs(raw) else os.path.join(str(_project.project_root()), raw)
    return os.path.normpath(path)


def _rel(path, root):
    return os.path.relpath(path, root).replace("\\", "/")


def _content_files(root, extensions):
    content = os.path.join(root, "Content")
    for dirpath, _dirs, files in os.walk(content):
        for name in sorted(files):
            if name.endswith(extensions):
                yield os.path.join(dirpath, name)


# =================================================================================================
# packages
# =================================================================================================
def export_packages(root):
    packages = set()
    for path in _content_files(root, (".uasset", ".umap")):
        rel = _rel(path, os.path.join(root, "Content"))
        packages.add("/Game/" + os.path.splitext(rel)[0])
    return {"packages": sorted(packages)}


# =================================================================================================
# stringtables
# =================================================================================================
def export_stringtables(root):
    import stringtable_keys
    tables, unreadable = {}, []
    content = os.path.join(root, "Content")
    for path in _content_files(root, (".uasset",)):
        try:
            rows = stringtable_keys.entries(path)
        except Exception as exc:                                  # noqa: BLE001 - recorded
            unreadable.append({"path": _rel(path, root), "why": str(exc)})
            continue
        if not rows:
            continue
        package = "/Game/" + _rel(path, content)[:-len(".uasset")]
        # A call site names the table id: the package path, a dot, then the object name.
        tables[package + "." + package.rsplit("/", 1)[-1]] = dict(rows)
    return {"tables": tables, "unreadable": unreadable}


# =================================================================================================
# variables
# =================================================================================================
_VAR = re.compile(r'NewVariables\(\d+\)=\(([^\n]*)')
_EXPORT_NAME = re.compile(r'^Text(.+)-(\d+)\.txt$')


def read_text_export(path):
    """A text export, with the encoding sniffed from the BOM. Some exports are UTF-16."""
    with open(path, "rb") as fh:
        head = fh.read(2)
    enc = "utf-16" if head in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    with open(path, encoding=enc, errors="ignore") as fh:
        return fh.read()


def parse_new_variables(text):
    out = {}
    for m in _VAR.finditer(text):
        s = m.group(1)
        nm = re.search(r'VarName="([^"]+)"', s)
        if not nm:
            continue
        ctr = re.search(r'ContainerType=(\w+)', s)
        cat = re.search(r'PinCategory="([^"]*)"', s)
        pf = re.search(r'PropertyFlags=(\d+)', s)
        out[nm.group(1)] = {
            "container": ctr.group(1) if ctr else "None",
            "category": cat.group(1) if cat else "?",
            "flags": int(pf.group(1)) if pf else 0,
        }
    return out


def export_variables(root):
    import _t3d_dir
    import parent_census

    by_stem = collections.defaultdict(list)
    for path in glob.glob(os.path.join(_t3d_dir.T3D_DIR, "Text*.txt")):
        m = _EXPORT_NAME.match(os.path.basename(path))
        if m:
            by_stem[m.group(1)].append(path)

    blueprints = {}
    for stem in sorted(by_stem):
        # Newest first, by MTIME. The trailing number of an export is not chronological: the
        # exporter's index resets on an editor restart.
        files = sorted(by_stem[stem], key=os.path.getmtime, reverse=True)
        exports = []
        for path in files:
            entry = {"file": os.path.basename(path)}
            try:
                text = read_text_export(path)
            except OSError as exc:
                entry.update({"decoded": False, "error": str(exc), "variables": {}})
                exports.append(entry)
                continue
            variables = parse_new_variables(text)
            # Zero variables and no object at all means the file did not decode.
            entry["decoded"] = bool(variables) or "Begin Object" in text
            entry["variables"] = variables
            exports.append(entry)
        blueprints[stem] = {"exports": exports}

    parents = {}
    here = os.getcwd()
    os.chdir(root)
    try:
        by_class = parent_census.build_index("Content")
        for path in sorted(glob.glob(os.path.join("Content", "**", "*.uasset"), recursive=True)):
            try:
                own, _parent, _kind, _pkg = parent_census.direct_parent(path)
            except Exception:                                     # noqa: BLE001 - not a Blueprint
                continue
            if own is None:
                continue
            chain, landing, rootcls, _rootpkg = parent_census.chain_of(path, by_class)
            stem = own[:-2] if own.endswith("_C") else own
            parents[stem] = {"path": path.replace("\\", "/"), "chain": chain,
                             "landing": landing, "root": rootcls}
    finally:
        os.chdir(here)
    return {"blueprints": blueprints, "parents": parents}


# =================================================================================================
# structs
# =================================================================================================
class ParseError(Exception):
    pass


def packed_fname(name):
    """A serialised FName entry: int32 length-including-NUL, the bytes, then the NUL."""
    b = name.encode("ascii")
    return struct.pack("<i", len(b) + 1) + b + b"\x00"


# The editor-data object exists only inside a struct asset. Every package that USES a struct
# imports the class name `UserDefinedStruct`, so that name does not select the struct assets.
UDS_DISCRIMINATOR = packed_fname("UserDefinedStructEditorData")


def read_name_table(data):
    """Walk the package's FName table: the longest valid run of `int32 len` + ASCII + NUL + 4 bytes
    of hashes. The table must hold the names a UserDefinedStruct carries, or ParseError."""
    best = None
    for start in range(0, min(len(data), 4096)):
        names = []
        off = start
        while off + 4 <= len(data) and len(names) < 8000:
            length = struct.unpack_from("<i", data, off)[0]
            if length <= 0 or length > 512:
                break
            raw = data[off + 4:off + 4 + length]
            if len(raw) < length or not raw.endswith(b"\x00"):
                break
            body = raw[:-1]
            if not all(9 <= c < 127 for c in body):
                break
            names.append(body.decode("ascii", "replace"))
            off = off + 4 + length + 4
        if len(names) > 20 and (best is None or len(names) > len(best)):
            best = names
    if best is None:
        raise ParseError("no FName table could be walked")
    for sanity in ("VarName", "UserDefinedStructEditorData", "StructVariableDescription"):
        if sanity not in best:
            raise ParseError("FName table walked but is missing the sanity name %r - "
                             "the table is mis-anchored and every field read would be wrong"
                             % sanity)
    return best


def _struct_index(data):
    names = read_name_table(data)
    index = {}
    for i, name in enumerate(names):
        index.setdefault(name, i)

    def tag_offsets(prop, typ):
        """Offsets of every FPropertyTag whose (Name, Type) pair is (prop, typ)."""
        if prop not in index or typ not in index:
            return []
        pattern = struct.pack("<iiii", index[prop], 0, index[typ], 0)
        return [m.start() for m in re.finditer(re.escape(pattern), data)]

    return names, index, tag_offsets


def parse_struct_fields(data):
    """The FStructVariableDescription records: name, SaveGame, container, FriendlyName, category."""
    names, index, tag_offsets = _struct_index(data)

    def resolve(offset):
        i, _number = struct.unpack_from("<ii", data, offset)
        if not 0 <= i < len(names):
            raise ParseError("FName index %d out of range (table has %d)" % (i, len(names)))
        return names[i]

    def after_tag_header(offset):
        """Skip Size(4) + ArrayIndex(4) + optional property GUID, land on the value."""
        pos = offset + 16 + 8
        has_guid = data[pos]
        return pos + 1 + (16 if has_guid else 0)

    def scan_for_known(offset, window, table):
        """UE5 stores a tag's extras as FPropertyTypeName, so an EnumProperty value sits at a
        version-dependent offset. Look forward a bounded window for the first accepted FName."""
        for pos in range(offset, min(offset + window, len(data) - 8)):
            i, number = struct.unpack_from("<ii", data, pos)
            if number == 0 and i in table:
                return table[i]
        return None

    container_names = {index[n]: n for n in names if n.startswith("EPinContainerType::")}

    events = []
    for off in tag_offsets("VarName", "NameProperty"):
        events.append((off, "VarName", resolve(after_tag_header(off))))
    for off in tag_offsets("bEnableSaveGame", "BoolProperty"):
        # A BoolProperty carries its value in the tag itself: Size is 0, the byte follows ArrayIndex.
        events.append((off, "saveGame", bool(data[off + 24])))
    for off in tag_offsets("Category", "NameProperty"):
        events.append((off, "category", resolve(after_tag_header(off))))
    for typ in ("EnumProperty", "ByteProperty"):
        for off in tag_offsets("ContainerType", typ):
            events.append((off, "container", scan_for_known(off + 16, 80, container_names)))
    for off in tag_offsets("FriendlyName", "StrProperty"):
        pos = after_tag_header(off)
        length = struct.unpack_from("<i", data, pos)[0]
        if 0 < length < 300:
            events.append((off, "friendly",
                           data[pos + 4:pos + 4 + length - 1].decode("ascii", "replace")))

    events.sort()
    fields, current = [], None
    for _off, key, value in events:
        if key == "VarName":
            if current:
                fields.append(current)
            # VarName is serialised as `<name>_<index>_<32 hex GUID>`.
            current = {"name": value.rsplit("_", 2)[0], "raw": value, "saveGame": False,
                       "container": None, "friendly": None, "category": None}
        elif current is not None:
            current[key] = value
    if current:
        fields.append(current)
    if not fields:
        raise ParseError("zero fields decoded - a UserDefinedStruct always has at least one, so "
                         "this is a parse failure, NOT an empty struct")
    return fields


def parse_struct_types(data):
    """Per field: name, container, category and terminal category of the inline FEdGraphPinType."""
    names, index, tag_offsets = _struct_index(data)

    def name_value(offset):
        pos = offset + 16 + 8
        has_guid = data[pos]
        pos = pos + 1 + (16 if has_guid else 0)
        i, _number = struct.unpack_from("<ii", data, pos)
        if not 0 <= i < len(names):
            raise ParseError("FName index %d out of range at %d" % (i, offset))
        return names[i]

    container_names = {index[n]: n for n in names if n.startswith("EPinContainerType::")}

    def scan_container(offset):
        for pos in range(offset, min(offset + 96, len(data) - 8)):
            i, number = struct.unpack_from("<ii", data, pos)
            if number == 0 and i in container_names:
                return container_names[i]
        return None

    events = []
    for off in tag_offsets("VarName", "NameProperty"):
        events.append((off, "VAR", name_value(off).rsplit("_", 2)[0]))
    for off in tag_offsets("Category", "NameProperty"):
        events.append((off, "category", name_value(off)))
    for off in tag_offsets("TerminalCategory", "NameProperty"):
        events.append((off, "terminal", name_value(off)))
    for typ in ("EnumProperty", "ByteProperty"):
        for off in tag_offsets("ContainerType", typ):
            events.append((off, "container", scan_container(off + 16)))

    events.sort()
    fields, current = [], None
    for _off, key, value in events:
        if key == "VAR":
            if current:
                fields.append(current)
            current = {"name": value, "category": None,
                       "terminal": "None", "container": "EPinContainerType::None"}
        elif current is not None and value is not None:
            current[key] = value
    if current:
        fields.append(current)
    if not fields:
        raise ParseError("zero fields decoded - a UserDefinedStruct always has at least one, "
                         "so this is a parse failure, NOT an empty struct")
    return fields


def _struct_control(root, structs):
    control = (_project.config("blueprint").get("blueprint_export", {}) or {}).get("structControl")
    if not control or not control.get("asset") or not control.get("expect"):
        return {"status": "skipped", "struct": None, "problems": []}
    asset = control["asset"]
    path = asset if os.path.isabs(asset) else os.path.join(root, asset)
    problems = []
    if not os.path.exists(path):
        problems.append("control asset missing: %s - cannot prove the decoder works" % asset)
    else:
        try:
            with open(path, "rb") as handle:
                fields = parse_struct_types(handle.read())
        except ParseError as exc:
            fields = []
            problems.append("control asset failed to parse: %s" % exc)
        got = {f["name"]: (f["container"], f["category"], f["terminal"]) for f in fields}
        if fields:
            for row in control["expect"]:
                want = tuple(row[1:])
                if got.get(row[0]) != want:
                    problems.append("control %s: expected %s, decoded %s"
                                    % (row[0], want, got.get(row[0])))
    return {"status": "failed" if problems else "passed", "struct": control.get("struct"),
            "problems": problems}


def export_structs(root):
    structs, errors = {}, []
    for path in _content_files(root, (".uasset",)):
        try:
            with open(path, "rb") as handle:
                blob = handle.read()
        except OSError as exc:
            errors.append("%s: cannot be read (%s)" % (_rel(path, root), exc))
            continue
        if UDS_DISCRIMINATOR not in blob:
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        entry = {"path": _rel(path, root), "fields": None, "types": None,
                 "fieldsError": None, "typesError": None}
        try:
            entry["fields"] = parse_struct_fields(blob)
        except (ParseError, struct.error, IndexError) as exc:
            entry["fieldsError"] = str(exc)
        try:
            entry["types"] = parse_struct_types(blob)
        except (ParseError, struct.error, IndexError) as exc:
            entry["typesError"] = str(exc)
        structs[stem] = entry
    return {"structs": structs, "errors": errors, "control": _struct_control(root, structs)}


# =================================================================================================
# blueprints
# =================================================================================================
GRAPH_ARRAYS = ("FunctionGraphs", "MacroGraphs", "UbergraphPages", "DelegateSignatureGraphs")


def _fname_value(pkg, off, error):
    idx, num = struct.unpack_from("<ii", pkg.data, off)
    if not 0 <= idx < len(pkg.names):
        raise error("FName value index %d outside the table" % idx)
    return pkg.names[idx] + ("_%d" % (num - 1) if num else "")


def _blueprint_header(path):
    """(package, record) for a Blueprint, (None, None) for any other package. Header bytes only."""
    import _uasset
    pkg = _uasset.Package(path)
    stem = os.path.basename(path)[:-len(".uasset")]
    gen = None
    for e in pkg.exports:
        if e.object_name == stem + "_C":
            gen = e
            break
    if gen is None:
        return None, None
    name, _cls, is_import = pkg.resolve(gen.super_index)
    rec = {"parent": name, "parentIsScript": False}
    if is_import and gen.super_index < 0:
        im = pkg.import_of(gen.super_index)
        rec["parentIsScript"] = (im.package_name == "/Script/Angelscript"
                                 or im.class_name == "ASClass")
    return (pkg, gen), rec


def _object_array(pkg, tag, error):
    """ArrayProperty of ObjectProperty -> the referenced export/import names."""
    n = struct.unpack_from("<i", pkg.data, tag.value_off)[0]
    if n < 0 or n > 10000:
        raise error("implausible object-array count %d on %s" % (n, tag.name))
    out = []
    for k in range(n):
        idx = struct.unpack_from("<i", pkg.data, tag.value_off + 4 + 4 * k)[0]
        out.append(pkg.resolve(idx)[0])
    return [x for x in out if x]


def _new_variables(pkg, tag, error):
    """ArrayProperty of StructProperty FBPVariableDescription."""
    import _uasset
    data = pkg.data
    n = struct.unpack_from("<i", data, tag.value_off)[0]
    if n < 0 or n > 10000:
        raise error("implausible NewVariables count %d" % n)
    p = tag.value_off + 4
    out = []
    for _ in range(n):
        fields, p, _term = pkg.walk(p, tag.next_off)
        by_name = {f.name: f for f in fields}
        if "VarName" not in by_name:
            raise error("FBPVariableDescription with no VarName")
        name = _fname_value(pkg, by_name["VarName"].value_off, error)
        category = sub_name = sub_class = container = ""
        vt = by_name.get("VarType")
        if vt is not None:
            # FEdGraphPinType has a NATIVE serializer, so its bytes are not tagged properties:
            # FName PinCategory, FName PinSubCategory, FPackageIndex PinSubCategoryObject,
            # uint8 ContainerType.
            o = vt.value_off
            category = _fname_value(pkg, o, error)
            idx = struct.unpack_from("<i", data, o + 16)[0]
            if idx:
                sub_name, sub_class, _is_import = pkg.resolve(idx)
            container = _uasset.CONTAINER_NAMES.get(data[o + 20], "?")
        out.append({"name": name, "category": category, "subName": sub_name or "",
                    "subClass": sub_class or "", "container": container})
    return out


def _custom_event_name(pkg, export, error):
    for tag in pkg.script_tags(export):
        if tag.name == "CustomFunctionName":
            if tag.type != "NameProperty" or tag.size != 8:
                raise error("CustomFunctionName is %s/%d, not NameProperty/8"
                            % (tag.type, tag.size))
            return _fname_value(pkg, tag.value_off, error)
    return None


def _blueprint_body(pkg, gen, rec):
    """Variables, graph lists, custom events and overridden functions of one Blueprint."""
    from _uasset import PackageError
    bp_export = None
    for e in pkg.exports:
        cls = pkg.class_of(e) or ""
        # WidgetBlueprint, AnimBlueprint and the like are UBlueprint subclasses with the same lists.
        if cls == "Blueprint" or (cls.endswith("Blueprint") and not cls.endswith("GeneratedClass")):
            bp_export = e
            break
    if bp_export is None:
        raise PackageError("no UBlueprint export")

    body = {"variables": [], "functionGraphs": [], "macroGraphs": [], "ubergraphPages": [],
            "delegateGraphs": [], "customEvents": [], "overrides": []}
    keys = {"FunctionGraphs": "functionGraphs", "MacroGraphs": "macroGraphs",
            "UbergraphPages": "ubergraphPages", "DelegateSignatureGraphs": "delegateGraphs"}
    for tag in pkg.script_tags(bp_export):
        if tag.name in GRAPH_ARRAYS:
            body[keys[tag.name]] = _object_array(pkg, tag, PackageError)
        elif tag.name == "NewVariables":
            body["variables"] = _new_variables(pkg, tag, PackageError)

    gen_index = pkg.exports.index(gen) + 1
    overrides = set()
    for e in pkg.exports:
        cls = pkg.class_of(e)
        if cls == "K2Node_CustomEvent":
            nm = _custom_event_name(pkg, e, PackageError)
            if nm:
                body["customEvents"].append(nm)
        elif cls == "Function" and e.outer_index == gen_index and e.super_index != 0:
            if pkg.resolve(e.super_index)[0] == e.object_name:
                overrides.add(e.object_name)
    body["overrides"] = sorted(overrides)
    rec.update(body)


def export_blueprints(root):
    from _uasset import PackageError
    scanned, unparsable, headers = 0, [], {}
    for path in _content_files(root, (".uasset",)):
        scanned += 1
        try:
            handle, rec = _blueprint_header(path)
        except PackageError as ex:
            unparsable.append({"path": _rel(path, root), "why": str(ex)})
            continue
        except Exception as ex:                                   # noqa: BLE001 - must not be silent
            unparsable.append({"path": _rel(path, root), "why": "%s: %s" % (type(ex).__name__, ex)})
            continue
        if rec is None:
            continue
        rec["path"] = _rel(path, root)
        headers[os.path.basename(path)[:-len(".uasset")]] = (handle, rec)

    # Walk each Blueprint up through its `_C` parents until a script class is reached. Only those
    # carry a body in the file.
    blueprints = {}
    for stem in sorted(headers):
        handle, rec = headers[stem]
        seen, cur, hops = set(), stem, 0
        rec["scriptParent"], rec["hops"] = None, None
        while cur in headers and cur not in seen:
            seen.add(cur)
            parent_rec = headers[cur][1]
            if parent_rec["parentIsScript"]:
                rec["scriptParent"], rec["hops"] = parent_rec["parent"], hops
                break
            parent = parent_rec["parent"]
            if parent and parent.endswith("_C"):
                cur, hops = parent[:-2], hops + 1
            else:
                break
        rec["bodyError"] = None
        if rec["scriptParent"] is not None:
            pkg, gen = handle
            try:
                _blueprint_body(pkg, gen, rec)
            except PackageError as ex:
                rec["bodyError"] = str(ex)
            except Exception as ex:                               # noqa: BLE001
                rec["bodyError"] = "%s: %s" % (type(ex).__name__, ex)
        blueprints[stem] = rec
    return {"assetsScanned": scanned, "unparsable": unparsable, "blueprints": blueprints}


# =================================================================================================
# components
# =================================================================================================
def _scan_components(path):
    """Every component-bearing fact in one package. Raises on anything it cannot decode."""
    from _uasset import Package, _fname
    pkg = Package(path)
    pkg.load_payload()
    rec = {"classes": [], "scs": {}, "addComponent": [], "handlers": [], "instanced": []}

    def obj(tag):
        return struct.unpack_from("<i", pkg.data, tag.value_off)[0]

    scs_owner = {}                                   # SimpleConstructionScript export idx -> class
    for i, e in enumerate(pkg.exports):
        cls = pkg.class_of(e) or ""
        if cls.endswith("BlueprintGeneratedClass"):
            rec["classes"].append([e.object_name, pkg.resolve(e.super_index)[0] or ""])
        elif cls == "SimpleConstructionScript":
            scs_owner[i + 1] = pkg.resolve(e.outer_index)[0] or ""
        elif cls == "InheritableComponentHandler":
            rec["handlers"].append(pkg.resolve(e.outer_index)[0] or "")

    is_map = path.endswith(".umap")
    for e in pkg.exports:
        cls = pkg.class_of(e) or ""
        if cls == "SCS_Node":
            owner = scs_owner.get(e.outer_index)
            if owner is None:
                # A node whose construction script is not in this package is not a live one.
                continue
            tags = {t.name: t for t in pkg.script_tags(e)}
            ccls = pkg.resolve(obj(tags["ComponentClass"]))[0] if "ComponentClass" in tags else ""
            var = (_fname(pkg.data, tags["InternalVariableName"].value_off, pkg.names)
                   if "InternalVariableName" in tags else "")
            rec["scs"].setdefault(owner, []).append([var, ccls or "", "scs:" + e.object_name])
        elif cls.startswith("K2Node_AddComponent"):
            tags = {t.name: t for t in pkg.script_tags(e)}
            tmpl = (pkg.resolve(obj(tags["TemplateType"]))[0] or "") if "TemplateType" in tags else ""
            rec["addComponent"].append([e.object_name, tmpl])
        elif is_map and cls.endswith("Component"):
            outer, ocls, _imp = pkg.resolve(e.outer_index)
            if outer and ocls and str(ocls).endswith("_C"):
                rec["instanced"].append([outer, e.object_name, cls])
    return rec


def export_components(root):
    from _uasset import PackageError
    packages, dropped = {}, []
    for path in _content_files(root, (".uasset", ".umap")):
        rel = _rel(path, root)
        try:
            packages[rel] = _scan_components(path)
        except (PackageError, struct.error, KeyError, IndexError, ValueError) as ex:
            dropped.append({"path": rel, "why": str(ex)})
    return {"packages": packages, "dropped": dropped}


# =================================================================================================
# references
# =================================================================================================
def export_references(root):
    import _t3d_dir
    best = {}
    for p in glob.glob(os.path.join(_t3d_dir.T3D_DIR, "Text*.txt")):
        m = re.match(r"(.+)-(\d+)$", os.path.basename(p)[4:-4])
        if not m:
            continue
        a, n = m.group(1), int(m.group(2))
        if a not in best or n > best[a][0]:
            best[a] = (n, p)
    member = collections.Counter()
    member_assets = collections.defaultdict(set)
    graphs = collections.defaultdict(set)
    events = collections.defaultdict(set)
    mn = re.compile(r'MemberName="([^"]+)"')
    gn = re.compile(r'Begin Object Class=/Script/Engine\.EdGraph Name="([^"]+)"')
    ev = re.compile(r'CustomFunctionName="([^"]+)"')
    for a, (_n, p) in best.items():
        with open(p, encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
        for name in set(mn.findall(txt)):
            member[name] += txt.count('MemberName="%s"' % name)
            member_assets[name].add(a)
        for name in set(gn.findall(txt)):
            graphs[name].add(a)
        for name in set(ev.findall(txt)):
            events[name].add(a)
    return {
        "assets": sorted(best),
        "memberNames": {k: {"count": member[k], "assets": sorted(member_assets[k])}
                        for k in sorted(member)},
        "functionGraphs": {k: sorted(v) for k, v in sorted(graphs.items())},
        "customEvents": {k: sorted(v) for k, v in sorted(events.items())},
    }


EXPORTERS = {
    "packages": export_packages,
    "stringtables": export_stringtables,
    "variables": export_variables,
    "structs": export_structs,
    "blueprints": export_blueprints,
    "components": export_components,
    "references": export_references,
}


def write(kind, data, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    doc = {"kind": kind, "schema": SCHEMA, "generatedBy": "blueprint_export.py " + kind,
           "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    doc.update(data)
    path = os.path.join(out_dir, kind + ".json")
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=1, sort_keys=False, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 epilog="kinds: " + ", ".join(KINDS) + ", all")
    ap.add_argument("kind", choices=KINDS + ("all",))
    ap.add_argument("--project", help="the project directory (see packs/PACKS.md)")
    ap.add_argument("--out-dir", help="the export directory (default: exportDir of "
                                      ".octopus/blueprint.json, else .octopus/%s)" % DEFAULT_EXPORT_DIR)
    args = ap.parse_args(argv)
    root = str(_project.project_root())
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else export_dir()
    kinds = KINDS if args.kind == "all" else (args.kind,)
    failed = 0
    for kind in kinds:
        try:
            data = EXPORTERS[kind](root)
        except Exception as exc:                                  # noqa: BLE001 - one line per kind
            print("%-12s FAILED: %s: %s" % (kind, type(exc).__name__, exc))
            failed += 1
            continue
        print("%-12s %s" % (kind, write(kind, data, out_dir)))
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
