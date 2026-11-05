# ATLAS FAST — Unprotected exposure quarantine offline review

**Date:** 2026-11-05. **Scope:** ATLAS_FAST_DEV only. **Live decision: NO-GO.**

## Work done

Added `unprotected_exposure_quarantine_candidate.py` as an independent, **offline-only diagnostic** for an exposure state of NONZERO or UNKNOWN when protective STOP state is ABSENT, UNKNOWN or UNVERIFIED. It accepts an enumerated failure cause and attempts an owner-private durable emergency latch without obtaining the contended WAL writer fence. The result never authorizes entry, declares STOP coverage, declares FLAT or permits automatic POST retry.

On persistence failure, quarantine raises `ALARM_PERSISTENCE_FAILED_HALT_ESCALATE`, rather than interpreting persistence failure as clearance. An existing latch is reported separately and must not be mistaken for a newly created, validated alarm record. Presence is checked in a second read, but this is **not atomic entry authorization** and remains susceptible to races.

Added nine synthetic tests: nonzero exposure, WAL lock contention, repeat alarm, corrupt existing alarm, UNKNOWN exposure and STOP, invalid states, invalid failure cause, persistence failure, and no fabricated exchange protection.

Total **271/271 offline tests passed (262+9).**

## Remaining blockers

- Candidate not connected to actual Owner SDK, sender, signed exchange reads, STOP placement or emergency flatten.
- Presence of an alarm does not establish a working STOP, verified FLAT, or manual owner acknowledgment.
- A failing alarm persistence path must HALT and escalate; a returned previous latch does not prove valid alarm contents.
- Atomic alarm-vs-entry gate and full cross-writer WAL contract not proven.
- Development WAL markers remain fail-closed; production Owner SDK, RC1, Binance and GitHub untouched.

**NO-GO FOR LIVE.**
