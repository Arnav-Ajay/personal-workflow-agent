"""Tests for the executable Coinbase portfolio summary."""

import os
from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import coinbase_account_summary
from src.coinbase import CoinbaseAccount, CoinbaseClient, CoinbaseClientError, CoinbaseCrypto, CoinbaseOrders


class CoinbaseAccountSummaryTests(unittest.TestCase):
    def test_direct_execution_resolves_import_from_another_directory(self):
        env = os.environ.copy()
        env["COINBASE_API_KEY"] = ""
        env["COINBASE_API_SECRET"] = ""
        script = Path(__file__).resolve().parents[2] / "scripts" / "coinbase_account_summary.py"
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [sys.executable, "-I", str(script)],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing COINBASE_API_KEY, COINBASE_API_SECRET", result.stderr)
        self.assertNotIn("ImportError", result.stderr)

    def test_main_prints_portfolios_with_wallets_and_orders(self):
        output = StringIO()
        orders = CoinbaseOrders(in_progress=[], filled_last_week=[])
        with (
            patch.object(coinbase_account_summary, "load_dotenv") as load,
            patch.object(coinbase_account_summary, "CoinbaseClient", spec=CoinbaseClient) as client,
            patch.dict(os.environ, {"COINBASE_API_KEY": "key", "COINBASE_API_SECRET": "secret"}),
            redirect_stdout(output),
        ):
            wallet = CoinbaseCrypto(
                uuid="wallet-one", name="USDC wallet", currency="USDC", retail_portfolio_id="p1",
                available_balance=Decimal("10"), hold=Decimal("2"), value_usdc=Decimal("12"),
                previous_week_value_usdc=Decimal("12"), weekly_change_usdc=Decimal("0"),
                weekly_change_percent=Decimal("0"), active=True, ready=True,
            )
            client.return_value.get_user_portfolios.return_value = [CoinbaseAccount(
                id="p1", name="portfolio one", total_value_usdc=Decimal("12"), wallets=[wallet],
                previous_week_value_usdc=Decimal("12"), weekly_change_usdc=Decimal("0"),
                weekly_change_percent=Decimal("0"),
            )]
            client.return_value.get_orders.return_value = orders
            self.assertEqual(coinbase_account_summary.main(), 0)
        load.assert_called_once_with(coinbase_account_summary.PROJECT_ROOT / ".env")
        client.assert_called_once_with("key", "secret")
        text = output.getvalue()
        client.return_value.get_user_portfolios.assert_called_once_with()
        client.return_value.get_orders.assert_called_once_with()
        self.assertEqual(text.count("USDC wallet"), 1)
        self.assertIn("available_balance=Decimal('10')", text)
        self.assertIn("hold=Decimal('2')", text)
        self.assertIn("Portfolios:", text)
        self.assertIn("portfolio one", text)
        self.assertIn("Orders:", text)
        self.assertIn("CoinbaseOrders(in_progress=[], filled_last_week=[])", text)

    def test_client_error_returns_failure(self):
        with (
            patch.object(coinbase_account_summary, "load_dotenv"),
            patch.object(coinbase_account_summary, "CoinbaseClient", spec=CoinbaseClient) as client,
            patch.dict(os.environ, {"COINBASE_API_KEY": "key", "COINBASE_API_SECRET": "secret"}),
            patch("sys.stderr") as stderr,
        ):
            client.return_value.get_user_portfolios.side_effect = CoinbaseClientError("unavailable")
            self.assertEqual(coinbase_account_summary.main(), 1)
        stderr.write.assert_any_call("Coinbase summary failed: unavailable")

    def test_missing_secret_is_named_without_exposing_key(self):
        with (
            patch.object(coinbase_account_summary, "load_dotenv"),
            patch.object(coinbase_account_summary, "CoinbaseClient", spec=CoinbaseClient) as client,
            patch.dict(os.environ, {"COINBASE_API_KEY": "private-key", "COINBASE_API_SECRET": ""}),
            patch("sys.stderr") as stderr,
        ):
            self.assertEqual(coinbase_account_summary.main(), 1)
        client.assert_not_called()
        message = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn("missing COINBASE_API_SECRET", message)
        self.assertNotIn("private-key", message)


if __name__ == "__main__":
    unittest.main()
