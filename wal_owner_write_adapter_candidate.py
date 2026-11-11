"""Legacy WAL adapters used by the G54 development owner copy.

Only the caller-supplied directory is used. No SDK, credentials, live
activation or resolver is invoked by the owner writer functions.
"""
from __future__ import annotations

import math
import time

from wal_writer_fence_candidate import (
    WalFenceBlocked, append_record, resolve_reserve_only, resolve_settled_scope as _resolve_settled,
    _safe,
)

_ORDER_ROLES=frozenset(("ENTRY","STOP","TP1","TP2","EMERGENCY",
                        "CANCEL_STOP","CANCEL_TP1","CANCEL_TP2"))

def _validate_owner_row(name,row):
    """Check persisted legacy bytes/schema inside the same append fence.

    Historical resolved rows may be read but never created by these writers.
    This is a single-scope legacy contract, not an epoch/replay authority.
    """
    if type(row) is not dict:
        raise WalFenceBlocked("OWNER_WAL_SCHEMA_INVALID")
    if name.startswith("resolved_"):
        scope=_safe(row.get("scope"),"SCOPE")
        if (name!="resolved_"+scope+".json"
                or type(row.get("outcome")) is not str or not row["outcome"]):
            raise WalFenceBlocked("OWNER_WAL_SCHEMA_INVALID")
        return
    at=row.get("at")
    if type(at) not in (int,float) or not math.isfinite(at) or at<0:
        raise WalFenceBlocked("OWNER_WAL_SCHEMA_INVALID")
    if name.startswith(("incident_","emergency_latch_")):
        scope=_safe(row.get("scope"),"SCOPE")
        prefix="incident_" if name.startswith("incident_") else "emergency_latch_"
        keys={"scope","at","reason","new_entry_permitted","retry_permitted"}
        if prefix=="emergency_latch_":keys.add("state")
        if (name!=prefix+scope+".json" or set(row)!=keys
                or type(row.get("reason")) is not str or not row["reason"]
                or row.get("new_entry_permitted") is not False
                or row.get("retry_permitted") is not False
                or (prefix=="emergency_latch_" and
                    row.get("state")!="MANUAL_OWNER_REVIEW_REQUIRED")):
            raise WalFenceBlocked("OWNER_WAL_SCHEMA_INVALID")
        return
    role=row.get("role")
    if type(role) is not str or role not in _ORDER_ROLES|{"POLICY_RESERVE"}:
        raise WalFenceBlocked("OWNER_WAL_ROLE_INVALID")
    identity=_safe(row.get("intent"),"INTENT")
    detail=row.get("detail")
    if (set(row)!={"role","intent","at","state","detail"}
            or name!=role+"_"+identity+".json" or type(detail) is not dict):
        raise WalFenceBlocked("OWNER_WAL_SCHEMA_INVALID")
    scope=_safe(detail.get("scope"),"SCOPE")
    required={"scope","symbol","client_id"}
    if role=="CANCEL_STOP" and "symbol" not in detail:required.remove("symbol")
    # Client IDs remain the legacy exchange identifiers, not filesystem names.
    if (set(detail)!=required or type(detail.get("client_id")) is not str
            or not 0<len(detail["client_id"])<=120):
        raise WalFenceBlocked("OWNER_WAL_SCHEMA_INVALID")
    if "symbol" in detail:_safe(detail["symbol"],"SYMBOL")
    state="RESERVATION_PENDING" if role=="POLICY_RESERVE" else "WRITE_OUTCOME_UNKNOWN_NO_RETRY"
    if row.get("state")!=state or (role=="POLICY_RESERVE" and identity!=scope):
        raise WalFenceBlocked("OWNER_WAL_SCHEMA_INVALID")

def append_order_journal(directory, *, role, identity, scope, symbol=None,
                         client_id=None, expected_uid=None, detail=None):
    """Preserve the original Owner journal's role/intent/detail contract."""
    if type(role) is not str or role not in _ORDER_ROLES:
        raise WalFenceBlocked("OWNER_WAL_ROLE_INVALID")
    _safe(identity,"INTENT")
    if detail is None:
        detail={"scope":scope,"symbol":symbol,"client_id":client_id}
    if type(detail) is not dict:
        raise WalFenceBlocked("OWNER_WAL_SCHEMA_INVALID")
    detail=dict(detail)  # Legacy fields are scalar; do not retain a caller's mutable dict.
    return append_record(
        directory,name=role+"_"+identity+".json",
        row={"role":role,"intent":identity,"at":time.time(),
             "state":"WRITE_OUTCOME_UNKNOWN_NO_RETRY",
             "detail":detail},
        scope=scope,expected_uid=expected_uid,validate_row=_validate_owner_row,
    )


def append_reservation(directory, *, scope, symbol, client_id,
                       expected_uid=None):
    """Preserve POLICY_RESERVE_<scope>.json, without changing policy ledger."""
    return append_record(
        directory,name="POLICY_RESERVE_"+scope+".json",
        row={"role":"POLICY_RESERVE","intent":scope,"at":time.time(),
             "state":"RESERVATION_PENDING",
             "detail":{"scope":scope,"symbol":symbol,"client_id":client_id}},
        scope=scope,expected_uid=expected_uid,validate_row=_validate_owner_row,
    )


def append_incident(directory, *, scope, reason, expected_uid=None,existing_ok=False):
    """Incident must be persisted or raise a blocking error; never ignore it."""
    if type(reason) is not str or not reason:
        raise WalFenceBlocked("INCIDENT_REASON_INVALID")
    return append_record(
        directory,name="incident_"+scope+".json",
        row={"scope":scope,"at":time.time(),"reason":reason,
             "new_entry_permitted":False,"retry_permitted":False},
        scope=scope,expected_uid=expected_uid,validate_row=_validate_owner_row,
        existing_ok=existing_ok,
    )


def resolve_zero_reservation_offline(directory, *, scope,symbol,client_id,
                                     proof,expected_uid=None):
    """Candidate atomic marker; proof=True is ONLY a synthetic test input."""
    return resolve_reserve_only(
        directory,scope=scope,symbol=symbol,client_id=client_id,
        proof=proof,expected_uid=expected_uid,
    )


def resolve_settled_scope(directory, *, scope, outcome, expected_names, expected_uid=None):
    """G55/Faz 3: owner kapanış işareti; şema doğrulaması ve ortak fence altında.

    Yalnız owner'ın imzalı FLAT + çıkışlar temizlendi doğrulamasından sonra çağrılır.
    Herhangi bir belirsizlikte WalFenceBlocked fırlatır (işaret yazılmaz).
    """
    return _resolve_settled(directory, scope=scope, outcome=outcome, proof=True,
                            expected_names=expected_names, expected_uid=expected_uid,
                            validate_row=_validate_owner_row)
