"""Offline proof of an explicit owner transition for the active JSONL file."""
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from uuid import uuid4


def regular(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("nonregular input")
    return fd


def append(path, row, limit, *, writer_uid, legacy_uid, gid, mode):
    path = Path(path)
    data = json.dumps(row, separators=(",", ":")).encode() + b"\n"
    # Existing lock only. Do not silently establish a separate lock inode.
    with os.fdopen(regular(path.with_name(path.name + ".lock"), os.O_RDWR), "r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with os.fdopen(regular(path, os.O_WRONLY | os.O_APPEND), "ab") as active:
            src = os.fstat(active.fileno())
            if src.st_uid not in {writer_uid, legacy_uid} or (src.st_gid, stat.S_IMODE(src.st_mode)) != (gid, mode):
                raise PermissionError("unapproved source metadata")
            archives = list(path.parent.glob(path.name + ".*.archive"))
            same = [p for p in archives if not p.is_symlink() and p.stat().st_ino == src.st_ino
                    and p.stat().st_dev == src.st_dev]
            if len(same) > 1:
                raise ValueError("duplicate interrupted rotation links")
            if not same and (not src.st_size or src.st_size + len(data) <= limit):
                active.write(data)
                active.flush()
                os.fsync(active.fileno())
                return
            if os.geteuid() != writer_uid:
                raise PermissionError("rotation belongs to the designated writer")
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".i4-writer-")
            try:
                with os.fdopen(fd, "wb") as new:
                    if os.fstat(new.fileno()).st_gid != gid:
                        os.fchown(new.fileno(), -1, gid)
                    os.fchmod(new.fileno(), mode)
                    dst = os.fstat(new.fileno())
                    if (dst.st_uid, dst.st_gid, stat.S_IMODE(dst.st_mode)) != (writer_uid, gid, mode):
                        raise PermissionError("new metadata does not match explicit policy")
                    new.write(data)
                    new.flush()
                    os.fsync(new.fileno())
                if not same:
                    archive = path.with_name(f"{path.name}.{time.time_ns():020d}.{uuid4().hex}.archive")
                    os.link(path, archive, follow_symlinks=False)
                # Active pathname never disappears. The old inode and metadata remain in archive.
                os.replace(tmp, path)
                dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            finally:
                Path(tmp).unlink(missing_ok=True)
