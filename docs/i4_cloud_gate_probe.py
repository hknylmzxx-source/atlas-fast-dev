"""Test publish ordering and cloud rejection using isolated synthetic bytes."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from i4_cloud_gate_candidate import prepare_upload
from i4_snapshot_publish_candidate import publish_pair


class CloudGate(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.archive = self.root / "Atlas_20261113_033005.tar.gz"
        self.report = self.root / "Atlas_20261113_033005.report.json"
        self.dest = self.root / "uploaded"
        self.candidate = self.root / "candidate"
        self.candidate.write_bytes(b"synthetic archive bytes")
        self.data = {"status": "PARTIAL", "final_archive": str(self.archive),
                     "archive_sha256": hashlib.sha256(self.candidate.read_bytes()).hexdigest()}

    def publish(self, **kw):
        publish_pair(self.candidate, self.archive, self.report, self.data, **kw)

    def test_pair_copied_after_access_grants(self):
        grants = []
        def grant(path):
            self.assertFalse(self.archive.exists())
            grants.append(path)
        self.publish(grant=grant)
        self.assertEqual(len(grants), 2)
        self.assertEqual(prepare_upload(self.archive, self.report, self.dest), ("PARTIAL", self.data["archive_sha256"]))
        self.assertEqual(self.dest.read_bytes(), self.candidate.read_bytes())

    def test_access_grant_failure_blocks_publication(self):
        def grant(path):
            if path != self.candidate:
                raise PermissionError("ACL failed")
        with self.assertRaises(PermissionError):
            self.publish(grant=grant)
        self.assertFalse(self.archive.exists())
        self.assertFalse(self.report.exists())

    def test_missing_report(self):
        self.candidate.rename(self.archive)
        with self.assertRaises(FileNotFoundError):
            prepare_upload(self.archive, self.report, self.dest)
        self.assertFalse(self.dest.exists())

    def test_orphan_report(self):
        self.report.write_text(json.dumps(self.data))
        with self.assertRaises(FileNotFoundError):
            prepare_upload(self.archive, self.report, self.dest)
        self.assertFalse(self.dest.exists())

    def test_wrong_digest(self):
        self.publish()
        self.archive.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            prepare_upload(self.archive, self.report, self.dest)
        self.assertFalse(self.dest.exists())

    def test_wrong_report_identity(self):
        self.data["final_archive"] = "/different/archive"
        self.publish()
        with self.assertRaisesRegex(ValueError, "does not validate"):
            prepare_upload(self.archive, self.report, self.dest)
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()
