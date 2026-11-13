"""03:45 cloud job candidate. Explicit paths; no production defaults."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from i4_cloud_gate_candidate import prepare_upload, regular_read
from i4_snapshot_publish_candidate import fsync_dir


def state_write(path, result):
    fd, name = tempfile.mkstemp(prefix=".i4-status-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        fsync_dir(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def run(source, destination, staging, state, *, now=None):
    source, destination, staging, state = (Path(p).resolve() for p in (source, destination, staging, state))
    now = time.time() if now is None else now
    result = {"status": "FAIL", "checked_at_unix": now}
    try:
        if staging == destination or destination in staging.parents or staging in destination.parents:
            raise ValueError("staging must be separate from destination")
        if staging.stat().st_dev != destination.stat().st_dev:
            raise ValueError("atomic publication requires a shared filesystem")
        matches = [(p.stat(follow_symlinks=False).st_mtime, p) for p in source.iterdir()
                   if re.fullmatch(r"Atlas_\d{8}_\d{6}\.tar\.gz", p.name)]
        if not matches:
            raise FileNotFoundError("no snapshot")
        mtime, archive = max(matches)
        result["archive"] = archive.name
        if now - mtime > 26 * 3600:
            result["status"] = "STALE_SNAPSHOT"
            raise ValueError("snapshot older than 26 hours")
        if mtime > now + 300:
            raise ValueError("snapshot timestamp is in the future")
        report = source / "raporlar" / (archive.name.removesuffix(".tar.gz") + ".report.json")
        target = destination / archive.name
        with tempfile.TemporaryDirectory(prefix=".i4-cloud-job-", dir=staging) as td:
            verified = Path(td) / "verified"
            archive_status, sha = prepare_upload(archive, report, verified)
            try:
                os.link(verified, target, follow_symlinks=False)
                fsync_dir(destination)
                copy_status = "OK_COPIED"
            except FileExistsError:
                with regular_read(target) as f:
                    if hashlib.file_digest(f, "sha256").hexdigest() != sha:
                        raise ValueError("destination SHA-256 mismatch; existing copy retained")
                copy_status = "OK_ALREADY_PRESENT"
        result.update(status="PARTIAL_SNAPSHOT" if archive_status == "PARTIAL" else copy_status,
                      copy_status=copy_status, archive_status=archive_status, archive_sha256=sha)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result["error"] = str(exc)
    state_write(state, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for flag in ("source", "destination", "staging", "state"):
        parser.add_argument("--" + flag, required=True)
    result = run(**vars(parser.parse_args()))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] in {"OK_COPIED", "OK_ALREADY_PRESENT", "PARTIAL_SNAPSHOT"} else 1)
