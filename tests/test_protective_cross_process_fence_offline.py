"""Cross-process STOP/TP WAL contention diagnostic; strictly synthetic.

A separate Python process holds the actual advisory WAL fence. The parent
runs selected Owner write functions using fake venue and temporary WAL only.
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
from test_safety_chain_integration import (
    Integration,PreSendRefused,STOP,ENTRY,SCOPE,SYM)
from wal_owner_write_adapter_candidate import append_order_journal
from emergency_latch_candidate import (
    publish_alarm_latch,require_no_emergency_latch,EmergencyLatchBlocked)

TP={"symbol":SYM,"newClientOrderId":"OFFLINE-TP-CHILD"}
CHILD=(
    "import os,sys;"
    "sys.path.insert(0,sys.argv[2]);"
    "from wal_writer_fence_candidate import writer_fence;"
    "ctx=writer_fence(sys.argv[1],expected_uid=int(sys.argv[3]));"
    "fd=ctx.__enter__();"
    "print('LOCKED',flush=True);"
    "sys.stdin.readline();"
    "ctx.__exit__(None,None,None)"
)

class ProtectiveCrossProcessFenceTests(unittest.TestCase):
    def setUp(self):
        self.s=Integration()
        self.s.reserve()
        self.uid=os.getuid()
        self.s.ns["journal"]=self.journal
        self.child=subprocess.Popen(
            [sys.executable,"-B","-c",CHILD,str(self.s.wal),
             str(ROOT),str(self.uid)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True,bufsize=1)
        self.assertEqual(self.child.stdout.readline().strip(),"LOCKED")

    def tearDown(self):
        try:
            if self.child.poll() is None:
                self.child.stdin.write("\n")
                self.child.stdin.flush()
                self.child.wait(timeout=5)
        finally:
            if self.child.poll() is None:
                self.child.kill()
                self.child.wait()
            self.child.stdin.close()
            self.child.stdout.close()
            self.child.stderr.close()
            self.s.close()

    def journal(self,role,identity,detail):
        return append_order_journal(
            self.s.wal,role=role,identity=identity,
            scope=detail["scope"],symbol=detail["symbol"],
            client_id=detail["client_id"],expected_uid=self.uid)

    def test_stop_is_refused_without_post_across_process_boundary(self):
        with self.assertRaisesRegex(PreSendRefused,"WAL_FENCE_BUSY"):
            self.s.ns["place_stop_write"](
                self.s.venue,STOP,SCOPE,sleep=lambda _:None)
        self.assertEqual(self.s.venue.post_calls,[])
        self.assertEqual(self.s.venue.lease,0)

    def test_tp_is_refused_without_post_across_process_boundary(self):
        with self.assertRaisesRegex(PreSendRefused,"WAL_FENCE_BUSY"):
            self.s.ns["one_write"](self.s.venue,"TP1",TP,SCOPE)
        self.assertEqual(self.s.venue.post_calls,[])
        self.assertEqual(self.s.venue.lease,0)

    def test_entry_is_refused_without_post_across_process_boundary(self):
        with self.assertRaisesRegex(PreSendRefused,"WAL_FENCE_BUSY"):
            self.s.ns["one_write"](
                self.s.venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        self.assertEqual(self.s.venue.post_calls,[])
        self.assertEqual(self.s.venue.lease,0)

    def test_alarm_is_persisted_while_other_process_holds_fence(self):
        self.assertEqual(publish_alarm_latch(
            self.s.wal,scope=SCOPE,reason="PROTECTIVE_STOP_BLOCKED",
            expected_uid=self.uid),"LATCHED")
        with self.assertRaisesRegex(EmergencyLatchBlocked,"NO_ENTRY"):
            require_no_emergency_latch(self.s.wal,expected_uid=self.uid)
        with self.assertRaisesRegex(Exception,"EMERGENCY_LATCH"):
            self.s.ns["startup_reconcile"]()

    def test_no_implicit_retry_after_fence_release(self):
        with self.assertRaises(PreSendRefused):
            self.s.ns["place_stop_write"](
                self.s.venue,STOP,SCOPE,sleep=lambda _:None)
        self.child.stdin.write("\n")
        self.child.stdin.flush()
        self.child.wait(timeout=5)
        self.assertEqual(self.s.venue.post_calls,[])
        self.assertEqual(len(list(self.s.wal.glob("STOP_*.json"))),0)

if __name__=="__main__":
    unittest.main()
