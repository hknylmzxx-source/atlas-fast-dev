"""Offline probe for cooperative JSONL rotation. Do not install."""
import fcntl
import json
import multiprocessing as mp
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

def append(path, row, limit):
    data = json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n"
    with path.with_name(path.name + ".lock").open("ab") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        archives = lambda: list(path.parent.glob(path.name + ".*.archive"))
        if not path.exists():
            old = archives()
            mode = stat.S_IMODE(max(old, key=lambda f: f.name).stat().st_mode) if old else 0o640
            with path.open("xb") as created:
                os.fchmod(created.fileno(), mode)
        info = path.stat()
        if info.st_size and info.st_size + len(data) > limit:
            import time
            archive = path.with_name(f"{path.name}.{time.time_ns():020d}.{uuid4().hex}.archive")
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".i4-", delete=False) as new:
                tmp = Path(new.name)
                try:
                    os.fchmod(new.fileno(), stat.S_IMODE(info.st_mode))
                    fresh = os.fstat(new.fileno())
                    if (fresh.st_uid, fresh.st_gid) != (info.st_uid, info.st_gid):
                        raise PermissionError("ownership mismatch")
                    os.fsync(new.fileno())
                    os.rename(path, archive)
                    os.replace(tmp, path)
                finally:
                    tmp.unlink(missing_ok=True)
        with path.open("ab") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())

def worker(path, group):
    for n in range(100):
        append(path, {"id": group * 100 + n}, 512)

class Probe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "events.jsonl"
        self.path.write_bytes(b'{"id": -1}\n')
        self.path.chmod(0o644)

    def rows(self):
        files = sorted(self.path.parent.glob("*.archive"))
        return [json.loads(line)["id"] for f in [*files, self.path] for line in f.read_bytes().splitlines()]

    def test_concurrent_rotation(self):
        ps = [mp.Process(target=worker, args=(self.path, i)) for i in range(4)]
        for p in ps: p.start()
        for p in ps:
            p.join(20)
            self.assertEqual(p.exitcode, 0)
        self.assertEqual(sorted(self.rows()), list(range(-1, 400)))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o644)

    def test_rename_denied_leaves_active_unchanged(self):
        before = self.path.read_bytes()
        with patch("os.rename", side_effect=PermissionError):
            with self.assertRaises(PermissionError): append(self.path, {"id": 0}, 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".i4-*")), [])

    def test_restart_after_old_file_archived(self):
        with patch("os.replace", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError): append(self.path, {"id": 0}, 1)
        self.assertFalse(self.path.exists())
        append(self.path, {"id": 0}, 100)
        self.assertEqual(self.rows(), [-1, 0])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o644)

    def test_temp_disk_full_preserves_active(self):
        before = self.path.read_bytes()
        with patch("tempfile.NamedTemporaryFile", side_effect=OSError(28, "disk full")):
            with self.assertRaises(OSError): append(self.path, {"id": 0}, 1)
        self.assertEqual(self.path.read_bytes(), before)

if __name__ == "__main__":
    unittest.main()
