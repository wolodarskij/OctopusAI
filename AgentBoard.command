#!/bin/sh
# Open the Agent Board of every registered project: macOS's AgentBoard.bat. Run
# AgentBoard.command. Ctrl+C in this window stops it.
# `./AgentBoard.command --project <name|path>` opens the page on that project. Run from an empty
# folder, or given a folder in no project, it installs Octopus AI there first; with no project
# registered and none given, it asks for the folder.
# When Python 3.10 or newer or git is missing, it runs tools/setup-mac.sh first.
printf '\033]0;Octopus AI - Agent Board\007'
HERE="$(cd "$(dirname "$0")" && pwd)" || exit 1
# A window the Finder opens has the login shell's PATH; Homebrew's, and its `python`, are added
# when they are missing.
brew_path() {
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$b" ]; then
      eval "$("$b" shellenv)"
      d="$("$b" --prefix python 2>/dev/null)/libexec/bin"
      case ":$PATH:" in *":$d:"*) ;; *) [ -d "$d" ] && PATH="$d:$PATH" ;; esac
      break
    fi
  done
  export PATH
}
pick_python() {
  if command -v python >/dev/null 2>&1; then PY=python; else PY=python3; fi
}
ready() {
  "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1 &&
    git --version >/dev/null 2>&1
}
brew_path
pick_python
if ! ready; then
  echo "Octopus AI needs Python 3.10 or newer and git. The setup installs what is missing."
  /bin/bash "$HERE/tools/setup-mac.sh" --yes
  brew_path
  pick_python
  if ! ready; then
    echo
    echo "Python or git is still missing. Read the setup's lines above, then open AgentBoard.command again."
    exit 1
  fi
fi
exec "$PY" "$HERE/octopus.py" board "$@"
