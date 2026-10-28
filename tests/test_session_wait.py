"""What a session at its prompt waits for.   python -m unittest discover tests

No test starts a process: the session dicts and the tickets are built by hand.
"""

import copy
import json
import os
import time
import unittest
from unittest import mock
from types import SimpleNamespace

from support import FixtureCase, project

import session_state  # noqa: E402
import todo  # noqa: E402

SID = "0f0e0d0c-0b0a-4908-8706-050403020100"


class Asks(unittest.TestCase):
    def test_a_question_on_the_last_line(self):
        self.assertTrue(session_state.asks("I read the code.\nShall I also fix the parser?"))

    def test_a_question_two_lines_above_a_sign_off(self):
        self.assertTrue(session_state.asks("Which lane should it go to?\n\nThanks.\nDone."))

    def test_a_question_mark_inside_code_only(self):
        self.assertFalse(session_state.asks("I changed `x = a ? b : c` in the parser.\n"
                                            "The ticket is in Verification."))

    def test_an_old_question_above_the_last_three_lines(self):
        self.assertFalse(session_state.asks("Why?\none\ntwo\nthree"))

    def test_an_empty_text(self):
        self.assertFalse(session_state.asks(""))
        self.assertFalse(session_state.asks(None))

    def test_trailing_markdown_and_brackets(self):
        self.assertTrue(session_state.asks("**Shall I go on?**"))
        self.assertTrue(session_state.asks("(or should it wait?)"))
        self.assertTrue(session_state.asks('He asked "why?"  '))


class AsksLast(unittest.TestCase):
    def test_only_the_last_line_counts(self):
        self.assertTrue(session_state.asks_last("I read the code.\n**Shall I fix it?**"))
        self.assertFalse(session_state.asks_last("Which lane?\n\nThanks."))
        self.assertFalse(session_state.asks_last(""))


class SaysFinished(unittest.TestCase):
    def test_the_line_alone_at_the_end(self):
        self.assertEqual(session_state.says_finished("It holds.\n\nWORK IS FINISHED\n"),
                         "WORK IS FINISHED")
        self.assertEqual(session_state.says_finished("**WORK IS FINISHED**"), "WORK IS FINISHED")
        self.assertFalse(session_state.says_finished("WORK IS FINISHED\nOne more thing."))

    def test_the_line_of_an_older_brief_still_reads(self):
        self.assertTrue(session_state.says_finished("The ticket holds.\n\nINVESTIGATION FINISHED\n"))

    def test_the_line_in_markdown(self):
        self.assertTrue(session_state.says_finished("Done.\n`INVESTIGATION FINISHED`"))
        self.assertTrue(session_state.says_finished("Done.\n**INVESTIGATION FINISHED**"))

    def test_the_line_not_at_the_end(self):
        self.assertFalse(session_state.says_finished("INVESTIGATION FINISHED\nOne more thing."))

    def test_the_line_inside_a_sentence(self):
        self.assertFalse(session_state.says_finished("I will write INVESTIGATION FINISHED later"))
        self.assertFalse(session_state.says_finished("investigation finished"))

    def test_an_empty_text(self):
        self.assertFalse(session_state.says_finished(""))
        self.assertFalse(session_state.says_finished(None))


class SaysDone(FixtureCase):
    def setUp(self):
        super().setUp()
        self.tok = project.use(self.make("Done"))

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def test_the_ticket_is_in_verification_or_done(self):
        for text in ("Changed core/x.py.\nT0069 is in Verification.", "**T0069 is done**",
                     "Moved T0069 to Verification", "T0069: Verification / waiting",
                     "T0069 is in verification", "`T0069` IS IN VERIFICATION",
                     "T0069 is now in Done.\nFiles: a.py\nb.py"):
            self.assertTrue(session_state.says_done(text), text)
        self.assertEqual(session_state.says_done("x\nT0069 is in Verification."),
                         "T0069 is in Verification")

    def test_no_statement(self):
        for text in ("T0069 is in In Progress.", "T0069 will go to Verification?",
                     "T0069 is in Verification?", "Is T0069 done?",
                     "T0069 is in Verification.\none\ntwo\nthree", "", None):
            self.assertFalse(session_state.says_done(text), text)


class SaysDoneRenamed(FixtureCase):
    def test_a_renamed_verify_lane(self):
        lanes = copy.deepcopy(project.DEFAULT_LANES)
        lanes[3].update({"dir": "Review", "label": "Under review"})
        tok = project.use(self.make("Renamed", config={"lanes": lanes}))
        try:
            self.assertTrue(session_state.says_done("T0069 is in Under review."))
            self.assertTrue(session_state.says_done("Moved T0069 to Review"))
            self.assertTrue(session_state.says_done("T0069 is in Verification."))
        finally:
            project.reset(tok)


class Record(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Wait")
        self.tok = project.use(self.root)
        # No test starts the judge: a plain message would.
        patcher = mock.patch.object(session_state, "start_judge")
        self.judges = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def test_the_marker_carries_the_question(self):
        session_state.mark_idle(SID, 42, asks=True)
        marks = session_state.idle_sessions()
        self.assertIsNotNone(session_state.idle_since(marks, SID, 42))
        self.assertTrue(session_state.idle_asks(SID, 42))
        session_state.mark_idle(SID, 42, asks=False)
        self.assertFalse(session_state.idle_asks(SID, 42))

    def test_an_unknown_terminal_marker_is_the_fallback(self):
        session_state.mark_idle(SID, 0, asks=True)
        self.assertTrue(session_state.idle_asks(SID, 42))

    def test_an_old_one_line_marker_still_reads(self):
        d = project.current().SESSIONS_DIR
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.idle" % session_state.key(SID, 42))).write_text("1700000000.000\n",
                                                                  encoding="utf-8")
        marks = session_state.idle_sessions()
        self.assertEqual(session_state.idle_since(marks, SID, 42), 1700000000.0)
        self.assertFalse(session_state.idle_asks(SID, 42))

    def test_a_report_reads_back(self):
        self.assertEqual(session_state.reported(SID, 42), "")
        session_state.report(SID, 42, "input")
        self.assertEqual(session_state.reported(SID, 42), "input")
        session_state.report(SID, 0, "finished")
        self.assertEqual(session_state.reported(SID, 7), "finished")
        with self.assertRaises(ValueError):
            session_state.report(SID, 42, "maybe")

    def test_an_event_report_carries_its_reason(self):
        session_state.report(SID, 42, "event", "the editor\n restart  ")
        self.assertEqual(session_state.reported(SID, 42), "event")
        self.assertEqual(session_state.reported_why(SID, 42), "the editor restart")
        session_state.report(SID, 42, "input")
        self.assertEqual(session_state.reported_why(SID, 42), "")
        session_state.report(SID, 0, "event", "a build")
        self.assertEqual(session_state.reported_why(SID, 7), "a build")

    def test_the_stop_hook_records_the_background_tasks(self):
        # The payload Claude Code 2.1.287 sends when a turn ends with a subagent running.
        tasks = [{"id": "a563997732141d3f6", "type": "subagent", "status": "running",
                  "description": "Probe B2 deliberate wait", "agent_type": "general-purpose"},
                 {"id": "b1", "type": "shell", "status": "completed", "description": "build"}]
        # An empty process table: the hook writes the record of an unknown terminal.
        with mock.patch.object(session_state, "_procs", return_value={}):
            session_state._hook({"session_id": SID, "hook_event_name": "Stop",
                                 "last_assistant_message": "Probe B2 waits for its subagent.",
                                 "background_tasks": tasks})
            self.assertEqual(session_state.background_tasks(SID, 42),
                             [{"id": "a563997732141d3f6", "type": "subagent",
                               "description": "Probe B2 deliberate wait"}])
            self.assertFalse(session_state.idle_asks(SID, 42))
            # The next turn ends with nothing left in the background.
            session_state._hook({"session_id": SID, "hook_event_name": "Stop",
                                 "last_assistant_message": "Done.", "background_tasks": []})
            self.assertEqual(session_state.background_tasks(SID, 42), [])

    def test_a_payload_without_background_tasks_records_none(self):
        for value in (None, "x", [None, 3], [{"status": "killed"}]):
            self.assertEqual(session_state.running_tasks(value), [])
        session_state.mark_tasks(SID, 42, [{"id": "a", "type": "subagent", "description": ""}])
        session_state.mark_busy(SID, 42)
        self.assertEqual(session_state.background_tasks(SID, 42), [])

    def test_the_next_prompt_clears_the_marker_and_the_report(self):
        session_state.mark_idle(SID, 42, asks=True)
        session_state.report(SID, 42, "input")
        session_state.report(SID, 0, "input")
        session_state.mark_busy(SID, 42)
        self.assertIsNone(session_state.idle_since(session_state.idle_sessions(), SID, 42))
        self.assertFalse(session_state.idle_asks(SID, 42))
        self.assertEqual(session_state.reported(SID, 42), "")

    def test_an_idle_compaction_keeps_the_question_and_the_report(self):
        # A session compacted while idle read as running and its question was lost.
        self.hook("Stop", last_assistant_message="Do you approve these two lines?")
        session_state.report(SID, 0, "input", "Do you approve these two lines?")
        self.hook("SessionStart", source="compact")
        self.assertIsNotNone(session_state.idle_since(session_state.idle_sessions(), SID, 0))
        self.assertTrue(session_state.idle_asks(SID, 0))
        self.assertEqual(session_state.reported(SID, 0), "input")
        self.hook("UserPromptSubmit")
        self.assertEqual(session_state.idle_sessions(), {})
        self.assertEqual(session_state.reported(SID, 0), "")

    def test_a_new_or_resumed_claude_still_clears_the_marker(self):
        for source in ("startup", "resume", "clear"):
            self.hook("Stop", last_assistant_message="Which one?")
            self.hook("SessionStart", source=source)
            self.assertEqual(session_state.idle_sessions(), {}, source)

    def test_the_stop_hook_records_the_question(self):
        # An empty process table: the hook finds no Claude and no shell, so it writes the
        # marker of an unknown terminal and sets no window title.
        with mock.patch.object(session_state, "_procs", return_value={}):
            session_state._hook({"session_id": SID, "hook_event_name": "Stop",
                                 "last_assistant_message": "Which one do you want?"})
            self.assertTrue(session_state.idle_asks(SID, 0))
            session_state._hook({"session_id": SID, "hook_event_name": "Stop"})
            self.assertFalse(session_state.idle_asks(SID, 0))
            session_state._hook({"session_id": SID, "hook_event_name": "Stop",
                                 "last_assistant_message": "Which one?"})
            session_state._hook({"session_id": SID, "hook_event_name": "UserPromptSubmit"})
            self.assertFalse(session_state.idle_asks(SID, 0))

    def test_the_stop_hook_records_the_finished_line(self):
        with mock.patch.object(session_state, "_procs", return_value={}):
            session_state._hook({"session_id": SID, "hook_event_name": "Stop",
                                 "last_assistant_message": "It holds.\nWORK IS FINISHED"})
            self.assertEqual(session_state.idle_finished(SID, 0), "WORK IS FINISHED")
            self.assertFalse(session_state.idle_asks(SID, 0))
            # The line outranks a question above it: the session asks nothing at the end.
            session_state._hook({"session_id": SID, "hook_event_name": "Stop",
                                 "last_assistant_message": "Shall I?\nINVESTIGATION FINISHED"})
            self.assertEqual(session_state.idle_finished(SID, 0), "INVESTIGATION FINISHED")
            self.assertFalse(session_state.idle_asks(SID, 0))
            self.assertFalse(session_state.idle_unsure(SID, 0))
            session_state._hook({"session_id": SID, "hook_event_name": "UserPromptSubmit"})
            self.assertFalse(session_state.idle_finished(SID, 0))
        self.judges.assert_not_called()

    def test_the_stop_hook_records_the_done_phrase(self):
        self.hook("Stop", last_assistant_message="Shall I go on?\nFiles: a.py\n"
                                                 "T0069 is in Verification.")
        self.assertEqual(session_state.idle_finished(SID, 0), "T0069 is in Verification")
        self.assertFalse(session_state.idle_asks(SID, 0))
        self.judges.assert_not_called()

    def test_an_old_finished_marker_reads_as_the_finished_line(self):
        d = project.current().SESSIONS_DIR
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.idle" % session_state.key(SID, 0))).write_text("1700000000.000\nfinished\n",
                                                                 encoding="utf-8")
        self.assertEqual(session_state.idle_finished(SID, 0), "WORK IS FINISHED")

    def test_the_judge_runs_only_when_no_rule_reads_the_message(self):
        for text in ("I changed the parser.", "Which lane?\nI changed the parser."):
            self.judges.reset_mock()
            self.hook("Stop", last_assistant_message=text)
            self.judges.assert_called_once()
            self.assertEqual(self.judges.call_args[0][:3], (SID, 0, text))
        self.assertTrue(session_state.idle_unsure(SID, 0))
        self.assertFalse(session_state.idle_asks(SID, 0))
        for text in ("Done.\nWORK IS FINISHED", "T0069 is in Verification.",
                     "Which lane should it go to?", "", "  \n"):
            self.judges.reset_mock()
            self.hook("Stop", last_assistant_message=text)
            self.judges.assert_not_called()

    def test_a_hook_under_the_judge_writes_nothing(self):
        with mock.patch.dict(os.environ, {session_state.JUDGE_ENV: "1"}):
            self.hook("Stop", last_assistant_message="Which one?")
            self.hook("Notification", notification_type="permission_prompt")
        self.assertEqual(session_state.idle_sessions(), {})
        self.assertFalse(session_state._dir().is_dir() and any(session_state._dir().iterdir()))
        self.judges.assert_not_called()

    def hook(self, event, started=None, **fields):
        with mock.patch.object(session_state, "_procs", return_value={}):
            session_state._hook(dict(fields, session_id=SID, hook_event_name=event), started)

    def test_a_permission_prompt_waits_for_the_owner(self):
        # The prompt stops the turn, and no `Stop` fires while it waits.
        self.hook("Notification", notification_type="permission_prompt")
        self.assertTrue(session_state.idle_dialog(SID, 0))
        self.assertTrue(session_state.idle_asks(SID, 0))
        self.assertIsNotNone(session_state.idle_since(session_state.idle_sessions(), SID, 0))

    def test_a_question_or_a_plan_dialog_waits_for_the_owner(self):
        for tool in ("AskUserQuestion", "ExitPlanMode"):
            self.hook("UserPromptSubmit")
            self.hook("PreToolUse", tool_name=tool)
            self.assertTrue(session_state.idle_dialog(SID, 0), tool)

    def test_other_tools_and_notifications_change_nothing(self):
        self.hook("PreToolUse", tool_name="Bash")
        self.hook("Notification", notification_type="idle_prompt")
        self.assertEqual(session_state.idle_sessions(), {})
        # An idle notification after the turn ended leaves the Stop marker as it was.
        self.hook("Stop", last_assistant_message="Which one?")
        self.hook("Notification", notification_type="idle_prompt")
        self.assertTrue(session_state.idle_asks(SID, 0))

    def test_the_permission_prompt_of_a_question_dialog_keeps_its_question(self):
        self.hook("PreToolUse", tool_name="AskUserQuestion", tool_input={"questions": [
            {"question": "Which lane?", "options": [{"label": "Pipeline"}, {"label": "Blocked"}]}]})
        self.hook("Notification", notification_type="permission_prompt",
                  message="Claude needs your permission to use AskUserQuestion")
        self.assertEqual(session_state.idle_question(SID, 0), "Which lane? (Pipeline | Blocked)")
        self.hook("Notification", notification_type="permission_prompt",
                  message="Claude needs your permission")
        self.assertEqual(session_state.idle_question(SID, 0), "Which lane? (Pipeline | Blocked)")
        self.hook("Notification", notification_type="permission_prompt",
                  message="Claude needs your permission to use Bash")
        self.assertEqual(session_state.idle_question(SID, 0),
                         "Claude needs your permission to use Bash")

    def test_an_answered_question_dialog_clears_the_input_report(self):
        session_state.report(SID, 0, "input", "Which lane?")
        self.hook("PreToolUse", tool_name="AskUserQuestion")
        self.assertTrue(session_state.idle_dialog(SID, 0))
        self.hook("PostToolUse", started=time.time() + 1, tool_name="AskUserQuestion")
        self.assertFalse(session_state.idle_dialog(SID, 0))
        self.assertEqual(session_state.reported(SID, 0), "")
        # The answer of another dialog leaves an `event` report as it was.
        session_state.report(SID, 0, "event", "editor restart")
        self.hook("PostToolUse", started=time.time() + 1, tool_name="AskUserQuestion")
        self.assertEqual(session_state.reported(SID, 0), "event")

    def test_the_tool_that_ran_after_the_dialog_clears_it(self):
        self.hook("Notification", notification_type="permission_prompt")
        session_state.report(SID, 0, "input")
        self.hook("PostToolUse", started=time.time() + 1, tool_name="Bash")
        self.assertIsNone(session_state.idle_since(session_state.idle_sessions(), SID, 0))
        # The report is the session's own word: only the owner's next prompt clears it.
        self.assertEqual(session_state.reported(SID, 0), "input")

    def test_a_tool_never_clears_the_stop_marker(self):
        self.hook("Stop", last_assistant_message="Shall I?")
        self.hook("PostToolUse", started=time.time() + 1, tool_name="Bash")
        self.assertTrue(session_state.idle_asks(SID, 0))

    def test_a_turn_that_ends_in_an_api_error_waits_for_the_owner(self):
        # `StopFailure` fires instead of `Stop`; the error text is no message to judge.
        self.hook("UserPromptSubmit")
        self.hook("StopFailure", error="rate_limit",
                  last_assistant_message="API Error: Rate limit reached")
        self.assertIsNotNone(session_state.idle_since(session_state.idle_sessions(), SID, 0))
        self.assertEqual(session_state.idle_error(SID, 0), "rate_limit")
        self.assertFalse(session_state.idle_asks(SID, 0))
        self.judges.assert_not_called()
        self.hook("StopFailure")
        self.assertEqual(session_state.idle_error(SID, 0), "error")
        # The owner's next prompt, the retry, takes it again; a later `Stop` replaces it.
        self.hook("UserPromptSubmit")
        self.assertEqual(session_state.idle_error(SID, 0), "")
        self.hook("StopFailure", error="overloaded")
        self.hook("Stop", last_assistant_message="Which one?")
        self.assertEqual(session_state.idle_error(SID, 0), "")
        self.assertTrue(session_state.idle_asks(SID, 0))

    def test_a_late_background_hook_keeps_a_newer_dialog(self):
        # PostToolUse runs async: a dialog written after it started is a new one.
        self.hook("Notification", notification_type="permission_prompt")
        self.hook("PostToolUse", started=time.time() - 60, tool_name="Bash")
        self.assertTrue(session_state.idle_dialog(SID, 0))


class Judge(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Judge")
        self.tok = project.use(self.root)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def keep(self, text="I changed the parser.", shell=42):
        session_state.mark_idle(SID, shell)
        session_state._last_path(SID, shell).write_text(text, encoding="utf-8")

    def test_the_judge_can_run_without_claude(self):
        import todo_server
        path = self.root / ".octopus" / "project.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["provider"] = "codex"
        path.write_text(json.dumps(config), encoding="utf-8")
        project.reset(self.tok)
        self.tok = project.use(self.root)
        self.keep()
        with mock.patch.object(todo_server, "_ask_provider", return_value=VERDICT_INPUT) as ask:
            session_state.judge(SID, 42)
        self.assertEqual(ask.call_args.args[1], {"provider": "codex", "model": None})

    def run_judge(self, answer, shell=42, during=None):
        import todo_server

        def ask(prompt, body, timeout):
            self.prompt, self.model = prompt, body.get("model")
            if during:
                during()
            return answer
        with mock.patch.object(todo_server, "_ask_provider", side_effect=ask):
            return session_state.judge(SID, shell)

    def test_start_judge_keeps_the_message_and_returns_at_once(self):
        with mock.patch("subprocess.Popen") as popen:
            session_state.start_judge(SID, 42, "I changed the parser.", self.root)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[2:7], ["--judge", SID, "42", "--project", str(self.root)])
        self.assertEqual(session_state._last_path(SID, 42).read_text(encoding="utf-8"),
                         "I changed the parser.")
        self.assertFalse(popen.return_value.wait.called)

    def test_each_answer_writes_the_verdict(self):
        for state in ("input", "finished", "working"):
            self.keep()
            answer = ('Here: {"state": "%s", "why": "it  says so", '
                      '"quote": "I changed the parser."} done' % state)
            self.assertEqual(self.run_judge(answer), state)
            self.assertEqual(session_state.judged(SID, 42),
                             (state, 'it says so: "I changed the parser."'))
        self.assertIn("I changed the parser.", self.prompt)
        self.assertEqual(self.model, project.current().models.get("haiku", "haiku"))
        # Every hook under the judge's own `claude -p` returns at once.
        self.assertEqual(os.environ.get(session_state.JUDGE_ENV), "1")

    def test_the_prompt_names_the_ticket(self):
        project.reset(self.tok)
        tid = self.new_ticket(self.root, "Fix the parser")
        self.tok = project.use(self.root)
        sid = todo.launch_session_id(tid, "work")
        session_state.mark_idle(sid, 0)
        session_state._last_path(sid, 0).write_text("I changed it.", encoding="utf-8")
        import todo_server
        with mock.patch.object(todo_server, "_ask_provider",
                               return_value='{"state": "finished", "why": "x"}') as ask:
            session_state.judge(sid, 0)
        prompt = ask.call_args[0][0]
        self.assertIn(tid, prompt)
        self.assertIn("Fix the parser", prompt)
        self.assertIn("`work`", prompt)

    def judge_prompt(self, sid):
        session_state.mark_idle(sid, 0)
        session_state._last_path(sid, 0).write_text("T0002 asks: hunger or chance?",
                                                    encoding="utf-8")
        import todo_server
        with mock.patch.object(todo_server, "_ask_provider",
                               return_value='{"state": "working", "why": "x"}') as ask:
            session_state.judge(sid, 0)
        return ask.call_args[0][0]

    def test_the_prompt_names_the_agents_the_session_waits_for(self):
        # The reported bug: a plan session whose children ask the owner relayed their
        # questions, and the judge, told nothing of the children, answered input.
        project.reset(self.tok)
        parent = self.new_ticket(self.root, "No offenders exist")
        child = self.new_ticket(self.root, "Nobody becomes a cannibal", "--parent", parent)
        self.tok = project.use(self.root)
        prompt = self.judge_prompt(todo.launch_session_id(parent, "plan"))
        self.assertIn("waits for other agents", prompt)
        self.assertIn("ticket %s: Nobody becomes a cannibal" % child, prompt)
        self.assertIn("only reports or relays, is not input", prompt)
        # A session that waits for no other agent gets the prompt without that part.
        prompt = self.judge_prompt(todo.launch_session_id(child, "work"))
        self.assertNotIn("waits for other agents", prompt)
        # Its scheduled check and its background work count as well.
        sid = todo.launch_session_id(child, "work")
        session_state.add_job(sid, 0, "e4810603")
        session_state.mark_tasks(sid, 0, [AGENT])
        prompt = self.judge_prompt(sid)
        self.assertIn("job e4810603", prompt)
        self.assertIn("Probe B2 deliberate wait", prompt)

    def test_a_rejected_answer_is_asked_once_more_with_its_fault(self):
        import todo_server
        self.keep()
        answers = iter(['{"state": "finished", "why": "x", "quote": "Completed the cycle"}',
                        '{"state": "finished", "why": "x", "quote": "I changed the parser."}'])
        prompts = []

        def ask(prompt, body, timeout):
            prompts.append(prompt)
            return next(answers)
        with mock.patch.object(todo_server, "_ask_provider", side_effect=ask):
            self.assertEqual(session_state.judge(SID, 42), "finished")
        self.assertEqual(len(prompts), 2)
        self.assertIn("Your previous answer was rejected", prompts[1])
        self.assertIn("Completed the cycle", prompts[1])
        self.assertEqual(session_state.judged(SID, 42),
                         ("finished", 'x: "I changed the parser."'))

    def test_garbage_writes_nothing(self):
        for answer in ("no idea", '{"state": "maybe"}', "{not json}", "",
                       '{"state": "finished", "why": "x"}',
                       '{"state": "finished", "why": "x", "quote": "Completed the cycle"}'):
            self.keep()
            self.assertEqual(self.run_judge(answer), "")
            self.assertEqual(session_state.judged(SID, 42), ("", ""))

    def test_a_marker_written_meanwhile_discards_the_verdict(self):
        self.keep()

        def rewrite():
            time.sleep(0.01)
            session_state.mark_idle(SID, 42)
        self.assertEqual(self.run_judge(VERDICT_INPUT, during=rewrite), "")
        self.assertEqual(session_state.judged(SID, 42), ("", ""))

    def test_a_stale_verdict_is_ignored_and_the_next_prompt_clears_it(self):
        self.keep()
        self.run_judge(VERDICT_INPUT)
        self.assertEqual(session_state.judged(SID, 42)[0], "input")
        time.sleep(0.01)
        session_state.mark_idle(SID, 42)
        self.assertEqual(session_state.judged(SID, 42), ("", ""))
        self.keep()
        self.run_judge(VERDICT_INPUT)
        session_state.mark_busy(SID, 42)
        self.assertEqual(session_state.judged(SID, 42), ("", ""))
        self.assertFalse(session_state._judge_path(SID, 42).exists())
        self.assertFalse(session_state._last_path(SID, 42).exists())

    def test_no_message_no_verdict(self):
        session_state.mark_idle(SID, 42)
        self.assertEqual(self.run_judge('{"state": "input", "why": "x"}'), "")

    def test_a_failed_claude_writes_nothing(self):
        import todo_server
        self.keep()
        with mock.patch.object(todo_server, "_ask_provider", side_effect=ValueError("gone")):
            self.assertEqual(session_state.judge(SID, 42), "")
        self.assertEqual(session_state.judged(SID, 42), ("", ""))


class Transcript(FixtureCase):
    """A session whose hook files are gone is read back from its transcript."""

    def setUp(self):
        super().setUp()
        self.root = self.make("Transcript")
        self.tok = project.use(self.root)
        self.path = self.tmp / "t.jsonl"

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def write(self, *rows):
        import json
        self.path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    @staticmethod
    def said(text, stop="end_turn", kind="text"):
        part = {"type": kind, "text": text} if kind == "text" else {"type": kind}
        return {"type": "assistant", "message": {"role": "assistant", "stop_reason": stop,
                                                 "content": [part]}}

    def test_the_last_turn_ended(self):
        self.write({"type": "user", "message": {"content": "go"}},
                   self.said("Reading.", "tool_use"), {"type": "user", "message": {}},
                   self.said("", "end_turn", "thinking"),
                   self.said("T0061 is done and waits in Verification."),
                   {"type": "system", "subtype": "stop_hook_summary"})
        text, ended, when = session_state.transcript_tail(self.path)
        self.assertEqual(text, "T0061 is done and waits in Verification.")
        self.assertTrue(ended)
        self.assertGreater(when, 0)

    def test_a_turn_that_stopped_at_a_tool_call(self):
        self.write(self.said("Running the build.", "tool_use"))
        self.assertFalse(session_state.transcript_tail(self.path)[1])

    def test_the_owners_slash_command_is_no_turn(self):
        # The reported bug: `/model` after the last message read as a dialog.
        self.write(self.said("Plan written."),
                   {"type": "user", "message": {"content": "<local-command-caveat>x"}},
                   {"type": "user", "message": {"content": "<command-name>/model</command-name>"}},
                   {"type": "user", "message": {"content": [
                       {"type": "text", "text": "[Request interrupted by user]"}]}},
                   {"type": "user", "isMeta": True, "message": {"content": "meta"}},
                   {"type": "file-history-snapshot"})
        self.assertEqual(session_state.transcript_tail(self.path)[:2], ("Plan written.", True))

    def test_a_compaction_is_no_turn(self):
        self.write(self.said("Do you approve these two lines?"),
                   {"type": "system", "subtype": "compact_boundary"},
                   {"type": "user", "isCompactSummary": True,
                    "message": {"content": "This session is being continued..."}})
        self.assertEqual(session_state.transcript_tail(self.path)[:2],
                         ("Do you approve these two lines?", True))

    def test_no_transcript_or_no_message_never_began(self):
        self.assertEqual(session_state.transcript_tail(self.tmp / "none.jsonl"), (None, None, 0))
        self.write({"type": "system"}, {"type": "attachment"})
        self.assertEqual(session_state.transcript_tail(self.path), (None, None, 0))

    def test_recover_writes_the_marker_at_the_turns_time(self):
        self.assertFalse(session_state.recover(SID, 42, "It holds.\nWORK IS FINISHED", 1700.0))
        self.assertEqual(session_state.idle_sessions()[session_state.key(SID, 42)], 1700.0)
        self.assertEqual(session_state.idle_finished(SID, 42), "WORK IS FINISHED")
        self.assertTrue(session_state.recover(SID, 42, "I changed the parser.", 1800.0))

    # Claude Code's own message when a turn ends in an API error: the error, not Claude's.
    API_ERROR = {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
         "message": {"model": "<synthetic>", "role": "assistant", "stop_reason": "stop_sequence",
                     "content": [{"type": "text", "text": "API Error: Rate limit reached"}]}}

    def test_a_turn_that_ended_in_an_api_error_names_the_error(self):
        self.write(self.said("Reading the ticket."), self.API_ERROR)
        self.assertEqual(session_state.transcript_tail(self.path, error=True)[1:2] +
                         session_state.transcript_tail(self.path, error=True)[3:],
                         (True, "rate_limit"))
        self.write(self.said("Plan written."))
        self.assertEqual(session_state.transcript_tail(self.path, error=True)[3], "")
        self.assertEqual(session_state.transcript_tail(self.tmp / "none.jsonl", error=True),
                         (None, None, 0, ""))

    def test_recover_of_an_api_error_writes_the_error_marker_and_no_judge(self):
        self.assertFalse(session_state.recover(SID, 42, "API Error: Rate limit reached",
                                               1700.0, "rate_limit"))
        self.assertEqual(session_state.idle_error(SID, 42), "rate_limit")
        self.assertEqual(session_state.idle_sessions()[session_state.key(SID, 42)], 1700.0)

    def test_mark_stale_of_an_api_error_writes_the_error_marker_and_no_judge(self):
        self.assertFalse(session_state.mark_stale(SID, 42, "Context limit reached", 1700.0,
                                                  "invalid_request"))
        self.assertEqual(session_state.idle_error(SID, 42), "invalid_request")
        self.assertFalse(session_state.ensure_judged(SID, 42, self.tmp))

    def test_the_judge_runs_once_per_turn_on_what_no_rule_read(self):
        self.write(self.said("I changed the parser."))
        session_state.mark_idle(SID, 42)
        with mock.patch("subprocess.Popen") as popen:
            self.assertTrue(session_state.ensure_judged(SID, 42, self.root, self.path))
            self.assertEqual(session_state._last_path(SID, 42).read_text(encoding="utf-8"),
                             "I changed the parser.")
            self.assertTrue(session_state.judge_started(SID, 42))
            self.assertFalse(session_state.ensure_judged(SID, 42, self.root, self.path))
            self.assertEqual(popen.call_count, 1)
            # The next turn gets its own judge.
            time.sleep(0.01)
            session_state.mark_idle(SID, 42)
            self.assertTrue(session_state.ensure_judged(SID, 42, self.root, self.path))
            # A question, the finished line, a dialog: no judge.
            for kw in ({"asks": True}, {"finished": "WORK IS FINISHED"}, {"dialog": True}):
                time.sleep(0.01)
                session_state.mark_idle(SID, 42, **kw)
                self.assertFalse(session_state.ensure_judged(SID, 42, self.root, self.path))
            self.assertEqual(popen.call_count, 2)

    def test_a_question_on_the_last_line_never_goes_to_the_judge(self):
        # The owner (2026-10-18): a question on the last line is input, whatever the session
        # waits for.
        text = "T0020 waits for its children.\nT0033 asks: hunger first or chance?"
        session_state.mark_stop(SID, 42, text)
        session_state._last_path(SID, 42).write_text(text, encoding="utf-8")
        with mock.patch("subprocess.Popen") as popen:
            self.assertFalse(session_state.ensure_judged(SID, 42, self.root, self.path))
            for kw in ({"dialog": True}, {"error": "overloaded"},
                       {"finished": "WORK IS FINISHED"}):
                time.sleep(0.01)
                session_state.mark_idle(SID, 42, **kw)
                self.assertFalse(session_state.ensure_judged(SID, 42, self.root, self.path))
            self.assertEqual(popen.call_count, 0)

    def test_prune_keeps_the_files_of_a_session_that_still_runs(self):
        old = time.time() - 9 * 24 * 3600
        session_state.mark_idle(SID, 42)
        other = "1f0e0d0c-0b0a-4908-8706-050403020100"
        session_state.mark_idle(other, 42)
        for sid in (SID, other):
            p = session_state._marker(sid, 42)
            os.utime(p, (old, old))
        session_state._dir().joinpath("%s.live.json" % SID).write_text(
            '{"session": "%s", "claude": 7}' % SID, encoding="utf-8")
        with mock.patch.object(session_state, "_procs", return_value={7: (1, "claude.exe")}), \
                mock.patch.object(session_state, "_alive",
                                  side_effect=lambda pid, started, procs: pid in procs):
            session_state.prune()
        self.assertTrue(session_state._marker(SID, 42).exists())
        self.assertFalse(session_state._marker(other, 42).exists())


class Activity(FixtureCase):
    """What the transcript says of the current turn: ended, its last update, what it does."""

    def setUp(self):
        super().setUp()
        self.root = self.make("Activity")
        self.tok = project.use(self.root)
        self.path = self.tmp / "a.jsonl"

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def write(self, *rows):
        self.path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    @staticmethod
    def call(name, stop="tool_use", ts="2026-10-18T20:00:00Z", **tin):
        return {"type": "assistant", "timestamp": ts, "message": {
            "stop_reason": stop, "content": [{"type": "tool_use", "id": "t1", "name": name,
                                              "input": tin}]}}

    @staticmethod
    def said(text, stop="end_turn", ts="2026-10-18T20:01:00Z"):
        return {"type": "assistant", "timestamp": ts, "message": {
            "stop_reason": stop, "content": [{"type": "text", "text": text}]}}

    RESULT = {"type": "user", "timestamp": "2026-10-18T20:00:05Z", "message": {
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}

    def test_a_pending_tool_call_runs_and_names_the_tool(self):
        self.write({"type": "user", "message": {"content": "go"}},
                   self.said("Running the tests.", "tool_use"),
                   self.call("Bash", command="python -m unittest", description="Run the tests"))
        a = session_state.transcript_activity(self.path)
        self.assertFalse(a["ended"])
        self.assertEqual(a["doing"], "Bash: Run the tests")
        self.assertEqual(a["text"], "Running the tests.")
        self.assertEqual(a["since"], session_state._stamp("2026-10-18T20:00:00Z"))
        self.assertGreater(a["when"], 0)

    def test_the_answer_of_a_question_dialog_has_its_time(self):
        self.write(self.call("Bash", command="ls"), self.RESULT)
        self.assertEqual(session_state.last_answer(self.path), 0)
        self.write(self.call("AskUserQuestion", questions=[{"question": "Which lane?"}]),
                   self.RESULT, self.said("Done.\nWORK IS FINISHED"))
        self.assertEqual(session_state.last_answer(self.path),
                         session_state._stamp("2026-10-18T20:00:05Z"))
        self.assertEqual(session_state.last_answer(self.tmp / "none.jsonl"), 0)

    def test_a_tool_result_last_runs(self):
        self.write(self.call("Read", file_path="D:/x/todo.py"), self.RESULT)
        a = session_state.transcript_activity(self.path)
        self.assertFalse(a["ended"])
        self.assertEqual(a["doing"], "Read: D:/x/todo.py")

    def test_an_end_turn_ended(self):
        self.write(self.call("Edit", file_path="a.py"), self.RESULT,
                   self.said("Changed it.\nWORK IS FINISHED"),
                   {"type": "system", "subtype": "turn_duration"})
        a = session_state.transcript_activity(self.path)
        self.assertTrue(a["ended"])
        self.assertEqual(a["text"], "Changed it.\nWORK IS FINISHED")
        self.assertEqual(a["doing"], "WORK IS FINISHED")

    def test_an_interrupt_ends_the_turn_and_a_slash_command_is_no_turn(self):
        self.write(self.call("Bash", command="sleep 600"),
                   {"type": "user", "message": {"content": [
                       {"type": "text", "text": "[Request interrupted by user for tool use]"}]}})
        self.assertTrue(session_state.transcript_activity(self.path)["ended"])
        self.write(self.said("Plan written."),
                   {"type": "user", "message": {"content": "<command-name>/recap</command-name>"}})
        self.assertTrue(session_state.transcript_activity(self.path)["ended"])

    def test_a_long_action_is_cut(self):
        self.write(self.call("Bash", command="x" * 300))
        self.assertEqual(len(session_state.transcript_activity(self.path)["doing"]),
                         session_state.DOING_CHARS)

    def test_no_transcript(self):
        self.assertIsNone(session_state.transcript_activity(self.tmp / "none.jsonl"))
        self.write({"type": "system"})
        self.assertIsNone(session_state.transcript_activity(self.path))

    def test_an_api_error_ends_the_turn_and_names_the_error(self):
        self.write(self.call("Read", file_path="a.py"), self.RESULT,
                   {"type": "assistant", "isApiErrorMessage": True, "message": {
                       "model": "<synthetic>", "stop_reason": "stop_sequence",
                       "content": [{"type": "text", "text": "API Error: 529 overloaded"}]}})
        a = session_state.transcript_activity(self.path)
        self.assertTrue(a["ended"])
        self.assertEqual(a["error"], "error")
        self.write(self.said("Changed it."))
        self.assertEqual(session_state.transcript_activity(self.path)["error"], "")

    def test_the_recap_row_after_the_command(self):
        self.write({"type": "system", "subtype": "local_command", "commandRun": {
            "command": "recap", "args": ""}, "timestamp": "2026-10-18T20:25:57Z",
            "content": "<local-command-stdout>You're working T0004. It's in Verification."
                       "</local-command-stdout>"},
            {"type": "system", "subtype": "local_command", "commandRun": {"command": "model"},
             "timestamp": "2026-10-18T20:26:00Z", "content": "<local-command-stdout>x"
                                                             "</local-command-stdout>"})
        t = session_state._stamp("2026-10-18T20:25:51Z")
        self.assertEqual(session_state.recap_after(SID, self.path, t),
                         "You're working T0004. It's in Verification.")
        self.assertEqual(session_state.recap_after(SID, self.path, t + 3600), "")


class FinishBrief(FixtureCase):
    def test_every_brief_asks_for_the_finished_line_but_a_managers(self):
        root = self.make("Brief")
        tid = self.new_ticket(root, "Fix the parser")
        mgr = self.new_ticket(root, "Manager", "--repeat", "manager", "--permits", "projects")
        tok = project.use(root)
        try:
            t = todo.find(todo.load_all(), tid)
            for mode in todo.MODES:
                text = todo.build_brief(t, mode)
                self.assertIn("`WORK IS FINISHED`", text, mode)
                self.assertNotIn("INVESTIGATION FINISHED", text, mode)
            self.assertNotIn("WORK IS FINISHED",
                             todo.build_brief(todo.find(todo.load_all(), mgr), "work"))
        finally:
            project.reset(tok)

    def test_every_mode_closes_its_own_terminal_but_a_manager(self):
        root = self.make("Brief")
        tid = self.new_ticket(root, "Fix the parser")
        rep = self.new_ticket(root, "Run the tests", "--repeat", "test")
        mgr = self.new_ticket(root, "Manager", "--repeat", "manager", "--permits", "projects")
        tok = project.use(root)
        try:
            tickets = todo.load_all()
            plan = todo.build_brief(todo.find(tickets, tid), "plan")
            finish = plan.split("## When you finish")[1].split("## The ticket")[0]
            self.assertNotIn("CronCreate", finish)
            for one in (tid, rep):
                t = todo.find(tickets, one)
                for mode in todo.MODES:
                    self.assertIn("close %s --mode %s --self" % (one, mode),
                                  todo.build_brief(t, mode), (one, mode))
            self.assertNotIn("--self", todo.build_brief(todo.find(tickets, mgr), "work"))
        finally:
            project.reset(tok)


class SelfClose(unittest.TestCase):
    """`close --self` and the detached closer it starts."""

    @staticmethod
    def sess(running=False, wait="finished"):
        return {"key": "T0001/work", "id": "T0001", "mode": "work", "session": SID,
                "running": running, "wait": wait, "shell": 900, "claude": 901}

    def closer(self, states, wait=60):
        """close_when_finished over `live_sessions` answers in `states` (a session or
        None). Returns its rc and the sessions close_session got."""
        seq = iter(states)
        closed = []
        with mock.patch.object(todo, "live_sessions",
                               side_effect=lambda *a: [s for s in [next(seq)] if s]), \
                mock.patch.object(todo, "close_session",
                                  side_effect=lambda s, force=False: closed.append(s) or 0), \
                mock.patch("time.sleep"):
            rc = todo.close_when_finished("T0001", "work", wait=wait, poll=0)
        return rc, closed

    def test_a_running_session_closes_once_finished(self):
        rc, closed = self.closer([self.sess(running=True, wait=None), self.sess()])
        self.assertEqual((rc, len(closed)), (0, 1))

    def test_a_session_that_asks_stays_open(self):
        self.assertEqual(self.closer([self.sess(wait="input")]), (0, []))

    def test_a_gone_session_is_left_alone(self):
        self.assertEqual(self.closer([None]), (0, []))

    def test_the_closer_gives_up_after_its_wait(self):
        self.assertEqual(self.closer([self.sess(running=True, wait=None)], wait=0), (0, []))

    def closer_now(self, states, wait=60):
        """The `--now` closer the `Stop` hook starts on `WORK IS FINISHED`."""
        seq = iter(states)
        closed = []
        with mock.patch.object(todo, "live_sessions",
                               side_effect=lambda *a: [s for s in [next(seq)] if s]), \
                mock.patch.object(todo, "close_session",
                                  side_effect=lambda s, force=False: closed.append(s) or 0), \
                mock.patch("time.sleep"):
            rc = todo.close_when_finished("T0001", "work", wait=wait, poll=0, now=True)
        return rc, closed

    def test_now_closes_the_ended_turn_without_waiting_for_the_state(self):
        # T0085: an old event report, `idle`, or a judge not yet answered closes nothing.
        for wait in ("event", "idle", "finished"):
            s = dict(self.sess(wait=wait), says_finished="WORK IS FINISHED")
            rc, closed = self.closer_now([s])
            self.assertEqual((rc, len(closed)), (0, 1), wait)

    def test_now_falls_back_to_the_wait_on_a_new_turn(self):
        # The owner typed before the closer looked: the new turn needs `finished`.
        running = dict(self.sess(running=True, wait=None), says_finished="")
        self.assertEqual(self.closer_now([running, self.sess(wait="input")]), (0, []))
        rc, closed = self.closer_now([running, self.sess()])
        self.assertEqual((rc, len(closed)), (0, 1))

    def run_self(self, parent):
        """cmd_close --self with this process under `parent`."""
        import winproc
        procs = {os.getpid(): (parent, "python.exe"), 901: (900, "claude.exe"),
                 900: (5, "cmd.exe"), 77: (5, "cmd.exe")}
        args = SimpleNamespace(id="T0001", mode="work", self=True, when_finished=False,
                               finished=False, force=False)
        with mock.patch.object(todo, "live_sessions", return_value=[self.sess(running=True)]), \
                mock.patch.object(winproc, "processes", return_value=procs), \
                mock.patch.object(todo, "P", SimpleNamespace(ROOT="X")), \
                mock.patch.object(todo, "spawn_detached") as spawn:
            rc = todo.cmd_close(args)
        return rc, spawn

    def test_self_starts_the_closer_inside_its_own_session(self):
        rc, spawn = self.run_self(901)
        self.assertEqual(rc, 0)
        argv = spawn.call_args[0][0]
        self.assertIn("--when-finished", argv)
        self.assertEqual(argv[argv.index("close") + 1], "T0001")

    def test_self_refuses_another_session(self):
        rc, spawn = self.run_self(77)
        self.assertEqual(rc, 2)
        spawn.assert_not_called()

    def test_force_on_its_own_session_points_to_self(self):
        import io
        import winproc
        procs = {os.getpid(): (901, "python.exe"), 901: (900, "claude.exe"),
                 900: (5, "cmd.exe")}
        args = SimpleNamespace(id="T0001", mode="work", self=False, when_finished=False,
                               finished=False, force=True)
        err = io.StringIO()
        with mock.patch.object(todo, "live_sessions", return_value=[self.sess(running=True)]), \
                mock.patch.object(winproc, "processes", return_value=procs), \
                mock.patch.object(todo, "P", SimpleNamespace(ROOT="X", cli="todo")), \
                mock.patch("sys.stderr", err):
            rc = todo.cmd_close(args)
        self.assertEqual(rc, 1)
        self.assertIn("todo close T0001 --mode work --self", err.getvalue())


class Jobs(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Jobs")
        self.tok = project.use(self.root)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def hook(self, event, **fields):
        with mock.patch.object(session_state, "_procs", return_value={}):
            session_state._hook(dict(fields, session_id=SID, hook_event_name=event))

    def create(self, job, recurring=None):
        tin = {"cron": "3,13,23,33,43,53 * * * *", "prompt": "Run show T0002"}
        if recurring is not None:
            tin["recurring"] = recurring
        self.hook("PostToolUse", tool_name="CronCreate", tool_input=tin,
                  tool_response="Scheduled recurring task %s (3,13 * * * *)." % job)

    def test_the_job_id_of_a_response(self):
        self.assertEqual(session_state.job_id("Scheduled one-shot task e4810603 (17 4 * * *)."),
                         "e4810603")
        self.assertEqual(session_state.job_id({"id": "ab12cd34"}), "ab12cd34")
        self.assertEqual(session_state.job_id({"text": "Scheduled recurring job ff00aa11"}),
                         "ff00aa11")
        self.assertEqual(session_state.job_id(None), "")

    def test_create_then_delete(self):
        self.create("e4810603")
        self.create("0badcafe", recurring=True)
        self.assertEqual(session_state.jobs(SID, 0), ["e4810603", "0badcafe"])
        self.hook("PostToolUse", tool_name="CronDelete", tool_input={"id": "e4810603"})
        self.assertEqual(session_state.jobs(SID, 0), ["0badcafe"])
        self.hook("PostToolUse", tool_name="CronDelete", tool_input={"id": "0badcafe"})
        self.assertEqual(session_state.jobs(SID, 0), [])

    def test_a_one_shot_job_is_not_recorded(self):
        self.create("e4810603", recurring=False)
        self.assertEqual(session_state.jobs(SID, 0), [])

    def test_an_unreadable_id_is_recorded_and_goes_with_any_cancel(self):
        self.hook("PostToolUse", tool_name="CronCreate", tool_input={}, tool_response="",
                  tool_use_id="toolu_1")
        self.assertEqual(session_state.jobs(SID, 0), ["?toolu_1"])
        self.hook("PostToolUse", tool_name="CronDelete", tool_input={"id": "e4810603"})
        self.assertEqual(session_state.jobs(SID, 0), [])

    def test_a_resumed_claude_keeps_the_jobs_and_a_new_session_drops_them(self):
        # The manager T0058: its job fired on for a day after its Claude was resumed.
        self.create("e4810603")
        self.hook("UserPromptSubmit")
        self.hook("SessionStart", source="compact")
        self.hook("SessionStart", source="resume")
        self.assertEqual(session_state.jobs(SID, 0), ["e4810603"])
        self.hook("SessionStart", source="startup")
        self.assertEqual(session_state.jobs(SID, 0), [])

    def test_a_job_expires_after_seven_days(self):
        session_state.add_job(SID, 0, "e4810603", now=time.time() - 8 * 24 * 3600)
        self.assertEqual(session_state.jobs(SID, 0), [])

    def test_the_jobs_of_the_own_terminal_and_an_unknown_one(self):
        session_state.add_job(SID, 42, "aaaa1111")
        session_state.add_job(SID, 0, "bbbb2222")
        self.assertEqual(sorted(session_state.jobs(SID, 42)), ["aaaa1111", "bbbb2222"])
        self.assertEqual(session_state.jobs(SID, 7), ["bbbb2222"])


def _row(ts, **fields):
    return json.dumps(dict(fields, timestamp=ts)) + "\n"


def _create(ts, use, cron="*/20 * * * *", recurring=True):
    return _row(ts, type="assistant", message={"content": [{
        "type": "tool_use", "id": use, "name": "CronCreate",
        "input": {"cron": cron, "recurring": recurring, "prompt": "Manager status report"}}]})


def _result(ts, use, text):
    return _row(ts, type="user", message={"content": [{
        "type": "tool_result", "tool_use_id": use, "content": text}]})


def _delete(ts, job):
    return _row(ts, type="assistant", message={"content": [{
        "type": "tool_use", "id": "toolu_d" + job, "name": "CronDelete",
        "input": {"id": job}}]})


def _fire(ts, job):
    return _row(ts, type="system", subtype="scheduled_task_fire", taskId=job,
                content="Running scheduled task")


def _iso(when):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(when, timezone.utc).isoformat().replace("+00:00", "Z")


class TranscriptJobs(FixtureCase):
    """The jobs the transcript shows: made, cancelled, firing."""

    def setUp(self):
        super().setUp()
        self.root = self.make("TranscriptJobs")
        self.tok = project.use(self.root)
        self.path = self.root / "t.jsonl"
        self.path.write_text("", encoding="utf-8")
        self.now = time.time()

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def add(self, *rows):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("".join(rows))

    def ago(self, sec):
        return _iso(self.now - sec)

    def jobs(self):
        return session_state.transcript_jobs(SID, self.path, self.now)

    def test_made_then_cancelled(self):
        self.add(_create(self.ago(600), "toolu_1"),
                 _result(self.ago(599), "toolu_1", "Scheduled recurring job 5433589e (Every"
                         " 10 minutes). Session-only."),
                 _create(self.ago(500), "toolu_2"),
                 _result(self.ago(499), "toolu_2", [{"type": "text", "text":
                                                     "Scheduled recurring job 80e364a3."}]))
        self.assertEqual(self.jobs(), ["5433589e", "80e364a3"])
        self.add(_delete(self.ago(100), "5433589e"))
        self.assertEqual(self.jobs(), ["80e364a3"])

    def test_a_one_shot_and_a_failed_create_hold_nothing(self):
        self.add(_create(self.ago(60), "toolu_1", recurring=False),
                 _result(self.ago(59), "toolu_1", "Scheduled one-shot task e4810603."),
                 _fire(self.ago(10), "e4810603"),
                 _create(self.ago(50), "toolu_2"))
        self.add(_row(self.ago(49), type="user", message={"content": [{
            "type": "tool_result", "tool_use_id": "toolu_2", "is_error": True,
            "content": "Invalid cron job 1234abcd"}]}))
        self.assertEqual(self.jobs(), [])

    def test_a_week_old_job_runs_while_it_fires(self):
        # The manager T0058: job 22b1d598, made in an earlier Claude, fired on.
        self.add(_create(self.ago(8 * 86400), "toolu_1"),
                 _result(self.ago(8 * 86400), "toolu_1", "Scheduled recurring job 22b1d598."))
        self.assertEqual(self.jobs(), [])
        self.add(_fire(self.ago(1200), "22b1d598"))
        self.assertEqual(self.jobs(), ["22b1d598"])

    def test_a_firing_job_never_seen_made_runs(self):
        self.add(_fire(self.ago(300), "f074b69b"))
        self.assertEqual(self.jobs(), ["f074b69b"])
        self.add(_delete(self.ago(5), "f074b69b"))
        self.assertEqual(self.jobs(), [])

    def test_the_scan_goes_on_where_it_stopped_and_starts_over_on_a_shorter_file(self):
        self.add(_create(self.ago(60), "toolu_1"))
        self.assertEqual(self.jobs(), [])
        # The result in two writes: the half line waits for the rest.
        line = _result(self.ago(59), "toolu_1", "Scheduled recurring job 0badcafe.")
        self.add(line[:20])
        self.assertEqual(self.jobs(), [])
        self.add(line[20:])
        self.assertEqual(self.jobs(), ["0badcafe"])
        self.path.write_text(_fire(self.ago(1), "e4810603"), encoding="utf-8")
        self.assertEqual(self.jobs(), ["e4810603"])

    def test_no_transcript_no_job(self):
        self.assertEqual(session_state.transcript_jobs(SID, self.root / "none.jsonl"), [])

    def test_all_jobs_joins_both_records(self):
        session_state.add_job(SID, 42, "aaaa1111")
        self.add(_fire(self.ago(30), "bbbb2222"), _fire(self.ago(20), "aaaa1111"))
        self.assertEqual(session_state.all_jobs(SID, 42, self.path, self.now),
                         ["aaaa1111", "bbbb2222"])
        self.assertEqual(session_state.all_jobs(SID, 42, None, self.now), ["aaaa1111"])


class OwnShell(unittest.TestCase):
    """A session's shell is the direct parent of its Claude, never a shell further up."""

    def procs(self, claude_parent_image):
        """pid -> (parent pid, image): the hook, the shell Claude ran it in, Claude, Claude's
        parent, and a cmd above that."""
        return {os.getpid(): (10, "python.exe"), 10: (20, "cmd.exe"), 20: (30, "claude.exe"),
                30: (40, claude_parent_image), 40: (1, "cmd.exe")}

    def test_the_shell_claude_runs_in(self):
        self.assertEqual(session_state.owning_shell(self.procs("cmd.exe")), 30)

    def test_a_claude_a_program_started_has_no_shell(self):
        # The board's `claude -p` runs under python.exe, under the board's own cmd.
        self.assertEqual(session_state.owning_shell(self.procs("python.exe")), 0)

    def test_a_record_counts_its_shell_only_when_it_is_claudes_parent(self):
        self.assertEqual(session_state._own_shell({"shell": 30, "claude_parent": 30}), 30)
        self.assertEqual(session_state._own_shell({"shell": 30, "claude_parent": 20}), 0)
        self.assertEqual(session_state._own_shell({"shell": 30}), 0)


def session(mode="work", running=False, claude=1234, asks=False, reported="", tid="T0001",
            hooked=True, says_finished=False, dialog=False, jobs=(), why="", tasks=(),
            unsure=False, judge="", judge_why="", waits_for=(), judging=False, error="",
            judge_dead=False, stale=False, judge_from="", recap="", doing="", updated=None,
            starting=False):
    """A session entry as `live_sessions` gives it: its turn ended, unless it is `running`
    (wrote lately) or `stale` (wrote nothing for `STALE_SECONDS`)."""
    return {"id": tid, "mode": mode, "claude": claude, "ended": not running and not stale,
            "stale": stale, "asks": asks, "reported": reported, "reported_why": why,
            "error": error, "hooked": hooked, "starting": starting,
            "says_finished": says_finished, "dialog": dialog, "jobs": list(jobs),
            "tasks": list(tasks), "unsure": unsure, "judge": judge, "judge_why": judge_why,
            "judge_from": judge_from, "recap": recap, "waits_for": list(waits_for),
            "judging": judging, "judge_dead": judge_dead, "doing": doing,
            "updated": time.time() - 360 if updated is None and stale else updated}


# A plan a worker can carry out without asking.
PLAN = "## What is wrong\n\nx\n\n## Plan\n\n1. Edit core/engine/todo.py.\n2. Run the tests.\n"

# A verdict that proves itself on the message `keep` writes.
VERDICT_INPUT = '{"state": "input", "why": "x", "quote": "I changed the parser."}'

AGENT = {"id": "a563997732141d3f6", "type": "subagent", "description": "Probe B2 deliberate wait"}
SHELL = {"id": "b7", "type": "shell", "description": "python -c sleep"}


class WaitState(FixtureCase):
    """The owner's order (2026-10-18): exit, dialog, error, report; the finished line; a
    question on the last line; a job or own background work; a turn that wrote within 5
    minutes; the judge; the judge on the recap."""

    def setUp(self):
        super().setUp()
        self.root = self.make("Wait")
        self.tok = project.use(self.root)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def ticket(self, role, status_role, blocked_by=(), repeat="", body=None, difficulty="small"):
        return SimpleNamespace(id="T0001", state=todo.lane(role), status=todo.S(status_role),
                               blocked_by=list(blocked_by), repeat=repeat,
                               body=PLAN if body is None else body, difficulty=difficulty)

    def lanes(self):
        """A ticket in every lane, and no ticket."""
        return (self.ticket("start", "pipeline"), self.ticket("active", "implementing"),
                self.ticket("active", "ready to implement"),
                self.ticket("blocked", "blocked by user"), self.ticket("verify", "waiting"),
                self.ticket("closed", "done"), None)

    def wait(self, s, t):
        return todo.wait_state(s, t)[0]

    # -- step 0: the hard facts -------------------------------------------------------

    def test_claude_exited(self):
        for s in (session(claude=None, asks=True), session(claude=None, jobs=["e4810603"]),
                  session(claude=None, tasks=[AGENT]), session(claude=None, reported="event")):
            self.assertEqual(todo.wait_state(s, self.ticket("active", "implementing")),
                             ("finished", "Claude exited"))

    def test_a_question_before_the_first_turn_waits_for_input(self):
        # The folder trust question fires no hook and writes no transcript.
        for t in self.lanes():
            self.assertEqual(self.wait(session(hooked=False), t), "input")
        self.assertEqual(todo.wait_state(session(hooked=False, starting=True), None),
                         (None, "starts"))

    def test_a_dialog_waits_for_input_wherever_the_ticket_is(self):
        for t in self.lanes():
            for s in (session(dialog=True, jobs=["e4810603"]), session(dialog=True,
                                                                       running=True),
                      session(dialog=True, says_finished=True, tasks=[AGENT]),
                      session(dialog=True, reported="event")):
                self.assertEqual(self.wait(s, t), "input")

    def test_a_turn_that_ended_in_an_api_error_waits_for_input(self):
        for t in self.lanes():
            self.assertEqual(todo.wait_state(session(error="overloaded", tasks=[AGENT]), t),
                             ("input", "its turn ended in an API error (overloaded): "
                                       "try again"))

    def test_a_reported_event_waits_for_the_event(self):
        for t in self.lanes():
            self.assertEqual(
                todo.wait_state(session(reported="event", why="the editor restart", asks=True,
                                        jobs=["e4810603"]), t),
                ("event", "waits for the editor restart"))
        self.assertEqual(todo.wait_state(session(reported="event"), None),
                         ("event", "waits for an outside event"))

    def test_the_finished_line_ends_a_reported_event(self):
        # T0086: the report outlives the turn; the finished line of that turn closes it.
        for t in (self.ticket("verify", "waiting"), self.ticket("closed", "done")):
            self.assertEqual(
                todo.wait_state(session(reported="event", why="the editor restart",
                                        says_finished=True), t),
                ("finished", "said WORK IS FINISHED"))

    def test_a_reported_question_waits_for_input_until_the_finished_line(self):
        # T0171: the finished line outranks a question reported earlier in the turn.
        t = self.ticket("closed", "done")
        self.assertEqual(todo.wait_state(session(reported="input", why="Which lane?",
                                                 jobs=["e4810603"]), t),
                         ("input", "reported: Which lane?"))
        self.assertEqual(todo.wait_state(session(reported="input", why="Which glyph?",
                                                 says_finished=True), t),
                         ("finished", "said WORK IS FINISHED"))

    # -- step 1: the finished line ----------------------------------------------------

    def test_the_finished_line_finishes_where_the_ticket_agrees(self):
        done = {"work": (self.ticket("verify", "waiting"), self.ticket("closed", "done"),
                         self.ticket("blocked", "blocked by other task", ["T0002"])),
                "plan": (self.ticket("active", "ready to implement"),
                         self.ticket("verify", "waiting")),
                "review": (self.ticket("start", "pipeline"), self.ticket("verify", "waiting"),
                           self.ticket("active", "implementing")),
                "verify": (self.ticket("closed", "done"), self.ticket("start", "pipeline"))}
        for mode, tickets in done.items():
            for t in tickets:
                self.assertEqual(todo.wait_state(session(mode=mode, says_finished=True), t),
                                 ("finished", "said WORK IS FINISHED"), (mode, t.state))
        self.assertEqual(todo.wait_state(session(says_finished="T0001 is in Verification"),
                                         self.ticket("verify", "waiting")),
                         ("finished", "said T0001 is in Verification"))
        self.assertEqual(self.wait(session(reported="finished", tid=None), None), "finished")
        self.assertEqual(self.wait(session(says_finished=True, mode=""), None), "finished")

    def test_the_finished_line_and_a_finished_report_finish_over_undone_work(self):
        # T0171: the ticket is only a note in the signals, never a state.
        undone = {"work": (self.ticket("active", "waiting"),
                           self.ticket("active", "implementing"),
                           self.ticket("start", "pipeline"),
                           self.ticket("blocked", "blocked by user")),
                  "plan": (self.ticket("active", "planning"), self.ticket("start", "pipeline")),
                  "review": (self.ticket("active", "investigating"),),
                  "verify": (self.ticket("verify", "testing"),)}
        for mode, tickets in undone.items():
            for t in tickets:
                for s in (session(mode=mode, says_finished=True),
                          session(mode=mode, reported="finished")):
                    self.assertEqual(self.wait(s, t), "finished", (mode, t.state, t.status))
        t = self.ticket("active", "waiting")
        s = session(says_finished=True)
        s["wait"], s["why"] = todo.wait_state(s, t)
        self.assertEqual((s["wait"], s["why"]), ("finished", "said WORK IS FINISHED"))
        self.assertIn(["ticket check", "T0001 is in %s waiting" % todo.lane("active")],
                      todo.wait_signals(s, t))

    def test_a_plan_that_leaves_something_open_is_only_a_note(self):
        # T0171: `plan_open` decides no state; the finished line finishes the planner.
        opened = {PLAN + "3. The glyph: TBD.\n": "the plan leaves open: 3. The glyph: TBD.",
                  PLAN + "3. Which eight row bytes make the glyph?\n":
                      "the plan leaves open: 3. Which eight row bytes make the glyph?",
                  PLAN + "\n### Open questions\n\n- the name of the lane\n":
                      "the plan leaves open: Open questions",
                  "## What is wrong\n\nx\n": "the ticket has no ## Plan"}
        for body, why in opened.items():
            t = self.ticket("active", "ready to implement", body=body)
            self.assertEqual(todo.plan_open(t), why, body)
            for s in (session(mode="plan", says_finished=True),
                      session(mode="plan", judge="finished", judge_why="planned")):
                self.assertEqual(self.wait(s, t), "finished", body)
        t = self.ticket("active", "ready to implement", difficulty="unknown")
        self.assertEqual(todo.plan_open(t), "its difficulty is still unknown")
        self.assertEqual(todo.wait_state(session(mode="plan", says_finished=True), t),
                         ("finished", "said WORK IS FINISHED"))
        # Nothing open, or the open-questions line says there are none: the plan is done.
        for body in (PLAN, PLAN + "Open questions: none.\n"):
            t = self.ticket("active", "ready to implement", body=body)
            self.assertEqual(self.wait(session(mode="plan", says_finished=True), t),
                             "finished")
        # A question below the plan, in another section, is not the plan's.
        t = self.ticket("active", "ready to implement", body=PLAN + "\n## Notes\n\nWhy?\n")
        self.assertEqual(self.wait(session(mode="plan", says_finished=True), t), "finished")

    def test_the_finished_line_outranks_a_job_and_background_work(self):
        # The owner (2026-10-18): the finished line wins over a cron job.
        t = self.ticket("blocked", "blocked by other task", ["T0002"])
        for s in (session(says_finished=True, jobs=["e4810603"]),
                  session(says_finished=True, tasks=[AGENT]),
                  session(says_finished=True, waits_for=["T0002"], judge="working")):
            self.assertEqual(self.wait(s, t), "finished")

    def test_a_manager_never_finishes_while_its_claude_runs(self):
        t = self.ticket("active", "implementing", repeat="manager")
        for s in (session(says_finished=True), session(reported="finished"),
                  session(judge="finished"), session(judging=True)):
            self.assertEqual(todo.wait_state(s, t),
                             ("idle", "manager: waits for the owner's orders"))
        self.assertEqual(self.wait(session(asks=True), t), "input")
        self.assertEqual(self.wait(session(jobs=["e4810603"]), t), "subagent")
        self.assertEqual(self.wait(session(claude=None), t), "finished")

    def test_a_turn_that_runs_has_no_finished_line_yet(self):
        self.assertEqual(self.wait(session(running=True, says_finished=True),
                                   self.ticket("verify", "waiting")), None)

    # -- step 2: a question on the last line ------------------------------------------

    def test_a_question_on_the_last_line_waits_for_input_whatever_it_waits_for(self):
        # The relay exception is gone: the owner reads the question.
        t = self.ticket("blocked", "blocked by other task", ["T0033"])
        s = session(asks=True, jobs=["e4810603"], tasks=[AGENT], waits_for=["T0033"])
        s["question"] = "hunger first or chance?"
        self.assertEqual(todo.wait_state(s, t), ("input", "asked: hunger first or chance?"))
        self.assertEqual(self.wait(session(asks=True, judge="working"), t), "input")
        self.assertEqual(self.wait(session(asks=True, running=True), t), None)

    # -- step 3: a job, or its own background work -----------------------------------

    def test_a_job_or_background_work_is_working(self):
        for t in (self.ticket("active", "implementing"), self.ticket("verify", "waiting"),
                  None):
            self.assertEqual(todo.wait_state(session(jobs=["e4810603"]), t),
                             ("subagent", "its scheduled job e4810603"))
            self.assertEqual(todo.wait_state(session(tasks=[AGENT]), t),
                             ("subagent", "its own 1 subagent: Probe B2 deliberate wait"))
        self.assertEqual(todo.wait_state(session("plan", tasks=[AGENT, AGENT, SHELL]), None)[1],
                         "its own 2 subagents, 1 background shell")
        # Its turn runs while it holds the job: still working, and the board names `doing`.
        self.assertEqual(self.wait(session(jobs=["e4810603"], running=True), None),
                         "subagent")

    def test_a_job_of_a_ticket_that_waits_for_tickets_is_a_blocker(self):
        t = self.ticket("blocked", "blocked by other task", ["T0002"])
        self.assertEqual(todo.wait_state(session(jobs=["e4810603"]), t),
                         ("blocker", "blocked by T0002; its scheduled job e4810603"))
        self.assertEqual(todo.wait_state(session(tasks=[AGENT], waits_for=["T0003", "T0004"]),
                                         self.ticket("active", "implementing")),
                         ("blocker", "waits for T0003, T0004; its own 1 subagent: Probe B2 "
                                     "deliberate wait"))

    # -- step 4: a turn that runs -----------------------------------------------------

    def test_a_turn_that_wrote_lately_runs_wherever_the_ticket_is(self):
        for t in self.lanes():
            self.assertEqual(todo.wait_state(session(running=True, doing="Bash: tests"), t),
                             (None, ""))
            self.assertEqual(self.wait(session(running=True, judge="finished",
                                               waits_for=["T0002"]), t), None)

    # -- step 5: the judge -----------------------------------------------------------

    def test_the_judge_decides_an_ended_turn_that_no_rule_reads(self):
        for t in (self.ticket("active", "implementing"), self.ticket("verify", "waiting")):
            self.assertEqual(todo.wait_state(session(judge="input", judge_why="asks which"), t),
                             ("input", "judge: asks which"))
            self.assertEqual(todo.wait_state(session(judge="finished", judge_why="done"), t),
                             ("finished", "judge: done"))
            self.assertEqual(todo.wait_state(session(judge="working", judge_why="the build"),
                                             t),
                             ("subagent", "judge: still works: the build"))
        self.assertEqual(todo.wait_state(session(judge="working", judge_why="T0002",
                                                 waits_for=["T0002"]),
                                         self.ticket("active", "implementing")),
                         ("blocker", "waits for T0002; judge: still works: T0002"))

    def test_the_judge_never_finishes_a_session_with_no_ticket(self):
        cli = session("cli", tid=None, judge="finished", judge_why="done")
        self.assertEqual(todo.wait_state(cli, None),
                         ("idle", "judge: finished: done; no ticket, so it stays open"))
        self.assertEqual(self.wait(dict(cli, judge="input"), None), "input")

    def test_no_verdict_yet_is_idle_wherever_the_ticket_is(self):
        # The owner (2026-10-18): the ticket's lane decides no state.
        for t in self.lanes():
            self.assertEqual(todo.wait_state(session(judging=True), t),
                             ("idle", "asked nothing; the judge reads its last message"))
            self.assertEqual(todo.wait_state(session(unsure=True), t),
                             ("idle", "asked nothing; no rule reads its last message"))
            self.assertEqual(todo.wait_state(session(judging=True, judge_dead=True), t),
                             ("idle", "the judge left no verdict"))

    def test_a_stale_turn_goes_to_the_judge(self):
        t = self.ticket("active", "implementing")
        self.assertEqual(todo.wait_state(session(stale=True, judging=True), t),
                         (None, "no update for 6 min; the judge reads its last message"))
        self.assertEqual(todo.wait_state(session(stale=True), t), (None, "no update for 6 min"))
        self.assertEqual(self.wait(session(stale=True, judge="input"), t), "input")
        self.assertEqual(self.wait(session(stale=True, judge="working"), t), "subagent")
        self.assertEqual(self.wait(session(stale=True, judge="finished"), t), "finished")
        self.assertEqual(todo.wait_state(session(stale=True, judging=True, judge_dead=True), t),
                         ("idle", "no update for 6 min; the judge left no verdict"))
        self.assertEqual(todo.wait_state(session(stale=True, judge="unsure", judge_why="w"), t),
                         ("idle", "no update for 6 min; the judge is unsure: w"))

    # -- step 6: the recap -----------------------------------------------------------

    def test_an_unsure_judge_reads_the_recap(self):
        t = self.ticket("active", "implementing")
        for recap in ("sent", "reading"):
            self.assertEqual(todo.wait_state(session(judge="unsure", recap=recap), t),
                             ("idle", "the judge is unsure of its last message; it reads the "
                                      "recap"))
        self.assertEqual(todo.wait_state(session(judge="unsure", judge_why="w",
                                                 recap="missing"), t),
                         ("idle", "the judge is unsure; no recap came: w"))
        self.assertEqual(self.wait(session(judge="unsure", recap="dead"), t), "idle")
        self.assertEqual(todo.wait_state(session(judge="finished", judge_why="all done",
                                                 judge_from="recap"), t),
                         ("finished", "recap judge: all done"))
        self.assertEqual(self.wait(session(judge="input", judge_from="recap"), t), "input")
        self.assertEqual(todo.wait_state(session(judge="unsure", judge_why="w",
                                                 judge_from="recap"), t),
                         ("idle", "the judge is unsure, of its recap too: w"))

    def test_close_never_ends_the_process_that_asks(self):
        s = {"key": "board", "running": False, "shell": os.getpid(), "claude": None}
        with self.assertRaises(ValueError):
            todo.close_session(s)

    def close(self, exits_alone):
        """close_session on a shell 900 that runs Claude 901 under it, in a console 902.
        Returns the pids taskkill got and the (pid, code) terminate got."""
        import winproc
        procs = {os.getpid(): (1, "python.exe"), 900: (5, "cmd.exe"),
                 901: (900, "claude.exe"), 902: (900, "conhost.exe")}
        killed, ended = [], []
        waits = iter([exits_alone, True])
        with mock.patch.object(winproc, "processes", return_value=procs), \
                mock.patch.object(winproc, "taskkill",
                                  side_effect=lambda pid, **kw: killed.append(pid) or 0), \
                mock.patch.object(winproc, "wait_exit", side_effect=lambda pid, s: next(waits)), \
                mock.patch.object(winproc, "terminate",
                                  side_effect=lambda pid, code: ended.append((pid, code))):
            rc = todo.close_session({"key": "T0001/work", "running": False,
                                     "shell": 900, "claude": 901})
        return rc, killed, ended

    def test_close_ends_claude_and_lets_the_shell_exit_with_0(self):
        # A killed shell exits with 1 and Windows Terminal keeps its tab; a `cmd /c` shell
        # whose Claude is gone runs the reset and exits with 0.
        self.assertEqual(self.close(exits_alone=True), (0, [901], []))

    def test_close_ends_a_shell_that_stays_with_exit_code_0(self):
        self.assertEqual(self.close(exits_alone=False), (0, [901], [(900, 0)]))

    @unittest.skipUnless(os.name == "nt", "the cmd line of Windows; test_posix covers /bin/sh")
    def test_a_launched_shell_exits_after_claude(self):
        line = todo._shell_line(["claude", "--session-id", SID], "T - T0001 - work")
        self.assertTrue(line.endswith("--reset-terminal"))
        for terminal in ("wt", "cmd"):
            with mock.patch("subprocess.Popen") as popen:
                self.assertEqual(todo._open_terminal(line, "t", terminal), 0)
            argv = popen.call_args[0][0]
            argv = argv if isinstance(argv, str) else " ".join(argv)
            self.assertIn("cmd /c ", argv)
            self.assertNotIn("/k", argv)

    @unittest.skipUnless(os.name == "nt", "Windows Terminal; test_posix covers Terminal.app")
    def test_a_wt_session_opens_a_window_of_its_own(self):
        with mock.patch("subprocess.Popen") as popen:
            self.assertEqual(todo._open_terminal("x", "t", "wt"), 0)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[:3], ["wt", "-w", "new"])
        self.assertNotIn("focus-tab", argv)

    @unittest.skipUnless(os.name == "nt", "Windows' console windows; test_posix covers macOS")
    def test_focus_raises_the_window_of_the_session_console(self):
        import winproc
        s = {"key": "T0001/work", "id": "T0001", "mode": "work", "shell": 10, "claude": 11,
             "own": False, "title": "Wait - T0001 - work", "terminal": None}
        wins = [(500, winproc.WT_WINDOW_CLASS, "another tab's title", 1)]
        with mock.patch.object(todo, "_console_io", return_value={"hwnd": 500}), \
                mock.patch.object(winproc, "windows", return_value=wins), \
                mock.patch.object(winproc, "raise_window", return_value=True) as raised, \
                mock.patch.object(winproc, "find_terminal_window") as by_title, \
                mock.patch("subprocess.run") as run:
            self.assertTrue(todo.focus_session(s))
        raised.assert_called_once_with(500)
        by_title.assert_not_called()
        run.assert_not_called()
        # No window for the console: the title finds it.
        with mock.patch.object(todo, "_console_io", return_value={"hwnd": 0}), \
                mock.patch.object(winproc, "windows", return_value=wins), \
                mock.patch.object(winproc, "raise_window", return_value=True) as raised, \
                mock.patch.object(winproc, "find_terminal_window", return_value=700), \
                mock.patch("subprocess.run") as run:
            self.assertTrue(todo.focus_session(s))
        raised.assert_called_once_with(700)
        run.assert_not_called()

    def test_live_sessions_carry_no_tab(self):
        import session_state
        import winproc
        sid = todo.launch_session_id("T0001", "work", 1)
        t = SimpleNamespace(id="T0001", run=1, repeat="", sessions=[], blocked_by=[],
                            parent=None, state=todo.lane("active"),
                            status=todo.S("implementing"))
        procs = {100: (50, "cmd.exe"), 50: (1, "openconsole.exe")}
        with mock.patch.object(winproc, "processes", return_value=procs), \
                mock.patch.object(winproc, "command_lines",
                                  return_value={100: "claude --session-id %s" % sid}), \
                mock.patch.object(winproc, "created_at", return_value=0), \
                mock.patch.object(session_state, "live_records", return_value={}), \
                mock.patch.object(todo, "pty_records", return_value={}):
            found = todo.live_sessions([t])
        self.assertEqual([s["key"] for s in found], ["T0001/work"])
        self.assertTrue(found[0]["tabbed"])
        self.assertNotIn("tab", found[0])

    def test_finished_sessions_keeps_only_finished(self):
        ss = [{"key": "a", "wait": None}, {"key": "b", "wait": "input"},
              {"key": "c", "wait": "blocker"}, {"key": "d", "wait": "finished"},
              {"key": "e", "wait": "event"}, {"key": "f", "wait": "subagent"}]
        self.assertEqual([s["key"] for s in todo.finished_sessions(ss)], ["d"])


class ReadTurn(FixtureCase):
    """`read_turn`: the transcript and the hook files of one session, the judge and the
    recap. No process starts: `Popen` and `send_session` are mocks."""

    def setUp(self):
        super().setUp()
        self.root = self.make("ReadTurn")
        self.tok = project.use(self.root)
        self.path = self.tmp / "s.jsonl"
        p = mock.patch.object(todo, "session_transcript", return_value=self.path)
        p.start()
        self.addCleanup(p.stop)
        self.popen = mock.patch("subprocess.Popen").start()
        self.addCleanup(mock.patch.stopall)
        self.send = mock.patch.object(todo, "send_session", return_value=8).start()

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    @staticmethod
    def iso(t):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")

    def write(self, rows, age=0):
        self.path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        then = time.time() - age
        os.utime(self.path, (then, then))

    def said(self, text, ago, stop="end_turn"):
        return {"type": "assistant", "timestamp": self.iso(time.time() - ago), "message": {
            "stop_reason": stop, "content": [{"type": "text", "text": text}]}}

    def call(self, ago, **tin):
        return {"type": "assistant", "timestamp": self.iso(time.time() - ago), "message": {
            "stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t1",
                                                    "name": "Bash", "input": tin}]}}

    def decide(self, ticket=None, **kw):
        s = {"id": "T0001", "mode": "work", "session": SID, "mark_shell": 42, "claude": 7,
             "hooked": True, "waits_for": []}
        s.update(kw)
        t = ticket or SimpleNamespace(id="T0001", state=todo.lane("active"),
                                      status=todo.S("implementing"), blocked_by=[], repeat="")
        todo.read_turn(s)
        s["wait"], s["why"] = todo.wait_state(s, t)
        return s

    def judge_argv(self):
        return [c[0][0] for c in self.popen.call_args_list]

    def test_a_turn_that_wrote_a_minute_ago_runs_and_names_what_it_does(self):
        self.write([self.call(60, description="Run the tests", command="python -m unittest")],
                   age=60)
        s = self.decide()
        self.assertEqual((s["wait"], s["doing"]), (None, "Bash: Run the tests"))
        self.assertFalse(s["ended"])
        self.assertFalse(self.popen.called)

    def result(self, ago, tool_id="t1"):
        return {"type": "user", "timestamp": self.iso(time.time() - ago), "message": {
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}}

    def test_a_tool_call_with_no_result_for_ten_minutes_runs_and_no_judge_reads_it(self):
        self.write([self.call(600, description="Wait 590 s", command="sleep 590")], age=600)
        s = self.decide()
        self.assertFalse(s["stale"])
        self.assertEqual((s["wait"], s["calling"]), (None, "Bash: Wait 590 s"))
        self.assertTrue(s["why"].startswith("no update for 10 min; a tool call runs: "
                                            "Bash: Wait 590 s, since "), s["why"])
        self.assertFalse(self.popen.called)

    def test_a_turn_that_wrote_nothing_for_six_minutes_goes_to_the_judge(self):
        self.write([self.said("Running the long build.", 400, "tool_use"),
                    self.call(380, command="make all"), self.result(360)], age=360)
        s = self.decide()
        self.assertTrue(s["stale"])
        self.assertEqual(s["wait"], None)
        self.assertEqual(s["why"], "no update for 6 min; the judge reads its last message")
        self.assertEqual(self.popen.call_count, 1)
        stamp, doing = session_state.stale_turn(SID, 42)
        self.assertEqual(doing, "Bash: make all")
        self.assertEqual(session_state.turn_stamp(SID, 42), stamp)
        self.assertEqual(session_state._last_path(SID, 42).read_text(encoding="utf-8"),
                         "Running the long build.")
        # Once per stamp; its verdict counts while the transcript stands still.
        self.decide()
        self.assertEqual(self.popen.call_count, 1)
        session_state._judge_path(SID, 42).write_text("%s\nworking\nthe build\n" % stamp,
                                                      encoding="utf-8")
        self.assertEqual(self.decide()["wait"], "subagent")

    def test_a_stale_turn_that_wrote_no_text_runs_and_no_judge_reads_it(self):
        self.write([self.call(380, command="make all"), self.result(360)], age=360)
        s = self.decide()
        self.assertTrue(s["stale"])
        self.assertEqual((s["wait"], s["why"]), (None, "no update for 6 min"))
        self.assertFalse(self.popen.called)

    def test_a_turn_that_ended_in_an_old_usage_limit_is_that_error_not_a_verdict(self):
        # The reported case (T0084): a plan session stopped at the weekly limit 11 days ago;
        # the board's `/recap` removed its marker, and the judge read the error text as now.
        ended = time.time() - 11 * 86400
        error = {"type": "assistant", "timestamp": self.iso(ended), "isApiErrorMessage": True,
                 "error": "rate_limit", "apiErrorStatus": 429,
                 "apiErrorParams": {"rate_limit_info": {"resetsAt": int(ended) + 3600,
                                                        "rateLimitType": "seven_day"}},
                 "message": {"model": "<synthetic>", "stop_reason": "stop_sequence",
                             "content": [{"type": "text", "text":
                                          "You've hit your weekly limit · resets 5pm"}]}}
        self.write([self.said("Asking the design questions.", 11 * 86400 + 60), error,
                    {"type": "user", "isCompactSummary": True,
                     "message": {"content": "This session is being continued..."}},
                    {"type": "user", "message": {"content":
                                                 "<command-name>/recap</command-name>"}}],
                   age=3600)
        when = session_state._stamp(self.iso(ended))
        session_state.mark_stop(SID, 42, "You've hit your weekly limit · resets 5pm", when)
        session_state._judge_path(SID, 42).write_text(
            "%s\nworking\nSession hit rate limit; waiting for automatic reset\n"
            % session_state.turn_stamp(SID, 42), encoding="utf-8")
        s = self.decide()
        limit = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ended) + 3600))
        at = time.strftime("%Y-%m-%d %H:%M", time.localtime(when))
        self.assertEqual((s["wait"], s["why"]), (
            "input", "its turn ended in an API error at %s (rate_limit, limit until %s): "
                     "try again" % (at, limit)))
        self.assertEqual(s["judge"], "")
        self.assertFalse(session_state._judge_path(SID, 42).exists())
        self.assertFalse(self.popen.called)

    def test_an_ended_turn_with_a_plain_report_goes_to_the_judge_at_once(self):
        self.write([self.said("I changed the parser.", 2)], age=2)
        session_state.mark_stop(SID, 42, "I changed the parser.")
        s = self.decide()
        self.assertEqual((s["wait"], s["why"]),
                         ("idle", "asked nothing; the judge reads its last message"))
        self.assertEqual(self.popen.call_count, 1)

    def test_an_unsure_judge_types_recap_once_and_judges_the_recap(self):
        last = self.said("I changed the parser.", 5)
        self.write([last], age=5)
        session_state.mark_stop(SID, 42, "I changed the parser.")
        stamp = session_state.turn_stamp(SID, 42)
        session_state._judge_path(SID, 42).write_text("%s\nunsure\nopen\n" % stamp,
                                                      encoding="utf-8")
        s = self.decide()
        self.assertEqual((s["recap"], s["wait"]), ("sent", "idle"))
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args[0][1:], ("/recap", ["Enter"]))
        self.decide()
        self.assertEqual(self.send.call_count, 1)
        # The recap stands in the transcript: the judge reads it, once.
        sent = session_state.recap_record(SID, 42)["sent"]
        self.write([last,
                    {"type": "system", "subtype": "local_command", "timestamp": self.iso(sent + 1),
                     "commandRun": {"command": "recap"},
                     "content": "<local-command-stdout>It waits for T0002."
                                "</local-command-stdout>"}],
                   age=5)
        s = self.decide()
        self.assertEqual(s["recap"], "reading")
        self.assertIn("--recap", self.judge_argv()[-1])
        self.assertEqual(session_state.recap_record(SID, 42)["text"], "It waits for T0002.")
        self.decide()
        self.assertEqual(sum("--recap" in a for a in self.judge_argv()), 1)
        # Its verdict decides.
        session_state._judge_path(SID, 42).write_text(
            "%s\nworking\nwaits for T0002\nrecap\n" % stamp, encoding="utf-8")
        s = self.decide()
        self.assertEqual((s["wait"], s["why"], s["recap"]),
                         ("subagent", "recap judge: still works: waits for T0002", ""))

    def test_no_recap_within_its_wait_is_idle_and_reload_asks_again(self):
        self.write([self.said("I changed the parser.", 5)], age=5)
        session_state.mark_stop(SID, 42, "I changed the parser.")
        stamp = session_state.turn_stamp(SID, 42)
        session_state._judge_path(SID, 42).write_text("%s\nunsure\nopen\n" % stamp,
                                                      encoding="utf-8")
        self.decide()
        rec = session_state.recap_record(SID, 42)
        rec["sent"] -= session_state.RECAP_WAIT + 1
        session_state._recap_path(SID, 42).write_text(json.dumps(rec), encoding="utf-8")
        s = self.decide()
        self.assertEqual((s["recap"], s["wait"], s["why"]),
                         ("missing", "idle", "the judge is unsure; no recap came: open"))
        self.assertTrue(session_state.rejudge(SID, 42))
        self.decide()
        self.assertEqual(self.send.call_count, 2)

    def test_a_turn_that_began_without_a_prompt_drops_the_old_marker(self):
        # A scheduled prompt fired after the turn the marker ended: no hook removed it.
        session_state.mark_stop(SID, 42, "Checked.\nWORK IS FINISHED", when=time.time() - 120)
        self.write([self.said("Checked.\nWORK IS FINISHED", 121),
                    {"type": "user", "timestamp": self.iso(time.time() - 30),
                     "message": {"content": "Run the check on T0002."}},
                    self.call(20, command="python .octopus/todo.py show T0002")], age=20)
        s = self.decide()
        self.assertEqual(s["wait"], None)
        self.assertFalse(s["says_finished"])
        self.assertFalse(session_state._marker(SID, 42).exists())

    def test_a_missed_stop_is_written_from_the_transcript(self):
        self.write([self.said("Done.\nWORK IS FINISHED", 60)], age=60)
        s = self.decide(SimpleNamespace(id="T0001", state=todo.lane("verify"),
                                        status=todo.S("waiting"), blocked_by=[], repeat=""))
        self.assertTrue(s["ended"])
        self.assertEqual((s["wait"], s["why"]), ("finished", "said WORK IS FINISHED"))
        # A moment after the turn, the Stop hook still writes its own.
        session_state.mark_busy(SID, 42)
        self.write([self.said("Done.\nWORK IS FINISHED", 1)], age=1)
        self.assertEqual(self.decide()["wait"], None)


class Reevaluate(FixtureCase):
    """The owner's Reload: every session decided again, with the signals behind its state."""

    def setUp(self):
        super().setUp()
        self.root = self.make("Reeval")
        self.tok = project.use(self.root)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def started_judge(self, age, shell=42):
        session_state.mark_idle(SID, shell)
        with mock.patch("subprocess.Popen"):
            session_state.start_judge(SID, shell, "I changed the parser.", self.root)
        then = time.time() - age
        os.utime(session_state._judging_path(SID, shell), (then, then))

    def test_a_judge_that_died_without_a_verdict_starts_again(self):
        self.started_judge(session_state.JUDGE_STALE + 5)
        self.assertTrue(session_state.judge_started(SID, 42))
        self.assertTrue(session_state.rejudge(SID, 42))
        self.assertFalse(session_state.judge_started(SID, 42))
        with mock.patch("subprocess.Popen") as popen:
            self.assertTrue(session_state.ensure_judged(SID, 42, self.root))
        self.assertEqual(popen.call_count, 1)

    def test_a_judge_still_in_its_time_or_with_a_verdict_is_kept(self):
        self.started_judge(5)
        self.assertFalse(session_state.rejudge(SID, 42))
        self.assertTrue(session_state.judge_started(SID, 42))
        self.started_judge(session_state.JUDGE_STALE + 5)
        session_state._judge_path(SID, 42).write_text(
            "%s\nfinished\nit says so\n" % session_state._marker_time(SID, 42),
            encoding="utf-8")
        self.assertFalse(session_state.rejudge(SID, 42))
        self.assertFalse(session_state.rejudge("1f0e0d0c-0b0a-4908-8706-050403020100", 42))

    def signals(self, s, t=None):
        s.setdefault("session", SID)
        s.setdefault("mark_shell", 42)
        s["wait"], s["why"] = todo.wait_state(s, t)
        return dict(todo.wait_signals(s, t))

    def ticket(self, role, status_role):
        return SimpleNamespace(id="T0001", state=todo.lane(role), status=todo.S(status_role),
                               blocked_by=[], repeat="")

    def test_a_running_session_names_the_stop_marker_that_never_came(self):
        s = session(running=True, doing="Bash: Run the tests")
        s["updated"] = s["doing_since"] = time.time() - 30
        sig = self.signals(s, self.ticket("verify", "waiting"))
        self.assertEqual(sig["Stop marker"], "none since the last prompt")
        self.assertEqual(sig["turn"], "runs")
        self.assertEqual(sig["last update"], "30 s ago")
        self.assertEqual(sig["doing"], "Bash: Run the tests (30 s ago)")
        self.assertEqual(sig["judge"], "not needed: the turn runs")
        self.assertEqual(todo.state_text(dict(s, running=True)),
                         "running: Bash: Run the tests")
        self.assertEqual(sig["ticket"], "%s / waiting" % todo.P.STATE_LABEL[todo.lane("verify")])
        self.assertEqual(sig["decided"], "running")
        self.assertIn("transcript", sig)

    def test_an_idle_session_names_the_judge_that_has_no_verdict(self):
        s = session(judging=True)
        s["idle_since"] = time.time() - 300
        sig = self.signals(s, self.ticket("active", "implementing"))
        self.assertEqual(sig["Stop marker"], "written 5 min ago")
        self.assertEqual(sig["last message"], "no rule reads it")
        self.assertEqual(sig["judge"], "started, no verdict yet")
        self.assertTrue(sig["decided"].startswith("idle: asked nothing"))
        s["judging"], s["rejudged"] = True, True
        self.assertEqual(self.signals(s, self.ticket("active", "implementing"))["judge"],
                         "the last one left no verdict: started again")
        s = session(stale=True, judge="unsure", judge_why="open", recap="missing")
        sig = self.signals(s, self.ticket("active", "implementing"))
        self.assertEqual(sig["turn"], "runs, no update for 6 min: stale")
        self.assertEqual(sig["judge"], "unsure: open")
        self.assertEqual(sig["recap"], "sent, none came")

    def test_each_rule_names_what_it_read(self):
        s = session(says_finished="WORK IS FINISHED", reported="event", why="an editor restart",
                    jobs=["j1"], tasks=[AGENT])
        s["idle_since"] = time.time()
        sig = self.signals(s, self.ticket("verify", "waiting"))
        self.assertEqual(sig["last message"], "says WORK IS FINISHED")
        self.assertEqual(sig["judge"], "not needed: a rule decides")
        self.assertEqual(sig["recap"], "not needed")
        self.assertEqual(sig["report"], "event: an editor restart")
        self.assertEqual(sig["jobs"], "j1")
        self.assertEqual(sig["background work"], "1 subagent: Probe B2 deliberate wait")
        self.assertEqual(sig["decided"], "finished: said WORK IS FINISHED")
        s = session(judge="finished", judge_why="the ticket is in Verification")
        s["idle_since"] = time.time()
        sig = self.signals(s, self.ticket("verify", "waiting"))
        self.assertEqual(sig["judge"], "finished: the ticket is in Verification")
        sig = self.signals(session(claude=None, hooked=False), None)
        self.assertEqual(sig["Claude"], "exited")
        self.assertEqual(sig["hooks"], "none has fired under this Claude")
        self.assertEqual(sig["ticket"], "none")
        self.assertEqual(sig["decided"], "finished: Claude exited")

    def scan(self, recheck):
        import winproc
        sid = todo.launch_session_id("T0001", "work", 1)
        t = SimpleNamespace(id="T0001", run=1, repeat="", sessions=[], blocked_by=[],
                            parent=None, state=todo.lane("active"),
                            status=todo.S("implementing"))
        procs = {100: (50, "cmd.exe"), 50: (1, "openconsole.exe"), 200: (100, "claude.exe")}
        session_state.mark_idle(sid, 100)
        with mock.patch.object(winproc, "processes", return_value=procs), \
                mock.patch.object(winproc, "command_lines",
                                  return_value={100: "claude --session-id %s" % sid}), \
                mock.patch.object(winproc, "created_at", return_value=0), \
                mock.patch.object(session_state, "live_records",
                                  return_value={sid: {"claude": 200, "shell": 100}}), \
                mock.patch.object(session_state, "rejudge", return_value=True) as rejudge, \
                mock.patch.object(session_state, "ensure_judged", return_value=True), \
                mock.patch.object(todo, "pty_records", return_value={}):
            found = todo.live_sessions([t], recheck=recheck)
        return found[0], rejudge

    def test_only_a_recheck_retries_the_judge(self):
        s, rejudge = self.scan(False)
        self.assertFalse(rejudge.called)
        self.assertFalse(s["rejudged"])
        self.assertEqual(dict(s["signals"])["decided"], "idle: " + s["why"])
        s, rejudge = self.scan(True)
        self.assertEqual(rejudge.call_count, 1)
        self.assertTrue(s["rejudged"])
        self.assertEqual(dict(s["signals"])["judge"],
                         "the last one left no verdict: started again")


if __name__ == "__main__":
    unittest.main()
