"""Makes `python ml.py all holdings` work with any Python on the PATH.

Put this file next to ml.py, then add ONE line as the first import of ml.py:

    import _bootstrap  # re-runs this script inside .venv

On startup it switches to the script's own folder, and if the current Python is not
the one in .venv it creates .venv (when missing), installs requirements.txt, and
re-runs the same script with the same arguments using the .venv Python.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_MARKER = "BROKER_BOOTSTRAPPED"  # guards against re-launching forever

_script = Path(sys.argv[0]).resolve()
_folder = _script.parent
os.chdir(_folder)  # config.json, .env and the output file are read from here


def _venv_python(folder: Path) -> Path:
    if os.name == "nt":
        return folder / ".venv" / "Scripts" / "python.exe"
    return folder / ".venv" / "bin" / "python"


def _inside_venv() -> bool:
    # Inside a virtual environment sys.prefix is the .venv folder itself.
    return Path(sys.prefix).resolve() == (_folder / ".venv").resolve()


def _ensure_environment() -> None:
    python = _venv_python(_folder)
    if os.environ.get(_MARKER) == "1" or _inside_venv():
        return  # already running inside .venv

    if not python.exists():
        print(f"Creating virtual environment in {_folder / '.venv'} ...", file=sys.stderr)
        try:
            subprocess.check_call([sys.executable, "-m", "venv", str(_folder / ".venv")])
        except subprocess.CalledProcessError:
            raise SystemExit("Could not create .venv. Install Python 3.10+ from python.org "
                             '(tick "Add python.exe to PATH") and try again.')

    missing = subprocess.run([str(python), "-c", "import selenium, bs4, dotenv"],
                             capture_output=True).returncode != 0
    if missing:
        print("Installing dependencies (first run only) ...", file=sys.stderr)
        requirements = _folder / "requirements.txt"
        install = [str(python), "-m", "pip", "install", "-q"]
        install += (["-r", str(requirements)] if requirements.exists()
                    else ["selenium", "beautifulsoup4", "python-dotenv"])
        try:
            subprocess.check_call(install)
        except subprocess.CalledProcessError:
            raise SystemExit("Dependency installation failed. Check the internet connection.")

    environment = {**os.environ, _MARKER: "1"}
    raise SystemExit(subprocess.call([str(python), str(_script), *sys.argv[1:]], env=environment))


_ensure_environment()
