"""Offline `.uasset` package-header reader: name table, imports, exports, tagged properties.

The census tools of this pack read `.uasset` bytes through this module. It parses the package by
the summary's own offsets, the way the engine does. It does not look for the longest plausible run
of bytes.

WHY IT PARSES RATHER THAN SCANS
-------------------------------
"Is this Blueprint parented to an AngelScript class" cannot be answered by scanning bytes for
`/Script/Angelscript`: a Blueprint that merely REFERENCES a script class imports the same name. The
parent is `SuperIndex` on the `<Asset>_C` export and nothing else in the file distinguishes it.

Same for graphs. `FunctionGraphs` / `MacroGraphs` / `UbergraphPages` / `DelegateSignatureGraphs` on the
`UBlueprint` export are the authored lists. A node-class heuristic over `EdGraph` exports gets macros
and ubergraph pages backwards: every macro and every ubergraph page is an `EdGraph` export, and
the only difference between them is what it contains.

The layouts come from the engine source:

    PackageFileSummary.cpp   summary field order and the version gates
    ObjectResource.cpp       FObjectImport / FObjectExport
    PropertyTag.cpp          FPropertyTag, UE5 PROPERTY_TAG_COMPLETE_TYPE_NAME form
    PropertyTypeName.cpp     FPropertyTypeName is a NODE LIST, not one FName
    EdGraphPin.cpp           FEdGraphPinType has a NATIVE serializer - its bytes are not tagged

EVERY FAILURE RAISES `PackageError`. Nothing here returns a degraded result, because a degraded parse
of a Blueprint reads downstream as "that asset has no collisions".
"""
import struct

PACKAGE_FILE_TAG = 0x9E2A83C1

# EUnrealEngineObjectUE5Version (ObjectVersion.h). Only the gates this parser needs.
UE5_OPTIONAL_RESOURCES = 1003
UE5_ADD_SOFTOBJECTPATH_LIST = 1008
UE5_METADATA_SERIALIZATION_OFFSET = 1014
UE5_VERSE_CELLS = 1015
UE5_PACKAGE_SAVED_HASH = 1016

# EUnrealEngineObjectUE4Version. Derived by enumerating ObjectVersion.h rather than remembered; the
# enumeration is right iff the last entry is 522, which is what current UE5 assets carry.
UE4_ARRAY_PROPERTY_INNER_TAGS = 282
UE4_STRUCT_GUID_IN_PROPERTY_TAG = 441
UE4_SERIALIZE_TEXT_IN_PACKAGES = 459
UE4_PROPERTY_GUID_IN_PROPERTY_TAG = 503
UE4_NAME_HASHES_SERIALIZED = 504
UE4_PROPERTY_TAG_SET_MAP_SUPPORT = 509
UE4_ADDED_PACKAGE_SUMMARY_LOCALIZATION_ID = 516
UE5_PROPERTY_TAG_EXTENSION = 1011
UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME = 1012

PKG_FILTER_EDITOR_ONLY = 0x80000000

# EPropertyTagFlags (PropertyTag.h)
TAG_HAS_ARRAY_INDEX = 0x01
TAG_HAS_PROPERTY_GUID = 0x02
TAG_HAS_EXTENSIONS = 0x04

# EPinContainerType (EdGraphPinType.h)
CONTAINER_NAMES = {0: "", 1: "TArray", 2: "TSet", 3: "TMap"}


class PackageError(Exception):
    pass


class _R(object):
    """Little-endian cursor. Every read is bounds-checked by struct itself."""

    def __init__(self, data, off=0):
        self.d = data
        self.o = off

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.o)[0]
        self.o += 4
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.o)[0]
        self.o += 4
        return v

    def skip(self, n):
        self.o += n

    def fstring(self):
        n = self.i32()
        if n == 0:
            return ""
        if n > 0:
            b = self.d[self.o:self.o + n]
            self.o += n
            return b[:-1].decode("utf-8", "replace")
        n = -n
        b = self.d[self.o:self.o + n * 2]
        self.o += n * 2
        return b[:-2].decode("utf-16-le", "replace")


def _skip_custom_versions(r, legacy):
    """FCustomVersionContainer. THREE on-disk formats; -5..-3 is not the modern one."""
    n = r.i32()
    if n < 0 or n > 4096:
        raise PackageError("implausible custom-version count %d" % n)
    if legacy <= -6:                              # Optimized: FGuid + int32
        r.skip(n * 20)
    elif legacy <= -3:                            # Guids: FGuid + int32 + FString FriendlyName
        for _ in range(n):
            r.skip(20)
            r.fstring()
    else:                                         # Enums: int32 tag + int32 version
        r.skip(n * 8)


def read_summary(data):
    """FPackageFileSummary, as far as DependsOffset."""
    try:
        r = _R(data)
        if r.u32() != PACKAGE_FILE_TAG:
            raise PackageError("not a package (bad tag)")
        legacy = r.i32()
        if not -9 <= legacy < 0:
            raise PackageError("unsupported LegacyFileVersion %d" % legacy)
        if legacy != -4:
            r.i32()                               # LegacyUE3Version
        ue4 = r.i32()
        ue5 = r.i32() if legacy <= -8 else 0
        r.i32()                                   # FileVersionLicenseeUE
        s = {"legacy": legacy, "ue4": ue4, "ue5": ue5}
        if ue5 >= UE5_PACKAGE_SAVED_HASH:
            r.skip(20)                            # FIoHash SavedHash (IoHash.h: uint8[20])
            s["TotalHeaderSize"] = r.i32()
        if legacy <= -2:
            _skip_custom_versions(r, legacy)
        if ue5 < UE5_PACKAGE_SAVED_HASH:
            s["TotalHeaderSize"] = r.i32()
        s["PackageName"] = r.fstring()
        s["PackageFlags"] = r.u32()
        s["NameCount"] = r.i32()
        s["NameOffset"] = r.i32()
        if ue5 >= UE5_ADD_SOFTOBJECTPATH_LIST:
            r.i32()
            r.i32()                               # SoftObjectPaths count/offset
        if not (s["PackageFlags"] & PKG_FILTER_EDITOR_ONLY) \
                and ue4 >= UE4_ADDED_PACKAGE_SUMMARY_LOCALIZATION_ID:
            r.fstring()                           # LocalizationId
        if ue4 >= UE4_SERIALIZE_TEXT_IN_PACKAGES:
            r.i32()
            r.i32()                               # GatherableTextData count/offset
        s["ExportCount"] = r.i32()
        s["ExportOffset"] = r.i32()
        s["ImportCount"] = r.i32()
        s["ImportOffset"] = r.i32()
        if ue5 >= UE5_VERSE_CELLS:
            r.skip(16)                            # CellExport / CellImport count+offset
        if ue5 >= UE5_METADATA_SERIALIZATION_OFFSET:
            r.i32()                               # MetaDataOffset
        s["DependsOffset"] = r.i32()
        return s
    except struct.error as ex:
        raise PackageError("summary ran off the end: %s" % ex)


def read_names(data, s):
    """The FName table. Entry = int32 len (negative = UTF-16) + chars + NUL [+ 4 bytes of hash]."""
    o = s["NameOffset"]
    hashed = s["ue4"] >= UE4_NAME_HASHES_SERIALIZED
    out = []
    try:
        for _ in range(s["NameCount"]):
            n = struct.unpack_from("<i", data, o)[0]
            o += 4
            if abs(n) > 1024:
                raise PackageError("implausible name length %d" % n)
            if n >= 0:
                out.append(data[o:o + n - 1].decode("utf-8", "replace"))
                o += n
            else:
                n = -n
                out.append(data[o:o + (n - 1) * 2].decode("utf-16-le", "replace"))
                o += n * 2
            if hashed:
                o += 4
    except struct.error as ex:
        raise PackageError("name table ran off the end: %s" % ex)
    return out


def _fname(data, off, names):
    idx, num = struct.unpack_from("<ii", data, off)
    if not 0 <= idx < len(names):
        raise PackageError("FName index %d outside a %d-entry table" % (idx, len(names)))
    return names[idx] + ("_%d" % (num - 1) if num else "")


class Import(object):
    __slots__ = ("class_package", "class_name", "object_name", "package_name")

    def __init__(self, cp, cn, on, pn):
        self.class_package = cp
        self.class_name = cn
        self.object_name = on
        self.package_name = pn


class Export(object):
    __slots__ = ("class_index", "super_index", "outer_index", "object_name",
                 "serial_size", "serial_offset", "script_start", "script_end")


# ClassPackage(8) + ClassName(8) + OuterIndex(4) + ObjectName(8), then two version-gated fields.
# `PackageName` did not exist before VER_UE4_NON_OUTER_PACKAGE_IMPORT. An asset saved at ue4=518 is
# one version below it, and a reader that assumes the field misreads every import of that asset.
IMPORT_STRIDE_BASE = 28
UE4_NON_OUTER_PACKAGE_IMPORT = 519


def read_imports(data, s, names):
    has_package_name = s["ue4"] >= UE4_NON_OUTER_PACKAGE_IMPORT
    stride = (IMPORT_STRIDE_BASE + (8 if has_package_name else 0)
              + (4 if s["ue5"] >= UE5_OPTIONAL_RESOURCES else 0))
    # Cross-check against the table's own extent. The export map is written straight after the
    # import map, so a stride derived from the version gates and one measured from the offsets must
    # agree; when they do not, the gates are wrong and every FName read after this point is garbage.
    n = s["ImportCount"]
    span = s["ExportOffset"] - s["ImportOffset"]
    if n and span > 0 and span % n == 0 and span // n != stride:
        raise PackageError("import stride %d from version gates, %d from the table extent"
                           % (stride, span // n))
    o = s["ImportOffset"]
    out = []
    try:
        for _ in range(s["ImportCount"]):
            out.append(Import(_fname(data, o, names),
                              _fname(data, o + 8, names),
                              _fname(data, o + 20, names),
                              _fname(data, o + 28, names) if has_package_name else ""))
            o += stride
    except struct.error as ex:
        raise PackageError("import table ran off the end: %s" % ex)
    return out


def read_exports(data, s, names):
    """The export map.

    The entry is fixed-size per package version, but its size turns on five separate version gates.
    Rather than reproduce all five, take the stride from the map's own extent - DependsOffset is
    written immediately after the export map - and reject anything implausible. A stride wrong by one
    field turns every ObjectName into garbage, and garbage can read as a clean asset.
    """
    n = s["ExportCount"]
    if n == 0:
        return []
    span = s["DependsOffset"] - s["ExportOffset"]
    if span <= 0 or span % n:
        raise PackageError("export map extent %d does not divide %d entries" % (span, n))
    stride = span // n
    if not 56 <= stride <= 176:
        raise PackageError("implausible export stride %d" % stride)
    out = []
    o = s["ExportOffset"]
    try:
        for _ in range(n):
            e = Export()
            e.class_index, e.super_index, _tmpl, e.outer_index = struct.unpack_from("<iiii", data, o)
            e.object_name = _fname(data, o + 16, names)
            e.serial_size, e.serial_offset = struct.unpack_from("<qq", data, o + 28)
            # ScriptSerializationStart/End are the LAST two fields and only exist on versioned UE5
            # packages. They are RELATIVE to serial_offset. Read them off the tail so the gate does
            # not have to be reproduced.
            if stride >= 112:
                e.script_start, e.script_end = struct.unpack_from("<qq", data, o + stride - 16)
            else:
                e.script_start, e.script_end = 0, e.serial_size
            out.append(e)
            o += stride
    except struct.error as ex:
        raise PackageError("export map ran off the end: %s" % ex)
    return out


class Tag(object):
    __slots__ = ("name", "type_nodes", "size", "flags", "value_off", "next_off")

    def __init__(self, name, type_nodes, size, flags, value_off, next_off):
        self.name = name
        self.type_nodes = type_nodes
        self.size = size
        self.flags = flags
        self.value_off = value_off
        self.next_off = next_off

    @property
    def type(self):
        return self.type_nodes[0] if self.type_nodes else ""


def walk_tags(data, p, end, names, ue4=522, ue5=UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME):
    """FPropertyTag stream -> ([Tag], offset just past the terminator, terminated?).

    TWO on-disk forms, and one project can hold both. UE5 packages at or past
    PROPERTY_TAG_COMPLETE_TYPE_NAME write an FPropertyTypeName node tree and a flags byte; older ones
    (for example ue4=518 / ue5=0) write a single FName type, an ArrayIndex, per-type extras, and a
    HasPropertyGuid byte. Reading the old form with the new reader misaligns after the first tag and
    then reports whatever byte lands on the flags position, as false "property tag extensions".
    """
    complete_type = ue5 >= UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME
    out = []
    while p + 8 <= end:
        idx, num = struct.unpack_from("<ii", data, p)
        if not 0 <= idx < len(names):
            raise PackageError("property tag name index %d outside the table at %d" % (idx, p))
        if names[idx] == "None":
            return out, p + 8, True
        name = names[idx] + ("_%d" % (num - 1) if num else "")
        q = p + 8
        if complete_type:
            # FPropertyTypeName is a node LIST: {FName, int32 InnerCount} until the tree closes.
            remaining = 1
            nodes = []
            while remaining > 0:
                ti, _tn, inner = struct.unpack_from("<iii", data, q)
                q += 12
                if not 0 <= ti < len(names):
                    raise PackageError("property type index %d outside the table at %d" % (ti, q))
                nodes.append(names[ti])
                remaining += inner - 1
            size = struct.unpack_from("<i", data, q)[0]
            q += 4
            flags = data[q]
            q += 1
            if flags & TAG_HAS_ARRAY_INDEX:
                q += 4
            if flags & TAG_HAS_PROPERTY_GUID:
                q += 16
            if flags & TAG_HAS_EXTENSIONS:
                raise PackageError("property tag extensions are not decoded (tag %s)" % name)
        else:
            ti = struct.unpack_from("<i", data, q)[0]
            if not 0 <= ti < len(names):
                raise PackageError("property type index %d outside the table at %d" % (ti, q))
            type_name = names[ti]
            nodes = [type_name]
            q += 8
            size = struct.unpack_from("<i", data, q)[0]
            q += 8                                          # Size, ArrayIndex
            flags = 0
            q = _skip_legacy_type_extras(data, q, names, type_name, nodes, ue4, ue5)
            if ue4 >= UE4_PROPERTY_GUID_IN_PROPERTY_TAG:
                has_guid = data[q]
                q += 1
                if has_guid:
                    q += 16
            if ue5 >= UE5_PROPERTY_TAG_EXTENSION:
                ext = data[q]
                q += 1
                if ext:
                    raise PackageError("property tag extensions are not decoded (tag %s)" % name)
        if size < 0:
            raise PackageError("negative tag size on %s" % name)
        out.append(Tag(name, nodes, size, flags, q, q + size))
        p = q + size
    return out, p, False


def _skip_legacy_type_extras(data, q, names, type_name, nodes, ue4, ue5):
    """The per-type fields the pre-PROPERTY_TAG_COMPLETE_TYPE_NAME tag carries after ArrayIndex."""
    def fname():
        nonlocal q
        idx = struct.unpack_from("<i", data, q)[0]
        q += 8
        if not 0 <= idx < len(names):
            raise PackageError("legacy tag inner-type index %d outside the table" % idx)
        return names[idx]

    if type_name == "StructProperty":
        nodes.append(fname())
        if ue4 >= UE4_STRUCT_GUID_IN_PROPERTY_TAG:
            q += 16
    elif type_name == "BoolProperty":
        q += 1
    elif type_name in ("ByteProperty", "EnumProperty"):
        nodes.append(fname())
    elif type_name == "ArrayProperty":
        if ue4 >= UE4_ARRAY_PROPERTY_INNER_TAGS:
            nodes.append(fname())
    elif type_name == "OptionalProperty":
        nodes.append(fname())
    elif ue4 >= UE4_PROPERTY_TAG_SET_MAP_SUPPORT:
        if type_name == "SetProperty":
            nodes.append(fname())
        elif type_name == "MapProperty":
            nodes.append(fname())
            nodes.append(fname())
    return q


class Package(object):
    """A parsed package. The header is read eagerly; export payloads on demand."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as h:
            head = h.read(8192)
            self.summary = read_summary(head)
            need = max(self.summary.get("TotalHeaderSize", 0),
                       self.summary["DependsOffset"], self.summary["NameOffset"],
                       self.summary["ExportOffset"], self.summary["ImportOffset"]) + 64
            if need > len(head):
                h.seek(0)
                head = h.read(need)
        self.data = head
        self.names = read_names(self.data, self.summary)
        self.imports = read_imports(self.data, self.summary, self.names)
        self.exports = read_exports(self.data, self.summary, self.names)
        self._payload_loaded = False

    def resolve(self, index):
        """FPackageIndex -> (object name, class name, is_import). 0 is null."""
        if index < 0:
            if -index - 1 >= len(self.imports):
                raise PackageError("import index %d out of range" % index)
            im = self.imports[-index - 1]
            return im.object_name, im.class_name, True
        if index > 0:
            if index - 1 >= len(self.exports):
                raise PackageError("export index %d out of range" % index)
            e = self.exports[index - 1]
            return e.object_name, self.class_of(e), False
        return None, None, False

    def class_of(self, e):
        return self.resolve(e.class_index)[0]

    def import_of(self, index):
        return self.imports[-index - 1] if index < 0 else None

    def load_payload(self):
        """Read the whole file. Needed only for NewVariables and custom-event names."""
        if self._payload_loaded:
            return
        with open(self.path, "rb") as h:
            self.data = h.read()
        self._payload_loaded = True

    def script_tags(self, e):
        """Tagged properties of one export.

        The tagged stream starts ONE byte past `ScriptSerializationStartOffset` on a UE5 package. The walk is bounded by `ScriptSerializationEndOffset`, so a wrong start does not
        run away - it raises on the first bad name index.
        """
        self.load_payload()
        base = e.serial_offset + e.script_start
        end = e.serial_offset + e.script_end
        # The tagged stream starts AT `ScriptSerializationStartOffset` on pre-UE5 packages and ONE
        # byte past it on UE5 ones. Rather than gate on a version nobody has written down, try both
        # and keep the one that walks to a `None` terminator. A wrong start does not run away - the
        # walk is bounded by `ScriptSerializationEndOffset` and raises on the first bad name index.
        first = None
        for start in (base, base + 1):
            try:
                tags, _end, terminated = self.walk(start, end)
            except (PackageError, struct.error) as ex:
                first = first or ex
                continue
            if terminated and tags:
                return tags
        raise PackageError("no tagged-property stream at %d or %d (%s)" % (base, base + 1, first))

    def walk(self, p, end):
        return walk_tags(self.data, p, end, self.names,
                         self.summary["ue4"], self.summary["ue5"])
