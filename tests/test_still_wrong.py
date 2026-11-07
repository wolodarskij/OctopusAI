"""A finished ticket never says that something is still wrong.   python -m unittest discover tests"""

import unittest

from support import FixtureCase

import todo  # noqa: E402


class StillWrongPhrase(unittest.TestCase):
    def test_a_remaining_defect_is_found(self):
        for text in ("The map page still does not turn", "The zoom is not fixed here",
                     "Known limit: no undo", "The board works partly",
                     "Export does not work yet", "The parser still fails on tabs",
                     "Two cases remain open"):
            self.assertTrue(todo.still_wrong(text), text)

    def test_what_works_is_no_defect(self):
        for text in ("The board shows the outcome line",
                     "Git no longer tracks files that name a directory on disk",
                     "The game loads with no console errors",
                     "A refused peek no longer opens the view; before, it still opened it"):
            self.assertEqual(todo.still_wrong(text), "", text)


class StillWrongMove(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Sample Game")
        self.id = self.new_ticket(self.root, "Feature: The board shows a line")

    def path(self):
        return next((self.root / "TODO").rglob("%s-*.md" % self.id))

    def test_move_to_verification_refuses_a_still_wrong_outcome(self):
        rc, _out, err = self.todo(self.root, "move", self.id, "Verification", "--outcome",
                                  "The line shows; the colour still does not change")
        self.assertEqual(rc, 2, err)
        self.assertIn("still wrong", err)
        self.assertNotIn("Verification", str(self.path().parent.name))

    def test_move_to_verification_refuses_a_still_wrong_result(self):
        p = self.path()
        p.write_text(p.read_text(encoding="utf-8")
                     + "\n## Result\n\n- The colour is not fixed here.\n", encoding="utf-8")
        rc, _out, err = self.todo(self.root, "move", self.id, "Verification", "--outcome",
                                  "The board shows the line")
        self.assertEqual(rc, 2, err)
        self.assertIn("## Result", err)
        self.assertNotIn("outcome:", self.path().read_text(encoding="utf-8"))

    def test_a_failed_verification_may_say_what_is_wrong(self):
        rc, _out, err = self.todo(self.root, "move", self.id, "Verification", "--outcome",
                                  "The board shows the line")
        self.assertEqual(rc, 0, err)
        rc, _out, err = self.todo(self.root, "move", self.id, "Pipeline", "--outcome",
                                  "The line still does not show")
        self.assertEqual(rc, 0, err)

    def test_outcome_refuses_a_still_wrong_line_on_a_finished_ticket(self):
        rc, _out, err = self.todo(self.root, "move", self.id, "Verification", "--outcome",
                                  "The board shows the line")
        self.assertEqual(rc, 0, err)
        rc, _out, err = self.todo(self.root, "outcome", self.id, "The colour is not done yet")
        self.assertEqual(rc, 2, err)


if __name__ == "__main__":
    unittest.main()
