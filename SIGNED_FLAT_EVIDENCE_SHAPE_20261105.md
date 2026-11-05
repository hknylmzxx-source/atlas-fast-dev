# ATLAS FAST — Synthetic signed-FLAT evidence shape review

**Date:** 2026-11-05. **Workspace:** `ATLAS_FAST_DEV`. **Release:** NO-GO.

Added `signed_flat_evidence_shape_candidate.py` to check the *shape, consistency and local age* of caller-supplied synthetic position and normal/algo order observations. The checker **does not call Binance**, verify real authentication/signatures, validate server provenance or authorize release. Distinct caller-provided request IDs are not reliable signed provenance.

Checks include typed and bounded observation times, exact target symbol, three unique nonempty claimed read-request IDs, nonempty position data with finite decimal zero amounts, and explicitly empty normal/algo order lists. Failing or contradictory input is flagged, including stale/future time, bad symbol, NaN position, nonzero exposure, outstanding orders and missing reads. The result always includes `caller_claims_unverified=True`, `authenticated_signed_get_verified=False`, `position_flat_independently_verified=False`, `entry_authorized=False`, and `may_clear_latch=False`.

Added 15 offline tests in `tests/test_signed_flat_evidence_shape_candidate.py`. **Total 294/294 offline tests passed (279+15).** The full-correct-looking synthetic case deliberately does NOT release the latch.

This is a necessary input-format filter, **not sufficient evidence** to clear emergency alarms. Still required: real trusted signed GET channel provenance and account binding, multi-read coherence under an explicit observation window, strict Owner authorization, verified STOP/TP final states, policy ledger reconciliation, shared writer fence and atomic entry gate. No live SDK modifications, Binance writes, permission changes, GitHub push or RC1 modification.

**Decision: NO-GO FOR LIVE.**
