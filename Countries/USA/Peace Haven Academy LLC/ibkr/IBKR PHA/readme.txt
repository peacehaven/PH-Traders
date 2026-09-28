academy
==============================================================

ibkr account U5881272 (USA.ACADEMY).
consumer key PEACAPIKY. keys are read from ~/ibkr-peace and are not
copied into this folder.

quick start
------------------------------
    cd ~/ibkr-per-company/academy
    python ib.py test
    python ib.py holdings

commands.txt in this folder lists everything, with examples.

why no account name is needed
------------------------------
  this folder is configured with exactly one account, so the script uses
  it automatically. "python ib.py holdings" is complete as written. the
  account number is printed above every confirmation prompt: that is what
  to check before typing yes.

everything is lowercase
------------------------------
  commands, symbols, flags and account names can all be typed lowercase.
  "python ib.py buy iau 1 --limit 82.00" is the same as the uppercase form.

safety
------------------------------
  real money. every order is printed in words and waits for a typed yes;
  anything else abandons it. a sell is refused if it is larger than the
  free position (holding minus working sell orders). ibkr's api takes
  whole shares only, so fractional quantities are refused before sending.

this folder contains live api credentials. keep it off shared drives and
out of any git repository.
