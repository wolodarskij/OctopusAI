"""Strip provenance clauses out of AngelScript comments. Comment lines only, never code.

A change log written into a comment records WHO did the work and WHEN, in front of the text that
describes the code:

    // FOLDED YYYY-MM-DD (agent-members). `UMyComponent.CanUse` is `return !bLocked` and REPLACES ...
    // PORTED YYYY-MM-DD (tick 12), replacing the fail-closed `Value = 0` above.
    //  *  Moved up YYYY-MM-DD (tick 9) to unblock TWO functions at once ...

The fact after the clause is worth keeping; the clause is not. The change log and the claim board
of the project record who changed what and when.

This removes ONLY the provenance clause - a leading FOLDED/PORTED/REVERTED/... sentence, a `(tick N)`,
a parenthesised agent id, a bare date. It never reflows prose, never merges sentences, and never
deletes a whole comment, which is what makes it safe to run over the whole layer. Judgement calls -
which paragraphs should exist at all - stay manual.

    python comment_deprovenance.py --report            # every line it would change, before/after
    python comment_deprovenance.py --report Script/Gameplay
    python comment_deprovenance.py --apply Script/Gameplay/MyComponent.as

`--apply` writes each file ONCE, so it costs one hot reload per file rather than one per edit.
Verify afterwards with `python comment_census.py --markers`.

Project settings (`.octopus/ue-angelscript.json`, object `comment_deprovenance`): `agentPattern`, the
pattern of an agent id.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("comment_deprovenance", {})
CWD0 = os.getcwd()


def _project_path(p):
    """An input path. A relative path that exists in the project stays relative to the project
    root; otherwise it resolves against the caller's directory."""
    if os.path.isabs(p) or os.path.exists(os.path.join(ROOT, p)):
        return p
    here = os.path.join(CWD0, p)
    return os.path.abspath(here) if os.path.exists(here) else p


DATE = r'20\d\d-\d\d-\d\d'
AGENT = CFG.get('agentPattern', r'(?:claude|agent)-[a-z0-9-]{2,}')
VERB = (r'FOLDED|PORTED|RE-?MEASURED|REVERTED|RESTORED|CORRECTED|MOVED UP|WRITTEN|EXTENDED'
        r'|DECLARED|ADDED|LANDED|CLOSED|RE-?CHECKED|VERIFIED|MIGRATED|SPLIT|REMOVED')

# Narrow by design. Comment prose WRAPS, so a sentence-level rule reaches across a line break it
# cannot see and eats half a fact. Every rule here matches a clause that is complete on ONE line;
# anything else is reported, not edited.
RULES = [
    # A leading provenance clause terminated on the same line:
    #   `FOLDED <date> (agent-members). `  `**PORTED <date> (tick 12), **`  `MOVED UP <date> - `
    (re.compile(rf'^\**(?:{VERB})\b[^.!?:;\-]{{0,40}}?{DATE}'
                rf'(?:\s*\((?:tick\s+\d+|{AGENT})\))*'
                rf'(?:\s+by\s+`?{AGENT}`?)?\**\s*[.,:;\-]\s*'), ''),
    # Parenthesised provenance anywhere on the line.
    (re.compile(rf'\s*\((?:tick\s+\d+|`?{AGENT}`?)\)'), ''),
    # `by `agent-text`` / `by agent-members`, attribution only.
    (re.compile(rf',?\s+by\s+`?{AGENT}`?(?=[\s.,:;)]|$)'), ''),
    # `at tick 36` / `, tick 21` - the number is never referred to again.
    (re.compile(r'\s*\b(?:at|by|from|since)\s+tick\s+\d+\b'), ''),
    (re.compile(r',?\s*\btick\s+\d+\b'), ''),
    # A bare date only where it reads as an aside: `, <date>,` / ` on <date>`.
    (re.compile(rf'\s+(?:on|since|from)\s+{DATE}\b'), ''),
    (re.compile(rf',\s*{DATE}\s*,'), ','),
    (re.compile(rf'\s*\(\s*{DATE}\s*\)'), ''),
]

CLEANUP = [
    (re.compile(r'\(\s*\)'), ''),
    (re.compile(r'\s+([,.;:])'), r'\1'),
    (re.compile(r'([,;:])\s*([.!?])'), r'\2'),
    (re.compile(r'\s{2,}(?=\S)'), ' '),
    # A verb left with nothing to say once its date went: `, declared.` / `REVERTED:` / `PORTED -`
    (re.compile(rf',?\s*(?:{VERB}|declared|added|written|moved up|ported|folded|landed)\s*[.\-]\s*$',
                re.I), '.'),
    (re.compile(rf'^\**(?:{VERB})\**\s*[:.\-]\s*'), ''),
    (re.compile(r'^[,;:]\s*'), ''),   # a clause whose opening aside was the thing removed
]


def comment_mask(lines):
    """True for lines that are entirely comment (or blank). Trailing `code // note` is False."""
    mask, depth = [], 0
    for ln in lines:
        s = ln.strip()
        if depth:
            mask.append(True)
            if '*/' in s:
                depth -= 1
            continue
        if not s or s.startswith('//'):
            mask.append(True); continue
        if s.startswith('/*'):
            mask.append(True)
            if '*/' not in s[2:]:
                depth += 1
            continue
        mask.append(False)
    return mask


def split_gutter(line):
    """Return (prefix, text) where prefix is `\t// ` or `\t *  ` or `\t/** `."""
    m = re.match(r'^(\s*(?://+|\*+|/\*\*+)\s*)(.*)$', line)
    if not m:
        return None, line
    return m.group(1), m.group(2)


def strip_line(line):
    prefix, text = split_gutter(line)
    if prefix is None or not text.strip():
        return line
    closing = ''
    if text.rstrip().endswith('*/'):
        text, closing = text.rstrip()[:-2].rstrip(), ' */'
    out = text
    for rx, rep in RULES:
        out = rx.sub(rep, out)
    if out != text:
        for rx, rep in CLEANUP:
            out = rx.sub(rep, out)
        out = out.strip()
        # A line reduced to nothing but a fragment marker is not worth keeping as text.
        if out in ('.', '-', '*', ''):
            return prefix.rstrip() + closing if closing else prefix.rstrip()
        # Keep sentence capitalisation when the clause that opened it went.
        if out[:1].islower() and text[:1].isupper():
            out = out[0].upper() + out[1:]
    return prefix + out + closing


def process(path, apply=False, report=False):
    raw = path.read_text(encoding='utf-8')
    nl = '\r\n' if '\r\n' in raw else '\n'
    lines = raw.replace('\r\n', '\n').split('\n')
    mask = comment_mask(lines)
    changed = 0
    out = list(lines)
    for i, ln in enumerate(lines):
        if not mask[i]:
            continue
        new = strip_line(ln)
        if new != ln:
            out[i] = new
            changed += 1
            if report:
                print(f'{path}:{i+1}')
                print(f'  -{ln}')
                print(f'  +{new}')
    if changed and apply:
        path.write_text(nl.join(out), encoding='utf-8', newline='')
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('roots', nargs='*', default=['Script'])
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--report', action='store_true')
    args = ap.parse_args()
    args.roots = [_project_path(r) for r in args.roots]
    os.chdir(ROOT)

    files = []
    for r in args.roots:
        p = Path(r)
        files.extend(sorted(p.rglob('*.as')) if p.is_dir() else [p])

    total = 0
    touched = 0
    for f in files:
        n = process(f, apply=args.apply, report=args.report)
        if n:
            touched += 1
            total += n
            if not args.report:
                print(f'{f}  {n} line(s)')
    verb = 'stripped' if args.apply else 'would strip'
    print(f'\n{verb} a provenance clause from {total} comment line(s) in {touched} of '
          f'{len(files)} file(s)', file=sys.stderr)


if __name__ == '__main__':
    main()
