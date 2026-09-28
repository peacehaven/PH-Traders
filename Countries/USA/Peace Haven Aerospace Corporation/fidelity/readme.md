# Fidelity holdings → terminal + JSON

Reads your Fidelity positions and prints them as a table, then saves
`positions.fidelity.json` for your trading application:

```json
{
  "account": "Fidelity Account",
  "as_of": "2026-09-24T12:30:00+00:00",
  "positions": [
    { "symbol": "AAPL", "quantity": 100, "market_value": 24500.0, "unrealized_pnl": 3200.0 }
  ]
}
```

## Signing in

`python fidelity.py` opens an ordinary Chrome window and waits while **you** sign in.
Nothing is attached to the browser during the sign-in: the script only watches the local
debugging port over plain HTTP to notice when your account pages appear, then attaches to
read them. Fidelity's bot detection reacts to a driver being attached during sign-in, so
doing it in this order is what keeps the login clean.

No password is stored by default, so there is nothing in this folder to leak.

`python fidelity.py --fill` is the opposite order - attach first, type a stored login - and
Fidelity usually refuses it with "Sorry, we can't complete this action right now". It is
there in case that changes. To use it:

```
python fidelity.py --save-login     # stored by Windows Credential Manager, not here
python fidelity.py --fill
python fidelity.py --forget-login   # remove it again
```

## What you get

Every account is listed with its holdings, cash line and account total, then a grand total:

```
REGULAR ACCOUNT  (Z1234xxxx)
----------------------------------------------------------
  Symbol  Quantity  Market value  Unrealized P/L
  ------  --------  ------------  --------------
  AAPL    100       24,500.00     3,200.00
  Cash: 1,234.56
  Account total: 25,734.56

Peace Haven Properties LLC  (Z9876xxxx)
----------------------------------------------------------
  no holdings

==========================================================
9 accounts (2 with holdings or cash), 4 holdings
Grand total: 27,110.12
```

The JSON keeps an `accounts` array with the same detail, plus the flat `positions` list your
trading application already reads, each entry tagged with its account.

## How it works, and why it is built this way

Fidelity refuses sign-ins from a browser that a driver launched, so this project never
launches one. It opens an **ordinary Chrome** window with a debugging port, you sign in
there yourself, and the script then reads the page you are looking at. Consequences:

- **No password is stored.** There is no `.env` here and nothing to leak.
- Chrome keeps running after the script exits, with its own profile in `chrome-profile/`.
  While that window is open and the session alive, later runs need nothing from you.
- When Fidelity times the session out, sign in again in that window.

## Setup

Install Python 3.10+ (tick "Add python.exe to PATH") and Google Chrome. Then:

```
python fidelity.py
```

The first run creates `.venv`, installs three packages, opens Chrome and waits for you to
sign in. After that it goes to Positions, prints your holdings and writes the JSON.

On Windows you can also just double-click `run.bat`.

## Everyday use

```
python fidelity.py            # table + positions.fidelity.json
python fidelity.py --json     # also print the JSON
python fidelity.py -v         # verbose, shows which columns were matched
```

## If the browser route is blocked

Fidelity's bot detection flags any session a driver is attached to, and answers the sign-in
with "Sorry, we can't complete this action right now" - even in a normal Chrome window,
because the DevTools connection itself is detectable. When that happens, use the export:

1. Sign in to Fidelity in your everyday browser.
2. Open Positions, make sure the account selector shows **All accounts**.
3. Click **Download**.
4. Then:

```
python fidelity.py --csv ~/Downloads/Portfolio_Positions_Sep-24-2026.csv
```

The CSV carries Account Number and Account Name, so you get the same per-account terminal
output and the same JSON - no browser involved, nothing to detect, nothing to break when
Fidelity changes its markup.

## What each file does

| File | Purpose |
|---|---|
| `fidelity.py` | Entry point: gather, print, save |
| `chrome_session.py` | Finds/starts Chrome, attaches, waits for sign-in, grabs the grid |
| `positions.py` | Turns the grid or CSV into accounts and positions (no browser; fully tested) |
| `config.json` | URLs, port, profile folder, column names |
| `test_positions.py` | 24 tests: `python -m pytest -q` |
| `_bootstrap.py` | Makes `python fidelity.py` work without activating `.venv` |

## Reading Fidelity's grid

Fidelity uses ag-Grid, which splits one visual row across two containers: the pinned Symbol
column and the scrolling numbers, with the same `row-index` in each. This joins on that
index and keys cells by `col-id` (`sym`, `qty`, `curVal`, `totGLStk`), which is why a
column reorder on the site cannot silently shift your numbers.

A row counts as a holding when it has a symbol **and** a quantity, so group headings,
subtotals, cash lines and pending activity drop out. A security Fidelity cannot price shows
`--`; the holding is kept with `market_value: null` rather than disappearing.

## Watch mode

Downloading becomes the only manual step:

```
python fidelity.py --watch
```

Leave it running. Each time you click Download on the Positions page, the new CSV is picked
up within a few seconds, printed per account, and saved to the JSON. Folders and filename
patterns are `csv_watch_dirs` / `csv_patterns` in `config.json`. Ctrl+C stops it.

## Timing

All pauses live in `config.json` under `delays`, in seconds:

| Setting | What it waits for | Default |
|---|---|---|
| `before_typing_seconds` | the sign-in form to finish loading before anything is typed | 1.5 |
| `per_character_seconds` | between keystrokes, so the form's JavaScript sees each one | 0.08 |
| `between_fields_seconds` | after the username, before the password | 0.8 |
| `before_submit_seconds` | after the password, before the button is clicked | 1.2 |
| `after_sign_in_seconds` | after your sign-in is noticed, before the page is read | 3.0 |
| `after_page_load_seconds` | after opening the positions page | 2.0 |

Values are clamped to 0-30 seconds; a missing or invalid one falls back to the default.
Raise `after_sign_in_seconds` if the grid is sometimes half-loaded, and
`per_character_seconds` to 0 to send each field in one go.

## Config

| Key | Meaning |
|---|---|
| `positions_url` | Page the script reads |
| `chrome.debug_port` | Port Chrome listens on (9222) |
| `chrome.profile_dir` | Chrome profile for this project |
| `chrome.path` | Set only if Chrome is installed somewhere unusual |
| `sign_in_timeout_seconds` | How long it waits for you to sign in (300) |
| `columns` | Column ids/names for each JSON field |
| `skip_row_pattern` | Rows to ignore |

## Notes

- Nothing is written when a run fails, so the previous JSON stays intact. Use `as_of` to
  spot stale data.
- `chrome-profile/` holds your signed-in session — it is in `.gitignore`; don't share it.
- Automated reading is outside Fidelity's terms even with you doing the sign-in. A few runs
  a day is sensible; a tight loop is not. The sanctioned hands-off route is Fidelity Access
  through an aggregator such as Plaid or SnapTrade.
