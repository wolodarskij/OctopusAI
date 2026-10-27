"""The ticket category, which replaced `kind`.   python -m unittest discover tests"""

import json
import unittest

from support import FixtureCase, project


class Category(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Cats")

    def ticket_file(self, tid):
        return next((self.root / "TODO").rglob("%s-*.md" % tid))

    def query(self, *argv):
        rc, out, err = self.todo(self.root, "query", "--json", *argv)
        self.assertEqual(rc, 0, err)
        return {r["id"]: r for r in json.loads(out)}

    def test_defaults_and_derivation_from_the_source(self):
        p = project.load(self.root)
        self.assertEqual(p.CATEGORIES, ("project code", "internal tooling"))
        self.assertEqual(p.problems, [])
        tool = self.new_ticket(self.root, "tooling one", "--source", "TODO/x.md")
        code = self.new_ticket(self.root, "code one", "--source", "src/a.py")
        rows = self.query()
        self.assertEqual(rows[tool]["category"], "internal tooling")
        self.assertEqual(rows[code]["category"], "project code")
        rc, out, _ = self.todo(self.root, "query", "--category", "internal tooling", "--ids")
        self.assertEqual(out.strip(), tool)
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out)

    def test_a_project_adds_a_category(self):
        cfg = self.root / ".octopus" / "project.json"
        d = json.loads(cfg.read_text(encoding="utf-8"))
        d["categories"] = {"art": "the textures and the models"}
        cfg.write_text(json.dumps(d), encoding="utf-8")
        p = project.load(self.root)
        self.assertEqual(p.CATEGORIES, ("project code", "internal tooling", "art"))
        tid = self.new_ticket(self.root, "a texture", "--category", "art")
        self.assertIn("category: art", self.ticket_file(tid).read_text(encoding="utf-8"))
        rc, out, _ = self.todo(self.root, "query", "--category", "art", "--ids")
        self.assertEqual(out.strip(), tid)
        rc, out, _ = self.todo(self.root, "list", "--category", "art")
        self.assertIn(tid, out)
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out)
        rc, _out, err = self.todo(self.root, "new", "--title", "x", "--category", "nope")
        self.assertEqual(rc, 2)
        self.assertIn("not a category", err)

    def test_an_unknown_category_fails_check(self):
        tid = self.new_ticket(self.root, "odd")
        f = self.ticket_file(tid)
        f.write_text(f.read_text(encoding="utf-8").replace(
            "title: odd", "title: odd\ncategory: nonsense"), encoding="utf-8")
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("category 'nonsense'", out)

    def test_a_legacy_kind_reads_as_its_category_and_fails_check(self):
        tid = self.new_ticket(self.root, "old one")
        f = self.ticket_file(tid)
        f.write_text(f.read_text(encoding="utf-8").replace(
            "title: old one", "title: old one\nkind: tooling"), encoding="utf-8")
        self.assertEqual(self.query()[tid]["category"], "internal tooling")
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("old field `kind`", out)

    def test_migrate_rewrites_tickets_and_config_once(self):
        a = self.new_ticket(self.root, "old tooling")
        b = self.new_ticket(self.root, "old code")
        for tid, kind in ((a, "tooling"), (b, "code")):
            f = self.ticket_file(tid)
            f.write_text(f.read_text(encoding="utf-8").replace(
                "severity:", "kind: %s\nseverity:" % kind), encoding="utf-8")
        cfg = self.root / ".octopus" / "project.json"
        d = json.loads(cfg.read_text(encoding="utf-8"))
        d["kinds"] = {"code": "the product", "tooling": "the tools"}
        d["queryFields"] = ["id", "kind", "title"]
        cfg.write_text(json.dumps(d), encoding="utf-8")
        self.assertTrue(any("migrate" in x for x in project.load(self.root).problems))

        rc, out, err = self.todo(self.root, "migrate")
        self.assertEqual(rc, 0, err)
        self.assertIn("4 change(s)", out)
        text_a = self.ticket_file(a).read_text(encoding="utf-8")
        self.assertIn("category: internal tooling", text_a)
        self.assertNotIn("kind:", text_a)
        self.assertIn("category: project code", self.ticket_file(b).read_text(encoding="utf-8"))
        d = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertNotIn("kinds", d)
        self.assertEqual(d["categories"], {"project code": "the product",
                                           "internal tooling": "the tools"})
        self.assertEqual(d["queryFields"], ["id", "category", "title"])
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out)

        rc, out, _ = self.todo(self.root, "migrate")
        self.assertEqual(rc, 0)
        self.assertIn("0 change(s)", out)


if __name__ == "__main__":
    unittest.main()
