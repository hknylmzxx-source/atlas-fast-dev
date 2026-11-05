"""Offline STOP/TP WAL fault injection against actual selected Owner functions.

All transport, clocks and WAL paths are synthetic. No production imports.
"""
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tests"))
from test_safety_chain_integration import (
    Integration,Blocked,PreSendRefused,ENTRY,STOP,SCOPE,SYM)

class ProtectiveWalFaultTests(unittest.TestCase):
    def setUp(self):
        self.s=Integration()
        self.s.reserve()
    def tearDown(self):
        self.s.close()

    def _refuse_journal(self,*args,**kwargs):
        raise OSError("OFFLINE_SYNTHETIC_WAL_UNAVAILABLE")

    def test_stop_journal_failure_never_posts(self):
        s=self.s
        s.ns["journal"]=self._refuse_journal
        with self.assertRaisesRegex(PreSendRefused,"OFFLINE_SYNTHETIC_WAL_UNAVAILABLE"):
            s.ns["place_stop_write"](s.venue,STOP,SCOPE,sleep=lambda _:None)
        self.assertEqual(s.venue.post_calls,[])
        self.assertEqual(s.venue.lease,0)

    def test_entry_journal_failure_never_posts(self):
        s=self.s
        s.ns["journal"]=self._refuse_journal
        with self.assertRaisesRegex(PreSendRefused,"OFFLINE_SYNTHETIC_WAL_UNAVAILABLE"):
            s.ns["one_write"](s.venue,"ENTRY",ENTRY,SCOPE,owner_approved_at=100)
        self.assertEqual(s.venue.post_calls,[])
        self.assertEqual(s.venue.lease,0)

    def test_stop_unknown_post_has_one_attempt_and_retains_wal(self):
        s=self.s
        s.venue.post_error=TimeoutError("SYNTHETIC_STOP_UNCERTAIN")
        with self.assertRaisesRegex(Blocked,"UNKNOWN_WRITE_RESULT_DO_NOT_RETRY_STOP"):
            s.ns["place_stop_write"](s.venue,STOP,SCOPE,sleep=lambda _:None)
        self.assertEqual(len(s.venue.post_calls),1)
        self.assertEqual(s.venue.lease,0)
        wal=[x for x in s.wal.iterdir() if x.name.startswith("STOP_")]
        self.assertEqual(len(wal),1)

    def test_tp_unknown_post_has_one_attempt_and_retains_wal(self):
        s=self.s
        s.venue.post_error=TimeoutError("SYNTHETIC_TP_UNCERTAIN")
        wire={"symbol":SYM,"newClientOrderId":"OFFLINE-TP1-01"}
        with self.assertRaisesRegex(Blocked,"UNKNOWN_WRITE_RESULT_DO_NOT_RETRY_TP1"):
            s.ns["one_write"](s.venue,"TP1",wire,SCOPE)
        self.assertEqual(len(s.venue.post_calls),1)
        wal=[x for x in s.wal.iterdir() if x.name.startswith("TP1_")]
        self.assertEqual(len(wal),1)

    def test_stop_second_same_identity_refused_before_post(self):
        s=self.s
        s.ns["one_write"](s.venue,"STOP",STOP,SCOPE)
        self.assertEqual(len(s.venue.post_calls),1)
        # Repeating the same durable WAL identity must not POST twice.
        s.ns["journal"]=lambda role,digest,body: (
            self._refuse_journal() if (s.wal/(role+"_"+digest+".json")).exists()
            else s.journal(role,digest,body))
        with self.assertRaises(PreSendRefused):
            s.ns["one_write"](s.venue,"STOP",STOP,SCOPE)
        self.assertEqual(len(s.venue.post_calls),1)

    def test_restart_still_blocks_unresolved_stop_wal(self):
        s=self.s
        s.ns["one_write"](s.venue,"STOP",STOP,SCOPE)
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_WAL_SCOPE"):
            s.ns["startup_reconcile"]()

if __name__=="__main__":
    unittest.main()
