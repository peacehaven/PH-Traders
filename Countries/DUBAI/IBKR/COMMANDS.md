# IBKR Commands - Dubai PHE

## Setup

cd ~/ibkr
python setup_ib.py
python ib.py accounts

## Account

python ib.py UAE.PHE test
python ib.py UAE.PHE holdings
python ib.py UAE.PHE orders

## Trading

python ib.py UAE.PHE buy SMCI 10 --limit 37
python ib.py UAE.PHE buy SMCI 10 --market
python ib.py UAE.PHE sell NIO 100 --limit 3.6
python ib.py UAE.PHE cancel 123456789

## Holdings

python ib.py all holdings
