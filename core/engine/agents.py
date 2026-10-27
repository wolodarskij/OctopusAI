"""agents.py - one copy of a project's rules and skills, and the entry file each AI provider reads.

THE LAYOUT. The rules and the skills live once, in `.agents/`, where no AI provider owns them:

    .agents/rules/octopus/<ruleset>/*.md   the Octopus AI rules (the payload of each ruleset)
    .agents/rules/**/*.md                  the project's own rules
    .agents/skills/<skill>/SKILL.md        every skill, Octopus AI's and the project's own

Each AI provider reads its own entry file, and each entry only points at `.agents/`:

    AGENTS.md                    Codex: a block that lists every rule file and names the skills
                                 directory
    CLAUDE.md                    Claude Code: a block that imports `AGENTS.md` and every rule file
    .codex/hooks.json            Codex: the hook lines of the status reminder (`STATUS_HOOKS`)
                                 and of the README guard (`GUARD_EVENTS`), merged into the
                                 project's own hooks
    .claude/skills/<skill>/      Claude Code: a pointer, the frontmatter of the skill and one
                                 line that sends Claude to `.agents/skills/<skill>/SKILL.md`

`AGENTS.md` also carries the no-narration rule itself (`NO_NARRATION`): Codex loads it and not
the files it lists. `reporting_problems` names each entry or setting that lets an agent of a
project miss the rule; `todo.py check` and `octopus status` report them.

Codex reads `.agents/skills/` itself. A block sits between BEGIN and END; the text outside it
belongs to the project and is never touched. The pointers are whole generated files, recorded
in the install record; a file at their path that Octopus AI did not write stays as it is. A
hook file can hold the project's own hooks too: `write_entries`
adds the status hook lines to it, and `remove_entries` takes only them out. `write_entries` derives every entry from what `.agents/` holds,
so a rule or a skill added there reaches every AI provider at the next `update` or session start.

ADOPT moves what a project kept in Claude's own places into `.agents/`: its rules from
`.claude/rules/`, its skills from `.claude/skills/`, and the text of a `CLAUDE.md` into an
`AGENTS.md` that has none of its own. A rule with `paths:` frontmatter stays in
`.claude/rules/`, because only Claude reads that scope and an import would load it always.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
from pathlib import Path

AGENTS_DIR = ".agents"
RULES = ".agents/rules"
SKILLS = ".agents/skills"
OCTOPUS_RULES = ".agents/rules/octopus"
CLAUDE_RULES = ".claude/rules"
CLAUDE_SKILLS = ".claude/skills"
ENTRY_FILES = ("AGENTS.md", "CLAUDE.md")
# The record's owner of every generated whole file.
OWNER = "agents"

# The payload paths before the one copy moved to `.agents/`. `install` moves a file it
# recorded under one of them.
MOVED = ((".claude/rules/octopus/", OCTOPUS_RULES + "/"), (CLAUDE_SKILLS + "/", SKILLS + "/"))

BEGIN = ("<!-- octopus:begin - generated from .agents/ by Octopus AI. Edit the files in "
         ".agents/, not this block. -->")
END = "<!-- octopus:end -->"
_BLOCK = re.compile(r"[ \t]*<!-- octopus:begin\b.*?<!-- octopus:end -->[ \t]*\r?\n?", re.S)
POINTER = "<!-- octopus:skill-pointer -->"
GENERATED_MARK = "<!-- octopus:generated -->"
# A line of a CLAUDE.md that imports one rule file of `.agents/rules/` by hand.
_HAND_IMPORT = re.compile(r"(?m)^@\.agents/rules/\S+[ \t]*(?:\r?\n|\Z)")

# The rule every message to the owner obeys, in full in `reporting-to-the-owner.md`. The
# `AGENTS.md` block carries it, because Codex loads that and not the files it lists; every
# Claude launch carries it in its system prompt.
REPORTING_RULE = "reporting-to-the-owner.md"
NO_NARRATION = (
    "**Never narrate your own work.** In every message to the owner, mid-task and final, write "
    "only what was asked for: the answer, the files changed, a problem that stopped the work, a "
    "question. Never write a plan or a next step, what you read, check or run, what a tool "
    "printed, your reasoning, or a summary of your steps: no \"I will use an edit tool to edit "
    "...\", no \"Let me ...\", no \"Checking ...\", no \"Reading ...\". **One status line is "
    "allowed:** at the start of your run, and again when about 5 minutes passed since your last "
    "message, write one short sentence: what you work on now. Never at every step, and nothing "
    "more than that sentence.")
# What Claude Code's mid-turn reminder says to the agent. A `cmd` `set` line carries it, so it
# holds no double quote and none of `& | < > ^ %`.
STATUS_REMINDER = ("About 5 minutes passed since your last message to the owner. Write one "
                   "short sentence: what you work on now. Then continue, and write nothing else "
                   "about your work.")
# The env that makes Claude Code's mid-turn reminder the 5-minute trigger of the status line:
# it fires once 300 seconds passed since the agent's last visible text, its last reminder or
# the owner's prompt, and says `STATUS_REMINDER`.
STATUS_ENV = {"CLAUDE_CODE_SILENT_TURN_REMINDER": "1",
              "CLAUDE_CODE_SILENT_TURN_REMINDER_SECONDS": "300",
              "CLAUDE_CODE_SILENT_TURN_REMINDER_TEXT": STATUS_REMINDER}
# Codex has no timer of its own. Each AI provider's hook file runs `octopus
# status-hook <provider>` (`core/hooks/status_reminder.py`) on these events: each event but the
# last starts the clock of the reminder again, and the last, after a tool call, adds
# `STATUS_REMINDER` once 300 seconds passed.
STATUS_HOOKS = {
    "codex": (".codex/hooks.json", ("SessionStart", "UserPromptSubmit", "Stop", "PostToolUse")),
}
# The README guard (`core/hooks/readme_guard.py`) answers these events of the same hook line.
GUARD_EVENTS = {"codex": ("PreToolUse",)}
STATUS_HOOK_TIMEOUT = 10


def new_path(rel):
    """The `.agents/` path of a payload file recorded under its old Claude path, or None."""
    for old, new in MOVED:
        if rel.startswith(old):
            return new + rel[len(old):]
    return None


# ── what `.agents/` holds ────────────────────────────────────────────────────

def rule_files(root, rulesets=()):
    """Every rule file under `.agents/rules/`, project-relative: the Octopus AI rules in the
    order of `rulesets`, then the project's own, sorted."""
    root = Path(root)
    base = root / RULES
    if not base.is_dir():
        return []
    every = sorted(p.relative_to(root).as_posix() for p in base.rglob("*.md") if p.is_file())
    order = {n: i for i, n in enumerate(rulesets)}

    def key(rel):
        parts = rel.split("/")
        if rel.startswith(OCTOPUS_RULES + "/") and len(parts) > 4:
            return (0, order.get(parts[3], len(order)), rel)
        return (1, 0, rel)
    return sorted(every, key=key)


def skill_names(root):
    """Every skill under `.agents/skills/`: a directory that holds a `SKILL.md`."""
    base = Path(root) / SKILLS
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir() if (d / "SKILL.md").is_file())


def frontmatter(text):
    """The YAML frontmatter of a markdown file, without its fences, or ""."""
    m = re.match(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", text, re.S)
    return m.group(1) if m else ""


# ── the generated text ──────────────────────────────────────────────────────

def agents_block(rules, skills, cli):
    lines = [BEGIN, "## Octopus AI", "",
             "The rules and the skills of this project live once, in `.agents/`. Every agent "
             "reads the same files.", "", NO_NARRATION, "",
             "**The rules.** Read each file below before your first change. Claude Code has "
             "them loaded already, through `CLAUDE.md`.", ""]
    lines += ["- `%s`" % r for r in rules] or ["- (none yet)"]
    lines += ["", "**The skills** are in `.agents/skills/<name>/SKILL.md`. Codex finds them "
              "there; Claude Code finds them through `.claude/skills/`."]
    if skills:
        lines += ["", "Skills: %s." % ", ".join("`%s`" % s for s in skills)]
    lines += ["", "**The tickets:** `%s --help`." % cli, END]
    return "\n".join(lines) + "\n"


def claude_block(rules):
    return "\n".join([BEGIN, "@AGENTS.md"] + ["@%s" % r for r in rules] + [END]) + "\n"


def skill_pointer(name, source_text):
    """The `.claude/skills/<name>/SKILL.md` that sends Claude to the one copy."""
    fm = frontmatter(source_text) or "name: %s" % name
    src = "%s/%s/SKILL.md" % (SKILLS, name)
    return ("---\n%s\n---\n%s\n"
            "This skill lives in `%s/%s/`. Read `%s` now and follow it; the files it names sit "
            "beside it. Generated by Octopus AI: edit that file, not this one.\n\n"
            "Arguments: $ARGUMENTS\n" % (fm, POINTER, SKILLS, name, src))


def hook_events(provider):
    """Every event the hook file of `provider` runs the status hook line on."""
    return STATUS_HOOKS[provider][1] + GUARD_EVENTS.get(provider, ())


def is_status_hook(cmd, provider=None):
    """Whether `cmd` is the status hook line of `provider` that some install wrote."""
    if not isinstance(cmd, str):
        return False
    try:
        words = shlex.split(cmd)
    except ValueError:
        return False
    if words and words[0] == "&":
        words = words[1:]
    if len(words) != 4:
        return False
    exe, script, action, pid = words
    # Accept the original PATH-based commands and absolute interpreter paths.
    # PowerShell's doubled apostrophes may collapse in shlex; only the script's
    # basename matters here, so either spelling still identifies our generated hook.
    return (bool(exe == "python" or re.match(r"^(?:/|[A-Za-z]:[/\\]|\\\\)", exe))
            and script.replace("\\", "/").rsplit("/", 1)[-1] in ("octopus.py", "aitools.py")
            and action == "status-hook" and bool(re.fullmatch(r"[a-z][a-z0-9_-]*", pid))
            and (provider is None or pid == provider))


def _hook_lists(d):
    """`{event: list}` of the hook file `d`, or None when `d` does not have the shape of one."""
    hooks = d.get("hooks", {})
    if not isinstance(hooks, dict) or not all(isinstance(v, list) for v in hooks.values()):
        return None
    return hooks


def _without_status_hook(groups, provider):
    """The hook list of one event without the status hook lines. A Codex group holds its
    commands in `hooks`; a flat hook entry is one command."""
    out = []
    for g in groups:
        if not isinstance(g, dict):
            out.append(g)
        elif isinstance(g.get("hooks"), list):
            kept = [h for h in g["hooks"]
                    if not (isinstance(h, dict) and is_status_hook(h.get("command"), provider))]
            if kept or not g["hooks"]:
                out.append(dict(g, hooks=kept))
        elif not is_status_hook(g.get("command"), provider):
            out.append(g)
    return out


def _has_status_hook(groups, provider):
    return any(isinstance(g, dict) and isinstance(g.get("hooks"), list)
               and any(isinstance(h, dict) and is_status_hook(h.get("command"), provider)
                       for h in g["hooks"])
               for g in groups)


def _json_text(d):
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


def set_status_hooks(text, provider, line):
    """The hook file `text` ("" or None: no file) of `provider` with `line` on each of its
    events, and every hook of the project's own kept. None when `text` is not a hook file."""
    try:
        d = json.loads(text) if text and text.strip() else {}
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    d.setdefault("hooks", {})
    hooks = _hook_lists(d)
    if hooks is None:
        return None
    hook = {"command": line, "timeout": STATUS_HOOK_TIMEOUT}
    for ev in hook_events(provider):
        groups = _without_status_hook(hooks.get(ev, []), provider)
        groups.append({"hooks": [dict({"type": "command"}, **hook)]})
        hooks[ev] = groups
    return _json_text(d)


def strip_status_hooks(text, provider=None):
    """The hook file `text` without the status hook lines; "" when nothing else is left.
    `text` itself when it is not a hook file."""
    try:
        d = json.loads(text) if text and text.strip() else {}
    except ValueError:
        return text
    hooks = _hook_lists(d) if isinstance(d, dict) else None
    if hooks is None:
        return text
    for ev in list(hooks):
        groups = _without_status_hook(hooks[ev], provider)
        if groups:
            hooks[ev] = groups
        else:
            del hooks[ev]
    if not hooks:
        d.pop("hooks", None)
    if not d or set(d) == {"version"}:
        return ""
    return _json_text(d)


def status_hook_problem(root, provider):
    """Why the hook file of `provider` does not run the status hook on each of its events, or
    None."""
    rel = STATUS_HOOKS[provider][0]
    events = hook_events(provider)
    p = Path(root) / rel
    try:
        d = json.loads(_read(p)) if p.is_file() else None
    except (OSError, ValueError):
        d = None
    hooks = _hook_lists(d) if isinstance(d, dict) else None
    if hooks is None:
        return ("%s is %s, so Codex does not ask for a status line once every 5 minutes - run: "
                "octopus update" % (rel, "not a hook file" if p.is_file() else "missing"))
    missing = [ev for ev in events if not _has_status_hook(hooks.get(ev, []), provider)]
    if missing:
        return ("%s does not run the status hook on %s, so Codex does not ask for a status line "
                "once every 5 minutes - run: octopus update" % (rel, ", ".join(missing)))
    return None


def is_pointer(path):
    """Whether the file is one `write_entries` generates: a skill pointer or another generated entry."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return POINTER in text or GENERATED_MARK in text


# ── blocks ──────────────────────────────────────────────────────────────────

def _read(p):
    with open(p, "r", encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def set_block(text, block):
    """`text` with its generated block replaced by `block`, or `block` added at its end."""
    if "\r\n" in text:
        block = block.replace("\n", "\r\n")
    if _BLOCK.search(text):
        return _BLOCK.sub(lambda _m: block, text, count=1)
    if not text.strip():
        return block
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.rstrip("\r\n") + nl + nl + block


def strip_block(text):
    """`text` with its generated block taken out, and the blank line `set_block` put before a
    block it added at the end."""
    out = _BLOCK.sub("", text)
    if not out.strip():
        return ""
    if not text.rstrip().endswith(END):
        return out
    return out.rstrip("\r\n") + ("\r\n" if "\r\n" in text else "\n")


def own_text(text):
    """The text of an entry file outside the generated block."""
    return strip_block(text).strip()


# ── write and remove ────────────────────────────────────────────────────────

def write_entries(root, rec, rulesets=(), cli="python .octopus/todo.py", out=print, hook=None):
    """Write every entry file from what `.agents/` holds. `rec` is the install record: the
    blocks and the hook files go to `rec.entries`, the whole files to `rec.files`. `hook` is
    the command `octopus status-hook` without its provider; None writes no hook file.
    Returns the project-relative paths written or removed."""
    root = Path(root)
    rules = rule_files(root, rulesets)
    skills = skill_names(root)
    changed = []
    for rel, block in (("AGENTS.md", agents_block(rules, skills, cli)),
                       ("CLAUDE.md", claude_block(rules))):
        p = root / rel
        old = _read(p) if p.is_file() else None
        new = set_block(old or "", block)
        if rel not in rec.entries:
            rec.entries[rel] = {"created": old is None}
        if new != old:
            _write(p, new)
            changed.append(rel)
    wanted = {}
    for s in skills:
        src = _read(root / SKILLS / s / "SKILL.md")
        wanted["%s/%s/SKILL.md" % (CLAUDE_SKILLS, s)] = skill_pointer(s, src)
    for rel, text in wanted.items():
        p = root / rel
        if p.exists() and not is_pointer(p):
            continue                                    # the project's own file
        if rec.put(rel, text.encode("utf-8"), OWNER, replace=True) == "written":
            changed.append(rel)
    # Remove generated files no longer offered by this installation.
    for rel in sorted(r for r, v in rec.files.items() if v.get("by") == OWNER and r not in wanted):
        (root / rel).unlink(missing_ok=True)
        del rec.files[rel]
        _prune(root, (root / rel).parent, stop=root)
        changed.append(rel)
    active_hooks = {rel for rel, _events in STATUS_HOOKS.values()}
    for rel, info in list(rec.entries.items()):
        if not rel.endswith("/hooks.json") or rel in active_hooks:
            continue
        p = root / rel
        if p.is_file():
            old = _read(p)
            new = strip_status_hooks(old)
            if not new.strip() and info.get("created"):
                p.unlink()
                _prune(root, p.parent, stop=root)
                changed.append(rel)
            elif new != old:
                _write(p, new)
                changed.append(rel)
        del rec.entries[rel]
    for provider, (rel, _events) in STATUS_HOOKS.items() if hook else ():
        p = root / rel
        old = _read(p) if p.is_file() else None
        new = set_status_hooks(old, provider, "%s %s" % (hook, provider))
        if new is None:
            continue                                    # the project's own file, not a hook file
        if rel not in rec.entries:
            rec.entries[rel] = {"created": old is None}
        if new != old:
            _write(p, new)
            changed.append(rel)
    return changed


def remove_entries(root, rec):
    """Take the generated blocks out of `AGENTS.md` and `CLAUDE.md`, and the status hook lines
    out of the hook files; a file Octopus AI created and that holds nothing else goes. The
    whole generated files are in `rec.files`, and uninstall removes them with the rest."""
    root = Path(root)
    hook_files = {rel: provider for provider, (rel, _events) in STATUS_HOOKS.items()}
    for rel, info in list(rec.entries.items()):
        p = root / rel
        if p.is_file():
            if rel.endswith("/hooks.json"):
                text = strip_status_hooks(_read(p), hook_files.get(rel))
            else:
                text = strip_block(_read(p))
            if not text.strip() and info.get("created"):
                p.unlink()
                if rel.endswith("/hooks.json"):
                    _prune(root, p.parent, stop=root)
            else:
                _write(p, text)
        del rec.entries[rel]


def reporting_problems(root):
    """Each way the project at `root` fails to load the no-narration rule into an agent, as
    one line each; [] when every AI provider loads it. Claude Code needs the import in
    `CLAUDE.md` and `STATUS_ENV` in `.claude/settings.json`; Codex needs the rule in
    `AGENTS.md` and the status hook in its hook file."""
    root = Path(root)
    rule = "%s/core/%s" % (OCTOPUS_RULES, REPORTING_RULE)
    problems = []
    if not (root / rule).is_file():
        problems.append("%s is missing - run: octopus update" % rule)
    claude = _read(root / "CLAUDE.md") if (root / "CLAUDE.md").is_file() else ""
    if "@" + rule not in [line.strip() for line in claude.splitlines()]:
        problems.append("CLAUDE.md does not import %s, so Claude Code does not load it" % rule)
    p = root / "AGENTS.md"
    if not p.is_file():
        problems.append("AGENTS.md is missing, so Codex does not load the no-narration rule")
    elif NO_NARRATION not in _read(p):
        problems.append("AGENTS.md does not carry the no-narration rule - run: octopus update")
    try:
        env = json.loads(_read(root / ".claude" / "settings.json")).get("env") or {}
    except (OSError, ValueError, AttributeError):
        env = {}
    for k, v in STATUS_ENV.items():
        if env.get(k) != v:
            problems.append(".claude/settings.json does not set %s as Octopus AI does, so Claude "
                            "Code does not ask for a status line once every 5 minutes - run: "
                            "octopus update" % k)
    for provider in STATUS_HOOKS:
        problem = status_hook_problem(root, provider)
        if problem:
            problems.append(problem)
    return problems


def _prune(root, d, stop):
    """Remove `d` and its parents up to `stop` while they are empty."""
    d, stop = Path(d), Path(stop)
    while d != stop and d != Path(root) and stop in d.parents:
        try:
            if any(d.iterdir()):
                return
            d.rmdir()
        except OSError:
            return
        d = d.parent


# ── adopt ───────────────────────────────────────────────────────────────────

def _same_tree(a, b):
    fa = sorted(p.relative_to(a).as_posix() for p in Path(a).rglob("*") if p.is_file())
    fb = sorted(p.relative_to(b).as_posix() for p in Path(b).rglob("*") if p.is_file())
    return fa == fb and all((Path(a) / f).read_bytes() == (Path(b) / f).read_bytes() for f in fa)


def _move(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def adopt(root, recorded=(), dry_run=False, out=print):
    """Move the project's own rules and skills out of Claude's places into `.agents/`, and the
    text of `CLAUDE.md` into an `AGENTS.md` that has none of its own. `recorded` holds the
    paths Octopus AI wrote, which are not the project's. Returns `{moved, dropped, kept,
    rewritten}`: lists of project-relative paths (a move as `old -> new`)."""
    root = Path(root)
    recorded = set(recorded)
    res = {"moved": [], "dropped": [], "kept": [], "rewritten": []}
    renames = []                                        # (old prefix, new prefix)

    rules_dir = root / CLAUDE_RULES
    if rules_dir.is_dir():
        for p in sorted(rules_dir.rglob("*.md")):
            rel = p.relative_to(root).as_posix()
            if rel in recorded:
                continue
            text = _read(p)
            if re.search(r"(?m)^paths:", frontmatter(text)):
                res["kept"].append("%s (paths: scope, Claude only)" % rel)
                continue
            dst_rel = RULES + rel[len(CLAUDE_RULES):]
            dst = root / dst_rel
            if dst.exists():
                if dst.read_bytes() == p.read_bytes():
                    res["dropped"].append(rel)
                    if not dry_run:
                        p.unlink()
                else:
                    res["kept"].append("%s (%s differs)" % (rel, dst_rel))
                continue
            res["moved"].append("%s -> %s" % (rel, dst_rel))
            renames.append((rel, dst_rel))
            if not dry_run:
                _move(p, dst)

    skills_dir = root / CLAUDE_SKILLS
    if skills_dir.is_dir():
        for p in sorted(skills_dir.iterdir()):
            rel = p.relative_to(root).as_posix()
            if any(r == rel or r.startswith(rel + "/") for r in recorded):
                continue
            if p.is_dir() and is_pointer(p / "SKILL.md"):
                continue
            dst_rel = SKILLS + rel[len(CLAUDE_SKILLS):]
            dst = root / dst_rel
            if dst.exists():
                same = _same_tree(p, dst) if p.is_dir() and dst.is_dir() else \
                    (p.is_file() and dst.is_file() and p.read_bytes() == dst.read_bytes())
                if same:
                    res["dropped"].append(rel)
                    renames.append((rel + ("/" if p.is_dir() else ""),
                                    dst_rel + ("/" if p.is_dir() else "")))
                    if not dry_run:
                        shutil.rmtree(p) if p.is_dir() else p.unlink()
                else:
                    res["kept"].append("%s (%s differs)" % (rel, dst_rel))
                continue
            res["moved"].append("%s -> %s" % (rel, dst_rel))
            renames.append((rel + ("/" if p.is_dir() else ""), dst_rel + ("/" if p.is_dir() else "")))
            if not dry_run:
                _move(p, dst)
    if not dry_run and rules_dir.is_dir():
        for d in sorted((x for x in rules_dir.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
            _prune(root, d, stop=root / ".claude")
        _prune(root, rules_dir, stop=root / ".claude")

    # The text that named the old places names the new ones. Once no rule is left in
    # `.claude/rules/`, a mention of the directory itself names `.agents/rules/`.
    if renames and not dry_run and not (root / CLAUDE_RULES).exists():
        renames.append((CLAUDE_RULES + "/", RULES + "/"))
    if renames:
        texts = [root / f for f in ENTRY_FILES] + list((root / AGENTS_DIR).rglob("*.md"))
        for f in texts:
            rel = f.relative_to(root).as_posix()
            if not f.is_file() or rel in recorded:
                continue
            old = _read(f)
            new = old
            for a, b in sorted(renames, key=lambda x: -len(x[0])):
                new = re.sub(r"(?<![\w/.])" + re.escape(a), b, new)
            if new != old:
                res["rewritten"].append(rel)
                if not dry_run:
                    _write(f, new)

    # A hand import of a rule in `.agents/rules/` is one the generated block makes already.
    # CLAUDE.md's own text goes to an AGENTS.md that has none, and CLAUDE.md keeps the block.
    claude, agents = root / "CLAUDE.md", root / "AGENTS.md"
    ctext = _read(claude) if claude.is_file() else ""
    atext = _read(agents) if agents.is_file() else ""
    body = _HAND_IMPORT.sub("", strip_block(ctext))
    block = _BLOCK.search(ctext)
    if body.strip() and not own_text(atext):
        res["moved"].append("CLAUDE.md (its own text) -> AGENTS.md")
        if not dry_run:
            ablock = _BLOCK.search(atext)
            _write(agents, set_block(body, ablock.group(0)) if ablock
                   else body.rstrip("\r\n") + "\n")
            _write(claude, block.group(0) if block else "")
    else:
        if body.strip():
            res["kept"].append("CLAUDE.md (its own text; AGENTS.md has its own too)")
        if not dry_run and ctext and body != strip_block(ctext):
            res["rewritten"].append("CLAUDE.md")
            _write(claude, set_block(body, block.group(0)) if block else body)
    return res
