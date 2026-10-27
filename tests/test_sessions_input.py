"""`sessions --input`: only the sessions that wait for the owner's input.
python -m unittest discover tests"""

import argparse
import contextlib
import io
import json
import unittest
from unittest import mock

import support  # noqa: E402  (puts core/engine on the path)
import todo  # noqa: E402


def _sess(tid, running=False, wait="finished"):
    return {"key": "%s/work" % tid, "id": tid, "mode": "work", "session": tid + "-sid",
            "running": running, "wait": wait, "shell": 900, "claude": 901, "tabbed": False,
            "title": tid, "signals": [("wait", wait)]}


LIVE = [_sess("T0001", running=True), _sess("T0002", wait="input"),
        _sess("T0003", wait="event"), _sess("T0004", wait="input"), _sess("T0005")]


def _run(*argv):
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--why", action="store_true")
    p.add_argument("--input", action="store_true")
    out = io.StringIO()
    with mock.patch.object(todo, "live_sessions", lambda recheck=False: list(LIVE)), \
            contextlib.redirect_stdout(out):
        rc = todo.cmd_sessions(p.parse_args(list(argv)))
    return rc, out.getvalue()


class SessionsInput(unittest.TestCase):
    def test_json_keeps_only_input(self):
        rc, out = _run("--json", "--input")
        self.assertEqual(rc, 0)
        self.assertEqual([s["id"] for s in json.loads(out)], ["T0002", "T0004"])

    def test_table_keeps_only_input(self):
        rc, out = _run("--input")
        self.assertEqual(rc, 0)
        for tid in ("T0002", "T0004"):
            self.assertIn(tid, out)
        for tid in ("T0001", "T0003", "T0005"):
            self.assertNotIn(tid, out)
        self.assertIn("2 open", out)

    def test_why_keeps_only_input(self):
        _, out = _run("--why", "--input")
        self.assertIn("T0002/work", out)
        self.assertNotIn("T0003/work", out)

    def test_without_flag_lists_all(self):
        _, out = _run("--json")
        self.assertEqual(len(json.loads(out)), 5)

    def test_none_waits(self):
        with mock.patch.object(todo, "live_sessions",
                               lambda recheck=False: [_sess("T0001", running=True)]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                todo.cmd_sessions(argparse.Namespace(json=False, why=False, input=True))
        self.assertIn("No launched session waits for input.", out.getvalue())


class CliFlag(support.FixtureCase):
    def test_cli_accepts_flag(self):
        # A project of its own: the checkout is a project only where Octopus AI was
        # installed into it.
        root = self.make("Alpha", "none")
        out = io.StringIO()
        with mock.patch.object(todo, "live_sessions", lambda recheck=False: list(LIVE)), \
                contextlib.redirect_stdout(out):
            rc = todo.main(["--project", str(root), "sessions", "--input", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual([s["id"] for s in json.loads(out.getvalue())], ["T0002", "T0004"])


if __name__ == "__main__":
    unittest.main()
