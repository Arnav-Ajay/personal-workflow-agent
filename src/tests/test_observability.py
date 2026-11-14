"""Structured traces, context cleanup and Coinbase instrumentation."""

import json
import logging
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import Mock

from src.coinbase import CoinbaseClient, CoinbaseClientError
from src.observability import configure_logging, trace


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "logs" / "test.jsonl"
        self.enterContext(redirect_stderr(StringIO()))

    def records(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_nested_spans_correlate_and_restore_parent_after_failure(self):
        with configure_logging(self.path):
            with trace("root") as root:
                with self.assertRaises(ValueError), trace("child"):
                    raise ValueError("private-secret-do-not-log")
                logging.getLogger("src.tests").info("Parent restored")
            logging.getLogger("src.tests").info("Outside trace")
        records = self.records()
        failed = next(record for record in records if record.get("event") == "span.failed")
        self.assertEqual(failed["trace_id"], root.trace_id)
        self.assertEqual(failed["parent_span_id"], root.span_id)
        self.assertEqual(failed["error_type"], "ValueError")
        self.assertGreaterEqual(failed["duration_ms"], 0)
        restored = next(record for record in records if record["message"] == "Parent restored")
        self.assertEqual(restored["span_id"], root.span_id)
        self.assertIsNone(records[-1]["trace_id"])
        self.assertNotIn("private-secret-do-not-log", self.path.read_text())

    def test_repeated_configuration_does_not_duplicate_or_leak_handlers(self):
        package_logger = logging.getLogger("src")
        previous_handlers = list(package_logger.handlers)
        previous_level = package_logger.level
        for _ in range(2):
            with configure_logging(self.path):
                package_logger.info("Once per run")
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(package_logger.handlers, previous_handlers)
        self.assertEqual(package_logger.level, previous_level)

    def test_coinbase_component_and_api_requests_share_trace(self):
        client = CoinbaseClient.__new__(CoinbaseClient)
        client.client = Mock()
        client.client.get_portfolios.return_value.to_dict.return_value = {"portfolios": []}
        client.client.get_accounts.return_value.to_dict.return_value = {"accounts": [], "has_next": False}
        with configure_logging(self.path), trace("run") as run:
            self.assertEqual(client.get_user_portfolios(), [])
            client.client.get_portfolios.side_effect = CoinbaseClientError("secret payload")
            with self.assertRaises(CoinbaseClientError):
                client.get_user_portfolios()
        records = self.records()
        self.assertEqual({record["trace_id"] for record in records}, {run.trace_id})
        operations = {record.get("operation") for record in records}
        self.assertTrue({"run", "coinbase.portfolios", "coinbase.api_request"} <= operations)
        self.assertTrue(any(record["component"] == "src.coinbase.coinbase_api" for record in records))
        self.assertNotIn("secret payload", self.path.read_text())

    def test_file_logging_does_not_duplicate_events_through_root_handlers(self):
        root = logging.getLogger()
        output = StringIO()
        handler = logging.StreamHandler(output)
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        package = logging.getLogger("src")
        previous_propagate = package.propagate
        with configure_logging(self.path):
            package.info("One destination per handler")
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(package.propagate, previous_propagate)

    def test_failed_start_logging_does_not_leak_trace_context(self):
        class FailingHandler(logging.Handler):
            def emit(self, record):
                raise RuntimeError("logging unavailable")

        failing = FailingHandler()
        trace_logger = logging.getLogger("src.observability.runtime")
        with configure_logging(self.path):
            trace_logger.addHandler(failing)
            try:
                with self.assertRaisesRegex(RuntimeError, "logging unavailable"), trace("broken"):
                    self.fail("Span should fail before executing the block")
            finally:
                trace_logger.removeHandler(failing)
            with trace("next") as span:
                self.assertIsNone(span.parent_span_id)


if __name__ == "__main__":
    unittest.main()
