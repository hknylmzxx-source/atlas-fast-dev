"""Offline STOP/TP bounded WAL wait under the selected Owner mutation lease.

Tests use real POSIX flock in a temporary directory, fake venue and clock.
No exchange client, network calls, active Owner SDK imports or live writes.
"""
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
from test_safety_chain_integration import (
    Integration,PreSendRefused,Blocked,SCOPE,SYM,STOP)
from wal_writer_fence_candidate import writer_fence
from wal_owner_write_adapter_candidate import append_order_journal
from protective_wal_deadline_candidate import append_protective_bounded
from emergency_latch_candidate import publish_alarm_latch

TP={"symbol":SYM,"newClientOrderId":"OFFLINE-TP-LEASE"}

class ProtectiveLeaseDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.sim=Integration()
        self.sim.reserve()
        self.uid=os.getuid()
        self.now=0.0
        self.sleeps=[]
        self.sim.ns["journal"]=self.journal
    def tearDown(self):
        self.sim.close()
    def tick(self,secs):
        self.now+=secs
        self.sleeps.append(secs)
    def journal(self,role,identity,detail):
        if role in ("STOP","TP1","TP2"):
            return append_protective_bounded(
                self.sim.wal,role=role,identity=identity,
                scope=detail["scope"],symbol=detail["symbol"],
                client_id=detail["client_id"],expected_uid=self.uid,
                timeout_s=0.03,poll_s=0.01,
                clock=lambda:self.now,sleep=self.tick)
        return append_order_journal(
            self.sim.wal,role=role,identity=identity,
            scope=detail["scope"],symbol=detail["symbol"],
            client_id=detail["client_id"],expected_uid=self.uid)
    def stop(self):
        return self.sim.ns["place_stop_write"](
            self.sim.venue,STOP,SCOPE,sleep=lambda secs:None)

    def test_stop_contention_exhausts_under_lease_without_post(self):
        with writer_fence(self.sim.wal,expected_uid=self.uid):
            with self.assertRaisesRegex(
                    PreSendRefused,"PROTECTIVE_WAL_DEADLINE_NO_POST_OWNER_REVIEW"):
                self.stop()
        self.assertEqual(self.sim.venue.post_calls,[])
        self.assertEqual(self.sim.venue.lease,0)
        self.assertFalse(self.sim.venue.stop_open)
        self.assertEqual(len(self.sleeps),3)

    def test_tp_contention_exhausts_under_lease_without_post(self):
        with writer_fence(self.sim.wal,expected_uid=self.uid):
            with self.assertRaisesRegex(
                    PreSendRefused,"PROTECTIVE_WAL_DEADLINE_NO_POST_OWNER_REVIEW"):
                self.sim.ns["one_write"](self.sim.venue,"TP1",TP,SCOPE)
        self.assertEqual(self.sim.venue.post_calls,[])
        self.assertEqual(self.sim.venue.lease,0)

    def test_post_uncertainty_is_not_retried_by_pre_send_wait(self):
        self.sim.venue.post_error=TimeoutError("OFFLINE_STOP_UNKNOWN")
        with self.assertRaisesRegex(Blocked,"UNKNOWN_WRITE_RESULT_DO_NOT_RETRY_STOP"):
            self.stop()
        self.assertEqual(len(self.sim.venue.post_calls),1)
        self.assertEqual(self.sim.venue.lease,0)
        self.assertEqual(len(list(self.sim.wal.glob("STOP_*.json"))),1)
        self.assertEqual(self.sleeps,[])

    def test_duplicate_wal_still_never_posts_twice(self):
        self.stop()
        with self.assertRaisesRegex(PreSendRefused,"WAL_DUPLICATE_NO_WRITE"):
            self.stop()
        self.assertEqual(len(self.sim.venue.post_calls),1)
        self.assertEqual(self.sim.venue.lease,0)

    def test_deadline_failure_can_record_alarm_but_not_claim_stop(self):
        with writer_fence(self.sim.wal,expected_uid=self.uid):
            with self.assertRaises(PreSendRefused):
                self.stop()
            self.assertEqual(publish_alarm_latch(
                self.sim.wal,scope=SCOPE,reason="OFFLINE_STOP_TIMEOUT",
                expected_uid=self.uid),"LATCHED")
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_EMERGENCY_LATCH"):
            self.sim.ns["startup_reconcile"]()
        self.assertEqual(self.sim.venue.post_calls,[])
        self.assertFalse(self.sim.venue.stop_open)

    def test_uncontended_wal_stop_uses_original_owner_lease(self):
        self.stop()
        self.assertEqual(len(self.sim.venue.post_calls),1)
        self.assertEqual(self.sim.venue.lease,0)
        self.assertEqual(len(list(self.sim.wal.glob("STOP_*.json"))),1)

if __name__=="__main__":
    unittest.main()
