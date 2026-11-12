"""Read-only Coinbase Advanced Trade portfolio and market information."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, TYPE_CHECKING

from requests import RequestException

from .cb_account import CoinbaseAccount, CoinbaseCrypto, CoinbaseOrder, CoinbaseOrders, MarketMovement

if TYPE_CHECKING:
    from coinbase.rest import RESTClient

logger = logging.getLogger(__name__)


class CoinbaseClientError(RuntimeError):
    """Raised when Coinbase data cannot be retrieved or interpreted."""


def _decimal(value: Any, description: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CoinbaseClientError(f"Invalid {description}: {value!r}") from exc
    if not result.is_finite():
        raise CoinbaseClientError(f"Invalid {description}: {value!r}")
    return result


def _optional_decimal(value: Any, description: str) -> Decimal | None:
    return None if value is None or value == "" else _decimal(value, description)


def _timestamp(value: str, description: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise CoinbaseClientError(f"Invalid {description}: {value!r}") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise CoinbaseClientError(f"{description} must include a timezone")
    return result.astimezone(timezone.utc)


def _now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(timezone.utc)


def _change(current: Decimal, previous: Decimal) -> tuple[Decimal, Decimal | None]:
    difference = current - previous
    return difference, difference / previous * 100 if previous else None


def _required(data: dict[str, Any], key: str, description: str) -> Any:
    if not isinstance(data, dict):
        raise CoinbaseClientError(f"Coinbase {description} is not an object")
    value = data.get(key)
    if value is None or value == "":
        raise CoinbaseClientError(f"Coinbase {description} is missing {key}")
    return value


class CoinbaseClient:
    """A read-only wrapper around the Coinbase Advanced Trade REST client."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        if not api_key or not api_secret:
            raise CoinbaseClientError("Coinbase API credentials are required")
        try:
            from coinbase.rest import RESTClient
        except ImportError as exc:
            raise CoinbaseClientError("coinbase-advanced-py is not installed") from exc
        self.client: RESTClient = RESTClient(api_key=api_key, api_secret=api_secret, timeout=30)

    @staticmethod
    def _call(operation: str, function: Callable[..., Any], **kwargs: Any) -> Any:
        try:
            return function(**kwargs)
        except RequestException as exc:
            raise CoinbaseClientError(f"Coinbase {operation} failed: {exc}") from exc

    def _pages(self, operation: str, function: Callable[..., Any], field: str, **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            response = self._call(operation, function, cursor=cursor, **kwargs).to_dict()
            page = _required(response, field, operation)
            if not isinstance(page, list):
                raise CoinbaseClientError(f"Coinbase {operation} returned invalid {field}")
            if any(not isinstance(item, dict) for item in page):
                raise CoinbaseClientError(f"Coinbase {operation} returned invalid {field} entries")
            items.extend(page)
            has_next = _required(response, "has_next", operation)
            if not isinstance(has_next, bool):
                raise CoinbaseClientError(f"Coinbase {operation} returned invalid has_next")
            if not has_next:
                return items
            cursor = _required(response, "cursor", operation)
            if not isinstance(cursor, str) or cursor in seen:
                raise CoinbaseClientError(f"Coinbase {operation} returned an invalid or repeated cursor")
            seen.add(cursor)

    def _product_price(self, product_id: str) -> Decimal:
        response = self._call(
            f"product {product_id}", self.client.get_public_product, product_id=product_id
        ).to_dict()
        price = _decimal(_required(response, "price", product_id), f"{product_id} price")
        if price <= 0:
            raise CoinbaseClientError(f"Coinbase {product_id} price must be positive")
        return price

    def _historical_price(self, product_id: str, when: datetime) -> Decimal:
        # Coinbase includes both endpoints and returns newest candles first.
        start = int(when.timestamp()) // 60 * 60
        response = self._call(
            f"candles for {product_id}",
            self.client.get_public_candles,
            product_id=product_id,
            start=str(start),
            end=str(start),
            granularity="ONE_MINUTE",
            limit=1,
        ).to_dict()
        candles = _required(response, "candles", product_id)
        if not isinstance(candles, list) or any(not isinstance(candle, dict) for candle in candles):
            raise CoinbaseClientError(f"Coinbase {product_id} returned invalid candles")
        for candle in candles:
            if str(candle.get("start")) == str(start):
                price = _decimal(_required(candle, "open", product_id), f"{product_id} candle price")
                if price <= 0:
                    raise CoinbaseClientError(f"Coinbase {product_id} candle price must be positive")
                return price
        # Illiquid products can have no trade in the target minute. Use the
        # latest completed hourly candle within the preceding 24 hours.
        last_hour = int(when.timestamp()) // 3600 * 3600 - 3600
        response = self._call(
            f"hourly candles for {product_id}",
            self.client.get_public_candles,
            product_id=product_id,
            start=str(last_hour - 23 * 3600),
            end=str(last_hour),
            granularity="ONE_HOUR",
            limit=24,
        ).to_dict()
        candles = _required(response, "candles", product_id)
        if not isinstance(candles, list) or any(not isinstance(candle, dict) for candle in candles):
            raise CoinbaseClientError(f"Coinbase {product_id} returned invalid candles")
        eligible = [
            candle for candle in candles
            if isinstance(candle, dict)
            and str(candle.get("start", "")).isdigit()
            and last_hour - 23 * 3600 <= int(candle["start"]) <= last_hour
        ]
        if not eligible:
            raise CoinbaseClientError(f"Coinbase has no recent {product_id} candle for {when.isoformat()}")
        candle = max(eligible, key=lambda item: int(item["start"]))
        price = _decimal(_required(candle, "close", product_id), f"{product_id} candle price")
        if price <= 0:
            raise CoinbaseClientError(f"Coinbase {product_id} candle price must be positive")
        return price

    def _usdc_rates(
        self, currency: str, when: datetime, cache: dict[str, tuple[Decimal, Decimal]]
    ) -> tuple[Decimal, Decimal]:
        if currency == "USDC":
            return Decimal(1), Decimal(1)
        if currency in cache:
            return cache[currency]
        product = f"{currency}-USDC"
        rates = self._product_price(product), self._historical_price(product, when)
        cache[currency] = rates
        return rates

    def _get_wallets(
        self, retail_portfolio_id: str | None = None, *, as_of: datetime | None = None
    ) -> list[CoinbaseCrypto]:
        """Return crypto wallets, including holds, with seven-day USDC estimates."""
        now = _now(as_of)
        rows = self._pages("accounts", self.client.get_accounts, "accounts", limit=250)
        rates: dict[str, tuple[Decimal, Decimal]] = {}
        wallets: list[CoinbaseCrypto] = []
        for row in rows:
            portfolio_id = _required(row, "retail_portfolio_id", "account")
            if retail_portfolio_id is not None and portfolio_id != retail_portfolio_id:
                continue
            currency = _required(row, "currency", "account")
            account_type = _required(row, "type", "account")
            if account_type == "ACCOUNT_TYPE_FIAT":
                logger.warning("Excluding fiat wallet %s from USDC portfolio valuation", currency)
                continue
            if account_type not in {"ACCOUNT_TYPE_CRYPTO", "ACCOUNT_TYPE_VAULT"}:
                raise CoinbaseClientError(f"Unsupported Coinbase account type: {account_type}")
            balance = _required(row, "available_balance", "account")
            hold = _required(row, "hold", "account")
            if _required(balance, "currency", "balance") != currency or _required(hold, "currency", "hold") != currency:
                raise CoinbaseClientError(f"Coinbase {currency} wallet has mismatched balance currencies")
            available = _decimal(_required(balance, "value", "balance"), "available balance")
            held = _decimal(_required(hold, "value", "hold"), "held balance")
            quantity = available + held
            if quantity < 0:
                raise CoinbaseClientError(f"Coinbase {currency} wallet has a negative balance")
            current_rate, previous_rate = (
                self._usdc_rates(currency, now - timedelta(days=7), rates)
                if quantity else (Decimal(0), Decimal(0))
            )
            value, previous = quantity * current_rate, quantity * previous_rate
            difference, percent = _change(value, previous)
            wallets.append(CoinbaseCrypto(
                uuid=_required(row, "uuid", "account"),
                name=_required(row, "name", "account"),
                currency=currency,
                retail_portfolio_id=portfolio_id,
                available_balance=available,
                hold=held,
                value_usdc=value,
                previous_week_value_usdc=previous,
                weekly_change_usdc=difference,
                weekly_change_percent=percent,
                active=_required(row, "active", "account"),
                ready=_required(row, "ready", "account"),
            ))
        return wallets

    def get_user_portfolios(
        self, retail_portfolio_id: str | None = None, *, as_of: datetime | None = None
    ) -> list[CoinbaseAccount]:
        """Return portfolios with wallet balances, holds and seven-day USDC estimates.

        Optionally select a retail portfolio ID before valuing its wallets.
        """
        now = _now(as_of)
        response = self._call("portfolios", self.client.get_portfolios).to_dict()
        portfolios = _required(response, "portfolios", "portfolios")
        if not isinstance(portfolios, list) or any(not isinstance(p, dict) for p in portfolios):
            raise CoinbaseClientError("Coinbase returned invalid portfolios")
        if retail_portfolio_id is not None:
            portfolios = [
                portfolio for portfolio in portfolios
                if _required(portfolio, "uuid", "portfolio") == retail_portfolio_id
            ]
        wallets = self._get_wallets(retail_portfolio_id, as_of=now)
        ids = {_required(p, "uuid", "portfolio") for p in portfolios}
        unmatched = {wallet.retail_portfolio_id for wallet in wallets} - ids
        if unmatched:
            raise CoinbaseClientError(f"Wallets reference unknown portfolios: {sorted(unmatched)}")
        result: list[CoinbaseAccount] = []
        for portfolio in portfolios:
            portfolio_id = _required(portfolio, "uuid", "portfolio")
            selected = [wallet for wallet in wallets if wallet.retail_portfolio_id == portfolio_id]
            total = sum((wallet.value_usdc for wallet in selected), Decimal(0))
            previous = sum((wallet.previous_week_value_usdc for wallet in selected), Decimal(0))
            difference, percent = _change(total, previous)
            result.append(CoinbaseAccount(
                id=portfolio_id,
                name=_required(portfolio, "name", "portfolio"),
                total_value_usdc=total,
                wallets=selected,
                previous_week_value_usdc=previous,
                weekly_change_usdc=difference,
                weekly_change_percent=percent,
            ))
        return result

    def get_orders(
        self, retail_portfolio_id: str | None = None, *, as_of: datetime | None = None
    ) -> CoinbaseOrders:
        """Return active orders and orders fully filled within the last seven days."""
        now = _now(as_of)
        active_statuses = {"PENDING", "OPEN", "QUEUED", "CANCEL_QUEUED", "EDIT_QUEUED"}
        rows: list[dict[str, Any]] = []
        for source in ("RETAIL_ADVANCED", "RETAIL_SIMPLE"):
            for status in sorted(active_statuses | {"FILLED"}):
                rows.extend(self._pages(
                    f"{source} {status} orders", self.client.list_orders, "orders",
                    order_status=[status], limit=100,
                    retail_portfolio_id=retail_portfolio_id, order_placement_source=source,
                ))
        active: list[CoinbaseOrder] = []
        filled: list[CoinbaseOrder] = []
        seen: set[str] = set()
        for row in rows:
            if retail_portfolio_id is not None:
                order_portfolio = _required(row, "retail_portfolio_id", "order")
                if order_portfolio != retail_portfolio_id:
                    continue
            order_id = _required(row, "order_id", "order")
            if order_id in seen:
                continue
            seen.add(order_id)
            side = _required(row, "side", "order")
            status = _required(row, "status", "order")
            if side not in {"BUY", "SELL"} or status not in active_statuses | {"FILLED"}:
                raise CoinbaseClientError(f"Unexpected Coinbase order side/status: {side}/{status}")
            last_fill = row.get("last_fill_time")
            if status == "FILLED" and not last_fill:
                raise CoinbaseClientError("Filled Coinbase order is missing last_fill_time")
            last_fill_time = _timestamp(last_fill, "last fill time") if last_fill else None
            if status == "FILLED" and not (now - timedelta(days=7) <= last_fill_time <= now):
                continue
            average = row.get("average_filled_price")
            order = CoinbaseOrder(
                order_id=order_id,
                product_id=_required(row, "product_id", "order"),
                side=side,
                status=status,
                created_time=_timestamp(_required(row, "created_time", "order"), "order creation time"),
                last_fill_time=last_fill_time,
                filled_size=_optional_decimal(row.get("filled_size"), "filled size"),
                filled_value=_optional_decimal(row.get("filled_value"), "filled value"),
                average_filled_price=_optional_decimal(average, "average filled price"),
                total_fees=_decimal(_required(row, "total_fees", "order"), "total fees"),
                retail_portfolio_id=row.get("retail_portfolio_id"),
                order_type=row.get("order_type"),
                order_configuration=row.get("order_configuration"),
            )
            (filled if status == "FILLED" else active).append(order)
        return CoinbaseOrders(in_progress=active, filled_last_week=filled)

    def get_watchlist_movement(
        self, product_ids: list[str], *, as_of: datetime | None = None
    ) -> list[MarketMovement]:
        """Return rolling 24-hour, 7-day and 30-day price changes for products."""
        now = _now(as_of)
        result: list[MarketMovement] = []
        for product_id in product_ids:
            if not isinstance(product_id, str) or not product_id.strip():
                raise ValueError("product IDs must be non-empty strings")
            current = self._product_price(product_id)
            percentages: list[Decimal] = []
            for days in (1, 7, 30):
                previous = self._historical_price(product_id, now - timedelta(days=days))
                percent = _change(current, previous)[1]
                if percent is None:
                    raise CoinbaseClientError(f"Cannot calculate {product_id} movement from a zero price")
                percentages.append(percent)
            result.append(MarketMovement(product_id, *percentages))
        return result
