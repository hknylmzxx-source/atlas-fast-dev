"""Offline-only emergency alarm durability tests. No Binance/SDK imports."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emergency_latch_candidate import (
    publish_alarm_latch,require_no_emergency_latch,EmergencyLatchBlocked)
from wal_writer_fence_candidate import writer_fence
from wal_owner_write_adapter_candidate import (
    append_reservation, resolve_zero_reservation_offline)

SCOPE="OFFLINE_SCOPE"

class EmergencyLatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(prefix="atlas-emergency-latch-")
        self.root=Path(self.tmp.name)
        self.root.chmod(0o700)
        self.uid=os.getuid()
    def tearDown(self):
        self.tmp.cleanup()
    def latch(self,reason="STOP_UNKNOWN"):
        return publish_alarm_latch(self.root,scope=SCOPE,reason=reason,
                                   expected_uid=self.uid)
    def check(self):
        return require_no_emergency_latch(self.root,expected_uid=self.uid)

    def test_latch_persists_without_other_lock(self):
        self.assertTrue(self.check())
        self.assertEqual(self.latch(),"LATCHED")
        row=json.loads((self.root/"emergency_latch_OFFLINE_SCOPE.json").read_text())
        self.assertIs(row["new_entry_permitted"],False)
        self.assertIs(row["retry_permitted"],False)
        with self.assertRaisesRegex(EmergencyLatchBlocked,"NO_ENTRY"):
            self.check()

    def test_wal_lock_busy_does_not_block_emergency_latch(self):
        with writer_fence(self.root,expected_uid=self.uid):
            self.assertEqual(self.latch(),"LATCHED")
        with self.assertRaisesRegex(EmergencyLatchBlocked,"NO_ENTRY"):
            self.check()

    def test_independent_process_publishes_latch_while_lock_held(self):
        code=(
            "import sys;"
            "sys.path.insert(0,sys.argv[2]);"
            "from emergency_latch_candidate import publish_alarm_latch;"
            "print(publish_alarm_latch(sys.argv[1],scope='OFFLINE_SCOPE',"
            "reason='STOP_UNCERTAIN',expected_uid=int(sys.argv[3])))"
        )
        with writer_fence(self.root,expected_uid=self.uid):
            p=subprocess.run([sys.executable,"-B","-c",code,
                str(self.root),str(Path(__file__).resolve().parents[1]),
                str(self.uid)],capture_output=True,text=True,timeout=5)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(p.stdout.strip(),"LATCHED")
        with self.assertRaisesRegex(EmergencyLatchBlocked,"NO_ENTRY"):
            self.check()

    def test_repeat_alarm_cannot_overwrite_first(self):
        self.latch("FIRST")
        self.assertEqual(self.latch("SECOND"),"ALREADY_LATCHED")
        r=json.loads((self.root/"emergency_latch_OFFLINE_SCOPE.json").read_text())
        self.assertEqual(r["reason"],"FIRST")

    def test_marker_does_not_supersede_alarm(self):
        append_reservation(self.root,scope=SCOPE,symbol="AINUSDT",
                           client_id="OFFLINE_CID",expected_uid=self.uid)
        self.latch()
        # Current isolated resolver is NOT integrated with emergency latches.
        # This test specifically documents why an independent startup gate is
        # mandatory before any such marker could be interpreted as release.
        with self.assertRaisesRegex(Exception,"SCOPE_HAS_OTHER_WAL"):
            resolve_zero_reservation_offline(self.root,scope=SCOPE,
                symbol="AINUSDT",client_id="OFFLINE_CID",proof=True,
                expected_uid=self.uid)
        with self.assertRaisesRegex(EmergencyLatchBlocked,"NO_ENTRY"):
            self.check()

    def test_reject_weak_directory_permissions(self):
        self.root.chmod(0o755)
        with self.assertRaisesRegex(EmergencyLatchBlocked,"UNTRUSTED"):
            self.latch()

    def test_reject_symlinked_directory(self):
        link=self.root.parent/(self.root.name+"-link")
        link.symlink_to(self.root,target_is_directory=True)
        try:
            with self.assertRaisesRegex(EmergencyLatchBlocked,"SYMLINK"):
                publish_alarm_latch(link,scope=SCOPE,reason="TEST",
                                    expected_uid=self.uid)
        finally:
            link.unlink()

    def test_reject_bad_scope(self):
        with self.assertRaisesRegex(EmergencyLatchBlocked,"SCOPE_INVALID"):
            publish_alarm_latch(self.root,scope="../escape",reason="TEST",
                                expected_uid=self.uid)

    def test_missing_directory_refused(self):
        with self.assertRaises(Exception):
            publish_alarm_latch(self.root/"nonexistent",scope=SCOPE,
                                reason="TEST",expected_uid=self.uid)

if __name__=="__main__":
    unittest.main()
