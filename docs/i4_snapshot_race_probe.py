"""Deterministic offline race test for the installed snapshot v4 core."""
import fcntl
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

from i4_rotation_probe import append

loader = importlib.machinery.SourceFileLoader("snapshot_v4", sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
snapshot = importlib.util.module_from_spec(spec)
loader.exec_module(snapshot)
name = "fast_signal_live_dry_run_events.jsonl"
group = "FAST_SIGNAL_LIVE_PILOT"
prefix = f"Atlas/{group}/"


def case(base, coordinated):
    root = base / "source"
    folder = root / group
    folder.mkdir(parents=True)
    active = folder / name
    append(active, {"id": 1}, 1024)
    lock_path = active.with_name(name + ".lock")
    out, report = base / "backup.tar.gz", base / "report.json"
    fired = False

    def hook(_path, relative):
        nonlocal fired
        if relative != f"{group}/{name}":
            return
        assert not fired
        fired = True
        if not coordinated:
            append(active, {"id": 2}, 1)
            return
        # A separate process models a cooperating writer, without timing assumptions.
        code = """import fcntl,sys
with open(sys.argv[1], 'ab') as f:
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(37)
"""
        assert subprocess.run([sys.executable, "-I", "-B", "-c", code, str(lock_path)]).returncode == 37

    args = (root, out, report, {group: 1}, [group])
    if coordinated:
        with lock_path.open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            result = snapshot.build(*args, hook=hook)
        append(active, {"id": 2}, 1)
    else:
        result = snapshot.build(*args, hook=hook)
    assert fired and result["status"] == "OK", result
    with tarfile.open(out, "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile() and m.name.startswith(prefix + name)
                   and (m.name.endswith(".archive") or m.name == prefix + name)]
        restored = [json.loads(line)["id"] for m in members for line in tar.extractfile(m)]
    assert restored == ([1] if coordinated else [2]), (coordinated, restored)
    assert any(folder.glob(name + ".*.archive"))
    print(f"snapshot={result['status']} coordinated={coordinated} restored={restored}")

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    case(base / "uncoordinated", False)
    case(base / "coordinated", True)
