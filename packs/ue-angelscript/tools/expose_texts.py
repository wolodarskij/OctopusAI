"""Move every text literal out of a function body and onto the class, where a designer can edit it.

A text written inline in a function is editable only by changing code. Hoisted to an
`EditDefaultsOnly` FText member it appears in the Details panel, it is gathered from the source
literal AND from the asset once a Blueprint overrides it, and it cannot be lost when a graph goes.

The default keeps the original expression, so the localisation key never changes and a parked
translation still matches:

    UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Text")
    FText txtYouNeedAKey = NSLOCTEXT("", "0FFFD63A...", "\\r\\nYou Need A Key");

    UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Text")
    FText txtWelcome = FText::FromStringTable(n"/Game/Text/Short.Short", "welcome");

    python expose_texts.py report
    python expose_texts.py hoist --file Script/... [--apply]

`report` splits what is left into hoistable and run-time. It is the guard, and it exits 1 while any
hoistable literal remains. A run-time site picks its key from an expression and stays where it is.

To rehearse a pass without touching the tree the editor compiles, run the tool with `--project`
on a copy of the project.

Project settings (`.octopus/ue-angelscript.json`, object `expose_texts`): `category`, the `Category`
the new members carry.
"""
import argparse
import io
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("expose_texts", {})
CATEGORY = CFG.get("category", "Text")
NL = chr(10)
CALL = re.compile(r'(NSLOCTEXT|FText::FromStringTable)\s*\(')
CLASS = re.compile(r'^\s*class\s+(\w+)\s*(?::\s*\w+)?\s*$')
# a member declaration: a type then a name then '=' or ';'
MEMBER = re.compile(r'^\t(?:private |protected |public )?[\w:<>,\s]+\s+\w+\s*(?:=[^;]*)?;\s*$')
FUNC = re.compile(r'^\t(?:private |protected |public )?[\w:<>,&\s]+\s+\w+\s*\(')


def read(path):
    return io.open(path, encoding='utf-8').read()


def write(path, text):
    io.open(path, 'w', encoding='utf-8', newline=NL).write(text)


def match_paren(text, open_index):
    """Index just past the ')' that closes the '(' at open_index. Skips string literals."""
    depth = 0
    i = open_index
    in_str = False
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == '\\':
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def class_bodies(text):
    """(class name, body start index, body end index) for every class in the file."""
    out = []
    lines = text.split(NL)
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    for i, line in enumerate(lines):
        m = CLASS.match(line)
        if not m:
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() != '{':
            if lines[j].strip():
                break
            j += 1
        if j >= len(lines) or lines[j].strip() != '{':
            continue
        depth = 0
        k = j
        while k < len(lines):
            depth += lines[k].count('{') - lines[k].count('}')
            if depth == 0:
                break
            k += 1
        out.append((m.group(1), offsets[j + 1], offsets[min(k, len(lines) - 1)]))
    return out


def insertion_line(lines, body_first_line):
    """The line index to put new members on: after the last member, before the first function."""
    last_member = body_first_line
    for i in range(body_first_line, len(lines)):
        s = lines[i].strip()
        if s == '}' and not lines[i].startswith('\t'):
            break
        if FUNC.match(lines[i]) or s.startswith('UFUNCTION('):
            break
        if MEMBER.match(lines[i]):
            last_member = i + 1
    return last_member


CAMEL = re.compile(r'[^0-9A-Za-z]+')


def name_for(expr, used):
    """A readable member name: the English source when there is one, else the table row key."""
    src = None
    m = re.search(r'NSLOCTEXT\(\s*"[^"]*"\s*,\s*"[^"]+"\s*,\s*"((?:[^"\\]|\\.)*)"', expr, re.S)
    if m:
        src = m.group(1)
    else:
        m = re.search(r'FromStringTable\([^,]+,\s*"([^"]+)"', expr, re.S)
        if m:
            src = m.group(1)
    if not src:
        m = re.search(r'"([0-9A-F]{8,})"', expr)
        src = m.group(1) if m else 'text'

    src = src.replace('\\r', ' ').replace('\\n', ' ').replace('<br>', ' ')
    src = re.sub(r'\{[^}]*\}', ' ', src)                 # format slots say nothing about the name
    src = re.sub(r'<[^>]*>', ' ', src)                   # rich-text markup likewise
    words = [w for w in CAMEL.split(src) if w][:5]
    if not words:
        words = ['text']
    base = 'txt' + ''.join(w[:1].upper() + w[1:] for w in words)
    base = base[:52]
    name = base
    n = 2
    while name in used:
        name = '%s%d' % (base, n)
        n += 1
    used.add(name)
    return name


STRLIT = re.compile(r'n?"(?:[^"\\]|\\.)*"', re.S)


LOCAL_TABLE = re.compile(r'^\s*(?:const\s+)?FName\s+(\w+)\s*=\s*(n"[^"]+")\s*;', re.M)


def local_tables(text):
    """`const FName exampleTable = n"/Game/Text/...";` -> the literal, when the name is declared once.

    A member initialiser cannot read a function local, so the literal is substituted into it. Only a
    name declared exactly once in the file qualifies: two declarations mean two different tables.
    """
    found = {}
    for m in LOCAL_TABLE.finditer(text):
        found.setdefault(m.group(1), []).append(m.group(2))
    return dict((k, v[0]) for k, v in found.items() if len(v) == 1)


def inline_locals(expr, table):
    for name, literal in table.items():
        expr = re.sub(r'(?<![\w.])%s(?![\w])' % re.escape(name), literal, expr)
    return expr


def is_constant(expr):
    """True when every argument is a literal, so the expression can be a member initialiser.

    `FromStringTable(table, usable ? "a" : "b")` picks its row at run time and has to stay where it
    is: a member initialiser cannot read a local.
    """
    inner = expr[expr.index('(') + 1:-1]
    rest = STRLIT.sub('', inner)
    return not re.search(r'\w', rest)


def find_sites(text, start, end):
    """Every top-level text call in [start, end). Inner calls of a hoisted one are not separate."""
    sites = []
    i = start
    while True:
        m = CALL.search(text, i)
        if not m or m.start() >= end:
            break
        open_i = m.end() - 1
        close = match_paren(text, open_i)
        if close < 0:
            i = m.end()
            continue
        sites.append((m.start(), close, text[m.start():close]))
        i = close
    return sites


MEMBER_NAME = re.compile(r'^\t(?:private |protected |public )?[\w:<>,\s]+\s+(\w+)\s*(?:=|;)', re.M)


def every_member_name():
    """Every member name in the layer. A generated name has to miss all of them: a child class that
    declares a name its parent already has is a hard compile error, and the parent is another file."""
    names = set()
    for root, dirs, fs in os.walk(os.path.join(ROOT, 'Script')):
        for f in fs:
            if f.endswith('.as'):
                names |= set(MEMBER_NAME.findall(read(os.path.join(root, f))))
    return names


def hoist_file(path, apply_it, used=None):
    text = read(path)
    bodies = class_bodies(text)
    if not bodies:
        return 0, 'no class in this file'

    lines = text.split(NL)
    line_offset = []
    pos = 0
    for line in lines:
        line_offset.append(pos)
        pos += len(line) + 1

    if used is None:
        used = every_member_name()
    tables = local_tables(text)

    # every edit as a character offset on the ORIGINAL text, so nothing drifts
    edits = []          # (start, end, replacement)
    members = 0
    skipped = 0
    for _cname, bstart, bend in bodies:
        first_line = text[:bstart].count(NL)
        seen = {}
        decls = []
        for s, e, expr in find_sites(text, bstart, bend):
            line_start = text.rfind(NL, 0, s) + 1
            line_end = text.find(NL, s)
            if re.match(r'^\tFText \w+ =', text[line_start:line_end]):
                continue                                    # already a member initialiser
            expr = inline_locals(expr, tables)
            if not is_constant(expr):
                skipped += 1
                continue
            key = re.sub(r'\s+', ' ', expr)
            if key in seen:
                edits.append((s, e, seen[key]))
                continue
            name = name_for(expr, used)
            seen[key] = name
            edits.append((s, e, name))
            body = re.sub(r'^[\t ]+', '\t\t', expr, flags=re.M)
            decls.append('\tUPROPERTY(EditDefaultsOnly, BlueprintReadWrite, '
                         'Category = "%s", meta = (MultiLine = true))' % CATEGORY)
            decls.append('\tFText %s = %s;' % (name, body.strip()))
            decls.append('')
            members += 1
        if decls:
            while decls and decls[-1] == '':
                decls.pop()
            at = insertion_line(lines, first_line)
            before = lines[at - 1].strip() if at else ''
            lead = [] if before in ('{', '') else ['']
            block = NL.join(lead + decls + ['']) + NL
            edits.append((line_offset[at], line_offset[at], block))

    if not edits:
        return 0, 'nothing inline (%d run-time site(s) left alone)' % skipped

    for s, e, rep in sorted(edits, key=lambda x: (-x[0], -x[1])):
        text = text[:s] + rep + text[e:]

    if apply_it:
        write(path, text)
    note = 'hoisted %d site(s), %d new member(s)' % (len(edits) - len(bodies) + 1, members)
    if skipped:
        note += ', %d run-time site(s) left alone' % skipped
    return members, note


def files_with_sites():
    out = []
    for root, dirs, fs in os.walk(os.path.join(ROOT, 'Script')):
        for f in sorted(fs):
            if not f.endswith('.as'):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, ROOT).replace(os.sep, '/')
            text = read(p)
            bodies = class_bodies(text)
            tables = local_tables(text)
            hoistable = 0
            runtime = 0
            for _c, bstart, bend in bodies:
                for s, _e, expr in find_sites(text, bstart, bend):
                    line_start = text.rfind(NL, 0, s) + 1
                    line_end = text.find(NL, s)
                    if re.match(r'^\tFText \w+ =', text[line_start:line_end]):
                        continue
                    if is_constant(inline_locals(expr, tables)):
                        hoistable += 1
                    else:
                        runtime += 1
            if hoistable or runtime:
                out.append((rel, hoistable, runtime))
    return out


def cmd_report(args):
    rows = files_with_sites()
    hoistable = sum(r[1] for r in rows)
    runtime = sum(r[2] for r in rows)
    print('%-58s %9s %9s' % ('FILE', 'HOISTABLE', 'RUN-TIME'))
    for rel, h, rt in sorted(rows, key=lambda r: (-r[1], -r[2])):
        print('%-58s %9d %9d' % (rel, h, rt))
    print('--- %d hoistable, %d run-time (a key chosen by an expression, correctly left alone)'
          % (hoistable, runtime))
    return 1 if hoistable else 0


def cmd_hoist(args):
    paths = args.file or [r[0] for r in files_with_sites()]
    used = every_member_name()          # shared across the run, so no two files pick one name
    total = 0
    for rel in paths:
        p = os.path.join(ROOT, rel.replace('/', os.sep))
        n, msg = hoist_file(p, args.apply, used)
        total += n
        print('%-60s %s' % (rel, msg))
    print('--- %d site(s)%s' % (total, '' if args.apply else '   (dry run)'))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command')
    p = sub.add_parser('report')
    p.set_defaults(func=cmd_report)
    p = sub.add_parser('hoist')
    p.add_argument('--file', action='append')
    p.add_argument('--apply', action='store_true')
    p.set_defaults(func=cmd_hoist)
    a = ap.parse_args()
    if not getattr(a, 'func', None):
        ap.print_help()
        return 2
    return a.func(a)


if __name__ == '__main__':
    sys.exit(main())
