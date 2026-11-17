"""Build independent Coinbase and Numerai reports plus a short navigation page."""

import argparse
import sys
import os
from urllib.parse import quote
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from src.commons import load_latest_snapshot
from src.commons.reporting import save_html_report, save_report_images
from src.commons.workflow_report import render_report_index
from src.coinbase.reporting import render_coinbase_report
from src.commons.reporting import RenderedReport
from src.numerai.reporting import render_numerai_report
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
            reports = {}
            if snapshots["coinbase"] is not None:
                reports["coinbase"] = RenderedReport(render_coinbase_report(snapshots["coinbase"]))
            if snapshots["numerai"] is not None:
                reports["numerai"] = render_numerai_report(snapshots["numerai"])
            links = []
            for source in reports:
                path = args.artifacts_dir / source / "report.html"
                href = quote(Path(os.path.relpath(path, output.parent)).as_posix(), safe="/")
                links.append((source.title(), href, snapshots[source]["as_of"]))
            index = render_report_index(links)
            for source, report in reports.items():
                directory = args.artifacts_dir / source
                save_report_images(directory / "report_images", report.images)
                save_html_report(directory / "report.html", report.html)
            save_html_report(output, index)
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        print(f"Workflow dashboard failed: {exc}", file=sys.stderr)
        return 1
    print(f"Report index saved: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
