"""Numerai entry points, artifact safety and graph-ready export integration."""

import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from src.commons import load_latest_snapshot
from src.numerai import NumeraiClient, NumeraiClientError
from scripts import numerai_account_summary as account_summary
from scripts import numerai_export_trends as export_trends
import test_numerai as fixtures


class NumeraiScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.argv = ["--artifacts-dir", str(self.directory)]
        self.output, self.errors = StringIO(), StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(redirect_stderr(self.errors))
        self.enterContext(patch.dict(os.environ, {"NUMERAI_PUBLIC_ID": "fake-public", "NUMERAI_SECRET": "fake-secret",
                                                "NUMERAI_SECRET_KEY": "", "NUMERAI_USERNAME": ""}))
        self.enterContext(patch.object(account_summary, "load_dotenv"))
        self.client = self.enterContext(patch.object(account_summary, "NumeraiClient", spec=NumeraiClient)).return_value
        # Exercise the real normalization path with an offline transport fixture.
        rounds = [fixtures.round_row(number, number <= 10) for number in range(20, 0, -1)]
        with patch("src.numerai.client.requests.Session"):
            with closing(NumeraiClient("test-id", "test-secret")) as real_client:
                with patch.object(real_client, "_query", side_effect=fixtures.query_responder(
                        fixtures.account_data(), fixtures.overview_data(), rounds)):
                    self.data = real_client.get_performance(as_of=fixtures.NOW)
        self.client.get_performance.return_value = self.data

    def test_fetch_saves_precision_and_all_requested_sections(self):
        self.assertEqual(account_summary.main(self.argv), 0)
        snapshot = load_latest_snapshot(self.directory, source="numerai")
        self.assertEqual(set(snapshot["data"]), {"tournament", "account", "model", "rounds", "metric_notes"})
        self.assertEqual(len(snapshot["data"]["rounds"]["last_resolved"]), 7)
        self.assertEqual(len(snapshot["data"]["rounds"]["next_resolving"]), 7)
        self.assertEqual(snapshot["data"]["account"]["balances"]["availableNmr"], "0.1234567890123456789")
        self.client.close.assert_called_once_with()
        log = (self.directory / "log.json").read_text(encoding="utf-8")
        self.assertNotIn("fake-secret", log)
        self.assertTrue(all(json.loads(line)["trace_id"] == snapshot["trace_id"] for line in log.splitlines()))

    def test_failed_fetch_preserves_last_good_manifest(self):
        self.assertEqual(account_summary.main(self.argv), 0)
        before = (self.directory / "manifest.json").read_bytes()
        self.client.get_performance.side_effect = NumeraiClientError("unavailable")
        self.assertEqual(account_summary.main(self.argv), 1)
        self.assertEqual((self.directory / "manifest.json").read_bytes(), before)
        self.assertEqual(self.client.close.call_count, 2)

    def test_csv_export_is_source_linked_and_checksums_match(self):
        self.assertEqual(account_summary.main(self.argv), 0)
        snapshot = load_latest_snapshot(self.directory, source="numerai")
        self.assertEqual(export_trends.main(self.argv), 0)
        folder = self.directory / "trends" / snapshot["snapshot_id"]
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(manifest["source_snapshot_id"], snapshot["snapshot_id"])
        self.assertEqual(len(manifest["artifacts"]), 9)
        for entry in manifest["artifacts"]:
            content = (folder / entry["path"]).read_bytes()
            self.assertEqual(entry["size_bytes"], len(content))
            self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())
        with (folder / "round_scores.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual({row["score_version"] for row in rows}, {"1", "2"})
        self.assertIn("score_resolved", rows[0])
        self.assertIn("resolved", rows[0])

    def test_missing_secret_is_named_without_exposing_public_id(self):
        with patch.dict(os.environ, {"NUMERAI_SECRET": ""}):
            self.assertEqual(account_summary.main(self.argv), 1)
        self.assertIn("NUMERAI_SECRET", self.errors.getvalue())
        self.assertNotIn("fake-public", self.errors.getvalue())
        self.client.get_performance.assert_not_called()

    def test_direct_execution_from_another_directory(self):
        env = os.environ.copy()
        env.update(NUMERAI_PUBLIC_ID="", NUMERAI_SECRET="", NUMERAI_SECRET_KEY="")
        script = Path(__file__).resolve().parents[2] / "scripts/numerai_account_summary.py"
        result = subprocess.run([sys.executable, "-I", str(script), *self.argv], cwd=self.directory,
                                env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("NUMERAI_PUBLIC_ID", result.stderr)
        self.assertNotIn("ImportError", result.stderr)

    def test_export_entry_point_runs_from_another_directory(self):
        self.assertEqual(account_summary.main(self.argv), 0)
        script = Path(__file__).resolve().parents[2] / "scripts/numerai_export_trends.py"
        result = subprocess.run([sys.executable, "-I", str(script), *self.argv], cwd=self.directory,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Numerai trend datasets saved", result.stdout)
