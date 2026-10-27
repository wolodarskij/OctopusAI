"""octopus.py - Octopus AI: one TODO engine for many projects.

    python octopus.py todo [--project <name|path>] <command> ...   the ticket tree of a project
    python octopus.py board [--project <name|path>] [--port N] [--no-open]
                                                  the board, serving every registered project
    python octopus.py sessions [--since-last] [--if-any] [--json]
                                                  the manager's update: every agent of every
                                                  registered project, in four sections
    python octopus.py check [--job-created] [--dry-run]
                                                  the manager's 10-minute check: launch the
                                                  unlaunched follow-ups, then the update while
                                                  any agent finished, waits or works
    python octopus.py hook                        the Claude Code hook: SessionStart,
                                                  UserPromptSubmit, Stop, Notification,
                                                  PreToolUse and PostToolUse; PreToolUse also
                                                  runs the README guard (readme_guard.py)
    python octopus.py status-hook codex           the Codex hook: the status reminder
                                                  once every 5 minutes (status_reminder.py),
                                                  and the README guard on PreToolUse
    python octopus.py new <path> --name <name> [--packs <pack,pack>]
                                                  create the directory and install into it;
                                                  every pack is optional, and `packs` adds
                                                  or removes one later
    python octopus.py install <path> [--packs <pack,pack>]
    python octopus.py update <path> | --all       write again every payload file a pack changed
    python octopus.py uninstall <path> [--pack <name>]
    python octopus.py status <path>               each installed file; each open ticket or file
                                                  that misnames the toolkit; each entry file or
                                                  setting that lets an agent narrate its work
    python octopus.py adopt <path> | --all [--dry-run]
                                                  move a project's own rules and skills from
                                                  .claude/ into .agents/, and write each
                                                  AI provider's entry file
    python octopus.py packs <path> [list | add <pack> | remove <pack> | set <pack,pack,...>]
    python octopus.py rules [<rule> on | off | default] [--project <name|path>]
                                                  the optional rules: list them with their
                                                  state, or switch one for every project
                                                  (global) or for one project. The older
                                                  `rules <path> [list | on|off|default <rule>]`
                                                  still works
    python octopus.py pack-registry [list | add <folder|url> [--name] [--ref] [--path]
                                    | update <name> | remove <name>]
                                                  the packs every project can choose
    python octopus.py skills <path> [list | libraries | available <source> | add ...
                                    | update [<skill>] | remove <skill>]
                                                  skills from a git repository, a folder,
                                                  or the Claude or Codex skill library
    python octopus.py lanes <path> [list | add <dir> ... | rename <dir> ... | remove <dir>]
    python octopus.py projects [list | add <path> [--packs <pack,pack>] | remove <path>]
    python octopus.py providers [list | show <id> | add <id> ... | edit <id> ... | remove <id>
                                | check <id>]     the AI providers a launch can start: Claude
                                                  Code, Codex and custom CLIs.
                                                  `agents` is its old name
    python octopus.py setup [--provider <id> --model <model>] [--status]
                                                  the default AI provider and model of this
                                                  machine, checked with one short headless call;
                                                  with no choice named, it asks
    python octopus.py ask [prompt | -] --provider codex [--model MODEL] [--effort EFFORT]
                                                  a headless answer; omit the prompt to read stdin
                                                  --project PATH uses that project's settings
    python octopus.py relocate <new path> [--wait] [--no-link]
                                                  move this checkout, and every path that
                                                  names it in every registered project

A project keeps its tickets and its config in its own tree. Octopus AI keeps the engine and the
list of the projects it serves (`%APPDATA%/OctopusAI/projects.json`, or `OCTOPUS_HOME`).

The checkout is a project too. The first command in a fresh clone, any one but the two hooks,
installs Octopus AI into the checkout and registers it as "Octopus AI"; the `SessionStart` hook
does the same when a Claude session starts there first. A checkout with `.octopus/project.json`
is left as it is.

Until a default AI provider and model is confirmed, the first command in a terminal asks for it,
except `board` (the board asks in its page) and the two hooks, and a launch stops.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

OCTOPUS = Path(__file__).resolve().parent
for _d in ("core/engine", "core/win", "core/hooks", "core/posix"):
    _p = str(OCTOPUS / _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import project  # noqa: E402


def cmd_hook(argv):
    """One hook event. Never fails: a hook that fails would stop the session it runs in."""
    # Taken first: `PostToolUse` runs in the background, and a marker the `Stop` hook wrote
    # after this moment is newer than the answer this hook reports, so it stays.
    started = __import__("time").time()
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            return 0
    except Exception:                                   # noqa: BLE001 - a hook never raises
        return 0
    event = str(payload.get("hook_event_name") or "")
    if event == "PreToolUse":
        try:
            import readme_guard
            if payload.get("tool_name") in readme_guard.CLAUDE_TOOLS:
                out = readme_guard.decide(payload)
                if out:
                    sys.stdout.write(json.dumps(out) + "\n")
                    sys.stdout.flush()
                return 0
        except Exception:                               # noqa: BLE001 - a hook never raises
            pass
    if event == "SessionStart" and not project.has_config(OCTOPUS):
        try:
            import admin
            if any(admin.is_checkout(d) for d in (
                    str(payload.get("cwd") or ""),
                    __import__("os").environ.get("CLAUDE_PROJECT_DIR", "")) if d):
                ensure_self(out=lambda *_a: None)
        except Exception:                               # noqa: BLE001 - a hook never raises
            pass
    root = None
    for start in (str(payload.get("cwd") or ""),
                  __import__("os").environ.get("CLAUDE_PROJECT_DIR", "")):
        if start:
            root = project.find_root(start)
            if root:
                break
    if root is None:
        return 0
    if event == "SessionStart":
        try:
            import log_session
            log_session.append(payload)
        except Exception:                               # noqa: BLE001
            pass
        try:
            import todo
            tok = project.use(root)
            try:
                todo.write_index(quiet=True)
            finally:
                project.reset(tok)
        except Exception:                               # noqa: BLE001
            pass
        # The payload a pack owns (the blueprint pack's editor plugin) follows the pack.
        try:
            _admin().refresh(root, out=lambda *_a: None)
        except Exception:                               # noqa: BLE001
            pass
    try:
        import session_state
        session_state.main([], payload=payload, started=started)
    except Exception:                                   # noqa: BLE001
        pass
    return 0


def _admin():
    import admin
    return admin


def ensure_self(out=None):
    """A fresh clone installs Octopus AI into itself and registers itself as a project
    (`admin.ensure_self`). Its lines go to stderr, or to `out`; it never stops the command."""
    import contextlib
    err = out or (lambda line: print(line, file=sys.stderr))
    try:
        with contextlib.redirect_stdout(sys.stderr if out is None else __import__("io").StringIO()):
            _admin().ensure_self(out=err)
    except (OSError, ValueError) as exc:
        err("error: Octopus AI could not install itself into %s: %s" % (OCTOPUS, exc))


# The commands that never ask for the default AI provider and model.
NO_SETUP_QUESTION = ("hook", "status-hook", "board", "setup", "-h", "--help")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("hook", "status-hook"):
        ensure_self()
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in NO_SETUP_QUESTION and _interactive():
        import providers
        if providers.setup_request():
            setup_dialog()
    if cmd == "setup":
        return cmd_setup(rest)
    if cmd == "todo":
        import todo
        return todo.main(rest)
    if cmd == "board":
        import todo_server
        return todo_server.main(rest)
    if cmd == "hook":
        return cmd_hook(rest)
    if cmd == "status-hook":
        import status_reminder
        return status_reminder.main(rest)
    if cmd == "sessions":
        import sessionreport
        return sessionreport.main(rest)
    if cmd == "check":
        import managercheck
        return managercheck.main(rest)

    ap = argparse.ArgumentParser(prog="octopus %s" % cmd)
    try:
        if cmd in ("new", "install"):
            ap.add_argument("path")
            ap.add_argument("--profile", help=argparse.SUPPRESS)
            ap.add_argument("--name")
            ap.add_argument("--slug")
            ap.add_argument("--no-register", action="store_true")
            ap.add_argument("--no-manager", action="store_true",
                            help="file no manager ticket in the Octopus AI checkout's own project "
                                 "when it had no config")
            ap.add_argument("--packs", help="the optional packs, comma-separated; none when left "
                                            "out. `octopus pack-registry` lists them")
            a = ap.parse_args(rest)
            path = Path(a.path)
            packs = _packs_arg(ap, a)
            if cmd == "new":
                path.mkdir(parents=True, exist_ok=True)
            _admin().install(path, packs, a.name, a.slug, register=not a.no_register,
                             manager=not a.no_manager)
            return 0
        if cmd == "update":
            ap.add_argument("path", nargs="?")
            ap.add_argument("--all", action="store_true", help="every registered project")
            a = ap.parse_args(rest)
            if a.all:
                _admin().update_all()
                return 0
            if not a.path:
                ap.error("name the project directory, or pass --all")
            _admin().update(project.resolve(a.path))
            return 0
        if cmd == "uninstall":
            ap.add_argument("path")
            ap.add_argument("--pack")
            a = ap.parse_args(rest)
            _admin().uninstall(Path(a.path), a.pack)
            return 0
        if cmd == "adopt":
            ap.add_argument("path", nargs="?")
            ap.add_argument("--all", action="store_true", help="every registered project")
            ap.add_argument("--dry-run", action="store_true", help="say what would move")
            a = ap.parse_args(rest)
            if a.all:
                _admin().adopt_all(dry_run=a.dry_run)
                return 0
            if not a.path:
                ap.error("name the project directory, or pass --all")
            _admin().adopt(project.resolve(a.path), dry_run=a.dry_run)
            return 0
        if cmd == "status":
            ap.add_argument("path")
            a = ap.parse_args(rest)
            c = _admin().status(Path(a.path))
            import oldnames
            old = oldnames.report(oldnames.scan_project(project.resolve(a.path)))
            print("%d file(s) misname the toolkit" % old)
            return 1 if c["missing"] or c["edited"] or c["reporting"] or old else 0
        if cmd == "packs":
            ap.add_argument("path")
            ap.add_argument("action", nargs="?", default="list",
                            choices=("list", "add", "remove", "set"))
            ap.add_argument("pack", nargs="?", help="set: the packs, comma-separated; "
                                                    "an empty string removes every pack")
            a = ap.parse_args(rest)
            if a.action == "list":
                p = project.load(project.resolve(a.path))
                print("\n".join(p.RULES["rulesets"]))
                return 0
            if a.action == "set":
                if a.pack is None:
                    ap.error("name the packs, comma-separated")
                added, removed = _admin().packs_set(Path(a.path), _pack_list(a.pack))
                print("added %s; removed %s" % (", ".join(added) or "none",
                                                ", ".join(removed) or "none"))
                return 0
            if not a.pack:
                ap.error("name the pack")
            if a.action == "add":
                _admin().pack_add(Path(a.path), a.pack)
            else:
                _admin().uninstall(Path(a.path), a.pack)
            return 0
        if cmd == "rules":
            return cmd_rules(ap, rest)
        if cmd == "relocate":
            ap.add_argument("path", help="the new directory of this checkout")
            ap.add_argument("--wait", action="store_true",
                            help="wait until nothing but the board and the managers runs from "
                                 "the checkout, instead of refusing")
            ap.add_argument("--no-link", action="store_true",
                            help="leave no junction at the old path")
            a = ap.parse_args(rest)
            # The move outlives the window it runs in; `relocate.log` beside the registry
            # keeps what it did.
            log = project.home() / "relocate.log"

            def out(line):
                line = __import__("time").strftime("%Y-%m-%d %H:%M:%S ") + line
                print(line, flush=True)
                log.parent.mkdir(parents=True, exist_ok=True)
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            try:
                _admin().relocate(Path(a.path), wait=a.wait, link=not a.no_link, out=out)
            except (OSError, ValueError) as exc:
                out("error: %s" % exc)
                return 2
            return 0
        if cmd == "pack-registry":
            import packreg
            return packreg.main(rest)
        if cmd == "skills":
            import skills
            return skills.main(rest)
        if cmd == "lanes":
            return cmd_lanes(rest)
        if cmd == "ask":
            return cmd_ask(rest)
        if cmd in ("providers", "agents"):
            return cmd_providers(rest)
        if cmd == "projects":
            ap.add_argument("action", nargs="?", default="list", choices=("list", "add", "remove"))
            ap.add_argument("path", nargs="?")
            ap.add_argument("--packs", help="add: the optional packs of a directory with no "
                                            "config, comma-separated; none when left out")
            ap.add_argument("--profile", help=argparse.SUPPRESS)
            ap.add_argument("--name", help="add: the project name")
            a = ap.parse_args(rest)
            if a.action == "list":
                for r in project.load_registry():
                    print("%-28s %s" % (r["name"], r["path"]))
                return 0
            if not a.path:
                ap.error("name the project directory")
            if a.action == "add":
                root = Path(a.path).resolve()
                if root.is_dir() and project.find_root(root) is None:
                    # A directory with no config gets everything a project needs first.
                    _admin().install(root, _packs_arg(ap, a) or [], a.name, register=False)
                row = project.register(project.resolve(str(root)), a.name)
                print("registered %s  %s" % (row["name"], row["path"]))
            else:
                n = project.unregister(Path(a.path))
                print("%d project(s) removed from the registry" % n)
            return 0
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    print("error: unknown command %r; `octopus --help` lists them" % cmd, file=sys.stderr)
    return 2


RULE_SWITCH = {"on": True, "off": False, "default": None}


def cmd_rules(ap, rest):
    """`rules [--project P]` lists the optional rules; `rules <rule> on|off|default [--project
    P]` switches one, globally or in project P. `rules <path> [list | on|off|default <rule>]`
    is the older form, for one project."""
    ap.add_argument("words", nargs="*", help="<rule> on|off|default; nothing to list")
    ap.add_argument("--project", help="a registered name or a path: that project's switch, "
                                      "not the global one")
    a = ap.parse_args(rest)
    words, root = list(a.words), project.resolve(a.project) if a.project else None
    if words and words[0] not in ("list",) + tuple(RULE_SWITCH) and Path(words[0]).is_dir() \
            and (len(words) == 1 or words[1] in ("list",) + tuple(RULE_SWITCH)):
        root, words = project.resolve(words[0]), words[1:]
        if len(words) == 2:
            words = [words[1], words[0]]
    if words in ([], ["list"]):
        if root is not None:
            rows = _admin().optional_rules(root)
            for r in rows:
                print("%-3s  %-30s  %-8s  %-10s  %s"
                      % ("on" if r["on"] else "off", r["name"], r["source"], r["ruleset"],
                         r["title"]))
        else:
            rows = _admin().global_rules()
            for r in rows:
                own = ", ".join("%s %s" % (x["name"], "on" if x["on"] else "off")
                                for x in r["projects"] if x["source"] == "project")
                print("%-3s  %-30s  %-8s  %-10s  %s%s"
                      % ("on" if r["on"] else "off", r["name"], r["source"], r["ruleset"],
                         r["title"], "  (own switch: %s)" % own if own else ""))
        if not rows:
            print("no optional rule")
        return 0
    if len(words) != 2 or words[1] not in RULE_SWITCH:
        ap.error("give <rule> on|off|default, or nothing to list")
    change = {words[0]: RULE_SWITCH[words[1]]}
    try:
        if root is not None:
            _admin().set_rules(root, change)
        else:
            _admin().set_global_rules(change)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


def _pack_list(text):
    """`a,b` -> ["a", "b"]; None stays None."""
    if text is None:
        return None
    return [x.strip() for x in text.split(",") if x.strip()]


def _packs_arg(ap, a):
    """The packs `--packs` names, else the packs of an old `--profile`, else None."""
    packs = _pack_list(a.packs)
    if a.profile and packs is not None:
        ap.error("name the packs only: --packs")
    if a.profile:
        return _admin().packs_of_profile(a.profile)
    return packs


def _print_check(r):
    print("%s  %s  started %s, answered %s, exit %s, %.1fs"
          % (r["id"], "OK" if r["ok"] else "FAILED", "yes" if r["started"] else "no",
             "yes" if r["answered"] else "no", r["exit"], r["seconds"]))
    print("  command %s" % r["command"])
    if r["said"]:
        print("  said    %s" % r["said"])
    if r["key"]:
        print("  warning %s is unset in this environment" % r["key"])


def cmd_ask(rest):
    """A one-shot answer from a CLI, with the prompt in an argument or on stdin."""
    import providers
    ap = argparse.ArgumentParser(prog="octopus ask")
    ap.add_argument("prompt", nargs="?", help="the prompt; omit or use - to read stdin")
    ap.add_argument("--project", help="use this project's provider, model and executable settings")
    ap.add_argument("--provider", choices=providers.ids())
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--timeout", type=int, default=240)
    a = ap.parse_args(rest)
    if a.timeout <= 0:
        ap.error("--timeout must be positive")
    if a.prompt in (None, "-") and sys.stdin.isatty():
        ap.error("provide a prompt or pipe it on stdin")
    prompt = sys.stdin.read() if a.prompt in (None, "-") else a.prompt
    if not prompt.strip():
        ap.error("the prompt is empty")
    if a.project:
        p = project.load(Path(a.project))
        pid = a.provider or p.provider
        model, effort = p.choose(pid, a.model, a.effort)
        spec = dict(providers.get(pid), command=p.providers[pid]["command"])
    else:
        spec = providers.get(a.provider or providers.default_id())
        key = a.model or spec["defaultModel"]
        if key and key not in spec["models"] and key not in spec["models"].values():
            ap.error("%s does not list model %r" % (spec["label"], key))
        model = spec["models"].get(key, key)
        efforts = providers.model_efforts(spec, model)
        effort = a.effort or (spec["defaultEffort"] if spec["defaultEffort"] in efforts else "")
        if effort and effort not in efforts:
            ap.error("%s does not support effort %r for %s" % (spec["label"], effort, model))
    print(providers.answer(spec, prompt, model, effort, a.timeout))
    return 0


def _interactive():
    return sys.stdin.isatty() and sys.stdout.isatty()


def _print_setup(r):
    model = r["modelId"] or r["model"] or "its own default model"
    if r["ok"]:
        print("confirmed: %s on %s answers (%.1fs). It is the default now." % (
            r["id"], model, r["seconds"]))
        return
    print("FAILED: %s on %s: %s" % (r["id"], model, r["said"] or "no answer"))
    print("  command %s" % r["command"])
    print("  fix     %s" % r["fix"])


def _pick(question, options, default=None):
    """The index of the option the owner picks, or None for an empty answer or `q`."""
    for i, text in enumerate(options, 1):
        print("  %d  %s" % (i, text))
    while True:
        hint = " [%d]" % (default + 1) if default is not None else ""
        try:
            got = input("%s%s, or q to stop: " % (question, hint)).strip()
        except EOFError:
            return None
        if got.lower() == "q" or (not got and default is None):
            return None
        if not got:
            return default
        if got.isdigit() and 1 <= int(got) <= len(options):
            return int(got) - 1
        print("  type a number from 1 to %d" % len(options))


def setup_dialog():
    """Ask in the terminal for the default AI provider and model, check them, and ask again
    after a failure. Returns 0 once they are confirmed, 1 when the owner stops."""
    import providers
    state = providers.default_state()
    print(state["request"])
    while True:
        choices = state["choices"]
        ids = [c["id"] for c in choices]
        default = ids.index(state["provider"]) if state["provider"] in ids else             next((i for i, c in enumerate(choices) if c["installed"]), None)
        i = _pick("The default AI provider", ["%s (%s)%s" % (
            c["label"], c["id"], "" if c["installed"] else ", not installed") for c in choices],
            default)
        if i is None:
            print("Stopped. Run `python octopus.py setup` to choose them later.")
            return 1
        c = choices[i]
        model = ""
        if c["models"]:
            keys = [k for k, _ in c["models"]]
            m = _pick("The default model of %s" % c["label"],
                      ["%s (%s)" % (k, v) for k, v in c["models"]],
                      keys.index(c["defaultModel"]) if c["defaultModel"] in keys else None)
            if m is None:
                print("Stopped. Run `python octopus.py setup` to choose them later.")
                return 1
            model = keys[m]
        print("Checking %s on %s with one short headless call..." % (c["label"], model or
                                                                      "its own default model"))
        r = providers.setup(c["id"], model)
        _print_setup(r)
        if r["ok"]:
            return 0
        state = providers.default_state()


def cmd_setup(rest):
    """The default AI provider and model of this machine (`providers.setup`)."""
    import providers
    ap = argparse.ArgumentParser(prog="octopus setup")
    ap.add_argument("--provider", choices=providers.ids())
    ap.add_argument("--model", help="a key or an id of the provider's models")
    ap.add_argument("--status", action="store_true",
                    help="say whether the default is confirmed; exit 1 when it is not")
    a = ap.parse_args(rest)
    if a.status:
        s = providers.default_state()
        if s["provider"]:
            print("%s %s on %s, %s" % ("confirmed" if s["confirmed"] else "not confirmed",
                                       s["provider"], s["modelId"] or s["model"] or
                                       "its own default model", s["at"]))
            if not s["ok"]:
                print("  said    %s" % s["said"])
                print("  fix     %s" % s["fix"])
        if s["request"]:
            print(s["request"])
        print("file: %s" % providers.default_file())
        return 0 if s["confirmed"] else 1
    if a.provider:
        p = providers.get(a.provider)
        try:
            r = providers.setup(a.provider, a.model if a.model is not None else p["defaultModel"])
        except ValueError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        _print_setup(r)
        return 0 if r["ok"] else 1
    if a.model:
        ap.error("--model needs --provider")
    if not _interactive():
        ap.error("name --provider and --model, or run it in a terminal to be asked")
    return setup_dialog()


def cmd_providers(rest):
    """The AI providers of global settings: the CLIs a launch can start (`providers.py`)."""
    import providers
    ap = argparse.ArgumentParser(prog="octopus providers")
    ap.add_argument("action", nargs="?", default="list",
                    choices=("list", "show", "add", "edit", "remove", "check"))
    ap.add_argument("id", nargs="?")
    ap.add_argument("--json", help="add, edit: the entry, or the fields to change, as JSON")
    ap.add_argument("--label")
    ap.add_argument("--dialect", choices=providers.DIALECTS)
    ap.add_argument("--command", dest="command_", metavar="PATH_OR_COMMAND",
                    help="executable name or full path, including paths with spaces")
    ap.add_argument("--key-env", help="the name of the variable that holds the key")
    ap.add_argument("--env", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--model", action="append", default=[], metavar="KEY=ID",
                    help="a model a launch on it may name; --model KEY= removes one")
    ap.add_argument("--efforts", help="the reasoning efforts it takes, comma-separated")
    ap.add_argument("--default-model", help="a key of its models; empty: its CLI's own")
    ap.add_argument("--default-effort", help="one of its efforts")
    ap.add_argument("--no-check", action="store_true", help="add, edit: do not run the check")
    a = ap.parse_args(rest)
    if a.action == "list":
        v = providers.view()
        for r in v["providers"]:
            last = r["lastCheck"]
            state = "never checked" if not last else "%s %s" % (
                "OK" if last["ok"] else "FAILED", last["at"])
            kind = "built in" + (", changed" if r["overridden"] else "") if r["builtin"] \
                else "own"
            print("%-12s %-22s %-9s %-26s %s" % (r["id"], r["label"], r["dialect"],
                                                  "%s (%s)" % (r["command"], kind), state))
        for p in v["problems"]:
            print("problem: %s" % p)
        print("file: %s" % v["file"])
        return 0
    if not a.id:
        ap.error("name the AI provider")
    if a.action == "show":
        print(json.dumps(providers.get(a.id), indent=1))
        return 0
    if a.action == "remove":
        print("%s %s" % (a.id, providers.remove(a.id)))
        return 0
    if a.action == "check":
        r = providers.check(a.id)
        _print_check(r)
        return 0 if r["ok"] else 1
    if a.action == "add" and providers.entry(a.id):
        ap.error("%s is listed already; `edit` changes it" % a.id)
    if a.action == "edit" and a.id not in providers.load():
        ap.error("no AI provider %r; `add` makes one" % a.id)
    row = providers.entry(a.id) if a.action == "edit" else {}
    if a.json:
        given = json.loads(a.json)
        if not isinstance(given, dict):
            ap.error("--json is a JSON object")
        row.update(given)
    for k, v in (("label", a.label), ("dialect", a.dialect), ("command", a.command_),
                 ("keyEnv", a.key_env), ("defaultModel", a.default_model),
                 ("defaultEffort", a.default_effort), ("efforts", _pack_list(a.efforts))):
        if v is not None:
            row[k] = v
    if a.model:
        # An edit of a built-in starts from the models it runs now.
        models = dict(row.get("models") or providers.load().get(a.id, {}).get("models") or {})
        for kv in a.model:
            key, sep, mid = kv.partition("=")
            if not sep or not key:
                ap.error("--model is KEY=ID")
            if mid:
                models[key] = mid
            else:
                models.pop(key, None)
        row["models"] = models
    for kv in a.env:
        name, sep, value = kv.partition("=")
        if not sep:
            ap.error("--env is NAME=VALUE")
        row.setdefault("env", {})[name] = value
    row["id"] = a.id
    p = providers.save(row)
    print("%s saved in %s" % (p["id"], providers.store_file()))
    if not a.no_check:
        r = providers.check(p["id"])
        _print_check(r)
        return 0 if r["ok"] else 1
    return 0


def cmd_lanes(rest):
    ap = argparse.ArgumentParser(prog="octopus lanes")
    ap.add_argument("path")
    sub = ap.add_subparsers(dest="action")
    sub.add_parser("list")
    p = sub.add_parser("add")
    p.add_argument("dir")
    p.add_argument("--label")
    p.add_argument("--after")
    p.add_argument("--statuses", required=True, help="comma-separated; the first is the default")
    p.add_argument("--color")
    p = sub.add_parser("rename")
    p.add_argument("dir")
    p.add_argument("--label")
    p.add_argument("--dir", dest="new_dir")
    p = sub.add_parser("remove")
    p.add_argument("dir")
    a = ap.parse_args(rest)
    admin = _admin()
    root = project.resolve(a.path)
    try:
        if a.action == "add":
            admin.lane_add(root, a.dir, a.label, a.after,
                           [x.strip() for x in a.statuses.split(",") if x.strip()], a.color)
        elif a.action == "rename":
            admin.lane_rename(root, a.dir, a.label, a.new_dir)
        elif a.action == "remove":
            admin.lane_remove(root, a.dir)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    p = project.load(root)
    if a.action and a.action != "list":
        admin.reindex(root)
    for ln in p.lanes:
        print("%-14s %-16s %-8s %s" % (ln["dir"], ln["label"], ln["role"], ", ".join(ln["statuses"])))
    for prob in p.problems:
        print("  %s" % prob)
    return 1 if p.problems else 0


if __name__ == "__main__":
    sys.exit(main())
