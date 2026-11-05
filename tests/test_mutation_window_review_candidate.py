"""Synthetic write-watermark regression; never release owner safety gates."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mutation_window_review_candidate import assess_mutation_window
from test_bracketed_observation_coherence_candidate import sample,NOW,S

def watermark(revision,at):
    return {"symbol":S,"revision":revision,"observed_at_ms":at,
            "outstanding_write_count":0,"write_lease_held":False}

class MutationWindowTests(unittest.TestCase):
    def setUp(self):
        self.reads=sample()
        self.before=watermark(10,97900)
        self.after=watermark(10,98800)
    def check(self):
        return assess_mutation_window(before=self.before,after=self.after,
                     reads=self.reads,symbol=S,now_ms=NOW)
    def blocked(self,reason):
        result=self.check()
        self.assertIn(reason,result["reasons"])
        self.assertFalse(result["synthetic_mutation_window_consistent"])
        self.assertFalse(result["entry_authorized"])
        self.assertFalse(result["may_clear_latch"])
    def test_stable_synthetic_revision_does_not_authorize(self):
        r=self.check()
        self.assertTrue(r["synthetic_mutation_window_consistent"])
        self.assertFalse(r["source_mutation_sequence_authenticated"])
        self.assertFalse(r["unobserved_exchange_writes_excluded"])
        self.assertFalse(r["atomic_entry_gate_verified"])
        self.assertFalse(r["entry_authorized"])
    def test_intervening_write_revision_blocks(self):
        self.after["revision"]=11
        self.blocked("MUTATION_REVISION_CHANGED_DURING_READS")
    def test_before_lease_held_blocks(self):
        self.before["write_lease_held"]=True
        self.blocked("BEFORE_MUTATION_LEASE_NOT_CLEAR")
    def test_after_lease_held_blocks(self):
        self.after["write_lease_held"]=True
        self.blocked("AFTER_MUTATION_LEASE_NOT_CLEAR")
    def test_pending_write_blocks(self):
        self.after["outstanding_write_count"]=1
        self.blocked("AFTER_OUTSTANDING_WRITES_UNVERIFIED")
    def test_invalid_revision_blocks(self):
        self.before["revision"]=False
        self.blocked("BEFORE_REVISION_INVALID")
    def test_mismatched_symbol_blocks(self):
        self.after["symbol"]="OTHERUSDT"
        self.blocked("AFTER_WATERMARK_SYMBOL_MISMATCH")
    def test_early_after_watermark_blocks(self):
        self.after["observed_at_ms"]=98500
        self.blocked("WATERMARK_DOES_NOT_BRACKET_READS")
    def test_late_before_watermark_blocks(self):
        self.before["observed_at_ms"]=98100
        self.blocked("WATERMARK_DOES_NOT_BRACKET_READS")
    def test_nonzero_position_blocks_through_bracket(self):
        self.reads["account_after"]["positions"][0]["positionAmt"]="4"
        self.blocked("BRACKETED_READS_REJECTED")
    def test_open_algo_order_blocks_through_bracket(self):
        self.reads["algo_orders"]["orders"]=[{"id":"offline"}]
        self.blocked("BRACKETED_READS_REJECTED")
    def test_missing_watermark_blocks(self):
        self.before=None
        self.blocked("MUTATION_WATERMARK_MISSING")

if __name__=="__main__":
    unittest.main()
