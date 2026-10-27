"""The board's Project view: every setting of a project, read and changed through admin.
python -m unittest discover tests"""

import json
import threading
import unittest
import urllib.error
import urllib.request

from support import FixtureCase, admin, project, quiet

import test_board_api
import todo_server  # noqa: E402


class ProjectPage(FixtureCase):
    call = test_board_api.BoardApi.call

    def setUp(self):
        super().setUp()
        self.root = self.make("Api")
        self.tid = self.new_ticket(self.root, "first one", "--permits", "outside")
        self.httpd = todo_server.make_server(0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def view(self, project_name="Api"):
        """GET /api/project. Returns (status, json)."""
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/project?project=%s" % (self.httpd.server_address[1],
                                                           project_name),
            headers={"X-Todo-Token": todo_server.TOKEN})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def config(self):
        return json.loads((self.root / ".octopus" / "project.json").read_text(encoding="utf-8"))

    def assert_record_holds(self):
        counts = admin.status(self.root, out=quiet)
        self.assertEqual((counts["edited"], counts["missing"]), (0, 0), counts["rows"])

    def test_the_view_carries_every_section(self):
        st, v = self.view()
        self.assertEqual(st, 200, v)
        for key in ("name", "slug", "description", "rulesets", "packs", "lanes",
                    "models", "efforts", "defaultModel", "defaultEffort", "terminal",
                    "permits", "groups", "categories", "problems", "installed"):
            self.assertIn(key, v)
        self.assertEqual((v["name"], v["slug"]), ("Api", "api"))
        self.assertNotIn("profile", v)
        self.assertEqual([r["name"] for r in v["rulesets"]], ["core", "windows"])
        browser = next(p for p in v["packs"] if p["name"] == "browser-game")
        self.assertFalse(browser["installed"])
        blueprint = next(p for p in v["packs"] if p["name"] == "blueprint")
        self.assertEqual(blueprint["requires"], ["unreal"])
        self.assertEqual([ln["dir"] for ln in v["lanes"]][0], "Pipeline")
        self.assertEqual(v["terminal"]["board"], "external")
        outside = next(p for p in v["permits"] if p["name"] == "outside")
        self.assertEqual(outside["tickets"], 1)
        self.assertEqual(v["groups"]["store"], "map")
        self.assertIn("project code", v["categories"]["all"])
        self.assertEqual(v["installed"]["edited"], 0)
        self.assertTrue(v["installed"]["rows"])

    def test_each_op_changes_the_config_and_keeps_the_record(self):
        ops = [
            ({"op": "meta", "name": "Api", "description": "the test project"},
             lambda c: c["description"] == "the test project"),
            ({"op": "models", "models": {"big": "claude-opus-5-5", "small": "claude-haiku-5-5"},
              "efforts": ["low", "high"], "defaultModel": "small", "defaultEffort": "low"},
             lambda c: c["defaultModel"] == "small" and c["efforts"] == ["low", "high"]),
            ({"op": "groups", "store": "files", "required": True},
             lambda c: c["groups"] == {"store": "files", "required": True}),
            ({"op": "categories", "categories": {"docs": "the written guides"}},
             lambda c: c["categories"] == {"docs": "the written guides"}),
        ]
        for body, holds in ops:
            st, r = self.call("project", body)
            self.assertEqual(st, 200, (body, r))
            self.assertTrue(holds(self.config()), (body, self.config()))
            self.assert_record_holds()
        _st, v = self.view()
        self.assertEqual(v["defaultModel"], "small")
        self.assertEqual(v["groups"]["store"], "files")
        self.assertIn("docs", v["categories"]["all"])

    def test_a_rename_serves_the_project_under_its_new_name(self):
        st, r = self.call("project", {"op": "meta", "name": "Renamed", "description": ""})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["project"], "Renamed")
        self.assertEqual(r["snapshot"]["project"]["name"], "Renamed")
        self.assertEqual(self.config()["name"], "Renamed")
        self.assertIn("Renamed", [x["name"] for x in project.load_registry()])
        self.assertEqual(self.view("Renamed")[0], 200)
        self.assert_record_holds()

    def test_pack_add_then_remove(self):
        st, r = self.call("project", {"op": "packAdd", "pack": "browser-game"})
        self.assertEqual(st, 200, r)
        self.assertIn("browser-game", self.config()["rulesets"])
        self.assertTrue((self.root / "run.bat").exists())
        self.assert_record_holds()
        st, r = self.call("project", {"op": "packRemove", "pack": "browser-game"})
        self.assertEqual(st, 200, r)
        self.assertNotIn("browser-game", self.config()["rulesets"])
        self.assertFalse((self.root / "run.bat").exists())
        self.assert_record_holds()

    def test_pack_remove_never_takes_out_the_whole_project(self):
        for pack in ("", "core", "nope"):
            st, r = self.call("project", {"op": "packRemove", "pack": pack})
            self.assertEqual(st, 400, r)
        self.assertTrue((self.root / ".octopus" / "installed.json").exists())
        self.assertEqual(self.config()["rulesets"], ["core", "windows"])

    def test_packs_set_ticks_any_number_of_packs(self):
        st, r = self.call("project", {"op": "packsSet", "packs": ["blueprint", "browser-game"]})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.config()["rulesets"],
                         ["core", "windows", "unreal", "blueprint", "browser-game"])
        self.assertNotIn("profile", self.config())
        st, v = self.view()
        on = {p["name"] for p in v["packs"] if p["installed"]}
        self.assertEqual(on, {"unreal", "blueprint", "browser-game"})
        st, r = self.call("project", {"op": "packsSet", "packs": ["browser-game"]})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.config()["rulesets"], ["core", "windows", "browser-game"])
        self.assert_record_holds()
        st, r = self.call("project", {"op": "packsSet", "packs": "browser-game"})
        self.assertEqual(st, 400, r)

    def test_global_packs_add_a_folder_pack_every_project_can_tick(self):
        import test_packreg
        d = test_packreg.write_pack(self.tmp / "src" / "sample")
        st, r = self.call("packs", {"op": "add", "source": str(d)}, project_name="")
        self.assertEqual(st, 200, r)
        row = next(p for p in r["packs"] if p["name"] == "sample")
        self.assertEqual((row["source"], row["users"]), ("folder", []))
        st, v = self.view()
        self.assertIn("sample", [p["name"] for p in v["packs"]])
        st, r = self.call("project", {"op": "packsSet", "packs": ["sample"]})
        self.assertEqual(st, 200, r)
        st, r = self.call("packs", {"op": "remove", "name": "sample"}, project_name="")
        self.assertEqual(st, 400, r)
        self.assertIn("in use by Api", r["error"])
        st, r = self.call("packs", {"op": "add", "source": str(self.tmp / "nowhere")},
                          project_name="")
        self.assertEqual(st, 400, r)

    def test_add_project_takes_ticked_packs(self):
        st, r = self.call("addProject", {"path": str(self.tmp / "fresh"), "name": "Fresh",
                                         "packs": ["ue-angelscript"]}, project_name="")
        self.assertEqual(st, 200, r)
        pj = json.loads((self.tmp / "fresh/.octopus/project.json").read_text(encoding="utf-8"))
        self.assertEqual(pj["rulesets"], ["core", "windows", "unreal", "ue-angelscript"])

    def test_skills_install_list_and_remove(self):
        import test_skills
        test_skills.skill(self.tmp / "src", "alpha", "1.0")
        st, r = self.call("skills", {"op": "available", "folder": str(self.tmp / "src")})
        self.assertEqual(st, 200, r)
        self.assertEqual([s["name"] for s in r["available"]["skills"]], ["alpha"])
        st, r = self.call("skills", {"op": "add", "folder": str(self.tmp / "src"),
                                     "skills": ["alpha"]})
        self.assertEqual(st, 200, r)
        self.assertEqual([(s["name"], s["state"]) for s in r["installed"]], [("alpha", "same")])
        st, v = self.view()
        self.assertEqual([s["name"] for s in v["skills"]["installed"]], ["alpha"])
        self.assertIn("codex", [lib["name"] for lib in v["skills"]["libraries"]])
        st, r = self.call("skills", {"op": "remove", "skills": ["alpha"]})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["installed"], [])
        self.assert_record_holds()

    def test_a_bad_default_model_is_refused_and_the_file_is_unchanged(self):
        p = self.root / ".octopus" / "project.json"
        before = p.read_bytes()
        st, r = self.call("project", {"op": "models", "defaultModel": "gpt"})
        self.assertEqual(st, 400, r)
        self.assertIn("defaultModel", r["error"])
        self.assertEqual(p.read_bytes(), before)

    def test_set_settings_refuses_the_slug_and_a_bad_store(self):
        p = self.root / ".octopus" / "project.json"
        before = p.read_bytes()
        with self.assertRaises(ValueError):
            admin.set_settings(self.root, slug="other")
        with self.assertRaises(ValueError):
            admin.set_settings(self.root, groups={"store": "heap"})
        self.assertEqual(p.read_bytes(), before)

    def test_a_category_in_use_is_kept(self):
        self.call("project", {"op": "categories", "categories": {"docs": "the guides"}})
        self.new_ticket(self.root, "a guide", "--category", "docs")
        st, r = self.call("project", {"op": "categories", "categories": {}})
        self.assertEqual(st, 400, r)
        self.assertIn("docs", self.config()["categories"])

    def test_remove_project_unregisters_it(self):
        self.assertIn("Api", [x["name"] for x in project.load_registry()])
        st, r = self.call("removeProject", {"name": "Api"})
        self.assertEqual(st, 200, r)
        self.assertNotIn("Api", [x["name"] for x in project.load_registry()])
        self.assertTrue((self.root / ".octopus" / "project.json").exists())
        st, r = self.call("removeProject", {"name": "Api"})
        self.assertEqual(st, 404, r)

    def test_the_board_remembers_the_project_view(self):
        st, r = self.call("board", {"view": "project"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["board"]["view"], "project")


if __name__ == "__main__":
    unittest.main()
