"""JSON snapshots published through an atomically replaced latest manifest."""

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.observability import trace

logger = logging.getLogger(__name__)


@trace("artifacts.load_latest_snapshot")
def load_latest_snapshot(directory: Path, *, source: str | None = None) -> dict[str, Any]:
    """Read the manifest once and verify its referenced snapshot before use."""
    directory = directory.resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Unsupported artifact manifest")
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError("Manifest must reference exactly one snapshot")
    entry = entries[0]
    if not isinstance(entry.get("path"), str) or not entry["path"]:
        raise ValueError("Manifest has an invalid snapshot path")
    path = (directory / entry["path"]).resolve()
    if not path.is_relative_to(directory):
        raise ValueError("Snapshot path must stay inside the artifact directory")
    content = path.read_bytes()
    if entry.get("size_bytes") != len(content) or entry.get("sha256") != hashlib.sha256(content).hexdigest():
        raise ValueError("Snapshot size or checksum does not match its manifest")
    snapshot = json.loads(content)
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ValueError("Unsupported snapshot schema")
    for field in ("source", "snapshot_id", "trace_id", "as_of", "created_at"):
        if field not in manifest or field not in snapshot or snapshot[field] != manifest[field]:
            raise ValueError(f"Snapshot {field} does not match its manifest")
    if source is not None and snapshot["source"] != source:
        raise ValueError(f"Expected a {source} snapshot")
    logger.info("Loaded verified %s snapshot %s", snapshot["source"], snapshot["snapshot_id"])
    return snapshot


def _json_default(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Cannot serialize a non-finite Decimal")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Artifact timestamps must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _encode(value: Any) -> bytes:
    return (json.dumps(value, default=_json_default, indent=2, ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    """Write beside the destination so replacement stays on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@trace("artifacts.save_snapshot")
def save_snapshot(
    directory: Path, *, source: str, data: dict[str, Any], as_of: datetime,
    trace_id: str | None = None, metadata: dict[str, Any] | None = None,
) -> Path:
    """Save a complete snapshot, then publish its manifest; return the manifest path.

    Readers follow manifest.json to the referenced snapshot. Previous snapshots
    remain available, and a failed write never publishes a partial snapshot.
    Decimal values are strings to preserve precision. All timestamps use UTC.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", source):
        raise ValueError("source must be a simple component name")
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    created_at = datetime.now(timezone.utc)
    snapshot_id = uuid4().hex
    relative_path = Path("snapshots") / f"{snapshot_id}.json"
    snapshot = _encode({
        "schema_version": 1, "source": source, "snapshot_id": snapshot_id,
        "trace_id": trace_id, "as_of": as_of, "created_at": created_at, "data": data,
    })
    manifest = _encode({
        "schema_version": 1, "source": source, "snapshot_id": snapshot_id,
        "trace_id": trace_id, "as_of": as_of, "created_at": created_at,
        "artifacts": [{
            "path": relative_path.as_posix(), "media_type": "application/json",
            "size_bytes": len(snapshot), "sha256": hashlib.sha256(snapshot).hexdigest(),
        }],
        "metadata": metadata or {},
    })
    _atomic_write(directory / relative_path, snapshot)
    manifest_path = directory / "manifest.json"
    _atomic_write(manifest_path, manifest)
    logger.info("Published %s snapshot %s", source, snapshot_id)
    return manifest_path
