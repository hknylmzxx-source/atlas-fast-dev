"""Static source audit regression: no production imports, execution or IO writes."""
import ast
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wal_marker_static_audit import audit_owner_source

SOURCE=Path(__file__).resolve().parents[1]/"owner_single_execution.py"

class WalMarkerAuditTests(unittest.TestCase):
    def setUp(self):
        self.report=audit_owner_source(SOURCE)
    def test_all_three_unfenced_release_callers_are_in_scope(self):
        self.assertEqual(set(self.report["marker_callers"]),{
            "risk_ledger_entry_gate","_restart_one","run_once"})
    def test_no_unreviewed_new_marker_caller(self):
        self.assertEqual(self.report["unexpected_marker_callers"],[])
        self.assertEqual(self.report["expected_marker_callers_missing"],[])
    def test_zero_entry_auto_marker_stays_disabled(self):
        self.assertEqual(self.report["zero_entry_marker_call_count"],0)
    def test_no_false_claim_shared_fence_is_wired(self):
        self.assertIs(self.report["owner_imports_shared_wal_fence"],False)
        self.assertIs(self.report["marker_has_explicit_fence_context"],False)
    def test_no_false_claim_alarm_startup_is_wired(self):
        self.assertIs(self.report["startup_scans_emergency_latch"],False)
        self.assertIs(self.report["owner_imports_restart_alarm_candidate"],False)
    def test_release_classification_is_no_go(self):
        self.assertFalse(self.report["live_release_allowed"])
        self.assertEqual(self.report["assessment"],
                         "NO_GO_REVIEW_UNPROVEN_WRITER_ATOMICITY")
    def test_all_three_callers_reach_the_same_fail_closed_marker(self):
        tree=ast.parse(SOURCE.read_text())
        funcs={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
        marker=funcs["mark_scope_resolved"]
        self.assertFalse(any(isinstance(n,ast.Call)
                             and isinstance(n.func,ast.Attribute)
                             and n.func.attr in ("open","write","fsync")
                             for n in ast.walk(marker)))
        self.assertTrue(any(isinstance(n,ast.Constant)
                            and n.value=="WAL_RESOLUTION_MARKER_DISABLED_SHARED_FENCE_UNPROVEN"
                            for n in ast.walk(marker)))
        self.assertEqual(set(self.report["marker_callers"]),{
            "risk_ledger_entry_gate","_restart_one","run_once"})

    def test_marker_fail_closed_in_isolated_ast_without_filesystem(self):
        tree=ast.parse(SOURCE.read_text())
        marker=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                    and n.name=="mark_scope_resolved")
        scope={}
        class Blocked(Exception):
            pass
        exec(compile(ast.Module(body=[marker],type_ignores=[]),
                     "<offline-marker-source>","exec"),{"Blocked":Blocked},scope)
        with self.assertRaisesRegex(Blocked,"SHARED_FENCE_UNPROVEN"):
            scope["mark_scope_resolved"]("TEST_SCOPE","TEST_SETTLED")

    def test_read_only_audit_supports_synthetic_source(self):
        sample=("def mark_scope_resolved():\n    pass\n"
                "def startup_reconcile():\n    pass\n"
                "def _resolve_verified_zero_entry_scope():\n    pass\n"
                "def risk_ledger_entry_gate():\n    mark_scope_resolved()\n"
                "def _restart_one():\n    mark_scope_resolved()\n"
                "def run_once():\n    mark_scope_resolved()\n")
        with tempfile.TemporaryDirectory() as p:
            path=Path(p)/"sample.py"
            path.write_text(sample)
            report=audit_owner_source(path)
            self.assertEqual(len(report["marker_callers"]),3)
            self.assertEqual(report["zero_entry_marker_call_count"],0)

if __name__=="__main__":
    unittest.main()
