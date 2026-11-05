"""ATLAS FAST offline end-to-end safety chain simulation.

Selected owner functions compiled from AST. Signed reads, journal, lease and POST
are LOCAL FAKE objects. Only TemporaryDirectory files are ever modified.
"""
import ast
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
OWNER=ROOT/"owner_single_execution.py"
TREE=ast.parse(OWNER.read_text())
NAMES={
    "_require_fresh_owner_approval",
    "_check_approval_after_reservation",
    "_policy_inputs_market_last",
    "risk_ledger_entry_gate",
    "write_policy_reserve_record",
    "policy_settle_after_flat",
    "one_write",
    "place_stop_write",
    "_restart_wal_records",
    "startup_reconcile",
    "mark_scope_resolved",
    "_resolve_verified_zero_entry_scope",
    "_settle_zero_entry_and_resolve",
}
SELECTED=[n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
assert len(SELECTED)==len(NAMES)
SOURCE=ast.fix_missing_locations(ast.Module(body=SELECTED,type_ignores=[]))

sys.path.insert(0,str(ROOT))
import restart_coverage as rc
import stop_ambiguity_guard as stopmod

SYM="AINUSDT"
SCOPE="offline-scope-synthetic"
ENTRY_ID="OFFLINE-ENTRY-01"
STOP_ID="OFFLINE-STOP-01"
ENTRY={"symbol":SYM,"newClientOrderId":ENTRY_ID}
STOP={"symbol":SYM,"clientAlgoId":STOP_ID}
PLAN={"symbol":SYM,"clientAlgoId":STOP_ID,"side":"SELL",
      "triggerPrice":"0.31461","workingType":"CONTRACT_PRICE",
      "positionSide":"BOTH"}
ALGO=dict(PLAN,algoStatus="NEW",orderType="STOP_MARKET",closePosition="true")

class Blocked(RuntimeError):pass
class OrderNotFound(Exception):pass
class PreSendRefused(Blocked):pass
class LeaseRefused(PreSendRefused):
    def __init__(self,msg,transient=False):
        super().__init__(msg)
        self.transient=transient is True
class SignedVenue:pass

class FakeVenue:
    def __init__(self,sim):
        self.sim=sim
        self.position="0"
        self.stop_open=False
        self.post_error=None
        self.post_calls=[]
        self.lease=0
        self.outage=False
        self.order_query_status="NOT_FOUND"

    def read(self,method,**kwargs):
        if method!="query_order":
            raise AssertionError("UNEXPECTED_SIGNED_GET")
        if self.outage or self.order_query_status=="TIMEOUT":
            raise TimeoutError("OFFLINE_EXACT_ORDER_GET_TIMEOUT")
        if self.order_query_status=="NOT_FOUND":
            raise OrderNotFound("OFFLINE_EXACT_ORDER_DOES_NOT_EXIST")
        return {"symbol":SYM,"clientOrderId":ENTRY_ID,"status":"FILLED"}

    def acquire_mutation_lease(self):
        self.lease+=1
    def release_mutation_lease(self):
        self.lease-=1
    def post_once(self,method,endpoint,**kwargs):
        self.post_calls.append((method,endpoint))
        if self.post_error is not None:
            raise self.post_error

    def read_account(self):
        if self.outage:raise TimeoutError("OFFLINE_SIGNED_GET_TIMEOUT")
        return ({"positions":[{"symbol":SYM,"positionSide":"BOTH",
                "positionAmt":self.position}]},self.sim.ms)

    def read_open_algo_orders(self,symbol):
        if self.outage:raise TimeoutError("OFFLINE_STOP_GET_TIMEOUT")
        return ([dict(ALGO)] if self.stop_open else [],self.sim.ms)

    def read_open_orders(self,symbol):
        if self.outage:raise TimeoutError("OFFLINE_ORDER_GET_TIMEOUT")
        return ([],self.sim.ms)

    def read_mark(self,symbol):
        if self.outage:raise TimeoutError("OFFLINE_MARK_GET_TIMEOUT")
        return ({"markPrice":"0.32000"},self.sim.ms)

    def query_algo(self,client_algo_id):
        if self.outage:raise TimeoutError("OFFLINE_QUERY_STOP_TIMEOUT")
        if self.stop_open:return dict(ALGO)
        raise stopmod.OrderNotFound("FAKE_STOP_NOT_FOUND")

    def now_ms(self):
        return self.sim.ms

    def sleep(self,sec):
        self.sim.sleep_calls.append(sec)

class FakeGate:
    def __init__(self,sim):
        self.sim=sim
        self.open=False
        self.close_count=0
        self.settle_fail=False
        self.closed_event=None
        self.opened_event={"type":"OPENED","scope":SCOPE,"at_ms":500000}

    def _scope_events(self,scope):
        if scope!=SCOPE:
            raise Blocked("OFFLINE_SCOPE_MISMATCH")
        return self.opened_event,self.closed_event

    def pending_scopes(self):
        return [{"scope":SCOPE,"state":"OPENED"}] if self.open else []

    def settle(self,scope,*,symbol,entry_filled_qty,flat_proof,envelope,local_now_ms):
        if self.settle_fail:
            raise Blocked("OFFLINE_SETTLE_NOT_PROVEN")
        if scope!=SCOPE or symbol!=SYM or envelope!="synthetic-income":
            raise Blocked("OFFLINE_SIGNED_DOCUMENT_MISMATCH")
        if (flat_proof["position_amt"]!="0" or flat_proof["open_orders"]!=0
                or flat_proof["open_algo_orders"]!=0):
            raise Blocked("OFFLINE_NOT_FLAT")
        self.open=False
        self.close_count+=1
        self.closed_event={"type":"CLOSED","scope":scope,
            "realized_pnl_usdt":"0",
            "source":"SIGNED_INCOME_REALIZED_PNL","close_time_ms":local_now_ms}
        return {"state":"CLOSED","event":self.closed_event,
                "risk_exceeded":False,"already_settled":False}

class FakeInputs:
    def __init__(self,sim):
        self.sim=sim
        self.income_fail=False

    def signed_income_envelope(self,venue):
        if self.income_fail:
            raise TimeoutError("OFFLINE_INCOME_GET_TIMEOUT")
        return "synthetic-income"

    def policy_market(self,venue,bundle,account):
        return {"exchange_info_received_ms":self.sim.ms,
                "mark_time_ms":self.sim.ms}

class Integration:
    def __init__(self):
        self.temp=TemporaryDirectory(prefix="atlas-fast-offline-")
        self.root=Path(self.temp.name)
        self.wal=self.root/"wal"
        self.private=self.root/"private"
        self.wal.mkdir(mode=0o700)
        self.private.mkdir(mode=0o700)
        self.ms=1_000_000
        self.now=110.0
        self.sleep_calls=[]
        self.incidents=[]
        self.venue=FakeVenue(self)
        self.gate=FakeGate(self)
        self.inputs=FakeInputs(self)
        self.ns={
            "LOG":self.wal,
            "PRIVATE":self.private,
            "Blocked":Blocked,
            "OrderNotFound":OrderNotFound,
            "PreSendRefused":PreSendRefused,
            "LeaseRefused":LeaseRefused,
            "SignedVenue":SignedVenue,
            "time":SimpleNamespace(monotonic=lambda:self.now,
                    time=lambda:100.0,sleep=lambda sec:self.sleep_calls.append(sec)),
            "Decimal":Decimal,
            "hashlib":hashlib,
            "json":json,
            "os":os,
            "OWNER_APPROVAL_MAX_AGE_S":15,
            "MAX_RISK":Decimal("0.75"),
            "SETTLE_WAIT_S":65,
            "SETTLE_ATTEMPTS":3,
            "SETTLE_RETRY_S":30,
            "STOP_LEASE_RETRIES":3,
            "STOP_LEASE_RETRY_SLEEP":1,
            "POST_PATHS":frozenset(("/fapi/v1/order","/fapi/v1/algoOrder")),
            "_now_ms":lambda:self.ms,
            "_contract":lambda fn,*args,**kw:fn(*args,**kw),
            "journal":self.journal,
            "payload":lambda role,wire:(
                "new_algo_order" if role=="STOP" else "new_order",
                "/fapi/v1/algoOrder" if role=="STOP" else "/fapi/v1/order",
                {"symbol":wire["symbol"]}),
            "checked":lambda venue,role,wire:{"role":role,"status":"FAKE_VERIFIED"},
            "_live_token_ok":lambda _:True,
            "emergency_note":self.emergency_note,
            "snapshot":self.snapshot,
            "exposure":lambda acct,sym:Decimal(self.venue.position),
        }
        exec(compile(SOURCE,str(OWNER),"exec"),self.ns)

    def close(self):
        self.temp.cleanup()

    def journal(self,role,digest,detail):
        dest=self.wal/(role+"_"+digest+".json")
        dest.write_text(json.dumps({"role":role,"detail":detail}))

    def emergency_note(self,scope,reason):
        self.incidents.append(reason)
        (self.wal/("incident_"+scope+".json")).write_text(
            json.dumps({"scope":scope,"reason":reason}))

    def snapshot(self,venue):
        if venue.outage:raise TimeoutError("OFFLINE_ACCOUNT_TIMEOUT")
        account={"positions":[{"symbol":SYM,"positionSide":"BOTH",
                               "positionAmt":venue.position}]}
        return account,[],([dict(ALGO)] if venue.stop_open else [])

    def reserve(self):
        class Phase:
            def __init__(self,parent):
                self.parent=parent
            def reserve(self,*args,**kw):
                self.parent.gate.open=True
                return {"risk_worst_usdt":"0.50"}
        bundle={"entry":ENTRY}
        market,envelope,timing=self.ns["_policy_inputs_market_last"](
            self.inputs,self.venue,bundle,{"positions":[]})
        receipt=self.ns["risk_ledger_entry_gate"](
            Phase(self),bundle,market,envelope,SCOPE,gate=self.gate)
        assert receipt["risk_worst_usdt"]=="0.50"
        assert (self.wal/("POLICY_RESERVE_"+SCOPE+".json")).is_file()
        return bundle

    def gate_report(self):
        def step(venue,journal,*,scope,symbol,stop_id,tp_ids,sleep,alarm):
            ev=stopmod.gather(venue,symbol=symbol,stop_id=stop_id,tp_ids=tp_ids)
            return stopmod.classify(ev,PLAN)[0]
        events=[]
        return rc.restart_launch(
            load_wal=self.ns["_restart_wal_records"],
            venue=self.venue,
            guard_step=step,
            journal=object(),
            alarm=events.append,
            sleep=lambda sec:None,
            startup_reconcile=self.ns["startup_reconcile"],
            restart_reconcile=lambda:{"status":"RECONCILED" if not self.gate.open
                                      else "KEPT_ENTRIES_BLOCKED"},
            alarm_healthy=lambda:True), events

class EndToEndSafetyChainTests(unittest.TestCase):
    def setUp(self):
        self.s=Integration()
    def tearDown(self):
        self.s.close()

    def test_expired_approval_after_reservation_settles_but_wal_stays_locked(self):
        s=self.s
        bundle=s.reserve()
        s.now=116.0
        with self.assertRaisesRegex(Blocked,"ZERO_ENTRY_AUTO_RESOLUTION_DISABLED_WAL_FENCE_UNPROVEN"):
            s.ns["_check_approval_after_reservation"](
                100.0,s.gate,s.inputs,s.venue,bundle,SCOPE)
        self.assertEqual(s.gate.close_count,1)
        self.assertFalse(s.gate.open)
        self.assertEqual(s.venue.post_calls,[])
        report,_=s.gate_report()
        self.assertFalse(report["entries_allowed"])
        self.assertTrue(report["startup_gate"].startswith("BLOCKED:"))
        marker=s.wal/("resolved_"+SCOPE+".json")
        self.assertFalse(marker.exists())

    def test_expired_approval_with_income_outage_leaves_ledger_and_incident(self):
        s=self.s
        bundle=s.reserve()
        s.now=116
        s.inputs.income_fail=True
        with self.assertRaisesRegex(Blocked,"OWNER_APPROVAL_EXPIRED_AFTER_RESERVATION"):
            s.ns["_check_approval_after_reservation"](
                100.0,s.gate,s.inputs,s.venue,bundle,SCOPE)
        self.assertTrue(s.gate.open)
        self.assertEqual(s.gate.close_count,0)
        self.assertEqual(s.venue.post_calls,[])
        self.assertTrue(s.incidents)
        report,_=s.gate_report()
        self.assertFalse(report["entries_allowed"])

    def test_uncertain_entry_transport_and_unprotected_position_block_restart(self):
        s=self.s
        s.reserve()
        s.venue.post_error=TimeoutError("OFFLINE_POST_CONNECTION_LOST")
        with self.assertRaisesRegex(Blocked,"UNKNOWN_WRITE_RESULT_DO_NOT_RETRY_ENTRY"):
            s.ns["one_write"](s.venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        self.assertEqual(len(s.venue.post_calls),1)
        s.venue.position="79"  # synthetic worst-case: might have filled
        report,events=s.gate_report()
        self.assertEqual(report["protection"][SCOPE],"UNPROTECTED_NO_STOP_SENT")
        self.assertFalse(report["entries_allowed"])
        self.assertTrue(any("MANUAL_CLOSE_REQUIRED" in x for x in events))

    def test_stop_coverage_then_flat_with_open_exit_never_releases(self):
        s=self.s
        s.reserve()
        s.ns["one_write"](s.venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        s.venue.position="79"
        s.ns["place_stop_write"](s.venue,STOP,SCOPE,sleep=lambda _:None)
        s.venue.stop_open=True
        report,_=s.gate_report()
        self.assertEqual(report["protection"][SCOPE],"PROTECTED")
        self.assertFalse(report["entries_allowed"])
        s.venue.position="0"
        report,_=s.gate_report()
        self.assertEqual(report["protection"][SCOPE],"FLAT_EXITS_OPEN")
        self.assertFalse(report["entries_allowed"])
        # Signed simulated flat with cleaned exits: still no release before WAL resolution.
        s.venue.stop_open=False
        report,_=s.gate_report()
        self.assertEqual(report["protection"][SCOPE],"FLAT")
        self.assertFalse(report["entries_allowed"])
        self.assertTrue(report["startup_gate"].startswith("BLOCKED:"))

    def test_flat_settled_without_shared_fence_keeps_restart_locked(self):
        s=self.s
        s.reserve()
        s.ns["one_write"](s.venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        s.venue.position="79"
        s.ns["place_stop_write"](s.venue,STOP,SCOPE,sleep=lambda _:None)
        s.venue.stop_open=True
        assert s.gate_report()[0]["entries_allowed"] is False
        # Simulate owner-reviewed verified closure and cleared stops.
        s.venue.position="0"
        s.venue.stop_open=False
        settle=s.ns["policy_settle_after_flat"](
            s.gate,s.inputs,s.venue,{"entry":{"symbol":SYM}},SCOPE,"79")
        self.assertEqual(settle["state"],"CLOSED")
        self.assertFalse(s.gate.open)
        # Even fully settled and FLAT cannot authorize an unfenced marker.
        with self.assertRaisesRegex(
                Blocked,"WAL_RESOLUTION_MARKER_DISABLED_SHARED_FENCE_UNPROVEN"):
            s.ns["mark_scope_resolved"](SCOPE,"OFFLINE_TEST_SIGNED_FLAT")
        self.assertFalse((s.wal/("resolved_"+SCOPE+".json")).exists())
        report,_=s.gate_report()
        self.assertEqual(report["protection"],{SCOPE:"FLAT"})
        self.assertTrue(report["startup_gate"].startswith("BLOCKED:"))
        self.assertEqual(report["policy"],"RECONCILED")
        self.assertFalse(report["entries_allowed"])
        self.assertEqual(len(s.venue.post_calls),2)  # simulated ENTRY and STOP

class ZeroEntryResolutionProofTests(unittest.TestCase):
    """The local WAL marker is never written on incomplete zero-entry evidence."""
    def setUp(self):
        self.s=Integration()
        self.s.reserve()
        self.settled=self.s.ns["policy_settle_after_flat"](
            self.s.gate,self.s.inputs,self.s.venue,{"entry":{"symbol":SYM}},SCOPE,"0")
        self.assertEqual(self.settled["event"]["type"],"CLOSED")

    def tearDown(self):
        self.s.close()

    def _reject(self,message):
        with self.assertRaisesRegex(Blocked,message):
            self.s.ns["_resolve_verified_zero_entry_scope"](
                self.s.gate,self.s.venue,{"entry":ENTRY},SCOPE,self.settled)
        self.assertFalse((self.s.wal/("resolved_"+SCOPE+".json")).exists())

    def test_exact_signed_query_timeout_keeps_wal_blocked(self):
        self.s.venue.order_query_status="TIMEOUT"
        self._reject("ZERO_ENTRY_SIGNED_ORDER_ABSENCE_UNVERIFIED")

    def test_exchange_order_found_even_if_currently_flat_keeps_wal(self):
        self.s.venue.order_query_status="FOUND"
        self._reject("ZERO_ENTRY_ORDER_FOUND_UNEXPECTED")

    def test_new_position_after_ledger_settle_keeps_wal(self):
        self.s.venue.position="79"
        self._reject("ZERO_ENTRY_FRESH_FLAT_READ_UNVERIFIED")

    def test_open_stop_after_ledger_settle_keeps_wal(self):
        self.s.venue.stop_open=True
        self._reject("ZERO_ENTRY_FRESH_FLAT_READ_UNVERIFIED")

    def test_another_symbol_open_position_keeps_wal(self):
        base=self.s.snapshot
        def with_foreign_position(venue):
            account,normal,algo=base(venue)
            account["positions"].append({
                "symbol":"OTHERUSDT","positionSide":"BOTH","positionAmt":"2"})
            return account,normal,algo
        self.s.ns["snapshot"]=with_foreign_position
        self._reject("ZERO_ENTRY_FRESH_FLAT_READ_UNVERIFIED")

    def test_entry_wal_present_even_with_flat_and_closed_ledger_keeps_wal(self):
        self.s.journal("ENTRY","TEST-WAL",{"scope":SCOPE,"symbol":SYM,
                                          "client_id":ENTRY_ID})
        self._reject("ZERO_ENTRY_SCOPE_HAS_OTHER_WAL_OR_INCIDENT")

    def test_stop_wal_present_keeps_wal(self):
        self.s.journal("STOP","TEST-WAL",{"scope":SCOPE,"symbol":SYM,
                                         "client_id":STOP_ID})
        self._reject("ZERO_ENTRY_SCOPE_HAS_OTHER_WAL_OR_INCIDENT")

    def test_incident_after_settlement_keeps_wal(self):
        self.s.emergency_note(SCOPE,"OWNER_REVIEW_REQUIRED")
        self._reject("ZERO_ENTRY_SCOPE_HAS_OTHER_WAL_OR_INCIDENT")

    def test_reserve_client_id_mismatch_keeps_wal(self):
        p=self.s.wal/("POLICY_RESERVE_"+SCOPE+".json")
        row=json.loads(p.read_text())
        row["detail"]["client_id"]="SOMEONE-ELSE"
        p.write_text(json.dumps(row))
        self._reject("ZERO_ENTRY_EXACT_RESERVATION_WAL_REQUIRED")

    def test_close_event_nonzero_pnl_keeps_wal(self):
        self.settled["event"]["realized_pnl_usdt"]="0.01"
        self._reject("ZERO_ENTRY_LEDGER_CLOSED_NOT_RECONFIRMED")

    def test_close_event_wrong_source_keeps_wal(self):
        self.settled["event"]["source"]="OWNER_SIGNED_REVIEW"
        self._reject("ZERO_ENTRY_SIGNED_CLOSED_EVENT_MISSING")

    def test_ledger_pending_state_keeps_wal(self):
        self.s.gate.open=True
        self._reject("ZERO_ENTRY_LEDGER_CLOSED_NOT_RECONFIRMED")

    def test_unsigned_or_missing_close_result_keeps_wal(self):
        self.settled={}
        self._reject("ZERO_ENTRY_SETTLEMENT_NOT_CERTIFIED")

    def test_wal_changes_during_signed_query_keeps_wal(self):
        original=self.s.ns["_restart_wal_records"]
        n=[0]
        def race():
            n[0]+=1
            rows=original()
            if n[0]==2:
                rows["ENTRY_RACE.json"]={
                    "role":"ENTRY","detail":{"scope":SCOPE,"symbol":SYM,
                                             "client_id":ENTRY_ID}}
            return rows
        self.s.ns["_restart_wal_records"]=race
        self._reject("ZERO_ENTRY_WAL_CHANGED_DURING_CHECK")

    def test_wal_corruption_keeps_wal(self):
        (self.s.wal/"ENTRY_broken.json").write_text("broken-json")
        self._reject("RESTART_WAL_UNREADABLE_KEEP_OPENED")

    def test_all_evidence_still_keeps_wal_locked_until_writer_fence_exists(self):
        self._reject("ZERO_ENTRY_AUTO_RESOLUTION_DISABLED_WAL_FENCE_UNPROVEN")

    def test_certain_presend_lease_refusal_keeps_wal_until_fence(self):
        s=self.s
        s.gate.open=True
        s.gate.closed_event=None
        s.gate.close_count=0
        s.now=116
        with self.assertRaisesRegex(PreSendRefused,
            "OWNER_APPROVAL_EXPIRED_UNDER_LEASE_NO_WRITE"):
            s.ns["one_write"](s.venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        self.assertEqual(s.venue.post_calls,[])
        self.assertFalse(any(p.name.startswith("ENTRY_") for p in s.wal.iterdir()))
        with self.assertRaisesRegex(Blocked,"ZERO_ENTRY_AUTO_RESOLUTION_DISABLED_WAL_FENCE_UNPROVEN"):
            s.ns["_settle_zero_entry_and_resolve"](
                s.gate,s.inputs,s.venue,{"entry":ENTRY},SCOPE)
        self.assertFalse((s.wal/("resolved_"+SCOPE+".json")).exists())

    def test_uncertain_post_even_when_later_flat_must_not_auto_resolve(self):
        s=self.s
        s.gate.open=True
        s.gate.closed_event=None
        s.gate.close_count=0
        s.venue.post_error=TimeoutError("SIMULATED_UNKNOWN_SEND")
        with self.assertRaisesRegex(Blocked,"UNKNOWN_WRITE_RESULT_DO_NOT_RETRY_ENTRY"):
            s.ns["one_write"](s.venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        self.assertEqual(len(s.venue.post_calls),1)
        self.assertTrue(any(p.name.startswith("ENTRY_") for p in s.wal.iterdir()))
        self.assertIsNone(s.gate.closed_event)
        self._reject("ZERO_ENTRY_LEDGER_CLOSED_NOT_RECONFIRMED")
        # Even if a fake later settles a flat position, the ENTRY WAL remains.
        result=s.ns["policy_settle_after_flat"](
            s.gate,s.inputs,s.venue,{"entry":{"symbol":SYM}},SCOPE,"0")
        self.assertEqual(result["event"]["type"],"CLOSED")
        self.settled=result
        self._reject("ZERO_ENTRY_SCOPE_HAS_OTHER_WAL_OR_INCIDENT")

    def test_known_race_cannot_reach_resolution_marker_while_disabled(self):
        """TOCTOU remains unfixed; fail-closed containment prevents marker."""
        s=self.s
        original=s.ns["mark_scope_resolved"]
        def concurrent_append_before_marker(scope,outcome):
            s.journal("ENTRY","CONCURRENT-RACE",{
                "scope":scope,"symbol":SYM,"client_id":ENTRY_ID})
            return original(scope,outcome)
        s.ns["mark_scope_resolved"]=concurrent_append_before_marker
        with self.assertRaisesRegex(Blocked,"ZERO_ENTRY_AUTO_RESOLUTION_DISABLED_WAL_FENCE_UNPROVEN"):
            s.ns["_resolve_verified_zero_entry_scope"](
                s.gate,s.venue,{"entry":ENTRY},SCOPE,self.settled)
        self.assertFalse((s.wal/("resolved_"+SCOPE+".json")).exists())
        self.assertFalse(any(p.name.startswith("ENTRY_") for p in s.wal.iterdir()))

    def test_exact_signed_absence_is_required_by_source(self):
        fn=next(x for x in TREE.body if isinstance(x,ast.FunctionDef)
                and x.name=="_resolve_verified_zero_entry_scope")
        methods=[x for x in ast.walk(fn) if isinstance(x,ast.Call)
                 and isinstance(x.func,ast.Attribute)
                 and x.func.attr=="read"
                 and x.args and isinstance(x.args[0],ast.Constant)]
        self.assertEqual([x.args[0].value for x in methods],["query_order"])


if __name__=="__main__":
    unittest.main()
