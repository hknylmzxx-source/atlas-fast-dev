"""Synthetic 03:45 job tests."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from i4_cloud_job_candidate import run


class CloudJob(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.src, self.dst, self.stage = [self.base / n for n in ("src", "dst", "stage")]
        for p in (self.src, self.dst, self.stage, self.src / "raporlar"):
            p.mkdir()
        self.a = self.src / "Atlas_20261113_033005.tar.gz"
        self.a.write_bytes(b"sample")
        self.now = time.time()
        self.r = self.src / "raporlar" / "Atlas_20261113_033005.report.json"
        self.state = self.base / "state.json"
        self.target = self.dst / self.a.name

    def report(self, status="OK", sha=None):
        self.r.write_text(json.dumps(dict(status=status, final_archive=str(self.a),
            archive_sha256=sha or hashlib.sha256(self.a.read_bytes()).hexdigest())))

    def job(self):
        result = run(self.src, self.dst, self.stage, self.state, now=self.now)
        self.assertEqual(json.loads(self.state.read_bytes()), result)
        self.assertEqual(list(self.stage.iterdir()), [])
        return result["status"]

    def test_valid_and_existing(self):
        self.report()
        self.assertEqual(self.job(), "OK_COPIED")
        self.assertEqual(self.job(), "OK_ALREADY_PRESENT")

    def test_partial(self):
        self.report("PARTIAL")
        self.assertEqual(self.job(), "PARTIAL_SNAPSHOT")
        self.assertEqual(self.job(), "PARTIAL_SNAPSHOT")

    def test_missing_even_if_existing(self):
        self.target.write_bytes(self.a.read_bytes())
        self.assertEqual(self.job(), "FAIL")

    def test_wrong_sha_no_copy(self):
        self.report(sha="0"*64)
        self.assertEqual(self.job(), "FAIL")
        self.assertFalse(self.target.exists())

    def test_bad_report_supersedes_stale_state(self):
        self.report()
        self.assertEqual(self.job(), "OK_COPIED")
        self.r.write_text("{")
        self.assertEqual(self.job(), "FAIL")

    def test_stale(self):
        self.report()
        os.utime(self.a, (self.now-27*3600,)*2)
        self.assertEqual(self.job(), "STALE_SNAPSHOT")
        self.assertFalse(self.target.exists())

    def test_conflicting_destination_preserved(self):
        self.report()
        self.target.write_bytes(b"old")
        self.assertEqual(self.job(), "FAIL")
        self.assertEqual(self.target.read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
