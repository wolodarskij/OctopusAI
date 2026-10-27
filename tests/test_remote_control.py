"""Remote Control per launch: the project's default, the ticket's override, the board's switch.
python -m unittest discover tests"""

import re
import unittest
from unittest import mock

from support import FixtureCase, project

import todo as T  # noqa: E402
import todo_server  # noqa: E402


class RemoteControl(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Remote")
        self.tid = self.new_ticket(self.root, "a ticket")

    def argv(self):
        tok = project.use(self.root)
        try:
            t = T.find(T.load_all(), self.tid)
            return T._launch_argv(t, "work"), T.session_title(t, "work")
        finally:
            project.reset(tok)

    def set(self, value):
        rc, out, err = self.todo(self.root, "remote-control", self.tid, value)
        self.assertEqual(rc, 0, err)
        return out

    def test_on_by_default(self):
        argv, title = self.argv()
        self.assertEqual(argv[argv.index("--remote-control") + 1], title)
        self.assertNotIn("--name", argv)

    def test_the_ticket_turns_it_off_and_keeps_the_name(self):
        out = self.set("off")
        self.assertIn("from its next launch", out)
        argv, title = self.argv()
        self.assertNotIn("--remote-control", argv)
        self.assertEqual(argv[argv.index("--name") + 1], title)
        text = next((self.root / "TODO").rglob("%s-*.md" % self.tid)).read_text(encoding="utf-8")
        self.assertIn("remote-control: off", text)
        rc, out, err = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out + err)

    def test_the_project_default_and_the_ticket_override(self):
        root = self.make("Quiet", config={"remoteControl": False})
        tid = self.new_ticket(root, "quiet")
        tok = project.use(root)
        try:
            t = T.find(T.load_all(), tid)
            self.assertFalse(T.remote_control(t))
            T.set_remote_control(t, "on")
            self.assertTrue(T.remote_control(T.find(T.load_all(), tid)))
            T.set_remote_control(t, "default")
            t = T.find(T.load_all(), tid)
            self.assertNotIn(T.RC_FIELD, t.meta)
            self.assertFalse(T.remote_control(t))
        finally:
            project.reset(tok)

    def test_a_bad_project_value_is_a_problem(self):
        root = self.make("Bad", config={"remoteControl": "yes"})
        self.assertTrue(any("remoteControl" in x for x in project.load(root).problems))

    def test_check_fails_a_bad_ticket_value(self):
        p = next((self.root / "TODO").rglob("%s-*.md" % self.tid))
        p.write_text(p.read_text(encoding="utf-8").replace(
            "status: pipeline", "status: pipeline\nremote-control: maybe"), encoding="utf-8")
        self.todo(self.root, "index")
        rc, out, _err = self.todo(self.root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("remote-control 'maybe'", out)

    def test_the_scan_reads_either_name(self):
        for flag in ("--remote-control", "--name"):
            m = re.search(T._NAME_ARG, 'claude x %s "Remote - T0001 - a - work" --model m' % flag)
            self.assertEqual(m.group(1), "Remote - T0001 - a - work")

    def test_a_provider_without_it_is_unavailable(self):
        tok = project.use(self.root)
        try:
            t = T.find(T.load_all(), self.tid)
            self.assertFalse(T.remote_control_available(t, "codex"))
            self.assertFalse(T.remote_control(t, "codex"))
            self.assertTrue(T.remote_control_available(t, "claude"))
        finally:
            project.reset(tok)

    def test_on_types_the_command_into_a_session_at_its_prompt(self):
        tok = project.use(self.root)
        try:
            t = T.find(T.load_all(), self.tid)
            live = [{"id": self.tid, "mode": "work", "claude": 1, "running": False,
                     "key": "%s/work" % self.tid},
                    {"id": self.tid, "mode": "plan", "claude": 2, "running": True,
                     "key": "%s/plan" % self.tid}]
            with mock.patch.object(T, "send_session", return_value=1) as send:
                typed = T.apply_remote_control(t, live)
            self.assertEqual(typed, ["%s/work" % self.tid])
            send.assert_called_once_with(live[0], T.RC_COMMAND, ["Enter"])
            T.set_remote_control(t, "off")
            with mock.patch.object(T, "send_session") as send:
                self.assertEqual(T.apply_remote_control(T.find(T.load_all(), self.tid), live),
                                 [])
            send.assert_not_called()
        finally:
            project.reset(tok)

    def test_the_board_action_and_snapshot(self):
        tok = project.use(self.root)
        try:
            with mock.patch.object(T, "live_sessions", lambda tickets=None: []):
                r = todo_server.act_remoteControl({"ids": [self.tid], "value": "off"})
                self.assertEqual(r["typed"], [])
                snap = todo_server.snapshot()
            row = next(x for x in snap["tickets"] if x["id"] == self.tid)
            self.assertEqual((row["remoteControl"], row["remoteControlSet"],
                              row["remoteControlAvailable"]), (False, "off", True))
            self.assertTrue(snap["project"]["remoteControl"])
            with self.assertRaises(ValueError):
                todo_server.act_remoteControl({"ids": [self.tid], "value": "maybe"})
        finally:
            project.reset(tok)


if __name__ == "__main__":
    unittest.main()
