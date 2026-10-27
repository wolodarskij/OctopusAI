"""The pack tools: the split between the `blueprint` pack and the `ue-angelscript` pack.

A tool of the `ue-angelscript` pack reads `Script/` and the Blueprint export, and never a Blueprint.
Each test builds a small project in a temporary directory: a few hand-written `.as` files and a
hand-written export file. No test reads a real project.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

OCTOPUS = Path(__file__).resolve().parents[1]
PACKS = OCTOPUS / "packs"
AS_TOOLS = PACKS / "ue-angelscript" / "tools"
BP_TOOLS = PACKS / "blueprint" / "tools"


class Fixture(unittest.TestCase):
    """A temporary project with `.octopus/project.json` and `.octopus/ue-angelscript.json`."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="octopus-packs-"))
        (self.root / ".octopus").mkdir()
        self.write(".octopus/project.json", json.dumps({"name": "F", "slug": "f", "rulesets": []}))
        self.write(".octopus/ue-angelscript.json", "{}")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def export(self, kind, data, export_dir=".octopus/export/blueprint"):
        doc = {"kind": kind, "schema": 1, "generatedBy": "test", "generated": "test"}
        doc.update(data)
        self.write("%s/%s.json" % (export_dir, kind), json.dumps(doc))

    def run_tool(self, tool, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        env.pop("OCTOPUS_PROJECT", None)
        r = subprocess.run([sys.executable, str(AS_TOOLS / tool), "--project", str(self.root)]
                           + [str(a) for a in args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, cwd=str(self.root), timeout=60)
        return r.returncode, r.stdout, r.stderr

    def assert_missing_export(self, tool, kind, *args):
        rc, out, err = self.run_tool(tool, *args)
        lines = [l for l in (out + err).splitlines() if l.strip()]
        self.assertEqual(rc, 2, out + err)
        self.assertEqual(len(lines), 1, out + err)
        self.assertIn("blueprint_export.py", lines[0])
        self.assertIn(" %s " % kind, lines[0])
        self.assertIn("%s.json" % kind, lines[0])


class CheckAssetPaths(Fixture):
    def setUp(self):
        super().setUp()
        self.write("Script/Game/Loader.as",
                   'class ULoader\n{\n    FString A = "/Game/Data/Table.Table";\n'
                   '    FString B = "/Game/Data/Gone.Gone";\n}\n')

    def test_reads_the_package_export(self):
        self.export("packages", {"packages": ["/Game/Data/Table"]})
        rc, out, err = self.run_tool("check_asset_paths.py")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("MISSING /Game/Data/Gone.Gone", out)
        self.assertNotIn("MISSING /Game/Data/Table.Table", out)
        self.assertIn("2 literal object path(s)", out)

    def test_clean_when_every_package_is_exported(self):
        self.export("packages", {"packages": ["/Game/Data/Table", "/Game/Data/Gone"]})
        rc, out, err = self.run_tool("check_asset_paths.py")
        self.assertEqual(rc, 0, out + err)

    def test_export_dir_comes_from_blueprint_json(self):
        self.write(".octopus/blueprint.json", json.dumps({"exportDir": "Exports/bp"}))
        self.export("packages", {"packages": ["/Game/Data/Table", "/Game/Data/Gone"]},
                    export_dir="Exports/bp")
        rc, out, err = self.run_tool("check_asset_paths.py")
        self.assertEqual(rc, 0, out + err)

    def test_missing_export(self):
        self.assert_missing_export("check_asset_paths.py", "packages")


class CheckStringTableKeys(Fixture):
    def setUp(self):
        super().setUp()
        self.write("Script/UI/Labels.as",
                   'class ULabels\n{\n'
                   '    FText A = FText::FromStringTable(n"/Game/Text/ST_Main.ST_Main", "Hello");\n'
                   '    FText B = FText::FromStringTable(n"/Game/Text/ST_Main.ST_Main", "Absent");\n'
                   '}\n')

    def test_reads_the_stringtable_export(self):
        self.export("stringtables", {"tables": {"/Game/Text/ST_Main.ST_Main": {"Hello": "Hi"}},
                                     "unreadable": []})
        rc, out, err = self.run_tool("check_string_table_keys.py")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("names /Game/Text/ST_Main.ST_Main / Absent", out)
        self.assertNotIn("/ Hello", out)
        self.assertIn("2 sites, 1 keys no table carries", out)

    def test_missing_export(self):
        self.assert_missing_export("check_string_table_keys.py", "stringtables")


class CheckMemberSpecifiers(Fixture):
    def setUp(self):
        super().setUp()
        self.write("Script/Gameplay/HealthComponent.as",
                   "class UHealthComponent : UActorComponent\n{\n"
                   "    UPROPERTY(Replicated)\n    int32 Health = 3;\n\n"
                   "    UPROPERTY()\n    TArray<int32> Marks;\n}\n")

    def test_reads_the_variables_export(self):
        self.export("variables", {
            "blueprints": {"BP_Health": {"exports": [{
                "file": "TextBP_Health-1.txt", "decoded": True,
                "variables": {"Health": {"container": "None", "category": "int", "flags": 0},
                              "Marks": {"container": "Array", "category": "int", "flags": 0}}}]}},
            "parents": {"BP_Health": {"path": "Content/BP_Health.x", "chain": ["HealthComponent"],
                                      "landing": "script", "root": "HealthComponent"}}})
        rc, out, err = self.run_tool("check_member_specifiers.py")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("== HealthComponent.as", out)
        self.assertIn("REPLICATION script=True bp=False", out)
        self.assertIn("mismatches: 1", out)
        self.assertIn("verified: 1 against the current export", out)

    def test_missing_export(self):
        self.assert_missing_export("check_member_specifiers.py", "variables")


STRUCT_AS = ("struct FScoreRow\n{\n"
             "    UPROPERTY()\n    int32 Score = 0;\n\n"
             "    UPROPERTY(SaveGame)\n    TArray<FName> Tags;\n}\n")


def struct_export(score_category="int"):
    return {"structs": {"ScoreRow": {
        "path": "Content/Data/ScoreRow.x", "fieldsError": None, "typesError": None,
        "fields": [
            {"name": "Score", "raw": "Score_2_0", "saveGame": True,
             "container": "EPinContainerType::None", "friendly": "Score", "category": ""},
            {"name": "Tags", "raw": "Tags_3_0", "saveGame": True,
             "container": "EPinContainerType::Array", "friendly": "Tags", "category": ""}],
        "types": [
            {"name": "Score", "container": "EPinContainerType::None",
             "category": score_category, "terminal": "None"},
            {"name": "Tags", "container": "EPinContainerType::Array", "category": "name",
             "terminal": "None"}]}},
        "errors": [], "control": {"status": "skipped", "struct": None, "problems": []}}


class CheckStructSpecifiers(Fixture):
    def setUp(self):
        super().setUp()
        self.write("Script/DataTypes/ScoreRow.as", STRUCT_AS)

    def test_reads_the_structs_export(self):
        self.export("structs", struct_export())
        rc, out, err = self.run_tool("check_struct_specifiers.py", "--json")
        self.assertEqual(rc, 1, out + err)
        report = json.loads(out)
        self.assertEqual(report["fields_compared"], 2)
        self.assertEqual([(d["struct"], d["field"], d["axis"]) for d in report["defects"]],
                         [("FScoreRow", "Score", "SAVEGAME LOST")])

    def test_missing_export(self):
        self.assert_missing_export("check_struct_specifiers.py", "structs")


class CheckStructTypes(Fixture):
    def setUp(self):
        super().setUp()
        self.write("Script/DataTypes/ScoreRow.as", STRUCT_AS)

    def test_reads_the_structs_export(self):
        self.export("structs", struct_export(score_category="text"))
        rc, out, err = self.run_tool("check_struct_types.py")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("FScoreRow.Score", out)
        self.assertIn("asset says `text`", out)
        self.assertIn("compared 2 fields", out)

    def test_clean_types(self):
        self.export("structs", struct_export())
        rc, out, err = self.run_tool("check_struct_types.py")
        self.assertEqual(rc, 0, out + err)

    def test_failed_control_stops_the_audit(self):
        data = struct_export()
        data["control"] = {"status": "failed", "struct": "FKnown", "problems": ["control x: bad"]}
        self.export("structs", data)
        rc, out, err = self.run_tool("check_struct_types.py")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("THE DECODER IS WRONG", out)

    def test_missing_export(self):
        self.assert_missing_export("check_struct_types.py", "structs")


class CollisionCensus(Fixture):
    def setUp(self):
        super().setUp()
        self.write("Script/Gameplay/DoorBase.as",
                   "class UDoorBase : UObject\n{\n"
                   "    UPROPERTY()\n    int32 Health = 3;\n\n"
                   "    UFUNCTION()\n    void Open()\n    {\n    }\n\n"
                   "    UFUNCTION(BlueprintEvent)\n    void OnOpened()\n    {\n    }\n}\n")

    def export_blueprints(self):
        self.export("blueprints", {"assetsScanned": 2, "unparsable": [], "blueprints": {
            "BP_Door": {"path": "Content/BP_Door.x", "parent": "DoorBase", "parentIsScript": True,
                        "scriptParent": "DoorBase", "hops": 0, "bodyError": None,
                        "variables": [{"name": "Health", "category": "int", "subName": "",
                                       "subClass": "", "container": ""},
                                      {"name": "Note", "category": "string", "subName": "",
                                       "subClass": "", "container": ""}],
                        "functionGraphs": ["Open", "OnOpened"], "macroGraphs": [],
                        "ubergraphPages": ["EventGraph"], "delegateGraphs": [],
                        "customEvents": [], "overrides": []},
            "BP_Lamp": {"path": "Content/BP_Lamp.x", "parent": "Actor", "parentIsScript": False,
                        "scriptParent": None, "hops": None, "bodyError": None}}})

    def test_reads_the_blueprints_export(self):
        self.export_blueprints()
        rc, out, err = self.run_tool("collision_census.py", "--json")
        self.assertEqual(rc, 1, out + err)
        report = json.loads(out)
        found = sorted((f["asset"], f["kind"], f["name"], f["severity"]) for f in report["findings"])
        self.assertEqual(found, [("BP_Door", "function graph", "Open", ""),
                                 ("BP_Door", "variable", "Health", "SAFE")])
        self.assertEqual([(s["asset"], s["name"]) for s in report["seams"]],
                         [("BP_Door", "OnOpened")])
        self.assertEqual(report["script_parented"], 1)

    def test_missing_export(self):
        self.assert_missing_export("collision_census.py", "blueprints")


class ComponentDupes(Fixture):
    def setUp(self):
        super().setUp()
        self.write("Script/Gameplay/Mover.as",
                   "class UMoverComponent : UActorComponent\n{\n}\n\n"
                   "class ACart : AActor\n{\n"
                   "    UPROPERTY(DefaultComponent)\n    UMoverComponent Mover;\n}\n")

    def test_reads_the_components_export(self):
        self.export("components", {"dropped": [], "packages": {
            "Content/BP_Cart.x": {"classes": [["BP_Cart_C", "Cart"]],
                                  "scs": {"BP_Cart_C": [["MoverCopy", "MoverComponent",
                                                         "scs:SCS_Node_0"]]},
                                  "addComponent": [], "handlers": [], "instanced": []}}})
        rc, out, err = self.run_tool("component_dupes.py")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("DUPLICATE COMPONENT SETS: 1 class(es)", out)
        self.assertIn("=== BP_Cart_C", out)
        self.assertIn("base MoverComponent  x2", out)

    def test_missing_export(self):
        self.assert_missing_export("component_dupes.py", "components")


class StubCensus(Fixture):
    def setUp(self):
        super().setUp()
        self.write("Script/Gameplay/Signals.as",
                   "class USignals : UObject\n{\n"
                   "    UFUNCTION(BlueprintCallable)\n    void Ring()\n    {\n    }\n}\n")

    def test_enrich_reads_the_references_export(self):
        self.export("references", {"assets": ["BP_Bell"],
                                   "memberNames": {"Ring": {"count": 3, "assets": ["BP_Bell"]}},
                                   "functionGraphs": {}, "customEvents": {"Ring": ["BP_Bell"]}})
        out_json = self.root / "stubs.json"
        rc, out, err = self.run_tool("stub_census.py", "--enrich", "--json", out_json)
        self.assertEqual(rc, 0, out + err)
        rows = json.loads(out_json.read_text(encoding="utf-8"))
        ring = [r for r in rows if r["name"] == "Ring"]
        self.assertEqual(len(ring), 1)
        self.assertEqual(ring[0]["bp_members"], 3)
        self.assertEqual(ring[0]["bp_assets"], ["BP_Bell"])
        self.assertEqual(ring[0]["bp_event"], ["BP_Bell"])

    def test_without_enrich_needs_no_export(self):
        rc, out, err = self.run_tool("stub_census.py")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("empty / near-empty bodies:            1", out)

    def test_missing_export(self):
        self.assert_missing_export("stub_census.py", "references", "--enrich")


class Exporter(Fixture):
    """`blueprint_export.py` on hand-written files: two package files that are not real packages,
    and one text export."""

    def setUp(self):
        super().setUp()
        (self.root / "Content" / "Data").mkdir(parents=True)
        (self.root / "Content" / "Data" / "Table.uasset").write_bytes(b"not a package")
        (self.root / "Content" / "Level.umap").write_bytes(b"not a package")
        self.write("T3D/TextBP_Bell-1.txt",
                   'Begin Object Class=/Script/Engine.Blueprint Name="BP_Bell"\n'
                   '   NewVariables(0)=(VarName="Volume",VarType=(PinCategory="real",'
                   'ContainerType=Array),PropertyFlags=32)\n'
                   '   Begin Object Class=/Script/Engine.EdGraph Name="Ring"\n'
                   '   FunctionReference=(MemberName="Ring")\n'
                   '   CustomFunctionName="Chime"\n'
                   'End Object\n')
        self.write("Script/Game/Loader.as",
                   'class ULoader\n{\n    FString A = "/Game/Data/Table.Table";\n}\n')

    def run_export(self, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        env.pop("OCTOPUS_PROJECT", None)
        r = subprocess.run([sys.executable, str(BP_TOOLS / "blueprint_export.py"),
                            "--project", str(self.root)] + list(args),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, cwd=str(self.root), timeout=60)
        return r.returncode, r.stdout, r.stderr

    def read(self, kind):
        p = self.root / ".octopus" / "export" / "blueprint" / (kind + ".json")
        return json.loads(p.read_text(encoding="utf-8"))

    def test_all_kinds(self):
        rc, out, err = self.run_export("all")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.read("packages")["packages"], ["/Game/Data/Table", "/Game/Level"])
        variables = self.read("variables")["blueprints"]["BP_Bell"]["exports"][0]
        self.assertTrue(variables["decoded"])
        self.assertEqual(variables["variables"]["Volume"],
                         {"container": "Array", "category": "real", "flags": 32})
        refs = self.read("references")
        self.assertEqual(refs["memberNames"]["Ring"], {"count": 1, "assets": ["BP_Bell"]})
        self.assertEqual(refs["functionGraphs"]["Ring"], ["BP_Bell"])
        self.assertEqual(refs["customEvents"]["Chime"], ["BP_Bell"])
        self.assertEqual([u["path"] for u in self.read("blueprints")["unparsable"]],
                         ["Content/Data/Table.uasset"])
        self.assertEqual(sorted(d["path"] for d in self.read("components")["dropped"]),
                         ["Content/Data/Table.uasset", "Content/Level.umap"])
        self.assertEqual(self.read("structs")["structs"], {})
        self.assertEqual(self.read("structs")["control"]["status"], "skipped")
        self.assertEqual(self.read("stringtables")["tables"], {})
        for kind in ("packages", "stringtables", "variables", "structs", "blueprints",
                     "components", "references"):
            self.assertEqual(self.read(kind)["kind"], kind)
        # The AngelScript half reads what the Blueprint half wrote.
        rc, out, err = self.run_tool("check_asset_paths.py")
        self.assertEqual(rc, 0, out + err)


class PackFiles(unittest.TestCase):
    def test_every_pack_python_file_parses(self):
        files = sorted(PACKS.rglob("*.py"))
        self.assertTrue(files)
        for p in files:
            with self.subTest(path=str(p.relative_to(OCTOPUS))):
                ast.parse(p.read_text(encoding="utf-8"), filename=str(p))

    def test_angelscript_tools_never_name_a_blueprint_source(self):
        forbidden = re.compile(r"\.uasset|_uasset|t3d", re.I)
        for p in sorted(AS_TOOLS.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            with self.subTest(path=p.name):
                text = p.read_text(encoding="utf-8", errors="replace")
                hit = forbidden.search(text)
                self.assertIsNone(hit, "%s contains %r" % (p.name, hit and hit.group(0)))

    def test_angelscript_tools_import_nothing_from_the_blueprint_pack(self):
        blueprint_modules = {p.stem for p in BP_TOOLS.glob("*.py")}
        for p in sorted(AS_TOOLS.glob("*.py")):
            with self.subTest(path=p.name):
                tree = ast.parse(p.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [a.name.split(".")[0] for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        names = [node.module.split(".")[0]]
                    for n in names:
                        self.assertNotIn(n, blueprint_modules,
                                         "%s imports %s of the blueprint pack" % (p.name, n))
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr in ("insert", "append")
                            and ast.unparse(node.func.value) == "sys.path"):
                        self.assertNotIn("blueprint", ast.unparse(node).lower(),
                                         "%s puts the blueprint pack on sys.path" % p.name)


if __name__ == "__main__":
    unittest.main()
