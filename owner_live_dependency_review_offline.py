"""ATLAS FAST offline read-only release dependency diagnostic.

A structurally sound observation is NOT proof of owner approval, active STOP,
emergency flatten or executable Binance write authority. This tool never
authorizes a release and never imports/executes the live Owner SDK.
"""
from datetime import datetime
from pathlib import Path
import json

class ReadinessInputBlocked(ValueError):
    pass

REQUIRED=(
 "owner_sdk_rpc_connected",
 "owner_executor_installed_verified",
 "owner_sdk_execution_handoff_verified",
 "owner_native_approval_execution_connected",
 "human_trade_approval_verified",
 "continuous_stop_watch_connected",
 "stop_live_protection_verified",
 "signed_emergency_flatten_connected",
 "live_sender_connected",
)
BLOCKERS={
 "owner_sdk_rpc_connected":"OWNER_RPC_NOT_CONNECTED",
 "owner_executor_installed_verified":"OWNER_EXECUTOR_NOT_VERIFIED",
 "owner_sdk_execution_handoff_verified":"EXECUTION_HANDOFF_NOT_VERIFIED",
 "owner_native_approval_execution_connected":"OWNER_APPROVAL_ROUTE_NOT_CONNECTED",
 "human_trade_approval_verified":"OWNER_EXACT_APPROVAL_NOT_VERIFIED",
 "continuous_stop_watch_connected":"CONTINUOUS_STOP_WATCH_NOT_CONNECTED",
 "stop_live_protection_verified":"EXCHANGE_STOP_COVERAGE_NOT_VERIFIED",
 "signed_emergency_flatten_connected":"EMERGENCY_FLATTEN_NOT_CONNECTED",
 "live_sender_connected":"LIVE_SENDER_NOT_CONNECTED",
}

def inspect_bridge_state(bridge, *, observed_at, max_age_seconds=60):
    if type(bridge) is not dict or type(observed_at) is not datetime or observed_at.tzinfo is None:
        raise ReadinessInputBlocked("INVALID_REVIEW_INPUT")
    if not (type(max_age_seconds) in (int,float) and 0<max_age_seconds<=120):
        raise ReadinessInputBlocked("INVALID_STALENESS_BOUND")
    issues=[]
    try:
        seen=datetime.fromisoformat(bridge["generated_at"])
        age=(observed_at-seen).total_seconds()
        if seen.tzinfo is None or age<0 or age>max_age_seconds:
            issues.append("BRIDGE_STATUS_STALE_OR_FUTURE")
    except (KeyError,TypeError,ValueError):
        issues.append("BRIDGE_TIMESTAMP_UNVERIFIED")
    if bridge.get("mode")!="DRY_RUN_ONLY":
        issues.append("BRIDGE_MODE_NOT_EXPECTED")
    if bridge.get("orders_sent") is not False:
        issues.append("ORDER_ACTIVITY_UNVERIFIED")
    for field in REQUIRED:
        if bridge.get(field) is not True:
            issues.append(BLOCKERS[field])
    # A status report is untrusted telemetry; never allow any claimed True
    # flags to remove the production NO-GO.
    return {"status":"OFFLINE_RELEASE_DEPENDENCY_REVIEW",
            "missing_controls":sorted(set(issues)),
            "all_claimed_controls_present":not issues,
            "owner_evidence_independently_authenticated":False,
            "exchange_stop_independently_verified":False,
            "emergency_close_independently_verified":False,
            "live_order_authorized":False,
            "deployment_permitted":False}

def inspect_status_file(path, *, observed_at):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size>65536:
        raise ReadinessInputBlocked("UNTRUSTED_STATUS_FILE")
    with p.open(encoding="utf-8") as f:
        return inspect_bridge_state(json.load(f),observed_at=observed_at)
