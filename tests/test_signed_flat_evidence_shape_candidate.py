"""Synthetic snapshot shape checks; no Binance calls or entry release."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from signed_flat_evidence_shape_candidate import (
    inspect_synthetic_flat_observation,EvidenceShapeBlocked)

NOW=100000
S="AINUSDT"

def valid():
    return {"symbol":S,"captured_at_ms":NOW-100,
            "read_request_ids":{"account":"a1","normal_orders":"b2",
                                "algo_orders":"c3"},
            "positions":[{"symbol":S,"positionAmt":"0"}],
            "normal_orders":[],"algo_orders":[]}

class SignedFlatEvidenceShapeTests(unittest.TestCase):
    def check(self,observation):
        return inspect_synthetic_flat_observation(
            observation=observation,symbol=S,now_ms=NOW)
    def rejected(self,obs,reason):
        result=self.check(obs)
        self.assertIn(reason,result["reasons"])
        self.assertFalse(result["schema_flat_candidate"])
        self.assertFalse(result["authenticated_signed_get_verified"])
        self.assertFalse(result["entry_authorized"])
        self.assertFalse(result["may_clear_latch"])
    def test_well_formed_claim_does_not_release(self):
        r=self.check(valid())
        self.assertTrue(r["schema_flat_candidate"])
        self.assertFalse(r["authenticated_signed_get_verified"])
        self.assertFalse(r["position_flat_independently_verified"])
        self.assertFalse(r["entry_authorized"])
        self.assertFalse(r["may_clear_latch"])
    def test_stale_snapshot_rejected(self):
        x=valid();x["captured_at_ms"]=NOW-6000
        self.rejected(x,"OBSERVATION_STALE_OR_INVALID_CLOCK")
    def test_future_snapshot_rejected(self):
        x=valid();x["captured_at_ms"]=NOW+1
        self.rejected(x,"OBSERVATION_STALE_OR_INVALID_CLOCK")
    def test_wrong_symbol_rejected(self):
        x=valid();x["symbol"]="OTHERUSDT"
        self.rejected(x,"SYMBOL_MISMATCH")
    def test_missing_orders_rejected(self):
        x=valid();del x["algo_orders"]
        self.rejected(x,"ALGO_ORDERS_MISSING")
    def test_live_normal_order_rejected(self):
        x=valid();x["normal_orders"]=[{"clientOrderId":"synthetic"}]
        self.rejected(x,"NORMAL_ORDERS_NOT_EMPTY")
    def test_live_algo_stop_rejected(self):
        x=valid();x["algo_orders"]=[{"clientAlgoId":"synthetic"}]
        self.rejected(x,"ALGO_ORDERS_NOT_EMPTY")
    def test_nonzero_position_rejected(self):
        x=valid();x["positions"][0]["positionAmt"]="79"
        self.rejected(x,"NONZERO_POSITION")
    def test_nan_position_rejected(self):
        x=valid();x["positions"][0]["positionAmt"]="NaN"
        self.rejected(x,"POSITION_AMOUNT_INVALID")
    def test_empty_position_collection_rejected(self):
        x=valid();x["positions"]=[]
        self.rejected(x,"POSITIONS_MISSING")
    def test_duplicate_read_ids_rejected(self):
        x=valid();x["read_request_ids"]["algo_orders"]="a1"
        self.rejected(x,"SIGNED_READ_PROVENANCE_NOT_ESTABLISHED")
    def test_missing_read_ids_rejected(self):
        x=valid();del x["read_request_ids"]
        self.rejected(x,"SIGNED_READ_PROVENANCE_NOT_ESTABLISHED")
    def test_boolean_timestamp_rejected(self):
        x=valid();x["captured_at_ms"]=True
        self.rejected(x,"OBSERVATION_STALE_OR_INVALID_CLOCK")
    def test_bad_context_raises(self):
        with self.assertRaisesRegex(EvidenceShapeBlocked,"REVIEW_CONTEXT"):
            inspect_synthetic_flat_observation(
                observation=valid(),symbol="",now_ms=NOW)
    def test_untrusted_input_never_mutated(self):
        x=valid();before=copy.deepcopy(x)
        self.check(x)
        self.assertEqual(x,before)

if __name__=="__main__":
    unittest.main()
