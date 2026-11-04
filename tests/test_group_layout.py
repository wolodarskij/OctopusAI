"""Manual group order and nesting, separate from AI ticket assignments."""

import json
from contextlib import contextmanager
from unittest import mock

from support import FixtureCase, project

import todo as T
import todo_server as server


class GroupLayout(FixtureCase):
    @contextmanager
    def groups(self, store):
        root = self.make("Layout-" + store, config={"groups": {"store": store}})
        token = project.use(root)
        try:
            for name in ("alpha", "beta", "gamma", "delta"):
                T.group_create(name)
            yield root
        finally:
            project.reset(token)

    def test_manual_moves_keep_subtrees_and_sibling_order_in_both_stores(self):
        for store in ("map", "files"):
            with self.subTest(store=store), self.groups(store) as root:
                self.assertEqual(T.load_group_layout()["parents"], {})
                server.act_group({"op": "move", "name": "gamma", "before": "alpha", "manual": True})
                self.assertEqual(T.load_group_layout()["order"], ["gamma", "alpha", "beta", "delta"])
                T.group_move("beta", "alpha", manual=True)
                T.group_move("gamma", "alpha", "beta", manual=True)
                T.group_move("delta", "beta", manual=True)
                T.group_move("alpha", before=None, manual=True)
                saved = json.loads((root / "TODO" / ".group-layout.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["parents"], {"beta": "alpha", "gamma": "alpha", "delta": "beta"})
                self.assertLess(saved["order"].index("gamma"), saved["order"].index("beta"))
                T.group_move("beta", manual=True)
                self.assertEqual(T.load_group_layout()["parents"], {"gamma": "alpha", "delta": "beta"})
                with mock.patch.object(T, "live_sessions", return_value=[]):
                    self.assertEqual(server.snapshot()["groupLayout"], T.load_group_layout())

    def test_invalid_moves_change_neither_layout_nor_membership(self):
        for store in ("map", "files"):
            with self.subTest(store=store), self.groups(store) as root:
                T.group_move("beta", "alpha", manual=True)
                T.group_move("gamma", "beta", manual=True)
                path = root / "TODO" / ".group-layout.json"
                before = path.read_bytes()
                invalid = [
                    {"name": "alpha", "parent": "gamma", "manual": True},
                    {"name": "alpha", "parent": "alpha", "manual": True},
                    {"name": "missing", "manual": True},
                    {"name": "alpha", "parent": "missing", "manual": True},
                    {"name": "delta", "before": "beta", "manual": True},
                    {"name": "delta", "before": "delta", "manual": True},
                    {"name": "delta", "parent": "alpha"},
                    {"name": "delta", "parent": "alpha", "manual": "true"},
                ]
                for move in invalid:
                    with self.assertRaises(ValueError):
                        server.act_group({"op": "move", **move})
                    self.assertEqual(path.read_bytes(), before)
                self.assertEqual(set(T.group_names()), {"alpha", "beta", "gamma", "delta"})

    def test_rename_and_delete_keep_children_and_their_tickets(self):
        for store in ("map", "files"):
            with self.subTest(store=store), self.groups(store) as root:
                tid = self.new_ticket(root, "child ticket")
                T.group_assign([tid], "gamma")
                T.group_move("beta", "alpha", manual=True)
                T.group_move("gamma", "beta", manual=True)
                T.group_rename("beta", "renamed")
                self.assertEqual(T.load_group_layout()["parents"], {"renamed": "alpha", "gamma": "renamed"})
                T.group_delete("renamed")
                self.assertEqual(T.load_group_layout()["parents"], {})
                self.assertEqual([t.id for t in T.group_members("gamma")], [tid])
                T.group_create("renamed")
                self.assertNotIn("renamed", T.load_group_layout()["parents"])

    def test_ai_proposals_cannot_create_or_change_nesting(self):
        for store in ("map", "files"):
            with self.subTest(store=store), self.groups(store) as root:
                tid = self.new_ticket(root, "ticket to assign")
                T.group_move("beta", "alpha", manual=True)
                path = root / "TODO" / ".group-layout.json"
                before = path.read_bytes()
                proposals = server.parse_group_drafts(json.dumps({"groups": [{
                    "name": "fresh", "ids": [tid], "parent": "alpha", "children": ["beta"],
                    "order": ["beta", "alpha"], "manual": True}]}), [tid], set(T.group_names()))
                self.assertEqual(set(proposals[0]), {"name", "title", "description", "new", "ids"})
                server.act_groupApply({"proposals": [{**proposals[0], "parent": "alpha", "manual": True}]})
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(T.load_group_layout()["parents"], {"beta": "alpha"})
                self.assertEqual([t.id for t in T.group_members("fresh")], [tid])
                server.act_groupApply({"proposals": [{"name": "beta", "ids": [tid], "parent": "gamma"}]})
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual([t.id for t in T.group_members("beta")], [tid])

    def test_stale_or_invalid_layout_still_shows_every_group(self):
        with self.groups("map") as root:
            path = root / "TODO" / ".group-layout.json"
            path.write_text(json.dumps({"order": ["gone", "beta", "beta", []],
                                        "parents": {"alpha": "beta", "beta": "alpha",
                                                    "gamma": "gone", "delta": "delta"}}), encoding="utf-8")
            layout = T.load_group_layout()
            self.assertEqual(layout["order"], ["beta", "alpha", "gamma", "delta"])
            self.assertEqual(layout["parents"], {"alpha": "beta"})
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(T.load_group_layout()["parents"], {})
