"""The VCS layer and the vcs / vcsPull settings.   python -m unittest discover tests"""

import os
import shutil
import unittest

from support import FixtureCase, project

import vcs  # noqa: E402

GIT = shutil.which("git")
IDENTITY = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


@unittest.skipUnless(GIT, "git is not on the PATH")
class Git(FixtureCase):
    def setUp(self):
        super().setUp()
        self._git_env = {k: os.environ.get(k) for k in IDENTITY}
        os.environ.update(IDENTITY)

    def tearDown(self):
        for k, v in self._git_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def repo(self, name="work"):
        root = self.tmp / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "a.txt").write_text("a\n", encoding="utf-8")
        g = vcs.backend(root, "git")
        steps = g.init(["TODO/.briefs/", "Binaries/"])
        self.assertTrue(all(s.rc == 0 for s in steps), steps)
        return root, g

    def ok(self, steps):
        self.assertTrue(steps and all(s.rc == 0 for s in steps), steps)

    def test_init_creates_the_repository_the_gitignore_and_a_first_commit(self):
        root, g = self.repo()
        self.assertEqual(vcs.detect(root), "git")
        lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("TODO/.briefs/", lines)
        self.assertIn("Binaries/", lines)
        log = vcs._git(root, "log", "--format=%s")
        self.assertEqual(log.out.splitlines(), ["Initial commit"])
        self.assertEqual(g.status().info["dirty"], [])

    def test_init_keeps_an_existing_gitignore_and_adds_only_what_it_lacks(self):
        root = self.tmp / "keep"
        root.mkdir()
        (root / ".gitignore").write_text("*.log\nBinaries/\n", encoding="utf-8")
        self.ok(vcs.backend(root, "git").init(["Binaries/", "Saved/"]))
        lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines.count("Binaries/"), 1)
        self.assertEqual(lines[:2], ["*.log", "Binaries/"])
        self.assertIn("Saved/", lines)

    def test_status_on_a_clean_tree_and_on_a_dirty_one(self):
        root, g = self.repo()
        info = g.status().info
        self.assertTrue(info["repo"])
        self.assertTrue(info["branch"])
        self.assertEqual(info["dirty"], [])
        self.assertIsNone(info["ahead"])
        self.assertFalse(info["rebase"] or info["merge"])
        (root / "a.txt").write_text("changed\n", encoding="utf-8")
        (root / "new.txt").write_text("new\n", encoding="utf-8")
        self.assertEqual(sorted(g.status().info["dirty"]), ["a.txt", "new.txt"])
        self.ok(g.commit_all("change"))
        self.assertEqual(g.status().info["dirty"], [])
        self.assertEqual(len(g.commit_all("nothing")), 1)

    def test_status_outside_a_repository(self):
        root = self.tmp / "plain"
        root.mkdir()
        self.assertFalse(vcs.backend(root, "git").status().info["repo"])
        self.assertIsNone(vcs.detect(root))
        self.assertIsInstance(vcs.backend(root), vcs.NoVcs)

    def test_sync_against_a_bare_repository(self):
        root, g = self.repo()
        bare = self.tmp / "bare.git"
        self.ok([vcs._git(self.tmp, "init", "--bare", str(bare))])
        self.ok(g.set_remote(str(bare)))
        self.ok(g.sync("rebase"))
        info = g.status().info
        self.assertEqual((info["ahead"], info["behind"]), (0, 0))
        self.assertEqual(info["remote"], "origin")
        self.assertTrue(info["remote_url"])

        (root / "a.txt").write_text("mine\n", encoding="utf-8")
        self.ok(g.commit_all("mine"))
        info = g.status().info
        self.assertEqual((info["ahead"], info["behind"]), (1, 0))

        other = self.tmp / "other"
        self.ok([vcs._git(self.tmp, "clone", str(bare), str(other))])
        (other / "b.txt").write_text("theirs\n", encoding="utf-8")
        o = vcs.backend(other, "git")
        self.ok(o.commit_all("theirs"))
        self.ok(o.sync("rebase"))
        self.ok([vcs._git(root, "fetch")])
        info = g.status().info
        self.assertEqual((info["ahead"], info["behind"]), (1, 1))

        self.ok(g.sync("rebase"))
        info = g.status().info
        self.assertEqual((info["ahead"], info["behind"]), (0, 0))
        self.assertTrue((root / "b.txt").is_file())

    def test_sync_stops_at_the_first_failure(self):
        _root, g = self.repo()
        steps = g.sync("rebase")
        self.assertEqual(len(steps), 1)
        self.assertNotEqual(steps[0].rc, 0)
        with self.assertRaises(ValueError):
            g.sync("squash")

    def test_deleting_an_unmerged_branch_needs_confirm_and_the_branch_stays(self):
        root, g = self.repo()
        base = g.branches().info["current"]
        self.ok(g.branch_create("side"))
        self.ok(g.branch_switch("side"))
        (root / "s.txt").write_text("s\n", encoding="utf-8")
        self.ok(g.commit_all("side work"))
        self.ok(g.branch_switch(base))
        with self.assertRaises(vcs.NeedsConfirm) as cm:
            g.branch_delete("side")
        self.assertEqual(cm.exception.argv, ["git", "branch", "-D", "side"])
        self.assertIn("side", g.branches().info["local"])
        self.ok(g.merge("side"))
        self.ok(g.branch_delete("side"))
        self.assertNotIn("side", g.branches().info["local"])

    def test_init_on_a_diversion_project_needs_confirm(self):
        root = self.tmp / "dv"
        (root / ".diversion").mkdir(parents=True)
        self.assertEqual(vcs.detect(root), "diversion")
        with self.assertRaises(vcs.NeedsConfirm):
            vcs.backend(root, "git").init([])
        self.assertFalse((root / ".git").exists())


class Settings(FixtureCase):
    def test_a_project_without_vcs_is_none_and_checks_clean(self):
        root = self.make("Plain")
        p = project.Project(root)
        self.assertEqual((p.vcs, p.vcs_pull), ("none", "rebase"))
        self.assertIn("TODO/.briefs/", p.RULES["gitignore"])
        self.todo(root, "index")
        rc, out, err = self.todo(root, "check")
        self.assertEqual(rc, 0, out + err)

    def test_a_bad_vcs_value_fails_check(self):
        root = self.make("Bad", config={"vcs": "svn"})
        rc, out, err = self.todo(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("`vcs` is 'svn'", out + err)

    def test_a_bad_vcs_pull_value_fails_check(self):
        root = self.make("BadPull", config={"vcs": "git", "vcsPull": "squash"})
        rc, out, err = self.todo(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("`vcsPull` is 'squash'", out + err)


if __name__ == "__main__":
    unittest.main()
