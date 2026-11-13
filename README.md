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

To fetch and save the latest portfolio and order snapshot, set `COINBASE_API_KEY` and
`COINBASE_API_SECRET` in the environment or the project-root `.env` file, then
run:

```powershell
python .\scripts\coinbase_account_summary.py
```

The script loads `.env` without overriding existing environment variables.
It is the main Coinbase entry point and returns a nonzero exit code on fetch,
serialization, logging setup, or artifact write failure. It prints the saved
manifest path on success. Portfolios and orders share one UTC reference time.

The latest manifest is `src/artifacts/coinbase/manifest.json`; its artifact path
points to a complete snapshot in `snapshots/<snapshot_id>.json`. Manifest metadata
includes counts, checksum, byte size, schema version, UTC timestamps, and trace ID.
Failed runs preserve the previous manifest; previous snapshots are retained.
Read [the artifact format](src/artifacts/README.md) for serialization details.
Shared artifact helpers live in `src/commons`.

Structured JSON logs (one JSON object per line) and timed trace events are written to
`src/artifacts/coinbase/log.json` and stderr. Traces cover the run,
credential loading, Coinbase components, each API request, and artifact saving.
Trace and parent span IDs link events together; failed spans record error types
without logging exception payloads or credentials. Log files rotate at 5 MB with
three backups. Generated snapshots and logs are ignored by Git.

Optional output locations and watchlist products:

```powershell
python .\scripts\coinbase_account_summary.py --artifacts-dir .\src\artifacts\coinbase --log-dir .\src\artifacts\coinbase --watchlist BTC-USDC ETH-USDC
```

### Local HTML dashboard

Render the latest saved snapshot, then open the resulting HTML file:

```powershell
python .\scripts\coinbase_dashboard.py
Start-Process .\src\artifacts\coinbase\report.html
```

The dashboard shows portfolio totals and allocation, per-wallet quantities and
holds, in-progress orders, recently filled orders, and watchlist movement. It
reads the manifest and verifies the snapshot's checksum and identity before
rendering. It uses saved artifacts only; no API credentials, web server, browser
JavaScript, or external assets are needed. Run the renderer again after fetching
a new snapshot to refresh the report. A failed render preserves the previous HTML.

Use `--artifacts-dir`, `--output path/to/report.html`, or `--log-dir` to change
locations. Logs default to `<artifacts-dir>/log.json`; `--log-dir` overrides only
the log directory. The default output is `<artifacts-dir>/report.html`. The layout adapts
to mobile screens and printing, and large data tables scroll horizontally.

The reusable body is `src.coinbase.reporting.render_coinbase_body(snapshot)`;
shared HTML components and the document template live in `src/commons/reporting.py`
and `src/commons/templates/summary.html`. Core tables and styles are inline so
the same report sections can later be composed into weekly emails. Email sending
is not part of this command. Weekly figures describe estimated market movement
of current quantities, not actual profit/loss. Order values retain their product's
quote units; portfolio values use USDC. Hover over numbers to see exact saved
values where the displayed amount is rounded.

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
than returned as empty results. Responses requiring additional authentication
also raise `CoinbaseClientError`. Duplicate wallet or portfolio IDs are rejected
to prevent inflated totals. Pagination streams one page at a time, and repeated
watchlist products reuse prices within each call while preserving input order.

Run the offline tests with:

```console
python -m unittest discover -s src/tests
```
