# -*- coding: utf-8 -*-
"""Dependency graph of `Script/**/*.as`, as one self-contained interactive HTML file.

The tool reads the `Script/` tree of an AngelScript project and draws it by LAYER. Columns are
layers, edges are the references between files, and an edge that goes UP the stack has the
violation colour. The layer table comes from the project settings.

    python script_graph.py                       # -> the configured HTML file
    python script_graph.py --json                # also writes the JSON next to the HTML
    python script_graph.py --self-test           # replant known facts; report nothing if they fail
    python script_graph.py --write-headers       # DRY RUN of the per-file dependency block
    python script_graph.py --write-headers --apply   # write it into every Script/**/*.as
    python script_graph.py --write-headers --apply --ignore-claims   # held files too
    python script_graph.py --open

Every command also takes `--project <path>`. The unreal pack's `_project.py` finds the project.

PROJECT SETTINGS are the `script_graph` object of `<project>/.octopus/ue-angelscript.json`:

    layers        [[name, rank], ...]. A directory under `Script/` is a layer. Without this table,
                  every directory that holds a `.as` file is a layer at rank 0.
    rootLayer     the layer of the files directly in `Script/` (default "Globals")
    outputHtml    the default `--out`, relative to the project (default "Docs/script-graph.html")
    projectName   the name in the HTML title (default: the name of the project directory)
    docLink       the link from a node to its prose page, with `{page}` and `{anchor}`
                  (default "Layers/{page}.md#{anchor}")
    docRootPage   the `{page}` of a file in the root layer (default "Root")
    claimsFile    the claim board that `--apply` reads (default ".claude/coordination/CLAIMS.md")
    messagesFile  the message board that the output names (default ".claude/coordination/MESSAGES.md")
    headerNote    the second line of every generated header block
    selfTest      the fixtures of `--self-test`. See the SELF-TEST section.

NODE IDENTITY is the file path relative to `Script/`, POSIX separators, extension kept:
`Gameplay/Components/MyComponent.as`. That is the id in the JSON, the `data-id` in the DOM, and the
URL fragment - `script-graph.html#Gameplay/Components/MyComponent.as` selects that node on load. A
prose page can link a node with that fragment. Each node also carries `doc`, the link to its prose
page, made from `docLink` with no lookup table: `Script/<Layer>/<Sub>/<File>.as` gives
`{page}` = `<Layer>-<Sub>` and `{anchor}` = `<file>` in lower case.

FIVE EDGE KINDS, because they mean different things:

    inherit   `class A : B`
    member    a declared field whose type is another script class, struct or enum
    call      a call into another file, including `USubsystem::Get()` and namespace-qualified calls
    event     a global-scope `event` declaration, the `UPROPERTY()` holding it, its `Broadcast`
              sites and its `AddUFunction` subscribers
    type      a body naming another file's type - `Cast<X>`, a local of type X, an `EFoo::Bar` value

WHAT A ZERO MEANS. A function with no caller in `Script/` is labelled exactly that, never "unused".
A Blueprint can call any reflected function, a component is found at runtime by `GetComponentByClass`,
and a `K2Node_AddDelegate` bind produces no call node at all. This reads only `Script/`, so the
strong signal is the pair "no caller in `Script/`" AND "not reflected": nothing outside the script
layer can reach that one.

CYCLES ARE COMPUTED OVER UNAMBIGUOUS EDGES ONLY. An ambiguous call edge goes to every file that
declares the name, so it can make a cycle that does not exist. Components found only with those
edges are reported separately as `basis: ambiguous` and counted nowhere else. Each component names
its CUT LINKS - the single file pairs whose removal breaks it apart - and the edge kinds that close
it. An `inherit` closes a cycle that no wrapper can open, and a `type` is usually one Cast from gone.

`--write-headers` puts the dependency list into each file as a delimited, regenerable `//` block at
the top: every project file it references. Dry run by default. See the HEADER BLOCKS section.

`--apply` IS CLAIM-AWARE. It writes the whole tree, so it reads the claim board first, skips every
file another agent holds, names each skipped file at the top of the output and exits 3. Holds are
resolved by block POSITION - the board is append-only and its timestamps can be skewed. Any block
shape the parser cannot read resolves to HELD. `--ignore-claims` writes them anyway and prints every
hold it overrides. See the CLAIM BOARD section.

`--as-agent <id>` drops the holds YOU own and nothing else. An agent claims `Script/` before a
tree-wide write, and without this flag that claim makes the write skip every file as held by the
same agent. Every other agent's hold still skips its files and the run still exits 3. It is not
`--ignore-claims`.

REACHABILITY COUNTS `n"Name"` LITERALS. `System::SetTimer(this, n"Foo", ...)` and
`Delegate.AddUFunction(this, n"Foo")` resolve by reflected name and produce no call expression.
Without them, live handlers would read as callerless.

Offline. Reads files only, never calls the editor.
"""
from __future__ import unicode_literals

import argparse
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal" / "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

ROOT = str(_project.project_root())
CFG = _project.config("ue-angelscript").get("script_graph", {})

# `strip_comments` blanks `//` and block comments and keeps every byte offset. `_SPEC` is the
# specifier pattern that reads the nested-paren form (`meta=(DisplayName="...")`). A `[^)]*`
# specifier pattern stops at the inner `)` and drops the declaration.
from collision_census import strip_comments, _SPEC        # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _project_path(posix_rel):
    """A project-relative POSIX path from the settings, as an absolute path."""
    return os.path.join(ROOT, *[p for p in posix_rel.split("/") if p])


SCRIPT_ROOT = os.path.join(ROOT, "Script")
OUT_HTML = _project_path(CFG.get("outputHtml", "Docs/script-graph.html"))
PROJECT_NAME = CFG.get("projectName") or os.path.basename(ROOT.rstrip("\\/"))
DOC_LINK = CFG.get("docLink", "Layers/{page}.md#{anchor}")
DOC_ROOT_PAGE = CFG.get("docRootPage", "Root")
ROOT_LAYER = CFG.get("rootLayer", "Globals")

# ------------------------------------------------------------------------------------------------
# Layers.
#
# The `layers` setting gives each directory under `Script/` a rank: its dependency depth, with the
# lowest rank at the bottom of the stack. Two layers can share a rank. An edge is UPWARD, and has
# the violation colour, when rank(target) > rank(source).
#
# Without a `layers` setting, every directory that holds a `.as` file is a layer at rank 0, the
# root layer first. Then no edge is upward.
# ------------------------------------------------------------------------------------------------
def _layers_from_tree(script_root):
    """Every directory under `script_root` that holds a `.as` file, at rank 0, root layer first."""
    names = set()
    for dirpath, _dirs, fns in os.walk(script_root):
        if any(fn.endswith(".as") for fn in fns):
            r = os.path.relpath(dirpath, script_root).replace("\\", "/")
            names.add(ROOT_LAYER if r == "." else r)
    return [(n, 0) for n in sorted(names, key=lambda n: (n != ROOT_LAYER, n))]


if CFG.get("layers"):
    LAYERS = [(str(name), int(rank)) for name, rank in CFG["layers"]]
else:
    LAYERS = _layers_from_tree(SCRIPT_ROOT)
LAYER_RANK = dict(LAYERS)
LAYER_COL = dict((name, i) for i, (name, _r) in enumerate(LAYERS))

# Words that take a `(` but are not calls.
NOT_CALLS = set("""
if else for while switch case return do catch try sizeof new delete this
UFUNCTION UPROPERTY UCLASS USTRUCT UENUM UMETA meta DefaultComponent Cast
""".split())

# Function names the engine invokes itself. `BlueprintOverride` already says so; the list also
# catches an override that does not carry the specifier.
ENGINE_ENTRY = set("""
BeginPlay EndPlay Tick ConstructionScript Initialize Deinitialize PostInitializeComponents
GetLifetimeReplicatedProps ShouldCreateSubsystem ReceiveTick
""".split())

REFLECTED_ENTRY_SPECS = ("BlueprintOverride", "BlueprintEvent", "BlueprintImplementableEvent",
                         "BlueprintNativeEvent")

DELEGATE_SUBSCRIBE = ("AddUFunction", "BindUFunction", "AddDynamic", "Bind")


def wiring_state(broadcasts, subs):
    """One half, both halves, or neither. `inert` is its own answer: a property nothing broadcasts
    AND nothing subscribes is a declaration, and reporting it as `no-broadcast` hides that."""
    if not broadcasts and not subs:
        return "inert"
    if not broadcasts:
        return "no-broadcast"
    if not subs:
        return "no-subscriber"
    return "ok"

# ------------------------------------------------------------------------------------------------
# Regexes. Every structural one runs against MASKED text - comments blanked, string CONTENTS blanked
# - so a brace or a keyword inside a literal cannot move the parse.
# ------------------------------------------------------------------------------------------------
DECL_RE = re.compile(r'\b(class|struct|namespace|enum)\s+(?:class\s+)?([A-Za-z_]\w*)'
                     r'\s*(?::\s*([^{\n]+?))?\s*\{')
EVENT_RE = re.compile(r'\bevent\s+[A-Za-z_][\w:<>, ]*?\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*;')
# The optional access specifier is what an encapsulated member carries: `private int32 myBody;`.
# Without it the whole declaration is invisible and the header block states a short dependency list.
MEMBER_RE = re.compile(
    r'^[ \t]*(?:(?:private|protected|public)\s+)?'
    r'((?:const\s+)?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?\s*(?:<[^;{}]*>)?)\s*&?\s+'
    r'([A-Za-z_]\w*)\s*(?:=[^;{}]*)?;', re.M)
CALLSITE_RE = re.compile(r'(?:([A-Za-z_]\w*)\s*(::|\.)\s*)?([A-Za-z_]\w*)\s*\(')
NAMEHEAD_RE = re.compile(r'([A-Za-z_]\w*)\s*\(')
FNAME_LITERAL_RE = re.compile(r'\bn"([A-Za-z_]\w*)"')
UPROPERTY_COUNT_RE = re.compile(r'\bUPROPERTY\s*\(')
UFUNCTION_COUNT_RE = re.compile(r'\bUFUNCTION\s*\(')
UPROPERTY_TAIL_RE = re.compile(r'UPROPERTY\(' + _SPEC + r'\)\s*$')
UFUNCTION_TAIL_RE = re.compile(r'UFUNCTION\(' + _SPEC + r'\)\s*$')
DOC_MARKER_RE = re.compile(r'//\s*DOC:\s*(\S+)')
TODO_MARKER_RE = re.compile(r'\bTODO\(([^)\s]+)\)')
MIGRATED_MARKER_RE = re.compile(r'//\s*Migrated from\s+(\S+)\s+Blueprint')
MEMBER_KEYWORDS = set("return delete else case default break continue throw using typedef".split())


def mask_strings(code):
    """Blank the CONTENTS of string literals, keeping length and the quote characters.

    `strip_comments` deliberately leaves strings intact - a comment scanner must, or it walks into a
    `"//"`. Structure scanning wants the opposite: `FString s = "a { b";` must not open a scope.
    """
    out = []
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if c == '"' or c == "'":
            quote = c
            out.append(c)
            i += 1
            while i < n:
                if code[i] == "\\" and i + 1 < n:
                    out.append("  ")
                    i += 2
                    continue
                if code[i] == quote:
                    out.append(quote)
                    i += 1
                    break
                out.append("\n" if code[i] == "\n" else " ")
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def brace_depths(masked):
    """depth[i] = brace nesting depth BEFORE character i. Length is len(masked) + 1."""
    depth = [0] * (len(masked) + 1)
    d = 0
    for i, c in enumerate(masked):
        depth[i] = d
        if c == "{":
            d += 1
        elif c == "}":
            d -= 1
    depth[len(masked)] = d
    return depth


def match_close(masked, i, op="(", cl=")"):
    """Index of the closer matching the opener at `i`, or -1."""
    d = 0
    n = len(masked)
    while i < n:
        c = masked[i]
        if c == op:
            d += 1
        elif c == cl:
            d -= 1
            if d == 0:
                return i
        i += 1
    return -1


def line_start(text, pos):
    j = text.rfind("\n", 0, pos)
    return 0 if j < 0 else j + 1


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def preceding_macro(masked, ls, tail_re):
    """The specifier string of a UPROPERTY/UFUNCTION sitting immediately above line-start `ls`.

    Comments are already blank, so "immediately above" tolerates comment and blank lines. Anchoring
    the pattern at the end of the window is what makes the nested-paren specifier form safe here.
    """
    window = masked[max(0, ls - 1500):ls].rstrip()
    if not window.endswith(")"):
        return None
    m = tail_re.search(window)
    return m.group(1) if m else None


class Sym(object):
    __slots__ = ("name", "kind", "owner", "line", "spec", "detail")

    def __init__(self, name, kind, owner, line, spec=None, detail=None):
        self.name = name
        self.kind = kind
        self.owner = owner
        self.line = line
        self.spec = spec
        self.detail = detail


class ScriptFile(object):
    def __init__(self, relpath, path, layer, text, code, masked):
        self.id = relpath
        self.path = path
        self.layer = layer
        self.rank = LAYER_RANK[layer]
        self.text = text
        self.code = code
        self.masked = masked
        self.lines = text.count("\n") + 1
        self.decls = []          # Sym: class / struct / enum / namespace, detail = base
        self.events = []         # Sym: global-scope `event` declaration, detail = params
        self.members = []        # Sym: field, detail = declared type text
        self.funcs = []          # Sym: function definition, detail = return type
        self.func_spans = []     # (name, owner, body_open, body_close)
        self.docs = []
        self.todos = []
        self.migrated = 0
        self.missed_props = 0
        self.missed_funcs = 0


def parse_file(relpath, path, layer):
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    code = strip_comments(text)
    masked = mask_strings(code)
    sf = ScriptFile(relpath, path, layer, text, code, masked)
    depth = brace_depths(masked)

    sf.docs = sorted(set(DOC_MARKER_RE.findall(text)))
    sf.todos = sorted(set(TODO_MARKER_RE.findall(text)))
    sf.migrated = len(MIGRATED_MARKER_RE.findall(text))

    # ---- top-level declarations and their body spans ---------------------------------------------
    bodies = []
    for m in DECL_RE.finditer(masked):
        if depth[m.start()] != 0:
            continue
        kind, name, base = m.group(1), m.group(2), (m.group(3) or "").strip()
        base = base.split(",")[0].strip() or None
        if kind == "enum" and base and not base[0].isupper():
            base = None                                    # `enum E : uint8`
        open_brace = m.end() - 1
        close = match_close(masked, open_brace, "{", "}")
        if close < 0:
            close = len(masked) - 1
        sf.decls.append(Sym(name, kind, None, line_of(text, m.start()), None, base))
        bodies.append((name, kind, open_brace + 1, close))

    for m in EVENT_RE.finditer(masked):
        if depth[m.start()] != 0:
            continue
        sf.events.append(Sym(m.group(1), "event", None, line_of(text, m.start()),
                             None, " ".join(m.group(2).split())))

    # ---- members and functions -------------------------------------------------------------------
    def scan_scope(owner, start, end, want_depth):
        for mm in MEMBER_RE.finditer(masked, start, end):
            if depth[mm.start(1)] != want_depth:
                continue
            typ = " ".join(mm.group(1).split())
            if typ.split()[0] in MEMBER_KEYWORDS:
                continue
            ls = line_start(masked, mm.start())
            spec = preceding_macro(masked, ls, UPROPERTY_TAIL_RE)
            sf.members.append(Sym(mm.group(2), "member", owner, line_of(text, mm.start()),
                                  spec, typ))
        for mm in NAMEHEAD_RE.finditer(masked, start, end):
            if depth[mm.start(1)] != want_depth:
                continue
            nm = mm.group(1)
            if nm in NOT_CALLS:
                continue
            op = mm.end() - 1
            cl = match_close(masked, op)
            if cl < 0:
                continue
            k = cl + 1
            while k < end and masked[k] in " \t\r\n":
                k += 1
            if masked.startswith("const", k):
                k += 5
                while k < end and masked[k] in " \t\r\n":
                    k += 1
            if k >= end or masked[k] != "{":
                continue                                   # a call or a declaration, not a body
            ls = line_start(masked, mm.start())
            head = masked[ls:mm.start(1)].strip()
            if not head or head.endswith((",", "(", "&&", "||", "=", ".", "!")):
                continue                                   # no return type -> not a definition
            body_close = match_close(masked, k, "{", "}")
            if body_close < 0:
                body_close = end
            spec = preceding_macro(masked, ls, UFUNCTION_TAIL_RE)
            sf.funcs.append(Sym(nm, "func", owner, line_of(text, mm.start()), spec, head))
            sf.func_spans.append((nm, owner, k, body_close))

    covered = []
    for name, kind, bstart, bend in bodies:
        if kind == "enum":
            continue
        scan_scope(name, bstart, bend, 1)
        covered.append((bstart, bend))
    cursor = 0                                             # global scope = everything not in a body
    for bstart, bend in sorted(covered):
        if bstart > cursor:
            scan_scope(None, cursor, bstart, 0)
        cursor = max(cursor, bend + 1)
    if cursor < len(masked):
        scan_scope(None, cursor, len(masked), 0)

    # ---- the shortfall check: a regex that quietly matches fewer declarations than exist ----------
    sf.missed_props = len(UPROPERTY_COUNT_RE.findall(masked)) - \
        sum(1 for s in sf.members if s.spec is not None)
    sf.missed_funcs = len(UFUNCTION_COUNT_RE.findall(masked)) - \
        sum(1 for s in sf.funcs if s.spec is not None)
    return sf


def collect(script_root):
    files, unreadable, unknown_layers = [], [], []
    for dirpath, dirs, names in os.walk(script_root):
        dirs.sort()
        for fn in sorted(names):
            if not fn.endswith(".as"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, script_root).replace("\\", "/")
            layer = os.path.dirname(rel) or ROOT_LAYER
            if layer not in LAYER_RANK:
                unknown_layers.append(rel)
                continue
            try:
                files.append(parse_file(rel, path, layer))
            except Exception as exc:                       # noqa: BLE001 - reported, never silent
                unreadable.append("%s: %s: %s" % (rel, type(exc).__name__, exc))
    files.sort(key=lambda f: f.id)
    return files, unreadable, unknown_layers


# =================================================================================================
# GRAPH
# =================================================================================================
class Graph(object):
    def __init__(self):
        self.files = []
        self.index = {}            # file id -> position
        self.type_owner = {}       # class/struct/enum/namespace name -> file id (first declarer)
        self.type_dupes = {}       # name -> [file ids] when more than one file declares it
        self.decl_kind = {}        # name -> class|struct|enum|namespace
        self.base_of = {}          # class name -> base name
        self.func_owners = {}      # function name -> set of file ids declaring it
        self.func_class = {}       # (file id, function name) -> set of declaring class names
        self.event_decl = {}       # event type name -> file id
        self.event_holders = {}    # event type name -> [(file id, class, property)]
        self.prop_event = {}       # (file id, class, property) -> event type name
        self.prop_by_name = {}     # property name -> [(file id, class, property)], every declarer
        self.props = {}            # (file id, class, property) -> record
        self.ambiguous_events = {} # property name -> count of occurrences resolvable to >1 event
        self.edges = {}            # (src, dst, kind) -> {"n": int, "why": [...], "amb": bool}
        self.called = {}           # function name -> call count seen anywhere in Script/
        self.reflected_refs = {}   # n"Name" literal -> count
        self.ambiguous_calls = {}  # function name -> count of unresolvable-to-one-file call sites
        self.external_quals = {}   # `Foo::` qualifier not declared in Script/ -> count
        self.events = {}           # event type -> record

    # -- building ---------------------------------------------------------------------------------
    def add_edge(self, src, dst, kind, why, amb=False, sym=None):
        """`sym` is the PROJECT SYMBOL that made this reference - a class, struct, enum or event
        name declared in the target file. `--write-headers` prints it beside the target path,
        because "both file+class" is what the header block is for."""
        if src == dst or src is None or dst is None:
            return
        key = (src, dst, kind)
        rec = self.edges.get(key)
        if rec is None:
            rec = self.edges[key] = {"n": 0, "why": [], "amb": amb, "syms": set()}
        rec["n"] += 1
        rec["amb"] = rec["amb"] and amb
        if sym:
            rec["syms"].add(sym)
        if why and len(rec["why"]) < 8 and why not in rec["why"]:
            rec["why"].append(why)

    def add_syms(self, src, dst, kind, names):
        """Attach symbol names to an edge that already exists, without counting it again."""
        rec = self.edges.get((src, dst, kind))
        if rec is not None and names:
            rec["syms"].update(n for n in names if n)


def build(files):
    g = Graph()
    g.files = files
    for i, f in enumerate(files):
        g.index[f.id] = i

    # ---- symbol tables ---------------------------------------------------------------------------
    for f in files:
        for d in f.decls:
            g.decl_kind.setdefault(d.name, d.kind)
            if d.name in g.type_owner:
                g.type_dupes.setdefault(d.name, [g.type_owner[d.name]]).append(f.id)
            else:
                g.type_owner[d.name] = f.id
            if d.kind == "class" and d.detail:
                g.base_of.setdefault(d.name, d.detail)
        for ev in f.events:
            g.event_decl.setdefault(ev.name, f.id)
        for fn in f.funcs:
            g.func_owners.setdefault(fn.name, set()).add(f.id)
            if fn.owner:
                g.func_class.setdefault((f.id, fn.name), set()).add(fn.owner)

    for f in files:
        for m in f.members:
            base_type = m.detail.replace("const ", "")
            for tok in re.findall(r'[A-Za-z_]\w*', base_type):
                if tok in g.event_decl:
                    key = (f.id, m.owner, m.name)
                    g.prop_event[key] = tok
                    g.prop_by_name.setdefault(m.name, []).append(key)
                    g.event_holders.setdefault(tok, []).append(key)

    type_names = sorted(set(g.type_owner) | set(g.event_decl))
    TYPE_RE = re.compile(r'\b(' + "|".join(re.escape(t) for t in type_names) + r')\b')

    # ---- inheritance -----------------------------------------------------------------------------
    for f in files:
        for d in f.decls:
            if d.detail and d.detail in g.type_owner:
                g.add_edge(f.id, g.type_owner[d.detail], "inherit",
                           "%s : %s" % (d.name, d.detail), sym=d.detail)

    # ---- member types ----------------------------------------------------------------------------
    for f in files:
        for m in f.members:
            for tok in sorted(set(re.findall(r'[A-Za-z_]\w*', m.detail))):
                owner = g.type_owner.get(tok) or g.event_decl.get(tok)
                if owner:
                    kind = "event" if tok in g.event_decl else "member"
                    g.add_edge(f.id, owner, kind, "%s %s" % (m.detail, m.name), sym=tok)

    # ---- calls, broadcasts, subscriptions, reflected-name literals -------------------------------
    for f in files:
        for name in FNAME_LITERAL_RE.findall(f.code):
            g.reflected_refs[name] = g.reflected_refs.get(name, 0) + 1
        own_types = set(d.name for d in f.decls)
        for fname, owner, bstart, bend in f.func_spans:
            body = f.masked[bstart:bend]
            for mm in CALLSITE_RE.finditer(body):
                obj, sep, fn = mm.group(1), mm.group(2), mm.group(3)
                if fn in NOT_CALLS:
                    continue
                if fn == "Broadcast" and obj:
                    continue                               # handled in the event pass below
                if fn in DELEGATE_SUBSCRIBE and obj and obj in g.prop_event:
                    continue                               # ditto
                if sep == "::":
                    if obj == "Super":
                        base = g.base_of.get(owner)
                        tgt = g.type_owner.get(base) if base else None
                        if tgt:
                            g.add_edge(f.id, tgt, "call", "Super::%s" % fn, sym=base)
                        continue
                    tgt = g.type_owner.get(obj)
                    if tgt:
                        g.add_edge(f.id, tgt, "call", "%s::%s()" % (obj, fn), sym=obj)
                        g.called[fn] = g.called.get(fn, 0) + 1
                    else:
                        g.external_quals[obj] = g.external_quals.get(obj, 0) + 1
                    continue
                owners = g.func_owners.get(fn)
                if not owners:
                    continue                               # engine method or a local lambda-ish name
                g.called[fn] = g.called.get(fn, 0) + 1
                outside = sorted(o for o in owners if o != f.id)
                if not outside:
                    continue                               # resolves inside this file
                if len(outside) == 1:
                    g.add_edge(f.id, outside[0], "call", "%s()" % fn)
                    g.add_syms(f.id, outside[0], "call", g.func_class.get((outside[0], fn)))
                else:
                    g.ambiguous_calls[fn] = g.ambiguous_calls.get(fn, 0) + 1
                    for o in outside:
                        g.add_edge(f.id, o, "call", "%s() (ambiguous: %d declarers)"
                                   % (fn, len(owners)), amb=True)
                        g.add_syms(f.id, o, "call", g.func_class.get((o, fn)))

        # type references inside bodies: casts, locals, enum values
        for fname, owner, bstart, bend in f.func_spans:
            for mm in TYPE_RE.finditer(f.masked, bstart, bend):
                tok = mm.group(1)
                if tok in own_types:
                    continue
                tgt = g.type_owner.get(tok) or g.event_decl.get(tok)
                if tgt and tgt != f.id:
                    g.add_edge(f.id, tgt, "type", tok, sym=tok)

    # ---- events ----------------------------------------------------------------------------------
    for ev, decl_file in sorted(g.event_decl.items()):
        g.events[ev] = {"name": ev, "decl": decl_file, "holders": [], "broadcasts": [],
                        "subs": [], "params": ""}
    for f in files:
        for e in f.events:
            g.events[e.name]["params"] = e.detail
    for ev, holders in g.event_holders.items():
        g.events[ev]["holders"] = sorted(holders)
        for fid, _cls, _prop in holders:
            g.add_edge(fid, g.event_decl[ev], "event", "holds %s" % ev, sym=ev)
    for key, ev in g.prop_event.items():
        g.props[key] = {"file": key[0], "cls": key[1] or "", "prop": key[2], "event": ev,
                        "broadcasts": [], "subs": [], "ambiguous": 0}

    # Every name a file declares with a type, members and function locals together, so
    # `MyButton.Clicked` resolves through `UMyButton MyButton;`.
    decl_types = {}
    for f in files:
        names = {}
        for mm in MEMBER_RE.finditer(f.masked):
            typ = " ".join(mm.group(1).split())
            head = typ.split()[0] if typ.split() else ""
            if head in MEMBER_KEYWORDS:
                continue
            clean = re.sub(r'\s*<.*', "", typ.replace("const ", "")).strip()
            names.setdefault(mm.group(2), []).append((mm.start(), clean))
        decl_types[f.id] = names

    def type_at(fid, name, pos):
        """The type of `name` as declared nearest above `pos`. A file can declare one name twice
        with two types, so the last declaration in the file can be the wrong answer."""
        decls = decl_types.get(fid, {}).get(name)
        if not decls:
            return None
        before = [t for start, t in decls if start < pos]
        return before[-1] if before else decls[0][1]

    def class_at(f, pos):
        """The class whose body holds `pos`."""
        for _fname, owner, bstart, bend in f.func_spans:
            if bstart <= pos <= bend:
                return owner
        return None

    def holders_of_class(cands, cls_name):
        """The candidates declared on `cls_name` or on a class it derives from."""
        seen, walk = [], cls_name
        for _ in range(8):
            if not walk:
                break
            seen.append(walk)
            walk = g.base_of.get(walk)
        for name in seen:
            hit = [k for k in cands if k[1] == name]
            if hit:
                return hit
        return []

    # RESOLUTION. A property name is not unique: several classes can each declare a property of
    # one name. An occurrence resolves to the holders
    # declared in the same file first, and to every declarer of the name when the file declares
    # none. An occurrence that reaches more than one event type is counted for each and recorded in
    # `ambiguous_events`, so a shared name can never hide one event's missing half behind another
    # event's wiring.
    BCAST_RE = re.compile(r'([A-Za-z_]\w*)\s*\.\s*(Broadcast|' +
                          "|".join(DELEGATE_SUBSCRIBE) + r')\s*\(')
    for f in files:
        for mm in BCAST_RE.finditer(f.masked):
            prop, verb = mm.group(1), mm.group(2)
            cands = g.prop_by_name.get(prop)
            if not cands:
                continue
            back = re.search(r'([A-Za-z_]\w*)\s*\.\s*$',
                             f.masked[max(0, mm.start() - 96):mm.start()])
            if back:
                chosen = holders_of_class(cands, type_at(f.id, back.group(1), mm.start()))
            else:
                chosen = holders_of_class(cands, class_at(f, mm.start()))
            if not chosen:
                chosen = [k for k in cands if k[0] == f.id] or cands
            evs = sorted(set(g.prop_event[k] for k in chosen))
            amb = len(evs) > 1
            if amb:
                g.ambiguous_events[prop] = g.ambiguous_events.get(prop, 0) + 1
            line = line_of(f.text, mm.start())
            handler = None
            if verb != "Broadcast":
                hm = re.search(r'n"([A-Za-z_]\w*)"', f.code[mm.end():mm.end() + 160])
                handler = hm.group(1) if hm else "?"
                if hm:
                    g.reflected_refs[handler] = g.reflected_refs.get(handler, 0) + 1
            for k in chosen:
                rec = g.props[k]
                if amb:
                    rec["ambiguous"] += 1
                if verb == "Broadcast":
                    rec["broadcasts"].append([f.id, line])
                else:
                    rec["subs"].append([f.id, line, handler])
            for ev in evs:
                if verb == "Broadcast":
                    if [f.id, line] not in g.events[ev]["broadcasts"]:
                        g.events[ev]["broadcasts"].append([f.id, line])
                elif [f.id, line, handler] not in g.events[ev]["subs"]:
                    g.events[ev]["subs"].append([f.id, line, handler])
            for hf in sorted(set(k[0] for k in chosen)):
                g.add_edge(f.id, hf, "event", "%s.%s" % (prop, verb), amb=amb, sym=evs[0])
    for rec in g.props.values():
        rec["broadcasts"].sort()
        rec["subs"].sort()
        rec["state"] = wiring_state(rec["broadcasts"], rec["subs"])
    for ev in g.events.values():
        ev["broadcasts"].sort()
        ev["subs"].sort()
        if not ev["holders"]:
            ev["state"] = "no-holder"
        else:
            ev["state"] = wiring_state(ev["broadcasts"], ev["subs"])
    return g


def reachability(g):
    """Per function: is anything in `Script/` able to reach it, and if not, can anything outside?"""
    out = {}
    for f in g.files:
        for fn in f.funcs:
            spec = fn.spec
            reflected = spec is not None
            why = None
            if g.called.get(fn.name):
                why = "called in Script/ (%d site(s))" % g.called[fn.name]
            elif g.reflected_refs.get(fn.name):
                why = 'reached by n"%s" reflected-name literal (%d site(s))' % (
                    fn.name, g.reflected_refs[fn.name])
            elif spec and any(s in spec for s in REFLECTED_ENTRY_SPECS):
                why = "engine/Blueprint entry point (%s)" % spec.strip()
            elif fn.name in ENGINE_ENTRY:
                why = "engine entry point by name"
            out[(f.id, fn.owner or "<global>", fn.name, fn.line)] = {
                "reachable": why is not None,
                "why": why,
                "reflected": reflected,
                "spec": spec or "",
            }
    return out


# =================================================================================================
# CYCLES
#
# Two shapes, and they cost different amounts to live with:
#
#   PAIR  two files that reference each other. Often deliberate - a parent and the component it
#         owns - and cheap to see.
#   SCC   three or more files no cut can separate. This is what "intertwined" means: none of them
#         can be lifted out, read, ported or tested without the others.
#
# AMBIGUITY IS KEPT SEPARATE. A call to a name declared in more than one file is drawn to every
# candidate rather than dropped, so an ambiguous edge can MANUFACTURE a cycle that does not exist.
# Components are therefore computed twice: once over unambiguous edges only (`basis: certain`) and
# once over every edge. A component that appears only in the second pass is reported as
# `basis: ambiguous` and never mixed in with the first set.
#
# CUT LINKS are the answer to "which edge do I remove". A link is one ordered file pair, all edge
# kinds together, because removing a reference means removing every kind of it. A link is a cut
# when deleting it leaves the component no longer strongly connected. A component with no cut link
# needs at least two removals, which is the honest signal that it is welded rather than merely
# looped.
# =================================================================================================
def _tarjan(nodes, adj):
    """Strongly connected components, iterative so a deep chain cannot blow the stack.

    Returns a list of components, each a sorted list of node ids. Node order in the input decides
    the output order, so the result is deterministic.
    """
    index = {}
    low = {}
    on_stack = set()
    stack = []
    comps = []
    counter = [0]

    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(adj.get(root, ()))))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(adj.get(nxt, ())))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                comps.append(sorted(comp))
    return comps


def _strongly_connected(members, adj):
    """Is the subgraph induced on `members` a single strongly connected component?"""
    keep = set(members)
    sub = dict((m, set(adj.get(m, ())) & keep) for m in members)
    comps = _tarjan(sorted(members), sub)
    return len(comps) == 1 and len(comps[0]) == len(members)


def _links_within(g, members, allow_ambiguous):
    """Ordered file pairs inside a component, all edge kinds aggregated onto one link."""
    keep = set(members)
    links = {}
    for (s, t, kind), rec in sorted(g.edges.items()):
        if s not in keep or t not in keep:
            continue
        if rec["amb"] and not allow_ambiguous:
            continue
        rl = links.setdefault((s, t), {"kinds": [], "n": 0, "syms": set(), "amb": True})
        if kind not in rl["kinds"]:
            rl["kinds"].append(kind)
        rl["n"] += rec["n"]
        rl["syms"].update(rec["syms"])
        rl["amb"] = rl["amb"] and rec["amb"]
    return links


def find_cycles(g):
    """Every strongly connected component of size >= 2, certain ones first."""
    ids = [f.id for f in g.files]
    unamb, full = {}, {}
    for (s, t, _k), rec in g.edges.items():
        full.setdefault(s, set()).add(t)
        if not rec["amb"]:
            unamb.setdefault(s, set()).add(t)

    certain = [c for c in _tarjan(ids, unamb) if len(c) >= 2]
    certain_sets = [set(c) for c in certain]
    extra = [c for c in _tarjan(ids, full)
             if len(c) >= 2 and set(c) not in certain_sets]

    def describe(members, adj, basis):
        allow = (basis == "ambiguous")
        links = _links_within(g, members, allow)
        cut = []
        for pair in sorted(links):
            trimmed = dict((m, set(adj.get(m, ())) & set(members)) for m in members)
            trimmed[pair[0]] = set(x for x in trimmed[pair[0]] if x != pair[1])
            if not _strongly_connected(members, trimmed):
                cut.append(pair)
        mutual = sorted(set((a, b) for (a, b) in links if (b, a) in links and a < b))
        kinds = []
        for pair in sorted(links):
            for k in links[pair]["kinds"]:
                if k not in kinds:
                    kinds.append(k)
        closing = cut if cut else sorted(links)
        closing_kinds = []
        for pair in closing:
            for k in links[pair]["kinds"]:
                if k not in closing_kinds:
                    closing_kinds.append(k)
        return {
            "members": sorted(members),
            "size": len(members),
            "shape": "pair" if len(members) == 2 else "scc",
            "basis": basis,
            "layers": sorted(set(g.files[g.index[m]].layer for m in members)),
            "links": [{"s": s, "t": t, "kinds": links[(s, t)]["kinds"],
                       "n": links[(s, t)]["n"], "amb": links[(s, t)]["amb"],
                       "syms": sorted(links[(s, t)]["syms"])[:6],
                       "cut": (s, t) in cut,
                       "up": g.files[g.index[t]].rank > g.files[g.index[s]].rank}
                      for (s, t) in sorted(links)],
            "cut": [list(p) for p in cut],
            "mutual": [list(p) for p in mutual],
            "kinds": sorted(kinds),
            "closingKinds": sorted(closing_kinds),
            # Which certain components this one merges. Empty on a certain component itself.
            "contains": [sorted(cs) for cs in certain_sets if cs < set(members)],
        }

    out = [describe(c, unamb, "certain") for c in certain]
    out += [describe(c, full, "ambiguous") for c in extra]
    # Biggest first, then by first member, so the worst tangle is the first thing read.
    out.sort(key=lambda c: (c["basis"] != "certain", -c["size"], c["members"][0]))
    return out


def find_mutual_pairs(g, cycles):
    """Every two files that reference each other, listed whether or not a larger SCC swallows them.

    A mutual pair IS a 2-cycle, but many of them can sit inside one large component, and
    `find_cycles` reports that component once. This list is the cheap-to-see shape on its own.
    `basis: ambiguous` means at least one of the two directions exists only through an ambiguous
    call edge, so the pair may not be real.
    """
    pairs = {}
    for (s, t, kind), rec in sorted(g.edges.items()):
        if s < t:
            a, b, fwd = s, t, True
        else:
            a, b, fwd = t, s, False
        p = pairs.setdefault((a, b), {"ab": {}, "ba": {}})
        side = p["ab"] if fwd else p["ba"]
        side.setdefault(kind, {"n": 0, "amb": True, "syms": set()})
        side[kind]["n"] += rec["n"]
        side[kind]["amb"] = side[kind]["amb"] and rec["amb"]
        side[kind]["syms"].update(rec["syms"])

    comp_of = {}
    for ci, c in enumerate(cycles):
        for m in c["members"]:
            comp_of.setdefault(m, ci)

    out = []
    for (a, b), p in sorted(pairs.items()):
        if not p["ab"] or not p["ba"]:
            continue

        def side(d):
            return {"kinds": sorted(d), "n": sum(v["n"] for v in d.values()),
                    "amb": all(v["amb"] for v in d.values()),
                    "syms": sorted(set().union(*[v["syms"] for v in d.values()]))[:6]}
        sa, sb = side(p["ab"]), side(p["ba"])
        out.append({
            "a": a, "b": b, "ab": sa, "ba": sb,
            "basis": "ambiguous" if (sa["amb"] or sb["amb"]) else "certain",
            "kinds": sorted(set(sa["kinds"]) | set(sb["kinds"])),
            "comp": comp_of.get(a, -1),
            "up": (g.files[g.index[b]].rank > g.files[g.index[a]].rank
                   or g.files[g.index[a]].rank > g.files[g.index[b]].rank),
            "layers": sorted(set([g.files[g.index[a]].layer, g.files[g.index[b]].layer])),
        })
    out.sort(key=lambda p: (p["basis"] != "certain", -(p["ab"]["n"] + p["ba"]["n"]),
                            p["a"], p["b"]))
    return out


# =================================================================================================
# LAYOUT  (computed here, not in the browser, so the file is byte-identical between runs)
# =================================================================================================
COL_W, ROW_H, NODE_W, NODE_H = 250, 30, 200, 22
PAD_X, PAD_Y, HEAD_H = 34, 34, 42


def layout(g):
    by_layer = {}
    for f in g.files:
        by_layer.setdefault(f.layer, []).append(f)
    pos = {}
    for name, _rank in LAYERS:
        for i, f in enumerate(by_layer.get(name, [])):
            pos[f.id] = (PAD_X + LAYER_COL[name] * COL_W, PAD_Y + HEAD_H + i * ROW_H)
    width = PAD_X * 2 + (len(LAYERS) - 1) * COL_W + NODE_W
    height = PAD_Y * 2 + HEAD_H + max([len(v) for v in by_layer.values()] or [1]) * ROW_H
    return pos, width, height, by_layer


def make_data(g, source_hash, dropped):
    pos, width, height, by_layer = layout(g)
    reach = reachability(g)
    cycles = find_cycles(g)
    mutual = find_mutual_pairs(g, cycles)
    cyc_of_node = {}
    for _ci, _c in enumerate(cycles):
        for _m in _c["members"]:
            cyc_of_node.setdefault(_m, _ci)

    nodes = []
    for f in g.files:
        x, y = pos[f.id]
        funcs = []
        no_ref = no_unref = 0
        for fn in sorted(f.funcs, key=lambda s: (s.owner or "", s.name, s.line)):
            r = reach[(f.id, fn.owner or "<global>", fn.name, fn.line)]
            if not r["reachable"]:
                if r["reflected"]:
                    no_ref += 1
                else:
                    no_unref += 1
            funcs.append({"n": fn.name, "o": fn.owner or "<global>", "l": fn.line,
                          "spec": (r["spec"] or "")[:90], "ok": r["reachable"],
                          "why": r["why"] or "", "refl": r["reflected"]})
        members = [{"n": m.name, "t": m.detail[:80], "o": m.owner or "<global>",
                    "spec": (m.spec or "")[:70], "l": m.line}
                   for m in sorted(f.members, key=lambda s: (s.owner or "", s.name))]
        # The `docLink` setting makes the link without a lookup table. A file in the root layer
        # uses the `docRootPage` setting as its page.
        stem = os.path.basename(f.id)[:-3]
        page = DOC_ROOT_PAGE if f.layer == ROOT_LAYER else f.layer.replace("/", "-")
        nodes.append({
            "id": f.id,
            "doc": DOC_LINK.replace("{page}", page).replace("{anchor}", stem.lower()),
            "name": os.path.basename(f.id),
            "layer": f.layer, "rank": f.rank, "col": LAYER_COL[f.layer],
            "x": x, "y": y, "lines": f.lines,
            "decls": [{"n": d.name, "k": d.kind, "base": d.detail or "", "l": d.line}
                      for d in f.decls],
            "events": [{"n": e.name, "p": e.detail, "l": e.line} for e in f.events],
            "members": members, "funcs": funcs,
            "docs": f.docs, "todos": f.todos, "migrated": f.migrated,
            "noCallerRefl": no_ref, "noCallerUnrefl": no_unref,
            "cyc": cyc_of_node.get(f.id, -1),
        })

    # An edge belongs to a cycle when both ends sit in the same component AND that component's
    # basis admits it: an ambiguous edge is never counted into a `certain` component.
    cut_links = set()
    for c in cycles:
        for s, t in c["cut"]:
            cut_links.add((s, t))
    edges = []
    for (s, t, kind), rec in sorted(g.edges.items()):
        si, ti = g.index[s], g.index[t]
        up = g.files[ti].rank > g.files[si].rank
        ci = -1
        for j, c in enumerate(cycles):
            if s in c["members"] and t in c["members"]:
                if rec["amb"] and c["basis"] == "certain":
                    continue
                ci = j
                break
        edges.append({"s": si, "t": ti, "k": kind, "n": rec["n"],
                      "up": up, "cross": g.files[ti].layer != g.files[si].layer,
                      "amb": bool(rec["amb"]), "why": rec["why"][:8], "cyc": ci,
                      "cut": bool(ci >= 0 and (s, t) in cut_links),
                      "syms": sorted(rec["syms"])[:6]})

    events = []
    for name in sorted(g.events):
        ev = dict(g.events[name])
        ev["holders"] = [list(h) for h in ev["holders"]]
        events.append(ev)

    props = [dict(g.props[k]) for k in sorted(g.props)]

    counts = {"files": len(g.files), "edges": len(edges)}
    for k in ("inherit", "member", "call", "event", "type"):
        counts[k] = sum(1 for e in edges if e["k"] == k)
    counts["up"] = sum(1 for e in edges if e["up"])
    counts["cross"] = sum(1 for e in edges if e["cross"])
    counts["ambiguous"] = sum(1 for e in edges if e["amb"])
    counts["funcs"] = sum(len(n["funcs"]) for n in nodes)
    counts["noCallerRefl"] = sum(n["noCallerRefl"] for n in nodes)
    counts["noCallerUnrefl"] = sum(n["noCallerUnrefl"] for n in nodes)
    counts["events"] = len(events)
    counts["props"] = len(props)
    counts["propsUnwired"] = sum(1 for r in props if r["state"] != "ok")
    counts["propsAmbiguous"] = sum(1 for r in props if r["ambiguous"])
    counts["cycles"] = len(cycles)
    counts["cyclePairs"] = sum(1 for c in cycles if c["shape"] == "pair"
                               and c["basis"] == "certain")
    counts["cycleSccs"] = sum(1 for c in cycles if c["shape"] == "scc"
                              and c["basis"] == "certain")
    counts["cyclesAmbiguous"] = sum(1 for c in cycles if c["basis"] == "ambiguous")
    counts["cycleNodes"] = len(set(m for c in cycles if c["basis"] == "certain"
                                   for m in c["members"]))
    counts["mutual"] = sum(1 for p in mutual if p["basis"] == "certain")
    counts["mutualAmbiguous"] = sum(1 for p in mutual if p["basis"] == "ambiguous")
    counts["worstCycle"] = max([c["size"] for c in cycles if c["basis"] == "certain"] or [0])
    counts["classes"] = sum(1 for n in nodes for d in n["decls"] if d["k"] == "class")
    counts["structs"] = sum(1 for n in nodes for d in n["decls"] if d["k"] == "struct")
    counts["enums"] = sum(1 for n in nodes for d in n["decls"] if d["k"] == "enum")

    matrix = {}
    for e in edges:
        key = "%s>%s" % (g.files[e["s"]].layer, g.files[e["t"]].layer)
        matrix[key] = matrix.get(key, 0) + 1

    return {
        "hash": source_hash,
        "root": ROOT.replace("\\", "/"),
        "layers": [{"name": n, "rank": r, "col": LAYER_COL[n], "n": len(by_layer.get(n, []))}
                   for n, r in LAYERS],
        "w": width, "h": height,
        "nodes": nodes, "edges": edges, "events": events, "props": props,
        "cycles": cycles, "mutual": mutual,
        "counts": counts, "matrix": matrix, "dropped": dropped,
    }


# =================================================================================================
# HTML
# =================================================================================================
HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Script dependency graph — __PROJECT__</title>
<style>
:root{--bg:#0f1115;--panel:#161a21;--panel2:#1b202a;--line:#252b36;--ink:#e6e9ef;--dim:#8b93a3;
 --inherit:#c58af9;--member:#3d84f7;--call:#3fb27f;--event:#f0a132;--type:#5d6675;
 --up:#e5534b;--sel:#ffd76e;--warn:#e5534b;--cyc:#ff5fa2}
@media (prefers-color-scheme:light){:root{--bg:#f6f7f9;--panel:#fff;--panel2:#f0f2f5;
 --line:#dfe3e9;--ink:#1a1d23;--dim:#5f6773;--type:#a8b0bd}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:13px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;overflow:hidden}
header{padding:9px 16px;border-bottom:1px solid var(--line);background:var(--panel);
 display:flex;gap:14px;align-items:center;flex-wrap:wrap}
h1{margin:0;font-size:15px;letter-spacing:-.2px;white-space:nowrap}
.pill{font-size:11.5px;padding:2px 9px;border-radius:999px;border:1px solid var(--line);
 background:var(--bg);white-space:nowrap}
.pill b{font-variant-numeric:tabular-nums}
.pill.bad{border-color:var(--up);color:var(--up)}
#q{background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:7px;
 padding:5px 9px;width:210px;font:inherit;font-size:12px}
main{display:grid;grid-template-columns:210px 1fr 380px;height:calc(100vh - 47px)}
aside{overflow:auto;padding:12px 14px;border-right:1px solid var(--line);background:var(--panel)}
aside.right{border-right:0;border-left:1px solid var(--line)}
aside h2{font-size:10.5px;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
 margin:16px 0 6px}
aside h2:first-child{margin-top:0}
label.c{display:flex;gap:7px;align-items:center;font-size:12px;padding:2px 0;cursor:pointer}
label.c input{margin:0}
label.c .sw{width:20px;height:3px;border-radius:2px;flex:none}
label.c .ct{margin-left:auto;color:var(--dim);font-variant-numeric:tabular-nums;font-size:11px}
.stage{position:relative;overflow:hidden;background:var(--bg);cursor:grab}
.stage.drag{cursor:grabbing}
svg{display:block;width:100%;height:100%}
.edge{fill:none;stroke-width:1.1;opacity:.30}
.edge.k-inherit{stroke:var(--inherit)}.edge.k-member{stroke:var(--member)}
.edge.k-call{stroke:var(--call)}.edge.k-event{stroke:var(--event)}
.edge.k-type{stroke:var(--type);opacity:.16}
.edge.up{stroke:var(--up);opacity:.55;stroke-width:1.5}
.edge.amb{stroke-dasharray:3 3}
.edge.hl{opacity:1;stroke-width:2.2}
.edge.off{display:none}
.edge.cyc{stroke:var(--cyc);opacity:.7;stroke-width:1.6}
.edge.cyc.cut{stroke-width:3;opacity:1}
.nd rect{fill:var(--panel2);stroke:var(--line);stroke-width:1;rx:4}
.nd text{font:11px ui-sans-serif,-apple-system,"Segoe UI",sans-serif;fill:var(--ink)}
.nd .lay{fill:var(--dim);font-size:9.5px}
.nd{cursor:pointer}
.nd:hover rect{stroke:var(--ink)}
.nd.sel rect{stroke:var(--sel);stroke-width:2}
.nd.dim{opacity:.16}
.nd.off{display:none}
.nd.hit rect{fill:var(--sel);fill-opacity:.20;stroke:var(--sel)}
.nd.cyc rect{stroke:var(--cyc)}
.nd.cycsel rect{stroke:var(--cyc);stroke-width:2.5;fill:var(--cyc);fill-opacity:.16}
.nd .flag{fill:var(--warn)}
.tag.cyc{border-color:var(--cyc);color:var(--cyc)}
.tag.cut{border-color:var(--cyc);color:var(--cyc);font-weight:700}
.colhead{font:600 11px ui-sans-serif,sans-serif;fill:var(--dim);text-transform:uppercase;
 letter-spacing:.08em}
.colrule{stroke:var(--line);stroke-width:1;stroke-dasharray:2 4}
.right .path{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;color:var(--dim);
 word-break:break-all}
.sym{padding:3px 0;border-bottom:1px solid var(--line);font-size:12px}
.sym:last-child{border-bottom:0}
.sym .k{color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px}
.bad{color:var(--warn)}
.muted{color:var(--dim)}
.lnk{color:var(--member);cursor:pointer;text-decoration:underline dotted}
.tag{display:inline-block;font-size:10px;padding:1px 6px;border-radius:99px;
 border:1px solid var(--line);color:var(--dim);margin:0 4px 3px 0}
.tag.inherit{border-color:var(--inherit);color:var(--inherit)}
.tag.member{border-color:var(--member);color:var(--member)}
.tag.call{border-color:var(--call);color:var(--call)}
.tag.event{border-color:var(--event);color:var(--event)}
.tag.type{border-color:var(--type);color:var(--type)}
.tag.up{border-color:var(--up);color:var(--up)}
details{margin:6px 0}
summary{cursor:pointer;font-size:11px;text-transform:uppercase;letter-spacing:.08em;
 color:var(--dim);outline:none}
table.mx{border-collapse:collapse;font-size:10.5px;width:100%}
table.mx td,table.mx th{border:1px solid var(--line);padding:2px 4px;text-align:right;
 font-variant-numeric:tabular-nums}
table.mx th{color:var(--dim);font-weight:500;text-align:left}
table.mx td.u{color:var(--up);font-weight:700}
table.mx td.z{color:var(--line)}
.foot{padding:8px 14px;color:var(--dim);font-size:11px;border-top:1px solid var(--line)}
kbd{border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:0 4px;
 font:11px ui-monospace,monospace}
</style></head><body>
<header>
  <h1>Script dependency graph</h1>
  <input id="q" placeholder="find file, class, function…  ( / )" autocomplete="off">
  <span class="pill">files <b id="s-files"></b></span>
  <span class="pill">edges <b id="s-edges"></b></span>
  <span class="pill">cross-layer <b id="s-cross"></b></span>
  <span class="pill bad">upward <b id="s-up"></b></span>
  <span class="pill" id="p-cyc" style="cursor:pointer;border-color:var(--cyc);color:var(--cyc)">
    cycles <b id="s-cyc"></b></span>
  <span class="pill bad">no caller in Script/ <b id="s-nc"></b></span>
  <span class="pill" id="s-sel"></span>
</header>
<main>
  <aside class="left">
    <h2>Edge kind</h2><div id="kinds"></div>
    <h2>Layer</h2><div id="layers"></div>
    <h2>Show</h2>
    <label class="c"><input type="checkbox" id="f-cross">cross-layer edges only</label>
    <label class="c"><input type="checkbox" id="f-up">upward edges only</label>
    <label class="c"><input type="checkbox" id="f-amb" checked>include ambiguous call edges</label>
    <label class="c"><input type="checkbox" id="f-focus" checked>focus neighbourhood on select</label>
    <label class="c"><input type="checkbox" id="f-nc">only files with a callerless function</label>
    <label class="c"><input type="checkbox" id="f-cyc">cycle edges only</label>
    <label class="c"><input type="checkbox" id="f-cut">cut links only (needs the box above)</label>
    <div class="muted" style="font-size:11px;margin-top:8px">An edge points from the file that
      <b>depends</b> to the file it depends <b>on</b>. <span style="color:var(--up)">Red</span> = the
      target ranks <b>above</b> the source, which the layering forbids. Dashed = the callee name is
      declared in more than one file, so the edge is drawn to every candidate rather than dropped.
      A <span style="color:var(--warn)">dot</span> on a node = a function with no caller in
      <span class="mono">Script/</span>; solid = one of them is unreflected too.
      <span style="color:var(--cyc)">Pink</span> = the edge sits inside a cycle; a thick pink edge
      is a <b>cut link</b>, one whose removal breaks the component apart on its own.</div>
    <h2>Layer &rarr; layer</h2><div id="matrix"></div>
    <h2>View</h2>
    <label class="c"><span class="lnk" id="reset">reset zoom</span></label>
    <div class="muted" style="font-size:11px;margin-top:6px">drag to pan · wheel to zoom ·
      <kbd>/</kbd> search · <kbd>Esc</kbd> clear</div>
  </aside>
  <div class="stage" id="stage">
    <svg id="svg"><g id="vp"><g id="cols"></g><g id="edges"></g><g id="nodes"></g></g></svg>
  </div>
  <aside class="right" id="detail"></aside>
</main>
<script id="graph-data" type="application/json">__DATA__</script>
<script>
var D = JSON.parse(document.getElementById('graph-data').textContent);
var KINDS = ['inherit','member','call','event','type'];
var NODE_W=__NODE_W__, NODE_H=__NODE_H__, COL_W=__COL_W__, HEAD_H=__HEAD_H__, PAD_Y=__PAD_Y__;
var SVGNS='http://www.w3.org/2000/svg';
var sel=-1, kindOn={}, layerOn={}, nodeEls=[], edgeEls=[], adj=[];
var view='problems', cycSel=-1;

function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function el(t,a){var e=document.createElementNS(SVGNS,t);for(var k in a)e.setAttribute(k,a[k]);return e;}

// ---- build ------------------------------------------------------------------------------------
D.nodes.forEach(function(){adj.push({in:[],out:[]});});
D.edges.forEach(function(e,i){adj[e.s].out.push(i);adj[e.t].in.push(i);});

(function(){
  var g=document.getElementById('cols');
  D.layers.forEach(function(L){
    var x=34+L.col*COL_W;
    var t=el('text',{x:x,y:PAD_Y+14,'class':'colhead'});
    t.textContent=L.name+'  ('+L.n+')  r'+L.rank; g.appendChild(t);
    var r=el('line',{x1:x-8,y1:PAD_Y+22,x2:x+NODE_W+8,y2:PAD_Y+22,'class':'colrule'});
    g.appendChild(r);
  });
})();

(function(){
  var g=document.getElementById('edges');
  D.edges.forEach(function(e,i){
    var a=D.nodes[e.s], b=D.nodes[e.t];
    var sx,tx, sy=a.y+NODE_H/2, ty=b.y+NODE_H/2, p;
    if(a.col===b.col){
      sx=a.x+NODE_W; tx=b.x+NODE_W;
      p='M'+sx+' '+sy+' C '+(sx+70)+' '+sy+', '+(tx+70)+' '+ty+', '+tx+' '+ty;
    }else if(b.col>a.col){
      sx=a.x+NODE_W; tx=b.x;
      var m=(sx+tx)/2;
      p='M'+sx+' '+sy+' C '+m+' '+sy+', '+m+' '+ty+', '+tx+' '+ty;
    }else{
      sx=a.x; tx=b.x+NODE_W;
      var m2=(sx+tx)/2;
      p='M'+sx+' '+sy+' C '+m2+' '+sy+', '+m2+' '+ty+', '+tx+' '+ty;
    }
    var cls='edge k-'+e.k+(e.up?' up':'')+(e.amb?' amb':'')+
            (e.cyc>=0?' cyc':'')+(e.cut?' cut':'');
    var path=el('path',{d:p,'class':cls});
    g.appendChild(path); edgeEls.push(path);
  });
})();

(function(){
  var g=document.getElementById('nodes');
  D.nodes.forEach(function(n,i){
    var grp=el('g',{'class':'nd','data-i':i,'data-id':n.id});
    grp.appendChild(el('rect',{x:n.x,y:n.y,width:NODE_W,height:NODE_H}));
    var t=el('text',{x:n.x+7,y:n.y+15});
    var label=n.name.replace(/\.as$/,'');
    t.textContent=label.length>26?label.slice(0,25)+'…':label;
    grp.appendChild(t);
    if(n.noCallerUnrefl>0||n.noCallerRefl>0){
      var f=el('circle',{cx:n.x+NODE_W-9,cy:n.y+NODE_H/2,r:3.2,'class':'flag'});
      f.setAttribute('fill-opacity',n.noCallerUnrefl>0?'1':'0.45');
      grp.appendChild(f);
    }
    var ti=el('title'); ti.textContent=n.id+'  ('+n.lines+' lines)'; grp.appendChild(ti);
    grp.addEventListener('click',function(ev){ev.stopPropagation();select(i);});
    g.appendChild(grp); nodeEls.push(grp);
  });
})();

// ---- filters ----------------------------------------------------------------------------------
(function(){
  var kc={}; D.edges.forEach(function(e){kc[e.k]=(kc[e.k]||0)+1;});
  var box=document.getElementById('kinds');
  KINDS.forEach(function(k){
    kindOn[k]=(k!=='type');
    var l=document.createElement('label'); l.className='c';
    l.innerHTML='<input type="checkbox" data-k="'+k+'"'+(kindOn[k]?' checked':'')+'>'+
      '<span class="sw" style="background:var(--'+k+')"></span>'+k+
      '<span class="ct">'+(kc[k]||0)+'</span>';
    l.querySelector('input').addEventListener('change',function(){kindOn[k]=this.checked;apply();});
    box.appendChild(l);
  });
  var lb=document.getElementById('layers');
  D.layers.forEach(function(L){
    layerOn[L.name]=true;
    var l=document.createElement('label'); l.className='c';
    l.innerHTML='<input type="checkbox" checked>'+L.name+'<span class="ct">'+L.n+'</span>';
    l.querySelector('input').addEventListener('change',function(){
      layerOn[L.name]=this.checked;apply();});
    lb.appendChild(l);
  });
})();

(function(){
  var names=D.layers.map(function(L){return L.name;});
  var h='<table class="mx"><tr><th></th>';
  names.forEach(function(n){h+='<th style="text-align:right">'+n.split('/').pop().slice(0,4)+'</th>';});
  h+='</tr>';
  names.forEach(function(a){
    h+='<tr><th>'+a+'</th>';
    names.forEach(function(b){
      var v=D.matrix[a+'>'+b]||0;
      var ra=D.layers.filter(function(L){return L.name===a;})[0].rank;
      var rb=D.layers.filter(function(L){return L.name===b;})[0].rank;
      h+='<td class="'+(v===0?'z':(rb>ra?'u':''))+'">'+(v||'·')+'</td>';
    });
    h+='</tr>';
  });
  document.getElementById('matrix').innerHTML=h+'</table>';
})();

function query(){return document.getElementById('q').value.trim().toLowerCase();}

function matches(n,q){
  if(!q) return false;
  if(n.id.toLowerCase().indexOf(q)>=0) return true;
  var i;
  for(i=0;i<n.decls.length;i++) if(n.decls[i].n.toLowerCase().indexOf(q)>=0) return true;
  for(i=0;i<n.funcs.length;i++) if(n.funcs[i].n.toLowerCase().indexOf(q)>=0) return true;
  for(i=0;i<n.members.length;i++) if(n.members[i].n.toLowerCase().indexOf(q)>=0) return true;
  for(i=0;i<n.events.length;i++) if(n.events[i].n.toLowerCase().indexOf(q)>=0) return true;
  return false;
}

function apply(){
  var crossOnly=document.getElementById('f-cross').checked;
  var upOnly=document.getElementById('f-up').checked;
  var amb=document.getElementById('f-amb').checked;
  var focus=document.getElementById('f-focus').checked;
  var ncOnly=document.getElementById('f-nc').checked;
  var cycOnly=document.getElementById('f-cyc').checked;
  var cutOnly=document.getElementById('f-cut').checked;
  var cycMembers=null;
  if(cycSel>=0){cycMembers={};D.cycles[cycSel].members.forEach(function(id){cycMembers[id]=1;});}
  var q=query();
  var near=null;
  if(sel>=0&&focus){
    near={}; near[sel]=1;
    adj[sel].out.forEach(function(i){if(kindOn[D.edges[i].k])near[D.edges[i].t]=1;});
    adj[sel].in.forEach(function(i){if(kindOn[D.edges[i].k])near[D.edges[i].s]=1;});
  }
  var shown=0, vis=[];
  D.nodes.forEach(function(n,i){
    var on=layerOn[n.layer];
    if(on&&ncOnly&&!(n.noCallerRefl+n.noCallerUnrefl)) on=false;
    vis[i]=on;
    var e=nodeEls[i];
    e.classList.toggle('off',!on);
    e.classList.toggle('dim',!!(on&&near&&!near[i]));
    e.classList.toggle('hit',!!(on&&q&&matches(n,q)));
    e.classList.toggle('sel',i===sel);
    e.classList.toggle('cyc',n.cyc>=0);
    e.classList.toggle('cycsel',!!(cycMembers&&cycMembers[n.id]));
    if(on)shown++;
  });
  var ec=0;
  D.edges.forEach(function(ed,i){
    // An edge is drawn only when BOTH endpoints are drawn - a half-anchored curve reads as a
    // dependency on nothing.
    var on=kindOn[ed.k]&&vis[ed.s]&&vis[ed.t];
    if(on&&crossOnly&&!ed.cross) on=false;
    if(on&&upOnly&&!ed.up) on=false;
    if(on&&!amb&&ed.amb) on=false;
    if(on&&cycOnly&&ed.cyc<0) on=false;
    if(on&&cycOnly&&cutOnly&&!ed.cut) on=false;
    if(on&&cycMembers&&!(cycMembers[D.nodes[ed.s].id]&&cycMembers[D.nodes[ed.t].id])) on=false;
    if(on&&near&&!(ed.s===sel||ed.t===sel)) on=false;
    var e=edgeEls[i];
    e.classList.toggle('off',!on);
    e.classList.toggle('hl',!!(on&&sel>=0&&(ed.s===sel||ed.t===sel)));
    if(on)ec++;
  });
  document.getElementById('s-sel').textContent=shown+' / '+D.nodes.length+' files, '+
    ec+' edges shown';
}

// ---- detail panel -----------------------------------------------------------------------------
function edgeList(i){
  var out={},inn={};
  adj[i].out.forEach(function(j){var e=D.edges[j];(out[e.k]=out[e.k]||[]).push(e);});
  adj[i].in.forEach(function(j){var e=D.edges[j];(inn[e.k]=inn[e.k]||[]).push(e);});
  function block(m,dirLabel,other){
    var h='';
    KINDS.forEach(function(k){
      if(!m[k])return;
      h+='<div class="sym"><span class="k">'+dirLabel+' · '+k+' ('+m[k].length+')</span><br>';
      m[k].sort(function(a,b){return b.n-a.n;}).forEach(function(e){
        var o=D.nodes[other(e)];
        h+='<div><span class="lnk" data-goto="'+esc(o.id)+'">'+esc(o.id)+'</span> '+
           '<span class="muted">×'+e.n+(e.up?' <span class="tag up">upward</span>':'')+
           (e.amb?' <span class="tag">ambiguous</span>':'')+'</span>';
        if(e.why.length) h+='<div class="muted mono">'+esc(e.why.join(', '))+'</div>';
        h+='</div>';
      });
      h+='</div>';
    });
    return h;
  }
  return block(out,'depends on',function(e){return e.t;})+
         block(inn,'used by',function(e){return e.s;});
}

// `vscode://file/<abs>:<line>` opens the real source. The absolute root is baked in at generation
// time, so the link is right for this checkout and inert anywhere else.
function link(id,line,label){
  return '<a class="lnk" href="vscode://file/'+esc(D.root)+'/Script/'+esc(id)+':'+line+'">'+
         esc(label)+'</a>';
}

function detail(i){
  var n=D.nodes[i];
  var h='<h2>File</h2><div><b>'+esc(n.name)+'</b></div><div class="path">Script/'+esc(n.id)+
        '</div><div class="muted">layer <b>'+esc(n.layer)+'</b> · rank '+n.rank+' · '+
        n.lines+' lines</div>';
  h+='<div style="margin-top:6px"><span class="tag">node id <span class="mono">'+esc(n.id)+
     '</span></span> '+link(n.id,1,'open in editor')+
     ' · <a class="lnk" href="'+esc(n.doc)+'">prose page</a></div>';

  if(n.decls.length){
    h+='<h2>Declares</h2>';
    n.decls.forEach(function(d){
      h+='<div class="sym"><span class="k">'+d.k+'</span> <span class="mono">'+esc(d.n)+'</span>'+
         (d.base?' <span class="muted">: '+esc(d.base)+'</span>':'')+
         ' <span class="muted">:'+d.l+'</span></div>';
    });
  }
  if(n.events.length){
    h+='<h2>Declares events</h2>';
    n.events.forEach(function(e){
      var rec=D.events.filter(function(x){return x.name===e.n;})[0]||{};
      var st=rec.state||'?';
      h+='<div class="sym"><span class="mono">'+esc(e.n)+'</span>('+esc(e.p)+')'+
         ' <span class="tag'+(st==='ok'?'':' up')+'">'+esc(st)+'</span>'+
         '<div class="muted">holders '+((rec.holders||[]).length)+
         ' · broadcasts '+((rec.broadcasts||[]).length)+
         ' · subscribers in Script/ '+((rec.subs||[]).length)+'</div></div>';
    });
  }

  var bad=n.funcs.filter(function(f){return !f.ok;});
  h+='<h2>Functions ('+n.funcs.length+')</h2>';
  if(bad.length){
    h+='<div class="sym bad">'+bad.length+' with <b>no caller in Script/</b> — '+
       bad.filter(function(f){return !f.refl;}).length+' of them unreflected, so nothing outside '+
       'the script layer can reach those either.</div>';
  }
  h+='<details><summary>list all</summary>';
  n.funcs.forEach(function(f){
    h+='<div class="sym"><span class="mono'+(f.ok?'':' bad')+'">'+esc(f.o)+'::'+esc(f.n)+
       '</span> '+link(n.id,f.l,':'+f.l)+
       (f.spec?' <span class="tag">'+esc(f.spec)+'</span>':' <span class="tag">unreflected</span>')+
       '<div class="muted">'+(f.ok?esc(f.why):'<span class="bad">no caller in Script/</span>')+
       '</div></div>';
  });
  h+='</details>';

  h+='<h2>Members ('+n.members.length+')</h2><details><summary>list all</summary>';
  n.members.forEach(function(m){
    h+='<div class="sym"><span class="mono">'+esc(m.t)+' '+esc(m.n)+'</span> '+
       '<span class="muted">:'+m.l+(m.spec!==''?' · UPROPERTY('+esc(m.spec)+')':' · unreflected')+
       '</span></div>';
  });
  h+='</details>';

  if(n.docs.length||n.todos.length||n.migrated){
    h+='<h2>Markers</h2>';
    if(n.migrated) h+='<div class="sym">'+n.migrated+' <span class="k">Migrated from … Blueprint '+
      'variable</span> provenance comments (read by check_member_specifiers.py)</div>';
    n.todos.forEach(function(t){h+='<span class="tag up">TODO('+esc(t)+')</span>';});
    if(n.docs.length){
      h+='<details><summary>'+n.docs.length+' DOC: pointers into the frozen legacy tree'+
         '</summary>';
      n.docs.forEach(function(d){h+='<div class="sym mono muted">'+esc(d)+'</div>';});
      h+='</details>';
    }
  }

  if(n.cyc>=0){
    var c=D.cycles[n.cyc];
    h+='<h2>Cycle</h2><div class="sym">This file is inside a <b>'+
       (c.shape==='pair'?'mutual pair':''+c.size+'-file SCC')+'</b>'+
       (c.basis==='certain'?'':' <span class="tag">ambiguity-dependent</span>')+
       '. <span class="lnk tag cyc" data-cyc="'+n.cyc+'">show only this component</span>'+
       '<div class="muted">Nothing in it can be lifted out without the rest. '+
       (c.cut.length? c.cut.length+' cut link(s) would break it apart.'
                    : 'No single link cuts it.')+'</div></div>';
    var mp=D.mutual.filter(function(p){return p.a===n.id||p.b===n.id;});
    if(mp.length){
      h+='<div class="sym"><span class="k">references each other with ('+mp.length+')</span><br>';
      mp.forEach(function(p){
        var o=(p.a===n.id?p.b:p.a);
        h+='<div>'+pairLine(o)+' <span class="tag cyc">'+esc(p.kinds.join(', '))+'</span>'+
           (p.basis==='certain'?'':'<span class="tag">ambiguous</span>')+'</div>';
      });
      h+='</div>';
    }
  }
  h+='<h2>Edges</h2>'+edgeList(i);
  var box=document.getElementById('detail');
  box.innerHTML=h;
  wire(box);
}

function problems(){
  var h='<h2>What this graph is</h2><div class="muted">Columns are the layers of <span '+
    'class="mono">Script/</span>, left to right by dependency rank. An edge points from the file '+
    'that <b>depends</b> to the file it depends <b>on</b>. Red edges run <b>upward</b> — the '+
    'target sits at a higher rank than the source, which the layering forbids.</div>';
  h+='<h2>A zero here is about this tool’s reach</h2><div class="muted">A red dot on a node '+
    'means at least one function in it has <b>no caller in <span class="mono">Script/</span></b>. '+
    'That is never the same as unused: a Blueprint can call any reflected function, a component is '+
    'found at runtime by <span class="mono">GetComponentByClass</span>, and a '+
    '<span class="mono">K2Node_AddDelegate</span> bind produces no call node at all. A '+
    '<b>solid</b> dot means at least one of them is also <b>unreflected</b> — no '+
    'UFUNCTION — so nothing outside the script layer can reach it either. That pair is the '+
    'strong signal.</div>';
  h+='<h2>Cycles ('+D.counts.cycleSccs+' SCC of 3+, '+D.counts.cyclePairs+' 2-file, '+
     D.counts.mutual+' mutual pairs)</h2><div class="muted">'+D.counts.cycleNodes+' of '+
     D.counts.files+' files sit inside a cycle. <span class="lnk" data-view="cycles">open the '+
     'cycles view</span> for the components, their cut links and the edge kinds that close '+
     'them.</div>';
  var pr=D.props.filter(function(r){return r.state!=='ok';});
  h+='<h2>Delegate properties not fully wired ('+pr.length+' of '+D.props.length+')</h2>';
  h+='<div class="muted">The project’s recurring silent defect is a dispatcher whose '+
     'declaration moved and whose broadcast did not. The unit is the PROPERTY, because one event '+
     'type can be held by a wired property and an unwired one at the same time.</div>';
  pr.forEach(function(r){
    h+='<div class="sym"><span class="mono">'+esc(r.cls||'-')+'::'+esc(r.prop)+'</span> '+
       '<span class="tag up">'+esc(r.state)+'</span>'+
       (r.ambiguous?' <span class="tag">name shared with another event type</span>':'')+
       '<div class="muted"><span class="lnk" data-goto="'+esc(r.file)+'">'+esc(r.file)+
       '</span> · '+esc(r.event)+' · broadcasts '+r.broadcasts.length+
       ' · subscribers in Script/ '+r.subs.length+'</div></div>';
  });
  var nodes=D.nodes.filter(function(n){return n.noCallerUnrefl>0;})
    .sort(function(a,b){return b.noCallerUnrefl-a.noCallerUnrefl;});
  h+='<h2>Unreflected and no caller in Script/</h2>';
  h+='<div class="muted">Nothing in the script layer calls these, and no UFUNCTION means no '+
     'Blueprint, timer or delegate can either.</div>';
  nodes.forEach(function(n){
    h+='<div class="sym"><span class="lnk" data-goto="'+esc(n.id)+'">'+esc(n.id)+'</span> '+
       '<b>'+n.noCallerUnrefl+'</b><div class="muted mono">'+
       esc(n.funcs.filter(function(f){return !f.ok&&!f.refl;})
         .map(function(f){return f.o+'::'+f.n;}).join(', '))+'</div></div>';
  });
  var iso=D.nodes.filter(function(n,i){return adj[i].in.length===0;});
  h+='<h2>No incoming edge from any other .as file ('+iso.length+')</h2>';
  iso.forEach(function(n){
    h+='<div class="sym"><span class="lnk" data-goto="'+esc(n.id)+'">'+esc(n.id)+'</span></div>';
  });
  if(D.dropped && D.dropped.length){
    h+='<h2 class="bad">Parser left out</h2>';
    D.dropped.forEach(function(d){h+='<div class="sym bad mono">'+esc(d)+'</div>';});
  }else{
    h+='<h2>Parser left out</h2><div class="muted">Nothing. Every <span class="mono">.as</span> '+
       'file parsed, and every <span class="mono">UPROPERTY</span> / '+
       '<span class="mono">UFUNCTION</span> in the tree was matched.</div>';
  }
  h+='<h2>Source fingerprint</h2><div class="mono muted">'+esc(D.hash)+'</div>';
  var box=document.getElementById('detail');
  box.innerHTML=h;
  wire(box);
}

function wire(box){
  box.querySelectorAll('[data-goto]').forEach(function(a){
    a.addEventListener('click',function(){
      var id=a.getAttribute('data-goto');
      for(var j=0;j<D.nodes.length;j++) if(D.nodes[j].id===id){select(j);centre(j);break;}
    });
  });
  box.querySelectorAll('[data-cyc]').forEach(function(a){
    a.addEventListener('click',function(){
      var n=parseInt(a.getAttribute('data-cyc'),10);
      cycSel=(cycSel===n?-1:n); view='cycles'; sel=-1; cyclesView(); apply();
    });
  });
  box.querySelectorAll('[data-view]').forEach(function(a){
    a.addEventListener('click',function(){
      view=a.getAttribute('data-view'); cycSel=-1; sel=-1; select(-1);
    });
  });
}

function pairLine(id){
  return '<span class="lnk" data-goto="'+esc(id)+'">'+esc(id)+'</span>';
}

function cyclesView(){
  var c1=D.counts.cyclePairs, c2=D.counts.cycleSccs, ca=D.counts.cyclesAmbiguous;
  var h='<h2>Cycles <span class="lnk" data-view="problems" style="float:right;font-size:11px;'+
        'text-transform:none;letter-spacing:0">overview</span></h2>';
  h+='<div class="muted">Two shapes, and they cost different amounts. A <b>mutual pair</b> is two '+
     'files that reference each other — often deliberate, a parent and the component it owns. An '+
     '<b>SCC</b> is three or more files no single removal separates: none of them can be read, '+
     'ported or tested without the others.</div>';
  h+='<div class="muted" style="margin-top:6px"><b>Ambiguity is kept out.</b> A call to a name '+
     'declared in more than one file is drawn to every candidate, so it can manufacture a cycle '+
     'that does not exist. Components are computed over <b>unambiguous edges only</b>; anything '+
     'that exists only through an ambiguous edge is listed at the bottom under '+
     '<b>ambiguity-dependent</b> and is counted nowhere else.</div>';
  h+='<div style="margin-top:8px"><span class="tag cyc">'+c2+' SCC of 3+</span>'+
     '<span class="tag cyc">'+c1+' 2-file component</span>'+
     '<span class="tag">'+D.counts.mutual+' mutual pairs in total</span>'+
     '<span class="tag">'+ca+' ambiguity-dependent</span></div>';

  D.cycles.forEach(function(c,i){
    if(c.basis!=='certain') return;
    h+='<h2>'+(c.shape==='pair'?'Mutual pair':'SCC')+' · '+c.size+' files</h2>';
    h+='<div class="sym"><span class="lnk tag cyc" data-cyc="'+i+'">'+
       (cycSel===i?'showing only this component':'show only this component')+'</span>'+
       '<span class="tag">layers '+esc(c.layers.join(', '))+'</span>'+
       '<span class="tag">edge kinds '+esc(c.kinds.join(', '))+'</span></div>';
    h+='<div class="sym"><span class="k">edges that close it</span><br><span class="muted">'+
       (c.cut.length? c.cut.length+' <b>cut link'+(c.cut.length===1?'':'s')+'</b> — removing one '+
         'of these on its own breaks the component apart. Closing kinds: <b>'+
         esc(c.closingKinds.join(', '))+'</b>. An <span class="mono">inherit</span> closes a cycle '+
         'no wrapper can open; a <span class="mono">type</span> is usually one Cast away from '+
         'gone.'
        : 'No single link cuts it. Every removal leaves it still strongly connected, so it needs '+
          'at least two.')+'</span></div>';
    (c.cut.length?c.cut:[]).forEach(function(p){
      var L=null;
      c.links.forEach(function(l){if(l.s===p[0]&&l.t===p[1])L=l;});
      h+='<div class="sym">'+pairLine(p[0])+' <span class="muted">&rarr;</span> '+pairLine(p[1])+
         ' <span class="tag cut">cut</span>'+
         (L?'<span class="tag">'+esc(L.kinds.join(', '))+' ×'+L.n+'</span>'+
             (L.up?'<span class="tag up">upward</span>':'')+
             '<div class="muted mono">'+esc(L.syms.join(', '))+'</div>':'')+'</div>';
    });
    h+='<details><summary>all '+c.links.length+' links inside this component</summary>';
    c.links.forEach(function(l){
      h+='<div class="sym">'+pairLine(l.s)+' <span class="muted">&rarr;</span> '+pairLine(l.t)+
         ' <span class="tag">'+esc(l.kinds.join(', '))+' ×'+l.n+'</span>'+
         (l.cut?'<span class="tag cut">cut</span>':'')+
         (l.up?'<span class="tag up">upward</span>':'')+
         (l.syms.length?'<div class="muted mono">'+esc(l.syms.join(', '))+'</div>':'')+'</div>';
    });
    h+='</details>';
    h+='<details><summary>'+c.size+' member files</summary>';
    c.members.forEach(function(m){h+='<div class="sym">'+pairLine(m)+'</div>';});
    h+='</details>';
  });

  var mc=D.mutual.filter(function(p){return p.basis==='certain';});
  h+='<h2>Mutual pairs ('+mc.length+')</h2><div class="muted">Both directions exist between these '+
     'two files. Listed whether or not a larger SCC already swallows them — on this tree most of '+
     'them do sit inside one.</div>';
  h+='<details><summary>list all</summary>';
  mc.forEach(function(p){
    h+='<div class="sym">'+pairLine(p.a)+' <span class="muted">&harr;</span> '+pairLine(p.b)+
       ' <span class="tag cyc">'+esc(p.kinds.join(', '))+'</span>'+
       '<div class="muted mono">&rarr; '+esc(p.ab.kinds.join(','))+' ×'+p.ab.n+
       ' · &larr; '+esc(p.ba.kinds.join(','))+' ×'+p.ba.n+'</div></div>';
  });
  h+='</details>';

  var amb=D.cycles.filter(function(c){return c.basis!=='certain';});
  var ambP=D.mutual.filter(function(p){return p.basis!=='certain';});
  h+='<h2>Ambiguity-dependent ('+amb.length+' component'+(amb.length===1?'':'s')+', '+
     ambP.length+' pair'+(ambP.length===1?'':'s')+')</h2>';
  h+='<div class="muted">These exist only because a call name is declared in more than one file '+
     'and the edge is drawn to every candidate. Each one may be an artifact. They are reported '+
     'here and counted nowhere else.</div>';
  amb.forEach(function(c,i){
    var idx=D.cycles.indexOf(c);
    h+='<div class="sym"><span class="lnk tag cyc" data-cyc="'+idx+'">'+c.size+
       ' files</span><span class="tag">'+esc(c.kinds.join(', '))+'</span>'+
       '<div class="muted mono">'+esc(c.members.join(', '))+'</div></div>';
  });
  ambP.forEach(function(p){
    h+='<div class="sym">'+pairLine(p.a)+' <span class="muted">&harr;</span> '+pairLine(p.b)+
       ' <span class="tag">'+esc(p.kinds.join(', '))+'</span></div>';
  });

  var box=document.getElementById('detail');
  box.innerHTML=h;
  wire(box);
}

function select(i){
  sel=i;
  if(i>=0){detail(i);location.hash=D.nodes[i].id;}
  else{
    if(view==='cycles')cyclesView(); else problems();
    if(location.hash)history.replaceState(null,'',location.pathname);
  }
  apply();
}

// ---- pan / zoom -------------------------------------------------------------------------------
var vp=document.getElementById('vp'), stage=document.getElementById('stage');
var tx=0,ty=0,k=1;
function xform(){vp.setAttribute('transform','translate('+tx+','+ty+') scale('+k+')');}
function centre(i){
  var n=D.nodes[i], r=stage.getBoundingClientRect();
  tx=r.width/2-(n.x+NODE_W/2)*k; ty=r.height/2-(n.y+NODE_H/2)*k; xform();
}
stage.addEventListener('wheel',function(e){
  e.preventDefault();
  var r=stage.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
  var f=Math.exp(-e.deltaY*0.0015), nk=Math.min(4,Math.max(0.15,k*f));
  tx=mx-(mx-tx)*(nk/k); ty=my-(my-ty)*(nk/k); k=nk; xform();
},{passive:false});
var down=null;
stage.addEventListener('mousedown',function(e){down={x:e.clientX,y:e.clientY,tx:tx,ty:ty};
  stage.classList.add('drag');});
window.addEventListener('mousemove',function(e){
  if(!down)return; tx=down.tx+(e.clientX-down.x); ty=down.ty+(e.clientY-down.y); xform();});
window.addEventListener('mouseup',function(e){
  if(down&&Math.abs(e.clientX-down.x)<3&&Math.abs(e.clientY-down.y)<3&&
     e.target.id==='svg') select(-1);
  down=null; stage.classList.remove('drag');});
document.getElementById('reset').addEventListener('click',function(){
  var r=stage.getBoundingClientRect();
  k=Math.min(1,Math.min(r.width/(D.w+40),r.height/(D.h+40))); tx=12; ty=12; xform();});

// ---- wiring -----------------------------------------------------------------------------------
['f-cross','f-up','f-amb','f-focus','f-nc','f-cyc','f-cut'].forEach(function(id){
  document.getElementById(id).addEventListener('change',apply);});
document.getElementById('p-cyc').addEventListener('click',function(){
  view='cycles'; cycSel=-1; select(-1);});
document.getElementById('q').addEventListener('input',apply);
window.addEventListener('keydown',function(e){
  if(e.key==='/'&&document.activeElement.id!=='q'){e.preventDefault();
    document.getElementById('q').focus();}
  if(e.key==='Escape'){document.getElementById('q').value='';select(-1);
    document.getElementById('q').blur();}
  if(e.key==='Enter'&&document.activeElement.id==='q'){
    var q=query();
    for(var j=0;j<D.nodes.length;j++) if(matches(D.nodes[j],q)){select(j);centre(j);break;}
  }
});
document.getElementById('s-files').textContent=D.counts.files;
document.getElementById('s-edges').textContent=D.counts.edges;
document.getElementById('s-cross').textContent=D.counts.cross;
document.getElementById('s-up').textContent=D.counts.up;
document.getElementById('s-cyc').textContent=D.counts.cycleNodes+' files';
document.getElementById('s-nc').textContent=D.counts.noCallerRefl+D.counts.noCallerUnrefl;
(function(){
  var r=stage.getBoundingClientRect();
  k=Math.min(1,Math.min(r.width/(D.w+40),r.height/(D.h+40))); tx=12; ty=12; xform();
})();
var frag=decodeURIComponent(location.hash.replace(/^#/,''));
var start=-1;
if(frag) for(var j=0;j<D.nodes.length;j++) if(D.nodes[j].id===frag) start=j;
select(start);
if(start>=0) centre(start);
</script>
</body></html>
"""


def render(data):
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).replace("<", "\\u003c")
    project = (PROJECT_NAME.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    html = HTML.replace("__PROJECT__", project, 1)
    html = html.replace("__DATA__", payload)
    for token, value in (("__NODE_W__", NODE_W), ("__NODE_H__", NODE_H), ("__COL_W__", COL_W),
                         ("__HEAD_H__", HEAD_H), ("__PAD_Y__", PAD_Y)):
        html = html.replace(token, str(value))
    return html


# =================================================================================================
# CLAIM BOARD
#
# `--apply` writes every file under `Script/` in one pass. An agent mid-edit on any of them loses
# that edit. The claim board (the `claimsFile` setting) records who holds what, and this reads it
# so `--apply` can skip held files instead of overwriting them.
#
# AN AGENT'S CURRENT STATE IS ITS LAST BLOCK BY FILE POSITION. The board is append-only, timestamps
# are self-reported, and clocks can be skewed - a superseded ACTIVE block can carry a stamp LATER
# than the DONE that released it. Nothing below reads, sorts or compares a timestamp.
#
# EVERY SHAPE IT CANNOT READ RESOLVES TO HELD:
#
#   header with no status word              held   `## bp-widgets - timestamp correction to the
#                                                    block immediately above`
#   header whose first field is not an      held under a synthetic id IF the block lists exclusive
#     agent id                                     paths; ignored as a note if it lists none
#                                                  (`## GHOST REAPING - agent-3, ...`)
#   live block with no `Exclusive paths`    held on every path-like token in the block
#     section
#   unbalanced ``` fence                    the whole file is re-read with fence handling off,
#                                           which can only ADD blocks, never drop one
#
# A claim wrongly kept costs a message. A claim wrongly dropped costs an asset.
#
# A header such as `## agent-7-B (4 subagents) - ACTIVE - ...` has text between the id and the
# dash. The parenthetical is stripped here before the id is validated.
# =================================================================================================
CLAIMS_PATH = _project_path(CFG.get("claimsFile", ".claude/coordination/CLAIMS.md"))
MESSAGES_FILE = CFG.get("messagesFile", ".claude/coordination/MESSAGES.md")

# Small vocabulary on purpose. A word outside it is UNKNOWN, and UNKNOWN is HELD. Adding loose words
# such as `HOLD` would match the prose `I hold nothing` that ends most release blocks.
CLAIM_RELEASED = ("DONE", "RELEASED", "CLOSED", "REAPED")
CLAIM_HELD = ("ACTIVE", "BLOCKED", "PAUSED")

_CLAIM_STATUS_RE = re.compile(r'\b(' + "|".join(CLAIM_RELEASED + CLAIM_HELD) + r')\b')
# Header fields are separated by an em/en dash, by a spaced ASCII dash, or by a spaced `?`. A
# writer whose stdout cannot encode U+2014 puts a literal `?` where the em dash was. `->` in
# `23:52 -> 00:15` does not match, because the ASCII branch needs whitespace on BOTH sides.
_CLAIM_SEP_RE = re.compile('\\s*[\u2014\u2013]\\s*|\\s+--\\s+|\\s+-\\s+|\\s+\\?\\s+')
_CLAIM_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]*$')
_CLAIM_PATHS_RE = re.compile(r'^\s*\*\*\s*Exclusive paths\s*:?\s*\*\*\s*:?\s*(.*)$', re.I)
_CLAIM_LABEL_RE = re.compile(r'^\s*\*\*[^*]')
_CLAIM_BULLET_RE = re.compile(r'^\s*[-*+]\s+(.*)$')
_CLAIM_TRAIL_PAREN_RE = re.compile(r'\s*\([^)]*\)\s*$')
# Path-like tokens, used only as the fallback for a live block that declares no paths section.
_CLAIM_PATHLIKE_RE = re.compile(
    r'\b(?:Script|Content|Source|Config|Plugins|tools|Docs)(?:/[\w.+\-]+)*/?')


class ClaimHold(object):
    """One agent that currently holds paths, with the block position that decided it."""

    __slots__ = ("agent", "status", "line", "paths", "inferred")

    def __init__(self, agent, status, line, paths, inferred):
        self.agent = agent
        self.status = status          # None when the header carried no status word
        self.line = line              # 1-based line of the deciding `## ` header
        self.paths = paths
        self.inferred = inferred      # paths came from path-like tokens, not an Exclusive section


def _claim_blocks(text):
    """(line_no, header_line, body_lines) per `## ` block, fenced code blocks skipped.

    Returns (blocks, warnings). An unbalanced fence makes the fence state meaningless, so the file
    is re-read with fence handling off - that can only add blocks, and a block added is a hold kept.
    """
    lines = text.splitlines()
    warnings = []
    for honour_fences in (True, False):
        fence = False
        starts = []
        for i, ln in enumerate(lines):
            stripped = ln.lstrip()
            if honour_fences and (stripped.startswith("```") or stripped.startswith("~~~")):
                fence = not fence
                continue
            if not fence and ln.startswith("## "):
                starts.append(i)
        if honour_fences and fence:
            warnings.append("the board has an unbalanced ``` fence; re-read with fence handling "
                            "OFF, so fenced example blocks are treated as real claims")
            continue
        blocks = []
        for k, i in enumerate(starts):
            j = starts[k + 1] if k + 1 < len(starts) else len(lines)
            blocks.append((i + 1, lines[i][3:], lines[i + 1:j]))
        return blocks, warnings
    return [], warnings


def _claim_head(head):
    """(agent id or raw first field, status word or None) from a header line without its `## `."""
    parts = [p for p in _CLAIM_SEP_RE.split(head) if p.strip()]
    agent = (parts[0] if parts else "").strip().strip("`*").strip()
    agent = _CLAIM_TRAIL_PAREN_RE.sub("", agent).strip()
    status = None
    if len(parts) > 1:
        cand = parts[1].split("(")[0].strip().strip("`*").upper()
        m = _CLAIM_STATUS_RE.match(cand)
        if m:
            status = m.group(1)
    if status is None:
        m = _CLAIM_STATUS_RE.search(head.upper())
        if m:
            status = m.group(1)
    return agent, status


def _is_agent_id(s):
    """Is this header's first field an agent id rather than a section keyword?

    `## RETRACTION - agent-3, ...` and `## GHOST REAPING - ...` are coordinator notes. The
    multi-word ones fail the id pattern already; `RETRACTION` does not, so one more test: an
    ALL-CAPS single word carrying no digit and no hyphen is a keyword. An agent id is lowercase,
    or it carries hyphens and digits (`agent-7-B`).

    A wrong answer matters only for a block that lists NO exclusive paths, since a keyword block
    with paths is held under a synthetic id either way.
    """
    if not _CLAIM_ID_RE.match(s or ""):
        return False
    return not (s.isupper() and "-" not in s and not any(c.isdigit() for c in s))


def _clean_claim_path(raw):
    """One `- <path>` entry reduced to a path, or "" if nothing path-shaped survives."""
    # The trailing parenthetical goes FIRST: `Script/  (the whole tree - the write is tree-wide)`
    # carries a dash inside it, and splitting on separators first leaves an unbalanced `(` behind.
    s = _CLAIM_TRAIL_PAREN_RE.sub("", raw.strip())
    s = _CLAIM_SEP_RE.split(s.strip())[0]
    s = _CLAIM_TRAIL_PAREN_RE.sub("", s).strip()
    s = s.strip("`").strip("*_").strip().rstrip(".,;:").strip()
    s = s.replace("\\", "/").lstrip("/")
    if s.lower().startswith("./"):
        s = s[2:]
    root_prefix = ROOT.replace("\\", "/").rstrip("/") + "/"
    if s.lower().startswith(root_prefix.lower()):
        s = s[len(root_prefix):]
    if not s or " " in s:
        return ""
    return s


def _claim_paths(body_lines):
    """(paths, saw_section) for one block's `**Exclusive paths:**` list.

    The list ends at the next `**Label:**`, which is what keeps `**Not touching:**` out of it.
    """
    paths, saw = [], False
    i = 0
    while i < len(body_lines):
        m = _CLAIM_PATHS_RE.match(body_lines[i])
        if not m:
            i += 1
            continue
        saw = True
        inline = _clean_claim_path(m.group(1))
        if inline:
            paths.append(inline)
        i += 1
        while i < len(body_lines):
            ln = body_lines[i]
            if not ln.strip():
                i += 1
                continue
            if _CLAIM_LABEL_RE.match(ln):
                break
            b = _CLAIM_BULLET_RE.match(ln)
            if not b:
                break
            p = _clean_claim_path(b.group(1))
            if p:
                paths.append(p)
            i += 1
    return paths, saw


def resolve_claims(text):
    """(holds, warnings) - every agent whose LAST block by POSITION is not a release."""
    blocks, warnings = _claim_blocks(text)
    parsed = []
    for line_no, head, body in blocks:
        agent, status = _claim_head(head)
        paths, saw = _claim_paths(body)
        body_text = "\n".join(body)
        if not _is_agent_id(agent):
            if not (paths or saw):
                continue                       # a coordinator note, not a claim block
            agent = "<unparsed-header@line %d>" % line_no
            status = None
            warnings.append("line %d: header does not start with an agent id and the block lists "
                            "exclusive paths - treated as HELD: %s" % (line_no, head[:90]))
        parsed.append((agent, status, line_no, paths, saw, body_text))

    by_agent = {}
    for rec in parsed:
        by_agent.setdefault(rec[0], []).append(rec)

    holds = []
    for agent in sorted(by_agent):
        runs = by_agent[agent]
        last_released = -1
        for idx, rec in enumerate(runs):
            if rec[1] in CLAIM_RELEASED:
                last_released = idx
        if last_released == len(runs) - 1:
            continue                            # released by its own last block
        live = runs[last_released + 1:]
        paths, saw_any = [], False
        for _a, _s, _ln, p, saw, _b in live:
            paths.extend(p)
            saw_any = saw_any or saw
        inferred = False
        if not saw_any:
            inferred = True
            for _a, _s, _ln, _p, _saw, body_text in live:
                for m in _CLAIM_PATHLIKE_RE.finditer(body_text):
                    p = _clean_claim_path(m.group(0))
                    if p:
                        paths.append(p)
            warnings.append("%s (line %d): live block declares no `**Exclusive paths:**` section - "
                            "every path-like token in it is treated as HELD"
                            % (agent, live[-1][2]))
        if live[-1][1] is None:
            warnings.append("%s (line %d): header carries no status word - treated as HELD"
                            % (agent, live[-1][2]))
        seen, ordered = set(), []
        for p in paths:
            if p.lower() not in seen:
                seen.add(p.lower())
                ordered.append(p)
        holds.append(ClaimHold(agent, live[-1][1], live[-1][2], ordered, inferred))
    return holds, warnings


def claim_matches(claim, filekey):
    """Does one claim string cover `filekey` (posix, relative to the repo root)?

    Handles an exact file, a directory with or without a trailing slash, `Script/` as a whole,
    a glob, and a bare `Foo.as` basename.
    """
    if not claim:
        return False
    c, f = claim.lower().rstrip("/"), filekey.lower()
    if not c:
        return False
    if "*" in c or "?" in c:
        return fnmatch.fnmatch(f, c) or fnmatch.fnmatch(f, c + "/*")
    if c == f or f.startswith(c + "/"):
        return True
    if "/" not in c and c.endswith(".as"):
        return os.path.basename(f) == c
    return False


def claim_key(path, script_root, relid):
    """The path a claim would be written as: relative to the repo root, posix separators.

    A `--script-root` outside the repo (the self-test's temp copy) falls back to that root's own
    directory name, which is `Script`, so a fixture claim reads exactly like a real one.
    """
    try:
        k = os.path.relpath(os.path.abspath(path), ROOT).replace("\\", "/")
    except ValueError:
        k = None
    if not k or k.startswith(".."):
        k = os.path.basename(os.path.abspath(script_root).rstrip("\\/")) + "/" + relid
    return k


def load_claims(claims_path):
    """(holds, warnings, error). `error` is set when the board itself could not be read."""
    if not os.path.isfile(claims_path):
        return [], [], "claim board not found at %s" % claims_path
    try:
        with io.open(claims_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception as exc:                                 # noqa: BLE001
        return [], [], "claim board unreadable (%s: %s)" % (type(exc).__name__, exc)
    holds, warnings = resolve_claims(text)
    return holds, warnings, None


# =================================================================================================
# HEADER BLOCKS
#
# WHAT: each `.as` file gets a generated list of the project files it references.
#
# WHERE: the very TOP of the file, then one blank line, then whatever was there before:
#
#   1. It cannot come between a doc comment and the symbol that doc comment describes. A detached
#      doc comment is a defect.
#   2. It is `//` line comments, never `/** */`, so it can never form the doc-doc pair that
#      `comment_census.py --stacked` reports.
#   3. Byte offset 0 is the one position that does not move when the file is edited below it, so
#      the strip-and-reinsert is exact.
#
# The block carries no date, no agent id, no tick number and no pointer into documentation, because
# `comment_census.py`'s marker patterns score those as narration.
#
# IDEMPOTENT BY CONSTRUCTION: every BEGIN..END region anywhere in the file is stripped first, then
# exactly one fresh block is inserted. A rerun with no source change leaves every file
# byte-identical, which the self-test proves. Line endings and encoding are preserved per file: the
# block is written with the file's own dominant newline. The `headerNote` setting is its second line.
# =================================================================================================
HDR_BEGIN = "// --- BEGIN SCRIPT-GRAPH DEPENDS ---"
HDR_END = "// --- END SCRIPT-GRAPH DEPENDS ---"
HDR_BEGIN_RE = re.compile(r'^[ \t]*//[ \t]*-{2,}[ \t]*BEGIN SCRIPT-GRAPH DEPENDS')
HDR_END_RE = re.compile(r'^[ \t]*//[ \t]*-{2,}[ \t]*END SCRIPT-GRAPH DEPENDS')
HDR_NOTE = CFG.get("headerNote",
                   "// Generated. Regenerate with: script_graph.py --write-headers --apply")


def header_lines(g, fid, cycles, mutual):
    """The block for one file, as a list of lines without their newline."""
    deps = {}
    for (s, t, kind), rec in g.edges.items():
        if s != fid:
            continue
        d = deps.setdefault(t, {"kinds": [], "syms": set(), "amb": True})
        if kind not in d["kinds"]:
            d["kinds"].append(kind)
        d["syms"].update(rec["syms"])
        d["amb"] = d["amb"] and rec["amb"]

    mutual_with = set()
    for p in mutual:
        if p["a"] == fid:
            mutual_with.add(p["b"])
        elif p["b"] == fid:
            mutual_with.add(p["a"])
    comp = None
    for c in cycles:
        if fid in c["members"]:
            comp = c
            break

    out = [HDR_BEGIN, HDR_NOTE]
    if not deps:
        out.append("// References no other file in Script/.")
    else:
        # The file list only. Symbols, edge kinds and cycle membership are in the HTML graph, so
        # this block stays short enough to read past.
        line = "// References:"
        for t in sorted(deps):
            add = (" " if line.endswith(":") else ", ") + t
            if len(line) + len(add) > 108:
                out.append(line + ",")
                line = "//   " + t
            else:
                line += add
        out.append(line)
    out.append(HDR_END)
    return out


def _kinds_ordered(kinds):
    """Edge kinds in a fixed order, so the block does not churn on dict ordering."""
    return [k for k in ("inherit", "member", "call", "event", "type") if k in kinds]


def _dominant_newline(text):
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def strip_header(text):
    """Remove EVERY generated block, plus the single blank line each one is followed by.

    Returns (text, removed_count, unclosed) - `unclosed` names a BEGIN with no END, which is the one
    shape this refuses to touch rather than guess at.
    """
    lines = text.splitlines(True)
    out, i, removed, unclosed = [], 0, 0, False
    while i < len(lines):
        if HDR_BEGIN_RE.match(lines[i]):
            j = i + 1
            while j < len(lines) and not HDR_END_RE.match(lines[j]):
                if HDR_BEGIN_RE.match(lines[j]):
                    break
                j += 1
            if j >= len(lines) or not HDR_END_RE.match(lines[j]):
                unclosed = True
                out.append(lines[i])
                i += 1
                continue
            i = j + 1
            if i < len(lines) and lines[i].strip() == "":
                i += 1
            removed += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out), removed, unclosed


def render_headers(g, script_root):
    """(path, old_bytes, new_bytes, note) for every file whose block would change."""
    cycles = find_cycles(g)
    mutual = find_mutual_pairs(g, cycles)
    changes, refused = [], []
    for f in g.files:
        path = os.path.join(script_root, f.id.replace("/", os.sep))
        try:
            raw = open(path, "rb").read()
        except Exception as exc:                             # noqa: BLE001
            refused.append("%s: unreadable: %s" % (f.id, exc))
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            refused.append("%s: not valid UTF-8 (%s) - refusing to rewrite it" % (f.id, exc))
            continue
        body, removed, unclosed = strip_header(text)
        if unclosed:
            refused.append("%s: a BEGIN marker with no END - refusing to rewrite it" % f.id)
            continue
        nl = _dominant_newline(text)
        block = nl.join(header_lines(g, f.id, cycles, mutual)) + nl + nl
        new = block + body
        if new != text:
            changes.append((path, f.id, text, new, "replaced" if removed else "added"))
    return changes, refused


# =================================================================================================
# SELF-TEST
#
# Two halves, and both must pass or nothing is written.
#
#   FACTS   statements about the live tree, each checkable by hand from the paths named. They are
#           the `selfTest.facts` setting, then two checks that hold for every tree.
#   PLANTS  a COPY of `Script/` with one extra file carrying a known event with no broadcast, a
#           reflected function with no caller, an unreflected function with no caller, a nested
#           `meta=(DisplayName=...)` specifier, an inheritance edge and a cross-layer call. If the
#           parser cannot reproduce those, its clean sheet on the real tree means nothing.
#
# The plants need three settings of `selfTest`: `plantLayer` (a layer directory the plants go
# into), `plantBase` (`{"class", "file"}`: a script class the plant derives from, and its file) and
# `plantService` (`{"class", "file"}`: a script class with a static `Get()` that the plant holds and
# calls, in another layer). `minFiles` is the least number of files the planted copy must hold.
# Without the three, the self-test says so and exits 0.
#
# The copy is written to a temp directory. `Script/` itself is never touched - an `.as` write
# hot-reloads the whole layer for every agent sharing the editor.
#
# The claim plants use a FIXTURE board in the same temp directory, never the live one: a plant that
# depended on what the agents happen to hold would pass or fail by accident. They cover every
# header shape the parser must read, including the two that decide correctness - a DONE carrying an
# EARLIER stamp than the ACTIVE it releases, and an id with a parenthetical before the dash.
# =================================================================================================
ST = CFG.get("selfTest", {}) or {}
PLANT_LAYER = ST.get("plantLayer", "")
PLANT_BASE = ST.get("plantBase", {}) or {}
PLANT_SERVICE = ST.get("plantService", {}) or {}
MIN_FILES = int(ST.get("minFiles", 1))
P_ = PLANT_LAYER + "/"

PLANT_FILE = P_ + "__selftest_plant.as"
PLANT_SRC = '''// Self-test plant. Written into a COPY of Script/ by script_graph.py --self-test.
event void FSelfTestNeverBroadcast();

UCLASS()
class USelfTestPlant : @BASE@
{
\tUPROPERTY()
\tFSelfTestNeverBroadcast OnNever;

\tUPROPERTY(EditAnywhere, meta=(DisplayName="Plant Member"))
\t@SERVICE@ PlantSub;

\tUFUNCTION(BlueprintPure, meta=(DisplayName="Plant Nested Spec"))
\tbool PlantReflectedNoCaller() const
\t{
\t\treturn false;
\t}

\tvoid PlantUnreflectedNoCaller()
\t{
\t}

\tUFUNCTION(BlueprintOverride)
\tvoid PlantEntryPoint()
\t{
\t\t@SERVICE@ sub = @SERVICE@::Get();
\t}
}
'''.replace("@BASE@", PLANT_BASE.get("class", "")).replace("@SERVICE@", PLANT_SERVICE.get("class", ""))

# Cycle plants. Four files that must form exactly two certain components and one ambiguity-only
# component, none of them touching a component of the real tree:
#
#   PAIR      A --member--> B, B --type--> A                     -> certain, size 2
#   TRIANGLE  T1 -> T2 -> T3 -> T1, all type edges               -> certain, size 3
#   AMBIGUITY X --member--> Y unambiguously, and Y calls a name declared in BOTH X and Z, so the
#             return edge Y -> X exists only as an ambiguous call        -> ambiguous, size 2
#
# The classes derive from nothing and hold no engine type, so they cannot be dragged into any
# existing component by an incidental edge.
# Name plants. Two files declare a property called `SharedSignal`, each of a different event type.
# One is broadcast and subscribed in its own file; the other is touched nowhere. A census keyed by
# the bare property name credits one event with the other's wiring, and the unwired half then reads
# as wired.
NAME_PLANTS = {
    P_ + "__selftest_shared_a.as": '''
event void FSelfTestSharedWired();

class USelfTestSharedHolderA
{
	UPROPERTY()
	FSelfTestSharedWired SharedSignal;

	UFUNCTION()
	void SelfTestSharedHandler()
	{
	}

	UFUNCTION(BlueprintOverride)
	void SelfTestSharedEntry()
	{
		SharedSignal.AddUFunction(this, n"SelfTestSharedHandler");
		SharedSignal.Broadcast();
	}
}
''',
    P_ + "__selftest_shared_b.as": '''
event void FSelfTestSharedUnwired();

class USelfTestSharedHolderB
{
	UPROPERTY()
	FSelfTestSharedUnwired SharedSignal;
}
''',
}


CYCLE_PLANTS = {
    P_ + "__selftest_pair_a.as": '''
class USelfTestPairA
{
\tUSelfTestPairB PairB;
}
''',
    P_ + "__selftest_pair_b.as": '''
class USelfTestPairB
{
\tvoid PairBTouch()
\t{
\t\tUSelfTestPairA back;
\t}
}
''',
    P_ + "__selftest_tri_1.as": '''
class USelfTestTri1
{
\tvoid Tri1Touch()
\t{
\t\tUSelfTestTri2 next;
\t}
}
''',
    P_ + "__selftest_tri_2.as": '''
class USelfTestTri2
{
\tvoid Tri2Touch()
\t{
\t\tUSelfTestTri3 next;
\t}
}
''',
    P_ + "__selftest_tri_3.as": '''
class USelfTestTri3
{
\tvoid Tri3Touch()
\t{
\t\tUSelfTestTri1 next;
\t}
}
''',
    P_ + "__selftest_amb_x.as": '''
class USelfTestAmbX
{
\tUSelfTestAmbY AmbY;

\tvoid SelfTestAmbShared()
\t{
\t}
}
''',
    P_ + "__selftest_amb_y.as": '''
class USelfTestAmbY
{
\tvoid AmbYCall()
\t{
\t\tSelfTestAmbShared();
\t}
}
''',
    P_ + "__selftest_amb_z.as": '''
class USelfTestAmbZ
{
\tvoid SelfTestAmbShared()
\t{
\t}
}
''',
}

# Claim-board fixture. Written into the temp directory, never read from the live board: a plant that
# depended on what the agents happen to hold would pass or fail by accident.
#
# Each block is one header shape the parser must read. The two that matter most are
# `fixture-superseded`, whose DONE carries an EARLIER stamp than the ACTIVE it releases, and
# `fixture-untidy (4 subagents)`, which has text between the id and the dash.
FIXTURE_CLAIMS = """<!-- self-test fixture -->
# Claim board

Template, inside a fence, and it must be IGNORED:

```markdown
## <agent-id> — ACTIVE — started <YYYY-MM-DD HH:MM>
**Exclusive paths:**
- Script/@P@/__selftest_fenced.as
```

## fixture-live — ACTIVE — started 2000-01-01 23:55 — heartbeat 2000-01-01 23:55
**Doing:** holding one file.
**Exclusive paths:**
- Script/@P@/__selftest_pair_a.as
**Shared resources:** angelscript-reload
**Not touching:** Script/@P@/__selftest_amb_z.as

## fixture-released — ACTIVE — started 2000-01-01 22:00 — heartbeat 2000-01-01 22:00
**Exclusive paths:**
- Script/@P@/__selftest_pair_b.as

## fixture-released — DONE — 2000-01-01 22:00 -> 22:40 — **RELEASED. I hold nothing.**

## fixture-superseded — ACTIVE — started 2000-01-02 05:00 — heartbeat 2000-01-02 05:00
**Exclusive paths:**
- Script/@P@/__selftest_tri_1.as

## fixture-superseded - DONE - 2000-01-01 23:20 (this stamp is EARLIER than the ACTIVE block above; position decides) - **RELEASED.**

## fixture-untidy (4 subagents) — ACTIVE — started 2000-01-01 23:58 — heartbeat 2000-01-01 23:58
**Exclusive paths:**
- Script/@P@/__selftest_tri_2.as   (agent 1)

## fixture-nostatus — timestamp correction to the block immediately above
**Exclusive paths:**
- Script/@P@/__selftest_tri_3.as

## fixture-reclaimed — DONE — 2000-01-01 22:00 — **RELEASED.**
**Exclusive paths:**
- Script/@P@/__selftest_amb_x.as

## fixture-reclaimed — ACTIVE — started 2000-01-01 22:30 — heartbeat 2000-01-01 22:30
**Exclusive paths:**
- Script/@P@/__selftest_amb_x.as

## fixture-inline — ACTIVE — started 2000-01-01 23:59 — heartbeat 2000-01-01 23:59
Same single path, same scope: `Script/@P@/__selftest_amb_y.as`. No paths section at all.

## fixture-mojibake ? ACTIVE ? started 2000-01-01 23:57 ? heartbeat 2000-01-01 23:57
**Exclusive paths:**
- Script/@P@/__selftest_plant.as

## GHOST REAPING — coordinator, 2000-01-01 23:59. A note, not a claim: no agent id, no paths.
It must be ignored rather than read as an agent called GHOST holding something.

## RETRACTION — coordinator, 2000-01-01 23:59. One ALL-CAPS word, so it passes the id pattern.
A note about Script/@P@/__selftest_amb_z.as, not a claim on it.

## SWEEP — coordinator, 2000-01-01 23:59. An ALL-CAPS keyword that DOES list paths.
**Exclusive paths:**
- Script/@P@/__selftest_sweep_target.as
""".replace("@P@", PLANT_LAYER)

# A second fixture: one agent holding `Script/` whole, the claim that a tree-wide write needs.
FIXTURE_CLAIMS_TREE = """# Claim board

## fixture-tree — ACTIVE — started 2000-01-01 23:58 — heartbeat 2000-01-01 23:58
**Exclusive paths:**
- Script/  (the whole tree — the write is tree-wide and cannot be scoped per file)
**Shared resources:** angelscript-reload
""".replace("@P@", PLANT_LAYER)

FIXTURE_HELD = [
    P_ + "__selftest_pair_a.as",     # plain ACTIVE
    P_ + "__selftest_tri_2.as",      # untidy header, `(4 subagents)` before the dash
    P_ + "__selftest_tri_3.as",      # no status word in the header
    P_ + "__selftest_amb_x.as",      # DONE then ACTIVE again - position, not time
    P_ + "__selftest_amb_y.as",      # live block with no Exclusive paths section
    P_ + "__selftest_plant.as",      # separator mangled from an em dash to `?`
]
FIXTURE_FREE = [
    P_ + "__selftest_pair_b.as",     # released by a later DONE
    P_ + "__selftest_tri_1.as",      # released by a DONE carrying an EARLIER stamp
    P_ + "__selftest_amb_z.as",      # named under `**Not touching:**`, not claimed
]


class _Args(object):
    """The argparse namespace `write_headers` reads, built by hand for the self-test."""

    def __init__(self, **kw):
        self.script_root = kw["script_root"]
        self.apply = kw.get("apply", False)
        self.show = kw.get("show", 0)
        self.claims = kw["claims"]
        self.ignore_claims = kw.get("ignore_claims", False)
        self.as_agent = kw.get("as_agent", None)


def _run_write_headers(**kw):
    """(return code, captured stdout). Captured so the self-test's own output stays readable."""
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = write_headers(_Args(**kw))
    return rc, buf.getvalue()


def _fact(g, f):
    """One fact of the `selfTest.facts` setting, checked against the graph.

    Each fact is an object with a `label` (the line the self-test prints), a `check` and the
    parameters of that check:

        edge                  src, dst, kind    the edge exists
        eventHeldAndBroadcast event, file       the event is declared in `file`, held and broadcast
        eventBroadcastFrom    event, from, holder  an event edge `from` -> `holder` exists, and the
                                                event is broadcast in `from`
        callsInto             file, min         at least `min` call edges end in `file`
        reachable             file, func        the function is not reported callerless
        specHas               file, func, word  the function's specifier holds `word`
        notTypes              names             none of the names became a node
        reachesDirs           file, min         `file` has edges into at least `min` top directories
        sameComponent         files             the files sit in one certain component
        mutualPair            a, b              the two files are a certain mutual pair
    """
    c = f.get("check")
    if c == "edge":
        return (f["src"], f["dst"], f["kind"]) in g.edges
    if c == "eventHeldAndBroadcast":
        ev = f["event"]
        return (g.event_decl.get(ev) == f["file"]
                and g.events[ev]["holders"]
                and g.events[ev]["broadcasts"])
    if c == "eventBroadcastFrom":
        return ((f["from"], f["holder"], "event") in g.edges
                and any(b[0] == f["from"] for b in g.events[f["event"]]["broadcasts"]))
    if c == "callsInto":
        return sum(1 for (s, t, k) in g.edges
                   if t == f["file"] and k == "call") >= int(f.get("min", 1))
    if c == "reachable":
        return _reach_ok(g, f["file"], f["func"])
    if c == "specHas":
        return _spec_has(g, f["file"], f["func"], f["word"])
    if c == "notTypes":
        return not (set(f["names"]) & set(g.type_owner))
    if c == "reachesDirs":
        return len(set(t.split("/")[0] for (s, t, k) in g.edges
                       if s == f["file"])) >= int(f.get("min", 1))
    if c == "sameComponent":
        return any(cy["basis"] == "certain" and all(m in cy["members"] for m in f["files"])
                   for cy in find_cycles(g))
    if c == "mutualPair":
        return any(p["a"] == f["a"] and p["b"] == f["b"] and p["basis"] == "certain"
                   for p in find_mutual_pairs(g, find_cycles(g)))
    raise ValueError("unknown fact check %r" % (c,))


FACTS = [(f.get("label", f.get("check", "?")), (lambda g, f=f: _fact(g, f)))
         for f in (ST.get("facts") or [])]
FACTS += [
    # (label, callable(g) -> bool) - true for every tree
    ("no reported component is a lone file, and no file is claimed by two components",
     lambda g: (all(c["size"] >= 2 for c in find_cycles(g))
                and len([m for c in find_cycles(g) if c["basis"] == "certain"
                         for m in c["members"]])
                == len(set(m for c in find_cycles(g) if c["basis"] == "certain"
                           for m in c["members"])))),
    ("every cut link a component names really does break it: removing it leaves the component no "
     "longer strongly connected",
     lambda g: _cuts_really_cut(g)),
]


def _cuts_really_cut(g):
    """Re-derive every reported cut link from the edge set, independently of how it was found."""
    for c in find_cycles(g):
        allow = c["basis"] == "ambiguous"
        adj = {}
        for (s, t, _k), rec in g.edges.items():
            if rec["amb"] and not allow:
                continue
            adj.setdefault(s, set()).add(t)
        members = c["members"]
        for s, t in c["cut"]:
            trimmed = dict((m, set(adj.get(m, ())) & set(members)) for m in members)
            trimmed[s] = set(x for x in trimmed[s] if x != t)
            if _strongly_connected(members, trimmed):
                return False
        # and a component with a cut link must not be strongly connected without it
        if not _strongly_connected(members,
                                   dict((m, set(adj.get(m, ())) & set(members))
                                        for m in members)):
            return False
    return True


def _reach_ok(g, fid, fname):
    r = reachability(g)
    for key, rec in r.items():
        if key[0] == fid and key[2] == fname:
            return rec["reachable"]
    return False


def _spec_has(g, fid, fname, word):
    for f in g.files:
        if f.id != fid:
            continue
        for fn in f.funcs:
            if fn.name == fname and fn.spec and word in fn.spec:
                return True
    return False


def self_test():
    ok = True
    if not (PLANT_LAYER and PLANT_BASE.get("class") and PLANT_BASE.get("file")
            and PLANT_SERVICE.get("class") and PLANT_SERVICE.get("file")):
        print("SELF-TEST SKIPPED: the project settings name no self-test fixtures. Set "
              "script_graph.selfTest (plantLayer, plantBase, plantService) in "
              ".octopus/ue-angelscript.json.")
        return 0
    print("FACTS - checked against the live Script/ tree, read-only")
    files, unreadable, unknown = collect(SCRIPT_ROOT)
    if unreadable or unknown:
        if unknown:
            print("  FAIL  %d file(s) sit in a directory the LAYERS table does not name. Add the "
                  "directory to LAYERS and run again: %s" % (len(unknown), unknown))
        if unreadable:
            print("  FAIL  the tree did not parse cleanly: %s" % (unreadable,))
        return 1
    g = build(files)
    for label, fn in FACTS:
        try:
            good = bool(fn(g))
        except Exception as exc:                            # noqa: BLE001
            good, label = False, "%s  [%s: %s]" % (label, type(exc).__name__, exc)
        print("  %s  %s" % ("PASS" if good else "FAIL", label))
        ok = ok and good

    print("PLANTS - a COPY of Script/ with known defects injected; Script/ itself is not touched")
    tmp = tempfile.mkdtemp(prefix="script_graph_selftest_")
    try:
        copy = os.path.join(tmp, "Script")
        shutil.copytree(SCRIPT_ROOT, copy)
        plant_path = os.path.join(copy, PLANT_FILE.replace("/", os.sep))
        with io.open(plant_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(PLANT_SRC)
        for rel_, src in sorted(dict(CYCLE_PLANTS, **NAME_PLANTS).items()):
            with io.open(os.path.join(copy, rel_.replace("/", os.sep)), "w",
                         encoding="utf-8", newline="\n") as fh:
                fh.write(src.lstrip("\n"))
        pfiles, punread, punknown = collect(copy)
        if punread or punknown:
            print("  FAIL  the planted copy did not parse: %s %s" % (punread, punknown))
            return 1
        pg = build(pfiles)
        preach = reachability(pg)
        shortfall = sum(f.missed_props + f.missed_funcs for f in pfiles if f.id == PLANT_FILE)

        def _plant_prop(graph, cls, prop):
            for (_fid, owner, name), rec in graph.props.items():
                if owner == cls and name == prop:
                    return rec["state"]
            return None

        def look(name):
            for key, rec in preach.items():
                if key[0] == PLANT_FILE and key[2] == name:
                    return rec
            return None

        checks = [
            ("planted event FSelfTestNeverBroadcast is reported inert",
             pg.events.get("FSelfTestNeverBroadcast", {}).get("state") == "inert"),
            ("planted SharedSignal on holder A, broadcast and subscribed, is reported ok",
             _plant_prop(pg, "USelfTestSharedHolderA", "SharedSignal") == "ok"),
            ("planted SharedSignal on holder B, same NAME and a different event type, is still "
             "reported inert",
             _plant_prop(pg, "USelfTestSharedHolderB", "SharedSignal") == "inert"),
            ("planted reflected PlantReflectedNoCaller: no caller in Script/, reflected",
             bool(look("PlantReflectedNoCaller")) and not look("PlantReflectedNoCaller")["reachable"]
             and look("PlantReflectedNoCaller")["reflected"]),
            ("planted unreflected PlantUnreflectedNoCaller: no caller in Script/, NOT reflected",
             bool(look("PlantUnreflectedNoCaller"))
             and not look("PlantUnreflectedNoCaller")["reachable"]
             and not look("PlantUnreflectedNoCaller")["reflected"]),
            ('planted nested specifier meta=(DisplayName="Plant Nested Spec") survived the parse',
             bool(look("PlantReflectedNoCaller"))
             and "DisplayName" in look("PlantReflectedNoCaller")["spec"]),
            ("planted BlueprintOverride PlantEntryPoint is NOT reported callerless",
             bool(look("PlantEntryPoint")) and look("PlantEntryPoint")["reachable"]),
            ("planted inheritance edge -> %s" % PLANT_BASE["file"],
             (PLANT_FILE, PLANT_BASE["file"], "inherit") in pg.edges),
            ("planted cross-layer call edge -> %s" % PLANT_SERVICE["file"],
             (PLANT_FILE, PLANT_SERVICE["file"], "call") in pg.edges),
            ("planted member edge -> %s" % PLANT_SERVICE["file"],
             (PLANT_FILE, PLANT_SERVICE["file"], "member") in pg.edges),
            ("planted event holder edge is an edge of kind 'event' inside the plant file "
             "(declaration and holder in one file, so no edge) and the event has 1 holder",
             len(pg.events.get("FSelfTestNeverBroadcast", {}).get("holders", [])) == 1),
            ("zero UPROPERTY / UFUNCTION shortfall on the planted file", shortfall == 0),
        ]

        # ---- cycle plants ------------------------------------------------------------------
        pcy = find_cycles(pg)
        pmu = find_mutual_pairs(pg, pcy)

        def comp_for(*members):
            want = set(members)
            for c in pcy:
                if set(c["members"]) == want:
                    return c
            return None

        pair = comp_for(P_ + "__selftest_pair_a.as", P_ + "__selftest_pair_b.as")
        tri = comp_for(P_ + "__selftest_tri_1.as", P_ + "__selftest_tri_2.as",
                       P_ + "__selftest_tri_3.as")
        amb = comp_for(P_ + "__selftest_amb_x.as", P_ + "__selftest_amb_y.as")
        checks += [
            ("planted MUTUAL PAIR is one certain component of size 2, shape 'pair'",
             bool(pair) and pair["basis"] == "certain" and pair["size"] == 2
             and pair["shape"] == "pair"),
            ("planted mutual pair's two links are BOTH cut links, kinds member and type",
             bool(pair) and len(pair["cut"]) == 2
             and sorted(pair["closingKinds"]) == ["member", "type"]),
            ("planted mutual pair also appears in the mutual-pair list, basis certain",
             any(p["a"] == P_ + "__selftest_pair_a.as"
                 and p["b"] == P_ + "__selftest_pair_b.as" and p["basis"] == "certain"
                 for p in pmu)),
            ("planted THREE-FILE SCC is one certain component of size 3, shape 'scc'",
             bool(tri) and tri["basis"] == "certain" and tri["size"] == 3
             and tri["shape"] == "scc"),
            ("planted three-file SCC contains NO mutual pair -- a ring is not two-way anywhere",
             bool(tri) and tri["mutual"] == []
             and not any(p["a"].startswith(P_ + "__selftest_tri_") for p in pmu)),
            ("planted three-file SCC names all 3 of its links as cut links",
             bool(tri) and len(tri["cut"]) == 3),
            ("planted AMBIGUITY-ONLY cycle is reported, and reported SEPARATELY as "
             "basis 'ambiguous'",
             bool(amb) and amb["basis"] == "ambiguous"),
            ("the ambiguity-only cycle is absent from the certain set -- it is never mixed in",
             not any(c["basis"] == "certain"
                     and P_ + "__selftest_amb_y.as" in c["members"] for c in pcy)),
            ("the ambiguous call really is ambiguous: SelfTestAmbShared is declared in 2 files",
             len(pg.func_owners.get("SelfTestAmbShared", ())) == 2),
            ("no planted component leaked into the tree's own: every component is all-plant or "
             "all-real",
             all(all(m.startswith(P_ + "__selftest") for m in c["members"])
                 or all(not m.startswith(P_ + "__selftest") for m in c["members"])
                 for c in pcy)),
        ]

        # ---- claim board plants --------------------------------------------------------------
        # Against a FIXTURE board in the temp directory. A plant reading the live board would pass
        # or fail by whatever the agents happen to hold this minute.
        fixture = os.path.join(tmp, "CLAIMS_fixture.md")
        with io.open(fixture, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(FIXTURE_CLAIMS)
        cholds, cwarn = resolve_claims(FIXTURE_CLAIMS)
        cagents = sorted(h.agent for h in cholds)
        cheld = held_by_claims(pfiles, copy, cholds)
        tholds, _tw = resolve_claims(FIXTURE_CLAIMS_TREE)
        theld = held_by_claims(pfiles, copy, tholds)
        bpholds, _bw = resolve_claims(
            "## fixture-bp — ACTIVE — started 2000-01-01 23:00\n"
            "**Exclusive paths:**\n- Content/Maps/ExampleMap.umap\n")
        checks += [
            ("claim board: exactly six live agent holds plus one synthetic id for the keyword "
             "block that listed paths",
             [c for c in cagents if not c.startswith("<")]
             == ["fixture-inline", "fixture-live", "fixture-mojibake", "fixture-nostatus",
                 "fixture-reclaimed", "fixture-untidy"]
             and len([c for c in cagents if c.startswith("<")]) == 1),
            ("claim board: the held set is exactly the six planted held files, nothing more",
             set(cheld) == set(FIXTURE_HELD)),
            ("claim: a header whose em dash was mangled to a literal `?` still resolves to HELD",
             P_ + "__selftest_plant.as" in cheld),
            ("claim: a plain ACTIVE block holds its file",
             P_ + "__selftest_pair_a.as" in cheld),
            ("claim: an agent whose LAST block is DONE holds nothing",
             P_ + "__selftest_pair_b.as" not in cheld),
            ("claim: a DONE stamped 2000-01-01 23:20 releases an ACTIVE stamped 2000-01-02 05:00 "
             "that sits ABOVE it -- position decides, never the timestamp",
             P_ + "__selftest_tri_1.as" not in cheld
             and "2000-01-02 05:00" in FIXTURE_CLAIMS and "2000-01-01 23:20" in FIXTURE_CLAIMS),
            ("claim: `## fixture-untidy (4 subagents) - ACTIVE - ...` resolves to HELD -- text "
             "between the id and the dash does not free the block",
             P_ + "__selftest_tri_2.as" in cheld),
            ("claim: a header carrying no status word resolves to HELD, and says so",
             P_ + "__selftest_tri_3.as" in cheld
             and any("fixture-nostatus" in w for w in cwarn)),
            ("claim: DONE then a later ACTIVE re-claims -- position wins in both directions",
             P_ + "__selftest_amb_x.as" in cheld),
            ("claim: a live block with no `**Exclusive paths:**` section holds every path-like "
             "token in it, flagged inferred",
             P_ + "__selftest_amb_y.as" in cheld
             and any(h.agent == "fixture-inline" and h.inferred for h in cholds)),
            ("claim: `**Not touching:**` is not a claim -- the path list ends at the next label",
             P_ + "__selftest_amb_z.as" not in cheld),
            ("claim: a `## ` header inside a ``` fence is not a claim",
             not any("__selftest_fenced" in c for h in cholds for c in h.paths)),
            ("claim: a note block with no agent id and no paths is ignored, not read as an agent",
             not any("GHOST" in h.agent.upper() for h in cholds)),
            ("claim: `## RETRACTION - ...`, one ALL-CAPS word with no paths, is a note and holds "
             "nothing -- it must not claim the file its prose names",
             not any(h.agent == "RETRACTION" for h in cholds)
             and P_ + "__selftest_amb_z.as" not in cheld),
            ("claim: an ALL-CAPS keyword block that DOES list paths is still HELD, under a "
             "synthetic id",
             any(h.agent.startswith("<unparsed-header@line")
                 and any("__selftest_sweep_target" in c for c in h.paths) for h in cholds)),
            ("claim: one agent holding `Script/` whole holds every file in the tree",
             len(theld) == len(pfiles) and len(pfiles) >= MIN_FILES),
            ("claim: a `Content/` claim holds no `.as` file",
             held_by_claims(pfiles, copy, bpholds) == {}),
        ]

        # ---- claim board, end to end through --apply -------------------------------------------
        run2 = os.path.join(tmp, "run2", "Script")
        shutil.copytree(SCRIPT_ROOT, run2)
        for rel_, src in ([(PLANT_FILE, PLANT_SRC)]
                          + sorted(dict(CYCLE_PLANTS, **NAME_PLANTS).items())):
            with io.open(os.path.join(run2, rel_.replace("/", os.sep)), "w",
                         encoding="utf-8", newline="\n") as fh:
                fh.write(src.lstrip("\n"))

        def _run2_bytes():
            out = {}
            for rid in FIXTURE_HELD + FIXTURE_FREE:
                out[rid] = open(os.path.join(run2, rid.replace("/", os.sep)), "rb").read()
            return out

        pre = _run2_bytes()
        rc1, out1 = _run_write_headers(script_root=run2, apply=True, claims=fixture)
        post = _run2_bytes()
        skip_pos = min([out1.find(rid) for rid in FIXTURE_HELD])
        wrote_pos = out1.find("WROTE")
        rc2, out2 = _run_write_headers(script_root=run2, apply=True, claims=fixture,
                                       ignore_claims=True)
        post2 = _run2_bytes()
        rc3, _out3 = _run_write_headers(script_root=run2, apply=True,
                                        claims=os.path.join(tmp, "no_such_board.md"))

        # --as-agent: an agent that claimed its own paths must still be able to write them, and
        # must NOT thereby write anybody else's. A fresh copy, because run2 is already written.
        run3 = os.path.join(tmp, "run3", "Script")
        shutil.copytree(SCRIPT_ROOT, run3)
        for rel_, src in ([(PLANT_FILE, PLANT_SRC)]
                          + sorted(dict(CYCLE_PLANTS, **NAME_PLANTS).items())):
            with io.open(os.path.join(run3, rel_.replace("/", os.sep)), "w",
                         encoding="utf-8", newline=chr(10)) as fh:
                fh.write(src.lstrip(chr(10)))

        def _run3_bytes():
            return dict((rid, open(os.path.join(run3, rid.replace("/", os.sep)), "rb").read())
                        for rid in FIXTURE_HELD + FIXTURE_FREE)

        pre3 = _run3_bytes()
        rc4, out4 = _run_write_headers(script_root=run3, apply=True, claims=fixture,
                                       as_agent="fixture-live")
        post3 = _run3_bytes()
        rc5, out5 = _run_write_headers(script_root=run3, apply=True, claims=fixture,
                                       as_agent="nobody-holds-this")
        others = [r for r in FIXTURE_HELD if r != P_ + "__selftest_pair_a.as"]
        checks += [
            ("--apply leaves every held file BYTE-IDENTICAL and writes every free one",
             all(post[r] == pre[r] for r in FIXTURE_HELD)
             and all(post[r] != pre[r] for r in FIXTURE_FREE)),
            ("--apply exits NON-ZERO (3) when it skipped a held file", rc1 == 3),
            ("--apply names every skipped file, ABOVE the write summary",
             all(r in out1 for r in FIXTURE_HELD) and skip_pos >= 0 and wrote_pos > skip_pos),
            ("--apply names the holder beside each skipped file",
             "fixture-live" in out1 and "fixture-inline" in out1 and "fixture-untidy" in out1),
            ("--ignore-claims writes the held files, exits 0, and prints every hold it overrode",
             all(post2[r] != pre[r] for r in FIXTURE_HELD) and rc2 == 0
             and "OVERRIDE" in out2 and all(r in out2 for r in FIXTURE_HELD)),
            ("a missing claim board REFUSES the write (2) rather than writing the tree blind",
             rc3 == 2),
            ("--as-agent writes the files THAT AGENT holds -- claiming Script/ before a tree-wide "
             "write must not make the write skip everything",
             post3[P_ + "__selftest_pair_a.as"]
             != pre3[P_ + "__selftest_pair_a.as"]),
            ("--as-agent leaves every OTHER agent's held file byte-identical, and still exits 3",
             all(post3[r] == pre3[r] for r in others) and rc4 == 3),
            ("--as-agent names the hold it dropped, and it is not an --ignore-claims override",
             "SELF-CLAIM DROPPED" in out4 and "fixture-live" in out4 and "OVERRIDE" not in out4),
            ("--as-agent with an id nobody holds says so rather than passing silently",
             "matches NO live hold" in out5 and rc5 == 3),
        ]

        # ---- header block plants ------------------------------------------------------------
        ch1, ref1 = render_headers(pg, copy)
        for path, _fid, _old, new, _note in ch1:
            with io.open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
        before = {}
        for dp, _dn, fns in os.walk(copy):
            for fn in sorted(fns):
                if fn.endswith(".as"):
                    fp = os.path.join(dp, fn)
                    before[fp] = open(fp, "rb").read()
        f2, _u2, _k2 = collect(copy)
        g2 = build(f2)
        ch2, ref2 = render_headers(g2, copy)
        after_same = all(open(fp, "rb").read() == b for fp, b in before.items())
        sample = [b for fp, b in sorted(before.items())
                  if fp.endswith("__selftest_pair_a.as")]
        sample_text = sample[0].decode("utf-8") if sample else ""
        narration = re.compile(r'\b20\d\d-\d\d-\d\d\b|\btick \d+|\b(?:bp|port|tool)-[a-z0-9-]{3,}\b')
        block = sample_text.split(HDR_END)[0] if HDR_END in sample_text else ""
        checks += [
            ("--write-headers changes every file once, then reports NOTHING to change on a "
             "second pass",
             bool(ch1) and ch2 == [] and not ref1 and not ref2),
            ("second pass leaves every file BYTE-IDENTICAL", after_same),
            ("the block is `//` lines only -- it can never form the /** */ pair "
             "comment_census.py --stacked reports",
             "/**" not in block and "/*" not in block),
            ("the block carries no date, no agent id and no tick number",
             not narration.search(block)),
            ("the block names every file its own file references",
             P_ + "__selftest_pair_b.as" in block),
            ("the block is the file list only -- no class name, no edge kind, no cycle line",
             "USelfTestPairB" not in block and "CYCLE" not in block
             and "member" not in block and "inherit" not in block),
            ("strip_header REFUSES a file whose BEGIN has no END",
             strip_header(HDR_BEGIN + "\n// x\nclass A {}\n")[2] is True),
            ("strip_header collapses a duplicated block rather than nesting one",
             strip_header((HDR_BEGIN + "\n" + HDR_END + "\n\n") * 2 + "class A {}\n")[0]
             == "class A {}\n"),
        ]

        for label, good in checks:
            print("  %s  %s" % ("PASS" if good else "FAIL", label))
            ok = ok and bool(good)
        nplants = len(checks)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("")
    if not ok:
        print("SELF-TEST FAILED. Nothing was written: a decoder that cannot reproduce a fact it "
              "was given must not report on facts it was not.")
        return 1
    print("SELF-TEST PASSED (%d facts, %d plants). Script/ was never written: every plant went "
          "into a COPY in a temp directory." % (len(FACTS), nplants))
    return 0


# =================================================================================================
# MAIN
# =================================================================================================
def source_fingerprint(files):
    h = hashlib.sha256()
    for f in files:
        h.update(f.id.encode("utf-8"))
        h.update(f.text.encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def rel(path):
    """`os.path.relpath` raises across Windows drives - the output can legitimately sit on C:."""
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return os.path.abspath(path)


def held_by_claims(files, script_root, holds):
    """{file id -> [(agent, the claim string that matched)]} over every live hold."""
    out = {}
    for f in files:
        key = claim_key(os.path.join(script_root, f.id.replace("/", os.sep)), script_root, f.id)
        for h in holds:
            for c in h.paths:
                if claim_matches(c, key):
                    out.setdefault(f.id, []).append((h.agent, c))
                    break
    return out


def write_headers(a):
    files, unreadable, unknown = collect(a.script_root)
    if not files:
        print("no .as files under %s" % a.script_root)
        return 2

    dropped = []
    for item in unreadable:
        dropped.append("UNREADABLE  %s" % item)
    for item in unknown:
        dropped.append("UNKNOWN LAYER  %s  (directory not in LAYERS; add it and re-run)" % item)
    for f in files:
        if f.missed_props:
            dropped.append("UPROPERTY NOT MATCHED  %s  x%d" % (f.id, f.missed_props))
        if f.missed_funcs:
            dropped.append("UFUNCTION NOT MATCHED  %s  x%d" % (f.id, f.missed_funcs))
    if dropped:
        print("THE PARSER LEFT SOMETHING OUT -- %d item(s). A header block written from an "
              "incomplete parse states a dependency list that is wrong, so --apply is refused:"
              % len(dropped))
        for line in dropped:
            print("  " + line)
        return 2

    # ---- the claim board, FIRST, so a skipped file is the first thing read --------------------
    holds, cwarn, cerr = load_claims(a.claims)
    if cerr and not a.ignore_claims:
        print("CLAIM BOARD UNREADABLE -- %s" % cerr)
        print("Refusing to write. A tree-wide write that does not know who holds what is the "
              "failure this check exists to stop. Pass --claims <path>, or --ignore-claims to "
              "write anyway.")
        return 2
    mine = getattr(a, "as_agent", None)
    if mine:
        own = [h for h in holds if h.agent == mine]
        holds = [h for h in holds if h.agent != mine]
        if own:
            print("SELF-CLAIM DROPPED -- --as-agent %s. %d block(s) you own no longer skip files; "
                  "every other agent's hold still does:" % (mine, len(own)))
            for h in own:
                print("  MINE  line %-6d %s" % (h.line, "; ".join(h.paths) or "(no path listed)"))
        else:
            print("SELF-CLAIM DROPPED -- --as-agent %s matches NO live hold on %s. Claim before "
                  "you write." % (mine, rel(a.claims)))
        print("")
    held = held_by_claims(files, a.script_root, holds)

    if a.ignore_claims:
        print("OVERRIDE: --ignore-claims. Every file will be written, held or not.")
        if cerr:
            print("  the board could not be read (%s), so nothing could be skipped anyway." % cerr)
        else:
            print("  %d live hold(s) on %s; %d of the %d file(s) here are held:"
                  % (len(holds), rel(a.claims), len(held), len(files)))
            for fid in sorted(held):
                print("  OVERRIDDEN  %-50s held by %s"
                      % (fid, "; ".join("%s (claims `%s`)" % (ag, cl) for ag, cl in held[fid])))
            if not held:
                print("  OVERRIDDEN  nothing - no live hold covers any file in this run.")
            for w in cwarn:
                print("  board note  " + w)
        print("  An override is correct only after the board has been swept and the holds are "
              "known dead. Say in %s that it was used, and on what."
              % os.path.basename(MESSAGES_FILE))
        print("")
        held = {}
    elif held:
        print("HELD BY ANOTHER AGENT -- %d of %d file(s) will NOT be written:"
              % (len(held), len(files)))
        for fid in sorted(held):
            print("  SKIP  %-50s %s"
                  % (fid, "; ".join("%s (claims `%s`)" % (ag, cl) for ag, cl in held[fid])))
        print("  Resolved from %s by block POSITION, never by timestamp. Nothing waits on a hold: "
              "do other work, or ask in %s." % (rel(a.claims), MESSAGES_FILE))
        print("")

    if cwarn and not a.ignore_claims:
        print("CLAIM BOARD SHAPES READ CONSERVATIVELY -- %d. Each resolved to HELD:" % len(cwarn))
        for w in cwarn:
            print("  " + w)
        print("")

    rc_held = 3 if held else 0

    g = build(files)
    changes, refused = render_headers(g, a.script_root)
    changes = [c for c in changes if c[1] not in held]
    if refused:
        print("REFUSED -- %d file(s) will not be touched at all:" % len(refused))
        for line in refused:
            print("  " + line)
        print("")

    if not changes:
        print("%d files, %d skipped as held, every remaining generated block already current. "
              "Nothing to write." % (len(files), len(held)))
        return rc_held or (1 if refused else 0)

    print("%d of %d files would change (%d skipped as held)."
          % (len(changes), len(files), len(held)))
    added = sum(1 for c in changes if c[4] == "added")
    print("  %d gain a block, %d have one replaced." % (added, len(changes) - added))
    for path, fid, _old, _new, note in changes[:40]:
        print("  %-8s %s" % (note, fid))
    if len(changes) > 40:
        print("  ... and %d more" % (len(changes) - 40))

    for path, fid, _old, new in [c[:4] for c in changes[:max(0, a.show)]]:
        print("")
        print("---- %s ----" % fid)
        for line in new.split("\n"):
            if line.startswith(HDR_BEGIN[:6]) or line.startswith("//"):
                print(line.rstrip("\r"))
            if HDR_END_RE.match(line):
                break

    if not a.apply:
        print("")
        print("DRY RUN. Nothing was written. Re-run with --apply to write, and read the note "
              "below first.")
        print("Saving any Script/**/*.as recompiles the whole AngelScript layer for every agent "
              "sharing the editor. Run --apply as ONE batch, announce it in "
              "%s before, and check the log afterwards for "
              "'Keeping all old script code'." % MESSAGES_FILE)
        return rc_held

    for path, fid, _old, new, _note in changes:
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
    print("")
    print("WROTE %d file(s) under %s, SKIPPED %d held by another agent. The script layer is "
          "recompiling now." % (len(changes), a.script_root, len(held)))
    return rc_held or (1 if refused else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script-root", default=SCRIPT_ROOT)
    ap.add_argument("--out", default=OUT_HTML)
    ap.add_argument("--json", action="store_true",
                    help="also write script-graph.json next to the HTML, for other tools")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--open", dest="open_", action="store_true")
    ap.add_argument("--write-headers", dest="write_headers", action="store_true",
                    help="print the generated dependency block each Script/ file would gain. "
                         "DRY RUN - writes nothing without --apply")
    ap.add_argument("--apply", action="store_true",
                    help="with --write-headers, actually write the files. Saving any .as file "
                         "hot-reloads the whole script layer for every agent sharing the editor, "
                         "so this is one batched change, announced first")
    ap.add_argument("--show", type=int, default=3,
                    help="with --write-headers, how many full blocks to print (default 3)")
    ap.add_argument("--claims", default=CLAIMS_PATH,
                    help="the claim board to resolve holds from (default: the claimsFile "
                         "setting). A file held by another agent is named and skipped, and the "
                         "exit code is 3")
    ap.add_argument("--ignore-claims", dest="ignore_claims", action="store_true",
                    help="write held files too. Prints every hold it is overriding. Correct only "
                         "for a coordinator that has just swept the board")
    ap.add_argument("--as-agent", dest="as_agent", metavar="AGENT-ID",
                    help="your own agent id. An agent claims `Script/` before a tree-wide write, "
                         "and without this the write then skips every file as held BY YOU. This "
                         "drops the holds owned by AGENT-ID and nothing else -- every other "
                         "agent's hold still skips its files. Not --ignore-claims")
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    if a.write_headers:
        return write_headers(a)

    files, unreadable, unknown = collect(a.script_root)
    if not files:
        print("no .as files under %s" % a.script_root)
        return 2

    dropped = []
    for item in unreadable:
        dropped.append("UNREADABLE  %s" % item)
    for item in unknown:
        dropped.append("UNKNOWN LAYER  %s  (directory not in LAYERS; add it and re-run)" % item)
    for f in files:
        if f.missed_props:
            dropped.append("UPROPERTY NOT MATCHED  %s  x%d" % (f.id, f.missed_props))
        if f.missed_funcs:
            dropped.append("UFUNCTION NOT MATCHED  %s  x%d" % (f.id, f.missed_funcs))

    g = build(files)
    data = make_data(g, source_fingerprint(files), dropped)
    c = data["counts"]

    # Print the shortfall FIRST. A truncation reported under a clean-looking summary reads as
    # "covered everything", which is the failure this section exists to stop.
    if dropped:
        print("THE PARSER LEFT SOMETHING OUT -- %d item(s). Everything below is incomplete:"
              % len(dropped))
        for line in dropped:
            print("  " + line)
        print("")

    out = a.out
    d = os.path.dirname(out)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(data))
    if a.json:
        jp = os.path.splitext(out)[0] + ".json"
        with io.open(jp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(data, sort_keys=True, ensure_ascii=False, indent=1))
        print("json  -> %s" % rel(jp))

    print("%d files, %d classes, %d structs, %d enums, %d functions, %d events"
          % (c["files"], c["classes"], c["structs"], c["enums"], c["funcs"], c["events"]))
    print("%d edges: inherit %d · member %d · call %d · event %d · type %d"
          % (c["edges"], c["inherit"], c["member"], c["call"], c["event"], c["type"]))
    print("%d cross-layer, %d UPWARD (target ranks above source), %d ambiguous call edges"
          % (c["cross"], c["up"], c["ambiguous"]))
    print("CYCLES over unambiguous edges: %d SCC of 3+, %d two-file component, %d mutual pairs; "
          "%d of %d files sit inside one, worst component %d files"
          % (c["cycleSccs"], c["cyclePairs"], c["mutual"], c["cycleNodes"], c["files"],
             c["worstCycle"]))
    for cyc in data["cycles"]:
        if cyc["basis"] != "certain":
            continue
        print("  %-4s %2d files  layers %s" % (cyc["shape"], cyc["size"],
                                               ", ".join(cyc["layers"])))
        if cyc["cut"]:
            print("       %d cut link(s), closing kinds %s -- removing one on its own breaks it "
                  "apart:" % (len(cyc["cut"]), ", ".join(cyc["closingKinds"])))
            for s, t in cyc["cut"]:
                kinds = [l["kinds"] for l in cyc["links"] if l["s"] == s and l["t"] == t]
                print("         %s -> %s  [%s]"
                      % (s, t, ", ".join(kinds[0]) if kinds else ""))
        else:
            print("       no single link cuts it: every removal leaves it strongly connected")
    if c["cyclesAmbiguous"] or c["mutualAmbiguous"]:
        print("  AMBIGUITY-DEPENDENT and counted nowhere above: %d component(s), %d mutual pair(s)"
              % (c["cyclesAmbiguous"], c["mutualAmbiguous"]))
    print("%d functions with no caller in Script/ -- %d of them unreflected"
          % (c["noCallerRefl"] + c["noCallerUnrefl"], c["noCallerUnrefl"]))
    bad_props = [r for r in data["props"] if r["state"] != "ok"]
    print("%d of %d delegate properties not fully wired: %s"
          % (len(bad_props), c["props"],
             ", ".join("%s::%s %s (%s)" % (r["cls"] or "-", r["prop"], r["event"], r["state"])
                       for r in bad_props) or "none"))
    if c["propsAmbiguous"]:
        amb = sorted(g.ambiguous_events.items(), key=lambda kv: (-kv[1], kv[0]))
        print("  %d of them share a property NAME with another event type, so every occurrence "
              "counts for each: %s"
              % (c["propsAmbiguous"], ", ".join("%s x%d" % (n, k) for n, k in amb)))
    print("html  -> %s   fingerprint %s" % (rel(out), data["hash"]))

    if g.ambiguous_calls:
        top = sorted(g.ambiguous_calls.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        print("ambiguous call names (declared in more than one file, drawn dashed to every "
              "candidate, never silently dropped): "
              + ", ".join("%s x%d" % (n, c2) for n, c2 in top))

    if a.open_:
        import webbrowser
        webbrowser.open("file:///" + os.path.abspath(out).replace("\\", "/"))

    return 2 if dropped else 0


if __name__ == "__main__":
    sys.exit(main())
