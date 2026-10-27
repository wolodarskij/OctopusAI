#!/bin/bash
# Set up a Mac to run Octopus AI. Run it in Terminal from the checkout:
#
#     tools/setup-mac.sh          ask before each install
#     tools/setup-mac.sh --yes    install whatever is missing without asking
#
# What it makes sure of, installing only what is missing:
#   1. Homebrew                 the package manager the rest comes from (asks for your password once)
#   2. python on the PATH       Homebrew's Python 3, and the plain `python` command the hooks,
#                               the briefs and AgentBoard.command run
#   3. tmux                     the terminal that holds every launched session: the board reads
#                               its screen and types into it
#   4. Claude Code              the `claude` CLI a launch starts
# Then it runs the checks: each tool's version, and `python octopus.py --help`.
# Last, it asks for the default AI provider and model and checks them (`python octopus.py setup`).
# With --yes it does not ask: the board asks in its page.
#
# It changes nothing else, and it is safe to run again: a step that is done says so and moves on.

set -u
YES=0
[ "${1:-}" = "--yes" ] && YES=1
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="$HOME/.zprofile"
MARK="# Octopus AI: Homebrew and its python"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  ok  %s\n' "$*"; }
ask()  {
  [ "$YES" = 1 ] && return 0
  printf '  %s [Y/n] ' "$1"; read -r a
  case "$a" in [nN]*) return 1 ;; *) return 0 ;; esac
}

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This script sets up macOS. On Linux install python3, tmux and Claude Code with your package manager."
  exit 1
fi

# -- 1. Homebrew ---------------------------------------------------------------
say "1. Homebrew"
BREW=""
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && BREW="$b" && break; done
if [ -z "$BREW" ]; then
  if ask "Homebrew is not installed. Install it now (from brew.sh; it asks for your password)?"; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || exit 1
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && BREW="$b" && break; done
  fi
  [ -z "$BREW" ] && { echo "  Homebrew is needed for the rest. See https://brew.sh"; exit 1; }
fi
eval "$("$BREW" shellenv)"
ok "$("$BREW" --version | head -1)"

# -- 2. Python, and the `python` command -----------------------------------------
say "2. Python"
if [ -z "$("$BREW" list --versions python 2>/dev/null)" ]; then
  if ask "Install Python 3 with Homebrew?"; then "$BREW" install python || exit 1; fi
fi
PYBIN="$("$BREW" --prefix python 2>/dev/null)/libexec/bin"
[ -x "$PYBIN/python" ] || { echo "  Homebrew's python is missing: brew install python"; exit 1; }
# Homebrew's `python` and `pip` live in libexec/bin; the login shell puts it, and Homebrew
# itself, first on the PATH.
if ! grep -qF "$MARK" "$PROFILE" 2>/dev/null; then
  if ask "Put Homebrew and its \`python\` on the PATH of every new terminal ($PROFILE)?"; then
    {
      echo ""
      echo "$MARK"
      echo "eval \"\$($BREW shellenv)\""
      echo "export PATH=\"\$(brew --prefix python)/libexec/bin:\$PATH\""
    } >> "$PROFILE"
    ok "added to $PROFILE"
  fi
else
  ok "$PROFILE already sets the PATH"
fi
export PATH="$PYBIN:$PATH"
ok "$(python --version) at $(command -v python)"

# -- 3. tmux --------------------------------------------------------------------
say "3. tmux"
if ! command -v tmux >/dev/null 2>&1; then
  if ask "Install tmux with Homebrew?"; then "$BREW" install tmux || exit 1; fi
fi
if command -v tmux >/dev/null 2>&1; then ok "$(tmux -V)"; else echo "  missing: own terminals and the board's terminal window need it"; fi

# -- 4. Claude Code -------------------------------------------------------------
say "4. Claude Code"
for d in "$HOME/.local/bin"; do case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH" ;; esac; done
if ! command -v claude >/dev/null 2>&1; then
  if ask "Install the Claude Code CLI (the native installer from claude.ai)?"; then
    curl -fsSL https://claude.ai/install.sh | bash || exit 1
  fi
fi
if command -v claude >/dev/null 2>&1; then
  ok "claude $(claude --version 2>/dev/null | head -1)"
  if ! grep -q '\.local/bin' "$PROFILE" 2>/dev/null && [ -x "$HOME/.local/bin/claude" ]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$PROFILE"
    ok "added ~/.local/bin to $PROFILE"
  fi
else
  echo "  missing: a launch starts \`claude\`. See https://docs.claude.com/en/docs/claude-code"
fi

# -- the checks -----------------------------------------------------------------
say "Checks"
cd "$HERE" || exit 1
python octopus.py --help >/dev/null && ok "python octopus.py runs"
chmod +x AgentBoard.command 2>/dev/null && ok "AgentBoard.command can run"

# -- the default AI provider and model ------------------------------------------
if [ "$YES" != 1 ]; then
  say "Default AI provider and model"
  python octopus.py setup
fi
echo
echo "Done. Open a new Terminal window (it reads $PROFILE), then:"
echo "  python octopus.py board        or run AgentBoard.command"
