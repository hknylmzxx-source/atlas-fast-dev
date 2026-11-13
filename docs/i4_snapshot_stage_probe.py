"""Staging/restore test using a supplied copy of the installed v4 snapshot."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

from i4_rotation_probe import append
from i4_snapshot_stage_candidate import freeze, verify_members

v4 = Path(sys.argv[1]).resolve()
group = "FAST_SIGNAL_LIVE_PILOT"
name = "fast_signal_live_dry_run_events.jsonl"
prefix = f"Atlas/{group}/"

with tempfile.TemporaryDirectory() as d:
    base = Path(d)
    active = base / "source" / name
    active.parent.mkdir()
    for n in range(10):
        append(active, {"id": n}, 31)

    def rotate_during_copy():
        subprocess.run([sys.executable, "-B", "-c",
            "from pathlib import Path; from i4_rotation_probe import append; import sys; append(Path(sys.argv[1]), {'id': 10}, 1)",
            str(active)], check=True, timeout=5)

    root = base / "staged"
    root.mkdir()
    parts = root / group
    expected = freeze(active, parts, rotate_during_copy)
    ordered = [p for p in sorted(expected) if p != name] + [name]
    assert [json.loads(l)["id"] for p in ordered for l in (parts / p).read_bytes().splitlines()] == list(range(10))
    assert json.loads(active.read_bytes()) == {"id": 10}

    def build(label):
        out = base / f"{label}.tar.gz"
        report = base / f"{label}.json"
        result = subprocess.check_output([sys.executable, "-I", "-B", str(v4),
                "--root", str(root), "--archive", str(out), "--report", str(report), "--require", group])
        assert json.loads(result)["status"] == "OK", result
        return out

    def check(label, ok):
        candidate = build(label)
        try:
            with tarfile.open(candidate, "r:gz") as tar:
                verify_members(tar, prefix, expected)
        except ValueError as e:
            assert not ok and str(e)=="I4 snapshot missing or changed expected parts"
        else:
            assert ok
            candidate.rename(base / "verified.tar.gz")

    check("complete", True)
    oldest = parts / ordered[0]
    data = oldest.read_bytes()
    oldest.unlink()
    check("missing", False)
    oldest.write_bytes(data.replace(b"0", b"9", 1))
    assert hashlib.sha256(oldest.read_bytes()).hexdigest() != expected[oldest.name]["sha256"]
    check("changed", False)
    with (parts / name).open("ab") as f:
        f.write(b"{")
    with active.open("ab") as f:
        f.write(b"{")
    try:
        freeze(active, base / "partial")
    except ValueError as e:
        assert "partial JSONL" in str(e)
    else:
        raise AssertionError("partial capture accepted")
    print("PASS concurrent rotate, stable snapshot, v4 restore, missing reject, changed reject, partial reject")
