"""Convert the Fidelity positions CSV you downloaded into positions.fidelity.json.

Usage:
    python fidelity_csv.py                  # newest Portfolio_Positions CSV it can find
    python fidelity_csv.py path/to/file.csv # a specific file

Where it looks, newest first: downloads-fidelity/ in this folder, then your Downloads
folder. Sign in to Fidelity in your normal browser, open Positions, click Download,
then run this.
"""
from __future__ import annotations

try:  # re-runs inside .venv when _bootstrap.py is next to this file
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    pass

import sys
import time
from pathlib import Path

from broker_positions import main

FOLDER = Path(__file__).resolve().parent
SEARCH_DIRS = [FOLDER / "downloads-fidelity", FOLDER, Path.home() / "Downloads"]
PATTERNS = ["Portfolio_Positions*.csv", "*Positions*.csv", "*.csv"]


def newest_csv() -> Path | None:
    for pattern in PATTERNS:
        found = [f for directory in SEARCH_DIRS if directory.is_dir()
                 for f in directory.glob(pattern)]
        if found:
            return max(found, key=lambda f: f.stat().st_mtime)
    return None


if __name__ == "__main__":
    if len(sys.argv) > 1:
        csv_path = Path(sys.argv[1]).expanduser()
        if not csv_path.is_file():
            raise SystemExit(f"No such file: {csv_path}")
    else:
        csv_path = newest_csv()
        if csv_path is None:
            raise SystemExit(
                "No CSV found. Sign in to Fidelity, open Positions, click Download, "
                "then run this again.\nLooked in: "
                + ", ".join(str(d) for d in SEARCH_DIRS))

    age_hours = (time.time() - csv_path.stat().st_mtime) / 3600
    print(f"Using {csv_path} (downloaded {age_hours:.1f} hours ago)", file=sys.stderr)
    if age_hours > 12:
        print("WARNING: that file is old. Download a fresh one before trading on it.",
              file=sys.stderr)
    raise SystemExit(main(["--config", "config.fidelity.json",
                           "--csv", str(csv_path),
                           "--out", "positions.fidelity.json"]))
