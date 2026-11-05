"""Synthetic Owner release dependency checks; never create real orders."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from owner_live_dependency_review_offline import (
 REQUIRED,inspect_bridge_state,inspect_status_file,ReadinessInputBlocked)

NOW=datetime(2026,11,5,14,0,tzinfo=timezone.utc)

def payload():
    x={"generated_at":NOW.isoformat(),"mode":"DRY_RUN_ONLY",
       "orders_sent":False}
    x.update({name:False for name in REQUIRED})
    x["owner_sdk_rpc_connected"]=True
    x["owner_executor_installed_verified"]=True
    return x

class LiveDependencyOfflineTests(unittest.TestCase):
    def read(self,source):
        return inspect_bridge_state(source,observed_at=NOW)
    def test_known_rpc_is_not_live_sender(self):
        r=self.read(payload())
        self.assertIn("LIVE_SENDER_NOT_CONNECTED",r["missing_controls"])
        self.assertIn("EXCHANGE_STOP_COVERAGE_NOT_VERIFIED",r["missing_controls"])
        self.assertNotIn("OWNER_RPC_NOT_CONNECTED",r["missing_controls"])
        self.assertFalse(r["live_order_authorized"])
    def test_even_all_claimed_flags_cannot_release(self):
        x=payload();x.update({name:True for name in REQUIRED})
        r=self.read(x)
        self.assertTrue(r["all_claimed_controls_present"])
        self.assertFalse(r["owner_evidence_independently_authenticated"])
        self.assertFalse(r["exchange_stop_independently_verified"])
        self.assertFalse(r["deployment_permitted"])
    def test_stale_bridge_refused(self):
        x=payload();x["generated_at"]=(NOW-timedelta(seconds=120)).isoformat()
        self.assertIn("BRIDGE_STATUS_STALE_OR_FUTURE",self.read(x)["missing_controls"])
    def test_future_bridge_refused(self):
        x=payload();x["generated_at"]=(NOW+timedelta(seconds=1)).isoformat()
        self.assertIn("BRIDGE_STATUS_STALE_OR_FUTURE",self.read(x)["missing_controls"])
    def test_human_owner_approval_not_substituted_by_phone_availability(self):
        x=payload();x["phone_live_order_release_enabled"]=True
        r=self.read(x)
        self.assertIn("OWNER_EXACT_APPROVAL_NOT_VERIFIED",r["missing_controls"])
        self.assertFalse(r["live_order_authorized"])
    def test_claimed_stop_watch_without_stop_proof_is_blocked(self):
        x=payload();x["continuous_stop_watch_connected"]=True
        self.assertIn("EXCHANGE_STOP_COVERAGE_NOT_VERIFIED",self.read(x)["missing_controls"])
    def test_claimed_flatten_without_stop_watch_is_blocked(self):
        x=payload();x["signed_emergency_flatten_connected"]=True
        self.assertIn("CONTINUOUS_STOP_WATCH_NOT_CONNECTED",self.read(x)["missing_controls"])
    def test_status_file_is_read_only(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/"bridge.json"
            p.write_text(json.dumps(payload()))
            before=p.read_bytes()
            result=inspect_status_file(p,observed_at=NOW)
            self.assertFalse(result["live_order_authorized"])
            self.assertEqual(p.read_bytes(),before)
    def test_symlink_is_refused(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/"original.json";p.write_text(json.dumps(payload()))
            q=Path(tmp)/"link.json";q.symlink_to(p)
            with self.assertRaisesRegex(ReadinessInputBlocked,"UNTRUSTED_STATUS"):
                inspect_status_file(q,observed_at=NOW)
    def test_untrusted_boolean_values_cannot_pass(self):
        x=payload();x["human_trade_approval_verified"]=1
        self.assertIn("OWNER_EXACT_APPROVAL_NOT_VERIFIED",self.read(x)["missing_controls"])

if __name__=="__main__":
    unittest.main()
