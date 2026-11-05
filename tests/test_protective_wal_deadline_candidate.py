"""Synthetic protective bounded WAL wait tests: never transmit an order."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protective_wal_deadline_candidate import (
    append_protective_bounded,ProtectiveJournalDeadlineExceeded)
from wal_writer_fence_candidate import writer_fence,WalFenceBlocked
from emergency_latch_candidate import publish_alarm_latch

class ProtectiveDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix="atlas-protective-deadline-")
        self.root=Path(self.temp.name)
        self.root.chmod(0o700)
        self.uid=os.getuid()
        self.now=0.0
        self.attempts=0
    def tearDown(self):
        self.temp.cleanup()
    def tick(self,delay):
        self.now+=delay
        self.attempts+=1
    def write(self,**kwargs):
        return append_protective_bounded(
            self.root,role="STOP",identity="offline_id",scope="SCOPE",
            symbol="AINUSDT",client_id="OFFLINE_STOP",
            expected_uid=self.uid,clock=lambda:self.now,
            sleep=self.tick,**kwargs)
    def test_uncontended_protective_write_immediate(self):
        self.assertEqual(self.write(),"STOP_offline_id.json")
        self.assertEqual(self.attempts,0)
    def test_contended_deadline_expiry_never_creates_journal(self):
        with writer_fence(self.root,expected_uid=self.uid):
            with self.assertRaisesRegex(
                    ProtectiveJournalDeadlineExceeded,"NO_POST_OWNER_REVIEW"):
                self.write(timeout_s=0.03,poll_s=0.01)
        self.assertEqual(self.attempts,3)
        self.assertFalse((self.root/"STOP_offline_id.json").exists())
    def test_short_contention_then_release_before_deadline(self):
        # A fake sleep is only a test hook; does not simulate real writer
        # holding and releasing a lock while this test owns it.
        with writer_fence(self.root,expected_uid=self.uid):
            with self.assertRaises(ProtectiveJournalDeadlineExceeded):
                self.write(timeout_s=0,poll_s=0.01)
        self.assertEqual(self.write(timeout_s=0.04),"STOP_offline_id.json")
    def test_duplicate_wal_is_never_retried(self):
        self.write()
        with self.assertRaisesRegex(WalFenceBlocked,"WAL_DUPLICATE_NO_WRITE"):
            self.write(timeout_s=0.1)
        self.assertEqual(self.attempts,0)
    def test_resolved_marker_refusal_not_retried(self):
        (self.root/"resolved_SCOPE.json").write_text('{"scope":"SCOPE"}')
        (self.root/"resolved_SCOPE.json").chmod(0o600)
        with self.assertRaisesRegex(WalFenceBlocked,"SCOPE_ALREADY_RESOLVED"):
            self.write(timeout_s=0.1)
        self.assertEqual(self.attempts,0)
    def test_non_protective_role_is_rejected(self):
        with self.assertRaisesRegex(WalFenceBlocked,"PROTECTIVE_ROLE_ONLY"):
            append_protective_bounded(self.root,role="ENTRY",
                identity="x",scope="SCOPE",symbol="AINUSDT",
                client_id="OFFLINE",expected_uid=self.uid)
    def test_invalid_timeout_refused(self):
        with self.assertRaisesRegex(WalFenceBlocked,"WAIT_BOUND_INVALID"):
            self.write(timeout_s=10)
    def test_emergency_alarm_can_be_saved_even_when_wait_exhausts(self):
        with writer_fence(self.root,expected_uid=self.uid):
            with self.assertRaises(ProtectiveJournalDeadlineExceeded):
                self.write(timeout_s=0.02)
            self.assertEqual(publish_alarm_latch(self.root,scope="SCOPE",
                 reason="PROTECTIVE_WAL_DEADLINE",expected_uid=self.uid),
                 "LATCHED")
        self.assertTrue((self.root/"emergency_latch_SCOPE.json").exists())

if __name__=="__main__":
    unittest.main()
