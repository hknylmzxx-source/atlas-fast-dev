"""ATLAS FAST synthetic failure injection for ENTRY/STOP and restart WAL.

Selected source functions are compiled via AST; no live module, SDK or network.
"""
import ast
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parent.parent
PATH=ROOT/"owner_single_execution.py"
TREE=ast.parse(PATH.read_text())
NAMES={"one_write","place_stop_write","_require_fresh_owner_approval",
       "_restart_wal_records","startup_reconcile","_R2DisabledGuardVenue"}
NODES=[n for n in TREE.body if
       (isinstance(n,ast.FunctionDef) or isinstance(n,ast.ClassDef))
       and n.name in NAMES]
assert set(n.name for n in NODES)==NAMES
MODULE=ast.fix_missing_locations(ast.Module(body=NODES,type_ignores=[]))

class Blocked(RuntimeError):pass
class PreSendRefused(Blocked):pass
class LeaseRefused(PreSendRefused):
    def __init__(self,message,transient=False):
        super().__init__(message)
        self.transient=transient is True
class SignedVenue:pass

class FakeVenue:
    def __init__(self,leases=None,post_exception=None,release_exception=None):
        self.leases=list(leases or [])
        self.post_exception=post_exception
        self.release_exception=release_exception
        self.acquired=0
        self.posted=0
        self.released=0
    def acquire_mutation_lease(self):
        self.acquired+=1
        if self.leases:
            x=self.leases.pop(0)
            if x is not None:raise x
    def release_mutation_lease(self):
        self.released+=1
        if self.release_exception:raise self.release_exception
    def post_once(self,method,endpoint,**kwargs):
        self.posted+=1
        if self.post_exception:raise self.post_exception
    def read_account(self):
        return {"positions":[]},[]

class Harness:
    def __init__(self,wal_dir=None,private_dir=None,journal_error=None,readback_error=None):
        self.clock={"now":110}
        self.journal_rows=[]
        self.readbacks=0
        self.sleeps=[]
        self.journal_error=journal_error
        self.readback_error=readback_error
        self.wal=wal_dir
        self.private=private_dir
        self.ns={}
        self.ns.update({
            "Blocked":Blocked,"PreSendRefused":PreSendRefused,
            "LeaseRefused":LeaseRefused,"SignedVenue":SignedVenue,
            "hashlib":hashlib,"json":json,
            "LOG":self.wal,"PRIVATE":self.private,
            "time":SimpleNamespace(monotonic=lambda:self.clock["now"],
                sleep=lambda seconds:self.sleeps.append(seconds)),
            "OWNER_APPROVAL_MAX_AGE_S":15,
            "STOP_LEASE_RETRIES":3,"STOP_LEASE_RETRY_SLEEP":1,
            "POST_PATHS":frozenset(("/fapi/v1/order","/fapi/v1/algoOrder")),
            "payload":lambda role,wire:(
                "new_algo_order" if role=="STOP" else "new_order",
                "/fapi/v1/algoOrder" if role=="STOP" else "/fapi/v1/order",
                {"symbol":wire["symbol"]}),
            "_live_token_ok":lambda _:True,
            "journal":self.journal,
            "checked":self.checked,
        })
        exec(compile(MODULE,str(PATH),"exec"),self.ns)
    def journal(self,*args):
        if self.journal_error:raise self.journal_error
        self.journal_rows.append(args)
    def checked(self,venue,role,wire):
        self.readbacks+=1
        if self.readback_error:raise self.readback_error
        return {"role":role,"status":"FAKE_VERIFIED"}

ENTRY={"symbol":"AINUSDT","newClientOrderId":"OFFLINE-ENTRY"}
STOP={"symbol":"AINUSDT","clientAlgoId":"OFFLINE-STOP"}
SCOPE="OFFLINE-SCOPE-TEST"

class OrderWriteFailureTests(unittest.TestCase):
    def test_entry_post_transport_timeout_never_retries(self):
        h=Harness()
        venue=FakeVenue(post_exception=TimeoutError("SIMULATED_NETWORK_DROP"))
        with self.assertRaisesRegex(Blocked,"UNKNOWN_WRITE_RESULT_DO_NOT_RETRY_ENTRY"):
            h.ns["one_write"](venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        self.assertEqual((venue.acquired,venue.posted,venue.released),(1,1,1))
        self.assertEqual((len(h.journal_rows),h.readbacks),(1,0))

    def test_stop_post_transport_timeout_never_retries(self):
        h=Harness()
        venue=FakeVenue(post_exception=TimeoutError("SIMULATED_STOP_RESULT_UNKNOWN"))
        with self.assertRaisesRegex(Blocked,"UNKNOWN_WRITE_RESULT_DO_NOT_RETRY_STOP"):
            h.ns["place_stop_write"](venue,STOP,SCOPE,sleep=h.sleeps.append)
        self.assertEqual(venue.posted,1)
        self.assertEqual(venue.acquired,1)
        self.assertEqual(len(h.journal_rows),1)
        self.assertEqual(h.sleeps,[])

    def test_stop_busy_lease_retries_before_send_only(self):
        h=Harness()
        venue=FakeVenue(leases=[
            LeaseRefused("FENCE_BUSY",True),
            LeaseRefused("FENCE_BUSY",True),
            None,
        ])
        receipt=h.ns["place_stop_write"](venue,STOP,SCOPE,sleep=h.sleeps.append)
        self.assertEqual(receipt["status"],"FAKE_VERIFIED")
        self.assertEqual(venue.acquired,3)
        self.assertEqual(venue.posted,1)
        self.assertEqual(len(h.journal_rows),1)
        self.assertEqual(h.sleeps,[1,1])

    def test_stop_permanent_lease_failure_does_not_retry(self):
        h=Harness()
        venue=FakeVenue(leases=[LeaseRefused("LATCHED",False)])
        with self.assertRaises(LeaseRefused):
            h.ns["place_stop_write"](venue,STOP,SCOPE,sleep=h.sleeps.append)
        self.assertEqual(venue.acquired,1)
        self.assertEqual(venue.posted,0)
        self.assertEqual(h.sleeps,[])

    def test_stop_transient_lease_exhausts_bounded_retries(self):
        h=Harness()
        venue=FakeVenue(leases=[LeaseRefused("BUSY",True)]*4)
        with self.assertRaises(LeaseRefused):
            h.ns["place_stop_write"](venue,STOP,SCOPE,sleep=h.sleeps.append)
        self.assertEqual(venue.acquired,4)
        self.assertEqual(venue.posted,0)
        self.assertEqual(h.sleeps,[1,1,1])

    def test_entry_journal_failure_blocks_before_post(self):
        h=Harness(journal_error=OSError("SIMULATED_DISK_FULL"))
        venue=FakeVenue()
        with self.assertRaises(PreSendRefused):
            h.ns["one_write"](venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        self.assertEqual(venue.posted,0)
        self.assertEqual(venue.released,1)
        self.assertEqual(h.journal_rows,[])

    def test_signed_readback_timeout_does_not_resend(self):
        h=Harness(readback_error=TimeoutError("SIMULATED_GET_UNAVAILABLE"))
        venue=FakeVenue()
        with self.assertRaisesRegex(Blocked,"SIGNED_READBACK_MISSING_STOP"):
            h.ns["place_stop_write"](venue,STOP,SCOPE,sleep=h.sleeps.append)
        self.assertEqual(venue.posted,1)
        self.assertEqual(h.readbacks,6)
        self.assertEqual(h.sleeps,[0.5]*5)
        self.assertEqual(len(h.journal_rows),1)

    def test_lease_release_failure_after_post_blocks(self):
        h=Harness()
        venue=FakeVenue(release_exception=OSError("LOCK_RELEASE_FAILED"))
        with self.assertRaisesRegex(Blocked,"UNKNOWN_WRITE_RESULT_LEASE_RELEASE_FAILED_STOP"):
            h.ns["place_stop_write"](venue,STOP,SCOPE,sleep=h.sleeps.append)
        self.assertEqual(venue.posted,1)
        self.assertEqual(venue.released,1)
        self.assertEqual(len(h.journal_rows),1)

    def test_r2_disabled_guard_forwards_reads_but_blocks_emergency_post(self):
        h=Harness()
        venue=FakeVenue()
        wrapper=h.ns["_R2DisabledGuardVenue"](venue)
        self.assertFalse(wrapper.emergency_post_enabled)
        self.assertEqual(wrapper.read_account()[0],{"positions":[]})
        with self.assertRaisesRegex(Blocked,"R2_EMERGENCY_POST_CHANNEL_DISABLED"):
            wrapper.post_reduce_only_market(symbol="AINUSDT",quantity="79")
        self.assertEqual(venue.posted,0)

class RestartWalFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.wal=self.root/"wal"
        self.wal.mkdir()
        self.private=self.root/"private"
        self.private.mkdir()
        self.h=Harness(wal_dir=self.wal,private_dir=self.private)
    def tearDown(self):
        self.temp.cleanup()
    def wal_record(self):
        (self.wal/"ENTRY_test.json").write_text(json.dumps(
            {"role":"ENTRY","detail":{"scope":SCOPE,"symbol":"AINUSDT",
                                       "client_id":"OFFLINE-ENTRY"}}))
    def test_unresolved_wal_blocks_new_entry(self):
        self.wal_record()
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_WAL_SCOPE_OWNER_REVIEW_REQUIRED"):
            self.h.ns["startup_reconcile"]()
        self.assertTrue((self.wal/"ENTRY_test.json").exists())

    def test_resolved_wal_scope_allows_startup_gate(self):
        self.wal_record()
        (self.wal/("resolved_"+SCOPE+".json")).write_text(json.dumps(
            {"scope":SCOPE,"outcome":"SIGNED_FLAT"}))
        self.h.ns["startup_reconcile"]()

    def test_unresolved_incident_blocks_even_if_wal_resolved(self):
        self.wal_record()
        (self.wal/("resolved_"+SCOPE+".json")).write_text("{}")
        (self.wal/"incident_test.json").write_text("{}")
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_INCIDENT_OWNER_REVIEW_REQUIRED"):
            self.h.ns["startup_reconcile"]()

    def test_corrupt_wal_blocks_and_is_not_deleted(self):
        f=self.wal/"ENTRY_broken.json"
        f.write_text("{invalid")
        with self.assertRaisesRegex(Blocked,"UNREADABLE_WAL_OWNER_REVIEW_REQUIRED"):
            self.h.ns["startup_reconcile"]()
        self.assertTrue(f.exists())

    def test_active_stop_manifest_blocks_new_entry(self):
        f=self.private/"active_stop_manifest.json"
        f.write_text("{}")
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_STOP_MANIFEST_OWNER_REVIEW_REQUIRED"):
            self.h.ns["startup_reconcile"]()
        self.assertTrue(f.exists())

    def test_restart_wal_read_failure_fails_closed(self):
        (self.wal/"OTHER.json").write_text("broken-json")
        with self.assertRaisesRegex(Blocked,"RESTART_WAL_UNREADABLE_KEEP_OPENED"):
            self.h.ns["_restart_wal_records"]()

if __name__=="__main__":
    unittest.main()
