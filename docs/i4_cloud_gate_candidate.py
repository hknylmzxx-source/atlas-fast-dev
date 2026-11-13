"""Offline cloud input gate; no network or production paths."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile


def grant_macos_reader(path, uid):
    if sys.platform != "darwin" or uid == os.geteuid():
        raise ValueError("a distinct macOS reader account is required")
    name = pwd.getpwuid(uid).pw_name
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("invalid reader account")
    os.chmod(path, 0o600)
    subprocess.run(["/bin/chmod", "+a", f"user:{name} allow read", str(path)], check=True)


def regular_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def prepare_upload(archive, report, destination):
    """Validate the exact bytes copied; expose the copy only after verification."""
    archive, report, destination = map(Path, (archive, report, destination))
    if not re.fullmatch(r"Atlas_\d{8}_\d{6}\.tar\.gz", archive.name):
        raise ValueError("not a final snapshot name")
    stamp = archive.name.removesuffix(".tar.gz")
    if report.name != stamp + ".report.json":
        raise ValueError("report name does not match archive")
    with regular_read(report) as f:
        data = json.load(f)
    sha = data.get("archive_sha256")
    if (data.get("status") not in {"OK", "PARTIAL"}
            or data.get("final_archive") != str(archive.absolute())
            or not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha)):
        raise ValueError("report does not validate this archive")
    fd, tmp = tempfile.mkstemp(prefix=".i4-cloud-", dir=destination.parent)
    try:
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as out, regular_read(archive) as src:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                digest.update(chunk)
                out.write(chunk)
            if digest.hexdigest() != sha:
                raise ValueError("archive SHA-256 mismatch")
            out.flush()
            os.fsync(out.fileno())
        os.link(tmp, destination)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return data["status"], sha
