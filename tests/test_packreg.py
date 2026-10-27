"""The pack registry, and packs chosen as a set.   python -m unittest tests.test_packreg

Each pack here is made in the temporary directory: a folder, or a local git repository reached
by a `file://` URL. No test reaches the network.
"""

import json
import subprocess

from support import FixtureCase, admin, project, quiet

import packreg  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "-c", "init.defaultBranch=main"] + list(args), cwd=cwd, check=True,
                   capture_output=True)


def write_pack(d, title="Sample engine: a rule and a skill", requires=None, extra=None,
               skill=True):
    d.mkdir(parents=True, exist_ok=True)
    rs = {"title": title, "payload": [{"from": "rules/", "to": ".agents/rules/octopus/%s/" % d.name}]}
    if requires:
        rs["requires"] = requires
    if skill:
        rs["payload"].append({"from": "skills/", "to": ".agents/skills/"})
        (d / "skills" / "sample-skill").mkdir(parents=True, exist_ok=True)
        (d / "skills" / "sample-skill" / "SKILL.md").write_text(
            "---\nname: sample-skill\ndescription: Does a sample thing.\n---\nBody.\n",
            encoding="utf-8")
    rs.update(extra or {})
    (d / "rules").mkdir(exist_ok=True)
    (d / "rules" / "sample.md").write_text("# Sample rule\n", encoding="utf-8")
    (d / "ruleset.json").write_text(json.dumps(rs, indent=2), encoding="utf-8")
    return d


class Registry(FixtureCase):
    def repo(self, name="engine-pack", sub=""):
        r = self.tmp / "src" / name
        write_pack(r / sub if sub else r)
        git(r, "init", "-q")
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "first")
        return r

    def test_the_shipped_packs_are_listed_and_cannot_be_removed(self):
        names = [p["name"] for p in packreg.available()]
        self.assertEqual(names, sorted(names))
        self.assertIn("git", names)
        self.assertTrue(all(p["source"] == "builtin" for p in packreg.available()))
        with self.assertRaisesRegex(ValueError, "ships with Octopus AI"):
            packreg.remove("git", out=quiet)

    def test_a_folder_pack_is_used_in_place_and_installs_into_a_project(self):
        d = write_pack(self.tmp / "src" / "sample")
        row = packreg.add(d, out=quiet)
        self.assertEqual((row["name"], row["source"]), ("sample", "folder"))
        self.assertIn("sample", project.pack_names())
        self.assertEqual(project.pack_dir("sample"), d.resolve())
        root = self.make("Sample Game")
        admin.pack_add(root, "sample", out=quiet)
        self.assertTrue((root / ".agents/rules/octopus/sample/sample.md").is_file())
        self.assertTrue((root / ".agents/skills/sample-skill/SKILL.md").is_file())
        self.assertTrue((root / ".claude/skills/sample-skill/SKILL.md").is_file())
        self.assertEqual(project.load(root).problems, [])
        self.assertEqual(packreg.users("sample"), ["Sample Game"])
        with self.assertRaisesRegex(ValueError, "in use by Sample Game"):
            packreg.remove("sample", out=quiet)
        admin.uninstall(root, "sample", out=quiet)
        packreg.remove("sample", out=quiet)
        self.assertNotIn("sample", project.pack_names())
        self.assertTrue(d.is_dir())

    def test_a_git_pack_is_cloned_at_its_ref_and_updates(self):
        r = self.repo(sub="packs/engine")
        git(r, "tag", "v1")
        row = packreg.add(r.as_uri(), ref="v1", subpath="packs/engine", out=quiet)
        self.assertEqual(row["name"], "engine")
        self.assertEqual(row["source"], "git")
        first = row["commit"]
        self.assertEqual(project.pack_dir("engine"),
                         project.pack_clone_dir("engine") / "packs" / "engine")
        root = self.make("Sample Game")
        admin.pack_add(root, "engine", out=quiet)
        rule = r / "packs/engine/rules/sample.md"
        rule.write_text("# Sample rule, second\n", encoding="utf-8")
        git(r, "commit", "-q", "-am", "second")
        # Pinned to the tag: an update stays where it is.
        self.assertEqual(packreg.update("engine", out=quiet)["commit"], first)
        row = packreg.update("engine", ref="main", out=quiet)
        self.assertNotEqual(row["commit"], first)
        self.assertEqual((root / ".agents/rules/octopus/engine/sample.md").read_text(
            encoding="utf-8"), "# Sample rule, second\n")
        admin.uninstall(root, "engine", out=quiet)
        packreg.remove("engine", out=quiet)
        self.assertFalse(project.pack_clone_dir("engine").exists())

    def test_an_update_that_breaks_the_format_is_undone(self):
        r = self.repo()
        row = packreg.add(r.as_uri(), out=quiet)
        (r / "ruleset.json").write_text('{"title": "x", "nonsense": 1}', encoding="utf-8")
        git(r, "commit", "-q", "-am", "broken")
        with self.assertRaisesRegex(ValueError, "unknown key 'nonsense'"):
            packreg.update(row["name"], out=quiet)
        self.assertEqual(packreg._row(row["name"])["commit"], row["commit"])
        self.assertIn('"title": "Sample engine', (project.pack_dir(row["name"]) / "ruleset.json")
                      .read_text(encoding="utf-8"))

    def test_add_validates_the_pack_format(self):
        cases = {
            "no-title": ({"title": ""}, "has no `title`"),
            "bad-key": ({"colour": "red"}, "unknown key 'colour'"),
            "bad-from": ({"payload": [{"from": "missing/", "to": "x/"}]}, "missing/ is not in"),
            "bad-to": ({"payload": [{"from": "rules/", "to": "../outside/"}]}, "leaves the project"),
            "bad-requires": ({"requires": ["nowhere"]}, "no pack 'nowhere'"),
            "bad-brief": ({"brief": ["work"]}, "brief/work.md"),
            "bad-permit": ({"permits": {"outside": {"grants": "again"}}}, "already defined"),
        }
        for name, (extra, why) in cases.items():
            d = write_pack(self.tmp / "src" / name, extra=extra)
            with self.assertRaisesRegex(ValueError, why, msg=name):
                packreg.add(d, out=quiet)
        d = write_pack(self.tmp / "src" / "no-skill-md")
        (d / "skills" / "sample-skill" / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no `name`"):
            packreg.add(d, out=quiet)
        self.assertEqual([r for r in packreg.available() if r["source"] != "builtin"], [])

    def test_a_name_that_is_taken_is_refused(self):
        for name in ("core", "git"):
            d = write_pack(self.tmp / "src" / ("x-" + name))
            with self.assertRaisesRegex(ValueError, "called %s" % name):
                packreg.add(d, name=name, out=quiet)
        d = write_pack(self.tmp / "src" / "twice")
        packreg.add(d, out=quiet)
        with self.assertRaisesRegex(ValueError, "added already"):
            packreg.add(d, out=quiet)

    def test_a_failed_clone_leaves_nothing(self):
        with self.assertRaises(ValueError):
            packreg.add((self.tmp / "nowhere").as_uri() + "/", name="gone", out=quiet)
        self.assertFalse(project.pack_clone_dir("gone").exists())
        self.assertNotIn("gone", project.pack_names())

    def test_the_cli_lists_adds_and_removes(self):
        d = write_pack(self.tmp / "src" / "sample")
        rc, out, err = self.cli("pack-registry", "add", d)
        self.assertEqual(rc, 0, err)
        rc, out, err = self.cli("pack-registry", "list")
        self.assertIn("sample", out)
        self.assertIn("folder", out)
        rc, out, err = self.cli("pack-registry", "remove", "sample")
        self.assertEqual(rc, 0, err)


class PackSet(FixtureCase):
    def test_order_packs_adds_what_is_required_in_order(self):
        self.assertEqual(admin.order_packs(["ue-angelscript"]), ["unreal", "ue-angelscript"])
        self.assertEqual(admin.order_packs(["blueprint", "git", "ue-angelscript"]),
                         admin.order_packs(["git", "ue-angelscript", "blueprint"]))
        got = admin.order_packs(["blueprint", "git", "ue-angelscript"])
        self.assertLess(got.index("unreal"), got.index("blueprint"))
        self.assertLess(got.index("unreal"), got.index("ue-angelscript"))
        with self.assertRaisesRegex(ValueError, "no pack 'nope'"):
            admin.order_packs(["nope"])

    def test_install_with_packs_takes_the_base_and_those_packs(self):
        root = self.tmp / "game"
        root.mkdir()
        admin.install(root, None, "Sample Game", packs=["blueprint", "git"], out=quiet,
                      manager=False)
        pj = json.loads((root / ".octopus/project.json").read_text(encoding="utf-8"))
        self.assertEqual(pj["rulesets"][:2], ["core", "windows"])
        self.assertEqual(set(pj["rulesets"][2:]), {"unreal", "blueprint", "git"})
        self.assertEqual(pj["profile"], "custom")
        self.assertEqual(project.load(root).problems, [])
        other = self.tmp / "other"
        other.mkdir()
        admin.install(other, None, "Other Game", packs=["blueprint"], out=quiet, manager=False)
        pj = json.loads((other / ".octopus/project.json").read_text(encoding="utf-8"))
        self.assertEqual(pj["profile"], "unreal")

    def test_packs_set_adds_and_removes_to_match(self):
        root = self.make("Sample Game", profile="unreal-angelscript")
        added, removed = admin.packs_set(root, ["browser-game", "unreal"], out=quiet)
        self.assertEqual(added, ["browser-game"])
        self.assertEqual(set(removed), {"blueprint", "ue-angelscript"})
        pj = json.loads((root / ".octopus/project.json").read_text(encoding="utf-8"))
        self.assertEqual(pj["rulesets"], ["core", "windows", "unreal", "browser-game"])
        self.assertEqual(pj["profile"], "custom")
        self.assertEqual(project.load(root).problems, [])
        added, removed = admin.packs_set(root, [], out=quiet)
        self.assertEqual(set(removed), {"unreal", "browser-game"})
        pj = json.loads((root / ".octopus/project.json").read_text(encoding="utf-8"))
        self.assertEqual(pj["rulesets"], ["core", "windows"])
        self.assertEqual(pj["profile"], "none")

    def test_packs_set_keeps_the_projects_own_rulesets(self):
        root = self.make("Sample Game")
        own = root / ".octopus" / "rulesets"
        own.mkdir(parents=True)
        (own / "house.json").write_text('{"title": "the house rules"}', encoding="utf-8")
        pj = admin.settings(root)
        pj["rulesets"].append("house")
        admin._save_pj(root, pj)
        admin.packs_set(root, ["git"], out=quiet)
        self.assertEqual(admin.settings(root)["rulesets"], ["core", "windows", "git", "house"])

    def test_the_cli_sets_the_packs(self):
        root = self.make("Sample Game")
        rc, out, err = self.cli("packs", root, "set", "blueprint")
        self.assertEqual(rc, 0, err)
        self.assertIn("added unreal, blueprint", out)
        rc, out, err = self.cli("new", self.tmp / "fresh", "--name", "Fresh", "--packs", "git")
        self.assertEqual(rc, 0, err)
        pj = json.loads((self.tmp / "fresh/.octopus/project.json").read_text(encoding="utf-8"))
        self.assertEqual(pj["rulesets"], ["core", "windows", "git"])
