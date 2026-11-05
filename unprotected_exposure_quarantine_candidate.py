"""OFFLINE ONLY: fail-closed quarantine response to unprotected exposure.

Not connected to Owner runtime, exchange, STOP sender, emergency flatten or
authorization. This is a persistence and state-contract diagnostic ONLY.
"""
from emergency_latch_candidate import (
    EmergencyLatchBlocked,publish_alarm_latch,require_no_emergency_latch)

class QuarantineNotDurable(RuntimeError):
    pass

def quarantine_unprotected_exposure(directory, *, scope, exposure_state,
                                    stop_state, failure, expected_uid=None):
    """Persist no-entry owner-review latch if exposure/STOP is unverified.

    'UNKNOWN' exposure is never treated as FLAT. Return ONLY no-entry states.
    Existing latch is not revalidated as reliable evidence, so callers cannot
    treat ALREADY_LATCHED as proof of a fresh write.
    """
    if exposure_state not in ("NONZERO","UNKNOWN") or type(exposure_state) is not str:
        raise QuarantineNotDurable("EXPOSURE_NOT_AN_UNPROTECTED_SCENARIO")
    if stop_state not in ("ABSENT","UNKNOWN","UNVERIFIED") or type(stop_state) is not str:
        raise QuarantineNotDurable("STOP_NOT_AN_UNPROTECTED_SCENARIO")
    if failure not in ("WAL_DEADLINE","STOP_READBACK_UNKNOWN",
                       "STOP_POST_UNKNOWN","WAL_IO_FAILED"):
        raise QuarantineNotDurable("FAILURE_REASON_NOT_ALLOWED")
    try:
        status=publish_alarm_latch(
            directory,scope=scope,
            reason="UNPROTECTED_"+exposure_state+"_"+stop_state+"_"+failure,
            expected_uid=expected_uid)
    except Exception as exc:
        raise QuarantineNotDurable("ALARM_PERSISTENCE_FAILED_HALT_ESCALATE") from exc
    # Both LATCHED and ALREADY_LATCHED must block entry, even corrupt content.
    try:
        require_no_emergency_latch(directory,expected_uid=expected_uid)
    except EmergencyLatchBlocked as exc:
        if "UNRESOLVED_EMERGENCY_LATCH" not in str(exc):
            raise QuarantineNotDurable("ALARM_RECHECK_FAILED_HALT_ESCALATE") from exc
    except Exception as exc:
        raise QuarantineNotDurable("ALARM_RECHECK_FAILED_HALT_ESCALATE") from exc
    else:
        raise QuarantineNotDurable("ALARM_VANISHED_HALT_ESCALATE")
    return {"decision":"NO_ENTRY_MANUAL_OWNER_REVIEW",
            "entry_authorized":False,
            "stop_protected":False,
            "position_flat_verified":False,
            "automatic_retry_allowed":False,
            "new_latch_persisted":status=="LATCHED",
            "previous_latch_detected":status=="ALREADY_LATCHED"}
