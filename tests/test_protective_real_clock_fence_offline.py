"""Real-clock cross-process WAL wait experiments; OFFLINE ONLY.

The child holds a local POSIX flock in a disposable directory. No exchange,
Owner SDK import, live order POST, tokens, or persistent runtime mutation.
"""
import os
from pathlib import Path
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protective_wal_deadline_candidate import (
    append_protective_bounded,ProtectiveJournalDeadlineExceeded)
from wal_writer_fence_candidate import WalFenceBlocked
from emergency_latch_candidate import (
    publish_alarm_latch,require_no_emergency_latch,EmergencyLatchBlocked)

CHILD=(
  "import sys,time;"
  "sys.path.insert(0,sys.argv[2]);"
  "from wal_writer_fence_candidate import writer_fence;"
  "ctx=writer_fence(sys.argv[1],expected_uid=int(sys.argv[3]));"
  "fd=ctx.__enter__();"
  "print('LOCKED',flush=True);"
  "time.sleep(float(sys.argv[4]));"
  "ctx.__exit__(None,None,None)"
)

class CrossProcessDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix="atlas-real-clock-fence-")
        self.root=Path(self.temp.name)
        self.root.chmod(0o700)
        self.uid=os.getuid()
        self.children=[]
    def tearDown(self):
        for proc in self.children:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)
        self.temp.cleanup()
    def child(self,hold_seconds):
        proc=subprocess.Popen(
            [sys.executable,"-B","-c",CHILD,str(self.root),str(ROOT),
             str(self.uid),str(hold_seconds)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.children.append(proc)
        self.assertEqual(proc.stdout.readline().strip(),"LOCKED")
        return proc
    def append(self,role="STOP",name="OFFLINE_ID",timeout=0.2):
        return append_protective_bounded(self.root,role=role,identity=name,
            scope="OFFLINE_SCOPE",symbol="AINUSDT",client_id="OFFLINE_CID",
            expected_uid=self.uid,timeout_s=timeout,poll_s=0.005)

    def test_child_releases_before_deadline_stop_journal_written(self):
        proc=self.child(0.04)
        begin=time.monotonic()
        self.assertEqual(self.append(timeout=0.25),"STOP_OFFLINE_ID.json")
        elapsed=time.monotonic()-begin
        self.assertGreaterEqual(elapsed,0)
        self.assertLess(elapsed,2.0) # test-run health bound, NOT live SLA
        self.assertTrue((self.root/"STOP_OFFLINE_ID.json").is_file())
        proc.wait(timeout=5)

    def test_child_holds_beyond_deadline_stop_fails_without_journal(self):
        self.child(0.25)
        begin=time.monotonic()
        with self.assertRaisesRegex(ProtectiveJournalDeadlineExceeded,
                                    "NO_POST_OWNER_REVIEW"):
            self.append(timeout=0.03)
        elapsed=time.monotonic()-begin
        self.assertGreaterEqual(elapsed,0.02)
        self.assertFalse((self.root/"STOP_OFFLINE_ID.json").exists())

    def test_tp_write_after_child_unlocks(self):
        self.child(0.03)
        self.assertEqual(self.append(role="TP1",timeout=0.25),
                         "TP1_OFFLINE_ID.json")

    def test_alarm_persists_while_child_holds_lock_on_deadline(self):
        self.child(0.25)
        with self.assertRaises(ProtectiveJournalDeadlineExceeded):
            self.append(timeout=0.025)
        self.assertEqual(publish_alarm_latch(self.root,scope="OFFLINE_SCOPE",
            reason="OFFLINE_STOP_DEADLINE",expected_uid=self.uid),"LATCHED")
        with self.assertRaisesRegex(EmergencyLatchBlocked,"NO_ENTRY"):
            require_no_emergency_latch(self.root,expected_uid=self.uid)

    def test_no_implicit_retry_when_child_releases_after_timeout(self):
        proc=self.child(0.15)
        with self.assertRaises(ProtectiveJournalDeadlineExceeded):
            self.append(timeout=0.025)
        proc.wait(timeout=5)
        self.assertFalse((self.root/"STOP_OFFLINE_ID.json").exists())

if __name__=="__main__":
    unittest.main()
