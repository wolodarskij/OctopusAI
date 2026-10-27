# -*- coding: utf-8 -*-
"""Parse a whole-Blueprint T3D export into a per-graph node/pin/link model.

Why this exists alongside `t3d_dump.py`: that script keys nodes by bare name in a flat
dict, which is correct for a single-graph dump but wrong for a whole asset. In a large
Blueprint many node names repeat across graphs — `K2Node_FunctionEntry_0` appears once per
function — so a flat map silently merges them. This scopes every node to its owning graph.

`PinId` is not globally unique either: copied nodes carry the same pin ids, and a global
id->pin map collapses many pins onto one key, resolving links to the wrong node. Links are
therefore resolved per graph by (node name, pin id), which is exact because a link never
crosses a graph boundary and `LinkedTo` already names its node.

Reads the text produced by the project's asset-text export toolset (see `export_bp_t3d.py`),
which dumps the live object out of memory and so renders graphs the live editor refuses to
read (a node whose function reference cannot be resolved raises and kills `read_graph_dsl`
for its entire graph).

Usage:
    python tools/t3d_graphs.py <t3d.txt> --outdir <dir>     # one JSON per graph + index
    python tools/t3d_graphs.py <t3d.txt> --graph <Name>     # pretty-print one graph
    python tools/t3d_graphs.py <t3d.txt> --summary          # per-graph node counts
"""
import argparse
import codecs
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

DECL = re.compile(r'^(\s*)Begin Object Class=/Script/[A-Za-z]+\.([A-Za-z0-9_]+) Name="([^"]+)"')
BODY = re.compile(r'^(\s*)Begin Object Name="([^"]+)"')
END = re.compile(r'^(\s*)End Object')
PIN = re.compile(r'CustomProperties Pin \((.*)\)\s*$')


def field(s, key):
    # Escape-aware: a quoted T3D value may contain \" of its own. A plain [^"]* stops
    # at the first escaped quote and silently returns a truncated value -- a string pin
    # holding <img id="Body"/> came back as <img id=\ and read like a broken literal.
    m = re.search(re.escape(key) + r'="((?:[^"\\]|\\.)*)"', s)
    if m:
        return m.group(1).replace('\\"', '"').replace('\\\\', '\\')
    m = re.search(re.escape(key) + r'=([^,\)]*)', s)
    return m.group(1) if m else None


# FText pin literals. `DefaultValue` holds them with the inner quotes escaped, so the
# `[^"]*` in field() stops at the first \" and yields a truncated `LOCTABLE(\`. That
# truncation makes them look as if they were not exported at all. `DefaultTextValue` carries
# the same literal unescaped, so read that instead and match balanced parentheses rather
# than stopping at the first comma (every one of these forms contains commas).
TEXTVAL = re.compile(r'DefaultTextValue=((?:NSLOCTEXT|LOCTABLE|INVTEXT)\()')


def text_default(s):
    m = TEXTVAL.search(s)
    if not m:
        return None
    i, depth, in_str, esc = m.end(), 1, False, False
    while i < len(s) and depth:
        c = s[i]
        if esc:
            esc = False
        elif c == '\\':
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
        i += 1
    return s[m.start(1):i]


def read_dump(path):
    """Read a T3D export, sniffing the BOM for its encoding.

    `export_version_text` does not emit one encoding consistently -- it writes plain
    UTF-8 for some assets and UTF-16LE (with a BOM) for others. Decoding a UTF-16 dump
    as UTF-8 does not raise: every other byte is a NUL, which is a perfectly legal
    UTF-8 character, so the text survives as "B\\x00e\\x00g\\x00i\\x00n" and simply
    matches no pattern. With `errors='replace'` on top, the result is a silent zero --
    0 graphs parsed out of a 1.5 MB file that is entirely intact.
    """
    raw = io.open(path, 'rb').read()
    for bom, enc in ((codecs.BOM_UTF8, 'utf-8-sig'),
                     (codecs.BOM_UTF16_LE, 'utf-16'),
                     (codecs.BOM_UTF16_BE, 'utf-16')):
        if raw.startswith(bom):
            return raw.decode(enc)
    return raw.decode('utf-8', errors='replace')


def parse(path):
    lines = read_dump(path).split('\n')

    # Pass 1 - declarations give each node its class and its owning graph.
    node_class = {}
    declared_graphs = []
    stack = []
    for line in lines:
        m = DECL.match(line)
        if m:
            indent, cls, name = len(m.group(1)), m.group(2), m.group(3)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            graph = None
            for ind, c, n in stack:
                if c == 'EdGraph':
                    graph = n
            if cls == 'EdGraph':
                graph = name
                declared_graphs.append(name)
            else:
                node_class[(graph, name)] = cls
            stack.append((indent, cls, name))
            continue
        m = END.match(line)
        if m:
            indent = len(m.group(1))
            while stack and stack[-1][0] >= indent:
                stack.pop()

    # Pass 2 - bodies, scoped the same way.
    # Seed from the declaration pass so graphs with no nodes at all still appear;
    # omitting them would understate coverage.
    graphs = {}
    for (g, _n) in node_class:
        if g:
            graphs.setdefault(g, {'graph': g, 'nodes': []})
    for gname in declared_graphs:
        graphs.setdefault(gname, {'graph': gname, 'nodes': []})
    stack = []
    cur = None
    for line in lines:
        m = BODY.match(line)
        if m:
            indent, name = len(m.group(1)), m.group(2)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            graph = None
            for ind, n in stack:
                if n in graphs or (None, n) in node_class or any(g == n for g, _ in node_class):
                    graph = n
            enclosing = stack[-1][1] if stack else None
            stack.append((indent, name))
            if enclosing is None:
                cur = None
                continue
            g = enclosing
            graphs.setdefault(g, {'graph': g, 'nodes': []})
            cur = {'name': name, 'graph': g,
                   'class': node_class.get((g, name), '?'),
                   'pins': [], 'props': {}}
            graphs[g]['nodes'].append(cur)
            continue
        m = END.match(line)
        if m:
            indent = len(m.group(1))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            cur = None
            continue
        if cur is None:
            continue

        pm = PIN.search(line)
        if pm:
            s = pm.group(1)
            links = []
            for blob in re.findall(r'LinkedTo=\(([^)]*)\)', s):
                for tok in blob.split(','):
                    tok = tok.strip()
                    parts = tok.split()
                    if len(parts) == 2:
                        links.append({'node': parts[0], 'pin': parts[1]})
            cur['pins'].append({
                'id': field(s, 'PinId'),
                'name': field(s, 'PinName'),
                'dir': field(s, 'Direction') or 'EGPD_Input',
                'type': field(s, 'PinType.PinCategory'),
                'sub': field(s, 'PinType.PinSubCategoryObject') or '',
                'default': text_default(s) or field(s, 'DefaultValue'),
                'defobj': field(s, 'DefaultObject'),
                # Container kind and value type. Without these a TMap pin reads as its key
                # type alone and an array parameter reads as a scalar, so every pass had to
                # reopen the T3D to tell `TMap<FName,FString>` from `FName`.
                'container': field(s, 'PinType.ContainerType'),
                'valuetype': field(s, 'PinType.PinValueType'),
                'const': 'bIsConst=True' in s,
                'ref': 'bIsReference=True' in s,
                'links': links,
            })
            continue

        st = line.strip()
        # `LocalVariables(N)=(VarName="x",VarType=(PinCategory="int"),...)` — indexed, so it
        # needs prefix matching rather than the exact `key=` test below. These are the
        # function's declared locals, including ones no node reads; without this the only
        # trace of a local is a MemberScope on some Get/Set, which misses the unused ones.
        if st.startswith('LocalVariables('):
            vn = re.search(r'VarName="([^"]+)"', st)
            vt = re.search(r'PinCategory="([^"]+)"', st)
            if vn:
                cur.setdefault('locals', []).append(
                    {'name': vn.group(1),
                     'type': vt.group(1) if vt else None,
                     'array': 'ContainerType=Array' in st})
            continue
        for key in ('NodePosX', 'NodePosY', 'NodeGuid', 'NodeComment', 'bIsPureFunc',
                    'bDefaultsToPureFunc', 'FunctionReference', 'VariableReference',
                    # The export writes `MacroGraphReference=(MacroGraph=...,GraphGuid=...)`.
                    # A bare `MacroGraph` key never matches; every macro instance carries
                    # this one.
                    'EventReference', 'MacroGraphReference', 'CustomFunctionName', 'VariableGuid',
                    'EnabledState', 'DelegateReference', 'StructType', 'DelegatePropertyName',
                    # K2Node_CreateDelegate names its handler here and nowhere else. Without
                    # these two the bound function is only inferable from wiring.
                    'SelectedFunctionName', 'SelectedFunctionGuid', 'ErrorType',
                    # Entry points that are NOT K2Node_Event. A K2Node_InputKey starts its
                    # own exec chain, so a graph can hold live nodes reachable from no
                    # event at all, for example a chain that hangs off a LeftMouseButton
                    # node. Without these keys such an entry point is unnameable.
                    'InputKey', 'InputAxisKey', 'InputActionName', 'InputAxisName',
                    'AxisKey', 'bConsumeInput', 'ComponentPropertyName',
                    # An event's RPC kind is decided here, never by its name -- one class
                    # inverts the Server/Multicast suffixes, another's names are simply
                    # unrelated to the flags. Note an ABSENT FunctionFlags line means the
                    # UK2Node_CustomEvent default (0x0C020000: Public|BlueprintCallable|
                    # BlueprintEvent), i.e. not replicated at all.
                    'FunctionFlags', 'ExtraFlags'):
            if st.startswith(key + '='):
                cur['props'][key] = st[len(key) + 1:]

    # Derived, readable fields.
    for g in graphs.values():
        for n in g['nodes']:
            p = n['props']
            fr = p.get('FunctionReference', '')
            vr = p.get('VariableReference', '')
            ev = p.get('EventReference', '')
            mn = re.search(r'MemberName="([^"]+)"', fr)
            if mn:
                n['call'] = mn.group(1)
                mp = re.search(r"MemberParent=\"[^']*'([^']*)'\"", fr)
                n['callOwner'] = mp.group(1).split('.')[-1] if mp else 'self'
                n['pure'] = 'True' in (p.get('bDefaultsToPureFunc', '')
                                       + p.get('bIsPureFunc', ''))
            vn = re.search(r'MemberName="([^"]+)"', vr)
            if vn:
                n['var'] = vn.group(1)
                n['varSelf'] = 'bSelfContext=True' in vr
                mp = re.search(r"MemberParent=\"[^']*'([^']*)'\"", vr)
                n['varOwner'] = mp.group(1).split('.')[-1] if mp else 'self'
            en = re.search(r'MemberName="([^"]+)"', ev)
            if en:
                n['event'] = en.group(1)
            if 'CustomFunctionName' in p:
                n['event'] = p['CustomFunctionName'].strip('"')
            mgr = p.get('MacroGraphReference', '')
            mg = re.search(r"MacroGraph=\"[^']*'([^']*)'\"", mgr)
            if mg:
                path = mg.group(1)
                n['macro'] = path.split(':')[-1]
                # StandardMacros holds the engine's ForEachLoop/IsValid/Select; anything
                # else is a macro declared by this Blueprint, which makes the instance an
                # attributable call site for that local macro.
                n['macroLocal'] = 'StandardMacros' not in path
            mgg = re.search(r'GraphGuid=([0-9A-F]{32})', mgr)
            if mgg:
                n['macroGuid'] = mgg.group(1)
            if 'NodeComment' in p:
                n['comment'] = p['NodeComment'].strip('"')
            n['pos'] = [int(p.get('NodePosX', '0') or 0), int(p.get('NodePosY', '0') or 0)]
            n['guid'] = p.get('NodeGuid')

    # Resolve links PER GRAPH, keyed by (node name, pin id).
    #
    # PinId is NOT globally unique - copied nodes carry the same pin ids, which is why
    # `animgraph_dump.py` keys on (node, pinId) too. A global id->pin map silently
    # collapses pins onto one key and would resolve links to the wrong node.
    # Links never cross a graph boundary, and LinkedTo already names the target node, so
    # scoping the lookup to the graph is both correct and sufficient.
    total_pins = 0
    unresolved = 0
    for g in graphs.values():
        local = {}
        for n in g['nodes']:
            for pin in n['pins']:
                total_pins += 1
                local[(n['name'], pin['id'])] = {'node': n['name'], 'pin': pin['name'],
                                                 'dir': pin['dir'], 'type': pin['type']}
        for n in g['nodes']:
            for pin in n['pins']:
                for l in pin['links']:
                    tgt = local.get((l['node'], l['pin']))
                    if tgt:
                        l['toNode'] = tgt['node']
                        l['toPin'] = tgt['pin']
                    else:
                        unresolved += 1
                        l['unresolved'] = True
    stats = {'graphs': len(graphs),
             'nodes': sum(len(g['nodes']) for g in graphs.values()),
             'pins': total_pins,
             'unresolvedLinks': unresolved}
    return graphs, stats


def label(n):
    c = (n['class'] or '?').replace('K2Node_', '')
    if 'event' in n:
        return 'EVENT %s' % n['event']
    if 'macro' in n:
        return 'MACRO %s' % n['macro']
    if c == 'CallFunction':
        return 'call %s::%s%s' % (n.get('callOwner', '?'), n.get('call', '?'),
                                  ' [pure]' if n.get('pure') else '')
    if c == 'VariableGet':
        return 'get %s' % n.get('var', '?')
    if c == 'VariableSet':
        return 'SET %s' % n.get('var', '?')
    if c == 'IfThenElse':
        return 'BRANCH'
    if c == 'Comment':
        return '// %s' % n.get('comment', '')
    return c


SECTION = re.compile(
    r'^\s*(FunctionGraphs|MacroGraphs|UbergraphPages|DelegateSignatureGraphs|IntermediateGeneratedGraphs|EventGraphs)'
    r'\(\d+\)="[^\']*\'([^\']+)\'"')


def graph_sections(path):
    """Map graph name -> the asset property that lists it.

    A nested `Begin Object` is treated as a graph by the body pass, which is right for an
    EdGraph and wrong for anything else that nests -- a UserWidget's `WidgetTree` and every
    panel inside it parse as graphs too. On a widget that can turn 2 real graphs into 15,
    silently inflating any census built on the count.

    The asset's own `FunctionGraphs(N)=` / `UbergraphPages(N)=` properties are the
    authoritative list, so record them and let callers filter. Anything absent here is
    reported as section `None` rather than dropped, because macro-expansion and generated
    graphs are legitimately unlisted.
    """
    out = {}
    for line in read_dump(path).split('\n'):
        m = SECTION.match(line)
        if m:
            out[m.group(2).split(':')[-1]] = m.group(1)
    return out


def main():
    _project.optional_project_root()
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--outdir')
    ap.add_argument('--graph')
    ap.add_argument('--summary', action='store_true')
    ap.add_argument('--real-only', action='store_true',
                    help='only graphs listed in the asset\'s own *Graphs/UbergraphPages properties')
    a = ap.parse_args()

    graphs, stats = parse(a.path)
    sections = graph_sections(a.path)
    if a.real_only:
        graphs = {k: v for k, v in graphs.items() if k in sections}

    if a.summary:
        print('graphs: %(graphs)d   nodes: %(nodes)d   pins: %(pins)d   unresolved links: %(unresolvedLinks)d' % stats)
        print('  (%d of %d listed in the asset\'s own graph properties)'
              % (sum(1 for k in graphs if k in sections), len(graphs)))
        for name in sorted(graphs, key=lambda k: -len(graphs[k]['nodes'])):
            print('  %-34s %4d  %s' % (name, len(graphs[name]['nodes']),
                                       sections.get(name, '-')))
        return

    if a.graph:
        g = graphs.get(a.graph)
        if not g:
            print('no such graph. available: %s' % ', '.join(sorted(graphs)))
            return
        for n in g['nodes']:
            print('%-30s %s' % (n['name'], label(n)))
            for p in n['pins']:
                d = 'OUT' if p['dir'] == 'EGPD_Output' else 'in '
                tgt = ''
                if p['links']:
                    tgt = ' -> ' + ', '.join('%s.%s' % (l.get('toNode', l['node']),
                                                        l.get('toPin', '?')) for l in p['links'])
                dv = ''
                if not p['links'] and p['dir'] != 'EGPD_Output':
                    v = p.get('defobj') or p.get('default')
                    if v not in (None, '', '()'):
                        dv = ' = %s' % v
                print('     %s %-26s %-9s%s%s' % (d, p['name'], p['type'] or '', tgt, dv))
            print()
        return

    if a.outdir:
        os.makedirs(a.outdir, exist_ok=True)
        index = {}
        for name, g in graphs.items():
            safe = re.sub(r'[^A-Za-z0-9_.-]', '_', name)
            with io.open(os.path.join(a.outdir, safe + '.json'), 'w', encoding='utf-8') as f:
                json.dump(g, f, indent=1)
            index[name] = {'file': safe + '.json', 'nodes': len(g['nodes']),
                           'section': sections.get(name)}
        with io.open(os.path.join(a.outdir, '_index.json'), 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=1)
        print('wrote %d graphs to %s' % (len(graphs), a.outdir))
        return

    print('nothing to do; pass --summary, --graph or --outdir')


if __name__ == '__main__':
    main()
