"""The ticket tree of this project, served by Octopus AI.

    python .octopus/todo.py <command> ...      the same commands as `octopus todo`
    python .octopus/todo.py serve [--page assets] ...   the board on this project, as `octopus board`
    python .octopus/todo.py assets list | check ...     the asset files (the browser-game pack)

Octopus AI lives in {{OCTOPUS}}. This file only points the engine at this project.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, "{{OCTOPUS}}/core/engine")

if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        import todo_server  # noqa: E402
        sys.exit(todo_server.main(["--project", str(ROOT)] + sys.argv[2:]))
    if sys.argv[1:2] == ["assets"]:
        sys.path.insert(0, "{{OCTOPUS}}/packs/browser-game/tools")
        import assets  # noqa: E402
        sys.exit(assets.main(["--project", str(ROOT)] + sys.argv[2:]))
    import todo  # noqa: E402
    sys.exit(todo.main(["--project", str(ROOT)] + sys.argv[1:]))
