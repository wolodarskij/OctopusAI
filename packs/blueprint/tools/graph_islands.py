# -*- coding: utf-8 -*-
"""Measure the entry-point islands of one parsed graph.

An event entry documents only what its own chain reaches, so the first thing any pass
needs is: what does each entry reach, do the reaches overlap, and do they account for every
node? A shortfall means a live entry point was missed -- for example, half the nodes of a
player controller can hang off a `K2Node_InputKey` while a coverage gate still reads green
because the *graph* is covered by its other entries.

Entry points are events AND the non-event nodes that start their own exec chain:
InputKey / InputAction / InputAxis / InputTouch / ActorBoundEvent / ComponentBoundEvent /
Timeline. Walking undirected over exec *and* data links is deliberate -- a pure node feeding
two chains belongs to both, and that is exactly the overlap this measures.

    python tools/graph_islands.py Saved/BP_ExampleActorGraphs/EventGraph.json
"""
import io
import json
import os
import sys
import collections

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

# Windows consoles default to cp1252, which cannot encode the U+2229 used in the
# intersection report -- the traceback lands *after* the counts are printed, so it
# reads as a parse failure rather than an encoding one.
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

EVENTS = ('K2Node_Event', 'K2Node_CustomEvent')
EXTRA = ('K2Node_InputKey', 'K2Node_InputAction', 'K2Node_InputAxisEvent',
         'K2Node_InputAxisKeyEvent', 'K2Node_InputTouch', 'K2Node_ActorBoundEvent',
         'K2Node_ComponentBoundEvent', 'K2Node_Timeline')


def load(path):
    g = json.load(io.open(path, encoding='utf-8'))
    nodes = {n['name']: n for n in g['nodes']}
    adj = collections.defaultdict(set)
    for n in g['nodes']:
        for p in n.get('pins', []):
            for l in (p.get('links') or []):
                other = l.get('toNode') or l.get('node')
                if other in nodes:
                    adj[n['name']].add(other)
                    adj[other].add(n['name'])
    return g, nodes, adj


def reach(start, adj):
    seen, stack = {start}, [start]
    while stack:
        cur = stack.pop()
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def main():
    _project.optional_project_root()
    path = sys.argv[1]
    g, nodes, adj = load(path)
    entries = [n for n in g['nodes']
               if (n.get('class') in EVENTS and n.get('event')) or n.get('class') in EXTRA]
    if not entries:
        print('no entry point in', path)
        return
    reaches = {}
    for e in entries:
        label = e.get('event') or '%s(%s)' % (e['class'].replace('K2Node_', ''), e['name'])
        reaches[label] = reach(e['name'], adj)

    print('%s — %d nodes, %d entry points' % (path, len(nodes), len(entries)))
    for k in sorted(reaches, key=lambda k: -len(reaches[k])):
        print('   %-34s reach %3d' % (k, len(reaches[k])))

    keys = sorted(reaches)
    overlaps = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            both = reaches[keys[i]] & reaches[keys[j]]
            if both:
                overlaps.append((keys[i], keys[j], len(both)))
    print('\n   pairwise intersections: %d non-empty of %d'
          % (len(overlaps), len(keys) * (len(keys) - 1) // 2))
    for a, b, c in overlaps:
        print('      %s ∩ %s = %d' % (a, b, c))

    union = set().union(*reaches.values())
    print('\n   union %d of %d' % (len(union), len(nodes)))
    missed = set(nodes) - union
    if missed:
        print('   UNREACHED (%d):' % len(missed))
        for m in sorted(missed):
            print('      %-30s %s' % (m, nodes[m].get('class')))


if __name__ == '__main__':
    main()
