# -*- coding: utf-8 -*-
"""Worklist for an exhaustive per-symbol documentation pass on one Blueprint.

One entry per function graph, one entry per event. A graph carrying events contributes
one entry *per event*, never one for the graph -- a 493-node ubergraph documented as a
single unit is useless, and the per-event split is what makes shared nodes tractable:
each entry covers what its own chain reaches, so a node used by three events is
described three times, scoped to each, and never collapsed into one description
belonging to no event.

`page` is the markdown file an entry's heading actually lives in. `next-per-page`
relies on it to keep two agents off one file, so a wrong page field is not cosmetic --
it hands the same file to two writers.

    python tools/doc_entries.py <target> build --graphs <dir> --docs <dir> [--page NAME]
    python tools/doc_entries.py <target> status
    python tools/doc_entries.py <target> next-per-page
    python tools/doc_entries.py <target> claim NAME
    python tools/doc_entries.py <target> done NAME
    python tools/doc_entries.py <target> coverage      graphs with no entry -- the gate
    python tools/doc_entries.py <target> set-page NAME PAGE

Without `--docs`, the worklist lives in the directory that `docEntriesDir` of
`.octopus/blueprint.json` names, with `{target}` replaced by the target. The default is
`Docs/Classes/{target}`.

`build` preserves status/claim/note for entries that already exist, so it is safe to
re-run after a re-export.
"""
import io
import json
import os
import re
import sys
import argparse
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

ROOT = str(_project.project_root())

# Symbol names are not ASCII. A name can carry a Cyrillic letter that looks like a Latin
# one, and printing it to a cp1252 console raises UnicodeEncodeError *after* the
# worklist has already been written -- the edit lands but the command looks like it
# failed. Reconfigure stdout instead of stripping the character, so the name printed is
# the name on disk.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
EVENT_CLASSES = ('K2Node_Event', 'K2Node_CustomEvent')

# Entry points that are NOT K2Node_Event. Each starts its own exec chain, so a graph can
# hold live nodes reachable from no event at all, for example a chain that hangs off a
# single LeftMouseButton node. Counting only EVENT_CLASSES silently drops those, and the coverage gate still reads
# green because the *graph* is covered by its other entries -- so the miss is invisible.
ENTRY_CLASSES = {
    'K2Node_InputKey': ('InputKey', 'AxisKey'),
    'K2Node_InputAxisKeyEvent': ('AxisKey',),
    'K2Node_InputAction': ('InputActionName',),
    'K2Node_InputAxisEvent': ('InputAxisName',),
    'K2Node_InputTouch': (),
    'K2Node_ActorBoundEvent': ('DelegatePropertyName',),
    'K2Node_ComponentBoundEvent': ('DelegatePropertyName', 'ComponentPropertyName'),
}


def entry_label(node):
    """Name a non-event entry point from whichever property identifies it."""
    props = node.get('props', {})
    for k in ENTRY_CLASSES.get(node.get('class'), ()):
        v = props.get(k)
        if v:
            return '%s(%s)' % (node['class'].replace('K2Node_', ''), str(v).strip('"'))
    return node['class'].replace('K2Node_', '') + '_' + node['name'].rsplit('_', 1)[-1]


def worklist_path(docs):
    return os.path.join(ROOT, docs, '_entries.json')


def load(docs):
    return json.load(io.open(worklist_path(docs), encoding='utf-8'))


def save(docs, d):
    with io.open(worklist_path(docs), 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1, ensure_ascii=False)


def build(target, graphs, docs, default_page):
    gdir = os.path.join(ROOT, graphs)
    index = json.load(io.open(os.path.join(gdir, '_index.json'), encoding='utf-8'))
    entries = {}
    for gname, meta in index.items():
        g = json.load(io.open(os.path.join(gdir, meta['file']), encoding='utf-8'))
        nodes = g['nodes']
        events = [n for n in nodes if n.get('class') in EVENT_CLASSES and n.get('event')]
        extra = [n for n in nodes if n.get('class') in ENTRY_CLASSES]
        if events or extra:
            for ev in events:
                entries['%s::%s' % (gname, ev['event'])] = {
                    'kind': 'event', 'graph': gname, 'symbol': ev['event'],
                    'entryNode': ev['name'], 'page': default_page,
                    'graphNodes': len(nodes), 'status': 'pending',
                    'claimedUtc': None, 'doneUtc': None, 'note': None,
                }
            for n in extra:
                label = entry_label(n)
                entries['%s::%s' % (gname, label)] = {
                    'kind': 'event', 'graph': gname, 'symbol': label,
                    'entryNode': n['name'], 'page': default_page,
                    'graphNodes': len(nodes), 'status': 'pending',
                    'claimedUtc': None, 'doneUtc': None, 'note': None,
                }
        else:
            entries[gname] = {
                'kind': 'function', 'graph': gname, 'symbol': gname,
                'entryNode': None, 'page': default_page,
                'graphNodes': len(nodes), 'status': 'pending',
                'claimedUtc': None, 'doneUtc': None, 'note': None,
            }

    prior = {}
    if os.path.exists(worklist_path(docs)):
        prior = load(docs).get('entries', {})
    for k, v in entries.items():
        if k in prior:
            for f in ('status', 'claimedUtc', 'doneUtc', 'note', 'page'):
                v[f] = prior[k].get(f, v[f])

    out = {
        'target': target,
        'graphs': graphs,
        'docs': docs,
        'rule': ('One entry per function graph, one per event. A node reached by several '
                 'events is documented once per event, scoped to that chain - never '
                 'collapsed into a single shared description.'),
        'generatedUtc': datetime.datetime.now(datetime.timezone.utc)
                        .strftime('%Y-%m-%dT%H:%M:%SZ'),
        'entries': entries,
    }
    os.makedirs(os.path.join(ROOT, docs), exist_ok=True)
    save(docs, out)
    print('built %d entries (%d function, %d event) for %s'
          % (len(entries),
             sum(1 for e in entries.values() if e['kind'] == 'function'),
             sum(1 for e in entries.values() if e['kind'] == 'event'),
             target))


def pick(entries, n):
    """Biggest-first within pending, so the hard ones do not pile up at the end."""
    pend = [k for k, v in entries.items() if v['status'] == 'pending']
    pend.sort(key=lambda k: (-entries[k]['graphNodes'], k))
    return pend[:n]


def status(d):
    e = d['entries']
    counts = {}
    for v in e.values():
        counts[v['status']] = counts.get(v['status'], 0) + 1
    total = len(e)
    done = counts.get('done', 0)
    w = 40
    filled = int(w * done / total) if total else 0
    print(' %s  [%s%s] %d/%d  (%.1f%%)'
          % (d['target'], '#' * filled, '.' * (w - filled), done, total,
             100.0 * done / total if total else 0))
    for k in sorted(counts):
        print('   %-12s %d' % (k, counts[k]))
    by_page = {}
    for v in e.values():
        by_page.setdefault(v['page'], [0, 0])
        by_page[v['page']][1] += 1
        if v['status'] == 'done':
            by_page[v['page']][0] += 1
    print('\n BY PAGE')
    for p in sorted(by_page):
        a, b = by_page[p]
        print('   %-24s %3d/%-3d%s' % (p, a, b, '  COMPLETE' if a == b else ''))


def coverage(d):
    """The gate: every graph in the export must be covered by at least one entry."""
    gdir = os.path.join(ROOT, d['graphs'])
    index = json.load(io.open(os.path.join(gdir, '_index.json'), encoding='utf-8'))
    covered = {v['graph'] for v in d['entries'].values() if v['status'] == 'done'}
    missing = sorted(set(index) - covered)
    print('graphs in export        : %d' % len(index))
    print('graphs with a done entry: %d' % len(covered & set(index)))
    print('graphs WITHOUT an entry : %d' % len(missing))
    for m in missing:
        print('   ', m)
    return 0 if not missing else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target')
    ap.add_argument('cmd')
    ap.add_argument('rest', nargs='*')
    ap.add_argument('--graphs')
    ap.add_argument('--docs')
    ap.add_argument('--page', default='functions.md')
    a = ap.parse_args()

    if a.cmd == 'build':
        return build(a.target, a.graphs, a.docs, a.page)

    docs = a.docs or _project.setting('blueprint', 'docEntriesDir',
                                      'Docs/Classes/{target}').replace('{target}', a.target)
    d = load(docs)
    e = d['entries']

    if a.cmd == 'status':
        return status(d)
    if a.cmd == 'coverage':
        sys.exit(coverage(d))
    if a.cmd == 'next-per-page':
        busy = {v['page'] for v in e.values() if v['status'] == 'in-progress'}
        seen = {}
        for k in pick(e, len(e)):
            pg = e[k]['page']
            if pg in busy or pg in seen:
                continue
            seen[pg] = k
        for pg in sorted(seen):
            print('%s\t%s' % (pg, seen[pg]))
        return

    name = a.rest[0] if a.rest else None
    if name not in e:
        print('no such entry: %s' % name)
        sys.exit(1)
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if a.cmd == 'claim':
        if e[name]['status'] == 'done':
            print('REFUSED: already done')
            sys.exit(1)
        e[name]['status'] = 'in-progress'
        e[name]['claimedUtc'] = now
    elif a.cmd == 'done':
        e[name]['status'] = 'done'
        e[name]['doneUtc'] = now
    elif a.cmd == 'set-page':
        e[name]['page'] = a.rest[1]
    else:
        print('unknown command: %s' % a.cmd)
        sys.exit(1)
    save(docs, d)
    print('%s -> %s' % (name, e[name].get('page') if a.cmd == 'set-page' else e[name]['status']))


if __name__ == '__main__':
    main()
