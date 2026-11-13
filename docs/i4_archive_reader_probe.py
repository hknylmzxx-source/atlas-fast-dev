"""Offline reference reader for I4 JSONL rotation. Not an installer."""
from contextlib import ExitStack, contextmanager
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile
import unittest

from i4_rotation_probe import append


@contextmanager
def snapshot_parts(path):
    """Capture ordered file descriptors and length boundaries under the writer lock."""
    with ExitStack() as stack:
        with path.with_name(path.name + ".lock").open("ab") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            pattern = re.compile(re.escape(path.name) + r"\.\d{20}\.[0-9a-f]{32}\.archive")
            archives = sorted(p for p in path.parent.iterdir() if pattern.fullmatch(p.name))
            parts = []
            for part in (*archives, path):
                f = stack.enter_context(part.open("rb"))
                parts.append((f, os.fstat(f.fileno()).st_size))
        yield parts


def validated_lines(file, remaining=None):
    while remaining is None or remaining:
        line = file.readline() if remaining is None else file.readline(remaining)
        if not line:
            if remaining is not None:
                raise ValueError("part shrank after snapshot")
            return
        if not line.endswith(b"\n"):
            raise ValueError("partial JSONL record")
        if not isinstance(json.loads(line), dict):
            raise ValueError("JSONL record is not an object")
        if remaining is not None:
            remaining -= len(line)
        yield line


def ordered_lines(parts):
    for file, size in parts:
        yield from validated_lines(file, size)


def fingerprint(lines):
    digest = hashlib.sha256()
    count = 0
    for line in lines:
        digest.update(line)
        count += 1
    return count, digest.hexdigest()


def verify_gzip(archive, candidate):
    with archive.open("rb") as original, gzip.open(candidate, "rb") as compressed:
        if fingerprint(validated_lines(original)) != fingerprint(validated_lines(compressed)):
            raise ValueError("archive fingerprint mismatch")


class ArchiveProbe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / "events.jsonl"
        for i in range(30):
            append(self.log, {"id": i, "note": "ölçüm"}, 150)

    def ids(self, log):
        with snapshot_parts(log) as parts:
            return [json.loads(line)["id"] for line in ordered_lines(parts)]

    def test_reads_archives_then_active(self):
        self.assertGreater(len(list(self.root.glob("*.archive"))), 2)
        self.assertEqual(self.ids(self.log), list(range(30)))

    def test_snapshot_survives_rotation_and_excludes_new_append(self):
        with snapshot_parts(self.log) as parts:
            append(self.log, {"id": 30}, 1)
            self.assertEqual([json.loads(l)["id"] for l in ordered_lines(parts)], list(range(30)))
        self.assertEqual(self.ids(self.log), list(range(31)))

    def test_snapshot_excludes_later_append_without_rotation(self):
        with snapshot_parts(self.log) as parts:
            append(self.log, {"id": 30}, 4096)
            self.assertEqual([json.loads(l)["id"] for l in ordered_lines(parts)], list(range(30)))

    def test_rejects_truncated_log_and_missing_active(self):
        with self.log.open("ab") as f:
            f.write(b'{"id":')
        with self.assertRaisesRegex(ValueError, "partial JSONL"):
            self.ids(self.log)
        self.log.unlink()
        with self.assertRaises(FileNotFoundError):
            self.ids(self.log)

    def test_gzip_verified_and_mismatch_rejected_without_removal(self):
        archive = sorted(self.root.glob("*.archive"))[0]
        candidate = self.root / "candidate.gz"
        candidate.write_bytes(gzip.compress(archive.read_bytes()))
        verify_gzip(archive, candidate)
        candidate.write_bytes(gzip.compress(b'{"id": -1}\n'))
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            verify_gzip(archive, candidate)
        self.assertTrue(archive.exists())

    def test_tar_restore_preserves_complete_stream(self):
        backup = io.BytesIO()
        expected_parts = sorted(self.root.glob("*.archive")) + [self.log]
        with tarfile.open(fileobj=backup, mode="w:gz") as tar:
            for p in expected_parts:
                tar.add(p, arcname=p.name)
        backup.seek(0)
        restore = self.root / "restore"
        restore.mkdir()
        with tarfile.open(fileobj=backup, mode="r:gz") as tar:
            self.assertEqual(tar.getnames(), [p.name for p in expected_parts])
            for p in expected_parts:
                with tar.extractfile(p.name) as f:
                    (restore / p.name).write_bytes(f.read())
        self.assertEqual(self.ids(restore / self.log.name), list(range(30)))


if __name__ == "__main__":
    unittest.main()
