"""Synthetic signal observability regression tests: no private log dependency."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from signal_observability_offline import bounded_last_jsonl,summary,SignalLogError

class SignalObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix="atlas-signal-observe-")
        self.file=Path(self.temp.name)/"events.jsonl"
    def tearDown(self):
        self.temp.cleanup()
    def put(self,rows):
        self.file.write_text("".join(json.dumps(r)+"\n" for r in rows))
    def test_last_n_rows(self):
        self.put([{"status":"WAIT","seq":i} for i in range(8)])
        self.assertEqual([r["seq"] for r in bounded_last_jsonl(self.file,max_rows=3)],
                         [5,6,7])
    def test_partial_first_line_discarded(self):
        self.put([{"status":"WAIT","seq":i,"padding":"a"*190} for i in range(20)])
        rows=bounded_last_jsonl(self.file,max_rows=20,max_bytes=1024)
        self.assertGreater(len(rows),1)
        self.assertEqual(rows[-1]["seq"],19)
    def test_symlink_refused(self):
        self.put([{"status":"WAIT"}])
        symlink=Path(self.temp.name)/"link"
        symlink.symlink_to(self.file)
        with self.assertRaisesRegex(SignalLogError,"SYMLINK"):
            bounded_last_jsonl(symlink)
    def test_invalid_json_refused(self):
        self.file.write_text("{bad\n")
        with self.assertRaisesRegex(SignalLogError,"LOG_ROW_INVALID"):
            bounded_last_jsonl(self.file)
    def test_wait_not_conflated_with_executor_exit(self):
        result=summary(
            [{"status":"WAIT","generated_at":"2026-11-05T13:00:00+03:00",
              "mode":"DRY_RUN_ONLY","blockers":["NO_CONFIRMED_SIGNAL"]}],
            [{"status":"PHONE_APPROVED_EXECUTOR_STARTED","sig_symbol":"AINUSDT"},
             {"status":"EXECUTOR_EXITED_OWNER_REVIEW",
              "sig_symbol":"AINUSDT","exit_code":0}])
        self.assertEqual(result["status_counts"],{"WAIT":1})
        self.assertEqual(result["blocker_counts"],{"NO_CONFIRMED_SIGNAL":1})
        self.assertEqual(result["handoff_exits_in_sample"][0]["exit_code"],0)
        self.assertIs(result["exchange_execution_verified"],False)
    def test_invalid_blocker_format_is_not_accepted(self):
        result=summary([{"status":"WAIT","blockers":"NO_CONFIRMED_SIGNAL"}],[])
        self.assertEqual(result["blocker_counts"],{"UNVERIFIED_BLOCKER_FORMAT":1})
    def test_empty_logs_are_explicit(self):
        self.put([])
        self.assertEqual(bounded_last_jsonl(self.file),[])
        self.assertEqual(summary([],[])["evaluations"],0)

if __name__=="__main__":
    unittest.main()
