"""Restart exact-order proof regression tests (offline).

Source AST only, no network, no SDK instantiation, no live WAL and no POST.
"""
from __future__ import annotations

import ast
from decimal import Decimal
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"owner_single_execution.py"
tree=ast.parse(SOURCE.read_text())
functions=[n for n in tree.body if isinstance(n,ast.FunctionDef)
           and n.name in {"_restart_entry_fill","_restart_one"}]
assert len(functions)==2
compiled=compile(ast.fix_missing_locations(ast.Module(body=functions,type_ignores=[])),
                 str(SOURCE),"exec")

SCOPE="OFFLINE-SCOPE"
SYM="AINUSDT"
CLIENT="OFFLINE-ORDER-ID"
PENDING={"scope":SCOPE,"state":"OPENED"}
RESERVE={"role":"POLICY_RESERVE","detail":{"scope":SCOPE,
          "symbol":SYM,"client_id":CLIENT}}
RECORDS={"POLICY_RESERVE_"+SCOPE+".json":RESERVE}

class Blocked(RuntimeError):pass
class OrderNotFound(RuntimeError):pass

class FakeVenue:
    def __init__(self,state):
        self.state=state
        self.read_count=0
        self.post_count=0
    def read(self,name,**kwargs):
        assert name=="query_order",name
        assert kwargs.get("symbol")==SYM
        assert kwargs.get("orig_client_order_id")==CLIENT
        self.read_count+=1
        if self.state=="NOT_FOUND":raise OrderNotFound("SYNTHETIC_400_MINUS_2013")
        if self.state=="TIMEOUT":raise TimeoutError("SYNTHETIC_TIMEOUT")
        if self.state=="FOUND":
            return {"clientOrderId":CLIENT,"symbol":SYM,"status":"FILLED",
                    "executedQty":"2"}
        if self.state=="TERMINAL_ZERO":
            return {"clientOrderId":CLIENT,"symbol":SYM,"status":"CANCELED",
                    "executedQty":"0"}
        raise AssertionError("INVALID_FAKE_STATE")

class TestRestartExactOrderProof(unittest.TestCase):
    def make(self, state, proof_failure=False):
        venue=FakeVenue(state)
        calls=[]
        class Gate:pass
        gate=Gate()
        ns={
            "Blocked":Blocked,
            "OrderNotFound":OrderNotFound,
            "hashlib":hashlib,"Decimal":Decimal,
            "TERMINAL_NOT_FILLED":("CANCELED","EXPIRED","REJECTED","EXPIRED_IN_MATCH"),
            "snapshot":lambda v:({"positions":[]},[],[]),
            "exposure":lambda acct,symbol:Decimal("0"),
            "policy_settle_after_flat":lambda *a: (
                calls.append("SETTLE") or
                {"event":{"type":"CLOSED","scope":SCOPE,
                          "source":"SIGNED_INCOME_REALIZED_PNL",
                          "realized_pnl_usdt":"0"},"risk_exceeded":False}),
            "_resolve_verified_zero_entry_scope":lambda *a: (
                (_ for _ in ()).throw(Blocked("INCOMPLETE_PROOF"))
                if proof_failure else calls.append("EXACT_WAL_PROOF")),
            "mark_scope_resolved":lambda *a:calls.append("MARK_RESOLVED"),
            "LOG":SimpleNamespace(__truediv__=lambda *a:None),
        }
        exec(compiled,ns)
        return ns,venue,calls,gate

    def test_missing_entry_wal_with_signed_order_found_never_assumes_zero(self):
        ns,v,calls,gate=self.make("FOUND")
        self.assertIsNone(ns["_restart_entry_fill"](v,RECORDS,SCOPE,SYM,CLIENT))
        self.assertEqual(v.read_count,1)

    def test_missing_entry_wal_with_read_timeout_is_unknown(self):
        ns,v,calls,gate=self.make("TIMEOUT")
        self.assertIsNone(ns["_restart_entry_fill"](v,RECORDS,SCOPE,SYM,CLIENT))

    def test_missing_entry_wal_only_exact_not_found_permits_zero(self):
        ns,v,calls,gate=self.make("NOT_FOUND")
        self.assertEqual(ns["_restart_entry_fill"](v,RECORDS,SCOPE,SYM,CLIENT),"0")
        self.assertEqual(v.read_count,1)

    def test_found_order_blocks_restart_scope_before_settlement(self):
        ns,v,calls,gate=self.make("FOUND")
        result=ns["_restart_one"](gate,object(),v,RECORDS,PENDING)
        self.assertEqual(result,"KEPT_ENTRY_STATUS_UNKNOWN")
        self.assertEqual(calls,[])

    def test_timeout_blocks_restart_scope_before_settlement(self):
        ns,v,calls,gate=self.make("TIMEOUT")
        result=ns["_restart_one"](gate,object(),v,RECORDS,PENDING)
        self.assertEqual(result,"KEPT_ENTRY_STATUS_UNKNOWN")
        self.assertEqual(calls,[])

    def test_exact_not_found_but_secondary_proof_missing_keeps_wal(self):
        ns,v,calls,gate=self.make("NOT_FOUND",proof_failure=True)
        result=ns["_restart_one"](gate,object(),v,RECORDS,PENDING)
        self.assertEqual(result,"KEPT_ZERO_ENTRY_WAL_PROOF_INCOMPLETE")
        self.assertEqual(calls,["SETTLE"])
        self.assertEqual(v.post_count,0)

    def test_full_second_proof_in_fake_environment_can_resolve(self):
        ns,v,calls,gate=self.make("NOT_FOUND")
        result=ns["_restart_one"](gate,object(),v,RECORDS,PENDING)
        self.assertEqual(result,"SETTLED")
        self.assertEqual(calls,["SETTLE","EXACT_WAL_PROOF"])
        self.assertEqual(v.post_count,0)

    def test_terminal_zero_without_entry_wal_remains_unknown_not_absent(self):
        ns,v,calls,gate=self.make("TERMINAL_ZERO")
        result=ns["_restart_one"](gate,object(),v,RECORDS,PENDING)
        self.assertEqual(result,"KEPT_ENTRY_STATUS_UNKNOWN")
        self.assertEqual(calls,[])

    def test_source_requires_signed_query_for_missing_wal(self):
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                and n.name=="_restart_entry_fill")
        ifs=[n for n in ast.walk(fn) if isinstance(n,ast.If)]
        proof=next(n for n in ifs if isinstance(n.test,ast.Compare)
                   and isinstance(n.test.left,ast.Name) and n.test.left.id=="name")
        texts=[n for n in ast.walk(proof) if isinstance(n,ast.Call)
               and isinstance(n.func,ast.Attribute)
               and n.func.attr=="read"]
        self.assertEqual(len(texts),1)

if __name__=="__main__":unittest.main()
