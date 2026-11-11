"""Models returned by the Coinbase Advanced Trade client."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class CoinbaseCrypto:
    """A wallet and the estimated market movement of its current balance."""

    uuid: str
    name: str
    currency: str
    retail_portfolio_id: str
    available_balance: Decimal
    hold: Decimal
    value_usdc: Decimal
    previous_week_value_usdc: Decimal
    weekly_change_usdc: Decimal
    weekly_change_percent: Decimal | None
    active: bool
    ready: bool

    @property
    def total_balance(self) -> Decimal:
        return self.available_balance + self.hold


@dataclass(frozen=True)
class CoinbaseAccount:
    """A retail portfolio, valued using its current spot wallet balances."""

    id: str
    name: str
    total_value_usdc: Decimal
    wallets: list[CoinbaseCrypto]
    previous_week_value_usdc: Decimal
    weekly_change_usdc: Decimal
    weekly_change_percent: Decimal | None


@dataclass(frozen=True)
class CoinbaseOrder:
    order_id: str
    product_id: str
    side: str
    status: str
    created_time: datetime
    last_fill_time: datetime | None
    filled_size: Decimal | None
    filled_value: Decimal | None
    average_filled_price: Decimal | None
    total_fees: Decimal
    retail_portfolio_id: str | None
    order_type: str | None
    order_configuration: dict[str, Any] | None


@dataclass(frozen=True)
class CoinbaseOrders:
    """Open orders and orders fully filled in the rolling seven-day window."""

    in_progress: list[CoinbaseOrder]
    filled_last_week: list[CoinbaseOrder]


@dataclass(frozen=True)
class MarketMovement:
    """Percentage price changes for a caller-supplied Coinbase product."""

    product_id: str
    daily_percent: Decimal
    weekly_percent: Decimal
    monthly_percent: Decimal
