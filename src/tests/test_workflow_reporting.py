"""Collective HTML/email reporting and Numerai chart integration."""

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import timedelta
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import workflow_dashboard
from src.commons import load_latest_snapshot, save_snapshot
from src.commons.charts import ChartSeries, line_chart
from src.commons.reporting import embed_report_images
from src.commons.workflow_report import render_report_index
from src.coinbase.reporting import render_coinbase_report
from src.numerai import NumeraiClient
from src.numerai.reporting import build_numerai_charts, render_numerai_body, render_numerai_report
import test_coinbase_reporting as coinbase_fixtures
import test_numerai as numerai_fixtures


class HtmlElements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class WorkflowReportingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.now = numerai_fixtures.NOW
        save_snapshot(self.directory / "coinbase", source="coinbase", data=coinbase_fixtures.report_data(),
                      as_of=self.now - timedelta(days=2), trace_id="coinbase-trace")
        rounds = [numerai_fixtures.round_row(n, n <= 10) for n in range(20, 0, -1)]
        with patch("src.numerai.client.requests.Session"):
            with closing(NumeraiClient("test-id", "test-secret")) as client:
                with patch.object(client, "_query", side_effect=numerai_fixtures.query_responder(
                        numerai_fixtures.account_data(), numerai_fixtures.overview_data(), rounds)):
                    data = client.get_performance(as_of=self.now)
        save_snapshot(self.directory / "numerai", source="numerai", data=data,
                      as_of=self.now - timedelta(days=1), trace_id="numerai-trace")
        self.coinbase = load_latest_snapshot(self.directory / "coinbase", source="coinbase")
        self.numerai = load_latest_snapshot(self.directory / "numerai", source="numerai")

    def test_reports_keep_sources_separate(self):
        coinbase = render_coinbase_report(self.coinbase)
        report = render_numerai_report(self.numerai)
        for title in ("Overall account performance", "Model performance", "Last 7 resolved rounds", "Next 7 resolving rounds"):
            self.assertIn(title, report.html)
            self.assertNotIn(title, coinbase)
        for title in ("Asset breakdown", "Your watchlist"):
            self.assertIn(title, coinbase)
            self.assertNotIn(title, report.html)
        self.assertNotIn("Coinbase", report.html)
        self.assertNotIn("Numerai", coinbase)
        self.assertIn("13 Nov 2026, 12:00 UTC", coinbase)
        self.assertIn("14 Nov 2026, 12:00 UTC", report.html)
        self.assertIn(self.coinbase["snapshot_id"], coinbase)
        self.assertIn(self.numerai["snapshot_id"], report.html)
        self.assertIn("provisional observations, not predictions", report.html)
        self.assertIn("data:image/png;base64,", report.html)
        self.assertNotIn('src="cid:', report.html)
        self.assertEqual(len(report.images), 4)
        self.assertTrue(all(image.startswith(b"\x89PNG\r\n\x1a\n") for image in report.images.values()))

    def test_email_mode_retains_cids_and_has_no_active_or_remote_content(self):
        snapshot = deepcopy(self.numerai)
        snapshot["data"]["model"]["name"] = '<script>alert("private")</script>'
        report = render_numerai_report(snapshot, email=True)
        self.assertIn("&lt;script&gt;", report.html)
        self.assertNotIn("data:image/png;base64,", report.html)
        self.assertNotIn('src="cid:', embed_report_images(report))
        elements = HtmlElements(report.html).elements
        self.assertFalse(any(tag in {"script", "iframe", "form", "link"} for tag, _ in elements))
        for tag, attrs in elements:
            self.assertFalse(any(key.startswith("on") for key in attrs))
            if tag == "img":
                self.assertIn(attrs["src"].removeprefix("cid:"), report.images)
                self.assertTrue(attrs.get("alt"))

    def test_empty_histories_have_clear_states_and_do_not_invent_charts(self):
        snapshot = deepcopy(self.numerai)
        data = snapshot["data"]
        for owner in ("account", "model"):
            for key in data[owner]["history"]:
                data[owner]["history"][key] = []
        data["rounds"].update(last_resolved=[], next_resolving=[])
        images = build_numerai_charts(data)
        self.assertEqual(images, {})
        body = render_numerai_body(snapshot, images=images)
        self.assertIn("0 available", body)
        self.assertIn("No resolving rounds", body)
        self.assertIn("No resolved rounds", body)
        self.assertIn("No observations available", body)

    def test_round_charts_keep_versions_separate_and_provisional_flags(self):
        data = deepcopy(self.numerai["data"])
        for row in data["rounds"]["next_resolving"]:
            row["payout_multipliers"].append({"displayName": "CORR", "version": "2"})
        with patch("src.numerai.reporting.line_chart", return_value=None) as plot:
            build_numerai_charts(data)
        resolved, resolving = plot.call_args_list[-2].args[0], plot.call_args_list[-1].args[0]
        self.assertTrue(all(not series.provisional for series in resolved))
        self.assertTrue(all(series.provisional for series in resolving))
        self.assertEqual({series.label for series in resolving}, {"CORR / v1", "CORR / v2"})

    def test_index_only_links_independent_local_reports(self):
        index = render_report_index([
            ("Coinbase", "coinbase/report.html", self.coinbase["as_of"]),
            ("Numerai", "numerai/report.html", self.numerai["as_of"]),
        ])
        self.assertIn('href="coinbase/report.html"', index)
        self.assertIn('href="numerai/report.html"', index)
        self.assertNotIn("Asset breakdown", index)
        self.assertNotIn("Overall account performance", index)
        with self.assertRaisesRegex(ValueError, "fetch scripts"):
            render_report_index([])
        with self.assertRaises(ValueError):
            render_report_index([("Unsafe", "javascript:alert(1)", "today")])

    def test_cli_keeps_previous_report_when_source_checksum_fails(self):
        argv = ["--artifacts-dir", str(self.directory)]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(workflow_dashboard.main(argv), 0)
            output = self.directory / "report.html"
            charts = list((self.directory / "numerai/report_images").glob("*.png"))
            self.assertEqual(len(charts), 4)
            self.assertTrue(all(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in charts))
            self.assertTrue((self.directory / "coinbase/report.html").is_file())
            self.assertTrue((self.directory / "numerai/report.html").is_file())
            before = output.read_bytes()
            manifest = json.loads((self.directory / "numerai/manifest.json").read_text())
            source = self.directory / "numerai" / manifest["artifacts"][0]["path"]
            source.write_bytes(source.read_bytes() + b" ")
            self.assertEqual(workflow_dashboard.main(argv), 1)
        self.assertEqual(output.read_bytes(), before)
        records = [json.loads(line) for line in (self.directory / "log.json").read_text().splitlines()]
        self.assertTrue(any(record.get("event") == "span.failed" for record in records))

    def test_direct_entry_point_runs_without_credentials_from_another_directory(self):
        script = Path(__file__).resolve().parents[2] / "scripts/workflow_dashboard.py"
        result = subprocess.run([sys.executable, "-I", str(script), "--artifacts-dir", str(self.directory)],
                                cwd=self.directory, capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.directory / "report.html").is_file())

    def test_chart_without_values_is_not_rendered_as_zero(self):
        self.assertIsNone(line_chart([ChartSeries("Missing", [(self.now, None)])], ylabel="Score"))

    def test_numerai_entry_point_needs_only_its_own_source(self):
        script = Path(__file__).resolve().parents[2] / "scripts/numerai_dashboard.py"
        result = subprocess.run([sys.executable, "-I", str(script), "--artifacts-dir", str(self.directory / "numerai")],
                                cwd=self.directory, capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        html = (self.directory / "numerai/report.html").read_text(encoding="utf-8")
        self.assertIn("Overall account performance", html)
        self.assertNotIn("Coinbase", html)


if __name__ == "__main__":
    unittest.main()
