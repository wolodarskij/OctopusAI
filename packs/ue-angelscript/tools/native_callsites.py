# -*- coding: utf-8 -*-
"""Classify occurrences of a member name in AngelScript and C++ as declaration or call.

The Blueprint call index (`build_callindex.py`, blueprint pack) answers for Blueprint callers. It says
nothing about native ones, and a bare name grep cannot tell `void UMyComponent::StartMove(...)` -
which DECLARES the thing - from `Mover->StartMove(...)`, which calls it.

This tool does the discrimination textually and conservatively:

  * generated code is skipped: anything under `Intermediate/`, `UHT/`, `Binaries/`, `ThirdParty/`
    or a `.generated.h` is machine-written and never a hand-authored call site
  * an occurrence is a DECLARATION when the name is preceded by a type-ish token or a
    `Class::` qualifier and followed by `(`  -> `void AMyActor::Bar(`, `bool Bar(`
  * an occurrence is a CALL when it is reached through `.`, `->`, `::` on an
    expression, or stands alone in statement position followed by `(`
  * anything else is UNCLASSIFIED and counts against promotion

A member is reported `declaration-only` ONLY when it has at least one declaration and
**zero** calls and **zero** unclassified occurrences. Anything less stays undecided.

Relative paths resolve against the project root.

Usage:
    python native_callsites.py --names Saved/native_names.json
    python native_callsites.py --name StartMove --name StopMove -v
"""
from __future__ import unicode_literals
import io
import os
import re
import sys
import json
import argparse
import collections
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

ROOT = str(_project.project_root())

# Every relative path, the defaults and the arguments alike, resolves against the project root.
os.chdir(ROOT)

SOURCE_DIRS = ('Script', 'Source', 'Plugins')
SOURCE_EXT = ('.as', '.h', '.cpp', '.hpp', '.inl')
GENERATED = re.compile(r'(^|[\\/])(Intermediate|UHT|Binaries|ThirdParty)[\\/]|\.generated\.h$', re.I)

# a type-ish token immediately before the name, optionally with a Class:: qualifier
DECL = r'(?:^|[\s\*&])(?:[A-Za-z_][\w:<>,\s\*&]*?\s+)(?:[A-Za-z_]\w*::)?%s\s*\('
# reached through an expression, or a bare statement-position call
CALL_MEMBER = r'(?:->|\.)\s*%s\s*\('
CALL_STATIC = r'(?:^|[\s{};])(?:Super|[A-Z]\w*)::%s\s*\('
CALL_BARE = r'(?:^|[\s{};(,])%s\s*\('


def source_files(root='.'):
    for d in SOURCE_DIRS:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            if GENERATED.search(dirpath + os.sep):
                continue
            for f in filenames:
                if f.endswith(SOURCE_EXT):
                    p = os.path.join(dirpath, f)
                    if not GENERATED.search(p):
                        yield p


def classify(names, root='.', verbose=False):
    """Return {name: {'decl': [...], 'call': [...], 'other': [...]}}."""
    pats = {}
    for n in names:
        e = re.escape(n)
        pats[n] = (re.compile(DECL % e), re.compile(CALL_MEMBER % e),
                   re.compile(CALL_STATIC % e), re.compile(CALL_BARE % e),
                   re.compile(r'\b%s\b' % e))
    out = dict((n, {'decl': [], 'call': [], 'other': []}) for n in names)
    scanned = 0
    for path in source_files(root):
        try:
            body = io.open(path, encoding='utf-8', errors='replace').read()
        except IOError:
            continue
        scanned += 1
        rel = path.replace('\\', '/')
        for n in names:
            decl, cm, cs, cb, word = pats[n]
            if not word.search(body):
                continue
            for i, line in enumerate(body.split('\n'), 1):
                if not word.search(line):
                    continue
                where = '%s:%d' % (rel, i)
                if cm.search(line) or cs.search(line):
                    out[n]['call'].append(where)
                elif decl.search(line):
                    out[n]['decl'].append(where)
                elif cb.search(line):
                    # bare Name( with nothing type-ish in front: a call
                    out[n]['call'].append(where)
                else:
                    out[n]['other'].append(where)
            if verbose:
                r = out[n]
                print('  %-28s %-52s decl=%d call=%d other=%d'
                      % (n, rel[-52:], len(r['decl']), len(r['call']), len(r['other'])))
    return out, scanned


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--names', help='JSON list of member names, or {"names": [...]}')
    ap.add_argument('--name', action='append', default=[], help='a single name; repeatable')
    ap.add_argument('--out', default='Saved/native_callsites.json')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args(argv)

    names = list(args.name)
    if args.names:
        data = json.load(io.open(args.names, encoding='utf-8'))
        names += data.get('names', data) if isinstance(data, dict) else data
    names = sorted(set(n for n in names if n))
    if not names:
        ap.error('give --names or at least one --name')

    result, scanned = classify(names, verbose=args.verbose)

    decl_only, has_call, undecided = [], [], []
    for n in names:
        r = result[n]
        if r['call']:
            has_call.append(n)
        elif r['decl'] and not r['other']:
            decl_only.append(n)
        else:
            undecided.append(n)

    payload = {
        'sourceFilesScanned': scanned,
        'skipped': 'Intermediate/, UHT/, Binaries/, ThirdParty/, *.generated.h',
        'declarationOnly': decl_only,
        'hasNativeCall': dict((n, result[n]['call'][:6]) for n in has_call),
        'undecided': dict((n, {'decl': result[n]['decl'][:3],
                               'other': result[n]['other'][:3]}) for n in undecided),
    }
    out_dir = os.path.dirname(args.out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with io.open(args.out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(payload, indent=1, ensure_ascii=False))

    print('scanned %d hand-written source files (generated code skipped)' % scanned)
    print('  declaration only, no call : %d' % len(decl_only))
    print('  has a native call site    : %d' % len(has_call))
    print('  undecided                 : %d' % len(undecided))
    print('wrote %s' % args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
