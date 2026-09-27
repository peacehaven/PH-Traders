#!/usr/bin/env python3
"""
 broker_positions.py - sign in to Fidelity with Selenium, read the positions,
 and write them as JSON for a trading application.

    python broker_positions.py --config config.fidelity.json --dump-tables
    python broker_positions.py --config config.fidelity.json --out positions.fidelity.json

Credentials come from FIDELITY_USERNAME / FIDELITY_PASSWORD (environment or .env).
Fidelity-specific details (URLs, CSS selectors, and column names) live in
config.fidelity.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import socket
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger("broker_positions")


class ParseError(Exception):
    """The page didn't contain a positions table we can trust."""


# --------------------------------------------------------------------------- #
# Parsing: pure functions, no browser needed (covered by test_parser.py)
# --------------------------------------------------------------------------- #

# Header names to look for, best match first. Override any field in the config.
DEFAULT_COLUMN_ALIASES = {
    "symbol": ["symbol", "ticker", "symbol/cusip", "security"],
    "quantity": ["quantity", "qty", "shares", "units"],
    "unrealized_pnl": ["unrealized gain/loss", "unrealized g/l", "unrealized p&l",
                       "total gain/loss", "gain/loss", "g/l", "p&l"],
    "market_value": ["market value", "mkt value", "current value", "value"],
}
REQUIRED_FIELDS = ("symbol", "quantity", "market_value")
# A loose "contains" match must not pick columns like "Day's Gain/Loss" or "Gain/Loss (%)".
EXCLUDE_IN_LOOSE_MATCH = ("%", "day", "today", "change", "cost", "price")
DEFAULT_SKIP_ROWS = r"(?i)\b(total|subtotal)\b"

_NUMBER = re.compile(r"\(?[-+\u2212]?\$?\s*\d[\d,]*(?:\.\d+)?\)?")
_EMPTY = {"", "-", "--", "\u2014", "n/a", "na"}


def parse_number(text: str | None) -> float | None:
    """'$24,500.00' -> 24500.0, '(120.25)' or '-$120.25' -> -120.25, '--' -> None."""
    if text is None or text.strip().lower() in _EMPTY:
        return None
    match = _NUMBER.search(text)
    if not match:
        return None
    token = match.group(0)
    negative = (token.startswith("(") and token.endswith(")")) or "-" in token or "\u2212" in token
    value = float(re.sub(r"[^\d.]", "", token))
    return -value if negative else value


def extract_ag_grid(soup) -> tuple[list[str], list[list[str]]]:
    """Read an ag-Grid (Fidelity's positions table).

    ag-Grid splits one visual row across containers - the pinned Symbol column lives in
    .ag-pinned-left-cols-container, the rest in .ag-center-cols-container - and repeats the
    same row-index in each. Joining on row-index and keying cells by col-id keeps the halves
    aligned; matching by position does not, because the halves hold different column counts.
    """
    root = soup.select_one(".ag-root") or soup
    columns: list[str] = []
    for header in root.select(".ag-header-row"):
        for cell in header.select("[col-id]"):
            if cell.get("col-id") not in columns:
                columns.append(cell.get("col-id"))

    by_row: dict[str, dict[str, str]] = {}
    for container in root.select(".ag-pinned-left-cols-container, .ag-center-cols-container, "
                                 ".ag-pinned-right-cols-container"):
        for row in container.select('[role="row"]'):
            index = row.get("row-index") or row.get("aria-rowindex") or str(len(by_row))
            cells = by_row.setdefault(index, {})
            for cell in row.select("[col-id]"):
                cells[cell.get("col-id")] = cell.get_text("\n", strip=True)

    if not by_row:
        raise ParseError("Found an ag-Grid but no rows; the page may not have finished loading.")
    if not columns:
        columns = sorted({c for cells in by_row.values() for c in cells})

    order = sorted(by_row, key=lambda i: int(i) if str(i).lstrip("-").isdigit() else 0)
    rows = [[by_row[i].get(column, "") for column in columns] for i in order]
    return columns, rows


def extract_grid(html: str) -> tuple[list[str], list[list[str]]]:
    """Return (headers, rows) from an HTML <table> or an ARIA grid (div role="grid")."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one(".ag-center-cols-container, .ag-root"):
        return extract_ag_grid(soup)
    table = soup.find("table")
    if table is not None:
        row_els = table.find_all("tr")

        def cells_of(row):
            return row.find_all(["th", "td"], recursive=False)

        def is_header(cell):
            return cell.name == "th"
    else:
        grid = soup.find(attrs={"role": ["grid", "treegrid", "table"]})
        if grid is None:
            raise ParseError("No <table> or role=grid element found.")

        # AG Grid keeps pinned columns (Fidelity's Symbol column) in a separate
        # container from the remaining columns. Join their rows by aria-rowindex.
        pinned = grid.select_one(".ag-pinned-left-cols-container")
        center = grid.select_one(".ag-center-cols-container")
        if pinned is not None and center is not None:
            headers = [" ".join(cell.get_text(" ", strip=True).split())
                       for cell in grid.find_all(attrs={"role": "columnheader"})]
            merged: dict[str, list[str]] = {}
            for container in (pinned, center):
                for row in container.find_all(attrs={"role": "row"}):
                    row_index = row.get("aria-rowindex")
                    if row_index is None:
                        continue
                    cells = row.find_all(attrs={"role": ["rowheader", "gridcell", "cell"]})
                    merged.setdefault(row_index, []).extend(
                        cell.get_text("\n", strip=True) for cell in cells
                    )
            rows = [merged[index] for index in sorted(merged, key=int)]
            return headers, rows

        row_els = grid.find_all(attrs={"role": "row"})

        def cells_of(row):
            return row.find_all(attrs={"role": ["columnheader", "rowheader", "gridcell", "cell"]})

        def is_header(cell):
            return cell.get("role") == "columnheader"

    headers: list[str] | None = None
    rows: list[list[str]] = []
    for row in row_els:
        cells = cells_of(row)
        if not cells:
            continue
        if all(is_header(c) for c in cells):
            if headers is None:  # later repeated header rows are ignored
                headers = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
            continue
        rows.append([c.get_text("\n", strip=True) for c in cells])

    if headers is None:  # no <th>/columnheader cells: treat the first row as the header
        if not rows:
            raise ParseError("The table is empty.")
        headers, rows = [" ".join(t.split()) for t in rows[0]], rows[1:]
    return headers, rows


def map_columns(headers: list[str], aliases: dict | None = None) -> dict[str, int]:
    """Map each output field to a column index: exact header match first, then 'contains'."""
    aliases = {**DEFAULT_COLUMN_ALIASES, **(aliases or {})}
    norm = [" ".join(h.lower().split()) for h in headers]
    colmap: dict[str, int] = {}
    used: set[int] = set()
    for field, names in aliases.items():
        names = [" ".join(n.lower().split()) for n in names]
        idx = next((i for n in names for i, h in enumerate(norm)
                    if i not in used and h == n), None)
        if idx is None:
            idx = next((i for n in names for i, h in enumerate(norm)
                        if i not in used and n in h
                        and (not any(x in h for x in EXCLUDE_IN_LOOSE_MATCH)
                             or (field == "unrealized_pnl" and "$" in h and "%" in h))), None)
        if idx is not None:
            colmap[field] = idx
            used.add(idx)
    return colmap


def parse_positions(headers: list[str], rows: list[list[str]],
                    aliases: dict | None = None, skip_pattern: str | None = None) -> list[dict]:
    colmap = map_columns(headers, aliases)
    missing = [f for f in REQUIRED_FIELDS if f not in colmap]
    if missing:
        raise ParseError(f"Missing column(s) {missing}. Headers on the page: {headers}. "
                         "Add the right header text under 'column_aliases' in config.fidelity.json.")
    skip_re = re.compile(skip_pattern or DEFAULT_SKIP_ROWS)
    last_col = max(colmap.values())

    positions = []
    for cells in rows:
        if len(cells) <= last_col:  # group labels, spanned total rows, expanded lot details
            continue
        first_line = cells[colmap["symbol"]].split("\n")[0].strip()
        if not first_line or skip_re.search(f"{cells[0]} {first_line}"):
            continue
        quantity = parse_number(cells[colmap["quantity"]])
        market_value = parse_number(cells[colmap["market_value"]])
        if quantity is None:  # group headers, subtotals, cash and pending rows
            if first_line.lower() not in {
                "account:", "account total", "balances", "cash", "cash balance",
                "pending activity",
            } and not first_line.lower().startswith("you do not hold"):
                log.warning("Skipping row %r: no quantity or market value", first_line)
            continue
        if market_value is None:
            log.warning("%s has no current value on the page (shown as '--'); keeping the "
                        "position with market_value null.", first_line.split()[0])
        pnl = parse_number(cells[colmap["unrealized_pnl"]]) if "unrealized_pnl" in colmap else None
        positions.append({
            "symbol": first_line.split()[0].upper().strip("*"),
            "quantity": int(quantity) if quantity.is_integer() else quantity,
            "market_value": None if market_value is None else round(market_value, 2),
            "unrealized_pnl": None if pnl is None else round(pnl, 2),
        })
    return positions


def parse_balances(headers: list[str], rows: list[list[str]],
                   aliases: dict | None = None) -> list[dict]:
    colmap = map_columns(headers, aliases)
    value_col = colmap.get("market_value")
    if value_col is None:
        return []
    balances = []
    for cells in rows:
        if len(cells) <= value_col:
            continue
        name = cells[0].split("\n")[0].strip()
        if name.lower() not in {"cash", "cash balance", "pending activity"}:
            continue
        value = parse_number(cells[value_col])
        if value is not None:
            balances.append({"name": name, "value": round(value, 2)})
    return balances


def parse_account_total(headers: list[str], rows: list[list[str]],
                        aliases: dict | None = None) -> float | None:
    colmap = map_columns(headers, aliases)
    value_col = colmap.get("market_value")
    if value_col is None:
        return None
    for cells in rows:
        if len(cells) > value_col and cells[0].split("\n")[0].strip().lower() in {
            "total", "account total",
        }:
            return parse_number(cells[value_col])
    return None


def parse_account_sections(headers: list[str], rows: list[list[str]],
                           aliases: dict | None = None,
                           skip_pattern: str | None = None) -> list[dict]:
    """Split Fidelity's combined grid into account sections using Account: rows."""
    symbol_col = map_columns(headers, aliases).get("symbol", 0)
    sections: list[tuple[str, list[list[str]]]] = []
    account_name: str | None = None
    account_rows: list[list[str]] = []

    for row in rows:
        if len(row) <= symbol_col:
            continue
        lines = [line.strip() for line in row[symbol_col].split("\n") if line.strip()]
        if lines and lines[0].lower() == "account:":
            if account_name is not None:
                sections.append((account_name, account_rows))
            account_name = lines[1] if len(lines) > 1 else "Fidelity Account"
            account_rows = [row]
        elif account_name is not None:
            account_rows.append(row)

    if account_name is not None:
        sections.append((account_name, account_rows))
    if not sections:
        sections = [("Fidelity Account", rows)]

    return [
        {
            "account": name,
            "positions": parse_positions(headers, account_rows, aliases, skip_pattern),
            "balances": parse_balances(headers, account_rows, aliases),
            "account_total": parse_account_total(headers, account_rows, aliases),
        }
        for name, account_rows in sections
    ]


def read_csv_grid(path) -> tuple[list[str], list[list[str]]]:
    """Read a downloaded holdings CSV into the same (headers, rows) shape as a web table.

    Fidelity exports can include disclaimers after the holdings. The header row is the first
    row containing a 'Symbol' cell, and the holdings end at the first near-empty row.
    """
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        table = [[cell.strip().strip('"') for cell in row] for row in csv.reader(f)]

    start = next((i for i, row in enumerate(table)
                  if any(c.strip().lower() == "symbol" for c in row)), None)
    if start is None:
        raise ParseError(f"No 'Symbol' header row found in {path}. Open the file and check "
                         "it is the holdings export.")
    headers = [c.strip() for c in table[start]]
    rows = []
    for row in table[start + 1:]:
        if len([c for c in row if c.strip()]) <= 1:  # blank line or trailing disclaimer
            break
        rows.append(row)
    return headers, rows


def credentials_from_env(cfg: dict, env: dict | None = None) -> tuple[str | None, str | None]:
    """Read the Fidelity credential pair selected by the config."""
    env = os.environ if env is None else env
    prefix = cfg.get("credentials_env_prefix", "BROKER")
    user = env.get(f"{prefix}_USERNAME")
    password = env.get(f"{prefix}_PASSWORD")
    return user, password


def build_payload(account: str, positions: list[dict], balances: list[dict] | None = None,
                  account_total: float | None = None) -> dict:
    return {
        "account": account,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "positions": positions,
        "balances": balances or [],
        "account_total": account_total,
    }


def print_holdings_table(positions: list[dict], balances: list[dict],
                         account_total: float | None = None,
                         account_name: str = "Fidelity Account") -> None:
    rows = [
        [position["symbol"], position["quantity"], position["market_value"],
         position["unrealized_pnl"]]
        for position in positions
    ]
    rows.extend([[balance["name"], "", balance["value"], ""] for balance in balances])
    headers = ["Holding / Balance", "Quantity", "Value", "Unrealized P/L"]
    widths = [max(len(str(row[index])) for row in [headers, *rows])
              for index in range(len(headers))]
    print(f"\n=== {account_name} ===")
    print("\n" + "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))
    if account_total is not None:
        print(f"\nAccount total: ${account_total:,.2f}")


def write_json_atomic(path: Path, payload: dict) -> None:
    """Write to a temp file and rename, so the trading app never reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Browser
# --------------------------------------------------------------------------- #

def to_locators(spec) -> list[tuple[str, str]]:
    """A selector is CSS, or XPath when prefixed with 'xpath:'. A config value may be one
    selector or a list of fallbacks, tried in order."""
    specs = spec if isinstance(spec, list) else [spec]
    return [(By.XPATH, s[len("xpath:"):]) if s.startswith("xpath:") else (By.CSS_SELECTOR, s)
            for s in specs if s]


def find_all(driver, spec) -> list:
    return [el for loc in to_locators(spec) for el in driver.find_elements(*loc)]


def attach_to_chrome(address: str) -> webdriver.Chrome:
    """Drive a Chrome window the person opened and signed in to themselves.

    Nothing about the browser is faked: it is an ordinary Chrome started with a debugging
    port, and the sign-in is done by hand. The script only reads the page afterwards.
    """
    host, _, port = address.partition(":")
    with socket.socket() as probe:
        probe.settimeout(2)
        if probe.connect_ex((host or "127.0.0.1", int(port or 9222))) != 0:
            raise SystemExit(
                f"No Chrome is listening on {address}.\n"
                "Start one first (double-click start_chrome_debug.bat), sign in to your "
                "account in that window, then run this again.")
    options = Options()
    options.debugger_address = address
    log.info("Attaching to the Chrome window on %s.", address)
    return webdriver.Chrome(options=options)


def make_driver(cfg: dict, headless: bool) -> webdriver.Chrome:
    if cfg.get("attach_to_chrome"):
        return attach_to_chrome(cfg["attach_to_chrome"])
    opts = Options()
    # A dedicated, persistent profile keeps the site's "recognised device" cookie between
    # runs, so a one-time new-device check doesn't come back on every run.
    profile = Path(cfg.get("chrome_profile_dir", ".chrome-profile")).resolve()
    download_dir = Path(cfg.get("download_dir", "downloads")).resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    opts.add_argument(f"--user-data-dir={profile}")
    opts.add_argument("--window-size=1400,1000")
    opts.add_experimental_option("prefs", {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    })
    if headless:
        opts.add_argument("--headless=new")
    return webdriver.Chrome(options=opts)  # Selenium Manager downloads the matching driver


def _displayed(el) -> bool:
    try:
        return el.is_displayed()
    except StaleElementReferenceException:
        return False


def _first_visible(driver, spec, editable: bool = False):
    """First visible match; with editable=True, also skip disabled/read-only boxes."""
    for el in find_all(driver, spec):
        try:
            if el.is_displayed() and (not editable or (
                    el.is_enabled() and not el.get_attribute("readonly"))):
                return el
        except StaleElementReferenceException:
            continue
    return None


def wait_visible(driver, spec, timeout: float, editable: bool = False):
    return WebDriverWait(driver, timeout).until(
        lambda d: _first_visible(d, spec, editable) or False)


def click(driver, el) -> None:
    try:
        el.click()
    except ElementClickInterceptedException:  # cookie banners, overlays
        driver.execute_script("arguments[0].click();", el)


def is_present(driver, spec, timeout: float) -> bool:
    try:
        WebDriverWait(driver, timeout).until(lambda d: bool(find_all(d, spec)))
        return True
    except TimeoutException:
        return False


def switch_to_frame(driver, spec, timeout: float) -> None:
    frame = WebDriverWait(driver, timeout).until(lambda d: next(iter(find_all(d, spec)), False))
    driver.switch_to.frame(frame)


def describe_form(driver, limit: int = 40) -> str:
    """List visible inputs/buttons (attributes only, never values) to help fix selectors."""
    lines = []
    for el in driver.find_elements(By.CSS_SELECTOR, "input, select, textarea, button, a"):
        if len(lines) >= limit:
            break
        if not _displayed(el):
            continue
        text = " ".join((el.text or "").split())[:50]
        if el.tag_name == "a" and not re.search(r"(?i)log|sign|user", text):
            continue
        attrs = " ".join(f'{a}="{v}"' for a in
                         ("type", "id", "name", "autocomplete", "aria-label", "placeholder")
                         if (v := el.get_attribute(a)))
        lines.append(f"  <{el.tag_name} {attrs}> {text}".rstrip())
    frames = len(driver.find_elements(By.TAG_NAME, "iframe"))
    if frames:
        lines.append(f"  (+{frames} iframe(s); if the form is inside one, set selectors.login_iframe)")
    return "\n".join(lines) or "  (no visible form elements)"


def _username_box(driver, sel: dict, timeout: float):
    """Return the User ID box. If the site shows a remembered (masked) user ID instead,
    click 'Log in with a different user ID' first so we always type the ID from .env."""
    def ready(d):
        link = _first_visible(d, sel.get("different_user_link"))
        if link:
            return ("switch", link)
        box = _first_visible(d, sel["username"], editable=True)
        return ("box", box) if box else False

    kind, el = WebDriverWait(driver, timeout).until(ready)
    if kind == "switch":
        log.info("Remembered user ID shown; switching to the user ID from .env.")
        click(driver, el)
        el = wait_visible(driver, sel["username"], timeout, editable=True)
    return el


def wait_for_manual_login(driver, cfg: dict) -> None:
    """Let the person sign in themselves, then carry on once the site is logged in."""
    marker = cfg["selectors"].get("logged_in_marker")
    url_hint = cfg.get("signed_in_url_contains", "")
    timeout = cfg.get("manual_login_timeout_seconds", 300)
    if not (marker or url_hint):
        raise ParseError("Manual sign-in needs selectors.logged_in_marker or "
                         "signed_in_url_contains set in the config.")

    print("\n" + "=" * 72, file=sys.stderr)
    print("  Sign in to your account in the Chrome window that just opened.", file=sys.stderr)
    print("  Nothing is typed for you. Finish any security code as normal.", file=sys.stderr)
    print(f"  This will continue on its own once you are in (waiting {timeout}s).",
          file=sys.stderr)
    print("=" * 72 + "\n", file=sys.stderr)

    def ready(d):
        if marker and find_all(d, marker):
            return True
        return bool(url_hint) and url_hint in d.current_url

    try:
        WebDriverWait(driver, timeout, poll_frequency=1).until(ready)
    except TimeoutException:
        raise TimeoutException(
            f"Still not signed in after {timeout}s. Run again and finish signing in, or "
            "increase manual_login_timeout_seconds in the config.")
    log.info("Signed in. Reading holdings.")


def _enter_credentials(driver, cfg: dict, username: str, password: str):
    """Type the user ID and password and submit. Returns the URL from before submitting."""
    sel, timeout = cfg["selectors"], cfg.get("timeout_seconds", 30)
    try:
        user_box = _username_box(driver, sel, timeout)
        user_box.clear()
        user_box.send_keys(username)
        # Some sites only show the password box after the username is submitted.
        try:
            pass_box = wait_visible(driver, sel["password"], 2, editable=True)
        except TimeoutException:
            click(driver, wait_visible(driver, sel["submit"], timeout))
            pass_box = wait_visible(driver, sel["password"], timeout, editable=True)
    except TimeoutException:
        log.error("Couldn't find the sign-in fields. Visible form elements on %s:\n%s\n"
                  "Adjust selectors.username / password / submit in config.fidelity.json.",
                  driver.current_url, describe_form(driver))
        raise
    pass_box.clear()
    pass_box.send_keys(password)

    url_before = driver.current_url
    try:
        click(driver, wait_visible(driver, sel["submit"], 3))
    except TimeoutException:
        pass_box.send_keys(Keys.ENTER)
    driver.switch_to.default_content()
    return url_before


def login(driver, cfg: dict, username: str, password: str) -> None:
    sel, timeout = cfg["selectors"], cfg.get("timeout_seconds", 30)
    marker = sel.get("logged_in_marker")
    retry_spec = sel.get("login_error_retry")  # "Go back to login" on Fidelity's error page
    attempts = max(1, int(cfg.get("login_attempts", 3)))
    login_timeout = cfg.get("login_timeout_seconds", 180)

    if cfg.get("attach_to_chrome"):
        if marker and is_present(driver, marker, 3):
            log.info("Already signed in in that window.")
            return
        if cfg.get("positions_url"):
            driver.get(cfg["positions_url"])
            if marker and is_present(driver, marker, 5):
                log.info("Already signed in in that window.")
                return
        driver.get(cfg["login_url"])
        return wait_for_manual_login(driver, cfg)

    driver.get(cfg["login_url"])
    if marker and is_present(driver, marker, 3):
        log.info("Session still valid; skipping sign-in.")
        return
    if cfg.get("login_mode") == "manual":
        return wait_for_manual_login(driver, cfg)
    if sel.get("login_iframe"):
        switch_to_frame(driver, sel["login_iframe"], timeout)

    for attempt in range(1, attempts + 1):
        url_before = _enter_credentials(driver, cfg, username, password)
        log.info("Credentials submitted (attempt %d of %d). If the site asks you to verify "
                 "this browser, complete it in the Chrome window. Waiting up to %ss.",
                 attempt, attempts, login_timeout)

        def outcome(d, url_before=url_before):
            """'in' once signed in, 'error' on the site's try-again page, else False."""
            if marker and find_all(d, marker):
                return "in"
            if retry_spec and _first_visible(d, retry_spec):
                return "error"
            if marker:
                return False
            if d.current_url != url_before:
                return "in"
            # Single-page apps may keep the URL; the password box disappearing is the next sign.
            return "in" if (not sel.get("login_iframe") and not any(
                _displayed(e) for e in find_all(d, sel["password"]))) else False

        if WebDriverWait(driver, login_timeout).until(outcome) == "in":
            log.info("Signed in.")
            return

        # Fidelity sometimes answers a correct sign-in with "Sorry, we can't complete this
        # action right now." Going back to the login page and retrying usually clears it.
        log.warning("The site showed its 'try again' page (attempt %d of %d).",
                    attempt, attempts)
        if attempt == attempts:
            break
        click(driver, wait_visible(driver, retry_spec, timeout))
        time.sleep(3)
        if sel.get("login_iframe"):
            switch_to_frame(driver, sel["login_iframe"], timeout)

    if cfg.get("login_fallback_to_manual", True):
        log.warning("The site refused the automated sign-in %d times. Handing over to you.",
                    attempts)
        return wait_for_manual_login(driver, cfg)
    raise TimeoutException(
        f"Sign-in did not finish after {attempts} attempts; the site kept showing its "
        "'try again' page. Sign in manually in an ordinary Chrome window to check the "
        "account is healthy, then run again, or download the CSV and use --csv.")


def dismiss_popups(driver, spec, wait: float = 2) -> None:
    """Click through optional interstitials ('Remind me later', etc.) if one is showing."""
    if not spec:
        return
    try:
        el = wait_visible(driver, spec, wait)
    except TimeoutException:
        return
    log.info("Dismissing interstitial: %r", " ".join((el.text or "").split())[:40])
    click(driver, el)


def open_positions_page(driver, cfg: dict) -> None:
    timeout = cfg.get("timeout_seconds", 30)
    dismiss_popups(driver, cfg.get("dismiss_if_present"))
    if cfg.get("positions_url"):
        driver.get(cfg["positions_url"])
        dismiss_popups(driver, cfg.get("dismiss_if_present"))
    for spec in cfg.get("clicks_before_read", []):  # e.g. a "Positions" tab, a "Show all" button
        click(driver, wait_visible(driver, spec, timeout))


def download_holdings_report(driver, cfg: dict) -> Path | None:
    """Click through the broker's export controls and return the downloaded file."""
    spec = cfg.get("holdings_report_download")
    settings = cfg.get("holdings_report_settings") or {}
    if not spec and not settings:
        return None
    timeout = cfg.get("table_timeout_seconds", 60)
    download_dir = Path(cfg.get("download_dir", "downloads")).resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    before = {p for p in download_dir.iterdir() if p.is_file()}
    if spec:
        click(driver, wait_visible(driver, spec, timeout))
    if settings.get("download_more_data"):
        handles_before = set(driver.window_handles)
        click(driver, wait_visible(driver, settings["download_more_data"], timeout))
        WebDriverWait(driver, timeout).until(
            lambda d: len(set(d.window_handles) - handles_before) > 0
            or "/tfpdownloads/" in d.current_url.lower())
        new_handles = set(driver.window_handles) - handles_before
        if new_handles:
            driver.switch_to.window(next(iter(new_handles)))
    if settings.get("holdings"):
        click(driver, wait_visible(driver, settings["holdings"], timeout))
    if settings.get("csv"):
        click(driver, wait_visible(driver, settings["csv"], timeout))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = {p for p in download_dir.iterdir() if p.is_file()}
        new = current - before
        if new and not any(p.suffix == ".crdownload" for p in current):
            newest = max(new, key=lambda p: p.stat().st_mtime)
            log.info("Downloaded holdings report: %s", newest)
            return newest
        time.sleep(0.5)
    raise TimeoutException("Holdings report download did not finish.")


def read_positions_table(driver, spec, timeout: float) -> str:
    """Wait until the table has rows and has stopped changing, then return its HTML."""
    state = {"rows": -1, "since": time.monotonic()}

    def settled(d):
        found = find_all(d, spec)
        if not found:
            return False
        n = len(found[0].find_elements(By.CSS_SELECTOR, "tr, [role='row']"))
        now = time.monotonic()
        if n != state["rows"]:
            state.update(rows=n, since=now)
            return False
        if n > 1 and now - state["since"] >= 1.5:
            return found[0].get_attribute("outerHTML")
        return False

    return WebDriverWait(driver, timeout, poll_frequency=0.5,
                         ignored_exceptions=[StaleElementReferenceException]).until(settled)


def dump_tables(driver, aliases: dict | None) -> None:
    """Print every table/grid on the current page to help you pick selectors."""
    soup = BeautifulSoup(driver.page_source, "html.parser")
    found = soup.find_all("table") + soup.find_all(attrs={"role": ["grid", "treegrid"]})
    iframes = len(driver.find_elements(By.TAG_NAME, "iframe"))
    print(f"\n{len(found)} table(s) and {iframes} iframe(s) on {driver.current_url}\n")
    for i, el in enumerate(found):
        if el.get("id"):
            selector = f"#{el['id']}"
        else:
            role = f"[role='{el['role']}']" if el.get("role") else ""
            selector = el.name + role + "".join(f".{c}" for c in el.get("class", []))
        try:
            headers, rows = extract_grid(str(el))
        except ParseError:
            headers, rows = [], []
        mapping = {f: headers[j] for f, j in map_columns(headers, aliases).items()}
        print(f"[{i}] selector: {selector}   data rows: {len(rows)}")
        print(f"    headers: {headers}")
        print(f"    mapped:  {mapping}\n")
    if iframes and not found:
          print("No tables in the main page. The positions table may be inside an iframe: "
              "set selectors.positions_iframe in config.fidelity.json and run again.")


def save_debug(driver, folder: str = "debug") -> None:
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = Path(folder)
        out.mkdir(exist_ok=True)
        driver.save_screenshot(str(out / f"{stamp}.png"))
        (out / f"{stamp}.html").write_text(driver.page_source, encoding="utf-8")
        log.error("Saved screenshot + HTML to %s/ (contains account data; delete after use).", out)
    except Exception as exc:  # the browser may already be gone
        log.error("Could not save debug files: %s", exc)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.fidelity.json")
    ap.add_argument("--out", help="write JSON to this file (default: print to stdout)")
    ap.add_argument("--headless", action="store_true", help="no visible browser window")
    ap.add_argument("--dump-tables", action="store_true",
                    help="list every table on the positions page, then exit")
    ap.add_argument("--csv", metavar="PATH",
                    help="parse a holdings CSV you already downloaded; no browser, no login")
    ap.add_argument("--allow-empty", action="store_true",
                    help="accept a positions table with zero rows")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    load_dotenv()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    prefix = cfg.get("credentials_env_prefix", "BROKER")

    def finish(headers, rows) -> int:
        sections = parse_account_sections(headers, rows, cfg.get("column_aliases"),
                                          cfg.get("skip_row_pattern"))
        positions = [position for section in sections for position in section["positions"]]
        if not positions and not args.allow_empty:
            raise ParseError("Found the holdings but parsed zero positions; refusing to report "
                             "an empty account (use --allow-empty if that's real).")
        account_total = sum(
            section["account_total"] for section in sections
            if section["account_total"] is not None
        ) or None
        payload = build_payload(cfg.get("account_name", "Brokerage Account"), positions,
                                [balance for section in sections for balance in section["balances"]],
                                account_total)
        if args.out:
            write_json_atomic(Path(args.out), payload)
            log.info("Wrote %d positions to %s", len(positions), args.out)
        for section in sections:
            print_holdings_table(section["positions"], section["balances"],
                                 section["account_total"], section["account"])
        return 0

    if args.csv:  # offline: parse a file that is already on disk
        try:
            return finish(*read_csv_grid(Path(args.csv)))
        except Exception:
            log.exception("Could not parse %s; no JSON was written.", args.csv)
            return 1

    username, password = credentials_from_env(cfg)
    if cfg.get("login_mode") == "manual":
        username, password = username or "", password or ""
    elif not (username and password):
        log.error("Set %s_USERNAME and %s_PASSWORD in the environment or a .env file.",
              prefix, prefix)
        return 2
    if not cfg.get("login_url", "").startswith("http"):
        log.error("Set login_url in %s to your broker's sign-in page.", args.config)
        return 2

    sel = cfg["selectors"]
    driver = make_driver(cfg, args.headless)
    try:
        login(driver, cfg, username, password)
        open_positions_page(driver, cfg)
        report = download_holdings_report(driver, cfg)

        if args.dump_tables:
            if not args.headless:
                input("\nOpen the positions page in the browser if it isn't showing, "
                      "then press Enter here... ")
            if sel.get("positions_iframe"):
                switch_to_frame(driver, sel["positions_iframe"], cfg.get("timeout_seconds", 30))
            dump_tables(driver, cfg.get("column_aliases"))
            return 0

        if cfg.get("positions_source", "table") == "csv":
            # Fidelity draws holdings in an ag-Grid that splits the symbol column away from
            # the rest, so the downloaded CSV is the dependable source there.
            if report is None:
                raise ParseError("positions_source is 'csv' but no export steps are set; "
                                 "check holdings_report_download in the config.")
            headers, rows = read_csv_grid(report)
        else:
            if sel.get("positions_iframe"):
                switch_to_frame(driver, sel["positions_iframe"], cfg.get("timeout_seconds", 30))
            html = read_positions_table(driver, sel["positions_table"],
                                        cfg.get("table_timeout_seconds", 60))
            headers, rows = extract_grid(html)
        log.debug("Headers: %s", headers)
        return finish(headers, rows)
    except Exception:
        log.exception("Run failed; no JSON was written.")
        save_debug(driver)
        return 1
    finally:
        if cfg.get("attach_to_chrome"):
            log.info("Leaving your Chrome window open.")
        else:
            driver.quit()


if __name__ == "__main__":
    sys.exit(main())
