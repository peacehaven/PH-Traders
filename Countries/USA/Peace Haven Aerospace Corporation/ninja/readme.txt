ninjatrader
==============================================================

what this is
------------------------------
  ninja.py trades futures through the ninjatrader 8 desktop application
  running on this machine. it does not talk to a server on the internet,
  so ninjatrader must be open whenever a command is used.

  commands, flags and options are all lowercase. instrument names are the
  exception: they must match ninjatrader exactly, contract month included,
  and need quotes because of the space - "mes 03-26".

before the first command
------------------------------
  1. install ninjatrader 8 desktop. the web portal at ninjatrader.com has
     no api at all; only the desktop application does.

  2. open it, then:
         tools > options > general > tick "enable ati server" > ok
     and RESTART ninjatrader. it does not take effect until you do.

  3. connect a feed:
         connections > simulated data feed
     that puts you on sim101, the simulator, where nothing costs money.

  4. set up the script:
         cd ~/ninjatrader
         cp ninja_config.example.json ninja_config.json
         python ninja.py status

     "ninjatrader answered on localhost:36973" means it is working.
     "ninjatrader is not listening" means step 2 did not take.

the commands
------------------------------
  checking
      python ninja.py status                  is ninjatrader reachable
      python ninja.py cash                    cash value, buying power, realized p&l
      python ninja.py position "mes 03-26"    long, short or flat

  buying and selling
      python ninja.py buy  "mes 03-26" 1 --limit 5900
      python ninja.py buy  "mes 03-26" 1 --market
      python ninja.py sell "mes 03-26" 1 --limit 6000
      python ninja.py sell "mes 03-26" 1 --market --tif gtc

  after an order
      python ninja.py order ORDER_ID          status, filled, average fill price
      python ninja.py cancel ORDER_ID
      python ninja.py flatten "mes 03-26"     close the position at market
      python ninja.py cancelall               cancel every working order

  the order id is printed when the order is accepted, and you can set your
  own with --order-id if you prefer.

the config
------------------------------
  ninja_config.json holds four settings:

      host            localhost - where ninjatrader is running
      port            36973 - the ati port, matches tools > options
      account         sim101 - which account orders go to
      max_quantity    2 - the most contracts one order may carry

  change account only when you genuinely mean to trade real money, and
  raise max_quantity deliberately rather than to make an order fit.

safety, because futures are leveraged
------------------------------
  - the config defaults to sim101, the simulator.
  - a non-simulator account is refused unless --live is added to the
    command. that check runs before the script even connects.
  - orders larger than max_quantity are refused.
  - every order prints in words, says whether the account is live or the
    simulator, and waits for a typed yes. anything else abandons it.

  one mes contract carries several times the exposure of a share trade,
  and a market order on a futures contract fills at whatever price is
  available. prove every command on sim101 before using --live.

what it cannot do
------------------------------
  - it cannot start ninjatrader, connect a feed, or log in for you.
  - it cannot list working orders: the ati reports one order at a time by
    id, so use the control center's orders tab for the full picture.
  - some broker connections do not report cash value or buying power
    through the ati; those lines then read "not reported", which is not
    an error.

when something is wrong
------------------------------
  "ninjatrader is not listening"
        ninjatrader is closed, or the ati server is not enabled, or it was
        enabled without restarting the application.

  "refusing: ... is not the simulator"
        the config names a live account. add --live only if that is really
        what you want.

  "ninjatrader rejected the command"
        usually a wrong instrument name or an unconnected data feed. the
        control center's log tab gives the reason.

  no reply within 10 seconds
        ninjatrader is busy or stuck; check the control center.

the account in the web portal showed no buying power, so a live order
would be rejected until it is funded. sim101 needs no funding.
