"""G38: yalnız bellek içi mock kanıtlarla owner incelemesi ön kontrolü.

Bu modül kriptografik doğrulama, gerçek owner okuması veya disk güvenliği
sağlamaz. Makbuz güveni, yapılandırılmış MockSource'un verdiği aynı Python
nesnesi ve kayıtlı içeriğiyle sınırlıdır; düşmanca Python koduna karşı sınır
değildir. Pozisyon değeri net bakiye değil, brüt açık miktar modelidir.
Hiçbir sonuç kilit açma veya yeni giriş yetkisi vermez.
"""

import argparse
from dataclasses import dataclass, field, replace
from decimal import Decimal
import json


KINDS = ("position", "normal_open_orders", "conditional_algo_open_orders")
RECORD_STATES = {"ledger_closed": "CLOSED", "wal": "CLOSED", "incident": "SUPHELI"}
MOCK_NOW_MS = 1_000_000


@dataclass(frozen=True)
class Binding:
    scope: str
    account: str
    symbol: str


def _valid_binding(binding):
    return type(binding) is Binding and all(
        type(value) is str and bool(value.strip()) and value == value.strip()
        for value in (binding.scope, binding.account, binding.symbol)
    )


def _binding_stamp(binding):
    if type(binding) is not Binding:
        return None
    return (binding.scope, binding.account, binding.symbol)


@dataclass(frozen=True, eq=False)
class MockReceipt:
    source_id: str
    receipt_id: int
    binding: Binding
    kind: str
    value: object
    observed_at_ms: int


def _receipt_stamp(receipt):
    return (
        receipt.source_id, type(receipt.receipt_id), receipt.receipt_id,
        _binding_stamp(receipt.binding),
        receipt.kind, type(receipt.value), receipt.value,
        type(receipt.observed_at_ms), receipt.observed_at_ms,
    )


class MockSource:
    """Ayrı bir mock gözlem kaynağı; gerçek imza/SDK içermez.

    issue() test verisini olduğu gibi kaydeder. Kaynak kimliğinin doğrulanması,
    veri içeriğinin sıfır, geçerli veya güncel olduğunu tek başına kanıtlamaz.
    """

    def __init__(self, source_id, kind, binding):
        self.source_id = source_id
        self.kind = kind
        self.binding = binding
        self._issued = {}

    def issue(self, value, observed_at_ms):
        if type(value) not in (int, Decimal, str, bool, type(None)):
            raise TypeError("Mock değer değişmez bir skaler olmalı")
        receipt = MockReceipt(
            self.source_id, len(self._issued) + 1,
            Binding(self.binding.scope, self.binding.account, self.binding.symbol),
            self.kind, value, observed_at_ms,
        )
        self._issued[id(receipt)] = (receipt, _receipt_stamp(receipt))
        return receipt

    def owns(self, receipt):
        if type(receipt) is not MockReceipt:
            return False
        issued = self._issued.get(id(receipt))
        return (
            issued is not None and issued[0] is receipt
            and issued[1] == _receipt_stamp(receipt)
        )


@dataclass(frozen=True)
class JournalRecord:
    record_id: str
    kind: str
    binding: Binding
    ledger_id: str
    wal_id: str
    incident_id: str
    state: str


@dataclass(frozen=True, eq=False)
class MockSnapshot:
    generation: int
    records: tuple
    cleanup_phase: str
    observed_at_ms: int


def _record_stamp(record):
    if type(record) is not JournalRecord:
        return ("INVALID_RECORD", id(record))
    return (
        record.record_id, record.kind, _binding_stamp(record.binding),
        record.ledger_id, record.wal_id, record.incident_id, record.state,
    )


def _snapshot_stamp(snapshot):
    return (
        type(snapshot.generation), snapshot.generation,
        tuple(_record_stamp(r) for r in snapshot.records),
        snapshot.cleanup_phase, type(snapshot.observed_at_ms), snapshot.observed_at_ms,
    )


class MockJournal:
    """Disk yerine bellek modeli; replace() yalnız mock nesneyi değiştirir.

    Nesneye bağlı snapshot ve nesil kontrolü TOCTOU tasarımını örnekler.
    Gerçek dosya, symlink, kilit, fsync veya atomik disk işlemi uygulanmaz.
    """

    def __init__(self, records, cleanup_phase="INTACT"):
        self._records = tuple(records)
        self._cleanup_phase = cleanup_phase
        self._generation = 0
        self._issued = {}

    def replace(self, records, cleanup_phase="INTACT"):
        self._records = tuple(records)
        self._cleanup_phase = cleanup_phase
        self._generation += 1

    def capture(self, observed_at_ms):
        snapshot = MockSnapshot(
            self._generation, self._records, self._cleanup_phase, observed_at_ms,
        )
        self._issued[id(snapshot)] = (snapshot, _snapshot_stamp(snapshot))
        return snapshot

    def owns(self, snapshot):
        if type(snapshot) is not MockSnapshot or type(snapshot.records) is not tuple:
            return False
        issued = self._issued.get(id(snapshot))
        return (
            issued is not None and issued[0] is snapshot
            and issued[1] == _snapshot_stamp(snapshot)
        )

    def is_current(self, snapshot):
        return self.owns(snapshot) and snapshot.generation == self._generation


@dataclass(frozen=True)
class Decision:
    disposition: str
    reason_codes: tuple
    unlock: str = field(default="NO_UNLOCK", init=False)
    new_entry: str = field(default="NO_NEW_ENTRY", init=False)
    manual_owner_review_required: bool = field(default=True, init=False)
    mode: str = field(default="OFFLINE_MOCK_ONLY", init=False)

    def to_dict(self):
        return {
            "mode": self.mode, "disposition": self.disposition,
            "unlock": self.unlock, "new_entry": self.new_entry,
            "manual_owner_review_required": self.manual_owner_review_required,
            "reason_codes": list(self.reason_codes),
        }


def _time_reasons(observed, now_ms, max_age_ms, label):
    if type(observed) is not int or observed < 0:
        return [f"{label}_INVALID_TIME"]
    if observed > now_ms:
        return [f"{label}_FUTURE"]
    if now_ms - observed > max_age_ms:
        return [f"{label}_STALE"]
    return []


def _binding_reasons(actual, expected, label):
    if not _valid_binding(actual):
        return [f"{label}_INVALID_BINDING"]
    return [
        f"{label}_{name.upper()}_MISMATCH"
        for name in ("scope", "account", "symbol")
        if getattr(actual, name) != getattr(expected, name)
    ]


def _journal_reasons(snapshot, binding, now_ms, max_age_ms):
    reasons = _time_reasons(snapshot.observed_at_ms, now_ms, max_age_ms, "JOURNAL")
    if snapshot.cleanup_phase != "INTACT":
        reasons.append("CRASH_PARTIAL_CLEANUP_OR_NONINTACT_STATE")
    by_kind = {}
    seen_ids = set()
    for record in snapshot.records:
        if type(record) is not JournalRecord:
            reasons.append("UNKNOWN_JOURNAL_RECORD")
            continue
        if type(record.kind) is not str or record.kind not in RECORD_STATES:
            reasons.append("UNKNOWN_JOURNAL_RECORD")
            continue
        identifiers = (record.record_id, record.ledger_id, record.wal_id, record.incident_id)
        if not all(type(x) is str and x.strip() and x == x.strip() for x in identifiers):
            reasons.append("INVALID_RECORD_ID")
            continue
        if record.record_id in seen_ids or record.kind in by_kind:
            reasons.append("DUPLICATE_JOURNAL_RECORD")
        seen_ids.add(record.record_id)
        by_kind[record.kind] = record
        reasons.extend(_binding_reasons(record.binding, binding, "JOURNAL"))
        if record.state != RECORD_STATES[record.kind]:
            reasons.append(f"{record.kind.upper()}_STATE_INVALID")
    for kind in RECORD_STATES:
        if kind not in by_kind:
            reasons.append(f"MISSING_{kind.upper()}")
    if len(by_kind) == len(RECORD_STATES):
        expected = tuple(by_kind[kind].record_id for kind in RECORD_STATES)
        if len(set(expected)) != 3:
            reasons.append("JOURNAL_IDS_NOT_DISTINCT")
        for record in by_kind.values():
            if (record.ledger_id, record.wal_id, record.incident_id) != expected:
                reasons.append("LEDGER_WAL_INCIDENT_LINK_MISMATCH")
    return reasons


class OfflinePreflight:
    """Yalnız yapılandırılmış üç ayrı mock kaynağına güvenen saf karar katmanı."""

    def __init__(self, binding, sources, max_age_ms=5_000):
        self.binding = binding
        self.sources = sources
        self.max_age_ms = max_age_ms

    def evaluate(self, receipts, journal, snapshot, *, now_ms):
        reasons = ["MOCK_PROVENANCE_ONLY", "MANUAL_OWNER_REVIEW_REQUIRED"]
        failures = []
        if (
            not _valid_binding(self.binding)
            or type(now_ms) is not int or now_ms < 0
            or type(self.max_age_ms) is not int or self.max_age_ms <= 0
        ):
            failures.append("INVALID_PREFLIGHT_CONFIGURATION")
        sources = self.sources
        if type(sources) is not tuple or len(sources) != 3 or any(
            type(source) is not MockSource for source in sources
        ):
            failures.append("INVALID_SOURCE_CONFIGURATION")
        elif (
            len({id(source) for source in sources}) != 3
            or any(type(s.source_id) is not str or not s.source_id.strip() for s in sources)
            or len({s.source_id for s in sources}) != 3
            or any(type(s.kind) is not str or s.kind not in KINDS for s in sources)
            or len({s.kind for s in sources}) != 3
        ):
            failures.append("INVALID_SOURCE_CONFIGURATION")
        if failures:
            return Decision("FAIL_CLOSED", tuple(reasons + failures))

        if type(receipts) is not tuple:
            failures.append("INVALID_RECEIPT_COLLECTION")
            receipts = ()
        seen_kinds = set()
        seen_objects = set()
        by_kind = {s.kind: s for s in sources}
        for receipt in receipts:
            if type(receipt) is not MockReceipt:
                failures.append("UNTRUSTED_RECEIPT")
                continue
            if type(receipt.kind) is not str or receipt.kind not in KINDS:
                failures.append("UNKNOWN_RECEIPT_KIND")
                continue
            if receipt.kind in seen_kinds or id(receipt) in seen_objects:
                failures.append("DUPLICATE_RECEIPT")
            seen_kinds.add(receipt.kind)
            seen_objects.add(id(receipt))
            source = by_kind[receipt.kind]
            if not MockSource.owns(source, receipt):
                failures.append("RECEIPT_IDENTITY_OR_CONTENT_MISMATCH")
            if receipt.source_id != source.source_id or receipt.kind != source.kind:
                failures.append("SOURCE_RECEIPT_METADATA_MISMATCH")
            failures.extend(_binding_reasons(source.binding, self.binding, "SOURCE"))
            failures.extend(_binding_reasons(receipt.binding, self.binding, "RECEIPT"))
            failures.extend(_time_reasons(
                receipt.observed_at_ms, now_ms, self.max_age_ms, receipt.kind.upper(),
            ))
            value = receipt.value
            valid_value = type(value) is int or (
                receipt.kind == "position" and type(value) is Decimal and value.is_finite()
            )
            if not valid_value:
                failures.append(f"{receipt.kind.upper()}_INVALID_VALUE")
            elif value != 0:
                failures.append(f"{receipt.kind.upper()}_NONZERO")
        for kind in KINDS:
            if kind not in seen_kinds:
                failures.append(f"MISSING_{kind.upper()}_RECEIPT")

        if type(journal) is not MockJournal or not MockJournal.owns(journal, snapshot):
            failures.append("UNTRUSTED_JOURNAL_SNAPSHOT")
        else:
            failures.extend(_journal_reasons(snapshot, self.binding, now_ms, self.max_age_ms))
            if not MockJournal.is_current(journal, snapshot):
                failures.append("JOURNAL_CHANGED_SINCE_CAPTURE")
        if failures:
            return Decision("FAIL_CLOSED", tuple(dict.fromkeys(reasons + failures)))
        return Decision(
            "MANUAL_OWNER_REVIEW",
            tuple(reasons + ["EVIDENCE_COHERENT_FOR_MANUAL_REVIEW"]),
        )


@dataclass(frozen=True)
class Scenario:
    preflight: OfflinePreflight
    receipts: tuple
    journal: MockJournal
    snapshot: MockSnapshot
    now_ms: int = MOCK_NOW_MS

    def run(self):
        return self.preflight.evaluate(
            self.receipts, self.journal, self.snapshot, now_ms=self.now_ms,
        )


SCENARIOS = (
    "coherent", "bool-spoof", "missing-algo", "position-open", "normal-order-open",
    "algo-order-open", "stale", "future", "account-mismatch", "scope-mismatch",
    "symbol-mismatch", "duplicate", "unknown-record", "broken-links",
    "crash-partial", "toctou",
)


def build_scenario(name="coherent"):
    """Tamamen sentetik ve tekrarlanabilir fixture; dış veri kabul etmez."""
    if name not in SCENARIOS:
        raise ValueError("Bilinmeyen offline senaryo")
    binding = Binding("mock-owner-scope", "mock-account", "MOCKUSDT")
    source_binding = binding
    if name in ("account-mismatch", "scope-mismatch", "symbol-mismatch"):
        values = dict(scope=binding.scope, account=binding.account, symbol=binding.symbol)
        values[name.split("-")[0]] = "different-mock-value"
        source_binding = Binding(**values)
    sources = tuple(MockSource(f"mock-{kind}", kind, source_binding) for kind in KINDS)
    observed = MOCK_NOW_MS - 100
    if name == "stale":
        observed = MOCK_NOW_MS - 5_001
    elif name == "future":
        observed = MOCK_NOW_MS + 1
    nonzero = {"position-open": 0, "normal-order-open": 1, "algo-order-open": 2}.get(name)
    receipts = tuple(s.issue(1 if i == nonzero else 0, observed) for i, s in enumerate(sources))
    if name == "bool-spoof":
        receipts = ({"signed_owner_reads": True, "signed_flat_confirmed": True},)
    elif name == "missing-algo":
        receipts = receipts[:2]
    elif name == "duplicate":
        receipts += (receipts[0],)
    identifiers = ("ledger-1", "wal-1", "incident-1")
    records = tuple(
        JournalRecord(record_id, kind, binding, *identifiers, state)
        for record_id, (kind, state) in zip(identifiers, RECORD_STATES.items())
    )
    if name == "unknown-record":
        records += ({"kind": "unknown"},)
    elif name == "broken-links":
        records = (replace(records[0], wal_id="orphan-wal"),) + records[1:]
    phase = "PARTIAL" if name == "crash-partial" else "INTACT"
    journal = MockJournal(records, phase)
    snapshot = journal.capture(MOCK_NOW_MS - 100)
    if name == "toctou":
        journal.replace(records, phase)
    return Scenario(OfflinePreflight(binding, sources), receipts, journal, snapshot)


def main(argv=None):
    parser = argparse.ArgumentParser(description="G38: yalnız offline/mock kilit kanıt incelemesi")
    parser.add_argument("--scenario", choices=SCENARIOS + ("all",), default="coherent",
                        help="Sentetik senaryo (varsayılan: coherent)")
    args = parser.parse_args(argv)
    names = SCENARIOS if args.scenario == "all" else (args.scenario,)
    results = [{"scenario": name, **build_scenario(name).run().to_dict()} for name in names]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 2 if any(r["disposition"] == "FAIL_CLOSED" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
