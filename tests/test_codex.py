"""Codex executable discovery, model choices and headless calls without paid inference."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from support import FixtureCase, admin, project, quiet, tree_hash

import agents
import providers
import todo
import todo_server


class Codex(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Codex project", config={"provider": "codex", "providers": {
            "codex": {"defaultModel": "gpt-6-luna", "defaultEffort": "high"}}})
        self.token = project.use(self.root)

    def tearDown(self):
        project.reset(self.token)
        super().tearDown()

    def test_model_aliases_and_model_specific_efforts(self):
        p = project.load(self.root)
        self.assertEqual(p.choose("codex"), ("gpt-6-luna", "high"))
        self.assertEqual(p.choose("codex", "codex", "ultra"), ("gpt-6.1-sol", "ultra"))
        self.assertEqual(p.choose("codex", "gpt-6-luna", "max"), ("gpt-6-luna", "max"))
        with self.assertRaisesRegex(ValueError, "gpt-6-luna"):
            p.choose("codex", "gpt-6-luna", "ultra")
        for model in ("opus", "gpt-5-codex"):
            with self.assertRaises(ValueError):
                p.choose("codex", model)
        choices = todo_server.provider_choices()["codex"]
        self.assertTrue(choices["headless"])
        self.assertNotIn("ultra", choices["modelEfforts"]["gpt-6-luna"])
        self.assertIn("ultra", choices["modelEfforts"]["codex"])

    def test_global_invalid_model_effort_default_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not supported"):
            providers.save({"id": "codex", "defaultModel": "gpt-6-luna", "defaultEffort": "ultra"})

    def test_changing_models_drops_an_incompatible_inherited_effort(self):
        p = project.load(self.root)
        p.providers["codex"]["defaultEffort"] = "ultra"
        self.assertEqual(p.choose("codex", "gpt-6-luna"), ("gpt-6-luna", ""))
        with self.assertRaises(ValueError):
            p.choose("codex", "gpt-6-luna", "ultra")

    def test_quoted_executable_is_saved_and_used_for_launch_and_check(self):
        exe = self.tmp / "Tools with spaces" / "codex.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"fixture")
        providers.save({"id": "codex", "command": '"%s"' % exe,
                        "defaultModel": "gpt-6-luna", "defaultEffort": "max"})
        self.assertEqual(providers.get("codex")["command"], str(exe))
        self.assertEqual(providers.resolve_command('"%s"' % exe), str(exe))
        with mock.patch.object(providers.subprocess, "run", return_value=mock.Mock(
                returncode=0, stdout="OK", stderr="")) as run:
            self.assertTrue(providers.check("codex", record=False)["ok"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], str(exe))
        self.assertIn("gpt-6-luna", argv)
        self.assertIn("model_reasoning_effort=max", argv)
        rc, out, err = self.todo(self.root, "chat", "--dry-run", "--provider", "codex")
        self.assertEqual(rc, 0, err)
        self.assertIn('"%s"' % exe, out)
        self.assertIn("--sandbox read-only", out)
        self.assertIn("--no-daemon", out)

    @unittest.skipUnless(os.name == "nt", "Windows launch shell")
    def test_worker_startup_errors_remain_visible(self):
        argv = [sys.executable, "-c", "raise SystemExit(7)"]
        with mock.patch.dict(os.environ, {"TERM": "dumb"}):
            line = todo._shell_line(argv, "Codex startup test", {"OCTOPUS_PROVIDER": "codex"})
        self.assertIn("set TERM=xterm-256color", line)
        self.assertIn("--reset-terminal & echo.", line)
        self.assertIn("& pause)", line)
        result = subprocess.run("cmd /c " + line, input="\n", capture_output=True,
                                text=True, timeout=15)
        self.assertIn("Provider exited with an error.", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows desktop installation")
    def test_desktop_install_is_found_without_path_but_bad_explicit_path_is_not_replaced(self):
        base = self.tmp / "OpenAI" / "Codex" / "bin"
        old, new = base / "old" / "codex.exe", base / "new" / "codex.exe"
        for p in (old, new):
            p.parent.mkdir(parents=True)
            p.write_bytes(b"fixture")
        os.utime(old, (1, 1))
        os.utime(new, (2, 2))
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.tmp)}), \
                mock.patch.object(providers.shutil, "which", return_value=None):
            self.assertEqual(providers.resolve_command("codex"), str(new))
            self.assertIsNone(providers.resolve_command(str(self.tmp / "missing.exe")))

    @unittest.skipUnless(os.name == "nt", "Windows launch environment")
    def test_worker_finds_python_without_inheriting_it_on_path(self):
        inherited = str(self.tmp / "Tools & helpers")
        with mock.patch.dict(os.environ, {"PATH": inherited}):
            marks = providers.launch_env("codex")
            self.assertEqual(providers.launch_env("claude"), {})
        self.assertEqual(marks["PATH"], str(Path(sys.executable).parent) + os.pathsep + inherited)
        marks["OCTOPUS_PROVIDER"] = "codex"
        line = todo._shell_line(["python", "-c", "print('OCTOPUS_PYTHON_OK')"], "Python test", marks)
        result = subprocess.run(os.environ["COMSPEC"] + " /d /c " + line, input="\n",
                                capture_output=True, text=True, timeout=15,
                                env=dict(os.environ, PATH=inherited))
        self.assertIn("OCTOPUS_PYTHON_OK", result.stdout, result.stderr)
        self.assertNotIn("Provider exited with an error.", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows launch environment")
    def test_custom_codex_provider_keeps_its_path_after_python(self):
        providers.save({"id": "local-codex", "dialect": "codex", "command": "codex",
                        "env": {"Path": "X:\\Local tools", "LOCAL_SETTING": "yes"}})
        env = providers.launch_env("local-codex")
        self.assertEqual(env["Path"], str(Path(sys.executable).parent) + os.pathsep + "X:\\Local tools")
        self.assertNotIn("PATH", env)
        self.assertEqual(env["LOCAL_SETTING"], "yes")

    def test_draft_uses_project_provider_and_passes_long_unicode_prompt_on_stdin(self):
        prompt = "Fix café 🐙\n" * 2000
        before = tree_hash(self.root)
        done = mock.Mock(returncode=0, stdout='{"title":"Feature: draft","description":"A draft"}', stderr="")
        with mock.patch.object(providers.subprocess, "run", return_value=done) as run:
            draft = todo_server.act_draft({"text": prompt})
        self.assertEqual(draft["drafts"][0]["title"], "Feature: draft")
        argv, kw = run.call_args.args[0], run.call_args.kwargs
        self.assertIn("exec", argv)
        self.assertIn("gpt-6-luna", argv)
        self.assertIn("model_reasoning_effort=high", argv)
        self.assertEqual(argv[-1], "-")
        self.assertIn(prompt, kw["input"])
        self.assertNotIn(prompt, " ".join(argv))
        self.assertNotEqual(kw["cwd"], str(self.root))
        self.assertIn("--ignore-user-config", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertFalse(Path(kw["cwd"]).exists())
        self.assertEqual(before, tree_hash(self.root))

    def test_provider_override_validates_before_starting(self):
        with mock.patch.object(providers.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                todo_server.act_draft({"text": "draft", "provider": "codex", "model": "opus"})
            with self.assertRaises(ValueError):
                todo_server.act_draft({"text": "draft", "model": "gpt-6-luna", "effort": "ultra"})
            run.assert_not_called()

    def test_grouping_uses_codex_and_does_not_apply_the_proposal(self):
        tid = self.new_ticket(self.root, "Parser bug")
        before = tree_hash(self.root)
        done = mock.Mock(returncode=0, stdout=json.dumps({"name": "parser", "ids": [tid]}), stderr="")
        with mock.patch.object(providers.subprocess, "run", return_value=done) as run:
            result = todo_server.act_groupDraft({"mode": "one", "text": "Parser work"})
        self.assertEqual(result["proposals"][0]["ids"], [tid])
        self.assertIn("exec", run.call_args.args[0])
        self.assertEqual(before, tree_hash(self.root))

    def test_headless_failure_never_treats_partial_json_as_success(self):
        p = providers.get("codex")
        for done in (mock.Mock(returncode=1, stdout='{"title":"partial"}', stderr="authentication failed"),
                     mock.Mock(returncode=0, stdout="", stderr="")):
            with mock.patch.object(providers.subprocess, "run", return_value=done):
                with self.assertRaises(ValueError):
                    providers.answer(p, "test")
        with mock.patch.object(providers.subprocess, "run", side_effect=subprocess.TimeoutExpired("codex", 1)):
            with self.assertRaisesRegex(ValueError, "did not answer"):
                providers.answer(p, "test", timeout=1)

    def test_real_subprocess_and_cli_use_stdin_and_final_file(self):
        script = self.tmp / "fake codex.py"
        script.write_text('''import json, os, pathlib, sys
prompt = sys.stdin.read()
answer = json.dumps({"prompt": prompt, "argv": sys.argv[1:], "nested": "CLAUDECODE" in os.environ})
pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(answer, encoding="utf-8")
print("progress which is not the final answer")
''', encoding="utf-8")
        providers.save({"id": "codex", "command": sys.executable, "args": [str(script)]})
        prompt = "café & $value\n" * 2000
        rc, out, err = self.cli("ask", "--project", self.root, "-", stdin=prompt,
                                env={"CLAUDECODE": "parent"})
        self.assertEqual(rc, 0, err)
        result = json.loads(out)
        self.assertEqual(result["prompt"], prompt)
        self.assertIn("gpt-6-luna", result["argv"])
        self.assertFalse(result["nested"])

    def test_read_only_chat_uses_codex(self):
        result = todo_server.act_chat({"dryRun": True})
        self.assertEqual(result["provider"], "codex")
        self.assertIn("--sandbox read-only", result["command"])
        self.assertIn("--model gpt-6-luna", result["command"])
        self.assertNotIn("--session-id", result["argv"])
        self.assert_auto_review(result["argv"])

    def assert_auto_review(self, argv):
        self.assertEqual(argv.count("--ask-for-approval"), 1)
        self.assertEqual(argv[argv.index("--ask-for-approval") + 1], "on-request")
        self.assertEqual(argv.count("on-request"), 1)
        self.assertEqual(argv.count("approvals_reviewer=auto_review"), 1)
        self.assertEqual(argv[argv.index("approvals_reviewer=auto_review") - 1], "-c")
        for flag in ("auto-review", "--approve-for-me", "--full-auto", "--yolo",
                     "--dangerously-bypass-approvals-and-sandbox"):
            self.assertNotIn(flag, argv)

    def test_codex_defaults_cover_all_ticket_modes_and_chats(self):
        other = self.make("Shared workspace")
        tid = self.new_ticket(self.root, "Approval defaults", "--permits", "projects")
        ticket = todo.find(todo.load_all(), tid)
        variants = (None, {"id": "codex", "label": "Codex override"},
                    {"id": "local-codex", "dialect": "codex", "command": "local-codex",
                     "models": {"fast": "gpt-6-luna"}, "defaultModel": "fast",
                     "efforts": ["high"], "defaultEffort": "high",
                     "features": {"effort": True, "addDir": True}})
        for row in variants:
            with self.subTest(row=row):
                pid = row["id"] if row else "codex"
                if row:
                    providers.save(row)
                    project.reset(self.token)
                    self.token = project.use(self.root)
                self.assertEqual(providers.get(pid)["approvalPolicy"], "auto-review")
                for mode in ("work", "plan", "review", "verify"):
                    with self.subTest(mode=mode):
                        argv = todo._launch_argv(ticket, mode=mode, provider=pid)
                        self.assert_auto_review(argv)
                        self.assertIn("--no-daemon", argv)
                        self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
                        self.assertEqual(argv[argv.index("--model") + 1], "gpt-6-luna")
                        self.assertIn("model_reasoning_effort=high", argv)
                        dirs = [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]
                        self.assertEqual(dirs, [] if mode == "review" else [str(other.resolve())])
                        self.assertIn("TODO/.briefs/%s.md" % tid, argv[-1])
                chat = todo.chat_argv(provider=pid)
                self.assert_auto_review(chat)
                self.assertIn("--no-daemon", chat)
                self.assertEqual(chat[chat.index("--sandbox") + 1], "read-only")
                self.assertEqual(chat[chat.index("--model") + 1], "gpt-6-luna")
                self.assertIn("model_reasoning_effort=high", chat)
                self.assertNotIn("--add-dir", chat)
        self.assertEqual(providers.get("claude")["approvalPolicy"], "")
        self.assertEqual(providers.approval_argv(providers.get("claude")), [])

    def test_interactive_approval_override_round_trips_through_provider_settings(self):
        tid = self.new_ticket(self.root, "Approval policy")
        ticket = todo.find(todo.load_all(), tid)
        claude = todo._launch_argv(ticket, provider="claude")
        self.assertEqual(claude[claude.index("--permission-mode") + 1], "auto")
        # `untrusted` checks legacy argument preservation, not installed CLI support.
        for policy in ("auto-review", "on-request", "untrusted", "never", ""):
            with self.subTest(policy=policy):
                result = todo_server.act_providers({"op": "save", "check": False,
                    "provider": {"id": "codex", "approvalPolicy": policy}})
                saved = next(p for p in result["providers"] if p["id"] == "codex")
                self.assertEqual(saved["approvalPolicy"], policy)
                providers._CACHE.clear()
                self.assertEqual(providers.get("codex")["approvalPolicy"], policy)
                worker = todo._launch_argv(ticket, provider="codex")
                chat = todo_server.act_chat({"dryRun": True})["argv"]
                for argv in (worker, chat):
                    if policy == "auto-review":
                        self.assert_auto_review(argv)
                    elif policy:
                        self.assertEqual(argv.count("--ask-for-approval"), 1)
                        self.assertEqual(argv[argv.index("--ask-for-approval") + 1], policy)
                    else:
                        self.assertNotIn("--ask-for-approval", argv)
                    if policy != "auto-review":
                        self.assertNotIn("approvals_reviewer=auto_review", argv)
                headless = providers.answer_argv(providers.get("codex"), "", "")
                self.assertIn('approval_policy="never"', headless)
                self.assertIn("features.shell_tool=false", headless)
                self.assertEqual(headless[headless.index("--sandbox") + 1], "read-only")
                self.assertNotIn("--ask-for-approval", headless)
                self.assertNotIn("approvals_reviewer=auto_review", headless)
                health = providers.check_argv(providers.get("codex"))
                self.assertNotIn("--ask-for-approval", health)
                self.assertNotIn("approvals_reviewer=auto_review", health)
                self.assertEqual(todo._launch_argv(ticket, provider="claude"), claude)
        result = todo_server.act_providers({"op": "remove", "id": "codex"})
        reset = next(p for p in result["providers"] if p["id"] == "codex")
        self.assertEqual(reset["approvalPolicy"], "auto-review")
        self.assertFalse(reset["overridden"])
        self.assert_auto_review(todo._launch_argv(ticket, provider="codex"))
        self.assert_auto_review(todo.chat_argv(provider="codex"))

    def test_custom_codex_preserves_explicit_empty_policy(self):
        providers.save({"id": "local-codex", "dialect": "codex", "command": "local-codex",
                        "approvalPolicy": ""})
        project.reset(self.token)
        self.token = project.use(self.root)
        self.assertEqual(providers.get("local-codex")["approvalPolicy"], "")
        self.assertNotIn("--ask-for-approval", todo.chat_argv(provider="local-codex"))

    def test_explicit_approval_arguments_override_the_automatic_default(self):
        tid = self.new_ticket(self.root, "Explicit approval arguments")
        ticket = todo.find(todo.load_all(), tid)
        forms = [["-a", "never"], ["-anever"], ["-a=never"],
                 ["--ask-for-approval", "untrusted"], ["--ask-for-approval=never"],
                 ["--approve-for-me"], ["--full-auto"], ["--yolo"],
                 ["--dangerously-bypass-approvals-and-sandbox"]]
        for key, value in (("approval_policy", '"never"'), ("approvals_reviewer", '"user"')):
            assignment = "%s=%s" % (key, value)
            forms += [["-c", assignment], ["--config", assignment],
                      ["--config=" + assignment], ["-c" + assignment],
                      ["-c=" + assignment], ["-c", " %s = %s" % (key, value)]]
        for args in forms:
            for policy in (None, "auto-review", ""):
                with self.subTest(args=args, policy=policy):
                    row = {"id": "codex", "args": args}
                    if policy is not None:
                        row["approvalPolicy"] = policy
                    for _ in range(2):
                        saved = providers.save(row)
                        self.assertEqual(saved["args"], args)
                        for argv in (todo._launch_argv(ticket, provider="codex"),
                                     todo.chat_argv(provider="codex")):
                            self.assertEqual(argv[1:1 + len(args)], args)
                            self.assertEqual(argv.count("--ask-for-approval"),
                                             args.count("--ask-for-approval"))
                            self.assertNotIn("approvals_reviewer=auto_review", argv)
                        row = {k: saved[k] for k in providers.FIELDS if k in saved}

    def test_unrelated_arguments_keep_automatic_review(self):
        for args in (["--oss"], ["-c", "model_provider=local"],
                     ["--config=model_provider=local"], ["-cmodel_provider=local"],
                     ["-c=model_provider=local"],
                     ["--config", "profiles.local.approval_policy=never"],
                     ["-c", "note=approvals_reviewer=user"],
                     ["-c", "--ask-for-approval"],
                     ["--config", "approval_policy_hint=never"]):
            with self.subTest(args=args):
                providers.save({"id": "codex", "args": args})
                argv = todo.chat_argv(provider="codex")
                self.assertEqual(argv[1:1 + len(args)], args)
                self.assert_auto_review(argv[1 + len(args):])

    def test_invalid_approval_settings_are_rejected(self):
        rows = [{"id": "codex", "approvalPolicy": value}
                for value in ("typo", "auto_review", [], {}, None, True, 1)]
        rows += [{"id": "claude", "approvalPolicy": value} for value in ("never", "auto-review")]
        rows.append({"id": "local", "command": "local-cli", "approvalPolicy": "auto-review"})
        for row in rows:
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, "approvalPolicy"):
                providers.save(row)

    def test_hook_update_replaces_old_commands_without_changing_claude(self):
        claude = (self.root / ".claude" / "settings.json").read_bytes()
        path = self.root / ".codex" / "hooks.json"
        legacy = 'python "D:/Old tools/octopus.py" status-hook codex'
        own = {"hooks": [{"type": "command", "command": "echo project hook"}]}
        # The project's own line stays, with the tail that keeps it from stopping a session.
        safe = {"hooks": [{"type": "command", "command": "echo project hook; exit 0"}]}
        path.write_text(json.dumps({"hooks": {ev: [own, {"hooks": [
            {"type": "command", "command": legacy}]}] for ev in agents.STATUS_HOOKS["codex"][1]}}),
            encoding="utf-8")
        for _ in range(2):
            admin.update(self.root, out=quiet)
            self.assertEqual((self.root / ".claude" / "settings.json").read_bytes(), claude)
            hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
            self.assertIn("PreToolUse", hooks)
            for event, groups in hooks.items():
                existing = [safe] if event in agents.STATUS_HOOKS["codex"][1] else []
                self.assertEqual(groups[:-1], existing)
                command = groups[-1]["hooks"][0]["command"]
                self.assertNotEqual(command, legacy)
                self.assertTrue(agents.is_status_hook(command, "codex"))
        remaining = json.loads(agents.strip_status_hooks(path.read_text(encoding="utf-8")))
        self.assertTrue(all(groups == [safe] for groups in remaining["hooks"].values()))

    def test_all_generated_hooks_run_without_python_on_path(self):
        # A path with shell metacharacters must arrive literally, including on Windows.
        checkout = self.tmp / "O'Brien $team tools"
        checkout.mkdir()
        (checkout / "octopus.py").write_text(
            "import runpy\nrunpy.run_path(%r, run_name='__main__')\n" %
            str(admin.OCTOPUS / "octopus.py"), encoding="utf-8")
        with mock.patch.object(admin, "OCTOPUS", checkout):
            line = admin.status_hook_command() + " codex"
        self.assertTrue(agents.is_status_hook(line, "codex"))
        if os.name == "nt":
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if not shell:
                self.skipTest("PowerShell not installed")
            argv = [shell, "-NoProfile", "-NonInteractive", "-Command", line]
        else:
            argv = ["/bin/sh", "-c", line]
        env = dict(os.environ, PATH="", PYTHONIOENCODING="utf-8")
        for event in agents.hook_events("codex"):
            with self.subTest(event=event):
                result = subprocess.run(argv, input=json.dumps({"session_id": "no-path",
                    "hook_event_name": event, "cwd": str(self.root), "prompt": "Hook test"}),
                    capture_output=True, text=True, env=env, cwd=self.root, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "{}" if event == "Stop" else "")
                self.assertEqual(result.stderr.strip(), "")
        state = json.loads((project.home() / "status-reminder" / "codex-no-path.json").read_text())
        self.assertEqual(state["request"], "Hook test")
