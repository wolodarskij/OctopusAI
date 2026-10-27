"""readme_guard.py - a README file changes only after the owner approved its exact new text.

The rule is `core/rules/readme-needs-owner-approval.md`. This guard makes it hold when an agent
forgets the rule. It runs on `PreToolUse`:

    Claude Code   `octopus hook`               Edit, Write, MultiEdit, NotebookEdit, Bash, PowerShell
    Codex         `octopus status-hook codex`  apply_patch, Bash

A tool call that writes a README file is refused, unless
the owner's approval of that file is recorded for the session, or the rule is switched off for
the project that holds the file (`rule_holds`). The refusal tells the agent to
show the owner the exact new text, ask, and record the yes:

    python .octopus/todo.py approve-readme <file> --session <id> --owner-order "<the order, in neutral words>"

The record of each session is one JSON file in `project.home()/readme-approvals/`, outside every
repository: `{"files": {<normalized path>: {"file", "words", "time"}}}`.

A README file is a file named `README`, or `README.<ext>`, in any case, or a name that starts
with `README` in capitals (`README_ru.md`). `readme_guard.py` is not one.

A shell command is split into its commands, with the quotes taken off each word. Only a word in
a write position counts: the target of a redirection; an argument of `rm`, `mv`, `tee`, `touch`
and the PowerShell file cmdlets; the last argument of `cp`; an argument of `sed -i`, `perl -i`,
`git checkout`, `git restore`, `git rm`, `git mv`; and, in a script that `python -c`, a heredoc
or the PowerShell tool runs, a string literal that is a README path when the script writes a
file. A word is a README path only when its name holds no space, so a note or a message that
holds the word README never counts. A program that writes a README file on its own is not seen;
the rule still binds the agent that runs it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent / "engine") not in sys.path:
    sys.path.insert(0, str(HERE.parent / "engine"))

STATE_DIR = "readme-approvals"
# The optional rule this guard enforces: off in a project, the guard lets every write there.
RULE = "readme-needs-owner-approval"
# A record that nothing touched for this long belongs to a session that is gone.
STALE = 30 * 24 * 3600

FILE_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}
SHELL_TOOLS = ("Bash", "PowerShell")
PATCH_TOOL = "apply_patch"
# The Claude `PreToolUse` matcher of the guard.
CLAUDE_TOOLS = tuple(FILE_TOOLS) + SHELL_TOOLS

_PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)
# The programs that write, move or remove each path they are given.
_WRITERS = {"rm", "mv", "tee", "touch", "truncate", "install", "ln", "unlink", "shred", "del",
            "erase", "move", "ren", "rename", "ri", "mi", "rni", "sc", "ac", "set-content",
            "add-content", "out-file", "new-item", "remove-item", "move-item", "rename-item",
            "clear-content"}
# The programs that write only their last path.
_COPIERS = {"cp", "copy", "copy-item", "cpi"}
# The programs that write their paths only with an in-place option.
_IN_PLACE = {"sed": re.compile(r"^(?:-[a-zA-Z]*i|--in-place)"),
             "perl": re.compile(r"^-[a-zA-Z]*i")}
_GIT_WRITES = {"checkout", "restore", "rm", "mv"}
_INTERPRETERS = {"python", "python3", "py", "node", "perl", "ruby", "pwsh", "powershell"}
_CODE_FLAGS = {"-c", "-e", "-command", "--eval"}
_PREFIXES = {"sudo", "env", "command", "exec", "time", "nohup"}
# Longest first: the lexer takes the first that matches.
_OPERATORS = ("<<<", "<<-", "&&", "||", ">>", "<<", ">&", "&>", ";", "|", "&", ">", "<", "\n")
_ENDS = {"&&", "||", ";", "|", "&", "\n"}
_REDIRECTS = {">", ">>", "&>", ">&"}
_LITERAL = re.compile(r"\"([^\"\n]*)\"|'([^'\n]*)'")
_SCRIPT_WRITES = re.compile(
    r"write_text|write_bytes|\.write\(|writeFile|appendFile|WriteAll(?:Text|Lines|Bytes)|"
    r"AppendAll(?:Text|Lines)|open\([^)]*['\"](?:w|a|wb|ab|w\+|r\+)['\"]|os\.remove|os\.rename|"
    r"os\.replace|shutil\.(?:copy|move)|\.unlink\(|Set-Content|Add-Content|Out-File", re.I)


def _name(path):
    return re.split(r"[\\/]", str(path).strip().strip("\"'"))[-1]


def is_readme(path):
    """Whether `path` names a README file: `README` or `README.<ext>` in any case, or a name
    that starts with `README` in capitals."""
    name = _name(path)
    return bool(re.match(r"readme(?:\.|$)", name, re.I)) or name.startswith("README")


def is_readme_path(word, file_only=False):
    """Whether one shell word is a path to a README file, not text that holds the word: its name
    holds no space, and the word holds a space only inside a folder name. With `file_only`, a
    bare `README` with no folder and no extension does not count."""
    w = str(word).strip()
    name = _name(w)
    if not is_readme(name) or re.search(r"\s", name):
        return False
    if re.search(r"\s", w) and not re.search(r"[\\/]", w):
        return False
    return not file_only or "." in name or bool(re.search(r"[\\/]", w))


def absolute(path, cwd=None):
    """`path` absolute against `cwd`, with `/d/...` of Git Bash as `D:/...` on Windows."""
    p = str(path).strip().strip("\"'")
    if os.name == "nt":
        m = re.match(r"^/([a-zA-Z])(/.*)?$", p)
        if m:
            p = "%s:%s" % (m.group(1).upper(), m.group(2) or "/")
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return os.path.normpath(p).replace("\\", "/")


def normalize(path, cwd=None):
    """`path` as one spelling: `absolute`, in the case Windows compares."""
    return os.path.normcase(absolute(path, cwd)).replace("\\", "/")


def _heredoc(command, i, delimiter):
    """`(body, end)`: the heredoc body that starts at `i`, the line after its operator, and the
    index after its closing line."""
    lines, j = [], i
    while j < len(command):
        k = command.find("\n", j)
        k = len(command) if k < 0 else k
        line = command[j:k]
        if line.strip() == delimiter:
            return "\n".join(lines), k + 1
        lines.append(line)
        j = k + 1
    return "\n".join(lines), len(command)


def _lex(command, posix=True):
    """The tokens of a shell command, each `[kind, text, body]`: kind `word` (its quotes taken
    off) or `op`. A `<<` token carries its heredoc body. `posix` reads `\\` as an escape, as
    bash does; PowerShell keeps it."""
    tokens, waiting = [], []
    word, has = [], False
    i, n = 0, len(command)

    def flush():
        nonlocal word, has
        if has:
            tokens.append(["word", "".join(word), None])
        word, has = [], False

    while i < n:
        c = command[i]
        if posix and c == "\\" and i + 1 < n:
            if command[i + 1] != "\n":
                word.append(command[i + 1])
                has = True
            i += 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and command[j] != c:
                if posix and c == '"' and command[j] == "\\" and j + 1 < n:
                    j += 1
                word.append(command[j])
                j += 1
            has = True
            i = j + 1
            continue
        if c in " \t\r":
            flush()
            i += 1
            continue
        op = next((o for o in _OPERATORS if command.startswith(o, i)), None)
        if op is None:
            word.append(c)
            has = True
            i += 1
            continue
        flush()
        tokens.append(["op", op, None])
        i += len(op)
        if op in ("<<", "<<-"):
            waiting.append(len(tokens) - 1)
        elif op == "\n":
            for k in waiting:
                after = tokens[k + 1] if k + 1 < len(tokens) else None
                if after and after[0] == "word":
                    tokens[k][2], i = _heredoc(command, i, after[1])
            waiting = []
    flush()
    return tokens


def _commands(tokens):
    """The simple commands of the tokens: `(words, redirect targets, heredoc bodies)` each."""
    out, cmd = [], []
    for t in tokens + [["op", "\n", None]]:
        if t[0] == "op" and t[1] in _ENDS:
            if cmd:
                words, targets, bodies = [], [], []
                k = 0
                while k < len(cmd):
                    kind, text, body = cmd[k]
                    if kind == "word":
                        words.append(text)
                    elif k + 1 < len(cmd) and cmd[k + 1][0] == "word":
                        if text in _REDIRECTS:
                            targets.append(cmd[k + 1][1])
                        if body is not None:
                            bodies.append(body)
                        k += 1
                    k += 1
                out.append((words, targets, bodies))
            cmd = []
        else:
            cmd.append(t)
    return out


def _program(words):
    """`(program, arguments)`: the program in lower case without its folder and `.exe`, after
    the variable settings and the prefixes such as `sudo`."""
    k = 0
    while k < len(words) and (re.match(r"^[A-Za-z_]\w*=", words[k])
                              or words[k].lower() in _PREFIXES):
        k += 1
    if k >= len(words):
        return "", []
    prog = _name(words[k]).lower()
    if prog.endswith(".exe"):
        prog = prog[:-4]
    return prog, words[k + 1:]


def _script_targets(code):
    """Each README path that a string literal of the script `code` names, when the script
    writes a file."""
    if not _SCRIPT_WRITES.search(code):
        return []
    return [s for m in _LITERAL.finditer(code) for s in (m.group(1) if m.group(1) is not None
                                                          else m.group(2),)
            if is_readme_path(s, file_only=True)]


def _command_targets(words, targets, bodies):
    """Each README path that one simple command writes."""
    out = [t for t in targets if is_readme_path(t)]
    prog, args = _program(words)
    plain = [a for a in args if not a.startswith("-")]
    if prog in _WRITERS:
        out += [a for a in plain if is_readme_path(a)]
    elif prog == "dd":
        out += [a[3:] for a in args if a.startswith("of=") and is_readme_path(a[3:])]
    elif prog in _COPIERS:
        out += [plain[-1]] if plain and is_readme_path(plain[-1]) else []
    elif prog in _IN_PLACE and any(_IN_PLACE[prog].match(a) for a in args):
        out += [a for a in plain if is_readme_path(a)]
    elif prog == "git" and plain and plain[0] in _GIT_WRITES:
        out += [a for a in plain[1:] if is_readme_path(a)]
    if prog in _INTERPRETERS:
        for k, a in enumerate(args[:-1]):
            if a.lower() in _CODE_FLAGS:
                out += _script_targets(args[k + 1])
        for body in bodies:
            out += _script_targets(body)
    return out


def _shell_targets(command, powershell=False):
    """Each README file the shell `command` writes."""
    out = []
    for words, targets, bodies in _commands(_lex(command, posix=not powershell)):
        out += _command_targets(words, targets, bodies)
    if powershell:
        out += _script_targets(command)
    return list(dict.fromkeys(out))


def targets(tool, tool_input):
    """Each README file that the tool call `tool` with `tool_input` writes, as it names it."""
    if not isinstance(tool_input, dict):
        return []
    if tool in FILE_TOOLS:
        path = tool_input.get(FILE_TOOLS[tool])
        return [path] if isinstance(path, str) and is_readme(path) else []
    command = tool_input.get("command")
    if isinstance(command, list):
        command = " ".join(str(c) for c in command)
    if not isinstance(command, str):
        return []
    if tool == PATCH_TOOL:
        return [m.group(1) for m in _PATCH_FILE.finditer(command) if is_readme(m.group(1))]
    if tool in SHELL_TOOLS:
        return _shell_targets(command, powershell=tool == "PowerShell")
    return []


def session_key(session):
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session or "").strip())[:120]
    return s or None


def state_dir():
    import project
    return project.home() / STATE_DIR


def _load(p):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def approve(session, path, words, cwd=None, base=None):
    """Record the owner's approval of the README file `path` for `session`. Returns the
    normalized path. Raises ValueError when a part is missing."""
    key = session_key(session)
    if not key:
        raise ValueError("no session id: pass --session <id>, the id the refusal names")
    if not is_readme(path):
        raise ValueError("%s is not a README file" % path)
    if not (isinstance(words, str) and words.strip()):
        raise ValueError("--owner-order needs the owner's approval, stated in neutral words")
    d = Path(base) if base else state_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / (key + ".json")
    rec = _load(p)
    files = rec.setdefault("files", {})
    norm = normalize(path, cwd)
    files[norm] = {"file": str(path), "words": words.strip(), "time": int(time.time())}
    tmp = p.with_name(p.name + ".tmp%d" % os.getpid())
    tmp.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(p))
    _prune(d)
    return norm


def approved(session, path, cwd=None, base=None):
    """Whether the owner's approval of `path` is recorded for `session`."""
    key = session_key(session)
    if not key:
        return False
    d = Path(base) if base else state_dir()
    files = _load(d / (key + ".json")).get("files", {})
    return isinstance(files, dict) and normalize(path, cwd) in files


def _prune(d):
    now = time.time()
    try:
        for f in d.glob("*.json"):
            if now - f.stat().st_mtime > STALE:
                f.unlink()
    except OSError:
        pass


def rule_holds(path):
    """Whether the README rule is on for the absolute `path`: in the project that holds the
    file, else by the global switch (`project.rule_enabled`). A fault reads as on."""
    try:
        import project
        return project.rule_enabled(project.find_root(os.path.dirname(path)), RULE)
    except Exception:                                   # noqa: BLE001 - a hook never raises
        return True


def _approve_command(root, session, path):
    cli = ('python "%s"' % (Path(root) / ".octopus" / "todo.py").as_posix() if root
           else 'python "%s" todo' % (HERE.parent.parent / "octopus.py").as_posix())
    return '%s approve-readme "%s" --session %s --owner-order "<the order, in neutral words>"' % (
        cli, path, session)


def reason(paths, session, root=None):
    """The refusal the agent reads."""
    lines = ["Refused: %s is a README file. The owner's order, 2026-10-27: every README change "
             "goes through the owner, and no agent makes one without the owner's approval."
             % ", ".join(paths),
             "Show the owner the exact new text of the change, ask, and wait for the answer. "
             "Write nothing to the file without a yes.",
             "When the owner says yes, record it, then make the change:"]
    lines += ["  " + _approve_command(root, session, p) for p in paths]
    return "\n".join(lines)


def decide(payload, base=None):
    """The `PreToolUse` answer for one tool call: a refusal, or None to let it run. Never
    raises."""
    try:
        if str(payload.get("hook_event_name") or "") != "PreToolUse":
            return None
        cwd = str(payload.get("cwd") or "") or None
        found = targets(str(payload.get("tool_name") or ""), payload.get("tool_input"))
        session = str(payload.get("session_id") or "")
        waiting = [p for p in dict.fromkeys(found) if not approved(session, p, cwd, base)]
        if not waiting:
            return None
        waiting = [p for p in (absolute(p, cwd) for p in waiting) if rule_holds(p)]
        if not waiting:
            return None
        root = None
        try:
            import project
            root = project.find_root(cwd) if cwd else None
        except Exception:                               # noqa: BLE001 - no project there
            root = None
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason(waiting, session,
                                                                          root)}}
    except Exception:                                   # noqa: BLE001 - a hook never raises
        return None
