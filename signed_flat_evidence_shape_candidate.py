"""OFFLINE ONLY: inspect synthetic exchange observation bundle consistency.

A caller's source label or data is NEVER proof of authenticated signed
Binance GET. This module cannot authorize ENTRY or release quarantine.
"""
from decimal import Decimal,InvalidOperation

class EvidenceShapeBlocked(RuntimeError):
    pass

def _amount(v):
    if type(v) is not str:
        raise EvidenceShapeBlocked("POSITION_AMOUNT_NOT_STRING")
    try:
        d=Decimal(v)
    except InvalidOperation:
        raise EvidenceShapeBlocked("POSITION_AMOUNT_INVALID") from None
    if not d.is_finite():
        raise EvidenceShapeBlocked("POSITION_AMOUNT_NONFINITE")
    return d

def inspect_synthetic_flat_observation(*, observation, symbol, now_ms,
                                       max_age_ms=5000):
    """Syntactic/temporal checks only. No SDK or cryptographic verification."""
    reasons=[]
    if type(symbol) is not str or not symbol or type(now_ms) is not int:
        raise EvidenceShapeBlocked("REVIEW_CONTEXT_INVALID")
    if type(max_age_ms) is not int or not 0<max_age_ms<=10000:
        raise EvidenceShapeBlocked("REVIEW_AGE_LIMIT_INVALID")
    if type(observation) is not dict:
        reasons.append("OBSERVATION_MISSING")
        observation={}
    t=observation.get("captured_at_ms")
    if type(t) is not int or t>now_ms or now_ms-t>max_age_ms:
        reasons.append("OBSERVATION_STALE_OR_INVALID_CLOCK")
    if observation.get("symbol")!=symbol:
        reasons.append("SYMBOL_MISMATCH")
    ids=observation.get("read_request_ids")
    if (type(ids) is not dict
            or set(ids)!={"account","normal_orders","algo_orders"}
            or any(type(v) is not str or not v for v in ids.values())
            or len(set(ids.values()))!=3):
        reasons.append("SIGNED_READ_PROVENANCE_NOT_ESTABLISHED")
    positions=observation.get("positions")
    if type(positions) is not list or not positions:
        reasons.append("POSITIONS_MISSING")
    else:
        matches=[]
        for row in positions:
            if type(row) is not dict or row.get("symbol")!=symbol:
                reasons.append("POSITION_ROW_SYMBOL_INVALID")
                continue
            try:
                matches.append(_amount(row.get("positionAmt")))
            except EvidenceShapeBlocked:
                reasons.append("POSITION_AMOUNT_INVALID")
        if not matches:
            reasons.append("TARGET_POSITION_NOT_OBSERVED")
        elif any(value!=0 for value in matches):
            reasons.append("NONZERO_POSITION")
    for key in ("normal_orders","algo_orders"):
        orders=observation.get(key)
        if type(orders) is not list:
            reasons.append(key.upper()+"_MISSING")
        elif len(orders):
            reasons.append(key.upper()+"_NOT_EMPTY")
    return {
        "schema_flat_candidate":len(reasons)==0,
        "caller_claims_unverified":True,
        "reasons":sorted(set(reasons)),
        "authenticated_signed_get_verified":False,
        "position_flat_independently_verified":False,
        "entry_authorized":False,
        "may_clear_latch":False,
        "decision":"MANUAL_OWNER_REVIEW_NO_RELEASE",
    }
