# -*- coding: utf-8 -*-
"""Extract anim-graph pose connectivity from a T3D dump.

PinIds are NOT unique across nodes in an anim graph - ModifyBone copies share
them - so pins are keyed by (node, pinId).
"""
import io, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

_project.optional_project_root()
path = sys.argv[1]
txt = io.open(path, "r", encoding="utf-8", errors="replace").read()

nodes, pins, stack = {}, {}, []

for line in txt.splitlines():
    s = line.strip()
    m = re.match(r'Begin Object Class=[^ ]*[\./]([A-Za-z_0-9]+) Name="([^"]+)"', s)
    if m:
        nodes.setdefault(m.group(2), {"cls": m.group(1), "props": ""})
        stack.append(m.group(2))
        continue
    if s.startswith("Begin Object "):
        m2 = re.search(r'Name="([^"]+)"', s)
        stack.append(m2.group(1) if m2 else "?")
        continue
    if s.startswith("End Object"):
        if stack:
            stack.pop()
        continue
    if not stack:
        continue
    cur = stack[-1]
    if s.startswith("Node=(") and cur in nodes:
        nodes[cur]["props"] = s
    if s.startswith("CustomProperties Pin ("):
        pid = re.search(r'PinId=([0-9A-F]+)', s)
        pn = re.search(r'PinName="([^"]*)"', s)
        lt = re.findall(r'LinkedTo=\(([^)]*)\)', s)
        links = []
        for chunk in lt:
            for ref in chunk.split(","):
                ref = ref.strip()
                if ref:
                    parts = ref.split()
                    links.append((parts[0], parts[1]) if len(parts) == 2 else (ref, None))
        if pid:
            pins[(cur, pid.group(1))] = {"node": cur, "name": pn.group(1) if pn else "?",
                                         "links": links}

print("=== NODES ===")
for n in sorted(nodes):
    if not nodes[n]["props"] and nodes[n]["cls"] not in ("AnimGraphNode_Root",):
        if not nodes[n]["cls"].startswith("AnimGraphNode"):
            continue
    print("%-36s %s" % (n, nodes[n]["cls"]))
    if nodes[n]["props"]:
        print("      " + nodes[n]["props"][:320])

print()
print("=== EDGES (source -> dest) ===")
seen = set()
for key, p in pins.items():
    for (tn, tp) in p["links"]:
        t = pins.get((tn, tp), {}).get("name", "?")
        a = "%s.%s" % (p["node"], p["name"])
        b = "%s.%s" % (tn, t)
        k = tuple(sorted([a, b]))
        if k in seen:
            continue
        seen.add(k)
        print("%-46s <->  %s" % (a, b))
