"""Email-conscious Numerai summaries built from saved performance snapshots."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from src.commons.charts import ChartSeries, line_chart
from src.commons.reporting import RenderedReport, embed_report_images, render_document, section, table, text
from src.observability import trace

REPORT_NOTE = (
    "Numerai: resolving-round scores and payouts are provisional observations, not forecasts. "
    "Round windows count submitted rounds, not days. A resolved round does not confirm on-chain payment. "
    "Amounts are in NMR; return rates retain Numerai's native units. "
    "Charts retain metric names and versions and use saved data only."
)


def _number(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        value = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid Numerai report number") from exc
    if not value.is_finite():
        raise ValueError("Numerai report numbers must be finite")
    return value


def _amount(value: Any, *, signed: bool = False) -> str:
    number = _number(value)
    if number is None:
        return "&mdash;"
    color = "#12745e" if signed and number > 0 else "#b44040" if signed and number < 0 else "#172638"
    formatted = format(number, "+,.4f" if signed and number else ",.4f")
    return f'<span style="color:{color};" title="{text(value)}">{text(formatted)}</span>'


def _time(value: str) -> datetime:
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Date-only history entries are API calendar dates; use UTC for chart placement.
    return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)


def _date(value: str | None) -> str:
    return "Unknown" if value is None else _time(value).strftime("%d %b %Y, %H:%M UTC")


def payout_metrics(rounds: list[dict]) -> list[tuple[str, str | None]]:
    identities = {(config["displayName"], config.get("version"))
                  for row in rounds for config in row["payout_multipliers"]}
    if not identities:
        identities = {(score["name"], score.get("version")) for row in rounds for score in row["scores"]}
    return sorted(identities, key=lambda identity: (identity[0], identity[1] or ""))


def _score_label(identity: tuple[str, str | None]) -> str:
    name, version = identity
    return f"{name} / v{version}" if version is not None else name


def _score_value(row: dict, identity: tuple[str, str | None]) -> Any:
    matches = [score for score in row["scores"] if (score["name"], score.get("version")) == identity]
    if len(matches) > 1:
        raise ValueError("A round contains duplicate score identities")
    return matches[0]["value"] if matches else None


def _chart(data: bytes | None, images: dict[str, bytes], name: str) -> None:
    if data is not None:
        images[name] = data


@trace("numerai.report_charts")
def build_numerai_charts(data: dict[str, Any]) -> dict[str, bytes]:
    images: dict[str, bytes] = {}
    returns = [ChartSeries(label, [(_time(row["date"]), _number(row.get("allTimeNmr")))
                                  for row in data[owner]["history"]["returns"]])
               for owner, label in (("account", "Account return"), ("model", "Model return"))]
    _chart(line_chart(returns, ylabel="Cumulative return (NMR)"), images, "numerai-returns")
    daily = data["model"]["history"]["daily_performance"]
    reputations = [ChartSeries(key, [(_time(row["date"]), _number(row.get(key))) for row in daily])
                   for key in ("corrRep", "mmcRep", "corr60Rep", "tcRep", "alphaRep", "fncV4Rep")]
    _chart(line_chart(reputations, ylabel="API daily reputation"), images, "numerai-reputation")
    for window, name, provisional in (("last_resolved", "numerai-resolved", False),
                                       ("next_resolving", "numerai-resolving", True)):
        rows = data["rounds"][window]
        series = [ChartSeries(_score_label(identity), [(_time(row["resolve_time"]), _number(_score_value(row, identity)))
                                                      for row in rows if row["resolve_time"]], provisional)
                  for identity in payout_metrics(rows)]
        _chart(line_chart(series, ylabel="Latest score", xlabel="Scheduled resolution date (UTC)"), images, name)
    return images


def _image(images: dict[str, bytes], name: str, alt: str) -> str:
    if name not in images:
        return '<p style="padding:14px;color:#687787;font-size:12px;">No observations available for this chart.</p>'
    return (f'<img src="cid:{text(name)}" alt="{text(alt)}" width="800" '
            'style="display:block;width:100%;max-width:800px;height:auto;margin:14px auto;border:0;">')


def _returns_table(returns: dict | None) -> str:
    returns = returns or {}
    return table(["Return window", "Actual return (NMR)"], [
        [text(label), _amount(returns.get(key), signed=True)]
        for label, key in (("All time", "allTimeNmr"), ("Three months", "threeMonthsNmr"),
                           ("One year", "oneYearNmr"), ("One day", "oneDayNmr"))
    ], "No return data available.")


def _key_values(values: list[tuple[str, Any, str]]) -> str:
    return table(["Metric", "Value"], [[text(label), (text(value) if unit in {"models", "rounds"} else _amount(value)) + f" {text(unit)}"]
                                        for label, value, unit in values], "No metrics available.")


def _round_table(rows: list[dict], *, provisional: bool) -> str:
    identities = payout_metrics(rows)
    body = []
    for row in rows:
        status = "Overdue / provisional" if row["overdue"] else "Provisional" if provisional else "Resolved"
        body.append([
            f'<strong>{text(row["round_number"])}</strong><br><span style="font-size:10px;color:#687787;">{text(status)}</span>',
            text(_date(row["resolve_time"])), _amount(row["at_risk_nmr"]), _amount(row["payout_nmr"], signed=True),
            *[_amount(_score_value(row, identity), signed=True) for identity in identities],
        ])
    return table(["Round / state", "Scheduled resolution", "At risk (NMR)",
                  "Provisional payout (NMR)" if provisional else "Round payout (NMR)",
                  *[_score_label(identity) for identity in identities]], body,
                 "No resolving rounds in this snapshot." if provisional else "No resolved rounds in this snapshot.")


@trace("numerai.render_report_body")
def render_numerai_body(snapshot: dict[str, Any], *, images: dict[str, bytes] | None = None) -> str:
    """Build the four Numerai views with cid: chart references for email reuse."""
    if not isinstance(snapshot, dict) or snapshot.get("source") != "numerai" or type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1:
        raise ValueError("Expected a version 1 Numerai snapshot")
    data = snapshot["data"]
    images = {} if images is None else images
    account, model, rounds = data["account"], data["model"], data["rounds"]
    summary = account["summary"]
    account_info = _key_values([
        ("Classic stake", summary.get("totalStake"), "NMR"),
        ("Available balance", account["balances"].get("availableNmr"), "NMR"),
        ("Classic models", len(account["models"]), "models"),
    ])
    body = section("NUMERAI / 01", "Overall account performance",
                   "Numerai-provided aggregate returns, balances, and model stakes.",
                   account_info + _returns_table(summary.get("returns")) +
                   _image(images, "numerai-returns", "Account and model cumulative NMR returns over time"))
    account_scores = summary.get("scores") or {}
    score_rows = [[text(name), text(value) if name.endswith("Rank") else _amount(value)] for name, value in account_scores.items()
                  if name != "date" and value is not None]
    if score_rows:
        body += section("NUMERAI / ACCOUNT SCORES", "Account scores and rankings",
                        "Exact API metric names and native units; rank values are not scores.",
                        table(["API metric", "Value"], score_rows, "No account scores available."))
    current_scores = [[text(score["displayName"]), _amount(score.get("reputation")),
                       text(score["rank"]) if score.get("rank") is not None else "&mdash;",
                       text(score["stakedRank"]) if score.get("stakedRank") is not None else "&mdash;"]
                      for score in model["summary"]["scores"]]
    model_body = _key_values([
        ("Model stake", model["summary"].get("stake_nmr"), "NMR"),
        ("Resolved submitted rounds", rounds["resolved_round_count"], "rounds"),
        ("Currently resolving", rounds["resolving_round_count"], "rounds"),
    ]) + _returns_table(model["summary"].get("returns"))
    model_body += table(["Score identity (API)", "Reputation", "Rank", "Staked rank"], current_scores,
                        "No current model reputations or ranks available.")
    model_body += _image(images, "numerai-reputation", "Daily model reputation over time, separated by API metric")
    body += section("NUMERAI / 02", f"Model performance · {model['name']}",
                    "Actual NMR returns and current API reputation/ranks. Theoretical returns are retained in the source artifacts.", model_body)
    requested = rounds["requested_count"]
    resolved, resolving = rounds["last_resolved"], rounds["next_resolving"]
    body += section("NUMERAI / 03", f"Last {requested} resolved rounds",
                    f"{len(resolved)} available; latest resolution first. Payout-score metrics retain their exact names and versions.",
                    _round_table(resolved, provisional=False) +
                    _image(images, "numerai-resolved", "Resolved round scores by scheduled resolution date"))
    body += section("NUMERAI / 04", f"Next {requested} resolving rounds",
                    f"{len(resolving)} available; earliest resolution first. Scores and payouts are provisional observations, not predictions.",
                    _round_table(resolving, provisional=True) +
                    _image(images, "numerai-resolving", "Provisional round scores plotted against scheduled resolution dates; not a forecast"))
    body += ('<p style="font-size:11px;color:#687787;line-height:1.8;margin-top:18px;">'
             'Round charts show payout-score identities. All other score identities, percentiles, versioned statistics '
             'and intra-round observations remain available in the Numerai snapshot and CSV exports. '
             'Missing values are shown as a dash; chart gaps do not imply zero performance.</p>')
    return body


@trace("numerai.render_report")
def render_numerai_report(snapshot: dict[str, Any], *, email: bool = False) -> RenderedReport:
    """Render Numerai alone; retain CID references only for email composition."""
    if not isinstance(snapshot, dict) or snapshot.get("source") != "numerai":
        raise ValueError("Expected a Numerai snapshot")
    images = build_numerai_charts(snapshot["data"])
    html = render_document(
        title="Numerai · Account and model performance", body=render_numerai_body(snapshot, images=images),
        as_of=_date(snapshot["as_of"]), snapshot_id=snapshot["snapshot_id"],
        trace_id=snapshot.get("trace_id") or "Not recorded", label="NUMERAI REPORT",
        heading="Your models, at a glance.", description="Account performance, model trends, and the rounds to watch.",
        notes=[REPORT_NOTE],
        email=email,
    )
    report = RenderedReport(html, images)
    return report if email else RenderedReport(embed_report_images(report), images)
