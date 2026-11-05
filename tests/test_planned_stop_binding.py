"""Exact planned-STOP binding tests using copied pure guard source."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import stop_ambiguity_guard as g

SYMBOL="AINUSDT"
CID="OFFLINE-OWNER-STOP"
PLAN={
 "clientAlgoId":CID,
 "symbol":SYMBOL,
 "side":"SELL",
 "triggerPrice":"0.31461",
 "workingType":"CONTRACT_PRICE",
 "positionSide":"BOTH",
}
STOP=dict(PLAN,algoStatus="NEW",orderType="STOP_MARKET",closePosition="true")

def evidence():
    t=1_000_000
    row=deepcopy(STOP)
    return {
        "symbol":SYMBOL,
        "stop_id":CID,
        "tp_ids":[],
        "errors":[],
        "now_ms":t,
        "account":({"positions":[
            {"symbol":SYMBOL,"positionSide":"BOTH","positionAmt":"79"}
        ]},t-100),
        "open_algo":([row],t-100),
        "open_orders":([],t-100),
        "mark":({"markPrice":"0.32000"},t-100),
        "own_stop":("FOUND",deepcopy(row)),
    }

class PlannedStopBindingTests(unittest.TestCase):
    def test_exact_owner_stop_is_protected_at_read(self):
        self.assertEqual(g.classify(evidence(),PLAN),
                         ("PROTECTED","OWN_STOP_ACTIVE_PROTECTIVE"))

    def test_different_stop_trigger_is_ambiguous_not_protected(self):
        x=evidence()
        x["open_algo"][0][0]["triggerPrice"]="0.31462"
        x["own_stop"]=("FOUND",deepcopy(x["open_algo"][0][0]))
        state,why=g.classify(x,PLAN)
        self.assertEqual(state,"AMBIGUOUS")
        self.assertIn("OWN_STOP_NOT_PLANNED:triggerPrice",why)

    def test_wrong_working_type_is_ambiguous(self):
        x=evidence()
        x["open_algo"][0][0]["workingType"]="MARK_PRICE"
        x["own_stop"]=("FOUND",deepcopy(x["open_algo"][0][0]))
        state,why=g.classify(x,PLAN)
        self.assertEqual(state,"AMBIGUOUS")
        self.assertIn("OWN_STOP_NOT_PLANNED:workingType",why)

    def test_stale_read_cannot_certify_stop(self):
        x=evidence()
        x["mark"]=({"markPrice":"0.32"},x["now_ms"]-5_001)
        self.assertEqual(g.classify(x,PLAN),
                         ("AMBIGUOUS","EVIDENCE_STALE_OR_INCOMPLETE"))

    def test_stop_query_timeout_cannot_certify_stop(self):
        x=evidence()
        x["errors"]=["own_stop:TimeoutError"]
        state,why=g.classify(x,PLAN)
        self.assertEqual(state,"AMBIGUOUS")
        self.assertIn("READ_FAILED",why)

    def test_own_stop_missing_with_open_position_is_unprotected(self):
        x=evidence()
        x["open_algo"]=([],x["now_ms"]-100)
        x["own_stop"]=("NOT_FOUND",None)
        self.assertEqual(g.classify(x,PLAN),
                         ("UNPROTECTED_PROVEN","POSITION_OPEN_NO_STOP_DEFINITIVE"))

    def test_foreign_order_makes_stop_ownership_ambiguous(self):
        x=evidence()
        x["open_orders"]=([
            {"symbol":SYMBOL,"clientOrderId":"FOREIGN-ORDER"}
        ],x["now_ms"]-100)
        self.assertEqual(g.classify(x,PLAN),
                         ("AMBIGUOUS","FOREIGN_ORDERS_ON_SYMBOL"))

    def test_signed_flat_with_open_stop_requires_cleanup(self):
        x=evidence()
        x["account"]=({"positions":[
            {"symbol":SYMBOL,"positionSide":"BOTH","positionAmt":"0"}
        ]},x["now_ms"]-100)
        self.assertEqual(g.classify(x,PLAN),
                         ("FLAT_EXITS_OPEN","SIGNED_FLAT_OWN_EXITS_NEED_CLEANUP"))

if __name__=="__main__":
    unittest.main()
