# ATLAS FAST — Protective WAL bounded pre-send wait candidate

**Date:** 2026-11-05. **Development only:** `ATLAS_FAST_DEV`. **Release: NO-GO**.

## Scope

`protective_wal_deadline_candidate.py` is a strictly offline experimental bounded wait around the existing WAL *append*, for STOP/TP1/TP2 only. It retries solely when the pre-send WAL fence returns exactly `WAL_FENCE_BUSY`. Any duplicate identity, resolved scope, malformed WAL or other failure immediately raises without retry. It never accesses signed Binance, sends or resends orders, or releases an owner approval.

The allowed configured timeout is 0 to 0.5 seconds, with a polling interval at most 0.05 seconds. The default is 0.1 seconds. If exhausted the code raises `PROTECTIVE_WAL_DEADLINE_NO_POST_OWNER_REVIEW`. This is a **test candidate**, not a validated upper latency bound for a live STOP order or a guarantee of STOP availability. The wait period is *before* any POST, not permission to retry ambiguous POST outcomes.

## Regression coverage

`tests/test_protective_wal_deadline_candidate.py` adds eight tests:
- Uncontended STOP WAL write
- Busy-lock exhaustion creates no WAL journal
- A subsequent pre-send attempt after the external lock is released
- Duplicate WAL rejected without retries
- Resolved marker rejected without retries
- ENTRY role refused
- Excessive timeout refused
- Independent emergency alarm latch remains persistable after the bounded wait fails

Full offline suite: **246/246 passed** (238+8).

## Unproven and blocked for production

The timeout test uses a deterministic fake clock and tests writer-fence lock contention, not a proven real-time latency bound. For actual protective STOP order availability, next steps include independent-process timing tests, lock priority/ordering, full Owner mutation-lease integration, bounded WAL scan/fsync durations, emergency owner intervention, and a proven atomic alarm-to-ENTRY gate.

Active production Owner SDK unchanged. Development marker creation remains disabled and NO-GO, emergency latch checked only in dev startup. RC1 unchanged. No live order, account mutation, settings change or GitHub push.
