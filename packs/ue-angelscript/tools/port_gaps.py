"""Per-class port gap report - what a script parent still owes, with the false work filtered OUT.

WHY THIS IS NOT JUST A DIFF
---------------------------
A naive diff (documented symbols minus `void Name(` in the script file) overstates the work. It
cannot see four things:

  1. INTERFACE MEMBERS. AngelScript cannot implement a UInterface. A member of an interface named in
     `interfaceNames` can never be ported, so a gap that counts it never reaches zero.

  2. ALREADY IMPLEMENTED ON THE MATCHING SUBSYSTEM, possibly under another name. A port of such a
     function DUPLICATES it.

  3. MARSHALLERS. A function that matches `marshallerPattern` converts objects to structs and back.
     It stays a stub and is not work.

  4. LAYER SCOPE. When a subsystem claims the layer, its header says where the behaviour goes.

So this reports gaps in THREE buckets, and only the first is work.

USAGE
-----
    python port_gaps.py              # all pairs
    python port_gaps.py MyComponent  # substring filter on the class name

Reads only. The heuristics are conservative: anything it cannot classify lands in NEEDS REVIEW,
because a false "nothing to do" is the expensive direction.

PROJECT SETTINGS (`.octopus/ue-angelscript.json`, object `port_gaps`)
------------------------------------------------------------------
  pairs              [[script parent, docs class, matching subsystem or null]]
  docsDir            the per-class docs directory; pages are `<docsDir>/<Class>/{functions,events}*.md`
  interfaceNames     interface names whose members cannot be ported
  marshallerPattern  regex of marshaller names
  marshallerReason   the text printed beside a marshaller
"""
import glob
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("port_gaps", {})

# Every path in this tool is relative to the project root.
os.chdir(ROOT)

# (script parent, docs class, matching subsystem or None)
PAIRS = [tuple(p) for p in CFG.get("pairs", [])]
DOCS_DIR = CFG.get("docsDir", "Docs/Classes").rstrip("/")
INTERFACE_NAMES = list(CFG.get("interfaceNames", []))
MARSHALLER_REASON = CFG.get("marshallerReason", "marshaller - left as a stub, do not port")

HEAD = re.compile(r'^### `([^`]+)`', re.M)

MARSHALLER = re.compile(CFG.get(
    "marshallerPattern", r'^(Serialize\w*|Deserialize\w*|\w*FromStruct\w*|\w*ToStruct\w*)$'))


# Engine lifecycle entries are spelled differently on the two sides: the Blueprint graph is
# `ReceiveBeginPlay`, the AngelScript override is `BeginPlay`.
LIFECYCLE = {
    "ReceiveBeginPlay": "BeginPlay",
    "ReceiveTick": "Tick",
    "ReceiveEndPlay": "EndPlay",
    "ReceiveDestroyed": "Destroyed",
    "UserConstructionScript": "ConstructionScript",
}


def script_defines(src, name):
    """Is `name` implemented or declared in this script text?

    Three forms count:

      1. a function        `void Name(`  /  `<type> Name(`
      2. a PROPERTY        `FSomeSignature Name;` - an event dispatcher is a property, not a function
      3. a lifecycle override under its AngelScript name - see LIFECYCLE above

    Spaces are removed from the name first, because the script side drops spaces from Blueprint
    symbols (`Set Is Open` -> `SetIsOpen`).
    """
    flat = name.replace(" ", "")
    candidates = [flat]
    if flat in LIFECYCLE:
        candidates.append(LIFECYCLE[flat])

    for c in candidates:
        if ("void %s(" % c) in src or (" %s(" % c) in src:
            return True
        # property declaration: end of a declaration line, not a call or a member access
        if re.search(r'^\s*[A-Za-z_][\w:<>,\s]*\s+' + re.escape(c) + r'\s*(=[^;]*)?;\s*$',
                     src, re.M):
            return True
    return False


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else ""

    # A class that is not in PAIRS produces no gap output, and an empty run reads like a clean
    # result. So when the caller names a class, the tool says whether it covers it.
    if want:
        matched = [c for p, c, _s in PAIRS
                   if want.lower() in c.lower() or want.lower() in p.lower()]
        if not matched:
            print("NOT COVERED: no PAIRS entry matches %r." % want)
            print("  This tool reports NOTHING for a class it does not know, which is NOT the same")
            print("  as reporting no gaps. Compute the gap by hand from %s/<Class>/" % DOCS_DIR)
            print("  {functions,events}*.md and publish it, or add the pair below.")
            print("  Known pairs: %s" % ", ".join(sorted(c for _p, c, _s in PAIRS)))
            return 2

    for path, cls, subsystem in PAIRS:
        if want and want.lower() not in cls.lower() and want.lower() not in path.lower():
            continue
        if not os.path.exists(path):
            print("%-46s script file missing" % path)
            continue

        pages = (glob.glob("%s/%s/functions*.md" % (DOCS_DIR, cls))
                 + glob.glob("%s/%s/functions/*.md" % (DOCS_DIR, cls))
                 + glob.glob("%s/%s/events*.md" % (DOCS_DIR, cls)))
        if not pages:
            print("%-46s no docs pages" % os.path.basename(path))
            continue

        doc_text = ""
        names = []
        for p in pages:
            t = open(p, encoding="utf-8", errors="ignore").read()
            doc_text += t
            for m in HEAD.finditer(t):
                for part in m.group(1).split("·"):
                    n = re.sub(r'\s*\(.*\)$', '', part.strip().strip("`")).strip()
                    if n and not n.startswith("Event"):
                        names.append(n)

        src = open(path, encoding="utf-8", errors="ignore").read()
        sub_src = ""
        if subsystem and os.path.exists(subsystem):
            sub_src = open(subsystem, encoding="utf-8", errors="ignore").read()

        work, blocked, elsewhere = [], [], []
        for n in dict.fromkeys(names):
            if script_defines(src, n):
                continue

            # 1. interface member - it cannot be ported
            body = ""
            i = doc_text.find("### `%s`" % n)
            if i >= 0:
                body = doc_text[i:i + 1200]
            if any(iface in body for iface in INTERFACE_NAMES):
                blocked.append((n, "interface member - AngelScript cannot implement a UInterface"))
                continue

            # 2. marshaller - stays a stub
            if MARSHALLER.match(n.replace(" ", "")):
                blocked.append((n, MARSHALLER_REASON))
                continue

            # 3. already implemented on the matching subsystem, possibly renamed
            if sub_src and script_defines(sub_src, n):
                elsewhere.append((n, "already on %s" % os.path.basename(subsystem)))
                continue

            work.append(n)

        print("\n== %-38s %d documented" % (os.path.basename(path), len(dict.fromkeys(names))))
        print("   NEEDS REVIEW (%d):" % len(work))
        for n in work[:20]:
            print("      %s" % n)
        if blocked:
            print("   NOT WORK (%d):" % len(blocked))
            for n, why in blocked:
                print("      %-28s %s" % (n, why))
        if elsewhere:
            print("   ELSEWHERE (%d):" % len(elsewhere))
            for n, why in elsewhere:
                print("      %-28s %s" % (n, why))
        if subsystem:
            print("   NOTE: %s claims this layer - check its header before porting onto the object "
                  "class." % os.path.basename(subsystem))


if __name__ == "__main__":
    # main() returns 2 for NOT COVERED; the exit code carries it.
    sys.exit(main() or 0)
