"""An AI session's `move` to Verification is refused while the model names a part of the
ticket's order, in the order's own words, that the work did not do.
python -m unittest discover tests"""

import contextlib
import io
import os
import unittest
from unittest import mock

from support import FixtureCase

import todo  # noqa: E402

ORDER = "Rewrite the page Docs/Guide.md and publish it."


class OrderCheck(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Sample Game")
        self.id = self.new_ticket(self.root, "Documentation: The guide names the board command",
                                  "--description", ORDER)
        p = self.path()
        p.write_text(p.read_text(encoding="utf-8")
                     + "\n## Result\n\n- This ticket did not change the guide.\n",
                     encoding="utf-8")

    def path(self):
        return next((self.root / "TODO").rglob("%s-*.md" % self.id))

    def move(self, answers, ai=True):
        """`todo.main move <id> Verification` in this process, with the model's answers
        stubbed. Returns (rc, err, the prompts the model got)."""
        prompts, answers = [], list(answers)

        def ask(prompt):
            prompts.append(prompt)
            return answers.pop(0) if answers else ""

        err = io.StringIO()
        env = {"CLAUDECODE": "1"} if ai else {}
        with mock.patch.object(todo, "_ask_dup_model", ask), \
                mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            if not ai:
                os.environ.pop("OCTOPUS_SESSION", None)
            rc = todo.main(["--project", str(self.root), "move", self.id, "Verification",
                            "--outcome", "The guide names the board command"])
        return rc, err.getvalue(), prompts

    def test_an_undone_part_refuses_the_move(self):
        rc, err, prompts = self.move(['{"undone": [{"part": "publish it", '
                                      '"why": "the result says the guide did not change"}]}'])
        self.assertEqual(rc, 2, err)
        self.assertEqual(len(prompts), 1)
        self.assertIn(ORDER, prompts[0])
        self.assertIn("did not change the guide", prompts[0])
        self.assertIn("publish it", err)
        self.assertNotEqual(self.path().parent.name, "Verification")

    def test_a_part_not_in_the_order_is_asked_again_then_the_move_goes_on(self):
        wrong = '{"undone": [{"part": "deploy the server", "why": "-"}]}'
        rc, err, prompts = self.move([wrong, wrong])
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(prompts), 2)
        self.assertIn("deploy the server", prompts[1])
        self.assertEqual(self.path().parent.name, "Verification")

    def test_every_part_done_moves(self):
        rc, err, prompts = self.move(['{"undone": []}'])
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(self.path().parent.name, "Verification")

    def test_the_owners_shell_asks_no_model(self):
        rc, err, prompts = self.move([], ai=False)
        self.assertEqual(rc, 0, err)
        self.assertEqual(prompts, [])


if __name__ == "__main__":
    unittest.main()
