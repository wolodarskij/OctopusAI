"""macOS and Linux: the process calls, the `/bin/sh` launch line, tmux.   python -m unittest tests.test_posix

On Windows only the parts that run anywhere run: the dispatch of `winproc`, the quoting, the
key names. The rest needs a POSIX machine, and the last class needs tmux on it.
"""

import os
import re
import shutil
import subprocess
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401  (puts core/engine, core/win and core/posix on the path)
from support import FixtureCase, project

import posixterm  # noqa: E402
import todo  # noqa: E402

POSIX = os.name != "nt"
SID = "0f0e0d0c-0b0a-4908-8706-050403020100"
NAME = "Wait - T0001 - fix the lamp $HOME - work"


class Dispatch(unittest.TestCase):
    def test_winproc_is_the_module_of_this_machine(self):
        import winproc
        self.assertEqual(winproc.__name__, "winproc_nt" if os.name == "nt" else "posixproc")
        for name in ("processes", "children", "created_at", "command_lines", "_peb_command_line",
                     "current_directory", "taskkill", "terminate", "wait_exit", "windows",
                     "find_terminal_window", "raise_window", "WT_WINDOW_CLASS"):
            self.assertTrue(hasattr(winproc, name), name)


class Quoting(unittest.TestCase):
    def test_a_plain_word_stays_bare(self):
        self.assertEqual(posixterm.quote("--session-id"), "--session-id")
        self.assertEqual(posixterm.quote(SID), SID)

    def test_a_word_with_specials_is_double_quoted(self):
        self.assertEqual(posixterm.quote('a "b" $c `d` \\e'), '"a \\"b\\" \\$c \\`d\\` \\\\e"')

    def test_the_line_round_trips_through_sh(self):
        if not POSIX:
            self.skipTest("needs /bin/sh")
        argv = ["printf", "%s|", "plain", NAME, 'q"uote', "back`tick", "semi;colon", "new\nline"]
        line = posixterm.join(argv)
        out = subprocess.run(["/bin/sh", "-c", line], capture_output=True, text=True).stdout
        self.assertEqual(out, "".join(a + "|" for a in argv[2:]))

    def test_the_shell_line(self):
        line = posixterm.shell_line(
            ["claude", "--session-id", SID, "--remote-control", NAME, "Work on it; then stop"],
            NAME, "/work/My Game", {"A": "1"}, {"OCTOPUS_PROVIDER": "codex"},
            ["/usr/bin/python3", "/x/session_state.py", "--reset-terminal"])
        self.assertTrue(line.startswith('cd "/work/My Game" && export A=1 && '))
        self.assertTrue(line.endswith("; /usr/bin/python3 /x/session_state.py --reset-terminal"))
        self.assertIn("export OCTOPUS_PROVIDER=codex && claude", line)
        # The session scan reads the session id and the name off the shell's command line.
        self.assertTrue(re.search(todo._LINE_SESSION, line))
        self.assertEqual(re.search(todo._NAME_ARG, line).group(1),
                         NAME.replace("$", "\\$"))
        self.assertTrue(posixterm.LAUNCH_LINE.match("/bin/sh -c " + line))
        self.assertTrue(posixterm.LAUNCH_LINE.match(
            "/bin/sh -c " + posixterm.export({"OCTOPUS_VERIFY": "w:T0001"}) + line))
        self.assertFalse(posixterm.LAUNCH_LINE.match("/bin/zsh -c echo --session-id " + SID))


class Keys(unittest.TestCase):
    def test_the_board_names_as_tmux_names(self):
        for name, key in (("Enter", "Enter"), ("Esc", "Escape"), ("Backspace", "BSpace"),
                          ("Shift+Tab", "BTab"), ("Ctrl+C", "C-c"), ("Alt+m", "M-m"),
                          ("Ctrl+Left", "C-Left"), ("Shift+Up", "S-Up"),
                          ("PageUp", "PPage"), ("Delete", "DC"), ("x", "x")):
            self.assertEqual(posixterm.tmux_key(name), key, name)

    def test_an_unknown_name_is_refused(self):
        for name in ("Hyper+X", "F99", "Ctrl+"):
            with self.assertRaises(ValueError):
                posixterm.tmux_key(name)


@unittest.skipUnless(POSIX, "the POSIX process calls")
class PosixProc(unittest.TestCase):
    def setUp(self):
        import winproc
        self.wp = winproc

    def test_this_process(self):
        procs = self.wp.processes()
        self.assertEqual(procs[os.getpid()][0], os.getppid())
        self.assertIn(os.getpid(), self.wp.children(procs, os.getppid()))
        self.assertLess(todo._age(self.wp.created_at(os.getpid())), 24 * 3600)
        self.assertGreaterEqual(todo._age(self.wp.created_at(os.getpid())), 0)
        line = self.wp._peb_command_line(os.getpid())
        self.assertTrue(line)
        self.assertEqual(self.wp.command_lines([os.getpid()]), {os.getpid(): line})
        self.assertEqual(os.path.realpath(self.wp.current_directory(os.getpid())),
                         os.path.realpath(os.getcwd()))

    def test_a_child_is_found_ended_and_waited_for(self):
        child = subprocess.Popen(["/bin/sh", "-c", "sleep 30; echo " + SID])
        try:
            procs = self.wp.processes()
            self.assertEqual(procs[child.pid], (os.getpid(), "sh"))
            self.assertIn(SID, self.wp.command_lines([child.pid])[child.pid])
            self.assertFalse(self.wp.wait_exit(child.pid, 0.2))
            self.assertEqual(self.wp.taskkill(child.pid, force=True, tree=True), 0)
            self.assertTrue(self.wp.wait_exit(child.pid, 5))
        finally:
            child.kill()
            child.wait()
        self.assertIsNone(self.wp.created_at(child.pid))
        self.assertEqual(self.wp.taskkill(child.pid), 128)

    def test_no_window_here(self):
        self.assertEqual(self.wp.windows(), [])
        self.assertIsNone(self.wp.find_terminal_window("x"))
        self.assertFalse(self.wp.raise_window(1))


@unittest.skipUnless(POSIX, "the /bin/sh launch")
class Launch(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Wait")
        self.tok = project.use(self.root)

    def tearDown(self):
        project.reset(self.tok)
        super().tearDown()

    def test_the_line_moves_to_the_project_and_resets_after(self):
        line = todo._shell_line(["claude", "--session-id", SID], "T - T0001 - work")
        self.assertTrue(line.startswith("cd "))
        self.assertIn("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1", line)
        self.assertTrue(line.endswith("--reset-terminal"))

    def test_wt_and_cmd_open_a_window_and_own_none(self):
        line = todo._shell_line(["claude", "--session-id", SID], "T - T0001 - work")
        for terminal, window in (("wt", True), ("cmd", True), ("own", False)):
            with mock.patch.object(posixterm, "launch", return_value=None) as launch:
                self.assertEqual(todo._open_terminal(line, "t", terminal), 0)
            args, kw = launch.call_args
            self.assertEqual(args[0], line)
            self.assertEqual(args[3], "octopus-" + SID)
            self.assertEqual(kw["window"], window)

    def test_a_refused_launch_is_rc_2(self):
        with mock.patch.object(posixterm, "launch", return_value="no tmux"):
            self.assertEqual(todo._open_terminal("x", "t", "own"), 2)

    def test_without_tmux_a_window_runs_the_line_and_own_is_refused(self):
        with mock.patch.object(posixterm, "tmux_path", return_value=None), \
                mock.patch.object(posixterm, "open_window", return_value=None) as opened:
            self.assertIsNone(posixterm.launch("cd /x && claude", "t", "/x", "octopus-1",
                                               self.tmp))
            self.assertIn("/bin/sh -c 'cd /x && claude'", opened.call_args[0][0])
            self.assertIn("tmux", posixterm.launch("x", "t", "/x", "octopus-1", self.tmp,
                                                   window=False))

    def test_the_window_script_attaches_and_closes_after(self):
        with mock.patch.object(posixterm, "tmux_path", return_value="/opt/x/tmux"), \
                mock.patch.object(posixterm, "DARWIN", True), \
                mock.patch("subprocess.Popen") as popen:
            self.assertIsNone(posixterm.attach_window("octopus-1", "T - T0001", self.tmp))
        script = self.tmp / "octopus-1.command"
        self.assertEqual(popen.call_args[0][0], ["open", "-a", "Terminal", str(script)])
        text = script.read_text(encoding="utf-8")
        self.assertIn("attach-session -t =octopus-1", text)
        self.assertIn("has-session -t '=octopus-1'", text)
        self.assertTrue(os.access(script, os.X_OK))

    def test_the_scan_finds_a_launched_sh_and_its_claude(self):
        import session_state
        import winproc
        sid = todo.launch_session_id("T0001", "work", 1)
        t = SimpleNamespace(id="T0001", run=1, repeat="", sessions=[], blocked_by=[],
                            parent=None, state=todo.lane("active"),
                            status=todo.S("implementing"))
        line = todo._shell_line(["claude", "--session-id", sid, "--remote-control",
                                 "Wait - T0001 - x - work"], "Wait - T0001 - x - work")
        procs = {100: (50, "sh"), 50: (1, "tmux"), 200: (100, "claude"),
                 300: (1, "zsh"), 301: (300, "claude")}
        lines = {100: "/bin/sh -c " + line,
                 300: "/bin/zsh -c grep -- '--session-id %s' notes" % sid}
        with mock.patch.object(winproc, "processes", return_value=procs), \
                mock.patch.object(winproc, "command_lines",
                                  side_effect=lambda pids: {p: lines[p] for p in pids
                                                            if p in lines}), \
                mock.patch.object(winproc, "created_at", return_value=0), \
                mock.patch.object(session_state, "live_records", return_value={}), \
                mock.patch.object(posixterm, "panes",
                                  return_value={100: {"pane": "%1", "attached": False,
                                                      "session": "octopus-" + sid}}):
            found = todo.live_sessions([t])
        self.assertEqual([(s["key"], s["shell"], s["claude"]) for s in found],
                         [("T0001/work", 100, 200)])
        self.assertTrue(found[0]["own"], "a tmux session with no window is an own terminal")

    def test_focus_screen_send_and_log_go_to_tmux(self):
        s = {"key": "T0001/work", "id": "T0001", "mode": "work", "shell": 100, "claude": 200,
             "own": False, "title": "Wait - T0001 - work", "terminal": None}
        with mock.patch.object(posixterm, "focus", return_value=True) as focus:
            self.assertTrue(todo.focus_session(s))
        self.assertEqual(focus.call_args[0][0], 100)
        calls = []

        def tmux(*args, check=True):
            calls.append(args)
            if args[0] == "display-message":
                return "80 3 4 1\n> hello\n  world\n"
            return "one\ntwo\n"
        with mock.patch.object(posixterm, "pane_of",
                               return_value={"pane": "%7", "attached": True, "session": "s"}), \
                mock.patch.object(posixterm, "tmux", side_effect=tmux), \
                mock.patch.object(posixterm, "SEND_PAUSE", 0):
            screen = todo.screen_session(s)
            self.assertEqual(screen, {"cols": 80, "rows": 3, "cursor": [4, 1], "window": [0, 2],
                                      "lines": ["> hello", "  world", ""]})
            self.assertEqual(todo.send_session(s, "yes", ["Enter"]), 4)
            self.assertEqual(todo.log_session(s, 0)["text"], "one\ntwo")
            self.assertEqual(todo.log_session(s, 4), {"text": "two", "offset": 7, "size": 7})
        self.assertIn(("send-keys", "-t", "%7", "-l", "--", "yes"), calls)
        self.assertIn(("send-keys", "-t", "%7", "Enter"), calls)

    def test_a_detached_closer_has_a_session_of_its_own(self):
        with mock.patch("subprocess.Popen") as popen:
            todo.spawn_detached(["x"])
        self.assertTrue(popen.call_args[1]["start_new_session"])


@unittest.skipUnless(POSIX and posixterm.tmux_path(), "needs tmux")
class Tmux(unittest.TestCase):
    """A real session on a tmux server of the test's own."""

    def setUp(self):
        self.socket = mock.patch.object(posixterm, "SOCKET", "octopus-test-%d" % os.getpid())
        self.socket.start()
        self.name = "octopus-" + str(uuid.uuid4())

    def tearDown(self):
        posixterm.tmux("kill-server", check=False)
        self.socket.stop()

    def test_screen_keys_and_log(self):
        line = "printf 'ready\\n'; read answer; printf 'got %s\\n' \"$answer\"; sleep 30"
        self.assertIsNone(posixterm.launch(line, "t", os.getcwd(), self.name, "/tmp",
                                           window=False))
        shell = next(pid for pid, p in posixterm.panes().items() if p["session"] == self.name)
        self.assertFalse(posixterm.pane_of(shell)["attached"])
        deadline = time.time() + 5
        while "ready" not in "\n".join(posixterm.screen(shell)["lines"]):
            self.assertLess(time.time(), deadline, "the session never printed")
            time.sleep(0.1)
        posixterm.send(shell, "yes", ["Enter"])
        deadline = time.time() + 5
        while "got yes" not in "\n".join(posixterm.screen(shell)["lines"]):
            self.assertLess(time.time(), deadline, "the keys never arrived")
            time.sleep(0.1)
        self.assertIn("got yes", posixterm.log(shell)["text"])
        import winproc
        self.assertIn("read answer", winproc.command_lines([shell])[shell])
        winproc.taskkill(shell, force=True)
        self.assertTrue(winproc.wait_exit(shell, 5))


if __name__ == "__main__":
    unittest.main()
