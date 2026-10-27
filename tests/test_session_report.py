"""The manager's update (`octopus sessions`) and `manager --claim`: the four sections, the
snapshot of the last call, and a session the owner opened that becomes the manager.
python -m unittest discover tests"""

import json
import unittest
import uuid
from unittest import mock

from support import FixtureCase, project

import session_state  # noqa: E402
import sessionreport as R  # noqa: E402
import todo as T  # noqa: E402

SID_A = str(uuid.UUID(int=1))
SID_B = str(uuid.UUID(int=2))
SID_C = str(uuid.UUID(int=3))
SID_D = str(uuid.UUID(int=4))
SID_OWN = str(uuid.UUID(int=5))


def sess(sid, tid, wait=None, mode="work", **kw):
    s = {"session": sid, "id": tid, "mode": mode, "running": wait is None, "wait": wait,
         "why": "", "claude": 1, "shell": 2, "waits_for": []}
    s.update(kw)
    return s


class Report(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Sample Game", "none")
        self.work = self.new_ticket(self.root, "Feature: the work")
        self.ask = self.new_ticket(self.root, "Feature: the question")
        self.wait = self.new_ticket(self.root, "Feature: the waiter")
        self.done = self.new_ticket(self.root, "Feature: the done one")
        self.mid = self.new_ticket(self.root, "the manager", "--repeat", "manager")
        for tid in (self.work, self.ask, self.wait, self.done):
            rc, _o, err = self.todo(self.root, "move", tid, "InProgress", "--status",
                                    "implementing")
            self.assertEqual(rc, 0, err)
        rc, _o, err = self.todo(self.root, "move", self.done, "Verification")
        self.assertEqual(rc, 0, err)
        self.sessions = [
            sess(SID_A, self.work, None, doing="Bash: run the tests"),
            sess(SID_B, self.ask, "input", reported_why="Which lane? (Pipeline / Blocked)"),
            sess(SID_C, self.wait, "input", why="reported: waits", jobs=["job1"]),
            sess(SID_D, self.mid, "idle"),
        ]

    def run_report(self, since_last, sessions=None, tail="Landed the fix.\n\nWORK IS FINISHED"):
        with mock.patch.object(T, "live_sessions", return_value=sessions or self.sessions), \
                mock.patch.object(T, "_is_own_session",
                                  side_effect=lambda s, *_a: s["session"] == SID_OWN), \
                mock.patch.object(session_state, "transcript_tail",
                                  return_value=(tail, True, 0)):
            rows, live = R.collect(since_last)
        if since_last:
            R.save_snapshot(live)
        return rows, R.render(rows)

    def section(self, rows, key):
        return sorted(r.get("id") for r in rows if r["section"] == key)

    def test_four_sections_in_order_and_an_empty_one_says_none(self):
        _rows, text = self.run_report(False)
        heads = [ln for ln in text.splitlines() if ln.startswith("**")]
        self.assertEqual(heads, ["**Finished since the last update**", "**Waiting for your input**",
                                 "**Working**", "**Other**"])
        finished = text.split("**Waiting for your input**")[0]
        self.assertIn("none", finished)

    def test_each_session_lands_in_its_section(self):
        own = sess(SID_OWN, None, None, mode="cli")
        rows, text = self.run_report(False, self.sessions + [own])
        self.assertEqual(self.section(rows, "working"), [self.work])
        self.assertEqual(self.section(rows, "input"), [self.ask])
        # `input` with a scheduled job still does its own work: Other.
        self.assertEqual(self.section(rows, "other"), [self.wait])
        # The manager and the session that runs the report are left out.
        self.assertNotIn(self.mid, [r.get("id") for r in rows])
        self.assertNotIn(SID_OWN, [r.get("session") for r in rows])
        self.assertIn("Which lane? (Pipeline / Blocked)", text)
        self.assertIn('- Sample Game %s "Feature: the work" — Bash: run the tests'
                      % self.work, text)

    def test_since_last_reports_a_gone_session_once(self):
        gone_done = sess(str(uuid.UUID(int=6)), self.done, None)
        self.run_report(True, self.sessions + [gone_done])
        self.assertTrue(R.snapshot_file().exists())
        # The terminal of the finished session closed: it is finished since the last call.
        rows, text = self.run_report(True)
        self.assertEqual(self.section(rows, "finished"), [self.done])
        self.assertIn('"Feature: the done one" — Verification / waiting\n', text)
        rows, _text = self.run_report(True)
        self.assertEqual(self.section(rows, "finished"), [])

    def test_a_gone_session_with_its_work_undone_is_other(self):
        self.run_report(True)
        rest = [s for s in self.sessions if s["session"] != SID_A]
        rows, text = self.run_report(True, rest)
        self.assertEqual(self.section(rows, "finished"), [])
        self.assertIn(self.work, self.section(rows, "other"))
        self.assertIn("closed with the work undone", text)

    def test_a_finished_agent_is_its_outcome_line(self):
        rc, _o, err = self.todo(self.root, "outcome", self.done, "The board shows the outcome")
        self.assertEqual(rc, 0, err)
        rows, text = self.run_report(True, [sess(SID_A, self.done, "finished")])
        self.assertIn('- Sample Game %s "Feature: the done one" — The board shows the '
                      'outcome; Verification / waiting\n' % self.done, text)
        # The last message of the session is not the result: the outcome is.
        self.assertNotIn("Landed the fix", text)

    def test_a_finished_open_session_is_reported_once(self):
        fin = sess(SID_A, self.done, "finished")
        rows, _t = self.run_report(True, [fin])
        self.assertEqual(self.section(rows, "finished"), [self.done])
        rows, _t = self.run_report(True, [fin])
        self.assertEqual(self.section(rows, "finished"), [])

    def test_the_cli_prints_the_sections(self):
        rc, out, err = self.cli("sessions", "--since-last")
        self.assertEqual(rc, 0, err)
        self.assertIn("**Finished since the last update**", out)
        self.assertIn("**Other**", out)
        data = json.loads(R.snapshot_file().read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_result_line_skips_marks_and_the_finished_line(self):
        self.assertEqual(R.result_line("## Done\n\nWORK IS FINISHED"), "Done")
        self.assertEqual(R.result_line("| a | b |\n|---|---|"), "a | b")
        self.assertEqual(R.result_line("WORK IS FINISHED"), "")


class Claim(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Sample Game", "none")
        self.mid = self.new_ticket(self.root, "the manager", "--repeat", "manager",
                                   "--permits", "projects")
        self.tok = project.use(self.root)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def ticket(self, tid=None):
        return T.find(T.load_all(), tid or self.mid)

    def test_claim_records_this_session_as_the_managers_work_session(self):
        with mock.patch.object(T, "running_managers", return_value=[]):
            rc = T.claim_manager(self.ticket(), sid=SID_A)
        self.assertEqual(rc, 0)
        t = self.ticket()
        self.assertEqual((t.state, t.status), (T.lane("active"), T.S("implementing")))
        self.assertEqual(T.ticket_session_id(t, "work"), SID_A)
        self.assertEqual(t.meta["session"], SID_A)
        self.assertTrue((self.root / "TODO" / ".briefs" / ("%s.md" % self.mid)).exists())
        # The watchdog sees the claimed session as the manager's.
        s = {"id": self.mid, "mode": "work", "claude": 1, "session": SID_A}
        self.assertTrue(T.manager_running(t, [s]))
        self.assertFalse(T.manager_dead(t, [s]))

    def test_a_running_manager_refuses_the_claim(self):
        other = (project.current().title, self.root, self.mid,
                 {"session": SID_B, "id": self.mid, "mode": "work", "running": False,
                  "wait": "idle", "claude": 1, "shell": 2})
        with mock.patch.object(T, "running_managers", return_value=[other]), \
                mock.patch.object(T, "close_session") as close:
            rc = T.claim_manager(self.ticket(), sid=SID_A)
        self.assertEqual(rc, 3)
        self.assertFalse(close.called)
        self.assertEqual(self.ticket().state, T.lane("start"))

    def test_take_over_closes_the_running_manager(self):
        other = (project.current().title, self.root, self.mid,
                 {"session": SID_B, "id": self.mid, "mode": "work", "running": False,
                  "wait": "idle", "claude": 1, "shell": 2})
        with mock.patch.object(T, "running_managers", return_value=[other]), \
                mock.patch.object(T, "close_session", return_value=0) as close:
            rc = T.claim_manager(self.ticket(), take_over=True, sid=SID_A)
        self.assertEqual(rc, 0)
        self.assertEqual(close.call_args[0][0]["session"], SID_B)
        self.assertEqual(T.ticket_session_id(self.ticket(), "work"), SID_A)

    def test_take_over_returns_another_manager_ticket_to_the_start_lane(self):
        root2 = self.make("Other Game", "none")
        mid2 = self.new_ticket(root2, "the other manager", "--repeat", "manager")
        rc, _o, err = self.todo(root2, "move", mid2, "InProgress", "--status", "implementing")
        self.assertEqual(rc, 0, err)
        other = ("Other Game", root2, mid2,
                 {"session": SID_B, "id": mid2, "mode": "work", "running": False,
                  "wait": "idle", "claude": 1, "shell": 2})
        with mock.patch.object(T, "running_managers", return_value=[other]), \
                mock.patch.object(T, "close_session", return_value=0):
            rc = T.claim_manager(self.ticket(), take_over=True, sid=SID_A)
        self.assertEqual(rc, 0)
        tok = project.use(root2)
        try:
            self.assertEqual(T.find(T.load_all(), mid2).state, T.lane("start"))
        finally:
            project.reset(tok)
        self.assertEqual(self.ticket().state, T.lane("active"))

    def test_no_session_id_refuses(self):
        with mock.patch.object(T, "own_session_id", return_value=""):
            rc = T.claim_manager(self.ticket())
        self.assertEqual(rc, 2)

    def test_a_hand_session_a_ticket_records_is_that_tickets_session(self):
        import winproc
        with mock.patch.object(T, "running_managers", return_value=[]):
            T.claim_manager(self.ticket(), sid=SID_A)
        t = self.ticket()
        procs = {300: (1, "claude.exe")}
        rec = {SID_A: {"claude": 300, "shell": 0, "claude_alive": True, "shell_alive": False}}
        with mock.patch.object(winproc, "processes", return_value=procs), \
                mock.patch.object(winproc, "command_lines", return_value={}), \
                mock.patch.object(winproc, "created_at", return_value=0), \
                mock.patch.object(session_state, "live_records", return_value=rec), \
                mock.patch.object(T, "pty_records", return_value={}), \
                mock.patch.object(T, "read_turn", side_effect=lambda s, *_a: s):
            found = T.live_sessions([t])
        self.assertEqual([(s["id"], s["mode"]) for s in found], [(self.mid, "work")])
        self.assertTrue(T.manager_running(t, found))


if __name__ == "__main__":
    unittest.main()
