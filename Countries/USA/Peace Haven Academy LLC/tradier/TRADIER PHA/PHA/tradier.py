"""tradier.py - Tradier brokerage from the command line.

    python tradier.py accounts                    show configured accounts
    python tradier.py SANDBOX quote AAPL          last price
    python tradier.py SANDBOX holdings            cash, balances, positions
    python tradier.py SANDBOX orders              working orders
    python tradier.py SANDBOX buy AAPL 1 --limit 220
    python tradier.py SANDBOX buy AAPL 1 --market
    python tradier.py SANDBOX sell AAPL 1 --limit 225 --tif gtc
    python tradier.py SANDBOX cancel 123456
    python tradier.py all holdings

  options (1 contract = 100 shares):
    python tradier.py SANDBOX expirations AAPL
    python tradier.py SANDBOX chain AAPL 2026-10-16 --type call
    python tradier.py SANDBOX option buy_to_open  AAPL 2026-10-16 call 220 1 --limit 3.50
    python tradier.py SANDBOX option sell_to_close AAPL 2026-10-16 call 220 1 --limit 4.00

Config lives in tradier_accounts.json beside this file.
"""
import json, pathlib, sys

import requests

CONFIG = pathlib.Path(__file__).with_name('tradier_accounts.json')
BASES = {'sandbox': 'https://sandbox.tradier.com/v1', 'production': 'https://api.tradier.com/v1'}


def load():
    if not CONFIG.exists():
        sys.exit(f"Missing {CONFIG}. Run setup_tradier.py first.")
    return json.loads(CONFIG.read_text())


class Tradier:
    def __init__(self, token, account, env='sandbox'):
        self.base, self.account = BASES[env], account
        self.h = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}

    def _call(self, method, path, **kw):
        r = requests.request(method, f"{self.base}{path}", headers=self.h, timeout=30, **kw)
        try:
            body = r.json()
        except ValueError:
            r.raise_for_status()
            raise SystemExit(f"Unexpected reply from Tradier: {r.text[:200]}")
        for key in ('errors', 'error'):
            if isinstance(body, dict) and body.get(key):
                raise SystemExit(f"Tradier error: {body[key]}")
        if r.status_code >= 400:
            raise SystemExit(f"Tradier HTTP {r.status_code}: {str(body)[:300]}")
        return body

    def balances(self):
        return (self._call('GET', f'/accounts/{self.account}/balances') or {}).get('balances') or {}

    def positions(self):
        data = (self._call('GET', f'/accounts/{self.account}/positions') or {}).get('positions')
        return as_list(data, 'position')

    def orders(self):
        data = (self._call('GET', f'/accounts/{self.account}/orders') or {}).get('orders')
        return as_list(data, 'order')

    def quote(self, symbol):
        rows = self.quotes(symbol)
        return rows[0] if rows else None

    def quotes(self, symbols):
        """One call for many symbols; returns a list of quote dicts."""
        if not symbols:
            return []
        joined = symbols if isinstance(symbols, str) else ','.join(symbols)
        data = (self._call('GET', '/markets/quotes', params={'symbols': joined}) or {}).get('quotes')
        return as_list(data, 'quote')

    def place(self, symbol, side, qty, order_type, price=None, tif='day'):
        payload = {'class': 'equity', 'symbol': symbol, 'side': side, 'quantity': str(qty),
                   'type': order_type, 'duration': tif}
        if price is not None:
            payload['price'] = f"{price:.2f}"
        return (self._call('POST', f'/accounts/{self.account}/orders', data=payload) or {}).get('order') or {}

    def expirations(self, symbol):
        data = (self._call('GET', '/markets/options/expirations',
                           params={'symbol': symbol, 'includeAllRoots': 'true'}) or {}).get('expirations')
        return [str(d) for d in as_list(data, 'date')]

    def chain(self, symbol, expiration):
        data = (self._call('GET', '/markets/options/chains',
                           params={'symbol': symbol, 'expiration': expiration, 'greeks': 'true'}) or {}).get('options')
        return as_list(data, 'option')

    def place_option(self, underlying, occ, side, qty, order_type, price=None, tif='day', preview=False):
        payload = {'class': 'option', 'symbol': underlying, 'option_symbol': occ, 'side': side,
                   'quantity': str(qty), 'type': order_type, 'duration': tif}
        if price is not None:
            payload['price'] = f"{price:.2f}"
        if preview:
            payload['preview'] = 'true'
        return (self._call('POST', f'/accounts/{self.account}/orders', data=payload) or {}).get('order') or {}

    def cancel(self, order_id):
        return (self._call('DELETE', f'/accounts/{self.account}/orders/{order_id}') or {}).get('order') or {}


def as_list(data, key):
    """Tradier returns 'null', a single object, or a list - normalise to a list."""
    if not data or data in ('null', 'none'):
        return []
    inner = data.get(key) if isinstance(data, dict) else data
    if not inner:
        return []
    return inner if isinstance(inner, list) else [inner]


import re as _re
from datetime import date as _date

OCC_RE = _re.compile(r'^([A-Z]{1,6})(\d{6})([CP])(\d{8})$')
OPTION_SIDES = ('buy_to_open', 'sell_to_open', 'buy_to_close', 'sell_to_close')


def occ_symbol(root, expiry, right, strike):
    """AAPL, 2026-10-16, call, 220 -> AAPL261016C00220000"""
    y, m, d = (int(x) for x in expiry.split('-'))
    r = right.lower()[0]
    if r not in 'cp':
        raise ValueError("right must be call or put")
    return f"{root.upper()}{y % 100:02d}{m:02d}{d:02d}{r.upper()}{int(round(float(strike) * 1000)):08d}"


def describe_occ(occ):
    """AAPL261016C00220000 -> 'AAPL 16 Oct 2026 220 CALL'"""
    m = OCC_RE.match(str(occ).upper())
    if not m:
        return str(occ)
    root, ymd, cp, strike = m.groups()
    d = _date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:]))
    k = int(strike) / 1000
    k = f"{k:g}"
    return f"{root} {d.day} {d.strftime('%b %Y')} {k} {'CALL' if cp == 'C' else 'PUT'}"


def is_option(symbol):
    return bool(OCC_RE.match(str(symbol).upper()))


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


OPEN_STATES = ('open', 'partially_filled', 'pending', 'submitted', 'accepted')


def cmd_holdings(t, args):
    b = t.balances()
    print(f"Account {t.account}")
    print(f"  Total value     {num(b.get('total_equity')):>12,.2f}")
    print(f"  Cash            {num(b.get('total_cash')):>12,.2f}")
    bp = (b.get('cash') or {}).get('cash_available') or (b.get('margin') or {}).get('stock_buying_power')
    if bp is not None:
        print(f"  Available       {num(bp):>12,.2f}")
    rows = t.positions()
    if not rows:
        print("  No open positions.")
        return
    prices = {}
    try:
        for q in t.quotes([str(p.get('symbol', '')) for p in rows if p.get('symbol')]):
            prices[str(q.get('symbol', '')).upper()] = num(q.get('last'))
    except SystemExit:
        raise
    except Exception:
        pass                                     # quotes unavailable: show what we can

    w = max([8] + [len(describe_occ(p.get('symbol', ''))) + 1 for p in rows])
    print(f"  {'Symbol':<{w}}{'Qty':>8}{'Avg cost':>10}{'Last':>9}{'Cost basis':>12}"
          f"{'Mkt value':>12}{'Unrealized':>12}{'%':>8}")
    tot_cost = tot_value = 0.0
    for p in rows:
        sym, qty = str(p.get('symbol', '')).upper(), num(p.get('quantity'))
        cost = num(p.get('cost_basis'))
        last = prices.get(sym)
        mult = 100 if is_option(sym) else 1
        avg = cost / (qty * mult) if qty else 0.0
        tot_cost += cost
        if last:
            value = last * qty * mult
            pnl = value - cost
            pct = (pnl / cost * 100) if cost else 0.0
            tot_value += value
            print(f"  {describe_occ(sym):<{w}}{qty:>8.0f}{avg:>10.2f}{last:>9.2f}{cost:>12,.2f}"
                  f"{value:>12,.2f}{pnl:>+12,.2f}{pct:>+7.1f}%")
        else:
            tot_value += cost
            print(f"  {describe_occ(sym):<{w}}{qty:>8.0f}{avg:>10.2f}{'n/a':>9}{cost:>12,.2f}"
                  f"{'n/a':>12}{'n/a':>12}{'':>8}")
    pnl = tot_value - tot_cost
    pct = (pnl / tot_cost * 100) if tot_cost else 0.0
    print(f"  {'TOTAL':<{w}}{'':>8}{'':>10}{'':>9}{tot_cost:>12,.2f}"
          f"{tot_value:>12,.2f}{pnl:>+12,.2f}{pct:>+7.1f}%")


def cmd_orders(t, args):
    rows = [o for o in t.orders() if str(o.get('status', '')).lower() in OPEN_STATES]
    if not rows:
        print("No open orders.")
        return
    for o in rows:
        print(f"{o.get('id'):<10} {o.get('symbol',''):<8} {o.get('side',''):<5} "
              f"{num(o.get('exec_quantity')):.0f}/{num(o.get('quantity')):.0f}  "
              f"{o.get('type','')} {o.get('price','')}  {o.get('duration','')}  {o.get('status','')}")


def cmd_quote(t, args):
    q = t.quote(args.symbol)
    if not q:
        sys.exit(f"No quote for {args.symbol}.")
    print(f"{q.get('symbol')} {q.get('description','')}")
    print(f"  last {num(q.get('last')):,.2f}  bid {num(q.get('bid')):,.2f}  ask {num(q.get('ask')):,.2f}"
          f"  change {num(q.get('change')):+,.2f}")


def held_qty(t, symbol):
    return sum(num(p.get('quantity')) for p in t.positions()
               if str(p.get('symbol', '')).upper() == symbol)


def committed_sells(t, symbol):
    total = 0.0
    for o in t.orders():
        if (str(o.get('status', '')).lower() in OPEN_STATES
                and str(o.get('symbol', '')).upper() == symbol
                and str(o.get('side', '')).lower().startswith('sell')):
            total += num(o.get('quantity')) - num(o.get('exec_quantity'))
    return total


def cmd_trade(t, args, side):
    symbol, qty = args.symbol, args.quantity
    price = None if args.market else args.limit
    kind = 'market' if price is None else f"limit {price:.2f}"
    held = held_qty(t, symbol)
    print(f"{side.title()} {qty} {symbol} at {kind}, {args.tif}, account {t.account}")
    if side == 'sell':
        committed = committed_sells(t, symbol)
        print(f"  holding {held:.0f} {symbol}, {committed:.0f} already committed to open sells")
        if qty > held - committed:
            sys.exit(f"Refusing: only {held - committed:.0f} free to sell.")
    else:
        q = t.quote(symbol)
        if q:
            est = num(q.get('ask')) or num(q.get('last'))
            if price is not None:
                est = price
            print(f"  {symbol} last {num(q.get('last')):,.2f} -> estimated cost {est * qty:,.2f}")
    if input("Type yes to send: ").strip().lower() != 'yes':
        sys.exit("Not sent.")
    res = t.place(symbol, side, qty, 'market' if price is None else 'limit', price, args.tif)
    print(f"RESULT: order {res.get('id')} {res.get('status', '')}".rstrip())


def cmd_expirations(t, args):
    dates = t.expirations(args.symbol)
    if not dates:
        sys.exit(f"No option expirations for {args.symbol}.")
    print(f"{args.symbol} expirations:")
    for d in dates[:args.limit]:
        print(f"  {d}")
    if len(dates) > args.limit:
        print(f"  ... {len(dates) - args.limit} more (use --limit)")


def cmd_chain(t, args):
    rows = t.chain(args.symbol, args.expiry)
    if not rows:
        sys.exit(f"No chain for {args.symbol} {args.expiry}. Check the date with: expirations {args.symbol}")
    q = t.quote(args.symbol)
    spot = num(q.get('last')) if q else 0.0
    rows = [r for r in rows if str(r.get('option_type', '')).lower().startswith(args.type[0])]
    rows.sort(key=lambda r: num(r.get('strike')))
    if spot and args.near:
        rows.sort(key=lambda r: abs(num(r.get('strike')) - spot))
        rows = sorted(rows[:args.near], key=lambda r: num(r.get('strike')))
    print(f"{args.symbol} {args.expiry} {args.type.upper()}S   underlying last {spot:,.2f}")
    print(f"  {'Strike':>8}{'Bid':>8}{'Ask':>8}{'Last':>8}{'Volume':>9}{'Open int':>10}{'Delta':>8}  OCC symbol")
    for r in rows:
        g = r.get('greeks') or {}
        mark = ' <' if spot and abs(num(r.get('strike')) - spot) == min(abs(num(x.get('strike')) - spot) for x in rows) else ''
        print(f"  {num(r.get('strike')):>8.2f}{num(r.get('bid')):>8.2f}{num(r.get('ask')):>8.2f}"
              f"{num(r.get('last')):>8.2f}{num(r.get('volume')):>9.0f}{num(r.get('open_interest')):>10.0f}"
              f"{num(g.get('delta')):>+8.2f}  {r.get('symbol', '')}{mark}")


def held_option_qty(t, occ):
    return sum(num(p.get('quantity')) for p in t.positions() if str(p.get('symbol', '')).upper() == occ)


def cmd_option(t, args):
    side = args.side
    occ = occ_symbol(args.symbol, args.expiry, args.right, args.strike)
    price = None if args.market else args.limit
    qty = args.quantity
    label = describe_occ(occ)
    kind = 'market' if price is None else f"limit {price:.2f}"

    # --- guards that run before anything touches the network ---
    if qty > args.max:
        sys.exit(f"Refusing: {qty} contracts is over the per-order cap of {args.max} (raise it with --max).")
    if side == 'sell_to_open' and not args.allow_writing:
        sys.exit("Refusing: sell_to_open WRITES an option, with losses that can far exceed the premium.\n"
                 "  If you really mean to write it, add --allow-writing.")

    held = held_option_qty(t, occ)
    if side == 'sell_to_close' and qty > held:
        sys.exit(f"Refusing: you hold {held:.0f} long {label} contracts, can't sell_to_close {qty}.")
    if side == 'buy_to_close' and qty > -held:
        sys.exit(f"Refusing: you are short {max(0, -held):.0f} {label} contracts, can't buy_to_close {qty}.")

    print(f"{side.replace('_', ' ').upper()} {qty} x {label}  ({occ})")
    print(f"  at {kind}, {args.tif}, account {t.account}")
    oq = t.quote(occ)
    if oq:
        print(f"  option bid {num(oq.get('bid')):.2f}  ask {num(oq.get('ask')):.2f}  last {num(oq.get('last')):.2f}")
    per = price if price is not None else (num(oq.get('ask')) if oq and side.startswith('buy') else num(oq.get('bid')) if oq else 0)
    if per:
        verb = 'cost' if side.startswith('buy') else 'credit'
        print(f"  estimated {verb}: {per:.2f} x {qty} x 100 = {per * qty * 100:,.2f}  (before fees)")

    # --- Tradier preview: validates the order and returns its cost, places nothing ---
    pv = t.place_option(args.symbol, occ, side, qty, 'market' if price is None else 'limit',
                        price, args.tif, preview=True)
    if pv:
        cost = pv.get('order_cost') or pv.get('cost')
        fees = pv.get('commission') or pv.get('fees')
        bits = [f"status {pv.get('status', 'ok')}"]
        if cost not in (None, ''):
            bits.append(f"order cost {num(cost):,.2f}")
        if fees not in (None, ''):
            bits.append(f"fees {num(fees):,.2f}")
        print("  Tradier preview:", ", ".join(bits))
        if str(pv.get('result', 'true')).lower() == 'false':
            sys.exit(f"Tradier preview rejected it: {pv}")

    if input("Type yes to send: ").strip().lower() != 'yes':
        sys.exit("Not sent.")
    res = t.place_option(args.symbol, occ, side, qty, 'market' if price is None else 'limit', price, args.tif)
    print(f"RESULT: order {res.get('id')} {res.get('status', '')}".rstrip())


def cmd_cancel(t, args):
    if input(f"Cancel order {args.order_id} in {t.account}? Type yes: ").strip().lower() != 'yes':
        sys.exit("Not cancelled.")
    res = t.cancel(args.order_id)
    print(f"RESULT: order {res.get('id')} {res.get('status','')}".rstrip())


def build_parser():
    import argparse
    p = argparse.ArgumentParser(prog='tradier.py')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('accounts')
    for cmd in ('holdings', 'orders'):
        q = sub.add_parser(cmd); q.add_argument('account', type=str.upper)
    q = sub.add_parser('quote'); q.add_argument('account', type=str.upper); q.add_argument('symbol', type=str.upper)
    for cmd in ('buy', 'sell'):
        q = sub.add_parser(cmd)
        q.add_argument('account', type=str.upper)
        q.add_argument('symbol', type=str.upper)
        q.add_argument('quantity', type=int)
        g = q.add_mutually_exclusive_group(required=True)
        g.add_argument('--limit', type=float)
        g.add_argument('--market', action='store_true')
        q.add_argument('--tif', choices=('day', 'gtc'), default='day', type=str.lower)
    q = sub.add_parser('cancel'); q.add_argument('account', type=str.upper); q.add_argument('order_id')

    q = sub.add_parser('expirations', help='option expiry dates for a symbol')
    q.add_argument('account', type=str.upper); q.add_argument('symbol', type=str.upper)
    q.add_argument('--limit', type=int, default=12)

    q = sub.add_parser('chain', help='option chain near the current price')
    q.add_argument('account', type=str.upper); q.add_argument('symbol', type=str.upper)
    q.add_argument('expiry', help='YYYY-MM-DD, from the expirations command')
    q.add_argument('--type', choices=('call', 'put'), default='call', type=str.lower)
    q.add_argument('--near', type=int, default=10, help='strikes closest to the price (0 = all)')

    q = sub.add_parser('option', help='place a single-leg option order')
    q.add_argument('account', type=str.upper)
    q.add_argument('side', choices=OPTION_SIDES, type=str.lower)
    q.add_argument('symbol', type=str.upper, help='underlying, e.g. AAPL')
    q.add_argument('expiry', help='YYYY-MM-DD')
    q.add_argument('right', choices=('call', 'put'), type=str.lower)
    q.add_argument('strike', type=float)
    q.add_argument('quantity', type=int, help='contracts (1 contract = 100 shares)')
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument('--limit', type=float, help='price per share, e.g. 3.50 = $350 per contract')
    g.add_argument('--market', action='store_true')
    q.add_argument('--tif', choices=('day', 'gtc'), default='day', type=str.lower)
    q.add_argument('--max', type=int, default=5, help='per-order contract cap (default 5)')
    q.add_argument('--allow-writing', action='store_true', help='permit sell_to_open')
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = load()
    known = {n.upper() for n in cfg} | {'ALL'}
    if argv and argv[0].upper() in known and len(argv) > 1:      # allow "SANDBOX holdings" too
        argv = [argv[1], argv[0]] + argv[2:]
    # this folder holds one account, so the name may be left out entirely
    if len(cfg) == 1 and argv and argv[0].lower() != 'accounts':
        only = next(iter(cfg))
        if len(argv) == 1 or argv[1].upper() not in known:
            argv = [argv[0], only] + argv[1:]
    args = build_parser().parse_args(argv)
    if args.command == 'accounts':
        for n, a in cfg.items():
            print(f"{n:<10} {a['env']:<11} {a['account']}")
        return 0
    if args.account == 'ALL' and args.command in ('buy', 'sell', 'cancel', 'option'):
        sys.exit("Refusing to trade on 'all' - name one account.")
    if args.account != 'ALL' and args.account not in cfg:
        sys.exit(f"Unknown account {args.account}; have: {', '.join(cfg)}")
    names = list(cfg) if args.account == 'ALL' else [args.account]
    for name in names:
        a = cfg[name]
        print(f"\n=== {name} ({a['env']}) ===")
        t = Tradier(a['token'], a['account'], a['env'])
        try:
            if args.command in ('buy', 'sell'):
                cmd_trade(t, args, args.command)
            else:
                {'holdings': cmd_holdings, 'orders': cmd_orders, 'quote': cmd_quote,
                 'cancel': cmd_cancel, 'expirations': cmd_expirations, 'chain': cmd_chain,
                 'option': cmd_option}[args.command](t, args)
        except SystemExit:
            raise
        except Exception as e:
            print(f"FAILED: {e}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
