"""Performance records with explicit round state and score identity."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class NumeraiScore:
    name: str
    version: str | None
    value: Decimal | None
    percentile: Decimal | None
    date: datetime | None
    day: int | None
    resolved: bool | None
    resolve_date: datetime | None
    payout_pending_nmr: Decimal | None
    payout_settled_nmr: Decimal | None


@dataclass
class NumeraiRound:
    round_number: int
    round_id: str | None
    submission_id: str | None
    target: str | None
    data_datestamp: int | None
    open_time: datetime | None
    score_time: datetime | None
    close_staking_time: datetime | None
    resolve_time: datetime | None
    resolved: bool
    overdue: bool
    at_risk_nmr: Decimal | None
    payout_nmr: Decimal | None
    payout_status: str
    payout_factor: Decimal | None
    payout_multipliers: list[dict[str, Any]]
    scores: list[NumeraiScore]
    score_history: list[NumeraiScore] = field(default_factory=list)
