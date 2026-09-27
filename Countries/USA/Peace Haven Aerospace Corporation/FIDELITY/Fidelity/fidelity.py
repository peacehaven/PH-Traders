"""Command-line entry point for Fidelity holdings."""
from __future__ import annotations

import _bootstrap  # re-runs this script inside .venv

import sys

from broker_positions import main


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["all", "holdings"]):
        raise SystemExit("Usage: python fidelity.py all holdings")
    raise SystemExit(main(["--config", "config.fidelity.json"]))
