"""Fixture projects for the Octopus AI tests.

Every test runs in a temporary directory. `OCTOPUS_HOME` points at a temporary registry, so
no test reads or writes the registry of the machine it runs on.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

OCTOPUS = Path(__file__).resolve().parents[1]
for _d in ("core/engine", "core/win", "core/hooks", "core/posix"):
    _p = str(OCTOPUS / _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import admin  # noqa: E402
import project  # noqa: E402


def quiet(*_a, **_k):
    return None


def confirmed(home, provider="claude", model="opus"):
    """Write `default-provider.json` in `home`: the default `provider` and `model`, checked."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "default-provider.json").write_text(json.dumps(
        {"provider": provider, "model": model, "at": "2026-01-01 00:00", "ok": True}),
        encoding="utf-8")


class FixtureCase(unittest.TestCase):
    """A temporary directory, a temporary registry, and helpers to build projects in it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="octopus-test-"))
        self._env = {k: os.environ.get(k) for k in ("OCTOPUS_HOME", "CLAUDECODE",
                                                     "OCTOPUS_VERIFY", "CLAUDE_PROJECT_DIR")}
        os.environ["OCTOPUS_HOME"] = str(self.tmp / "home")
        for k in ("CLAUDECODE", "OCTOPUS_VERIFY", "CLAUDE_PROJECT_DIR"):
            os.environ.pop(k, None)
        # A machine whose default AI provider and model are confirmed, so a launch runs.
        confirmed(project.home())

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, name, packs=(), slug=None, register=True, config=None):
        """A project directory with Octopus AI and `packs` installed. `config` updates
        project.json."""
        root = self.tmp / name
        root.mkdir(parents=True, exist_ok=True)
        admin.install(root, list(packs), name, slug, register=register, out=quiet, manager=False)
        if config:
            p = root / ".octopus" / "project.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d.update(config)
            p.write_text(json.dumps(d, indent=2), encoding="utf-8")
        return root

    def cli(self, *argv, env=None, stdin=None):
        """Run `octopus.py` as a subprocess. Returns (exit code, stdout, stderr)."""
        e = dict(os.environ, PYTHONIOENCODING="utf-8")
        e.update(env or {})
        r = subprocess.run([sys.executable, str(OCTOPUS / "octopus.py")] + [str(a) for a in argv],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=e, input=stdin, cwd=str(self.tmp))
        return r.returncode, r.stdout, r.stderr

    def todo(self, root, *argv, env=None):
        return self.cli("todo", "--project", root, *argv, env=env)

    def new_ticket(self, root, title, *extra):
        rc, out, err = self.todo(root, "new", "--title", title, "--severity", "Medium", *extra)
        self.assertEqual(rc, 0, err)
        return out.split()[0]


def tree_hash(root):
    """One hash over every file under `root`: its relative path and its bytes."""
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode("utf-8"))
            h.update(p.read_bytes())
        elif p.is_dir() and not any(p.iterdir()):
            h.update(("dir:" + p.relative_to(root).as_posix()).encode("utf-8"))
    return h.hexdigest()
