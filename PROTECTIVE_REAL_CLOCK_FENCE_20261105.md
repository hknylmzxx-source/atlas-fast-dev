# ATLAS FAST — Protective WAL real-clock independent-process timing

**Date:** 2026-11-05. **Area:** `ATLAS_FAST_DEV` only. **Live release: NO-GO.**

## Test design

`tests/test_protective_real_clock_fence_offline.py` starts a separate local Python process that acquires the actual Mac POSIX `flock` WAL writer fence in a disposable private directory. Parent process invokes only `append_protective_bounded` for STOP/TP WAL records, never the Owner SDK or any Binance transport.

Five new cases:
1. A 40ms child lock hold allows STOP WAL append before a 250ms pre-send deadline.
2. A 250ms child hold with a 30ms deadline fails closed, with no STOP journal.
3. TP1 WAL append can succeed after a short independent-process lock hold.
4. When the STOP WAL deadline expires while the lock remains busy, the separate emergency alarm latch can be persisted and is sufficient to reject the experimental no-entry diagnostic.
5. If the child releases the lock only after the deadline, the timed-out STOP record is not silently retried or created.

**Result: 251/251 total offline tests passed (246+5).**

## What this does NOT prove

The child holds a fixed synthetic-duration advisory lock; this is a small real-clock contention test, **not** a statistically significant timing/latency distribution or production STOP availability SLA. The generic `append_protective_bounded` is not wired to Owner `place_stop_write`. A failed WAL append means **the protective order was not sent**; an alarm file does not close an unprotected Binance position.

No owner-private SDK modifications, live orders, permissions changes or RC1 modification. All development WAL markers remain fail-closed. Signed Binance order-absence, active stop coverage and emergency close remain unverified.

**NO-GO FOR LIVE.**
