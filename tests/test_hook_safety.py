"""No hook stops a session.   python -m unittest tests.test_hook_safety

Every hook line that Octopus AI writes or remakes exits 0, whatever its command did: a missing
script, a wrong directory, a crash or any exit code. A script path relative to the project root
is written from `$CLAUDE_PROJECT_DIR`, so a session that changed its directory still finds it.
"""

import json
import os
import shutil
import subprocess
import unittest

import support
from support import admin, quiet
import agents


def commands(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    return {ev: [h["command"] for g in groups for h in g["hooks"]]
            for ev, groups in d["hooks"].items()}


class SecureHooks(support.FixtureCase):

    def setUp(self):
        super().setUp()
        self.root = self.make("Sample Game", register=False)
        (self.root / "tools").mkdir()
        (self.root / "tools" / "state.py").write_text(
            "import sys\nprint('ran')\nsys.exit(2)\n", encoding="utf-8")
        self.settings = self.root / ".claude" / "settings.json"

    def own_hooks(self, hooks):
        d = json.loads(self.settings.read_text(encoding="utf-8"))
        for ev, cmd in hooks.items():
            d.setdefault("hooks", {}).setdefault(ev, []).append(
                {"hooks": [{"type": "command", "command": cmd}]})
        self.settings.write_text(json.dumps(d), encoding="utf-8")

    def test_engine_lines_end_with_the_tail(self):
        for line in commands(self.root / ".claude" / "settings.local.json").values():
            self.assertEqual(line, [admin.hook_command()])
        self.assertTrue(admin.hook_command().endswith(agents.HOOK_TAIL))
        self.assertTrue(admin.is_hook_command('python "X:/Tools/OctopusAI/octopus.py" hook'))
        self.assertTrue(admin.is_hook_command(admin.hook_command()))

    def test_own_lines_are_remade_once(self):
        self.own_hooks({"UserPromptSubmit": "python tools/state.py",
                        "PreToolUse": "python './tools/state.py' --check || exit 2",
                        "SessionStart": "python tools/state.py; exit 0"})
        self.assertEqual(admin.secure_hooks(self.root, out=quiet), [".claude/settings.json"])
        got = commands(self.settings)
        self.assertEqual(got["UserPromptSubmit"][-1],
                         'python "$CLAUDE_PROJECT_DIR/tools/state.py"; exit 0')
        self.assertEqual(got["PreToolUse"][-1],
                         '(python "$CLAUDE_PROJECT_DIR/tools/state.py" --check || exit 2); exit 0')
        self.assertEqual(got["SessionStart"][-1],
                         'python "$CLAUDE_PROJECT_DIR/tools/state.py"; exit 0')
        self.assertEqual(admin.secure_hooks(self.root, out=quiet), [])

    def test_update_remakes_the_lines(self):
        self.own_hooks({"UserPromptSubmit": "python tools/state.py"})
        admin.update(self.root, out=quiet)
        self.assertEqual(commands(self.settings)["UserPromptSubmit"][-1],
                         'python "$CLAUDE_PROJECT_DIR/tools/state.py"; exit 0')
        self.assertEqual(admin.status(self.root, out=quiet)["edited"], 0)

    def test_a_remade_line_exits_0_from_another_directory(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("no bash")
        self.own_hooks({"UserPromptSubmit": "python tools/state.py"})
        admin.secure_hooks(self.root, out=quiet)
        away = self.root / "scratch"
        away.mkdir()
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.root.as_posix())
        for line in (commands(self.settings)["UserPromptSubmit"][-1],
                     'python "%s/missing.py" hook%s' % (self.tmp.as_posix(), agents.HOOK_TAIL)):
            with self.subTest(line=line):
                r = subprocess.run([bash, "-c", line], cwd=away, env=env, input="{}",
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)


class NeverBlocks(unittest.TestCase):

    def test_tail(self):
        self.assertEqual(agents.never_blocks("a"), "a; exit 0")
        self.assertEqual(agents.never_blocks("a;"), "a; exit 0")
        self.assertEqual(agents.never_blocks("a &"), "a & exit 0")
        self.assertEqual(agents.never_blocks("a; exit 0"), "a; exit 0")
        self.assertEqual(agents.never_blocks("a && exit 0"), "a && exit 0; exit 0")

    def test_status_hook_line(self):
        line = agents.status_hook_line("python \"X:/Tools/OctopusAI/octopus.py\" status-hook",
                                       "codex")
        self.assertTrue(line.endswith(agents.HOOK_TAIL))
        self.assertTrue(agents.is_status_hook(line, "codex"))


if __name__ == "__main__":
    unittest.main()
