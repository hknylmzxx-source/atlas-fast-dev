"""Read-only static audit of Owner WAL marker/write pathways.

This diagnostic never imports or executes the Owner executor. It only parses
the explicit source file. Results are not authorization for live trading.
"""
from __future__ import annotations
import ast
from pathlib import Path

EXPECTED_MARKER_CALLERS=frozenset({
    "risk_ledger_entry_gate","_restart_one","run_once",
})
EXPECTED_WAL_WRITERS=frozenset({
    "journal","emergency_note","write_policy_reserve_record",
    "mark_scope_resolved",
})

def _calls(fn,name):
    return [n.lineno for n in ast.walk(fn)
            if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Name)
            and n.func.id==name]

def audit_owner_source(path):
    tree=ast.parse(Path(path).read_text())
    functions={x.name:x for x in tree.body if isinstance(x,ast.FunctionDef)}
    marker_callers={}
    for name,fn in functions.items():
        matches=_calls(fn,"mark_scope_resolved")
        if matches:
            marker_callers[name]=matches
    missing=sorted(EXPECTED_MARKER_CALLERS-set(marker_callers))
    unexpected=sorted(set(marker_callers)-EXPECTED_MARKER_CALLERS)
    marker_fn=functions["mark_scope_resolved"]
    startup_fn=functions["startup_reconcile"]
    zero_fn=functions["_resolve_verified_zero_entry_scope"]
    emergency_imports=[
        n.module for n in ast.walk(tree)
        if isinstance(n,ast.ImportFrom) and n.module and
        "emergency_restart_diagnostic" in n.module
    ]
    # A marker writes to WAL, but the source doesn't use the candidate fence.
    candidate_imports=[
        n.module for n in ast.walk(tree)
        if isinstance(n,ast.ImportFrom) and n.module and
        "wal_writer_fence_candidate" in n.module
    ]
    return {
        "marker_callers":marker_callers,
        "expected_marker_callers_missing":missing,
        "unexpected_marker_callers":unexpected,
        "zero_entry_marker_call_count":len(_calls(zero_fn,"mark_scope_resolved")),
        "startup_scans_emergency_latch":any(
            isinstance(n,ast.Constant) and isinstance(n.value,str)
            and "emergency_latch_" in n.value
            for n in ast.walk(startup_fn)),
        "owner_imports_restart_alarm_candidate":bool(emergency_imports),
        "owner_imports_shared_wal_fence":bool(candidate_imports),
        "marker_has_explicit_fence_context":any(
            isinstance(n,ast.With)
            and any(isinstance(item.context_expr,ast.Call)
                    and isinstance(item.context_expr.func,ast.Name)
                    and item.context_expr.func.id=="writer_fence"
                    for item in n.items)
            for n in ast.walk(marker_fn)),
        "writers_to_review":sorted(EXPECTED_WAL_WRITERS),
        "live_release_allowed":False,
        "assessment":"NO_GO_REVIEW_UNPROVEN_WRITER_ATOMICITY",
    }
