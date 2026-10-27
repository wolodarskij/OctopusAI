"""A launch offers and accepts only the models and efforts of the AI provider it starts.
python -m unittest discover tests"""

import json
import sys

import threading

from support import FixtureCase, project

import providers  # noqa: E402 - support puts core/engine on the path
import test_project_page
import todo_server  # noqa: E402

class Case(FixtureCase):
    """A project, a ticket and a board server, as the Project view's tests have them."""
    view = test_project_page.ProjectPage.view
    config = test_project_page.ProjectPage.config
    call = test_project_page.ProjectPage.call

    def setUp(self):
        super().setUp()
        self.root = self.make("Api")
        self.tid = self.new_ticket(self.root, "first one")
        self.httpd = todo_server.make_server(0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()


class Lists(Case):
    """Each provider holds its models and efforts; a project adds to them per provider."""

    def test_each_built_in_holds_its_own_models_and_efforts(self):
        p = providers.load()
        self.assertIn("claude-opus-5-5", p["claude"]["models"].values())
        self.assertEqual(p["claude"]["defaultModel"], "opus")
        self.assertIn("max", p["claude"]["efforts"])
        self.assertFalse(set(p["codex"]["models"].values()) & set(p["claude"]["models"].values()))
        self.assertIn("low", p["codex"]["efforts"])

    def test_a_provider_default_outside_its_lists_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            providers.save({"id": "codex", "defaultModel": "opus"})
        self.assertIn("`defaultModel` 'opus' is not a key of `models`", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            providers.save({"id": "local", "command": sys.executable, "models": {"s": "m"},
                            "efforts": ["low"], "defaultEffort": "max"})
        self.assertIn("`defaultEffort` 'max'", str(cm.exception))

    def test_a_new_model_in_global_settings_reaches_every_launch_with_no_code(self):
        providers.save({"id": "codex", "models": {"next": "gpt-next"}, "defaultModel": "next"})
        p = project.load(self.root)
        self.assertEqual(p.providers["codex"]["models"], {"next": "gpt-next"})
        self.assertEqual(p.choose("codex"), ("gpt-next", ""))

    def test_a_project_default_its_agent_cannot_run_is_a_problem_and_falls_back(self):
        cfg = self.config()
        cfg.update(models={"opus": "m"}, defaultModel="nope", efforts=["high"],
                   defaultEffort="max",
                   providers={"codex": {"defaultModel": "opus", "defaultEffort": "invalid"}})
        (self.root / ".octopus" / "project.json").write_text(json.dumps(cfg), encoding="utf-8")
        project._CACHE.clear()
        p = project.load(self.root)
        said = "\n".join(p.problems)
        for text in ("`defaultModel` 'nope' of Claude Code", "`defaultEffort` 'max' of Claude Code",
                     "`defaultModel` 'opus' of OpenAI Codex",
                     "`defaultEffort` 'invalid' of OpenAI Codex"):
            self.assertIn(text, said)
        self.assertEqual(p.default_model_key, "opus")
        self.assertEqual(p.providers["codex"]["defaultModel"], "")


class Launch(Case):
    """A model or an effort the provider cannot run never reaches its command line."""

    def setUp(self):
        super().setUp()
        providers.save({"id": "plain", "command": "plain-cli", "label": "Plain", "models": {"auto": "auto"}})

    def dry(self, *extra):
        return self.todo(self.root, "work", self.tid, "--dry-run", *extra)

    def test_each_agent_runs_only_its_own_models(self):
        rc, out, err = self.dry("--provider", "codex", "--model", "codex", "--effort", "low")
        self.assertEqual(rc, 0, err)
        self.assertIn("--model gpt-6.1-sol", out)
        self.assertIn("model_reasoning_effort=low", out)
        for provider, model in (("codex", "opus"), ("claude", "gpt-6.1-sol"), ("plain", "haiku")):
            rc, out, err = self.dry("--provider", provider, "--model", model)
            self.assertEqual(rc, 2, (provider, model))
            self.assertIn("cannot run the model %r" % model, err)
            self.assertEqual(out, "")

    def test_an_effort_the_agent_lacks_is_refused(self):
        rc, _out, err = self.dry("--provider", "plain", "--effort", "high")
        self.assertEqual(rc, 2)
        self.assertIn("Plain takes no reasoning effort", err)
        rc, _out, err = self.dry("--provider", "codex", "--effort", "invalid")
        self.assertEqual(rc, 2)
        self.assertIn("OpenAI Codex takes no effort 'invalid'", err)

    def test_no_claude_default_reaches_another_provider(self):
        rc, out, err = self.dry("--provider", "codex")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("--model", out)
        self.assertNotIn("model_reasoning_effort", out)
        self.assertNotIn("claude-", out)
        rc, out, err = self.dry()
        self.assertIn("--model claude-opus-5-5 --effort high", out)

    def test_the_board_refuses_an_incompatible_launch_and_offers_each_agents_choices(self):
        st, r = self.call("launch", {"id": self.tid, "mode": "work", "provider": "codex",
                                     "model": "opus", "dryRun": True})
        self.assertEqual(st, 400, r)
        self.assertIn("OpenAI Codex cannot run the model 'opus'", r["error"])
        st, r = self.call("launch", {"id": self.tid, "mode": "work", "provider": "plain",
                                     "effort": "high", "dryRun": True})
        self.assertEqual(st, 400, r)
        st, r = self.call("launch", {"id": self.tid, "mode": "work", "provider": "codex",
                                     "model": "gpt-6.1-sol", "dryRun": True})
        self.assertEqual(st, 200, r)
        self.assertIn("--model gpt-6.1-sol", r["command"])
        c = r["snapshot"]["providerChoices"]
        self.assertIn("opus", c["claude"]["models"])
        self.assertNotIn("opus", c["codex"]["models"])
        self.assertFalse(c["plain"]["effort"])
        self.assertEqual(c["plain"]["efforts"], [])


class ProjectSettings(Case):
    """The Project view edits the models of the project's default provider."""

    def test_the_models_op_writes_the_named_agents_own_lists(self):
        st, r = self.call("project", {"op": "models", "provider": "codex",
                                      "models": {"fast": "gpt-fast"}, "efforts": [],
                                      "defaultModel": "fast", "defaultEffort": "high"})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.config()["providers"]["codex"],
                         {"models": {"fast": "gpt-fast"}, "defaultModel": "fast",
                          "defaultEffort": "high"})
        st, v = self.view()
        a = v["providerModels"]["codex"]
        self.assertEqual(a["own"]["models"], {"fast": "gpt-fast"})
        self.assertIn("codex", a["inherited"]["models"])
        self.assertEqual(a["offered"]["defaultModel"], "fast")
        st, r = self.call("project", {"op": "models", "provider": "codex", "defaultModel": "opus"})
        self.assertEqual(st, 400, r)
        self.assertIn("defaultModel", r["error"])

    def test_changing_the_agent_says_which_default_model_now_applies(self):
        st, r = self.call("project", {"op": "provider", "provider": "codex"})
        self.assertEqual(st, 200, r)
        self.assertIn("OpenAI Codex cannot run claude-opus-5-5", " ".join(r["log"]))
        self.assertEqual(r["snapshot"]["defaultProvider"], "codex")
        self.assertNotIn("opus", r["snapshot"]["models"])
        st, v = self.view()
        self.assertEqual(v["provider"], "codex")


class Cli(FixtureCase):
    """`octopus providers edit` changes a provider's models and efforts."""

    def test_models_and_efforts_are_edited_from_the_command_line(self):
        rc, out, err = self.cli("providers", "edit", "codex", "--model", "next=gpt-next",
                                "--model", "gpt-5-mini=", "--efforts", "low,high",
                                "--default-model", "next", "--default-effort", "high",
                                "--no-check")
        self.assertEqual(rc, 0, err)
        p = providers.load()["codex"]
        self.assertIn("next", p["models"])
        self.assertNotIn("gpt-5-mini", p["models"])
        self.assertEqual((p["efforts"], p["defaultModel"]), (["low", "high"], "next"))
        rc, out, err = self.cli("providers", "edit", "codex", "--default-model", "nope",
                                "--no-check")
        self.assertEqual(rc, 2)
        self.assertIn("`defaultModel` 'nope' is not a key of `models`", err)
