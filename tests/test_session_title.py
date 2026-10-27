"""A session's name carries a short ticket title.   python -m unittest discover tests"""

import re
import unittest
from unittest import mock

from support import FixtureCase, project

import todo  # noqa: E402  (support puts core/engine on the path)


class SessionTitle(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Alpha", "none")

    def title_of(self, ticket_title, mode="work"):
        tid = self.new_ticket(self.root, ticket_title)
        tok = project.use(self.root)
        try:
            return tid, todo.session_title(todo.find(todo.load_all(), tid), mode)
        finally:
            project.reset(tok)

    def test_short_title_is_kept_whole(self):
        tid, name = self.title_of("Fix the parser")
        self.assertEqual(name, "Alpha - %s - Fix the parser - work" % tid)

    def test_long_title_is_cut_on_a_word_boundary(self):
        long = ("Re-run the full game kit against every level, then compare the results "
                "with the last green run")
        tid, name = self.title_of(long, "plan")
        short = name[len("Alpha - %s - " % tid):-len(" - plan")]
        self.assertLessEqual(len(short), 40)
        self.assertTrue(long.startswith(short), short)
        self.assertEqual(long[len(short)], " ")
        self.assertNotRegex(short, r"[ .,;:!?-]$")
        self.assertTrue(name.endswith(" - plan"))

    def test_quotes_and_shell_characters_are_stripped(self):
        tid, name = self.title_of('Say "hi" & quit | 100% <now> ^done')
        self.assertIsNone(re.search(r'["&|^%<>]', name), name)
        self.assertEqual(name, "Alpha - %s - Say hi quit 100 now done - work" % tid)

    def test_id_and_mode_stay_fields_of_their_own(self):
        tid, name = self.title_of("A - B - C")
        self.assertTrue(name.startswith("Alpha - %s - " % tid))
        self.assertTrue(name.endswith(" - work"))

    def test_scan_reads_the_name_back_off_the_shell_line(self):
        tid, name = self.title_of('Quote " and back\\slash')
        tok = project.use(self.root)
        try:
            t = todo.find(todo.load_all(), tid)
            line = todo._shell_line(todo._launch_argv(t, "work", None, False, False, None), name)
        finally:
            project.reset(tok)
        m = re.search(r'--remote-control\s+"([^"]+)"', line)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), name)

    def test_short_title_function(self):
        self.assertEqual(todo.short_title("Short one."), "Short one")
        self.assertEqual(todo.short_title(""), "")
        self.assertEqual(todo.short_title("x" * 60), "x" * 40)
        self.assertEqual(todo.short_title("word " * 20), "word word word word word word word word")

    def current(self, name):
        tok = project.use(self.root)
        try:
            return todo.current_title(name)
        finally:
            project.reset(tok)

    def test_old_name_gains_the_ticket_title(self):
        tid, name = self.title_of("Fix the parser")
        self.assertEqual(self.current("Alpha - %s - work" % tid), name)

    def test_new_name_comes_back_unchanged(self):
        tid, name = self.title_of("Fix the parser", "plan")
        self.assertEqual(self.current(name), name)

    def test_name_follows_a_changed_ticket_title(self):
        tid, name = self.title_of("Fix the parser")
        p = next((self.root / "TODO").rglob("%s-*.md" % tid))
        p.write_text(p.read_text(encoding="utf-8").replace(
            "title: Fix the parser", "title: Rewrite the lexer"), encoding="utf-8")
        self.assertEqual(self.current(name), "Alpha - %s - Rewrite the lexer - work" % tid)

    def test_other_names_come_back_unchanged(self):
        self.title_of("Fix the parser")
        for name in ("Alpha - T9999 - work", "Beta - T0001 - work", "Alpha - chat",
                     "Alpha - 0123abcd", "Alpha - T0001 - Fix the parser - chat"):
            self.assertEqual(self.current(name), name)

    def test_hook_retitles_an_old_shell_line(self):
        import session_state
        import winproc
        tid, name = self.title_of("Fix the parser")
        line = 'claude --remote-control "Alpha - %s - work" --model x' % tid
        tok = project.use(self.root)
        try:
            with mock.patch.object(winproc, "command_lines", return_value={42: line}):
                got = session_state.session_title("0123abcd-0000", 42)
        finally:
            project.reset(tok)
        self.assertEqual(got, name)


if __name__ == "__main__":
    unittest.main()
