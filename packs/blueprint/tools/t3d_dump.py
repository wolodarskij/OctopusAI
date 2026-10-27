# -*- coding: utf-8 -*-
"""Parse a Blueprint T3D diff dump into a node/pin/link model and print it."""
import io, os, re, sys, collections

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

_project.optional_project_root()
path = sys.argv[1]


def read_t3d(p):
    """Read a T3D export, sniffing the encoding from its BOM.

    An export directory can hold both UTF-8 and UTF-16 exports. Reading UTF-16 as utf-8 does not
    raise: every character survives separated by a NUL, so the text matches no pattern and the tool
    reports an empty graph instead of a read failure.
    """
    with open(p, "rb") as fh:
        head = fh.read(2)
    enc = "utf-16" if head in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    return io.open(p, encoding=enc, errors="replace").read()


txt = read_t3d(path)

# Node class map from the header block
cls = {}
for m in re.finditer(r'Begin Object Class=/Script/[A-Za-z]+\.([A-Za-z0-9_]+) Name="([^"]+)"', txt):
    cls[m.group(2)] = m.group(1)

# Body blocks: Begin Object Name="X" ... End Object
bodies = {}
cur = None
buf = []
for line in txt.splitlines():
    m = re.match(r'\s*Begin Object Name="([^"]+)"', line)
    if m:
        cur = m.group(1); buf = []; continue
    if cur and re.match(r'\s*End Object', line):
        bodies[cur] = "\n".join(buf); cur = None; continue
    if cur is not None:
        buf.append(line)

PIN = re.compile(r'CustomProperties Pin \((.*)\)\s*$')

def field(s, key):
    m = re.search(re.escape(key) + r'="([^"]*)"', s)
    if m: return m.group(1)
    m = re.search(re.escape(key) + r'=([^,\)]*)', s)
    return m.group(1) if m else None

nodes = {}
for name, body in bodies.items():
    n = {"name": name, "class": cls.get(name, "?"), "pins": [], "raw": body}
    n["fn"] = field(body, "FunctionReference")
    mm = re.search(r'MemberName="([^"]+)"', body or "")
    n["member"] = mm.group(1) if mm else None
    vr = re.search(r'VariableReference=\(MemberParent="[^\']*\'([^\']*)\'",MemberName="([^"]+)"', body or "")
    if vr: n["var"] = vr.group(2); n["varowner"] = vr.group(1).split(".")[-1]
    fr = re.search(r'FunctionReference=\((?:MemberParent="[^\']*\'([^\']*)\'",)?MemberName="([^"]+)"', body or "")
    if fr: n["call"] = fr.group(2); n["callowner"] = (fr.group(1) or "self").split(".")[-1]
    ev = re.search(r'EventReference=\((?:MemberParent="[^\']*\'([^\']*)\'",)?MemberName="([^"]+)"', body or "")
    if ev: n["event"] = ev.group(2)
    mg = re.search(r"MacroGraph=\"[^']*'([^']*)'\"", body or "")
    if mg: n["macro"] = mg.group(1)
    cm = re.search(r'NodeComment="([^"]*)"', body or "")
    if cm: n["comment"] = cm.group(1)
    for line in (body or "").splitlines():
        m = PIN.search(line)
        if not m: continue
        s = m.group(1)
        p = {
            "id": field(s, "PinId"),
            "name": field(s, "PinName"),
            "dir": field(s, "Direction") or "EGPD_Input",
            "type": field(s, "PinType.PinCategory"),
            "sub": (field(s, "PinType.PinSubCategoryObject") or ""),
            "default": field(s, "DefaultValue"),
            "defobj": field(s, "DefaultObject"),
            "linked": re.findall(r'LinkedTo=\(([^)]*)\)', s),
        }
        links = []
        if p["linked"]:
            for tok in p["linked"][0].split(","):
                tok = tok.strip()
                if not tok: continue
                parts = tok.split()
                if len(parts) == 2: links.append((parts[0], parts[1]))
        p["links"] = links
        n["pins"].append(p)
    nodes[name] = n

def label(n):
    c = n["class"].replace("K2Node_", "")
    if "event" in n: return "EVENT %s" % n["event"]
    if "macro" in n: return "MACRO %s" % n["macro"].split(":")[-1]
    if c == "CallParentFunction": return "Parent::%s" % (n.get("call") or n.get("member") or "?")
    if c == "CallFunction": return "call %s::%s" % (n.get("callowner", "?"), n.get("call") or "?")
    if c == "VariableGet": return "get %s.%s" % (n.get("varowner", "?"), n.get("var") or "?")
    if c == "VariableSet": return "SET %s.%s" % (n.get("varowner", "?"), n.get("var") or "?")
    if c == "IfThenElse": return "BRANCH"
    if c in ("PromotableOperator", "CommutativeAssociativeBinaryOperator"):
        return "op %s" % (n.get("call") or "?")
    if c == "Knot": return "reroute"
    if c == "Comment": return "// %s" % n.get("comment", "")
    return c

for name in sorted(nodes):
    n = nodes[name]
    if n["class"] in ("SceneThumbnailInfo", "Blueprint", "EdGraph"): continue
    print("%-34s %s" % (name, label(n)))
    for p in n["pins"]:
        d = "OUT" if p["dir"] == "EGPD_Output" else "in "
        tgt = "  -> " + ", ".join("%s.%s" % (a, b[:6]) for a, b in p["links"]) if p["links"] else ""
        dv = ""
        if not p["links"] and p["dir"] != "EGPD_Output":
            v = p.get("defobj") or p.get("default")
            if v not in (None, "", "()"): dv = "  = %s" % v
        unc = "   <UNCONNECTED>" if (not p["links"] and p["dir"] == "EGPD_Output") else ""
        print("      %s %-24s %-10s%s%s%s" % (d, p["name"], p["type"], tgt, dv, unc))
    print()

# reverse index pin id -> node
print("### PIN ID INDEX")
for name, n in nodes.items():
    for p in n["pins"]:
        if p["id"]: print("%s %s %s.%s" % (p["id"][:6], p["dir"][5:], name, p["name"]))
