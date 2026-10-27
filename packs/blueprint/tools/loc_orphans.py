"""Hold on to a translation whose source string has gone, and put it back when the source returns.

A GatherText run rebuilds the manifest from what it can find today. When a change removes the
Blueprint graph that carried a text, that text leaves the manifest, and GenerateGatherArchive then
drops the matching row from every language archive. The translation is gone from the tree even
though nobody decided to throw it away.

This tool keeps those rows in a store file (`locStore` in `.octopus/blueprint.json`) and writes them
back into the archives the moment the same namespace and key appear again.

    python tools/loc_orphans.py extract --reference ../Backups/Localization-copy/Game
    python tools/loc_orphans.py report
    python tools/loc_orphans.py restore [--apply]

`extract` compares a reference copy of the localization target directory (`locDir`, by default
Content/Localization/Game) against the live one and adds every translated row the live tree has
lost. It never removes anything from the store.

`restore` fills a live archive row whose translation is empty from the store. It refuses a row whose
source text has changed, because a translation of a different sentence is worse than no translation.
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

ROOT = str(_project.project_root())
# The localization target: its name names the `.archive` and `.manifest` files.
TARGET = _project.setting('blueprint', 'locTarget', 'Game')
# OCTOPUS_LOC_DIR points the tool at a copy of the tree, which is how restore is tested.
LIVE = (_project.env('LOC_DIR')
        or str(_project.project_path('locDir', 'Content/Localization/' + TARGET, 'blueprint')))
STORE = str(_project.project_path('locStore', _project.in_config('orphaned-translations.json'),
                                  'blueprint'))


def _cultures():
    """The cultures `locCultures` lists, or every culture directory under LIVE with an archive."""
    listed = _project.setting('blueprint', 'locCultures', None)
    if listed:
        return list(listed)
    try:
        return sorted(d for d in os.listdir(LIVE)
                      if os.path.isfile(os.path.join(LIVE, d, TARGET + '.archive')))
    except OSError:
        return []


CULTURES = _cultures()
ARCHIVE = TARGET + '.archive'
MANIFEST = TARGET + '.manifest'


def read_json(path):
    raw = io.open(path, 'rb').read()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return json.loads(raw.decode('utf-16'))
    return json.loads(raw.decode('utf-8-sig'))


def write_archive(path, data):
    """Match what the commandlet writes: UTF-16 LE with a BOM, tab indent, \\n line endings."""
    text = json.dumps(data, indent='\t', ensure_ascii=False) + '\n'
    io.open(path, 'wb').write(text.encode('utf-16'))


def walk(node, ns=''):
    here = node.get('Namespace', '')
    full = (ns + '.' + here).strip('.') if here else ns
    for child in (node.get('Children') or []):
        yield full, child.get('Key', ''), child
    for sub in (node.get('Subnamespaces') or []):
        for item in walk(sub, full):
            yield item


def translation_of(entry):
    return (entry.get('Translation') or {}).get('Text', '')


def source_of(entry):
    return (entry.get('Source') or {}).get('Text', '')


def load_store():
    if not os.path.exists(STORE):
        return {'FormatVersion': 1, 'Entries': []}
    return read_json(STORE)


def save_store(store):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    text = json.dumps(store, indent='\t', ensure_ascii=False, sort_keys=False) + '\n'
    io.open(STORE, 'w', encoding='utf-8', newline='\n').write(text)


def store_index(store):
    return dict(((e['namespace'], e['key']), e) for e in store['Entries'])


def cmd_extract(args):
    store = load_store()
    index = store_index(store)
    added = 0
    touched = 0

    for culture in CULTURES:
        ref = os.path.join(args.reference, culture, ARCHIVE)
        live = os.path.join(LIVE, culture, ARCHIVE)
        if not (os.path.exists(ref) and os.path.exists(live)):
            print('skip %s: no archive pair' % culture)
            continue

        old = dict(((n, k), e) for n, k, e in walk(read_json(ref)))
        new = set((n, k) for n, k, _e in walk(read_json(live)))

        for ident in sorted(set(old) - new):
            entry = old[ident]
            text = translation_of(entry)
            if not text.strip():
                continue
            row = index.get(ident)
            if row is None:
                row = {'namespace': ident[0], 'key': ident[1],
                       'source': source_of(entry), 'translations': {}}
                store['Entries'].append(row)
                index[ident] = row
                added += 1
            if culture not in row['translations']:
                row['translations'][culture] = text
                touched += 1

    store['Entries'].sort(key=lambda e: (e['namespace'], e['key']))
    if args.apply:
        save_store(store)
        print('WROTE %s' % os.path.relpath(STORE, ROOT))
    else:
        print('(dry run - pass --apply to write)')
    print('%d new key(s), %d translation(s) held, %d key(s) in the store'
          % (added, touched, len(store['Entries'])))
    return 0


def cmd_report(args):
    store = load_store()
    if not store['Entries']:
        print('the store is empty')
        return 0

    manifest = os.path.join(LIVE, MANIFEST)
    live_keys = set()
    if os.path.exists(manifest):
        for _ns, _k, child in walk(read_json(manifest)):
            for k in (child.get('Keys') or []):
                live_keys.add(k.get('Key'))

    restorable = [e for e in store['Entries'] if e['key'] in live_keys]
    per_culture = {}
    for e in store['Entries']:
        for c in e['translations']:
            per_culture[c] = per_culture.get(c, 0) + 1

    print('keys held           : %d' % len(store['Entries']))
    print('translations held   : %d' % sum(per_culture.values()))
    for c in CULTURES:
        print('   %-8s %d' % (c, per_culture.get(c, 0)))
    print('keys whose source is back in the manifest: %d' % len(restorable))
    for e in restorable[:20]:
        print('   %s  %s' % (e['key'], (e['source'] or '')[:60].replace('\n', ' ')))
    return 0


def cmd_restore(args):
    store = load_store()
    index = store_index(store)
    if not index:
        print('the store is empty')
        return 0

    total_filled = 0
    total_skipped = 0
    for culture in CULTURES:
        path = os.path.join(LIVE, culture, ARCHIVE)
        if not os.path.exists(path):
            continue
        data = read_json(path)
        filled = 0
        skipped = 0
        for ns, key, entry in walk(data):
            row = index.get((ns, key))
            if row is None or culture not in row['translations']:
                continue
            if translation_of(entry).strip():
                continue
            if source_of(entry) != row['source']:
                skipped += 1
                continue
            entry.setdefault('Translation', {})['Text'] = row['translations'][culture]
            filled += 1
        if filled and args.apply:
            write_archive(path, data)
        total_filled += filled
        total_skipped += skipped
        print('%-8s filled %3d   source changed, left alone %3d' % (culture, filled, skipped))

    if not args.apply:
        print('(dry run - pass --apply to write the archives)')
    print('--- %d translation(s) restored, %d needing review' % (total_filled, total_skipped))
    return 0


def cmd_remap(args):
    """Carry a held translation to a new identity.

    Moving a text into a string table changes what the archive keys it under: an NSLOCTEXT literal
    is ("", <32-hex key>) and a string table row is (<table namespace>, <row key>). Without this the
    translation stays parked under a name nothing asks for again.

    The map file is a list of {"from": {"namespace", "key"}, "to": {"namespace", "key", "source"}}.
    """
    mapping = read_json(args.map)
    store = load_store()
    index = store_index(store)
    added = 0
    missing = []

    for row in mapping:
        src = (row['from']['namespace'], row['from']['key'])
        dst = (row['to']['namespace'], row['to']['key'])
        held = index.get(src)
        if held is None:
            missing.append(src)
            continue
        if dst in index:
            for culture, text in held['translations'].items():
                index[dst]['translations'].setdefault(culture, text)
            continue
        entry = {'namespace': dst[0], 'key': dst[1],
                 'source': row['to'].get('source', held['source']),
                 'translations': dict(held['translations'])}
        store['Entries'].append(entry)
        index[dst] = entry
        added += 1

    store['Entries'].sort(key=lambda e: (e['namespace'], e['key']))
    if args.apply:
        save_store(store)
        print('WROTE %s' % os.path.relpath(STORE, ROOT))
    else:
        print('(dry run - pass --apply to write)')
    print('%d identity/identities added, %d source key(s) not held' % (added, len(missing)))
    for m in missing[:10]:
        print('   not held: %s / %s' % m)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command')

    p = sub.add_parser('extract', help='add every translation the live archives have lost')
    p.add_argument('--reference', required=True,
                   help='a copy of the localization target directory taken before the gather')
    p.add_argument('--apply', action='store_true')
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser('report', help='what the store holds and what can go back now')
    p.set_defaults(func=cmd_report)

    p = sub.add_parser('remap', help='carry a held translation to a new namespace and key')
    p.add_argument('--map', required=True,
                   help='JSON list of {"from":{"namespace","key"},"to":{"namespace","key"}}')
    p.add_argument('--apply', action='store_true')
    p.set_defaults(func=cmd_remap)

    p = sub.add_parser('restore', help='write held translations back into the live archives')
    p.add_argument('--apply', action='store_true')
    p.set_defaults(func=cmd_restore)

    args = parser.parse_args()
    if not getattr(args, 'func', None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
