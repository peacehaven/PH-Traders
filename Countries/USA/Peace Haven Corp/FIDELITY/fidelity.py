"""Launch the Fidelity project from the workspace parent directory."""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


project_dir = Path(__file__).resolve().parent / "Fidelity"
os.chdir(project_dir)
sys.path.insert(0, str(project_dir))
runpy.run_path(str(project_dir / "fidelity.py"), run_name="__main__")
