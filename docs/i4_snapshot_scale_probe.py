"""Synthetic scale benchmark. Uses only temporary paths."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

import i4_snapshot_stage_candidate as stage
from i4_snapshot_publish_candidate import GROUP, NAME, build_verified

loader = importlib.machinery.SourceFileLoader("snapshot_v4", sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
v4 = importlib.util.module_from_spec(spec)
loader.exec_module(v4)
rows = 385285
target_bytes = 226278610
sample = json.dumps({"id":"000000","note":"x"*561}, separators=(",", ":")).encode()+b"\n"
assert len(sample)<1000
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    root = base / "input"
    folder = root / GROUP
    folder.mkdir(parents=True)
    active = folder / NAME
    with active.open("wb") as f:
        for i in range(rows):
            n = target_bytes//rows + (i < target_bytes % rows)
            fixed = ('{"id":"%06d","note":"' % i).encode()
            f.write(fixed+b"x"*(n-len(fixed)-3)+b'"}\n')
    assert active.stat().st_size == target_bytes
    active.with_name(NAME+".lock").touch()
    held = []
    entered = []
    original = stage.fcntl.flock

    def measure(fd, op):
        result = original(fd, op)
        if op == stage.fcntl.LOCK_SH:
            entered.append(time.perf_counter())
        return result

    def after_capture():
        held.append(time.perf_counter()-entered[-1])
        with active.open("ab") as f:
            f.write(b'{"id":"later"}\n')

    output = base / "Atlas_scale.tar.gz"
    report = base / "scale.json"
    start = time.perf_counter()
    with patch.object(stage.fcntl, "flock", measure):
        result = build_verified(v4, root, output, report, required=[GROUP], after_capture=after_capture)
    elapsed = time.perf_counter()-start
    assert result["status"]=="OK", result
    import tarfile
    with tarfile.open(output,"r:gz") as t:
        m=t.getmember(f"Atlas/{GROUP}/{NAME}")
        assert m.size==target_bytes
        with t.extractfile(m) as f:
            assert sum(1 for _ in f)==rows
    print(json.dumps({"fixture_bytes":target_bytes,"rows":rows,"lock_held_ms":round(held[0]*1000,3),
          "stage_payload_bytes":target_bytes,"final_gzip_bytes":output.stat().st_size,
          "pipeline_seconds":round(elapsed,3),"new_append_excluded":True}))
