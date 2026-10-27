"""The reply check of the reporting rule (`core/hooks/reply_check.py`): it never blocks a turn,
and the sentences that fail reach the agent with its next prompt.
python -m unittest discover tests"""

import json
import sys
import unittest
from unittest import mock

import support

sys.path.insert(0, str(support.OCTOPUS / "core" / "hooks"))

import reply_check as rc  # noqa: E402
import status_reminder as sr  # noqa: E402

MESSAGE = ("greeting.txt now says hello world.\n"
           "The edit took 14 seconds.\n"
           "Not tested: whether the Mac build reads the file.\n"
           "Which language comes next, English or German?\n\n"
           "WORK IS FINISHED")
ANSWER = json.dumps({"parts": ["fix the greeting"], "fails": [
    {"sentence": "The edit took 14 seconds.", "why": "measurement"},
    {"sentence": "Not tested: whether the Mac build reads the file.", "why": "caveat"},
    {"sentence": "Which language comes next, English or German?", "why": "offer"},
    {"sentence": "WORK IS FINISHED", "why": "closing"},
    {"sentence": "A sentence the message does not hold.", "why": "made up"}]})


class ReplyCheck(support.FixtureCase):

    def test_codex_check_uses_the_project_provider_model_and_executable(self):
        import providers
        root = self.make("Codex reply", config={"provider": "codex", "providers": {
            "codex": {"command": "C:/Tools/Codex/codex.exe", "defaultModel": "gpt-6-luna",
                      "defaultEffort": "high"}}})
        with mock.patch.object(providers, "answer", return_value=ANSWER) as answer:
            fails = rc.check(MESSAGE, "Fix the greeting.", cwd=root)
        self.assertEqual(len(fails), 2)
        spec, _prompt, model, effort, _timeout = answer.call_args.args
        self.assertEqual(spec["id"], "codex")
        self.assertEqual(spec["command"], "C:/Tools/Codex/codex.exe")
        self.assertEqual((model, effort), ("gpt-6-luna", "high"))
        self.assertEqual(spec["env"][rc.ENV], "1")

    def test_claude_check_keeps_its_small_model_and_a_failure_gives_no_note(self):
        import providers
        root = self.make("Claude reply", config={"models": {"haiku": "claude-haiku-test"}})
        with mock.patch.object(providers, "answer", side_effect=ValueError("no answer")) as answer:
            self.assertEqual(rc.check(MESSAGE, "Fix the greeting.", cwd=root), [])
        self.assertEqual(answer.call_args.args[0]["id"], "claude")
        self.assertEqual(answer.call_args.args[2], "claude-haiku-test")

    def test_only_quoted_unprotected_sentences_fail(self):
        fails = rc.check(MESSAGE, "Fix the greeting.", ask=lambda _p: ANSWER)
        self.assertEqual([s for s, _w in fails], ["The edit took 14 seconds.",
                                                  "Not tested: whether the Mac build reads "
                                                  "the file."])

    def test_a_question_and_the_finished_line_are_never_checked(self):
        self.assertFalse(rc.worth_checking("Which lane, Pipeline or Blocked?"))
        self.assertFalse(rc.worth_checking("WORK IS FINISHED"))
        self.assertTrue(rc.protected("T0002 is in Verification."))

    def test_the_note_waits_for_the_next_prompt_once(self):
        note = self.tmp / "s.replynote"
        rc.write_note(note, [("The edit took 14 seconds.", "measurement")])
        text = rc.take_note(note)
        self.assertIn('"The edit took 14 seconds."', text)
        self.assertEqual(rc.take_note(note), "")
        rc.write_note(note, [("x.", "")], now=0)
        self.assertEqual(rc.take_note(note), "")

    def test_a_launch_prompt_asks_for_what_its_brief_asks(self):
        req = rc.launch_request("Work ticket T0001. Read TODO/.briefs/T0001.md first.")
        self.assertIn("WORK IS FINISHED", req)
        self.assertEqual(rc.launch_request("Fix it."), "Fix it.")

    def test_codex_stop_never_blocks_and_the_next_prompt_carries_the_note(self):
        base = self.tmp / "clock"

        def ev(name, **kw):
            return sr.handle("codex", dict({"session_id": "s1", "hook_event_name": name}, **kw),
                             base=base, ask=lambda _p: ANSWER)

        self.assertIsNone(ev("UserPromptSubmit", prompt="Fix the greeting."))
        self.assertEqual(ev("Stop", last_assistant_message=MESSAGE), {})
        out = ev("UserPromptSubmit", prompt="Next.")
        self.assertIn("Not tested: whether the Mac build",
                      out["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(ev("UserPromptSubmit", prompt="Again."))


if __name__ == "__main__":
    unittest.main()
