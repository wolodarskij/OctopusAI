"""Git on the board's Project view: `act_vcs` through the server.
python -m unittest discover tests"""

import json
import os
import threading
import unittest

from support import FixtureCase

import test_board_api
import test_vcs
import todo_server  # noqa: E402
import vcs  # noqa: E402


class Served(FixtureCase):
    """A project served by a board server."""
    call = test_board_api.BoardApi.call

    def setUp(self):
        super().setUp()
        self.root = self.make("Api")
        self.httpd = todo_server.make_server(0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def config(self):
        return json.loads((self.root / ".octopus" / "project.json").read_text(encoding="utf-8"))


class NoVcs(Served):
    def test_a_project_with_no_vcs_reads_as_none(self):
        st, r = self.call("vcs", {"op": "status"})
        self.assertEqual(st, 200, r)
        self.assertNotIn("error", r)
        self.assertEqual(r["status"]["vcs"], "none")
        self.assertIsNone(r["status"]["detected"])
        self.assertFalse(r["status"]["repo"])

    def test_an_op_on_no_repository_is_refused(self):
        st, r = self.call("vcs", {"op": "commit", "message": "x"})
        self.assertEqual(st, 400, r)

    def test_the_project_op_edits_vcs_pull(self):
        st, r = self.call("project", {"op": "vcs", "vcsPull": "ff-only"})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.config()["vcsPull"], "ff-only")
        st, r = self.call("project", {"op": "vcs", "vcsPull": "squash"})
        self.assertEqual(st, 400, r)
        self.assertEqual(self.config()["vcsPull"], "ff-only")


@unittest.skipUnless(test_vcs.GIT, "git is not on the PATH")
class GitOps(Served):
    def setUp(self):
        self._git_env = {k: os.environ.get(k) for k in test_vcs.IDENTITY}
        os.environ.update(test_vcs.IDENTITY)
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for k, v in self._git_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def ok(self, r):
        self.assertTrue(all(s["rc"] == 0 for s in r["steps"]), r["steps"])

    def init(self):
        st, r = self.call("vcs", {"op": "init"})
        self.assertEqual(st, 200, r)
        self.ok(r)
        return r

    def test_init_creates_the_repository_and_sets_vcs(self):
        r = self.init()
        self.assertEqual(vcs.detect(self.root), "git")
        self.assertEqual(self.config()["vcs"], "git")
        self.assertIn("git", self.config()["rulesets"])
        self.assertTrue(r["status"]["repo"])
        self.assertEqual(r["status"]["vcs"], "git")
        self.assertEqual(r["status"]["dirty"], [])
        self.assertIn("TODO/.briefs/",
                      (self.root / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertIn("git init", [s["cmd"] for s in r["steps"]])

    def test_init_without_the_pack_leaves_the_rulesets(self):
        st, r = self.call("vcs", {"op": "init", "pack": False})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.config()["vcs"], "git")
        self.assertNotIn("git", self.config()["rulesets"])

    def test_commit_commits_all(self):
        self.init()
        (self.root / "b.txt").write_text("b\n", encoding="utf-8")
        st, r = self.call("vcs", {"op": "status"})
        self.assertIn("b.txt", r["status"]["dirty"])
        st, r = self.call("vcs", {"op": "commit", "message": "add b"})
        self.assertEqual(st, 200, r)
        self.ok(r)
        self.assertEqual(r["status"]["dirty"], [])
        self.assertEqual(vcs._git(self.root, "log", "-1", "--format=%s").out, "add b")

    def test_sync_against_a_bare_remote(self):
        self.init()
        bare = self.tmp / "remote.git"
        self.assertEqual(vcs._git(self.tmp, "init", "--bare", str(bare)).rc, 0)
        st, r = self.call("vcs", {"op": "remote", "url": str(bare)})
        self.assertEqual(st, 200, r)
        self.ok(r)
        self.assertEqual(r["status"]["remote"], "origin")
        st, r = self.call("vcs", {"op": "sync"})
        self.assertEqual(st, 200, r)
        self.ok(r)
        (self.root / "c.txt").write_text("c\n", encoding="utf-8")
        self.call("vcs", {"op": "commit", "message": "add c"})
        st, r = self.call("vcs", {"op": "status"})
        self.assertEqual(r["status"]["ahead"], 1)
        st, r = self.call("vcs", {"op": "sync"})
        self.ok(r)
        self.assertEqual((r["status"]["ahead"], r["status"]["behind"]), (0, 0))

    def test_an_unmerged_branch_delete_needs_a_confirmation(self):
        home = self.init()["status"]["branch"]
        st, r = self.call("vcs", {"op": "branchCreate", "branch": "side"})
        self.ok(r)
        self.call("vcs", {"op": "branchSwitch", "branch": "side"})
        (self.root / "d.txt").write_text("d\n", encoding="utf-8")
        self.call("vcs", {"op": "commit", "message": "on side"})
        st, r = self.call("vcs", {"op": "branchSwitch", "branch": home})
        self.ok(r)
        st, r = self.call("vcs", {"op": "branchDelete", "branch": "side"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["cmd"], "git branch -D side")
        self.assertIn("needsConfirm", r)
        self.assertIn("side", vcs.backend(self.root, "git").branches().info["local"])
        st, r = self.call("vcs", {"op": "branchDelete", "branch": "side", "confirm": True})
        self.ok(r)
        self.assertNotIn("side", r["status"]["branches"])

    def test_a_branch_name_never_reads_as_an_option(self):
        self.init()
        st, r = self.call("vcs", {"op": "branchCreate", "branch": "--force"})
        self.assertEqual(st, 400, r)

    def test_init_on_a_diversion_project_needs_a_confirmation(self):
        (self.root / ".diversion").mkdir()
        st, r = self.call("vcs", {"op": "init"})
        self.assertEqual(st, 200, r)
        self.assertIn("needsConfirm", r)
        self.assertEqual(r["cmd"], "git init")
        self.assertFalse((self.root / ".git").exists())
        self.assertEqual(self.config().get("vcs", "none"), "none")

    def test_enable_sets_vcs_on_an_existing_repository(self):
        self.assertEqual(vcs._git(self.root, "init").rc, 0)
        st, r = self.call("vcs", {"op": "status"})
        self.assertEqual((r["status"]["vcs"], r["status"]["detected"]), ("none", "git"))
        st, r = self.call("vcs", {"op": "enable", "pack": False})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["status"]["vcs"], "git")


if __name__ == "__main__":
    unittest.main()
