"""Groups in both stores, and parents with children.   python -m unittest discover tests"""

import json
import unittest

from support import FixtureCase


class FileGroups(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Topics",
                              config={"groups": {"store": "files", "required": True}})

    def test_a_ticket_needs_a_group_and_the_group_needs_a_file(self):
        rc, _out, err = self.todo(self.root, "new", "--title", "no group")
        self.assertEqual(rc, 2)
        self.assertIn("needs --group", err)
        rc, out, err = self.todo(self.root, "group", "items", "--title", "Items")
        self.assertEqual(rc, 0, err)
        self.assertTrue((self.root / "TODO" / "groups" / "items.md").exists())
        tid = self.new_ticket(self.root, "with group", "--group", "items")
        rc, out, _ = self.todo(self.root, "groups")
        self.assertIn("items", out)
        self.assertIn("Items", out)
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out)
        index = (self.root / "TODO" / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("| `items` | 1 | Items |", index)
        self.assertIn("| Group |", index)
        rc, out, _ = self.todo(self.root, "group", "items", "--rename", "things")
        self.assertIn("1 ticket(s) rewritten", out)
        rc, out, _ = self.todo(self.root, "show", tid)
        self.assertIn("group: things", out)

    def test_check_fails_an_open_ticket_in_no_group(self):
        self.todo(self.root, "group", "items", "--title", "Items")
        tid = self.new_ticket(self.root, "grouped", "--group", "items")
        path = next((self.root / "TODO" / "Pipeline").glob(tid + "-*.md"))
        text = path.read_text(encoding="utf-8").replace("group: items\n", "")
        path.write_text(text, encoding="utf-8")
        self.todo(self.root, "index")
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("is in no group", out)

    def test_delete_refuses_while_open_tickets_name_the_group(self):
        self.todo(self.root, "group", "a", "--title", "A")
        self.todo(self.root, "group", "b", "--title", "B")
        tid = self.new_ticket(self.root, "in a", "--group", "a")
        rc, _out, err = self.todo(self.root, "group", "a", "--delete")
        self.assertEqual(rc, 2)
        self.assertIn("open ticket(s) name group 'a'", err)
        rc, out, err = self.todo(self.root, "group", "a", "--delete", "--into", "b")
        self.assertEqual(rc, 0, err)
        rc, out, _ = self.todo(self.root, "query", "--group", "b", "--ids")
        self.assertEqual(out.strip(), tid)


class MapGroups(FixtureCase):
    def test_map_groups_and_an_auto_group(self):
        root = self.make("Mapped",
                         config={"autoGroups": [{"name": "Found by tests", "tag": "from-test"}]})
        t1 = self.new_ticket(root, "one", "--tags", "from-test")
        t2 = self.new_ticket(root, "two")
        rc, out, err = self.todo(root, "groups", "--set", "Early", t1, t2)
        self.assertEqual(rc, 0, err)
        text = (root / "TODO" / "GROUPS.md").read_text(encoding="utf-8")
        self.assertIn("## Early (2)", text)
        self.assertIn("## Found by tests (1)", text)
        rc, out, _ = self.todo(root, "groups", "--table")
        self.assertIn("| Found by tests | 1 | tag `from-test`", out)
        rc, out, _ = self.todo(root, "groups", "--run", "Found by tests", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("Work ticket %s" % t1, out)


class Children(FixtureCase):
    def test_children_and_wait_follow_the_lanes(self):
        root = self.make("Family")
        parent = self.new_ticket(root, "parent")
        kid = self.new_ticket(root, "kid", "--parent", parent)
        rc, out, _ = self.todo(root, "children", parent)
        self.assertEqual(rc, 1)
        self.assertIn("1 still open", out)
        rc, out, _ = self.todo(root, "query", "--parents", "--ids")
        self.assertEqual(out.strip(), parent)
        rc, out, _ = self.todo(root, "query", "--roots", "--ids")
        self.assertEqual(out.strip(), parent)
        self.todo(root, "move", kid, "Verification")
        rc, out, _ = self.todo(root, "wait", parent, "--interval", "1", "--timeout", "5")
        self.assertEqual(rc, 0, out)
        rc, out, _ = self.todo(root, "show", parent)
        self.assertIn("## Children", out)

    def test_rulesets_prints_what_each_ruleset_adds(self):
        root = self.make("Rules", ["unreal", "blueprint"])
        rc, out, err = self.todo(root, "rulesets")
        self.assertEqual(rc, 0, err)
        self.assertIn("Rules (rules)   .octopus/project.json", out)
        self.assertIn("unreal", out)
        self.assertIn("testerPermits pie, editor", " ".join(out.split()))


if __name__ == "__main__":
    unittest.main()
