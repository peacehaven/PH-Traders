IBKR - one folder per company
============================================================

Each folder holds its own copy of the script and only that company's
credentials. Because a folder contains a single account, the account name
can be left out of every command: "python ib.py holdings" is enough.

  PHE       U23350571   keys in ~/ibkr-phe     trading OK
  PHC       U13903350   keys in ~/ibkr-phc     trading OK
  PHIC      U18417414   keys in ~/ibkr-pcf     reads only (see its COMMANDS.txt)
  ACADEMY   U5881272    keys in ~/ibkr-peace   trading OK
  AERO      U26700634   keys in ~/ibkr-aero    trading OK

The .pem key files are NOT in these folders. They stay in ~/ibkr-phe,
~/ibkr-phc, ~/ibkr-pcf, ~/ibkr-peace and ~/ibkr-aero, and each config
points at its own. Nothing here changes those folders.

Install once:

    cd ~
    unzip ibkr-per-company.zip
    pip install "ibind[oauth]"

Then, for any company:

    cd ~/ibkr-per-company/PHC
    python ib.py test
    python ib.py holdings

COMMANDS.txt in each folder lists everything that folder can do.

These files contain live API tokens. Keep the folder off shared drives and
out of any git repository.
