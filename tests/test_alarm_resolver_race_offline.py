"""ATLAS FAST offline alarm-versus-resolution concurrency checks.

These prove a narrow invariant only: the diagnostic NO-ENTRY gate refuses a
persistent emergency latch. They do NOT establish an atomic live entry barrier.
"""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emergency_latch_candidate import (
    EmergencyLatchBlocked,publish_alarm_latch,require_no_emergency_latch)
from emergency_restart_diagnostic_candidate import (
    StartupDiagnosticBlocked,verify_restart_diagnostic)
from wal_owner_write_adapter_candidate import (
    append_reservation,resolve_zero_reservation_offline)

SCOPE="OFFLINE_SCOPE"
SYM="AINUSDT"
CID="OFFLINE_CID"

class AlarmRaceOfflineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(prefix="atlas-alarm-resolve-race-")
        self.root=Path(self.tmp.name)
        self.root.chmod(0o700)
        self.uid=os.getuid()
        append_reservation(self.root,scope=SCOPE,symbol=SYM,
                           client_id=CID,expected_uid=self.uid)
    def tearDown(self):
        self.tmp.cleanup()
    def alarm(self):
        return publish_alarm_latch(self.root,scope=SCOPE,
                    reason="STOP_UNCERTAIN",expected_uid=self.uid)
    def resolve(self):
        return resolve_zero_reservation_offline(self.root,scope=SCOPE,
                    symbol=SYM,client_id=CID,proof=True,expected_uid=self.uid)
    def gate(self):
        return verify_restart_diagnostic(
                    self.root,legacy_gate=lambda:True,
                    expected_uid=self.uid)
    def assert_blocked(self):
        with self.assertRaisesRegex(StartupDiagnosticBlocked,"EMERGENCY_LATCH"):
            self.gate()

    def test_alarm_before_resolve_prevents_marker(self):
        self.alarm()
        with self.assertRaises(Exception):
            self.resolve()
        self.assertFalse((self.root/("resolved_"+SCOPE+".json")).exists())
        self.assert_blocked()

    def test_alarm_after_marker_still_blocks_restart(self):
        self.resolve()
        self.alarm()
        self.assertTrue((self.root/("resolved_"+SCOPE+".json")).exists())
        self.assert_blocked()

    def test_legacy_gate_writes_alarm_during_check(self):
        def gate():
            self.alarm()
            return True
        with self.assertRaisesRegex(StartupDiagnosticBlocked,"LATCH_APPEARED"):
            verify_restart_diagnostic(self.root,legacy_gate=gate,
                                      expected_uid=self.uid)

    def test_independent_alarm_and_marker_workers_do_not_authorize(self):
        source=str(Path(__file__).resolve().parents[1])
        alarm_code=(
          "import sys;"
          "sys.path.insert(0,sys.argv[2]);"
          "from emergency_latch_candidate import publish_alarm_latch;"
          "publish_alarm_latch(sys.argv[1],scope='OFFLINE_SCOPE',"
          "reason='STOP_UNKNOWN',expected_uid=int(sys.argv[3]))")
        resolve_code=(
          "import sys;"
          "sys.path.insert(0,sys.argv[2]);"
          "from wal_owner_write_adapter_candidate import resolve_zero_reservation_offline;"
          "resolve_zero_reservation_offline(sys.argv[1],scope='OFFLINE_SCOPE',"
          "symbol='AINUSDT',client_id='OFFLINE_CID',"
          "proof=True,expected_uid=int(sys.argv[3]))")
        args=[str(self.root),source,str(self.uid)]
        p1=subprocess.Popen([sys.executable,"-B","-c",alarm_code,*args],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        p2=subprocess.Popen([sys.executable,"-B","-c",resolve_code,*args],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        p1.communicate(timeout=7)
        p2.communicate(timeout=7)
        self.assertTrue((self.root/("emergency_latch_"+SCOPE+".json")).is_file())
        self.assert_blocked()

    def test_repeated_alarm_does_not_clear_existing_marker_or_latch(self):
        self.resolve()
        self.assertEqual(self.alarm(),"LATCHED")
        self.assertEqual(self.alarm(),"ALREADY_LATCHED")
        self.assert_blocked()

    def test_bad_alarm_content_still_blocks(self):
        path=self.root/("emergency_latch_"+SCOPE+".json")
        path.write_bytes(b"\x00not-json")
        path.chmod(0o600)
        self.assert_blocked()

    def test_diagnostic_clear_is_never_entry_authorization(self):
        report=self.gate()
        self.assertIs(report["entry_authorized"],False)
        self.assertIs(report["atomic_release_verified"],False)
        self.alarm()
        self.assert_blocked()

    def test_alarm_does_not_block_by_shared_wal_flock(self):
        from wal_writer_fence_candidate import writer_fence
        with writer_fence(self.root,expected_uid=self.uid):
            self.assertEqual(self.alarm(),"LATCHED")
        self.assert_blocked()

if __name__=="__main__":
    unittest.main()
