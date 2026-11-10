"""G48: bağımsız, yalnız stdlib kullanan OFFLINE uyumluluk protokolü.

owner-v1: ENTRY/TP1/TP2 NORMAL, STOP ALGO; mevcut owner gibi ONE_WAY/BOTH.
conditional-algo-v2: ENTRY NORMAL, STOP/TP1/TP2 ALGO; yalnız hipotetik model.
İkinci profil owner'ın desteklediği veya Binance'de doğrulanmış bir sürüm
değildir. Endpoint'ler taşınan etiketlerdir; istek üretme/gönderme API'si yoktur.
Emir tipi/fiyat/miktar/risk/closePosition/reduceOnly intent'i modellenmez.

G46 import edilmez, değiştirilmez veya otomatik dönüştürülmez. Her parser ve
tüketici dışarıdan açık expected_profile ister. Eksik profil, alias, fallback,
kind/endpoint'ten profil tahmini ve sessiz downgrade yoktur. G48 kimliği G46
CanonicalIdentityV1 değildir; protokol, sürüm ve profil hash/anahtara dahildir.

NORMAL newClientOrderId + order_id; ALGO clientAlgoId + algo_id kullanır.
ALGO actual_order_id yalnız pasif çocuk referansıdır; NORMAL order_id yerine
geçmez, eşleşme/ownership/STOP kanıtı üretmez. Server kimlikleri None olabilir;
sonradan eklemek yeni kimliktir ve aynı client'a yeniden bağlanamaz.

MAINNET/TESTNET pasif namespace etiketleridir. SHA-256 imza veya güven kökü
değildir. Her mock sonuç FAIL_CLOSED/NO_RETRY ve bütün yetkiler False kalır.
Tüketici tek işbirlikçi akışta bellek içi fixture'dır: kalıcılık, thread/process
atomikliği, gerçek generation otoritesi, zaman güncelliği veya restart koruması
yoktur. Yeni instance replay hafızasını kaybeder. Tombstone'lar silinmez.
Frozen nesneler düşmanca süreç içi Python koduna karşı güvenlik sınırı değildir.
Gerçek owner/SDK/STOP/guard entegrasyonu ve canlı yetki kesinlikle yoktur.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
import hashlib
import json
import re
from typing import Any


PROTOCOL = "ATLAS_G48_IDENTITY_KIND_COMPAT"
SCHEMA_VERSION = 1
MAX_INTEGER = 2**63 - 1
MAX_WIRE_BYTES = 16_384
IDENTITY_DOMAIN = b"ATLAS/G48/KIND_COMPAT/IDENTITY/V1\x00"
RECEIPT_DOMAIN = b"ATLAS/G48/KIND_COMPAT/MOCK_RECEIPT/V1\x00"


class CompatError(ValueError):
    """Ham girdiyi içermeyen sabit ret kodu."""


class Profile(Enum):
    OWNER_V1 = "owner-v1"
    CONDITIONAL_ALGO_V2 = "conditional-algo-v2"


class Environment(Enum):
    OFFLINE = "OFFLINE"
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"


class Role(Enum):
    ENTRY = "ENTRY"
    STOP = "STOP"
    TP1 = "TP1"
    TP2 = "TP2"


class Kind(Enum):
    NORMAL = "NORMAL"
    ALGO = "ALGO"


class ClientNamespace(Enum):
    NORMAL = "newClientOrderId"
    ALGO = "clientAlgoId"


class PositionMode(Enum):
    ONE_WAY = "ONE_WAY"
    HEDGE = "HEDGE"


class PositionSide(Enum):
    BOTH = "BOTH"
    LONG = "LONG"
    SHORT = "SHORT"


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class MockStatus(Enum):
    ACKED = "ACKED"
    OBSERVED = "OBSERVED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    MODEL_RETIRED = "MODEL_RETIRED"


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise CompatError(code)


def _enum(value: object, enum_type: type[Enum]) -> None:
    _require(type(value) is enum_type, "INVALID_ENUM_TYPE")


def _parse_enum(value: object, enum_type: type[Enum]) -> Any:
    _require(type(value) is str, "INVALID_ENUM")
    try:
        return enum_type(value)
    except ValueError:
        raise CompatError("UNKNOWN_ENUM") from None


def _integer(value: object) -> None:
    _require(type(value) is int and 1 <= value <= MAX_INTEGER, "INVALID_INTEGER")


def _text(value: object, pattern: str, code: str) -> None:
    _require(type(value) is str and re.fullmatch(pattern, value) is not None, code)


def _name(value: object) -> None:
    _text(value, r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", "INVALID_NAME")


def _server_id(value: object) -> None:
    if value is not None:
        _text(value, r"[1-9][0-9]{0,19}", "INVALID_SERVER_ID")


def _header(protocol: object, version: object) -> None:
    _require(type(protocol) is str and protocol == PROTOCOL, "PROTOCOL_MISMATCH")
    _require(type(version) is int and version == SCHEMA_VERSION, "UNSUPPORTED_VERSION")


def _exact_dict(value: object, cls: type) -> dict[str, Any]:
    _require(type(value) is dict, "OBJECT_REQUIRED")
    _require(all(type(k) is str for k in value), "INVALID_FIELD_NAME")
    _require(value.keys() == {f.name for f in fields(cls)}, "FIELD_SET_MISMATCH")
    return dict(value)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        _require(key not in value, "DUPLICATE_FIELD")
        value[key] = item
    return value


def _invalid_constant(_: str) -> None:
    raise CompatError("INVALID_JSON_CONSTANT")


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
        if isinstance(exc, CompatError):
            raise
        raise CompatError("INVALID_JSON") from None


def _expect(actual: Profile, expected: Profile) -> None:
    _enum(expected, Profile)
    _require(actual is expected, "EXPECTED_PROFILE_CONFLICT")


def _kind_for(profile: Profile, role: Role) -> Kind:
    # Profil ve role doğrulanmadan bu özel yardımcı çağrılmaz.
    if role is Role.ENTRY:
        return Kind.NORMAL
    if profile is Profile.OWNER_V1 and role in (Role.TP1, Role.TP2):
        return Kind.NORMAL
    return Kind.ALGO


@dataclass(frozen=True, slots=True)
class OrderReference:
    client_namespace: ClientNamespace
    client_id: str | None
    client_algo_id: str | None
    order_id: str | None
    algo_id: str | None
    actual_order_id: str | None

    def __post_init__(self) -> None:
        _enum(self.client_namespace, ClientNamespace)
        normal = self.client_namespace is ClientNamespace.NORMAL
        _require((self.client_id is not None) == normal and
                 (self.client_algo_id is not None) != normal,
                 "AMBIGUOUS_CLIENT_NAMESPACE")
        # Bilerek dar ASCII altkümesi; trim/alias/otomatik alan taşıması yoktur.
        _text(self.client_id if normal else self.client_algo_id,
              r"[A-Za-z0-9][A-Za-z0-9_-]{0,35}", "INVALID_CLIENT_ID")
        for item in (self.order_id, self.algo_id, self.actual_order_id):
            _server_id(item)
        if normal:
            _require(self.algo_id is None and self.actual_order_id is None,
                     "ORDER_ID_NAMESPACE_MISMATCH")
        else:
            _require(self.order_id is None, "ORDER_ID_NAMESPACE_MISMATCH")
            _require(self.actual_order_id is None or self.algo_id is not None,
                     "UNBOUND_ACTUAL_ORDER_ID")

    @property
    def client_text(self) -> str:
        return self.client_id if self.client_id is not None else self.client_algo_id

    @property
    def server_text(self) -> str | None:
        return self.order_id if self.client_namespace is ClientNamespace.NORMAL else self.algo_id

    def to_dict(self) -> dict[str, Any]:
        return {f.name: (getattr(self, f.name).value if f.name == "client_namespace"
                         else getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, value: object) -> OrderReference:
        data = _exact_dict(value, cls)
        data["client_namespace"] = _parse_enum(data["client_namespace"], ClientNamespace)
        return cls(**data)


@dataclass(frozen=True, slots=True)
class CompatIdentityV1:
    protocol: str
    schema_version: int
    profile: Profile
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
    entry_side: Side
    order_side: Side
    order_role: Role
    order_kind: Kind
    endpoint: str
    order_identity: OrderReference

    def __post_init__(self) -> None:
        _header(self.protocol, self.schema_version)
        for value, enum_type in ((self.profile, Profile), (self.environment, Environment),
                                 (self.position_mode, PositionMode),
                                 (self.position_side, PositionSide), (self.entry_side, Side),
                                 (self.order_side, Side), (self.order_role, Role),
                                 (self.order_kind, Kind)):
            _enum(value, enum_type)
        _require(type(self.venue) is str and self.venue == "BINANCE_USDM", "INVALID_VENUE")
        _name(self.account_id)
        _name(self.parent_scope_id)
        _integer(self.execution_epoch)
        _integer(self.slot_generation)
        _require(type(self.slot_id) is str and self.slot_id in ("A", "B"), "INVALID_SLOT")
        _text(self.symbol, r"[A-Z0-9]{2,32}", "INVALID_SYMBOL")
        if self.position_mode is PositionMode.ONE_WAY:
            _require(self.position_side is PositionSide.BOTH, "POSITION_MODE_SIDE_MISMATCH")
        else:
            _require(self.position_side in (PositionSide.LONG, PositionSide.SHORT),
                     "POSITION_MODE_SIDE_MISMATCH")
            expected_entry = Side.BUY if self.position_side is PositionSide.LONG else Side.SELL
            _require(self.entry_side is expected_entry, "HEDGE_ENTRY_SIDE_MISMATCH")
        _require(self.profile is not Profile.OWNER_V1 or
                 self.position_mode is PositionMode.ONE_WAY, "OWNER_V1_REQUIRES_ONE_WAY")
        opposite = Side.SELL if self.entry_side is Side.BUY else Side.BUY
        _require(self.order_side is (self.entry_side if self.order_role is Role.ENTRY else opposite),
                 "ORDER_SIDE_MISMATCH")
        _require(self.order_kind is _kind_for(self.profile, self.order_role),
                 "ROLE_KIND_PROFILE_MISMATCH")
        normal = self.order_kind is Kind.NORMAL
        endpoint = "/fapi/v1/order" if normal else "/fapi/v1/algoOrder"
        _require(type(self.endpoint) is str and self.endpoint == endpoint,
                 "ENDPOINT_KIND_MISMATCH")
        _require(type(self.order_identity) is OrderReference, "INVALID_ORDER_REFERENCE_TYPE")
        namespace = ClientNamespace.NORMAL if normal else ClientNamespace.ALGO
        _require(self.order_identity.client_namespace is namespace, "CLIENT_KIND_MISMATCH")

    @property
    def composite_key(self) -> tuple:
        """G48'e özgü tam anahtar; profil/endpoint/yön bilgisi atılamaz."""
        return tuple(getattr(self, f.name) for f in fields(self))

    def to_dict(self) -> dict[str, Any]:
        value = {}
        for f in fields(self):
            item = getattr(self, f.name)
            value[f.name] = (item.value if isinstance(item, Enum) else
                             item.to_dict() if type(item) is OrderReference else item)
        return value

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_dict())

    @property
    def identity_hash(self) -> str:
        return hashlib.sha256(IDENTITY_DOMAIN + self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, value: object, *, expected_profile: Profile) -> CompatIdentityV1:
        _enum(expected_profile, Profile)
        data = _exact_dict(value, cls)
        for name, enum_type in (("profile", Profile), ("environment", Environment),
                                ("position_mode", PositionMode), ("position_side", PositionSide),
                                ("entry_side", Side), ("order_side", Side),
                                ("order_role", Role), ("order_kind", Kind)):
            data[name] = _parse_enum(data[name], enum_type)
        _expect(data["profile"], expected_profile)
        data["order_identity"] = OrderReference.from_dict(data["order_identity"])
        return cls(**data)

    @classmethod
    def from_bytes(cls, data: bytes, *, expected_profile: Profile) -> CompatIdentityV1:
        return cls.from_dict(_load(data), expected_profile=expected_profile)


@dataclass(frozen=True, slots=True)
class MockReceiptV1:
    protocol: str
    schema_version: int
    event_id: str
    parent_event_id: str | None
    sequence: int
    identity: CompatIdentityV1
    status: MockStatus
    provenance: str = "OFFLINE_MOCK"
    decision: str = "FAIL_CLOSED"
    retry_policy: str = "NO_RETRY"
    entry_authorized: bool = False
    owner_authorized: bool = False
    unlock: bool = False
    stop_proven: bool = False
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _header(self.protocol, self.schema_version)
        _name(self.event_id)
        if self.parent_event_id is not None:
            _name(self.parent_event_id)
            _require(self.parent_event_id != self.event_id, "SELF_PARENT_EVENT")
        _integer(self.sequence)
        _require(type(self.identity) is CompatIdentityV1, "INVALID_IDENTITY_TYPE")
        _enum(self.status, MockStatus)
        for value, expected in ((self.provenance, "OFFLINE_MOCK"),
                                (self.decision, "FAIL_CLOSED"), (self.retry_policy, "NO_RETRY")):
            _require(type(value) is str and value == expected, "UNSAFE_DECISION_OR_SOURCE")
        for value in (self.entry_authorized, self.owner_authorized, self.unlock, self.stop_proven):
            _require(type(value) is bool and value is False, "UNSAFE_AUTHORITY")
        object.__setattr__(self, "receipt_hash",
                           hashlib.sha256(RECEIPT_DOMAIN + _canonical(self._payload())).hexdigest())

    def __bool__(self) -> bool:
        raise TypeError("MOCK_RECEIPT_IS_NOT_AUTHORIZATION")

    def _payload(self) -> dict[str, Any]:
        value = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "receipt_hash"}
        value["identity"] = self.identity.to_dict()
        value["status"] = self.status.value
        return value

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload(), receipt_hash=self.receipt_hash)

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_dict())

    @classmethod
    def from_dict(cls, value: object, *, expected_profile: Profile) -> MockReceiptV1:
        data = _exact_dict(value, cls)
        supplied_hash = data.pop("receipt_hash")
        _text(supplied_hash, r"[0-9a-f]{64}", "INVALID_RECEIPT_HASH")
        data["identity"] = CompatIdentityV1.from_dict(data["identity"],
                                                    expected_profile=expected_profile)
        data["status"] = _parse_enum(data["status"], MockStatus)
        result = cls(**data)
        _require(supplied_hash == result.receipt_hash, "RECEIPT_HASH_MISMATCH")
        return result

    @classmethod
    def from_bytes(cls, data: bytes, *, expected_profile: Profile) -> MockReceiptV1:
        return cls.from_dict(_load(data), expected_profile=expected_profile)


class OfflineCompatConsumer:
    """Kayıt kabulü yetki değildir; tek instance/tek çağrı akışı fixture'ı."""

    def __init__(self, *, expected_profile: Profile) -> None:
        _enum(expected_profile, Profile)
        self._expected_profile = expected_profile
        self._events: dict[str, str] = {}
        self._modes: dict[tuple, PositionMode] = {}
        self._epochs: dict[tuple, int] = {}
        self._slots: dict[tuple, tuple] = {}
        self._clients: dict[tuple, tuple] = {}
        self._servers: dict[tuple, tuple] = {}
        self._roles: dict[tuple, tuple] = {}
        self._sequences: dict[tuple, int] = {}
        self._last_events: dict[tuple, str] = {}

    @property
    def expected_profile(self) -> Profile:
        return self._expected_profile

    def consume(self, data: bytes) -> MockReceiptV1:
        result = MockReceiptV1.from_bytes(data, expected_profile=self._expected_profile)
        ident = result.identity
        key = ident.composite_key
        previous_hash = self._events.get(result.event_id)
        if previous_hash is not None:
            raise CompatError("DUPLICATE_EVENT" if previous_hash == result.receipt_hash
                              else "EVENT_ID_COLLISION")
        account = (ident.environment, ident.venue, ident.account_id)
        parent = (*account, ident.parent_scope_id)
        slot = (*parent, ident.slot_id)
        _require(self._modes.get(account, ident.position_mode) is ident.position_mode,
                 "ACCOUNT_POSITION_MODE_CONFLICT")
        _require(ident.execution_epoch >= self._epochs.get(parent, ident.execution_epoch),
                 "STALE_EXECUTION_EPOCH")
        binding = (ident.symbol, ident.position_mode, ident.position_side, ident.entry_side)
        old_slot = self._slots.get(slot)
        if old_slot is not None:
            old_epoch, old_generation, old_binding = old_slot
            _require(ident.execution_epoch >= old_epoch, "STALE_EXECUTION_EPOCH")
            _require(ident.slot_generation >= old_generation, "STALE_SLOT_GENERATION")
            if ident.execution_epoch > old_epoch:
                _require(ident.slot_generation > old_generation, "REUSED_RESTART_GENERATION")
            if ident.slot_generation == old_generation:
                _require(binding == old_binding, "SLOT_BINDING_CONFLICT")
        role_key = (*slot, ident.execution_epoch, ident.slot_generation, ident.order_role)
        _require(self._roles.get(role_key, key) == key, "ROLE_IDENTITY_COLLISION")
        # Rol/slot/parent/generation namespace'i daraltamaz; tombstone kalıcıdır
        # (yalnız bu instance içinde). NORMAL ile ALGO ise ayrı namespace'tir.
        order_namespace = (*account, ident.order_kind)
        client_key = (*order_namespace, ident.order_identity.client_text)
        _require(self._clients.get(client_key, key) == key, "CLIENT_ID_COLLISION")
        server_text = ident.order_identity.server_text
        server_key = (*order_namespace, server_text)
        if server_text is not None:
            _require(self._servers.get(server_key, key) == key, "SERVER_ID_COLLISION")
        _require(result.sequence > self._sequences.get(parent, 0), "REPLAYED_SEQUENCE")
        _require(result.parent_event_id == self._last_events.get(key), "CAUSAL_PARENT_MISMATCH")

        # Retlerin hiçbiri event/nesil/namespace/sequence state'ini ilerletmez.
        self._events[result.event_id] = result.receipt_hash
        self._modes[account] = ident.position_mode
        self._epochs[parent] = ident.execution_epoch
        self._slots[slot] = (ident.execution_epoch, ident.slot_generation, binding)
        self._roles[role_key] = key
        self._clients[client_key] = key
        if server_text is not None:
            self._servers[server_key] = key
        self._sequences[parent] = result.sequence
        self._last_events[key] = result.event_id
        return result
