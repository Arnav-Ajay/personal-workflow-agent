"""Read-only Coinbase Advanced Trade portfolio and market information."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, TYPE_CHECKING

from requests import HTTPError, RequestException

from src.observability import trace

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
        if not isinstance(value, str):
            raise ValueError("timestamp must be a string")
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CoinbaseClientError(f"Invalid {description}: {value!r}") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise CoinbaseClientError(f"{description} must include a timezone")
    return result.astimezone(timezone.utc)


def _now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _change(current: Decimal, previous: Decimal) -> tuple[Decimal, Decimal | None]:
    difference = current - previous
    return difference, difference / previous * 100 if previous else None


def _required(
    data: dict[str, Any], key: str, description: str, expected_type: type | None = None
) -> Any:
    if not isinstance(data, dict):
        raise CoinbaseClientError(f"Coinbase {description} is not an object")
    value = data.get(key)
    if value is None or value == "":
        raise CoinbaseClientError(f"Coinbase {description} is missing {key}")
    if expected_type is not None and not isinstance(value, expected_type):
        raise CoinbaseClientError(f"Coinbase {description} has invalid {key}")
    if isinstance(value, str) and not value.strip():
        raise CoinbaseClientError(f"Coinbase {description} has blank {key}")
    return value


def _records(data: dict[str, Any], field: str, description: str) -> list[dict[str, Any]]:
    records = _required(data, field, description, list)
    if any(not isinstance(item, dict) for item in records):
        raise CoinbaseClientError(f"Coinbase {description} returned invalid {field} entries")
    return records


def _price(data: dict[str, Any], field: str, product_id: str) -> Decimal:
    price = _decimal(_required(data, field, product_id), f"{product_id} price")
    if price <= 0:
        raise CoinbaseClientError(f"Coinbase {product_id} price must be positive")
    return price


class CoinbaseClient:
    """A read-only wrapper around the Coinbase Advanced Trade REST client."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (api_key, api_secret)):
            raise CoinbaseClientError("Coinbase API credentials are required")
        try:
            from coinbase.rest import RESTClient
        except ImportError as exc:
            raise CoinbaseClientError("coinbase-advanced-py is not installed") from exc
        try:
            self.client: RESTClient = RESTClient(api_key=api_key, api_secret=api_secret, timeout=30)
        except (TypeError, ValueError) as exc:
            raise CoinbaseClientError("Coinbase API credentials have an invalid format") from exc

    def close(self) -> None:
        """Release the SDK HTTP session; also usable with contextlib.closing."""
        self.client.session.close()

    @staticmethod
    @trace("coinbase.api_request")
    def _call(operation: str, function: Callable[..., Any], **kwargs: Any) -> dict[str, Any]:
        logger.info("Requesting Coinbase %s", operation)
        try:
            response = function(**kwargs).to_dict()
        except RequestException as exc:
            raise CoinbaseClientError(f"Coinbase {operation} failed: {exc}") from exc
        except Exception as exc:
            # SDK response parsing and JWT signing can raise plain Exception.
            raise CoinbaseClientError(f"Coinbase {operation} failed in the SDK") from exc
        if not isinstance(response, dict):
            raise CoinbaseClientError(f"Coinbase {operation} response is not an object")
        if response.get("proof_token_required") is True:
            raise CoinbaseClientError(f"Coinbase {operation} requires additional authentication")
        return response

    def _pages(
        self, operation: str, function: Callable[..., Any], field: str, **kwargs: Any
    ) -> Iterator[dict[str, Any]]:
        """Yield validated rows one page at a time, rejecting cursor loops."""
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            response = self._call(operation, function, cursor=cursor, **kwargs)
            page = _records(response, field, operation)
            has_next = _required(response, "has_next", operation, bool)
            if has_next:
                cursor = _required(response, "cursor", operation, str)
                if cursor in seen:
                    raise CoinbaseClientError(f"Coinbase {operation} returned a repeated cursor")
                seen.add(cursor)
            yield from page
            if not has_next:
                return

    def _product_price(self, product_id: str) -> Decimal:
        response = self._call(
            f"product {product_id}", self.client.get_public_product, product_id=product_id
        )
        return _price(response, "price", product_id)

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
        )
        candles = _records(response, "candles", product_id)
        for candle in candles:
            if str(candle.get("start")) == str(start):
                return _price(candle, "open", product_id)
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
        )
        candles = _records(response, "candles", product_id)
        eligible = [
            candle for candle in candles
            if str(candle.get("start", "")).isascii()
            and str(candle.get("start", "")).isdigit()
            and last_hour - 23 * 3600 <= int(candle["start"]) <= last_hour
        ]
        if not eligible:
            raise CoinbaseClientError(f"Coinbase has no recent {product_id} candle for {when.isoformat()}")
        candle = max(eligible, key=lambda item: int(item["start"]))
        return _price(candle, "close", product_id)

    def _usdc_rates(
        self, currency: str, when: datetime, cache: dict[str, tuple[Decimal, Decimal]]
    ) -> tuple[Decimal, Decimal]:
        if currency == "USDC":
            return Decimal(1), Decimal(1)
        if currency not in cache:
            product = f"{currency}-USDC"
            cache[currency] = self._product_price(product), self._historical_price(product, when)
        return cache[currency]

    def _get_wallets(
        self, retail_portfolio_id: str | None, *, as_of: datetime
    ) -> Iterator[CoinbaseCrypto]:
        """Return crypto wallets, including holds, with seven-day USDC estimates."""
        rows = self._pages("accounts", self.client.get_accounts, "accounts", limit=250)
        rates: dict[str, tuple[Decimal, Decimal]] = {}
        prices: dict[str, Decimal | None] = {"USDC": Decimal(1)}
        seen: set[str] = set()
        for row in rows:
            portfolio_id = _required(row, "retail_portfolio_id", "account", str)
            if retail_portfolio_id is not None and portfolio_id != retail_portfolio_id:
                continue
            currency = _required(row, "currency", "account", str)
            account_type = _required(row, "type", "account", str)
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
            if available < 0 or held < 0:
                raise CoinbaseClientError(f"Coinbase {currency} wallet has a negative balance")
            quantity = available + held
            wallet_id = _required(row, "uuid", "account", str)
            if wallet_id in seen:
                raise CoinbaseClientError(f"Coinbase returned duplicate wallet: {wallet_id}")
            seen.add(wallet_id)
            current_rate, previous_rate = (
                self._usdc_rates(currency, as_of - timedelta(days=7), rates)
                if quantity else (Decimal(0), Decimal(0))
            )
            if quantity:
                prices[currency] = current_rate
            elif currency not in prices:
                try:
                    prices[currency] = self._product_price(f"{currency}-USDC")
                except CoinbaseClientError as exc:
                    cause = exc.__cause__
                    if not isinstance(cause, HTTPError) or cause.response is None or cause.response.status_code != 404:
                        raise
                    # Retired assets may remain as empty wallets with no market.
                    prices[currency] = None
                    logger.warning("No current USDC price for empty %s wallet", currency)
            value, previous = quantity * current_rate, quantity * previous_rate
            difference, percent = _change(value, previous)
            yield CoinbaseCrypto(
                uuid=wallet_id,
                name=_required(row, "name", "account", str),
                currency=currency,
                retail_portfolio_id=portfolio_id,
                available_balance=available,
                hold=held,
                value_usdc=value,
                previous_week_value_usdc=previous,
                weekly_change_usdc=difference,
                weekly_change_percent=percent,
                active=_required(row, "active", "account", bool),
                ready=_required(row, "ready", "account", bool),
                current_price_usdc=prices[currency],
            )

    @trace("coinbase.portfolios")
    def get_user_portfolios(
        self, retail_portfolio_id: str | None = None, *, as_of: datetime | None = None
    ) -> list[CoinbaseAccount]:
        """Return portfolios with wallet balances, holds and seven-day USDC estimates.

        Optionally select a retail portfolio ID before valuing its wallets.
        """
        now = _now(as_of)
        response = self._call("portfolios", self.client.get_portfolios)
        portfolios = _records(response, "portfolios", "portfolios")
        if retail_portfolio_id is not None:
            portfolios = [
                portfolio for portfolio in portfolios
                if _required(portfolio, "uuid", "portfolio", str) == retail_portfolio_id
            ]
        grouped: dict[str, list[CoinbaseCrypto]] = {}
        for portfolio in portfolios:
            portfolio_id = _required(portfolio, "uuid", "portfolio", str)
            _required(portfolio, "name", "portfolio", str)
            if portfolio_id in grouped:
                raise CoinbaseClientError(f"Coinbase returned duplicate portfolio: {portfolio_id}")
            grouped[portfolio_id] = []
        for wallet in self._get_wallets(retail_portfolio_id, as_of=now):
            if wallet.retail_portfolio_id not in grouped:
                raise CoinbaseClientError(f"Wallets reference unknown portfolios: {wallet.retail_portfolio_id}")
            grouped[wallet.retail_portfolio_id].append(wallet)
        result: list[CoinbaseAccount] = []
        for portfolio in portfolios:
            portfolio_id = portfolio["uuid"]
            selected = grouped[portfolio_id]
            total = sum((wallet.value_usdc for wallet in selected), Decimal(0))
            previous = sum((wallet.previous_week_value_usdc for wallet in selected), Decimal(0))
            difference, percent = _change(total, previous)
            result.append(CoinbaseAccount(
                id=portfolio_id,
                name=portfolio["name"],
                total_value_usdc=total,
                wallets=selected,
                previous_week_value_usdc=previous,
                weekly_change_usdc=difference,
                weekly_change_percent=percent,
            ))
        return result

    @trace("coinbase.orders")
    def get_orders(
        self, retail_portfolio_id: str | None = None, *, as_of: datetime | None = None
    ) -> CoinbaseOrders:
        """Return active orders and orders fully filled within the last seven days."""
        now = _now(as_of)
        active_statuses = {"PENDING", "OPEN", "QUEUED", "CANCEL_QUEUED", "EDIT_QUEUED"}
        statuses = active_statuses | {"FILLED"}
        # Separate status/source queries preserve Coinbase's filtering behavior.
        rows = (
            row
            for source in ("RETAIL_ADVANCED", "RETAIL_SIMPLE")
            for status in sorted(statuses)
            for row in self._pages(
                f"{source} {status} orders", self.client.list_orders, "orders",
                order_status=[status], limit=100,
                retail_portfolio_id=retail_portfolio_id, order_placement_source=source,
            )
        )
        active: list[CoinbaseOrder] = []
        filled: list[CoinbaseOrder] = []
        seen: set[str] = set()
        cutoff = now - timedelta(days=7)
        for row in rows:
            if retail_portfolio_id is not None:
                order_portfolio = _required(row, "retail_portfolio_id", "order", str)
                if order_portfolio != retail_portfolio_id:
                    continue
            order_id = _required(row, "order_id", "order", str)
            if order_id in seen:
                continue
            seen.add(order_id)
            side = _required(row, "side", "order", str)
            status = _required(row, "status", "order", str)
            if side not in {"BUY", "SELL"} or status not in statuses:
                raise CoinbaseClientError(f"Unexpected Coinbase order side/status: {side}/{status}")
            last_fill = row.get("last_fill_time")
            if status == "FILLED" and not last_fill:
                raise CoinbaseClientError("Filled Coinbase order is missing last_fill_time")
            last_fill_time = _timestamp(last_fill, "last fill time") if last_fill else None
            if status == "FILLED" and not (cutoff <= last_fill_time <= now):
                continue
            order = CoinbaseOrder(
                order_id=order_id,
                product_id=_required(row, "product_id", "order", str),
                side=side,
                status=status,
                created_time=_timestamp(_required(row, "created_time", "order"), "order creation time"),
                last_fill_time=last_fill_time,
                filled_size=_optional_decimal(row.get("filled_size"), "filled size"),
                filled_value=_optional_decimal(row.get("filled_value"), "filled value"),
                average_filled_price=_optional_decimal(row.get("average_filled_price"), "average filled price"),
                total_fees=_decimal(_required(row, "total_fees", "order"), "total fees"),
                retail_portfolio_id=row.get("retail_portfolio_id"),
                order_type=row.get("order_type"),
                order_configuration=row.get("order_configuration"),
            )
            (filled if status == "FILLED" else active).append(order)
        return CoinbaseOrders(in_progress=active, filled_last_week=filled)

    @trace("coinbase.watchlist")
    def get_watchlist_movement(
        self, product_ids: list[str], *, as_of: datetime | None = None
    ) -> list[MarketMovement]:
        """Return rolling 24-hour, 7-day and 30-day price changes for products."""
        now = _now(as_of)
        if any(not isinstance(product_id, str) or not product_id.strip() for product_id in product_ids):
            raise ValueError("product IDs must be non-empty strings")
        movements: dict[str, MarketMovement] = {}
        for product_id in dict.fromkeys(product_ids):
            current = self._product_price(product_id)
            percentages: list[Decimal] = []
            for days in (1, 7, 30):
                previous = self._historical_price(product_id, now - timedelta(days=days))
                percentages.append((current - previous) / previous * 100)
            movements[product_id] = MarketMovement(product_id, *percentages, current_price=current)
        return [movements[product_id] for product_id in product_ids]
