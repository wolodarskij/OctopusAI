# -*- coding: utf-8 -*-
"""Build the documentation call index (`_callindex.json`) from T3D dumps.

The call index is what licenses the documentation's only status that is a *claim*:
the docs skill allows `orphaned` "only when `_callindex.json` reports
`sourcesNotSwept: []`". Regenerate it after every sweep; a hand-written index goes stale.

The output path is `--out`; the default is `callIndexPath` of `.octopus/blueprint.json`
(default `Docs/_callindex.json`), relative to the project root.

This tool does not talk to the editor. The editor is the only thing that can PRODUCE
a T3D and the worst thing to search one with (`AssetTools.read_file` caps at 80 KB),
so the split is:

    editor  ->  the asset-text export toolset (export_bp_t3d.py)  ->  T3D/*.txt
    host    ->  this tool                                         ->  _callindex.json

Input is a manifest JSON mapping asset path -> dump path, of the shape the sandbox
already writes:  {"dumps": {"/Game/...": "D:/.../Text....txt"}}
Pass one or more with --manifest; pass --source to label where each batch came from.

WHAT THE INDEX CAN AND CANNOT SEE
---------------------------------
A call records `FunctionReference=(MemberParent="...Class'X_C'",MemberName="M")`, so
callers are attributable to the declaring class. Two cases leave no such record and
are therefore invisible - both are recorded in the output under `blindSpots` so no
consumer can forget them:

  * inheritance      - a child calling an inherited member does so in self-context
                       (`bSelfContext=True`, no MemberParent at all)
  * interface dispatch - the sender names the INTERFACE, never the implementing class

Usage:
    python tools/build_callindex.py \
        --manifest Saved/sweepdumps.json --source nonTier1Blueprints \
        --manifest Saved/tier1_dumps.json --source tier1Blueprints \
        --levels-checked 8 --levels-without-blueprint 5
"""
from __future__ import unicode_literals
import io
import codecs
import os
import re
import sys
import json
import argparse
import collections
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

REF = re.compile(r'(?:Function|Event|Delegate)Reference=\((?P<body>[^)]*)\)')
MEMBER_NAME = re.compile(r'MemberName="([^"]+)"')
MEMBER_PARENT = re.compile(r'MemberParent="[^\']*\'([^\']+)\'"')
SELF_CONTEXT = re.compile(r'bSelfContext=True')


def short_class(path):
    """'/Script/Engine.BlueprintGeneratedClass'/Game/X/Y.Y_C'' -> 'Y'."""
    leaf = path.rsplit('.', 1)[-1]
    return leaf[:-2] if leaf.endswith('_C') else leaf


def read_dump(path):
    """Decode a T3D dump by its BOM.

    `export_version_text` writes UTF-16 LE for some assets and UTF-8 for most. Reading
    them all as UTF-8 does not raise: every other byte is NUL, so the text decodes to
    garbage, no regex matches, and the asset silently contributes ZERO references. That
    reads as "this class calls nothing" rather than "this dump was never parsed".
    """
    with io.open(path, 'rb') as fh:
        raw = fh.read()
    for bom, enc in ((codecs.BOM_UTF8, 'utf-8-sig'),
                     (codecs.BOM_UTF16_LE, 'utf-16'),
                     (codecs.BOM_UTF16_BE, 'utf-16')):
        if raw.startswith(bom):
            return raw.decode(enc, errors='replace')
    return raw.decode('utf-8', errors='replace')


def scan(dump_path):
    """Yield (member, owner_or_None, self_context) for every reference in one dump."""
    try:
        body = read_dump(dump_path)
    except IOError:
        return
    for m in REF.finditer(body):
        seg = m.group('body')
        name = MEMBER_NAME.search(seg)
        if not name:
            continue
        parent = MEMBER_PARENT.search(seg)
        yield (name.group(1),
               short_class(parent.group(1)) if parent else None,
               bool(SELF_CONTEXT.search(seg)))


def main(argv=None):
    _project.project_root()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--manifest', action='append', default=[],
                    help='JSON with {"dumps": {assetPath: dumpPath}}; repeatable')
    ap.add_argument('--source', action='append', default=[],
                    help='label for the matching --manifest; repeatable')
    ap.add_argument('--not-swept', action='append', default=[],
                    help='label a source this run could NOT read; repeatable. '
                         'Anything listed here keeps sourcesNotSwept non-empty, which '
                         'withholds the `orphaned` licence -- use it rather than letting '
                         'an unread source pass as swept.')
    ap.add_argument('--levels-checked', type=int, default=0,
                    help='level Blueprints read via <Map>.<Map>:PersistentLevel.<Map>')
    ap.add_argument('--levels-without-blueprint', type=int, default=0,
                    help='maps that have no level Blueprint at all')
    ap.add_argument('--out', default=str(_project.project_path('callIndexPath',
                                                               'Docs/_callindex.json',
                                                               'blueprint')))
    args = ap.parse_args(argv)

    if len(args.source) != len(args.manifest):
        ap.error('--source must be given once per --manifest')

    calls = collections.defaultdict(lambda: collections.defaultdict(set))
    selfcalls = collections.defaultdict(set)
    swept = []
    missing = []
    scanned = 0

    for manifest, source in zip(args.manifest, args.source):
        try:
            dumps = json.load(io.open(manifest, encoding='utf-8')).get('dumps', {})
        except (IOError, ValueError) as exc:
            print('SKIP %s (%s)' % (manifest, exc))
            continue
        n = 0
        for asset, dump in dumps.items():
            if not dump or not os.path.exists(dump):
                missing.append(asset)
                continue
            caller = asset.rsplit('/', 1)[-1]
            for member, owner, is_self in scan(dump):
                if owner:
                    calls[owner][member].add(caller)
                elif is_self:
                    selfcalls[member].add(caller)
            n += 1
            scanned += 1
        swept.append({'source': source, 'manifest': manifest, 'assets': n})

    symbols = {}
    for owner in sorted(calls):
        symbols[owner] = dict((m, sorted(c)) for m, c in sorted(calls[owner].items()))

    index = {
        'schemaVersion': 2,
        'generatedUtc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'generatedBy': 'tools/build_callindex.py',
        'sourcesSwept': swept,
        'sourcesNotSwept': list(args.not_swept),
        'levelBlueprints': {
            'read': args.levels_checked,
            'withoutBlueprint': args.levels_without_blueprint,
        },
        'blindSpots': [
            'inheritance: a child calling an inherited member records bSelfContext '
            'with no MemberParent, so the call is not attributable to the parent',
            'interface dispatch: the sender names the interface as MemberParent, '
            'never the implementing class',
        ],
        'counts': {
            'dumpsScanned': scanned,
            'dumpsMissing': len(missing),
            'classesWithCallers': len(symbols),
            'distinctMembersCalled': sum(len(v) for v in symbols.values()),
            'selfContextMembers': len(selfcalls),
        },
        'selfContextCalls': dict((m, sorted(c)) for m, c in sorted(selfcalls.items())),
        'symbols': symbols,
    }
    if missing:
        index['dumpsMissing'] = sorted(missing)[:200]

    out_dir = os.path.dirname(args.out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with io.open(args.out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(index, indent=1, sort_keys=False, ensure_ascii=False))

    print('wrote %s' % args.out)
    for s in swept:
        print('  swept %-22s %4d assets' % (s['source'], s['assets']))
    print('  dumps scanned            %d' % scanned)
    print('  dumps missing            %d' % len(missing))
    print('  classes with callers     %d' % len(symbols))
    print('  distinct members called  %d' % index['counts']['distinctMembersCalled'])
    print('  self-context members     %d  (not attributable to a class)'
          % len(selfcalls))
    return 0


if __name__ == '__main__':
    sys.exit(main())
