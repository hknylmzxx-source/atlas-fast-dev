"""OFFLINE ONLY: reject synthetic account mutation across FLAT review windows.

A stable source-reported revision is only a consistency *claim*, not trusted
exchange sequence authority or proof of an atomic Binance snapshot.
Never authorizes entry, resolves WAL, or clears an emergency alarm.
"""
from bracketed_observation_coherence_candidate import assess_bracketed_observation

def assess_mutation_window(*, before, after, reads, symbol, now_ms):
    reasons=[]
    sequence=assess_bracketed_observation(reads=reads,symbol=symbol,
                                         now_ms=now_ms)
    if not sequence["synthetic_read_sequence_consistent"]:
        reasons.append("BRACKETED_READS_REJECTED")
    if type(before) is not dict or type(after) is not dict:
        reasons.append("MUTATION_WATERMARK_MISSING")
        before={} if type(before) is not dict else before
        after={} if type(after) is not dict else after
    for label,record in (("BEFORE",before),("AFTER",after)):
        if record.get("symbol")!=symbol:
            reasons.append(label+"_WATERMARK_SYMBOL_MISMATCH")
        if type(record.get("revision")) is not int or record["revision"]<0:
            reasons.append(label+"_REVISION_INVALID")
        if type(record.get("outstanding_write_count")) is not int or record["outstanding_write_count"]!=0:
            reasons.append(label+"_OUTSTANDING_WRITES_UNVERIFIED")
        if record.get("write_lease_held") is not False:
            reasons.append(label+"_MUTATION_LEASE_NOT_CLEAR")
        if type(record.get("observed_at_ms")) is not int:
            reasons.append(label+"_TIME_INVALID")
    if not reasons:
        if before["revision"]!=after["revision"]:
            reasons.append("MUTATION_REVISION_CHANGED_DURING_READS")
        if not (before["observed_at_ms"]<=reads["account_before"]["start_ms"]
                and after["observed_at_ms"]>=reads["account_after"]["end_ms"]
                and after["observed_at_ms"]<=now_ms):
            reasons.append("WATERMARK_DOES_NOT_BRACKET_READS")
    return {
        "synthetic_mutation_window_consistent":not reasons,
        "reasons":sorted(set(reasons)),
        "source_mutation_sequence_authenticated":False,
        "unobserved_exchange_writes_excluded":False,
        "atomic_entry_gate_verified":False,
        "entry_authorized":False,
        "may_clear_latch":False,
        "decision":"NO_ENTRY_MANUAL_OWNER_REVIEW",
    }
