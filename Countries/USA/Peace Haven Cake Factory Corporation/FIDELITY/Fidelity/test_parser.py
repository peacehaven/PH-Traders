"""Tests for the table -> JSON logic. No browser or brokerage login needed.

Run with:  pytest -q
"""
import pytest

from broker_positions import (
    ParseError,
    build_payload,
    extract_grid,
    map_columns,
    parse_number,
    parse_positions,
)

# Looks like a typical brokerage positions table: sort-icon text in headers,
# symbol + company name in one cell, $ and % columns side by side, a total row.
HTML_TABLE = """
<table class="positions">
  <thead><tr>
    <th>Symbol <span>sort</span></th><th>Description</th><th>Quantity</th>
    <th>Price</th><th>Day's Gain/Loss</th><th>Market Value</th>
    <th>Unrealized Gain/Loss ($)</th><th>Unrealized Gain/Loss (%)</th>
  </tr></thead>
  <tbody>
    <tr><td><a>AAPL</a><div>APPLE INC</div></td><td>Apple</td><td>100</td>
        <td>$245.00</td><td>+$150.00</td><td>$24,500.00</td><td>+$3,200.00</td><td>15.02%</td></tr>
    <tr><td>MSFT</td><td>Microsoft</td><td>50</td>
        <td>$450.00</td><td>-$20.00</td><td>$22,500.00</td><td>$4,100.00</td><td>22.3%</td></tr>
    <tr><td>XYZ</td><td>Loser Co</td><td>10.5</td>
        <td>$3.00</td><td>--</td><td>$31.50</td><td>($120.25)</td><td>-79%</td></tr>
    <tr><td>Account Total</td><td></td><td></td>
        <td></td><td></td><td>$47,031.50</td><td>$7,179.75</td><td></td></tr>
  </tbody>
</table>
"""

# Many modern sites render "tables" as divs with ARIA roles.
HTML_ARIA_GRID = """
<div role="grid">
  <div role="row">
    <div role="columnheader">Ticker</div><div role="columnheader">Shares</div>
    <div role="columnheader">Value</div><div role="columnheader">Gain/Loss</div>
  </div>
  <div role="row">
    <div role="gridcell">NVDA</div><div role="gridcell">1,200</div>
    <div role="gridcell">$150,000.00</div><div role="gridcell">\u2212$2,500.00</div>
  </div>
</div>
"""


@pytest.mark.parametrize("text, expected", [
    ("$24,500.00", 24500.0),
    ("+$3,200.00", 3200.0),
    ("-$3,200.00", -3200.0),
    ("($120.25)", -120.25),
    ("$(120.25)", -120.25),
    ("\u2212$2,500.00", -2500.0),       # unicode minus sign
    ("1,200", 1200.0),
    ("$3,200.00\n+15.02%", 3200.0),     # first number in a multi-line cell
    ("--", None),
    ("\u2014", None),
    ("", None),
    ("N/A", None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_html_table_becomes_positions():
    headers, rows = extract_grid(HTML_TABLE)
    positions = parse_positions(headers, rows)
    assert positions == [
        {"symbol": "AAPL", "quantity": 100, "market_value": 24500.0, "unrealized_pnl": 3200.0},
        {"symbol": "MSFT", "quantity": 50, "market_value": 22500.0, "unrealized_pnl": 4100.0},
        {"symbol": "XYZ", "quantity": 10.5, "market_value": 31.5, "unrealized_pnl": -120.25},
    ]


def test_dollar_column_wins_over_percent_and_day_columns():
    headers, _ = extract_grid(HTML_TABLE)
    cols = {field: headers[i] for field, i in map_columns(headers).items()}
    assert cols["unrealized_pnl"] == "Unrealized Gain/Loss ($)"
    assert cols["market_value"] == "Market Value"


def test_aria_grid_is_supported():
    headers, rows = extract_grid(HTML_ARIA_GRID)
    assert parse_positions(headers, rows) == [
        {"symbol": "NVDA", "quantity": 1200, "market_value": 150000.0, "unrealized_pnl": -2500.0},
    ]


def test_missing_required_column_raises():
    html = "<table><tr><th>Symbol</th><th>Price</th></tr><tr><td>AAPL</td><td>1</td></tr></table>"
    headers, rows = extract_grid(html)
    with pytest.raises(ParseError, match="quantity"):
        parse_positions(headers, rows)


def test_custom_aliases_from_config():
    html = ("<table><tr><th>Sym</th><th>Units Held</th><th>Worth</th></tr>"
            "<tr><td>SPY</td><td>3</td><td>$1,650.00</td></tr></table>")
    headers, rows = extract_grid(html)
    aliases = {"symbol": ["sym"], "market_value": ["worth"]}
    assert parse_positions(headers, rows, aliases) == [
        {"symbol": "SPY", "quantity": 3, "market_value": 1650.0, "unrealized_pnl": None},
    ]


def test_payload_matches_trading_app_schema():
    payload = build_payload("Fidelity Account", [{"symbol": "AAPL"}])
    assert payload["account"] == "Fidelity Account"
    assert payload["positions"] == [{"symbol": "AAPL"}]
    assert "as_of" in payload


def test_selectors_accept_css_xpath_and_fallback_lists():
    from broker_positions import to_locators
    assert to_locators("input[type='password']") == [("css selector", "input[type='password']")]
    assert to_locators(["xpath://label[normalize-space()='User ID']/following::input[1]",
                        "input[id*='user' i]", ""]) == [
        ("xpath", "//label[normalize-space()='User ID']/following::input[1]"),
        ("css selector", "input[id*='user' i]"),
    ]


# --- Fidelity CSV holdings reports --------------------------------------------------------

FIDELITY_CSV = '''Account Number,Account Name,Symbol,Description,Quantity,Last Price,Current Value,Today's Gain/Loss Dollar,Total Gain/Loss Dollar,Total Gain/Loss Percent,Type
X12345678,Individual,AAPL,APPLE INC,100.000,$245.00,$24500.00,+$150.00,+$3200.00,+15.02%,Cash
X12345678,Individual,SPAXX**,FIDELITY GOVERNMENT MMKT,1250.120,$1.00,$1250.12,$0.00,n/a,n/a,Cash
X12345678,Individual,Pending Activity,Pending Activity,,,-$500.00,,,,
"Date downloaded 09/23/2026"

Brokerage services are provided by Fidelity Brokerage Services LLC.
'''

def test_fidelity_csv_becomes_positions(tmp_path):
    from broker_positions import read_csv_grid
    path = tmp_path / "Portfolio_Positions_Sep-23-2026.csv"
    path.write_text(FIDELITY_CSV, encoding="utf-8")
    headers, rows = read_csv_grid(path)
    assert headers[2] == "Symbol"
    positions = parse_positions(headers, rows)
    assert positions == [
        {"symbol": "AAPL", "quantity": 100, "market_value": 24500.0, "unrealized_pnl": 3200.0},
        {"symbol": "SPAXX", "quantity": 1250.12, "market_value": 1250.12, "unrealized_pnl": None},
    ]


def test_fidelity_credentials_are_read_from_environment():
    from broker_positions import credentials_from_env
    env = {"FIDELITY_USERNAME": "fid_id", "FIDELITY_PASSWORD": "f_pw"}
    assert credentials_from_env({"credentials_env_prefix": "FIDELITY"}, env) == ("fid_id", "f_pw")
    assert credentials_from_env({"credentials_env_prefix": "FIDELITY"}, {}) == (None, None)


def test_fidelity_config_is_ready_to_use():
    import json
    from pathlib import Path
    cfg = json.loads(Path(__file__).with_name("config.fidelity.json").read_text(encoding="utf-8"))
    assert cfg["login_url"].startswith("https://digital.fidelity.com")
    assert cfg["positions_source"] == "table"
    assert cfg["credentials_env_prefix"] == "FIDELITY"
    assert cfg["chrome_profile_dir"] == ".chrome-profile-fidelity"
