"""`WORK IS FINISHED` is never lost when the work is finished, and never demanded or enforced
where the work is not finished.   python -m unittest tests.test_work_finished

The briefs are built with no launch note unless a test names one: the rules alone must force
the behavior. No test starts a process: the closer's spawn is replaced, and the session dicts
and the tickets are built by hand.
"""

import re
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from support import OCTOPUS, FixtureCase, project

import reply_check  # noqa: E402
import session_state  # noqa: E402
import todo  # noqa: E402

LINE = "WORK IS FINISHED"
AGENT = {"id": "a563997732141d3f6", "type": "subagent", "description": "the build"}
STOPPED_NOTE = "Ask the owner nothing: the owner cannot answer a question dialog."


def session(mode="work", says_finished=False, reported="", why="", asks=False, jobs=(),
            tasks=(), provider="claude", running=False):
    """A session entry as `live_sessions` gives it: its turn ended, unless it is `running`."""
    s = {"id": "T0001", "mode": mode, "claude": 1234, "ended": not running, "stale": False,
         "asks": asks, "reported": reported, "reported_why": why, "error": "", "hooked": True,
         "starting": False, "says_finished": says_finished, "dialog": False,
         "jobs": list(jobs), "tasks": list(tasks), "unsure": False, "judge": "",
         "judge_why": "", "judge_from": "", "recap": "", "waits_for": [], "judging": False,
         "judge_dead": False, "doing": "", "updated": time.time()}
    if provider != "claude":
        s["provider"] = provider
    return s


def blocks(text):
    """The paragraphs and the list items of a brief."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        out.extend(b for b in re.split(r"\n(?=\s*(?:-|\d+\.) )", para) if b.strip())
    return out


class WorkFinished(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Finish", config={"providers": {
            "codex": {"defaultModel": "gpt-6-luna", "defaultEffort": "high"}}})
        self.tid = self.new_ticket(self.root, "Feature: the parser reads a header")
        self.planned = self.new_ticket(self.root, "Feature: the parser reads a footer")
        self.tok = project.use(self.root)
        t = todo.find(todo.load_all(), self.planned)
        t.path.write_text(t.path.read_text(encoding="utf-8").rstrip("\n")
                          + "\n\n## Plan\n\n1. Edit core/engine/todo.py.\n", encoding="utf-8")

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    # -- helpers --------------------------------------------------------------------------

    def ticket_of(self, tid=None):
        return todo.find(todo.load_all(), tid or self.tid)

    def brief(self, mode="work", tid=None, note="", provider="claude"):
        return todo.build_brief(self.ticket_of(tid), mode, note, provider)

    @staticmethod
    def lane_ticket(role, status_role, blocked_by=()):
        return SimpleNamespace(id="T0001", state=todo.lane(role), status=todo.S(status_role),
                               blocked_by=list(blocked_by), repeat="", body="x",
                               difficulty="small")

    def closer(self, text, mode="work", shell=0):
        """Whether the `Stop` hook starts the closer on `text`, and whether at once."""
        with mock.patch.object(todo, "spawn_detached") as spawn:
            started = session_state.close_finished(todo.launch_session_id(self.tid, mode),
                                                   text, shell)
        argv = spawn.call_args[0][0] if spawn.called else []
        return started, bool(argv) and argv[-1] == "--now"

    def move(self, lane, status):
        project.reset(self.tok)
        rc, _o, err = self.todo(self.root, "move", self.tid, lane, "--status", status)
        self.assertEqual(rc, 0, err)
        self.tok = project.use(self.root)

    def assertEndsWithLine(self, text, condition):
        close = text.split("### Close this terminal")[1]
        self.assertIn(condition, close)
        self.assertIn("end your last message with `%s`" % LINE, close.lower().replace(
            LINE.lower(), LINE))

    # -- never lost when the work is finished -----------------------------------------------

    def test_01_a_work_session_ends_with_the_line_when_its_work_is_finished(self):
        text = self.brief("work")
        self.assertIn("When the work of this ticket is finished and you have no question for "
                      "the owner,** end\n  your last message with this exact line", text)
        self.assertEndsWithLine(text, "When the work is done, the ticket is in Verification")

    def test_02_a_plan_session_ends_with_the_line_when_the_plan_is_ready(self):
        text = self.brief("plan")
        self.assertEndsWithLine(text, "the ticket is `ready to implement`")
        self.assertIn("When the work of this ticket is finished", text)

    def test_03_a_review_session_ends_with_the_line_when_its_verdict_is_given(self):
        self.assertEndsWithLine(self.brief("review"), "When the verdict is done")

    def test_04_a_verify_session_ends_with_the_line_when_its_measurement_is_made(self):
        text = self.brief("verify")
        self.assertEndsWithLine(text, "When the measurement is done")
        self.assertIn("when the measurement is made, that message ends with\n  `%s`" % LINE,
                      text)

    def test_05_a_work_session_after_a_plan_still_ends_with_the_line(self):
        text = self.brief("work", self.planned)
        self.assertIn("A plan already exists on this ticket", text)
        self.assertEndsWithLine(text, "When the work is done, the ticket is in Verification")
        self.assertEqual(todo.wait_state(session(says_finished=True),
                                         self.lane_ticket("verify", "waiting")),
                         ("finished", "said %s" % LINE))

    def test_06_no_brief_bans_the_line_after_a_message_of_the_owner(self):
        for mode in todo.MODES:
            text = self.brief(mode)
            self.assertNotIn("in an answer to a message the owner wrote", text, mode)
            self.assertIn("**Never leave out `%s` when the work is finished.** This holds in "
                          "every\n  launch mode, also in a turn after the owner's answer, after "
                          "a message of the owner" % LINE, text, mode)

    def test_07_the_line_after_an_answered_question_finishes_the_session(self):
        sid = todo.launch_session_id(self.tid, "work")
        session_state.mark_stop(sid, 42, "Which asset?")
        session_state.report(sid, 42, "input", "Which asset?")
        self.assertEqual(self.closer("Added the asset.\n" + LINE, shell=42), (True, True))
        self.assertEqual(todo.wait_state(session(reported="input", why="Which asset?",
                                                 says_finished=True),
                                         self.lane_ticket("verify", "waiting")),
                         ("finished", "said %s" % LINE))

    def test_08_the_line_after_a_resume_closes_the_session(self):
        sid = todo.launch_session_id(self.tid, "work")
        session_state.mark_talk(sid, 42, "Go on with the ticket.")
        self.assertTrue(session_state.owner_talks(sid, 42))
        self.assertEqual(self.closer("Changed core/x.py.\n" + LINE, shell=42), (True, True))
        self.assertIn("after\n  a resume of this session", self.brief("work"))

    def test_09_the_line_after_a_background_job_finishes_the_session(self):
        for t in (self.lane_ticket("verify", "waiting"), self.lane_ticket("closed", "done")):
            self.assertEqual(todo.wait_state(session(says_finished=True, tasks=[AGENT],
                                                     jobs=["e4810603"]), t),
                             ("finished", "said %s" % LINE))
        self.assertIn("after the event or the background job you waited for", self.brief())

    def test_10_the_engine_never_ignores_the_line(self):
        for mode in todo.MODES:
            for t in (self.lane_ticket("start", "pipeline"),
                      self.lane_ticket("active", "implementing"),
                      self.lane_ticket("active", "waiting"),
                      self.lane_ticket("blocked", "blocked by other task", ["T0002"])):
                self.assertEqual(todo.wait_state(session(mode, says_finished=True), t),
                                 ("finished", "said %s" % LINE), (mode, t.state))
        self.move("InProgress", "waiting")
        self.assertEqual(self.closer("Changed it.\n" + LINE), (True, True))

    def test_11_a_codex_session_reports_finished_only_when_its_work_is_finished(self):
        text = self.brief("work", provider="codex")
        self.assertIn("**When the work of this ticket is finished,** run `", text)
        self.assertIn("Never run it on stopped work: report `input` or `event`.", text)
        self.assertEqual(todo.wait_state(session(reported="finished", provider="codex"),
                                         self.lane_ticket("verify", "waiting")),
                         ("finished", "reported finished"))

    def test_12_the_reply_check_never_fails_the_line(self):
        self.assertTrue(reply_check.protected(LINE))
        self.assertTrue(reply_check.protected("**%s**" % LINE))
        self.assertFalse(reply_check.worth_checking(LINE))
        self.assertEqual(session_state.says_finished("Done.\n`%s`" % LINE), LINE)

    # -- never demanded or enforced where the work is not finished --------------------------

    def test_13_a_missing_asset_makes_the_session_ask_the_owner_and_wait(self):
        for mode in todo.MODES:
            text = self.brief(mode)
            self.assertIn("**A session that cannot do its work never writes `%s`.**" % LINE,
                          text, mode)
            self.assertIn("**You cannot find what the work needs** (an asset, a file, a value, "
                          "a name): ask the\n    owner where it is or for it, with `report ... "
                          "input` and the question dialog, and wait\n    for the answer.",
                          text, mode)
        for rule in (OCTOPUS / "core/rules/todo-tracking.md",
                     OCTOPUS / ".agents/rules/octopus/core/todo-tracking.md"):
            body = " ".join(rule.read_text(encoding="utf-8").split())
            self.assertIn("A session that cannot find what its work needs (an asset, a file, a "
                          "value) asks the owner with `report <id> input` and the question "
                          "dialog, and waits for the answer", body, rule)

    def test_14_a_note_that_forbids_questions_never_permits_the_line_on_stopped_work(self):
        for note in ("", STOPPED_NOTE):
            text = self.brief("work", note=note)
            self.assertIn("**A launch note never makes stopped work finished.** A note that "
                          "tells you to ask the\n  owner nothing", text)
            self.assertIn("end your last\n  message with the question as its last line", text)
        self.assertEqual(todo.wait_state(session(asks=True),
                                         self.lane_ticket("active", "waiting"))[0], "input")

    def test_15_an_ungranted_permit_reports_an_event_and_never_the_line(self):
        text = self.brief("work")
        section = text.split("**Not granted:")[1].split("\n## ")[0]
        self.assertIn("report %s event --why \"the owner grants the permit" % self.tid, section)
        self.assertIn("End the\nturn without `%s`" % LINE, section)
        self.assertNotIn("and stop.", section)

    def test_16_an_outside_action_waits_as_an_event_and_never_finishes(self):
        self.assertIn("An ungranted permit or an action outside the project, such as an editor "
                      "restart:\n    `report ... event`.", self.brief("work"))
        for t in (self.lane_ticket("active", "waiting"),
                  self.lane_ticket("active", "implementing"), None):
            self.assertEqual(todo.wait_state(session(reported="event",
                                                     why="the editor restart"), t),
                             ("event", "waits for the editor restart"))
        self.move("InProgress", "waiting")
        self.assertEqual(self.closer("I wait for the editor restart."), (False, False))

    def test_17_a_blocker_keeps_the_session_open_and_never_finishes(self):
        text = self.brief("work")
        self.assertIn("Another ticket: the steps under **When another ticket blocks this one**.",
                      text)
        self.assertIn("End the turn and wait. Do not exit the session", text)
        t = self.lane_ticket("blocked", "blocked by other task", ["T0002"])
        self.assertEqual(todo.wait_state(session(jobs=["e4810603"]), t)[0], "blocker")

    def test_18_an_open_question_waits_for_input_and_never_finishes(self):
        text = self.brief("work")
        self.assertIn("**Work is never finished while a question is open.**", text)
        self.assertIn("while a question to the owner is open", text)
        t = self.lane_ticket("active", "implementing")
        self.assertEqual(todo.wait_state(session(asks=True), t)[0], "input")
        self.assertEqual(todo.wait_state(session(reported="input", why="Where is the asset?"),
                                         t), ("input", "reported: Where is the asset?"))

    def test_19_neither_a_plan_nor_the_judge_turns_stopped_work_into_finished(self):
        plan = self.brief("plan")
        self.assertIn("**A plan never tells the worker to write `%s` on a path where the work "
                      "stops.**" % LINE, plan)
        self.assertIn("for a missing asset,\nfile or value, to ask the owner", plan)
        work = self.brief("work", self.planned)
        self.assertIn("A step of the plan that tells you to write `%s` where the work stops "
                      "does not\nhold" % LINE, work)
        self.assertIn("when you cannot find what a step needs, ask the owner and wait for the "
                      "answer.", work)
        judge = " ".join(session_state.JUDGE_PROMPT.replace("\\\n", "").split())
        self.assertIn("finished = the work of its ticket is done", judge)
        self.assertIn("A session that says its work stopped (it cannot find what the work "
                      "needs, it lacks a permit, it waits for an outside action, another "
                      "ticket or the owner) is never finished", judge)
        self.assertIn("gives what it cannot find (an asset, a file, a value)", judge)

    def test_20_no_instruction_demands_the_line_without_finished_work(self):
        order = re.compile(r"\bend(?:s)?\b[^.]{0,40}\b(?:message|with)\b", re.I)
        condition = re.compile(r"finished|done|ready to implement|measurement is made|never|"
                               r"without|only when", re.I)
        for mode in todo.MODES:
            for tid in (self.tid, self.planned):
                for b in blocks(self.brief(mode, tid)):
                    if LINE in b and order.search(b):
                        self.assertRegex(b.replace(LINE, ""), condition, (mode, tid, b))
        for mode, asks in reply_check.LAUNCH_ASKS.items():
            self.assertRegex(asks, r"only when the \w+ is (?:finished|given|made)", mode)
            self.assertIn("no finished line", asks, mode)
        self.move("InProgress", "waiting")
        self.assertEqual(self.closer("No Milo asset exists. Where is it?"), (False, False))


if __name__ == "__main__":
    unittest.main()
