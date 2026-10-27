"""console_io.py - read the screen of another process's console, and type into it.

A console process on Windows has a screen buffer and an input buffer, both owned by the
console host (`conhost.exe`, or `OpenConsole.exe` inside Windows Terminal). A process that
has no console of its own can attach to that console with `AttachConsole(pid)`, read the
screen buffer through `CONOUT$` and write key records into the input buffer through
`CONIN$`. That is how the Agent Board reads a launched session's terminal and types into it
without a window coming forward.

    python core/win/console_io.py screen <pid>                 the screen as JSON
    python core/win/console_io.py send <pid> --text "hello"    type text
    python core/win/console_io.py send <pid> --keys Enter,Ctrl+C
    python core/win/console_io.py window <pid>                 the window that shows it
    python core/win/console_io.py serve                        JSON requests on stdin

`pid` is any process attached to the console: the session's `cmd.exe` or its `claude.exe`.

An attach is process-wide and a process can hold one console at a time, so `todo.py` runs
this file as a subprocess with no window and never attaches the board server itself. It
keeps one `serve` worker for every call, because a new Python per call costs ~200 ms; the
worker detaches after each request. `session_pty.py`, which owns one console for its whole life, imports
the functions and keeps its attach.

The screen is the console's buffer: for a pseudoconsole (a Windows Terminal tab, or an own
pty) the buffer is the visible viewport, so the scrollback is not in it.

Only `ctypes`. Octopus AI declares no dependencies.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as w
import json
import signal
import sys
import time

k =ctypes.windll.kernel32

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ_WRITE = 0x3
OPEN_EXISTING = 3
KEY_EVENT = 0x0001
SHIFT_PRESSED = 0x0010
LEFT_CTRL_PRESSED = 0x0008
LEFT_ALT_PRESSED = 0x0002
CREATE_NO_WINDOW = 0x08000000
# Seconds between the typed text and the named keys of one `send`.
SEND_PAUSE = 0.4


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", w.WORD),
                ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", w.BOOL), ("wRepeatCount", w.WORD), ("wVirtualKeyCode", w.WORD),
                ("wVirtualScanCode", w.WORD), ("UnicodeChar", w.WCHAR),
                ("dwControlKeyState", w.DWORD)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", w.WORD), ("Event", KEY_EVENT_RECORD)]


k.AttachConsole.argtypes = [w.DWORD]
k.AttachConsole.restype = w.BOOL
k.FreeConsole.restype = w.BOOL
k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD,
                          w.HANDLE]
k.CreateFileW.restype = w.HANDLE
k.GetConsoleScreenBufferInfo.argtypes = [w.HANDLE, ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO)]
k.ReadConsoleOutputCharacterW.argtypes = [w.HANDLE, w.LPWSTR, w.DWORD, COORD,
                                          ctypes.POINTER(w.DWORD)]
k.WriteConsoleInputW.argtypes = [w.HANDLE, ctypes.POINTER(INPUT_RECORD), w.DWORD,
                                 ctypes.POINTER(w.DWORD)]
k.GetConsoleWindow.restype = w.HANDLE
_user32 = ctypes.windll.user32
_user32.MapVirtualKeyW.argtypes = [w.UINT, w.UINT]
_user32.MapVirtualKeyW.restype = w.UINT
_user32.GetAncestor.argtypes = [w.HWND, w.UINT]
_user32.GetAncestor.restype = w.HWND
GA_ROOTOWNER = 3


def MapVirtualKeyW(vk, kind):
    return _user32.MapVirtualKeyW(vk, kind)

# Named keys: the virtual-key code and the character a key record carries, and the VT
# sequence a pseudoconsole's input pipe takes for the same key.
KEYS = {
    "Enter": (0x0D, "\r", "\r"),
    "Esc": (0x1B, "\x1b", "\x1b"),
    "Tab": (0x09, "\t", "\t"),
    "Backspace": (0x08, "\x08", "\x7f"),
    "Delete": (0x2E, "", "\x1b[3~"),
    "Space": (0x20, " ", " "),
    "Up": (0x26, "", "\x1b[A"),
    "Down": (0x28, "", "\x1b[B"),
    "Left": (0x25, "", "\x1b[D"),
    "Right": (0x27, "", "\x1b[C"),
    "Home": (0x24, "", "\x1b[H"),
    "End": (0x23, "", "\x1b[F"),
    "PageUp": (0x21, "", "\x1b[5~"),
    "PageDown": (0x22, "", "\x1b[6~"),
}
KEY_NAMES = list(KEYS) + ["Shift+Tab", "Ctrl+<letter>", "Alt+<letter>", "Ctrl+<named key>",
                          "Alt+<named key>", "Shift+<named key>"]

# The last letter of the xterm sequence of a cursor key, which takes a modifier as
# `ESC [1;<m><letter>`, and the number of a tilde key, which takes `ESC [<n>;<m>~`.
CSI_LETTER = {"Up": "A", "Down": "B", "Right": "C", "Left": "D", "Home": "H", "End": "F"}
CSI_TILDE = {"Delete": "3", "PageUp": "5", "PageDown": "6"}
ALT_PREFIXED = ("Enter", "Esc", "Tab", "Backspace", "Space")


def _vt_named(base, state):
    """The VT sequence of the named key `base` with the modifiers in `state`, by the xterm
    convention `m = 1 + shift + 2*alt + 4*ctrl`."""
    vt = KEYS[base][2]
    shift = bool(state & SHIFT_PRESSED)
    alt = bool(state & LEFT_ALT_PRESSED)
    ctrl = bool(state & LEFT_CTRL_PRESSED)
    m = 1 + shift + 2 * alt + 4 * ctrl
    if base in CSI_LETTER and m > 1:
        return "\x1b[1;%d%s" % (m, CSI_LETTER[base])
    if base in CSI_TILDE and m > 1:
        return "\x1b[%s;%d~" % (CSI_TILDE[base], m)
    if base == "Tab" and shift:
        vt = "\x1b[Z"
    if base in ALT_PREFIXED and alt:
        vt = "\x1b" + vt
    return vt


def parse_key(name):
    """`(vk, char, state, vt)` for a key name: `Enter`, `Shift+Tab`, `Ctrl+C`, `Alt+M`, or
    a single character. Raises ValueError for a name it does not know."""
    name = name.strip()
    state = 0
    parts = name.split("+")
    base = parts[-1]
    for mod in parts[:-1]:
        m = mod.lower()
        if m == "ctrl":
            state |= LEFT_CTRL_PRESSED
        elif m == "shift":
            state |= SHIFT_PRESSED
        elif m == "alt":
            state |= LEFT_ALT_PRESSED
        else:
            raise ValueError("unknown modifier %r in %r" % (mod, name))
    if base in KEYS:
        vk, ch, _vt = KEYS[base]
        return vk, ch, state, _vt_named(base, state)
    if len(base) == 1:
        if state & LEFT_CTRL_PRESSED and base.isalpha():
            ctrl = chr(ord(base.upper()) - 64)
            return ord(base.upper()), ctrl, state, ctrl
        if state & LEFT_ALT_PRESSED:
            return ord(base.upper()), base, state, "\x1b" + base
        return ord(base.upper()) if base.isalnum() else 0, base, state, base
    raise ValueError("unknown key %r; the names are %s" % (name, ", ".join(KEY_NAMES)))


def records(text="", keys=()):
    """Key records for `text` typed as characters, then the named `keys`. Each key is a
    down record and an up record."""
    out = []

    def push(vk, ch, state):
        for down in (1, 0):
            r = INPUT_RECORD()
            r.EventType = KEY_EVENT
            r.Event.bKeyDown = down
            r.Event.wRepeatCount = 1
            r.Event.wVirtualKeyCode = vk
            # A real keyboard sends a scan code. Without it a terminal app that reads key
            # records (win32-input-mode) can drop a lone Enter as not a key press.
            r.Event.wVirtualScanCode = MapVirtualKeyW(vk, 0) if vk else 0
            r.Event.UnicodeChar = ch if ch else "\0"
            r.Event.dwControlKeyState = state
            out.append(r)

    for ch in text:
        push(ord(ch.upper()) if ch.isalnum() and ch.isascii() else 0, ch, 0)
    for name in keys:
        vk, ch, state, _vt = parse_key(name)
        push(vk, ch, state)
    return out


def vt_bytes(text="", keys=()):
    """The same input as VT bytes for a pseudoconsole's input pipe."""
    parts = [text]
    for name in keys:
        parts.append(parse_key(name)[3])
    return "".join(parts).encode("utf-8")


class Console:
    """An attach to one process's console. `with Console(pid) as c: c.screen()`."""

    def __init__(self, pid):
        self.pid = int(pid)
        self.out = None
        self.inp = None

    def __enter__(self):
        return self.attach()

    def __exit__(self, *exc):
        self.detach()

    def attach(self):
        k.FreeConsole()
        if not k.AttachConsole(self.pid):
            err = ctypes.GetLastError()
            raise OSError(err, "AttachConsole(%d) failed: %s" % (self.pid, ctypes.FormatError(err)))
        self.out = k.CreateFileW("CONOUT$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ_WRITE,
                                 None, OPEN_EXISTING, 0, None)
        self.inp = k.CreateFileW("CONIN$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ_WRITE,
                                 None, OPEN_EXISTING, 0, None)
        return self

    def detach(self):
        for h in (self.out, self.inp):
            if h:
                k.CloseHandle(h)
        self.out = self.inp = None
        k.FreeConsole()

    def screen(self):
        """The whole screen buffer: `cols`, `rows`, `cursor` `[x, y]`, `window`
        `[top, bottom]`, and `lines` with trailing spaces removed."""
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not k.GetConsoleScreenBufferInfo(self.out, ctypes.byref(info)):
            err = ctypes.GetLastError()
            raise OSError(err, "GetConsoleScreenBufferInfo: %s" % ctypes.FormatError(err))
        cols, rows = info.dwSize.X, info.dwSize.Y
        lines = []
        buf = ctypes.create_unicode_buffer(cols + 1)
        n = w.DWORD()
        for y in range(rows):
            if not k.ReadConsoleOutputCharacterW(self.out, buf, cols, COORD(0, y), ctypes.byref(n)):
                lines.append("")
                continue
            lines.append(buf.value[:n.value].rstrip())
        return {"cols": cols, "rows": rows,
                "cursor": [info.dwCursorPosition.X, info.dwCursorPosition.Y],
                "window": [info.srWindow.Top, info.srWindow.Bottom],
                "lines": lines}

    def send(self, text="", keys=()):
        """Type `text`, then the named `keys`. Returns the number of records written.

        Text and keys go in two writes with a pause between: an app that sees many
        characters in one read takes them as a paste, and an Enter inside a paste is a new
        line, not a submit."""
        total = 0
        batches = [records(text, ())] if text else []
        if keys:
            batches.append(records("", keys))
        for i, recs in enumerate(batches):
            if i:
                time.sleep(SEND_PAUSE)
            arr = (INPUT_RECORD * len(recs))(*recs)
            n = w.DWORD()
            if not k.WriteConsoleInputW(self.inp, arr, len(recs), ctypes.byref(n)):
                err = ctypes.GetLastError()
                raise OSError(err, "WriteConsoleInput: %s" % ctypes.FormatError(err))
            total += n.value
        return total


def screen(pid):
    with Console(pid) as c:
        return c.screen()


def send(pid, text="", keys=()):
    with Console(pid) as c:
        return c.send(text, keys)


def window(pid):
    """`{"hwnd": n}`: the top-level window that shows the console of `pid`, 0 when none.
    Under a classic console it is the conhost window itself. Under a pseudoconsole (a
    Windows Terminal window) the console window is a hidden pseudo window that the
    terminal window owns, so its root owner is that terminal window."""
    with Console(pid):
        hwnd = k.GetConsoleWindow()
        root = _user32.GetAncestor(hwnd, GA_ROOTOWNER) if hwnd else None
        return {"hwnd": int(root or hwnd or 0)}


def handle(req):
    """One `serve` request as a reply dict. Each request attaches, reads or writes, and
    detaches before it returns: an attached process keeps the console alive, so a worker
    that stayed attached would hold a finished Windows Terminal tab open."""
    try:
        cmd = req.get("cmd")
        if cmd == "screen":
            return screen(req["pid"])
        if cmd == "send":
            return {"written": send(req["pid"], req.get("text") or "", req.get("keys") or [])}
        if cmd == "window":
            return window(req["pid"])
        return {"error": "unknown cmd %r" % cmd}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"error": str(exc)}


def serve(inp=None, out=None):
    """Answer one JSON request per line on `inp` with one JSON reply per line on `out`, until
    EOF. The requests are `{"cmd": "screen", "pid": n}`,
    `{"cmd": "send", "pid": n, "text": "", "keys": []}` and `{"cmd": "window", "pid": n}`.
    `todo.py` keeps one of these running for the board, in place of a new process per call."""
    inp = sys.stdin if inp is None else inp
    out = sys.stdout if out is None else out
    for line in inp:
        if not line.strip():
            continue
        try:
            reply = handle(json.loads(line))
        except ValueError as exc:
            reply = {"error": "bad request: %s" % exc}
        out.write(json.dumps(reply, ensure_ascii=False) + "\n")
        out.flush()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read or type into another process's console.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="answer JSON requests on stdin, one per line, until EOF")
    p = sub.add_parser("screen", help="the screen buffer as JSON")
    p.add_argument("pid", type=int)
    p = sub.add_parser("window", help="the window that shows the console, as JSON")
    p.add_argument("pid", type=int)
    p = sub.add_parser("send", help="type text and keys into the console")
    p.add_argument("pid", type=int)
    p.add_argument("--text", default="")
    p.add_argument("--keys", default="", help="comma-separated: Enter, Esc, Tab, Shift+Tab, "
                   "Backspace, Delete, Up, Down, Left, Right, Home, End, PageUp, PageDown, "
                   "Ctrl+<letter>, Alt+<letter>, and Ctrl+/Alt+/Shift+ a named key "
                   "(Ctrl+Left, Alt+Enter, ...)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if args.cmd == "serve":
        try:
            sys.stdin.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
        # While attached, the worker gets the console's Ctrl+C and Ctrl+Break, the ones a
        # `send` of `Ctrl+C` raises among them. They are meant for the session.
        for name in ("SIGINT", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), signal.SIG_IGN)
        return serve()
    try:
        if args.cmd == "screen":
            print(json.dumps(screen(args.pid), ensure_ascii=False))
        elif args.cmd == "window":
            print(json.dumps(window(args.pid)))
        else:
            keys = [x for x in args.keys.split(",") if x.strip()]
            print(json.dumps({"written": send(args.pid, args.text, keys)}))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
