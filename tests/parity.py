"""parity.py - does Octopus AI give the same answers as a project's own copy of the engine?

    python tests/parity.py --old <dir with the old todo.py> --project <project dir>
    python tests/parity.py --old <dir> --project <dir> --legacy-config \
        --groups files --groups-required --query-fields id,title,...

Nothing is written into the project. The script copies the project's ticket tree twice into a
temporary directory:

    A/   the ticket tree and the old engine under A/tools/, run as the project runs it
    B/   the ticket tree and the project's .octopus/ config, run by Octopus AI

and compares, byte for byte:

    INDEX.md and GROUPS.md after `index`       (both engines, as subprocesses)
    query --json, check (output and exit code) (both engines, as subprocesses)
    rulesets, groups                           (only with --legacy-config)
    the brief of every ticket in every mode    (both engines, imported)
    the `work --dry-run` line of every ticket in every mode
    the session id of every ticket, mode and run 1-3

`--legacy-config` is for a project whose old engine keeps its config in
`TODO/project.json` and `TODO/rulesets/`: B gets an `.octopus/` built from those files, plus
the Octopus AI keys named on the command line. The first line of `rulesets` names the config
file, so that one path is compared with the file name normalised.

Exit 0 when everything is identical; exit 1 with the first differences otherwise.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OCTOPUS = Path(__file__).resolve().parents[1]
ENGINE = OCTOPUS / "core" / "engine"
MODES = ("work", "plan", "review", "verify")


def copy_tree(src, dst):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".sessions", ".briefs", "*.tmp"))


def build_a(project, old, work, todo_dir):
    a = work / "A"
    (a / "tools").mkdir(parents=True)
    copy_tree(project / todo_dir, a / todo_dir)
    for f in Path(old).glob("*.py"):
        shutil.copy2(f, a / "tools" / f.name)
    return a


def build_b(project, work, todo_dir, args):
    b = work / "B"
    b.mkdir(parents=True)
    copy_tree(project / todo_dir, b / todo_dir)
    if args.legacy_config:
        cfg = b / ".octopus"
        (cfg / "rulesets").mkdir(parents=True)
        pj = json.loads((project / todo_dir / "project.json").read_text(encoding="utf-8"))
        pj.setdefault("cli", "python tools/todo.py")
        pj.setdefault("sessionNamespace", args.session_namespace or pj["slug"] + "-todo")
        pj["groups"] = {"store": args.groups, "required": bool(args.groups_required)}
        if args.query_fields:
            pj["queryFields"] = args.query_fields.split(",")
        (cfg / "project.json").write_text(json.dumps(pj, indent=1), encoding="utf-8")
        for f in (project / todo_dir / "rulesets").glob("*.json"):
            shutil.copy2(f, cfg / "rulesets" / f.name)
    else:
        shutil.copytree(project / ".octopus", b / ".octopus")
    return b


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return r.returncode, r.stdout


def old_cli(a, *argv):
    return run([sys.executable, str(a / "tools" / "todo.py")] + list(argv), a)


def new_cli(b, *argv):
    return run([sys.executable, str(OCTOPUS / "octopus.py"), "todo", "--project", str(b)]
               + list(argv), b)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(path).parent))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


class Report:
    def __init__(self):
        self.checked, self.diffs = 0, []

    def same(self, label, x, y):
        self.checked += 1
        if x != y:
            d = "".join(list(difflib.unified_diff(
                str(x).splitlines(True), str(y).splitlines(True), "old", "octopus", n=1))[:40])
            self.diffs.append("%s\n%s" % (label, d))
            return False
        return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--old", required=True, help="the directory holding the old todo.py")
    ap.add_argument("--project", required=True, help="the project whose ticket tree is copied")
    ap.add_argument("--todo-dir", default="TODO")
    ap.add_argument("--legacy-config", action="store_true")
    ap.add_argument("--groups", default="map", choices=("map", "files"))
    ap.add_argument("--groups-required", action="store_true")
    ap.add_argument("--query-fields", default="")
    ap.add_argument("--session-namespace", default="")
    ap.add_argument("--no-briefs", action="store_true", help="skip the per-ticket comparison")
    ap.add_argument("--keep", action="store_true", help="keep the temporary directory")
    args = ap.parse_args(argv)

    project = Path(args.project).resolve()
    work = Path(tempfile.mkdtemp(prefix="octopus-parity-"))
    rep = Report()
    try:
        a = build_a(project, args.old, work, args.todo_dir)
        b = build_b(project, work, args.todo_dir, args)
        todo_a, todo_b = a / args.todo_dir, b / args.todo_dir

        old_cli(a, "index", "--quiet")
        new_cli(b, "index", "--quiet")
        for name in ("INDEX.md", "GROUPS.md"):
            pa, pb = todo_a / name, todo_b / name
            rep.same(name, pa.read_text(encoding="utf-8") if pa.exists() else None,
                     pb.read_text(encoding="utf-8") if pb.exists() else None)
        rep.same("query --json", old_cli(a, "query", "--json"), new_cli(b, "query", "--json"))
        rep.same("check", old_cli(a, "check"), new_cli(b, "check"))
        if args.legacy_config:
            ra, rb = old_cli(a, "rulesets"), new_cli(b, "rulesets")
            rb = (rb[0], rb[1].replace(".octopus/project.json",
                                       "%s/project.json" % args.todo_dir, 1))
            rep.same("rulesets", ra, rb)
            rep.same("groups", old_cli(a, "groups"), new_cli(b, "groups"))
            rep.same("groups --json", old_cli(a, "groups", "--json"),
                     new_cli(b, "groups", "--json"))

        if not args.no_briefs:
            compare_tickets(a, b, rep)
    finally:
        if args.keep:
            print("kept %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)

    for d in rep.diffs[:12]:
        print(d)
    print("%d comparisons, %d different" % (rep.checked, len(rep.diffs)))
    return 1 if rep.diffs else 0


def compare_tickets(a, b, rep):
    """Every brief, dry-run line and session id, with both engines imported."""
    old = load_module("parity_old_todo", a / "tools" / "todo.py")
    sys.path.insert(0, str(ENGINE))
    import project
    new = load_module("parity_new_todo", ENGINE / "todo.py")
    project.use(b)

    def line(argv):
        return " ".join(('"%s"' % x) if " " in x else x for x in argv)

    olds = {t.id: t for t in old.load_all()}
    news = {t.id: t for t in new.load_all()}
    rep.same("ticket ids", sorted(olds), sorted(news))
    has_brief = hasattr(old, "build_brief")
    for tid in sorted(set(olds) & set(news)):
        to, tn = olds[tid], news[tid]
        for mode in MODES:
            if has_brief:
                rep.same("brief %s %s" % (tid, mode), old.build_brief(to, mode, ""),
                         new.build_brief(tn, mode, ""))
            rep.same("dry-run %s %s" % (tid, mode), line(old._launch_argv(to, mode)),
                     line(new._launch_argv(tn, mode)))
            for run_n in (1, 2, 3):
                rep.same("session id %s %s run %d" % (tid, mode, run_n),
                         old.launch_session_id(tid, mode, run_n)
                         if old.launch_session_id.__code__.co_argcount > 2
                         else old.launch_session_id(tid, mode),
                         new.launch_session_id(tid, mode, run_n)
                         if old.launch_session_id.__code__.co_argcount > 2
                         else new.launch_session_id(tid, mode))
    if has_brief and olds:
        tid = sorted(olds)[0]
        note = "a note\nin two lines"
        rep.same("brief %s with a note" % tid, old.build_brief(olds[tid], "work", note),
                 new.build_brief(news[tid], "work", note))


if __name__ == "__main__":
    sys.exit(main())
