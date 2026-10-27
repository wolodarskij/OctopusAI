"""session_pty.py - a pseudoconsole host for one launched Claude session, with no window.

`todo work <id> --terminal own` starts this file detached. It creates a Windows
pseudoconsole (ConPTY), runs the session's `cmd /c ... claude ...` line inside it, and
serves the terminal to the board over a loopback socket: the screen, typed input, the
scrolled log, a resize and a stop. The board shows the screen in a popup on the page and
types into it, so the session needs no Windows Terminal tab and no console window.

    python core/win/session_pty.py --ticket T0001 --mode work --session <uuid> \
        --title "<title> - T0001 - work" --sessions-dir <project>/TODO/.sessions \
        --cwd <project> [--cols 120 --rows 40] --cmd-line "cmd /c <line>"

`--cmd-line` is the whole command line, given to `CreateProcess` as it is: `cmd /c` and
the line `todo.py` builds for every launch.

While the shell runs the host writes `<sessions-dir>/<session>.<mode>.pty.json`:

    {"host_pid", "shell_pid", "port", "token", "cols", "rows", "log", "ticket", "mode",
     "session", "title", "started"}

`todo.py` finds an own session by the `shell_pid` in that record, and it removes a record
whose `host_pid` is gone. The output stream goes to `<sessions-dir>/<session>.<mode>.log`,
raw VT, kept under 4 MB.

Protocol: one JSON object per line on the socket, answered by one JSON line.

    {"token": ..., "cmd": "screen"}                    -> {"screen": {cols, rows, cursor, lines}}
    {"token": ..., "cmd": "send", "text": "", "keys": []} -> {"written": n}
    {"token": ..., "cmd": "log", "offset": 0}          -> {"text": ..., "offset": n, "size": n}
    {"token": ..., "cmd": "resize", "cols": c, "rows": r} -> {"cols": c, "rows": r}
    {"token": ..., "cmd": "stop"}                      -> {"stopped": true}

Only `ctypes`. Octopus AI declares no dependencies.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as w
import json
import msvcrt
import os
import re
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import console_io  # noqa: E402
from console_io import COORD, Console  # noqa: E402

LOG_CAP = 4 * 1024 * 1024
LOG_KEEP = 3 * 1024 * 1024

k = ctypes.windll.kernel32

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
STD_HANDLES = (-10, -11, -12)
WAIT_TIMEOUT = 0x102


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD),
                ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD),
                ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD), ("wShowWindow", w.WORD),
                ("cbReserved2", w.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD),
                ("dwThreadId", w.DWORD)]


k.CreatePseudoConsole.argtypes = [COORD, w.HANDLE, w.HANDLE, w.DWORD, ctypes.POINTER(ctypes.c_void_p)]
k.CreatePseudoConsole.restype = ctypes.c_long
k.ResizePseudoConsole.argtypes = [ctypes.c_void_p, COORD]
k.ResizePseudoConsole.restype = ctypes.c_long
k.ClosePseudoConsole.argtypes = [ctypes.c_void_p]
k.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD,
                                                ctypes.POINTER(ctypes.c_size_t)]
k.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
k.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
                             ctypes.c_void_p, w.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
k.SetStdHandle.argtypes = [w.DWORD, w.HANDLE]
k.GetStdHandle.restype = w.HANDLE
k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]

# `\x1b[...X` control sequences, `\x1b]...\x07` operating-system commands, `\x1bX` two-byte
# escapes, and the control characters other than newline and tab.
_VT = re.compile("\x1b\\[[0-?]*[ -/]*[@-~]|\x1b\\][^\x07\x1b]*(?:\x07|\x1b\\\\)|\x1b[@-Z\\\\-_]"
                 "|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_vt(text):
    """The log as readable text: a cursor-forward becomes that many spaces, a cursor
    position becomes a line break, every other escape sequence goes, and `\\r` folds into
    the line."""
    text = re.sub("\x1b\\[(\\d*)C", lambda m: " " * int(m.group(1) or 1), text)
    text = re.sub("\x1b\\[\\d*(?:;\\d*)?[Hf]", "\n", text)
    text = _VT.sub("", text)
    return re.sub(r"\r+\n?", "\n", text)


class Host:
    def __init__(self, args):
        self.args = args
        self.cols, self.rows = args.cols, args.rows
        self.hpc = ctypes.c_void_p()
        self.proc = PROCESS_INFORMATION()
        self.in_w = None
        self.out_r = None
        self.console = None
        self.lock = threading.Lock()
        self.dir = Path(args.sessions_dir)
        self.log = self.dir / ("%s.%s.log" % (args.session.lower(), args.mode))
        self.record = self.dir / ("%s.%s.pty.json" % (args.session.lower(), args.mode))
        self.token = secrets.token_urlsafe(16)
        self.stopping = False

    # ── the pseudoconsole ─────────────────────────────────────────────────

    def spawn(self, cmdline):
        in_r, in_w = os.pipe()
        out_r, out_w = os.pipe()
        self.in_w, self.out_r = in_w, out_r
        hr = k.CreatePseudoConsole(COORD(self.cols, self.rows),
                                   w.HANDLE(msvcrt.get_osfhandle(in_r)),
                                   w.HANDLE(msvcrt.get_osfhandle(out_w)), 0, ctypes.byref(self.hpc))
        if hr != 0:
            raise OSError("CreatePseudoConsole failed: 0x%08x" % (hr & 0xFFFFFFFF))
        size = ctypes.c_size_t()
        k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attrs = ctypes.create_string_buffer(size.value)
        if not k.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)):
            raise OSError("InitializeProcThreadAttributeList failed")
        if not k.UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                                           self.hpc.value, ctypes.sizeof(ctypes.c_void_p), None, None):
            raise OSError("UpdateProcThreadAttribute failed")
        six = STARTUPINFOEXW()
        six.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        six.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
        # `CreateProcess` copies a parent's redirected standard handles into the child even
        # with no inheritance, and the child then writes to them and never to the
        # pseudoconsole. With no standard handles, the child takes the console's.
        saved = [k.GetStdHandle(h) for h in STD_HANDLES]
        for h in STD_HANDLES:
            k.SetStdHandle(h, None)
        try:
            ok = k.CreateProcessW(None, ctypes.create_unicode_buffer(cmdline), None, None, False,
                                  EXTENDED_STARTUPINFO_PRESENT, None, str(self.args.cwd),
                                  ctypes.byref(six), ctypes.byref(self.proc))
        finally:
            for h, old in zip(STD_HANDLES, saved):
                k.SetStdHandle(h, old)
        if not ok:
            err = ctypes.GetLastError()
            raise OSError(err, "CreateProcess failed: %s" % ctypes.FormatError(err))
        self._keep = attrs
        threading.Thread(target=self.pump, daemon=True).start()

    def pump(self):
        """Copy the pseudoconsole's output into the log, capped."""
        while True:
            try:
                data = os.read(self.out_r, 65536)
            except OSError:
                break
            if not data:
                break
            with self.lock:
                try:
                    with open(self.log, "ab") as f:
                        f.write(data)
                    if self.log.stat().st_size > LOG_CAP:
                        tail = self.log.read_bytes()[-LOG_KEEP:]
                        self.log.write_bytes(tail)
                except OSError:
                    pass

    def attach(self):
        """Attach to the shell's console for the screen reads. The console exists once the
        shell starts, so the first tries can fail."""
        k.SetConsoleCtrlHandler(None, True)             # a Ctrl+C in the console is not ours
        for _ in range(50):
            try:
                self.console = Console(self.proc.dwProcessId).attach()
                return
            except OSError:
                time.sleep(0.1)
        raise OSError("could not attach to the shell's console")

    def alive(self):
        return k.WaitForSingleObject(self.proc.hProcess, 0) == WAIT_TIMEOUT

    # ── the record and the socket ─────────────────────────────────────────

    def write_record(self, port):
        self.dir.mkdir(parents=True, exist_ok=True)
        rec = {"host_pid": os.getpid(), "shell_pid": self.proc.dwProcessId, "port": port,
               "token": self.token, "cols": self.cols, "rows": self.rows, "log": str(self.log),
               "ticket": self.args.ticket, "mode": self.args.mode, "session": self.args.session,
               "title": self.args.title, "started": time.time()}
        self.record.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")

    def serve(self, srv):
        while not self.stopping:
            try:
                conn, _addr = srv.accept()
            except OSError:
                break
            threading.Thread(target=self.answer, args=(conn,), daemon=True).start()

    def answer(self, conn):
        with conn:
            conn.settimeout(5)
            buf = b""
            try:
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                req = json.loads(buf.decode("utf-8") or "{}")
                if not secrets.compare_digest(str(req.get("token", "")), self.token):
                    reply = {"error": "bad token"}
                else:
                    reply = self.handle(req)
            except Exception as exc:                    # noqa: BLE001 - the host stays up
                reply = {"error": "%s: %s" % (type(exc).__name__, exc)}
            try:
                conn.sendall((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            except OSError:
                pass

    def handle(self, req):
        cmd = req.get("cmd")
        if cmd == "screen":
            with self.lock:
                return {"screen": self.console.screen(), "alive": self.alive()}
        if cmd == "send":
            # Text and keys in two writes, as console_io.Console.send does: an Enter in the
            # same read as the text is taken as part of a paste.
            text = console_io.vt_bytes(req.get("text", ""), [])
            keys = console_io.vt_bytes("", req.get("keys") or [])
            if text:
                os.write(self.in_w, text)
            if text and keys:
                time.sleep(console_io.SEND_PAUSE)
            if keys:
                os.write(self.in_w, keys)
            return {"written": len(text) + len(keys)}
        if cmd == "log":
            offset = max(0, int(req.get("offset") or 0))
            try:
                raw = self.log.read_bytes()
            except OSError:
                raw = b""
            if offset > len(raw):
                offset = 0
            text = strip_vt(raw[offset:].decode("utf-8", "replace"))
            return {"text": text, "offset": len(raw), "size": len(raw)}
        if cmd == "resize":
            cols, rows = int(req["cols"]), int(req["rows"])
            cols, rows = max(20, min(cols, 400)), max(5, min(rows, 200))
            if k.ResizePseudoConsole(self.hpc, COORD(cols, rows)) != 0:
                return {"error": "ResizePseudoConsole failed"}
            self.cols, self.rows = cols, rows
            self.write_record(self.port)
            return {"cols": cols, "rows": rows}
        if cmd == "stop":
            self.stop()
            return {"stopped": True}
        return {"error": "unknown command %r" % cmd}

    def stop(self):
        self.stopping = True
        import winproc
        winproc.taskkill(self.proc.dwProcessId, force=True, tree=True)

    def finish(self):
        try:
            self.record.unlink()
        except OSError:
            pass
        if self.console:
            self.console.detach()
        if self.hpc.value:
            k.ClosePseudoConsole(self.hpc)

    # ── the run ───────────────────────────────────────────────────────────

    def run(self, cmdline):
        self.spawn(cmdline)
        self.attach()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        self.port = srv.getsockname()[1]
        self.write_record(self.port)
        threading.Thread(target=self.serve, args=(srv,), daemon=True).start()
        try:
            while self.alive():
                time.sleep(0.5)
        finally:
            self.stopping = True
            srv.close()
            self.finish()
        code = w.DWORD()
        k.GetExitCodeProcess(self.proc.hProcess, ctypes.byref(code))
        return int(code.value)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Host one Claude session in a pseudoconsole.")
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--mode", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--sessions-dir", dest="sessions_dir", required=True,
                    help="the project's TODO/.sessions directory")
    ap.add_argument("--cwd", required=True, help="the project directory the shell starts in")
    ap.add_argument("--cols", type=int, default=120)
    ap.add_argument("--rows", type=int, default=40)
    ap.add_argument("--cmd-line", required=True,
                    help="the whole command line to run inside the pseudoconsole")
    args = ap.parse_args(argv)
    return Host(args).run(args.cmd_line)


if __name__ == "__main__":
    sys.exit(main())
