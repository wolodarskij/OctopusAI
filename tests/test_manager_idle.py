"""Shared manager scheduling, optional startup, and availability while idle."""

import concurrent.futures
import os
import json
import http.client
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from support import OCTOPUS, project
import managercheck
import todo
import todo_server


class ManagerIdle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, OCTOPUS_HOME=self.tmp.name)
        env.start()
        self.addCleanup(env.stop)

    def test_separate_processes_reserve_only_one_wake(self):
        key = ("sample", "session")
        self.assertFalse(managercheck.claim_wake(key, 1000))
        script = ("import sys; sys.path.insert(0, sys.argv[1]); import managercheck; "
                  "print(int(managercheck.claim_wake(('sample', 'session'), 1600)))")
        def claim(_):
            return subprocess.check_output(
                [sys.executable, "-c", script, str(OCTOPUS / "core/engine")],
                text=True, timeout=10).strip()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sorted(pool.map(claim, range(4))), ["0", "0", "0", "1"])
        self.assertFalse(managercheck.claim_wake(key, 1601))
        self.assertTrue(managercheck.claim_wake(key, 2200))

    def test_clock_rollback_does_not_trigger_a_wake(self):
        key = ("sample", "session")
        self.assertFalse(managercheck.claim_wake(key, 1000))
        self.assertFalse(managercheck.claim_wake(key, 500))
        self.assertFalse(managercheck.claim_wake(key, 1099))
        self.assertTrue(managercheck.claim_wake(key, 1100))

    def test_setting_persists_and_invalid_values_are_rejected(self):
        self.assertTrue(project.manager_autostart())
        for enabled in (False, True):
            result = todo_server.act_managerSettings({"op": "save", "autostart": enabled})
            self.assertEqual(result["autostart"], enabled)
            self.assertEqual(todo_server.act_managerSettings({})["autostart"], enabled)
        with self.assertRaises(ValueError):
            todo_server.act_managerSettings({"op": "save", "autostart": "false"})

    def test_settings_api_works_without_a_selected_project(self):
        server = todo_server.ThreadingHTTPServer(("127.0.0.1", 0), todo_server.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                for body, expected in (({"op": "save", "autostart": False}, False),
                                       ({"op": "list"}, False),
                                       ({"op": "save", "autostart": True}, True)):
                    conn.request("POST", "/api/managerSettings", json.dumps(body),
                                 {"X-Todo-Token": todo_server.TOKEN,
                                  "Content-Type": "application/json"})
                    response = conn.getresponse()
                    result = json.loads(response.read())
                    self.assertEqual(response.status, 200, result)
                    self.assertEqual(result["autostart"], expected)
            finally:
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_disabled_autostart_never_scans_or_launches_a_manager(self):
        project.save_manager_autostart(False)
        with mock.patch.object(todo_server, "_home_project") as home, \
                mock.patch.object(todo, "load_all") as load:
            self.assertEqual(todo_server.autostart_managers(1000), [])
            todo_server._watch_project(Path(self.tmp.name), 1000)
            home.assert_not_called()
            load.assert_not_called()

    def test_manual_start_remains_available_when_autostart_is_disabled(self):
        project.save_manager_autostart(False)
        with mock.patch.object(todo_server, "_home_project", return_value=("", None)) as home:
            todo_server.autostart_managers(1000, manual=True)
            home.assert_called_once()

    def test_changing_the_setting_does_not_close_live_sessions(self):
        with mock.patch.object(todo, "close_session") as close:
            todo_server.act_managerSettings({"op": "save", "autostart": False})
            close.assert_not_called()

    def test_idle_manager_is_not_a_finished_session_to_close(self):
        ticket = SimpleNamespace(id="T0001", repeat=todo.MANAGER)
        session = {"id": ticket.id, "provider": "codex", "claude": 123,
                   "reported": "finished", "running": False, "wait": "idle"}
        self.assertEqual(todo.wait_state(session, ticket)[0], "idle")
        self.assertEqual(todo.autoclose_sessions([session], [ticket]), [])


if __name__ == "__main__":
    unittest.main()
