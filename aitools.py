"""Runs `octopus.py` with the same arguments."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "octopus.py"), run_name="__main__")
