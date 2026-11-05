"""Offline unprotected exposure quarantine: persistence and restart contracts."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from unprotected_exposure_quarantine_candidate import (
    QuarantineNotDurable,quarantine_unprotected_exposure)
from emergency_latch_candidate import require_no_emergency_latch,EmergencyLatchBlocked
from wal_writer_fence_candidate import writer_fence
from test_safety_chain_integration import Integration,SCOPE,Blocked

class UnprotectedQuarantineTests(unittest.TestCase):
    def setUp(self):
        self.sim=Integration()
        self.sim.reserve()
        self.uid=os.getuid()
        self.sim.venue.position="79"
    def tearDown(self):
        self.sim.close()
    def quarantine(self,**kwargs):
        defaults={"scope":SCOPE,"exposure_state":"NONZERO",
                  "stop_state":"ABSENT","failure":"WAL_DEADLINE",
                  "expected_uid":self.uid}
        defaults.update(kwargs)
        return quarantine_unprotected_exposure(self.sim.wal,**defaults)
    def test_filled_unprotected_exposure_is_durable_and_blocked(self):
        result=self.quarantine()
        self.assertEqual(result["decision"],"NO_ENTRY_MANUAL_OWNER_REVIEW")
        self.assertTrue(result["new_latch_persisted"])
        self.assertFalse(result["entry_authorized"])
        self.assertFalse(result["stop_protected"])
        self.assertFalse(result["position_flat_verified"])
        self.assertFalse(result["automatic_retry_allowed"])
        self.assertEqual(self.sim.venue.position,"79")
        with self.assertRaisesRegex(Blocked,"EMERGENCY_LATCH"):
            self.sim.ns["startup_reconcile"]()
    def test_alarm_can_persist_while_wal_fence_busy(self):
        with writer_fence(self.sim.wal,expected_uid=self.uid):
            result=self.quarantine()
        self.assertTrue(result["new_latch_persisted"])
    def test_repeat_does_not_claim_new_latch_written(self):
        self.quarantine()
        result=self.quarantine()
        self.assertFalse(result["new_latch_persisted"])
        self.assertTrue(result["previous_latch_detected"])
        self.assertFalse(result["entry_authorized"])
    def test_corrupt_existing_latch_remains_no_entry(self):
        path=self.sim.wal/("emergency_latch_"+SCOPE+".json")
        path.write_text("{corrupt")
        path.chmod(0o600)
        result=self.quarantine()
        self.assertFalse(result["new_latch_persisted"])
        self.assertTrue(result["previous_latch_detected"])
        with self.assertRaisesRegex(Blocked,"EMERGENCY_LATCH"):
            self.sim.ns["startup_reconcile"]()
    def test_unknown_exposure_and_stop_are_quarantined(self):
        result=self.quarantine(exposure_state="UNKNOWN",stop_state="UNKNOWN",
                               failure="STOP_READBACK_UNKNOWN")
        self.assertFalse(result["position_flat_verified"])
    def test_refuse_flat_or_verified_stop_inputs(self):
        with self.assertRaisesRegex(QuarantineNotDurable,"EXPOSURE_NOT"):
            self.quarantine(exposure_state="FLAT")
        with self.assertRaisesRegex(QuarantineNotDurable,"STOP_NOT"):
            self.quarantine(stop_state="ACTIVE_SIGNED")
        self.assertFalse(list(self.sim.wal.glob("emergency_latch_*")))
    def test_invalid_failure_reason_refused(self):
        with self.assertRaisesRegex(QuarantineNotDurable,"FAILURE_REASON"):
            self.quarantine(failure="RETRY_POST")
        self.assertFalse(list(self.sim.wal.glob("emergency_latch_*")))
    def test_persistence_failure_requires_escalation(self):
        self.sim.wal.chmod(0o755)
        with self.assertRaisesRegex(QuarantineNotDurable,"PERSISTENCE_FAILED"):
            self.quarantine()
        self.sim.wal.chmod(0o700)
    def test_quarantine_is_not_protective_order(self):
        self.quarantine()
        self.assertEqual(self.sim.venue.post_calls,[])
        self.assertFalse(self.sim.venue.stop_open)
        self.assertEqual(self.sim.venue.position,"79")
        self.assertTrue(self.sim.gate.open)
        self.assertFalse(self.sim.gate_report()[0]["entries_allowed"])

if __name__=="__main__":
    unittest.main()
