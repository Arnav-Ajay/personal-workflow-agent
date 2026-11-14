"""Render verified Coinbase snapshot data as reusable, script-free HTML."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from src.commons.reporting import render_document, section, table, text
from src.observability import trace


def _decimal(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Snapshot contains an invalid numeric value") from exc
    if not number.is_finite():
        raise ValueError("Snapshot contains a non-finite numeric value")
    return number


def _amount(value: Any, *, places: int = 2, signed: bool = False) -> str:
    if value is None:
        return "&mdash;"
    number = _decimal(value)
    if not number:
        number = abs(number)
    formatted = f"{number:+,.{places}f}" if signed and number else f"{number:,.{places}f}"
    if places > 2:
        formatted = formatted.rstrip("0").rstrip(".")
        if number and abs(number) < Decimal(10) ** -places:
            formatted = format(number, "+.4E" if signed else ".4E")
    return f'<span title="{text(value)}">{text(formatted)}</span>'


def _movement(value: Any, *, percent: bool = False) -> str:
    if value is None:
        return '<span style="color:#687787;">Not available</span>'
    number = _decimal(value)
    color = "#12745e" if number > 0 else "#b44040" if number < 0 else "#687787"
    suffix = "%" if percent else " USDC"
    places = 4 if number and abs(number) < Decimal("0.01") else 2
    return f'<span style="color:{color};font-weight:600;">{_amount(value, places=places, signed=True)}{suffix}</span>'


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Snapshot dates must be strings")
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError("Snapshot dates must include a timezone")
    return date.astimezone(timezone.utc)


def _date(value: str | None) -> str:
    return "Not available" if value is None else _timestamp(value).strftime("%d %b %Y, %H:%M UTC")


def _small(value: object) -> str:
    return f'<span style="font-size:11px;color:#687787;">{text(value)}</span>'


def _share(value: Decimal, total: Decimal) -> str:
    share = value / total * 100 if total else Decimal(0)
    width = max(Decimal(0), min(Decimal(100), share))
    return (
        f'{_amount(share)}%'
        '<table role="presentation" style="width:70px;border-collapse:collapse;margin-top:7px;">'
        f'<tr><td style="height:4px;background:#5279c4;width:{width:.2f}%;"></td>'
        f'<td style="height:4px;background:#e8edf5;width:{100-width:.2f}%;"></td></tr></table>'
    )


def _order_rows(orders: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for order in sorted(orders, key=lambda row: _timestamp(row.get("last_fill_time") or row["created_time"]), reverse=True):
        side = str(order["side"])
        color = "#12745e" if side == "BUY" else "#b44040"
        configuration = order.get("order_configuration")
        if configuration is None:
            configuration = {}
        if not isinstance(configuration, dict):
            raise ValueError("Order configuration must be an object")
        terms = []
        for settings in configuration.values():
            if isinstance(settings, dict):
                for key in ("base_size", "quote_size", "limit_price", "stop_price"):
                    if settings.get(key) is not None:
                        terms.append(f"{key.replace('_', ' ')}: {settings[key]}")
        rows.append([
            f'<strong>{text(order["product_id"])}</strong><br>{_small(order.get("order_type") or "Order")}'
            f'<br>{_small(order["order_id"])}',
            f'<strong style="color:{color};">{text(side)}</strong><br>{_small(str(order["status"]).replace("_", " "))}',
            _amount(order.get("filled_size"), places=8) + (f'<br>{_small(" · ".join(terms))}' if terms else ""),
            _amount(order.get("average_filled_price"), places=4),
            _amount(order.get("filled_value")),
            _amount(order.get("total_fees"), places=4),
            text(_date(order.get("last_fill_time") or order["created_time"])),
        ])
    return rows


@trace("coinbase.render_report_body")
def render_coinbase_body(snapshot: dict[str, Any]) -> str:
    """Return the core report sections, independently reusable in email layouts."""
    if (not isinstance(snapshot, dict) or type(snapshot.get("schema_version")) is not int or
            snapshot["schema_version"] != 1 or snapshot.get("source") != "coinbase"):
        raise ValueError("Expected a version 1 Coinbase snapshot")
    data = snapshot.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("orders"), dict):
        raise ValueError("Snapshot data and orders must be objects")
    portfolios = data["portfolios"]
    active, filled = data["orders"]["in_progress"], data["orders"]["filled_last_week"]
    watchlist = data.get("watchlist", [])
    if any(not isinstance(rows, list) for rows in (portfolios, active, filled, watchlist)):
        raise ValueError("Snapshot collections must be lists")
    if any(not isinstance(row, dict) for rows in (portfolios, active, filled, watchlist) for row in rows):
        raise ValueError("Snapshot entries must be objects")
    for portfolio in portfolios:
        if not isinstance(portfolio["wallets"], list) or any(not isinstance(row, dict) for row in portfolio["wallets"]):
            raise ValueError("Portfolio wallets must be a list of objects")
        for wallet in portfolio["wallets"]:
            if any(not isinstance(wallet.get(flag), bool) for flag in ("active", "ready")):
                raise ValueError("Wallet active and ready flags must be booleans")
    total = sum((_decimal(p["total_value_usdc"]) for p in portfolios), Decimal(0))
    previous = sum((_decimal(p["previous_week_value_usdc"]) for p in portfolios), Decimal(0))
    difference = total - previous
    change = difference / previous * 100 if previous else None
    wallet_count = sum(len(p["wallets"]) for p in portfolios)
    body = (
        '<div style="background:#172c48;border-radius:14px;padding:28px;color:#fff;">'
        '<p style="margin:0 0 12px;font-size:10px;letter-spacing:2px;color:#b6c8e1;">TOTAL CRYPTO VALUE</p>'
        f'<p class="summary-value" style="margin:0;font-size:48px;letter-spacing:-1.5px;font-weight:600;">{_amount(total)} '
        '<span style="font-size:17px;font-weight:400;color:#b6c8e1;letter-spacing:0;">USDC</span></p>'
        f'<p style="margin:12px 0 0;color:#b6c8e1;font-size:12px;">{len(portfolios)} {"portfolio" if len(portfolios) == 1 else "portfolios"} &nbsp; / &nbsp; {wallet_count} {"wallet" if wallet_count == 1 else "wallets"} &nbsp; / &nbsp; available + held balances</p>'
        '</div>'
        '<table class="metrics" role="presentation" style="width:100%;border-collapse:collapse;margin:22px 0 0;"><tr>'
    )
    metrics = [
        ("7-DAY MARKET MOVEMENT", _movement(difference), _movement(change, percent=True)),
        ("PREVIOUS-WEEK ESTIMATE", _amount(previous) + " USDC", "At current quantities"),
        ("IN PROGRESS", str(len(active)), "Active orders"),
        ("FILLED THIS WEEK", str(len(filled)), "Rolling seven-day window"),
    ]
    for label, value, caption in metrics:
        body += (
            '<td style="width:25%;padding:8px 18px 8px 0;vertical-align:top;">'
            f'<p style="font-size:9px;letter-spacing:1px;color:#687787;margin:0 0 10px;">{text(label)}</p>'
            f'<p style="font-size:19px;font-weight:600;margin:0 0 7px;">{value}</p>'
            f'<p style="font-size:11px;color:#687787;margin:0;">{caption}</p></td>'
        )
    body += "</tr></table>"

    portfolio_rows = [[
        f'<strong>{text(p["name"])}</strong><br>{_small(p["id"])}',
        _amount(p["total_value_usdc"]) + " USDC",
        _movement(p["weekly_change_usdc"]) + "<br>" + _movement(p.get("weekly_change_percent"), percent=True),
        _share(_decimal(p["total_value_usdc"]), total),
        text(len(p["wallets"])),
    ] for p in sorted(portfolios, key=lambda row: _decimal(row["total_value_usdc"]), reverse=True)]
    body += section("01 / ALLOCATION", "Your portfolios", "A snapshot of where your crypto value is held.",
                    table(["Portfolio", "Value", "7-day movement", "Share", "Wallets"], portfolio_rows,
                          "No portfolios in this snapshot."))

    holdings = ""
    for portfolio in portfolios:
        holdings += (f'<h3 style="font-size:15px;margin:20px 14px 6px;">{text(portfolio["name"])}</h3>')
        rows = []
        for wallet in sorted(portfolio["wallets"], key=lambda row: _decimal(row["value_usdc"]), reverse=True):
            quantity = _decimal(wallet["available_balance"]) + _decimal(wallet["hold"])
            state = "Ready" if wallet["active"] and wallet["ready"] else "Not ready" if wallet["active"] else "Inactive"
            rows.append([
                f'<strong>{text(wallet["currency"])}</strong><br>{_small(wallet["name"])}<br>{_small(state)}',
                _amount(wallet.get("current_price_usdc"), places=8),
                _amount(quantity, places=8),
                _amount(wallet["available_balance"], places=8),
                _amount(wallet["hold"], places=8),
                _amount(wallet["value_usdc"]) + " USDC",
                _movement(wallet["weekly_change_usdc"]) + "<br>" + _movement(wallet.get("weekly_change_percent"), percent=True),
            ])
        holdings += table(["Asset", "Current price (USDC)", "Total quantity", "Available", "Held", "Value", "7-day movement"], rows,
                          "This portfolio has no crypto wallets.")
    body += section("02 / HOLDINGS", "Asset breakdown", "Wallet quantities stay in their original asset; market values are estimated in USDC.",
                    holdings or '<p style="padding:12px;color:#687787;">No wallets in this snapshot.</p>')
    headers = ["Product / order", "Side / status", "Filled size / terms", "Avg. price (quote)", "Filled value (quote)", "Fees (quote)", "Last fill / created"]
    body += section("03 / ORDERS", "In-progress orders", "Open and queued orders. Prices, filled values, and fees use each product's quote currency.",
                    table(headers, _order_rows(active), "No in-progress orders. Nothing waiting to be filled."))
    body += section("04 / ACTIVITY", "Filled in the last seven days", "Based on last fill time, including orders created before this week.",
                    table(headers, _order_rows(filled), "No fully filled orders in the seven-day window."))
    watch_rows = [[
        f'<strong>{text(item["product_id"])}</strong>',
        _amount(item.get("current_price"), places=8) + "<br>" + _small(str(item["product_id"]).rsplit("-", 1)[-1]),
        _movement(item["daily_percent"], percent=True),
        _movement(item["weekly_percent"], percent=True),
        _movement(item["monthly_percent"], percent=True),
    ] for item in watchlist]
    body += section("05 / MARKETS", "Your watchlist", "Snapshot prices use each product's quote currency, for example CAD per USDC for USDC-CAD.",
                    table(["Product", "Current price (quote)", "24 hours", "7 days", "30 days"], watch_rows,
                          "No watchlist products included in this snapshot."))
    return body


@trace("coinbase.render_report")
def render_coinbase_report(snapshot: dict[str, Any]) -> str:
    return render_document(
        title="Coinbase · Portfolio snapshot", body=render_coinbase_body(snapshot),
        as_of=_date(snapshot["as_of"]), snapshot_id=snapshot["snapshot_id"],
        trace_id=snapshot.get("trace_id") or "Not recorded",
    )
