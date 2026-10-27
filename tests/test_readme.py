"""The README's commands run as written.   python -m unittest discover tests

The quick start runs end to end in a temporary directory, with `work` as a dry run. Every
`octopus.py` and `todo.py` command the README names exists, and every repository file it names
is there.
"""

import os
import re
import shlex
import subprocess
import sys
import unittest

from support import OCTOPUS, FixtureCase

README = OCTOPUS / "README.md"
SAMPLE = "X:/Projects/MyGame"


def section(text, heading):
    """The body of the `## heading` section, up to the next `## `."""
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(heading), text, re.S | re.M)
    if not m:
        raise AssertionError("README has no section %r" % heading)
    return m.group(1)


def commands(body):
    """The command of each indented code line, without the comment column after it."""
    out = []
    for line in body.splitlines():
        if line.startswith("    ") and line.strip():
            out.append(re.split(r"\s{2,}", line.strip())[0])
    return out


def run(argv, cwd):
    p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=os.environ.copy())
    return p.returncode, p.stdout + p.stderr


class QuickStart(FixtureCase):
    def test_quick_start_runs_as_written(self):
        steps = commands(section(README.read_text(encoding="utf-8"), "Install and quick start"))
        self.assertTrue(steps)
        game = self.tmp / "MyGame"
        game.mkdir()
        cwd = OCTOPUS
        for step in steps:
            argv = shlex.split(step.replace(SAMPLE, game.as_posix()))
            if argv[0] == "git":
                continue
            if argv[0] == "cd":
                cwd = OCTOPUS if argv[1] == "OctopusAI" else game
                continue
            if argv[0].endswith(".bat"):
                self.assertTrue((cwd / argv[0]).is_file(), step)
                continue
            self.assertEqual(argv[0], "python", step)
            argv[0] = sys.executable
            if argv[1:3] == [".octopus/todo.py", "work"]:
                argv.append("--dry-run")
            rc, out = run(argv, cwd)
            self.assertEqual(rc, 0, "%s\n%s" % (step, out))
        self.assertTrue((game / ".octopus" / "project.json").is_file())
        self.assertTrue(list((game / "TODO").rglob("T0001-*.md")))


class Reference(FixtureCase):
    def setUp(self):
        super().setUp()
        self.text = README.read_text(encoding="utf-8")

    def test_every_named_command_exists(self):
        seen = set()
        for line in commands(self.text):
            argv = shlex.split(line)
            if argv[0] != "python" or len(argv) < 3 or argv[2].startswith("-"):
                continue
            script = argv[1]
            if script not in ("octopus.py", ".octopus/todo.py"):
                continue
            seen.add((script, argv[2]))
        self.assertTrue(seen)
        # A project of its own for the ticket commands: the checkout is a project only where
        # Octopus AI was installed into it.
        root = self.make("MyGame")
        for script, cmd in sorted(seen):
            path = OCTOPUS / "octopus.py"
            pre = [] if script == "octopus.py" else ["todo", "--project", str(root)]
            rc, out = run([sys.executable, str(path)] + pre + [cmd, "--help"], OCTOPUS)
            self.assertEqual(rc, 0, "%s %s\n%s" % (script, cmd, out))

    def test_named_repository_files_exist(self):
        named = {"AgentBoard.bat": "AgentBoard.bat",
                 "AgentBoard.command": "AgentBoard.command",
                 "tools/setup-mac.sh": "tools/setup-mac.sh",
                 "/octopusmanager": "core/skills/octopusmanager/SKILL.md"}
        for word, path in named.items():
            self.assertIn(word, self.text)
            self.assertTrue((OCTOPUS / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
