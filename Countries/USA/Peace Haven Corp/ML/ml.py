"""Convenience entry point for displaying one or multiple holdings accounts."""
from __future__ import annotations

import _bootstrap  # re-runs this script inside .venv

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv

from broker_positions import main, print_holdings_table


def run_accounts() -> int:
    load_dotenv()
    accounts = []
    for number in range(1, 7):
        username = os.getenv(f"BROKER_ACCOUNT_{number}_USERNAME")
        password = os.getenv(f"BROKER_ACCOUNT_{number}_PASSWORD")
        name = os.getenv(f"BROKER_ACCOUNT_{number}_NAME", f"Account {number}")
        if username or password:
            if not username or not password:
                print(f"Missing username or password for account {number}.", file=sys.stderr)
                return 2
            accounts.append((number, name, username, password))

    if not accounts:
        return main(["--out", "positions.json"])

    combined = []
    failures = []
    with TemporaryDirectory() as temp_dir:
        for number, name, username, password in accounts:
            env = os.environ.copy()
            env["BROKER_USERNAME"] = username
            env["BROKER_PASSWORD"] = password
            output = Path(temp_dir) / f"account-{number}.json"
            profile = Path(f".chrome-profile-{number}").resolve()
            command = [sys.executable, "broker_positions.py", "--out", str(output),
                       "--profile", str(profile), "--allow-empty"]
            try:
                result = subprocess.run(command, env=env, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, check=False)
                if result.returncode:
                    result = subprocess.run(command, env=env, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, check=False)
            except KeyboardInterrupt:
                print(f"\nInterrupted while processing {name}; skipping remaining accounts.",
                      file=sys.stderr)
                failures.append((number, name))
                break
            if result.returncode:
                failures.append((number, name))
                continue
            payload = json.loads(output.read_text(encoding="utf-8"))
            payload["account"] = name
            combined.append(payload)
            print(f"\n=== {name} ===")
            print_holdings_table(
                payload["positions"],
                payload.get("balances", []),
                payload.get("account_total"),
            )

    positions = []
    balances = []
    total = 0.0
    for payload in combined:
        for position in payload["positions"]:
            positions.append({"account": payload["account"], **position})
        for balance in payload.get("balances", []):
            balances.append({"account": payload["account"], **balance})
        if payload.get("account_total") is not None:
            total += payload["account_total"]

    aggregate = {
        "account": "All Accounts",
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "accounts": combined,
        "positions": positions,
        "balances": balances,
        "account_total": round(total, 2),
        "failed_accounts": [{"number": number, "name": name} for number, name in failures],
    }
    Path("positions.json").write_text(json.dumps(aggregate, indent=2), encoding="utf-8")
    if failures:
        print("Failed accounts: " + ", ".join(name for _, name in failures), file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["all", "holdings"]):
        raise SystemExit("Usage: python ml.py all holdings")
    raise SystemExit(run_accounts())
