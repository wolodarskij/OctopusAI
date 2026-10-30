"""aifiled.py - move the open tickets that an AI filed without an owner's request to AI-Pipeline.

    python octopus.py ai-filed [--dry-run]

`new` marks such a ticket `filed-by: ai` and files it in the `ai` lane. A ticket filed
before `new` did that, or filed past it, carries no mark. This sweep finds each of them in
every registered project and moves it: the manager's 10-minute check runs it, and the
command above runs it at once.

THE EVIDENCE is the Claude Code transcript of the session that filed the ticket: the
`new` call of the ticket tree and the ticket file it printed. The ticket is requested when
the command carried `--requested` or `--owner-order`, or when the owner's own prompt
started the turn that ran it. It is an AI ticket when a launch prompt, a scheduled job, a
task notification or another engine prompt started that turn. A ticket that no transcript
shows filed is requested.

A TICKET STAYS where it is when it:
- is not in the start or the blocked lane, or is in an epic;
- carries `filed-by: ai` already, is repeatable, is `blocked by user`, or holds
  `## Owner's orders`;
- is a child of a requested ticket, or a follow-up that a ticket of another project names
  under `## Follow-ups`;
- has a session that runs now;
- was judged before. Each ticket is judged once, so every later move of the owner stands.

Both records live beside the registry, outside every repository: `ai-filed.json`, the
tickets judged per project, and `ai-filed-scan.json`, what each transcript showed, read
again only from where the last scan stopped.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import project  # noqa: E402

JUDGED_FILE = "ai-filed.json"
SCAN_FILE = "ai-filed-scan.json"
REQUESTED, AI = "requested", "ai"

_PROJECT_ARG = r"""--project[ =](?:"[^"]*"|'[^']*'|[^\s"']+)"""
_NEW = re.compile(r"""(?:todo\.py|octopus\.py["']?\s+todo)["']?(?:\s+%s)?\s+new\b""" % _PROJECT_ARG)
_PROJECT = re.compile(r"""--project[ =](?:"([^"]*)"|'([^']*)'|([^\s"']+))""")
_TODO_PATH = re.compile(r"""([A-Za-z]:[\\/][^\s"']*?|/[A-Za-z]/[^\s"']*?)[\\/]\.octopus[\\/]todo\.py""")
_CD = re.compile(r"""^\s*(?:cd|Set-Location|pushd)\s+(?:-\w+\s+)?(?:"([^"]+)"|'([^']+)'|([^\s;&|]+))""")
_REQUESTED = re.compile(r"\s--(?:requested|owner-order)\b")
_FILED = re.compile(r"^(T\d{3,})  (\S[^\r\n]*?\.md)\s*$", re.M)
_ENGINE_PROMPT = re.compile(r"^(?:(?:Work|Re-evaluate|Verify) ticket T\d+\. Read \S*\.briefs[\\/]"
                            r"|Run the 10-minute check:|<task-notification>|You judge one Claude "
                            r"Code session)")
# The launch prompt of a ticket session, and the brief it names.
_LAUNCH = re.compile(r"(?:Work|Re-evaluate|Verify) ticket T\d+\. Read (\S*\.briefs[\\/]T\d+\.md)")
_KEEP_TURN = ("This session is being continued", "[Request interrupted", "<local-command",
              "Base directory for this skill", "[Your previous response")


# ── the transcripts ───────────────────────────────────────────────────────

def transcripts():
    """Every top-level Claude Code transcript on this machine. A subagent's transcript is
    left out: its prompt comes from its session, never from the owner."""
    return sorted(_transcript_dir().glob("*/*.jsonl"))


def _transcript_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "projects"


def prompt_kind(rec):
    """Who started the turn that this user record opens: "owner", "engine", or None for a
    record that opens no turn of its own (a tool result, a compaction, a skill's text)."""
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    else:
        text = content if isinstance(content, str) else ""
    text = text.lstrip()
    if "turnCompanion" in rec or text.startswith(_KEEP_TURN):
        return None
    if text.startswith("<command-name>"):
        # A slash command opens the owner's turn when it carries words; a bare one such as
        # `/recap` or `/remote-control` is also what the engine types.
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        return "owner" if args and args.group(1).strip() else None
    origin = rec.get("turnOrigin")
    if origin is None:
        o = rec.get("origin")
        origin = o.get("kind") if isinstance(o, dict) else None
    if origin is None and (rec.get("isMeta") or rec.get("entrypoint") == "sdk-cli"):
        origin = "engine"
    if origin not in (None, "human"):
        return "engine"
    return "engine" if _ENGINE_PROMPT.match(text) else "owner"


def _call_place(cmd, cwd):
    """`[project argument, todo.py path, cd path, cwd]` of one `new` call: what tells which
    project it filed into."""
    m = _PROJECT.search(cmd)
    p = _TODO_PATH.search(cmd)
    c = _CD.match(cmd)
    return [next((g for g in m.groups() if g is not None), "") if m else "",
            p.group(1) if p else "",
            next((g for g in c.groups() if g is not None), "") if c else "",
            cwd or ""]


def scan_file(path, entry=None):
    """Read one transcript from where `entry` stopped. Returns the new entry: `size`,
    `mtime`, `offset`, the kind of the open `turn`, the `pending` calls whose result has not
    come, and every `filings` row `[ticket file name, requested, place]`."""
    st = path.stat()
    e = dict(entry or {})
    if not e or st.st_size < e.get("offset", 0):
        e = {"offset": 0, "turn": None, "pending": {}, "filings": []}
    pending, filings, turn = dict(e["pending"]), list(e["filings"]), e["turn"]
    offset = e["offset"]
    with open(path, "rb") as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            offset += len(raw)
            if b'"type":"assistant"' in raw:
                if b"tool_use" not in raw or b"todo" not in raw or b"new" not in raw:
                    continue
                rec = _load(raw)
                for b in ((rec.get("message") or {}).get("content") or []):
                    if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                        continue
                    cmd = (b.get("input") or {}).get("command")
                    if isinstance(cmd, str) and _NEW.search(cmd):
                        pending[b.get("id", "")] = [
                            turn == "owner" or bool(_REQUESTED.search(cmd)),
                            _call_place(cmd, rec.get("cwd"))]
                continue
            if b'"type":"user"' not in raw:
                continue
            if b'"tool_result"' in raw:
                if not any(i.encode() in raw for i in pending):
                    continue
                rec = _load(raw)
                for b in ((rec.get("message") or {}).get("content") or []):
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"
                            and b.get("tool_use_id") in pending):
                        continue
                    requested, place = pending.pop(b["tool_use_id"])
                    out = b.get("content")
                    if isinstance(out, list):
                        out = "\n".join(x.get("text", "") for x in out if isinstance(x, dict))
                    for _tid, rel in _FILED.findall(out if isinstance(out, str) else ""):
                        filings.append([re.split(r"[\\/]", rel)[-1], requested, place])
                continue
            rec = _load(raw)
            if rec.get("type") == "user":
                kind = prompt_kind(rec)
                if kind:
                    turn = kind
    return {"size": st.st_size, "mtime": st.st_mtime, "offset": offset, "turn": turn,
            "pending": pending, "filings": filings}


def _load(raw):
    try:
        rec = json.loads(raw)
    except ValueError:
        return {}
    return rec if isinstance(rec, dict) else {}


def _block_text(content):
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return content if isinstance(content, str) else ""


def owner_words(sid):
    """What the owner said to the Claude Code session `sid`: `(said, brief)`. `said` lists,
    oldest first, `(kind, text)`: kind "prompt" for the owner's own prompt, also one the
    owner sent while a turn ran, and "answer" for the owner's answer to a question dialog of
    the session, with its question. `brief` is the path of the brief that the launch prompt
    of the session names, or None. Both are empty when no transcript of `sid` is on this
    machine."""
    said, brief, asks = [], None, set()
    if not sid:
        return said, brief
    hits = sorted(_transcript_dir().glob("*/%s.jsonl" % sid))
    if not hits:
        return said, brief
    try:
        fh = open(hits[0], "rb")
    except OSError:
        return said, brief
    with fh:
        for raw in fh:
            if b'"type":"assistant"' in raw:
                if b"AskUserQuestion" not in raw:
                    continue
                for b in ((_load(raw).get("message") or {}).get("content") or []):
                    if (isinstance(b, dict) and b.get("type") == "tool_use"
                            and b.get("name") == "AskUserQuestion"):
                        asks.add(b.get("id", ""))
                continue
            if b'"type":"attachment"' in raw and b'"queued_command"' in raw:
                att = _load(raw).get("attachment") or {}
                origin = att.get("origin")
                text = att.get("prompt")
                if (att.get("type") == "queued_command" and isinstance(origin, dict)
                        and origin.get("kind") == "human" and isinstance(text, str)
                        and text.strip()):
                    said.append(("prompt", text.strip()))
                continue
            if b'"type":"user"' not in raw:
                continue
            rec = _load(raw)
            content = (rec.get("message") or {}).get("content")
            if b'"tool_result"' in raw:
                for b in (content if isinstance(content, list) else []):
                    if (isinstance(b, dict) and b.get("type") == "tool_result"
                            and b.get("tool_use_id") in asks):
                        text = _block_text(b.get("content")).strip()
                        if text:
                            said.append(("answer", text))
                continue
            kind = prompt_kind(rec)
            text = _block_text(content).strip()
            if kind == "owner" and text:
                said.append(("prompt", text))
            elif kind == "engine" and brief is None:
                m = _LAUNCH.match(text)
                if m:
                    brief = Path(rec.get("cwd") or ".") / m.group(1)
    return said, brief


def scan(cache=None):
    """`{transcript path: entry}` for every transcript, each read only past what `cache`
    holds for it."""
    cache = load_json(SCAN_FILE) if cache is None else cache
    out = {}
    for p in transcripts():
        key = str(p)
        old = cache.get(key)
        try:
            st = p.stat()
            if old and old.get("size") == st.st_size and old.get("mtime") == st.st_mtime:
                out[key] = old
            else:
                out[key] = scan_file(p, old)
        except OSError:
            if old:
                out[key] = old
    return out


# ── the records ────────────────────────────────────────────────────────────

def load_json(name):
    try:
        data = json.loads((project.home() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_json(name, data):
    f = project.home() / name
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(str(tmp), str(f))


def _norm(path):
    """A path in one spelling: `/e/x` and `E:\\x` are `e:/x`."""
    s = str(path or "").strip().replace("\\", "/")
    m = re.match(r"^/([A-Za-z])(/|$)", s)
    if m:
        s = "%s:/%s" % (m.group(1), s[3:])
    return s.rstrip("/").lower()


def filing_root(place, registry):
    """The root of the registered project that one `new` call filed into, or ""."""
    arg, todo_path, cd, cwd = (list(place) + ["", "", "", ""])[:4]
    roots = {_norm(r["path"]): r["path"] for r in registry}
    if arg:
        for r in registry:
            if arg.lower() == r["name"].lower() or _norm(arg) == _norm(r["path"]):
                return _norm(r["path"])
        return ""
    where = _norm(todo_path)
    if not where and cd:
        where = _norm(cd) if re.match(r"^([A-Za-z]:|/)", cd.replace("\\", "/")) \
            else _norm(Path(cwd or ".") / cd)
    where = where or _norm(cwd)
    hits = [r for r in roots if where == r or where.startswith(r + "/")]
    return max(hits, key=len) if hits else ""


def evidence(entries, registry):
    """`{(project root, ticket file name): REQUESTED | AI}` from every transcript. One
    requested filing outweighs any number of others."""
    out = {}
    for e in entries.values():
        for name, requested, place in e.get("filings", []):
            root = filing_root(place, registry)
            if not root:
                continue
            key = (root, name)
            if requested or out.get(key) == REQUESTED:
                out[key] = REQUESTED
            else:
                out[key] = AI
    return out


def named_followups(registry):
    """`{(project root, ticket id)}` that a ticket of another project names under
    `## Follow-ups`: the part of a change for every project that reached that project. A
    follow-up in the naming ticket's own project is a finding of its session."""
    import managercheck
    import todo as T
    roots = {r["name"]: _norm(r["path"]) for r in registry}
    out = set()
    for r in registry:
        tok = _use(r["path"])
        if tok is None:
            continue
        try:
            for t in T.load_all():
                for name, tid in managercheck.named_followups(t.body, roots, r["name"]):
                    if name in roots and name != r["name"]:
                        out.add((roots[name], tid))
        except Exception:                               # noqa: BLE001 - one project only
            pass
        finally:
            project.reset(tok)
    return out


def _use(root):
    try:
        return project.use(root)
    except Exception:                                   # noqa: BLE001 - an unreachable project
        return None


# ── the sweep ──────────────────────────────────────────────────────────────

def _owner_ordered(t):
    return re.search(r"^##\s+Owner's orders\s*$", t.body, re.M) is not None


def candidates(tickets, judged):
    """The tickets of the current project that the sweep judges now."""
    import todo as T
    lanes = (T.lane("start"), T.lane("blocked"))
    return [t for t in tickets
            if t.state in lanes and not t.epic and not t.ai_filed and not t.repeat
            and t.status != T.P.OWNER_BLOCKS and not _owner_ordered(t)
            and t.path.name not in judged]


def verdict(t, by_id, root, proof, followups, seen=()):
    """REQUESTED or AI for one ticket of the current project."""
    if t.ai_filed:
        return AI
    if _owner_ordered(t) or (root, t.id) in followups:
        return REQUESTED
    parent = by_id.get(t.parent) if t.parent else None
    if parent is not None and parent.id not in seen \
            and verdict(parent, by_id, root, proof, followups, seen + (t.id,)) == REQUESTED:
        return REQUESTED
    return proof.get((root, t.path.name), REQUESTED)


def move_to_ai(t):
    """Move one ticket to the `ai` lane and mark it `filed-by: ai`."""
    import todo as T
    meta, body = T.parse(T._read(t.path))
    meta[T.FILED_BY_FIELD] = T.AI_FILED
    meta["status"] = T.default_status(T.lane("ai"))
    dst = T._dest_dir(T.lane("ai"), "") / t.path.name
    T._write(t.path, T.render(meta, body))
    shutil.move(str(t.path), str(dst))


def sweep(registry=None, dry_run=False, out=None, err=None):
    """Judge every unjudged ticket of every registered project, and move each AI ticket to
    the `ai` lane. Returns `[(project name, ticket id, title, lane it left)]`."""
    import todo as T
    registry = project.load_registry() if registry is None else registry
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    judged = load_json(JUDGED_FILE)
    todo = {}
    for r in registry:
        tok = _use(r["path"])
        if tok is None:
            continue
        try:
            tickets = T.load_all()
            cands = candidates(tickets, judged.get(_norm(r["path"]), {}))
            if cands:
                todo[r["name"]] = (r["path"], tickets, cands)
        except Exception as exc:                        # noqa: BLE001 - the others go on
            err.write("ai-filed: %s: %s\n" % (r["name"], exc))
        finally:
            project.reset(tok)
    if not todo:
        return []
    entries = scan()
    if not dry_run:
        save_json(SCAN_FILE, entries)
    proof = evidence(entries, registry)
    followups = named_followups(registry)
    moved = []
    for name, (path, tickets, cands) in todo.items():
        root = _norm(path)
        mine = judged.setdefault(root, {})
        tok = _use(path)
        if tok is None:
            continue
        try:
            by_id = {t.id: t for t in tickets}
            ai = []
            for t in cands:
                v = verdict(t, by_id, root, proof, followups)
                if v == AI:
                    ai.append(t)
                else:
                    mine[t.path.name] = v
            busy = {s["id"] for s in T.live_sessions(ai)} if ai else set()
            here = 0
            for t in ai:
                if t.id in busy:
                    continue
                if dry_run:
                    out.write("would move %s %s \"%s\": %s -> %s\n"
                              % (name, t.id, t.title, T.P.STATE_LABEL[t.state],
                                 T.P.STATE_LABEL[T.lane("ai")]))
                else:
                    move_to_ai(t)
                    out.write("%s %s \"%s\": %s -> %s\n"
                              % (name, t.id, t.title, T.P.STATE_LABEL[t.state],
                                 T.P.STATE_LABEL[T.lane("ai")]))
                mine[t.path.name] = AI
                moved.append((name, t.id, t.title, t.state))
                here += 1
            if here and not dry_run:
                T._prune_empty_dirs()
                T.write_index(quiet=True)
        except Exception as exc:                        # noqa: BLE001 - the others go on
            err.write("ai-filed: %s: %s\n" % (name, exc))
        finally:
            project.reset(tok)
    if not dry_run:
        save_json(JUDGED_FILE, judged)
    return moved


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="octopus ai-filed",
                                 description="move the open tickets that an AI filed without "
                                             "an owner's request to AI-Pipeline, in every "
                                             "registered project")
    ap.add_argument("--dry-run", action="store_true",
                    help="move nothing and record nothing: print each move it would make")
    a = ap.parse_args(argv)
    sweep(dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
