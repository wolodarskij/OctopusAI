"""The manager's check (`octopus check`): the full update while any agent finished, waits or
works, nothing only when none does, no launch of a follow-up, the news that wakes the manager,
the engine's watcher, and the board's wake.
python -m unittest discover tests"""

import argparse
import contextlib
import io
import os
import unittest
import uuid
from unittest import mock

from support import FixtureCase, project

import admin  # noqa: E402
import managercheck as C  # noqa: E402
import session_state  # noqa: E402
import sessionreport as R  # noqa: E402
import todo as T  # noqa: E402
import todo_server  # noqa: E402

SID_A = str(uuid.UUID(int=11))
SID_B = str(uuid.UUID(int=12))


def box(text="", dim=None, cursor=None):
    """A Claude Code screen whose input box holds `text`, one line per row. The cursor stands
    after the text, as after typing, unless `cursor` names it."""
    rows = text.split("\n")
    lines = ["output", "─" * 20, "❯ " + rows[0]] + ["  " + r for r in rows[1:]]
    lines += ["─" * 20, "  ? for shortcuts"]
    return {"cols": 80, "rows": len(lines), "lines": lines,
            "cursor": cursor or [2 + len(rows[-1]), 1 + len(rows)],
            "dim": dim if dim is not None else [[] for _ in lines]}


SUGGESTION = dict(dim=[[], [], [2, 10], [], []], cursor=[2, 2])


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
                        "Working", "Blocked", "Other"):
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

    def write_body(self, tid, order, result):
        tok = project.use(self.root)
        try:
            t = T.find(T.load_all(), tid)
            text = t.path.read_text(encoding="utf-8")
            head = text[:text.index("\n## ")]
            t.path.write_text(head + "\n## What it should do\n\n" + order
                              + "\n\n## Result\n\n" + result + "\n", encoding="utf-8")
        finally:
            project.reset(tok)

    def test_a_finished_investigation_gives_its_answer(self):
        self.write_body(self.a, "Measure how long the board takes to load.",
                        "- The board loads in 2.4 s.\n- The ticket list takes 1.9 s of it.")
        self.write_body(self.b, "The board shows the outcome line.",
                        "- `core/engine/todo_board.html`")
        self.report([sess(SID_A, self.a, None), sess(SID_B, self.b, None)])
        out = self.report([])
        lines = out.splitlines()
        first = lines.index(next(x for x in lines if self.a in x))
        self.assertEqual(lines[first + 1:first + 3],
                         ["  - The board loads in 2.4 s.",
                          "  - The ticket list takes 1.9 s of it."])
        self.assertNotIn("todo_board.html", out)

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
                    "core/skills/manager-check/SKILL.md", "core/skills/manager-brief/SKILL.md",
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
        for to in ("InProgress", "Verification"):
            rc, _o, err = self.todo(self.root, "move", self.parent, to)
            self.assertEqual(rc, 0, err)
        tok = project.use(self.root)
        try:
            t = T.find(T.load_all(), self.parent)
            t.path.write_text(t.path.read_text(encoding="utf-8")
                              + "\n## Follow-ups\n\n- Other Game %s: Feature: the follow-up\n"
                              % self.fu, encoding="utf-8")
        finally:
            project.reset(tok)

    def test_the_section_names_its_project_and_ids(self):
        body = "## Plan\n\nT0009\n\n## Follow-ups\n\n- Other Game T0003: x\n- T0004 here\n"
        self.assertEqual(C.named_followups(body, ["Other Game", "Other"], "Own"),
                         [("Other Game", "T0003"), ("Own", "T0004")])

    def test_the_check_launches_no_follow_up(self):
        work = mock.Mock(return_value=0)
        with mock.patch.object(T, "cmd_work", work), \
                mock.patch.object(R, "report", return_value=0), \
                mock.patch.object(R, "collect", return_value=([], [])):
            for _ in range(3):
                C.run(out=io.StringIO(), err=io.StringIO())
                C.evaluate(err=io.StringIO())
        self.assertFalse(work.called)


class News(FixtureCase):
    def setUp(self):
        super().setUp()
        self.make("Sample Game")
        self.rows = []
        self.patches = [mock.patch.object(C, "sort_ai_tickets"),
                        mock.patch.object(C, "notice_ids", return_value=[]),
                        mock.patch.object(R, "collect",
                                          side_effect=lambda *a, **k: (self.rows, []))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def news(self, now=1000):
        news, seen = C.evaluate(now=now)
        if news:
            C.remember(seen)
        return news

    def test_a_working_agent_is_news_on_every_check(self):
        self.rows = [{"key": "a", "section": "working", "text": "Bash"}]
        self.assertTrue(self.news(1000))
        self.assertTrue(self.news(1000 + C.INTERVAL))

    def test_an_agent_waiting_for_something_else_is_news(self):
        self.rows = [{"key": "a", "section": "other", "text": "event: an editor restart"}]
        self.assertTrue(self.news())

    def test_a_finished_agent_is_news(self):
        self.rows = [{"key": "a", "section": "finished", "text": "done"}]
        self.assertTrue(self.news())

    def test_a_waiting_question_is_news_on_every_check(self):
        self.rows = [{"key": "a", "section": "input", "text": "Which one?"}]
        self.assertTrue(self.news())
        self.assertTrue(self.news())

    def test_no_agent_is_no_news(self):
        self.rows = []
        self.assertFalse(self.news())
        self.assertEqual(C.load_state()["checked"], 1000)

    def test_a_new_notice_is_news_once(self):
        with mock.patch.object(C, "notice_ids", return_value=["N0001"]):
            self.assertTrue(self.news())
            self.assertFalse(self.news())

    def test_news_not_remembered_stays_news(self):
        self.rows = [{"key": "a", "section": "input", "text": "Which one?"}]
        self.assertTrue(C.evaluate(now=1)[0])
        self.assertTrue(C.evaluate(now=2)[0])


class Watch(FixtureCase):
    def setUp(self):
        super().setUp()
        self.make("Sample Game")
        self.clock = [100000.0]
        self.wake = mock.Mock(return_value=[])
        self.stamp = mock.Mock(return_value=1)
        self.has_manager = mock.Mock(return_value=True)
        self.patches = [mock.patch.object(C, "wake_home", self.wake),
                        mock.patch.object(C, "_engine_stamp", self.stamp),
                        mock.patch.object(C, "_home_has_manager", self.has_manager),
                        mock.patch.object(T, "in_manager_home", contextlib.nullcontext)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def sleep(self, seconds):
        self.clock[0] += seconds

    def watch(self, loops, token="w1"):
        out = io.StringIO()
        rc = C.watch(out=out, now_fn=lambda: self.clock[0], sleep=self.sleep, token=token,
                     loops=loops)
        return rc, out.getvalue()

    def test_a_running_board_keeps_it_idle(self):
        C.mark_board(self.clock[0])
        self.watch(5)
        self.assertFalse(self.wake.called)

    def test_it_wakes_the_manager_and_keeps_running(self):
        self.wake.return_value = ["T0001-work"]
        self.assertEqual(self.watch(3), (0, ""))
        self.assertEqual(self.wake.call_count, 3)

    def test_it_records_its_process(self):
        self.watch(0)
        self.assertEqual(C.load_state()["watcher_pid"], os.getpid())

    def test_a_newer_watcher_replaces_it_silently(self):
        orig = self.sleep

        def sleep(seconds):
            orig(seconds)
            C.update_state(watcher="w2")
        self.sleep = sleep
        self.assertEqual(self.watch(None), (0, ""))
        self.assertFalse(self.wake.called)

    def test_a_code_change_ends_it(self):
        self.stamp.side_effect = [1, 2]
        self.assertEqual(self.watch(None), (0, ""))
        self.assertFalse(self.wake.called)

    def test_no_active_manager_ends_it(self):
        self.has_manager.return_value = False
        self.assertEqual(self.watch(None), (0, ""))
        self.assertFalse(self.wake.called)

    def test_a_failed_poll_keeps_it_running(self):
        self.wake.side_effect = [OSError("gone"), []]
        rc, out = self.watch(2)
        self.assertEqual(rc, 0)
        self.assertIn("OSError: gone", out)
        self.assertEqual(self.wake.call_count, 2)


class Wake(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make(admin.SELF_NAME)
        self.mid = self.new_ticket(self.root, "the manager", "--repeat", "manager")
        rc, _out, err = self.todo(self.root, "move", self.mid, "InProgress",
                                  "--status", "implementing")
        self.assertEqual(rc, 0, err)
        self.tok = project.use(self.root)
        self.live = [sess(SID_A, self.mid, None)]
        self.send = mock.Mock()
        self.news = (True, {"notices": []})
        self.screen = box()
        self.patches = [mock.patch.object(T, "live_sessions",
                                          lambda tickets=None: self.live),
                        mock.patch.object(T, "send_session", self.send),
                        mock.patch.object(T, "screen_session", lambda s: self.screen),
                        mock.patch.object(C, "evaluate", lambda **kw: self.news),
                        mock.patch.dict(todo_server._CHECK,
                                        {"since": 0.0, "managers": False, "skip": ""})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        project.reset(self.tok)
        super().tearDown()

    def test_a_manager_is_woken_on_news_at_most_every_ten_minutes(self):
        now = 1e9
        self.assertEqual(todo_server._wake_project(self.root, now), [])
        self.assertEqual(todo_server._wake_project(self.root, now + C.INTERVAL - 1), [])
        self.assertEqual(len(todo_server._wake_project(self.root, now + C.INTERVAL)), 1)
        text, keys = self.send.call_args[0][1:]
        self.assertIn("check", text)
        self.assertEqual(keys, ["Enter"])
        self.assertEqual(todo_server._wake_project(self.root, now + C.INTERVAL + 30), [])

    def test_no_news_types_nothing(self):
        self.news = (False, {"notices": []})
        todo_server._wake_project(self.root, 1e9)
        self.assertEqual(todo_server._wake_project(self.root, 1e9 + C.INTERVAL), [])
        self.assertFalse(self.send.called)

    def test_every_provider_is_woken(self):
        self.live = [sess(SID_A, self.mid, None, provider="codex")]
        todo_server._wake_project(self.root, 1e9)
        self.assertEqual(len(todo_server._wake_project(self.root, 1e9 + C.INTERVAL)), 1)

    def test_a_manager_waiting_for_the_owner_is_not_typed_into(self):
        self.live = [sess(SID_A, self.mid, "input", dialog=True)]
        todo_server._wake_project(self.root, 1e9)
        self.assertEqual(todo_server._wake_project(self.root, 1e9 + C.INTERVAL), [])
        self.assertFalse(self.send.called)

    def test_a_manager_read_as_input_from_its_update_is_woken(self):
        # The judge read the questions that the manager's update relays as its own.
        self.live = [sess(SID_A, self.mid, "input", why="judge: input")]
        todo_server._wake_project(self.root, 1e9)
        self.assertEqual(len(todo_server._wake_project(self.root, 1e9 + C.INTERVAL)), 1)

    def test_a_manager_whose_input_box_holds_text_is_not_typed_into(self):
        todo_server._wake_project(self.root, 1e9)
        self.screen = box("Documentation bug is always filed as")
        self.assertEqual(todo_server._wake_project(self.root, 1e9 + C.INTERVAL), [])
        self.assertFalse(self.send.called)
        self.assertIn("unsent text", todo_server._CHECK["skip"])
        # The box is empty again: the next tick types the check, with no new interval.
        self.screen = box()
        self.assertEqual(len(todo_server._wake_project(self.root, 1e9 + C.INTERVAL + 30)), 1)

    def test_a_box_that_fills_before_the_check_is_typed_gives_the_wake_back(self):
        todo_server._wake_project(self.root, 1e9)
        screens = iter([box(), box("s")])
        with mock.patch.object(T, "screen_session", lambda s: next(screens)):
            self.assertEqual(todo_server._wake_project(self.root, 1e9 + C.INTERVAL), [])
        self.assertFalse(self.send.called)
        self.assertEqual(len(todo_server._wake_project(self.root, 1e9 + C.INTERVAL + 30)), 1)

    def test_a_suggested_prompt_does_not_hold_the_check(self):
        todo_server._wake_project(self.root, 1e9)
        self.screen = box("try this", **SUGGESTION)
        self.assertEqual(len(todo_server._wake_project(self.root, 1e9 + C.INTERVAL)), 1)

    def test_a_manager_with_an_old_brief_and_text_in_its_box_gets_no_brief(self):
        t, _brief = self.stale_brief()
        self.screen = box("half a message")
        todo_server._wake_project(self.root, 1e9)
        self.assertFalse(self.send.called)
        self.assertTrue(T.manager_brief_stale(t))

    def test_a_lone_character_that_stands_unchanged_is_deleted_and_the_check_typed(self):
        todo_server._wake_project(self.root, 1e9)
        self.screen = box("#")
        self.assertEqual(todo_server._wake_project(self.root, 1e9 + 30), [])

        def send(s, text, keys):
            if keys == ["Backspace"]:
                self.screen = box()
        self.send.side_effect = send
        self.assertEqual(len(todo_server._wake_project(self.root, 1e9 + C.INTERVAL)), 1)
        self.assertEqual([c[0][1:] for c in self.send.call_args_list],
                         [("", ["Backspace"]), ("/manager-check", ["Enter"])])

    def test_a_lone_character_that_just_came_is_typing(self):
        todo_server._wake_project(self.root, 1e9)
        self.screen = box("D")
        self.assertEqual(todo_server._wake_project(self.root, 1e9 + C.INTERVAL), [])
        self.screen = box("Do")
        self.assertEqual(todo_server._wake_project(self.root, 1e9 + C.INTERVAL + 90), [])
        self.assertFalse(self.send.called)

    def test_a_lone_character_with_the_cursor_elsewhere_is_typing(self):
        todo_server._wake_project(self.root, 1e9)
        self.screen = box("#", cursor=[5, 2])
        todo_server._wake_project(self.root, 1e9 + 30)
        self.assertEqual(todo_server._wake_project(self.root, 1e9 + C.INTERVAL), [])
        self.assertFalse(self.send.called)

    def test_a_stray_character_that_stays_after_the_delete_holds_the_check(self):
        todo_server._wake_project(self.root, 1e9)
        self.screen = box("#")
        todo_server._wake_project(self.root, 1e9 + 30)
        with mock.patch.object(C, "STRAY_CLEAR", 0):
            self.assertEqual(todo_server._wake_project(self.root, 1e9 + C.INTERVAL), [])
        self.assertEqual([c[0][1:] for c in self.send.call_args_list], [("", ["Backspace"])])

    def test_a_claude_screen_with_no_input_box_holds_the_check(self):
        todo_server._wake_project(self.root, 1e9)
        self.screen = {"cols": 80, "rows": 1, "cursor": [0, 0], "lines": ["output"],
                       "dim": [[]]}
        self.assertEqual(todo_server._wake_project(self.root, 1e9 + C.INTERVAL), [])
        self.assertFalse(self.send.called)

    def test_the_board_writes_its_heartbeat(self):
        todo_server._wake_project(self.root, 1e9)
        self.assertTrue(C.board_runs(1e9 + C.INTERVAL))
        self.assertFalse(C.board_runs(1e9 + C.BOARD_STALE))

    def stale_brief(self):
        t = T.find(T.load_all(), self.mid)
        brief = T.rewrite_manager_brief(t)
        os.utime(str(brief), ns=(1, 1))
        self.assertTrue(T.manager_brief_stale(t))
        return t, brief

    def test_a_manager_with_an_old_brief_is_told_to_read_the_new_one(self):
        t, brief = self.stale_brief()
        self.assertEqual(todo_server._wake_project(self.root, 1e9), [])
        text = self.send.call_args[0][1]
        self.assertEqual(text, '/manager-brief "%s"' % brief.as_posix())
        self.assertFalse(T.manager_brief_stale(t))
        self.send.reset_mock()
        todo_server._wake_project(self.root, 1e9 + 30)
        self.assertFalse(self.send.called)

    def test_a_session_with_no_brief_skill_gets_the_full_brief_text(self):
        _t, brief = self.stale_brief()
        self.live = [sess(SID_A, self.mid, None, provider="codex")]
        todo_server._wake_project(self.root, 1e9)
        text = self.send.call_args[0][1]
        self.assertIn(brief.as_posix(), text)
        self.assertIn("Manager brief", text)
        self.assertIn(T.MANAGER_BRIEF_ANSWER, text)
        self.assertNotIn("write nothing", text.lower())

    def test_a_claude_manager_gets_the_check_as_a_skill_call(self):
        todo_server._wake_project(self.root, 1e9)
        todo_server._wake_project(self.root, 1e9 + C.INTERVAL)
        self.assertEqual(self.send.call_args[0][1], "/manager-check")

    def test_a_session_with_no_check_skill_gets_the_full_check_text(self):
        for s in (sess(SID_A, self.mid, None, provider="codex"),
                  sess(SID_A, self.mid, None, cwd=str(self.tmp / "no project"))):
            self.assertTrue(C.wake_text(s).startswith("Manager check: run python"))

    def test_a_manager_waiting_for_the_owner_gets_no_new_brief(self):
        t, _brief = self.stale_brief()
        self.live = [sess(SID_A, self.mid, "input", dialog=True)]
        todo_server._wake_project(self.root, 1e9)
        self.assertFalse(self.send.called)
        self.assertTrue(T.manager_brief_stale(t))

    def test_the_view_says_when_the_check_is_late_and_why(self):
        self.live = [sess(SID_A, self.mid, "input", dialog=True)]
        todo_server._wake_project(self.root, 1e9)
        late = 1e9 + C.INTERVAL + C.LATE_MARGIN + 1
        view = todo_server.manager_check_view(late)
        self.assertTrue(view["late"])
        self.assertEqual(view["why"], "the manager waits for your answer")
        C.update_state(checked=late - 5)
        self.assertFalse(todo_server.manager_check_view(late)["late"])


class TypedInput(unittest.TestCase):
    def test_an_empty_box_holds_nothing(self):
        self.assertEqual(T.typed_input(box()), "")

    def test_typed_text_is_read_over_every_line_of_the_box(self):
        self.assertEqual(T.typed_input(box("first\nsecond")), "first\n  second")

    def test_a_suggested_prompt_is_no_typed_text(self):
        self.assertEqual(T.typed_input(box("try this", **SUGGESTION)), "")

    def test_a_faint_suggestion_with_no_dim_is_known_by_the_cursor_at_its_start(self):
        self.assertEqual(T.typed_input(box("granted, run it", cursor=[2, 2])), "")

    def test_typed_text_with_the_cursor_after_it_is_typed(self):
        self.assertEqual(T.typed_input(box("#")), "#")

    def test_the_empty_prompt_line_with_no_space_is_an_empty_box(self):
        scr = box()
        scr["lines"][2] = "❯"
        self.assertEqual(T.typed_input(scr), "")

    def test_a_screen_with_no_box_gives_none(self):
        self.assertIsNone(T.typed_input({"lines": ["output", "more"]}))


class Restart(unittest.TestCase):
    def test_a_code_change_restarts_only_once_it_holds_for_a_tick(self):
        self.assertEqual(todo_server.restart_due(None, 5, start=5), (False, None))
        self.assertEqual(todo_server.restart_due(None, 6, start=5), (False, 6))
        self.assertEqual(todo_server.restart_due(6, 7, start=5), (False, 7))
        self.assertEqual(todo_server.restart_due(7, 7, start=5), (True, 7))

    def args(self, **kw):
        a = dict(port=4321, project="Sample Game", page="board", no_manager=False,
                 no_open=False)
        a.update(kw)
        return argparse.Namespace(**a)

    def test_the_supervisor_starts_the_server_again_on_the_same_port_and_keys(self):
        codes = [todo_server.RESTART_EXIT, 0]
        calls = []

        def call(argv, env):
            calls.append((argv, env))
            return codes.pop(0)
        rc = todo_server.supervise(self.args(), call=call, stamp=lambda: 1, sleep=None)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)
        first, again = calls[0][0], calls[1][0]
        self.assertNotIn("--no-open", first)
        self.assertIn("--no-open", again)
        self.assertEqual(first[first.index("--port") + 1], "4321")
        self.assertEqual(again[again.index("--port") + 1], "4321")
        env = calls[1][1]
        self.assertEqual(env[todo_server.TOKEN_ENV], todo_server.TOKEN)
        self.assertEqual(env[todo_server.CHILD_ENV], "1")

    def test_a_failed_restart_waits_for_the_next_code_change(self):
        codes = [todo_server.RESTART_EXIT, 1, 0]
        stamps = [1, 2, 2, 2, 3, 3]
        slept = []
        rc = todo_server.supervise(self.args(), call=lambda argv, env: codes.pop(0),
                                   stamp=lambda: stamps.pop(0), sleep=slept.append)
        self.assertEqual(rc, 0)
        self.assertEqual(codes, [])
        self.assertEqual(len(slept), 2)

    def test_a_server_that_fails_at_the_first_start_ends_the_board(self):
        rc = todo_server.supervise(self.args(), call=lambda argv, env: 2, stamp=lambda: 1,
                                   sleep=None)
        self.assertEqual(rc, 2)


class Brief(FixtureCase):
    def test_the_manager_brief_starts_no_watcher_and_no_cron_job(self):
        root = self.make("Sample Game")
        mid = self.new_ticket(root, "the manager", "--repeat", "manager", "--permits",
                              "projects")
        tok = project.use(root)
        try:
            text = T.build_brief(T.find(T.load_all(), mid), "work")
        finally:
            project.reset(tok)
        self.assertNotIn("check --watch", text)
        self.assertIn("Start no watcher: the engine starts it.", text)
        self.assertIn("make no `CronCreate` job", text)
        self.assertNotIn("--job-created", text)
        self.assertNotIn("{octopus}", text)


if __name__ == "__main__":
    unittest.main()
