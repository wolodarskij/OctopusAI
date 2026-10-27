"""The checkout moves: every registered project follows it.   python -m unittest tests.test_relocate

A project names the checkout by its absolute path in the hook line, in every installed file the
`{{OCTOPUS}}` path was filled into, and in its config. `relocate` rewrites them all.
"""

import json
import os
import unittest
from pathlib import Path

import support
from support import admin, project, quiet


def hook_lines(root):
    d = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    return [h["command"] for groups in d["hooks"].values() for g in groups for h in g["hooks"]]


def names(root, path):
    """The project files that name `path` in any spelling."""
    pat = admin._path_pattern(path)
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and "TODO" not in p.relative_to(root).parts:
            try:
                if pat.search(p.read_text(encoding="utf-8")):
                    out.append(p.relative_to(root).as_posix())
            except (UnicodeDecodeError, OSError):
                pass
    return out


class SwapPath(unittest.TestCase):

    def test_every_spelling_keeps_its_own(self):
        old, new = "X:/Tools/AITools", "X:/Tools/OctopusAI"
        text = ('python "X:/Tools/AITools/octopus.py" hook\n'
                'X:\\Tools\\AITools\\AgentBoard.bat\n'
                '"cli": "X:\\\\Tools\\\\AITools\\\\octopus.py"\n'
                'x:/tools/aitools.\n')
        self.assertEqual(admin.swap_path(text, old, new),
                         'python "X:/Tools/OctopusAI/octopus.py" hook\n'
                         'X:\\Tools\\OctopusAI\\AgentBoard.bat\n'
                         '"cli": "X:\\\\Tools\\\\OctopusAI\\\\octopus.py"\n'
                         'X:/Tools/OctopusAI.\n')

    def test_a_longer_name_stays(self):
        old, new = "X:/Tools/AITools", "X:/Tools/OctopusAI"
        for text in ("X:/Tools/AIToolsOld/x", "X:/Tools/AITools-test/x",
                     "XX:/Tools/AITools/x"):
            self.assertEqual(admin.swap_path(text, old, new), text)

    def test_hook_line_of_any_checkout(self):
        self.assertTrue(admin.is_hook_command('python "E:/Elsewhere/OctopusAI/octopus.py" hook'))
        self.assertTrue(admin.is_hook_command('python "X:/Tools/AITools/aitools.py" hook'))
        self.assertFalse(admin.is_hook_command('python "D:/x/octopus.py" board'))
        self.assertFalse(admin.is_hook_command(None))


class RelocateProject(support.FixtureCase):

    def setUp(self):
        super().setUp()
        self.root = self.make("Alpha", "unreal")
        self.away = self.tmp / "Moved" / "OctopusAI"

    def test_every_path_follows_and_the_record_stays_in_step(self):
        (self.root / "CLAUDE.md").write_text("run %s/octopus.py todo\n" % admin.OCTOPUS.as_posix(),
                                             encoding="utf-8")
        changed = admin.relocate_project(self.root, admin.OCTOPUS, self.away, out=quiet)
        self.assertIn(".claude/settings.json", changed)
        self.assertIn(".octopus/todo.py", changed)
        self.assertIn("CLAUDE.md", changed)
        self.assertEqual(names(self.root, admin.OCTOPUS), [])
        self.assertTrue(all(c.startswith('python "%s/' % self.away.as_posix())
                            for c in hook_lines(self.root)))
        counts = admin.status(self.root, out=quiet)
        self.assertEqual((counts["edited"], counts["missing"]), (0, 0))

    def test_install_after_a_move_rewrites_the_old_hook_line(self):
        admin.relocate_project(self.root, admin.OCTOPUS, self.away, out=quiet)
        before = len(hook_lines(self.root))
        admin.install(self.root, register=False, out=quiet)
        lines = hook_lines(self.root)
        self.assertEqual(len(lines), before)
        self.assertEqual(set(lines), {admin.hook_command()})

    def test_uninstall_after_a_move_takes_the_moved_line(self):
        admin.relocate_project(self.root, admin.OCTOPUS, self.away, out=quiet)
        sp = self.root / ".claude" / "settings.json"
        d = json.loads(sp.read_text(encoding="utf-8"))
        d["model"] = "x"                     # an edit, so uninstall cannot restore the bytes
        sp.write_text(json.dumps(d), encoding="utf-8")
        admin.uninstall(self.root, out=quiet)
        if sp.exists():
            self.assertNotIn("hooks", json.loads(sp.read_text(encoding="utf-8")))


class Relocate(support.FixtureCase):
    """The whole move, of a stand-in checkout in the temporary directory."""

    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())
        # Resolved, as the checkout's own path is: macOS's temporary directory lies under the
        # link /var -> /private/var, and relocate rewrites the resolved spelling.
        self.old = self.tmp.resolve() / "Tools" / "AITools"
        self.new = self.tmp.resolve() / "Tools" / "OctopusAI"
        (self.old / "TODO" / ".sessions").mkdir(parents=True)
        (self.old / "octopus.py").write_text("", encoding="utf-8")
        self.root = self.make("Alpha", "none")
        admin.relocate_project(self.root, admin.OCTOPUS, self.old, out=quiet)
        rows = project.load_registry() + [{"name": "Checkout", "path": str(self.old)}]
        project.save_registry(rows)
        claude = self.tmp / "claude"
        (claude / "projects" / admin.claude_key(self.old)).mkdir(parents=True)
        (claude / "projects" / admin.claude_key(self.old) / "s.jsonl").write_text("{}\n")
        (claude / ".claude.json").write_text(json.dumps(
            {"projects": {self.old.as_posix(): {"hasTrustDialogAccepted": True}}}))
        (self.old / "TODO" / ".sessions" / "s.live.json").write_text(json.dumps(
            {"transcript": str(claude / "projects" / admin.claude_key(self.old) / "s.jsonl")}))
        self._claude = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(claude)
        self.claude = claude

    def tearDown(self):
        if self._claude is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._claude
        if self.old.is_junction() if hasattr(self.old, "is_junction") else False:
            os.rmdir(self.old)
        super().tearDown()

    def test_the_move(self):
        admin.relocate(self.new, old=self.old, out=quiet)
        self.assertTrue((self.new / "octopus.py").is_file())
        self.assertTrue((self.old / "octopus.py").is_file(), "the junction at the old path")
        self.assertEqual(names(self.root, self.old), [])
        rows = {r["name"]: r["path"] for r in project.load_registry()}
        self.assertEqual(rows["Checkout"], str(self.new))
        key = admin.claude_key(self.new)
        self.assertTrue((self.claude / "projects" / key / "s.jsonl").is_file())
        cfg = json.loads((self.claude / ".claude.json").read_text(encoding="utf-8"))
        self.assertTrue(cfg["projects"][self.new.as_posix()]["hasTrustDialogAccepted"])
        live = (self.new / "TODO" / ".sessions" / "s.live.json").read_text(encoding="utf-8")
        self.assertIn(key, live)

    def test_refuses_a_target_that_exists(self):
        self.new.mkdir()
        with self.assertRaises(ValueError):
            admin.relocate(self.new, old=self.old, out=quiet)


if __name__ == "__main__":
    unittest.main()
