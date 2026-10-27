"""Configurable lanes.   python -m unittest discover tests"""

import json
import threading
import unittest
import urllib.request

from support import FixtureCase, admin, project

import todo_server  # noqa: E402


class Lanes(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Lanes")
        self.t1 = self.new_ticket(self.root, "one")
        self.t2 = self.new_ticket(self.root, "two")

    def index(self):
        return (self.root / "TODO" / "INDEX.md").read_text(encoding="utf-8")

    def test_default_lanes_are_the_five(self):
        p = project.load(self.root)
        self.assertEqual(p.STATES, ("Pipeline", "InProgress", "Blocked", "Verification", "Done"))
        self.assertEqual(p.problems, [])

    def test_a_label_rename_changes_only_the_labels(self):
        _rc, q_before, _ = self.todo(self.root, "query", "--json")
        admin.lane_rename(self.root, "InProgress", label="Doing")
        admin.reindex(self.root)
        self.assertIn("**Doing** 0", self.index())
        self.assertIn("## Doing (0)", self.index())
        self.assertTrue((self.root / "TODO" / "InProgress").is_dir())
        _rc, q_after, _ = self.todo(self.root, "query", "--json")
        self.assertEqual(q_before, q_after)
        rc, out, _ = self.todo(self.root, "board")
        self.assertIn("Doing (0)", out)

    def test_a_dir_rename_moves_the_directory_and_its_tickets(self):
        self.todo(self.root, "move", self.t1, "InProgress")
        admin.lane_rename(self.root, "InProgress", label="Doing", new_dir="Doing")
        admin.reindex(self.root)
        self.assertTrue(list((self.root / "TODO" / "Doing").glob("T0001-*.md")))
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out)

    def test_an_added_lane_takes_tickets_through_move_board_and_query(self):
        admin.lane_add(self.root, "Review", "In Review", "InProgress", ["waiting for review",
                                                                        "reviewing"])
        admin.reindex(self.root)
        p = project.load(self.root)
        self.assertEqual(p.STATES[2], "Review")
        self.assertEqual(p.problems, [])
        rc, out, err = self.todo(self.root, "move", self.t1, "Review")
        self.assertEqual(rc, 0, err)
        self.assertIn("-> In Review", out)
        rc, out, _ = self.todo(self.root, "query", "--state", "Review", "--ids")
        self.assertEqual(out.strip(), self.t1)
        self.assertIn("## In Review (1)", self.index())
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 0, out)

        httpd = todo_server.make_server(0)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/api/move?project=Lanes" % httpd.server_address[1],
                data=json.dumps({"ids": [self.t2], "state": "Review",
                                 "status": "reviewing"}).encode(),
                headers={"X-Todo-Token": todo_server.TOKEN})
            with urllib.request.urlopen(req) as r:
                snap = json.loads(r.read())["snapshot"]
            states = {t["id"]: (t["state"], t["status"]) for t in snap["tickets"]}
            self.assertEqual(states[self.t2], ("Review", "reviewing"))
            self.assertEqual(snap["lanes"][2]["label"], "In Review")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_a_lane_with_a_role_or_with_tickets_cannot_be_removed(self):
        with self.assertRaises(ValueError):
            admin.lane_remove(self.root, "Blocked")
        admin.lane_add(self.root, "Parked", "Parked", None, ["parked"])
        self.todo(self.root, "move", self.t1, "Parked")
        with self.assertRaises(ValueError):
            admin.lane_remove(self.root, "Parked")
        self.todo(self.root, "move", self.t1, "Pipeline")
        admin.lane_remove(self.root, "Parked")
        self.assertNotIn("Parked", project.load(self.root).STATES)

    def set_lanes(self, lanes):
        p = self.root / ".octopus" / "project.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["lanes"] = lanes
        p.write_text(json.dumps(d), encoding="utf-8")

    def check_fails_with(self, text):
        rc, out, _ = self.todo(self.root, "check")
        self.assertEqual(rc, 1, out)
        self.assertIn(text, out)

    def test_check_fails_a_role_with_no_lane(self):
        lanes = [dict(x) for x in project.DEFAULT_LANES]
        lanes[2]["role"] = "none"
        self.set_lanes(lanes)
        self.check_fails_with("role 'blocked' names 0 lane(s)")

    def test_check_fails_a_role_with_two_lanes(self):
        lanes = [dict(x) for x in project.DEFAULT_LANES]
        lanes.append({"dir": "Also", "label": "Also", "role": "closed", "statuses": ["x"]})
        self.set_lanes(lanes)
        self.check_fails_with("role 'closed' names 2 lane(s)")

    def test_check_fails_a_lane_with_no_statuses(self):
        lanes = [dict(x) for x in project.DEFAULT_LANES]
        lanes.append({"dir": "Empty", "label": "Empty", "role": "none", "statuses": []})
        self.set_lanes(lanes)
        self.check_fails_with("lane 'Empty' has no statuses")

    def test_check_fails_a_ticket_in_a_directory_that_is_not_a_lane(self):
        stray = self.root / "TODO" / "Somewhere"
        stray.mkdir()
        (stray / "T0003-stray.md").write_text("---\nid: T0003\ntitle: stray\n---\n",
                                              encoding="utf-8")
        self.check_fails_with("TODO/Somewhere holds tickets and is not a lane")


if __name__ == "__main__":
    unittest.main()
