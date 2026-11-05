"""Fail-closed owner-approval expiry tests; AST-only offline execution.

No SDK import, network, live executor invocation or trading.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"owner_single_execution.py"
TREE=ast.parse(SOURCE.read_text())
FUNCTIONS={
    f.name:f for f in TREE.body
    if isinstance(f,ast.FunctionDef)
}
NAMES=("_require_fresh_owner_approval","_check_approval_after_reservation","one_write")
assert all(n in FUNCTIONS for n in NAMES)
SAFE_AST=ast.fix_missing_locations(ast.Module(body=[FUNCTIONS[n] for n in NAMES],type_ignores=[]))

class Blocked(RuntimeError):pass
class PreSendRefused(RuntimeError):pass
class SignedVenue:pass

class FakeVenue:
    def __init__(self, clock, lease_elapsed=0):
        self.clock=clock
        self.lease_elapsed=lease_elapsed
        self.actions=[]
    def acquire_mutation_lease(self):
        self.actions.append("acquire")
        self.clock["now"]+=self.lease_elapsed
    def release_mutation_lease(self):
        self.actions.append("release")
    def post_once(self, method, endpoint, **kwargs):
        self.actions.append("POST")
        if method!="new_order" or endpoint!="/fapi/v1/order":
            raise AssertionError("Unexpected fake POST")

def namespace(clock, journal, settle):
    ns={
        "Blocked":Blocked,
        "PreSendRefused":PreSendRefused,
        "SignedVenue":SignedVenue,
        "OWNER_APPROVAL_MAX_AGE_S":15,
        "time":SimpleNamespace(monotonic=lambda:clock["now"]),
        "hashlib":hashlib,
        "payload":lambda role,wire:("new_order","/fapi/v1/order",
                                   {"symbol":wire["symbol"]}),
        "POST_PATHS":frozenset(("/fapi/v1/order",)),
        "journal":lambda *args: journal.append(args),
        "checked":lambda venue,role,wire:{"status":"SIGNED_FAKE_READBACK","role":role},
        "_live_token_ok":lambda token:True,
        "_settle_zero_entry_and_resolve":settle,
    }
    exec(compile(SAFE_AST,str(SOURCE),"exec"),ns)
    return ns

ENTRY={"symbol":"AINUSDT","newClientOrderId":"FAKE-ENTRY-ONLY"}
SCOPE="OFFLINE-DUMMY-SCOPE"

class OwnerApprovalExpiryTests(unittest.TestCase):
    def test_elapsed_exactly_15_is_accepted(self):
        clock={"now":115}
        ns=namespace(clock,[],lambda *x:None)
        ns["_require_fresh_owner_approval"](100,"CHECK")

    def test_elapsed_15_point_001_fails_closed(self):
        clock={"now":115.001}
        ns=namespace(clock,[],lambda *x:None)
        with self.assertRaisesRegex(Blocked,"OWNER_APPROVAL_EXPIRED_CHECK"):
            ns["_require_fresh_owner_approval"](100,"CHECK")

    def test_negative_monotonic_delta_fails_closed(self):
        clock={"now":99}
        ns=namespace(clock,[],lambda *x:None)
        with self.assertRaisesRegex(Blocked,"OWNER_APPROVAL_EXPIRED_CLOCK"):
            ns["_require_fresh_owner_approval"](100,"CLOCK")

    def test_missing_approval_timestamp_fails_closed(self):
        clock={"now":100}
        ns=namespace(clock,[],lambda *x:None)
        with self.assertRaisesRegex(Blocked,"OWNER_APPROVAL_TIME_MISSING_CHECK"):
            ns["_require_fresh_owner_approval"](None,"CHECK")

    def test_reservation_expiry_calls_zero_fill_settle_once(self):
        clock={"now":117}
        calls=[]
        ns=namespace(clock,[],lambda *args:calls.append(args))
        with self.assertRaisesRegex(Blocked,"OWNER_APPROVAL_EXPIRED_AFTER_RESERVATION"):
            ns["_check_approval_after_reservation"](100,"gate","inputs","venue",ENTRY,SCOPE)
        self.assertEqual(calls,[("gate","inputs","venue",ENTRY,SCOPE)])

    def test_reservation_settlement_failure_stays_fail_closed(self):
        clock={"now":118}
        calls=[]
        def fail_settle(*args):
            calls.append(args)
            raise Blocked("ZERO_FILL_SETTLEMENT_BLOCKED")
        ns=namespace(clock,[],fail_settle)
        with self.assertRaisesRegex(Blocked,"ZERO_FILL_SETTLEMENT_BLOCKED"):
            ns["_check_approval_after_reservation"](100,1,2,3,ENTRY,SCOPE)
        self.assertEqual(len(calls),1)

    def test_fresh_approval_does_not_settle(self):
        clock={"now":113}
        calls=[]
        ns=namespace(clock,[],lambda *args:calls.append(args))
        ns["_check_approval_after_reservation"](100,1,2,3,ENTRY,SCOPE)
        self.assertEqual(calls,[])

    def test_missing_approval_before_entry_cannot_mutate(self):
        clock={"now":100}
        journal=[]
        ns=namespace(clock,journal,lambda *args:None)
        venue=FakeVenue(clock)
        with self.assertRaisesRegex(PreSendRefused,"OWNER_APPROVAL_TIME_REQUIRED_NO_WRITE"):
            ns["one_write"](venue,"ENTRY",ENTRY,SCOPE)
        self.assertEqual(venue.actions,[])
        self.assertEqual(journal,[])

    def test_mutation_lease_delay_cannot_send_expired_entry(self):
        clock={"now":110}
        journal=[]
        ns=namespace(clock,journal,lambda *args:None)
        venue=FakeVenue(clock,lease_elapsed=6)
        with self.assertRaisesRegex(PreSendRefused,"OWNER_APPROVAL_EXPIRED_UNDER_LEASE_NO_WRITE"):
            ns["one_write"](venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        self.assertEqual(venue.actions,["acquire","release"])
        self.assertEqual(journal,[])

    def test_fresh_entry_mock_writes_exactly_once(self):
        clock={"now":114}
        journal=[]
        ns=namespace(clock,journal,lambda *args:None)
        venue=FakeVenue(clock)
        receipt=ns["one_write"](venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        self.assertEqual(receipt["status"],"SIGNED_FAKE_READBACK")
        self.assertEqual(venue.actions,["acquire","POST","release"])
        self.assertEqual(len(journal),1)

    def test_non_entry_write_does_not_require_entry_approval(self):
        """The ENTRY consent-age gate must not block protective STOP/TP writes."""
        clock={"now":180}
        journal=[]
        ns=namespace(clock,journal,lambda *args:None)
        venue=FakeVenue(clock)
        receipt=ns["one_write"](venue,"TP1",
                                {"symbol":"AINUSDT","newClientOrderId":"FAKE-TP"},SCOPE)
        self.assertEqual(receipt["status"],"SIGNED_FAKE_READBACK")
        self.assertEqual(venue.actions,["acquire","POST","release"])
        self.assertEqual(len(journal),1)

    def test_source_check_is_after_reservation_before_entry(self):
        run=FUNCTIONS["run_once"]
        calls=[
            (node.lineno,node.func.id)
            for node in ast.walk(run)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)
            and node.func.id in {
                "risk_ledger_entry_gate",
                "_check_approval_after_reservation",
                "one_write",
                "_require_fresh_owner_approval",
            }
        ]
        by_name={}
        for lineno,name in calls:
            by_name.setdefault(name,[]).append(lineno)
        self.assertEqual(len(by_name["risk_ledger_entry_gate"]),1)
        self.assertEqual(len(by_name["_check_approval_after_reservation"]),1)
        self.assertGreaterEqual(len(by_name["one_write"]),2)  # ENTRY and TP roles
        self.assertGreaterEqual(len(by_name["_require_fresh_owner_approval"]),2)
        entry_calls=[
            node for node in ast.walk(run)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)
            and node.func.id=="one_write"
            and len(node.args)>=2 and isinstance(node.args[1],ast.Constant)
            and node.args[1].value=="ENTRY"
        ]
        self.assertEqual(len(entry_calls),1)
        entry=entry_calls[0]
        self.assertLess(by_name["risk_ledger_entry_gate"][0],by_name["_check_approval_after_reservation"][0])
        self.assertLess(by_name["_check_approval_after_reservation"][0],entry.lineno)
        self.assertIn("owner_approved_at",[k.arg for k in entry.keywords])

if __name__=="__main__":
    unittest.main()
