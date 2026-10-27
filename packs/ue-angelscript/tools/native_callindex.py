# -*- coding: utf-8 -*-
"""Build the AngelScript + C++ half of the call index.

`build_callindex.py` (blueprint pack) answers "who calls X" for Blueprint callers. It cannot see
the native layer. This tool reads the native layer, `Source/` and `Script/`, and nothing else:

  DECLARATIONS  a `class X` / `class X : Y` opens a scope; a signature line inside it
                declares a member. `UFUNCTION(...)` on the preceding line marks it
                reflected, which is what makes it callable from a Blueprint.
  CALLS         `Obj.Member(`, `Obj->Member(`, `Class::Member(`, or a bare `Member(`
                in statement position.

Only members the project's OWN native layer declares are counted. A call to
`World.GetGameState()` or `Tags.Add()` is engine API and is discarded.

The output merges into a call index JSON file under `native`, in the same
{owner: {member: [callers]}} shape the Blueprint half uses.

    python native_callindex.py --merge Docs/_callindex.json

Relative paths resolve against the project root.
"""
from __future__ import unicode_literals
import argparse
import collections
import io
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

ROOT = str(_project.project_root())

# Every relative path, the defaults and the arguments alike, resolves against the project root.
os.chdir(ROOT)

try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

CLASS_AS = re.compile(r'^\s*(?:class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)')
CLASS_CPP = re.compile(r'^\s*(?:class|struct)\s+(?:[A-Z_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)')
# A signature: <return type> Name(  -- the return type is one token, optionally templated.
SIG = re.compile(
    r'^\s*(?:virtual\s+|static\s+|const\s+)*'
    r'(?:[A-Za-z_][A-Za-z0-9_]*(?:<[^>]*>)?[&*]?\s+)'
    r'([A-Za-z_][A-Za-z0-9_]*)\s*\(')
CALL_QUALIFIED = re.compile(r'(?:\.|->|::)\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(')
CALL_BARE = re.compile(r'(?:^|[\s;{}(,=!&|+\-])([A-Za-z_][A-Za-z0-9_]*)\s*\(')

# Words that match SIG or CALL_BARE but are language, not members.
NOISE = set('''if for while switch return catch sizeof new delete else do try throw
class struct enum union namespace template typename operator case default and or not
UFUNCTION UPROPERTY UCLASS USTRUCT UENUM UMETA GENERATED_BODY DECLARE_LOG_CATEGORY_EXTERN
DEFINE_LOG_CATEGORY UE_LOG check ensure Cast TEXT NSLOCTEXT LOCTEXT static_cast
const_cast reinterpret_cast dynamic_cast'''.split())


def files(root, exts):
    for base, dirs, names in os.walk(root):
        low = base.replace('\\', '/').lower()
        if '/intermediate/' in low or '/uht/' in low:
            continue
        for n in names:
            if n.endswith(exts) and not n.endswith('.generated.h'):
                yield os.path.join(base, n)


def read(path):
    return io.open(path, encoding='utf-8', errors='replace').read()


def scan_declarations(roots):
    """member -> set(owner class). A member may be declared by more than one class."""
    owners = collections.defaultdict(set)
    reflected = set()
    per_file = {}
    for root, exts, is_as in roots:
        if not os.path.isdir(root):
            continue
        for path in files(root, exts):
            text = read(path)
            cur = os.path.basename(path).rsplit('.', 1)[0]
            primary = None
            prev_reflected = False
            found = []
            for line in text.split('\n'):
                stripped = line.strip()
                m = (CLASS_AS if is_as else CLASS_CPP).match(line)
                if m and not stripped.endswith(';'):
                    cur = m.group(1)
                    if primary is None:
                        primary = cur
                if 'UFUNCTION' in stripped:
                    prev_reflected = True
                    continue
                s = SIG.match(line)
                if s:
                    name = s.group(1)
                    if name not in NOISE and not stripped.startswith('//'):
                        owners[name].add(cur)
                        found.append(name)
                        if prev_reflected:
                            reflected.add(name)
                prev_reflected = False
            per_file[path] = (primary or os.path.basename(path), found)
    return owners, reflected, per_file


def scan_calls(roots, known, per_file, ambiguous):
    """owner -> member -> set(caller). Only members in `known` are counted.

    `ambiguous` names are skipped entirely rather than credited to every declarer: the
    type of the receiver in `Obj.Member(` cannot be resolved textually, so a project
    struct that declares `Num` would otherwise be credited with every `Array.Num()` call
    in the tree. Excluding is the conservative direction -- it under-reports rather than
    inventing edges.
    """
    calls = collections.defaultdict(lambda: collections.defaultdict(set))
    for root, exts, _is_as in roots:
        if not os.path.isdir(root):
            continue
        for path in files(root, exts):
            caller = per_file.get(path, (os.path.basename(path), []))[0]
            declared_here = set(per_file.get(path, (None, []))[1])
            text = read(path)
            for line in text.split('\n'):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*'):
                    continue
                if SIG.match(line):          # a declaration line is not a call site
                    continue
                names = set(CALL_QUALIFIED.findall(line))
                for n in CALL_BARE.findall(line):
                    names.add(n)
                for n in names:
                    if n in NOISE or n not in known or n in ambiguous:
                        continue
                    for owner in known[n]:
                        # A member calling itself inside its own class is not a cross-edge,
                        # but a sibling method calling it is -- keep both, drop only the
                        # case where the only "caller" is the declaring file itself and the
                        # name never appears qualified.
                        if owner == caller and n in declared_here:
                            continue
                        calls[owner][n].add(caller)
    return calls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--script-root', default='Script')
    ap.add_argument('--cpp-root', default='Source')
    ap.add_argument('--merge', help='path to the call index JSON file to merge into')
    ap.add_argument('--out', help='write the native section standalone')
    a = ap.parse_args()

    roots = [(a.script_root, ('.as',), True), (a.cpp_root, ('.h', '.cpp'), False)]
    owners, reflected, per_file = scan_declarations(roots)

    # A name is ambiguous when more than one project class declares it, or when it
    # collides with a common engine/container method -- in both cases `Obj.Name(` cannot
    # be attributed to a declarer without resolving Obj's type, which text cannot do.
    ENGINE_COMMON = set('''Num Add AddUnique Remove RemoveAt Empty Reset Contains Find
        FindIndex Insert Sort Last Top Push Pop IsEmpty IsValid GetWorld Init Tick
        NativeConstruct NativePreConstruct NativeTick Construct Destruct BeginPlay
        EndPlay GetName SetValue GetValue Select Clear Append Len Reserve'''.split())
    ambiguous = set(n for n, o in owners.items() if len(o) > 1) | (set(owners) & ENGINE_COMMON)

    calls = scan_calls(roots, owners, per_file, ambiguous)

    symbols = {}
    for owner in sorted(calls):
        symbols[owner] = dict((m, sorted(c)) for m, c in sorted(calls[owner].items()))

    section = {
        'sources': [a.script_root, a.cpp_root],
        'counts': {
            'filesScanned': len(per_file),
            'membersDeclared': len(owners),
            'membersReflected': len(reflected),
            'membersAmbiguous': len(ambiguous),
            'classesWithCallers': len(symbols),
            'distinctMembersCalled': sum(len(v) for v in symbols.values()),
        },
        'ambiguousMembers': sorted(ambiguous),
        'blindSpots': [
            'the receiver type in `Obj.Member(` cannot be resolved textually, so a member '
            'name declared by two project classes -- or colliding with a common engine '
            'method such as Num/Add/Init -- is EXCLUDED rather than credited to each. '
            'See ambiguousMembers; those have no caller list either way',
            'BlueprintEvent members are implemented in Blueprint; a Blueprint override is '
            'not a native call site and does not appear here',
        ],
        'symbols': symbols,
    }

    print('files scanned          %d' % len(per_file))
    print('native members declared %d  (%d reflected via UFUNCTION)'
          % (len(owners), len(reflected)))
    print('members ambiguous, excluded %d' % len(ambiguous))
    print('classes with callers   %d' % len(symbols))
    print('distinct members called %d' % section['counts']['distinctMembersCalled'])

    if a.out:
        io.open(a.out, 'w', encoding='utf-8').write(
            json.dumps(section, indent=1, ensure_ascii=False))
        print('wrote %s' % a.out)

    if a.merge:
        ix = json.load(io.open(a.merge, encoding='utf-8'))
        ix['native'] = section
        ix['sourcesNotSwept'] = [s for s in ix.get('sourcesNotSwept', [])
                                 if not s.startswith('cppAndAngelscript')]
        ix['sourcesSwept'].append({'source': 'nativeScriptAndCpp',
                                   'manifest': a.script_root + ' + ' + a.cpp_root,
                                   'assets': len(per_file)})
        io.open(a.merge, 'w', encoding='utf-8', newline='\n').write(
            json.dumps(ix, indent=1, ensure_ascii=False))
        print('merged into %s; sourcesNotSwept now %d'
              % (a.merge, len(ix['sourcesNotSwept'])))


if __name__ == '__main__':
    main()
