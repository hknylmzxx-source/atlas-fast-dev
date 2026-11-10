"""G50: yalnız redakte JSON için stdlib çevrimdışı TP olay korelasyonu.

Gerçek 6/9 Kasım 2026 USUSDT kanıtı bu modülle sağlanmaz. ``SIGNED_RECEIPT``
bir kullanıcı kaynak beyanıdır; imza doğrulaması değildir. Sentetik veri ayrı
namespace'tedir. Her sonuç sıfır yetkilidir ve gerçek neden UNRESOLVED kalır.

CLI: ``python3 -B tp_incident_evidence_offline_g50.py --demo`` veya redakte
JSON'u stdin'e verin. Dosya/yol/URL açılmaz, kayıt tutulmaz. Ham receipt, stream,
WAL veya secret kabul edilmez: kullanıcı önce belgelenen dar şemaya çevirmeli,
tüm kimlikleri tutarlı ``r:...`` takma adlarla değiştirmelidir. Bu biçim kontrolü
redaksiyonun doğruluğunu kanıtlamaz. Rapor bu takma adları da yeniden adlandırır.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import json
import re
import sys


SCHEMA = "ATLAS_G50_TP_INCIDENT_EVIDENCE_V1"
INCIDENT_DAYS = ("2026-11-06", "2026-11-09")
MAX_BYTES = 1_048_576
MAX_EVENTS = 2000
# Statik geliştirme kaynağı referansı; import, çalıştırma veya canlı sürüm iddiası yok.
SOURCE_REFERENCE = {
    "base": "1d9e3aa0131a664b1ddb8f7100ada3f6e7719b5b",
    "source": "owner_single_execution.py",
    "actual_order": 564,
    "checked": 571,
    "cleanup_owned_exits": 751,
    "callgraph": [
        "run_once -> one_write -> checked -> actual_order",
        "actual_order: STOP -> query_algo_order(clientAlgoId)",
        "actual_order: ENTRY/TP -> query_order(symbol,newClientOrderId)",
        "run_once -> cleanup_owned_exits -> snapshot/exposure",
        "cleanup_owned_exits: signed FLAT -> exact active identifiers",
        "cleanup_owned_exits: journal(CANCEL_STOP/TP) -> delete_once -> snapshot",
    ],
    "limit": "Kaynak akışı olay anında çalıştığının veya iptal nedeninin kanıtı değildir.",
}


class Verdict(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class Source(str, Enum):
    SIGNED_RECEIPT = "SIGNED_RECEIPT"
    USER_STREAM = "USER_STREAM"
    WAL = "WAL"


class Origin(str, Enum):
    SYNTHETIC = "SYNTHETIC"
    USER_REDACTED = "USER_REDACTED"


class EventType(str, Enum):
    ORDER_STATE = "ORDER_STATE"
    QUERY_MISSING = "QUERY_MISSING"
    OPEN_LIST_ABSENT = "OPEN_LIST_ABSENT"
    POSITION = "POSITION"
    CANCEL_INTENT = "CANCEL_INTENT"
    DELETE_ACK = "DELETE_ACK"
    STREAM_GAP = "STREAM_GAP"


class FindingCode(str, Enum):
    MISSING_REAL_EVIDENCE = "MISSING_REAL_EVIDENCE"
    MISSING_SOURCE = "MISSING_SOURCE"
    TP_EVIDENCE_MISSING = "TP_EVIDENCE_MISSING"
    PROVENANCE_UNVERIFIED = "PROVENANCE_UNVERIFIED"
    FRAGMENTARY_EVIDENCE = "FRAGMENTARY_EVIDENCE"
    SYNTHETIC_ISOLATED = "SYNTHETIC_ISOLATED"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    EVENT_REF_CONFLICT = "EVENT_REF_CONFLICT"
    IDENTITY_SCOPE_CONFLICT = "IDENTITY_SCOPE_CONFLICT"
    SERVER_ID_CONFLICT = "SERVER_ID_CONFLICT"
    STATE_CONFLICT = "STATE_CONFLICT"
    TERMINAL_STATE_CONFLICT = "TERMINAL_STATE_CONFLICT"
    FILL_REGRESSION = "FILL_REGRESSION"
    QUANTITY_CONFLICT = "QUANTITY_CONFLICT"
    QUANTITY_EVIDENCE_MISSING = "QUANTITY_EVIDENCE_MISSING"
    CLOCK_CONFLICT = "CLOCK_CONFLICT"
    SEQUENCE_DISCONTINUITY = "SEQUENCE_DISCONTINUITY"
    SEQUENCE_CONFLICT = "SEQUENCE_CONFLICT"
    STREAM_GAP_REPORTED = "STREAM_GAP_REPORTED"
    QUERY_MISSING_NOT_CANCELED = "QUERY_MISSING_NOT_CANCELED"
    OPEN_ABSENCE_NOT_TERMINAL = "OPEN_ABSENCE_NOT_TERMINAL"
    FLAT_NOT_TP_CANCEL_PROOF = "FLAT_NOT_TP_CANCEL_PROOF"
    CANCEL_NOT_CONFIRMED = "CANCEL_NOT_CONFIRMED"
    DELETE_ACK_REPORTED = "DELETE_ACK_REPORTED"
    TEMPORAL_ASSOCIATION_ONLY = "TEMPORAL_ASSOCIATION_ONLY"
    CANCELED_REPORTED = "CANCELED_REPORTED"
    EXPIRY_CAUSE_UNRESOLVED = "EXPIRY_CAUSE_UNRESOLVED"
    PARTIAL_FILL_REPORTED = "PARTIAL_FILL_REPORTED"
    FILL_REPORTED = "FILL_REPORTED"
    ACTOR_UNKNOWN = "ACTOR_UNKNOWN"
    CLAIM_UNVERIFIED = "CLAIM_UNVERIFIED"
    SIGNED_ORDER_EVIDENCE_MISSING = "SIGNED_ORDER_EVIDENCE_MISSING"


class EvidenceError(ValueError):
    """Hata yalnız sabit kod taşır; kullanıcı alanı veya exception metni taşımaz."""


@dataclass(frozen=True)
class Scope:
    account: str
    parent: str
    slot: str
    generation: int
    position_side: str


@dataclass(frozen=True)
class Identity:
    role: str
    kind: str
    endpoint: str
    client_id: str | None
    order_id: str | None
    client_algo_id: str | None
    algo_id: str | None
    actual_order_id: str | None

    @property
    def client(self) -> str:
        return self.client_id if self.kind == "NORMAL" else self.client_algo_id

    @property
    def server(self) -> str | None:
        return self.order_id if self.kind == "NORMAL" else self.algo_id


@dataclass(frozen=True)
class Provenance:
    origin: Origin
    source: Source
    artifact_ref: str
    authenticity: str
    coverage: str


@dataclass(frozen=True)
class Event:
    event_ref: str
    incident_day: str
    symbol: str
    occurred_at: datetime
    observed_at: datetime
    scope: Scope
    identity: Identity | None
    provenance: Provenance
    event_type: EventType
    status_field: str | None
    status: str | None
    query_code: str | None
    orig_qty: str | None
    executed_qty: str | None
    position_qty: str | None
    actor: str
    reason: str | None
    sequence: int | None
    sequence_domain: str | None

    @property
    def order_key(self) -> tuple:
        # Gün, köken ve tam lifecycle ayrıdır; sunucu kimliği eklenmesi client bağını bozmaz.
        i = self.identity
        return (self.incident_day, self.provenance.origin, self.scope,
                self.symbol, i.role, i.kind, i.client) if i else ()


@dataclass(frozen=True)
class Finding:
    code: FindingCode
    verdict: Verdict
    evidence_refs: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class TimelineRow:
    event_ref: str
    occurred_at_utc: str
    observed_at_utc: str
    origin: Origin
    source: Source
    artifact_ref: str
    authenticity: str
    coverage: str
    account_ref: str
    scope_ref: str
    slot_ref: str
    lifecycle_ref: str
    position_side: str
    role: str | None
    kind: str | None
    client_ref: str | None
    server_ref: str | None
    actual_order_ref: str | None
    event_type: EventType
    status_field: str | None
    status_claim: str | None
    query_code: str | None
    orig_qty_claim: str | None
    executed_qty_claim: str | None
    position_qty_claim: str | None
    actor_claim: str
    reason_claim: str | None


@dataclass(frozen=True)
class IncidentReport:
    incident_day: str
    verdict: Verdict
    findings: tuple[Finding, ...]
    timeline: tuple[TimelineRow, ...]


@dataclass(frozen=True)
class Authority:
    entry_authorized: bool = False
    owner_authorized: bool = False
    unlock: bool = False
    stop_proven: bool = False


@dataclass(frozen=True)
class Report:
    verdict: Verdict
    real_incident_cause: Verdict
    incidents: tuple[IncidentReport, ...]
    authority: Authority

    def to_dict(self) -> dict:
        return {"schema": SCHEMA, **asdict(self), "source_reference": deepcopy(SOURCE_REFERENCE),
                "limits": [
                    "İmza, capture completeness ve aktör doğrulanmaz; yalnız beyan işlenir.",
                    "Zaman sırası nedensellik değildir; gün etiketi kullanıcı tarafından verilir.",
                    "Sentetik olaylar gerçek olaylar ile eşleştirilmez.",
                    "Giriş, owner, unlock ve STOP proof yetkisi üretmez.",
                ]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True, indent=2)


ALIAS = re.compile(r"r:[a-z][a-z0-9_-]{0,31}\Z", re.ASCII)
TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z", re.ASCII)
QUANTITY = re.compile(r"-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,18})?\Z", re.ASCII)


def _fail(code: str) -> None:
    raise EvidenceError(code)


def _fields(value: object, names: str) -> dict:
    if type(value) is not dict or value.keys() != frozenset(names.split()):
        _fail("SCHEMA_FIELDS_INVALID")
    return value


def _choice(value: object, choices) -> str:
    if type(value) is not str or value not in tuple(choices):
        _fail("ENUM_INVALID")
    return value


def _alias(value: object, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str or not ALIAS.fullmatch(value):
        _fail("REDACTED_ALIAS_REQUIRED")
    return value


def parse_utc(value: object) -> datetime:
    """Saniye zorunlu RFC3339 altkümesi; naive/epoch/unknown -00:00 reddedilir."""
    if type(value) is not str or not TIME.fullmatch(value) or value.endswith("-00:00"):
        _fail("TIMESTAMP_INVALID")
    if value[-6:-5] in ("+", "-"):
        hours, minutes = int(value[-5:-3]), int(value[-2:])
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            _fail("TIMESTAMP_INVALID")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        _fail("TIMESTAMP_INVALID")


def _qty(value: object, signed: bool = False) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not QUANTITY.fullmatch(value):
        _fail("QUANTITY_INVALID")
    if not signed and value.startswith("-"):
        _fail("QUANTITY_INVALID")
    return value


def _parse_scope(raw: object) -> Scope:
    d = _fields(raw, "account parent slot generation position_side")
    if type(d["generation"]) is not int or not 0 <= d["generation"] <= 2**31 - 1:
        _fail("GENERATION_INVALID")
    return Scope(_alias(d["account"]), _alias(d["parent"]), _alias(d["slot"]),
                 d["generation"], _choice(d["position_side"], ("BOTH", "LONG", "SHORT")))


def _parse_identity(raw: object) -> Identity | None:
    if raw is None:
        return None
    d = _fields(raw, "role kind endpoint client_id order_id client_algo_id algo_id actual_order_id")
    role = _choice(d["role"], ("ENTRY", "TP1", "TP2", "STOP"))
    kind = _choice(d["kind"], ("NORMAL", "ALGO"))
    if kind != ("ALGO" if role == "STOP" else "NORMAL"):
        _fail("OWNER_ROLE_KIND_CONFLICT")
    endpoint = "/fapi/v1/algoOrder" if kind == "ALGO" else "/fapi/v1/order"
    if d["endpoint"] != endpoint:
        _fail("ENDPOINT_KIND_CONFLICT")
    ids = {k: _alias(d[k], True) for k in
           ("client_id", "order_id", "client_algo_id", "algo_id", "actual_order_id")}
    if kind == "NORMAL":
        if ids["client_id"] is None or any(ids[k] is not None for k in
                                            ("client_algo_id", "algo_id", "actual_order_id")):
            _fail("IDENTITY_NAMESPACE_CONFLICT")
    elif (ids["client_algo_id"] is None or ids["client_id"] is not None
          or ids["order_id"] is not None
          or (ids["actual_order_id"] is not None and ids["algo_id"] is None)):
        _fail("IDENTITY_NAMESPACE_CONFLICT")
    return Identity(role, kind, endpoint, **ids)


def _parse_event(raw: object) -> Event:
    d = _fields(raw, "event_ref incident_day symbol occurred_at observed_at scope identity provenance "
                "event_type status_field status query_code orig_qty executed_qty position_qty "
                "actor reason sequence sequence_domain")
    p = _fields(d["provenance"], "origin source artifact_ref authenticity coverage")
    prov = Provenance(Origin(_choice(p["origin"], Origin)), Source(_choice(p["source"], Source)),
                      _alias(p["artifact_ref"]),
                      _choice(p["authenticity"], ("NOT_VERIFIED_OFFLINE",)),
                      _choice(p["coverage"], ("FRAGMENT", "CALLER_CLAIMED_COMPLETE")))
    kind = EventType(_choice(d["event_type"], EventType))
    identity = _parse_identity(d["identity"])
    needs_identity = kind not in (EventType.POSITION, EventType.STREAM_GAP)
    if needs_identity != (identity is not None):
        _fail("EVENT_IDENTITY_REQUIRED_OR_FORBIDDEN")
    state = kind in (EventType.ORDER_STATE, EventType.DELETE_ACK)
    if state:
        if prov.source == Source.WAL:
            _fail("WAL_IS_NOT_EXCHANGE_STATE")
        expected_field = "status" if identity.kind == "NORMAL" else "algoStatus"
        _choice(d["status_field"], (expected_field,))
        statuses = (("NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED",
                     "EXPIRED_IN_MATCH", "REJECTED", "UNKNOWN") if identity.kind == "NORMAL"
                    else ("NEW", "WORKING", "TRIGGERED", "FINISHED", "CANCELED", "EXPIRED",
                          "REJECTED", "UNKNOWN"))
        _choice(d["status"], statuses)
    elif d["status_field"] is not None or d["status"] is not None:
        _fail("STATUS_FORBIDDEN")
    if kind == EventType.DELETE_ACK:
        if prov.source != Source.SIGNED_RECEIPT or d["status"] != "CANCELED":
            _fail("DELETE_ACK_SOURCE_INVALID")
    if kind == EventType.CANCEL_INTENT and prov.source != Source.WAL:
        _fail("CANCEL_INTENT_SOURCE_INVALID")
    if kind == EventType.STREAM_GAP and prov.source != Source.USER_STREAM:
        _fail("STREAM_GAP_SOURCE_INVALID")
    if kind in (EventType.QUERY_MISSING, EventType.OPEN_LIST_ABSENT):
        if prov.source != Source.SIGNED_RECEIPT:
            _fail("QUERY_SOURCE_INVALID")
    if kind == EventType.QUERY_MISSING:
        _choice(d["query_code"], ("HTTP_404", "ORDER_NOT_FOUND"))
    elif d["query_code"] is not None:
        _fail("QUERY_CODE_FORBIDDEN")
    orig, executed = _qty(d["orig_qty"]), _qty(d["executed_qty"])
    position = _qty(d["position_qty"], signed=True)
    if (kind == EventType.POSITION) != (position is not None):
        _fail("POSITION_QUANTITY_REQUIRED_OR_FORBIDDEN")
    if (not state or identity.kind != "NORMAL") and (orig is not None or executed is not None):
        _fail("ORDER_QUANTITIES_FORBIDDEN")
    seq = d["sequence"]
    if seq is not None and (type(seq) is not int or not 0 <= seq <= 2**63 - 1):
        _fail("SEQUENCE_INVALID")
    domain = _alias(d["sequence_domain"], nullable=True)
    if (seq is None) != (domain is None) or (seq is not None and prov.source == Source.SIGNED_RECEIPT):
        _fail("SEQUENCE_DOMAIN_INVALID")
    return Event(_alias(d["event_ref"]), _choice(d["incident_day"], INCIDENT_DAYS),
                 _choice(d["symbol"], ("USUSDT",)), parse_utc(d["occurred_at"]),
                 parse_utc(d["observed_at"]), _parse_scope(d["scope"]), identity, prov, kind,
                 d["status_field"], d["status"], d["query_code"], orig, executed, position,
                 _choice(d["actor"], ("UNKNOWN", "OWNER_REPORTED", "USER_REPORTED",
                                      "EXCHANGE_REPORTED", "OTHER_REPORTED")),
                 None if d["reason"] is None else _choice(d["reason"],
                     ("REDUCE_ONLY_REPORTED", "STP_REPORTED", "USER_REPORTED", "OTHER_REPORTED")),
                 seq, domain)


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def parse_document(raw: bytes) -> tuple[Event, ...]:
    """Ham metni hata çıktısına taşımayan strict, boyut sınırlı normalizasyon."""
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        _fail("INPUT_SIZE_OR_TYPE_INVALID")
    try:
        d = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                       parse_constant=lambda _: _fail("NONFINITE_JSON_FORBIDDEN"))
        d = _fields(d, "schema redacted events")
        if d["schema"] != SCHEMA or d["redacted"] is not True:
            _fail("SCHEMA_OR_REDACTION_ATTESTATION_INVALID")
        if type(d["events"]) is not list or len(d["events"]) > MAX_EVENTS:
            _fail("EVENT_LIMIT_INVALID")
        return tuple(_parse_event(e) for e in d["events"])
    except EvidenceError:
        raise
    except (ValueError, TypeError, RecursionError, OverflowError):
        _fail("JSON_OR_SCHEMA_INVALID")


def _time(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sort_key(e: Event) -> tuple:
    # Son tie-breaker içerik; giriş sırası ve hash randomization çıktıyı etkilemez.
    return (e.occurred_at, e.observed_at, e.provenance.source.value,
            e.provenance.origin.value, e.event_ref, repr(e))


class _Aliases:
    def __init__(self) -> None:
        self.maps = defaultdict(dict)

    def get(self, category: str, key: object) -> str | None:
        if key is None:
            return None
        table = self.maps[category]
        if key not in table:
            table[key] = f"{category}-{len(table) + 1:04d}"
        return table[key]


def _incident(day: str, original: tuple[Event, ...]) -> IncidentReport:
    events = sorted(set(original), key=_sort_key)
    refs = {e: f"event-{n:04d}" for n, e in enumerate(events, 1)}
    findings = []

    def add(code: FindingCode, detail: str, evidence=(), review: bool = False) -> None:
        findings.append(Finding(code, Verdict.REVIEW_REQUIRED if review else Verdict.UNRESOLVED,
                                tuple(sorted({refs[e] for e in evidence})), detail))

    real = [e for e in events if e.provenance.origin == Origin.USER_REDACTED]
    if not real:
        add(FindingCode.MISSING_REAL_EVIDENCE, "Gerçek olaya ait kullanıcı redakte kanıtı yok; neden UNRESOLVED.")
    for source in Source:
        if not any(e.provenance.source == source for e in real):
            add(FindingCode.MISSING_SOURCE, f"Gerçek olay için kaynak eksik: {source.value}.")
    for role in ("TP1", "TP2"):
        if not any(e.identity and e.identity.role == role and
                   e.event_type in (EventType.ORDER_STATE, EventType.DELETE_ACK) for e in real):
            add(FindingCode.TP_EVIDENCE_MISSING, f"{role}: gerçek olay durum kanıtı eksik.")
    if events:
        add(FindingCode.PROVENANCE_UNVERIFIED, "Kaynak etiketleri beyan; imza ve aktör doğrulanmadı.", events)
        add(FindingCode.FRAGMENTARY_EVIDENCE, "Tamlık beyanı dahil; sağlanan parçalar eksiksiz geçmiş kanıtı değildir.", events)
    synthetic = [e for e in events if e.provenance.origin == Origin.SYNTHETIC]
    if synthetic:
        add(FindingCode.SYNTHETIC_ISOLATED, "Sentetik olaylar gerçek kanıt namespace'ine katılmaz.", synthetic)
    if len(original) != len(events):
        counts = defaultdict(int)
        for e in original:
            counts[e] += 1
        add(FindingCode.DUPLICATE_EVENT, "Aynı normalize kayıt bir kez gösterildi; yeni kanıt sayılmadı.",
            [e for e in events if counts[e] > 1])

    event_ids, bindings, orders, sequences = (defaultdict(list) for _ in range(4))
    for e in events:
        p, i = e.provenance, e.identity
        event_ids[(p.origin, e.event_ref)].append(e)
        if i:
            orders[e.order_key].append(e)
            # client/server namespace farklı; actual_order_id burada ASLA bağ kurmaz.
            for id_kind, value in (("client", i.client), ("server", i.server)):
                if value is not None:
                    bindings[(p.origin, e.scope.account, i.kind, id_kind, value)].append(e)
        if e.sequence is not None:
            sequences[(p.origin, p.source, e.scope.account, e.sequence_domain)].append(e)
        if e.observed_at < e.occurred_at:
            add(FindingCode.CLOCK_CONFLICT, "Gözlem zamanı olaydan önce; saatlerin sırası inceleme ister.", [e], True)
        if e.event_type == EventType.QUERY_MISSING:
            add(FindingCode.QUERY_MISSING_NOT_CANCELED, "404/order-not-found iptal, fill veya yeniden gönderim kanıtı değildir.", [e])
        if e.event_type == EventType.OPEN_LIST_ABSENT:
            add(FindingCode.OPEN_ABSENCE_NOT_TERMINAL, "Açık listede yokluk terminal durum veya iptal kanıtı değildir.", [e])
        if e.event_type == EventType.POSITION and Decimal(e.position_qty) == 0:
            add(FindingCode.FLAT_NOT_TP_CANCEL_PROOF, "FLAT beyanı her TP'nin iptal edildiğini veya cleanup çalıştığını kanıtlamaz.", [e])
        if e.event_type == EventType.STREAM_GAP:
            add(FindingCode.STREAM_GAP_REPORTED, "Akış boşluğu beyanı var; aradaki olaylar bilinmiyor.", [e], True)
        if e.event_type == EventType.DELETE_ACK:
            add(FindingCode.DELETE_ACK_REPORTED, "DELETE yanıtı beyanı var; çevrimdışı authenticity/istek yürütümü doğrulanmadı.", [e])
        if e.status == "CANCELED":
            add(FindingCode.CANCELED_REPORTED, "CANCELED durum beyanı var; bu tek başına neden veya aktör kanıtı değildir.", [e])
        if e.status in ("EXPIRED", "EXPIRED_IN_MATCH"):
            add(FindingCode.EXPIRY_CAUSE_UNRESOLVED, "EXPIRED borsa kaynaklı belirli bir nedeni kanıtlamaz.", [e])
        if e.status in ("CANCELED", "EXPIRED", "EXPIRED_IN_MATCH") and e.actor == "UNKNOWN":
            add(FindingCode.ACTOR_UNKNOWN, "İşlemi yapan aktör bilinmiyor.", [e])
        if e.actor != "UNKNOWN" or e.reason is not None:
            add(FindingCode.CLAIM_UNVERIFIED, "Aktör/neden etiketi kullanıcı beyanıdır; doğrulanmış neden değildir.", [e])
        if i and i.kind == "NORMAL" and e.event_type in (EventType.ORDER_STATE, EventType.DELETE_ACK):
            if e.orig_qty is None or e.executed_qty is None:
                add(FindingCode.QUANTITY_EVIDENCE_MISSING, "Miktar kanıtı eksik; partial/full fill çıkarılamaz.", [e])
            else:
                orig, filled = Decimal(e.orig_qty), Decimal(e.executed_qty)
                if (orig <= 0 or filled > orig or (e.status == "NEW" and filled != 0)
                    or (e.status == "PARTIALLY_FILLED" and not 0 < filled < orig)
                    or (e.status == "FILLED" and filled != orig)):
                    add(FindingCode.QUANTITY_CONFLICT, "Durum ve miktar beyanları çelişiyor.", [e], True)
                if orig > 0 and 0 < filled < orig:
                    add(FindingCode.PARTIAL_FILL_REPORTED, "Kısmi dolum miktarı beyanı; kalan TP veya pozisyon sonucu çıkarılmaz.", [e])
                if e.status == "FILLED" and orig > 0 and filled == orig:
                    add(FindingCode.FILL_REPORTED, "Bu kimliğin dolum beyanı; diğer TP'ler için sonuç değildir.", [e])

    for group in event_ids.values():
        if len(group) > 1:
            add(FindingCode.EVENT_REF_CONFLICT, "Aynı gün/köken olay referansı farklı içerikler taşıyor; biri seçilmedi.", group, True)
    for group in bindings.values():
        if len({e.order_key for e in group}) > 1:
            add(FindingCode.IDENTITY_SCOPE_CONFLICT, "Kimlik birden fazla scope/slot/role/lifecycle ile beyan edilmiş; birleştirilmedi.", group, True)
    for group in sequences.values():
        by_seq = defaultdict(list)
        for e in group:
            by_seq[e.sequence].append(e)
        if any(len(v) > 1 for v in by_seq.values()):
            add(FindingCode.SEQUENCE_CONFLICT, "Aynı sequence domain/sayı için farklı olaylar var.", group, True)
        numbers = sorted(by_seq)
        if any(b != a + 1 for a, b in zip(numbers, numbers[1:])):
            add(FindingCode.SEQUENCE_DISCONTINUITY, "Sağlanan parçada sıra atlaması var; gerçek stream/WAL kaybı olduğu çıkarılmaz.", group, True)

    terminal = {"CANCELED", "FILLED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED", "FINISHED"}
    for group in orders.values():
        states = [e for e in group if e.event_type in (EventType.ORDER_STATE, EventType.DELETE_ACK)]
        if not any(e.provenance.source == Source.SIGNED_RECEIPT for e in states):
            add(FindingCode.SIGNED_ORDER_EVIDENCE_MISSING, "Tam bu lifecycle/kimlik için signed durum beyanı yok.", group)
        server_conflict = len({e.identity.server for e in group if e.identity.server is not None}) > 1
        if server_conflict:
            add(FindingCode.SERVER_ID_CONFLICT, "Aynı client lifecycle birden fazla server kimliğine bağlanmış.", group, True)
        quantities = {Decimal(e.orig_qty) for e in states if e.orig_qty is not None}
        if len(quantities) > 1:
            add(FindingCode.QUANTITY_CONFLICT, "Aynı emir için başlangıç miktarı değişiyor.", states, True)
        # Her olay çifti için bulgu üretmek yerine zaman grupları ve watermark kullanılır.
        # Böylece 2000 olaylık çelişkili girdi karesel rapor/bellek büyütemez.
        by_time = defaultdict(list)
        for e in states:
            by_time[e.occurred_at].append(e)
        first_terminal, max_fill = None, None
        for batch in by_time.values():
            if len({e.status for e in batch}) > 1:
                add(FindingCode.STATE_CONFLICT, "Aynı olay anında farklı durumlar beyan edilmiş; kaynak önceliğiyle çözülmedi.", batch, True)
            if len({Decimal(e.executed_qty) for e in batch if e.executed_qty is not None}) > 1:
                add(FindingCode.QUANTITY_CONFLICT, "Aynı olay anında kümülatif miktar çelişkisi var.", batch, True)
            for e in batch:
                if first_terminal and e.status not in (first_terminal.status, "UNKNOWN"):
                    add(FindingCode.TERMINAL_STATE_CONFLICT, "Terminal durum sonrası farklı durum; gecikme/çelişki incelemesi gerekli.", [first_terminal, e], True)
                if max_fill and e.executed_qty is not None and Decimal(max_fill.executed_qty) > Decimal(e.executed_qty):
                    add(FindingCode.FILL_REGRESSION, "Kümülatif dolum miktarı zamanla azalmış görünüyor.", [max_fill, e], True)
            for e in batch:
                if first_terminal is None and e.status in terminal:
                    first_terminal = e
                if e.executed_qty is not None and (max_fill is None or Decimal(e.executed_qty) > Decimal(max_fill.executed_qty)):
                    max_fill = e
        intents = [e for e in group if e.event_type == EventType.CANCEL_INTENT]
        latest_ack = max((e.occurred_at for e in states if e.event_type == EventType.DELETE_ACK), default=None)
        for intent in intents:
            if server_conflict or latest_ack is None or latest_ack < intent.occurred_at:
                add(FindingCode.CANCEL_NOT_CONFIRMED, "WAL cancel niyeti başarılı DELETE değildir; eşleşen yanıt beyanı eksik.", [intent])
        if intents and not server_conflict:
            related = [e for e in states if e.status == "CANCELED" and e.occurred_at >= intents[0].occurred_at]
            if related:
                add(FindingCode.TEMPORAL_ASSOCIATION_ONLY, "Aynı lifecycle'da iptal niyeti ve sonraki CANCELED ilişkisi var; nedensellik/aktör doğrulanmaz.", [*intents, *related])

    aliases, rows = _Aliases(), []
    for e in events:
        p, s, i = e.provenance, e.scope, e.identity
        ns = (p.origin, s.account)
        rows.append(TimelineRow(
            refs[e], _time(e.occurred_at), _time(e.observed_at), p.origin, p.source,
            aliases.get("artifact", (p.origin, p.source, p.artifact_ref)), p.authenticity, p.coverage,
            aliases.get("account", ns), aliases.get("scope", (*ns, s.parent)),
            aliases.get("slot", (*ns, s.parent, s.slot)), aliases.get("lifecycle", (p.origin, s)),
            s.position_side, i.role if i else None, i.kind if i else None,
            aliases.get("client", (*ns, i.kind, i.client)) if i else None,
            aliases.get("server", (*ns, i.kind, i.server)) if i and i.server else None,
            aliases.get("passive-child", (*ns, i.actual_order_id)) if i and i.actual_order_id else None,
            e.event_type, e.status_field, e.status, e.query_code, e.orig_qty,
            e.executed_qty, e.position_qty, e.actor, e.reason))
    findings = sorted(set(findings), key=lambda f: (f.code.value, f.evidence_refs, f.detail))
    verdict = Verdict.REVIEW_REQUIRED if any(f.verdict == Verdict.REVIEW_REQUIRED for f in findings) else Verdict.UNRESOLVED
    return IncidentReport(day, verdict, tuple(findings), tuple(rows))


def analyze_document(raw: bytes) -> Report:
    events = parse_document(raw)
    incidents = tuple(_incident(day, tuple(e for e in events if e.incident_day == day)) for day in INCIDENT_DAYS)
    verdict = Verdict.REVIEW_REQUIRED if any(i.verdict == Verdict.REVIEW_REQUIRED for i in incidents) else Verdict.UNRESOLVED
    return Report(verdict, Verdict.UNRESOLVED, incidents, Authority())


def synthetic_document() -> bytes:
    """İki gün için JSON fixture; gerçek receipt/ID içermez, dışarıdan veri almaz."""
    events = []
    for day in INCIDENT_DAYS:
        base = {
            "event_ref": "r:state", "incident_day": day, "symbol": "USUSDT",
            "occurred_at": day + "T12:00:00+03:00", "observed_at": day + "T09:00:01Z",
            "scope": {"account": "r:account", "parent": "r:scope", "slot": "r:slot",
                      "generation": 1, "position_side": "BOTH"},
            "identity": {"role": "TP1", "kind": "NORMAL", "endpoint": "/fapi/v1/order",
                         "client_id": "r:tp", "order_id": "r:server", "client_algo_id": None,
                         "algo_id": None, "actual_order_id": None},
            "provenance": {"origin": "SYNTHETIC", "source": "SIGNED_RECEIPT",
                           "artifact_ref": "r:fixture", "authenticity": "NOT_VERIFIED_OFFLINE",
                           "coverage": "FRAGMENT"},
            "event_type": "ORDER_STATE", "status_field": "status", "status": "CANCELED",
            "query_code": None, "orig_qty": "10", "executed_qty": "3", "position_qty": None,
            "actor": "UNKNOWN", "reason": None, "sequence": None, "sequence_domain": None,
        }
        events.append(base)
    return json.dumps({"schema": SCHEMA, "redacted": True, "events": events}).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--help"]:
        print("Kullanım: python3 -B tp_incident_evidence_offline_g50.py [--demo|--help]\n"
              "Argümansız: yalnız redakte G50 JSON stdin. Çıktı: sıfır yetkili JSON rapor.\n"
              "Dosya/URL açılmaz; --demo yalnız sentetik iki gün fixture'ını analiz eder.")
        return 0
    try:
        if args not in ([], ["--demo"]):
            _fail("CLI_ARGUMENTS_INVALID")
        raw = synthetic_document() if args else sys.stdin.buffer.read(MAX_BYTES + 1)
        report = analyze_document(raw)
        print(report.to_json())
        return 0
    except EvidenceError as error:
        print(json.dumps({"error": str(error), "verdict": Verdict.REVIEW_REQUIRED,
                          "real_incident_cause": Verdict.UNRESOLVED,
                          "authority": asdict(Authority())}), file=sys.stderr)
        return 2
    except Exception:
        # Yol/ham veri/üçüncü taraf exception metni ve traceback stderr'e sızmaz.
        print('{"error":"OFFLINE_ANALYSIS_FAILED","verdict":"REVIEW_REQUIRED",'
              '"real_incident_cause":"UNRESOLVED","authority":'
              '{"entry_authorized":false,"owner_authorized":false,"unlock":false,"stop_proven":false}}',
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
