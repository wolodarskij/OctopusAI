"""An AI session's `new` refuses a ticket that looks like an open ticket, `evidence` adds to
the open ticket, and `check` lists the open pairs that look like duplicates.
python -m unittest discover tests"""

import unittest

from support import FixtureCase

AI = {"CLAUDECODE": "1"}


class Duplicates(FixtureCase):
    def setUp(self):
        super().setUp()
        self.a = self.make("Alpha")
        self.first = self.new_ticket(self.a, "Bug: The parser drops the last line of a file",
                                     "--source", "src/parser.py:40")

    def ai_new(self, title, *extra):
        return self.todo(self.a, "new", "--title", title, "--severity", "Low", *extra, env=AI)

    def test_the_same_file_is_refused_and_listed(self):
        rc, _out, err = self.ai_new("Bug: The parser reads a tab as two spaces",
                                    "--source", "src\\parser.py:90")
        self.assertEqual(rc, 2)
        self.assertIn(self.first, err)
        self.assertIn("same file src/parser.py", err)
        self.assertFalse(list((self.a / "TODO").rglob("T0002-*.md")))

    def test_a_close_title_is_refused(self):
        rc, _out, err = self.ai_new("Bug: The parser drops the last line of every file")
        self.assertEqual(rc, 2)
        self.assertIn("close title", err)

    def test_a_different_ticket_is_filed(self):
        rc, _out, err = self.ai_new("Bug: The board loses its scroll position",
                                    "--source", "src/board.py")
        self.assertEqual(rc, 0, err)

    def test_the_owner_shell_is_not_refused(self):
        rc, _out, err = self.todo(self.a, "new", "--title", "Bug: The parser reads a tab",
                                  "--source", "src/parser.py")
        self.assertEqual(rc, 0, err)

    def test_the_override_names_the_tickets_and_says_why(self):
        rc, _out, err = self.ai_new("Bug: The parser reads a tab as two spaces",
                                    "--source", "src/parser.py", "--not-duplicate", self.first)
        self.assertEqual(rc, 2)
        self.assertIn("--why", err)
        rc, _out, err = self.ai_new("Bug: The parser reads a tab as two spaces",
                                    "--source", "src/parser.py", "--not-duplicate", self.first,
                                    "--why", "a tab is not a line end")
        self.assertEqual(rc, 0, err)
        text = next((self.a / "TODO").rglob("T0002-*.md")).read_text(encoding="utf-8")
        self.assertIn("not-duplicate: %s" % self.first, text)
        self.assertIn("## Not a duplicate\n\n- %s: a tab is not a line end" % self.first, text)

    def test_an_override_that_misses_a_ticket_is_refused(self):
        other = self.new_ticket(self.a, "Bug: The parser leaks a file handle",
                                "--source", "src/parser.py")
        rc, _out, err = self.ai_new("Bug: The parser reads a tab as two spaces",
                                    "--source", "src/parser.py", "--not-duplicate", self.first,
                                    "--why", "a tab is not a line end")
        self.assertEqual(rc, 2)
        self.assertIn(other, err)
        self.assertNotIn("%s  [" % self.first, err)

    def test_evidence_goes_under_evidence(self):
        rc, _out, err = self.todo(self.a, "evidence", self.first, "src/parser.py:41 also")
        self.assertEqual(rc, 0, err)
        rc, _out, err = self.todo(self.a, "evidence", self.first, "a second run")
        self.assertEqual(rc, 0, err)
        text = next((self.a / "TODO").rglob("%s-*.md" % self.first)).read_text(encoding="utf-8")
        body = text.split("## Evidence", 1)[1]
        self.assertRegex(body, r"^\n\n- \d{4}-\d\d-\d\d: src/parser.py:41 also\n"
                               r"- \d{4}-\d\d-\d\d: a second run\n")

    def test_check_lists_a_pair_and_link_settles_it(self):
        second = self.new_ticket(self.a, "Bug: The parser drops the last line of each file")
        rc, out, err = self.todo(self.a, "check")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("%s and %s: close title" % (self.first, second), out)
        rc, _out, err = self.todo(self.a, "link", second, "--not-duplicate", self.first,
                                  "--why", "a different reader")
        self.assertEqual(rc, 0, err)
        rc, out, _err = self.todo(self.a, "check")
        self.assertNotIn("look like duplicates", out)

    def test_check_skips_the_same_file_with_different_titles(self):
        self.new_ticket(self.a, "Bug: The board loses its scroll position",
                        "--source", "src/parser.py")
        rc, out, err = self.todo(self.a, "check")
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("look like duplicates", out)


if __name__ == "__main__":
    unittest.main()
