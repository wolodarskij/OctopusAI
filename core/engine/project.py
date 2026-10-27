"""project.py - one project that Octopus AI serves: its config, its rulesets, its paths.

A project keeps everything of its own inside its own tree:

    <project>/
      .octopus/
        project.json          name, slug, rulesets, models, lanes, and the keys below
        rulesets/<name>.json  the project's own rulesets
        brief/<section>.md    brief sections that replace the generic ones
        <pack>.json           the settings a pack's tools read
      TODO/                   the tickets, INDEX.md, GROUPS.md, .board.json, .ids, .briefs/,
                              .sessions/ - the directory name is `todoDir`

Octopus AI holds the engine and the list of the projects it serves. The list lives outside the
Octopus AI tree, in `%APPDATA%/OctopusAI/projects.json`, because it holds project paths.
`OCTOPUS_HOME` names another directory for it.

THE CURRENT PROJECT. The engine reads every path and every rule through `current()`. The CLI
sets it once from `--project` or from the nearest `.octopus/project.json` above the working
directory. The board server sets it for each request, so one server serves every project.
It is a `ContextVar`, so two requests on two threads never see each other's project.

A RULESET is a JSON object. The project lists its rulesets in order; each name resolves to
the project's own `.octopus/rulesets/<name>.json`, else `core/rulesets/<name>.json`, else the
pack `packs/<name>/ruleset.json`, else a pack added on this machine (`packreg.py`). Lists
join in order without duplicates, a later scalar wins, a permit defined twice is a problem, an
unknown key is a problem.

OPTIONAL RULES. A ruleset names the rule files of its payload that a project can switch on and
off: `"optionalRules": {"<file stem>": {"title": "...", "default": true}}`. The global settings
switch one for every project in `<home>/rules.json`, `{"rules": {"<file stem>": false}}`. A
project switches one in `project.json`, `"rules": {"<file stem>": false}`, and its switch wins.
A rule that neither names takes its ruleset's default. `admin` copies an optional rule only
while it is on (`rule_on`); a guard of a rule asks `rule_enabled`.

THE PACKS. `packs/` holds the packs Octopus AI ships. A pack added on this machine is a row of
`<home>/packs.json`, outside the tree, because it names a machine path: a folder used in place,
or a git clone under `<home>/packs/<name>/`. `pack_dir()` and `pack_names()` read both.
"""

from __future__ import annotations

import contextvars
import io
import json
import os
from collections.abc import Mapping
from pathlib import Path

import vcs as _vcs

OCTOPUS = Path(__file__).resolve().parents[2]
CORE = OCTOPUS / "core"
PACKS = OCTOPUS / "packs"
CONFIG_DIR = ".octopus"
PROJECT_FILE = "project.json"
# The default `verifyEnv`.
VERIFY_ENV = "OCTOPUS_VERIFY"

RULESET_LISTS = ("hardRules", "checks", "toolingPaths", "writePaths", "readOnly", "granted",
                 "gitignore", "oldNameAllowed", "oldNameAliases", "detect")
RULESET_KEYS = RULESET_LISTS + ("title", "lookOnScreen", "permits", "terminal", "sessions",
                                "permitOverrides", "deny", "testerPermits", "modePermits", "brief",
                                "payload", "requires", "optionalRules")
PERMIT_KEYS = ("grants", "deny", "addDirEnv", "addDir", "addDirRegistry")
TERMINALS = ("wt", "cmd")
# The old name of each renamed shipped pack -> its name now. A project.json, a `requires` and the
# command line may still say the old one; `admin.migrate_pack_names` rewrites a project to the new.
PACK_ALIASES = {"angelscript": "ue-angelscript", "browser": "browser-game"}

class _Live(Mapping):
    """A read-only dict that reads the provider list (`providers.py`) on every use, so a
    provider the owner adds reaches a running board with no restart."""

    def __init__(self, pick):
        self._pick = pick

    def _now(self):
        import providers
        return {pid: self._pick(p) for pid, p in providers.load().items()}

    def __getitem__(self, k):
        return self._now()[k]

    def __iter__(self):
        return iter(self._now())

    def __len__(self):
        return len(self._now())

    def __repr__(self):
        return repr(self._now())


# The AI providers a launch can start, and the command each runs by default: the built-in
# Claude Code and Codex, and the providers the owner listed (`providers.py`). `provider`
# in project.json names the default; `providers` changes the command, the models and the
# efforts of each. Claude's are the top-level `models`, `efforts`, `defaultModel` and
# `defaultEffort`. A launch may name only a model and an effort of its own provider
# (`Project.choose`).
PROVIDERS = _Live(lambda p: p["command"])
PROVIDER_KEYS = ("command", "models", "efforts", "defaultModel", "defaultEffort")
DEFAULT_PROVIDER = "claude"
# The project.json keys before providers had their name -> the keys now. `Project` reads an old
# key while the new one is absent; `admin.migrate_provider_keys` rewrites it on `update`.
OLD_PROVIDER_KEYS = {"agent": "provider", "agents": "providers"}
# What a launch of each provider can do (`providers.FEATURES`). `hooks`: the board reads its state
# from the hook files and its transcript; without them, from its process and `report`.
# `remoteControl`: Claude Code's Remote Control. `planMode`: a permission mode that keeps a
# plan from editing. `deny`: a tool or a command denied per launch. `resume`: the ticket's own
# session again.
PROVIDER_FEATURES = _Live(lambda p: dict(p["features"]))

def _provider_value_ok(key, value):
    """Whether `value` has the type a `providers.<id>.<key>` takes."""
    if key == "models":
        return isinstance(value, dict) and all(isinstance(v, str) for v in value.values())
    if key == "efforts":
        return _is_str_list(value)
    return isinstance(value, str)


# The roles a lane can have. Each of the first five names exactly one lane; `none` is a lane
# the code has no need of, and a project may add any number of those.
LANE_ROLES = ("start", "active", "blocked", "verify", "closed")

DEFAULT_LANES = [
    {"dir": "Pipeline", "label": "Pipeline", "role": "start", "color": "#c9a227",
     "statuses": ["pipeline"]},
    {"dir": "InProgress", "label": "In Progress", "role": "active", "color": "#4f8bd6",
     "statuses": ["waiting", "investigating", "planning", "ready to implement",
                  "implementing"]},
    {"dir": "Blocked", "label": "Blocked", "role": "blocked", "color": "#c25b5b",
     "statuses": ["blocked by other task", "blocked by user"]},
    {"dir": "Verification", "label": "Verification", "role": "verify", "color": "#8e77d6",
     "statuses": ["waiting", "testing"]},
    {"dir": "Done", "label": "Done", "role": "closed", "color": "#3f9e6b",
     "statuses": ["done", "closed by other task", "cancelled"]},
]

# The statuses the code acts on, by role. A project renames one by mapping the role to
# another word; the word must be a status of the lane the role needs.
STATUS_ROLES = ("pipeline", "waiting", "investigating", "planning", "ready to implement",
                "implementing", "testing", "blocked by other task", "blocked by user", "done",
                "closed by other task", "cancelled")

CATEGORY_CODE = "project code"
CATEGORY_TOOLING = "internal tooling"
DEFAULT_CATEGORIES = {
    CATEGORY_CODE: "the product: what the project ships",
    CATEGORY_TOOLING: "what measures or records the product: tools, docs, the rules, this board",
}
# The values of the old field `kind`, and the category each became. `migrate` rewrites them.
LEGACY_KINDS = {"code": CATEGORY_CODE, "tooling": CATEGORY_TOOLING}
DEFAULT_SEVERITY = {
    "High": "the user gets a wrong result, loses data, or the program stops",
    "Medium": "the user gets a right result today by accident",
    "Low": "the user gets nothing wrong",
}
DEFAULT_DIFFICULTY = {
    "small": "one file, one site, and the change is known",
    "medium": "several sites in one layer, and the change is known",
    "large": "many sites, or it spans several layers, and the change is known",
    "unknown": "the change cannot be sized: the intent, the measurement or the decision "
               "is missing",
}
DEFAULT_QUERY_FIELDS = ["id", "title", "state", "status", "severity", "difficulty",
                        "category", "source", "epic", "permits", "claim", "session", "launched",
                        "sessions", "blockedBy", "group", "related", "path"]
QUERY_FIELDS = DEFAULT_QUERY_FIELDS + ["parent", "children", "tags", "repeat", "run"]


# ── the registry of projects ────────────────────────────────────────────────

def env(name, default=""):
    """The variable `OCTOPUS_<name>`, else `default`."""
    return os.environ.get("OCTOPUS_" + name, "").strip() or default


def home():
    """The directory that holds the registry. Outside the Octopus AI tree."""
    given = env("HOME")
    if given:
        return Path(given)
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "OctopusAI"


def registry_file():
    return home() / "projects.json"


def load_registry():
    """`[{"name", "path"}]`, in the order the projects were registered."""
    try:
        with io.open(registry_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    rows = data.get("projects", []) if isinstance(data, dict) else []
    return [r for r in rows if isinstance(r, dict) and r.get("name") and r.get("path")]


def save_registry(rows):
    p = registry_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"projects": rows}, indent=1) + "\n")
    os.replace(tmp, p)


def register(root, name=None):
    """Add a project to the registry, or update its name. Returns its row."""
    root = Path(root).resolve()
    name = name or load(root).name
    rows = [r for r in load_registry() if Path(r["path"]).resolve() != root]
    if any(r["name"] == name for r in rows):
        raise ValueError("another project is registered as %r" % name)
    row = {"name": name, "path": str(root)}
    rows.append(row)
    save_registry(rows)
    return row


def unregister(root):
    root = Path(root).resolve()
    rows = load_registry()
    keep = [r for r in rows if Path(r["path"]).resolve() != root]
    save_registry(keep)
    return len(rows) - len(keep)


def config_dir(root):
    """The name of the project's config directory."""
    return CONFIG_DIR


def config_path(root, *parts):
    """A path inside the project's config directory."""
    return Path(root).joinpath(config_dir(root), *parts)


def has_config(d):
    """Whether `d` holds `.octopus/project.json`."""
    return (Path(d) / CONFIG_DIR / PROJECT_FILE).is_file()


def find_root(start=None):
    """The nearest directory at or above `start` that `has_config`, or None."""
    d = Path(start or os.getcwd()).resolve()
    for c in [d] + list(d.parents):
        if has_config(c):
            return c
    return None


def resolve(ident=None, start=None):
    """A project root from a registered name, a path, or the working directory."""
    if ident:
        for r in load_registry():
            if r["name"].lower() == str(ident).lower():
                return Path(r["path"])
        p = Path(ident)
        if p.is_dir():
            root = find_root(p)
            if root is None:
                raise SystemExit("error: %s holds no %s/%s" % (p, CONFIG_DIR, PROJECT_FILE))
            return root
        raise SystemExit("error: no project %r: not a registered name and not a directory"
                         % ident)
    root = find_root(start)
    if root is None:
        raise SystemExit("error: no project here: pass --project <name|path>, or run inside "
                         "a directory with %s/%s" % (CONFIG_DIR, PROJECT_FILE))
    return root


# ── loading ─────────────────────────────────────────────────────────────────

def _load_json(path, problems, rel):
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        problems.append("%s is missing" % rel)
        return None
    except (OSError, ValueError) as exc:
        problems.append("%s is not valid JSON: %s" % (rel, exc))
        return None
    if not isinstance(data, dict):
        problems.append("%s is not a JSON object" % rel)
        return None
    return data


def _is_str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def pack_name(name):
    """The name a pack goes by now: `name`, or the new name of a renamed pack."""
    return PACK_ALIASES.get(name, name)


def ruleset_name(root, name):
    """The name a listed ruleset goes by now: a project's own ruleset keeps its name, an old
    pack name becomes the new one."""
    if name in PACK_ALIASES and not (root / config_dir(root) / "rulesets"
                                     / ("%s.json" % name)).is_file():
        return PACK_ALIASES[name]
    return name


def ruleset_path(root, name):
    """(path, where) of one ruleset: the project's own first, then the core, then a pack."""
    cd = config_dir(root)
    own = root / cd / "rulesets" / ("%s.json" % name)
    if own.is_file():
        return own, "%s/rulesets/%s.json" % (cd, name)
    core = CORE / "rulesets" / ("%s.json" % name)
    if core.is_file():
        return core, "core/rulesets/%s.json" % name
    d = pack_dir(name)
    if d is not None:
        return d / "ruleset.json", "packs/%s/ruleset.json" % pack_name(name)
    return own, "rulesets/%s.json" % name


# A pack that `packreg.validate` loads before it is registered: name -> directory.
_CANDIDATE = contextvars.ContextVar("octopus_candidate_pack", default=None)


def pack_registry_file():
    """The packs added on this machine. Outside the Octopus AI tree: it holds machine paths."""
    return home() / "packs.json"


def pack_clone_dir(name):
    """Where the git clone of an added pack lives."""
    return home() / "packs" / name / "repo"


def added_packs():
    """`[{"name", "source", "dir", ...}]`: every row of the pack registry, with the directory
    its `ruleset.json` is in; `dir` is None when that directory is gone."""
    try:
        with io.open(pack_registry_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    for r in data.get("packs", []) if isinstance(data, dict) else []:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        r = dict(r)
        if r.get("source") == "git":
            d = pack_clone_dir(r["name"])
            if r.get("subpath"):
                d = d / r["subpath"]
        else:
            d = Path(r["path"]) if r.get("path") else None
        r["dir"] = d if d is not None and (d / "ruleset.json").is_file() else None
        out.append(r)
    return out


def pack_dir(name):
    """The directory a pack's ruleset, payload and brief sections are read from, or None:
    a pack Octopus AI ships, else one added on this machine."""
    cand = _CANDIDATE.get()
    if cand and name in cand:
        return cand[name]
    name = pack_name(name)
    d = PACKS / name
    if (d / "ruleset.json").is_file():
        return d
    for r in added_packs():
        if r["name"] == name:
            return r["dir"]
    return None


def pack_names():
    """Every available pack: the shipped ones, then the added ones, each once."""
    names = [d.name for d in sorted(PACKS.iterdir()) if (d / "ruleset.json").is_file()]
    names += [r["name"] for r in added_packs() if r["dir"] is not None and r["name"] not in names]
    cand = _CANDIDATE.get() or {}
    return names + [n for n in cand if n not in names]


def with_candidate(packs):
    """Read `packs` (name -> directory) as packs until `reset_candidate(token)`."""
    return _CANDIDATE.set(dict(packs))


def reset_candidate(token):
    _CANDIDATE.reset(token)


def load_rules(root, project=None):
    """(rules, problems): project.json merged with its rulesets. A broken file costs its own
    contribution, never the whole tool. `project`, a candidate project.json dict, is read in
    place of the file, so a change can be checked before it is written."""
    problems = []
    rules = {"name": "", "slug": "", "description": "", "rulesets": [], "models": {},
             "efforts": [], "defaultModel": "", "defaultEffort": "", "permits": {},
             "lookOnScreen": "", "terminal": "", "sessions": False, "contributions": {},
             "deny": [], "testerPermits": [], "modePermits": {}, "brief": {}, "payload": {},
             "requires": {}, "sources": {}, "optionalRules": {}}
    for k in RULESET_LISTS:
        rules[k] = []
    rel = "%s/%s" % (config_dir(root), PROJECT_FILE)
    if project is None:
        project = _load_json(config_path(root, PROJECT_FILE), problems, rel) or {}
    rules["project"] = project
    for k in ("name", "slug", "description", "defaultModel", "defaultEffort"):
        v = project.get(k, "")
        if not isinstance(v, str) or (k in ("name", "slug") and not v):
            problems.append("%s: `%s` must be a %sstring"
                            % (rel, k, "non-empty " if k in ("name", "slug") else ""))
            continue
        rules[k] = v
    if "models" in project:
        m = project["models"]
        if isinstance(m, dict) and all(isinstance(v, str) for v in m.values()):
            rules["models"] = dict(m)
        else:
            problems.append("%s: `models` must map a key to a model id" % rel)
    if "efforts" in project:
        if _is_str_list(project["efforts"]):
            rules["efforts"] = list(project["efforts"])
        else:
            problems.append("%s: `efforts` must be a list of strings" % rel)
    # Claude's lists are its provider's under these; `Project._providers` checks the defaults
    # against the merged lists.
    names = project.get("rulesets", [])
    if not _is_str_list(names):
        problems.append("%s: `rulesets` must be a list of names" % rel)
        names = []
    names = [ruleset_name(root, n) for n in names]
    permit_owner = {}
    for name in names:
        if name in rules["rulesets"]:
            problems.append("%s lists ruleset %r twice" % (rel, name))
            continue
        path, where = ruleset_path(root, name)
        data = _load_json(path, problems, where)
        if data is None:
            continue
        rules["rulesets"].append(name)
        rules["sources"][name] = path
        added = {}
        for k in data:
            if k not in RULESET_KEYS:
                problems.append("%s: unknown key %r; one of %s"
                                % (where, k, ", ".join(RULESET_KEYS)))
        for k in RULESET_LISTS:
            if k not in data:
                continue
            if not _is_str_list(data[k]):
                problems.append("%s: `%s` must be a list of strings" % (where, k))
                continue
            new = [x for x in data[k] if x not in rules[k]]
            rules[k] += new
            if new:
                added[k] = new
        for k in ("title", "lookOnScreen"):
            if k in data:
                if isinstance(data[k], str):
                    if k == "lookOnScreen":
                        rules[k] = data[k]
                    added[k] = data[k]
                else:
                    problems.append("%s: `%s` must be a string" % (where, k))
        if "terminal" in data:
            if data["terminal"] in TERMINALS:
                rules["terminal"] = added["terminal"] = data["terminal"]
            else:
                problems.append("%s: `terminal` must be one of %s"
                                % (where, ", ".join(TERMINALS)))
        if "sessions" in data:
            if isinstance(data["sessions"], bool):
                rules["sessions"] = added["sessions"] = data["sessions"]
            else:
                problems.append("%s: `sessions` must be true or false" % where)
        permits = data.get("permits", {})
        if not isinstance(permits, dict):
            problems.append("%s: `permits` must map a name to its definition" % where)
            permits = {}
        for pname, spec in permits.items():
            if pname in permit_owner:
                problems.append("%s defines permit %r, already defined by %s"
                                % (where, pname, permit_owner[pname]))
                continue
            if not isinstance(spec, dict) or not isinstance(spec.get("grants"), str) \
                    or not _is_str_list(spec.get("deny", [])) \
                    or not isinstance(spec.get("addDirEnv", ""), str) \
                    or not isinstance(spec.get("addDir", ""), str) \
                    or not isinstance(spec.get("addDirRegistry", False), bool) \
                    or any(k not in PERMIT_KEYS for k in spec):
                problems.append("%s: permit %r needs `grants` (string), and optionally `deny` "
                                "(list of strings), `addDirEnv` and `addDir` (strings), "
                                "`addDirRegistry` (true or false), nothing else"
                                % (where, pname))
                continue
            permit_owner[pname] = where
            rules["permits"][pname] = {
                "grants": spec["grants"], "deny": list(spec.get("deny", [])),
                "addDirEnv": spec.get("addDirEnv", "").replace("{SLUG}", rules["slug"].upper()),
                "addDir": spec.get("addDir", ""),
                "addDirRegistry": spec.get("addDirRegistry", False)}
            added.setdefault("permits", []).append(pname)
        over = data.get("permitOverrides", {})
        if not isinstance(over, dict):
            problems.append("%s: `permitOverrides` must map a permit to its changes" % where)
            over = {}
        for pname, spec in over.items():
            if pname not in rules["permits"]:
                problems.append("%s overrides permit %r, which no earlier ruleset defines"
                                % (where, pname))
                continue
            if not isinstance(spec, dict) or any(
                    not (isinstance(v, bool) if k == "addDirRegistry" else
                         k in ("grants", "addDirEnv", "addDir") and isinstance(v, str))
                    for k, v in spec.items()):
                problems.append("%s: the override of %r takes only `grants`, `addDirEnv` and "
                                "`addDir`, as strings, and `addDirRegistry`, as true or false"
                                % (where, pname))
                continue
            for k, v in spec.items():
                rules["permits"][pname][k] = (v.replace("{SLUG}", rules["slug"].upper())
                                              if k == "addDirEnv" else v)
            added.setdefault("permitOverrides", []).append(pname)
        deny = data.get("deny", [])
        if not isinstance(deny, list) or not all(
                isinstance(d, dict) and _is_str_list(d.get("tools")) and
                _is_str_list(d.get("unless", [])) and set(d) <= {"tools", "unless"}
                for d in deny):
            problems.append("%s: `deny` must be a list of {tools, unless}" % where)
            deny = []
        for d in deny:
            rules["deny"].append({"tools": list(d["tools"]), "unless": list(d.get("unless", []))})
            added.setdefault("deny", []).extend(d["tools"])
        if "testerPermits" in data:
            if _is_str_list(data["testerPermits"]):
                new = [x for x in data["testerPermits"] if x not in rules["testerPermits"]]
                rules["testerPermits"] += new
                if new:
                    added["testerPermits"] = new
            else:
                problems.append("%s: `testerPermits` must be a list of permit names" % where)
        if "modePermits" in data:
            mp = data["modePermits"]
            if isinstance(mp, dict) and all(_is_str_list(v) for v in mp.values()):
                for mode, names in mp.items():
                    have = rules["modePermits"].setdefault(mode, [])
                    new = [x for x in names if x not in have]
                    have += new
                    if new:
                        added.setdefault("modePermits", []).append(
                            "%s=%s" % (mode, "+".join(new)))
            else:
                problems.append("%s: `modePermits` must map a launch mode to a list of permit"
                                " names" % where)
        if "brief" in data:
            if _is_str_list(data["brief"]):
                d = path.parent / "brief"
                for sec in data["brief"]:
                    rules["brief"][sec] = d / ("%s.md" % sec)
                added["brief"] = list(data["brief"])
            else:
                problems.append("%s: `brief` must be a list of section names" % where)
        if "payload" in data:
            pl = data["payload"]
            if isinstance(pl, list) and all(isinstance(e, dict) and isinstance(e.get("from"), str)
                                            and isinstance(e.get("to"), str) for e in pl):
                base = CORE if where.startswith("core/") else path.parent
                rules["payload"][name] = [(base, e["from"], e["to"]) for e in pl]
                added["payload"] = ["%s -> %s" % (e["from"], e["to"]) for e in pl]
            else:
                problems.append("%s: `payload` must be a list of {from, to}" % where)
        if "requires" in data:
            if _is_str_list(data["requires"]):
                need = [ruleset_name(root, n) for n in data["requires"]]
                rules["requires"][name] = need
                added["requires"] = list(need)
            else:
                problems.append("%s: `requires` must be a list of ruleset names" % where)
        if "optionalRules" in data:
            opt = data["optionalRules"]
            if isinstance(opt, dict) and all(
                    isinstance(s, dict) and isinstance(s.get("title", ""), str)
                    and isinstance(s.get("default", True), bool) for s in opt.values()):
                for stem, s in opt.items():
                    if stem in rules["optionalRules"]:
                        problems.append("%s: optional rule %r is defined twice" % (where, stem))
                        continue
                    rules["optionalRules"][stem] = {"ruleset": name, "title": s.get("title", ""),
                                                    "default": s.get("default", True)}
                added["optionalRules"] = list(opt)
            else:
                problems.append("%s: `optionalRules` must map a rule file stem to "
                                "{title, default}" % where)
        rules["contributions"][name] = added
    for name, need in rules["requires"].items():
        for n in need:
            if n not in rules["rulesets"]:
                problems.append("ruleset %s requires %s, which the project does not list"
                                % (name, n))
            elif rules["rulesets"].index(n) > rules["rulesets"].index(name):
                problems.append("ruleset %s requires %s, which the project lists after it"
                                % (name, n))
    switches = project.get("rules", {})
    if not isinstance(switches, dict) or not all(isinstance(v, bool) for v in switches.values()):
        problems.append("%s: `rules` must map an optional rule to true or false" % rel)
    else:
        for stem in switches:
            if stem not in rules["optionalRules"]:
                problems.append("%s: `rules` names %r, which no ruleset makes optional"
                                % (rel, stem))
    for p in rules["testerPermits"]:
        if p not in rules["permits"]:
            problems.append("testerPermits names %r, which is not a permit" % p)
    for mode, names in rules["modePermits"].items():
        for p in names:
            if p not in rules["permits"]:
                problems.append("modePermits of %s names %r, which is not a permit" % (mode, p))
    for d in rules["deny"]:
        for p in d["unless"]:
            if p not in rules["permits"]:
                problems.append("a deny rule names permit %r, which is not a permit" % p)
    return rules, problems


def global_rules_file():
    """The global switches of the optional rules. Outside the Octopus AI tree."""
    return home() / "rules.json"


def global_rules():
    """The global switches: an optional rule's file stem -> true or false."""
    try:
        with io.open(global_rules_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    rows = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        return {}
    return {k: v for k, v in rows.items() if isinstance(v, bool)}


def save_global_rules(switches):
    p = global_rules_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"rules": dict(sorted(switches.items()))}, indent=1) + "\n")
    os.replace(tmp, p)


def rule_source(stem, project, glob=None):
    """Where the state of the optional rule `stem` comes from: "project" (the switch in the
    project.json dict `project`), "global" (the global switch) or "default" (its ruleset's)."""
    switches = (project or {}).get("rules")
    if isinstance(switches, dict) and isinstance(switches.get(stem), bool):
        return "project"
    if stem in (global_rules() if glob is None else glob):
        return "global"
    return "default"


def rule_on(stem, default, project, glob=None):
    """Whether the optional rule `stem` is on: the switch in the project.json dict `project`,
    else the global switch (`glob`, read from `global_rules_file` when None), else `default`,
    the rule's own."""
    glob = global_rules() if glob is None else glob
    source = rule_source(stem, project, glob)
    if source == "project":
        return project["rules"][stem]
    if source == "global":
        return glob[stem]
    return default


def rule_enabled(root, stem):
    """Whether the rule `stem` holds in the project at `root`, for a guard that enforces it. A
    rule no ruleset of the project makes optional always holds. With no project (`root` None),
    the global switch decides, else the rule holds."""
    if root is None:
        return global_rules().get(stem, True)
    rules, _problems = load_rules(root)
    spec = rules["optionalRules"].get(stem)
    if spec is None:
        return True
    return rule_on(stem, spec["default"], rules.get("project") or {})


class Project:
    """Everything the engine reads about one project. Built once per load; cheap."""

    def __init__(self, root, cfg=None):
        """`cfg`, a candidate project.json dict, is read in place of the file."""
        root = Path(root).resolve()
        self.ROOT = root
        self.config_dir = config_dir(root)
        self.CONFIG = root / self.config_dir
        rules, problems = load_rules(root, cfg)
        self.RULES, self.problems = rules, list(problems)
        cfg = rules.get("project", {})
        self.cfg = cfg
        self.name = rules["name"] or root.name
        self.slug = rules["slug"] or root.name.lower()
        self.description = rules["description"]
        self.title = cfg.get("title") or self.name
        self.cli = cfg.get("cli") or "python %s/todo.py" % self.config_dir
        ns = cfg.get("sessionNamespace") or "%s-todo" % self.slug
        self.session_namespace = ns
        self.verify_env = cfg.get("verifyEnv") or VERIFY_ENV
        self.verify_value = cfg.get("verifyValue") or "{ns}:{id}"
        todo_dir = cfg.get("todoDir") or "TODO"
        self.todo_dir = todo_dir
        self.TODO = root / todo_dir
        self.INDEX = self.TODO / "INDEX.md"
        self.IDFILE = self.TODO / ".ids"
        self.BRIEFS = self.TODO / ".briefs"
        self.GROUPS = self.TODO / "GROUPS.md"
        self.GROUPS_MAP = self.TODO / ".groups.json"
        self.GROUPS_DIR = self.TODO / "groups"
        self.BOARD = self.TODO / ".board.json"
        self.SESSIONS_DIR = self.TODO / ".sessions"
        self.SESSIONS_BOARD = root / (cfg.get("sessionsBoard") or "%s/SESSIONS.md" % self.config_dir)
        g = cfg.get("groups") or {}
        self.groups_store = g.get("store", "map") if isinstance(g, dict) else "map"
        self.groups_required = bool(g.get("required", False)) if isinstance(g, dict) else False
        if self.groups_store not in ("map", "files"):
            self.problems.append("%s/%s: `groups.store` must be map or files"
                                 % (self.config_dir, PROJECT_FILE))
            self.groups_store = "map"
        self.vcs = cfg.get("vcs") or "none"
        if self.vcs not in _vcs.KINDS:
            self.problems.append("%s/%s: `vcs` is %r; one of %s"
                                 % (self.config_dir, PROJECT_FILE, self.vcs, ", ".join(_vcs.KINDS)))
            self.vcs = "none"
        self.vcs_pull = cfg.get("vcsPull") or _vcs.DEFAULT_PULL
        if self.vcs_pull not in _vcs.PULL_ARGS:
            self.problems.append("%s/%s: `vcsPull` is %r; one of %s"
                                 % (self.config_dir, PROJECT_FILE, self.vcs_pull,
                                    ", ".join(_vcs.PULL_ARGS)))
            self.vcs_pull = _vcs.DEFAULT_PULL
        self.auto_groups = []
        for ag in cfg.get("autoGroups") or []:
            if isinstance(ag, dict) and ag.get("name") and ag.get("tag"):
                self.auto_groups.append({"name": ag["name"], "tag": ag["tag"],
                                         "note": ag.get("note", "")})
            else:
                self.problems.append("%s/%s: every `autoGroups` entry needs `name` and `tag`"
                                     % (self.config_dir, PROJECT_FILE))
        qf = cfg.get("queryFields") or DEFAULT_QUERY_FIELDS
        if "kind" in qf or "kinds" in cfg:
            self.problems.append("%s/%s: the field `kind` is now `category`; run: %s migrate"
                                 % (self.config_dir, PROJECT_FILE, self.cli))
            qf = [("category" if f == "kind" else f) for f in qf]
        bad = [f for f in qf if f not in QUERY_FIELDS]
        if bad:
            self.problems.append("%s/%s: `queryFields` names %s; one of %s"
                                 % (self.config_dir, PROJECT_FILE, ", ".join(bad),
                                    ", ".join(QUERY_FIELDS)))
        self.query_fields = [f for f in qf if f in QUERY_FIELDS]
        # `remoteControl`: whether a launch runs with Claude Code's Remote Control, unless
        # its ticket says `remote-control: on|off`.
        rc = cfg.get("remoteControl", True)
        if not isinstance(rc, bool):
            self.problems.append("%s/%s: `remoteControl` is true or false"
                                 % (self.config_dir, PROJECT_FILE))
            rc = True
        self.remote_control = rc
        # `replyCheck`: whether the `Stop` hook checks the last message of every session
        # against the reporting rule (`core/hooks/reply_check.py`).
        rc = cfg.get("replyCheck", True)
        if not isinstance(rc, bool):
            self.problems.append("%s/%s: `replyCheck` is true or false"
                                 % (self.config_dir, PROJECT_FILE))
            rc = True
        self.reply_check = rc
        self._lanes(cfg.get("lanes") or DEFAULT_LANES, cfg.get("statusRoles") or {})
        cats = cfg.get("categories") or {}
        if not isinstance(cats, dict) or not all(isinstance(v, str) for v in cats.values()):
            self.problems.append("%s/%s: `categories` must map a category to its meaning"
                                 % (self.config_dir, PROJECT_FILE))
            cats = {}
        self.CATEGORY_MEANING = dict(DEFAULT_CATEGORIES, **cats)
        self.CATEGORIES = tuple(self.CATEGORY_MEANING)
        sev = cfg.get("severityMeaning") or DEFAULT_SEVERITY
        self.SEVERITY_MEANING = dict(sev)
        self.DIFF_MEANING = dict(DEFAULT_DIFFICULTY, **(cfg.get("difficultyMeaning") or {}))
        self.TOOLING_PREFIX = tuple(rules["toolingPaths"])
        self.PERMITS = {n: s["grants"] for n, s in rules["permits"].items()}
        self.TESTER_PERMITS = tuple(rules["testerPermits"])
        self.MODE_PERMITS = {m: tuple(v) for m, v in rules["modePermits"].items()}
        self.terminal = rules["terminal"] or "wt"
        self._providers(cfg, rules)
        # Claude's lists and defaults, under the names the rest of the engine reads.
        claude = self.providers[DEFAULT_PROVIDER]
        self.models = claude["models"]
        self.efforts = claude["efforts"]
        self.default_model_key = claude["defaultModel"]
        self.default_model = self.models.get(claude["defaultModel"], "")
        self.default_effort = claude["defaultEffort"]

    def _providers(self, cfg, rules=None):
        """`provider`, the provider a launch starts unless it names another, and `providers`,
        the command, the models and the efforts of each provider. Either names any provider of
        the provider list; what the project leaves out comes from the list. Claude's own come
        from the top-level keys too. A default the provider cannot run is a problem, and the
        list's default takes its place. A project that names no provider launches the machine's
        default (`providers.default_id`). The old keys `agent` and `agents` are read while the
        new ones are absent."""
        import providers
        known = providers.load()
        where = "%s/%s" % (self.config_dir, PROJECT_FILE)
        self.provider = cfg.get("provider") or cfg.get("agent") or providers.default_id()
        if self.provider not in known:
            self.problems.append("%s: `provider` is %r; one of %s"
                                 % (where, self.provider, ", ".join(known)))
            self.provider = DEFAULT_PROVIDER
        self.providers = {}
        given = cfg.get("providers") if "providers" in cfg else cfg.get("agents")
        given = given or {}
        if not isinstance(given, dict):
            self.problems.append("%s: `providers` must map an AI provider to its settings"
                                 % where)
            given = {}
        # The project's own settings of each provider, as project.json holds them.
        self.provider_settings = given
        for name, spec in given.items():
            if name not in known:
                self.problems.append("%s: `providers` names %r; one of %s"
                                     % (where, name, ", ".join(known)))
            elif not isinstance(spec, dict) or set(spec) - set(PROVIDER_KEYS) or not all(
                    _provider_value_ok(k, v) for k, v in spec.items()):
                self.problems.append("%s: `providers.%s` takes %s; `models` maps a key to a "
                                     "model id, `efforts` is a list, the rest are strings"
                                     % (where, name, ", ".join(PROVIDER_KEYS)))
        top = {}
        if rules is not None:
            top = {k: rules[k] for k in ("models", "efforts", "defaultModel", "defaultEffort")
                   if rules.get(k)}
        for name, p in known.items():
            spec = given.get(name) if isinstance(given.get(name), dict) else {}
            spec = {k: v for k, v in spec.items() if _provider_value_ok(k, v)}
            if name == DEFAULT_PROVIDER:
                spec = dict(top, **spec)
            models = dict(p["models"], **spec.get("models", {}))
            efforts = list(spec.get("efforts") or p["efforts"])
            a = {"command": spec.get("command") or p["command"], "models": models,
                 "efforts": efforts if p["features"]["effort"] else [],
                 "defaultModel": spec.get("defaultModel") or p["defaultModel"],
                 "defaultEffort": spec.get("defaultEffort") or p["defaultEffort"]}
            a["modelEfforts"] = {mid: providers.model_efforts(p, mid, a["efforts"])
                                 for mid in models.values()}
            if a["defaultModel"] and a["defaultModel"] not in models:
                self.problems.append("%s: `defaultModel` %r of %s is not one of its models "
                                     "(%s); its default %s applies"
                                     % (where, a["defaultModel"], p["label"],
                                        ", ".join(models) or "none",
                                        p["defaultModel"] or "of the CLI"))
                a["defaultModel"] = p["defaultModel"] if p["defaultModel"] in models else ""
            default_efforts = a["modelEfforts"].get(models.get(a["defaultModel"]), a["efforts"])
            if a["defaultEffort"] and a["defaultEffort"] not in default_efforts:
                if spec.get("defaultEffort"):
                    self.problems.append("%s: `defaultEffort` %r of %s is not one of its "
                                         "efforts (%s)" % (where, a["defaultEffort"], p["label"],
                                                           ", ".join(default_efforts) or "none"))
                a["defaultEffort"] = (p["defaultEffort"] if p["defaultEffort"] in default_efforts
                                      else "")
            self.providers[name] = a

    def choose(self, provider, model=None, effort=None):
        """`(model id, effort)` of a launch on `provider`: the model named, a key or an id of
        the provider's models, else its default; the effort named, one of its efforts, else its
        default. Either is "" when the provider takes none and the CLI picks. Raises
        ValueError, naming what the provider can run, for a model or an effort it cannot."""
        import providers
        a = self.providers.get(provider)
        if a is None:
            raise ValueError("%r is not an AI provider. One of: %s"
                             % (provider, ", ".join(self.providers)))
        label = providers.label(provider)
        models = a["models"]
        if model and model not in models and model not in models.values():
            raise ValueError("%s cannot run the model %r. Its models: %s"
                             % (label, model, ", ".join(models) or
                                "none listed; add them to %s in Global settings" % label))
        model = model or a["defaultModel"]
        model = models.get(model, model) if model else ""
        efforts = a["modelEfforts"].get(model, a["efforts"])
        effort = effort or (a["defaultEffort"] if a["defaultEffort"] in efforts else "")
        if effort and effort not in efforts:
            if not efforts:
                raise ValueError("%s takes no reasoning effort" % label)
            raise ValueError("%s takes no effort %r%s. Its efforts: %s"
                             % (label, effort, " for " + model if model else "",
                                ", ".join(efforts)))
        return model, effort

    def _lanes(self, lanes, roles):
        where = "%s/%s" % (self.config_dir, PROJECT_FILE)
        out, seen = [], set()
        for ln in lanes if isinstance(lanes, list) else []:
            if not isinstance(ln, dict) or not ln.get("dir"):
                self.problems.append("%s: every lane needs a `dir`" % where)
                continue
            d = ln["dir"]
            if d in seen:
                self.problems.append("%s: lane %r is listed twice" % (where, d))
                continue
            seen.add(d)
            st = ln.get("statuses")
            if not _is_str_list(st) or not st:
                self.problems.append("%s: lane %r has no statuses" % (where, d))
                st = ["waiting"]
            role = ln.get("role") or "none"
            if role not in LANE_ROLES + ("none",):
                self.problems.append("%s: lane %r has role %r; one of %s, none"
                                     % (where, d, role, ", ".join(LANE_ROLES)))
                role = "none"
            out.append({"dir": d, "label": ln.get("label") or d, "role": role,
                        "color": ln.get("color") or "#888888", "statuses": list(st)})
        self.lanes = out
        self.STATES = tuple(ln["dir"] for ln in out)
        self.STATE_LABEL = {ln["dir"]: ln["label"] for ln in out}
        self.STATUSES = {ln["dir"]: tuple(ln["statuses"]) for ln in out}
        self.ROLE = {}
        for r in LANE_ROLES:
            hits = [ln["dir"] for ln in out if ln["role"] == r]
            if len(hits) != 1:
                self.problems.append("%s: role %r names %s lane(s); it must name exactly one"
                                     % (where, r, len(hits)))
            self.ROLE[r] = hits[0] if hits else (self.STATES[0] if self.STATES else r)
        self.S = {r: (roles.get(r) or r) for r in STATUS_ROLES}
        need = {"pipeline": "start", "implementing": "active", "planning": "active",
                "investigating": "active", "ready to implement": "active",
                "waiting": "active", "testing": "verify", "blocked by other task": "blocked",
                "blocked by user": "blocked", "done": "closed",
                "closed by other task": "closed", "cancelled": "closed"}
        for r, lane_role in need.items():
            lane = self.ROLE[lane_role]
            if self.S[r] not in self.STATUSES.get(lane, ()):
                self.problems.append("%s: status %r (role %r) is not a status of lane %s"
                                     % (where, self.S[r], r, lane))
        active, verify, closed = self.ROLE["active"], self.ROLE["verify"], self.ROLE["closed"]
        self.OWNER_CLOSES = (self.S["closed by other task"], self.S["cancelled"])
        self.OWNER_BLOCKS = self.S["blocked by user"]
        # Open lanes: every lane before the verify lane, the added lanes included.
        vi = self.STATES.index(verify) if verify in self.STATES else len(self.STATES)
        self.OPEN_STATES = tuple(s for s in self.STATES[:vi] if s != closed)
        self.LAUNCH_LANE = {
            "work": (active, self.S["implementing"]),
            "plan": (active, self.S["planning"]),
            "review": (active, self.S["investigating"]),
            "verify": (verify, self.S["testing"]),
        }

    # ── lookups ─────────────────────────────────────────────────────────────

    def lane(self, role):
        """The directory of the lane with this role."""
        return self.ROLE[role]

    def label(self, role):
        return self.STATE_LABEL[self.ROLE[role]]

    def add_dir(self, permit):
        """The path a granted permit adds to the session, or empty."""
        spec = self.RULES["permits"].get(permit, {})
        env = spec.get("addDirEnv", "")
        val = os.environ.get(env, "").strip() if env else ""
        return val or spec.get("addDir", "")

    def add_dirs(self, permit):
        """Every path a granted permit adds to the session: its `add_dir`, then, for an
        `addDirRegistry` permit, the root of every other registered project that exists."""
        out = []
        one = self.add_dir(permit)
        if one:
            out.append(one)
        if self.RULES["permits"].get(permit, {}).get("addDirRegistry"):
            for r in load_registry():
                root = Path(r["path"])
                if root.is_dir() and root.resolve() != self.ROOT \
                        and str(root.resolve()) not in out:
                    out.append(str(root.resolve()))
        return out

    def denied_tools(self, permits):
        """What the launched session cannot reach at the tool level: the `deny` patterns of
        every permit it is not granted, then every `deny` rule none of whose permits it
        holds."""
        out = []
        for name, spec in self.RULES["permits"].items():
            if name not in permits:
                out += [d for d in spec["deny"] if d not in out]
        for rule in self.RULES["deny"]:
            if not any(p in permits for p in rule["unless"]):
                out += [d for d in rule["tools"] if d not in out]
        return out

    def brief_layers(self):
        """section name -> path, lowest layer first overridden by higher: core, then every
        ruleset's brief sections in ruleset order, then the project's own."""
        found = {}
        core = CORE / "engine" / "brief"
        for p in sorted(core.glob("*.md")):
            if p.stem != "README":
                found[p.stem] = p
        for sec, p in self.RULES["brief"].items():
            if p.is_file():
                found[sec] = p
        own = self.CONFIG / "brief"
        if own.is_dir():
            for p in sorted(own.glob("*.md")):
                if p.stem != "README":
                    found[p.stem] = p
        # The `provider` section was named `agent` before: a ruleset's or a project's own
        # `agent.md` still replaces the core section, unless that layer has a `provider.md`.
        old = found.pop("agent", None)
        if old is not None and found.get("provider", core / "provider.md").parent == core:
            found["provider"] = old
        return found

    def verify_token(self, tid):
        return self.verify_value.replace("{ns}", self.session_namespace).replace("{id}", tid)

    def verify_env_value(self):
        """The verify value in the environment."""
        return os.environ.get(self.verify_env, "")

    def verify_ok(self, value):
        """Whether a verify value in the environment belongs to this project."""
        if not value:
            return False
        if "{ns}" not in self.verify_value:
            return True
        return value.startswith(self.session_namespace + ":")


_CURRENT = contextvars.ContextVar("octopus_project", default=None)
_CACHE = {}


def load(root):
    """The project at `root`. Reloaded when its config changed on disk."""
    root = Path(root).resolve()
    stamp = _stamp(root)
    hit = _CACHE.get(root)
    if hit and hit[0] == stamp:
        return hit[1]
    p = Project(root)
    _CACHE[root] = (stamp, p)
    return p


def _stamp(root):
    out = []
    d = root / config_dir(root)
    # The AI provider list too, under its name now and its old one: its models and efforts are
    # each provider's in every project.
    for p in [d / PROJECT_FILE, home() / "providers.json", home() / "agents.json"] \
            + sorted((d / "rulesets").glob("*.json")) + sorted((d / "brief").glob("*.md")):
        try:
            out.append((str(p), p.stat().st_mtime_ns))
        except OSError:
            pass
    return tuple(out)


def use(root_or_project):
    """Make a project the current one. Returns the token `reset` takes."""
    p = root_or_project if isinstance(root_or_project, Project) else load(root_or_project)
    return _CURRENT.set(p)


def reset(token):
    _CURRENT.reset(token)


def current():
    p = _CURRENT.get()
    if p is None:
        raise RuntimeError("no current project: call project.use(root) first")
    return p


class _Proxy:
    """`P.STATES` reads `current().STATES`, so the engine names the current project's values
    the way it named module constants."""

    def __getattr__(self, name):
        return getattr(current(), name)


P = _Proxy()
