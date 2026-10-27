# -*- coding: utf-8 -*-
"""Parse the RigVM model out of a Control Rig T3D dump.

The T3D declares every object twice: an empty forward-declaration block, then a
populated one. This reads the populated `Begin Object Name="..."` tree, so nodes,
their pins (with defaults) and the RigVMLink source/target pin paths all come out.

Usage:  python tools/rigvm_dump.py <textfile> [graphName]
"""
import io, os, re, sys, collections

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

_project.optional_project_root()
path = sys.argv[1]
txt = io.open(path, "r", encoding="utf-8", errors="replace").read()

# The populated model starts at the first SourcePinPath / ResolvedFunctionName block.
start = min(i for i in (txt.find("SourcePinPath"), txt.find("ResolvedFunctionName")) if i > 0)
start = txt.rfind('Begin Object Name="', 0, start)
body = txt[start:]

BEGIN = re.compile(r'^(\s*)Begin Object Name="([^"]+)"')
END = re.compile(r'^\s*End Object')
KV = re.compile(r'^\s*([A-Za-z_][A-Za-z_0-9]*)=(.*)$')

stack = []
objs = {}          # path tuple -> dict of scalar props
order = []

for line in body.splitlines():
    m = BEGIN.match(line)
    if m:
        stack.append(m.group(2))
        key = tuple(stack)
        objs[key] = {}
        order.append(key)
        continue
    if END.match(line):
        if stack:
            stack.pop()
        continue
    if not stack:
        continue
    m = KV.match(line)
    if m:
        k, v = m.group(1), m.group(2).strip()
        if k in ("SubPins", "Pins", "Nodes", "Links", "Contents"):
            continue
        objs[tuple(stack)].setdefault(k, v.strip('"'))

# --- links -------------------------------------------------------------
links = []
for key, p in objs.items():
    if "SourcePinPath" in p and "TargetPinPath" in p:
        graph = ".".join(key[:-1]) if len(key) >= 2 else "?"
        links.append((graph, p["SourcePinPath"], p["TargetPinPath"]))

# --- nodes -------------------------------------------------------------
nodes = []
for key, p in objs.items():
    if "ResolvedFunctionName" in p or "TemplateNotation" in p or "VariableDescription" in p:
        nodes.append((key, p))

want = sys.argv[2] if len(sys.argv) > 2 else None

print("=== NODES ===")
for key, p in nodes:
    graph = ".".join(key[:-1]) if len(key) >= 2 else "?"
    if want and want not in graph:
        continue
    name = key[-1]
    fn = p.get("ResolvedFunctionName") or p.get("TemplateNotation") or ""
    title = p.get("NodeTitle", "")
    print("%-40s %-46s %s" % (graph + "." + name, fn, title))
    # pins directly under this node
    for k2, p2 in objs.items():
        if len(k2) == len(key) + 1 and k2[:-1] == key:
            dv = p2.get("DefaultValue")
            if dv is None:
                continue
            cpp = p2.get("CPPType", "")
            d = p2.get("Direction", "")
            print("        %-22s %-10s %-26s = %s" % (k2[-1], d, cpp, dv[:70]))

print()
print("=== LINKS (%d) ===" % len(links))
per = collections.defaultdict(list)
for g, s, t in links:
    per[g].append((s, t))
for g in sorted(per):
    if want and g != want:
        continue
    print("--- %s (%d) ---" % (g, len(per[g])))
    for s, t in sorted(per[g]):
        print("   %-52s ->  %s" % (s, t))
