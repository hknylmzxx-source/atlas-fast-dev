"""Offline-only restart gate composition with emergency alarm latch.

Never authorize trades: this function returns an observational result only.
Caller must separately maintain all Owner live prerequisites and global locks.
"""
from emergency_latch_candidate import require_no_emergency_latch

class StartupDiagnosticBlocked(RuntimeError):
    pass

def verify_restart_diagnostic(directory, *, legacy_gate, expected_uid=None):
    """Run independently fail-closed checks before/after existing startup gate.

    Re-reading reduces (does not eliminate) an alarm-to-entry TOCTOU gap.
    This function MUST NOT be treated as proof of atomic live entry release.
    """
    if not callable(legacy_gate):
        raise StartupDiagnosticBlocked("LEGACY_GATE_MISSING")
    try:
        require_no_emergency_latch(directory,expected_uid=expected_uid)
    except Exception:
        raise StartupDiagnosticBlocked("EMERGENCY_LATCH_PRESENT_OR_UNREADABLE") from None
    try:
        outcome=legacy_gate()
    except Exception:
        raise StartupDiagnosticBlocked("LEGACY_STARTUP_GATE_BLOCKED") from None
    if outcome is not True:
        raise StartupDiagnosticBlocked("LEGACY_STARTUP_GATE_NOT_TRUE")
    try:
        require_no_emergency_latch(directory,expected_uid=expected_uid)
    except Exception:
        raise StartupDiagnosticBlocked("EMERGENCY_LATCH_APPEARED_DURING_STARTUP") from None
    return {"status":"DIAGNOSTIC_CLEAR_AT_READ_ONLY",
            "entry_authorized":False,
            "atomic_release_verified":False}
