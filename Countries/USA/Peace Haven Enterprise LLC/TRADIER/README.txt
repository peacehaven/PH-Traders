Tradier - one folder per company
============================================================

Each folder holds its own copy of the script and only that company's
account. Because a folder contains a single account, the account name can
be left out of every command: "python tradier.py holdings" is enough.

  SANDBOX      VA75673164   paper money, practise here first
  PHC          6YB92742     real money
  PHA          6YB90990     real money
  PHE          6YB90994     real money
  PCF          6YB92716     real money
  PHIC         6YB89227     real money
  PROPERTIES   6YB91149     real money
  AEROSPACE    6YB92272     real money

Install once:

    cd ~
    unzip tradier-per-company.zip
    pip install requests

Then, for any company:

    cd ~/tradier-per-company/PHC
    python tradier.py holdings

COMMANDS.txt in each folder lists everything that folder can do.

These files contain live API tokens. Keep the folder off shared drives and
out of any git repository.
