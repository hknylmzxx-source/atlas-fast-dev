"""Test promotion using a copy of the installed snapshot v4 module."""
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


if __name__ == "__main__":
    unittest.main()
