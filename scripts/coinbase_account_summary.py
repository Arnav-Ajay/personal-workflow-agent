"""Print the current Coinbase portfolio and order summary."""

import os
from pathlib import Path
from pprint import pprint
import sys

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from src.coinbase import CoinbaseClient, CoinbaseClientError


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.getenv("COINBASE_API_KEY", "")
    secret = os.getenv("COINBASE_API_SECRET", "")
    missing = [
        name for name, value in (
            ("COINBASE_API_KEY", key),
            ("COINBASE_API_SECRET", secret),
        ) if not value
    ]
    if missing:
        print(
            f"Coinbase summary failed: missing {', '.join(missing)}. "
            f"Set the missing variables in the environment or {PROJECT_ROOT / '.env'}.",
            file=sys.stderr,
        )
        return 1
    try:
        client = CoinbaseClient(key, secret)
        portfolios = client.get_user_portfolios()
        orders = client.get_orders()
    except CoinbaseClientError as exc:
        print(f"Coinbase summary failed: {exc}", file=sys.stderr)
        return 1

    print("Portfolios:")
    for portfolio in portfolios:
        pprint(portfolio)
    print("\nOrders:")
    pprint(orders)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
