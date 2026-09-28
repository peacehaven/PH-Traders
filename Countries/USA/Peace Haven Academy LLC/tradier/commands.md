PHA - Tradier account 6YB90990   (USA.PHA)
==============================================================


First time only:  pip install requests

Open Git Bash and go to this folder:

    cd ~/tradier-per-company/PHA

Looking (nothing is ever sent):

    python tradier.py holdings          cash, total value, positions with P&L
    python tradier.py orders            working orders, with their ids
    python tradier.py quote AAPL        last, bid and ask

Buying:

    python tradier.py buy AAPL 1 --limit 220.00
    python tradier.py buy AAPL 1 --market
    python tradier.py buy AAPL 1 --limit 220.00 --tif gtc

Selling:

    python tradier.py sell AAPL 1 --limit 225.00
    python tradier.py sell AAPL 1 --market

Cancelling (the id comes from the orders command):

    python tradier.py cancel 123456

Options (1 contract = 100 shares):

    python tradier.py expirations AAPL
    python tradier.py chain AAPL 2026-10-16 --type call
    python tradier.py option buy_to_open   AAPL 2026-10-16 call 220 1 --limit 3.50
    python tradier.py option sell_to_close AAPL 2026-10-16 call 220 1 --limit 4.00

