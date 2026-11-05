"""ATLAS FAST: read-only offline signal and handoff log diagnostic helpers.

No Binance SDK, owner executor, commands, network, key material, or writes.
Only explicit file paths supplied by caller. Safe on synthetic fixtures.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path

class SignalLogError(ValueError):
    pass

def bounded_last_jsonl(path, *, max_rows=1000, max_bytes=4_000_000):
    """Read a bounded tail of a JSONL file without parsing partial first lines."""
    path=Path(path)
    if not path.is_file() or path.is_symlink():
        raise SignalLogError("LOG_MISSING_OR_SYMLINK")
    if not (1<=max_rows<=10000 and 1024<=max_bytes<=16_000_000):
        raise SignalLogError("BOUND_INVALID")
    with path.open("rb") as f:
        size=f.seek(0,2)
        start=max(0,size-max_bytes)
        f.seek(start)
        if start:
            f.readline()  # exclude partial first line
        raw=f.readlines()
    if not raw:
        return []
    rows=[]
    for line in raw[-max_rows:]:
        if not line.strip():
            continue
        if len(line)>64_000:
            raise SignalLogError("ROW_OVERSIZED")
        try:
            x=json.loads(line)
        except (ValueError,UnicodeDecodeError) as exc:
            raise SignalLogError("LOG_ROW_INVALID") from exc
        if type(x) is not dict:
            raise SignalLogError("LOG_ROW_NOT_OBJECT")
        rows.append(x)
    return rows

def summary(signal_rows, handoff_rows):
    """Distinguish signal evaluation, owner handoff and actual exchange proof."""
    count=Counter(str(r.get("status","UNKNOWN")) for r in signal_rows)
    blockers=Counter()
    for r in signal_rows:
        b=r.get("blockers")
        if type(b) is not list:
            blockers["UNVERIFIED_BLOCKER_FORMAT"]+=1
        else:
            for item in b:
                if type(item) is str:
                    blockers[item]+=1
    last=signal_rows[-1] if signal_rows else {}
    approved=[]
    exits=[]
    for r in handoff_rows:
        status=r.get("status")
        if status=="PHONE_APPROVED_EXECUTOR_STARTED":
            approved.append({
                "at":r.get("at"),"symbol":r.get("sig_symbol"),
                "side":r.get("sig_side"),"score":r.get("sig_score"),
            })
        elif status=="EXECUTOR_EXITED_OWNER_REVIEW":
            exits.append({
                "at":r.get("at"),"symbol":r.get("sig_symbol"),
                "side":r.get("sig_side"),"exit_code":r.get("exit_code"),
            })
    return {
        "latest_signal_evaluation_at":last.get("generated_at"),
        "latest_signal_mode":last.get("mode"),
        "evaluations":len(signal_rows),
        "status_counts":dict(count),
        "blocker_counts":dict(blockers),
        "handoff_approvals_in_sample":approved,
        "handoff_exits_in_sample":exits,
        "exchange_execution_verified":False,
        "note":"Exit codes and dry-run signals never prove Binance fills.",
    }
