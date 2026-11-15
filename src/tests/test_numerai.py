"""Offline coverage for Numerai performance scope, round selection and failures."""

import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

import requests

from src.numerai import NumeraiClient, NumeraiClientError
from src.numerai import queries

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def score(value="0.1", *, version="1", day=20, resolved=True):
    return {"displayName": "CORR", "version": version, "value": value, "percentile": "0.9",
            "date": (NOW - timedelta(days=1)).isoformat(), "day": day, "resolved": resolved,
            "resolveDate": NOW.isoformat(), "payoutPending": None, "payoutSettled": "0.01"}


def round_row(number, resolved):
    resolve_time = NOW + timedelta(days=number - 10 if not resolved else number - 11)
    return {"roundId": f"round-{number}", "roundNumber": number, "submissionId": f"submission-{number}",
            "roundTarget": "main", "roundOpenTime": (NOW - timedelta(days=30)).isoformat(),
            "roundScoreTime": (NOW - timedelta(days=5)).isoformat(), "roundCloseStakingTime": None,
            "roundResolveTime": resolve_time.isoformat(), "roundResolved": resolved,
            "roundDataDatestamp": 20261101, "atRisk": "1.234567890123456789", "payout": "0.1",
            "roundPayoutFactor": "0.8", "payoutMultipliers": [
                {"id": "config-1", "scoreConfigId": "score-config-1", "name": "correlation",
                 "version": "1", "displayName": "CORR", "multiplier": "0.5"}],
            "submissionScores": [score(resolved=resolved), score("0.2", version="2", resolved=resolved)]}


def account_data():
    return {"username": "test_account", "availableNmr": "0.1234567890123456789",
            "models": [{"name": "kaijo_model_01", "id": "owned-model", "tournament": 8, "archived": False},
                       {"name": "other_model", "id": "signals-model", "tournament": 11}]}


def overview_data():
    return {
        "accountProfile": {"username": "test_account", "tournament": 8, "totalStake": "10.5",
                           "returns": {"allTime": Decimal("0.15"), "allTimeNmr": "1.2345"},
                           "scores": {"corrLtm": Decimal("0.1"), "corrLtmRank": 123},
                           "returnsTs": [{"date": "2026-11-13", "allTimeNmr": "1.2"}],
                           "scoresTs": [{"date": "2026-11-13", "corr": Decimal("0.1")}],
                           "totalStakeTs": [{"date": "2026-11-13", "time": NOW.isoformat(), "value": "10.5", "delta": "0.5"}]},
        "v3UserProfile": {"id": "owned-model", "username": "kaijo_model_01", "accountName": "test_account",
                          "tournament": 8, "startDate": "2026-01-01T00:00:00Z", "isActive": True,
                          "stakeValue": "10.5", "latestReturns": {"allTimeNmr": "1.2"},
                          "latestUserScores": [{"displayName": "CORR", "reputation": Decimal("0.1"), "rank": 12}],
                          "returns": [{"date": "2026-11-13", "allTimeNmr": "1.2"}],
                          "stakeValues": [{"time": NOW.isoformat(), "value": "10.5", "delta": "0.5"}],
                          "dailyModelPerformances": [{"date": NOW.isoformat(), "corrRep": Decimal("0.1"), "corrRank": 12}]},
    }


def query_responder(account, overview, rounds):
    def query(operation, document, variables=None, **kwargs):
        if operation == "account":
            return {"account": deepcopy(account)}
        if operation == "overview":
            return deepcopy(overview)
        page = deepcopy(rounds[variables["offset"]:variables["offset"] + variables["limit"]])
        if operation == "round_history":
            for row in page:
                row["intraRoundSubmissionScores"] = [score(day=1), score(day=2), score("-0.2", version="2", day=2)]
        return {"v2RoundModelPerformances": page}
    return query


class NumeraiTests(unittest.TestCase):
    def setUp(self):
        self.session = self.enterContext(patch("src.numerai.client.requests.Session")).return_value
        self.client = NumeraiClient("test-public-id", "never-log-this-secret")
        self.addCleanup(self.client.close)
        self.account, self.overview = account_data(), overview_data()
        self.rounds = [round_row(number, number <= 10) for number in range(20, 0, -1)]

    def configure(self):
        return self.enterContext(patch.object(self.client, "_query", side_effect=query_responder(
            self.account, self.overview, self.rounds)))

    def test_selects_exactly_seven_by_resolution_order_and_retains_all_history(self):
        query = self.configure()
        data = self.client.get_performance(as_of=NOW)
        self.assertEqual([r.round_number for r in data["rounds"]["last_resolved"]], [10, 9, 8, 7, 6, 5, 4])
        self.assertEqual([r.round_number for r in data["rounds"]["next_resolving"]], [11, 12, 13, 14, 15, 16, 17])
        self.assertEqual(len(data["rounds"]["history"]), 20)
        self.assertTrue(all(len(r.score_history) == 3 for r in data["rounds"]["last_resolved"]))
        self.assertEqual(data["account"]["balances"]["availableNmr"], Decimal("0.1234567890123456789"))
        self.assertEqual(len(data["account"]["models"]), 1)
        self.assertEqual(data["model"]["summary"]["stake_nmr"], Decimal("10.5"))
        self.assertEqual(len(data["model"]["summary"]["resolved_round_score_statistics"]), 2)
        self.assertEqual(sum(call.args[0] == "round_history" for call in query.call_args_list), 1)

    def test_overdue_unresolved_round_is_not_misclassified_as_settled(self):
        self.rounds[0]["roundResolveTime"] = (NOW - timedelta(days=2)).isoformat()
        self.configure()
        data = self.client.get_performance(as_of=NOW)
        self.assertEqual(data["rounds"]["next_resolving"][0].round_number, 20)
        self.assertTrue(data["rounds"]["next_resolving"][0].overdue)
        self.assertEqual(data["rounds"]["next_resolving"][0].payout_status, "provisional")

    def test_unscored_future_round_is_not_a_resolving_round(self):
        self.rounds[0].update(roundScoreTime=(NOW + timedelta(days=1)).isoformat(), submissionScores=[])
        self.configure()
        data = self.client.get_performance(as_of=NOW, round_count=100)
        self.assertEqual(data["rounds"]["resolving_round_count"], 9)
        self.assertNotIn(20, [r.round_number for r in data["rounds"]["next_resolving"]])

    def test_short_histories_do_not_invent_rounds_and_versions_stay_separate(self):
        self.rounds = self.rounds[-2:]
        self.configure()
        data = self.client.get_performance(as_of=NOW)
        self.assertEqual(len(data["rounds"]["last_resolved"]), 2)
        self.assertEqual(data["rounds"]["next_resolving"], [])
        stats = data["rounds"]["last_resolved_statistics"]
        self.assertEqual([(s["name"], s["version"]) for s in stats], [("CORR", "1"), ("CORR", "2")])
        self.assertTrue(all(s["sharpe_unannualized"] is None for s in stats))

    def test_pagination_reads_every_page(self):
        self.configure()
        with patch("src.numerai.client.PAGE_SIZE", 3):
            rows = self.client.get_model_rounds("owned-model", as_of=NOW)
        self.assertEqual(len(rows), 20)

    def test_duplicate_round_or_repeated_page_is_rejected(self):
        self.rounds[1] = deepcopy(self.rounds[0])
        self.configure()
        with self.assertRaisesRegex(NumeraiClientError, "duplicate"):
            self.client.get_performance(as_of=NOW)

    def test_round_changes_between_listing_and_detailed_fetch_fail(self):
        query = self.configure()
        original = query.side_effect
        def changed(operation, document, variables=None, **kwargs):
            data = original(operation, document, variables, **kwargs)
            if operation == "round_history":
                data["v2RoundModelPerformances"][0]["roundId"] = "different-round"
            return data
        query.side_effect = changed
        with self.assertRaisesRegex(NumeraiClientError, "changed during fetch"):
            self.client.get_performance(as_of=NOW)

    def test_model_ownership_and_account_identity_are_checked(self):
        query = self.configure()
        with self.assertRaisesRegex(NumeraiClientError, "configured username"):
            self.client.get_performance(as_of=NOW, expected_username="other_account")
        with self.assertRaisesRegex(NumeraiClientError, "owned"):
            self.client.get_performance("other_model", as_of=NOW)
        self.assertFalse(any(call.args[0] == "overview" for call in query.call_args_list))

    def test_profile_must_match_requested_model(self):
        self.overview["v3UserProfile"]["id"] = "different-model"
        self.configure()
        with self.assertRaisesRegex(NumeraiClientError, "different model"):
            self.client.get_performance(as_of=NOW)

    def test_invalid_dates_flags_and_numeric_values_fail_explicitly(self):
        for changes in ({"roundResolved": "false"}, {"roundResolveTime": "2026-11-01"}, {"atRisk": "NaN"}, {"roundNumber": True}):
            with self.subTest(changes=changes):
                self.rounds[0] = {**round_row(20, False), **changes}
                with patch.object(self.client, "_query", return_value={"v2RoundModelPerformances": self.rounds}):
                    with self.assertRaises(NumeraiClientError):
                        self.client.get_model_rounds("owned-model", as_of=NOW)

    def test_bad_arguments_do_not_make_network_requests(self):
        for count in (0, -1, True, 101):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.client.get_performance(round_count=count)
        with self.assertRaises(ValueError):
            self.client.get_performance(as_of=NOW.replace(tzinfo=None))
        self.session.post.assert_not_called()

    def test_transport_uses_decimal_decoding_and_auth_only_when_requested(self):
        response = self.session.post.return_value
        response.json.return_value = {"data": {"value": "ok"}}
        self.client._query("public", "query { x }")
        self.assertEqual(self.session.post.call_args.kwargs["headers"], {})
        self.client._query("private", queries.ACCOUNT, authenticated=True)
        self.assertIn("Authorization", self.session.post.call_args.kwargs["headers"])
        response.json.assert_called_with(parse_float=Decimal)
        self.assertEqual(self.session.post.call_args.kwargs["timeout"], (10, 45))
        self.assertNotIn("email", queries.ACCOUNT)
        self.assertNotIn("walletAddress", queries.ACCOUNT)
        self.assertNotIn("apiTokens", queries.ACCOUNT)

    def test_transport_and_graphql_errors_do_not_expose_payloads(self):
        self.session.post.side_effect = requests.Timeout("never-log-this-secret")
        with self.assertRaises(NumeraiClientError) as caught:
            self.client._query("test", "query { x }")
        self.assertNotIn("never-log-this-secret", str(caught.exception))
        self.session.post.side_effect = None
        self.session.post.return_value.json.return_value = {"data": {"partial": True}, "errors": [{"message": "private details"}]}
        with self.assertRaises(NumeraiClientError) as caught:
            self.client._query("test", "query { x }")
        self.assertNotIn("private details", str(caught.exception))

    def test_credentials_and_close(self):
        with self.assertRaises(NumeraiClientError):
            NumeraiClient("", "secret")
        self.client.close()
        self.session.close.assert_called_once_with()
