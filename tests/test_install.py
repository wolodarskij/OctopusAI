"""Install, uninstall and packs.   python -m unittest discover tests"""

import json
import unittest

from support import FixtureCase, admin, project, quiet, tree_hash

# The `--packs` of a new project -> the rulesets it gets.
CHOICES = {
    "": ["core", "windows"],
    "git": ["core", "windows", "git"],
    "blueprint": ["core", "windows", "unreal", "blueprint"],
    "ue-angelscript,browser-game": ["core", "windows", "browser-game", "unreal", "ue-angelscript"],
}


def owners(root):
    rec = json.loads((root / ".octopus" / "installed.json").read_text(encoding="utf-8"))
    return {v["by"] for v in rec["files"].values()}


class Install(FixtureCase):
    def test_new_takes_exactly_the_packs_it_names(self):
        for i, (packs, rulesets) in enumerate(CHOICES.items()):
            root = self.tmp / ("p-%d" % i)
            args = ["--packs", packs] if packs else []
            rc, _out, err = self.cli("new", root, "--name", "P %d" % i, *args)
            self.assertEqual(rc, 0, err)
            pj = json.loads((root / ".octopus" / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(pj["rulesets"], rulesets)
            self.assertNotIn("profile", pj)
            p = project.load(root)
            self.assertEqual(p.problems, [])
            self.assertEqual(p.RULES["rulesets"], rulesets)
            packs = {r for r in rulesets if r not in ("core", "windows")}
            with_payload = {r for r in owners(root) if r not in ("core", "windows", "agents")}
            self.assertEqual(with_payload, packs)
            has_editor = "editor" in p.PERMITS
            self.assertEqual(has_editor, "unreal" in rulesets)
        names = [r["name"] for r in project.load_registry()]
        self.assertEqual(names, ["P %d" % i for i in range(len(CHOICES))])

    def test_install_then_uninstall_leaves_the_tree_as_it_was(self):
        root = self.tmp / "keep"
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "settings.json").write_bytes(
            b'{\r\n  "permissions": {"allow": ["Bash(ls)"]}\r\n}\r\n')
        (root / "src").mkdir()
        (root / "src" / "main.ts").write_text("export const x = 1;\n", encoding="utf-8")
        before = tree_hash(root)
        admin.install(root, "unreal-angelscript", "Keep", out=quiet)
        self.assertNotEqual(tree_hash(root), before)
        settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("SessionStart", settings["hooks"])
        hooks = {ev: g[0] for ev, g in settings["hooks"].items()}
        self.assertEqual(hooks["PreToolUse"]["matcher"], "AskUserQuestion|ExitPlanMode")
        self.assertIn("permission_prompt", hooks["Notification"]["matcher"])
        self.assertNotIn("matcher", hooks["PostToolUse"])
        self.assertTrue(hooks["PostToolUse"]["hooks"][0]["async"])
        self.assertNotIn("async", hooks["Stop"]["hooks"][0])
        self.assertNotIn("async", hooks["StopFailure"]["hooks"][0])
        # The owner's own rule stays, and install adds the rule that lets a session close itself.
        self.assertEqual(settings["permissions"], {"allow": ["Bash(ls)"] + admin.SESSION_ALLOW})
        admin.uninstall(root, out=quiet)
        self.assertEqual(tree_hash(root), before)
        self.assertEqual(project.load_registry(), [])

    def test_uninstall_of_one_pack_keeps_the_pack_below_it(self):
        root = self.make("Layers", ["unreal", "blueprint", "ue-angelscript"])
        rec = json.loads((root / ".octopus" / "installed.json").read_text(encoding="utf-8"))
        unreal_files = [k for k, v in rec["files"].items() if v["by"] == "unreal"]
        as_files = [k for k, v in rec["files"].items() if v["by"] == "ue-angelscript"]
        self.assertTrue(unreal_files and as_files)
        admin.uninstall(root, "ue-angelscript", out=quiet)
        for f in unreal_files:
            self.assertTrue((root / f).exists(), f)
        for f in as_files:
            self.assertFalse((root / f).exists(), f)
        self.assertEqual(project.load(root).RULES["rulesets"],
                         ["core", "windows", "unreal", "blueprint"])

    def test_uninstall_of_a_required_pack_removes_its_dependents_and_says_so(self):
        root = self.make("Chain", ["unreal", "blueprint", "ue-angelscript"])
        said = []
        gone = admin.uninstall(root, "unreal", out=said.append)
        self.assertEqual(sorted(gone), ["blueprint", "ue-angelscript", "unreal"])
        self.assertIn("also removes blueprint, ue-angelscript", said[0])
        self.assertEqual(project.load(root).RULES["rulesets"], ["core", "windows"])

    def test_uninstall_refuses_an_edited_file(self):
        root = self.make("Edited", ["unreal", "blueprint"])
        rec = json.loads((root / ".octopus" / "installed.json").read_text(encoding="utf-8"))
        one = next(k for k, v in rec["files"].items() if v["by"] == "unreal")
        (root / one).write_text("changed by hand\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            admin.uninstall(root, "unreal", out=quiet)
        lines = []
        counts = admin.status(root, out=lines.append)
        self.assertEqual(counts["edited"], 1)

    def test_packs_add_puts_a_pack_in_and_checks_requires(self):
        root = self.make("Grow")
        with self.assertRaises(ValueError):
            admin.pack_add(root, "ue-angelscript", out=quiet)
        admin.pack_add(root, "unreal", out=quiet)
        admin.pack_add(root, "blueprint", out=quiet)
        admin.pack_add(root, "ue-angelscript", out=quiet)
        self.assertEqual(project.load(root).RULES["rulesets"],
                         ["core", "windows", "unreal", "blueprint", "ue-angelscript"])
        self.assertIn("ue-angelscript", owners(root))

    def test_the_shim_runs_the_engine_for_its_own_project(self):
        import subprocess
        import sys
        root = self.make("Shim")
        self.new_ticket(root, "shim ticket")
        r = subprocess.run([sys.executable, str(root / ".octopus" / "todo.py"), "query", "--ids"],
                           capture_output=True, text=True, cwd=str(self.tmp))
        self.assertEqual(r.stdout.strip(), "T0001")


PLUGIN = admin.PACKS / "blueprint" / "Plugins" / "AssetTextExport"


class PluginPayload(FixtureCase):
    """The blueprint pack owns the project's copy of the AssetTextExport plugin."""

    def test_install_puts_the_plugin_in_plugins_tools_and_asks_for_a_build(self):
        root = self.make("Fresh", ["unreal", "blueprint"])
        copy = root / "Plugins" / "Tools" / "AssetTextExport"
        for src in PLUGIN.rglob("*"):
            if src.is_file() and "__pycache__" not in src.parts:
                self.assertTrue((copy / src.relative_to(PLUGIN)).is_file(), src)
        self.assertEqual((copy / "AssetTextExport.uplugin").read_bytes(),
                         (PLUGIN / "AssetTextExport.uplugin").read_bytes())
        self.assertTrue((root / ".octopus" / "build-pending").is_file())

    def test_install_replaces_a_hand_copy_where_the_project_keeps_it(self):
        root = self.tmp / "Hand"
        old = root / "Plugins" / "AssetTextExport"
        (old / "Binaries" / "Win64").mkdir(parents=True)
        (old / "Binaries" / "Win64" / "UnrealEditor-AssetTextExport.dll").write_bytes(b"built")
        (old / "AssetTextExport.uplugin").write_text('{"Installed": true}', encoding="utf-8")
        (old / "Notes.txt").write_text("the owner's own file\n", encoding="utf-8")
        admin.install(root, "unreal", "Hand", register=False, out=quiet)
        self.assertFalse((root / "Plugins" / "Tools").exists())
        self.assertEqual((old / "AssetTextExport.uplugin").read_bytes(),
                         (PLUGIN / "AssetTextExport.uplugin").read_bytes())
        self.assertTrue((old / "Source" / "AssetTextExport" / "Private" / "GraphBody.cpp").is_file())
        self.assertEqual((old / "Binaries" / "Win64" / "UnrealEditor-AssetTextExport.dll").read_bytes(),
                         b"built")
        self.assertTrue((old / "Notes.txt").is_file())
        self.assertEqual(admin.status(root, out=quiet)["edited"], 0)

    def test_the_session_start_hook_restores_the_copy_to_the_pack(self):
        root = self.make("Hooked", ["unreal", "blueprint"])
        (root / ".octopus" / "build-pending").unlink()
        readme = root / "Plugins" / "Tools" / "AssetTextExport" / "README.md"
        readme.write_text("an old copy\n", encoding="utf-8")
        payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "s1",
                              "source": "startup", "cwd": str(root)})
        rc, _out, err = self.cli("hook", stdin=payload)
        self.assertEqual(rc, 0, err)
        self.assertEqual(readme.read_bytes(), admin._fill((PLUGIN / "README.md").read_bytes(),
                                                          admin._values(root, {"name": "Hooked"})))
        self.assertTrue((root / ".octopus" / "build-pending").is_file())
        self.assertFalse((root / ".octopus" / admin.REFRESH_LOCK).exists())

    def test_uninstall_of_the_pack_takes_the_plugin_and_the_marker_out(self):
        root = self.make("Out", ["unreal", "blueprint"])
        admin.uninstall(root, "blueprint", out=quiet)
        copy = root / "Plugins" / "Tools" / "AssetTextExport"
        self.assertFalse((copy / "AssetTextExport.uplugin").exists())
        self.assertFalse((root / ".octopus" / "build-pending").exists())


class PackChange(FixtureCase):
    """An update after a pack change, on a pack built in the test directory."""

    def setUp(self):
        super().setUp()
        self.pack = self.tmp / "packs" / "fake"
        (self.pack / "Plugins" / "Thing" / "Source").mkdir(parents=True)
        (self.pack / "rules").mkdir()
        (self.pack / "rules" / "r.md").write_text("rule 1\n", encoding="utf-8")
        (self.pack / "Plugins" / "Thing" / "Thing.uplugin").write_text("v1\n", encoding="utf-8")
        (self.pack / "Plugins" / "Thing" / "Source" / "a.cpp").write_text("a1\n", encoding="utf-8")
        (self.pack / "Plugins" / "Thing" / "Source" / "b.cpp").write_text("b1\n", encoding="utf-8")
        (self.pack / "ruleset.json").write_text(json.dumps({"title": "fake", "payload": [
            {"from": "rules/", "to": ".claude/rules/octopus/fake/"},
            {"from": "Plugins/Thing/", "to": "Plugins/Tools/Thing/", "find": "Thing.uplugin",
             "replace": True, "pending": ".octopus/build-pending"}]}), encoding="utf-8")
        real = admin._ruleset_file
        self._real = real
        admin._ruleset_file = lambda n: ((self.pack / "ruleset.json", self.pack) if n == "fake"
                                         else real(n))
        self.root = self.make("Changed", register=False)
        admin.pack_add(self.root, "fake", out=quiet)
        (self.root / ".octopus" / "build-pending").unlink()

    def tearDown(self):
        admin._ruleset_file = self._real
        super().tearDown()

    def test_refresh_follows_the_pack_and_asks_for_a_build(self):
        copy = self.root / "Plugins" / "Tools" / "Thing"
        self.assertEqual(admin.refresh(self.root, out=quiet), 0)
        self.assertFalse((self.root / ".octopus" / "build-pending").exists())
        (self.pack / "Plugins" / "Thing" / "Source" / "a.cpp").write_text("a2\n", encoding="utf-8")
        (self.pack / "Plugins" / "Thing" / "Source" / "b.cpp").unlink()
        (self.pack / "Plugins" / "Thing" / "Source" / "c.cpp").write_text("c1\n", encoding="utf-8")
        (copy / "Thing.uplugin").write_text("edited in the project\n", encoding="utf-8")
        (self.pack / "rules" / "r.md").write_text("rule 2\n", encoding="utf-8")
        self.assertEqual(admin.refresh(self.root, out=quiet), 4)
        self.assertEqual((copy / "Source" / "a.cpp").read_text(encoding="utf-8"), "a2\n")
        self.assertFalse((copy / "Source" / "b.cpp").exists())
        self.assertEqual((copy / "Source" / "c.cpp").read_text(encoding="utf-8"), "c1\n")
        self.assertEqual((copy / "Thing.uplugin").read_text(encoding="utf-8"), "v1\n")
        self.assertTrue((self.root / ".octopus" / "build-pending").is_file())
        # Refresh takes only what the pack owns; the rule follows on `update`.
        rule = self.root / ".claude" / "rules" / "octopus" / "fake" / "r.md"
        self.assertEqual(rule.read_text(encoding="utf-8"), "rule 1\n")
        self.assertEqual(admin.refresh(self.root, out=quiet), 0)
        admin.update(self.root, out=quiet)
        self.assertEqual(rule.read_text(encoding="utf-8"), "rule 2\n")
        self.assertEqual(admin.status(self.root, out=quiet)["edited"], 0)

    def test_refresh_leaves_the_work_to_a_holder_of_the_lock(self):
        (self.pack / "Plugins" / "Thing" / "Thing.uplugin").write_text("v2\n", encoding="utf-8")
        lock = self.root / ".octopus" / admin.REFRESH_LOCK
        lock.write_text("", encoding="utf-8")
        self.assertEqual(admin.refresh(self.root, out=quiet), 0)
        lock.unlink()
        self.assertEqual(admin.refresh(self.root, out=quiet), 1)


class PackRename(FixtureCase):
    """A renamed pack: the old name is an alias, and update or the SessionStart hook moves a
    project that still lists it to the new name."""

    def regress(self, root, old, new, profile=None):
        """`profile` puts back the key an older install wrote."""
        """Put the project back as an install under the old pack name left it."""
        cd = root / ".octopus"
        pj = json.loads((cd / "project.json").read_text(encoding="utf-8"))
        pj["rulesets"] = [old if n == new else n for n in pj["rulesets"]]
        if profile:
            pj["profile"] = profile
        data = (json.dumps(pj, indent=2) + "\n").encode("utf-8")
        (cd / "project.json").write_bytes(data)
        rec = json.loads((cd / "installed.json").read_text(encoding="utf-8"))
        src, dst = ".agents/rules/octopus/%s/" % new, ".agents/rules/octopus/%s/" % old
        files = {}
        for rel, v in rec["files"].items():
            if v["by"] == new:
                v["by"] = old
            if rel.startswith(src):
                (root / dst).mkdir(parents=True, exist_ok=True)
                (root / rel).rename(root / (dst + rel[len(src):]))
                rel = dst + rel[len(src):]
            files[rel] = v
        files[".octopus/project.json"]["hash"] = admin._sha(data)
        rec["files"] = files
        if profile:
            rec["profile"] = profile
        (root / src).rmdir()
        (cd / "installed.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")

    def assert_moved(self, root, old, new):
        cd = root / ".octopus"
        pj = json.loads((cd / "project.json").read_text(encoding="utf-8"))
        self.assertIn(new, pj["rulesets"])
        self.assertNotIn(old, pj["rulesets"])
        self.assertFalse((root / ".agents/rules/octopus" / old).exists())
        self.assertTrue(any((root / ".agents/rules/octopus" / new).iterdir()))
        self.assertIn(new, owners(root))
        self.assertNotIn(old, owners(root))
        counts = admin.status(root, out=quiet)
        self.assertEqual((counts["edited"], counts["missing"]), (0, 0))
        self.assertIn(".agents/rules/octopus/%s/" % new,
                      (root / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_an_old_name_still_loads_as_the_new_pack(self):
        root = self.make("Old", ["unreal", "blueprint", "ue-angelscript"])
        self.regress(root, "angelscript", "ue-angelscript")
        p = project.load(root)
        self.assertEqual(p.problems, [])
        self.assertEqual(p.RULES["rulesets"][-1], "ue-angelscript")

    def test_update_moves_a_project_to_the_new_name_and_its_settings_file(self):
        root = self.make("Moved", ["unreal", "blueprint", "ue-angelscript"])
        self.regress(root, "angelscript", "ue-angelscript")
        (root / ".octopus" / "angelscript.json").write_text('{"a": 1}', encoding="utf-8")
        admin.update(root, out=quiet)
        self.assert_moved(root, "angelscript", "ue-angelscript")
        self.assertFalse((root / ".octopus" / "angelscript.json").exists())
        self.assertEqual(json.loads((root / ".octopus" / "ue-angelscript.json")
                                    .read_text(encoding="utf-8")), {"a": 1})
        rule = (root / ".agents/rules/octopus/ue-angelscript/angelscript.md").read_text(
            encoding="utf-8")
        self.assertNotIn("packs/angelscript/", rule)

    def test_the_session_start_refresh_moves_the_browser_pack_and_drops_the_profile(self):
        root = self.make("Game", ["browser-game"])
        self.regress(root, "browser", "browser-game", profile="browser")
        self.assertGreater(admin.refresh(root, out=quiet), 0)
        self.assert_moved(root, "browser", "browser-game")
        pj = json.loads((root / ".octopus" / "project.json").read_text(encoding="utf-8"))
        self.assertNotIn("profile", pj)
        rec = json.loads((root / ".octopus" / "installed.json").read_text(encoding="utf-8"))
        self.assertNotIn("profile", rec)
        self.assertEqual(admin.refresh(root, out=quiet), 0)

    def test_the_command_line_takes_the_old_names(self):
        root = self.tmp / "p-old"
        rc, _out, err = self.cli("new", root, "--name", "P old", "--profile", "browser")
        self.assertEqual(rc, 0, err)
        pj = json.loads((root / ".octopus" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(pj["rulesets"], ["core", "windows", "browser-game"])
        self.assertNotIn("profile", pj)
        admin.uninstall(root, "browser", out=quiet)
        admin.pack_add(root, "unreal", out=quiet)
        admin.pack_add(root, "angelscript", out=quiet)
        self.assertEqual(project.load(root).RULES["rulesets"],
                         ["core", "windows", "unreal", "ue-angelscript"])
        self.assertEqual(admin.order_packs(["angelscript", "browser"]),
                         ["browser-game", "unreal", "ue-angelscript"])


if __name__ == "__main__":
    unittest.main()
