"""Export graph-ready CSV series from a verified saved Numerai snapshot."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from src.commons import load_latest_snapshot
from src.commons.artifacts import save_csv_bundle
from src.numerai.trends import trend_tables
from src.observability import configure_logging, trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=PROJECT_ROOT / "src/artifacts/numerai")
    parser.add_argument("--log-dir", type=Path, help="Defaults to the artifacts directory")
    args = parser.parse_args(argv)
    try:
        with configure_logging((args.log_dir or args.artifacts_dir) / "log.json"), trace("numerai.export_trends") as run:
            snapshot = load_latest_snapshot(args.artifacts_dir, source="numerai")
            tables = trend_tables(snapshot["data"])
            directory = args.artifacts_dir / "trends" / snapshot["snapshot_id"]
            manifest = save_csv_bundle(directory, tables, source="numerai", snapshot_id=snapshot["snapshot_id"],
                                       trace_id=run.trace_id)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Numerai trend export failed: {exc}", file=sys.stderr)
        return 1
    print(f"Numerai trend datasets saved: {manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
