"""Offline integration tests for the Coinbase snapshot entry point."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import coinbase_account_summary
from src.coinbase import CoinbaseAccount, CoinbaseClient, CoinbaseClientError, CoinbaseCrypto, CoinbaseOrders


class CoinbaseAccountSummaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.artifacts = self.directory / "artifacts"
        self.logs = self.artifacts
        self.argv = ["--artifacts-dir", str(self.artifacts)]
        self.output, self.errors = StringIO(), StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(redirect_stderr(self.errors))
        self.enterContext(patch.dict(os.environ, {"COINBASE_API_KEY": "private-key", "COINBASE_API_SECRET": "secret"}))
        self.load = self.enterContext(patch.object(coinbase_account_summary, "load_dotenv"))
        self.factory = self.enterContext(patch.object(coinbase_account_summary, "CoinbaseClient", spec=CoinbaseClient))
        self.client = self.factory.return_value
        wallet = CoinbaseCrypto(
            uuid="wallet-one", name="USDC wallet", currency="USDC", retail_portfolio_id="p1",
            available_balance=Decimal("10.1234567890123456789"), hold=Decimal("2"),
            value_usdc=Decimal("12.1234567890123456789"), previous_week_value_usdc=Decimal("12"),
            weekly_change_usdc=Decimal("0.1234567890123456789"), weekly_change_percent=None,
            active=True, ready=False,
        )
        self.client.get_user_portfolios.return_value = [CoinbaseAccount(
            id="p1", name="Main", total_value_usdc=wallet.value_usdc, wallets=[wallet],
            previous_week_value_usdc=Decimal("12"), weekly_change_usdc=wallet.weekly_change_usdc,
            weekly_change_percent=None,
        )]
        self.client.get_orders.return_value = CoinbaseOrders(in_progress=[], filled_last_week=[])

    def test_main_saves_snapshot_manifest_and_correlated_logs(self):
        self.assertEqual(coinbase_account_summary.main(self.argv), 0)
        manifest_path = self.artifacts / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        entry = manifest["artifacts"][0]
        content = (self.artifacts / entry["path"]).read_bytes()
        snapshot = json.loads(content)
        self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(entry["size_bytes"], len(content))
        self.assertEqual(snapshot["trace_id"], manifest["trace_id"])
        self.assertEqual(snapshot["snapshot_id"], manifest["snapshot_id"])
        self.assertEqual(snapshot["data"]["portfolios"][0]["wallets"][0]["available_balance"], "10.1234567890123456789")
        self.assertEqual(snapshot["data"]["orders"], {"in_progress": [], "filled_last_week": []})
        self.assertEqual(manifest["metadata"]["wallet_count"], 1)
        self.client.get_user_portfolios.assert_called_once()
        self.assertEqual(self.client.get_user_portfolios.call_args, self.client.get_orders.call_args)
        self.client.get_watchlist_movement.assert_not_called()
        self.load.assert_called_once_with(coinbase_account_summary.PROJECT_ROOT / ".env")
        records = [json.loads(line) for line in (self.logs / "log.json").read_text().splitlines()]
        self.assertEqual({record["trace_id"] for record in records}, {manifest["trace_id"]})
        self.assertIn("artifacts.save_snapshot", {record.get("operation") for record in records})
        self.assertIn(str(manifest_path), self.output.getvalue())
        self.assertNotIn("private-key", (self.logs / "log.json").read_text())

    def test_optional_watchlist_is_saved(self):
        self.client.get_watchlist_movement.return_value = []
        self.assertEqual(coinbase_account_summary.main(self.argv + ["--watchlist", "BTC-USDC"]), 0)
        self.client.get_watchlist_movement.assert_called_once_with(
            ["BTC-USDC"], as_of=self.client.get_orders.call_args.kwargs["as_of"]
        )
        manifest = json.loads((self.artifacts / "manifest.json").read_text())
        self.assertEqual(manifest["metadata"]["watchlist_products"], ["BTC-USDC"])

    def test_fetch_failure_preserves_previous_manifest(self):
        self.assertEqual(coinbase_account_summary.main(self.argv), 0)
        original = (self.artifacts / "manifest.json").read_bytes()
        self.client.get_orders.side_effect = CoinbaseClientError("unavailable")
        self.assertEqual(coinbase_account_summary.main(self.argv), 1)
        self.assertEqual((self.artifacts / "manifest.json").read_bytes(), original)
        self.assertEqual(len(list((self.artifacts / "snapshots").glob("*.json"))), 1)
        records = [json.loads(line) for line in (self.logs / "log.json").read_text().splitlines()]
        self.assertTrue(any(record.get("event") == "span.failed" for record in records))

    def test_write_failure_returns_nonzero(self):
        with patch.object(coinbase_account_summary, "save_snapshot", side_effect=OSError("disk full")):
            self.assertEqual(coinbase_account_summary.main(self.argv), 1)
        self.assertIn("disk full", self.errors.getvalue())
        self.assertFalse((self.artifacts / "manifest.json").exists())

    def test_missing_secret_is_named_without_exposing_key(self):
        with patch.dict(os.environ, {"COINBASE_API_SECRET": ""}):
            self.assertEqual(coinbase_account_summary.main(self.argv), 1)
        self.factory.assert_not_called()
        self.assertIn("missing COINBASE_API_SECRET", self.errors.getvalue())
        self.assertNotIn("private-key", self.errors.getvalue())
        self.assertFalse((self.artifacts / "manifest.json").exists())

    def test_direct_execution_resolves_import_from_another_directory(self):
        env = os.environ.copy()
        env.update(COINBASE_API_KEY="", COINBASE_API_SECRET="")
        script = Path(__file__).resolve().parents[2] / "scripts" / "coinbase_account_summary.py"
        result = subprocess.run(
            [sys.executable, "-I", str(script), *self.argv], cwd=self.directory,
            env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing COINBASE_API_KEY, COINBASE_API_SECRET", result.stderr)
        self.assertNotIn("ImportError", result.stderr)


if __name__ == "__main__":
    unittest.main()
