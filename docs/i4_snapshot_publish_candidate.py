"""Offline v4 adapter. Explicit paths only. No production installer."""
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile

from i4_snapshot_stage_candidate import freeze, verify_members

GROUP = "FAST_SIGNAL_LIVE_PILOT"
NAME = "fast_signal_live_dry_run_events.jsonl"


def build_verified(v4, root, output, report, *, thresholds=None, required=(),
                   owner_private=(), after_capture=None, gate=verify_members):
    root, output, report = (Path(p).resolve() for p in (root, output, report))
    if any(p == root or root in p.parents for p in (output, report)):
        raise ValueError("output and report must be outside source root")
    if output == report:
        raise ValueError("archive and report must differ")
    if output.exists() or report.exists():
        raise FileExistsError("destination already exists")
    published = False
    result = {"status": "FAIL", "fatal": [], "final_archive": None, "archive_sha256": None}
    # stage outside the source; unpublished candidate has a name not matched by cloud scan.
    with tempfile.TemporaryDirectory(prefix=".i4-work-", dir=output.parent) as temp:
        work = Path(temp)
        stage = work / "frozen"
        archive, internal = work / "candidate.tar.gz", work / "v4.json"
        active = root / GROUP / NAME
        try:
            expected = freeze(active, stage, after_capture)
            # Use v4's normal traversal for unrelated files. For I4, override only
            # the named files listed at the cut, not any newly rotated member.
            original_scan = v4.os.scandir
            live_group = root / GROUP
            frozen = {n: stage / n for n in expected}

            def scan(path):
                if Path(path) != live_group:
                    return original_scan(path)
                with original_scan(path) as entries:
                    live = [e for e in entries if e.name not in frozen and not
                            (e.name.startswith(NAME + ".") and e.name.endswith(".archive"))]
                with original_scan(stage) as entries:
                    return [*live, *(e for e in entries if e.name in frozen)]

            try:
                v4.os.scandir = scan
                raw = v4.build(root, archive, internal, thresholds, required, owner_private=owner_private)
            finally:
                v4.os.scandir = original_scan
            result = dict(raw, status="FAIL", final_archive=None, archive_sha256=None)
            if raw["status"] not in {"OK", "PARTIAL"}:
                raise ValueError("v4 candidate not publishable")
            with tarfile.open(archive, "r:gz") as tar:
                gate(tar, f"Atlas/{GROUP}/", expected)
            with archive.open("rb") as f:
                sha = hashlib.file_digest(f, "sha256").hexdigest()
            if sha != raw["archive_sha256"]:
                raise ValueError("candidate archive SHA changed")
            os.link(archive, output)
            published = True
            result.update(status=raw["status"], final_archive=str(output), archive_sha256=sha)
        except (OSError, ValueError, tarfile.TarError) as exc:
            result["fatal"] = [*result["fatal"], f"I4_PREPUBLICATION: {exc}"]
        try:
            with report.open("x", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except OSError:
            if published:
                output.unlink()
            raise
    return result
