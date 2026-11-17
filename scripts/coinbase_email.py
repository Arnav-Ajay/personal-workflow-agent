"""Preview a Coinbase report email, or dispatch it through Gmail with --send."""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from src.coinbase.email_report import build_coinbase_email
from src.commons import load_latest_snapshot
from src.commons.artifacts import atomic_write, save_json_artifact
from src.gmail import GmailClient, GmailClientError, recipients
from src.observability import configure_logging, trace

logger = logging.getLogger("src.scripts.coinbase_email")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=PROJECT_ROOT / "src/artifacts/coinbase")
    parser.add_argument("--log-dir", type=Path, help="Defaults to the artifacts directory")
    parser.add_argument("--send", action="store_true", help="Dispatch through Gmail; otherwise only save a local .eml preview")
    args = parser.parse_args(argv)
    try:
        with configure_logging((args.log_dir or args.artifacts_dir) / "log.json"), trace("coinbase.email") as run:
            load_dotenv(PROJECT_ROOT / ".env")
            snapshot = load_latest_snapshot(args.artifacts_dir, source="coinbase")
            targets = recipients(os.getenv("GMAIL_TO", ""))
            message = build_coinbase_email(snapshot, sender=os.getenv("GMAIL_USERNAME", ""), recipients=targets,
                                           report_timezone=os.getenv("REPORT_TIMEZONE") or "UTC")
            directory = args.artifacts_dir / "emails"
            preview = directory / f"{run.trace_id}.eml"
            receipt_path = directory / f"{run.trace_id}.json"
            receipt = {"schema_version": 1, "source": "coinbase", "snapshot_id": snapshot["snapshot_id"],
                       "trace_id": run.trace_id, "message_id": str(message["Message-ID"]),
                       "created_at": datetime.now(timezone.utc), "recipient_count": len(targets), "status": "preview"}
            atomic_write(preview, message.as_bytes())
            save_json_artifact(receipt_path, receipt)
            if args.send:
                enabled = os.getenv("GMAIL_ENABLED", "false").strip().lower() in {"true", "1", "yes", "on"}
                if not enabled:
                    raise ValueError("Set GMAIL_ENABLED=true to use --send")
                try:
                    result = GmailClient(os.getenv("GMAIL_USERNAME", ""), os.getenv("GMAIL_APP_PASSWORD", "")).send(message)
                except (GmailClientError, ValueError) as exc:
                    save_json_artifact(receipt_path, {**receipt, "status": "failed_or_uncertain", "error_type": type(exc).__name__})
                    raise
                receipt.update(status="accepted" if result.refused_count == 0 else "partially_accepted",
                               accepted_count=result.accepted_count, refused_count=result.refused_count)
                try:
                    save_json_artifact(receipt_path, receipt)
                except OSError as exc:
                    raise GmailClientError("Gmail accepted the message but saving its receipt failed; check Sent mail before retrying") from exc
                if result.refused_count:
                    raise GmailClientError("Gmail refused some recipients; check the receipt before retrying")
                logger.info("Coinbase email accepted by Gmail")
    except (GmailClientError, OSError, ValueError, TypeError, KeyError, ArithmeticError, ZoneInfoNotFoundError) as exc:
        print(f"Coinbase email failed: {exc}", file=sys.stderr)
        return 1
    print(f"Coinbase email {'accepted by Gmail' if args.send else 'preview saved'}: {preview.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
