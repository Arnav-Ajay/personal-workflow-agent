"""Fetch Numerai account/model performance and publish a verified snapshot."""

import argparse
import logging
import os
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from src.commons import save_snapshot
from src.numerai import NumeraiClient, NumeraiClientError
from src.observability import configure_logging, trace

logger = logging.getLogger("src.scripts.numerai_account_summary")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="kaijo_model_01")
    parser.add_argument("--round-count", type=int, default=7)
    parser.add_argument("--artifacts-dir", type=Path, default=PROJECT_ROOT / "src/artifacts/numerai")
    parser.add_argument("--log-dir", type=Path, help="Defaults to the artifacts directory")
    args = parser.parse_args(argv)
    try:
        with configure_logging((args.log_dir or args.artifacts_dir) / "log.json"), trace("numerai.snapshot") as run:
            load_dotenv(PROJECT_ROOT / ".env")
            public_id = os.getenv("NUMERAI_PUBLIC_ID", "")
            secret = os.getenv("NUMERAI_SECRET") or os.getenv("NUMERAI_SECRET_KEY", "")
            missing = [name for name, value in (("NUMERAI_PUBLIC_ID", public_id), ("NUMERAI_SECRET or NUMERAI_SECRET_KEY", secret)) if not value.strip()]
            if missing:
                raise ValueError(f"Missing {', '.join(missing)} in the environment or project .env")
            as_of = datetime.now(timezone.utc)
            with closing(NumeraiClient(public_id, secret)) as client:
                data = client.get_performance(args.model, round_count=args.round_count, as_of=as_of,
                                              expected_username=os.getenv("NUMERAI_USERNAME") or None)
            rounds = data["rounds"]
            manifest = save_snapshot(args.artifacts_dir, source="numerai", data=data, as_of=as_of,
                                     trace_id=run.trace_id, metadata={
                                         "model_name": data["model"]["name"], "tournament": data["tournament"]["id"],
                                         "submitted_round_count": rounds["submitted_round_count"],
                                         "last_resolved_count": len(rounds["last_resolved"]),
                                         "next_resolving_count": len(rounds["next_resolving"]),
                                         "requested_round_count": args.round_count,
                                     })
            logger.info("Published Numerai performance snapshot")
    except (NumeraiClientError, OSError, ValueError, TypeError) as exc:
        print(f"Numerai snapshot failed: {exc}", file=sys.stderr)
        return 1
    print(f"Numerai snapshot saved: {manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
