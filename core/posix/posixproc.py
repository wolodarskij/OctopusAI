"""posixproc.py - the process calls the Agent Board needs, on macOS and Linux.

The same calls as `core/win/winproc_nt.py`, and `winproc` hands this module to every caller
that is not on Windows. macOS answers through libproc and `sysctl` by ctypes, Linux through
`/proc`. Like the Windows module it needs nothing outside the standard library.

WHAT IT ANSWERS. Which processes exist and who their parent is, when a process started, what
command line it carries, its current directory, and how to end it. Windows has top-level
windows to find and raise; here the terminal window belongs to `posixterm.py`, so `windows`,
`find_terminal_window` and `raise_window` find nothing.

THE NAMES. A process is named by its executable's file name, lowercased, the way Windows names
it by its image: `sh`, `zsh`, `node`, `claude`, `tmux`. Claude Code's native build runs from a
file named after its version (`.../versions/2.1.3`), so a name that is only a version is
replaced by the file name the process was started as, its `argv[0]`.
"""

from __future__ import annotations

import os
import re
import signal
import sys
import time

# The same names as the Windows module; no window here ever has one of these classes.
WT_WINDOW_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"
CONSOLE_WINDOW_CLASS = "ConsoleWindowClass"

# Process start times are kept in the unit `winproc_nt.created_at` gives: 100 nanoseconds
# since 1601, so `todo._age` reads both alike.
_EPOCH_1601 = 11644473600

_VERSION_NAME = re.compile(r"^\d+(\.\d+)+")

DARWIN = sys.platform == "darwin"


def _filetime(sec, usec=0):
    return int((sec + _EPOCH_1601) * 10**7 + usec * 10)


# -- macOS: libproc and sysctl ------------------------------------------------

if DARWIN:
    import ctypes
    import ctypes.util

    _libc = ctypes.CDLL(ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib",
                        use_errno=True)
    _libc.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _libc.proc_listallpids.restype = ctypes.c_int
    _libc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                   ctypes.c_void_p, ctypes.c_int]
    _libc.proc_pidinfo.restype = ctypes.c_int
    _libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                             ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    _libc.sysctl.restype = ctypes.c_int

    PROC_PIDTBSDINFO = 3
    PROC_PIDVNODEPATHINFO = 9
    PROC_PIDT_SHORTBSDINFO = 13
    SZOMB = 5
    MAXCOMLEN = 16
    MAXPATHLEN = 1024
    CTL_KERN, KERN_ARGMAX, KERN_PROCARGS2 = 1, 8, 49

    class _BsdShortInfo(ctypes.Structure):
        """`struct proc_bsdshortinfo`: any process of the machine answers it."""
        _fields_ = [("pid", ctypes.c_uint32), ("ppid", ctypes.c_uint32),
                    ("pgid", ctypes.c_uint32), ("status", ctypes.c_uint32),
                    ("comm", ctypes.c_char * MAXCOMLEN), ("flags", ctypes.c_uint32),
                    ("uid", ctypes.c_uint32), ("gid", ctypes.c_uint32),
                    ("ruid", ctypes.c_uint32), ("rgid", ctypes.c_uint32),
                    ("svuid", ctypes.c_uint32), ("svgid", ctypes.c_uint32),
                    ("rfu", ctypes.c_uint32)]

    class _BsdInfo(ctypes.Structure):
        """`struct proc_bsdinfo`, which carries the start time."""
        _fields_ = [("flags", ctypes.c_uint32), ("status", ctypes.c_uint32),
                    ("xstatus", ctypes.c_uint32), ("pid", ctypes.c_uint32),
                    ("ppid", ctypes.c_uint32), ("uid", ctypes.c_uint32),
                    ("gid", ctypes.c_uint32), ("ruid", ctypes.c_uint32),
                    ("rgid", ctypes.c_uint32), ("svuid", ctypes.c_uint32),
                    ("svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
                    ("comm", ctypes.c_char * MAXCOMLEN),
                    ("name", ctypes.c_char * (2 * MAXCOMLEN)),
                    ("nfiles", ctypes.c_uint32), ("pgid", ctypes.c_uint32),
                    ("pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
                    ("e_tpgid", ctypes.c_uint32), ("nice", ctypes.c_int32),
                    ("start_tvsec", ctypes.c_uint64), ("start_tvusec", ctypes.c_uint64)]

    # `struct proc_vnodepathinfo`: the current directory's `vnode_info` (152 bytes), then
    # its path; the root directory follows the same way.
    _VNODE_INFO_SIZE = 152
    _VNODEPATHINFO_SIZE = 2 * (_VNODE_INFO_SIZE + MAXPATHLEN)

    def _short(pid):
        info = _BsdShortInfo()
        n = _libc.proc_pidinfo(pid, PROC_PIDT_SHORTBSDINFO, 0, ctypes.byref(info),
                               ctypes.sizeof(info))
        return info if n == ctypes.sizeof(info) else None

    def _bsd(pid):
        info = _BsdInfo()
        n = _libc.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ctypes.byref(info),
                               ctypes.sizeof(info))
        return info if n == ctypes.sizeof(info) else None

    def _pids():
        n = _libc.proc_listallpids(None, 0)
        if n <= 0:
            return []
        buf = (ctypes.c_int * (n + 256))()
        n = _libc.proc_listallpids(buf, ctypes.sizeof(buf))
        return [p for p in buf[:max(n, 0)] if p > 0]

    _ARGMAX = [0]

    def _argv(pid):
        """The process's arguments out of `KERN_PROCARGS2`, or None when refused: the
        argument count, the executable path, padding, then the arguments."""
        if not _ARGMAX[0]:
            mib = (ctypes.c_int * 2)(CTL_KERN, KERN_ARGMAX)
            val, size = ctypes.c_int(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
            if _libc.sysctl(mib, 2, ctypes.byref(val), ctypes.byref(size), None, 0) != 0:
                return None
            _ARGMAX[0] = val.value or 1024 * 1024
        mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, int(pid))
        buf = ctypes.create_string_buffer(_ARGMAX[0])
        size = ctypes.c_size_t(_ARGMAX[0])
        if _libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
            return None
        raw = buf.raw[:size.value]
        if len(raw) < 4:
            return None
        argc = int.from_bytes(raw[:4], sys.byteorder)
        rest = raw[4:]
        end = rest.find(b"\0")
        if end < 0:
            return None
        pos = end
        while pos < len(rest) and rest[pos:pos + 1] == b"\0":
            pos += 1
        args = rest[pos:].split(b"\0")[:argc]
        return [a.decode("utf-8", "replace") for a in args]

    def _state(pid):
        info = _short(pid)
        return None if info is None else int(info.status)

    def processes():
        """Every process, as `pid -> (parent pid, lowercased executable name)`."""
        out = {}
        for pid in _pids():
            info = _short(pid)
            if info is None:
                continue
            name = info.comm.decode("utf-8", "replace").lower()
            if _VERSION_NAME.match(name):
                argv = _argv(pid)
                if argv:
                    name = os.path.basename(argv[0]).lstrip("-").lower() or name
            out[pid] = (int(info.ppid), name)
        return out

    def created_at(pid):
        """When the process started, in the unit of `winproc_nt.created_at`. `None` when the
        process is gone or refused. Two runs under one pid never share this value."""
        info = _bsd(int(pid)) if pid else None
        if info is None or not info.start_tvsec:
            return None
        return _filetime(int(info.start_tvsec), int(info.start_tvusec))

    def _command_line(pid):
        argv = _argv(pid)
        return " ".join(argv) if argv else None

    def current_directory(pid):
        """The process's current directory, or `None` when refused."""
        buf = ctypes.create_string_buffer(_VNODEPATHINFO_SIZE)
        n = _libc.proc_pidinfo(int(pid), PROC_PIDVNODEPATHINFO, 0, buf, _VNODEPATHINFO_SIZE)
        if n != _VNODEPATHINFO_SIZE:
            return None
        path = buf.raw[_VNODE_INFO_SIZE:_VNODE_INFO_SIZE + MAXPATHLEN].split(b"\0", 1)[0]
        return path.decode("utf-8", "replace") or None

    def _gone(pid):
        state = _state(pid)
        return state is None or state == SZOMB


# -- Linux and the rest: /proc ------------------------------------------------

else:
    _PROC = "/proc"

    def _stat(pid):
        """`(comm, state, ppid, starttime)` out of `/proc/<pid>/stat`, or None."""
        try:
            with open("%s/%d/stat" % (_PROC, pid), "rb") as fh:
                raw = fh.read().decode("utf-8", "replace")
        except OSError:
            return None
        head, _sep, tail = raw.rpartition(")")
        comm = head.split("(", 1)[-1]
        fields = tail.split()
        try:
            return comm, fields[0], int(fields[1]), int(fields[19])
        except (IndexError, ValueError):
            return None

    def _argv(pid):
        try:
            with open("%s/%d/cmdline" % (_PROC, pid), "rb") as fh:
                raw = fh.read()
        except OSError:
            return None
        args = raw.rstrip(b"\0").split(b"\0")
        return [a.decode("utf-8", "replace") for a in args] if raw else None

    _BOOT = [None]

    def _boot_time():
        if _BOOT[0] is None:
            try:
                with open("%s/stat" % _PROC) as fh:
                    for line in fh:
                        if line.startswith("btime "):
                            _BOOT[0] = int(line.split()[1])
                            break
            except OSError:
                pass
        return _BOOT[0] or 0

    def processes():
        """Every process, as `pid -> (parent pid, lowercased executable name)`."""
        out = {}
        try:
            names = os.listdir(_PROC)
        except OSError:
            return out
        for d in names:
            if not d.isdigit():
                continue
            st = _stat(int(d))
            if st is None:
                continue
            name = st[0].lower()
            if _VERSION_NAME.match(name):
                argv = _argv(int(d))
                if argv:
                    name = os.path.basename(argv[0]).lstrip("-").lower() or name
            out[int(d)] = (st[2], name)
        return out

    def created_at(pid):
        st = _stat(int(pid)) if pid else None
        if st is None:
            return None
        ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        sec = _boot_time() + st[3] / float(ticks)
        return _filetime(int(sec), int((sec - int(sec)) * 1e6))

    def _command_line(pid):
        argv = _argv(pid)
        return " ".join(argv) if argv else None

    def current_directory(pid):
        try:
            return os.readlink("%s/%d/cwd" % (_PROC, int(pid)))
        except OSError:
            return None

    def _gone(pid):
        st = _stat(int(pid))
        return st is None or st[1] == "Z"


# -- the same on every POSIX machine -----------------------------------------

def children(procs, pid):
    """The pids whose parent is `pid`, out of a `processes()` result."""
    return [p for p, (ppid, _name) in procs.items() if ppid == pid]


def _peb_command_line(pid):
    """The command line, its arguments joined by spaces. The Windows module reads it out of
    the process's PEB, hence the name every caller already uses."""
    return _command_line(int(pid))


def command_lines(pids):
    """`pid -> command line` for the pids given that still run and may be read."""
    out = {}
    for pid in pids:
        line = _command_line(int(pid))
        if line:
            out[pid] = line
    return out


def _tree(pid, procs=None):
    """`pid` and every process below it, the deepest first."""
    procs = processes() if procs is None else procs
    order, todo = [], [pid]
    while todo:
        p = todo.pop()
        if p in order:
            continue
        order.append(p)
        todo += children(procs, p)
    return list(reversed(order))


def taskkill(pid, force=False, tree=True):
    """End the process, with `tree` its children too: SIGKILL with `force`, else SIGTERM.
    Returns 0 when the process was signalled, 128 when there is none (the code `taskkill`
    gives), 1 when it was refused."""
    sig = signal.SIGKILL if force else signal.SIGTERM
    rc = 128
    for p in (_tree(int(pid)) if tree else [int(pid)]):
        try:
            os.kill(p, sig)
            if p == int(pid):
                rc = 0
        except ProcessLookupError:
            pass
        except PermissionError:
            if p == int(pid):
                rc = 1
    return rc


def terminate(pid, code=0):
    """End the process alone. POSIX gives no way to choose another process's exit code, so
    `code` is not used: a tmux pane closes whatever its shell's code. Returns True when the
    process was signalled."""
    try:
        os.kill(int(pid), signal.SIGKILL)
        return True
    except OSError:
        return False


def wait_exit(pid, seconds):
    """Wait up to `seconds` for the process to exit. True when it is gone. A zombie counts
    as gone: it runs nothing, it only waits for its parent to read its exit code."""
    end = time.time() + seconds
    while True:
        if _gone(int(pid)):
            return True
        if time.time() >= end:
            return False
        time.sleep(0.05)


# -- windows: none here -------------------------------------------------------

def windows(class_name=None):
    """Windows' top-level windows. A terminal window here is found by `posixterm.py`."""
    return []


def find_terminal_window(match):
    return None


def raise_window(hwnd):
    return False
