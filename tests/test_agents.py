"""One copy of the rules and skills in `.agents/`, an entry file per AI provider, project
initialization, and the launch of Codex and custom providers.   python -m unittest discover tests"""

import json
import shutil
import unittest

import support
from support import admin, project, quiet, tree_hash

import agents  # noqa: E402 - support puts core/engine on the path


def read(p):
    return p.read_text(encoding="utf-8")


def record(root):
    return json.loads(read(root / ".octopus" / "installed.json"))


class Layout(support.FixtureCase):

    def test_install_keeps_one_copy_and_an_entry_for_each_provider(self):
        root = self.make("Layout", "unreal")
        rules = agents.rule_files(root)
        self.assertIn(".agents/rules/octopus/core/todo-tracking.md", rules)
        self.assertIn(".agents/rules/octopus/unreal/unreal-engine.md", rules)
        self.assertFalse((root / ".claude" / "rules").exists())
        claude, agents_md = read(root / "CLAUDE.md"), read(root / "AGENTS.md")
        self.assertIn("@AGENTS.md", claude)
        for r in rules:
            self.assertIn("@%s" % r, claude)
            self.assertIn("`%s`" % r, agents_md)
        # An entry points; it never carries the text of a rule.
        body = read(root / ".agents" / "rules" / "octopus" / "core" / "todo-tracking.md")
        line = next(ln for ln in body.splitlines() if len(ln) > 60)
        for entry in (claude, agents_md):
            self.assertNotIn(line, entry)
        for s in agents.skill_names(root):
            pointer = read(root / ".claude" / "skills" / s / "SKILL.md")
            source = read(root / ".agents" / "skills" / s / "SKILL.md")
            self.assertIn(agents.frontmatter(source), pointer)
            self.assertIn(".agents/skills/%s/SKILL.md" % s, pointer)
            self.assertNotIn(source.replace(agents.frontmatter(source), "", 1).strip(), pointer)
        self.assertIn("restart-unreal-editor", agents.skill_names(root))

    def test_a_fresh_project_passes_check_and_launches_every_mode(self):
        root = self.make("Fresh")
        rc, out, err = self.todo(root, "check")
        self.assertEqual(rc, 0, out + err)
        tid = self.new_ticket(root, "Bug: something")
        for mode in ("plan", "work", "review", "verify"):
            for provider in ("claude", "codex"):
                rc, out, err = self.todo(root, mode, tid, "--provider", provider, "--dry-run")
                self.assertEqual(rc, 0, (mode, provider, err))

    def test_update_keeps_the_projects_own_text_and_restores_the_block(self):
        root = self.make("Own")
        own = "# Own instructions\n\nKeep this.\n"
        (root / "AGENTS.md").write_text(own + read(root / "AGENTS.md"), encoding="utf-8")
        claude = read(root / "CLAUDE.md").replace("@AGENTS.md", "@AGENTS.md\n@hand-edit.md")
        (root / "CLAUDE.md").write_text("Claude only.\n\n" + claude, encoding="utf-8")
        admin.update(root, out=quiet)
        self.assertTrue(read(root / "AGENTS.md").startswith(own))
        self.assertTrue(read(root / "CLAUDE.md").startswith("Claude only.\n"))
        self.assertNotIn("@hand-edit.md", read(root / "CLAUDE.md"))
        before = tree_hash(root)
        admin.update(root, out=quiet)
        self.assertEqual(tree_hash(root), before)

    def test_a_rule_or_a_skill_added_to_agents_reaches_every_entry_at_session_start(self):
        root = self.make("Grow")
        (root / ".agents" / "rules" / "style.md").write_text("# Style\n", encoding="utf-8")
        sk = root / ".agents" / "skills" / "deploy"
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text("---\nname: deploy\ndescription: Ship it.\n---\nSteps.\n",
                                     encoding="utf-8")
        self.assertTrue(admin.refresh(root, out=quiet))
        self.assertIn("@.agents/rules/style.md", read(root / "CLAUDE.md"))
        self.assertIn("`.agents/rules/style.md`", read(root / "AGENTS.md"))
        self.assertIn("description: Ship it.", read(root / ".claude" / "skills" / "deploy" / "SKILL.md"))
        shutil.rmtree(sk)
        admin.refresh(root, out=quiet)
        self.assertFalse((root / ".claude" / "skills" / "deploy").exists())

    def test_a_skill_of_the_projects_own_in_claudes_place_is_left_alone(self):
        root = self.tmp / "Mine"
        (root / ".claude" / "skills" / "todo").mkdir(parents=True)
        (root / ".claude" / "skills" / "todo" / "SKILL.md").write_text("mine\n", encoding="utf-8")
        admin.install(root, "none", "Mine", register=False, out=quiet, manager=False)
        self.assertEqual(read(root / ".claude" / "skills" / "todo" / "SKILL.md"), "mine\n")
        self.assertTrue((root / ".agents" / "skills" / "todo" / "SKILL.md").is_file())

    def test_uninstall_takes_out_the_blocks_and_keeps_the_projects_text(self):
        root = self.tmp / "Back"
        root.mkdir()
        (root / "CLAUDE.md").write_text("Mine.\n", encoding="utf-8")
        admin.install(root, "unreal", "Back", register=False, out=quiet, manager=False)
        admin.uninstall(root, out=quiet)
        self.assertEqual(read(root / "CLAUDE.md"), "Mine.\n")
        for rel in ("AGENTS.md", ".agents", ".claude", "TODO", ".octopus"):
            self.assertFalse((root / rel).exists(), rel)


class NoNarration(support.FixtureCase):
    """Every AI provider loads the rule that forbids an agent to narrate its own work, and
    Claude Code's reminder asks for one status line once every 5 minutes."""

    RULE = ".agents/rules/octopus/core/reporting-to-the-owner.md"

    def test_every_entry_file_of_a_project_carries_the_rule(self):
        root = self.make("Entries", "unreal")
        self.assertIn("@" + self.RULE, read(root / "CLAUDE.md").splitlines())
        self.assertIn(agents.NO_NARRATION, read(root / "AGENTS.md"))
        self.assertIn("I will use an edit tool to edit", agents.NO_NARRATION)
        env = json.loads(read(root / ".claude" / "settings.json"))["env"]
        self.assertEqual(env["CLAUDE_CODE_SILENT_TURN_REMINDER"], "1")
        self.assertEqual(env["CLAUDE_CODE_SILENT_TURN_REMINDER_SECONDS"], "300")
        self.assertEqual(env["CLAUDE_CODE_SILENT_TURN_REMINDER_TEXT"], agents.STATUS_REMINDER)
        self.assertEqual(agents.reporting_problems(root), [])
        self.assertEqual(admin.status(root, out=quiet)["reporting"], [])

    def test_check_and_status_name_each_entry_that_drops_the_rule(self):
        root = self.make("Dropped")
        claude = read(root / "CLAUDE.md").replace("@%s\n" % self.RULE, "")
        (root / "CLAUDE.md").write_text(claude, encoding="utf-8")
        (root / "AGENTS.md").write_text(read(root / "AGENTS.md").replace(agents.NO_NARRATION, ""),
                                        encoding="utf-8")
        p = root / ".claude" / "settings.json"
        d = json.loads(read(p))
        del d["env"]["CLAUDE_CODE_SILENT_TURN_REMINDER_SECONDS"]
        d["env"]["CLAUDE_CODE_SILENT_TURN_REMINDER"] = "0"
        p.write_text(json.dumps(d), encoding="utf-8")
        problems = agents.reporting_problems(root)
        for word in ("CLAUDE.md", "AGENTS.md", "CLAUDE_CODE_SILENT_TURN_REMINDER ",
                     "CLAUDE_CODE_SILENT_TURN_REMINDER_SECONDS"):
            self.assertTrue(any(word in x for x in problems), (word, problems))
        rc, out, err = self.todo(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("CLAUDE.md does not import %s" % self.RULE, out)
        rc, out, err = self.cli("status", root)
        self.assertEqual(rc, 1)
        self.assertIn("warning  AGENTS.md does not carry the no-narration rule", out)
        admin.update(root, out=quiet)
        self.assertEqual(agents.reporting_problems(root), [])

    def test_every_brief_carries_the_rule_with_the_owners_words(self):
        root = self.make("Briefs")
        tid = self.new_ticket(root, "Bug: something")
        for mode in ("plan", "work", "review", "verify"):
            for provider in ("claude", "codex"):
                rc, out, err = self.todo(root, mode, tid, "--provider", provider,
                                         "--terminal", "none")
                self.assertEqual(rc, 0, (mode, provider, err))
                brief = read(root / "TODO" / ".briefs" / ("%s.md" % tid))
                self.assertIn("## Every message you write", brief, (mode, provider))
                self.assertIn("I will use an edit tool to edit", brief, (mode, provider))
                self.assertIn("One status line is allowed", brief, (mode, provider))
                self.assertIn(self.RULE, brief)

    def test_a_claude_launch_has_the_rule_in_its_system_prompt_and_the_5_minute_reminder(self):
        import todo
        root = self.make("Launch")
        tid = self.new_ticket(root, "Bug: something")
        tok = project.use(root)
        try:
            t = todo.find(todo.load_all(), tid)
            for mode in ("plan", "work", "review", "verify"):
                argv = todo._launch_argv(t, mode, None, False, False, None)
                self.assertEqual(argv[argv.index("--append-system-prompt") + 1],
                                 todo.NO_NARRATION_PROMPT)
                line = todo._shell_line(argv, "Launch - %s - work" % tid)
                self.assertIn("set CLAUDE_CODE_SILENT_TURN_REMINDER=1&& ", line)
                self.assertIn("set CLAUDE_CODE_SILENT_TURN_REMINDER_SECONDS=300&& ", line)
                self.assertIn("set CLAUDE_CODE_SILENT_TURN_REMINDER_TEXT=%s&& " % agents.STATUS_REMINDER,
                              line)
            chat = todo.chat_argv()
            self.assertIn(todo.NO_NARRATION_PROMPT, chat[chat.index("--append-system-prompt") + 1])
        finally:
            project.reset(tok)
        # `cmd` reads every double quote of its line; the argument carries none.
        self.assertNotIn('"', todo.NO_NARRATION_PROMPT)
        self.assertIn("I will use an edit tool to edit", todo.NO_NARRATION_PROMPT)
        # A `cmd` `set` line carries the reminder's text.
        for ch in '"&|<>^%':
            self.assertNotIn(ch, agents.STATUS_REMINDER)


class StatusHook(support.FixtureCase):
    """Codex gets the 5-minute status reminder from a hook after a tool call."""

    def hooks(self):
        import sys
        d = str(support.OCTOPUS / "core" / "hooks")
        if d not in sys.path:
            sys.path.insert(0, d)
        import status_reminder
        return status_reminder

    def test_install_writes_the_hook_files_and_uninstall_takes_them_out(self):
        root = self.make("Hooks")
        codex = json.loads(read(root / ".codex" / "hooks.json"))["hooks"]
        for ev in agents.STATUS_HOOKS["codex"][1]:
            self.assertTrue(agents.is_status_hook(codex[ev][-1]["hooks"][0]["command"], "codex"))
        self.assertEqual(agents.reporting_problems(root), [])
        admin.uninstall(root, out=quiet)
        self.assertFalse((root / ".codex").exists())

    def test_the_projects_own_hooks_stay(self):
        root = self.make("Own")
        own = {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "lint"}]}]}}
        (root / ".codex" / "hooks.json").write_text(json.dumps(own), encoding="utf-8")
        admin.update(root, out=quiet)
        d = json.loads(read(root / ".codex" / "hooks.json"))
        self.assertEqual(d["hooks"]["PostToolUse"][0], own["hooks"]["PostToolUse"][0])
        self.assertEqual(len(d["hooks"]["PostToolUse"]), 2)
        before = read(root / ".codex" / "hooks.json")
        admin.update(root, out=quiet)
        self.assertEqual(read(root / ".codex" / "hooks.json"), before)
        self.assertEqual(json.loads(agents.strip_status_hooks(before, "codex")), own)

    def test_check_names_a_missing_hook(self):
        root = self.make("Missing")
        p = root / ".codex" / "hooks.json"
        d = json.loads(read(p))
        del d["hooks"]["PostToolUse"]
        p.write_text(json.dumps(d), encoding="utf-8")
        problems = agents.reporting_problems(root)
        self.assertTrue(any(".codex/hooks.json does not run the status hook on PostToolUse" in x
                            for x in problems), problems)
        rc, out, err = self.todo(root, "check")
        self.assertEqual(rc, 1)
        admin.update(root, out=quiet)
        self.assertEqual(agents.reporting_problems(root), [])

    def test_the_reminder_comes_after_a_tool_call_once_5_minutes_passed(self):
        sr = self.hooks()
        base = self.tmp / "clock"
        tr = self.tmp / "rollout.jsonl"
        tr.write_text("", encoding="utf-8")

        def ev(provider, name, t, sid="s1"):
            return sr.handle(provider, {"session_id": sid, "hook_event_name": name,
                                        "transcript_path": str(tr)}, now=t, base=base)

        self.assertIsNone(ev("codex", "SessionStart", 0))
        self.assertIsNone(ev("codex", "PostToolUse", 100))
        out = ev("codex", "PostToolUse", 301)
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], agents.STATUS_REMINDER)
        self.assertIsNone(ev("codex", "PostToolUse", 400))
        # Visible text in the transcript starts the clock again; a reasoning item does not.
        with open(tr, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "response_item", "payload": {
                "type": "reasoning", "summary": []}}) + "\n")
            fh.write(json.dumps({"type": "event_msg", "payload": {
                "type": "agent_message", "message": "Writing the parser."}}) + "\n")
        self.assertIsNone(ev("codex", "PostToolUse", 590))
        self.assertIsNone(ev("codex", "PostToolUse", 700))
        self.assertIsNotNone(ev("codex", "PostToolUse", 891))
        self.assertEqual(ev("codex", "Stop", 900), {})
        self.assertIsNone(ev("codex", "PostToolUse", 1100))
        # The clock starts at the time the text's line carries, not at the tool call after it.
        with open(tr, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": "1970-01-01T00:18:30Z", "type": "assistant",
                                 "message": {"role": "assistant", "content": [
                                     {"type": "text", "text": "Testing the parser."}]}}) + "\n")
        self.assertIsNone(ev("codex", "PostToolUse", 1300))
        self.assertIsNotNone(ev("codex", "PostToolUse", 1411))
        # Each session keeps its own clock.
        self.assertIsNone(ev("codex", "PostToolUse", 1200, sid="s2"))

    def test_the_command_answers_on_stdout_and_never_fails(self):
        rc, out, err = self.cli("status-hook", "codex", stdin="not json")
        self.assertEqual((rc, out.strip()), (0, ""))
        rc, out, err = self.cli("status-hook", "codex",
                                stdin=json.dumps({"session_id": "x", "hook_event_name": "Stop"}))
        self.assertEqual((rc, out.strip()), (0, "{}"))


class OldLayout(support.FixtureCase):

    def test_update_removes_obsolete_generated_entries_and_preserves_user_hooks(self):
        root = self.make("Obsolete")
        for keep_user_hook in (False, True):
            with self.subTest(keep_user_hook=keep_user_hook):
                rec = record(root)
                rel = ".old-tool/hooks.json"
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                own = {"command": "lint"}
                generated = {"command": 'python "D:/Tools/octopus.py" status-hook old-tool'}
                p.write_text(json.dumps({"version": 1, "hooks": {
                    "afterTool": ([own] if keep_user_hook else []) + [generated]}}),
                    encoding="utf-8")
                rec["entries"][rel] = {"created": True}
                rule = ".old-tool/rules/generated.md"
                (root / rule).parent.mkdir(parents=True, exist_ok=True)
                (root / rule).write_text(agents.GENERATED_MARK, encoding="utf-8")
                rec["files"][rule] = {"by": agents.OWNER}
                (root / ".octopus" / "installed.json").write_text(json.dumps(rec), encoding="utf-8")
                admin.update(root, out=quiet)
                self.assertNotIn(rel, record(root)["entries"])
                self.assertFalse((root / rule).exists())
                if keep_user_hook:
                    self.assertEqual(json.loads(read(p))["hooks"], {"afterTool": [own]})
                else:
                    self.assertFalse(p.parent.exists())

    def test_update_moves_a_payload_recorded_in_claudes_places(self):
        root = self.make("Old")
        rec = record(root)
        # The install of before: every payload file in `.claude/`, no entry file.
        for rel in [k for k, v in rec["files"].items() if v["by"] == agents.OWNER]:
            (root / rel).unlink()
            del rec["files"][rel]
        for rel in rec.pop("entries"):
            (root / rel).unlink()
        moved = {}
        for rel in list(rec["files"]):
            for new, old in ((".agents/rules/octopus/", ".claude/rules/octopus/"),
                             (".agents/skills/", ".claude/skills/")):
                if rel.startswith(new):
                    dst = root / (old + rel[len(new):])
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(root / rel), str(dst))
                    moved[old + rel[len(new):]] = rec["files"].pop(rel)
        rec["files"].update(moved)
        shutil.rmtree(root / ".agents")
        (root / ".octopus" / "installed.json").write_text(json.dumps(rec), encoding="utf-8")
        admin.update(root, out=quiet)
        self.assertFalse((root / ".claude" / "rules").exists())
        self.assertTrue((root / ".agents" / "rules" / "octopus" / "core" / "todo-tracking.md").is_file())
        self.assertIn(agents.POINTER, read(root / ".claude" / "skills" / "todo" / "SKILL.md"))
        counts = admin.status(root, out=quiet)
        self.assertEqual((counts["edited"], counts["missing"]), (0, 0), counts["rows"])
        self.assertFalse(any(r.startswith(".claude/rules/") for r in record(root)["files"]))


class Adopt(support.FixtureCase):

    def setUp(self):
        super().setUp()
        self.root = r = self.make("Adopt")
        (r / ".claude" / "rules" / "team").mkdir(parents=True)
        (r / ".claude" / "rules" / "style.md").write_text("# Style\n", encoding="utf-8")
        (r / ".claude" / "rules" / "team" / "review.md").write_text(
            "See `.claude/rules/style.md`.\n", encoding="utf-8")
        (r / ".claude" / "rules" / "scoped.md").write_text("---\npaths: src/**\n---\nScoped.\n",
                                                           encoding="utf-8")
        for name, text in (("deploy", "deploy\n"), ("same", "same\n"), ("differs", "here\n")):
            d = r / ".claude" / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("---\nname: %s\ndescription: x\n---\n%s" % (name, text),
                                        encoding="utf-8")
        for name, text in (("same", "same\n"), ("differs", "there\n")):
            d = r / ".agents" / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("---\nname: %s\ndescription: x\n---\n%s" % (name, text),
                                        encoding="utf-8")
        claude = read(r / "CLAUDE.md")
        (r / "CLAUDE.md").write_text("# Project\n\n@.claude/rules/style.md\nRead `.claude/rules/` "
                                     "first.\n\n" + claude, encoding="utf-8")
        (r / "AGENTS.md").write_text(agents.strip_block(read(r / "AGENTS.md")) or "",
                                     encoding="utf-8")

    def test_a_dry_run_changes_nothing(self):
        before = tree_hash(self.root)
        res = admin.adopt(self.root, dry_run=True, out=quiet)
        self.assertTrue(res["moved"])
        self.assertEqual(tree_hash(self.root), before)

    def test_adopt_moves_the_projects_own_rules_and_skills_into_agents(self):
        r = self.root
        admin.adopt(r, out=quiet)
        self.assertEqual(read(r / ".agents" / "rules" / "style.md"), "# Style\n")
        self.assertEqual(read(r / ".agents" / "rules" / "team" / "review.md"),
                         "See `.agents/rules/style.md`.\n")
        # A rule scoped with `paths:` stays where only Claude reads the scope.
        self.assertTrue((r / ".claude" / "rules" / "scoped.md").is_file())
        self.assertFalse((r / ".claude" / "rules" / "style.md").exists())
        self.assertIn("deploy\n", read(r / ".agents" / "skills" / "deploy" / "SKILL.md"))
        for name in ("deploy", "same"):
            self.assertIn(agents.POINTER, read(r / ".claude" / "skills" / name / "SKILL.md"))
        # A copy that differs from its twin is not thrown away.
        self.assertIn("here\n", read(r / ".claude" / "skills" / "differs" / "SKILL.md"))
        self.assertIn("there\n", read(r / ".agents" / "skills" / "differs" / "SKILL.md"))
        # CLAUDE.md's own text moved to the empty AGENTS.md; the generated block imports the rule.
        claude, agents_md = read(r / "CLAUDE.md"), read(r / "AGENTS.md")
        self.assertTrue(agents_md.startswith("# Project\n"))
        # A rule is left in `.claude/rules/`, so a mention of the directory still names it.
        self.assertIn("Read `.claude/rules/` first.", agents_md)
        self.assertNotIn("@.claude/rules/style.md", agents_md)
        self.assertNotIn("# Project", claude)
        self.assertEqual(claude.count("@.agents/rules/style.md"), 1)
        self.assertIn("@.agents/rules/team/review.md", claude)
        rc, out, err = self.todo(r, "check")
        self.assertEqual(rc, 0, out + err)


class Launch(support.FixtureCase):

    def setUp(self):
        super().setUp()
        import providers
        providers.save({"id": "local", "command": "local-cli", "dialect": "template",
                        "plan": ["--read-only"], "auto": ["--yes"],
                        "features": {"planMode": True}})
        self.root = self.make("Launch", "unreal", config={
            "providers": {"codex": {"models": {"fast": "gpt-fast"}, "defaultEffort": "xhigh"}}})
        self.tid = self.new_ticket(self.root, "Bug: something")

    def argv(self, *extra):
        rc, out, err = self.todo(self.root, "work", self.tid, "--dry-run", *extra)
        self.assertEqual(rc, 0, err)
        return out.splitlines()[0]

    def test_codex_runs_in_the_workspace_sandbox_with_its_own_model_and_effort(self):
        line = self.argv("--provider", "codex", "--model", "fast")
        self.assertIn("--no-daemon", line)
        self.assertIn("--sandbox workspace-write", line)
        self.assertNotIn("--ask-for-approval", line)
        self.assertIn("--model gpt-fast", line)
        self.assertIn("model_reasoning_effort=xhigh", line)
        self.assertNotIn("--session-id", line)
        self.assertNotIn("--remote-control", line)
        self.assertTrue(line.endswith('Then get on with it."'), line)

    def test_a_claude_model_is_refused_on_another_provider(self):
        rc, out, err = self.todo(self.root, "work", self.tid, "--dry-run", "--provider", "codex",
                                 "--model", "opus")
        self.assertEqual(rc, 2)
        self.assertIn("OpenAI Codex cannot run the model 'opus'", err)
        self.assertEqual(out, "")

    def test_a_custom_provider_plans_with_its_configured_flags(self):
        rc, out, err = self.todo(self.root, "plan", self.tid, "--dry-run", "--provider", "local")
        self.assertEqual(rc, 0, err)
        self.assertIn("--read-only", out)
        self.assertIn("--yes", self.argv("--provider", "local"))

    def test_the_project_default_provider_is_used_and_a_launch_overrides_it(self):
        p = self.root / ".octopus" / "project.json"
        d = json.loads(read(p))
        d["provider"] = "local"
        p.write_text(json.dumps(d), encoding="utf-8")
        self.assertTrue(self.argv().startswith("local-cli "))
        self.assertIn("--session-id", self.argv("--provider", "claude"))
        d["provider"] = "nobody"
        p.write_text(json.dumps(d), encoding="utf-8")
        project._CACHE.clear()
        self.assertTrue(any("`provider`" in x for x in project.load(self.root).problems))

    def test_the_brief_of_another_provider_says_what_its_launch_cannot_enforce(self):
        self.argv("--provider", "codex")
        rc, out, err = self.todo(self.root, "work", self.tid, "--provider", "codex", "--terminal", "none")
        self.assertEqual(rc, 0, err)
        brief = read(self.root / "TODO" / ".briefs" / ("%s.md" % self.tid))
        self.assertIn("## This session runs on OpenAI Codex", brief)
        self.assertIn("report %s finished --mode work" % self.tid, brief)
        self.assertIn("The launch cannot deny a tool", brief)
        self.todo(self.root, "work", self.tid, "--terminal", "none")
        self.assertNotIn("This session runs on", read(self.root / "TODO" / ".briefs" / ("%s.md" % self.tid)))


class State(unittest.TestCase):
    """A Codex session has no hook: its process and `report` decide its state."""

    def setUp(self):
        import todo
        self.todo = todo

    def state(self, **s):
        return self.todo.wait_state(dict({"provider": "codex", "claude": 4242}, **s), None)[0]

    def test_it_runs_while_its_process_lives_and_nothing_was_reported(self):
        self.assertIsNone(self.state())
        self.assertEqual(self.state(claude=None), "finished")

    def test_a_report_decides(self):
        self.assertEqual(self.state(reported="finished"), "finished")
        self.assertEqual(self.state(reported="input", reported_why="which?"), "input")
        self.assertEqual(self.state(reported="event", reported_why="a build"), "event")


class Initialization(support.FixtureCase):

    def test_new_files_no_manager_outside_the_checkout(self):
        root = self.tmp / "Managed"
        rc, out, err = self.cli("new", root, "--name", "Managed", "--profile", "none")
        self.assertEqual(rc, 0, err)
        self.assertEqual(list((root / "TODO").rglob("T*.md")), [])
        admin.update(root, out=quiet)
        self.assertEqual(list((root / "TODO").rglob("T*.md")), [])
        rc, out, err = self.todo(root, "check")
        self.assertEqual(rc, 0, out + err)

    def test_projects_add_installs_a_bare_directory(self):
        root = self.tmp / "Bare"
        root.mkdir()
        rc, out, err = self.cli("projects", "add", root, "--name", "Bare")
        self.assertEqual(rc, 0, err)
        self.assertTrue((root / ".octopus" / "project.json").is_file())
        self.assertTrue((root / "AGENTS.md").is_file())
        self.assertEqual([r["name"] for r in project.load_registry()], ["Bare"])


if __name__ == "__main__":
    unittest.main()
