"""An AI session's `new` refuses a ticket only when the model takes it for an open ticket's
defect or request, `evidence` adds to the open ticket, and `check` lists the pairs the model
confirms.   python -m unittest discover tests"""

import contextlib
import io
import json
import os
import unittest
from unittest import mock

from support import FixtureCase

import todo  # noqa: E402

PARSER = "Bug: The parser drops the last line of a file"
PARSER_TEXT = "parse_file() returns every line but the last when the file ends without a newline."


class Duplicates(FixtureCase):
    def setUp(self):
        super().setUp()
        self.a = self.make("Alpha")
        self.first = self.new_ticket(self.a, PARSER, "--source", "src/parser.py:40",
                                     "--description", PARSER_TEXT)
        self.new_ticket(self.a, "Feature: The board shows a clock", "--source", "src/board.py")

    def run_todo(self, *argv, answer=None, ai=True):
        """`todo.main` in this process, with the model's answer stubbed. Returns (rc, out, err,
        the prompts the model got)."""
        prompts = []

        def ask(prompt):
            prompts.append(prompt)
            if isinstance(answer, Exception):
                raise answer
            return answer

        out, err = io.StringIO(), io.StringIO()
        env = {"CLAUDECODE": "1"} if ai else {}
        with mock.patch.object(todo, "_ask_dup_model", ask), \
                mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = todo.main(["--project", str(self.a)] + list(argv))
        return rc, out.getvalue(), err.getvalue(), prompts

    def ai_new(self, title, *extra, answer=None):
        return self.run_todo("new", "--title", title, "--severity", "Low", *extra, answer=answer)

    def filed(self, tid):
        return list((self.a / "TODO").rglob("%s-*.md" % tid))

    def test_the_same_source_file_alone_asks_nothing_and_files(self):
        rc, _out, err, prompts = self.ai_new("Feature: The parser reads tabs as four spaces",
                                             "--source", "src/parser.py:90")
        self.assertEqual(rc, 0, err)
        self.assertEqual(prompts, [])
        self.assertTrue(self.filed("T0003"))

    def test_the_model_verdict_same_refuses(self):
        rc, _out, err, prompts = self.ai_new(
            "Bug: The parser loses the last line of every file",
            "--description", "The last line is dropped when no newline ends the file.",
            answer='{"same": ["%s"], "why": "both name the lost last line"}' % self.first)
        self.assertEqual(rc, 2)
        self.assertEqual(len(prompts), 1)
        self.assertIn("OPEN ticket %s" % self.first, prompts[0])
        self.assertNotIn("board shows a clock", prompts[0])
        self.assertIn(self.first, err)
        self.assertIn("both name the lost last line", err)
        self.assertFalse(self.filed("T0003"))

    def test_the_model_verdict_different_files(self):
        rc, _out, err, prompts = self.ai_new(
            "Bug: The parser drops the first line of a file", answer='{"same": [], "why": "-"}')
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(prompts), 1)
        self.assertTrue(self.filed("T0003"))

    def test_a_model_fault_files_the_ticket(self):
        rc, _out, err, _prompts = self.ai_new(
            "Bug: The parser drops the last line of every file",
            answer=ValueError("Claude Code did not answer in 120 seconds"))
        self.assertEqual(rc, 0, err)
        self.assertIn("no verdict", err)
        self.assertTrue(self.filed("T0003"))

    def test_an_id_the_model_invents_refuses_nothing(self):
        rc, _out, err, _prompts = self.ai_new(
            "Bug: The parser drops the last line of every file", answer='{"same": ["T0999"]}')
        self.assertEqual(rc, 0, err)

    def test_the_owner_shell_asks_nothing(self):
        rc, _out, err, prompts = self.run_todo("new", "--title", PARSER, ai=False)
        self.assertEqual(rc, 0, err)
        self.assertEqual(prompts, [])

    def test_the_override_names_the_tickets_and_says_why(self):
        rc, _out, err, _p = self.ai_new(PARSER, "--not-duplicate", self.first)
        self.assertEqual(rc, 2)
        self.assertIn("--why", err)
        rc, _out, err, prompts = self.ai_new(PARSER, "--not-duplicate", self.first,
                                             "--why", "a different reader")
        self.assertEqual(rc, 0, err)
        self.assertEqual(prompts, [])
        text = self.filed("T0003")[0].read_text(encoding="utf-8")
        self.assertIn("not-duplicate: %s" % self.first, text)
        self.assertIn("## Not a duplicate\n\n- %s: a different reader" % self.first, text)

    def test_evidence_goes_under_evidence(self):
        rc, _out, err, _p = self.run_todo("evidence", self.first, "src/parser.py:41 also")
        self.assertEqual(rc, 0, err)
        rc, _out, err, _p = self.run_todo("evidence", self.first, "a second run")
        self.assertEqual(rc, 0, err)
        body = self.filed(self.first)[0].read_text(encoding="utf-8").split("## Evidence", 1)[1]
        self.assertRegex(body, r"^\n\n- \d{4}-\d\d-\d\d: src/parser.py:41 also\n"
                               r"- \d{4}-\d\d-\d\d: a second run\n")

    def test_check_lists_the_pairs_the_model_confirms_and_keeps_the_verdict(self):
        second = self.new_ticket(self.a, "Bug: The parser drops the last line of each file")
        pair = '{"pairs": [{"a": "%s", "b": "%s", "same": true}]}' % (self.first, second)
        rc, out, err, prompts = self.run_todo("check", answer=pair)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(prompts), 1)
        self.assertIn("PAIR %s %s" % (self.first, second), prompts[0])
        self.assertIn("name the same defect or request", out)
        rc, out, _err, prompts = self.run_todo("check", answer=ValueError("not asked"))
        self.assertEqual(prompts, [])
        self.assertIn("name the same defect or request", out)
        rc, _out, err, _p = self.run_todo("link", second, "--not-duplicate", self.first,
                                          "--why", "a different reader")
        self.assertEqual(rc, 0, err)
        rc, out, _err, prompts = self.run_todo("check")
        self.assertEqual(prompts, [])
        self.assertNotIn("same defect", out)

    def test_check_lists_nothing_the_model_calls_different(self):
        second = self.new_ticket(self.a, "Bug: The parser drops the last line of each file")
        pair = json.dumps({"pairs": [{"a": self.first, "b": second, "same": False}]})
        rc, out, err, _p = self.run_todo("check", answer=pair)
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("same defect", out)


if __name__ == "__main__":
    unittest.main()
