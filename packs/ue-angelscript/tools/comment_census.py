"""Comment census for the AngelScript layer - report-only, never writes.

A comment layer grows when each change writes its own reasoning into the file: dates, agent ids,
tick numbers, work-queue entry numbers, text export filenames, node counts, PropertyFlags hex, quotes,
and retractions of comments earlier in the same file. This measures that, so a cleanup can be aimed
and then checked.

    python comment_census.py                  # per-file table, worst first
    python comment_census.py --markers        # count each narration marker
    python comment_census.py --stacked        # doc comments detached from their declaration
    python comment_census.py --preludes       # narration blocks above a doc comment
    python comment_census.py --violations     # every comment line that fails the comment rule
    python comment_census.py --undocumented   # every UPROPERTY / UFUNCTION with no comment
    python comment_census.py Script/Gameplay  # limit to a subtree

WHAT COUNTS AS NARRATION is the `markers` table. Each pattern names something that describes the
change history rather than the code, so a reader who wants to know what the code does has to skip it.

`--stacked` finds real defects rather than noise: two `/** */` blocks in a row means the first one
is attached to nothing, so it now documents whichever symbol follows the second.

Project settings (`.octopus/ue-angelscript.json`, object `comment_census`): `markers` and
`violations` replace the default pattern tables, as lists of [name, pattern]; a pattern carries
its flags inline, e.g. `(?i)`. `keptPattern` replaces the pattern of the exempt anchor lines.
"""

from __future__ import annotations

import argparse
import os
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("comment_census", {})
CWD0 = os.getcwd()


def _project_path(p):
    """An input path. A relative path that exists in the project stays relative to the project
    root; otherwise it resolves against the caller's directory."""
    if os.path.isabs(p) or os.path.exists(os.path.join(ROOT, p)):
        return p
    here = os.path.join(CWD0, p)
    return os.path.abspath(here) if os.path.exists(here) else p


DEFAULT_MARKERS = {
    'date':            re.compile(r'\b20\d\d-\d\d-\d\d\b'),
    'agent-id':        re.compile(r'\b(claude|agent)-[a-z0-9-]{3,}\b'),
    'tick-number':     re.compile(r'\btick \d+', re.I),
    'export-forensics': re.compile(r'\b(T3[D]|K2Node|ExtraFlags|PropertyFlags|FunctionFlags'
                                  r'|CPF_[A-Za-z]+|pin literal|unwired)\b'),
    'node-counts':     re.compile(r'\b\d+ (nodes|exec links|call nodes|graphs)\b', re.I),
    'ported-by':       re.compile(r'\bPORTED\b.{0,40}\bby\b', re.I | re.S),
    'shouting':        re.compile(r'\*\*[A-Z][A-Z ,\'`.\-]{10,}'),
    'process-meta':    re.compile(r'\b(this port|no longer a seam|recorded as blocked'
                                  r'|WHY IT EXISTS)\b', re.I),
    'todo-marker':     re.compile(r'TODO\('),
}

MARKERS = ({k: re.compile(p) for k, p in CFG['markers']} if 'markers' in CFG
           else DEFAULT_MARKERS)


def comment_spans(lines):
    """Yield (start, end, style) for every contiguous comment run. 0-based, inclusive."""
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith('/**'):
            st = i
            while i < len(lines) and not lines[i].rstrip().endswith('*/'):
                i += 1
            yield (st, i, 'doc')
        elif s.startswith('/*'):
            st = i
            while i < len(lines) and '*/' not in lines[i]:
                i += 1
            yield (st, i, 'blk')
        elif s.startswith('//'):
            st = i
            while i + 1 < len(lines) and lines[i + 1].strip().startswith('//'):
                i += 1
            yield (st, i, 'line')
        i += 1


def stacked(lines):
    """Doc comments immediately followed by another doc comment — attached to no declaration."""
    spans = [s for s in comment_spans(lines)]
    out = []
    for a, b in zip(spans, spans[1:]):
        if a[2] == 'doc' and b[2] == 'doc' and b[0] == a[1] + 1:
            out.append((a[0] + 1, a[1] + 1, lines[a[0]].strip()[:70]))
    return out


# A sentence carrying one of these constrains what a future editor may do, or names a player-visible
# cost. It must not disappear with the narration around it.
SALVAGE = re.compile(
    r'in plain words|\bmust not\b|\bdo not\b|\bnever\b|\bcannot\b|\bwill not\b|\bdesync'
    r'|\bSaveGame\b|\bReplicated\b|\bNSLOCTEXT\b|\bLOCTABLE\b|\bfail[- ]closed\b|\bbSizeToFit\b'
    r'|\breserved word|\bDisplayName\b|\bFindRow\b|\brow struct\b|\bsilently\b|TODO\(', re.I)


def preludes(path):
    """`//` narration blocks sitting directly above a /** */ doc comment.

    Redundant by construction: the doc comment below already says what the symbol is, so the block is
    the port's diary. Returns (start, end, size, salvage_lines) with 1-based inclusive lines.
    """
    lines = path.read_text(encoding='utf-8').replace('\r\n', '\n').split('\n')
    spans = list(comment_spans(lines))
    out = []
    for i, (s, e, style) in enumerate(spans):
        if style != 'line' or i + 1 >= len(spans):
            continue
        ns, _, nstyle = spans[i + 1]
        if nstyle != 'doc' or any(lines[k].strip() for k in range(e + 1, ns)):
            continue
        body = lines[s:e + 1]
        salv = [(n + s + 1, l.strip()) for n, l in enumerate(body) if SALVAGE.search(l)]
        # Narration only counts where it is NOT part of a fact worth keeping. Otherwise a legitimate
        # note like "must NOT carry `Replicated`: the Blueprint carried no CPF_Net" reads as diary,
        # because `CPF_Net` is itself one of the forensics markers.
        kept = {ln for ln, _ in salv}
        noise = [l for n, l in enumerate(body) if n + s + 1 not in kept
                 and any(rx.search(l) for k, rx in MARKERS.items() if k != 'todo-marker')]
        if not noise:
            continue
        out.append((s + 1, e + 1, e - s + 1, salv))
    return out



# --- the rule test -------------------------------------------------------------------------------
# A comment sentence stays only when it describes the thing it is attached to. Each pattern below
# matches a sentence that a DATA edit, a new subclass or a finished feature would falsify, or one of
# the forms the comment rule bans outright.

# Machine-read anchors the rule exempts. A line that is one of these is never a violation.
KEPT = re.compile(CFG['keptPattern']) if 'keptPattern' in CFG else re.compile(
    r'^\s*//\s*(Migrated from .* Blueprint variable|DOC:\s'
    r'|Legacy (variables|functions) of |---)'
    r'|TODO\(')

# One entry of a legacy-name reference block: two spaces, a name, a dash.
LEGACY_ENTRY = re.compile(r'^\s*//\s{2,}\S.*?\s[-—]\s')

ACRONYMS = set(
    'AI API BP CDO CVAR DA DLC DPI DSL DT EMS EOS FMOD GUID HUD ICE ID INI JSON LTR MCP NPC OK PC '
    'PIE PVE PVP RGB RHI RPC RTL SCS SFX STE UE UI UMG URL UTF UUID VCS XP'.split()) | {'T3' 'D'}

DEFAULT_VIOLATIONS = [
    ('authored-data', re.compile(
        r'\bauthored (range|as|rows?|data|values?|CDO|padding|two|three|four|five|six|seven)\b'
        r'|\bin the authored data\b|\bempty on (the|every|all)\b|\bthe authored (CDO|rows?|table)\b'
        r'|\b\d+ of \d+ rows?\b|\b\d+ authored\b'
        r'|\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d+)[- ](rows?|entries'
        r'|entry|variants?|characters?|elements?|values?)\b'
        r'|\brows? (carry|carries|read|reads|spell)\b', re.I)),
    ('design-assumption', re.compile(
        r'\bso (they|it) stay(s)?\b|\bneither yet\b|\ba convention nothing enforces\b'
        r'|\bhas a future\b'
        r'|\bnever will be\b|\b(is|are) meant to become\b|\bis expected to become\b'
        r'|\bbeing (DELETED|deleted), not\b|\bwill (be removed|never)\b'
        r'|\bis not ported and never\b|\bcorrect as it stands\b', re.I)),
    ('speaks-for-children', re.compile(
        r'\boverridden by\b|\bchildren override\b|\bChildren:'
        r'|\bsubclass(es)? (override|carry|author|reparent)\b'
        r'|\ball (three|four|five|six|seven|eight|nine|ten|twelve|\d+) (children'
        r'|characters|widgets|leaves)\b|\bstays on the children\b'
        r'|\bthe children implement\b|\bempty on (the parent|this base)\b', re.I)),
    ('history', re.compile(
        r'\bused to\b|\bauthored\b|\bthe Blueprint\b|\bformerly\b|\bpreviously\b|\boriginally\b'
        r'|\bwas once\b|\bhad been\b|\bpredates\b|\bno longer\b|\bthat expired\b|\bis gone\b'
        r'|\brenamed\b|\brename[ds]? (from|off)\b|\bcarried over\b|\bthe port\b|\bported\b'
        r'|\brestored\b|\bfolded\b|\bsuperseded\b|\breplaces?d? the\b|\bre-?measured\b'
        r'|\bthe old\b|\bbefore we\b|\bwe moved\b|\bstill (owns|carries|declares)\b', re.I)),
    ('defence', re.compile(
        r'\bmust stay\b|\bmust not\b|\bso (they|it) stays?\b|\bdo not\b|\bdont\b'
        r'|\bnever (rename|reorder|change|remove|add|tidy|fix|collapse|touch|declare)\b'
        r'|\bkeep (this|the|it|them)\b|\bis kept\b|\bkept\b|\bdo NOT\b|\bleave (it|this) alone\b'
        r'|\bload[- ]bearing\b|\bis mandatory\b', re.I)),
    ('reaches-past', re.compile(
        r'\brenders? as\b|\bon screen\b|\bFindRow\b|\bthe compiler\b|\bsilently\b'
        r'|\breflection\b|\bserialis|\bserializ|\bthe caller (reads|sees|gets)\b'
        r'|\bevery caller\b|\bconsumers?\b|\breinstance\b|\bhot reload\b', re.I)),
    ('introspection', re.compile(
        r'\bdeliberate(ly)?\b|\bon purpose\b|\bfaithful\b|\bverbatim\b|\breproduced\b'
        r'|\bnot a defect\b|\bharmless\b|\bprobably\b|\bI think\b|\bworth\b|\bthe reason\b'
        r'|\bwhich is why\b|\bthat is why\b|\bthe whole point\b|\bthe defect\b', re.I)),
    ('absence-as-prose', re.compile(
        r'\bno consumer\b|\bno callers?\b|\bnothing (calls|reads|writes|fills|drives|broadcasts'
        r'|positions|uses)\b|\bnot ported\b|\bnot wired\b|\bunwired\b'
        r'|\bis dead\b|\bare dead\b|\bdoes nothing\b|\bdo nothing\b|\bNOTHING USES\b'
        r'|\bNot deprecated\b|\bseams?\b|\bempty stub\b|\bis a stub\b|\bnever ported\b'
        r'|\bdisconnected island\b|\bdetached island\b', re.I)),
    ('provenance', re.compile(
        r'\b20\d\d-\d\d-\d\d\b|\btick \d+\b|PropertyFlags|K2Node|\bT3[D]\b'
        r'|\bre-?measured\b|\bmeasured across\b|§\d+'
        r'|\b\d+ (nodes?|assets|packages|graphs|call sites|callables|errors|widgets|referencers)\b'
        r'|\bfiled as\b', re.I)),
    ('blueprint-word', re.compile(
        r'\bpins?\b|\bexecs?\b|\bnodes?\b|\bgraphs?\b|\bubergraphs?\b|\bmacros?\b'
        r'|\bK2Node\b|\bSCS\b|\bRowNotFound\b|\bRow Not Found\b'
        r'|\bre-?wire[ds]?\b|\bwired\b|\bunwired\b|\bconnected\b|\bdisconnected\b'
        r'|\bconnections?\b|\bislands?\b', re.I)),
]

VIOLATIONS = ([(k, re.compile(p)) for k, p in CFG['violations']] if 'violations' in CFG
              else DEFAULT_VIOLATIONS)

def _shouting(text):
    """Two or more shouted words in a row. An approved acronym does not count."""
    words = re.findall(r"[A-Za-z][A-Za-z']*", text)
    run = 0
    for w in words:
        if len(w) > 2 and w.isupper() and w not in ACRONYMS:
            run += 1
            if run >= 2:
                return True
        else:
            run = 0
    return bool(re.search(r'\*\*[A-Z][A-Z ]{6,}', text))


def violations(path):
    """Every comment line that fails the rule, as (line, category, text). 1-based."""
    lines = path.read_text(encoding='utf-8').replace('\r\n', '\n').split('\n')

    start = 0
    for i, l in enumerate(lines[:80]):
        if l.startswith('// --- END SCRIPT-GRAPH'):
            start = i + 1
            break

    out = []
    for i in range(start, len(lines)):
        raw = lines[i]
        t = raw.strip()
        if not t.startswith(('//', '/*', '*')):
            continue
        if KEPT.search(raw) or LEGACY_ENTRY.match(raw):
            continue
        body = re.sub(r'^[/*\s]+', '', t).rstrip('*/ ')
        if not body:
            continue
        for name, pat in VIOLATIONS:
            if pat.search(body):
                out.append((i + 1, name, body[:110]))
        if _shouting(body):
            out.append((i + 1, 'shouting', body[:110]))

    for s, e, style in comment_spans(lines):
        if e < start or KEPT.search(lines[s]) or LEGACY_ENTRY.match(lines[s]):
            continue
        if e - s + 1 > 8:
            out.append((s + 1, 'over-length',
                        '%d-line block: %s' % (e - s + 1, lines[s].strip()[:80])))
    return sorted(out)



DECL = re.compile(r'^\s*(UPROPERTY|UFUNCTION)\s*\(')


def undocumented(path):
    """Every UPROPERTY / UFUNCTION with no comment line above it, as (line, text). 1-based."""
    lines = path.read_text(encoding='utf-8').replace('\r\n', '\n').split('\n')
    out = []
    for i, l in enumerate(lines):
        if not DECL.match(l):
            continue
        j = i - 1
        while j >= 0 and lines[j].strip() == '':
            j -= 1
        prev = lines[j].strip() if j >= 0 else ''
        if not prev.startswith(('//', '*', '/*')):
            # name the declaration the specifier sits on, which is the line after it
            decl = lines[i + 1].strip() if i + 1 < len(lines) else ''
            out.append((i + 1, (l.strip() + '  ->  ' + decl)[:110]))
    return out


def survey(path):
    text = path.read_text(encoding='utf-8')
    lines = text.replace('\r\n', '\n').split('\n')
    spans = list(comment_spans(lines))
    ncomment = sum(e - s + 1 for s, e, _ in spans)
    hits = {k: len(p.findall(text)) for k, p in MARKERS.items()}
    return {
        'path': path, 'lines': len(lines), 'comment': ncomment, 'blocks': len(spans),
        'markers': hits, 'noise': sum(v for k, v in hits.items()
                                      if k not in ('todo-marker', 'doc-pointer')),
        'stacked': stacked(lines),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('roots', nargs='*', default=['Script'])
    ap.add_argument('--markers', action='store_true', help='marker totals across the layer')
    ap.add_argument('--stacked', action='store_true', help='only the detached doc comments')
    ap.add_argument('--preludes', action='store_true',
                    help='narration blocks above a doc comment, worst first, with lines to salvage')
    ap.add_argument('--violations', action='store_true',
                    help='every comment line that fails the comment rule')
    ap.add_argument('--undocumented', action='store_true',
                    help='every UPROPERTY / UFUNCTION with no comment above it')
    args = ap.parse_args()
    args.roots = [_project_path(r) for r in args.roots]
    os.chdir(ROOT)

    files = []
    for r in args.roots:
        p = Path(r)
        files.extend(sorted(p.rglob('*.as')) if p.is_dir() else [p])

    rows = [survey(f) for f in files]
    tl = sum(r['lines'] for r in rows)
    tc = sum(r['comment'] for r in rows)

    if args.undocumented:
        total = 0
        byfile = {}
        for f in files:
            for ln, txt in undocumented(f):
                print('%s:%d  %s' % (f, ln, txt))
                total += 1
                byfile[str(f)] = byfile.get(str(f), 0) + 1
        print('')
        print('%d undocumented declaration(s) in %d of %d file(s)' % (total, len(byfile), len(files)))
        return

    if args.violations:
        total = 0
        bycat = {}
        byfile = {}
        for f in files:
            for ln, cat, txt in violations(f):
                enc = sys.stdout.encoding or 'utf-8'
                safe = txt.encode(enc, 'replace').decode(enc, 'replace')
                print('%s:%d  %-20s %s' % (f, ln, cat, safe))
                total += 1
                bycat[cat] = bycat.get(cat, 0) + 1
                byfile[str(f)] = byfile.get(str(f), 0) + 1
        print('')
        for k, v in sorted(bycat.items(), key=lambda kv: -kv[1]):
            print('%7d  %s' % (v, k))
        print('')
        print('%d violation(s) in %d of %d file(s)' % (total, len(byfile), len(files)))
        return

    if args.stacked:
        n = 0
        for r in rows:
            for st, en, head in r['stacked']:
                print(f"{r['path']}:{st}-{en}  {head}")
                n += 1
        print(f'\n{n} detached doc comment(s) across {len(rows)} files')
        return

    if args.preludes:
        rows = [(f, p) for f in files for p in preludes(f)]
        rows.sort(key=lambda r: -r[1][2])
        clean = sum(1 for _, p in rows if not p[3])
        for f, (st, en, size, salv) in rows:
            print(f'{size:5d}  {f}:{st}-{en}'
                  f'{"" if salv else "   (nothing to salvage - safe to delete)"}')
            for ln, txt in salv:
                print(f'         keep {ln}: {txt[:120]}')
        print(f'\n{len(rows)} narration preludes, {sum(p[2] for _, p in rows)} lines. '
              f'{clean} carry nothing worth salvaging; the other {len(rows) - clean} need the fact '
              f'folded into the doc comment below before the block goes.')
        return

    if args.markers:
        tot = {}
        for r in rows:
            for k, v in r['markers'].items():
                tot[k] = tot.get(k, 0) + v
        for k, v in sorted(tot.items(), key=lambda kv: -kv[1]):
            print(f'{v:7d}  {k}')
        print(f'\n{len(rows)} files, {tl} lines, {tc} comment lines ({100*tc/tl:.0f}%)')
        return

    print(f"{'comment/lines':>14}  {'%':>3}  {'noise':>5}  file")
    for r in sorted(rows, key=lambda r: -r['comment']):
        pct = 100 * r['comment'] / max(r['lines'], 1)
        print(f"{r['comment']:6d}/{r['lines']:<7d} {pct:3.0f}  {r['noise']:5d}  {r['path']}")
    nstack = sum(len(r['stacked']) for r in rows)
    print(f"\n{len(rows)} files, {tl} lines, {tc} comment lines ({100*tc/tl:.0f}%), "
          f"{sum(r['noise'] for r in rows)} narration markers, {nstack} detached doc comments")


if __name__ == '__main__':
    main()
