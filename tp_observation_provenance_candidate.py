"""Saf offline TP provenance adayı; yalnız önceden verilen mock kayıtlarını okur.

``signed-read`` burada sadece senaryonun adıdır. İmza, credentials, SDK veya
Binance doğrulaması yoktur. Token, tek kaynak örneğinin bellekte ürettiği
değişmez makbuzun kimliğini bağlar; kriptografik kanıt/güvenlik sınırı değildir.
Çağıran fixture'ları kendisi seçebilir. Python iç durumuna müdahale, eşzamanlı
kullanım ve süreçler arası kalıcı replay koruması kapsam dışıdır.

Owner/ENTRY/STOP/TP akışına ve WAL'a bağlı değildir. Hiçbir sonuç emir, retry,
STOP değişikliği veya kilit açma yetkisi vermez; modülde I/O yolu bulunmaz.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


class MockProvenanceBlocked(ValueError):
    """Mock kayıt veya inceleme bağlamı geçersiz."""


_FIELDS = (
    "scope", "symbol", "order_id", "client_order_id", "role",
    "observed_at_ms", "status", "quantity", "executed_qty",
)
_IDENTITY_FIELDS = _FIELDS[:5]
_STOP_STATES = frozenset(("OPEN", "MISSING", "FILLED", "CANCELED",
                          "UNKNOWN", "AMBIGUOUS"))


def _text(value):
    return type(value) is str and bool(value) and value.strip() == value


def _timestamp(value):
    return type(value) is int and value >= 0


def _identity_valid(row):
    return (all(_text(row.get(key)) for key in
                ("scope", "symbol", "client_order_id", "role"))
            and row["role"] in ("TP1", "TP2")
            and type(row.get("order_id")) is int and row["order_id"] > 0)


def _amount(value):
    if not _text(value):
        raise MockProvenanceBlocked("TP_AMOUNT_INVALID")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise MockProvenanceBlocked("TP_AMOUNT_INVALID") from None
    if not number.is_finite() or number < 0:
        raise MockProvenanceBlocked("TP_AMOUNT_INVALID")
    return number


def _snapshot(row):
    if type(row) is not dict or set(row) != set(_FIELDS):
        raise MockProvenanceBlocked("OBSERVATION_SHAPE_INVALID")
    if not _identity_valid(row):
        raise MockProvenanceBlocked("OBSERVATION_IDENTITY_INVALID")
    if not _timestamp(row["observed_at_ms"]):
        raise MockProvenanceBlocked("OBSERVATION_TIMESTAMP_INVALID")
    quantity, filled = _amount(row["quantity"]), _amount(row["executed_qty"])
    status = row["status"]
    if type(status) is not str or quantity <= 0 or not (
        (status == "NEW" and filled == 0)
        or (status == "PARTIALLY_FILLED" and 0 < filled < quantity)
        or (status == "FILLED" and filled == quantity)
    ):
        raise MockProvenanceBlocked("TP_FILL_STATE_INVALID")
    return tuple(row[key] for key in _FIELDS)


@dataclass(frozen=True)
class MockReadReceipt:
    """Yalnız kayıtlı nesne kimliği kabul edilir; taklit boolean kabul edilmez."""

    request_id: str
    received_at_ms: int
    observation: tuple
    authority_token: object = field(repr=False)


class MockSignedReadSource:
    """Önceden yüklenen dict fixture'ları; callback/transport/API kabul etmez.

    Her request_id bir kez okunur. Fixture kopyalanır, her makbuz ayrı object
    token alır. Başarılı incelemede token bu kaynakta tüketilir; aynı kaynağı
    paylaşan ikinci inceleyici de tüketilmiş token'ı kabul edemez.
    """

    def __init__(self, fixtures):
        if (type(fixtures) is not dict or not fixtures
                or any(not _text(key) for key in fixtures)):
            raise MockProvenanceBlocked("MOCK_FIXTURES_INVALID")
        self._fixtures = {key: _snapshot(row) for key, row in fixtures.items()}
        self._issued = {}
        self._read_ids = set()
        self._consumed = set()

    def read(self, *, request_id, received_at_ms):
        """Yalnız bellekteki fixture okunur; saat çağırandan gelen mock saattir."""
        if not _text(request_id) or not _timestamp(received_at_ms):
            raise MockProvenanceBlocked("MOCK_READ_CONTEXT_INVALID")
        if request_id not in self._fixtures:
            raise MockProvenanceBlocked("MOCK_READ_NOT_FOUND")
        if request_id in self._read_ids:
            raise MockProvenanceBlocked("MOCK_REQUEST_ID_REUSED")
        receipt = MockReadReceipt(request_id, received_at_ms,
                                  self._fixtures[request_id], object())
        self._issued[receipt.authority_token] = receipt
        self._read_ids.add(request_id)
        return receipt

    def _require_unconsumed(self, receipt):
        if (type(receipt) is not MockReadReceipt
                or type(receipt.authority_token) is not object
                or self._issued.get(receipt.authority_token) is not receipt):
            raise MockProvenanceBlocked("MOCK_SOURCE_RECEIPT_NOT_BOUND")
        if receipt.authority_token in self._consumed:
            raise MockProvenanceBlocked("MOCK_RECEIPT_REPLAYED")


def _result(accepted, reason=None, safety_observations=()):
    return {
        "decision": ("MOCK_TP_OBSERVATION_MATCH_ONLY" if accepted
                     else "MOCK_TP_OBSERVATION_REJECTED"),
        "mock_only": True,
        "mock_source_receipt_bound": accepted,
        "reasons": [] if accepted else [reason],
        "safety_observations": list(safety_observations),
        "authenticated_signed_get_verified": False,
        "real_exchange_data_verified": False,
        "entry_authorized": False,
        "may_clear_latch": False,
        "may_release_owner_lock": False,
        "may_submit_order": False,
        "may_modify_stop": False,
        "may_retry_wal": False,
    }


class TpObservationProvenanceCandidate:
    """Tek TP kimliği için sıralı, yalnız bellekte çalışan inceleyici.

    Open-order listeleri ve STOP etiketi doğrulanmış kanıt değildir. Bunlar
    yalnız tanısal çıktıya taşınır; boş listeler veya terminal STOP etiketi de
    hiçbir kilidi açmaz. Ret, token'ı tüketmez ve kabul geçmişini ilerletmez.
    Sıra/ilerleme geçmişi bu inceleyiciye aittir; yeni inceleyici bu geçmişi
    devralmaz. Tüketilmiş token geçmişi ise kaynak örneğinde paylaşılır.
    """

    def __init__(self, *, source, scope, symbol, order_id, client_order_id, role):
        identity = dict(zip(_IDENTITY_FIELDS,
                            (scope, symbol, order_id, client_order_id, role)))
        if type(source) is not MockSignedReadSource or not _identity_valid(identity):
            raise MockProvenanceBlocked("MOCK_REVIEW_CONTEXT_INVALID")
        self._source = source
        self._identity = identity
        self._previous = None

    def inspect(self, observation, *, receipt, now_ms, normal_orders,
                conditional_orders, stop_state, max_age_ms=5000):
        if (not _timestamp(now_ms) or type(max_age_ms) is not int
                or not 0 < max_age_ms <= 10000):
            return _result(False, "MOCK_REVIEW_CLOCK_INVALID")
        if (type(normal_orders) is not list or type(conditional_orders) is not list
                or any(type(row) is not dict
                       for row in normal_orders + conditional_orders)
                or type(stop_state) is not str or stop_state not in _STOP_STATES):
            return _result(False, "SAFETY_CONTEXT_INVALID")
        safety = ["STOP_STATE_" + stop_state]
        if normal_orders:
            safety.append("NORMAL_OPEN_ORDERS_PRESENT")
        if conditional_orders:
            safety.append("CONDITIONAL_OPEN_ORDERS_PRESENT")
        try:
            snapshot = _snapshot(observation)
            for key, expected in self._identity.items():
                if observation[key] != expected:
                    raise MockProvenanceBlocked(key.upper() + "_MISMATCH")
            self._source._require_unconsumed(receipt)
            if snapshot != receipt.observation:
                raise MockProvenanceBlocked("MOCK_RECEIPT_OBSERVATION_CONFLICT")
            observed = observation["observed_at_ms"]
            if (not 0 <= now_ms - observed <= max_age_ms
                    or not 0 <= now_ms - receipt.received_at_ms <= max_age_ms
                    or receipt.received_at_ms < observed):
                raise MockProvenanceBlocked("OBSERVATION_STALE_FUTURE_OR_READ_TIME_INVALID")
            self._require_progress(observation)
        except MockProvenanceBlocked as exc:
            return _result(False, str(exc), safety)
        self._source._consumed.add(receipt.authority_token)
        self._previous = snapshot
        return _result(True, safety_observations=safety)

    def _require_progress(self, observation):
        if self._previous is None:
            return
        previous = dict(zip(_FIELDS, self._previous))
        timestamp = observation["observed_at_ms"]
        if timestamp < previous["observed_at_ms"]:
            raise MockProvenanceBlocked("OBSERVATION_REPLAYED_OR_OUT_OF_ORDER")
        if timestamp == previous["observed_at_ms"]:
            if observation == previous:
                raise MockProvenanceBlocked("DUPLICATE_OBSERVATION")
            raise MockProvenanceBlocked("CONFLICTING_OBSERVATION")
        if (_amount(observation["quantity"]) != _amount(previous["quantity"])
                or previous["status"] == "FILLED"
                or _amount(observation["executed_qty"]) < _amount(previous["executed_qty"])):
            raise MockProvenanceBlocked("CONFLICTING_OBSERVATION")
        if _amount(observation["executed_qty"]) == _amount(previous["executed_qty"]):
            raise MockProvenanceBlocked("DUPLICATE_OBSERVATION_NO_FILL_PROGRESS")
