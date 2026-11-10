"""G42 — yalnız çevrimdışı, callback çalıştıran standalone davranış adayı.

Eski WAL/fence/owner modüllerini import etmez ve üretime bağlı değildir.
Pozitif timeout hem deneme öncesinde hem fence altında append'ten hemen önce
kontrol edilir. Sıfır timeout tek nonblocking fence denemesidir; süre limiti
değil, beklememe semantiğidir. Lease expiry sıfır timeout'ta da geçerlidir.

Callback sözleşmesi: acquire nonblocking olmalı, FenceBusy yalnız acquisition
öncesi contention bildirmeli; bütün yazarlar aynı fence'e katılmalı; lookup
salt okunur, append_once storage düzeyinde aynı (scope, role) için tekil olmalı.
lease_is_current güvenilir mock otoritenin güncel epoch/nesil/nesne kontrolüdür.
Bu sınıf kendi başına lease otoritesi, disk journal'ı veya replay uygulamaz.

Check -> append callback arasında OS/Python preemption mümkündür: son kontrol
ile gerçek storage mutation atomik değildir. ACKED dahi sert deadline veya
fiziksel dayanıklılık kanıtı değildir. Append başladıktan sonraki hata/geç ACK
UNKNOWN/NO_RETRY olur; "yazılmadı" anlamına gelmez. Bellekteki cache/karantina
crash'te kaybolur: yeniden kurulan harness replay_only=True başlamalıdır.
Hiçbir sonuç entry yetkisi, owner kilidi açma veya STOP kanıtı vermez.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import re
import threading
import time
from typing import Callable, ContextManager


class FenceBusy(RuntimeError):
    """Yalnız mutasyonsuz, nonblocking fence acquisition başarısızlığı."""


class FsyncAck(Enum):
    OK = "OK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Intent:
    scope: str
    role: str
    identity: str
    symbol: str
    client_id: str


@dataclass(frozen=True, slots=True)
class Lease:
    scope: str
    owner: str
    epoch: int
    generation: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class FenceGrant:
    scope: str
    owner: str
    epoch: int
    generation: int


@dataclass(frozen=True, slots=True)
class Callbacks:
    acquire: Callable[[Lease], ContextManager[FenceGrant]]
    lease_is_current: Callable[[Lease], bool]
    lookup: Callable[[Intent], Intent | None]
    append_once: Callable[[Intent], None]
    fsync_file: Callable[[Intent], FsyncAck]
    fsync_directory: Callable[[], FsyncAck]


@dataclass(frozen=True, slots=True)
class Decision:
    status: str
    reason: str
    attempts: int = 0
    append_attempted: bool = False
    durability_confirmed: bool = False
    retry_policy: str = field(default="NO_RETRY", init=False)
    entry_authorized: bool = field(default=False, init=False)
    unlock: bool = field(default=False, init=False)
    stop_proven: bool = field(default=False, init=False)


class _Rejected(Exception):
    def __init__(self, reason, status="BLOCKED"):
        self.reason = reason
        self.status = status


_NAME = re.compile(r"[A-Za-z0-9_-]{1,120}\Z")


def _name(value):
    return type(value) is str and _NAME.fullmatch(value) is not None


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


class _Budget:
    def __init__(self, clock, timeout_s):
        self.clock = clock
        self.previous = None
        start = self.sample()
        self.deadline = None if timeout_s == 0 else start + timeout_s
        if self.deadline is not None and (
            not math.isfinite(self.deadline) or self.deadline <= start
        ):
            raise _Rejected("DEADLINE_NOT_REPRESENTABLE")

    def sample(self):
        now = self.clock()
        if not _number(now) or (self.previous is not None and now < self.previous):
            raise _Rejected("CLOCK_INVALID_OR_REGRESSED")
        self.previous = now
        return now

    def check(self):
        now = self.sample()
        if self.deadline is not None and now >= self.deadline:
            raise _Rejected("DEADLINE_EXPIRED", "DEADLINE")
        return now


class ProtectiveDeadlinePatchCandidate:
    """Gerçek döngü/fence/callback sırasını çalıştırır; canlı adaptör içermez.

    Terminal sonuç aynı nesnede aynı intent için cache'lenir. UNKNOWN bütün
    scope'u karantinaya alır. Sıralı veya eşzamanlı ikinci çağrı, in-flight
    işlem varken beklemeden reddedilir. Ayrı instance/process'lerin tekilliği
    ortak storage callback sözleşmesine bağlıdır. replay_only açıkça seçilir;
    fresh offline fixture dışında False kullanımı yetkilendirilmiş sayılmaz.
    """

    def __init__(self, callbacks: Callbacks, *, replay_only: bool,
                 clock=time.monotonic, sleep=time.sleep):
        if type(callbacks) is not Callbacks or any(
            not callable(getattr(callbacks, name))
            for name in Callbacks.__dataclass_fields__
        ):
            raise ValueError("CALLBACKS_INVALID")
        if type(replay_only) is not bool or not callable(clock) or not callable(sleep):
            raise ValueError("HARNESS_INVALID")
        self._callbacks = callbacks
        self._clock = clock
        self._sleep = sleep
        self._replay_only = replay_only
        self._outcomes = {}
        self._quarantined = set()
        self._call_gate = threading.Lock()

    def mark_restart(self):
        """Tek yönlü replay-only kapısı; açma/temizleme/lease yenileme yolu yok."""
        self._replay_only = True

    def run(self, intent: Intent, lease: Lease, *, timeout_s=0.1, poll_s=0.01):
        if type(intent) is not Intent or intent.role not in ("STOP", "TP1", "TP2"):
            raise ValueError("PROTECTIVE_INTENT_INVALID")
        if not all(_name(getattr(intent, key)) for key in Intent.__dataclass_fields__):
            raise ValueError("INTENT_FIELD_INVALID")
        if not _number(timeout_s) or not 0 <= timeout_s <= 0.5:
            raise ValueError("TIMEOUT_INVALID")
        if not _number(poll_s) or not 0 < poll_s <= 0.05:
            raise ValueError("POLL_INVALID")
        if not self._call_gate.acquire(blocking=False):
            return Decision("BLOCKED", "CALL_IN_PROGRESS_NO_RETRY")
        try:
            if self._replay_only:
                return Decision("REPLAY_ONLY", "RESTART_REQUIRES_EXTERNAL_REVIEW")
            key = (intent.scope, intent.role)
            if key in self._outcomes:
                prior_intent, result = self._outcomes[key]
                if prior_intent != intent:
                    return Decision("BLOCKED", "IDEMPOTENCY_CONFLICT")
                return result
            if intent.scope in self._quarantined:
                return Decision("BLOCKED", "SCOPE_QUARANTINED_NO_RETRY")
            if not self._valid_lease_shape(intent, lease):
                result = Decision("BLOCKED", "LEASE_INVALID")
            else:
                result = self._execute(intent, lease, timeout_s, poll_s)
            self._outcomes[key] = (intent, result)
            if result.status == "UNKNOWN":
                self._quarantined.add(intent.scope)
            return result
        finally:
            self._call_gate.release()

    @staticmethod
    def _valid_lease_shape(intent, lease):
        return (
            type(lease) is Lease and _name(lease.scope) and lease.scope == intent.scope
            and _name(lease.owner) and type(lease.epoch) is int and lease.epoch >= 0
            and type(lease.generation) is int and lease.generation >= 1
            and _number(lease.expires_at)
        )

    def _check(self, budget, lease):
        if self._replay_only:
            raise _Rejected("RESTART_REPLAY_ONLY", "REPLAY_ONLY")
        if self._callbacks.lease_is_current(lease) is not True:
            raise _Rejected("LEASE_STALE")
        now = budget.check()
        if now >= lease.expires_at:
            raise _Rejected("LEASE_EXPIRED")
        if self._replay_only:
            raise _Rejected("RESTART_REPLAY_ONLY", "REPLAY_ONLY")

    @staticmethod
    def _check_grant(grant, lease):
        if type(grant) is not FenceGrant:
            raise _Rejected("FENCE_GRANT_STALE_OR_INVALID")
        if (type(grant.epoch) is not int or type(grant.generation) is not int
                or not _name(grant.scope) or not _name(grant.owner)
                or (grant.scope, grant.owner, grant.epoch, grant.generation)
                != (lease.scope, lease.owner, lease.epoch, lease.generation)):
            raise _Rejected("FENCE_GRANT_STALE_OR_INVALID")

    def _execute(self, intent, lease, timeout_s, poll_s):
        attempts = 0
        append_attempted = False
        stage = "CLOCK"

        def rejected(error):
            status = "UNKNOWN" if append_attempted else error.status
            return Decision(status, error.reason, attempts, append_attempted)

        def callback_error():
            status = "UNKNOWN" if append_attempted else "BLOCKED"
            return Decision(status, stage + "_ERROR_NO_RETRY", attempts, append_attempted)

        try:
            budget = _Budget(self._clock, timeout_s)
        except _Rejected as error:
            return rejected(error)
        except Exception:
            return callback_error()

        while True:
            entered = False
            completed = False
            try:
                stage = "PRE_ACQUIRE_CHECK"
                self._check(budget, lease)
                attempts += 1
                stage = "ACQUIRE"
                with self._callbacks.acquire(lease) as grant:
                    entered = True
                    stage = "FENCE_CHECK"
                    self._check_grant(grant, lease)
                    self._check(budget, lease)
                    stage = "LOOKUP"
                    existing = self._callbacks.lookup(intent)
                    if existing is not None:
                        if type(existing) is not Intent:
                            raise _Rejected("LOOKUP_INVALID")
                        if existing != intent:
                            raise _Rejected("IDEMPOTENCY_CONFLICT")
                        # Varlık, geçmiş fsync veya deadline başarısını kanıtlamaz.
                        raise _Rejected("PREEXISTING_RECORD_DURABILITY_UNKNOWN", "UNKNOWN")
                    stage = "BEFORE_MUTATION_CHECK"
                    self._check(budget, lease)
                    # Bu kontrol ile callback'in gerçek yazımı atomik DEĞİLDİR.
                    stage = "APPEND"
                    append_attempted = True
                    self._callbacks.append_once(intent)
                    stage = "FILE_FSYNC"
                    if self._callbacks.fsync_file(intent) is not FsyncAck.OK:
                        raise _Rejected("FILE_FSYNC_UNCONFIRMED", "UNKNOWN")
                    stage = "DIRECTORY_FSYNC"
                    if self._callbacks.fsync_directory() is not FsyncAck.OK:
                        raise _Rejected("DIRECTORY_FSYNC_UNCONFIRMED", "UNKNOWN")
                    stage = "POST_APPEND_CHECK"
                    self._check(budget, lease)
                    completed = True
                    stage = "FENCE_EXIT"
                if not completed:
                    # Exception yutan bir context manager başarı üretemez.
                    raise _Rejected("FENCE_SUPPRESSED_FAILURE")
                stage = "FINAL_ACK_CHECK"
                self._check(budget, lease)
                return Decision("ACKED", "MOCK_CALLBACK_ACK_ONLY", attempts, True, True)
            except FenceBusy:
                # FenceBusy body/exit/başka callback'ten gelirse retry güvenli değildir.
                if entered or stage != "ACQUIRE":
                    return callback_error()
                if timeout_s == 0:
                    return Decision("BLOCKED", "FENCE_BUSY_NONBLOCKING", attempts)
                try:
                    stage = "BUSY_CHECK"
                    self._check(budget, lease)
                    remaining = budget.deadline - budget.previous
                    stage = "SLEEP"
                    self._sleep(min(poll_s, remaining))
                    # Döngü başındaki check oversleep/late-release yolunu kapatır.
                except _Rejected as error:
                    return rejected(error)
                except Exception:
                    return callback_error()
            except _Rejected as error:
                return rejected(error)
            except Exception:
                return callback_error()
