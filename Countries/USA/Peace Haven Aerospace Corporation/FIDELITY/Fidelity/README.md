# Fidelity Positions → JSON

Signs in to Fidelity with Selenium, downloads your holdings CSV, and writes JSON for your
trading application.

## Credentials

`.env` holds your Fidelity credentials:

```
FIDELITY_USERNAME=...
FIDELITY_PASSWORD=...
```

The configuration selects the `FIDELITY_USERNAME` and `FIDELITY_PASSWORD` pair.

## Fidelity

Sign-in opens a separate Chrome window, types your username and password, and waits while you
clear any verification. Expect a security code on the first run, because
`.chrome-profile-fidelity` is a brand-new browser to Fidelity. Later runs reuse it.

**Holdings come directly from the positions table.** The script reads the rendered Fidelity grid
and prints the holdings in the terminal (`"positions_source": "table"` in
`config.fidelity.json`). The table columns map like this:

| Table column | Output field |
|---|---|
| Symbol | `symbol` |
| Quantity | `quantity` |
| Current Value | `market_value` |
| Total Gain/Loss Dollar | `unrealized_pnl` |

Run **"Fidelity: Get positions"** in VS Code, or `python fid.py`.

If the table is not found, the run fails with a screenshot in `debug/`. Open the positions page
yourself and adjust `selectors.positions_table` if Fidelity changes the grid markup.

## Parsing a CSV you already have

No browser, no login:

```
python broker_positions.py --config config.fidelity.json --csv downloads-fidelity/Portfolio_Positions.csv --out positions.fidelity.json
```

In VS Code, open the CSV file, then run **"Parse the CSV file I have open (no login)"**.
Useful for checking the column mapping, and as a fallback whenever a sign-in breaks.

## Config reference

Selectors are CSS (`#dom-username-input`) or XPath with an `xpath:` prefix, and most keys accept
a list of fallbacks tried in order.

| Key | What it's for |
|---|---|
| `credentials_env_prefix` | Uses the `FIDELITY_*` pair from `.env` |
| `positions_source` | `table` (read the Fidelity page) |
| `chrome_profile_dir` / `download_dir` | Must be unique per broker |
| `selectors.username` / `password` / `submit` | Sign-in form |
| `selectors.different_user_link` | Used when the site shows a remembered username |
| `selectors.logged_in_marker` | Proof of being signed in |
| `selectors.positions_table` | Only used when `positions_source` is `table` |
| `column_aliases` | Header names for each JSON field |
| `skip_row_pattern` | Regex of rows to ignore |

## Safety

- Nothing is written when a run fails, so `positions.fidelity.json` keeps its last good contents. Use
  `as_of` in your trading app to spot stale data.
- `.env`, `.chrome-profile*/`, `downloads*/`, `debug/` and the JSON outputs all contain account
  data or credentials. They are in `.gitignore`. Don't commit, email or upload them; strip them
  out before sharing this folder with anyone.
- Check Fidelity's online terms before automating sign-ins. Frequent scripted logins can
  trigger fraud checks or lock an account.
