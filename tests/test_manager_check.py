"""The manager's 10-minute check (`octopus check`): the full update while any agent finished,
waits or works, nothing only when none does,
the follow-ups a finished cross-project ticket left unlaunched, the age of the check job,
and the watchdog's wake of a manager whose agent has no scheduler.
python -m unittest discover tests"""

import io
import unittest
import uuid
from unittest import mock

from support import FixtureCase, project

import admin  # noqa: E402
import managercheck as C  # noqa: E402
import providers  # noqa: E402
import session_state  # noqa: E402
import sessionreport as R  # noqa: E402
import todo as T  # noqa: E402
import todo_server  # noqa: E402

SID_A = str(uuid.UUID(int=11))
SID_B = str(uuid.UUID(int=12))
DAY = 24 * 3600


def sess(sid, tid, wait=None, mode="work", **kw):
    s = {"session": sid, "id": tid, "mode": mode, "running": wait is None, "wait": wait,
         "why": "", "claude": 1, "shell": 2, "waits_for": [], "key": "%s-%s" % (tid, mode)}
    s.update(kw)
    return s


class IfAny(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Sample Game")
        self.a = self.new_ticket(self.root, "Feature: the first")
        self.b = self.new_ticket(self.root, "Feature: the second")
        for tid in (self.a, self.b):
            rc, _o, err = self.todo(self.root, "move", tid, "InProgress", "--status",
                                    "implementing")
            self.assertEqual(rc, 0, err)

    def report(self, sessions):
        out = io.StringIO()
        with mock.patch.object(T, "live_sessions", return_value=sessions),                 mock.patch.object(T, "_is_own_session", return_value=False),                 mock.patch.object(session_state, "transcript_tail",
                                  return_value=("Done.", True, 0)):
            R.report(since_last=True, if_any=True, out=out)
        out = out.getvalue()
        self.assertNotIn("no change", out.lower())
        self.assertNotIn("nothing changed", out.lower())
        return out

    def test_a_working_agent_is_reported_on_every_run(self):
        self.assertIn("**Working**", self.report([sess(SID_A, self.a, None, doing="Bash: one")]))
        out = self.report([sess(SID_A, self.a, None, doing="Bash: one")])
        for heading in ("Finished since the last update", "Waiting for your input",
                        "Working", "Other"):
            self.assertIn("**%s**" % heading, out)
        self.assertIn(self.a, out)

    def test_a_waiting_question_is_repeated_word_for_word_until_answered(self):
        q = "Which way should the parser go? " + "(a: keep the old grammar / b: drop it) " * 8
        live = [sess(SID_A, self.a, "input", reported_why=q)]
        first, second = self.report(live), self.report(live)
        self.assertIn(" ".join(q.split()), first)
        self.assertEqual(first, second)
        self.assertNotIn(" ".join(q.split()), self.report([sess(SID_A, self.a, None)]))

    def test_an_agent_waiting_for_something_else_is_reported(self):
        live = [sess(SID_A, self.a, "event", why="an editor restart")]
        self.report(live)
        self.assertIn("an editor restart", self.report(live))

    def test_a_finished_agent_is_reported_once_then_nothing(self):
        self.report([sess(SID_A, self.a, None)])
        out = self.report([])
        self.assertIn("**Finished since the last update**", out)
        self.assertIn(self.a, out)
        self.assertEqual(self.report([]), "")

    def test_no_agent_at_all_prints_nothing(self):
        self.assertEqual(self.report([]), "")
        self.assertEqual(self.report([]), "")

    def test_the_cli_takes_if_any(self):
        rc, out, err = self.cli("sessions", "--if-any")
        self.assertEqual(rc, 0, err)
        self.assertTrue(R.snapshot_file().exists())
        rc, out, err = self.cli("sessions", "--if-any")
        self.assertEqual((rc, out.strip()), (0, ""), err)


class ManagerText(unittest.TestCase):
    """The shipped manager texts never give the manager a "no change" line to copy."""

    def test_no_shipped_manager_text_offers_a_no_change_line(self):
        from support import OCTOPUS
        for rel in ("core/engine/brief/repeat.manager.md", "core/skills/octopusmanager/SKILL.md",
                    "core/engine/managercheck.py", "core/engine/sessionreport.py"):
            text = (OCTOPUS / rel).read_text(encoding="utf-8").lower()
            self.assertNotIn("only when an agent changed", text, rel)
            self.assertNotIn("--if-changed", text, rel)


class FollowUps(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Sample Game")
        self.other = self.make("Other Game")
        self.fu = self.new_ticket(self.other, "Feature: the follow-up")
        self.parent = self.new_ticket(self.root, "Feature: every project",
                                      "--permits", "projects")
        self.finish(self.parent, ["- Other Game %s: Feature: the follow-up" % self.fu])

    def finish(self, tid, lines, lane="Verification"):
        for to in ("InProgress", lane):
            rc, _o, err = self.todo(self.root, "move", tid, to)
            self.assertEqual(rc, 0, err)
        tok = project.use(self.root)
        try:
            t = T.find(T.load_all(), tid)
            t.path.write_text(t.path.read_text(encoding="utf-8")
                              + "\n## Follow-ups\n\n" + "\n".join(lines) + "\n",
                              encoding="utf-8")
        finally:
            project.reset(tok)

    def found(self):
        return [(n, tid) for n, _r, tid, _s in C.unlaunched()]

    def test_the_section_names_its_project_and_ids(self):
        body = "## Plan\n\nT0009\n\n## Follow-ups\n\n- Other Game T0003: x\n- T0004 here\n"
        self.assertEqual(C.named_followups(body, ["Other Game", "Other"], "Own"),
                         [("Other Game", "T0003"), ("Own", "T0004")])

    def test_an_unlaunched_follow_up_of_a_finished_ticket_is_found(self):
        self.assertEqual(self.found(), [("Other Game", self.fu)])

    def test_a_launched_follow_up_is_left_alone(self):
        tok = project.use(self.other)
        try:
            T.record_launch(T.find(T.load_all(), self.fu), "work")
        finally:
            project.reset(tok)
        self.assertEqual(self.found(), [])

    def test_a_ticket_still_open_or_without_the_permit_names_none(self):
        own = self.new_ticket(self.other, "Feature: another")
        plain = self.new_ticket(self.root, "Feature: no permit")
        self.finish(plain, ["- Other Game %s" % own])
        rc, _o, err = self.todo(self.root, "move", self.parent, "InProgress")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.found(), [])

    def test_the_check_launches_it_by_its_difficulty(self):
        work = mock.Mock(return_value=0)
        with mock.patch.object(T, "cmd_work", work):
            done = C.launch_followups()
        self.assertEqual(done, [("Other Game", self.fu)])
        a = work.call_args[0][0]
        self.assertEqual((a.id, a.mode), (self.fu, "plan"))
        self.assertIn("Sample Game %s" % self.parent, a.note)

    def test_a_failed_launch_says_so_and_prints_nothing_to_the_update(self):
        err = io.StringIO()
        with mock.patch.object(T, "cmd_work", side_effect=lambda a: print("boom") or 2):
            done = C.launch_followups(err=err)
        self.assertEqual(done, [])
        self.assertIn("the launch failed: boom", err.getvalue())


class JobAge(FixtureCase):
    def setUp(self):
        super().setUp()
        self.make("Sample Game")

    def run_check(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(C, "launch_followups", return_value=[]), \
                mock.patch.object(R, "report", return_value=0):
            rc = C.run(out=out, err=err, **kw)
        return rc, err.getvalue()

    def test_job_created_records_the_session(self):
        rc, _e = self.run_check(job_created=True, sid=SID_A, now=1000)
        self.assertEqual(rc, 0)
        self.assertEqual(C.load_jobs()[SID_A], 1000)

    def test_a_fresh_job_needs_nothing(self):
        C.record_job(SID_A, now=0)
        self.assertEqual(self.run_check(sid=SID_A, now=5 * DAY)[0], 0)

    def test_an_old_job_exits_three_and_says_to_make_it_again(self):
        C.record_job(SID_A, now=0)
        rc, err = self.run_check(sid=SID_A, now=6 * DAY + 1)
        self.assertEqual(rc, C.RENEW)
        self.assertIn("CronCreate", err)

    def test_a_session_with_no_record_needs_nothing(self):
        C.record_job(SID_A, now=0)
        self.assertEqual(self.run_check(sid=SID_B, now=30 * DAY)[0], 0)

    def test_job_created_without_a_session_refuses(self):
        self.assertEqual(self.run_check(job_created=True, sid="")[0], 2)


class Wake(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make(admin.SELF_NAME)
        self.mid = self.new_ticket(self.root, "the manager", "--repeat", "manager")
        rc, _out, err = self.todo(self.root, "move", self.mid, "InProgress",
                                  "--status", "implementing")
        self.assertEqual(rc, 0, err)
        todo_server._WAKES.clear()
        self.tok = project.use(self.root)
        t = T.find(T.load_all(), self.mid)
        t.meta["provider"] = "codex"
        T._write(t.path, T.render(t.meta, t.body))
        self.live = [sess(SID_A, self.mid, None, provider="codex")]
        self.send = mock.Mock()
        self.patches = [mock.patch.object(T, "live_sessions",
                                          lambda tickets=None: self.live),
                        mock.patch.object(T, "send_session", self.send)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        todo_server._WAKES.clear()
        project.reset(self.tok)
        super().tearDown()

    def test_a_manager_without_a_scheduler_is_woken_every_ten_minutes(self):
        now = 1e9
        self.assertEqual(todo_server._wake_project(self.root, now), [])
        self.assertEqual(todo_server._wake_project(self.root, now + C.INTERVAL - 1), [])
        self.assertEqual(len(todo_server._wake_project(self.root, now + C.INTERVAL)), 1)
        text, keys = self.send.call_args[0][1:]
        self.assertIn("check", text)
        self.assertEqual(keys, ["Enter"])
        self.assertEqual(todo_server._wake_project(self.root, now + C.INTERVAL + 30), [])

    def test_a_manager_waiting_for_the_owner_is_not_typed_into(self):
        self.live = [sess(SID_A, self.mid, "input", provider="codex")]
        todo_server._wake_project(self.root, 1e9)
        self.assertEqual(todo_server._wake_project(self.root, 1e9 + C.INTERVAL), [])
        self.assertFalse(self.send.called)

    def test_a_claude_manager_schedules_its_own_check(self):
        t = T.find(T.load_all(), self.mid)
        t.meta.pop("provider")
        T._write(t.path, T.render(t.meta, t.body))
        self.live = [sess(SID_A, self.mid, None)]
        todo_server._wake_project(self.root, 1e9)
        self.assertEqual(todo_server._wake_project(self.root, 1e9 + C.INTERVAL), [])
        self.assertFalse(self.send.called)

    def test_only_claude_has_a_scheduler(self):
        p = providers.load()
        self.assertTrue(p["claude"]["features"]["scheduler"])
        self.assertFalse(p["codex"]["features"]["scheduler"])


class Brief(FixtureCase):
    def test_the_manager_brief_starts_the_check(self):
        root = self.make("Sample Game")
        mid = self.new_ticket(root, "the manager", "--repeat", "manager", "--permits",
                              "projects")
        tok = project.use(root)
        try:
            text = T.build_brief(T.find(T.load_all(), mid), "work")
        finally:
            project.reset(tok)
        self.assertIn("CronCreate", text)
        self.assertIn("1,11,21,31,41,51 * * * *", text)
        self.assertIn('octopus.py" check --job-created', text)
        self.assertNotIn("{octopus}", text)


if __name__ == "__main__":
    unittest.main()
