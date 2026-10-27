"""The check that keeps names that must not appear out of the open tickets and the files git tracks.
python -m unittest discover tests"""

import json
import subprocess
import unittest

from support import FixtureCase

import oldnames


class Hits(unittest.TestCase):
    def test_every_old_spelling_is_a_hit(self):
        text = ("the AITools board\nAI Tools\npython aitools.py hook\n"
                "AITOOLS_VERIFY\nnothing here\n")
        self.assertEqual(oldnames.hits(text),
                         [(1, "AITools"), (2, "AI Tools"), (3, "aitools"), (4, "AITOOLS")])

    def test_another_name_that_starts_with_it_is_no_hit(self):
        self.assertEqual(oldnames.hits("X:/Tools/AIToolsKit\naitools-todo\n"), [])

    def test_an_alias_is_no_hit_where_it_stands(self):
        text = '"Bash(python .aitools/todo.py close:*)"\npython .aitools/todo.py list\n'
        self.assertEqual(oldnames.hits(text, [".aitools/todo.py close"]), [(2, "aitools")])

    def test_allowed_globs(self):
        globs = ["CLAIMS.md", ".claude/coordination/**", "TODO/*/T0090-*.md"]
        self.assertTrue(oldnames.allowed("CLAIMS.md", globs))
        self.assertTrue(oldnames.allowed("sub/CLAIMS.md", globs))
        self.assertTrue(oldnames.allowed(".claude/coordination/archive/a.md", globs))
        self.assertTrue(oldnames.allowed("TODO/Pipeline/T0090-x.md", globs))
        self.assertFalse(oldnames.allowed("TODO/Pipeline/T0091-x.md", globs))
        self.assertFalse(oldnames.allowed("docs/a.md", globs))


class Check(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Names", "none")
        self.git("init", "-q")

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root)] + list(args), check=True,
                       capture_output=True)

    def track(self):
        self.git("add", "-A", "-f")

    def test_an_untracked_ticket_is_no_hit(self):
        self.new_ticket(self.root, "Bug: the AITools board drops a ticket")
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out)

    def test_an_open_ticket_fails_check_and_a_closed_one_is_history(self):
        tid = self.new_ticket(self.root, "Bug: the AITools board drops a ticket")
        self.track()
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 1, out)
        self.assertIn("names the toolkit AITools", out)
        self.assertIn(tid, out)
        rc, out, err = self.todo(self.root, "move", tid, "Done", "--verified")
        self.assertEqual(rc, 0, err)
        self.track()
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out)

    def test_the_slug_of_a_ticket_file_is_checked(self):
        tid = self.new_ticket(self.root, "Bug: the board drops a ticket")
        old = next((self.root / "TODO").rglob("%s-*.md" % tid))
        old.rename(old.with_name("%s-the-aitools-board.md" % tid))
        self.track()
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 1, out)
        self.assertIn("file name", out)

    def test_status_reads_the_project_files(self):
        (self.root / "tools").mkdir()
        (self.root / "tools" / "run.bat").write_text(
            'python "X:/Tools/AITools/octopus.py" board\n', encoding="utf-8")
        claims = self.root / ".claude" / "coordination" / "CLAIMS.md"
        claims.parent.mkdir(parents=True, exist_ok=True)
        claims.write_text("- .aitools/unreal.json\n", encoding="utf-8")
        (self.root / "local.txt").write_text("AITools\n", encoding="utf-8")
        self.track()
        self.git("rm", "-q", "--cached", "local.txt")
        rc, out, _ = self.cli("status", self.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("tools/run.bat names the toolkit AITools (line 1)", out)
        self.assertNotIn("CLAIMS.md", out)
        self.assertNotIn("local.txt", out)
        # A project's own ruleset allows a file that keeps the name on purpose.
        rs = self.root / ".octopus" / "rulesets" / "names.json"
        rs.parent.mkdir(parents=True, exist_ok=True)
        rs.write_text(json.dumps({"title": "aliases", "oldNameAllowed": ["tools/run.bat"]}),
                      encoding="utf-8")
        cfg = self.root / ".octopus" / "project.json"
        d = json.loads(cfg.read_text(encoding="utf-8"))
        d["rulesets"].append("names")
        cfg.write_text(json.dumps(d), encoding="utf-8")
        self.assertEqual(oldnames.scan_project(self.root), [])


if __name__ == "__main__":
    unittest.main()
