"""Authored designer values of a WidgetBlueprint, read off disk with no editor.

WHY THIS EXISTS
---------------
Reparenting a WidgetBlueprint keeps its `WidgetTree`, but the values a designer typed are spread
across three places and only one of them survives every migration step:

    the widget tree      brushes, textures, fonts, sizes, colours - survives a reparent
    the CDO              defaults of the Blueprint's OWN variables - DESTROYED with the variable
    the graph            values a function writes at runtime - read them from the graph export

`remove_variable` deletes a child variable and its authored default with it, and nothing else in the
project records that default. This prints all three so the value can be re-entered on the parent.

Values are decoded from the tagged-property stream, so an unrecognised type prints its type and byte
length rather than a guess. A property this cannot decode is listed under UNDECODED, never omitted.

    python tools/widget_designer_values.py Content/UI/WBP_ExampleWidget.uasset
    python tools/widget_designer_values.py --tree <asset>      # widget tree only
    python tools/widget_designer_values.py --md <asset>        # markdown, for a Docs/ page
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project
from _uasset import Package, PackageError

# Struct types with a native serializer: their bytes are not a tagged stream, so they are decoded
# field by field above, or reported by size.
NATIVE_STRUCTS = {
    "Vector2D", "Vector", "Vector4", "Rotator", "Quat", "LinearColor", "Color",
    "IntPoint", "Guid", "DateTime", "Transform", "Box2D", "GameplayTag",
    "GameplayTagContainer",
}


def _fname_at(pkg, off):
    idx, num = struct.unpack_from("<ii", pkg.data, off)
    if not 0 <= idx < len(pkg.names):
        raise PackageError("name index %d out of table" % idx)
    return pkg.names[idx] + ("_%d" % (num - 1) if num else "")


def _obj_ref(pkg, index):
    """FPackageIndex -> a readable path, the way a designer would recognise it."""
    if index == 0:
        return "None"
    try:
        if index < 0:
            # The import map carries no outer chain, so the asset is named without its folder.
            # `class_name` disambiguates two assets that share a short name.
            im = pkg.imports[-index - 1]
            return "%s (%s)" % (im.object_name, im.class_name)
        return pkg.exports[index - 1].object_name
    except (IndexError, PackageError):
        return "<index %d>" % index


def decode(pkg, tag, depth=0):
    """One tagged property -> a printable value, or None when the type is not decodable here."""
    d, off, t = pkg.data, tag.value_off, tag.type
    try:
        if t == "BoolProperty":
            return "true" if (tag.flags & 0x02) else "false"
        if t in ("ByteProperty", "EnumProperty"):
            return str(d[off]) if tag.size == 1 else _fname_at(pkg, off)
        if t == "IntProperty":
            return str(struct.unpack_from("<i", d, off)[0])
        if t == "Int64Property":
            return str(struct.unpack_from("<q", d, off)[0])
        if t == "FloatProperty":
            return "%g" % struct.unpack_from("<f", d, off)[0]
        if t == "DoubleProperty":
            return "%g" % struct.unpack_from("<d", d, off)[0]
        if t == "NameProperty":
            return _fname_at(pkg, off)
        if t == "StrProperty":
            n = struct.unpack_from("<i", d, off)[0]
            if n == 0:
                return '""'
            if n > 0:
                return '"%s"' % d[off + 4:off + 4 + n - 1].decode("utf-8", "replace")
            return '"%s"' % d[off + 4:off + 4 + (-n * 2) - 2].decode("utf-16-le", "replace")
        if t in ("ObjectProperty", "ClassProperty"):
            return _obj_ref(pkg, struct.unpack_from("<i", d, off)[0])
        if t in ("SoftObjectProperty", "SoftClassProperty"):
            return None
        if t == "StructProperty":
            sname = tag.type_nodes[1] if len(tag.type_nodes) > 1 else "?"
            if sname == "LinearColor" and tag.size >= 16:
                return "(R=%g G=%g B=%g A=%g)" % struct.unpack_from("<4f", d, off)
            if sname == "Color" and tag.size >= 4:
                b, g, r, a = d[off], d[off + 1], d[off + 2], d[off + 3]
                return "(R=%d G=%d B=%d A=%d)" % (r, g, b, a)
            # A brush's ImageSize is 8 bytes of float or 16 of double, and a resave renames the type
            # from Vector2D to DeprecateSlateVector2D without touching the numbers. Decode both
            # spellings, or that rename reads as a lost designer value.
            if sname in ("Vector2D", "DeprecateSlateVector2D"):
                if tag.size == 8:
                    return "(X=%g Y=%g)" % struct.unpack_from("<2f", d, off)
                if tag.size >= 16:
                    return "(X=%g Y=%g)" % struct.unpack_from("<2d", d, off)
            if sname in NATIVE_STRUCTS:
                return "<%s, %d bytes>" % (sname, tag.size)
            if depth >= 3:
                return "<%s, %d bytes>" % (sname, tag.size)
            try:
                inner, _e, terminated = pkg.walk(off, off + tag.size)
            except (PackageError, struct.error):
                return "<%s, %d bytes>" % (sname, tag.size)
            if not terminated:
                return "<%s, %d bytes>" % (sname, tag.size)
            parts = []
            for it in inner:
                v = decode(pkg, it, depth + 1)
                parts.append("%s=%s" % (it.name, v if v is not None else "?"))
            return "%s(%s)" % (sname, ", ".join(parts)) if parts else "%s()" % sname
    except (struct.error, PackageError, UnicodeError):
        return None
    return None


def parent_class(pkg, asset):
    """The generated class's super, which is what a reparent changes."""
    want = asset + "_C"
    for e in pkg.exports:
        if e.object_name == want and pkg.class_of(e) == "WidgetBlueprintGeneratedClass":
            return _obj_ref(pkg, e.super_index)
    for e in pkg.exports:
        if e.object_name == want:
            return _obj_ref(pkg, e.super_index)
    return "<no generated class export>"


# Exports that carry authored appearance. A slot class holds the layout a designer dragged out.
VISUAL_CLASSES = (
    "Image", "TextBlock", "RichTextBlock", "Border", "SizeBox", "Button", "ProgressBar",
    "CheckBox", "Slider", "Spacer", "Overlay", "ScaleBox", "CanvasPanelSlot", "BorderSlot",
    "SizeBoxSlot", "OverlaySlot", "HorizontalBoxSlot", "VerticalBoxSlot", "ScaleBoxSlot",
    "UniformGridSlot", "WidgetSwitcherSlot", "GridSlot", "BackgroundBlur", "NamedSlot",
    "EditableTextBox", "MultiLineEditableTextBox", "WidgetSwitcher", "Throbber", "InvalidationBox",
)


def dump(path, tree_only=False, markdown=False):
    asset = os.path.splitext(os.path.basename(path))[0]
    pkg = Package(path)
    pkg.load_payload()

    h1, h2 = ("## ", "### ") if markdown else ("== ", "-- ")
    out = ["%s%s" % (h1, asset), "",
           "- **asset:** `%s`" % path.replace("\\", "/"),
           "- **parent class:** `%s`" % parent_class(pkg, asset), ""]

    cdo_name = "Default__%s_C" % asset
    groups = []
    undecoded = []

    for e in pkg.exports:
        cls = pkg.class_of(e) or ""
        is_cdo = e.object_name == cdo_name
        if tree_only and is_cdo:
            continue
        if not is_cdo and cls not in VISUAL_CLASSES:
            continue
        try:
            tags = pkg.script_tags(e)
        except (PackageError, struct.error):
            continue
        rows = []
        for tg in tags:
            v = decode(pkg, tg)
            if v is None:
                undecoded.append((e.object_name, tg.name, tg.type, tg.size))
            else:
                rows.append((tg.name, v))
        if rows:
            label = ("CDO - the Blueprint's own variable defaults" if is_cdo
                     else "%s (%s)" % (e.object_name, cls))
            groups.append((label, rows))

    # An export can appear twice in a package; keep one of each identical heading.
    seen = set()
    for label, rows in groups:
        key = (label, tuple(rows))
        if key in seen:
            continue
        seen.add(key)
        out += ["%s%s" % (h2, label), ""]
        if markdown:
            out += ["| property | authored value |", "|---|---|"]
            out += ["| `%s` | `%s` |" % (k, v.replace("|", "\\|")) for k, v in rows]
        else:
            out += ["    %-34s %s" % (k, v) for k, v in rows]
        out.append("")

    if undecoded:
        out += ["%sUNDECODED - read these live before removing them" % h2, ""]
        seen_u = set()
        for owner, prop, typ, size in undecoded:
            if (owner, prop) in seen_u:
                continue
            seen_u.add((owner, prop))
            out.append("    %-24s %-28s %-18s %d bytes" % (owner, prop, typ, size))
        out.append("")
    return "\n".join(out)


def main():
    _project.optional_project_root()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("assets", nargs="+", help=".uasset paths")
    ap.add_argument("--tree", action="store_true", help="widget tree only, skip the CDO")
    ap.add_argument("--md", action="store_true", help="markdown tables, for a Docs/ page")
    a = ap.parse_args()
    for p in a.assets:
        try:
            print(dump(p, a.tree, a.md))
        except (PackageError, struct.error, IOError) as ex:
            print("%s: FAILED - %s" % (p, ex))
    return 0


if __name__ == "__main__":
    sys.exit(main())
