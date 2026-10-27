# -*- coding: utf-8 -*-
"""Offline census of DETACHED EXEC ISLANDS in every Blueprint graph under `Content/`.

An island is a region of a graph that no execution can reach: the nodes are intact, the asset
compiles, no error or warning names them, and the behaviour simply never runs. A compile, an error
census and a collision census cannot see one.

HOW IT READS THE GRAPH -- from the bytes, no editor
---------------------------------------------------
`UEdGraphNode::Serialize` writes its pins straight after the tagged-property stream, through
`UEdGraphPin::SerializeAsOwningNode` (EdGraphNode.cpp:260). So a node's pins -- names, categories,
directions, and the `LinkedTo` node+GUID pairs that ARE the wires -- sit at `serial_offset +
ScriptSerializationEndOffset`, and `tools/_uasset.py` already parses everything up to that point.

The layouts come from the engine source:

    EdGraphPin.cpp:1838   UEdGraphPin::Serialize        field order
    EdGraphPin.cpp:2063   SerializePinArray/SerializePin   each entry is bNullPtr + owner + guid,
                                                           THEN the pin body (which repeats both)
    EdGraphPin.cpp:163    FEdGraphPinType::Serialize    native, untagged
    Text.cpp:893          FText::SerializeText          Flags, int8 history type, then the history
    TextHistory.cpp:810   FTextHistory_Base             namespace, key, source, AND DevNotes

WHY THE PARSE IS STRICT
-----------------------
Every pin walk must land EXACTLY on the export's serial end and every pin must name its own export as
its owner. A pin stream read one field short still yields plausible-looking node links, and a wrong
link set reads downstream as "that graph has no island". A package that will not parse is NAMED
and counted, and the run exits non-zero.

REACHABILITY
------------
Forward over exec output pins from every genuine entry point (events, input events, function entry
nodes, timelines, and the tunnel entry of any macro or collapsed graph that has a reachable
instance), then BACKWARD over data input pins, because a pure node feeding a reachable node runs.
Macro instances are followed by OBJECT POINTER -- a `K2Node_MacroInstance` never names its macro, so
no name-based scan can see a macro call site.

Whatever is left is partitioned into connected components. That is deliberately conservative: two
island fragments joined by a single data wire are reported as one island, and a fragment wired to a
live node's data input is not reported at all.

    python tools/exec_islands.py                       # whole project, ranked
    python tools/exec_islands.py --asset <path.uasset> # one package, every island listed
    python tools/exec_islands.py --min 3               # islands of at least N nodes (default 2)
    python tools/exec_islands.py --self-test           # plant a known island, refuse to report if missed
"""
from __future__ import print_function

import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project                                                          # noqa: E402
from _uasset import Package, PackageError, PACKAGE_FILE_TAG, _R          # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

# Custom-version GUIDs, rendered as the four little-endian uint32s the file stores.
# DevObjectVersion.cpp / EngineVersion.cpp.
CV_FRAMEWORK = "cffc743f43b04480939114df171d2073"
CV_EDITOR = "e4b068edf49442e9a231da0b2e46bb41"
CV_RELEASE = "9c54d522a8264fbe9421074661b482d0"
CV_UE5MAIN = "697dd581e64f41abaa4a51ecbeb7b628"
CV_UE5REL = "d89b5e4224bd4d468412aca8df641779"
CV_BLUEPRINT = "b0d832e41f894f0daccf7eb736fd4aa2"
CV_FORTNITE = "601d1886ac644f84aa16d3de0deac7d6"

V_PINS_STORE_FNAME = 31                 # FFrameworkObjectVersion
V_PIN_CONTAINER_TYPE = 26               # FFrameworkObjectVersion
V_CULTURE_INVARIANT_KEY = 32            # FEditorObjectVersion
V_ARG_DATA_IS_VARIANT = 23              # FEditorObjectVersion
V_UOBJECT_WRAPPER_FLAG = 32             # FReleaseObjectVersion
V_PIN_SOURCE_INDEX = 50                 # FUE5MainStreamObjectVersion
V_SINGLE_PRECISION_PIN = 36             # FUE5ReleaseStreamObjectVersion
V_ARG_DATA_64BIT = 12                   # FUE5ReleaseStreamObjectVersion
V_ADVANCED_CONTAINER = 6                # FBlueprintsObjectVersion
V_DEV_NOTES = 260                       # FFortniteMainBranchObjectVersion

# A node class that starts its own execution. `K2Node_Tunnel` is the macro/collapsed-graph entry and
# is only an entry when an instance of its graph is itself reachable -- handled separately.
ENTRY_CLASSES = frozenset((
    "K2Node_Event", "K2Node_CustomEvent", "K2Node_FunctionEntry",
    "K2Node_InputKey", "K2Node_InputAction", "K2Node_InputAxisEvent",
    "K2Node_InputAxisKeyEvent", "K2Node_InputTouch", "K2Node_InputKeyEvent",
    "K2Node_ActorBoundEvent", "K2Node_ComponentBoundEvent", "K2Node_Timeline",
    "K2Node_EnhancedInputAction", "K2Node_InputDebugKey",
))

# A PURE function graph has no exec pins anywhere: its entry node drives nothing and its result node
# is driven by nothing. Seeding the DATA frontier with every result node is what makes such a graph
# read as live -- without it every pure function in the project reports as one large island.
DATA_ENTRY_CLASSES = frozenset(("K2Node_FunctionResult",))

# Classes that carry no behaviour of their own: an island made only of these is graph furniture.
INERT_CLASSES = frozenset((
    "EdGraphNode_Comment", "K2Node_Knot", "K2Node_VariableGet", "K2Node_Self",
    "K2Node_MakeArray", "K2Node_MakeStruct", "K2Node_BreakStruct", "K2Node_Literal",
    "K2Node_GetArrayItem", "K2Node_Select", "K2Node_EnumLiteral", "K2Node_CastByteToEnum",
    "K2Node_ClassDynamicCast", "K2Node_GetClassDefaults", "K2Node_MakeMap", "K2Node_MakeSet",
    "K2Node_MakeVariable", "K2Node_TemporaryVariable", "K2Node_CallMaterialParameterCollectionFunction",
))

# Function names whose only product is developer output. An island holding nothing else is noise,
# and is not reported as a finding. `debugFunctions` in `.octopus/blueprint.json` adds the project's
# own debug printers to this list.
DEBUG_FUNCTIONS = frozenset((
    "PrintString", "PrintText", "PrintWarning", "LogString",
)) | frozenset(_project.setting("blueprint", "debugFunctions", []))


def read_custom_versions(data):
    """FCustomVersionContainer out of the summary. `_uasset.read_summary` skips it."""
    r = _R(data)
    if r.u32() != PACKAGE_FILE_TAG:
        raise PackageError("not a package (bad tag)")
    legacy = r.i32()
    if not -9 <= legacy < 0:
        raise PackageError("unsupported LegacyFileVersion %d" % legacy)
    if legacy != -4:
        r.i32()
    r.i32()                                                 # FileVersionUE4
    ue5 = r.i32() if legacy <= -8 else 0
    r.i32()                                                 # FileVersionLicenseeUE
    if ue5 >= 1016:
        r.skip(20)
        r.i32()
    out = {}
    if legacy <= -2:
        n = r.i32()
        if n < 0 or n > 4096:
            raise PackageError("implausible custom-version count %d" % n)
        for _ in range(n):
            g = data[r.o:r.o + 16]
            r.o += 16
            v = r.i32()
            out["%08x%08x%08x%08x" % struct.unpack_from("<IIII", g, 0)] = v
            if legacy > -6:
                r.fstring()
    return out


class Cursor(object):
    """Little-endian cursor over one export's payload. Every read is bounds-checked by struct."""

    def __init__(self, data, off, names):
        self.d = data
        self.o = off
        self.names = names

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.o)[0]
        self.o += 4
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.o)[0]
        self.o += 4
        return v

    def i64(self):
        v = struct.unpack_from("<q", self.d, self.o)[0]
        self.o += 8
        return v

    def u8(self):
        v = self.d[self.o]
        self.o += 1
        return v

    def i8(self):
        v = struct.unpack_from("<b", self.d, self.o)[0]
        self.o += 1
        return v

    def f32(self):
        self.o += 4

    def f64(self):
        self.o += 8

    def guid(self):
        v = self.d[self.o:self.o + 16]
        self.o += 16
        return v

    def fname(self):
        i, n = struct.unpack_from("<ii", self.d, self.o)
        self.o += 8
        if not 0 <= i < len(self.names):
            raise PackageError("FName index %d outside a %d-entry table" % (i, len(self.names)))
        return self.names[i] + ("_%d" % (n - 1) if n else "")

    def fstring(self):
        n = self.i32()
        if n == 0:
            return ""
        if abs(n) > (1 << 22):
            raise PackageError("implausible string length %d" % n)
        if n > 0:
            b = self.d[self.o:self.o + n]
            self.o += n
            return b[:-1].decode("utf-8", "replace")
        n = -n
        b = self.d[self.o:self.o + n * 2]
        self.o += n * 2
        return b[:-2].decode("utf-16-le", "replace")

    def boolean(self):
        """FArchive serialises a bool as a 32-bit int, not a byte."""
        return self.i32() != 0


class Pin(object):
    __slots__ = ("name", "category", "direction", "links", "orphaned", "default")

    def __init__(self, name, category, direction, links, orphaned, default):
        self.name = name
        self.category = category
        self.direction = direction              # 0 = input, 1 = output
        self.links = links                      # [export index of the node at the other end]
        self.orphaned = orphaned
        self.default = default

    @property
    def is_exec(self):
        return self.category == "exec"


class Node(object):
    __slots__ = ("index", "name", "cls", "graph", "pins", "symbol", "target_graph")

    def __init__(self, index, name, cls, graph):
        self.index = index
        self.name = name
        self.cls = cls
        self.graph = graph                      # export index of the owning EdGraph
        self.pins = []
        self.symbol = ""                        # function / variable / event name, when it has one
        self.target_graph = 0                   # macro or collapsed graph this node instances


class PinReader(object):
    """Decodes one export's pin array. Nothing here returns a degraded result."""

    def __init__(self, pkg, cv):
        self.p = pkg
        self.framework = cv.get(CV_FRAMEWORK, 0)
        self.editor = cv.get(CV_EDITOR, 0)
        self.release = cv.get(CV_RELEASE, 0)
        self.ue5main = cv.get(CV_UE5MAIN, 0)
        self.ue5rel = cv.get(CV_UE5REL, 0)
        self.blueprint = cv.get(CV_BLUEPRINT, 0)
        self.fortnite = cv.get(CV_FORTNITE, 0)

    # -- FText -------------------------------------------------------------------------------
    def _ftext(self, r):
        r.u32()                                             # ETextFlag
        ht = r.i8()
        if ht == -1:                                        # None
            if self.editor >= V_CULTURE_INVARIANT_KEY and r.boolean():
                r.fstring()
            return
        if ht == 0:                                         # Base
            r.fstring(); r.fstring(); r.fstring()           # namespace, key, source
            self._dev_notes(r)
            return
        if ht in (1, 2, 3):                                 # Named / Ordered / ArgumentData format
            self._ftext(r)                                  # the format text
            n = r.i32()
            if not 0 <= n < 4096:
                raise PackageError("implausible format-argument count %d" % n)
            for _ in range(n):
                if ht == 1:
                    r.fstring()                             # TSortedMap key
                    self._format_argument_value(r)
                elif ht == 2:
                    self._format_argument_value(r)
                else:
                    self._format_argument_data(r)
            return
        if ht == 10:                                        # Transform
            self._ftext(r)
            r.u8()
            return
        if ht == 11:                                        # StringTableEntry
            r.fname(); r.fstring()
            return
        raise PackageError("FText history type %d is not decoded" % ht)

    def _dev_notes(self, r):
        if self.fortnite >= V_DEV_NOTES:
            r.fstring()

    def _format_argument_value(self, r):
        t = r.i8()
        if t == 0:
            r.i64()
        elif t in (1, 5):
            r.i64()
        elif t == 2:
            r.f32()
        elif t == 3:
            r.f64()
        elif t == 4:
            self._ftext(r)
        else:
            raise PackageError("format argument type %d is not decoded" % t)

    def _format_argument_data(self, r):
        r.fstring()                                         # ArgumentName
        t = r.u8() if self.editor >= V_ARG_DATA_IS_VARIANT else 4
        if t == 0:
            r.i64() if self.ue5rel >= V_ARG_DATA_64BIT else r.i32()
        elif t == 2:
            r.f32()
        elif t == 3:
            r.f64()
        elif t == 4:
            self._ftext(r)
        elif t == 5:
            r.u8()
        else:
            raise PackageError("format argument data type %d is not decoded" % t)

    # -- FEdGraphPinType ---------------------------------------------------------------------
    def _pin_type(self, r):
        if self.framework >= V_PINS_STORE_FNAME:
            category = r.fname()
            r.fname()                                       # PinSubCategory
        else:
            category = r.fstring()
            r.fstring()
        r.i32()                                             # PinSubCategoryObject
        if self.framework >= V_PIN_CONTAINER_TYPE:
            if r.u8() == 3:                                 # EPinContainerType::Map
                self._terminal_type(r)
        else:
            if self.blueprint >= V_ADVANCED_CONTAINER:
                if r.boolean():
                    self._terminal_type(r)
                r.boolean()
            r.boolean()
        r.boolean(); r.boolean()                            # bIsReference, bIsWeakPointer
        r.i32(); r.fname(); r.guid()                        # PinSubCategoryMemberReference
        r.boolean()                                         # bIsConst
        if self.release >= V_UOBJECT_WRAPPER_FLAG:
            r.boolean()
        if self.ue5rel >= V_SINGLE_PRECISION_PIN:
            r.boolean()
        return category

    def _terminal_type(self, r):
        if self.framework >= V_PINS_STORE_FNAME:
            r.fname(); r.fname()
        else:
            r.fstring(); r.fstring()
        r.i32()
        r.boolean(); r.boolean()
        if self.release >= V_UOBJECT_WRAPPER_FLAG:
            r.boolean()

    # -- pins --------------------------------------------------------------------------------
    def _pin_ref(self, r):
        """One serialised UEdGraphPin* -> the owning node's package index, or None."""
        if r.boolean():                                     # bNullPtr
            return None
        node = r.i32()
        r.guid()
        return node

    def _pin_ref_array(self, r):
        n = r.i32()
        if not 0 <= n < 4096:
            raise PackageError("implausible pin-array count %d" % n)
        return [x for x in (self._pin_ref(r) for _ in range(n)) if x is not None]

    def _pin_body(self, r):
        r.i32()                                             # OwningNode (again)
        r.guid()                                            # PinId
        name = r.fname() if self.framework >= V_PINS_STORE_FNAME else r.fstring()
        self._ftext(r)                                      # PinFriendlyName
        if self.ue5main >= V_PIN_SOURCE_INDEX:
            r.i32()
        r.fstring()                                         # PinToolTip
        direction = r.u8()
        category = self._pin_type(r)
        default = r.fstring()
        r.fstring()                                         # AutogeneratedDefaultValue
        r.i32()                                             # DefaultObject
        self._ftext(r)                                      # DefaultTextValue
        links = self._pin_ref_array(r)
        self._pin_ref_array(r)                              # SubPins
        self._pin_ref(r)                                    # ParentPin
        self._pin_ref(r)                                    # ReferencePassThroughConnection
        r.guid()                                            # PersistentGuid
        bits = r.u32()
        return Pin(name, category, direction, links, bool(bits & (1 << 5)), default)

    def _entry(self, r):
        if r.boolean():                                     # bNullPtr on the owning-node array
            return None
        owner = r.i32()
        r.guid()
        pin = self._pin_body(r)
        return owner, pin

    def _tail(self, r, kind):
        """What a node subclass writes AFTER its pins.

        Only two subclasses in the whole editor write anything there, found by extracting every
        `Serialize` body in `Editor/BlueprintGraph/Private` and keeping the ones containing `Ar <<`:

            UK2Node_EditablePinBase   TArray<FUserPinInfo>  (K2Node_EditablePinBase.cpp:254)
            UK2Node_DynamicCast       EPureState : uint8    (K2Node_DynamicCast.cpp:62)

        `EditablePinBase` covers every event, every tunnel, both function terminators, macro
        instances and collapsed graphs -- a third of the nodes in a Blueprint.
        """
        if kind == "editable":
            n = r.i32()
            if not 0 <= n < 4096:
                raise PackageError("implausible user-pin count %d" % n)
            for _ in range(n):
                r.fname() if self.framework >= V_PINS_STORE_FNAME else r.fstring()
                self._pin_type(r)
                r.u8()                                      # DesiredPinDirection
                r.fstring()                                 # PinDefaultValue
        elif kind == "purestate":
            r.u8()

    def read_at(self, idx, start, end, tail):
        r = Cursor(self.p.data, start, self.p.names)
        n = r.i32()
        if not 0 <= n < 4096:
            raise PackageError("implausible pin count %d" % n)
        pins = []
        for _ in range(n):
            got = self._entry(r)
            if got is None:
                continue
            owner, pin = got
            if owner != idx + 1:
                raise PackageError("pin claims owner %d, export is %d" % (owner, idx + 1))
            pins.append(pin)
        self._tail(r, tail)
        if r.o != end:
            raise PackageError("pin walk ended at %d, export ends at %d (delta %d)"
                               % (r.o, end, r.o - end))
        return pins

    def read(self, idx):
        """The pin array of export `idx`.

        `ScriptSerializationEndOffset` lands a few bytes before the array on UE5 packages, the same off-by-a-field `_uasset.script_tags` already works around at the start
        offset. Candidates are tried and only an EXACT landing on the export's serial end is
        accepted, so a wrong guess cannot produce a plausible-looking wrong link set.
        """
        e = self.p.exports[idx]
        self.p.load_payload()
        end = e.serial_offset + e.serial_size
        first = None
        for base in self._bases(e):
            for k in (4, 8, 0, 12, 1, 2, 3, 5, 6, 7):
                for tail in ("none", "editable", "purestate"):
                    try:
                        return self.read_at(idx, base + k, end, tail)
                    except (PackageError, struct.error, IndexError) as ex:
                        if first is None:
                            first = ex
        raise PackageError(str(first))

    def _bases(self, e):
        """Where the pin array starts.

        Modern packages carry `ScriptSerializationEndOffset` on the export and the array follows it.
        Packages saved before that field existed -- for example ue4=518 -- do not, and `_uasset`
        fills those fields with
        `0, serial_size`, which points at the END of the payload. For those the tagged-property
        stream has to be walked to find its own terminator.
        """
        end = e.serial_offset + e.serial_size
        out = []
        if 0 < e.script_end < e.serial_size:
            out.append(e.serial_offset + e.script_end)
        for start in (e.serial_offset, e.serial_offset + 1):
            try:
                _tags, p, terminated = self.p.walk(start, end)
            except (PackageError, struct.error, IndexError):
                continue
            if terminated:
                out.append(p)
        return out


def _fname_at(pkg, off):
    i, n = struct.unpack_from("<ii", pkg.data, off)
    if not 0 <= i < len(pkg.names):
        raise PackageError("FName index %d outside the table" % i)
    return pkg.names[i] + ("_%d" % (n - 1) if n else "")


def _node_symbol(pkg, e, cls):
    """The function / variable / event / macro a node names, from its tagged properties."""
    symbol = ""
    target = 0
    try:
        tags = pkg.script_tags(e)
    except (PackageError, struct.error):
        return symbol, target
    for tag in tags:
        try:
            if tag.name in ("FunctionReference", "VariableReference", "EventReference",
                            "DelegateReference"):
                inner, _p, _t = pkg.walk(tag.value_off, tag.next_off)
                for f in inner:
                    if f.name == "MemberName":
                        symbol = _fname_at(pkg, f.value_off)
            elif tag.name in ("CustomFunctionName", "DelegatePropertyName", "EventSignatureName"):
                symbol = symbol or _fname_at(pkg, tag.value_off)
            elif tag.name in ("MacroGraphReference", "GraphReference"):
                inner, _p, _t = pkg.walk(tag.value_off, tag.next_off)
                for f in inner:
                    if f.name in ("MacroGraph", "Graph"):
                        target = struct.unpack_from("<i", pkg.data, f.value_off)[0]
            elif tag.name == "BoundGraph":
                target = struct.unpack_from("<i", pkg.data, tag.value_off)[0]
        except (PackageError, struct.error):
            continue
    if cls == "K2Node_DynamicCast" and not symbol:
        symbol = "Cast"
    return symbol, target


def load_graphs(path):
    """Parse one package into {graph export index: [Node]}, plus the graph names.

    Raises PackageError if ANY graph node in the package fails to parse. A partial node set is a
    partial link set, and a partial link set invents islands.
    """
    pkg = Package(path)
    with open(path, "rb") as h:
        cv = read_custom_versions(h.read(8192))
    reader = PinReader(pkg, cv)
    pkg.load_payload()

    nodes = {}
    graph_names = {}
    for i, e in enumerate(pkg.exports):
        cls = pkg.class_of(e) or ""
        if cls in ("EdGraph", "MaterialGraph", "AnimationGraph"):
            graph_names[i + 1] = e.object_name
    failures = []
    for i, e in enumerate(pkg.exports):
        cls = pkg.class_of(e) or ""
        if not (cls.startswith("K2Node_") or cls == "EdGraphNode_Comment"):
            continue
        node = Node(i + 1, e.object_name, cls, e.outer_index)
        try:
            node.pins = reader.read(i)
        except (PackageError, struct.error, IndexError) as ex:
            failures.append("%s (%s): %s" % (e.object_name, cls, ex))
            continue
        node.symbol, node.target_graph = _node_symbol(pkg, e, cls)
        nodes[node.index] = node
    if failures:
        raise PackageError("%d of %d graph nodes did not parse; first: %s"
                           % (len(failures), len(failures) + len(nodes), failures[0]))
    return pkg, nodes, graph_names


# =================================================================================================
# REACHABILITY
# =================================================================================================
def reachable_set(nodes, entered_graphs=()):
    """Two sets, kept apart on purpose.

    EXEC -- reached by following exec output pins from a genuine entry point.
    DATA -- reached by following a data INPUT back to whatever feeds it, from an EXEC or DATA node.

    A node reached only through DATA does NOT propagate along its exec pins. Merging the two is the
    obvious shortcut and it hides islands: one impure node on a detached chain whose result happens
    to be wired into a live node would drag its whole chain back into the reachable set, and the
    island would read as clean.

    `entered_graphs` names macro graphs whose instances live in OTHER packages -- a macro library's
    graphs are entered from assets this parse never sees.
    """
    by_graph = collections.defaultdict(list)
    for n in nodes.values():
        by_graph[n.graph].append(n)

    exec_frontier = [n.index for n in nodes.values() if n.cls in ENTRY_CLASSES]
    data_frontier = [n.index for n in nodes.values() if n.cls in DATA_ENTRY_CLASSES]
    seen = set(exec_frontier) | set(data_frontier)
    entered = set()

    def enter(graph):
        if graph in entered:
            return
        entered.add(graph)
        for other in by_graph.get(graph, ()):
            if other.cls == "K2Node_Tunnel" and other.index not in seen:
                seen.add(other.index)
                exec_frontier.append(other.index)

    for g in entered_graphs:
        enter(g)

    while exec_frontier or data_frontier:
        if exec_frontier:
            idx = exec_frontier.pop()
            live = True
        else:
            idx = data_frontier.pop()
            live = False
        node = nodes.get(idx)
        if node is None:
            continue
        # A macro or collapsed-graph instance enters its graph -- by OBJECT POINTER, never by name,
        # so no name-based scan can see the call site. A PURE macro instance is entered too.
        if node.target_graph:
            enter(node.target_graph)
        for pin in node.pins:
            if pin.is_exec and pin.direction == 1 and live:
                for t in pin.links:
                    if t in nodes and t not in seen:
                        seen.add(t)
                        exec_frontier.append(t)
            elif not pin.is_exec and pin.direction == 0:
                for t in pin.links:                         # whatever feeds this pin is evaluated
                    if t in nodes and t not in seen:
                        seen.add(t)
                        data_frontier.append(t)
    return seen


def components(nodes, subset):
    """Connected components of `subset`, undirected over every link internal to it."""
    adj = collections.defaultdict(set)
    for idx in subset:
        for pin in nodes[idx].pins:
            for t in pin.links:
                if t in subset:
                    adj[idx].add(t)
                    adj[t].add(idx)
    out = []
    unvisited = set(subset)
    while unvisited:
        root = unvisited.pop()
        comp = {root}
        stack = [root]
        while stack:
            cur = stack.pop()
            for nxt in adj[cur]:
                if nxt not in comp:
                    comp.add(nxt)
                    unvisited.discard(nxt)
                    stack.append(nxt)
        out.append(comp)
    return out


class Island(object):
    def __init__(self, asset, graph, nodes, member_indices, whole_graph=False):
        self.asset = asset
        self.graph = graph
        self.members = [nodes[i] for i in member_indices]
        self.size = len(self.members)
        # True when NOTHING in the owning graph is reachable. That is a different defect from a
        # severed chain: the graph is a macro or a collapsed graph that nothing instances, or an
        # ubergraph page whose events were removed.
        self.whole_graph = whole_graph

    @property
    def classes(self):
        return collections.Counter(n.cls for n in self.members)

    @property
    def symbols(self):
        out = []
        for n in self.members:
            if n.symbol and n.cls not in ("K2Node_FunctionEntry", "K2Node_FunctionResult"):
                out.append(n.symbol)
        return out

    @property
    def roots(self):
        """Members with an exec INPUT pin that is wired to nothing -- the shape of a severed chain."""
        out = []
        for n in self.members:
            for p in n.pins:
                if p.is_exec and p.direction == 0 and not p.links:
                    out.append(n)
                    break
        return out

    @property
    def substance(self):
        """`real`, `debug` or `inert`. Only `real` is a finding."""
        real = False
        debug_only = True
        for n in self.members:
            if n.cls in INERT_CLASSES:
                continue
            if n.cls == "K2Node_CallFunction" and n.symbol in DEBUG_FUNCTIONS:
                real = True
                continue
            debug_only = False
            real = True
        if not real:
            return "inert"
        return "debug" if debug_only else "real"

    def describe(self):
        sym = self.symbols
        top = ", ".join(sorted(set(sym))[:6]) or "-"
        return "%-40s %-26s %3d nodes  %-6s %-11s %s" % (
            self.asset, self.graph[:26], self.size, self.substance,
            "whole-graph" if self.whole_graph else "severed", top)


def imported_graph_index(paths):
    """{package name: {graph names another package imports from it}}, from headers only.

    A macro graph is instanced by object pointer, and the instance can sit in a DIFFERENT package --
    that is what a macro library is. Without this, every graph in every macro library reads as one
    large island, which is the loudest wrong answer this census could give.
    """
    out = collections.defaultdict(set)
    unattributed = set()
    for path in paths:
        try:
            pkg = Package(path)
        except (PackageError, struct.error, IndexError, OSError):
            continue
        for im in pkg.imports:
            if im.class_name != "EdGraph":
                continue
            # An import whose PackageName is empty or the literal FName `None` names no package.
            # Reading `None` as a package name files every engine StandardMacro under one fake
            # package and leaves the real owners unentered, so a macro library that other assets
            # instance reads as dead.
            if im.package_name and im.package_name != "None":
                out[im.package_name].add(im.object_name)
            else:
                unattributed.add(im.object_name)
    return out, unattributed


def islands_of(path, min_size=2, entered=()):
    pkg, nodes, graph_names = load_graphs(path)
    asset = os.path.splitext(os.path.basename(path))[0]
    entered_indices = [idx for idx, name in graph_names.items() if name in entered]
    live = reachable_set(nodes, entered_indices)
    dead = set(nodes) - live
    live_graphs = set(nodes[i].graph for i in live)
    out = []
    for comp in components(nodes, dead):
        if len(comp) < min_size:
            continue
        owner = next(iter(nodes[i].graph for i in comp))
        out.append(Island(asset, graph_names.get(owner, "?"), nodes, comp,
                          owner not in live_graphs))
    return pkg, nodes, graph_names, out


# =================================================================================================
# SELF TEST
# =================================================================================================
def self_test(root):
    """Plant a known island and refuse to report anything if the detector misses it.

    The plant is made on a COPY of the parsed model, never on the asset -- this pass opens nothing
    and writes nothing under `Content/`. It cuts the single exec link that feeds a live chain and
    asserts that (a) the chain becomes an island, (b) it was NOT one before, and (c) the island's
    membership is exactly the nodes downstream of the cut.
    """
    candidates = []
    search = _project.setting("blueprint", "selfTestAssetDir", "Content")
    for dirpath, _dirs, files in os.walk(os.path.join(root, *search.replace("\\", "/").split("/"))):
        for f in files:
            if f.endswith(".uasset"):
                candidates.append(os.path.join(dirpath, f))
        if len(candidates) > 400:
            break
    planted = 0
    for path in sorted(candidates):
        try:
            pkg, nodes, graph_names = load_graphs(path)
        except (PackageError, struct.error, IndexError, OSError):
            continue
        before = reachable_set(nodes)
        if len(before) < 12:
            continue
        # Find a live node whose single exec input carries exactly one link, and cut it.
        target = None
        for idx in sorted(before):
            n = nodes[idx]
            if n.cls in ENTRY_CLASSES or n.cls == "K2Node_Tunnel":
                continue
            ins = [p for p in n.pins if p.is_exec and p.direction == 0]
            if len(ins) == 1 and len(ins[0].links) == 1:
                target = (n, ins[0])
                break
        if target is None:
            continue
        node, pin = target
        src = pin.links[0]
        pin.links = []
        for p in nodes[src].pins:
            if p.is_exec and p.direction == 1:
                p.links = [x for x in p.links if x != node.index]
        after = reachable_set(nodes)
        lost = before - after
        if node.index not in lost:
            print("SELF-TEST FAILED: cutting the only exec input of %s in %s did not detach it"
                  % (node.name, os.path.basename(path)))
            return False
        comps = components(nodes, set(nodes) - after)
        holding = [c for c in comps if node.index in c]
        if len(holding) != 1 or not lost.issubset(holding[0]):
            print("SELF-TEST FAILED: the planted island in %s did not come back whole"
                  % os.path.basename(path))
            return False
        print("SELF-TEST ok: %s / %s -- cut one exec link into %s, %d nodes detached, "
              "recovered as one island of %d"
              % (os.path.basename(path), graph_names.get(node.graph, "?"), node.name,
                 len(lost), len(holding[0])))
        planted += 1
        if planted >= 3:
            return True
    print("SELF-TEST FAILED: no package offered a plantable link")
    return False


# =================================================================================================
# MAIN
# =================================================================================================
def walk_content(root):
    for dirpath, _dirs, files in os.walk(os.path.join(root, "Content")):
        for f in sorted(files):
            if f.endswith(".uasset") or f.endswith(".umap"):
                yield os.path.join(dirpath, f)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(_project.project_root()))
    ap.add_argument("--asset", help="one package; lists every island it holds")
    ap.add_argument("--min", type=int, default=2, help="smallest island reported (default 2)")
    ap.add_argument("--all", action="store_true", help="list inert and debug-only islands too")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(0 if self_test(args.root) else 1)

    if args.asset:
        pkg, nodes, graph_names, isles = islands_of(args.asset, args.min)
        live = reachable_set(nodes)
        print("%s: %d graph nodes, %d graphs, %d reachable, %d detached"
              % (os.path.basename(args.asset), len(nodes), len(graph_names), len(live),
                 len(nodes) - len(live)))
        for isle in sorted(isles, key=lambda i: -i.size):
            if not args.all and isle.substance != "real":
                continue
            print("  " + isle.describe())
            for n in sorted(isle.roots, key=lambda n: n.name):
                print("      root: %-30s %s" % (n.cls, n.symbol or n.name))
            print("      %s" % dict(isle.classes))
        return

    paths = list(walk_content(args.root))
    by_package, unattributed = imported_graph_index(paths)
    print("cross-package graph index: %d packages export a graph somebody imports, "
          "%d imported graph names could not be attributed to a package"
          % (len(by_package), len(unattributed)))

    total = read = 0
    failed = []
    found = []
    for path in walk_content(args.root):
        total += 1
        pkg_name = "/Game/" + os.path.relpath(path, os.path.join(args.root, "Content")) \
            .replace("\\", "/").rsplit(".", 1)[0]
        entered = by_package.get(pkg_name, set()) | unattributed
        try:
            _pkg, nodes, graph_names, isles = islands_of(path, args.min, entered)
        except (PackageError, struct.error, IndexError, OSError) as ex:
            failed.append((os.path.relpath(path, args.root), str(ex)[:110]))
            continue
        read += 1
        found.extend(isles)

    # What was dropped is named FIRST -- a census that hides its own reach is worthless.
    print("=" * 100)
    print("COVERAGE: %d packages, %d read, %d NOT READ" % (total, read, len(failed)))
    if failed:
        by_reason = collections.Counter()
        for _p, msg in failed:
            by_reason[msg.split("(")[0][:70]] += 1
        for reason, n in by_reason.most_common(12):
            print("   %4d  %s" % (n, reason))
        print("   first ten by name:")
        for p, msg in failed[:10]:
            print("      %-70s %s" % (p, msg[:60]))
    print("=" * 100)

    real = [i for i in found if i.substance == "real"]
    print("ISLANDS: %d total of >= %d nodes; %d carry behaviour, %d debug-only, %d inert"
          % (len(found), args.min, len(real),
             sum(1 for i in found if i.substance == "debug"),
             sum(1 for i in found if i.substance == "inert")))
    print("-" * 100)
    for isle in sorted(real, key=lambda i: -i.size):
        print(isle.describe())
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
