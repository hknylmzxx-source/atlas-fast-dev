"""Use a test copy of installed v4. Never use a production root."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
from unittest.mock import patch

from i4_gecikme_candidate import event_lines
from i4_snapshot_publish_candidate import GROUP, NAME, build_verified
from i4_writer_handoff_candidate import append

loader = importlib.machinery.SourceFileLoader("snapshot_v4", sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
v4 = importlib.util.module_from_spec(spec)
loader.exec_module(v4)

with tempfile.TemporaryDirectory() as temp:
    base = Path(temp)
    root = base / "root"
    p = root / GROUP / NAME
    p.parent.mkdir(parents=True)
    p.write_bytes(b'{"id":1}\n')
    p.with_name(NAME + ".lock").touch()
    st = p.stat()
    policy = dict(writer_uid=os.getuid(), legacy_uid=os.getuid(), gid=st.st_gid,
                  mode=stat.S_IMODE(st.st_mode))
    with patch("os.replace", side_effect=OSError("interrupted after hardlink")):
        try:
            append(p, {"id": 2}, 1, **policy)
        except OSError:
            pass
        else:
            raise AssertionError("interrupt not triggered")
    alias, = p.parent.glob("*.archive")
    assert os.path.samefile(alias, p)
    assert [json.loads(row)["id"] for row in event_lines(p)] == [1]
    out = base / "interrupted.tar.gz"
    result = build_verified(v4, root, out, base / "report.json", required=[GROUP])
    assert result["status"] == "OK", result
    with tarfile.open(out) as tar:
        members = [m for m in tar.getmembers() if m.name.startswith(f"Atlas/{GROUP}/{NAME}") and
                   (m.name.endswith(".archive") or m.name.endswith(".jsonl"))]
        assert len(members) == 1 and members[0].name.endswith(".jsonl")
        assert tar.extractfile(members[0]).read() == p.read_bytes()
    append(p, {"id": 2}, 1, **policy)
    assert not os.path.samefile(alias, p)
    assert [json.loads(row)["id"] for row in event_lines(p)] == [1, 2]
    assert build_verified(v4, root, base / "recovered.tar.gz",
                          base / "recovered.json", required=[GROUP])["status"] == "OK"
    bad = p.with_name(NAME + ".00000000000000000001." + "f" * 32 + ".archive")
    os.link(alias, bad)
    try:
        list(event_lines(p))
    except ValueError as e:
        assert "duplicate archive inode" in str(e)
    else:
        raise AssertionError("duplicate archives accepted")
    rejected = base / "rejected.tar.gz"
    assert build_verified(v4, root, rejected, base / "rejected.json",
                          required=[GROUP])["status"] == "FAIL"
    assert not rejected.exists()
    print("PASS interrupted alias counted once, backup contains one copy, recovery retains both records, anomalous archive duplicates rejected")

with tempfile.TemporaryDirectory() as temp:
    p = Path(temp) / NAME
    p.write_bytes(b'{"id":0}\n')
    p.with_name(NAME + ".lock").touch()
    st = p.stat()
    policy = dict(writer_uid=os.getuid(), legacy_uid=os.getuid(), gid=st.st_gid,
                  mode=stat.S_IMODE(st.st_mode))
    malformed = p.with_name(NAME + ".malformed.archive")
    os.link(p, malformed)
    try:
        append(p, {"id": 1}, 1, **policy)
    except ValueError as e:
        assert "noncanonical archive" in str(e)
    else:
        raise AssertionError("malformed alias accepted")
    assert os.path.samefile(p, malformed)
    malformed.unlink()
    for stamp, value in [(100, 1), (100, 2), (50, 3)]:
        with patch("i4_writer_handoff_candidate.time.time_ns", return_value=stamp):
            append(p, {"id": value}, 1, **policy)
    assert [json.loads(row)["id"] for row in event_lines(p)] == [0, 1, 2, 3]
    print("PASS malformed alias rejected and repeated or reversed clock preserves order")
