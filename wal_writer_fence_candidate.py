"""ATLAS FAST WAL writer fence *candidate*: isolated, not production wired.

All WAL writers and the resolver MUST participate in the same OS advisory
lock; a compliant append refuses to write to an already-resolved scope.
This module operates only on a caller-specified directory and never opens
Binance, reads credentials, or changes the active Owner SDK.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat

class WalFenceBlocked(RuntimeError):
    pass

_NAME=re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_LOCK=".atlas_wal_writer_fence.lock"

def _safe(value, label):
    if type(value) is not str or not _NAME.fullmatch(value):
        raise WalFenceBlocked("UNTRUSTED_"+label)
    return value

@contextmanager
def writer_fence(directory, *, expected_uid=None):
    """Non-blocking shared writer fence, with a verified owner-private directory.

    The lock must be held through the entire WAL scan, validation and fsync
    of the marker. NEVER acquire it twice in a nested process call.
    """
    directory=Path(directory)
    if directory.is_symlink():
        raise WalFenceBlocked("WAL_DIR_SYMLINK")
    flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_DIRECTORY
    try:
        directory_fd=os.open(directory,flags)
    except OSError as exc:
        raise WalFenceBlocked("WAL_DIR_NOT_TRUSTED") from exc
    lockfd=None
    try:
        dst=os.fstat(directory_fd)
        if (not stat.S_ISDIR(dst.st_mode)
                or dst.st_mode & 0o077
                or (expected_uid is not None and dst.st_uid!=expected_uid)):
            raise WalFenceBlocked("WAL_DIR_PERMISSIONS")
        try:
            lockfd=os.open(_LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,
                           dir_fd=directory_fd)
        except OSError as exc:
            raise WalFenceBlocked("WAL_FENCE_NOT_TRUSTED") from exc
        locked=os.fstat(lockfd)
        if (not stat.S_ISREG(locked.st_mode)
                or locked.st_nlink!=1
                or locked.st_uid!=dst.st_uid
                or locked.st_mode & 0o077):
            raise WalFenceBlocked("WAL_FENCE_PERMISSIONS")
        try:
            fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError,BlockingIOError) as exc:
            raise WalFenceBlocked("WAL_FENCE_BUSY") from exc
        # Check pathname still references the locked inode after acquiring it.
        path_stat=os.stat(_LOCK,dir_fd=directory_fd,follow_symlinks=False)
        if (path_stat.st_ino!=locked.st_ino or path_stat.st_dev!=locked.st_dev):
            raise WalFenceBlocked("WAL_FENCE_CHANGED")
        yield directory_fd
    finally:
        if lockfd is not None:
            try:fcntl.flock(lockfd,fcntl.LOCK_UN)
            finally:os.close(lockfd)
        os.close(directory_fd)

def _read_all(directory_fd):
    # Caller must already own the WAL writer fence.
    rows={}
    for name in os.listdir(directory_fd):
        if not name.endswith(".json"):
            continue
        if not _NAME.fullmatch(name[:-5]):
            raise WalFenceBlocked("WAL_FILENAME_INVALID")
        try:
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory_fd)
            try:
                st=os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
                    raise WalFenceBlocked("WAL_RECORD_PERMISSIONS")
                with os.fdopen(fd,"r") as stream:
                    fd=-1
                    row=json.load(stream)
            finally:
                if fd>=0:os.close(fd)
        except (OSError,ValueError,UnicodeError) as exc:
            raise WalFenceBlocked("WAL_RECORD_UNREADABLE") from exc
        if type(row) is not dict:
            raise WalFenceBlocked("WAL_RECORD_UNREADABLE")
        rows[name]=row
    return rows

def _write_once(directory_fd,name,row):
    _safe(name[:-5],"WAL_FILE")
    if not name.endswith(".json"):
        raise WalFenceBlocked("WAL_FILENAME_INVALID")
    raw=(json.dumps(row,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
    if len(raw)>16384:
        raise WalFenceBlocked("WAL_ROW_TOO_LARGE")
    try:
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,
                   0o600,dir_fd=directory_fd)
    except FileExistsError as exc:
        raise WalFenceBlocked("WAL_DUPLICATE_NO_WRITE") from exc
    except OSError as exc:
        raise WalFenceBlocked("WAL_CREATE_FAILED") from exc
    try:
        with os.fdopen(fd,"wb") as out:
            fd=-1
            out.write(raw);out.flush();os.fsync(out.fileno())
        os.fsync(directory_fd)
    finally:
        if fd>=0:os.close(fd)

def append_record(directory,*,name,row,scope,expected_uid=None):
    """Every compliant writer checks resolution and appends under one fence."""
    _safe(scope,"SCOPE")
    if not name.endswith(".json"):
        raise WalFenceBlocked("WAL_FILENAME_INVALID")
    if type(row) is not dict:
        raise WalFenceBlocked("WAL_ROW_INVALID")
    if name.startswith("resolved_"):
        raise WalFenceBlocked("RESOLUTION_MARKER_REQUIRES_RESOLVER")
    if ((row.get("detail") or {}).get("scope")!=scope
            and row.get("scope")!=scope):
        raise WalFenceBlocked("WAL_SCOPE_MISMATCH")
    with writer_fence(directory,expected_uid=expected_uid) as fd:
        all_rows=_read_all(fd)
        if "resolved_"+scope+".json" in all_rows:
            raise WalFenceBlocked("SCOPE_ALREADY_RESOLVED_NO_WRITE")
        _write_once(fd,name,row)
    return name

def resolve_reserve_only(directory,*,scope,symbol,client_id,proof,expected_uid=None):
    """Atomically reread the entire scope and commit its zero-entry marker.

    proof is an explicit synthetic decision issued by a separate verified
    read-only policy checker; this candidate DOES NOT validate Binance evidence.
    """
    scope=_safe(scope,"SCOPE")
    symbol=_safe(symbol,"SYMBOL")
    client_id=_safe(client_id,"CLIENT_ID")
    if proof is not True:
        raise WalFenceBlocked("SIGNED_PROOF_NOT_CONFIRMED")
    with writer_fence(directory,expected_uid=expected_uid) as fd:
        rows=_read_all(fd)
        reserve="POLICY_RESERVE_"+scope+".json"
        row=rows.get(reserve)
        detail=row.get("detail") if type(row) is dict else None
        if (type(detail) is not dict
                or row.get("role")!="POLICY_RESERVE"
                or row.get("intent")!=scope
                or detail.get("scope")!=scope
                or detail.get("symbol")!=symbol
                or detail.get("client_id")!=client_id):
            raise WalFenceBlocked("RESERVATION_PROOF_MISSING")
        for name,item in rows.items():
            if name==reserve:
                continue
            if name=="resolved_"+scope+".json":
                raise WalFenceBlocked("ALREADY_RESOLVED")
            extra=item.get("detail")
            if ((type(extra) is dict and extra.get("scope")==scope)
                    or item.get("scope")==scope):
                raise WalFenceBlocked("SCOPE_HAS_OTHER_WAL")
        target="resolved_"+scope+".json"
        _write_once(fd,target,{
            "scope":scope,
            "outcome":"ZERO_ENTRY_SIGNED_FLAT_LEDGER_CLOSED_OFFLINE_CANDIDATE",
            "kind":"WAL_WRITER_FENCE_CANDIDATE",
        })
        return target
