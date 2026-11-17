"""Build a standalone Numerai HTML report from the latest saved snapshot."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from src.commons import load_latest_snapshot
from src.commons.reporting import save_html_report, save_report_images
from src.numerai.reporting import render_numerai_report
from src.observability import configure_logging, trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=PROJECT_ROOT / "src/artifacts/numerai")
    parser.add_argument("--output", type=Path, help="Defaults to <artifacts-dir>/report.html")
    parser.add_argument("--log-dir", type=Path, help="Defaults to the artifacts directory")
    args = parser.parse_args(argv)
    output = args.output or args.artifacts_dir / "report.html"
    try:
        with configure_logging((args.log_dir or args.artifacts_dir) / "log.json"), trace("numerai.dashboard"):
            snapshot = load_latest_snapshot(args.artifacts_dir, source="numerai")
            report = render_numerai_report(snapshot)
            save_report_images(output.parent / "report_images", report.images)
            save_html_report(output, report.html)
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        print(f"Numerai dashboard failed: {exc}", file=sys.stderr)
        return 1
    print(f"Numerai dashboard saved: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
