# -*- coding: utf-8 -*-
"""Read the entries of a `UStringTable` asset from its package bytes.

Offline. The export body is: 12 bytes, the table id as an FString, an int32 count, then that
many (key, source) FString pairs.

    python tools/stringtable_keys.py --project <path> Content/Text/ST_Example.uasset
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project
import _uasset


def _fstring(blob, at):
    (n,) = struct.unpack_from('<i', blob, at)
    at += 4
    if n == 0:
        return '', at
    if n > 0:
        raw = blob[at:at + n - 1]
        return raw.decode('utf-8', 'replace'), at + n
    n = -n
    raw = blob[at:at + (n - 1) * 2]
    return raw.decode('utf-16-le', 'replace'), at + n * 2


def entries(path):
    """Every (key, source) pair the asset at `path` carries. An asset that is not a
    `UStringTable` answers with an empty map."""
    blob = open(path, 'rb').read()
    summary = _uasset.read_summary(blob)
    names = _uasset.read_names(blob, summary)
    imports = _uasset.read_imports(blob, summary, names)
    exports = _uasset.read_exports(blob, summary, names)

    def class_of(export):
        index = getattr(export, 'class_index', 0)
        if index < 0 and -index - 1 < len(imports):
            return str(getattr(imports[-index - 1], 'object_name', ''))
        return ''

    stem = os.path.splitext(os.path.basename(path))[0]
    for export in exports:
        if class_of(export) != 'StringTable':
            continue
        if str(getattr(export, 'object_name', '')) != stem:
            continue
        body = blob[export.serial_offset:export.serial_offset + export.serial_size]

        # The body is: the tagged-property terminator, the table id as an FString, the entry count,
        # then the entries. Neither the terminator's width nor the entry layout is the same on every
        # asset, and the table id can differ from the asset name. So every shape is read and the one
        # that accounts for the whole body wins. A probe that ends early, runs off the end or reads
        # an empty key has taken the wrong shape.
        answers = []
        for head in range(8, 20):
            try:
                _, at = _fstring(body, head)
                (count,) = struct.unpack_from('<i', body, at)
            except Exception:
                continue
            if count < 1 or count > len(body):
                continue
            for trailing in (0, 4):
                rows = _read_entries(body, at + 4, count, trailing)
                if rows is not None:
                    answers.append(rows)
        if not answers:
            raise ValueError('%s: no entry layout fits the export body' % path)
        if any(rows != answers[0] for rows in answers[1:]):
            raise ValueError('%s: %d entry layouts fit and they disagree' % (path, len(answers)))
        return answers[0]
    return {}


def _read_entries(body, at, count, trailing):
    """`count` (key, source) pairs from `at`, or None when this layout does not fit."""
    rows = {}
    try:
        for _ in range(count):
            key, at = _fstring(body, at)
            value, at = _fstring(body, at)
            at += trailing
            if not key or at > len(body):
                return None
            rows[key] = value
    except Exception:
        return None
    if len(rows) != count or len(body) - at > 8:
        return None
    return rows


if __name__ == '__main__':
    _project.optional_project_root()
    for arg in sys.argv[1:]:
        rows = entries(arg)
        print('%s: %d' % (arg, len(rows)))
        for key, value in rows.items():
            print('  %s = %s' % (key, value[:70].replace('\n', ' ')))
