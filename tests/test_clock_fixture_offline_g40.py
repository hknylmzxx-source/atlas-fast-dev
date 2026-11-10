"""G40: çevrimdışı süreç/saat yarışı teşhisi; üretim güvenliği onayı değildir.

Yalnız geçici dizin, yerel flock ve testin başlattığı çocuk süreç kullanılır.
İki karakterizasyon testi mevcut yarışları görünür kılar; geçmeleri bu
yarışların giderildiği anlamına gelmez. Owner/SDK import'u veya trade auth yoktur.
"""
import ast
from contextlib import contextmanager
import os
from pathlib import Path
import selectors
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import protective_wal_deadline_candidate as candidate
from wal_writer_fence_candidate import WalFenceBlocked, writer_fence


# Süre dolunca normal başarıyla kilit bırakılmaz: watchdog çocukta hata üretir.
# Normal bırakma, ebeveyn timeout'u gözledikten sonraki açık RELEASE mesajıdır.
HELD_CHILD = r"""
import select
import sys
sys.path.insert(0, sys.argv[2])
from wal_writer_fence_candidate import writer_fence
with writer_fence(sys.argv[1], expected_uid=int(sys.argv[3])):
    print('LOCKED', flush=True)
    ready, _, _ = select.select([sys.stdin], [], [], float(sys.argv[4]))
    if not ready or sys.stdin.buffer.readline() != b'RELEASE\n':
        raise RuntimeError('G40_FIXTURE_RELEASE_NOT_CONFIRMED')
print('RELEASED', flush=True)
"""


def _expect_line(proc, expected, timeout=5.0):
    """Bölünmüş satır ve EOF dahil readiness bekleyişi monotonic ile sınırlı."""
    deadline = time.monotonic() + timeout
    received = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise RuntimeError("G40_FIXTURE_READINESS_TIMEOUT")
            byte = os.read(proc.stdout.fileno(), 1)
            if not byte:
                raise RuntimeError("G40_FIXTURE_READINESS_EOF")
            received.extend(byte)
            if byte == b"\n":
                if bytes(received) != expected:
                    raise RuntimeError("G40_FIXTURE_UNEXPECTED_ACK")
                return
            if len(received) > 128:
                raise RuntimeError("G40_FIXTURE_ACK_TOO_LONG")


@contextmanager
def _ready_child(directory, program=HELD_CHILD, duration=10.0):
    """Sadece başlattığı çocuğu sonlandırır; diğer süreçlere erişmez."""
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", program, str(directory), str(ROOT),
         str(os.getuid()), str(duration)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        _expect_line(proc, b"LOCKED\n")
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=5)


@contextmanager
def _held_child(directory):
    with _ready_child(directory) as proc:
        if proc.poll() is not None:
            raise RuntimeError("G40_FIXTURE_CHILD_ALREADY_EXITED")
        try:
            with writer_fence(directory, expected_uid=os.getuid()):
                raise RuntimeError("G40_FIXTURE_READY_WITHOUT_ACTIVE_FENCE")
        except WalFenceBlocked as error:
            if str(error) != "WAL_FENCE_BUSY":
                raise
        yield proc


def _release_and_wait(proc):
    proc.stdin.write(b"RELEASE\n")
    proc.stdin.flush()
    _expect_line(proc, b"RELEASED\n")
    if proc.wait(timeout=5) != 0:
        raise RuntimeError("G40_FIXTURE_CHILD_FAILED")


def _legacy_timed_child():
    # Eski test modülü import edilmez; CHILD sabiti yalnız AST ile okunur.
    tree = ast.parse((ROOT / "tests/test_protective_real_clock_fence_offline.py").read_text())
    matches = [node.value for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "CHILD"
                       for target in node.targets)]
    if len(matches) != 1:
        raise RuntimeError("G40_LEGACY_CHILD_SOURCE_CHANGED")
    return ast.literal_eval(matches[0])


class ClockFixtureOfflineG40Tests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="atlas-g40-clock-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.marker = self.directory / "STOP_G40_OFFLINE.json"

    def append(self, **kwargs):
        return candidate.append_protective_bounded(
            self.directory, role="STOP", identity="G40_OFFLINE",
            scope="G40_OFFLINE_SCOPE", symbol="AINUSDT", client_id="G40_OFFLINE_CID",
            expected_uid=os.getuid(), timeout_s=0.025, poll_s=0.005, **kwargs)

    def test_held_child_timeout_then_release_never_retries_or_writes(self):
        with _held_child(self.directory) as proc:
            with self.assertRaisesRegex(candidate.ProtectiveJournalDeadlineExceeded,
                                        "NO_POST_OWNER_REVIEW"):
                self.append()
            self.assertIsNone(proc.poll())
            self.assertFalse(self.marker.exists())
            _release_and_wait(proc)
            self.assertFalse(self.marker.exists())

    def test_characterizes_stale_timed_ready_before_parent_append(self):
        # Ebeveynin LOCKED sonrasında schedule edilmemesinin kontrollü karşılığı.
        with _ready_child(self.directory, _legacy_timed_child(), 0.15) as proc:
            self.assertEqual(proc.wait(timeout=5), 0)
            self.assertEqual(self.append(), self.marker.name)
            self.assertTrue(self.marker.is_file())

    def test_characterizes_unlocked_retry_after_monotonic_oversleep(self):
        now = [100.0]
        attempts = []
        sleeps = []
        original_append = candidate.append_order_journal
        with _held_child(self.directory) as proc:
            def attempt(*args, **kwargs):
                attempts.append(now[0])
                return original_append(*args, **kwargs)

            def oversleep(delay):
                sleeps.append(delay)
                _release_and_wait(proc)
                now[0] += 0.15

            with patch.object(candidate, "append_order_journal", side_effect=attempt):
                result = self.append(clock=lambda: now[0], sleep=oversleep)
            self.assertEqual(result, self.marker.name)
            self.assertEqual(attempts, [100.0, 100.15])
            self.assertEqual(sleeps, [0.005])
            self.assertGreater(now[0], 100.0 + 0.025)
            self.assertTrue(self.marker.is_file())

    def test_still_busy_after_monotonic_oversleep_refuses_without_write(self):
        now = [100.0]
        sleeps = []
        with _held_child(self.directory) as proc:
            def oversleep(delay):
                sleeps.append(delay)
                now[0] += 0.15

            with self.assertRaises(candidate.ProtectiveJournalDeadlineExceeded):
                self.append(clock=lambda: now[0], sleep=oversleep)
            self.assertEqual(sleeps, [0.005])
            self.assertFalse(self.marker.exists())
            _release_and_wait(proc)
            self.assertFalse(self.marker.exists())

    def test_missing_child_readiness_aborts_without_journal_attempt(self):
        with patch.object(candidate, "append_order_journal") as append:
            with self.assertRaisesRegex(RuntimeError, "G40_FIXTURE_READINESS_EOF"):
                with _ready_child(self.directory, program="raise SystemExit(7)"):
                    self.append()
            append.assert_not_called()
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
