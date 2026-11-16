"""Build a collective Coinbase + Numerai HTML summary from verified snapshots."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from src.commons import load_latest_snapshot
from src.commons.reporting import save_html_report, save_report_images
from src.commons.workflow_report import render_workflow_report
from src.observability import configure_logging, trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=PROJECT_ROOT / "src/artifacts")
    parser.add_argument("--output", type=Path, help="Defaults to <artifacts-dir>/report.html")
    parser.add_argument("--log-dir", type=Path, help="Defaults to the artifacts directory")
    args = parser.parse_args(argv)
    output = args.output or args.artifacts_dir / "report.html"
    try:
        with configure_logging((args.log_dir or args.artifacts_dir) / "log.json"), trace("workflow.dashboard"):
            snapshots = {}
            for source in ("coinbase", "numerai"):
                directory = args.artifacts_dir / source
                snapshots[source] = load_latest_snapshot(directory, source=source) if (directory / "manifest.json").exists() else None
            report = render_workflow_report(snapshots["coinbase"], snapshots["numerai"])
            save_report_images(output.parent / "report_images", report.images)
            save_html_report(output, report.html)
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        print(f"Workflow dashboard failed: {exc}", file=sys.stderr)
        return 1
    print(f"Workflow dashboard saved: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
