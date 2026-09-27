"""Turn a Fidelity positions page (or its CSV export) into JSON.

Nothing here touches a browser, so every rule below is covered by test_positions.py.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

log = logging.getLogger("fidelity")

EMPTY = {"", "-", "--", "\u2014", "n/a", "na", "not priced today"}
NUMBER = re.compile(r"\(?[-+\u2212]?\$?\s*\d[\d,]*(?:\.\d+)?\)?")
# A loose "contains" match must not grab these columns.
NOT_IN_LOOSE_MATCH = ("%", "day", "today", "change", "cost", "price", "52")


class ParseError(Exception):
    """The page or file didn't contain holdings we can trust."""


def parse_number(text: str | None) -> float | None:
    """'$24,500.00' -> 24500.0, '(120.25)' and '-$120.25' -> -120.25, '--' -> None."""
    if text is None or text.strip().lower() in EMPTY:
        return None
    match = NUMBER.search(text)
    if not match:
        return None
    token = match.group(0)
    negative = ((token.startswith("(") and token.endswith(")"))
                or "-" in token or "\u2212" in token)
    value = float(re.sub(r"[^\d.]", "", token))
    return -value if negative else value


# --------------------------------------------------------------------------- #
# Reading the page
# --------------------------------------------------------------------------- #

def _collect_ag_grid(soup, columns: list[str], by_row: dict) -> None:
    """Add one snapshot of the grid into the shared column list and row map."""
    root = soup.select_one(".ag-root") or soup
    for header in root.select(".ag-header-row"):
        for cell in header.select("[col-id]"):
            if cell.get("col-id") not in columns:
                columns.append(cell.get("col-id"))

    for container in root.select(".ag-pinned-left-cols-container, "
                                 ".ag-center-cols-container, "
                                 ".ag-pinned-right-cols-container"):
        for row in container.select('[role="row"]'):
            index = row.get("row-index") or row.get("aria-rowindex")
            if index is None:
                index = str(len(by_row))
            cells = by_row.setdefault(index, {})
            for cell in row.select("[col-id]"):
                text = cell.get_text("\n", strip=True)
                if text or cell.get("col-id") not in cells:
                    cells[cell.get("col-id")] = text


def read_ag_grid(soup) -> tuple[list[str], list[list[str]]]:
    """Read Fidelity's positions grid from one snapshot."""
    return _assemble([soup])


def _assemble(soups) -> tuple[list[str], list[list[str]]]:
    columns: list[str] = []
    by_row: dict[str, dict[str, str]] = {}
    for soup in soups:
        _collect_ag_grid(soup, columns, by_row)

    if not by_row:
        raise ParseError("Found the grid but no rows; the page may still be loading.")
    if not columns:
        columns = sorted({c for cells in by_row.values() for c in cells})

    order = sorted(by_row, key=lambda i: int(i) if str(i).lstrip("-").isdigit() else 0)
    return columns, [[by_row[i].get(column, "") for column in columns] for i in order]


def read_snapshots(htmls) -> tuple[list[str], list[list[str]]]:
    """Merge several snapshots of the same grid, taken while scrolling down it.

    ag-Grid only keeps the rows you can see in the page, so an account below the fold shows
    its total while its holdings are absent. Scrolling and merging by row-index brings every
    row in exactly once.
    """
    soups = [BeautifulSoup(html, "html.parser") for html in htmls]
    if any(soup.select_one(".ag-center-cols-container, .ag-root") for soup in soups):
        return _assemble(soups)
    return read_html_table(soups[0])


def read_html_table(soup) -> tuple[list[str], list[list[str]]]:
    """Fallback for an ordinary <table> or an ARIA grid of divs."""
    table = soup.find("table")
    if table is not None:
        row_elements = table.find_all("tr")

        def cells_of(row):
            return row.find_all(["th", "td"], recursive=False)

        def is_header(cell):
            return cell.name == "th"
    else:
        grid = soup.find(attrs={"role": ["grid", "treegrid", "table"]})
        if grid is None:
            raise ParseError("No holdings grid or table found on the page.")
        row_elements = grid.find_all(attrs={"role": "row"})

        def cells_of(row):
            return row.find_all(attrs={"role": ["columnheader", "rowheader",
                                                "gridcell", "cell"]})

        def is_header(cell):
            return cell.get("role") == "columnheader"

    headers: list[str] | None = None
    rows: list[list[str]] = []
    for row in row_elements:
        cells = cells_of(row)
        if not cells:
            continue
        if all(is_header(cell) for cell in cells):
            if headers is None:
                headers = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
            continue
        rows.append([cell.get_text("\n", strip=True) for cell in cells])

    if headers is None:
        if not rows:
            raise ParseError("The table is empty.")
        headers, rows = [" ".join(t.split()) for t in rows[0]], rows[1:]
    return headers, rows


def read_page(html: str) -> tuple[list[str], list[list[str]]]:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one(".ag-center-cols-container, .ag-root"):
        return read_ag_grid(soup)
    return read_html_table(soup)


def read_csv_export(path) -> tuple[list[str], list[list[str]]]:
    """Read a downloaded positions CSV.

    Fidelity's export has disclaimer lines below the data, so the header row is the first
    row holding a 'Symbol' cell and the holdings stop at the first near-empty row.
    """
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as handle:
        table = [[cell.strip().strip('"') for cell in row] for row in csv.reader(handle)]

    start = next((i for i, row in enumerate(table)
                  if any(cell.strip().lower() == "symbol" for cell in row)), None)
    if start is None:
        raise ParseError(f"No 'Symbol' header row in {path}; is that the positions export?")

    headers = [cell.strip() for cell in table[start]]
    rows = []
    for row in table[start + 1:]:
        if len([cell for cell in row if cell.strip()]) <= 1:
            break
        rows.append(row)
    return headers, rows


# --------------------------------------------------------------------------- #
# Turning rows into positions
# --------------------------------------------------------------------------- #

def map_columns(headers: list[str], wanted: dict[str, list[str]]) -> dict[str, int]:
    """Match each output field to a column: exact name first, then 'contains'."""
    normalised = [" ".join(h.lower().split()) for h in headers]
    found: dict[str, int] = {}
    used: set[int] = set()
    for field, names in wanted.items():
        names = [" ".join(n.lower().split()) for n in names]
        index = next((i for name in names for i, header in enumerate(normalised)
                      if i not in used and header == name), None)
        if index is None:
            index = next((i for name in names for i, header in enumerate(normalised)
                          if i not in used and name in header
                          and not any(bad in header for bad in NOT_IN_LOOSE_MATCH)), None)
        if index is not None:
            found[field] = index
            used.add(index)
    return found


def to_positions(headers, rows, columns: dict, skip_pattern: str | None = None) -> list[dict]:
    """Rows in, positions out.

    A row counts as a holding when it has a symbol and a quantity. Group headings,
    subtotals, cash lines and pending activity have no quantity, so they drop out.
    A holding Fidelity cannot price shows '--' and is kept with market_value null,
    because you still own it.
    """
    mapping = map_columns(headers, columns)
    missing = [field for field in ("symbol", "quantity") if field not in mapping]
    if missing:
        raise ParseError(f"Could not find column(s) {missing}. Columns on the page: "
                         f"{headers}. Adjust 'columns' in config.json.")
    skip = re.compile(skip_pattern) if skip_pattern else None
    last_column = max(mapping.values())

    positions = []
    for cells in rows:
        if len(cells) <= last_column:
            continue
        label = cells[mapping["symbol"]].split("\n")[0].strip()
        if not label or (skip and skip.search(f"{cells[0]} {label}")):
            continue
        quantity = parse_number(cells[mapping["quantity"]])
        if quantity is None:
            continue
        value = parse_number(cells[mapping["market_value"]]) if "market_value" in mapping else None
        pnl = parse_number(cells[mapping["unrealized_pnl"]]) if "unrealized_pnl" in mapping else None
        symbol = label.split()[0].upper().strip("*")
        if value is None:
            log.warning("%s has no price on the page; keeping it with market_value null.",
                        symbol)
        positions.append({
            "symbol": symbol,
            "quantity": int(quantity) if float(quantity).is_integer() else quantity,
            "market_value": None if value is None else round(value, 2),
            "unrealized_pnl": None if pnl is None else round(pnl, 2),
        })
    return positions


def build_payload(account: str, positions: list[dict]) -> dict:
    return {
        "account": account,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "positions": positions,
    }


def write_json(path, payload: dict) -> None:
    """Write via a temp file so a reader never sees a half-written file."""
    import json
    import os
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
    os.replace(temporary, path)


def _quantity(value) -> str:
    if isinstance(value, float):
        return f"{value:,}".rstrip("0").rstrip(".")
    return f"{value:,}"


def _money(value) -> str:
    return "--" if value is None else f"{value:,.2f}"


def holdings_block(positions: list[dict], indent: str = "") -> list[str]:
    """Just the header and rows - no totals footer, so callers decide what follows."""
    headers = ["Symbol", "Quantity", "Market value", "Unrealized P/L"]
    rows = [[p["symbol"], _quantity(p["quantity"]),
             _money(p["market_value"]), _money(p["unrealized_pnl"])] for p in positions]
    widths = [max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))]
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)),
             "  ".join("-" * w for w in widths)]
    lines += ["  ".join(str(v).ljust(widths[i]) for i, v in enumerate(row)) for row in rows]
    return [indent + line.rstrip() for line in lines]


def render_table(positions: list[dict], account: str) -> str:
    """One flat table - used for a CSV, which has no account headings."""
    lines = [f"\n{account}  ({len(positions)} holdings)", ""] if account else []
    lines += holdings_block(positions)
    priced = [p["market_value"] for p in positions if p["market_value"] is not None]
    if priced:
        lines += ["", f"Total of priced holdings: {sum(priced):,.2f}"]
        if len(priced) != len(positions):
            lines.append(f"({len(positions) - len(priced)} holding(s) had no price)")
    return "\n".join(lines)



# --------------------------------------------------------------------------- #
# Splitting the grid into accounts
# --------------------------------------------------------------------------- #

def _clean_account_label(label: str, link_text: str, number_pattern: str) -> tuple[str, str]:
    """'REGULAR ACCOUNT Z12345678 Manage dividends' -> ('REGULAR ACCOUNT', 'Z12345678')."""
    text = " ".join(label.replace("\n", " ").split())
    text = re.sub(rf"\s*({link_text})\s*", " ", text).strip()
    match = re.search(number_pattern, text)
    number = match.group(1) if match else ""
    name = text[:match.start()].strip() if match else text
    return name, number


def to_accounts(headers, rows, config: dict) -> list[dict]:
    """Group the grid into accounts, each with its holdings, cash and total.

    Fidelity lists every account in one grid: an 'Account: NAME NUMBER' row, then that
    account's holdings, a cash line, and an account total. Accounts with nothing in them
    say so instead of listing rows.
    """
    columns = config["columns"]
    patterns = {k: re.compile(v) for k, v in config.get("row_patterns", {}).items()}
    mapping = map_columns(headers, columns)
    if "symbol" not in mapping or "quantity" not in mapping:
        raise ParseError(f"Could not find the symbol/quantity columns in {headers}.")

    symbol_col = mapping["symbol"]
    value_col = mapping.get("market_value")
    last_column = max(mapping.values())
    accounts: list[dict] = []
    current: dict | None = None
    grand_total = None

    for cells in rows:
        if len(cells) <= last_column:
            continue
        label = " ".join(cells[symbol_col].split("\n")[0].split())
        full = " ".join(cells[symbol_col].replace("\n", " ").split())
        value = parse_number(cells[value_col]) if value_col is not None else None

        account_match = patterns["account"].match(full) if "account" in patterns else None
        if account_match:
            name, number = _clean_account_label(
                account_match.group(1), config.get("account_link_text", "(?!x)x"),
                config.get("account_number", "([A-Z]?\\d{5,})$"))
            current = {"name": name, "number": number, "cash": None,
                       "total": None, "positions": []}
            accounts.append(current)
            continue
        if current is None:
            continue
        if "grand_total" in patterns and patterns["grand_total"].match(full):
            grand_total = value
            continue
        if "account_total" in patterns and patterns["account_total"].match(full):
            current["total"] = value
            continue
        if "cash" in patterns and patterns["cash"].match(full):
            current["cash"] = value
            continue
        if "empty_account" in patterns and patterns["empty_account"].match(full):
            continue

        holding = to_positions(headers, [cells], columns, None)
        if holding:
            current["positions"].append(holding[0])
        elif label:
            log.debug("Ignored row: %s", label[:40])

    for account in accounts:
        account["positions_value"] = round(
            sum(p["market_value"] for p in account["positions"]
                if p["market_value"] is not None), 2)
        account["complete"] = _totals_agree(account)
        if not account["complete"]:
            log.warning("%s: rows add up to %.2f but the account total is %.2f - some "
                        "holdings did not load.", account["name"] or "account",
                        account["positions_value"] + (account["cash"] or 0.0),
                        account["total"])
    return accounts, grand_total


def _totals_agree(account: dict, tolerance: float = 0.02) -> bool:
    """True when the rows we read explain the account total the broker printed."""
    if account.get("total") is None:
        return True  # nothing to check against
    if any(p["market_value"] is None for p in account["positions"]):
        return True  # an unpriced holding makes the sum meaningless
    read = account["positions_value"] + (account["cash"] or 0.0)
    return abs(read - account["total"]) <= tolerance


def render_accounts(accounts: list[dict], grand_total: float | None) -> str:
    lines: list[str] = []
    for account in accounts:
        title = account["name"] or "Account"
        if account["number"]:
            title += f"  ({account['number']})"
        lines += ["", title, "-" * max(len(title), 58)]
        if account["positions"]:
            lines += holdings_block(account["positions"], indent="  ")
        else:
            lines.append("  no holdings")
        unpriced = sum(1 for p in account["positions"] if p["market_value"] is None)
        if unpriced:
            lines.append(f"  ({unpriced} holding(s) not priced by Fidelity)")
        if account["cash"] is not None:
            lines.append(f"  Cash: {_money(account['cash'])}")
        if account["total"] is not None:
            lines.append(f"  Account total: {_money(account['total'])}")
        if account.get("complete") is False:
            read = account["positions_value"] + (account["cash"] or 0.0)
            lines.append(f"  ** only {_money(read)} of that was read - {_money(account['total'] - read)} "
                         "of holdings did not load **")

    held = sum(len(a["positions"]) for a in accounts)
    funded = sum(1 for a in accounts if a["positions"] or a["cash"])
    lines += ["", "=" * 58,
              f"{len(accounts)} accounts ({funded} with holdings or cash), {held} holdings"]
    if grand_total is not None:
        lines.append(f"Grand total: {_money(grand_total)}")
    return "\n".join(lines)


def build_account_payload(account_name: str, accounts: list[dict],
                          grand_total: float | None) -> dict:
    """JSON with per-account detail, plus the flat positions list for the trading app."""
    flat = []
    for account in accounts:
        for holding in account["positions"]:
            flat.append({**holding, "account": account["name"],
                         "account_number": account["number"]})
    payload = build_payload(account_name, flat)
    payload["accounts"] = accounts
    payload["grand_total"] = grand_total
    return payload


def to_accounts_from_columns(headers, rows, config: dict):
    """Group a CSV export by its Account Name / Account Number columns.

    Fidelity's download covers every account you can see, so this gives the same
    per-account view as the web page without a browser being involved.
    """
    columns = config["columns"]
    lookup = map_columns(headers, {
        "name": config.get("account_name_columns", ["account name"]),
        "number": config.get("account_number_columns", ["account number"]),
    })
    if "name" not in lookup and "number" not in lookup:
        return [], None

    accounts: list[dict] = []
    index: dict[tuple[str, str], dict] = {}
    for cells in rows:
        holding = to_positions(headers, [cells], columns, config.get("skip_row_pattern"))
        if not holding:
            continue
        name = cells[lookup["name"]].strip() if "name" in lookup else ""
        number = cells[lookup["number"]].strip() if "number" in lookup else ""
        key = (name, number)
        if key not in index:
            index[key] = {"name": name or "Account", "number": number,
                          "cash": None, "total": None, "positions": []}
            accounts.append(index[key])
        index[key]["positions"].append(holding[0])

    for account in accounts:
        account["positions_value"] = round(
            sum(p["market_value"] for p in account["positions"]
                if p["market_value"] is not None), 2)
        account["total"] = account["positions_value"] or None
    grand_total = round(sum(a["positions_value"] for a in accounts), 2) if accounts else None
    return accounts, grand_total


def find_csv(folders, patterns, newer_than: float = 0.0):
    """Newest CSV across the folders, optionally only ones touched after `newer_than`."""
    found = []
    for folder in folders:
        directory = Path(folder).expanduser()
        if not directory.is_dir():
            continue
        for pattern in patterns:
            for path in directory.glob(pattern):
                if path.is_file() and path.stat().st_mtime > newer_than:
                    found.append(path)
    return max(found, key=lambda f: f.stat().st_mtime) if found else None
