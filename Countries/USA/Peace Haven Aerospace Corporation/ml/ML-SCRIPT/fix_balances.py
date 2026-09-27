"""Show every balance row Merrill lists (money accounts, margin balance, ...).

Merrill's Balances block holds more than cash and pending activity: a money account
(ML BANK DEPOSIT PROGRAM and friends) can hold the whole value of an account, and there
may be a margin balance too. The script only recognised two labels, so an account could
print as empty while its total said $549.41.

Run this once in the ML-SCRIPT folder:  python fix_balances.py
"""
import ast
import re
import shutil
from pathlib import Path

target = Path("broker_positions.py")
if not target.exists():
    raise SystemExit("Run this in the folder that holds broker_positions.py.")

source = target.read_text(encoding="utf-8")
original = source

# 1. Recognise every balance row, not just two of them.
if "BALANCE_ROW" not in source:
    anchor = "DEFAULT_SKIP_ROWS = "
    if anchor not in source:
        raise SystemExit("Could not find DEFAULT_SKIP_ROWS; send me the top of the file.")
    line_end = source.index("\n", source.index(anchor)) + 1
    source = (source[:line_end]
              + '# Rows in Merrill\'s "Balances" block: money accounts, cash, margin, pending.\n'
                'BALANCE_ROW = re.compile(\n'
                '    r"(?i)^(money accounts?|cash balance|margin balance|pending activity"\n'
                '    r"|[a-z][a-z ]*balance)$")\n'
              + source[line_end:])

old_filter = '''        if name.lower() not in {"cash balance", "pending activity"}:
            continue'''
new_filter = '''        if not BALANCE_ROW.match(name):
            continue'''
if old_filter in source:
    source = source.replace(old_filter, new_filter, 1)
elif "BALANCE_ROW.match(name)" not in source:
    raise SystemExit("Could not find the balance filter in parse_balances; send me that "
                     "function and I'll adjust this.")

# 2. Keep those rows out of the holdings list, so nothing is counted twice.
source = re.sub(r'DEFAULT_SKIP_ROWS = r"\(\?i\)\\b\(total\|subtotal\)\\b"',
                'DEFAULT_SKIP_ROWS = r"(?i)\\\\b(total|subtotal|money accounts?|margin balance)\\\\b"',
                source, count=1)

# 3. Stop the "Skipping row" warnings for rows that are balances, not holdings.
source = source.replace(
    '''            if first_line.lower() not in {"balances", "cash balance", "pending activity"}:''',
    '''            if first_line.lower() != "balances" and not BALANCE_ROW.match(first_line):''', 1)

if source == original:
    print("Nothing to change - this file already handles every balance row.")
    raise SystemExit(0)

ast.parse(source)  # refuse to save a broken file
shutil.copy(target, target.with_suffix(".py.backup"))
target.write_text(source, encoding="utf-8")
print("Patched broker_positions.py (backup: broker_positions.py.backup).\n"
      "Money accounts and margin balances now show under each account, and the\n"
      "balances add up to the account total.")
