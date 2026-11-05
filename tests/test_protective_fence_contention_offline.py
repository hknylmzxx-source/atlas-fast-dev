"""Synthetic protective WAL writer-fence contention tests.

Owner one_write/place_stop_write are selected source functions executed with a
fake venue and temporary WAL. Never import live SDK or issue real orders.
"""
import hashlib
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
from test_safety_chain_integration import (
    Integration,PreSendRefused,STOP,SCOPE,SYM,ENTRY)
from wal_writer_fence_candidate import writer_fence
from wal_owner_write_adapter_candidate import append_order_journal
from emergency_latch_candidate import (
    publish_alarm_latch,require_no_emergency_latch,EmergencyLatchBlocked)

TP={"symbol":SYM,"newClientOrderId":"OFFLINE-TP-CONTEND"}

class ProtectiveFenceContentionTests(unittest.TestCase):
    def setUp(self):
        self.sim=Integration()
        self.sim.reserve()
        self.uid=os.getuid()
        self.sim.ns["journal"]=self.journal
    def tearDown(self):
        self.sim.close()
    def journal(self,role,digest,detail):
        return append_order_journal(
            self.sim.wal,role=role,identity=digest,
            scope=detail["scope"],symbol=detail["symbol"],
            client_id=detail["client_id"],expected_uid=self.uid)
    def stop(self):
        return self.sim.ns["place_stop_write"](
            self.sim.venue,STOP,SCOPE,sleep=lambda _:None)
    def alarm(self):
        return publish_alarm_latch(
            self.sim.wal,scope=SCOPE,reason="STOP_WAL_FENCE_BUSY",
            expected_uid=self.uid)

    def test_stop_fence_busy_refuses_post_without_retry(self):
        with writer_fence(self.sim.wal,expected_uid=self.uid):
            with self.assertRaisesRegex(PreSendRefused,"WAL_FENCE_BUSY"):
                self.stop()
        self.assertEqual(self.sim.venue.post_calls,[])
        self.assertEqual(self.sim.venue.lease,0)

    def test_tp_fence_busy_refuses_post(self):
        with writer_fence(self.sim.wal,expected_uid=self.uid):
            with self.assertRaisesRegex(PreSendRefused,"WAL_FENCE_BUSY"):
                self.sim.ns["one_write"](self.sim.venue,"TP1",TP,SCOPE)
        self.assertEqual(self.sim.venue.post_calls,[])

    def test_entry_fence_busy_refuses_post(self):
        with writer_fence(self.sim.wal,expected_uid=self.uid):
            with self.assertRaisesRegex(PreSendRefused,"WAL_FENCE_BUSY"):
                self.sim.ns["one_write"](
                    self.sim.venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        self.assertEqual(self.sim.venue.post_calls,[])

    def test_alarm_survives_busy_stop_fence(self):
        with writer_fence(self.sim.wal,expected_uid=self.uid):
            with self.assertRaisesRegex(PreSendRefused,"WAL_FENCE_BUSY"):
                self.stop()
            self.assertEqual(self.alarm(),"LATCHED")
        with self.assertRaisesRegex(EmergencyLatchBlocked,"NO_ENTRY"):
            require_no_emergency_latch(self.sim.wal,expected_uid=self.uid)
        with self.assertRaisesRegex(Exception,"EMERGENCY_LATCH"):
            self.sim.ns["startup_reconcile"]()

    def test_stop_after_fence_released_writes_only_once(self):
        self.stop()
        self.assertEqual(len(self.sim.venue.post_calls),1)
        self.assertEqual(len(list(self.sim.wal.glob("STOP_*.json"))),1)
        with self.assertRaisesRegex(PreSendRefused,"WAL_DUPLICATE_NO_WRITE"):
            self.stop()
        self.assertEqual(len(self.sim.venue.post_calls),1)

    def test_alarm_does_not_claim_stop_protected(self):
        with writer_fence(self.sim.wal,expected_uid=self.uid):
            with self.assertRaises(PreSendRefused):
                self.stop()
            self.alarm()
        self.assertFalse(self.sim.venue.stop_open)
        self.assertEqual(self.sim.venue.post_calls,[])
        self.assertEqual(self.sim.venue.lease,0)

if __name__=="__main__":
    unittest.main()
