"""Snapshot publication and serialization guarantees."""

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from src.commons import artifacts


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.as_of = datetime(2026, 11, 13, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    def save(self, data=None):
        return artifacts.save_snapshot(
            self.directory, source="test", as_of=self.as_of,
            data=data if data is not None else {"balance": Decimal("1.234567890123456789")},
        )

    def test_latest_manifest_references_complete_snapshot_and_retains_history(self):
        first = json.loads(self.save().read_text())
        second = json.loads(self.save({"balance": Decimal("2")}).read_text())
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(len(list((self.directory / "snapshots").glob("*.json"))), 2)
        for manifest in (first, second):
            entry = manifest["artifacts"][0]
            content = (self.directory / entry["path"]).read_bytes()
            self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(entry["size_bytes"], len(content))
            snapshot = json.loads(content)
            self.assertEqual(snapshot["as_of"], "2026-11-12T18:30:00+00:00")
            self.assertEqual(snapshot["snapshot_id"], manifest["snapshot_id"])

    def test_failed_snapshot_or_manifest_replace_preserves_previous_latest(self):
        manifest_path = self.save()
        previous = manifest_path.read_bytes()
        replace = artifacts.os.replace
        for target in ("snapshot", "manifest"):
            def fail_replace(source, destination):
                is_manifest = Path(destination).name == "manifest.json"
                if is_manifest == (target == "manifest"):
                    raise OSError("disk failure")
                return replace(source, destination)

            with self.subTest(target=target), patch.object(artifacts.os, "replace", side_effect=fail_replace):
                with self.assertRaises(OSError):
                    self.save()
            self.assertEqual(manifest_path.read_bytes(), previous)
            self.assertFalse(list(self.directory.rglob(".tmp-*")))

    def test_serialization_failure_does_not_publish_or_modify_files(self):
        manifest_path = self.save()
        previous = manifest_path.read_bytes()
        for value in (object(), Decimal("NaN"), float("inf"), datetime(2026, 1, 1)):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                self.save({"bad": value})
        self.assertEqual(manifest_path.read_bytes(), previous)
        self.assertEqual(len(list((self.directory / "snapshots").glob("*.json"))), 1)

    def test_as_of_must_be_timezone_aware(self):
        for value in (None, datetime(2026, 1, 1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                artifacts.save_snapshot(self.directory, source="test", data={}, as_of=value)
        self.assertFalse((self.directory / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
