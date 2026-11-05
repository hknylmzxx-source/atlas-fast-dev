"""Actual restart/STOP coverage logic with fully fake venue and alarm.

Imported modules are copied offline source without keys; no SDK or network.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import restart_coverage as rc

SCOPE="scope-offline-123"
SYMBOL="AINUSDT"

def records(stop=False, resolved=False):
    result={
        "ENTRY_1.json":{
            "role":"ENTRY",
            "detail":{"scope":SCOPE,"symbol":SYMBOL,"client_id":"FAKE-ENTRY"},
        },
    }
    if stop:
        result["STOP_1.json"]={
            "role":"STOP",
            "detail":{"scope":SCOPE,"symbol":SYMBOL,"client_id":"FAKE-STOP"},
        }
    if resolved:
        result["resolved_"+SCOPE+".json"]={"scope":SCOPE,"outcome":"FLAT"}
    return result

class FakeVenue:
    def __init__(self,position="0"):
        self.position=str(position)
        self.ms=1_000_000
        self.sends=0
    def read_account(self):
        return {"positions":[{"symbol":SYMBOL,"positionSide":"BOTH",
                              "positionAmt":self.position}]}, []
    def now_ms(self):
        return self.ms
    def sleep(self,s):
        pass
    def post_reduce_only_market(self,**kwargs):
        self.sends+=1
        raise AssertionError("OFFLINE TEST MUST NEVER SEND")

def report(v,wal,guard_result="FLAT",healthy=True,alarm=None):
    events=[]
    def alarm_fn(message):
        events.append(message)
        if alarm is not None:
            return alarm(message)
    out=rc.protection_gate(
        load_wal=lambda:wal,
        venue=v,
        guard_step=lambda *args,**kwargs: guard_result,
        journal=object(),
        alarm=alarm_fn,
        sleep=lambda secs:None,
        alarm_healthy=lambda:healthy,
    )
    return out,events

class RestartProtectionTests(unittest.TestCase):
    def test_unprotected_position_without_stop_blocks(self):
        v=FakeVenue("79")
        r,events=report(v,records(stop=False))
        self.assertFalse(r["entries_allowed"])
        self.assertEqual(r["protection"][SCOPE],"UNPROTECTED_NO_STOP_SENT")
        self.assertTrue(any("MANUAL_CLOSE_REQUIRED" in x for x in events))
        self.assertEqual(v.sends,0)

    def test_flat_without_stop_is_not_misreported_protected(self):
        r,events=report(FakeVenue("0"),records(stop=False))
        self.assertEqual(r["protection"][SCOPE],"FLAT_NO_STOP_SENT")
        self.assertTrue(r["entries_allowed"])
        self.assertEqual(events,[])

    def test_protected_open_position_still_blocks_new_entry(self):
        r,events=report(FakeVenue("79"),records(stop=True),guard_result="PROTECTED")
        self.assertEqual(r["protection"][SCOPE],"PROTECTED")
        self.assertFalse(r["entries_allowed"])
        self.assertEqual(events,[])

    def test_missing_stop_evidence_blocks(self):
        r,_=report(FakeVenue("79"),records(stop=True),guard_result="AMBIGUOUS")
        self.assertFalse(r["entries_allowed"])

    def test_guard_network_timeout_blocks(self):
        def offline_timeout(*args,**kwargs):
            raise TimeoutError("SIMULATED_READ_FAILURE")
        events=[]
        r=rc.protection_gate(load_wal=lambda:records(stop=True),
            venue=FakeVenue("79"),guard_step=offline_timeout,journal=object(),
            alarm=events.append,sleep=lambda x:None,alarm_healthy=lambda:True)
        self.assertEqual(r["protection"][SCOPE],"GUARD_FAILED")
        self.assertFalse(r["entries_allowed"])
        self.assertTrue(any("RESTART_GUARD_FAILED" in x for x in events))

    def test_unreadable_wal_blocks(self):
        def broken_wal():
            raise ValueError("SIMULATED_CORRUPT_JSON")
        r=rc.protection_gate(load_wal=broken_wal,
            venue=FakeVenue(),guard_step=lambda *a,**k:"FLAT",
            journal=object(),alarm=lambda x:None,sleep=lambda x:None)
        self.assertEqual(r["wal"],"UNREADABLE")
        self.assertFalse(r["entries_allowed"])

    def test_ambiguous_wal_symbols_block(self):
        wal=records(stop=False)
        wal["TP1.json"]={"role":"TP1","detail":{"scope":SCOPE,"symbol":"OTHERUSDT",
                                                  "client_id":"FAKE-TP"}}
        r,_=report(FakeVenue(),wal)
        self.assertEqual(r["protection"][SCOPE],"AMBIGUOUS_WAL")
        self.assertFalse(r["entries_allowed"])

    def test_alarm_log_failure_blocks_even_if_flat(self):
        r,_=report(FakeVenue(),records(stop=True),guard_result="FLAT",healthy=False)
        self.assertIn("ALARM_LOG_UNHEALTHY",r["alarm_failures"])
        self.assertFalse(r["entries_allowed"])

    def test_restart_requires_startup_and_policy_clear(self):
        calls=[]
        r=rc.restart_launch(load_wal=lambda:records(stop=True),venue=FakeVenue(),
            guard_step=lambda *a,**k:"FLAT",journal=object(),alarm=lambda x:None,
            sleep=lambda x:None,startup_reconcile=lambda:calls.append("startup"),
            restart_reconcile=lambda:{"status":"RECONCILED"},
            alarm_healthy=lambda:True)
        self.assertEqual(calls,["startup"])
        self.assertTrue(r["entries_allowed"])
        r2=rc.restart_launch(load_wal=lambda:records(stop=True),venue=FakeVenue(),
            guard_step=lambda *a,**k:"FLAT",journal=object(),alarm=lambda x:None,
            sleep=lambda x:None,
            startup_reconcile=lambda:(_ for _ in ()).throw(RuntimeError("UNRESOLVED_WAL")),
            restart_reconcile=lambda:{"status":"RECONCILED"})
        self.assertFalse(r2["entries_allowed"])

    def test_resolved_wal_scope_not_replayed(self):
        r,_=report(FakeVenue(),records(stop=True,resolved=True))
        self.assertEqual(r["protection"],{})
        self.assertTrue(r["entries_allowed"])

class ContinuousCoverageTests(unittest.TestCase):
    def make_monitor(self,venue,states,alarm_events,observer=None):
        it=iter(states)
        def step(*args,**kwargs):return next(it)
        return rc.CoverageMonitor(venue=venue,guard_step=step,
            journal=object(),alarm=alarm_events.append,scope=SCOPE,symbol=SYMBOL,
            stop_id="FAKE-STOP",tp_ids=[],observer_status=observer or
            (lambda:{"status":"OK","generated_at_ms":venue.ms}),
            min_ticks=3,min_span_ms=10_000,alarm_healthy=lambda:True)

    def test_three_protected_ticks_verify_window_only(self):
        v=FakeVenue("79")
        alarms=[]
        mon=self.make_monitor(v,["PROTECTED"]*3,alarms)
        self.assertFalse(mon.tick()["continuous_stop_coverage_verified"])
        v.ms+=5_000
        self.assertFalse(mon.tick()["continuous_stop_coverage_verified"])
        v.ms+=5_000
        self.assertTrue(mon.tick()["continuous_stop_coverage_verified"])
        self.assertEqual(v.sends,0)

    def test_missing_stop_breaks_verified_window(self):
        v=FakeVenue("79")
        alarm=[]
        mon=self.make_monitor(v,["PROTECTED","AMBIGUOUS","PROTECTED"],alarm)
        mon.tick()
        v.ms+=5_000
        second=mon.tick()
        v.ms+=5_000
        third=mon.tick()
        self.assertFalse(second["continuous_stop_coverage_verified"])
        self.assertFalse(third["continuous_stop_coverage_verified"])
        self.assertIn("AMBIGUOUS",third["broken"])
        self.assertEqual(v.sends,0)

    def test_large_gap_breaks_window(self):
        v=FakeVenue("79")
        alarms=[]
        mon=self.make_monitor(v,["PROTECTED"]*2,alarms)
        mon.tick()
        v.ms+=16_000
        r=mon.tick()
        self.assertFalse(r["continuous_stop_coverage_verified"])
        self.assertIn("GAP",r["broken"])
        self.assertIn("COVERAGE_GAP_MS_EXCEEDED",alarms)

    def test_stop_reader_timeout_breaks_window(self):
        v=FakeVenue("79")
        alarms=[]
        def fail(*a,**k):raise TimeoutError("NO_NETWORK")
        monitor=rc.CoverageMonitor(venue=v,guard_step=fail,
            journal=object(),alarm=alarms.append,scope=SCOPE,symbol=SYMBOL,
            stop_id="FAKE-STOP",tp_ids=[],observer_status=lambda:{
                "status":"OK","generated_at_ms":v.ms},max_read_failures=1)
        r=monitor.tick()
        self.assertFalse(r["continuous_stop_coverage_verified"])
        self.assertTrue(any("COVERAGE_LOST_READS_OR_STOP" in x for x in alarms))

    def test_signed_flat_ends_supervision_in_test(self):
        v=FakeVenue("0")
        alarm=[]
        monitor=self.make_monitor(v,["PROTECTED","FLAT"],alarm)
        states=[monitor.tick()]
        v.ms+=5_000
        r=rc.supervise(monitor,sleep=lambda x:None,max_ticks=1)
        self.assertEqual(r["state"],"FLAT")
        self.assertEqual(v.sends,0)

if __name__=="__main__":
    unittest.main()
