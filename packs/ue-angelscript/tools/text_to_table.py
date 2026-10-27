# -*- coding: utf-8 -*-
"""Move a text out of `Script/` into a string table, and carry its translations.

A text written as `NSLOCTEXT("", "<package text id>", "<source>")` lives only in the `.as` file.
Its translations sit in `Content/Localization/<target>/<lang>/<target>.locres` under the empty
namespace, and the next gather drops them when the Blueprint the gather read them from is gone.

This moves one text onto a string table key and rewrites every localisation file:

    <target>.manifest            the gathered source, under the table's namespace
    <lang>/<target>.archive      the translator's record
    <lang>/<target>.locres       what the game reads
    <lang>/*.po                  the export for a translation service, where one exists

    python text_to_table.py --old <key> --table /Game/Text/Foo.Foo --key <new key> --source <text> --apply

`--source` gives the source string when the old key carries no translation anywhere.
Without `--apply` it reports what it would do and writes nothing.

The reader reproduces every file byte for byte before it writes one; a file it cannot
reproduce stops the run.

Project settings (`.octopus/ue-angelscript.json`, object `text_to_table`): `localizationTarget`, the
name of the localisation target, and `nativeCulture`, the culture whose entry is the source text.
"""
import argparse
import io
import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("text_to_table", {})
TARGET = CFG.get("localizationTarget", "Game")
NATIVE = CFG.get("nativeCulture", "en")

BASE = 'Content/Localization/' + TARGET
MAGIC = bytes([0x0E, 0x14, 0x74, 0x75, 0x67, 0x4A, 0x03, 0xFC,
               0x4A, 0x15, 0x90, 0x9D, 0xC3, 0x37, 0x7F, 0x1B])


# ------------------------------------------------------------------ locres --
# Version 2 (Optimized_CRC32), from Engine/Source/Runtime/Core/Private/
# Internationalization/TextLocalizationResource.cpp and TextKey.cpp:
#
#     FGuid magic | uint8 version | int64 string-array offset
#     uint32 entry count | uint32 namespace count
#     namespace: uint32 hash, FString name, uint32 key count
#       key:     uint32 hash, FString key, uint32 source hash, int32 string index
#     at the offset: int32 count, then FString text and int32 reference count
#
# Both key hashes are discarded when the file loads.

class Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def take(self, n):
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self):
        return self.take(1)[0]

    def u32(self):
        return struct.unpack('<I', self.take(4))[0]

    def i32(self):
        return struct.unpack('<i', self.take(4))[0]

    def i64(self):
        return struct.unpack('<q', self.take(8))[0]

    def string(self):
        n = self.i32()
        if n == 0:
            return u'', 'zero'
        if n > 0:
            raw = self.take(n)
            assert raw[-1:] == b'\x00', 'an ansi string is not terminated'
            return raw[:-1].decode('utf-8'), 'ansi'
        raw = self.take(-n * 2)
        assert raw[-2:] == b'\x00\x00', 'a wide string is not terminated'
        return raw[:-2].decode('utf-16-le'), 'wide'


def put_string(s, form):
    if form == 'zero':
        return struct.pack('<i', 0)
    if form == 'wide':
        return struct.pack('<i', -(len(s) + 1)) + (s + u'\x00').encode('utf-16-le')
    raw = (s + u'\x00').encode('utf-8')
    return struct.pack('<i', len(raw)) + raw


def load_locres(path):
    with open(path, 'rb') as f:
        data = f.read()
    r = Reader(data)
    assert r.take(16) == MAGIC, '%s is not a locres file' % path
    version = r.u8()
    assert version == 2, '%s is version %d, only version 2 is handled' % (path, version)
    offset = r.i64()
    entry_count = r.u32()

    namespaces = []
    for _ in range(r.u32()):
        ns_hash = r.u32()
        ns, ns_form = r.string()
        keys = []
        for _ in range(r.u32()):
            key_hash = r.u32()
            key, key_form = r.string()
            keys.append([key_hash, key, key_form, r.u32(), r.i32()])
        namespaces.append([ns_hash, ns, ns_form, keys])

    r.p = offset
    strings = []
    for _ in range(r.i32()):
        text, form = r.string()
        strings.append([text, form, r.i32()])

    return {'version': version, 'entry_count': entry_count,
            'namespaces': namespaces, 'strings': strings}


def dump_locres(res):
    body = struct.pack('<II', res['entry_count'], len(res['namespaces']))
    for ns_hash, ns, ns_form, keys in res['namespaces']:
        body += struct.pack('<I', ns_hash) + put_string(ns, ns_form)
        body += struct.pack('<I', len(keys))
        for key_hash, key, key_form, source_hash, index in keys:
            body += struct.pack('<I', key_hash) + put_string(key, key_form)
            body += struct.pack('<II', source_hash, index & 0xFFFFFFFF)

    head = MAGIC + struct.pack('<B', res['version'])
    tail = struct.pack('<i', len(res['strings']))
    for text, form, refs in res['strings']:
        tail += put_string(text, form) + struct.pack('<i', refs)

    return head + struct.pack('<q', len(head) + 8 + len(body)) + body + tail


def find_key(res, namespace, key):
    for _, ns, _, keys in res['namespaces']:
        if ns == namespace:
            for row in keys:
                if row[1] == key:
                    return row
    return None


def find_namespace(res, name):
    for row in res['namespaces']:
        if row[1] == name:
            return row
    return None


# -------------------------------------------------------- archive and json --
def load_json(path):
    return json.loads(io.open(path, encoding='utf-16').read())


def save_json(path, data):
    body = json.dumps(data, ensure_ascii=False, indent='\t')
    io.open(path, 'w', encoding='utf-16', newline='\n').write(body)


def check_json(path, data):
    with open(path, 'rb') as f:
        original = f.read()
    body = json.dumps(data, ensure_ascii=False, indent='\t')
    made = b'\xff\xfe' + body.encode('utf-16-le')
    assert made == original, 'the reader does not reproduce %s' % path


def subnamespace(data, name):
    for ns in data.get('Subnamespaces') or []:
        if ns['Namespace'] == name:
            return ns
    return None


# ------------------------------------------------------------------- moves --
def load_all():
    """Every localisation file, parsed once."""
    manifest_path = os.path.join(BASE, TARGET + '.manifest')
    state = {'manifest': load_json(manifest_path), 'languages': {}}
    check_json(manifest_path, state['manifest'])

    for lang in sorted(os.listdir(BASE)):
        folder = os.path.join(BASE, lang)
        if not os.path.isdir(folder):
            continue

        locres_path = os.path.join(folder, TARGET + '.locres')
        res = load_locres(locres_path)
        with open(locres_path, 'rb') as f:
            assert dump_locres(res) == f.read(), 'the reader does not reproduce %s' % locres_path

        archive_path = os.path.join(folder, TARGET + '.archive')
        archive = load_json(archive_path)
        check_json(archive_path, archive)

        pos = {}
        for name in sorted(os.listdir(folder)):
            if name.endswith('.po'):
                full = os.path.join(folder, name)
                pos[full] = io.open(full, encoding='utf-8-sig').read()

        state['languages'][lang] = {'locres': res, 'locres_path': locres_path,
                                    'archive': archive, 'archive_path': archive_path,
                                    'po': pos}
    return state


def save_all(state):
    save_json(os.path.join(BASE, TARGET + '.manifest'), state['manifest'])
    for lang in state['languages'].values():
        with open(lang['locres_path'], 'wb') as f:
            f.write(dump_locres(lang['locres']))
        save_json(lang['archive_path'], lang['archive'])
        for path, text in lang['po'].items():
            io.open(path, 'w', encoding='utf-8-sig', newline='\n').write(text)


def take_locres(res, namespace, new_key, old_key):
    """Give the old key's translation to the new key, then drop the old key."""
    old = find_key(res, '', old_key)
    if old is None:
        return None

    if find_key(res, namespace, new_key) is None:
        target = find_namespace(res, namespace)
        if target is None:
            target = [0, namespace, 'ansi', []]
            res['namespaces'].append(target)
        target[3].append([0, new_key, 'ansi', old[3], old[4]])

    for row in res['namespaces']:
        if row[1] == '':
            row[3] = [k for k in row[3] if k[1] != old_key]
    return res['strings'][old[4]][0]


def take_archive(data, namespace, new_key, old_key, source, translation):
    target = subnamespace(data, namespace)
    if target is None:
        target = {'Namespace': namespace, 'Children': []}
        data.setdefault('Subnamespaces', []).append(target)
    if not any(c.get('Key') == new_key for c in target['Children']):
        target['Children'].append({'Source': {'Text': source},
                                   'Translation': {'Text': translation},
                                   'Key': new_key})

    def drop(node):
        node['Children'] = [c for c in (node.get('Children') or []) if c.get('Key') != old_key]
        for s in node.get('Subnamespaces') or []:
            drop(s)
    drop(data)


def take_manifest(data, namespace, new_key, old_key, source, table):
    target = subnamespace(data, namespace)
    if target is None:
        target = {'Namespace': namespace, 'Children': []}
        data.setdefault('Subnamespaces', []).append(target)

    held = False
    for child in target['Children']:
        for key in child.get('Keys') or []:
            if key.get('Key') == new_key:
                held = True
    if not held:
        target['Children'].append({'Source': {'Text': source},
                                   'Keys': [{'Key': new_key, 'Path': table}]})

    def drop(node):
        node['Children'] = [c for c in (node.get('Children') or [])
                            if not any(k.get('Key') == old_key for k in (c.get('Keys') or []))]
        for s in node.get('Subnamespaces') or []:
            drop(s)
    drop(data)


def take_po(text, old_key, new_key, table, seen):
    start = text.find('#. Key:\t' + old_key)
    if start < 0:
        return text
    end = text.find('\n\n', start)

    if new_key in seen:
        return text[:start] + text[end + 2:]

    lines = []
    for line in text[start:end].split('\n'):
        if line.startswith('#. Key:'):
            lines.append('#. Key:\t' + new_key)
        elif line.startswith('#. SourceLocation:'):
            lines.append('#. SourceLocation:\t' + table)
        elif line.startswith('#: '):
            lines.append('#: ' + table)
        elif line.startswith('msgctxt '):
            lines.append('msgctxt ",%s"' % new_key)
        else:
            lines.append(line)
    return text[:start] + '\n'.join(lines) + text[end:]


def move_many(moves, apply, log):
    """Carry a list of {old, table, namespace, key, source} onto their table keys."""
    state = load_all()
    seen = set()
    carried = 0

    for one in moves:
        namespace, key, table, source = one['namespace'], one['key'], one['table'], one['source']
        english = None

        for lang, files in state['languages'].items():
            translation = take_locres(files['locres'], namespace, key, one['old'])
            if lang == NATIVE and translation is not None:
                english = translation
            take_archive(files['archive'], namespace, key, one['old'], source,
                         translation if translation is not None else source)
            for path in list(files['po']):
                files['po'][path] = take_po(files['po'][path], one['old'], key, table, seen)

        take_manifest(state['manifest'], namespace, key, one['old'], source, table)
        seen.add(key)
        if english is not None:
            carried += 1

    if apply:
        save_all(state)
    log('%d texts, %d had an English entry to carry' % (len(moves), carried))
    return state


def move(old_key, table, new_key, source, apply, log, namespace=None):
    return move_many([{'old': old_key, 'table': table, 'key': new_key, 'source': source,
                       'namespace': namespace or table.rsplit('.', 1)[-1]}], apply, log)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old', required=True, help='the package text id the script carries')
    parser.add_argument('--table', required=True, help='the table id, /Game/Text/Foo.Foo')
    parser.add_argument('--key', required=True, help='the key the text takes in the table')
    parser.add_argument('--namespace', help='the table namespace, when it is not the object name')
    parser.add_argument('--source', required=True, help='the source string')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)

    out = io.open(1, 'w', encoding='utf-8', errors='replace', closefd=False)
    if not args.table.startswith('/Game/'):
        # Git Bash rewrites a leading slash into a Windows path. Pass the table id as
        # //Game/Text/Foo.Foo there, or run this from PowerShell.
        out.write('the table id must start with /Game/, this one is %r\n' % args.table)
        out.flush()
        return 1

    move(args.old, args.table, args.key, args.source, args.apply,
         lambda message: out.write(message + '\n'), namespace=args.namespace)
    out.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
