#!/usr/bin/env python3
"""stub_census.py - census every empty / near-empty UFUNCTION body in Script/.

An empty stub reads as finished work. This tool counts them, so the number is known.

It counts by anchored FORM (`^\\s*UFUNCTION\\(`), never by bare word. A bare grep also
matches comments and strings.

Usage:
    python stub_census.py                 # summary + TSV to stdout
    python stub_census.py --tsv out.tsv   # machine-readable
    python stub_census.py --all           # include non-empty bodies too
    python stub_census.py --enrich --json out.json   # add callers, docs hits and a verdict

Project settings (`.octopus/ue-angelscript.json`, object `stub_census`): the verdict table
`manual`, the `ruled` patterns, the comment patterns, and the documentation directories.

`--enrich` reads the Blueprint callers from the Blueprint export `references.json`, never from a
Blueprint. The `blueprint` pack writes the file:

    python "<Octopus AI>/packs/blueprint/tools/blueprint_export.py" references --project <project>

When the file is missing, `--enrich` prints the command and exits 2.
"""
import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("stub_census", {})
CWD0 = os.getcwd()

SCRIPT_ROOT = "Script"


def _caller_path(p):
    """An output path as the caller wrote it, resolved against the caller's directory."""
    return p if os.path.isabs(p) else os.path.join(CWD0, p)


def _project_path(p):
    """An input path. A relative path that exists in the project stays relative to the project
    root; otherwise it resolves against the caller's directory."""
    if os.path.isabs(p) or os.path.exists(os.path.join(ROOT, p)):
        return p
    here = os.path.join(CWD0, p)
    return os.path.abspath(here) if os.path.exists(here) else p


UFUNC_RE = re.compile(r"^[ \t]*UFUNCTION\(")
UPROP_RE = re.compile(r"^[ \t]*UPROPERTY\(")
CLASS_RE = re.compile(r"^[ \t]*(?:class|struct)[ \t]+([A-Za-z_]\w*)")
LINE_COMMENT_RE = re.compile(r"//.*$")


def strip_comments(text):
    """Remove // and /* */ comments. Naive about string literals containing them."""
    out = []
    i = 0
    n = len(text)
    in_block = False
    in_str = None
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_block:
            if c == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            i += 1
            continue
        if in_str:
            out.append(c)
            if c == "\\":
                if i + 1 < n:
                    out.append(nxt)
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        if c == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c in "\"'":
            in_str = c
            out.append(c)
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def balanced_end(text, start, open_ch, close_ch):
    """Index just past the matching close for the opener at `start`."""
    depth = 0
    i = start
    n = len(text)
    in_str = None
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in "\"'":
            in_str = c
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = (j + 2) if j != -1 else n
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def owner_class_at(lines, idx):
    """Nearest enclosing class/struct by scanning back for a decl at lower indent."""
    for k in range(idx, -1, -1):
        m = CLASS_RE.match(lines[k])
        if m:
            return m.group(1)
    return "<global>"


def leading_comment(lines, idx):
    """Comment lines immediately above `idx` (contiguous, allowing blank-free run)."""
    got = []
    k = idx - 1
    while k >= 0:
        s = lines[k].strip()
        if s.startswith("//") or s.startswith("/*") or s.startswith("*") or s.endswith("*/"):
            got.append(s)
            k -= 1
            continue
        break
    return " ".join(reversed(got))


SIG_RE = re.compile(
    r"^[ \t]*(?:(?P<pre>(?:private|protected|public|const|mixin)\s+)*)"
    r"(?P<ret>[A-Za-z_][\w:<>,\s\*&]*?)?[ \t]+"
    r"(?P<name>[A-Za-z_]\w*)[ \t]*\("
)


def parse_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    lines = src.split("\n")
    # char offset of each line start
    offsets = []
    off = 0
    for ln in lines:
        offsets.append(off)
        off += len(ln) + 1

    results = []
    for i, line in enumerate(lines):
        if not UFUNC_RE.match(line):
            continue
        # full UFUNCTION(...) text, possibly multiline
        start = offsets[i] + line.index("UFUNCTION(") + len("UFUNCTION")
        end = balanced_end(src, start, "(", ")")
        if end == -1:
            continue
        spec = src[start:end]
        spec_flat = " ".join(spec.split())
        # find signature line: next line after spec end that isn't blank/comment/UPROPERTY
        spec_end_line = src.count("\n", 0, end)
        j = spec_end_line
        # if spec ended mid-line and signature is on same line, handle
        rest_of_line = src[end: offsets[j] + len(lines[j])].strip()
        sig_line_idx = None
        if rest_of_line and not rest_of_line.startswith("//"):
            sig_line_idx = j
            sig_start = end
        else:
            j += 1
            while j < len(lines):
                s = lines[j].strip()
                if not s or s.startswith("//") or s.startswith("/*") or s.startswith("*"):
                    j += 1
                    continue
                break
            if j >= len(lines):
                continue
            sig_line_idx = j
            sig_start = offsets[j]
        # find the paren of the signature
        paren = src.find("(", sig_start)
        if paren == -1:
            continue
        paren_end = balanced_end(src, paren, "(", ")")
        if paren_end == -1:
            continue
        head = src[sig_start:paren].strip()
        params = src[paren:paren_end]
        m = re.search(r"([A-Za-z_]\w*)\s*$", head)
        fname = m.group(1) if m else "?"
        ret = head[: m.start()].strip() if m else head
        ret = re.sub(r"^(?:private|protected|public|mixin)\s+", "", ret).strip()

        # body
        k = paren_end
        while k < len(src) and src[k] in " \t\r\n":
            k += 1
        # skip trailing const/override etc
        tail = src[paren_end:k + 40]
        mm = re.match(r"[\s]*((?:const|override|final|property)[\s]*)*", src[paren_end:])
        k = paren_end + (mm.end() if mm else 0)
        while k < len(src) and src[k] in " \t\r\n":
            k += 1
        if k >= len(src):
            continue
        if src[k] == ";":
            body_raw = None
        elif src[k] == "{":
            b_end = balanced_end(src, k, "{", "}")
            if b_end == -1:
                continue
            body_raw = src[k + 1: b_end - 1]
        else:
            body_raw = None

        if body_raw is None:
            stmts = []
            body_disp = "<no body>"
            inner_comment = ""
        else:
            inner_comment = " ".join(
                s.strip() for s in body_raw.split("\n")
                if s.strip().startswith("//") or s.strip().startswith("/*")
            )
            clean = strip_comments(body_raw).strip()
            stmts = [s.strip() for s in re.split(r";", clean) if s.strip()]
            body_disp = " ".join(clean.split())

        results.append({
            "file": path.replace("\\", "/"),
            "line": i + 1,
            "sig_line": sig_line_idx + 1,
            "class": owner_class_at(lines, i),
            "name": fname,
            "ret": ret if ret else "void",
            "params": " ".join(params.split()),
            "spec": spec_flat,
            "comment": leading_comment(lines, i),
            "inner_comment": inner_comment,
            "body": body_disp,
            "nstmt": len(stmts),
            "stmts": stmts,
        })
    return results


TRIVIAL_RET = re.compile(
    r"^return\s*(?:true|false|0|0\.0f?|0\.f|-1|\"\"|n\"\"|nullptr|null|"
    r"[A-Za-z_]\w*::\w+|F?[A-Za-z_]\w*\(\s*\)|\{\s*\}|)$"
)
TRIVIAL_ASSIGN = re.compile(r"^[\w\.]+\s*=\s*(?:true|false|0|0\.0f?|-1|\"\"|n\"\"|nullptr|null)$")


def is_near_empty(rec):
    """Empty, or a single statement that produces no behaviour."""
    if rec["body"] == "<no body>":
        return True, "declaration-only"
    if rec["nstmt"] == 0:
        return True, "empty"
    if rec["nstmt"] == 1:
        s = rec["stmts"][0]
        if s == "return":
            return True, "bare return"
        if TRIVIAL_RET.match(s):
            return True, "constant return"
        if TRIVIAL_ASSIGN.match(s):
            return True, "constant assignment"
        # single return of a local default-constructed value
        if re.match(r"^return\s+[A-Za-z_]\w*$", s):
            return False, ""
    return False, ""


OUTPARAM_RE = re.compile(r"&\s*out\s+([A-Za-z_]\w*)")


def mark_real_one_liners(stubs):
    """A single `member = literal;` is a REAL body. A single `outparam = literal;`
    is a stub return."""
    for r in stubs:
        r["outparams"] = OUTPARAM_RE.findall(r["params"])
        r["isreal"] = False
        if r["why"] == "constant assignment":
            lhs = r["stmts"][0].split("=")[0].strip().split(".")[0]
            if lhs not in r["outparams"]:
                r["isreal"] = True


def add_callers(stubs, root):
    """Script call sites (excluding the declaration), and from the Blueprint export
    `references.json`: the member names call nodes name, and the function graphs and custom
    events Blueprints declare."""
    import _export
    refs = _export.load("references")
    srcs = {}
    for dp, _d, fs in os.walk(root):
        for f in fs:
            if f.endswith(".as"):
                p = os.path.join(dp, f).replace("\\", "/")
                srcs[p] = open(p, encoding="utf-8", errors="replace").read().split("\n")
    decl = {(r["file"], r["sig_line"]) for r in stubs}

    member = collections.Counter()
    massets = collections.defaultdict(set)
    for name, hit in (refs.get("memberNames") or {}).items():
        member[name] = int(hit.get("count", 0))
        massets[name] = set(hit.get("assets") or [])

    # Does any Blueprint DECLARE a body of this name (function graph or custom event)?
    # A member-name hit alone is only a CALL and proves nothing about an override.
    graphs = {k: set(v) for k, v in (refs.get("functionGraphs") or {}).items()}
    events = {k: set(v) for k, v in (refs.get("customEvents") or {}).items()}

    for r in stubs:
        r["bp_graph"] = sorted(graphs.get(r["name"], []))
        r["bp_event"] = sorted(events.get(r["name"], []))
        pat = re.compile(r"(?<![\w:.])" + re.escape(r["name"]) + r"\s*\(")
        hits = []
        for p, lines in srcs.items():
            for i, l in enumerate(lines):
                if (p, i + 1) in decl:
                    continue
                s = l.strip()
                if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
                    continue
                if pat.search(l):
                    hits.append("%s:%d" % (p, i + 1))
        r["script_callers"] = hits
        r["bp_members"] = member.get(r["name"], 0)
        r["bp_assets"] = sorted(massets.get(r["name"], []))


def add_docs(stubs, docroot=None):
    if docroot is None:
        docroot = CFG.get("docRoot", "Docs")
    prefixes = tuple(CFG.get("docClassPrefixes", []))
    idx = collections.defaultdict(list)
    names = {r["name"] for r in stubs}
    pats = {n: re.compile(r"(?<![\w])" + re.escape(n) + r"(?![\w])") for n in names}
    for dp, _d, fs in os.walk(docroot):
        for f in fs:
            if not f.endswith(".md"):
                continue
            p = os.path.join(dp, f).replace("\\", "/")
            try:
                lines = open(p, encoding="utf-8", errors="replace").read().split("\n")
            except OSError:
                continue
            for i, l in enumerate(lines):
                for n, pat in pats.items():
                    if n in l and pat.search(l):
                        idx[n].append("%s:%d" % (p, i + 1))
    for r in stubs:
        hits = idx.get(r["name"], [])
        r["doc_hits"] = hits[:8]
        r["doc_n"] = len(hits)
        # Only a page under one of the `docClassPrefixes` records a Blueprint BODY. A hit
        # anywhere else does not, and "faithful" is unprovable without one.
        cls = [h for h in hits if prefixes and h.startswith(prefixes)]
        r["doc_class"] = cls[:6]
        r["doc_class_n"] = len(cls)


def manual_table():
    """The hand-verified verdicts: (file basename, function name) -> (verdict, reason).

    `manual` in the project settings is a list of [file, function, verdict, reason]. A hand
    verdict wins over the keyword pass and is exempt from the demotion to UNVERIFIABLE."""
    return {(f, n): (v, why) for f, n, v, why in CFG.get("manual", [])}


# --------------------------------------------------------------------------- classification
# A stub is a debt and must be VISIBLE as one. The classes below are the audit's whole product.
# Order matters - first match wins. `ruled` in the project settings adds classes that match
# before the others: a list of [verdict, pattern].

# Says, in some form, "the Blueprint body was empty too" - i.e. the emptiness is the port.
FAITHFUL = (
    r"empty in the blueprint|empty in bp|blueprint(?:'s)? (?:body|graph)?\s*(?:is|was) (?:also )?empty|"
    r"no-?op in the blueprint|the blueprint does nothing|verified empty|"
    r"the empty body is the port|empty body is not a stub|is the port, not a stub|faithful port"
)
# Says, in some form, "this is not implemented and here is what it owes".
DECLARED = (
    r"\btodo\b|declaration only|declaration-only|not implemented|unimplemented|deliberate stub|"
    r"\bblocked\b|still a stub|still empty|nothing overrides it|nothing implements it|not ported|"
    r"fails? closed|fail-closed|is not live|not live yet|seam is dead|seam is unimplemented|owes"
)
# A comment that reads as FINISHED work when it is not.
FALSE_DONE = r"implemented in the blueprint|blueprint side implements|body is in the blueprint"
OVERRIDABLE = re.compile(r"\bBlueprintEvent\b|\bBlueprintOverride\b")


def classify(r):
    """-> (class, reason, stale). Needs enrich() for bp_graph/bp_event.

    `stale` is a CROSS-CUTTING flag, not a class: a comment can name its debt properly
    in the body and still open with a sentence that has since become false.
    """
    c = ((r.get("comment") or "") + " " + (r.get("inner_comment") or ""))
    low = " ".join(c.split()).lower()
    ovr = bool(OVERRIDABLE.search(r["spec"]))
    r["overridable"] = ovr

    # A completion claim is honest only if a Blueprint CAN and DOES override this.
    stale = ""
    if re.search(CFG.get("falseDonePattern", FALSE_DONE), low):
        if not ovr:
            stale = ("says 'implemented in the Blueprint', but the declaration is not "
                     "BlueprintEvent/BlueprintOverride, so no Blueprint can override it "
                     "(AngelscriptClassGenerator.cpp:3329-3331)")
        elif not (r.get("bp_graph") or r.get("bp_event")):
            stale = "says a Blueprint implements it; no graph or event of that name exists"

    key = (os.path.basename(r["file"]), r["name"])
    MANUAL = manual_table()
    if key in MANUAL:
        verdict, why = MANUAL[key]
        r["manual"] = True          # hand-verified with a cited page; exempt from demotion
        return verdict, why, stale
    if not low.strip():
        return "SILENT DEBT", "no comment at all", stale
    for name, pat in CFG.get("ruled", []):
        if re.search(pat, low):
            return name, CFG.get("ruledReason", "ruled by project instruction"), stale
    # DECLARED beats a stale opening sentence: the debt IS named further down.
    if re.search(CFG.get("declaredPattern", DECLARED), low):
        return "DECLARED DEBT", "comment names what it owes", stale
    if re.search(CFG.get("faithfulPattern", FAITHFUL), low):
        return "FAITHFUL EMPTY", "comment records the Blueprint body as empty/degenerate", stale
    if stale:
        return "SILENT DEBT", "the only statement about this body is a false completion claim", stale
    return "SILENT DEBT", "comment describes a body but never says this one does not run", stale


def live_caller(r):
    return len(r["script_callers"]) > 0 or r["bp_members"] > 0


# A value-returning stub must fail CLOSED.
PERMISSIVE = re.compile(r"=\s*true\s*$|^return\s+true$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=SCRIPT_ROOT)
    ap.add_argument("--tsv")
    ap.add_argument("--json")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--enrich", action="store_true",
                    help="add caller counts and documentation hit counts (slower)")
    args = ap.parse_args()
    tsv_path = _caller_path(args.tsv) if args.tsv else None
    json_path = _caller_path(args.json) if args.json else None
    args.root = _project_path(args.root)
    os.chdir(ROOT)

    recs = []
    for dirpath, _dirs, files in os.walk(args.root):
        for f in sorted(files):
            if f.endswith(".as"):
                recs.extend(parse_file(os.path.join(dirpath, f)))

    stubs = []
    for r in recs:
        empty, why = is_near_empty(r)
        r["empty"] = empty
        r["why"] = why
        if empty:
            stubs.append(r)
    mark_real_one_liners(stubs)
    if args.enrich:
        add_callers(stubs, args.root)
        add_docs(stubs)

    if args.enrich:
        for r in stubs:
            r["verdict"], r["reason"], r["stale"] = classify(r)
            # "Faithful empty" and "silent debt" are IDENTICAL in the code. The only
            # discriminator is a documentation page that records the Blueprint body.
            if (r["verdict"] == "FAITHFUL EMPTY" and r["doc_class_n"] == 0
                    and not r.get("manual")):
                r["verdict"] = "UNVERIFIABLE"
                r["reason"] = CFG.get("unverifiablePrefix",
                                      "reads as faithful, but no documentation page records "
                                      "the body: ") + r["reason"]
            r["live"] = live_caller(r)
            r["permissive"] = bool(PERMISSIVE.search(r["body"].strip())) and not r["isreal"]

    out = recs if args.all else stubs
    nreal = sum(1 for r in stubs if r["isreal"])
    print("total anchored UFUNCTION declarations: %d" % len(recs))
    print("empty / near-empty bodies:            %d" % len(stubs))
    print("  of which REAL one-line bodies:      %d  (member write, not a stub)" % nreal)
    print("TRUE stubs:                           %d" % (len(stubs) - nreal))
    print("files with at least one true stub:    %d"
          % len({s["file"] for s in stubs if not s["isreal"]}))
    print()
    hdr = ["file", "line", "class", "name", "ret", "why", "spec", "comment", "body"]
    rows = ["\t".join(hdr)]
    for r in sorted(out, key=lambda x: (x["file"], x["line"])):
        rows.append("\t".join(str(r.get(h, "")).replace("\t", " ") for h in hdr))
    text = "\n".join(rows)
    if args.tsv:
        with open(tsv_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("wrote %s" % args.tsv)
    else:
        print(text)
    if args.json:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
