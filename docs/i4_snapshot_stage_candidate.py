"""Offline I4 staging candidate. All inputs and outputs are explicit."""
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from decimal import Decimal


def open_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"not a regular file: {path.name}")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def freeze(active, dest, after_capture=None):
    """Pin names, open files and lengths under lock, then copy without holding it."""
    active, dest = Path(active), Path(dest)
    pattern = re.compile(re.escape(active.name) + r"\.\d{20}\.[0-9a-f]{32}\.archive")
    lock_path = active.with_name(active.name + ".lock")
    with ExitStack() as stack:
        with open_regular(lock_path) as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            paths = sorted(p for p in active.parent.iterdir() if pattern.fullmatch(p.name))
            active_file = stack.enter_context(open_regular(active))
            active_stat = os.fstat(active_file.fileno())
            active_key = (active_stat.st_dev, active_stat.st_ino)
            parts, seen = [], set()
            for p in paths:
                with open_regular(p) as f:
                    st = os.fstat(f.fileno())
                    key = (st.st_dev, st.st_ino)
                    if key in seen:
                        raise ValueError("duplicate archive inode")
                    seen.add(key)
                    if key != active_key:
                        pinned = stack.enter_context(os.fdopen(os.dup(f.fileno()), "rb"))
                        parts.append((p.name, pinned, st))
            parts.append((active.name, active_file, active_stat))
        if after_capture:
            after_capture()
        dest.mkdir()
        manifest = {}
        for name, f, st in parts:
            size = st.st_size
            sha, remaining, tail = hashlib.sha256(), size, b""
            with (dest / name).open("xb") as out:
                while remaining:
                    chunk = f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError(f"snapshot part shrank: {name}")
                    sha.update(chunk)
                    out.write(chunk)
                    tail = chunk[-1:]
                    remaining -= len(chunk)
                if size and tail != b"\n":
                    raise ValueError(f"partial JSONL record: {name}")
                out.flush()
                os.fsync(out.fileno())
            manifest[name] = {"bytes": size, "sha256": sha.hexdigest(), "mode": stat.S_IMODE(st.st_mode),
                              "uid": st.st_uid, "gid": st.st_gid, "mtime_ns": st.st_mtime_ns}
        with (dest / "i4_expected_parts.json").open("x", encoding="utf-8") as f:
            json.dump(manifest, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        return manifest


def verify_members(tar, prefix, expected, *, content_only=False):
    """Gate promotion using the in-memory capture, not just tar's internal manifest."""
    if not expected:
        raise ValueError("empty capture manifest")
    actual = {}
    for m in tar:
        if not m.name.startswith(prefix):
            continue
        name = m.name[len(prefix):]
        if name not in expected:
            continue
        if name in actual or not m.isfile():
            raise ValueError(f"unexpected archive member type or duplicate: {name}")
        sha, size = hashlib.sha256(), 0
        with tar.extractfile(m) as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(chunk)
                size += len(chunk)
        actual[name] = {"bytes": size, "sha256": sha.hexdigest(), "mode": m.mode,
                        "uid": m.uid, "gid": m.gid,
                        "mtime_ns": int(Decimal(m.pax_headers.get("mtime", str(m.mtime))) * 10**9)}
    if content_only:
        project = lambda members: {n: {k: row[k] for k in ('bytes', 'sha256')} for n, row in members.items()}
        actual, expected = project(actual), project(expected)
    if actual != expected:
        raise ValueError("I4 snapshot missing or changed expected parts")
