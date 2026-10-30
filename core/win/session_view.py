"""session_view.py - a console window that shows one own session and takes its input.

An own session runs in a pseudoconsole that `session_pty.py` holds, with no window. `focus`
starts this file in a console window of its own on the owner's desktop. The window shows the
host's output stream and sends every key the owner types to the host, so it works as the
session's terminal. A close of the window ends only the window: the session keeps running.

    python core/win/session_view.py --record <sessions-dir>/<session>.<mode>.pty.json

The window reads the host's log (`log` in the record) from its end, sets the terminal modes
the session turned on before the window opened, and resizes the pseudoconsole to the
window, so the session paints its whole screen again. It follows a resize of the window.
The console reads the keys as VT input, and each read goes to the host as one `send`.

While it runs, the window writes `<session>.<mode>.view.json` beside the host's record:
`{"pid", "created"}`. `todo.py` raises that window on the next `focus` and opens no second
one. The window ends when the host ends.

Only `ctypes`. Octopus AI declares no dependencies.
"""

from __future__ import annotations

import argparse
import codecs
import ctypes
import ctypes.wintypes as w
import json
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import winproc_nt as winproc  # noqa: E402

k = ctypes.windll.kernel32

STD_INPUT, STD_OUTPUT = -10, -11
ENABLE_PROCESSED_OUTPUT = 0x0001
ENABLE_WRAP_AT_EOL_OUTPUT = 0x0002
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
DISABLE_NEWLINE_AUTO_RETURN = 0x0008
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
CP_UTF8 = 65001
# Seconds between two reads of the log while nothing new came, between two reads of the
# window size, and between two checks that the host still runs.
POLL = 0.02
SIZE_EVERY = 0.25
ALIVE_EVERY = 1.0
# The terminal modes the session can turn on before the window opens, set again in the
# window: cursor keys, the cursor, the mouse, the focus events, the alternate screen and
# the bracketed paste.
KEEP_MODES = ("1", "25", "1000", "1002", "1003", "1004", "1006", "1049", "2004")
_MODE = re.compile("\x1b\\[\\?([0-9;]+)([hl])")
# The pseudoconsole asks its terminal for win32-input-mode. This window reads VT input, so
# the request never reaches the console of the window.
_WIN32_INPUT = re.compile("\x1b\\[\\?9001[hl]")


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", w.WORD),
                ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]


k.GetStdHandle.restype = w.HANDLE
k.GetConsoleMode.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
k.SetConsoleMode.argtypes = [w.HANDLE, w.DWORD]
k.GetConsoleScreenBufferInfo.argtypes = [w.HANDLE, ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO)]
k.ReadConsoleW.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD),
                           ctypes.c_void_p]
k.WriteConsoleW.argtypes = [w.HANDLE, w.LPCWSTR, w.DWORD, ctypes.POINTER(w.DWORD),
                            ctypes.c_void_p]
k.SetConsoleTitleW.argtypes = [w.LPCWSTR]


def modes(text):
    """`\\x1b[?<n>h` or `l` for each mode of `KEEP_MODES` that `text` set, its last state."""
    last = {}
    for m in _MODE.finditer(text):
        for n in m.group(1).split(";"):
            if n in KEEP_MODES:
                last[n] = m.group(2)
    return "".join("\x1b[?%s%s" % (n, v) for n, v in last.items())


def split_escape(text):
    """`text` without an escape sequence that the next chunk completes, and that rest."""
    at = text.rfind("\x1b")
    if at < 0 or len(text) - at > 32:
        return text, ""
    tail = text[at + 1:]
    if tail[:1] == "[" and not re.search("[@-~]", tail[1:]):
        return text[:at], text[at:]
    if tail[:1] == "]" and "\x07" not in tail and "\x1b\\" not in tail:
        return text[:at], text[at:]
    if not tail:
        return text[:at], text[at:]
    return text, ""


class View:
    def __init__(self, record):
        self.path = Path(record)
        self.rec = json.loads(self.path.read_text(encoding="utf-8"))
        self.view = self.path.with_name(self.path.name[:-len(".pty.json")] + ".view.json")
        self.hin = k.GetStdHandle(STD_INPUT)
        self.hout = k.GetStdHandle(STD_OUTPUT)
        self.done = threading.Event()
        self.size = (0, 0)

    # ── the host ──────────────────────────────────────────────────────────

    def call(self, request, timeout=5.0):
        request = dict(request, token=self.rec["token"])
        with socket.create_connection(("127.0.0.1", self.rec["port"]), timeout=timeout) as sock:
            sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        reply = json.loads(buf.decode("utf-8") or "{}")
        if reply.get("error"):
            raise OSError(reply["error"])
        return reply

    def host_alive(self):
        return self.path.is_file() and self.rec.get("host_pid") in winproc.processes()

    # ── the console of this window ────────────────────────────────────────

    def setup(self):
        k.SetConsoleOutputCP(CP_UTF8)
        k.SetConsoleCP(CP_UTF8)
        k.SetConsoleTitleW(self.rec.get("title") or "session")
        k.SetConsoleMode(self.hout, ENABLE_PROCESSED_OUTPUT | ENABLE_WRAP_AT_EOL_OUTPUT
                         | ENABLE_VIRTUAL_TERMINAL_PROCESSING | DISABLE_NEWLINE_AUTO_RETURN)
        # No line input, no echo, no processed input: Ctrl+C is a key for the session. No
        # quick edit: a click in a classic console would stop the output.
        k.SetConsoleMode(self.hin, ENABLE_VIRTUAL_TERMINAL_INPUT | ENABLE_EXTENDED_FLAGS)

    def window_size(self):
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not k.GetConsoleScreenBufferInfo(self.hout, ctypes.byref(info)):
            return self.size
        return (info.srWindow.Right - info.srWindow.Left + 1,
                info.srWindow.Bottom - info.srWindow.Top + 1)

    def write(self, text):
        if not text:
            return
        n = w.DWORD()
        for i in range(0, len(text), 8192):
            part = text[i:i + 8192]
            k.WriteConsoleW(self.hout, part, len(part), ctypes.byref(n), None)

    # ── the run ───────────────────────────────────────────────────────────

    def resize(self, cols, rows, repaint=False):
        """Give the pseudoconsole the size of this window. `repaint` makes the session paint
        its whole screen also when the size is the same."""
        if repaint and (cols, rows) == (self.rec.get("cols"), self.rec.get("rows")):
            self.call({"cmd": "resize", "cols": cols, "rows": max(5, rows - 1)})
            time.sleep(0.1)
        self.call({"cmd": "resize", "cols": cols, "rows": rows})
        self.rec["cols"], self.rec["rows"] = cols, rows
        self.size = (cols, rows)

    def keys(self):
        """Every key the owner types, to the host, until the host ends."""
        buf = ctypes.create_unicode_buffer(512)
        n = w.DWORD()
        while not self.done.is_set():
            if not k.ReadConsoleW(self.hin, buf, 512, ctypes.byref(n), None):
                time.sleep(0.05)
                continue
            text = buf.value[:n.value]
            if not text:
                continue
            try:
                self.call({"cmd": "send", "text": text})
            except (OSError, ValueError):
                if not self.host_alive():
                    self.done.set()

    def run(self):
        self.setup()
        try:
            self.view.write_text(json.dumps({"pid": os.getpid(),
                                             "created": winproc.created_at(os.getpid())}) + "\n",
                                 encoding="utf-8")
        except OSError:
            pass
        try:
            return self.show()
        finally:
            try:
                self.view.unlink()
            except OSError:
                pass

    def show(self):
        log = Path(self.rec["log"])
        try:
            raw = log.read_bytes()
        except OSError:
            raw = b""
        offset = len(raw)
        self.write("\x1b[?1049l\x1b[H\x1b[2J\x1b[3J"
                   + modes(raw.decode("utf-8", "replace")))
        del raw
        cols, rows = self.window_size()
        self.resize(cols, rows, repaint=True)
        threading.Thread(target=self.keys, daemon=True).start()
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        carry = ""
        next_size = next_alive = 0.0
        f = None
        try:
            while not self.done.is_set():
                now = time.monotonic()
                if now >= next_size:
                    next_size = now + SIZE_EVERY
                    size = self.window_size()
                    if size != self.size and size[0] > 0 and size[1] > 0:
                        try:
                            self.resize(*size)
                        except (OSError, ValueError):
                            pass
                if f is None:
                    # The host makes the log at the session's first output.
                    try:
                        f = open(log, "rb")
                    except OSError:
                        f = None
                if now >= next_alive:
                    next_alive = now + ALIVE_EVERY
                    if not self.host_alive():
                        break
                    end = os.fstat(f.fileno()).st_size if f is not None else offset
                    if end < offset:
                        # The host cut the log to its tail: read on from its end, and let
                        # the session paint its screen again.
                        offset = end
                        try:
                            self.resize(*self.size, repaint=True)
                        except (OSError, ValueError):
                            pass
                data = b""
                if f is not None:
                    f.seek(offset)
                    data = f.read(65536)
                if not data:
                    time.sleep(POLL)
                    continue
                offset += len(data)
                text, carry = split_escape(carry + decoder.decode(data))
                self.write(_WIN32_INPUT.sub("", text))
        finally:
            if f is not None:
                f.close()
        self.done.set()
        self.write("\r\n\x1b[0m[the session ended]\r\n")
        time.sleep(2)
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Show one own session in this console window.")
    ap.add_argument("--record", required=True,
                    help="the host's record, <sessions-dir>/<session>.<mode>.pty.json")
    args = ap.parse_args(argv)
    return View(args.record).run()


if __name__ == "__main__":
    sys.exit(main())
