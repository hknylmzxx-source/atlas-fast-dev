"""OFFLINE ONLY: bounded pre-send WAL lock-contention probe for protective writes.

Retries *only* acquiring a pre-send WAL journal after WAL_FENCE_BUSY.
Never calls Binance, posts an order, enables a trade or bypasses Owner guards.
"""
import time
from wal_writer_fence_candidate import WalFenceBlocked
from wal_owner_write_adapter_candidate import append_order_journal

class ProtectiveJournalDeadlineExceeded(WalFenceBlocked):
    pass

def append_protective_bounded(directory, *, role, identity, scope, symbol,
                              client_id, expected_uid=None,
                              timeout_s=0.1, poll_s=0.01,
                              clock=time.monotonic, sleep=time.sleep):
    if role not in ("STOP","TP1","TP2"):
        raise WalFenceBlocked("PROTECTIVE_ROLE_ONLY")
    if not (isinstance(timeout_s,(int,float)) and type(timeout_s) is not bool
            and 0<=timeout_s<=0.5):
        raise WalFenceBlocked("PROTECTIVE_WAIT_BOUND_INVALID")
    if not (isinstance(poll_s,(int,float)) and type(poll_s) is not bool
            and 0<poll_s<=0.05):
        raise WalFenceBlocked("PROTECTIVE_POLL_INVALID")
    deadline=clock()+timeout_s
    while True:
        try:
            return append_order_journal(
                directory,role=role,identity=identity,scope=scope,
                symbol=symbol,client_id=client_id,expected_uid=expected_uid)
        except WalFenceBlocked as err:
            # A duplicate/invalid/unsafe WAL is NEVER retried.
            if str(err)!="WAL_FENCE_BUSY":
                raise
            remaining=deadline-clock()
            if remaining<=0:
                raise ProtectiveJournalDeadlineExceeded(
                    "PROTECTIVE_WAL_DEADLINE_NO_POST_OWNER_REVIEW") from err
            sleep(min(poll_s,remaining))
