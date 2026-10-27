"""winproc_nt.py - the Windows process and window calls the Agent Board needs, through ctypes.

WHY CTYPES. `psutil` and `pywin32` are not installed and Octopus AI declares no
dependencies. Everything here is in the standard library.

WHAT IT ANSWERS. Which processes exist and who their parent is, when a process started,
what command line it carries, and which top-level window to bring to the front. Nothing
here knows about tickets; `todo.py` joins these answers to the ticket tree.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
MAX_PATH = 260

SW_SHOW = 5
SW_RESTORE = 9

WT_WINDOW_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"
CONSOLE_WINDOW_CLASS = "ConsoleWindowClass"


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD),
                ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * MAX_PATH)]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort),
                ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_void_p)]


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("ExitStatus", ctypes.c_long),
                ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_void_p),
                ("BasePriority", ctypes.c_long),
                ("UniqueProcessId", ctypes.c_void_p),
                ("InheritedFromUniqueProcessId", ctypes.c_void_p)]


kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
user32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]

ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
INVALID_HANDLE = ctypes.c_void_p(-1).value


# -- processes --------------------------------------------------------------

def processes():
    """Every process, as `pid -> (parent pid, lowercased image name)`. One snapshot, a few
    milliseconds, and it opens a handle on nothing."""
    out = {}
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE:
        return out
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID), e.szExeFile.lower())
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return out


def children(procs, pid):
    """The pids whose parent is `pid`, out of a `processes()` result."""
    return [p for p, (ppid, _name) in procs.items() if ppid == pid]


def created_at(pid):
    """When the process started, as a count of 100 nanoseconds since 1601. `None` when the
    process is gone or the handle is refused. Two runs under one pid never share this
    value, so it is what tells a reused pid from the original."""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        c, x, k, u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not kernel32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(x),
                                        ctypes.byref(k), ctypes.byref(u)):
            return None
        return (c.dwHighDateTime << 32) | c.dwLowDateTime
    finally:
        kernel32.CloseHandle(h)


def _peb_command_line(pid):
    """The command line read out of the process's own PEB."""
    return _peb_parameter(pid, 0x70)


def current_directory(pid):
    """The process's current directory, read out of its PEB, or `None` when the handle is
    refused. A process holds its current directory open, so Windows refuses to rename it."""
    return _peb_parameter(pid, 0x38)


def _peb_parameter(pid, offset):
    """One `UNICODE_STRING` of the process's RTL_USER_PROCESS_PARAMETERS, at `offset`."""
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return None
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        written = wt.ULONG()
        if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi),
                                           ctypes.byref(written)) != 0:
            return None
        if not pbi.PebBaseAddress:
            return None

        def read(addr, size):
            buf = ctypes.create_string_buffer(size)
            got = ctypes.c_size_t()
            if not kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size,
                                              ctypes.byref(got)):
                return None
            return buf.raw[:got.value]

        # PEB.ProcessParameters, then the string at `offset`: 0x70 CommandLine, 0x38
        # CurrentDirectory.DosPath. The offsets are the 64-bit ones, and this reads a 64-bit
        # process from a 64-bit Python.
        raw = read(pbi.PebBaseAddress + 0x20, 8)
        if not raw or len(raw) < 8:
            return None
        params = int.from_bytes(raw, "little")
        raw = read(params + offset, ctypes.sizeof(UNICODE_STRING))
        if not raw or len(raw) < ctypes.sizeof(UNICODE_STRING):
            return None
        us = UNICODE_STRING.from_buffer_copy(raw)
        if not us.Length or not us.Buffer:
            return None
        text = read(us.Buffer, us.Length)
        if not text:
            return None
        return text.decode("utf-16-le", "replace")
    except (OSError, ValueError):
        return None
    finally:
        kernel32.CloseHandle(h)


def _cim_command_lines(pids):
    """The fallback. One PowerShell process answers for the whole remainder, never one
    per pid."""
    import json
    import subprocess
    want = ",".join(str(p) for p in pids)
    script = ("Get-CimInstance Win32_Process | "
              "Where-Object { @(%s) -contains $_.ProcessId } | "
              "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress" % want)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout.strip() or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    if isinstance(data, dict):
        data = [data]
    return {int(d["ProcessId"]): (d.get("CommandLine") or "")
            for d in data if d.get("ProcessId")}


def command_lines(pids):
    """`pid -> command line` for the pids given, through the PEB, with CIM for whatever the
    PEB route could not read."""
    out, missing = {}, []
    for pid in pids:
        line = _peb_command_line(pid)
        if line:
            out[pid] = line
        else:
            missing.append(pid)
    if missing:
        out.update(_cim_command_lines(missing))
    return out


def taskkill(pid, force=False, tree=True):
    """End the process. `tree` takes its children with it, which is what closes a terminal
    tab whose shell is ended. Returns the exit code of `taskkill`."""
    import subprocess
    argv = ["taskkill", "/PID", str(pid)]
    if tree:
        argv.append("/T")
    if force:
        argv.append("/F")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=30).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def terminate(pid, code=0):
    """End the process alone with `code` as its exit code. A shell ended with 0 closes its
    Windows Terminal tab under the default `closeOnExit`; `taskkill /F` ends it with 1, and
    the tab stays. Returns True when the process was ended."""
    h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        return False
    try:
        return bool(kernel32.TerminateProcess(h, code))
    finally:
        kernel32.CloseHandle(h)


def wait_exit(pid, seconds):
    """Wait up to `seconds` for the process to exit. True when it is gone."""
    h = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return True
    try:
        return kernel32.WaitForSingleObject(h, int(seconds * 1000)) == 0
    finally:
        kernel32.CloseHandle(h)


# -- windows ----------------------------------------------------------------

def windows(class_name=None):
    """Every visible top-level window as `(hwnd, class, title, pid)`, held to one class
    when a class is given."""
    found = []
    cls = ctypes.create_unicode_buffer(256)
    txt = ctypes.create_unicode_buffer(1024)

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        user32.GetClassNameW(hwnd, cls, 256)
        if class_name and cls.value != class_name:
            return True
        user32.GetWindowTextW(hwnd, txt, 1024)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((int(hwnd), cls.value, txt.value, int(pid.value)))
        return True

    user32.EnumWindows(ENUMPROC(visit), 0)
    return found


def find_terminal_window(match):
    """The handle of the terminal window whose title holds `match`, or `None`. Looks at
    Windows Terminal windows first, then classic consoles."""
    for cls in (WT_WINDOW_CLASS, CONSOLE_WINDOW_CLASS):
        for hwnd, _c, title, _pid in windows(cls):
            if match and match in title:
                return hwnd
    return None


user32.SwitchToThisWindow.argtypes = [wt.HWND, wt.BOOL]
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wt.DWORD, ctypes.c_void_p]

VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


def raise_window(hwnd):
    """Bring the window to the front and give it the keyboard. Returns True when the
    window really is the foreground one afterwards.

    Windows refuses the foreground to a process that does not already hold it, and
    flashes the taskbar button instead. Three routes get past that, and they are tried in
    order because each one fails on a different machine: attaching to the foreground
    window's input queue, a keystroke that counts as input to this process, and the call
    Alt+Tab itself uses. None of them can take the foreground from a window of a higher
    integrity level, and the lock screen is one, so a locked machine always answers
    False."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)
    user32.BringWindowToTop(hwnd)

    def landed():
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)

    fg = user32.GetForegroundWindow()
    mine = kernel32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other) and other != mine and bool(
        user32.AttachThreadInput(mine, other, True))
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(mine, other, False)
    if landed():
        return True

    user32.keybd_event(VK_MENU, 0, 0, None)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, None)
    user32.SetForegroundWindow(hwnd)
    if landed():
        return True

    user32.SwitchToThisWindow(hwnd, True)
    return landed()
