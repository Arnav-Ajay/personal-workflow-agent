"""Read-only Numerai Classic reporting using the official GraphQL service.

Only performance fields are requested. Headers, credentials, response payloads,
email addresses and wallet addresses are never logged.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from src.observability import trace

from . import queries
from .models import NumeraiRound, NumeraiScore

logger = logging.getLogger(__name__)
API_URL = "https://api-tournament.numer.ai"
TOURNAMENT = 8
PAGE_SIZE = 200


class NumeraiClientError(RuntimeError):
    """A request, permission, or performance-data validation failure."""


def _number(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise NumeraiClientError("Numerai returned an invalid numeric value") from exc
    if not result.is_finite():
        raise NumeraiClientError("Numerai returned a non-finite numeric value")
    return result


def _time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str):
            raise ValueError("Timestamp is not a string")
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("Timestamp has no timezone")
        return result.astimezone(timezone.utc)
    except ValueError as exc:
        raise NumeraiClientError("Numerai returned an invalid timezone-aware timestamp") from exc


def _object(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NumeraiClientError(f"Numerai {description} is unavailable or not an object")
    return value


def _rows(value: Any, description: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise NumeraiClientError(f"Numerai returned invalid {description}")
    return value


def _text(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NumeraiClientError(f"Numerai returned invalid {description}")
    return value


def _integer(value: Any, description: str, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if type(value) is not int or value < 0:
        raise NumeraiClientError(f"Numerai returned invalid {description}")
    return value


def _boolean(value: Any, description: str, *, optional: bool = False) -> bool | None:
    if optional and value is None:
        return None
    if not isinstance(value, bool):
        raise NumeraiClientError(f"Numerai returned invalid {description}")
    return value


# Keep API metric names intact. NMR scalars arrive as strings; Float scalars
# are parsed directly to Decimal by requests' JSON decoder.
_NMR_FIELDS = {
    "availableNmr", "availableStakeCredit", "heldForFusionStakes", "heldForPendingWithdrawals",
    "heldForScheduledStakeIncreases", "onChainWalletBalance", "totalStake", "stakeValue", "value", "delta",
}


def _metrics(value: Any) -> Any:
    if isinstance(value, list):
        return [_metrics(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _number(item) if key in _NMR_FIELDS or key.endswith("Nmr") else _metrics(item)
            for key, item in value.items()
        }
    return value


def _history(value: Any, description: str, *, time_field: str = "date") -> list[dict[str, Any]]:
    rows = _rows(value, description)
    seen: set[str] = set()
    result = []
    for row in rows:
        timestamp = _text(row.get(time_field), f"{description} {time_field}")
        # API Date scalars are calendar dates, not midnight account observations.
        if len(timestamp) == 10:
            try:
                datetime.strptime(timestamp, "%Y-%m-%d")
            except ValueError as exc:
                raise NumeraiClientError(f"Numerai returned invalid {description} date") from exc
            key = timestamp
        else:
            key = _time(timestamp).isoformat()
        if key in seen:
            raise NumeraiClientError(f"Numerai returned duplicate {description} timestamps")
        seen.add(key)
        result.append({**_metrics(row), time_field: key})
    return sorted(result, key=lambda row: row[time_field])


def _score(row: dict[str, Any]) -> NumeraiScore:
    version = row.get("version")
    if version is not None:
        version = _text(version, "score version")
    return NumeraiScore(
        name=_text(row.get("displayName"), "score name"), version=version,
        value=_number(row.get("value")), percentile=_number(row.get("percentile")),
        date=_time(row.get("date")), day=_integer(row.get("day"), "score day", optional=True),
        resolved=_boolean(row.get("resolved"), "score resolved", optional=True),
        resolve_date=_time(row.get("resolveDate")),
        payout_pending_nmr=_number(row.get("payoutPending")),
        payout_settled_nmr=_number(row.get("payoutSettled")),
    )


def _score_order(score: NumeraiScore) -> tuple:
    return (score.date or datetime.min.replace(tzinfo=timezone.utc), score.day if score.day is not None else -1,
            score.name, score.version or "")


def _latest_scores(rows: list[dict[str, Any]]) -> list[NumeraiScore]:
    latest = {}
    for score in sorted((_score(row) for row in rows), key=_score_order):
        latest[score.name, score.version] = score
    return sorted(latest.values(), key=lambda score: (score.name, score.version or ""))


def _round(row: dict[str, Any], as_of: datetime) -> NumeraiRound:
    resolved = _boolean(row.get("roundResolved"), "round resolved")
    resolve_time = _time(row.get("roundResolveTime"))
    if resolved and resolve_time is None:
        raise NumeraiClientError("A resolved round is missing its resolve time")
    multipliers = _rows(row.get("payoutMultipliers"), "payout multipliers")
    return NumeraiRound(
        round_number=_integer(row.get("roundNumber"), "round number"),
        round_id=row.get("roundId"), submission_id=row.get("submissionId"), target=row.get("roundTarget"),
        data_datestamp=_integer(row.get("roundDataDatestamp"), "round datestamp", optional=True),
        open_time=_time(row.get("roundOpenTime")), score_time=_time(row.get("roundScoreTime")),
        close_staking_time=_time(row.get("roundCloseStakingTime")), resolve_time=resolve_time,
        resolved=resolved, overdue=not resolved and resolve_time is not None and resolve_time < as_of,
        at_risk_nmr=_number(row.get("atRisk")), payout_nmr=_number(row.get("payout")),
        payout_status="resolved" if resolved else "provisional",
        payout_factor=_number(row.get("roundPayoutFactor")),
        payout_multipliers=[{**m, "multiplier": _number(m.get("multiplier"))} for m in multipliers],
        scores=_latest_scores(_rows(row.get("submissionScores"), "round scores")),
    )


def _score_statistics(rounds: list[NumeraiRound]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[Decimal]] = {}
    for round_info in rounds:
        for score in round_info.scores:
            if score.value is not None:
                groups.setdefault((score.name, score.version), []).append(score.value)
    result = []
    for (name, version), values in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        count = len(values)
        mean = sum(values) / count
        std = (sum((value - mean) ** 2 for value in values) / (count - 1)).sqrt() if count > 1 else None
        result.append({"name": name, "version": version, "round_count": count, "mean": mean,
                       "sample_std": std, "sharpe_unannualized": mean / std if std else None,
                       "min": min(values), "max": max(values),
                       "positive_fraction": Decimal(sum(value > 0 for value in values)) / count})
    return result


class NumeraiClient:
    """Read performance data for the authenticated account and an owned Classic model."""

    def __init__(self, public_id: str, secret: str) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (public_id, secret)):
            raise NumeraiClientError("Numerai public ID and secret are required")
        self._authorization = f"Token {public_id}${secret}"
        self.session = requests.Session()

    def close(self) -> None:
        self.session.close()

    def _query(self, operation: str, query: str, variables: dict | None = None, *, authenticated: bool = False) -> dict:
        with trace(f"numerai.api.{operation}"):
            logger.info("Requesting Numerai %s", operation)
            try:
                response = self.session.post(
                    API_URL, json={"query": query, "variables": variables or {}},
                    headers={"Authorization": self._authorization} if authenticated else {}, timeout=(10, 45),
                )
                response.raise_for_status()
                payload = response.json(parse_float=Decimal)
            except (requests.RequestException, ValueError) as exc:
                # Do not include exception text: it may contain headers or payloads.
                raise NumeraiClientError(f"Numerai {operation} request failed ({type(exc).__name__})") from exc
            payload = _object(payload, f"{operation} response")
            if payload.get("errors"):
                raise NumeraiClientError(f"Numerai {operation} returned GraphQL errors; check API read permissions and schema")
            return _object(payload.get("data"), f"{operation} data")

    @trace("numerai.account")
    def get_account(self) -> dict[str, Any]:
        account = _object(self._query("account", queries.ACCOUNT, authenticated=True).get("account"), "account")
        _text(account.get("username"), "account username")
        _rows(account.get("models"), "account models")
        return account

    @trace("numerai.model_rounds")
    def get_model_rounds(self, model_id: str, *, as_of: datetime) -> list[NumeraiRound]:
        rounds = []
        seen: set[int] = set()
        offset = 0
        while True:
            page = _rows(self._query("rounds", queries.ROUNDS, {
                "modelId": model_id, "tournament": TOURNAMENT, "limit": PAGE_SIZE, "offset": offset,
            }).get("v2RoundModelPerformances"), "model rounds")
            for row in page:
                entry = _round(row, as_of)
                if entry.round_number in seen:
                    raise NumeraiClientError("Numerai returned duplicate rounds or repeated pagination")
                seen.add(entry.round_number)
                rounds.append(entry)
            if len(page) < PAGE_SIZE:
                return rounds
            offset += len(page)

    def _add_round_histories(self, model_id: str, all_rounds: list[NumeraiRound], selected: list[NumeraiRound]) -> None:
        numbers = {entry.round_number for entry in selected}
        blocks: list[list[int]] = []
        for index, entry in enumerate(all_rounds):
            if entry.round_number in numbers:
                if not blocks or index != blocks[-1][-1] + 1:
                    blocks.append([])
                blocks[-1].append(index)
        for block in blocks:
            page = _rows(self._query("round_history", queries.ROUND_HISTORY, {
                "modelId": model_id, "tournament": TOURNAMENT, "limit": len(block), "offset": block[0],
            }).get("v2RoundModelPerformances"), "round history")
            if len(page) != len(block):
                raise NumeraiClientError("Numerai round listing changed during fetch; retry")
            for index, row in zip(block, page):
                entry = all_rounds[index]
                if row.get("roundNumber") != entry.round_number or row.get("roundId") != entry.round_id:
                    raise NumeraiClientError("Numerai round listing changed during fetch; retry")
                entry.score_history = sorted(
                    (_score(score) for score in _rows(row.get("intraRoundSubmissionScores"), "score history")),
                    key=_score_order,
                )

    @trace("numerai.performance")
    def get_performance(self, model_name: str = "kaijo_model_01", *, round_count: int = 7,
                        as_of: datetime | None = None, expected_username: str | None = None) -> dict[str, Any]:
        model_name = _text(model_name, "requested model name")
        if type(round_count) is not int or not 1 <= round_count <= 100:
            raise ValueError("round_count must be between 1 and 100")
        now = datetime.now(timezone.utc) if as_of is None else as_of
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        now = now.astimezone(timezone.utc)
        account = self.get_account()
        username = account["username"]
        if expected_username and username.casefold() != expected_username.strip().casefold():
            raise NumeraiClientError("Numerai credentials do not match the configured username")
        owned = [m for m in account["models"] if m.get("tournament") == TOURNAMENT]
        matching = [m for m in owned if str(m.get("name", "")).casefold() == model_name.casefold()]
        if len(matching) != 1:
            raise NumeraiClientError("The requested Classic model is not uniquely owned by this account")
        model_id = _text(matching[0].get("id"), "model ID")
        overview = self._query("overview", queries.OVERVIEW, {
            "username": username, "model": matching[0]["name"], "tournament": TOURNAMENT,
        })
        account_profile = _object(overview.get("accountProfile"), "account profile")
        model = _object(overview.get("v3UserProfile"), "model profile")
        if account_profile.get("username") != username or account_profile.get("tournament") != TOURNAMENT:
            raise NumeraiClientError("Numerai returned a different account profile or tournament")
        if model.get("id") != model_id or model.get("tournament") != TOURNAMENT:
            raise NumeraiClientError("Numerai returned a different model or tournament")
        all_rounds = self.get_model_rounds(model_id, as_of=now)
        resolved = sorted((r for r in all_rounds if r.resolved), key=lambda r: (r.resolve_time, r.round_number), reverse=True)
        resolving = sorted((r for r in all_rounds if not r.resolved and r.resolve_time is not None and
                            (r.scores or r.score_time is not None and r.score_time <= now)),
                           key=lambda r: (r.resolve_time, r.round_number))
        last_resolved, next_resolving = resolved[:round_count], resolving[:round_count]
        self._add_round_histories(model_id, all_rounds, last_resolved + next_resolving)
        return {
            "tournament": {"id": TOURNAMENT, "name": "Numerai Classic"},
            "account": {
                "username": username,
                "balances": _metrics({key: account.get(key) for key in (
                    "availableNmr", "availableStakeCredit", "heldForFusionStakes", "heldForPendingWithdrawals",
                    "heldForScheduledStakeIncreases", "onChainWalletBalance",
                )}),
                "models": [{"name": m["name"], "id": m["id"], "tournament": m["tournament"], "archived": m.get("archived")} for m in owned],
                "summary": _metrics({key: account_profile.get(key) for key in ("totalStake", "returns", "scores")}),
                "history": {
                    "returns": _history(account_profile.get("returnsTs"), "account returns"),
                    "scores": _history(account_profile.get("scoresTs"), "account scores"),
                    "stake": _history(account_profile.get("totalStakeTs"), "account stake"),
                },
            },
            "model": {
                "name": matching[0]["name"], "id": model_id, "start_date": _time(model.get("startDate")),
                "active": _boolean(model.get("isActive"), "model active"),
                "summary": {"stake_nmr": _number(model.get("stakeValue")),
                            "returns": _metrics(model.get("latestReturns")),
                            "scores": _metrics(_rows(model.get("latestUserScores"), "model latest scores")),
                            "resolved_round_score_statistics": _score_statistics(resolved)},
                "history": {
                    "returns": _history(model.get("returns"), "model returns"),
                    "daily_performance": _history(model.get("dailyModelPerformances"), "model daily performance"),
                    "stake": _history(model.get("stakeValues"), "model stake", time_field="time"),
                },
            },
            "rounds": {
                "requested_count": round_count, "submitted_round_count": len(all_rounds),
                "resolved_round_count": len(resolved), "resolving_round_count": len(resolving),
                "last_resolved": last_resolved, "next_resolving": next_resolving,
                "last_resolved_statistics": _score_statistics(last_resolved),
                "next_resolving_statistics": _score_statistics(next_resolving),
                "history": sorted(all_rounds, key=lambda r: r.round_number),
            },
            "metric_notes": {
                "money_unit": "NMR", "return_units": "API-native rates; preserved without percentage conversion",
                "score_identity": "Group by name AND version; do not mix score definitions or canon/non-canon metrics",
                "round_windows": "Counts of submitted rounds, not days; the API roundResolved flag determines resolved rounds",
                "resolving": "Latest provisional observations, not predictions or guaranteed payouts; overdue rounds remain visible",
                "payouts": "Use round payout_nmr for totals; never sum repeated daily or per-score payout fields. A resolved round does not assert on-chain payout settlement",
                "sharpe": "Unannualized sample mean/sample standard deviation, grouped by exact metric identity",
                "history": "All API-returned account/model daily series and submitted rounds; selected windows also have intra-round observations",
            },
        }
