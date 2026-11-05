"""OFFLINE ONLY: durable emergency alarm latch independent of WAL writer lock.

This candidate does not send STOP/TP orders, bypass owner locks, alter live
settings or authorize entries. A latch must be treated as a permanent NO-ENTRY
block until owner review; do not infer that a marker can supersede it.
"""
import json
import os
from pathlib import Path
import re
import stat
import time

class EmergencyLatchBlocked(RuntimeError):
    pass

_SAFE=re.compile(r"^[A-Za-z0-9_-]{1,120}$")

def _directory_fd(directory, expected_uid):
    directory=Path(directory)
    if directory.is_symlink():
        raise EmergencyLatchBlocked("LATCH_DIRECTORY_SYMLINK")
    try:
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        s=os.fstat(fd)
        if (not stat.S_ISDIR(s.st_mode) or s.st_mode & 0o077
                or (expected_uid is not None and s.st_uid!=expected_uid)):
            raise EmergencyLatchBlocked("LATCH_DIRECTORY_UNTRUSTED")
        return fd
    except Exception:
        if "fd" in locals():
            os.close(fd)
        raise

def publish_alarm_latch(directory, *, scope, reason, expected_uid=None):
    """Persist a fail-closed alarm without waiting for the WAL writer fence.

    If the latch exists, preserve it rather than replacing prior evidence.
    Caller must halt and escalate on all persistence failures.
    """
    if type(scope) is not str or not _SAFE.fullmatch(scope):
        raise EmergencyLatchBlocked("LATCH_SCOPE_INVALID")
    if type(reason) is not str or not reason or len(reason)>250:
        raise EmergencyLatchBlocked("LATCH_REASON_INVALID")
    name="emergency_latch_"+scope+".json"
    fd=_directory_fd(directory,expected_uid)
    try:
        row={"scope":scope,"reason":reason,"new_entry_permitted":False,
             "retry_permitted":False,"state":"MANUAL_OWNER_REVIEW_REQUIRED",
             "at":time.time()}
        raw=(json.dumps(row,sort_keys=True)+"\n").encode()
        try:
            outfd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,
                          0o600,dir_fd=fd)
        except FileExistsError:
            # Existing latch is never dismissed by an emergency repeat.
            return "ALREADY_LATCHED"
        try:
            with os.fdopen(outfd,"wb") as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.fsync(fd)
        except Exception:
            # Leave possibly partial latch in place for fail-closed startup.
            raise
        return "LATCHED"
    finally:
        os.close(fd)

def require_no_emergency_latch(directory, *, expected_uid=None):
    """Candidate NO-ENTRY check. Never treats a WAL resolved marker as release."""
    fd=_directory_fd(directory,expected_uid)
    try:
        for name in os.listdir(fd):
            if name.startswith("emergency_latch_"):
                raise EmergencyLatchBlocked("UNRESOLVED_EMERGENCY_LATCH_NO_ENTRY")
        return True
    finally:
        os.close(fd)
