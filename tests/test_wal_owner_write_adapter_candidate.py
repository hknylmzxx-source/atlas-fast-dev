"""Offline WAL Owner adapter tests. No actual Owner SDK or Binance access."""
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wal_owner_write_adapter_candidate import (
    append_order_journal,append_reservation,append_incident,
    resolve_zero_reservation_offline)
from wal_writer_fence_candidate import WalFenceBlocked

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix="offline-adapter-")
        self.dir=Path(self.temp.name)
        self.dir.chmod(0o700)
        self.uid=os.getuid()
        self.scope="OFFLINE_SCOPE"
    def tearDown(self):
        self.temp.cleanup()
    def reserve(self):
        return append_reservation(self.dir,scope=self.scope,symbol="AINUSDT",
                                  client_id="OFFLINE_ID",expected_uid=self.uid)
    def resolve(self):
        return resolve_zero_reservation_offline(self.dir,scope=self.scope,
            symbol="AINUSDT",client_id="OFFLINE_ID",
            proof=True,expected_uid=self.uid)
    def order(self,role="ENTRY",identity="id"):
        return append_order_journal(self.dir,role=role,identity=identity,
            scope=self.scope,symbol="AINUSDT",
            client_id="OFFLINE_ID",expected_uid=self.uid)

    def test_reservation_contract(self):
        self.reserve()
        row=json.loads((self.dir/"POLICY_RESERVE_OFFLINE_SCOPE.json").read_text())
        self.assertEqual(row["state"],"RESERVATION_PENDING")
        self.assertEqual(row["detail"]["client_id"],"OFFLINE_ID")
    def test_order_contract(self):
        self.reserve()
        self.order()
        row=json.loads((self.dir/"ENTRY_id.json").read_text())
        self.assertEqual(row["state"],"WRITE_OUTCOME_UNKNOWN_NO_RETRY")
        self.assertEqual(row["detail"]["scope"],self.scope)
    def test_entry_blocks_resolution(self):
        self.reserve()
        self.order()
        with self.assertRaisesRegex(WalFenceBlocked,"SCOPE_HAS_OTHER_WAL"):
            self.resolve()
    def test_incident_blocks_resolution(self):
        self.reserve()
        append_incident(self.dir,scope=self.scope,
            reason="OFFLINE_ALARM",expected_uid=self.uid)
        with self.assertRaisesRegex(WalFenceBlocked,"SCOPE_HAS_OTHER_WAL"):
            self.resolve()
    def test_incident_contract(self):
        append_incident(self.dir,scope=self.scope,
            reason="OFFLINE_ALARM",expected_uid=self.uid)
        row=json.loads((self.dir/"incident_OFFLINE_SCOPE.json").read_text())
        self.assertFalse(row["new_entry_permitted"])
        self.assertFalse(row["retry_permitted"])
    def test_late_entry_rejected(self):
        self.reserve()
        self.resolve()
        with self.assertRaisesRegex(WalFenceBlocked,"SCOPE_ALREADY_RESOLVED_NO_WRITE"):
            self.order()
    def test_duplicate_incident_refused(self):
        append_incident(self.dir,scope=self.scope,
            reason="FIRST",expected_uid=self.uid)
        with self.assertRaisesRegex(WalFenceBlocked,"WAL_DUPLICATE_NO_WRITE"):
            append_incident(self.dir,scope=self.scope,
                reason="SECOND",expected_uid=self.uid)
    def test_emergency_order_blocks_resolution(self):
        self.reserve()
        self.order("EMERGENCY","e1")
        with self.assertRaisesRegex(WalFenceBlocked,"SCOPE_HAS_OTHER_WAL"):
            self.resolve()
    def test_bad_role_refused(self):
        with self.assertRaisesRegex(WalFenceBlocked,"OWNER_WAL_ROLE_INVALID"):
            self.order("BAD","x")

if __name__=="__main__":
    unittest.main()
