# Personal Workflow Agent

An agentic containerised system to monitor various workflows that I'm currently working on and present that insight as daily/weekly summary email/messages.

Goal is to not login to various websites/apps to monitor the progess but have a single place for all the information and pregress updates.

Starting with 2 projects that I'm currently working on:
1. Numerai Competition
2. Portfolio / Expense report

## Coinbase reporting

`src.coinbase` provides read-only Coinbase Advanced Trade reporting. Install
the dependencies in `requirements.txt` and supply an API key with **view**
permission. Pass credentials explicitly; importing the package does not read
the `.env` file or make requests.

```python
import os
from src.coinbase import CoinbaseClient

client = CoinbaseClient(os.environ["COINBASE_API_KEY"], os.environ["COINBASE_API_SECRET"])
portfolios = client.get_user_portfolios()  # optionally pass a retail portfolio ID
wallets = [wallet for portfolio in portfolios for wallet in portfolio.wallets]
orders = client.get_orders()          # optionally pass a retail portfolio ID
watchlist = client.get_watchlist_movement(["BTC-USDC", "ETH-USDC"])
```

To print the portfolio and order summary directly, set `COINBASE_API_KEY` and
`COINBASE_API_SECRET` in the environment or the project-root `.env` file, then
run:

```powershell
python .\scripts\coinbase_account_summary.py
```

The script loads `.env` without overriding existing environment variables.

Portfolios correspond to Coinbase **retail portfolios**, not the Coinbase
login profile. `get_user_portfolios` includes each wallet's identity, currency,
available balance, hold, active/ready status, USDC valuation and weekly movement
in `portfolio.wallets`. Each portfolio total sums current crypto wallet balances
(available plus held) in USDC; fiat wallets are excluded with a warning and
derivatives are not included. Weekly wallet
and portfolio changes reprice **today's quantities** at market prices from
seven days ago. They are market-movement estimates, not actual profit/loss or
historical account value: trades, transfers, fees and changing balances are
not reconstructed. Values are `Decimal`s; a percentage is `None` if the
previous value is zero.

The client uses Coinbase's `ASSET-USDC` markets and values USDC at face value.
It does not assume USD and USDC are equivalent; missing markets cause
`CoinbaseClientError`. Coinbase names these pairs with the asset first (for
example, `ETH-USDC` and `NMR-USDC`). Market prices are Coinbase
last-traded prices, not guaranteed execution prices. Historical prices use
the target minute's open; if that candle is missing, the latest completed
hourly candle in the preceding 24 hours is used. Missing or stale data is an
error. These are estimates and no candle history is saved.

Orders query both retail Advanced and Simple placement sources. They include
active buy/sell orders and fully filled buy/sell orders whose
last fill was within the rolling seven-day window, including orders created
earlier. Cancelled, expired and failed orders are excluded because Coinbase's
order response does not provide a reliable closure timestamp for them.
For a portfolio-filtered order query, the client also checks the returned
portfolio ID locally because Coinbase documents that the server-side filter is
ignored for CDP keys; missing order portfolio IDs are reported as an error.
Access is limited to the portfolios permitted by the key.
Watchlists are supplied as product IDs, not fetched from the Coinbase app;
their percentage changes compare the current price to prices approximately
24 hours, 7 days and 30 days earlier. Orders and watchlist movements retain
their actual product and quote currency, including non-USDC products. `as_of`, when supplied, sets the
timezone-aware reference time for those windows; current prices are still live,
so it is not a backtesting interface. API and data errors are surfaced rather
than returned as empty results.

Run the offline tests with:

```console
python -m unittest discover -s src/tests -p "test_coinbase*.py"
```