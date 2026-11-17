"""Coinbase-only MIME previews and Gmail SMTP behavior; no live delivery."""

import json
import os
import smtplib
import ssl
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import coinbase_email
from src.coinbase.email_report import build_coinbase_email
from src.commons import load_latest_snapshot, save_snapshot
from src.gmail import GmailClient, GmailClientError, mailbox, recipients
import test_coinbase_reporting as fixtures

SENDER = "sender@example.invalid"
TARGET = "recipient@example.invalid"


class CoinbaseEmailTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.argv = ["--artifacts-dir", str(self.directory)]
        self.output, self.errors = StringIO(), StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(redirect_stderr(self.errors))
        self.enterContext(patch.object(coinbase_email, "load_dotenv"))
        self.enterContext(patch.dict(os.environ, {"GMAIL_USERNAME": SENDER, "GMAIL_TO": TARGET,
                                                "GMAIL_APP_PASSWORD": "test-app-password", "GMAIL_ENABLED": "true",
                                                "REPORT_TIMEZONE": "UTC"}))
        self.smtp_factory = self.enterContext(patch("src.gmail.client.smtplib.SMTP_SSL"))
        self.smtp = self.smtp_factory.return_value.__enter__.return_value
        self.smtp.send_message.return_value = {}
        save_snapshot(self.directory, source="coinbase", data=fixtures.report_data(),
                      as_of=datetime(2026, 11, 16, 21, tzinfo=timezone.utc), trace_id="source-trace")
        self.snapshot = load_latest_snapshot(self.directory, source="coinbase")

    def message(self):
        return build_coinbase_email(self.snapshot, sender=SENDER, recipients=(TARGET,))

    def receipts(self):
        return [json.loads(path.read_text()) for path in (self.directory / "emails").glob("*.json")]

    def test_default_only_previews_even_if_gmail_is_enabled(self):
        self.assertEqual(coinbase_email.main(self.argv), 0)
        self.smtp_factory.assert_not_called()
        path = next((self.directory / "emails").glob("*.eml"))
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        self.assertEqual(str(message["From"]), SENDER)
        self.assertEqual(str(message["To"]), TARGET)
        self.assertEqual(message["X-Workflow-Source"], "coinbase")
        html = message.get_body(preferencelist=("html",)).get_content()
        plain = message.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("Asset breakdown", html)
        self.assertIn("Current price", html)
        self.assertIn("Total crypto value", plain)
        self.assertNotIn("Numerai", html)
        self.assertNotIn("Numerai", plain)
        self.assertNotIn("<script>", html)
        self.assertNotIn("test-app-password", path.read_text())
        self.assertEqual(self.receipts()[0]["status"], "preview")

    def test_send_uses_verified_tls_and_records_smtp_acceptance(self):
        self.assertEqual(coinbase_email.main(self.argv + ["--send"]), 0)
        self.smtp_factory.assert_called_once()
        args, kwargs = self.smtp_factory.call_args
        self.assertEqual(args, ("smtp.gmail.com", 465))
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(kwargs["context"].check_hostname)
        self.smtp.login.assert_called_once_with(SENDER, "test-app-password")
        self.smtp.send_message.assert_called_once()
        self.assertEqual(self.smtp.send_message.call_args.kwargs["to_addrs"], [TARGET])
        self.assertEqual(self.receipts()[0]["status"], "accepted")
        log = (self.directory / "log.json").read_text()
        self.assertNotIn(SENDER, log)
        self.assertNotIn(TARGET, log)
        self.assertNotIn("test-app-password", log)

    def test_send_requires_enabled_config(self):
        with patch.dict(os.environ, {"GMAIL_ENABLED": "false"}):
            self.assertEqual(coinbase_email.main(self.argv + ["--send"]), 1)
        self.smtp_factory.assert_not_called()
        self.assertIn("GMAIL_ENABLED=true", self.errors.getvalue())

    def test_preview_does_not_require_a_password(self):
        with patch.dict(os.environ, {"GMAIL_APP_PASSWORD": ""}):
            self.assertEqual(coinbase_email.main(self.argv), 0)
        self.smtp_factory.assert_not_called()

    def test_receipt_failure_after_acceptance_warns_against_blind_retry(self):
        save = coinbase_email.save_json_artifact
        def save_until_accepted(path, receipt):
            if receipt["status"] == "accepted":
                raise OSError("disk unavailable")
            save(path, receipt)
        with patch.object(coinbase_email, "save_json_artifact", side_effect=save_until_accepted):
            self.assertEqual(coinbase_email.main(self.argv + ["--send"]), 1)
        self.smtp.send_message.assert_called_once()
        self.assertIn("Gmail accepted the message", self.errors.getvalue())
        self.assertIn("check Sent mail", self.errors.getvalue())

    def test_auth_error_is_sanitized_and_does_not_claim_success(self):
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private-address-or-secret")
        self.assertEqual(coinbase_email.main(self.argv + ["--send"]), 1)
        self.smtp.send_message.assert_not_called()
        self.assertIn("authentication failed", self.errors.getvalue())
        self.assertNotIn("private-address-or-secret", self.errors.getvalue())
        self.assertEqual(self.receipts()[0]["status"], "failed_or_uncertain")

    def test_partial_refusal_is_a_nonzero_result_with_a_receipt(self):
        with patch.dict(os.environ, {"GMAIL_TO": f"{TARGET},second@example.invalid"}):
            self.smtp.send_message.return_value = {"second@example.invalid": (550, b"refused")}
            self.assertEqual(coinbase_email.main(self.argv + ["--send"]), 1)
        receipt = self.receipts()[0]
        self.assertEqual(receipt["status"], "partially_accepted")
        self.assertEqual((receipt["accepted_count"], receipt["refused_count"]), (1, 1))
        self.smtp.send_message.assert_called_once()

    def test_network_failure_is_not_retried(self):
        self.smtp.send_message.side_effect = OSError("private transport detail")
        with self.assertRaisesRegex(GmailClientError, "Check Sent mail") as caught:
            GmailClient(SENDER, "password").send(self.message())
        self.smtp.send_message.assert_called_once()
        self.assertNotIn("private transport detail", str(caught.exception))

    def test_addresses_reject_header_injection_and_hidden_recipients(self):
        for value in ("", "missing-at", f"{SENDER}\r\nBcc: other@example.invalid", f"Name <{SENDER}>"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mailbox(value)
        self.assertEqual(recipients(f"{TARGET},{TARGET}"), (TARGET,))
        message = self.message()
        message["Bcc"] = "hidden@example.invalid"
        with self.assertRaises(ValueError):
            GmailClient(SENDER, "password").send(message)
        self.smtp_factory.assert_not_called()

    def test_subject_uses_snapshot_date_in_report_timezone(self):
        message = build_coinbase_email(self.snapshot, sender=SENDER, recipients=(TARGET,), report_timezone="Asia/Kolkata")
        self.assertIn("2026-11-17 02:30 IST", str(message["Subject"]))

    def test_invalid_source_does_not_connect_to_smtp(self):
        manifest = self.directory / "manifest.json"
        manifest.write_text("{}")
        self.assertEqual(coinbase_email.main(self.argv + ["--send"]), 1)
        self.smtp_factory.assert_not_called()
