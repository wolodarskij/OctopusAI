"""The keys the board sends to a session's console.   python -m unittest discover tests

The key records and the VT bytes are built and compared. Only `Serve` attaches, to the
console of a windowless child it starts.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import support  # noqa: F401  (puts core/win on sys.path)

if sys.platform == "win32":
    import console_io  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "console_io needs ctypes.windll")
class VtBytes(unittest.TestCase):
    def vt(self, name):
        return console_io.parse_key(name)[3]

    def test_ctrl_left_carries_the_modifier(self):
        self.assertEqual(self.vt("Ctrl+Left"), "\x1b[1;5D")

    def test_shift_right_carries_the_modifier(self):
        self.assertEqual(self.vt("Shift+Right"), "\x1b[1;2C")

    def test_ctrl_delete_is_a_tilde_key_with_the_modifier(self):
        self.assertEqual(self.vt("Ctrl+Delete"), "\x1b[3;5~")

    def test_alt_enter_is_escape_then_enter(self):
        self.assertEqual(self.vt("Alt+Enter"), "\x1b\r")

    def test_shift_tab_is_back_tab(self):
        self.assertEqual(self.vt("Shift+Tab"), "\x1b[Z")

    def test_ctrl_r_is_a_control_character(self):
        self.assertEqual(self.vt("Ctrl+R"), "\x12")

    def test_home_without_a_modifier_does_not_change(self):
        self.assertEqual(self.vt("Home"), "\x1b[H")

    def test_text_comes_before_the_keys(self):
        self.assertEqual(console_io.vt_bytes("ab", ["Left"]), b"ab\x1b[D")


@unittest.skipUnless(sys.platform == "win32", "console_io needs ctypes.windll")
class Records(unittest.TestCase):
    def test_a_modified_key_record_carries_the_modifier(self):
        recs = console_io.records("a", ["Ctrl+Left"])
        self.assertEqual(len(recs), 4)                     # a down and an up for each key
        self.assertEqual(recs[0].Event.UnicodeChar, "a")
        for r in recs[2:]:
            self.assertEqual(r.Event.wVirtualKeyCode, 0x25)
            self.assertTrue(r.Event.dwControlKeyState & console_io.LEFT_CTRL_PRESSED)


@unittest.skipUnless(sys.platform == "win32", "console_io needs ctypes.windll")
class Serve(unittest.TestCase):
    """The `serve` worker the board keeps. It attaches to a windowless child's console, so
    no window opens."""

    def setUp(self):
        self.child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                      creationflags=console_io.CREATE_NO_WINDOW)
        self.addCleanup(self.child.wait)
        self.addCleanup(self.child.kill)
        self.worker = subprocess.Popen(
            [sys.executable, str(Path(console_io.__file__)), "serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
            creationflags=console_io.CREATE_NO_WINDOW)
        self.addCleanup(self.stop_worker)

    def stop_worker(self):
        self.worker.kill()
        self.worker.wait(10)
        for pipe in (self.worker.stdin, self.worker.stdout):
            if not pipe.closed:
                pipe.close()

    def ask(self, request):
        self.worker.stdin.write(json.dumps(request) + "\n")
        self.worker.stdin.flush()
        return json.loads(self.worker.stdout.readline())

    def test_one_worker_answers_requests_in_a_row(self):
        first = self.ask({"cmd": "screen", "pid": self.child.pid})
        self.assertNotIn("error", first)
        self.assertIn("lines", first)
        second = self.ask({"cmd": "send", "pid": self.child.pid, "text": "", "keys": []})
        self.assertEqual(second, {"written": 0})

    def test_an_error_is_a_reply_and_the_worker_goes_on(self):
        gone = self.ask({"cmd": "screen", "pid": 0x7FFFFFF0})
        self.assertIn("error", gone)
        self.assertIn("lines", self.ask({"cmd": "screen", "pid": self.child.pid}))

    def test_eof_ends_the_worker(self):
        self.worker.stdin.close()
        self.assertEqual(self.worker.wait(timeout=10), 0)


if __name__ == "__main__":
    unittest.main()
