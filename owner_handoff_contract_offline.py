"""Offline-only contract reconciliation for the running Owner handoff telemetry.

This is not a trading API and does not change the live controller.
"""
from datetime import datetime

class OwnerHandoffContractError(ValueError): pass

def classify_handoff(snapshot, *, now, max_age_seconds=25):
    if type(snapshot) is not dict or not isinstance(now,datetime) or now.tzinfo is None:
        raise OwnerHandoffContractError("UNTRUSTED_HANDOFF_INPUT")
    try:
        ts=datetime.fromisoformat(snapshot["generated_at"])
        age=(now-ts).total_seconds()
    except (KeyError,TypeError,ValueError):
        raise OwnerHandoffContractError("UNVERIFIED_HANDOFF_TIMESTAMP") from None
    if ts.tzinfo is None or not 0<=age<=max_age_seconds:
        raise OwnerHandoffContractError("STALE_HANDOFF")
    if snapshot.get("schema")!="ATLAS_OWNER_CONTINUOUS_SIGNAL_HANDOFF_V1" or snapshot.get("owner_uid")!=501:
        raise OwnerHandoffContractError("OWNER_HANDOFF_IDENTITY_UNVERIFIED")
    click=snapshot.get("owner_native_trade_click_required")
    phone=snapshot.get("phone_live_order_release_enabled")
    if type(click) is not bool or type(phone) is not bool:
        raise OwnerHandoffContractError("INVALID_APPROVAL_ROUTE_FIELDS")
    if snapshot.get("unattended_order_post_permission") is not False:
        raise OwnerHandoffContractError("UNATTENDED_ORDER_PERMISSION_UNSAFE")
    if snapshot.get("unattended_independent_stop_close_allowed") is not False:
        raise OwnerHandoffContractError("UNATTENDED_CLOSE_PERMISSION_UNSAFE")
    status=snapshot.get("status")
    if status not in ("WAITING_ELIGIBLE_SIGNAL","WAITING_OWNER_APPROVAL","BLOCKED_OR_WAITING"):
        raise OwnerHandoffContractError("UNKNOWN_HANDOFF_STATUS")
    return {
        "status":"OWNER_HANDOFF_OBSERVED",
        "handoff_running":True,
        "approval_route":"PHONE" if phone and not click else "NATIVE_CLICK" if click and not phone else "AMBIGUOUS" if phone and click else "NO_APPROVAL_ROUTE",
        "signal_handoff_status":status,
        "exact_owner_grant_verified":False,
        "live_sender_connected":False,
        "binance_order_post_allowed":False,
        "live_order_authorized":False,
    }
