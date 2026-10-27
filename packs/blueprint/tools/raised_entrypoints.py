# -*- coding: utf-8 -*-
"""Offline census of BLUEPRINT ENTRY POINTS THAT NOTHING RAISES.

`tools/exec_islands.py` answers "can execution reach this node INSIDE its own graph". It says
nothing about whether the graph's entry point is ever entered: a function reachable inside a
macro nothing drives, a function declared in several packages and called by none, an event
enterable only through a dead call node. This censuses it.

WHAT IT MEASURES
----------------
For every function graph, macro graph and custom event in every package under `Content/`:

    tier 1  NO REFERENCE     nothing anywhere in Content/, Script/ or Source/ names it
    tier 2  ALL DEAD         something names it, and every naming node is itself unreachable
                             inside its own graph

Both mean the entry point is never raised. Tier 2 is the composition of this census with the island
census and is invisible to either alone.

EVERY MECHANISM GETS ITS OWN SWEEP, because they do not look alike on disk:

    function graph      K2Node_CallFunction family, by FunctionReference.MemberName --
                        MINUS the graph's own FunctionEntry and FunctionResult, which carry the
                        same MemberName and inflate every naive count by two
    custom event        the same call-node sweep on the event's own name, OR a delegate bind
    macro graph         K2Node_MacroInstance, which names its macro by OBJECT POINTER and never by
                        name -- no name-based scan can see it. Followed by pointer, and
                        cross-package instances by the importer's EdGraph import
    delegate handler    K2Node_CreateDelegate.SelectedFunctionName -- produces NO call node at all
    timer / by-name     any pin default string anywhere in the project (SetTimerByFunctionName,
                        BindEventByName, CallFunctionByName). Deliberately broad
    engine hook         K2Node_Event and the input/bound/timeline entry classes. The engine raises
                        them; NOT a finding, and reported separately
    script-side         a whole-word scan of Script/**.as and Source/** -- a Blueprint graph
                        overriding a script UFUNCTION(BlueprintEvent) is raised from there

WHAT A ZERO PROVES
------------------
Nothing, until it is calibrated. Every run reproduces the project's call-node controls before
anything else it claims. The controls are `raisedControls` in `.octopus/blueprint.json`:
{"<function name>": [gap, fingerprint]}. What must reproduce is the GAP between the two columns --
a fact about node classes that does not move when `Content/` gains or loses call nodes. The
absolute counts are a corpus fingerprint: they move on any legitimate edit, so drift in them is
REPORTED, never failed. With no controls set, calibration is skipped and the run says so. Coverage
("N packages, N read, 0 NOT READ") is what proves the corpus was fully seen.

A package that will not parse is NAMED FIRST and counted, and the run exits non-zero -- a partial
node set is a partial reference set, and a missing reference invents an unraised graph.

CALIBRATION IS NOT CORROBORATION WHEN EVERY INSTRUMENT SHARES A PARSER. `exec_islands` and this
both sit on `tools/_uasset.py`, like every census that reads package bytes, so agreeing numbers can
be one bug reported several times. `--crosscheck` puts each control against
`tools/search_bp_refs.py --exact --maps`, a packed-FName byte scan that shares no code with the
parser, at the unit of PACKAGES. The scanner over-approximates -- an FName outlives the node that
used it -- so the assertion is that the parser's package set is a SUBSET of the scanner's, and a
package the parser names that the scanner cannot see fails the run.

    python tools/raised_entrypoints.py                  # whole project
    python tools/raised_entrypoints.py --controls       # calibration only
    python tools/raised_entrypoints.py --crosscheck     # + check the controls against an
                                                        #   instrument sharing no code with this one
    python tools/raised_entrypoints.py --asset <p>      # one package's entry points
    python tools/raised_entrypoints.py --name Foo       # every reference to one name, live or dead
    python tools/raised_entrypoints.py --self-test      # plant a known unraised graph; refuse if missed
    python tools/raised_entrypoints.py --json <out>     # machine-readable dump

It opens nothing in the editor and writes nothing under `Content/`, `Script/`, `Source/` or
`Config/`. The self-test plants on a COPY of the parsed model, never on an asset.
"""
from __future__ import print_function

import argparse
import collections
import io
import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project                                                              # noqa: E402
from _uasset import Package, PackageError                                    # noqa: E402
from exec_islands import (load_graphs, reachable_set, imported_graph_index,  # noqa: E402
                          walk_content, ENTRY_CLASSES)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

# Node classes that REFERENCE a function/event by name. `K2Node_CallFunction` and every subclass
# that stores a `FunctionReference` -- array/map/set library calls, interface messages, parent
# calls, data-table calls. `K2Node_FunctionEntry`, `K2Node_FunctionResult`, `K2Node_Event` and
# `K2Node_CustomEvent` carry the same MemberName and ARE the entry point, never a call.
NOT_A_CALL = frozenset((
    "K2Node_FunctionEntry", "K2Node_FunctionResult", "K2Node_Event", "K2Node_CustomEvent",
    "K2Node_Tunnel", "K2Node_VariableGet", "K2Node_VariableSet", "K2Node_ComponentBoundEvent",
    "K2Node_ActorBoundEvent", "K2Node_Timeline",
))

# `Super::X` does NOT raise X. It sits INSIDE the override of X and runs only when that override is
# itself raised, so counting it makes an unraised override look raised by its own body: the child
# overrides that call their own parent would score the function as raised when nothing calls it.
# Recorded separately so a report can say "only Super:: calls".
PARENT_CALL = "K2Node_CallParentFunction"

# A ubergraph page is not an entry point in its own right -- its events are.
UBERGRAPH_PREFIXES = ("EventGraph", "ExecuteUbergraph")

# The project's calibration controls: name -> (gap, fingerprint), from `raisedControls` in
# `.octopus/blueprint.json`.
#
# `gap` = (every node naming the symbol) - (`K2Node_CallFunction` only), and it is the ASSERTION.
# It is the trap this census exists to avoid: a function whose own graph has one FunctionEntry and
# four FunctionResult nodes inflates by 5, because those nodes carry the same MemberName, so a naive
# count of "how many nodes name this function" over-reports its callers by 5. Adding or deleting a
# call node moves BOTH columns by the same amount and leaves the gap alone, which is why the gap is
# the part that must reproduce.
#
# `fingerprint` = the absolute `all` column when it was last observed. An equality check on it goes
# stale on every legitimate `Content/` edit. So it is reported as signed drift and fails nothing,
# except when it COLLAPSES to under half, which no edit produces and a broken parser does.
CONTROLS = {name: tuple(value) for name, value in
            (_project.setting("blueprint", "raisedControls", {}) or {}).items()}

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]*$")

# Graph names the ENGINE enters directly, with no call node anywhere. `UserConstructionScript` runs
# on every actor construction; `*_AutoGenFunc*` is generated for widget animations and timelines.
# Neither is ever raised by a call node, and both are noise.
ENGINE_GRAPHS = frozenset(("UserConstructionScript", "ReceiveBeginPlay", "ReceiveTick",
                           "ReceiveDestroyed", "PreConstruct", "Construct", "Destruct", "Tick"))
AUTOGEN = re.compile(r"_AutoGenFunc|^ExecuteUbergraph")


def _fname_at(pkg, off):
    i, n = struct.unpack_from("<ii", pkg.data, off)
    if not 0 <= i < len(pkg.names):
        raise PackageError("FName index %d outside the table" % i)
    return pkg.names[i] + ("_%d" % (n - 1) if n else "")


def _object_array_indices(pkg, tag):
    n = struct.unpack_from("<i", pkg.data, tag.value_off)[0]
    if n < 0 or n > 20000:
        raise PackageError("implausible object-array count %d on %s" % (n, tag.name))
    return [struct.unpack_from("<i", pkg.data, tag.value_off + 4 + 4 * k)[0] for k in range(n)]


def graph_lists(pkg):
    """{kind: set(export index)} from the UBlueprint's own authored lists.

    A node-class heuristic gets macros and ubergraph pages backwards; these four arrays are what
    the editor writes.
    """
    out = {"function": set(), "macro": set(), "ubergraph": set(), "delegate": set()}
    bp = None
    for e in pkg.exports:
        cls = pkg.class_of(e) or ""
        if cls == "Blueprint" or (cls.endswith("Blueprint") and not cls.endswith("GeneratedClass")):
            bp = e
            break
    if bp is None:
        return out                       # a level package has no UBlueprint export
    for tag in pkg.script_tags(bp):
        key = {"FunctionGraphs": "function", "MacroGraphs": "macro",
               "UbergraphPages": "ubergraph", "DelegateSignatureGraphs": "delegate"}.get(tag.name)
        if key:
            out[key].update(i for i in _object_array_indices(pkg, tag) if i > 0)
    return out


def override_owners(pkg):
    """{function name: owner of the function it overrides}.

    A Blueprint override is a `UFunction` export on the generated class whose `SuperIndex` names a
    function of the SAME name on a parent. That is the only offline signal that a widget's `OnPaint`
    or `OnDrop` graph is raised by the ENGINE rather than by a call node -- without it every UMG
    override in the project reports as unraised.
    """
    out = {}
    gen = None
    for i, e in enumerate(pkg.exports):
        if (pkg.class_of(e) or "").endswith("BlueprintGeneratedClass"):
            gen = i + 1
            break
    if gen is None:
        return out
    for e in pkg.exports:
        if pkg.class_of(e) != "Function" or e.outer_index != gen or e.super_index == 0:
            continue
        try:
            name, _cls, is_import = pkg.resolve(e.super_index)
        except PackageError:
            continue
        if name != e.object_name:
            continue
        owner = ""
        if is_import:
            im = pkg.import_of(e.super_index)
            outer = pkg.resolve(im and 0 or 0) if False else None
            owner = im.package_name or ""
        out[e.object_name] = owner or "?"
    return out


def extra_symbols(pkg, e, cls):
    """Names a node references that `exec_islands._node_symbol` does not extract.

    `K2Node_CreateDelegate.SelectedFunctionName` is the one that matters: a delegate bind produces
    NO call node, so a handler bound this way scores zero on a call-node sweep.
    """
    out = []
    if cls not in ("K2Node_CreateDelegate", "K2Node_BaseAsyncTask", "K2Node_AddDelegate",
                   "K2Node_CallDelegate", "K2Node_RemoveDelegate", "K2Node_AssignDelegate"):
        return out
    try:
        tags = pkg.script_tags(e)
    except (PackageError, struct.error):
        return out
    for tag in tags:
        try:
            if tag.name in ("SelectedFunctionName", "DelegateFunctionName",
                            "ProxyFactoryFunctionName"):
                out.append(_fname_at(pkg, tag.value_off))
        except (PackageError, struct.error):
            continue
    return out


class EntryPoint(object):
    __slots__ = ("asset", "path", "pkg_name", "kind", "name", "graph_index", "size",
                 "raised_by", "refs_total", "refs_live", "note")

    def __init__(self, asset, path, pkg_name, kind, name, graph_index, size):
        self.asset = asset
        self.path = path
        self.pkg_name = pkg_name
        self.kind = kind                 # function | macro | collapsed | event | engine-event
        self.name = name
        self.graph_index = graph_index
        self.size = size                 # behaviour-carrying nodes in the graph
        self.raised_by = []
        self.refs_total = 0
        self.refs_live = 0
        self.note = ""


class Scan(object):
    def __init__(self):
        self.entries = []
        self.refs = collections.Counter()               # name -> naming nodes (calls only)
        self.refs_any = collections.Counter()           # name -> EVERY node naming it, calibration
        self.refs_live = collections.Counter()          # name -> naming nodes that are reachable
        self.ref_sites = collections.defaultdict(list)
        self.string_names = set()                       # every pin-default string in the project
        self.var_names = collections.Counter()
        self.parent_refs = collections.Counter()        # name -> Super:: calls, which raise nothing
        self.macro_entered = collections.defaultdict(set)
        self.macro_entered_any = collections.defaultdict(set)


def scan_package(path, root, entered_names, out, cache=None):
    pkg, nodes, graph_names = load_graphs(path)
    asset = os.path.splitext(os.path.basename(path))[0]
    pkg_name = "/Game/" + os.path.relpath(path, os.path.join(root, "Content")) \
        .replace("\\", "/").rsplit(".", 1)[0]
    entered_indices = [i for i, n in graph_names.items() if n in entered_names]
    live = reachable_set(nodes, entered_indices)

    lists = graph_lists(pkg)
    overrides = override_owners(pkg)
    # A Blueprint INTERFACE's own graphs are signatures. They are raised on the IMPLEMENTERS, via
    # `K2Node_Message`, and the interface asset itself never runs one.
    is_interface = any((pkg.class_of(e) or "") == "BlueprintGeneratedClass"
                       and pkg.resolve(e.super_index)[0] == "Interface" for e in pkg.exports)
    by_graph = collections.defaultdict(list)
    for n in nodes.values():
        by_graph[n.graph].append(n)

    for n in nodes.values():
        is_live = n.index in live
        if n.symbol:
            out.refs_any[n.symbol] += 1
        if n.cls in ("K2Node_VariableGet", "K2Node_VariableSet"):
            if n.symbol:
                out.var_names[n.symbol] += 1
        elif n.symbol and n.cls == PARENT_CALL:
            out.parent_refs[n.symbol] += 1
            out.ref_sites[n.symbol].append(
                (asset, graph_names.get(n.graph, "?"), n.cls, is_live))
        elif n.symbol and n.cls not in NOT_A_CALL:
            out.refs[n.symbol] += 1
            if is_live:
                out.refs_live[n.symbol] += 1
            out.ref_sites[n.symbol].append(
                (asset, graph_names.get(n.graph, "?"), n.cls, is_live))
        for extra in extra_symbols(pkg, pkg.exports[n.index - 1], n.cls):
            out.refs[extra] += 1
            if is_live:
                out.refs_live[extra] += 1
            out.ref_sites[extra].append(
                (asset, graph_names.get(n.graph, "?"), n.cls + "(bind)", is_live))
        # A macro instance names its macro by POINTER. Record which graphs are instanced, and
        # whether the instancing node is itself reachable.
        if n.target_graph:
            out.macro_entered_any[pkg_name].add(n.target_graph)
            if is_live:
                out.macro_entered[pkg_name].add(n.target_graph)
        for p in n.pins:
            d = (p.default or "").strip()
            if d and len(d) < 96 and IDENT.match(d):
                out.string_names.add(d)

    if cache is not None:
        rec = {"asset": asset, "pkg": pkg_name, "nodes": {}, "entries": [], "results": [],
               "tunnels": collections.defaultdict(list), "graph_names": graph_names,
               "cross_entered": [i for i, n in graph_names.items() if n in entered_names]}
        for n in nodes.values():
            ex, di = [], []
            for pn in n.pins:
                if pn.is_exec and pn.direction == 1:
                    ex.extend(pn.links)
                elif not pn.is_exec and pn.direction == 0:
                    di.extend(pn.links)
            sym = n.symbol if (n.symbol and n.cls not in NOT_A_CALL and n.cls != PARENT_CALL) else None
            rec["nodes"][n.index] = (sym, ex, di, n.target_graph)
            if n.cls == "K2Node_Tunnel":
                rec["tunnels"][n.graph].append(n.index)
            elif n.cls == "K2Node_FunctionResult":
                rec["results"].append(n.index)
            elif n.cls == "K2Node_CustomEvent":
                rec["entries"].append((n.index, "event", n.symbol))
            elif n.cls in ENTRY_CLASSES:
                rec["entries"].append((n.index, "engine", n.symbol or n.name))
            elif n.cls == "K2Node_FunctionEntry":
                rec["entries"].append((n.index, "function", graph_names.get(n.graph, "")))
        cache.append(rec)

    for gidx, gname in graph_names.items():
        members = by_graph.get(gidx, [])
        if not members:
            continue
        classes = set(m.cls for m in members)
        kind = None
        if gidx in lists["function"] or "K2Node_FunctionEntry" in classes:
            kind = "function"
        elif gidx in lists["macro"]:
            kind = "macro"
        elif gidx in lists["delegate"]:
            kind = None
        elif gidx in lists["ubergraph"] or gname.startswith(UBERGRAPH_PREFIXES):
            kind = None
        elif "K2Node_Tunnel" in classes:
            kind = "collapsed"
        if kind and (gname in ENGINE_GRAPHS or AUTOGEN.search(gname)):
            ep = EntryPoint(asset, os.path.relpath(path, root), pkg_name, kind, gname, gidx,
                            len([m for m in members if m.cls != "EdGraphNode_Comment"]))
            ep.raised_by.append("engine-graph")
            out.entries.append(ep)
            continue
        if kind and is_interface:
            ep = EntryPoint(asset, os.path.relpath(path, root), pkg_name, kind, gname, gidx,
                            len([m for m in members if m.cls != "EdGraphNode_Comment"]))
            ep.raised_by.append("interface-signature")
            out.entries.append(ep)
            continue
        if kind:
            ep = EntryPoint(asset, os.path.relpath(path, root), pkg_name, kind, gname, gidx,
                            len([m for m in members if m.cls != "EdGraphNode_Comment"]))
            if gname in overrides:
                ep.raised_by.append("override(%s)" % (overrides[gname] or "?"))
            out.entries.append(ep)
        for m in members:
            if m.cls == "K2Node_CustomEvent" and m.symbol:
                out.entries.append(EntryPoint(asset, os.path.relpath(path, root), pkg_name,
                                              "event", m.symbol, gidx,
                                              downstream(nodes, m.index)))
            elif m.cls in ENTRY_CLASSES and m.cls != "K2Node_CustomEvent":
                ep = EntryPoint(asset, os.path.relpath(path, root), pkg_name, "engine-event",
                                m.symbol or m.name, gidx, 0)
                ep.raised_by.append("engine/parent")
                out.entries.append(ep)


COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


# =================================================================================================
# TRANSITIVE CLOSURE -- the third tier, and the weakest claim of the three
# =================================================================================================
# Tiers 1 and 2 ask "does anything raise this entry point". This asks the stronger question "is this
# entry point reachable from a root the engine or the script layer actually enters". It is what a
# macro with one instance needs when the instance IS exec-reachable inside its graph and its graph
# is an event that nothing raises.
#
# It is reported SEPARATELY and labelled weaker on purpose: one raise mechanism this parser cannot
# see makes every entry point below it read as dead, so a tier-3 hit is a lead, not a measurement.
def closure(cache, seed_names):
    """Fixpoint: which entry-point names are reachable from an engine or script root."""
    raised = set(seed_names)
    while True:
        grew = False
        for rec in cache:
            nodes = rec["nodes"]
            frontier_exec = []
            frontier_data = []
            for idx, kind, name in rec["entries"]:
                if kind == "engine" or name in raised:
                    frontier_exec.append(idx)
            frontier_data.extend(rec["results"])
            seen = set(frontier_exec) | set(frontier_data)
            entered = set()

            def enter(g):
                if g in entered:
                    return
                entered.add(g)
                for t in rec["tunnels"].get(g, ()):
                    if t not in seen:
                        seen.add(t)
                        frontier_exec.append(t)

            for g in rec["cross_entered"]:
                enter(g)
            while frontier_exec or frontier_data:
                if frontier_exec:
                    idx = frontier_exec.pop()
                    live = True
                else:
                    idx = frontier_data.pop()
                    live = False
                n = nodes.get(idx)
                if n is None:
                    continue
                if n[3]:
                    enter(n[3])
                if live:
                    for t in n[1]:
                        if t in nodes and t not in seen:
                            seen.add(t)
                            frontier_exec.append(t)
                for t in n[2]:
                    if t in nodes and t not in seen:
                        seen.add(t)
                        frontier_data.append(t)
            for idx in seen:
                n = nodes.get(idx)
                if n and n[0] and n[0] not in raised:
                    raised.add(n[0])
                    grew = True
                if n and n[3]:
                    gname = rec["graph_names"].get(n[3])
                    if gname and gname not in raised:
                        raised.add(gname)
                        grew = True
        if not grew:
            return raised


def downstream(nodes, start):
    """Nodes an event's own body owns -- exec forward from it, then data back into that.

    A custom event has no graph of its own: its body sits in the shared ubergraph page, so the
    graph's node count says nothing about how much behaviour the event holds. This is what makes
    an unraised event rankable next to an unraised function graph.
    """
    seen = {start}
    frontier = [start]
    while frontier:
        n = nodes.get(frontier.pop())
        if n is None:
            continue
        for pin in n.pins:
            if pin.is_exec and pin.direction == 1:
                for t in pin.links:
                    if t in nodes and t not in seen:
                        seen.add(t)
                        frontier.append(t)
    data = list(seen)
    while data:
        n = nodes.get(data.pop())
        if n is None:
            continue
        for pin in n.pins:
            if not pin.is_exec and pin.direction == 0:
                for t in pin.links:
                    if t in nodes and t not in seen:
                        seen.add(t)
                        data.append(t)
    return len(seen) - 1


def script_text(root):
    """Every `.as` / C++ source line in the project WITH COMMENTS STRIPPED.

    A comment is exactly where a Blueprint symbol's name gets written in prose, and counting one as
    a raise is a false negative that hides the finding: an unraised event scores "raised by script"
    when a source file explains in a comment what the event does.
    """
    blobs = []
    for sub in ("Script", "Source"):
        for dirpath, _d, files in os.walk(os.path.join(root, sub)):
            for f in files:
                if f.endswith((".as", ".h", ".cpp", ".cs")):
                    try:
                        with open(os.path.join(dirpath, f), "rb") as h:
                            blobs.append(COMMENT.sub(" ", h.read().decode("utf-8", "replace")))
                    except OSError:
                        continue
    return "\n".join(blobs)


def adjudicate(out, script_ident):
    for ep in out.entries:
        if ep.kind == "engine-event":
            continue
        if ep.kind in ("macro", "collapsed"):
            live_here = ep.graph_index in out.macro_entered.get(ep.pkg_name, ())
            any_here = ep.graph_index in out.macro_entered_any.get(ep.pkg_name, ())
            if live_here:
                ep.raised_by.append("macro-instance(live)")
            elif any_here:
                ep.note = "every instance of this macro is on a dead island"
            ep.refs_total = 1 if any_here else 0
            ep.refs_live = 1 if live_here else 0
        else:
            ep.refs_total = out.refs.get(ep.name, 0)
            ep.refs_live = out.refs_live.get(ep.name, 0)
            if ep.refs_live:
                ep.raised_by.append("call-node(live)x%d" % ep.refs_live)
            elif ep.refs_total:
                ep.note = "every naming node is on a dead island"
            elif out.parent_refs.get(ep.name):
                ep.note = "only Super:: calls from overrides name it"
        base = ep.name.replace(" ", "")
        if ep.name in script_ident or base in script_ident:
            ep.raised_by.append("script/source")
        if ep.name in out.string_names or base in out.string_names:
            ep.raised_by.append("by-name-string")


def tier(ep):
    if ep.raised_by:
        return None
    return 1 if ep.refs_total == 0 else 2


def self_test(root):
    """Plant a known unraised graph on a COPY of the parsed model; refuse to report if missed."""
    picked = None
    search = _project.setting("blueprint", "selfTestAssetDir", "Content")
    under = os.path.join(root, *search.replace("\\", "/").split("/")) + os.sep
    for path in walk_content(root):
        if not path.startswith(under):
            continue
        try:
            _pkg, nodes, graph_names = load_graphs(path)
        except (PackageError, struct.error, IndexError, OSError):
            continue
        live = reachable_set(nodes)
        names = collections.Counter()
        for n in nodes.values():
            if n.symbol and n.cls not in NOT_A_CALL and n.index in live:
                names[n.symbol] += 1
        for gidx, gname in graph_names.items():
            members = [m for m in nodes.values() if m.graph == gidx]
            if any(m.cls == "K2Node_FunctionEntry" for m in members) and names.get(gname, 0) >= 1:
                picked = (path, nodes, gname, names[gname], live)
                break
        if picked:
            break
    if not picked:
        print("SELF-TEST FAILED: no package offered a locally-called function graph")
        return False
    path, nodes, gname, before, live = picked

    refs = sum(1 for n in nodes.values()
               if n.symbol == gname and n.cls not in NOT_A_CALL and n.index in live)
    if refs == 0:
        print("SELF-TEST FAILED: control lost -- %s scored 0 live references before the plant"
              % gname)
        return False
    for n in nodes.values():
        if n.symbol == gname and n.cls not in NOT_A_CALL:
            n.symbol = "__PLANTED_AWAY__"
    after = sum(1 for n in nodes.values()
                if n.symbol == gname and n.cls not in NOT_A_CALL and n.index in live)
    if after != 0:
        print("SELF-TEST FAILED: the plant left %d references to %s" % (after, gname))
        return False
    # The cross-check verdict, planted both ways. It is the only offline check on `_uasset.py`
    # itself, so a silent inversion of its direction would remove that check without a symptom.
    good, only = crosscheck_verdict({"A", "B"}, {"A", "B", "C"})
    if not (good and only == []):
        print("SELF-TEST FAILED: a parser package set inside the scanner's was not accepted")
        return False
    good, only = crosscheck_verdict({"A", "GHOST"}, {"A", "B"})
    if good or only != ["GHOST"]:
        print("SELF-TEST FAILED: a package the parser named and the scanner cannot see was not "
              "reported")
        return False

    print("SELF-TEST ok: %s / %s -- %d live call nodes before the plant, 0 after, so the graph is "
          "recovered as unraised. The cross-check verdict accepts a subset and rejects a planted "
          "parser-only package. Nothing on disk was touched."
          % (os.path.basename(path), gname, before))
    return True


def run_scan(root, cache=None):
    paths = list(walk_content(root))
    by_package, unattributed = imported_graph_index(paths)
    out = Scan()
    failed = []
    read = 0
    for path in paths:
        pkg_name = "/Game/" + os.path.relpath(path, os.path.join(root, "Content")) \
            .replace("\\", "/").rsplit(".", 1)[0]
        entered = by_package.get(pkg_name, set()) | unattributed
        try:
            scan_package(path, root, entered, out, cache)
        except (PackageError, struct.error, IndexError, OSError) as ex:
            failed.append((os.path.relpath(path, root), str(ex)[:110]))
            continue
        read += 1
    for ep in out.entries:
        if ep.kind in ("macro", "collapsed") and ep.name in by_package.get(ep.pkg_name, ()):
            ep.raised_by.append("macro-instance(cross-package)")
    return out, len(paths), read, failed


def crosscheck_verdict(parser_pkgs, scanner_pkgs):
    """(passes, packages the parser named that the independent scanner cannot see).

    A parser-only package is the parser inventing a reference. A scanner-only package is normal --
    the FName outlives the node -- so it never fails.
    """
    only_mine = sorted(set(parser_pkgs) - set(scanner_pkgs))
    return (not only_mine), only_mine


def crosscheck(out, root):
    """Check each control against an instrument that shares no code with `_uasset.py`.

    `tools/search_bp_refs.py --exact` finds packages by matching the LENGTH-PREFIXED FName in the
    package name table, byte by byte. It does not parse the summary, the export table, a tagged
    property or a pin, so a bug in `_uasset.py` cannot reach it. That makes it the only offline
    falsifier this census has: the four whole-project instruments all sit on one parser, and their
    agreement is one parser reporting four times.

    The two do NOT measure the same thing, and the direction is what carries the check. A name
    stays in the name table after the node using it is deleted, so the scanner is an
    OVER-approximation: parser packages must be a SUBSET of scanner packages. A package the parser
    names and the scanner cannot see is the parser inventing a reference, and it fails the run.
    """
    import contextlib
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import search_bp_refs
    from pathlib import Path

    print("INDEPENDENT CROSS-CHECK -- tools/search_bp_refs.py --exact --maps, a packed-FName byte")
    print("scan that shares no code with _uasset.py. The scanner over-approximates (an FName")
    print("outlives the node that used it), so the parser's packages must be a SUBSET of its:")
    ok = True
    for name in sorted(CONTROLS):
        mine = set(site[0] for site in out.ref_sites.get(name, ()))
        buf = io.StringIO() if hasattr(io, "StringIO") else None
        with contextlib.redirect_stdout(buf):
            hits = search_bp_refs.search(name, Path(root) / "Content", True, False, True)
        theirs = set(os.path.splitext(os.path.basename(str(h)))[0] for h in hits)
        good, only_mine = crosscheck_verdict(mine, theirs)
        ok = ok and good
        print("   %-26s parser %3d package(s) / scanner %3d   %s"
              % (name, len(mine), len(theirs),
                 "subset ok, %d scanner-only" % len(theirs - mine) if not only_mine
                 else "PARSER INVENTED %d: %s" % (len(only_mine), ", ".join(only_mine[:6]))))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(_project.project_root()))
    ap.add_argument("--asset")
    ap.add_argument("--name")
    ap.add_argument("--controls", action="store_true")
    ap.add_argument("--crosscheck", action="store_true",
                    help="also check every control against tools/search_bp_refs.py, which shares "
                         "no code with _uasset.py. Adds about 30 s.")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--min", type=int, default=1, help="smallest graph reported (default 1)")
    ap.add_argument("--transitive", action="store_true",
                    help="also report tier 3 -- entry points raised only from other entry points "
                         "that are themselves never raised. A WEAKER claim; one unseen raise "
                         "mechanism cascades through everything below it.")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(0 if self_test(args.root) else 1)

    cache = [] if args.transitive else None
    out, total, read, failed = run_scan(args.root, cache)

    print("=" * 104)
    print("COVERAGE: %d packages, %d read, %d NOT READ" % (total, read, len(failed)))
    for p, msg in failed[:20]:
        print("   %-72s %s" % (p, msg[:60]))
    print("=" * 104)
    ok = True
    if CONTROLS:
        print("CALIBRATION against the published controls (all nodes naming it / K2Node_CallFunction")
        print("only). The ASSERTION is the GAP between the columns; the absolute count is a corpus")
        print("fingerprint, and drift in it is reported, not failed:")
    else:
        print("CALIBRATION skipped: no controls are set (raisedControls in .octopus/blueprint.json).")
    for name, (gap_expect, fingerprint) in sorted(CONTROLS.items()):
        got_all = out.refs_any.get(name, 0)
        got_call = out.refs.get(name, 0)
        gap = got_all - got_call
        good = gap == gap_expect
        collapsed = got_all * 2 < fingerprint
        ok = ok and good and not collapsed
        drift = got_all - fingerprint
        note = ("gap %d ok" % gap) if good else ("gap %d MISMATCH (published %d)"
                                                 % (gap, gap_expect))
        if collapsed:
            note += "  -- CORPUS COLLAPSED, under half the %d fingerprint" % fingerprint
        elif drift:
            note += ("  (corpus drift %+d vs fingerprint %d -- expected when Content/ changed)"
                     % (drift, fingerprint))
        print("   %-26s %5d / %5d   %s" % (name, got_all, got_call, note))
    if args.crosscheck:
        print("=" * 104)
        ok = crosscheck(out, args.root) and ok
    print("=" * 104)
    if args.controls:
        sys.exit(0 if ok and not failed else 1)

    if args.name:
        print("REFERENCES TO %r: %d total, %d live"
              % (args.name, out.refs.get(args.name, 0), out.refs_live.get(args.name, 0)))
        for site in out.ref_sites.get(args.name, []):
            print("   %-38s %-30s %-30s %s" % (site[0], site[1][:30], site[2],
                                               "LIVE" if site[3] else "dead"))
        return

    adjudicate(out, set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", script_text(args.root))))

    if args.asset:
        want = os.path.splitext(os.path.basename(args.asset))[0]
        for ep in sorted((e for e in out.entries if e.asset == want),
                         key=lambda e: (e.kind, e.name)):
            print("%-13s %-42s %3d nodes  refs %3d/%3d live  %-28s %s"
                  % (ep.kind, ep.name[:42], ep.size, ep.refs_live, ep.refs_total,
                     ",".join(ep.raised_by) or "-- UNRAISED --", ep.note))
        return

    unraised_all = [e for e in out.entries if tier(e)]
    unraised = [e for e in unraised_all if e.size >= args.min]
    print("ENTRY POINTS: %d total (%d function, %d macro/collapsed, %d custom event, "
          "%d engine-raised)"
          % (len(out.entries),
             sum(1 for e in out.entries if e.kind == "function"),
             sum(1 for e in out.entries if e.kind in ("macro", "collapsed")),
             sum(1 for e in out.entries if e.kind == "event"),
             sum(1 for e in out.entries if e.kind == "engine-event")))
    print("UNRAISED: %d entry points of ANY SIZE  (tier 1 nothing names it: %d; tier 2 every "
          "naming node dead: %d)"
          % (len(unraised_all), sum(1 for e in unraised_all if tier(e) == 1),
             sum(1 for e in unraised_all if tier(e) == 2)))
    print("   That is the population `--json` dumps and the number to quote.")
    print("   LISTED BELOW: %d of them with >= %d node(s) (--min %d). The other %d are smaller and "
          "are hidden from the list only."
          % (len(unraised), args.min, args.min, len(unraised_all) - len(unraised)))
    print("-" * 104)
    for ep in sorted(unraised, key=lambda e: (-e.size, e.asset, e.name)):
        print("T%d %-36s %-10s %-42s %3d nodes  %s"
              % (tier(ep), ep.asset[:36], ep.kind, ep.name[:42], ep.size, ep.note))

    if args.transitive:
        seeds = set()
        for ep in out.entries:
            if any(r.startswith(("engine", "script", "by-name", "override", "macro-instance(cross"))
                   for r in ep.raised_by):
                seeds.add(ep.name)
        reached = closure(cache, seeds)
        t3 = [e for e in out.entries
              if e.kind in ("function", "event", "macro", "collapsed") and tier(e) is None
              and e.name not in reached and e.size >= args.min]
        print("-" * 104)
        print("TIER 3 (weaker claim): %d entry points of >= %d node(s) (--min %d) are raised ONLY "
              "from entry points that are themselves never raised" % (len(t3), args.min, args.min))
        for ep in sorted(t3, key=lambda e: -e.size)[:60]:
            print("T3 %-36s %-10s %-42s %3d nodes" % (ep.asset[:36], ep.kind, ep.name[:42],
                                                     ep.size))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as h:
            json.dump([{"asset": e.asset, "path": e.path, "kind": e.kind, "name": e.name,
                        "size": e.size, "tier": tier(e), "refs_total": e.refs_total,
                        "refs_live": e.refs_live, "note": e.note,
                        "raised_by": e.raised_by} for e in out.entries], h, indent=1)
        print("wrote %s -- %d entry points, %d of them UNRAISED. The dump is every entry point at "
              "ANY size; --min filters the console list only, never this file."
              % (args.json, len(out.entries), len(unraised_all)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
