"""Callable transformations from Numerai snapshots to graph-ready tables."""

from typing import Any


def trend_tables(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Keep round scores in long form so score names/versions cannot collide."""
    tables = {}
    for owner in ("account", "model"):
        for name, rows in data[owner]["history"].items():
            tables[f"{owner}_{name}"] = rows
    rounds = data["rounds"]
    selected = {row["round_number"]: "last_resolved" for row in rounds["last_resolved"]}
    selected.update({row["round_number"]: "next_resolving" for row in rounds["next_resolving"]})
    tables.update(round_scores=[], round_payouts=[], selected_round_score_history=[])
    for row in rounds["history"]:
        context = {"round_number": row["round_number"], "resolved": row["resolved"],
                   "resolve_time": row["resolve_time"], "window": selected.get(row["round_number"], "history")}
        tables["round_payouts"].append({**context, "at_risk_nmr": row["at_risk_nmr"],
                                       "payout_nmr": row["payout_nmr"], "payout_status": row["payout_status"]})
        for score in row["scores"]:
            tables["round_scores"].append({**context, **{f"score_{k}": v for k, v in score.items()}})
        for score in row["score_history"]:
            tables["selected_round_score_history"].append({**context, **{f"score_{k}": v for k, v in score.items()}})
    return tables

