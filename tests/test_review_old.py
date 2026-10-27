"""`review-old`: the tickets a Board management sweep reviews.   python -m unittest discover tests"""

import datetime
import re
import unittest

from support import FixtureCase, project

import todo  # noqa: E402  (support puts core/engine on the path)


def _ago(days):
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


class ReviewOld(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Sweep", "none")
        self.ids = {}
        for name in ("old", "young", "blocked", "repeat", "reviewed", "active"):
            self.ids[name] = self.new_ticket(self.root, "%s ticket" % name)
        self.todo(self.root, "move", self.ids["blocked"], "Blocked")
        self.todo(self.root, "repeat", self.ids["repeat"], "test")
        self.todo(self.root, "move", self.ids["active"], "InProgress")
        ages = {"old": 30, "young": 1, "blocked": 20, "repeat": 30, "reviewed": 30,
                "active": 30}
        for name, days in ages.items():
            extra = {"created": _ago(days)}
            if name == "reviewed":
                extra["sessions"] = "review 0000-review %s 10:00" % _ago(2)
            self._set(self.ids[name], extra)

    def _set(self, tid, fields):
        tok = project.use(self.root)
        try:
            t = todo.find(todo.load_all(), tid)
            meta = dict(t.meta, **fields)
            todo._write(t.path, todo.render(meta, t.body))
        finally:
            project.reset(tok)

    def _picked(self, days=5):
        tok = project.use(self.root)
        try:
            return [t.id for t in todo.old_tickets(todo.load_all(), days)]
        finally:
            project.reset(tok)

    def test_takes_old_start_and_blocked_tickets_oldest_first(self):
        self.assertEqual(self._picked(), [self.ids["old"], self.ids["blocked"]])

    def test_a_review_older_than_days_no_longer_skips(self):
        self.assertIn(self.ids["reviewed"], self._picked(days=1))

    def test_an_open_session_skips(self):
        tok = project.use(self.root)
        try:
            live = [{"id": self.ids["old"]}]
            picked = [t.id for t in todo.old_tickets(todo.load_all(), 5, live)]
        finally:
            project.reset(tok)
        self.assertNotIn(self.ids["old"], picked)
        self.assertIn(self.ids["blocked"], picked)

    def test_list_and_dry_run_launch_nothing(self):
        rc, out, err = self.todo(self.root, "review-old", "list", "--days", "5")
        self.assertEqual(rc, 0, err)
        self.assertIn("2 ticket(s) older than 5 day(s)", out)
        rc, out, err = self.todo(self.root, "review-old", "run", "--days", "5", "--dry-run")
        self.assertEqual(rc, 0, err)
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertEqual(len(lines), 2, out)
        self.assertTrue(all(re.match(r"T\d{4}  would review  ", l) for l in lines), out)
        rc, out, err = self.todo(self.root, "show", self.ids["old"])
        self.assertIn("[Pipeline]", out.splitlines()[0])
        self.assertNotIn("launched: review", out)


if __name__ == "__main__":
    unittest.main()
