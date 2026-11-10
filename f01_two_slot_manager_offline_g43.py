"""G43 / F01: çalışan, yalnız bellek içi iki-slot mock modeli.

Standart kütüphane dışında import, ağ/SDK, dosya, emir veya kilit açma API'si
yoktur. RESERVED yalnız sentetik intent/bütçe kaydıdır; işlem kabulü değildir.
Her karar ve rapor entry_authorized=False, unlock=False, NO_RETRY taşır.

İşbirliği yapan bütün istemciler AYNI MockSlotStore'u kullanır. Lease kontrolü,
slot/kimlik/bütçe kontrolü ve intent ekleme aynı RLock altında yapılır; saat
de açık sentetik girdidir. Ayrı Python süreçlerinin bellekleri ortak değildir:
süreçler aşağıda aynı store'a bağlanan ayrı facade'larla MODELLENİR. Gerçek
süreçler arası fencing, kalıcılık veya crash recovery sağlanmaz. mock_restart
elde kalan otoriteyi tek yönlü MANUAL_REVIEW yapar; yeni boş store oluşturmak
gerçek recovery değildir. Python nesne kimliği kriptografik kanıt değildir.

Gözlemler yalnız açık MockObservation girdileridir, borsa kanıtı değildir.
Entry, STOP, TP1 ve TP2 ayrı izlenir. STOP/TP alarmı birbirini kapatamaz.
Tüm risk/marjin rezervasyonu kısmi dolumda ve belirsizlikte korunur. Yalnız
tüm kayıtlı emirler terminal, güncel, kesin ve mock net miktar sıfırken model
slotu emekliye ayrılabilir; tarihsel slot/client/order kimlikleri tekrar açılamaz.

Örnek: python3 -B f01_two_slot_manager_offline_g43.py
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from decimal import Context, Decimal, ROUND_CEILING, localcontext
from functools import wraps
import json
import re
from threading import RLock


ROLES = frozenset(("ENTRY", "STOP", "TP1", "TP2"))
TERMINAL = frozenset(("FILLED", "CANCELED", "EXPIRED", "REJECTED"))
STATUSES = TERMINAL | {"NEW", "PARTIAL", "MISSING", "UNKNOWN"}
ZERO = Decimal("0")


class ModelInputError(ValueError):
    """Sabit hata kodu; ham girdi veya credentials içermez."""


def _name(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None


def _integer(value, *, positive=False):
    return type(value) is int and (1 if positive else 0) <= value <= 2**63 - 1


def _amount(value, *, positive=False):
    if (type(value) is not str
            or re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,8})?", value) is None):
        raise ModelInputError("INVALID_DECIMAL_TEXT")
    number = Decimal(value)
    if positive and number <= 0:
        raise ModelInputError("POSITIVE_AMOUNT_REQUIRED")
    return number


def _exact(method):
    """Çağıranın Decimal precision/trap ayarları mock bütçeyi değiştiremez."""
    @wraps(method)
    def wrapped(*args, **kwargs):
        with localcontext(Context(prec=64)):
            return method(*args, **kwargs)
    return wrapped


@dataclass(frozen=True, slots=True)
class ModelOnly:
    entry_authorized: bool = field(default=False, init=False)
    unlock: bool = field(default=False, init=False)
    retry_policy: str = field(default="NO_RETRY", init=False)
    mode: str = field(default="OFFLINE_MOCK_ONLY", init=False)


@dataclass(frozen=True, slots=True)
class Binding:
    scope: str
    account: str
    symbol: str
    position_side: str

    def __post_init__(self):
        if (not _name(self.scope) or not _name(self.account)
                or type(self.symbol) is not str
                or re.fullmatch(r"[A-Z0-9]{2,24}", self.symbol) is None
                or type(self.position_side) is not str
                or self.position_side not in {"LONG", "SHORT", "BOTH"}):
            raise ModelInputError("INVALID_BINDING")


@dataclass(frozen=True, slots=True)
class MockFunds:
    account: str
    available_margin: str

    def __post_init__(self):
        if not _name(self.account):
            raise ModelInputError("INVALID_ACCOUNT")
        _amount(self.available_margin)


@dataclass(frozen=True, slots=True)
class MockLimits:
    global_risk: str
    slot_risk: str
    global_margin: str
    slot_margin: str
    global_leverage: int
    slot_leverage: int
    funds: tuple[MockFunds, ...]
    max_observation_age_ms: int = 1_000

    def __post_init__(self):
        for value in (self.global_risk, self.slot_risk, self.global_margin, self.slot_margin):
            _amount(value, positive=True)
        if (not _integer(self.global_leverage, positive=True)
                or not _integer(self.slot_leverage, positive=True)
                or not _integer(self.max_observation_age_ms, positive=True)
                or type(self.funds) is not tuple or not self.funds
                or any(type(f) is not MockFunds for f in self.funds)
                or len({f.account for f in self.funds}) != len(self.funds)):
            raise ModelInputError("INVALID_LIMITS")


@dataclass(frozen=True, slots=True)
class SlotRequest:
    slot_id: str
    binding: Binding
    quantity: str
    price: str
    risk: str
    leverage: int

    def __post_init__(self):
        if not _name(self.slot_id) or type(self.binding) is not Binding:
            raise ModelInputError("INVALID_SLOT_REQUEST")
        for value in (self.quantity, self.price, self.risk):
            _amount(value, positive=True)
        if not _integer(self.leverage, positive=True):
            raise ModelInputError("INVALID_LEVERAGE")

    @property
    def notional(self):
        with localcontext(Context(prec=64)):
            return _amount(self.quantity) * _amount(self.price)

    @property
    def margin(self):
        with localcontext(Context(prec=64)):
            return (self.notional / self.leverage).quantize(
                Decimal("0.00000001"), rounding=ROUND_CEILING)


@dataclass(frozen=True, slots=True)
class OrderIntent:
    binding: Binding
    role: str
    client_id: str
    order_id: str
    order_kind: str
    quantity: str
    reduce_only: bool

    def __post_init__(self):
        if (type(self.binding) is not Binding or type(self.role) is not str
                or self.role not in ROLES or not _name(self.client_id)
                or not _name(self.order_id) or type(self.order_kind) is not str
                or self.order_kind not in {"NORMAL", "ALGO"}
                or type(self.reduce_only) is not bool
                or self.reduce_only != (self.role != "ENTRY")):
            raise ModelInputError("INVALID_ORDER_INTENT")
        _amount(self.quantity, positive=True)


@dataclass(frozen=True, slots=True)
class MockObservation:
    intent: OrderIntent
    status: str
    cumulative_filled: str
    observed_at_ms: int
    sequence: int


@dataclass(frozen=True, slots=True, eq=False)
class MockLease:
    slot_id: str
    binding: Binding
    owner: str
    epoch: int
    generation: int
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class IntentRecord:
    intent: OrderIntent
    reserved_at_ms: int
    last_observation: MockObservation | None = None
    known_observation: MockObservation | None = None
    uncertain: bool = False

    @property
    def filled(self):
        return ZERO if self.known_observation is None else _amount(
            self.known_observation.cumulative_filled)


@dataclass(frozen=True, slots=True)
class SlotRecord:
    request: SlotRequest
    intents: tuple[IntentRecord, ...]
    active: bool = True

    def find(self, role):
        return next((r for r in self.intents if r.intent.role == role), None)

    @property
    @_exact
    def exposure(self):
        return self.find("ENTRY").filled - sum(
            (r.filled for r in self.intents if r.intent.role != "ENTRY"), ZERO)


@dataclass(frozen=True, slots=True)
class Decision(ModelOnly):
    status: str
    reason: str
    slot_id: str = ""
    lease: MockLease | None = None


@dataclass(frozen=True, slots=True)
class Alarm:
    slot_id: str
    origin: str
    code: str


@dataclass(frozen=True, slots=True)
class SlotView(ModelOnly):
    record: SlotRecord
    exposure: str
    stop_state: str
    tp_states: tuple[tuple[str, str], ...]
    manual_review: bool


@dataclass(frozen=True, slots=True)
class Report(ModelOnly):
    status: str
    reason: str
    active_slots: int
    reserved_risk: str
    reserved_margin: str
    gross_notional: str
    account_reserved_margin: tuple[tuple[str, str], ...]
    slots: tuple[SlotView, ...]
    alarms: tuple[Alarm, ...]
    epoch: int
    restarted: bool


class MockSlotStore:
    """Tek güvenilir bellek otoritesi; yalnız yerel model mutex'i kullanır.

    Limits/funds sabittir. Bütçeler brüt toplanır; scope, hesap, sembol veya
    LONG/SHORT netleştirmesi yoktur. Client id hesap genelinde; order id ise
    (hesap, NORMAL/ALGO) ad alanında tekildir. Emekli kayıtlar da saklanır.
    """

    def __init__(self, limits):
        if type(limits) is not MockLimits:
            raise ModelInputError("MOCK_LIMITS_REQUIRED")
        self._limits = limits
        self._mutex = RLock()
        self._slots = {}
        self._leases = {}
        self._generation = {}
        self._last_now_ms = 0
        self._epoch = 1
        self._restarted = False


class TwoSlotManagerOffline:
    """Aynı store'a bağlı facade'lar bağımsız mock süreç/istemci rolleridir."""

    def __init__(self, store):
        if type(store) is not MockSlotStore:
            raise ModelInputError("SHARED_MOCK_STORE_REQUIRED")
        self._store = store

    def _clock(self, now_ms):
        if not _integer(now_ms):
            return "CLOCK_INVALID"
        if now_ms < self._store._last_now_ms:
            return "CLOCK_REGRESSION"
        self._store._last_now_ms = now_ms
        return None

    def _gate(self, now_ms):
        error = self._clock(now_ms)
        if error:
            return error
        if self._store._restarted:
            return "RESTART_MANUAL_REVIEW"
        return None

    def _lease_error(self, lease, slot_id, binding, now_ms):
        if (type(lease) is not MockLease
                or self._store._leases.get(slot_id) is not lease
                or lease.slot_id != slot_id or lease.binding != binding
                or lease.epoch != self._store._epoch
                or lease.generation != self._store._generation.get(slot_id)):
            return "LEASE_UNTRUSTED_OR_STALE"
        if now_ms >= lease.expires_at_ms:
            return "LEASE_EXPIRED"
        return None

    def mock_issue_lease(self, slot_id, binding, *, owner, now_ms, ttl_ms):
        """Mock otoritenin nesil rotasyonu: önceki token anında geçersizleşir."""
        with self._store._mutex:
            error = self._gate(now_ms)
            if error:
                return Decision("DENY", error)
            if (not _name(slot_id) or type(binding) is not Binding or not _name(owner)
                    or not _integer(ttl_ms, positive=True)
                    or not _integer(now_ms + ttl_ms)):
                return Decision("DENY", "INVALID_LEASE_INPUT")
            slot = self._store._slots.get(slot_id)
            old = self._store._leases.get(slot_id)
            if ((slot is not None and slot.request.binding != binding)
                    or (old is not None and old.binding != binding)):
                return Decision("DENY", "SLOT_BINDING_CONFLICT", slot_id)
            if slot is not None and not slot.active:
                return Decision("DENY", "RETIRED_SLOT_NO_RETRY", slot_id)
            generation = self._store._generation.get(slot_id, 0) + 1
            lease = MockLease(slot_id, binding, owner, self._store._epoch,
                              generation, now_ms + ttl_ms)
            self._store._generation[slot_id] = generation
            self._store._leases[slot_id] = lease
            return Decision("MOCK_LEASE", "MODEL_ONLY", slot_id, lease)

    def _identity_error(self, intent):
        for slot in self._store._slots.values():
            for record in slot.intents:
                other = record.intent
                if other.binding.account != intent.binding.account:
                    continue
                if other.client_id == intent.client_id:
                    return "CLIENT_ID_REPLAY_OR_CONFLICT"
                if other.order_kind == intent.order_kind and other.order_id == intent.order_id:
                    return "ORDER_ID_REPLAY_OR_CONFLICT"
        return None

    def _budget_error(self, request):
        limits = self._store._limits
        active = [s for s in self._store._slots.values() if s.active]
        if len(active) >= 2:
            return "THIRD_ACTIVE_SLOT"
        if any(s.request.binding.account == request.binding.account
               and s.request.binding.symbol == request.binding.symbol
               and (s.request.binding.position_side == request.binding.position_side
                    or "BOTH" in {s.request.binding.position_side, request.binding.position_side})
               for s in active):
            return "EXPOSURE_BINDING_ALREADY_ACTIVE"
        if _amount(request.risk) > _amount(limits.slot_risk):
            return "SLOT_RISK_LIMIT"
        if request.margin > _amount(limits.slot_margin):
            return "SLOT_MARGIN_LIMIT"
        if request.leverage > limits.slot_leverage:
            return "SLOT_LEVERAGE_LIMIT"
        if request.leverage > limits.global_leverage:
            return "GLOBAL_LEVERAGE_LIMIT"
        if sum((_amount(s.request.risk) for s in active), ZERO) + _amount(
                request.risk) > _amount(limits.global_risk):
            return "GLOBAL_RISK_LIMIT"
        if sum((s.request.margin for s in active), ZERO) + request.margin > _amount(
                limits.global_margin):
            return "GLOBAL_MARGIN_LIMIT"
        available = next((f for f in limits.funds if f.account == request.binding.account), None)
        if available is None:
            return "ACCOUNT_FUNDS_UNKNOWN"
        account_reserved = sum((s.request.margin for s in active
                                if s.request.binding.account == request.binding.account), ZERO)
        if account_reserved + request.margin > _amount(available.available_margin):
            return "ACCOUNT_FUNDS_EXHAUSTED"
        return None

    @_exact
    def reserve_slot(self, request, entry_intent, lease, *, now_ms):
        """Tek atomik mock rezervasyon; dış callback veya gönderim içermez."""
        with self._store._mutex:
            error = self._gate(now_ms)
            if error:
                return Decision("DENY", error)
            if type(request) is not SlotRequest or type(entry_intent) is not OrderIntent:
                return Decision("DENY", "INVALID_RESERVATION_INPUT")
            if (entry_intent.role != "ENTRY" or entry_intent.binding != request.binding
                    or _amount(entry_intent.quantity) != _amount(request.quantity)):
                return Decision("DENY", "ENTRY_BINDING_OR_QUANTITY_MISMATCH", request.slot_id)
            error = self._lease_error(lease, request.slot_id, request.binding, now_ms)
            if error:
                return Decision("DENY", error, request.slot_id)
            existing = self._store._slots.get(request.slot_id)
            if existing is not None:
                exact = (existing.request == request
                         and existing.find("ENTRY").intent == entry_intent)
                return Decision("DENY", "DUPLICATE_SLOT_NO_RETRY" if exact
                                else "SLOT_REPLAY_CONFLICT", request.slot_id)
            error = self._identity_error(entry_intent) or self._budget_error(request)
            if error:
                return Decision("DENY", error, request.slot_id)
            self._store._slots[request.slot_id] = SlotRecord(
                request, (IntentRecord(entry_intent, now_ms),))
            return Decision("RESERVED", "SYNTHETIC_ENTRY_INTENT_ONLY", request.slot_id)

    @_exact
    def reserve_protective_intent(self, slot_id, intent, lease, *, now_ms):
        with self._store._mutex:
            error = self._gate(now_ms)
            if error:
                return Decision("DENY", error)
            if not _name(slot_id) or type(intent) is not OrderIntent or intent.role == "ENTRY":
                return Decision("DENY", "INVALID_PROTECTIVE_INTENT")
            slot = self._store._slots.get(slot_id)
            if slot is None or not slot.active:
                return Decision("DENY", "ACTIVE_SLOT_NOT_FOUND", slot_id)
            if intent.binding != slot.request.binding:
                return Decision("DENY", "BINDING_MISMATCH", slot_id)
            error = self._lease_error(lease, slot_id, intent.binding, now_ms)
            if error:
                return Decision("DENY", error, slot_id)
            existing = slot.find(intent.role)
            if existing is not None:
                return Decision("DENY", "DUPLICATE_INTENT_NO_RETRY" if existing.intent == intent
                                else "ROLE_INTENT_CONFLICT_NO_RETRY", slot_id)
            if any(r.uncertain for r in slot.intents):
                return Decision("DENY", "SLOT_UNCERTAIN_NO_RETRY", slot_id)
            quantity = _amount(intent.quantity)
            if intent.role == "STOP":
                valid_quantity = quantity == _amount(slot.request.quantity)
            else:
                tp_total = sum((_amount(r.intent.quantity) for r in slot.intents
                                if r.intent.role.startswith("TP")), ZERO)
                valid_quantity = tp_total + quantity <= _amount(slot.request.quantity)
            if not valid_quantity:
                return Decision("DENY", "PROTECTIVE_QUANTITY_LIMIT", slot_id)
            error = self._identity_error(intent)
            if error:
                return Decision("DENY", error, slot_id)
            self._store._slots[slot_id] = replace(
                slot, intents=slot.intents + (IntentRecord(intent, now_ms),))
            return Decision("RESERVED", "SYNTHETIC_PROTECTIVE_INTENT_ONLY", slot_id)

    def _observation_error(self, record, observation, now_ms):
        if (type(observation.status) is not str or observation.status not in STATUSES
                or not _integer(observation.observed_at_ms)
                or not _integer(observation.sequence, positive=True)):
            return "INVALID_OBSERVATION"
        try:
            filled = _amount(observation.cumulative_filled)
        except ModelInputError:
            return "INVALID_OBSERVATION"
        if observation.observed_at_ms > now_ms:
            return "FUTURE_OBSERVATION"
        if now_ms - observation.observed_at_ms > self._store._limits.max_observation_age_ms:
            return "STALE_OBSERVATION"
        if observation.observed_at_ms < record.reserved_at_ms:
            return "PRE_INTENT_OBSERVATION"
        previous = record.last_observation
        if previous is not None:
            if observation == previous:
                return "DUPLICATE_OBSERVATION_NO_RETRY"
            if (observation.sequence <= previous.sequence
                    or observation.observed_at_ms < previous.observed_at_ms):
                return "OUT_OF_ORDER_OR_CONFLICTING_OBSERVATION"
        total = _amount(record.intent.quantity)
        if filled < record.filled or filled > total:
            return "FILL_REGRESSION_OR_OVERFILL"
        status = observation.status
        if ((status == "NEW" and filled != 0)
                or (status == "PARTIAL" and not ZERO < filled < total)
                or (status == "FILLED" and filled != total)
                or (status == "REJECTED" and filled != 0)):
            return "STATUS_FILL_CONFLICT"
        if status in {"UNKNOWN", "MISSING"} and filled != record.filled:
            return "UNCERTAIN_FILL_NOT_PROOF"
        known = record.known_observation
        if known is not None and status not in {"UNKNOWN", "MISSING"}:
            if known.status in TERMINAL and (status != known.status or filled != record.filled):
                return "TERMINAL_STATE_CONFLICT"
            if known.status == "PARTIAL" and status == "NEW":
                return "STATUS_REGRESSION"
        return None

    @_exact
    def observe(self, slot_id, observation, *, now_ms):
        """Mock okuma kaydı; lease/intent üretmez, otomatik retry yapmaz."""
        with self._store._mutex:
            error = self._clock(now_ms)
            if error:
                return Decision("DENY", error)
            if (not _name(slot_id) or type(observation) is not MockObservation
                    or type(observation.intent) is not OrderIntent):
                return Decision("DENY", "MOCK_OBSERVATION_REQUIRED")
            slot = self._store._slots.get(slot_id)
            if slot is None or not slot.active:
                return Decision("DENY", "ACTIVE_SLOT_NOT_FOUND", slot_id)
            record = slot.find(observation.intent.role)
            if record is None or record.intent != observation.intent:
                return Decision("DENY", "EXACT_INTENT_BINDING_REQUIRED", slot_id)
            error = self._observation_error(record, observation, now_ms)
            if error:
                return Decision("DENY", error, slot_id)
            uncertain = observation.status in {"UNKNOWN", "MISSING"}
            updated = replace(record, last_observation=observation,
                              known_observation=record.known_observation if uncertain else observation,
                              uncertain=record.uncertain or uncertain)
            updated_slot = replace(slot, intents=tuple(
                updated if r is record else r for r in slot.intents))
            if updated_slot.exposure < 0:
                return Decision("DENY", "EXIT_FILLS_EXCEED_KNOWN_ENTRY", slot_id)
            self._store._slots[slot_id] = updated_slot
            if uncertain or record.uncertain or self._store._restarted:
                return Decision("MANUAL_REVIEW", "UNKNOWN_OR_MISSING_NO_RETRY", slot_id)
            return Decision("OBSERVED", "SYNTHETIC_OBSERVATION_ONLY", slot_id)

    @_exact
    def mock_retire_slot(self, slot_id, lease, *, now_ms):
        """Yalnız kesin mock kapanışta bütçe bookkeeping'i; gerçek unlock yok."""
        with self._store._mutex:
            error = self._gate(now_ms)
            if error:
                return Decision("DENY", error)
            if not _name(slot_id):
                return Decision("DENY", "INVALID_SLOT_ID")
            slot = self._store._slots.get(slot_id)
            if slot is None or not slot.active:
                return Decision("DENY", "ACTIVE_SLOT_NOT_FOUND", slot_id)
            error = self._lease_error(lease, slot_id, slot.request.binding, now_ms)
            if error:
                return Decision("DENY", error, slot_id)
            if any(r.uncertain or r.known_observation is None
                   or r.known_observation.status not in TERMINAL
                   or now_ms - r.known_observation.observed_at_ms >
                   self._store._limits.max_observation_age_ms for r in slot.intents):
                return Decision("DENY", "ORDERS_NOT_FRESH_TERMINAL_NO_RETRY", slot_id)
            if slot.exposure != 0:
                return Decision("DENY", "EXPOSURE_REMAINS", slot_id)
            self._store._slots[slot_id] = replace(slot, active=False)
            return Decision("MODEL_RETIRED", "MOCK_BUDGET_RELEASE_ONLY", slot_id)

    def mock_restart(self, *, now_ms):
        """Kalan bellek kaydı korunur; tüm facade'lar tek yönlü kapatılır."""
        with self._store._mutex:
            error = self._clock(now_ms)
            # Saat hatası restart kapısını açık bırakamaz.
            if not self._store._restarted:
                self._store._epoch += 1
            self._store._restarted = True
            self._store._leases.clear()
            return Decision("MANUAL_REVIEW", error or "RESTART_MANUAL_REVIEW")

    def _fresh(self, record, now_ms):
        observation = record.last_observation
        return observation is not None and (
            0 <= now_ms - observation.observed_at_ms <= self._store._limits.max_observation_age_ms)

    def _view(self, slot, now_ms, clock_error):
        slot_id = slot.request.slot_id
        alarms = []
        exposure = slot.exposure
        stop = slot.find("STOP")
        entry = slot.find("ENTRY")
        entry_may_fill = (entry.uncertain or entry.known_observation is None
                          or entry.known_observation.status not in TERMINAL)
        possible_exposure = exposure > 0 or entry_may_fill
        if not slot.active:
            stop_state = "MODEL_RETIRED"
        elif clock_error and possible_exposure:
            stop_state = "STOP_CLOCK_UNTRUSTED_DANGER"
        elif stop is not None and stop.uncertain:
            stop_state = "STOP_UNKNOWN_OR_MISSING_DANGER"
        elif not possible_exposure:
            stop_state = "NO_KNOWN_EXPOSURE"
        elif stop is None or stop.last_observation is None:
            stop_state = "STOP_MISSING_DANGER"
        elif not self._fresh(stop, now_ms):
            stop_state = "STOP_STALE_DANGER"
        elif stop.last_observation.status not in {"NEW", "PARTIAL"}:
            stop_state = "STOP_INACTIVE_DANGER"
        elif _amount(stop.intent.quantity) - stop.filled < exposure:
            stop_state = "STOP_UNDERCOVERED_DANGER"
        else:
            stop_state = "MOCK_STOP_OBSERVED"
        if stop_state.endswith("DANGER"):
            alarms.append(Alarm(slot_id, "STOP", stop_state))
        if slot.active and (entry.uncertain or not self._fresh(entry, now_ms)):
            alarms.append(Alarm(slot_id, "ENTRY", "ENTRY_UNKNOWN_OR_STALE_NO_RETRY"))
        tp_states = []
        for role in ("TP1", "TP2"):
            record = slot.find(role)
            state = "NO_INTENT"
            if record is not None:
                if record.uncertain:
                    state = "UNKNOWN_NO_RETRY"
                elif not self._fresh(record, now_ms):
                    state = "UNOBSERVED_OR_STALE_NO_RETRY"
                else:
                    state = record.last_observation.status
                if slot.active and state not in {"NEW", "FILLED"}:
                    alarms.append(Alarm(slot_id, role, "TP_" + state))
            tp_states.append((role, state))
        manual = self._store._restarted or bool(alarms) or any(r.uncertain for r in slot.intents)
        return SlotView(slot, str(exposure), stop_state, tuple(tp_states), manual), alarms

    @_exact
    def report(self, *, now_ms):
        with self._store._mutex:
            error = self._clock(now_ms)
            effective_now = self._store._last_now_ms
            views, alarms = [], []
            for slot in self._store._slots.values():
                view, slot_alarms = self._view(slot, effective_now, error)
                views.append(view)
                alarms.extend(slot_alarms)
            if error or self._store._restarted:
                alarms.append(Alarm("", "MODEL", error or "RESTART_MANUAL_REVIEW"))
            active = [s for s in self._store._slots.values() if s.active]
            risk = sum((_amount(s.request.risk) for s in active), ZERO)
            margin = sum((s.request.margin for s in active), ZERO)
            notional = sum((s.request.notional for s in active), ZERO)
            accounts = tuple((f.account, str(sum(
                (s.request.margin for s in active if s.request.binding.account == f.account), ZERO)))
                for f in self._store._limits.funds)
            danger = any(a.origin == "STOP" for a in alarms)
            manual = any(v.manual_review for v in views)
            status = ("MANUAL_REVIEW" if self._store._restarted or error else
                      "DANGER" if danger else "MANUAL_REVIEW" if alarms or manual else "MODEL_ONLY")
            return Report(status, error or ("RESTART_MANUAL_REVIEW" if self._store._restarted
                                           else "SYNTHETIC_FACTS_ONLY"),
                          len(active), str(risk), str(margin), str(notional), accounts,
                          tuple(views), tuple(alarms), self._store._epoch, self._store._restarted)


def demo():
    limits = MockLimits("20", "10", "100", "60", 5, 5,
                        (MockFunds("MOCK_ACCOUNT", "100"),))
    manager = TwoSlotManagerOffline(MockSlotStore(limits))
    decisions = []
    for index in range(1, 4):
        binding = Binding("MOCK_SCOPE", "MOCK_ACCOUNT", f"MOCK{index}USDT", "LONG")
        request = SlotRequest(f"slot-{index}", binding, "1", "100", "5", 5)
        intent = OrderIntent(binding, "ENTRY", f"mock-client-{index}",
                             f"mock-order-{index}", "NORMAL", "1", False)
        lease = manager.mock_issue_lease(request.slot_id, binding, owner="mock-process",
                                         now_ms=100, ttl_ms=1_000).lease
        decisions.append(manager.reserve_slot(request, intent, lease, now_ms=100))
    return {**asdict(ModelOnly()), "decisions": [asdict(d) for d in decisions],
            "report": asdict(manager.report(now_ms=100))}


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, sort_keys=True, indent=2))
