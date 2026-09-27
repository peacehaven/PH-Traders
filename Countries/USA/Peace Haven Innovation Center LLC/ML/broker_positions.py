#!/usr/bin/env python3
"""
broker_positions.py - sign in to a brokerage website with Selenium, read the
rendered positions table, and write it as JSON for a trading application.

  python broker_positions.py --dump-tables          first run: find the right table
  python broker_positions.py --out positions.json   normal run

Credentials come from BROKER_USERNAME / BROKER_PASSWORD (environment or .env).
Site-specific details (URLs, CSS selectors, column names) live in config.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
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

# Header names to look for, best match first. Override any field in config.json.
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
DEFAULT_SKIP_ROWS = r"(?i)\b(total|subtotal|money accounts?|margin balance)\b"
# Rows in Merrill's "Balances" block: money accounts, cash, margin, pending.
BALANCE_ROW = re.compile(
    r"(?i)^(money accounts?|cash balance|margin balance|pending activity"
    r"|[a-z][a-z ]*balance)$")

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


def extract_grid(html: str) -> tuple[list[str], list[list[str]]]:
    """Return (headers, rows) from an HTML <table> or an ARIA grid (div role="grid")."""
    soup = BeautifulSoup(html, "html.parser")
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
                         "Add the right header text under 'column_aliases' in config.json.")
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
        if quantity is None or market_value is None:
            if first_line.lower() != "balances" and not BALANCE_ROW.match(first_line):
                log.warning("Skipping row %r: no quantity or market value", first_line)
            continue
        pnl = parse_number(cells[colmap["unrealized_pnl"]]) if "unrealized_pnl" in colmap else None
        positions.append({
            "symbol": first_line.split()[0].upper(),
            "quantity": int(quantity) if quantity.is_integer() else quantity,
            "market_value": round(market_value, 2),
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
        if not BALANCE_ROW.match(name):
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
        if len(cells) > value_col and cells[0].split("\n")[0].strip().lower() == "total":
            return parse_number(cells[value_col])
    return None


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
                         account_total: float | None = None) -> None:
    rows = [
        [position["symbol"], position["quantity"], position["market_value"],
         position["unrealized_pnl"]]
        for position in positions
    ]
    rows.extend([[balance["name"], "", balance["value"], ""] for balance in balances])
    headers = ["Holding / Balance", "Quantity", "Value", "Unrealized P/L"]
    widths = [max(len(str(row[index])) for row in [headers, *rows])
              for index in range(len(headers))]
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


def make_driver(cfg: dict, headless: bool) -> webdriver.Chrome:
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


def login(driver, cfg: dict, username: str, password: str) -> None:
    sel, timeout = cfg["selectors"], cfg.get("timeout_seconds", 30)
    marker = sel.get("logged_in_marker")

    driver.get(cfg["login_url"])
    if marker and is_present(driver, marker, 3):
        log.info("Session still valid; skipping sign-in.")
        return
    if sel.get("login_iframe"):
        switch_to_frame(driver, sel["login_iframe"], timeout)

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
                  "Adjust selectors.username / password / submit in config.json.",
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

    login_timeout = cfg.get("login_timeout_seconds", 180)
    log.info("Credentials submitted. If the site asks you to verify this browser (e.g. a code "
             "by text), complete it in the Chrome window. Waiting up to %ss.", login_timeout)

    def signed_in(d):
        if marker:
            if find_all(d, marker):
                return True
        error_selectors = (
            "#signin-error-target",
            "#ah-first-factor-error-container",
            "[role='alert']",
        )
        error_text = " ".join(
            (element.text or "").lower()
            for selector in error_selectors
            for element in find_all(d, selector)
            if _displayed(element)
        )
        if any(term in error_text for term in ("incorrect", "invalid", "unable to", "try again")):
            raise ParseError("Merrill rejected the login credentials.")
        if marker:
            return False
        if d.current_url != url_before:
            return True
        # Single-page apps may keep the URL; the password box disappearing is the next best sign.
        return not sel.get("login_iframe") and not any(
            _displayed(e) for e in find_all(d, sel["password"]))

    WebDriverWait(driver, login_timeout).until(signed_in)
    log.info("Signed in.")


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
    if "/accounts-overview" in driver.current_url.lower():
        account_button = _first_visible(driver, "button[data-account='true']")
        if account_button:
            click(driver, account_button)
            WebDriverWait(driver, timeout).until(
                lambda d: bool(find_all(d, "#nw__account-detail-view:not(.hide)"))
            )
            holdings_link = _first_visible(
                driver, "a[href*='/TFPHoldings/Holdings.aspx']")
            if holdings_link:
                click(driver, holdings_link)
    for spec in cfg.get("clicks_before_read", []):  # e.g. a "Positions" tab, a "Show all" button
        url_before = driver.current_url
        click(driver, wait_visible(driver, spec, timeout))
        if "HoldingsByAccount.aspx" in spec:
            WebDriverWait(driver, timeout).until(
                lambda d: d.current_url != url_before
                and "HoldingsByAccount.aspx" in d.current_url)


def download_holdings_report(driver, cfg: dict) -> bool:
    spec = cfg.get("holdings_report_download")
    settings = cfg.get("holdings_report_settings") or {}
    if not spec and not settings:
        return False
    timeout = cfg.get("table_timeout_seconds", 60)
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
    csv_spec = settings.get("csv")
    if not csv_spec:
        return False
    download_dir = Path(cfg.get("download_dir", "downloads")).resolve()
    before = {p for p in download_dir.iterdir() if p.is_file()}
    click(driver, wait_visible(driver, csv_spec, timeout))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = {p for p in download_dir.iterdir() if p.is_file()}
        if (current - before) and not any(p.suffix == ".crdownload" for p in current):
            log.info("Downloaded holdings report to %s.", download_dir)
            return True
        time.sleep(0.5)
    raise TimeoutException("Holdings CSV download did not finish.")


def read_positions_table(driver, spec, timeout: float) -> str:
    """Wait until the table has rows and has stopped changing, then return its HTML."""
    state = {"rows": -1, "since": time.monotonic()}

    def settled(d):
        for table in find_all(d, spec):
            try:
                table_html = table.get_attribute("outerHTML")
                if not table_html:
                    continue
                headers, _ = extract_grid(table_html)
                if not all(field in map_columns(headers) for field in REQUIRED_FIELDS):
                    continue
                n = len(table.find_elements(By.CSS_SELECTOR, "tr, [role='row']"))
                now = time.monotonic()
                if n != state["rows"]:
                    state.update(rows=n, since=now)
                    return False
                if n > 1 and now - state["since"] >= 1.5:
                    return table_html
            except (ParseError, StaleElementReferenceException):
                continue
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
              "set selectors.positions_iframe in config.json and run again.")


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
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", help="write JSON to this file (default: print to stdout)")
    ap.add_argument("--profile", help="use a separate Chrome profile directory")
    ap.add_argument("--headless", action="store_true", help="no visible browser window")
    ap.add_argument("--dump-tables", action="store_true",
                    help="list every table on the positions page, then exit")
    ap.add_argument("--allow-empty", action="store_true",
                    help="accept a positions table with zero rows")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    load_dotenv()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.profile:
        cfg["chrome_profile_dir"] = args.profile
    username, password = os.getenv("BROKER_USERNAME"), os.getenv("BROKER_PASSWORD")
    if not (username and password):
        log.error("Set BROKER_USERNAME and BROKER_PASSWORD in the environment or a .env file.")
        return 2
    if not cfg.get("login_url", "").startswith("http"):
        log.error("Set login_url in %s to your broker's sign-in page.", args.config)
        return 2

    sel = cfg["selectors"]
    driver = make_driver(cfg, args.headless)
    try:
        login(driver, cfg, username, password)
        open_positions_page(driver, cfg)
        download_holdings_report(driver, cfg)

        if args.dump_tables:
            if not args.headless:
                input("\nOpen the positions page in the browser if it isn't showing, "
                      "then press Enter here... ")
            if sel.get("positions_iframe"):
                switch_to_frame(driver, sel["positions_iframe"], cfg.get("timeout_seconds", 30))
            dump_tables(driver, cfg.get("column_aliases"))
            return 0

        if sel.get("positions_iframe"):
            switch_to_frame(driver, sel["positions_iframe"], cfg.get("timeout_seconds", 30))
        html = read_positions_table(driver, sel["positions_table"],
                                    cfg.get("table_timeout_seconds", 60))
        headers, rows = extract_grid(html)
        log.debug("Headers: %s", headers)
        positions = parse_positions(headers, rows, cfg.get("column_aliases"),
                                    cfg.get("skip_row_pattern"))
        balances = parse_balances(headers, rows, cfg.get("column_aliases"))
        account_total = parse_account_total(headers, rows, cfg.get("column_aliases"))
        if not positions and not args.allow_empty:
            raise ParseError("Found the table but parsed zero positions; refusing to report "
                             "an empty account (use --allow-empty if that's real).")

        payload = build_payload(cfg.get("account_name", "Brokerage Account"), positions,
                    balances, account_total)
        if args.out:
            write_json_atomic(Path(args.out), payload)
            log.info("Wrote %d positions to %s", len(positions), args.out)
        print_holdings_table(positions, balances, account_total)
        return 0
    except Exception:
        log.exception("Run failed; no JSON was written.")
        save_debug(driver)
        return 1
    finally:
        try:
            driver.quit()
        except Exception:
            log.debug("Browser session was already closed.", exc_info=True)


if __name__ == "__main__":
    sys.exit(main())
