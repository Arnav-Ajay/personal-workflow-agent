"""Fetch Coinbase data and publish the latest snapshot and artifact manifest."""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from src.coinbase import CoinbaseClient, CoinbaseClientError
from src.commons import save_snapshot
from src.observability import configure_logging, trace

logger = logging.getLogger("src.scripts.coinbase_account_summary")


def _fetch_and_save(args: argparse.Namespace, trace_id: str) -> Path:
    with trace("coinbase.credentials"):
        load_dotenv(PROJECT_ROOT / ".env")
        credentials = {name: os.getenv(name, "") for name in ("COINBASE_API_KEY", "COINBASE_API_SECRET")}
        missing = [name for name, value in credentials.items() if not value.strip()]
        if missing:
            raise ValueError(f"missing {', '.join(missing)}. Set them in the environment or {PROJECT_ROOT / '.env'}.")
        client = CoinbaseClient(credentials["COINBASE_API_KEY"], credentials["COINBASE_API_SECRET"])

    as_of = datetime.now(timezone.utc)
    portfolios = client.get_user_portfolios(as_of=as_of)
    orders = client.get_orders(as_of=as_of)
    watchlist = client.get_watchlist_movement(args.watchlist, as_of=as_of) if args.watchlist else []
    return save_snapshot(
        args.artifacts_dir, source="coinbase", as_of=as_of, trace_id=trace_id,
        data={"portfolios": portfolios, "orders": orders, "watchlist": watchlist},
        metadata={
            "portfolio_count": len(portfolios),
            "wallet_count": sum(len(portfolio.wallets) for portfolio in portfolios),
            "in_progress_order_count": len(orders.in_progress),
            "filled_last_week_order_count": len(orders.filled_last_week),
            "watchlist_products": args.watchlist,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=PROJECT_ROOT / "src/artifacts/coinbase")
    parser.add_argument("--log-dir", type=Path, help="Defaults to the artifacts directory")
    parser.add_argument("--watchlist", nargs="+", default=[], metavar="PRODUCT_ID")
    args = parser.parse_args(argv)
    try:
        with configure_logging((args.log_dir or args.artifacts_dir) / "log.json"):
            with trace("coinbase.snapshot") as run:
                manifest = _fetch_and_save(args, run.trace_id)
                logger.info("Coinbase snapshot saved; manifest: %s", manifest)
    except (CoinbaseClientError, OSError, TypeError, ValueError) as exc:
        print(f"Coinbase snapshot failed: {exc}", file=sys.stderr)
        return 1
    print(f"Coinbase snapshot saved: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
