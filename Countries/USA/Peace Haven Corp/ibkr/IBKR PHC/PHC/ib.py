"""ib.py - one script for every IBKR account. No environment variables needed.

    python ib.py accounts                      list configured accounts
    python ib.py PHC test                      connection check
    python ib.py PHC holdings                  cash, P&L, positions
    python ib.py PHC orders                    working orders
    python ib.py PHC buy SMCI 10 --limit 37    limit buy
    python ib.py PHC buy SMCI 10 --market      market buy
    python ib.py PHC sell NIO 100 --limit 3.6  limit sell (never more than you hold)
    python ib.py PHC cancel 123456789
    python ib.py all holdings                  every account in turn
"""
import base64, json, pathlib, re, sys

CONFIG = pathlib.Path(__file__).with_name('ib_accounts.json')


def load():
    if not CONFIG.exists():
        sys.exit(f"Missing {CONFIG}. See the setup note.")
    return json.loads(CONFIG.read_text())


def dh_prime(path):
    lines = pathlib.Path(path).read_text().splitlines()
    der = base64.b64decode("".join(l for l in lines if not l.startswith('-----')))
    from Crypto.Util.asn1 import DerSequence
    seq = DerSequence(); seq.decode(der)
    return format(seq[0], 'x')


def connect(name, cfg):
    from ibind import IbkrClient
    from ibind.oauth.oauth1a import OAuth1aConfig
    a = cfg[name]
    folder = pathlib.Path(a['folder']).expanduser()
    oauth = OAuth1aConfig(
        consumer_key=a['consumer_key'], access_token=a['access_token'],
        access_token_secret=a['access_token_secret'],
        signature_key_fp=str(folder / 'private_signature.pem'),
        encryption_key_fp=str(folder / 'private_encryption.pem'),
        dh_prime=dh_prime(folder / 'dhparam.pem'), dh_generator=2,
        realm=a.get('realm', 'limited_poa'), maintain_oauth=False,
        init_brokerage_session=False)   # we start it ourselves, with retries
    c = IbkrClient(use_oauth=True, oauth_config=oauth, auto_register_shutdown=False)
    acct = c.portfolio_accounts().data[0]['accountId']
    c.session_error = start_session(c)      # None when the trading session is usable
    return c, acct


def start_session(c, attempts=3):
    """Open the brokerage session. Returns None on success, else the reason.

    Portfolio reads (balances, positions) work without it; orders do not.
    """
    import time
    last = None
    for attempt in range(attempts):
        try:
            c.initialize_brokerage_session(compete=True)
            c.receive_brokerage_accounts()
            return None
        except Exception as e:
            last = str(e)
            if attempt < attempts - 1:
                time.sleep(2)
    return last


def money(v):
    return f"{v:,.2f}"


def cmd_test(c, acct, args):
    print(f"SUCCESS: connected, account {acct}")
    if getattr(c, 'session_error', None):
        print("  NOTE: reads work, but the trading session could not start, so orders will fail:")
        print(f"        {short(c.session_error)}")


def short(text, limit=160):
    text = str(text)
    return text if len(text) <= limit else text[:limit] + '...'


def require_session(c):
    if getattr(c, 'session_error', None):
        sys.exit("Cannot trade: the brokerage session did not start.\n"
                 f"  {short(c.session_error)}\n"
                 "  Try again shortly, and log out of TWS / Client Portal for this account first.")


def cmd_holdings(c, acct, args):
    s = c.portfolio_summary(acct).data
    amt = lambda k: (s.get(k) or {}).get('amount') or 0
    print(f"Account {acct}")
    print(f"  Net liquidation {money(amt('netliquidation')):>12}")
    print(f"  Cash            {money(amt('totalcashvalue')):>12}   available {money(amt('availablefunds'))}")
    rows = [p for p in c.positions(acct).data if p.get('position')]
    if not rows:
        print("  No open positions.")
        return
    print(f"  {'Symbol':<8}{'Qty':>8}{'Avg cost':>11}{'Last':>10}{'Mkt value':>12}{'Unrealized':>12}")
    for p in rows:
        print(f"  {p.get('contractDesc',''):<8}{p.get('position',0):>8.0f}{p.get('avgCost',0):>11.2f}"
              f"{p.get('mktPrice',0) or 0:>10.2f}{p.get('mktValue',0):>12.2f}{p.get('unrealizedPnl',0):>+12.2f}")


CLOSED = ('filled', 'cancelled', 'canceled', 'rejected', 'expired')


def _retrying(c, call):
    """IBKR sometimes needs /iserver/accounts queried again before it accepts a call."""
    import time
    for attempt in range(3):
        try:
            return call()
        except Exception as e:
            if 'query /accounts first' not in str(e) or attempt == 2:
                raise
            c.receive_brokerage_accounts()
            time.sleep(1)


def open_orders(c, symbol=None):
    rows = (_retrying(c, lambda: c.live_orders(force=True)).data or {}).get('orders', []) or []
    out = []
    for o in rows:
        if str(o.get('status', '')).lower() in CLOSED:
            continue
        if symbol and str(o.get('ticker', '')).upper() != symbol:
            continue
        out.append(o)
    return out


def cmd_orders(c, acct, args):
    if getattr(c, 'session_error', None):
        print(f"Cannot list orders: trading session unavailable ({short(c.session_error)})")
        return
    rows = open_orders(c)
    if not rows:
        print("No open orders.")
        return
    for o in rows:
        print(f"{o.get('orderId'):<12} {o.get('ticker',''):<8} {o.get('side',''):<5} "
              f"{o.get('filledQuantity',0):.0f}/{o.get('totalSize',0):.0f}  "
              f"{o.get('orderType','')} {o.get('price','')}  {o.get('timeInForce','')}  {o.get('status','')}")


def held_qty(c, acct, symbol):
    return sum(p.get('position', 0) for p in c.positions(acct).data
               if str(p.get('contractDesc', '')).upper() == symbol)


def held_conid(c, acct, symbol):
    for p in c.positions(acct).data:
        if str(p.get('contractDesc', '')).upper() == symbol and p.get('position'):
            return int(p['conid'])
    return None


def last_price(c, conid):
    """Last trade price: live snapshot (which needs warming up), else the latest 1-minute bar."""
    import time
    def f(v):
        try:
            return float(str(v).lstrip('CHB'))
        except (TypeError, ValueError):
            return None
    for _ in range(8):
        try:
            rows = c.live_marketdata_snapshot([str(conid)], ['31', '84', '86']).data or []
        except Exception:
            rows = []
        row = rows[0] if rows else {}
        price = f(row.get('31')) or f(row.get('86')) or f(row.get('84'))
        if price:
            return price
        time.sleep(1)
    try:
        bars = (c.marketdata_history_by_conid(str(conid), '1min', period='1d').data or {}).get('data') or []
        if bars:
            return float(bars[-1]['c'])
    except Exception:
        pass
    return None


def cmd_trade(c, acct, args, side):
    from ibind.client.ibkr_utils import OrderRequest
    require_session(c)
    symbol, qty = args.symbol, args.quantity
    price = None if args.market else args.limit
    conid = held_conid(c, acct, symbol) if side == 'SELL' else None
    if conid is None:
        conid = int(c.stock_conid_by_symbol(symbol).data[symbol])
    if getattr(args, 'dollars', None):
        if side != 'BUY':
            sys.exit("--dollars is for buying; sell by share quantity.")
        ref = price if price is not None else last_price(c, conid)
        if not ref:
            sys.exit(f"Can't size a ${args.dollars:,.2f} order: no price for {symbol}.")
        qty = int(args.dollars // ref)                          # whole shares only - IBKR's API
        if qty < 1:                                              # rejects fractional stock orders
            sys.exit(f"${args.dollars:,.2f} doesn't buy one share of {symbol} at ~{ref:,.2f}.\n"
                     f"  IBKR's API can't place fractional stock orders; the minimum is 1 share "
                     f"(~${ref:,.2f}).")
        print(f"  ${args.dollars:,.2f} at ~{ref:,.2f} = {qty} whole shares of {symbol} "
              f"(~${qty * ref:,.2f}; the ${args.dollars - qty * ref:,.2f} left over can't be invested via the API)")
    held = held_qty(c, acct, symbol)
    committed = sum(o.get('remainingQuantity', 0) for o in open_orders(c, symbol)
                    if str(o.get('side', '')).upper() == 'SELL')
    kind = 'market' if price is None else f"limit {price:.2f}"
    qs = f"{qty:g}"
    print(f"{side.title()} {qs} {symbol} at {kind}, {args.tif}, account {acct}")
    print(f"  holding {held:g} {symbol}, {committed:g} already committed to open sells")
    if side == 'SELL' and qty > held - committed:
        sys.exit(f"Refusing: only {held - committed:g} free to sell (IBKR would treat this as a short).")
    if input("Type yes to send: ").strip().lower() != 'yes':
        sys.exit("Not sent.")
    req = OrderRequest(conid=conid, side=side, quantity=qty,
                       order_type='MKT' if price is None else 'LMT',
                       acct_id=acct, price=price, tif=args.tif)
    answers = {}
    while True:
        try:
            result = _retrying(c, lambda: c.place_order(req, answers, acct)).data
            break
        except ValueError as e:
            if 'No answer found for question' not in str(e):
                raise
            q = str(e).split('question: ', 1)[1].strip().strip('"')
            print("IBKR asks:", re.sub('<[^>]+>', ' ', q)[:140].strip())
            if input("Send anyway? [y/N]: ").strip().lower() != 'y':
                sys.exit("Not sent.")
            answers[q] = True
    print("RESULT:", result[0] if isinstance(result, list) and result else result)


def cmd_cancel(c, acct, args):
    require_session(c)
    if input(f"Cancel order {args.order_id} in {acct}? Type yes: ").strip().lower() != 'yes':
        sys.exit("Not cancelled.")
    print(_retrying(c, lambda: c.cancel_order(args.order_id, acct)).data)


def build_parser(names):
    import argparse
    p = argparse.ArgumentParser(prog='ib.py')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('accounts')
    for cmd in ('test', 'holdings', 'orders'):
        q = sub.add_parser(cmd); q.add_argument('account', type=str.upper)
    for cmd in ('buy', 'sell'):
        q = sub.add_parser(cmd)
        q.add_argument('account', type=str.upper)
        q.add_argument('symbol', type=str.upper)
        q.add_argument('quantity', type=float, nargs='?', help='whole shares')
        g = q.add_mutually_exclusive_group(required=True)
        g.add_argument('--limit', type=float)
        g.add_argument('--market', action='store_true')
        if cmd == 'buy':
            q.add_argument('--dollars', type=float, help='budget: buys as many WHOLE shares as it covers')
        q.add_argument('--tif', choices=('DAY', 'GTC'), default='DAY', type=str.upper)
    q = sub.add_parser('cancel'); q.add_argument('account', type=str.upper); q.add_argument('order_id')
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = load()
    if argv and argv[0].upper() in {n.upper() for n in cfg} | {'ALL'}:   # allow "PHC holdings" order too
        argv = [argv[1], argv[0]] + argv[2:] if len(argv) > 1 else argv
    # this folder holds one account, so the name may be left out entirely
    if len(cfg) == 1 and argv and argv[0].lower() != 'accounts':
        only = next(iter(cfg))
        known = {n.upper() for n in cfg} | {'ALL'}
        if len(argv) == 1 or argv[1].upper() not in known:
            argv = [argv[0], only] + argv[1:]
    args = build_parser(list(cfg)).parse_args(argv)
    if args.command in ('buy', 'sell'):
        dollars = getattr(args, 'dollars', None)
        if dollars is not None and args.quantity is not None:
            sys.exit("Give either a share quantity or --dollars, not both.")
        if dollars is None and args.quantity is None:
            sys.exit("Give a share quantity, or --dollars AMOUNT for a buy.")
        if dollars is not None and dollars <= 0:
            sys.exit("--dollars must be positive.")
        if args.quantity is not None and args.quantity <= 0:
            sys.exit("Quantity must be positive.")
        if args.quantity is not None and args.quantity != int(args.quantity):
            sys.exit(f"{args.quantity:g} isn't a whole number. IBKR's API rejects fractional stock orders -\n"
                     "  use a whole share count, or place fractional orders in IBKR's own app.")
    if args.command == 'accounts':
        for n, a in cfg.items():
            print(f"{n:<8} {a['consumer_key']:<10} {a['folder']}")
        return 0
    if args.account == 'ALL' and args.command in ('buy', 'sell', 'cancel'):
        sys.exit("Refusing to trade on 'all' - name one account.")
    if args.account != 'ALL' and args.account not in cfg:
        sys.exit(f"Unknown account {args.account}; have: {', '.join(cfg)}")
    names = list(cfg) if args.account == 'ALL' else [args.account]
    for name in names:
        print(f"\n=== {name} ===")
        try:
            c, acct = connect(name, cfg)
        except Exception as e:
            print(f"FAILED to connect: {e}")
            continue
        try:
            if args.command in ('buy', 'sell'):
                cmd_trade(c, acct, args, args.command.upper())
            else:
                {'test': cmd_test, 'holdings': cmd_holdings, 'orders': cmd_orders,
                 'cancel': cmd_cancel}[args.command](c, acct, args)
        except SystemExit:
            raise
        except Exception as e:
            print(f"FAILED: {e}")
        finally:
            try: c.close()
            except Exception: pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
