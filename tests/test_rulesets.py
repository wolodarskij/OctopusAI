"""The ruleset loader.   python -m unittest discover tests"""

import json
import unittest

from support import FixtureCase, project


class Rulesets(FixtureCase):
    def tree(self, cfg, **rulesets):
        root = self.tmp / "p"
        (root / ".octopus" / "rulesets").mkdir(parents=True, exist_ok=True)
        base = {"name": "P", "slug": "p", "description": "d", "rulesets": list(rulesets)}
        base.update(cfg)
        (root / ".octopus" / "project.json").write_text(json.dumps(base), encoding="utf-8")
        for name, data in rulesets.items():
            text = data if isinstance(data, str) else json.dumps(data)
            (root / ".octopus" / "rulesets" / ("%s.json" % name)).write_text(text, encoding="utf-8")
        return project.load_rules(root)

    def test_core_rulesets_load_clean(self):
        root = self.make("Plain")
        rules, problems = project.load_rules(root)
        self.assertEqual(problems, [])
        self.assertEqual(rules["rulesets"], ["core", "windows"])
        self.assertEqual(rules["permits"]["outside"]["addDirEnv"], "PLAIN_OUTSIDE_DIR")
        self.assertEqual(rules["models"][rules["defaultModel"]], "claude-opus-5-5")
        self.assertEqual(rules["defaultEffort"], "high")

    def test_lists_concatenate_in_order_without_duplicates_and_scalars_last_wins(self):
        rules, problems = self.tree({}, a={"checks": ["x", "y"], "terminal": "cmd"},
                                    b={"checks": ["y", "z"], "terminal": "wt"})
        self.assertEqual(problems, [])
        self.assertEqual(rules["checks"], ["x", "y", "z"])
        self.assertEqual(rules["terminal"], "wt")
        self.assertEqual(rules["contributions"]["b"]["checks"], ["z"])

    def test_a_permit_defined_twice_fails(self):
        perm = {"permits": {"outside": {"grants": "g"}}}
        _rules, problems = self.tree({}, a=perm, b=perm)
        self.assertEqual(len(problems), 1)
        self.assertIn("already defined by .octopus/rulesets/a.json", problems[0])

    def test_a_missing_ruleset_fails(self):
        _rules, problems = self.tree({"rulesets": ["nowhere"]})
        self.assertEqual(len(problems), 1)
        self.assertIn("nowhere.json is missing", problems[0])

    def test_a_malformed_ruleset_fails(self):
        _, problems = self.tree({}, a="{not json")
        self.assertIn("not valid JSON", problems[0])
        _, problems = self.tree({}, a={"checks": "npm test"})
        self.assertIn("`checks` must be a list of strings", problems[0])
        _, problems = self.tree({}, a={"colour": "red"})
        self.assertIn("unknown key 'colour'", problems[0])
        _, problems = self.tree({}, a={"terminal": "xterm"})
        self.assertIn("`terminal` must be one of", problems[0])

    def test_defaults_are_checked_against_the_providers_lists_not_here(self):
        # Claude's lists are its AI provider's under these; `Project._providers` checks the
        # defaults against the merged lists (tests/test_model_choice.py).
        _, problems = self.tree({"models": {"opus": "m"}, "defaultModel": "fable",
                                 "efforts": ["high"], "defaultEffort": "max"})
        self.assertEqual(problems, [])

    def test_add_dir_variable_takes_the_project_slug(self):
        rules, _ = self.tree({}, a={"permits": {"outside": {"grants": "g",
                                                            "addDirEnv": "{SLUG}_OUT"}}})
        self.assertEqual(rules["permits"]["outside"]["addDirEnv"], "P_OUT")

    def test_a_permit_override_changes_an_earlier_permit(self):
        rules, problems = self.tree({}, a={"permits": {"outside": {"grants": "g"}}},
                                    b={"permitOverrides": {"outside": {"grants": "h",
                                                                       "addDir": "X:/eng"}}})
        self.assertEqual(problems, [])
        self.assertEqual(rules["permits"]["outside"]["grants"], "h")
        self.assertEqual(rules["permits"]["outside"]["addDir"], "X:/eng")
        _, problems = self.tree({}, b={"permitOverrides": {"nothing": {"grants": "h"}}})
        self.assertIn("which no earlier ruleset defines", problems[0])

    def test_add_dir_registry_must_be_a_boolean(self):
        rules, problems = self.tree({}, a={"permits": {"p": {"grants": "g",
                                                             "addDirRegistry": True}}})
        self.assertEqual(problems, [])
        self.assertIs(rules["permits"]["p"]["addDirRegistry"], True)
        _, problems = self.tree({}, a={"permits": {"p": {"grants": "g",
                                                         "addDirRegistry": "yes"}}})
        self.assertIn("`addDirRegistry` (true or false)", problems[0])

    def test_an_override_may_set_add_dir_registry(self):
        rules, problems = self.tree({}, a={"permits": {"p": {"grants": "g"}}},
                                    b={"permitOverrides": {"p": {"addDirRegistry": True}}})
        self.assertEqual(problems, [])
        self.assertIs(rules["permits"]["p"]["addDirRegistry"], True)
        _, problems = self.tree({}, a={"permits": {"p": {"grants": "g"}}},
                                b={"permitOverrides": {"p": {"addDirRegistry": "x"}}})
        self.assertIn("`addDirRegistry`, as true or false", problems[0])
        _, problems = self.tree({}, a={"permits": {"p": {"grants": "g"}}},
                                b={"permitOverrides": {"p": {"addDir": True}}})
        self.assertIn("takes only", problems[0])

    def test_a_deny_rule_applies_while_none_of_its_permits_is_granted(self):
        root = self.tmp / "p"
        self.tree({}, a={"permits": {"x": {"grants": "g"}, "y": {"grants": "g"}},
                         "deny": [{"tools": ["T1", "T2"], "unless": ["x", "y"]},
                                  {"tools": ["T3"], "unless": ["y"]}]})
        p = project.load(root)
        self.assertEqual(p.denied_tools([]), ["T1", "T2", "T3"])
        self.assertEqual(p.denied_tools(["x"]), ["T3"])
        self.assertEqual(p.denied_tools(["y"]), [])

    def test_requires_names_an_earlier_ruleset(self):
        _, problems = self.tree({}, a={"requires": ["b"]}, b={})
        self.assertIn("which the project lists after it", problems[0])
        _, problems = self.tree({}, a={"requires": ["zzz"]})
        self.assertIn("which the project does not list", problems[0])

    def test_tester_permits_must_be_permits(self):
        _, problems = self.tree({}, a={"testerPermits": ["nope"]})
        self.assertIn("testerPermits names 'nope'", problems[0])

    def test_mode_permits_join_per_mode_and_must_be_permits(self):
        perm = {"permits": {"x": {"grants": "g"}, "y": {"grants": "h"}}}
        rules, problems = self.tree({}, a=dict(perm, modePermits={"work": ["x"]}),
                                    b={"modePermits": {"work": ["x", "y"], "verify": ["y"]}})
        self.assertEqual(problems, [])
        self.assertEqual(rules["modePermits"], {"work": ["x", "y"], "verify": ["y"]})
        _, problems = self.tree({}, a={"modePermits": {"work": ["nope"]}})
        self.assertIn("modePermits of work names 'nope'", problems[0])
        _, problems = self.tree({}, a={"modePermits": ["x"]})
        self.assertIn("`modePermits` must map a launch mode", problems[0])

    def test_the_project_ruleset_wins_over_a_core_ruleset_of_the_same_name(self):
        rules, problems = self.tree({}, core={"title": "own core", "permits": {
            "outside": {"grants": "own"}}})
        self.assertEqual(problems, [])
        self.assertEqual(rules["permits"]["outside"]["grants"], "own")


if __name__ == "__main__":
    unittest.main()
