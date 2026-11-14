"""Build a standalone HTML dashboard from the latest saved Coinbase snapshot."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from src.coinbase.reporting import render_coinbase_report
from src.commons import load_latest_snapshot
from src.commons.reporting import save_html_report
from src.observability import configure_logging, trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=PROJECT_ROOT / "src/artifacts/coinbase")
    parser.add_argument("--output", type=Path, help="Defaults to <artifacts-dir>/report.html")
    parser.add_argument("--log-dir", type=Path, help="Defaults to the artifacts directory")
    args = parser.parse_args(argv)
    output = args.output or args.artifacts_dir / "report.html"
    try:
        with configure_logging((args.log_dir or args.artifacts_dir) / "log.json"), trace("coinbase.dashboard"):
            snapshot = load_latest_snapshot(args.artifacts_dir, source="coinbase")
            report = render_coinbase_report(snapshot)
            save_html_report(output, report)
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as exc:
        print(f"Coinbase dashboard failed: {exc}. Check the artifact and output paths; "
              "fetch missing snapshots with scripts/coinbase_account_summary.py.", file=sys.stderr)
        return 1
    print(f"Coinbase dashboard saved: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
