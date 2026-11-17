# Personal Workflow Agent

An agentic containerised system to monitor various workflows that I'm currently working on and present that insight as daily/weekly summary email/messages.

Goal is to not login to various websites/apps to monitor the progess but have a single place for all the information and pregress updates.

Starting with 2 projects that I'm currently working on:
1. Numerai Competition
2. Portfolio / Expense report

## Separate reports

Build independent Coinbase and Numerai pages from their latest saved snapshots:

```powershell
python .\scripts\workflow_dashboard.py
Start-Process .\src\artifacts\report.html
```

The top-level page is a short index linking to:

- `src/artifacts/coinbase/report.html`: holdings, current prices, orders and watchlist.
- `src/artifacts/numerai/report.html`: account/model performance, round windows and four trend charts.

Each report can also be regenerated independently:

```powershell
python .\scripts\coinbase_dashboard.py
python .\scripts\numerai_dashboard.py
```

The pages are self-contained local HTML with inline formatting, responsive tables
and no browser JavaScript or remote assets. Numerai charts are embedded PNGs,
with reusable files in `src/artifacts/numerai/report_images/`. Install
`requirements.txt` to include Matplotlib for chart generation. Rendering reads
verified artifacts only; run the fetch scripts first when newer data is needed.
Corrupt snapshots fail validation and preserve the previous report. Logs remain
under the respective artifacts directory; the index command logs to
`src/artifacts/log.json`.

Runnable orchestration stays under `scripts/`. Rendering, chart and artifact
helpers remain callable modules under `src/commons`, `src/coinbase` and
`src/numerai`. Browser templates and the email template share the same source
sections; the email wrapper uses presentation tables and inline styles.

## Coinbase Gmail dispatch

Preview a Coinbase-only email from the latest saved Coinbase snapshot:

```powershell
python .\scripts\coinbase_email.py
```

This creates an `.eml` preview and a JSON receipt in
`src/artifacts/coinbase/emails/` without connecting to Gmail, even when
`GMAIL_ENABLED=true`. The email contains HTML and plain-text alternatives;
it includes the snapshot capture time and does not include Numerai data.
The subject uses `REPORT_TIMEZONE` (UTC when unset).

To fetch fresh data and explicitly dispatch through Gmail:

```powershell
python .\scripts\coinbase_account_summary.py
python .\scripts\coinbase_email.py --send
```

Sending requires `GMAIL_ENABLED=true`, `GMAIL_USERNAME`, `GMAIL_APP_PASSWORD`,
and `GMAIL_TO` in the environment or `.env`. `GMAIL_TO` accepts comma-separated
bare email addresses. The app password is used only when sending; a normal
Google account password is not supported. Google documents
[app passwords and their account requirements](https://support.google.com/accounts/answer/185833).
The sender uses [Gmail SMTP over verified TLS on port 465](https://developers.google.com/workspace/gmail/imap/imap-smtp).

Receipts record `preview`, `accepted`, `partially_accepted`, or
`failed_or_uncertain`, with message/snapshot IDs and recipient counts rather
than addresses or SMTP response bodies. SMTP acceptance is not confirmation of
inbox delivery. There are no automatic retries: if acceptance is uncertain,
check Sent mail before retrying to avoid duplicate messages. Each explicit
`--send` invocation creates a new message; no schedule is installed.
Authentication, partial-refusal and timeout paths are covered with mocked SMTP.
No Numerai dispatch entry point is provided.

`--artifacts-dir` selects the Coinbase artifact directory and `--log-dir` can
change the log destination. Mail previews and receipts contain private report
information and must stay under ignored artifacts. Callable MIME composition
lives in `src/coinbase/email_report.py`, Gmail transport in `src/gmail`, and
artifact persistence in `src/commons`.

## Numerai reporting

Reusable Numerai Classic reporting code lives in `src/numerai`; runnable entry
points live in `scripts/`. It covers
account performance, `kaijo_model_01`, its last seven resolved rounds, and its
next seven resolving rounds, including daily and intra-round trend data.

```powershell
python -m scripts.numerai_account_summary
python -m scripts.numerai_export_trends
```

Credentials are read from the existing `.env`. Outputs, manifests and logs go
to `src/artifacts/numerai`; shared artifact and observability code is reused.
The exporter produces nine graph-ready CSV datasets, preserving score names,
versions, dates, values, percentiles and resolved/provisional state.
See [Numerai usage and metric definitions](src/numerai/README.md) for the data
layout, round-selection rules, plotting columns and credential names.

## Coinbase reporting

`src.coinbase` provides read-only Coinbase Advanced Trade reporting. Install
the dependencies in `requirements.txt` and supply an API key with **view**
permission. Pass credentials explicitly; importing the package does not read
the `.env` file or make requests.

```python
import os
from contextlib import closing
from src.coinbase import CoinbaseClient

with closing(CoinbaseClient(os.environ["COINBASE_API_KEY"], os.environ["COINBASE_API_SECRET"])) as client:
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
The HTTP session is closed after fetching, including when a request fails.

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

The default watchlist is `BTC-USDC`, `ETH-USDC`, `NMR-USDC`, and `USDC-CAD`.
Coinbase lists BTC, ETH, and NMR with USDC as the quote currency; `USDC-CAD`
tracks CAD per USDC. The percentage changes retain each market's direction.
`--watchlist` overrides this list; pass `--watchlist` alone to disable it.

Optional output locations and watchlist products:

```powershell
python .\scripts\coinbase_account_summary.py --artifacts-dir .\src\artifacts\coinbase --log-dir .\src\artifacts\coinbase --watchlist BTC-USDC ETH-USDC NMR-USDC USDC-CAD
```

### Local HTML dashboard

Render the latest saved snapshot, then open the resulting HTML file:

```powershell
python .\scripts\coinbase_dashboard.py
Start-Process .\src\artifacts\coinbase\report.html
```

The dashboard shows portfolio totals and allocation, per-wallet quantities and
holds, in-progress orders, recently filled orders, and watchlist movement. It
includes a current price column for wallets (USDC per asset) and watchlist
products (quote currency per base asset, such as CAD per USDC). These are saved
snapshot prices, refreshed by the fetch script. Empty wallets also fetch current
prices; retired assets without a market show an unavailable price. Older
snapshots without saved prices remain readable and display a dash.
The dashboard
reads the manifest and verifies the snapshot's checksum and identity before
rendering. Metadata types and timezone-aware timestamps are validated before
rendering, and orders are sorted by their actual timestamps, including offsets.
It uses saved artifacts only; no API credentials, web server, browser
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
