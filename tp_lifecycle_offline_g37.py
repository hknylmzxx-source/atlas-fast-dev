"""G37: bağımsız, yalnız sentetik TP telemetrisi ve yerel alarm kuyruğu.

SDK, ağ, emir yürütme, STOP veya owner kilidi bağlantısı yoktur. Mock makbuz
kimliği gerçek imza/provenance kanıtı değildir. SHA256 zinciri de imza değildir;
disk sahibi saldırgana karşı WORM veya harici bir bütünlük kökü sağlamaz.

Çalıştırma: python3 -B tp_lifecycle_offline_g37.py
Kalıcı sentetik günlük: --journal-directory <yeni-offline-dizin>
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from decimal import Decimal, InvalidOperation
import fcntl
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
from tempfile import TemporaryDirectory
import time
import uuid


class LifecycleError(ValueError):
    """Sabit, hassas girdi içermeyen ret kodu."""


class JournalError(LifecycleError):
    """Günlük belirsizliği: bu izleyici artık yazamaz."""


ROLES = frozenset(("TP1", "TP2"))
STATUSES = frozenset(("NEW", "PARTIAL", "FILLED", "EXPIRED", "CANCELED", "REJECTED"))
TERMINAL = frozenset(("FILLED", "EXPIRED", "CANCELED", "REJECTED"))
REASONS = frozenset((
    "SESSION_START", "RESTART_DISK_REPLAY", "SYNTHETIC_POST_INTENT",
    "MOCK_READ_ACCEPTED", "AMBIGUOUS_POST", "READ_TIMEOUT", "DUPLICATE_INTENT",
    "UNTRUSTED_MOCK_RECEIPT", "BINDING_MISMATCH", "INVALID_OBSERVATION",
    "STALE_OR_FUTURE", "DUPLICATE_RECEIPT", "REORDERED_OBSERVATION",
    "CONFLICTING_OBSERVATION", "TARGET_QUARANTINED",
    # G55/Faz 3: pozisyon bağlamlı TP terminal gözlemleri
    "MOCK_READ_ACCEPTED_FLAT_TERMINAL", "MOCK_READ_ACCEPTED_EXPOSED_PROTECTED",
    "MOCK_READ_ACCEPTED_EXPOSED_UNPROTECTED",
))
# G55: bağlam nedeni -> alarm. MOCK_READ_ACCEPTED (bağlamsız) eski davranış: terminal = OWNER_ALARM.
_CONTEXT_REASONS = {
    "MOCK_READ_ACCEPTED_FLAT_TERMINAL": "NONE",
    "MOCK_READ_ACCEPTED_EXPOSED_PROTECTED": "NONE",
    "MOCK_READ_ACCEPTED_EXPOSED_UNPROTECTED": "OWNER_ALARM",
}
_OBSERVATION_REASONS = frozenset(("MOCK_READ_ACCEPTED", *_CONTEXT_REASONS))
ZERO_HASH = "0" * 64


def _integer(value, *, positive=False):
    if type(value) is not int or not (1 if positive else 0) <= value <= 2**63 - 1:
        raise LifecycleError("INVALID_INTEGER")
    return value


def _quantity(value):
    # Yalnız sonlu, sınırlı, düz ondalık metin; float/NaN/bool kabul edilmez.
    if type(value) is not str or not re.fullmatch(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?", value):
        raise LifecycleError("INVALID_QUANTITY")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise LifecycleError("INVALID_QUANTITY") from None


@dataclass(frozen=True)
class Binding:
    role: str
    scope: str
    symbol: str
    order_id: str
    qty: str

    def __post_init__(self):
        if type(self.role) is not str or self.role not in ROLES:
            raise LifecycleError("TP_ROLE_ONLY")
        for value, pattern in (
            (self.scope, r"[A-Za-z0-9_-]{1,48}"),
            (self.symbol, r"[A-Z0-9]{2,24}"),
            (self.order_id, r"[A-Za-z0-9_-]{1,48}"),
        ):
            if type(value) is not str or not re.fullmatch(pattern, value):
                raise LifecycleError("INVALID_BINDING")
        if _quantity(self.qty) <= 0:
            raise LifecycleError("INVALID_QUANTITY")


@dataclass(frozen=True)
class MockObservation:
    binding: Binding
    status: str
    filled_qty: str
    observed_at_ms: int
    read_sequence: int


@dataclass(frozen=True, eq=False)
class MockReadReceipt:
    receipt_id: str
    observation: MockObservation


def _receipt_snapshot(receipt):
    if (type(receipt) is not MockReadReceipt
            or type(receipt.observation) is not MockObservation
            or type(receipt.observation.binding) is not Binding):
        raise LifecycleError("UNTRUSTED_MOCK_RECEIPT")
    observation = receipt.observation
    return (receipt.receipt_id, *asdict(observation.binding).values(),
            observation.status, observation.filled_qty,
            observation.observed_at_ms, observation.read_sequence)


class MockTrustedSource:
    """Sadece süreç içi mock kimlik kaydı; signed=True veya kriptografi yok.

    Üretilen makbuzun aynı nesnesi ve değişmemiş tüm alanları aranır.
    Güvenilen Python süreci varsayılır; bu bir güvenlik sandbox'ı değildir.
    """

    def __init__(self):
        self._issued = {}

    def issue_mock_read(self, binding, status, filled_qty, observed_at_ms, read_sequence):
        receipt = MockReadReceipt(uuid.uuid4().hex, MockObservation(
            binding, status, filled_qty, observed_at_ms, read_sequence))
        self._issued[id(receipt)] = (receipt, _receipt_snapshot(receipt))
        return receipt

    def verifies_identity(self, receipt):
        issued = self._issued.get(id(receipt))
        if issued is None or issued[0] is not receipt:
            return False
        try:
            return issued[1] == _receipt_snapshot(receipt)
        except LifecycleError:
            return False


@dataclass(frozen=True)
class Event:
    # Tam redaction allowlist: ham yanıt, exception, header, metadata yok.
    seq: int
    event_id: str
    previous: str
    kind: str
    at_ms: int
    binding: Binding | None
    state: str
    filled_qty: str
    observed_at_ms: int
    read_sequence: int
    receipt_id: str
    reason: str
    alarm: str
    provenance: str
    digest: str = ""


def _encode(document):
    return (json.dumps(document, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("ascii")


def _digest(document):
    return hashlib.sha256(_encode({k: v for k, v in document.items() if k != "digest"})).hexdigest()


def _validate_event_shape(event):
    """Yazımdan ÖNCE allowlist; bozuk/serbest metin hiçbir zaman diske gitmez."""
    _integer(event.seq, positive=True)
    for value in (event.at_ms, event.observed_at_ms, event.read_sequence):
        _integer(value)
    _quantity(event.filled_qty)
    for value, pattern in (
        (event.event_id, r"[0-9a-f]{32}"), (event.previous, r"[0-9a-f]{64}"),
        (event.receipt_id, r"(?:[0-9a-f]{32})?"),
    ):
        if type(value) is not str or not re.fullmatch(pattern, value):
            raise JournalError("INVALID_EVENT_SHAPE")
    for value, allowed in (
        (event.kind, {"START", "RESTART", "INTENT", "OBSERVATION", "UNKNOWN", "FAIL_CLOSED"}),
        (event.state, STATUSES | {"NO_NEW_ENTRY", "INTENT_RECORDED_NO_RETRY",
                                 "UNKNOWN_NO_RETRY", "OWNER_REVIEW_NO_RETRY"}),
        (event.reason, REASONS),
        (event.alarm, {"NONE", "RECONCILE", "OWNER_REVIEW", "OWNER_ALARM"}),
        (event.provenance, {"NO_READ_PROOF", "SYNTHETIC_INTENT_ONLY", "MOCK_IDENTITY_ONLY"}),
    ):
        if type(value) is not str or value not in allowed:
            raise JournalError("INVALID_EVENT_SHAPE")
    if event.binding is not None:
        if type(event.binding) is not Binding:
            raise JournalError("INVALID_EVENT_SHAPE")
        Binding(**asdict(event.binding))


class EventJournal:
    """POSIX append-only API: geçici dosya + fsync + ezmeyen link + dizin fsync.

    flock ve beklenen kuyruk, işbirliği yapan eşzamanlı yazarları çitler.
    Yarım yayın/bozuk kayıt/uyumsuz kuyruk kabul edilmez. Kayıt silme API'si yok.
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        try:
            self.directory.mkdir(mode=0o700, parents=False, exist_ok=True)
            info = self.directory.lstat()
        except OSError:
            raise JournalError("JOURNAL_DIRECTORY_UNAVAILABLE") from None
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o022):
            raise JournalError("UNTRUSTED_JOURNAL_DIRECTORY")
        self._identity = (info.st_dev, info.st_ino)

    @contextmanager
    def _locked(self):
        directory_fd = lock_fd = None
        try:
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(directory_fd)
            if (info.st_dev, info.st_ino) != self._identity:
                raise JournalError("JOURNAL_DIRECTORY_REPLACED")
            lock_fd = os.open(".g37-lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                              0o600, dir_fd=directory_fd)
            lock_info = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
                raise JournalError("INVALID_JOURNAL_LOCK")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield directory_fd
        except OSError:
            raise JournalError("JOURNAL_IO_FAILURE") from None
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def _read(self, directory_fd):
        events = []
        identifiers = set()
        previous = ZERO_HASH
        for name in sorted(os.listdir(directory_fd)):
            if name == ".g37-lock":
                continue
            if not re.fullmatch(r"[0-9]{12}\.json", name):
                raise JournalError("UNEXPECTED_OR_PENDING_JOURNAL_FILE")
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory_fd)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8192:
                    raise JournalError("INVALID_JOURNAL_FILE")
                raw = stream.read(8193)
            try:
                document = json.loads(raw)
                if (type(document) is not dict
                        or set(document) != {f.name for f in fields(Event)}
                        or raw != _encode(document)):
                    raise ValueError
                binding = document["binding"]
                if binding is not None:
                    if type(binding) is not dict or set(binding) != {f.name for f in fields(Binding)}:
                        raise ValueError
                    binding = Binding(**binding)
                event = Event(**{**document, "binding": binding})
                _validate_event_shape(event)
                if (event.seq != len(events) + 1 or name != f"{event.seq:012d}.json"
                        or type(event.event_id) is not str
                        or not re.fullmatch(r"[0-9a-f]{32}", event.event_id)
                        or event.event_id in identifiers or event.previous != previous
                        or event.digest != _digest(document)
                        or (events and event.at_ms < events[-1].at_ms)):
                    raise ValueError
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise JournalError("CORRUPT_JOURNAL") from None
            events.append(event)
            identifiers.add(event.event_id)
            previous = event.digest
        return tuple(events)

    def replay(self):
        with self._locked() as directory_fd:
            return self._read(directory_fd)

    def append(self, *, expected_tail, kind, at_ms, binding=None, state="NO_NEW_ENTRY",
               filled_qty="0", observed_at_ms=0, read_sequence=0, receipt_id="",
               reason, alarm="NONE", provenance="NO_READ_PROOF"):
        with self._locked() as directory_fd:
            events = self._read(directory_fd)
            tail = events[-1].digest if events else ZERO_HASH
            if tail != expected_tail:
                raise JournalError("STALE_JOURNAL_WRITER")
            _integer(at_ms)
            if events and at_ms < events[-1].at_ms:
                raise JournalError("CLOCK_REORDERED")
            event = Event(len(events) + 1, uuid.uuid4().hex, tail, kind, at_ms, binding,
                          state, filled_qty, observed_at_ms, read_sequence,
                          receipt_id, reason, alarm, provenance)
            _validate_event_shape(event)
            if any(old.event_id == event.event_id for old in events):
                raise JournalError("DUPLICATE_EVENT_ID")
            event = replace(event, digest=_digest(asdict(event)))
            temporary = ".pending-" + event.event_id
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
            with os.fdopen(fd, "wb") as stream:
                stream.write(_encode(asdict(event)))
                stream.flush()
                os.fsync(stream.fileno())
            # link ne var olan olayı değiştirir ne kısmi JSON yayımlar.
            os.link(temporary, f"{event.seq:012d}.json", src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd, follow_symlinks=False)
            os.fsync(directory_fd)
            os.unlink(temporary, dir_fd=directory_fd)
            os.fsync(directory_fd)
            return event


@dataclass(frozen=True)
class TPView:
    binding: Binding
    state: str
    intent_at_ms: int
    filled_qty: str = "0"
    observed_at_ms: int = 0
    read_sequence: int = 0
    quarantined: bool = False
    no_retry: bool = True


def _read_problem(view, observation, at_ms, max_age_ms):
    if observation.binding != view.binding:
        return "BINDING_MISMATCH"
    try:
        Binding(**asdict(observation.binding))
        _integer(observation.observed_at_ms, positive=True)
        _integer(observation.read_sequence, positive=True)
        filled = _quantity(observation.filled_qty)
        qty = _quantity(view.binding.qty)
        if type(observation.status) is not str or observation.status not in STATUSES:
            raise LifecycleError("INVALID_OBSERVATION")
        if (filled > qty or (observation.status == "NEW" and filled != 0)
                or (observation.status == "PARTIAL" and not 0 < filled < qty)
                or (observation.status == "FILLED" and filled != qty)
                or (observation.status in {"EXPIRED", "CANCELED"} and filled >= qty)
                or (observation.status == "REJECTED" and filled != 0)):
            raise LifecycleError("INVALID_OBSERVATION")
    except (LifecycleError, TypeError):
        return "INVALID_OBSERVATION"
    if not 0 <= at_ms - observation.observed_at_ms <= max_age_ms:
        return "STALE_OR_FUTURE"
    if (observation.observed_at_ms < view.intent_at_ms
            or observation.observed_at_ms <= view.observed_at_ms
            or observation.read_sequence <= view.read_sequence):
        return "REORDERED_OBSERVATION"
    if (view.state in TERMINAL or filled < _quantity(view.filled_qty)
            or (view.state == "PARTIAL" and observation.status in {"NEW", "REJECTED"})):
        return "CONFLICTING_OBSERVATION"
    return None


def _alarm(status, reason="MOCK_READ_ACCEPTED"):
    if reason in _CONTEXT_REASONS:
        if status not in {"EXPIRED", "CANCELED", "REJECTED"}:
            raise LifecycleError("CONTEXT_REASON_TERMINAL_ONLY")
        return _CONTEXT_REASONS[reason]
    if status == "PARTIAL":
        return "RECONCILE"
    if status in {"EXPIRED", "CANCELED", "REJECTED"}:
        return "OWNER_ALARM"
    return "NONE"


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._mutex:
            return method(self, *args, **kwargs)
    return call


class TPLifecycleOffline:
    """Sadece telemetri. Alarm olayları dayanıklı, değişmez yerel outbox'tır.

    Hiçbir giriş izni veya owner kilidi temizleme API'si yoktur. NO_RETRY,
    sentetik POST niyetinin sonucundan bağımsız ve TP başına kalıcıdır.
    """

    def __init__(self, journal, source, *, clock_ms=None, max_age_ms=5000):
        if type(journal) is not EventJournal or type(source) is not MockTrustedSource:
            raise LifecycleError("OFFLINE_TYPES_ONLY")
        _integer(max_age_ms, positive=True)
        self._journal, self._source = journal, source
        self._mutex = RLock()
        self._clock = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._max_age = max_age_ms
        self._events = []
        self._views = {}
        self._receipts = set()
        self._failed = False
        self._restarted = False
        for event in journal.replay():
            self._apply(event)
        self._restarted = bool(self._events)
        self._emit(kind="RESTART" if self._restarted else "START",
                   reason="RESTART_DISK_REPLAY" if self._restarted else "SESSION_START")

    @property
    @_serialized
    def events(self):
        return tuple(self._events)

    @property
    @_serialized
    def alarms(self):
        return tuple(event for event in self._events if event.alarm != "NONE")

    @_serialized
    def view(self, role):
        self._require_role(role)
        return self._views.get(role)

    @_serialized
    def report(self):
        return {
            "mode": "OFFLINE_SYNTHETIC_ONLY", "entry_authorized": False,
            "entry_policy": "NO_NEW_ENTRY", "post_policy": "NO_RETRY",
            "real_signature_verified": False, "stop_integration": "ABSENT",
            "journal_health": "FAILED" if self._failed else "OK",
            "restarted": self._restarted, "events": len(self._events),
            "synthetic_post_intents": sum(e.kind == "INTENT" for e in self._events),
            "tp": {role: view.state for role, view in self._views.items()},
            "alarms": [{"event_id": e.event_id, "role": e.binding.role,
                        "alarm": e.alarm, "reason": e.reason} for e in self.alarms],
        }

    def _require_role(self, role):
        if type(role) is not str or role not in ROLES:
            raise LifecycleError("TP_ROLE_ONLY")

    def _target(self, role):
        self._require_role(role)
        if self._failed:
            raise JournalError("JOURNAL_FAILED_NO_RETRY")
        if role not in self._views:
            raise LifecycleError("NO_SYNTHETIC_INTENT")
        return self._views[role]

    def _apply(self, event):
        # Replay, gerçek makbuz doğrulaması değildir; yalnız kayıt tutarlılığıdır.
        if event.reason not in REASONS:
            raise JournalError("INVALID_EVENT_SEMANTICS")
        if event.kind in {"START", "RESTART"}:
            expected_reason = "SESSION_START" if event.kind == "START" else "RESTART_DISK_REPLAY"
            valid = (event.binding is None and event.state == "NO_NEW_ENTRY"
                     and event.reason == expected_reason and event.alarm == "NONE"
                     and event.provenance == "NO_READ_PROOF"
                     and (not self._events if event.kind == "START" else bool(self._events)))
            self._restarted = event.kind == "RESTART"
        else:
            if type(event.binding) is not Binding:
                raise JournalError("INVALID_EVENT_BINDING")
            role = event.binding.role
            view = self._views.get(role)
            if event.kind == "INTENT":
                valid = (view is None and not self._restarted and bool(self._events)
                         and not self._identity_in_use(event.binding)
                         and event.state == "INTENT_RECORDED_NO_RETRY"
                         and event.reason == "SYNTHETIC_POST_INTENT" and event.alarm == "NONE"
                         and event.provenance == "SYNTHETIC_INTENT_ONLY")
                if valid:
                    self._views[role] = TPView(event.binding, event.state, event.at_ms)
            elif view is None or event.binding != view.binding:
                valid = False
            elif event.kind == "OBSERVATION":
                observation = MockObservation(event.binding, event.state, event.filled_qty,
                                              event.observed_at_ms, event.read_sequence)
                valid = (not view.quarantined and event.receipt_id not in self._receipts
                         and re.fullmatch(r"[0-9a-f]{32}", event.receipt_id) is not None
                         and _read_problem(view, observation, event.at_ms, self._max_age) is None
                         and event.reason in _OBSERVATION_REASONS
                         and (event.reason == "MOCK_READ_ACCEPTED"
                              or event.state in {"EXPIRED", "CANCELED", "REJECTED"})
                         and event.alarm == _alarm(event.state, event.reason)
                         and event.provenance == "MOCK_IDENTITY_ONLY")
                if valid:
                    self._views[role] = replace(view, state=event.state, filled_qty=event.filled_qty,
                                               observed_at_ms=event.observed_at_ms,
                                               read_sequence=event.read_sequence)
                    self._receipts.add(event.receipt_id)
            elif event.kind == "UNKNOWN":
                valid = (not view.quarantined and view.state not in TERMINAL
                         and (event.reason != "AMBIGUOUS_POST"
                              or view.state == "INTENT_RECORDED_NO_RETRY")
                         and event.state == "UNKNOWN_NO_RETRY" and event.alarm == "OWNER_REVIEW"
                         and event.reason in {"AMBIGUOUS_POST", "READ_TIMEOUT"}
                         and event.provenance == "NO_READ_PROOF")
                if valid:
                    self._views[role] = replace(view, state=event.state)
            elif event.kind == "FAIL_CLOSED":
                valid = (event.state == "OWNER_REVIEW_NO_RETRY" and event.alarm == "OWNER_REVIEW"
                         and event.reason not in _OBSERVATION_REASONS
                         and event.reason not in {"SESSION_START", "RESTART_DISK_REPLAY",
                                                 "SYNTHETIC_POST_INTENT", "MOCK_READ_ACCEPTED",
                                                 "AMBIGUOUS_POST", "READ_TIMEOUT"}
                         and event.provenance == "NO_READ_PROOF")
                if valid:
                    self._views[role] = replace(view, state=event.state, quarantined=True)
            else:
                valid = False
        if event.kind != "OBSERVATION":
            valid = valid and (event.filled_qty == "0" and event.observed_at_ms == 0
                               and event.read_sequence == 0 and event.receipt_id == "")
        if not valid:
            raise JournalError("INVALID_EVENT_SEMANTICS")
        self._events.append(event)

    def _emit(self, **data):
        if self._failed:
            raise JournalError("JOURNAL_FAILED_NO_RETRY")
        try:
            at_ms = data.pop("at_ms", None)
            if at_ms is None:
                at_ms = self._clock()
            event = self._journal.append(
                expected_tail=self._events[-1].digest if self._events else ZERO_HASH,
                at_ms=at_ms, **data)
            self._apply(event)
            return event
        except (OSError, LifecycleError):
            self._failed = True
            raise JournalError("JOURNAL_FAILED_NO_RETRY") from None

    def _reject(self, role, reason):
        return self._emit(kind="FAIL_CLOSED", binding=self._target(role).binding,
                          state="OWNER_REVIEW_NO_RETRY", reason=reason, alarm="OWNER_REVIEW")

    def _identity_in_use(self, binding):
        return any((view.binding.scope, view.binding.symbol, view.binding.order_id)
                   == (binding.scope, binding.symbol, binding.order_id)
                   for view in self._views.values())

    @_serialized
    def record_synthetic_post_intent(self, binding):
        if type(binding) is not Binding:
            raise LifecycleError("INVALID_BINDING")
        Binding(**asdict(binding))
        if self._restarted:
            raise LifecycleError("NO_NEW_ENTRY")
        if binding.role in self._views:
            return self._reject(binding.role, "DUPLICATE_INTENT")
        if self._identity_in_use(binding):
            raise LifecycleError("CONFLICTING_TP_IDENTITY")
        return self._emit(kind="INTENT", binding=binding, state="INTENT_RECORDED_NO_RETRY",
                          reason="SYNTHETIC_POST_INTENT", provenance="SYNTHETIC_INTENT_ONLY")

    @_serialized
    def record_unknown(self, role, *, reason="READ_TIMEOUT"):
        view = self._target(role)
        if type(reason) is not str or reason not in {"AMBIGUOUS_POST", "READ_TIMEOUT"}:
            raise LifecycleError("INVALID_UNKNOWN_REASON")
        if view.quarantined:
            return self._reject(role, "TARGET_QUARANTINED")
        if (view.state in TERMINAL
                or (reason == "AMBIGUOUS_POST" and view.state != "INTENT_RECORDED_NO_RETRY")):
            return self._reject(role, "CONFLICTING_OBSERVATION")
        return self._emit(kind="UNKNOWN", binding=view.binding, state="UNKNOWN_NO_RETRY",
                          reason=reason, alarm="OWNER_REVIEW")

    @_serialized
    def observe(self, role, receipt, *, exposure=None):
        """exposure: G55 ExposureContext (opsiyonel). Yalnız EXPIRED/CANCELED/REJECTED
        için kullanılır; yoksa/bozuksa eski güvenli davranış (OWNER_ALARM) korunur."""
        view = self._target(role)
        if view.quarantined:
            return self._reject(role, "TARGET_QUARANTINED")
        if not self._source.verifies_identity(receipt):
            return self._reject(role, "UNTRUSTED_MOCK_RECEIPT")
        if receipt.receipt_id in self._receipts:
            return self._reject(role, "DUPLICATE_RECEIPT")
        try:
            at_ms = self._clock()
            _integer(at_ms)
        except (TypeError, ValueError):
            self._failed = True
            raise JournalError("JOURNAL_FAILED_NO_RETRY") from None
        problem = _read_problem(view, receipt.observation, at_ms, self._max_age)
        if problem:
            return self._reject(role, problem)
        observation = receipt.observation
        reason = "MOCK_READ_ACCEPTED"
        if exposure is not None and observation.status in {"EXPIRED", "CANCELED", "REJECTED"}:
            from tp_exposure_guard_offline_g55 import (
                classify_tp_terminal, CTX_FLAT_EXPIRY, CTX_EXPOSED_PROTECTED)
            context = classify_tp_terminal(observation.status, exposure)
            reason = ("MOCK_READ_ACCEPTED_FLAT_TERMINAL" if context == CTX_FLAT_EXPIRY
                      else "MOCK_READ_ACCEPTED_EXPOSED_PROTECTED"
                      if context == CTX_EXPOSED_PROTECTED
                      else "MOCK_READ_ACCEPTED_EXPOSED_UNPROTECTED")
        return self._emit(kind="OBSERVATION", at_ms=at_ms, binding=view.binding,
                          state=observation.status, filled_qty=observation.filled_qty,
                          observed_at_ms=observation.observed_at_ms,
                          read_sequence=observation.read_sequence,
                          receipt_id=receipt.receipt_id, reason=reason,
                          alarm=_alarm(observation.status, reason),
                          provenance="MOCK_IDENTITY_ONLY")


def synthetic_demo(directory):
    """TP1 NEW→EXPIRED; TP2 belirsiz→NEW→PARTIAL→timeout→FILLED; disk replay."""
    now = [1_000_000]
    source = MockTrustedSource()
    journal = EventJournal(directory)
    previous = journal.replay()
    if previous:
        now[0] = max(now[0], previous[-1].at_ms)
    tracker = TPLifecycleOffline(journal, source, clock_ms=lambda: now[0])
    if not tracker.report()["restarted"]:
        tp1 = Binding("TP1", "g37-demo", "MOCKUSDT", "synthetic-tp1", "2")
        tp2 = Binding("TP2", "g37-demo", "MOCKUSDT", "synthetic-tp2", "3")
        for binding in (tp1, tp2):
            tracker.record_synthetic_post_intent(binding)
        tracker.record_unknown("TP2", reason="AMBIGUOUS_POST")
        for binding, status, filled, sequence in (
            (tp1, "NEW", "0", 1), (tp2, "NEW", "0", 1),
            (tp2, "PARTIAL", "1", 2), (tp1, "EXPIRED", "0", 2),
        ):
            now[0] += 10
            tracker.observe(binding.role, source.issue_mock_read(
                binding, status, filled, now[0], sequence))
        tracker.record_unknown("TP2")
        now[0] += 10
        tracker.observe("TP2", source.issue_mock_read(tp2, "FILLED", "3", now[0], 3))
    before_restart = tracker.report()
    now[0] = max(now[0], tracker.events[-1].at_ms)
    restarted = TPLifecycleOffline(EventJournal(directory), MockTrustedSource(),
                                   clock_ms=lambda: now[0])
    return {"before_restart": before_restart, "after_disk_replay": restarted.report()}


def main():
    parser = argparse.ArgumentParser(description="G37: yalnız offline sentetik TP telemetrisi")
    parser.add_argument("--journal-directory", type=Path)
    args = parser.parse_args()
    try:
        if args.journal_directory is None:
            with TemporaryDirectory(prefix="atlas-g37-") as directory:
                result = synthetic_demo(Path(directory) / "journal")
        else:
            result = synthetic_demo(args.journal_directory)
    except LifecycleError:
        print(json.dumps({"error": "OFFLINE_FAIL_CLOSED", "entry_policy": "NO_NEW_ENTRY",
                          "post_policy": "NO_RETRY", "entry_authorized": False}))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
