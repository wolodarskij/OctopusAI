"""Follow a Blueprint exec/data chain in a T3D export, resolving nodes SAFELY.

WHY THIS EXISTS
---------------
Node names in a T3D export are unique per GRAPH, not per asset. One Blueprint can contain ten
nodes called `K2Node_CallArrayFunction_2`. Any reader that resolves a link by matching
`Begin Object Name="K2Node_..."` over the whole file gets whichever instance appears first, which is
usually the wrong graph - and the result is a plausible-looking chain rather than an error.

THE FIX
-------
A LinkedTo entry names the target node AND the target pin id. So:

  - index every node as a (name, instance) pair, never by name alone;
  - index every pin by its 32-hex PinId, recording which (name, instance) owns it;
  - follow `LinkedTo` by the (node name, PinId) pair, scoped to the source graph, and land on
    the owning instance.

The live editor has the same hazard for node refPaths: Unreal reuses freed node names.

USAGE
-----
    python tools/t3d_node_chain.py <t3d-file> <marker> [max-depth]

`marker` is any substring identifying the START node, e.g. a CustomFunctionName. Quote it; Blueprint
symbols often contain spaces (`Set Is Ready`, `Game  Ended` with two).

Reads only. Produces an indented walk of the chain with pin names, literal defaults and link targets.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

PIN_RE = re.compile(r'CustomProperties Pin \(PinId=([0-9A-F]{32}),([^\n]*)')
GUID_RE = re.compile(r'([0-9A-F]{32})')
# LinkedTo entries are "<NodeName> <PinId>" PAIRS. A PinId alone is ambiguous, because PinIds are
# NOT unique. The node name makes the resolution exact.
LINK_RE = re.compile(r'([A-Za-z0-9_]+)\s+([0-9A-F]{32})')

# A T3D export is TWO PASSES over the same nodes, and only the second carries data:
#
#     Begin Object Class=/Script/BlueprintGraph.K2Node_Foo Name="K2Node_Foo_0"   <- declaration
#     End Object                                                                    (EMPTY body)
#     ...
#     Begin Object Name="K2Node_Foo_0"                                           <- values
#        <properties and CustomProperties Pin lines>
#     End Object
#
# NODE_RE deliberately matches only the second form - `Begin Object Name=`, with no `Class=`. A
# pattern that matches `Begin Object Class=... Name=` picks up the empty declarations too and doubles
# every instance count.


NODE_RE = re.compile(r'\n      Begin Object Name="([^"]+)"\n(.*?)\n      End Object', re.S)

# GRAPH SCOPING.
#
# The value pass nests three levels deep and the indentation is load-bearing:
#
#     Begin Object Name="<Blueprint>"          0 spaces
#        Begin Object Name="<GraphName>"       3 spaces   <- GRAPH_RE
#           Begin Object Name="K2Node_..."     6 spaces   <- NODE_RE
#
# Without this, nodes are indexed `(name, instance)` FILE-WIDE. That is safe for following a data
# chain, because PinIds are globally unique - but it makes a node reached BY NAME unattributable:
# one Blueprint can hold five instances of `K2Node_IfThenElse_0`, and nothing says which graph each
# belongs to.
GRAPH_RE = re.compile(r'\n   Begin Object Name="([^"]+)"\n(.*?)\n   End Object', re.S)



def read_t3d(path):
    """Read a T3D export, sniffing the encoding from its BOM.

    NOT ALL EXPORTS ARE UTF-8: an export directory can hold UTF-16 exports too.

    Reading UTF-16 as utf-8 with errors="ignore" DOES NOT RAISE. The BOM is dropped and every
    character survives separated by a NUL, so the text matches no pattern and this tool reports
    "marker not found" for a graph that is present and intact.

    A failed lookup in a T3D is not evidence the content is absent. Check the encoding first.
    """
    with open(path, "rb") as fh:
        head = fh.read(2)
    enc = "utf-16" if head in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    return open(path, encoding=enc, errors="ignore").read()


def load(path):
    """Return (nodes, pins). nodes: name -> [body...]. pins: pinid -> {node, inst, name, ...}.

    KNOWN LIMIT: this indexes nodes at the standard six-space depth - a node inside an EdGraph
    inside a Blueprint. That covers the ubergraph and the function graphs.

    It does NOT cover every event. An event can resolve as a start node while its links land on pins
    this index does not hold, so the walk stops after one line. An indent-agnostic regex is worse: it
    truncates any node body at its first nested object.

    So: when the walk stops after the start node, the tool has not found the chain - it has not proved
    there is no chain. Fall back to resolving the start node's LinkedTo pin ids by hand; PinIds are
    globally unique, which is the property that actually matters, and a five-line script does it.
    """
    text = read_t3d(path)
    nodes = {}
    owner = {}   # (name, inst) -> graph name

    # Index per graph, then fall back to a whole-file scan if the nesting does not match (some
    # exports differ). A tool that silently finds no nodes reads as an empty graph.
    graphs = list(GRAPH_RE.finditer(text))
    if graphs:
        for gm in graphs:
            gname, gbody = gm.group(1), gm.group(2)
            for m in NODE_RE.finditer("\n" + gbody):
                nm = m.group(1)
                nodes.setdefault(nm, []).append(m.group(2))
                owner[(nm, len(nodes[nm]) - 1)] = gname
    else:
        for m in NODE_RE.finditer(text):
            nodes.setdefault(m.group(1), []).append(m.group(2))

    pins = {}
    for name, bodies in nodes.items():
        for inst, body in enumerate(bodies):
            for pm in PIN_RE.finditer(body):
                pid, rest = pm.group(1), pm.group(2)
                nm = re.search(r'PinName="([^"]+)"', rest)
                dv = re.search(r'DefaultValue="([^"]*)"', rest)
                lt = re.search(r'LinkedTo=\(([^)]*)\)', rest)
                rec = {
                    "node": name,
                    "inst": inst,
                    "graph": owner.get((name, inst), "?"),
                    "name": nm.group(1) if nm else "?",
                    "default": dv.group(1) if dv else "",
                    "links": GUID_RE.findall(lt.group(1)) if lt else [],
                    # (node_name, pin_id) pairs - the exact form. Prefer this over "links".
                    "link_pairs": LINK_RE.findall(lt.group(1)) if lt else [],
                }
                # PIN IDS ARE **NOT** GLOBALLY UNIQUE: one Blueprint can carry the same pin id on
                # two or three nodes. A plain `pins[pid] = rec` is therefore LAST-WRITE-WINS, and a
                # link through a duplicated id lands on whichever node was indexed last.
                #
                # Keep every claimant, and let the resolver pick by graph.
                pins.setdefault(pid, []).append(rec)
    return nodes, pins, owner


def resolve(pins, pid, from_graph=None):
    """Resolve a pin id to ONE record, preferring a claimant in the caller's own graph.

    Exec and data links never cross graph boundaries in a Blueprint, so when a pin id is claimed by
    several nodes the one in the source node's graph is the real target. Falls back to the first
    claimant when the graph is unknown or no claimant matches - and a caller that cares should check
    `len(pins[pid]) > 1` rather than trust a fallback.
    """
    claims = pins.get(pid)
    if not claims:
        return None
    if from_graph:
        for c in claims:
            if c["graph"] == from_graph:
                return c
    return claims[0]


def label(nodes, name, inst, owner=None):
    body = nodes[name][inst] if name in nodes and inst < len(nodes[name]) else ""
    for pat in (r'CustomFunctionName="([^"]+)"', r'MemberName="([^"]+)"', r'VarName="([^"]+)"'):
        m = re.search(pat, body)
        if m:
            g = owner.get((name, inst)) if owner else None
            return "%s[%s]%s" % (name, m.group(1), ("@" + g) if g and g != "?" else "")
    return name


def pins_of(pins, name, inst):
    out = []
    for pid, claims in pins.items():
        for c in claims:
            if c["node"] == name and c["inst"] == inst:
                out.append((pid, c))
    return out


def walk(nodes, pins, name, inst, depth, seen, limit, owner=None):
    owner_index = owner or {}
    if depth > limit or (name, inst) in seen:
        return
    seen.add((name, inst))
    print("  " * depth + label(nodes, name, inst, owner))
    for _pid, info in pins_of(pins, name, inst):
        if info["name"] == "OutputDelegate":
            continue
        here = info.get("graph")
        targets = []
        # Resolve by the NODE NAME carried in LinkedTo, scoped to this graph. Exact - no PinId
        # ambiguity involved. Falls back to id-only resolution for any pair that does not match.
        for tname, tpid in info.get("link_pairs") or []:
            hit = None
            for cand in pins.get(tpid, []):
                if cand["node"] == tname and cand["graph"] == here:
                    hit = cand
                    break
            if hit is None:
                for (nm2, inst2), g2 in owner_index.items():
                    if nm2 == tname and g2 == here:
                        hit = {"node": nm2, "inst": inst2, "name": "?"}
                        break
            if hit is None:
                hit = resolve(pins, tpid, here)
            if hit is not None:
                targets.append((hit["node"], hit["inst"], hit["name"]))
        line = "%-18s" % info["name"]
        if info["default"]:
            line += " =%s" % info["default"]
        if targets:
            line += " -> " + ", ".join("%s.%s" % (label(nodes, n, i, owner), p) for n, i, p in targets)
        if info["default"] or targets:
            print("  " * (depth + 1) + line)
        for n, i, _p in targets:
            walk(nodes, pins, n, i, depth + 2, seen, limit, owner)


def main():
    _project.optional_project_root()
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    path, marker = sys.argv[1], sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10

    nodes, pins, owner = load(path)
    start = None
    for name, bodies in nodes.items():
        for inst, body in enumerate(bodies):
            if marker in body:
                start = (name, inst)
                break
        if start:
            break
    if not start:
        sys.exit("marker not found: %s" % marker)

    dupes = len(nodes[start[0]])
    print("start: %s   (this node NAME has %d instance(s) in the file)\n"
          % (label(nodes, start[0], start[1], owner), dupes))
    walk(nodes, pins, start[0], start[1], 0, set(), limit, owner)


if __name__ == "__main__":
    main()
