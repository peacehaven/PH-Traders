"""Fidelity holdings -> terminal table + JSON.

    python fidelity.py all holdings    open Chrome, sign in, read every account
    python fidelity.py --csv FILE      read a positions CSV you downloaded instead
    python fidelity.py --json          also print the JSON

Your stored login is typed for you (--save-login once, --no-fill to skip). The password
lives in Windows Credential Manager, never in this folder, and the Chrome window stays
open afterwards so later runs need nothing from you.
"""
from __future__ import annotations

try:  # re-runs inside .venv when _bootstrap.py is next to this file
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    pass

import argparse
import json
import logging
import sys
from pathlib import Path

import positions as parser
import time

from chrome_session import (
    ChromeError,
    attach,
    delay_for,
    ensure_chrome,
    forget_credentials,
    is_signed_in,
    load_credentials,
    read_grid_snapshots,
    save_credentials,
    type_credentials,
    wait_for_sign_in,
    wait_until_signed_in,
)

FOLDER = Path(__file__).resolve().parent
log = logging.getLogger("fidelity")


def load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Missing {path.name}. It should sit next to fidelity.py.")
    return json.loads(path.read_text(encoding="utf-8"))


def from_browser(config: dict, fill: bool) -> tuple[list[str], list[list[str]]]:
    """Sign-in happens in an ordinary Chrome with nothing attached; we attach afterwards."""
    chrome = config.get("chrome", {})
    signed_in = config.get("signed_in", {})
    timeout = config.get("sign_in_timeout_seconds", 300)

    # Open on the plain login page: going straight to a protected page adds an
    # AuthRedUrl parameter to the sign-in URL, which is one more thing that can upset it.
    start_url = chrome.get("start_url") or config.get("login_url") or config["positions_url"]
    port = ensure_chrome(chrome, FOLDER, start_url)

    username, password = (None, None)
    if fill:
        username, password = load_credentials(config.get("keyring_service",
                                                         "fidelity-holdings"))
        if not (username and password):
            log.info("No stored login yet - run 'python fidelity.py --save-login' once to "
                     "have it typed for you. Signing in is up to you this time.")

    if username and password:
        driver = attach(port, "fidelity.com")
        if not is_signed_in(driver, signed_in):
            # Make sure we are on the sign-in page: a Chrome left open from earlier could
            # be sitting anywhere, and the form would never be found.
            if "signin" not in driver.current_url and "login" not in driver.current_url:
                log.info("Opening the sign-in page.")
                driver.get(config.get("login_url", start_url))
            if type_credentials(driver, config.get("sign_in", {}), username, password,
                                config):
                log.info("Typed your stored login.")
            wait_until_signed_in(driver, signed_in, timeout)
    else:
        wait_for_sign_in(port, signed_in.get("url_contains", "/ftgw/digital/"), timeout,
                         config)
        driver = attach(port, "fidelity.com")

    if config["positions_url"].split("#")[0] not in driver.current_url:
        log.info("Opening the positions page.")
        driver.get(config["positions_url"])
        time.sleep(delay_for(config, "after_page_load_seconds", 2.0))

    snapshots = read_grid_snapshots(driver, signed_in.get("grid_selector", ".ag-root, table"),
                                    config.get("grid_timeout_seconds", 90), config)
    log.info("Chrome stays open; the next run can read it again without signing in.")
    return parser.read_snapshots(snapshots)


def check_credentials(service: str) -> int:
    """Report what would be used to sign in, without showing the password."""
    import os

    if os.getenv("FIDELITY_USERNAME") and os.getenv("FIDELITY_PASSWORD"):
        print("Using the FIDELITY_USERNAME / FIDELITY_PASSWORD environment variables.")
        return 0
    try:
        import keyring
        print(f"keyring backend: {keyring.get_keyring().__class__.__name__}")
    except ImportError:
        print("keyring is not installed in this environment, so nothing can be stored.\n"
              "Fix it with:  .venv/Scripts/python.exe -m pip install keyring")
        return 1
    except Exception as error:
        print(f"keyring is installed but unusable here: {error}")
        return 1

    username, password = load_credentials(service)
    if username and password:
        hidden = username[:2] + "*" * max(len(username) - 2, 3)
        print(f"Stored login found under '{service}': {hidden}\n"
              "It will be typed for you on the next run.")
        return 0
    print(f"No stored login under '{service}'.\n"
          "Save one with:  python fidelity.py --save-login")
    return 1


def report(config: dict, args, headers, rows) -> int:
    """Shared output path: group, print, save."""
    accounts, grand_total = parser.to_accounts_from_columns(headers, rows, config)
    holdings = [h for account in accounts for h in account["positions"]]
    if not accounts:
        holdings = parser.to_positions(headers, rows, config["columns"],
                                       config.get("skip_row_pattern"))
    if not holdings:
        log.error("No holdings were read, so nothing was written.")
        return 1

    account = config.get("account_name", "Fidelity Account")
    if accounts:
        payload = parser.build_account_payload(account, accounts, grand_total)
        print(parser.render_accounts(accounts, grand_total))
    else:
        payload = parser.build_payload(account, holdings)
        print(parser.render_table(holdings, account))

    destination = Path(args.out or config.get("output_file", "positions.fidelity.json"))
    if not destination.is_absolute():
        destination = FOLDER / destination
    parser.write_json(destination, payload)
    print(f"\nSaved {len(holdings)} holdings to {destination}")
    return 0


def watch_for_csv(config: dict, args) -> int:
    """Convert every positions CSV as it arrives, so downloading is the only manual step."""
    folders = config.get("csv_watch_dirs", ["~/Downloads"])
    patterns = config.get("csv_patterns", ["Portfolio_Positions*.csv"])
    poll = float(config.get("watch_poll_seconds", 3))
    seen = 0.0

    latest = parser.find_csv(folders, patterns)
    if latest:
        seen = latest.stat().st_mtime
        print(f"Ignoring the CSV already there ({latest.name}). Download a fresh one.")
    print("Watching: " + ", ".join(str(Path(f).expanduser()) for f in folders))
    print("Sign in to Fidelity in your normal browser, open Positions with All accounts "
          "selected, and click Download. Ctrl+C to stop.\n")

    try:
        while True:
            found = parser.find_csv(folders, patterns, newer_than=seen)
            if found:
                time.sleep(1)  # let the download finish writing
                seen = found.stat().st_mtime
                print(f"\nNew file: {found}")
                try:
                    headers, rows = parser.read_csv_export(found)
                    report(config, args, headers, rows)
                except parser.ParseError as problem:
                    log.error("%s", problem)
            time.sleep(poll)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


def main(argv: list[str] | None = None) -> int:
    arguments = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    arguments.add_argument("words", nargs="*", metavar="all holdings",
                           help="optional: the words 'all holdings' are accepted and ignored")
    arguments.add_argument("--csv", metavar="FILE",
                           help="read a downloaded positions CSV instead of the page")
    arguments.add_argument("--out", metavar="FILE", help="where to write the JSON")
    arguments.add_argument("--json", action="store_true", help="also print the JSON")
    arguments.add_argument("--config", default=str(FOLDER / "config.json"))
    arguments.add_argument("--watch", action="store_true",
                           help="keep running: convert each positions CSV as you download it")
    arguments.add_argument("--fill", action="store_true",
                           help="(default) type your stored login for you")
    arguments.add_argument("--no-fill", action="store_true",
                           help="do not type anything; sign in yourself")
    arguments.add_argument("--save-login", action="store_true",
                           help="store your login in the OS credential manager and exit")
    arguments.add_argument("--check-login", action="store_true",
                           help="say whether a stored login was found, and where")
    arguments.add_argument("--forget-login", action="store_true",
                           help="remove the stored login and exit")
    arguments.add_argument("-v", "--verbose", action="store_true")
    args = arguments.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s  %(message)s", stream=sys.stderr)
    config = load_config(Path(args.config))
    service = config.get("keyring_service", "fidelity-holdings")
    if args.save_login:
        return save_credentials(service)
    if args.forget_login:
        return forget_credentials(service)
    if args.check_login:
        return check_credentials(service)

    if args.words and args.words != ["all", "holdings"]:
        log.warning("Ignoring unexpected words: %s", " ".join(args.words))

    if args.watch:
        return watch_for_csv(config, args)

    try:
        accounts, grand_total = [], None
        if args.csv:
            headers, rows = parser.read_csv_export(Path(args.csv).expanduser())
            accounts, grand_total = parser.to_accounts_from_columns(headers, rows, config)
        else:
            headers, rows = from_browser(config, not args.no_fill)
            accounts, grand_total = parser.to_accounts(headers, rows, config)
        log.debug("Columns: %s", headers)
        holdings = [h for account in accounts for h in account["positions"]]
        if not accounts:  # a CSV, or a page without account headings
            holdings = parser.to_positions(headers, rows, config["columns"],
                                           config.get("skip_row_pattern"))
    except (ChromeError, parser.ParseError) as problem:
        log.error("%s", problem)
        return 1

    if not holdings:
        log.error("No holdings were read, so nothing was written. If the page looked right, "
                  "run with -v and send me the column list.")
        return 1

    account = config.get("account_name", "Fidelity Account")
    if accounts:
        payload = parser.build_account_payload(account, accounts, grand_total)
        print(parser.render_accounts(accounts, grand_total))
    else:
        payload = parser.build_payload(account, holdings)
        print(parser.render_table(holdings, account))

    destination = Path(args.out or config.get("output_file", "positions.fidelity.json"))
    if not destination.is_absolute():
        destination = FOLDER / destination
    parser.write_json(destination, payload)
    print(f"\nSaved {len(holdings)} holdings to {destination}")
    if args.json:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
