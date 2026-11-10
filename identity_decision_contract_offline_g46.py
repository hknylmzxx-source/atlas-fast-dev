"""G46 / V1: yalnız stdlib, OFFLINE kimlik ve kapalı karar sözleşmesi.

Bu modül model sonuçlarını taşır; owner/SDK/adapter veya yürütme API'si yoktur.
MAINNET/TESTNET yalnız ayrıştırılan namespace etiketleridir. Bütün kararlar
entry_authorized=False, unlock=False, stop_proven=False, NO_RETRY taşır.
ACKED callback cevabıdır; DURABLE yerel fixture etiketidir. Hiçbiri gerçek
STOP, güncel lease, borsa kabulü, risk rezervasyonu veya izin kanıtı değildir.

V1 bilerek dar: ENTRY=NORMAL, STOP/TP1/TP2=ALGO; ONE_WAY=BOTH,
HEDGE=LONG/SHORT. İsimler ASCII ve büyük/küçük harfe duyarlıdır; alias veya
normalizasyon yoktur. Server id pozitif kanonik ondalık metin ya da None'dır.
Server id sonradan eklenemez: kimliğin tamamı değişmezdir. Her wire alanı,
None/False olanlar dahil zorunludur. JSON yalnız UTF-8 bytes, sıralı anahtar,
kompakt ayraç ve tam kanonik byte eşitliğiyle kabul edilir.

OfflineDecisionConsumer tek işbirlikçi çağrı akışı için bellek içi fixture'dır.
Başarılı doğrulama yalnız kayıt kabulüdür. Nesil/kimlik tombstone'ları instance
ömründedir; yeni instance replay/restart güvenliği sağlamaz. Kalıcı fence,
fsync, iki süreç atomikliği, saat güncelliği veya güven kökü yoktur. Frozen
tipler normal Python kullanımı içindir; düşmanca süreç içi koda karşı sandbox
değildir. SHA-256 imza/HMAC değildir; yeniden hash'lenen sahte kayıtları
doğrulamaz. Gerçek importer ve G37–G45 bağlantısı özellikle bulunmaz.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
import re
from typing import Any


SCHEMA_VERSION = 1
MAX_INTEGER = 2**63 - 1
MAX_WIRE_BYTES = 32_768
IDENTITY_DOMAIN = b"ATLAS/G46/IDENTITY/V1\x00"
RECEIPT_DOMAIN = b"ATLAS/G46/DECISION/V1\x00"


class ContractError(ValueError):
    """Ham girdi taşımayan sabit ret kodu; ret hiçbir yetki üretmez."""


class Environment(Enum):
    OFFLINE = "OFFLINE"
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"


class PositionMode(Enum):
    ONE_WAY = "ONE_WAY"
    HEDGE = "HEDGE"


class PositionSide(Enum):
    BOTH = "BOTH"
    LONG = "LONG"
    SHORT = "SHORT"


class OrderRole(Enum):
    ENTRY = "ENTRY"
    STOP = "STOP"
    TP1 = "TP1"
    TP2 = "TP2"


class OrderKind(Enum):
    NORMAL = "NORMAL"
    ALGO = "ALGO"


class ModelStatus(Enum):
    NEW = "NEW"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    DEADLINE = "DEADLINE"
    REPLAY_ONLY = "REPLAY_ONLY"
    ACKED = "ACKED"
    DURABLE = "DURABLE"
    RESERVED = "RESERVED"
    MODEL_RETIRED = "MODEL_RETIRED"
    MANUAL_OWNER_REVIEW = "MANUAL_OWNER_REVIEW"


class EvidenceKind(Enum):
    MODEL_RESULT = "MODEL_RESULT"
    MOCK_OBSERVATION = "MOCK_OBSERVATION"
    CALLBACK_ACK = "CALLBACK_ACK"
    LOCAL_JOURNAL = "LOCAL_JOURNAL"


class Provenance(Enum):
    OFFLINE_MODEL = "OFFLINE_MODEL"
    OFFLINE_CALLBACK = "OFFLINE_CALLBACK"
    OFFLINE_LOCAL_JOURNAL = "OFFLINE_LOCAL_JOURNAL"


class DurabilityState(Enum):
    NOT_CONFIRMED = "NOT_CONFIRMED"
    CALLBACK_ACK_ONLY = "CALLBACK_ACK_ONLY"
    LOCAL_FSYNC_ACK_ONLY = "LOCAL_FSYNC_ACK_ONLY"


class DecisionCode(Enum):
    FAIL_CLOSED = "FAIL_CLOSED"


class RetryPolicy(Enum):
    NO_RETRY = "NO_RETRY"


CompositeKeyV1 = tuple[
    Environment, str, str, str, int, str, int, str, PositionMode, PositionSide,
    OrderRole, OrderKind, "OrderIdentityV1",
]


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ContractError(code)


def _version(value: object) -> None:
    _require(type(value) is int and value == SCHEMA_VERSION, "UNSUPPORTED_VERSION")


def _integer(value: object, *, positive: bool = True) -> None:
    _require(type(value) is int and (1 if positive else 0) <= value <= MAX_INTEGER,
             "INVALID_INTEGER")


def _name(value: object) -> None:
    _require(type(value) is str and
             re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is not None,
             "INVALID_NAME")


def _enum(value: object, enum_type: type[Enum]) -> None:
    _require(type(value) is enum_type, "INVALID_ENUM_TYPE")


def _digest(value: object) -> None:
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
             "INVALID_HASH")


def _false(value: object) -> None:
    _require(type(value) is bool and value is False, "UNSAFE_AUTHORITY")


def _exact_dict(value: object, cls: type) -> dict[str, Any]:
    _require(type(value) is dict, "OBJECT_REQUIRED")
    _require(all(type(k) is str for k in value), "INVALID_FIELD_NAME")
    _require(value.keys() == {f.name for f in fields(cls)}, "FIELD_SET_MISMATCH")
    return dict(value)


def _parse_enum(value: object, enum_type: type[Enum]) -> Any:
    _require(type(value) is str, "INVALID_ENUM")
    try:
        return enum_type(value)
    except ValueError:
        raise ContractError("UNKNOWN_ENUM") from None


def _wire_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _wire_value(getattr(value, f.name)) for f in fields(value)}
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "DUPLICATE_FIELD")
        result[key] = value
    return result


def _invalid_constant(_: str) -> None:
    raise ContractError("INVALID_JSON_CONSTANT")


def _load(data: bytes) -> dict[str, Any]:
    _require(type(data) is bytes and 0 < len(data) <= MAX_WIRE_BYTES,
             "INVALID_WIRE_BYTES")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_invalid_constant)
        _require(type(value) is dict, "OBJECT_REQUIRED")
        _require(_canonical(value) == data, "NONCANONICAL_SERIALIZATION")
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("INVALID_JSON") from None


@dataclass(frozen=True, slots=True)
class OrderIdentityV1:
    client_id: str | None
    client_algo_id: str | None
    server_order_id: str | None

    def __post_init__(self) -> None:
        _require((self.client_id is None) != (self.client_algo_id is None),
                 "CLIENT_NAMESPACE_REQUIRED")
        _name(self.client_id if self.client_id is not None else self.client_algo_id)
        if self.server_order_id is not None:
            _require(type(self.server_order_id) is str and re.fullmatch(
                r"[1-9][0-9]{0,19}", self.server_order_id) is not None,
                "INVALID_SERVER_ORDER_ID")

    @classmethod
    def from_dict(cls, value: object) -> OrderIdentityV1:
        return cls(**_exact_dict(value, cls))


@dataclass(frozen=True, slots=True)
class CanonicalIdentityV1:
    schema_version: int
    environment: Environment
    venue: str
    account_id: str
    parent_scope_id: str
    execution_epoch: int
    slot_id: str
    slot_generation: int
    symbol: str
    position_mode: PositionMode
    position_side: PositionSide
    order_role: OrderRole
    order_kind: OrderKind
    order_identity: OrderIdentityV1

    def __post_init__(self) -> None:
        _version(self.schema_version)
        for value, enum_type in ((self.environment, Environment),
                                 (self.position_mode, PositionMode),
                                 (self.position_side, PositionSide),
                                 (self.order_role, OrderRole),
                                 (self.order_kind, OrderKind)):
            _enum(value, enum_type)
        _require(type(self.venue) is str and re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,31}", self.venue) is not None, "INVALID_VENUE")
        for value in (self.account_id, self.parent_scope_id):
            _name(value)
        _require(type(self.slot_id) is str and self.slot_id in {"A", "B"},
                 "INVALID_SLOT")
        _integer(self.execution_epoch)
        _integer(self.slot_generation)
        _require(type(self.symbol) is str and re.fullmatch(
            r"[A-Z0-9]{2,24}", self.symbol) is not None, "INVALID_SYMBOL")
        _require((self.position_mode is PositionMode.ONE_WAY and
                  self.position_side is PositionSide.BOTH) or
                 (self.position_mode is PositionMode.HEDGE and
                  self.position_side in {PositionSide.LONG, PositionSide.SHORT}),
                 "POSITION_MODE_SIDE_MISMATCH")
        expected_kind = (OrderKind.NORMAL if self.order_role is OrderRole.ENTRY
                         else OrderKind.ALGO)
        _require(self.order_kind is expected_kind, "ORDER_ROLE_KIND_MISMATCH")
        _require(type(self.order_identity) is OrderIdentityV1, "INVALID_ORDER_IDENTITY")
        _require((self.order_kind is OrderKind.NORMAL and
                  self.order_identity.client_id is not None) or
                 (self.order_kind is OrderKind.ALGO and
                  self.order_identity.client_algo_id is not None),
                 "ORDER_CLIENT_NAMESPACE_MISMATCH")

    @property
    def composite_key(self) -> CompositeKeyV1:
        """G45'in sırası; lease/risk kimlikleri bu anahtara daraltılmaz."""
        return (self.environment, self.venue, self.account_id, self.parent_scope_id,
                self.execution_epoch, self.slot_id, self.slot_generation, self.symbol,
                self.position_mode, self.position_side, self.order_role, self.order_kind,
                self.order_identity)

    def to_dict(self) -> dict[str, Any]:
        return _wire_value(self)

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_dict())

    @property
    def identity_hash(self) -> str:
        return hashlib.sha256(IDENTITY_DOMAIN + self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> CanonicalIdentityV1:
        data = _exact_dict(value, cls)
        for name, enum_type in (("environment", Environment),
                                ("position_mode", PositionMode),
                                ("position_side", PositionSide),
                                ("order_role", OrderRole), ("order_kind", OrderKind)):
            data[name] = _parse_enum(data[name], enum_type)
        data["order_identity"] = OrderIdentityV1.from_dict(data["order_identity"])
        return cls(**data)

    @classmethod
    def from_bytes(cls, data: bytes) -> CanonicalIdentityV1:
        return cls.from_dict(_load(data))


@dataclass(frozen=True, slots=True)
class LeaseV1:
    lease_epoch: int
    lease_generation: int
    lease_owner: str

    def __post_init__(self) -> None:
        _integer(self.lease_epoch)
        _integer(self.lease_generation)
        _name(self.lease_owner)

    @classmethod
    def from_dict(cls, value: object) -> LeaseV1:
        return cls(**_exact_dict(value, cls))


@dataclass(frozen=True, slots=True)
class DecisionEnvelopeV1:
    schema_version: int
    event_id: str
    parent_event_id: str | None
    identity: CanonicalIdentityV1
    lease: LeaseV1
    risk_reservation_id: str
    intent_hash: str
    policy_hash: str
    recorded_at_monotonic_source: str
    recorded_at_monotonic_ns: int
    recorded_at_utc_observed: str
    evidence_kind: EvidenceKind
    status: ModelStatus
    provenance: Provenance
    wal_sequence: int
    durability_state: DurabilityState
    decision: DecisionCode = DecisionCode.FAIL_CLOSED
    retry_policy: RetryPolicy = RetryPolicy.NO_RETRY
    entry_authorized: bool = False
    unlock: bool = False
    stop_proven: bool = False
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _version(self.schema_version)
        _name(self.event_id)
        if self.parent_event_id is not None:
            _name(self.parent_event_id)
            _require(self.parent_event_id != self.event_id, "SELF_PARENT_EVENT")
        _require(type(self.identity) is CanonicalIdentityV1, "INVALID_IDENTITY_TYPE")
        _require(type(self.lease) is LeaseV1, "INVALID_LEASE_TYPE")
        _name(self.risk_reservation_id)
        _digest(self.intent_hash)
        _digest(self.policy_hash)
        _name(self.recorded_at_monotonic_source)
        _integer(self.recorded_at_monotonic_ns, positive=False)
        _integer(self.wal_sequence)
        _require(type(self.recorded_at_utc_observed) is str, "INVALID_UTC")
        try:
            dt = datetime.strptime(self.recorded_at_utc_observed, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            raise ContractError("INVALID_UTC") from None
        _require(dt.strftime("%Y-%m-%dT%H:%M:%SZ") == self.recorded_at_utc_observed,
                 "INVALID_UTC")
        for value, enum_type in ((self.evidence_kind, EvidenceKind),
                                 (self.status, ModelStatus),
                                 (self.provenance, Provenance),
                                 (self.durability_state, DurabilityState),
                                 (self.decision, DecisionCode),
                                 (self.retry_policy, RetryPolicy)):
            _enum(value, enum_type)
        for value in (self.entry_authorized, self.unlock, self.stop_proven):
            _false(value)
        source_contract = {
            EvidenceKind.MODEL_RESULT: (Provenance.OFFLINE_MODEL,
                                       DurabilityState.NOT_CONFIRMED),
            EvidenceKind.MOCK_OBSERVATION: (Provenance.OFFLINE_MODEL,
                                           DurabilityState.NOT_CONFIRMED),
            EvidenceKind.CALLBACK_ACK: (Provenance.OFFLINE_CALLBACK,
                                       DurabilityState.CALLBACK_ACK_ONLY),
            EvidenceKind.LOCAL_JOURNAL: (Provenance.OFFLINE_LOCAL_JOURNAL,
                                        DurabilityState.LOCAL_FSYNC_ACK_ONLY),
        }
        _require((self.provenance, self.durability_state) ==
                 source_contract[self.evidence_kind], "EVIDENCE_PROVENANCE_MISMATCH")
        required_evidence = {
            ModelStatus.ACKED: EvidenceKind.CALLBACK_ACK,
            ModelStatus.DURABLE: EvidenceKind.LOCAL_JOURNAL,
            ModelStatus.RESERVED: EvidenceKind.MODEL_RESULT,
            ModelStatus.MODEL_RETIRED: EvidenceKind.MODEL_RESULT,
            ModelStatus.MANUAL_OWNER_REVIEW: EvidenceKind.MODEL_RESULT,
            ModelStatus.NEW: EvidenceKind.MOCK_OBSERVATION,
            ModelStatus.PARTIAL: EvidenceKind.MOCK_OBSERVATION,
            ModelStatus.FILLED: EvidenceKind.MOCK_OBSERVATION,
            ModelStatus.MISSING: EvidenceKind.MOCK_OBSERVATION,
        }
        _require(self.status not in required_evidence or
                 self.evidence_kind is required_evidence[self.status],
                 "STATUS_EVIDENCE_MISMATCH")
        object.__setattr__(self, "receipt_hash", hashlib.sha256(
            RECEIPT_DOMAIN + _canonical(self._payload())).hexdigest())

    def __bool__(self) -> bool:
        raise TypeError("DECISION_HAS_NO_TRUTHINESS")

    def _payload(self) -> dict[str, Any]:
        return {f.name: _wire_value(getattr(self, f.name))
                for f in fields(self) if f.name != "receipt_hash"}

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "receipt_hash": self.receipt_hash}

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> DecisionEnvelopeV1:
        data = _exact_dict(value, cls)
        receipt = data.pop("receipt_hash")
        _digest(receipt)
        data["identity"] = CanonicalIdentityV1.from_dict(data["identity"])
        data["lease"] = LeaseV1.from_dict(data["lease"])
        for name, enum_type in (("evidence_kind", EvidenceKind), ("status", ModelStatus),
                                ("provenance", Provenance),
                                ("durability_state", DurabilityState),
                                ("decision", DecisionCode), ("retry_policy", RetryPolicy)):
            data[name] = _parse_enum(data[name], enum_type)
        result = cls(**data)
        _require(result.receipt_hash == receipt, "RECEIPT_HASH_MISMATCH")
        return result

    @classmethod
    def from_bytes(cls, data: bytes) -> DecisionEnvelopeV1:
        return cls.from_dict(_load(data))


class OfflineDecisionConsumer:
    """Yalnız kanonik bytes tüketir; ret sırasında bellek kaydı değişmez.

    Execution epoch parent kapsamındadır. Slot generation epoch değişse de
    geri alınamaz; yeni epoch aynı slotta daha yüksek generation ister.
    Lease (epoch, generation) ayrı parent watermark'ıdır; aynı token'da owner
    değişemez. Account position_mode bu instance ömründe değişemez.
    Her identity ilk olayda parent=None, sonra kendi son event'ine bağlanır.
    WAL sequence parent genelinde, monotonic ns identity içinde artar.
    Risk id account genelinde tek exact identity+intent+policy'ye bağlanır.
    Başarı/ret gerçek emir, kilit veya STOP durumunu değiştirmez.
    """

    def __init__(self) -> None:
        self._events: dict[str, str] = {}
        self._epochs: dict[tuple[Any, ...], int] = {}
        self._slots: dict[tuple[Any, ...], CanonicalIdentityV1] = {}
        self._leases: dict[tuple[Any, ...], LeaseV1] = {}
        self._modes: dict[tuple[Any, ...], PositionMode] = {}
        self._orders: dict[tuple[Any, ...], str] = {}
        self._roles: dict[tuple[Any, ...], str] = {}
        self._risks: dict[tuple[Any, ...], tuple[str, str, str]] = {}
        self._last: dict[str, DecisionEnvelopeV1] = {}
        self._sequences: dict[tuple[Any, ...], int] = {}

    def consume(self, data: bytes) -> DecisionEnvelopeV1:
        envelope = DecisionEnvelopeV1.from_bytes(data)
        identity = envelope.identity
        identity_hash = identity.identity_hash
        previous_event = self._events.get(envelope.event_id)
        if previous_event is not None:
            raise ContractError("DUPLICATE_EVENT" if previous_event == envelope.receipt_hash
                                else "EVENT_ID_COLLISION")

        account = (identity.environment, identity.venue, identity.account_id)
        scope = (*account, identity.parent_scope_id)
        slot = (*scope, identity.slot_id)
        lifecycle = (*scope, identity.execution_epoch, identity.slot_id,
                     identity.slot_generation)
        _require(identity.execution_epoch >= self._epochs.get(scope, 0),
                 "STALE_EXECUTION_EPOCH")
        prior_slot = self._slots.get(slot)
        if prior_slot is not None:
            _require(identity.slot_generation >= prior_slot.slot_generation,
                     "STALE_SLOT_GENERATION")
            if identity.slot_generation == prior_slot.slot_generation:
                _require(identity.execution_epoch == prior_slot.execution_epoch,
                         "SLOT_ABA")
                _require((identity.symbol, identity.position_mode, identity.position_side) ==
                         (prior_slot.symbol, prior_slot.position_mode, prior_slot.position_side),
                         "SLOT_IDENTITY_COLLISION")
        _require(account not in self._modes or
                 self._modes[account] is identity.position_mode, "ACCOUNT_MODE_CONFLICT")

        prior_lease = self._leases.get(scope)
        if prior_lease is not None:
            token = (envelope.lease.lease_epoch, envelope.lease.lease_generation)
            prior_token = (prior_lease.lease_epoch, prior_lease.lease_generation)
            _require(token >= prior_token, "STALE_LEASE")
            _require(token != prior_token or envelope.lease.lease_owner ==
                     prior_lease.lease_owner, "LEASE_OWNER_COLLISION")

        order = identity.order_identity
        client = order.client_id if order.client_id is not None else order.client_algo_id
        order_keys = [(*account, identity.order_kind, "CLIENT", client)]
        if order.server_order_id is not None:
            order_keys.append((*account, identity.order_kind, "SERVER", order.server_order_id))
        for key in order_keys:
            _require(key not in self._orders or self._orders[key] == identity_hash,
                     "ORDER_IDENTITY_COLLISION")
        role_key = (*lifecycle, identity.order_role)
        _require(role_key not in self._roles or self._roles[role_key] == identity_hash,
                 "ROLE_IDENTITY_COLLISION")

        risk_key = (*account, envelope.risk_reservation_id)
        risk_binding = (identity_hash, envelope.intent_hash, envelope.policy_hash)
        _require(risk_key not in self._risks or self._risks[risk_key] == risk_binding,
                 "RISK_RESERVATION_COLLISION")
        previous = self._last.get(identity_hash)
        if previous is None:
            _require(envelope.parent_event_id is None, "UNKNOWN_PARENT_EVENT")
        else:
            _require((envelope.risk_reservation_id, envelope.intent_hash, envelope.policy_hash) ==
                     (previous.risk_reservation_id, previous.intent_hash, previous.policy_hash),
                     "IMMUTABLE_INTENT_COLLISION")
            _require(envelope.parent_event_id == previous.event_id, "CAUSAL_CHAIN_MISMATCH")
            _require(envelope.recorded_at_monotonic_source ==
                     previous.recorded_at_monotonic_source, "CLOCK_SOURCE_MISMATCH")
            _require(envelope.recorded_at_monotonic_ns >
                     previous.recorded_at_monotonic_ns, "STALE_OBSERVATION")
        _require(envelope.wal_sequence > self._sequences.get(scope, 0), "STALE_WAL_SEQUENCE")

        # Yalnız bütün kontrollerden sonra fixture belleğine kaydet. Kalıcı/atomik
        # storage veya eşzamanlı caller garantisi bu satırlardan çıkarılamaz.
        self._events[envelope.event_id] = envelope.receipt_hash
        self._epochs[scope] = identity.execution_epoch
        self._slots[slot] = identity
        self._leases[scope] = envelope.lease
        self._modes[account] = identity.position_mode
        for key in order_keys:
            self._orders[key] = identity_hash
        self._roles[role_key] = identity_hash
        self._risks[risk_key] = risk_binding
        self._last[identity_hash] = envelope
        self._sequences[scope] = envelope.wal_sequence
        return envelope
