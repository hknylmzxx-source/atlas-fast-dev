"""Offline I4 staging candidate. All inputs and outputs are explicit."""
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re


def freeze(active, dest, after_capture=None):
    """Pin names, open files and lengths under lock, then copy without holding it."""
    active, dest = Path(active), Path(dest)
    pattern = re.compile(re.escape(active.name) + r"\.\d{20}\.[0-9a-f]{32}\.archive")
    lock_path = active.with_name(active.name + ".lock")
    with ExitStack() as stack:
        with lock_path.open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            paths = sorted(p for p in active.parent.iterdir() if pattern.fullmatch(p.name))
            parts = []
            for p in (*paths, active):
                f = stack.enter_context(p.open("rb"))
                parts.append((p.name, f, os.fstat(f.fileno()).st_size))
        if after_capture:
            after_capture()
        dest.mkdir()
        manifest = {}
        for name, f, size in parts:
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
            manifest[name] = {"bytes": size, "sha256": sha.hexdigest()}
        with (dest / "i4_expected_parts.json").open("x", encoding="utf-8") as f:
            json.dump(manifest, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        return manifest


def verify_members(tar, prefix, expected):
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
        actual[name] = {"bytes": size, "sha256": sha.hexdigest()}
    if actual != expected:
        raise ValueError("I4 snapshot missing or changed expected parts")
