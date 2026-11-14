"""HTML report behavior, precision, and safety using offline snapshots."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path

from scripts import coinbase_dashboard
from src.coinbase.reporting import render_coinbase_body, render_coinbase_report
from src.commons import load_latest_snapshot, save_snapshot


def report_data():
    return {
        "portfolios": [{
            "id": "p1", "name": "Main <portfolio>", "total_value_usdc": "110",
            "previous_week_value_usdc": "100", "weekly_change_usdc": "10", "weekly_change_percent": "10",
            "wallets": [{
                "currency": "USDC", "name": '<script>alert("wallet")</script>',
                "available_balance": "9007199254740993.123456789", "hold": "2",
                "value_usdc": "110", "weekly_change_usdc": "0.00003",
                "weekly_change_percent": None, "active": True, "ready": False,
                "current_price_usdc": "1.00000001",
            }],
        }],
        "orders": {
            "in_progress": [{
                "order_id": 'order-"<unsafe>', "product_id": "BTC-EUR", "side": "BUY", "status": "OPEN",
                "created_time": "2026-11-13T00:00:00+00:00", "last_fill_time": None,
                "filled_size": None, "filled_value": None, "average_filled_price": None,
                "total_fees": "0", "order_type": "LIMIT",
                "order_configuration": {"limit_limit_gtc": {"limit_price": "100", "base_size": "2"}},
            }],
            "filled_last_week": [],
        },
        "watchlist": [{"product_id": "BTC-EUR", "current_price": "89012.12345678", "daily_percent": "1", "weekly_percent": "-2", "monthly_percent": "0"}],
    }


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class CoinbaseReportingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.artifacts = self.directory / "artifacts"
        self.as_of = datetime(2026, 11, 13, tzinfo=timezone.utc)
        self.manifest_path = save_snapshot(
            self.artifacts, source="coinbase", data=report_data(), as_of=self.as_of, trace_id="test-trace"
        )
        self.snapshot = load_latest_snapshot(self.artifacts, source="coinbase")

    def test_report_covers_all_sections_and_preserves_decimal_precision(self):
        report = render_coinbase_report(self.snapshot)
        for title in ("Your portfolios", "Asset breakdown", "In-progress orders", "Filled in the last seven days", "Your watchlist"):
            self.assertIn(title, report)
        self.assertIn("9,007,199,254,740,995.12345679", report)
        self.assertIn("9007199254740995.123456789", report)
        self.assertIn("Not available", report)
        self.assertIn("Not ready", report)
        self.assertIn("13 Nov 2026, 00:00 UTC", report)
        self.assertIn("test-trace", report)
        self.assertIn("Avg. price (quote)", report)
        self.assertIn("BTC-EUR", report)
        self.assertIn("Current price (USDC)", report)
        self.assertIn("Current price (quote)", report)
        self.assertIn("89,012.12345678", report)
        self.assertIn("1.00000001", report)
        self.assertIn("limit price: 100", report)
        self.assertNotIn("-0.00 USDC", report)
        self.assertEqual(self.snapshot["data"], report_data())

    def test_old_snapshots_without_prices_still_render(self):
        snapshot = deepcopy(self.snapshot)
        del snapshot["data"]["portfolios"][0]["wallets"][0]["current_price_usdc"]
        del snapshot["data"]["watchlist"][0]["current_price"]
        self.assertIn("&mdash;", render_coinbase_report(snapshot))

    def test_artifact_text_is_escaped_and_report_has_no_active_content(self):
        report = render_coinbase_report(self.snapshot)
        tags = Elements(report).tags
        self.assertFalse(any(tag in {"script", "iframe", "img", "link", "form"} for tag, _ in tags))
        self.assertFalse(any(key.startswith("on") for _, attrs in tags for key in attrs))
        self.assertIn("&lt;script&gt;", report)
        self.assertIn("Main &lt;portfolio&gt;", report)
        self.assertIn("Content-Security-Policy", report)
        self.assertNotIn("<script>", report)

    def test_core_sections_can_be_reused_without_document_shell(self):
        body = render_coinbase_body(self.snapshot)
        self.assertNotIn("<html", body)
        self.assertNotIn("<style", body)
        self.assertIn('style="', body)
        self.assertIn("Your portfolios", body)

    def test_order_sort_uses_instants_instead_of_timestamp_text(self):
        snapshot = deepcopy(self.snapshot)
        first, second = deepcopy(snapshot["data"]["orders"]["in_progress"][0]), deepcopy(snapshot["data"]["orders"]["in_progress"][0])
        first.update(order_id="earlier-order", created_time="2026-11-13T01:00:00+05:30")
        second.update(order_id="later-order", created_time="2026-11-12T23:00:00Z")
        snapshot["data"]["orders"]["in_progress"] = [first, second]
        report = render_coinbase_report(snapshot)
        self.assertLess(report.index("later-order"), report.index("earlier-order"))

    def test_invalid_nested_data_has_clear_validation_errors(self):
        for data in (None, [], "bad", {"orders": None}, {"orders": []}):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, "objects"):
                render_coinbase_body({**self.snapshot, "data": data})
        for flag in ("false", 0, None):
            snapshot = deepcopy(self.snapshot)
            snapshot["data"]["portfolios"][0]["wallets"][0]["active"] = flag
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "booleans"):
                render_coinbase_body(snapshot)
        for configuration in ([], False, ""):
            snapshot = deepcopy(self.snapshot)
            snapshot["data"]["orders"]["in_progress"][0]["order_configuration"] = configuration
            with self.subTest(configuration=configuration), self.assertRaisesRegex(ValueError, "configuration"):
                render_coinbase_body(snapshot)

    def test_tiny_positive_movement_keeps_explicit_plus_sign(self):
        snapshot = deepcopy(self.snapshot)
        snapshot["data"]["portfolios"][0]["wallets"][0]["weekly_change_usdc"] = "0.0000000001"
        self.assertIn("+1.0000E-10", render_coinbase_body(snapshot))

    def test_empty_snapshot_and_undefined_change_have_clear_states(self):
        snapshot = deepcopy(self.snapshot)
        snapshot["data"] = {"portfolios": [], "orders": {"in_progress": [], "filled_last_week": []}}
        report = render_coinbase_report(snapshot)
        self.assertIn("No portfolios", report)
        self.assertIn("No wallets", report)
        self.assertIn("No in-progress orders", report)
        self.assertIn("No fully filled orders", report)
        self.assertIn("No watchlist products", report)
        self.assertIn("Not available", report)
        self.assertNotIn("NaN", report)

    def test_reader_rejects_corruption_and_path_escape(self):
        manifest = json.loads(self.manifest_path.read_text())
        path = self.artifacts / manifest["artifacts"][0]["path"]
        content = path.read_bytes()
        path.write_bytes(content + b" ")
        with self.assertRaisesRegex(ValueError, "checksum"):
            load_latest_snapshot(self.artifacts)
        manifest["artifacts"][0]["path"] = "../outside.json"
        self.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "inside"):
            load_latest_snapshot(self.artifacts)

    def test_reader_rejects_mismatched_snapshot_identity(self):
        manifest = json.loads(self.manifest_path.read_text())
        path = self.artifacts / manifest["artifacts"][0]["path"]
        content = path.read_bytes().replace(b'"test-trace"', b'"other-trace"')
        path.write_bytes(content)
        manifest["artifacts"][0].update(size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        self.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "trace_id"):
            load_latest_snapshot(self.artifacts)

    def test_cli_writes_html_and_preserves_it_on_corrupt_input(self):
        output, errors = StringIO(), StringIO()
        argv = ["--artifacts-dir", str(self.artifacts), "--log-dir", str(self.directory / "logs")]
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(coinbase_dashboard.main(argv), 0)
            report_path = self.artifacts / "report.html"
            before = report_path.read_bytes()
            self.manifest_path.write_text("{}")
            self.assertEqual(coinbase_dashboard.main(argv), 1)
        self.assertTrue((self.directory / "logs" / "log.json").is_file())
        self.assertEqual(report_path.read_bytes(), before)
        self.assertIn("Unsupported artifact manifest", errors.getvalue())
        self.assertIn(str(report_path), output.getvalue())

    def test_direct_execution_and_missing_artifact_message(self):
        script = Path(__file__).resolve().parents[2] / "scripts/coinbase_dashboard.py"
        argv = ["--artifacts-dir", str(self.artifacts), "--log-dir", str(self.directory / "logs")]
        result = subprocess.run([sys.executable, "-I", str(script), *argv], cwd=self.directory,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.manifest_path.unlink()
        result = subprocess.run([sys.executable, "-I", str(script), *argv], cwd=self.directory,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("coinbase_account_summary.py", result.stderr)


if __name__ == "__main__":
    unittest.main()
