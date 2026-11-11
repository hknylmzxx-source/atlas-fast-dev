"""G54: owner AST parçaları + sahte venue + yalnız sentetik geçici WAL.

Owner modülü/SDK import edilmez. UID501 sınırı kaydedilir; test makinesinin
UID'si yalnız bu testin adapter sarmalayıcısında sentetik dizine uygulanır.
"""
import ast
from contextlib import ExitStack
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import wal_owner_write_adapter_candidate as adapter
import wal_writer_fence_candidate as fence

TREE=ast.parse((ROOT/"owner_single_execution.py").read_text())
FUNCTIONS={n.name:n for n in TREE.body if isinstance(n,ast.FunctionDef)}
CLASSES={n.name:n for n in TREE.body if isinstance(n,ast.ClassDef)}
SCOPE="G54_SYNTHETIC_SCOPE"
SYMBOL="AINUSDT"
ROLES=("ENTRY","STOP","TP1","TP2","EMERGENCY","CANCEL_STOP","CANCEL_TP1","CANCEL_TP2")
ORIGINALS={name:getattr(adapter,name) for name in
           ("append_order_journal","append_reservation","append_incident")}


def owner_namespace(directory):
    names=("journal","emergency_note","write_policy_reserve_record",
           "one_write","place_stop_write","_require_fresh_owner_approval",
           "cleanup_owned_exits","emergency_flatten","_emergency_flatten_locked",
           "startup_reconcile","mark_scope_resolved","risk_ledger_entry_gate")
    ns={"LOG":directory,"PRIVATE":directory,"os":os,"json":json,
        "hashlib":hashlib,"Decimal":Decimal,"fcntl":fcntl,
        "time":SimpleNamespace(time=lambda:123.0,monotonic=lambda:101.0,
                               sleep=lambda _:None),
        "OWNER_APPROVAL_MAX_AGE_S":15,"STOP_LEASE_RETRIES":3,
        "STOP_LEASE_RETRY_SLEEP":1,"POST_PATHS":{"/fapi/v1/order","/fapi/v1/algoOrder"},
        "SignedVenue":type("SyntheticSignedVenue",(),{}),
        "_live_token_ok":lambda _:True,"_now_ms":lambda:1000,
        "MAX_RISK":Decimal("0.75"),"_contract":lambda fn,*a,**k:fn(*a,**k),
        "payload":lambda role,wire:("new_order","/fapi/v1/order",{}),
        "checked":lambda *args:{"status":"SYNTHETIC_CHECKED"},
        "clear_owner_stop_manifest_after_flat":lambda bundle:None,
        "exposure":lambda account,symbol:Decimal(account["size"])}
    nodes=[CLASSES[n] for n in ("Blocked","PreSendRefused","LeaseRefused")]
    nodes += [FUNCTIONS[n] for n in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"<g54-isolated-owner>","exec"),ns)
    return ns


class FakeVenue:
    def __init__(self,root,ns):
        self.root=root;self.ns=ns
        self.events=[];self.posts=[];self.deletes=[];self.held=False
        self.post_error=None;self.release_error=None
    def acquire_mutation_lease(self):
        if self.held:raise AssertionError("NESTED_MUTATION_LEASE")
        self.events.append("lease_acquire");self.held=True
    def release_mutation_lease(self):
        self.events.append("lease_release");self.held=False
        if self.release_error:raise self.release_error
    def _dispatch(self,verb,args,kwargs):
        # Signed dispatch must not be nested under the WAL lock. DELETE and
        # emergency are allowed to acquire their own lease only AFTER append.
        with fence.writer_fence(self.root,expected_uid=os.getuid()):pass
        own_lease=not self.held
        if own_lease:self.acquire_mutation_lease()
        try:
            self.events.append(verb)
            (self.posts if verb=="POST" else self.deletes).append((args,kwargs))
            if verb=="POST" and self.post_error:raise self.post_error
        finally:
            if own_lease:self.release_mutation_lease()
    def post_once(self,*args,**kwargs):return self._dispatch("POST",args,kwargs)
    def delete_once(self,*args,**kwargs):return self._dispatch("DELETE",args,kwargs)


class OwnerFixture(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix="g54-owner-synthetic-")
        self.root=Path(self.temp.name)
        self.ns=owner_namespace(self.root)
        self.venue=FakeVenue(self.root,self.ns)
        self.uid_calls=[];self.sleeps=[]
        self.ns["time"].sleep=self.sleeps.append
        self.stack=ExitStack()
        for name,original in ORIGINALS.items():
            def redirect(directory,_original=original,**kwargs):
                self.assertEqual(Path(directory),self.root)
                self.uid_calls.append(kwargs["expected_uid"])
                self.assertEqual(kwargs["expected_uid"],501)
                kwargs["expected_uid"]=os.getuid()
                return _original(directory,**kwargs)
            self.stack.enter_context(patch.object(adapter,name,side_effect=redirect))
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.stack.close)
    def wire(self,role):
        key="clientAlgoId" if role=="STOP" else "newClientOrderId"
        return {"symbol":SYMBOL,key:"G54_"+role}
    def order(self,role="ENTRY",identity=None):
        detail={"scope":SCOPE,"client_id":"G54_"+role}
        if role!="CANCEL_STOP":detail["symbol"]=SYMBOL
        self.ns["journal"](role,identity or "ID_"+role,detail)
    def write(self,role="ENTRY"):
        return self.ns["one_write"](self.venue,role,self.wire(role),SCOPE,owner_approved_at=100)
    def reserve(self):
        return self.ns["write_policy_reserve_record"](SCOPE,SYMBOL,"G54_ENTRY")
    def incident(self,reason="SYNTHETIC_ALARM"):
        return self.ns["emergency_note"](SCOPE,reason)
    def assert_no_marker(self):
        self.assertEqual(list(self.root.glob("resolved_*.json")),[])
    def seed(self,name,raw):
        path=self.root/name
        with path.open("xb") as out:out.write(raw)
        path.chmod(0o600)
        return path
    def configure_cleanup(self):
        stop={"symbol":SYMBOL,"clientAlgoId":"G54_STOP"}
        tps=[{"symbol":SYMBOL,"newClientOrderId":"G54_TP"+str(i)} for i in (1,2)]
        self.bundle={"entry":{"symbol":SYMBOL},"stop":stop,"take_profits":tps}
        calls=[]
        def snapshot(_):
            calls.append(True)
            if len(calls)>1:return {"size":"0"},[],[]
            return ({"size":"0"},[{"symbol":SYMBOL,"clientOrderId":t["newClientOrderId"]}
                    for t in tps],[stop])
        self.ns["snapshot"]=snapshot
    def configure_emergency(self):
        calls=[]
        def snapshot(_):
            calls.append(True)
            return {"size":"2" if len(calls)==1 else "0"},[],[]
        self.ns["snapshot"]=snapshot
        self.ns["actual_order"]=lambda v,r,w:{"status":"FILLED","clientOrderId":w["newClientOrderId"]}
        def intervention():
            fd=os.open(self.root/"synthetic_intervention.lock",os.O_RDWR|os.O_CREAT,0o600)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return fd
        self.ns["emergency_intervention_lock"]=intervention


class OwnerWriterTests(OwnerFixture):
    def role_contract(self,role):
        self.order(role)
        row=json.loads((self.root/(role+"_ID_"+role+".json")).read_bytes())
        self.assertEqual(row["role"],role)
        self.assertEqual(row["state"],"WRITE_OUTCOME_UNKNOWN_NO_RETRY")
        self.assertEqual(row["detail"]["scope"],SCOPE)
        self.assertEqual(self.uid_calls,[501]);self.assert_no_marker()
    def test_entry_writer(self):self.role_contract("ENTRY")
    def test_stop_writer(self):self.role_contract("STOP")
    def test_tp1_writer(self):self.role_contract("TP1")
    def test_tp2_writer(self):self.role_contract("TP2")
    def test_emergency_writer(self):self.role_contract("EMERGENCY")
    def test_cancel_stop_preserves_symbol_absence(self):self.role_contract("CANCEL_STOP")
    def test_cancel_tp1_writer(self):self.role_contract("CANCEL_TP1")
    def test_cancel_tp2_writer(self):self.role_contract("CANCEL_TP2")
    def test_all_ten_writers_hold_same_os_fence_during_append(self):
        original=fence._write_once
        observed=[]
        def verify(fd,name,row):
            with self.assertRaisesRegex(fence.WalFenceBlocked,"WAL_FENCE_BUSY"):
                with fence.writer_fence(self.root,expected_uid=os.getuid()):pass
            observed.append(name)
            return original(fd,name,row)
        with patch.object(fence,"_write_once",side_effect=verify):
            for role in ROLES:self.order(role)
            self.reserve();self.incident()
        self.assertEqual(len(observed),10);self.assert_no_marker()
    def test_reservation_contract_and_startup_remains_blocked(self):
        self.reserve()
        row=json.loads((self.root/("POLICY_RESERVE_"+SCOPE+".json")).read_bytes())
        self.assertEqual(row["state"],"RESERVATION_PENDING")
        with self.assertRaisesRegex(self.ns["Blocked"],"UNRESOLVED_WAL_SCOPE"):
            self.ns["startup_reconcile"]()
    def test_incident_contract_file_and_directory_fsync(self):
        original=os.fsync;calls=[]
        def sync(fd):
            calls.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            original(fd)
        with patch.object(fence.os,"fsync",side_effect=sync):self.incident()
        self.assertEqual(calls,["file","dir"])
        row=json.loads((self.root/("incident_"+SCOPE+".json")).read_bytes())
        self.assertIs(row["new_entry_permitted"],False)
        self.assertIs(row["retry_permitted"],False)
        with self.assertRaisesRegex(self.ns["Blocked"],"UNRESOLVED_INCIDENT"):
            self.ns["startup_reconcile"]()
    def test_repeat_incident_preserves_and_resyncs_first_evidence(self):
        self.incident("FIRST")
        path=self.root/("incident_"+SCOPE+".json");before=path.read_bytes()
        with patch.object(fence.os,"fsync",wraps=os.fsync) as sync:self.incident("SECOND")
        self.assertEqual(sync.call_count,2);self.assertEqual(path.read_bytes(),before)
    def test_corrupt_existing_incident_is_not_silently_accepted(self):
        self.seed("incident_"+SCOPE+".json",b"{")
        with self.assertRaisesRegex(self.ns["Blocked"],"OWNER_WAL_INCIDENT_FAILED"):
            self.incident()
    def test_duplicate_order_preserves_bytes_and_blocks(self):
        self.order();path=self.root/"ENTRY_ID_ENTRY.json";before=path.read_bytes()
        with self.assertRaisesRegex(self.ns["Blocked"],"DUPLICATE_WAL_ATTEMPT_NO_RESEND"):
            self.order()
        self.assertEqual(path.read_bytes(),before)
    def test_duplicate_reservation_blocks(self):
        self.reserve()
        with self.assertRaisesRegex(self.ns["Blocked"],"POLICY_RESERVE_RECORD_EXISTS"):
            self.reserve()
    def test_busy_all_writers_are_immediate_blocked_and_make_no_json(self):
        with fence.writer_fence(self.root,expected_uid=os.getuid()):
            for callback in ([lambda role=r:self.order(role) for r in ROLES]
                             +[self.reserve,self.incident]):
                with self.subTest(callback=callback):
                    with self.assertRaisesRegex(self.ns["Blocked"],"WAL_FENCE_BUSY"):callback()
        self.assertEqual(list(self.root.glob("*.json")),[]);self.assertEqual(self.sleeps,[])
    def test_missing_adapter_fails_closed_as_owner_blocked(self):
        with patch.dict(sys.modules,{"wal_owner_write_adapter_candidate":None}):
            for callback in (self.order,self.reserve,self.incident):
                with self.assertRaises(self.ns["Blocked"]):callback()
    def test_unsafe_role_scope_detail_and_oversize_refused(self):
        for role,identity,detail in (("BAD","id",{"scope":SCOPE}),
                ("ENTRY","../escape",{"scope":SCOPE}),
                ("ENTRY","id",[]),
                ("ENTRY","id",{"scope":"../escape"}),
                ("ENTRY","id",{"scope":SCOPE,"symbol":SYMBOL,"client_id":"x"*20000})):
            with self.subTest(role=role,identity=identity):
                with self.assertRaises(self.ns["Blocked"]):self.ns["journal"](role,identity,detail)
        self.assertEqual(list(self.root.glob("*.json")),[])
    def test_marker_entrypoint_never_writes(self):
        self.reserve()
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_RESOLUTION_MARKER_DISABLED"):
            self.ns["mark_scope_resolved"](SCOPE,"SIGNED_FLAT")
        self.assert_no_marker()


class OwnerDispatchTests(OwnerFixture):
    def test_lease_fsync_then_unlocked_post_then_release(self):
        original=os.fsync
        def sync(fd):
            self.assertTrue(self.venue.held)
            self.venue.events.append("dir_fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_fsync")
            original(fd)
        with patch.object(fence.os,"fsync",side_effect=sync):self.write("STOP")
        self.assertEqual(self.venue.events,["lease_acquire","file_fsync","dir_fsync","POST","lease_release"])
    def test_stop_wal_busy_is_not_transient_lease_retry(self):
        with fence.writer_fence(self.root,expected_uid=os.getuid()):
            with self.assertRaisesRegex(self.ns["PreSendRefused"],"WAL_FENCE_BUSY") as error:
                self.ns["place_stop_write"](self.venue,self.wire("STOP"),SCOPE,sleep=self.sleeps.append)
        self.assertNotIsInstance(error.exception,self.ns["LeaseRefused"])
        self.assertEqual(self.sleeps,[]);self.assertEqual(self.venue.posts,[])
        self.assertEqual(self.venue.events,["lease_acquire","lease_release"])
    def test_unknown_post_never_resends_on_second_call(self):
        self.venue.post_error=TimeoutError("SYNTHETIC_UNKNOWN")
        with self.assertRaisesRegex(self.ns["Blocked"],"UNKNOWN_WRITE_RESULT_DO_NOT_RETRY_STOP"):
            self.write("STOP")
        with self.assertRaises(self.ns["PreSendRefused"]):self.write("STOP")
        self.assertEqual(len(self.venue.posts),1);self.assertFalse(self.venue.held)
    def test_post_release_error_is_unknown_and_never_resends(self):
        self.venue.release_error=OSError("SYNTHETIC_RELEASE")
        with self.assertRaisesRegex(self.ns["Blocked"],"UNKNOWN_WRITE_RESULT_LEASE_RELEASE_FAILED"):
            self.write()
        self.venue.release_error=None
        with self.assertRaises(self.ns["PreSendRefused"]):self.write()
        self.assertEqual(len(self.venue.posts),1)
    def test_wal_unlock_failure_closes_fds_and_never_posts_or_resends(self):
        original_flock=fcntl.flock;original_close=os.close;closed=[]
        def flock(fd,operation):
            if operation==fcntl.LOCK_UN:raise OSError("SYNTHETIC_UNLOCK_FAILURE")
            return original_flock(fd,operation)
        def close(fd):
            closed.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            return original_close(fd)
        with patch.object(fence.fcntl,"flock",side_effect=flock),patch.object(fence.os,"close",side_effect=close):
            with self.assertRaisesRegex(self.ns["PreSendRefused"],"WAL_FENCE_RELEASE_UNKNOWN_NO_RETRY"):
                self.write("STOP")
        self.assertIn("dir",closed);self.assertIn("file",closed)
        with self.assertRaises(self.ns["PreSendRefused"]):self.write("STOP")
        self.assertEqual(self.venue.posts,[]);self.assertFalse(self.venue.held)
    def test_file_fsync_failure_leaves_wal_but_never_posts_or_retries(self):
        with patch.object(fence.os,"fsync",side_effect=OSError("SYNTHETIC_FILE_FSYNC")):
            with self.assertRaisesRegex(self.ns["PreSendRefused"],"WAL_APPEND_UNKNOWN_NO_RETRY"):
                self.write("STOP")
        with self.assertRaises(self.ns["PreSendRefused"]):self.write("STOP")
        self.assertEqual(len(list(self.root.glob("STOP_*.json"))),1)
        self.assertEqual(self.venue.posts,[]);self.assertFalse(self.venue.held)
    def test_directory_fsync_failure_never_posts(self):
        original=os.fsync
        def sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError("SYNTHETIC_DIR_FSYNC")
            original(fd)
        with patch.object(fence.os,"fsync",side_effect=sync):
            with self.assertRaisesRegex(self.ns["PreSendRefused"],"WAL_APPEND_UNKNOWN_NO_RETRY"):
                self.write()
        self.assertEqual(self.venue.posts,[]);self.assertFalse(self.venue.held)
    def test_corrupt_readback_does_not_repeat_post(self):
        def bad(*args):raise self.ns["Blocked"]("SYNTHETIC_READBACK")
        self.ns["checked"]=bad
        with self.assertRaisesRegex(self.ns["Blocked"],"SIGNED_READBACK_MISSING_STOP"):
            self.ns["place_stop_write"](self.venue,self.wire("STOP"),SCOPE,sleep=self.sleeps.append)
        self.assertEqual(len(self.venue.posts),1);self.assertEqual(self.sleeps,[0.5]*5)
    def test_cancel_stop_and_both_tps_release_fence_before_delete_lease(self):
        self.configure_cleanup()
        self.assertTrue(self.ns["cleanup_owned_exits"](self.venue,self.bundle,SCOPE))
        self.assertEqual(len(self.venue.deletes),3)
        self.assertEqual(len(list(self.root.glob("CANCEL_*.json"))),3);self.assert_no_marker()
    def test_cancel_busy_does_not_delete(self):
        self.configure_cleanup()
        with fence.writer_fence(self.root,expected_uid=os.getuid()):
            with self.assertRaisesRegex(self.ns["Blocked"],"WAL_FENCE_BUSY"):
                self.ns["cleanup_owned_exits"](self.venue,self.bundle,SCOPE)
        self.assertEqual(self.venue.deletes,[])
    def test_cancel_unknown_is_not_automatically_repeated(self):
        self.configure_cleanup()
        def unknown(*a,**k):
            self.venue.deletes.append((a,k));raise TimeoutError("SYNTHETIC_DELETE_UNKNOWN")
        self.venue.delete_once=unknown
        with self.assertRaisesRegex(self.ns["Blocked"],"STOP_CANCEL_UNCERTAIN"):
            self.ns["cleanup_owned_exits"](self.venue,self.bundle,SCOPE)
        self.configure_cleanup()
        with self.assertRaisesRegex(self.ns["Blocked"],"DUPLICATE_WAL_ATTEMPT"):
            self.ns["cleanup_owned_exits"](self.venue,self.bundle,SCOPE)
        self.assertEqual(len(self.venue.deletes),1)
    def test_emergency_intervention_then_wal_then_unlocked_post_lease(self):
        self.configure_emergency()
        self.assertTrue(self.ns["emergency_flatten"](self.venue,SYMBOL,SCOPE))
        self.assertEqual(len(self.venue.posts),1);self.assert_no_marker()
    def test_emergency_busy_releases_intervention_and_never_posts(self):
        self.configure_emergency()
        with fence.writer_fence(self.root,expected_uid=os.getuid()):
            with self.assertRaisesRegex(self.ns["Blocked"],"WAL_FENCE_BUSY"):
                self.ns["emergency_flatten"](self.venue,SYMBOL,SCOPE)
        fd=self.ns["emergency_intervention_lock"]()
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
        self.assertEqual(self.venue.posts,[])
    def test_reservation_wal_failure_prevents_policy_ledger_mutation(self):
        reserve_calls=[]
        phase=SimpleNamespace(reserve=lambda *a,**k:reserve_calls.append(True))
        with fence.writer_fence(self.root,expected_uid=os.getuid()):
            with self.assertRaisesRegex(self.ns["Blocked"],"WAL_FENCE_BUSY"):
                self.ns["risk_ledger_entry_gate"](phase,{"entry":self.wire("ENTRY")},{},{},SCOPE)
        self.assertEqual(reserve_calls,[])


class WalValidationTests(OwnerFixture):
    def test_owner_uid_is_never_inferred_from_current_process(self):
        with patch.object(adapter,"append_reservation",side_effect=fence.WalFenceBlocked("DENIED")) as call:
            with self.assertRaises(self.ns["Blocked"]):self.reserve()
        self.assertEqual(call.call_args.kwargs["expected_uid"],501)
    def test_wrong_directory_uid_refused(self):
        with self.assertRaisesRegex(fence.WalFenceBlocked,"WAL_DIR_PERMISSIONS"):
            ORIGINALS["append_reservation"](self.root,scope=SCOPE,symbol=SYMBOL,client_id="id",expected_uid=os.getuid()+1)
    def test_symlinked_wal_directory_refused(self):
        link=self.root/"link";link.symlink_to(self.root,target_is_directory=True)
        self.ns["LOG"]=link
        with self.assertRaisesRegex(self.ns["Blocked"],"SYMLINK"):self.reserve()
    def test_group_readable_directory_refused(self):
        self.root.chmod(0o750)
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_DIR_PERMISSIONS"):self.reserve()
    def test_symlinked_fence_refused(self):
        (self.root/fence._LOCK).symlink_to(self.root/"missing")
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_FENCE_NOT_TRUSTED"):self.reserve()
    def test_hardlinked_record_refused(self):
        self.reserve();os.link(self.root/("POLICY_RESERVE_"+SCOPE+".json"),self.root/"copy.json")
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_RECORD_PERMISSIONS"):self.incident()
    def test_duplicate_json_keys_refused(self):
        self.seed("bad.json",b'{"scope":"a","scope":"b"}')
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_RECORD_UNREADABLE"):self.incident()
    def test_invalid_utf8_refused(self):
        self.seed("bad.json",b'\xff')
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_RECORD_UNREADABLE"):self.incident()
    def test_existing_oversize_record_refused_before_parsing(self):
        self.seed("bad.json",b" "*16385)
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_RECORD_SIZE_INVALID"):self.incident()
    def test_nonfinite_json_refused(self):
        self.seed("bad.json",b'{"at":NaN}')
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_RECORD_UNREADABLE"):self.incident()
    def test_exponent_overflow_in_nested_json_refused(self):
        self.seed("bad.json",b'{"scope":"x","nested":{"value":1e999}}')
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_RECORD_UNREADABLE"):self.incident()
    def test_oversize_incident_never_creates_partial_file(self):
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_ROW_TOO_LARGE"):
            self.incident("x"*17000)
        self.assertEqual(list(self.root.glob("*.json")),[])
    def test_valid_json_wrong_legacy_schema_blocks_all_writers(self):
        self.seed("ENTRY_bad.json",b'{"role":"ENTRY","detail":{"scope":"x"}}')
        for callback in (self.order,self.reserve,self.incident):
            with self.assertRaisesRegex(self.ns["Blocked"],"OWNER_WAL_SCHEMA_INVALID"):callback()
    def test_symlinked_record_refused(self):
        target=self.seed("target",b"{}")
        (self.root/"bad.json").symlink_to(target)
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_RECORD_UNREADABLE"):self.incident()
    def test_fifo_record_fails_without_waiting_for_a_writer(self):
        os.mkfifo(self.root/"bad.json",0o600)
        with self.assertRaisesRegex(self.ns["Blocked"],"WAL_RECORD_PERMISSIONS"):self.incident()
    def test_directory_replacement_during_scan_blocks_append(self):
        self.root=self.root/"wal"
        self.root.mkdir(mode=0o700)
        self.ns["LOG"]=self.root
        original=fence._read_all
        def replace(fd,validate_row=None):
            rows=original(fd,validate_row)
            self.root.rename(self.root.with_name("old_wal"))
            self.root.mkdir(mode=0o700)
            return rows
        with patch.object(fence,"_read_all",side_effect=replace):
            with self.assertRaisesRegex(self.ns["Blocked"],"WAL_DIR_CHANGED"):self.order()
        self.assertFalse((self.root/"ENTRY_ID_ENTRY.json").exists())


class CoverageOnIncidentFailureTests(OwnerFixture):
    def run_failure(self,failure,coverage=True):
        sys.path.insert(0,str(ROOT/"tests"))
        from test_run_once_full_flow_offline import RunOnceSimulator,Blocked
        sim=RunOnceSimulator(failure=failure,coverage=coverage)
        self.addCleanup(sim.close)
        # Use the REAL extracted emergency_note, not a stubbed exception.
        sim.namespace["LOG"]=self.root
        exec(compile(ast.Module(body=[FUNCTIONS["emergency_note"]],type_ignores=[]),
                     "<g54-incident-only>","exec"),sim.namespace)
        with fence.writer_fence(self.root,expected_uid=os.getuid()):
            with self.assertRaisesRegex(Blocked,"OWNER_WAL_INCIDENT_FAILED:.*WAL_FENCE_BUSY"):
                sim.run()
        self.assertIn("FLATTEN_OR_SUPERVISE_MOCK",sim.events)
        self.assertTrue(sim.venue.closed);self.assertFalse(sim.resolved)
        self.assertEqual(sim.events.count("FLATTEN_OR_SUPERVISE_MOCK"),1)
        self.assert_no_marker()
        return sim
    def test_stop_failure_and_incident_busy_still_supervises(self):
        sim=self.run_failure("stop")
        self.assertEqual(sim.writes,["ENTRY"])
    def test_unknown_entry_and_incident_busy_still_supervises(self):
        sim=self.run_failure("entry_unknown_open")
        self.assertEqual(sim.writes,["ENTRY"])
    def test_tp_failure_and_incident_busy_never_resends_tp(self):
        sim=self.run_failure("tp")
        self.assertEqual(sim.writes,["ENTRY","STOP","TP1"])
    def test_coverage_loss_and_incident_busy_cannot_bypass_supervision(self):
        self.run_failure(None,coverage=False)


class StaticParticipationTests(unittest.TestCase):
    def assert_baseline(self,name,digest):
        node=FUNCTIONS.get(name,CLASSES.get(name))
        self.assertEqual(hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest(),digest,name)
    def test_signed_transport_and_mutation_dispatch_ast_unchanged_from_base(self):
        # Digests come from 3c223d5, not from the patched source. The complete
        # SignedVenue AST includes signed GET, POST, DELETE, token and lease gates.
        self.assert_baseline("SignedVenue","0516273fcc382899611aa3799ea12db3e2b530ceffa5968539d68ecdf097b7b1")
        self.assert_baseline("make_guarded_request","046862183bc6fdf2265b587cbdb10290f07faa0d231987b390ed2c452a869b73")
    def test_owner_and_global_and_intervention_locks_ast_unchanged_from_base(self):
        for name,digest in {
            "owner_only":"50e78c56ac0c62d4d6e82f22171be538e0417ef1a3b272a632cecd0681401ddc",
            "global_one_trade_lock":"41a1572269efa167d451a34acecdba3a999d5644bcc7b233be57655e6222c6fc",
            "emergency_intervention_lock":"8608f90b5eba87ac8ae626cabc053a8718410c44c2eb932eb9e791f8bcdf7bfb",
        }.items():self.assert_baseline(name,digest)
    def test_stop_retry_and_post_delete_callers_ast_unchanged_from_base(self):
        for name,digest in {
            "one_write":"b72b5a9062c54bbb9e960fc9ce37ef2cf8325971c218a33c56f6caf089dfd942",
            "place_stop_write":"6444c6eb323ff2531a01ceab04476d7fff8bb949588c8d38b3b14912323c7a4e",
            "emergency_flatten":"afa3149b6b378504eaec9c0d8d981c76f1c56b8b73a31f07176ab798fe7f9ad7",
            "_emergency_flatten_locked":"b248866e85608b6f54c3291043da4cf7e09db6916239b90c36353f710281b216",
            "cleanup_owned_exits":"6c6d7d2d566ab37c9f6a3e966e96470de240f636df7ac0413a3b72230769f2a6",
        }.items():self.assert_baseline(name,digest)
    def test_resolve_zero_entry_and_startup_ast_unchanged_from_base(self):
        for name,digest in {
            "mark_scope_resolved":"1c620311c5d2fa64586a4d9bcb3a1c071b882d7075fd7deb9310629611a3877d",
            "_resolve_verified_zero_entry_scope":"f3bdca56cc7fbde3a2373d2c6ac79cb0ec1141edb50ef0fe438d076afb14298f",
            "startup_reconcile":"1ff1f1f98bb4c77acdd66753444d821221fe8bfaa457349b15cbc7aad6ca37ed",
        }.items():self.assert_baseline(name,digest)
    def test_three_owner_writers_delegate_and_have_no_direct_writable_open(self):
        for owner_name,callee in (("journal","append_order_journal"),
                ("emergency_note","append_incident"),
                ("write_policy_reserve_record","append_reservation")):
            calls=[n for n in ast.walk(FUNCTIONS[owner_name]) if isinstance(n,ast.Call)]
            self.assertEqual(sum(isinstance(n.func,ast.Name) and n.func.id==callee for n in calls),1)
            self.assertFalse(any(isinstance(n.func,ast.Attribute) and n.func.attr in ("open","write","fsync") for n in calls))
    def test_resolvers_stay_unconditional_or_terminal_blocked(self):
        marker=FUNCTIONS["mark_scope_resolved"]
        self.assertEqual(len(marker.body),2)
        self.assertIsInstance(marker.body[-1],ast.Raise)
        self.assertIsInstance(FUNCTIONS["_resolve_verified_zero_entry_scope"].body[-1],ast.Raise)
        for name in ("journal","emergency_note","write_policy_reserve_record"):
            self.assertFalse(any(isinstance(n,ast.Name) and n.id in
                                 ("resolve_reserve_only","resolve_zero_reservation_offline","mark_scope_resolved")
                                 for n in ast.walk(FUNCTIONS[name])))
    def test_no_owner_module_imported_by_new_tests(self):
        self.assertNotIn("owner_single_execution",sys.modules)
    def test_all_adapter_roles_and_cancel_callers_remain_visible(self):
        self.assertEqual(set(ROLES),set(adapter._ORDER_ROLES))
        cleanup=FUNCTIONS["cleanup_owned_exits"]
        names=[n.func.id for n in ast.walk(cleanup) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        self.assertEqual(names.count("journal"),2)
    def test_wal_flock_is_nonblocking_and_never_dispatches_sdk(self):
        source=ast.parse((ROOT/"wal_writer_fence_candidate.py").read_text())
        calls=[ast.unparse(n.func) for n in ast.walk(source) if isinstance(n,ast.Call)]
        self.assertIn("fcntl.flock",calls)
        self.assertFalse(set(calls)&{"post_once","delete_once","time.sleep","requests.get"})


if __name__=="__main__":unittest.main()
