"""The git pack: its permits, the permits of each launch mode, the commands it always denies,
and the commit policy in the brief.
python -m unittest discover tests"""

import json
import unittest

from support import FixtureCase, admin, project, quiet

import todo as T  # noqa: E402

COMMIT = ["Bash(git commit:*)", "Bash(git revert:*)", "Bash(git cherry-pick:*)",
          "Bash(git tag:*)"]
SYNC = ["Bash(git push:*)", "Bash(git pull:*)", "Bash(git remote add:*)",
        "Bash(git remote set-url:*)"]
BRANCH = ["Bash(git switch:*)", "Bash(git checkout:*)", "Bash(git branch:*)",
          "Bash(git merge:*)", "Bash(git rebase:*)", "Bash(git worktree:*)"]
ALWAYS = ["Bash(git push --force:*)", "Bash(git push -f:*)", "Bash(git push --force-with-lease:*)",
          "Bash(git reset --hard:*)", "Bash(git clean:*)", "Bash(git stash:*)",
          "Bash(git restore:*)", "Bash(git branch -D:*)", "Bash(git add -A)",
          "Bash(git add --all:*)", "Bash(git add .)", "Bash(git commit -a:*)",
          "Bash(git commit -am:*)",
          "Bash(git commit --all:*)", "Bash(git commit * -am*)"]


class GitPack(FixtureCase):
    def project(self, where, pack=True, vcs="git"):
        """A project named Proj under `where`, with the git pack or without it."""
        root = self.tmp / where / "Proj"
        root.mkdir(parents=True)
        admin.install(root, [], "Proj", register=False, out=quiet)
        if pack:
            admin.pack_add(root, "git", out=quiet)
        p = root / ".octopus" / "project.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["vcs"] = vcs
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")
        return root

    def ticket(self, root, *extra):
        rc, out, err = self.todo(root, "new", "--title", "fix the thing", "--severity",
                                 "Medium", *extra)
        self.assertEqual(rc, 0, err)
        return out.split()[0]

    def run_in(self, root, fn):
        tok = project.use(root)
        try:
            return fn(project.P)
        finally:
            project.reset(tok)

    def brief(self, root, tid, mode="work"):
        return self.run_in(root, lambda P: T.build_brief(T.find(T.load_all(), tid), mode))

    def argv(self, root, tid, mode="work"):
        return self.run_in(root, lambda P: T._launch_argv(T.find(T.load_all(), tid), mode))

    def test_the_pack_loads_without_a_problem(self):
        root = self.project("a")
        self.run_in(root, lambda P: self.assertEqual(P.problems, []))
        self.ticket(root, "--permits", "git-commit,git-sync,git-branch")
        rc, out, err = self.todo(root, "check")
        self.assertEqual(rc, 0, out + err)

    def test_no_permit_denies_every_pattern(self):
        root = self.project("a")
        denied = self.run_in(root, lambda P: P.denied_tools([]))
        for pat in COMMIT + SYNC + BRANCH + ALWAYS:
            self.assertIn(pat, denied)

    def test_git_commit_drops_only_the_commit_patterns(self):
        root = self.project("a")
        denied = self.run_in(root, lambda P: P.denied_tools(["git-commit"]))
        for pat in COMMIT:
            self.assertNotIn(pat, denied)
        for pat in SYNC + BRANCH + ALWAYS:
            self.assertIn(pat, denied)

    def test_git_sync_may_abort_a_pull_but_never_force(self):
        root = self.project("a")
        denied = self.run_in(root, lambda P: P.denied_tools(["git-sync"]))
        for pat in SYNC + ["Bash(git merge:*)", "Bash(git rebase:*)"]:
            self.assertNotIn(pat, denied)
        for pat in ALWAYS:
            self.assertIn(pat, denied)
        every = self.run_in(root, lambda P: P.denied_tools(["git-commit", "git-sync",
                                                            "git-branch"]))
        self.assertEqual(sorted(every), sorted(set(every)))
        for pat in ALWAYS:
            self.assertIn(pat, every)
        for pat in COMMIT + SYNC + BRANCH:
            self.assertNotIn(pat, every)

    def test_a_work_launch_always_may_commit_and_never_sync(self):
        root = self.project("a")
        tid = self.ticket(root)
        argv = self.argv(root, tid, "work")
        denied = argv[argv.index("--disallowedTools") + 1]
        for pat in SYNC + BRANCH + ALWAYS:
            self.assertIn(pat, denied)
        for pat in COMMIT:
            self.assertNotIn(pat, denied)

    def test_a_verify_launch_always_may_commit_pull_and_push(self):
        root = self.project("a")
        tid = self.ticket(root)
        argv = self.argv(root, tid, "verify")
        denied = argv[argv.index("--disallowedTools") + 1]
        for pat in COMMIT + SYNC + ["Bash(git merge:*)", "Bash(git rebase:*)"]:
            self.assertNotIn(pat, denied)
        for pat in ["Bash(git switch:*)", "Bash(git checkout:*)", "Bash(git branch:*)",
                    "Bash(git worktree:*)"] + ALWAYS:
            self.assertIn(pat, denied)

    def test_a_plan_launch_and_the_manager_get_no_mode_permit(self):
        root = self.project("a")
        tid = self.ticket(root)
        argv = self.argv(root, tid, "plan")
        for pat in COMMIT + SYNC:
            self.assertIn(pat, argv[argv.index("--disallowedTools") + 1])
        mid = self.ticket(root, "--repeat", "manager", "--permits", "projects")
        argv = self.argv(root, mid, "work")
        for pat in COMMIT + SYNC:
            self.assertIn(pat, argv[argv.index("--disallowedTools") + 1])

    def test_a_review_launch_denies_them_all(self):
        root = self.project("a")
        tid = self.ticket(root, "--permits", "git-commit,git-sync,git-branch")
        argv = self.argv(root, tid, "review")
        denied = argv[argv.index("--disallowedTools") + 1]
        for pat in COMMIT + SYNC + BRANCH + ALWAYS:
            self.assertIn(pat, denied)

    def test_a_work_brief_holds_the_commit_policy(self):
        root = self.project("a")
        tid = self.ticket(root)
        brief = self.brief(root, tid, "work")
        self.assertIn("- **Commit your work before the move to Verification above.**", brief)
        self.assertIn("commit as `%s: fix the thing`" % tid, brief)
        self.assertIn("`git apply --cached <patch>`", brief)
        self.assertIn("Never pull and never push", brief)
        self.assertIn("| `git-commit` |", brief)
        self.assertNotIn("{vcs_", brief)
        self.assertLess(brief.index("## When you finish"),
                        brief.index("- **Commit your work before"))

    def test_verify_plan_and_review_briefs(self):
        root = self.project("a")
        tid = self.ticket(root)
        verify = self.brief(root, tid, "verify")
        self.assertIn("- **Push the work when it holds.**", verify)
        self.assertIn("`%s: verified`" % tid, verify)
        self.assertIn("`git pull --no-rebase`, then `git push`", verify)
        self.assertIn("**It does not hold:** commit nothing more and push nothing.", verify)
        self.assertIn("A plan session never commits", self.brief(root, tid, "plan"))
        self.assertIn("A review session never commits", self.brief(root, tid, "review"))

    def test_the_manager_never_commits(self):
        root = self.project("a")
        tid = self.ticket(root, "--repeat", "manager", "--permits", "projects,git-commit")
        brief = self.brief(root, tid, "work")
        self.assertIn("The manager never commits", brief)
        self.assertNotIn("Commit as", brief)

    def test_without_git_no_brief_commits(self):
        root = self.project("nogit", vcs="none")
        tid = self.ticket(root)
        mid = self.ticket(root, "--repeat", "manager", "--permits", "projects")
        for mode in T.MODES:
            got = self.brief(root, tid, mode)
            self.assertNotIn("git repository", got, mode)
        self.assertNotIn("## Git", self.brief(root, mid, "work"))

    def test_git_without_the_pack_commits_in_work_and_pushes_in_verify(self):
        plain = self.project("plain", pack=False, vcs="none")
        root = self.project("nopack", pack=False, vcs="git")
        tid = self.ticket(plain)
        mid = self.ticket(plain, "--repeat", "manager", "--permits", "projects")
        self.assertEqual(self.ticket(root), tid)
        self.assertEqual(self.ticket(root, "--repeat", "manager", "--permits", "projects"), mid)
        self.assertIn("Commit your work before", self.brief(root, tid, "work"))
        self.assertIn("Push the work when it holds", self.brief(root, tid, "verify"))
        for mode in ("plan", "review"):
            self.assertEqual(self.brief(root, tid, mode), self.brief(plain, tid, mode), mode)
        self.assertEqual(self.brief(root, mid, "work"), self.brief(plain, mid, "work"))

    def test_with_the_pack_git_only_inserts_its_section(self):
        # The pack adds its permits to the ungranted and denied lists, so the reference is the
        # same pack with `vcs: none`: turning git on inserts one block and changes nothing else.
        off = self.project("off", vcs="none")
        on = self.project("on")
        for root in (off, on):
            tid = self.ticket(root)
            mid = self.ticket(root, "--repeat", "manager", "--permits", "projects")
        for t, mode in [(tid, m) for m in T.MODES] + [(mid, "work")]:
            n, g = self.brief(off, t, mode), self.brief(on, t, mode)
            k = len(n)
            for i, (a, b) in enumerate(zip(n, g)):
                if a != b:
                    k = i
                    break
            tail = len(n) - k
            self.assertEqual(g[len(g) - tail:], n[k:], mode)
            self.assertIn("This project is a git repository", g[k:len(g) - tail], mode)


if __name__ == "__main__":
    unittest.main()
