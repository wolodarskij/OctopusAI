"""posixterm.py - the terminals of launched sessions on macOS and Linux.

On Windows a session is a `cmd /c` line in a Windows Terminal window, or in a pseudoconsole
`session_pty.py` holds; `console_io.py` reads its screen and types into it. Here the same line
runs under `/bin/sh -c`, inside a tmux session of Octopus AI's own tmux server
(`tmux -L octopus`, configured by `tmux.conf` beside this file):

- an external terminal (`wt` or `cmd` in the config) is that tmux session with a Terminal.app
  window attached to it (on Linux, `x-terminal-emulator`);
- an own terminal (`own`) is the tmux session with no window: the board shows its screen.

tmux is what reads the screen (`capture-pane`), types into it (`send-keys`) and keeps the
scrolled output, so the board's terminal window works for both. Closing the Terminal window
leaves the session running in tmux, and the board still shows it. Without tmux a session still
starts, in a Terminal window of its own, and the board finds it, raises its window and reads
its state from the hooks, but cannot show its screen or type into it.

The shell's command line carries the whole launch line, `/bin/sh -c cd "<root>" && ...`, so
the session scan reads the session id off it as it does off `cmd.exe`'s on Windows.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONF = HERE / "tmux.conf"
SOCKET = "octopus"
DARWIN = sys.platform == "darwin"

# The pause between typed text and the keys after it, as `console_io.SEND_PAUSE`: an app
# that reads an Enter together with the text takes it as part of a paste.
SEND_PAUSE = 0.4

# Where tmux lives when the PATH of the caller does not say: a board started from Finder or
# by launchd gets a short PATH.
_TMUX_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin", "/usr/bin")


# ── quoting ─────────────────────────────────────────────────────────────────

_SAFE = re.compile(r"^[A-Za-z0-9_@%+=:,./-]+$")


def quote(arg):
    """`arg` as one word of a `/bin/sh` line. A word that needs quoting goes in double
    quotes, the way `todo._shell_line` quotes a word for `cmd`: the session scan reads
    `--remote-control "<name>"` off the line in either form."""
    arg = str(arg)
    if arg and _SAFE.match(arg):
        return arg
    return '"%s"' % re.sub(r'([\\"$`])', r"\\\1", arg)


def join(argv):
    return " ".join(quote(a) for a in argv)


def export(variables):
    """`export K=V ... && ` for `variables`, or "" for none."""
    if not variables:
        return ""
    return "export %s && " % " ".join("%s=%s" % (k, quote(v)) for k, v in variables.items())


def shell_line(argv, title, root, preset, marks, reset):
    """The `/bin/sh -c` line that runs `argv` as a session of the project at `root`. The
    same steps as the `cmd` line on Windows: move to the project, set `preset` and `marks`,
    name the window, run the agent, then `reset` the terminal modes whatever its exit."""
    return ("cd %s && %sprintf '\\033]0;%%s\\007' %s && %s%s; %s"
            % (quote(root), export(preset), quote(title), export(marks), join(argv),
               join(reset)))


# A launch line as the session scan sees it on the shell's command line. A verify launch
# sets its flag before the rest.
LAUNCH_LINE = re.compile(r"^\S*\bsh -c (?:export [^\n]*? && )?cd ")


# ── tmux ────────────────────────────────────────────────────────────────────

def tmux_path():
    """The tmux executable, or None when this machine has none."""
    found = shutil.which("tmux")
    if found:
        return found
    for d in _TMUX_DIRS:
        p = os.path.join(d, "tmux")
        if os.access(p, os.X_OK):
            return p
    return None


def _env():
    # A board run from inside the owner's own tmux must not talk to that server.
    return {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}


def tmux(*args, check=True):
    """Run one tmux command on the Octopus AI server and return its output."""
    exe = tmux_path()
    if not exe:
        raise OSError("tmux is not installed; on macOS: brew install tmux")
    r = subprocess.run([exe, "-L", SOCKET, "-f", str(CONF)] + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=_env(), timeout=20)
    if check and r.returncode != 0:
        raise OSError("tmux %s: %s" % (args[0], (r.stderr or r.stdout).strip()))
    return r.stdout


def session_name(sid):
    """The tmux session of one launch: its Claude session id, which is unique."""
    return "octopus-%s" % sid


def panes():
    """`shell pid -> {"pane", "session", "attached"}` for every pane of the Octopus AI
    tmux server. Empty when tmux or its server is not there."""
    if not tmux_path():
        return {}
    try:
        out = tmux("list-panes", "-a", "-F",
                   "#{pane_pid} #{pane_id} #{session_attached} #{session_name}", check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in out.splitlines():
        parts = line.split(" ", 3)
        if len(parts) == 4 and parts[0].isdigit():
            found[int(parts[0])] = {"pane": parts[1], "attached": parts[2] not in ("", "0"),
                                    "session": parts[3]}
    return found


def pane_of(pid):
    """The tmux pane whose shell is `pid`, or None."""
    return panes().get(int(pid or 0))


def _pane(pid):
    info = pane_of(pid)
    if info is None:
        raise OSError("the session's shell (pid %s) runs in no tmux pane of Octopus AI%s"
                      % (pid, "" if tmux_path() else "; tmux is not installed"))
    return info["pane"]


# ── launch ──────────────────────────────────────────────────────────────────

def launch(line, title, root, name, scripts_dir, window=True):
    """Start `line` in the tmux session `name`, and with `window` open a terminal window on
    it. Without tmux, a window runs the line itself, and a windowless launch is refused.
    Returns None, or the reason nothing started."""
    if tmux_path():
        tmux("kill-session", "-t", "=" + name, check=False)   # a leftover of this session id
        tmux("new-session", "-d", "-s", name, "-x", "200", "-y", "50", "-c", str(root),
             "/bin/sh", "-c", line)
        if window:
            return attach_window(name, title, scripts_dir)
        return None
    if not window:
        return ("an own terminal needs tmux, which reads the session's screen; on macOS: "
                "brew install tmux")
    return open_window("/bin/sh -c %s" % _sq(line), title,
                       Path(scripts_dir) / ("%s.command" % name))


def _sq(text):
    """`text` in single quotes, for a script line that runs it as one argument."""
    return "'%s'" % text.replace("'", "'\\''")


def attach_window(name, title, scripts_dir):
    """Open a terminal window attached to the tmux session `name`. Returns None, or the
    reason no window opened."""
    attach = "%s -L %s -f %s attach-session -t %s" % (
        quote(tmux_path()), SOCKET, quote(CONF), quote("=" + name))
    return open_window(attach, title, Path(scripts_dir) / ("%s.command" % name), session=name)


def open_window(command, title, script, session=None):
    """Open a terminal window of its own that runs `command`. On macOS that is a Terminal.app
    window: `open` runs the `.command` script `script`, so no permission to control Terminal
    is asked for. With `session`, the tmux session `command` attaches to, the window closes
    once that session has ended. Returns None, or the reason no window opened."""
    script = Path(script)
    script.parent.mkdir(parents=True, exist_ok=True)
    body = ["#!/bin/sh",
            "# Written by Octopus AI for one session window; the next launch writes it again.",
            "printf '\\033]0;%%s\\007' %s" % _sq(title),
            "TTY=$(tty)",
            command]
    if session and DARWIN:
        # The tmux session ended, rather than the owner detaching: the window closes, as a
        # Windows Terminal tab closes when its shell exits.
        body += ["if ! %s -L %s has-session -t %s 2>/dev/null; then"
                 % (quote(tmux_path()), SOCKET, _sq("=" + session)),
                 "  ( sleep 0.5; osascript -e \"tell application \\\"Terminal\\\" to close "
                 "(every window whose tty of tab 1 is \\\"$TTY\\\")\" ) >/dev/null 2>&1 &",
                 "fi"]
    body.append("exit 0")
    script.write_text("\n".join(body) + "\n", encoding="utf-8")
    script.chmod(0o755)
    try:
        if DARWIN:
            subprocess.Popen(["open", "-a", "Terminal", str(script)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return None
        for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            exe = shutil.which(term)
            if exe:
                argv = [exe, "--", "/bin/sh", str(script)] if term == "gnome-terminal" \
                    else [exe, "-e", "/bin/sh", str(script)]
                subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return None
    except OSError as exc:
        return str(exc)
    return "no terminal program found to open a window in; the script is %s" % script


# ── the screen, the keys, the log ───────────────────────────────────────────

def screen(pid):
    """The screen of the session whose shell is `pid`, as `console_io.Console.screen` gives
    it: `cols`, `rows`, `cursor` `[x, y]`, `window` `[top, bottom]` and `lines`."""
    pane = _pane(pid)
    out = tmux("display-message", "-p", "-t", pane,
               "#{pane_width} #{pane_height} #{cursor_x} #{cursor_y}",
               ";", "capture-pane", "-p", "-t", pane)
    head, _sep, body = out.partition("\n")
    cols, rows, x, y = (int(v) for v in head.split())
    lines = [ln.rstrip() for ln in body.split("\n")][:rows]
    lines += [""] * (rows - len(lines))
    return {"cols": cols, "rows": rows, "cursor": [x, y], "window": [0, rows - 1],
            "lines": lines}


# The board's key names (`console_io.KEYS`) as tmux names them.
_KEYS = {"Enter": "Enter", "Esc": "Escape", "Tab": "Tab", "Backspace": "BSpace",
         "Delete": "DC", "Space": "Space", "Up": "Up", "Down": "Down", "Left": "Left",
         "Right": "Right", "Home": "Home", "End": "End", "PageUp": "PPage",
         "PageDown": "NPage"}


def tmux_key(name):
    """The tmux key for a board key name: `Enter`, `Shift+Tab`, `Ctrl+C`, `Alt+M`,
    `Ctrl+Left`, or one character. Raises ValueError for a name it does not know."""
    parts = name.strip().split("+")
    base, mods = parts[-1], [m.lower() for m in parts[:-1]]
    if any(m not in ("ctrl", "shift", "alt") for m in mods):
        raise ValueError("unknown modifier in %r" % name)
    if base == "Tab" and mods == ["shift"]:
        return "BTab"
    if base in _KEYS:
        key = _KEYS[base]
    elif len(base) == 1:
        key = base.lower() if "ctrl" in mods else base
        if "shift" in mods and base.isalpha():
            key, mods = base.upper(), [m for m in mods if m != "shift"]
    else:
        raise ValueError("unknown key %r" % name)
    prefix = "".join({"ctrl": "C-", "alt": "M-", "shift": "S-"}[m] for m in mods)
    return prefix + key


def send(pid, text="", keys=()):
    """Type `text`, then the named `keys`, into the session whose shell is `pid`. Returns
    how many characters and keys went in."""
    pane = _pane(pid)
    names = [tmux_key(k) for k in keys if k]
    if text:
        tmux("send-keys", "-t", pane, "-l", "--", text)
    if text and names:
        time.sleep(SEND_PAUSE)
    if names:
        tmux("send-keys", "-t", pane, *names)
    return len(text) + len(names)


def log(pid, offset=0):
    """The scrolled output of the session, as the pseudoconsole host gives it on Windows:
    `text` from `offset`, the new `offset` and the `size`. tmux keeps the last 50000 lines;
    an offset past the end, after older lines left, starts again at 0."""
    pane = _pane(pid)
    full = tmux("capture-pane", "-p", "-J", "-S", "-", "-E", "-", "-t", pane).rstrip("\n")
    offset = max(0, int(offset or 0))
    if offset > len(full):
        offset = 0
    return {"text": full[offset:], "offset": len(full), "size": len(full)}


# ── the window ──────────────────────────────────────────────────────────────

def _tty_of(pid):
    try:
        r = subprocess.run(["ps", "-o", "tty=", "-p", str(int(pid))], capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    tty = r.stdout.strip()
    return "" if not tty or tty.startswith("?") else "/dev/" + tty


_RAISE = '''
on run argv
  set want to item 1 of argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        if tty of t is want then
          set selected of t to true
          set index of w to 1
          activate
          return "yes"
        end if
      end repeat
    end repeat
  end tell
  return "no"
end run
'''


def focus(pid, title="", scripts_dir=None):
    """Bring the terminal window of the session whose shell is `pid` to the front. A tmux
    session whose window was closed gets a new one, attached to it. Returns True when a
    window shows the session."""
    if not DARWIN:
        return False
    info = pane_of(pid)
    ttys = []
    if info is not None:
        try:
            ttys = tmux("list-clients", "-t", "=" + info["session"], "-F",
                        "#{client_tty}", check=False).split()
        except OSError:
            ttys = []
        if not ttys and scripts_dir is not None:
            return attach_window(info["session"], title or info["session"], scripts_dir) is None
    else:
        tty = _tty_of(pid)
        ttys = [tty] if tty else []
    for tty in ttys:
        try:
            r = subprocess.run(["osascript", "-e", _RAISE, tty], capture_output=True,
                               text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return False
        if r.stdout.strip() == "yes":
            return True
    return False


# ── the rest of the machine ─────────────────────────────────────────────────

def open_file(path):
    """Open a file in the program the machine uses for it."""
    subprocess.Popen(["open" if DARWIN else "xdg-open", str(path)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def ask_folder(start="", prompt="Choose the project directory"):
    """A native folder dialog. Returns the chosen directory, "" when the owner cancels, or
    None when this machine has no dialog to show (the caller falls back to Tk)."""
    if not DARWIN:
        return None
    script = 'POSIX path of (choose folder with prompt "%s"' % prompt.replace('"', "'")
    if start:
        script += ' default location (POSIX file "%s")' % start.replace('"', '\\"')
    script += ")"
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        if "-128" in (r.stderr or ""):                  # the owner pressed Cancel
            return ""
        raise OSError("the folder dialog failed: %s" % (r.stderr or "").strip())
    return r.stdout.strip()
