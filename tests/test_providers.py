"""The provider list of global settings: built-in and own providers, the launch on one, the
check, the board's calls.   python -m unittest discover tests"""

import json
import sys
import threading
import urllib.error
import urllib.request
from unittest import mock

from support import FixtureCase, admin, project, quiet

import providers  # noqa: E402
import todo  # noqa: E402
import todo_server  # noqa: E402

# A CLI that answers every prompt with OK, or fails when its first argument says so.
FAKE = r'''
import sys
if "--fail" in sys.argv:
    sys.stderr.write("no endpoint\n")
    sys.exit(3)
print("OK")
'''


class Providers(FixtureCase):
    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake_provider.py"
        self.fake.write_text(FAKE, encoding="utf-8")

    def local(self, **over):
        row = {"id": "local", "label": "Local model", "dialect": "template",
               "command": sys.executable, "args": [str(self.fake)],
               "model": ["--model", "{model}"], "auto": ["--yes"], "plan": ["--read-only"],
               "addDir": ["--dir", "{dir}"],
               "env": {"OPENAI_BASE_URL": "http://localhost:8000/v1"},
               "keyEnv": "LOCAL_PROVIDER_KEY", "models": {"small": "qwen-small"},
               "defaultModel": "small", "features": {"addDir": True, "planMode": True},
               "images": ["python.exe"]}
        row.update(over)
        return row

    def test_the_built_ins_are_listed_with_their_features(self):
        p = providers.load()
        self.assertEqual(list(p), ["claude", "codex"])
        self.assertTrue(p["claude"]["features"]["hooks"])
        self.assertFalse(p["codex"]["features"]["hooks"])
        self.assertEqual(dict(project.PROVIDER_FEATURES)["codex"], p["codex"]["features"])

    def test_an_own_provider_reaches_the_live_agent_views_at_once(self):
        self.assertNotIn("local", project.PROVIDERS)
        providers.save(self.local())
        self.assertIn("local", project.PROVIDERS)
        self.assertEqual(project.PROVIDERS["local"], sys.executable)
        self.assertTrue(project.PROVIDER_FEATURES["local"]["planMode"])
        self.assertIn("python.exe", providers.images())

    def test_an_entry_never_holds_a_key_and_names_its_variable(self):
        with self.assertRaises(ValueError) as c:
            providers.save(self.local(env={"OPENAI_API_KEY": "sk-123"}))
        self.assertIn("secret", str(c.exception))
        with self.assertRaises(ValueError):
            providers.save(self.local(keyEnv="sk-123 abc"))
        self.assertNotIn("local", providers.load())

    def test_a_bad_entry_says_every_problem(self):
        errs = providers.validate({"id": "Bad Id", "dialect": "nope", "colour": 1})
        text = "; ".join(errs)
        for word in ("`id`", "unknown field colour", "`dialect`", "`command`"):
            self.assertIn(word, text)
        self.assertTrue(any("only `claude`" in e for e in
                            providers.validate(self.local(dialect="claude"))))
        self.assertTrue(any("{prompt}" in e for e in
                            providers.validate(self.local(prompt=["--ask"]))))
        self.assertTrue(any("{nope}" in e for e in
                            providers.validate(self.local(model=["--m", "{nope}"]))))

    def test_a_problem_in_the_file_leaves_the_entry_out_and_is_named(self):
        f = providers.store_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"providers": [{"id": "broken", "dialect": "template"}]}),
                     encoding="utf-8")
        self.assertNotIn("broken", providers.load())
        self.assertTrue(any("broken" in p and "`command`" in p for p in providers.problems()))

    def test_an_override_changes_a_built_in_and_removing_it_resets(self):
        providers.save({"id": "codex", "command": "codex-nightly",
                        "env": {"CODEX_HOME": "X:/Tools/codex"}})
        p = providers.load()["codex"]
        self.assertEqual(p["command"], "codex-nightly")
        self.assertEqual(p["dialect"], "codex")
        self.assertTrue(p["overridden"])
        self.assertEqual(providers.remove("codex"), "reset")
        self.assertEqual(providers.load()["codex"]["command"], "codex")
        with self.assertRaises(ValueError):
            providers.remove("codex")
        with self.assertRaises(ValueError):
            providers.save({"id": "codex", "dialect": "template"})

    def test_check_reports_a_start_and_an_answer_and_records_it(self):
        providers.save(self.local())
        r = providers.check("local")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["started"] and r["answered"])
        self.assertEqual(r["said"], "OK")
        self.assertEqual(r["key"], "LOCAL_PROVIDER_KEY")
        self.assertTrue(providers.last_check("local")["ok"])

    def test_check_says_when_it_does_not_answer_or_does_not_start(self):
        providers.save(self.local(args=[str(self.fake), "--fail"]))
        r = providers.check("local")
        self.assertTrue(r["started"])
        self.assertFalse(r["answered"])
        self.assertEqual(r["exit"], 3)
        self.assertIn("no endpoint", r["said"])
        providers.save(self.local(command="no-such-provider-cli"))
        r = providers.check("local")
        self.assertFalse(r["started"])
        self.assertIn("not on PATH", r["said"])

    def test_the_template_argv_places_every_field(self):
        p = providers._complete("local", self.local())
        argv = providers.template_argv(p, "do it", p["auto"], model="m1", effort="high",
                                       dirs=["X:/Tools/a"], session="s1")
        self.assertEqual(argv, [sys.executable, str(self.fake), "--yes", "--dir", "X:/Tools/a",
                                "--model", "m1", "do it"])

    def test_a_project_picks_an_own_provider_as_its_default(self):
        providers.save(self.local())
        root = self.make("Sample Game", config={"provider": "local"})
        p = project.load(root)
        self.assertEqual(p.problems, [])
        self.assertEqual(p.provider, "local")
        self.assertEqual(p.providers["local"]["defaultModel"], "small")
        other = self.make("Other", config={"provider": "nobody"})
        self.assertTrue(any("`provider`" in x for x in project.load(other).problems))

    def test_a_launch_on_an_own_provider_builds_its_command_and_sets_its_env(self):
        providers.save(self.local())
        root = self.make("Sample Game")
        tid = self.new_ticket(root, "Feature: something")
        rc, out, err = self.todo(root, "work", tid, "--provider", "local", "--dry-run")
        self.assertEqual(rc, 0, err)
        line = out.splitlines()[0]
        self.assertIn(str(self.fake), line)
        self.assertIn("--yes", line)
        self.assertIn("--model qwen-small", line)
        self.assertIn("set     OPENAI_BASE_URL=http://localhost:8000/v1", out)
        self.assertIn("set     OCTOPUS_PROVIDER=local", out)
        self.assertIn("LOCAL_PROVIDER_KEY is unset", err)
        rc, out, err = self.todo(root, "plan", tid, "--provider", "local", "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertIn("--read-only", out.splitlines()[0])
        brief = next((root / "TODO" / ".briefs").glob("%s.md" % tid)).read_text(encoding="utf-8")
        self.assertIn("Local model", brief)

    def test_the_old_list_file_is_read_and_the_next_save_moves_it(self):
        old = providers.old_store_file()
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(json.dumps({"agents": [self.local()]}), encoding="utf-8")
        self.assertIn("local", providers.load())
        providers.save(self.local(label="Local again"))
        self.assertFalse(old.exists())
        data = json.loads(providers.store_file().read_text(encoding="utf-8"))
        self.assertEqual([r["id"] for r in data["providers"]], ["local"])
        self.assertNotIn("agents", data)
        self.assertEqual(providers.load()["local"]["label"], "Local again")

    def test_the_old_project_keys_are_read_and_update_rewrites_them(self):
        providers.save(self.local())
        root = self.make("Sample Game",
                         config={"agent": "local", "agents": {"codex": {"defaultEffort": "low"}}})
        p = project.load(root)
        self.assertEqual(p.problems, [])
        self.assertEqual(p.provider, "local")
        self.assertEqual(p.providers["codex"]["defaultEffort"], "low")
        admin.update(root, out=lambda *_a: None)
        cfg = json.loads((root / ".octopus" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["provider"], "local")
        self.assertEqual(cfg["providers"], {"codex": {"defaultEffort": "low"}})
        self.assertNotIn("agent", cfg)
        self.assertNotIn("agents", cfg)
        self.assertEqual(project.load(root).provider, "local")

    def test_a_ticket_launched_under_the_old_line_keeps_its_provider(self):
        providers.save(self.local())
        root = self.make("Sample Game")
        tid = self.new_ticket(root, "Feature: something")
        path = next((root / "TODO").rglob("%s-*.md" % tid))
        path.write_text(path.read_text(encoding="utf-8").replace(
            "\ncreated:", "\nagent: local\ncreated:", 1), encoding="utf-8")
        rc, out, err = self.todo(root, "remote-control", tid, "on")
        self.assertNotEqual(rc, 0)
        self.assertIn("launches local", err)
        rc, out, err = self.todo(root, "work", tid, "--provider", "local", "--dry-run")
        self.assertEqual(rc, 0, err)

    def test_provider_names_the_ai_provider_and_agent_stays_its_alias(self):
        providers.save(self.local())
        root = self.make("Sample Game")
        tid = self.new_ticket(root, "Feature: something")
        lines = []
        for flag in ("--provider", "--agent"):
            rc, out, err = self.todo(root, "work", tid, flag, "local", "--dry-run")
            self.assertEqual(rc, 0, err)
            lines.append(out.splitlines()[0])
        self.assertEqual(lines[0], lines[1])
        self.assertIn(str(self.fake), lines[0])
        rc, out, err = self.todo(root, "work", "--help")
        self.assertIn("--provider", out)
        rc, out, err = self.todo(root, "work", tid, "--provider", "nobody", "--dry-run")
        self.assertNotEqual(rc, 0)

    def test_octopus_providers_lists_them_and_agents_stays_its_alias(self):
        providers.save(self.local())
        outs = []
        for cmd in ("providers", "agents"):
            rc, out, err = self.cli(cmd, "list")
            self.assertEqual(rc, 0, err)
            outs.append(out)
        self.assertEqual(outs[0], outs[1])
        self.assertIn("Local model", outs[0])
        rc, out, err = self.cli("providers", "show")
        self.assertIn("name the AI provider", err)
        rc, out, err = self.cli("--help")
        self.assertIn("octopus.py providers", out)


class ProvidersApi(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Api")
        self.httpd = todo_server.make_server(0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, action, body=None, method="POST"):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/%s?project=Api" % (self.httpd.server_address[1], action),
            data=json.dumps(body or {}).encode() if method == "POST" else None,
            headers={"X-Todo-Token": todo_server.TOKEN})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_adding_a_provider_checks_it_and_the_project_can_choose_it(self):
        seen = []

        def fake_check(pid, **_k):
            seen.append(pid)
            return {"id": pid, "ok": True, "started": True, "answered": True}
        row = {"id": "local", "label": "Local", "dialect": "template", "command": "local-cli"}
        with mock.patch.object(todo_server.providers, "check", side_effect=fake_check):
            st, r = self.call("providers", {"op": "save", "provider": row})
        self.assertEqual(st, 200, r)
        self.assertEqual(seen, ["local"])
        self.assertTrue(r["check"]["ok"])
        self.assertIn("local", [a["id"] for a in r["providers"]])
        st, r = self.call("providers", method="GET")
        self.assertEqual(st, 200, r)
        self.assertIn("local", [a["id"] for a in r["providers"]])
        st, r = self.call("project", {"op": "provider", "provider": "local"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["snapshot"]["defaultProvider"], "local")
        self.assertEqual(r["snapshot"]["providerLabels"]["local"], "Local")
        st, r = self.call("project", {"op": "provider", "provider": "nobody"})
        self.assertEqual(st, 400, r)

    def test_a_secret_in_env_is_refused_and_remove_takes_an_own_provider_out(self):
        st, r = self.call("providers", {"op": "save", "check": False, "provider": {
            "id": "local", "command": "x", "env": {"API_TOKEN": "abc"}}})
        self.assertEqual(st, 400, r)
        st, r = self.call("providers", {"op": "save", "check": False, "provider": {
            "id": "local", "command": "x"}})
        self.assertEqual(st, 200, r)
        st, r = self.call("providers", {"op": "remove", "id": "local"})
        self.assertEqual((st, r["result"]), (200, "removed"), r)
        self.assertNotIn("local", [a["id"] for a in r["providers"]])


class Migration(FixtureCase):
    """What an install from before AI-providers had their own name wrote: `agents.json`, the
    project.json keys `agent` and `agents`, a ticket's `agent:`, a project's own brief section
    `agent.md` and a shell's `OCTOPUS_AGENT`. Each still works, and is moved to the new name."""

    def test_agents_json_becomes_providers_json(self):
        old = project.home() / "agents.json"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(json.dumps({"agents": [{"id": "local", "label": "Local",
                                               "dialect": "template", "command": "x"}],
                                   "checks": {"local": {"ok": True}}}), encoding="utf-8")
        self.assertIn("local", providers.load())
        self.assertFalse(old.exists())
        data = json.loads(providers.store_file().read_text(encoding="utf-8"))
        self.assertEqual(providers.store_file().name, "providers.json")
        self.assertEqual([r["id"] for r in data["providers"]], ["local"])
        self.assertNotIn("agents", data)
        self.assertEqual(data["checks"], {"local": {"ok": True}})

    def test_an_older_project_json_is_read_and_update_renames_its_keys(self):
        root = self.make("Old", config={
            "agent": "codex", "agents": {"codex": {"defaultEffort": "low"}}})
        p = project.load(root)
        self.assertEqual(p.problems, [])
        self.assertEqual(p.provider, "codex")
        self.assertEqual(p.providers["codex"]["defaultEffort"], "low")
        admin.update(root, out=quiet)
        pj = admin.settings(root)
        self.assertEqual((pj.get("provider"), pj.get("providers")),
                         ("codex", {"codex": {"defaultEffort": "low"}}))
        self.assertNotIn("agent", pj)
        self.assertNotIn("agents", pj)
        self.assertEqual(admin.status(root, out=quiet)["edited"], 0)

    def test_a_settings_change_renames_the_old_keys_too(self):
        root = self.make("Old", config={"agent": "codex"})
        admin.set_settings(root, title="Old Game")
        pj = admin.settings(root)
        self.assertEqual(pj.get("provider"), "codex")
        self.assertNotIn("agent", pj)

    def test_a_tickets_agent_field_is_read_and_rewritten_as_provider(self):
        root = self.make("Old")
        tid = self.new_ticket(root, "Feature: something")
        tok = project.use(root)
        try:
            t = todo.find(todo.load_all(), tid)
            t.meta["agent"] = "codex"
            self.assertEqual(todo.ticket_provider(t), "codex")
            self.assertFalse(todo.remote_control_available(t))
            todo.record_launch(t, "work", "local")
            t = todo.find(todo.load_all(), tid)
            self.assertEqual((t.meta.get("provider"), t.meta.get("agent")), ("local", None))
        finally:
            project.reset(tok)

    def test_a_projects_own_agent_section_still_replaces_the_provider_section(self):
        root = self.make("Old")
        own = root / ".octopus" / "brief"
        own.mkdir(parents=True, exist_ok=True)
        (own / "agent.md").write_text("## Our own words for {agent_label}\n", encoding="utf-8")
        tid = self.new_ticket(root, "Feature: something")
        rc, out, err = self.todo(root, "work", tid, "--provider", "codex", "--dry-run")
        self.assertEqual(rc, 0, err)
        brief = (root / "TODO" / ".briefs" / ("%s.md" % tid)).read_text(encoding="utf-8")
        self.assertIn("## Our own words for OpenAI Codex", brief)
        self.assertNotIn("No hook reads what this session does", brief)

    def test_a_shell_started_before_the_rename_is_still_found(self):
        import session_state
        import winproc
        root = self.make("Old")
        tid = self.new_ticket(root, "Feature: something")
        tok = project.use(root)
        try:
            t = todo.find(todo.load_all(), tid)
            sid = todo.launch_session_id(tid, "work", 1)
            line = ("cmd /c set OCTOPUS_AGENT=codex&& set OCTOPUS_SESSION=%s&& codex exec "
                    "\"Work ticket %s.\"" % (sid, tid))
            procs = {100: (50, "cmd.exe"), 101: (100, "codex.exe"), 50: (1, "explorer.exe")}
            with mock.patch.object(winproc, "processes", return_value=procs), \
                    mock.patch.object(winproc, "command_lines", return_value={100: line}), \
                    mock.patch.object(winproc, "created_at", return_value=0), \
                    mock.patch.object(session_state, "live_records", return_value={}), \
                    mock.patch.object(todo, "pty_records", return_value={}):
                found = todo.live_sessions([t])
        finally:
            project.reset(tok)
        self.assertEqual([(s["key"], s["provider"], s["claude"]) for s in found],
                         [("%s/work" % tid, "codex", 101)])
