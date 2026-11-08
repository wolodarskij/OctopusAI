"""What the toolkits before Octopus AI left in a project, and how update removes it.
python -m unittest discover tests"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - puts the engine on sys.path

import leftovers

ENGINE = {
    "todo.py": '"""todo.py - the TODO/ ticket tree: one file per ticket."""\nimport session_state\n',
    "todo_server.py": '"""todo_server.py - the TODO board, served."""\nimport todo\n',
    "session_state.py": '"""session_state.py - which launched session waits at its prompt."""\n',
    "winproc.py": '"""winproc.py - the Windows process and window calls the TODO board needs."""\n',
    "todo_board.html": "<!doctype html>\n",
    "test_todo_edges.py": '"""Edges."""\nimport todo\n',
}


class Leftovers(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.write(".octopus/project.json", json.dumps({"name": "Sample Game", "slug": "sample",
                                                         "rulesets": ["core"]}))
        self.write(".octopus/installed.json", json.dumps({"files": {
            ".agents/rules/octopus/core/todo-tracking.md": {"hash": "x", "by": "core"},
            ".agents/skills/todo/SKILL.md": {"hash": "x", "by": "core"}}}))
        for name, text in ENGINE.items():
            self.write("tools/" + name, text)
        self.write("tools/own.py", '"""A tool of the project."""\n')

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="")

    def read(self, rel):
        with open(self.root / rel, encoding="utf-8", newline="") as fh:
            return fh.read()

    def by_path(self):
        return {i["path"]: i for i in leftovers.find(self.root)}

    def test_the_old_engine_goes_and_an_own_tool_stays(self):
        items = self.by_path()
        for name in ENGINE:
            self.assertEqual(items["tools/" + name]["action"], "remove", name)
        self.assertNotIn("tools/own.py", items)
        leftovers.clean(self.root, out=lambda *_a: None)
        self.assertEqual(sorted(p.name for p in (self.root / "tools").iterdir()), ["own.py"])

    def test_a_script_by_the_same_name_with_other_text_stays(self):
        self.write("tools/todo.py", '"""Our own todo list."""\n')
        self.assertNotIn("tools/todo.py", self.by_path())

    def test_an_old_script_a_staying_file_imports_stays(self):
        self.write("tools/own.py", "def run():\n    import todo\n")
        items = self.by_path()
        self.assertEqual(items["tools/todo.py"]["action"], "keep")
        self.assertIn("tools/own.py imports it", items["tools/todo.py"]["why"])
        self.assertEqual(items["tools/session_state.py"]["action"], "keep")

    def test_old_hook_lines_go_and_other_hooks_stay(self):
        self.write(".claude/settings.json", json.dumps({"hooks": {
            "Stop": [{"hooks": [
                {"type": "command", "command": 'python "$CLAUDE_PROJECT_DIR/tools/session_state.py"; exit 0'},
                {"type": "command", "command": "npm run lint"}]}],
            "UserPromptSubmit": [{"hooks": [
                {"type": "command", "command": "python tools/session_state.py"}]}]},
            "env": {"A": "1"}}))
        leftovers.clean(self.root, out=lambda *_a: None)
        d = json.loads(self.read(".claude/settings.json"))
        self.assertEqual(d["hooks"], {"Stop": [{"hooks": [
            {"type": "command", "command": "npm run lint"}]}]})
        self.assertEqual(d["env"], {"A": "1"})

    def test_a_hook_line_of_a_missing_old_script_goes(self):
        (self.root / "tools" / "session_state.py").unlink()
        self.write(".claude/settings.local.json", json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "python tools/session_state.py"}]}]}}))
        self.assertEqual(leftovers.clean_hooks(self.root, out=lambda *_a: None),
                         [".claude/settings.local.json"])
        self.assertNotIn("hooks", json.loads(self.read(".claude/settings.local.json")))

    def test_an_own_old_ruleset_moves_into_the_config(self):
        self.write("TODO/project.json", json.dumps({"rulesets": ["core", "sample"]}))
        self.write("TODO/rulesets/core.json", json.dumps({"title": "old core"}))
        self.write("TODO/rulesets/sample.json", json.dumps({"title": "Sample", "checks": ["make"]}))
        leftovers.clean(self.root, out=lambda *_a: None)
        self.assertFalse((self.root / "TODO" / "project.json").exists())
        self.assertFalse((self.root / "TODO" / "rulesets").exists())
        self.assertEqual(json.loads(self.read(".octopus/rulesets/sample.json"))["checks"], ["make"])
        self.assertFalse((self.root / ".octopus" / "rulesets" / "core.json").exists())
        self.assertEqual(json.loads(self.read(".octopus/project.json"))["rulesets"],
                         ["core", "sample"])

    def test_an_old_ruleset_with_an_unknown_key_keeps_the_old_config(self):
        self.write("TODO/project.json", json.dumps({"rulesets": ["sample"]}))
        self.write("TODO/rulesets/sample.json", json.dumps({"title": "Sample", "odd": 1}))
        items = self.by_path()
        self.assertEqual(items["TODO/rulesets/sample.json"]["action"], "keep")
        self.assertNotIn("TODO/project.json", items)

    def test_the_old_install_its_payload_and_its_ignore_lines_go(self):
        self.write(".aitools/installed.json", json.dumps({"files": {
            ".claude/rules/aitools/core/todo-tracking.md": {"hash": "y", "by": "core"}}}))
        self.write(".aitools/SESSIONS.md", "| session-id | x |\n|---|---|\n| s1 | a |\n")
        self.write(".octopus/SESSIONS.md", "| session-id | x |\n|---|---|\n")
        self.write(".claude/rules/aitools/core/todo-tracking.md", "old")
        self.write(".gitignore", "node_modules/\n\n# Old AITools install\n/.aitools/\n\nrecordings/\n")
        leftovers.clean(self.root, out=lambda *_a: None)
        self.assertFalse((self.root / ".aitools").exists())
        self.assertFalse((self.root / ".claude" / "rules").exists())
        self.assertIn("| s1 | a |", self.read(".octopus/SESSIONS.md"))
        self.assertEqual(self.read(".gitignore"), "node_modules/\n\nrecordings/\n")

    def test_an_old_claude_copy_of_a_payload_skill_and_rule_goes(self):
        self.write(".claude/skills/todo/SKILL.md", "python tools/todo.py list\n")
        self.write(".claude/skills/mine/SKILL.md", "python tools/todo.py list\n")
        self.write(".claude/rules/todo-tracking.md", "python tools/todo.py move\n")
        leftovers.clean(self.root, out=lambda *_a: None)
        self.assertFalse((self.root / ".claude" / "skills" / "todo").exists())
        self.assertFalse((self.root / ".claude" / "rules" / "todo-tracking.md").exists())
        self.assertTrue((self.root / ".claude" / "skills" / "mine").exists())

    def test_old_commands_in_text_become_the_octopus_commands(self):
        self.write("run-board.bat", "python tools\\todo_server.py --page assets\r\n")
        self.write("docs/notes.md", "Run `python tools/todo.py check`; policy: "
                                    "`.claude/rules/todo-tracking.md`.\n")
        self.write("README.md", "python tools/todo.py board\n")
        self.write("TODO/Done/T0001-x.md", "python tools/todo.py check\n")
        items = self.by_path()
        self.assertEqual(items["README.md"]["action"], "readme")
        leftovers.clean(self.root, out=lambda *_a: None)
        self.assertEqual(self.read("run-board.bat"),
                         "python .octopus\\todo.py serve --page assets\r\n")
        self.assertEqual(self.read("docs/notes.md"),
                         "Run `python .octopus/todo.py check`; policy: "
                         "`.agents/rules/octopus/core/todo-tracking.md`.\n")
        self.assertEqual(self.read("README.md"), "python tools/todo.py board\n")
        self.assertEqual(self.read("TODO/Done/T0001-x.md"), "python tools/todo.py check\n")

    def test_the_checkout_has_no_leftovers(self):
        self.assertEqual(leftovers.find(leftovers._project.OCTOPUS), [])


if __name__ == "__main__":
    unittest.main()
