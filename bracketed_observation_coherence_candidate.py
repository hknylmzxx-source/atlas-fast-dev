"""OFFLINE ONLY: bracketed synthetic account/order observation coherence.

Position-before, normal orders, algo orders, position-after must be ordered
within a bounded local monotonic window. This is only a data consistency
diagnostic, not an atomic Binance snapshot or live-entry permission.
"""
from decimal import Decimal,InvalidOperation

STEPS=("account_before","normal_orders","algo_orders","account_after")

def assess_bracketed_observation(*, reads, symbol, now_ms,
                                 max_window_ms=2000,max_age_ms=5000):
    reasons=[]
    if (type(symbol) is not str or not symbol or type(now_ms) is not int
        or type(max_window_ms) is not int or not 0<max_window_ms<=10000
        or type(max_age_ms) is not int or not 0<max_age_ms<=10000):
        raise ValueError("COHERENCE_CONTEXT_INVALID")
    if type(reads) is not dict or set(reads)!=set(STEPS):
        reasons.append("READ_SEQUENCE_INCOMPLETE")
        reads={} if type(reads) is not dict else reads
    parsed=[]
    req_ids=[]
    for role in STEPS:
        row=reads.get(role)
        if type(row) is not dict:
            reasons.append(role.upper()+"_READ_MISSING")
            continue
        if row.get("symbol")!=symbol:
            reasons.append(role.upper()+"_SYMBOL_MISMATCH")
        start=row.get("start_ms");end=row.get("end_ms")
        if (type(start) is not int or type(end) is not int
            or start>end or end>now_ms or now_ms-end>max_age_ms
            or start<0):
            reasons.append(role.upper()+"_TIME_INVALID")
        else:
            parsed.append((role,start,end))
        ident=row.get("request_id")
        if type(ident) is not str or not ident:
            reasons.append(role.upper()+"_REQUEST_ID_MISSING")
        else:
            req_ids.append(ident)
        if role in ("account_before","account_after"):
            pos=row.get("positions")
            if type(pos) is not list or not pos:
                reasons.append(role.upper()+"_POSITIONS_MISSING")
            else:
                values=[]
                for item in pos:
                    if type(item) is not dict or item.get("symbol")!=symbol:
                        reasons.append(role.upper()+"_POSITION_SYMBOL_INVALID")
                        continue
                    value=item.get("positionAmt")
                    if type(value) is not str:
                        reasons.append(role.upper()+"_POSITION_INVALID")
                        continue
                    try:
                        q=Decimal(value)
                    except InvalidOperation:
                        reasons.append(role.upper()+"_POSITION_INVALID")
                        continue
                    if not q.is_finite():
                        reasons.append(role.upper()+"_POSITION_INVALID")
                    else:
                        values.append(q)
                if not values:
                    reasons.append(role.upper()+"_TARGET_POSITION_NOT_OBSERVED")
                elif any(v!=0 for v in values):
                    reasons.append(role.upper()+"_NONZERO_POSITION")
        elif type(row.get("orders")) is not list:
            reasons.append(role.upper()+"_ORDERS_MISSING")
        elif row["orders"]:
            reasons.append(role.upper()+"_ORDERS_NOT_EMPTY")
    if len(req_ids)!=len(STEPS) or len(set(req_ids))!=len(STEPS):
        reasons.append("REQUEST_IDS_NOT_UNIQUE")
    if len(parsed)==len(STEPS):
        for (_,_,end),(_,start,_) in zip(parsed,parsed[1:]):
            if end>start:
                reasons.append("READ_INTERVALS_OVERLAP_OR_OUT_OF_ORDER")
        if parsed[-1][2]-parsed[0][1]>max_window_ms:
            reasons.append("READ_WINDOW_TOO_WIDE")
    return {
        "synthetic_read_sequence_consistent":not reasons,
        "reasons":sorted(set(reasons)),
        "authenticated_signed_binance_reads_verified":False,
        "atomic_exchange_snapshot_verified":False,
        "no_intervening_exchange_change_proven":False,
        "entry_authorized":False,
        "may_clear_latch":False,
        "decision":"OWNER_REVIEW_REQUIRED_NO_RELEASE",
    }
