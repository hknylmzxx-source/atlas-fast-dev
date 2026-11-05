"""Offline-only quarantine review evidence assessor, never a release mechanism.

Does not clear files, change Owner state, or authorize an entry. It strictly
describes which independently reviewed evidence is still absent.
"""
from pathlib import Path
import json
from emergency_latch_candidate import require_no_emergency_latch,EmergencyLatchBlocked

REQUIRED=(
    "owner_review_authenticated",
    "signed_position_flat",
    "signed_normal_orders_empty",
    "signed_algo_orders_empty",
    "owned_stop_tp_final_state_verified",
    "policy_ledger_settled",
    "wal_integrity_reviewed",
    "unresolved_incidents_reviewed",
    "continuous_stop_gap_reviewed",
    "emergency_action_outcome_reviewed",
)
def assess_quarantine_review(directory, *, evidence):
    """Pure fail-closed advisory. No value here ever grants entry."""
    if type(evidence) is not dict:
        return {"decision":"OWNER_REVIEW_EVIDENCE_MISSING",
                "missing":list(REQUIRED),
                "entry_authorized":False,"may_clear_latch":False}
    missing=[field for field in REQUIRED if evidence.get(field) is not True
             or type(evidence.get(field)) is not bool]
    # Caller-provided booleans are NOT verified signed Binance evidence.
    # Even with all of them present, no release/clearance decision is issued.
    try:
        require_no_emergency_latch(directory)
        latch_present=False
    except EmergencyLatchBlocked:
        latch_present=True
    except Exception:
        latch_present=True
        if "wal_integrity_reviewed" not in missing:
            missing.append("wal_integrity_reviewed")
    return {"decision":("OWNER_MANUAL_VERIFICATION_REQUIRED"
                         if not missing else "OWNER_REVIEW_EVIDENCE_MISSING"),
            "missing":missing,
            "emergency_latch_detected_or_unreadable":latch_present,
            "entry_authorized":False,
            "may_clear_latch":False,
            "signed_exchange_evidence_independently_verified":False}
