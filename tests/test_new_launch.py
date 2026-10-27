"""`new --launch` files a ticket and starts its session, and a brief that holds `projects`
tells its session to do so in every project.   python -m unittest discover tests"""

import unittest

from support import FixtureCase


class NewLaunch(FixtureCase):
    def setUp(self):
        super().setUp()
        self.a = self.make("Alpha", "none")
        self.b = self.make("Beta", "none")

    def launch(self, root, *extra):
        rc, out, err = self.todo(root, "new", "--title", "Feature: a follow-up",
                                 "--severity", "Medium", "--launch", "--dry-run", *extra)
        self.assertEqual(rc, 0, err)
        return out

    def test_a_sized_ticket_launches_work(self):
        out = self.launch(self.a, "--difficulty", "small")
        self.assertIn("T0001", out.splitlines()[0])
        self.assertIn("title   Alpha - T0001 - Feature: a follow-up - work", out)

    def test_an_unknown_ticket_launches_plan_in_auto_mode(self):
        out = self.launch(self.a)
        self.assertIn("title   Alpha - T0001 - Feature: a follow-up - plan", out)
        self.assertIn("--permission-mode auto", out)

    def test_another_project_through_project(self):
        out = self.launch(self.b, "--difficulty", "medium")
        self.assertIn("  in      %s" % self.b.resolve(), out)
        self.assertTrue(list((self.b / "TODO" / "Pipeline").glob("T0001-*.md")))
        self.assertFalse(list((self.a / "TODO").rglob("T0001-*.md")))

    def test_the_note_reaches_the_brief(self):
        self.launch(self.a, "--difficulty", "small", "--note", "what the parent changed")
        brief = (self.a / "TODO" / ".briefs" / "T0001.md").read_text(encoding="utf-8")
        self.assertIn("> what the parent changed", brief)

    def test_launch_refuses_a_lane_other_than_the_start_lane(self):
        rc, _out, err = self.todo(self.a, "new", "--title", "Feature: x", "--launch",
                                  "--state", "Verification")
        self.assertEqual(rc, 2)
        self.assertIn("--launch", err)
        self.assertFalse(list((self.a / "TODO").rglob("T0001-*.md")))

    def test_without_launch_nothing_starts(self):
        rc, out, err = self.todo(self.a, "new", "--title", "Feature: x")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("title ", out)
        self.assertFalse((self.a / "TODO" / ".briefs" / "T0001.md").exists())


class ProjectsBrief(FixtureCase):
    def setUp(self):
        super().setUp()
        self.a = self.make("Alpha", "none")
        self.make("Beta", "none")

    def brief(self, *extra):
        tid = self.new_ticket(self.a, "Feature: change every project", *extra)
        rc, out, err = self.todo(self.a, "brief", tid)
        self.assertEqual(rc, 0, err)
        return (self.a / out.strip()).read_text(encoding="utf-8")

    def test_a_ticket_with_projects_gets_the_section_and_the_table(self):
        brief = self.brief("--permits", "projects")
        self.assertIn("## A change for every project reaches every project", brief)
        self.assertIn("--launch", brief)
        self.assertIn('todo --project "Beta"', brief)
        self.assertNotIn("{projects}", brief)

    def test_a_ticket_without_projects_does_not(self):
        brief = self.brief()
        self.assertNotIn("A change for every project", brief)
        self.assertNotIn('todo --project "Beta"', brief)

    def test_the_manager_gets_its_own_order_and_not_the_section(self):
        brief = self.brief("--repeat", "manager", "--permits", "projects")
        self.assertIn("A change for every project runs in every project", brief)
        self.assertNotIn("## A change for every project reaches every project", brief)


if __name__ == "__main__":
    unittest.main()
