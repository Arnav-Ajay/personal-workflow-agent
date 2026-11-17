# Numerai Classic performance

`src/numerai` contains callable clients, data models, queries and trend-table
transformations. Command-line parsing, environment loading and execution live
in `scripts/numerai_account_summary.py` and `scripts/numerai_export_trends.py`.

To build the independent Numerai page, run `python scripts/numerai_dashboard.py`
and open `src/artifacts/numerai/report.html`. It includes all four Numerai views
and static PNG trend charts, with provisional rounds clearly labeled.
`python scripts/workflow_dashboard.py` regenerates the separate Coinbase and
Numerai reports and a short index at `src/artifacts/report.html`. Numerai is not
included in Coinbase emails, and has no email-dispatch entry point.
The callable renderer is `src/numerai/reporting.py`.

Run from the repository root:

```powershell
python -m scripts.numerai_account_summary
python -m scripts.numerai_export_trends
```

Direct execution also works:

```powershell
python .\scripts\numerai_account_summary.py
python .\scripts\numerai_export_trends.py
```

The fetcher defaults to `kaijo_model_01` and seven rounds per window. Override
with `--model MODEL_NAME` or `--round-count COUNT` (1–100). Both commands accept
`--artifacts-dir` and `--log-dir`; logs default to the selected artifacts directory.

Credentials are loaded from the environment or project-root `.env`, without
overriding existing environment values. Required: `NUMERAI_PUBLIC_ID` and
`NUMERAI_SECRET` (or NumerAPI's standard `NUMERAI_SECRET_KEY`). Optional
`NUMERAI_USERNAME` checks that the authenticated account matches the intended
account. `NUMERAI_API_KEY_NAME` is only a key label and is not an API credential.
The key needs account read access. These commands perform read-only queries;
they do not upload predictions, change stakes, or claim payouts.

## Artifact structure

```text
src/artifacts/numerai/
  log.json
  manifest.json
  snapshots/<snapshot_id>.json
  trends/<snapshot_id>/
    manifest.json
    account_returns.csv
    account_scores.csv
    account_stake.csv
    model_returns.csv
    model_daily_performance.csv
    model_stake.csv
    round_scores.csv
    round_payouts.csv
    selected_round_score_history.csv
```

The primary manifest references a complete JSON snapshot, with checksum, size,
timestamps, trace ID, and counts. It is published only after fetching and
validating all data. Fetch failures preserve the previous manifest. CSVs are
derived views of that snapshot; their separate manifest records the source
snapshot ID, export trace ID, and each CSV's checksum and size. Files are ignored
by Git. Shared artifact writers live in `src/commons`; logging and timed traces
use `src/observability`. No new SDK or data-analysis dependency is required.

## The four performance views

| Snapshot path under `data` | Contents |
| --- | --- |
| `account.summary` | Numerai's aggregate account returns, NMR gains, total stake, scores and ranks |
| `account.balances` | Available NMR, stake credits, held balances and on-chain balance, kept separate |
| `account.history` | Full API-returned account returns, score/rank and stake time series |
| `model.summary` | Current model stake, actual/theoretical returns, latest reputation/ranks, resolved-round statistics |
| `model.history` | Full API-returned daily performance, return and stake histories |
| `rounds.last_resolved` | Up to seven submitted rounds, latest resolution first, marked resolved by the API |
| `rounds.next_resolving` | Up to seven submitted/scoring rounds, earliest resolution first, still provisional |
| `rounds.history` | Every fetched submitted round, in ascending round-number order |

Both selected windows include detailed intra-round score histories as
`score_history`, along with current scores, percentiles, score dates/days,
resolution flags, stake at risk, payouts, payout factor, and exact score
configuration identities/multipliers. `history` contains the same records;
unselected rounds have latest scores and an empty intra-round history.

Windows count **rounds, not days**. Selection uses the API's `roundResolved`
flag and scheduled resolution timestamp, rather than assuming a fixed 20- or
60-day schedule. Overdue unresolved rounds stay in the resolving queue and have
`overdue: true`. An unscored future round is excluded from the resolving window.
Missing history yields fewer rows, never invented performance. The snapshot
reports total available counts as well as requested counts.

## Metrics and plotting

NMR amounts and score values are serialized as decimal strings to preserve
API precision. Null remains unknown; it does not become zero. Return-rate
fields retain Numerai's native units without an unverified percentage
conversion. Account aggregate returns come directly from Numerai, rather than
adding model percentages or treating deposits as earnings.

Round metrics are identified by **`name` and `version` together**. Canonical and
noncanonical scores are separate identities. `round_scores.csv` and
`selected_round_score_history.csv` are in long form: each row represents one
score observation. `resolved` describes the whole round; `score_resolved`
describes that individual score. Some scores can settle while a longer round
is still resolving. Provisional values and payouts can change. A resolved round does not by itself
confirm on-chain payment or a completed claim.

For a line/trend chart, use:

| Dataset | X axis | Y axes / grouping |
| --- | --- | --- |
| `account_returns.csv` | `date` | `allTimeNmr`, `threeMonthsNmr`, `oneYearNmr`, or native return-rate fields |
| `account_scores.csv` | `date` | API score, long-term score, or rank fields |
| `account_stake.csv` | `date` or `time` | `value` (NMR); `delta` is a change, not profit |
| `model_returns.csv` | `date` | Actual returns/NMR or separately labeled theoretical returns |
| `model_daily_performance.csv` | `date` | CORR/MMC/TC/FNC/Alpha reputation or rank fields, including available neutralized variants |
| `model_stake.csv` | `time` | `value` (NMR) |
| `round_scores.csv` | `round_number` or `resolve_time` | `score_value` / `score_percentile`, grouped by `score_name` + `score_version` |
| `round_payouts.csv` | `round_number` or `resolve_time` | `payout_nmr` / `at_risk_nmr`; separate resolved from provisional |
| `selected_round_score_history.csv` | `score_date` or `score_day` | `score_value`, grouped by round + metric name + version; filter `window` |

Score statistics include sample count, mean, sample standard deviation,
unannualized Sharpe, min/max, and positive fraction for each exact score
identity. Sharpe is null for fewer than two observations or zero variance.
Statistics use the latest per-round/metric observation when its value is non-null.
Never sum repeated daily or per-score payout fields: use `round_payouts.csv`
for round totals. Histories are API observations, not forecasts; the reference
time does not turn live API data into a backtesting interface. Empty datasets
export as empty CSVs, indicating there were no observations.

Snapshot identifiers are validated before use as export-directory names. CSV
exports reject formula-like text in cells and headers rather than modifying
metric identities; ordinary signed/scientific numeric strings remain unchanged.
The original source data remains in the JSON snapshot if a CSV export is rejected.

The fetcher validates model ownership, account/tournament identity, pagination,
and round identities when loading detailed histories. The performance endpoint
currently supports limit/offset pagination but rejects newer `roundNumberEq`
filters, so selected histories are fetched by verified list positions. If the
listing changes between requests, the run fails safely and should be retried.

The queries follow the [official NumerAPI source](https://github.com/numerai/numerapi)
and were checked against the live Numerai GraphQL schema. The compatibility
round-performance endpoint is used for stake/payout configuration metadata and
intra-round observations; deprecated fixed Corr/MMC multiplier fields are not
queried. New score identities are retained in the versioned score lists.
