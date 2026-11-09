"""The files that name a directory of this machine stay out of every VCS.
python -m unittest tests.test_machine_files"""

import json
import shutil
import subprocess
import unittest

import support
from support import admin, quiet

import vcs


def git(root, *args):
    return subprocess.run(["git"] + list(args), cwd=str(root), capture_output=True, text=True)


class MachineFiles(support.FixtureCase):

    def test_the_list_holds_the_shim_the_record_the_hook_files_and_filled_payload(self):
        root = self.make("Alpha")
        files = admin.machine_files(root)
        for rel in (".octopus/todo.py", ".octopus/installed.json", ".claude/settings.local.json",
                    ".codex/hooks.json", ".agents/rules/octopus/core/todo-tracking.md"):
            self.assertIn(rel, files)
        self.assertNotIn(".claude/settings.json", files)
        self.assertNotIn(".octopus/project.json", files)
        shared = (root / ".claude" / "settings.json").read_text(encoding="utf-8")
        self.assertIsNone(admin._path_pattern(admin.OCTOPUS).search(shared))

    def test_an_older_install_moves_its_hook_lines_to_the_local_file(self):
        root = self.make("Old")
        sp = root / ".claude" / "settings.json"
        d = json.loads(sp.read_text(encoding="utf-8"))
        d["hooks"] = {"Stop": [{"hooks": [{"type": "command", "command": admin.hook_command()}]}]}
        sp.write_text(json.dumps(d), encoding="utf-8")
        admin.update(root, out=quiet)
        self.assertNotIn("hooks", json.loads(sp.read_text(encoding="utf-8")))
        local = json.loads((root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
        self.assertIn("Stop", local["hooks"])


@unittest.skipUnless(shutil.which("git"), "git is not on the PATH")
class Git(support.FixtureCase):

    def test_install_into_a_repository_leaves_every_machine_file_untracked(self):
        root = self.tmp / "Repo"
        root.mkdir()
        self.assertEqual(git(root, "init", "-q").returncode, 0)
        admin.install(root, ["git"], "Repo", out=quiet)
        listed = git(root, "status", "--porcelain", "--untracked-files=all").stdout
        shown = {ln[3:].strip() for ln in listed.splitlines()}
        for rel in admin.machine_files(root):
            self.assertNotIn(rel, shown)
        self.assertIn(".claude/settings.json", shown)
        rec = admin.Record(root)
        self.assertEqual(rec.ignored["kind"], "git")

    def test_refresh_adds_the_list_when_a_repository_comes_later(self):
        root = self.make("Later")
        self.assertFalse((root / ".gitignore").exists())
        self.assertEqual(git(root, "init", "-q").returncode, 0)
        admin.refresh(root, out=quiet)
        lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/.octopus/todo.py", lines)
        before = (root / ".gitignore").read_bytes()
        admin.refresh(root, out=quiet)
        self.assertEqual((root / ".gitignore").read_bytes(), before)

    def test_a_path_git_ignores_already_gets_no_line(self):
        root = self.tmp / "Covered"
        root.mkdir()
        git(root, "init", "-q")
        (root / ".gitignore").write_text("/.octopus/\n", encoding="utf-8")
        steps = vcs.ignore(root, "git", [".octopus/todo.py", ".codex/hooks.json"])
        self.assertTrue(all(s.rc == 0 for s in steps))
        lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertNotIn("/.octopus/todo.py", lines)
        self.assertIn("/.codex/hooks.json", lines)


class IgnoreFiles(support.FixtureCase):

    def test_each_file_based_kind_writes_its_own_file(self):
        cases = {"diversion": (".dvignore", "/.octopus/todo.py"),
                 "plastic": ("ignore.conf", "/.octopus/todo.py"),
                 "mercurial": (".hgignore", r"re:^\.octopus/todo\.py$")}
        for kind, (name, line) in cases.items():
            root = self.tmp / kind
            root.mkdir()
            steps = vcs.ignore(root, kind, [".octopus/todo.py"])
            self.assertEqual([s.rc for s in steps], [0], kind)
            self.assertIn(line, (root / name).read_text(encoding="utf-8").splitlines(), kind)

    def test_no_vcs_writes_nothing(self):
        self.assertEqual(vcs.ignore(self.tmp, "none", [".octopus/todo.py"]), [])
        self.assertEqual(vcs.ignore(self.tmp, None, [".octopus/todo.py"]), [])


if __name__ == "__main__":
    unittest.main()
