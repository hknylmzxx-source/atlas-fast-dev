"""Synthetic bracketed observation temporal coherence regression tests."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bracketed_observation_coherence_candidate import assess_bracketed_observation
S="AINUSDT"
NOW=100000
def sample():
    def row(start,end,ident):
        return {"symbol":S,"start_ms":start,"end_ms":end,"request_id":ident}
    before=row(98000,98100,"req-a")
    before["positions"]=[{"symbol":S,"positionAmt":"0"}]
    normal=row(98200,98300,"req-b")
    normal["orders"]=[]
    algo=row(98400,98500,"req-c")
    algo["orders"]=[]
    after=row(98600,98700,"req-d")
    after["positions"]=[{"symbol":S,"positionAmt":"0"}]
    return {"account_before":before,"normal_orders":normal,
            "algo_orders":algo,"account_after":after}
class BracketedCoherenceTests(unittest.TestCase):
    def evaluate(self,reads):
        return assess_bracketed_observation(reads=reads,symbol=S,now_ms=NOW)
    def reject(self,reads,reason):
        value=self.evaluate(reads)
        self.assertIn(reason,value["reasons"])
        self.assertFalse(value["synthetic_read_sequence_consistent"])
        self.assertFalse(value["may_clear_latch"])
        self.assertFalse(value["entry_authorized"])
    def test_coherent_claim_still_cannot_authorize(self):
        value=self.evaluate(sample())
        self.assertTrue(value["synthetic_read_sequence_consistent"])
        self.assertFalse(value["authenticated_signed_binance_reads_verified"])
        self.assertFalse(value["atomic_exchange_snapshot_verified"])
        self.assertFalse(value["no_intervening_exchange_change_proven"])
        self.assertFalse(value["may_clear_latch"])
    def test_missing_final_account_read(self):
        x=sample();del x["account_after"]
        self.reject(x,"READ_SEQUENCE_INCOMPLETE")
    def test_initial_account_nonzero(self):
        x=sample();x["account_before"]["positions"][0]["positionAmt"]="3"
        self.reject(x,"ACCOUNT_BEFORE_NONZERO_POSITION")
    def test_final_account_nonzero(self):
        x=sample();x["account_after"]["positions"][0]["positionAmt"]="-2"
        self.reject(x,"ACCOUNT_AFTER_NONZERO_POSITION")
    def test_normal_orders_nonempty(self):
        x=sample();x["normal_orders"]["orders"]=[{"id":"simulated"}]
        self.reject(x,"NORMAL_ORDERS_ORDERS_NOT_EMPTY")
    def test_algo_orders_nonempty(self):
        x=sample();x["algo_orders"]["orders"]=[{"id":"simulated"}]
        self.reject(x,"ALGO_ORDERS_ORDERS_NOT_EMPTY")
    def test_stale_read_rejected(self):
        x=sample();x["account_before"]["start_ms"]=90000
        x["account_before"]["end_ms"]=90001
        self.reject(x,"ACCOUNT_BEFORE_TIME_INVALID")
    def test_future_read_rejected(self):
        x=sample();x["account_after"]["end_ms"]=NOW+1
        self.reject(x,"ACCOUNT_AFTER_TIME_INVALID")
    def test_out_of_order_read_rejected(self):
        x=sample();x["algo_orders"]["start_ms"]=98250
        self.reject(x,"READ_INTERVALS_OVERLAP_OR_OUT_OF_ORDER")
    def test_wide_window_rejected(self):
        x=sample();x["account_before"]["start_ms"]=96500
        x["account_before"]["end_ms"]=96600
        self.reject(x,"READ_WINDOW_TOO_WIDE")
    def test_duplicate_request_ids_rejected(self):
        x=sample();x["algo_orders"]["request_id"]="req-a"
        self.reject(x,"REQUEST_IDS_NOT_UNIQUE")
    def test_nan_or_ambiguous_position_rejected(self):
        x=sample();x["account_after"]["positions"][0]["positionAmt"]="NaN"
        self.reject(x,"ACCOUNT_AFTER_POSITION_INVALID")
    def test_symbol_mismatch_rejected(self):
        x=sample();x["normal_orders"]["symbol"]="OTHERUSDT"
        self.reject(x,"NORMAL_ORDERS_SYMBOL_MISMATCH")
    def test_bool_timestamp_rejected(self):
        x=sample();x["account_before"]["start_ms"]=True
        self.reject(x,"ACCOUNT_BEFORE_TIME_INVALID")
    def test_does_not_mutate_inputs(self):
        x=sample();y=copy.deepcopy(x);self.evaluate(x)
        self.assertEqual(x,y)
if __name__=="__main__":
    unittest.main()
