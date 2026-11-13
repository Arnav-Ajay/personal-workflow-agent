"""Offline tests for Coinbase portfolio, order and market reporting."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

from requests import HTTPError, Response, Timeout

from src.coinbase import (
    CoinbaseAccount,
    CoinbaseClient,
    CoinbaseClientError,
    CoinbaseCrypto,
    CoinbaseOrder,
    CoinbaseOrders,
    MarketMovement,
)


NOW = datetime(2026, 11, 11, 12, 34, 56, tzinfo=timezone.utc)
D = Decimal


def response(**values):
    return Mock(to_dict=Mock(return_value=values))


def not_found():
    http_response = Response()
    http_response.status_code = 404
    return HTTPError("market not found", response=http_response)


def wallet(currency="BTC", portfolio="p1", available="1", hold="0.5", **changes):
    data = {
        "uuid": f"{portfolio}-{currency}",
        "name": f"{currency} wallet",
        "currency": currency,
        "retail_portfolio_id": portfolio,
        "type": "ACCOUNT_TYPE_CRYPTO",
        "available_balance": {"value": available, "currency": currency},
        "hold": {"value": hold, "currency": currency},
        "active": True,
        "ready": True,
        "new_api_field": "ignored",
    }
    data.update(changes)
    return data


def order(order_id, status, last_fill=None, **changes):
    data = {
        "order_id": order_id,
        "product_id": "BTC-USDC",
        "side": "BUY",
        "status": status,
        "created_time": "2026-10-01T00:00:00Z",
        "last_fill_time": last_fill,
        "filled_size": "0.5",
        "filled_value": "50",
        "average_filled_price": "100",
        "total_fees": "1.25",
        "retail_portfolio_id": "p1",
    }
    data.update(changes)
    return data


class CoinbaseTests(unittest.TestCase):
    def setUp(self):
        self.sdk = Mock()
        self.client = CoinbaseClient.__new__(CoinbaseClient)
        self.client.client = self.sdk
        self.sdk.get_portfolios.return_value = response(portfolios=[
            {"uuid": "p1", "name": "Main"}, {"uuid": "p2", "name": "Other"}
        ])

    def configure_market(self):
        prices = {"BTC-USDC": "100", "ETH-USDC": "200", "NMR-USDC": "10"}
        old_prices = {"BTC-USDC": "80", "ETH-USDC": "250", "NMR-USDC": "8"}

        def product(product_id):
            if product_id not in prices:
                raise not_found()
            return response(price=prices[product_id])

        def candles(product_id, start, end, granularity, limit):
            self.assertEqual(granularity, "ONE_MINUTE")
            self.assertEqual(start, end)
            self.assertEqual(limit, 1)
            return response(candles=[{"start": start, "open": old_prices[product_id]}])

        self.sdk.get_public_product.side_effect = product
        self.sdk.get_public_candles.side_effect = candles

    def test_exports_and_credentials(self):
        self.assertTrue(all((CoinbaseCrypto, CoinbaseAccount, CoinbaseOrder, CoinbaseOrders, MarketMovement)))
        with self.assertRaisesRegex(CoinbaseClientError, "credentials"):
            CoinbaseClient("", "secret")
        with patch("coinbase.rest.RESTClient") as factory:
            client = CoinbaseClient("key", "secret")
        factory.assert_called_once_with(api_key="key", api_secret="secret", timeout=30)
        self.assertIs(client.client, factory.return_value)

    def test_invalid_credentials_raise_client_error(self):
        for key, secret in ((" ", "secret"), ("key", "\t"), (None, "secret")):
            with self.subTest(key=key, secret=secret), self.assertRaises(CoinbaseClientError):
                CoinbaseClient(key, secret)
        with patch("coinbase.rest.RESTClient", side_effect=ValueError("bad secret")):
            with self.assertRaisesRegex(CoinbaseClientError, "invalid format"):
                CoinbaseClient("key", "secret")

    def test_paginated_wallets_and_usdc_prices(self):
        self.configure_market()
        self.sdk.get_accounts.side_effect = [
            response(accounts=[wallet(), wallet("USDC", available="10", hold="2")], has_next=True, cursor="page2"),
            response(accounts=[wallet("ETH", available="2", hold="0")], has_next=False),
        ]
        wallets = self.client.get_user_portfolios(as_of=NOW)[0].wallets
        self.assertEqual(len(wallets), 3)
        btc, usdc, eth = wallets
        self.assertEqual(
            (btc.uuid, btc.name, btc.currency, btc.retail_portfolio_id),
            ("p1-BTC", "BTC wallet", "BTC", "p1"),
        )
        self.assertEqual((btc.available_balance, btc.hold), (D("1"), D("0.5")))
        self.assertTrue(btc.active)
        self.assertTrue(btc.ready)
        self.assertEqual(btc.total_balance, D("1.5"))
        self.assertEqual(btc.value_usdc, D("150"))
        self.assertEqual(btc.previous_week_value_usdc, D("120"))
        self.assertEqual(btc.weekly_change_usdc, D("30"))
        self.assertEqual(btc.weekly_change_percent, D("25"))
        self.assertEqual((usdc.value_usdc, usdc.weekly_change_usdc), (D("12"), D("0")))
        self.assertEqual((eth.value_usdc, eth.weekly_change_usdc, eth.weekly_change_percent),
                         (D("400"), D("-100"), D("-20")))
        self.assertEqual(self.sdk.get_accounts.call_args_list[1].kwargs["cursor"], "page2")

    def test_portfolios_aggregate_and_match_wallet_ids(self):
        self.configure_market()
        self.sdk.get_portfolios.return_value = response(portfolios=[
            {"uuid": "p1", "name": "Main"}, {"uuid": "p2", "name": "Other"}
        ])
        self.sdk.get_accounts.return_value = response(accounts=[
            wallet(), wallet("USDC", portfolio="p1", available="10", hold="0"),
            wallet("ETH", portfolio="p2", available="2", hold="0"),
        ], has_next=False)
        main, other = self.client.get_user_portfolios(as_of=NOW)
        self.assertEqual((main.id, main.name, main.total_value_usdc), ("p1", "Main", D("160")))
        self.assertEqual((main.previous_week_value_usdc, main.weekly_change_usdc), (D("130"), D("30")))
        self.assertEqual(main.weekly_change_percent, D("30") / D("130") * 100)
        self.assertEqual(len(main.wallets), 2)
        self.assertEqual((other.id, other.total_value_usdc), ("p2", D("400")))
        self.assertEqual([item.currency for item in other.wallets], ["ETH"])
        self.sdk.get_portfolios.assert_called_once_with()
        self.sdk.get_accounts.assert_called_once_with(cursor=None, limit=250)

    def test_unknown_portfolio_is_an_error(self):
        self.sdk.get_portfolios.return_value = response(portfolios=[])
        self.sdk.get_accounts.return_value = response(accounts=[wallet("USDC")], has_next=False)
        with self.assertRaisesRegex(CoinbaseClientError, "unknown portfolios"):
            self.client.get_user_portfolios(as_of=NOW)

    def test_portfolio_filter_skips_unrelated_assets(self):
        self.sdk.get_accounts.return_value = response(accounts=[
            wallet("USDC", portfolio="p1"), wallet("UNKNOWN", portfolio="p2")
        ], has_next=False)
        result = self.client.get_user_portfolios("p1", as_of=NOW)
        self.assertEqual([portfolio.id for portfolio in result], ["p1"])
        self.assertEqual([x.currency for x in result[0].wallets], ["USDC"])
        self.sdk.get_public_product.assert_not_called()

    def test_portfolio_filter_without_match_returns_empty(self):
        self.sdk.get_accounts.return_value = response(accounts=[wallet()], has_next=False)
        self.assertEqual(self.client.get_user_portfolios("missing", as_of=NOW), [])
        self.sdk.get_public_product.assert_not_called()

    def test_invalid_portfolio_response_fails_explicitly(self):
        for data in ({}, {"portfolios": {}}, {"portfolios": [None]}):
            with self.subTest(data=data):
                self.sdk.get_portfolios.return_value = response(**data)
                with self.assertRaisesRegex(CoinbaseClientError, "portfolios"):
                    self.client.get_user_portfolios(as_of=NOW)
        self.sdk.get_accounts.assert_not_called()

    def test_zero_wallet_requires_no_market_and_zero_percent_is_undefined(self):
        self.sdk.get_accounts.return_value = response(accounts=[wallet("UNKNOWN", available="0", hold="0")], has_next=False)
        item = self.client.get_user_portfolios(as_of=NOW)[0].wallets[0]
        self.assertEqual(item.value_usdc, D(0))
        self.assertIsNone(item.weekly_change_percent)
        self.sdk.get_public_product.assert_not_called()

    def test_price_cache_is_shared_by_wallets(self):
        self.configure_market()
        self.sdk.get_accounts.return_value = response(accounts=[wallet(), wallet(portfolio="p2")], has_next=False)
        self.client.get_user_portfolios(as_of=NOW)
        self.assertEqual(self.sdk.get_public_product.call_count, 1)
        self.assertEqual(self.sdk.get_public_candles.call_count, 1)

    def test_missing_usdc_market_is_an_error(self):
        self.sdk.get_accounts.return_value = response(accounts=[wallet("NMR")], has_next=False)
        self.sdk.get_public_product.side_effect = not_found()
        with self.assertRaises(CoinbaseClientError):
            self.client.get_user_portfolios(as_of=NOW)
        self.assertEqual(
            [call.kwargs["product_id"] for call in self.sdk.get_public_product.call_args_list],
            ["NMR-USDC"],
        )

    def test_network_failure_does_not_trigger_market_fallback(self):
        self.sdk.get_accounts.return_value = response(accounts=[wallet()], has_next=False)
        self.sdk.get_public_product.side_effect = Timeout("network timeout")
        with self.assertRaisesRegex(CoinbaseClientError, "network timeout"):
            self.client.get_user_portfolios(as_of=NOW)
        self.assertEqual(self.sdk.get_public_product.call_count, 1)

    def test_fiat_wallets_are_excluded_with_warning(self):
        self.sdk.get_accounts.return_value = response(accounts=[
            wallet("CAD", type="ACCOUNT_TYPE_FIAT"),
            wallet("USD", type="ACCOUNT_TYPE_FIAT"),
            wallet("USDC", available="5", hold="1"),
        ], has_next=False)
        with self.assertLogs("src.coinbase.coinbase_api", level="WARNING") as logs:
            wallets = self.client.get_user_portfolios(as_of=NOW)[0].wallets
        self.assertEqual([item.currency for item in wallets], ["USDC"])
        self.assertEqual(wallets[0].value_usdc, D("6"))
        self.assertEqual(len(logs.records), 2)
        self.sdk.get_public_product.assert_not_called()

    def test_portfolio_total_excludes_fiat_wallets(self):
        self.sdk.get_portfolios.return_value = response(portfolios=[{"uuid": "p1", "name": "Main"}])
        self.sdk.get_accounts.return_value = response(accounts=[
            wallet("CAD", available="500", hold="0", type="ACCOUNT_TYPE_FIAT"),
            wallet("USDC", available="25", hold="0"),
        ], has_next=False)
        with self.assertLogs("src.coinbase.coinbase_api", level="WARNING"):
            portfolio = self.client.get_user_portfolios(as_of=NOW)[0]
        self.assertEqual(portfolio.total_value_usdc, D("25"))
        self.assertEqual([item.currency for item in portfolio.wallets], ["USDC"])

    def test_wallet_type_is_required_and_unknown_type_is_an_error(self):
        for kind in (None, "ACCOUNT_TYPE_UNSPECIFIED", "ACCOUNT_TYPE_PERP_FUTURES"):
            with self.subTest(kind=kind):
                self.sdk.get_accounts.return_value = response(
                    accounts=[wallet(type=kind)], has_next=False
                )
                with self.assertRaises(CoinbaseClientError):
                    self.client.get_user_portfolios(as_of=NOW)

    def test_nmr_usdc_market_and_vault_balance(self):
        self.configure_market()
        self.sdk.get_accounts.return_value = response(accounts=[
            wallet("NMR", available="2", hold="1", type="ACCOUNT_TYPE_VAULT")
        ], has_next=False)
        item = self.client.get_user_portfolios(as_of=NOW)[0].wallets[0]
        self.assertEqual((item.value_usdc, item.previous_week_value_usdc, item.weekly_change_usdc),
                         (D("30"), D("24"), D("6")))
        self.sdk.get_public_product.assert_called_once_with(product_id="NMR-USDC")

    def test_bad_balances_and_invalid_response_fail_explicitly(self):
        for value in ("NaN", "infinity", "not-a-number", "-1"):
            with self.subTest(value=value):
                self.sdk.get_accounts.return_value = response(accounts=[wallet(available=value, hold="0")], has_next=False)
                with self.assertRaises(CoinbaseClientError):
                    self.client.get_user_portfolios(as_of=NOW)
        self.sdk.get_accounts.return_value = response(accounts=[wallet(available_balance=None)], has_next=False)
        with self.assertRaisesRegex(CoinbaseClientError, "available_balance"):
            self.client.get_user_portfolios(as_of=NOW)
        mismatch = wallet()
        mismatch["hold"] = {"value": "1", "currency": "ETH"}
        self.sdk.get_accounts.return_value = response(accounts=[mismatch], has_next=False)
        with self.assertRaisesRegex(CoinbaseClientError, "mismatched"):
            self.client.get_user_portfolios(as_of=NOW)

    def test_repeated_and_missing_cursors_are_errors(self):
        for cursor in (None, "again"):
            with self.subTest(cursor=cursor):
                self.sdk.get_accounts.return_value = response(accounts=[], has_next=True, cursor=cursor)
                with self.assertRaisesRegex(CoinbaseClientError, "cursor"):
                    self.client.get_user_portfolios(as_of=NOW)

    def test_missing_api_data_is_not_an_empty_success(self):
        self.sdk.get_accounts.return_value = response()
        with self.assertRaisesRegex(CoinbaseClientError, "accounts"):
            self.client.get_user_portfolios(as_of=NOW)
        self.sdk.get_accounts.side_effect = Timeout("unavailable")
        with self.assertRaisesRegex(CoinbaseClientError, "unavailable"):
            self.client.get_user_portfolios(as_of=NOW)

    def test_empty_portfolio_has_no_percent_change(self):
        self.sdk.get_portfolios.return_value = response(portfolios=[{"uuid": "p1", "name": "Empty"}])
        self.sdk.get_accounts.return_value = response(accounts=[], has_next=False)
        result = self.client.get_user_portfolios(as_of=NOW)[0]
        self.assertEqual(result.total_value_usdc, D(0))
        self.assertIsNone(result.weekly_change_percent)

    def test_negative_balance_components_cannot_cancel_each_other(self):
        for available, hold in (("-1", "2"), ("2", "-1"), ("-1", "1")):
            with self.subTest(available=available, hold=hold):
                self.sdk.get_accounts.return_value = response(
                    accounts=[wallet("USDC", available=available, hold=hold)], has_next=False
                )
                with self.assertRaisesRegex(CoinbaseClientError, "negative balance"):
                    self.client.get_user_portfolios(as_of=NOW)

    def test_duplicate_wallet_across_pages_does_not_inflate_portfolio(self):
        self.sdk.get_accounts.side_effect = [
            response(accounts=[wallet("USDC")], has_next=True, cursor="next"),
            response(accounts=[wallet("USDC")], has_next=False),
        ]
        with self.assertRaisesRegex(CoinbaseClientError, "duplicate wallet"):
            self.client.get_user_portfolios(as_of=NOW)

    def test_duplicate_portfolios_are_rejected_before_loading_wallets(self):
        self.sdk.get_portfolios.return_value = response(portfolios=[
            {"uuid": "p1", "name": "Main"}, {"uuid": "p1", "name": "Duplicate"}
        ])
        with self.assertRaisesRegex(CoinbaseClientError, "duplicate portfolio"):
            self.client.get_user_portfolios(as_of=NOW)
        self.sdk.get_accounts.assert_not_called()

    def test_wallet_identity_and_flags_require_correct_types(self):
        for changes in (
            {"uuid": []}, {"retail_portfolio_id": {}}, {"currency": []},
            {"type": []}, {"name": "   "}, {"active": "false"}, {"ready": 0},
        ):
            with self.subTest(changes=changes):
                row = wallet("USDC")
                row.update(changes)
                self.sdk.get_accounts.return_value = response(accounts=[row], has_next=False)
                with self.assertRaises(CoinbaseClientError):
                    self.client.get_user_portfolios(as_of=NOW)
        self.sdk.get_accounts.return_value = response(
            accounts=[wallet("USDC", active=False, ready=False)], has_next=False
        )
        item = self.client.get_user_portfolios(as_of=NOW)[0].wallets[0]
        self.assertIs(item.active, False)
        self.assertIs(item.ready, False)

    def test_malformed_sdk_responses_raise_client_errors(self):
        for invalid in (None, Mock(to_dict=Mock(return_value=[]))):
            with self.subTest(response=invalid):
                self.sdk.get_portfolios.return_value = invalid
                with self.assertRaises(CoinbaseClientError):
                    self.client.get_user_portfolios(as_of=NOW)
        for error in (ValueError("bad JSON"), TypeError("invalid data"), Exception("JWT signing failed")):
            with self.subTest(error=error):
                self.sdk.get_portfolios.side_effect = error
                with self.assertRaises(CoinbaseClientError) as caught:
                    self.client.get_user_portfolios(as_of=NOW)
                self.assertIs(caught.exception.__cause__, error)

    def test_orders_requiring_additional_authentication_are_not_empty_success(self):
        self.sdk.list_orders.return_value = response(
            orders=[], has_next=False, proof_token_required=True
        )
        with self.assertRaisesRegex(CoinbaseClientError, "additional authentication"):
            self.client.get_orders(as_of=NOW)

    def test_malformed_pagination_metadata_is_rejected(self):
        for metadata in ({"has_next": "false"}, {"has_next": 0}, {"has_next": True, "cursor": []}):
            with self.subTest(metadata=metadata):
                self.sdk.get_accounts.return_value = response(accounts=[], **metadata)
                with self.assertRaises(CoinbaseClientError):
                    self.client.get_user_portfolios(as_of=NOW)

    def test_invalid_order_identity_and_timestamps_raise_client_errors(self):
        for changes in ({"order_id": []}, {"side": []}, {"status": []}, {"created_time": 123}):
            with self.subTest(changes=changes):
                row = order("open", "OPEN")
                row.update(changes)
                self.sdk.list_orders.return_value = response(orders=[row], has_next=False)
                with self.assertRaises(CoinbaseClientError):
                    self.client.get_orders(as_of=NOW)

    def test_watchlist_reuses_prices_for_duplicate_products(self):
        self.configure_market()
        movements = self.client.get_watchlist_movement(["BTC-USDC", "ETH-USDC", "BTC-USDC"], as_of=NOW)
        self.assertEqual([item.product_id for item in movements], ["BTC-USDC", "ETH-USDC", "BTC-USDC"])
        self.assertEqual(movements[0], movements[2])
        self.assertEqual(self.sdk.get_public_product.call_count, 2)
        self.assertEqual(self.sdk.get_public_candles.call_count, 6)

    def test_invalid_watchlist_is_validated_before_requests(self):
        with self.assertRaises(ValueError):
            self.client.get_watchlist_movement(["BTC-USDC", ""], as_of=NOW)
        self.sdk.get_public_product.assert_not_called()

    def test_orders_use_fill_time_not_creation_time_and_paginate(self):
        start = (NOW - timedelta(days=7)).isoformat()
        def list_orders(**kwargs):
            statuses = kwargs["order_status"]
            self.assertEqual(len(statuses), 1, "Coinbase rejects combined active statuses")
            source, status, cursor = kwargs["order_placement_source"], statuses[0], kwargs["cursor"]
            self.assertNotIn("start_date", kwargs)
            self.assertEqual(kwargs["retail_portfolio_id"], "p1")
            if source == "RETAIL_ADVANCED" and status == "OPEN":
                if cursor is None:
                    return response(orders=[order("open", "OPEN")], has_next=True, cursor="next")
                self.assertEqual(cursor, "next")
                return response(orders=[order("open2", "OPEN")], has_next=False)
            if source == "RETAIL_ADVANCED" and status == "FILLED":
                return response(orders=[
                    order("at-start", "FILLED", start),
                    order("old", "FILLED", (NOW - timedelta(days=7, seconds=1)).isoformat()),
                    order("sell", "FILLED", NOW.isoformat(), side="SELL"),
                    order("future", "FILLED", (NOW + timedelta(seconds=1)).isoformat()),
                ], has_next=False)
            if source == "RETAIL_ADVANCED" and status == "CANCEL_QUEUED":
                return response(orders=[order("queued", status)], has_next=False)
            if source == "RETAIL_SIMPLE" and status == "OPEN":
                return response(orders=[order("simple", status), order("open", status)], has_next=False)
            return response(orders=[], has_next=False)

        self.sdk.list_orders.side_effect = list_orders
        result = self.client.get_orders("p1", as_of=NOW)
        self.assertEqual({x.order_id for x in result.in_progress}, {"open", "open2", "queued", "simple"})
        self.assertEqual({x.order_id for x in result.filled_last_week}, {"at-start", "sell"})
        self.assertEqual(next(x for x in result.filled_last_week if x.order_id == "sell").side, "SELL")
        self.assertEqual(result.in_progress[0].total_fees, D("1.25"))
        queries = {
            (call.kwargs["order_placement_source"], call.kwargs["order_status"][0])
            for call in self.sdk.list_orders.call_args_list
        }
        self.assertEqual(queries, {
            (source, status)
            for source in ("RETAIL_ADVANCED", "RETAIL_SIMPLE")
            for status in ("OPEN", "PENDING", "QUEUED", "CANCEL_QUEUED", "EDIT_QUEUED", "FILLED")
        })
        self.assertEqual(self.sdk.list_orders.call_count, 13)

    def test_orders_missing_fill_time_and_unexpected_status_fail(self):
        for item in (order("x", "FILLED"), order("y", "CANCELLED"), order("z", "OPEN", side="UNKNOWN")):
            with self.subTest(item=item):
                self.sdk.list_orders.return_value = response(orders=[item], has_next=False)
                with self.assertRaises(CoinbaseClientError):
                    self.client.get_orders(as_of=NOW)

    def test_order_filter_is_enforced_locally_and_missing_portfolio_fails(self):
        self.sdk.list_orders.return_value = response(orders=[
            order("selected", "OPEN", order_configuration={"limit_limit_gtc": {"base_size": "2"}}),
            order("other", "OPEN", retail_portfolio_id="p2"),
        ], has_next=False)
        result = self.client.get_orders("p1", as_of=NOW)
        self.assertEqual([item.order_id for item in result.in_progress], ["selected"])
        self.assertEqual(result.in_progress[0].order_configuration["limit_limit_gtc"]["base_size"], "2")
        self.sdk.list_orders.return_value = response(orders=[order("missing", "OPEN", retail_portfolio_id=None)], has_next=False)
        with self.assertRaisesRegex(CoinbaseClientError, "retail_portfolio_id"):
            self.client.get_orders("p1", as_of=NOW)

    def test_order_optional_filled_fields_remain_unknown(self):
        self.sdk.list_orders.return_value = response(orders=[
            order("open", "OPEN", filled_size=None, filled_value=None, average_filled_price=None)
        ], has_next=False)
        result = self.client.get_orders(as_of=NOW).in_progress[0]
        self.assertIsNone(result.filled_size)
        self.assertIsNone(result.filled_value)
        self.assertIsNone(result.average_filled_price)

    def test_non_usdc_order_retains_its_quote_currency(self):
        self.sdk.list_orders.return_value = response(
            orders=[order("original", "OPEN", product_id="BTC-USD", filled_value="50")],
            has_next=False,
        )
        item = self.client.get_orders(as_of=NOW).in_progress[0]
        self.assertEqual((item.product_id, item.filled_value), ("BTC-USD", D("50")))

    def test_sdk_response_objects_are_converted(self):
        from coinbase.rest.types.accounts_types import ListAccountsResponse
        from coinbase.rest.types.orders_types import ListOrdersResponse
        from coinbase.rest.types.portfolios_types import ListPortfoliosResponse

        self.sdk.get_accounts.return_value = ListAccountsResponse({
            "accounts": [wallet("USDC", available="5", hold="2")], "has_next": False
        })
        self.sdk.get_portfolios.return_value = ListPortfoliosResponse({
            "portfolios": [{"uuid": "p1", "name": "Main"}]
        })
        self.sdk.list_orders.return_value = ListOrdersResponse({
            "orders": [order("a", "OPEN", order_configuration={"limit_limit_gtc": {"base_size": "2"}})],
            "has_next": False,
        })
        self.assertEqual(self.client.get_user_portfolios(as_of=NOW)[0].total_value_usdc, D("7"))
        self.assertEqual(
            self.client.get_orders(as_of=NOW).in_progress[0].order_configuration,
            {"limit_limit_gtc": {"base_size": "2"}},
        )

    def test_watchlist_prices_use_each_rolling_period(self):
        self.sdk.get_public_product.return_value = response(price="120")

        def candles(product_id, start, end, granularity, limit):
            self.assertEqual((product_id, end, granularity, limit), ("BTC-USDC", start, "ONE_MINUTE", 1))
            previous = {
                int((NOW - timedelta(days=1)).timestamp()) // 60 * 60: "100",
                int((NOW - timedelta(days=7)).timestamp()) // 60 * 60: "150",
                int((NOW - timedelta(days=30)).timestamp()) // 60 * 60: "80",
            }[int(start)]
            return response(candles=[{"start": start, "open": previous}])

        self.sdk.get_public_candles.side_effect = candles
        movement = self.client.get_watchlist_movement(["BTC-USDC"], as_of=NOW)[0]
        self.assertEqual((movement.product_id, movement.daily_percent, movement.weekly_percent, movement.monthly_percent),
                         ("BTC-USDC", D("20"), D("-20"), D("50")))

    def test_non_usdc_watchlist_product_is_preserved(self):
        self.sdk.get_public_product.return_value = response(price="120")
        self.sdk.get_public_candles.side_effect = lambda **kwargs: response(
            candles=[{"start": kwargs["start"], "open": "100"}]
        )
        movement = self.client.get_watchlist_movement(["BTC-USD"], as_of=NOW)[0]
        self.assertEqual((movement.product_id, movement.weekly_percent), ("BTC-USD", D("20")))
        self.sdk.get_public_product.assert_called_once_with(product_id="BTC-USD")

    def test_illiquid_minute_uses_latest_completed_hour(self):
        target = NOW - timedelta(days=7)
        last_hour = int(target.timestamp()) // 3600 * 3600 - 3600
        self.sdk.get_public_candles.side_effect = [
            response(candles=[]),
            response(candles=[
                {"start": str(last_hour - 7200), "close": "1.2"},
                {"start": str(last_hour), "close": "1.4"},
                {"start": str(last_hour + 3600), "close": "9"},
            ]),
        ]
        self.assertEqual(self.client._historical_price("NMR-USDC", target), D("1.4"))
        self.assertEqual(self.sdk.get_public_candles.call_args_list[1].kwargs["granularity"], "ONE_HOUR")

    def test_missing_or_invalid_historical_prices_fail(self):
        self.sdk.get_public_candles.side_effect = [response(candles=[]), response(candles=[])]
        with self.assertRaisesRegex(CoinbaseClientError, "no recent"):
            self.client._historical_price("BTC-USDC", NOW)
        start = str(int(NOW.timestamp()) // 60 * 60)
        self.sdk.get_public_candles.side_effect = None
        self.sdk.get_public_candles.return_value = response(candles=[{"start": start, "open": "0"}])
        with self.assertRaisesRegex(CoinbaseClientError, "positive"):
            self.client._historical_price("BTC-USDC", NOW)
        self.sdk.get_public_candles.return_value = response(candles=[None])
        with self.assertRaisesRegex(CoinbaseClientError, "invalid candles"):
            self.client._historical_price("BTC-USDC", NOW)

    def test_empty_and_invalid_watchlist_and_naive_time(self):
        self.assertEqual(self.client.get_watchlist_movement([], as_of=NOW), [])
        with self.assertRaises(ValueError):
            self.client.get_watchlist_movement([""], as_of=NOW)
        for as_of in (NOW.replace(tzinfo=None), "2026-11-11"):
            with self.subTest(as_of=as_of), self.assertRaisesRegex(ValueError, "timezone-aware"):
                self.client.get_user_portfolios(as_of=as_of)


if __name__ == "__main__":
    unittest.main()
