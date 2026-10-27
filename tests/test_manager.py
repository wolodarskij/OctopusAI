"""The manager ticket: its lanes, its brief, its permit, and the watchdog that restarts it.
python -m unittest discover tests"""

import argparse
import unittest
from pathlib import Path
from unittest import mock

from support import FixtureCase, project

import admin  # noqa: E402
import session_state  # noqa: E402
import todo as T  # noqa: E402
import todo_server  # noqa: E402


def session(tid, mode="work", claude=1234):
    return {"id": tid, "mode": mode, "running": False, "claude": claude}


class Manager(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Boss", "none")
        self.mid = self.new_ticket(self.root, "the manager", "--repeat", "manager",
                                   "--permits", "projects")

    def file(self):
        return next((self.root / "TODO").rglob("%s-*.md" % self.mid))

    def ticket(self):
        tok = project.use(self.root)
        try:
            return T.find(T.load_all(), self.mid)
        finally:
            project.reset(tok)

    def test_new_files_a_manager_in_pipeline(self):
        self.assertEqual(self.file().parent.name, "Pipeline")
        self.assertIn("repeat: manager", self.file().read_text(encoding="utf-8"))
        rc, out, err = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out + err)

    def test_move_refuses_verification_blocked_and_done(self):
        for lane in ("Verification", "Blocked"):
            rc, _out, err = self.todo(self.root, "move", self.mid, lane)
            self.assertEqual(rc, 1, err)
            self.assertIn("manager ticket", err)
            self.assertEqual(self.file().parent.name, "Pipeline")
        rc, _out, err = self.todo(self.root, "move", self.mid, "Done", "--verified")
        self.assertEqual(rc, 1, err)
        self.assertEqual(self.file().parent.name, "Pipeline")

    def test_move_to_in_progress_and_back_is_allowed(self):
        rc, _out, err = self.todo(self.root, "move", self.mid, "InProgress",
                                  "--status", "implementing")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.file().parent.name, "InProgress")
        rc, out, err = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out + err)
        rc, _out, err = self.todo(self.root, "move", self.mid, "Pipeline")
        self.assertEqual(rc, 0, err)

    def test_the_owner_retires_it_as_cancelled(self):
        rc, _out, err = self.todo(self.root, "move", self.mid, "Done", "--status", "cancelled")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.file().parent.name, "Done")
        text = self.file().read_text(encoding="utf-8")
        self.assertIn("status: cancelled", text)
        self.assertNotIn("## Runs", text)
        rc, out, err = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out + err)

    def test_an_epic_move_with_a_manager_in_it_is_refused(self):
        rc, _out, err = self.todo(self.root, "epic", "grp", "--title", "g")
        self.assertEqual(rc, 0, err)
        dst = self.root / "TODO" / "Pipeline" / "grp" / self.file().name
        self.file().rename(dst)
        rc, _out, err = self.todo(self.root, "move", "--epic", "grp", "Verification")
        self.assertEqual(rc, 1, err)
        self.assertTrue(dst.exists())

    def test_new_refuses_a_manager_straight_into_verification(self):
        rc, _out, err = self.todo(self.root, "new", "--title", "m2", "--repeat", "manager",
                                  "--state", "Verification")
        self.assertEqual(rc, 1, err)
        self.assertIn("never enters Verification", err)

    def test_check_fails_a_manager_put_into_verification_by_hand(self):
        dst = self.root / "TODO" / "Verification" / self.file().name
        dst.parent.mkdir(parents=True, exist_ok=True)
        self.file().rename(dst)
        self.todo(self.root, "index")
        rc, out, _err = self.todo(self.root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("never enters Verification", out)

    def test_check_fails_a_manager_closed_as_done(self):
        src = self.file()
        dst = self.root / "TODO" / "Done" / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8").replace("status: pipeline", "status: done")
        dst.write_text(text, encoding="utf-8")
        src.unlink()
        self.todo(self.root, "index")
        rc, out, _err = self.todo(self.root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("enters Done only as", out)

    def test_plan_review_and_verify_are_refused(self):
        for mode in ("plan", "review", "verify"):
            rc, _out, err = self.todo(self.root, mode, self.mid, "--dry-run")
            self.assertEqual(rc, 2, mode)
            self.assertIn("runs only as `work`", err)
        rc, _out, err = self.todo(self.root, "work", self.mid, "--dry-run")
        self.assertEqual(rc, 0, err)

    def test_the_brief_lists_the_projects_and_never_hands_over(self):
        other = self.make("Other", "none")
        rc, out, err = self.todo(self.root, "brief", self.mid)
        self.assertEqual(rc, 0, err)
        brief = (self.root / out.strip()).read_text(encoding="utf-8")
        self.assertIn("You are the manager", brief)
        self.assertIn(str(other.resolve()), brief)
        self.assertIn('todo --project "Other"', brief)
        self.assertIn("Boss (this project)", brief)
        self.assertNotIn("move %s Verification" % self.mid, brief)
        self.assertNotIn("## When you finish", brief)
        self.assertNotIn("## The boundary", brief)
        self.assertNotIn("{projects}", brief)

    def test_work_adds_every_other_registered_root(self):
        other = self.make("Other", "none")
        third = self.make("Third", "none")
        rc, out, err = self.todo(self.root, "work", self.mid, "--dry-run")
        self.assertEqual(rc, 0, err)
        line = out.splitlines()[0]
        self.assertIn("--add-dir", line)
        dirs = line.split("--add-dir", 1)[1].split("--model")[0]
        self.assertIn(str(other.resolve()), dirs)
        self.assertIn(str(third.resolve()), dirs)
        self.assertNotIn(str(self.root.resolve()), dirs)

    def test_a_ticket_without_the_permit_adds_no_root(self):
        self.make("Other", "none")
        tid = self.new_ticket(self.root, "plain")
        rc, out, err = self.todo(self.root, "work", tid, "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("--add-dir", out.splitlines()[0])

    def test_restart_no_launch_returns_it_to_pipeline(self):
        self.todo(self.root, "move", self.mid, "InProgress", "--status", "implementing")
        rc, _out, err = self.todo(self.root, "restart", self.mid, "--no-launch")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.file().parent.name, "Pipeline")


class ManagerDead(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Dead", "none")
        self.mid = self.new_ticket(self.root, "the manager", "--repeat", "manager")
        self.tok = project.use(self.root)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def t(self, lane="InProgress"):
        t = T.find(T.load_all(), self.mid)
        t.state = lane
        return t

    def test_no_entry_is_dead(self):
        self.assertTrue(T.manager_dead(self.t(), []))
        self.assertTrue(T.manager_dead(self.t(), [session("T9999")]))

    def test_an_entry_without_claude_is_dead(self):
        self.assertTrue(T.manager_dead(self.t(), [session(self.mid, claude=None)]))

    def test_an_entry_of_another_mode_is_dead(self):
        self.assertTrue(T.manager_dead(self.t(), [session(self.mid, mode="plan")]))

    def test_an_entry_with_claude_is_alive(self):
        self.assertFalse(T.manager_dead(self.t(), [session(self.mid)]))

    def test_a_manager_in_pipeline_is_never_dead(self):
        self.assertFalse(T.manager_dead(self.t("Pipeline"), []))

    def test_an_ordinary_ticket_is_never_dead(self):
        tid = self.new_ticket(self.root, "plain")
        t = T.find(T.load_all(), tid)
        t.state = "InProgress"
        self.assertFalse(T.manager_dead(t, []))


class Watchdog(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make(admin.SELF_NAME, "none")
        self.mid = self.new_ticket(self.root, "the manager", "--repeat", "manager")
        rc, _out, err = self.todo(self.root, "move", self.mid, "InProgress",
                                  "--status", "implementing")
        self.assertEqual(rc, 0, err)
        todo_server._WATCH.clear()
        self.live = []
        self.work = mock.Mock(return_value=0)
        self.patches = [mock.patch.object(todo_server.T, "live_sessions",
                                          lambda tickets=None: self.live),
                        mock.patch.object(todo_server.T, "cmd_work", self.work)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        todo_server._WATCH.clear()
        super().tearDown()

    def view(self):
        return todo_server.watchdog_view(self.root.resolve())[self.mid]

    def test_restarts_on_the_second_dead_tick_only(self):
        now = 1e9
        todo_server.watchdog_tick(now)
        self.work.assert_not_called()
        todo_server.watchdog_tick(now + 30)
        self.work.assert_called_once()
        args = self.work.call_args.args[0]
        self.assertEqual((args.id, args.mode), (self.mid, "work"))
        self.assertEqual(self.view()["restarts"], 1)

    def test_no_restart_within_the_launch_grace(self):
        now = 1e9
        todo_server.watchdog_tick(now)
        todo_server.watchdog_tick(now + 30)
        self.assertEqual(self.work.call_count, 1)
        todo_server.watchdog_tick(now + 60)
        todo_server.watchdog_tick(now + 80)
        self.assertEqual(self.work.call_count, 1)
        todo_server.watchdog_tick(now + 100)
        todo_server.watchdog_tick(now + 130)
        self.assertEqual(self.work.call_count, 2)

    def test_a_live_session_resets_the_count(self):
        now = 1e9
        todo_server.watchdog_tick(now)
        self.live[:] = [dict(session(self.mid), id=self.mid)]
        todo_server.watchdog_tick(now + 30)
        self.live[:] = []
        todo_server.watchdog_tick(now + 60)
        self.work.assert_not_called()

    def test_stops_after_five_restarts_in_ten_minutes(self):
        now = 1e9
        for i in range(20):
            todo_server.watchdog_tick(now + 30 * i)
        self.assertEqual(self.work.call_count, 5)
        self.assertTrue(self.view()["stopped"])
        # the session runs again: the watchdog starts over
        self.live[:] = [session(self.mid)]
        todo_server.watchdog_tick(now + 30 * 20)
        self.assertFalse(self.view()["stopped"])

    def test_a_manager_in_pipeline_is_left_alone(self):
        self.todo(self.root, "move", self.mid, "Pipeline")
        for i in range(4):
            todo_server.watchdog_tick(1e9 + 30 * i)
        self.work.assert_not_called()

    def test_a_failing_project_does_not_stop_the_tick(self):
        with mock.patch.object(todo_server, "_watch_project", side_effect=RuntimeError("x")):
            todo_server.watchdog_tick(1e9)

    def test_step_is_pure(self):
        e = {}
        self.assertFalse(todo_server.watchdog_step(e, True, 1000, 0))
        self.assertTrue(todo_server.watchdog_step(e, True, 1030, 0))
        self.assertFalse(todo_server.watchdog_step({}, True, 1000, 990))


class AutoClose(FixtureCase):
    """The owner (2026-10-18): every agent that finished its work closes its window; never
    the manager, never a session that waits for the owner."""

    def setUp(self):
        super().setUp()
        self.root = self.make("Shut", "none")
        self.done = self.new_ticket(self.root, "the finished one")
        self.asks = self.new_ticket(self.root, "the one that asks")
        self.mid = self.new_ticket(self.root, "the manager", "--repeat", "manager")
        todo_server._FINISHED.clear()
        self.live = [self.sess(self.done, "verify", "finished"),
                     self.sess(self.asks, "work", "input"),
                     self.sess(self.mid, "work", "finished"),
                     self.sess("", "", "finished")]
        self.closed = []
        self.patches = [mock.patch.object(todo_server.T, "live_sessions",
                                          lambda tickets=None: self.live),
                        mock.patch.object(todo_server.T, "close_session",
                                          side_effect=lambda s, force=False:
                                          self.closed.append(s["key"]) or 0)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        todo_server._FINISHED.clear()
        super().tearDown()

    @staticmethod
    def sess(tid, mode, wait):
        return {"key": "%s/%s" % (tid or "abcdef12", mode), "id": tid, "mode": mode,
                "running": False, "wait": wait, "claude": 1234, "shell": 99}

    def test_selects_only_finished_ticket_sessions_that_are_no_manager(self):
        tok = project.use(self.root)
        try:
            keys = [s["key"] for s in T.autoclose_sessions(self.live, T.load_all())]
        finally:
            project.reset(tok)
        self.assertEqual(keys, ["%s/verify" % self.done])

    def test_closes_a_finished_session_on_the_second_tick_only(self):
        todo_server.watchdog_tick(1e9)
        self.assertEqual(self.closed, [])
        todo_server.watchdog_tick(1e9 + 30)
        self.assertEqual(self.closed, ["%s/verify" % self.done])

    def test_a_session_that_stops_being_finished_stays_open(self):
        todo_server.watchdog_tick(1e9)
        self.live[0] = self.sess(self.done, "verify", "input")
        todo_server.watchdog_tick(1e9 + 30)
        self.live[0] = self.sess(self.done, "verify", "finished")
        todo_server.watchdog_tick(1e9 + 60)
        self.assertEqual(self.closed, [])


class BoardMove(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Drag", "none")
        self.mid = self.new_ticket(self.root, "the manager", "--repeat", "manager")
        self.tok = project.use(self.root)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def test_a_drag_to_verification_or_done_is_refused(self):
        for state in ("Verification", "Blocked", "Done"):
            with self.assertRaises(ValueError):
                todo_server.act_move({"ids": [self.mid], "state": state})
        todo_server.act_move({"ids": [self.mid], "state": "Done", "status": "cancelled"})
        self.assertEqual(T.find(T.load_all(), self.mid).state, "Done")


class ShippedManager(FixtureCase):
    """The owner (2026-10-26): the manager ships with the repository, with its general
    instructions, and the board starts it when it opens unless it runs. The owner
    (2026-10-27): the manager belongs to the Octopus AI project, whichever project is open."""

    def setUp(self):
        super().setUp()
        self.root = self.make(admin.SELF_NAME, "none")
        self.other = self.make("Away", "none")
        for root in (self.root, self.other):
            for f in (root / "TODO").rglob("T*.md"):
                if "repeat: manager" in f.read_text(encoding="utf-8"):
                    f.unlink()
            self.todo(root, "index")
        todo_server._WATCH.clear()
        todo_server.DEFAULT[0] = "Away"
        self.live = []
        self.work = mock.Mock(return_value=0)
        self.patches = [mock.patch.object(todo_server.T, "live_sessions",
                                          lambda tickets=None, **_k: self.live),
                        mock.patch.object(todo_server.T, "cmd_work", self.work)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        todo_server._WATCH.clear()
        todo_server.DEFAULT[0] = None
        super().tearDown()

    def managers(self, root):
        return [f for f in (root / "TODO").rglob("T*.md")
                if "repeat: manager" in f.read_text(encoding="utf-8")]

    def test_the_template_is_a_manager_with_its_permits(self):
        text = T.MANAGER_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("repeat: manager", text)
        self.assertIn("projects", text)
        self.assertNotIn("id:", text.split("---")[1])

    def test_the_brief_carries_the_standing_orders(self):
        rc, out, err = self.todo(self.root, "manager")
        self.assertEqual(rc, 0, err)
        mid = out.split()[0]
        rc, out, err = self.todo(self.root, "brief", mid)
        self.assertEqual(rc, 0, err)
        brief = (self.root / out.strip()).read_text(encoding="utf-8")
        for words in ("A task is a ticket", "Never wait passively", "Fix tooling faults",
                      "positive statement", "Project facts live in the project",
                      "**Finished since the last update**", "**Waiting for your input**",
                      "**Working**", "**Other**", "sessions --since-last", "Never repeat",
                      "Never write \"no change\""):
            self.assertIn(words, brief)

    def test_manager_files_one_from_the_template_once(self):
        rc, out, err = self.todo(self.root, "manager")
        self.assertEqual(rc, 0, err)
        self.assertIn("filed from the template", out)
        rc, out, err = self.todo(self.root, "manager")
        self.assertNotIn("filed from the template", out)
        self.assertEqual(len(self.managers(self.root)), 1)
        rc, out, err = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out + err)

    def test_manager_start_dry_run_launches_work(self):
        rc, out, err = self.todo(self.root, "manager", "--start", "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertIn("--remote-control", out)

    def test_a_retired_manager_is_never_filed_again_nor_started(self):
        rc, out, _err = self.todo(self.root, "manager")
        mid = out.split()[0]
        self.todo(self.root, "move", mid, "Done", "--status", "cancelled")
        self.assertEqual(todo_server.autostart_managers(1e9), [])
        self.work.assert_not_called()
        self.assertEqual(len(self.managers(self.root)), 1)

    def test_the_board_files_and_starts_the_home_manager(self):
        started = todo_server.autostart_managers(1e9)
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0][0], admin.SELF_NAME)
        self.assertEqual(len(self.managers(self.root)), 1)
        self.assertEqual(self.managers(self.other), [])
        args = self.work.call_args.args[0]
        self.assertEqual((args.id, args.mode), (started[0][1], "work"))

    def test_a_running_manager_is_left_alone(self):
        rc, out, _err = self.todo(self.root, "manager")
        mid = out.split()[0]
        self.todo(self.root, "move", mid, "InProgress", "--status", "implementing")
        self.live[:] = [session(mid)]
        self.assertEqual(todo_server.autostart_managers(1e9), [])
        self.work.assert_not_called()

    def test_manager_from_another_project_files_it_at_home(self):
        rc, out, err = self.todo(self.other, "manager")
        self.assertEqual(rc, 0, err)
        self.assertIn(admin.SELF_NAME, out)
        self.assertEqual(len(self.managers(self.root)), 1)
        self.assertEqual(self.managers(self.other), [])
        rc, out, err = self.todo(self.other, "watchdog")
        self.assertEqual(rc, 0, err)
        self.assertIn(out.split()[0], self.managers(self.root)[0].name)

    def test_an_active_manager_elsewhere_is_never_started(self):
        mid = self.new_ticket(self.other, "the manager", "--repeat", "manager")
        self.todo(self.other, "move", mid, "InProgress", "--status", "implementing")
        started = todo_server.autostart_managers(1e9)
        self.assertEqual([name for name, _id in started], [admin.SELF_NAME])
        self.assertEqual(len(self.managers(self.root)), 1)
        for i in range(1, 5):
            todo_server.watchdog_tick(1e9 + 100 * i)
        self.assertEqual({c.args[0].id for c in self.work.call_args_list},
                         {started[0][1]})

    def test_the_home_launch_counts_for_the_watchdogs_grace(self):
        rc, out, _err = self.todo(self.root, "manager")
        mid = out.split()[0]
        self.todo(self.root, "move", mid, "InProgress", "--status", "implementing")
        self.assertEqual(todo_server.autostart_managers(1e9), [(admin.SELF_NAME, mid)])
        todo_server.watchdog_tick(1e9 + 10)
        todo_server.watchdog_tick(1e9 + 40)
        self.assertEqual(self.work.call_count, 1)

    def test_a_claim_from_another_project_is_recorded_at_home(self):
        sid = "0b0e8f1c-1111-4222-8333-944455556666"
        tok = project.use(self.other)
        try:
            session_state.write_live(sid, {"session": sid, "claude": 4321, "shell": 0})
            with mock.patch.object(T, "own_session_id", return_value=sid), \
                    mock.patch.object(T, "running_managers", return_value=[]):
                rc = T.cmd_manager(argparse.Namespace(claim=True, take_over=False,
                                                      start=False))
            self.assertEqual(rc, 0)
            self.assertIsNone(session_state.read_live(sid))
            self.assertEqual(session_state.redirected_to(sid).resolve(), self.root.resolve())
        finally:
            project.reset(tok)
        self.assertEqual(self.managers(self.other), [])
        tok = project.use(self.root)
        try:
            t = next(t for t in T.load_all() if t.repeat == T.MANAGER)
            self.assertEqual((t.state, t.session), (T.lane("active"), sid))
            self.assertEqual(session_state.read_live(sid)["claude"], 4321)
        finally:
            project.reset(tok)

    def test_no_home_project_refuses(self):
        project.unregister(self.root)
        rc, _out, err = self.todo(self.other, "manager")
        self.assertEqual(rc, 2)
        self.assertIn("no registered project is the Octopus AI project", err)
        self.assertEqual(self.managers(self.other), [])


if __name__ == "__main__":
    unittest.main()
