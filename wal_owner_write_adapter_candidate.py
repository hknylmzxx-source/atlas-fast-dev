"""Offline adapter candidates for the Owner's pre-resolution WAL writers.

These write into an EXPLICIT test directory only. No hooks are installed in
owner_single_execution.py, and there is no automatic production activation.
"""
from __future__ import annotations

import hashlib
import time

from wal_writer_fence_candidate import (
    WalFenceBlocked, append_record, resolve_reserve_only,
)


def append_order_journal(directory, *, role, identity, scope, symbol,
                         client_id, expected_uid=None):
    """Preserve the original Owner journal's role/intent/detail contract."""
    if role not in ("ENTRY","STOP","TP1","TP2","EMERGENCY"):
        raise WalFenceBlocked("OWNER_WAL_ROLE_INVALID")
    return append_record(
        directory,name=role+"_"+identity+".json",
        row={"role":role,"intent":identity,"at":time.time(),
             "state":"WRITE_OUTCOME_UNKNOWN_NO_RETRY",
             "detail":{"scope":scope,"symbol":symbol,"client_id":client_id}},
        scope=scope,expected_uid=expected_uid,
    )


def append_reservation(directory, *, scope, symbol, client_id,
                       expected_uid=None):
    """Preserve POLICY_RESERVE_<scope>.json, without changing policy ledger."""
    return append_record(
        directory,name="POLICY_RESERVE_"+scope+".json",
        row={"role":"POLICY_RESERVE","intent":scope,"at":time.time(),
             "state":"RESERVATION_PENDING",
             "detail":{"scope":scope,"symbol":symbol,"client_id":client_id}},
        scope=scope,expected_uid=expected_uid,
    )


def append_incident(directory, *, scope, reason, expected_uid=None):
    """Incident must be persisted or raise a blocking error; never ignore it."""
    if type(reason) is not str:
        raise WalFenceBlocked("INCIDENT_REASON_INVALID")
    return append_record(
        directory,name="incident_"+scope+".json",
        row={"scope":scope,"at":time.time(),"reason":reason,
             "new_entry_permitted":False,"retry_permitted":False},
        scope=scope,expected_uid=expected_uid,
    )


def resolve_zero_reservation_offline(directory, *, scope,symbol,client_id,
                                     proof,expected_uid=None):
    """Candidate atomic marker; proof=True is ONLY a synthetic test input."""
    return resolve_reserve_only(
        directory,scope=scope,symbol=symbol,client_id=client_id,
        proof=proof,expected_uid=expected_uid,
    )
