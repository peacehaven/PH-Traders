"""Tests for the parsing rules. No browser, no login, no account data."""
import json
from pathlib import Path

import pytest

from positions import (
    ParseError,
    build_payload,
    map_columns,
    parse_number,
    read_csv_export,
    read_page,
    render_table,
    to_positions,
)

COLUMNS = json.loads((Path(__file__).with_name("config.json")).read_text())["columns"]
SKIP = json.loads((Path(__file__).with_name("config.json")).read_text())["skip_row_pattern"]

# Shaped like Fidelity's real grid: pinned Symbol column in one container, numbers in
# another, same row-index in both, cells keyed by col-id. Row 2 is an unpriced holding,
# row 3 a subtotal line, row 4 a section heading.
AG_GRID = """
<div class="ag-root">
  <div class="ag-header-row"><div col-id="sym">Symbol</div></div>
  <div class="ag-header-row">
    <div col-id="lstPrStk">Last price</div><div col-id="todGLStk">Today's gain/loss</div>
    <div col-id="totGLStk">Total gain/loss</div><div col-id="curVal">Current value</div>
    <div col-id="qty">Quantity</div>
  </div>
  <div class="ag-pinned-left-cols-container">
    <div role="row" row-index="0"><div col-id="sym">AAPL<div>APPLE INC</div></div></div>
    <div role="row" row-index="1"><div col-id="sym">IAU<div>ISHARES GOLD TR</div></div></div>
    <div role="row" row-index="2"><div col-id="sym">849109103<div>DELISTED CO</div></div></div>
    <div role="row" row-index="3"><div col-id="sym">Total</div></div>
    <div role="row" row-index="4"><div col-id="sym">Individual - TOD X12345678</div></div>
  </div>
  <div class="ag-center-cols-container">
    <div role="row" row-index="0"><div col-id="lstPrStk">$245.00</div>
      <div col-id="todGLStk">+$150.00</div><div col-id="totGLStk">+$3,200.00 15.02%</div>
      <div col-id="curVal">$24,500.00</div><div col-id="qty">100</div></div>
    <div role="row" row-index="1"><div col-id="lstPrStk">$180.00</div>
      <div col-id="todGLStk">-$6.00</div><div col-id="totGLStk">-$9.99 -1.11%</div>
      <div col-id="curVal">$888.00</div><div col-id="qty">4.936</div></div>
    <div role="row" row-index="2"><div col-id="lstPrStk">--</div>
      <div col-id="todGLStk">--</div><div col-id="totGLStk">--</div>
      <div col-id="curVal">--</div><div col-id="qty">250,000</div></div>
    <div role="row" row-index="3"><div col-id="lstPrStk"></div>
      <div col-id="todGLStk"></div><div col-id="totGLStk"></div>
      <div col-id="curVal">$25,388.00</div><div col-id="qty"></div></div>
    <div role="row" row-index="4"><div col-id="lstPrStk"></div>
      <div col-id="todGLStk"></div><div col-id="totGLStk"></div>
      <div col-id="curVal"></div><div col-id="qty"></div></div>
  </div>
</div>
"""

CSV_EXPORT = '''Account Number,Account Name,Symbol,Description,Quantity,Last Price,Current Value,Today's Gain/Loss Dollar,Total Gain/Loss Dollar,Total Gain/Loss Percent,Type
X12345678,Individual,AAPL,APPLE INC,100.000,$245.00,$24500.00,+$150.00,+$3200.00,+15.02%,Cash
X12345678,Individual,SPAXX**,FIDELITY GOVT MMKT,1250.120,$1.00,$1250.12,$0.00,n/a,n/a,Cash
X12345678,Individual,Pending Activity,Pending Activity,,,-$500.00,,,,
"Date downloaded 09/24/2026"

Brokerage services provided by Fidelity Brokerage Services LLC.
'''


@pytest.mark.parametrize("text, expected", [
    ("$24,500.00", 24500.0), ("+$3,200.00 15.02%", 3200.0), ("-$120.25", -120.25),
    ("($120.25)", -120.25), ("\u2212$2,500.00", -2500.0), ("250,000", 250000.0),
    ("--", None), ("", None), ("n/a", None), ("Not priced today", None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_ag_grid_joins_the_two_containers_by_row_index():
    headers, rows = read_page(AG_GRID)
    assert headers[0] == "sym"        # keyed by col-id, not header text
    assert len(rows) == 5             # no row lost in the join


def test_holdings_are_read_and_non_holdings_dropped():
    headers, rows = read_page(AG_GRID)
    assert to_positions(headers, rows, COLUMNS, SKIP) == [
        {"symbol": "AAPL", "quantity": 100, "market_value": 24500.0, "unrealized_pnl": 3200.0},
        {"symbol": "IAU", "quantity": 4.936, "market_value": 888.0, "unrealized_pnl": -9.99},
        # unpriced, but still held
        {"symbol": "849109103", "quantity": 250000, "market_value": None,
         "unrealized_pnl": None},
    ]


def test_dollar_column_wins_over_percent_and_today_columns():
    headers, _ = read_page(AG_GRID)
    chosen = {field: headers[i] for field, i in map_columns(headers, COLUMNS).items()}
    assert chosen["unrealized_pnl"] == "totGLStk"   # not todGLStk
    assert chosen["market_value"] == "curVal"       # not lstPrStk


def test_csv_export_gives_the_same_shape(tmp_path):
    path = tmp_path / "Portfolio_Positions_Sep-24-2026.csv"
    path.write_text(CSV_EXPORT, encoding="utf-8")
    headers, rows = read_csv_export(path)
    assert to_positions(headers, rows, COLUMNS, SKIP) == [
        {"symbol": "AAPL", "quantity": 100, "market_value": 24500.0, "unrealized_pnl": 3200.0},
        {"symbol": "SPAXX", "quantity": 1250.12, "market_value": 1250.12,
         "unrealized_pnl": None},
    ]


def test_missing_columns_explain_themselves():
    with pytest.raises(ParseError, match="quantity"):
        to_positions(["Symbol", "Price"], [["AAPL", "$1.00"]], COLUMNS, SKIP)


def test_payload_and_table():
    holdings = [{"symbol": "AAPL", "quantity": 100, "market_value": 24500.0,
                 "unrealized_pnl": 3200.0},
                {"symbol": "XYZ", "quantity": 5, "market_value": None,
                 "unrealized_pnl": None}]
    payload = build_payload("Fidelity Account", holdings)
    assert payload["account"] == "Fidelity Account" and "as_of" in payload
    assert payload["positions"] == holdings

    table = render_table(holdings, "Fidelity Account")
    assert "AAPL" in table and "24,500.00" in table
    assert "--" in table                       # unpriced holding shown, not hidden
    assert "1 holding(s) had no price" in table


# Two accounts in one grid, the second empty - exactly how Fidelity lists them.
MULTI_ACCOUNT = AG_GRID.replace(
    '<div class="ag-pinned-left-cols-container">',
    '''<div class="ag-pinned-left-cols-container">
    <div role="row" row-index="-2"><div col-id="sym">Account: REGULAR ACCOUNT Z11111111 Manage dividends</div></div>
    <div role="row" row-index="5"><div col-id="sym">Cash HELD IN FCASH</div></div>
    <div role="row" row-index="6"><div col-id="sym">Account total</div></div>
    <div role="row" row-index="8"><div col-id="sym">Account: TRADITIONAL IRA 222222222 Option summary</div></div>
    <div role="row" row-index="9"><div col-id="sym">You do not hold any positions or funds in this account.</div></div>
    <div role="row" row-index="10"><div col-id="sym">Grand total</div></div>'''
).replace(
    '<div class="ag-center-cols-container">',
    '''<div class="ag-center-cols-container">
    <div role="row" row-index="-2"><div col-id="lstPrStk"></div><div col-id="todGLStk"></div>
      <div col-id="totGLStk"></div><div col-id="curVal"></div><div col-id="qty"></div></div>
    <div role="row" row-index="5"><div col-id="lstPrStk"></div><div col-id="todGLStk"></div>
      <div col-id="totGLStk"></div><div col-id="curVal">$1,234.56</div><div col-id="qty"></div></div>
    <div role="row" row-index="6"><div col-id="lstPrStk"></div><div col-id="todGLStk"></div>
      <div col-id="totGLStk"></div><div col-id="curVal">$26,622.56</div><div col-id="qty"></div></div>
    <div role="row" row-index="8"><div col-id="lstPrStk"></div><div col-id="todGLStk"></div>
      <div col-id="totGLStk"></div><div col-id="curVal"></div><div col-id="qty"></div></div>
    <div role="row" row-index="9"><div col-id="lstPrStk"></div><div col-id="todGLStk"></div>
      <div col-id="totGLStk"></div><div col-id="curVal"></div><div col-id="qty"></div></div>
    <div role="row" row-index="10"><div col-id="lstPrStk"></div><div col-id="todGLStk"></div>
      <div col-id="totGLStk"></div><div col-id="curVal">$99,999.00</div><div col-id="qty"></div></div>'''
)

CONFIG = json.loads((Path(__file__).with_name("config.json")).read_text())


def test_grid_is_split_into_accounts():
    from positions import to_accounts
    headers, rows = read_page(MULTI_ACCOUNT)
    accounts, grand_total = to_accounts(headers, rows, CONFIG)

    assert [(a["name"], a["number"]) for a in accounts] == [
        ("REGULAR ACCOUNT", "Z11111111"),     # link text stripped, number split out
        ("TRADITIONAL IRA", "222222222"),
    ]
    assert len(accounts[0]["positions"]) == 3       # holdings land under their account
    assert accounts[0]["cash"] == 1234.56           # the cash line, not a holding
    assert accounts[0]["total"] == 26622.56         # the account total line
    assert accounts[1]["positions"] == []           # the empty account stays listed
    assert grand_total == 99999.0


def test_account_payload_keeps_a_flat_list_for_the_trading_app():
    from positions import build_account_payload, to_accounts
    headers, rows = read_page(MULTI_ACCOUNT)
    accounts, grand_total = to_accounts(headers, rows, CONFIG)
    payload = build_account_payload("Fidelity Account", accounts, grand_total)

    assert len(payload["accounts"]) == 2
    assert [p["symbol"] for p in payload["positions"]] == ["AAPL", "IAU", "849109103"]
    assert all(p["account"] == "REGULAR ACCOUNT" for p in payload["positions"])
    assert payload["grand_total"] == 99999.0


def test_accounts_render_with_empty_ones_shown():
    from positions import render_accounts, to_accounts
    headers, rows = read_page(MULTI_ACCOUNT)
    accounts, grand_total = to_accounts(headers, rows, CONFIG)
    text = render_accounts(accounts, grand_total)
    assert "REGULAR ACCOUNT" in text and "Z11111111" in text
    assert "no holdings" in text          # empty account is not silently dropped
    assert "2 accounts (1 with holdings or cash), 3 holdings" in text


TWO_ACCOUNT_CSV = '''Account Number,Account Name,Symbol,Description,Quantity,Current Value,Total Gain/Loss Dollar
Z11111111,REGULAR ACCOUNT,AAPL,APPLE INC,100,$24500.00,+$3200.00
222222222,TRADITIONAL IRA,IAU,ISHARES GOLD,4.936,$888.00,-$9.99
222222222,TRADITIONAL IRA,SPAXX**,CORE MONEY MARKET,1250.12,$1250.12,n/a
222222222,TRADITIONAL IRA,Pending Activity,Pending Activity,,-$500.00,

Brokerage services provided by Fidelity Brokerage Services LLC.
'''


def test_csv_is_grouped_by_account_too(tmp_path):
    from positions import render_accounts, to_accounts_from_columns
    path = tmp_path / "Portfolio_Positions.csv"
    path.write_text(TWO_ACCOUNT_CSV, encoding="utf-8")
    headers, rows = read_csv_export(path)
    accounts, grand_total = to_accounts_from_columns(headers, rows, CONFIG)

    assert [(a["name"], a["number"], len(a["positions"])) for a in accounts] == [
        ("REGULAR ACCOUNT", "Z11111111", 1),
        ("TRADITIONAL IRA", "222222222", 2),
    ]
    assert grand_total == 26638.12
    assert "TRADITIONAL IRA" in render_accounts(accounts, grand_total)


def test_only_a_signed_in_tab_counts():
    from chrome_session import signed_in_tab
    tabs = [
        {"type": "background_page", "url": "chrome-extension://abc"},
        {"type": "page", "url": "https://digital.fidelity.com/prgw/digital/signin/retail"},
        {"type": "page", "url": "https://digital.fidelity.com/ftgw/digital/portfolio/positions"},
    ]
    assert signed_in_tab(tabs, "/ftgw/digital/")["url"].endswith("positions")
    assert signed_in_tab(tabs[:2], "/ftgw/digital/") is None   # login page does not count


def test_delays_come_from_config_and_are_clamped():
    from chrome_session import delay_for
    config = {"delays": {"per_character_seconds": 0.12, "before_submit_seconds": 999,
                         "after_sign_in_seconds": -5, "between_fields_seconds": "slow"}}
    assert delay_for(config, "per_character_seconds", 0.08) == 0.12
    assert delay_for(config, "before_submit_seconds", 1.2) == 30.0    # capped
    assert delay_for(config, "after_sign_in_seconds", 3.0) == 0.0     # no negatives
    assert delay_for(config, "between_fields_seconds", 0.8) == 0.8    # bad value -> default
    assert delay_for({}, "before_typing_seconds", 1.5) == 1.5         # missing -> default


def test_typing_sends_one_character_at_a_time():
    from chrome_session import type_text

    class FakeBox:
        def __init__(self): self.keys = []
        def send_keys(self, value): self.keys.append(value)

    box = FakeBox()
    type_text(box, "abc", 0.001)
    assert box.keys == ["a", "b", "c"]

    fast = FakeBox()
    type_text(fast, "abc", 0)          # zero delay sends it in one go
    assert fast.keys == ["abc"]


def test_find_csv_picks_the_newest_and_respects_newer_than(tmp_path):
    import os
    import time as _time
    from positions import find_csv

    old = tmp_path / "Portfolio_Positions_old.csv"
    new = tmp_path / "Portfolio_Positions_new.csv"
    old.write_text("x", encoding="utf-8")
    _time.sleep(0.01)
    new.write_text("x", encoding="utf-8")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    assert find_csv([tmp_path], ["Portfolio_Positions*.csv"]) == new
    assert find_csv([tmp_path], ["Portfolio_Positions*.csv"], newer_than=1500) == new
    assert find_csv([tmp_path], ["Portfolio_Positions*.csv"], newer_than=2500) is None
    assert find_csv([tmp_path / "nope"], ["*.csv"]) is None


def test_scrolled_snapshots_merge_into_one_grid():
    """ag-Grid drops rows you scrolled past, so each snapshot holds only part of the list."""
    from positions import read_snapshots

    def snapshot(rows):
        pinned = "".join(f'<div role="row" row-index="{i}"><div col-id="sym">{s}</div></div>'
                         for i, s, _, _ in rows)
        centre = "".join(f'<div role="row" row-index="{i}">'
                         f'<div col-id="qty">{q}</div><div col-id="curVal">{v}</div></div>'
                         for i, _, q, v in rows)
        return (f'<div class="ag-root"><div class="ag-header-row"><div col-id="sym">Symbol</div>'
                f'<div col-id="qty">Quantity</div><div col-id="curVal">Current value</div></div>'
                f'<div class="ag-pinned-left-cols-container">{pinned}</div>'
                f'<div class="ag-center-cols-container">{centre}</div></div>')

    top = snapshot([(0, "AAPL", "100", "$24,500.00"), (1, "IAU", "5", "$888.00")])
    bottom = snapshot([(2, "SMCI", "1", "$41.32"), (3, "NVDA", "2", "$360.00")])

    headers, rows = read_snapshots([top, bottom])
    assert len(rows) == 4                                  # nothing lost between passes
    positions = to_positions(headers, rows, CONFIG["columns"], CONFIG["skip_row_pattern"])
    assert [p["symbol"] for p in positions] == ["AAPL", "IAU", "SMCI", "NVDA"]


def test_account_is_flagged_when_rows_are_missing():
    from positions import _totals_agree
    complete = {"positions": [{"market_value": 41.32}], "positions_value": 41.32,
                "cash": 0.40, "total": 41.72}
    short = {"positions": [{"market_value": 41.32}], "positions_value": 41.32,
             "cash": 0.40, "total": 61.72}
    empty_but_funded = {"positions": [], "positions_value": 0.0, "cash": 0.0, "total": 549.41}
    unpriced = {"positions": [{"market_value": None}], "positions_value": 0.0,
                "cash": 0.0, "total": 549.41}

    assert _totals_agree(complete)
    assert not _totals_agree(short)            # the $20 gap you spotted
    assert not _totals_agree(empty_but_funded)  # a total with no holdings read
    assert _totals_agree(unpriced)              # unpriced rows make the sum meaningless


def test_a_closed_startup_tab_is_skipped():
    """chromedriver can land on Chrome's discarded startup target; we move off it."""
    from chrome_session import use_live_tab

    class Switch:
        def __init__(self, driver): self.driver = driver
        def window(self, handle):
            if handle in self.driver.closed:
                raise Exception("no such window: target window already closed")
            self.driver.current = handle

    class FakeDriver:
        def __init__(self, handles, closed, urls):
            self.window_handles, self.closed, self.urls = handles, closed, urls
            self.current = None
            self.switch_to = Switch(self)
        @property
        def current_url(self): return self.urls[self.current]

    driver = FakeDriver(["dead", "blank", "site"], {"dead"},
                        {"blank": "about:blank",
                         "site": "https://digital.fidelity.com/ftgw/digital/portfolio/positions"})
    use_live_tab(driver, "fidelity.com")
    assert driver.current == "site"          # the dead tab is skipped, the site preferred

    no_site = FakeDriver(["dead", "blank"], {"dead"}, {"blank": "about:blank"})
    use_live_tab(no_site, "fidelity.com")
    assert no_site.current == "blank"        # falls back to any live tab


def test_waiting_for_a_field_that_renders_late():
    """The sign-in fields are drawn by JavaScript, so looking once is not enough."""
    from chrome_session import wait_first_visible

    class Element:
        def is_displayed(self): return True
        def is_enabled(self): return True

    class LateDriver:
        def __init__(self, appear_on): self.calls, self.appear_on = 0, appear_on
        def find_elements(self, by, selector):
            self.calls += 1
            return [Element()] if self.calls >= self.appear_on else []

    assert wait_first_visible(LateDriver(appear_on=3), ["#dom-username-input"], 5) is not None
    assert wait_first_visible(LateDriver(appear_on=999), ["#nope"], 1) is None


def test_shadow_dom_fields_are_found_when_css_misses_them():
    """Fidelity's inputs can sit inside web components; a plain CSS lookup returns nothing."""
    from chrome_session import wait_first_visible

    class Element:
        def is_displayed(self): return True
        def is_enabled(self): return True

    class ShadowDriver:
        def find_elements(self, by, selector): return []        # ordinary lookup: nothing
        def execute_script(self, script, *args): return Element()  # deep lookup: found

    assert wait_first_visible(ShadowDriver(), ["#dom-username-input"], 2) is not None


def test_a_field_that_ignores_keystrokes_is_set_directly():
    from chrome_session import fill_field

    class StubbornBox:
        """Accepts keystrokes but keeps its value empty, like a framework-controlled input."""
        def __init__(self): self.value = ""
        def clear(self): self.value = ""
        def send_keys(self, text): pass
        def get_attribute(self, name): return self.value

    class Driver:
        def __init__(self, box): self.box, self.scripts = box, 0
        def execute_script(self, script, element, text):
            self.scripts += 1
            element.value = text        # the native setter path

    box = StubbornBox()
    driver = Driver(box)
    assert fill_field(driver, box, "my-username", 0) is True
    assert driver.scripts == 1 and box.value == "my-username"


def test_a_normal_field_needs_no_fallback():
    from chrome_session import fill_field

    class GoodBox:
        def __init__(self): self.value = ""
        def clear(self): self.value = ""
        def send_keys(self, text): self.value += text
        def get_attribute(self, name): return self.value

    class Driver:
        def __init__(self): self.scripts = 0
        def execute_script(self, *args): self.scripts += 1

    box, driver = GoodBox(), Driver()
    assert fill_field(driver, box, "abc", 0) is True
    assert driver.scripts == 0 and box.value == "abc"
