"""Offline parity and restore test with externally supplied installed snapshot files."""
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

from i4_rotation_probe import append

here = Path(__file__).resolve().parent
original, snapshot, thresholds, private = map(lambda s: Path(s).resolve(), sys.argv[1:])
name = "fast_signal_live_dry_run_events.jsonl"
groups = json.loads(thresholds.read_text())
def run(script, *args):
    return subprocess.check_output([sys.executable, "-I", "-B", str(script), *map(str, args)])

with tempfile.TemporaryDirectory() as temp:
    base = Path(temp)
    root = base / "source"
    for group, minimum in groups.items():
        folder = root / group
        folder.mkdir(parents=True)
        for i in range(minimum):
            (folder / f"fixture-{i}.txt").write_text("fixture\n")
    folder = root / "FAST_SIGNAL_LIVE_PILOT"
    active = folder / name
    rows = []
    for i in range(20):
        rows.append({"status": "READY_DRY_RUN_PLAN", "generated_at": f"2026-11-13T09:00:{i+3:02}+00:00", "plan": {"symbol": f"T{i}USDT", "side": "LONG", "source_generated_at": f"2026-11-13T09:00:{i:02}+00:00"}})
    active.write_text("".join(json.dumps(row)+"\n" for row in rows))
    states = ["WAITING_PAIRED_IPHONE_EXACT_CONSENT", "PHONE_APPROVED_EXECUTOR_STARTED", "EXECUTOR_EXITED_OWNER_REVIEW"]
    (folder / "fast_handoff_decision_log.jsonl").write_text("".join(json.dumps({"paper_ticket":"test", "sig_source_at":rows[0]["plan"]["source_generated_at"], "sig_symbol":"T0USDT", "at":f"2026-11-13T09:00:{10+i:02}+00:00", "status":st})+"\n" for i,st in enumerate(states)))
    (folder / "fast_signal_live_dry_run_state.json").write_text('{"signal_age_seconds":3}')
    legacy = base / "legacy.py"
    text = original.read_text()
    key = 'D = "/Users/Shared/Atlas/FAST_SIGNAL_LIVE_PILOT/"'
    assert text.count(key)==1
    legacy.write_text(text.replace(key,"D = "+repr(str(folder)+"/")))
    expected = run(legacy)
    candidate = here / "i4_gecikme_candidate.py"
    assert run(candidate,"--root",folder)==expected
    active.unlink()
    for row in rows:
        append(active,row,520)
    archives = sorted(folder.glob(name+".*.archive"))
    assert archives
    assert run(candidate,"--root",folder)==expected
    assert run(legacy)!=expected
    out, report = base / "fixture.tar.gz", base / "fixture.report.json"
    result = json.loads(run(snapshot,"--root",root,"--archive",out,"--report",report,
                            "--thresholds",thresholds,"--owner-private",private,"--require",*groups))
    assert result["status"]=="OK", result
    required = [active, *archives, folder / (name+".lock"),
                folder / "fast_handoff_decision_log.jsonl", folder / "fast_signal_live_dry_run_state.json"]
    restore = base / "restored"
    restore.mkdir()
    with tarfile.open(out,"r:gz") as tar:
        members = {p:tar.getmember(str(PurePosixPath("Atlas",*p.relative_to(root).parts))) for p in required}
        for p,m in members.items():
            assert m.isfile()
            data = tar.extractfile(m).read()
            assert data==p.read_bytes()
            (restore/p.name).write_bytes(data)
    assert run(candidate,"--root",restore)==expected
    print(f"PASS legacy parity, rotated parity, active-only regression, installed v4 OK, {len(required)} exact restored members, restored parity")
