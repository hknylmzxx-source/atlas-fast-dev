#!/usr/bin/env python3
"""ATLAS FAST — sinyal->telefon->emir gecikme ölçümü (Claude, 28.10.2026). SALT-OKUR, paylaşılan klasör."""
import json, os
from datetime import datetime
import argparse
from pathlib import Path
from contextlib import ExitStack
import fcntl
import re
import stat

def open_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("nonregular log input")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def event_lines(path):
    """Snapshot the archive list and byte boundaries, without writing a lock file."""
    lock_path = path.with_name(path.name + ".lock")
    pattern = re.compile(re.escape(path.name) + r"\.\d{20}\.[0-9a-f]{32}\.archive")
    with ExitStack() as stack:
        try:
            lock = stack.enter_context(open_regular(lock_path))
        except FileNotFoundError:
            # Legacy reader before coordinated rotation is installed.
            active = stack.enter_context(open_regular(path))
            if lock_path.exists() or any(pattern.fullmatch(p.name) for p in path.parent.iterdir()):
                raise RuntimeError("rotation lock missing or initialized during read, retry")
            yield from active
            return
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            archives = sorted(p for p in path.parent.iterdir() if pattern.fullmatch(p.name))
            active = stack.enter_context(open_regular(path))
            active_stat = os.fstat(active.fileno())
            active_key = (active_stat.st_dev, active_stat.st_ino)
            parts, seen = [], set()
            for part in archives:
                with open_regular(part) as f:
                    st = os.fstat(f.fileno())
                    key = (st.st_dev, st.st_ino)
                    if key in seen:
                        raise ValueError("duplicate archive inode")
                    seen.add(key)
                    if key != active_key:
                        pinned = stack.enter_context(os.fdopen(os.dup(f.fileno()), "rb"))
                        parts.append((pinned, st.st_size))
            parts.append((active, active_stat.st_size))
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
        for f, remaining in parts:
            while remaining:
                line = f.readline(remaining)
                if not line or not line.endswith(b"\n"):
                    raise ValueError("incomplete JSONL snapshot")
                remaining -= len(line)
                yield line



def t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def q(v):
    v = sorted(v); n = len(v)
    return "n=%d min %.0f medyan %.0f p90 %.0f maks %.0f sn" % (n, v[0], v[n // 2], v[int(n * .9)], v[-1]) if v else "yok"


def main(root):
    first = {}
    for line in event_lines(root / "fast_signal_live_dry_run_events.jsonl"):
        if b"READY_DRY_RUN_PLAN" not in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        p = r.get("plan") or {}
        k = (p.get("symbol"), p.get("side"), p.get("source_generated_at"))
        if k[2] and k not in first:
            first[k] = t(r["generated_at"])
    d1 = [first[k] - t(k[2]) for k in first]
    ch, ap, ex = {}, {}, {}
    for line in (root / "fast_handoff_decision_log.jsonl").open(encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        k = r.get("paper_ticket")
        if not k or not r.get("sig_source_at"):
            continue
        st = r.get("status")
        if st == "WAITING_PAIRED_IPHONE_EXACT_CONSENT" and k not in ch:
            ch[k] = (t(r["at"]), t(r["sig_source_at"]), r.get("sig_symbol"))
        if st == "PHONE_APPROVED_EXECUTOR_STARTED":
            ap[k] = t(r["at"])
        if st == "EXECUTOR_EXITED_OWNER_REVIEW":
            ex[k] = t(r["at"])
    d2, d3 = [], []
    for k, (a, src, sym) in ch.items():
        d3.append(a - src)
        for kk, v in first.items():
            if kk[0] == sym and abs(t(kk[2]) - src) < 1:
                d2.append(a - v)
    print("1) sinyal kaynağı -> dry-run READY :", q(d1))
    print("2) dry-run READY -> telefona düşme :", q(d2))
    print("   sinyal kaynağı -> telefon (toplam):", q(d3))
    print("3) telefon -> EVET               :", q([ap[k] - ch[k][0] for k in ap if k in ch]))
    print("4) EVET -> yürütücü bitti         :", q([ex[k] - ap[k] for k in ap if k in ex]))
    with (root / "fast_signal_live_dry_run_state.json").open(encoding="utf-8") as state:
        st = json.load(state)
    print("dry-run son durum: signal_age_seconds =", st.get("signal_age_seconds"))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, type=Path)
    main(p.parse_args().root)
