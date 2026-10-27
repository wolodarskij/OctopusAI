"""artifacts.py - the artifacts of one project: the pages agents made, and the project wiki.

    octopus todo artifact add <path|url> [--title T] [--description D] [--ticket T0001] [--date D]
    octopus todo artifact list                    every registered artifact; marks a missing file
    octopus todo artifact remove <path|url>
    octopus todo artifact found                   the HTML pages under the scan directories that
                                                  the manifest does not hold

WHAT AN ARTIFACT IS. A page an agent made for the owner to read: a report, an evaluation, a
documentation page. It is a file of the project (an HTML page, as a rule), or a page published
elsewhere, such as on claude.ai, by its `https://` URL. The board's Artifacts view lists them
and opens them.

THE MANIFEST. `.octopus/artifacts.json`, tracked with the project:

    {"artifacts": [{"path": "Docs/x.html", "title", "description", "ticket", "date"},
                   {"url": "https://...", "title", "description", "ticket", "date"}]}

Each entry has exactly one of `path` (relative to the project root, forward slashes) and `url`.
The list stays sorted by date, newest first.

THE WIKI. Every `.md` file under the wiki directory. It needs no registration: the board lists
the files and renders each one with `render_md`. `artifacts` in `.octopus/project.json` names
the directories; both are optional:

    "artifacts": {"wiki": "Docs", "scan": ["Docs"]}

THE GUARD. `safe_path` is the only way the board reaches a file by a path that a page or a link
names: a file under the project root, and no part of its path starts with a dot, so `.git`,
`.octopus`, `.env` and `TODO/.briefs` stay closed.
"""

from __future__ import annotations

import datetime
import html
import json
import re
from pathlib import Path

import admin as _admin

MANIFEST = ".octopus/artifacts.json"
DEFAULT_WIKI = "Docs"
DEFAULT_SCAN = ("Docs",)
PAGE_EXT = (".html", ".htm")
_SKIP = {"node_modules", "__pycache__"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEAD = 65536          # the bytes of a file read for its title


# ── the manifest ────────────────────────────────────────────────────────────

def _manifest(root):
    return Path(root) / MANIFEST


def load(root):
    """The registered artifacts, newest first."""
    try:
        d = json.loads(_manifest(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = d.get("artifacts") if isinstance(d, dict) else None
    out = []
    for e in rows if isinstance(rows, list) else []:
        if isinstance(e, dict) and (isinstance(e.get("path"), str) or isinstance(e.get("url"), str)):
            out.append(e)
    return out


def _sorted(entries):
    out = sorted(entries, key=lambda e: (e.get("title") or "").lower())
    return sorted(out, key=lambda e: e.get("date") or "", reverse=True)


def save(root, entries):
    """Write the manifest, sorted by date, newest first."""
    rows = []
    for e in _sorted(entries):
        row = {"path": e["path"]} if e.get("path") else {"url": e["url"]}
        for k in ("title", "description", "ticket", "date"):
            row[k] = e.get(k) or ""
        rows.append(row)
    _admin._write_bytes(_manifest(root), _admin._json_bytes({"artifacts": rows}))


def _key(e):
    return ("path", e["path"]) if e.get("path") else ("url", e.get("url"))


def is_url(target):
    return "://" in target or target.lower().startswith(("http:", "https:"))


def _target(root, target, must_exist):
    """("path", rel) or ("url", url) for what the caller names. Raises ValueError."""
    target = (target or "").strip()
    if not target:
        raise ValueError("name a file of the project or an https:// URL")
    if is_url(target):
        if not target.lower().startswith("https://") or len(target) <= len("https://"):
            raise ValueError("a URL artifact must start with https://: %s" % target)
        return "url", target
    p = Path(target)
    root = Path(root).resolve()
    if p.is_absolute():
        try:
            target = p.resolve().relative_to(root).as_posix()
        except ValueError:
            raise ValueError("%s is outside the project %s" % (target, root)) from None
    rel = target.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if must_exist:
        if safe_path(root, rel) is None:
            raise ValueError("%s is no file of the project, or it is in a closed directory" % rel)
    return "path", Path(rel).as_posix()


def add(root, target, title=None, description="", ticket="", date=None):
    """Register a file of the project or an https:// URL, or update the entry that holds it.
    Returns the entry. Raises ValueError on a refused target or a bad date."""
    kind, value = _target(root, target, must_exist=True)
    if date is not None and date != "" and not _DATE.match(date):
        raise ValueError("the date is YYYY-MM-DD: %s" % date)
    entries = load(root)
    old = next((e for e in entries if _key(e) == (kind, value)), None)
    if old is None:
        old = {kind: value, "title": "", "description": "", "ticket": "", "date": ""}
        entries.append(old)
    if title:
        old["title"] = title.strip()
    elif not old.get("title"):
        old["title"] = page_title(Path(root) / value) if kind == "path" else value
    if description is not None and description != "":
        old["description"] = " ".join(description.split())
    if ticket is not None and ticket != "":
        old["ticket"] = ticket.strip()
    if date:
        old["date"] = date
    elif not old.get("date"):
        old["date"] = datetime.date.today().isoformat()
    save(root, entries)
    return old


def remove(root, target):
    """Remove the entry that holds the target. Returns True when one was there."""
    kind, value = _target(root, target, must_exist=False)
    entries = load(root)
    keep = [e for e in entries if _key(e) != (kind, value)]
    if len(keep) == len(entries):
        return False
    save(root, keep)
    return True


# ── the files ───────────────────────────────────────────────────────────────

def safe_path(root, rel):
    """The file `rel` names under `root`, or None: a path outside the root, a part that starts
    with a dot, a drive or a stream (`:`), and a directory are all refused."""
    rel = (rel or "").replace("\\", "/")
    if not rel or rel.startswith("/") or ":" in rel or "\x00" in rel:
        return None
    parts = rel.split("/")
    if any(not s or s.startswith(".") for s in parts):
        return None
    root = Path(root).resolve()
    try:
        p = (root / rel).resolve()
        inner = p.relative_to(root)
    except (OSError, ValueError):
        return None
    if any(s.startswith(".") for s in inner.parts):
        return None
    return p if p.is_file() else None


def page_title(path):
    """The `<title>` of an HTML file, the first `# ` heading of a Markdown file, else the
    file name."""
    path = Path(path)
    try:
        with open(path, "rb") as fh:
            text = fh.read(_HEAD).decode("utf-8", "replace")
    except OSError:
        return path.name
    if path.suffix.lower() == ".md":
        m = re.search(r"^#\s+(.+?)\s*#*\s*$", text, re.M)
    else:
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if m:
        t = " ".join(html.unescape(m.group(1)).split())
        if t:
            return t
    return path.name


def _cfg(cfg):
    a = (cfg or {}).get("artifacts")
    a = a if isinstance(a, dict) else {}
    wiki = a.get("wiki") if isinstance(a.get("wiki"), str) else DEFAULT_WIKI
    scan = a.get("scan") if isinstance(a.get("scan"), list) else list(DEFAULT_SCAN)
    return wiki.strip("/\\") or DEFAULT_WIKI, [s.strip("/\\") for s in scan if isinstance(s, str)]


def _walk(root, sub, exts):
    """Every file under `root/sub` whose suffix is in `exts`, as a project-relative path.
    Dot directories and `node_modules` are skipped."""
    root = Path(root).resolve()
    base = root / sub if sub else root
    if not base.is_dir() or (sub and safe_dir(root, sub) is None):
        return []
    out = []
    stack = [base]
    while stack:
        d = stack.pop()
        try:
            kids = list(d.iterdir())
        except OSError:
            continue
        for k in kids:
            if k.name.startswith(".") or k.name in _SKIP:
                continue
            if k.is_dir():
                stack.append(k)
            elif k.suffix.lower() in exts:
                out.append(k.relative_to(root).as_posix())
    return sorted(out, key=str.lower)


def safe_dir(root, rel):
    """The directory `rel` names under `root`, or None, by the rules of `safe_path`."""
    rel = (rel or "").replace("\\", "/").strip("/")
    if not rel or ":" in rel or any(not s or s.startswith(".") for s in rel.split("/")):
        return None
    root = Path(root).resolve()
    p = (root / rel).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    return p if p.is_dir() else None


def found(root, cfg):
    """Every HTML page under the scan directories that the manifest does not hold, with its
    title."""
    _, scan = _cfg(cfg)
    have = {e["path"] for e in load(root) if e.get("path")}
    out, seen = [], set()
    for sub in scan:
        for rel in _walk(root, sub, PAGE_EXT):
            if rel in have or rel in seen:
                continue
            seen.add(rel)
            out.append({"path": rel, "title": page_title(Path(root) / rel)})
    return out


def wiki(root, cfg):
    """Every Markdown page under the wiki directory, the index of the wiki root first."""
    w, _ = _cfg(cfg)
    pages = _walk(root, w, (".md",))
    first = {("%s/%s" % (w, n)).lower() for n in ("index.md", "readme.md")}
    return sorted(pages, key=lambda p: (p.lower() not in first, p.lower()))


def listing(root, cfg):
    """What the board's Artifacts view draws."""
    rows = []
    for e in load(root):
        r = dict(e)
        r["missing"] = bool(e.get("path")) and safe_path(root, e["path"]) is None
        rows.append(r)
    w, _ = _cfg(cfg)
    return {"artifacts": rows, "found": found(root, cfg), "wiki": wiki(root, cfg),
            "wikiRoot": w}


# ── the Markdown renderer ───────────────────────────────────────────────────

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([\w+-]*)")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_SAFE_URL = re.compile(r"^(https?:|mailto:|#|[^:]*$)", re.I)

_STYLE = """
:root{--bg:#ffffff;--fg:#1d2129;--dim:#5b6372;--line:#dfe3ea;--code-bg:#f3f5f8;--link:#2f62c9;--quote:#c9d1de}
@media (prefers-color-scheme:dark){
  :root{--bg:#111318;--fg:#d9dde5;--dim:#8f97a6;--line:#2a2f3a;--code-bg:#1b1f27;--link:#7aa4f5;--quote:#3a4252}
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:20px 16px 48px;overflow-wrap:break-word}
a{color:var(--link)}
h1,h2,h3,h4,h5,h6{line-height:1.3;margin:1.4em 0 .5em}
h1{font-size:1.7em;margin-top:.4em}
h2{font-size:1.35em;border-bottom:1px solid var(--line);padding-bottom:.25em}
h3{font-size:1.15em}
code{font:.88em ui-monospace,Consolas,Menlo,monospace;background:var(--code-bg);padding:.1em .3em;border-radius:4px}
pre{background:var(--code-bg);padding:12px 14px;border-radius:8px;overflow-x:auto}
pre code{background:none;padding:0}
blockquote{margin:1em 0;padding:0 0 0 14px;border-left:3px solid var(--quote);color:var(--dim)}
.table{overflow-x:auto}
table{border-collapse:collapse;margin:1em 0;font-size:.93em}
th,td{border:1px solid var(--line);padding:5px 9px;text-align:left;vertical-align:top}
th{background:var(--code-bg)}
img{max-width:100%;height:auto}
hr{border:0;border-top:1px solid var(--line);margin:1.6em 0}
"""


def _slug(text, used):
    s = re.sub(r"[^\w\- ]", "", re.sub(r"<[^>]+>", "", html.unescape(text)).lower()).strip()
    s = re.sub(r"\s", "-", s) or "section"
    base, n = s, 1
    while s in used:
        s = "%s-%d" % (base, n)
        n += 1
    used.add(s)
    return s


def _href(url):
    """The URL as an attribute value, or None for a scheme other than http, https and mailto."""
    raw = html.unescape(url)
    if not _SAFE_URL.match(raw.strip()):
        return None
    return html.escape(raw, quote=True)


def _inline(text):
    """One line of Markdown as HTML. Every character is escaped before any markup is made."""
    keep = []

    def hold(s):
        keep.append(s)
        return "\x00%d\x00" % (len(keep) - 1)

    def code(m):
        return hold("<code>%s</code>" % html.escape(m.group(2).strip(" ") or m.group(2), quote=False))

    text = re.sub(r"(`+)(.+?)\1", code, text.replace("\x00", ""))
    text = html.escape(text, quote=True)

    def image(m):
        src = _href(m.group(2))
        if src is None:
            return m.group(1)
        return hold('<img src="%s" alt="%s" loading="lazy">' % (src, m.group(1)))

    def link(m):
        href = _href(m.group(2))
        label = _emphasis(m.group(1))
        return hold('<a href="%s">%s</a>' % (href, label) if href else label)

    def auto(m):
        return hold('<a href="%s">%s</a>' % (m.group(1), m.group(1)))

    text = re.sub(r"!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+&quot;.*?&quot;)?\s*\)", image, text)
    text = re.sub(r"\[([^\]]+)\]\(\s*([^)\s]*)(?:\s+&quot;.*?&quot;)?\s*\)", link, text)
    text = re.sub(r"&lt;(https?://[^\s&]+)&gt;", auto, text)
    text = _emphasis(text)
    while "\x00" in text:
        text = re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], text)
    return text


def _emphasis(text):
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\w)_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)", r"<em>\1</em>", text)
    text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", text)
    return text


def _cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _indent(line):
    return len(line) - len(line.lstrip(" ")) if line.strip() else 0


def _starts_block(lines, i):
    line = lines[i]
    return bool(_FENCE.match(line) or _HEADING.match(line) or _RULE.match(line)
                or _ITEM.match(line) or line.lstrip().startswith(">")
                or ("|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1])))


def _blocks(lines, used):
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _FENCE.match(line)
        if m:
            fence, lang = m.group(1), m.group(2)
            body = []
            i += 1
            while i < n and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            i += 1
            cls = ' class="language-%s"' % html.escape(lang) if lang else ""
            out.append("<pre><code%s>%s</code></pre>" % (cls, html.escape("\n".join(body), quote=False)))
            continue
        m = _HEADING.match(line)
        if m:
            level, text = len(m.group(1)), _inline(m.group(2))
            out.append('<h%d id="%s">%s</h%d>' % (level, _slug(text, used), text, level))
            i += 1
            continue
        if _RULE.match(line):
            out.append("<hr>")
            i += 1
            continue
        if "|" in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            head = _cells(line)
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_cells(lines[i]))
                i += 1
            t = ["<div class=\"table\"><table><thead><tr>"]
            t += ["<th>%s</th>" % _inline(c) for c in head]
            t.append("</tr></thead><tbody>")
            for r in rows:
                r = (r + [""] * len(head))[:max(len(head), len(r))]
                t.append("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in r))
            t.append("</tbody></table></div>")
            out.append("".join(t))
            continue
        if line.lstrip().startswith(">"):
            body = []
            while i < n and lines[i].strip() and (lines[i].lstrip().startswith(">") or body):
                s = lines[i].lstrip()
                if s.startswith(">"):
                    s = s[1:]
                    s = s[1:] if s.startswith(" ") else s
                elif _starts_block(lines, i):
                    break
                body.append(s)
                i += 1
            out.append("<blockquote>%s</blockquote>" % _blocks(body, used))
            continue
        m = _ITEM.match(line)
        if m:
            i = _list(lines, i, used, out)
            continue
        para = []
        while i < n and lines[i].strip() and not (para and _starts_block(lines, i)):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % "<br>".join(_inline(p) for p in _hard_breaks(para)))
    return "\n".join(out)


def _hard_breaks(para):
    """Join the lines of a paragraph; a line that ends in two spaces or a backslash keeps its
    break."""
    out, cur = [], []
    for p in para:
        cur.append(p.rstrip("\\").rstrip())
        if p.endswith("\\"):
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def _list(lines, i, used, out):
    """One list from line `i`; appends its HTML to `out` and returns the line after it."""
    n = len(lines)
    first = _ITEM.match(lines[i])
    base = _indent(lines[i])
    ordered = first.group(2)[0].isdigit()
    items = []
    while i < n:
        line = lines[i]
        m = _ITEM.match(line)
        if m and _indent(line) == base and m.group(2)[0].isdigit() == ordered:
            items.append([m.group(3)])
            i += 1
            continue
        if not line.strip():
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n and _indent(lines[j]) > base:
                items[-1].append("")
                i = j
                continue
            m = _ITEM.match(lines[j]) if j < n else None
            if m and _indent(lines[j]) == base and m.group(2)[0].isdigit() == ordered:
                i = j
                continue
            break
        if _indent(line) > base:
            items[-1].append(line[base:] if len(line) > base else line.strip())
            i += 1
            continue
        if _starts_block(lines, i):
            break
        items[-1].append(line.strip())
        i += 1
    tag = "ol" if ordered else "ul"
    start = ""
    if ordered:
        num = int(re.match(r"\d+", first.group(2)).group(0))
        start = ' start="%d"' % num if num != 1 else ""
    parts = ["<%s%s>" % (tag, start)]
    for it in items:
        sub = it[:1] + _dedent(it[1:])
        body = _blocks(sub, used)
        if body.startswith("<p>") and body.count("<p>") == 1:
            body = body[3:].replace("</p>", "", 1)
        parts.append("<li>%s</li>" % body)
    parts.append("</%s>" % tag)
    out.append("".join(parts))
    return i


def _dedent(lines):
    ind = [_indent(x) for x in lines if x.strip()]
    cut = min(ind) if ind else 0
    return [x[cut:] if x.strip() else "" for x in lines]


def render_md(text, title=None):
    """A Markdown page as a whole HTML document: light and dark colors, readable on a phone.
    No raw HTML of the source passes through."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n")
    if lines and lines[0].strip() == "---":
        end = next((k for k in range(1, len(lines)) if lines[k].strip() == "---"), None)
        if end is not None:
            lines = lines[end + 1:]
    body = _blocks(lines, set())
    if not title:
        m = next((_HEADING.match(x) for x in lines if _HEADING.match(x)), None)
        title = m.group(2) if m else "Page"
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<meta name=\"color-scheme\" content=\"light dark\">"
            "<title>%s</title><style>%s</style></head><body><main>\n%s\n</main></body></html>\n"
            % (html.escape(title), _STYLE, body))
