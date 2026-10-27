"""The ticket tree of this project, served by Octopus AI.

    python .octopus/todo.py <command> ...      the same commands as `octopus todo`

Octopus AI lives in {{OCTOPUS}}. This file only points the engine at this project.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, "{{OCTOPUS}}/core/engine")
import todo  # noqa: E402

if __name__ == "__main__":
    sys.exit(todo.main(["--project", str(ROOT)] + sys.argv[1:]))
