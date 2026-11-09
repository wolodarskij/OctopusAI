"""The version control packs other than git: perforce, diversion, svn, mercurial and plastic.
Each one loads, its permits deny their commands, a launch mode holds its permits, and its brief
sections reach the brief only when `vcs` names its kind.
python -m unittest discover tests"""

import json
import unittest

from support import FixtureCase, admin, project, quiet

import todo as T  # noqa: E402
import vcs  # noqa: E402

PACKS = {
    "perforce": {"marker": ".p4config", "work": "Record your work for the submit",
                 "verify": "Submit the work when it holds", "plan": "A plan session never",
                 "review": "A review session never", "manager": "## Perforce",
                 "always": "Bash(p4 revert:*)", "work_permits": [],
                 "verify_permits": ["p4-submit", "p4-sync"]},
    "diversion": {"marker": ".diversion", "work": "Write your files on the ticket",
                  "verify": "Commit the work when it holds", "plan": "A plan session never",
                  "review": "A review session never", "manager": "## Diversion",
                  "always": "Bash(dv reset:*)", "work_permits": [],
                  "verify_permits": ["dv-commit"]},
    "svn": {"marker": ".svn", "work": "Record your work for the commit",
            "verify": "Commit the work when it holds", "plan": "A plan session never",
            "review": "A review session never", "manager": "## SVN",
            "always": "Bash(svn revert:*)", "work_permits": [],
            "verify_permits": ["svn-commit", "svn-update"]},
    "mercurial": {"marker": ".hg", "work": "Commit your work before the move",
                  "verify": "Push the work when it holds", "plan": "A plan session never",
                  "review": "A review session never", "manager": "## Mercurial",
                  "always": "Bash(hg revert:*)", "work_permits": ["hg-commit"],
                  "verify_permits": ["hg-commit", "hg-sync"]},
    "plastic": {"marker": ".plastic", "work": "Write your files on the ticket",
                "verify": "Check in the work when it holds", "plan": "A plan session never",
                "review": "A review session never", "manager": "## Unity Version Control",
                "always": "Bash(cm undo:*)", "work_permits": [],
                "verify_permits": ["cm-checkin"]},
}


class VcsPacks(FixtureCase):
    def project(self, where, pack, vcs_kind):
        root = self.tmp / where / "Proj"
        root.mkdir(parents=True)
        admin.install(root, [], "Proj", register=False, out=quiet)
        if pack:
            admin.pack_add(root, pack, out=quiet)
        p = root / ".octopus" / "project.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["vcs"] = vcs_kind
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

    def brief(self, root, tid, mode):
        return self.run_in(root, lambda P: T.build_brief(T.find(T.load_all(), tid), mode))

    def denied(self, root, tid, mode):
        argv = self.run_in(root, lambda P: T._launch_argv(T.find(T.load_all(), tid), mode))
        return argv[argv.index("--disallowedTools") + 1]

    def test_every_kind_has_its_pack(self):
        for kind in vcs.KINDS:
            if kind != "none":
                self.assertEqual(vcs.PACKS[kind], kind)

    def test_detect_reads_the_marker(self):
        for kind, spec in PACKS.items():
            d = self.tmp / ("detect-" + kind)
            (d / spec["marker"]).mkdir(parents=True)
            self.assertEqual(vcs.detect(d), kind)

    def test_each_pack_loads_and_its_permits_deny_their_commands(self):
        for kind, spec in PACKS.items():
            with self.subTest(kind):
                root = self.project("load-" + kind, kind, kind)
                self.run_in(root, lambda P: self.assertEqual(P.problems, []))
                rc, out, err = self.todo(root, "check")
                self.assertEqual(rc, 0, out + err)
                perms = self.run_in(root, lambda P: dict(P.RULES["permits"]))
                denied = self.run_in(root, lambda P: P.denied_tools([]))
                for name in spec["verify_permits"]:
                    for pat in perms[name]["deny"]:
                        self.assertIn(pat, denied)
                self.assertIn(spec["always"], denied)
                every = self.run_in(root, lambda P: P.denied_tools(sorted(P.PERMITS)))
                self.assertIn(spec["always"], every)

    def test_a_verify_launch_holds_its_permits_and_a_review_launch_none(self):
        for kind, spec in PACKS.items():
            with self.subTest(kind):
                root = self.project("mode-" + kind, kind, kind)
                tid = self.ticket(root)
                perms = self.run_in(root, lambda P: dict(P.RULES["permits"]))
                verify = self.denied(root, tid, "verify")
                review = self.denied(root, tid, "review")
                work = self.denied(root, tid, "work")
                for name in spec["verify_permits"]:
                    for pat in perms[name]["deny"]:
                        self.assertNotIn(pat, verify)
                        self.assertIn(pat, review)
                        if name not in spec["work_permits"]:
                            self.assertIn(pat, work)
                self.assertIn(spec["always"], verify)

    def test_the_brief_of_each_mode_holds_the_section_of_its_kind(self):
        for kind, spec in PACKS.items():
            with self.subTest(kind):
                root = self.project("brief-" + kind, kind, kind)
                tid = self.ticket(root)
                mid = self.ticket(root, "--repeat", "manager", "--permits", "projects")
                for mode in ("work", "verify", "plan", "review"):
                    got = self.brief(root, tid, mode)
                    self.assertIn(spec[mode], got, mode)
                    self.assertNotIn("git repository", got, mode)
                    self.assertNotIn("{id}", got, mode)
                both = self.brief(root, tid, "work") + self.brief(root, tid, "verify")
                self.assertIn("%s: fix the thing" % tid, both)
                self.assertIn(spec["manager"], self.brief(root, mid, "work"))

    def test_the_pack_without_vcs_adds_no_section(self):
        for kind, spec in PACKS.items():
            with self.subTest(kind):
                root = self.project("off-" + kind, kind, "none")
                tid = self.ticket(root)
                for mode in ("work", "verify"):
                    self.assertNotIn(spec[mode], self.brief(root, tid, mode), mode)

    def test_vcs_without_its_pack_is_a_problem(self):
        root = self.project("nopack", None, "perforce")
        problems = self.run_in(root, lambda P: list(P.problems))
        self.assertTrue(any("perforce pack is not installed" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
