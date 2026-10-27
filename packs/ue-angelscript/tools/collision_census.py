"""Parent/child collisions between an AngelScript class and the Blueprints under it.

A name declared on BOTH a script parent and a child Blueprint is a collision: the Blueprint does not
compile until the child's copy goes. This census finds them offline - no editor - and says
which removal route each one needs. There are four routes, and they are not interchangeable.

    kind             removed by
    ---------------  --------------------------------------------------------------------
    variable         BlueprintTools.remove_variable      (destroys every Get/Set node)
    function graph   BlueprintTools.remove_function_graph
    custom event     remove_bp_event.py                  (an event is a node, not a graph)
    dispatcher       the parent/child PROPERTY recipe in angelscript.md - never remove_variable,
                     which destroys every Bind Event instead of repointing it
    macro graph      remove_bp_macro.py --empty          (remove_function_graph SUCCEEDS AND DOES
                                                          NOTHING on a macro - it searches
                                                          FunctionGraphs, a macro is in MacroGraphs)

A macro is reported in its OWN section, not among the collisions: a macro graph emits no UFunction,
so a name match with a script function does not stop the Blueprint compiling. Macros are duplicated
logic to retire. They do not block a compile, so they are not counted as blockers.

THE SCOPE
---------
The asset list comes from `SuperIndex` on each `<Asset>_C` export. So the scan follows grandchildren
(a Blueprint parented to a Blueprint parented to a script class) with no list to keep, and it
reports all four kinds.

WHAT IS *NOT* A COLLISION - three shapes that look exactly like one
------------------------------------------------------------------
1. **A `BlueprintEvent` or `BlueprintOverride` parent.** Both set `FUNC_BlueprintEvent`, so the
   child graph IS the implementation and removing it deletes the feature. Reported under NOT WORK,
   never as work.
2. **A `meta=(DisplayName=...)` that merely looks like the graph name.** A function graph called
   `Play SFX` and a script function `PlaySFX` with that DisplayName do not collide. A DisplayName is
   not an FName. Matching is on the declared identifier only - no space-stripping, no DisplayName
   comparison.
3. **A `DelegateSignatureGraphs` entry with no `NewVariables` entry of category `mcdelegate`.** The
   variable IS the dispatcher property; the signature graph is data beside it and can outlive the
   property. Reported under COVERAGE as a leftover.

THE SAFE / DESTRUCTIVE SPLIT (variables only)
---------------------------------------------
`remove_variable` maps to `FBlueprintEditorUtils::RemoveMemberVariable` -> `RemoveVariableNodes`,
which deletes every Get/Set node for the variable across every graph, unconditionally. It never looks
for an inherited property on the parent, and the asset still compiles afterwards.

So the split is about what can be RECOVERED:

    SAFE         the parent declares a compatible property, so the reads can be REBUILT against it
    DESTRUCTIVE  the child holds a `UserDefinedStruct` or a `_C` class - script can name neither, so
                 the parent necessarily declares something else and nothing can be rebuilt

Both get removed. The split decides how much the ledger of lost behaviour owes.

WHERE THE BLUEPRINT SIDE COMES FROM
-----------------------------------
The Blueprint export `blueprints.json` holds every Blueprint with its direct parent, and for each
one that reaches a script class its variables, graph lists, custom events and overrides. This tool
reads `Script/` and that file, and nothing else. The `blueprint` pack writes the file:

    python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" blueprints --project <project>

When the file is missing, the tool prints the command and exits 2.

WHAT THIS RUN CANNOT SEE - stated every run, in the COVERAGE section
--------------------------------------------------------------------
* The export is the package state ON DISK when it was written. A live in-editor edit that has not
  been saved is invisible, in both directions: a variable removed live but unsaved still shows here.
  An export older than the last save shows the old state; write it again first.
* Map packages are not in the export, so a level Blueprint parented to a script class is out of
  scope.
* Any package the exporter could not parse is NAMED, never counted as clean.

USAGE
-----
    python collision_census.py              # the full report
    python collision_census.py --all        # also list every asset that is clean
    python collision_census.py --vars-only  # variables only
    python collision_census.py --graphs-only
    python collision_census.py --asset MyActor     # substring filter
    python collision_census.py --json
    python collision_census.py --self-test  # prove the detector finds planted defects

`--self-test` reads its control assets from `collision_census.selfTest` in
`<project>/.octopus/ue-angelscript.json`, and skips when none is set.

Exit code: 0 no work outstanding, 1 collisions found, 2 something could not be read.
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project                                                             # noqa: E402
import _export                                                              # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("collision_census", {})

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# =================================================================================================
# SCRIPT SIDE
# =================================================================================================
CLASS_RE = re.compile(r'^\s*class\s+(\w+)\s*(?::\s*([\w\s,]+?))?\s*(?:\{|$)', re.M)
# The specifier list nests ONE level - `meta=(DisplayName="x")`. `[^)]*` stops at the inner `)`
# and drops every declaration that carries a DisplayName.
_SPEC = r'((?:[^()]|\([^()]*\))*)'
# `[^>]*` cannot spell a nested template such as `TArray<TSoftObjectPtr<AMyActor>>`. Take everything
# up to the last `>` on the line instead.
# The optional access specifier is what an encapsulated member carries: `private int32 myBody;`.
# Without it the declaration is invisible and its collision cannot be reported.
PROP_RE = re.compile(r'UPROPERTY\(' + _SPEC + r'\)[ \t]*\n(?:[ \t]*\n)*\s*'
                     r'(?:(?:private|protected|public)\s+)?'
                     r'([\w:]+(?:<[^\n;]*>)?)\s+(\w+)\s*(?:=[^;]*)?;')
FUNC_RE = re.compile(r'UFUNCTION\(' + _SPEC + r'\)[ \t]*\n(?:[ \t]*\n)*[ \t]*([^\n]*)')
FUNC_NAME_RE = re.compile(r'\b(\w+)\s*\(')
UPROPERTY_COUNT_RE = re.compile(r'\bUPROPERTY\s*\(')
UFUNCTION_COUNT_RE = re.compile(r'\bUFUNCTION\s*\(')


class ScriptClass(object):
    def __init__(self, name, base, path):
        self.name = name
        self.base = base
        self.path = path
        self.props = {}                 # declared name -> declared type
        self.funcs = {}                 # declared name -> specifier string


def strip_comments(text):
    """Blank out `//` and block comments, keeping every byte offset.

    Comments often quote declarations, such as "UPROPERTY(), NOT BlueprintAssignable". Counting
    them makes the shortfall figure below meaningless, and a commented-out declaration would
    otherwise parse as real.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            quote = c
            out.append(c)
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == "\\":
                    i += 1
                    if i < n:
                        out.append(text[i])
                        i += 1
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_script(script_root):
    """Every `class` in `Script/`, with its UPROPERTY and UFUNCTION declarations.

    Members and functions are attributed to the class whose source range they fall in. The parse is
    counted against a raw `UPROPERTY(` / `UFUNCTION(` count and the difference is reported: a regex
    that quietly matches fewer declarations than exist is the exact failure this file exists to stop.
    """
    classes = {}
    missed_props = missed_funcs = 0
    for dirpath, _dirs, files in os.walk(script_root):
        for fn in sorted(files):
            if not fn.endswith(".as"):
                continue
            path = os.path.join(dirpath, fn)
            with io.open(path, encoding="utf-8", errors="replace") as handle:
                text = strip_comments(handle.read())
            bounds = [(m.start(), m.group(1), (m.group(2) or "").split(",")[0].strip())
                      for m in CLASS_RE.finditer(text)]
            if not bounds:
                continue
            for i, (start, name, base) in enumerate(bounds):
                end = bounds[i + 1][0] if i + 1 < len(bounds) else len(text)
                body = text[start:end]
                cls = classes.setdefault(name, ScriptClass(name, base or None, path))
                if cls.base is None and base:
                    cls.base = base
                seen_p = 0
                for m in PROP_RE.finditer(body):
                    cls.props[m.group(3)] = m.group(2).strip()
                    seen_p += 1
                seen_f = 0
                for m in FUNC_RE.finditer(body):
                    nm = FUNC_NAME_RE.search(m.group(2))
                    if not nm:
                        continue
                    cls.funcs[nm.group(1)] = m.group(1)
                    seen_f += 1
                missed_props += len(UPROPERTY_COUNT_RE.findall(body)) - seen_p
                missed_funcs += len(UFUNCTION_COUNT_RE.findall(body)) - seen_f
    return classes, missed_props, missed_funcs


def registered_name_map(classes):
    """AngelScript registers `UMyComponent` as `/Script/Angelscript.MyComponent`.

    The prefix is dropped, so the reverse lookup has to try the prefixed spellings too. Trying them
    rather than stripping a leading `U`/`A` avoids turning `UsableItemAction` into `sableItemAction`.
    """
    out = {}
    for name in classes:
        for key in (name, name[1:] if len(name) > 1 and name[0] in "UAF" else None):
            if key:
                out.setdefault(key, []).append(name)
    return out


def script_ancestry(classes, name):
    """[class, its base, ...] within `Script/`. Stops at the first name not declared in script."""
    chain = []
    seen = set()
    cur = name
    while cur in classes and cur not in seen:
        seen.add(cur)
        chain.append(classes[cur])
        cur = classes[cur].base
    return chain


# The two specifiers that set FUNC_BlueprintEvent, so a child graph over either one binds and runs.
OVERRIDABLE = ("BlueprintEvent", "BlueprintOverride")


def has_specifier(spec, word):
    return re.search(r'\b%s\b' % re.escape(word), spec or "") is not None


# =================================================================================================
# BLUEPRINT SIDE
# =================================================================================================
GRAPH_ARRAYS = ("FunctionGraphs", "MacroGraphs", "UbergraphPages", "DelegateSignatureGraphs")


class BPVar(object):
    def __init__(self, name, category, sub_name, sub_class, container):
        self.name = name
        self.category = category
        self.sub_name = sub_name
        self.sub_class = sub_class
        self.container = container

    @property
    def destructive(self):
        """Can the parent stand in for this at all?

        No, if the child's type is something AngelScript cannot name: a `UserDefinedStruct`, or a
        Blueprint class (`..._C`).
        """
        if self.sub_class == "UserDefinedStruct":
            return True
        return bool(self.sub_name) and self.sub_name.endswith("_C")

    @property
    def type_text(self):
        base = self.sub_name or self.category
        return ("%s<%s>" % (self.container, base)) if self.container else base


class BPRecord(object):
    def __init__(self, asset, path):
        self.asset = asset
        self.path = path
        self.parent = None              # object name of the direct super
        self.parent_is_script = False
        self.script_class = None        # AngelScript class name, once resolved
        self.variables = []
        self.function_graphs = []
        self.macro_graphs = []
        self.ubergraph_pages = []
        self.delegate_graphs = []
        self.stale_delegate_graphs = []   # signature graphs with no `mcdelegate` variable
        self.custom_events = []
        self.overrides = set()          # child UFunctions whose SuperIndex names the same function


# =================================================================================================
# THE SCAN
# =================================================================================================
class Scan(object):
    def __init__(self):
        self.packages = 0
        self.blueprints = {}            # asset stem -> BPRecord
        self.unparsable = []            # (path, reason)
        self.body_failed = []           # (asset, reason)
        self.stale_delegate_graphs = [] # (asset, graph name), script-parented assets only


def record_from_export(stem, data):
    """A BPRecord from one entry of `blueprints.json`."""
    rec = BPRecord(stem, data.get("path", stem))
    rec.parent = data.get("parent")
    rec.parent_is_script = bool(data.get("parentIsScript"))
    rec.body_error = data.get("bodyError")
    rec.body_read = "variables" in data
    rec.variables = [BPVar(v.get("name", ""), v.get("category", ""), v.get("subName", ""),
                           v.get("subClass", ""), v.get("container", ""))
                     for v in data.get("variables") or []]
    rec.function_graphs = list(data.get("functionGraphs") or [])
    rec.macro_graphs = list(data.get("macroGraphs") or [])
    rec.ubergraph_pages = list(data.get("ubergraphPages") or [])
    rec.delegate_graphs = list(data.get("delegateGraphs") or [])
    rec.custom_events = list(data.get("customEvents") or [])
    rec.overrides = set(data.get("overrides") or [])
    live = {v.name.lower() for v in rec.variables if v.category == "mcdelegate"}
    rec.stale_delegate_graphs = [g for g in rec.delegate_graphs if g.lower() not in live]
    return rec


def scan_content():
    """The Blueprint side, from the export file `blueprints.json`."""
    data = _export.load("blueprints")
    scan = Scan()
    scan.packages = int(data.get("assetsScanned", 0))
    scan.unparsable = [(u.get("path", "?"), u.get("why", "")) for u in data.get("unparsable", [])]
    for stem, entry in sorted((data.get("blueprints") or {}).items()):
        scan.blueprints[stem] = record_from_export(stem, entry)
    return scan


def resolve_script_ancestors(scan, classes, reg):
    """Walk each Blueprint up until an AngelScript class is reached.

    A grandchild reaches its script ancestor through one or more `_C` parents.
    """
    ambiguous = []
    for rec in scan.blueprints.values():
        seen = set()
        cur = rec
        hops = 0
        while cur is not None and cur.asset not in seen:
            seen.add(cur.asset)
            if cur.parent_is_script:
                cands = reg.get(cur.parent, [])
                if len(cands) > 1:
                    ambiguous.append((rec.asset, cur.parent, cands))
                rec.script_class = cands[0] if cands else None
                rec.script_registered = cur.parent
                rec.hops = hops
                break
            if cur.parent and cur.parent.endswith("_C"):
                cur = scan.blueprints.get(cur.parent[:-2])
                hops += 1
            else:
                cur = None
    return ambiguous


# =================================================================================================
# COMPARISON
# =================================================================================================
class Finding(object):
    def __init__(self, asset, kind, name, detail, owner, route, severity):
        self.asset = asset
        self.kind = kind
        self.name = name
        self.detail = detail
        self.owner = owner
        self.route = route
        self.severity = severity        # "SAFE" | "DESTRUCTIVE" | "" for graphs

    def as_dict(self):
        return dict(asset=self.asset, kind=self.kind, name=self.name, detail=self.detail,
                    owner=self.owner, route=self.route, severity=self.severity)


ROUTE = {
    "variable": "BlueprintTools.remove_variable  (destroys every Get/Set node)",
    "function graph": "BlueprintTools.remove_function_graph",
    "custom event": CFG.get("routeCustomEvent", "remove_bp_event.py"),
    "dispatcher": "parent/child property collision - angelscript.md, the four-step recipe",
    "macro graph": CFG.get("routeMacroGraph", "remove_bp_macro.py") + " --empty  (remove_function_graph is a silent no-op)",
}


def compare(rec, classes, want_vars=True, want_graphs=True):
    """-> (collisions, shadows, seams). Matching is case-insensitive because FName comparison is."""
    chain = script_ancestry(classes, rec.script_class) if rec.script_class else []
    props = {}
    funcs = {}
    for cls in reversed(chain):
        for k, v in cls.props.items():
            props[k.lower()] = (k, v, cls.name)
        for k, v in cls.funcs.items():
            funcs[k.lower()] = (k, v, cls.name)

    collisions = []
    shadows = []
    seams = []

    # A Blueprint event dispatcher is a `NewVariables` entry of category `mcdelegate`. Its
    # `DelegateSignatureGraphs` entry is data beside it and can outlive it, so the graph array is
    # never the key. Reporting the variable under both kinds counts it twice and offers
    # `remove_variable` as one of the two routes, which for a dispatcher destroys every Bind Event.
    # Report it once, as a dispatcher.
    dispatcher_vars = [v for v in rec.variables if v.category == "mcdelegate"]
    dispatchers = {v.name.lower() for v in dispatcher_vars}

    if want_vars:
        for var in rec.variables:
            if var.name.lower() in dispatchers:
                continue
            hit = props.get(var.name.lower())
            if not hit:
                continue
            collisions.append(Finding(
                rec.asset, "variable", var.name,
                "%s  vs script %s" % (var.type_text, hit[1]), hit[2],
                ROUTE["variable"], "DESTRUCTIVE" if var.destructive else "SAFE"))

    if not want_graphs:
        return collisions, shadows, seams

    # A dispatcher is a PROPERTY on the generated class, so it collides with a script UPROPERTY, not
    # with a function. Comparing it against the function set finds nothing and reads as clean.
    for var in dispatcher_vars:
        name = var.name
        hit = props.get(name.lower())
        if hit:
            collisions.append(Finding(rec.asset, "dispatcher", name,
                                      "parent %s::%s is %s" % (hit[2], hit[0], hit[1]),
                                      hit[2], ROUTE["dispatcher"], ""))

    for kind, names in (("function graph", rec.function_graphs),
                        ("custom event", rec.custom_events),
                        ("macro graph", rec.macro_graphs)):
        for name in names:
            hit = funcs.get(name.lower())
            if not hit:
                continue
            decl, spec, owner = hit
            invite = next((s for s in OVERRIDABLE if has_specifier(spec, s)), None)
            if invite:
                # NOT a collision. The parent invited the override and the child graph is the
                # implementation; removing it deletes the feature.
                seams.append(Finding(rec.asset, kind, name,
                                     "parent %s::%s is %s" % (owner, decl, invite),
                                     owner, "leave alone", ""))
                continue
            detail = "parent %s::%s (%s)" % (owner, decl, spec or "no specifiers")
            if name in rec.overrides:
                detail += "  [the child declares it as an OVERRIDE - it cannot bind]"
            if kind == "macro graph":
                # A macro emits no UFunction, so the name match does NOT stop the Blueprint
                # compiling. It is duplicated logic to retire. It is not counted as a blocker.
                shadows.append(Finding(rec.asset, kind, name, detail, owner, ROUTE[kind], ""))
            else:
                collisions.append(Finding(rec.asset, kind, name, detail, owner, ROUTE[kind], ""))
    return collisions, shadows, seams


# =================================================================================================
# REPORT
# =================================================================================================
def run(script_root, args, out=sys.stdout):
    classes, missed_props, missed_funcs = parse_script(script_root)
    reg = registered_name_map(classes)
    scan = scan_content()
    ambiguous = resolve_script_ancestors(scan, classes, reg)

    parented = [r for r in scan.blueprints.values() if r.script_class]
    unresolved = [r for r in scan.blueprints.values()
                  if r.script_class is None and getattr(r, "script_registered", None)]
    if args.asset:
        needle = args.asset.lower()
        parented = [r for r in parented if needle in r.asset.lower()]

    findings, shadows, seams = [], [], []
    for rec in sorted(parented, key=lambda r: r.asset):
        if rec.body_error:
            scan.body_failed.append((rec.asset, rec.body_error))
            continue
        if not rec.body_read:
            scan.body_failed.append((rec.asset, "the export holds no body for it"))
            continue
        f, h, s = compare(rec, classes, not args.graphs_only, not args.vars_only)
        findings += f
        shadows += h
        seams += s
        scan.stale_delegate_graphs += [(rec.asset, g) for g in rec.stale_delegate_graphs]

    if args.json:
        json.dump({"findings": [f.as_dict() for f in findings],
                   "shadows": [h.as_dict() for h in shadows],
                   "seams": [s.as_dict() for s in seams],
                   "assets_scanned": scan.packages,
                   "blueprints": len(scan.blueprints),
                   "script_parented": len(parented),
                   "read": len(parented) - len(scan.body_failed),
                   "unparsable": [{"path": p, "why": w} for p, w in scan.unparsable],
                   "body_failed": [{"asset": a, "why": w} for a, w in scan.body_failed],
                   "stale_delegate_graphs": [{"asset": a, "graph": g}
                                             for a, g in scan.stale_delegate_graphs]},
                  out, indent=1)
        out.write("\n")
        return findings, shadows, seams, scan

    read_ok = len(parented) - len(scan.body_failed)
    out.write("COLLISION CENSUS - offline. Reads the Blueprint export and %s; no editor.\n"
              % os.path.relpath(script_root, ROOT).replace("\\", "/"))
    out.write("%d packages scanned | %d Blueprints | %d with an AngelScript ancestor | %d read\n"
              % (scan.packages, len(scan.blueprints), len(parented), read_ok))
    out.write("%d script classes, %d UPROPERTY, %d UFUNCTION parsed\n\n"
              % (len(classes), sum(len(c.props) for c in classes.values()),
                 sum(len(c.funcs) for c in classes.values())))

    by_asset = {}
    for f in findings:
        by_asset.setdefault(f.asset, []).append(f)
    shadow_count = {}
    for h in shadows:
        shadow_count[h.asset] = shadow_count.get(h.asset, 0) + 1

    for rec in sorted(parented, key=lambda r: r.asset):
        hits = by_asset.get(rec.asset)
        if not hits:
            if args.all:
                # "clean" here means no COLLISION. Say when the asset still owes shadowed macros,
                # or the word reads as "nothing left on this asset".
                extra = ("   + %d shadowed macro(s)" % shadow_count[rec.asset]
                         if rec.asset in shadow_count else "")
                out.write("%-36s clean   (-> %s)%s\n" % (rec.asset, rec.script_class, extra))
            continue
        out.write("\n%-36s %d collision(s)   (-> %s)\n" % (rec.asset, len(hits), rec.script_class))
        for f in sorted(hits, key=lambda x: (x.kind, x.name)):
            out.write("     %-14s %-30s %-11s %s\n"
                      % (f.kind, f.name, f.severity, f.detail))

    out.write("\n=== TOTALS ===\n")
    counts = {}
    for f in findings:
        key = f.kind if f.kind != "variable" else "variable/" + f.severity
        counts[key] = counts.get(key, 0) + 1
    if counts:
        for key in sorted(counts):
            out.write("%-24s %d\n" % (key, counts[key]))
    else:
        out.write("none\n")

    if findings:
        out.write("\nREMOVAL ROUTES\n")
        for kind in sorted({f.kind for f in findings}):
            out.write("  %-15s %s\n" % (kind, ROUTE[kind]))

    out.write("\n=== SHADOWED MACROS: %d ===\n" % len(shadows))
    out.write("A macro graph emits no UFunction, so a name match with a script function does NOT\n"
              "stop the Blueprint compiling. These are duplicated logic to retire, not blockers.\n"
              "Route: %s\n" % ROUTE["macro graph"])
    shadow_by_asset = {}
    for h in shadows:
        shadow_by_asset.setdefault(h.asset, []).append(h.name)
    for asset in sorted(shadow_by_asset):
        out.write("     %-36s %d   %s\n"
                  % (asset, len(shadow_by_asset[asset]),
                     ", ".join(sorted(shadow_by_asset[asset])[:6])
                     + (" ..." if len(shadow_by_asset[asset]) > 6 else "")))

    out.write("\n=== NOT WORK: %d overridable seam(s) across %d asset(s) ===\n"
              % (len(seams), len({s.asset for s in seams})))
    out.write("The parent invited the override; the child graph IS the implementation. Removing one\n"
              "deletes the feature, it does not clear a collision.\n")
    seam_by_decl = {}
    for s in seams:
        seam_by_decl.setdefault("%s::%s" % (s.owner, s.name), []).append(s.asset)
    for decl in sorted(seam_by_decl):
        assets = sorted(seam_by_decl[decl])
        out.write("     %-44s x%-3d %s\n"
                  % (decl, len(assets),
                     ", ".join(assets if args.all else assets[:4])
                     + ("" if args.all or len(assets) <= 4 else " ... (--all for the rest)")))

    out.write("\n=== COVERAGE: what this run did NOT see ===\n")
    out.write("  * DISK state when the export was written. A live editor edit that is not saved is\n"
              "    invisible - including a variable already removed live, which still shows here as\n"
              "    a collision.\n")
    out.write("  * map packages are not scanned (%d packages were). A level Blueprint parented to a\n"
              "    script class would be missed.\n" % scan.packages)
    if scan.unparsable:
        out.write("  * %d packages could not be parsed and are NOT counted clean:\n"
                  % len(scan.unparsable))
        for path, why in scan.unparsable[:args.list_limit]:
            out.write("      %-70s %s\n" % (path, why[:60]))
        if len(scan.unparsable) > args.list_limit:
            out.write("      ... %d more (--list-limit to see them all)\n"
                      % (len(scan.unparsable) - args.list_limit))
    else:
        out.write("  * every package parsed.\n")
    if scan.body_failed:
        out.write("  * %d script-parented Blueprint(s) whose BODY could not be read - these are\n"
                  "    UNMEASURED, not clean:\n" % len(scan.body_failed))
        for asset, why in scan.body_failed:
            out.write("      %-36s %s\n" % (asset, why[:80]))
    if unresolved:
        out.write("  * %d Blueprint(s) name an AngelScript parent no `.as` class matches:\n"
                  % len(unresolved))
        for rec in unresolved:
            out.write("      %-36s -> %s\n" % (rec.asset, rec.script_registered))
    if ambiguous:
        out.write("  * %d ambiguous script-class name(s):\n" % len(ambiguous))
        for asset, reg_name, cands in ambiguous:
            out.write("      %-36s %s -> %s\n" % (asset, reg_name, cands))
    if scan.stale_delegate_graphs:
        out.write("  * %d delegate signature graph(s) with no dispatcher property - leftovers:\n"
                  % len(scan.stale_delegate_graphs))
        for asset, graph in scan.stale_delegate_graphs:
            out.write("      %-36s %s\n" % (asset, graph))
    if missed_props or missed_funcs:
        out.write("  * script parse shortfall: %d UPROPERTY and %d UFUNCTION occurrences did not\n"
                  "    match the declaration regex. Those declarations are INVISIBLE to this run.\n"
                  % (missed_props, missed_funcs))
    if args.vars_only:
        out.write("  * --vars-only: graph collisions were not looked for.\n")
    if args.graphs_only:
        out.write("  * --graphs-only: variable collisions were not looked for.\n")
    if args.asset:
        out.write("  * --asset %r: %d of %d script-parented assets were skipped.\n"
                  % (args.asset, len([r for r in scan.blueprints.values() if r.script_class])
                     - len(parented), len([r for r in scan.blueprints.values() if r.script_class])))

    if findings:
        out.write("\n*** %d COLLISION(S) REMAIN.%s ***\n"
                  % (len(findings), CFG.get("remainNote", "")))
    elif read_ok == 0:
        out.write("\n!! NO VERDICT: nothing was read. This is not evidence that collisions are clear. !!\n")
    else:
        out.write("\nNo collisions remain. (%d script-parented assets read, %d shadowed macros"
                  " outstanding)\n" % (read_ok, len(shadows)))
    return findings, shadows, seams, scan


# =================================================================================================
# SELF-TEST
# =================================================================================================
#
# The controls are real assets of the project, named in `collision_census.selfTest` of
# `<project>/.octopus/ue-angelscript.json`. Each control is a verdict the tool has to reproduce or
# fail loudly:
#
#   a graph that is gone from disk   the tool must NOT report it, and must still report a
#                                    replanted one, or "clean" proves nothing.
#   the BlueprintEvent seams         real NON-collisions.
#   a DisplayName look-alike         a graph named `Play SFX` beside a script `PlaySFX` with that
#                                    DisplayName. A DisplayName is not an FName.
#
# Defects are replanted into a COPY of `Script/`, never the live tree - an `.as` write hot-reloads
# the whole script layer.
#
# The keys of `selfTest` (every one optional):
#   title                  the first line of the self-test
#   minScriptParented      the census must find more script-parented Blueprints than this
#   ancestry               [{asset, scriptClass, label}]  the asset resolves to this script class
#   functionGraphs         [{asset, count, absent, label}] function graph count, and names that are gone
#   graphCounts            [{asset, counts: [function, macro, ubergraph, delegate], label}]
#   customEvents           [{asset, event, label}]         a custom event that must decode by name
#   namedFunctionGraphs    [{asset, graph, label}]         a function graph that must decode by name
#   notReportedAssets      [{asset, label}]                an asset that must have no finding
#   notReportedNames       [{asset, name, label}]          a name that must not be a finding
#   seams                  [[asset, name]]                 names that must be BlueprintEvent seams
#   staleSignatureGraphs   [{asset, graph, label}]         a signature graph with no dispatcher
#   plants                 [{file, class, decl, asset, name, kind}]  a declaration replanted into
#                          `file` (relative to Script/) that must be caught as `kind`
#   seamPlants             [{file, class, decl, asset, name, spec}]  a replant that must be a seam
#   variableControl        {asset, file, class}  the asset that supplies one SAFE and one
#                          DESTRUCTIVE variable, and the script class to replant them into
# =================================================================================================
def _inject(path, class_name, decl):
    with io.open(path, encoding="utf-8") as handle:
        text = handle.read()
    m = re.search(r'^\s*class\s+%s\b[^\n]*\n\{' % re.escape(class_name), text, re.M)
    if not m:
        m = re.search(r'^\s*class\s+%s\b[^{]*\{' % re.escape(class_name), text, re.M)
    if not m:
        raise SystemExit("SELF-TEST BROKEN: class %s not found in %s" % (class_name, path))
    at = m.end()
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(text[:at] + "\n" + decl + text[at:])


def self_test(args):
    st = CFG.get("selfTest")
    if not st:
        print("SELF-TEST SKIPPED - no collision_census.selfTest in .octopus/ue-angelscript.json.")
        return 0
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        print("  %-4s %s%s" % ("PASS" if cond else "FAIL", label, ("  " + detail) if detail else ""))
        if not cond:
            ok = False

    print("%s\n" % st.get("title", "SELF-TEST - the tool must reproduce the verdicts of the"
                                   " control assets."))

    base_args = argparse.Namespace(**vars(args))
    base_args.json = True
    base_args.asset = None
    sink = io.StringIO()
    script_root = os.path.join(ROOT, "Script")
    findings, shadows, seams, scan = run(script_root, base_args, sink)

    print("[1] the parser reaches the assets at all")
    if "minScriptParented" in st:
        n = len([r for r in scan.blueprints.values() if r.script_class])
        check(st.get("minScriptParentedLabel",
                     "more than %d script-parented Blueprints found" % st["minScriptParented"]),
              n > st["minScriptParented"], "%d" % n)
    for c in st.get("ancestry", []):
        rec = scan.blueprints.get(c["asset"])
        check(c.get("label", "%s resolves to %s" % (c["asset"], c["scriptClass"])),
              rec is not None and rec.script_class == c["scriptClass"],
              str(rec and rec.script_class))
    check("no script-parented Blueprint failed to read", not scan.body_failed,
          "; ".join("%s: %s" % (a, w) for a, w in scan.body_failed[:3]))

    print("\n[2] the graphs of the control assets decode")
    for c in st.get("functionGraphs", []):
        rec = scan.blueprints.get(c["asset"])
        absent = c.get("absent", [])
        check(c.get("label", "%s has %d function graphs and no %s"
                    % (c["asset"], c["count"], ", ".join(absent))),
              rec is not None and len(rec.function_graphs) == c["count"]
              and not [g for g in absent if g in rec.function_graphs],
              str(rec and rec.function_graphs))
    for c in st.get("graphCounts", []):
        rec = scan.blueprints.get(c["asset"])
        want = tuple(c["counts"])
        check(c.get("label", "%s: %d function graphs, %d macros, %d ubergraph pages, %d delegates"
                    % ((c["asset"],) + want)),
              rec is not None and (len(rec.function_graphs), len(rec.macro_graphs),
                                   len(rec.ubergraph_pages), len(rec.delegate_graphs)) == want,
              str(rec and (len(rec.function_graphs), len(rec.macro_graphs),
                           len(rec.ubergraph_pages), len(rec.delegate_graphs))))
    for c in st.get("customEvents", []):
        rec = scan.blueprints.get(c["asset"])
        check(c.get("label", "%s custom events decode by name" % c["asset"]),
              rec is not None and c["event"] in rec.custom_events,
              str(rec and rec.custom_events))
    for c in st.get("namedFunctionGraphs", []):
        rec = scan.blueprints.get(c["asset"])
        check(c.get("label", "%s's function graph really is named '%s'" % (c["asset"], c["graph"])),
              rec is not None and c["graph"] in rec.function_graphs,
              str(rec and rec.function_graphs))

    print("\n[3] false positives the tool must NOT report")
    for c in st.get("notReportedAssets", []):
        check(c.get("label", "%s is not reported" % c["asset"]),
              not [f for f in findings if f.asset == c["asset"]],
              str([f.name for f in findings if f.asset == c["asset"]]))
    for c in st.get("notReportedNames", []):
        check(c.get("label", "%s::%s is not reported" % (c["asset"], c["name"])),
              not [f for f in findings if f.asset == c["asset"] and f.name == c["name"]])
    seam_names = {(s.asset, s.name) for s in seams}
    for asset, name in st.get("seams", []):
        check("%s::%s is classified a BlueprintEvent SEAM, not work" % (asset, name),
              (asset, name) in seam_names)
    check("no BlueprintEvent seam leaked into the findings list",
          not ({(f.asset, f.name) for f in findings} & seam_names))
    for c in st.get("staleSignatureGraphs", []):
        asset, graph = c["asset"], c["graph"]
        check(c.get("label", "%s::%s is a stale signature graph and is absent from the findings"
                    % (asset, graph)),
              (asset, graph) in scan.stale_delegate_graphs
              and not [f for f in findings if f.asset == asset and f.name == graph],
              str([f.kind for f in findings if f.asset == asset]))

    print("\n[4] replant real defects into a COPY of Script/ - a clean sheet must not survive them")
    plants = st.get("plants", [])
    tmp = tempfile.mkdtemp(prefix="collision_census_selftest_")
    try:
        copy = os.path.join(tmp, "Script")
        shutil.copytree(script_root, copy)
        for p in plants:
            _inject(os.path.join(copy, p["file"].replace("/", os.sep)), p["class"], p["decl"])
        sink = io.StringIO()
        planted, pshadows, _pseams, _pscan = run(copy, base_args, sink)
        got = {(f.asset, f.name): f for f in planted + pshadows}
        for p in plants:
            f = got.get((p["asset"], p["name"]))
            check("replanted %s::%s is caught as a %s" % (p["asset"], p["name"], p["kind"]),
                  f is not None and f.kind == p["kind"],
                  "" if f is not None and f.kind == p["kind"] else "got %s" % (f and f.kind))
        for p in plants:
            if p["kind"] != "macro graph":
                continue
            key = (p["asset"], p["name"])
            check("the replanted MACRO is a shadow, not a collision (a macro emits no UFunction)",
                  key in {(h.asset, h.name) for h in pshadows}
                  and key not in {(f.asset, f.name) for f in planted})
        # A dispatcher is a PROPERTY, and a project need not carry one on disk, so the control is
        # an in-memory record against an in-memory class.
        plant_cls = ScriptClass("UPlantBase", None, "<memory>")
        plant_cls.props["Fired"] = "FFiredSignature"
        plant_rec = BPRecord("PlantAsset", "<memory>")
        plant_rec.script_class = "UPlantBase"
        plant_rec.delegate_graphs = ["Fired"]
        plant_rec.variables = [BPVar("Fired", "mcdelegate", "", "", "")]
        df, dh, _ds = compare(plant_rec, {"UPlantBase": plant_cls})
        check("an mcdelegate variable colliding with a script UPROPERTY is reported ONCE, as a"
              " dispatcher",
              len(df + dh) == 1 and df[0].kind == "dispatcher",
              str([(f.name, f.kind) for f in df + dh]))
        plant_rec.variables = []
        gf, gh, _gs = compare(plant_rec, {"UPlantBase": plant_cls})
        check("the same signature graph with NO mcdelegate variable is not reported",
              not (gf + gh), str([(f.name, f.kind) for f in gf + gh]))

        for n, p in enumerate(st.get("seamPlants", [])):
            copy2 = os.path.join(tmp, "Script2_%d" % n)
            shutil.copytree(script_root, copy2)
            _inject(os.path.join(copy2, p["file"].replace("/", os.sep)), p["class"], p["decl"])
            sink = io.StringIO()
            pf, ph, ps, _ = run(copy2, base_args, sink)
            key = (p["asset"], p["name"])
            check("the SAME name declared %s becomes a seam, not a collision" % p["spec"],
                  key in {(s.asset, s.name) for s in ps}
                  and key not in {(f.asset, f.name) for f in pf + ph})

        vc = st.get("variableControl")
        if vc:
            copy3 = os.path.join(tmp, "Script3")
            shutil.copytree(script_root, copy3)
            controls = _pick_variable_controls(scan, vc["asset"])
            check("%s supplies both a SAFE and a DESTRUCTIVE variable control" % vc["asset"],
                  len(controls) == 2, str([(v.name, v.type_text) for v in controls.values()]))
            for v in controls.values():
                _inject(os.path.join(copy3, vc["file"].replace("/", os.sep)), vc["class"],
                        "\tUPROPERTY()\n\tbool %s = false;\n" % v.name)
            sink = io.StringIO()
            vf, _vh, _vs, _ = run(copy3, base_args, sink)
            for want, v in controls.items():
                f = [x for x in vf if x.asset == vc["asset"] and x.name == v.name]
                check("replanted variable %s (%s) is caught and rated %s"
                      % (v.name, v.type_text, want),
                      len(f) == 1 and f[0].severity == want,
                      "" if len(f) == 1 and f[0].severity == want else str([x.severity for x in f]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%s" % ("SELF-TEST PASSED" if ok else "SELF-TEST FAILED - do not trust this run"))
    return 0 if ok else 2


def _pick_variable_controls(scan, asset):
    """severity -> one real variable of that severity on `asset`, chosen from live data."""
    out = {}
    for v in scan.blueprints[asset].variables:
        out.setdefault("DESTRUCTIVE" if v.destructive else "SAFE", v)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--all", action="store_true", help="also list every asset that is clean")
    ap.add_argument("--vars-only", action="store_true", help="variables only")
    ap.add_argument("--graphs-only", action="store_true")
    ap.add_argument("--asset", help="substring filter on the asset name")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--script-root", default=os.path.join(ROOT, "Script"))
    ap.add_argument("--list-limit", type=int, default=12)
    args = ap.parse_args()

    if args.self_test:
        sys.exit(self_test(args))

    findings, _shadows, _seams, scan = run(args.script_root, args)
    # An asset that could not be read is NOT a clean asset. Exit 2 rather than 0 for it, so a
    # scripted caller cannot read "no collisions" off a run that measured nothing.
    if scan.body_failed or scan.unparsable:
        sys.exit(2)
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
