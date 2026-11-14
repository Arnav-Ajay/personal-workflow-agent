"""JSON snapshots published through an atomically replaced latest manifest."""

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.observability import trace

logger = logging.getLogger(__name__)


def _validate_envelope(value: Any, description: str) -> None:
    """Validate shared metadata before trusting a manifest or snapshot."""
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError(f"Unsupported {description}")
    for field in ("source", "snapshot_id", "trace_id"):
        if field == "trace_id" and field in value and value[field] is None:
            continue
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"Invalid {description} {field}")
    for field in ("as_of", "created_at"):
        timestamp = value.get(field)
        try:
            if not isinstance(timestamp, str):
                raise ValueError("Timestamp must be a string")
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Timestamp must include a timezone")
        except ValueError as exc:
            raise ValueError(f"Invalid {description} {field}") from exc


@trace("artifacts.load_latest_snapshot")
def load_latest_snapshot(directory: Path, *, source: str | None = None) -> dict[str, Any]:
    """Read the manifest once and verify its referenced snapshot before use."""
    directory = directory.resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    _validate_envelope(manifest, "artifact manifest")
    if not isinstance(manifest.get("metadata"), dict):
        raise ValueError("Manifest metadata must be an object")
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError("Manifest must reference exactly one snapshot")
    entry = entries[0]
    if (type(entry.get("size_bytes")) is not int or entry["size_bytes"] < 0 or
            not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
        raise ValueError("Invalid snapshot size or checksum in manifest")
    if not isinstance(entry.get("path"), str) or not entry["path"]:
        raise ValueError("Manifest has an invalid snapshot path")
    path = (directory / entry["path"]).resolve()
    if not path.is_relative_to(directory):
        raise ValueError("Snapshot path must stay inside the artifact directory")
    content = path.read_bytes()
    if entry["size_bytes"] != len(content) or entry["sha256"] != hashlib.sha256(content).hexdigest():
        raise ValueError("Snapshot size or checksum does not match its manifest")
    snapshot = json.loads(content)
    _validate_envelope(snapshot, "snapshot schema")
    if not isinstance(snapshot.get("data"), dict):
        raise ValueError("Snapshot data must be an object")
    for field in ("source", "snapshot_id", "trace_id", "as_of", "created_at"):
        if snapshot[field] != manifest[field]:
            raise ValueError(f"Snapshot {field} does not match its manifest")
    if source is not None and snapshot["source"] != source:
        raise ValueError(f"Expected a {source} snapshot")
    logger.info("Loaded verified %s snapshot %s", snapshot["source"], snapshot["snapshot_id"])
    return snapshot


def _json_default(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in fields(value)}
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


def atomic_write(path: Path, content: bytes) -> None:
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
    if not isinstance(source, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", source):
        raise ValueError("source must be a simple component name")
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    if not isinstance(data, dict) or (metadata is not None and not isinstance(metadata, dict)):
        raise ValueError("Snapshot data and metadata must be objects")
    if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip()):
        raise ValueError("trace_id must be a nonblank string or None")
    created_at = datetime.now(timezone.utc)
    snapshot_id = uuid4().hex
    relative_path = Path("snapshots") / f"{snapshot_id}.json"
    envelope = {
        "schema_version": 1, "source": source, "snapshot_id": snapshot_id,
        "trace_id": trace_id, "as_of": as_of, "created_at": created_at,
    }
    snapshot = _encode({**envelope, "data": data})
    manifest = _encode({
        **envelope,
        "artifacts": [{
            "path": relative_path.as_posix(), "media_type": "application/json",
            "size_bytes": len(snapshot), "sha256": hashlib.sha256(snapshot).hexdigest(),
        }],
        "metadata": metadata or {},
    })
    atomic_write(directory / relative_path, snapshot)
    manifest_path = directory / "manifest.json"
    atomic_write(manifest_path, manifest)
    logger.info("Published %s snapshot %s", source, snapshot_id)
    return manifest_path
