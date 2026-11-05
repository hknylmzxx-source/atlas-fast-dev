"""Offline startup diagnostic and independent alarm latch regression tests."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emergency_latch_candidate import publish_alarm_latch
from emergency_restart_diagnostic_candidate import (
    StartupDiagnosticBlocked,verify_restart_diagnostic)

class RestartGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix="atlas-restart-gate-")
        self.root=Path(self.temp.name)
        self.root.chmod(0o700)
        self.uid=os.getuid()
    def tearDown(self):
        self.temp.cleanup()
    def check(self,legacy):
        return verify_restart_diagnostic(
            self.root,legacy_gate=legacy,expected_uid=self.uid)
    def latch(self):
        return publish_alarm_latch(self.root,scope="OFFLINE_SCOPE",
                                   reason="STOP_UNCERTAIN",expected_uid=self.uid)

    def test_unlatched_and_legacy_clear_remains_non_authorizing(self):
        rep=self.check(lambda:True)
        self.assertEqual(rep["status"],"DIAGNOSTIC_CLEAR_AT_READ_ONLY")
        self.assertIs(rep["entry_authorized"],False)
        self.assertIs(rep["atomic_release_verified"],False)

    def test_existing_latch_prevents_legacy_gate_call(self):
        self.latch()
        calls=[]
        with self.assertRaisesRegex(StartupDiagnosticBlocked,"LATCH_PRESENT"):
            self.check(lambda:calls.append("called") or True)
        self.assertEqual(calls,[])

    def test_latch_during_legacy_gate_prevents_clear(self):
        def gate():
            self.latch()
            return True
        with self.assertRaisesRegex(StartupDiagnosticBlocked,"LATCH_APPEARED"):
            self.check(gate)

    def test_legacy_refusal_stays_blocked(self):
        with self.assertRaisesRegex(StartupDiagnosticBlocked,"NOT_TRUE"):
            self.check(lambda:False)

    def test_legacy_exception_is_fail_closed(self):
        def gate():
            raise ValueError("MOCK_BAD_LEDGER")
        with self.assertRaisesRegex(StartupDiagnosticBlocked,"GATE_BLOCKED"):
            self.check(gate)

    def test_missing_legacy_callback_refused(self):
        with self.assertRaisesRegex(StartupDiagnosticBlocked,"LEGACY_GATE_MISSING"):
            self.check(None)

    def test_bad_latch_row_still_blocks_by_filename(self):
        path=self.root/"emergency_latch_OFFLINE_SCOPE.json"
        path.write_text("{corrupt")
        path.chmod(0o600)
        with self.assertRaisesRegex(StartupDiagnosticBlocked,"LATCH_PRESENT"):
            self.check(lambda:True)

    def test_symlink_latch_name_still_blocks(self):
        target=self.root/"target.txt"
        target.write_text("untrusted")
        (self.root/"emergency_latch_OFFLINE_SCOPE.json").symlink_to(target)
        with self.assertRaisesRegex(StartupDiagnosticBlocked,"LATCH_PRESENT"):
            self.check(lambda:True)

    def test_untrusted_dir_fails_closed(self):
        self.root.chmod(0o755)
        with self.assertRaisesRegex(StartupDiagnosticBlocked,"LATCH_PRESENT"):
            self.check(lambda:True)

if __name__=="__main__":
    unittest.main()
