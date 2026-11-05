"""Offline full run_once function integration suite.

The exact run_once Python AST is executed in an isolated namespace whose every
external dependency is synthetic. No production imports, SDK object, API
credential, live disk path, actual HTTP session, or real order action.
"""
from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"owner_single_execution.py"
TREE=ast.parse(SOURCE.read_text())
NAMES={"run_once","_policy_inputs_market_last",
       "_require_fresh_owner_approval","_check_approval_after_reservation",
       "_policy_timing_suffix"}
NODES=[n for n in TREE.body if isinstance(n,ast.FunctionDef)
       and n.name in NAMES]
assert {n.name for n in NODES}==NAMES
ISOLATED=compile(ast.fix_missing_locations(ast.Module(
    body=NODES,type_ignores=[])),str(SOURCE),"exec")

class Blocked(RuntimeError):pass
class PreSendRefused(Blocked):pass

BUNDLE={
    "entry":{"symbol":"AINUSDT","side":"BUY","quantity":"2",
             "newClientOrderId":"OFFLINE-ENTRY-EXACT"},
    "stop":{"symbol":"AINUSDT","side":"SELL","triggerPrice":"95",
            "clientAlgoId":"OFFLINE-STOP-EXACT"},
    "take_profits":[
        {"symbol":"AINUSDT","newClientOrderId":"OFFLINE-TP1"},
        {"symbol":"AINUSDT","newClientOrderId":"OFFLINE-TP2"},
    ],
}

class FakeVenue:
    def __init__(self,sim):
        self.sim=sim
        self.closed=False
        self.armed=False

    def arm_trade(self,token):
        self.sim.events.append("VENUE_ARM")
        if token!="OFFLINE-TOKEN":
            raise AssertionError("WRONG_FAKE_TOKEN")
        self.armed=True

    def close(self):
        self.closed=True
        self.sim.events.append("VENUE_CLOSE")

class FakePhase:
    def __init__(self,gate,sim):
        self.sim=sim

    def preflight(self,*args,**kwargs):
        self.sim.events.append("POLICY_PREFLIGHT")
        if self.sim.failure=="preflight":
            raise Blocked("OFFLINE_POLICY_REFUSAL")

    def owner_approved(self,*args,**kwargs):
        self.sim.events.append("POLICY_OWNER_APPROVED")

    def reserve(self,*args,**kwargs):
        self.sim.events.append("POLICY_RESERVE")
        self.sim.reserved=True
        return {"risk_worst_usdt":"0.25"}

class FakeInputs:
    def __init__(self,sim):
        self.sim=sim
        self.signed_income_reads=0
        self.market_reads=0

    def stop_guard(self):
        self.sim.events.append("GUARD_CONFIG")
        return (object(),self.sim.alarm,object())

    def policy_gate(self):
        self.sim.events.append("POLICY_GATE")
        return object()

    def signed_income_envelope(self,venue):
        self.signed_income_reads+=1
        self.sim.events.append("SIGNED_INCOME_MOCK")
        if self.sim.failure=="income":
            raise Blocked("OFFLINE_INCOME_UNAVAILABLE")
        return {"synthetic":"income"}

    def policy_market(self,venue,bundle,account):
        self.market_reads+=1
        self.sim.events.append("MARKET_MOCK")
        if self.sim.failure=="second_market_expiry" and self.market_reads==2:
            self.sim.clock["s"]=116.1
        return {
            "mark_time_ms":self.sim.clock["ms"]-10,
            "exchange_info_received_ms":self.sim.clock["ms"]-5,
        }

class FakeAlarm:
    def __init__(self,sim):
        self.sim=sim
        self.sinks=("OFFLINE_FAKE_SINK",)
        self.log=SimpleNamespace(healthy=True)

    def __call__(self,reason):
        self.sim.events.append("ALARM:"+str(reason)[:50])

class RunOnceSimulator:
    """A pure, disposable, closed-world dependency injection environment."""

    def __init__(self, failure=None, coverage=True, entry_filled=True):
        self.temp=TemporaryDirectory(prefix="atlas-run-once-fake-")
        self.failure=failure
        self.coverage=coverage
        self.entry_filled=entry_filled
        self.events=[]
        self.clock={"s":100.0,"ms":1_000_000}
        self.alarm=FakeAlarm(self)
        self.inputs=FakeInputs(self)
        self.venue=None
        self.writes=[]
        self.reserved=False
        self.settled=False
        self.resolved=False
        self.closed_reasons=[]
        self.flat=True
        self._snapshot_count=0
        self.namespace={
            "Blocked":Blocked,
            "PreSendRefused":PreSendRefused,
            "Decimal":Decimal,
            "OWNER_APPROVAL_MAX_AGE_S":15,
            "time":SimpleNamespace(monotonic=lambda:self.clock["s"],
                                   sleep=lambda s:self.events.append("MOCK_SLEEP")),
            "_now_ms":lambda:self.clock["ms"],
            "_contract":lambda fn,*a,**k:fn(*a,**k),
            "owner_only":self.owner_only,
            "integration_inputs":lambda:self.inputs,
            "restart_protection_gate":self.restart_protection_gate,
            "live_prerequisites":self.live_prerequisites,
            "live_r2_prerequisite":self.live_r2_prerequisite,
            "startup_reconcile":self.startup_reconcile,
            "snapshot":self.snapshot,
            "risk_authority":self.risk_authority,
            "SignedVenue":self.make_venue,
            "risk_ledger_entry_gate":self.reserve,
            "one_write":self.one_write,
            "resolve_uncertain_entry":self.resolve_uncertain_entry,
            "exposure":self.exposure,
            "flatten_or_supervise":self.flatten_or_supervise,
            "dec":lambda x:Decimal(str(x)),
            "fill_breaches_policy":lambda *args:False,
            "place_stop_write":self.place_stop,
            "signed_owned_stop_check":self.stop_proof,
            "stop_shape":lambda x:x,
            "publish_owner_stop_manifest":self.publish_manifest,
            "stop_guard_reconcile":self.guard_reconcile,
            "emergency_note":self.emergency_note,
            "cleanup_owned_exits":self.cleanup,
            "supervise_until_flat":self.supervise,
            "policy_settle_after_flat":self.settle,
            "_settle_zero_entry_and_resolve":self.zero_settle,
            "mark_scope_resolved":self.mark_resolved,
            "LOG":Path(self.temp.name)/"mock-wal",
            "LIVE_AUTH_USED_DIR":Path(self.temp.name)/"mock-auth",
        }
        exec(ISOLATED,self.namespace)

    def close(self):
        self.temp.cleanup()

    def module_context(self):
        """Only local sys.modules stubs, automatically restored afterwards."""
        def mod(name,**attrs):
            o=ModuleType(name)
            for k,v in attrs.items():setattr(o,k,v)
            return o

        contract=mod("fast_r8_offline_integration_contract_F3F4_CANDIDATE",
            production_entry=self.production_entry,
            PolicyPhase=lambda gate:FakePhase(gate,self),
            validate_stop_guard_config=lambda *a,**k:self.events.append("GUARD_VALID"),
            authorize_live_trade=lambda *a,**k:"OFFLINE-TOKEN")
        exact=mod("exact_trade_approval",
            owner_phone_grant=self.phone_grant,
            consume_exact_grant=self.consume_grant,
            _canonical=lambda b:(
                "OFFLINE-SCOPE",b["entry"]["symbol"],"BUY",Decimal("2"),
                Decimal("95"),None),
            owner_live_authorization=self.live_auth)
        signed=mod("sdk_signed_sender",
            signed_owned_stop_check=self.stop_proof)
        class PilotBlocked(RuntimeError):pass
        pilot=mod("live_pilot",
            PilotBlocked=PilotBlocked,
            check_symbol=lambda symbol:self.events.append("PILOT_SYMBOL"),
            pilot_status=lambda:self.events.append("PILOT_STATUS"))
        guard=mod("stop_ambiguity_guard",
            guard_step=lambda *a,**k:None,
            entry_allowed=lambda j:(True,[]))
        return patch.dict(sys.modules,{
            contract.__name__:contract,exact.__name__:exact,
            signed.__name__:signed,pilot.__name__:pilot,guard.__name__:guard,
        })

    def production_entry(self):
        self.events.append("PRODUCTION_GATE_MOCK")
        if self.failure=="production_gate":
            raise Blocked("OFFLINE_PRODUCTION_GATE_CLOSED")

    def owner_only(self):
        self.events.append("OWNER_ONLY_MOCK")

    def restart_protection_gate(self,guard):
        self.events.append("RESTART_STOP_MOCK")
        if self.failure=="restart":
            raise Blocked("OFFLINE_RESTART_PROTECTION_NOT_CLEAR")

    def live_prerequisites(self,guard):
        self.events.append("LIVE_PREREQUISITES_MOCK")
        if self.failure=="live_prerequisites":
            raise Blocked("OFFLINE_LIVE_PREREQUISITES_MISSING")

    def live_r2_prerequisite(self,*args):
        self.events.append("R2_PREREQUISITE_MOCK")
        if self.failure=="r2":
            raise Blocked("OFFLINE_R2_DISABLED")

    def startup_reconcile(self):
        self.events.append("STARTUP_RECONCILE_MOCK")

    def make_venue(self):
        self.events.append("FAKE_VENUE_CREATE")
        self.venue=FakeVenue(self)
        return self.venue

    def snapshot(self,venue):
        self._snapshot_count+=1
        self.events.append("SIGNED_SNAPSHOT_MOCK")
        if self.failure=="second_snapshot_open" and self._snapshot_count==2:
            return {"positions":[{"symbol":"AINUSDT","positionAmt":"1"}]},[],[]
        if self.failure=="entry_unknown_open" and self._snapshot_count>=3:
            return {"positions":[{"symbol":"AINUSDT","positionAmt":"2"}]},[],[]
        return {"positions":[]},[],[]

    def exposure(self,account,symbol):
        return sum((Decimal(str(p["positionAmt"])) for p in account["positions"]
                   if p.get("symbol")==symbol),Decimal("0"))

    def risk_authority(self,*args):
        self.events.append("RISK_AUTHORITY_MOCK")

    def phone_grant(self,bundle):
        self.events.append("PHONE_GRANT_MOCK")
        if self.failure=="phone":
            raise Blocked("OFFLINE_PHONE_APPROVAL_DENIED")
        return "offline-approval"

    def consume_grant(self,grant,bundle):
        self.events.append("PHONE_GRANT_CONSUMED_MOCK")
        return True

    def live_auth(self,grant,bundle):
        self.events.append("LIVE_AUTH_MOCK")
        return "offline-record"

    def reserve(self,phase,bundle,market,envelope,scope,gate):
        self.events.append("LEDGER_RESERVE_MOCK")
        if self.failure=="reserve":
            raise Blocked("OFFLINE_RESERVATION_REFUSED")
        self.reserved=True
        if self.failure=="after_reservation_expiry":
            self.clock["s"]=116.2
        return {"risk_worst_usdt":"0.25"}

    def one_write(self,venue,role,wire,scope,owner_approved_at=None):
        self.events.append("ORDER_MOCK_"+role)
        if role=="ENTRY" and self.failure=="presend":
            raise PreSendRefused("OFFLINE_PRESEND_REFUSED")
        if role=="ENTRY" and self.failure in ("entry_unknown_flat","entry_unknown_open"):
            self.writes.append(role)   # record unknown result, never real POST
            raise TimeoutError("OFFLINE_UNKNOWN_ENTRY_POST_RESULT")
        if role=="TP1" and self.failure=="tp":
            self.writes.append(role)
            raise Blocked("OFFLINE_TP_NOT_VERIFIED")
        self.writes.append(role)
        if role=="ENTRY":
            return {"avgPrice":"100","executedQty":"2","status":"FILLED"}
        return {"status":"OFFLINE_VERIFIED"}

    def resolve_uncertain_entry(self,venue,wire):
        self.events.append("RESOLVE_UNCERTAIN_ENTRY_MOCK")
        if self.failure=="entry_unknown_flat":
            raise Blocked("OFFLINE_ENTRY_RESULT_UNKNOWN")
        if self.failure=="entry_unknown_open":
            raise Blocked("OFFLINE_ENTRY_RESULT_UNKNOWN")
        return None

    def flatten_or_supervise(self,*args):
        self.events.append("FLATTEN_OR_SUPERVISE_MOCK")
        return False

    def zero_settle(self,*args):
        self.events.append("ZERO_SETTLE_MOCK")
        self.settled=True
        return "MOCK_SIGNED_ZERO"

    def settle(self,*args):
        self.events.append("SIGNED_POLICY_SETTLE_MOCK")
        self.settled=True
        return {"state":"CLOSED","risk_exceeded":False}

    def place_stop(self,*args,**kwargs):
        self.events.append("STOP_MOCK")
        if self.failure=="stop":
            raise Blocked("OFFLINE_STOP_FAILED")
        self.writes.append("STOP")

    def stop_proof(self,**kwargs):
        self.events.append("STOP_SIGNED_READBACK_MOCK")
        return {"status":"OWNER_SIGNED_PROTECTIVE_STOP_ACTIVE_AT_READ"}

    def publish_manifest(self,*args):
        self.events.append("STOP_MANIFEST_MOCK")

    def guard_reconcile(self,*args):
        self.events.append("GUARD_RECONCILE_MOCK")

    def emergency_note(self,scope,reason):
        self.events.append("INCIDENT_MOCK")
        self.closed_reasons.append(reason)

    def cleanup(self,*args):
        self.events.append("EXIT_CLEANUP_MOCK")

    def supervise(self,*args):
        self.events.append("SUPERVISION_MOCK")
        return {"state":"FLAT",
                "continuous_stop_coverage_verified":self.coverage,
                "broken":["MOCK_GAP"] if not self.coverage else []}

    def mark_resolved(self,scope,outcome):
        self.events.append("MARK_WAL_RESOLVED_MOCK")
        self.resolved=True
        return outcome

    def run(self):
        with self.module_context():
            return self.namespace["run_once"](BUNDLE)


class RunOnceFullFlowTests(unittest.TestCase):
    def setUp(self):
        self.sim=RunOnceSimulator()
    def tearDown(self):
        self.sim.close()

    def scenario(self,failure=None,coverage=True):
        self.sim.failure=failure
        self.sim.coverage=coverage
        return self.sim

    def test_offline_happy_path_all_roles_in_order(self):
        s=self.scenario()
        result=s.run()
        self.assertEqual(result,"FLAT_AND_OWNED_EXITS_VERIFIED_CLEARED")
        self.assertEqual(s.writes,["ENTRY","STOP","TP1","TP2"])
        self.assertEqual(s.inputs.signed_income_reads,2)
        self.assertEqual(s.inputs.market_reads,2)
        self.assertTrue(s.settled)
        self.assertTrue(s.resolved)
        self.assertTrue(s.venue.closed)
        self.assertLess(s.events.index("PHONE_GRANT_MOCK"),
                        s.events.index("LEDGER_RESERVE_MOCK"))
        self.assertLess(s.events.index("LEDGER_RESERVE_MOCK"),
                        s.events.index("ORDER_MOCK_ENTRY"))
        self.assertLess(s.events.index("ORDER_MOCK_ENTRY"),
                        s.events.index("STOP_MOCK"))
        self.assertLess(s.events.index("STOP_SIGNED_READBACK_MOCK"),
                        s.events.index("ORDER_MOCK_TP1"))

    def test_production_gate_refusal_before_owner_sdk(self):
        s=self.scenario("production_gate")
        with self.assertRaisesRegex(Blocked,"OFFLINE_PRODUCTION_GATE_CLOSED"):
            s.run()
        self.assertEqual(s.events,["PRODUCTION_GATE_MOCK"])
        self.assertEqual(s.writes,[])
        self.assertIsNone(s.venue)

    def test_restart_stop_refusal_before_venue(self):
        s=self.scenario("restart")
        with self.assertRaisesRegex(Blocked,"OFFLINE_RESTART_PROTECTION_NOT_CLEAR"):
            s.run()
        self.assertNotIn("FAKE_VENUE_CREATE",s.events)
        self.assertNotIn("PHONE_GRANT_MOCK",s.events)
        self.assertEqual(s.writes,[])

    def test_prerequisite_refusal_before_venue(self):
        s=self.scenario("live_prerequisites")
        with self.assertRaisesRegex(Blocked,"OFFLINE_LIVE_PREREQUISITES_MISSING"):
            s.run()
        self.assertIsNone(s.venue)
        self.assertEqual(s.writes,[])

    def test_preflight_refusal_happens_before_phone(self):
        s=self.scenario("preflight")
        with self.assertRaisesRegex(Blocked,"OFFLINE_POLICY_REFUSAL"):
            s.run()
        self.assertNotIn("PHONE_GRANT_MOCK",s.events)
        self.assertEqual(s.writes,[])
        self.assertTrue(s.venue.closed)

    def test_phone_refusal_never_reserves_or_writes(self):
        s=self.scenario("phone")
        with self.assertRaisesRegex(Blocked,"OFFLINE_PHONE_APPROVAL_DENIED"):
            s.run()
        self.assertNotIn("LEDGER_RESERVE_MOCK",s.events)
        self.assertEqual(s.writes,[])
        self.assertTrue(s.venue.closed)

    def test_r2_prerequisite_blocks_pre_reservation(self):
        s=self.scenario("r2")
        with self.assertRaisesRegex(Blocked,"OFFLINE_R2_DISABLED"):
            s.run()
        self.assertNotIn("LEDGER_RESERVE_MOCK",s.events)
        self.assertEqual(s.writes,[])

    def test_second_snapshot_open_blocks_before_reserve(self):
        s=self.scenario("second_snapshot_open")
        with self.assertRaisesRegex(Blocked,"ACCOUNT_NOT_FLAT_AFTER_OWNER_APPROVAL"):
            s.run()
        self.assertEqual(s.writes,[])
        self.assertNotIn("LEDGER_RESERVE_MOCK",s.events)

    def test_second_market_expires_owner_consent_before_reserve(self):
        s=self.scenario("second_market_expiry")
        with self.assertRaisesRegex(Blocked,"OWNER_APPROVAL_EXPIRED_AFTER_SECOND_MARKET_BEFORE_RESERVATION"):
            s.run()
        self.assertEqual(s.writes,[])
        self.assertFalse(s.reserved)

    def test_owner_approval_expires_after_reservation(self):
        s=self.scenario("after_reservation_expiry")
        with self.assertRaisesRegex(Blocked,"OWNER_APPROVAL_EXPIRED_AFTER_RESERVATION"):
            s.run()
        self.assertTrue(s.reserved)
        self.assertEqual(s.writes,[])
        self.assertTrue(s.settled)
        self.assertFalse(s.resolved) # real resolution requires separate proofs

    def test_presend_failure_zero_settles_without_entry(self):
        s=self.scenario("presend")
        with self.assertRaises(PreSendRefused):
            s.run()
        self.assertEqual(s.writes,[])
        self.assertTrue(s.settled)
        self.assertTrue(s.venue.closed)

    def test_entry_unknown_result_does_not_resend(self):
        s=self.scenario("entry_unknown_flat")
        with self.assertRaisesRegex(Blocked,"ENTRY_UNRESOLVED_INCIDENT_LEFT_NO_RELEASE"):
            s.run()
        self.assertEqual(s.writes,["ENTRY"])
        self.assertIn("ENTRY_WRITE_OR_RECEIPT_UNCERTAIN",s.closed_reasons)
        self.assertNotIn("STOP_MOCK",s.events)
        self.assertFalse(s.resolved)

    def test_entry_unknown_open_triggers_manual_supervision(self):
        s=self.scenario("entry_unknown_open")
        with self.assertRaisesRegex(Blocked,"ENTRY_UNRESOLVED_INCIDENT_LEFT_NO_RELEASE"):
            s.run()
        self.assertEqual(s.writes,["ENTRY"])
        self.assertIn("FLATTEN_OR_SUPERVISE_MOCK",s.events)
        self.assertFalse(s.resolved)

    def test_stop_failure_raises_and_never_sends_tp(self):
        s=self.scenario("stop")
        with self.assertRaisesRegex(Blocked,"OFFLINE_STOP_FAILED"):
            s.run()
        self.assertEqual(s.writes,["ENTRY"])
        self.assertIn("STOP_ABSENT_OR_UNCERTAIN_AFTER_ENTRY",s.closed_reasons)
        self.assertIn("GUARD_RECONCILE_MOCK",s.events)
        self.assertIn("FLATTEN_OR_SUPERVISE_MOCK",s.events)
        self.assertNotIn("ORDER_MOCK_TP1",s.events)
        self.assertFalse(s.resolved)

    def test_tp_failure_keeps_wal_unresolved(self):
        s=self.scenario("tp")
        with self.assertRaisesRegex(Blocked,"TP_UNKNOWN_POSITION_EMERGENCY_FLATTENED"):
            s.run()
        self.assertEqual(s.writes,["ENTRY","STOP","TP1"])
        self.assertIn("FLATTEN_OR_SUPERVISE_MOCK",s.events)
        self.assertFalse(s.resolved)

    def test_unverified_coverage_after_flat_never_resolves_wal(self):
        s=self.scenario(coverage=False)
        result=s.run()
        self.assertEqual(result,"FLAT_SETTLED_COVERAGE_NOT_VERIFIED_OWNER_REVIEW")
        self.assertEqual(s.writes,["ENTRY","STOP","TP1","TP2"])
        self.assertTrue(s.settled)
        self.assertFalse(s.resolved)
        self.assertTrue(any(x.startswith("STOP_COVERAGE_NOT_CONTINUOUSLY_VERIFIED")
                            for x in s.closed_reasons))

    def test_mock_income_failure_never_requests_approval(self):
        s=self.scenario("income")
        with self.assertRaisesRegex(Blocked,"OFFLINE_INCOME_UNAVAILABLE"):
            s.run()
        self.assertNotIn("PHONE_GRANT_MOCK",s.events)
        self.assertEqual(s.writes,[])


if __name__=="__main__":
    unittest.main()
