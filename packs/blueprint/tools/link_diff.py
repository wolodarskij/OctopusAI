# -*- coding: utf-8 -*-
"""Offline census of DATA LINKS a Blueprint LOST while every node survived.

THE DEFECT THIS FINDS
---------------------
A variable is retyped (for example from a Blueprint interface to an object array). A node that read
it stops producing an interface-typed output, it can no longer feed an interface `self` pin, and the
engine drops the link. Every node survives in both exports. Only the link differs, and the graph
left behind is internally consistent, compiles, and reads as authored dead code. A port that reads
the later graph then records the call as a deliberate no-op.

So the tell is: A DATA LINK GONE WITH THE NODES UNCHANGED.

WHY THE ISLAND CENSUS CANNOT ANSWER IT
--------------------------------------
`tools/exec_islands.py` proves authored-dead-code by exec-REACHABILITY identity against the oldest
backup. That is the right proof for an exec island and blind here: these nodes were exec-reachable
before and after, so no island ever formed. The two instruments are complementary, not redundant.

WHAT IT DOES
------------
For every asset with two or more exports in the T3D directory (`t3dDir` of `.octopus/blueprint.json`,
default `T3D`), per GRAPH, diff the earliest export that holds the graph against the LATEST export
that still holds it, and report links that are

    * gone in the later export,
    * NOT exec (an exec loss is an ordinary rewire and drowns the signal),
    * between two nodes that SURVIVE IN BOTH, matched structurally (class + called/read symbol),
    * and not merely re-pinned (the same node pair still linked at the same pin category).

Findings are tiered. T1 is the signal: the graph's node set is byte-for-byte the same set and the lost
pin carries a `/Game/` type, i.e. exactly a Blueprint class or UserDefinedStruct that was retyped.

TRAPS
-----
* `<n>` in `Text<Asset>-<n>.txt` is a PER-RUN COUNTER, NOT AGE: `-9` can be older than `-67`, and
  the counter resets when the editor restarts. Everything here sorts by `os.path.getmtime`.
* THE T3D DIRECTORY IS MIXED-ENCODING. `raw.decode('utf-16')` on an ASCII file SUCCEEDS, returns CJK
  garbage and reports a confident zero. `t3d_graphs.read_dump` sniffs the BOM; `--self-test` puts a
  control string through both encodings and refuses to run if either loses it.
* PIN GUIDS ARE NOT UNIQUE and node names repeat across graphs. `t3d_graphs.parse` resolves `LinkedTo`
  per graph by the `(node-name, pin-id)` PAIR, which is why this reuses it rather than re-deriving.
* AN EXPORT DROPS ORPHANED PINS ENTIRELY, so a pin orphaned by the later edit is simply absent there.
  That is not a hole in this instrument — the link is gone either way and gets reported — but it does
  mean the later export cannot be asked what the pin used to be called.
* THE LATEST EXPORT OFTEN NO LONGER HOLDS THE GRAPH. Diffing earliest-against-latest whole-asset
  therefore reports the entire graph as vanished and finds nothing. Resolution is PER GRAPH: the last
  export that still contains it.

WHAT IT CANNOT SEE — declared, because every instrument here has a blind spot
----------------------------------------------------------------------------
1. A DEFECT WITH NO EXPORT-TO-EXPORT DELTA. A pin can be wired in BOTH surviving exports while a
   port's comment claims it is not; the wrong verdict is then in the comment, not in the graph.
   Nothing in a two-export diff can find that. Use `--verify <Asset> <Graph>` to check such a claim
   by hand.
2. AN ASSET WITH ONE EXPORT cannot be diffed at all. The run prints how many those are.
3. A LOSS OLDER THAN THE EARLIEST EXPORT is invisible.
4. A NODE WHOSE SYMBOL CHANGED does not match structurally and its links are excluded, so a loss that
   coincided with a retarget of the same node is dropped rather than reported.
5. THIS IS NOT A REGRESSION PROOF. Most losses are somebody's deliberate edit. The instrument ranks;
   it does not adjudicate.

KNOWN ANSWERS come from the `linkDiff` object of `.octopus/blueprint.json`:

    calibration       [{"asset", "graph", "link": [fromNode, fromPin, toNode, toPin] or null,
                        "expect": true|false, "note"}]   the defects --calibrate must reproduce
    survivors         [[asset, graph, message]]   graphs proved correct; a T1 on one is a tool bug
    knownSignature    {asset: message}            assets whose losses are already known
    knownAdjudicated  [[asset, graph, note]]      findings already judged to owe nothing

    python tools/link_diff.py --self-test        # plant a known loss + controls; REFUSES on failure
    python tools/link_diff.py --calibrate        # the project's calibration cases
    python tools/link_diff.py                    # the sweep, T1 in full
    python tools/link_diff.py --asset BP_ExampleActor
    python tools/link_diff.py --tier 2           # include primitive-typed stable losses
    python tools/link_diff.py --verify BP_ExampleActor "Example Graph"
"""
from __future__ import print_function

import argparse
import codecs
import collections
import io
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project                                                             # noqa: E402
import t3d_graphs as T3D                                                    # noqa: E402
import _t3d_dir                                                             # noqa: E402

ROOT = str(_project.project_root())
DIFFDIR = _t3d_dir.T3D_DIR
STEM_RE = re.compile(r'^Text(.+)-(\d+)\.txt$')

# ---------------------------------------------------------------------------
# Known answers, from `linkDiff` in `.octopus/blueprint.json`. These exist so a run states what it
# reproduced and so the tool cannot quietly re-file work that is already adjudicated.
# ---------------------------------------------------------------------------
_KNOWN = _project.setting('blueprint', 'linkDiff', {}) or {}

# The defects this instrument must reproduce. `expect` says whether a two-export LINK DIFF can see
# it at all — see blind spot 1.
CALIBRATION = [dict(c, link=tuple(c['link']) if c.get('link') else None)
               for c in _KNOWN.get('calibration', [])]

# Graphs PROVED correct as they stand. A T1 finding on one of them is THIS TOOL'S BUG, not a new
# finding, and is labelled so.
SURVIVORS = {(row[0], row[1]): (row[2] if len(row) > 2 else
                                '!! TOOL BUG - this verdict was proved CORRECT')
             for row in _KNOWN.get('survivors', [])}

# Assets that already carry this exact signature and are known, not news.
KNOWN_SIGNATURE = dict(_KNOWN.get('knownSignature', {}))

# Findings already adjudicated as owing nothing.
KNOWN_ADJUDICATED = {(row[0], row[1]): row[2] for row in _KNOWN.get('knownAdjudicated', [])}


# ---------------------------------------------------------------------------
# Reduction: a parsed export -> the minimum needed to diff it.
# ---------------------------------------------------------------------------

def node_sym(n):
    """The symbol a node calls, reads, handles or expands."""
    return n.get('call') or n.get('var') or n.get('event') or n.get('macro') or ''


def node_sig(n):
    """Structural identity of a node, for matching ACROSS two exports: (class, symbol).

    Unreal REUSES FREED NODE NAMES, so `(graph, name)` alone can point at a different node in the
    later export.

    The CLASS half is the survival test. The SYMBOL half is NOT: one node can go `Is Server` ->
    `IsServer` (a rename plus redirect) while the graph is otherwise untouched, and requiring symbol
    equality would demote a real loss out of the top tier. A symbol change is a RETARGET, is itself
    part of the retype family, and is reported as a flag rather than used to exclude.

    The OWNER of a called function is deliberately not part of either half: a retarget changes it
    (`BPI_Example_C::GetName` -> `MyActor::GetName`) on a node that is plainly still the same
    consumer.
    """
    return (n.get('class') or '?', node_sym(n))


def same_symbol(a, b):
    """Rename-tolerant symbol compare. `Is Server` and `IsServer` are the same symbol."""
    norm = lambda s: re.sub(r'[^a-z0-9]', '', (s or '').lower())
    return norm(a) == norm(b)


def reduce_graph(g):
    """-> {'nodes': {name: sig}, 'edges': {edgekey: meta}}.

    An edge is keyed by the UNORDERED pair of its two endpoints, each `(node, pinName)`. Both
    endpoints list the link, so keying unordered collapses the two records into one and makes the
    diff a plain set difference.
    """
    nodes = {}
    for n in g['nodes']:
        nodes[n['name']] = node_sig(n)
    classes = {k: v[0] for k, v in nodes.items()}
    edges = {}
    for n in g['nodes']:
        for p in n['pins']:
            a = (n['name'], p['name'])
            for l in p['links']:
                b = (l.get('toNode', l['node']), l.get('toPin', '?'))
                key = tuple(sorted([a, b]))
                # An unresolved link means the target pin is absent from THIS export (the orphan
                # drop). Keep it, flagged, rather than dropping the record.
                edges[key] = {
                    'cat': p['type'] or '?',
                    'sub': p.get('sub') or '',
                    'unresolved': bool(l.get('unresolved')),
                    'src': a, 'dst': b,
                }
    return {'nodes': nodes, 'classes': classes, 'edges': edges}


def reduce_export(path):
    graphs, stats = T3D.parse(path)
    return {name: reduce_graph(g) for name, g in graphs.items()}, stats


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

def corpus(diffdir=DIFFDIR):
    """-> (stems, singles). stems: name -> [(mtime, path), ...] ascending, len >= 2."""
    by_stem = collections.defaultdict(list)
    for fn in os.listdir(diffdir):
        m = STEM_RE.match(fn)
        if not m:
            continue
        p = os.path.join(diffdir, fn)
        by_stem[m.group(1)].append((os.path.getmtime(p), p))
    multi, single = {}, {}
    for k, v in by_stem.items():
        v.sort()
        (multi if len(v) >= 2 else single)[k] = v
    return multi, single


def diff_stem(files, verbose=False):
    """Diff one asset. `files` is [(mtime, path)] ascending.

    PER-GRAPH resolution of the two endpoints of the comparison:
      * base   = the EARLIEST export that holds the graph,
      * head   = the LATEST export that still holds it.
    Diffing the whole asset earliest-against-latest instead reports a removed graph as a total
    vanishing and finds nothing inside it.

    Every export of the stem is parsed. A few MB parse in well under a second, so the whole corpus
    is minutes, and the alternative (a cap on how far back to look) hides coverage silently.
    """
    base, head = {}, {}
    newest_path = files[-1][1]
    for mt, path in files:                       # ascending: first sighting wins
        red, _ = reduce_export(path)
        for gname, g in red.items():
            if gname not in base:
                base[gname] = (mt, path, g)
            head[gname] = (mt, path, g)          # last sighting wins

    out = []
    # Coverage counters. "Assets reachable" is not "graphs compared", and a filter that silently
    # drops candidates is how a zero becomes a lie here - so both are counted and printed.
    cov = {'graphs': len(base), 'compared': 0, 'single': 0,
           'excludedNodeGone': 0, 'exec': 0}
    for gname, (bmt, bpath, bg) in sorted(base.items()):
        hmt, hpath, hg = head[gname]
        if hpath == bpath:                       # only ever seen in one export
            cov['single'] += 1
            continue
        cov['compared'] += 1
        # STABILITY is about the graph's SHAPE - the same node names carrying the same classes.
        # A node added or removed is an ordinary edit and drops the finding a tier. A symbol RENAME
        # is not a shape change and must not.
        node_set_identical = (bg['classes'] == hg['classes'])
        # A node "survives" when the later export holds the same name with the same CLASS.
        survives = {n for n, c in bg['classes'].items() if hg['classes'].get(n) == c}

        # How many links of each category run between each node pair, before and after. Used to
        # separate a genuine disconnection from a PIN RENAME (`int` -> `NewValue` on IncrementInt,
        # where the wire follows the renamed pin).
        #
        # COUNTING, not membership. A graph can have TWO struct links between one node pair and
        # lose one of them; a membership test calls that a rename because a link of the same
        # category survived, and silently hides a real loss.
        def pair_counts(g):
            c = collections.Counter()
            for key, meta in g['edges'].items():
                c[(frozenset(k[0] for k in key), meta['cat'])] += 1
            return c

        before, after = pair_counts(bg), pair_counts(hg)

        for key, meta in bg['edges'].items():
            if key in hg['edges']:
                continue
            n1, n2 = key[0][0], key[1][0]
            if meta['cat'] == 'exec':
                cov['exec'] += 1
                continue
            if n1 not in survives or n2 not in survives:
                cov['excludedNodeGone'] += 1
                continue
            pk = (frozenset((n1, n2)), meta['cat'])
            repinned = after[pk] >= before[pk]
            retargeted = not (same_symbol(bg['nodes'][n1][1], hg['nodes'][n1][1])
                              and same_symbol(bg['nodes'][n2][1], hg['nodes'][n2][1]))
            out.append({
                'graph': gname,
                'from': '%s.%s' % key[0],
                'to': '%s.%s' % key[1],
                'fromNode': n1, 'toNode': n2,
                'fromSig': bg['nodes'][n1], 'toSig': bg['nodes'][n2],
                'cat': meta['cat'],
                'sub': meta['sub'],
                'nodesOld': len(bg['nodes']), 'nodesNew': len(hg['nodes']),
                'stable': node_set_identical,
                'repinned': repinned,
                'retargeted': retargeted,
                'base': os.path.basename(bpath), 'head': os.path.basename(hpath),
                # Does the graph still exist in the stem's NEWEST export? A graph since removed
                # whole owes nothing on this finding - its behaviour left by deletion, which is a
                # different ledger entry from a silently dropped wire.
                'graphSurvives': (hpath == newest_path),
                'baseTime': bmt, 'headTime': hmt,
            })
    return out, cov


def tier(f):
    """T1 signal / T2 stable-but-primitive / T3 the graph changed shape too.

    The `/Game/` test is what makes T1 the retype family: a pin whose sub-category names an asset is
    a Blueprint class or a UserDefinedStruct, which is exactly what a retype changes. A float or
    an int pin losing a wire is an ordinary edit.
    """
    if f['repinned']:
        return 4
    if f['stable']:
        return 1 if '/Game/' in f['sub'] else 2
    return 3


def short_sub(sub):
    if not sub:
        return ''
    m = re.search(r"'([^']+)'", sub)
    name = (m.group(1) if m else sub).split('.')[-1].split('/')[-1]
    kind = 'UDS' if 'UserDefinedStruct' in sub else ('BP' if 'BlueprintGeneratedClass' in sub else '')
    return ('%s %s' % (kind, name)).strip()


# ---------------------------------------------------------------------------
# Self-test. It plants a known loss and three controls, and REFUSES to report if any assertion fails.
# ---------------------------------------------------------------------------

def _t3d(graphs):
    """Render a minimal but format-faithful T3D. graphs: [(name, [node...])].

    node = (name, class, [(pinName, dir, cat, sub, [(tgtNode, tgtPinId)], pinId)])
    """
    out = ['Begin Object Class=/Script/Engine.Blueprint Name="SelfTest"']
    for gname, nodes in graphs:
        out.append('   Begin Object Class=/Script/Engine.EdGraph Name="%s"' % gname)
        for nname, ncls, _pins in nodes:
            out.append('      Begin Object Class=/Script/BlueprintGraph.%s Name="%s"' % (ncls, nname))
            out.append('      End Object')
        out.append('   End Object')
    for gname, nodes in graphs:
        out.append('   Begin Object Name="%s"' % gname)
        for nname, ncls, pins in nodes:
            out.append('      Begin Object Name="%s"' % nname)
            out.append('         NodeGuid=%s' % ('0' * 32))
            for pname, pdir, pcat, psub, links, pid in pins:
                lt = ''.join('%s %s,' % (t, i) for t, i in links)
                out.append(
                    '         CustomProperties Pin (PinId=%s,PinName="%s",Direction="%s",'
                    'PinType.PinCategory="%s",PinType.PinSubCategoryObject=%s,'
                    'PinType.ContainerType=None,PinType.bIsReference=False,'
                    'LinkedTo=(%s),bOrphanedPin=False,)'
                    % (pid, pname, pdir, pcat, psub or 'None', lt))
            out.append('      End Object')
        out.append('   End Object')
    out.append('End Object')
    return '\n'.join(out) + '\n'


def _selftest_pair():
    """(old_text, new_text) with one planted loss and three controls."""
    IFACE = ("\"/Script/Engine.BlueprintGeneratedClass'/Game/Blueprints/"
             "BPI_Example.BPI_Example_C'\"")
    P = lambda n: ('%032X' % n)

    def planted(linked):
        # PLANT: the data link Get.Output -> Call.self, on a /Game/ typed pin, with the exec chain
        # left intact on both sides so the loss cannot be inferred from exec flow.
        return ('Planted', [
            ('K2Node_FunctionEntry_0', 'K2Node_FunctionEntry',
             [('then', 'EGPD_Output', 'exec', None, [('K2Node_CallFunction_0', P(3))], P(1))]),
            ('K2Node_VariableGet_0', 'K2Node_VariableGet',
             [('Output', 'EGPD_Output', 'object', IFACE,
               [('K2Node_CallFunction_0', P(4))] if linked else [], P(2))]),
            ('K2Node_CallFunction_0', 'K2Node_CallFunction',
             [('execute', 'EGPD_Input', 'exec', None, [('K2Node_FunctionEntry_0', P(1))], P(3)),
              ('self', 'EGPD_Input', 'object', IFACE,
               [('K2Node_VariableGet_0', P(2))] if linked else [], P(4))]),
        ])

    def control_stable():
        # CONTROL A: nothing changes. Must produce no finding at all.
        return ('ControlStable', [
            ('K2Node_VariableGet_1', 'K2Node_VariableGet',
             [('Output', 'EGPD_Output', 'object', IFACE, [('K2Node_CallFunction_1', P(6))], P(5))]),
            ('K2Node_CallFunction_1', 'K2Node_CallFunction',
             [('self', 'EGPD_Input', 'object', IFACE, [('K2Node_VariableGet_1', P(5))], P(6))]),
        ])

    def control_removed(keep):
        # CONTROL B: the consumer node is DELETED along with its link. An ordinary edit. Must NOT be
        # reported, because one endpoint does not survive.
        nodes = [('K2Node_VariableGet_2', 'K2Node_VariableGet',
                  [('Output', 'EGPD_Output', 'object', IFACE,
                    [('K2Node_CallFunction_2', P(8))] if keep else [], P(7))])]
        if keep:
            nodes.append(('K2Node_CallFunction_2', 'K2Node_CallFunction',
                          [('self', 'EGPD_Input', 'object', IFACE,
                            [('K2Node_VariableGet_2', P(7))], P(8))]))
        return ('ControlRemoved', nodes)

    def control_repin(pinname):
        # CONTROL C: the pin is RENAMED and the wire follows it. The `NewValue`/`int` case. Must be
        # classified repinned (tier 4), never as a loss.
        return ('ControlRepin', [
            ('K2Node_VariableGet_3', 'K2Node_VariableGet',
             [('Output', 'EGPD_Output', 'int', None, [('K2Node_CallFunction_3', P(10))], P(9))]),
            ('K2Node_CallFunction_3', 'K2Node_CallFunction',
             [(pinname, 'EGPD_Input', 'int', None, [('K2Node_VariableGet_3', P(9))], P(10))]),
        ])

    def control_exec(linked):
        # CONTROL D: an EXEC link is lost with both nodes surviving. Deliberately out of scope.
        return ('ControlExec', [
            ('K2Node_FunctionEntry_1', 'K2Node_FunctionEntry',
             [('then', 'EGPD_Output', 'exec', None,
               [('K2Node_CallFunction_4', P(12))] if linked else [], P(11))]),
            ('K2Node_CallFunction_4', 'K2Node_CallFunction',
             [('execute', 'EGPD_Input', 'exec', None,
               [('K2Node_FunctionEntry_1', P(11))] if linked else [], P(12))]),
        ])

    def control_double(both):
        # CONTROL E: TWO links of the SAME category between the SAME node pair, one of them lost.
        # A membership test for "is this pair still linked at this category" calls that a rename and
        # hides a real loss. The count must decide.
        return ('ControlDouble', [
            ('K2Node_FunctionEntry_2', 'K2Node_FunctionEntry',
             [('fightResult1', 'EGPD_Output', 'struct', IFACE,
               ([('K2Node_CallFunction_5', P(14))] if both else []) + [('K2Node_CallFunction_5', P(15))],
               P(13))]),
            ('K2Node_CallFunction_5', 'K2Node_CallFunction',
             [('a', 'EGPD_Input', 'struct', IFACE,
               [('K2Node_FunctionEntry_2', P(13))] if both else [], P(14)),
              ('b', 'EGPD_Input', 'struct', IFACE,
               [('K2Node_FunctionEntry_2', P(13))], P(15))]),
        ])

    old = _t3d([planted(True), control_stable(), control_removed(True),
                control_repin('int'), control_exec(True), control_double(True)])
    new = _t3d([planted(False), control_stable(), control_removed(False),
                control_repin('NewValue'), control_exec(False), control_double(False)])
    return old, new


def self_test(quiet=False):
    """-> True/False. Every assertion is named, so a failure says which one."""
    fails, checks = [], []

    def check(ok, label):
        checks.append((bool(ok), label))
        if not ok:
            fails.append(label)

    tmp = tempfile.mkdtemp(prefix='linkdiff-selftest-')
    old, new = _selftest_pair()

    # --- encoding control. `raw.decode('utf-16')` on an ASCII file SUCCEEDS and returns garbage, so
    # the reader is put through BOTH encodings and required to bring the marker back either way.
    marker = 'Example Action Graph'
    probe = old.replace('Planted', marker)
    p_utf8 = os.path.join(tmp, 'TextEnc-1.txt')
    io.open(p_utf8, 'w', encoding='utf-8').write(probe)
    p_u16 = os.path.join(tmp, 'TextEnc-2.txt')
    with io.open(p_u16, 'wb') as fh:
        fh.write(codecs.BOM_UTF16_LE + probe.encode('utf-16-le'))
    check(marker in T3D.read_dump(p_utf8), 'encoding: UTF-8 export decodes')
    check(marker in T3D.read_dump(p_u16), 'encoding: UTF-16LE export decodes')
    g8, _ = T3D.parse(p_utf8)
    g16, _ = T3D.parse(p_u16)
    check(marker in g8 and marker in g16 and
          len(g8[marker]['nodes']) == len(g16[marker]['nodes']) == 3,
          'encoding: both encodings parse to the same graph')

    # --- mtime ordering control. The file NUMBER must never decide age: the "old" text is written to
    # the HIGHER number and given the OLDER mtime, which is the real-world shape.
    a = os.path.join(tmp, 'TextSelfTest-77.txt')     # older content, higher number
    b = os.path.join(tmp, 'TextSelfTest-3.txt')      # newer content, lower number
    io.open(a, 'w', encoding='utf-8').write(old)
    io.open(b, 'w', encoding='utf-8').write(new)
    os.utime(a, (1000000, 1000000))
    os.utime(b, (2000000, 2000000))

    multi, _single = corpus(tmp)
    check('SelfTest' in multi, 'corpus: the stem is found')
    check(multi.get('SelfTest', [(0, '')])[0][1] == a,
          'corpus: EARLIEST MTIME is the base, not the lowest file number')

    findings, _cov = diff_stem(multi['SelfTest'])
    tiers = collections.Counter(tier(f) for f in findings)
    t1 = [f for f in findings if tier(f) == 1 and f['graph'] == 'Planted']

    check(len(t1) == 1, 'planted: exactly one T1 finding in the planted graph (got %d)' % len(t1))
    if t1:
        f = t1[0]
        check(f['stable'], 'planted: the graph is reported node-stable')
        check('BPI_Example_C' in f['sub'], 'planted: the retyped pin type is carried through')
        check(set([f['fromNode'], f['toNode']]) ==
              set(['K2Node_VariableGet_0', 'K2Node_CallFunction_0']),
              'planted: both endpoints named')
    check(not any(f['graph'] == 'ControlStable' for f in findings),
          'control A: an unchanged graph reports nothing')
    check(not any(f['graph'] == 'ControlRemoved' for f in findings),
          'control B: a loss whose endpoint node was DELETED is excluded')
    rep = [f for f in findings if f['graph'] == 'ControlRepin']
    check(len(rep) == 1 and rep[0]['repinned'] and tier(rep[0]) == 4,
          'control C: a pin RENAME is classified repinned, never as a loss')
    check(not any(f['graph'] == 'ControlExec' for f in findings),
          'control D: a lost EXEC link is out of scope')
    dbl = [f for f in findings if f['graph'] == 'ControlDouble']
    check(len(dbl) == 1 and not dbl[0]['repinned'] and tier(dbl[0]) == 1,
          'control E: one of TWO same-category links between one pair is a LOSS, not a rename')

    # --- negative control F on the whole method: an export diffed against ITSELF must be silent.
    c = os.path.join(tmp, 'TextIdent-1.txt')
    d = os.path.join(tmp, 'TextIdent-2.txt')
    io.open(c, 'w', encoding='utf-8').write(old)
    io.open(d, 'w', encoding='utf-8').write(old)
    os.utime(c, (1000000, 1000000))
    os.utime(d, (2000000, 2000000))
    m2, _ = corpus(tmp)
    check(diff_stem(m2['Ident'])[0] == [],
          'control F: an export diffed against itself is silent')

    if not quiet:
        for ok, label in checks:
            print('  %s  %s' % ('PASS' if ok else 'FAIL', label))
        print('  self-test: %d checks, %d failed  (tiers seen: %s)'
              % (len(checks), len(fails), dict(tiers)))
    for fn in os.listdir(tmp):
        os.remove(os.path.join(tmp, fn))
    os.rmdir(tmp)
    return not fails


# ---------------------------------------------------------------------------
# Calibration against the project's real defects
# ---------------------------------------------------------------------------

_WORDS = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven',
          8: 'eight', 9: 'nine', 10: 'ten'}


def calibrate(multi):
    if not CALIBRATION:
        print('CALIBRATION skipped: no cases are set (linkDiff.calibration in .octopus/blueprint.json)')
        return True
    n = len(CALIBRATION)
    print('CALIBRATION - the %s defect%s this instrument was built from'
          % (_WORDS.get(n, str(n)), '' if n == 1 else 's'))
    ok = True
    for c in CALIBRATION:
        files = multi.get(c['asset'])
        if not files:
            print('  MISS  %-22s no multi-export corpus' % c['asset'])
            ok = False
            continue
        found = [f for f in diff_stem(files)[0] if f['graph'] == c['graph']]
        if c['expect']:
            a, ap, b, bp = c['link']
            want = set([(a, ap), (b, bp)])
            hit = [f for f in found
                   if set([tuple(f['from'].rsplit('.', 1)), tuple(f['to'].rsplit('.', 1))]) == want]
            print('  %-5s %-22s %-20s %s'
                  % ('FOUND' if hit else 'MISS', c['asset'], c['graph'],
                     ('T%d, %s, nodes %d->%d %s' % (tier(hit[0]), hit[0]['cat'],
                                                    hit[0]['nodesOld'], hit[0]['nodesNew'],
                                                    'STABLE' if hit[0]['stable'] else 'CHANGED'))
                     if hit else c['note']))
            if not hit:
                ok = False
        else:
            print('  N/A   %-22s %-20s %d other link losses in this graph'
                  % (c['asset'], c['graph'], len(found)))
            print('        %s' % c['note'])
    return ok


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def flag(stem, graph):
    if (stem, graph) in SURVIVORS:
        return SURVIVORS[(stem, graph)]
    if (stem, graph) in KNOWN_ADJUDICATED:
        return 'known, owes nothing: ' + KNOWN_ADJUDICATED[(stem, graph)]
    if stem in KNOWN_SIGNATURE:
        return KNOWN_SIGNATURE[stem]
    return ''


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--calibrate', action='store_true')
    ap.add_argument('--asset', help='one stem, e.g. BP_ExampleActor')
    ap.add_argument('--verify', nargs=2, metavar=('ASSET', 'GRAPH'),
                    help='dump the EARLIEST export link set for one graph (blind spot 1)')
    ap.add_argument('--tier', type=int, default=1, help='report tiers 1..N (default 1)')
    ap.add_argument('--json', help='write every finding, all tiers, to this path')
    ap.add_argument('--diffdir', default=DIFFDIR)
    a = ap.parse_args()

    if a.self_test:
        sys.exit(0 if self_test() else 2)

    # THE SELF-TEST GATES EVERY RUN: an instrument that cannot reproduce a known answer reports
    # nothing at all.
    if not self_test(quiet=True):
        print('SELF-TEST FAILED - refusing to report. Run --self-test for the failing assertion.')
        sys.exit(2)

    multi, single = corpus(a.diffdir)
    total = sum(len(v) for v in multi.values()) + sum(len(v) for v in single.values())
    print('CORPUS  %d export files in %s' % (total, os.path.relpath(a.diffdir, ROOT)))
    print('        %d asset stems: %d with >=2 exports (%d files, REACHABLE), '
          '%d with one export (NOT REACHABLE - a single export cannot be diffed)'
          % (len(multi) + len(single), len(multi), sum(len(v) for v in multi.values()),
             len(single)))
    print('        self-test PASSED (%s)' % 'planted loss found, 4 controls silent')
    print('')

    if a.verify:
        stem, gname = a.verify
        files = multi.get(stem) or single.get(stem)
        if not files:
            print('no exports for %r' % stem)
            sys.exit(1)
        red, _ = reduce_export(files[0][1])
        if gname not in red:
            print('%r holds no graph %r. graphs: %s'
                  % (os.path.basename(files[0][1]), gname, ', '.join(sorted(red))))
            sys.exit(1)
        g = red[gname]
        print('EARLIEST export %s  graph %r  %d nodes  %d links'
              % (os.path.basename(files[0][1]), gname, len(g['nodes']), len(g['edges'])))
        for key, meta in sorted(g['edges'].items()):
            print('  %-9s %-46s -> %-46s %s'
                  % (meta['cat'], '%s.%s' % key[0], '%s.%s' % key[1], short_sub(meta['sub'])))
        return

    stems = {a.asset: multi[a.asset]} if a.asset and a.asset in multi else multi
    if a.asset and a.asset not in multi:
        print('%r has fewer than two exports - not reachable.' % a.asset)
        sys.exit(1)

    if a.calibrate:
        sys.exit(0 if calibrate(multi) else 2)

    allf, failed = [], []
    cov = collections.Counter()
    for stem in sorted(stems):
        try:
            found, c = diff_stem(stems[stem])
            cov.update(c)
            for f in found:
                f['asset'] = stem
                allf.append(f)
        except Exception as exc:                                   # noqa: BLE001
            failed.append((stem, repr(exc)))

    if failed:
        print('PARSE FAILURES - these assets were NOT measured:')
        for stem, err in failed:
            print('  %-30s %s' % (stem, err))
        print('')

    print('COVERAGE  %d graphs seen; %d COMPARED across two exports; %d appear in only one export'
          % (cov['graphs'], cov['compared'], cov['single']))
    print('          %d lost EXEC links skipped by design; %d lost non-exec links EXCLUDED because '
          'an endpoint node did not survive (an ordinary edit - and blind spot 4)'
          % (cov['exec'], cov['excludedNodeGone']))
    print('')

    counts = collections.Counter(tier(f) for f in allf)
    print('FINDINGS  %d candidate lost links inspected across %d assets'
          % (len(allf), len(set(f['asset'] for f in allf))))
    print('  T1 %5d  node set UNCHANGED and the pin carries a /Game/ type - the retype family'
          % counts[1])
    print('  T2 %5d  node set UNCHANGED, primitive pin type' % counts[2])
    print('  T3 %5d  the graph gained or lost nodes too - an ordinary edit' % counts[3])
    print('  T4 %5d  the node pair is still linked at the same category - a pin RENAME, not a loss'
          % counts[4])
    print('')

    shown = [f for f in allf if tier(f) <= a.tier]
    by_asset = collections.defaultdict(list)
    for f in shown:
        by_asset[f['asset']].append(f)

    for stem in sorted(by_asset, key=lambda s: (-len(by_asset[s]), s)):
        fl = flag(stem, '')
        print('=== %s   %d finding(s)%s' % (stem, len(by_asset[stem]), '   [%s]' % fl if fl else ''))
        for f in sorted(by_asset[stem], key=lambda x: (x['graph'], x['from'])):
            gf = flag(stem, f['graph'])
            print('  T%d %-26s %-42s -> %-42s'
                  % (tier(f), f['graph'][:26], f['from'][:42], f['to'][:42]))
            print('     %-9s %-30s nodes %d->%d %s %s  %s..%s'
                  % (f['cat'], short_sub(f['sub']), f['nodesOld'], f['nodesNew'],
                     'STABLE' if f['stable'] else 'CHANGED',
                     'LIVE' if f['graphSurvives'] else 'GRAPH-SINCE-REMOVED',
                     f['base'], f['head']))
            if gf:
                print('     %s' % gf)
        print('')

    if a.json:
        for f in allf:
            f['tier'] = tier(f)
        io.open(a.json, 'w', encoding='utf-8').write(
            json.dumps(allf, indent=1, sort_keys=True, default=str))
        print('wrote %s (%d findings, all tiers)' % (a.json, len(allf)))


if __name__ == '__main__':
    main()
