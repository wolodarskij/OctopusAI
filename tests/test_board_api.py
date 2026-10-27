"""The board's ticket form, launch and chat calls.   python -m unittest discover tests"""

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from support import FixtureCase, project

import todo_server  # noqa: E402


class BoardApi(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Api", "none")
        self.tid = self.new_ticket(self.root, "first one", "--source", "src/a.py")
        self.httpd = todo_server.make_server(0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, action, body=None, project_name="Api"):
        """POST /api/<action>. Returns (status, json)."""
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/%s?project=%s" % (self.httpd.server_address[1], action,
                                                       project_name),
            data=json.dumps(body or {}).encode(), headers={"X-Todo-Token": todo_server.TOKEN})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def snapshot(self, project_name="Api"):
        """GET /api/tickets, as the board does on a reload. Returns (status, json)."""
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/tickets?project=%s" % (self.httpd.server_address[1],
                                                           project_name),
            headers={"X-Todo-Token": todo_server.TOKEN})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def ticket_file(self):
        return next((self.root / "TODO").rglob("%s-*.md" % self.tid))

    def test_reload_decides_every_session_again_with_its_signals(self):
        seen = []

        def scan(tickets=None, recheck=False):
            seen.append(recheck)
            s = {"id": self.tid, "mode": "work", "key": "%s/work" % self.tid,
                 "running": False, "wait": "idle", "why": "asked nothing", "pty": {"t": 1},
                 "signals": [["judge", "started, no verdict yet"],
                             ["decided", "idle: asked nothing"]]}
            return [s]
        with mock.patch.object(todo_server.T, "live_sessions", side_effect=scan):
            st, r = self.call("reevaluate")
        self.assertEqual(st, 200, r)
        # Every project the server serves is decided again with the recheck; the snapshot
        # that follows is the poll's own scan.
        self.assertTrue(seen[0])
        self.assertFalse(seen[-1])
        mine = next(p for p in r["projects"] if p["name"] == "Api")
        self.assertEqual(mine["error"], "")
        self.assertEqual(mine["sessions"][0]["signals"][0], ["judge", "started, no verdict yet"])
        self.assertNotIn("pty", mine["sessions"][0])
        self.assertEqual(r["snapshot"]["sessions"][0]["wait"], "idle")
        self.assertEqual(r["snapshot"]["tickets"][0]["id"], self.tid)

    def test_reload_opens_no_window_and_a_running_session_says_what_it_does(self):
        # The owner (2026-10-18): Reload only decides every session again; it opens no
        # window and lists no tickets.
        page = (Path(todo_server.__file__).parent / "todo_board.html").read_text(encoding="utf-8")
        for gone in ('id="rdlg"', "#rdlg", "showReevaluated", '"reeval"'):
            self.assertNotIn(gone, page)
        reload = page[page.index("const reload = async"):]
        reload = reload[:reload.index("};") + 2]
        self.assertIn("/api/reevaluate", reload)
        self.assertNotIn("openVeil", reload)
        self.assertIn('subagent: "working"', page)
        self.assertIn("const doingWord", page)

    def test_snapshot_carries_categories_models_and_efforts(self):
        _st, r = self.call("move", {"ids": [self.tid], "state": "Pipeline"})
        snap = r["snapshot"]
        self.assertEqual(snap["categories"], ["project code", "internal tooling"])
        self.assertIn("internal tooling", snap["categoryMeaning"])
        self.assertIn("models", snap)
        self.assertIn("efforts", snap)
        self.assertIn("defaultModel", snap)
        self.assertIn("defaultEffort", snap)
        self.assertEqual(snap["tickets"][0]["category"], "project code")

    def test_edit_updates_fields_and_body_and_keeps_the_file_name(self):
        name = self.ticket_file().name
        st, t = self.call("ticket", {"id": self.tid})
        self.assertEqual(st, 200, t)
        self.assertEqual(t["title"], "first one")
        self.assertEqual(t["category"], "project code")
        st, r = self.call("edit", {"id": self.tid, "base": t["base"], "title": "renamed",
                                   "severity": "High", "category": "internal tooling",
                                   "body": "## What is wrong\n\nA new body.\n\n## Plan\n\nkeep"})
        self.assertEqual(st, 200, r)
        f = self.ticket_file()
        self.assertEqual(f.name, name)
        text = f.read_text(encoding="utf-8")
        self.assertIn("title: renamed", text)
        self.assertIn("severity: High", text)
        self.assertIn("category: internal tooling", text)
        self.assertIn("A new body.", text)
        self.assertIn("## Plan", text)
        self.assertIn("status: pipeline", text)

    def test_a_stale_edit_is_refused(self):
        _st, t = self.call("ticket", {"id": self.tid})
        f = self.ticket_file()
        f.write_text(f.read_text(encoding="utf-8") + "\nmore\n", encoding="utf-8")
        st, r = self.call("edit", {"id": self.tid, "base": t["base"], "title": "x",
                                   "body": "b"})
        self.assertEqual(st, 400)
        self.assertIn("changed since you opened it", r["error"])
        self.assertIn("title: first one", f.read_text(encoding="utf-8"))

    def test_an_edit_with_a_bad_value_is_refused(self):
        _st, t = self.call("ticket", {"id": self.tid})
        st, r = self.call("edit", {"id": self.tid, "base": t["base"], "category": "nope",
                                   "body": "b"})
        self.assertEqual(st, 400)
        self.assertIn("not a category", r["error"])

    def test_parse_drafts(self):
        tok = project.use(self.root)
        self.addCleanup(project.reset, tok)
        one = todo_server.parse_drafts('Here: {"title": "a", "category": "internal tooling"}')
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0]["category"], "internal tooling")
        self.assertEqual(one[0]["severity"], "Medium")
        many = todo_server.parse_drafts(
            'Sure.\n[{"title": "a", "severity": "High"}, {"title": "b", "kind": "tooling"}]\nok')
        self.assertEqual([d["title"] for d in many], ["a", "b"])
        self.assertEqual(many[0]["severity"], "High")
        self.assertEqual(many[1]["category"], "internal tooling")
        with self.assertRaises(ValueError):
            todo_server.parse_drafts("no json here")

    def test_splice_sections_keeps_every_other_section(self):
        body = ("## What is wrong\n\nold\n\n## Evidence\n\nold ev\n\n## Plan\n\n1. x\n\n\n\n"
                "## Runs\n\n- r")
        self.assertEqual(todo_server.splice_sections(body, "new", "new ev"),
                         "## What is wrong\n\nnew\n\n## Evidence\n\nnew ev\n\n## Plan\n\n"
                         "1. x\n\n\n\n## Runs\n\n- r")
        self.assertEqual(todo_server.splice_sections(body, "new", ""),
                         body.replace("\n\nold\n\n", "\n\nnew\n\n"))
        self.assertEqual(todo_server.splice_sections("## What is wrong\n\na\n\n## Plan\n\np",
                                                     "d", "e"),
                         "## What is wrong\n\nd\n\n## Evidence\n\ne\n\n## Plan\n\np")
        self.assertEqual(todo_server.splice_sections("loose text\n\n## Plan\n\np", "d", ""),
                         "## What is wrong\n\nd\n\n## Plan\n\np")

    def test_splice_sections_writes_the_heading_that_the_title_names(self):
        feature = "## What it should do\n\nold\n\n## Plan\n\np"
        self.assertEqual(todo_server.splice_sections(feature, "new", "", "Feature: x"),
                         "## What it should do\n\nnew\n\n## Plan\n\np")
        self.assertEqual(todo_server.splice_sections(feature, "new", "e", "Bug: x"),
                         "## What is wrong\n\nnew\n\n## Evidence\n\ne\n\n## Plan\n\np")
        self.assertEqual(todo_server.splice_sections("## What is wrong\n\nold", "new", "",
                                                     "Feature: x"),
                         "## What it should do\n\nnew")
        self.assertEqual(todo_server.splice_sections("loose\n\n## Plan\n\np", "d", "",
                                                     "Feature: x"),
                         "## What it should do\n\nd\n\n## Plan\n\np")

    def test_draft_of_a_ticket_rewrites_two_sections_and_writes_nothing(self):
        before = self.ticket_file().read_text(encoding="utf-8")
        answer = ('{"title": "better", "severity": "High", "difficulty": "small", '
                  '"description": "new words", "evidence": "src/a.py:3"}')
        body = "## What is wrong\n\nold words\n\n## Plan\n\n1. keep me"
        with mock.patch.object(todo_server, "_ask_provider", return_value=answer) as ask:
            st, r = self.call("draft", {"ticket": {"title": "first one", "severity": "Low",
                                                   "body": body}})
        self.assertEqual(st, 200, r)
        self.assertIn("old words", ask.call_args[0][0])
        [d] = r["drafts"]
        self.assertEqual((d["title"], d["severity"], d["difficulty"]),
                         ("better", "High", "small"))
        self.assertEqual(d["body"], "## What is wrong\n\nnew words\n\n## Evidence\n\n"
                                    "src/a.py:3\n\n## Plan\n\n1. keep me")
        self.assertEqual(self.ticket_file().read_text(encoding="utf-8"), before)
        st, r = self.call("draft", {"ticket": {"title": "x", "body": "  "}})
        self.assertEqual(st, 400)

    def test_draft_of_a_ticket_splits_like_a_note(self):
        """Edit sends the body as `text`, with the split box, as New task does: the first draft
        is the rewritten ticket, every further draft a new ticket."""
        answer = ('[{"title": "kept", "description": "one problem", "evidence": ""},'
                  ' {"title": "split off", "description": "other problem", "evidence": "b.py:1"}]')
        body = "## What is wrong\n\ntwo problems\n\n## Plan\n\n1. keep me"
        with mock.patch.object(todo_server, "_ask_provider", return_value=answer) as ask:
            st, r = self.call("draft", {"text": body, "split": True,
                                        "ticket": {"title": "first one", "severity": "Low"}})
        self.assertEqual(st, 200, r)
        prompt = ask.call_args[0][0]
        self.assertIn("two problems", prompt)
        self.assertIn("a JSON array", prompt)
        self.assertIn("The first object is the rewritten ticket", prompt)
        kept, off = r["drafts"]
        self.assertEqual(kept["body"], "## What is wrong\n\none problem\n\n## Plan\n\n1. keep me")
        self.assertNotIn("body", off)
        self.assertEqual((off["title"], off["evidence"]), ("split off", "b.py:1"))
        with mock.patch.object(todo_server, "_ask_provider", return_value=answer) as ask:
            st, r = self.call("draft", {"text": body, "ticket": {"title": "first one"}})
        self.assertEqual(len(r["drafts"]), 1)
        self.assertIn("ONE JSON object", ask.call_args[0][0])
        self.assertNotIn("The first object", ask.call_args[0][0])

    def test_draft_of_a_ticket_without_a_description_keeps_the_body(self):
        """An answer with no description must not splice an empty What is wrong section."""
        answer = '{"title": "better", "what is wrong": "wrong key", "evidence": ""}'
        body = "## What is wrong\n\nold words\n\n## Plan\n\n1. keep me"
        with mock.patch.object(todo_server, "_ask_provider", return_value=answer):
            st, r = self.call("draft", {"text": body, "ticket": {"title": "first one"}})
        self.assertEqual(st, 400, r)
        self.assertNotIn("drafts", r)

    def test_snapshot_says_when_the_server_code_is_older_than_the_files(self):
        _st, snap = self.snapshot()
        self.assertFalse(snap["serverStale"])
        with mock.patch.object(todo_server, "CODE_STAMP", todo_server.CODE_STAMP - 1):
            _st, snap = self.snapshot()
        self.assertTrue(snap["serverStale"])

    def use(self, root=None):
        tok = project.use(root or self.root)
        self.addCleanup(project.reset, tok)

    def test_the_manage_view_became_groups(self):
        self.use()
        (self.root / "TODO" / ".board.json").write_text('{"view": "manage"}', encoding="utf-8")
        self.assertEqual(todo_server.T.load_board()["view"], "groups")
        with self.assertRaises(ValueError):
            todo_server.T.save_board(view="manage")

    def test_parse_group_drafts(self):
        self.use()
        one = todo_server.parse_group_drafts(
            'Here: {"name": "Parser", "title": "t", "ids": ["t0001", "T9999", "T0001"]}',
            ["T0001", "T0002"], {"Old"})
        self.assertEqual(one, [{"name": "Parser", "title": "t", "description": "",
                                "new": True, "ids": ["T0001"]}])
        many = todo_server.parse_group_drafts(
            '{"groups": [{"name": "Old", "ids": ["T0001"]},'
            ' {"name": "New", "ids": ["T0001", "T0002"]}, {"name": "Empty", "ids": ["T7"]}]}',
            ["T0001", "T0002"], {"Old"})
        self.assertEqual([(g["name"], g["new"], g["ids"]) for g in many],
                         [("Old", False, ["T0001"]), ("New", True, ["T0002"])])
        with self.assertRaises(ValueError):
            todo_server.parse_group_drafts("no json here", ["T0001"], set())
        with self.assertRaises(ValueError):
            todo_server.parse_group_drafts('{"name": "x", "ids": ["T9"]}', ["T0001"], set())

    def test_group_draft_proposes_and_writes_nothing(self):
        other = self.new_ticket(self.root, "second one")
        self.call("group", {"op": "create", "name": "Old"})
        gfile = self.root / "TODO" / ".groups.json"
        before = gfile.read_text(encoding="utf-8")
        seen = {}

        def answer(prompt, model, timeout, what=""):
            seen.update(prompt=prompt, timeout=timeout)
            return '{"groups": [{"name": "Fresh", "title": "F", "ids": ["%s", "%s"]}]}' % (
                self.tid, other)
        with mock.patch.object(todo_server, "_ask_provider", answer):
            st, r = self.call("groupDraft", {"mode": "all"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["proposals"][0]["ids"], [self.tid, other])
        self.assertTrue(r["proposals"][0]["new"])
        self.assertEqual(seen["timeout"], 600)
        self.assertIn("first one", seen["prompt"])
        self.assertIn("- Old: no ticket", seen["prompt"])
        self.assertEqual(gfile.read_text(encoding="utf-8"), before)
        st, r = self.call("groupDraft", {"mode": "one"})
        self.assertEqual(st, 400)

    def test_group_apply_creates_and_places(self):
        self.call("group", {"op": "create", "name": "Old"})
        other = self.new_ticket(self.root, "second one")
        st, r = self.call("groupApply", {"proposals": [
            {"name": "Fresh", "title": "F", "new": True, "ids": [self.tid]},
            {"name": "Old", "new": False, "ids": [other]}]})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["created"], ["Fresh"])
        self.assertEqual(r["placed"], {self.tid: "Fresh", other: "Old"})
        groups = r["snapshot"]["groups"]
        self.assertEqual(groups["Fresh"], [self.tid])
        self.assertEqual(groups["Old"], [other])

    def test_group_apply_in_the_files_store(self):
        root = self.make("Files", "none", config={"groups": {"store": "files"}})
        tid = self.new_ticket(root, "loose one")
        self.use(root)
        props = todo_server.parse_group_drafts(
            '{"name": "Parser Work", "ids": ["%s"]}' % tid, [tid], set())
        self.assertEqual(props[0]["name"], "parser-work")
        st, r = self.call("groupApply", {"proposals": props}, project_name="Files")
        self.assertEqual(st, 200, r)
        self.assertEqual(r["created"], ["parser-work"])
        self.assertTrue((root / "TODO" / "groups" / "parser-work.md").exists())
        self.assertEqual(r["snapshot"]["groups"]["parser-work"], [tid])

    def test_ask_provider_opens_no_console_window(self):
        self.use()
        done = mock.Mock(returncode=0, stdout=" answer \n", stderr="")
        with mock.patch("subprocess.run", return_value=done) as run:
            self.assertEqual(todo_server._ask_provider("p", {}, 5), "answer")
        kw = run.call_args.kwargs
        self.assertEqual(kw["creationflags"], getattr(__import__("subprocess"),
                                                      "CREATE_NO_WINDOW", 0))
        if hasattr(__import__("subprocess"), "CREATE_NO_WINDOW"):
            self.assertEqual(kw["creationflags"], 0x08000000)
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(argv[argv.index("--tools") + 1], "")

    def test_new_writes_the_section_that_the_title_names(self):
        for title, head in (("Bug: the board drops a ticket", "## What is wrong"),
                            ("Feature: Illness should mutate a person", "## What it should do"),
                            ("feature: lower case prefix", "## What it should do")):
            st, r = self.call("new", {"title": title, "description": "words"})
            self.assertEqual(st, 200, r)
            text = next((self.root / "TODO").rglob("%s-*.md" % r["id"])).read_text(
                encoding="utf-8")
            self.assertIn("%s\n\nwords\n" % head, text)
            other = ({"## What is wrong", "## What it should do"} - {head}).pop()
            self.assertNotIn(other, text)

    def test_new_writes_the_category(self):
        st, r = self.call("new", {"title": "made here", "category": "internal tooling"})
        self.assertEqual(st, 200, r)
        f = next((self.root / "TODO").rglob("%s-*.md" % r["id"]))
        self.assertIn("category: internal tooling", f.read_text(encoding="utf-8"))

    def test_launch_focuses_a_live_session_instead_of_starting_one(self):
        live = [{"id": self.tid, "mode": "work", "key": "%s.work" % self.tid,
                 "running": False, "wait": "input", "why": ""}]
        with mock.patch.object(todo_server.T, "live_sessions", lambda tickets=None: live), \
                mock.patch.object(todo_server.T, "cmd_work") as work:
            st, r = self.call("launch", {"id": self.tid, "mode": "work"})
            self.assertEqual(st, 200, r)
            self.assertTrue(r["running"])
            self.assertEqual(r["rc"], 3)
            work.assert_not_called()
            # a live plan session does not stop a work launch
            live[0]["mode"] = "plan"
            work.return_value = 0
            st, r = self.call("launch", {"id": self.tid, "mode": "work"})
            self.assertEqual(st, 200, r)
            self.assertNotIn("running", r)
            work.assert_called_once()

    def test_a_board_move_of_a_manager_to_verification_is_refused(self):
        mid = self.new_ticket(self.root, "the manager", "--repeat", "manager")
        st, r = self.call("move", {"ids": [mid], "state": "Verification"})
        self.assertEqual(st, 400, r)
        self.assertIn("manager ticket", r["error"])
        self.assertTrue(next((self.root / "TODO" / "Pipeline").glob("%s-*.md" % mid), None))
        _st, snap = self.snapshot()
        self.assertIn("watchdog", snap)

    def test_launch_refuses_an_unknown_model(self):
        st, r = self.call("launch", {"id": self.tid, "mode": "work", "model": "gpt"})
        self.assertEqual(st, 400)
        self.assertIn("cannot run the model 'gpt'", r["error"])

    def test_chat_is_read_only(self):
        st, r = self.call("chat", {"dryRun": True})
        self.assertEqual(st, 200, r)
        argv = r["argv"]
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "plan")
        denied = argv[argv.index("--disallowedTools") + 1].split()
        for tool in ("Write", "Edit", "NotebookEdit", "Bash", "PowerShell", "ExitPlanMode"):
            self.assertIn(tool, denied)
        self.assertTrue(r["session"])

    def test_chat_cli_prints_the_read_only_command(self):
        rc, out, err = self.todo(self.root, "chat", "--terminal", "none")
        self.assertEqual(rc, 0, err)
        self.assertIn("--permission-mode plan", out)
        self.assertIn("ExitPlanMode", out)

    def test_lane_layout_is_kept_across_a_reload(self):
        st, r = self.call("board", {"laneOrder": ["Done", "Pipeline", "Done"],
                                    "laneWidth": {"Pipeline": 320, "Done": 20},
                                    "laneShut": ["Blocked"]})
        self.assertEqual(st, 200, r)
        st, snap = self.snapshot()
        self.assertEqual(st, 200, snap)
        bd = snap["board"]
        self.assertEqual(bd["laneOrder"], ["Done", "Pipeline"])
        self.assertEqual(bd["laneWidth"], {"Pipeline": 320, "Done": 160})
        self.assertEqual(bd["laneShut"], ["Blocked"])
        # a write of one setting keeps the others
        self.call("board", {"laneShut": []})
        _st, snap = self.snapshot()
        self.assertEqual(snap["board"]["laneOrder"], ["Done", "Pipeline"])
        self.assertEqual(snap["board"]["laneShut"], [])
        st, r = self.call("board", {"laneWidth": [300]})
        self.assertEqual(st, 400, r)


class BoardPage(unittest.TestCase):
    """The page's header and Project view, read as text."""

    HEADER = ("projsel problems others reload addproj newtask chat viewbar repview filterbtn "
              "count gselbar gselN gselG gselPut gselOut gselClr collapse grpmore newgroup "
              "aigroup aigroups states live closefin assets").split()
    PROJVIEW = ("pName pSlug pDesc pProfile pBoard pPath pRemove pMetaSave gitVcs gitState "
                "pRulesets pPacks gLaneRows pModels pEfforts pDefModel pDefEffort pModelsSave "
                "pTermRs gBoardFile gTerm gFocus pPermits pStore pRequired pAuto pGroupsSave "
                "pCats pCatsSave pProblems pProbSay pFileSay pFiles").split()

    def test_header_bands_and_project_view_keep_their_ids(self):
        # T0089: the header is two bands, every project's controls above this project's,
        # and the Project view keeps every id its script reads.
        page = (Path(todo_server.__file__).parent / "todo_board.html").read_text(encoding="utf-8")
        for i in self.HEADER + self.PROJVIEW:
            self.assertEqual(page.count('id="%s"' % i), 1, i)
        band = page.index('id="hProject"')
        for i in ("addproj", "others", "projsel"):
            self.assertLess(page.index('id="%s"' % i), band, i)
            self.assertGreater(page.index('id="%s"' % i), page.index('id="hGlobal"'), i)
        for i in ("closefin", "newtask", "live", "reload"):
            self.assertGreater(page.index('id="%s"' % i), band, i)
            self.assertLess(page.index('id="%s"' % i), page.index("</header>"), i)
        # T0141: the session states, Reload and Close finished are one group, left of the
        # spacer; Reload is a symbol; the Project Settings view has one button.
        states = page[page.index('id="states"'):]
        states = states[:states.index("</div>")]
        for i in ("live", "reload", "closefin"):
            self.assertIn('id="%s"' % i, states, i)
        self.assertLess(page.index('id="states"'),
                        page.index('<span class="grow">', page.index('id="hProject"')))
        self.assertIn("<svg", states)
        self.assertNotIn('id="settings"', page)
        self.assertIn('data-v="project" title="every setting of this project">Project Settings<', page)
        self.assertIn('data-v="lanes">Boards<', page)
        view = page[page.index('<div id="projview"'):page.index('<div id="menu">')]
        for i in self.PROJVIEW:
            self.assertIn('id="%s"' % i, view, i)


@unittest.skipUnless(sys.platform == "win32", "the terminal cache checks a pid with winproc")
class TerminalCalls(FixtureCase):
    """The terminal window resolves its session once, not on every call."""

    call = BoardApi.call

    def setUp(self):
        super().setUp()
        self.root = self.make("Api", "none")
        self.tid = self.new_ticket(self.root, "first one", "--source", "src/a.py")
        self.httpd = todo_server.make_server(0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        import winproc
        todo_server._TERM_CACHE.clear()
        self.addCleanup(todo_server._TERM_CACHE.clear)
        self.found = 0

        def find(ident, mode=None, sessions=None):
            self.found += 1
            return {"id": ident, "mode": "work", "key": "%s/work" % ident, "own": False,
                    "shell": os.getpid(), "claude": None, "created": winproc.created_at(os.getpid()),
                    "running": False, "wait": "input", "why": "", "title": "t"}

        screen = {"cols": 4, "rows": 1, "cursor": [0, 0], "lines": ["ok"]}
        for name, value in (("find_session", find), ("screen_session", lambda s: screen)):
            patch = mock.patch.object(todo_server.T, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_two_screens_in_a_row_resolve_the_session_once(self):
        for _ in range(2):
            st, r = self.call("screen", {"id": self.tid, "mode": "work"})
            self.assertEqual(st, 200, r)
            self.assertEqual(r["screen"]["lines"], ["ok"])
        self.assertEqual(self.found, 1)

    def test_an_os_error_drops_the_cached_session(self):
        self.call("screen", {"id": self.tid, "mode": "work"})
        with mock.patch.object(todo_server.T, "screen_session",
                               mock.Mock(side_effect=OSError("console gone"))):
            st, r = self.call("screen", {"id": self.tid, "mode": "work"})
        self.assertEqual(st, 400, r)
        self.assertEqual(self.found, 1)
        st, _r = self.call("screen", {"id": self.tid, "mode": "work"})
        self.assertEqual(st, 200)
        self.assertEqual(self.found, 2)

    def test_the_page_never_gets_the_pty_record(self):
        self.assertEqual(todo_server._public([{"key": "a", "pty": {"token": "x"}}]),
                         [{"key": "a"}])


if __name__ == "__main__":
    unittest.main()
