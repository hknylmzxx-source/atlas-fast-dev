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
    def test_dev_startup_explicitly_blocks_emergency_latch(self):
        self.assertIs(self.report["startup_scans_emergency_latch"],True)
        # Only direct source-level blocking is wired. The two-read prototype
        # and live atomic-entry protection are still NOT installed.
        self.assertIs(self.report["owner_imports_restart_alarm_candidate"],False)
    def test_release_classification_is_no_go(self):
        self.assertFalse(self.report["live_release_allowed"])
        self.assertEqual(self.report["assessment"],
                         "NO_GO_REVIEW_UNPROVEN_WRITER_ATOMICITY")
    def test_all_three_callers_reach_the_same_fenced_marker(self):
        # G55/Faz 3: işaret doğrudan dosya yazmaz; yalnız ortak fence'li resolver'a delege eder.
        tree=ast.parse(SOURCE.read_text())
        funcs={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
        marker=funcs["mark_scope_resolved"]
        self.assertFalse(any(isinstance(n,ast.Call)
                             and isinstance(n.func,ast.Attribute)
                             and n.func.attr in ("open","write","fsync")
                             for n in ast.walk(marker)))
        self.assertEqual(sum(isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                             and n.func.id=="resolve_settled_scope"
                             for n in ast.walk(marker)),1)
        self.assertEqual(set(self.report["marker_callers"]),{
            "risk_ledger_entry_gate","_restart_one","run_once"})

    def test_marker_fail_closed_in_isolated_ast_without_filesystem(self):
        # Resolver modülü yoksa / hata verirse işaret Blocked fırlatır (fail-closed).
        tree=ast.parse(SOURCE.read_text())
        marker=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                    and n.name=="mark_scope_resolved")
        class Blocked(Exception):
            pass
        import builtins
        real_import=builtins.__import__
        def no_resolver(name,*a,**k):
            if name=="wal_owner_write_adapter_candidate":
                raise ImportError("SYNTHETIC_MISSING")
            return real_import(name,*a,**k)
        scope={}
        exec(compile(ast.Module(body=[marker],type_ignores=[]),
                     "<offline-marker-source>","exec"),
             {"Blocked":Blocked,"LOG":Path("/nonexistent-g55"),
              "__builtins__":dict(vars(builtins),__import__=no_resolver)},scope)
        with self.assertRaisesRegex(Blocked,"WAL_RESOLUTION_MARKER_NOT_WRITTEN"):
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
