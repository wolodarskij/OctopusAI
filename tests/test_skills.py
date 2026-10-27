"""Skills installed into a project from a source.   python -m unittest tests.test_skills

Each source is made in the temporary directory: a local git repository reached by a `file://`
URL, a folder, or a library whose URL points at such a repository. No test reaches the network.
"""

import json
import os
import subprocess

from support import FixtureCase, admin, quiet

import skills  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "-c", "init.defaultBranch=main"] + list(args), cwd=cwd, check=True,
                   capture_output=True)


def skill(d, name, version="", body="Body.\n", extra=None):
    d = d / name
    d.mkdir(parents=True, exist_ok=True)
    fm = "name: %s\ndescription: The %s skill.\n" % (name, name)
    if version:
        fm += "version: %s\n" % version
    (d / "SKILL.md").write_text("---\n%s---\n%s" % (fm, body), encoding="utf-8")
    for rel, text in (extra or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text, encoding="utf-8")
    return d


class Skills(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Sample Game")
        self.src = self.tmp / "src" / "library"
        skill(self.src / "skills", "alpha", "1.0", extra={"tools/run.py": "print(1)\n"})
        skill(self.src / "skills", "beta")
        skill(self.src / "plugins" / "p1" / "skills", "beta")
        git(self.src, "init", "-q")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "first")
        self.url = self.src.as_uri()
        self._libs = dict(skills.LIBRARIES)
        skills.LIBRARIES["sample"] = {"title": "a sample library", "url": self.url,
                                      "path": "skills"}
        self._claude = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude-home")

    def tearDown(self):
        skills.LIBRARIES.clear()
        skills.LIBRARIES.update(self._libs)
        if self._claude is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._claude
        super().tearDown()

    def commit(self, msg):
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", msg)

    def record(self):
        return json.loads((self.root / ".octopus/skills.json").read_text(encoding="utf-8"))

    def test_the_libraries_name_claude_and_codex(self):
        names = [l["name"] for l in skills.libraries()]
        for n in ("claude", "claude-plugins", "codex"):
            self.assertIn(n, names)
        self.assertIn("github.com/openai/skills", skills.LIBRARIES["codex"]["url"])
        self.assertIn("github.com/anthropics/skills", skills.LIBRARIES["claude"]["url"])

    def test_a_marketplace_claude_code_has_is_a_library(self):
        mk = self.tmp / "claude-home/plugins/marketplaces/sample-market"
        skill(mk / "plugins/tool/skills", "gamma")
        (self.tmp / "claude-home/plugins/known_marketplaces.json").write_text(json.dumps(
            {"sample-market": {"installLocation": str(mk)}}), encoding="utf-8")
        r = skills.available(self.root, library="marketplace:sample-market")
        self.assertEqual([s["path"] for s in r["skills"]], ["plugins/tool/skills/gamma"])
        skills.add(self.root, ["gamma"], library="marketplace:sample-market", out=quiet)
        self.assertTrue((self.root / ".agents/skills/gamma/SKILL.md").is_file())

    def test_available_lists_every_skill_and_tells_apart_two_of_one_name(self):
        r = skills.available(self.root, git=self.url)
        paths = [s["path"] for s in r["skills"]]
        self.assertEqual(paths, ["plugins/p1/skills/beta", "skills/alpha", "skills/beta"])
        with self.assertRaisesRegex(ValueError, "2 skills are called beta"):
            skills.add(self.root, ["beta"], git=self.url, out=quiet)
        with self.assertRaisesRegex(ValueError, "holds 3 skills"):
            skills.add(self.root, [], git=self.url, out=quiet)

    def test_a_skill_from_git_reaches_every_agent(self):
        got = skills.add(self.root, ["skills/alpha"], git=self.url, out=quiet)
        self.assertEqual(got, ["alpha"])
        self.assertTrue((self.root / ".agents/skills/alpha/tools/run.py").is_file())
        pointer = (self.root / ".claude/skills/alpha/SKILL.md").read_text(encoding="utf-8")
        self.assertIn(".agents/skills/alpha/SKILL.md", pointer)
        self.assertIn("`alpha`", (self.root / "AGENTS.md").read_text(encoding="utf-8"))
        row = self.record()["skills"]["alpha"]
        self.assertEqual((row["source"], row["url"], row["path"], row["version"]),
                         ("git", self.url, "skills/alpha", "1.0"))
        self.assertEqual(len(row["commit"]), 40)
        rows = skills.listing(self.root)
        self.assertEqual([(r["name"], r["state"]) for r in rows], [("alpha", "same")])
        with self.assertRaisesRegex(ValueError, "installed already"):
            skills.add(self.root, ["skills/alpha"], git=self.url, out=quiet)

    def test_a_library_skill_installs_by_name_and_updates(self):
        skills.add(self.root, ["alpha"], library="sample", out=quiet)
        self.assertEqual(self.record()["skills"]["alpha"]["library"], "sample")
        skill(self.src / "skills", "alpha", "1.1", body="Second.\n",
              extra={"tools/new.py": "print(2)\n"})
        (self.src / "skills/alpha/tools/run.py").unlink()
        self.commit("second")
        skills.update(self.root, out=quiet)
        d = self.root / ".agents/skills/alpha"
        self.assertTrue((d / "tools/new.py").is_file())
        self.assertFalse((d / "tools/run.py").exists())
        self.assertIn("Second.", (d / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(self.record()["skills"]["alpha"]["version"], "1.1")

    def test_an_edited_skill_is_kept_unless_forced(self):
        skills.add(self.root, ["alpha"], library="sample", out=quiet)
        md = self.root / ".agents/skills/alpha/SKILL.md"
        md.write_text(md.read_text(encoding="utf-8") + "Mine.\n", encoding="utf-8")
        self.assertEqual(skills.listing(self.root)[0]["state"], "edited")
        with self.assertRaisesRegex(ValueError, "edited since"):
            skills.update(self.root, ["alpha"], out=quiet)
        with self.assertRaisesRegex(ValueError, "edited since"):
            skills.remove(self.root, ["alpha"], out=quiet)
        skills.update(self.root, ["alpha"], force=True, out=quiet)
        self.assertNotIn("Mine.", md.read_text(encoding="utf-8"))

    def test_remove_takes_the_files_the_pointer_and_the_record(self):
        skills.add(self.root, ["alpha"], library="sample", out=quiet)
        skills.remove(self.root, ["alpha"], out=quiet)
        self.assertFalse((self.root / ".agents/skills/alpha").exists())
        self.assertFalse((self.root / ".claude/skills/alpha").exists())
        self.assertFalse((self.root / ".octopus/skills.json").exists())
        rec = admin.Record(self.root)
        self.assertFalse([r for r, v in rec.files.items() if v["by"] == "skill:alpha"])

    def test_a_skill_the_project_has_already_is_never_replaced(self):
        own = skill(self.root / ".agents/skills", "alpha", body="The project's own.\n")
        with self.assertRaisesRegex(ValueError, "project's own"):
            skills.add(self.root, ["alpha"], library="sample", out=quiet)
        self.assertIn("own", (own / "SKILL.md").read_text(encoding="utf-8"))

    def test_a_folder_and_a_ref(self):
        folder = self.tmp / "loose"
        skill(folder, "delta")
        skills.add(self.root, [], folder=folder / "delta", out=quiet)
        self.assertEqual(self.record()["skills"]["delta"]["source"], "folder")
        git(self.src, "tag", "v1")
        skill(self.src / "skills", "alpha", "2.0")
        self.commit("third")
        skills.add(self.root, ["alpha"], git=self.url, ref="v1", out=quiet)
        row = self.record()["skills"]["alpha"]
        self.assertEqual((row["ref"], row["version"]), ("v1", "1.0"))
        skills.update(self.root, ["alpha"], out=quiet)
        self.assertEqual(self.record()["skills"]["alpha"]["version"], "1.0")
        skills.update(self.root, ["alpha"], ref="main", out=quiet)
        self.assertEqual(self.record()["skills"]["alpha"]["version"], "2.0")

    def test_the_cli(self):
        rc, out, err = self.cli("skills", self.root, "add", "--git", self.url, "--all")
        self.assertNotEqual(rc, 0)
        rc, out, err = self.cli("skills", self.root, "add", "--git", self.url,
                                "skills/alpha", "plugins/p1/skills/beta")
        self.assertEqual(rc, 0, err)
        rc, out, err = self.cli("skills", self.root, "list")
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        rc, out, err = self.cli("skills", self.root, "available", self.url)
        self.assertIn("* skills/alpha", out)
        rc, out, err = self.cli("skills", self.root, "remove", "alpha", "beta")
        self.assertEqual(rc, 0, err)
