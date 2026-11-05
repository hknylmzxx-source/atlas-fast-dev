"""Offline simulation: filled ENTRY followed by unavailable protective STOP.

Selected Owner write functions and restart coverage run with fake venue and
temporary owner-private WAL. No real Binance transport or active SDK imports.
"""
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
from test_safety_chain_integration import (
    Integration,Blocked,PreSendRefused,SCOPE,SYM,ENTRY,STOP)
from wal_writer_fence_candidate import writer_fence
from wal_owner_write_adapter_candidate import append_order_journal
from protective_wal_deadline_candidate import append_protective_bounded
from emergency_latch_candidate import publish_alarm_latch

class FilledEntryUnprotectedStopTests(unittest.TestCase):
    def setUp(self):
        self.sim=Integration()
        self.sim.reserve()
        self.uid=os.getuid()
        self.now=0.0
        self.sim.ns["journal"]=self.journal
        # One synthetic ENTRY POST, then an already-filled exposure.
        self.sim.ns["one_write"](self.sim.venue,"ENTRY",ENTRY,SCOPE,
                                  owner_approved_at=100)
        self.sim.venue.position="79"
        self.assertEqual(len(self.sim.venue.post_calls),1)
        self.assertFalse(self.sim.venue.stop_open)
    def tearDown(self):
        self.sim.close()
    def tick(self,delta):
        self.now+=delta
    def journal(self,role,identity,detail):
        if role=="STOP":
            return append_protective_bounded(
                self.sim.wal,role=role,identity=identity,
                scope=detail["scope"],symbol=detail["symbol"],
                client_id=detail["client_id"],expected_uid=self.uid,
                timeout_s=0.03,poll_s=0.01,clock=lambda:self.now,
                sleep=self.tick)
        return append_order_journal(
            self.sim.wal,role=role,identity=identity,
            scope=detail["scope"],symbol=detail["symbol"],
            client_id=detail["client_id"],expected_uid=self.uid)
    def stop(self):
        return self.sim.ns["place_stop_write"](
            self.sim.venue,STOP,SCOPE,sleep=lambda _:None)
    def fail_stop_with_fence(self):
        with writer_fence(self.sim.wal,expected_uid=self.uid):
            with self.assertRaisesRegex(
                    PreSendRefused,"PROTECTIVE_WAL_DEADLINE_NO_POST_OWNER_REVIEW"):
                self.stop()
    def alarm(self):
        return publish_alarm_latch(
            self.sim.wal,scope=SCOPE,reason="FILLED_ENTRY_STOP_UNAVAILABLE",
            expected_uid=self.uid)

    def test_filled_entry_then_stop_deadline_is_unprotected_and_no_retry(self):
        self.fail_stop_with_fence()
        self.assertEqual(len(self.sim.venue.post_calls),1)
        self.assertEqual(self.sim.venue.lease,0)
        self.assertFalse(self.sim.venue.stop_open)
        self.assertFalse(list(self.sim.wal.glob("STOP_*.json")))
        report,events=self.sim.gate_report()
        self.assertEqual(report["protection"][SCOPE],
                         "UNPROTECTED_NO_STOP_SENT")
        self.assertFalse(report["entries_allowed"])
        self.assertTrue(any("MANUAL_CLOSE_REQUIRED" in e for e in events))

    def test_filled_entry_stop_timeout_alarm_blocks_future_startup(self):
        self.fail_stop_with_fence()
        self.assertEqual(self.alarm(),"LATCHED")
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_EMERGENCY_LATCH"):
            self.sim.ns["startup_reconcile"]()
        self.assertFalse(self.sim.gate_report()[0]["entries_allowed"])

    def test_alarm_does_not_falsely_mark_position_flat(self):
        self.fail_stop_with_fence()
        self.alarm()
        self.assertEqual(self.sim.venue.position,"79")
        self.assertFalse(self.sim.venue.stop_open)
        self.assertEqual(len(self.sim.venue.post_calls),1)
        self.assertFalse((self.sim.wal/("resolved_"+SCOPE+".json")).exists())

    def test_new_stop_attempt_after_timeout_is_not_implicit(self):
        self.fail_stop_with_fence()
        self.assertEqual(len(self.sim.venue.post_calls),1)
        self.assertFalse(list(self.sim.wal.glob("STOP_*.json")))
        self.assertEqual(self.sim.venue.lease,0)

    def test_ledger_still_open_and_restart_blocked_without_alarm(self):
        self.fail_stop_with_fence()
        self.assertTrue(self.sim.gate.open)
        report,_=self.sim.gate_report()
        self.assertEqual(report["policy"],"KEPT_ENTRIES_BLOCKED")
        self.assertFalse(report["entries_allowed"])
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_WAL_SCOPE"):
            self.sim.ns["startup_reconcile"]()

if __name__=="__main__":
    unittest.main()
