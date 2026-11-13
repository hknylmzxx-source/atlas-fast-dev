"""Test promotion using a copy of the installed snapshot v4 module."""
import hashlib
import json
import os
import signal
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

from i4_rotation_probe import append
from i4_snapshot_publish_candidate import GROUP, NAME, build_verified
from i4_snapshot_stage_candidate import verify_members

loader = importlib.machinery.SourceFileLoader("snapshot_v4", sys.argv.pop(1))
spec = importlib.util.spec_from_loader(loader.name, loader)
v4 = importlib.util.module_from_spec(spec)
loader.exec_module(v4)


class Publication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "source"
        self.active = self.root / GROUP / NAME
        self.active.parent.mkdir(parents=True)
        for n in range(4):
            append(self.active, {"id": n}, 16)
        (self.active.parent / "unrelated").write_bytes(b"safe\n")
        self.archive = self.base / "Atlas_example.tar.gz"
        self.report = self.base / "report.json"

    def run_candidate(self, **kwargs):
        return build_verified(v4, self.root, self.archive, self.report, required=[GROUP], **kwargs)

    def test_gate_before_final_name_and_rotation_during_capture(self):
        def gate(tar, prefix, expected):
            self.assertFalse(self.archive.exists())
            verify_members(tar, prefix, expected)
        result = self.run_candidate(after_capture=lambda: append(self.active, {"id": 4}, 1), gate=gate)
        self.assertEqual(result["status"], "OK", result)
        with tarfile.open(self.archive) as tar:
            self.assertEqual(tar.extractfile(f"Atlas/{GROUP}/{NAME}").read(), b'{"id": 3}\n')
            self.assertEqual(tar.extractfile(f"Atlas/{GROUP}/unrelated").read(), b"safe\n")
        self.assertEqual(list(self.base.glob(".i4-work-*")), [])

    def test_missing_part_no_publish(self):
        def gate(tar, prefix, expected):
            verify_members(tar, prefix, dict(expected, missing={"bytes": 0, "sha256": ""}))
        result = self.run_candidate(gate=gate)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("missing or changed", str(result["fatal"]))
        self.assertFalse(self.archive.exists())

    def test_existing_final_is_never_overwritten(self):
        self.archive.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            self.run_candidate()
        self.assertEqual(self.archive.read_bytes(), b"existing")

    def test_capture_partial_no_publish(self):
        with self.active.open("ab") as f:
            f.write(b"{")
        result = self.run_candidate()
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(self.archive.exists())

    def crash_at(self, point):
        pid = os.fork()
        if pid == 0:
            def checkpoint(current):
                if current == point:
                    os.kill(os.getpid(), signal.SIGKILL)
            try:
                self.run_candidate(checkpoint=checkpoint)
            finally:
                os._exit(91)
        _, status = os.waitpid(pid, 0)
        self.assertTrue(os.WIFSIGNALED(status))
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)

    def test_kill_before_report_leaves_no_public_names(self):
        self.crash_at("before_report")
        self.assertFalse(self.report.exists())
        self.assertFalse(self.archive.exists())

    def test_kill_after_report_leaves_no_cloud_visible_archive(self):
        self.crash_at("after_report")
        data = json.loads(self.report.read_text())
        self.assertEqual(data["status"], "OK")
        self.assertIn("must exist", data["publication_rule"])
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.base.glob("Atlas_*.tar.gz")), [])

    def test_kill_after_archive_leaves_valid_report_and_archive(self):
        self.crash_at("after_archive")
        data = json.loads(self.report.read_text())
        self.assertEqual(data["final_archive"], str(self.archive))
        self.assertEqual(data["archive_sha256"], hashlib.sha256(self.archive.read_bytes()).hexdigest())

    def test_conflicting_archive_after_report_is_never_overwritten(self):
        def compete(point):
            if point == "after_report":
                self.archive.write_bytes(b"other process")
        with self.assertRaises(FileExistsError):
            self.run_candidate(checkpoint=compete)
        self.assertEqual(self.archive.read_bytes(), b"other process")
        self.assertNotEqual(json.loads(self.report.read_text())["archive_sha256"],
                            hashlib.sha256(self.archive.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
