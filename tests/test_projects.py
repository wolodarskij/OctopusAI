"""Two projects served by one engine.   python -m unittest discover tests"""

import json
import threading
import unittest
import urllib.request
import uuid

from support import FixtureCase, project

import todo  # noqa: E402  (support puts core/engine on the path)
import todo_server  # noqa: E402


class TwoProjects(FixtureCase):
    def setUp(self):
        super().setUp()
        self.a = self.make("Alpha")
        self.b = self.make("Beta")
        self.ta = self.new_ticket(self.a, "first ticket of alpha")
        self.tb = self.new_ticket(self.b, "first ticket of beta")

    def test_both_start_at_the_same_id(self):
        self.assertEqual(self.ta, "T0001")
        self.assertEqual(self.tb, "T0001")

    def test_session_ids_and_titles_differ(self):
        seen = {}
        for root in (self.a, self.b):
            tok = project.use(root)
            try:
                t = todo.find(todo.load_all(), "T0001")
                seen[root] = ({todo.launch_session_id("T0001", m) for m in todo.MODES},
                              todo.session_title(t, "work"))
            finally:
                project.reset(tok)
        (ids_a, title_a), (ids_b, title_b) = seen[self.a], seen[self.b]
        self.assertFalse(ids_a & ids_b)
        self.assertEqual(len(ids_a), 4)
        self.assertNotEqual(title_a, title_b)

    def test_dry_run_names_the_project_root_and_title(self):
        for root, name in ((self.a, "Alpha"), (self.b, "Beta")):
            for mode in todo.MODES:
                rc, out, err = self.todo(root, mode, "T0001", "--dry-run")
                self.assertEqual(rc, 0, err)
                self.assertIn('"%s - T0001 - first ticket of %s - %s"' % (name, name.lower(), mode),
                              out)
                self.assertIn("  in      %s" % root.resolve(), out)
                self.assertIn("  title   %s - T0001 - first ticket of %s - %s"
                              % (name, name.lower(), mode), out)

    def test_one_board_server_lists_and_moves_tickets_in_both(self):
        httpd = todo_server.make_server(0)
        port = httpd.server_address[1]
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        base = "http://127.0.0.1:%d" % port
        tok = todo_server.TOKEN

        def get(path):
            with urllib.request.urlopen(base + path + ("&" if "?" in path else "?")
                                        + "token=" + tok) as r:
                return json.loads(r.read())

        def post(path, body):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                         headers={"X-Todo-Token": tok,
                                                  "Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        try:
            names = [p["name"] for p in get("/api/projects")["projects"]]
            self.assertEqual(names, ["Alpha", "Beta"])
            snap_a = get("/api/tickets?project=Alpha")
            snap_b = get("/api/tickets?project=Beta")
            self.assertEqual([t["title"] for t in snap_a["tickets"]], ["first ticket of alpha"])
            self.assertEqual([t["title"] for t in snap_b["tickets"]], ["first ticket of beta"])
            r = post("/api/move?project=Beta", {"ids": ["T0001"], "state": "InProgress"})
            self.assertEqual(r["moved"], ["T0001"])
            self.assertEqual(r["snapshot"]["tickets"][0]["state"], "InProgress")
            self.assertTrue((self.b / "TODO" / "InProgress").glob("T0001-*.md"))
            self.assertTrue(list((self.a / "TODO" / "Pipeline").glob("T0001-*.md")))
            self.assertFalse(list((self.b / "TODO" / "Pipeline").glob("T0001-*.md")))
            sessions = post("/api/allSessions", {})
            self.assertEqual([p["name"] for p in sessions["projects"]], ["Alpha", "Beta"])
            # The Sessions view lists the current project only, until the owner asks for all.
            self.assertEqual(snap_a["board"]["others"], "hidden")
            r = post("/api/board?project=Alpha", {"others": "shown"})
            self.assertEqual(r["board"]["others"], "shown")
            self.assertEqual(get("/api/tickets?project=Alpha")["board"]["others"], "shown")
            self.assertEqual(get("/api/tickets?project=Beta")["board"]["others"], "hidden")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_pick_folder_returns_the_chosen_directory_or_nothing(self):
        httpd = todo_server.make_server(0)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        base = "http://127.0.0.1:%d" % httpd.server_address[1]
        asked = []
        answers = [str(self.b), ""]

        def fake(start):
            asked.append(start)
            return answers.pop(0)

        def post(body):
            req = urllib.request.Request(base + "/api/pickFolder", data=json.dumps(body).encode(),
                                         headers={"X-Todo-Token": todo_server.TOKEN,
                                                  "Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        real = todo_server._ask_folder
        todo_server._ask_folder = fake
        try:
            self.assertEqual(post({"start": str(self.a)}), {"path": str(self.b)})
            self.assertEqual(post({"start": str(self.a / "no-such-dir")}), {"path": ""})
            # The dialog opens at an existing directory only.
            self.assertEqual(asked, [str(self.a), ""])
        finally:
            todo_server._ask_folder = real
            httpd.shutdown()
            httpd.server_close()

    def test_a_hook_writes_only_under_its_own_project(self):
        sid = str(uuid.uuid4())
        for event in ("SessionStart", "Stop"):
            payload = {"session_id": sid, "hook_event_name": event, "cwd": str(self.a),
                       "source": "startup"}
            rc, _out, err = self.cli("hook", stdin=json.dumps(payload))
            self.assertEqual(rc, 0, err)
        a_files = sorted(p.name for p in (self.a / "TODO" / ".sessions").iterdir())
        self.assertTrue(any(n.startswith(sid) for n in a_files), a_files)
        self.assertFalse((self.b / "TODO" / ".sessions").exists())
        board = (self.a / ".octopus" / "SESSIONS.md").read_text(encoding="utf-8")
        self.assertIn(sid, board)
        self.assertFalse((self.b / ".octopus" / "SESSIONS.md").exists())

    def test_a_hook_stays_in_its_launch_project_when_the_shell_moves(self):
        sid = str(uuid.uuid4())
        payload = {"session_id": sid, "hook_event_name": "Stop", "cwd": str(self.b)}
        rc, _out, err = self.cli("hook", stdin=json.dumps(payload),
                                 env={"CLAUDE_PROJECT_DIR": str(self.a)})
        self.assertEqual(rc, 0, err)
        a_files = sorted(p.name for p in (self.a / "TODO" / ".sessions").iterdir())
        self.assertTrue(any(n.startswith(sid) for n in a_files), a_files)
        self.assertFalse((self.b / "TODO" / ".sessions").exists())

    def test_a_hook_outside_every_project_does_nothing(self):
        payload = {"session_id": str(uuid.uuid4()), "hook_event_name": "Stop",
                   "cwd": str(self.tmp / "home")}
        rc, _out, _err = self.cli("hook", stdin=json.dumps(payload))
        self.assertEqual(rc, 0)
        self.assertFalse((self.a / "TODO" / ".sessions").exists())

    def test_a_verify_value_of_one_project_does_not_close_in_another(self):
        self.todo(self.a, "move", "T0001", "Verification")
        self.todo(self.b, "move", "T0001", "Verification")
        tok = project.use(self.a)
        try:
            value_a = project.current().verify_token("T0001")
        finally:
            project.reset(tok)
        env = {"CLAUDECODE": "1", "OCTOPUS_VERIFY": value_a}
        rc, _out, err = self.todo(self.b, "move", "T0001", "Done", env=env)
        self.assertEqual(rc, 2)
        self.assertIn("only a verification session", err)
        rc, out, err = self.todo(self.a, "move", "T0001", "Done", env=env)
        self.assertEqual(rc, 0, err)
        self.assertIn("-> Done", out)

    def test_every_project_keeps_its_own_ticket_files(self):
        for root in (self.a, self.b):
            self.assertTrue((root / "TODO" / "INDEX.md").exists())
            self.assertTrue((root / "TODO" / ".ids").exists())
        self.assertFalse((self.tmp / "home" / "TODO").exists())


if __name__ == "__main__":
    unittest.main()
