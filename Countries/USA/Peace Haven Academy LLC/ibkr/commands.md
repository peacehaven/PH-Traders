academy - ibkr account U5881272 (USA.ACADEMY)
==============================================================


    cd ~/ibkr-per-company/academy

checking the connection
------------------------------
    python ib.py test

looking - nothing is ever sent
------------------------------
    python ib.py holdings
    python ib.py orders

buying
------------------------------
    python ib.py buy iau 1 --limit 82.00
    python ib.py buy aapl 2 --market
    python ib.py buy iau 1 --limit 82.00 --tif gtc
    python ib.py buy iau --dollars 250 --market

selling
------------------------------
    python ib.py sell iau 1 --limit 84.00
    python ib.py sell nio 50 --market

cancelling - the id comes from the orders command
------------------------------
    python ib.py cancel 123456789

first time only:  pip install "ibind[oauth]"
