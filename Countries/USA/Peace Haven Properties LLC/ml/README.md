# Merrill Positions → JSON

Signs in to Merrill (https://olui2.fs.ml.com/login/signin.aspx) with Selenium, reads the
positions table, and writes JSON for your trading application:

```json
{
  "account": "Merrill Account",
  "as_of": "2026-09-22T09:30:00+00:00",
  "positions": [
    { "symbol": "AAPL", "quantity": 100, "market_value": 24500.0, "unrealized_pnl": 3200.0 }
  ]
}
```

## What you need

- Python 3.10 or newer (on Windows, tick **"Add python.exe to PATH"** when installing)
- Google Chrome
- Visual Studio Code

## Setup in VS Code

1. **Open the folder.** `File → Open Folder…` and choose this `broker-positions` folder.
   Click **Install** when VS Code offers the recommended Python extensions.
2. **Install dependencies.** `Terminal → Run Task… → Setup: create .venv and install requirements`.
3. **Pick the interpreter.** `Ctrl+Shift+P` (Mac: `Cmd+Shift+P`) → `Python: Select Interpreter`
   → choose the one inside `.venv`.
4. **Check the tests.** `Terminal → Run Task… → Run tests` should report `20 passed`.
5. **Add your login.** Copy `.env.example` to a new file named `.env` and fill in your full
   Merrill User ID and password.

`config.json` already points at Merrill's sign-in page.

## First run: sign in and find the table

Open **Run and Debug** (`Ctrl+Shift+D`), pick **"1. Find the positions table (--dump-tables)"**,
and press **F5**.

A separate Chrome window opens (its own profile, not your everyday Chrome) and types
your User ID and password.

- **Expect a one-time device check.** To Merrill this window is a new browser, so it may
  ask you to confirm with a code by text or email, even if your normal Chrome never does.
  Complete it in that window (you have 3 minutes) and choose to remember the device if
  offered. Later runs reuse the same profile, so it shouldn't ask again.
- **Saved user ID.** If the window shows a remembered, masked User ID, the script clicks
  "Log in with a different user ID" and types the one from `.env`.

Once you're signed in, go to your holdings/positions page in that window, click back into
the VS Code terminal, and press **Enter**. The script lists every table on the page:

```
[2] selector: #positionsTable   data rows: 14
    headers: ['Symbol', 'Quantity', 'Price', 'Value', 'Unrealized Gain/Loss ($)']
    mapped:  {'symbol': 'Symbol', 'quantity': 'Quantity', ...}
```

Copy the right selector into `selectors.positions_table` in `config.json`, and copy the
address bar URL of that page into `positions_url`.

## Normal runs

Pick **"2. Get positions -> positions.json"** and press **F5**. The file appears in this
folder. Once that works reliably, **"3. … (headless)"** runs without a window.

From a terminal instead:

```
python broker_positions.py --out positions.json
```

## If sign-in fails

The terminal prints the visible form fields on the page, for example:

```
  <input type="text" id="userIdInput" name="userid" autocomplete="username">
  <button type="button"> Log in
```

Put a matching selector first in the `selectors.username` (or `password` / `submit`) list in
`config.json` and run again. A screenshot and the page HTML are also saved to `debug/`.
They contain account data, so delete them when you're done.

## config.json reference

Selectors are CSS (`#id`, `input[name='x']`) or XPath with an `xpath:` prefix. Most keys
also accept a list of fallbacks, tried in order.

| Key | What it's for |
|---|---|
| `login_url` | Sign-in page (set to Merrill's) |
| `positions_url` | Page with the positions table (fill in after the first run) |
| `selectors.different_user_link` | "Log in with a different user ID" link, used when a saved ID is shown |
| `selectors.username` / `password` / `submit` | Sign-in form, matched by the "User ID" label and "Log in" button |
| `selectors.logged_in_marker` | Proof of being signed in; defaults to a "Log out" link |
| `selectors.login_iframe` / `positions_iframe` | Only if the form or table is inside an iframe |
| `selectors.positions_table` | Selector from the `--dump-tables` output |
| `dismiss_if_present` | Optional pop-ups to click away ("Remind me later", "Not now") |
| `clicks_before_read` | Clicks needed before reading, e.g. a "Positions" tab or "Show all" |
| `column_aliases` | Merrill's header names if they differ, e.g. `{"market_value": ["Value"]}` |
| `skip_row_pattern` | Regex for rows to ignore (defaults skip totals and cash rows) |

## Safety

- The script **never writes partial or empty JSON**. If it can't find the table or a
  required column, it exits with an error and leaves the previous `positions.json` alone.
  Use the `as_of` field in your trading app to detect stale data.
- `.env` (your password) and `.chrome-profile/` (your signed-in session) both give access
  to your account. They're in `.gitignore`; never commit or share them.
- Check Merrill's online terms before automating sign-ins. Frequent scripted logins can
  trigger fraud checks or lock the account; once or a few times a day is gentler than
  every few minutes.
