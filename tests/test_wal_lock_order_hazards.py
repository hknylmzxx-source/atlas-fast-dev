"""ATLAS FAST offline lock-order hazard regression probes.

Do not wire experimental advisory fence into live Owner execution.
Tests use disposable directories and genuine local POSIX flock only.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wal_writer_fence_candidate import writer_fence,WalFenceBlocked
from wal_owner_write_adapter_candidate import (
    append_incident,append_order_journal,append_reservation,
    resolve_zero_reservation_offline,
)

SCOPE="OFFLINE_SCOPE"
SYMBOL="AINUSDT"
CLIENT="OFFLINE_CID"

class LockOrderHazardTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix="atlas-wal-lock-order-")
        self.root=Path(self.temp.name)
        self.root.chmod(0o700)
        self.uid=os.getuid()
        append_reservation(self.root,scope=SCOPE,symbol=SYMBOL,
                           client_id=CLIENT,expected_uid=self.uid)

    def tearDown(self):
        self.temp.cleanup()

    def incident(self):
        return append_incident(self.root,scope=SCOPE,
                               reason="OFFLINE_STOP_UNCERTAIN",
                               expected_uid=self.uid)

    def test_held_resolver_fence_blocks_alarm_writer(self):
        """Known design blocker: do not wire a lock that loses alarms."""
        with writer_fence(self.root,expected_uid=self.uid):
            with self.assertRaisesRegex(WalFenceBlocked,"WAL_FENCE_BUSY"):
                self.incident()
        self.assertFalse((self.root/"incident_OFFLINE_SCOPE.json").exists())

    def test_emergency_stop_writer_also_refused_under_contended_fence(self):
        with writer_fence(self.root,expected_uid=self.uid):
            with self.assertRaisesRegex(WalFenceBlocked,"WAL_FENCE_BUSY"):
                append_order_journal(self.root,role="STOP",identity="synthetic",
                    scope=SCOPE,symbol=SYMBOL,client_id=CLIENT,
                    expected_uid=self.uid)
        self.assertFalse((self.root/"STOP_synthetic.json").exists())

    def test_emergency_tp_writer_also_refused_under_contended_fence(self):
        with writer_fence(self.root,expected_uid=self.uid):
            with self.assertRaisesRegex(WalFenceBlocked,"WAL_FENCE_BUSY"):
                append_order_journal(self.root,role="TP1",identity="synthetic",
                    scope=SCOPE,symbol=SYMBOL,client_id=CLIENT,
                    expected_uid=self.uid)

    def test_no_marker_if_incident_was_written_first(self):
        self.incident()
        with self.assertRaisesRegex(WalFenceBlocked,"SCOPE_HAS_OTHER_WAL"):
            resolve_zero_reservation_offline(
                self.root,scope=SCOPE,symbol=SYMBOL,client_id=CLIENT,
                proof=True,expected_uid=self.uid)
        self.assertFalse((self.root/"resolved_OFFLINE_SCOPE.json").exists())

    def test_marker_prevents_late_incident_but_must_not_silence_external_alarm(self):
        """Known design hazard: incident append fails if marker committed."""
        resolve_zero_reservation_offline(
            self.root,scope=SCOPE,symbol=SYMBOL,client_id=CLIENT,
            proof=True,expected_uid=self.uid)
        with self.assertRaisesRegex(WalFenceBlocked,"SCOPE_ALREADY_RESOLVED_NO_WRITE"):
            self.incident()
        self.assertFalse((self.root/"incident_OFFLINE_SCOPE.json").exists())

    def test_g54_executor_wires_adapter_but_not_raw_fence(self):
        """FAZ2: G54 owner'ı adapter'a bağladı; ham fence modülü doğrudan import edilmez.
        Bu bir geliştirme kopyası gerçeğidir, canlı entegrasyon kanıtı değildir."""
        import ast
        tree=ast.parse((Path(__file__).resolve().parents[1]/
                        "owner_single_execution.py").read_text())
        imports=[n for n in ast.walk(tree)
                 if isinstance(n,(ast.Import,ast.ImportFrom))]
        names=[]
        for n in imports:
            if isinstance(n,ast.ImportFrom):
                names.append(n.module)
            else:
                names.extend(alias.name for alias in n.names)
        self.assertNotIn("wal_writer_fence_candidate",names)
        self.assertIn("wal_owner_write_adapter_candidate",names)

if __name__=="__main__":
    unittest.main()
