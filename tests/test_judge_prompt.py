"""The judge's request, and the question the board quotes.   python -m unittest discover tests

The live case runs `claude -p` and costs a model call: it runs only with OCTOPUS_LIVE_JUDGE=1.
"""

import os
import shutil
import unittest
from types import SimpleNamespace
from unittest import mock

from support import FixtureCase, project

import session_state  # noqa: E402
import todo  # noqa: E402

SID = "1f1e1d1c-1b1a-4918-8716-151413121110"

# The kind of message that the manager T0058 was judged `input` on: a status report that
# lists the questions OTHER sessions ask the owner, and asks nothing itself.
MANAGER_REPORT = """**Working**
- Octopus AI T0074 "Session state detection mixes up waiting for agents with waiting for the owner" - work, implementing
- Sample Game T0033 "Enemies: wolves" - work, implementing

**Waiting for you**
- Sample Game T0034 "Enemies: bandits" - plan approval
- Sample Game T0035 "Enemies: dragons" - asks: should a dragon burn every village, or only the nearest one?
- Octopus AI T0069 "Board shows stale lanes" - asks: keep the old column order or sort by severity?

**Blocked by you**
- Sample Game T0020 "No enemies exist: wolves, the" - waits for T0033, T0034, T0035

**Landed**
- Octopus AI T0071 "Feature: Board groups collapse" - Verification
- Octopus AI T0072 "Bug: Focus opens the wrong tab" - Verification"""

# A real question the judge must keep: it stands above the options, not on the last line.
PICK = """Two ways to fix the stale lanes on the board:

1. Re-read the tree on every poll.
2. Watch the directory with ReadDirectoryChangesW.

Which one do you want? I will not start until you pick one.

Both keep the board API unchanged."""


class Prompt(unittest.TestCase):
    def text(self):
        return session_state.JUDGE_PROMPT % {"project": "P", "ticket": "", "message": "m",
                                             "lead": session_state.JUDGE_LEAD,
                                             "what": session_state.JUDGE_WHAT,
                                             "recap": "", "others": ""}

    def test_input_is_only_what_the_session_itself_asks(self):
        p = self.text()
        self.assertIn("the session ITSELF asks the owner a question", p)
        self.assertIn("Never input, whatever words it uses", p)
        self.assertIn("a report: a summary, a status table", p)
        self.assertIn('"Waiting for you" or "Blocked by you"', p)

    def test_every_verdict_explains_itself_and_quotes_its_proof(self):
        # The owner (2026-10-18): the judge quotes the open question, or the line that said
        # the work was done.
        p = self.text()
        self.assertIn("why = one sentence of at most 20 words that explains the verdict", p)
        self.assertIn("For input, the question the session asks the owner", p)
        self.assertIn("for finished, the line that says the work was done", p)
        self.assertIn("never from these instructions", p)
        self.assertIn('"quote": "<see below>"', p)

    def test_a_recurring_task_is_working_never_finished(self):
        # The owner (2026-10-18): a recap that says it runs a recurring task, judged
        # finished, is the judge's fault.
        self.assertIn("runs a recurring or scheduled task", self.text())
        self.assertIn("is working, never finished", self.text())

    def test_unsure_is_an_answer(self):
        # The owner (2026-10-18): an unsure judge sends /recap and judges the recap.
        p = self.text()
        self.assertIn('"input" | "finished" | "working" | "unsure"', p)
        self.assertIn("unsure = the text does not show what it waits for", p)

    def test_a_stale_turn_names_its_last_action(self):
        lead = session_state.JUDGE_LEAD_STALE % {"minutes": 6, "doing": "Bash: tests"}
        self.assertIn("has not ended its turn", lead)
        self.assertIn("nothing for 6 min; its last action: Bash: tests", lead)


class Verdict(unittest.TestCase):
    """A verdict proves itself with a quote from the message or the recap."""

    MESSAGE = "**Done.** T0058 status report sent.\n\nWhich one do you want: A or B?"
    RECAP = "I'm running the 20-minute status reports across all projects."

    def parse(self, answer):
        return session_state.parse_verdict(answer, (self.MESSAGE, self.RECAP))

    def test_the_why_carries_the_explanation_and_the_quote(self):
        self.assertEqual(
            self.parse('{"state": "input", "why": "It needs a pick.", '
                       '"quote": "Which one do you want: A or B?"}'),
            ("input", 'It needs a pick: "Which one do you want: A or B?"'))

    def test_a_quote_from_the_recap_counts(self):
        state, why = self.parse('{"state": "working", "why": "runs a recurring report", '
                                '"quote": "running the 20-minute status reports"}')
        self.assertEqual(state, "working")
        self.assertIn("20-minute status reports", why)

    def test_markdown_case_and_an_ellipsis_do_not_matter(self):
        self.assertIsNotNone(self.parse('{"state": "finished", "why": "w", '
                                        '"quote": "done. T0058 status ... sent"}'))

    def test_a_quote_that_stands_nowhere_or_none_is_no_verdict(self):
        self.assertIsNone(self.parse('{"state": "finished", "why": "w", '
                                     '"quote": "Completed management cycle"}'))
        self.assertIsNone(self.parse('{"state": "finished", "why": "Completed"}'))
        # An unsure verdict proves nothing, and needs no quote.
        self.assertEqual(self.parse('{"state": "unsure", "why": "it says nothing"}'),
                         ("unsure", "it says nothing"))
        self.assertEqual(self.parse('{"state": "unsure", "why": "w", "quote": "made up"}'),
                         ("unsure", "w"))
        # Without sources nothing is checked.
        self.assertEqual(session_state.parse_verdict('{"state": "finished", "why": "w"}'),
                         ("finished", "w"))

    def test_a_relayed_question_is_no_input(self):
        # Haiku, 2026-10-18, on the manager's report: input, quoting another ticket's ask.
        sources = (MANAGER_REPORT, "", "")
        for quote in ('Octopus AI T0069 \\"Board shows stale lanes\\" - asks: keep the old '
                      'column order or sort by severity?',
                      'Sample Game T0034 \\"Enemies: bandits\\" - plan approval'):
            verdict, fault = session_state.check_verdict(
                '{"state": "input", "why": "w", "quote": "%s"}' % quote, sources)
            self.assertIsNone(verdict)
            self.assertIn("only relays", fault)
        # The same line is a fine proof that it waits for those sessions.
        self.assertIsNotNone(session_state.parse_verdict(
            '{"state": "working", "why": "w", "quote": "T0034 \\"Enemies: bandits\\" - '
            'plan approval"}', sources))
        # The session's own ticket is its own question.
        self.assertIsNotNone(session_state.parse_verdict(
            '{"state": "input", "why": "w", "quote": "T0069 asks: keep the old column order '
            'or sort by severity?"}', ("T0069 asks: keep the old column order or sort by "
                                       "severity?", "", ""), own=["T0069"]))


class Recap(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Recap")
        self.tok = project.use(self.root)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def test_the_last_recap_of_the_transcript(self):
        import json
        path = self.root / "t.jsonl"
        rows = [{"type": "system", "subtype": "away_summary", "content": "old one",
                 "timestamp": "2026-10-17T13:00:00Z"},
                {"type": "assistant", "message": {"content": "x"}},
                {"type": "system", "subtype": "away_summary",
                 "content": "I'm running the status reports. (disable recaps in /config)",
                 "timestamp": "2026-10-17T13:49:42Z"}]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.assertEqual(session_state.last_recap(SID, path),
                         ("I'm running the status reports.", "2026-10-17T13:49:42Z"))
        self.assertEqual(session_state.last_recap(SID, self.root / "none.jsonl"), ("", ""))


class Question(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Question")
        self.tok = project.use(self.root)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def test_the_last_sentence_of_the_lowest_question(self):
        self.assertEqual(session_state.question_of(
            "I read the parser. **Shall I also fix the lexer?**"), "Shall I also fix the lexer?")
        # A question in the middle of a line is the judge's to quote.
        self.assertEqual(session_state.question_of(PICK), "")
        self.assertEqual(session_state.question_of("Which lane?\n\nThanks.\nDone."), "Which lane?")
        self.assertEqual(session_state.question_of("No question here."), "")
        self.assertEqual(len(session_state.question_of("x" * 500 + "?")),
                         session_state.QUESTION_CHARS)

    def test_the_marker_keeps_the_question(self):
        session_state.mark_stop(SID, 42, "I read it.\nShall I fix the lexer too?")
        self.assertTrue(session_state.idle_asks(SID, 42))
        self.assertEqual(session_state.idle_question(SID, 42), "Shall I fix the lexer too?")
        session_state.mark_stop(SID, 42, "Which lane?\nI wait.\nThe tree is clean.")
        self.assertTrue(session_state.idle_unsure(SID, 42))
        self.assertEqual(session_state.idle_question(SID, 42), "Which lane?")
        session_state.mark_stop(SID, 42, "WORK IS FINISHED")
        self.assertEqual(session_state.idle_question(SID, 42), "")

    def test_the_board_quotes_the_question(self):
        s = {"id": "T0001", "mode": "work", "ended": True, "claude": 1, "hooked": True,
             "asks": True, "question": "Shall I fix the lexer too?"}
        self.assertEqual(todo.wait_state(s, None), ("input", "asked: Shall I fix the lexer too?"))
        # A ? above the last line is the judge's to read.
        s.update(asks=False, unsure=True, question="Which lane?", judging=True)
        self.assertEqual(todo.wait_state(s, None),
                         ("idle", "asked nothing; the judge reads its last message"))
        s.update(judge="input", judge_why='needs a lane: "Which lane?"')
        self.assertEqual(todo.wait_state(s, None),
                         ("input", 'judge: needs a lane: "Which lane?"'))


class WhichQuestion(FixtureCase):
    """The owner (2026-10-18): every `input` names its question, and a report of an earlier
    turn is no question."""

    def setUp(self):
        super().setUp()
        self.root = self.make("Which")
        self.tok = project.use(self.root)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def base(self, **kw):
        s = {"id": "T0001", "mode": "work", "running": False, "claude": 1, "hooked": True}
        s.update(kw)
        return s

    def test_a_dialog_keeps_its_question(self):
        ask = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [
            {"question": "Which lane?"}, {"question": "Which severity?"}]}}
        self.assertEqual(session_state.dialog_text(ask), "Which lane? / Which severity?")
        self.assertEqual(session_state.dialog_text({"tool_name": "ExitPlanMode"}),
                         "approve the plan")
        self.assertEqual(session_state.dialog_text(
            {"message": "Claude needs your permission to use Bash"}),
            "Claude needs your permission to use Bash")
        self.assertIs(session_state.dialog_text({}), True)
        session_state.mark_idle(SID, 42, dialog=session_state.dialog_text(ask))
        self.assertTrue(session_state.idle_dialog(SID, 42))
        q = session_state.idle_question(SID, 42)
        self.assertEqual(todo.wait_state(self.base(dialog=True, asks=True, question=q), None),
                         ("input", "dialog: Which lane? / Which severity?"))

    def test_a_report_names_its_question(self):
        self.assertEqual(todo.wait_state(self.base(reported="input",
                                                   reported_why="Which lane?"), None),
                         ("input", "reported: Which lane?"))
        self.assertEqual(todo.wait_state(self.base(reported="input", question="Go on?"), None),
                         ("input", "reported: Go on?"))
        self.assertEqual(todo.wait_state(self.base(reported="input", judge="input",
                                                   judge_why="Delete or rename?"), None),
                         ("input", "reported: Delete or rename?"))

    def test_report_input_needs_the_question(self):
        tid = self.new_ticket(self.root, "Fix the parser")
        rc, _out, err = self.todo(self.root, "report", tid, "input")
        self.assertEqual(rc, 2)
        self.assertIn("--why", err)

    def test_a_report_of_an_earlier_turn_is_dropped(self):
        import time
        session_state.report(SID, 42, "input", "Which lane?")
        time.sleep(0.05)
        session_state.mark_stop(SID, 42, "Which lane?")          # the turn that asked
        self.assertEqual(session_state.reported(SID, 42), "input")
        time.sleep(0.05)
        # The next turn began without the owner's prompt: a scheduled prompt.
        session_state.mark_stop(SID, 42, "I checked the blocker. Nothing changed.")
        self.assertEqual(session_state.reported(SID, 42), "")
        # A report made in this turn stays, and a dialog marker ends no turn.
        session_state.mark_idle(SID, 42, dialog=True)
        session_state.report(SID, 42, "input", "Which lane?")
        session_state.mark_stop(SID, 42, "Which lane?")
        self.assertEqual(session_state.reported(SID, 42), "input")


class Closes(FixtureCase):
    """The owner (2026-10-18): a planner that finished planning, and a session that said
    WORK IS FINISHED, close their terminal."""

    def setUp(self):
        super().setUp()
        self.root = self.make("Closes")
        self.tid = self.new_ticket(self.root, "Fix the parser")
        self.mgr = self.new_ticket(self.root, "Manager", "--repeat", "manager",
                                   "--permits", "projects")
        self.tok = project.use(self.root)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def move(self, status, lane="InProgress"):
        project.reset(self.tok)
        rc, _o, err = self.todo(self.root, "move", self.tid, lane, "--status", status)
        self.assertEqual(rc, 0, err)
        self.tok = project.use(self.root)

    def plan(self, text="1. Edit core/engine/todo.py.\n2. Run the tests.\n"):
        """Write `## Plan` into the ticket and size it."""
        t = todo.find(todo.load_all(), self.tid)
        body = t.path.read_text(encoding="utf-8").replace("difficulty: unknown",
                                                          "difficulty: small")
        body = body.split("\n\n## Plan\n")[0].rstrip("\n")
        t.path.write_text(body + "\n\n## Plan\n\n" + text, encoding="utf-8")

    def closer(self, tid, mode, text, shell=0):
        with mock.patch.object(todo, "spawn_detached") as spawn:
            started = session_state.close_finished(todo.launch_session_id(tid, mode), text,
                                                   shell)
        return started, spawn.call_args[0][0] if spawn.called else None

    def test_a_planner_closes_once_the_plan_is_on_the_ticket(self):
        self.assertEqual(self.closer(self.tid, "plan", "Planning.")[0], False)
        self.move("ready to implement")
        # The owner (2026-10-20): a plan that cannot be executed is no finished plan.
        self.assertEqual(self.closer(self.tid, "plan", "The plan is on the ticket.\n"
                                                       "WORK IS FINISHED"), (False, None))
        self.plan("1. Draw the glyph.\n2. Which eight row bytes make it?\n")
        self.assertEqual(self.closer(self.tid, "plan", "The plan is on the ticket.\n"
                                                       "WORK IS FINISHED"), (False, None))
        self.plan()
        started, argv = self.closer(self.tid, "plan", "The plan is on the ticket.")
        self.assertTrue(started)
        self.assertEqual(argv[-5:], ["close", self.tid, "--mode", "plan", "--when-finished"])
        started, argv = self.closer(self.tid, "plan", "Planned.\nWORK IS FINISHED")
        self.assertEqual(argv[-6:], ["close", self.tid, "--mode", "plan", "--when-finished",
                                     "--now"])

    def test_a_believable_finished_line_closes_at_once_but_never_a_manager(self):
        # T0085: the ticket agrees with the line, so the closer does not wait for the state.
        self.assertFalse(self.closer(self.tid, "work", "Changed it.")[0])
        self.move("investigating")
        started, argv = self.closer(self.tid, "verify", "Measured.\nWORK IS FINISHED")
        self.assertEqual(argv[-6:], ["close", self.tid, "--mode", "verify", "--when-finished",
                                     "--now"])
        self.move("waiting", "Verification")
        for mode in ("work", "review"):
            started, argv = self.closer(self.tid, mode, "Changed it.\nWORK IS FINISHED")
            self.assertTrue(started, mode)
            self.assertEqual(argv[-6:], ["close", self.tid, "--mode", mode, "--when-finished",
                                         "--now"])
        self.assertFalse(self.closer(self.mgr, "work", "WORK IS FINISHED")[0])

    def test_a_finished_line_over_undone_work_closes_nothing(self):
        # A ticket left in InProgress `waiting` for the owner's glyph, then the line.
        self.move("waiting")
        text = ("To continue, launch work %s with --note holding the eight row bytes of the "
                "glyph.\nWORK IS FINISHED" % self.tid)
        self.assertEqual(self.closer(self.tid, "work", text), (False, None))
        self.move("investigating")
        self.assertEqual(self.closer(self.tid, "review", "WORK IS FINISHED"), (False, None))
        sid = todo.launch_session_id(self.tid, "work")
        session_state.mark_stop(sid, 42, text)
        s = {"id": self.tid, "mode": "work", "ended": True, "claude": 1, "hooked": True,
             "says_finished": session_state.idle_finished(sid, 42)}
        self.move("waiting")
        wait, why = todo.wait_state(s, todo.find(todo.load_all(), self.tid))
        self.assertEqual(wait, "input")
        self.assertTrue(why.startswith("said WORK IS FINISHED with the work undone"), why)

    def test_a_question_reported_in_the_turn_keeps_the_terminal_open(self):
        self.move("waiting", "Verification")
        sid = todo.launch_session_id(self.tid, "work")
        session_state.mark_stop(sid, 42, "Changed it.")
        session_state.report(sid, 42, "input", "Which glyph?")
        self.assertEqual(self.closer(self.tid, "work", "Which glyph?\nWORK IS FINISHED", 42),
                         (False, None))

    def test_an_owner_conversation_closes_only_by_its_own_finished_line(self):
        # The manager hotfix (2026-10-20): a manager's ticket was killed while the owner talked.
        self.move("waiting", "Verification")
        sid = todo.launch_session_id(self.tid, "work")
        session_state.mark_talk(sid, 42, "So, were you closed?")
        self.assertTrue(session_state.owner_talks(sid, 42))
        self.assertEqual(self.closer(self.tid, "work", "No: %s is in Verification." % self.tid,
                                     42), (False, None))
        # The judge's `finished` starts no closer.
        session_state.mark_idle(sid, 42)
        session_state._last_path(sid, 42).write_text("It sits in Verification.",
                                                     encoding="utf-8")
        import todo_server
        answer = ('{"state": "finished", "why": "done", "quote": "It sits in Verification."}')
        with mock.patch.dict(os.environ), \
                mock.patch.object(todo_server, "_ask_provider", return_value=answer), \
                mock.patch.object(todo, "spawn_detached") as spawn:
            self.assertEqual(session_state.judge(sid, 42), "finished")
        spawn.assert_not_called()
        # The board leaves it open.
        live = [{"key": "%s/work" % self.tid, "id": self.tid, "mode": "work", "session": sid,
                 "shell": 42, "running": False, "wait": "finished"}]
        self.assertEqual(todo.autoclose_sessions(live, todo.load_all()), [])
        # Its own believable line closes it at once.
        started, argv = self.closer(self.tid, "work", "Done.\nWORK IS FINISHED", 42)
        self.assertEqual(argv[-1], "--now")
        # The launch prompt clears the mark.
        session_state.mark_talk(sid, 42, "Work ticket %s. Read TODO/.briefs/%s.md first"
                                % (self.tid, self.tid))
        self.assertFalse(session_state.owner_talks(sid, 42))

    def judged(self, tid, mode, state):
        """The judge's verdict `state` on a launched session; the closer argv it started."""
        import todo_server
        sid = todo.launch_session_id(tid, mode)
        session_state.mark_idle(sid, 42)
        session_state._last_path(sid, 42).write_text("Changed it.", encoding="utf-8")
        answer = '{"state": "%s", "why": "it says so", "quote": "Changed it."}' % state
        with mock.patch.dict(os.environ), \
                mock.patch.object(todo_server, "_ask_provider", return_value=answer), \
                mock.patch.object(todo, "spawn_detached") as spawn:
            self.assertEqual(session_state.judge(sid, 42), state)
        return spawn.call_args[0][0] if spawn.called else None

    def test_the_judges_finished_closes_the_session(self):
        argv = self.judged(self.tid, "verify", "finished")
        self.assertEqual(argv[-5:], ["close", self.tid, "--mode", "verify", "--when-finished"])
        self.assertIsNone(self.judged(self.tid, "review", "input"))
        self.assertIsNone(self.judged(self.mgr, "work", "finished"))

    def test_a_planned_session_is_decided_by_its_last_message_not_its_ticket(self):
        # The owner (2026-10-18): the ticket's lane decides no state; the judge reads it.
        t = SimpleNamespace(id="T0001", state=todo.lane("active"),
                            status=todo.S("ready to implement"), blocked_by=[], repeat="",
                            body="## Plan\n\n1. Edit it.\n", difficulty="small")
        s = {"id": "T0001", "mode": "plan", "ended": True, "claude": 1, "hooked": True}
        self.assertEqual(todo.wait_state(dict(s, says_finished=True), t)[0], "finished")
        self.assertEqual(todo.wait_state(dict(s, judge="finished"), t)[0], "finished")
        self.assertEqual(todo.wait_state(dict(s, judge="working"), t)[0], "subagent")
        self.assertEqual(todo.wait_state(dict(s, jobs=["0badcafe"]), t)[0], "subagent")
        self.assertEqual(todo.wait_state(dict(s, asks=True), t)[0], "input")
        self.assertEqual(todo.wait_state(dict(s, judge="input"), t)[0], "input")
        self.assertEqual(todo.wait_state(dict(s, unsure=True, judging=True), t)[0], "idle")
        self.assertEqual(todo.wait_state(s, t)[0], "idle")


@unittest.skipUnless(os.environ.get("OCTOPUS_LIVE_JUDGE") and shutil.which("claude"),
                     "set OCTOPUS_LIVE_JUDGE=1, with claude on the PATH, to run the judge live")
class Live(FixtureCase):
    """The judge itself, `claude -p` on the project's Haiku, on the manager's report."""

    def setUp(self):
        super().setUp()
        self.root = self.make("Live")
        self.tok = project.use(self.root)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def judge(self, text):
        self.assertTrue(session_state.mark_stop(SID, 42, text))
        session_state._last_path(SID, 42).write_text(text, encoding="utf-8")
        state = session_state.judge(SID, 42)
        return state, session_state.judged(SID, 42)[1]

    def test_a_report_of_other_sessions_questions_is_not_input(self):
        state, why = self.judge(MANAGER_REPORT)
        self.assertIn(state, ("working", "finished"), why)

    def test_a_question_above_the_options_is_input(self):
        state, why = self.judge(PICK)
        self.assertEqual(state, "input", why)
        self.assertTrue(why)


if __name__ == "__main__":
    unittest.main()
