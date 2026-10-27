"""providers.py - built-in Claude Code and OpenAI Codex, and the owner's custom CLIs.
A provider is the AI tool; one running session of it on a ticket is an agent.

THE LIST. Claude Code and Codex are built in. The owner's list is `providers.json` beside the project
registry (`project.home()`), outside every tracked tree:

    {"providers": [{"id": "codex", "env": {...}},               an override of a built-in
                {"id": "local", "label": "...", "dialect": "codex", "command": "codex", ...}],
     "checks": {"local": {"at": ..., "ok": true, ...}}}        the last check of each

An older install kept the same list as `agents.json` under the key `agents`; the first read
moves it to `providers.json` (`_migrate`).

An entry with a built-in id overrides the fields it names; any other id is a provider of its
own. Removing an override restores the built-in; a built-in itself is never removed.

THE FIELDS of a provider:

    label                  the name the board shows
    dialect                how a launch's command line is built: `claude` or `codex` (the
                           launch of that CLI, `todo.py`), or `template` (the fields below)
    command, args          the executable; the arguments every launch and every check carries first
    approvalPolicy        Codex interactive launches: empty inherits its config, or on-request,
                           untrusted, never. Headless answer calls remain noninteractive.
    prompt                 template: where the prompt goes, `["{prompt}"]`
    model, effort          template: `["--model", "{model}"]`; empty when the CLI takes none
    auto, plan             template: the permission or sandbox mode of a launch that may edit,
                           and of a plan launch
    addDir                 template: one more directory, `["--add-dir", "{dir}"]`
    resume                 template: the session under the ticket's id, `["--resume", "{session}"]`
    check                  the arguments of a one-shot answer to `{prompt}`, after `args`
    env                    variables the launch sets: an endpoint, a provider name. Never a key
    keyEnv                 the name of the variable that holds the key; it lives in the owner's
                           environment, and a launch or a check says when it is unset
    models, defaultModel   the models it can run, a key to a model id, and the key a launch
                           takes when it names none; empty, the CLI picks its own
    efforts, defaultEffort the reasoning efforts it takes, and the default; a provider without
                           the `effort` feature takes none. A project's `providers.<id>` in
                           project.json overrides the four; a launch may name only these
    features               what a launch of it can do (FEATURES)
    images                 the process names under a session's shell, for the session scan
    transcripts            where the CLI writes its transcripts, a glob

`project.PROVIDERS` and `project.PROVIDER_FEATURES` are live views of this list, so the launch, the
`--provider` choices and the board read every provider without a restart.

THE DEFAULT. `default-provider.json` beside the list holds the machine's default AI provider and
model and the proof that they work: `{provider, model, modelId, at, ok, said, command, fix}`.
`setup` writes it after one short headless call of that provider on that model. While no
default is confirmed, `octopus setup` and the board ask for it, and a launch stops.

THE OLD NAME. Before providers had their name the list was `agents.json` under the key `agents`.
That file is read while `providers.json` is absent, and the next write moves it to the new one.
"""

from __future__ import annotations

import copy
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import project as _project

FILE = "providers.json"
DEFAULT_FILE = "default-provider.json"
# The list's file and key before providers had their name.
OLD_FILE = "agents.json"
OLD_KEY = "agents"
DIALECTS = ("claude", "codex", "template")
APPROVAL_POLICIES = ("", "on-request", "untrusted", "never")
# `hooks`: the board reads its state from the hook files and its transcript; without them,
# from its process and `report`. `remoteControl`: Claude Code's Remote Control. `planMode`: a
# permission mode that keeps a plan from editing. `deny`: a tool denied per launch. `resume`:
# the ticket's own session again. `addDir`: a directory outside the project. `effort`: a
# reasoning effort. `skills`: it reads `.agents/skills/`, or Claude's pointers to them.
# `scheduler`: it schedules its own recurring prompt (`CronCreate`); a manager on a provider
# without one gets its 10-minute check from the board's watchdog.
FEATURES = ("hooks", "remoteControl", "planMode", "deny", "resume", "addDir", "effort",
            "skills", "scheduler")
LIST_FIELDS = ("args", "prompt", "model", "effort", "auto", "plan", "addDir", "resume",
               "check", "images", "efforts")
STR_FIELDS = ("label", "dialect", "command", "keyEnv", "defaultModel", "defaultEffort",
              "transcripts", "approvalPolicy")
FIELDS = ("id",) + STR_FIELDS + LIST_FIELDS + ("env", "models", "features")
# The placeholders a list field may hold.
PLACEHOLDERS = ("prompt", "model", "effort", "dir", "session", "root")
CHECK_PROMPT = "Reply with the single word OK and nothing else."
CHECK_TIMEOUT = 120

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# A variable whose value is a secret. Its value never enters the list; `keyEnv` names it.
_SECRET_NAME = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)")
# A character that ends or redirects the `cmd` line the value is set on.
_SHELL_UNSAFE = re.compile(r'["&|^%<>\r\n]')
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _none(**on):
    return {f: bool(on.get(f)) for f in FEATURES}


BUILTIN = {
    "claude": {
        "label": "Claude Code", "dialect": "claude", "command": "claude",
        "features": _none(hooks=1, remoteControl=1, planMode=1, deny=1, resume=1, addDir=1,
                          effort=1, skills=1, scheduler=1),
        "models": {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5",
                   "haiku": "claude-haiku-5-5", "fable": "claude-fable-5-1"},
        "defaultModel": "opus",
        "efforts": ["low", "medium", "high", "xhigh", "max"], "defaultEffort": "high",
        "check": ["-p", "{prompt}"],
        "images": ["claude.exe", "node.exe"],
        "transcripts": "~/.claude/projects/<project>/<session>.jsonl",
    },
    "codex": {
        "label": "OpenAI Codex", "dialect": "codex", "command": "codex",
        "features": _none(addDir=1, effort=1, skills=1),
        # No default model or effort: Codex takes its own, from its config.
        "models": {"codex": "gpt-6.1-sol", "gpt-6.1-sol": "gpt-6.1-sol",
                   "gpt-6.1-terra": "gpt-6.1-terra", "gpt-6-astra": "gpt-6-astra",
                   "gpt-6-sol": "gpt-6-sol", "gpt-6-luna": "gpt-6-luna",
                   "gpt-5.6-sol": "gpt-5.6-sol", "gpt-5.6-terra": "gpt-5.6-terra",
                   "gpt-5.6-luna": "gpt-5.6-luna"},
        "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
        "check": ["exec", "--sandbox", "read-only", "--skip-git-repo-check", "{prompt}"],
        "images": ["codex.exe", "node.exe"],
        "transcripts": "~/.codex/sessions/**/rollout-*.jsonl",
    },
}
DEFAULT = "claude"

# Unknown/custom model ids keep the owner's effort list. Known models constrain it.
CODEX_MODEL_EFFORTS = {
    mid: [e for e in BUILTIN["codex"]["efforts"] if e != "ultra" or "luna" not in mid]
    for mid in BUILTIN["codex"]["models"].values()
}


def model_efforts(p, model, efforts=None):
    offered = list(p["efforts"] if efforts is None else efforts)
    supported = CODEX_MODEL_EFFORTS.get(model) if p["dialect"] == "codex" else None
    return [e for e in offered if supported is None or e in supported]


def command_path(command):
    """Accept a pasted executable path, including Explorer's Copy as path quotes."""
    command = command.strip()
    if len(command) > 1 and command[0] == command[-1] and command[0] in "\"'":
        command = command[1:-1]
    return os.path.expandvars(os.path.expanduser(command))


def resolve_command(command):
    """Resolve an explicit path or PATH command, then common Codex install locations."""
    command = command_path(command)
    found = shutil.which(command)
    if found:
        return found
    # An explicit path must never silently fall back to a different installation.
    if command.lower() not in ("codex", "codex.exe", "codex.cmd"):
        return None
    candidates = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            base = Path(local) / "OpenAI" / "Codex" / "bin"
            candidates += sorted(base.glob("*/codex.exe"),
                                 key=lambda p: p.stat().st_mtime, reverse=True)
            candidates += [base / "codex.exe"]
        roaming = os.environ.get("APPDATA")
        if roaming:
            candidates.append(Path(roaming) / "npm" / "codex.cmd")
    else:
        candidates += [Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex"),
                       Path("/Applications/Codex.app/Contents/Resources/codex")]
    try:
        candidates += [Path.home() / ".local" / "bin" / "codex"]
    except RuntimeError:
        pass
    return next((str(p) for p in candidates if p.is_file()
                 and (os.name == "nt" or os.access(p, os.X_OK))), None)


def launch_command(command):
    return resolve_command(command) or command_path(command)

# What a provider of its own starts from: a CLI that takes the prompt as its last argument.
_TEMPLATE = {"dialect": "template", "args": [], "prompt": ["{prompt}"], "model": [],
             "effort": [], "auto": [], "plan": [], "addDir": [], "resume": [],
             "check": ["{prompt}"], "env": {}, "keyEnv": "", "models": {}, "defaultModel": "",
             "efforts": [], "defaultEffort": "", "features": _none(), "images": [], "transcripts": "",
             "approvalPolicy": ""}


def store_file():
    return _project.home() / FILE


def old_store_file():
    return _project.home() / OLD_FILE


def _migrate():
    """Move an older install's `agents.json` to `providers.json`, its key `agents` renamed.
    Nothing happens once `providers.json` exists; the old file then stays where it is."""
    new = store_file()
    old = new.with_name(OLD_FILE)
    if new.exists() or not old.is_file():
        return
    try:
        with io.open(old, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return                                          # unreadable: left for the owner
    if not isinstance(data, dict):
        return
    if OLD_KEY in data and "providers" not in data:
        data["providers"] = data.pop(OLD_KEY)
    _write(data)
    try:
        old.unlink()
    except OSError:
        pass


# ── reading and writing the list ─────────────────────────────────────────────

_CACHE = {}


def _read():
    """The file as a dict, `{"providers": [...], "checks": {...}}`, read again only when it
    changed. The old file and its old key are read while the new file is absent."""
    _migrate()
    f = store_file()
    if not f.is_file() and old_store_file().is_file():
        f = old_store_file()
    try:
        st = f.stat()
        key = (str(f), st.st_mtime_ns, st.st_size)
    except OSError:
        return {"providers": [], "checks": {}}
    if _CACHE.get("key") != key:
        try:
            with io.open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            data = {"providers": [], "checks": {}, "error": "%s: %s" % (f, exc)}
        if not isinstance(data, dict):
            data = {"providers": [], "checks": {}, "error": "%s: not a JSON object" % f}
        if OLD_KEY in data and "providers" not in data:
            data["providers"] = data.pop(OLD_KEY)
        _CACHE.clear()
        _CACHE.update(key=key, data=data)
    return copy.deepcopy(_CACHE["data"])


def _write(data):
    f = store_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(f) + ".tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"providers": data.get("providers") or [],
                             "checks": data.get("checks") or {}}, indent=1) + "\n")
    os.replace(tmp, f)
    # The list now lives under its new name only.
    try:
        old_store_file().unlink()
    except OSError:
        pass
    _CACHE.clear()


def _entries(data):
    rows = data.get("providers")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k == "features" and isinstance(v, dict):
            out["features"] = dict(out.get("features") or {}, **v)
        elif k != "id":
            out[k] = copy.deepcopy(v)
    return out


def _complete(pid, spec):
    """Every field present: the template's defaults under what the spec names."""
    out = _merge(_TEMPLATE, spec)
    out["id"] = pid
    out["label"] = out.get("label") or pid
    out["features"] = {f: bool((out.get("features") or {}).get(f)) for f in FEATURES}
    return out


def load():
    """`{id: provider}`, the built-ins first, then the owner's in the order of the file. An
    entry with a problem is left out; `problems()` names it."""
    data = _read()
    out = {pid: _complete(pid, spec) for pid, spec in BUILTIN.items()}
    for row in _entries(data):
        pid = row.get("id")
        if validate(row):
            continue
        base = BUILTIN.get(pid, {})
        out[pid] = _complete(pid, _merge(base, row))
        out[pid]["builtin"] = pid in BUILTIN
        out[pid]["overridden"] = pid in BUILTIN
    for pid in BUILTIN:
        out[pid].setdefault("builtin", True)
        out[pid].setdefault("overridden", False)
    return out


def problems():
    """What is wrong with the file, one line each."""
    data = _read()
    out = [data["error"]] if data.get("error") else []
    seen = set()
    for i, row in enumerate(_entries(data)):
        pid = row.get("id") or "#%d" % (i + 1)
        if pid in seen:
            out.append("%s: %s is listed twice; the later entry wins" % (FILE, pid))
        seen.add(pid)
        out += ["%s: %s: %s" % (FILE, pid, p) for p in validate(row)]
    return out


def ids():
    return list(load())


def get(pid):
    p = load().get(pid)
    if p is None:
        raise ValueError("%r is not an AI provider. One of: %s" % (pid, ", ".join(load())))
    return p


def label(pid):
    p = load().get(pid)
    return p["label"] if p else pid


def dialect(pid):
    p = load().get(pid)
    return p["dialect"] if p else pid


def features(pid):
    p = load().get(pid)
    return dict(p["features"]) if p else {}


def images():
    """The process names of every provider's session, lower case."""
    out = []
    for p in load().values():
        out += [n.lower() for n in p["images"] if n.lower() not in out]
    return tuple(out)


def last_check(pid):
    return (_read().get("checks") or {}).get(pid)


def validate(row):
    """The problems of one entry of the file; [] when it is sound."""
    out = []
    if not isinstance(row, dict):
        return ["an entry is a JSON object"]
    pid = row.get("id")
    if not isinstance(pid, str) or not _ID.match(pid):
        out.append("`id` is lower-case letters, digits, `_` and `-`, starting with a letter")
    unknown = sorted(set(row) - set(FIELDS))
    if unknown:
        out.append("unknown field %s; the fields are %s" % (", ".join(unknown), ", ".join(FIELDS)))
    for k in STR_FIELDS:
        if k in row and not isinstance(row[k], str):
            out.append("`%s` is a string" % k)
    if isinstance(row.get("command"), str) and not command_path(row["command"]):
        out.append("`command` names the executable")
    for k in LIST_FIELDS:
        if k in row:
            v = row[k]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                out.append("`%s` is a list of strings" % k)
                continue
            bad = sorted({m for x in v for m in _PLACEHOLDER.findall(x)} - set(PLACEHOLDERS))
            if bad:
                out.append("`%s` names %s; the placeholders are %s"
                           % (k, ", ".join("{%s}" % b for b in bad),
                              ", ".join("{%s}" % p for p in PLACEHOLDERS)))
    builtin = pid in BUILTIN
    d = row.get("dialect", BUILTIN.get(pid, {}).get("dialect", "template"))
    if d not in DIALECTS:
        out.append("`dialect` is one of %s" % ", ".join(DIALECTS))
    elif d == "claude" and pid != "claude":
        # The session scan, the hooks and the state engine know a Claude session by the id
        # `claude`; Claude Code against another endpoint is the `claude` entry's own `env`.
        out.append("only `claude` has the claude dialect; set `env` on `claude` to point "
                   "Claude Code at another endpoint")
    elif builtin and "dialect" in row and d != BUILTIN[pid]["dialect"]:
        out.append("a built-in keeps its dialect, %s" % BUILTIN[pid]["dialect"])
    policy = row.get("approvalPolicy", "")
    if policy not in APPROVAL_POLICIES:
        out.append("`approvalPolicy` must be empty, on-request, untrusted or never")
    elif policy and d != "codex":
        out.append("`approvalPolicy` applies only to the codex dialect")
    if not builtin and not (isinstance(row.get("command"), str) and row["command"].strip()):
        out.append("`command` names the executable")
    if d == "template":
        for k, default in (("prompt", ["{prompt}"]), ("check", ["{prompt}"])):
            v = row.get(k, default)
            if isinstance(v, list) and not any("{prompt}" in x for x in v if isinstance(x, str)):
                out.append("`%s` must place {prompt}" % k)
    env = row.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                            for k, v in env.items()):
        out.append("`env` maps a variable name to its value")
    else:
        for k, v in env.items():
            if not _ENV_NAME.match(k):
                out.append("`env`: %r is not a variable name" % k)
            elif _SECRET_NAME.search(k):
                out.append("`env`: %s holds a secret; never write it here. Set it in your own "
                           "environment and name it in `keyEnv`" % k)
            if _SHELL_UNSAFE.search(v):
                out.append("`env`: the value of %s holds a character the shell line cannot "
                           "carry (\" & | ^ %% < > or a line break)" % k)
    key = row.get("keyEnv", "")
    if isinstance(key, str) and key and not _ENV_NAME.match(key):
        out.append("`keyEnv` is a variable name, never the key itself")
    models = row.get("models", {})
    if not isinstance(models, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                               for k, v in models.items()):
        out.append("`models` maps a key to a model id")
    else:
        # An override replaces the built-in's lists; the default must be one of them.
        base = BUILTIN.get(pid, {})
        models = models if "models" in row else base.get("models", {})
        efforts = row.get("efforts", base.get("efforts", []))
        dm = row.get("defaultModel", base.get("defaultModel", ""))
        de = row.get("defaultEffort", base.get("defaultEffort", ""))
        if isinstance(dm, str) and dm and dm not in models:
            out.append("`defaultModel` %r is not a key of `models` (%s)"
                       % (dm, ", ".join(models) or "none listed"))
        if isinstance(de, str) and de and isinstance(efforts, list) and de not in efforts:
            out.append("`defaultEffort` %r is not one of `efforts` (%s)"
                       % (de, ", ".join(efforts) or "none listed"))
        elif d == "codex" and isinstance(dm, str) and isinstance(de, str) and de:
            allowed = CODEX_MODEL_EFFORTS.get(models.get(dm))
            if allowed is not None and de not in allowed:
                out.append("`defaultEffort` %r is not supported by %s" % (de, models[dm]))
    feats = row.get("features", {})
    if not isinstance(feats, dict) or set(feats) - set(FEATURES) \
            or not all(isinstance(v, bool) for v in feats.values()):
        out.append("`features` maps %s to true or false" % ", ".join(FEATURES))
    return out


def save(row):
    """Add a provider, or replace the owner's entry of the same id. Raises ValueError with
    every problem of the entry. Returns the provider as `load()` gives it."""
    row = {k: v for k, v in row.items() if k in FIELDS}
    if isinstance(row.get("command"), str):
        row["command"] = command_path(row["command"])
    errs = validate(row)
    if errs:
        raise ValueError("; ".join(errs))
    data = _read()
    rows = [r for r in _entries(data) if r.get("id") != row["id"]]
    rows.append(row)
    data["providers"] = rows
    _write(data)
    return load()[row["id"]]


def remove(pid):
    """Remove the owner's entry: a provider of its own goes, a built-in returns to its
    defaults. Returns `removed` or `reset`."""
    data = _read()
    rows = _entries(data)
    keep = [r for r in rows if r.get("id") != pid]
    if len(keep) == len(rows):
        if pid in BUILTIN:
            raise ValueError("%s is built in and has no changes to reset" % pid)
        raise ValueError("no AI provider %r" % pid)
    data["providers"] = keep
    if pid not in BUILTIN:
        (data.get("checks") or {}).pop(pid, None)
    _write(data)
    return "reset" if pid in BUILTIN else "removed"


def entry(pid):
    """The owner's entry of `pid` as it stands in the file, or {}."""
    return next((dict(r) for r in _entries(_read()) if r.get("id") == pid), {})


# ── a command line out of the template fields ────────────────────────────────

def fill(args, **values):
    """`args` with each `{name}` replaced. A word whose placeholder has no value is dropped."""
    out = []
    for a in args:
        names = _PLACEHOLDER.findall(a)
        if any(values.get(n) in (None, "") for n in names):
            continue
        out.append(_PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), a))
    return out


def approval_argv(p):
    """An optional Codex interactive override; otherwise let its config decide."""
    policy = p.get("approvalPolicy", "")
    return ["--ask-for-approval", policy] if p["dialect"] == "codex" and policy else []


def template_argv(p, prompt, mode_args, model="", effort="", dirs=(), session="", root=""):
    """The command line of a `template` provider: the command, its `args`, the mode's
    permission arguments, one `addDir` per directory, the model, the effort, the session,
    and the prompt."""
    v = {"model": model, "effort": effort, "session": session, "root": root}
    argv = [p["command"]] + fill(p["args"], **v) + fill(mode_args, **v)
    if p["features"]["addDir"]:
        for d in dirs:
            argv += fill(p["addDir"], dir=d, **v)
    if model:
        argv += fill(p["model"], **v)
    if effort and p["features"]["effort"]:
        argv += fill(p["effort"], **v)
    if session and p["features"]["resume"]:
        argv += fill(p["resume"], **v)
    return argv + fill(p["prompt"], prompt=prompt, **v)


def launch_env(pid):
    """The variables a launch of `pid` sets on its shell line."""
    p = load().get(pid)
    env = dict(p["env"]) if p else {}
    if os.name == "nt" and p and p["dialect"] == "codex":
        # Ticket commands use the Python interpreter that runs Octopus.
        key = next((k for k in env if k.upper() == "PATH"), "PATH")
        env[key] = str(Path(sys.executable).parent) + os.pathsep + env.get(key, os.environ.get("PATH", ""))
    return env


def key_missing(pid, environ=None):
    """The `keyEnv` of `pid` when the environment does not set it, else ""."""
    p = load().get(pid)
    key = (p or {}).get("keyEnv") or ""
    environ = os.environ if environ is None else environ
    return key if key and not environ.get(key) else ""


# ── a check: does it start, and does it answer ───────────────────────────────

def check_argv(p, prompt=CHECK_PROMPT, model_key=None):
    """The check's command line, on `model_key` of the provider's models, else its default."""
    model = p["models"].get(p["defaultModel"] if model_key is None else model_key, "")
    effort = p["defaultEffort"]
    argv = [p["command"]] + fill(p["args"], model=model, effort=effort)
    if model:
        argv += (["--model", model] if p["dialect"] != "template"
                 else fill(p["model"], model=model))
    if effort:
        argv += (["-c", "model_reasoning_effort=%s" % effort] if p["dialect"] == "codex"
                 else ["--effort", effort] if p["dialect"] == "claude"
                 else fill(p["effort"], effort=effort))
    return argv + fill(p["check"], prompt=prompt, model=model, effort=effort)


def answer_argv(p, model="", effort="", output=""):
    """A bounded stdin-to-answer call. Codex drafting uses an isolated read-only workspace."""
    argv = [launch_command(p["command"])] + fill(p["args"], model=model, effort=effort)
    if p["dialect"] == "codex":
        argv += ["exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
                 "--ignore-user-config", "-c", "approval_policy=\"never\"",
                 "-c", "features.shell_tool=false", "-c", "web_search=\"disabled\"",
                 "--color", "never"]
        if output:
            argv += ["--output-last-message", output]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["-c", "model_reasoning_effort=%s" % effort]
        return argv + ["-"]
    if p["dialect"] == "claude":
        argv += ["-p", "--tools", "", "--strict-mcp-config"]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
        return argv
    raise ValueError("%s does not support headless answers; choose Claude Code or Codex"
                     % p["label"])


def answer(p, prompt, model="", effort="", timeout=240):
    """Return only a successful final answer; neither provider receives the project tree."""
    env = dict(os.environ, **p["env"])
    env.pop("CLAUDECODE", None)
    key = p.get("keyEnv")
    if key and not env.get(key):
        raise ValueError("%s is unset" % key)
    with tempfile.TemporaryDirectory(prefix="octopus-answer-") as work:
        output = Path(work) / "answer.txt"
        argv = answer_argv(p, model, effort, str(output))
        try:
            r = subprocess.run(argv, input=prompt, cwd=work, env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=timeout,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise ValueError("%s did not answer in %d seconds" % (p["label"], timeout))
        except OSError as exc:
            raise ValueError("%s did not start: %s. Set its executable path in Global settings."
                             % (p["label"], exc))
        if r.returncode:
            raise ValueError("%s failed (exit %s): %s" % (
                p["label"], r.returncode, _tail(r.stderr or r.stdout or "no diagnostic")))
        result = (output.read_text(encoding="utf-8") if output.is_file() else r.stdout or "").strip()
        if not result:
            raise ValueError("%s returned no answer" % p["label"])
        return result


def check(pid, timeout=CHECK_TIMEOUT, record=True, run=None, model_key=None):
    """Run the provider once, headless, on a fixed prompt in an empty directory, on `model_key`
    or its default model. Returns `{id, at, ok, started, answered, exit, seconds, said, command,
    key}`; with `record`, the result is kept as its last check."""
    p = get(pid)
    argv = check_argv(p, model_key=model_key)
    out = {"id": pid, "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "ok": False, "started": False, "answered": False, "exit": None, "seconds": 0.0,
           "said": "", "command": subprocess.list2cmdline(argv),
           "key": key_missing(pid)}
    exe = resolve_command(argv[0])
    if exe is None:
        out["said"] = "%s is not on PATH or at that path; set its executable path in Global settings" % argv[0]
        return _record(out) if record else out
    out["command"] = subprocess.list2cmdline([exe] + argv[1:])
    env = dict(os.environ, **p["env"])
    # A check started from inside a Claude session must not count as one of its children.
    env.pop("CLAUDECODE", None)
    work = tempfile.mkdtemp(prefix="octopus-provider-check-")
    started = time.time()
    try:
        r = (run or subprocess.run)([exe] + argv[1:], cwd=work, env=env, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout, stdin=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out["started"] = True
        out["exit"] = r.returncode
        text = (r.stdout or "").strip()
        out["answered"] = r.returncode == 0 and bool(text)
        out["said"] = _tail(text if out["answered"] else (r.stderr or "").strip() or text)
    except subprocess.TimeoutExpired:
        out["started"] = True
        out["said"] = "no answer within %d seconds" % timeout
    except OSError as exc:
        out["said"] = "it did not start: %s" % exc
    finally:
        out["seconds"] = round(time.time() - started, 1)
        shutil.rmtree(work, ignore_errors=True)
    out["ok"] = out["started"] and out["answered"]
    return _record(out) if record else out


def _tail(text, limit=400):
    text = " ".join(text.split())
    return text if len(text) <= limit else "..." + text[-limit:]


def _record(result):
    data = _read()
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    checks[result["id"]] = {k: v for k, v in result.items() if k != "id"}
    data["checks"] = checks
    _write(data)
    return result


# ── the default AI provider and model, and the proof that they work ─────────

# What the owner does when the check of a built-in fails, by what failed.
_INSTALL = {"claude": "install Claude Code (https://docs.claude.com/en/docs/claude-code/setup)",
            "codex": "install Codex (npm install -g @openai/codex)"}
_LOGIN = {"claude": "run `claude` once in a terminal and log in",
          "codex": "run `codex login`"}


def default_file():
    return _project.home() / DEFAULT_FILE


def default_record():
    """The last choice as `default-provider.json` holds it, or {}."""
    try:
        with io.open(default_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_default(rec):
    f = default_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(f) + ".tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, indent=1) + "\n")
    os.replace(tmp, f)


def default_id():
    """The provider of the confirmed choice, else `claude`. A project that names no provider
    launches this one."""
    rec = default_record()
    return rec["provider"] if rec.get("ok") and rec.get("provider") in load() else DEFAULT


def default_state():
    """`{confirmed, provider, model, modelId, at, ok, said, fix, request, choices, file}`. The
    default is confirmed when its check passed, its provider is still listed, and its model is
    still the provider's default model. `choices` is every provider with its models, for the
    question."""
    rec = default_record()
    known = load()
    p = known.get(rec.get("provider"))
    confirmed = bool(rec.get("ok") and p and rec.get("model", "") == p["defaultModel"])
    request = "" if confirmed else (
        "Choose the default AI provider and model of this machine, and check that they work: "
        "run `python octopus.py setup`, or choose them on the board.")
    if not confirmed and rec.get("ok") and p:
        request = ("The default model of %s is now %s, and no check confirmed it. %s"
                   % (p["label"], p["defaultModel"] or "the CLI's own", request))
    choices = [{"id": pid, "label": q["label"], "models": [[k, v] for k, v in q["models"].items()],
                "defaultModel": q["defaultModel"],
                "installed": resolve_command(q["command"]) is not None}
               for pid, q in known.items()]
    return {"confirmed": confirmed, "provider": rec.get("provider", ""),
            "model": rec.get("model", ""), "modelId": rec.get("modelId", ""),
            "at": rec.get("at", ""), "ok": bool(rec.get("ok")), "said": rec.get("said", ""),
            "fix": rec.get("fix", ""), "request": request, "choices": choices,
            "file": str(default_file())}


def setup_request():
    """The request to choose and check the default, or "" when it is confirmed."""
    return default_state()["request"]


def fix_hint(p, result):
    """What the owner does after a failed check, in one line."""
    pid = p["id"]
    if result.get("key"):
        return "set %s in your environment, then check again" % result["key"]
    if not result.get("started"):
        return "%s, or set its executable path in Global settings" % _INSTALL.get(
            pid, "install %s" % p["label"])
    if "model" in (result.get("said") or "").lower():
        return "choose another model: %s refuses this one" % p["label"]
    return "%s, or choose another model" % _LOGIN.get(
        pid, "check that %s is logged in to its service" % p["label"])


def setup(pid, model, timeout=CHECK_TIMEOUT, run=None):
    """Check `model` (a key or an id of the provider's models) of `pid` with one short headless
    call. A pass makes it the provider's default model and the machine's default, confirmed. A
    failure changes no setting; it is recorded only while no default is confirmed. Returns the
    check with `model`, `modelId` and, on a failure, `fix`. Raises ValueError for a provider or
    a model the list does not hold."""
    p = get(pid)
    models = p["models"]
    key = next((k for k, v in models.items() if model in (k, v)), None) if model else ""
    if key is None or (models and not key):
        raise ValueError("%s has no model %r. Its models: %s"
                         % (p["label"], model or "", ", ".join(models) or "none listed"))
    r = check(pid, timeout=timeout, record=False, run=run, model_key=key)
    r.update(model=key, modelId=models.get(key, ""))
    if not r["ok"]:
        r["fix"] = fix_hint(p, r)
    rec = {"provider": pid, "model": key, "modelId": r["modelId"], "at": r["at"],
           "ok": r["ok"], "said": r["said"], "command": r["command"], "fix": r.get("fix", "")}
    if r["ok"]:
        if p["defaultModel"] != key:
            save(dict(entry(pid), id=pid, defaultModel=key))
        _record(dict(r))
        _write_default(rec)
    elif not default_state()["confirmed"]:
        _write_default(rec)
    return r


def view():
    """The list as the board and `octopus providers` show it."""
    checks = _read().get("checks") or {}
    rows = []
    for pid, p in load().items():
        row = {k: p[k] for k in FIELDS if k in p}
        row.update(builtin=p["builtin"], overridden=p["overridden"],
                   lastCheck=checks.get(pid), keyMissing=key_missing(pid),
                   resolvedCommand=resolve_command(p["command"]),
                   own=entry(pid))
        rows.append(row)
    return {"providers": rows, "problems": problems(), "file": str(store_file()),
            "dialects": list(DIALECTS), "features": list(FEATURES),
            "placeholders": list(PLACEHOLDERS), "default": default_id(),
            "defaultSetup": default_state()}
